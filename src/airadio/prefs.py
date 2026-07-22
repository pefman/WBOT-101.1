"""Persist station desk settings across restarts (data/prefs.json)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PREFS_VERSION = 1


def prefs_path(data_dir: Path) -> Path:
    return Path(data_dir) / "prefs.json"


def load_prefs(data_dir: Path) -> dict[str, Any]:
    path = prefs_path(data_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read prefs %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def save_prefs(data_dir: Path, prefs: dict[str, Any]) -> Path:
    path = prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": PREFS_VERSION, **prefs}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def merge_prefs(data_dir: Path, **updates: Any) -> dict[str, Any]:
    """Load existing prefs, apply non-None updates, save, return full dict."""
    current = load_prefs(data_dir)
    for key, value in updates.items():
        if value is None:
            continue
        current[key] = value
    current.pop("version", None)
    save_prefs(data_dir, current)
    return current
