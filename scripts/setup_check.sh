#!/usr/bin/env bash
# Install-time / quick check (packages + tools only — not LLM/ACE runtime).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "MISS .venv — run:"
  echo "  python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"

echo "== Project package check (skip live LLM / ACE API) =="
python -m airadio.preflight --skip-llm --skip-ace
echo
echo "When ready to go on air:"
echo "  bash scripts/install_acestep.sh   # once, for music"
echo "  # start your local LLM (Ollama / llama-server)"
echo "  ./start.sh                        # full preflight + radio"
