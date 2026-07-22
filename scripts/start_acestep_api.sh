#!/usr/bin/env bash
# Start ACE-Step 1.5 REST API (project vendor install). Default port 8001.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${ACESTEP_HOME:-$ROOT/vendor/ACE-Step-1.5}"

if [[ ! -d "$VENDOR" ]]; then
  echo "ACE-Step not installed. Run: bash scripts/install_acestep.sh"
  exit 1
fi

export PATH="$HOME/.local/bin:$PATH"
cd "$VENDOR"

# Defaults tuned for RTX 4090 (~24GB) — quality + speed
export ACESTEP_API_HOST="${ACESTEP_API_HOST:-127.0.0.1}"
export ACESTEP_API_PORT="${ACESTEP_API_PORT:-8001}"
export ACESTEP_CONFIG_PATH="${ACESTEP_CONFIG_PATH:-acestep-v15-turbo}"
export ACESTEP_LM_MODEL_PATH="${ACESTEP_LM_MODEL_PATH:-acestep-5Hz-lm-1.7B}"
export ACESTEP_LM_BACKEND="${ACESTEP_LM_BACKEND:-pt}"
export ACESTEP_INIT_LLM="${ACESTEP_INIT_LLM:-true}"
# Leave headroom if LLM also on GPU
export ACESTEP_OFFLOAD_TO_CPU="${ACESTEP_OFFLOAD_TO_CPU:-false}"

LOG="${ROOT}/data/acestep-api.log"
mkdir -p "$ROOT/data"
echo "Starting ACE-Step API on ${ACESTEP_API_HOST}:${ACESTEP_API_PORT}"
echo "Log: $LOG"
echo "Models: DiT=${ACESTEP_CONFIG_PATH} LM=${ACESTEP_LM_MODEL_PATH}"

# Prefer official entrypoint
if uv run --help >/dev/null 2>&1; then
  nohup uv run acestep-api >>"$LOG" 2>&1 &
else
  nohup python -m acestep.api_server >>"$LOG" 2>&1 &
fi
echo $! >"$ROOT/data/acestep-api.pid"
echo "PID $(cat "$ROOT/data/acestep-api.pid")"

# Wait for health
for i in $(seq 1 60); do
  if curl -sf "http://${ACESTEP_API_HOST}:${ACESTEP_API_PORT}/health" >/dev/null 2>&1; then
    echo "ACE-Step API is up: http://${ACESTEP_API_HOST}:${ACESTEP_API_PORT}/health"
    exit 0
  fi
  sleep 2
done
echo "WARN: API did not become healthy in 120s — check $LOG"
exit 1
