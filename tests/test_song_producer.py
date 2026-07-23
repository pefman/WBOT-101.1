import asyncio
from pathlib import Path

import pytest

from airadio.models_types import Genre, StationConfig
from airadio.producers.song import (
    RADIO_GENRE_ID,
    pick_genre,
    _format_recent_songs,
    _compose_track,
)


def test_pick_genre_random_from_enabled():
    genres = {
        "a": Genre("a", "A", "s", "l", "d", 100, 75),
        "b": Genre("b", "B", "s", "l", "d", 100, 75),
    }
    seen = {pick_genre(genres, ["a"]).id for _ in range(30)}
    assert seen == {"a"}


def test_pick_genre_radio_shuffles_all_concrete():
    genres = {
        RADIO_GENRE_ID: Genre("radio", "Radio", "s", "l", "d", 100, 75),
        "rock": Genre("rock", "Rock", "s", "l", "d", 100, 75),
        "jazz": Genre("jazz", "Jazz", "s", "l", "d", 100, 75),
        "pop": Genre("pop", "Pop", "s", "l", "d", 100, 75),
    }
    seen = {pick_genre(genres, ["radio"]).id for _ in range(40)}
    assert RADIO_GENRE_ID not in seen
    assert seen <= {"rock", "jazz", "pop"}
    assert len(seen) >= 2


def test_format_recent_songs():
    text = _format_recent_songs([("Band A", "Song 1"), ("Band B", "Song 2")])
    assert "Band A" in text
    assert "Do NOT reuse" in text
    assert _format_recent_songs(None) == ""


def test_compose_track_two_step(monkeypatch):
    calls: list[str] = []

    async def fake_vllm(base_url, model, system, user, **kw):
        calls.append(system[:40])
        if "A&R" in system or "playlist-ready" in system:
            return '{"artist":"Pallet Knife","title":"Cold Handle","tags_extra":"crunchy guitars, live room"}'
        return '{"lyrics":"[Intro]\\n[Verse]\\nline one short hook\\nline two short hook\\n[Chorus]\\nhook line that sticks\\nhook line that sticks\\n[Verse]\\nmore story short line\\n[Chorus]\\nhook line that sticks\\n[Outro]"}'

    monkeypatch.setattr("airadio.producers.song.vllm_generate_text", fake_vllm)

    station = StationConfig(
        name="T",
        host_name="Rex",
        system_prompt="sys",
        primary_voice="orpheus_leo",
        vllm_text_model="qwen2.5-7b-instruct",
        vllm_base_url="http://127.0.0.1:8000",
        language="en",
        enabled_genres=["rock"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=100,
        talk_max_words=80,
        data_dir=Path("/tmp"),
    )
    genre = Genre(
        "rock",
        "Rock",
        "electric guitars",
        "anthemic",
        "warm",
        120,
        100,
        major="Rock",
        tags="rock, alternative rock, energetic, electric guitars, bass, drums, 110-140 bpm",
        lyrics_skeleton="[Intro]\n[Verse]\n[line]\n[Chorus]\n[hook]\n[Outro]",
    )

    artist, title, lyrics, style = asyncio.run(
        _compose_track(
            station,
            genre,
            recent_songs=[("Old Act", "Old Title")],
        )
    )
    assert artist == "Pallet Knife"
    assert title == "Cold Handle"
    assert "hook line" in lyrics.lower()
    # ACE caption is comma tags, not a long prose paragraph
    assert "electric guitars" in style
    assert "rock" in style.lower()
    assert "Song structure:" not in style
    assert len(calls) == 2


def test_generate_song_requires_ace_api(tmp_path, monkeypatch):
    from airadio.clients import acestep as ace

    async def unreachable(_base: str) -> bool:
        return False

    monkeypatch.setattr(ace, "_api_reachable", unreachable)

    out = tmp_path / "song.wav"
    with pytest.raises(RuntimeError, match="ACE-Step API not reachable"):
        asyncio.run(ace.generate_song("synthwave night", "", 2, out))
