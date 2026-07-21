# Midnight Wire — Self-contained local AI radio

**No system packages.** Everything the radio app needs installs into the project **`.venv`**.

| Piece | Where it lives |
|-------|----------------|
| App + FastAPI + orchestrator | `.venv` / `src/airadio` |
| **Kokoro TTS** | `.venv` (pip) |
| **espeak-ng** for phonemes | `.venv` via `espeakng-loader` (not apt) |
| **ffmpeg** for HLS | `.venv` via `imageio-ffmpeg` (not apt) |
| Web UI + hls.js | packaged in `src/airadio/static/` (no npm at runtime) |
| Station / genres | `config/` |
| Generated audio | `data/` (gitignored) |

**Outside the app (local services you already run):**

- A **local LLM HTTP server** (OpenAI-compatible), e.g. llama.cpp `llama-server` or Ollama — configured in `config/station.yaml`
- Optional **ACE-Step** checkout for real music (`ACESTEP_HOME`), or `ACESTEP_MOCK=1` for synthetic tracks

The radio does **not** `apt install` ffmpeg, espeak, or anything else.

## One-time setup (project only)

```bash
cd WBOT-101.1
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# First-time model weights land under ~/.cache/huggingface (or set HF_HOME
# inside the project if you want that cache here too).

bash scripts/setup_check.sh
```

## Run (single process)

```bash
source .venv/bin/activate
export KOKORO_DEVICE=cpu          # leave GPU free for your LLM / music
export ACESTEP_MOCK=1             # or real ACE-Step via ACESTEP_HOME

# Point station.yaml at your local LLM (already set for llama-server if used)
uvicorn airadio.main:app --app-dir src --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** — UI is served by the app. No Vite/npm required.

## Configure

- `config/station.yaml` — name, host persona, LLM base URL + model id, voice, buffers  
- `config/genres/*.yaml` — 10 genre packs; random pick per song  

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Web player |
| GET | `/api/health` | Self-contained component check |
| GET | `/api/now` | Now playing |
| POST | `/api/control` | `{"action":"play"\|"stop"}` |
| GET | `/stream/playlist.m3u8` | HLS |

## Tests

```bash
source .venv/bin/activate
export ACESTEP_MOCK=1 KOKORO_DEVICE=cpu
pytest -v
```

## Design docs

- `docs/superpowers/specs/2026-07-21-local-ai-radio-design.md`
- `docs/superpowers/plans/2026-07-21-local-ai-radio.md`
