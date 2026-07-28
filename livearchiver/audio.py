"""ffmpeg plumbing: extract audio, find silences, cut and tag tracks.

Everything is master-first: we extract one lossless FLAC master of the whole
show, then cut every track from that master, so re-splitting after fixing a
tracklist never touches the original download.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from .platformsupport import ffmpeg, ffprobe, popen_kwargs, safe_filename

log = logging.getLogger("livearchiver")

MEDIA_EXTS = (".mkv", ".mp4", ".webm", ".avi", ".mov", ".mpg",
              ".flac", ".wav", ".shn", ".mp3", ".ogg", ".m4a", ".opus")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.debug("$ %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True,
                          **popen_kwargs())


def find_source_media(show_dir: Path) -> list[Path]:
    """Media files of the original download, multi-file items sorted by name."""
    files = sorted(p for p in show_dir.iterdir()
                   if p.suffix.lower() in MEDIA_EXTS and p.is_file())
    return files


def extract_master(show_dir: Path) -> Path:
    """Produce audio/full.flac from the downloaded media (concatenating if the
    source is one-file-per-track, as archive.org items often are)."""
    audio_dir = show_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    master = audio_dir / "full.flac"
    if master.exists():
        log.info("master already exists: %s", master)
        return master

    sources = find_source_media(show_dir)
    if not sources:
        raise RuntimeError(f"no media files found in {show_dir}")

    if len(sources) == 1:
        cmd = [ffmpeg(), "-nostdin", "-i", str(sources[0]),
               "-vn", "-acodec", "flac", str(master)]
    else:
        concat = audio_dir / "concat.txt"
        concat.write_text("".join(
            f"file '{p.resolve().as_posix()}'\n" for p in sources))
        cmd = [ffmpeg(), "-nostdin", "-f", "concat", "-safe", "0",
               "-i", str(concat), "-vn", "-acodec", "flac", str(master)]

    log.info("extracting audio master (%d source file(s)) ...", len(sources))
    p = _run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{p.stderr[-2000:]}")
    return master


def duration_of(media: Path) -> float:
    p = _run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
              "-of", "default=noprint_wrappers=1:nokey=1", str(media)])
    return float(p.stdout.strip())


_SIL_END = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def detect_silences(media: Path, noise_db: int = -35, min_len: float = 1.5) -> list[float]:
    """Return candidate cut points (middle of each detected silence)."""
    p = _run([ffmpeg(), "-nostdin", "-i", str(media), "-af",
              f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"])
    cuts = []
    for m in _SIL_END.finditer(p.stderr):
        end, dur = float(m.group(1)), float(m.group(2))
        cuts.append(end - dur / 2)
    log.info("silence detection: %d candidate gaps", len(cuts))
    return cuts


def fit_names_to_silences(names: list[str], cuts: list[float],
                          total: float) -> list[dict]:
    """We know N song names and have M candidate gaps. Pick the N-1 most even
    cut points. Crude but a solid starting point for manual review."""
    need = len(names) - 1
    if len(cuts) < need:
        log.warning("only %d gaps for %d songs — falling back to even spacing",
                    len(cuts), len(names))
        cuts = [total * (i + 1) / len(names) for i in range(need)]
        chosen = cuts
    else:
        ideal = [total * (i + 1) / len(names) for i in range(need)]
        chosen, used = [], set()
        for tgt in ideal:
            best = min((c for c in cuts if c not in used),
                       key=lambda c: abs(c - tgt))
            used.add(best)
            chosen.append(best)
        chosen.sort()
    bounds = [0.0] + chosen + [total]
    return [{"title": names[i], "start": bounds[i], "end": bounds[i + 1]}
            for i in range(len(names))]


def _safe(name: str) -> str:
    return safe_filename(name, 100)


MIN_TRACK_LEN = 0.5
# Tracklists are written to the nearest second and durations are estimated
# from frame counts, so a sub-second overshoot is rounding, not a mismatch.
END_TOLERANCE = 1.5


def sanitize_tracks(tracks: list[dict], total: float) -> tuple[list[dict], list[str]]:
    """Clamp track boundaries to the real audio length and drop the ones that
    can't exist.

    A tracklist can easily disagree with the audio: a setlist posted in the
    comments may come from a different (longer) upload of the same show, a
    hand-edited tracks.json can have a typo, or the source video may be cut
    short.  Without this, ffmpeg is handed a start past the end of the file
    and dies halfway through the split, leaving a partial track set behind.
    """
    clean, warnings = [], []
    for i, t in enumerate(tracks, 1):
        start = float(t.get("start") or 0.0)
        end = float(t["end"]) if t.get("end") else total
        title = t.get("title") or f"Track {i:02d}"
        if start >= total:
            warnings.append(f"{title!r} starts at {_hms(start)}, past the end "
                            f"of the audio ({_hms(total)}) — skipped")
            continue
        clamped_end = min(end, total)
        if end - total > END_TOLERANCE:
            warnings.append(f"{title!r} ends at {_hms(end)}, past the end of "
                            f"the audio — trimmed to {_hms(total)}")
        if clamped_end - start < MIN_TRACK_LEN:
            warnings.append(f"{title!r} is shorter than {MIN_TRACK_LEN}s — skipped")
            continue
        clean.append({**t, "title": title, "start": start, "end": clamped_end})
    return clean, warnings


def cut_tracks(master: Path, tracks: list[dict], show: dict,
               artist: str = "Unknown Artist",
               album: Optional[str] = None, progress=None) -> list[Path]:
    total = duration_of(master)
    album = album or " - ".join(x for x in (show.get("date"), show.get("venue")) if x) \
        or "Live"
    out_dir = master.parent
    tracks, warnings = sanitize_tracks(tracks, total)
    for w in warnings:
        log.warning("tracklist: %s", w)
    if not tracks:
        raise RuntimeError("no usable tracks after checking the tracklist "
                           "against the audio length — use the timeline "
                           "editor to place the cuts by hand")

    written = []
    for i, t in enumerate(tracks, 1):
        if progress:
            progress(i, len(tracks), t["title"])
        start, end = t["start"], t["end"]
        out = out_dir / f"{i:02d} - {_safe(t['title'])}.flac"
        cmd = [ffmpeg(), "-nostdin", "-y", "-i", str(master),
               "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
               "-acodec", "flac",
               "-metadata", f"title={t['title']}",
               "-metadata", f"artist={artist}",
               "-metadata", f"album={album}",
               "-metadata", f"track={i}/{len(tracks)}",
               ]
        if show.get("date"):
            cmd += ["-metadata", f"date={show['date']}"]
        if show.get("venue"):
            cmd += ["-metadata", f"comment=Recorded live at {show['venue']}"]
        cmd.append(str(out))
        p = _run(cmd)
        if p.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on track {i}:\n{p.stderr[-2000:]}")
        log.info("wrote %s  [%s - %s]", out.name,
                 _hms(start), _hms(end))
        written.append(out)
    return written


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
