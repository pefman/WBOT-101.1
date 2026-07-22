from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from airadio.audio.process import build_talk_song_continuous
from airadio.models_types import Genre, RadioState, Segment, StationConfig
from airadio.producers.song import produce_song
from airadio.producers.talk import produce_talk
from airadio.stream.hls import build_hls_from_wav, copy_wav_as_fallback

log = logging.getLogger(__name__)

TalkFn = Callable[..., Awaitable[Segment]]
SongFn = Callable[..., Awaitable[Segment]]


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
        # Songs that have finished airplay (most recent last)
        self._played_songs: deque[Segment] = deque(maxlen=5)
        self.mood_id: str | None = None
        self.mood_label: str | None = None
        self.dj_id: str | None = None
        self.dj_blurb: str | None = None
        self._dj_personality: str = ""
        self.recent_talk_texts: deque[str] = deque(maxlen=8)
        self._system_template: str = (
            station.system_prompt_template or station.system_prompt
        )
        # Bumped on every DJ/voice change so in-flight talk can be discarded
        self.dj_generation: int = 0
        self._skip_current: bool = False
        # Wall-clock length of the *current* metadata segment on air
        self._current_play_ms: int | None = None
        # Bumped only when the HLS/WAV package is rewritten (not on talk→song meta switch)
        self.stream_id: int = 0

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

    async def play(self) -> None:
        await self.start()
        self.state = RadioState.BUFFERING
        self.buffering_message = "Generating first segments (talk + song)…"
        self._play_event.set()

        # Wait until buffer_min ready
        deadline = time.time() + 600  # 10 min cold start allowance
        while len(self.ready) < self.station.buffer_min:
            if time.time() > deadline:
                self.state = RadioState.STOPPED
                self.buffering_message = "Timed out waiting for buffer"
                raise TimeoutError(self.buffering_message)
            self.buffering_message = (
                f"Buffering… {len(self.ready)}/{self.station.buffer_min} segments ready"
            )
            await asyncio.sleep(0.5)

        # Pre-package the first segment so the stream URL works as soon as we go on air
        if self.ready:
            self.buffering_message = "Packaging audio stream…"
            try:
                first = self.ready[0]
                nxt = self.ready[1] if len(self.ready) > 1 else None
                await asyncio.to_thread(self._prepare_stream_wav, first, nxt)
            except Exception as exc:  # noqa: BLE001
                log.warning("Pre-package failed: %s", exc)

        self.state = RadioState.PLAYING
        self.buffering_message = ""
        if not self._playback_task or self._playback_task.done():
            self._playback_task = asyncio.create_task(
                self._playback_loop(), name="radio-playback"
            )

    async def stop(self) -> None:
        self._play_event.clear()
        self.state = RadioState.STOPPED
        self.buffering_message = ""
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
        log.info("Stopped — queue cleared; generation waits for Play")

    def now(self) -> dict:
        seg_meta = None
        if self.current:
            seg_meta = self.current.meta()
            if self._current_play_ms is not None:
                seg_meta["duration_ms"] = self._current_play_ms
        return {
            "state": self.state.value,
            "buffering_message": self.buffering_message,
            "segment": seg_meta,
            "station_name": self.station.name,
            "queue_depth": len(self.ready),
            "segment_started_at": self.segment_started_at,
            "mood_id": self.mood_id,
            "mood_label": self.mood_label,
            "dj_id": self.dj_id,
            "dj_name": self.station.host_name,
            "dj_blurb": self.dj_blurb,
            "kokoro_voice": self.station.kokoro_voice,
            "enabled_genres": list(self.station.enabled_genres),
            "crossfade_sec": float(getattr(self.station, "crossfade_sec", 0) or 0),
            "language": self.station.language,
            # Client must only re-attach audio when this changes (not on meta handoff)
            "stream_id": self.stream_id,
        }

    def queue_meta(self) -> list[dict]:
        return [s.meta() for s in self.ready]

    def played_songs_meta(self, *, limit: int = 5) -> list[dict]:
        """Most recently finished songs first (newest at top)."""
        items = list(self._played_songs)
        items.reverse()
        return [s.meta() for s in items[:limit]]

    def _record_played_song(self, seg: Segment | None) -> None:
        if seg is None or seg.kind != "song":
            return
        # Avoid duplicate if same id re-recorded
        if self._played_songs and self._played_songs[-1].id == seg.id:
            return
        self._played_songs.append(seg)

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
        """Change Kokoro voice for future talk; drop queued talk so old audio is not played."""
        self.station.kokoro_voice = voice_id
        self._bump_talk_generation(skip_current_talk=clear_pending_talk)
        removed = self.drop_pending_talks() if clear_pending_talk else 0
        log.info(
            "Kokoro voice set to %s (DJ %s) gen=%d removed_pending_talk=%d",
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
        self.station.host_name = name
        self.station.system_prompt = build_system_prompt(
            self._system_template,
            station_name=self.station.name,
            host_name=name,
            personality=personality,
        )
        if apply_voice and voice:
            self.station.kokoro_voice = voice
        if clear_pending_talk:
            self._bump_talk_generation(skip_current_talk=True)
            removed = self.drop_pending_talks()
        else:
            removed = 0
        log.info(
            "DJ set to %s (%s) voice=%s gen=%d removed_pending_talk=%d",
            dj_id,
            name,
            self.station.kokoro_voice,
            self.dj_generation,
            removed,
        )
        return {
            "dj_id": dj_id,
            "name": name,
            "blurb": blurb,
            "voice": self.station.kokoro_voice,
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
        )
        removed = 0
        if clear_pending_songs:
            kept: deque[Segment] = deque()
            for seg in self.ready:
                if seg.kind == "song":
                    removed += 1
                    continue
                kept.append(seg)
            self.ready = kept
            if kept:
                self._last_enqueued_kind = kept[-1].kind

        log.info(
            "Genres set (%s) n=%d removed_pending_songs=%d",
            ordered,
            len(ordered),
            removed,
        )
        return {
            "enabled_genres": list(ordered),
            "mood_id": self.mood_id,
            "mood_label": self.mood_label,
            "removed_pending_songs": removed,
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

    async def _worker_loop(self) -> None:
        """Fill the air buffer only while Play is active (not on idle / DJ pick)."""
        while self._running:
            try:
                # Off air: pick DJ/mood/voice freely — no LLM/TTS/music until Play
                if not self._play_event.is_set():
                    await asyncio.sleep(0.25)
                    continue

                if len(self.ready) >= self.station.buffer_target:
                    await asyncio.sleep(0.5)
                    continue

                next_kind = self._next_kind()
                gen = self.dj_generation
                if next_kind == "song":
                    self.buffering_message = self.buffering_message or "Composing a track…"
                    async with self._gpu_lock:
                        seg = await self.song_fn(
                            self.station, self.genres, self.segments_dir
                        )
                else:
                    self.buffering_message = self.buffering_message or "Writing DJ talk…"
                    prev = self._last_song_from_history()
                    next_song = self._peek_next_song_in_queue()
                    host_at_start = self.station.host_name
                    voice_at_start = self.station.kokoro_voice
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
                            self.station.kokoro_voice,
                        )
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
                if seg.kind == "talk" and seg.text:
                    self.recent_talk_texts.append(seg.text)
                log.info(
                    "Enqueued %s %s (queue=%d)",
                    seg.kind,
                    seg.title,
                    len(self.ready),
                )
                async with self._cv:
                    self._cv.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Worker error: %s", exc)
                self.buffering_message = f"Generation error: {exc}"
                await asyncio.sleep(2.0)

    def _peek_next_song_in_queue(self) -> Segment | None:
        """If a song is already buffered ahead, let talk tease its real title."""
        for seg in self.ready:
            if seg.kind == "song":
                return seg
        return None

    def _next_kind(self) -> str:
        if self._last_enqueued_kind is None:
            return "talk"
        return "song" if self._last_enqueued_kind == "talk" else "talk"

    def _last_song_from_history(self) -> Segment | None:
        for s in reversed(self._history):
            if s.kind == "song":
                return s
        if self.current and self.current.kind == "song":
            return self.current
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

            # --- Phase 1: talk (or plain song segment) ---
            self.current = seg
            self.segment_started_at = time.time()
            talk_ms = int(plan["talk_ms"])
            self._current_play_ms = talk_ms
            try:
                await self._sleep_interruptible(max(0.5, talk_ms / 1000.0))
            except asyncio.CancelledError:
                raise

            if not self._play_event.is_set():
                # Stopped mid-segment: still count a song if it was on air
                if seg.kind == "song":
                    self._record_played_song(seg)
                continue
            if self._skip_current:
                self._skip_current = False
                if seg.kind == "song":
                    self._record_played_song(seg)
                continue

            # Plain song (no continuous handoff) finished airplay
            if seg.kind == "song" and not plan.get("continuous"):
                self._record_played_song(seg)

            # --- Phase 2: same continuous stream, hand off metadata to song ---
            song: Segment | None = plan.get("song")
            if plan.get("continuous") and song is not None:
                if plan.get("consume_song") and self.ready and self.ready[0].id == song.id:
                    self.ready.popleft()
                # Stream keeps playing — do not re-package; only flip now-playing
                self.current = song
                self.segment_started_at = time.time()
                song_ms = max(500, int(plan["total_ms"]) - talk_ms)
                self._current_play_ms = song_ms
                log.info(
                    "Seamless handoff to song «%s» (%.1fs left on same stream)",
                    song.title,
                    song_ms / 1000.0,
                )
                try:
                    await self._sleep_interruptible(song_ms / 1000.0)
                except asyncio.CancelledError:
                    raise
                self._record_played_song(song)

        self.current = None
        self.segment_started_at = None
        self._current_play_ms = None

    async def _sleep_interruptible(self, wait_s: float) -> None:
        """Sleep for wait_s unless _skip_current is set (DJ/voice switch)."""
        end = time.time() + wait_s
        while time.time() < end:
            if self._skip_current:
                self._skip_current = False
                log.info("Segment playback cut short (host/voice switch)")
                return
            if not self._play_event.is_set():
                return
            remaining = end - time.time()
            await asyncio.sleep(min(0.2, max(0.01, remaining)))

    def _prepare_stream_wav(
        self, seg: Segment, next_seg: Segment | None
    ) -> dict:
        """
        Build the WAV that will go on air.

        Talk→song: one continuous file (bed under last words + rest of track)
        so the player never reloads when the voice ends.
        """
        overlap = float(getattr(self.station, "crossfade_sec", 0) or 0)
        bed_gain = float(getattr(self.station, "crossfade_bed_gain", 0.55) or 0.55)

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
                    "total_ms": total_ms,
                    "consume_song": True,
                    "song": next_seg,
                    "continuous": True,
                }
            except Exception as exc:  # noqa: BLE001
                log.warning("Continuous crossfade failed, dry talk: %s", exc)

        return {
            "wav": seg.audio_path,
            "talk_ms": seg.duration_ms,
            "total_ms": seg.duration_ms,
            "consume_song": False,
            "song": None,
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
