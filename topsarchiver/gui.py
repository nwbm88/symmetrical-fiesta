"""Native desktop app (Qt / PySide6).  Launch with `topsarchiver gui`.

Two tabs:

* **Get Music** — search YouTube + archive.org, browse the catalog grouped
  by show (duplicates side by side, best version suggested), and queue
  downloads.  The queue is strictly sequential: one download at a time.
* **Collection** — everything you have: shows, provenance (date/place/source),
  the original description, the split tracks, and buttons to extract & split.

Splits share the same job queue as downloads, so only one heavy task ever
runs at once.
"""

from __future__ import annotations

import json
import queue
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QCheckBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QSplitter,
    QListWidget, QListWidgetItem, QProgressBar, QLabel, QPlainTextEdit,
    QFileDialog, QDialog, QFormLayout, QDialogButtonBox, QMessageBox,
)

from . import catalog as cat_mod
from . import sources
from .download import download
from .pipeline import split_show, scan_collection

REC_ROLE = Qt.UserRole + 1


# ------------------------------------------------------------------ workers

class SearchWorker(QThread):
    """One-shot background search of all sources."""
    done = Signal(list, str)  # results, error summary ("" if fine)

    def __init__(self, queries, limit=50, parent=None):
        super().__init__(parent)
        self.queries, self.limit = queries, limit

    def run(self):
        results, errors = [], []
        for name, fn in sources.SEARCHERS.items():
            try:
                results.extend(fn(self.queries, self.limit))
            except Exception as e:
                errors.append(f"{name}: {e}")
        self.done.emit(results, "; ".join(errors))


class Job:
    def __init__(self, kind: str, label: str, payload: dict):
        self.id = uuid.uuid4().hex
        self.kind, self.label, self.payload = kind, label, payload


class JobWorker(QThread):
    """Persistent worker that executes downloads and splits one at a time."""
    job_started = Signal(str)
    job_progress = Signal(str, float, str)   # id, fraction (-1 = busy), message
    job_finished = Signal(str, bool, str, object)  # id, ok, message, result

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: "queue.Queue[Job | None]" = queue.Queue()

    def submit(self, job: Job):
        self.jobs.put(job)

    def shutdown(self):
        self.jobs.put(None)

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            self.job_started.emit(job.id)

            def prog(frac, msg, _id=job.id):
                self.job_progress.emit(_id, -1.0 if frac is None else float(frac), msg)

            try:
                if job.kind == "download":
                    dest = download(job.payload["rec"],
                                    Path(job.payload["collection"]),
                                    audio_only=job.payload["audio_only"],
                                    progress=prog)
                    self.job_finished.emit(job.id, True, f"saved to {dest}",
                                           {"dest": str(dest)})
                elif job.kind == "split":
                    res = split_show(Path(job.payload["show_dir"]),
                                     setlist_key=job.payload.get("setlist_key") or None,
                                     redetect=job.payload.get("redetect", False),
                                     progress=prog)
                    self.job_finished.emit(
                        job.id, True,
                        f"{len(res['files'])} tracks via {res['strategy']}", res)
            except Exception as e:
                self.job_finished.emit(job.id, False, str(e), None)


# ------------------------------------------------------------------ dialogs

