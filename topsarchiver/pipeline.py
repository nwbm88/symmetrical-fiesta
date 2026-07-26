"""Shared high-level operations used by both the CLI and the GUI.

`progress` callbacks take (fraction, message); fraction is 0..1 or None for
"busy, no percentage".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from . import audio as audio_mod
from .tracks import resolve_tracks

log = logging.getLogger("topsarchiver")

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

    files: list[Path] = []
    if cut:
        def on_track(i, n, title):
            notify(i / n, f"cutting {i}/{n}: {title}")
        files = audio_mod.cut_tracks(master, tracks, show, progress=on_track)

    return {"tracks": tracks, "strategy": strategy,
            "files": [str(f) for f in files], "master": str(master)}


def scan_collection(collection: Path) -> list[dict]:
    """Inventory of downloaded shows: manifest, master/split status, tracks."""
    shows = []
    if not collection.exists():
        return shows
    for d in sorted(collection.iterdir()):
        mf = d / "show.json"
        if not d.is_dir() or not mf.exists():
            continue
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
        shows.append({
            "dir": d,
            "manifest": manifest,
            "has_master": (audio_dir / "full.flac").exists(),
            "tracks": track_files,
            "tracklist": tracklist,
        })
    return shows
