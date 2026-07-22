from airadio.config import load_genres, load_moods, load_station, resolve_enabled_genres


def test_loads_genre_packs():
    genres = load_genres()
    assert len(genres) == 20
    assert "pop" in genres
    assert genres["pop"].major == "Pop"
    assert "techno_house" in genres
    assert "melodic_progressive_metal" in genres
    assert "classical" in genres
    assert "instrumental" in genres["classical"].lyric_style.lower()
    assert "kick" in genres["techno_house"].style_prompt.lower() or "house" in genres[
        "techno_house"
    ].style_prompt.lower()
    assert "kpop" in genres
    assert "latin" in genres


def test_station_expands_all_genres():
    station, genres = load_station()
    # name: git → repository directory name
    assert station.name == "WBOT-101.1"
    assert station.buffer_min == 2
    assert station.buffer_target == 4
    assert set(station.enabled_genres) == set(genres.keys())
    assert station.data_dir.exists()
    assert 0.0 <= station.news_bit_chance <= 1.0
    assert station.news_angles  # loaded from config/news_angles.yaml
    assert "WBOT-101.1" in station.system_prompt


def test_resolve_subset():
    genres = load_genres()
    ids = resolve_enabled_genres(["pop", "techno_house"], genres)
    assert ids == ["pop", "techno_house"]


def test_load_moods():
    genres = load_genres()
    default_id, moods = load_moods(all_genre_ids=list(genres.keys()))
    assert default_id in moods
    assert "late_night" in moods
    assert "eclectic" in moods
    assert "pop" in moods and "rock" in moods and "hiphop" in moods
    # all mood genre ids must exist
    for m in moods.values():
        for gid in m.genre_ids:
            assert gid in genres
    assert set(moods["eclectic"].genre_ids) == set(genres.keys())
