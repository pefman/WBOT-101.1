"""Voice catalog and DJ personality → voice mappings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    label: str
    locale: str  # e.g. American English
    gender: str  # female | male
    grade: str = ""
    notes: str = ""
    backend: str = "orpheus"  # Primary TTS engine (Orpheus by default)


# Orpheus voices (8 total, trained for natural radio speech — PRIMARY)
# Recommend for DJ talk segments: natural intonation, emotion support, conversational
ORPHEUS_VOICES: list[VoiceInfo] = [
    VoiceInfo("orpheus_tara", "Tara (Orpheus)", "American English", "female", backend="orpheus", notes="Clear, engaging, conversational"),
    VoiceInfo("orpheus_leo", "Leo (Orpheus)", "American English", "male", backend="orpheus", notes="Deep, authoritative, professional"),
    VoiceInfo("orpheus_dan", "Dan (Orpheus)", "American English", "male", backend="orpheus", notes="Warm, friendly"),
    VoiceInfo("orpheus_jess", "Jess (Orpheus)", "American English", "female", backend="orpheus", notes="Energetic, youthful"),
    VoiceInfo("orpheus_mia", "Mia (Orpheus)", "American English", "female", backend="orpheus", notes="Smooth, professional"),
    VoiceInfo("orpheus_zac", "Zac (Orpheus)", "American English", "male", backend="orpheus", notes="Casual, laid-back"),
    VoiceInfo("orpheus_leah", "Leah (Orpheus)", "American English", "female", backend="orpheus", notes="Softer, intimate"),
    VoiceInfo("orpheus_zoe", "Zoe (Orpheus)", "American English", "female", backend="orpheus", notes="Bright, upbeat"),
]

# Legacy voices for backward compatibility (Kokoro fallback voices)
# Source: https://huggingface.co/hexgrad/Kokoro-82M VOICES.md
VOICES: list[VoiceInfo] = ORPHEUS_VOICES + [
    # American female
    VoiceInfo("af_heart", "Heart", "American English", "female", "A", "Warm default"),
    VoiceInfo("af_alloy", "Alloy", "American English", "female", "C"),
    VoiceInfo("af_aoede", "Aoede", "American English", "female", "C+"),
    VoiceInfo("af_bella", "Bella", "American English", "female", "A-", "High quality"),
    VoiceInfo("af_jessica", "Jessica", "American English", "female", "D"),
    VoiceInfo("af_kore", "Kore", "American English", "female"),
    VoiceInfo("af_nicole", "Nicole", "American English", "female"),
    VoiceInfo("af_nova", "Nova", "American English", "female"),
    VoiceInfo("af_river", "River", "American English", "female"),
    VoiceInfo("af_sarah", "Sarah", "American English", "female"),
    VoiceInfo("af_sky", "Sky", "American English", "female"),
    # American male
    VoiceInfo("am_adam", "Adam", "American English", "male"),
    VoiceInfo("am_echo", "Echo", "American English", "male"),
    VoiceInfo("am_eric", "Eric", "American English", "male"),
    VoiceInfo("am_fenrir", "Fenrir", "American English", "male"),
    VoiceInfo("am_liam", "Liam", "American English", "male"),
    VoiceInfo("am_michael", "Michael", "American English", "male"),
    VoiceInfo("am_onyx", "Onyx", "American English", "male"),
    VoiceInfo("am_puck", "Puck", "American English", "male"),
    VoiceInfo("am_santa", "Santa", "American English", "male"),
    # British female
    VoiceInfo("bf_alice", "Alice", "British English", "female"),
    VoiceInfo("bf_emma", "Emma", "British English", "female"),
    VoiceInfo("bf_isabella", "Isabella", "British English", "female"),
    VoiceInfo("bf_lily", "Lily", "British English", "female"),
    # British male
    VoiceInfo("bm_daniel", "Daniel", "British English", "male"),
    VoiceInfo("bm_fable", "Fable", "British English", "male"),
    VoiceInfo("bm_george", "George", "British English", "male"),
    VoiceInfo("bm_lewis", "Lewis", "British English", "male"),
]

VOICE_BY_ID = {v.id: v for v in VOICES}

# Mapping: DJ personality → Orpheus voice recommendation
# Used to pick the best voice for a DJ personality
DJ_VOICE_MAP = {
    # Female-leaning hosts
    "rex": "orpheus_leo",  # Can be overridden per DJ in config
    "default_female": "orpheus_tara",
    "default_male": "orpheus_leo",
}


def orpheus_voice_id(voice_name: str) -> str:
    """Convert Orpheus voice name (e.g., 'leo') to voice_id (e.g., 'orpheus_leo')."""
    if not voice_name:
        return "orpheus_leo"
    if voice_name.startswith("orpheus_"):
        return voice_name
    return f"orpheus_{voice_name.lower()}"


def get_dj_voice(dj_id: str, gender: str = "male") -> str:
    """
    Get recommended Orpheus voice for a DJ based on personality.

    Args:
        dj_id: DJ identifier from config/djs.yaml
        gender: Hint from DJ profile ("male" or "female")

    Returns:
        Orpheus voice name (without "orpheus_" prefix): tara, leo, etc.
    """
    if dj_id in DJ_VOICE_MAP:
        voice_full = DJ_VOICE_MAP[dj_id]
        return voice_full.replace("orpheus_", "")

    # Default by gender
    default_key = "default_female" if gender.lower() == "female" else "default_male"
    voice_full = DJ_VOICE_MAP.get(default_key, "orpheus_leo")
    return voice_full.replace("orpheus_", "")


def list_voices() -> list[dict]:
    """List all available voices (Orpheus primary + Kokoro legacy fallback)."""
    return [
        {
            "id": v.id,
            "label": v.label,
            "locale": v.locale,
            "gender": v.gender,
            "grade": v.grade,
            "notes": v.notes,
            "backend": v.backend,
        }
        for v in VOICES
    ]


def is_known_voice(voice_id: str) -> bool:
    \"\"\"Check if voice_id is known (Orpheus primary or Kokoro legacy).\"\"\"
    return voice_id in VOICE_BY_ID
