"""HTTP client for wago.tools.

wago.tools is a Laravel + Inertia.js app.  It exposes three things we care
about, none of which need an account:

  GET /api/builds                 -> every known build, grouped by product
  GET /db2                        -> HTML page whose ``data-page`` attribute
                                     carries the full DB2 table list
  GET /db2/<Table>?build=...      -> Inertia page.  With a partial-reload
                                     header we get just the schema metadata:
                                     column headers, foreign keys, enum and
                                     flag definitions.
  GET /db2/<Table>/csv?build=...  -> the table itself, as CSV

Only the standard library is used so the resulting database stays usable on a
machine with no package manager and no network.
"""

from __future__ import annotations

import gzip
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass

BASE_URL = "https://wago.tools"

# wago.tools is a small community site.  Identify ourselves and stay polite.
USER_AGENT = "wowdb/1.0 (offline DB2 mirror; +https://github.com/wowdev)"

# Products that map to "retail".  The live retail client is ``wow``; the
# others are its test realms and are only used when explicitly asked for.
RETAIL_PRODUCTS = ("wow", "wowt", "wowxptr", "wow_beta")

DEFAULT_LOCALE = "enUS"

LOCALES = (
    "enUS", "koKR", "frFR", "deDE", "zhCN", "esES",
    "zhTW", "esMX", "ruRU", "ptBR", "itIT",
)


class WagoError(RuntimeError):
    """Raised when wago.tools cannot serve a request."""


@dataclass(frozen=True)
class Build:
    """One client build as reported by /api/builds."""

    product: str
    version: str
    created_at: str

    @property
    def expansion(self) -> int:
        try:
            return int(self.version.split(".", 1)[0])
        except ValueError:
            return 0

    def __str__(self) -> str:
        return f"{self.version} ({self.product}, {self.created_at})"


@dataclass(frozen=True)
class TableMeta:
    """Schema metadata for one DB2 table, as understood by WoWDBDefs."""

    name: str
    columns: list[str]
    # column -> (target table, target column)
    foreign_keys: dict[str, tuple[str, str]]
    # column -> {int value: label}
    enums: dict[str, dict[int, str]]
    # column -> {bit value: label}
    flags: dict[str, dict[int, str]]


