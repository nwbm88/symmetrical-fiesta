"""Shared, disk-cached thumbnail fetching.

The catalogue view asks for a thumbnail per visible row, so this has to be
cheap: images are cached on disk (and in memory as pixmaps), fetched on a
small thread pool, and every request for the same recording is coalesced
into one download.  Nothing blocks the UI thread.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt
from PySide6.QtGui import QPixmap

# Small enough that a few hundred cost nothing to keep around.
THUMB_W, THUMB_H = 128, 72
MAX_MEMORY_ITEMS = 600


def _urls_for(rec: dict) -> list[str]:
    rid = rec.get("id", "")
    if rec.get("source") == "youtube" and ":" in rid:
        vid = rid.split(":", 1)[1]
        return [f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"]
    if ":" in rid:
        return [f"https://archive.org/services/img/{rid.split(':', 1)[1]}"]
    return []


def full_size_urls(rec: dict) -> list[str]:
    """Bigger images for the preview dialog."""
    rid = rec.get("id", "")
    if rec.get("source") == "youtube" and ":" in rid:
        vid = rid.split(":", 1)[1]
        return [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"]
    return _urls_for(rec)


class _Signals(QObject):
    done = Signal(str, bytes)
    failed = Signal(str)


class _FetchTask(QRunnable):
    def __init__(self, rec_id: str, urls: list[str], cache_file: Path,
                 signals: _Signals):
        super().__init__()
        self.rec_id, self.urls, self.cache_file = rec_id, urls, cache_file
        self.signals = signals

    def run(self):
        for url in self.urls:
            try:
                r = requests.get(url, timeout=15)
                if r.ok and r.content and len(r.content) > 1000:
                    try:
                        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                        self.cache_file.write_bytes(r.content)
                    except OSError:
                        pass
                    self.signals.done.emit(self.rec_id, r.content)
                    return
            except Exception:
                continue
        self.signals.failed.emit(self.rec_id)


class ThumbnailCache(QObject):
    """Hands out thumbnails, fetching them in the background once each."""

    ready = Signal(str, QPixmap)     # recording id, scaled pixmap

    def __init__(self, cache_dir: Path, parent=None):
        super().__init__(parent)
        self.cache_dir = Path(cache_dir)
        self._memory: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._missing: set[str] = set()
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)

    def _cache_file(self, rec_id: str) -> Path:
        digest = hashlib.sha1(rec_id.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.jpg"

    def get(self, rec: dict):
        """Pixmap if we already have it, else None and a fetch is started."""
        rec_id = rec.get("id", "")
        if not rec_id or rec_id in self._missing:
            return None
        pix = self._memory.get(rec_id)
        if pix is not None:
            return pix

        cached = self._cache_file(rec_id)
        if cached.exists():
            pix = QPixmap()
            if pix.load(str(cached)):
                scaled = self._scale(pix)
                self._remember(rec_id, scaled)
                return scaled

        if rec_id not in self._pending:
            urls = _urls_for(rec)
            if not urls:
                self._missing.add(rec_id)
                return None
            self._pending.add(rec_id)
            self._pool.start(_FetchTask(rec_id, urls, cached, self._signals))
        return None

    @staticmethod
    def _scale(pix: QPixmap) -> QPixmap:
        return pix.scaled(THUMB_W, THUMB_H, Qt.KeepAspectRatio,
                          Qt.SmoothTransformation)

    def _remember(self, rec_id: str, pix: QPixmap):
        if len(self._memory) >= MAX_MEMORY_ITEMS:
            # cheap eviction: the disk cache still has everything
            self._memory.clear()
        self._memory[rec_id] = pix

    def _on_done(self, rec_id: str, data: bytes):
        self._pending.discard(rec_id)
        pix = QPixmap()
        if not pix.loadFromData(data):
            self._missing.add(rec_id)
            return
        scaled = self._scale(pix)
        self._remember(rec_id, scaled)
        self.ready.emit(rec_id, scaled)

    def _on_failed(self, rec_id: str):
        self._pending.discard(rec_id)
        self._missing.add(rec_id)

    def clear_disk_cache(self) -> int:
        n = 0
        if self.cache_dir.exists():
            for p in self.cache_dir.glob("*.jpg"):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        self._memory.clear()
        self._missing.clear()
        return n

    def shutdown(self):
        self._pool.clear()
        self._pool.waitForDone(2000)
