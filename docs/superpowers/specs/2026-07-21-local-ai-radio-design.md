# Local AI Radio Station — Design Spec

**Date:** 2026-07-21  
**Status:** Approved  
**Branch:** `feat/local-ai-radio`

## Goals

Build a **100% offline** AI radio that:

1. Alternates **talk → music → talk → music** forever  
2. Uses local **Ollama** (scripts), **Kokoro-82M** (TTS), **ACE-Step 1.5** (music)  
3. Streams from a **live-only deep buffer** (generate ahead while listening)  
4. Exposes a localhost web UI: play/stop, volume, now playing  
5. Picks from **10 curated genres** at random; station personality is YAML-configurable  

## Constraints

- Hardware: **8–12GB** NVIDIA VRAM; TTS + prefer LLM on **CPU**  
- Quality first over speed  
- Localhost single listener  
- License-safe: Kokoro Apache 2.0, ACE-Step MIT (not LeVo 2)  
- ACE-Step Tier 4 settings for 8–12GB; song duration ~75s  
- Cold start may take minutes; UI must show buffering  

## Architecture

```
Browser → FastAPI (/api/*, /stream/*)
            → Orchestrator (queue, BUFFERING|PLAYING|STOPPED)
                 → TalkProducer: Ollama → Kokoro (CPU)
                 → SongProducer: Ollama → ACE-Step (GPU, exclusive lock)
            → Segment store + HLS (ffmpeg) / WAV fallback
```

## Research

| Role | Choice | Rationale |
|------|--------|-----------|
| TTS | Kokoro-82M | Best lightweight local quality; CPU-friendly |
| Music | ACE-Step 1.5 | Best open commercial-friendly full-song model |
| Avoid | LeVo 2 | Best sound, non-commercial license |

## Config

- `config/station.yaml` — name, host, prompts, voices, buffers  
- `config/genres/*.yaml` — 10 genre packs with style/lyric/dj_tone  

## Non-goals

Multi-user streaming, cloud APIs, voice cloning, mobile apps, accounts.

See also: `docs/superpowers/plans/2026-07-21-local-ai-radio.md`
