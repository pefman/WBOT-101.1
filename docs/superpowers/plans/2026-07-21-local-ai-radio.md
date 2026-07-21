# Local AI Radio Station MVP — Design Spec + Implementation Plan

> **For agentic workers:** Use subagent-driven-development (recommended) or executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Build a 100% offline AI radio that alternates live talk (LLM + Kokoro TTS) and live songs (ACE-Step 1.5), buffered ahead of the listener, with a localhost web UI (play/stop, volume, now playing).

**Architecture:** FastAPI orchestrator maintains a deep segment queue (Talk → Song → Talk → Song…). Talk is generated on CPU (Ollama + Kokoro). Songs are generated one-at-a-time on GPU (ACE-Step 1.5 Tier-4 settings for 8–12GB). Browser plays continuous audio via HLS while polling `/api/now` for metadata.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Ollama, Kokoro-82M, ACE-Step 1.5, ffmpeg, Vite + vanilla JS, HLS.js

## Global Constraints

- **100% local / offline** after model download — no cloud TTS, music, or LLM APIs
- **Hardware:** 8–12GB NVIDIA VRAM; pin TTS + prefer LLM on CPU; GPU exclusive to ACE-Step
- **Format:** alternating talk / music forever (live-only deep buffer — no seed music library)
- **Genres:** 10 curated packs; random pick per song; station personality customizable via YAML
- **Listeners:** localhost single user only
- **Quality first** over generation speed
- **License-safe defaults:** Kokoro Apache 2.0; ACE-Step 1.5 MIT (not LeVo 2)
- ACE-Step **8–12GB = Tier 4:** 0.6B LM, INT8, CPU+DiT offload, no XL DiT; song duration **60–90s** default
- Cold start: Play enters BUFFERING until ≥2 segments ready; UI must show progress

---

# Part A — Design Spec (approved)

## Research conclusions

| Role | Choice | Why |
|------|--------|-----|
| TTS | **Kokoro-82M** | Best lightweight local quality 2026; ~350MB; CPU realtime; Apache 2.0 |
| Music | **ACE-Step 1.5** | Best open commercial-friendly full-song model; works on 8–12GB with auto tier |
| Host brain | **Ollama 7–8B instruct Q4** | Banter, titles, lyrics, refined style prompts |
| Avoid | LeVo 2 | Best sound but **non-commercial** license |

Sources: IT-JIM open music comparison (May 2026); Kokoro local guides; ACE-Step 1.5 GPU_COMPATIBILITY.md Tier 4 for 8–12GB.

## Architecture

```
Browser ──► FastAPI (/api/*, /stream/*)
               │
          Orchestrator
          queue + BUFFERING|PLAYING|STOPPED
               │
     ┌─────────┴──────────┐
 TalkProducer          SongProducer
 Ollama→Kokoro(CPU)    Ollama→ACE-Step(GPU)
     └─────────┬──────────┘
          Segment store (WAV + JSON)
          HLS packager (ffmpeg)
```

## Segment model

```python
@dataclass
class Segment:
    id: str                 # uuid4 hex
    kind: Literal["talk", "song"]
    title: str              # song title or "On air: {host}"
    genre_id: str | None
    text: str               # spoken script or lyrics
    audio_path: Path
    duration_ms: int
    created_at: float
```

## Buffer policy

| Param | Default |
|-------|---------|
| BUFFER_MIN | 2 |
| BUFFER_TARGET | 4 |
| song_duration_sec | 75 |
| talk_max_words | 100 |
| pattern | talk, song, talk, song, … |

## Station + genres

- `config/station.yaml` — name, host, system_prompt, kokoro_voice, ollama_model, enabled_genres, buffer_*, song_duration_sec
- `config/genres/*.yaml` — 10 genres: indie_rock, lofi_chill, synthwave, jazz_lounge, hiphop_boom_bap, dream_pop, hard_rock, folk_acoustic, house_dance, ambient_soundscape

## Web UI

Play / Stop · Volume · Now playing (type, title, genre) · Status (Stopped / Buffering msg / On air)

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | ollama / kokoro / acestep / ffmpeg |
| GET | `/api/now` | state + current segment |
| GET | `/api/queue` | upcoming metadata |
| POST | `/api/control` | `{"action":"play"\|"stop"}` |
| GET | `/api/config` | public station info |
| GET | `/stream/playlist.m3u8` | HLS |
| GET | `/stream/seg/{name}` | media parts |

