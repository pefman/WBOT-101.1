#!/usr/bin/env bash
# Self-contained check: only project .venv — no system packages required.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== AI Radio self-contained setup check =="
echo "Project: $ROOT"

if [[ ! -d .venv ]]; then
  echo "MISS .venv — run: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "OK  venv: $VIRTUAL_ENV"
python - <<'PY'
import sys
from importlib import import_module

def need(mod, label=None):
    label = label or mod
    try:
        m = import_module(mod)
        ver = getattr(m, "__version__", "ok")
        print(f"OK  {label}: {ver}")
        return True
    except Exception as e:
        print(f"MISS {label}: {e}")
        return False

ok = True
ok &= need("airadio")
ok &= need("fastapi")
ok &= need("kokoro")
ok &= need("imageio_ffmpeg")
ok &= need("espeakng_loader", "espeakng-loader")
ok &= need("torch")
ok &= need("soundfile")

from airadio.paths import bundled_ffmpeg, ensure_bundled_espeak, static_web_dir
try:
    ff = bundled_ffmpeg()
    print(f"OK  bundled ffmpeg: {ff}")
except Exception as e:
    print(f"MISS bundled ffmpeg: {e}")
    ok = False

es = ensure_bundled_espeak()
if es.get("library"):
    print(f"OK  bundled espeak: {es['library']}")
else:
    print("MISS bundled espeak (espeakng-loader)")
    ok = False

web = static_web_dir()
if (web / "index.html").is_file() and (web / "hls.min.js").is_file():
    print(f"OK  packaged UI: {web}")
else:
    print(f"MISS packaged UI under {web}")
    ok = False

sys.exit(0 if ok else 1)
PY

echo
echo "LLM note: point config/station.yaml at a *local* OpenAI-compatible server"
echo "  (llama-server / Ollama). That process is separate; the radio app itself"
echo "  stays self-contained in .venv and does not apt-install anything."
echo
echo "Music: export ACESTEP_MOCK=1 for dev, or set ACESTEP_HOME to a local checkout."
echo "All good if exit code 0."
