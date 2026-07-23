from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

from airadio.models_types import DJ, Genre, Mood, StationConfig

# Repo root: .../WBOT-101.1 (parent of src/)
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def git_repo_name(root: Path | None = None) -> str:
    """Station call letters = git repository directory name (e.g. WBOT-101.1)."""
    root = root or _repo_root()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).name
    except Exception:  # noqa: BLE001
        pass
    return root.name


def default_config_dir() -> Path:
    return _repo_root() / "config"


def default_station_path() -> Path:
    return default_config_dir() / "station.yaml"


def default_genres_dir() -> Path:
    return default_config_dir() / "genres"


def default_news_angles_path() -> Path:
    return default_config_dir() / "news_angles.yaml"


def load_news_angles(path: Path | None = None) -> list[str]:
    path = path or default_news_angles_path()
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    angles = raw.get("angles") or []
    return [str(a).strip() for a in angles if str(a).strip()]


def default_moods_path() -> Path:
    return default_config_dir() / "moods.yaml"


def default_djs_path() -> Path:
    return default_config_dir() / "djs.yaml"


def load_djs(path: Path | None = None) -> tuple[str, dict[str, DJ]]:
    """Return (default_dj_id, djs_by_id)."""
    path = path or default_djs_path()
    if not path.is_file():
        dj = DJ(
            id="rex",
            name="Rex",
            blurb="Default host",
            voice="bm_george",
            personality="Smooth classic FM host.",
        )
        return "rex", {"rex": dj}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_id = str(raw.get("default") or "rex")
    djs: dict[str, DJ] = {}
    for did, body in (raw.get("djs") or {}).items():
        body = body or {}
        samples_raw = body.get("voice_samples") or []
        samples: list[str] = []
        if isinstance(samples_raw, list):
            samples = [str(s).strip() for s in samples_raw if str(s).strip()]
        djs[str(did)] = DJ(
            id=str(did),
            name=str(body.get("name") or did).strip(),
            blurb=str(body.get("blurb") or "").strip(),
            voice=str(body.get("voice") or "bm_george").strip(),
            personality=str(body.get("personality") or "").strip(),
            voice_samples=tuple(samples),
        )
    if not djs:
        djs["rex"] = DJ(
            id="rex",
            name="Rex",
            blurb="Default host",
            voice="bm_george",
            personality="Smooth classic FM host.",
        )
        default_id = "rex"
    if default_id not in djs:
        default_id = next(iter(djs))
    return default_id, djs


def build_system_prompt(
    template: str,
    *,
    station_name: str,
    host_name: str,
    personality: str,
) -> str:
    text = (template or "").strip()
    text = text.replace("{station_name}", station_name)
    text = text.replace("{host_name}", host_name)
    text = text.replace("{personality}", personality.strip() or "Warm radio host.")
    return text


def load_moods(
    path: Path | None = None,
    *,
    all_genre_ids: list[str] | None = None,
) -> tuple[str, dict[str, Mood]]:
    """Return (default_mood_id, moods_by_id)."""
    path = path or default_moods_path()
    if not path.is_file():
        # Fallback: single eclectic mood
        ids = tuple(all_genre_ids or [])
        mood = Mood(id="eclectic", label="Eclectic", blurb="All genres", genre_ids=ids)
        return "eclectic", {"eclectic": mood}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_id = str(raw.get("default") or "eclectic")
    moods: dict[str, Mood] = {}
    for mid, body in (raw.get("moods") or {}).items():
        body = body or {}
        gids = body.get("genres") or ["all"]
        if gids == ["all"] or gids == "all":
            resolved = tuple(all_genre_ids or [])
        else:
            resolved = tuple(str(g) for g in gids)
        moods[str(mid)] = Mood(
            id=str(mid),
            label=str(body.get("label") or mid),
            blurb=str(body.get("blurb") or ""),
            genre_ids=resolved,
        )
    if not moods:
        ids = tuple(all_genre_ids or [])
        moods["eclectic"] = Mood(
            id="eclectic", label="Eclectic", blurb="All genres", genre_ids=ids
        )
        default_id = "eclectic"
    if default_id not in moods:
        default_id = next(iter(moods))
    return default_id, moods