## Failure modes

- Model missing → health red, Play blocked with message  
- ACE-Step OOM → retry shorter duration / fewer steps  
- Buffer underrun → BUFFERING, pause stream, resume when ready  
- Talk fail → short silence sting, continue to next song attempt  

## Project layout

```
WBOT-101.1/
  README.md
  pyproject.toml
  config/station.yaml
  config/genres/*.yaml
  src/airadio/
    __init__.py
    main.py
    config.py
    models_types.py
    health.py
    orchestrator.py
    producers/talk.py
    producers/song.py
    clients/ollama.py
    clients/kokoro.py
    clients/acestep.py
    stream/hls.py
    audio/process.py
  web/                 # Vite vanilla
  tests/
  scripts/setup_check.sh
  data/                # gitignored
  docs/superpowers/specs/2026-07-21-local-ai-radio-design.md
  docs/superpowers/plans/2026-07-21-local-ai-radio.md
```

---

# Part B — Implementation Plan

## File responsibilities

| Path | Responsibility |
|------|----------------|
| `config/*` | Human-editable station + genre prompts |
| `src/airadio/config.py` | Load/validate YAML into typed settings |
| `src/airadio/models_types.py` | Segment, StationConfig, Genre, AppState enums |
| `src/airadio/clients/*` | Thin wrappers; no orchestrator logic |
| `src/airadio/producers/*` | Build one Segment end-to-end |
| `src/airadio/orchestrator.py` | Queue, workers, play/stop, underrun |
| `src/airadio/stream/hls.py` | Turn WAV → HLS segments + playlist |
| `src/airadio/audio/process.py` | duration, loudnorm, optional crossfade |
| `src/airadio/main.py` | FastAPI routes + lifespan |
| `web/*` | Player UI |
| `tests/*` | Unit tests with mocked clients |

---

### Task 1: Project skeleton + config

**Files:**
- Create: `pyproject.toml`, `src/airadio/__init__.py`, `src/airadio/config.py`, `src/airadio/models_types.py`
- Create: `config/station.yaml`, `config/genres/{10 files}.yaml`
- Create: `.gitignore` (`data/`, `.venv/`, `node_modules/`, `__pycache__/`)
- Create: `tests/test_config.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `load_station(path) -> StationConfig`, `load_genres(dir) -> dict[str, Genre]`, dataclasses `StationConfig`, `Genre`, `Segment`, `RadioState`

- [ ] **Step 1:** Create package layout and `pyproject.toml` with deps: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic`, `pyyaml`, `soundfile`, `numpy`, `pytest`, `pytest-asyncio`

- [ ] **Step 2:** Define dataclasses in `models_types.py`:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

class RadioState(str, Enum):
    STOPPED = "stopped"
    BUFFERING = "buffering"
    PLAYING = "playing"

@dataclass(frozen=True)
class Genre:
    id: str
    name: str
    style_prompt: str
    lyric_style: str
    dj_tone: str
    bpm: int
    duration_sec: int

@dataclass
class StationConfig:
    name: str
    host_name: str
    system_prompt: str
    kokoro_voice: str
    ollama_model: str
    ollama_base_url: str
    language: str
    enabled_genres: list[str]  # ["all"] or ids
    buffer_min: int
    buffer_target: int
    song_duration_sec: int
    talk_max_words: int
    data_dir: Path
    acestep_cmd: list[str] | None = None  # optional override

@dataclass
class Segment:
    id: str
    kind: Literal["talk", "song"]
    title: str
    genre_id: str | None
    text: str
    audio_path: Path
    duration_ms: int
    created_at: float
```

- [ ] **Step 3:** Implement `config.py` loaders with PyYAML; resolve `enabled_genres: [all]` to all genre ids; create `data_dir` if missing.

- [ ] **Step 4:** Write all 10 genre YAML files with strong style prompts (full text in repo — no empty placeholders). Example synthwave:

```yaml
id: synthwave
name: Synthwave
style_prompt: >
  Retro 1980s synthwave, neon night-drive atmosphere, analog saw leads,
  gated reverb snare, pulsing bass, cinematic pads, clean mix, ~100 BPM.
