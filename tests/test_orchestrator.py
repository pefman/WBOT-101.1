import asyncio
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from airadio.models_types import Genre, RadioState, Segment, StationConfig
from airadio.orchestrator import Orchestrator


def _wav(path: Path, sec: float = 0.2):
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
        buffer_min=2,
        buffer_target=3,
        song_duration_sec=30,
        talk_max_words=40,
        data_dir=tmp,
        news_bit_chance=0.0,
    )


async def _fake_talk(station, out_dir, **kwargs):
    await asyncio.sleep(0.05)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"talk_{time.time_ns()}.wav"
    _wav(p, 0.15)
    return Segment(
        id=str(time.time_ns()),
        kind="talk",
        title=f"On air: {station.host_name}",
        genre_id=None,
        text="hi",
        audio_path=p,
        duration_ms=150,
        created_at=time.time(),
        host_name=station.host_name,
        voice_id=station.primary_voice,
        generation_id=kwargs.get("generation_id"),
    )


async def _fake_song(station, genres, out_dir, **_kwargs):
    await asyncio.sleep(0.05)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"song_{time.time_ns()}.wav"
    _wav(p, 0.15)
    return Segment(
        id=str(time.time_ns()),
        kind="song",
        title="Track",
        genre_id="g",
        text="",
        audio_path=p,
        duration_ms=150,
        created_at=time.time(),
    )


def test_play_waits_for_buffer_and_alternates(tmp_path):
    station = _station(tmp_path)
    genres = {"g": Genre("g", "G", "s", "l", "d", 100, 30)}
    orch = Orchestrator(station, genres, talk_fn=_fake_talk, song_fn=_fake_song)

    async def run():
        await orch.start()
        await orch.play()
        assert orch.state == RadioState.PLAYING
        assert len(orch.ready) + (1 if orch.current else 0) >= 1
        # Pattern of enqueued history starts with talk
        kinds = [s.kind for s in orch._history]
        assert kinds[0] == "talk"
        for i in range(1, min(4, len(kinds))):
            assert kinds[i] != kinds[i - 1]
        await orch.stop()
        assert orch.state == RadioState.STOPPED
        await orch.stop_workers()

    asyncio.run(run())
