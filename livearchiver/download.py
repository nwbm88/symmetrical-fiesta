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
from .integrity import (record_checksums, free_space, human_bytes)

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
    """Drop half-written files. Only used when the user cancels — an
    *interrupted* download keeps its .part file so it can resume."""
    for pattern in ("source.*", "*.part", "*.ytdl"):
        for p in dest.glob(pattern):
            try:
                p.unlink()
            except OSError:
                pass
    try:
        if not any(dest.iterdir()):
            dest.rmdir()
    except OSError:
        pass


def partial_bytes(dest: Path) -> int:
    """How much of an interrupted download is already on disk."""
    return sum(p.stat().st_size for p in dest.glob("*.part") if p.is_file())


class NotEnoughSpace(RuntimeError):
    pass


def _check_space(dest: Path, needed: int):
    """Refuse to start a download that obviously cannot fit."""
    if needed <= 0:
        return
    free = free_space(dest)
    if free < 0:
        return                       # couldn't tell; let it try
    if free < needed:
        raise NotEnoughSpace(
            f"Not enough disk space: this download needs about "
            f"{human_bytes(needed)} but only {human_bytes(free)} is free on "
            f"the drive holding your collection.\n\nFree some space, or "
            f"choose a lower quality / audio-only in the queue.")


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


def _fetch_cover(rec: dict, dest: Path):
    """Save the source thumbnail as cover.jpg for later embedding."""
    from .tagging import find_cover, save_cover
    from .thumbnails import full_size_urls
    if find_cover(dest):
        return
    for url in full_size_urls(rec):
        try:
            r = requests.get(url, timeout=20)
            if r.ok and len(r.content) > 1000:
                save_cover(dest, r.content)
                return
        except Exception:
            continue


def show_dir_name(rec: dict) -> str:
    show = rec.get("show") or {}
    date = show.get("date") or "unknown-date"
    venue = show.get("venue") or (rec.get("title") or "untitled show")[:60]
    return _safe(f"{date} - {venue}")


def download(rec: dict, collection: Path, audio_only: bool = False,
             progress=None, quality: str = None, should_cancel=None,
             cookies_browser: str = "") -> Path:
    """Fetch a recording into the collection.

    progress:      callable(fraction_or_None, message)
    quality:       key from jobs.QUALITY_CHOICES ("best", "720", "audio", ...)
    should_cancel: callable returning True to abort the download
    """
    source = rec.get("source")
    if source not in ("youtube", "archive.org"):
        # Check before creating folders, so a bad record fails with a clear
        # message rather than a stray KeyError from somewhere downstream.
        raise ValueError(
            f"don't know how to download from {source!r} — expected "
            f"'youtube' or 'archive.org'")

    if quality is None:
        quality = "audio" if audio_only else "best"
    audio_only = quality == "audio"
    artist = rec.get("artist") or "Unknown Artist"
    dest = collection / _safe(artist) / show_dir_name(rec)
    dest.mkdir(parents=True, exist_ok=True)

    if rec["source"] == "youtube":
        info = _download_youtube(rec, dest, quality, progress, should_cancel,
                                 cookies_browser)
    else:
        info = _download_archive_org(rec, dest, audio_only, progress,
                                     should_cancel)

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

    # Grab the thumbnail once, for embedding as album art at split time.
    try:
        _fetch_cover(rec, dest)
    except Exception as e:
        log.debug("cover art unavailable: %s", e)

    # Record what we got, so corruption or truncation can be detected later.
    try:
        if progress:
            progress(None, "recording checksums ...")
        record_checksums(dest)
    except Exception as e:
        log.warning("could not record checksums: %s", e)

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

    checked_space = [False]

    def hook(d):
        # The progress hook is the only place yt-dlp lets us interrupt a
        # download in flight; raising here unwinds it cleanly.
        if should_cancel and should_cancel():
            raise _Cancelled()
        if not checked_space[0] and d.get("status") == "downloading":
            checked_space[0] = True
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            # video and audio are downloaded separately then merged, so the
            # peak requirement is roughly twice the final size
            _check_space(dest, int(total * 2.2))
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
        # keep .part files and pick them up next time: a 4 GB download that
        # dies at 90% should not start again from zero
        "continuedl": True,
        "nopart": False,
        "retries": 10,
        "fragment_retries": 10,
        # comments often carry the date/venue/setlist the title lacks
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": ["150"],
                                       "comment_sort": ["top"]}},
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    resumed = partial_bytes(dest)
    if resumed and progress:
        progress(None, f"resuming — {human_bytes(resumed)} already downloaded")
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
    _check_space(dest, int((total_bytes or 0) * 1.1))
    done_bytes = 0
    saved = []
    for f in chosen:
        url = f"https://archive.org/download/{identifier}/{f['name']}"
        out = dest / _safe(Path(f["name"]).name)
        expected = int(f.get("size", -1))
        if out.exists() and out.stat().st_size == expected:
            log.info("already have %s", out.name)
            saved.append(out.name)
            done_bytes += max(expected, 0)
            continue

        # Resume a part-downloaded file rather than starting over.
        part = out.with_suffix(out.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = {}
        mode = "wb"
        if have and expected > 0 and have < expected:
            headers["Range"] = f"bytes={have}-"
            mode = "ab"
            log.info("resuming %s at %s", f["name"], human_bytes(have))
            if progress:
                progress(None, f"resuming {Path(f['name']).name} at "
                               f"{human_bytes(have)}")
        else:
            have = 0

        log.info("downloading %s (%.1f MB)", f["name"], max(expected, 0) / 1e6)
        with requests.get(url, stream=True, timeout=60, headers=headers) as r:
            if have and r.status_code == 200:
                # server ignored the range request — start clean
                have, mode = 0, "wb"
            elif have and r.status_code != 206:
                r.raise_for_status()
            else:
                r.raise_for_status()
            done_bytes += have
            with open(part, mode) as fh:
                for chunk in r.iter_content(1 << 20):
                    if should_cancel and should_cancel():
                        fh.close()
                        part.unlink(missing_ok=True)
                        from .jobs import JobCancelled
                        raise JobCancelled()
                    fh.write(chunk)
                    done_bytes += len(chunk)
                    if progress and total_bytes:
                        progress(min(done_bytes / total_bytes, 1.0),
                                 f"downloading {Path(f['name']).name}")
        part.replace(out)
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
