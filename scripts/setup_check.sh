#!/usr/bin/env bash
set -euo pipefail

echo "== AI Radio setup check =="

need() {
  if command -v "$1" >/dev/null 2>&1; then
    echo "OK  $1: $(command -v "$1")"
  else
    echo "MISS $1"
    return 1
  fi
}

fail=0
need python3 || fail=1
need ffmpeg || echo "WARN ffmpeg missing (HLS/loudnorm degraded)"
need espeak-ng || need espeak || echo "WARN espeak-ng missing (Kokoro may need it)"
command -v ollama >/dev/null 2>&1 && echo "OK  ollama" || echo "MISS ollama (install from https://ollama.com)"
command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L || echo "INFO no nvidia-smi (CPU-only or non-NVIDIA)"

echo
echo "Python packages (in active venv):"
python3 -c "import fastapi, httpx, yaml, soundfile; print('OK fastapi/httpx/yaml/soundfile')" 2>/dev/null || {
  echo "MISS install: pip install -e '.[dev]'"
  fail=1
}
python3 -c "import kokoro; print('OK kokoro')" 2>/dev/null || echo "WARN kokoro not installed (pip install kokoro) or use KOKORO_URL"

echo
echo "ACE-Step:"
if [[ "${ACESTEP_MOCK:-}" == "1" ]]; then
  echo "OK  ACESTEP_MOCK=1 (dev synthetic music)"
elif [[ -n "${ACESTEP_HOME:-}" ]]; then
  echo "OK  ACESTEP_HOME=$ACESTEP_HOME"
else
  echo "WARN set ACESTEP_HOME or ACESTEP_MOCK=1 — see README"
fi

echo
df -h . | tail -1
echo "Note: ACE-Step models often need ~20–25GB disk."

exit "$fail"
