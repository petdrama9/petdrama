from __future__ import annotations
import argparse
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path

import colorlog

from config import LOGS_DIR, MONEY_PRINTER_URL


def setup_logging():
    log_file = Path(LOGS_DIR) / "pipeline.log"

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))
    file_handler.setLevel(logging.DEBUG)

    color_fmt = "%(log_color)s" + fmt
    console_handler = colorlog.StreamHandler()
    console_handler.setFormatter(
        colorlog.ColoredFormatter(
            color_fmt,
            datefmt=date_fmt,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


setup_logging()
log = logging.getLogger("pipeline")

# Deferred imports so logging is set up first
from modules.idea_generator import generate_ideas, generate_description, generate_tags, generate_script, generate_video_terms, select_voice
from modules.video_creator import check_moneyprinter_running, create_video

from modules.uploader import authenticate_youtube, upload_video, QuotaExceededError
from modules.tracker import (
    add_to_queue,
    get_failed_for_retry,
    get_queue,
    get_stats,
    is_duplicate,
    mark_failed,
    mark_uploaded,
    pop_from_queue,
)


def run_pipeline(custom_title: str | None = None, dry_run: bool = False) -> bool:
    log.info("=" * 50)
    log.info("SPACE AUTOPILOT PIPELINE STARTED")
    log.info("=" * 50)

    title = None
    try:
        # Step 1: MoneyPrinterTurbo health check
        log.info(f"Checking MoneyPrinterTurbo at {MONEY_PRINTER_URL}...")
        if not check_moneyprinter_running():
            log.error("MoneyPrinterTurbo is NOT running")
            log.error(f"Start it: cd MoneyPrinterTurbo && python main.py")
            return False
        log.info("MoneyPrinterTurbo: OK")

        # Step 2: Get title
        if custom_title:
            title = custom_title
            log.info(f"Using custom title: {title}")
            if is_duplicate(title):
                log.warning(f"Duplicate, skipping: {title}")
                return True
        else:
            while True:
                if len(get_queue()) < 3:
                    log.info("Queue low — generating 10 new ideas via Gemini...")
                    ideas = generate_ideas(count=10)
                    add_to_queue(ideas)

                title = pop_from_queue()
                if not title:
                    log.error("No ideas in queue and generation failed")
                    return False

                if is_duplicate(title):
                    log.warning(f"Duplicate, skipping: {title}")
                    continue
                break

        log.info(f"Title: {title}")

        # Step 3: Metadata + script (all via Gemini, before MPT call)
        log.info("Generating script, description and tags via Gemini...")
        script = generate_script(title)
        description = generate_description(title)
        tags = generate_tags(title)
        log.info(f"Script: {len(script.split())} words | Tags: {len(tags)}")

        video_terms = generate_video_terms(script)
        log.info(f"Video terms: {video_terms}")

        # Select voice
        log.info("Selecting dynamic voice based on title...")
        voice = select_voice(title)

        # Step 4: Video creation (script passed in — MPT skips its own LLM call)
        log.info("Sending to MoneyPrinterTurbo (may take 5-10 minutes)...")
        video_path = create_video(title, script=script, video_terms=video_terms, voice_name=voice)
        log.info(f"Video ready: {video_path}")


        if dry_run:
            log.info("DRY RUN — skipping YouTube upload")
            log.info(f"Title:     {title}")
            log.info(f"Video:     {video_path}")

            log.info(f"Tags:      {tags}")
            return True

        # Step 6: YouTube upload
        log.info("Authenticating YouTube...")
        youtube = authenticate_youtube()

        log.info("Uploading video to YouTube...")
        video_id = upload_video(
            youtube=youtube,
            video_path=video_path,
            title=title,
            description=description,
            tags=tags,
        )

        video_url = f"https://youtube.com/watch?v={video_id}"
        log.info(f"Uploaded: {video_url}")

        # Step 7: Track
        mark_uploaded(title, video_id, video_url, datetime.now().isoformat())

        log.info("=" * 50)
        log.info("PIPELINE COMPLETE")
        log.info(f"URL: {video_url}")
        log.info("=" * 50)
        return True

    except QuotaExceededError:
        log.error("YouTube quota exceeded — will retry tomorrow")
        if title:
            mark_failed(title, "YouTube quota exceeded", 1)
        return False
    except Exception as e:
        log.error(f"Pipeline failed: {e}", exc_info=True)
        if title:
            mark_failed(title, str(e), 1)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Space Autopilot — YouTube Automation Pipeline")
    parser.add_argument("--setup", action="store_true", help="Run YouTube OAuth setup only")
    parser.add_argument("--now", action="store_true", help="Run pipeline once immediately")
    parser.add_argument("--idea", type=str, metavar="TITLE", help="Use a specific video title")
    parser.add_argument("--dry-run", action="store_true", help="Run without uploading to YouTube")
    parser.add_argument("--status", action="store_true", help="Show pipeline statistics")
    parser.add_argument("--retry-failed", action="store_true", help="Retry all failed videos")
    args = parser.parse_args()

    if args.setup:
        print("Running YouTube OAuth setup...")
        authenticate_youtube()
        print("Done! token.json saved to credentials/")

    elif args.status:
        stats = get_stats()
        print(f"Uploaded : {stats['uploaded']}")
        print(f"In Queue : {stats['queued']}")
        print(f"Failed   : {stats['failed']}")

    elif args.retry_failed:
        failed = get_failed_for_retry()
        print(f"Retrying {len(failed)} failed videos...")
        for item in failed:
            run_pipeline(custom_title=item["title"])

    elif args.now or args.idea or args.dry_run:
        custom_title = args.idea
        if not custom_title and not args.dry_run:
            failed = get_failed_for_retry()
            if failed:
                custom_title = failed[0]["title"]
                log.info(f"Retrying failed video: {custom_title}")
        success = run_pipeline(custom_title=custom_title, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    else:
        parser.print_help()
        print("\nQuick start:")
        print("  python main.py --setup      # YouTube OAuth (one-time)")
        print("  python main.py --dry-run    # Test without uploading")
        print("  python main.py --now        # Run pipeline once")
        print("  python scheduler.py         # Start daily automation")
