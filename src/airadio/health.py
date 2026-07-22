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
    acestep_ok, acestep_detail = acestep_available()
    ff_ok = ffmpeg_available()
    espeak = ensure_bundled_espeak()

    # Cover art status (non-blocking for station ok — procedural fallback exists)
    cover_backend = str(getattr(station, "cover_backend", "sd_turbo") or "procedural")
    cover_info: dict = {
        "ok": True,
        "detail": f"backend={cover_backend}",
        "backend": cover_backend,
    }
    if cover_backend.lower() in ("sd_turbo", "sdturbo", "turbo", "sd"):
        try:
            from airadio.art.sd_turbo import cover_model_status, sd_turbo_available

            st = cover_model_status()
            deps_ok = sd_turbo_available()
            ready = st.get("status") in ("ready", "idle") and deps_ok
            # idle before ensure still counts as ok if deps present
            if st.get("status") == "error":
                ready = False
            cover_info = {
                "ok": bool(deps_ok),  # deps missing → warn; download error soft
                "detail": st.get("detail")
                or st.get("error")
                or ("diffusers ready" if deps_ok else "install .[cover]"),
                "backend": cover_backend,
                "model": st.get("model"),
                "status": st.get("status"),
                "path": st.get("path"),
            }
            if not deps_ok:
                cover_info["ok"] = False
        except Exception as exc:  # noqa: BLE001
            cover_info = {
                "ok": False,
                "detail": str(exc),
                "backend": cover_backend,
            }

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
        "cover": cover_info,
    }
    # LLM is required — no silent fallback scripts
    ok = ollama["ok"] and acestep_ok and kokoro_ok and ff_ok
    return {
        "ok": ok,
        "degraded": False,
        "llm_mode": ollama.get("mode") or ("live" if ollama["ok"] else "error"),
        "llm_pull": pull,
        "cover_model": cover_info,
        "components": components,
        "self_contained": True,
    }
