from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from pathlib import Path

from airadio.art.cover import generate_cover
from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.acestep import generate_song
from airadio.clients.ollama import ollama_chat
from airadio.languages import ace_vocal_language, get_language, language_instruction
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


def _form_for_duration(duration_sec: int, *, instrumental: bool) -> tuple[str, str]:
    """
    Popular radio-single forms scaled to target length.
    Returns (form_label, section_map for prompts).
    """
    d = max(45, int(duration_sec))
    if instrumental:
        if d < 100:
            form = "Intro → Theme A → Theme B → Theme A' → Outro"
        elif d < 150:
            form = (
                "Intro → Theme A → Theme B → Development → Theme A return → "
                "Breakdown → Final theme → Outro"
            )
        else:
            form = (
                "Intro → Theme A → Theme B → Theme A variation → Bridge/break → "
                "Climactic theme → Soft outro"
            )
        return "instrumental arc", form

    if d < 100:
        # ~1:15–1:40 compact edit
        form = "[Intro] [Verse 1] [Chorus] [Verse 2] [Chorus] [Outro]"
        return "compact radio edit", form
    if d < 150:
        # ~2:00–2:30
        form = (
            "[Intro] [Verse 1] [Pre-Chorus] [Chorus] [Verse 2] [Pre-Chorus] "
            "[Chorus] [Bridge] [Final Chorus] [Outro]"
        )
        return "standard radio single", form
    # ~2:30–3:30 full pop/rock single
    form = (
        "[Intro] [Verse 1] [Pre-Chorus] [Chorus] [Verse 2] [Pre-Chorus] "
        "[Chorus] [Bridge / break] [Final Chorus (lift)] [Outro]"
    )
    return "full radio single", form


