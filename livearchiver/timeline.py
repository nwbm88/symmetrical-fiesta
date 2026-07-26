"""Waveform timeline editor — the manual fallback when automation fails.

Shows the whole concert as a waveform (Audacity/Premiere style).  The user
places cut markers between songs, drags them until they sit right, names
each segment, and saves — which writes the same ``audio/tracks.json`` the
splitter uses, so "Extract & split" then cuts exactly what was drawn.

Controls:
    double-click        add a cut marker
    drag a marker       move it
    right-click marker  delete it
    click               seek (plays from there when playback is available)
    Ctrl+wheel          zoom around the cursor
    wheel / scrollbar   pan
"""

from __future__ import annotations

import array
import bisect
import json
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollBar,
    QTreeWidget, QTreeWidgetItem, QWidget, QMessageBox, QDialogButtonBox,
    QCheckBox,
)

PEAK_RATE = 25          # envelope buckets per second (40 ms resolution)
DECODE_RATE = 4000      # Hz mono used for peak extraction

WATCHDOG_MS = 2500      # how long to watch new playback before judging it
MIN_PLAYBACK_RATE = 0.33  # fraction of real time below which it is broken


# ------------------------------------------------------------------ workers

class PeakLoader(QThread):
    """Decode the master with ffmpeg and build a min/max envelope."""
    ready = Signal(object, float)   # peaks: list[(mn, mx)] floats -1..1, duration
    failed = Signal(str)

    def __init__(self, media: Path, parent=None):
        super().__init__(parent)
        self.media = media

    def run(self):
        cache = self.media.parent / "peaks.json"
        try:
            if cache.exists() and cache.stat().st_mtime >= self.media.stat().st_mtime:
                data = json.loads(cache.read_text())
                if data.get("rate") == PEAK_RATE and data.get("peaks"):
                    peaks = [tuple(p) for p in data["peaks"]]
                    self.ready.emit(peaks, len(peaks) / PEAK_RATE)
                    return
        except Exception:
            pass  # cache unreadable — rebuild it

        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-v", "error", "-i", str(self.media),
                 "-ac", "1", "-ar", str(DECODE_RATE), "-f", "s16le", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            bucket = DECODE_RATE // PEAK_RATE
            peaks: list[tuple[float, float]] = []
            carry = b""
            mn, mx, n = 0, 0, 0
            while True:
                chunk = proc.stdout.read(1 << 16)
                if not chunk:
                    break
                buf = carry + chunk
                usable = len(buf) - (len(buf) % 2)
                carry = buf[usable:]
                samples = array.array("h", buf[:usable])
                for s in samples:
                    if s < mn:
                        mn = s
                    if s > mx:
                        mx = s
                    n += 1
                    if n == bucket:
                        peaks.append((mn / 32768.0, mx / 32768.0))
                        mn, mx, n = 0, 0, 0
            if n:
                peaks.append((mn / 32768.0, mx / 32768.0))
            err = proc.stderr.read().decode(errors="replace")
            proc.wait()
            if not peaks:
                self.failed.emit(f"could not decode audio:\n{err[-500:]}")
                return
            try:
                cache.write_text(json.dumps(
                    {"rate": PEAK_RATE,
                     "peaks": [[round(a, 3), round(b, 3)] for a, b in peaks]}))
            except Exception:
                pass
            self.ready.emit(peaks, len(peaks) / PEAK_RATE)
        except Exception as e:
            self.failed.emit(str(e))


class SilenceWorker(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, media: Path, parent=None):
        super().__init__(parent)
        self.media = media

    def run(self):
        try:
            from .audio import detect_silences
            self.done.emit(detect_silences(self.media))
        except Exception as e:
            self.failed.emit(str(e))


# ------------------------------------------------------------- the waveform

RULER_H = 20
LABEL_H = 16


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 \
        else f"{s // 60}:{s % 60:02d}"


