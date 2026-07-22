from airadio.producers import talk as talk_mod
from airadio.models_types import StationConfig, Segment
from pathlib import Path


def _station(**kw):
    base = dict(
        name="WBOT-101.1",
        host_name="Aria",
        system_prompt="host",
        kokoro_voice="af_heart",
        ollama_model="auto",
        ollama_base_url="http://127.0.0.1:11434",
        language="en",
        enabled_genres=["synthwave"],
        buffer_min=2,
        buffer_target=4,
        song_duration_sec=60,
        talk_max_words=80,
        data_dir=Path("/tmp"),
        news_bit_chance=0.0,
        news_angles=["diplomats and snacks"],
    )
    base.update(kw)
    return StationConfig(**base)


def test_pick_mode_returns_known():
    mode, instr = talk_mod._pick_mode()
    assert mode
    assert instr


def test_banned_detects_canned():
    assert talk_mod._looks_banned(
        "You're listening to WBOT. Stay tuned — more music is on the way."
    )
    assert not talk_mod._looks_banned(
        "Aria here under the streetlight glow, letting that last chorus hang a little longer."
    )


def test_prompt_includes_anti_repeat():
    st = _station()
    text = talk_mod._build_user_prompt(
        st,
        None,
        None,
        mode="bridge",
        mode_instruction="bridge songs",
        spice="be specific",
        mood_label="Late Night",
        mood_genres=["lofi_chill"],
        recent_talk=["Hello night people, keep the kettle warm."],
        news_angle=None,
    )
    assert "Do NOT reuse" in text
    assert "Hello night people" in text
    assert "more music is on the way" in text  # ban list mentioned
