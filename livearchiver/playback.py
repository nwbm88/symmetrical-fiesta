"""Audio playback for the timeline, with a fallback for machines where Qt
Multimedia has no usable audio device.

`QMediaPlayer` constructs happily even when the system exposes no audio
output at all (no PulseAudio/PipeWire session, a bare Linux box, some remote
desktops), so "did the import work?" is not a usable test — the button ends
up enabled and silently does nothing.  We check for a real output device
instead, and fall back to driving `ffplay` as a subprocess, which needs no
sound server integration beyond what ffmpeg already brings.

Both backends expose the same small interface:

    player.positionChanged(seconds)   signal
    player.stateChanged(is_playing)   signal
    player.play() / pause() / stop() / set_position(seconds)
    player.backend                    "Qt" | "ffplay"
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import (QCoreApplication, QObject, QTimer, QElapsedTimer,
                            QUrl, Signal)


class _BasePlayer(QObject):
    positionChanged = Signal(float)
    stateChanged = Signal(bool)
    backend = "none"

    def is_playing(self) -> bool:
        raise NotImplementedError


# ------------------------------------------------------------------- Qt

class QtPlayer(_BasePlayer):
    backend = "Qt"

    def __init__(self, media: Path, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        self._p = QMediaPlayer(self)
        self._out = QAudioOutput(self)
        self._p.setAudioOutput(self._out)
        self._p.setSource(QUrl.fromLocalFile(str(media.resolve())))
        self._p.positionChanged.connect(lambda ms: self.positionChanged.emit(ms / 1000.0))
        self._p.playbackStateChanged.connect(self._on_state)

    def _on_state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        self.stateChanged.emit(state == QMediaPlayer.PlayingState)

    def is_playing(self) -> bool:
        from PySide6.QtMultimedia import QMediaPlayer
        return self._p.playbackState() == QMediaPlayer.PlayingState

    def play(self):
        self._p.play()

    def pause(self):
        self._p.pause()

    def stop(self):
        self._p.stop()

    def set_position(self, seconds: float):
        self._p.setPosition(int(seconds * 1000))


# --------------------------------------------------------------- ffplay

class FfplayPlayer(_BasePlayer):
    """Drives `ffplay` as a child process.

    ffplay has no usable control channel without a terminal, so pausing means
    killing the process and remembering where we were; playing resumes with
    `-ss`.  The playhead is tracked with a wall clock, which is accurate
    enough for placing song boundaries.
    """
    backend = "ffplay"
    TICK_MS = 50

    def __init__(self, media: Path, duration: float = 0.0, parent=None):
        super().__init__(parent)
        self.media = media
        self.duration = duration
        self._proc: subprocess.Popen | None = None
        self._base = 0.0          # position when the current run started
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)
        # never leave a child playing after the app goes away
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._kill)

    def __del__(self):
        try:
            self._kill()
        except Exception:
            pass

    # -- internals

    def _position(self) -> float:
        if self._proc and self._clock.isValid():
            return self._base + self._clock.elapsed() / 1000.0
        return self._base

    def _tick(self):
        pos = self._position()
        # Two ways playback ends: ffplay exits (normal), or we reach the end
        # of the show.  Don't rely on the process alone — if it stalls or the
        # audio device misbehaves the playhead would otherwise run past the
        # end of the concert forever.
        ended = self._proc is None or self._proc.poll() is not None
        if not ended and self.duration and pos >= self.duration:
            ended = True
            pos = self.duration
        if ended:
            self._kill()
            self._base = min(pos, self.duration) if self.duration else pos
            self.positionChanged.emit(self._base)
            self.stateChanged.emit(False)
            return
        self.positionChanged.emit(pos)

    def _kill(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        self._timer.stop()

    # -- interface

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def play(self):
        if self.is_playing():
            return
        self._kill()
        self._proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-ss", f"{self._base:.3f}", str(self.media)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        self._clock.start()
        self._timer.start()
        self.stateChanged.emit(True)

    def pause(self):
        if not self.is_playing():
            return
        self._base = self._position()
        self._kill()
        self.stateChanged.emit(False)

    def stop(self):
        self._kill()
        self._base = 0.0
        self.stateChanged.emit(False)

    def set_position(self, seconds: float):
        was_playing = self.is_playing()
        self._kill()
        self._base = max(0.0, seconds)
        if was_playing:
            self.play()
        else:
            self.positionChanged.emit(self._base)


# -------------------------------------------------------------- factory

def audio_output_available() -> bool:
    """True when Qt can actually reach a sound device (not just import)."""
    try:
        from PySide6.QtMultimedia import QMediaDevices
        return not QMediaDevices.defaultAudioOutput().isNull()
    except Exception:
        return False


def ffplay_available() -> bool:
    return shutil.which("ffplay") is not None


def create_player(media: Path, duration: float = 0.0, parent=None,
                  prefer_ffplay: bool = False):
    """Best available player, or (None, reason) when nothing can play audio."""
    if not prefer_ffplay and audio_output_available():
        try:
            return QtPlayer(media, parent), ""
        except Exception:
            pass
    if ffplay_available():
        return FfplayPlayer(media, duration, parent), ""
    if prefer_ffplay:
        return None, ("Qt reported playback but the position never advanced, "
                      "and ffplay was not found on PATH — install ffmpeg "
                      "(which bundles ffplay) to preview audio")
    return None, ("no audio output device, and ffplay was not found on PATH "
                  "— install ffmpeg (which bundles ffplay) to preview audio")
