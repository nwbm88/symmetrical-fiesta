"""A local, offline web browser for the database.

Serves from the standard library only, over a read-only SQLite connection, on
localhost.  Foreign keys become links, so you can start at an item and click
through to the spell it casts, the quest it starts, or the set it belongs to.
"""

from __future__ import annotations

import html
import json
import sqlite3
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import query

PAGE_SIZE = 50

# Item quality colours, as used in game.
QUALITY_COLOURS = {
    "Poor": "#9d9d9d", "Common": "#ffffff", "Uncommon": "#1eff00",
    "Rare": "#0070dd", "Epic": "#a335ee", "Legendary": "#ff8000",
    "Artifact": "#e6cc80", "Heirloom": "#00ccff", "WoW Token": "#00ccff",
}

STYLE = """
:root {
  --bg: #14161a; --panel: #1b1e24; --line: #2c313a; --text: #dfe3ea;
  --muted: #8b94a3; --link: #6fb2ff; --accent: #e6cc80;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif; }
a { color: var(--link); text-decoration: none; }
a:hover { text-decoration: underline; }
header { position: sticky; top: 0; z-index: 5; background: var(--panel);
  border-bottom: 1px solid var(--line); padding: 10px 18px;
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
header .brand { font-weight: 700; color: var(--accent); letter-spacing: .5px; }
header .build { color: var(--muted); font-size: 12px; }
form.search { flex: 1; min-width: 260px; display: flex; gap: 8px; }
input[type=search], input[type=text], textarea {
  flex: 1; background: #0f1115; border: 1px solid var(--line); color: var(--text);
  border-radius: 6px; padding: 7px 10px; font: inherit; }
button { background: #2a3038; border: 1px solid var(--line); color: var(--text);
  border-radius: 6px; padding: 7px 14px; cursor: pointer; font: inherit; }
button:hover { background: #343c47; }
main { padding: 18px; max-width: 1500px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 26px 0 10px; color: var(--accent);
  text-transform: uppercase; letter-spacing: .6px; }
p.muted, .muted { color: var(--muted); }
.grid { display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); }
.card { background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 11px 13px; }
.card .name { font-weight: 600; }
.card .desc { color: var(--muted); font-size: 12px; margin-top: 3px; }
.tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--line);
  white-space: nowrap; max-width: 460px; overflow: hidden; text-overflow: ellipsis; }
th { background: #22262e; position: sticky; top: 0; font-weight: 600; }
tr:hover td { background: #1e222a; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; background: #262b33; border-radius: 4px;
  padding: 1px 6px; font-size: 11px; color: var(--muted); margin-left: 6px; }
.pager { display: flex; gap: 10px; align-items: center; margin: 12px 0; }
dl.row { display: grid; grid-template-columns: 260px 1fr; gap: 2px 14px; }
dl.row dt { color: var(--muted); padding: 4px 0; border-bottom: 1px solid #20242b; }
dl.row dd { margin: 0; padding: 4px 0; border-bottom: 1px solid #20242b;
  word-break: break-word; }
.label { color: var(--accent); }
.hit { padding: 7px 0; border-bottom: 1px solid var(--line); }
.hit .where { color: var(--muted); font-size: 12px; }
"""

HOME_LINKS = (
    ("items", "Items"), ("spells", "Spells"), ("creatures", "Creatures"),
    ("achievements", "Achievements"), ("dungeons", "Dungeons & raids"),
    ("zones", "Zones"), ("mounts", "Mounts"), ("battle_pets", "Battle pets"),
)


