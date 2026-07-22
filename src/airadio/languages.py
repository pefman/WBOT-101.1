"""Station on-air languages for DJ talk + song lyrics / ACE vocals."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    id: str  # ISO-ish code used in station.language
    label: str
    native: str
    # ACE-Step vocal_language field (best-effort; model may approximate)
    ace_vocal: str
    # Short name used in LLM instructions
    prompt_name: str


# English first = default. Expand as needed.
LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", "English", "en", "English"),
    Language("sv", "Swedish", "Svenska", "sv", "Swedish"),
    Language("no", "Norwegian", "Norsk", "no", "Norwegian"),
    Language("da", "Danish", "Dansk", "da", "Danish"),
    Language("de", "German", "Deutsch", "de", "German"),
    Language("fr", "French", "Français", "fr", "French"),
    Language("es", "Spanish", "Español", "es", "Spanish"),
    Language("it", "Italian", "Italiano", "it", "Italian"),
    Language("pt", "Portuguese", "Português", "pt", "Portuguese"),
    Language("nl", "Dutch", "Nederlands", "nl", "Dutch"),
    Language("fi", "Finnish", "Suomi", "fi", "Finnish"),
    Language("pl", "Polish", "Polski", "pl", "Polish"),
    Language("ja", "Japanese", "日本語", "ja", "Japanese"),
    Language("zh", "Chinese", "中文", "zh", "Chinese"),
    Language("ko", "Korean", "한국어", "ko", "Korean"),
)

_BY_ID = {lang.id: lang for lang in LANGUAGES}
DEFAULT_LANGUAGE = "en"


def is_known_language(code: str) -> bool:
    return (code or "").strip().lower() in _BY_ID


def get_language(code: str | None) -> Language:
    key = (code or DEFAULT_LANGUAGE).strip().lower()
    return _BY_ID.get(key, _BY_ID[DEFAULT_LANGUAGE])


def list_languages() -> list[dict]:
    return [
        {
            "id": lang.id,
            "label": lang.label,
            "native": lang.native,
            "prompt_name": lang.prompt_name,
        }
        for lang in LANGUAGES
    ]


def language_instruction(code: str | None) -> str:
    """Hard rule for music generation (lyrics / vocal language) — not DJ TTS."""
    lang = get_language(code)
    if lang.id == "en":
        return (
            "Music language: English. "
            "Write all song lyrics and sung lines in English."
        )
    return (
        f"Music language: {lang.prompt_name} ({lang.native}). "
        f"ALL song lyrics and sung lines MUST be in {lang.prompt_name} only. "
        f"Section tags like [Verse]/[Chorus] may stay in English. "
        f"Do not write lyrics in English except unavoidable proper nouns."
    )


def ace_vocal_language(code: str | None) -> str:
    return get_language(code).ace_vocal