class WagoClient:
    """Minimal, retrying HTTP client for the endpoints listed above."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: int = 300,
        retries: int = 5,
        min_interval: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._inertia_version: str | None = None
        self._last_request = 0.0

    # ------------------------------------------------------------------ http

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict | None = None,
             headers: dict | None = None) -> bytes:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request_headers = {
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        }
        if headers:
            request_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(self.retries):
            if attempt:
                # 2s, 4s, 8s, 16s - wago rate-limits aggressive clients.
                time.sleep(min(2 ** attempt, 30))
            self._throttle()
            try:
                request = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return _decode_body(response.read(), response.headers.get("Content-Encoding"))
            except urllib.error.HTTPError as exc:
                # 4xx other than 429 will not get better by retrying.
                if exc.code != 429 and 400 <= exc.code < 500:
                    raise WagoError(f"GET {url} -> HTTP {exc.code}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_error = exc

        raise WagoError(f"GET {url} failed after {self.retries} attempts: {last_error}")

    def _get_inertia(self, path: str, params: dict, component: str,
                     props: list[str]) -> dict:
        """Fetch a page as JSON, asking only for the props we need."""
        headers = {
            "X-Inertia": "true",
            "X-Inertia-Version": self.inertia_version(),
            "X-Inertia-Partial-Component": component,
            "X-Inertia-Partial-Data": ",".join(props),
            "Accept": "text/html, application/xhtml+xml",
        }
        body = self._get(path, params, headers)
        try:
            return json.loads(body)["props"]
        except (ValueError, KeyError) as exc:
            raise WagoError(f"unexpected Inertia response for {path}") from exc

    # -------------------------------------------------------------- discovery

    def inertia_version(self) -> str:
        """Asset hash Inertia uses for cache busting; required on every call."""
        if self._inertia_version is None:
            self._load_db2_index()
        assert self._inertia_version is not None
        return self._inertia_version

    def _load_db2_index(self) -> dict:
        """Parse the ``data-page`` blob embedded in the /db2 HTML page."""
        page = self._get("/db2").decode("utf-8", "replace")
        match = re.search(r'data-page="(.*?)"\s*>', page, re.S)
        if not match:
            raise WagoError("could not find Inertia payload on /db2")
        payload = json.loads(html.unescape(match.group(1)))
        self._inertia_version = payload.get("version")
        return payload["props"]

    def builds(self) -> dict[str, list[Build]]:
        """All builds, newest first, keyed by product."""
        raw = json.loads(self._get("/api/builds"))
        result: dict[str, list[Build]] = {}
        for product, entries in raw.items():
            result[product] = [
                Build(product, entry["version"], entry.get("created_at", ""))
                for entry in entries
                if entry.get("version")
            ]
        return result

    def latest_build(self, product: str = "wow") -> Build:
        builds = self.builds()
        if product not in builds or not builds[product]:
            known = ", ".join(sorted(builds))
            raise WagoError(f"unknown product {product!r}; known products: {known}")
        return builds[product][0]

    def tables(self) -> list[str]:
        """Every DB2 table wago knows about, alphabetically."""
        props = self._load_db2_index()
        return sorted(props["tables"].values())

    def versions(self) -> list[str]:
        """Builds that actually have DB2 data on wago, newest first."""
        return list(self._load_db2_index()["versions"])

    # ---------------------------------------------------------------- schemas

    def table_meta(self, table: str, build: str) -> TableMeta:
        """Column list, foreign keys and enum/flag labels for one table."""
        props = self._get_inertia(
            f"/db2/{urllib.parse.quote(table)}",
            {"build": build},
            component="DB2",
            props=["headers", "dbdFk", "dbdMeta"],
        )
        columns = list(props.get("headers") or [])

        foreign_keys: dict[str, tuple[str, str]] = {}
        for column, target in ((props.get("dbdFk") or {}).get("foreignKeys") or {}).items():
            table_name, _, column_name = str(target).partition("::")
            if table_name and column_name:
                foreign_keys[column] = (table_name, column_name)

        meta = props.get("dbdMeta") or {}
        return TableMeta(
            name=table,
            columns=columns,
            foreign_keys=foreign_keys,
            enums=_label_map(meta.get("enums")),
            flags=_label_map(meta.get("flags")),
        )

    # -------------------------------------------------------------------- csv

    def table_csv(self, table: str, build: str,
                  locale: str = DEFAULT_LOCALE) -> bytes:
        """The full table as CSV bytes (header row included)."""
        params = {"build": build}
        if locale and locale != DEFAULT_LOCALE:
            params["locale"] = locale
        return self._get(f"/db2/{urllib.parse.quote(table)}/csv", params)


def _decode_body(body: bytes, encoding: str | None) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body


def _label_map(entries) -> dict[str, dict[int, str]]:
    """Turn WoWDBDefs enum/flag definitions into {column: {value: label}}.

    Definitions carry a ``condition`` when the meaning of a column depends on
    another column's value (for example ``Effect`` in SpellEffect).  Those are
    ambiguous on their own, so only unconditional definitions are kept.
    """
    result: dict[str, dict[int, str]] = {}
    for entry in entries or []:
        column = entry.get("column")
        if not column or entry.get("condition"):
            continue
        # Definitions index array members as ``Flags[0]``; the CSV header for
        # the same column is ``Flags_0``.
        column = re.sub(r"\[(\d+)\]$", r"_\1", column)
        labels: dict[int, str] = {}
        for definition in entry.get("definitions") or []:
            name = (definition.get("name") or "").strip()
            if not name:
                continue
            try:
                value = int(str(definition.get("value")), 0)
            except (TypeError, ValueError):
                continue
            labels.setdefault(value, name)
        if labels:
            result.setdefault(column, {}).update(labels)
    return result
