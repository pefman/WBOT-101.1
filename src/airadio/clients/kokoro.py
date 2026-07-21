from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


def kokoro_available() -> tuple[bool, str]:
    if os.environ.get("KOKORO_URL"):
        return True, f"HTTP mode ({os.environ['KOKORO_URL']})"
    try:
        import kokoro  # noqa: F401

        return True, "python package 'kokoro' importable"
    except Exception as exc:  # noqa: BLE001
        return False, f"kokoro not available: {exc}"


def synthesize_kokoro(text: str, voice: str, out_path: Path) -> int:
    """
    Synthesize speech to WAV. Returns duration_ms.
    Uses KOKORO_URL OpenAI-compatible API if set, else local kokoro package.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text for TTS")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("KOKORO_URL")
    if url:
        return _synthesize_http(text, voice, out_path, url.rstrip("/"))
    return _synthesize_local(text, voice, out_path)


def _synthesize_http(text: str, voice: str, out_path: Path, base: str) -> int:
    # Kokoro-FastAPI OpenAI-compatible speech endpoint
    endpoint = f"{base}/v1/audio/speech"
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": voice,
        "response_format": "wav",
    }
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(endpoint, json=payload)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
    return _duration_ms(out_path)


def _synthesize_local(text: str, voice: str, out_path: Path) -> int:
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise RuntimeError(
            "kokoro package not installed. pip install kokoro soundfile "
            "and system package espeak-ng, or set KOKORO_URL"
        ) from exc

    # lang_code 'a' = American English (Kokoro convention)
    pipeline = KPipeline(lang_code="a")
    chunks: list[np.ndarray] = []
    for result in pipeline(text, voice=voice):
        # result may be tuple (graphemes, phonemes, audio) or object with .audio
        audio = None
        if hasattr(result, "audio"):
            audio = result.audio
        elif isinstance(result, (tuple, list)) and len(result) >= 3:
            audio = result[2]
        if audio is None:
            continue
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        chunks.append(arr)

    if not chunks:
        raise RuntimeError("Kokoro produced no audio")

    audio_out = np.concatenate(chunks)
    # peak normalize lightly
    peak = float(np.max(np.abs(audio_out))) or 1.0
    if peak > 1.0:
        audio_out = audio_out / peak
    sf.write(str(out_path), audio_out, SAMPLE_RATE)
    return int(round(len(audio_out) / SAMPLE_RATE * 1000))


def _duration_ms(path: Path) -> int:
    info = sf.info(str(path))
    return int(round(info.duration * 1000))


def write_silence_wav(out_path: Path, duration_sec: float = 1.0) -> int:
    """Fallback sting when TTS fails."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = int(SAMPLE_RATE * duration_sec)
    sf.write(str(out_path), np.zeros(n, dtype=np.float32), SAMPLE_RATE)
    return int(duration_sec * 1000)
