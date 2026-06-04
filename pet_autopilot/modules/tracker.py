from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path

from config import DATA_DIR, MAX_RETRIES

log = logging.getLogger("tracker")

UPLOADED_FILE = Path(DATA_DIR) / "uploaded.json"
QUEUE_FILE = Path(DATA_DIR) / "ideas_queue.json"
FAILED_FILE = Path(DATA_DIR) / "failed.json"

_DEFAULTS = {
    UPLOADED_FILE: {"videos": [], "total_count": 0},
    QUEUE_FILE: {"queue": []},
    FAILED_FILE: {"failed": []},
}


def _init_file(path: Path, default: dict):
    if not path.exists():
        path.write_text(json.dumps(default, indent=2))


def _load(path: Path, default: dict) -> dict:
    _init_file(path, default)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        log.warning(f"Corrupt JSON in {path}, resetting")
        path.write_text(json.dumps(default, indent=2))
        return default.copy()


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# Initialize all data files on import
for _path, _default in _DEFAULTS.items():
    _init_file(_path, _default)


def load_uploaded() -> dict:
    return _load(UPLOADED_FILE, _DEFAULTS[UPLOADED_FILE])


def save_uploaded(data: dict):
    _save(UPLOADED_FILE, data)


def load_queue() -> list:
    return _load(QUEUE_FILE, _DEFAULTS[QUEUE_FILE]).get("queue", [])


def save_queue(queue: list):
    _save(QUEUE_FILE, {"queue": queue})


def load_failed() -> list:
    return _load(FAILED_FILE, _DEFAULTS[FAILED_FILE]).get("failed", [])


def save_failed(failed: list):
    _save(FAILED_FILE, {"failed": failed})


def is_duplicate(title: str) -> bool:
    data = load_uploaded()
    uploaded_titles = {v["title"].lower() for v in data.get("videos", [])}
    return title.lower() in uploaded_titles


def mark_uploaded(title: str, video_id: str, url: str, upload_time: str):
    data = load_uploaded()
    data["videos"].append({
        "title": title,
        "video_id": video_id,
        "url": url,
        "upload_time": upload_time,
    })
    data["total_count"] = len(data["videos"])
    save_uploaded(data)
    log.info(f"Marked uploaded: {title} -> {url}")

    # Remove from failed if present
    failed = load_failed()
    failed = [f for f in failed if f["title"].lower() != title.lower()]
    save_failed(failed)


def mark_failed(title: str, error: str, attempt_number: int):
    failed = load_failed()
    existing = next((f for f in failed if f["title"].lower() == title.lower()), None)
    if existing:
        existing["attempts"] = attempt_number
        existing["last_error"] = error
        existing["last_attempt"] = datetime.now().isoformat()
    else:
        failed.append({
            "title": title,
            "attempts": attempt_number,
            "last_error": error,
            "last_attempt": datetime.now().isoformat(),
        })
    save_failed(failed)
    log.info(f"Marked failed (attempt {attempt_number}): {title}")


def get_queue() -> list:
    return load_queue()


def add_to_queue(ideas: list):
    queue = load_queue()
    added = 0
    for idea in ideas:
        if not is_duplicate(idea) and idea not in queue:
            queue.append(idea)
            added += 1
    save_queue(queue)
    log.info(f"Added {added} ideas to queue (total: {len(queue)})")


def pop_from_queue() -> str | None:
    queue = load_queue()
    if not queue:
        return None
    title = queue.pop(0)
    save_queue(queue)
    return title


def get_stats() -> dict:
    return {
        "uploaded": load_uploaded().get("total_count", 0),
        "queued": len(load_queue()),
        "failed": len(load_failed()),
    }


def get_failed_for_retry() -> list:
    return [f for f in load_failed() if f.get("attempts", 0) < MAX_RETRIES]
