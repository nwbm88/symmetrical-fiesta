"""Native desktop app (Qt / PySide6).  Launch with `livearchiver gui`,
`run.bat` on Windows or `run.sh` elsewhere.

Four tabs:

* **Get Music** — search YouTube + archive.org, browse the catalog grouped
  by show (duplicates side by side, best version suggested), preview a
  version before committing to it, and queue downloads.  The queue runs one
  job at a time and behaves like a download manager: pause, cancel, retry,
  reorder, per-item quality, clear finished.
* **Collection** — everything you have: shows, provenance (date/place/source),
  the original description, the split tracks, the timeline editor.
* **Artists** — every band in the catalog with its progress, so you can see
  what is finished and remove bands you no longer want.
* **Settings** — collection folder, setlist.fm key, default quality.

Splits share the job queue with downloads, so only one heavy task runs at
once.
"""

from __future__ import annotations

import json
import queue
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QCheckBox, QLineEdit, QTreeWidget, QTreeWidgetItem, QSplitter,
    QListWidget, QListWidgetItem, QProgressBar, QLabel, QPlainTextEdit,
    QFileDialog, QDialog, QFormLayout, QDialogButtonBox, QMessageBox,
    QComboBox, QMenu, QHeaderView,
)

from . import DEFAULT_ARTIST
from . import catalog as cat_mod
from . import sources
from .jobs import (Job, JobQueue, QUALITY_CHOICES, QUALITY_SHORT,
                   QUEUED, RUNNING, DONE, FAILED, CANCELLED)
from .pipeline import scan_collection
from .platformsupport import (check_ffmpeg, check_js_runtime,
                              find_js_runtime, COOKIE_BROWSERS)
from .thumbnails import ThumbnailCache, THUMB_W, THUMB_H
from .showinfo import extract_date
from .tracks import tracks_from_description

REC_ROLE = Qt.UserRole + 1
JOB_ROLE = Qt.UserRole + 2
ARTIST_ROLE = Qt.UserRole + 3


# ------------------------------------------------------------------ workers

class SearchWorker(QThread):
    """One-shot background search of all sources."""
    done = Signal(list, str)  # results, error summary ("" if fine)

    def __init__(self, artist, queries, limit=50, parent=None):
        super().__init__(parent)
        self.artist, self.queries, self.limit = artist, queries, limit

    def run(self):
        results, errors = [], []
        for name, fn in sources.SEARCHERS.items():
            try:
                results.extend(fn(self.artist, self.queries, self.limit))
            except Exception as e:
                errors.append(f"{name}: {e}")
        self.done.emit(results, "; ".join(errors))


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


class TextResolveDialog(QDialog):
    """The 'wall of text' resolver.

    Shows everything we scraped from the page — description, comments,
    reviews — so the user can highlight the piece the heuristics missed and
    say what it is: the date, the place, or a timestamped tracklist.
    """

    def __init__(self, manifest: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find show details in page text")
        self.resize(900, 700)
        self.tracks: list[dict] | None = None
        show = manifest.get("show", {})

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Highlight text below, then click a button to use it. "
            "“Parse selection as tracklist” understands timestamp lines like "
            "“14:32 Song Name”.")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(self._wall_of_text(manifest))
        lay.addWidget(self.text, 1)

        btns = QHBoxLayout()
        b_date = QPushButton("Use selection as date")
        b_date.clicked.connect(self._use_as_date)
        b_venue = QPushButton("Use selection as place")
        b_venue.clicked.connect(self._use_as_venue)
        b_tracks = QPushButton("Parse selection as tracklist")
        b_tracks.clicked.connect(self._use_as_tracklist)
        for b in (b_date, b_venue, b_tracks):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

        form = QFormLayout()
        self.date = QLineEdit(show.get("date") or "")
        self.date.setPlaceholderText("YYYY-MM-DD")
        self.venue = QLineEdit(show.get("venue") or "")
        self.venue.setPlaceholderText("Venue, City")
        self.tracks_label = QLabel("tracklist: unchanged")
        form.addRow("Date", self.date)
        form.addRow("Place", self.venue)
        form.addRow("", self.tracks_label)
        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _wall_of_text(manifest: dict) -> str:
        parts = [f"TITLE:\n{manifest.get('title') or ''}",
                 f"DESCRIPTION:\n{manifest.get('description') or '(none)'}"]
        comments = manifest.get("comments") or []
        if comments:
            parts.append("COMMENTS / REVIEWS:")
            for i, c in enumerate(comments, 1):
                parts.append(f"--- comment {i} ---\n{c}")
        else:
            parts.append("(no comments were captured for this show)")
        return "\n\n".join(parts)

    def _selection(self) -> str:
        # Qt uses U+2029 as the paragraph separator in selections
        return self.text.textCursor().selectedText().replace("\u2029", "\n")

    def _use_as_date(self):
        sel = self._selection()
        parsed = extract_date(sel) if sel else None
        if parsed:
            self.date.setText(parsed)
        else:
            QMessageBox.information(
                self, "No date found",
                "Couldn't read a date from the selection — highlight something "
                "like “June 14, 2016” or “2016-06-14”, or type it into the "
                "Date field yourself.")

    def _use_as_venue(self):
        sel = " ".join(self._selection().split())
        if sel:
            self.venue.setText(sel[:120])

    def _use_as_tracklist(self):
        sel = self._selection()
        tracks = tracks_from_description(sel) if sel else []
        if tracks:
            self.tracks = tracks
            self.tracks_label.setText(
                f"tracklist: {len(tracks)} tracks parsed from selection "
                f"(saved on Save — then use Re-split)")
        else:
            QMessageBox.information(
                self, "No tracklist found",
                "Couldn't parse a tracklist from the selection. It needs "
                "several lines with timestamps, e.g.\n\n"
                "    14:32 Heavydirtysoul\n    18:05 Migraine\n    ...")

    def values(self):
        return {"date": self.date.text().strip() or None,
                "venue": self.venue.text().strip() or None}


