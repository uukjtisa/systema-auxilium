"""
systema/ui/widgets/painted_icons.py

The house painted-icon button set (2026-07-21 icon overhaul, idea #3).

Every button that used an emoji/ASCII glyph (🎙️ 🔇 ■ ➤ 📎 ✕ − × >_) is now a
QPainter-drawn widget matching the PanelToggleButton gold standard
(window_controls.py): pure painted geometry, house-gray color lerp on hover
(#9AA0A6 → #E8EAED family), QVariantAnimation easing, no emoji, no assets,
no external icon fonts.

Design rules (user-approved):
  * Colors are the HARDCODED house grays — deliberately NOT theme-aware,
    matching the cards' grays. Do not wire these to the theme dict.
  * Input-row buttons are BOXLESS (no background pill) — hover feedback is
    the glyph brighten (+ per-button personality). Window-chrome buttons
    keep their hover pill.
  * Personality animations on key buttons only: send nudges right, voice
    pulses while ACTIVE, close turns red. Everything else: brighten lerp.

Adding a new icon = subclass GlyphButton and implement draw_glyph().
"""

import math

from PyQt6.QtWidgets import QComboBox, QPushButton, QWidget
from PyQt6.QtCore import (Qt, QRectF, QPointF, QTimer, QElapsedTimer,
                          QVariantAnimation, QEasingCurve)
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath


