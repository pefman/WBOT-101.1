from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal


class RadioState(str, Enum):
    STOPPED = "stopped"
    BUFFERING = "buffering"
    PLAYING = "playing"


@dataclass(frozen=True)
class Genre:
    id: str
    name: str
    style_prompt: str
    lyric_style: str
    dj_tone: str
    bpm: int
    duration_sec: int


@dataclass
class StationConfig:
    name: str
    host_name: str
    system_prompt: str
    kokoro_voice: str
    ollama_model: str
    ollama_base_url: str
    language: str
    enabled_genres: list[str]
    buffer_min: int
    buffer_target: int
    song_duration_sec: int
    talk_max_words: int
    data_dir: Path
    acestep_cmd: list[str] | None = None
    config_dir: Path | None = None


@dataclass
class Segment:
    id: str
    kind: Literal["talk", "song"]
    title: str
    genre_id: str | None
    text: str
    audio_path: Path
    duration_ms: int
    created_at: float

    def meta(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "genre_id": self.genre_id,
            "duration_ms": self.duration_ms,
            "text_preview": (self.text[:200] + "…") if len(self.text) > 200 else self.text,
        }
