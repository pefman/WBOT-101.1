"""Song library: keepers for re-air, disk GC for generated segments."""

from __future__ import annotations

import json
import logging
import random
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from airadio.models_types import Segment

log = logging.getLogger(__name__)

LIBRARY_VERSION = 1


@dataclass
class LibraryEntry:
    id: str
    title: str
    artist: str | None
    genre_id: str | None
    audio_path: str
    duration_ms: int
    text: str = ""
    generation_prompt: str | None = None
    cover_path: str | None = None
    created_at: float = 0.0
    last_aired_at: float = 0.0
    favorite: bool = False
    play_count: int = 0

    def audio_file(self) -> Path:
        return Path(self.audio_path)

    def is_playable(self) -> bool:
        p = self.audio_file()
        return p.is_file() and p.stat().st_size > 1000

    def to_segment(self) -> Segment | None:
        if not self.is_playable():
            return None
        cover = Path(self.cover_path) if self.cover_path else None
        if cover is not None and not cover.is_file():
            cover = None
        return Segment(
            id=self.id,
            kind="song",
            title=self.title,
            genre_id=self.genre_id,
            text=self.text or "",
            audio_path=self.audio_file(),
            duration_ms=int(self.duration_ms or 0),
            created_at=float(self.created_at or time.time()),
            artist=self.artist,
            generation_prompt=self.generation_prompt,
            cover_path=cover,
        )


