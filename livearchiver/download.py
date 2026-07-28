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

from .platformsupport import safe_filename, is_bot_check, BOT_CHECK_HELP

log = logging.getLogger("livearchiver")


class _Cancelled(Exception):
    """Internal: unwinds yt-dlp from inside its progress hook."""


def _has_cancel(exc: BaseException) -> bool:
    """yt-dlp re-wraps exceptions raised in hooks, so walk the chain."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, _Cancelled):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def _clean_partial(dest: Path):
    """Drop half-written files so a cancelled download leaves no debris."""
    for p in dest.glob("source.*"):
        try:
            p.unlink()
        except OSError:
            pass
    for p in dest.glob("*.part"):
        try:
            p.unlink()
        except OSError:
            pass


def _safe(name: str, maxlen: int = 120) -> str:
    return safe_filename(name, maxlen)


def _rename_to_match(dest: Path, rec: dict) -> Path:
    """Move a show folder to the name its final metadata deserves.

    Returns the folder actually in use — the original one if the rename is
    unnecessary, would collide, or the filesystem refuses (a file inside may
    still be locked on Windows).
    """
    wanted = show_dir_name(rec)
    if wanted == dest.name:
        return dest
    target = dest.parent / wanted
    if target.exists():
        return dest
    try:
        dest.rename(target)
        log.info("renamed show folder to %s", wanted)
        return target
    except OSError as e:
        log.warning("could not rename show folder to %r (%s) — keeping %r",
                    wanted, e, dest.name)
        return dest


def show_dir_name(rec: dict) -> str:
    date = rec["show"].get("date") or "unknown-date"
    venue = rec["show"].get("venue") or rec["title"][:60]
    return _safe(f"{date} - {venue}")


def download(rec: dict, collection: Path, audio_only: bool = False,
             progress=None, quality: str = None, should_cancel=None,
             cookies_browser: str = "") -> Path:
    """Fetch a recording into the collection.

    progress:      callable(fraction_or_None, message)
    quality:       key from jobs.QUALITY_CHOICES ("best", "720", "audio", ...)
    should_cancel: callable returning True to abort the download
    """
    if quality is None:
        quality = "audio" if audio_only else "best"
    audio_only = quality == "audio"
    artist = rec.get("artist") or "Unknown Artist"
    dest = collection / _safe(artist) / show_dir_name(rec)
    dest.mkdir(parents=True, exist_ok=True)

    if rec["source"] == "youtube":
        info = _download_youtube(rec, dest, quality, progress, should_cancel,
                                 cookies_browser)
    elif rec["source"] == "archive.org":
        info = _download_archive_org(rec, dest, audio_only, progress,
                                     should_cancel)
    else:
        raise ValueError(f"unknown source {rec['source']}")

    # Refine the show guess with full metadata now that we have it.  Check
    # the catalogue title as well as the fetched one: an extractor can return
    # something less informative than the search result (the generic
    # extractor, for instance, uses the filename), and throwing that away
    # loses the date and venue we already had.
    from .showinfo import extract_show_info
    show = dict(rec["show"])
    for title, desc in ((info.get("title"), info.get("description")),
                        (rec.get("title"), "")):
        if show.get("date") and show.get("venue"):
            break
        if not title:
            continue
        found = extract_show_info(title, desc or "")
        show["date"] = show.get("date") or found.date
        show["venue"] = show.get("venue") or found.venue

    # Still missing something?  Mine the comments/reviews — fans often post
    # the date, venue, or a full timestamped setlist there.
    comments = info.get("comments") or []
    for c in comments:
        if show["date"] and show["venue"]:
            break
        found = extract_show_info(c)
        show["date"] = show["date"] or found.date
        show["venue"] = show["venue"] or found.venue

    # The folder was named before we knew the date and venue — now that we
    # do, put it where it belongs, so the archive stays organised by show.
    dest = _rename_to_match(dest, {**rec, "show": show})

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

def _download_youtube(rec: dict, dest: Path, quality: str,
                      progress=None, should_cancel=None,
                      cookies_browser: str = "") -> dict:
    from yt_dlp import YoutubeDL
    from .jobs import format_string

    audio_only = quality == "audio"

    def hook(d):
        # The progress hook is the only place yt-dlp lets us interrupt a
        # download in flight; raising here unwinds it cleanly.
        if should_cancel and should_cancel():
            raise _Cancelled()
        if not progress:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            speed = (d.get("_speed_str") or "").strip()
            eta = (d.get("_eta_str") or "").strip()
            detail = " ".join(x for x in (speed, f"ETA {eta}" if eta else "") if x)
            if total:
                progress(d.get("downloaded_bytes", 0) / total,
                         f"downloading {d.get('_percent_str', '').strip()} {detail}")
            else:
                progress(None, f"downloading {detail}")
        elif d.get("status") == "finished":
            progress(None, "merging/processing ...")

    opts = {
        "outtmpl": str(dest / "source.%(ext)s"),
        "writeinfojson": True,
        "format": format_string(quality),
        "merge_output_format": None if audio_only else "mkv",
        "noplaylist": True,
        "progress_hooks": [hook],
        # comments often carry the date/venue/setlist the title lacks
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": ["150"],
                                       "comment_sort": ["top"]}},
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(rec["url"], download=True)
    except _Cancelled:
        _clean_partial(dest)
        from .jobs import JobCancelled
        raise JobCancelled()
    except Exception as e:
        # yt-dlp wraps hook exceptions, so check for our marker in the chain
        if _has_cancel(e):
            _clean_partial(dest)
            from .jobs import JobCancelled
            raise JobCancelled()
        if is_bot_check(str(e)):
            _clean_partial(dest)
            raise RuntimeError(BOT_CHECK_HELP) from e
        raise

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
                          progress=None, should_cancel=None) -> dict:
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
                    if should_cancel and should_cancel():
                        fh.close()
                        try:
                            out.unlink()
                        except OSError:
                            pass
                        from .jobs import JobCancelled
                        raise JobCancelled()
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
