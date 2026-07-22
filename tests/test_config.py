from airadio.config import load_genres, load_moods, load_station, resolve_enabled_genres


def test_loads_genre_packs():
    genres = load_genres()
    # Major packs + melodic progressive metal + freeform Radio meta
    assert len(genres) == 17
    expected = {
        "blues",
        "classical",
        "country",
        "electronic",
        "folk",
        "gospel",
        "hiphop",
        "indie",
        "jazz",
        "latin",
        "melodic_progressive_metal",
        "metal",
        "pop",
        "radio",
        "reggae",
        "rnb",
        "rock",
    }
    assert set(genres.keys()) == expected
    assert genres["pop"].major == "Pop"
    assert genres["electronic"].major == "Electronic"
    assert genres["rock"].name == "Rock"
    assert genres["radio"].name == "Radio"
    assert genres["melodic_progressive_metal"].major == "Metal"
    assert "progressive" in genres["melodic_progressive_metal"].style_prompt.lower()
    assert "melodic progressive metal" in genres["melodic_progressive_metal"].tags.lower()
    assert "[Chorus]" in genres["melodic_progressive_metal"].lyrics_skeleton
    assert "no vocals" in genres["classical"].tags.lower()
    assert genres["pop"].tags.count(",") >= 5


def test_station_defaults_to_radio():
    station, genres = load_station()
    # name: git → repository directory name
    assert station.name == "WBOT-101.1"
    assert station.buffer_min == 2
    assert station.buffer_target == 4
    # Freeform Radio is the default desk (random real pack per song)
    assert station.enabled_genres == ["radio"]
    assert "radio" in genres
    assert station.data_dir.exists()
    assert 0.0 <= station.news_bit_chance <= 1.0
    assert station.news_angles  # loaded from config/news_angles.yaml
    assert "WBOT-101.1" in station.system_prompt


def test_resolve_subset():
    genres = load_genres()
    ids = resolve_enabled_genres(["pop", "electronic"], genres)
    assert ids == ["pop", "electronic"]


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
