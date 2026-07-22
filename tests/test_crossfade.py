"""Talk↔song radio-style continuous crossfades."""

from pathlib import Path

import numpy as np
import soundfile as sf

from airadio.audio.process import (
    build_skip_crossfade,
    build_song_talk_continuous,
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


def test_song_talk_outro_continuous(tmp_path: Path):
    """DJ opens ~6s before song end; total longer than either alone."""
    song = tmp_path / "song.wav"
    talk = tmp_path / "talk.wav"
    out = tmp_path / "outro.wav"
    _tone(song, 12.0, 440.0, amp=0.5)
    _tone(talk, 5.0, 220.0)

    song_clear_ms, total_ms, ov = build_song_talk_continuous(
        song, talk, out, overlap_sec=6.0, bed_gain=0.3, duck_sec=0.8
    )
    assert out.is_file()
    # Clear song until DJ: ~12 - 6 = 6s
    assert 5500 < song_clear_ms < 6500
    assert 5.0 < ov <= 6.1
    # total ≈ clear + full talk = 6 + 5 = 11s (overlap shared)
    assert total_ms > song_clear_ms + 3500
    assert abs(probe_duration_ms(out) - total_ms) < 300
    # Should be shorter than dry concat (12+5=17) because of overlap
    assert total_ms < 14_000


def test_skip_crossfade_into_talk(tmp_path: Path):
    """Skip mid-song: short bed of current under full next talk."""
    song = tmp_path / "song.wav"
    talk = tmp_path / "talk.wav"
    out = tmp_path / "skip.wav"
    _tone(song, 10.0, 440.0, amp=0.5)
    _tone(talk, 4.0, 220.0)

    # Pretend we're ~3s into the song — short snappy bed
    tail_ms, total_ms, tail = build_skip_crossfade(
        song,
        talk,
        out,
        from_start_sec=3.0,
        tail_sec=1.4,
        bed_gain=0.28,
    )
    assert out.is_file()
    assert 0.5 < tail <= 2.3
    assert tail_ms > 400
    # Package is essentially the talk length (bed is shorter)
    assert 3500 < total_ms < 5500