def load_genres(genres_dir: Path | None = None) -> dict[str, Genre]:
    genres_dir = genres_dir or default_genres_dir()
    if not genres_dir.is_dir():
        raise FileNotFoundError(f"Genres directory not found: {genres_dir}")

    genres: dict[str, Genre] = {}
    for path in sorted(genres_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        gid = str(raw.get("id") or path.stem)
        style = str(raw.get("style_prompt") or "").strip()
        tags = str(raw.get("tags") or "").strip()
        # Prefer explicit ACE tags; fall back to style_prompt for older packs
        if not tags and style:
            tags = style.replace("\n", " ").strip()
        if not style and tags:
            style = tags
        genres[gid] = Genre(
            id=gid,
            name=str(raw.get("name") or gid),
            style_prompt=style,
            lyric_style=str(raw.get("lyric_style") or "").strip(),
            dj_tone=str(raw.get("dj_tone") or "").strip(),
            bpm=int(raw.get("bpm") or 100),
            duration_sec=int(raw.get("duration_sec") or 75),
            major=str(raw.get("major") or "").strip(),
            tags=tags,
            lyrics_skeleton=str(raw.get("lyrics_skeleton") or "").strip(),
        )
    if not genres:
        raise ValueError(f"No genre YAML files in {genres_dir}")
    return genres


def resolve_enabled_genres(
    enabled: list[str], all_genres: dict[str, Genre]
) -> list[str]:
    # "all" / empty → default freeform Radio pack (random real genre per song)
    if not enabled or enabled == ["all"] or (len(enabled) == 1 and enabled[0] == "all"):
        if "radio" in all_genres:
            return ["radio"]
        return [g for g in all_genres if g != "radio"] or list(all_genres.keys())
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

    # name: "git" / "auto" / empty → repository directory name
    name_raw = raw.get("name")
    if name_raw is None or str(name_raw).strip().lower() in ("", "git", "auto", "repo"):
        station_name = git_repo_name()
    else:
        station_name = str(name_raw).strip()

    system_template = str(raw.get("system_prompt") or "").strip()
    # host_name / voice may be overridden by default DJ in main via apply_dj
    host_name = str(raw.get("host_name") or "Host")
    system_prompt = build_system_prompt(
        system_template,
        station_name=station_name,
        host_name=host_name,
        personality="",
    )

    news_chance = float(raw.get("news_bit_chance") if raw.get("news_bit_chance") is not None else 0.4)
    news_chance = max(0.0, min(1.0, news_chance))

    news_path = path.parent / "news_angles.yaml"
    news_angles = load_news_angles(news_path if news_path.is_file() else None)

    station = StationConfig(
        name=station_name,
        host_name=host_name,
        system_prompt=system_prompt,
        primary_voice=str(raw.get("primary_voice") or "orpheus_leo"),
        vllm_text_model=str(raw.get("vllm_text_model") or "qwen2.5-7b-instruct"),
        vllm_base_url=str(raw.get("vllm_base_url") or "http://127.0.0.1:8000").rstrip(
            "/"
        ),
        language=str(raw.get("language") or "en"),
        enabled_genres=enabled,
        buffer_min=int(raw.get("buffer_min") or 2),
        buffer_target=int(raw.get("buffer_target") or 4),
        song_duration_sec=int(raw.get("song_duration_sec") or 165),
        talk_max_words=int(raw.get("talk_max_words") or 100),
        data_dir=data_dir,
        config_dir=path.parent,
        news_bit_chance=news_chance,
        news_angles=news_angles or None,
        system_prompt_template=system_template,
        default_dj=str(raw.get("default_dj") or "rex"),
        crossfade_sec=float(
            raw.get("crossfade_sec") if raw.get("crossfade_sec") is not None else 3.0
        ),
        crossfade_bed_gain=float(
            raw.get("crossfade_bed_gain")
            if raw.get("crossfade_bed_gain") is not None
            else 0.42
        ),
        outro_crossfade_sec=float(
            raw.get("outro_crossfade_sec")
            if raw.get("outro_crossfade_sec") is not None
            else 6.0
        ),
        outro_bed_gain=float(
            raw.get("outro_bed_gain")
            if raw.get("outro_bed_gain") is not None
            else 0.32
        ),
        library_max_songs=int(raw.get("library_max_songs") or 40),
        reair_chance=float(
            raw.get("reair_chance") if raw.get("reair_chance") is not None else 0.28
        ),
        segment_max_age_hours=float(
            raw.get("segment_max_age_hours")
            if raw.get("segment_max_age_hours") is not None
            else 48.0
        ),
        segment_max_files=int(raw.get("segment_max_files") or 200),
        cover_backend=str(raw.get("cover_backend") or "sd_turbo").strip().lower(),
        cover_sd_steps=int(raw.get("cover_sd_steps") or 2),
        cover_auto_download=bool(
            raw.get("cover_auto_download")
            if raw.get("cover_auto_download") is not None
            else True
        ),
    )
    return station, genres
