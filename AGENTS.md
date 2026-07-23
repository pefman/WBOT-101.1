# AI Agent Instructions for Midnight Wire (WBOT-101.1)

**Midnight Wire** is a **100% locally-hosted AI radio station generator** that creates and streams original talk radio shows with generated music. Everything runs locally in `.venv` with zero external cloud dependencies.

## Project at a Glance

| Aspect | Details |
|--------|---------|
| **Language** | Python 3.11+ (async/await) |
| **Framework** | FastAPI + Uvicorn |
| **Core Purpose** | Orchestrate LLM → TTS → Music generation → HLS streaming |
| **Key Components** | Kokoro TTS, ACE-Step 1.5 music generation, local LLM (Ollama/llama-server) |
| **Deployment** | Standalone: no `apt install` (ffmpeg, espeak-ng, Kokoro all in `.venv`) |
| **Config** | PyYAML files in `config/` (17 genre packs, DJ profiles, moods) |

## Tech Stack Essentials

- **Backend**: FastAPI 0.115+, Uvicorn (REST API + HLS stream server)
- **Async Orchestration**: Python asyncio (all I/O non-blocking)
- **Audio Processing**: ffmpeg (via imageio-ffmpeg), soundfile, numpy
- **TTS**: Kokoro (local, GPU-accelerated)
- **Music Generation**: ACE-Step 1.5 (vendor/, HTTP API on `:8001`)
- **LLM Integration**: HTTP client for OpenAI-compatible servers (Ollama/llama-server)
- **Testing**: pytest 8.0+, pytest-asyncio
- **Frontend**: Vanilla JS + hls.js 1.5.17 + Vite (packaged in `src/airadio/static/`)

**Key isolation**: Vendor code (ACE-Step 1.5) is large (~50 GB on GPU) and **optional**; core app works without it.

## Setup & Commands

### One-Time Setup
```bash
cd WBOT-101.1
python3 -m venv .venv
source .venv/bin/activate

# Install core packages + dev tools
pip install -e ".[dev]"

# Optional: Setup ACE-Step music generation (requires GPU, ~50 GB)
bash scripts/install_acestep.sh
```

### Quick Preflight (packages only, no services required)
```bash
bash scripts/setup_check.sh
```

### Start Radio (all services)
```bash
./start.sh
```
This script:
1. Validates `.venv` and packages
2. Starts ACE-Step API (`:8001`) if installed but not running
3. Checks LLM, TTS, and music API availability with clear fail + fix messages
4. Starts radio FastAPI on `0.0.0.0:8000`
5. Traps `Ctrl-C` to cleanly stop services

**Note**: You must run a local LLM yourself (e.g., `ollama serve` or `llama-server`). See [config/station.yaml](config/station.yaml) for LLM configuration.

### Run Tests
```bash
export KOKORO_DEVICE=cpu  # Force CPU for headless/CI
pytest -v
```
Test files map to core features:
- `test_api.py` — FastAPI endpoints
- `test_orchestrator.py` — State machine and buffering
- `test_song_producer.py` — Music generation pipeline
- `test_talk_producer.py` — LLM script + TTS
- `test_config.py`, `test_djs.py`, `test_languages.py` — Config validation
- Integration tests for clients (Ollama, ACE-Step, library, prefs)

## Core Architecture

```
FastAPI app (src/airadio/main.py)
  │
  ├─ REST endpoints: /api/now, /api/control, /api/request
  ├─ HLS stream: /stream/playlist.m3u8
  └─ Web UI: / (static/index.html)
      │
      └─→ Orchestrator (orchestrator.py)
            • Manages RadioState (STOPPED, BUFFERING, PLAYING)
            • Pre-generates segments in a deque while current plays
            • GPU lock: single asyncio.Lock prevents LLM and music gen racing
            │
            ├─→ produce_talk (producers/talk.py)
            │    • LLM writes DJ script
            │    • Kokoro TTS synthesizes audio → WAV
            │
            ├─→ produce_song (producers/song.py)
            │    • LLM writes song metadata
            │    • ACE-Step generates original music → WAV
            │
            └─→ Clients (clients/)
                 • ollama.py — LLM streaming
                 • acestep.py — HTTP API calls
                 • kokoro.py — TTS invocation
```

**Key design patterns**:
- **Async event-driven**: All I/O is non-blocking
- **Deque-based buffering**: Next segment pre-generates while current plays (no user-facing stalls)
- **GPU lock**: Ensures only one GPU-heavy task (LLM or music gen) runs at a time
- **Generation stages**: UI tracks progress (`writing`, `speaking`, `composing`, `packaging`)

### Module Responsibilities

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI app, lifespan (startup/shutdown), endpoints |
| `orchestrator.py` | **Core**: Radio state machine, segment buffering, GPU lock |
| `config.py` | YAML loaders (station, DJs, genres, moods, news angles) |
| `models_types.py` | Pydantic models (DJ, Genre, Segment, RadioState, etc.) |
| `health.py` | Service readiness checks (LLM, TTS, ACE-Step) |
| `library.py` | Song library management, re-air pool, garbage collection |
| `prefs.py` | Desk preferences (DJ, genres, language, voice) — persisted to `data/prefs.json` |
| `clients/ollama.py` | Streaming LLM client; auto-pulls models on startup |
| `clients/kokoro.py` | TTS invocation with voice/language selection |
| `clients/acestep.py` | HTTP API for music generation |
| `producers/talk.py` | Orchestrate LLM → Kokoro → WAV |
| `producers/song.py` | Orchestrate LLM → ACE-Step → WAV |
| `stream/hls.py` | WAV → ffmpeg → m3u8 + .ts segment packaging |
| `audio/process.py` | Audio mixing, crossfade, probing (WAV utilities) |
| `art/sd_turbo.py` | Album cover generation (optional, SD-Turbo) |