# ------------------------------------------------------------------ main win

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live Show Archiver")
        self.resize(1200, 780)

        self.settings = QSettings("livearchiver", "livearchiver")
        self.catalog_path = Path(self.settings.value("catalog", "catalog.json"))
        self.cat = cat_mod.load(self.catalog_path)
        self.cat.setdefault("downloaded", {})
        self.cat.setdefault("ignored", {})
        self.cat.setdefault("finished_artists", {})

        self.thumbs = ThumbnailCache(
            self.catalog_path.parent / ".thumbnails", self)
        self.thumbs.ready.connect(self._thumb_arrived)
        self._thumb_rows: dict[str, list] = {}

        self.queue = JobQueue(self)
        self.queue.job_progress.connect(self.on_job_progress)
        self.queue.job_finished.connect(self.on_job_finished)
        self.queue.changed.connect(self.refresh_queue)
        self.queue.start()
        self.worker = self.queue          # kept for existing call sites
        self.search_worker = None

        tabs = QTabWidget()
        tabs.addTab(self._build_acquire_tab(), "Get Music")
        tabs.addTab(self._build_collection_tab(), "Collection")
        tabs.addTab(self._build_artists_tab(), "Artists")
        tabs.addTab(self._build_settings_tab(), "Settings")
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Ready")

        self.refresh_tree()
        self.refresh_collection()
        self.refresh_artists()
        self.refresh_queue()
        self._warn_if_no_ffmpeg()

    def _warn_if_no_ffmpeg(self):
        problem = check_ffmpeg()
        self._ffmpeg_problem = problem
        notes = []
        if problem:
            notes.append("ffmpeg not found — downloading works, but audio "
                         "extraction and splitting will not")
        if check_js_runtime():
            notes.append("no JavaScript runtime — YouTube downloads may fail "
                         "or miss formats")
        if notes:
            self.statusBar().showMessage(" · ".join(notes) + ". See Settings.")

    # -------------------------------------------------- settings properties

    @property
    def collection_dir(self) -> Path:
        return Path(self.settings.value("collection_dir", "collection"))

    @property
    def setlist_key(self) -> str:
        return self.settings.value("setlist_key", "")

    @property
    def audio_only(self) -> bool:
        return self.settings.value("quality", "best") == "audio"

    @property
    def cookies_browser(self) -> str:
        return self.settings.value("cookies_browser", "")

    # -------------------------------------------------------- Get Music tab

    def _build_acquire_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Band / artist:"))
        self.artist_edit = QLineEdit(
            self.settings.value("artist", DEFAULT_ARTIST))
        self.artist_edit.setPlaceholderText("e.g. Twenty One Pilots")
        bar.addWidget(self.artist_edit, 1)
        self.search_btn = QPushButton("Search sources")
        self.search_btn.clicked.connect(self.start_search)
        self.extra_query = QLineEdit()
        self.extra_query.setPlaceholderText(
            "optional extra search, e.g. “red rocks 2019” "
            "(added to the built-in queries)")
        bar.addWidget(self.search_btn)
        bar.addWidget(self.extra_query, 1)
        outer.addLayout(bar)

        filters = QHBoxLayout()
        self.artist_filter = QComboBox()
        self.artist_filter.addItem("All bands", None)
        self.artist_filter.setToolTip("Narrow the hub down to one band.")
        self.artist_filter.currentIndexChanged.connect(self.refresh_tree)
        filters.addWidget(self.artist_filter)
        self.f_new = QCheckBox("Still to download")
        self.f_dupes = QCheckBox("Only shows with multiple versions")
        self.f_ignored = QCheckBox("Show ignored")
        self.show_thumbs = QCheckBox("Thumbnails")
        self.show_thumbs.setToolTip("Show a preview image for each version. "
                                    "Images are cached on disk after the "
                                    "first fetch.")
        self.show_thumbs.setChecked(
            self.settings.value("show_thumbs", "true") == "true")
        self.show_thumbs.toggled.connect(self._toggle_thumbs)
        self.f_text = QLineEdit()
        self.f_text.setPlaceholderText("filter by title / venue / date ...")
        for cb in (self.f_new, self.f_dupes, self.f_ignored):
            cb.toggled.connect(self.refresh_tree)
        self.f_text.textChanged.connect(self.refresh_tree)
        filters.addWidget(self.f_new)
        filters.addWidget(self.f_dupes)
        filters.addWidget(self.f_ignored)
        filters.addWidget(self.show_thumbs)
        filters.addWidget(self.f_text, 1)
        outer.addLayout(filters)

        split = QSplitter(Qt.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Show / version", "Source", "Length",
                                   "Quality", "Views", "Status"])
        self.tree.setIconSize(QSize(THUMB_W, THUMB_H))
        for col, width in ((1, 74), (2, 62), (3, 56), (4, 68), (5, 88)):
            self.tree.setColumnWidth(col, width)
        self._apply_title_width()
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self.open_recording_page)
        split.addWidget(self.tree)

        split.addWidget(self._build_queue_panel())
        split.setSizes([820, 440])
        outer.addWidget(split, 1)

        btns = QHBoxLayout()
        b_preview = QPushButton("👁 Preview ...")
        b_preview.setToolTip("Look at the thumbnail and skip through the video "
                             "before spending gigabytes on it.")
        b_preview.clicked.connect(self.preview_selected)
        b_queue = QPushButton("⬇ Queue selected")
        b_queue.setToolTip("Queues each selected version. Selecting a show row "
                           "queues its suggested best version.")
        b_queue.clicked.connect(self.queue_selected)
        b_ignore = QPushButton("Ignore selected version")
        b_ignore.setToolTip("Hide a duplicate you don't want; it stays in the "
                            "catalog but out of your way.")
        b_ignore.clicked.connect(self.ignore_selected)
        btns.addWidget(b_preview)
        btns.addWidget(b_queue)
        btns.addWidget(QLabel("at"))
        self.quality_box = QComboBox()
        for label, key in QUALITY_CHOICES:
            self.quality_box.addItem(label, key)
        saved_q = self.settings.value("quality", "best")
        idx = self.quality_box.findData(saved_q)
        self.quality_box.setCurrentIndex(max(idx, 0))
        self.quality_box.setToolTip("Quality for newly queued downloads. "
                                    "Each item in the queue keeps the setting "
                                    "it was added with, and can be changed "
                                    "there.")
        self.quality_box.currentIndexChanged.connect(
            lambda: self.settings.setValue("quality",
                                           self.quality_box.currentData()))
        btns.addWidget(self.quality_box)
        btns.addWidget(b_ignore)
        btns.addStretch(1)
        outer.addLayout(btns)
        return w

    # ------------------------------------------------------- queue panel

    def _build_queue_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)

        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Downloads</b> (one at a time)"))
        head.addStretch(1)
        self.pause_btn = QPushButton("⏸ Pause queue")
        self.pause_btn.setToolTip("Stop starting new downloads. Anything "
                                  "already running is left to finish.")
        self.pause_btn.clicked.connect(self.toggle_queue_paused)
        head.addWidget(self.pause_btn)
        lay.addLayout(head)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderLabels(["", "Item", "Quality", "Progress",
                                         "Status"])
        self.queue_tree.setRootIsDecorated(False)
        self.queue_tree.setAlternatingRowColors(True)
        self.queue_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.queue_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_tree.customContextMenuRequested.connect(self._queue_menu)
        h = self.queue_tree.header()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        self.queue_tree.setColumnWidth(0, 24)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for col, width in ((2, 62), (3, 62), (4, 150)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.queue_tree.setColumnWidth(col, width)
        lay.addWidget(self.queue_tree, 1)

        row1 = QHBoxLayout()
        for text, slot, tip in (
            ("Cancel", self.queue_cancel_selected,
             "Stop a running download, or drop a waiting one."),
            ("Remove", self.queue_remove_selected,
             "Take the item out of the list."),
            ("Retry", self.queue_retry_selected,
             "Put a failed or cancelled item back in the queue."),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row1.addWidget(b)
        row1.addStretch(1)
        b_up = QPushButton("▲")
        b_up.setToolTip("Move up the queue")
        b_up.clicked.connect(lambda: self.queue_move_selected(-1))
        b_down = QPushButton("▼")
        b_down.setToolTip("Move down the queue")
        b_down.clicked.connect(lambda: self.queue_move_selected(1))
        row1.addWidget(b_up)
        row1.addWidget(b_down)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        b_clear_done = QPushButton("Clear finished")
        b_clear_done.setToolTip("Remove completed, failed and cancelled items "
                                "from the list.")
        b_clear_done.clicked.connect(self.queue_clear_finished)
        b_clear_all = QPushButton("Clear all")
        b_clear_all.setToolTip("Empty the queue. A download in progress is "
                               "cancelled.")
        b_clear_all.clicked.connect(self.queue_clear_all)
        row2.addWidget(b_clear_done)
        row2.addWidget(b_clear_all)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.progress_label = QLabel("idle")
        self.progress_label.setWordWrap(True)
        lay.addWidget(self.progress_label)
        return panel

    def start_search(self):
        if self.search_worker and self.search_worker.isRunning():
            return
        artist = self.artist_edit.text().strip()
        if not artist:
            self.statusBar().showMessage("Enter a band/artist name first.")
            return
        self.settings.setValue("artist", artist)
        queries = sources.default_queries(artist)
        extra = self.extra_query.text().strip()
        if extra:
            queries.append(f"{artist} {extra}" if artist.lower() not in extra.lower()
                           else extra)
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Searching ...")
        self.statusBar().showMessage(
            f"Searching YouTube and archive.org for {artist} ...")
        self.search_worker = SearchWorker(artist, queries, parent=self)
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
        self.refresh_artists()

    def refresh_tree(self):
        self.tree.clear()
        self._thumb_rows = {}
        downloaded = self.cat["downloaded"]
        ignored = self.cat["ignored"]
        needle = self.f_text.text().strip().lower()
        pending = {j.payload["rec"]["id"] for j in self.queue.jobs()
                   if j.kind == "download" and not j.is_finished
                   and j.payload.get("rec")}

        bold = QFont()
        bold.setBold(True)
        only_artist = (self.artist_filter.currentData()
                       if hasattr(self, "artist_filter") else None)
        for g in cat_mod.group_recordings(self.cat):
            if only_artist and g.get("artist") != only_artist:
                continue
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
            if g.get("artist") and len(cat_mod.artists_in(self.cat)) > 1:
                head = f"{g['artist']}:  {head}"
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
                if self.show_thumbs.isChecked():
                    pix = self.thumbs.get(r)
                    if pix is not None:
                        child.setIcon(0, QIcon(pix))
                    else:
                        child.setIcon(0, self._placeholder_icon())
                    self._thumb_rows.setdefault(r["id"], []).append(child)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()

    def _apply_title_width(self):
        """The title column needs the extra room a thumbnail takes up."""
        on = self.show_thumbs.isChecked()
        self.tree.setColumnWidth(0, 330 + (THUMB_W + 8 if on else 0))

    def _toggle_thumbs(self, on: bool):
        self.settings.setValue("show_thumbs", "true" if on else "false")
        self.tree.setIconSize(QSize(THUMB_W, THUMB_H) if on else QSize(1, 1))
        self._apply_title_width()
        self.refresh_tree()

    def _placeholder_icon(self) -> QIcon:
        """A neutral tile so rows don't jump around as images arrive."""
        if not hasattr(self, "_placeholder"):
            pix = QPixmap(THUMB_W, THUMB_H)
            pix.fill(Qt.transparent)
            self._placeholder = QIcon(pix)
        return self._placeholder

    def _thumb_arrived(self, rec_id: str, pix):
        for item in self._thumb_rows.get(rec_id, []):
            try:
                item.setIcon(0, QIcon(pix))
            except RuntimeError:
                pass          # row was rebuilt while the fetch was in flight

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
        quality = self.quality_box.currentData() or "best"
        added = skipped = 0
        for rid in ids:
            rec = self.cat["recordings"].get(rid)
            if not rec:
                continue
            if rid in self.cat["downloaded"] or self.queue.has_job_for(rid):
                skipped += 1
                continue
            self._enqueue(Job("download", rec["title"][:70],
                              {"rec": rec, "collection": str(self.collection_dir),
                               "quality": quality,
                               "cookies_browser": self.cookies_browser}))
            added += 1
        if skipped:
            self.statusBar().showMessage(
                f"Queued {added}; skipped {skipped} already downloaded or queued.")
        self.refresh_tree()

    def preview_selected(self):
        ids = self._selected_rec_ids()
        if not ids:
            self.statusBar().showMessage("Select a version to preview first.")
            return
        rec = self.cat["recordings"].get(ids[0])
        if not rec:
            return
        from .preview import PreviewDialog
        dlg = PreviewDialog(rec, rec["id"] in self.cat["downloaded"],
                            self.cookies_browser, self)
        dlg.exec()
        if dlg.queue_requested and rec["id"] not in self.cat["downloaded"]:
            if self.queue.has_job_for(rec["id"]):
                self.statusBar().showMessage("That one is already in the queue.")
                return
            self._enqueue(Job("download", rec["title"][:70],
                              {"rec": rec, "collection": str(self.collection_dir),
                               "quality": self.quality_box.currentData() or "best",
                               "cookies_browser": self.cookies_browser}))
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
        self.f_attention = QCheckBox("⚠ Needs attention only")
        self.f_attention.setToolTip("Shows missing a date or place, with "
                                    "unidentified tracks, or not split yet.")
        self.f_attention.toggled.connect(self.refresh_collection)
        llay.addWidget(self.f_attention)
        self.show_list = QListWidget()
        self.show_list.currentRowChanged.connect(self.show_details)
        llay.addWidget(self.show_list, 1)
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self.refresh_collection)
        llay.addWidget(b_refresh)
        b_verify_all = QPushButton("Verify whole collection")
        b_verify_all.setToolTip("Check every downloaded show against its "
                                "recorded checksums.")
        b_verify_all.clicked.connect(self.verify_all)
        llay.addWidget(b_verify_all)
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
        b_timeline = QPushButton("Timeline editor ...")
        b_timeline.setToolTip("See the whole show as a waveform and place the "
                              "song cuts by eye/ear — for when automation "
                              "can't work out the tracklist.")
        b_timeline.clicked.connect(self.open_timeline)
        b_resolve = QPushButton("Find details in page text ...")
        b_resolve.setToolTip("Read the description and comments yourself and "
                             "point the archiver at the date, place, or a "
                             "timestamped tracklist it missed.")
        b_resolve.clicked.connect(self.resolve_text)
        b_edit = QPushButton("Edit date/venue")
        b_edit.clicked.connect(self.edit_show)
        b_verify = QPushButton("Verify files")
        b_verify.setToolTip("Re-check the downloaded files against the "
                            "checksums recorded when they arrived, catching "
                            "corruption and truncated downloads.")
        b_verify.clicked.connect(self.queue_verify)
        b_record = QPushButton("Record checksums")
        b_record.setToolTip("Store checksums for a show downloaded before "
                            "this feature existed, so it can be verified "
                            "from now on.")
        b_record.clicked.connect(self.record_checksums_for_show)
        b_open = QPushButton("Open folder")
        b_open.clicked.connect(self.open_show_folder)
        for b in (self.b_split, b_resplit, b_timeline, b_resolve, b_verify,
                  b_record, b_edit, b_open):
            btns.addWidget(b)
        btns.addStretch(1)
        rlay.addLayout(btns)

        split.addWidget(right)
        split.setSizes([340, 820])
        outer.addWidget(split)
        return w

    def refresh_collection(self):
        all_shows = scan_collection(self.collection_dir)
        if getattr(self, "f_attention", None) and self.f_attention.isChecked():
            all_shows = [s for s in all_shows if s["issues"] or not s["tracks"]]
        self.shows = all_shows
        multi_artist = len({s["artist"] for s in all_shows}) > 1
        row = self.show_list.currentRow()
        self.show_list.clear()
        for s in self.shows:
            n_tracks = len(s["tracks"])
            mark = "🎵" if n_tracks else ("🎚" if s["has_master"] else "⬇")
            if s["issues"]:
                mark = "⚠ " + mark
            name = f"{s['artist']} — {s['dir'].name}" if multi_artist else s["dir"].name
            item = QListWidgetItem(f"{mark} {name}")
            state = (f"{n_tracks} split tracks" if n_tracks else
                     "audio extracted, not split" if s["has_master"] else
                     "downloaded, audio not extracted yet")
            if s["issues"]:
                state += "\nNeeds attention: " + ", ".join(s["issues"])
            item.setToolTip(state)
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
        meta.append(f"<b>Artist:</b> {s['artist']}")
        meta.append(f"<b>Date:</b> {show.get('date') or '<i>unknown</i>'}")
        meta.append(f"<b>Place:</b> {show.get('venue') or '<i>unknown</i>'}")
        meta.append(f"<b>Source:</b> {m.get('source')} — "
                    f"<a href='{m.get('source_url')}'>{m.get('source_url')}</a>")
        if m.get("uploader"):
            meta.append(f"<b>Uploaded by:</b> {m['uploader']}")
        state = (f"{len(s['tracks'])} tracks split" if s["tracks"] else
                 "audio extracted, not split yet" if s["has_master"] else
                 "downloaded — audio not extracted yet")
        meta.append(f"<b>Status:</b> {state}")
        if s["issues"]:
            meta.append("<b style='color:#c0392b'>⚠ Needs attention:</b> "
                        + ", ".join(s["issues"])
                        + " — try “Find details in page text”")
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
        self._apply_show_values(s, dlg.values())

    def _report_verification(self, job, result):
        """A verification that ran is not a failure, whatever it found."""
        from .integrity import OK, NO_RECORD
        name = job.label.replace("verify: ", "")
        if result["status"] == OK:
            self.statusBar().showMessage(f"{name}: all files intact.")
            return
        if result["status"] == NO_RECORD:
            # Normal for anything downloaded before checksums existed.
            self._unrecorded_shows = getattr(self, "_unrecorded_shows", 0) + 1
            self.statusBar().showMessage(
                f"{name}: no checksums on record — nothing to compare "
                f"against. Use “Record checksums” to start tracking it.")
            return
        QMessageBox.warning(
            self, "Verification found problems",
            f"{name}\n\n"
            + "\n".join(f"• {p}" for p in result["problems"])
            + "\n\nRe-download the show to repair it — the original is the "
              "copy that matters; the split audio can always be regenerated.")

    def record_checksums_for_show(self):
        s = self._current_show()
        if not s:
            return
        from .integrity import record_checksums
        try:
            data = record_checksums(s["dir"])
            self.statusBar().showMessage(
                f"Recorded checksums for {len(data['files'])} file(s) in "
                f"{s['dir'].name}.")
        except Exception as e:
            QMessageBox.warning(self, "Could not record checksums", str(e))

    def queue_verify(self):
        s = self._current_show()
        if not s:
            return
        self._enqueue(Job("verify", f"verify: {s['dir'].name[:60]}",
                          {"show_dir": str(s["dir"])}))

    def verify_all(self):
        shows = getattr(self, "shows", [])
        if not shows:
            return
        if QMessageBox.question(
                self, "Verify collection",
                f"Check all {len(shows)} downloaded show(s) against their "
                f"recorded checksums? This reads every file, so it can take a "
                f"while on a big collection.") != QMessageBox.Yes:
            return
        for sh in shows:
            self._enqueue(Job("verify", f"verify: {sh['dir'].name[:60]}",
                              {"show_dir": str(sh["dir"])}))

    def open_timeline(self):
        s = self._current_show()
        if not s:
            return
        if not s["has_master"]:
            r = QMessageBox.question(
                self, "Extract audio first",
                "The timeline needs the extracted audio master "
                "(audio/full.flac), which this show doesn't have yet.\n\n"
                "Queue the extraction now? Re-open the timeline once it "
                "finishes.")
            if r == QMessageBox.Yes:
                self._enqueue(Job("extract", f"extract: {s['dir'].name[:60]}",
                                  {"show_dir": str(s["dir"])}))
            return
        from .timeline import TimelineDialog
        dlg = TimelineDialog(s["dir"], self)
        if dlg.exec() == QDialog.Accepted:
            self.statusBar().showMessage(
                f"Tracklist saved ({len(dlg.segments())} tracks).")
            if dlg.split_requested:
                self.queue_split()
            self.refresh_collection()

    def resolve_text(self):
        s = self._current_show()
        if not s:
            return
        dlg = TextResolveDialog(s["manifest"], self)
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.tracks:
            audio_dir = s["dir"] / "audio"
            audio_dir.mkdir(exist_ok=True)
            (audio_dir / "tracks.json").write_text(
                json.dumps(dlg.tracks, indent=2, ensure_ascii=False))
            self.statusBar().showMessage(
                f"Saved {len(dlg.tracks)}-track tracklist — now run "
                f"“Extract & split tracks”.")
        self._apply_show_values(s, dlg.values())

    def _apply_show_values(self, s: dict, values: dict):
        s["manifest"]["show"] = values
        (s["dir"] / "show.json").write_text(
            json.dumps(s["manifest"], indent=2, ensure_ascii=False))
        rid = s["manifest"].get("recording_id")
        if rid and rid in self.cat["recordings"]:
            self.cat["recordings"][rid]["show"] = values
            self.save_catalog()
        self.refresh_collection()
        self.refresh_tree()

    def open_show_folder(self):
        s = self._current_show()
        if s:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(s["dir"].resolve())))

    # ---------------------------------------------------------- Artists tab

    def _build_artists_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "Every band in your catalog, and how far through each one you are. "
            "Removing a band clears it from the hub — files already on disk are "
            "left alone."))

        self.artist_tree = QTreeWidget()
        self.artist_tree.setHeaderLabels(
            ["Band / artist", "Shows found", "Downloaded", "Split",
             "Ignored", "Still to get", "Status"])
        self.artist_tree.setRootIsDecorated(False)
        self.artist_tree.setAlternatingRowColors(True)
        self.artist_tree.setColumnWidth(0, 280)
        self.artist_tree.itemDoubleClicked.connect(
            lambda *_: self.focus_selected_artist())
        lay.addWidget(self.artist_tree, 1)

        btns = QHBoxLayout()
        b_focus = QPushButton("Show in Get Music")
        b_focus.setToolTip("Filter the Get Music list down to this band.")
        b_focus.clicked.connect(self.focus_selected_artist)
        b_search = QPushButton("Search again")
        b_search.setToolTip("Look for new uploads for this band.")
        b_search.clicked.connect(self.search_selected_artist)
        self.b_done = QPushButton("Mark as done")
        self.b_done.setToolTip("Flag a band you've finished collecting. "
                               "Purely a marker — nothing is deleted.")
        self.b_done.clicked.connect(self.toggle_selected_artist_done)
        b_remove = QPushButton("🗑 Remove from hub")
        b_remove.setToolTip("Forget this band and all its catalogued "
                            "recordings. Downloaded files stay on disk.")
        b_remove.clicked.connect(self.remove_selected_artist)
        for b in (b_focus, b_search, self.b_done):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(b_remove)
        lay.addLayout(btns)
        return w

    def _selected_artist(self) -> str | None:
        items = self.artist_tree.selectedItems()
        return items[0].data(0, ARTIST_ROLE) if items else None

    def refresh_artists(self):
        if not hasattr(self, "artist_tree"):
            return
        previous = self._selected_artist()
        self.artist_tree.clear()
        for st in cat_mod.artist_stats(self.cat, getattr(self, "shows", [])):
            if st["marked_done"]:
                status = "✓ done (marked)"
            elif st["complete"]:
                status = "✓ all downloaded"
            elif st["downloaded"]:
                status = f"in progress"
            else:
                status = "nothing downloaded yet"
            item = QTreeWidgetItem([
                st["artist"], str(st["found"]), str(st["downloaded"]),
                str(st["split"]), str(st["ignored"]), str(st["remaining"]),
                status])
            item.setData(0, ARTIST_ROLE, st["artist"])
            self.artist_tree.addTopLevelItem(item)
            if st["artist"] == previous:
                item.setSelected(True)
        self._sync_artist_filter()

    def focus_selected_artist(self):
        artist = self._selected_artist()
        if not artist:
            return
        idx = self.artist_filter.findData(artist)
        if idx >= 0:
            self.artist_filter.setCurrentIndex(idx)
        self.centralWidget().setCurrentIndex(0)

    def search_selected_artist(self):
        artist = self._selected_artist()
        if not artist:
            return
        self.artist_edit.setText(artist)
        self.centralWidget().setCurrentIndex(0)
        self.start_search()

    def toggle_selected_artist_done(self):
        artist = self._selected_artist()
        if not artist:
            return
        done = bool(self.cat.get("finished_artists", {}).get(artist))
        cat_mod.set_artist_done(self.cat, artist, not done)
        self.save_catalog()
        self.refresh_artists()

    def remove_selected_artist(self):
        artist = self._selected_artist()
        if not artist:
            self.statusBar().showMessage("Select a band first.")
            return
        n = sum(1 for r in self.cat["recordings"].values()
                if (r.get("artist") or "Unknown") == artist)
        have = sum(1 for rid, r in self.cat["recordings"].items()
                   if (r.get("artist") or "Unknown") == artist
                   and rid in self.cat["downloaded"])
        note = (f"\n\n{have} of them are downloaded — those files stay on disk "
                f"in your collection folder, and will reappear in the "
                f"Collection tab." if have else "")
        if QMessageBox.question(
                self, "Remove band from hub",
                f"Remove “{artist}” and its {n} catalogued recording(s) from "
                f"the hub?{note}") != QMessageBox.Yes:
            return
        removed = cat_mod.remove_artist(self.cat, artist)
        self.save_catalog()
        self.statusBar().showMessage(
            f"Removed “{artist}” ({removed} recordings) from the hub.")
        self.refresh_artists()
        self.refresh_tree()

    def _sync_artist_filter(self):
        """Keep the Get Music artist filter in step with the catalog."""
        if not hasattr(self, "artist_filter"):
            return
        current = self.artist_filter.currentData()
        self.artist_filter.blockSignals(True)
        self.artist_filter.clear()
        self.artist_filter.addItem("All bands", None)
        for a in sorted(cat_mod.artists_in(self.cat), key=str.lower):
            if a:
                self.artist_filter.addItem(a, a)
        idx = self.artist_filter.findData(current)
        self.artist_filter.setCurrentIndex(max(idx, 0))
        self.artist_filter.blockSignals(False)

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

        self.s_quality = QComboBox()
        for label, key in QUALITY_CHOICES:
            self.s_quality.addItem(label, key)
        qi = self.s_quality.findData(self.settings.value("quality", "best"))
        self.s_quality.setCurrentIndex(max(qi, 0))
        self.s_quality.setToolTip("Used for newly queued downloads. Individual "
                                  "items can be changed in the queue.")
        form.addRow("Default download quality", self.s_quality)

        self.s_cookies = QComboBox()
        for label, key in COOKIE_BROWSERS:
            self.s_cookies.addItem(label, key)
        ci = self.s_cookies.findData(self.cookies_browser)
        self.s_cookies.setCurrentIndex(max(ci, 0))
        self.s_cookies.setToolTip(
            "If YouTube says “sign in to confirm you're not a bot”, pick the "
            "browser you watch YouTube in. Downloads then reuse that browser's "
            "sign-in. Nothing is uploaded anywhere.")
        form.addRow("Use cookies from browser", self.s_cookies)

        save = QPushButton("Save settings")
        save.clicked.connect(self._save_settings)
        form.addRow("", save)

        problem = check_ffmpeg()
        ff = QLabel(problem if problem
                    else "✓ ffmpeg found — audio extraction and splitting "
                         "are available.")
        ff.setWordWrap(True)
        if problem:
            ff.setStyleSheet("color:#c0392b;")
        form.addRow("ffmpeg", ff)

        js_problem = check_js_runtime()
        js = QLabel(js_problem if js_problem
                    else f"✓ JavaScript runtime found "
                         f"({Path(find_js_runtime()).name}) — YouTube "
                         f"extraction is fully supported.")
        js.setWordWrap(True)
        if js_problem:
            js.setStyleSheet("color:#b8860b;")
        form.addRow("JavaScript runtime", js)
        form.addRow("", QLabel(
            "Keep yt-dlp up to date — YouTube changes regularly and downloads "
            "start failing when it goes stale.\n"
            "Windows: run update.bat.   Otherwise: pip install -U yt-dlp"))
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
        quality = self.s_quality.currentData() or "best"
        self.settings.setValue("quality", quality)
        self.settings.setValue("cookies_browser",
                               self.s_cookies.currentData() or "")
        idx = self.quality_box.findData(quality)
        if idx >= 0:
            self.quality_box.setCurrentIndex(idx)
        self.statusBar().showMessage("Settings saved.")
        self.refresh_collection()
        self.refresh_artists()

    # ---------------------------------------------------------- job plumbing

    def _enqueue(self, job: Job):
        self.queue.submit(job)
        self.statusBar().showMessage(f"Queued: {job.label}")

    STATUS_ICON = {QUEUED: "⏳", RUNNING: "▶", DONE: "✓",
                   FAILED: "✗", CANCELLED: "⊘"}

    def refresh_queue(self):
        """Redraw the queue list from the worker's authoritative state."""
        selected = {i.data(0, JOB_ROLE) for i in self.queue_tree.selectedItems()}
        self.queue_tree.clear()
        jobs = self.queue.jobs()
        for j in jobs:
            pct = (f"{int(j.progress * 100)}%"
                   if j.status == RUNNING and j.progress >= 0
                   else "100%" if j.status == DONE else "")
            quality = (QUALITY_SHORT.get(j.quality, j.quality)
                       if j.kind == "download" else "—")
            status = j.status if not j.message else f"{j.status} — {j.message}"
            item = QTreeWidgetItem([self.STATUS_ICON.get(j.status, ""),
                                    j.label, quality, pct, status])
            item.setData(0, JOB_ROLE, j.id)
            item.setToolTip(4, j.message or j.status)
            self.queue_tree.addTopLevelItem(item)
            if j.id in selected:
                item.setSelected(True)

        paused = self.queue.is_paused()
        self.pause_btn.setText("▶ Resume queue" if paused else "⏸ Pause queue")
        running = any(j.status == RUNNING for j in jobs)
        waiting = sum(1 for j in jobs if j.status == QUEUED)
        if paused:
            self.progress_label.setText(
                f"queue paused — {waiting} waiting" if not running
                else f"queue paused — finishing current download, "
                     f"{waiting} waiting")
        elif not running and not waiting:
            self.progress_label.setText("idle")
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)

    def _selected_job_ids(self) -> list[str]:
        return [i.data(0, JOB_ROLE) for i in self.queue_tree.selectedItems()]

    def toggle_queue_paused(self):
        self.queue.set_paused(not self.queue.is_paused())

    def queue_cancel_selected(self):
        for jid in self._selected_job_ids():
            self.queue.cancel(jid)

    def queue_remove_selected(self):
        for jid in self._selected_job_ids():
            self.queue.remove(jid)

    def queue_retry_selected(self):
        for jid in self._selected_job_ids():
            self.queue.retry(jid)

    def queue_move_selected(self, delta: int):
        ids = self._selected_job_ids()
        # move in the direction of travel so a multi-selection keeps its order
        for jid in (ids if delta < 0 else reversed(ids)):
            self.queue.move(jid, delta)

    def queue_clear_finished(self):
        n = self.queue.clear_finished()
        self.statusBar().showMessage(f"Cleared {n} finished item(s).")

    def queue_clear_all(self):
        jobs = self.queue.jobs()
        if not jobs:
            return
        running = any(j.status == RUNNING for j in jobs)
        msg = ("Clear the whole queue?" if not running else
               "Clear the whole queue and cancel the download in progress?")
        if QMessageBox.question(self, "Clear queue", msg) != QMessageBox.Yes:
            return
        self.queue.clear_all()

    def _queue_menu(self, pos):
        item = self.queue_tree.itemAt(pos)
        if not item:
            return
        jid = item.data(0, JOB_ROLE)
        job = next((j for j in self.queue.jobs() if j.id == jid), None)
        if job is None:
            return
        menu = QMenu(self)
        if job.status == QUEUED and job.kind == "download":
            sub = menu.addMenu("Change quality")
            for label, key in QUALITY_CHOICES:
                act = sub.addAction(label)
                act.setCheckable(True)
                act.setChecked(job.quality == key)
                act.triggered.connect(
                    lambda _=False, k=key, i=jid: self.queue.set_quality(i, k))
        if job.status in (QUEUED, RUNNING):
            menu.addAction("Cancel", lambda: self.queue.cancel(jid))
        if job.is_finished:
            menu.addAction("Retry", lambda: self.queue.retry(jid))
        menu.addAction("Remove from list", lambda: self.queue.remove(jid))
        if job.kind == "download":
            rec = job.payload.get("rec") or {}
            if rec.get("url"):
                menu.addSeparator()
                menu.addAction("Open source page", lambda: QDesktopServices.openUrl(
                    QUrl(rec["url"])))
        menu.exec(self.queue_tree.viewport().mapToGlobal(pos))

    def on_job_progress(self, jid, frac, msg):
        if frac < 0:
            self.progress.setRange(0, 0)  # indeterminate
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(frac * 1000))
        self.progress_label.setText(msg)
        # keep the row's percentage column live without a full rebuild
        for i in range(self.queue_tree.topLevelItemCount()):
            it = self.queue_tree.topLevelItem(i)
            if it.data(0, JOB_ROLE) == jid:
                it.setText(3, f"{int(frac * 100)}%" if frac >= 0 else "")
                it.setText(4, msg or RUNNING)
                break

    def on_job_finished(self, jid, ok, msg, result):
        job = next((j for j in self.queue.jobs() if j.id == jid), None)
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000 if ok else 0)
        self.progress_label.setText("idle")
        if job:
            if ok and job.kind == "download":
                self.cat["downloaded"][job.payload["rec"]["id"]] = result["dest"]
                self.save_catalog()
            if job.kind == "verify" and result:
                self._report_verification(job, result)
            elif not ok and job.status != CANCELLED:
                QMessageBox.warning(self, "Job failed",
                                    f"{job.label}\n\n{msg}")
            elif ok and result and result.get("warnings"):
                QMessageBox.information(
                    self, "Tracklist didn't match the audio",
                    "The tracklist disagreed with the actual recording:\n\n"
                    + "\n".join(f"• {w}" for w in result["warnings"])
                    + "\n\nThe cuts that made sense were written. Use the "
                      "timeline editor to place the rest by hand.")
        self.statusBar().showMessage(msg)
        self.refresh_tree()
        self.refresh_collection()
        self.refresh_artists()

    # -------------------------------------------------------------- helpers

    def save_catalog(self):
        cat_mod.save(self.cat, self.catalog_path)

    def closeEvent(self, event):
        busy = [j for j in self.queue.jobs() if j.status in (RUNNING, QUEUED)]
        if busy:
            r = QMessageBox.question(
                self, "Quit?",
                f"{len(busy)} download/split job(s) are still queued or "
                f"running — quit anyway?")
            if r != QMessageBox.Yes:
                event.ignore()
                return
        self.queue.shutdown()
        self.queue.wait(2000)
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
    app.setApplicationName("Live Show Archiver")
    win = MainWindow()
    win.show()
    return app.exec()


def main_standalone():
    sys.exit(run())


if __name__ == "__main__":
    main_standalone()
