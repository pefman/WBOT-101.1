"""Skip, generation progress, talk requests, library hooks."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from airadio.models_types import Genre, RadioState, Segment, StationConfig
from airadio.orchestrator import Orchestrator
from airadio.producers import talk as talk_mod


def _wav(path: Path, sec: float = 0.2) -> None:
    sr = 22050
    sf.write(str(path), np.zeros(int(sr * sec), dtype=np.float32), sr)


def _station(tmp: Path) -> StationConfig:
    return StationConfig(
        name="T",
        host_name="H",
        system_prompt="s",
        primary_voice="orpheus_leo",
        vllm_text_model="qwen2.5-7b-instruct",
        vllm_base_url="http://x",
        language="en",
        enabled_genres=["g"],
        buffer_min=1,
        buffer_target=2,
        song_duration_sec=30,
        talk_max_words=40,
        data_dir=tmp,
        news_bit_chance=0.0,
        reair_chance=0.0,
        library_max_songs=10,
    )


async def _fake_talk(station, out_dir, **kwargs):
    await asyncio.sleep(0.02)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"talk_{time.time_ns()}.wav"
    _wav(p, 0.25)
    req = kwargs.get("user_request")
    text = f"REQ:{req}" if req else "hi"
    if kwargs.get("on_stage"):
        kwargs["on_stage"]("talk_writing", "writing…")
        kwargs["on_stage"]("talk_speaking", "tts…")
    return Segment(
        id=str(time.time_ns()),
        kind="talk",
        title=f"On air: {station.host_name}",
        genre_id=None,
        text=text,
        audio_path=p,
        duration_ms=250,
        created_at=time.time(),
        host_name=station.host_name,
        voice_id=station.primary_voice,
        generation_id=kwargs.get("generation_id"),
    )


async def _fake_song(station, genres, out_dir, **kwargs):
    await asyncio.sleep(0.02)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"song_{time.time_ns()}.wav"
    _wav(p, 0.25)
    if kwargs.get("on_stage"):
        kwargs["on_stage"]("song_music", "ace…")
    return Segment(
        id=str(time.time_ns()),
        kind="song",
        title="Track",
        genre_id="g",
        text="",
        audio_path=p,
        duration_ms=250,
        created_at=time.time(),
        artist="Band",
    )


def test_skip_and_generation_in_now(tmp_path):
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)

    async def run():
        await orch.start()
        await orch.play()
        assert orch.state == RadioState.PLAYING
        # Wait until something is current
        for _ in range(50):
            if orch.current is not None:
                break
            await asyncio.sleep(0.05)
        snap = orch.now()
        assert "generation" in snap
        assert "ready" in snap["generation"]
        assert "progress" in snap["generation"]
        r = orch.skip()
        assert r["ok"] is True
        assert r["skipped"] is True
        await orch.stop()
        await orch.stop_workers()

    asyncio.run(run())


def test_talk_request_consumed(tmp_path):
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)
    orch.queue_talk_request("do a weather bit")
    assert orch.pending_requests() == ["do a weather bit"]

    async def run():
        await orch.start()
        await orch.play()
        # Allow worker to produce talk that consumes request
        for _ in range(100):
            if any(s.kind == "talk" and s.text.startswith("REQ:") for s in orch._history):
                break
            await asyncio.sleep(0.05)
        talks = [s for s in orch._history if s.kind == "talk"]
        assert talks
        assert any("weather" in (t.text or "") for t in talks)
        assert orch.pending_requests() == []
        await orch.stop()
        await orch.stop_workers()

    asyncio.run(run())


def test_build_user_prompt_includes_request():
    station = StationConfig(
        name="WBOT",
        host_name="Rex",
        system_prompt="host",
        primary_voice="orpheus_leo",
        vllm_text_model="qwen2.5-7b-instruct",
        vllm_base_url="http://x",
        language="en",
        enabled_genres=["pop"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=60,
        talk_max_words=80,
        data_dir=Path("/tmp"),
    )
    text = talk_mod._build_user_prompt(
        station,
        None,
        None,
        mode="bridge",
        mode_instruction="bridge",
        spice="spice",
        mood_label=None,
        mood_genres=None,
        recent_talk=None,
        news_angle=None,
        max_words=40,
        user_request="roast the last track gently",
    )
    assert "LISTENER REQUEST" in text
    assert "roast the last track" in text


def test_skip_when_stopped(tmp_path):
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)
    r = orch.skip()
    assert r["ok"] is False
    assert r["reason"] == "not_on_air"


def test_talk_context_queued_song_is_prev_not_next(tmp_path):
    """Songs already in the buffer play *before* a new talk — not 'coming up'."""
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)
    p = tmp_path / "s.wav"
    _wav(p, 0.2)
    song = Segment(
        id="song1",
        kind="song",
        title="Neon River",
        genre_id="g",
        text="",
        audio_path=p,
        duration_ms=200,
        created_at=time.time(),
        artist="Wire Saints",
    )
    orch.ready.append(song)
    prev = orch._prev_song_for_new_talk()
    nxt = orch._next_song_for_new_talk()
    assert prev is not None and prev.id == "song1"
    assert nxt is None  # must not tease the song that already precedes this talk


def test_talk_names_song_helper():
    talk = Segment(
        id="t",
        kind="talk",
        title="On air",
        genre_id=None,
        text="Coming up next, Wire Saints with Neon River, stay with us.",
        audio_path=Path("/tmp/x.wav"),
        duration_ms=1000,
        created_at=time.time(),
    )
    song = Segment(
        id="s",
        kind="song",
        title="Neon River",
        genre_id="g",
        text="",
        audio_path=Path("/tmp/y.wav"),
        duration_ms=1000,
        created_at=time.time(),
        artist="Wire Saints",
    )
    assert Orchestrator._talk_names_song(talk, song) is True
    talk2 = Segment(
        id="t2",
        kind="talk",
        title="On air",
        genre_id=None,
        text="That was a wild ride into the midnight hour.",
        audio_path=Path("/tmp/x.wav"),
        duration_ms=1000,
        created_at=time.time(),
    )
    assert Orchestrator._talk_names_song(talk2, song) is False


def test_set_genres_bumps_song_generation_and_clears_queue(tmp_path):
    station = _station(tmp_path)
    genres = {
        "g": Genre("g", "G", "s", "l", "d", 100, 30),
        "indie": Genre("indie", "Indie", "s", "l", "d", 100, 30),
        "melodic_progressive_metal": Genre(
            "melodic_progressive_metal", "MPM", "s", "l", "d", 100, 30
        ),
    }
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)
    # Pretend a wrong-genre song + stale talk are queued
    p = tmp_path / "indie.wav"
    _wav(p, 0.2)
    orch.ready.append(
        Segment(
            id="bad",
            kind="song",
            title="Indie Hit",
            genre_id="indie",
            text="",
            audio_path=p,
            duration_ms=200,
            created_at=time.time(),
        )
    )
    orch.ready.append(
        Segment(
            id="talky",
            kind="talk",
            title="On air",
            genre_id=None,
            text="that was an old reggae joint",
            audio_path=p,
            duration_ms=200,
            created_at=time.time(),
        )
    )
    gen0 = orch.song_generation
    r = orch.set_genres(["melodic_progressive_metal"], clear_pending_songs=True)
    assert r["removed_pending_songs"] == 1
    assert r["removed_pending_talks"] == 1
    assert orch.song_generation == gen0 + 1
    assert len(orch.ready) == 0
    assert orch.station.enabled_genres == ["melodic_progressive_metal"]


def test_last_song_only_from_airplay_not_generate_history(tmp_path):
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)
    p = tmp_path / "x.wav"
    _wav(p, 0.2)
    ghost = Segment(
        id="ghost",
        kind="song",
        title="Never Aired Reggae",
        genre_id="reggae",
        text="",
        audio_path=p,
        duration_ms=200,
        created_at=time.time(),
        artist="Ghost",
    )
    orch._history.append(ghost)
    # Not played → DJ must not name it
    assert orch._last_song_from_history() is None
    orch._record_played_song(ghost)
    assert orch._last_song_from_history() is not None
    assert orch._last_song_from_history().title == "Never Aired Reggae"
