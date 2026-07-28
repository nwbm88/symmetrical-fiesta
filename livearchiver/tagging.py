"""Finishing touches that make the archive usable elsewhere.

* **Cue sheet** — FLAC plus a ``.cue`` is what taper communities trade in.
  It records the exact track boundaries against the untouched master, so the
  split can be reproduced byte-for-byte by anyone, and players that read cue
  sheets can navigate the show without it being cut at all.
* **Album art** — the video thumbnail embedded in every track, so the show
  looks like an album in any player.
* **ReplayGain** — audience recordings vary enormously in level; without it
  a shuffle through the archive is a volume-knob workout.  Tags only, the
  audio is never re-encoded.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from .platformsupport import ffmpeg, popen_kwargs

log = logging.getLogger("livearchiver")


# ---------------------------------------------------------------- cue sheet

def _cue_time(seconds: float) -> str:
    """CUE uses MM:SS:FF with 75 frames per second."""
    total_frames = int(round(seconds * 75))
    minutes, rem = divmod(total_frames, 75 * 60)
    secs, frames = divmod(rem, 75)
    return f"{minutes:02d}:{secs:02d}:{frames:02d}"


def _cue_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', "'")


def write_cue_sheet(show_dir: Path, tracks: list[dict], show: dict,
                    artist: str, master_name: str = "full.flac") -> Path:
    """Write audio/show.cue describing the tracks against the master."""
    audio_dir = show_dir / "audio"
    audio_dir.mkdir(exist_ok=True)
    album = " - ".join(x for x in (show.get("date"), show.get("venue")) if x) \
        or "Live"

    lines = [f'PERFORMER "{_cue_escape(artist)}"',
             f'TITLE "{_cue_escape(album)}"']
    if show.get("date") and len(show["date"]) >= 4:
        lines.append(f'REM DATE {show["date"][:4]}')
    if show.get("venue"):
        lines.append(f'REM VENUE "{_cue_escape(show["venue"])}"')
    if show.get("date"):
        lines.append(f'REM SHOWDATE {show["date"]}')
    lines.append(f'FILE "{master_name}" WAVE')

    for i, t in enumerate(tracks, 1):
        lines += [f"  TRACK {i:02d} AUDIO",
                  f'    TITLE "{_cue_escape(t.get("title") or f"Track {i:02d}")}"',
                  f'    PERFORMER "{_cue_escape(artist)}"',
                  f"    INDEX 01 {_cue_time(float(t.get('start') or 0))}"]

    out = audio_dir / "show.cue"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("wrote cue sheet %s", out.name)
    return out


# --------------------------------------------------------------- album art

def find_cover(show_dir: Path) -> Optional[Path]:
    for name in ("cover.jpg", "cover.png", "folder.jpg", "thumbnail.jpg"):
        p = show_dir / name
        if p.exists():
            return p
    return None


def save_cover(show_dir: Path, data: bytes) -> Optional[Path]:
    try:
        out = show_dir / "cover.jpg"
        out.write_bytes(data)
        return out
    except OSError as e:
        log.warning("could not save cover art: %s", e)
        return None


def embed_cover(track: Path, cover: Path) -> bool:
    """Attach cover art to a FLAC, in place."""
    tmp = track.with_suffix(".arttmp.flac")
    cmd = [ffmpeg(), "-nostdin", "-y", "-i", str(track), "-i", str(cover),
           "-map", "0:a", "-map", "1:v", "-c", "copy",
           "-disposition:v", "attached_pic",
           "-metadata:s:v", "title=Album cover",
           "-metadata:s:v", "comment=Cover (front)",
           str(tmp)]
    p = subprocess.run(cmd, capture_output=True, text=True, **popen_kwargs())
    if p.returncode != 0 or not tmp.exists():
        log.warning("could not embed cover in %s: %s", track.name,
                    p.stderr[-200:])
        tmp.unlink(missing_ok=True)
        return False
    try:
        tmp.replace(track)
        return True
    except OSError as e:
        log.warning("could not replace %s: %s", track.name, e)
        tmp.unlink(missing_ok=True)
        return False


# --------------------------------------------------------------- ReplayGain

_LOUDNESS = re.compile(r"I:\s*(-?[\d.]+)\s*LUFS")
_PEAK = re.compile(r"Peak:\s*(-?[\d.]+)\s*dBFS")
REFERENCE_LUFS = -18.0     # ReplayGain 2.0 reference


def measure_loudness(path: Path) -> Optional[tuple[float, float]]:
    """(integrated LUFS, peak dBFS) for a file, via ffmpeg's ebur128."""
    cmd = [ffmpeg(), "-nostdin", "-i", str(path),
           "-af", "ebur128=peak=true", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, **popen_kwargs())
    # the summary block at the end carries the integrated figures
    tail = p.stderr[-2000:]
    loud = _LOUDNESS.findall(tail)
    peak = _PEAK.findall(tail)
    if not loud:
        return None
    try:
        return float(loud[-1]), float(peak[-1]) if peak else 0.0
    except ValueError:
        return None


