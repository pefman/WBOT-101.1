from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def build_hls_from_wav(
    wav_path: Path,
    out_dir: Path,
    *,
    segment_time: int = 4,
) -> Path:
    """
    Package a WAV into HLS (AAC .ts segments + index.m3u8).
    Returns path to playlist.
    """
    wav_path = Path(wav_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = out_dir / "index.m3u8"

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg required for HLS packaging")

    # Clear previous segments in this dir
    for p in out_dir.glob("seg*.ts"):
        p.unlink(missing_ok=True)
    playlist.unlink(missing_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav_path),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-f",
        "hls",
        "-hls_time",
        str(segment_time),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        str(out_dir / "seg%03d.ts"),
        str(playlist),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not playlist.is_file():
        raise RuntimeError(f"ffmpeg HLS failed: {proc.stderr[-1500:]}")
    return playlist


def copy_wav_as_fallback(wav_path: Path, out_dir: Path) -> Path:
    """If no ffmpeg, copy WAV for simple progressive playback."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "current.wav"
    dest.write_bytes(Path(wav_path).read_bytes())
    return dest
