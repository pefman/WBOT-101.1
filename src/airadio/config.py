from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from airadio.models_types import Genre, StationConfig

# Repo root: .../WBOT-101.1 (parent of src/)
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    return _repo_root() / "config"


def default_station_path() -> Path:
    return default_config_dir() / "station.yaml"


def default_genres_dir() -> Path:
    return default_config_dir() / "genres"


def load_genres(genres_dir: Path | None = None) -> dict[str, Genre]:
    genres_dir = genres_dir or default_genres_dir()
    if not genres_dir.is_dir():
        raise FileNotFoundError(f"Genres directory not found: {genres_dir}")

    genres: dict[str, Genre] = {}
    for path in sorted(genres_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        gid = str(raw.get("id") or path.stem)
        genres[gid] = Genre(
            id=gid,
            name=str(raw.get("name") or gid),
            style_prompt=str(raw.get("style_prompt") or "").strip(),
            lyric_style=str(raw.get("lyric_style") or "").strip(),
            dj_tone=str(raw.get("dj_tone") or "").strip(),
            bpm=int(raw.get("bpm") or 100),
            duration_sec=int(raw.get("duration_sec") or 75),
        )
    if not genres:
        raise ValueError(f"No genre YAML files in {genres_dir}")
    return genres


def resolve_enabled_genres(
    enabled: list[str], all_genres: dict[str, Genre]
) -> list[str]:
    if not enabled or enabled == ["all"] or (len(enabled) == 1 and enabled[0] == "all"):
        return list(all_genres.keys())
    unknown = [g for g in enabled if g not in all_genres]
    if unknown:
        raise ValueError(f"Unknown genre ids in station config: {unknown}")
    return list(enabled)


def load_station(
    path: Path | None = None,
    genres_dir: Path | None = None,
) -> tuple[StationConfig, dict[str, Genre]]:
    path = path or default_station_path()
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    genres = load_genres(genres_dir)
    enabled_raw = raw.get("enabled_genres") or ["all"]
    if isinstance(enabled_raw, str):
        enabled_raw = [enabled_raw]
    enabled = resolve_enabled_genres(list(enabled_raw), genres)

    data_dir = Path(raw.get("data_dir") or (_repo_root() / "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = (_repo_root() / data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "segments").mkdir(parents=True, exist_ok=True)
    (data_dir / "hls").mkdir(parents=True, exist_ok=True)

    acestep_cmd = raw.get("acestep_cmd")
    if acestep_cmd is not None and not isinstance(acestep_cmd, list):
        raise TypeError("acestep_cmd must be a list of strings or null")

    station = StationConfig(
        name=str(raw.get("name") or "AI Radio"),
        host_name=str(raw.get("host_name") or "Host"),
        system_prompt=str(raw.get("system_prompt") or "").strip(),
        kokoro_voice=str(raw.get("kokoro_voice") or "af_heart"),
        ollama_model=str(raw.get("ollama_model") or "qwen2.5:7b"),
        ollama_base_url=str(raw.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip(
            "/"
        ),
        language=str(raw.get("language") or "en"),
        enabled_genres=enabled,
        buffer_min=int(raw.get("buffer_min") or 2),
        buffer_target=int(raw.get("buffer_target") or 4),
        song_duration_sec=int(raw.get("song_duration_sec") or 75),
        talk_max_words=int(raw.get("talk_max_words") or 100),
        data_dir=data_dir,
        acestep_cmd=list(acestep_cmd) if acestep_cmd else None,
        config_dir=path.parent,
    )
    return station, genres