lyric_style: short nostalgic lines about city lights and night highways; anthemic chorus
dj_tone: cool late-night FM host, unhurried
bpm: 100
duration_sec: 75
```

(Same structure for: indie_rock, lofi_chill, jazz_lounge, hiphop_boom_bap, dream_pop, hard_rock, folk_acoustic, house_dance, ambient_soundscape — ambient may set lyric_style to "instrumental, no vocals" and song producer should pass empty/minimal lyrics.)

- [ ] **Step 5:** `station.yaml` defaults (Midnight Wire / Aria / qwen2.5:7b / af_heart / buffer 2–4 / 75s).

- [ ] **Step 6:** Test `tests/test_config.py` — loads station, 10 genres, `all` expands.

```bash
pytest tests/test_config.py -v
```

- [ ] **Step 7:** Commit `chore: scaffold airadio config and genre packs`

---

### Task 2: Ollama client + health

**Files:**
- Create: `src/airadio/clients/ollama.py`, `src/airadio/health.py`, `tests/test_ollama_client.py`
- Modify: `src/airadio/main.py` (minimal app with `/api/health` only)

**Interfaces:**
- Produces: `async def ollama_chat(base_url, model, system, user, timeout=120) -> str`
- Produces: `async def check_ollama(base_url, model) -> dict` → `{ok, detail}`

- [ ] **Step 1:** Implement `ollama_chat` via `httpx` POST `{base}/api/chat` with `stream: false`; raise on non-200.

- [ ] **Step 2:** Unit test with `httpx.MockTransport` or respx-style mock — assert messages payload.

- [ ] **Step 3:** `health.py` aggregates checks (ollama required; kokoro/acestep/ffmpeg stubbed true until later tasks).

- [ ] **Step 4:** FastAPI app lifespan loads config; `GET /api/health`, `GET /api/config` (public fields only).

- [ ] **Step 5:** Manual: `uvicorn airadio.main:app --app-dir src` with Ollama running.

- [ ] **Step 6:** Commit `feat: ollama client and health endpoint`

---

### Task 3: Kokoro TTS client + TalkProducer

**Files:**
- Create: `src/airadio/clients/kokoro.py`, `src/airadio/producers/talk.py`, `src/airadio/audio/process.py`
- Create: `tests/test_talk_producer.py`

**Interfaces:**
- Consumes: `StationConfig`, `ollama_chat`
- Produces: `synthesize_kokoro(text, voice, out_path) -> duration_ms`
- Produces: `async def produce_talk(station, genres_hint, prev_song, next_song, out_dir) -> Segment`

- [ ] **Step 1:** `audio/process.py` — `probe_duration_ms(path)` via soundfile; `loudnorm_ffmpeg(in, out)` optional wrapper calling ffmpeg.

- [ ] **Step 2:** `kokoro.py` — call Kokoro Python API (preferred) or HTTP to Kokoro-FastAPI if `KOKORO_URL` set. Write 24kHz WAV. Document install: `pip install kokoro soundfile` + system `espeak-ng`.

Minimal generation pattern (adjust to installed API):

```python
# Prefer official kokoro package pipeline; write PCM to out_path with soundfile
```

If import fails, health reports kokoro unavailable.

- [ ] **Step 3:** Talk prompt template: system = station.system_prompt; user includes prev/next song titles, genre, `dj_tone`, max words. Strip quotes; hard-cut word count.

- [ ] **Step 4:** `produce_talk` → Segment(kind="talk", title=f"On air: {host}", genre_id=None, …).

- [ ] **Step 5:** Test with mocked ollama + mocked synthesize returning a short silent WAV created in test.

- [ ] **Step 6:** Commit `feat: kokoro TTS and talk producer`

---

### Task 4: ACE-Step client + SongProducer

**Files:**
- Create: `src/airadio/clients/acestep.py`, `src/airadio/producers/song.py`
- Create: `tests/test_song_producer.py`

**Interfaces:**
- Consumes: `Genre`, `StationConfig`, `ollama_chat`
- Produces: `async def generate_song(style, lyrics, duration_sec, out_path) -> None` (subprocess or Python API)
- Produces: `async def produce_song(station, genres, out_dir) -> Segment`
- Produces: `pick_genre(genres, enabled) -> Genre`

- [ ] **Step 1:** `pick_genre` — filter enabled, `random.choice`.

- [ ] **Step 2:** Ollama returns JSON: `{title, lyrics, style_line}` with instruction to emit only JSON. Parse robustly (strip fences).

- [ ] **Step 3:** `acestep.py` wrapper:
  - Prefer Python API if ACE-Step installed as package
  - Else subprocess CLI documented in README
  - **Tier-4 defaults for 8–12GB:** enable INT8 / CPU offload flags per ACE-Step docs; duration = station.song_duration_sec or genre.duration_sec
  - Env: `ACESTEP_HOME`, `ACESTEP_PYTHON`, or `acestep_cmd` in station.yaml
  - On failure: retry once with `duration_sec = max(45, duration_sec - 20)`

- [ ] **Step 4:** `produce_song` writes WAV + returns Segment with title/genre/lyrics.

- [ ] **Step 5:** Unit tests mock ACE-Step and Ollama; assert genre id set and audio_path exists.

- [ ] **Step 6:** README section: install ACE-Step 1.5, model download, VRAM tier notes.

- [ ] **Step 7:** Commit `feat: ACE-Step song producer with genre packs`

---

### Task 5: Orchestrator (live-only deep buffer)

**Files:**
- Create: `src/airadio/orchestrator.py`, `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: producers, StationConfig
- Produces: class `Orchestrator` with:
  - `async def start()` / `async def stop_workers()`
  - `async def play()` / `async def stop()`
  - `def now() -> dict`
  - `def queue_meta() -> list`
  - `async def next_audio_path() -> Path | None` (for streamer)
  - state: RadioState, current Segment | None, deque ready, asyncio.Lock, single song generation mutex

