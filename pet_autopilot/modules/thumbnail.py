import logging
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("thumbnail")

GRADIENTS = [
    ((255, 167, 81), (255, 226, 89)),    # Warm orange/yellow
    ((42, 208, 168), (35, 166, 213)),    # Playful teal/cyan
    ((252, 74, 26), (247, 183, 51)),     # Bright coral/gold
    ((236, 111, 102), (243, 161, 131)),  # Soft pink/peach
    ((0, 198, 255), (0, 114, 255)),      # Pet-friendly bright blue
]

CANVAS_W, CANVAS_H = 1280, 720


def _draw_gradient(img: Image.Image, top: tuple, bottom: tuple):
    draw = ImageDraw.Draw(img)
    for y in range(CANVAS_H):
        t = y / CANVAS_H
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (CANVAS_W, y)], fill=(r, g, b))


def _draw_paw_print(img: Image.Image):
    # Simple placeholder for paw prints or just keep the gradient clean
    pass


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font_candidates = [
        "arialbd.ttf",
        "Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, max_chars: int = 22) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_thumbnail(title: str, output_path: str) -> str:
    img = Image.new("RGB", (CANVAS_W, CANVAS_H))
    gradient = random.choice(GRADIENTS)
    _draw_gradient(img, gradient[0], gradient[1])

    img = img.convert("RGBA")
    _draw_paw_print(img)
    img = img.convert("RGB")

    draw = ImageDraw.Draw(img)

    # Bottom banner
    banner_h = 80
    banner_overlay = Image.new("RGBA", (CANVAS_W, banner_h), (0, 0, 0, 160))
    img.paste(banner_overlay, (0, CANVAS_H - banner_h), banner_overlay)
    img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    banner_font = _load_font(28)
    draw.text((20, CANVAS_H - banner_h + 26), "PET FACTS", fill=(200, 200, 200), font=banner_font)

    # Watermark top-right
    wm_font = _load_font(22)
    wm_text = "Pet Drama"
    wm_bbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    wm_w = wm_bbox[2] - wm_bbox[0]
    draw.text((CANVAS_W - wm_w - 15, 15), wm_text, fill=(150, 150, 150), font=wm_font)

    # Title text
    lines = _wrap_text(title)
    font_size = 88
    while font_size > 40:
        font = _load_font(font_size)
        max_line_w = max(
            draw.textbbox((0, 0), line, font=font)[2] - draw.textbbox((0, 0), line, font=font)[0]
            for line in lines
        )
        if max_line_w <= CANVAS_W - 80:
            break
        font_size -= 6

    font = _load_font(font_size)
    line_h = font_size + 12
    total_text_h = len(lines) * line_h
    text_y = (CANVAS_H - banner_h - total_text_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (CANVAS_W - line_w) // 2
        y = text_y + i * line_h
        # Shadow
        draw.text((x + 3, y + 3), line, fill=(0, 0, 0), font=font)
        # Main text
        draw.text((x, y), line, fill=(255, 255, 255), font=font)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "JPEG", quality=95)
    log.info(f"Thumbnail saved: {output_path}")
    return output_path
