"""Downloading a chosen recording into the collection.

Layout on disk (one directory per show):

    collection/
      2016-06-14 - Hallenstadion Zurich/
        show.json          <- provenance: where/when filmed, source URL, etc.
        source.mkv         <- original download (kept for preservation)
        source.info.json   <- raw yt-dlp metadata (chapters, description...)
        audio/
          full.flac
          01 - Heavydirtysoul.flac
          ...
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("topsarchiver")


def _safe(name: str, maxlen: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:maxlen] or "untitled"


def show_dir_name(rec: dict) -> str:
    date = rec["show"].get("date") or "unknown-date"
    venue = rec["show"].get("venue") or rec["title"][:60]
    return _safe(f"{date} - {venue}")


def download(rec: dict, collection: Path, audio_only: bool = False) -> Path:
    dest = collection / show_dir_name(rec)
    dest.mkdir(parents=True, exist_ok=True)

    if rec["source"] == "youtube":
        info = _download_youtube(rec, dest, audio_only)
    elif rec["source"] == "archive.org":
        info = _download_archive_org(rec, dest, audio_only)
    else:
        raise ValueError(f"unknown source {rec['source']}")

    # Refine the show guess with full metadata now that we have it.
    from .showinfo import extract_show_info
    full = extract_show_info(info.get("title") or rec["title"],
                             info.get("description") or "")
    show = dict(rec["show"])
    show["date"] = show.get("date") or full.date
    show["venue"] = show.get("venue") or full.venue

    manifest = {
        "artist": "Twenty One Pilots",
        "show": show,
        "recording_id": rec["id"],
        "source": rec["source"],
        "source_url": rec["url"],
        "title": info.get("title") or rec["title"],
        "uploader": rec.get("uploader"),
        "upload_date": info.get("upload_date") or rec.get("upload_date"),
        "duration": info.get("duration") or rec.get("duration"),
        "files": info.get("files", []),
        "chapters": info.get("chapters") or [],
        "description": (info.get("description") or "")[:8000],
    }
    (dest / "show.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    log.info("saved %s", dest / "show.json")

    if not show["date"] or not show["venue"]:
        log.warning("could not determine %s for this show — edit %s and fill in "
                    '"show": {"date": ..., "venue": ...} by hand',
                    "date and venue" if not show["date"] and not show["venue"]
                    else ("date" if not show["date"] else "venue"),
                    dest / "show.json")
    return dest


# ---------------------------------------------------------------- YouTube

def _download_youtube(rec: dict, dest: Path, audio_only: bool) -> dict:
    from yt_dlp import YoutubeDL

    opts = {
        "outtmpl": str(dest / "source.%(ext)s"),
        "writeinfojson": True,
        "format": "bestaudio/best" if audio_only else "bestvideo+bestaudio/best",
        "merge_output_format": None if audio_only else "mkv",
        "noplaylist": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(rec["url"], download=True)

    files = [p.name for p in dest.iterdir() if p.name.startswith("source.")
             and not p.name.endswith(".info.json")]
    return {
        "title": info.get("title"),
        "description": info.get("description"),
        "upload_date": info.get("upload_date"),
        "duration": info.get("duration"),
        "chapters": info.get("chapters"),
        "files": files,
    }


# ---------------------------------------------------------- Internet Archive

AUDIO_EXTS = (".flac", ".wav", ".shn", ".mp3", ".ogg", ".m4a")
VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".mpg", ".mpeg", ".mov")


def _download_archive_org(rec: dict, dest: Path, audio_only: bool) -> dict:
    identifier = rec["id"].split(":", 1)[1]
    meta = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30).json()
    files = meta.get("files", [])

    chosen = _pick_ia_files(files, audio_only)
    if not chosen:
        raise RuntimeError(f"no downloadable media files found in {identifier}")

    saved = []
    for f in chosen:
        url = f"https://archive.org/download/{identifier}/{f['name']}"
        out = dest / _safe(Path(f["name"]).name)
        if out.exists() and out.stat().st_size == int(f.get("size", -1)):
            log.info("already have %s", out.name)
            saved.append(out.name)
            continue
        log.info("downloading %s (%.1f MB)", f["name"], int(f.get("size", 0)) / 1e6)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        saved.append(out.name)

    md = meta.get("metadata", {})
    return {
        "title": md.get("title"),
        "description": md.get("description") if isinstance(md.get("description"), str) else "",
        "upload_date": (md.get("publicdate") or "")[:10].replace("-", "") or None,
        "duration": None,
        "chapters": None,
        "files": saved,
    }


def _pick_ia_files(files: list[dict], audio_only: bool) -> list[dict]:
    """Prefer original (non-derivative) files; lossless audio first.
    A concert item is either one big file or one file per track — take
    every original media file and let the split step sort it out."""
    def rank(f):
        ext = Path(f["name"]).suffix.lower()
        orig = 0 if f.get("source") == "original" else 1
        if ext in (".flac", ".wav", ".shn"):
            kind = 0
        elif ext in VIDEO_EXTS:
            kind = 1 if not audio_only else 2
        elif ext in AUDIO_EXTS:
            kind = 2 if not audio_only else 1
        else:
            return None
        return (orig, kind)

    ranked = [(rank(f), f) for f in files]
    ranked = [(r, f) for r, f in ranked if r is not None]
    if not ranked:
        return []
    best = min(r for r, _ in ranked)
    return sorted((f for r, f in ranked if r == best), key=lambda f: f["name"])