async def _compose_track(
    station: StationConfig, genre: Genre
) -> tuple[str, str, str, str]:
    """Return (artist/band, title, lyrics, style_prompt_for_ace)."""
    instrumental = (
        "instrumental" in genre.lyric_style.lower()
        or "no vocal" in genre.lyric_style.lower()
    )
    duration = int(station.song_duration_sec or genre.duration_sec or 165)
    form_label, form_map = _form_for_duration(duration, instrumental=instrumental)

    lang = get_language(station.language)
    if instrumental:
        lyric_rules = (
            '- "lyrics": empty string ""\n'
            "- Artist can be a composer / project name.\n"
            f'- "arrangement": describe the {form_label} in one line for the music model.'
        )
        lyric_key_help = 'Use "" for lyrics.'
    else:
        lyric_rules = (
            '- "lyrics": FULL singable lyrics using EXACT section tags from the form below. '
            "Every section listed must appear with real lines (not placeholders). "
            "Choruses should share a hook; verses develop the story.\n"
            f"- Lyrics language: {lang.prompt_name} ONLY "
            f"(section tags like [Chorus] may stay English; sung lines in {lang.prompt_name}).\n"
            f'- "arrangement": one line restating the form + energy curve for the music model.'
        )
        lyric_key_help = (
            f"Write complete lyrics for every section in the form, in {lang.prompt_name}."
        )

    user = f"""Invent one original radio SINGLE that sounds like a real chart/playlist track.

Genre / subgenre: «{genre.name}» (major: {genre.major or "music"})

Genre style guide:
{genre.style_prompt}

Lyric style: {genre.lyric_style}
{language_instruction(station.language)}
Target duration: {duration} seconds (~{duration // 60}:{duration % 60:02d}).

SONG FORM (required — this is not a looped 30s sketch):
Form type: {form_label}
Sections in order:
{form_map}

Respond with ONLY valid JSON (no markdown) with keys:
- "artist": fictional band or artist name that fits this genre (sounds real)
- "title": short song title (vivid, radio-real; prefer {lang.prompt_name} wording when natural)
- "lyrics": string ({lyric_key_help})
- "style_line": dense production prompt (instruments, BPM, vocal character, mix)
- "arrangement": how energy moves through the form (intro sparse → chorus lift, etc.)

Rules:
{lyric_rules}
- Artist and title must feel like Spotify / radio playlist names.
- Never use: Transmission, Untitled, AI, Generated, Radio Station, Test.
- Do NOT collapse the form into one verse and one chorus only — hit the full map.
- Tag sections clearly, e.g. [Intro], [Verse 1], [Chorus], [Bridge], [Outro].
"""
    raw = await ollama_chat(
        station.ollama_base_url,
        station.ollama_model,
        "You write complete radio singles with classic pop/rock song form. "
        f"Output JSON only. All sung lyrics must be in {lang.prompt_name}. "
        "Lyrics must cover the full requested structure.",
        user,
        num_gpu=0,
        timeout=180.0,
        temperature=0.9,
        max_tokens=900,
    )
    data = _parse_json_blob(raw)
    artist = str(
        data.get("artist") or data.get("band") or data.get("artist_name") or ""
    ).strip()
    title = str(data.get("title") or data.get("song") or data.get("name") or "").strip()
    if not artist:
        raise RuntimeError(f"Song LLM omitted artist: {data!r}"[:300])
    if not title:
        raise RuntimeError(f"Song LLM omitted title: {data!r}"[:300])
    artist = artist.strip('"').strip("'")
    title = title.strip('"').strip("'")
    lyrics = str(data.get("lyrics") or "").strip()
    style_line = str(data.get("style_line") or genre.style_prompt).strip()
    arrangement = str(data.get("arrangement") or form_map).strip()

    # Fold form into ACE caption so DiT aims for multi-section song, not a loop
    style = (
        f"{genre.style_prompt.strip()} {style_line} "
        f"Song structure: {form_map}. Arrangement: {arrangement}. "
        f"Full {duration}s radio single with clear section changes "
        f"(intro, verses, choruses, bridge/break, outro) — not a short loop. "
        f"Vocals and lyrical language: {lang.prompt_name}. "
        f"Artist vibe: {artist}."
    ).strip()
    return artist, title, lyrics, style


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

    artist, title, lyrics, style = await _compose_track(station, genre)
    log.info("Song meta: %s — %s [%s]", artist, title, genre.id)

    duration = station.song_duration_sec or genre.duration_sec
    vlang = ace_vocal_language(station.language)
    try:
        await generate_song(
            style,
            lyrics,
            duration,
            raw_wav,
            cmd=station.acestep_cmd,
            vocal_language=vlang,
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
            vocal_language=vlang,
        )

    try:
        # Pin 48 kHz. Light trailing-silence trim only — keep real outros.
        loudnorm_ffmpeg(raw_wav, final_wav, sample_rate=48000, trim_silence=True)
        if not final_wav.is_file():
            raw_wav.replace(final_wav)
    except Exception:  # noqa: BLE001
        if raw_wav.is_file() and not final_wav.is_file():
            raw_wav.replace(final_wav)

    duration_ms = probe_duration_ms(final_wav)
    requested_ms = int(duration) * 1000
    if duration_ms < max(10_000, int(requested_ms * 0.45)):
        log.warning(
            "Song much shorter than requested: got %.1fs vs %ss target (%s — %s)",
            duration_ms / 1000.0,
            duration,
            artist,
            title,
        )
    else:
        log.info(
            "Song ready: %s — %s [%.1fs, target %ss]",
            artist,
            title,
            duration_ms / 1000.0,
            duration,
        )
    if raw_wav.is_file() and raw_wav != final_wav:
        try:
            raw_wav.unlink(missing_ok=True)
        except OSError:
            pass

    gen_prompt = (
        f"# {artist} — {title}\n"
        f"Genre: {genre.id} ({genre.name})\n"
        f"Duration target: {duration}s\n"
        f"Actual length: {duration_ms / 1000.0:.1f}s\n"
        f"\n## Style prompt (ACE-Step)\n{style}\n"
        f"\n## Lyrics\n{lyrics if lyrics else '(instrumental / empty)'}\n"
    )

    cover_path: Path | None = None
    cover_file = out_dir / f"{seg_id}_cover.png"
    try:
        generate_cover(
            cover_file,
            title=title,
            artist=artist,
            genre_id=genre.id,
            seed=seg_id,
        )
        cover_path = cover_file
    except Exception as exc:  # noqa: BLE001
        log.warning("Cover art failed (non-fatal): %s", exc)

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
