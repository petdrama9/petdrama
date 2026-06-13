"""
queue_manager.py — Persistent queue for reels that hit YouTube upload limits.

To prevent git merge conflicts during concurrent workflow runs, each queued reel
is stored as a separate JSON file inside the `pending_queue/` directory.
"""

from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("queue_manager")

QUEUE_DIR = Path(__file__).resolve().parent / "pending_queue"


# ─── Queue item ──────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_file_path(reel_url: str) -> Path:
    """Generate a stable, unique filename for a reel URL."""
    url_hash = hashlib.md5(reel_url.encode("utf-8")).hexdigest()
    return QUEUE_DIR / f"{url_hash}.json"


def load_queue() -> list[dict]:
    """Load all items from the pending queue directory."""
    if not QUEUE_DIR.exists():
        return []
        
    items = []
    for filepath in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            items.append(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read queue file %s: %s", filepath.name, exc)
            
    # Sort by queued_at so oldest gets retried first
    items.sort(key=lambda x: x.get("queued_at", ""))
    return items


def add_to_queue(
    reel_url: str,
    reason: str = "uploadLimitExceeded",
    extra: Optional[dict] = None,
) -> None:
    """
    Add a reel URL to the retry queue.
    Creates a new file or skips if it already exists.
    """
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _get_file_path(reel_url)

    if filepath.exists():
        logger.info("URL already in queue, skipping duplicate: %s", reel_url)
        return

    item = {
        "reel_url": reel_url,
        "reason": reason,
        "queued_at": _now_iso(),
        "attempts": 1,
        "last_attempt_at": _now_iso(),
        **(extra or {}),
    }
    
    filepath.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Added to retry queue: %s (reason: %s)", reel_url, reason)


def remove_from_queue(reel_url: str) -> None:
    """Remove a successfully processed URL from the queue."""
    filepath = _get_file_path(reel_url)
    if filepath.exists():
        try:
            filepath.unlink()
            logger.info("Removed from queue: %s", reel_url)
        except Exception as exc:
            logger.error("Failed to remove queue item %s: %s", reel_url, exc)


def bump_attempts(reel_url: str) -> None:
    """Increment attempt counter for a URL that failed again."""
    filepath = _get_file_path(reel_url)
    if not filepath.exists():
        return
        
    try:
        item = json.loads(filepath.read_text(encoding="utf-8"))
        item["attempts"] = item.get("attempts", 1) + 1
        item["last_attempt_at"] = _now_iso()
        filepath.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to bump attempts for %s: %s", reel_url, exc)


def queue_size() -> int:
    """Return number of pending items."""
    if not QUEUE_DIR.exists():
        return 0
    return len(list(QUEUE_DIR.glob("*.json")))
