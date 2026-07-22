#!/usr/bin/env bash
# Smart start: check deps, optionally start ACE-Step, run the radio, stop children on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED=$'\033[31m'
GRN=$'\033[32m'
YLW=$'\033[33m'
RST=$'\033[0m'

info()  { echo "${GRN}==>${RST} $*"; }
warn()  { echo "${YLW}!!${RST}  $*"; }
fail()  { echo "${RED}ERROR:${RST} $*" >&2; }

STARTED_ACE=0
APP_PID=""
ACE_PID_FILE="$ROOT/data/acestep-api.pid"

cleanup() {
  local code=$?
  if [[ -n "${APP_PID}" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" 2>/dev/null || true
    wait "$APP_PID" 2>/dev/null || true
  fi
  # Only stop ACE if we started it in this session
  if [[ "$STARTED_ACE" -eq 1 && -f "$ACE_PID_FILE" ]]; then
    local apid
    apid="$(cat "$ACE_PID_FILE" 2>/dev/null || true)"
    if [[ -n "$apid" ]] && kill -0 "$apid" 2>/dev/null; then
      info "Stopping ACE-Step API (pid $apid)…"
      kill "$apid" 2>/dev/null || true
      wait "$apid" 2>/dev/null || true
    fi
    rm -f "$ACE_PID_FILE"
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

# --- 1. Python + venv -------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.11+ and retry."
  exit 1
fi

if [[ ! -d .venv ]]; then
  fail "No .venv in $ROOT"
  echo
  echo "First-time setup:"
  echo "  cd $ROOT"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -e '.[dev]'"
  echo "  bash scripts/install_acestep.sh"
  echo "  # start your local LLM (e.g. ollama serve)"
  echo "  ./start.sh"
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if ! python -c "import airadio" 2>/dev/null; then
  warn "airadio not installed in venv — running: pip install -e '.[dev]'"
  pip install -e ".[dev]" || {
    fail "pip install failed. Fix errors above, then retry."
    exit 1
  }
fi

export KOKORO_DEVICE="${KOKORO_DEVICE:-cpu}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

# --- 2. Preflight (packages, ffmpeg, config, …) without ACE/LLM yet ----------
info "Checking project install…"
if ! python -m airadio.preflight --skip-llm --skip-ace; then
  fail "Project install incomplete. Fix the FAIL items above."
  exit 1
fi

# --- 3. ACE-Step: install check + start API if needed -----------------------
VENDOR="$ROOT/vendor/ACE-Step-1.5"
if [[ ! -d "$VENDOR" ]]; then
  fail "ACE-Step is not installed (required for real music)."
  echo "  bash scripts/install_acestep.sh"
  echo "Then re-run: ./start.sh"
  exit 1
fi

if ! curl -sf "http://127.0.0.1:8001/health" >/dev/null 2>&1; then
  info "ACE-Step API not running — starting it…"
  if ! bash "$ROOT/scripts/start_acestep_api.sh"; then
    fail "Could not start ACE-Step API. Check data/acestep-api.log"
    echo "  Manual start: bash scripts/start_acestep_api.sh"
    exit 1
  fi
  STARTED_ACE=1
else
  info "ACE-Step API already up on :8001"
fi

# --- 4. Full preflight including LLM ----------------------------------------
info "Checking LLM + ACE…"
if ! python -m airadio.preflight; then
  fail "Dependencies not ready. Fix the FAIL items above, then re-run."
  exit 1
fi

# --- 5. Run app -------------------------------------------------------------
HOST="${AIRADIO_HOST:-0.0.0.0}"
PORT="${AIRADIO_PORT:-8000}"
info "Starting radio on ${HOST}:${PORT}  (Ctrl-C to stop)"
# Reachable on LAN when HOST=0.0.0.0 (default)
if command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do
    info "  → http://${ip}:${PORT}/"
  done
fi
info "  → http://127.0.0.1:${PORT}/"
uvicorn airadio.main:app --app-dir src --host "$HOST" --port "$PORT" &
APP_PID=$!
wait "$APP_PID"
