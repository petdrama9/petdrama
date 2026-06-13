"""
youtube_upload.py — Upload a video to YouTube using the YouTube Data API v3.

Authentication uses an OAuth2 refresh token (no browser flow at runtime).
Implements resumable upload with exponential-backoff retry.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

import config
from groq_generator import VideoMetadata

logger = logging.getLogger("youtube_upload")

# ─── Exceptions ─────────────────────────────────────────────────────────────

class UploadLimitError(Exception):
    """Raised when YouTube rejects the upload due to daily upload limit.

    The caller should catch this, queue the reel, and retry after 24 hours.
    """


# ─── Constants ───────────────────────────────────────────────────────────────

_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3/videos"
_API_BASE = "https://www.googleapis.com/youtube/v3"
_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB — YouTube minimum recommended chunk


# ─── Data class ──────────────────────────────────────────────────────────────

@dataclass
class UploadResult:
    video_id: str
    url: str
    title: str

    @property
    def shorts_url(self) -> str:
        return f"https://www.youtube.com/shorts/{self.video_id}"


# ─── Token manager ───────────────────────────────────────────────────────────

class TokenManager:
    """Manages OAuth2 access tokens via the refresh token grant.

    Priority:
      1. token.json on disk  (written by the GitHub Action — same as fifa project)
      2. Individual env vars  (YOUTUBE_CLIENT_ID / SECRET / REFRESH_TOKEN)
    """

    TOKEN_FILE = config.BASE_DIR / "token.json"

    def __init__(
        self,
        client_id: str = config.YOUTUBE_CLIENT_ID,
        client_secret: str = config.YOUTUBE_CLIENT_SECRET,
        refresh_token: str = config.YOUTUBE_REFRESH_TOKEN,
        token_uri: str = config.YOUTUBE_TOKEN_URI,
    ) -> None:
        # Try to load from token.json first (fifa-project pattern)
        if self.TOKEN_FILE.exists():
            try:
                import json as _json
                data = _json.loads(self.TOKEN_FILE.read_text())
                self.client_id     = data.get("client_id", client_id)
                self.client_secret = data.get("client_secret", client_secret)
                self.refresh_token = data.get("refresh_token", refresh_token)
                self.token_uri     = data.get("token_uri", token_uri)
                # If there's a non-expired access token already, cache it
                self._access_token: Optional[str] = data.get("token")
                expiry_str = data.get("expiry", "")
                if expiry_str:
                    from datetime import datetime, timezone
                    expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    self._expires_at = expiry.timestamp()
                else:
                    self._expires_at = 0.0
                logger.info("Loaded credentials from token.json")
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse token.json: %s — falling back to env vars", exc)

        self.client_id     = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.token_uri     = token_uri
        self._access_token = None
        self._expires_at   = 0.0

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        return self._refresh()

    def _refresh(self) -> str:
        logger.info("Refreshing YouTube access token …")
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        with httpx.Client(timeout=15) as client:
            response = client.post(self.token_uri, data=payload)
            response.raise_for_status()
            data = response.json()

        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        logger.info("Access token refreshed. Expires in %ds.", int(data.get("expires_in", 3600)))
        return self._access_token


# ─── Uploader ────────────────────────────────────────────────────────────────

class YouTubeUploader:
    """Uploads videos to YouTube with resumable upload and retry logic."""

    def __init__(
        self,
        token_manager: Optional[TokenManager] = None,
        category_id: str = config.YOUTUBE_CATEGORY_ID,
        privacy: str = config.YOUTUBE_DEFAULT_PRIVACY,
        max_retries: int = 5,
    ) -> None:
        self.token_manager = token_manager or TokenManager()
        self.category_id = category_id
        self.privacy = privacy
        self.max_retries = max_retries

    # ── Snippet builder ──────────────────────────────────────────────────────

    @staticmethod
    def _sanitize(text: str, max_len: int) -> str:
        """Strip control characters YouTube rejects and enforce length limit."""
        # Remove ASCII control chars (0x00-0x1F) except newline/tab
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # Collapse excessive whitespace runs
        cleaned = re.sub(r" {3,}", "  ", cleaned)
        return cleaned[:max_len].strip()

    def _build_resource(self, metadata: VideoMetadata) -> dict:
        """Build the YouTube video resource body."""
        # Sanitize title and description
        title       = self._sanitize(metadata.title, config.TITLE_MAX_CHARS)
        hashtag_line = " ".join(metadata.hashtags)
        raw_desc    = f"{metadata.description}\n\n{hashtag_line}"
        description = self._sanitize(raw_desc, config.DESCRIPTION_MAX_CHARS)

        # YouTube rules: each tag ≤ 30 chars, total ≤ 500 chars
        tags: list[str] = []
        total = 0
        for tag in metadata.tags:
            tag = tag[:30]          # hard per-tag limit
            if total + len(tag) + 1 > config.MAX_TAGS:
                break
            tags.append(tag)
            total += len(tag) + 1

        logger.info("Upload snippet — title: %r | tags: %s", title, tags)

        return {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": self.category_id,
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

    # ── Resumable upload ─────────────────────────────────────────────────────

    def _initiate_resumable_upload(
        self, resource: dict, file_size: int, access_token: str
    ) -> str:
        """Call YouTube's resumable-upload endpoint and return the upload URI."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(file_size),
        }
        params = {
            "uploadType": "resumable",
            "part": "snippet,status",
        }
        with httpx.Client(timeout=30) as client:
            response = client.post(
                _UPLOAD_BASE,
                headers=headers,
                params=params,
                json=resource,
            )

        # Log full error body so we can diagnose 400/403 issues
        if response.status_code >= 400:
            logger.error(
                "YouTube API error %d:\n%s",
                response.status_code,
                response.text,
            )
            # Detect upload-limit specifically so caller can queue for later
            try:
                err_reason = (
                    response.json()
                    .get("error", {})
                    .get("errors", [{}])[0]
                    .get("reason", "")
                )
                if err_reason == "uploadLimitExceeded":
                    raise UploadLimitError(
                        "YouTube daily upload limit reached. "
                        "Reel will be queued and retried after 24 hours."
                    )
            except UploadLimitError:
                raise
            except Exception:
                pass  # JSON parse failed — fall through to generic raise
            response.raise_for_status()

        upload_uri = response.headers.get("Location")
        if not upload_uri:
            raise RuntimeError("YouTube did not return an upload URI.")
        logger.info("Upload session initiated successfully.")
        return upload_uri

    def _upload_chunks(self, upload_uri: str, video_path: Path) -> dict:
        """Stream the video file in chunks. Returns the completed video resource."""
        file_size = video_path.stat().st_size
        bytes_sent = 0

        with video_path.open("rb") as fh:
            while bytes_sent < file_size:
                chunk = fh.read(_CHUNK_SIZE)
                if not chunk:
                    break

                start = bytes_sent
                end = bytes_sent + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                }

                for attempt in range(1, self.max_retries + 1):
                    try:
                        with httpx.Client(timeout=120) as client:
                            resp = client.put(upload_uri, headers=headers, content=chunk)

                        # 308 Resume Incomplete — chunk accepted, continue
                        if resp.status_code == 308:
                            bytes_sent += len(chunk)
                            progress = bytes_sent / file_size * 100
                            logger.info(
                                "Upload progress: %.1f%% (%d / %d bytes)",
                                progress, bytes_sent, file_size,
                            )
                            break

                        # 200 / 201 — upload complete
                        if resp.status_code in (200, 201):
                            bytes_sent += len(chunk)
                            logger.info("Upload complete!")
                            return resp.json()

                        resp.raise_for_status()

                    except (httpx.TimeoutException, httpx.NetworkError) as exc:
                        wait = (2 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            "Chunk upload attempt %d failed: %s. Retrying in %.1fs …",
                            attempt, exc, wait,
                        )
                        time.sleep(wait)
                        if attempt == self.max_retries:
                            raise RuntimeError(
                                f"Failed to upload chunk after {self.max_retries} attempts."
                            ) from exc

        raise RuntimeError("Upload loop ended without a completion response.")

    # ── Public entry point ───────────────────────────────────────────────────

    def upload(self, video_path: Path, metadata: VideoMetadata) -> UploadResult:
        """
        Upload *video_path* to YouTube with *metadata*.

        Args:
            video_path: Local path to the downloaded MP4 file.
            metadata:   :class:`VideoMetadata` from the Groq generator.

        Returns:
            :class:`UploadResult` containing the YouTube video ID and URL.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        file_size = video_path.stat().st_size
        logger.info(
            "Starting YouTube upload: %s (%.1f MB)",
            video_path.name,
            file_size / 1_048_576,
        )

        access_token = self.token_manager.get_access_token()
        resource = self._build_resource(metadata)
        upload_uri = self._initiate_resumable_upload(resource, file_size, access_token)
        video_data = self._upload_chunks(upload_uri, video_path)

        video_id = video_data.get("id", "")
        if not video_id:
            raise RuntimeError(f"YouTube returned no video ID. Response: {video_data}")

        result = UploadResult(
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            title=metadata.title,
        )
        logger.info("YouTube video published: %s", result.shorts_url)
        return result

    # ── Channel info helper ──────────────────────────────────────────────────

    def get_channel_info(self) -> dict:
        """Fetch authenticated channel details (useful for debugging auth)."""
        access_token = self.token_manager.get_access_token()
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{_API_BASE}/channels",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"part": "snippet,contentDetails", "mine": "true"},
            )
            resp.raise_for_status()
        return resp.json()


# ─── Convenience function ────────────────────────────────────────────────────

def upload_to_youtube(video_path: Path, metadata: VideoMetadata) -> UploadResult:
    """Module-level helper — uploads and returns :class:`UploadResult`."""
    uploader = YouTubeUploader()
    return uploader.upload(video_path, metadata)
