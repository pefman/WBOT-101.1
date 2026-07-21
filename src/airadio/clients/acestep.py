from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)


def acestep_available(cmd: list[str] | None = None) -> tuple[bool, str]:
    if os.environ.get("ACESTEP_MOCK") == "1":
        return True, "ACESTEP_MOCK=1 (synthetic audio)"
    if cmd:
        exe = cmd[0]
        if shutil.which(exe) or Path(exe).exists():
            return True, f"custom cmd: {' '.join(cmd)}"
        return False, f"acestep_cmd executable not found: {exe}"
    home = os.environ.get("ACESTEP_HOME")
    if home and Path(home).is_dir():
        return True, f"ACESTEP_HOME={home}"
    if shutil.which("acestep"):
        return True, "acestep on PATH"
    # Python module probe
    try:
        import importlib.util

        if importlib.util.find_spec("acestep") is not None:
            return True, "python module acestep"
    except Exception:  # noqa: BLE001
        pass
    return False, "ACE-Step not configured (set ACESTEP_HOME, acestep_cmd, or ACESTEP_MOCK=1)"


async def generate_song(
    style: str,
    lyrics: str,
    duration_sec: int,
    out_path: Path,
    *,
    cmd: list[str] | None = None,
) -> None:
    """
    Generate a song WAV with ACE-Step 1.5 (Tier-4 friendly defaults for 8–12GB).

    Integration modes (first match wins):
    1. ACESTEP_MOCK=1 → synthetic tone bed (dev/tests)
    2. station acestep_cmd / ACESTEP_CMD → subprocess
    3. ACESTEP_HOME → run upstream generate script if present
    4. else raise with install instructions
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if os.environ.get("ACESTEP_MOCK") == "1":
        await asyncio.to_thread(_write_mock_song, out_path, duration_sec, style)
        return

    env_cmd = os.environ.get("ACESTEP_CMD")
    if env_cmd and not cmd:
        cmd = env_cmd.split()

    if cmd:
        await _run_cmd(cmd, style, lyrics, duration_sec, out_path)
        return

    home = os.environ.get("ACESTEP_HOME")
    if home:
        script = Path(home) / "generate.py"
        if script.is_file():
            py = os.environ.get("ACESTEP_PYTHON", "python")
            await _run_cmd(
                [
                    py,
                    str(script),
                    "--prompt",
                    style,
                    "--lyrics",
                    lyrics or "",
                    "--duration",
                    str(duration_sec),
                    "--output",
                    str(out_path),
                ],
                style,
                lyrics,
                duration_sec,
                out_path,
                already_built=True,
            )
            return

    raise RuntimeError(
        "ACE-Step not configured. Install ACE-Step 1.5 for 8–12GB VRAM (Tier 4: "
        "0.6B LM, INT8, CPU+DiT offload), then set ACESTEP_HOME or station.yaml "
        "acestep_cmd. For development without GPU music, export ACESTEP_MOCK=1."
    )


async def _run_cmd(
    cmd: list[str],
    style: str,
    lyrics: str,
    duration_sec: int,
    out_path: Path,
    *,
    already_built: bool = False,
) -> None:
    if already_built:
        full = cmd
    else:
        full = [
            *cmd,
            "--prompt",
            style,
            "--lyrics",
            lyrics or "",
            "--duration",
            str(duration_sec),
            "--output",
            str(out_path),
        ]
    log.info("Running ACE-Step: %s", " ".join(full[:6]) + "…")
    proc = await asyncio.create_subprocess_exec(
        *full,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"ACE-Step failed (code {proc.returncode}): {err}")
    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise RuntimeError(f"ACE-Step did not write audio to {out_path}")


def _write_mock_song(out_path: Path, duration_sec: int, style: str) -> None:
    """Pleasant placeholder chord drone for offline dev without ACE-Step."""
    sr = 44100
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    # Hash style into slight pitch variation
    base = 220.0 + (sum(ord(c) for c in style) % 80)
    wave = (
        0.25 * np.sin(2 * np.pi * base * t)
        + 0.15 * np.sin(2 * np.pi * base * 1.5 * t)
        + 0.10 * np.sin(2 * np.pi * base * 2 * t)
    )
    # Gentle amplitude envelope
    env = np.minimum(1.0, t / 0.5) * np.minimum(1.0, (duration_sec - t) / 1.0)
    wave = (wave * env * 0.4).astype(np.float32)
    # Stereo
    stereo = np.stack([wave, wave * 0.98], axis=1)
    sf.write(str(out_path), stereo, sr)
