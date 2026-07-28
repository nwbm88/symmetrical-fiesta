"""The catalog: every recording found, grouped so duplicates of the same
show sit together and the user can pick which version to download.

Grouping key: the show date (the strongest signal two uploads are the same
concert).  Within a date group, recordings are ranked by a quality score so
the suggested pick is first.  Undated recordings are grouped by normalised
venue text, and failing that stand alone.
"""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("livearchiver")


EMPTY = {"recordings": {}, "downloaded": {}, "ignored": {},
         "finished_artists": {}}


def _normalise(cat: dict) -> dict:
    """Make a catalogue safe to use however it was edited."""
    if not isinstance(cat, dict):
        return dict(EMPTY)
    for key, default in EMPTY.items():
        if not isinstance(cat.get(key), dict):
            cat[key] = dict(default) if isinstance(default, dict) else default
    # Every recording needs a show dict; a hand-edited catalogue may not have
    # one, and a KeyError here would take the whole Get Music view down.
    for rid, rec in list(cat["recordings"].items()):
        if not isinstance(rec, dict):
            del cat["recordings"][rid]
            continue
        rec.setdefault("id", rid)
        if not isinstance(rec.get("show"), dict):
            rec["show"] = {}
    return cat


def load(path: Path) -> dict:
    """Read the catalogue, falling back to the newest backup if it is
    corrupt.  A damaged catalogue must never stop the app from starting —
    and since the original is left untouched, nothing is lost by trying."""
    path = Path(path)
    if not path.exists():
        return dict(EMPTY)
    try:
        return _normalise(json.loads(path.read_text()))
    except Exception as e:
        log.error("%s is unreadable (%s)", path, e)

    from .integrity import latest_backup
    backup = latest_backup(path)
    if backup:
        try:
            cat = _normalise(json.loads(backup.read_text()))
            log.warning("recovered the catalogue from %s — the damaged file "
                        "has been left alone as %s", backup.name, path.name)
            return cat
        except Exception as e:
            log.error("the backup %s is unreadable too (%s)", backup, e)
    log.warning("starting with an empty catalogue; %s was left untouched",
                path)
    return dict(EMPTY)


def save(cat: dict, path: Path, backup: bool = True) -> None:
    """Write the catalogue, keeping rotating backups of the previous state.

    The catalogue holds curation that cannot be re-downloaded — which
    duplicate you chose, what you ignored, which bands are finished — so it
    is worth a few kilobytes of insurance.  The write itself goes via a
    temporary file so an interrupted save cannot leave a truncated catalogue.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        try:
            from .integrity import backup_catalog
            backup_catalog(path)
        except Exception:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cat, indent=2, ensure_ascii=False))
    tmp.replace(path)


def merge_results(cat: dict, results: list[dict]) -> int:
    added = 0
    for rec in results:
        if rec["id"] not in cat["recordings"]:
            added += 1
        cat["recordings"][rec["id"]] = rec
    return added


def _norm_venue(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    v = re.sub(r"[^a-z0-9 ]", "", v.lower())
    v = re.sub(r"\b(the|at|in|a|an)\b", "", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v or None


def quality_score(rec: dict) -> float:
    """Higher = better candidate. Duration dominates (a longer upload of the
    same show is usually the more complete one), lossless beats lossy,
    archive.org's structured metadata is a plus, popularity breaks ties."""
    score = 0.0
    if rec.get("duration"):
        score += min(rec["duration"], 3 * 3600) / 60.0          # up to ~180
    q = (rec.get("quality") or "").lower()
    if "flac" in q or "wav" in q or "24bit" in q:
        score += 120
    if rec.get("source") == "archive.org":
        score += 20
    if rec.get("views"):
        score += min(rec["views"], 10**6) ** 0.5 / 10.0          # up to 100
    return score


def group_recordings(cat: dict) -> list[dict]:
    """Return [{key, artist, date, venue, recordings:[rec,...]}] sorted by
    artist then date.  Recordings of different artists never share a group."""
    groups: dict[str, dict] = {}
    for rec in cat["recordings"].values():
        artist = rec.get("artist") or ""
        show = rec.get("show") or {}
        date = show.get("date")
        venue = show.get("venue")
        if date and len(date) == 10:
            key = f"{artist.lower()}|date:{date}"
        elif date and _norm_venue(venue):
            key = f"{artist.lower()}|dv:{date}:{_norm_venue(venue)}"
        elif _norm_venue(venue):
            key = f"{artist.lower()}|venue:{_norm_venue(venue)}"
        else:
            key = f"{artist.lower()}|solo:{rec['id']}"
        g = groups.setdefault(key, {"key": key, "artist": artist, "date": date,
                                    "venue": None, "recordings": []})
        g["recordings"].append(rec)
        if venue and (not g["venue"] or len(venue) > len(g["venue"])):
            g["venue"] = venue

    for g in groups.values():
        g["recordings"].sort(key=quality_score, reverse=True)
    return sorted(groups.values(),
                  key=lambda g: (g["artist"].lower(), g["date"] or "9999", g["key"]))


