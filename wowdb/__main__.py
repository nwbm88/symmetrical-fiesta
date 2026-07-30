"""Command line entry point: ``python -m wowdb <command>``."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from . import __version__, query
from .wago import DEFAULT_LOCALE, LOCALES, WagoClient, WagoError

DEFAULT_DATA = Path("data")
DEFAULT_DB = Path("wow.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wowdb",
        description="Mirror World of Warcraft's client database from wago.tools "
                    "into an offline, readable SQLite database.",
    )
    parser.add_argument("--version", action="version", version=f"wowdb {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    scrape = commands.add_parser("scrape", help="download every DB2 table")
    scrape.add_argument("--out", type=Path, default=DEFAULT_DATA,
                        help=f"output directory (default: {DEFAULT_DATA})")
    scrape.add_argument("--product", default="wow",
                        help="wow (live retail), wowt (PTR), wow_beta, ... "
                             "(default: wow)")
    scrape.add_argument("--build", default="latest",
                        help="build version, or 'latest' (default: latest)")
    scrape.add_argument("--locale", default=DEFAULT_LOCALE, choices=LOCALES,
                        help=f"language of localised text (default: {DEFAULT_LOCALE})")
    scrape.add_argument("--tables", nargs="+",
                        help="only these tables (default: all of them)")
    scrape.add_argument("--workers", type=int, default=6,
                        help="parallel downloads (default: 6)")
    scrape.add_argument("--refresh", action="store_true",
                        help="re-download tables already on disk")

    build = commands.add_parser("build", help="turn a scrape into a SQLite database")
    build.add_argument("--data", type=Path, default=DEFAULT_DATA)
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--no-fts", action="store_true",
                       help="skip the full-text search index (smaller, faster)")
    build.add_argument("--no-views", action="store_true",
                       help="skip the readable views, keep raw tables only")

    update = commands.add_parser(
        "update", help="scrape and build in one go (use this for a fresh patch)")
    update.add_argument("--data", type=Path, default=DEFAULT_DATA)
    update.add_argument("--db", type=Path, default=DEFAULT_DB)
    update.add_argument("--product", default="wow")
    update.add_argument("--build", default="latest")
    update.add_argument("--locale", default=DEFAULT_LOCALE, choices=LOCALES)
    update.add_argument("--workers", type=int, default=6)

    search = commands.add_parser("search", help="full-text search the database")
    search.add_argument("term", nargs="+")
    search.add_argument("--db", type=Path, default=DEFAULT_DB)
    search.add_argument("--table", help="restrict to one DB2 table")
    search.add_argument("--limit", type=int, default=25)

    sql = commands.add_parser("sql", help="run a read-only SQL query")
    sql.add_argument("statement", nargs="+")
    sql.add_argument("--db", type=Path, default=DEFAULT_DB)
    sql.add_argument("--csv", action="store_true", help="output CSV instead of a table")

    serve = commands.add_parser("serve", help="browse the database in a web browser")
    serve.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    info = commands.add_parser("info", help="what is in the database")
    info.add_argument("--db", type=Path, default=DEFAULT_DB)

    builds = commands.add_parser("builds", help="list builds available on wago.tools")
    builds.add_argument("--product", default="wow")
    builds.add_argument("--limit", type=int, default=15)

    args = parser.parse_args(argv)

    try:
        return _dispatch(args)
    except (WagoError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _dispatch(args) -> int:
    if args.command == "scrape":
        from .scrape import scrape as run_scrape

        run_scrape(args.out, args.product, args.build, args.locale,
                   args.tables, args.workers, args.refresh)
        print(f"\nnext: python -m wowdb build --data {args.out}")
        return 0

    if args.command == "build":
        from .build import build as run_build

        run_build(args.data, args.db, not args.no_fts, not args.no_views)
        print(f"\nnext: python -m wowdb serve --db {args.db}")
        return 0

    if args.command == "update":
        from .build import build as run_build
        from .scrape import scrape as run_scrape

        run_scrape(args.data, args.product, args.build, args.locale,
                   None, args.workers, False)
        print()
        run_build(args.data, args.db, True, True)
        return 0

    if args.command == "search":
        return _search(args)

    if args.command == "sql":
        return _sql(args)

    if args.command == "serve":
        from .server import serve as run_serve

        run_serve(args.db, args.host, args.port)
        return 0

    if args.command == "info":
        return _info(args)

    if args.command == "builds":
        client = WagoClient()
        for build in client.builds().get(args.product, [])[: args.limit]:
            print(f"{build.version:<18} {build.created_at}")
        return 0

    return 1


def _search(args) -> int:
    connection = query.connect(args.db)
    term = " ".join(args.term)
    hits = query.search(connection, term, args.limit, args.table)
    if not hits:
        print(f"no matches for {term!r}")
        return 1
    for hit in hits:
        label = query.row_label(connection, hit["table_name"], hit["row_id"])
        name = label or f"#{hit['row_id']}"
        print(f"{hit['table_name']:<28} {hit['row_id']:>8}  {name}")
        print(f"{'':<28} {'':>8}  {hit['column_name']}: "
              f"{str(hit['text'])[:100]}")
    return 0


def _sql(args) -> int:
    statement = " ".join(args.statement)
    connection = query.connect(args.db)
    try:
        cursor = connection.execute(statement)
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows = cursor.fetchmany(query.MAX_ROWS)
    if not cursor.description:
        return 0
    headers = [column[0] for column in cursor.description]

    if args.csv:
        import csv

        writer = csv.writer(sys.stdout)
        writer.writerow(headers)
        writer.writerows(rows)
        return 0

    widths = [len(header) for header in headers]
    printable = [[("" if value is None else str(value))[:60] for value in row]
                 for row in rows]
    for row in printable:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    print("  ".join(header.ljust(width) for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in printable:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)))
    print(f"\n{len(rows)} row(s)" + (" (truncated)" if len(rows) == query.MAX_ROWS else ""))
    return 0


def _info(args) -> int:
    connection = query.connect(args.db)
    meta = query.info(connection)
    width = max(len(key) for key in meta)
    for key, value in meta.items():
        print(f"{key.replace('_', ' '):<{width}}  {value}")

    print("\nreadable views:")
    for view in query.curated_views(connection):
        count = connection.execute(
            f'SELECT count(*) FROM "{view["view_name"]}"'
        ).fetchone()[0]
        print(f"  {view['view_name']:<20} {count:>9,}  {view['description']}")

    print("\nlargest tables:")
    for row in connection.execute(
        "SELECT table_name, row_count FROM db2_table ORDER BY row_count DESC LIMIT 10"
    ):
        print(f"  {row['table_name']:<30} {row['row_count']:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
