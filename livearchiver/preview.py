"""Preview a recording before committing to a multi-gigabyte download.

Shows the thumbnail and the full metadata straight away, and can stream the
video itself — yt-dlp resolves a direct media URL, which Qt plays without
anything being saved to disk.  Skipping through is the point: ten seconds at
three points in an hour-long upload tells you whether it is the whole show,
a phone recording from the back, or a re-upload of something you already
have.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal, QTimer
from PySide6.QtGui import QDesktopServices, QPixmap, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSlider,
    QDialogButtonBox, QWidget, QSizePolicy, QMessageBox,
)

import requests


def _hms(seconds) -> str:
    if not seconds:
        return "?"
    s = int(seconds)
    return (f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600
            else f"{s // 60}:{s % 60:02d}")


# ------------------------------------------------------------------ workers

class ThumbnailLoader(QThread):
    ready = Signal(bytes)
    failed = Signal(str)

    def __init__(self, rec: dict, parent=None):
        super().__init__(parent)
        self.rec = rec

    def run(self):
        for url in self._candidates():
            try:
                r = requests.get(url, timeout=15)
                if r.ok and r.content and len(r.content) > 1000:
                    self.ready.emit(r.content)
                    return
            except Exception:
                continue
        self.failed.emit("no thumbnail available")

    def _candidates(self) -> list[str]:
        rid = self.rec["id"]
        if self.rec["source"] == "youtube":
            vid = rid.split(":", 1)[1]
            return [f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg",
                    f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"]
        identifier = rid.split(":", 1)[1]
        return [f"https://archive.org/services/img/{identifier}"]


class StreamUrlResolver(QThread):
    """Ask yt-dlp for a directly playable URL (no download)."""
    ready = Signal(str, float)   # url, duration
    failed = Signal(str)

    def __init__(self, rec: dict, cookies_browser: str = "", parent=None):
        super().__init__(parent)
        self.rec = rec
        self.cookies_browser = cookies_browser

    def run(self):
        try:
            from yt_dlp import YoutubeDL
            # A single progressive stream plays far more reliably than a
            # separate video+audio pair, which Qt cannot mux on the fly.
            opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                    "format": "best[height<=720][acodec!=none][vcodec!=none]"
                              "/best[acodec!=none][vcodec!=none]/best"}
            if self.cookies_browser:
                opts["cookiesfrombrowser"] = (self.cookies_browser,)
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.rec["url"], download=False)
            url = info.get("url")
            if not url and info.get("requested_formats"):
                url = info["requested_formats"][0].get("url")
            if not url:
                self.failed.emit("no directly playable stream was offered")
                return
            self.ready.emit(url, float(info.get("duration") or 0))
        except Exception as e:
            from .platformsupport import is_bot_check
            msg = str(e)
            self.failed.emit("youtube-bot-check" if is_bot_check(msg) else msg)


# ------------------------------------------------------------------- dialog

class PreviewDialog(QDialog):
    """Thumbnail + metadata + optional in-app streaming with a scrub bar."""

    def __init__(self, rec: dict, already_downloaded: bool = False,
                 cookies_browser: str = "", parent=None):
        super().__init__(parent)
        self.rec = rec
        self.cookies_browser = cookies_browser
        self.queue_requested = False
        self._player = None
        self._video = None
        self._duration = float(rec.get("duration") or 0)
        self._seeking = False

        self.setWindowTitle("Preview")
        self.resize(880, 700)
        lay = QVBoxLayout(self)

        title = QLabel(rec.get("title") or "")
        f = QFont()
        f.setPointSize(12)
        f.setBold(True)
        title.setFont(f)
        title.setWordWrap(True)
        lay.addWidget(title)

        bits = [rec.get("source", ""), _hms(rec.get("duration"))]
        if rec.get("quality"):
            bits.append(rec["quality"])
        if rec.get("views"):
            bits.append(f"{rec['views']:,} views")
        if rec.get("uploader"):
            bits.append(f"by {rec['uploader']}")
        show = rec.get("show") or {}
        meta = QLabel(
            " · ".join(b for b in bits if b)
            + f"<br><b>Date:</b> {show.get('date') or 'unknown'}"
              f" &nbsp; <b>Place:</b> {show.get('venue') or 'unknown'}"
            + (f"<br><a href='{rec['url']}'>{rec['url']}</a>")
            + ("<br><b style='color:#2e7d32'>You already have this "
               "recording.</b>" if already_downloaded else ""))
        meta.setWordWrap(True)
        meta.setOpenExternalLinks(True)
        lay.addWidget(meta)

        # image / video area — the video widget replaces the still once
        # streaming starts
        self.stage = QWidget()
        self.stage.setMinimumHeight(360)
        self.stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stage_lay = QVBoxLayout(self.stage)
        self.stage_lay.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel("loading thumbnail ...")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:#111; color:#888;")
        self.stage_lay.addWidget(self.thumb)
        lay.addWidget(self.stage, 1)

        # transport
        row = QHBoxLayout()
        self.play_btn = QPushButton("▶ Stream preview")
        self.play_btn.setToolTip("Play the video without downloading it, so "
                                 "you can skip through and check what it is.")
        self.play_btn.clicked.connect(self.toggle_stream)
        self.pos_label = QLabel("0:00")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setEnabled(False)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.slider.sliderReleased.connect(self._slider_released)
        self.len_label = QLabel(_hms(self._duration))
        row.addWidget(self.play_btn)
        row.addWidget(self.pos_label)
        row.addWidget(self.slider, 1)
        row.addWidget(self.len_label)
        lay.addLayout(row)

        skips = QHBoxLayout()
        skips.addWidget(QLabel("Skip to:"))
        for label, frac in (("start", 0.0), ("10%", 0.10), ("25%", 0.25),
                            ("50%", 0.50), ("75%", 0.75), ("near end", 0.95)):
            b = QPushButton(label)
            b.setToolTip("Jump straight there — a fast way to check the whole "
                         "show is present and the sound holds up.")
            b.clicked.connect(lambda _=False, fr=frac: self.seek_fraction(fr))
            skips.addWidget(b)
        skips.addStretch(1)
        lay.addLayout(skips)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#888;")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        bb = QDialogButtonBox()
        b_queue = bb.addButton("⬇ Queue download", QDialogButtonBox.AcceptRole)
        b_queue.clicked.connect(lambda: setattr(self, "queue_requested", True))
        b_browser = bb.addButton("Open in browser", QDialogButtonBox.ActionRole)
        b_browser.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(rec["url"])))
        bb.addButton("Close", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._thumb_loader = ThumbnailLoader(rec, self)
        self._thumb_loader.ready.connect(self._thumb_ready)
        self._thumb_loader.failed.connect(
            lambda m: self.thumb.setText("no thumbnail available"))
        self._thumb_loader.start()
        self._resolver = None

    # ------------------------------------------------------------ thumbnail

    def _thumb_ready(self, data: bytes):
        pix = QPixmap()
        if pix.loadFromData(data):
            self._pixmap = pix
            self._show_thumb()
        else:
            self.thumb.setText("no thumbnail available")

    def _show_thumb(self):
        if getattr(self, "_pixmap", None) and self.thumb.isVisible():
            self.thumb.setPixmap(self._pixmap.scaled(
                self.thumb.width(), self.thumb.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._show_thumb()

    # -------------------------------------------------------------- stream

    def toggle_stream(self):
        if self._player is None:
            self._start_stream()
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self.play_btn.setText("▶ Play")
        else:
            self._player.play()
            self.play_btn.setText("⏸ Pause")

    def _start_stream(self):
        if self.rec["source"] != "youtube":
            self.status.setText(
                "Streaming preview works for YouTube. For archive.org, use "
                "“Open in browser” — its player streams the item directly.")
            return
        self.play_btn.setEnabled(False)
        self.status.setText("finding a playable stream ...")
        self._resolver = StreamUrlResolver(self.rec, self.cookies_browser, self)
        self._resolver.ready.connect(self._stream_ready)
        self._resolver.failed.connect(self._stream_failed)
        self._resolver.start()

    def _stream_failed(self, msg):
        self.play_btn.setEnabled(True)
        if msg == "youtube-bot-check":
            self.status.setText(
                "YouTube asked this computer to prove it isn't a bot, so the "
                "stream could not start. Pick your browser under “Use cookies "
                "from browser” in Settings, or just use “Open in browser”.")
        else:
            self.status.setText(
                f"Could not stream this one ({msg}). “Open in browser” still "
                f"works, and downloading is unaffected.")

    def _stream_ready(self, url: str, duration: float):
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError:
            self._stream_failed("Qt Multimedia is not installed")
            return
        if duration:
            self._duration = duration
            self.len_label.setText(_hms(duration))

        self._video = QVideoWidget()
        self._video.setStyleSheet("background:#000;")
        self.thumb.hide()
        self.stage_lay.addWidget(self._video)

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self._pos_changed)
        self._player.durationChanged.connect(self._dur_changed)
        self._player.errorOccurred.connect(
            lambda e, s: self._stream_failed(s or str(e)))
        self._player.setSource(QUrl(url))
        self._player.play()

        self.play_btn.setEnabled(True)
        self.play_btn.setText("⏸ Pause")
        self.slider.setEnabled(True)
        self.status.setText("streaming — nothing is being saved to disk")

    def _dur_changed(self, ms):
        if ms > 0:
            self._duration = ms / 1000.0
            self.len_label.setText(_hms(self._duration))

    def _pos_changed(self, ms):
        pos = ms / 1000.0
        self.pos_label.setText(_hms(pos))
        if not self._seeking and self._duration:
            self.slider.setValue(int(1000 * pos / self._duration))

    def _slider_released(self):
        self._seeking = False
        self.seek_fraction(self.slider.value() / 1000.0)

    def seek_fraction(self, frac: float):
        if self._player is None:
            # let the buttons work as "start here"
            self._pending_seek = frac
            self.status.setText("press “Stream preview” first, then skip around")
            return
        if self._duration:
            self._player.setPosition(int(frac * self._duration * 1000))

    # --------------------------------------------------------------- close

    def _teardown(self):
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        for w in (self._resolver, self._thumb_loader):
            if w is not None and w.isRunning():
                w.wait(1500)

    def accept(self):
        self._teardown()
        super().accept()

    def reject(self):
        self._teardown()
        super().reject()