## Config & Data Flow

**Configuration** (all in `config/`):
- `station.yaml` — Station name, LLM URL/model, default voice, buffer settings, song GC
- `genres/*.yaml` — 17 genre packs (moods, energy, themes, keywords for LLM prompts)
- `djs.yaml` — DJ profiles (voice, personality)
- `moods.yaml` — Mood presets (energy, pace, themes)
- `news_angles.yaml` — Topic prompts for talk generation

**Runtime state** (in `data/`, gitignored):
- `prefs.json` — Desk preferences (DJ, genres, language, voice)
- `library.json` — Kept songs + garbage collection metadata
- `hls/current/` — Active HLS segments (streamed to web player)

## Common Development Tasks

### Adding a New Genre Pack
1. Create `config/genres/my_genre.yaml` (copy from existing, e.g., `config/genres/rock.yaml`)
2. Update moods/energy/keywords to match genre
3. Test: `pytest test_config.py::test_load_genres -v`

### Debugging Generation Failures
1. Check preflight: `python -m airadio.preflight` (services available?)
2. Check LLM: `curl http://localhost:11434/api/generate` (Ollama running?)
3. Check ACE-Step: `curl http://localhost:8001/health` (music API up?)
4. Check TTS: Run `pytest test_talk_producer.py -v` (Kokoro working?)
5. Logs: Radio prints generation stage (`writing`, `speaking`, `composing`) in real time

### Adding LLM Prompt Logic
- DJ scripts: `clients/ollama.py` (prompt template) + `producers/talk.py` (orchestration)
- Song metadata: `producers/song.py` (LLM call for genre/mood/artist tags)
- Prompts are config-driven; update `config/news_angles.yaml` or `config/moods.yaml` to change behavior

### Testing a Feature
- Unit tests: `pytest tests/test_*.py -v` (isolated to one module)
- Integration: `pytest tests/test_orchestrator.py -v` (full pipeline)
- Always set `export KOKORO_DEVICE=cpu` for local testing (avoid GPU memory spikes)

## Key Conventions & Pitfalls

### Must Know
1. **Everything in `.venv`**: No `apt install` needed. ffmpeg, espeak-ng, Kokoro are all bundled. If you see import errors, reinstall: `pip install -e ".[dev]"`.
2. **Local LLM required**: Radio won't start without a running OpenAI-compatible HTTP server (Ollama, llama-server, etc.). Configure `config/station.yaml` (`ollama_base_url`, `ollama_model`).
3. **GPU lock is critical**: Two async tasks racing for GPU → OOM or silent failures. The lock in `orchestrator.py` prevents this; do not remove or bypass.
4. **Segments are pre-generated**: While user hears segment N, segment N+1 is already generating in the background. If generation fails, user hears silence, not an error. Check `/api/now` for `generation` stage.
5. **HLS uses .ts chunks**: Don't manipulate segments in `data/hls/current/` manually; ffmpeg manages them. Stale chunks are auto-cleaned.

### Common Mistakes
- **LLM not running**: App starts but plays silence. Run `ollama serve` in another terminal.
- **ACE-Step installed but not running**: Optional but slow first-request latency if not pre-warmed. `bash scripts/start_acestep_api.sh` to pre-warm.
- **Modifying config mid-stream**: Config is loaded at startup. Restart radio to pick up new genres/DJs.
- **GPU OOM during music gen**: Reduce `compose_inference_steps` in `config/station.yaml` or disable cover art (SD-Turbo is memory-heavy).
- **Stale `.venv`**: If pip errors occur, try: `rm -rf .venv && python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.

## Documentation

- **[README.md](README.md)** — Quickstart, setup, API overview
- **[config/station.yaml](config/station.yaml)** — Detailed config options (buffer sizes, model selection, reair probability)
- **[docs/](docs/)** — High-level architecture, superpowers, deployment plans (if present)

See README.md for full API reference and troubleshooting.

## Typical Workflows

### "The radio plays silence on my first request"
→ Check `/api/health` endpoint. If LLM/ACE/TTS unhealthy, preflight output will tell you exactly what's missing.

### "I want to change the DJ personality"
→ Edit `config/djs.yaml`, restart radio, pick DJ from web UI desk.

### "Music generation is too slow"
→ Reduce `compose_inference_steps` in `config/station.yaml` (default 30); trade quality for speed.

### "How do I disable album covers?"
→ Set `cover_backend: none` in `config/station.yaml`, or don't install `.[cover]` extras.

### "I want to add a new LLM prompt template"
→ Edit `config/news_angles.yaml` for talk prompts; prompts for music metadata live in `producers/song.py` (LLM call).

---

**Last updated**: 2026-07-23  
**Python version**: 3.11+  
**Main entry point**: `src/airadio/main.py` → `src/airadio/orchestrator.py`
