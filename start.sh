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
  
  # Stop the radio app if it's still running
  if [[ -n "${APP_PID}" ]]; then
    if kill -0 "$APP_PID" 2>/dev/null; then
      info "Stopping radio app (PID $APP_PID)…"
      # Try graceful termination first (SIGTERM)
      kill -TERM "$APP_PID" 2>/dev/null || true
      # Wait up to 3 seconds for graceful shutdown
      local count=0
      while kill -0 "$APP_PID" 2>/dev/null && [[ $count -lt 3 ]]; do
        sleep 1
        ((count++))
      done
      # Force kill if still running
      if kill -0 "$APP_PID" 2>/dev/null; then
        warn "App did not stop gracefully, forcing…"
        kill -9 "$APP_PID" 2>/dev/null || true
      fi
    fi
    # Clean up the wait process
    wait "$APP_PID" 2>/dev/null || true
  fi
  
  # Only stop ACE if we started it in this session.
  # Use stop_acestep_api.sh — pidfile alone is the `uv` parent; the real
  # server is a child holding :8001 + GPU VRAM.
  if [[ "$STARTED_ACE" -eq 1 ]]; then
    info "Stopping ACE-Step API…"
    bash "$ROOT/scripts/stop_acestep_api.sh" || true
  fi
  
  # Kill any stray vLLM processes on the expected port
  if command -v lsof >/dev/null 2>&1; then
    local vllm_pid
    vllm_pid=$(lsof -t -i :8000 2>/dev/null || true)
    if [[ -n "$vllm_pid" ]]; then
      warn "Found vLLM process ($vllm_pid) still running on :8000"
      info "Note: If vLLM is running in another terminal, it will continue (run 'kill $vllm_pid' manually if needed)"
    fi
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
  echo "  ./start.sh                    # Auto-installs everything + starts radio"
  echo ""
  echo "Note: vLLM, Orpheus, and models download automatically on first run."
  exit 1
fi

# shellcheck source=/dev/null
source .venv/bin/activate

# Check if critical packages are missing
MISSING=0
if ! python -c "import vllm" 2>/dev/null; then
  MISSING=1
fi
if ! python -c "import orpheus_tts" 2>/dev/null; then
  MISSING=1
fi
if ! python -c "import airadio" 2>/dev/null; then
  MISSING=1
fi

if [[ $MISSING -eq 1 ]]; then
  warn "Missing dependencies (vllm, orpheus_tts, or airadio)"
  info "Auto-installing: pip install -e '.[dev,cover]'"
  if ! pip install -e ".[dev,cover]"; then
    fail "pip install failed. Check errors above and retry."
    exit 1
  fi
  info "Dependencies installed successfully."
fi

export KOKORO_DEVICE="${KOKORO_DEVICE:-cpu}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

# --- 2. Preflight (packages, ffmpeg, config, …) without ACE/LLM yet ----------
info "Checking project install…"
if ! python -m airadio.preflight --skip-llm --skip-ace; then
  fail "Project install incomplete. Fix the FAIL items above."
  exit 1
fi

# --- 3. ACE-Step: install if needed, then start API if not running --------
VENDOR="$ROOT/vendor/ACE-Step-1.5"
if [[ ! -d "$VENDOR" ]]; then
  info "ACE-Step not installed — auto-installing…"
  if ! bash "$ROOT/scripts/install_acestep.sh"; then
    fail "ACE-Step install failed. Check disk space and GPU driver."
    exit 1
  fi
  info "ACE-Step installed successfully."
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

# --- 5. Run app (auto-starts vLLM if needed) --------------------------------
HOST="${AIRADIO_HOST:-0.0.0.0}"
PORT="${AIRADIO_PORT:-8888}"
info "Starting radio on ${HOST}:${PORT}  (Ctrl-C to stop)"
info "vLLM will be started internally or used if already running…"
# Reachable on LAN when HOST=0.0.0.0 (default)
if command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do
    info "  → http://${ip}:${PORT}/"
  done
fi
info "  → http://127.0.0.1:${PORT}/"
echo
uvicorn airadio.main:app --app-dir src --host "$HOST" --port "$PORT" &
APP_PID=$!

# Wait for the app to start, then print a ready message
sleep 2
if kill -0 "$APP_PID" 2>/dev/null; then
  info "Radio app started. Press Ctrl-C to stop."
  echo
fi

# Wait for the app to finish (will be interrupted by trap on Ctrl-C)
wait "$APP_PID" 2>/dev/null || true
