"""SD-Turbo album backgrounds — small distilled SD (~0.9B), no text LLM.

Text (title/artist) is still drawn with Pillow so lettering stays readable.
Pipeline is lazy-loaded and can be unloaded to free VRAM for ACE-Step.
Weights auto-download via huggingface_hub on app start when configured.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL_ID = "stabilityai/sd-turbo"
_lock = threading.Lock()
_pipe: Any = None
_device: str | None = None
_weights_ready: bool = False
_weights_path: str | None = None


@dataclass
class CoverModelState:
    """Shared status for boot download / health UI."""

    status: str = "idle"  # idle | checking | downloading | ready | error | skipped
    model: str = _MODEL_ID
    detail: str = ""
    error: str | None = None
    path: str | None = None
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "model": self.model,
            "detail": self.detail,
            "error": self.error,
            "path": self.path,
            "updated_at": self.updated_at,
        }


_cover_state = CoverModelState()


def cover_model_status() -> dict[str, Any]:
    return _cover_state.snapshot()


def _set_cover_state(
    status: str,
    *,
    detail: str = "",
    error: str | None = None,
    path: str | None = None,
) -> None:
    _cover_state.status = status
    _cover_state.detail = detail
    _cover_state.error = error
    if path is not None:
        _cover_state.path = path
    _cover_state.updated_at = time.time()

# Genre → short visual cue (no LLM needed)
_GENRE_LOOK: dict[str, str] = {
    "metal": "dark heavy metal aesthetic, crushed blacks, steel and crimson light",
    "melodic_progressive_metal": (
        "progressive metal album cover mood, cosmic darkness, blue-violet nebula, "
        "polished chrome geometry, dramatic stage light"
    ),
    "rock": "classic rock album art, warm amber stage lights, grain, raw energy",
    "indie": "indie album cover, muted film photography, soft grain, melancholic color",
    "electronic": "electronic music cover, neon grids, cyan magenta glow, night city",
    "pop": "glossy pop album cover, candy colors, soft bokeh, clean modern light",
    "jazz": "jazz album cover, smoky club, gold and deep blue, elegant abstract",
    "blues": "blues album mood, dusty road night, deep indigo and amber",
    "hiphop": "hip-hop cover energy, urban night, bold contrast, street texture",
    "classical": "classical album cover, marble and ink, restrained gold, quiet grandeur",
    "folk": "folk album cover, woodland dusk, warm earth tones, soft light",
    "country": "country album cover, open sky dusk, warm dust and wood tones",
    "reggae": "reggae album vibe, sun haze, green gold red accents, tropical dusk",
    "latin": "latin music cover energy, warm sunset, vivid but elegant color",
    "gospel": "gospel album cover, soft radiant light, hopeful sky, warm gold",
    "rnb": "r&b album cover, velvet night, magenta and deep purple, intimate glow",
}


def sd_turbo_available() -> bool:
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def ensure_sd_turbo_weights(*, force: bool = False) -> dict[str, Any]:
    """
    Download SD-Turbo weights into the Hugging Face cache if missing.

    Does **not** load the pipeline onto the GPU — only ensures files are local
    so the first cover gen (and offline restarts) work. Safe to call at boot.
    """
    global _weights_ready, _weights_path

    if _weights_ready and _weights_path and not force:
        _set_cover_state(
            "ready",
            detail=f"Weights ready: {_MODEL_ID}",
            path=_weights_path,
        )
        return cover_model_status()

    if not sd_turbo_available():
        _set_cover_state(
            "error",
            detail="diffusers/torch not installed",
            error="pip install -e '.[cover]'",
        )
        return cover_model_status()

    _set_cover_state("checking", detail=f"Checking cache for {_MODEL_ID}…")
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:  # noqa: BLE001
        _set_cover_state(
            "error",
            detail="huggingface_hub missing",
            error=str(exc),
        )
        return cover_model_status()

    # Prefer cache hit without network when possible
    try:
        if not force:
            path = snapshot_download(
                repo_id=_MODEL_ID,
                local_files_only=True,
            )
            _weights_ready = True
            _weights_path = path
            _set_cover_state(
                "ready",
                detail=f"Already cached: {_MODEL_ID}",
                path=path,
            )
            log.info("SD-Turbo weights already on disk: %s", path)
            return cover_model_status()
    except Exception:  # noqa: BLE001
        pass

    _set_cover_state(
        "downloading",
        detail=f"Downloading {_MODEL_ID} (first run ~3–5 GB)…",
    )
    log.info("Downloading SD-Turbo weights (%s)…", _MODEL_ID)
    try:
        path = snapshot_download(repo_id=_MODEL_ID)
        _weights_ready = True
        _weights_path = path
        _set_cover_state(
            "ready",
            detail=f"Downloaded: {_MODEL_ID}",
            path=path,
        )
        log.info("SD-Turbo weights ready: %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("SD-Turbo download failed: %s", exc)
        _set_cover_state(
            "error",
            detail="Download failed",
            error=str(exc),
        )
    return cover_model_status()


def unload_sd_turbo() -> None:
    """Free GPU memory held by the cover pipeline."""
    global _pipe, _device
    with _lock:
        if _pipe is None:
            return
        try:
            import torch

            del _pipe
            _pipe = None
            _device = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("SD-Turbo cover pipeline unloaded")
        except Exception as exc:  # noqa: BLE001
            log.warning("SD-Turbo unload: %s", exc)
            _pipe = None
            _device = None


def _get_pipe():
    global _pipe, _device, _weights_ready, _weights_path
    with _lock:
        if _pipe is not None:
            return _pipe, _device
        # Ensure weights exist (download if needed) before pipeline load
        if not _weights_ready:
            # Release lock during download to avoid deadlocks with ensure
            pass
    # Download outside the pipe lock so concurrent status reads work
    ensure_sd_turbo_weights()
    with _lock:
        if _pipe is not None:
            return _pipe, _device
        import torch
        from diffusers import AutoPipelineForText2Image

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device == "cuda" else torch.float32
        log.info("Loading SD-Turbo (%s) on %s…", _MODEL_ID, device)
        local = _weights_path
        try:
            kwargs: dict[str, Any] = {
                "torch_dtype": dtype,
            }
            if device == "cuda":
                kwargs["variant"] = "fp16"
            if local:
                kwargs["local_files_only"] = True
                pipe = AutoPipelineForText2Image.from_pretrained(local, **kwargs)
            else:
                pipe = AutoPipelineForText2Image.from_pretrained(_MODEL_ID, **kwargs)
        except Exception:
            # Some installs lack fp16 variant shards — fall back to default weights
            pipe = AutoPipelineForText2Image.from_pretrained(
                local or _MODEL_ID,
                torch_dtype=dtype,
                local_files_only=bool(local),
            )
        pipe.to(device)
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:  # noqa: BLE001
            pass
        _pipe = pipe
        _device = device
        _weights_ready = True
        log.info("SD-Turbo ready on %s", device)
        return _pipe, _device


def cover_prompt(
    *,
    title: str,
    artist: str,
    genre_id: str | None,
) -> str:
    look = _GENRE_LOOK.get(
        (genre_id or "").strip(),
        "modern album cover, moody abstract atmosphere, rich color grade",
    )
    # Do NOT ask the model to write the title — we overlay text later
    return (
        f"square album cover art, {look}, "
        f"inspired by the mood of a song called {(title or 'Untitled').strip()[:60]} "
        f"by {(artist or 'Unknown').strip()[:40]}, "
        "no text, no letters, no logo, no watermark, no typography, "
        "professional music packaging, high detail, centered composition"
    )


def generate_sd_background(
    *,
    title: str,
    artist: str,
    genre_id: str | None = None,
    seed: str | None = None,
    size: int = 512,
    steps: int = 2,
) -> "object":
    """Return a PIL RGB image (background only). Raises on failure."""
    import torch
    from PIL import Image

    pipe, device = _get_pipe()
    prompt = cover_prompt(title=title, artist=artist, genre_id=genre_id)
    # Deterministic-ish seed from segment id
    g = None
    if seed:
        import hashlib

        h = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
        g = torch.Generator(device=device).manual_seed(h % (2**31 - 1))

    steps = max(1, min(4, int(steps)))
    # SD-Turbo: guidance_scale=0.0 recommended
    result = pipe(
        prompt=prompt,
        num_inference_steps=steps,
        guidance_scale=0.0,
        width=size,
        height=size,
        generator=g,
    )
    img = result.images[0]
    if not isinstance(img, Image.Image):
        raise RuntimeError("SD-Turbo returned non-image")
    return img.convert("RGB")
