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

from airadio.config import load_station
from airadio.health import check_health
from airadio.orchestrator import Orchestrator
from airadio.paths import ensure_bundled_espeak, static_web_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("airadio")


class ControlBody(BaseModel):
    action: str = Field(..., pattern="^(play|stop)$")


def create_app() -> FastAPI:
    # Wire bundled espeak before any TTS import path can run
    ensure_bundled_espeak()

    station, genres = load_station()
    orchestrator = Orchestrator(station, genres)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.station = station
        app.state.genres = genres
        app.state.orchestrator = orchestrator
        await orchestrator.start()
        log.info("Station «%s» ready — %d genres (self-contained)", station.name, len(genres))
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

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        s = app.state.station
        return {
            "name": s.name,
            "host_name": s.host_name,
            "language": s.language,
            "enabled_genres": s.enabled_genres,
            "buffer_min": s.buffer_min,
            "buffer_target": s.buffer_target,
            "song_duration_sec": s.song_duration_sec,
            "kokoro_voice": s.kokoro_voice,
            "ollama_model": s.ollama_model,
            "self_contained": True,
        }

    @app.get("/api/now")
    async def api_now() -> dict[str, Any]:
        return app.state.orchestrator.now()

    @app.get("/api/queue")
    async def api_queue() -> dict[str, Any]:
        return {"queue": app.state.orchestrator.queue_meta()}

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
