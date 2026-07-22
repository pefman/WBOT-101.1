from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from airadio.art.cover import generate_cover
from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.acestep import generate_song
from airadio.clients.ollama import ollama_chat, unload_model
from airadio.languages import ace_vocal_language, get_language, language_instruction
from airadio.models_types import Genre, Segment, StationConfig

log = logging.getLogger(__name__)

StageCb = Callable[[str, str], None]

# Prompt knobs
_IDENTITY_TEMP = 0.95
_IDENTITY_MAX_TOKENS = 280
_LYRICS_TEMP = 0.8
_LYRICS_MAX_TOKENS = 1600


# Meta pack: when enabled (esp. alone), each song draws from real genre packs
RADIO_GENRE_ID = "radio"


def pick_genre(genres: dict[str, Genre], enabled_ids: list[str]) -> Genre:
    """Pick a concrete genre for this song.

    The special ``radio`` id is never returned to ACE — it means “shuffle”:
    - only ``radio`` enabled → random among all non-radio packs
    - ``radio`` + others → random among the other enabled packs
    - no ``radio`` → random among the concrete enabled packs
    """
    enabled = [i for i in enabled_ids if i in genres]
    concrete_ids = [i for i in enabled if i != RADIO_GENRE_ID]
    if RADIO_GENRE_ID in enabled and not concrete_ids:
        concrete_ids = [i for i in genres if i != RADIO_GENRE_ID]
    if not concrete_ids:
        concrete_ids = [i for i in genres if i != RADIO_GENRE_ID]
    pool = [genres[i] for i in concrete_ids if i in genres]
    if not pool:
        raise ValueError("No enabled genres available")
    return random.choice(pool)


def _parse_json_blob(text: str) -> dict:
    """Parse model JSON; allow raw newlines inside strings (strict=False)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    text = text.replace("\x00", "")

    def _load(blob: str) -> dict:
        return json.loads(blob, strict=False)

    try:
        return _load(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            blob = text[start : end + 1]
            try:
                return _load(blob)
            except json.JSONDecodeError:
                fixed = re.sub(
                    r"(?<=: )\"([^\"]*)\"",
                    lambda m: '"'
                    + m.group(1).replace("\n", "\\n").replace("\r", "\\r")
                    + '"',
                    blob,
                    flags=re.DOTALL,
                )
                try:
                    return _load(fixed)
                except json.JSONDecodeError:
                    return _load(blob.replace("\n", "\\n").replace("\r", "\\r"))
        raise


async def _chat_json(
    station: StationConfig,
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
    timeout: float = 180.0,
) -> dict:
    raw = await ollama_chat(
        station.ollama_base_url,
        station.ollama_model,
        system,
        user,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    try:
        return _parse_json_blob(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("  [song] JSON parse failed (%s); repair retry…", exc)
        repair = await ollama_chat(
            station.ollama_base_url,
            station.ollama_model,
            "You fix broken JSON. Output ONLY valid JSON. No markdown.",
            "Repair this into valid JSON only:\n\n" + raw[:4000],
            timeout=90.0,
            temperature=0.2,
            max_tokens=max_tokens,
        )
        return _parse_json_blob(repair)


def _is_instrumental(genre: Genre) -> bool:
    blob = f"{genre.lyric_style} {genre.tags} {genre.style_prompt}".lower()
    return (
        "no vocal" in blob
        or "no vocals" in blob
        or "instrumental" in genre.lyric_style.lower()
    )


def _ace_tags(genre: Genre, *, extras: str = "") -> str:
    """Build a clean ACE-Step caption: short comma-separated keywords."""
    base = (genre.tags or genre.style_prompt or genre.name).strip()
    # Collapse whitespace / newlines from YAML folded scalars
    base = re.sub(r"\s+", " ", base).strip().rstrip(",")
    parts = [p.strip() for p in base.split(",") if p.strip()]
    if extras:
        for p in extras.split(","):
            t = p.strip()
            if t and t.lower() not in {x.lower() for x in parts}:
                parts.append(t)
    # ACE caption: ~5–12 keywords (guide max 12)
    parts = parts[:12]
    return ", ".join(parts)


def _lyrics_skeleton(genre: Genre, *, instrumental: bool) -> str:
    skel = (genre.lyrics_skeleton or "").strip()
    if skel:
        return skel
    if instrumental:
        return (
            "[Intro]\n[Main Theme]\n[Development]\n[Climax]\n[Resolution]\n[Outro]"
        )
    return (
        "[Intro]\n[Verse]\n[line]\n[line]\n[Chorus]\n[hook]\n"
        "[Verse]\n[Chorus]\n[Bridge]\n[Chorus]\n[Outro]"
    )


def _format_recent_songs(recent: list[tuple[str, str]] | None) -> str:
    if not recent:
        return ""
    lines = ["Do NOT reuse or lightly rephrase these recent acts/titles:"]
    for i, (a, t) in enumerate(recent[-20:], 1):
        lines.append(f"  {i}. {a} — {t}")
    return "\n".join(lines)


async def _compose_identity(
    station: StationConfig,
    genre: Genre,
    *,
    instrumental: bool,
    recent_songs: list[tuple[str, str]] | None,
) -> tuple[str, str, str]:
    """Return (artist, title, optional tag extras — 0–4 keywords)."""
    lang = get_language(station.language)
    recent_block = _format_recent_songs(recent_songs)
    base_tags = _ace_tags(genre)

    system = (
        "You are a sharp music A&R inventing original playlist-ready acts. "
        "Output ONLY valid JSON (no markdown). "
        "Adult/edgy names are fine when they fit the genre. "
        "Never use meta labels: AI, Generated, Test, Transmission, Untitled, Radio Station."
    )

    artist_rule = (
        "composer / project name (instrumental act)"
        if instrumental
        else "fictional band or solo artist name that fits this genre"
    )

    user = f"""Invent a playlist-ready artist and song title.

