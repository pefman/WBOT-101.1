"""Song library, GC, and prefs persistence."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import soundfile as sf

from airadio.library import SongLibrary, garbage_collect_segments
from airadio.models_types import Segment
from airadio.prefs import load_prefs, merge_prefs, save_prefs


def _wav(path: Path, sec: float = 0.2) -> None:
    sr = 22050
    sf.write(str(path), np.zeros(int(sr * sec), dtype=np.float32), sr)


def _song(tmp: Path, sid: str = "abc", *, genre: str = "rock") -> Segment:
    p = tmp / f"{sid}.wav"
    _wav(p, 0.3)
    return Segment(
        id=sid,
        kind="song",
        title="Track",
        genre_id=genre,
        text="la la",
        audio_path=p,
        duration_ms=300,
        created_at=time.time(),
        artist="Band",
        generation_prompt="# Band — Track",
    )


def test_prefs_roundtrip(tmp_path):
    save_prefs(tmp_path, {"dj_id": "vega", "language": "es"})
    p = load_prefs(tmp_path)
    assert p["dj_id"] == "vega"
    assert p["language"] == "es"
    merge_prefs(tmp_path, enabled_genres=["rock", "jazz"])
    p2 = load_prefs(tmp_path)
    assert p2["dj_id"] == "vega"
    assert p2["enabled_genres"] == ["rock", "jazz"]


def test_library_remember_favorite_and_trim(tmp_path):
    lib = SongLibrary.load(tmp_path, max_songs=3)
    for i in range(5):
        audio = tmp_path / f"s{i}.wav"
        _wav(audio)
        seg = Segment(
            id=f"s{i}",
            kind="song",
            title=f"T{i}",
            genre_id="rock",
            text="",
            audio_path=audio,
            duration_ms=300,
            created_at=time.time() + i,
            artist="Band",
        )
        lib.remember(seg, favorite=(i == 0))
    # max 3: favorite s0 kept + 2 most recent non-fav
    assert len(lib.entries) == 3
    assert "s0" in lib.entries
    assert lib.entries["s0"].favorite is True
    # most recent non-favs survive
    assert "s4" in lib.entries
    lib.set_favorite("s4", True)
    assert lib.entries["s4"].favorite is True


def test_library_pick_reair(tmp_path, monkeypatch):
    lib = SongLibrary.load(tmp_path, max_songs=10)
    for i, g in enumerate(["rock", "jazz", "rock"]):
        audio = tmp_path / f"t{i}.wav"
        _wav(audio)
        seg = Segment(
            id=f"t{i}",
            kind="song",
            title=f"T{i}",
            genre_id=g,
            text="",
            audio_path=audio,
            duration_ms=200,
            created_at=time.time(),
            artist="A",
        )
        lib.remember(seg)
    # Missing genre must not re-air when a filter is active
    audio_x = tmp_path / "tx.wav"
    _wav(audio_x)
    lib.remember(
        Segment(
            id="tx",
            kind="song",
            title="NoGenre",
            genre_id=None,
            text="",
            audio_path=audio_x,
            duration_ms=200,
            created_at=time.time(),
            artist="X",
        )
    )

    # Force pick
    monkeypatch.setattr("airadio.library.random.random", lambda: 0.0)
    monkeypatch.setattr(
        "airadio.library.random.choices",
        lambda pool, weights=None, k=1: [pool[0]],
    )
    seg = lib.pick_reair(enabled_genres=["rock"], chance=1.0)
    assert seg is not None
    assert seg.genre_id == "rock"
    # Chance 0 → never
    assert lib.pick_reair(enabled_genres=["rock"], chance=0.0) is None
    # Only jazz filter never returns rock / untagged
    jazz = lib.pick_reair(enabled_genres=["jazz"], chance=1.0)
    assert jazz is not None
    assert jazz.genre_id == "jazz"
    # Filter with no matching keepers → None (don't leak other genres)
    assert lib.pick_reair(enabled_genres=["melodic_progressive_metal"], chance=1.0) is None


def test_garbage_collect_protects_library(tmp_path):
    segs = tmp_path / "segments"
    segs.mkdir()
    keep = segs / "keep.wav"
    drop = segs / "old.wav"
    _wav(keep)
    _wav(drop)
    # Make drop look old
    old = time.time() - 100 * 3600
    import os

    os.utime(drop, (old, old))
    r = garbage_collect_segments(
        segs,
        protect={keep.resolve()},
        max_age_hours=48,
        max_files=200,
    )
    assert r["deleted"] >= 1
    assert keep.is_file()
    assert not drop.is_file()
