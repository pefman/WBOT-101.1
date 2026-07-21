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
                "ollama": {"ok": True, "detail": "ok"},
                "kokoro": {"ok": True, "detail": "ok"},
                "acestep": {"ok": True, "detail": "ok"},
                "ffmpeg": {"ok": True, "detail": "ok"},
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


def test_control_play_stop(client):
    c, orch = client
    r = c.post("/api/control", json={"action": "play"})
    assert r.status_code == 200
    r = c.post("/api/control", json={"action": "stop"})
    assert r.status_code == 200