Genre: {genre.name}
Style tags (for context only — do not rewrite as a paragraph): {base_tags}
{language_instruction(station.language)}

JSON only:
- "artist": {artist_rule} (2–4 words)
- "title": short vivid title ({lang.prompt_name} when natural)
- "tags_extra": at most 3 extra keywords, or ""

No meta names (AI, Generated, Test, Untitled). Avoid Sky/Drive/Soul/Vibes clichés.
{recent_block}
"""
    data = await _chat_json(
        station,
        system,
        user,
        temperature=_IDENTITY_TEMP,
        max_tokens=_IDENTITY_MAX_TOKENS,
    )
    artist = str(
        data.get("artist") or data.get("band") or data.get("artist_name") or ""
    ).strip().strip('"').strip("'")
    title = str(
        data.get("title") or data.get("song") or data.get("name") or ""
    ).strip().strip('"').strip("'")
    if not artist:
        raise RuntimeError(f"Song LLM omitted artist: {data!r}"[:300])
    if not title:
        raise RuntimeError(f"Song LLM omitted title: {data!r}"[:300])
    extras = str(data.get("tags_extra") or data.get("style_line") or "").strip()
    # If model returned a long prose style_line, drop it — ACE wants tags only
    if extras and (len(extras) > 120 or "\n" in extras or extras.count(" ") > 12):
        extras = ""
    return artist, title, extras


async def _compose_lyrics(
    station: StationConfig,
    genre: Genre,
    *,
    artist: str,
    title: str,
    skeleton: str,
) -> str:
    """Fill ACE-style [Section] skeleton with short rhythmic lines."""
    lang = get_language(station.language)
    system = (
        'Write song lyrics. Output ONLY JSON: {"lyrics":"..."}. '
        f"Sung lines in {lang.prompt_name}. "
        "Short rhythmic lines (about 6–10 syllables). No long sentences."
    )
    user = f"""Write lyrics for:
Artist: {artist}
Title: {title}
Genre: {genre.name}
{language_instruction(station.language)}

Use exactly these section headers, in order. Under each [Verse]/[Chorus]/[Bridge]/
[Pre-Chorus] put 2–4 short sung lines. Other sections may be empty headers only.

{skeleton}

