"""
systema/ui/chat/event_cards.py
EventCardsMixin — tool/skill/file-op/memory event cards.
Extracted verbatim from chat_window.py (full-split pass, 2026-07-17).
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel,
                             QFrame, QMenu, QScrollArea, QApplication,
                             QGraphicsOpacityEffect, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QRect, QRectF, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PyQt6.QtGui import QAction, QCursor, QRegion, QPixmap, QPainter
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from systema.common.logger import _make_logger, _NoOpLogger
from systema.ui.chat.constants import *
from systema import APP_ROOT as _APP_ROOT
from systema.ui.widgets.code_blocks import CodeBlockWidget, TableBlockWidget

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


def _fmt_tok(n: int) -> str:
    """950 → '950', 1234 → '1.2k' (token-count tags on tool cards)."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class _ResizeGrip(QWidget):
    """A thin draggable strip under an expanded tool-card box: drag it to resize
    the target QTextEdit vertically, hard-clamped to [min_h, max_h] so the card
    can be squeezed compact or pulled bigger without ever glitching out."""

    def __init__(self, target, min_h=44, max_h=640, parent=None):
        super().__init__(parent)
        self._target = target
        self._min_h, self._max_h = min_h, max_h
        self._drag_y = None
        self._start_h = 0
        self.setFixedHeight(9)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setStyleSheet("background: transparent;")
        self.setToolTip("Drag to resize")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() / 2
        col = QColor(255, 255, 255, 55 if self._drag_y is None else 120)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(cx - 15, 3.0, 30, 2.5), 1.25, 1.25)
        p.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = event.globalPosition().y()
            try:
                self._start_h = self._target.height()
            except RuntimeError:
                self._start_h = self._min_h
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_y is not None:
            dy = event.globalPosition().y() - self._drag_y
            nh = int(max(self._min_h, min(self._max_h, self._start_h + dy)))
            try:
                self._target.setFixedHeight(nh)
            except RuntimeError:
                pass
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_y = None
        self.update()
        event.accept()


class _ElidedLabel(QLabel):
    """A single-line label that middle-elides its text to whatever width it is
    given (keeping the tail — i.e. the filename — visible), while still asking
    the layout for the FULL width when there's room. Used for file-op paths so a
    long path shrinks with the card instead of shoving the diff stats off-screen
    or hard-truncating the filename."""

    def __init__(self, full: str = "", mode=Qt.TextElideMode.ElideMiddle, parent=None):
        super().__init__(parent)
        self._full = full or ""
        self._mode = mode
        self.setToolTip(self._full)
        self._apply()

    def setFullText(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def _apply(self):
        w = self.width()
        if w <= 6:
            super().setText(self._full)
            return
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, self._mode, w))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def sizeHint(self):
        from PyQt6.QtCore import QSize
        fm = self.fontMetrics()
        h = super().sizeHint().height()
        return QSize(fm.horizontalAdvance(self._full) + 2, h)

    def minimumSizeHint(self):
        from PyQt6.QtCore import QSize
        h = super().minimumSizeHint().height()
        return QSize(48, h)


