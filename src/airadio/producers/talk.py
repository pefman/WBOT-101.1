from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.kokoro import synthesize_kokoro
from airadio.clients.ollama import ollama_chat
from airadio.config import default_config_dir
from airadio.models_types import Segment, StationConfig

log = logging.getLogger(__name__)

StageCb = Callable[[str, str], None]

BANNED_WHOLE_LINES = (
    "more music is on the way",
    "stay tuned",
    "you're listening to",
    "good evening, folks",
    "good evening folks",
    "tuning in to",
    "keep you company through the night",
    "you're tuning in",
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


def _pick_mode() -> tuple[str, str, int | None]:
    """Return (mode_id, instruction, max_words or None)."""
    data = _load_yaml("talk_modes.yaml")
    modes = data.get("modes") or {}
    if not modes:
        return "bridge", "Bridge between songs with warmth and specificity.", None
    items: list[tuple[str, str, int, int | None]] = []
    for mid, body in modes.items():
        body = body or {}
        w = int(body.get("weight") or 1)
        instr = str(body.get("instruction") or "").strip()
        mw = body.get("max_words")
        max_w = int(mw) if mw is not None else None
        items.append((str(mid), instr, max(1, w), max_w))
    total = sum(w for _, _, w, _ in items)
    r = random.uniform(0, total)
    acc = 0.0
    for mid, instr, w, max_w in items:
        acc += w
        if r <= acc:
            return mid, instr, max_w
    return items[-1][0], items[-1][1], items[-1][3]


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
        if ban in low and len(low) < 140:
            return True
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
    max_words: int,
    voice_samples: list[str] | None = None,
    user_request: str | None = None,
) -> str:
    now = datetime.now()
    # DJ talk / Kokoro TTS stays English — station.language only affects music.
    parts = [
        f"Host on mic: {station.host_name} on {station.name}. "
        f"Introduce yourself only as {station.host_name}.",
        "Write the entire mic script in English only (TTS is English).",
        f"Mode: {mode}",
        f"Mode brief: {mode_instruction}",
        f"Daypart: {_daypart(now)} ({now.strftime('%H:%M')}).",
        f"Hard max ~{max_words} words (prefer fewer).",
        "Output ONLY the words said on the mic. No quotes, stage directions, bullets, labels, or markdown.",
        "TTS rules: no emoji, no URLs, no ALL CAPS shouting, expand numbers as words, keep lines speakable.",
        "Sound like a real FM host — specific, alive, never corporate. Adult language OK if it fits the host.",
        f"Spice card: {spice}",
        "NEVER open with dead phrases: \"good evening folks\", \"you're tuning in\", "
        "\"more music is on the way\", \"stay tuned\", \"keep you company through the night\".",
        "Do not invent product features or mention APIs, models, or being an AI.",
        "Prefer naming the last or next track over abstract vibe talk. One metaphor max.",
    ]
    if voice_samples:
        sample = random.choice(voice_samples)
        parts.append(
            f"Voice reference (match tone/rhythm, do not copy wording): «{sample}»"
        )
    if mood_label:
        parts.append(f"Active station mood: {mood_label}.")
    if mood_genres:
        parts.append(
            "Mood genres: " + ", ".join(g.replace("_", " ") for g in mood_genres[:8])
        )
    if prev_song and prev_song.kind == "song":
        g = f" [{prev_song.genre_id}]" if prev_song.genre_id else ""
        art = f"{prev_song.artist} — " if prev_song.artist else ""
        parts.append(f"Just finished on air: {art}«{prev_song.title}»{g}.")
        if prev_song.text and prev_song.text.strip():
            snippet = prev_song.text.strip().split("\n")[0][:80]
            parts.append(f"Lyric vibe snippet: {snippet}")
        parts.append("You may name that artist and song once as the track that just ended.")
        parts.append(
            "Do NOT invent other track titles. "
            "Do NOT say you are about to play that same song again. "
            "It already played — look forward without repeating its title as 'up next'."
        )
    # Only tease next if it is a *different* track than just-played
    if (
        next_song
        and next_song.kind == "song"
        and (not prev_song or next_song.id != prev_song.id)
        and (
            not prev_song
            or (next_song.title or "").casefold() != (prev_song.title or "").casefold()
        )
    ):
        g = f" [{next_song.genre_id}]" if next_song.genre_id else ""
        art = f"{next_song.artist} — " if next_song.artist else ""
        parts.append(f"Coming up next (different track): {art}«{next_song.title}»{g}.")
        parts.append("You may tease this next artist + title once. Do not say it already played.")
    elif prev_song and prev_song.kind == "song":
        parts.append(
            "No separate next-track title is available — do not invent one. "
            "Bridge out of the last song into the vibe of more music."
        )
    if not prev_song and not next_song:
        parts.append(
            f"Top of set / cold open on {station.name} — no previous track this session. "
            "Do NOT say \"that was…\" or name a song that just finished. Welcome people forward."
        )
    elif not prev_song and next_song:
        parts.append(
            "No song has finished airplay yet this session — do NOT say \"that was…\". "
            "You may tease the coming track only."
        )

    if mode == "news" or news_angle:
        parts.append(
            "Include a brief funny world-news bit (1–2 sentences). "
            "Sharp satire OK; no real hate; no cruelty toward real victims."
        )
        if news_angle:
            parts.append(f"News angle seed: {news_angle}")

    if recent_talk:
        parts.append("Do NOT reuse phrases, openings, or structures from recent breaks:")
        for i, t in enumerate(recent_talk[-6:], 1):
            parts.append(f"  {i}. {t[:140]}")

    if user_request and user_request.strip():
        parts.append(
            "LISTENER REQUEST (highest priority — deliver this on-mic naturally, "
            "in character, without reading it as a meta instruction): "
            f"«{user_request.strip()[:400]}»"
        )
        parts.append(
            "Fold the request into real radio talk (not 'a listener asked me to…' "
            "unless it fits). Stay under the word max."
        )

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
    voice_samples: list[str] | None = None,
    user_request: str | None = None,
    on_stage: StageCb | None = None,
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

    host_name = station.host_name
    voice_id = station.kokoro_voice
    system_prompt = station.system_prompt
    ollama_base = station.ollama_base_url
    ollama_model = station.ollama_model
    talk_max = station.talk_max_words
    news_chance = float(getattr(station, "news_bit_chance", 0) or 0)

    mode, mode_instruction, mode_max = _pick_mode()
    max_words = mode_max if mode_max is not None else talk_max
    max_words = max(8, min(int(max_words), int(talk_max)))

    news_angle = None
    if mode == "news" or (
        random.random() < news_chance and mode not in ("silence_break",)
    ):
        news_angle = _pick_news_angle(station)
        if mode != "news" and news_angle and random.random() < 0.5:
            mode, mode_instruction, mode_max = (
                "news",
                "Funny world-news bit then back to music. Sharp, witty, not cruel.",
                mode_max or 55,
            )
            max_words = mode_max if mode_max is not None else max_words
            max_words = max(8, min(int(max_words), int(talk_max)))

    spice = _pick_spice()
    if dj_tone:
        spice = f"{spice} Host tone hint: {dj_tone}"

    # Listener request overrides mode variety — still a normal talk length
    if user_request and user_request.strip():
        mode = "request"
        mode_instruction = (
            "Fulfill the listener request on-mic as a short radio bit, "
            "then land cleanly toward music if natural."
        )

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
        max_words=max_words,
        voice_samples=voice_samples,
        user_request=user_request,
    )

    def _stage(stage: str, detail: str = "") -> None:
        if on_stage:
            try:
                on_stage(stage, detail)
            except Exception:  # noqa: BLE001
                pass

    log.info(
        "  [talk] 1/3 LLM writing script (host=%s mode=%s max_words=%s model=%s)…",
        host_name,
        mode,
        max_words,
        ollama_model,
    )
    _stage("talk_writing", f"Writing DJ script ({host_name})…")
    script = await ollama_chat(
        ollama_base,
        ollama_model,
        system_prompt,
        user,
        temperature=1.05,
        max_tokens=min(280, max(120, max_words * 3)),
    )
    script = script.strip().strip('"').strip("'")
    script = re.sub(r"\s+", " ", script)
    script = _trim_words(script, max_words)
    if _looks_banned(script):
        log.info("  [talk]    script had banned clichés — rewriting…")
        retry_user = (
            user
            + "\n\nRewrite completely. Different opening word. "
            "No stay-tuned / good-evening-folks clichés. Start mid-thought."
        )
        script = await ollama_chat(
            ollama_base,
            ollama_model,
            system_prompt,
            retry_user,
            temperature=1.1,
            max_tokens=min(240, max(100, max_words * 3)),
        )
        script = _trim_words(script.strip().strip('"'), max_words)
    if _looks_banned(script):
        raise RuntimeError(
            f"Talk LLM produced unusable copy (mode={mode}): {script!r}"[:300]
        )
    log.info(
        "  [talk]    → %s: %.100s%s",
        host_name,
        script,
        "…" if len(script) > 100 else "",
    )

    log.info("  [talk] 2/3 Kokoro TTS speaking (voice=%s)…", voice_id)
    _stage("talk_speaking", f"Speaking with voice {voice_id}…")
    try:
        duration_ms = await asyncio.to_thread(
            synthesize_kokoro, script, voice_id, raw_wav
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Kokoro TTS failed: {exc}") from exc

    log.info("  [talk] 3/3 Loudnorm / finalize (%.1fs raw)…", duration_ms / 1000.0)
    _stage("talk_finalize", "Normalizing talk audio…")
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

    log.info("  [talk] Done «%s» (%.1fs)", title, duration_ms / 1000.0)
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
