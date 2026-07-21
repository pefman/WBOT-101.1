from pathlib import Path

from airadio.config import load_genres, load_station, resolve_enabled_genres


def test_loads_ten_genres():
    genres = load_genres()
    assert len(genres) == 10
    assert "synthwave" in genres
    assert genres["synthwave"].bpm == 100
    assert "neon" in genres["synthwave"].style_prompt.lower() or "synth" in genres[
        "synthwave"
    ].style_prompt.lower()


def test_station_expands_all_genres():
    station, genres = load_station()
    assert station.name == "Midnight Wire"
    assert station.buffer_min == 2
    assert station.buffer_target == 4
    assert set(station.enabled_genres) == set(genres.keys())
    assert station.data_dir.exists()


def test_resolve_subset():
    genres = load_genres()
    ids = resolve_enabled_genres(["synthwave", "lofi_chill"], genres)
    assert ids == ["synthwave", "lofi_chill"]
