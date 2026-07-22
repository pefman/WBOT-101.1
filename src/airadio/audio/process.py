from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import soundfile as sf

from airadio.paths import bundled_ffmpeg

log = logging.getLogger(__name__)

# Default talk→song under-voice bed (seconds) — real-radio style ease-in
DEFAULT_CROSSFADE_SEC = 3.0


def probe_duration_ms(path: Path) -> int:
    info = sf.info(str(path))
    return int(round(info.duration * 1000))


def probe_sample_rate(path: Path) -> int:
    try:
        return int(sf.info(str(path)).samplerate) or 48000
    except Exception:  # noqa: BLE001
        return 48000


def ffmpeg_available() -> bool:
    try:
        bundled_ffmpeg()
        return True
    except Exception:  # noqa: BLE001
        return False


def ffmpeg_exe() -> str:
    return bundled_ffmpeg()


def loudnorm_ffmpeg(
    in_path: Path,
    out_path: Path,
    *,
    integrated: float = -16.0,
    sample_rate: int | None = None,
    trim_silence: bool = False,
) -> Path:
    """Normalize loudness using venv-bundled ffmpeg; otherwise copy.

    Important: always pin ``-ar`` to the source rate (or an explicit rate).
    Without that, some static ffmpeg builds upsample WAV output to 192 kHz,
    which bloats files and can make players finish the track early or play
    at the wrong speed.
    """
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg_available():
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
        return out_path

    ar = sample_rate or probe_sample_rate(in_path)
    # Keep radio-friendly rates only
    if ar not in (16000, 22050, 24000, 32000, 44100, 48000):
        log.warning("Unusual source rate %s Hz — forcing 48000", ar)
        ar = 48000

    filters = [f"loudnorm=I={integrated}:TP=-1.5:LRA=11"]
    if trim_silence:
        # Only strip *long* dead air after the outro — keep short tails / reverb.
        # (Aggressive trim was eating 3–5s and making 75s tracks feel like ~1:10.)
        filters.append(
            "silenceremove=stop_periods=1:stop_duration=1.4:stop_threshold=-45dB:"
            "stop_silence=0.25"
        )

    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(in_path),
        "-af",
        ",".join(filters),
        "-ar",
        str(ar),
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        log.warning("loudnorm failed, copying raw: %s", (proc.stderr or "")[-500:])
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
    else:
        # Guard: if ffmpeg still wrote a weird rate, re-encode rate only
        try:
            out_rate = probe_sample_rate(out_path)
            if out_rate not in (16000, 22050, 24000, 32000, 44100, 48000):
                log.warning(
                    "loudnorm output was %s Hz; re-encoding to %s", out_rate, ar
                )
                tmp = out_path.with_suffix(".ratefix.wav")
                r2 = subprocess.run(
                    [
                        ffmpeg_exe(),
                        "-y",
                        "-i",
                        str(out_path),
                        "-ar",
                        str(ar),
                        "-sample_fmt",
                        "s16",
                        str(tmp),
                    ],
                    capture_output=True,
                    text=True,
                )
                if r2.returncode == 0 and tmp.is_file():
                    tmp.replace(out_path)
                else:
                    tmp.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("rate check skipped: %s", exc)
    return out_path


