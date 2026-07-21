import numpy as np
import soundfile as sf
from pathlib import Path

from airadio.models_types import Genre
from airadio.producers.song import pick_genre


def test_pick_genre_random_from_enabled():
    genres = {
        "a": Genre("a", "A", "s", "l", "d", 100, 75),
        "b": Genre("b", "B", "s", "l", "d", 100, 75),
    }
    seen = {pick_genre(genres, ["a"]).id for _ in range(30)}
    assert seen == {"a"}


def test_mock_acestep_writes_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("ACESTEP_MOCK", "1")
    import asyncio
    from airadio.clients.acestep import generate_song

    out = tmp_path / "song.wav"
    asyncio.run(generate_song("synthwave night", "", 2, out))
    assert out.is_file()
    info = sf.info(str(out))
    assert info.duration >= 1.5
