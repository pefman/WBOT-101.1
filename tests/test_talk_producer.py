import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import soundfile as sf

from airadio.models_types import Segment, StationConfig
from airadio.producers import talk as talk_mod


def _station(tmp_path: Path) -> StationConfig:
    return StationConfig(
        name="Test FM",
        host_name="Host",
        system_prompt="You are a host.",
        kokoro_voice="af_heart",
        ollama_model="m",
        ollama_base_url="http://127.0.0.1:11434",
        language="en",
        enabled_genres=["synthwave"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=60,
        talk_max_words=50,
        data_dir=tmp_path,
    )


def test_produce_talk_with_mocks(tmp_path, monkeypatch):
    station = _station(tmp_path)

    async def fake_chat(*a, **k):
        return "Welcome back to Test FM, friends."

    def fake_synth(text, voice, out_path):
        sr = 24000
        sf.write(str(out_path), np.zeros(sr, dtype=np.float32), sr)
        return 1000

    monkeypatch.setattr(talk_mod, "ollama_chat", fake_chat)
    monkeypatch.setattr(talk_mod, "synthesize_kokoro", fake_synth)
    monkeypatch.setattr(talk_mod, "loudnorm_ffmpeg", lambda i, o: Path(i).replace(o) or o)

    # loudnorm_ffmpeg mock needs to actually produce file
    def ln(i, o):
        Path(o).write_bytes(Path(i).read_bytes())
        return Path(o)

    monkeypatch.setattr(talk_mod, "loudnorm_ffmpeg", ln)

    seg = asyncio.run(talk_mod.produce_talk(station, tmp_path / "seg"))
    assert isinstance(seg, Segment)
    assert seg.kind == "talk"
    assert "Test FM" in seg.text or "Welcome" in seg.text
    assert seg.audio_path.is_file()
