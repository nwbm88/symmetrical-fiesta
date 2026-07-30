"""Turn a scraped directory of CSV + metadata into one SQLite database.

The result has four layers, from raw to readable:

1. ``<Table>``          one table per DB2 file, typed, indexed, verbatim.
2. ``db2_*``            the schema itself: columns, foreign keys, enum and
                        flag labels, so the data describes itself offline.
3. ``v_<Table>``        the same rows with enum codes decoded to names, flag
                        bitmasks spelled out, and foreign keys resolved to
                        the name of the thing they point at.
4. ``items``, ``spells``, ... hand-written views over the tables people
                        actually ask about (see views.py).

Plus ``search``, an FTS5 index over every piece of text in the database.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from .scrape import load_meta
from .wago import TableMeta

# CSV cells can hold long localised strings (quest text, spell descriptions).
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# How many rows to look at when guessing a column's type.  SQLite's type
# affinity keeps later outliers intact, so a sample is enough.
TYPE_SAMPLE_ROWS = 50_000

INT_RE = re.compile(r"^-?\d+$")
FLOAT_RE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")

# Columns that hold the human-readable name of a row, best first.
DISPLAY_COLUMNS = (
    "Name_lang", "Display_lang", "DisplayName_lang", "FullName_lang",
    "Title_lang", "Name", "NameText", "Label_lang", "Description_lang",
    "Text_lang", "Female_lang", "Male_lang", "AreaName_lang",
    "MapName_lang", "ShortName_lang", "Filename", "FileName", "TextureFile",
)

# Text columns that would flood the search index with machine identifiers.
SEARCH_COLUMN_BLOCKLIST = re.compile(
    r"(hash|guid|uuid|filedataid|filepath|texture|model|sound|^path)",
    re.IGNORECASE,
)


class BuildReport:
    """What made it into the database, and what did not."""

    def __init__(self) -> None:
        self.tables = 0
        self.rows = 0
        self.columns = 0
        self.views = 0
        self.curated_views: list[str] = []
        self.skipped_views: list[tuple[str, str]] = []
        self.empty_tables: list[str] = []
        self.search_rows = 0


def build(
    data_dir: Path,
    db_path: Path,
    with_fts: bool = True,
    with_views: bool = True,
    log=print,
) -> BuildReport:
    """Build ``db_path`` from a directory produced by :func:`wowdb.scrape.scrape`."""
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} not found - run 'wowdb scrape' into {data_dir} first"
        )
    manifest = json.loads(manifest_path.read_text())

    csv_dir = data_dir / "csv"
    meta_dir = data_dir / "meta"
    sources = sorted(csv_dir.glob("*.csv"))
    if not sources:
        raise FileNotFoundError(f"no CSV files in {csv_dir}")

    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path)
    _tune_for_bulk_load(connection)
    report = BuildReport()
    started = time.monotonic()

    log(f"source  : {data_dir} ({len(sources)} tables, build {manifest.get('build')})")
    log(f"target  : {db_path}")

    _create_meta_tables(connection)

    metas: dict[str, TableMeta] = {}
    schemas: dict[str, dict[str, str]] = {}

    for index, csv_path in enumerate(sources, 1):
        table = csv_path.stem
        meta_path = meta_dir / f"{table}.json"
        meta = load_meta(meta_path) if meta_path.exists() else None

        try:
            column_types, row_count = _import_csv(connection, table, csv_path)
        except (UnicodeDecodeError, csv.Error, sqlite3.Error) as exc:
            log(f"  ! {table}: {exc}")
            continue

        if not column_types:
            report.empty_tables.append(table)
            continue

        metas[table] = meta or TableMeta(table, list(column_types), {}, {}, {})
        schemas[table] = column_types
        report.tables += 1
        report.rows += row_count
        report.columns += len(column_types)

        if index % 100 == 0 or index == len(sources):
            log(
                f"  [{index:>4}/{len(sources)}] {report.rows:,} rows, "
                f"{time.monotonic() - started:.0f}s"
            )

    connection.commit()

    log("indexes : primary keys and foreign keys")
    _create_indexes(connection, schemas, metas)

    log("schema  : recording column, foreign key, enum and flag metadata")
    _populate_meta_tables(connection, manifest, schemas, metas)

    if with_views:
        log("views   : decoded per-table views")
        report.views = _create_decoded_views(connection, schemas, metas)

        from .views import create_curated_views

        log("views   : curated views")
        created, skipped = create_curated_views(connection, schemas)
        report.curated_views = created
        report.skipped_views = skipped
        log(f"          {len(created)} created, {len(skipped)} skipped")

    if with_fts:
        log("search  : building full-text index (this is the slow part)")
        report.search_rows = _build_search_index(connection, schemas, log=log)
        log(f"          {report.search_rows:,} searchable strings")

    _record_build_info(connection, manifest, report)

    log("optimise: analyze + vacuum")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("ANALYZE")
    connection.commit()
    connection.execute("VACUUM")
    connection.close()

    size_mb = db_path.stat().st_size / 1e6
    log(
        f"done    : {report.tables} tables, {report.rows:,} rows, "
        f"{size_mb:,.0f} MB in {time.monotonic() - started:.0f}s"
    )
    return report


# --------------------------------------------------------------------- import


def _tune_for_bulk_load(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA cache_size = -200000")  # ~200 MB
    connection.execute("PRAGMA temp_store = MEMORY")


def _import_csv(connection: sqlite3.Connection, table: str,
                csv_path: Path) -> tuple[dict[str, str], int]:
    """Create and fill one table. Returns ({column: type}, row count)."""
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return {}, 0
        header = _unique_columns(header)
        if not header:
            return {}, 0
        types = _infer_types(reader, len(header))

    column_types = dict(zip(header, types))

    # DB2 tables are keyed by an integer ID; making it the rowid alias gives
    # free clustered lookups, which every FK join and the browser rely on.
    definitions = []
    for name, sql_type in column_types.items():
        if name == "ID" and sql_type == "INTEGER":
            definitions.append(f'"{name}" INTEGER PRIMARY KEY')
        else:
            definitions.append(f'"{name}" {sql_type}')
    connection.execute(f'CREATE TABLE "{table}" ({", ".join(definitions)})')

    placeholders = ", ".join("?" * len(header))
    insert = f'INSERT OR REPLACE INTO "{table}" VALUES ({placeholders})'
    converters = [_converter(sql_type) for sql_type in types]
    width = len(header)

    def rows():
        with csv_path.open(newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for row in reader:
                if len(row) != width:
                    # Pad or trim: a malformed line should not lose the table.
                    row = (row + [""] * width)[:width]
                yield [convert(value) for convert, value in zip(converters, row)]

    cursor = connection.executemany(insert, rows())
    count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    del cursor
    return column_types, count


def _unique_columns(header: list[str]) -> list[str]:
    """Clean up header names and make them unique."""
    seen: dict[str, int] = {}
    result = []
    for index, raw in enumerate(header):
        name = (raw or "").strip().lstrip("﻿") or f"column_{index}"
        name = name.replace('"', "'")
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        result.append(name)
    return result


def _infer_types(reader, width: int) -> list[str]:
    """Guess INTEGER / REAL / TEXT per column from a sample of rows."""
    is_int = [True] * width
    is_float = [True] * width
    seen_value = [False] * width

    for count, row in enumerate(reader):
        if count >= TYPE_SAMPLE_ROWS:
            break
        for index, value in enumerate(row[:width]):
            if not value:
                continue
            seen_value[index] = True
            if is_int[index] and not (INT_RE.match(value) and -2**63 <= int(value) < 2**63):
                is_int[index] = False
            if is_float[index] and not FLOAT_RE.match(value):
                is_float[index] = False

    types = []
    for index in range(width):
        if not seen_value[index]:
            types.append("TEXT")
        elif is_int[index]:
            types.append("INTEGER")
        elif is_float[index]:
            types.append("REAL")
        else:
            types.append("TEXT")
    return types


def _converter(sql_type: str):
    """Empty cells become NULL; numbers are stored as numbers."""
    if sql_type == "INTEGER":
        def to_int(value: str):
            if not value:
                return None
            try:
                return int(value)
            except ValueError:
                return value  # keep the original rather than lose it
        return to_int
    if sql_type == "REAL":
        def to_float(value: str):
            if not value:
                return None
            try:
                return float(value)
            except ValueError:
                return value
        return to_float
    return lambda value: value or None


def _create_indexes(connection: sqlite3.Connection,
                    schemas: dict[str, dict[str, str]],
                    metas: dict[str, TableMeta]) -> None:
    for table, columns in schemas.items():
        meta = metas.get(table)
        if not meta:
            continue
        for column in meta.foreign_keys:
            if column not in columns or column == "ID":
                continue
            name = f"ix_{table}_{column}".replace('"', "")
            try:
                connection.execute(
                    f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ("{column}")'
                )
            except sqlite3.Error:
                pass
    connection.commit()


# ----------------------------------------------------------------- meta layer


def _create_meta_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE wowdb_info (key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE db2_table (
            table_name    TEXT PRIMARY KEY,
            row_count     INTEGER,
            column_count  INTEGER,
            display_column TEXT
        );

        CREATE TABLE db2_column (
            table_name  TEXT,
            column_name TEXT,
            ordinal     INTEGER,
            data_type   TEXT,
            fk_table    TEXT,
            fk_column   TEXT,
            has_enum    INTEGER DEFAULT 0,
            has_flags   INTEGER DEFAULT 0,
            PRIMARY KEY (table_name, column_name)
        );

        CREATE TABLE db2_enum (
            table_name  TEXT,
            column_name TEXT,
            value       INTEGER,
            label       TEXT
        );

        CREATE TABLE db2_flag (
            table_name  TEXT,
            column_name TEXT,
            value       INTEGER,
            label       TEXT
        );
        """
    )