class WaveformView(QWidget):
    """Custom-painted waveform with draggable cut markers."""

    requestAdd = Signal(float)      # seconds — user double-clicked
    requestRemove = Signal(int)     # marker index — user right-clicked
    markerMoved = Signal(int, float)
    seeked = Signal(float)          # user clicked to seek
    viewChanged = Signal()          # zoom/offset changed (sync scrollbar)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.peaks: list[tuple[float, float]] = []
        self.duration = 0.0
        self.markers: list[float] = []
        self.labels: list[str] = []
        self.playhead: float | None = None
        self.zoom = 1.0             # px per second
        self.offset = 0.0           # seconds at left edge
        self.gain = 1.0             # vertical scale (see set_audio)
        self.normalize = True
        self._drag_idx: int | None = None

    # ------------------------------------------------------------- geometry

    def set_audio(self, peaks, duration):
        self.peaks, self.duration = peaks, duration
        # Audience recordings are often very quiet; drawn at true scale they
        # are a flat line you can't read song boundaries off.  Scale the
        # display up to the loudest peak (Audacity's vertical zoom).
        loudest = max((max(abs(a), abs(b)) for a, b in peaks), default=1.0)
        self.gain = min(1.0 / loudest, 25.0) if loudest > 0.001 else 1.0
        self.zoom_fit()

    def set_normalize(self, on: bool):
        self.normalize = on
        self.update()

    def set_markers(self, markers, labels):
        self.markers = list(markers)
        self.labels = list(labels)
        self.update()

    def set_playhead(self, seconds):
        self.playhead = seconds
        if seconds is not None and self.zoom > 0:
            right = self.offset + self.width() / self.zoom
            if seconds > right or seconds < self.offset:
                self.offset = max(0.0, min(seconds,
                                           self.duration - self.width() / self.zoom))
                self.viewChanged.emit()
        self.update()

    def visible_seconds(self) -> float:
        return self.width() / self.zoom if self.zoom else 0

    def zoom_fit(self):
        if self.duration:
            self.zoom = max(self.width(), 100) / self.duration
            self.offset = 0.0
            self.viewChanged.emit()
            self.update()

    def zoom_by(self, factor, around_x=None):
        if not self.duration:
            return
        around_x = self.width() / 2 if around_x is None else around_x
        t = self.offset + around_x / self.zoom
        min_zoom = max(self.width(), 100) / self.duration
        self.zoom = min(max(self.zoom * factor, min_zoom), 200.0)
        self.offset = max(0.0, min(t - around_x / self.zoom,
                                   self.duration - self.visible_seconds()))
        self.viewChanged.emit()
        self.update()

    def set_offset(self, seconds):
        self.offset = max(0.0, min(seconds, self.duration - self.visible_seconds()))
        self.viewChanged.emit()
        self.update()

    def _x_to_t(self, x) -> float:
        return max(0.0, min(self.offset + x / self.zoom, self.duration))

    def _t_to_x(self, t) -> float:
        return (t - self.offset) * self.zoom

    def _marker_at(self, x) -> int | None:
        for i, m in enumerate(self.markers):
            if abs(self._t_to_x(m) - x) <= 5:
                return i
        return None

    # ------------------------------------------------------------- painting

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        wave_top = RULER_H + LABEL_H
        wave_h = h - wave_top
        mid = wave_top + wave_h / 2

        p.fillRect(0, 0, w, h, QColor("#1b1e24"))
        p.fillRect(0, 0, w, RULER_H, QColor("#252a33"))

        if not self.peaks:
            p.setPen(QColor("#8a93a5"))
            p.drawText(self.rect(), Qt.AlignCenter, "loading waveform ...")
            return

        # ruler
        p.setPen(QColor("#8a93a5"))
        step = next((s for s in (1, 2, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)
                     if s * self.zoom >= 70), 3600)
        t = int(self.offset // step) * step
        while t <= self.offset + self.visible_seconds():
            x = self._t_to_x(t)
            p.drawLine(int(x), RULER_H - 5, int(x), RULER_H)
            p.drawText(int(x) + 3, RULER_H - 6, _hms(t))
            t += step

        # waveform
        p.setPen(QPen(QColor("#4fa3e3"), 1))
        n = len(self.peaks)
        gain = self.gain if self.normalize else 1.0
        for x in range(w):
            b0 = int((self.offset + x / self.zoom) * PEAK_RATE)
            b1 = max(b0 + 1, int((self.offset + (x + 1) / self.zoom) * PEAK_RATE))
            if b0 >= n:
                break
            mn = max(-1.0, min(pk[0] for pk in self.peaks[b0:min(b1, n)]) * gain)
            mx = min(1.0, max(pk[1] for pk in self.peaks[b0:min(b1, n)]) * gain)
            p.drawLine(x, int(mid + mn * wave_h * 0.48),
                       x, int(mid + mx * wave_h * 0.48))
        p.setPen(QColor("#2f3540"))
        p.drawLine(0, int(mid), w, int(mid))

        # segment labels
        bounds = [0.0] + self.markers + [self.duration]
        p.setPen(QColor("#d7dce5"))
        for i in range(len(bounds) - 1):
            x0, x1 = self._t_to_x(bounds[i]), self._t_to_x(bounds[i + 1])
            if x1 < 0 or x0 > w:
                continue
            label = self.labels[i] if i < len(self.labels) and self.labels[i] \
                else f"Track {i + 1:02d}"
            rect = QRectF(max(x0, 0) + 4, RULER_H, max(x1, 0) - max(x0, 0) - 8, LABEL_H)
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(label, Qt.ElideRight,
                                                  int(rect.width())))

        # markers
        for i, m in enumerate(self.markers):
            x = self._t_to_x(m)
            if x < -5 or x > w + 5:
                continue
            color = QColor("#ffb84d") if i == self._drag_idx else QColor("#e5534b")
            p.setPen(QPen(color, 2))
            p.drawLine(int(x), RULER_H, int(x), h)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([QPointF(x - 5, RULER_H),
                                     QPointF(x + 5, RULER_H),
                                     QPointF(x, RULER_H + 7)]))

        # playhead
        if self.playhead is not None:
            x = self._t_to_x(self.playhead)
            if 0 <= x <= w:
                p.setPen(QPen(QColor("#f5d90a"), 1))
                p.drawLine(int(x), RULER_H, int(x), h)
        p.end()

    # ---------------------------------------------------------------- input

    def mousePressEvent(self, ev):
        x = ev.position().x()
        if ev.button() == Qt.LeftButton:
            idx = self._marker_at(x)
            if idx is not None:
                self._drag_idx = idx
            else:
                self.seeked.emit(self._x_to_t(x))
            self.update()
        elif ev.button() == Qt.RightButton:
            idx = self._marker_at(x)
            if idx is not None:
                self.requestRemove.emit(idx)

    def mouseMoveEvent(self, ev):
        x = ev.position().x()
        if self._drag_idx is not None:
            i = self._drag_idx
            lo = self.markers[i - 1] + 0.2 if i > 0 else 0.0
            hi = self.markers[i + 1] - 0.2 if i + 1 < len(self.markers) \
                else self.duration
            self.markers[i] = max(lo, min(self._x_to_t(x), hi))
            self.markerMoved.emit(i, self.markers[i])
            self.update()
        else:
            self.setCursor(Qt.SizeHorCursor if self._marker_at(x) is not None
                           else Qt.ArrowCursor)

    def mouseReleaseEvent(self, ev):
        self._drag_idx = None
        self.update()

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.requestAdd.emit(self._x_to_t(ev.position().x()))

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.25 if delta > 0 else 0.8, ev.position().x())
        else:
            self.set_offset(self.offset - delta / self.zoom)

    def resizeEvent(self, ev):
        min_zoom = max(self.width(), 100) / self.duration if self.duration else 1
        if self.zoom < min_zoom:
            self.zoom = min_zoom
        self.viewChanged.emit()
        super().resizeEvent(ev)


