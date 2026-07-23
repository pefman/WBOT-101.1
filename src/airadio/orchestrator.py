from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from airadio.audio.process import (
    build_skip_crossfade,
    build_song_talk_continuous,
    build_talk_song_continuous,
    extract_wav_from,
    probe_duration_ms,
)
from airadio.stream.hls import copy_wav_as_fallback
from airadio.library import SongLibrary, garbage_collect_segments
from airadio.models_types import Genre, RadioState, Segment, StationConfig
from airadio.producers.song import produce_song
from airadio.producers.talk import produce_talk
from airadio.stream.hls import build_hls_from_wav, copy_wav_as_fallback

log = logging.getLogger(__name__)

TalkFn = Callable[..., Awaitable[Segment]]
SongFn = Callable[..., Awaitable[Segment]]

# Human labels for generation stages (API + UI)
_STAGE_LABELS: dict[str, str] = {
    "idle": "",
    "talk_writing": "Writing DJ script",
    "talk_speaking": "Speaking (TTS)",
    "talk_finalize": "Finalizing talk audio",
    "song_identity": "Inventing artist & title",
    "song_lyrics": "Writing lyrics",
    "song_unload_llm": "Freeing GPU for music",
    "song_music": "Composing music (ACE-Step)",
    "song_finalize": "Finalizing track",
    "song_reair": "Pulling a library keeper",
    "packaging": "Packaging stream",
    "buffering": "Buffering",
}


