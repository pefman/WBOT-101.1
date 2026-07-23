from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from airadio.art.sd_turbo import cover_model_status
from airadio.clients.vllm_unified import check_vllm
from airadio.config import load_djs, load_moods, load_station
from airadio.health import check_health
from airadio.languages import is_known_language, list_languages
from airadio.orchestrator import Orchestrator
from airadio.paths import ensure_bundled_espeak, static_web_dir
from airadio.prefs import load_prefs, merge_prefs
from airadio.voices import is_known_voice, list_voices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Quiet noisy HTTP client chatter so station pipeline logs stay readable
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class _QuietAccessFilter(logging.Filter):
    """Hide high-frequency UI poll requests from uvicorn access logs."""

    _paths = (
        "GET /api/now",
        "GET /api/queue",
        "GET /api/history",
        "GET /api/llm/status",
        "GET /api/health",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        return not any(p in msg for p in self._paths)


logging.getLogger("uvicorn.access").addFilter(_QuietAccessFilter())
log = logging.getLogger("airadio")


# Global vLLM subprocess reference
_vllm_subprocess: subprocess.Popen | None = None


async def start_vllm_if_needed(base_url: str) -> bool:
    """
    Check if vLLM is running externally; if not, start it internally.
    
    Returns True if vLLM is available (either externally or just started).
    """
    global _vllm_subprocess
    
    # Check if already running
    try:
        check = await check_vllm(base_url, "qwen2.5-7b-instruct", timeout=2.0)
        if check.get("ok"):
            log.info("vLLM already running externally at %s", base_url)
            return True
    except Exception:  # noqa: BLE001
        pass
    
    # Start internally if not running
    try:
        log.info("vLLM not running; starting internal subprocess…")
        # Ensure models directory exists
        models_dir = Path.cwd() / "models"
        models_dir.mkdir(exist_ok=True)
        
        # vLLM command
        cmd = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            "qwen2.5-7b-instruct",
            "--tensor-parallel-size",
            "1",
            "--gpu-memory-utilization",
            "0.8",
            "--port",
            "8000",
        ]
        
        env = {**os.environ, "HF_HOME": str(models_dir / "huggingface")}
        _vllm_subprocess = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        
        # Wait for vLLM to be ready (max 30 seconds)
        for attempt in range(60):
            await asyncio.sleep(0.5)
            try:
                check = await check_vllm(base_url, "qwen2.5-7b-instruct", timeout=1.0)
                if check.get("ok"):
                    log.info("vLLM started and ready on :8000")
                    return True
            except Exception:  # noqa: BLE001
                pass
        
        log.warning("vLLM did not become ready in time (check logs below)")
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to start vLLM: %s", exc)
        return False


async def stop_vllm() -> None:
    """Stop the internal vLLM subprocess if running."""
    global _vllm_subprocess
    if _vllm_subprocess:
        try:
            _vllm_subprocess.terminate()
            _vllm_subprocess.wait(timeout=5)
            log.info("vLLM subprocess terminated")
        except Exception as exc:  # noqa: BLE001
            log.warning("Error terminating vLLM: %s", exc)
            try:
                _vllm_subprocess.kill()
            except Exception:  # noqa: BLE001
                pass
        _vllm_subprocess = None


class ControlBody(BaseModel):
    action: str = Field(..., pattern="^(play|stop|skip)$")


class RequestBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


class FavoriteBody(BaseModel):
    segment_id: str = Field(..., min_length=1)
    favorite: bool = True


class MoodBody(BaseModel):
    mood_id: str = Field(..., min_length=1)


class GenresBody(BaseModel):
    """Enable an explicit set of genre ids (at least one)."""
    genre_ids: list[str] = Field(..., min_length=1)


class VoiceBody(BaseModel):
    voice_id: str = Field(..., min_length=2)


class DJBody(BaseModel):
    dj_id: str = Field(..., min_length=1)
    # If true, also apply the DJ's default Kokoro voice
    apply_voice: bool = True


class LanguageBody(BaseModel):
    language: str = Field(..., min_length=2, max_length=8)


