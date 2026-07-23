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

- A **local LLM HTTP server** (OpenAI-compatible), e.g. vLLM 0.19.1 — configured in `config/station.yaml`
- **ACE-Step** music API (`bash scripts/install_acestep.sh` then `bash scripts/start_acestep_api.sh`)

The radio does **not** `apt install` ffmpeg, espeak, or anything else.

## One-time setup

```bash
cd WBOT-101.1
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
bash scripts/setup_check.sh          # packages + bundled tools only
bash scripts/install_acestep.sh      # ACE-Step music stack (vendor/, GPU + disk)
```

Also run a **local LLM** (vLLM 0.19.1) matching `config/station.yaml`
(`vllm_base_url` / `vllm_text_model`).

## Run

```bash
# Start LLM yourself (example):
#   python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4 --port 8000

./start.sh
```

`./start.sh` will:

1. Check the project venv / packages  
2. Start **ACE-Step** on `:8001` if it is installed but not running  
3. Fail with clear **FAIL + fix** messages if LLM / ACE / tools are not ready  
4. Start the radio on **0.0.0.0:8000** (LAN-reachable; open `http://<your-ip>:8000/`)  

5. On **Ctrl-C**, stop the radio (and ACE if this script started it)

Manual checks only: `python -m airadio.preflight`

## Configure

- `config/station.yaml` — name, host persona, LLM base URL + model id, voice, buffers  
- `config/genres/*.yaml` — 10 genre packs; random pick per song  

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | Web player (desk / controls) |
| GET | `/listen` | Listen-only page (stream + now playing, no controls) |
| GET | `/api/health` | Self-contained component check |
| GET | `/api/now` | Now playing (+ `generation` stage/progress) |
| POST | `/api/control` | `{"action":"play"\|"stop"\|"skip"}` |
| POST | `/api/request` | Queue a listener talk bit: `{"text":"…"}` |
| GET | `/api/library` | Kept songs (re-air pool) |
| POST | `/api/favorite` | `{"segment_id":"…","favorite":true}` |
| GET | `/stream/playlist.m3u8` | HLS |

**Desk prefs** (DJ, genres, language, voice) persist in `data/prefs.json` across restarts.  
**Song library** + GC settings: `library_max_songs`, `reair_chance`, `segment_max_*` in `config/station.yaml`.  
**Cover art:** `cover_backend: sd_turbo` downloads **SD-Turbo** on app start when `cover_auto_download: true` (first boot ~3–5 GB). Install extras: `pip install -e ".[cover]"`.

## Tests

```bash
source .venv/bin/activate
export KOKORO_DEVICE=cpu
pytest -v
```

## Design docs

- `docs/superpowers/specs/2026-07-21-local-ai-radio-design.md`
- `docs/superpowers/plans/2026-07-21-local-ai-radio.md`
