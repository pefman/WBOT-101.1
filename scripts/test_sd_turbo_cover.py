#!/usr/bin/env python3
"""Smoke-test SD-Turbo cover generation. Writes PNGs under data/cover_tests/."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadio.art.cover import generate_cover  # noqa: E402
from airadio.art.sd_turbo import sd_turbo_available, unload_sd_turbo  # noqa: E402


def main() -> int:
    out_dir = ROOT / "data" / "cover_tests"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("diffusers available:", sd_turbo_available())
    if not sd_turbo_available():
        print("FAIL: install with: pip install -e '.[cover]'")
        return 1

    cases = [
        ("Shattered Reflections", "Echelon Ascend", "melodic_progressive_metal", "test-mpm-1"),
        ("Neon Rhythms", "Galactic Echoes", "electronic", "test-elec-1"),
    ]
    for title, artist, genre, seed in cases:
        out = out_dir / f"{seed}_cover.png"
        print(f"\n→ Generating [{genre}] {artist} — {title}")
        t0 = time.time()
        try:
            generate_cover(
                out,
                title=title,
                artist=artist,
                genre_id=genre,
                seed=seed,
                backend="sd_turbo",
                steps=2,
            )
        except Exception as exc:  # noqa: BLE001
            print("FAIL:", exc)
            return 1
        dt = time.time() - t0
        kb = out.stat().st_size / 1024
        print(f"  OK {out} ({kb:.0f} KiB, {dt:.1f}s)")

    unload_sd_turbo()
    print("\nDone. Open files in data/cover_tests/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