def _peak_amplitude(peak_dbfs: float) -> float:
    return min(10 ** (peak_dbfs / 20.0), 1.0) if peak_dbfs else 1.0


def apply_replaygain(tracks: list[Path], album_lufs: Optional[float] = None,
                     album_peak: float = 1.0, progress=None) -> int:
    """Write ReplayGain tags. Audio is untouched — only tags change."""
    tagged = 0
    for i, track in enumerate(tracks, 1):
        if progress:
            progress(i / max(len(tracks), 1), f"measuring loudness {i}/{len(tracks)}")
        measured = measure_loudness(track)
        if measured is None:
            continue
        lufs, peak_db = measured
        gain = REFERENCE_LUFS - lufs
        peak = _peak_amplitude(peak_db)
        meta = [
            "-metadata", f"REPLAYGAIN_TRACK_GAIN={gain:.2f} dB",
            "-metadata", f"REPLAYGAIN_TRACK_PEAK={peak:.6f}",
        ]
        if album_lufs is not None:
            meta += ["-metadata",
                     f"REPLAYGAIN_ALBUM_GAIN={REFERENCE_LUFS - album_lufs:.2f} dB",
                     "-metadata", f"REPLAYGAIN_ALBUM_PEAK={album_peak:.6f}"]
        tmp = track.with_suffix(".rgtmp.flac")
        cmd = [ffmpeg(), "-nostdin", "-y", "-i", str(track),
               "-c", "copy", *meta, str(tmp)]
        p = subprocess.run(cmd, capture_output=True, text=True, **popen_kwargs())
        if p.returncode == 0 and tmp.exists():
            try:
                tmp.replace(track)
                tagged += 1
                continue
            except OSError:
                pass
        tmp.unlink(missing_ok=True)
    return tagged


def finish_tracks(show_dir: Path, tracks: list[dict], track_files: list[Path],
                  show: dict, artist: str,
                  write_cue: bool = True, art: bool = True,
                  replaygain: bool = True, progress=None) -> dict:
    """Apply the whole finishing pass; every part is optional and best-effort."""
    out = {"cue": None, "cover": 0, "replaygain": 0}

    if write_cue and tracks:
        try:
            out["cue"] = str(write_cue_sheet(show_dir, tracks, show, artist))
        except Exception as e:
            log.warning("cue sheet failed: %s", e)

    if art:
        cover = find_cover(show_dir)
        if cover:
            if progress:
                progress(None, "embedding album art ...")
            for t in track_files:
                if embed_cover(t, cover):
                    out["cover"] += 1

    if replaygain and track_files:
        master = show_dir / "audio" / "full.flac"
        album = measure_loudness(master) if master.exists() else None
        out["replaygain"] = apply_replaygain(
            track_files,
            album_lufs=album[0] if album else None,
            album_peak=_peak_amplitude(album[1]) if album else 1.0,
            progress=progress)
    return out
