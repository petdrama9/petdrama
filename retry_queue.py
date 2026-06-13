"""
retry_queue.py — Process the pending retry queue.

Called by the daily GitHub Actions schedule workflow.
Exits 0 whether or not items were processed — never fails the workflow.

If the queue is empty, exits immediately (< 1 second, zero waste).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime, timezone

import queue_manager

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("retry_queue")

MAX_ATTEMPTS = 5  # Drop item from queue after this many consecutive failures


def process_queue() -> None:
    queue = queue_manager.load_queue()

    if not queue:
        logger.info("✅ Queue is empty — nothing to retry. Exiting cleanly.")
        return

    logger.info("=" * 60)
    logger.info("Retry queue processor started | %d item(s) pending", len(queue))
    logger.info("=" * 60)

    succeeded = 0
    still_limited = 0
    failed = 0
    dropped = 0

    for item in list(queue):  # iterate a copy so we can mutate during loop
        reel_url = item["reel_url"]
        attempts = item.get("attempts", 1)

        logger.info(
            "─── Processing: %s (attempt %d/%d)", reel_url, attempts, MAX_ATTEMPTS
        )

        if attempts > MAX_ATTEMPTS:
            logger.warning(
                "Item exceeded max attempts (%d). Dropping: %s", MAX_ATTEMPTS, reel_url
            )
            queue_manager.remove_from_queue(reel_url)
            dropped += 1
            continue

        # Run the main pipeline as a subprocess so each reel is fully isolated
        result = subprocess.run(
            [sys.executable, "app.py", "--url", reel_url],
            capture_output=False,   # let output stream live to Actions logs
            text=True,
        )

        exit_code = result.returncode

        if exit_code == 0:
            # Could be success OR re-queued (limit still hit)
            # Check if it's still in the queue after the run
            updated_queue = queue_manager.load_queue()
            still_in_queue = any(i["reel_url"] == reel_url for i in updated_queue)

            if still_in_queue:
                # Limit still hit — bump attempt count
                queue_manager.bump_attempts(reel_url)
                still_limited += 1
                logger.warning(
                    "⏳ Still upload-limited. Attempt %d recorded. Will retry later.",
                    attempts + 1,
                )
            else:
                # Successfully uploaded and removed from queue by app.py
                succeeded += 1
                logger.info("✅ Successfully uploaded and removed from queue.")
        else:
            # Non-limit failure (download error, auth issue, etc.)
            queue_manager.bump_attempts(reel_url)
            failed += 1
            logger.error(
                "❌ Upload failed (exit code %d). Attempt %d recorded.",
                exit_code,
                attempts + 1,
            )

    # ── Summary ──────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Retry queue summary:")
    logger.info("  ✅ Uploaded successfully : %d", succeeded)
    logger.info("  ⏳ Still upload-limited  : %d", still_limited)
    logger.info("  ❌ Failed (other error)  : %d", failed)
    logger.info("  🗑️  Dropped (max attempts): %d", dropped)
    logger.info("  📋 Remaining in queue    : %d", queue_manager.queue_size())
    logger.info("=" * 60)


if __name__ == "__main__":
    process_queue()
    sys.exit(0)
