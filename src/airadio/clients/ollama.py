from __future__ import annotations

from typing import Any

import httpx


async def ollama_chat(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 120.0,
    num_gpu: int | None = 0,
) -> str:
    """Chat with a local Ollama model. Prefer CPU (num_gpu=0) while GPU runs music."""
    url = f"{base_url.rstrip('/')}/api/chat"
    options: dict[str, Any] = {}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if options:
        payload["options"] = options

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    message = data.get("message") or {}
    content = message.get("content")
    if not content or not str(content).strip():
        raise RuntimeError("Ollama returned empty content")
    return str(content).strip()


async def check_ollama(base_url: str, model: str, *, timeout: float = 5.0) -> dict:
    """Return {ok: bool, detail: str}."""
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            tags = await client.get(f"{base}/api/tags")
            tags.raise_for_status()
            models = [m.get("name") for m in (tags.json().get("models") or [])]
            if model not in models and not any(
                (m or "").startswith(model.split(":")[0]) for m in models
            ):
                return {
                    "ok": False,
                    "detail": f"model '{model}' not found; available={models[:8]}",
                }
            return {"ok": True, "detail": f"ollama reachable; model ok ({model})"}
    except Exception as exc:  # noqa: BLE001 — health must never raise
        return {"ok": False, "detail": f"ollama unreachable: {exc}"}
