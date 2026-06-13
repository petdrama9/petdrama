"""
config.py — Central configuration and environment management.

All secrets are read from environment variables (GitHub Secrets in CI,
or a local .env file during development).
"""

import os
import sys
import logging
from pathlib import Path

# ─── Logging ────────────────────────────────────────────────────────────────

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("config")

# ─── Project paths ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

# ─── Groq ───────────────────────────────────────────────────────────────────

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_BASE: str = "https://api.groq.com/openai/v1"

# ─── Instagram / yt-dlp cookies ──────────────────────────────────────────────
# cookies.txt is copied from the fifa-shorts-automator project.
# It lets yt-dlp bypass Instagram's login-wall for private/restricted content.
COOKIES_FILE: Path = BASE_DIR / "cookies.txt"

# ─── YouTube ────────────────────────────────────────────────────────────────

YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_TOKEN_URI: str = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPES: list[str] = ["https://www.googleapis.com/auth/youtube.upload"]

# Upload defaults
YOUTUBE_CATEGORY_ID: str = "22"          # People & Blogs — works well for Shorts
YOUTUBE_DEFAULT_PRIVACY: str = "public"   # public | private | unlisted

# ─── yt-dlp ─────────────────────────────────────────────────────────────────

YTDLP_MAX_RETRIES: int = int(os.getenv("YTDLP_MAX_RETRIES", "3"))
YTDLP_SLEEP_INTERVAL: int = int(os.getenv("YTDLP_SLEEP_INTERVAL", "2"))

# ─── GitHub ─────────────────────────────────────────────────────────────────

GITHUB_PAT: str = os.getenv("GITHUB_PAT", os.getenv("GITHUB_TOKEN", ""))

# ─── Required secrets validation ─────────────────────────────────────────────

_REQUIRED: dict[str, str] = {
    "GROQ_API_KEY": GROQ_API_KEY,
    "YOUTUBE_CLIENT_ID": YOUTUBE_CLIENT_ID,
    "YOUTUBE_CLIENT_SECRET": YOUTUBE_CLIENT_SECRET,
    "YOUTUBE_REFRESH_TOKEN": YOUTUBE_REFRESH_TOKEN,
}


def validate_secrets() -> None:
    """Raise EnvironmentError if any required secret is missing.

    YouTube auth can be provided as:
      - token.json on disk  (fifa-project pattern, written by GitHub Action)
      - OR individual env vars (YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN)
    """
    missing: list[str] = []

    # Groq is always required
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")

    # YouTube: token.json OR env vars — either is fine
    token_file = BASE_DIR / "token.json"
    has_token_json = token_file.exists() and token_file.stat().st_size > 10
    has_env_vars   = all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN])

    if not has_token_json and not has_env_vars:
        missing.append(
            "YOUTUBE credentials (either token.json OR "
            "YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET + YOUTUBE_REFRESH_TOKEN)"
        )

    if missing:
        logger.error("Missing required secrets: %s", ", ".join(missing))
        raise EnvironmentError(
            f"Missing required secrets: {', '.join(missing)}"
        )

    auth_source = "token.json" if has_token_json else "env vars"
    logger.info("All required secrets are present (YouTube auth via %s).", auth_source)


# ─── Metadata limits ─────────────────────────────────────────────────────────

TITLE_MAX_CHARS: int = 100
DESCRIPTION_MAX_CHARS: int = 5000
MAX_TAGS: int = 500          # YouTube tag character limit (total)
HASHTAG_COUNT: int = 10
