from __future__ import annotations
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

from config import (
    BGM_TYPE,
    BGM_VOLUME,
    MAX_RETRIES,
    MONEY_PRINTER_URL,
    OUTPUTS_DIR,
    PEXELS_API_KEY,
    PIXABAY_API_KEY,
    SUBTITLE_ENABLED,
    VIDEO_LANGUAGE,
    VOICE_NAME,
)

log = logging.getLogger("video_creator")


def check_moneyprinter_running() -> bool:
    for endpoint in ["/api/health", "/docs", "/"]:
        try:
            resp = requests.get(f"{MONEY_PRINTER_URL}{endpoint}", timeout=5)
            if resp.status_code == 200:
                log.info(f"MoneyPrinterTurbo reachable at {MONEY_PRINTER_URL}{endpoint}")
                return True
        except requests.exceptions.ConnectionError:
            continue
    return False


def start_moneyprinter():
    possible_dirs = [
        Path("../MoneyPrinterTurbo"),
        Path("../../MoneyPrinterTurbo"),
        Path(os.path.expanduser("~/MoneyPrinterTurbo")),
        Path("C:/MoneyPrinterTurbo"),
    ]
    mpt_dir = None
    for d in possible_dirs:
        if d.exists() and (d / "main.py").exists():
            mpt_dir = d
            break

    if not mpt_dir:
        log.error("MoneyPrinterTurbo directory not found. Start it manually.")
        return

    log.info(f"Starting MoneyPrinterTurbo from {mpt_dir}")
    subprocess.Popen(
        ["python", "main.py"],
        cwd=str(mpt_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log.info("Waiting 10 seconds for MoneyPrinterTurbo to start...")
    time.sleep(10)


def _discover_api_endpoints() -> dict:
    """Probe MoneyPrinterTurbo's API to find correct endpoints."""
    candidates = {
        "create": [
            "/api/v1/videos",
            "/api/v1/video",
            "/api/videos",
            "/api/video",
        ],
        "task_status": [
            "/api/v1/tasks/{task_id}",
            "/api/tasks/{task_id}",
            "/api/v1/video/{task_id}",
        ],
    }

    # Try to get OpenAPI spec for definitive endpoints
    for spec_url in ["/openapi.json", "/docs/openapi.json"]:
        try:
            resp = requests.get(f"{MONEY_PRINTER_URL}{spec_url}", timeout=5)
            if resp.status_code == 200:
                spec = resp.json()
                paths = spec.get("paths", {})
                create_ep = next(
                    (p for p in paths if "video" in p.lower() and "task" not in p.lower()),
                    None,
                )
                if create_ep:
                    log.info(f"Discovered create endpoint from spec: {create_ep}")
                    candidates["create"] = [create_ep] + candidates["create"]
                break
        except Exception:
            pass

    return candidates


def _find_create_endpoint() -> str:
    endpoints = _discover_api_endpoints()
    for ep in endpoints["create"]:
        try:
            # OPTIONS or HEAD to check existence
            resp = requests.options(f"{MONEY_PRINTER_URL}{ep}", timeout=5)
            if resp.status_code in (200, 405):
                return ep
        except Exception:
            pass
    # Default fallback used by MoneyPrinterTurbo v1
    return "/api/v1/videos"


def _get_mpt_base() -> Path:
    possible_roots = [
        Path("c:/Users/FRIDAY/OneDrive/Desktop/MoneyPrinterTurbo"),
        Path("../MoneyPrinterTurbo"),
        Path("../../MoneyPrinterTurbo"),
        Path(os.path.expanduser("~/MoneyPrinterTurbo")),
    ]
    for root in possible_roots:
        if root.exists():
            return root.resolve()
    return Path("../MoneyPrinterTurbo").resolve()


def _find_output_video(task_id: str) -> str | None:
    mpt_base = _get_mpt_base()
    search_roots = [
        mpt_base / "storage" / "tasks" / task_id,
        mpt_base / "output",
        mpt_base / "outputs",
    ]
    for root in search_roots:
        if root.exists():
            # Prefer final-1.mp4 over combined-1.mp4
            finals = list(root.rglob("final-*.mp4"))
            candidates = finals if finals else list(root.rglob("*.mp4"))
            if candidates:
                mp4 = candidates[0]
                log.info(f"Found output video: {mp4}")
                dest = Path(OUTPUTS_DIR) / f"{task_id}.mp4"
                shutil.copy2(str(mp4), str(dest))
                return str(dest)
    return None


def poll_task_status(task_id: str) -> dict:
    endpoints = [
        f"/api/v1/tasks/{task_id}",
        f"/api/tasks/{task_id}",
        f"/api/v1/video/{task_id}",
    ]
    for ep in endpoints:
        try:
            resp = requests.get(f"{MONEY_PRINTER_URL}{ep}", timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return {}


def create_video(title: str, script: str = "", video_terms: str = None) -> str:
    create_endpoint = _find_create_endpoint()
    log.info(f"Using create endpoint: {create_endpoint}")

    payload = {
        "video_subject": title,
        "video_script": script,
        "video_language": VIDEO_LANGUAGE,
        "voice_name": VOICE_NAME,
        "video_concat_mode": "random",
        "video_clip_duration": 3,
        "video_transition_mode": "Shuffle",
        "video_aspect": "9:16",
        "bgm_type": BGM_TYPE,
        "bgm_volume": BGM_VOLUME,
        "subtitle_enabled": SUBTITLE_ENABLED,
        "subtitle_position": "custom",
        "custom_position": 80.0,
        "text_fore_color": "#FFFF00",
        "text_background_color": "#000000",
        "font_size": 75,
        "video_source": "pexels", # Pexels works better on GitHub Actions (Pixabay blocks server IPs)
        "pexels_api_key": PEXELS_API_KEY,
    }

    if video_terms:
        payload["video_terms"] = video_terms

    task_id = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"Creating video (attempt {attempt}): {title}")
            resp = requests.post(
                f"{MONEY_PRINTER_URL}{create_endpoint}",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            task_id = data.get("task_id") or data.get("id") or data.get("data", {}).get("task_id")
            if task_id:
                log.info(f"Task created: {task_id}")
                break
        except Exception as e:
            if "resp" in locals():
                log.warning(f"Response status: {resp.status_code} | Body: {resp.text}")
            log.warning(f"Create attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(30)
            else:
                raise RuntimeError(f"Failed to create video after {MAX_RETRIES} attempts") from e

    # Poll until complete or timeout (15 min)
    timeout = 2700
    poll_interval = 15
    elapsed = 0

    # MPT state integers: -1=FAILED, 1=COMPLETE, 4=PROCESSING
    TASK_STATE_FAILED = -1
    TASK_STATE_COMPLETE = 1

    while elapsed < timeout:
        time.sleep(poll_interval)
        elapsed += poll_interval

        status_data = poll_task_status(task_id)
        data = status_data.get("data", {})
        state = data.get("state")
        progress = data.get("progress", 0)

        log.info(f"Task {task_id} state={state} progress={progress}% ({elapsed}s elapsed)")

        if state == TASK_STATE_COMPLETE:
            # Try videos array from API response (relative paths like /tasks/{id}/final-1.mp4)
            mpt_base = _get_mpt_base()
            videos = data.get("videos", [])
            for rel_path in videos:
                abs_path = mpt_base / "storage" / rel_path.lstrip("/")
                if abs_path.exists():
                    dest = Path(OUTPUTS_DIR) / f"{task_id}.mp4"
                    shutil.copy2(str(abs_path), str(dest))
                    log.info(f"Copied video from API response path: {abs_path}")
                    return str(dest)

            # Fallback: search filesystem
            found = _find_output_video(task_id)
            if found:
                return found

            raise RuntimeError(f"Task {task_id} complete but video file not found")

        if state == TASK_STATE_FAILED:
            error_msg = data.get("message") or status_data.get("message") or "Unknown error"
            raise RuntimeError(f"Video creation failed: {error_msg}")

    raise RuntimeError(f"Video creation timed out after {timeout} seconds")
