"""
groq_generator.py — Generate viral YouTube Shorts metadata using Groq.

Strategy (in priority order):
  1. Extract a frame from the video → send to Groq vision model
     → AI *sees* what's actually in the video, caption is secondary context.
  2. If vision fails (no ffmpeg, bad frame, model error) → fall back to
     caption-only text generation.

This means a caption like "footsteps in japan 🌸" on a cooking video will
still produce accurate metadata because the AI looks at the actual frames.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

import config

logger = logging.getLogger("groq_generator")


# ─── Data class ──────────────────────────────────────────────────────────────

@dataclass
class VideoMetadata:
    """Holds AI-generated metadata for a YouTube Short."""

    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tags = [h.lstrip("#") for h in self.hashtags]
        self.title = self.title[: config.TITLE_MAX_CHARS]
        self.description = self.description[: config.DESCRIPTION_MAX_CHARS]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "hashtags": self.hashtags,
            "tags": self.tags,
        }


# ─── Frame extractor ─────────────────────────────────────────────────────────

class FrameExtractor:
    """Extracts representative frames from a video using ffmpeg."""

    # Extract frames at 20%, 50%, 80% of duration — covers most content types
    TIMESTAMPS_PCT = [0.2, 0.5, 0.8]

    @staticmethod
    def _get_duration(video_path: Path) -> float:
        """Return video duration in seconds using ffprobe."""
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])

    @staticmethod
    def _extract_frame(video_path: Path, timestamp: float, out_path: Path) -> bool:
        """Extract a single JPEG frame at *timestamp* seconds."""
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "3",          # quality 1-31, lower=better
                "-vf", "scale=640:-1",  # max width 640px for API efficiency
                str(out_path),
            ],
            capture_output=True, timeout=20,
        )
        return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0

    def extract_frames(self, video_path: Path, max_frames: int = 3) -> list[str]:
        """
        Extract up to *max_frames* frames and return them as base64-encoded JPEG strings.
        Returns empty list if ffmpeg is unavailable or extraction fails.
        """
        try:
            duration = self._get_duration(video_path)
        except Exception as exc:
            logger.warning("Could not get video duration: %s", exc)
            return []

        frames_b64: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, pct in enumerate(self.TIMESTAMPS_PCT[:max_frames]):
                ts = duration * pct
                out = Path(tmp) / f"frame_{i}.jpg"
                if self._extract_frame(video_path, ts, out):
                    b64 = base64.b64encode(out.read_bytes()).decode()
                    frames_b64.append(b64)
                    logger.debug("Extracted frame at %.1fs (%.0f%%)", ts, pct * 100)
                else:
                    logger.debug("Frame extraction failed at %.1fs", ts)

        logger.info("Extracted %d frame(s) for vision analysis.", len(frames_b64))
        return frames_b64


# ─── Generator ───────────────────────────────────────────────────────────────

class GroqMetadataGenerator:
    """Generates YouTube Shorts metadata via the Groq API.

    Uses vision model when video frames are available so metadata is based
    on what's *actually in the video*, not just the (often unrelated) caption.
    """

    # Vision model — supports image inputs
    VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
    # Text-only fallback
    TEXT_MODEL   = "llama-3.3-70b-versatile"

    SYSTEM_PROMPT = (
        "You are a viral YouTube Shorts content strategist with deep expertise "
        "in SEO, audience engagement, and trending content formats. "
        "Your job is to create metadata that maximises clicks, watch-time, and discoverability. "
        "IMPORTANT: Base the metadata on what you actually SEE in the video frames. "
        "The Instagram caption may be unrelated to the actual content — treat it as "
        "secondary context only."
    )

    VISION_PROMPT_TEMPLATE = """\
I am uploading an Instagram Reel as a YouTube Short. Analyse the provided video frames
and generate optimised metadata.

Instagram caption (may or may not match the video — use as secondary context only):
{caption}

Look carefully at all the frames and describe what is actually happening in the video,
then generate the metadata based on what you SEE.

Return ONLY a valid JSON object with these exact keys:

{{
  "visual_description": "<1-2 sentences: what is actually happening in the video>",
  "title": "<viral title under {title_max} chars, hooks viewer, based on VISUAL content>",
  "description": "<SEO-rich 150-300 word description based on what you saw, end with CTA>",
  "hashtags": ["#tag1", "#tag2", ..., "#tag{hashtag_count}"]
}}

Rules:
- Title under {title_max} characters
- Title based on VISUAL content, not the caption
- Exactly {hashtag_count} hashtags, always include #Shorts and #YouTubeShorts
- No emoji in title
- Description may include 2-3 relevant emojis"""

    TEXT_PROMPT_TEMPLATE = """\
Analyse the following Instagram Reel caption and generate optimised YouTube Shorts metadata.

Instagram Caption:
{caption}

Reel URL: {url}

Return ONLY a valid JSON object:

{{
  "title": "<viral title under {title_max} chars>",
  "description": "<SEO-rich 150-300 word description, end with CTA>",
  "hashtags": ["#tag1", "#tag2", ..., "#tag{hashtag_count}"]
}}

