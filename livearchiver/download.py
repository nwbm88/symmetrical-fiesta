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

log = logging.getLogger("livearchiver")


def _safe(name: str, maxlen: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:maxlen] or "untitled"


def show_dir_name(rec: dict) -> str:
    date = rec["show"].get("date") or "unknown-date"
    venue = rec["show"].get("venue") or rec["title"][:60]
    return _safe(f"{date} - {venue}")


def download(rec: dict, collection: Path, audio_only: bool = False,
             progress=None) -> Path:
    """progress: optional callable(fraction_or_None, message)."""
    artist = rec.get("artist") or "Unknown Artist"
    dest = collection / _safe(artist) / show_dir_name(rec)
    dest.mkdir(parents=True, exist_ok=True)

    if rec["source"] == "youtube":
        info = _download_youtube(rec, dest, audio_only, progress)
    elif rec["source"] == "archive.org":
        info = _download_archive_org(rec, dest, audio_only, progress)
    else:
        raise ValueError(f"unknown source {rec['source']}")

    # Refine the show guess with full metadata now that we have it.
    from .showinfo import extract_show_info
    full = extract_show_info(info.get("title") or rec["title"],
                             info.get("description") or "")
    show = dict(rec["show"])
    show["date"] = show.get("date") or full.date
    show["venue"] = show.get("venue") or full.venue

    # Still missing something?  Mine the comments/reviews — fans often post
    # the date, venue, or a full timestamped setlist there.
    comments = info.get("comments") or []
    for c in comments:
        if show["date"] and show["venue"]:
            break
        found = extract_show_info(c)
        show["date"] = show["date"] or found.date
        show["venue"] = show["venue"] or found.venue

    manifest = {
        "artist": artist,
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
        "comments": [c[:2000] for c in comments[:150]],
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

def _download_youtube(rec: dict, dest: Path, audio_only: bool,
                      progress=None) -> dict:
    from yt_dlp import YoutubeDL

    def hook(d):
        if not progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(d.get("downloaded_bytes", 0) / total,
                         f"downloading {d.get('_percent_str', '').strip()} "
                         f"{d.get('_speed_str', '').strip()}")
        elif d.get("status") == "finished":
            progress(None, "merging/processing ...")

    opts = {
        "outtmpl": str(dest / "source.%(ext)s"),
        "writeinfojson": True,
        "format": "bestaudio/best" if audio_only else "bestvideo+bestaudio/best",
        "merge_output_format": None if audio_only else "mkv",
        "noplaylist": True,
        "progress_hooks": [hook],
        # comments often carry the date/venue/setlist the title lacks
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": ["150"],
                                       "comment_sort": ["top"]}},
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
        "comments": [c.get("text", "") for c in info.get("comments") or []
                     if c.get("text")],
        "files": files,
    }


# ---------------------------------------------------------- Internet Archive

AUDIO_EXTS = (".flac", ".wav", ".shn", ".mp3", ".ogg", ".m4a")
VIDEO_EXTS = (".mkv", ".mp4", ".avi", ".mpg", ".mpeg", ".mov")


def _download_archive_org(rec: dict, dest: Path, audio_only: bool,
                          progress=None) -> dict:
    identifier = rec["id"].split(":", 1)[1]
    meta = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30).json()
    files = meta.get("files", [])

    chosen = _pick_ia_files(files, audio_only)
    if not chosen:
        raise RuntimeError(f"no downloadable media files found in {identifier}")

    total_bytes = sum(int(f.get("size", 0)) for f in chosen) or None
    done_bytes = 0
    saved = []
    for f in chosen:
        url = f"https://archive.org/download/{identifier}/{f['name']}"
        out = dest / _safe(Path(f["name"]).name)
        if out.exists() and out.stat().st_size == int(f.get("size", -1)):
            log.info("already have %s", out.name)
            saved.append(out.name)
            done_bytes += int(f.get("size", 0))
            continue
        log.info("downloading %s (%.1f MB)", f["name"], int(f.get("size", 0)) / 1e6)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
                    done_bytes += len(chunk)
                    if progress and total_bytes:
                        progress(min(done_bytes / total_bytes, 1.0),
                                 f"downloading {Path(f['name']).name}")
        saved.append(out.name)

    md = meta.get("metadata", {})
    reviews = [r.get("reviewbody", "") for r in meta.get("reviews") or []
               if r.get("reviewbody")]
    notes = md.get("notes")
    if isinstance(notes, str) and notes.strip():
        reviews.insert(0, notes)
    return {
        "title": md.get("title"),
        "description": md.get("description") if isinstance(md.get("description"), str) else "",
        "upload_date": (md.get("publicdate") or "")[:10].replace("-", "") or None,
        "duration": None,
        "chapters": None,
        "comments": reviews,
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
