from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Resolved model when station says "auto"
_resolved_model: str | None = None


async def list_models(base_url: str, *, timeout: float = 5.0) -> list[str]:
    """List model ids from OpenAI-compat or Ollama native API."""
    base = base_url.rstrip("/")
    ids: list[str] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(f"{base}/v1/models")
            if r.status_code == 200:
                data = r.json()
                for m in data.get("data") or data.get("models") or []:
                    mid = m.get("id") or m.get("name") or m.get("model")
                    if mid:
                        ids.append(str(mid))
                if ids:
                    return ids
        except Exception as exc:  # noqa: BLE001
            log.debug("v1/models failed: %s", exc)
        try:
            tags = await client.get(f"{base}/api/tags")
            if tags.status_code == 200:
                for m in tags.json().get("models") or []:
                    name = m.get("name")
                    if name:
                        ids.append(str(name))
        except Exception as exc:  # noqa: BLE001
            log.debug("api/tags failed: %s", exc)
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
    log.info("LLM model resolved to %s", _resolved_model)
    return _resolved_model


async def ollama_chat(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 180.0,
    num_gpu: int | None = 0,
    temperature: float = 0.95,
    max_tokens: int = 220,
) -> str:
    """Chat with a local LLM (OpenAI-compat or Ollama native)."""
    base = base_url.rstrip("/")
    resolved = await resolve_model(base, model)
    if resolved.lower() in ("", "auto", "default", "*"):
        raise RuntimeError(
            f"No chat models available at {base}. "
            "Load a model (ollama pull … or start llama-server with a GGUF)."
        )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    try:
        return await _openai_chat(
            base,
            resolved,
            messages,
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as openai_exc:
        try:
            return await _ollama_native_chat(
                base,
                resolved,
                messages,
                timeout=timeout,
                num_gpu=num_gpu,
                temperature=temperature,
            )
        except Exception as ollama_exc:
            global _resolved_model
            _resolved_model = None
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
    temperature: float = 0.95,
    max_tokens: int = 220,
) -> str:
    url = f"{base}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        "max_tokens": max_tokens,
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
    temperature: float = 0.95,
) -> str:
    url = f"{base}/api/chat"
    options: dict[str, Any] = {"temperature": temperature}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": options,
    }

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
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


async def check_ollama(base_url: str, model: str, *, timeout: float = 5.0) -> dict:
    """Return {ok, detail, model?, available?, mode?}."""
    base = base_url.rstrip("/")
    try:
        ids = await list_models(base, timeout=timeout)
        if not ids:
            return {
                "ok": False,
                "detail": f"server at {base} has no loaded chat models — "
                "Play blocked until a model is available",
                "mode": "error",
            }
        resolved = await resolve_model(base, model)
        return {
            "ok": True,
            "detail": f"chat OK · model={resolved}",
            "model": resolved,
            "available": ids[:8],
            "mode": "live",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "detail": f"LLM unreachable: {exc}",
            "mode": "error",
        }