class EventCardsMixin:
    """UI-event card builders (mixed into ChatWindow)."""

    def add_skill_card_message(self, skill_name: str, loaded: bool):
        """
        Add a skill-status entity card to the chat message list.
        Shows the skill name, a loaded/unloaded badge, and an Unload/Load button
        for manual user control.
        """
        from PyQt6.QtWidgets import QSizePolicy

        # Determine styling based on state
        if loaded:
            badge_text = "● Loaded"
            badge_color = "#4CAF50"
            badge_bg = "#1A2B1A"
            action_text = "Unload"
            action_color = "#C0392B"
            action_bg = "#3C1A1A"
            action_hover = "#4A2020"
            icon = "⚡"
        else:
            badge_text = "○ Unloaded"
            badge_color = "#9AA0A6"
            badge_bg = "#21262D"
            action_text = "Load"
            action_color = "#4CAF50"
            action_bg = "#1A2B1A"
            action_hover = "#223322"
            icon = "⚡"

        card = QFrame()
        _tc = self._t()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                margin: 2px 40px;
            }}
        """)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(10)

        # Icon
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("color: #7C7CFF; font-size: 14px; background: transparent;")
        icon_lbl.setFixedWidth(18)
        card_layout.addWidget(icon_lbl)

        # Skill name
        name_lbl = QLabel(f"<b>{skill_name}</b>")
        name_lbl.setStyleSheet(f"color: #C8CAFF; font-size: 12px; background: transparent;")
        card_layout.addWidget(name_lbl, stretch=1)

        # Badge
        badge_lbl = QLabel(badge_text)
        badge_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_bg};
                color: {badge_color};
                border-radius: 4px;
                font-size: 10px;
                padding: 2px 8px;
            }}
        """)
        card_layout.addWidget(badge_lbl)

        # Action button
        action_btn = QPushButton(action_text)
        action_btn.setFixedHeight(24)
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {action_bg};
                color: {action_color};
                border: 1px solid {action_color};
                border-radius: 4px;
                font-size: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background-color: {action_hover};
            }}
        """)

        # Capture current state in closure
        _loaded = loaded
        _name = skill_name

        def _on_action():
            skill_mgr = None
            if hasattr(self, 'controller') and self.controller:
                skill_mgr = getattr(self.controller.ai, 'skill_manager', None)
            if skill_mgr is None:
                return
            if _loaded:
                ok, msg = skill_mgr.unload_skill(_name)
            else:
                ok, msg = skill_mgr.load_skill(_name)
            if ok:
                # Update badge and button to reflect new state
                new_loaded = not _loaded
                if new_loaded:
                    badge_lbl.setText("● Loaded")
                    badge_lbl.setStyleSheet(f"""
                        QLabel {{
                            background-color: #1A2B1A; color: #4CAF50;
                            border-radius: 4px; font-size: 10px; padding: 2px 8px;
                        }}
                    """)
                    action_btn.setText("Unload")
                    action_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: #3C1A1A; color: #C0392B;
                            border: 1px solid #C0392B; border-radius: 4px;
                            font-size: 10px; padding: 0 10px;
                        }}
                        QPushButton:hover {{ background-color: #4A2020; }}
                    """)
                else:
                    badge_lbl.setText("○ Unloaded")
                    badge_lbl.setStyleSheet(f"""
                        QLabel {{
                            background-color: #21262D; color: #9AA0A6;
                            border-radius: 4px; font-size: 10px; padding: 2px 8px;
                        }}
                    """)
                    action_btn.setText("Load")
                    action_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: #1A2B1A; color: #4CAF50;
                            border: 1px solid #4CAF50; border-radius: 4px;
                            font-size: 10px; padding: 0 10px;
                        }}
                        QPushButton:hover {{ background-color: #223322; }}
                    """)
                # Rewire button with new state
                action_btn.clicked.disconnect()
                _new_loaded_ref = [new_loaded]

                def _on_action_updated(loaded_ref=_new_loaded_ref):
                    smgr = None
                    if hasattr(self, 'controller') and self.controller:
                        smgr = getattr(self.controller.ai, 'skill_manager', None)
                    if smgr is None:
                        return
                    if loaded_ref[0]:
                        ok2, _ = smgr.unload_skill(_name)
                        if ok2:
                            loaded_ref[0] = False
                            badge_lbl.setText("○ Unloaded")
                            badge_lbl.setStyleSheet("""
                                QLabel {
                                    background-color: #21262D; color: #9AA0A6;
                                    border-radius: 4px; font-size: 10px; padding: 2px 8px;
                                }
                            """)
                            action_btn.setText("Load")
                            action_btn.setStyleSheet("""
                                QPushButton {
                                    background-color: #1A2B1A; color: #4CAF50;
                                    border: 1px solid #4CAF50; border-radius: 4px;
                                    font-size: 10px; padding: 0 10px;
                                }
                                QPushButton:hover { background-color: #223322; }
                            """)
                    else:
                        ok2, _ = smgr.load_skill(_name)
                        if ok2:
                            loaded_ref[0] = True
                            badge_lbl.setText("● Loaded")
                            badge_lbl.setStyleSheet("""
                                QLabel {
                                    background-color: #1A2B1A; color: #4CAF50;
                                    border-radius: 4px; font-size: 10px; padding: 2px 8px;
                                }
                            """)
                            action_btn.setText("Unload")
                            action_btn.setStyleSheet("""
                                QPushButton {
                                    background-color: #3C1A1A; color: #C0392B;
                                    border: 1px solid #C0392B; border-radius: 4px;
                                    font-size: 10px; padding: 0 10px;
                                }
                                QPushButton:hover { background-color: #4A2020; }
                            """)

                action_btn.clicked.connect(_on_action_updated)
            else:
                # Show brief error feedback on badge
                badge_lbl.setText(f"⚠ {msg[:30]}")
                badge_lbl.setStyleSheet("""
                    QLabel {
                        background-color: #3C2A00; color: #FFC107;
                        border-radius: 4px; font-size: 10px; padding: 2px 8px;
                    }
                """)

        action_btn.clicked.connect(_on_action)
        card_layout.addWidget(action_btn)

        # Outer wrapper for spacing
        outer = QWidget()
        outer.setStyleSheet("background: transparent;")
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 2, 0, 2)
        outer_layout.addWidget(card)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, outer)
        self._animate_message_in(outer, on_settled=lambda: self.scroll_to_widget(outer))

    def add_loaded_skills_card(self, save_to_history: bool = True):
        """RETIRED (2026-07-17, user request): the "Skills loaded" chat card is
        gone — skills state lives in the sidebar (⚡ Skills). Kept as a no-op
        so live callers and old sessions' skills_card ui_events resolve
        harmlessly on reload."""
        return

    def warn_loaded_skills_if_any(self):
        """RETIRED with the skills card — no more loaded-skills nag in chat."""
        return

    def add_work_execution_widget(self, code: str, output: str):
        """Add a collapsible code+output block to the chat for python interpreter execution."""
        from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QWidget, QSizePolicy
        from PyQt6.QtGui import QFont

        _tc = self._t()
        outer = QFrame()
        outer.setStyleSheet(f"""
                    QFrame {{
                        background: {_tc['base']};
                        border: 1px solid {_tc['border']};
                        border-radius: 10px;
                        margin: 2px 0;
                    }}
                """)
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # Header row
        header = QFrame()
        header.setStyleSheet(f"""
                    QFrame {{
                        background: {_tc['surface']};
                        border-top-left-radius: 10px;
                        border-top-right-radius: 10px;
                        border-bottom: 1px solid {_tc['border']};
                    }}
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(14, 8, 12, 8)
        header_lay.setSpacing(8)

        lbl = QLabel("Code executed")
        lbl.setStyleSheet(
            f"color: {_tc['accent']}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        header_lay.addWidget(lbl)
        header_lay.addStretch()

        toggle_btn = QPushButton("▶  Show")
        toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid transparent;
                border-radius: 5px; padding: 3px 10px;
                font-size: 11px; color: #8B949E;
            }
            QPushButton:hover { background: rgba(88,166,255,0.12); color: #58A6FF; border-color: rgba(88,166,255,0.28); }
        """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header)

        # Body (hidden by default)
        body = QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        body.hide()

        mono_font = QFont('Consolas', 10)
        if not mono_font.exactMatch():
            mono_font = QFont('Courier New', 10)

        # Code section
        if code.strip():
            code_lbl = QLabel("CODE")
            code_lbl.setStyleSheet("color: #8B949E; font-size: 10px; font-weight: 700; padding: 6px 14px 2px 14px; background: transparent; border: none;")
            body_lay.addWidget(code_lbl)
            from PyQt6.QtWidgets import QTextEdit
            code_edit = QTextEdit()
            code_edit.setPlainText(code)
            code_edit.setReadOnly(True)
            code_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            code_edit.setFont(mono_font)
            code_edit.setStyleSheet(f"QTextEdit {{ background: {_tc['base']}; color: #E6EDF3; border: none; padding: 8px 14px; }}")
            code_edit.setFrameShape(QTextEdit.Shape.NoFrame)
            code_edit.setFixedHeight(min(max(len(code.splitlines()) * 17 + 24, 60), 300))
            body_lay.addWidget(code_edit)

        # Output section
        if output.strip():
            sep = QFrame()
            sep.setStyleSheet("background: rgba(88,166,255,0.10); border: none;")
            sep.setFixedHeight(1)
            body_lay.addWidget(sep)
            out_lbl = QLabel("STDOUT / STDERR")
            out_lbl.setStyleSheet("color: #8B949E; font-size: 10px; font-weight: 700; padding: 6px 14px 2px 14px; background: transparent; border: none;")
            body_lay.addWidget(out_lbl)
            from PyQt6.QtWidgets import QTextEdit
            out_edit = QTextEdit()
            out_edit.setPlainText(output)
            out_edit.setReadOnly(True)
            out_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            out_edit.setFont(mono_font)
            out_edit.setStyleSheet(f"QTextEdit {{ background: {_tc['deep']}; color: #8FBC8F; border: none; padding: 8px 14px; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}")
            out_edit.setFrameShape(QTextEdit.Shape.NoFrame)
            out_edit.setFixedHeight(min(max(len(output.splitlines()) * 17 + 24, 60), 200))
            body_lay.addWidget(out_edit)

        outer_lay.addWidget(body)

        def _toggle():
            if body.isHidden():
                body.show()
                toggle_btn.setText("▼  Hide")
            else:
                body.hide()
                toggle_btn.setText("▶  Show")

        toggle_btn.clicked.connect(_toggle)

        # Work cards belong to the AI's turn — stack inside the shared shell.
        self._insert_turn_segment(outer)
        self.scroll_to_bottom()

    def _current_step_seq(self):
        """Monotonic id of the interpreter's current execution (or None). Lets the
        streaming live card and the permanent note dedup to ONE card per step no
        matter which creation signal wins the race."""
        try:
            return getattr(self.controller.ai.tool_manager.work.interpreter,
                           'step_seq', None)
        except Exception:
            return None

    def start_live_output(self, code: str, annotation: str = None):
        """Open the REAL code-execution card in streaming mode (no separate
        console anymore): the card appears expanded with an empty output box and
        update_live_output() types stdout/stderr into it live. The next
        add_code_execution_note() (or end_live_output's fallback) finalizes the
        SAME card in place — output frozen, token tag set, Clear button enabled,
        collapsed, and persisted to history."""
        # Finalize any still-pending live card before opening a new one.
        if getattr(self, '_live_card', None) is not None:
            lc = self._live_card
            self._finalize_live_card(lc.get('code', ''), lc.get('last', '') or '',
                                     lc.get('annotation'))
        if annotation is None:
            try:
                annotation = self.controller.ai.tool_manager.work.interpreter.last_annotation or ""
            except Exception:
                annotation = ""
        # If the permanent note for THIS execution already rendered (its signal
        # won the race), opening a live card now would stack an empty duplicate
        # below it — skip.
        seq = self._current_step_seq()
        if seq is not None and seq == getattr(self, '_last_finalized_step', None):
            return
        # Build the card in live mode — it stores itself in self._live_card.
        self.add_code_execution_note(code, "", save_to_history=False,
                                     annotation=annotation, live=True)

    def update_live_output(self, text: str):
        """Stream the latest stdout/stderr into the live card's output box
        (no-op if unchanged) — the 'typing' effect IS the growing stdout."""
        lc = getattr(self, '_live_card', None)
        if lc is None:
            return
        if text == lc.get('last'):
            return
        lc['last'] = text
        out = lc.get('out_edit')
        if out is None:
            return
        try:
            sb = out.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            out.setPlainText(text)
            _n = len(text.splitlines()) or 1
            out.setFixedHeight(min(max(_n * 17 + 20, 44), 220))
            if at_bottom:
                sb.setValue(sb.maximum())
        except RuntimeError:
            pass

    def end_live_output(self):
        """Streaming finished. The card STAYS — the next add_code_execution_note()
        finalizes it (both call orders are handled). If no permanent note ever
        arrives (e.g. work aborted), a short fallback finalizes it with whatever
        streamed so the card never lingers half-live / unpersisted."""
        lc = getattr(self, '_live_card', None)
        if lc is None:
            # Legacy: tear down any stale transient console from older sessions.
            w = getattr(self, '_live_exec_widget', None)
            if w is not None:
                try:
                    w.setParent(None)
                    w.deleteLater()
                except Exception:
                    pass
                self._live_exec_widget = None
            return
        lc['ended'] = True
        try:
            QTimer.singleShot(2500, self._finalize_live_fallback)
        except Exception:
            pass

    def _finalize_live_fallback(self):
        """Finalize a live card that ended but never got a permanent note."""
        lc = getattr(self, '_live_card', None)
        if lc is None or not lc.get('ended'):
            return
        self._finalize_live_card(lc.get('code', ''), lc.get('last', '') or '',
                                 lc.get('annotation'))

    def _finalize_live_card(self, code: str, output: str, annotation=None):
        """Freeze a streaming live card into its permanent form: final output,
        token tag, enabled Clear button, collapsed detail, persisted to history."""
        lc = getattr(self, '_live_card', None)
        if lc is None:
            return
        self._live_card = None
        self._last_finalized_step = lc.get('step_seq')   # this step now has a card
        from systema.common.token_est import estimate_tokens
        try:
            oe = lc.get('out_edit')
            if oe is not None:
                oe.setPlainText(output if output.strip() else "(no output)")
                _n = len(output.splitlines()) or 1
                oe.setFixedHeight(min(max(_n * 17 + 20, 44), 130))
            lc['out_ref']['v'] = output
            tl = lc.get('tok_lbl')
            if tl is not None:
                tl.setText(f"~{_fmt_tok(estimate_tokens(output))} tok")
            stub = (output.strip() == "Output cleared by the user"
                    or output.strip().startswith("[Compacted]"))
            cb = lc.get('clear_btn')
            if cb is not None:
                cb.setVisible(bool(output.strip()) and not stub)
                cb.setEnabled(bool(output.strip()) and not stub)
            # Collapse to the compact note (streaming showed it expanded).
            det = lc.get('detail')
            tb = lc.get('toggle_btn')
            if det is not None:
                det.hide()
            if tb is not None:
                tb.setText("▶")
        except RuntimeError:
            pass
        # Persist now (live cards are built with save_to_history=False).
        # Prefer the annotation the live card CAPTURED at start_live_output over
        # the one passed in — the permanent-note callers pass None, which would
        # otherwise persist _annotation=None and make every reloaded card read
        # the "Code executed" fallback.
        _persist_annotation = annotation or lc.get('annotation') or ""
        try:
            self.controller.ai.conversation_history.append({
                'role': 'ui_event',
                'content': 'Code executed',
                '_code': code,
                '_output': output,
                '_annotation': _persist_annotation,
            })
        except Exception:
            pass

    def add_code_execution_note(self, code: str, output: str, save_to_history: bool = True,
                                annotation: str = None, live: bool = False):
        """Compact inline code-execution note (claude.ai-style borderless header).
        Persists to conversation_history as a ui_event so it survives reloads.

        live=True builds the card in STREAMING mode: the output box starts empty
        and EXPANDED, the Clear button is hidden until there is output, and the
        card is NOT persisted yet — update_live_output() streams into it and
        _finalize_live_card() freezes it. A non-live call while a live card is
        pending REUSES that card instead of stacking a duplicate."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QTextEdit
        from PyQt6.QtGui import QFont

        # Reuse a pending streaming card instead of adding a second permanent one.
        if not live and getattr(self, '_live_card', None) is not None:
            self._finalize_live_card(code, output, annotation)
            return

        _tc = self._t()

        # ── Outer wrapper — flush with the turn's text segments (no padding:
        #    the shell/segments own the spacing now) ────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet("QFrame { background-color: transparent; padding: 0px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header — BORDERLESS faint label (claude.ai-style): reads like a
        #    muted line of the response until clicked open ────────────────────
        header = QFrame()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.setStyleSheet("""
                    QFrame { background: transparent; border: none; border-radius: 6px; }
                    QFrame:hover { background: rgba(255,255,255,0.05); }
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 6, 2)
        header_lay.setSpacing(6)

        icon_lbl = QLabel(">_")
        icon_lbl.setStyleSheet(
            "color: #6E7681; font-size: 10px; font-weight: bold; "
            "font-family: Consolas, monospace; background: transparent; border: none;")
        header_lay.addWidget(icon_lbl)

        # Use the Working: annotation as the label if available
        if annotation is None:
            try:
                annotation = self.controller.ai.tool_manager.work.interpreter.last_annotation or ""
            except Exception:
                annotation = ""
        header_label = f"{annotation}" if annotation else "Code executed"
        first_line = (code.strip().splitlines()[0] if code.strip() else "no code")
        preview = first_line[:40] + ("…" if len(first_line) > 40 else "")
        summary_lbl = QLabel(
            f"<span style='color:#9AA0A6;font-size:11px;'>{header_label}</span>"
            f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
            f"<span style='font-family:monospace;font-size:10px;color:#5F6368;'>{preview}</span>")
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl)

        # Output token estimate — the card wears its context cost.
        from systema.common.token_est import estimate_tokens
        tok_lbl = QLabel(f"~{_fmt_tok(estimate_tokens(output))} tok")
        tok_lbl.setStyleSheet(
            "background: transparent; border: none; font-size: 9px; color: #5F6368;")
        header_lay.addWidget(tok_lbl)

        toggle_btn = QLabel("▶")
        toggle_btn.setStyleSheet(
            "background: transparent; border: none; font-size: 8px; color: #5F6368;")
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Expandable detail (hidden by default) ─────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(0, 4, 0, 0)
        detail_lay.setSpacing(4)

        mono = QFont('Consolas', 9)
        if not mono.exactMatch():
            mono = QFont('Courier New', 9)

        if code.strip():
            from PyQt6.QtWidgets import QTextEdit as _QTE
            from systema.ui.widgets.code_blocks import CodeSyntaxHighlighter
            code_edit = _QTE()
            code_edit.setPlainText(code.strip())
            code_edit.setReadOnly(True)
            code_edit.setLineWrapMode(_QTE.LineWrapMode.NoWrap)
            code_edit.setFont(mono)
            # Same VS-Code-dark scheme as chat code blocks — the input reads
            # as real code, not a gray dump.
            code_edit._highlighter = CodeSyntaxHighlighter(code_edit.document(), 'python')
            code_edit.setStyleSheet(
                f"QTextEdit {{ background: {_tc['base']}; color: #E6EDF3; "
                f"border: 1px solid {_tc['border']}; border-radius: 8px; padding: 8px 12px; }}")
            code_edit.setFrameShape(_QTE.Shape.NoFrame)
            _n = len(code.strip().splitlines())
            # Compact default (was 260 cap); a drag grip below lets the user pull
            # it taller. Clamps keep the resize from glitching out.
            code_edit.setFixedHeight(min(max(_n * 17 + 20, 44), 150))
            detail_lay.addWidget(code_edit)
            detail_lay.addWidget(_ResizeGrip(code_edit, min_h=44, max_h=680))

        out_edit = None
        clear_btn = None
        out_ref = {'v': output}
        if output.strip() or live:
            from PyQt6.QtWidgets import QTextEdit as _QTE2
            out_edit = _QTE2()
            out_edit.setPlainText(output.strip())
            out_edit.setReadOnly(True)
            out_edit.setLineWrapMode(_QTE2.LineWrapMode.WidgetWidth)
            out_edit.setFont(mono)
            out_edit.setStyleSheet(
                f"QTextEdit {{ background: {_tc['deep']}; color: #8FBC8F; "
                f"border: 1px solid {_tc['border']}; border-radius: 8px; padding: 8px 12px; }}")
            out_edit.setFrameShape(_QTE2.Shape.NoFrame)
            if live and not output.strip():
                out_edit.setPlaceholderText("Running…")
            _n = len(output.strip().splitlines()) or 1
            # Compact default (was 200 cap); drag grip below extends it.
            out_edit.setFixedHeight(min(max(_n * 17 + 20, 44), 130))
            detail_lay.addWidget(out_edit)
            detail_lay.addWidget(_ResizeGrip(out_edit, min_h=44, max_h=680))

            # ── Clear output — token-saving: replaces this result with a stub
            #    in the LIVE history AND the session savefile. Reads out_ref so
            #    a streaming card clears its FINAL (finalized) output. ─────────
            _already_stub = (output.strip() == "Output cleared by the user"
                             or output.strip().startswith("[Compacted]"))
            clear_btn = QPushButton("Clear output")
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(33, 38, 45, 0.78); color: #8B949E;
                    font-size: 10px; padding: 2px 9px;
                    border: 1px solid {_tc['border']}; border-radius: 4px;
                }}
                QPushButton:hover {{ color: #E06060; border-color: #E06060; }}
            """)
            clear_btn.setToolTip(
                "Replace this output with 'Output cleared by the user' in the\n"
                "conversation history and session file — frees its tokens.")

            def _clear_output(checked=False, _btn=clear_btn,
                              _edit=out_edit, _tok=tok_lbl, _ref=out_ref):
                try:
                    n = self.controller.rewrite_tool_output(
                        _ref['v'], "Output cleared by the user")
                except Exception:
                    n = 0
                _edit.setPlainText("Output cleared by the user")
                _edit.setFixedHeight(56)
                _tok.setText("~7 tok")
                _btn.setText("Cleared" if n else "Cleared (no match)")
                _btn.setEnabled(False)
            clear_btn.clicked.connect(_clear_output)

            _cb_row = QHBoxLayout()
            _cb_row.setContentsMargins(0, 0, 0, 0)
            _cb_row.addStretch()
            _cb_row.addWidget(clear_btn)
            detail_lay.addLayout(_cb_row)
            # Nothing to clear yet (streaming) or a stub — hide the button.
            if _already_stub or (live and not output.strip()):
                clear_btn.setVisible(False)
                clear_btn.setEnabled(False)

        outer_lay.addWidget(detail)

        def _toggle():
            if detail.isHidden():
                detail.show()
                toggle_btn.setText("▼")
            else:
                detail.hide()
                toggle_btn.setText("▶")

        header.mousePressEvent = lambda e: _toggle()

        # Live cards open EXPANDED so the streaming output is visible; they
        # collapse to the compact note when finalized.
        if live:
            detail.show()
            toggle_btn.setText("▼")

        # ── Join the AI turn group (creates the shell if the turn opens with
        #    a card — e.g. reloads, where work narration is suppressed) ───────
        g = self._insert_turn_segment(message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track in message_widgets ──────────────────────────────────────────
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'code_exec',
            'content_wrapper': header,
            'group_row': g['row'],
        })

        # ── Live: remember the streaming refs so update_live_output() can type
        #    into this card and _finalize_live_card() can freeze it. ──────────
        if live:
            self._live_card = {
                'widget': message_widget, 'out_edit': out_edit,
                'tok_lbl': tok_lbl, 'toggle_btn': toggle_btn, 'detail': detail,
                'clear_btn': clear_btn, 'out_ref': out_ref, 'code': code,
                'annotation': annotation, 'last': None, 'ended': False,
                'step_seq': self._current_step_seq(),
            }

        # ── Persist to conversation_history so session save/load works ────────
        if save_to_history and not live:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    'content': 'Code executed',
                    '_code': code,
                    '_output': output,
                    '_annotation': annotation,
                })
            except Exception:
                pass
            # This is the permanent card for the current step built WITHOUT a live
            # card (its streaming signal lost the race, or never fired) — mark the
            # step finalized so a late start_live_output() won't stack a duplicate.
            self._last_finalized_step = self._current_step_seq()

    def add_file_op_card(self, info: dict, save_to_history: bool = True):
        """Compact file-operation card (read_file / edit_file / write_file):

            ± ~parent/file.py   +29  −89   net −60      edit_file   [▶ Diff]

        Green added / red removed counts, net colored by sign; reads show the
        line range instead. Expands to the unified diff (or the read window).
        Persisted as a ui_event (_type 'file_op') so it survives reloads."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QTextEdit

        _tc = self._t()
        tool = info.get('tool', 'edit_file')
        display = info.get('display') or info.get('path', '')
        added = info.get('added')
        removed = info.get('removed')
        detail = info.get('detail', '')
        created = bool(info.get('created'))
        read_range = info.get('read_range', '')
        rejected = bool(info.get('rejected'))

        GREEN, RED = "#3FB950", "#F85149"
        MUTED, CTX = "#8B949E", "#C9D1D9"
        MONO = "'Consolas','Cascadia Mono','SF Mono',Menlo,monospace"
        icon_map = {'read_file': '›', 'edit_file': '±', 'write_file': '+',
                    'grep': '⌕'}
        read_like = tool in ('read_file', 'grep')

        message_widget = QFrame()
        message_widget.setStyleSheet("QFrame { background-color: transparent; padding: 0px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # Header — BORDERLESS faint label (claude.ai-style): a muted line that
        # blends into the response until expanded.
        header = QFrame()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header.setStyleSheet("""
                    QFrame { background: transparent; border: none; border-radius: 6px; }
                    QFrame:hover { background: rgba(255,255,255,0.05); }""")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 6, 2)
        header_lay.setSpacing(6)

        icon_lbl = QLabel(icon_map.get(tool, '±'))
        icon_lbl.setStyleSheet(
            "color: #6E7681; font-size: 12px; font-weight: 700;"
            " background: transparent; border: none;")
        icon_lbl.setFixedWidth(14)
        header_lay.addWidget(icon_lbl)

        # ── Path — its OWN elided label so it can shrink with the card without
        #    ever eating the diff stats. Middle-elide keeps the filename; the
        #    full path lives in the tooltip. ─────────────────────────────────
        path_lbl = _ElidedLabel(display)
        path_lbl.setStyleSheet(
            "color:#9AA0A6; font-size:11px; background:transparent; border:none;")
        path_lbl.setToolTip(info.get('path', '') or display)
        # No stretch: the header HUGS its content (like the >_ code-exec rows) so
        # the stats sit right beside the path instead of floating to the far
        # edge. The path is the shrinkable one — it elides only when the whole
        # row is squeezed past the bubble width cap.
        path_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        header_lay.addWidget(path_lbl)
        header_lay.addSpacing(12)

        # ── Stats cluster — a SEPARATE, never-wrapping label so +N −N net ±N
        #    always renders regardless of how narrow the card gets. ─────────
        if rejected:
            stats_html = (f"<span style='color:{RED};font-size:10px;font-weight:600;'>"
                          f"rejected</span>")
        elif read_like:
            stats_html = (f"<span style='color:#8B949E;font-size:10px;'>{read_range}</span>"
                          if read_range else "")
        else:
            net = (added or 0) - (removed or 0)
            net_color = GREEN if net >= 0 else RED
            net_txt = f"+{net}" if net >= 0 else str(net)
            bits = []
            if added is not None:
                bits.append(f"<span style='color:{GREEN};font-size:11px;"
                            f"font-weight:600;'>+{added}</span>")
            if removed is not None:
                bits.append(f"<span style='color:{RED};font-size:11px;"
                            f"font-weight:600;'>−{removed}</span>")
            bits.append(f"<span style='color:#5F6368;font-size:11px;'>·</span>"
                        f"<span style='color:{net_color};font-size:11px;'> net {net_txt}</span>")
            if created:
                bits.append(f"<span style='color:{GREEN};font-size:10px;'>&nbsp;new file</span>")
            stats_html = "&nbsp;&nbsp;".join(bits)

        if stats_html:
            stats_lbl = QLabel(stats_html)
            stats_lbl.setTextFormat(Qt.TextFormat.RichText)
            stats_lbl.setStyleSheet("background: transparent; border: none;")
            stats_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            header_lay.addWidget(stats_lbl)

        tool_lbl = QLabel(tool)
        tool_lbl.setStyleSheet(
            "color:#5F6368; font-size:10px; background:transparent; border:none;")
        tool_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        header_lay.addWidget(tool_lbl)

        toggle_btn = QLabel("▶")
        toggle_btn.setStyleSheet(
            "background: transparent; border: none; font-size: 8px; color: #5F6368;")
        header_lay.addWidget(toggle_btn)
        # AlignLeft + the header's Maximum policy = hug-to-content, left-packed
        # (matches the code-exec rows; kills the far-right stats float).
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        body = QTextEdit()
        body.setReadOnly(True)
        import html as _html
        _detail = detail or "(no detail)"
        if read_like:
            # Plain content (file window or search results) — no diff coloring.
            body.setHtml(
                f"<pre style=\"margin:0;font-family:{MONO};font-size:12px;"
                f"line-height:1.4;color:{CTX};white-space:pre;\">"
                f"{_html.escape(_detail)}</pre>")
        else:
            # Unified diff — colorize per line so it reads like a real diff.
            _rows = []
            for _ln in _detail.split('\n'):
                _e = _html.escape(_ln) or '&nbsp;'
                if _ln.startswith('+++') or _ln.startswith('---'):
                    _c, _w = MUTED, '700'
                elif _ln.startswith('@@'):
                    _c, _w = _tc['accent'], '600'
                elif _ln.startswith('+'):
                    _c, _w = GREEN, '400'
                elif _ln.startswith('-'):
                    _c, _w = RED, '400'
                else:
                    _c, _w = CTX, '400'
                _rows.append(f"<span style=\"color:{_c};font-weight:{_w};\">{_e}</span>")
            body.setHtml(
                f"<pre style=\"margin:0;font-family:{MONO};font-size:12px;"
                f"line-height:1.4;white-space:pre;\">" + "\n".join(_rows) + "</pre>")
        body.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Compact default; the drag grip below extends it (clamped).
        _bn = len(_detail.split('\n'))
        body.setFixedHeight(min(max(_bn * 17 + 20, 44), 170))
        body.setStyleSheet(f"""
                    QTextEdit {{
                        background: {_tc['base']}; border: 1px solid {_tc['border']};
                        border-radius: 8px; margin-top: 4px; padding: 8px;
                    }}""")
        body.hide()
        outer_lay.addWidget(body)
        body_grip = _ResizeGrip(body, min_h=44, max_h=680)
        body_grip.hide()
        outer_lay.addWidget(body_grip)

        def _toggle():
            showing = body.isVisible()
            body.setVisible(not showing)
            body_grip.setVisible(not showing)
            toggle_btn.setText("▶" if showing else "▼")
        header.mousePressEvent = lambda e: _toggle()

        # Cap to the responsive bubble width so the card shrinks with the window
        # (and never overflows a narrow viewport); _reflow_bubbles keeps it synced.
        header.setMaximumWidth(self._bubble_max_width())
        # File-op cards belong to the AI's turn — stack inside the shared shell.
        g = self._insert_turn_segment(message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'file_op',
            'content_wrapper': header,
            'main_container_widget': header,
            'group_row': g['row'],
        })

        if save_to_history:
            try:
                slim = dict(info)
                slim['detail'] = (detail or "")[:20000]
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    'content': f"± {tool} {display}",
                    '_type': 'file_op',
                    '_file_op': slim,
                })
            except Exception:
                pass

    def add_memory_context_widget(self, context_id: str, memories: list,
                                   save_to_history: bool = True):
        """Render a memory-context card with a Detach button.

        Visually distinct from code execution notes — uses a brain icon and
        amber/gold accent so users know it's memory, not code.
        When Detach is clicked the widget animates out AND the corresponding
        ui_event is removed from conversation_history via the controller.

        Parameters
        ----------
        context_id  : short UUID-derived string stored on the ui_event
        memories    : list of raw memory strings shown in the card
        save_to_history : False when replaying from a loaded session (entry
                          already exists in conversation_history)
        """
        if not context_id or not isinstance(context_id, str):
            return

        from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout,
                                     QPushButton, QLabel)

        _tc = self._t()

        # Follow the active theme
        _mem_accent = _tc['accent']
        _mem_bg = _tc['elevated']
        _mem_border = _tc['border']

        # ── Outer wrapper ──────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet(
            "QFrame { background-color: transparent; padding: 4px 16px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header row (always visible) ────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
                    QFrame {{
                        background-color: {_mem_bg};
                        border: 1px solid {_mem_border};
                        border-radius: 8px;
                    }}
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 6, 10, 6)
        header_lay.setSpacing(8)

        icon_lbl = QLabel("🧠")
        icon_lbl.setStyleSheet(
            "font-size: 13px; background: transparent; border: none;")
        icon_lbl.setFixedWidth(18)
        header_lay.addWidget(icon_lbl)

        # Preview: first memory title, trimmed
        preview_text = memories[0][:72] + ("…" if memories and len(memories[0]) > 72 else "") \
            if memories else "Memory recalled"
        count_label = f" +{len(memories) - 1} more" if len(memories) > 1 else ""

        summary_lbl = QLabel(
            f"<span style='color:{_mem_accent};font-size:11px;font-weight:600;'>"
            f"Memory recalled</span>"
            f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
            f"<span style='font-size:10px;color:#8B949E;'>{preview_text}</span>"
            f"<span style='font-size:10px;color:{_mem_accent};'>{count_label}</span>")
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)

        # ── Show / Hide toggle ─────────────────────────────────────────────
        toggle_btn = QPushButton("▶ Show")
        toggle_btn.setFixedSize(58, 20)
        toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {_tc['border']};
                border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
            }}
            QPushButton:hover {{ color: {_mem_accent}; border-color: {_mem_accent}; }}
        """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)

        # ── Detach button ──────────────────────────────────────────────────
        detach_btn = QPushButton("⊗ Detach")
        detach_btn.setFixedSize(62, 20)
        detach_btn.setToolTip(
            "Remove this memory from the conversation context.\n"
            "The AI will no longer see it in this session.")
        detach_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid #6B3030;
                border-radius: 4px; font-size: 10px; color: #8B6060; padding: 0 6px;
            }}
            QPushButton:hover {{ color: #E06060; border-color: #E06060; }}
        """)
        detach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(detach_btn)

        outer_lay.addWidget(header)

        # ── Expandable memory list ─────────────────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(4, 4, 4, 0)
        detail_lay.setSpacing(4)

        for mem_text in memories:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background: {_mem_bg};
                    border: 1px solid {_mem_accent}33;
                    border-radius: 6px;
                    padding: 0px;
                }}
            """)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(10, 6, 10, 6)
            lbl = QLabel(mem_text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"font-size: 11px; color: {_mem_accent}; background: transparent; border: none;")
            row_lay.addWidget(lbl)
            detail_lay.addWidget(row)

        outer_lay.addWidget(detail)

        # ── Toggle logic ───────────────────────────────────────────────────
        def _toggle():
            if detail.isHidden():
                detail.show()
                toggle_btn.setText("▼ Hide")
            else:
                detail.hide()
                toggle_btn.setText("▶ Show")

        toggle_btn.clicked.connect(_toggle)

        # ── Detach logic ───────────────────────────────────────────────────
        def _detach(_cid=context_id, _w=message_widget):
            try:
                self.controller.detach_memory_context(_cid)
            except Exception as e:
                log.error(f"[ChatWindow._detach] Error calling detach_memory_context: {e}")
            # Remove widget from message_widgets tracking list
            self.message_widgets[:] = [
                mw for mw in self.message_widgets if mw.get('widget') is not _w
            ]
            # Animate out and destroy
            self._animate_message_out(_w, callback=_w.deleteLater)

        detach_btn.clicked.connect(lambda: _detach())

        # ── Insert before the trailing spacer ─────────────────────────────
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(
            message_widget,
            on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track in message_widgets ───────────────────────────────────────
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'memory_context',
            'context_id': context_id,
            'content_wrapper': header,
            '_toggle_btn': toggle_btn,
        })

        # ── Persist to history (only on first insertion, not on reload) ────
        if save_to_history:
            try:
                import uuid as _uuid
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    '_type': 'memory_context',
                    '_memory_context_id': context_id,
                    'content': '',          # content is stored per-memory
                    '_memories_preview': memories,
                })
            except Exception:
                pass