# ------------------------------------------------------------------- dialog

class TimelineDialog(QDialog):
    """Full-show waveform with editable cut markers and track names."""

    def __init__(self, show_dir: Path, parent=None):
        super().__init__(parent)
        self.show_dir = show_dir
        self.master = show_dir / "audio" / "full.flac"
        self.setWindowTitle(f"Timeline — {show_dir.name}")
        self.resize(1150, 720)
        self.split_requested = False
        self._updating_table = False

        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        self.time_label = QLabel("0:00 / 0:00")
        b_in = QPushButton("Zoom in")
        b_in.clicked.connect(lambda: self.view.zoom_by(1.5))
        b_out = QPushButton("Zoom out")
        b_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.5))
        b_fit = QPushButton("Fit")
        b_fit.clicked.connect(self.view_fit)
        self.b_detect = QPushButton("Detect silences")
        self.b_detect.setToolTip("Add candidate markers at every quiet gap — "
                                 "then drag/delete them until they sit right.")
        self.b_detect.clicked.connect(self.detect_silences)
        b_clear = QPushButton("Clear markers")
        b_clear.clicked.connect(self.clear_markers)
        self.normalize_cb = QCheckBox("Boost quiet audio")
        self.normalize_cb.setChecked(True)
        self.normalize_cb.setToolTip("Scale the waveform up to its loudest "
                                     "peak so quiet audience recordings are "
                                     "still readable (display only — the "
                                     "audio itself is untouched).")
        self.normalize_cb.toggled.connect(
            lambda on: self.view.set_normalize(on))
        for x in (self.play_btn, self.time_label, b_in, b_out, b_fit,
                  self.b_detect, b_clear, self.normalize_cb):
            bar.addWidget(x)
        bar.addStretch(1)
        hint = QLabel("double-click: add cut · drag: move · right-click: delete "
                      "· Ctrl+wheel: zoom")
        hint.setStyleSheet("color: #888;")
        bar.addWidget(hint)
        lay.addLayout(bar)

        self.view = WaveformView()
        self.view.requestAdd.connect(self.add_marker)
        self.view.requestRemove.connect(self.remove_marker)
        self.view.markerMoved.connect(self.marker_moved)
        self.view.seeked.connect(self.seek)
        self.view.viewChanged.connect(self._sync_scroll)
        lay.addWidget(self.view, 2)

        self.scroll = QScrollBar(Qt.Horizontal)
        self.scroll.valueChanged.connect(self._scrolled)
        lay.addWidget(self.scroll)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(["#", "Song title (double-click to edit)",
                                    "Start", "End", "Length"])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 520)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._title_edited)
        self.table.itemSelectionChanged.connect(self._row_selected)
        lay.addWidget(self.table, 1)

        bb = QDialogButtonBox()
        b_save = bb.addButton("Save tracklist", QDialogButtonBox.AcceptRole)
        b_save_split = bb.addButton("Save && queue split",
                                    QDialogButtonBox.AcceptRole)
        bb.addButton(QDialogButtonBox.Cancel)
        b_save_split.clicked.connect(lambda: setattr(self, "split_requested", True))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        # state
        self.markers: list[float] = []
        self.titles: list[str] = [""]
        self.duration = 0.0
        self._silence_worker = None
        self._load_existing()

        self.loader = PeakLoader(self.master, self)
        self.loader.ready.connect(self._audio_ready)
        self.loader.failed.connect(self._audio_failed)
        self.loader.start()

        self._setup_playback()

    # -------------------------------------------------------------- loading

    def _load_existing(self):
        tj = self.show_dir / "audio" / "tracks.json"
        if tj.exists():
            try:
                tracks = json.loads(tj.read_text())
                self.markers = [float(t["start"]) for t in tracks[1:]
                                if t.get("start")]
                self.titles = [t.get("title") or "" for t in tracks]
            except Exception:
                pass

    def _audio_ready(self, peaks, duration):
        # The envelope's bucket count only approximates the length; ask
        # ffprobe so saved cut points line up with what ffmpeg will see.
        try:
            from .audio import duration_of
            duration = duration_of(self.master)
        except Exception:
            pass
        self.duration = duration
        if self.player is not None:
            # created before the length was known (ffplay needs it to know
            # where the end of the show is)
            self.player.duration = duration
        self.view.set_audio(peaks, duration)
        self.markers = [m for m in self.markers if m < duration]
        self.titles = self.titles[:len(self.markers) + 1] or [""]
        self._push_state()
        self.time_label.setText(f"0:00 / {_hms(duration)}")

    def _audio_failed(self, msg):
        QMessageBox.critical(self, "Waveform failed", msg)

    # ------------------------------------------------------------- playback

    def _setup_playback(self, prefer_ffplay=False):
        from .playback import create_player
        self._last_pos = getattr(self, "_last_pos", 0.0)
        self._play_from = getattr(self, "_play_from", 0.0)
        self._tried_fallback = prefer_ffplay
        self.player, reason = create_player(self.master, self.duration, self,
                                            prefer_ffplay=prefer_ffplay)
        # Qt can claim to be playing while nothing actually comes out and the
        # position never moves (no working backend for the format, an audio
        # server that accepts but discards, a device grabbed exclusively by
        # another app).  Watch the first couple of seconds and switch to
        # ffplay if the playhead never budges.
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.setInterval(WATCHDOG_MS)
        self._watchdog.timeout.connect(self._check_stalled)

        if self.player:
            self.player.positionChanged.connect(self._pos_changed)
            self.player.stateChanged.connect(self._play_state_changed)
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip(
                "Playing through ffplay." if self.player.backend == "ffplay"
                else "")
        else:
            self.play_btn.setEnabled(False)
            self.play_btn.setToolTip(reason)

    def _check_stalled(self):
        if (not self.player or self._tried_fallback
                or not self.player.is_playing()):
            return
        # Healthy playback advances roughly in step with the wall clock.
        # Anything slower than a third of real time is broken, not merely
        # slow — that covers a frozen playhead and a crawling one alike,
        # while leaving plenty of headroom for ordinary jitter.
        advanced = self._last_pos - self._play_from
        if advanced >= MIN_PLAYBACK_RATE * WATCHDOG_MS / 1000.0:
            return          # playing fine
        from .playback import ffplay_available
        if not ffplay_available():
            return
        resume_at = self._play_from
        self.player.stop()
        self.player.deleteLater()
        self._setup_playback(prefer_ffplay=True)
        if self.player:
            self.player.duration = self.duration
            self.player.set_position(resume_at)
            self._play_from = resume_at
            self.player.play()

    def _play_state_changed(self, playing: bool):
        self.play_btn.setText("⏸ Pause" if playing else "▶ Play")

    def _toggle_play(self):
        if not self.player:
            return
        if self.player.is_playing():
            self.player.pause()
            self._watchdog.stop()
        else:
            self._play_from = self._last_pos
            self.player.play()
            if self.player.backend == "Qt" and not self._tried_fallback:
                self._watchdog.start()

    def seek(self, seconds):
        if self.player:
            self.player.set_position(seconds)
        self._last_pos = self._play_from = seconds
        self.view.set_playhead(seconds)
        self.time_label.setText(f"{_hms(seconds)} / {_hms(self.duration)}")

    def _pos_changed(self, seconds):
        self._last_pos = seconds
        self.view.set_playhead(seconds)
        self.time_label.setText(f"{_hms(seconds)} / {_hms(self.duration)}")

    # ------------------------------------------------------- marker editing

    def add_marker(self, pos: float):
        k = bisect.bisect_left(self.markers, pos)
        self.markers.insert(k, pos)
        self.titles.insert(k + 1, "")
        self._push_state()

    def remove_marker(self, idx: int):
        self.markers.pop(idx)
        merged = self.titles[idx] or self.titles[idx + 1]
        self.titles[idx:idx + 2] = [merged]
        self._push_state()

    def marker_moved(self, idx: int, pos: float):
        self.markers[idx] = pos
        self._refresh_table()

    def clear_markers(self):
        self.markers = []
        self.titles = [self.titles[0] if self.titles else ""]
        self._push_state()

    def detect_silences(self):
        if self._silence_worker and self._silence_worker.isRunning():
            return
        self.b_detect.setEnabled(False)
        self.b_detect.setText("Detecting ...")
        self._silence_worker = SilenceWorker(self.master, self)
        self._silence_worker.done.connect(self._silences_done)
        self._silence_worker.failed.connect(self._silences_failed)
        self._silence_worker.start()

    def _silences_done(self, cuts):
        self.b_detect.setEnabled(True)
        self.b_detect.setText("Detect silences")
        cuts = [c for c in cuts if 1.0 < c < self.duration - 1.0]
        if not cuts:
            QMessageBox.information(
                self, "No silences found",
                "No quiet gaps detected — the crowd may be too loud. "
                "Place markers by hand (double-click on the waveform).")
            return
        old_titles = {round(m, 1): t
                      for m, t in zip(self.markers, self.titles[1:])}
        self.markers = sorted(cuts)
        self.titles = [self.titles[0]] + \
            [old_titles.get(round(m, 1), "") for m in self.markers]
        self._push_state()

    def _silences_failed(self, msg):
        self.b_detect.setEnabled(True)
        self.b_detect.setText("Detect silences")
        QMessageBox.warning(self, "Silence detection failed", msg)

    # ----------------------------------------------------------------- sync

    def _push_state(self):
        self.view.set_markers(self.markers, self.titles)
        self._refresh_table()

    def segments(self) -> list[dict]:
        bounds = [0.0] + self.markers + [self.duration]
        segs = []
        for i in range(len(bounds) - 1):
            if bounds[i + 1] - bounds[i] < 0.5:
                continue
            title = self.titles[i] if i < len(self.titles) and self.titles[i] \
                else f"Track {i + 1:02d}"
            segs.append({"title": title,
                         "start": round(bounds[i], 3),
                         "end": round(bounds[i + 1], 3)})
        return segs

    def _refresh_table(self):
        self._updating_table = True
        self.table.clear()
        for i, s in enumerate(self.segments(), 1):
            item = QTreeWidgetItem([f"{i:02d}", s["title"],
                                    _hms(s["start"]), _hms(s["end"]),
                                    _hms(s["end"] - s["start"])])
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self.table.addTopLevelItem(item)
        self._updating_table = False
        self.view.set_markers(self.markers, self.titles)

    def _title_edited(self, item, col):
        if self._updating_table or col != 1:
            return
        idx = self.table.indexOfTopLevelItem(item)
        if 0 <= idx < len(self.titles):
            self.titles[idx] = item.text(1).strip()
            self.view.set_markers(self.markers, self.titles)

    def _row_selected(self):
        items = self.table.selectedItems()
        if not items:
            return
        idx = self.table.indexOfTopLevelItem(items[0])
        segs = self.segments()
        if 0 <= idx < len(segs):
            self.seek(segs[idx]["start"])

    def _sync_scroll(self):
        self.scroll.blockSignals(True)
        vis = self.view.visible_seconds()
        self.scroll.setRange(0, max(0, int(self.duration - vis)))
        self.scroll.setPageStep(max(1, int(vis)))
        self.scroll.setValue(int(self.view.offset))
        self.scroll.blockSignals(False)

    def _scrolled(self, value):
        self.view.set_offset(float(value))

    def view_fit(self):
        self.view.zoom_fit()

    # ----------------------------------------------------------------- save

    def accept(self):
        segs = self.segments()
        if not segs:
            QMessageBox.warning(self, "Nothing to save",
                                "The timeline has no segments yet.")
            return
        out = self.show_dir / "audio" / "tracks.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(segs, indent=2, ensure_ascii=False))
        if self.player:
            self.player.stop()
        super().accept()

    def reject(self):
        if self.player:
            self.player.stop()
        super().reject()
