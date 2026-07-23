from __future__ import annotations

from airadio.audio.process import ffmpeg_available, ffmpeg_exe
from airadio.clients.acestep import acestep_available
# Health checks: vLLM, Orpheus TTS, ACE-Step
from airadio.clients.orpheus import orpheus_available
from airadio.clients.vllm_unified import check_vllm
from airadio.models_types import StationConfig
from airadio.paths import ensure_bundled_espeak


async def check_health(station: StationConfig) -> dict:
    vllm = await check_vllm(station.vllm_base_url, station.vllm_text_model)
    orpheus_ok, orpheus_detail = orpheus_available()
    acestep_ok, acestep_detail = acestep_available()
    ff_ok = ffmpeg_available()
    espeak = ensure_bundled_espeak()

    # Cover art status (non-blocking for station ok — procedural fallback exists)
    cover_backend = station.cover_backend
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
        "vllm": vllm,
        "orpheus": {"ok": orpheus_ok, "detail": orpheus_detail},
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
    # vLLM starts on-demand, so if service unreachable but will auto-start, mark degraded
    # (allows play but indicates suboptimal state)
    vllm_offline_will_start = (
        not vllm["ok"]
        and "will start on-demand" in vllm.get("detail", "")
    )
    degraded = vllm_offline_will_start  # Degraded if vLLM starting on-demand
    ok = acestep_ok and orpheus_ok and ff_ok  # ACE/Orpheus/ffmpeg required; vLLM has fallback
    return {
        "ok": ok,
        "degraded": degraded,
        "llm_mode": vllm.get("mode") or ("live" if vllm["ok"] else "error"),
        "cover_model": cover_info,
        "components": components,
        "self_contained": True,
    }
