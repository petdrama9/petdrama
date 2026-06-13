"""
app.py — Main orchestration entry point.

Usage (GitHub Actions / CLI):
    python app.py --url "https://www.instagram.com/reel/XXXX/"

Exit codes:
    0  — success
    1  — handled error (secrets missing, download failed, etc.)
    2  — unexpected error
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import config
from reel_downloader import download_reel
from groq_generator import generate_metadata
from youtube_upload import upload_to_youtube, UploadLimitError
import queue_manager

logger = logging.getLogger("app")


# ─── Log helpers ─────────────────────────────────────────────────────────────

def _write_log(log: dict, filename: str) -> None:
    """Persist a log dictionary as a JSON file in logs/."""
    log_path = config.LOGS_DIR / filename
    with log_path.open("w", encoding="utf-8") as fh:
        json.dump(log, fh, indent=2, ensure_ascii=False, default=str)
    logger.info("Log saved: %s", log_path)


def _run_id() -> str:
    """Generate a filesystem-safe run identifier based on UTC time."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ─── Pipeline ────────────────────────────────────────────────────────────────

def run_pipeline(reel_url: str) -> dict:
    """
    Execute the full pipeline end-to-end.

    Returns a result dictionary suitable for logging / GitHub output.
    Raises on unrecoverable error.
    """
    run_id = _run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info("=" * 60)
    logger.info("Pipeline started | run_id=%s", run_id)
    logger.info("Reel URL: %s", reel_url)

    # ── Step 0: Validate secrets ─────────────────────────────────────────────
    logger.info("[1/4] Validating secrets …")
    config.validate_secrets()

    # ── Step 1: Download reel ────────────────────────────────────────────────
    logger.info("[2/4] Downloading reel …")
    reel = download_reel(reel_url)
    logger.info(
        "Reel downloaded: %s | caption length=%d",
        reel.video_path.name,
        len(reel.caption),
    )

    # ── Step 2: Generate metadata ────────────────────────────────────────────
    logger.info("[3/4] Generating metadata with Groq …")
    metadata = generate_metadata(
        caption=reel.caption,
        reel_url=reel_url,
        video_path=reel.video_path,   # ← vision model sees actual video content
    )
    logger.info("Metadata generated successfully.")

    # ── Step 3: Upload to YouTube ────────────────────────────────────────────
    logger.info("[4/4] Uploading to YouTube …")
    result = upload_to_youtube(reel.video_path, metadata)
    logger.info("Upload complete! Shorts URL: %s", result.shorts_url)

    # ── Step 4: Cleanup downloaded file ─────────────────────────────────────
    try:
        reel.video_path.unlink(missing_ok=True)
        logger.info("Cleaned up local video file.")
    except Exception:  # noqa: BLE001
        logger.warning("Could not delete temp file: %s", reel.video_path)

    # ── Build result ─────────────────────────────────────────────────────────
    finished_at = datetime.now(timezone.utc).isoformat()
    outcome = {
        "run_id": run_id,
        "status": "success",
        "reel_url": reel_url,
        "youtube_video_id": result.video_id,
        "youtube_url": result.url,
        "youtube_shorts_url": result.shorts_url,
        "title": metadata.title,
        "description_preview": metadata.description[:200],
        "hashtags": metadata.hashtags,
        "caption_preview": reel.caption[:200],
        "started_at": started_at,
        "finished_at": finished_at,
    }

    _write_log(outcome, f"run_{run_id}.json")

    # Write GitHub Actions output (if running in CI)
    _write_github_outputs(outcome)

    return outcome


def _write_github_outputs(outcome: dict) -> None:
    """Write key-value pairs to GITHUB_OUTPUT for downstream steps."""
    import os
    output_file = os.getenv("GITHUB_OUTPUT")
    if not output_file:
        return
    try:
        with open(output_file, "a", encoding="utf-8") as fh:
            fh.write(f"video_id={outcome['youtube_video_id']}\n")
            fh.write(f"shorts_url={outcome['youtube_shorts_url']}\n")
            fh.write(f"title={outcome['title']}\n")
        logger.info("GitHub outputs written.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not write GITHUB_OUTPUT: %s", exc)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download an Instagram Reel and upload it as a YouTube Short.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app.py --url "https://www.instagram.com/reel/ABC123/"
  python app.py --url "https://www.instagram.com/p/ABC123/"
        """,
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Instagram Reel URL to process.",
        metavar="URL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reel_url = args.url.strip()

    if not reel_url:
        logger.error("Reel URL is empty.")
        sys.exit(1)

    try:
        outcome = run_pipeline(reel_url)
        print("\n" + "=" * 60)
        print("SUCCESS")
        print(f"  YouTube Shorts URL : {outcome['youtube_shorts_url']}")
        print(f"  Title              : {outcome['title']}")
        print("=" * 60 + "\n")
        sys.exit(0)

    except UploadLimitError as exc:
        # YouTube daily limit hit — queue for retry, do NOT fail the run
        logger.warning("⏳ %s", exc)
        queue_manager.add_to_queue(reel_url, reason="uploadLimitExceeded")
        _write_log(
            {
                "status": "queued",
                "reason": "uploadLimitExceeded",
                "message": "Reel queued for retry after 24 hours.",
                "reel_url": reel_url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            f"queued_{_run_id()}.json",
        )
        print("\n" + "=" * 60)
        print("QUEUED — YouTube upload limit reached.")
        print(f"  Reel saved for retry in ~24 hours: {reel_url}")
        print(f"  Queue size now: {queue_manager.queue_size()}")
        print("=" * 60 + "\n")
        sys.exit(0)   # ← exit 0, this is NOT a failure

    except EnvironmentError as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    except FileNotFoundError as exc:
        logger.error("File error: %s", exc)
        sys.exit(1)

    except RuntimeError as exc:
        logger.error("Pipeline error: %s", exc)
        _write_log(
            {
                "status": "error",
                "error": str(exc),
                "reel_url": reel_url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            f"error_{_run_id()}.json",
        )
        sys.exit(1)

    except Exception as exc:  # noqa: BLE001
        logger.critical("Unexpected error: %s", exc)
        logger.debug(traceback.format_exc())
        _write_log(
            {
                "status": "unexpected_error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "reel_url": reel_url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            f"error_{_run_id()}.json",
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
