from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000

# Cached pipeline — loading weights is expensive
_pipeline = None
_pipeline_voice_device: str | None = None


def kokoro_available() -> tuple[bool, str]:
    if os.environ.get("KOKORO_URL"):
        return True, f"HTTP mode ({os.environ['KOKORO_URL']})"
    try:
        import kokoro  # noqa: F401

        device = os.environ.get("KOKORO_DEVICE", "cpu")
        return True, f"python package 'kokoro' importable (device={device})"
    except Exception as exc:  # noqa: BLE001
        return False, f"kokoro not available: {exc}"


def synthesize_kokoro(text: str, voice: str, out_path: Path) -> int:
    """
    Synthesize speech to WAV. Returns duration_ms.
    Uses KOKORO_URL OpenAI-compatible API if set, else local kokoro package.

    Default device is **CPU** so the GPU can stay free for the LLM / ACE-Step.
    Override with env KOKORO_DEVICE=cuda if you have spare VRAM.
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


def _get_pipeline():
    global _pipeline, _pipeline_voice_device
    device = os.environ.get("KOKORO_DEVICE", "cpu")
    if _pipeline is not None and _pipeline_voice_device == device:
        return _pipeline

    # Use espeak-ng from the venv (espeakng-loader) — never system apt packages
    from airadio.paths import ensure_bundled_espeak

    ensure_bundled_espeak()

    from kokoro import KPipeline

    log.info("Loading Kokoro pipeline on device=%s (bundled espeak)", device)
    _pipeline = KPipeline(
        lang_code="a",
        repo_id="hexgrad/Kokoro-82M",
        device=device,
    )
    _pipeline_voice_device = device
    return _pipeline


def _synthesize_local(text: str, voice: str, out_path: Path) -> int:
    try:
        pipeline = _get_pipeline()
    except ImportError as exc:
        raise RuntimeError(
            "kokoro package not installed. pip install -e . "
            "and ensure espeakng-loader is available, or set KOKORO_URL"
        ) from exc

    chunks: list[np.ndarray] = []
    for result in pipeline(text, voice=voice):
        audio = None
        if hasattr(result, "audio"):
            audio = result.audio
        elif isinstance(result, (tuple, list)) and len(result) >= 3:
            audio = result[2]
        if audio is None:
            continue
        # Move off GPU if tensor
        if hasattr(audio, "detach"):
            audio = audio.detach().cpu().numpy()
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        chunks.append(arr)

    if not chunks:
        raise RuntimeError("Kokoro produced no audio")

    audio_out = np.concatenate(chunks)
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
