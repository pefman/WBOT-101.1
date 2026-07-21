from __future__ import annotations

import shutil

from airadio.audio.process import ffmpeg_available
from airadio.clients.acestep import acestep_available
from airadio.clients.kokoro import kokoro_available
from airadio.clients.ollama import check_ollama
from airadio.models_types import StationConfig


async def check_health(station: StationConfig) -> dict:
    ollama = await check_ollama(station.ollama_base_url, station.ollama_model)
    kokoro_ok, kokoro_detail = kokoro_available()
    acestep_ok, acestep_detail = acestep_available(station.acestep_cmd)
    ff_ok = ffmpeg_available()

    components = {
        "ollama": ollama,
        "kokoro": {"ok": kokoro_ok, "detail": kokoro_detail},
        "acestep": {"ok": acestep_ok, "detail": acestep_detail},
        "ffmpeg": {
            "ok": ff_ok,
            "detail": "ffmpeg on PATH" if ff_ok else "ffmpeg missing (HLS/loudnorm degraded)",
        },
    }
    # Required for play: ollama + (kokoro or silence fallback) + acestep
    # Kokoro hard-required for quality; allow play if silence fallback exists but mark degraded
    required_ok = ollama["ok"] and acestep_ok
    return {
        "ok": required_ok and kokoro_ok,
        "degraded": required_ok and not kokoro_ok,
        "components": components,
        "which_espeak": bool(shutil.which("espeak-ng") or shutil.which("espeak")),
    }
