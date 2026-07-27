"""
systema/ui/chat/window_controls.py
WindowControlsMixin — floating chrome for the header-less chat window:
boxless minimize/close overlay buttons and the 4-state panel-icon sidebar
toggle (a floating instance while the sidebar is closed, plus a hero-docked
instance inside the sidebar while it is open).
New for the 2026-07 chat window redesign (visual spec: Desktop\\chat-window-mock.html).
"""
from PyQt6.QtWidgets import QPushButton
from PyQt6.QtCore import Qt, QRectF, QPointF, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath

# Canonical copy lives in the shared painted-icon module (2026-07-21 icon
# overhaul); re-exported here so existing `from window_controls import
# _lerp_color` call sites keep working.
from systema.ui.widgets.painted_icons import _lerp_color


class PanelToggleButton(QPushButton):
    """Sidebar toggle drawn as a panel glyph (rounded frame + left divider).

    Idle: plain frame. Hover: a chevron scales/fades in inside the right
    pane (animated, matching the mock's transform-origin transition) —
    ▷ when direction='open' (click opens the sidebar), ◁ when
    direction='close' (click closes it). The open/idle state reuses the same
    plain frame as closed/idle, giving the 4 states from the approved mock.
    """

    GLYPH_W, GLYPH_H = 22.0, 16.0
    CHEV_ANIM_MS = 150

    def __init__(self, direction: str = 'open', parent=None):
        super().__init__(parent)
        self._direction = direction
        self._hover = False
        self._chev_p = 0.0        # chevron reveal progress 0..1 (animated)
        self._chev_anim = None
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton { background: transparent; border: none; border-radius: 8px; }
            QPushButton:hover { background: #21262D; }
            QPushButton:pressed { background: #2A3038; }
        """)

    def _animate_chevron(self, target: float):
        if self._chev_anim is not None:
            try:
                self._chev_anim.stop()
            except RuntimeError:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._chev_p))
        anim.setEndValue(float(target))
        anim.setDuration(self.CHEV_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _tick(v):
            self._chev_p = float(v)
            self.update()
        anim.valueChanged.connect(_tick)
        self._chev_anim = anim
        anim.start()

    def enterEvent(self, event):
        self._hover = True
        self._animate_chevron(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._animate_chevron(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)   # stylesheet paints the hover pill
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cp = self._chev_p
        pen = QPen(_lerp_color('#9AA0A6', '#E8EAED', cp), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        ox = (self.width() - self.GLYPH_W) / 2
        oy = (self.height() - self.GLYPH_H) / 2
        # frame + divider (left pane)
        p.drawRoundedRect(QRectF(ox + 1, oy + 1, 20, 14), 3.5, 3.5)
        p.drawLine(QPointF(ox + 7.5, oy + 1.8), QPointF(ox + 7.5, oy + 14.2))
        if cp > 0.01:
            # chevron scales/fades about its own centre (mock: transform-origin
            # 13.75 8, scale .55→1 + opacity 0→1)
            cx, cy = ox + 13.75, oy + 8.0
            p.save()
            p.setOpacity(cp)
            s = 0.55 + 0.45 * cp
            p.translate(cx, cy)
            p.scale(s, s)
            p.translate(-cx, -cy)
            chev = QPainterPath()
            if self._direction == 'open':          # ▷ — click to open
                chev.moveTo(ox + 12.0, oy + 4.5)
                chev.lineTo(ox + 15.5, oy + 8.0)
                chev.lineTo(ox + 12.0, oy + 11.5)
            else:                                  # ◁ — click to close
                chev.moveTo(ox + 15.5, oy + 4.5)
                chev.lineTo(ox + 12.0, oy + 8.0)
                chev.lineTo(ox + 15.5, oy + 11.5)
            p.drawPath(chev)
            p.restore()
        p.end()


class DotsButton(QPushButton):
    """A 'more actions' / menu button drawn as three crisp horizontal dots (no
    container), brightening on hover. Replaces the faint ⋯ glyph buttons (the
    per-message action row and the window session-tools button). Matches
    PanelToggleButton's antialiased, colour-lerp house style. No emoji."""

    HOVER_ANIM_MS = 130

    def __init__(self, size: int = 22, parent=None):
        super().__init__(parent)
        self._hover_p = 0.0        # hover reveal progress 0..1 (animated)
        self._hover_anim = None
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Fully custom-painted — no stylesheet chrome to fight the chip.
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def _animate_hover(self, target: float):
        if self._hover_anim is not None:
            try:
                self._hover_anim.stop()
            except RuntimeError:
                pass
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._hover_p))
        anim.setEndValue(float(target))
        anim.setDuration(self.HOVER_ANIM_MS)
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

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hp = self._hover_p
        w, h = float(self.width()), float(self.height())

        # Three horizontal dots — NO container (the chip was removed per feedback).
        # Slightly bolder than a font glyph + brighten on hover for affordance.
        dot_r = max(1.5, w * 0.085)
        gap = dot_r * 2.7
        cx, cy = w / 2.0, h / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_lerp_color('#8B949E', '#E8EAED', hp))
        for dx in (-gap, 0.0, gap):
            p.drawEllipse(QPointF(cx + dx, cy), dot_r, dot_r)
        p.end()


