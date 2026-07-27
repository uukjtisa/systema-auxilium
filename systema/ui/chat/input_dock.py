"""
systema/ui/chat/input_dock.py
InputDockMixin — floating input pill, attachments, token count.
Extracted verbatim from chat_window.py (full-split pass, 2026-07-17).
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel,
                             QFrame, QMenu, QScrollArea, QApplication,
                             QGraphicsOpacityEffect, QSizePolicy)
from PyQt6.QtCore import (Qt, QTimer, QPoint, pyqtSignal, QRect, QEvent,
                          QPropertyAnimation, QEasingCurve, QParallelAnimationGroup)
from PyQt6.QtGui import QAction, QCursor, QRegion, QPixmap
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from systema.common.logger import _make_logger, _NoOpLogger
from systema.ui.chat.constants import *
from systema import APP_ROOT as _APP_ROOT
from systema.ui.widgets.inputs import ResizableInput

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


class InlineStatus(QLabel):
    """A compact status / work-mode label that lives INSIDE the input pill's
    bottom action row (not a floating bar). It auto-hides when its text is
    cleared, so an empty label never reserves space in the row."""

    def setText(self, text):
        super().setText(text or "")
        self.setVisible(bool(text))


class _ChatBottomFade(QWidget):
    """Mouse-transparent gradient strip anchored to the BOTTOM of the chat
    display (not to the pill): chat content dims as it approaches the window's
    bottom edge, and the input pill simply floats on top of it. Fixed in
    place — growing/dragging the input taller covers more of it instead of
    dragging the dim band up mid-screen. Purely cosmetic: all mouse events
    pass through to the messages below."""

    # Total strip height from the container's bottom edge. The compact pill
    # (~110px) covers the strongest part; ~90px of dim stays visible above it.
    HEIGHT = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._color = QColor('#0D1117')

    def set_color(self, hex_color):
        try:
            self._color = QColor(hex_color)
        except Exception:
            self._color = QColor('#0D1117')
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QLinearGradient, QPainterPath
        from PyQt6.QtCore import QRectF
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Clip to bottom-rounded corners so the strip never paints square
        # pixels over the window's smooth 12px radius (rect extended upward
        # puts the path's top corners outside the widget).
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, -24, self.width(), self.height() + 24), 12, 12)
        p.setClipPath(clip)
        grad = QLinearGradient(0, 0, 0, self.height())
        # Gentle dim in the visible upper half; strong toward the bottom edge
        # (mostly covered by the floating pill).
        for stop, alpha in ((0.0, 0), (0.45, 90), (0.75, 180), (1.0, 225)):
            c = QColor(self._color)
            c.setAlpha(alpha)
            grad.setColorAt(stop, c)
        p.fillRect(self.rect(), grad)
        p.end()


class _InputResizeHandle(QFrame):
    """Slim grab bar flanking the input pill — drag to resize its width.
    Symmetric: the pill stays centred, so 1px of drag = 2px of width. The
    chosen width persists as input_box_geometry in settings.json
    (chat_window_config section)."""

    W, H = 5, 40

    def __init__(self, owner, side, parent):
        super().__init__(parent)
        self._owner = owner
        self._side = side            # 'left' | 'right'
        self._drag_x = None
        self._start_w = 0
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setStyleSheet("""
            QFrame { background: rgba(255,255,255,0.10); border-radius: 2px; }
            QFrame:hover { background: rgba(255,255,255,0.35); }
        """)
        # The handle places ITSELF against its parent's edges. Being positioned
        # only by the code that calls setGeometry() was not enough: the pill's
        # layout uses SetMinimumSize, so when the bottom row's content grows
        # (the token estimate going from "~0" to "~8.3k token per request", a
        # work banner appearing) the layout pushes a WIDER minimumSize onto the
        # container and Qt resizes it on the spot — no setGeometry call, so
        # nothing repositioned the bars and the right one stranded in the middle
        # of the pill, a stray vertical line next to the mic button.
        parent.installEventFilter(self)

    def reposition(self):
        """Glue this bar to its parent's side gutter, vertically centred."""
        p = self.parentWidget()
        if p is None:
            return
        try:
            y = max(0, (p.height() - self.H) // 2)
            self.move(4 if self._side == 'left' else p.width() - self.W - 4, y)
            self.raise_()
        except RuntimeError:
            pass

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.reposition()
        return False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_x = event.globalPosition().x()
            # Start from the DISPLAYED width so the drag never jumps when the
            # stored preference is wider than the current window allows.
            ic = getattr(self._owner, 'input_container', None)
            self._start_w = ic.width() if ic is not None else 640
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_x is None:
            return
        delta = event.globalPosition().x() - self._drag_x
        if self._side == 'left':
            delta = -delta
        cc = getattr(self._owner, '_chat_container', None)
        max_w = (cc.width() - 24) if cc is not None else 10 ** 6
        self._owner._input_box_width = int(
            max(420, min(self._start_w + 2 * delta, max_w)))
        self._owner._position_input_overlay()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_x is not None:
            self._drag_x = None
            try:
                self._owner.save_config()
            except Exception:
                pass
            event.accept()


class InputDockMixin:
    """Floating input pill + attachment/preview subsystem (mixed into ChatWindow)."""

    def _measure_input_overlay(self):
        """(QRect, reserve_px) for the floating pill — the ONE place this
        geometry is computed.

        Both the eager pass and the deferred settle pass call it. They used to
        carry SEPARATE COPIES of this arithmetic, and the copies drifted: the
        empty-session mid-window position was applied by the eager pass and
        then silently dragged back to the bottom edge by the settle pass a
        frame later. Never reintroduce a second copy.

        Returns None when the overlay is not wired up yet.
        """
        ic = getattr(self, 'input_container', None)
        cc = getattr(self, '_chat_container', None)
        if ic is None or cc is None:
            return None

        # Force the pill's SetMinimumSize layouts to recompute before we
        # measure. invalidate() FIRST: activate() alone no-ops when the layout
        # cache isn't marked dirty yet — Qt propagates a child setFixedHeight
        # upward via posted events, so on a synchronous SHRINK (send → clear)
        # the cached hints are still tall and the pill stays suspended
        # mid-screen. Growth invalidates eagerly, which is why only the
        # collapse direction ever stuck.
        lay = ic.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        # Height comes DETERMINISTICALLY from the pill's own content + the
        # container margins, rather than the container's sizeHint /
        # minimumSizeHint (which a snap-resize could inflate, letting the pill
        # balloon and clip off the bottom edge).
        card = getattr(self, '_input_card', None)
        if card is not None:
            card.layout().invalidate()
            card.layout().activate()
            m = lay.contentsMargins() if lay is not None else None
            pad = (m.top() + m.bottom()) if m is not None else 20
            h = card.sizeHint().height() + pad
        else:
            h = max(ic.sizeHint().height(), ic.minimumSizeHint().height())
        # A mid-relayout hint can momentarily read ~0 — never let the pill
        # collapse to an invisible sliver at the bottom edge ("input box
        # disappeared until I resized the window").
        h = max(h, 44)
        # Never let the top go negative (pill taller than the whole chat area).
        top = max(0, cc.height() - h)
        # EMPTY SESSION: float the pill in the middle of the window, just under
        # the greeting, instead of parking it at the bottom with a screenful of
        # dead space above it. It slides back to the bottom edge the moment the
        # intro is dismissed by a real message.
        if self._session_intro_showing():
            top = min(max(0, int(cc.height() * 0.52)), max(0, cc.height() - h))
        # Centred, width-resizable pill (2026-07 redesign): user width
        # preference (input_box_geometry), clamped to the chat area.
        w = min(max(420, int(getattr(self, '_input_box_width', 640))),
                max(200, cc.width() - 24))
        # Reserve the strip the pill covers, measured from its TOP rather than
        # from its height: identical while it sits on the bottom edge, correct
        # when it floats.
        return QRect(max(0, (cc.width() - w) // 2), top, w, h), \
            max(0, cc.height() - top) + 8

    def _position_input_overlay(self):
        """Anchor the floating input container over the chat area and pad the
        message list so the last message can scroll clear of it. No-op until
        the overlay is wired up."""
        ic = getattr(self, 'input_container', None)
        cc = getattr(self, '_chat_container', None)
        if ic is None or cc is None:
            return
        try:
            measured = self._measure_input_overlay()
            if measured is None:
                return
            rect, reserve = measured
            h = rect.height()
            ic.setGeometry(rect)
            # Bottom fade stays anchored to the chat display's bottom edge —
            # independent of the pill's height; the pill floats on top of it.
            fade = getattr(self, '_chat_fade', None)
            if fade is not None:
                fh = min(fade.HEIGHT, cc.height())
                fade.setGeometry(0, cc.height() - fh, cc.width(), fh)
                fade.raise_()
            ic.raise_()
            self._position_input_handles()
            # Guarded separately: a dead chat_layout must never abort the
            # settle-pass scheduling below.
            try:
                if hasattr(self, 'chat_layout'):
                    m = self.chat_layout.contentsMargins()
                    if m.bottom() != reserve:
                        self.chat_layout.setContentsMargins(m.left(), m.top(), m.right(), reserve)
            except RuntimeError:
                pass
            # Self-correction net: one deferred re-anchor after the event loop
            # settles catches any hint that was still stale on this pass. It
            # re-measures and no-ops when the rect is already right.
            if not getattr(self, '_overlay_recheck_scheduled', False):
                self._overlay_recheck_scheduled = True

                def _recheck():
                    self._overlay_recheck_scheduled = False
                    ic2 = getattr(self, 'input_container', None)
                    cc2 = getattr(self, '_chat_container', None)
                    if ic2 is None or cc2 is None:
                        return
                    self._position_input_overlay_settle()
                QTimer.singleShot(0, _recheck)
        except RuntimeError:
            pass

    def _position_input_overlay_settle(self):
        """Deferred second measurement pass — identical math to
        _position_input_overlay but never schedules another recheck, so the
        pair can't ping-pong."""
        ic = getattr(self, 'input_container', None)
        cc = getattr(self, '_chat_container', None)
        if ic is None or cc is None:
            return
        try:
            measured = self._measure_input_overlay()
            if measured is None:
                return
            target, reserve = measured
            if ic.geometry() != target:
                ic.setGeometry(target)
                fade = getattr(self, '_chat_fade', None)
                if fade is not None:
                    fh = min(fade.HEIGHT, cc.height())
                    fade.setGeometry(0, cc.height() - fh, cc.width(), fh)
                    fade.raise_()
                ic.raise_()
                self._position_input_handles()
            try:
                if hasattr(self, 'chat_layout'):
                    m = self.chat_layout.contentsMargins()
                    if m.bottom() != reserve:
                        self.chat_layout.setContentsMargins(m.left(), m.top(), m.right(), reserve)
            except RuntimeError:
                pass
        except RuntimeError:
            pass

    def _input_inertia_tick(self):
        """Called ~70fps while inertia is active for input field."""
        if not hasattr(self, 'input_field'):
            self._input_inertia_timer.stop()
            return

        sb = self.input_field.text_input.verticalScrollBar()
        self._input_inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._input_inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._input_inertia_timer.stop()
            self._input_inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._input_inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    def _invalidate_token_estimate(self):
        """Mark the cached system-prompt token estimate stale (skills were
        loaded/unloaded, mode switched, …) and refresh the label."""
        self._sys_tokens_dirty = True
        self._update_token_count()

    def _update_token_count(self):
        """Update the token estimate label whenever the input text changes."""
        try:
            if not hasattr(self, '_token_count_lbl') or not self._token_count_lbl.isVisible():
                return
            from systema.common.token_est import estimate_next_message_tokens, estimate_tokens
            text = self.input_field.toPlainText()
            hist = []
            sys_tokens = 0
            ai = getattr(self.controller, 'ai', None)
            if ai:
                hist = getattr(ai, 'chat_history', []) or getattr(ai, 'conversation_history', [])
                # The EFFECTIVE prompt (base + loaded skills + memory block) is
                # what the provider actually receives — measuring ai.system_prompt
                # missed loaded skills entirely (the pill sat at the fresh-session
                # value while the Debug window showed the real count). It is
                # rebuilt only when marked dirty (skill load/unload, 2s tick);
                # keystrokes reuse the cached number.
                if getattr(self, '_sys_tokens_dirty', True) or \
                        getattr(self, '_sys_tokens_cache', None) is None:
                    from systema.common.perf_monitor import span
                    with span("token_estimate.sys_prompt_rebuild"):
                        try:
                            _sys_prompt = ai._get_effective_system_prompt()
                        except Exception:
                            _sys_prompt = getattr(ai, 'system_prompt', '') or ''
                        self._sys_tokens_cache = estimate_tokens(_sys_prompt)
                    self._sys_tokens_dirty = False
                sys_tokens = self._sys_tokens_cache or 0
            total = estimate_next_message_tokens(text, hist) + sys_tokens
            lbl = f"~{total/1000:.1f}k token per request" if total >= 1000 else f"~{total} token per request"
            if total > 50000:
                color = "#FF6B6B"
            elif total > 20000:
                color = "#E8833A"
            elif total > 5000:
                color = "#8B949E"
            else:
                color = "#3D4450"
            self._token_count_lbl.setStyleSheet(
                f"QLabel {{ color: {color}; font-size: 9px; background: transparent; padding: 0 4px; }}")
            self._token_count_lbl.setText(lbl)
        except Exception:
            pass

    def _session_intro_showing(self) -> bool:
        """True while the empty-session opener (greeting banner) is up.

        Drives the mid-window input position. Kept cheap — this runs on every
        overlay reposition, which means every resize frame.
        """
        try:
            for entry in self.message_widgets:
                if entry.get('_intro'):
                    return True
        except (AttributeError, RuntimeError):
            pass
        return False

    def _position_input_handles(self):
        """Re-glue the two width-resize grab bars to the pill's side gutters.

        Each handle also tracks its parent's resizes on its own (see
        _InputResizeHandle), so this is the eager path, not the only one — a
        container that resizes itself still keeps its bars.
        """
        handles = getattr(self, '_input_resize_handles', None)
        if not handles:
            return
        for hd in handles:
            hd.reposition()

    # ── Retired: the pinned-image overlay ────────────────────────────────
    # An "image attached" card used to float above the input box, holding the
    # only record that a picture was live. It was pure UI state: nothing was
    # written to the session, so a reload lost every image, and the model only
    # ever received them because send_message() re-supplied the whole pinned
    # list on each send — which is exactly why any turn that did not go through
    # that path (every work-mode continuation) lost them mid-conversation.
    #
    # Images are now ImageRefs on the history entry that owns them, rendered as
    # real bubbles in the transcript. See ui/chat/image_bubbles.py, which
    # supplies attach_images() / detach_image() / delete_image() and the
    # _show_image_preview() + clear_pinned_images() compatibility shims that
    # used to live here.

    def _handle_image_file_drop(self, path):
        """Prompt the user to attach an image file or insert its path as text."""
        from PyQt6.QtWidgets import QMessageBox
        import os
        file_name = os.path.basename(path)
        msg = QMessageBox(self)
        msg.setWindowTitle("Attach as Image?")
        msg.setText(f'Attach "{file_name}" as an image?')
        msg.setInformativeText(
            "Yes — send as image to the AI\n"
            "No — insert file path as text instead")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._show_image_preview(path)
        else:
            quoted = f'"{path}"' if self.should_quote_path(path) else path
            current = self.input_field.toPlainText()
            if current:
                self.input_field.text_input.setPlainText(current + "\n" + quoted)
            else:
                self.input_field.text_input.setPlainText(quoted)

    def browse_for_file(self):
        """Alternative to drag & drop - supports multiple files"""
        from PyQt6.QtWidgets import QFileDialog

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select File(s)",
            "",
            "All Files (*.*)"
        )

        if not file_paths:
            return

        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        image_files = [self.clean_file_path(p) for p in file_paths
                       if any(p.lower().endswith(ext) for ext in valid_extensions)]
        non_images  = [self.clean_file_path(p) for p in file_paths
                       if not any(p.lower().endswith(ext) for ext in valid_extensions)]

        if image_files:
            if len(image_files) == 1:
                self._handle_image_file_drop(image_files[0])
            else:
                self._handle_multiple_image_files_dialog(image_files)

        for file_path in non_images:
            if self.should_quote_path(file_path):
                file_path = f'"{file_path}"'
            current_text = self.input_field.toPlainText()
            if current_text:
                self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
            else:
                self.input_field.text_input.setPlainText(file_path)

    def _handle_multiple_image_files_dialog(self, image_paths):
        """Show a checkbox dialog for multiple image files — attach as image or path."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QCheckBox, QPushButton, QLabel, QScrollArea, QWidget)

        dlg = QDialog(self)
        dlg.setWindowTitle("Attach Images")
        dlg.setMinimumWidth(480)
        dlg.setStyleSheet("""
            QDialog { background: #0D1117; color: #E6EDF3; }
            QLabel  { color: #E6EDF3; font-size: 12px; }
            QCheckBox { color: #E6EDF3; font-size: 12px; padding: 3px 0; }
            QPushButton {
                background: #21262D; border: 1px solid #30363D;
                border-radius: 6px; color: #E6EDF3;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background: #30363D; }
            QPushButton#primaryBtn {
                background: #1F6FEB; border-color: #388BFD;
            }
            QPushButton#primaryBtn:hover { background: #388BFD; }
        """)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 14, 16, 14)

        lay.addWidget(QLabel(f"Found {len(image_paths)} image file(s). Choose how to attach:"))

        # Scroll area with checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(280)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #21262D; border-radius: 6px; background: #161B22; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(10, 8, 10, 8)
        inner_lay.setSpacing(4)
        scroll.setWidget(inner)

        import os
        checkboxes = []
        for p in image_paths:
            cb = QCheckBox(os.path.basename(p))
            cb.setChecked(True)
            cb.setProperty("filepath", p)
            checkboxes.append(cb)
            inner_lay.addWidget(cb)

        lay.addWidget(scroll)

        # Select all / none row
        sel_row = QHBoxLayout()
        sel_all_btn  = QPushButton("Select All")
        sel_none_btn = QPushButton("Unselect All")
        sel_all_btn.clicked.connect(lambda: [cb.setChecked(True)  for cb in checkboxes])
        sel_none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(sel_none_btn)
        sel_row.addStretch()
        lay.addLayout(sel_row)

        lay.addWidget(QLabel("For checked files, attach as:"))

        # Action buttons
        btn_row = QHBoxLayout()
        img_btn  = QPushButton("Attach as Image(s)")
        img_btn.setObjectName("primaryBtn")
        path_btn = QPushButton("Insert Path(s)")
        cancel_btn = QPushButton("Cancel")

        btn_row.addWidget(img_btn)
        btn_row.addWidget(path_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        result = {"action": None}

        img_btn.clicked.connect(lambda: (result.__setitem__("action", "image"),  dlg.accept()))
        path_btn.clicked.connect(lambda: (result.__setitem__("action", "path"),   dlg.accept()))
        cancel_btn.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = [cb.property("filepath") for cb in checkboxes if cb.isChecked()]
        if not selected:
            return

        if result["action"] == "image":
            # ONE call, not one per file — images picked together belong in one
            # collage bubble, not several single-image bubbles stacked up.
            self.attach_images(selected)
        else:
            lines = []
            for p in selected:
                lines.append(f'"{p}"' if self.should_quote_path(p) else p)
            combined = "\n".join(lines)
            current = self.input_field.toPlainText()
            if current:
                self.input_field.text_input.setPlainText(current + "\n" + combined)
            else:
                self.input_field.text_input.setPlainText(combined)

    def set_input_enabled(self, enabled):
        """Gate SENDING, not typing.

        The text box stays live while the assistant is working so you can draft
        your next message instead of waiting with a dead cursor — the send
        button is what actually holds the message back, and Enter is gated on
        the same flag (see ChatWindow.send_message). Disabling the box as well
        just threw away keystrokes and stole focus.
        """
        self._send_allowed = bool(enabled)
        self.input_field.setEnabled(True)          # always typable
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.input_field.setPlaceholderText("Send a message... (Shift+Enter for new line)")
            # Restore focus so the user can type immediately without clicking
            self.input_field.text_input.setFocus()
        else:
            self.input_field.setPlaceholderText(
                "Processing Request... you can type ahead; Esc interrupts")
        # Mirror to Android phone if connected
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            _ab.set_input_enabled(enabled)

    def set_input_placeholder(self, text):
        """Update placeholder text on the input field."""
        self.input_field.setPlaceholderText(text)

    def _build_input_dock(self, chat_container):
        """Build the floating input pill + image preview/pinned areas and anchor
        the overlay. Extracted verbatim from init_ui (full-split pass, 2026-07-17)."""
        # ── Inline status + work labels ───────────────────────────────────────
        # These live INSIDE the input pill's bottom action row (added there
        # during input construction), so the thinking dots / "Working:" text take
        # no extra vertical space and never push the chat. Both auto-hide when
        # their text is cleared (InlineStatus).
        self.status_label = InlineStatus()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet("""
            QLabel#statusLabel {
                color: #C7CBD1;
                font-style: italic;
                font-size: 10px;
                background: transparent;
                padding: 0 4px;
            }
        """)
        self.status_label.hide()

        self._work_banner = InlineStatus()
        self._work_banner.setObjectName("workBanner")
        self._work_banner.setStyleSheet("""
            QLabel#workBanner {
                color: #7EB8F7;
                font-size: 10px;
                font-style: italic;
                background: transparent;
                padding: 0 4px;
            }
        """)
        self._work_banner.hide()
        # ─────────────────────────────────────────────────────────────────────

        # Input area
        input_container = QFrame()
        input_container.setObjectName("inputContainer")
        input_container.setStyleSheet("""
            QFrame#inputContainer {
                background-color: transparent;
                border-top: none;
            }
        """)

        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(14, 8, 14, 12)
        input_layout.setSpacing(0)
        # Keep the container's layout pinned to its content so an oversized
        # overlay geometry can never distribute extra height into the pill.
        input_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        # ── Pill-shaped input card ────────────────────────────────────────────
        combined_container = QFrame()
        combined_container.setObjectName("inputCard")
        # Vertical Maximum: the pill may never grow TALLER than its content, even
        # if the floating overlay is briefly given a larger rect on a snap-resize
        # (that stretch was making the empty pill balloon and clip off the bottom).
        combined_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        combined_container.setStyleSheet("""
            QFrame#inputCard {
                background-color: #1C2128;
                border: 1px solid #2D333B;
                border-radius: 18px;
            }
        """)

        combined_layout = QVBoxLayout(combined_container)
        combined_layout.setContentsMargins(0, 0, 0, 0)
        combined_layout.setSpacing(0)
        combined_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        # ── Text input area ───────────────────────────────────────────────────
        text_row = QWidget()
        text_row.setObjectName("inputTextRow")
        text_row.setStyleSheet("QWidget#inputTextRow { background: transparent; }")
        text_row_layout = QHBoxLayout(text_row)
        text_row_layout.setContentsMargins(16, 10, 16, 4)
        text_row_layout.setSpacing(0)
        text_row_layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)

        self.input_field = ResizableInput()
        self.input_field.text_input.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                color: #E8EAED;
                font-size: 13px;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                padding: 2px 0;
                line-height: 1.6;
            }
            QTextEdit:focus { background-color: transparent; }
        """)
        self.input_field.resize_handle.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #30363D;
                font-size: 6px;
                letter-spacing: 2px;
            }
            QLabel:hover {
                color: #8B949E;
                background-color: rgba(255,255,255,0.05);
                border-radius: 2px;
            }
        """)
        self.input_field.enterPressed.connect(self.send_message)
        self.input_field.text_input.textChanged.connect(self._update_token_count)
        self._suppress_input_sync = False
        self.input_field.text_input.textChanged.connect(self._on_input_changed_sync)
        text_row_layout.addWidget(self.input_field, 1)
        combined_layout.addWidget(text_row)

        # Install inertia scroll on the input field's viewport
        self.input_field.text_input.viewport().installEventFilter(self)

        # ── Bottom action row: [attach][mode]  ·····  [voice][interrupt][send] ──
        bottom_row = QWidget()
        bottom_row.setObjectName("inputBottomRow")
        bottom_row.setStyleSheet("QWidget#inputBottomRow { background: transparent; }")
        bottom_row_layout = QHBoxLayout(bottom_row)
        bottom_row_layout.setContentsMargins(10, 0, 10, 8)
        bottom_row_layout.setSpacing(4)

        # ── LEFT: attach + mode ───────────────────────────────────────────────
        # Icon overhaul (2026-07-21): every input-row action is a PAINTED,
        # BOXLESS glyph button (no background pill — user's aesthetic call);
        # hover feedback = glyph brighten + per-button personality.
        from systema.ui.widgets.painted_icons import (
            PaperclipButton, MicButton, MuteButton, StopButton, SendButton)
        browse_btn = PaperclipButton(30, tooltip="Attach file")
        browse_btn.clicked.connect(self.browse_for_file)
        bottom_row_layout.addWidget(browse_btn)

        # Session tools — the single menu (Compact / Clear / Files / Restore),
        # drawn as a painted message bubble (repurposed from the retired
        # execution-mode force button).
        from systema.ui.chat.window_controls import MessageButton
        self._session_tools_btn = MessageButton(30)
        self._session_tools_btn.setToolTip("Session tools")
        self._session_tools_btn.clicked.connect(self._show_session_menu)
        bottom_row_layout.addWidget(self._session_tools_btn)

        # ── Token estimate label ──────────────────────────────────────────────
        self._token_count_lbl = QLabel("~0 token per request")
        self._token_count_lbl.setStyleSheet(
            "QLabel { color: #3D4450; font-size: 9px; background: transparent; padding: 0 4px; }")
        self._token_count_lbl.setToolTip(
            "Estimated tokens for the next API request "
            "(System prompt + full conversation history + your input)")
        bottom_row_layout.addWidget(self._token_count_lbl)
        _show_tk = getattr(self.controller, 'settings', {}).get('show_token_count', True)
        self._token_count_lbl.setVisible(_show_tk)
        self._token_refresh_timer = QTimer(self)
        self._token_refresh_timer.setInterval(2000)
        # The periodic tick re-measures the effective system prompt too (mode
        # switches, memory-block growth); keystrokes reuse the cached number.
        self._token_refresh_timer.timeout.connect(self._invalidate_token_estimate)
        self._token_refresh_timer.start()
        # Loading/unloading a skill changes the next request immediately —
        # refresh the estimate right away instead of waiting for the tick.
        _tk_skill_mgr = getattr(self.controller, 'skill_manager', None)
        if _tk_skill_mgr is not None:
            try:
                _tk_skill_mgr.loaded_skills_changed.connect(self._invalidate_token_estimate)
            except Exception:
                pass

        # Voice status — used to live in the (now removed) header bar; same
        # attribute name so update_voice_status keeps working unchanged.
        self.voice_status_label = QLabel("")
        self.voice_status_label.setStyleSheet("""
                    QLabel {
                        font-size: 10px;
                        color: #9AA0A6;
                        margin: 0 8px;
                        background: transparent;
                    }
                """)
        bottom_row_layout.addWidget(self.voice_status_label)

        # Inline status + work-mode indicators (created earlier) live here, left
        # of the stretch — the thinking dots / "Working:" text sit in the pill.
        bottom_row_layout.addWidget(self._work_banner)
        bottom_row_layout.addWidget(self.status_label)

        bottom_row_layout.addStretch()

        # ── RIGHT: voice + interrupt + send (painted, boxless) ───────────────
        self.voice_btn_inline = MicButton(30, tooltip="Toggle voice mode")
        self.voice_btn_inline.clicked.connect(self.toggle_voice)
        bottom_row_layout.addWidget(self.voice_btn_inline)

        self.voice_interrupt_btn = MuteButton(30, tooltip="Interrupt voice")
        self.voice_interrupt_btn.clicked.connect(self.interrupt_voice)
        self.voice_interrupt_btn.hide()
        bottom_row_layout.addWidget(self.voice_interrupt_btn)

        self.interrupt_btn = StopButton(30, tooltip="Cancel AI response")
        self.interrupt_btn.clicked.connect(self.interrupt_response)
        self.interrupt_btn.hide()
        bottom_row_layout.addWidget(self.interrupt_btn)

        self.send_btn = SendButton(30, tooltip="Send")
        self.send_btn.clicked.connect(self.send_message)
        bottom_row_layout.addWidget(self.send_btn)

        # Retired: the image preview strip and the floating pinned-image
        # overlay. Both were UI-only records of an attachment; images now enter
        # the transcript directly (ui/chat/image_bubbles.py), so there is
        # nothing to preview before sending and nothing to pin above the box.

        combined_layout.addWidget(bottom_row)
        input_layout.addWidget(combined_container)

        self.input_container = input_container  # stored for glass background toggle
        self._input_card = combined_container    # pill; used to size the overlay
        self.input_container.installEventFilter(self)
        # The input is a FLOATING OVERLAY over the chat area (not a layout row):
        # the message scroll area fills the full height behind it, so the
        # transparent gaps around the solid pill reveal the chat, not the window
        # background/desktop. _position_input_overlay() anchors it to the bottom
        # and keeps the message list padded so nothing hides behind it.
        self._chat_container = chat_container
        input_container.setParent(chat_container)
        input_container.raise_()
        # Width-resize grab bars in the pill's side gutters (the 14px layout
        # margins) — drag either one to resize symmetrically around centre.
        self._input_resize_handles = [
            _InputResizeHandle(self, 'left', input_container),
            _InputResizeHandle(self, 'right', input_container),
        ]
        # Bottom fade: a mouse-transparent gradient strip glued just above the
        # pill — chat content dims as it slides underneath (clear on top, dim
        # near the input), in both glass and solid themes.
        self._chat_fade = _ChatBottomFade(chat_container)
        self._chat_fade.show()
        # Re-anchor the moment the chat area actually changes size (the window's
        # resizeEvent fires before the layout propagates the new width down to
        # chat_container, so reading its width there is stale — this fires after).
        chat_container.installEventFilter(self)
        # Re-anchor the instant auto-grow changes the pill height. This is a
        # DIRECT (non-deferred) connection fired right after setFixedHeight, so
        # the overlay is re-measured with the already-grown geometry and stays
        # bottom-anchored (growing upward) instead of spilling downward off the
        # window edge while a deferred reposition waited a frame.
        self.input_field.text_input.heightChanged.connect(self._position_input_overlay)
        QTimer.singleShot(0, self._position_input_overlay)