@dataclass
class SongLibrary:
    path: Path
    max_songs: int = 40
    entries: dict[str, LibraryEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, data_dir: Path, *, max_songs: int = 40) -> SongLibrary:
        path = Path(data_dir) / "library.json"
        lib = cls(path=path, max_songs=max(1, int(max_songs)))
        if not path.is_file():
            return lib
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read library %s: %s", path, exc)
            return lib
        for item in raw.get("songs") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            try:
                entry = LibraryEntry(
                    id=str(item["id"]),
                    title=str(item.get("title") or "Untitled"),
                    artist=(str(item["artist"]) if item.get("artist") else None),
                    genre_id=(str(item["genre_id"]) if item.get("genre_id") else None),
                    audio_path=str(item.get("audio_path") or ""),
                    duration_ms=int(item.get("duration_ms") or 0),
                    text=str(item.get("text") or ""),
                    generation_prompt=item.get("generation_prompt"),
                    cover_path=item.get("cover_path"),
                    created_at=float(item.get("created_at") or 0),
                    last_aired_at=float(item.get("last_aired_at") or 0),
                    favorite=bool(item.get("favorite")),
                    play_count=int(item.get("play_count") or 0),
                )
            except (TypeError, ValueError):
                continue
            if entry.audio_path:
                lib.entries[entry.id] = entry
        # Drop broken paths
        dead = [eid for eid, e in lib.entries.items() if not e.is_playable()]
        for eid in dead:
            del lib.entries[eid]
        if dead:
            lib.save()
        return lib

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        songs = [asdict(e) for e in self._ordered()]
        payload: dict[str, Any] = {
            "version": LIBRARY_VERSION,
            "max_songs": self.max_songs,
            "songs": songs,
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _ordered(self) -> list[LibraryEntry]:
        # Favorites first, then most recently aired
        return sorted(
            self.entries.values(),
            key=lambda e: (0 if e.favorite else 1, -float(e.last_aired_at or e.created_at or 0)),
        )

    def remember(self, seg: Segment, *, favorite: bool | None = None) -> None:
        if seg.kind != "song" or not seg.audio_path or not Path(seg.audio_path).is_file():
            return
        existing = self.entries.get(seg.id)
        fav = existing.favorite if existing and favorite is None else bool(favorite or False)
        if existing and favorite is True:
            fav = True
        play_count = (existing.play_count if existing else 0) + 1
        self.entries[seg.id] = LibraryEntry(
            id=seg.id,
            title=seg.title,
            artist=seg.artist,
            genre_id=seg.genre_id,
            audio_path=str(seg.audio_path),
            duration_ms=int(seg.duration_ms or 0),
            text=seg.text or "",
            generation_prompt=seg.generation_prompt,
            cover_path=str(seg.cover_path) if seg.cover_path else None,
            created_at=float(seg.created_at or time.time()),
            last_aired_at=time.time(),
            favorite=fav,
            play_count=play_count,
        )
        self._trim()
        self.save()

    def set_favorite(self, segment_id: str, favorite: bool = True) -> LibraryEntry | None:
        entry = self.entries.get(segment_id)
        if not entry:
            return None
        entry.favorite = bool(favorite)
        self.save()
        return entry

    def _trim(self) -> None:
        """Evict oldest non-favorites beyond max_songs."""
        while len(self.entries) > self.max_songs:
            candidates = [e for e in self.entries.values() if not e.favorite]
            if not candidates:
                # All favorites — drop oldest favorite
                candidates = list(self.entries.values())
            drop = min(
                candidates,
                key=lambda e: float(e.last_aired_at or e.created_at or 0),
            )
            del self.entries[drop.id]

    def pick_reair(
        self,
        *,
        enabled_genres: list[str] | None,
        exclude_ids: set[str] | None = None,
        chance: float = 0.28,
    ) -> Segment | None:
        """Maybe return a library song to re-air (or None to generate fresh)."""
        if chance <= 0 or random.random() >= chance:
            return None
        exclude = exclude_ids or set()
        enabled = set(enabled_genres or [])
        pool: list[LibraryEntry] = []
        for e in self.entries.values():
            if e.id in exclude:
                continue
            if not e.is_playable():
                continue
            # Genre filter: meta "radio" alone = any track; else match concrete ids.
            if enabled:
                concrete = {g for g in enabled if g != "radio"}
                freeform = "radio" in enabled and not concrete
                if not freeform:
                    if not e.genre_id or e.genre_id not in concrete:
                        continue
            pool.append(e)
        if not pool:
            return None
        # Prefer favorites and less-recently aired
        weights = []
        now = time.time()
        for e in pool:
            age_h = max(0.5, (now - float(e.last_aired_at or e.created_at or now)) / 3600.0)
            w = age_h * (3.0 if e.favorite else 1.0)
            weights.append(w)
        pick = random.choices(pool, weights=weights, k=1)[0]
        seg = pick.to_segment()
        if seg is None:
            return None
        log.info(
            "Re-airing library track «%s» — %s (fav=%s plays=%d)",
            pick.artist or "?",
            pick.title,
            pick.favorite,
            pick.play_count,
        )
        return seg

    def protected_paths(self) -> set[Path]:
        paths: set[Path] = set()
        for e in self.entries.values():
            paths.add(e.audio_file().resolve())
            if e.cover_path:
                paths.add(Path(e.cover_path).resolve())
        return paths

    def meta_list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for e in self._ordered()[:limit]:
            out.append(
                {
                    "id": e.id,
                    "title": e.title,
                    "artist": e.artist,
                    "genre_id": e.genre_id,
                    "duration_ms": e.duration_ms,
                    "favorite": e.favorite,
                    "play_count": e.play_count,
                    "cover_url": f"/api/covers/{e.id}.png" if e.cover_path else None,
                }
            )
        return out


def garbage_collect_segments(
    segments_dir: Path,
    *,
    protect: set[Path],
    max_age_hours: float = 48.0,
    max_files: int = 200,
) -> dict[str, int]:
    """Delete old segment audio/art not in protect set.

    Keeps files newer than max_age_hours first; if still over max_files, drops oldest.
    """
    segments_dir = Path(segments_dir)
    if not segments_dir.is_dir():
        return {"deleted": 0, "kept": 0}

    protect_resolved = {p.resolve() for p in protect}
    now = time.time()
    max_age_s = max(1.0, float(max_age_hours)) * 3600.0

    candidates: list[tuple[float, Path]] = []
    for path in segments_dir.iterdir():
        if not path.is_file():
            continue
        # Keep library index siblings / non-media alone
        if path.suffix.lower() not in {".wav", ".png", ".json", ".mp3", ".flac"}:
            continue
        if path.name in {"library.json", "prefs.json"}:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in protect_resolved:
            continue
        # Protect continuous air mixes only if very new (handled by age)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, path))

    deleted = 0
    # Age-based delete
    remaining: list[tuple[float, Path]] = []
    for mtime, path in candidates:
        if now - mtime > max_age_s:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                remaining.append((mtime, path))
        else:
            remaining.append((mtime, path))

    # Count protect + remaining as kept media
    remaining.sort(key=lambda t: t[0])  # oldest first
    while len(remaining) > max_files:
        _, path = remaining.pop(0)
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass

    kept = len(list(segments_dir.glob("*"))) if segments_dir.is_dir() else 0
    if deleted:
        log.info("Segment GC: deleted %d file(s), ~%d remain in %s", deleted, kept, segments_dir)
    return {"deleted": deleted, "kept": kept}


def copy_into_library_dir(seg: Segment, library_audio_dir: Path) -> Segment:
    """Optional: hardlink/copy audio into a stable library folder. Currently unused."""
    library_audio_dir.mkdir(parents=True, exist_ok=True)
    dest = library_audio_dir / f"{seg.id}.wav"
    if not dest.is_file() and seg.audio_path.is_file():
        try:
            shutil.copy2(seg.audio_path, dest)
            seg.audio_path = dest
        except OSError as exc:
            log.warning("Could not copy song into library dir: %s", exc)
    return seg
