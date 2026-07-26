"""Working out where the tracks are and what they're called.

Strategies, best first:

1. **Chapters** — YouTube chapters carry both timestamps and song titles.
2. **Description tracklist** — lines like "14:32 Heavydirtysoul" (or
   "Heavydirtysoul 14:32") in the video description.
3. **setlist.fm** — gives the song *names* for a show date when the video
   has no timestamps; combined with silence detection for the cut points.
   Needs a free API key (https://api.setlist.fm/docs/1.0/index.html).
4. **Silence detection only** — cut points from quiet gaps, generic names
   ("Track 01") for the user to rename later.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

log = logging.getLogger("livearchiver")

# Cleanup applied to song titles pulled from chapters/descriptions.
_JUNK = re.compile(r"^[\s\-–—:.\d)\]]+|[\s\-–—:.|]+$")


def _clean_title(t: str) -> str:
    orig = t.strip()
    t = re.sub(r"\((live|encore)\)$", "", orig, flags=re.I).strip()
    t = _JUNK.sub("", t).strip()
    return t or orig


def _ts_to_sec(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


# ------------------------------------------------------------- strategy 1+2

def tracks_from_chapters(chapters: list[dict]) -> list[dict]:
    out = []
    for ch in chapters:
        out.append({"title": _clean_title(ch.get("title") or ""),
                    "start": float(ch["start_time"]),
                    "end": float(ch["end_time"]) if ch.get("end_time") else None})
    return [t for t in out if t["title"]]


_TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
_LINE_TS_FIRST = re.compile(rf"^\W*({_TS})\W+(.+?)\s*$")
_LINE_TS_LAST = re.compile(rf"^(.+?)\W+({_TS})\W*$")


def tracks_from_description(description: str) -> list[dict]:
    """Parse a timestamp tracklist out of free-form description text."""
    cands = []
    for line in description.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_TS_FIRST.match(line)
        if m:
            cands.append((_ts_to_sec(m.group(1)), _clean_title(m.group(2))))
            continue
        m = _LINE_TS_LAST.match(line)
        if m:
            cands.append((_ts_to_sec(m.group(2)), _clean_title(m.group(1))))
    # A real tracklist is several lines with increasing timestamps.
    cands = [c for c in cands if c[1]]
    if len(cands) < 3:
        return []
    cands.sort(key=lambda c: c[0])
    if len({c[0] for c in cands}) < len(cands) * 0.8:
        return []
    tracks = []
    for i, (start, title) in enumerate(cands):
        end = cands[i + 1][0] if i + 1 < len(cands) else None
        tracks.append({"title": title, "start": float(start),
                       "end": float(end) if end else None})
    return tracks


# --------------------------------------------------------------- setlist.fm

SETLIST_API = "https://api.setlist.fm/rest/1.0/search/setlists"


def setlist_fm_songs(date: str, api_key: str, artist: str) -> Optional[list[str]]:
    """Song names (in order) for the show on ISO *date*, or None."""
    if not date or len(date) != 10:
        return None
    d = f"{date[8:10]}-{date[5:7]}-{date[0:4]}"  # API wants dd-MM-yyyy
    try:
        r = requests.get(SETLIST_API,
                         params={"artistName": artist, "date": d, "p": 1},
                         headers={"x-api-key": api_key, "Accept": "application/json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("setlist.fm lookup failed: %s", e)
        return None

    for sl in data.get("setlist", []):
        if sl.get("artist", {}).get("name", "").lower() != artist.lower():
            continue
        songs = []
        for st in sl.get("sets", {}).get("set", []):
            for song in st.get("song", []):
                name = song.get("name")
                if name and not song.get("tape"):
                    songs.append(name)
        if songs:
            venue = sl.get("venue", {})
            log.info("setlist.fm: matched %s @ %s (%d songs)",
                     sl.get("eventDate"), venue.get("name"), len(songs))
            return songs
    return None


# --------------------------------------------------------------- assembly

def resolve_tracks(manifest: dict, setlist_key: Optional[str] = None) -> tuple[list[dict], str]:
    """Return (tracks, strategy). tracks may have start/end == None when only
    names are known (silence detection fills in the cut points later)."""
    chapters = manifest.get("chapters") or []
    if len(chapters) >= 3:
        t = tracks_from_chapters(chapters)
        if len(t) >= 3:
            return t, "chapters"

    desc = manifest.get("description") or ""
    t = tracks_from_description(desc)
    if t:
        return t, "description tracklist"

    # Commenters often post the timestamped setlist the uploader didn't.
    best: list[dict] = []
    for c in manifest.get("comments") or []:
        t = tracks_from_description(c)
        if len(t) > len(best):
            best = t
    if best:
        return best, "tracklist found in comments"

    artist = manifest.get("artist")
    if setlist_key and artist:
        songs = setlist_fm_songs(manifest["show"].get("date"), setlist_key, artist)
        if songs:
            return [{"title": s, "start": None, "end": None} for s in songs], "setlist.fm"

    return [], "none"