def _lerp_color(a: str, b: str, t: float) -> QColor:
    """Linear blend between two hex colours (glyph idle → hover tint)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


class GlyphButton(QPushButton):
    """Base painted icon button: animated hover progress + optional hover
    pill. Subclasses implement draw_glyph(painter, w, h, hover_progress)."""

    IDLE = '#9AA0A6'
    HOVER = '#E8EAED'
    DISABLED = '#5F6368'
    ANIM_MS = 130
    PILL = None            # hover pill bg hex, or None = boxless
    PILL_PRESSED = None

    def __init__(self, size=30, parent=None, tooltip=""):
        super().__init__(parent)
        w, h = (size, size) if isinstance(size, int) else size
        self.setFixedSize(w, h)
        self._hover_p = 0.0
        self._hover_anim = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if tooltip:
            self.setToolTip(tooltip)
        if self.PILL:
            pressed = self.PILL_PRESSED or self.PILL
            self.setStyleSheet(
                "QPushButton { background: transparent; border: none;"
                " border-radius: 8px; }"
                f"QPushButton:hover {{ background: {self.PILL}; }}"
                f"QPushButton:pressed {{ background: {pressed}; }}")
        else:
            self.setStyleSheet(
                "QPushButton { background: transparent; border: none; }")

    # ── hover animation (house standard) ─────────────────────────────────────
    def _animate_hover(self, target: float):
        if self._hover_anim is not None:
            try:
                self._hover_anim.stop()
            except RuntimeError:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._hover_p))
        anim.setEndValue(float(target))
        anim.setDuration(self.ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _tick(v):
            self._hover_p = float(v)
            self.update()
        anim.valueChanged.connect(_tick)
        self._hover_anim = anim
        anim.start()

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    # ── painting ─────────────────────────────────────────────────────────────
    def _color(self, hp: float) -> QColor:
        if not self.isEnabled():
            return QColor(self.DISABLED)
        return _lerp_color(self.IDLE, self.HOVER, hp)

    def _pen(self, hp: float, width: float = 1.7) -> QPen:
        pen = QPen(self._color(hp), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def paintEvent(self, event):
        super().paintEvent(event)          # stylesheet paints the pill
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_glyph(p, float(self.width()), float(self.height()),
                        self._hover_p)
        p.end()

    def draw_glyph(self, p: QPainter, w: float, h: float, hp: float):
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Input-row buttons (boxless)
# ─────────────────────────────────────────────────────────────────────────────

class SendButton(GlyphButton):
    """Send — a filled paper-plane that nudges right on hover (personality)."""
    IDLE = '#C9D1D9'
    HOVER = '#FFFFFF'
    DISABLED = '#5F5F5F'

    def draw_glyph(self, p, w, h, hp):
        p.save()
        p.translate(1.6 * hp, 0)                     # hover nudge →
        cx, cy = w / 2.0, h / 2.0
        s = min(w, h) * 0.30                          # half-span of the plane
        path = QPainterPath()
        path.moveTo(cx - s, cy - s * 0.78)            # top-left
        path.lineTo(cx + s, cy)                       # nose
        path.lineTo(cx - s, cy + s * 0.78)            # bottom-left
        path.lineTo(cx - s * 0.42, cy)                # inner notch
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color(hp))
        p.drawPath(path)
        p.restore()


class MicButton(GlyphButton):
    """Voice toggle — mic glyph; while CHECKED (voice mode active) it turns
    green and a soft ring pulses outward (personality)."""
    ACTIVE = '#4CAF50'

    def __init__(self, size=30, parent=None, tooltip=""):
        super().__init__(size, parent, tooltip)
        self.setCheckable(True)
        self._pulse_p = 0.0
        self._pulse_anim = None
        self.toggled.connect(self._sync_pulse)

    def _sync_pulse(self, *_):
        on = self.isChecked() and self.isVisible()
        if on and self._pulse_anim is None:
            anim = QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setDuration(1400)
            anim.setLoopCount(-1)

            def _tick(v):
                self._pulse_p = float(v)
                self.update()
            anim.valueChanged.connect(_tick)
            self._pulse_anim = anim
            anim.start()
        elif not on and self._pulse_anim is not None:
            try:
                self._pulse_anim.stop()
            except RuntimeError:
                pass
            self._pulse_anim = None
            self._pulse_p = 0.0
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_pulse()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._pulse_anim is not None:            # never animate unseen
            try:
                self._pulse_anim.stop()
            except RuntimeError:
                pass
            self._pulse_anim = None
            self._pulse_p = 0.0

    def _color(self, hp):
        if self.isChecked():
            return _lerp_color(self.ACTIVE, '#7BDB80', hp)
        return super()._color(hp)

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0                          # unit scale
        # pulse ring (active only)
        if self._pulse_p > 0.0 and self.isChecked():
            t = self._pulse_p
            ring = QColor(self.ACTIVE)
            ring.setAlphaF(max(0.0, 0.40 * (1.0 - t)))
            rp = QPen(ring, 1.6)
            p.setPen(rp)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = (7.5 + 6.5 * t) * u
            p.drawEllipse(QPointF(cx, cy), r, r)
        pen = self._pen(hp)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # capsule
        cap_w, cap_h = 5.6 * u, 9.5 * u
        p.drawRoundedRect(QRectF(cx - cap_w / 2, cy - 8.5 * u, cap_w, cap_h),
                          cap_w / 2, cap_w / 2)
        # holder arc (open-bottom U around the capsule)
        arc_r = 5.6 * u
        arc_rect = QRectF(cx - arc_r, cy - 2.6 * u - arc_r, arc_r * 2, arc_r * 2)
        p.drawArc(arc_rect, -200 * 16, 220 * 16)
        # stem + base
        p.drawLine(QPointF(cx, cy + 3.1 * u), QPointF(cx, cy + 6.4 * u))
        p.drawLine(QPointF(cx - 3.2 * u, cy + 6.4 * u),
                   QPointF(cx + 3.2 * u, cy + 6.4 * u))


class MuteButton(GlyphButton):
    """Voice interrupt — speaker with a slash (red family, boxless)."""
    IDLE = '#F07070'
    HOVER = '#FF8A80'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        pen = self._pen(hp, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # speaker: small rect + cone
        path = QPainterPath()
        path.moveTo(cx - 6.5 * u, cy - 2.6 * u)
        path.lineTo(cx - 3.4 * u, cy - 2.6 * u)
        path.lineTo(cx + 0.6 * u, cy - 6.0 * u)
        path.lineTo(cx + 0.6 * u, cy + 6.0 * u)
        path.lineTo(cx - 3.4 * u, cy + 2.6 * u)
        path.lineTo(cx - 6.5 * u, cy + 2.6 * u)
        path.closeSubpath()
        p.drawPath(path)
        # sound waves, cut by the slash
        p.drawArc(QRectF(cx + 1.8 * u, cy - 3.4 * u, 5.4 * u, 6.8 * u),
                  -60 * 16, 120 * 16)
        # slash
        p.drawLine(QPointF(cx - 7.2 * u, cy + 7.2 * u),
                   QPointF(cx + 7.2 * u, cy - 7.2 * u))


class StopButton(GlyphButton):
    """Cancel AI response — filled rounded stop square (red family, boxless)."""
    IDLE = '#F07070'
    HOVER = '#FF8A80'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        s = min(w, h) * 0.20
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color(hp))
        p.drawRoundedRect(QRectF(cx - s, cy - s, s * 2, s * 2), 2.5, 2.5)


class PaperclipButton(GlyphButton):
    """Attach file — a paperclip drawn at 45° (boxless, dimmer idle like the
    old 📎 which sat quieter than the action buttons)."""
    IDLE = '#6E7280'
    HOVER = '#9AA0A6'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        p.setPen(self._pen(hp, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        p.translate(cx, cy)
        p.rotate(45)
        # clip: big U up, small U back down inside it
        path = QPainterPath()
        path.moveTo(-2.6 * u, -7.0 * u)
        path.lineTo(-2.6 * u, 4.4 * u)
        path.arcTo(QRectF(-2.6 * u, 1.8 * u, 5.2 * u, 5.2 * u), 180, 180)
        path.lineTo(2.6 * u, -5.2 * u)
        path.arcTo(QRectF(-0.2 * u, -8.0 * u, 2.8 * u, 2.8 * u), 0, 180)
        path.lineTo(-0.2 * u, 2.6 * u)
        p.drawPath(path)
        p.restore()


# ─────────────────────────────────────────────────────────────────────────────
# Window chrome + small utility buttons
# ─────────────────────────────────────────────────────────────────────────────

class MinimizeButton(GlyphButton):
    """Window minimize — single dash, hover pill (chrome style)."""
    PILL = '#21262D'
    PILL_PRESSED = '#2A3038'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        s = min(w, h) * 0.185
        p.setPen(self._pen(hp, 1.7))
        p.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))


class MaximizeButton(GlyphButton):
    """Window maximize/restore — square outline, hover pill."""
    PILL = '#21262D'
    PILL_PRESSED = '#2A3038'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        s = min(w, h) * 0.175
        p.setPen(self._pen(hp, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(cx - s, cy - s, s * 2, s * 2), 1.5, 1.5)


class CloseButton(GlyphButton):
    """Window close — X that turns danger-red. `pill=True` = chrome variant
    (red pill + white glyph on hover); `pill=False` = boxless small ✕ for
    thumbnails/rows (glyph itself turns red)."""
    DANGER_BG = '#EA4335'

    def __init__(self, size=30, parent=None, tooltip="", pill=True):
        self._danger_pill = pill
        if pill:
            self.PILL = self.DANGER_BG
            self.PILL_PRESSED = '#D33B2C'
        super().__init__(size, parent, tooltip)

    def _color(self, hp):
        if not self.isEnabled():
            return QColor(self.DISABLED)
        if self._danger_pill:
            return _lerp_color(self.IDLE, '#FFFFFF', hp)   # white on red pill
        return _lerp_color('#8B949E', self.DANGER_BG, hp)  # glyph turns red

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        s = min(w, h) * 0.16
        p.setPen(self._pen(hp, 1.7))
        p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
        p.drawLine(QPointF(cx - s, cy + s), QPointF(cx + s, cy - s))


class TrashButton(GlyphButton):
    """Delete — trash can (lid + handle + body + inner lines); glyph turns
    red on hover. Boxless (sits inside list rows)."""
    IDLE = '#8B949E'
    HOVER = '#EA4335'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        p.setPen(self._pen(hp, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # lid + handle
        p.drawLine(QPointF(cx - 5.8 * u, cy - 4.6 * u),
                   QPointF(cx + 5.8 * u, cy - 4.6 * u))
        p.drawLine(QPointF(cx - 1.9 * u, cy - 6.6 * u),
                   QPointF(cx + 1.9 * u, cy - 6.6 * u))
        # tapered body
        body = QPainterPath()
        body.moveTo(cx - 4.6 * u, cy - 4.6 * u)
        body.lineTo(cx - 3.8 * u, cy + 6.2 * u)
        body.lineTo(cx + 3.8 * u, cy + 6.2 * u)
        body.lineTo(cx + 4.6 * u, cy - 4.6 * u)
        p.drawPath(body)
        # inner lines
        p.drawLine(QPointF(cx - 1.6 * u, cy - 2.2 * u), QPointF(cx - 1.4 * u, cy + 3.9 * u))
        p.drawLine(QPointF(cx + 1.6 * u, cy - 2.2 * u), QPointF(cx + 1.4 * u, cy + 3.9 * u))


class RepeatButton(GlyphButton):
    """Checkable repeat toggle (pinned image: send every message vs once) —
    two circular arrows; accent-tinted while checked."""
    ACTIVE = '#58A6FF'

    def __init__(self, size=26, parent=None, tooltip=""):
        super().__init__(size, parent, tooltip)
        self.setCheckable(True)
        self.toggled.connect(lambda _c: self.update())

    def _color(self, hp):
        if self.isChecked():
            return _lerp_color(self.ACTIVE, '#8CC2FF', hp)
        return super()._color(hp)

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        p.setPen(self._pen(hp, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = 5.6 * u
        rect = QRectF(cx - r, cy - r, r * 2, r * 2)
        p.drawArc(rect, 30 * 16, 140 * 16)            # top arc
        p.drawArc(rect, 210 * 16, 140 * 16)           # bottom arc
        # arrowheads
        col = self._color(hp)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        for ang, base in ((30, (cx + r * 0.86, cy - r * 0.55)),
                          (210, (cx - r * 0.86, cy + r * 0.55))):
            bx, by = base
            head = QPainterPath()
            if ang == 30:
                head.moveTo(bx + 1.9 * u, by - 1.1 * u)
                head.lineTo(bx - 1.4 * u, by - 2.2 * u)
                head.lineTo(bx + 0.4 * u, by + 1.8 * u)
            else:
                head.moveTo(bx - 1.9 * u, by + 1.1 * u)
                head.lineTo(bx + 1.4 * u, by + 2.2 * u)
                head.lineTo(bx - 0.4 * u, by - 1.8 * u)
            head.closeSubpath()
            p.drawPath(head)


class ClearButton(GlyphButton):
    """Clear/backspace — left-pointing key outline with a small × inside
    (replaces the ⌫ text glyph)."""
    PILL = '#21262D'
    PILL_PRESSED = '#2A3038'

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        p.setPen(self._pen(hp, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # key outline: ⌫ pentagon pointing left
        path = QPainterPath()
        path.moveTo(cx - 7.0 * u, cy)
        path.lineTo(cx - 2.6 * u, cy - 4.6 * u)
        path.lineTo(cx + 6.4 * u, cy - 4.6 * u)
        path.lineTo(cx + 6.4 * u, cy + 4.6 * u)
        path.lineTo(cx - 2.6 * u, cy + 4.6 * u)
        path.closeSubpath()
        p.drawPath(path)
        # small ×
        s = 1.9 * u
        kx = cx + 1.4 * u
        p.drawLine(QPointF(kx - s, cy - s), QPointF(kx + s, cy + s))
        p.drawLine(QPointF(kx - s, cy + s), QPointF(kx + s, cy - s))


class TerminalButton(GlyphButton):
    """A painted `>_` prompt glyph (chevron + caret line) — the debug window's
    command-input toggle. Checkable-friendly: brightens while checked."""
    PILL = '#21262D'
    PILL_PRESSED = '#2A3038'

    def _color(self, hp):
        if self.isChecked():
            return _lerp_color(self.HOVER, '#FFFFFF', hp)
        return super()._color(hp)

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 30.0
        p.setPen(self._pen(hp, 1.7))
        # chevron >
        chev = QPainterPath()
        chev.moveTo(cx - 6.0 * u, cy - 4.2 * u)
        chev.lineTo(cx - 1.8 * u, cy)
        chev.lineTo(cx - 6.0 * u, cy + 4.2 * u)
        p.drawPath(chev)
        # caret _
        p.drawLine(QPointF(cx + 1.2 * u, cy + 4.2 * u),
                   QPointF(cx + 6.2 * u, cy + 4.2 * u))


def draw_app_mark(p: QPainter, rect: QRectF, detail: bool = True,
                  star: str = '#88DCC7', ring: str = '#34685F',
                  companion: str = '#ECF0F3'):
    """Paint the Systema Auxilium mark — the north star on its orbit.

    Vector twin of assets/systema_auxilium.ico, so anywhere the app shows its
    own mark it scales cleanly instead of blitting a fixed-size bitmap.
    `detail=False` drops the ring/companion for small sizes (the same tiering
    the .ico frames use).
    """
    cx, cy = rect.center().x(), rect.center().y()
    R = min(rect.width(), rect.height()) / 2.0
    rot = math.radians(-24)

    def astroid(ox, oy, r, sharp=3.0, n=96):
        path = QPainterPath()
        for i in range(n + 1):
            t = 2 * math.pi * i / n
            ct, st = math.cos(t), math.sin(t)
            x = r * (abs(ct) ** sharp) * (1 if ct >= 0 else -1)
            y = (r * 1.06) * (abs(st) ** sharp) * (1 if st >= 0 else -1)
            pt = QPointF(ox + x, oy + y)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        return path

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if detail:
        # orbit path, behind the star
        rx, ry = R * 0.92, R * 0.45
        p.save()
        p.translate(cx, cy)
        p.rotate(-24)
        pen = QPen(QColor(ring), max(1.0, R * 0.045))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(-rx, -ry, rx * 2, ry * 2))
        p.restore()

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(star))
    p.drawPath(astroid(cx, cy, R * 0.64))

    if detail:
        rx, ry = R * 0.92, R * 0.45
        a = math.radians(8)
        x, y = rx * math.cos(a), ry * math.sin(a)
        sx = cx + x * math.cos(rot) - y * math.sin(rot)
        sy = cy + x * math.sin(rot) + y * math.cos(rot)
        p.setBrush(QColor(companion))
        p.drawPath(astroid(sx, sy, R * 0.22))
    p.restore()


class EyeButton(GlyphButton):
    """Reveal toggle for masked fields (API keys): an eye, struck through
    while the secret is hidden. Checkable — checked = revealed.

    Replaces the old text "Show"/"Hide" button, whose letters clipped inside
    the narrow settings row. Icon-only, so it never depends on font metrics.
    """
    PILL = '#21262D'
    PILL_PRESSED = '#2A3038'

    def __init__(self, size=28, parent=None):
        super().__init__(size=size, parent=parent)
        self.setCheckable(True)
        self._sync_tip()
        self.toggled.connect(lambda _: self._sync_tip())

    def _sync_tip(self):
        self.setToolTip("Hide" if self.isChecked() else "Show")
        self.update()

    def draw_glyph(self, p, w, h, hp):
        cx, cy = w / 2.0, h / 2.0
        u = min(w, h) / 28.0
        p.setPen(self._pen(hp, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Almond outline: two mirrored arcs meeting at the corners.
        half_w, half_h = 8.0 * u, 4.6 * u
        eye = QPainterPath()
        eye.moveTo(cx - half_w, cy)
        eye.quadTo(cx, cy - half_h * 2.0, cx + half_w, cy)
        eye.quadTo(cx, cy + half_h * 2.0, cx - half_w, cy)
        p.drawPath(eye)

        # Pupil — filled when revealed, hollow ring when hidden.
        r = 2.5 * u
        if self.isChecked():
            p.setBrush(self._color(hp))
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        if not self.isChecked():
            # Slash: drawn twice — once in the panel colour underneath so the
            # cut reads cleanly over the eye, then the stroke itself.
            p.setPen(QPen(QColor('#161B22'), 3.4 * u,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx - 8.4 * u, cy + 6.4 * u),
                       QPointF(cx + 8.4 * u, cy - 6.4 * u))
            p.setPen(self._pen(hp, 1.5))
            p.drawLine(QPointF(cx - 8.0 * u, cy + 6.0 * u),
                       QPointF(cx + 8.0 * u, cy - 6.0 * u))


class ContextEyeButton(EyeButton):
    """The same eye, meaning "the assistant can see this".

    Used on image thumbnails for detach-from-context: checked = in context,
    unchecked (struck through) = detached. EyeButton's own tooltip flips
    between Show/Hide on every toggle, which is wrong here — the caller sets a
    tooltip that explains what detaching an IMAGE does, and this keeps it.
    """

    def __init__(self, size=20, tooltip="", parent=None):
        super().__init__(size=size, parent=parent)
        self._fixed_tip = tooltip
        self.setToolTip(tooltip)

    def _sync_tip(self):
        self.setToolTip(getattr(self, '_fixed_tip', '') or self.toolTip())
        self.update()


class ChevronCombo(QComboBox):
    """QComboBox that PAINTS its own ⌄ chevron.

    Styling `QComboBox::drop-down` in a stylesheet suppresses the platform's
    native arrow, and QSS `::down-arrow` needs an image resource — so an
    editable, stylesheet-themed combo ends up looking exactly like a plain
    text box. Painting the chevron ourselves is the house idiom (hardcoded
    grays, same as the rest of painted_icons) and is theme/platform proof.
    """
    ARROW_W = 22                      # reserved chevron strip on the right
    COLOR = '#9AA0A6'
    COLOR_HOVER = '#E8EAED'

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = float(self.height())
        cx = float(self.width()) - self.ARROW_W / 2.0 - 4.0
        cy = h / 2.0
        u = max(1.0, min(h, 22.0) / 22.0)
        color = QColor(self.COLOR_HOVER if self._hovered else self.COLOR)
        pen = QPen(color, 1.6 * u)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(cx - 4.0 * u, cy - 1.8 * u)
        path.lineTo(cx, cy + 2.4 * u)
        path.lineTo(cx + 4.0 * u, cy - 1.8 * u)
        p.drawPath(path)
        p.end()


class TerminalGlyph(QWidget):
    """Non-interactive painted `>_` glyph for card headers (replaces the
    QLabel(\">_\") text). Zoom-aware via set_px(); color via set_color()."""

    def __init__(self, px: int = 12, color: str = '#5F6368', parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.set_px(px)

    def set_px(self, px: int):
        px = max(8, int(px))
        self.setFixedSize(int(px * 1.5), int(px * 1.25))
        self.update()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        pen = QPen(self._color, max(1.3, h * 0.11))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = w / 2.0, h / 2.0
        u = h / 15.0
        chev = QPainterPath()
        chev.moveTo(cx - 5.4 * u, cy - 3.4 * u)
        chev.lineTo(cx - 1.9 * u, cy)
        chev.lineTo(cx - 5.4 * u, cy + 3.4 * u)
        p.drawPath(chev)
        p.drawLine(QPointF(cx + 0.9 * u, cy + 3.4 * u),
                   QPointF(cx + 5.2 * u, cy + 3.4 * u))
        p.end()


class SparkleGlyph(QWidget):
    """The Thinking card's icon: a painted four-point sparkle that ANIMATES
    while the assistant is actually thinking, and freezes solid when it stops.

    Replaces QLabel("◇") — a hollow outline diamond that read as an empty box
    next to the filled card glyphs, and could not move.

    Two motions, on their own phases so the result never looks like a metronome:
      * the star's arms breathe in and out, its glow following the extension;
      * two micro-sparks orbit it, each fading in as it swings past.

    Painted rather than swapped between text frames: a QLabel changing glyphs
    re-lays-out the whole card header every frame, and the shapes step instead
    of moving. This is the same approach as TerminalGlyph above.

    `stop()` leaves the star fully extended and opaque — the resting shape is
    the one a reloaded session shows, so a replayed transcript is identical to
    a finished live one.
    """

    FPS_MS = 33               # ~30fps: plenty for a 12px glyph, cheap enough
    BREATH_MS = 1100.0        # one full in-out of the arms
    ORBIT_MS = 1900.0         # one full revolution of the sparks
    MIN_ARM = 0.62            # arm length at full retraction (fraction of max)
    MIN_GLOW = 0.55           # never fade so far it reads as blinking out
    SPARKS = 2
    ORBIT_R = 0.36            # spark orbit radius (fraction of the widget)

    def __init__(self, px: int = 12, color: str = '#5F6368', parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._t0 = None                     # None = resting (not animating)
        self._timer = QTimer(self)
        self._timer.setInterval(self.FPS_MS)
        self._timer.timeout.connect(self.update)
        self._clock = QElapsedTimer()
        self.set_px(px)

    # ── geometry / style ─────────────────────────────────────────────────────

    def set_px(self, px: int):
        px = max(8, int(px))
        # Square, and roomier than the text glyph it replaces: the orbiting
        # sparks need somewhere to be without clipping.
        self.setFixedSize(int(px * 1.5), int(px * 1.5))
        self.update()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    # ── run state ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self):
        """Begin sparkling. Idempotent — restarting mid-cycle would jump the
        phase, so a second call while running is ignored."""
        if self._timer.isActive():
            return
        self._clock.start()
        self._t0 = 0.0
        self._timer.start()

    def stop(self):
        """Freeze at the resting shape: fully extended, no sparks."""
        self._timer.stop()
        self._t0 = None
        self.update()

    def hideEvent(self, event):
        # A card scrolled out of the transcript, or a turn torn down mid-stream,
        # must not keep a repaint timer alive.
        self.stop()
        super().hideEvent(event)

    # ── painting ─────────────────────────────────────────────────────────────

    def _phases(self):
        """(arm 0..1, glow 0..1, orbit radians). Resting = fully extended."""
        if self._t0 is None:
            return 1.0, 1.0, 0.0
        ms = float(self._clock.elapsed())
        breath = (math.sin(2 * math.pi * (ms / self.BREATH_MS)) + 1.0) / 2.0
        arm = self.MIN_ARM + (1.0 - self.MIN_ARM) * breath
        glow = self.MIN_GLOW + (1.0 - self.MIN_GLOW) * breath
        return arm, glow, 2 * math.pi * (ms / self.ORBIT_MS)

    @staticmethod
    def _star(cx, cy, r):
        """A four-point sparkle: arms to N/E/S/W, each pair pinched through the
        centre so the waist is concave — the shape of ✦ rather than a diamond."""
        path = QPainterPath()
        path.moveTo(cx, cy - r)
        for ex, ey in ((cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)):
            path.quadTo(cx, cy, ex, ey)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2.0, h / 2.0
        arm, glow, orbit = self._phases()

        body = QColor(self._color)
        body.setAlphaF(max(0.0, min(1.0, body.alphaF() * glow)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body)
        p.drawPath(self._star(cx, cy, (min(w, h) / 2.0 - 1.0) * arm))

        if self._t0 is not None:
            r = min(w, h) * self.ORBIT_R
            for i in range(self.SPARKS):
                a = orbit + (2 * math.pi * i / self.SPARKS)
                # Sparks brighten on the DIAGONALS and vanish as they cross an
                # arm. The star's waist is pinched, so the diagonals are the
                # only empty space in the glyph — fading in over an arm instead
                # (which sin(a)**2 did) just smeared the spark into the body.
                fade = math.sin(2 * a) ** 2
                if fade <= 0.02:
                    continue
                c = QColor(self._color)
                c.setAlphaF(max(0.0, min(1.0, c.alphaF() * fade * 0.9)))
                p.setBrush(c)
                p.drawPath(self._star(cx + r * math.cos(a), cy + r * math.sin(a),
                                      max(1.5, min(w, h) * 0.13)))
        p.end()
