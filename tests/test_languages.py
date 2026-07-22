from airadio.languages import (
    ace_vocal_language,
    get_language,
    is_known_language,
    language_instruction,
    list_languages,
)


def test_default_english():
    assert get_language(None).id == "en"
    assert get_language("").id == "en"
    assert get_language("SV").id == "sv"


def test_swedish_instruction_is_music_only():
    text = language_instruction("sv")
    assert "Swedish" in text
    assert "lyric" in text.lower() or "Music" in text
    assert "DJ" not in text and "mic" not in text


def test_list_includes_english_and_swedish():
    ids = {x["id"] for x in list_languages()}
    assert "en" in ids
    assert "sv" in ids


def test_ace_vocal_maps():
    assert ace_vocal_language("en") == "en"
    assert ace_vocal_language("sv") == "sv"
    assert is_known_language("sv")
    assert not is_known_language("xx")