def mix_song_under_talk(
    talk_path: Path,
    song_path: Path,
    out_path: Path,
    *,
    overlap_sec: float = DEFAULT_CROSSFADE_SEC,
    bed_gain: float = 0.55,
) -> Path:
    """
    Talk-only package with song bed under the last ``overlap_sec``.
    Prefer :func:`build_talk_song_continuous` for seamless on-air handoff.
    """
    talk_path = Path(talk_path)
    song_path = Path(song_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    talk_dur = probe_duration_ms(talk_path) / 1000.0
    if talk_dur <= 0:
        raise ValueError("talk has zero duration")
    overlap = max(0.4, min(float(overlap_sec), talk_dur - 0.25))
    delay_ms = max(0, int(round((talk_dur - overlap) * 1000)))
    bed = max(0.05, min(1.0, float(bed_gain)))

    if not ffmpeg_available():
        out_path.write_bytes(talk_path.read_bytes())
        return out_path

    fc = (
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{overlap:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={overlap:.3f},"
        f"volume={bed:.3f},"
        f"adelay={delay_ms}|{delay_ms}[bed];"
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo[voice];"
        f"[voice][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[out]"
    )
    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(talk_path),
        "-i",
        str(song_path),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        log.warning(
            "crossfade mix failed, using dry talk: %s",
            (proc.stderr or "")[-800:],
        )
        out_path.write_bytes(talk_path.read_bytes())
        return out_path
    return out_path


def build_talk_song_continuous(
    talk_path: Path,
    song_path: Path,
    out_path: Path,
    *,
    overlap_sec: float = DEFAULT_CROSSFADE_SEC,
    bed_gain: float = 0.45,
    post_ramp_sec: float = 1.8,
) -> tuple[int, int, float]:
    """
    One seamless radio handoff — **single timeline, no splice**:

    - Host talks in the clear
    - In the last ``overlap_sec``, the next song eases in under them
    - Host soft-exits; song keeps the same waveform and ramps bed→full
      over ``post_ramp_sec`` (no restart, no concat click)

    Returns ``(talk_ms, total_ms, overlap_used_sec)``.
    """
    talk_path = Path(talk_path)
    song_path = Path(song_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    talk_dur = probe_duration_ms(talk_path) / 1000.0
    song_dur = probe_duration_ms(song_path) / 1000.0
    if talk_dur <= 0 or song_dur <= 0:
        raise ValueError("talk/song has zero duration")

    # Need enough room for under-voice bed + post-voice ramp into the song
    overlap = max(1.2, min(float(overlap_sec), talk_dur - 0.4, song_dur - 0.5))
    post_ramp = max(0.8, min(float(post_ramp_sec), song_dur - overlap - 0.2))
    bed = max(0.12, min(0.75, float(bed_gain)))

    # When the song first appears on the timeline (seconds)
    t_bed = talk_dur - overlap  # music starts under voice
    t_voice_end = talk_dur  # host done
    t_full = talk_dur + post_ramp  # music at full level
    delay_ms = max(0, int(round(t_bed * 1000)))

    # Soft host exit (don't hard-chop the last consonant under the bed)
    voice_fade = min(0.35, max(0.12, overlap * 0.12))
    voice_fade_st = max(0.0, talk_dur - voice_fade)

    if not ffmpeg_available():
        out_path.write_bytes(talk_path.read_bytes())
        talk_ms = int(round(talk_dur * 1000))
        return talk_ms, talk_ms, 0.0

    # Volume envelope on the *delayed* song (t is timeline seconds after adelay):
    #   t < t_bed:          silence (delay pad)
    #   t_bed → t_voice_end: 0 → bed   (under the host)
    #   t_voice_end → t_full: bed → 1  (after host, same take continues)
    #   t >= t_full:         1
    # Commas escaped for filtergraph.
    vol = (
        f"if(lt(t\\,{t_bed:.4f})\\,0\\,"
        f"if(lt(t\\,{t_voice_end:.4f})\\,"
        f"{bed:.4f}*(t-{t_bed:.4f})/{overlap:.4f}\\,"
        f"if(lt(t\\,{t_full:.4f})\\,"
        f"{bed:.4f}+{1.0 - bed:.4f}*(t-{t_voice_end:.4f})/{post_ramp:.4f}\\,"
        f"1)))"
    )

    # One continuous song stream (never split/concat) + voice on top.
    # dropout_transition eases the mix when the voice input ends.
    fc = (
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"adelay={delay_ms}|{delay_ms},"
        f"volume={vol}:eval=frame[bed];"
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=out:st={voice_fade_st:.4f}:d={voice_fade:.4f}[voice];"
        f"[voice][bed]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0[out]"
    )
    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(talk_path),
        "-i",
        str(song_path),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(
            f"continuous crossfade failed: {(proc.stderr or '')[-900:]}"
        )

    talk_ms = int(round(talk_dur * 1000))
    total_ms = probe_duration_ms(out_path)
    expected = talk_dur - overlap + song_dur
    log.info(
        "Smooth talk→song (under=%.1fs post_ramp=%.1fs bed=%.2f "
        "talk=%.1fs total=%.1fs expect~%.1fs) → %s",
        overlap,
        post_ramp,
        bed,
        talk_dur,
        total_ms / 1000.0,
        expected,
        out_path.name,
    )
    return talk_ms, total_ms, overlap


def build_song_talk_continuous(
    song_path: Path,
    talk_path: Path,
    out_path: Path,
    *,
    overlap_sec: float = 6.0,
    bed_gain: float = 0.32,
    duck_sec: float = 0.85,
) -> tuple[int, int, float]:
    """
    Real-radio outro: song plays full, host comes in over the last
    ``overlap_sec`` while music ducks to a bed, then talk continues dry
    after the track ends — **single timeline, no splice**.

    Returns ``(song_clear_ms, total_ms, overlap_used_sec)`` where
    ``song_clear_ms`` is wall time until the DJ starts (phase-1 now-playing).
    """
    song_path = Path(song_path)
    talk_path = Path(talk_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    song_dur = probe_duration_ms(song_path) / 1000.0
    talk_dur = probe_duration_ms(talk_path) / 1000.0
    if talk_dur <= 0 or song_dur <= 0:
        raise ValueError("talk/song has zero duration")

    # Need enough song body before the host + a short talk
    overlap = max(3.5, min(float(overlap_sec), song_dur - 2.0, 10.0))
    if song_dur < overlap + 1.5:
        raise ValueError("song too short for outro talk bed")
    if talk_dur < 0.6:
        raise ValueError("talk too short for outro handoff")

    bed = max(0.08, min(0.6, float(bed_gain)))
    duck = max(0.35, min(float(duck_sec), overlap * 0.45))

    # DJ mic opens this many seconds into the song
    t_voice = song_dur - overlap
    t_duck_end = t_voice + duck
    # Soft tail so the last notes don't click under the host
    fade_tail = min(0.7, max(0.25, overlap * 0.12))
    t_fade_start = max(t_duck_end + 0.05, song_dur - fade_tail)
    delay_ms = max(0, int(round(t_voice * 1000)))

    if not ffmpeg_available():
        out_path.write_bytes(song_path.read_bytes())
        song_ms = int(round(song_dur * 1000))
        return song_ms, song_ms, 0.0

    # Song volume on the *master* timeline (eval=frame):
    #   t < t_voice:              full
    #   t_voice → t_duck_end:     1 → bed (duck under host)
    #   t_duck_end → t_fade_start: bed
    #   t_fade_start → song_dur:  bed → 0
    #   t >= song_dur:            0 (talk continues alone)
    vol = (
        f"if(lt(t\\,{t_voice:.4f})\\,1\\,"
        f"if(lt(t\\,{t_duck_end:.4f})\\,"
        f"1-{(1.0 - bed):.4f}*(t-{t_voice:.4f})/{duck:.4f}\\,"
        f"if(lt(t\\,{t_fade_start:.4f})\\,{bed:.4f}\\,"
        f"if(lt(t\\,{song_dur:.4f})\\,"
        f"{bed:.4f}*(1-(t-{t_fade_start:.4f})/{max(0.05, song_dur - t_fade_start):.4f})\\,"
        f"0))))"
    )

    # Soft voice ease-in so the first word isn't a hard cut-in over the bed
    voice_fade_in = min(0.22, max(0.08, overlap * 0.04))

    fc = (
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"volume={vol}:eval=frame[music];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:st=0:d={voice_fade_in:.4f},"
        f"adelay={delay_ms}|{delay_ms}[voice];"
        f"[music][voice]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0[out]"
    )
    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(song_path),
        "-i",
        str(talk_path),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(
            f"song→talk continuous failed: {(proc.stderr or '')[-900:]}"
        )

    song_clear_ms = int(round(t_voice * 1000))
    total_ms = probe_duration_ms(out_path)
    expected = t_voice + talk_dur
    log.info(
        "Smooth song→talk (outro=%.1fs bed=%.2f duck=%.2fs "
        "song_clear=%.1fs total=%.1fs expect~%.1fs) → %s",
        overlap,
        bed,
        duck,
        t_voice,
        total_ms / 1000.0,
        expected,
        out_path.name,
    )
    return song_clear_ms, total_ms, overlap


def build_skip_crossfade(
    from_path: Path,
    to_path: Path,
    out_path: Path,
    *,
    from_start_sec: float = 0.0,
    tail_sec: float = 1.4,
    bed_gain: float = 0.28,
) -> tuple[int, int, float]:
    """
    Skip handoff: very short bed of the current item under the *start* of the
    next (usually DJ), then finish the next dry.

    Defaults are snappy on purpose — long tails feel like the old song
    “starts again” after a stream swap.

    Returns ``(tail_ms, total_ms, tail_used_sec)``.
    """
    from_path = Path(from_path)
    to_path = Path(to_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from_dur = probe_duration_ms(from_path) / 1000.0
    to_dur = probe_duration_ms(to_path) / 1000.0
    if from_dur <= 0.4 or to_dur <= 0.4:
        raise ValueError("skip crossfade needs playable source and destination")

    start = max(0.0, min(float(from_start_sec), max(0.0, from_dur - 0.25)))
    avail = max(0.25, from_dur - start)
    # Keep skip beds short so the host arrives almost immediately
    tail = max(0.7, min(float(tail_sec), avail, 2.2))
    bed = max(0.08, min(0.45, float(bed_gain)))
    end = min(from_dur, start + tail)
    actual_tail = max(0.55, end - start)

    if not ffmpeg_available():
        out_path.write_bytes(to_path.read_bytes())
        to_ms = int(round(to_dur * 1000))
        return 0, to_ms, 0.0

    # Fast fade on the bed; next opens almost immediately at full
    fade_out = max(0.45, min(actual_tail, actual_tail * 0.9))
    voice_in = min(0.12, max(0.05, actual_tail * 0.08))

    fc = (
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim={start:.4f}:{end:.4f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st=0:d={fade_out:.4f},"
        f"volume={bed:.4f}[bed];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"afade=t=in:st=0:d={voice_in:.4f}[next];"
        f"[bed][next]amix=inputs=2:duration=longest:dropout_transition=0.6:normalize=0[out]"
    )
    cmd = [
        ffmpeg_exe(),
        "-y",
        "-i",
        str(from_path),
        "-i",
        str(to_path),
        "-filter_complex",
        fc,
        "-map",
        "[out]",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(
            f"skip crossfade failed: {(proc.stderr or '')[-900:]}"
        )

    tail_ms = int(round(actual_tail * 1000))
    total_ms = probe_duration_ms(out_path)
    log.info(
        "Skip crossfade (from=%.1fs tail=%.1fs bed=%.2f → next=%.1fs total=%.1fs) %s",
        start,
        actual_tail,
        bed,
        to_dur,
        total_ms / 1000.0,
        out_path.name,
    )
    return tail_ms, total_ms, actual_tail


def extract_wav_from(
    in_path: Path,
    out_path: Path,
    *,
    start_sec: float,
) -> Path:
    """Write WAV starting at ``start_sec`` (legacy helper)."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, float(start_sec))
    if start <= 0.05:
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
        return out_path

    if not ffmpeg_available():
        data, sr = sf.read(str(in_path), always_2d=True)
        i0 = int(start * sr)
        if i0 >= len(data):
            raise ValueError("start_sec past end of audio")
        sf.write(str(out_path), data[i0:], sr)
        return out_path

    cmd = [
        ffmpeg_exe(),
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(in_path),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"extract_wav_from failed: {(proc.stderr or '')[-500:]}")
    return out_path
