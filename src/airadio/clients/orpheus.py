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
    """Check if Orpheus TTS is available."""
    try:
        import vllm  # noqa: F401
        from orpheus_tts import OrpheusModel  # noqa: F401

        device = os.environ.get("ORPHEUS_DEVICE", "cuda")
        return True, f"orpheus-speech + vllm available (device={device})"
    except Exception as exc:  # noqa: BLE001
        return False, f"Orpheus TTS not available: {exc}"


def get_orpheus_model() -> object:
    """Lazy-load Orpheus model."""
    global _model, _model_device
    device = os.environ.get("ORPHEUS_DEVICE", "cuda")

    if _model is not None and _model_device == device:
        return _model

    log.info("Loading Orpheus TTS model (device=%s, ~6-8GB VRAM)…", device)
    try:
        from orpheus_tts import OrpheusModel

        _model = OrpheusModel(
            model_name="canopylabs/orpheus-tts-0.1-finetune-prod",
            max_model_len=2048,
        )
        _model_device = device
        log.info("✓ Orpheus model loaded")
        return _model
    except Exception as exc:
        raise RuntimeError(f"Failed to load Orpheus TTS model: {exc}") from exc


def unload_orpheus_model() -> None:
    """Free VRAM by unloading the model."""
    global _model
    if _model is not None:
        try:
            import torch

            del _model
            torch.cuda.empty_cache()
            _model = None
            log.info("✓ Orpheus model unloaded, VRAM freed")
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to fully unload Orpheus: %s", exc)


def inject_emotion_tags(script: str, emotions: list[str] | None = None) -> str:
    """
    Naturally inject emotion tags into script at pause points.

    Emotions: ["laugh", "chuckle", "sigh", "cough", "sniff", "groan", "yawn", "gasp"]

    Only injects 1-2 tags per script for naturalness. Places them at commas or
    end of clauses where a speaker would naturally take a beat.
    """
    if not emotions or not script:
        return script

    # Find natural pause points (commas, periods after short phrases)
    sentences = script.split(".")
    if not sentences:
        return script

    result_parts = []
    emotion_idx = 0
    injected = 0
    max_injections = min(2, max(1, len(emotions)))  # Max 2 emotions per script

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence:
            result_parts.append("")
            continue

        # Try to inject emotion at end of this sentence (before period)
        if injected < max_injections and emotion_idx < len(emotions):
            # Find a good place: after comma if exists, else at end
            if "," in sentence:
                parts = sentence.rsplit(",", 1)
                sentence = f'{parts[0]}<{emotions[emotion_idx]}>, {parts[1]}'
            else:
                # Add before the final phrase if possible
                words = sentence.split()
                if len(words) > 3:
                    sentence = f"{" ".join(words[:-1])}<{emotions[emotion_idx]}> {words[-1]}"

            injected += 1
            emotion_idx = (emotion_idx + 1) % len(emotions)

        result_parts.append(sentence)

    return ". ".join(p for p in result_parts if p) + ("." if script.rstrip().endswith(".") else "")


def synthesize_orpheus(
    text: str,
    voice: str,
    out_path: Path,
    emotions: list[str] | None = None,
) -> int:
    """
    Synthesize speech with Orpheus TTS. Returns duration_ms.

    Args:
        text: Script to synthesize
        voice: One of: tara, leah, jess, leo, dan, mia, zac, zoe
        out_path: Output WAV file path
        emotions: Optional list of emotion tags to inject

    Emotions (pick 0-2): laugh, chuckle, sigh, cough, sniff, groan, yawn, gasp
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text for Orpheus TTS")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Validate voice
    valid_voices = {"tara", "leah", "jess", "leo", "dan", "mia", "zac", "zoe"}
    if voice not in valid_voices:
        log.warning(
            "  [orpheus] unknown voice '%s'; falling back to 'leo'",
            voice,
        )
        voice = "leo"

    # Inject emotions naturally
    if emotions:
        text = inject_emotion_tags(text, emotions)

    try:
        model = get_orpheus_model()

        # Orpheus format: "voice_name: text"
        prompt = f"{voice}: {text}"
        log.info("  [orpheus] Synthesizing (%s, %d chars)…", voice, len(text))

        # Generate speech as audio chunks
        audio_chunks = []
        for audio_chunk in model.generate_speech(prompt=prompt):
            # Convert to numpy if needed
            if hasattr(audio_chunk, "numpy"):
                audio_chunk = audio_chunk.numpy()
            audio_chunks.append(np.asarray(audio_chunk, dtype=np.float32))

        if not audio_chunks:
            raise RuntimeError("Orpheus produced no audio")

        # Concatenate chunks
        audio_out = np.concatenate(audio_chunks)

        # Normalize if needed
        peak = float(np.max(np.abs(audio_out))) or 1.0
        if peak > 1.0:
            audio_out = audio_out / peak

        # Write to file
        sf.write(str(out_path), audio_out, SAMPLE_RATE)
        duration_ms = int(round(len(audio_out) / SAMPLE_RATE * 1000))
        log.info("  [orpheus] ✓ %d ms written to %s", duration_ms, out_path.name)
        return duration_ms

    except Exception as exc:
        log.error("  [orpheus] synthesis failed: %s", exc)
        raise RuntimeError(f"Orpheus TTS failed: {exc}") from exc


def write_silence_wav(out_path: Path, duration_sec: float = 1.0) -> int:
    """Fallback silence when TTS fails."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = int(SAMPLE_RATE * duration_sec)
    sf.write(str(out_path), np.zeros(n, dtype=np.float32), SAMPLE_RATE)
    return int(duration_sec * 1000)