Rules:
- Keep [Section] tags as written
- Sticky chorus hook, repeated
- No stage directions, no markdown, no placeholders like "line" or "hook"
- JSON only
"""
    data = await _chat_json(
        station,
        system,
        user,
        temperature=_LYRICS_TEMP,
        max_tokens=_LYRICS_MAX_TOKENS,
        timeout=240.0,
    )
    lyrics = str(data.get("lyrics") or "").strip()
    if not lyrics or len(lyrics) < 20:
        raise RuntimeError(f"Song LLM returned empty/short lyrics: {data!r}"[:300])
    return lyrics


async def _compose_track(
    station: StationConfig,
    genre: Genre,
    *,
    recent_songs: list[tuple[str, str]] | None = None,
    on_stage: StageCb | None = None,
) -> tuple[str, str, str, str]:
    """Return (artist, title, lyrics, ace_tags_caption)."""
    instrumental = _is_instrumental(genre)
    duration = int(station.song_duration_sec or genre.duration_sec or 165)
    skeleton = _lyrics_skeleton(genre, instrumental=instrumental)

    def _stage(stage: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(stage, detail)
            except Exception:  # noqa: BLE001
                pass

    log.info(
        "  [song] 1a/4 LLM identity (genre=%s, ACE tags)…",
        genre.id,
    )
    _stage("song_identity", f"Inventing act · {genre.name}…")
    artist, title, extras = await _compose_identity(
        station,
        genre,
        instrumental=instrumental,
        recent_songs=recent_songs,
    )
    log.info("  [song]     → %s — %s", artist, title)

    if instrumental:
        # ACE instrumental: section tags only (or empty) — no long prose lyrics
        lyrics = skeleton if skeleton else ""
        # Keep only section headers for structure cues
        lyrics = "\n".join(
            ln for ln in lyrics.splitlines() if ln.strip().startswith("[")
        )
        log.info("  [song] 1b/4 instrumental — section tags only")
    else:
        log.info("  [song] 1b/4 LLM lyrics (short lines + skeleton)…")
        _stage("song_lyrics", f"Writing lyrics · {artist} — {title}…")
        lyrics = await _compose_lyrics(
            station,
            genre,
            artist=artist,
            title=title,
            skeleton=skeleton,
        )

    # ACE caption: clean tags only (not a paragraph) — never include duration
    style = _ace_tags(genre, extras=extras)
    log.info("  [song] ACE tags: %s", style[:160] + ("…" if len(style) > 160 else ""))
    return artist, title, lyrics, style


async def produce_song(
    station: StationConfig,
    genres: dict[str, Genre],
    out_dir: Path,
    *,
    recent_songs: list[tuple[str, str]] | None = None,
    on_stage: StageCb | None = None,
) -> Segment:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    genre = pick_genre(genres, station.enabled_genres)
    seg_id = uuid.uuid4().hex
    raw_wav = out_dir / f"{seg_id}_raw.wav"
    final_wav = out_dir / f"{seg_id}.wav"

    def _stage(stage: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(stage, detail)
            except Exception:  # noqa: BLE001
                pass

    # Length is an ACE engine param only — never put seconds in tags/lyrics/prompts
    duration = int(station.song_duration_sec or genre.duration_sec or 165)
    artist, title, lyrics, style = await _compose_track(
        station, genre, recent_songs=recent_songs, on_stage=on_stage
    )

    # Free VRAM for ACE; reloads automatically on next talk
    log.info("  [song] unloading LLM so ACE can use the GPU…")
    _stage("song_unload_llm", "Freeing GPU for music model…")
    await unload_model(station.ollama_base_url, station.ollama_model)

    vlang = ace_vocal_language(station.language)
    log.info(
        "  [song] 2/4 ACE-Step generating music (genre=%s, lang=%s)…",
        genre.id,
        vlang,
    )
    _stage(
        "song_music",
        f"Composing «{title}» ({genre.name}) — this can take a while…",
    )
    try:
        await generate_song(
            style,
            lyrics,
            duration,
            raw_wav,
            vocal_language=vlang,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "  [song] ACE-Step failed (%s); retry shorter take",
            exc,
        )
        _stage("song_music", f"Retry shorter take · {title}…")
        shorter = max(45, duration - 20)
        await generate_song(
            style,
            lyrics,
            shorter,
            raw_wav,
            vocal_language=vlang,
        )

    log.info("  [song] 3/4 Loudnorm / finalize WAV…")
    _stage("song_finalize", "Normalizing track…")
    try:
        loudnorm_ffmpeg(raw_wav, final_wav, sample_rate=48000, trim_silence=True)
        if not final_wav.is_file():
            raw_wav.replace(final_wav)
    except Exception:  # noqa: BLE001
        if raw_wav.is_file() and not final_wav.is_file():
            raw_wav.replace(final_wav)

    duration_ms = probe_duration_ms(final_wav)
    if duration_ms < 10_000:
        log.warning(
            "  [song] Short audio: got %.1fs (%s — %s)",
            duration_ms / 1000.0,
            artist,
            title,
        )
    else:
        log.info(
            "  [song] 4/4 Done: %s — %s [%s]",
            artist,
            title,
            genre.name,
        )
    if raw_wav.is_file() and raw_wav != final_wav:
        try:
            raw_wav.unlink(missing_ok=True)
        except OSError:
            pass

    # Stored for the UI "copy" button — same two clean inputs ACE gets (no guide prose)
    gen_prompt = (
        f"{style.strip()}\n\n"
        f"{(lyrics or '').strip()}\n"
    )

    cover_path: Path | None = None
    cover_file = out_dir / f"{seg_id}_cover.png"
    try:
        backend = str(getattr(station, "cover_backend", "sd_turbo") or "sd_turbo")
        steps = int(getattr(station, "cover_sd_steps", 2) or 2)
        log.info("  [song] cover art (%s)…", backend)
        generate_cover(
            cover_file,
            title=title,
            artist=artist,
            genre_id=genre.id,
            seed=seg_id,
            backend=backend,
            steps=steps,
        )
        cover_path = cover_file
    except Exception as exc:  # noqa: BLE001
        log.warning("  [song] cover art failed (non-fatal): %s", exc)

    return Segment(
        id=seg_id,
        kind="song",
        title=title,
        genre_id=genre.id,
        text=lyrics,
        audio_path=final_wav,
        duration_ms=duration_ms,
        created_at=time.time(),
        artist=artist,
        generation_prompt=gen_prompt,
        cover_path=cover_path,
    )
