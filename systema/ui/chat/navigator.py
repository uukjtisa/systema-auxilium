"""
systema/ui/chat/navigator.py
DeepSeek-style message navigator for the chat window (2026-07 redesign).

Replaces the scrollbar: a right-edge overlay on the chat area that idles as
one faint dash per USER message (the active one accented in blue) and, on
hover, expands into a preview panel — click a row to jump straight to that
message. A scroll-spy keeps the active dash in sync while reading.

Hidden entirely until the session has at least 2 user messages.
"""
from PyQt6.QtCore import (Qt, QPoint, QRect, QRectF, QVariantAnimation,
                          QEasingCurve)
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget


def _lerp_rect(a: QRect, b: QRect, t: float) -> QRect:
    """Interpolate between the idle strip rect and the expanded panel rect."""
    return QRect(round(a.x() + (b.x() - a.x()) * t),
                 round(a.y() + (b.y() - a.y()) * t),
                 round(a.width() + (b.width() - a.width()) * t),
                 round(a.height() + (b.height() - a.height()) * t))


class MessageNavigator(QWidget):
    """Overlay child of the chat container; owns no data — every rebuild()
    re-reads the window's message_widgets, so it can never drift out of sync
    with the real chat (deleted / rewound / reloaded messages included)."""

    IDLE_W = 26           # idle hover-target width (dashes right-aligned inside)
    PANEL_W = 250         # expanded preview panel width
    ROW_H = 26            # expanded row height
    PITCH = 10            # idle dash pitch
    IDLE_MAX_DASHES = 7   # idle strip shows only the LATEST N (expand for all)
    DASH_W = 14
    DASH_W_ACTIVE = 18
    PAD = 10              # panel inner padding
    EDGE_MARGIN = 6       # gap to the container's right edge
    EXPAND_ANIM_MS = 180  # hover expand / collapse duration (mock: .18s ease-out)

    def __init__(self, win, parent):
        super().__init__(parent)
        self._win = win                # the ChatWindow (message_widgets, scroll api)
        self._entries = []             # [{'widget': message QFrame, 'label': str}]
        self._active = 0
        self._expanded = False         # hover INTENT (drives the animation target)
        self._anim_p = 0.0             # expand progress 0 (strip) .. 1 (panel)
        self._nav_anim = None
        self._hover_row = -1
        self._row_offset = 0           # first visible row when the panel overflows
        self._visible_rows = 0
        self._pitch = self.PITCH
        self._chunk = 0                # which idle CHUNK is currently displayed
        self._chunk_slide = 0.0        # y-offset for the chunk-change slide anim
        self._chunk_anim = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()
        try:
            win.chat_scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        except Exception:
            pass

    # ── data ───────────────────────────────────────────────────────────────

    def rebuild(self):
        """Re-read the window's message_widgets → one entry per user message."""
        entries = []
        for md in getattr(self._win, 'message_widgets', []):
            if md.get('role') != 'user':
                continue
            w = md.get('widget')
            if w is None:
                continue
            label = ' '.join(str(md.get('content', '')).split())[:80]
            entries.append({'widget': w, 'label': label})
        self._entries = entries
        self._row_offset = 0
        self._hover_row = -1
        self._sync_active(repaint=False)
        self.setVisible(len(entries) >= 2)
        self.reposition()
        self.update()

    # ── idle chunking ────────────────────────────────────────────────────────

    def _chunks(self):
        """Split the messages into idle 'pages' of AT MOST IDLE_MAX_DASHES,
        sized as EVENLY as possible so a big convo never ends on a lonely 1-2
        dash remainder (e.g. 9 → [5,4] not [7,2]; 20 → [7,7,6]). Returns a list
        of (start, end) index pairs."""
        import math
        n = len(self._entries)
        if n <= self.IDLE_MAX_DASHES:
            return [(0, n)]
        num = math.ceil(n / self.IDLE_MAX_DASHES)
        base, rem = divmod(n, num)
        out, start = [], 0
        for i in range(num):
            size = base + (1 if i < rem else 0)
            out.append((start, start + size))
            start += size
        return out

    def _active_chunk(self):
        """Index of the chunk that currently holds the active message."""
        for ci, (s, e) in enumerate(self._chunks()):
            if s <= self._active < e:
                return ci
        chunks = self._chunks()
        return len(chunks) - 1 if chunks else 0

    def _animate_chunk_shift(self, direction: int):
        """Slide the idle dashes when the active message crosses into a new
        chunk, so the jump reads as a deliberate move to that page."""
        if self._chunk_anim is not None:
            try:
                self._chunk_anim.stop()
            except RuntimeError:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(float(direction) * 16.0)
        anim.setEndValue(0.0)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _tick(v):
            self._chunk_slide = float(v)
            self.reposition()
            self.update()
        anim.valueChanged.connect(_tick)
        self._chunk_anim = anim
        anim.start()

    # ── geometry ───────────────────────────────────────────────────────────

    def _rects(self):
        """Compute BOTH the idle strip rect and the expanded panel rect (in
        parent coords) — the live geometry interpolates between them."""
        parent = self.parentWidget()
        n = len(self._entries)
        # idle strip — only the ACTIVE chunk of messages (the expanded panel
        # lists them all); a big convo otherwise grew an unusably tall column.
        chunks = self._chunks()
        cs, ce = chunks[min(self._chunk, len(chunks) - 1)] if chunks else (0, 0)
        k = max(1, ce - cs)
        self._pitch = self.PITCH
        ih = k * self._pitch
        idle = QRect(parent.width() - self.IDLE_W - self.EDGE_MARGIN,
                     max(0, (parent.height() - ih) // 2), self.IDLE_W, ih)
        # expanded panel
        avail_p = max(120, parent.height() - 140)
        self._visible_rows = max(3, min(n, (avail_p - 2 * self.PAD) // self.ROW_H))
        self._row_offset = max(0, min(self._row_offset, n - self._visible_rows))
        ph = self._visible_rows * self.ROW_H + 2 * self.PAD
        panel = QRect(parent.width() - self.PANEL_W - self.EDGE_MARGIN,
                      max(0, (parent.height() - ph) // 2), self.PANEL_W, ph)
        return idle, panel

    def reposition(self):
        parent = self.parentWidget()
        if parent is None or len(self._entries) == 0:
            return
        idle, panel = self._rects()
        self.setGeometry(_lerp_rect(idle, panel, self._anim_p))
        self.raise_()

    def _animate_expand(self, target: float):
        """Glide between strip and panel (geometry + cross-fade in paint)."""
        if self._nav_anim is not None:
            try:
                self._nav_anim.stop()
            except RuntimeError:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._anim_p))
        anim.setEndValue(float(target))
        anim.setDuration(self.EXPAND_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _tick(v):
            self._anim_p = float(v)
            self.reposition()
            self.update()
        anim.valueChanged.connect(_tick)
        self._nav_anim = anim
        anim.start()

    # ── scroll-spy ─────────────────────────────────────────────────────────

    def _on_scroll(self, _value=None):
        if self._entries and self.isVisible():
            self._sync_active()

    def _sync_active(self, repaint=True):
        """Active = last user message whose top sits above the 35%-viewport
        marker — i.e. the request the reader is currently looking at."""
        win = self._win
        if not self._entries or not hasattr(win, 'chat_scroll_area'):
            return
        try:
            sb = win.chat_scroll_area.verticalScrollBar()
            marker = sb.value() + win.chat_scroll_area.viewport().height() * 0.35
        except RuntimeError:
            return
        active = 0
        for i, e in enumerate(self._entries):
            try:
                if e['widget'].mapTo(win.chat_widget, QPoint(0, 0)).y() <= marker:
                    active = i
            except RuntimeError:
                continue
        if active != self._active:
            self._active = active
            self._sync_chunk(repaint=repaint)
            # Keep the expanded panel scrolled to wherever the reader is.
            if self._expanded:
                self._center_active_row()
            if repaint:
                self.update()

    def _sync_chunk(self, repaint=True):
        """Follow the active message into its chunk — animate the idle strip's
        jump so the move to the new page is visible."""
        new_chunk = self._active_chunk()
        if new_chunk != self._chunk:
            direction = 1 if new_chunk > self._chunk else -1
            self._chunk = new_chunk
            self.reposition()
            if repaint and self.isVisible():
                self._animate_chunk_shift(direction)

    def _center_active_row(self):
        """Scroll the expanded panel so the active row is centred/visible."""
        n = len(self._entries)
        if n > self._visible_rows:
            self._row_offset = max(0, min(self._active - self._visible_rows // 2,
                                          n - self._visible_rows))

    # ── navigation ─────────────────────────────────────────────────────────

    def _navigate(self, i):
        win = self._win
        try:
            widget = self._entries[i]['widget']
            if not widget.isVisible():
                return
        except (IndexError, RuntimeError):
            return
        # Explicit navigation: releases the sticky-bottom pin (back-reading),
        # unless the target IS the latest message — then following resumes.
        win._user_scrolling = False
        win._stick_to_bottom = (i == len(self._entries) - 1)
        self._active = i
        self._sync_chunk(repaint=True)   # idle strip follows to its chunk
        win.scroll_to_widget(widget, force=True)
        self.update()

    # ── hover expand / collapse ────────────────────────────────────────────

    def enterEvent(self, event):
        self._expanded = True
        # Open with the active row centred in the visible window.
        n = len(self._entries)
        self.reposition()   # refreshes _visible_rows before centring
        if n > self._visible_rows:
            self._row_offset = max(0, min(self._active - self._visible_rows // 2,
                                          n - self._visible_rows))
        self._animate_expand(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._expanded = False
        self._hover_row = -1
        self._animate_expand(0.0)
        super().leaveEvent(event)

    # ── input ──────────────────────────────────────────────────────────────

    def _row_at(self, y):
        r = int((y - self.PAD) // self.ROW_H)
        i = self._row_offset + r
        if 0 <= r < self._visible_rows and 0 <= i < len(self._entries):
            return i
        return -1

    def mouseMoveEvent(self, event):
        if self._expanded:
            row = self._row_at(event.position().y())
            if row != self._hover_row:
                self._hover_row = row
                self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._expanded and event.button() == Qt.MouseButton.LeftButton:
            row = self._row_at(event.position().y())
            if row >= 0:
                self._navigate(row)
            event.accept()
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        n = len(self._entries)
        if self._expanded and n > self._visible_rows:
            step = -1 if event.angleDelta().y() > 0 else 1
            self._row_offset = max(0, min(self._row_offset + step,
                                          n - self._visible_rows))
            self._hover_row = self._row_at(event.position().y())
            self.update()
            event.accept()
            return
        event.ignore()

    # ── theme ────────────────────────────────────────────────────────────────

    def _theme(self) -> dict:
        """Live theme dict from the owning ChatWindow (falls back to the
        obsidian-blue defaults if unavailable) — keeps the rail in step with
        the active chat theme instead of the old hardcoded blue."""
        try:
            return self._win._t()
        except Exception:
            return {'base': '#0D1117', 'elevated': '#21262D',
                    'border': '#30363D', 'accent': '#58A6FF'}

    # ── painting ───────────────────────────────────────────────────────────

    def paintEvent(self, event):
        n = len(self._entries)
        if n == 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        ap = self._anim_p   # 0 = strip, 1 = panel (cross-fade between them)
        t = self._theme()
        accent = QColor(t.get('accent', '#58A6FF'))

        if ap < 0.35:
            # Idle dashes fade out as the panel grows in. Only the ACTIVE CHUNK
            # is shown (hover/expand for the full list); it slides when the
            # active message crosses into a new chunk.
            base_op = 1.0 - ap / 0.35
            # Dip opacity mid-slide so the page-change reads as a transition.
            move = min(1.0, abs(self._chunk_slide) / 16.0)
            p.setOpacity(base_op * (1.0 - 0.55 * move))
            x_right = self.width() - 4
            chunks = self._chunks()
            cs, ce = chunks[min(self._chunk, len(chunks) - 1)] if chunks else (0, 0)
            for j, i in enumerate(range(cs, ce)):
                cy = j * self._pitch + self._pitch / 2 + self._chunk_slide
                if i == self._active:
                    color = accent
                    dw, dh = self.DASH_W_ACTIVE, 3.0
                else:
                    color = QColor(255, 255, 255, 56)     # rgba white .22
                    dw, dh = self.DASH_W, 2.5
                rect = QRectF(x_right - dw, cy - dh / 2, dw, dh)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(rect, dh / 2, dh / 2)
            p.setOpacity(1.0)
        if ap <= 0.02:
            p.end()
            return

        # Expanded preview panel (fades/grows in with the animation)
        p.setOpacity(ap)
        panel = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        panel_bg = QColor(t.get('elevated', '#111111'))
        panel_bg.setAlpha(248)
        p.setPen(QPen(QColor(t.get('border', '#1c1c1c')), 1))
        p.setBrush(panel_bg)
        p.drawRoundedRect(panel, 12, 12)

        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        fm = QFontMetrics(font)
        text_x = self.PAD + 12
        text_w = self.width() - text_x - self.PAD

        for r in range(self._visible_rows):
            i = self._row_offset + r
            if i >= n:
                break
            y0 = self.PAD + r * self.ROW_H
            if i == self._hover_row:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 14))
                p.drawRoundedRect(QRectF(6, y0 + 1, self.width() - 12,
                                         self.ROW_H - 2), 6, 6)
            if i == self._active:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
                p.drawRoundedRect(QRectF(self.PAD - 2, y0 + (self.ROW_H - 12) / 2,
                                         3, 12), 1.5, 1.5)
            p.setPen(QColor("#E8EAED" if i == self._active else "#9AA0A6"))
            text = fm.elidedText(self._entries[i]['label'],
                                 Qt.TextElideMode.ElideRight, text_w)
            p.drawText(QRectF(text_x, y0, text_w, self.ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       text)

        # Overflow hints: faint dots when rows continue above / below.
        if self._row_offset > 0:
            p.setPen(QColor(255, 255, 255, 70))
            p.drawText(QRectF(0, 1, self.width(), 10),
                       Qt.AlignmentFlag.AlignHCenter, "⋯")
        if self._row_offset + self._visible_rows < n:
            p.setPen(QColor(255, 255, 255, 70))
            p.drawText(QRectF(0, self.height() - 11, self.width(), 10),
                       Qt.AlignmentFlag.AlignHCenter, "⋯")
        p.end()
