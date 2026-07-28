"""Checksums, verification and catalogue backups.

An archive meant to last decades needs to be able to answer "is this file
still the file I downloaded?".  Silent corruption, a truncated download and
a half-written copy all look like a perfectly ordinary file otherwise.

Every downloaded show gets a ``checksums.json`` next to ``show.json``
recording the size and SHA-256 of each source file, and ``verify_show``
re-checks them.  Derived audio (the master and the split tracks) is
deliberately not hashed: it can always be regenerated from the source, and
hashing gigabytes of FLAC on every check would make verification something
you avoid running.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("livearchiver")

CHECKSUM_FILE = "checksums.json"
CHUNK = 1 << 20

OK, MISSING, CHANGED, UNRECORDED, NO_RECORD = (
    "ok", "missing", "changed", "unrecorded", "no-record")


def sha256_of(path: Path, progress: Optional[Callable] = None) -> str:
    h = hashlib.sha256()
    total = path.stat().st_size or 1
    done = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done / total, f"hashing {path.name}")
    return h.hexdigest()


def source_files(show_dir: Path) -> list[Path]:
    """The irreplaceable files — the original download, not derived audio."""
    return sorted(p for p in show_dir.iterdir()
                  if p.is_file()
                  and p.name not in (CHECKSUM_FILE, "show.json")
                  and not p.name.endswith(".part"))


def record_checksums(show_dir: Path, progress: Optional[Callable] = None) -> dict:
    """Hash the show's source files and store the result."""
    entries = {}
    for p in source_files(show_dir):
        entries[p.name] = {"size": p.stat().st_size,
                           "sha256": sha256_of(p, progress)}
    data = {"algorithm": "sha256", "recorded": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "files": entries}
    (show_dir / CHECKSUM_FILE).write_text(json.dumps(data, indent=2))
    return data


def verify_show(show_dir: Path, progress: Optional[Callable] = None,
                quick: bool = False) -> dict:
    """Check a show against its recorded checksums.

    ``quick`` compares sizes only, which is instant and still catches
    truncated and missing files; the full check re-hashes and additionally
    catches silent corruption.
    """
    record_path = show_dir / CHECKSUM_FILE
    result = {"dir": str(show_dir), "status": OK, "files": {}, "problems": []}
    if not record_path.exists():
        result["status"] = NO_RECORD
        result["problems"].append(
            "no checksums recorded — nothing to compare against")
        return result
    try:
        data = json.loads(record_path.read_text())
    except Exception as e:
        result["status"] = NO_RECORD
        result["problems"].append(f"checksum file unreadable: {e}")
        return result

    recorded = data.get("files", {})
    for name, meta in recorded.items():
        p = show_dir / name
        if not p.exists():
            result["files"][name] = MISSING
            result["problems"].append(f"{name}: missing")
            continue
        if p.stat().st_size != meta.get("size"):
            result["files"][name] = CHANGED
            result["problems"].append(
                f"{name}: size changed ({meta.get('size')} -> "
                f"{p.stat().st_size} bytes) — likely truncated")
            continue
        if quick:
            result["files"][name] = OK
            continue
        if sha256_of(p, progress) != meta.get("sha256"):
            result["files"][name] = CHANGED
            result["problems"].append(
                f"{name}: contents changed — the file is corrupt")
        else:
            result["files"][name] = OK

    for p in source_files(show_dir):
        if p.name not in recorded:
            result["files"][p.name] = UNRECORDED
            result["problems"].append(f"{p.name}: present but never recorded")

    if result["problems"]:
        result["status"] = (NO_RECORD if result["status"] == NO_RECORD
                            else CHANGED)
    return result


# ----------------------------------------------------------- catalogue backup

BACKUP_KEEP = 10


def backup_catalog(path: Path, keep: int = BACKUP_KEEP) -> Optional[Path]:
    """Keep rotating copies of the catalogue next to it.

    The catalogue holds the *curation* — which duplicate you picked, what you
    ignored, which bands are finished.  The audio can be downloaded again;
    those judgements cannot.
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return None
    backup_dir = path.parent / f"{path.stem}.backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.stem}-{stamp}{path.suffix}"
    if target.exists():
        return target
    try:
        shutil.copy2(path, target)
    except OSError as e:
        log.warning("could not back up the catalogue: %s", e)
        return None

    existing = sorted(backup_dir.glob(f"{path.stem}-*{path.suffix}"))
    for old in existing[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def latest_backup(path: Path) -> Optional[Path]:
    backup_dir = Path(path).parent / f"{Path(path).stem}.backups"
    if not backup_dir.exists():
        return None
    backups = sorted(backup_dir.glob(f"{Path(path).stem}-*{Path(path).suffix}"))
    return backups[-1] if backups else None


# ---------------------------------------------------------------- disk space

def free_space(path: Path) -> int:
    """Bytes free on the volume holding *path* (walking up if need be)."""
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return -1


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"
