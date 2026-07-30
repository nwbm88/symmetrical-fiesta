"""Read-side helpers shared by the CLI and the offline browser."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MAX_ROWS = 500


def connect(db_path: Path) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"{db_path} not found - run 'wowdb scrape' then 'wowdb build' first"
        )
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def info(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM wowdb_info")
    }


def tables(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT table_name, row_count, column_count, display_column "
        "FROM db2_table ORDER BY table_name"
    ).fetchall()


def curated_views(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT view_name, description FROM wowdb_view "
        "WHERE status = 'ok' ORDER BY view_name"
    ).fetchall()


def columns(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT column_name, data_type, fk_table, fk_column, has_enum, has_flags "
        "FROM db2_column WHERE table_name = ? ORDER BY ordinal",
        (table,),
    ).fetchall()


def relation_exists(connection: sqlite3.Connection, name: str) -> bool:
    """True if ``name`` is a real table or view (guards SQL interpolation)."""
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone() is not None


def search(connection: sqlite3.Connection, term: str,
           limit: int = 100, table: str | None = None) -> list[sqlite3.Row]:
    """Full-text search across every string in the database."""
    sql = (
        "SELECT table_name, row_id, column_name, text, rank FROM search "
        "WHERE search MATCH ?"
    )
    params: list = [_fts_query(term)]
    if table:
        sql += " AND table_name = ?"
        params.append(table)
    sql += " ORDER BY rank LIMIT ?"
    params.append(min(limit, MAX_ROWS))
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Malformed FTS expression - fall back to a literal phrase.
        params[0] = '"' + term.replace('"', "") + '"'
        return connection.execute(sql, params).fetchall()


def _fts_query(term: str) -> str:
    """Treat plain words as a prefix search; pass FTS operators through."""
    term = term.strip()
    if not term:
        return '""'
    if any(character in term for character in '"*:()'):
        return term
    words = term.split()
    return " ".join(f'"{word}"*' for word in words)


def row_label(connection: sqlite3.Connection, table: str, row_id) -> str | None:
    """The human-readable name of one row, if the table has one."""
    display = connection.execute(
        "SELECT display_column FROM db2_table WHERE table_name = ?", (table,)
    ).fetchone()
    if not display or not display[0] or not relation_exists(connection, table):
        return None
    try:
        found = connection.execute(
            f'SELECT "{display[0]}" FROM "{table}" WHERE ID = ?', (row_id,)
        ).fetchone()
    except sqlite3.Error:
        return None
    return found[0] if found else None


def select(connection: sqlite3.Connection, relation: str, limit: int = 50,
           offset: int = 0, where: str | None = None,
           params: tuple = ()) -> list[sqlite3.Row]:
    if not relation_exists(connection, relation):
        raise ValueError(f"no such table or view: {relation}")
    sql = f'SELECT * FROM "{relation}"'
    if where:
        sql += f" WHERE {where}"
    sql += " LIMIT ? OFFSET ?"
    return connection.execute(
        sql, (*params, min(limit, MAX_ROWS), offset)
    ).fetchall()