class Orchestrator:
    def __init__(
        self,
        station: StationConfig,
        genres: dict[str, Genre],
        *,
        talk_fn: TalkFn | None = None,
        song_fn: SongFn | None = None,
    ) -> None:
        self.station = station
        self.genres = genres
        self.talk_fn = talk_fn or produce_talk
        self.song_fn = song_fn or produce_song

        self.state = RadioState.STOPPED
        self.buffering_message = ""
        self.current: Segment | None = None
        self.ready: deque[Segment] = deque()
        self._last_enqueued_kind: str | None = None
        self._history: deque[Segment] = deque(maxlen=20)
        # Songs that have finished airplay (most recent last); UI shows last 5
        self._played_songs: deque[Segment] = deque(maxlen=8)
        self.mood_id: str | None = None
        self.mood_label: str | None = None
        self.dj_id: str | None = None
        self.dj_blurb: str | None = None
        self._dj_personality: str = ""
        self.recent_talk_texts: deque[str] = deque(maxlen=8)
        self._dj_voice_samples: list[str] = []
        self._system_template: str = (
            station.system_prompt_template or station.system_prompt
        )
        # Bumped on every DJ/voice change so in-flight talk can be discarded
        self.dj_generation: int = 0
        # Bumped on genre change so in-flight / wrong-genre songs are discarded
        self.song_generation: int = 0
        self._skip_current: bool = False
        # Wall-clock length of the *current* metadata segment on air
        self._current_play_ms: int | None = None
        # Bumped only when the HLS/WAV package is rewritten (not on talk→song meta switch)
        self.stream_id: int = 0

        # Live generation progress for UI
        self.generation_stage: str = "idle"
        self.generation_detail: str = ""
        # Listener talk requests (FIFO)
        self._talk_requests: deque[str] = deque(maxlen=20)

        # Cache for _recent_song_pairs (invalidated when history/played_songs change)
        self._recent_pairs_cache: list[tuple[str, str]] | None = None

        self._worker_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._running = False
        self._gpu_lock = asyncio.Lock()
        self._cv = asyncio.Condition()
        self._play_event = asyncio.Event()
        self.segment_started_at: float | None = None

        self.segments_dir = station.data_dir / "segments"
        self.hls_dir = station.data_dir / "hls" / "current"
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.hls_dir.mkdir(parents=True, exist_ok=True)

        self.library = SongLibrary.load(
            station.data_dir,
            max_songs=station.library_max_songs,
        )
        # Occasional GC counter
        self._gens_since_gc: int = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="radio-worker")

    async def stop_workers(self) -> None:
        self._running = False
        self._play_event.clear()
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self.state = RadioState.STOPPED

    def _set_stage(self, stage: str, detail: str = "") -> None:
        self.generation_stage = stage or "idle"
        self.generation_detail = detail or _STAGE_LABELS.get(stage, "")
        if self.state == RadioState.BUFFERING:
            self._refresh_buffering_message()

    def _refresh_buffering_message(self) -> None:
        ready = len(self.ready)
        need = self.station.buffer_min
        target = self.station.buffer_target
        stage = self.generation_stage
        detail = self.generation_detail or _STAGE_LABELS.get(stage, "")
        base = f"Buffering… {ready}/{need} ready (target {target})"
        if stage and stage != "idle" and detail:
            self.buffering_message = f"{base} · {detail}"
        elif stage and stage != "idle":
            label = _STAGE_LABELS.get(stage, stage)
            self.buffering_message = f"{base} · {label}" if label else base
        else:
            self.buffering_message = base

    def _generation_snapshot(self) -> dict:
        ready = len(self.ready)
        need = max(1, int(self.station.buffer_min))
        target = max(need, int(self.station.buffer_target))
        # Rough progress: segments ready toward min, plus stage weight while generating
        stage = self.generation_stage
        stage_frac = {
            "idle": 0.0,
            "talk_writing": 0.15,
            "talk_speaking": 0.35,
            "talk_finalize": 0.45,
            "song_identity": 0.1,
            "song_lyrics": 0.2,
            "song_unload_llm": 0.25,
            "song_music": 0.55,
            "song_finalize": 0.85,
            "song_reair": 0.5,
            "packaging": 0.9,
            "buffering": 0.05,
        }.get(stage, 0.2 if stage != "idle" else 0.0)
        if ready >= need:
            progress = 1.0 if self.state != RadioState.BUFFERING else 0.95
        else:
            progress = min(0.99, (ready + stage_frac) / need)
        return {
            "stage": stage,
            "stage_label": _STAGE_LABELS.get(stage, stage),
            "detail": self.generation_detail
            or _STAGE_LABELS.get(stage, "")
            or self.buffering_message,
            "ready": ready,
            "buffer_min": need,
            "buffer_target": target,
            "progress": round(progress, 3),
            "pending_requests": len(self._talk_requests),
        }

    async def _pre_package_with_retry(self, first: Segment, nxt: Segment | None) -> None:
        """Pre-package first segment with retry logic (2 attempts, exponential backoff)."""
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                await asyncio.to_thread(self._prepare_stream_wav, first, nxt)
                log.info("Pre-package succeeded on attempt %d", attempt)
                return
            except Exception as exc:  # noqa: BLE001
                if attempt < max_attempts:
                    backoff_sec = 2 ** (attempt - 1)  # 1s, 2s
                    log.warning(
                        "Pre-package attempt %d failed: %s (retrying in %ds)",
                        attempt,
                        exc,
                        backoff_sec,
                    )
                    await asyncio.sleep(backoff_sec)
                else:
                    log.error("Pre-package failed after %d attempts: %s", max_attempts, exc)
                    raise RuntimeError(f"Failed to package audio stream: {exc}") from exc

    async def play(self) -> None:
        await self.start()
        self.state = RadioState.BUFFERING
        self._set_stage("buffering", "Generating first segments (talk + song)…")
        self._play_event.set()
        log.info(
            "▶ PLAY — buffering to %d segment(s) (DJ=%s, genres=%d)",
            self.station.buffer_min,
            self.station.host_name,
            len(self.station.enabled_genres),
        )

        # Wait until buffer_min ready
        deadline = time.time() + 600  # 10 min cold start allowance
        while len(self.ready) < self.station.buffer_min:
            if time.time() > deadline:
                self.state = RadioState.STOPPED
                self.buffering_message = "Timed out waiting for buffer"
                self._set_stage("idle", "")
                raise TimeoutError(self.buffering_message)
            self._refresh_buffering_message()
            await asyncio.sleep(0.5)

        # Pre-package the first segment so the stream URL works as soon as we go on air
        if self.ready:
            self._set_stage("packaging", "Packaging audio stream…")
            log.info("▶ Packaging first stream segment for air…")
            first = self.ready[0]
            nxt = self.ready[1] if len(self.ready) > 1 else None
            await self._pre_package_with_retry(first, nxt)

        self.state = RadioState.PLAYING
        self.buffering_message = ""
        self._set_stage("idle", "")
        log.info("▶ ON AIR — queue has %d segment(s)", len(self.ready))
        if not self._playback_task or self._playback_task.done():
            self._playback_task = asyncio.create_task(
                self._playback_loop(), name="radio-playback"
            )

    async def stop(self) -> None:
        self._play_event.clear()
        self.state = RadioState.STOPPED
        self.buffering_message = ""
        self._set_stage("idle", "")
        # Invalidate anything mid-generation and drop the buffer so the next
        # Play starts fresh with the current DJ / mood / voice.
        self.dj_generation += 1
        self._skip_current = True
        self._current_play_ms = None
        self.ready.clear()
        self._last_enqueued_kind = None
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
        self.current = None
        self.segment_started_at = None
        log.info("■ STOP — queue cleared; generation waits for Play")

    def skip(self) -> dict:
        """Skip the currently playing segment (and continuous talk→song tail)."""
        if self.state not in (RadioState.PLAYING, RadioState.BUFFERING):
            return {
                "ok": False,
                "skipped": False,
                "reason": "not_on_air",
                "state": self.state.value,
            }
        if self.current is None and not self.ready:
            return {
                "ok": False,
                "skipped": False,
                "reason": "nothing_to_skip",
                "state": self.state.value,
            }
        title = self.current.title if self.current else "(buffering)"
        self._skip_current = True
        log.info("⏭ SKIP requested — cutting «%s»", title)
        return {
            "ok": True,
            "skipped": True,
            "title": title,
            "state": self.state.value,
        }

    def queue_talk_request(self, text: str) -> dict:
        cleaned = " ".join((text or "").strip().split())
        if not cleaned:
            raise ValueError("request text is empty")
        if len(cleaned) > 400:
            cleaned = cleaned[:400].rstrip() + "…"
        self._talk_requests.append(cleaned)
        log.info("Listener request queued (%d pending): %s", len(self._talk_requests), cleaned[:80])
        return {
            "ok": True,
            "queued": cleaned,
            "pending": len(self._talk_requests),
        }

    def pending_requests(self) -> list[str]:
        return list(self._talk_requests)

    def favorite_song(self, segment_id: str, *, favorite: bool = True) -> dict:
        entry = self.library.set_favorite(segment_id, favorite=favorite)
        if entry is None:
            # Try promote from played history
            for seg in list(self._played_songs) + list(self._history):
                if seg.id == segment_id and seg.kind == "song":
                    self.library.remember(seg, favorite=favorite)
                    entry = self.library.entries.get(segment_id)
                    break
        if entry is None:
            raise ValueError(f"Unknown song id: {segment_id}")
        return {
            "id": entry.id,
            "title": entry.title,
            "artist": entry.artist,
            "favorite": entry.favorite,
        }

    def now(self) -> dict:
        seg_meta = None
        if self.current:
            seg_meta = self.current.meta()
            if self._current_play_ms is not None:
                seg_meta["duration_ms"] = self._current_play_ms
        gen = self._generation_snapshot()
        # Prefer stage detail while buffering; keep legacy field in sync
        msg = self.buffering_message
        if self.state == RadioState.BUFFERING and gen.get("detail"):
            msg = gen["detail"] if "Buffering" in (gen["detail"] or "") else (
                f"Buffering… {gen['ready']}/{gen['buffer_min']} · {gen['detail']}"
            )
            self.buffering_message = msg
        return {
            "state": self.state.value,
            "buffering_message": msg,
            "generation": gen,
            "segment": seg_meta,
            "station_name": self.station.name,
            "queue_depth": len(self.ready),
            "segment_started_at": self.segment_started_at,
            "mood_id": self.mood_id,
            "mood_label": self.mood_label,
            "dj_id": self.dj_id,
            "dj_name": self.station.host_name,
            "dj_blurb": self.dj_blurb,
            "primary_voice": self.station.primary_voice,
            "enabled_genres": list(self.station.enabled_genres),
            "crossfade_sec": self.station.crossfade_sec,
            "language": self.station.language,
            # Client must only re-attach audio when this changes (not on meta handoff)
            "stream_id": self.stream_id,
            "pending_requests": gen["pending_requests"],
        }

    def queue_meta(self) -> list[dict]:
        return [s.meta() for s in self.ready]

    def played_songs_meta(self, *, limit: int = 5) -> list[dict]:
        """Most recently finished songs first (newest at top)."""
        items = list(self._played_songs)
        items.reverse()
        out: list[dict] = []
        for s in items[:limit]:
            m = s.meta()
            entry = self.library.entries.get(s.id)
            m["favorite"] = bool(entry.favorite) if entry else False
            out.append(m)
        return out

    def _record_played_song(self, seg: Segment | None) -> None:
        if seg is None or seg.kind != "song":
            return
        # Avoid duplicate if same id re-recorded
        if self._played_songs and self._played_songs[-1].id == seg.id:
            return
        self._played_songs.append(seg)
        self._recent_pairs_cache = None  # Invalidate cache
        try:
            self.library.remember(seg)
        except Exception as exc:  # noqa: BLE001
            log.warning("Library remember failed: %s", exc)

    def set_system_template(self, template: str) -> None:
        self._system_template = template

    def set_language(self, language: str) -> dict:
        """Switch language for song lyrics / ACE vocals only (not DJ TTS)."""
        from airadio.languages import get_language

        lang = get_language(language)
        self.station.language = lang.id
        log.info(
            "Music language set to %s (%s) — DJ talk stays English",
            lang.id,
            lang.prompt_name,
        )
        return {
            "language": lang.id,
            "label": lang.label,
            "native": lang.native,
        }

    def drop_pending_talks(self) -> int:
        """Remove not-yet-played talk segments (old host/voice still on disk/queue)."""
        kept: deque[Segment] = deque()
        removed = 0
        for seg in self.ready:
            if seg.kind == "talk":
                removed += 1
                continue
            kept.append(seg)
        self.ready = kept
        if kept:
            self._last_enqueued_kind = kept[-1].kind
        elif self.current:
            self._last_enqueued_kind = self.current.kind
        else:
            self._last_enqueued_kind = None
        if removed:
            log.info("Dropped %d pending talk segment(s) from queue", removed)
        return removed

    def _bump_talk_generation(self, *, skip_current_talk: bool = True) -> None:
        """Invalidate in-flight talk and optionally cut short the on-air talk break."""
        self.dj_generation += 1
        if skip_current_talk and self.current and self.current.kind == "talk":
            self._skip_current = True
            log.info(
                "Skipping current talk mid-play (was %s) after host/voice change",
                self.current.title,
            )

    def set_voice(self, voice_id: str, *, clear_pending_talk: bool = True) -> dict:
        """Change voice for future talk; drop queued talk so old audio is not played."""
        self.station.primary_voice = voice_id
        self._bump_talk_generation(skip_current_talk=clear_pending_talk)
        removed = self.drop_pending_talks() if clear_pending_talk else 0
        log.info(
            "Voice set to %s (DJ %s) gen=%d removed_pending_talk=%d",
            voice_id,
            self.dj_id,
            self.dj_generation,
            removed,
        )
        return {
            "voice_id": voice_id,
            "dj_id": self.dj_id,
            "dj_name": self.station.host_name,
            "removed_pending_talk": removed,
            "queue_depth": len(self.ready),
        }

    def _recent_song_pairs(self) -> list[tuple[str, str]]:
        """Artist/title pairs from history + played (for anti-repeat prompts). Cached."""
        if self._recent_pairs_cache is not None:
            return self._recent_pairs_cache
        
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for seg in list(self._history) + list(self._played_songs):
            if seg.kind != "song":
                continue
            artist = (seg.artist or "").strip() or "Unknown"
            title = (seg.title or "").strip()
            if not title:
                continue
            key = f"{artist.casefold()}|{title.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            out.append((artist, title))
        result = out[-20:]
        self._recent_pairs_cache = result
        return result

    def set_dj(
        self,
        dj_id: str,
        *,
        name: str,
        personality: str,
        voice: str,
        blurb: str = "",
        apply_voice: bool = True,
        clear_pending_talk: bool = True,
        voice_samples: list[str] | None = None,
    ) -> dict:
        """Switch on-air host: name + personality + optional default voice.

        Drops queued talk by default — those segments were already TTS'd with the
        previous host's voice/script and would keep playing as the wrong DJ.
        Also invalidates in-flight talk generation and skips the current talk.
        """
        from airadio.config import build_system_prompt

        self.dj_id = dj_id
        self.dj_blurb = blurb
        self._dj_personality = personality
        self._dj_voice_samples = list(voice_samples or [])
        self.station.host_name = name
        self.station.system_prompt = build_system_prompt(
            self._system_template,
            station_name=self.station.name,
            host_name=name,
            personality=personality,
        )
        if apply_voice and voice:
            self.station.primary_voice = voice
        if clear_pending_talk:
            self._bump_talk_generation(skip_current_talk=True)
            removed = self.drop_pending_talks()
        else:
            removed = 0
        log.info(
            "DJ set to %s (%s) voice=%s gen=%d removed_pending_talk=%d",
            dj_id,
            name,
            self.station.primary_voice,
            self.dj_generation,
            removed,
        )
        return {
            "dj_id": dj_id,
            "name": name,
            "blurb": blurb,
            "voice": self.station.primary_voice,
            "host_name": self.station.host_name,
            "removed_pending_talk": removed,
            "queue_depth": len(self.ready),
        }

    def set_genres(
        self,
        genre_ids: list[str],
        *,
        clear_pending_songs: bool = True,
        label: str | None = None,
    ) -> dict:
        """Set which genres may be used for upcoming songs."""
        if not genre_ids:
            raise ValueError("enable at least one genre")
        unknown = [g for g in genre_ids if g not in self.genres]
        if unknown:
            raise ValueError(f"unknown genres: {unknown}")

        # Dedupe, preserve order
        seen: set[str] = set()
        ordered: list[str] = []
        for gid in genre_ids:
            if gid not in seen:
                seen.add(gid)
                ordered.append(gid)

        self.station.enabled_genres = ordered
        self.mood_id = "custom"
        self.mood_label = label or (
            f"{len(ordered)} genre{'s' if len(ordered) != 1 else ''}"
            if len(ordered) != 1
            else (self.genres[ordered[0]].name if ordered[0] in self.genres else ordered[0])
        )
        removed_songs = 0
        removed_talks = 0
        if clear_pending_songs:
            # Invalidate mid-flight song generations so a metal pick never
            # airs an indie track that was already composing.
            self.song_generation += 1
            # Also drop queued talk — those breaks often name the last track
            # from the old mix ("that was a reggae cut…") and feel wrong.
            self.dj_generation += 1
            kept: deque[Segment] = deque()
            for seg in self.ready:
                if seg.kind == "song":
                    removed_songs += 1
                    continue
                if seg.kind == "talk":
                    removed_talks += 1
                    continue
                kept.append(seg)
            self.ready = kept
            # Fresh alternate pattern after a genre wipe
            if self.current:
                self._last_enqueued_kind = self.current.kind
            else:
                self._last_enqueued_kind = None

        log.info(
            "Genres set (%s) n=%d song_gen=%d removed_songs=%d removed_talks=%d",
            ordered,
            len(ordered),
            self.song_generation,
            removed_songs,
            removed_talks,
        )
        return {
            "enabled_genres": list(ordered),
            "mood_id": self.mood_id,
            "mood_label": self.mood_label,
            "removed_pending_songs": removed_songs,
            "removed_pending_talks": removed_talks,
            "queue_depth": len(self.ready),
        }

    def set_mood(
        self,
        mood_id: str,
        *,
        label: str,
        genre_ids: list[str],
        clear_pending_songs: bool = True,
    ) -> dict:
        """Legacy mood helper — maps to set_genres."""
        return self.set_genres(
            genre_ids, clear_pending_songs=clear_pending_songs, label=label
        )

    def _on_gen_stage(self, stage: str, detail: str = "") -> None:
        self._set_stage(stage, detail)
        if self.state == RadioState.PLAYING and detail:
            # Soft status while already on air (worker filling buffer)
            pass

    def _maybe_gc(self) -> None:
        self._gens_since_gc += 1
        if self._gens_since_gc < 4:
            return
        self._gens_since_gc = 0
        protect = self.library.protected_paths()
        for seg in list(self.ready) + ([self.current] if self.current else []):
            if seg and seg.audio_path:
                try:
                    protect.add(Path(seg.audio_path).resolve())
                except OSError:
                    pass
            if seg and seg.cover_path:
                try:
                    protect.add(Path(seg.cover_path).resolve())
                except OSError:
                    pass
        try:
            garbage_collect_segments(
                self.segments_dir,
                protect=protect,
                max_age_hours=self.station.segment_max_age_hours,
                max_files=self.station.segment_max_files,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Segment GC failed: %s", exc)

    async def _worker_loop(self) -> None:
        """Fill the air buffer only while Play is active (not on idle / DJ pick)."""
        while self._running:
            try:
                # Off air: pick DJ/mood/voice freely — no LLM/TTS/music until Play
                if not self._play_event.is_set():
                    await asyncio.sleep(0.25)
                    continue

                if len(self.ready) >= self.station.buffer_target:
                    if self.generation_stage != "idle":
                        self._set_stage("idle", "")
                    await asyncio.sleep(0.5)
                    continue

                next_kind = self._next_kind()
                gen = self.dj_generation
                song_gen = self.song_generation
                enabled_at_start = list(self.station.enabled_genres)
                q = len(self.ready)
                if next_kind == "song":
                    log.info(
                        "▶ Creating SONG (queue %d/%d, genres=%s)…",
                        q,
                        self.station.buffer_target,
                        ",".join(enabled_at_start[:6])
                        + ("…" if len(enabled_at_start) > 6 else ""),
                    )
                    # Maybe re-air a keeper instead of full ACE generation
                    exclude = {s.id for s in self.ready if s.kind == "song"}
                    if self.current and self.current.kind == "song":
                        exclude.add(self.current.id)
                    for s in self._played_songs:
                        # Prefer not re-airing the very last played track
                        exclude.add(s.id)
                    reair = self.library.pick_reair(
                        enabled_genres=list(self.station.enabled_genres),
                        exclude_ids=exclude,
                        chance=self.station.reair_chance,
                    )
                    if reair is not None:
                        self._set_stage(
                            "song_reair",
                            f"Re-airing «{reair.artist or '?'} — {reair.title}»…",
                        )
                        seg = reair
                    else:
                        async with self._gpu_lock:
                            seg = await self.song_fn(
                                self.station,
                                self.genres,
                                self.segments_dir,
                                recent_songs=self._recent_song_pairs(),
                                on_stage=self._on_gen_stage,
                            )
                    # Genre filter changed mid-flight — never air the wrong pack
                    if self.song_generation != song_gen:
                        log.info(
                            "Discarding song «%s» (genre filter changed during generate)",
                            getattr(seg, "title", "?"),
                        )
                        continue
                    # Reject songs outside the enabled filter (radio = freeform OK)
                    if seg.kind == "song" and seg.genre_id:
                        en = set(self.station.enabled_genres)
                        concrete = en - {"radio"}
                        freeform = "radio" in en and not concrete
                        if not freeform and seg.genre_id not in concrete:
                            log.warning(
                                "Discarding song «%s» genre=%s not in enabled %s",
                                seg.title,
                                seg.genre_id,
                                self.station.enabled_genres,
                            )
                            continue
                else:
                    log.info(
                        "▶ Creating TALK break (queue %d/%d, host=%s)…",
                        q,
                        self.station.buffer_target,
                        self.station.host_name,
                    )
                    prev = self._prev_song_for_new_talk()
                    # Never treat songs already in the queue as "coming up" —
                    # they air *before* this talk (see _next_song_for_new_talk).
                    next_song = self._next_song_for_new_talk()
                    host_at_start = self.station.host_name
                    voice_at_start = self.station.primary_voice
                    user_req = None
                    if self._talk_requests:
                        user_req = self._talk_requests.popleft()
                    log.info(
                        "  [talk] context prev=%s next=%s",
                        f"{prev.artist} — {prev.title}" if prev else "—",
                        f"{next_song.artist} — {next_song.title}" if next_song else "—",
                    )
                    seg = await self.talk_fn(
                        self.station,
                        self.segments_dir,
                        prev_song=prev,
                        next_song=next_song,
                        dj_tone=None,
                        mood_label=self.mood_label,
                        mood_genres=list(self.station.enabled_genres),
                        recent_talk=list(self.recent_talk_texts),
                        generation_id=gen,
                        voice_samples=list(self._dj_voice_samples),
                        user_request=user_req,
                        on_stage=self._on_gen_stage,
                    )
                    # DJ/voice changed while LLM+TTS ran — do not air the stale break
                    if self.dj_generation != gen or (
                        seg.host_name and seg.host_name != self.station.host_name
                    ):
                        log.warning(
                            "Discarding stale talk «%s» (gen %s→%s, host %s→%s, voice %s→%s)",
                            seg.title,
                            gen,
                            self.dj_generation,
                            host_at_start,
                            self.station.host_name,
                            voice_at_start,
                            self.station.primary_voice,
                        )
                        # Re-queue request if we consumed one and talk was discarded
                        if user_req:
                            self._talk_requests.appendleft(user_req)
                        continue

                # Stopped mid-generation — never enqueue for a dead session
                if not self._play_event.is_set() or self.dj_generation != gen:
                    log.info(
                        "Discarding %s «%s» (stopped or host changed during generate)",
                        seg.kind,
                        seg.title,
                    )
                    continue

                self.ready.append(seg)
                self._last_enqueued_kind = seg.kind
                self._history.append(seg)
                self._recent_pairs_cache = None  # Invalidate cache
                if seg.kind == "talk" and seg.text:
                    self.recent_talk_texts.append(seg.text)
                dur = (seg.duration_ms or 0) / 1000.0
                log.info(
                    "✓ Ready %s «%s» (%.1fs) — queue now %d/%d",
                    seg.kind.upper(),
                    seg.title,
                    dur,
                    len(self.ready),
                    self.station.buffer_target,
                )
                if self.state == RadioState.BUFFERING:
                    self._refresh_buffering_message()
                elif len(self.ready) >= self.station.buffer_target:
                    self._set_stage("idle", "")
                self._maybe_gc()
                async with self._cv:
                    self._cv.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Worker error: %s", exc)
                self.buffering_message = f"Generation error: {exc}"
                self._set_stage("idle", f"Generation error: {exc}")
                await asyncio.sleep(2.0)

    def _peek_next_song_in_queue(self) -> Segment | None:
        """First song waiting in the ready queue (plays before anything we append)."""
        for seg in self.ready:
            if seg.kind == "song":
                return seg
        return None

    def _prev_song_for_new_talk(self) -> Segment | None:
        """Song that will have just finished when a *newly enqueued* talk airs.

        Ready queue is FIFO and we always append. Anything already in ``ready``
        (and the current on-air song) plays *before* the talk we are about to
        create — so the last of those songs is "just played", never "coming up".
        """
        for seg in reversed(self.ready):
            if seg.kind == "song":
                return seg
        if self.current and self.current.kind == "song":
            return self.current
        return self._last_song_from_history()

    def _next_song_for_new_talk(self) -> Segment | None:
        """Song that will air *after* a newly enqueued talk.

        Because we only append, nothing in ``ready`` plays after the new talk.
        Returning a queued song as "coming up" makes the DJ tease a track that
        already played — e.g. "here's Song X again" after a Skip. Always None
        unless we later pre-build a follow-up song before the talk.
        """
        return None

    def _next_kind(self) -> str:
        if self._last_enqueued_kind is None:
            return "talk"
        return "song" if self._last_enqueued_kind == "talk" else "talk"

    def _last_song_from_history(self) -> Segment | None:
        """Song the DJ may call 'just played' — only tracks that actually aired.

        Never use the generate queue/history alone: after a restart or genre
        change that would make the host name a track the listener never heard
        this session (or from the wrong genre).
        """
        if self.current and self.current.kind == "song":
            return self.current
        for s in reversed(self._played_songs):
            if s.kind == "song":
                return s
        return None

    async def _playback_loop(self) -> None:
        while self._play_event.is_set():
            if not self.ready:
                self.state = RadioState.BUFFERING
                self.buffering_message = "Buffer underrun — generating more…"
                try:
                    async with self._cv:
                        await asyncio.wait_for(self._cv.wait(), timeout=300)
                except asyncio.TimeoutError:
                    self.state = RadioState.STOPPED
                    self.buffering_message = "Stopped: could not refill buffer"
                    return
                if not self.ready:
                    continue
                self.state = RadioState.PLAYING
                self.buffering_message = ""

            seg = self.ready.popleft()
            next_seg = self.ready[0] if self.ready else None
            self._skip_current = False

            try:
                self._set_stage("packaging", "Packaging stream…")
                plan = await asyncio.to_thread(self._prepare_stream_wav, seg, next_seg)
            except Exception as exc:  # noqa: BLE001
                log.exception("HLS package failed: %s", exc)
                plan = {
                    "wav": seg.audio_path,
                    "talk_ms": seg.duration_ms,
                    "total_ms": seg.duration_ms,
                    "consume_song": False,
                    "song": None,
                    "continuous": False,
                }

            try:
                self._package_stream(plan["wav"])
            except Exception as exc:  # noqa: BLE001
                log.exception("Stream package failed: %s", exc)
            finally:
                if self.state == RadioState.PLAYING:
                    self._set_stage("idle", "")

            # --- Phase 1: first half of package (talk, or song before outro) ---
            self.current = seg
            self.segment_started_at = time.time()
            phase1_ms = int(plan.get("talk_ms") or plan.get("phase1_ms") or seg.duration_ms)
            self._current_play_ms = phase1_ms
            log.info(
                "♫ On air: %s «%s» (%.1fs)",
                seg.kind.upper(),
                seg.title,
                phase1_ms / 1000.0,
            )
            try:
                skipped = await self._sleep_interruptible(max(0.5, phase1_ms / 1000.0))
            except asyncio.CancelledError:
                raise

            if not self._play_event.is_set():
                # Stopped mid-segment: still count a song if it was on air
                if seg.kind == "song":
                    self._record_played_song(seg)
                continue
            if skipped:
                # Blend into next voice/track instead of a hard cut
                if seg.kind == "song":
                    self._record_played_song(seg)
                blended = await self._skip_blend_into_next(seg, plan, phase1_ms)
                if not blended:
                    log.info("Skip with no next segment ready — hard cut")
                continue

            # Plain song (no continuous handoff) finished airplay
            if seg.kind == "song" and not plan.get("continuous"):
                self._record_played_song(seg)

            # --- Phase 2: same stream, hand off metadata (talk→song or song→talk) ---
            # Legacy key "song" holds the next segment (may be talk on outro handoff)
            handoff: Segment | None = plan.get("song") or plan.get("next")
            if plan.get("continuous") and handoff is not None:
                if plan.get("consume_song") and self.ready and self.ready[0].id == handoff.id:
                    self.ready.popleft()
                # Song that was phase 1 is done when we open the mic on the outro
                if seg.kind == "song":
                    self._record_played_song(seg)
                # Stream keeps playing — do not re-package; only flip now-playing
                self.current = handoff
                self.segment_started_at = time.time()
                phase2_ms = max(500, int(plan["total_ms"]) - phase1_ms)
                self._current_play_ms = phase2_ms
                log.info(
                    "Seamless handoff to %s «%s» (%.1fs left on same stream)",
                    handoff.kind,
                    handoff.title,
                    phase2_ms / 1000.0,
                )
                try:
                    skipped = await self._sleep_interruptible(phase2_ms / 1000.0)
                except asyncio.CancelledError:
                    raise
                # Talk→song: record the song that was phase 2
                if handoff.kind == "song":
                    self._record_played_song(handoff)
                if skipped:
                    # Skip mid phase-2 (e.g. mid-talk after song bed) → blend to following
                    await self._skip_blend_into_next(handoff, plan, phase2_ms)
                    continue

        self.current = None
        self.segment_started_at = None
        self._current_play_ms = None

    @staticmethod
    def _talk_names_song(talk: Segment, song: Segment) -> bool:
        """True if talk script likely teases/names this song (stale after Skip)."""
        if talk.kind != "talk" or not talk.text:
            return False
        blob = talk.text.casefold()
        title = (song.title or "").strip()
        artist = (song.artist or "").strip()
        if title and len(title) >= 4 and title.casefold() in blob:
            return True
        if artist and len(artist) >= 4 and artist.casefold() in blob:
            # Weak signal alone; require play/coming language nearby
            if any(
                w in blob
                for w in (
                    "coming up",
                    "up next",
                    "next up",
                    "about to",
                    "gonna play",
                    "going to play",
                    "here's",
                    "here is",
                )
            ):
                return True
        return False

    def _package_stream_fast_wav(self, wav_path) -> None:
        """Skip path: write WAV fallback first and bump stream_id (no HLS wait).

        Client soft-swaps on stream_id using current.wav — avoids the silence
        while ffmpeg builds a full HLS set.
        """
        from pathlib import Path

        wav_path = Path(wav_path)
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.stream_id += 1
        dest = copy_wav_as_fallback(wav_path, self.hls_dir)
        log.info(
            "Skip package WAV stream_id=%s → %s",
            self.stream_id,
            dest,
        )
        # Best-effort HLS in the background for other clients (non-blocking)
        def _hls_bg() -> None:
            try:
                from airadio.stream.hls import build_hls_from_wav

                build_hls_from_wav(wav_path, self.hls_dir)
            except Exception as exc:  # noqa: BLE001
                log.debug("Background HLS after skip failed: %s", exc)

        try:
            import threading

            threading.Thread(target=_hls_bg, name="skip-hls", daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    async def _skip_blend_into_next(
        self,
        from_seg: Segment,
        plan: dict,
        phase_ms: int,
    ) -> bool:
        """Crossfade/jump into the next segment without a hard silence gap.

        Prefer jumping into an already-built continuous air file (song→talk)
        at the DJ open — that avoids replaying mid-song as a bed.
        """
        if not self._play_event.is_set():
            return False

        # Prefer the continuous package's planned next, else queue head
        incoming: Segment | None = None
        if plan.get("continuous"):
            cand = plan.get("next") or plan.get("song")
            if cand is not None:
                incoming = cand
        if incoming is None and self.ready:
            incoming = self.ready[0]
        if incoming is None:
            return False
        if not incoming.audio_path.is_file():
            return False

        out = self.segments_dir / f"{from_seg.id}_skip_{incoming.id}.wav"
        total_ms = 0
        tail_ms = 0

        try:
            self._set_stage("packaging", "Skip · blending into next…")

            # Fast path: continuous song→talk air file already has DJ under bed.
            # Jump to the handoff point — no “song restarts then ducks”.
            air_wav = plan.get("wav")
            handoff_kind = plan.get("handoff") or ""
            phase1 = int(plan.get("phase1_ms") or plan.get("talk_ms") or 0)
            if (
                plan.get("continuous")
                and handoff_kind == "song_to_talk"
                and air_wav is not None
                and Path(air_wav).is_file()
                and phase1 > 500
                and from_seg.kind == "song"
            ):
                start_sec = max(0.0, phase1 / 1000.0)
                await asyncio.to_thread(
                    extract_wav_from, Path(air_wav), out, start_sec=start_sec
                )
                total_ms = probe_duration_ms(out)
                tail_ms = 0
                log.info(
                    "⏭ Skip jump into continuous DJ open (t=%.1fs) → «%s»",
                    start_sec,
                    incoming.title,
                )
            else:
                if not from_seg.audio_path.is_file():
                    # Pure cut to next
                    await asyncio.to_thread(
                        lambda: out.write_bytes(incoming.audio_path.read_bytes())
                    )
                    total_ms = int(incoming.duration_ms or probe_duration_ms(out))
                    tail_ms = 0
                else:
                    elapsed = 0.0
                    if self.segment_started_at:
                        elapsed = max(0.0, time.time() - self.segment_started_at)
                    if phase_ms > 0:
                        elapsed = min(elapsed, phase_ms / 1000.0)
                    # Snappy bed — host arrives almost immediately
                    tail = 1.35 if from_seg.kind == "song" else 0.9
                    bed = self.station.outro_bed_gain
                    if from_seg.kind == "talk":
                        bed = min(bed, 0.35)
                    tail_ms, total_ms, _tail = await asyncio.to_thread(
                        build_skip_crossfade,
                        from_seg.audio_path,
                        incoming.audio_path,
                        out,
                        from_start_sec=elapsed,
                        tail_sec=tail,
                        bed_gain=bed,
                    )
        except Exception as exc:  # noqa: BLE001
            log.warning("Skip blend failed (%s) — falling back to hard cut", exc)
            self._set_stage("idle", "")
            return False

        # Consume the incoming segment from the ready queue if present
        if self.ready and self.ready[0].id == incoming.id:
            self.ready.popleft()
            self._last_enqueued_kind = incoming.kind

        # If we skipped a song into a *pre-buffered* talk that teases that same
        # track as "coming up", skip the talk and jump to the next song instead.
        if (
            from_seg.kind == "song"
            and incoming.kind == "talk"
            and self._talk_names_song(incoming, from_seg)
        ):
            log.info(
                "⏭ Dropping talk that teases skipped «%s» — finding next song",
                from_seg.title,
            )
            alt = None
            for seg in list(self.ready):
                if seg.kind == "song":
                    alt = seg
                    break
            if alt is not None:
                while self.ready and self.ready[0].id != alt.id:
                    dropped = self.ready.popleft()
                    log.info("⏭ Discard queue «%s»", dropped.title)
                if self.ready and self.ready[0].id == alt.id:
                    self.ready.popleft()
                incoming = alt
                out = self.segments_dir / f"{from_seg.id}_skip_{incoming.id}.wav"
                try:
                    elapsed = 0.0
                    if self.segment_started_at:
                        elapsed = max(0.0, time.time() - self.segment_started_at)
                    tail_ms, total_ms, _ = await asyncio.to_thread(
                        build_skip_crossfade,
                        from_seg.audio_path,
                        incoming.audio_path,
                        out,
                        from_start_sec=elapsed,
                        tail_sec=1.2,
                        bed_gain=0.28,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Skip re-blend to song failed: %s", exc)
                    return False
            else:
                # No alternate song — still kill other stale talks; air this talk
                # only if it does not name the skipped track (already true branch
                # only when it does — so discard talk and wait for refill)
                log.info("⏭ No next song; discarding stale talk and waiting for buffer")
                self.dj_generation += 1
                self.drop_pending_talks()
                return False

        # Drop any *other* buffered talks (written with wrong "coming up" context)
        if from_seg.kind == "song":
            kept: deque = deque()
            for seg in self.ready:
                if seg.kind == "talk":
                    log.info("⏭ Discard stale buffered talk «%s»", seg.title)
                    continue
                kept.append(seg)
            self.ready = kept
            self.dj_generation += 1
            if self.ready:
                self._last_enqueued_kind = self.ready[-1].kind
            elif incoming:
                self._last_enqueued_kind = incoming.kind

        try:
            # Fast WAV package so the client can soft-swap without waiting on HLS
            await asyncio.to_thread(self._package_stream_fast_wav, out)
        except Exception as exc:  # noqa: BLE001
            log.warning("Skip blend package failed: %s", exc)
            self._set_stage("idle", "")
            return False
        finally:
            if self.state == RadioState.PLAYING:
                self._set_stage("idle", "")

        self.current = incoming
        self.segment_started_at = time.time()
        show_ms = max(
            int(incoming.duration_ms or 0),
            max(500, int(total_ms) - min(int(tail_ms), 800)),
        )
        if total_ms > show_ms:
            show_ms = int(total_ms)
        self._current_play_ms = show_ms
        log.info(
            "⏭ Skip → %s «%s» (%.1fs package)",
            incoming.kind,
            incoming.title,
            show_ms / 1000.0,
        )
        try:
            skipped_again = await self._sleep_interruptible(
                max(0.5, show_ms / 1000.0)
            )
        except asyncio.CancelledError:
            raise
        if incoming.kind == "song":
            self._record_played_song(incoming)
        if skipped_again and self._play_event.is_set():
            await self._skip_blend_into_next(
                incoming,
                {"continuous": False, "next": None, "song": None},
                show_ms,
            )
        return True

    async def _sleep_interruptible(self, wait_s: float) -> bool:
        """Sleep for wait_s. Returns True if skipped, False if completed/stopped."""
        end = time.time() + wait_s
        while time.time() < end:
            if self._skip_current:
                self._skip_current = False
                log.info("Segment playback cut short (skip)")
                return True
            if not self._play_event.is_set():
                return False
            remaining = end - time.time()
            await asyncio.sleep(min(0.2, max(0.01, remaining)))
        return False

    def _prepare_stream_wav(
        self, seg: Segment, next_seg: Segment | None
    ) -> dict:
        """
        Build the WAV that will go on air.

        Talk→song: bed under last words + rest of track (no player reload).
        Song→talk: DJ opens over the last outro seconds while music ducks.
        """
        overlap = self.station.crossfade_sec
        bed_gain = self.station.crossfade_bed_gain
        outro = self.station.outro_crossfade_sec
        outro_bed = self.station.outro_bed_gain

        if (
            seg.kind == "talk"
            and next_seg is not None
            and next_seg.kind == "song"
            and overlap > 0
            and seg.duration_ms / 1000.0 > overlap + 0.6
            and next_seg.duration_ms / 1000.0 > overlap + 0.5
            and seg.audio_path.is_file()
            and next_seg.audio_path.is_file()
        ):
            out = self.segments_dir / f"{seg.id}_x_{next_seg.id}_air.wav"
            try:
                talk_ms, total_ms, _ov = build_talk_song_continuous(
                    seg.audio_path,
                    next_seg.audio_path,
                    out,
                    overlap_sec=overlap,
                    bed_gain=bed_gain,
                )
                log.info(
                    "Talk→song continuous air file «%s» → «%s» (%.1fs + song)",
                    seg.title,
                    next_seg.title,
                    talk_ms / 1000.0,
                )
                return {
                    "wav": out,
                    "talk_ms": talk_ms,
                    "phase1_ms": talk_ms,
                    "total_ms": total_ms,
                    "consume_song": True,
                    "song": next_seg,
                    "next": next_seg,
                    "continuous": True,
                    "handoff": "talk_to_song",
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("Continuous talk→song failed, dry talk: %s", exc)

        if (
            seg.kind == "song"
            and next_seg is not None
            and next_seg.kind == "talk"
            and outro > 0
            and seg.duration_ms / 1000.0 > outro + 2.0
            and next_seg.duration_ms / 1000.0 > 0.6
            and seg.audio_path.is_file()
            and next_seg.audio_path.is_file()
        ):
            out = self.segments_dir / f"{seg.id}_x_{next_seg.id}_outro.wav"
            try:
                song_clear_ms, total_ms, ov = build_song_talk_continuous(
                    seg.audio_path,
                    next_seg.audio_path,
                    out,
                    overlap_sec=outro,
                    bed_gain=outro_bed,
                )
                log.info(
                    "Song→talk continuous air file «%s» → «%s» "
                    "(DJ in at -%.1fs, clear song %.1fs)",
                    seg.title,
                    next_seg.title,
                    ov,
                    song_clear_ms / 1000.0,
                )
                return {
                    "wav": out,
                    "talk_ms": song_clear_ms,  # phase-1 duration (legacy key)
                    "phase1_ms": song_clear_ms,
                    "total_ms": total_ms,
                    "consume_song": True,  # consume next (talk) from queue
                    "song": next_seg,  # phase-2 segment (talk)
                    "next": next_seg,
                    "continuous": True,
                    "handoff": "song_to_talk",
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("Continuous song→talk failed, dry song: %s", exc)

        return {
            "wav": seg.audio_path,
            "talk_ms": seg.duration_ms,
            "phase1_ms": seg.duration_ms,
            "total_ms": seg.duration_ms,
            "consume_song": False,
            "song": None,
            "next": None,
            "continuous": False,
        }

    def _package_stream(self, wav_path) -> None:
        from pathlib import Path

        wav_path = Path(wav_path)
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        self.stream_id += 1
        try:
            log.info(
                "Packaging HLS stream_id=%s for %s → %s",
                self.stream_id,
                wav_path,
                self.hls_dir,
            )
            playlist = build_hls_from_wav(wav_path, self.hls_dir)
            # Always keep WAV fallback in sync (same continuous file)
            copy_wav_as_fallback(wav_path, self.hls_dir)
            log.info("HLS ready: %s", playlist)
        except Exception as exc:
            log.warning("HLS failed (%s); writing WAV fallback", exc)
            dest = copy_wav_as_fallback(wav_path, self.hls_dir)
            log.info("WAV fallback ready: %s", dest)