def _populate_meta_tables(connection: sqlite3.Connection,
                          manifest: dict,
                          schemas: dict[str, dict[str, str]],
                          metas: dict[str, TableMeta]) -> None:
    for table, columns in schemas.items():
        meta = metas[table]
        row_count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        connection.execute(
            "INSERT INTO db2_table VALUES (?, ?, ?, ?)",
            (table, row_count, len(columns), display_column(columns)),
        )
        for ordinal, (column, sql_type) in enumerate(columns.items()):
            fk = meta.foreign_keys.get(column)
            connection.execute(
                "INSERT INTO db2_column VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    table, column, ordinal, sql_type,
                    fk[0] if fk else None,
                    fk[1] if fk else None,
                    1 if column in meta.enums else 0,
                    1 if column in meta.flags else 0,
                ),
            )
        for column, labels in meta.enums.items():
            if column in columns:
                connection.executemany(
                    "INSERT INTO db2_enum VALUES (?, ?, ?, ?)",
                    [(table, column, value, label) for value, label in labels.items()],
                )
        for column, labels in meta.flags.items():
            if column in columns:
                connection.executemany(
                    "INSERT INTO db2_flag VALUES (?, ?, ?, ?)",
                    [(table, column, value, label) for value, label in labels.items()],
                )

    connection.executescript(
        """
        CREATE INDEX ix_db2_column_fk ON db2_column (fk_table);
        CREATE INDEX ix_db2_enum ON db2_enum (table_name, column_name, value);
        CREATE INDEX ix_db2_flag ON db2_flag (table_name, column_name);
        """
    )
    connection.commit()


