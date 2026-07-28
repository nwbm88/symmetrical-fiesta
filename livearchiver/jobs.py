"""The download/processing queue.

Behaves like a download manager rather than a fire-and-forget list: jobs can
be paused, cancelled, removed, reordered and retried, and each download
carries its own quality setting.  Exactly one job runs at a time, which is
the point — a dozen parallel downloads of multi-gigabyte concert video helps
nobody.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QMutex, QMutexLocker, QThread, QWaitCondition, Signal

# What the user can pick per download.  Values are yt-dlp format strings;
# archive.org ignores everything except "audio".
QUALITY_CHOICES = [
    ("Best available (video + audio)", "best"),
    ("Video up to 1080p", "1080"),
    ("Video up to 720p", "720"),
    ("Video up to 480p (smallest)", "480"),
    ("Audio only", "audio"),
]

QUALITY_LABELS = dict((v, k) for k, v in QUALITY_CHOICES)

# compact forms for the queue's narrow Quality column
QUALITY_SHORT = {"best": "Best", "1080": "1080p", "720": "720p",
                 "480": "480p", "audio": "Audio"}

_FORMAT_STRINGS = {
    "best": "bestvideo+bestaudio/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "audio": "bestaudio/best",
}


def format_string(quality: str) -> str:
    return _FORMAT_STRINGS.get(quality, _FORMAT_STRINGS["best"])


class JobCancelled(Exception):
    """Raised inside a worker to unwind a cancelled download."""


QUEUED, RUNNING, DONE, FAILED, CANCELLED = (
    "queued", "running", "done", "failed", "cancelled")


@dataclass
class Job:
    kind: str                      # "download" | "split" | "extract"
    label: str
    payload: dict
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = QUEUED
    progress: float = -1.0         # -1 = indeterminate
    message: str = ""
    result: object = None

    @property
    def quality(self) -> str:
        return self.payload.get("quality", "best")

    @property
    def is_finished(self) -> bool:
        return self.status in (DONE, FAILED, CANCELLED)


class JobQueue(QThread):
    """Runs jobs one at a time; the list is editable while it runs."""

    changed = Signal()                              # list/status changed
    job_progress = Signal(str, float, str)          # id, fraction, message
    job_finished = Signal(str, bool, str, object)   # id, ok, message, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: list[Job] = []
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._paused = False
        self._stopping = False
        self._cancel_ids: set[str] = set()
        self._current: Job | None = None

    # ------------------------------------------------------------ queries

    def jobs(self) -> list[Job]:
        with QMutexLocker(self._mutex):
            return list(self._jobs)

    def current(self) -> Job | None:
        with QMutexLocker(self._mutex):
            return self._current

    def is_paused(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._paused

    def pending_count(self) -> int:
        with QMutexLocker(self._mutex):
            return sum(1 for j in self._jobs if j.status == QUEUED)

    def has_job_for(self, rec_id: str) -> bool:
        """Is this recording already queued or downloading?"""
        with QMutexLocker(self._mutex):
            return any(j.kind == "download"
                       and j.payload.get("rec", {}).get("id") == rec_id
                       and not j.is_finished
                       for j in self._jobs)

    # ------------------------------------------------------------ editing

    def submit(self, job: Job):
        with QMutexLocker(self._mutex):
            self._jobs.append(job)
            self._wake.wakeAll()
        self.changed.emit()

    def cancel(self, job_id: str):
        """Cancel a running job, or drop a queued one."""
        with QMutexLocker(self._mutex):
            for j in self._jobs:
                if j.id != job_id:
                    continue
                if j.status == RUNNING:
                    self._cancel_ids.add(job_id)
                elif j.status == QUEUED:
                    j.status = CANCELLED
                    j.message = "cancelled"
                break
        self.changed.emit()

    def remove(self, job_id: str) -> bool:
        """Remove a job from the list. Running jobs are cancelled instead."""
        removed = False
        with QMutexLocker(self._mutex):
            for j in list(self._jobs):
                if j.id != job_id:
                    continue
                if j.status == RUNNING:
                    self._cancel_ids.add(j.id)
                else:
                    self._jobs.remove(j)
                    removed = True
                break
        self.changed.emit()
        return removed

    def retry(self, job_id: str):
        with QMutexLocker(self._mutex):
            for j in self._jobs:
                if j.id == job_id and j.is_finished:
                    j.status = QUEUED
                    j.progress = -1.0
                    j.message = "waiting"
                    j.result = None
                    self._wake.wakeAll()
                    break
        self.changed.emit()

    def move(self, job_id: str, delta: int):
        """Reorder a pending job. Only queued jobs can move."""
        with QMutexLocker(self._mutex):
            idx = next((i for i, j in enumerate(self._jobs)
                        if j.id == job_id), None)
            if idx is None or self._jobs[idx].status != QUEUED:
                return
            new = idx + delta
            # can't jump above a running/finished job
            while 0 <= new < len(self._jobs) and self._jobs[new].status != QUEUED:
                new += delta
            if 0 <= new < len(self._jobs):
                self._jobs[idx], self._jobs[new] = self._jobs[new], self._jobs[idx]
        self.changed.emit()

    def set_quality(self, job_id: str, quality: str):
        with QMutexLocker(self._mutex):
            for j in self._jobs:
                if j.id == job_id and j.status == QUEUED:
                    j.payload["quality"] = quality
                    break
        self.changed.emit()

    def clear_finished(self) -> int:
        with QMutexLocker(self._mutex):
            before = len(self._jobs)
            self._jobs = [j for j in self._jobs if not j.is_finished]
            gone = before - len(self._jobs)
        self.changed.emit()
        return gone

    def clear_all(self) -> int:
        """Remove everything; a running job is cancelled."""
        with QMutexLocker(self._mutex):
            gone = 0
            keep = []
            for j in self._jobs:
                if j.status == RUNNING:
                    self._cancel_ids.add(j.id)
                    keep.append(j)
                else:
                    gone += 1
            self._jobs = keep
        self.changed.emit()
        return gone

    def set_paused(self, paused: bool):
        """Pause between jobs. A download already running is left to finish."""
        with QMutexLocker(self._mutex):
            self._paused = paused
            if not paused:
                self._wake.wakeAll()
        self.changed.emit()

    def shutdown(self):
        with QMutexLocker(self._mutex):
            self._stopping = True
            if self._current:
                self._cancel_ids.add(self._current.id)
            self._wake.wakeAll()

    # ------------------------------------------------------------- runner

    def _next_job(self) -> Job | None:
        """Block until there is a job to run, or we're shutting down."""
        while True:
            with QMutexLocker(self._mutex):
                if self._stopping:
                    return None
                if not self._paused:
                    for j in self._jobs:
                        if j.status == QUEUED:
                            j.status = RUNNING
                            j.message = "starting"
                            self._current = j
                            return j
                self._wake.wait(self._mutex, 250)

    def _cancelled(self, job_id: str) -> bool:
        with QMutexLocker(self._mutex):
            return job_id in self._cancel_ids

    def run(self):
        from .download import download
        from .pipeline import split_show

        while True:
            job = self._next_job()
            if job is None:
                return
            self.changed.emit()

            def prog(frac, msg, _id=job.id):
                if self._cancelled(_id):
                    raise JobCancelled()
                self.job_progress.emit(_id, -1.0 if frac is None else float(frac),
                                       msg or "")

            ok, message, result = False, "", None
            try:
                if job.kind == "download":
                    dest = download(job.payload["rec"],
                                    Path(job.payload["collection"]),
                                    quality=job.quality,
                                    progress=prog,
                                    should_cancel=lambda: self._cancelled(job.id),
                                    cookies_browser=job.payload.get(
                                        "cookies_browser", ""))
                    ok, message, result = True, f"saved to {dest}", {"dest": str(dest)}
                elif job.kind in ("split", "extract"):
                    res = split_show(Path(job.payload["show_dir"]),
                                     setlist_key=job.payload.get("setlist_key") or None,
                                     redetect=job.payload.get("redetect", False),
                                     cut=(job.kind == "split"),
                                     progress=prog)
                    if job.kind == "split":
                        message = f"{len(res['files'])} tracks via {res['strategy']}"
                        if res.get("warnings"):
                            message += f" ({len(res['warnings'])} warning(s))"
                    else:
                        message = "audio master extracted"
                    ok, result = True, res
            except JobCancelled:
                message = "cancelled"
            except Exception as e:
                message = str(e)

            with QMutexLocker(self._mutex):
                cancelled = job.id in self._cancel_ids
                self._cancel_ids.discard(job.id)
                job.status = (CANCELLED if cancelled or message == "cancelled"
                              else DONE if ok else FAILED)
                job.message = message
                job.result = result
                job.progress = 1.0 if job.status == DONE else job.progress
                self._current = None
                if self._stopping:
                    return
            self.job_finished.emit(job.id, job.status == DONE, message, result)
            self.changed.emit()
