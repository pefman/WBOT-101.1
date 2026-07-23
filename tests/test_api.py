from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import airadio.main as main_mod
from airadio.models_types import RadioState


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point config at real repo station but override data_dir via monkeypatch after load
    from airadio.config import load_station
    from airadio.orchestrator import Orchestrator

    station, genres = load_station()
    station.data_dir = tmp_path
    (tmp_path / "hls" / "current").mkdir(parents=True)
    (tmp_path / "segments").mkdir(parents=True)

    orch = Orchestrator(station, genres)
    orch.state = RadioState.STOPPED
    orch.play = AsyncMock()
    orch.stop = AsyncMock()
    orch.start = AsyncMock()
    orch.stop_workers = AsyncMock()
    orch.now = MagicMock(
        return_value={
            "state": "stopped",
            "buffering_message": "",
            "segment": None,
            "station_name": station.name,
            "queue_depth": 0,
            "segment_started_at": None,
        }
    )
    orch.queue_meta = MagicMock(return_value=[])

    async def fake_health(s):
        return {
            "ok": True,
            "degraded": False,
            "components": {
                "vllm": {"ok": True, "detail": "ok"},
                "orpheus": {"ok": True, "detail": "ok"},
                "acestep": {"ok": True, "detail": "ok"},
                "ffmpeg": {"ok": True, "detail": "ok"},
                "espeak": {"ok": True, "detail": "ok"},
                "llm": {"ok": True, "detail": "ok"},
            },
        }

    monkeypatch.setattr(main_mod, "load_station", lambda: (station, genres))
    monkeypatch.setattr(main_mod, "Orchestrator", lambda s, g: orch)
    monkeypatch.setattr(main_mod, "check_health", fake_health)

    app = main_mod.create_app()
    with TestClient(app) as c:
        c.app.state.orchestrator = orch  # ensure
        yield c, orch


def test_config_and_now(client):
    c, orch = client
    r = c.get("/api/config")
    assert r.status_code == 200
    assert "name" in r.json()
    r = c.get("/api/now")
    assert r.status_code == 200
    assert r.json()["state"] == "stopped"


def test_listen_page(client):
    c, _orch = client
    r = c.get("/listen")
    assert r.status_code == 200
    body = r.text
    assert "listen.js" in body or "Now playing" in body or "Tap to listen" in body


def test_control_play_stop(client):
    c, orch = client
    r = c.post("/api/control", json={"action": "play"})
    assert r.status_code == 200
    r = c.post("/api/control", json={"action": "stop"})
    assert r.status_code == 200


def test_control_skip(client):
    c, orch = client
    orch.state = RadioState.PLAYING
    orch.current = MagicMock()
    orch.current.title = "Now"
    orch.skip = MagicMock(
        return_value={"ok": True, "skipped": True, "title": "Now", "state": "playing"}
    )
    r = c.post("/api/control", json={"action": "skip"})
    assert r.status_code == 200
    assert r.json()["action"] == "skip"
    orch.skip.assert_called_once()


def test_request_and_library_endpoints(client):
    c, orch = client
    orch.queue_talk_request = MagicMock(
        return_value={"ok": True, "queued": "weather bit", "pending": 1}
    )
    orch.pending_requests = MagicMock(return_value=["weather bit"])
    orch.library = MagicMock()
    orch.library.meta_list = MagicMock(return_value=[])
    orch.favorite_song = MagicMock(
        return_value={"id": "x", "title": "T", "artist": "A", "favorite": True}
    )

    r = c.post("/api/request", json={"text": "weather bit"})
    assert r.status_code == 200
    assert r.json()["pending"] == 1

    r = c.get("/api/requests")
    assert r.status_code == 200
    assert r.json()["pending"] == ["weather bit"]

    r = c.get("/api/library")
    assert r.status_code == 200
    assert "songs" in r.json()

    r = c.post("/api/favorite", json={"segment_id": "x", "favorite": True})
    assert r.status_code == 200
    assert r.json()["favorite"] is True
