from __future__ import annotations

from airadio.audio.process import ffmpeg_available, ffmpeg_exe
from airadio.clients.acestep import acestep_available
from airadio.clients.kokoro import kokoro_available
from airadio.clients.ollama import check_ollama
from airadio.clients.ollama_pull import manager as ollama_manager
from airadio.models_types import StationConfig
from airadio.paths import ensure_bundled_espeak


async def check_health(station: StationConfig) -> dict:
    ollama = await check_ollama(station.ollama_base_url, station.ollama_model)
    pull = ollama_manager.status()
    kokoro_ok, kokoro_detail = kokoro_available()
    acestep_ok, acestep_detail = acestep_available(station.acestep_cmd)
    ff_ok = ffmpeg_available()
    espeak = ensure_bundled_espeak()

    components = {
        "llm": ollama,
        "kokoro": {"ok": kokoro_ok, "detail": kokoro_detail},
        "acestep": {"ok": acestep_ok, "detail": acestep_detail},
        "ffmpeg": {
            "ok": ff_ok,
            "detail": f"bundled venv ffmpeg: {ffmpeg_exe()}" if ff_ok else "bundled ffmpeg missing",
        },
        "espeak": {
            "ok": bool(espeak.get("library")),
            "detail": espeak.get("library") or "espeakng-loader not in venv",
        },
    }
    # LLM is required — no silent fallback scripts
    ok = ollama["ok"] and acestep_ok and kokoro_ok and ff_ok
    return {
        "ok": ok,
        "degraded": False,
        "llm_mode": ollama.get("mode") or ("live" if ollama["ok"] else "error"),
        "llm_pull": pull,
        "components": components,
        "self_contained": True,
    }
