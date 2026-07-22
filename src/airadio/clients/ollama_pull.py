"""
Ensure an Ollama chat model is present; pull with live progress if missing.

Uses Ollama's native API:
  POST {base}/api/pull  {"name": "...", "stream": true}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Preferred model when config is "auto" and nothing is installed yet
DEFAULT_PULL_MODEL = "qwen2.5:7b"


@dataclass
class PullState:
    status: str = "idle"  # idle | checking | pulling | ready | error
    model: str = ""
    detail: str = ""
    completed: int = 0
    total: int = 0
    percent: float | None = None
    layer: str = ""
    error: str | None = None
    updated_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "model": self.model,
            "detail": self.detail,
            "completed": self.completed,
            "total": self.total,
            "percent": self.percent,
            "layer": self.layer,
            "error": self.error,
            "updated_at": self.updated_at,
        }


class OllamaModelManager:
    def __init__(self) -> None:
        self.state = PullState()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return self.state.snapshot()

    async def list_tags(self, base_url: str, *, timeout: float = 5.0) -> list[str]:
        base = base_url.rstrip("/")
        names: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Prefer native Ollama tags (accurate for pulls)
                r = await client.get(f"{base}/api/tags")
                if r.status_code == 200:
                    for m in r.json().get("models") or []:
                        n = m.get("name")
                        if n:
                            names.append(str(n))
                    return names
                # Fallback OpenAI list
                r2 = await client.get(f"{base}/v1/models")
                if r2.status_code == 200:
                    data = r2.json()
                    for m in data.get("data") or data.get("models") or []:
                        mid = m.get("id") or m.get("name")
                        if mid:
                            names.append(str(mid))
        except Exception as exc:  # noqa: BLE001
            log.debug("list_tags failed: %s", exc)
        return names

    def _model_present(self, names: list[str], wanted: str) -> bool:
        if not wanted:
            return bool(names)
        if wanted in names:
            return True
        # ollama often returns "qwen2.5:7b" vs "qwen2.5:7b-instruct"
        base = wanted.split(":")[0]
        return any(n == wanted or n.startswith(wanted) or n.startswith(base + ":") for n in names)

    def resolve_wanted_model(self, configured: str, available: list[str]) -> str:
        cfg = (configured or "auto").strip()
        if cfg.lower() not in ("", "auto", "default", "*"):
            return cfg
        if available:
            return available[0]
        return DEFAULT_PULL_MODEL

    async def ensure_model(self, base_url: str, configured: str) -> dict[str, Any]:
        """
        If model missing, start background pull (idempotent).
        Returns current status snapshot.
        """
        async with self._lock:
            if self._task and not self._task.done():
                return self.status()

            self.state = PullState(status="checking", detail="Checking Ollama for models…")
            try:
                names = await self.list_tags(base_url)
            except Exception as exc:  # noqa: BLE001
                self.state.status = "error"
                self.state.error = str(exc)
                self.state.detail = f"Cannot reach Ollama at {base_url}: {exc}"
                self.state.updated_at = time.time()
                return self.status()

            wanted = self.resolve_wanted_model(configured, names)
            self.state.model = wanted

            if self._model_present(names, wanted if configured.lower() not in ("", "auto", "default", "*") else (names[0] if names else wanted)):
                # If auto and models exist, ready with first model
                if configured.lower() in ("", "auto", "default", "*") and names:
                    self.state.model = names[0]
                self.state.status = "ready"
                self.state.detail = f"Model ready: {self.state.model}"
                self.state.percent = 100.0
                self.state.updated_at = time.time()
                return self.status()

            # Need pull
            self.state.status = "pulling"
            self.state.detail = f"Starting download of {wanted}…"
            self.state.percent = 0.0
            self.state.updated_at = time.time()
            self._task = asyncio.create_task(
                self._pull_stream(base_url, wanted), name=f"ollama-pull-{wanted}"
            )
            return self.status()

    async def _pull_stream(self, base_url: str, model: str) -> None:
        base = base_url.rstrip("/")
        url = f"{base}/api/pull"
        self.state.model = model
        self.state.status = "pulling"
        self.state.error = None
        log.info("Ollama pull starting: %s", model)

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    url,
                    json={"name": model, "stream": True},
                ) as resp:
                    if resp.status_code >= 400:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise RuntimeError(f"pull HTTP {resp.status_code}: {body[:500]}")

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        self._apply_event(ev)

            # verify
            names = await self.list_tags(base_url, timeout=10.0)
            if self._model_present(names, model):
                self.state.status = "ready"
                self.state.detail = f"Model ready: {model}"
                self.state.percent = 100.0
                self.state.error = None
                log.info("Ollama pull complete: %s", model)
            else:
                self.state.status = "error"
                self.state.error = "Pull finished but model not listed"
                self.state.detail = self.state.error
        except Exception as exc:  # noqa: BLE001
            log.exception("Ollama pull failed: %s", exc)
            self.state.status = "error"
            self.state.error = str(exc)
            self.state.detail = f"Download failed: {exc}"
        finally:
            self.state.updated_at = time.time()

    def _apply_event(self, ev: dict[str, Any]) -> None:
        status = str(ev.get("status") or "")
        self.state.detail = status or self.state.detail
        digest = str(ev.get("digest") or "")
        if digest:
            self.state.layer = digest[:16]

        total = ev.get("total")
        completed = ev.get("completed")
        if isinstance(total, (int, float)) and total > 0 and isinstance(completed, (int, float)):
            self.state.total = int(total)
            self.state.completed = int(completed)
            self.state.percent = round(100.0 * float(completed) / float(total), 1)
            self.state.detail = f"{status} · {self.state.percent}%"
        elif status:
            # non-byte phases: pulling manifest, verifying, etc.
            self.state.detail = status

        if status == "success":
            self.state.percent = 100.0
            self.state.detail = "success"

        self.state.updated_at = time.time()


# Process-wide manager
manager = OllamaModelManager()
