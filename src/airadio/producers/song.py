from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path

from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.acestep import generate_song
from airadio.clients.ollama import ollama_chat
from airadio.models_types import Genre, Segment, StationConfig

log = logging.getLogger(__name__)


def pick_genre(genres: dict[str, Genre], enabled_ids: list[str]) -> Genre:
    pool = [genres[i] for i in enabled_ids if i in genres]
    if not pool:
        raise ValueError("No enabled genres available")
    return random.choice(pool)


def _parse_json_blob(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


async def _compose_track(
    station: StationConfig, genre: Genre
) -> tuple[str, str, str]:
    instrumental = "instrumental" in genre.lyric_style.lower() or "no vocal" in genre.lyric_style.lower()
    user = f"""Create one original radio track in the genre «{genre.name}».

Genre style guide:
{genre.style_prompt}

Lyric style: {genre.lyric_style}
Target duration: about {station.song_duration_sec} seconds.
Language: {station.language}

Respond with ONLY valid JSON (no markdown) with keys:
- "title": short song title
- "lyrics": full lyrics with [Verse]/[Chorus] tags, or empty string if instrumental
- "style_line": one dense production prompt for a music model (instruments, BPM, mood)

{"This genre is instrumental — set lyrics to empty string." if instrumental else "Include singable lyrics."}
"""
    raw = await ollama_chat(
        station.ollama_base_url,
        station.ollama_model,
        "You write song metadata for a local AI radio station. Output JSON only.",
        user,
        num_gpu=0,
        timeout=180.0,
    )
    data = _parse_json_blob(raw)
    title = str(data.get("title") or f"{genre.name} Untitled").strip()
    lyrics = str(data.get("lyrics") or "").strip()
    style_line = str(data.get("style_line") or genre.style_prompt).strip()
    # Merge genre style for stability
    style = f"{genre.style_prompt.strip()} {style_line}".strip()
    return title, lyrics, style


async def produce_song(
    station: StationConfig,
    genres: dict[str, Genre],
    out_dir: Path,
) -> Segment:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    genre = pick_genre(genres, station.enabled_genres)
    seg_id = uuid.uuid4().hex
    raw_wav = out_dir / f"{seg_id}_raw.wav"
    final_wav = out_dir / f"{seg_id}.wav"

    try:
        title, lyrics, style = await _compose_track(station, genre)
    except Exception as exc:  # noqa: BLE001
        log.exception("Song LLM failed: %s", exc)
        title = f"{genre.name} Transmission"
        lyrics = "" if "instrumental" in genre.lyric_style.lower() else (
            "[Verse]\nNight wire humming low\n"
            "[Chorus]\nKeep the signal on\n"
        )
        style = genre.style_prompt

    duration = station.song_duration_sec or genre.duration_sec
    try:
        await generate_song(
            style,
            lyrics,
            duration,
            raw_wav,
            cmd=station.acestep_cmd,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ACE-Step failed (%s); retry shorter duration", exc)
        shorter = max(45, duration - 20)
        await generate_song(
            style,
            lyrics,
            shorter,
            raw_wav,
            cmd=station.acestep_cmd,
        )

    try:
        loudnorm_ffmpeg(raw_wav, final_wav)
        if not final_wav.is_file():
            raw_wav.replace(final_wav)
    except Exception:  # noqa: BLE001
        if raw_wav.is_file() and not final_wav.is_file():
            raw_wav.replace(final_wav)

    duration_ms = probe_duration_ms(final_wav)
    if raw_wav.is_file() and raw_wav != final_wav:
        try:
            raw_wav.unlink(missing_ok=True)
        except OSError:
            pass

    return Segment(
        id=seg_id,
        kind="song",
        title=title,
        genre_id=genre.id,
        text=lyrics,
        audio_path=final_wav,
        duration_ms=duration_ms,
        created_at=time.time(),
    )