def display_column(columns) -> str | None:
    """Pick the column that best names a row of this table."""
    names = set(columns)
    for candidate in DISPLAY_COLUMNS:
        if candidate in names:
            return candidate
    for name in columns:
        if name.endswith("_lang"):
            return name
    return None


def _record_build_info(connection: sqlite3.Connection, manifest: dict,
                       report: BuildReport) -> None:
    info = {
        "wow_build": manifest.get("build", ""),
        "wow_product": manifest.get("product", ""),
        "build_released": manifest.get("build_created_at", ""),
        "locale": manifest.get("locale", ""),
        "source": "https://wago.tools",
        "scraped_at": manifest.get("finished_at", ""),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "table_count": str(report.tables),
        "row_count": str(report.rows),
        "column_count": str(report.columns),
        "searchable_strings": str(report.search_rows),
    }
    connection.executemany(
        "INSERT OR REPLACE INTO wowdb_info VALUES (?, ?)", list(info.items())
    )
    connection.commit()


# ---------------------------------------------------------------- decoded views


def _create_decoded_views(connection: sqlite3.Connection,
                          schemas: dict[str, dict[str, str]],
                          metas: dict[str, TableMeta]) -> int:
    """One ``v_<Table>`` view per table with codes turned into words."""
    display_columns = {
        table: display_column(columns) for table, columns in schemas.items()
    }
    created = 0

    for table, columns in schemas.items():
        meta = metas[table]
        selects = [f't."{column}"' for column in columns]
        existing = set(columns)

        for column in columns:
            labels = meta.enums.get(column)
            if labels:
                alias = _free_alias(f"{column}_label", existing)
                selects.append(f"{_case_expression(column, labels)} AS \"{alias}\"")
                continue

            if column in meta.flags:
                alias = _free_alias(f"{column}_flags", existing)
                selects.append(
                    "(SELECT group_concat(f.label, ', ') FROM db2_flag f "
                    f"WHERE f.table_name = '{table}' AND f.column_name = '{column}' "
                    f'AND f.value != 0 AND (t."{column}" & f.value) = f.value) '
                    f'AS "{alias}"'
                )
                continue

            target = meta.foreign_keys.get(column)
            if not target:
                continue
            target_table, target_column = target
            target_display = display_columns.get(target_table)
            if (
                target_table not in schemas
                or not target_display
                or target_column not in schemas[target_table]
                or target_table == table
            ):
                continue
            alias = _free_alias(f"{column}_name", existing)
            selects.append(
                f'(SELECT r."{target_display}" FROM "{target_table}" r '
                f'WHERE r."{target_column}" = t."{column}") AS "{alias}"'
            )

        try:
            connection.execute(
                f'CREATE VIEW "v_{table}" AS SELECT {", ".join(selects)} '
                f'FROM "{table}" t'
            )
            created += 1
        except sqlite3.Error:
            # A view is a convenience; never let one break the build.
            continue

    connection.commit()
    return created


