"""Procedural album covers — Pillow only, no LLM / diffusion."""

from __future__ import annotations

import colorsys
import hashlib
import logging
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

SIZE = 512

# Base hues (0–1) by genre family — dark radio aesthetic
_GENRE_HUE: dict[str, float] = {
    "pop": 0.92,
    "rock": 0.05,
    "hiphop_rap": 0.75,
    "edm": 0.55,
    "rnb_soul": 0.88,
    "country": 0.12,
    "heavy_metal": 0.0,
    "melodic_progressive_metal": 0.58,
    "alternative_rock": 0.62,
    "indie_rock_pop": 0.15,
    "punk_rock": 0.95,
    "jazz": 0.10,
    "blues": 0.60,
    "reggae": 0.28,
    "latin": 0.08,
    "kpop": 0.85,
    "folk_singer_songwriter": 0.11,
    "classical": 0.14,
    "gospel_christian": 0.13,
    "techno_house": 0.52,
}


def _seed_rng(seed: str) -> random.Random:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(h[:16], 16))


def _rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def _font(size: int) -> ImageFont.ImageFont:
    for name in (
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines[:4]


def generate_cover(
    out_path: Path,
    *,
    title: str,
    artist: str,
    genre_id: str | None = None,
    seed: str | None = None,
    size: int = SIZE,
) -> Path:
    """
    Draw a square album cover and write PNG to ``out_path``.
    Seeded so the same segment id always yields the same art.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = _seed_rng(seed or f"{artist}|{title}|{genre_id}")

    hue = _GENRE_HUE.get(genre_id or "", rng.random())
    hue2 = (hue + 0.08 + rng.random() * 0.12) % 1.0

    img = Image.new("RGB", (size, size), _rgb(hue, 0.35, 0.08))
    draw = ImageDraw.Draw(img, "RGBA")

    # Soft radial-ish backdrop with layered blobs
    for _ in range(6):
        cx = rng.randint(-size // 5, size + size // 5)
        cy = rng.randint(-size // 5, size + size // 5)
        r = rng.randint(size // 4, size // 1)
        col = _rgb(hue2 if rng.random() > 0.4 else hue, 0.45 + rng.random() * 0.35, 0.12 + rng.random() * 0.25)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*col, 90))

    img = img.filter(ImageFilter.GaussianBlur(radius=12))
    draw = ImageDraw.Draw(img, "RGBA")

    # Geometric motif
    style = rng.choice(["rings", "bars", "grid", "arc"])
    accent = _rgb(hue, 0.55, 0.72)
    gold = (201, 164, 92)
    if style == "rings":
        cx = cy = size // 2
        for i in range(4):
            rr = size // 5 + i * (size // 10) + rng.randint(-8, 8)
            draw.ellipse(
                (cx - rr, cy - rr, cx + rr, cy + rr),
                outline=(*accent, 160 - i * 25),
                width=2 + (i == 0),
            )
    elif style == "bars":
        n = rng.randint(5, 9)
        for i in range(n):
            x0 = int(size * (0.12 + i * 0.09))
            h = int(size * (0.2 + rng.random() * 0.45))
            y0 = size // 2 - h // 2
            draw.rectangle(
                (x0, y0, x0 + size // 18, y0 + h),
                fill=(*accent, 100 + rng.randint(0, 80)),
            )
    elif style == "grid":
        step = size // rng.randint(6, 10)
        for x in range(step, size, step):
            draw.line((x, 0, x, size), fill=(*accent, 40), width=1)
        for y in range(step, size, step):
            draw.line((0, y, size, y), fill=(*accent, 40), width=1)
        # diagonal streak
        draw.line((0, size // 3, size, size * 2 // 3), fill=(*gold, 90), width=3)
    else:  # arc
        bbox = (size // 8, size // 8, size - size // 8, size - size // 8)
        draw.arc(bbox, start=rng.randint(0, 90), end=rng.randint(200, 340), fill=(*gold, 200), width=6)
        draw.arc(bbox, start=rng.randint(0, 180), end=rng.randint(220, 360), fill=(*accent, 140), width=3)

    # Bottom vignette for text readability
    for y in range(size // 2, size):
        t = (y - size // 2) / (size // 2)
        alpha = int(180 * t * t)
        draw.line((0, y, size, y), fill=(0, 0, 0, alpha))

    # Thin gold frame
    inset = 10
    draw.rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        outline=(*gold, 180),
        width=2,
    )

    # Text
    title_font = _font(max(22, size // 16))
    artist_font = _font(max(16, size // 22))
    max_w = size - 48
    title_lines = _wrap(draw, (title or "Untitled").strip(), title_font, max_w)
    artist_lines = _wrap(draw, (artist or "").strip(), artist_font, max_w)

    y = size - 36
    for line in reversed(artist_lines):
        tw = draw.textlength(line, font=artist_font)
        draw.text(((size - tw) / 2, y - artist_font.size), line, font=artist_font, fill=(200, 190, 170))
        y -= artist_font.size + 4
    y -= 6
    for line in reversed(title_lines):
        tw = draw.textlength(line, font=title_font)
        draw.text(((size - tw) / 2, y - title_font.size), line, font=title_font, fill=(244, 241, 235))
        y -= title_font.size + 6

    # Tiny station mark
    mark = _font(max(12, size // 36))
    label = "WBOT"
    tw = draw.textlength(label, font=mark)
    draw.text((size - tw - 20, 18), label, font=mark, fill=(*gold, 200))

    img = img.convert("RGB")
    img.save(out_path, format="PNG", optimize=True)
    log.info("Cover art written %s", out_path)
    return out_path
