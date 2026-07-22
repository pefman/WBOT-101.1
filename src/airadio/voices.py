"""Kokoro-82M voice catalog for the radio host picker."""

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


# Primary English hosts (best supported for this station's English DJ)
# Source: https://huggingface.co/hexgrad/Kokoro-82M VOICES.md
VOICES: list[VoiceInfo] = [
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


def list_voices() -> list[dict]:
    return [
        {
            "id": v.id,
            "label": v.label,
            "locale": v.locale,
            "gender": v.gender,
            "grade": v.grade,
            "notes": v.notes,
        }
        for v in VOICES
    ]


def is_known_voice(voice_id: str) -> bool:
    return voice_id in VOICE_BY_ID
