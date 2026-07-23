from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
import soundfile as sf

log = logging.getLogger(__name__)

DEFAULT_API_URL = "http://127.0.0.1:8001"


def _api_base(url: str | None = None) -> str:
    return (url or os.environ.get("ACESTEP_API_URL") or DEFAULT_API_URL).rstrip("/")


def acestep_available() -> tuple[bool, str]:
    """Require a live ACE-Step REST API (real music only)."""
    base = _api_base()
    try:
        r = httpx.get(f"{base}/health", timeout=2.0)
        if r.status_code == 200:
            return True, f"ACE-Step API at {base}"
    except Exception:  # noqa: BLE001
        pass

    vendor = Path(__file__).resolve().parents[3] / "vendor" / "ACE-Step-1.5"
    if vendor.is_dir():
        return (
            False,
            f"ACE-Step cloned at {vendor} but API not running — "
            "run: bash scripts/start_acestep_api.sh",
        )

    return (
        False,
        "ACE-Step API not ready. Install: bash scripts/install_acestep.sh "
        "then: bash scripts/start_acestep_api.sh",
    )


async def _api_reachable(base: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{base}/health")
            return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def ensure_acestep_running() -> bool:
    """
    Ensure ACE-Step API is running on 8001.
    If not, attempt to restart it (non-fatal if it fails).
    Returns True if running, False otherwise.
    """
    base = _api_base()
    
    # Check if already running
    if await _api_reachable(base):
        return True
    
    log.warning("  [acestep] API not responding — attempting to restart…")
    
    # Try to start ACE-Step using the start script
    try:
        root = Path(__file__).resolve().parents[3]
        start_script = root / "scripts" / "start_acestep_api.sh"
        
        if not start_script.exists():
            log.warning("  [acestep] start script not found at %s", start_script)
            return False
        
        # Run start script in background
        proc = subprocess.Popen(
            ["bash", str(start_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        
        log.info("  [acestep] start script spawned (PID %s), waiting for readiness…", proc.pid)
        
        # Wait for API to become reachable (up to 30s)
        for attempt in range(60):
            await asyncio.sleep(0.5)
            if await _api_reachable(base):
                log.info("  [acestep] API is now ready on %s", base)
                return True
        
        log.warning("  [acestep] API did not respond within 30s (check data/acestep-api.log)")
        return False
        
    except Exception as exc:  # noqa: BLE001
        log.warning("  [acestep] restart failed: %s", exc)
        return False


def _thinking_default() -> bool:
    # Default OFF: ACE "thinking" loads a second GPU model (5Hz LM) and often OOMs
    # when DiT already holds VRAM. Set ACESTEP_THINKING=1 if you have free VRAM.
    return os.environ.get("ACESTEP_THINKING", "false").lower() in (
        "1",
        "true",
        "yes",
    )


def _format_task_failure(item: dict) -> str:
    """Pull a readable reason out of ACE-Step failure payloads."""
    parts: list[str] = []
    for key in ("progress_text", "error", "message", "detail"):
        val = item.get(key)
        if val:
            parts.append(str(val))
    result = item.get("result")
    if isinstance(result, str) and result:
        try:
            parsed = json.loads(result)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if isinstance(parsed, dict):
                for key in ("error", "message", "progress_text", "stage"):
                    if parsed.get(key):
                        parts.append(str(parsed[key]))
        except json.JSONDecodeError:
            parts.append(result[:400])
    text = " | ".join(parts) if parts else repr(item)[:500]
    if "out of memory" in text.lower() or "oom" in text.lower():
        text += (
            " — GPU full. DiT-only mode (thinking=false) is safer; free VRAM "
            "(stop other GPU apps / keep vLLM on CPU) or set ACESTEP_THINKING=0."
        )
    return text[:900]


async def _generate_via_api(
    base: str,
    style: str,
    lyrics: str,
    duration_sec: int,
    out_path: Path,
    *,
    vocal_language: str = "en",
    thinking: bool | None = None,
) -> None:
    """
    ACE-Step 1.5 async REST flow:
      POST /release_task → task_id
      POST /query_result until status 1/2
      GET  /v1/audio?path=... → file
    """
    duration = max(10, min(int(duration_sec), 600))
    use_thinking = _thinking_default() if thinking is None else thinking
    vlang = (vocal_language or "en").strip().lower() or "en"
    # When thinking is off, also skip CoT helpers that need the 5Hz LM
    payload = {
        "prompt": style,
        "caption": style,
        "lyrics": lyrics or "",
        "vocal_language": vlang,
        "audio_duration": float(duration),
        "duration": float(duration),
        "audio_format": "wav",
        "thinking": use_thinking,
        "use_cot_caption": use_thinking,
        "use_cot_language": use_thinking,
        "use_format": False,
        "inference_steps": int(os.environ.get("ACESTEP_STEPS", "8")),
        "batch_size": 1,
        "use_random_seed": True,
        "task_type": "text2music",
        "model": os.environ.get("ACESTEP_DIT_MODEL", "acestep-v15-turbo"),
    }

    log.info(
        "  [ace]  submit job duration=%ss lang=%s thinking=%s prompt=%.80s…",
        duration,
        vlang,
        use_thinking,
        style,
    )

    timeout = httpx.Timeout(30.0, read=120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}/release_task", json=payload)
        r.raise_for_status()
        body = r.json()
        data = body.get("data") if isinstance(body, dict) else body
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected release_task response: {body!r}"[:500])
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task_id in response: {body!r}"[:500])

        # Poll for completion (music gen can take 10s–several minutes)
        deadline = time.time() + float(os.environ.get("ACESTEP_TIMEOUT_SEC", "600"))
        file_url: str | None = None
        while time.time() < deadline:
            qr = await client.post(
                f"{base}/query_result",
                json={"task_id_list": [task_id]},
            )
            qr.raise_for_status()
            qbody = qr.json()
            items = qbody.get("data") if isinstance(qbody, dict) else qbody
            if not items:
                await asyncio.sleep(1.5)
                continue
            item = items[0] if isinstance(items, list) else items
            status = item.get("status")
            # status may be int or string early on
            if status in (0, "0", "queued", "running") or status is None:
                # Log progress occasionally
                prog = item.get("progress") or item.get("progress_text")
                if prog and int(time.time()) % 15 == 0:
                    log.info("  [ace]  task %s progress: %s", task_id[:8], prog)
                await asyncio.sleep(1.5)
                continue
            if status in (2, "2", "failed"):
                raise RuntimeError(
                    f"ACE-Step task failed: {_format_task_failure(item)}"
                )
            if status in (1, "1", "succeeded"):
                file_url = _extract_file_url(item)
                break
            await asyncio.sleep(1.5)
        else:
            raise TimeoutError(f"ACE-Step task {task_id} timed out")

        if not file_url:
            raise RuntimeError(f"No audio file in ACE-Step result for {task_id}")

        await _download_audio(client, base, file_url, out_path)

    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise RuntimeError(f"ACE-Step did not write audio to {out_path}")
    log.info(
        "  [ace]  audio ready %s (%s bytes)",
        out_path.name,
        out_path.stat().st_size,
    )


async def generate_song(
    style: str,
    lyrics: str,
    duration_sec: int,
    out_path: Path,
    *,
    api_url: str | None = None,
    vocal_language: str = "en",
) -> None:
    """Generate a song via the ACE-Step 1.5 REST API (real music only)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    base = _api_base(api_url)
    if not await _api_reachable(base):
        raise RuntimeError(
            "ACE-Step API not reachable at "
            f"{base}. Install + start with:\n"
            "  bash scripts/install_acestep.sh\n"
            "  bash scripts/start_acestep_api.sh"
        )

    try:
        await _generate_via_api(
            base,
            style,
            lyrics,
            duration_sec,
            out_path,
            vocal_language=vocal_language or "en",
        )
    except RuntimeError as exc:
        msg = str(exc).lower()
        # If thinking/LM path OOMs or fails, retry pure DiT text2music once
        if _thinking_default() and (
            "out of memory" in msg
            or "oom" in msg
            or "5hz lm" in msg
            or "lm init" in msg
            or "thinking" in msg
        ):
            log.warning(
                "  [ace]  failed with thinking/LM (%s); retry DiT-only (thinking=false)…",
                str(exc)[:200],
            )
            await _generate_via_api(
                base,
                style,
                lyrics,
                duration_sec,
                out_path,
                vocal_language=vocal_language or "en",
                thinking=False,
            )
            return
        raise


def _extract_file_url(item: dict) -> str | None:
    result = item.get("result")
    if result is None:
        return item.get("file")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    if isinstance(result, list) and result:
        result = result[0]
    if isinstance(result, dict):
        return result.get("file") or result.get("path")
    return None


async def _download_audio(
    client: httpx.AsyncClient, base: str, file_ref: str, out_path: Path
) -> None:
    """Download API audio (path URL or absolute path) and normalize to WAV."""
    tmp = out_path.with_suffix(".download")

    if file_ref.startswith("/v1/audio") or "path=" in file_ref:
        url = file_ref if file_ref.startswith("http") else f"{base}{file_ref}"
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    elif file_ref.startswith("http"):
        async with client.stream("GET", file_ref) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
    else:
        # Absolute path on API server (same machine)
        src = Path(unquote(file_ref))
        if not src.is_file():
            # Try query param form path=
            parsed = urlparse(file_ref)
            raise RuntimeError(f"Audio path not found: {src} ({parsed})")
        tmp.write_bytes(src.read_bytes())

    # Convert to WAV if needed (API may return mp3)
    if tmp.suffix.lower() in {".wav", ".wave"} or _looks_like_wav(tmp):
        # Ensure standard wav via soundfile round-trip if possible
        try:
            data, sr = sf.read(str(tmp), always_2d=True)
            sf.write(str(out_path), data, sr)
            tmp.unlink(missing_ok=True)
            return
        except Exception:  # noqa: BLE001
            tmp.replace(out_path)
            return

    from airadio.paths import bundled_ffmpeg

    ff = bundled_ffmpeg()
    conv = subprocess.run(
        [ff, "-y", "-i", str(tmp), str(out_path.with_suffix(".wav"))],
        capture_output=True,
        text=True,
    )
    tmp.unlink(missing_ok=True)
    if conv.returncode != 0:
        raise RuntimeError(f"ffmpeg convert failed: {conv.stderr[-800:]}")
    final = out_path.with_suffix(".wav")
    if final != out_path:
        final.replace(out_path)


def _looks_like_wav(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"RIFF"
    except OSError:
        return False