def artists_in(cat: dict) -> set[str]:
    return {r.get("artist") or "" for r in cat["recordings"].values()}


def artist_stats(cat: dict, shows: Optional[list] = None) -> list[dict]:
    """Per-artist progress for the Artists tab.

    ``shows`` is the output of pipeline.scan_collection, used to count how
    many downloaded shows are actually split into tracks.
    """
    downloaded = cat.get("downloaded", {})
    ignored = cat.get("ignored", {})
    finished = cat.get("finished_artists", {})

    split_by_artist: dict[str, int] = {}
    for s in shows or []:
        if s.get("tracks"):
            a = s.get("artist") or ""
            split_by_artist[a] = split_by_artist.get(a, 0) + 1

    out: dict[str, dict] = {}
    for rec in cat["recordings"].values():
        a = rec.get("artist") or "Unknown"
        e = out.setdefault(a, {"artist": a, "found": 0, "downloaded": 0,
                               "ignored": 0, "shows": set()})
        e["found"] += 1
        if rec["id"] in downloaded:
            e["downloaded"] += 1
        if rec["id"] in ignored:
            e["ignored"] += 1
        e["shows"].add((rec.get("show") or {}).get("date") or rec.get("id"))

    for a in split_by_artist:
        out.setdefault(a, {"artist": a, "found": 0, "downloaded": 0,
                           "ignored": 0, "shows": set()})

    result = []
    for a, e in out.items():
        remaining = max(e["found"] - e["downloaded"] - e["ignored"], 0)
        result.append({
            "artist": a,
            "found": e["found"],
            "distinct_shows": len(e["shows"]),
            "downloaded": e["downloaded"],
            "ignored": e["ignored"],
            "remaining": remaining,
            "split": split_by_artist.get(a, 0),
            "marked_done": bool(finished.get(a)),
            "complete": remaining == 0 and e["downloaded"] > 0,
        })
    return sorted(result, key=lambda r: r["artist"].lower())


def remove_artist(cat: dict, artist: str) -> int:
    """Drop an artist and everything catalogued for them. Files on disk are
    left alone — this only clears the hub."""
    ids = [rid for rid, rec in cat["recordings"].items()
           if (rec.get("artist") or "Unknown") == artist]
    for rid in ids:
        cat["recordings"].pop(rid, None)
        cat.get("downloaded", {}).pop(rid, None)
        cat.get("ignored", {}).pop(rid, None)
    cat.get("finished_artists", {}).pop(artist, None)
    return len(ids)


def set_artist_done(cat: dict, artist: str, done: bool) -> None:
    cat.setdefault("finished_artists", {})
    if done:
        cat["finished_artists"][artist] = True
    else:
        cat["finished_artists"].pop(artist, None)


def _fmt_dur(seconds) -> str:
    if not seconds:
        return "  ?:??"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:3d}:{s:02d}"


def print_groups(cat: dict, only_dupes: bool = False, only_new: bool = False) -> None:
    downloaded = cat.get("downloaded", {})
    groups = group_recordings(cat)
    shown = 0
    for g in groups:
        recs = g["recordings"]
        if only_dupes and len(recs) < 2:
            continue
        if only_new and all(r["id"] in downloaded for r in recs):
            continue
        shown += 1
        head = g["date"] or "unknown date"
        if g["venue"]:
            head += f" — {g['venue']}"
        if g.get("artist") and len(artists_in(cat)) > 1:
            head = f"[{g['artist']}] {head}"
        dupe = f"  ({len(recs)} versions)" if len(recs) > 1 else ""
        print(f"\n{head}{dupe}")
        for i, r in enumerate(recs):
            mark = "*" if r["id"] in downloaded else (">" if i == 0 and len(recs) > 1 else " ")
            bits = [_fmt_dur(r.get("duration")), r["source"]]
            if r.get("quality"):
                bits.append(r["quality"])
            if r.get("views"):
                bits.append(f"{r['views']:,} views")
            print(f"  {mark} [{r['id']}] {r['title'][:78]}")
            print(f"       {' | '.join(bits)}  {r['url']}")
    print(f"\n{shown} shows listed.  '>' = suggested best version, '*' = already downloaded.")