class EditShowDialog(QDialog):
    """Fix the date/venue of a downloaded show by hand."""

    def __init__(self, show: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit show details")
        form = QFormLayout(self)
        self.date = QLineEdit(show.get("date") or "")
        self.date.setPlaceholderText("YYYY-MM-DD")
        self.venue = QLineEdit(show.get("venue") or "")
        self.venue.setPlaceholderText("Venue, City")
        form.addRow("Date", self.date)
        form.addRow("Venue / place", self.venue)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return {"date": self.date.text().strip() or None,
                "venue": self.venue.text().strip() or None}


# ------------------------------------------------------------------ main win

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Twenty One Pilots Archiver")
        self.resize(1200, 780)

        self.settings = QSettings("topsarchiver", "topsarchiver")
        self.catalog_path = Path(self.settings.value("catalog", "catalog.json"))
        self.cat = cat_mod.load(self.catalog_path)
        self.cat.setdefault("downloaded", {})
        self.cat.setdefault("ignored", {})

        self.worker = JobWorker(self)
        self.worker.job_started.connect(self.on_job_started)
        self.worker.job_progress.connect(self.on_job_progress)
        self.worker.job_finished.connect(self.on_job_finished)
        self.worker.start()
        self.active_jobs: dict[str, tuple[Job, QListWidgetItem]] = {}
        self.search_worker = None

        tabs = QTabWidget()
        tabs.addTab(self._build_acquire_tab(), "Get Music")
        tabs.addTab(self._build_collection_tab(), "Collection")
        tabs.addTab(self._build_settings_tab(), "Settings")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Ready")

        self.refresh_tree()
        self.refresh_collection()

    # -------------------------------------------------- settings properties

    @property
    def collection_dir(self) -> Path:
        return Path(self.settings.value("collection_dir", "collection"))

    @property
    def setlist_key(self) -> str:
        return self.settings.value("setlist_key", "")

    @property
    def audio_only(self) -> bool:
        return self.settings.value("audio_only", "false") == "true"

    # -------------------------------------------------------- Get Music tab

    def _build_acquire_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        bar = QHBoxLayout()
        self.search_btn = QPushButton("Search sources")
        self.search_btn.clicked.connect(self.start_search)
        self.extra_query = QLineEdit()
        self.extra_query.setPlaceholderText(
            "optional extra search, e.g. “twenty one pilots red rocks 2019” "
            "(added to the built-in queries)")
        bar.addWidget(self.search_btn)
        bar.addWidget(self.extra_query, 1)
        outer.addLayout(bar)

        filters = QHBoxLayout()
        self.f_new = QCheckBox("Still to download")
        self.f_dupes = QCheckBox("Only shows with multiple versions")
        self.f_ignored = QCheckBox("Show ignored")
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("filter by title / venue / date ...")
        for cb in (self.f_new, self.f_dupes, self.f_ignored):
            cb.toggled.connect(self.refresh_tree)
        self.f_text.textChanged.connect(self.refresh_tree)
        filters.addWidget(self.f_new)
        filters.addWidget(self.f_dupes)
        filters.addWidget(self.f_ignored)
        filters.addWidget(self.f_text, 1)
        outer.addLayout(filters)

        split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Show / version", "Source", "Length",
                                   "Quality", "Views", "Status"])
        self.tree.setColumnWidth(0, 520)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self.open_recording_page)
        split.addWidget(self.tree)

        qpanel = QWidget()
        qlay = QVBoxLayout(qpanel)
        qlay.addWidget(QLabel("Download queue (one at a time)"))
        self.queue_list = QListWidget()
        qlay.addWidget(self.queue_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        qlay.addWidget(self.progress)
        self.progress_label = QLabel("idle")
        self.progress_label.setWordWrap(True)
        qlay.addWidget(self.progress_label)
        split.addWidget(qpanel)
        split.setSizes([820, 340])
        outer.addWidget(split, 1)

        btns = QHBoxLayout()
        b_queue = QPushButton("⬇ Queue selected")
        b_queue.setToolTip("Queues each selected version. Selecting a show row "
                           "queues its suggested best version.")
        b_queue.clicked.connect(self.queue_selected)
        b_ignore = QPushButton("Ignore selected version")
        b_ignore.setToolTip("Hide a duplicate you don't want; it stays in the "
                            "catalog but out of your way.")
        b_ignore.clicked.connect(self.ignore_selected)
        btns.addWidget(b_queue)
        btns.addWidget(b_ignore)
        btns.addStretch(1)
        outer.addLayout(btns)
        return w

    def start_search(self):
        if self.search_worker and self.search_worker.isRunning():
            return
        queries = list(sources.DEFAULT_QUERIES)
        extra = self.extra_query.text().strip()
        if extra:
            queries.append(extra)
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Searching ...")
        self.statusBar().showMessage("Searching YouTube and archive.org ...")
        self.search_worker = SearchWorker(queries, parent=self)
        self.search_worker.done.connect(self.on_search_done)
        self.search_worker.start()

    def on_search_done(self, results, errors):
        added = cat_mod.merge_results(self.cat, results)
        self.save_catalog()
        self.search_btn.setEnabled(True)
        self.search_btn.setText("Search sources")
        msg = f"Search finished: {added} new recordings, " \
              f"{len(self.cat['recordings'])} total."
        if errors:
            msg += f"  Problems: {errors}"
        self.statusBar().showMessage(msg)
        self.refresh_tree()

    def refresh_tree(self):
        self.tree.clear()
        downloaded = self.cat["downloaded"]
        ignored = self.cat["ignored"]
        needle = self.f_text.text().strip().lower()
        pending = {j.payload["rec"]["id"] for j, _ in self.active_jobs.values()
                   if j.kind == "download"}

        bold = QFont()
        bold.setBold(True)
        for g in cat_mod.group_recordings(self.cat):
            recs = [r for r in g["recordings"]
                    if self.f_ignored.isChecked() or r["id"] not in ignored]
            if not recs:
                continue
            if self.f_dupes.isChecked() and len(recs) < 2:
                continue
            if self.f_new.isChecked() and any(r["id"] in downloaded for r in recs):
                continue
            if needle:
                blob = " ".join([g.get("date") or "", g.get("venue") or ""] +
                                [r["title"] for r in recs]).lower()
                if needle not in blob:
                    continue

            head = g["date"] or "unknown date"
            if g["venue"]:
                head += f" — {g['venue']}"
            top = QTreeWidgetItem([head, "", "", "", "",
                                   f"{len(recs)} version(s)"])
            top.setFont(0, bold)
            top.setData(0, REC_ROLE, recs[0]["id"])  # best version
            for i, r in enumerate(recs):
                if r["id"] in downloaded:
                    status = "✓ downloaded"
                elif r["id"] in pending:
                    status = "queued"
                elif r["id"] in ignored:
                    status = "ignored"
                elif i == 0 and len(recs) > 1:
                    status = "suggested"
                else:
                    status = ""
                child = QTreeWidgetItem([
                    r["title"], r["source"], _fmt_dur(r.get("duration")),
                    r.get("quality") or "", _fmt_views(r.get("views")), status])
                child.setData(0, REC_ROLE, r["id"])
                child.setToolTip(0, r["url"])
                top.addChild(child)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()

    def _selected_rec_ids(self) -> list[str]:
        ids, seen = [], set()
        for item in self.tree.selectedItems():
            rid = item.data(0, REC_ROLE)
            if rid and rid not in seen:
                seen.add(rid)
                ids.append(rid)
        return ids

    def queue_selected(self):
        ids = self._selected_rec_ids()
        if not ids:
            self.statusBar().showMessage("Select one or more versions first.")
            return
        for rid in ids:
            rec = self.cat["recordings"].get(rid)
            if not rec or rid in self.cat["downloaded"]:
                continue
            if any(j.kind == "download" and j.payload["rec"]["id"] == rid
                   for j, _ in self.active_jobs.values()):
                continue
            job = Job("download", rec["title"][:70],
                      {"rec": rec, "collection": str(self.collection_dir),
                       "audio_only": self.audio_only})
            self._enqueue(job)
        self.refresh_tree()

    def ignore_selected(self):
        for rid in self._selected_rec_ids():
            if rid not in self.cat["downloaded"]:
                self.cat["ignored"][rid] = True
        self.save_catalog()
        self.refresh_tree()

    def open_recording_page(self, item, _col):
        rid = item.data(0, REC_ROLE)
        rec = self.cat["recordings"].get(rid)
        if rec:
            QDesktopServices.openUrl(QUrl(rec["url"]))

    # ------------------------------------------------------- Collection tab

    def _build_collection_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        llay = QVBoxLayout(left)
        self.show_list = QListWidget()
        self.show_list.currentRowChanged.connect(self.show_details)
        llay.addWidget(self.show_list, 1)
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self.refresh_collection)
        llay.addWidget(b_refresh)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        self.detail_head = QLabel("Select a show")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        self.detail_head.setFont(f)
        self.detail_head.setWordWrap(True)
        rlay.addWidget(self.detail_head)
        self.detail_meta = QLabel("")
        self.detail_meta.setWordWrap(True)
        self.detail_meta.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.detail_meta.setOpenExternalLinks(True)
        rlay.addWidget(self.detail_meta)

        self.track_tree = QTreeWidget()
        self.track_tree.setHeaderLabels(["#", "Track", "Status"])
        self.track_tree.setColumnWidth(0, 40)
        self.track_tree.setColumnWidth(1, 420)
        self.track_tree.setRootIsDecorated(False)
        self.track_tree.setAlternatingRowColors(True)
        rlay.addWidget(self.track_tree, 2)

        rlay.addWidget(QLabel("Original description"))
        self.description = QPlainTextEdit()
        self.description.setReadOnly(True)
        rlay.addWidget(self.description, 1)

        btns = QHBoxLayout()
        self.b_split = QPushButton("Extract && split tracks")
        self.b_split.clicked.connect(self.queue_split)
        b_resplit = QPushButton("Re-detect && split")
        b_resplit.setToolTip("Throw away tracks.json and detect the tracklist "
                             "again (after fixing chapters/setlist issues).")
        b_resplit.clicked.connect(lambda: self.queue_split(redetect=True))
        b_edit = QPushButton("Edit date/venue")
        b_edit.clicked.connect(self.edit_show)
        b_open = QPushButton("Open folder")
        b_open.clicked.connect(self.open_show_folder)
        for b in (self.b_split, b_resplit, b_edit, b_open):
            btns.addWidget(b)
        btns.addStretch(1)
        rlay.addLayout(btns)

        split.addWidget(right)
        split.setSizes([340, 820])
        outer.addWidget(split)
        return w

    def refresh_collection(self):
        self.shows = scan_collection(self.collection_dir)
        row = self.show_list.currentRow()
        self.show_list.clear()
        for s in self.shows:
            n_tracks = len(s["tracks"])
            mark = "🎵" if n_tracks else ("🎚" if s["has_master"] else "⬇")
            item = QListWidgetItem(f"{mark} {s['dir'].name}")
            item.setToolTip(f"{n_tracks} split tracks" if n_tracks else
                            "audio extracted, not split" if s["has_master"] else
                            "downloaded, audio not extracted yet")
            self.show_list.addItem(item)
        if self.shows:
            self.show_list.setCurrentRow(min(max(row, 0), len(self.shows) - 1))
        else:
            self.show_details(-1)

    def _current_show(self):
        row = self.show_list.currentRow()
        if 0 <= row < len(self.shows):
            return self.shows[row]
        return None

    def show_details(self, _row):
        s = self._current_show()
        self.track_tree.clear()
        if not s:
            self.detail_head.setText("No shows downloaded yet — use the "
                                     "Get Music tab.")
            self.detail_meta.setText("")
            self.description.setPlainText("")
            return
        m = s["manifest"]
        show = m.get("show", {})
        self.detail_head.setText(m.get("title") or s["dir"].name)
        meta = []
        meta.append(f"<b>Date:</b> {show.get('date') or '<i>unknown — please edit</i>'}")
        meta.append(f"<b>Place:</b> {show.get('venue') or '<i>unknown — please edit</i>'}")
        meta.append(f"<b>Source:</b> {m.get('source')} — "
                    f"<a href='{m.get('source_url')}'>{m.get('source_url')}</a>")
        if m.get("uploader"):
            meta.append(f"<b>Uploaded by:</b> {m['uploader']}")
        state = (f"{len(s['tracks'])} tracks split" if s["tracks"] else
                 "audio extracted, not split yet" if s["has_master"] else
                 "downloaded — audio not extracted yet")
        meta.append(f"<b>Status:</b> {state}")
        self.detail_meta.setText("<br>".join(meta))
        self.description.setPlainText(m.get("description") or "(no description)")

        if s["tracks"]:
            for i, name in enumerate(s["tracks"], 1):
                title = name
                if " - " in name:
                    title = name.split(" - ", 1)[1].rsplit(".", 1)[0]
                self.track_tree.addTopLevelItem(
                    QTreeWidgetItem([f"{i:02d}", title, "✓ split"]))
        elif s["tracklist"]:
            for i, t in enumerate(s["tracklist"], 1):
                self.track_tree.addTopLevelItem(QTreeWidgetItem(
                    [f"{i:02d}", t.get("title", "?"), "detected, not cut"]))

    def queue_split(self, redetect=False):
        s = self._current_show()
        if not s:
            return
        job = Job("split", f"split: {s['dir'].name[:60]}",
                  {"show_dir": str(s["dir"]), "setlist_key": self.setlist_key,
                   "redetect": bool(redetect)})
        self._enqueue(job)

    def edit_show(self):
        s = self._current_show()
        if not s:
            return
        dlg = EditShowDialog(s["manifest"].get("show", {}), self)
        if dlg.exec() != QDialog.Accepted:
            return
        s["manifest"]["show"] = dlg.values()
        (s["dir"] / "show.json").write_text(
            json.dumps(s["manifest"], indent=2, ensure_ascii=False))
        rid = s["manifest"].get("recording_id")
        if rid and rid in self.cat["recordings"]:
            self.cat["recordings"][rid]["show"] = dlg.values()
            self.save_catalog()
        self.refresh_collection()
        self.refresh_tree()

    def open_show_folder(self):
        s = self._current_show()
        if s:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(s["dir"].resolve())))

    # --------------------------------------------------------- Settings tab

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        row = QHBoxLayout()
        self.s_dir = QLineEdit(str(self.collection_dir))
        b = QPushButton("Browse ...")
        b.clicked.connect(self._pick_dir)
        row.addWidget(self.s_dir, 1)
        row.addWidget(b)
        form.addRow("Collection folder", row)

        self.s_key = QLineEdit(self.setlist_key)
        self.s_key.setPlaceholderText("optional — used to auto-name tracks when "
                                      "a video has no chapters/timestamps")
        form.addRow("setlist.fm API key", self.s_key)

        self.s_audio_only = QCheckBox("Download audio only (skip video streams)")
        self.s_audio_only.setChecked(self.audio_only)
        form.addRow("", self.s_audio_only)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        form.addRow("", save)
        form.addRow("", QLabel(
            "Requires ffmpeg on your PATH.  Keep yt-dlp up to date "
            "(pip install -U yt-dlp) — YouTube changes regularly."))
        return w

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Collection folder",
                                             self.s_dir.text())
        if d:
            self.s_dir.setText(d)

    def _save_settings(self):
        self.settings.setValue("collection_dir", self.s_dir.text().strip()
                               or "collection")
        self.settings.setValue("setlist_key", self.s_key.text().strip())
        self.settings.setValue("audio_only",
                               "true" if self.s_audio_only.isChecked() else "false")
        self.statusBar().showMessage("Settings saved.")
        self.refresh_collection()

    # ---------------------------------------------------------- job plumbing

    def _enqueue(self, job: Job):
        item = QListWidgetItem(f"⏳ {job.label}")
        self.queue_list.addItem(item)
        self.active_jobs[job.id] = (job, item)
        self.worker.submit(job)
        self.statusBar().showMessage(f"Queued: {job.label}")

    def on_job_started(self, jid):
        entry = self.active_jobs.get(jid)
        if entry:
            entry[1].setText(f"▶ {entry[0].label}")
            self.progress_label.setText(entry[0].label)

    def on_job_progress(self, jid, frac, msg):
        if frac < 0:
            self.progress.setRange(0, 0)  # indeterminate
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(frac * 1000))
        self.progress_label.setText(msg)

    def on_job_finished(self, jid, ok, msg, result):
        entry = self.active_jobs.pop(jid, None)
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if ok else 0)
        self.progress_label.setText("idle")
        if entry:
            job, item = entry
            item.setText(f"{'✓' if ok else '✗'} {job.label} — {msg}")
            if ok and job.kind == "download":
                self.cat["downloaded"][job.payload["rec"]["id"]] = result["dest"]
                self.save_catalog()
            if not ok:
                QMessageBox.warning(self, "Job failed",
                                    f"{job.label}\n\n{msg}")
        self.statusBar().showMessage(msg)
        self.refresh_tree()
        self.refresh_collection()

    # -------------------------------------------------------------- helpers

    def save_catalog(self):
        cat_mod.save(self.cat, self.catalog_path)

    def closeEvent(self, event):
        if self.active_jobs:
            r = QMessageBox.question(
                self, "Quit?",
                "A download or split is still running — quit anyway?")
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self.worker.shutdown()
        self.worker.wait(1000)
        event.accept()


def _fmt_dur(seconds) -> str:
    if not seconds:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_views(v) -> str:
    return f"{v:,}" if v else ""


def run() -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Twenty One Pilots Archiver")
    win = MainWindow()
    win.show()
    return app.exec()


def main_standalone():
    sys.exit(run())


if __name__ == "__main__":
    main_standalone()
