"""Album covers: procedural Pillow art, or SD-Turbo background + Pillow type."""

from __future__ import annotations

import colorsys
import hashlib
import logging
import os
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

SIZE = 512

# Base hues (0–1) by genre — dark radio aesthetic
_GENRE_HUE: dict[str, float] = {
    "pop": 0.92,
    "rock": 0.05,
    "hiphop": 0.75,
    "electronic": 0.55,
    "rnb": 0.88,
    "country": 0.12,
    "metal": 0.0,
    "indie": 0.15,
    "jazz": 0.10,
    "blues": 0.60,
    "reggae": 0.28,
    "latin": 0.08,
    "folk": 0.11,
    "classical": 0.14,
    "gospel": 0.13,
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


def _draw_text_shadow(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    """Readable type over photo art without a full black bar."""
    x, y = xy
    for dx, dy in ((0, 2), (2, 0), (-2, 0), (0, -1), (1, 1), (-1, 1), (2, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 200))
    draw.text((x, y), text, font=font, fill=(*fill, 255))


def _overlay_type(
    img: Image.Image,
    *,
    title: str,
    artist: str,
    size: int,
) -> Image.Image:
    """Frame + title/artist on full artwork (no half-cover black bar)."""
    base = img.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    # Soft bottom scrim: blend toward black in RGB so convert() can't wipe the art
    pixels = base.load()
    band_top = int(size * 0.82)
    for y in range(band_top, size):
        t = (y - band_top) / max(1, (size - 1) - band_top)
        # 0 → ~0.55 darken only at the very bottom edge
        darken = 0.12 + 0.45 * (t * t)
        keep = 1.0 - darken
        for x in range(size):
            r, g, b = pixels[x, y]
            pixels[x, y] = (
                int(r * keep),
                int(g * keep),
                int(b * keep),
            )

    # Text / frame on a transparent layer, then composite (preserves full art)
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    gold = (201, 164, 92)

    inset = 10
    draw.rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        outline=(*gold, 200),
        width=2,
    )

    title_font = _font(max(22, size // 16))
    artist_font = _font(max(16, size // 22))
    max_w = size - 48
    title_lines = _wrap(draw, (title or "Untitled").strip(), title_font, max_w)
    artist_lines = _wrap(draw, (artist or "").strip(), artist_font, max_w)

    y = size - 28
    for line in reversed(artist_lines):
        tw = draw.textlength(line, font=artist_font)
        _draw_text_shadow(
            draw,
            ((size - tw) / 2, y - artist_font.size),
            line,
            font=artist_font,
            fill=(230, 220, 200),
        )
        y -= artist_font.size + 3
    y -= 4
    for line in reversed(title_lines):
        tw = draw.textlength(line, font=title_font)
        _draw_text_shadow(
            draw,
            ((size - tw) / 2, y - title_font.size),
            line,
            font=title_font,
            fill=(255, 252, 245),
        )
        y -= title_font.size + 5

    mark = _font(max(12, size // 36))
    label = "WBOT"
    tw = draw.textlength(label, font=mark)
    _draw_text_shadow(
        draw,
        (size - tw - 20, 16),
        label,
        font=mark,
        fill=gold,
    )

    composed = Image.alpha_composite(base.convert("RGBA"), overlay)
    return composed.convert("RGB")


def generate_procedural_cover(
    out_path: Path,
    *,
    title: str,
    artist: str,
    genre_id: str | None = None,
    seed: str | None = None,
    size: int = SIZE,
) -> Path:
    """Classic geometric Pillow cover (no neural net)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rng = _seed_rng(seed or f"{artist}|{title}|{genre_id}")

    hue = _GENRE_HUE.get(genre_id or "", rng.random())
    # progressive metal pack maps near metal
    if genre_id == "melodic_progressive_metal":
        hue = 0.72
    hue2 = (hue + 0.08 + rng.random() * 0.12) % 1.0

    img = Image.new("RGB", (size, size), _rgb(hue, 0.35, 0.08))
    draw = ImageDraw.Draw(img, "RGBA")

    for _ in range(6):
        cx = rng.randint(-size // 5, size + size // 5)
        cy = rng.randint(-size // 5, size + size // 5)
        r = rng.randint(size // 4, size // 1)
        col = _rgb(
            hue2 if rng.random() > 0.4 else hue,
            0.45 + rng.random() * 0.35,
            0.12 + rng.random() * 0.25,
        )
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*col, 90))

    img = img.filter(ImageFilter.GaussianBlur(radius=12))
    draw = ImageDraw.Draw(img, "RGBA")

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
        draw.line((0, size // 3, size, size * 2 // 3), fill=(*gold, 90), width=3)
    else:
        bbox = (size // 8, size // 8, size - size // 8, size - size // 8)
        draw.arc(
            bbox,
            start=rng.randint(0, 90),
            end=rng.randint(200, 340),
            fill=(*gold, 200),
            width=6,
        )
        draw.arc(
            bbox,
            start=rng.randint(0, 180),
            end=rng.randint(220, 360),
            fill=(*accent, 140),
            width=3,
        )

    img = _overlay_type(img, title=title, artist=artist, size=size)
    img.save(out_path, format="PNG", optimize=True)
    log.info("Procedural cover written %s", out_path)
    return out_path


def generate_sd_turbo_cover(
    out_path: Path,
    *,
    title: str,
    artist: str,
    genre_id: str | None = None,
    seed: str | None = None,
    size: int = SIZE,
    steps: int = 2,
) -> Path:
    """SD-Turbo background + Pillow title/artist overlay."""
    from airadio.art.sd_turbo import generate_sd_background

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bg = generate_sd_background(
        title=title,
        artist=artist,
        genre_id=genre_id,
        seed=seed,
        size=size,
        steps=steps,
    )
    img = _overlay_type(bg, title=title, artist=artist, size=size)
    img.save(out_path, format="PNG", optimize=True)
    log.info("SD-Turbo cover written %s", out_path)
    return out_path


def resolve_cover_backend(explicit: str | None = None) -> str:
    """Return ``sd_turbo`` or ``procedural``."""
    raw = (explicit or os.environ.get("AIRADIO_COVER_BACKEND") or "sd_turbo").strip().lower()
    if raw in ("sd_turbo", "sdturbo", "turbo", "sd"):
        return "sd_turbo"
    return "procedural"


def generate_cover(
    out_path: Path,
    *,
    title: str,
    artist: str,
    genre_id: str | None = None,
    seed: str | None = None,
    size: int = SIZE,
    backend: str | None = None,
    steps: int = 2,
) -> Path:
    """
    Draw a square album cover and write PNG to ``out_path``.

    Default backend is SD-Turbo when available; falls back to procedural art
    if the model/deps fail. Seeded so the same segment id is stable for
    procedural; SD-Turbo uses the seed as a torch generator seed.
    """
    choice = resolve_cover_backend(backend)
    if choice == "sd_turbo":
        try:
            from airadio.art.sd_turbo import sd_turbo_available

            if not sd_turbo_available():
                raise RuntimeError("diffusers/torch not available")
            return generate_sd_turbo_cover(
                out_path,
                title=title,
                artist=artist,
                genre_id=genre_id,
                seed=seed,
                size=size,
                steps=steps,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("SD-Turbo cover failed (%s); using procedural fallback", exc)

    return generate_procedural_cover(
        out_path,
        title=title,
        artist=artist,
        genre_id=genre_id,
        seed=seed,
        size=size,
    )
