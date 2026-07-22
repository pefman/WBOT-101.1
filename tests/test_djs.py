"""Multi-DJ config: personalities, voices, system prompt fill."""

from pathlib import Path

from airadio.config import build_system_prompt, load_djs
from airadio.models_types import Genre, StationConfig
from airadio.orchestrator import Orchestrator


def test_load_djs_has_named_hosts():
    default_id, djs = load_djs()
    assert default_id == "rex"
    assert "rex" in djs
    assert "aria" in djs
    assert "june" in djs
    assert djs["rex"].name == "Rex"
    assert djs["rex"].voice == "bm_george"
    assert "smooth" in djs["rex"].personality.lower() or "FM" in djs["rex"].personality
    assert djs["aria"].name == "Aria"
    assert djs["june"].name == "June"


def test_build_system_prompt_includes_personality():
    tpl = "You are {host_name} on {station_name}.\n{personality}"
    out = build_system_prompt(
        tpl,
        station_name="WBOT-101.1",
        host_name="Rex",
        personality="Velvet FM energy.",
    )
    assert "Rex" in out
    assert "WBOT-101.1" in out
    assert "Velvet FM energy." in out


def test_set_dj_switches_name_personality_and_voice(tmp_path: Path):
    station = StationConfig(
        name="TestFM",
        host_name="Host",
        system_prompt="You are Host.",
        kokoro_voice="af_heart",
        ollama_model="qwen2.5:7b",
        ollama_base_url="http://127.0.0.1:11434",
        language="en",
        enabled_genres=["indie"],
        buffer_min=1,
        buffer_target=2,
        song_duration_sec=30,
        talk_max_words=80,
        data_dir=tmp_path,
        system_prompt_template=(
            "You are {host_name} on {station_name}. Personality: {personality}"
        ),
        default_dj="rex",
    )
    genres = {
        "indie": Genre(
            id="indie",
            name="Indie Rock",
            style_prompt="indie",
            lyric_style="alt",
            dj_tone="chill",
            bpm=110,
            duration_sec=30,
            major="rock",
        )
    }
    orch = Orchestrator(station, genres)
    orch.set_system_template(station.system_prompt_template)
    result = orch.set_dj(
        "rex",
        name="Rex",
        personality="Sleazy classic FM.",
        voice="bm_george",
        blurb="Smooth host",
        apply_voice=True,
    )
    assert result["host_name"] == "Rex"
    assert result["voice"] == "bm_george"
    assert orch.dj_id == "rex"
    assert orch.station.host_name == "Rex"
    assert orch.station.kokoro_voice == "bm_george"
    assert "Sleazy classic FM." in orch.station.system_prompt
    assert "Rex" in orch.station.system_prompt

    # Voice override only (keep personality)
    result2 = orch.set_dj(
        "june",
        name="June",
        personality="Chaotic fun.",
        voice="af_bella",
        blurb="Funny",
        apply_voice=False,
    )
    assert result2["host_name"] == "June"
    assert orch.station.kokoro_voice == "bm_george"  # not applied
    assert "Chaotic fun." in orch.station.system_prompt


def test_set_dj_drops_pending_talk_with_old_voice(tmp_path: Path):
    from airadio.models_types import Segment

    station = StationConfig(
        name="TestFM",
        host_name="June",
        system_prompt="You are June.",
        kokoro_voice="af_bella",
        ollama_model="qwen2.5:7b",
        ollama_base_url="http://127.0.0.1:11434",
        language="en",
        enabled_genres=["indie"],
        buffer_min=1,
        buffer_target=2,
        song_duration_sec=30,
        talk_max_words=80,
        data_dir=tmp_path,
        system_prompt_template="You are {host_name}. {personality}",
        default_dj="june",
    )
    genres = {
        "indie": Genre(
            id="indie",
            name="Indie Rock",
            style_prompt="indie",
            lyric_style="alt",
            dj_tone="chill",
            bpm=110,
            duration_sec=30,
            major="rock",
        )
    }
    orch = Orchestrator(station, genres)
    orch.set_system_template(station.system_prompt_template)
    talk = Segment(
        id="t1",
        kind="talk",
        title="On air: June",
        genre_id=None,
        text="hey",
        audio_path=tmp_path / "t.wav",
        duration_ms=5000,
        created_at=0.0,
    )
    song = Segment(
        id="s1",
        kind="song",
        title="Track",
        genre_id="indie",
        text="",
        audio_path=tmp_path / "s.wav",
        duration_ms=30000,
        created_at=0.0,
        artist="Band",
    )
    orch.ready.append(talk)
    orch.ready.append(song)
    orch._last_enqueued_kind = "song"

    result = orch.set_dj(
        "rex",
        name="Rex",
        personality="FM",
        voice="bm_george",
        blurb="sleazy",
        apply_voice=True,
    )
    assert result["removed_pending_talk"] == 1
    assert len(orch.ready) == 1
    assert orch.ready[0].kind == "song"
    assert orch.station.kokoro_voice == "bm_george"

    # Voice change also flushes talk
    orch.ready.append(
        Segment(
            id="t2",
            kind="talk",
            title="On air: Rex",
            genre_id=None,
            text="yo",
            audio_path=tmp_path / "t2.wav",
            duration_ms=4000,
            created_at=0.0,
        )
    )
    vr = orch.set_voice("am_michael")
    assert vr["removed_pending_talk"] == 1
    assert orch.station.kokoro_voice == "am_michael"
    assert all(s.kind != "talk" for s in orch.ready)
    assert orch.dj_generation >= 1


