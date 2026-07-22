from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.kokoro import synthesize_kokoro
from airadio.clients.ollama import ollama_chat
from airadio.config import default_config_dir
from airadio.models_types import Segment, StationConfig

log = logging.getLogger(__name__)

BANNED_WHOLE_LINES = (
    "more music is on the way",
    "stay tuned",
    "you're listening to",
)


def _trim_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",;:") + "."


def _load_yaml(name: str) -> dict:
    path = default_config_dir() / name
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pick_mode() -> tuple[str, str]:
    data = _load_yaml("talk_modes.yaml")
    modes = data.get("modes") or {}
    if not modes:
        return "bridge", "Bridge between songs with warmth and specificity."
    items: list[tuple[str, str, int]] = []
    for mid, body in modes.items():
        body = body or {}
        w = int(body.get("weight") or 1)
        instr = str(body.get("instruction") or "").strip()
        items.append((str(mid), instr, max(1, w)))
    total = sum(w for _, _, w in items)
    r = random.uniform(0, total)
    acc = 0.0
    for mid, instr, w in items:
        acc += w
        if r <= acc:
            return mid, instr
    return items[-1][0], items[-1][1]


def _pick_spice() -> str:
    cards = (_load_yaml("dj_spice.yaml").get("cards") or [])
    if not cards:
        return "Be specific and human."
    return str(random.choice(cards))


def _daypart(now: datetime | None = None) -> str:
    h = (now or datetime.now()).hour
    if 5 <= h < 11:
        return "morning coffee hours"
    if 11 <= h < 17:
        return "afternoon stretch"
    if 17 <= h < 21:
        return "evening wind-down"
    if 21 <= h or h < 2:
        return "late night"
    return "deep midnight"


def _looks_banned(script: str) -> bool:
    low = script.lower().strip()
    if len(low.split()) < 4:
        return True
    for ban in BANNED_WHOLE_LINES:
        if ban in low and len(low) < 120:
            return True
    # exact-ish canned fallback
    if "more music is on the way" in low:
        return True
    return False


def _build_user_prompt(
    station: StationConfig,
    prev_song: Segment | None,
    next_song: Segment | None,
    *,
    mode: str,
    mode_instruction: str,
    spice: str,
    mood_label: str | None,
    mood_genres: list[str] | None,
    recent_talk: list[str] | None,
    news_angle: str | None,
) -> str:
    now = datetime.now()
    # DJ talk / Kokoro TTS stays English — station.language only affects music.
    parts = [
        f"You are writing spoken on-air copy for {station.host_name} on {station.name}.",
        f"The host's name is {station.host_name} — introduce yourself only as that name, never another DJ.",
        "Write the entire mic script in English only (host TTS is English).",
        f"Mode: {mode}",
        f"Mode brief: {mode_instruction}",
        f"Daypart: {_daypart(now)} ({now.strftime('%H:%M')}).",
        f"Hard max ~{station.talk_max_words} words.",
        "Output ONLY the words said on the mic. No quotes, stage directions, bullets, or labels.",
        "Sound like a real late-night FM host — specific, alive, never corporate.",
        f"Spice card: {spice}",
        "NEVER use these dead phrases: \"more music is on the way\", "
        "\"stay tuned\", \"you're listening to X, stay tuned\".",
        "Do not invent product features or mention APIs, models, or being an AI.",
    ]
    if mood_label:
        parts.append(f"Active station mood: {mood_label}.")
    if mood_genres:
        parts.append("Mood genres: " + ", ".join(g.replace("_", " ") for g in mood_genres[:8]))
    if prev_song and prev_song.kind == "song":
        g = f" [{prev_song.genre_id}]" if prev_song.genre_id else ""
        art = f"{prev_song.artist} — " if prev_song.artist else ""
        parts.append(f"Just played: {art}«{prev_song.title}»{g}.")
        if prev_song.text and prev_song.text.strip():
            snippet = prev_song.text.strip().split("\n")[0][:80]
            parts.append(f"Lyric vibe snippet: {snippet}")
        parts.append("You may name the artist and song like a real DJ would.")
    if next_song and next_song.kind == "song":
        g = f" [{next_song.genre_id}]" if next_song.genre_id else ""
        art = f"{next_song.artist} — " if next_song.artist else ""
        parts.append(f"Coming up: {art}«{next_song.title}»{g}.")
        parts.append("You may tease artist + title if natural.")
    if not prev_song and not next_song:
        parts.append(f"Top of set — welcome people to {station.name}.")

    if mode == "news" or news_angle:
        parts.append(
            "Include a brief funny world-news bit (1–2 sentences), radio-safe satire."
        )
        if news_angle:
            parts.append(f"News angle seed: {news_angle}")

    if recent_talk:
        parts.append("Do NOT reuse phrases, openings, or structures from recent breaks:")
        for i, t in enumerate(recent_talk[-6:], 1):
            parts.append(f"  {i}. {t[:140]}")

    return "\n".join(parts)


