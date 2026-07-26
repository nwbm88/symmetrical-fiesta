"""Search backends.  Each returns a list of Recording dicts with a common shape:

{
  "id":        stable id, e.g. "yt:dQw4w9WgXcQ" or "ia:top-2016-06-14.flac16",
  "source":    "youtube" | "archive.org",
  "url":       canonical page URL,
  "title":     item title,
  "duration":  seconds or None,
  "uploader":  channel / uploader,
  "upload_date": "YYYYMMDD" or None (when it was *uploaded*, not performed),
  "views":     view/download count or None,
  "quality":   short human hint ("1080p", "FLAC", ...) when known,
  "show": {"date": ..., "venue": ...}   # heuristic guess, refined later
}

Adding another site = adding another search_* function that returns the
same shape and wiring it into SEARCHERS at the bottom.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from .showinfo import extract_show_info

log = logging.getLogger("livearchiver")

def default_queries(artist: str) -> list[str]:
    return [
        f"{artist} full concert",
        f"{artist} live full show",
        f"{artist} full set",
        f"{artist} live concert",
    ]


def _recording(source: str, rid: str, url: str, title: str, artist: str, **kw) -> dict:
    info = extract_show_info(title, kw.pop("description", "") or "")
    return {
        "id": f"yt:{rid}" if source == "youtube" else f"ia:{rid}",
        "source": source,
        "artist": artist,
        "url": url,
        "title": title,
        "duration": kw.get("duration"),
        "uploader": kw.get("uploader"),
        "upload_date": kw.get("upload_date"),
        "views": kw.get("views"),
        "quality": kw.get("quality"),
        "show": {"date": info.date, "venue": info.venue},
    }


# ---------------------------------------------------------------- YouTube

def search_youtube(artist: str, queries: list[str], limit: int = 50) -> list[dict]:
    from yt_dlp import YoutubeDL

    results: dict[str, dict] = {}
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    with YoutubeDL(opts) as ydl:
        for q in queries:
            log.info("YouTube: searching %r ...", q)
            try:
                data = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
            except Exception as e:  # network hiccup on one query shouldn't kill the run
                log.warning("YouTube search failed for %r: %s", q, e)
                continue
            for e in data.get("entries") or []:
                if not e or e.get("id") in results:
                    continue
                dur = e.get("duration")
                # Full shows run long; skip single-song clips under 15 minutes.
                if dur and dur < 15 * 60:
                    continue
                results[e["id"]] = _recording(
                    "youtube", e["id"],
                    e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                    e.get("title") or "",
                    artist,
                    duration=dur,
                    uploader=e.get("uploader") or e.get("channel"),
                    views=e.get("view_count"),
                )
    log.info("YouTube: %d candidate full-show videos", len(results))
    return list(results.values())


# ---------------------------------------------------------- Internet Archive

IA_SEARCH_URL = "https://archive.org/advancedsearch.php"


def search_archive_org(artist: str, limit: int = 200) -> list[dict]:
    params = {
        "q": f'("{artist}") AND (live OR concert OR festival OR tour) '
             'AND (mediatype:audio OR mediatype:movies)',
        "fl[]": ["identifier", "title", "date", "venue", "coverage",
                 "mediatype", "downloads", "creator", "format"],
        "rows": limit,
        "page": 1,
        "output": "json",
        "sort[]": "downloads desc",
    }
    log.info("archive.org: searching ...")
    try:
        r = requests.get(IA_SEARCH_URL, params=params, timeout=30)
        r.raise_for_status()
        docs = r.json()["response"]["docs"]
    except Exception as e:
        log.warning("archive.org search failed: %s", e)
        return []

    results = []
    for d in docs:
        title = d.get("title") or d["identifier"]
        creator = d.get("creator")
        if isinstance(creator, list):
            creator = ", ".join(creator)
        # archive.org items carry structured date/venue metadata — trust it
        # over title parsing when present.
        rec = _recording(
            "archive.org", d["identifier"],
            f"https://archive.org/details/{d['identifier']}",
            title,
            artist,
            uploader=creator,
            views=d.get("downloads"),
            quality=_ia_quality(d.get("format")),
        )
        date = (d.get("date") or "")[:10]
        if date:
            rec["show"]["date"] = date
        venue = d.get("venue") or d.get("coverage")
        if venue:
            rec["show"]["venue"] = venue if isinstance(venue, str) else ", ".join(venue)
        results.append(rec)
    log.info("archive.org: %d items", len(results))
    return results


def _ia_quality(formats) -> Optional[str]:
    if not formats:
        return None
    if isinstance(formats, str):
        formats = [formats]
    for pref in ("FLAC", "24bit Flac", "WAVE", "VBR MP3", "MPEG4", "h.264"):
        for f in formats:
            if pref.lower() in f.lower():
                return pref
    return None


SEARCHERS = {
    "youtube": lambda artist, queries, limit: search_youtube(artist, queries, limit),
    "archive.org": lambda artist, queries, limit: search_archive_org(artist, limit * 4),
}
