from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from airadio.clients.ollama_pull import manager as ollama_manager
from airadio.config import load_djs, load_moods, load_station
from airadio.health import check_health
from airadio.languages import is_known_language, list_languages
from airadio.orchestrator import Orchestrator
from airadio.paths import ensure_bundled_espeak, static_web_dir
from airadio.voices import is_known_voice, list_voices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("airadio")


class ControlBody(BaseModel):
    action: str = Field(..., pattern="^(play|stop)$")


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
    orchestrator = Orchestrator(station, genres)
    orchestrator.set_system_template(station.system_prompt_template or station.system_prompt)

    # Apply default DJ (name + personality + voice)
    if default_dj_id in djs:
        d = djs[default_dj_id]
        orchestrator.set_dj(
            d.id,
            name=d.name,
            personality=d.personality,
            voice=d.voice,
            blurb=d.blurb,
            apply_voice=True,
        )

    # All genres on by default (no mood packs in the UI)
    orchestrator.set_genres(
        list(genres.keys()),
        clear_pending_songs=False,
        label="All genres",
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
        await orchestrator.start()
        # Ensure Ollama model is present; pull with progress if missing
        if station.ollama_auto_pull:
            try:
                st = await ollama_manager.ensure_model(
                    station.ollama_base_url, station.ollama_model
                )
                log.info("Ollama ensure: %s", st.get("detail") or st.get("status"))
            except Exception as exc:  # noqa: BLE001
                log.warning("Ollama ensure failed: %s", exc)
        log.info(
            "Station «%s» ready — %d genres, %d moods, %d djs",
            station.name,
            len(genres),
            len(moods),
            len(djs),
        )
        yield
        await orchestrator.stop_workers()

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

    @app.get("/api/llm/status")
    async def api_llm_status() -> dict[str, Any]:
        return ollama_manager.status()

    @app.post("/api/llm/ensure")
    async def api_llm_ensure() -> dict[str, Any]:
        s = app.state.station
        if not s.ollama_auto_pull:
            raise HTTPException(
                status_code=400,
                detail="ollama_auto_pull is disabled in station.yaml",
            )
        return await ollama_manager.ensure_model(s.ollama_base_url, s.ollama_model)

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
            "ollama_model": s.ollama_model,
            "ollama_auto_pull": s.ollama_auto_pull,
            "llm_pull": ollama_manager.status(),
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
        )
        app.state.station.host_name = result["host_name"]
        app.state.station.kokoro_voice = result["voice"]
        app.state.station.system_prompt = app.state.orchestrator.station.system_prompt
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
            "songs": app.state.orchestrator.played_songs_meta(limit=5),
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

        log.info("Serving self-contained UI from %s", web)
    else:
        log.warning("No static UI at %s", web)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("airadio.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    run()
