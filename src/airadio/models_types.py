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
    major: str = ""


@dataclass(frozen=True)
class Mood:
    id: str
    label: str
    blurb: str
    genre_ids: tuple[str, ...]


@dataclass(frozen=True)
class DJ:
    id: str
    name: str
    blurb: str
    voice: str
    personality: str


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
    song_duration_sec: int  # ACE target length; ~150–180s for full pop form
    talk_max_words: int
    data_dir: Path
    ollama_auto_pull: bool = True
    acestep_cmd: list[str] | None = None
    config_dir: Path | None = None
    # 0.0–1.0 probability a talk break includes a funny world-news bit
    news_bit_chance: float = 0.4
    news_angles: list[str] | None = None
    # Raw template with {host_name} {station_name} {personality}
    system_prompt_template: str = ""
    default_dj: str = "rex"
    # Talk→song: start next track under the last N seconds of talk (0 = off)
    crossfade_sec: float = 3.0
    # Relative gain of the song bed under talk (0–1); ramps to 1 after voice
    crossfade_bed_gain: float = 0.42


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
    artist: str | None = None  # band / artist name for songs
    # Talk provenance — used to drop stale segments after DJ/voice switch
    host_name: str | None = None
    voice_id: str | None = None
    generation_id: int | None = None
    # Full prompt package used to generate this song (style + lyrics + meta)
    generation_prompt: str | None = None
    cover_path: Path | None = None  # procedural album art PNG

    def meta(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "artist": self.artist,
            "genre_id": self.genre_id,
            "duration_ms": self.duration_ms,
            "text_preview": (self.text[:200] + "…") if len(self.text) > 200 else self.text,
            "host_name": self.host_name,
            "voice_id": self.voice_id,
            "generation_prompt": self.generation_prompt,
            "cover_url": f"/api/covers/{self.id}.png" if self.cover_path else None,
        }
