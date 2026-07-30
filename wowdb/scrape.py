"""Mirror every DB2 table for one build into a local directory.

Layout of the output directory::

    <out>/manifest.json      what was fetched, from which build, and when
    <out>/meta/<Table>.json  columns, foreign keys, enum + flag labels
    <out>/csv/<Table>.csv    the table contents

The scrape is resumable: anything already on disk and recorded as complete in
the manifest is skipped, so an interrupted run can simply be re-issued.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .wago import DEFAULT_LOCALE, Build, TableMeta, WagoClient, WagoError


class Manifest:
    """Record of a scrape, kept on disk so runs can resume."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict = {"tables": {}}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except ValueError:
                pass
        self.data.setdefault("tables", {})
        self._lock = threading.Lock()

    @property
    def build(self) -> str | None:
        return self.data.get("build")

    @property
    def locale(self) -> str:
        return self.data.get("locale", DEFAULT_LOCALE)

    def start(self, build: Build, locale: str, table_count: int) -> None:
        # A different build or locale invalidates everything cached so far.
        if self.data.get("build") != build.version or self.data.get("locale") != locale:
            self.data["tables"] = {}
        self.data.update(
            build=build.version,
            product=build.product,
            build_created_at=build.created_at,
            locale=locale,
            table_count=table_count,
            started_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            source="https://wago.tools",
        )
        self.save()

    def entry(self, table: str) -> dict:
        return self.data["tables"].get(table, {})

    def record(self, table: str, **fields) -> None:
        with self._lock:
            self.data["tables"].setdefault(table, {}).update(fields)

    def finish(self) -> None:
        self.data["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)


def resolve_build(client: WagoClient, product: str, build: str | None) -> Build:
    """Turn ``--build latest`` / an explicit version into a concrete Build."""
    if build in (None, "", "latest"):
        return client.latest_build(product)

    for candidate in client.builds().get(product, []):
        if candidate.version == build:
            return candidate
    # Still allow it: wago serves DB2 data for builds beyond the product feed.
    return Build(product, build, "")


def scrape(
    out_dir: Path,
    product: str = "wow",
    build: str | None = "latest",
    locale: str = DEFAULT_LOCALE,
    tables: list[str] | None = None,
    workers: int = 6,
    refresh: bool = False,
    log=print,
) -> dict:
    """Download metadata + CSV for every table. Returns the manifest data."""
    client = WagoClient()

    target = resolve_build(client, product, build)
    log(f"build   : {target}")

    all_tables = client.tables()
    if tables:
        wanted = {name.lower() for name in tables}
        selected = [name for name in all_tables if name.lower() in wanted]
        missing = wanted - {name.lower() for name in selected}
        if missing:
            raise WagoError(f"unknown table(s): {', '.join(sorted(missing))}")
    else:
        selected = all_tables
    log(f"tables  : {len(selected)}")
    log(f"locale  : {locale}")
    log(f"output  : {out_dir}")

    meta_dir = out_dir / "meta"
    csv_dir = out_dir / "csv"
    meta_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(out_dir / "manifest.json")
    manifest.start(target, locale, len(selected))

    counters = {"done": 0, "skipped": 0, "failed": 0, "bytes": 0}
    lock = threading.Lock()
    started = time.monotonic()

    def fetch(table: str) -> None:
        meta_path = meta_dir / f"{table}.json"
        csv_path = csv_dir / f"{table}.csv"
        entry = manifest.entry(table)

        if (
            not refresh
            and entry.get("status") == "ok"
            and meta_path.exists()
            and csv_path.exists()
            and csv_path.stat().st_size == entry.get("csv_bytes")
        ):
            with lock:
                counters["skipped"] += 1
            return

        try:
            meta = client.table_meta(table, target.version)
            _write_meta(meta_path, meta)

            data = client.table_csv(table, target.version, locale)
            csv_path.write_bytes(data)

            rows = max(data.count(b"\n") - 1, 0)
            manifest.record(
                table,
                status="ok",
                csv_bytes=len(data),
                approx_rows=rows,
                columns=len(meta.columns),
            )
            with lock:
                counters["done"] += 1
                counters["bytes"] += len(data)
        except WagoError as exc:
            # Tables come and go between builds; a 404 here is normal.
            manifest.record(table, status="failed", error=str(exc))
            with lock:
                counters["failed"] += 1

        with lock:
            processed = counters["done"] + counters["skipped"] + counters["failed"]
        if processed % 25 == 0 or processed == len(selected):
            elapsed = time.monotonic() - started
            log(
                f"  [{processed:>4}/{len(selected)}] "
                f"{counters['done']} fetched, {counters['skipped']} cached, "
                f"{counters['failed']} failed, "
                f"{counters['bytes'] / 1e6:.0f} MB, {elapsed:.0f}s"
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, table) for table in selected]
        for future in as_completed(futures):
            # Surface programming errors instead of losing them in a worker.
            future.result()
            if counters["done"] % 50 == 0:
                manifest.save()

    manifest.finish()

    failed = sorted(
        name for name, entry in manifest.data["tables"].items()
        if entry.get("status") == "failed"
    )
    log(
        f"done    : {counters['done']} fetched, {counters['skipped']} cached, "
        f"{len(failed)} unavailable, {counters['bytes'] / 1e6:.1f} MB in "
        f"{time.monotonic() - started:.0f}s"
    )
    if failed:
        preview = ", ".join(failed[:8])
        more = f" (+{len(failed) - 8} more)" if len(failed) > 8 else ""
        log(f"note    : not published for this build: {preview}{more}")
    return manifest.data


def _write_meta(path: Path, meta: TableMeta) -> None:
    payload = dataclasses.asdict(meta)
    # JSON object keys must be strings; enum/flag values are ints.
    for section in ("enums", "flags"):
        payload[section] = {
            column: {str(value): label for value, label in labels.items()}
            for column, labels in payload[section].items()
        }
    payload["foreign_keys"] = {
        column: list(target) for column, target in payload["foreign_keys"].items()
    }
    path.write_text(json.dumps(payload, indent=1, sort_keys=True))


def load_meta(path: Path) -> TableMeta:
    """Read back what :func:`_write_meta` produced."""
    payload = json.loads(path.read_text())
    return TableMeta(
        name=payload["name"],
        columns=payload["columns"],
        foreign_keys={
            column: (target[0], target[1])
            for column, target in payload["foreign_keys"].items()
        },
        enums={
            column: {int(value): label for value, label in labels.items()}
            for column, labels in payload["enums"].items()
        },
        flags={
            column: {int(value): label for value, label in labels.items()}
            for column, labels in payload["flags"].items()
        },
    )


if __name__ == "__main__":  # pragma: no cover
    scrape(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