def _case_expression(column: str, labels: dict[int, str]) -> str:
    whens = " ".join(
        f"WHEN {value} THEN '{label.replace(chr(39), chr(39) * 2)}'"
        for value, label in sorted(labels.items())
    )
    return f'CASE t."{column}" {whens} END'


def _free_alias(alias: str, existing: set[str]) -> str:
    candidate = alias
    suffix = 2
    while candidate in existing:
        candidate = f"{alias}_{suffix}"
        suffix += 1
    existing.add(candidate)
    return candidate


# ----------------------------------------------------------------------- search


def _build_search_index(connection: sqlite3.Connection,
                        schemas: dict[str, dict[str, str]],
                        log=print) -> int:
    """FTS5 index over every human-meaningful string in the database."""
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE search USING fts5("
            "text, table_name UNINDEXED, row_id UNINDEXED, "
            "column_name UNINDEXED, tokenize='unicode61 remove_diacritics 2')"
        )
    except sqlite3.OperationalError:
        log("          FTS5 unavailable in this SQLite build - skipping search index")
        return 0

    total = 0
    for table, columns in schemas.items():
        text_columns = [
            column
            for column, sql_type in columns.items()
            if sql_type == "TEXT" and not SEARCH_COLUMN_BLOCKLIST.search(column)
        ]
        if not text_columns:
            continue
        key = "ID" if "ID" in columns else None

        for column in text_columns:
            row_expression = f't."{key}"' if key else "t.rowid"
            try:
                cursor = connection.execute(
                    "INSERT INTO search (text, table_name, row_id, column_name) "
                    f'SELECT t."{column}", ?, {row_expression}, ? FROM "{table}" t '
                    f'WHERE t."{column}" IS NOT NULL AND t."{column}" != \'\' '
                    f'AND length(t."{column}") <= 4000',
                    (table, column),
                )
                total += cursor.rowcount if cursor.rowcount > 0 else 0
            except sqlite3.Error:
                continue
        connection.commit()

    connection.execute("INSERT INTO search(search) VALUES ('optimize')")
    connection.commit()
    return total
