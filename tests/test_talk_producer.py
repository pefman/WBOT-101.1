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
        vllm_text_model="qwen2.5-7b-instruct",
        vllm_base_url="http://127.0.0.1:8000",
        language="en",
        enabled_genres=["synthwave"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=60,
        talk_max_words=50,
        data_dir=tmp_path,
        news_bit_chance=0.0,
        news_angles=["diplomats arguing about snacks"],
    )


def test_news_prompt_includes_angle():
    station = StationConfig(
        name="WBOT-101.1",
        host_name="Aria",
        system_prompt="host",
        kokoro_voice="af_heart",
        vllm_text_model="qwen2.5-7b-instruct",
        vllm_base_url="http://x",
        language="en",
        enabled_genres=["synthwave"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=60,
        talk_max_words=80,
        data_dir=Path("/tmp"),
        news_bit_chance=1.0,
        news_angles=["stock markets react to breakfast"],
    )
    text = talk_mod._build_user_prompt(
        station,
        None,
        None,
        mode="news",
        mode_instruction="funny news",
        spice="be witty",
        mood_label="Late Night",
        mood_genres=["lofi_chill"],
        recent_talk=None,
        news_angle="stock markets react to breakfast",
        max_words=55,
    )
    assert "world-news" in text
    assert "stock markets react to breakfast" in text
    assert "WBOT-101.1" in text


def test_produce_talk_with_mocks(tmp_path, monkeypatch):
    station = _station(tmp_path)

    async def fake_vllm(*a, **k):
        return "Welcome back to Test FM, friends."

    def fake_synth(text, voice, out_path, emotions=None):
        sr = 24000
        sf.write(str(out_path), np.zeros(sr, dtype=np.float32), sr)
        return 1000

    monkeypatch.setattr(talk_mod, "vllm_generate_text", fake_vllm)
    monkeypatch.setattr(talk_mod, "synthesize_orpheus", fake_synth)
    monkeypatch.setattr(talk_mod, "unload_orpheus_model", lambda: None)
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
