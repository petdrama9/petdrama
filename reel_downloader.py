"""
reel_downloader.py — Download Instagram Reels using yt-dlp.

Returns the local video path and the caption text (if available).
Includes retry logic and graceful error handling.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yt_dlp

import config

logger = logging.getLogger("reel_downloader")


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ReelInfo:
    """Holds the downloaded reel metadata and local path."""

    url: str
    video_path: Path
    caption: str = ""
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def is_vertical(self) -> bool:
        """True when the video is portrait-oriented (suitable for Shorts)."""
        if self.height and self.width:
            return self.height > self.width
        return True  # assume vertical if we can't detect


# ─── Downloader ──────────────────────────────────────────────────────────────

class ReelDownloader:
    """Downloads an Instagram Reel and extracts available metadata."""

    def __init__(
        self,
        output_dir: Path = config.DOWNLOAD_DIR,
        max_retries: int = config.YTDLP_MAX_RETRIES,
        sleep_interval: int = config.YTDLP_SLEEP_INTERVAL,
    ) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.sleep_interval = sleep_interval

    # ── yt-dlp options ──────────────────────────────────────────────────────

    def _build_ydl_opts(self, outtmpl: str) -> dict:
        """Return a yt-dlp options dictionary."""
        opts: dict = {
            # Output template
            "outtmpl": outtmpl,
            # Format: prefer pre-merged mp4 (no ffmpeg needed), fallback to merging when ffmpeg present
            "format": "best[ext=mp4][height<=1080]/bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            # Network
            "retries": self.max_retries,
            "sleep_interval": self.sleep_interval,
            "socket_timeout": 30,
            # Privacy / anti-block — mobile UA matches what Instagram expects
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
                ),
            },
            # Quiet operation — we use our own logging
            "quiet": True,
            "no_warnings": False,
            "verbose": False,
            # Postprocessors
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }
            ],
        }

        # Use cookies.txt from the fifa-shorts-automator project for Instagram auth
        if config.COOKIES_FILE.exists():
            opts["cookiefile"] = str(config.COOKIES_FILE)
            logger.info("Using cookies file: %s", config.COOKIES_FILE)
        else:
            logger.warning(
                "cookies.txt not found at %s — Instagram may block the download.",
                config.COOKIES_FILE,
            )

        return opts

    # ── Caption extraction ──────────────────────────────────────────────────

    @staticmethod
    def _clean_caption(raw: str) -> str:
        """Normalise whitespace and trim caption to a reasonable length."""
        if not raw:
            return ""
        # Collapse newlines and excessive spaces
        cleaned = re.sub(r"\s+", " ", raw.strip())
        # Truncate to 2000 chars (more than enough context for the AI)
        return cleaned[:2000]

    # ── Core download logic ─────────────────────────────────────────────────

    def download(self, reel_url: str) -> ReelInfo:
        """
        Download the reel at *reel_url* and return a :class:`ReelInfo`.

        Raises:
            RuntimeError: if the download fails after all retries.
        """
        logger.info("Starting download for: %s", reel_url)

        # Sanitise filename using yt-dlp's %(id)s
        outtmpl = str(self.output_dir / "%(id)s.%(ext)s")
        ydl_opts = self._build_ydl_opts(outtmpl)

        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    logger.info("Attempt %d/%d …", attempt, self.max_retries)
                    info = ydl.extract_info(reel_url, download=True)

                if info is None:
                    raise RuntimeError("yt-dlp returned no info dict.")

                # Resolve the actual output file path
                video_path = Path(ydl.prepare_filename(info))
                # yt-dlp may merge to .mp4 even if the template says otherwise
                if not video_path.exists():
                    # Try common alternatives
                    for suffix in (".mp4", ".mkv", ".webm"):
                        alt = video_path.with_suffix(suffix)
                        if alt.exists():
                            video_path = alt
                            break

                if not video_path.exists():
                    raise FileNotFoundError(
                        f"Downloaded file not found at: {video_path}"
                    )

                caption = self._clean_caption(
                    info.get("description", "") or info.get("title", "")
                )

                reel = ReelInfo(
                    url=reel_url,
                    video_path=video_path,
                    caption=caption,
                    title=info.get("title", ""),
                    uploader=info.get("uploader", ""),
                    duration=float(info.get("duration") or 0),
                    width=int(info.get("width") or 0),
                    height=int(info.get("height") or 0),
                    extra=info,
                )

                logger.info(
                    "Download complete: %s (%.1fs, %dx%d)",
                    video_path.name,
                    reel.duration,
                    reel.width,
                    reel.height,
                )
                return reel

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Attempt %d failed: %s. Retrying in %ds…",
                    attempt,
                    exc,
                    self.sleep_interval * attempt,
                )
                time.sleep(self.sleep_interval * attempt)

        raise RuntimeError(
            f"Failed to download reel after {self.max_retries} attempts: {last_exc}"
        ) from last_exc


# ─── Convenience function ────────────────────────────────────────────────────

def download_reel(reel_url: str) -> ReelInfo:
    """Module-level helper — downloads and returns :class:`ReelInfo`."""
    downloader = ReelDownloader()
    return downloader.download(reel_url)