def create_app() -> FastAPI:
    # Wire bundled espeak before any TTS import path can run
    ensure_bundled_espeak()

    station, genres = load_station()
    default_mood_id, moods = load_moods(all_genre_ids=list(genres.keys()))
    default_dj_id, djs = load_djs()
    if station.default_dj in djs:
        default_dj_id = station.default_dj

    # Restore desk prefs from previous session (if any)
    prefs = load_prefs(station.data_dir)
    pref_dj = str(prefs.get("dj_id") or "").strip()
    if pref_dj and pref_dj in djs:
        default_dj_id = pref_dj
    pref_lang = str(prefs.get("language") or "").strip().lower()
    if pref_lang and is_known_language(pref_lang):
        station.language = pref_lang
    pref_voice = str(prefs.get("kokoro_voice") or "").strip()
    if pref_voice and is_known_voice(pref_voice):
        station.kokoro_voice = pref_voice
    pref_genres = prefs.get("enabled_genres")
    if isinstance(pref_genres, list) and pref_genres:
        known = [g for g in pref_genres if isinstance(g, str) and g in genres]
        if known:
            station.enabled_genres = known

    orchestrator = Orchestrator(station, genres)
    orchestrator.set_system_template(station.system_prompt_template or station.system_prompt)

    # Apply default / restored DJ (name + personality + voice)
    if default_dj_id in djs:
        d = djs[default_dj_id]
        apply_voice = True
        # If prefs set an explicit voice, keep it over DJ default
        if pref_voice and is_known_voice(pref_voice):
            apply_voice = False
        orchestrator.set_dj(
            d.id,
            name=d.name,
            personality=d.personality,
            voice=d.voice,
            blurb=d.blurb,
            apply_voice=apply_voice,
            voice_samples=list(d.voice_samples),
        )
        if pref_voice and is_known_voice(pref_voice):
            orchestrator.set_voice(pref_voice, clear_pending_talk=False)

    # Genres from prefs, else station default (Radio = freeform random)
    gids = list(station.enabled_genres) if station.enabled_genres else ["radio"]
    if "radio" in genres and not gids:
        gids = ["radio"]
    label = None
    if not prefs.get("enabled_genres"):
        label = "Radio" if gids == ["radio"] else None
    orchestrator.set_genres(
        gids,
        clear_pending_songs=False,
        label=label,
    )
    log.info(
        "Desk genres at boot (%d): %s",
        len(gids),
        ", ".join(gids) if len(gids) <= 8 else ", ".join(gids[:8]) + f" … +{len(gids) - 8}",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.station = station
        app.state.genres = genres
        app.state.moods = moods
        app.state.djs = djs
        app.state.default_mood_id = default_mood_id
        app.state.default_dj_id = default_dj_id
        app.state.orchestrator = orchestrator
        
        # Start vLLM (internal or external)
        vllm_ready = await start_vllm_if_needed(station.vllm_base_url)
        if not vllm_ready:
            log.warning("vLLM may not be ready; ensure it's running before starting radio")
        
        await orchestrator.start()

        # Ensure SD-Turbo cover weights are on disk (download on first boot)
        backend = station.cover_backend.lower()
        auto_dl = station.cover_auto_download
        if auto_dl and backend in ("sd_turbo", "sdturbo", "turbo", "sd"):
            try:
                import asyncio

                from airadio.art.sd_turbo import ensure_sd_turbo_weights

                log.info("Ensuring SD-Turbo cover model is downloaded…")
                st = await asyncio.to_thread(ensure_sd_turbo_weights)
                log.info(
                    "Cover model ensure: %s — %s",
                    st.get("status"),
                    st.get("detail") or st.get("error") or "",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("SD-Turbo ensure failed (covers fall back to procedural): %s", exc)

        log.info(
            "Station «%s» ready — %d genres, %d moods, %d djs",
            station.name,
            len(genres),
            len(moods),
            len(djs),
        )
        yield
        await orchestrator.stop_workers()
        await stop_vllm()

    app = FastAPI(title="AI Radio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        return await check_health(app.state.station)

    @app.get("/api/languages")
    async def api_languages() -> dict[str, Any]:
        orch: Orchestrator = app.state.orchestrator
        return {
            "active": orch.station.language,
            "default": "en",
            "languages": list_languages(),
        }

    @app.post("/api/language")
    async def api_set_language(body: LanguageBody) -> dict[str, Any]:
        code = body.language.strip().lower()
        if not is_known_language(code):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown language: {code}. See GET /api/languages",
            )
        result = app.state.orchestrator.set_language(code)
        app.state.station.language = result["language"]
        app.state.station.system_prompt = app.state.orchestrator.station.system_prompt
        merge_prefs(app.state.station.data_dir, language=result["language"])
        return {"ok": True, **result}

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        s = app.state.station
        orch: Orchestrator = app.state.orchestrator
        return {
            "name": s.name,
            "host_name": orch.station.host_name,
            "language": orch.station.language,
            "enabled_genres": orch.station.enabled_genres,
            "mood_id": orch.mood_id,
            "mood_label": orch.mood_label,
            "dj_id": orch.dj_id,
            "dj_blurb": orch.dj_blurb,
            "buffer_min": s.buffer_min,
            "buffer_target": s.buffer_target,
            "song_duration_sec": s.song_duration_sec,
            "kokoro_voice": orch.station.kokoro_voice,
            "vllm_text_model": s.vllm_text_model,
            "vllm_base_url": s.vllm_base_url,
            "cover_backend": s.cover_backend,
            "cover_auto_download": s.cover_auto_download,
            "cover_model": cover_model_status(),
            "self_contained": True,
        }

    @app.get("/api/djs")
    async def api_djs() -> dict[str, Any]:
        djs = app.state.djs
        orch: Orchestrator = app.state.orchestrator
        return {
            "default": app.state.default_dj_id,
            "active": orch.dj_id,
            "djs": [
                {
                    "id": d.id,
                    "name": d.name,
                    "blurb": d.blurb,
                    "voice": d.voice,
                    "personality": d.personality,
                }
                for d in djs.values()
            ],
        }

    @app.post("/api/dj")
    async def api_set_dj(body: DJBody) -> dict[str, Any]:
        djs = app.state.djs
        did = body.dj_id.strip()
        if did not in djs:
            raise HTTPException(status_code=404, detail=f"Unknown DJ: {did}")
        d = djs[did]
        result = app.state.orchestrator.set_dj(
            d.id,
            name=d.name,
            personality=d.personality,
            voice=d.voice,
            blurb=d.blurb,
            apply_voice=body.apply_voice,
            voice_samples=list(d.voice_samples),
        )
        app.state.station.host_name = result["host_name"]
        app.state.station.kokoro_voice = result["voice"]
        app.state.station.system_prompt = app.state.orchestrator.station.system_prompt
        merge_prefs(
            app.state.station.data_dir,
            dj_id=result["dj_id"],
            kokoro_voice=result["voice"],
        )
        return {"ok": True, **result}

    @app.get("/api/voices")
    async def api_voices() -> dict[str, Any]:
        orch: Orchestrator = app.state.orchestrator
        return {
            "active": orch.station.kokoro_voice,
            "voices": list_voices(),
            "dj_id": orch.dj_id,
        }

    @app.post("/api/voice")
    async def api_set_voice(body: VoiceBody) -> dict[str, Any]:
        vid = body.voice_id.strip()
        if not is_known_voice(vid):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown voice id: {vid}. See GET /api/voices",
            )
        orch: Orchestrator = app.state.orchestrator
        result = orch.set_voice(vid, clear_pending_talk=True)
        app.state.station.kokoro_voice = result["voice_id"]
        merge_prefs(app.state.station.data_dir, kokoro_voice=result["voice_id"])
        return {"ok": True, **result}

    @app.get("/api/moods")
    async def api_moods() -> dict[str, Any]:
        moods = app.state.moods
        orch: Orchestrator = app.state.orchestrator
        return {
            "default": app.state.default_mood_id,
            "active": orch.mood_id,
            "moods": [
                {
                    "id": m.id,
                    "label": m.label,
                    "blurb": m.blurb,
                    "genres": list(m.genre_ids),
                }
                for m in moods.values()
            ],
        }

    @app.get("/api/genres")
    async def api_genres() -> dict[str, Any]:
        genres = app.state.genres
        orch: Orchestrator = app.state.orchestrator
        enabled = set(orch.station.enabled_genres)
        return {
            "genres": [
                {
                    "id": g.id,
                    "name": g.name,
                    "major": g.major,
                    "bpm": g.bpm,
                    "enabled": g.id in enabled,
                }
                for g in genres.values()
            ],
            "majors": sorted({g.major for g in genres.values() if g.major}),
        }

    @app.post("/api/mood")
    async def api_set_mood(body: MoodBody) -> dict[str, Any]:
        """Legacy: map a mood pack to genres (UI uses POST /api/genres)."""
        moods = app.state.moods
        mid = body.mood_id.strip()
        if mid not in moods:
            raise HTTPException(status_code=404, detail=f"Unknown mood: {mid}")
        m = moods[mid]
        gids = list(m.genre_ids) if m.genre_ids else list(app.state.genres.keys())
        try:
            result = app.state.orchestrator.set_mood(
                m.id, label=m.label, genre_ids=gids, clear_pending_songs=True
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/genres")
    async def api_set_genres(body: GenresBody) -> dict[str, Any]:
        gids = [g.strip() for g in body.genre_ids if g and str(g).strip()]
        try:
            result = app.state.orchestrator.set_genres(
                gids, clear_pending_songs=True, label=None
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        merge_prefs(
            app.state.station.data_dir,
            enabled_genres=result.get("enabled_genres") or gids,
        )
        return {"ok": True, **result}

    @app.post("/api/request")
    async def api_request(body: RequestBody) -> dict[str, Any]:
        """Queue a listener talk request for the next DJ break."""
        try:
            result = app.state.orchestrator.queue_talk_request(body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.get("/api/requests")
    async def api_requests() -> dict[str, Any]:
        orch: Orchestrator = app.state.orchestrator
        return {"pending": orch.pending_requests()}

    @app.get("/api/library")
    async def api_library() -> dict[str, Any]:
        orch: Orchestrator = app.state.orchestrator
        return {"songs": orch.library.meta_list(limit=30)}

    @app.post("/api/favorite")
    async def api_favorite(body: FavoriteBody) -> dict[str, Any]:
        try:
            result = app.state.orchestrator.favorite_song(
                body.segment_id.strip(), favorite=body.favorite
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.get("/api/now")
    async def api_now() -> dict[str, Any]:
        return app.state.orchestrator.now()

    @app.get("/api/queue")
    async def api_queue() -> dict[str, Any]:
        return {"queue": app.state.orchestrator.queue_meta()}

    @app.get("/api/history")
    async def api_history() -> dict[str, Any]:
        """Last songs that finished airplay (newest first)."""
        return {
            "songs": app.state.orchestrator.played_songs_meta(limit=8),
        }

    @app.get("/api/covers/{segment_id}.png")
    async def api_cover(segment_id: str):
        """Serve procedural album art for a segment id."""
        sid = segment_id.strip()
        if not sid or "/" in sid or ".." in sid:
            raise HTTPException(status_code=400, detail="Invalid cover id")
        orch: Orchestrator = app.state.orchestrator
        path = orch.segments_dir / f"{sid}_cover.png"
        if not path.is_file():
            # Also accept bare id if stored differently
            alt = orch.segments_dir / f"{sid}.png"
            path = alt if alt.is_file() else path
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Cover not found")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/control")
    async def api_control(body: ControlBody) -> dict[str, Any]:
        orch: Orchestrator = app.state.orchestrator
        if body.action == "play":
            health = await check_health(app.state.station)
            if not health["ok"] and not health.get("degraded"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Cannot play — dependencies unhealthy",
                        "health": health,
                    },
                )

            async def _play():
                try:
                    await orch.play()
                except Exception as exc:  # noqa: BLE001
                    log.exception("play failed: %s", exc)

            import asyncio

            asyncio.create_task(_play())
            return {"ok": True, "action": "play", "state": orch.state.value}
        if body.action == "skip":
            result = orch.skip()
            if not result.get("ok"):
                raise HTTPException(status_code=409, detail=result)
            return {"ok": True, "action": "skip", **result}
        await orch.stop()
        return {"ok": True, "action": "stop", "state": orch.state.value}

    hls_dir = station.data_dir / "hls" / "current"
    hls_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/stream/playlist.m3u8")
    async def stream_playlist():
        path = hls_dir / "index.m3u8"
        if path.is_file():
            return FileResponse(
                path,
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-cache"},
            )
        raise HTTPException(
            status_code=404,
            detail="HLS playlist not ready; try /stream/current.wav",
        )

    @app.get("/stream/current.wav")
    async def stream_wav():
        path = hls_dir / "current.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No WAV stream")
        return FileResponse(
            path, media_type="audio/wav", headers={"Cache-Control": "no-cache"}
        )

    app.mount(
        "/stream",
        StaticFiles(directory=str(hls_dir)),
        name="stream-static",
    )

    # Self-contained UI (no npm / Vite required at runtime)
    web = static_web_dir()
    if web.is_dir():
        app.mount("/static", StaticFiles(directory=str(web)), name="ui-static")

        @app.get("/")
        async def ui_index():
            index = web / "index.html"
            if not index.is_file():
                raise HTTPException(404, "UI not packaged")
            return FileResponse(index)

        @app.get("/listen")
        async def ui_listen():
            """Public listen-only page: stream + now playing, no desk controls."""
            path = web / "listen.html"
            if not path.is_file():
                raise HTTPException(404, "Listen UI not packaged")
            return FileResponse(path)

        log.info("Serving self-contained UI from %s", web)
    else:
        log.warning("No static UI at %s", web)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("airadio.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
