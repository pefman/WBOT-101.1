"""Orpheus TTS client for natural DJ voice synthesis with emotion injection."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000

# Cached model — loading weights is expensive (~6-8GB VRAM)
_model = None
_model_device: str | None = None


def orpheus_available() -> tuple[bool, str]:
    """Check if TTS is available (Orpheus or Kokoro fallback)."""
    try:
        import kokoro  # noqa: F401
        return True, "Kokoro TTS available (local, Orpheus client API not supported)"
    except Exception:  # noqa: BLE001
        return False, "No TTS available"


def get_orpheus_model() -> object:
    """Lazy-load Kokoro TTS pipeline."""
    global _model, _model_device
    device = os.environ.get("ORPHEUS_DEVICE", "cpu")

    if _model is not None and _model_device == device:
        return _model

    log.info("Loading Kokoro TTS pipeline (lang=en-us, device=%s)…", device)
    try:
        from kokoro import KPipeline

        _model = KPipeline(lang_code="a", device=device)
        _model_device = device
        log.info("✓ Kokoro TTS pipeline loaded")
        return _model
    except Exception as exc:
        raise RuntimeError(f"Failed to load Kokoro TTS pipeline: {exc}") from exc


def unload_orpheus_model() -> None:
    """Free memory by unloading the model."""
    global _model
    if _model is not None:
        try:
            del _model
            _model = None
            log.info("✓ TTS model unloaded")
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to unload TTS model: %s", exc)


def synthesize_orpheus(
    text: str,
    voice: str,
    out_path: Path,
    emotions: list[str] | None = None,
) -> int:
    """
    Synthesize speech with Kokoro TTS. Returns duration_ms.

    Args:
        text: Script to synthesize
        voice: Voice ID (for Kokoro; orpheus voices mapped to kokoro equivalents)
        out_path: Output WAV file path
        emotions: Ignored (Kokoro doesn't support emotion injection)

    Kokoro voices: af, af_bella, af_nicole, af_sarah, af_sky, am_adam, am_michael, bm_lewis, bm_george
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text for Kokoro TTS")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Map Orpheus voices to Kokoro equivalents
    voice_map = {
        "tara": "af_bella",
        "leah": "af_nicole",
        "jess": "af_sky",
        "leo": "am_michael",
        "dan": "am_adam",
        "mia": "af",
        "zac": "am_lewis",
        "zoe": "af_sarah",
    }
    kokoro_voice = voice_map.get(voice, "am_michael")

    try:
        pipeline = get_orpheus_model()

        log.info("  [kokoro] Synthesizing (%s, %d chars)…", kokoro_voice, len(text))

        # Kokoro KPipeline yields results with audio chunks
        audio_chunks = []
        for result in pipeline(text, voice=kokoro_voice, speed=1.0):
            # KPipeline.Result has .audio attribute with numpy array
            if hasattr(result, 'audio') and result.audio is not None:
                audio_chunks.append(np.asarray(result.audio, dtype=np.float32))

        if not audio_chunks:
            raise RuntimeError("Kokoro produced no audio")

        # Concatenate chunks
        audio_out = np.concatenate(audio_chunks)

        # Normalize if needed
        peak = float(np.max(np.abs(audio_out))) or 1.0
        if peak > 1.0:
            audio_out = audio_out / peak

        # Write to file (Kokoro uses 24kHz)
        sf.write(str(out_path), audio_out, SAMPLE_RATE)
        duration_ms = int(round(len(audio_out) / SAMPLE_RATE * 1000))
        log.info("  [kokoro] ✓ %d ms written to %s", duration_ms, out_path.name)
        return duration_ms

    except Exception as exc:
        log.error("  [kokoro] synthesis failed: %s", exc)
        raise RuntimeError(f"Kokoro TTS failed: {exc}") from exc


def write_silence_wav(out_path: Path, duration_sec: float = 1.0) -> int:
    """Fallback silence when TTS fails."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = int(SAMPLE_RATE * duration_sec)
    sf.write(str(out_path), np.zeros(n, dtype=np.float32), SAMPLE_RATE)
    return int(duration_sec * 1000)
