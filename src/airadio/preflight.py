"""
Runtime readiness checks before starting the radio.

Run:  python -m airadio.preflight
Exit: 0 = ready, 1 = something missing (messages printed).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def _repo_root() -> Path:
    # src/airadio/preflight.py → parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def _need_mod(mod: str, label: str | None = None) -> Check:
    label = label or mod
    try:
        m = import_module(mod)
        ver = getattr(m, "__version__", "ok")
        return Check(label, True, str(ver))
    except Exception as exc:  # noqa: BLE001
        return Check(
            label,
            False,
            str(exc),
            "From the project root, with venv active:\n"
            "  pip install -e '.[dev]'",
        )


def check_python() -> Check:
    v = sys.version_info
    ok = v >= (3, 11)
    return Check(
        "Python",
        ok,
        f"{v.major}.{v.minor}.{v.micro}",
        "Install Python 3.11 or newer (3.12 is fine).",
    )


def check_packages() -> list[Check]:
    return [
        _need_mod("airadio"),
        _need_mod("fastapi"),
        _need_mod("uvicorn"),
        _need_mod("orpheus_tts", "orpheus-tts"),
        _need_mod("imageio_ffmpeg", "imageio-ffmpeg"),
        _need_mod("torch"),
        _need_mod("soundfile"),
        _need_mod("httpx"),
        _need_mod("yaml", "pyyaml"),
    ]


def check_bundled_tools() -> list[Check]:
    out: list[Check] = []
    try:
        from airadio.paths import bundled_ffmpeg, ensure_bundled_espeak, static_web_dir

        ff = bundled_ffmpeg()
        out.append(Check("ffmpeg (bundled)", True, ff))
    except Exception as exc:  # noqa: BLE001
        out.append(
            Check(
                "ffmpeg (bundled)",
                False,
                str(exc),
                "pip install -e .  (pulls imageio-ffmpeg into .venv)",
            )
        )
        return out

    es = ensure_bundled_espeak()
    if es.get("library"):
        out.append(Check("espeak (bundled)", True, es["library"]))
    else:
        out.append(
            Check(
                "espeak (bundled)",
                False,
                "espeakng-loader missing or broken",
                "pip install -e .",
            )
        )

    web = static_web_dir()
    if (web / "index.html").is_file() and (web / "hls.min.js").is_file():
        out.append(Check("Web UI", True, str(web)))
    else:
        out.append(
            Check(
                "Web UI",
                False,
                f"missing static files under {web}",
                "Re-clone the repo or restore src/airadio/static/",
            )
        )
    return out


def check_config() -> Check:
    try:
        from airadio.config import load_station

        station, genres = load_station()
        return Check(
            "Config",
            True,
            f"station={station.name!r} genres={len(genres)} "
            f"vllm={station.vllm_base_url} model={station.vllm_text_model}",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "Config",
            False,
            str(exc),
            "Fix config/station.yaml and config/genres/*.yaml",
        )


def check_acestep_install() -> Check:
    root = _repo_root()
    vendor = root / "vendor" / "ACE-Step-1.5"
    if vendor.is_dir() and any(vendor.iterdir()):
        return Check("ACE-Step install", True, str(vendor))
    return Check(
        "ACE-Step install",
        False,
        f"not found at {vendor}",
        "Install once (needs GPU + disk for models):\n"
        "  bash scripts/install_acestep.sh",
    )


def check_acestep_api() -> Check:
    from airadio.clients.acestep import acestep_available

    ok, detail = acestep_available()
    if ok:
        return Check("ACE-Step API", True, detail)
    return Check(
        "ACE-Step API",
        False,
        detail,
        "Start the music server (separate process):\n"
        "  bash scripts/start_acestep_api.sh\n"
        "It listens on http://127.0.0.1:8001  (first run downloads models).",
    )


async def check_llm() -> Check:
    try:
        from airadio.clients.vllm_unified import check_vllm
        from airadio.config import load_station

        station, _ = load_station()
        result = await check_vllm(station.vllm_base_url, station.vllm_text_model)
        if result.get("ok"):
            return Check("vLLM (text+audio)", True, result.get("detail", "ok"))
        return Check(
            "vLLM (text+audio)",
            False,
            result.get("detail", "unreachable"),
            "Start vLLM (auto-starts in app or manual):\n"
            "  Option 1 (auto): ./start.sh  (app starts vLLM internally)\n"
            "  Option 2 (manual): bash scripts/launch_vllm.sh\n"
            "Models download on first use (~9GB total).",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "vLLM (text+audio)",
            False,
            str(exc),
            "Start vLLM: ./start.sh (auto) or bash scripts/launch_vllm.sh (manual)",
        )


def run_checks(*, require_llm: bool = True, require_ace: bool = True) -> list[Check]:
    checks: list[Check] = [check_python()]
    checks.extend(check_packages())
    checks.extend(check_bundled_tools())
    checks.append(check_config())
    checks.append(check_acestep_install())
    if require_ace:
        checks.append(check_acestep_api())
    if require_llm:
        checks.append(asyncio.run(check_llm()))
    return checks


def print_report(checks: list[Check]) -> bool:
    print()
    print("=== AI Radio preflight ===")
    all_ok = True
    fails: list[Check] = []
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        print(f"  {mark}  {c.name}: {c.detail}")
        if not c.ok:
            all_ok = False
            fails.append(c)
    print()
    if all_ok:
        print("All checks passed. Ready to start.")
        return True

    print("Not ready. Fix the failures below, then run again:")
    print()
    for i, c in enumerate(fails, 1):
        print(f"  [{i}] {c.name}")
        print(f"      Problem: {c.detail}")
        if c.fix:
            for line in c.fix.splitlines():
                print(f"      {line}")
        print()
    print("Quick first-time setup (project root):")
    print("  python3 -m venv .venv && source .venv/bin/activate")
    print("  pip install -e '.[dev]'")
    print("  bash scripts/install_acestep.sh")
    print("  # start LLM (vLLM), then:")
    print("  bash scripts/start_acestep_api.sh")
    print("  ./start.sh")
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    require_llm = "--skip-llm" not in argv
    require_ace = "--skip-ace" not in argv
    checks = run_checks(require_llm=require_llm, require_ace=require_ace)
    ok = print_report(checks)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