def _pick_news_angle(station: StationConfig) -> str | None:
    angles = list(station.news_angles or [])
    if not angles:
        return None
    return random.choice(angles)


async def produce_talk(
    station: StationConfig,
    out_dir: Path,
    *,
    prev_song: Segment | None = None,
    next_song: Segment | None = None,
    dj_tone: str | None = None,
    mood_label: str | None = None,
    mood_genres: list[str] | None = None,
    recent_talk: list[str] | None = None,
    generation_id: int | None = None,
) -> Segment:
    """Generate one talk break.

    Host name, system prompt, and Kokoro voice are snapshotted at the start so a
    mid-flight DJ/voice switch cannot produce a Rex script titled Vega with a
    mixed voice.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_id = uuid.uuid4().hex
    raw_wav = out_dir / f"{seg_id}_raw.wav"
    final_wav = out_dir / f"{seg_id}.wav"

    # Freeze identity for this generation (station fields may change while we wait)
    host_name = station.host_name
    voice_id = station.kokoro_voice
    system_prompt = station.system_prompt
    ollama_base = station.ollama_base_url
    ollama_model = station.ollama_model
    talk_max = station.talk_max_words
    news_chance = float(getattr(station, "news_bit_chance", 0) or 0)

    mode, mode_instruction = _pick_mode()
    # Force news mode sometimes via station chance even if mode wasn't news
    news_angle = None
    if mode == "news" or (
        random.random() < news_chance and mode not in ("silence_break",)
    ):
        if mode != "news":
            # blend: keep mode but add news
            pass
        news_angle = _pick_news_angle(station)
        if mode != "news" and news_angle and random.random() < 0.5:
            mode, mode_instruction = "news", (
                "Funny world-news bit then back to music. Witty, radio-safe."
            )

    spice = _pick_spice()
    if dj_tone:
        spice = f"{spice} Host tone hint: {dj_tone}"

    user = _build_user_prompt(
        station,
        prev_song,
        next_song,
        mode=mode,
        mode_instruction=mode_instruction,
        spice=spice,
        mood_label=mood_label,
        mood_genres=mood_genres,
        recent_talk=recent_talk,
        news_angle=news_angle,
    )
    # Pin host in the user prompt even if station mutates mid-call
    user = f"Host on mic for this break: {host_name}.\n{user}"

    script = await ollama_chat(
        ollama_base,
        ollama_model,
        system_prompt,
        user,
        num_gpu=0,
        temperature=1.05,
        max_tokens=min(280, max(120, talk_max * 3)),
    )
    script = script.strip().strip('"').strip("'")
    script = re.sub(r"\s+", " ", script)
    script = _trim_words(script, talk_max)
    if _looks_banned(script):
        retry_user = user + "\n\nRewrite completely. Ban any stay-tuned clichés."
        script = await ollama_chat(
            ollama_base,
            ollama_model,
            system_prompt,
            retry_user,
            num_gpu=0,
            temperature=1.1,
            max_tokens=200,
        )
        script = _trim_words(script.strip().strip('"'), talk_max)
    if _looks_banned(script):
        raise RuntimeError(
            f"Talk LLM produced unusable copy (mode={mode}): {script!r}"[:300]
        )
    log.info(
        "Talk mode=%s host=%s voice=%s gen=%s: %.80s…",
        mode,
        host_name,
        voice_id,
        generation_id,
        script,
    )

    try:
        duration_ms = await asyncio.to_thread(
            synthesize_kokoro, script, voice_id, raw_wav
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Kokoro TTS failed: {exc}") from exc

    try:
        loudnorm_ffmpeg(raw_wav, final_wav)
        if final_wav.is_file():
            duration_ms = probe_duration_ms(final_wav)
        else:
            raw_wav.replace(final_wav)
    except Exception:  # noqa: BLE001
        if raw_wav.is_file() and not final_wav.is_file():
            raw_wav.replace(final_wav)

    if raw_wav.is_file() and raw_wav != final_wav:
        try:
            raw_wav.unlink(missing_ok=True)
        except OSError:
            pass

    if mode == "news" or news_angle:
        title = f"On air: {host_name} · news bit"
    else:
        title = f"On air: {host_name} · {mode}"

    return Segment(
        id=seg_id,
        kind="talk",
        title=title,
        genre_id=None,
        text=script,
        audio_path=final_wav,
        duration_ms=duration_ms,
        created_at=time.time(),
        host_name=host_name,
        voice_id=voice_id,
        generation_id=generation_id,
    )
