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

# Defaults: DiT-only is reliable when VRAM is shared (Ollama, browser, etc.).
# Thinking/5Hz LM needs several extra free GB — enable with ACESTEP_INIT_LLM=true.
export ACESTEP_API_HOST="${ACESTEP_API_HOST:-127.0.0.1}"
export ACESTEP_API_PORT="${ACESTEP_API_PORT:-8001}"
export ACESTEP_CONFIG_PATH="${ACESTEP_CONFIG_PATH:-acestep-v15-turbo}"
export ACESTEP_LM_MODEL_PATH="${ACESTEP_LM_MODEL_PATH:-acestep-5Hz-lm-0.6B}"
export ACESTEP_LM_BACKEND="${ACESTEP_LM_BACKEND:-pt}"
export ACESTEP_INIT_LLM="${ACESTEP_INIT_LLM:-false}"
# Offload DiT when idle if VRAM is tight
export ACESTEP_OFFLOAD_TO_CPU="${ACESTEP_OFFLOAD_TO_CPU:-false}"

LOG="${ROOT}/data/acestep-api.log"
mkdir -p "$ROOT/data"

# Avoid stacking orphans: always clear any previous instance first.
if curl -sf "http://${ACESTEP_API_HOST}:${ACESTEP_API_PORT}/health" >/dev/null 2>&1 \
  || [[ -f "$ROOT/data/acestep-api.pid" ]]; then
  echo "Stopping any existing ACE-Step API before start…"
  bash "$ROOT/scripts/stop_acestep_api.sh" || true
fi

echo "Starting ACE-Step API on ${ACESTEP_API_HOST}:${ACESTEP_API_PORT}"
echo "Log: $LOG"
echo "Models: DiT=${ACESTEP_CONFIG_PATH} LM=${ACESTEP_LM_MODEL_PATH}"

# New session so stop can kill the whole process group (uv + python child).
# Prefer official entrypoint
if uv run --help >/dev/null 2>&1; then
  setsid nohup uv run acestep-api >>"$LOG" 2>&1 &
else
  setsid nohup python -m acestep.api_server >>"$LOG" 2>&1 &
fi
echo $! >"$ROOT/data/acestep-api.pid"
echo "PID $(cat "$ROOT/data/acestep-api.pid") (process group leader)"

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
