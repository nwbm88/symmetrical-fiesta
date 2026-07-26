"""Shared high-level operations used by both the CLI and the GUI.

`progress` callbacks take (fraction, message); fraction is 0..1 or None for
"busy, no percentage".
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from . import audio as audio_mod
from .tracks import resolve_tracks

log = logging.getLogger("livearchiver")

Progress = Callable[[Optional[float], str], None]


def split_show(show_dir: Path, setlist_key: Optional[str] = None,
               noise: int = -35, min_silence: float = 1.5,
               redetect: bool = False, cut: bool = True,
               progress: Optional[Progress] = None) -> dict:
    """Extract the audio master and cut it into named, tagged tracks.

    Returns {"tracks": [...], "strategy": str, "files": [...], "master": str}.
    """
    notify = progress or (lambda f, m: None)
    manifest_path = show_dir / "show.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found — not a show directory")
    manifest = json.loads(manifest_path.read_text())
    show = manifest["show"]

    notify(None, "extracting audio master ...")
    master = audio_mod.extract_master(show_dir)
    total = audio_mod.duration_of(master)

    tracks_path = show_dir / "audio" / "tracks.json"
    if tracks_path.exists() and not redetect:
        tracks = json.loads(tracks_path.read_text())
        strategy = "tracks.json (manual/previous)"
    else:
        tracks, strategy = resolve_tracks(manifest, setlist_key=setlist_key)
        if tracks and tracks[0]["start"] is None:
            # names came from setlist.fm — find cut points by silence
            notify(None, "detecting silences for cut points ...")
            cuts = audio_mod.detect_silences(master, noise, min_silence)
            tracks = audio_mod.fit_names_to_silences(
                [t["title"] for t in tracks], cuts, total)
            strategy += " + silence detection"
        elif not tracks:
            notify(None, "detecting silences for cut points ...")
            cuts = audio_mod.detect_silences(master, noise, min_silence)
            bounds = [0.0] + cuts + [total]
            tracks = [{"title": f"Track {i:02d}", "start": bounds[i - 1], "end": bounds[i]}
                      for i in range(1, len(bounds))]
            strategy = "silence detection only (generic names — rename in tracks.json)"
        tracks_path.parent.mkdir(exist_ok=True)
        tracks_path.write_text(json.dumps(tracks, indent=2, ensure_ascii=False))

    # The tracklist and the audio can disagree — a setlist posted in the
    # comments may belong to a longer upload of the same show.  Reconcile
    # before cutting so we never half-write a track set.
    tracks, warnings = audio_mod.sanitize_tracks(tracks, total)
    for w in warnings:
        log.warning("tracklist: %s", w)
        notify(None, f"tracklist warning: {w}")

    files: list[Path] = []
    if cut:
        def on_track(i, n, title):
            notify(i / n, f"cutting {i}/{n}: {title}")
        files = audio_mod.cut_tracks(master, tracks, show,
                                     artist=manifest.get("artist") or "Unknown Artist",
                                     progress=on_track)

    return {"tracks": tracks, "strategy": strategy, "warnings": warnings,
            "files": [str(f) for f in files], "master": str(master)}


_GENERIC_TRACK = re.compile(r"^\d\d - Track \d+\.flac$|^Track \d+$")


def show_issues(manifest: dict, track_files: list[str], tracklist: list[dict],
                has_master: bool) -> list[str]:
    """What still needs a human — surfaced as the ⚠ marking in the UI."""
    issues = []
    show = manifest.get("show", {})
    if not show.get("date"):
        issues.append("date unknown")
    elif len(show["date"]) < 10:
        issues.append(f"date incomplete ({show['date']})")
    if not show.get("venue"):
        issues.append("place unknown")
    names = track_files or [t.get("title", "") for t in tracklist]
    if names and any(_GENERIC_TRACK.match(n) for n in names):
        issues.append("tracks not identified (generic names)")
    if has_master and not track_files and not tracklist:
        issues.append("no tracklist found — resolve manually")
    return issues


def scan_collection(collection: Path) -> list[dict]:
    """Inventory of downloaded shows: manifest, master/split status, tracks,
    and outstanding issues.  Handles both the flat layout (collection/<show>/)
    and the per-artist layout (collection/<artist>/<show>/)."""
    shows = []
    if not collection.exists():
        return shows
    manifests = sorted(set(collection.glob("*/show.json")) |
                       set(collection.glob("*/*/show.json")))
    for mf in manifests:
        d = mf.parent
        try:
            manifest = json.loads(mf.read_text())
        except Exception:
            log.warning("unreadable show.json in %s — skipping", d)
            continue
        audio_dir = d / "audio"
        track_files = (sorted(p.name for p in audio_dir.glob("[0-9][0-9] - *.flac"))
                       if audio_dir.exists() else [])
        tracklist = []
        tl = audio_dir / "tracks.json"
        if tl.exists():
            try:
                tracklist = json.loads(tl.read_text())
            except Exception:
                pass
        has_master = (audio_dir / "full.flac").exists()
        shows.append({
            "dir": d,
            "artist": manifest.get("artist") or d.parent.name,
            "manifest": manifest,
            "has_master": has_master,
            "tracks": track_files,
            "tracklist": tracklist,
            "issues": show_issues(manifest, track_files, tracklist, has_master),
        })
    return shows
