from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path

from airadio.audio.process import loudnorm_ffmpeg, probe_duration_ms
from airadio.clients.kokoro import synthesize_kokoro, write_silence_wav
from airadio.clients.ollama import ollama_chat
from airadio.models_types import Segment, StationConfig

log = logging.getLogger(__name__)


def _trim_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",;:") + "."


def _build_user_prompt(
    station: StationConfig,
    prev_song: Segment | None,
    next_song: Segment | None,
    dj_tone: str | None,
) -> str:
    parts = [
        f"Write a short on-air talk segment for host {station.host_name}.",
        f"Maximum about {station.talk_max_words} words.",
        "Output only the words spoken on mic — no quotes, no stage directions.",
    ]
    if dj_tone:
        parts.append(f"Tone: {dj_tone}")
    if prev_song and prev_song.kind == "song":
        parts.append(
            f"You just finished playing: «{prev_song.title}»"
            + (f" ({prev_song.genre_id})" if prev_song.genre_id else "")
            + "."
        )
    if next_song and next_song.kind == "song":
        parts.append(
            f"Coming up next: «{next_song.title}»"
            + (f" ({next_song.genre_id})" if next_song.genre_id else "")
            + "."
        )
    if not prev_song and not next_song:
        parts.append("This is the top of the hour — welcome listeners to the station.")
    return "\n".join(parts)


async def produce_talk(
    station: StationConfig,
    out_dir: Path,
    *,
    prev_song: Segment | None = None,
    next_song: Segment | None = None,
    dj_tone: str | None = None,
) -> Segment:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_id = uuid.uuid4().hex
    raw_wav = out_dir / f"{seg_id}_raw.wav"
    final_wav = out_dir / f"{seg_id}.wav"

    user = _build_user_prompt(station, prev_song, next_song, dj_tone)
    try:
        script = await ollama_chat(
            station.ollama_base_url,
            station.ollama_model,
            station.system_prompt,
            user,
            num_gpu=0,
        )
        script = script.strip().strip('"').strip("'")
        script = _trim_words(script, station.talk_max_words)
    except Exception as exc:  # noqa: BLE001
        log.exception("Talk LLM failed: %s", exc)
        script = (
            f"You're listening to {station.name}. Stay tuned — more music is on the way."
        )

    try:
        duration_ms = await asyncio.to_thread(
            synthesize_kokoro, script, station.kokoro_voice, raw_wav
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Kokoro failed, using silence: %s", exc)
        duration_ms = write_silence_wav(raw_wav, 1.5)
        script = script or "(silence)"

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

    return Segment(
        id=seg_id,
        kind="talk",
        title=f"On air: {station.host_name}",
        genre_id=None,
        text=script,
        audio_path=final_wav,
        duration_ms=duration_ms,
        created_at=time.time(),
    )
