"""Resolve binaries and data that ship inside the project venv (no system packages)."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def bundled_ffmpeg() -> str:
    """
    Path to ffmpeg shipped via the imageio-ffmpeg wheel (installed into .venv).
    Never requires a system-wide ffmpeg package.
    """
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception as exc:  # noqa: BLE001
        log.debug("imageio-ffmpeg unavailable: %s", exc)
    raise RuntimeError(
        "Bundled ffmpeg not found. Reinstall the app: pip install -e . "
        "(pulls imageio-ffmpeg into the project venv)."
    )


def ensure_bundled_espeak() -> dict[str, str]:
    """
    Provide the espeak-ng library shipped in the venv via espeakng-loader
    (no apt install espeak-ng required).
    """
    try:
        import espeakng_loader

        lib = espeakng_loader.get_library_path()
        data = espeakng_loader.get_data_path()
        espeakng_loader.make_library_available()
        # phonemizer looks at these
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", lib)
        os.environ.setdefault("ESPEAK_DATA_PATH", data)
        return {"library": lib, "data": data}
    except Exception as exc:  # noqa: BLE001
        log.warning("Bundled espeak-ng not available: %s", exc)
        return {}


def package_root() -> Path:
    """Repo root (parent of src/)."""
    return Path(__file__).resolve().parents[2]


def static_web_dir() -> Path:
    """Self-contained web UI shipped with the package."""
    # Prefer package-local static; fall back to repo web/static
    candidates = [
        Path(__file__).resolve().parent / "static",
        package_root() / "src" / "airadio" / "static",
        package_root() / "web" / "static",
    ]
    for c in candidates:
        if c.is_dir() and (c / "index.html").is_file():
            return c
    return candidates[0]
