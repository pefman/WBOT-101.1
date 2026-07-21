from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

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
                await asyncio.to_thread(self._package_stream, self.ready[0])
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
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
            self._playback_task = None
        self.current = None
        self.segment_started_at = None

    def now(self) -> dict:
        return {
            "state": self.state.value,
            "buffering_message": self.buffering_message,
            "segment": self.current.meta() if self.current else None,
            "station_name": self.station.name,
            "queue_depth": len(self.ready),
            "segment_started_at": self.segment_started_at,
        }

    def queue_meta(self) -> list[dict]:
        return [s.meta() for s in self.ready]

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                if len(self.ready) >= self.station.buffer_target:
                    await asyncio.sleep(0.5)
                    continue

                # Only aggressively fill while playing/buffering or always warm?
                # Live-only: keep filling toward target once started once, or always.
                # Fill whenever under target so cold start and underrun recover.
                next_kind = self._next_kind()
                if next_kind == "song":
                    self.buffering_message = self.buffering_message or "Composing a track…"
                    async with self._gpu_lock:
                        seg = await self.song_fn(
                            self.station, self.genres, self.segments_dir
                        )
                else:
                    self.buffering_message = self.buffering_message or "Writing DJ talk…"
                    prev = self._last_song_from_history()
                    # Peek: if next will be song after this talk, we don't know title yet
                    seg = await self.talk_fn(
                        self.station,
                        self.segments_dir,
                        prev_song=prev,
                        next_song=None,
                        dj_tone=None,
                    )

                self.ready.append(seg)
                self._last_enqueued_kind = seg.kind
                self._history.append(seg)
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
            self.current = seg
            self.segment_started_at = time.time()
            try:
                await asyncio.to_thread(self._package_stream, seg)
            except Exception as exc:  # noqa: BLE001
                log.exception("HLS package failed: %s", exc)

            # Play for segment duration (orchestrator clock; browser follows stream)
            wait_s = max(0.5, seg.duration_ms / 1000.0)
            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                raise

        self.current = None
        self.segment_started_at = None

    def _package_stream(self, seg: Segment) -> None:
        self.hls_dir.mkdir(parents=True, exist_ok=True)
        try:
            log.info("Packaging HLS for %s → %s", seg.audio_path, self.hls_dir)
            playlist = build_hls_from_wav(seg.audio_path, self.hls_dir)
            log.info("HLS ready: %s", playlist)
        except Exception as exc:
            log.warning("HLS failed (%s); writing WAV fallback", exc)
            dest = copy_wav_as_fallback(seg.audio_path, self.hls_dir)
            log.info("WAV fallback ready: %s", dest)