def test_stale_talk_discarded_after_dj_change(tmp_path: Path):
    """Talk started under Rex must not enqueue after switch to Vega."""
    import asyncio
    import time as time_mod

    from airadio.models_types import Segment

    station = StationConfig(
        name="TestFM",
        host_name="Rex",
        system_prompt="You are Rex.",
        kokoro_voice="bm_george",
        ollama_model="qwen2.5:7b",
        ollama_base_url="http://127.0.0.1:11434",
        language="en",
        enabled_genres=["indie"],
        buffer_min=1,
        buffer_target=2,
        song_duration_sec=30,
        talk_max_words=80,
        data_dir=tmp_path,
        system_prompt_template="You are {host_name}. {personality}",
        default_dj="rex",
    )
    genres = {
        "indie": Genre(
            id="indie",
            name="Indie Rock",
            style_prompt="indie",
            lyric_style="alt",
            dj_tone="chill",
            bpm=110,
            duration_sec=30,
            major="rock",
        )
    }

    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def slow_talk(station, out_dir, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # In-flight under Rex — hold until DJ switches
            started.set()
            await release.wait()
            return Segment(
                id="stale",
                kind="talk",
                title="On air: Rex",
                genre_id=None,
                text="I'm Rex",
                audio_path=tmp_path / "t.wav",
                duration_ms=1000,
                created_at=time_mod.time(),
                host_name="Rex",
                voice_id="bm_george",
                generation_id=kwargs.get("generation_id"),
            )
        # Later attempts match current host
        return Segment(
            id=f"fresh{calls['n']}",
            kind="talk",
            title=f"On air: {station.host_name}",
            genre_id=None,
            text=f"Hi I'm {station.host_name}",
            audio_path=tmp_path / f"t{calls['n']}.wav",
            duration_ms=1000,
            created_at=time_mod.time(),
            host_name=station.host_name,
            voice_id=station.kokoro_voice,
            generation_id=kwargs.get("generation_id"),
        )

    async def fake_song(station, genres, out_dir, **_k):
        await asyncio.sleep(0.01)
        return Segment(
            id="song1",
            kind="song",
            title="Track",
            genre_id="indie",
            text="",
            audio_path=tmp_path / "s.wav",
            duration_ms=500,
            created_at=time_mod.time(),
        )

    orch = Orchestrator(station, genres, talk_fn=slow_talk, song_fn=fake_song)
    orch.set_system_template(station.system_prompt_template)
    orch.set_dj(
        "rex",
        name="Rex",
        personality="FM",
        voice="bm_george",
        clear_pending_talk=False,
    )

    async def run():
        await orch.start()
        orch._play_event.set()  # generation only runs while "on air"
        await asyncio.wait_for(started.wait(), timeout=2)
        gen_before = orch.dj_generation
        orch.set_dj(
            "vega",
            name="Vega",
            personality="spark",
            voice="af_nova",
            blurb="upbeat",
            apply_voice=True,
        )
        assert orch.dj_generation > gen_before
        release.set()
        await asyncio.sleep(0.25)
        assert all(s.id != "stale" for s in orch.ready)
        await orch.stop()
        await orch.stop_workers()

    asyncio.run(run())