class Handler(BaseHTTPRequestHandler):
    server_version = "wowdb"
    db_path: Path = Path("wow.db")

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # ------------------------------------------------------------------ routing

    def do_GET(self):  # noqa: N802 - required name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        connection = query.connect(self.db_path)
        try:
            if route == "/":
                body = self.page_home(connection)
            elif route == "/search":
                body = self.page_search(connection, _one(params, "q"),
                                        _one(params, "table"))
            elif route.startswith("/t/"):
                body = self.page_browse(
                    connection,
                    urllib.parse.unquote(route[3:]),
                    int(_one(params, "page") or 1),
                    _one(params, "q"),
                )
            elif route.startswith("/row/"):
                table, _, row_id = route[5:].partition("/")
                body = self.page_row(connection, urllib.parse.unquote(table), row_id)
            elif route == "/api/search":
                return self.send_json(
                    [dict(row) for row in
                     query.search(connection, _one(params, "q") or "", 100)]
                )
            else:
                return self.send_html("<main><h1>Not found</h1></main>", status=404)
        except (ValueError, sqlite3.Error) as exc:
            body = f"<main><h1>Error</h1><p class=muted>{html.escape(str(exc))}</p></main>"
        finally:
            connection.close()

        self.send_html(body)

    # -------------------------------------------------------------------- pages

    def page_home(self, connection) -> str:
        meta = query.info(connection)
        views = query.curated_views(connection)
        tables = query.tables(connection)

        cards = "".join(
            f'<a class="card" href="/t/{urllib.parse.quote(row["view_name"])}">'
            f'<div class="name">{html.escape(row["view_name"])}</div>'
            f'<div class="desc">{html.escape(row["description"] or "")}</div></a>'
            for row in views
        )
        rows = "".join(
            f'<tr><td><a href="/t/{urllib.parse.quote(row["table_name"])}">'
            f'{html.escape(row["table_name"])}</a></td>'
            f'<td class=num>{row["row_count"]:,}</td>'
            f'<td class=num>{row["column_count"]}</td></tr>'
            for row in tables
        )
        return f"""
        <main>
          <h1>World of Warcraft client database</h1>
          <p class=muted>Build {html.escape(meta.get('wow_build', '?'))}
            ({html.escape(meta.get('wow_product', '?'))}),
            locale {html.escape(meta.get('locale', '?'))} &middot;
            {int(meta.get('table_count', 0)):,} tables &middot;
            {int(meta.get('row_count', 0)):,} rows &middot;
            mirrored from wago.tools on {html.escape(meta.get('scraped_at', '?'))}</p>
          <h2>Readable views</h2>
          <div class=grid>{cards}</div>
          <h2>All {len(tables):,} raw DB2 tables</h2>
          <div class=tablewrap><table>
            <tr><th>Table</th><th>Rows</th><th>Columns</th></tr>{rows}
          </table></div>
        </main>"""

    def page_search(self, connection, term: str | None, table: str | None) -> str:
        if not term:
            return "<main><h1>Search</h1><p class=muted>Type something above.</p></main>"

        hits = query.search(connection, term, limit=200, table=table)
        if not hits:
            return (f"<main><h1>No matches for "
                    f"{html.escape(term)}</h1></main>")

        blocks = []
        for hit in hits:
            label = query.row_label(connection, hit["table_name"], hit["row_id"])
            name = html.escape(str(label)) if label else f'#{hit["row_id"]}'
            blocks.append(
                f'<div class=hit>'
                f'<a href="/row/{urllib.parse.quote(hit["table_name"])}/{hit["row_id"]}">'
                f'{name}</a>'
                f'<span class=pill>{html.escape(hit["table_name"])}</span>'
                f'<div class=where>{html.escape(hit["column_name"])}: '
                f'{html.escape(str(hit["text"])[:220])}</div></div>'
            )
        return (f"<main><h1>{len(hits)} matches for "
                f"{html.escape(term)}</h1>{''.join(blocks)}</main>")

    def page_browse(self, connection, relation: str, page: int,
                    term: str | None) -> str:
        if not query.relation_exists(connection, relation):
            return f"<main><h1>No such table: {html.escape(relation)}</h1></main>"

        page = max(page, 1)
        offset = (page - 1) * PAGE_SIZE

        where, params = None, ()
        if term:
            text_columns = [
                row[1] for row in
                connection.execute(f'PRAGMA table_info("{relation}")')
                if _is_text(row[2])
            ]
            if text_columns:
                where = " OR ".join(f'"{c}" LIKE ?' for c in text_columns)
                params = tuple(f"%{term}%" for _ in text_columns)

        rows = query.select(connection, relation, PAGE_SIZE, offset, where, params)
        if not rows:
            return (f"<main><h1>{html.escape(relation)}</h1>"
                    f"<p class=muted>No rows on this page.</p>"
                    f'<p><a href="/t/{urllib.parse.quote(relation)}">Back to start</a></p></main>')

        links = self._fk_links(connection, relation)
        headers = rows[0].keys()
        head = "".join(f"<th>{html.escape(name)}</th>" for name in headers)

        body_rows = []
        for row in rows:
            cells = []
            for column in headers:
                cells.append(self._cell(connection, relation, column, row[column], links))
            body_rows.append(f"<tr>{''.join(cells)}</tr>")

        previous = (f'<a href="/t/{urllib.parse.quote(relation)}?page={page - 1}'
                    f'{_q(term)}">&larr; previous</a>' if page > 1 else "")
        following = (f'<a href="/t/{urllib.parse.quote(relation)}?page={page + 1}'
                     f'{_q(term)}">next &rarr;</a>'
                     if len(rows) == PAGE_SIZE else "")

        return f"""
        <main>
          <h1>{html.escape(relation)}</h1>
          <form class=search action="/t/{urllib.parse.quote(relation)}">
            <input type=search name=q placeholder="filter this table"
                   value="{html.escape(term or '')}">
            <button>Filter</button>
          </form>
          <div class=pager>{previous}<span class=muted>page {page}</span>{following}</div>
          <div class=tablewrap><table><tr>{head}</tr>{''.join(body_rows)}</table></div>
        </main>"""

    def page_row(self, connection, table: str, row_id: str) -> str:
        if not query.relation_exists(connection, table):
            return f"<main><h1>No such table: {html.escape(table)}</h1></main>"
        row = connection.execute(
            f'SELECT * FROM "{table}" WHERE ID = ?', (row_id,)
        ).fetchone()
        if row is None:
            return (f"<main><h1>{html.escape(table)} #{html.escape(row_id)}</h1>"
                    f"<p class=muted>No such row.</p></main>")

        meta = {
            column["column_name"]: column
            for column in query.columns(connection, table)
        }
        items = []
        for column in row.keys():
            value = row[column]
            if value is None or value == "":
                continue
            rendered = self._value(connection, table, column, value, meta.get(column))
            items.append(f"<dt>{html.escape(column)}</dt><dd>{rendered}</dd>")

        label = query.row_label(connection, table, row_id) or f"#{row_id}"
        referenced = self._referenced_by(connection, table, row_id)

        return f"""
        <main>
          <h1>{html.escape(str(label))}</h1>
          <p class=muted>{html.escape(table)} &middot; ID {html.escape(str(row_id))}</p>
          <dl class=row>{''.join(items)}</dl>
          {referenced}
        </main>"""

    # ------------------------------------------------------------------ helpers

    def _fk_links(self, connection, relation: str) -> dict[str, str]:
        return {
            row["column_name"]: row["fk_table"]
            for row in connection.execute(
                "SELECT column_name, fk_table FROM db2_column "
                "WHERE table_name = ? AND fk_table IS NOT NULL",
                (relation,),
            )
        }

    def _cell(self, connection, relation, column, value, links) -> str:
        if value is None:
            return '<td class=muted>&ndash;</td>'
        if column in ("ID", "id") or column.endswith("_id"):
            target = links.get(column) or (relation if column == "ID" else None)
            if target and query.relation_exists(connection, target):
                return (f'<td class=num><a href="/row/{urllib.parse.quote(target)}'
                        f'/{value}">{value}</a></td>')
        if isinstance(value, (int, float)):
            return f'<td class=num>{value}</td>'

        text = str(value)
        colour = QUALITY_COLOURS.get(text)
        style = f' style="color:{colour}"' if colour else ""
        return f'<td{style}>{html.escape(text[:300])}</td>'

    def _value(self, connection, table, column, value, meta) -> str:
        parts = [html.escape(str(value)[:2000])]

        if meta and meta["fk_table"] and query.relation_exists(connection, meta["fk_table"]):
            target = meta["fk_table"]
            label = query.row_label(connection, target, value)
            shown = html.escape(str(label)) if label else f"{target} #{value}"
            parts.append(
                f'<a href="/row/{urllib.parse.quote(target)}/{value}">&rarr; {shown}</a>'
            )
        if meta and meta["has_enum"]:
            found = connection.execute(
                "SELECT label FROM db2_enum WHERE table_name = ? AND column_name = ? "
                "AND value = ?", (table, column, value),
            ).fetchone()
            if found:
                parts.append(f'<span class=label>{html.escape(found[0])}</span>')
        if meta and meta["has_flags"] and isinstance(value, int):
            found = connection.execute(
                "SELECT group_concat(label, ', ') FROM db2_flag "
                "WHERE table_name = ? AND column_name = ? AND value != 0 "
                "AND (? & value) = value", (table, column, value),
            ).fetchone()
            if found and found[0]:
                parts.append(f'<span class=label>{html.escape(found[0])}</span>')

        return " &nbsp; ".join(parts)

    def _referenced_by(self, connection, table: str, row_id: str) -> str:
        sources = connection.execute(
            "SELECT table_name, column_name FROM db2_column WHERE fk_table = ? "
            "ORDER BY table_name", (table,),
        ).fetchall()
        if not sources:
            return ""

        links = []
        for source in sources:
            if not query.relation_exists(connection, source["table_name"]):
                continue
            try:
                count = connection.execute(
                    f'SELECT count(*) FROM "{source["table_name"]}" '
                    f'WHERE "{source["column_name"]}" = ?', (row_id,)
                ).fetchone()[0]
            except sqlite3.Error:
                continue
            if not count:
                continue
            links.append(
                f'<a class=card href="/t/{urllib.parse.quote(source["table_name"])}">'
                f'<div class=name>{html.escape(source["table_name"])}'
                f'<span class=pill>{count:,}</span></div>'
                f'<div class=desc>via {html.escape(source["column_name"])}</div></a>'
            )
        if not links:
            return ""
        return f"<h2>Referenced by</h2><div class=grid>{''.join(links)}</div>"

    # ------------------------------------------------------------------ output

    def send_html(self, body: str, status: int = 200) -> None:
        page = f"""<!doctype html>
<html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>wowdb</title><style>{STYLE}</style></head><body>
<header>
  <a class=brand href="/">WOWDB</a>
  <form class=search action="/search">
    <input type=search name=q placeholder="Search everything - items, spells, zones, NPCs...">
    <button>Search</button>
  </form>
  <span class=build>{' &middot; '.join(
      f'<a href="/t/{name}">{title}</a>' for name, title in HOME_LINKS)}</span>
</header>
{body}
</body></html>"""
        encoded = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_json(self, payload) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _one(params: dict, key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _q(term: str | None) -> str:
    return f"&q={urllib.parse.quote(term)}" if term else ""


def _is_text(declared_type: str) -> bool:
    return "CHAR" in (declared_type or "").upper() or (declared_type or "").upper() == "TEXT"


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8000,
          log=print) -> None:
    # Fail fast with a clear message if the database is not there yet.
    query.connect(db_path).close()

    handler = type("BoundHandler", (Handler,), {"db_path": Path(db_path)})
    server = ThreadingHTTPServer((host, port), handler)
    log(f"browsing {db_path} at http://{host}:{port}/  (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\nstopped")
    finally:
        server.server_close()