**Logic:**

```
on play():
  state = BUFFERING
  ensure workers running
  wait until len(ready) >= buffer_min OR timeout error
  state = PLAYING
  begin advancing current from ready as stream consumes

worker loop:
  while running:
    if len(ready) >= buffer_target: sleep briefly; continue
    next_kind = talk if last_enqueued was song (or empty→talk) else song
    if next_kind == song: async with gpu_lock: segment = await produce_song(...)
    else: segment = await produce_talk(..., peek prev/next if available)
    ready.append(segment)

on stop():
  state = STOPPED
  stop consuming; optionally cancel in-flight; keep ready queue on disk optional
```

- [ ] **Step 1:** Implement Orchestrator with asyncio tasks; no FastAPI coupling.

- [ ] **Step 2:** Tests with fake producers that sleep 50ms and write tiny WAVs — assert play waits for 2 segments, pattern talk/song/talk/song, stop freezes state.

- [ ] **Step 3:** Underrun: if PLAYING and ready empty and current finished → BUFFERING until one segment ready.

- [ ] **Step 4:** Commit `feat: radio orchestrator with deep buffer`

---

### Task 6: HLS streaming

**Files:**
- Create: `src/airadio/stream/hls.py`
- Modify: `src/airadio/main.py`
- Create: `tests/test_hls.py`

**Interfaces:**
- Produces: `build_hls_from_wav(wav_path, out_dir, segment_time=4) -> playlist_name`
- Streamer maintains a sliding playlist of recent HLS parts from current + upcoming segments

**Approach (pragmatic MVP):**

1. When a Segment becomes current, ffmpeg:

```bash
ffmpeg -y -i input.wav -c:a aac -b:a 192k -f hls -hls_time 4 -hls_list_size 0 -hls_segment_filename 'seg%03d.ts' index.m3u8
```

2. Serve `data/hls/current/` via `/stream/`.
3. Simpler alternative if HLS stitching is painful: **progressive endpoint** `GET /stream/audio` that concatenates PCM/WAV sequentially with correct headers — browser `<audio src>` may need MediaSource; prefer **HLS.js** in frontend.

- [ ] **Step 1:** Implement ffmpeg wrapper; skip test if ffmpeg missing (`pytest.importorskip` / shutil.which).

- [ ] **Step 2:** Wire `GET /stream/playlist.m3u8` and static segment files.

