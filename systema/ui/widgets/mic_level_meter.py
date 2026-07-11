"""
ui/widgets/mic_level_meter.py
MicLevelMeter — a live microphone input-level meter (Discord/Zoom style).

Opens its OWN short-lived sounddevice input stream (independent of
VoiceHandler's capture loop — PortAudio allows concurrent input streams), reads
float32 frames on PortAudio's audio thread, and drives a custom-painted
segmented bar: gradient-lit segments, fast attack / smooth exponential decay,
and a slowly-falling peak-hold tick.

Reused in both the Settings > Voice tab and the voice-activation setup popup.
Public API: start(device_id=None) / stop(); the stream is always released on
stop() and hideEvent.
"""

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import Qt, QTimer, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QBrush, QPen
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from systema.ui import theme as _theme


class _SegmentBar(QWidget):
    """Custom-painted segmented level bar. set_level/set_peak take 0-100."""

    _SEGMENTS = 28
    _GAP = 3

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._p = palette
        self._level = 0.0
        self._peak = 0.0
        self.setFixedHeight(16)
        self.setMinimumWidth(160)

    def set_values(self, level, peak):
        self._level = max(0.0, min(100.0, level))
        self._peak = max(0.0, min(100.0, peak))
        self.update()

    def paintEvent(self, event):
        p = self._p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        seg_w = (w - self._GAP * (self._SEGMENTS - 1)) / self._SEGMENTS
        lit = int(round(self._level / 100.0 * self._SEGMENTS))

        # One gradient spanning the whole bar; each lit segment picks its slice.
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0.0, QColor(p['accent']))
        grad.setColorAt(1.0, QColor(p.get('accent_lt', p['accent'])))
        lit_brush = QBrush(grad)

        unlit_brush = QBrush(QColor(p['surface2']))
        border_pen = QPen(QColor(p['border']))
        border_pen.setWidthF(1.0)

        x = 0.0
        for i in range(self._SEGMENTS):
            rect = QRectF(x, 2.0, seg_w, h - 4.0)
            if i < lit:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(lit_brush)
            else:
                painter.setPen(border_pen)
                painter.setBrush(unlit_brush)
            painter.drawRoundedRect(rect, 2.5, 2.5)
            x += seg_w + self._GAP

        # Peak-hold tick — a thin marker at the recent maximum.
        if self._peak > 2.0:
            px = self._peak / 100.0 * w
            tick = QColor(p['text'])
            tick.setAlphaF(0.65)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(tick))
            painter.drawRoundedRect(QRectF(min(px, w - 2.5), 0.0, 2.5, float(h)), 1.2, 1.2)

        painter.end()


class MicLevelMeter(QWidget):
    """Live mic meter. Call start(device_id) to begin monitoring and stop() to
    release the stream. Always stops on hide."""

    # PortAudio's callback runs on its own audio thread and must never touch Qt
    # widgets directly — hop onto the GUI thread via this signal.
    _level_measured = pyqtSignal(float)

    _DECAY = 0.85          # displayed level multiplier per animation tick
    _PEAK_FALL = 1.1       # peak marker fall per tick, after the hold expires
    _PEAK_HOLD_TICKS = 20  # ~600 ms hold at 30 ms/tick

    def __init__(self, controller=None, parent=None, show_hint=True):
        super().__init__(parent)
        self._controller = controller
        self._stream = None
        self._target = 0.0
        self._level = 0.0
        self._peak = 0.0
        self._peak_age = 0
        try:
            self._p = _theme.current_palette(controller)
        except Exception:
            self._p = _theme.resolve_palette(_theme.THEMES[_theme.DEFAULT_THEME_KEY])

        self._level_measured.connect(self._on_level_measured)

        # Smooth attack/decay animation — runs only while the stream is open.
        self._anim = QTimer(self)
        self._anim.setInterval(30)
        self._anim.timeout.connect(self._on_anim_tick)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.bar = _SegmentBar(self._p)
        lay.addWidget(self.bar)

        self.hint = None
        if show_hint:
            self.hint = QLabel("Speak to test your microphone...")
            self.hint.setStyleSheet(
                f"color: {self._p['muted']}; font-size: 11px; background: transparent;")
            lay.addWidget(self.hint)

    # ── stream lifecycle ─────────────────────────────────────────────────────

    def start(self, device_id=None):
        """(Re)open the monitor stream on device_id (None = system default)."""
        self.stop()
        try:
            samplerate = None
            if device_id is not None:
                samplerate = int(sd.query_devices(device_id)['default_samplerate'])
            self._stream = sd.InputStream(
                device=device_id, channels=1, samplerate=samplerate,
                dtype='float32', blocksize=1024, callback=self._audio_callback)
            self._stream.start()
            self._anim.start()
            self._set_hint("Speak to test your microphone...")
        except Exception as e:
            if self._controller is not None:
                try:
                    self._controller.log(f"Mic meter couldn't open device: {e}", "WARNING")
                except Exception:
                    pass
            self._stream = None
            self._set_hint("Couldn't open this microphone - try another")

    def stop(self):
        """Close the monitor stream and reset the bar."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._anim.stop()
        self._target = self._level = self._peak = 0.0
        self._peak_age = 0
        self.bar.set_values(0.0, 0.0)

    # ── audio → UI ───────────────────────────────────────────────────────────

    def _audio_callback(self, indata, frames, time_info, status):
        # Runs on PortAudio's audio thread — keep tiny, only emit the signal.
        rms = float(np.sqrt(np.mean(np.square(indata))) if frames else 0.0)
        self._level_measured.emit(rms)

    def _on_level_measured(self, rms: float):
        # RMS -> dBFS -> 0-100 so quiet room noise stays low and normal speech
        # reads comfortably mid-scale.
        db = 20 * np.log10(rms + 1e-9)
        self._target = max(0.0, min(1.0, (db + 55) / 55)) * 100
        if self._target > 8:
            self._set_hint("Picking up audio")

    def _on_anim_tick(self):
        # Fast attack, exponential decay.
        if self._target >= self._level:
            self._level = self._target
        else:
            self._level = max(self._target, self._level * self._DECAY - 0.5)
        # Peak hold, then fall.
        if self._level >= self._peak:
            self._peak = self._level
            self._peak_age = 0
        else:
            self._peak_age += 1
            if self._peak_age > self._PEAK_HOLD_TICKS:
                self._peak = max(self._level, self._peak - self._PEAK_FALL)
        self.bar.set_values(self._level, self._peak)

    def _set_hint(self, text):
        if self.hint is not None:
            self.hint.setText(text)

    # ── Qt teardown safety ───────────────────────────────────────────────────

    def hideEvent(self, event):
        # Never leak the stream if the widget is hidden without an explicit stop.
        self.stop()
        super().hideEvent(event)
