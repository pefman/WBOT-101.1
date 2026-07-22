from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Resolved model when station says "auto"
_resolved_model: str | None = None


def llm_unload_enabled() -> bool:
    """Unload Ollama from VRAM before ACE music (default on)."""
    return os.environ.get("AIRADIO_LLM_UNLOAD", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


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


async def list_running_models(base_url: str, *, timeout: float = 5.0) -> list[str]:
    """Models currently loaded in VRAM (Ollama /api/ps). Empty if unsupported."""
    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{base}/api/ps")
            if r.status_code != 200:
                return []
            names: list[str] = []
            for m in r.json().get("models") or []:
                n = m.get("name") or m.get("model")
                if n:
                    names.append(str(n))
            return names
    except Exception as exc:  # noqa: BLE001
        log.debug("list_running_models failed: %s", exc)
        return []


async def unload_model(
    base_url: str,
    model: str | None = None,
    *,
    timeout: float = 60.0,
) -> None:
    """
    Free VRAM by unloading Ollama model(s).

    Uses POST /api/generate with keep_alive=0 (Ollama). Safe no-op if the
    server is OpenAI-only or already empty.
    """
    if not llm_unload_enabled():
        return

    base = base_url.rstrip("/")
    targets: list[str] = []
    if model and str(model).strip().lower() not in ("", "auto", "default", "*"):
        targets.append(str(model).strip())
    # Always try to unload whatever is actually resident
    for name in await list_running_models(base, timeout=min(timeout, 10.0)):
        if name not in targets:
            targets.append(name)

    if not targets:
        # Still poke configured/auto model — keep_alive 0 is harmless if absent
        resolved = await resolve_model(base, model or "auto")
        if resolved and resolved.lower() not in ("", "auto"):
            targets.append(resolved)

    if not targets:
        log.info("  [llm] unload: nothing loaded")
        return

    async with httpx.AsyncClient(timeout=timeout) as client:
        for name in targets:
            try:
                # Official unload: zero keep_alive, no generation work required
                r = await client.post(
                    f"{base}/api/generate",
                    json={"model": name, "keep_alive": 0, "stream": False},
                )
                if r.status_code >= 400:
                    # Fallback chat endpoint
                    r2 = await client.post(
                        f"{base}/api/chat",
                        json={
                            "model": name,
                            "messages": [],
                            "keep_alive": 0,
                            "stream": False,
                        },
                    )
                    if r2.status_code >= 400:
                        log.warning(
                            "  [llm] unload %s failed: generate=%s chat=%s",
                            name,
                            r.status_code,
                            r2.status_code,
                        )
                        continue
                log.info("  [llm] unloaded «%s» (VRAM free for ACE)", name)
            except Exception as exc:  # noqa: BLE001
                log.warning("  [llm] unload %s error: %s", name, exc)


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


def _default_num_gpu() -> int | None:
    """
    None = let Ollama place layers on GPU (fast text while model is loaded).
    Set OLLAMA_NUM_GPU=0 to force CPU layers even when model is resident.
    """
    raw = os.environ.get("OLLAMA_NUM_GPU")
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def ollama_chat(
    base_url: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float = 180.0,
    num_gpu: int | None = None,
    temperature: float = 0.95,
    max_tokens: int = 220,
    keep_alive: str | int | None = "10m",
) -> str:
    """Chat with a local LLM (OpenAI-compat or Ollama native).

    Loads the model into VRAM on first use. Prefer unload_model() before ACE
    so music can use the GPU. keep_alive keeps weights warm between talk/lyrics.
    """
    base = base_url.rstrip("/")
    resolved = await resolve_model(base, model)
    if resolved.lower() in ("", "auto", "default", "*"):
        raise RuntimeError(
            f"No chat models available at {base}. "
            "Load a model (ollama pull … or start llama-server with a GGUF)."
        )

    if num_gpu is None:
        num_gpu = _default_num_gpu()

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    log.info("  [llm] chat model=%s (loads into VRAM if needed)…", resolved)
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
                keep_alive=keep_alive,
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
    keep_alive: str | int | None = "10m",
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
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

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
