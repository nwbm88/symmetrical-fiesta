"""Command line interface.

Typical session:

    livearchiver search                  # build/refresh the catalog
    livearchiver list --dupes            # inspect shows with multiple versions
    livearchiver download yt:AbCdEf1234  # grab the version you picked
    livearchiver split "collection/2016-06-14 - ..."   # audio -> named tracks
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import catalog as cat_mod
from . import sources
from .download import download
from . import audio as audio_mod
from .pipeline import split_show

log = logging.getLogger("livearchiver")


def cmd_search(args) -> int:
    cat = cat_mod.load(args.catalog)
    queries = args.query or sources.default_queries(args.artist)
    results = []
    for name, fn in sources.SEARCHERS.items():
        if args.source and name != args.source:
            continue
        results.extend(fn(args.artist, queries, args.limit))
    added = cat_mod.merge_results(cat, results)
    cat_mod.save(cat, args.catalog)
    print(f"\ncatalog: {len(cat['recordings'])} recordings ({added} new) -> {args.catalog}")
    cat_mod.print_groups(cat, only_dupes=False, only_new=args.new)
    return 0


def cmd_list(args) -> int:
    cat = cat_mod.load(args.catalog)
    if not cat["recordings"]:
        print("catalog is empty — run 'livearchiver search' first")
        return 1
    cat_mod.print_groups(cat, only_dupes=args.dupes, only_new=args.new)
    return 0


def cmd_download(args) -> int:
    cat = cat_mod.load(args.catalog)
    rc = 0
    for rid in args.ids:
        rec = cat["recordings"].get(rid)
        if not rec:
            print(f"unknown id {rid!r} — ids look like yt:VIDEOID or ia:IDENTIFIER "
                  f"(see 'livearchiver list')", file=sys.stderr)
            rc = 1
            continue
        try:
            dest = download(rec, args.dir, audio_only=args.audio_only)
        except Exception as e:
            log.error("download of %s failed: %s", rid, e)
            rc = 1
            continue
        cat.setdefault("downloaded", {})[rid] = str(dest)
        cat_mod.save(cat, args.catalog)
        print(f"downloaded {rid} -> {dest}")
    return rc


def cmd_split(args) -> int:
    try:
        res = split_show(args.show_dir, setlist_key=args.setlist_key,
                         noise=args.noise, min_silence=args.min_silence,
                         redetect=args.redetect, cut=not args.dry_run)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    print(f"\ntracklist via {res['strategy']}:")
    for i, t in enumerate(res["tracks"], 1):
        print(f"  {i:02d}. [{audio_mod._hms(t['start'] or 0)}] {t['title']}")
    print(f"\n(tracklist saved to {args.show_dir / 'audio' / 'tracks.json'} — "
          f"edit it and re-run to fix names or cut points)")
    if not args.dry_run:
        print(f"\ndone: {len(res['files'])} tagged FLAC tracks in "
              f"{args.show_dir / 'audio'}")
    return 0


def cmd_todo(args) -> int:
    from .pipeline import scan_collection
    shows = scan_collection(args.dir)
    if not shows:
        print(f"no shows found under {args.dir}")
        return 0
    flagged = 0
    for s in shows:
        state = (f"{len(s['tracks'])} tracks split" if s["tracks"] else
                 "audio extracted, NOT split" if s["has_master"] else
                 "downloaded, audio NOT extracted")
        if s["issues"]:
            flagged += 1
            print(f"⚠ {s['dir']}")
            for i in s["issues"]:
                print(f"    - {i}")
            print(f"    ({state})")
        elif not s["tracks"]:
            flagged += 1
            print(f"… {s['dir']}\n    - {state}")
    if not flagged:
        print(f"all {len(shows)} shows are complete — dated, placed and split.")
    return 0


def cmd_gui(args) -> int:
    try:
        from .gui import run
    except ImportError:
        print("The desktop app needs PySide6 — install it with:\n"
              "    pip install PySide6", file=sys.stderr)
        return 1
    return run()


def main(argv=None) -> int:
    from . import DEFAULT_ARTIST

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", type=Path, default=Path("catalog.json"),
                        help="catalog file (default: ./catalog.json)")
    common.add_argument("-v", "--verbose", action="store_true")

    ap = argparse.ArgumentParser(
        prog="livearchiver",
        description="Archive a band's live recordings from YouTube "
                    "and the Internet Archive.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", parents=[common],
                       help="search sources and update the catalog")
    p.add_argument("--artist", default=DEFAULT_ARTIST,
                   help=f"band/artist to search for (default: {DEFAULT_ARTIST!r})")
    p.add_argument("--query", action="append",
                   help="extra search query (repeatable); defaults to a "
                        "built-in set of full-concert queries")
    p.add_argument("--limit", type=int, default=50, help="results per query")
    p.add_argument("--source", choices=list(sources.SEARCHERS),
                   help="search only this source")
    p.add_argument("--new", action="store_true",
                   help="only list shows not downloaded yet")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("list", parents=[common], help="show the catalog grouped by show")
    p.add_argument("--dupes", action="store_true",
                   help="only shows with multiple versions")
    p.add_argument("--new", action="store_true",
                   help="only shows not downloaded yet")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("download", parents=[common], help="download recordings by id")
    p.add_argument("ids", nargs="+", metavar="ID",
                   help="recording ids from 'list' (yt:... or ia:...)")
    p.add_argument("--dir", type=Path, default=Path("collection"),
                   help="collection root (default: ./collection)")
    p.add_argument("--audio-only", action="store_true",
                   help="skip the video stream, download audio only")
    p.set_defaults(fn=cmd_download)

    p = sub.add_parser("split", parents=[common], help="extract audio and cut into named tracks")
    p.add_argument("show_dir", type=Path, help="a show directory from 'download'")
    p.add_argument("--setlist-key",
                   help="setlist.fm API key, used to name tracks when the video "
                        "has no chapters/timestamps")
    p.add_argument("--noise", type=int, default=-35,
                   help="silence threshold in dB (default -35)")
    p.add_argument("--min-silence", type=float, default=1.5,
                   help="minimum silence length in seconds (default 1.5)")
    p.add_argument("--dry-run", action="store_true",
                   help="write/print the tracklist but don't cut audio")
    p.add_argument("--redetect", action="store_true",
                   help="ignore an existing tracks.json and re-detect")
    p.set_defaults(fn=cmd_split)

    p = sub.add_parser("todo", parents=[common],
                       help="list shows that still need manual attention "
                            "(missing date/place, unidentified tracks, not split)")
    p.add_argument("--dir", type=Path, default=Path("collection"),
                   help="collection root (default: ./collection)")
    p.set_defaults(fn=cmd_todo)

    p = sub.add_parser("gui", parents=[common],
                       help="launch the desktop app (needs PySide6)")
    p.set_defaults(fn=cmd_gui)

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