Rules:
- Title under {title_max} characters
- Exactly {hashtag_count} hashtags, always include #Shorts and #YouTubeShorts
- No emoji in title"""

    def __init__(
        self,
        api_key: str = config.GROQ_API_KEY,
        max_retries: int = 3,
        timeout: float = 45.0,
    ) -> None:
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not set.")
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=config.GROQ_API_BASE,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        self._frame_extractor = FrameExtractor()

    # ── JSON helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_json(text: str) -> dict:
        clean = re.sub(r"```(?:json)?\s*", "", text).strip()
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON in response:\n{text[:500]}")
        return json.loads(match.group())

    @staticmethod
    def _validate_and_fix(data: dict, caption: str) -> dict:
        title = str(data.get("title", "")).strip() or "You Have to See This!"
        title = title[: config.TITLE_MAX_CHARS]

        description = str(data.get("description", "")).strip()
        if not description:
            description = caption[:500] if caption else "Watch this amazing Short!"
        description = description[: config.DESCRIPTION_MAX_CHARS]

        hashtags = data.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]
        for mandatory in ("#Shorts", "#YouTubeShorts"):
            if mandatory not in hashtags:
                hashtags.append(mandatory)
        hashtags = hashtags[: config.HASHTAG_COUNT]

        return {"title": title, "description": description, "hashtags": hashtags}

    # ── API calls ─────────────────────────────────────────────────────────────

    def _call_vision(self, frames_b64: list[str], caption: str) -> Optional[str]:
        """Call Groq vision model with video frames. Returns raw JSON string or None."""
        prompt = self.VISION_PROMPT_TEMPLATE.format(
            caption=caption or "No caption provided.",
            title_max=config.TITLE_MAX_CHARS,
            hashtag_count=config.HASHTAG_COUNT,
        )

        # Build content array: text prompt + all frames as images
        content: list[dict] = [{"type": "text", "text": prompt}]
        for b64 in frames_b64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

        payload = {
            "model": self.VISION_MODEL,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user",   "content": content},
            ],
            "temperature": 0.7,
            "max_tokens": 1200,
            # Vision model doesn't support json_object mode — we parse manually
        }

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("Vision API call attempt %d/%d …", attempt, self.max_retries)
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                content_out = resp.json()["choices"][0]["message"]["content"]
                logger.debug("Vision raw response: %s", content_out[:300])
                return content_out
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Rate limited. Waiting %ds …", wait)
                    time.sleep(wait)
                elif exc.response.status_code in (400, 404):
                    # Model not available or bad request — skip vision entirely
                    logger.warning(
                        "Vision model unavailable (HTTP %d). Falling back to text.",
                        exc.response.status_code,
                    )
                    return None
                else:
                    logger.warning("Vision attempt %d failed: %s", attempt, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Vision attempt %d error: %s", attempt, exc)
                time.sleep(2 ** attempt)

        return None

    def _call_text(self, caption: str, reel_url: str) -> str:
        """Call Groq text model with caption only. Returns raw JSON string."""
        prompt = self.TEXT_PROMPT_TEMPLATE.format(
            caption=caption or "No caption available.",
            url=reel_url,
            title_max=config.TITLE_MAX_CHARS,
            hashtag_count=config.HASHTAG_COUNT,
        )
        payload = {
            "model": self.TEXT_MODEL,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            "temperature": 0.75,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("Text API call attempt %d/%d …", attempt, self.max_retries)
                resp = self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429:
                    time.sleep(2 ** attempt)
                else:
                    raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(2 ** attempt)

        raise RuntimeError(
            f"Text API failed after {self.max_retries} attempts: {last_exc}"
        ) from last_exc

    # ── Public entry point ───────────────────────────────────────────────────

    def generate(
        self,
        caption: str,
        reel_url: str = "",
        video_path: Optional[Path] = None,
    ) -> VideoMetadata:
        """
        Generate metadata — vision-first, text fallback.

        Args:
            caption:    Instagram caption (secondary context when video available).
            reel_url:   Original reel URL.
            video_path: Path to downloaded MP4. If provided, frames are extracted
                        and sent to the vision model so metadata reflects actual
                        video content, regardless of caption accuracy.
        """
        raw: Optional[str] = None

        # ── Try vision first ──────────────────────────────────────────────────
        if video_path and video_path.exists():
            frames = self._frame_extractor.extract_frames(video_path)
            if frames:
                logger.info(
                    "Using vision model (%s) with %d frame(s) — "
                    "metadata will be based on actual video content.",
                    self.VISION_MODEL, len(frames),
                )
                raw = self._call_vision(frames, caption)
                if raw:
                    logger.info("Vision analysis complete.")
                else:
                    logger.warning("Vision failed — falling back to text model.")
            else:
                logger.info("No frames extracted — using text model.")
        else:
            logger.info("No video path provided — using text-only model.")

        # ── Fall back to text model ───────────────────────────────────────────
        if raw is None:
            raw = self._call_text(caption, reel_url)

        data  = self._extract_json(raw)
        fixed = self._validate_and_fix(data, caption)

        # Log the visual description if present (from vision model)
        if "visual_description" in data:
            logger.info("Visual description: %s", data["visual_description"])

        metadata = VideoMetadata(
            title=fixed["title"],
            description=fixed["description"],
            hashtags=fixed["hashtags"],
        )
        logger.info("Generated title: %s", metadata.title)
        logger.info("Generated hashtags: %s", " ".join(metadata.hashtags))
        return metadata

    def __del__(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


# ─── Convenience function ────────────────────────────────────────────────────

def generate_metadata(
    caption: str,
    reel_url: str = "",
    video_path: Optional[Path] = None,
) -> VideoMetadata:
    """Module-level helper — generates and returns :class:`VideoMetadata`."""
    generator = GroqMetadataGenerator()
    return generator.generate(caption, reel_url, video_path)