class MessageButton(DotsButton):
    """Session Tools button drawn as a small chat/message bubble (painted, no
    emoji — the user's preferred 'message icon'). Reuses DotsButton's hover
    animation; only the glyph differs."""

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        hp = self._hover_p
        w, h = float(self.width()), float(self.height())
        pen = QPen(_lerp_color('#9AA0A6', '#E8EAED', hp), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Speech-bubble body
        bx, by = w * 0.20, h * 0.24
        bw, bh = w * 0.60, h * 0.40
        rad = bh * 0.34
        p.drawRoundedRect(QRectF(bx, by, bw, bh), rad, rad)
        # Tail at the bottom-left
        path = QPainterPath()
        path.moveTo(bx + bw * 0.28, by + bh)
        path.lineTo(bx + bw * 0.12, by + bh + h * 0.16)
        path.lineTo(bx + bw * 0.52, by + bh)
        p.drawPath(path)
        # Three tiny dots inside → reads unmistakably as a message bubble
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_lerp_color('#9AA0A6', '#E8EAED', hp))
        cy = by + bh * 0.5
        dr = max(0.9, w * 0.038)
        for fx in (0.34, 0.5, 0.66):
            p.drawEllipse(QPointF(bx + bw * fx, cy), dr, dr)
        p.end()


