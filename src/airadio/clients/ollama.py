from __future__ import annotations

from typing import Any

import httpx


async def ollama_chat(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 180.0,
    num_gpu: int | None = 0,
) -> str:
    """
    Chat with a local LLM.

    Supports:
    - llama.cpp / OpenAI-compatible: POST {base}/v1/chat/completions
    - Ollama native: POST {base}/api/chat
    """
    base = base_url.rstrip("/")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # Prefer OpenAI-compatible (llama-server, vLLM, LocalAI, …)
    try:
        return await _openai_chat(base, model, messages, timeout=timeout)
    except Exception as openai_exc:
        try:
            return await _ollama_native_chat(
                base, model, messages, timeout=timeout, num_gpu=num_gpu
            )
        except Exception as ollama_exc:
            raise RuntimeError(
                f"Local LLM chat failed. OpenAI-compat: {openai_exc}; "
                f"Ollama: {ollama_exc}"
            ) from ollama_exc


async def _openai_chat(
    base: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float,
) -> str:
    url = f"{base}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.8,
        # Cap output so the radio buffer never stalls on long monologues
        "max_tokens": 400,
        # Qwen3 / reasoning models via llama.cpp — speak, don't think out loud
        "chat_template_kwargs": {"enable_thinking": False},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI-compat returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    # Some reasoning models put text in a separate field
    if (not content or not str(content).strip()) and message.get("reasoning_content"):
        content = message.get("reasoning_content")
    if not content or not str(content).strip():
        raise RuntimeError(f"OpenAI-compat returned empty content: {data!r}"[:500])
    return _strip_thinking(str(content).strip())


async def _ollama_native_chat(
    base: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout: float,
    num_gpu: int | None,
) -> str:
    url = f"{base}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
    }
    if num_gpu is not None:
        payload["options"] = {"num_gpu": num_gpu}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    message = data.get("message") or {}
    content = message.get("content")
    if not content or not str(content).strip():
        raise RuntimeError("Ollama returned empty content")
    return _strip_thinking(str(content).strip())


def _strip_thinking(text: str) -> str:
    """Remove Qwen-style <think>…</think> blocks if present."""
    import re

    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


async def check_ollama(base_url: str, model: str, *, timeout: float = 5.0) -> dict:
    """Return {ok: bool, detail: str}. Works with Ollama or OpenAI-compat servers."""
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # OpenAI-compatible / llama-server
            r = await client.get(f"{base}/v1/models")
            if r.status_code == 200:
                data = r.json()
                ids: list[str] = []
                for m in data.get("data") or data.get("models") or []:
                    mid = m.get("id") or m.get("name") or m.get("model")
                    if mid:
                        ids.append(str(mid))
                if not ids:
                    return {"ok": True, "detail": "OpenAI-compat server up (no model list)"}
                # Accept exact or prefix match (gguf filenames can be long)
                if model in ids or any(model in i or i in model for i in ids):
                    return {
                        "ok": True,
                        "detail": f"OpenAI-compat OK; model≈{model}",
                    }
                return {
                    "ok": True,
                    "detail": f"OpenAI-compat OK; using first available ({ids[0]}); "
                    f"config model={model}",
                    "available": ids[:5],
                }

            # Ollama native
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
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"LLM unreachable: {exc}"}
