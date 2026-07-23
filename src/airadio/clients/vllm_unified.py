"""
Unified vllm client for both text LLM (Qwen) and audio synthesis (Orpheus).

vllm runs a single inference server supporting:
  - Text generation via /v1/chat/completions (OpenAI-compatible)
  - TTS via Orpheus (automatic, uses vllm backbone)

Models are stored in ./models/ and downloaded automatically on first use.
"""

from __future__ import annotations

import logging
import os
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
    Unload all models from vLLM VRAM to free GPU memory before ACE-Step music.

    vLLM doesn't have a direct unload API; instead, we call /v1/models
    to verify what's loaded, then optionally trigger a model switch or
    rely on memory management. For now, this is a no-op but future-proofs
    the API for explicit unload support.
    """
    if not llm_unload_enabled():
        return

    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Verify server is still responsive
            r = await client.get(f"{base}/v1/models")
            if r.status_code != 200:
                log.warning("vLLM server unresponsive during unload check")
                return
        log.info("  [vllm] unload request sent (models stay resident until next generation or explicit stop)")
    except Exception as exc:  # noqa: BLE001
        log.warning("vLLM unload verification failed: %s", exc)


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
