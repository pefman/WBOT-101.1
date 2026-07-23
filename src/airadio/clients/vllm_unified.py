"""
Unified vllm client for both text LLM (Qwen) and audio synthesis (Orpheus).

vllm runs a single inference server supporting:
  - Text generation via /v1/chat/completions (OpenAI-compatible)
  - TTS via Orpheus (automatic, uses vllm backbone)

Models are stored in ./models/ and downloaded automatically on first use.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Resolved model when station says "auto"
_resolved_model: str | None = None


def llm_unload_enabled() -> bool:
    """Unload vLLM from VRAM before ACE music (default on)."""
    return os.environ.get("AIRADIO_LLM_UNLOAD", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


async def ensure_vllm_running(
    base_url: str = "http://127.0.0.1:8000",
    model: str = "Qwen/Qwen2.5-7B-Instruct",
    *,
    timeout: float = 600.0,
) -> bool:
    """
    Check if vLLM is running on base_url; start it if not.
    
    Returns True if vLLM is ready. Raises exception if startup fails.
    This is called on-demand by produce_talk() before LLM generation.
    """
    base = base_url.rstrip("/")
    
    # Check if already running
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{base}/v1/models")
            if r.status_code == 200:
                log.info("  [vllm] already running on %s", base)
                return True
    except Exception:  # noqa: BLE001
        pass
    
    # Not running — start it
    log.info("  [vllm] starting process (model=%s)…", model)
    hf_home = os.environ.get("HF_HOME", str(Path.cwd() / "models" / "huggingface"))
    
    try:
        proc = subprocess.Popen(
            [
                "python",
                "-m",
                "vllm.entrypoints.openai.api_server",
                f"--model={model}",
                "--tensor-parallel-size=1",
                "--gpu-memory-utilization=0.8",
                "--port=8000",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "HF_HOME": hf_home},
            start_new_session=True,  # Detach from parent so it survives shell exit
        )
        log.info("  [vllm] process started (PID %s), waiting for readiness…", proc.pid)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to start vLLM process: {exc}") from exc
    
    # Poll for readiness
    start_time = asyncio.get_event_loop().time()
    poll_interval = 5.0
    last_status_time = start_time
    
    while asyncio.get_event_loop().time() - start_time < timeout:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{base}/v1/models")
                if r.status_code == 200:
                    log.info("  [vllm] ready on %s", base)
                    return True
        except Exception:  # noqa: BLE001
            pass
        
        # Status update every 30 seconds
        now = asyncio.get_event_loop().time()
        if now - last_status_time > 30:
            elapsed = int(now - start_time)
            log.info(f"  [vllm] still waiting for readiness (elapsed {elapsed}s)…")
            last_status_time = now
        
        await asyncio.sleep(poll_interval)
    
    raise TimeoutError(
        f"vLLM did not start within {timeout:.0f}s on {base}. "
        "Check GPU driver, VRAM availability, or model download progress."
    )


async def list_models(base_url: str, *, timeout: float = 5.0) -> list[str]:
    """List model ids from vLLM /v1/models endpoint."""
    base = base_url.rstrip("/")
    ids: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base}/v1/models")
            if r.status_code == 200:
                data = r.json()
                for m in data.get("data") or []:
                    mid = m.get("id") or m.get("model")
                    if mid:
                        ids.append(str(mid))
    except Exception as exc:  # noqa: BLE001
        log.debug("v1/models failed: %s", exc)
    return ids


async def resolve_model(base_url: str, configured: str) -> str:
    """If configured is auto/empty or missing, pick first available model."""
    global _resolved_model
    configured = (configured or "auto").strip()
    want_auto = configured.lower() in ("", "auto", "default", "*")

    ids = await list_models(base_url)
    if not ids:
        return configured if not want_auto else "auto"

    if not want_auto:
        if configured in ids or any(configured in i or i in configured for i in ids):
            _resolved_model = configured
            return configured
        log.warning(
            "Configured model %r not in server list %s — using %s",
            configured,
            ids[:5],
            ids[0],
        )

    if _resolved_model and _resolved_model in ids:
        return _resolved_model
    _resolved_model = ids[0]
    log.info("Text LLM model resolved to %s", _resolved_model)
    return _resolved_model


async def vllm_generate_text(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 180.0,
    temperature: float = 0.95,
    max_tokens: int = 220,
) -> str:
    """
    Generate text using vLLM text model (e.g., Qwen 2.5:7b-instruct).

    vLLM automatically manages VRAM; model stays resident until explicitly unloaded.
    Use unload_vllm_model() before ACE-Step music generation to free GPU.
    """
    base = base_url.rstrip("/")
    resolved = await resolve_model(base, model)
    if resolved.lower() in ("", "auto", "default", "*"):
        raise RuntimeError(
            f"No text models available at {base}. "
            "Start vLLM: vllm serve --model qwen2.5-7b-instruct "
            "--tensor-parallel-size 1 --gpu-memory-utilization 0.8"
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    log.info("  [vllm] text generation model=%s…", resolved)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            payload: dict[str, Any] = {
                "model": resolved,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            resp = await client.post(f"{base}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("vLLM returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content or not str(content).strip():
            raise RuntimeError(f"vLLM returned empty content: {data!r}"[:500])
        return str(content).strip()
    except Exception as exc:
        global _resolved_model
        _resolved_model = None
        raise RuntimeError(f"vLLM text generation failed: {exc}") from exc


async def unload_vllm_model(base_url: str, *, timeout: float = 10.0) -> None:
    """
    Kill vLLM process to free GPU VRAM before ACE-Step music generation.
    
    Since vLLM has no API to unload models, we terminate the process entirely.
    The model will be reloaded from cache on next generation (fast).
    """
    if not llm_unload_enabled():
        return

    import subprocess
    try:
        # Find vLLM process by port and kill it
        result = subprocess.run(
            ["lsof", "-ti:8000"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().split("\n")
        for pid_str in pids:
            if pid_str and pid_str.isdigit():
                pid = int(pid_str)
                try:
                    subprocess.run(["kill", str(pid)], timeout=2)
                    log.info(f"  [vllm] terminated process {pid} to free GPU VRAM")
                except Exception as e:  # noqa: BLE001
                    log.warning(f"  [vllm] failed to kill {pid}: {e}")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"  [vllm] unload check failed: {exc} (GPU VRAM may not be freed)")


async def check_vllm(base_url: str, text_model: str, *, timeout: float = 5.0) -> dict:
    """Return {ok, detail, model?, available?, mode?} for health check."""
    base = base_url.rstrip("/")
    try:
        ids = await list_models(base, timeout=timeout)
        if not ids:
            return {
                "ok": False,
                "detail": f"vLLM at {base} has no loaded models — "
                "Start vLLM with: vllm serve --model qwen2.5-7b-instruct",
                "mode": "error",
            }
        resolved = await resolve_model(base, text_model)
        return {
            "ok": True,
            "detail": f"vLLM OK · text_model={resolved} · {len(ids)} model(s) available",
            "model": resolved,
            "available": ids[:8],
            "mode": "live",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "detail": f"vLLM unreachable at {base}: {exc}",
            "mode": "error",
        }
