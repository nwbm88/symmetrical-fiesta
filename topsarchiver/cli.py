"""Command line interface.

Typical session:

    topsarchiver search                  # build/refresh the catalog
    topsarchiver list --dupes            # inspect shows with multiple versions
    topsarchiver download yt:AbCdEf1234  # grab the version you picked
    topsarchiver split "collection/2016-06-14 - ..."   # audio -> named tracks
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import catalog as cat_mod
from . import sources
from .download import download
from . import audio as audio_mod
from .tracks import resolve_tracks

log = logging.getLogger("topsarchiver")


def cmd_search(args) -> int:
    cat = cat_mod.load(args.catalog)
    queries = args.query or sources.DEFAULT_QUERIES
    results = []
    for name, fn in sources.SEARCHERS.items():
        if args.source and name != args.source:
            continue
        results.extend(fn(queries, args.limit))
    added = cat_mod.merge_results(cat, results)
    cat_mod.save(cat, args.catalog)
    print(f"\ncatalog: {len(cat['recordings'])} recordings ({added} new) -> {args.catalog}")
    cat_mod.print_groups(cat, only_dupes=False, only_new=args.new)
    return 0


def cmd_list(args) -> int:
    cat = cat_mod.load(args.catalog)
    if not cat["recordings"]:
        print("catalog is empty — run 'topsarchiver search' first")
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
                  f"(see 'topsarchiver list')", file=sys.stderr)
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
    show_dir = args.show_dir
    manifest_path = show_dir / "show.json"
    if not manifest_path.exists():
        print(f"{manifest_path} not found — is this a show directory created by "
              f"'topsarchiver download'?", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    show = manifest["show"]

    master = audio_mod.extract_master(show_dir)
    total = audio_mod.duration_of(master)

    tracks_path = show_dir / "audio" / "tracks.json"
    if tracks_path.exists() and not args.redetect:
        tracks = json.loads(tracks_path.read_text())
        strategy = "tracks.json (manual/previous)"
    else:
        tracks, strategy = resolve_tracks(manifest, setlist_key=args.setlist_key)
        if tracks and tracks[0]["start"] is None:
            # names came from setlist.fm — find cut points by silence
            cuts = audio_mod.detect_silences(master, args.noise, args.min_silence)
            tracks = audio_mod.fit_names_to_silences(
                [t["title"] for t in tracks], cuts, total)
            strategy += " + silence detection"
        elif not tracks:
            cuts = audio_mod.detect_silences(master, args.noise, args.min_silence)
            bounds = [0.0] + cuts + [total]
            tracks = [{"title": f"Track {i:02d}", "start": bounds[i - 1], "end": bounds[i]}
                      for i in range(1, len(bounds))]
            strategy = "silence detection only (generic names — rename in tracks.json)"
        tracks_path.parent.mkdir(exist_ok=True)
        tracks_path.write_text(json.dumps(tracks, indent=2, ensure_ascii=False))

    print(f"\ntracklist via {strategy}:")
    for i, t in enumerate(tracks, 1):
        print(f"  {i:02d}. [{audio_mod._hms(t['start'] or 0)}] {t['title']}")
    print(f"\n(tracklist saved to {tracks_path} — edit it and re-run to fix "
          f"names or cut points)")

    if args.dry_run:
        return 0
    audio_mod.cut_tracks(master, tracks, show)
    print(f"\ndone: {len(tracks)} tagged FLAC tracks in {show_dir / 'audio'}")
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--catalog", type=Path, default=Path("catalog.json"),
                        help="catalog file (default: ./catalog.json)")
    common.add_argument("-v", "--verbose", action="store_true")

    ap = argparse.ArgumentParser(
        prog="topsarchiver",
        description="Archive Twenty One Pilots live recordings from YouTube "
                    "and the Internet Archive.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", parents=[common],
                       help="search sources and update the catalog")
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

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