- [ ] **Step 3:** On orchestrator advance, rebuild or append playlist.

- [ ] **Step 4:** Commit `feat: HLS packaging and stream routes`

---

### Task 7: Control + now-playing API

**Files:**
- Modify: `src/airadio/main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1:** `POST /api/control` body `{"action":"play"|"stop"}` → orchestrator; 409 if health not ok on play.

- [ ] **Step 2:** `GET /api/now` → `{state, buffering_message, segment: {id,kind,title,genre_id,duration_ms} | null, station_name}`

- [ ] **Step 3:** `GET /api/queue` → list of upcoming metadata (no paths required).

- [ ] **Step 4:** pytest with TestClient + fake orchestrator.

- [ ] **Step 5:** Commit `feat: play/stop and now-playing API`

---

### Task 8: Web UI

**Files:**
- Create: `web/package.json`, `web/index.html`, `web/main.js`, `web/style.css`, `web/vite.config.js`

**UI requirements:**
- Station name from `/api/config`
- Play / Stop buttons
- Volume range input → `audio.volume`
- Now playing panel + status text (Buffering with message)
- Poll `/api/now` every 1s
- HLS: `hls.js` attach to `<audio>` when state is playing; tear down on stop

- [ ] **Step 1:** Scaffold Vite vanilla project; proxy `/api` and `/stream` to `http://127.0.0.1:8000`.

- [ ] **Step 2:** Implement controls + polling + basic dark radio aesthetic (readable, not fancy).

- [ ] **Step 3:** Manual check with mock backend or full stack.

- [ ] **Step 4:** Commit `feat: web player UI`

---

### Task 9: Integration polish + README

**Files:**
- Modify: `README.md`, `scripts/setup_check.sh`
- Create: `docs/superpowers/specs/2026-07-21-local-ai-radio-design.md` (copy Part A)
- Create: `docs/superpowers/plans/2026-07-21-local-ai-radio.md` (copy Part B)

- [ ] **Step 1:** setup_check.sh verifies: python, ffmpeg, ollama, nvidia-smi optional, espeak-ng, disk space warning for ACE-Step models.

- [ ] **Step 2:** README: architecture diagram, model install links, run commands:

```bash
# terminals
ollama serve && ollama pull qwen2.5:7b
# install ACE-Step 1.5 per upstream docs (Tier 4 / 8-12GB)
uvicorn airadio.main:app --app-dir src --host 127.0.0.1 --port 8000
cd web && npm i && npm run dev
```

- [ ] **Step 3:** Loudness normalize talk + song to similar integrated loudness if ffmpeg available.

- [ ] **Step 4:** End-to-end manual test script checklist in README.

- [ ] **Step 5:** Commit `docs: install guide and design/plan artifacts`

---

## Spec coverage checklist

| Requirement | Task |
|-------------|------|
| Local TTS Kokoro | 3 |
| Local music ACE-Step | 4 |
| Local LLM Ollama | 2, 3, 4 |
| Endless alternating talk/music | 5 |
| Live-only deep buffer | 5 |
| 10 genres random | 1, 4 |
| Customizable station | 1 |
| Play/stop, volume, now playing | 7, 8 |
| Offline-only | clients never call cloud |
| 8–12GB aware | 4 ACE-Step tier defaults |
| Localhost webapp | 8 |
| Health / failures | 2, 5, 7 |

## Risks called out for implementers

1. ACE-Step install is the heaviest dependency — isolate behind `clients/acestep.py` with clear errors.
2. First Play can take minutes — buffering UI is not optional.
3. Only one ACE-Step job at a time (GPU lock).
4. Prefer Ollama on CPU (`num_gpu: 0` in request options if supported) while generating music.

---

## Execution handoff (after plan mode exit)

When you leave plan mode / approve execution:

1. Write Part A → `docs/superpowers/specs/2026-07-21-local-ai-radio-design.md`
2. Write Part B → `docs/superpowers/plans/2026-07-21-local-ai-radio.md`
3. Choose:
   - **Subagent-driven** (recommended): one task per subagent + review
   - **Inline**: execute tasks in this session with checkpoints

**Suggested first implementation slice:** Tasks 1→2→3 (config + talk path) so you hear a DJ voice before fighting ACE-Step install.
