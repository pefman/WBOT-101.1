#!/usr/bin/env bash
# Install ACE-Step 1.5 into the project (vendor/) — separate from the airadio venv.
# Models download on first API start (~15–25GB). Needs free disk + NVIDIA GPU.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="${ACESTEP_HOME:-$ROOT/vendor/ACE-Step-1.5}"
REPO_URL="${ACESTEP_REPO:-https://github.com/ace-step/ACE-Step-1.5.git}"

echo "== Install ACE-Step 1.5 into project =="
echo "Target: $VENDOR"
df -h "$ROOT" | tail -1

mkdir -p "$(dirname "$VENDOR")"

if [[ ! -d "$VENDOR/.git" ]]; then
  echo "Cloning $REPO_URL …"
  git clone --depth 1 "$REPO_URL" "$VENDOR"
else
  echo "Already cloned; pulling latest…"
  git -C "$VENDOR" pull --ff-only || true
fi

cd "$VENDOR"

# uv is the official installer; install into user home if missing (not apt)
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv into \$HOME/.local (not system)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "uv sync (creates ACE-Step's own .venv under vendor/)…"
uv sync

echo
echo "OK. Next:"
echo "  bash scripts/start_acestep_api.sh"
echo "  # then start airadio: ./start.sh"
echo
echo "API will listen on http://127.0.0.1:8001"
echo "First song generation downloads model weights into the ACE-Step cache."
