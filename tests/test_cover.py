from pathlib import Path

from airadio.art.cover import generate_cover


def test_generate_cover_writes_png(tmp_path: Path):
    out = tmp_path / "x_cover.png"
    generate_cover(
        out,
        title="Midnight Circuit",
        artist="Neon Harbor",
        genre_id="melodic_progressive_metal",
        seed="abc123",
    )
    assert out.is_file()
    assert out.stat().st_size > 1000
    # same seed → same bytes
    out2 = tmp_path / "y_cover.png"
    generate_cover(
        out2,
        title="Midnight Circuit",
        artist="Neon Harbor",
        genre_id="melodic_progressive_metal",
        seed="abc123",
    )
    assert out.read_bytes() == out2.read_bytes()