class WindowControlsMixin:
    """Floating −/× buttons, floating sidebar toggle, drag-strip constants
    (mixed into ChatWindow). The title bar is gone — the top DRAG_STRIP_H px
    of the chat viewport act as the drag-to-move surface (see eventFilter)."""

    DRAG_STRIP_H = 44   # invisible drag zone height, replaces the title bar

    def _build_window_controls(self):
        """Create the floating overlay controls. Call once at the end of
        init_ui, after self.container has its layout."""
        # ── Sidebar toggle — floating, visible while the sidebar is CLOSED ──
        self.toggle_sidebar_btn = PanelToggleButton('open', self.container)
        self.toggle_sidebar_btn.setToolTip("Open sidebar")
        self.toggle_sidebar_btn.move(12, 12)
        self.toggle_sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.toggle_sidebar_btn.raise_()
        self.toggle_sidebar_btn.show()

        # ── Painted minimize / close — float top-right over the chat ────────
        # (icon overhaul: text −/× glyphs → painted chrome buttons)
        from systema.ui.widgets.painted_icons import MinimizeButton, CloseButton
        self._win_min_btn = MinimizeButton(32, self.container, "Minimize")
        self._win_min_btn.clicked.connect(self.showMinimized)

        self._win_close_btn = CloseButton(32, self.container, "Close", pill=True)
        self._win_close_btn.clicked.connect(self.close)

        # Session tools moved into the input row (see input_dock); the window
        # chrome now holds only −/× + the sidebar toggle.
        for b in (self._win_min_btn, self._win_close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.raise_()
            b.show()
        self._position_window_controls()

    def _position_window_controls(self):
        """Keep ⋯/−/× pinned to the container's top-right."""
        if not hasattr(self, '_win_close_btn'):
            return
        w = self.container.width()
        self._win_close_btn.move(w - 12 - 32, 12)
        self._win_min_btn.move(w - 52 - 32, 12)

    def _raise_window_controls(self):
        """Re-raise all floating controls above freshly added overlays."""
        for name in ('toggle_sidebar_btn', '_win_min_btn', '_win_close_btn'):
            b = getattr(self, name, None)
            if b is not None:
                b.raise_()

    # ── Session tools menu (⋯) ──────────────────────────────────────────────

    def _show_session_menu(self):
        """Themed dropdown with session-scoped token/file tools."""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        _tc = self._t()
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
                font-size: 12px;
            }}
            QMenu::item {{ padding: 7px 14px; border-radius: 4px; }}
            QMenu::item:selected {{
                background-color: {_tc['surface']};
                color: {_tc['accent']};
            }}
            QMenu::separator {{ height: 1px; background: {_tc['border']}; margin: 4px 6px; }}
        """)

        compaction_menu = menu.addMenu("Compaction")
        if self.controller.compaction_active():
            act_compact = QAction("Stop compacting", self)
            act_compact.triggered.connect(lambda: self.controller.stop_compaction())
        else:
            act_compact = QAction("Compact all toolcalls", self)
            act_compact.triggered.connect(
                lambda: self.controller.compact_all_toolcalls())
        compaction_menu.addAction(act_compact)

        act_restore = QAction("Restore all compacted toolcalls", self)
        act_restore.triggered.connect(lambda: self.controller.restore_all_compacted())
        compaction_menu.addAction(act_restore)

        act_agents = QAction("Compaction agents", self)
        act_agents.triggered.connect(lambda: self.controller.open_compaction_agents_dialog())
        compaction_menu.addAction(act_agents)

        outputs_menu = menu.addMenu("Tool outputs")
        act_clear = QAction("Clear all tool outputs", self)
        act_clear.triggered.connect(self._clear_all_tool_outputs)
        outputs_menu.addAction(act_clear)

        act_revert = QAction("Revert cleared outputs", self)
        act_revert.triggered.connect(lambda: self.controller.revert_cleared_outputs())
        outputs_menu.addAction(act_revert)

        menu.addSeparator()

        act_files = QAction("Files touched this session", self)
        act_files.triggered.connect(self._open_session_files_dialog)
        menu.addAction(act_files)

        act_images = QAction("Image attachments", self)
        act_images.triggered.connect(self._open_image_attachments_dialog)
        menu.addAction(act_images)

        btn = getattr(self, '_session_tools_btn', None)
        if btn is not None:
            # Opens UPWARD — the button now sits at the bottom of the window.
            from PyQt6.QtCore import QPoint
            gp = btn.mapToGlobal(btn.rect().topLeft())
            menu.exec(QPoint(gp.x(), gp.y() - menu.sizeHint().height()))
        else:
            menu.exec()

    def _clear_all_tool_outputs(self):
        """Replace EVERY toolcall output in this session with the cleared stub
        (confirmed) — bulk token reclaim."""
        from PyQt6.QtWidgets import QMessageBox
        outputs = [e.get('_output') for e in self.controller.ai.conversation_history
                   if e.get('role') == 'ui_event' and isinstance(e.get('_output'), str)
                   and len(e.get('_output', '').strip()) >= 8
                   and not e['_output'].strip().startswith('[Compacted]')
                   and e['_output'].strip() != 'Output cleared by the user']
        if not outputs:
            self.add_system_message("No tool outputs to clear in this session.")
            return
        from systema.common.token_est import estimate_tokens
        total = sum(estimate_tokens(o) for o in outputs)
        ret = QMessageBox.question(
            self, "Clear all tool outputs",
            f"Replace {len(outputs)} toolcall output(s) (~{total:,} tokens) with\n"
            f"'Output cleared by the user' in the history and session file?\n\n"
            f"The AI will no longer see these results.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        n = 0
        for o in outputs:
            n += self.controller.rewrite_tool_output(
                o, "Output cleared by the user", save=False)
        self.controller._auto_save_session()
        self.render_loaded_messages()
        self.add_system_message(
            f"Cleared {len(outputs)} tool output(s) — reclaimed ~{total:,} tokens.")

    def _open_session_files_dialog(self):
        """Files touched this session — reversible state stepper."""
        try:
            from systema.ui.dialogs.session_files_dialog import SessionFilesDialog
            dlg = SessionFilesDialog(self)
            dlg.exec()
        except Exception as e:
            self.add_system_message(f"Could not open the files dialog: {e}")

    def _open_image_attachments_dialog(self):
        """Images attached this session — per-image detach/delete plus the
        three bulk actions, and what they cost per request."""
        try:
            from systema.ui.dialogs.image_attachments_dialog import ImageAttachmentsDialog
            dlg = ImageAttachmentsDialog(self)
            dlg.exec()
        except Exception as e:
            self.add_system_message(f"Could not open the images dialog: {e}")
