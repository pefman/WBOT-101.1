from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import soundfile as sf

from airadio.paths import bundled_ffmpeg

log = logging.getLogger(__name__)


def probe_duration_ms(path: Path) -> int:
    info = sf.info(str(path))
    return int(round(info.duration * 1000))


def ffmpeg_available() -> bool:
    try:
        bundled_ffmpeg()
        return True
    except Exception:  # noqa: BLE001
        return False


def ffmpeg_exe() -> str:
    return bundled_ffmpeg()


def loudnorm_ffmpeg(in_path: Path, out_path: Path, *, integrated: float = -16.0) -> Path:
    """Normalize loudness using venv-bundled ffmpeg; otherwise copy."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
        return out_path

    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(in_path),
        "-af",
        f"loudnorm=I={integrated}:TP=-1.5:LRA=11",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning("loudnorm failed, copying raw: %s", proc.stderr[-500:])
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
    return out_path
