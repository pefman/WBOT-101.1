# Midnight Wire — Local AI Radio MVP

**100% offline** AI radio station: local LLM host banter, **Kokoro** TTS, and **ACE-Step 1.5** music, streamed to a simple web player (play/stop, volume, now playing).

```
Talk (Ollama + Kokoro CPU)  →  Song (ACE-Step GPU)  →  Talk  →  Song  →  …
         deep buffer ahead of the listener
```

## Hardware

| Component | Target |
|-----------|--------|
| GPU | **8–12GB** NVIDIA VRAM (ACE-Step Tier 4: 0.6B LM, INT8, CPU+DiT offload) |
| TTS / LLM | Prefer **CPU** so the GPU stays free for music |
| Disk | Plan **~25GB+** for ACE-Step checkpoints |

## Stack

| Role | Model / tool |
|------|----------------|
| Host brain | Ollama (e.g. `qwen2.5:7b`) |
| TTS | **Kokoro-82M** (Apache 2.0) |
| Music | **ACE-Step 1.5** (MIT) — not LeVo 2 (non-commercial) |
| API | FastAPI |
| UI | Vite + HLS.js |

## Quick start

```bash
# 1. Python env
cd WBOT-101.1
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install kokoro   # TTS; also: sudo apt install espeak-ng

# 2. Ollama
ollama serve   # if not already running
ollama pull qwen2.5:7b

# 3. Music — pick one:
#    A) Dev without ACE-Step:
export ACESTEP_MOCK=1
#    B) Real ACE-Step 1.5 (see upstream install):
#       https://github.com/ace-step/ACE-Step-1.5
#    export ACESTEP_HOME=/path/to/ACE-Step-1.5
#    # or set acestep_cmd in config/station.yaml

# 4. Optional checks
bash scripts/setup_check.sh

# 5. API
uvicorn airadio.main:app --app-dir src --host 127.0.0.1 --port 8000

# 6. Web UI (other terminal)
cd web && npm install && npm run dev
# open http://127.0.0.1:5173
```

Press **Play**. The station **buffers live** (first talk + song) before going on air — with real ACE-Step this can take **minutes** on 8–12GB. Status text shows buffering progress.

## Customize

- **Station personality:** `config/station.yaml` (`name`, `host_name`, `system_prompt`, `kokoro_voice`, buffers, duration)
- **Genres:** `config/genres/*.yaml` — 10 packs; random pick per song; restrict with `enabled_genres: [synthwave, lofi_chill]`

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | ollama / kokoro / acestep / ffmpeg |
| GET | `/api/config` | public station config |
| GET | `/api/now` | state + current segment |
| GET | `/api/queue` | upcoming segments |
| POST | `/api/control` | `{"action":"play"\|"stop"}` |
| GET | `/stream/playlist.m3u8` | HLS playlist |
| GET | `/stream/current.wav` | WAV fallback |

## Tests

```bash
source .venv/bin/activate
export ACESTEP_MOCK=1
pytest -v
```

## Project layout

```
config/           station + genre YAML
src/airadio/      FastAPI, orchestrator, producers, clients
web/              Vite player
data/             generated segments + HLS (gitignored)
docs/superpowers/ design + plan
```

## Notes

- **Live-only deep buffer** — no seed music library; generation runs while you listen.
- Only **one** ACE-Step job at a time (GPU lock).
- Ollama calls use `num_gpu: 0` by default so music can own the VRAM.
- Quality of open music models varies by prompt; genre packs are the main tuning knob.

## License

Application code: use as you like in this repo. Model licenses are separate (Kokoro Apache 2.0, ACE-Step MIT, Ollama model licenses vary).
