import logging
import logging.handlers
import time
from pathlib import Path

import colorlog
import schedule

from config import LOGS_DIR, UPLOAD_TIME, VIDEOS_PER_DAY
from modules.idea_generator import generate_ideas
from modules.tracker import add_to_queue, get_failed_for_retry, get_queue


def setup_logging():
    log_file = Path(LOGS_DIR) / "pipeline.log"
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))

    console_handler = colorlog.StreamHandler()
    console_handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s" + fmt,
            datefmt=date_fmt,
            log_colors={"DEBUG": "cyan", "INFO": "green", "WARNING": "yellow", "ERROR": "red"},
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


setup_logging()
log = logging.getLogger("scheduler")

from main import run_pipeline  # noqa: E402 — after logging setup


def scheduled_run():
    log.info("Scheduled run triggered")
    run_pipeline()


def retry_failed():
    log.info("Checking for failed videos to retry...")
    failed = get_failed_for_retry()
    if not failed:
        log.info("No failed videos to retry")
        return
    for item in failed:
        log.info(f"Retrying: {item['title']}")
        run_pipeline(custom_title=item["title"])


def replenish_ideas():
    log.info("Weekly idea replenishment check...")
    if len(get_queue()) < 10:
        ideas = generate_ideas(count=15)
        add_to_queue(ideas)
        log.info(f"Replenished queue with {len(ideas)} ideas")
    else:
        log.info("Queue healthy, no replenishment needed")


def start_scheduler():
    log.info(f"Scheduler started — daily upload at {UPLOAD_TIME}, {VIDEOS_PER_DAY}/day")

    if VIDEOS_PER_DAY == 1:
        schedule.every().day.at(UPLOAD_TIME).do(scheduled_run)
    elif VIDEOS_PER_DAY == 2:
        schedule.every().day.at("09:00").do(scheduled_run)
        schedule.every().day.at("18:00").do(scheduled_run)
    else:
        schedule.every(8).hours.do(scheduled_run)

    schedule.every().sunday.at("10:00").do(retry_failed)
    schedule.every().monday.at("08:00").do(replenish_ideas)

    log.info("Next scheduled jobs:")
    for job in schedule.jobs:
        log.info(f"  {job}")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    start_scheduler()
