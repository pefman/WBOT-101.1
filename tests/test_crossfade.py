"""Talk→song radio-style continuous crossfade."""

from pathlib import Path

import numpy as np
import soundfile as sf

from airadio.audio.process import (
    build_talk_song_continuous,
    extract_wav_from,
    mix_song_under_talk,
    probe_duration_ms,
)


def _tone(path: Path, seconds: float, freq: float, sr: int = 48000, amp: float = 0.3):
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    mono = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    stereo = np.stack([mono, mono], axis=1)
    sf.write(str(path), stereo, sr)


def test_mix_song_under_talk_keeps_talk_length(tmp_path: Path):
    talk = tmp_path / "talk.wav"
    song = tmp_path / "song.wav"
    out = tmp_path / "mixed.wav"
    _tone(talk, 5.0, 220.0)
    _tone(song, 8.0, 440.0, amp=0.5)

    mix_song_under_talk(talk, song, out, overlap_sec=2.0, bed_gain=0.5)
    assert out.is_file()
    talk_ms = probe_duration_ms(talk)
    out_ms = probe_duration_ms(out)
    assert abs(out_ms - talk_ms) < 150


def test_continuous_is_longer_than_talk(tmp_path: Path):
    talk = tmp_path / "talk.wav"
    song = tmp_path / "song.wav"
    out = tmp_path / "air.wav"
    _tone(talk, 6.0, 220.0)
    _tone(song, 10.0, 440.0, amp=0.5)

    talk_ms, total_ms, ov = build_talk_song_continuous(
        talk, song, out, overlap_sec=3.0, bed_gain=0.4, post_ramp_sec=1.5
    )
    assert out.is_file()
    assert abs(talk_ms - 6000) < 150
    # single-timeline: (talk - overlap) + full song ≈ 3 + 10 = 13s
    assert total_ms > talk_ms + 5000
    assert 2.0 < ov <= 3.1
    assert abs(probe_duration_ms(out) - total_ms) < 250


def test_extract_wav_from_shortens(tmp_path: Path):
    song = tmp_path / "song.wav"
    tail = tmp_path / "tail.wav"
    _tone(song, 6.0, 330.0)
    extract_wav_from(song, tail, start_sec=2.0)
    assert tail.is_file()
    ms = probe_duration_ms(tail)
    assert 3500 < ms < 4500
