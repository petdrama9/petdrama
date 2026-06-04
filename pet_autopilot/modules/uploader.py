import logging
import os
from pathlib import Path

import googleapiclient.errors
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import (
    CREDENTIALS_DIR,
    YOUTUBE_CATEGORY_ID,
    YOUTUBE_PRIVACY,
    YOUTUBE_SCOPES,
)

log = logging.getLogger("uploader")

TOKEN_PATH = Path(CREDENTIALS_DIR) / "token.json"
SECRETS_PATH = Path(CREDENTIALS_DIR) / "client_secrets.json"


class QuotaExceededError(Exception):
    pass


def authenticate_youtube():
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                log.info("Token refreshed successfully")
            except Exception as e:
                log.warning(f"Token refresh failed, re-authenticating: {e}")
                creds = None

        if not creds:
            # Headless CI environment — cannot run browser OAuth flow.
            # Re-generate token.json locally and update the YOUTUBE_TOKEN_JSON secret.
            raise RuntimeError(
                "YouTube token is missing or expired and cannot be refreshed.\n"
                "Run locally: python space_autopilot/scripts/refresh_token.py\n"
                "Then update the YOUTUBE_TOKEN_JSON GitHub secret with the new token.json contents."
            )

        TOKEN_PATH.write_text(creds.to_json())
        log.info(f"Token saved to {TOKEN_PATH}")

    youtube = build("youtube", "v3", credentials=creds)
    log.info("YouTube API authenticated")
    return youtube


def upload_video(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list,
    thumbnail_path: str,
) -> str:
    safe_title = title[:100]

    body = {
        "snippet": {
            "title": safe_title,
            "description": description,
            "tags": tags,
            "categoryId": YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=1 * 1024 * 1024,
        resumable=True,
        mimetype="video/mp4",
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    log.info(f"Starting upload: {safe_title}")
    video_id = None
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                log.info(f"Upload progress: {pct}%")
        except googleapiclient.errors.HttpError as e:
            if e.resp.status == 403:
                log.error("YouTube quota exceeded")
                raise QuotaExceededError("YouTube API quota exceeded") from e
            log.error(f"Upload HTTP error: {e}")
            raise

    video_id = response.get("id")
    log.info(f"Video uploaded successfully: {video_id}")

    # Upload thumbnail
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path),
        ).execute()
        log.info(f"Thumbnail uploaded for video {video_id}")
    except googleapiclient.errors.HttpError as e:
        log.warning(f"Thumbnail upload failed (video still uploaded): {e}")

    return video_id


def check_quota_remaining() -> bool:
    try:
        youtube = authenticate_youtube()
        youtube.videoCategories().list(part="snippet", regionCode="US").execute()
        return True
    except QuotaExceededError:
        return False
    except Exception as e:
        log.warning(f"Quota check failed: {e}")
        return True
