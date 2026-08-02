"""
systema/ui/chat/event_cards.py
EventCardsMixin — tool/skill/file-op/memory event cards.
Extracted verbatim from chat_window.py (full-split pass, 2026-07-17).
"""
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QLabel, QFrame, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, QRectF
from PyQt6.QtGui import QPainter
from PyQt6.QtGui import QColor, QTextCursor
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


def _fmt_tok(n: int) -> str:
    """950 → '950', 1234 → '1.2k' (token-count tags on tool cards)."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ── Card icons ───────────────────────────────────────────────────────────────
# One rule for the whole set: the glyph depicts the ACTION, never the tool —
# every card already prints its tool name right beside the icon, so spending the
# icon on it again said nothing. Two cards may share an icon when they really do
# the same thing (the painted magnifier = searching, on both grep and
# web_search; ▤ a page you read; ⊘ refused); what they may NOT do is share one
# across OPPOSITE actions, which is what load/unload used to.
#
# Pure functions of the card's own persisted data: a card rebuilt from a
# reloaded session derives the identical glyph, so there is no icon state to
# save and none that can drift.

def file_op_glyph(tool: str, added=None, removed=None,
                  created: bool = False, rejected: bool = False) -> str | None:
    """read/grep → what you looked at; write-like → what happened to the file.

    Returns None for ops whose icon is PAINTED rather than typed — today that
    is grep alone (painted_icons.SearchGlyph, the same magnifier the web_search
    card uses). The caller builds the widget; keeping the decision here means
    there is still ONE place that answers "which icon does this op get".
    """
    if rejected:
        return '⊘'                                  # refused, nothing written
    if tool == 'grep':
        return None                                 # painted magnifier
    if tool == 'read_file':
        return '▤'
    if created:
        return '⊕'                                  # brought into existence
    net = (added or 0) - (removed or 0)
    return '+' if net > 0 else ('−' if net < 0 else '±')


def skill_action_glyph(action: str, ok: bool = True) -> str:
    """Loading pulls instructions IN, unloading pushes them OUT — so it points."""
    if not ok:
        return '⊘'
    return '↧' if action == 'load' else '↥'


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
        name_lbl.setStyleSheet("color: #C8CAFF; font-size: 12px; background: transparent;")
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
                else:
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

        self._insert_chat_row(outer)
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
        from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget
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
            code_edit.setAcceptDrops(False)   # let file drags fall through to the chat surface
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
            out_edit.setAcceptDrops(False)   # let file drags fall through to the chat surface
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
            _lh = max(12, round(17 * self._get_msg_font_size() / 13.0))
            out.setFixedHeight(min(max(_n * _lh + 20, 44), 220))
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

    def finalize_code_step(self, code: str, output: str, annotation: str = ""):
        """Freeze ONE interpreter step's card with THAT step's own output.

        Called from ToolManager._deliver_work_step_output the moment the step's
        observation exists — the authoritative pairing. A pending live card is
        the card for this step, so it is frozen in place; if none is open (the
        step never streamed, e.g. a rejected or capability-blocked call) a
        permanent card is added instead.

        After this, `_last_finalized_step` marks the step as carded, so the
        post-batch add_code_execution_note() — which carries the whole batch's
        COMBINED observation — cannot overwrite this card or stack a duplicate.
        """
        lc = getattr(self, '_live_card', None)
        if lc is not None:
            self._finalize_live_card(code, output, annotation or lc.get('annotation'))
            return
        # No live card: the step never reached execution (rejected / disabled /
        # policy-blocked), so step_seq did not advance — add the card
        # unconditionally (two rejected calls in one batch are two cards), then
        # mark the step carded so the post-batch combined note stays suppressed.
        self.add_code_execution_note(code, output, annotation=annotation or None,
                                     authoritative=True)
        self._last_finalized_step = self._current_step_seq()

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
                _lh = max(12, round(17 * self._get_msg_font_size() / 13.0))
                oe.setFixedHeight(min(max(_n * _lh + 20, 44), 130))
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
                                annotation: str = None, live: bool = False,
                                authoritative: bool = False):
        """Compact inline code-execution note (claude.ai-style borderless header).
        Persists to conversation_history as a ui_event so it survives reloads.

        live=True builds the card in STREAMING mode: the output box starts empty
        and EXPANDED, the Clear button is hidden until there is output, and the
        card is NOT persisted yet — update_live_output() streams into it and
        _finalize_live_card() freezes it. A non-live call while a live card is
        pending REUSES that card instead of stacking a duplicate."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel
        from PyQt6.QtGui import QFont

        # Reuse a pending streaming card instead of adding a second permanent one.
        if not live and getattr(self, '_live_card', None) is not None:
            self._finalize_live_card(code, output, annotation)
            return

        # Batch-safety: the post-batch callers (controller / floating window)
        # pass the COMBINED observation of the whole batch. finalize_code_step()
        # has already given this step its own output, so a second permanent card
        # here would either duplicate it or overwrite it with every call's output
        # glued together (the reported "same name, empty output" symptom).
        if save_to_history and not live and not authoritative:
            _seq = self._current_step_seq()
            if _seq is not None and _seq == getattr(self, '_last_finalized_step', None):
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

        # Painted >_ prompt glyph (icon overhaul: the ASCII label is gone;
        # zoom handled in _restyle via set_px)
        from systema.ui.widgets.painted_icons import TerminalGlyph
        icon_lbl = TerminalGlyph(px=12, color='#6E7681')
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
        summary_lbl = QLabel()    # text + sizes set by _restyle (zoom-aware)
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl)

        # Output token estimate — the card wears its context cost.
        from systema.common.token_est import estimate_tokens
        tok_lbl = QLabel(f"~{_fmt_tok(estimate_tokens(output))} tok")
        header_lay.addWidget(tok_lbl)

        toggle_btn = QLabel("▶")
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Expandable detail (hidden by default) ─────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(0, 4, 0, 0)
        detail_lay.setSpacing(4)

        _zk = self._get_msg_font_size() / 13.0    # zoom scale for card chrome
        _mono_pt = max(7, round(9 * _zk))
        mono = QFont('Consolas', _mono_pt)
        if not mono.exactMatch():
            mono = QFont('Courier New', _mono_pt)
        _lh = max(12, round(17 * _zk))            # per-line height estimate

        code_edit = None
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
            code_edit.setFixedHeight(min(max(_n * _lh + 20, 44), 150))
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
            out_edit.setFixedHeight(min(max(_n * _lh + 20, 44), 130))
            detail_lay.addWidget(out_edit)
            detail_lay.addWidget(_ResizeGrip(out_edit, min_h=44, max_h=680))

            # ── Clear output — token-saving: replaces this result with a stub
            #    in the LIVE history AND the session savefile. Reads out_ref so
            #    a streaming card clears its FINAL (finalized) output. ─────────
            _already_stub = (output.strip() == "Output cleared by the user"
                             or output.strip().startswith("[Compacted]"))
            clear_btn = QPushButton("Clear output")
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            # styled by _restyle below (zoom-aware)
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

        # ── Zoom-aware styling — SINGLE source for every font-bearing style on
        #    this card; re-invoked by _apply_zoom_all on Ctrl+scroll ──────────
        def _restyle():
            z = self._card_z
            icon_lbl.set_px(z(12))          # painted glyph scales with zoom
            summary_lbl.setText(
                f"<span style='color:#9AA0A6;font-size:{z(11)}px;'>{header_label}</span>"
                f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
                f"<span style='font-family:monospace;font-size:{z(10)}px;color:#5F6368;'>{preview}</span>")
            tok_lbl.setStyleSheet(
                f"background: transparent; border: none; font-size: {z(9)}px; color: #5F6368;")
            toggle_btn.setStyleSheet(
                f"background: transparent; border: none; font-size: {z(8)}px; color: #5F6368;")
            pt = max(7, round(9 * self._get_msg_font_size() / 13.0))
            mf = QFont('Consolas', pt)
            if not mf.exactMatch():
                mf = QFont('Courier New', pt)
            for ed in (code_edit, out_edit):
                if ed is not None:
                    ed.setFont(mf)
            if clear_btn is not None:
                # Transparent at rest (matches the code-block chip rework);
                # translucent pill returns on hover for readability.
                clear_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: #8B949E;
                        font-size: {z(10)}px; padding: 2px 9px;
                        border: 1px solid transparent; border-radius: 4px;
                    }}
                    QPushButton:hover {{ color: #E06060; border-color: #E06060;
                        background: rgba(33, 38, 45, 0.92); }}
                """)
        _restyle()

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
            'zoom_restyle': _restyle,
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
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QTextEdit

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
        read_like = tool in ('read_file', 'grep')
        op_glyph = file_op_glyph(tool, added, removed, created, rejected)

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

        # None = this op's icon is painted, not typed (grep → the magnifier).
        if op_glyph is None:
            from systema.ui.widgets.painted_icons import SearchGlyph
            icon_lbl = SearchGlyph(px=12, color='#6E7681')
        else:
            icon_lbl = QLabel(op_glyph)              # styled by _restyle (zoom-aware)
        header_lay.addWidget(icon_lbl)

        # ── Path — its OWN elided label so it can shrink with the card without
        #    ever eating the diff stats. Middle-elide keeps the filename; the
        #    full path lives in the tooltip. ─────────────────────────────────
        path_lbl = _ElidedLabel(display)             # styled by _restyle (zoom-aware)
        path_lbl.setToolTip(info.get('path', '') or display)
        # No stretch: the header HUGS its content (like the >_ code-exec rows) so
        # the stats sit right beside the path instead of floating to the far
        # edge. The path is the shrinkable one — it elides only when the whole
        # row is squeezed past the bubble width cap.
        path_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        header_lay.addWidget(path_lbl)
        header_lay.addSpacing(12)

        # ── Stats cluster — a SEPARATE, never-wrapping label so +N −N net ±N
        #    always renders regardless of how narrow the card gets. Built by a
        #    function so _restyle can re-render it at the current zoom. ──────
        def _stats_html():
            z = self._card_z
            if rejected:
                return (f"<span style='color:{RED};font-size:{z(10)}px;font-weight:600;'>"
                        f"rejected</span>")
            if read_like:
                return (f"<span style='color:#8B949E;font-size:{z(10)}px;'>{read_range}</span>"
                        if read_range else "")
            net = (added or 0) - (removed or 0)
            net_color = GREEN if net >= 0 else RED
            net_txt = f"+{net}" if net >= 0 else str(net)
            bits = []
            # Zero counts are omitted: "+91 −0" put a minus sign on a purely
            # additive edit, which is exactly the thing the icon is now trying
            # to say at a glance.
            if added:
                bits.append(f"<span style='color:{GREEN};font-size:{z(11)}px;"
                            f"font-weight:600;'>+{added}</span>")
            if removed:
                bits.append(f"<span style='color:{RED};font-size:{z(11)}px;"
                            f"font-weight:600;'>−{removed}</span>")
            bits.append(f"<span style='color:#5F6368;font-size:{z(11)}px;'>·</span>"
                        f"<span style='color:{net_color};font-size:{z(11)}px;'> net {net_txt}</span>")
            if created:
                bits.append(f"<span style='color:{GREEN};font-size:{z(10)}px;'>&nbsp;new file</span>")
            return "&nbsp;&nbsp;".join(bits)

        stats_lbl = None
        if _stats_html():
            stats_lbl = QLabel()    # text set by _restyle (zoom-aware)
            stats_lbl.setTextFormat(Qt.TextFormat.RichText)
            stats_lbl.setStyleSheet("background: transparent; border: none;")
            stats_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
            header_lay.addWidget(stats_lbl)

        tool_lbl = QLabel(tool)                      # styled by _restyle
        tool_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        header_lay.addWidget(tool_lbl)

        toggle_btn = QLabel("▶")                     # styled by _restyle
        header_lay.addWidget(toggle_btn)
        # AlignLeft + the header's Maximum policy = hug-to-content, left-packed
        # (matches the code-exec rows; kills the far-right stats float).
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        body = QTextEdit()
        body.setReadOnly(True)
        body.setAcceptDrops(False)   # let file drags fall through to the chat surface
        import html as _html
        _detail = detail or "(no detail)"

        def _body_html():
            z = self._card_z
            if read_like:
                # Plain content (file window / search results) — no diff coloring.
                return (f"<pre style=\"margin:0;font-family:{MONO};font-size:{z(12)}px;"
                        f"line-height:1.4;color:{CTX};white-space:pre;\">"
                        f"{_html.escape(_detail)}</pre>")
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
            return (f"<pre style=\"margin:0;font-family:{MONO};font-size:{z(12)}px;"
                    f"line-height:1.4;white-space:pre;\">" + "\n".join(_rows) + "</pre>")

        body.setHtml(_body_html())
        body.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Compact default; the drag grip below extends it (clamped).
        _bn = len(_detail.split('\n'))
        _lh = max(12, round(17 * self._get_msg_font_size() / 13.0))
        body.setFixedHeight(min(max(_bn * _lh + 20, 44), 170))
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

        # ── Zoom-aware styling — SINGLE source for every font-bearing style on
        #    this card; re-invoked by _apply_zoom_all on Ctrl+scroll ──────────
        def _restyle():
            z = self._card_z
            if hasattr(icon_lbl, 'set_px'):          # painted glyph (grep)
                icon_lbl.set_px(z(12))
                icon_lbl.set_color("#6E7681")
            else:
                icon_lbl.setStyleSheet(
                    f"color: #6E7681; font-size: {z(12)}px; font-weight: 700;"
                    f" background: transparent; border: none;")
                icon_lbl.setFixedWidth(max(14, round(14 * self._get_msg_font_size() / 13.0)))
            path_lbl.setStyleSheet(
                f"color:#9AA0A6; font-size:{z(11)}px; background:transparent; border:none;")
            path_lbl._apply()      # re-elide with the new font metrics
            if stats_lbl is not None:
                stats_lbl.setText(_stats_html())
            tool_lbl.setStyleSheet(
                f"color:#5F6368; font-size:{z(10)}px; background:transparent; border:none;")
            toggle_btn.setStyleSheet(
                f"background: transparent; border: none; font-size: {z(8)}px; color: #5F6368;")
            body.setHtml(_body_html())
        _restyle()

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
            'zoom_restyle': _restyle,
        })

        if save_to_history:
            try:
                slim = dict(info)
                slim['detail'] = (detail or "")[:20000]
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    'content': f"{op_glyph} {tool} {display}",
                    '_type': 'file_op',
                    '_file_op': slim,
                })
            except Exception:
                pass

    def add_memory_context_widget(self, context_id: str, memories: list,
                                   save_to_history: bool = True):
        """Render a recalled-memories card embedded in the AI turn group.

        Flat-row layout: a collapsed header line (count + first title) that
        expands to one row per memory — bold title, short body preview and a
        muted meta line (date · tags · similarity). When Detach is clicked the
        widget animates out AND the corresponding ui_event is removed from
        conversation_history via the controller.

        Parameters
        ----------
        context_id  : short UUID-derived string stored on the ui_event
        memories    : list of dicts {text, created_at, similarity}; plain
                      strings (old sessions) are accepted and rendered
                      without date/score metadata
        save_to_history : False when replaying from a loaded session (entry
                          already exists in conversation_history)
        """
        if not context_id or not isinstance(context_id, str):
            return

        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel
        from datetime import datetime as _dt

        MUTED, DIM, CTX = "#8B949E", "#5F6368", "#C9D1D9"   # shared card grays

        def _parse_mem(m):
            """Normalize one memory (dict or legacy string) into display parts."""
            if isinstance(m, dict):
                text = str(m.get('text', ''))
                created = m.get('created_at') or ''
                sim = m.get('similarity') or 0.0
            else:
                text, created, sim = str(m), '', 0.0
            lines = text.split('\n')
            title = lines[0].strip() if lines and lines[0].strip() else '(untitled)'
            tags = ''
            body_parts = []
            for ln in lines[1:]:
                s = ln.strip()
                if s.lower().startswith('tags:'):
                    tags = s[5:].strip()
                elif s:
                    body_parts.append(s)
            body = ' '.join(body_parts)
            date = ''
            if created:
                try:
                    date = _dt.fromisoformat(created).strftime('%Y-%m-%d')
                except Exception:
                    date = ''
            try:
                sim = float(sim)
            except (TypeError, ValueError):
                sim = 0.0
            return {'title': title, 'body': body, 'tags': tags,
                    'date': date, 'sim': sim}

        mems = [_parse_mem(m) for m in (memories or [])]

        # ── Outer wrapper ──────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet(
            "QFrame { background-color: transparent; padding: 0px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header — BORDERLESS faint line (matches code-exec/file-op cards):
        #    blends into the turn shell until expanded; the whole line toggles ─
        header = QFrame()
        header.setObjectName("memHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 6, 2)
        header_lay.setSpacing(6)

        icon_lbl = QLabel("◈")   # monochrome glyph (no-emoji rule); styled by _restyle
        header_lay.addWidget(icon_lbl)

        _first_title = mems[0]['title'] if mems else "Memory recalled"
        preview_text = _first_title[:64] + ("…" if len(_first_title) > 64 else "")

        summary_lbl = QLabel()    # text + sizes set by _restyle (zoom-aware)
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)

        # ── Detach — painted boxless ✕ (icon overhaul); the button consumes
        #    its own clicks so detaching never fires the header's expand toggle
        from systema.ui.widgets.painted_icons import CloseButton as _XBtn
        detach_btn = _XBtn(18, pill=False)           # sized by _restyle (zoom)
        detach_btn.setToolTip(
            "Remove this memory from the conversation context.\n"
            "The AI will no longer see it in this session.")
        header_lay.addWidget(detach_btn)

        toggle_lbl = QLabel("▶")                     # styled by _restyle
        header_lay.addWidget(toggle_lbl)

        # AlignLeft + Maximum policy = hug-to-content like the code-exec rows.
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        # ── Expandable flat-row list ───────────────────────────────────────
        detail = QFrame()
        detail.setObjectName("memDetail")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(12, 8, 12, 8)
        detail_lay.setSpacing(6)

        _titles, _bodies, _metas, _seps = [], [], [], []
        for i, pm in enumerate(mems):
            if i:
                sep = QFrame()
                sep.setFixedHeight(1)
                _seps.append(sep)
                detail_lay.addWidget(sep)
            t_lbl = QLabel(pm['title'])
            t_lbl.setWordWrap(True)
            _titles.append(t_lbl)
            detail_lay.addWidget(t_lbl)
            if pm['body']:
                b_prev = pm['body'][:220] + ("…" if len(pm['body']) > 220 else "")
                b_lbl = QLabel(b_prev)
                b_lbl.setWordWrap(True)
                _bodies.append(b_lbl)
                detail_lay.addWidget(b_lbl)
            meta_bits = []
            if pm['date']:
                meta_bits.append(pm['date'])
            if pm['tags']:
                meta_bits.append(f"tags: {pm['tags']}")
            if pm['sim'] > 0:
                meta_bits.append(f"sim: {pm['sim']:.2f}")
            if meta_bits:
                m_lbl = QLabel("  ·  ".join(meta_bits))
                m_lbl.setWordWrap(True)
                _metas.append(m_lbl)
                detail_lay.addWidget(m_lbl)

        outer_lay.addWidget(detail)

        # ── Zoom + theme styling — SINGLE source for every font-bearing style
        #    on this card; reads the LIVE theme so apply_theme and
        #    _apply_zoom_all both just call it ─────────────────────────────
        def _restyle():
            z = self._card_z
            t = self._t()
            k = self._get_msg_font_size() / 13.0
            accent = t['accent']
            # objectName-scoped rules: an unscoped "QFrame {...}" here would
            # cascade into the separator QFrames inside the detail pane.
            header.setStyleSheet("""
                    QFrame#memHeader { background: transparent; border: none; border-radius: 6px; }
                    QFrame#memHeader:hover { background: rgba(255,255,255,0.05); }
                """)
            icon_lbl.setStyleSheet(
                f"font-size: {z(12)}px; color: #6E7681; font-weight: 700; "
                f"background: transparent; border: none;")
            icon_lbl.setFixedWidth(max(14, round(14 * k)))
            count_word = "memory" if len(mems) == 1 else "memories"
            summary_lbl.setText(
                f"<span style='color:#9AA0A6;font-size:{z(11)}px;'>"
                f"{len(mems)} {count_word} recalled</span>"
                f"&nbsp;&nbsp;<span style='color:{DIM};font-size:{z(10)}px;'>{preview_text}</span>")
            detach_btn.setFixedSize(max(18, round(18 * k)), max(18, round(18 * k)))
            toggle_lbl.setStyleSheet(
                f"background: transparent; border: none; font-size: {z(8)}px; color: {DIM};")
            detail.setStyleSheet(f"""
                QFrame#memDetail {{
                    background: {t['base']}; border: 1px solid {t['border']};
                    border-radius: 8px; margin-top: 4px;
                }}
            """)
            for sep in _seps:
                sep.setStyleSheet(f"background: {t['border']}; border: none;")
            for lbl in _titles:
                lbl.setStyleSheet(
                    f"font-size: {z(11)}px; font-weight: 600; color: {accent}; "
                    f"background: transparent; border: none;")
            for lbl in _bodies:
                lbl.setStyleSheet(
                    f"font-size: {z(10)}px; color: {CTX}; "
                    f"background: transparent; border: none;")
            for lbl in _metas:
                lbl.setStyleSheet(
                    f"font-size: {z(9)}px; color: {MUTED}; "
                    f"background: transparent; border: none;")
        _restyle()

        # ── Toggle logic — whole header line toggles, like the file-op card ─
        def _toggle():
            showing = detail.isVisible()
            detail.setVisible(not showing)
            toggle_lbl.setText("▶" if showing else "▼")

        header.mousePressEvent = lambda e: _toggle()

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
            # Animate out, then destroy via the shared path so an empty turn
            # shell (avatar husk) gets pruned too.
            self._animate_message_out(
                _w, callback=lambda: self._detach_chat_widget(_w))

        detach_btn.clicked.connect(lambda: _detach())

        # Cap to the responsive bubble width so the card shrinks with the window.
        header.setMaximumWidth(self._bubble_max_width())
        detail.setMaximumWidth(self._bubble_max_width())

        # Memory cards belong to the AI's turn — stack inside the shared shell.
        g = self._insert_turn_segment(message_widget)
        self._animate_message_in(
            message_widget,
            on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track in message_widgets ───────────────────────────────────────
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'memory_context',
            'context_id': context_id,
            'content_wrapper': header,
            'group_row': g['row'],
            '_toggle_btn': toggle_lbl,
            'zoom_restyle': _restyle,
        })

        # ── Persist to history (only on first insertion, not on reload) ────
        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    '_type': 'memory_context',
                    '_memory_context_id': context_id,
                    'content': '',          # content is stored per-memory
                    '_memories_preview': memories,
                })
            except Exception:
                pass

    # ── web_search result cards ────────────────────────────────────────────────
    # Card A: search results as a clickable title/url/snippet sub-list.
    # Card B: an opened page's clean text (or a links list) in a compact viewer.
    # Both follow the memory-context / code-exec card idioms so they blend in, and
    # both round-trip through the session via render_loaded_messages().

    def _web_card_shell(self, icon_glyph, header_text):
        """Shared chrome: transparent wrapper + clickable borderless header +
        hidden detail frame. Returns (message_widget, outer_lay, header,
        summary_lbl, toggle_lbl, detail, detail_lay).

        `icon_glyph` is a text glyph, OR an already-built widget for cards whose
        icon is painted rather than typed (the Thinking card's animated
        SparkleGlyph). The caller's _restyle sees whichever it passed.
        """
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel

        message_widget = QFrame()
        message_widget.setStyleSheet(
            "QFrame { background-color: transparent; padding: 0px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        header = QFrame()
        header.setObjectName("webHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(4, 2, 6, 2)
        header_lay.setSpacing(6)

        icon_lbl = (icon_glyph if isinstance(icon_glyph, QWidget)
                    else QLabel(icon_glyph))
        header_lay.addWidget(icon_lbl)
        summary_lbl = QLabel()
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)
        toggle_lbl = QLabel("▶")
        header_lay.addWidget(toggle_lbl)
        outer_lay.addWidget(header, alignment=Qt.AlignmentFlag.AlignLeft)

        detail = QFrame()
        detail.setObjectName("webDetail")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(12, 8, 12, 8)
        detail_lay.setSpacing(6)
        outer_lay.addWidget(detail)

        return (message_widget, outer_lay, header, summary_lbl,
                icon_lbl, toggle_lbl, detail, detail_lay)

    def _web_card_finalize(self, message_widget, header, detail, summary_lbl,
                           icon_lbl, toggle_lbl, restyle, role):
        """Shared wiring: toggle, width cap, insertion, tracking."""
        def _toggle():
            showing = detail.isVisible()
            detail.setVisible(not showing)
            toggle_lbl.setText("▶" if showing else "▼")
        header.mousePressEvent = lambda e: _toggle()
        header.setMaximumWidth(self._bubble_max_width())
        detail.setMaximumWidth(self._bubble_max_width())
        restyle()
        g = self._insert_turn_segment(message_widget)
        self._animate_message_in(
            message_widget,
            on_settled=lambda: self.scroll_to_widget(message_widget))
        self.message_widgets.append({
            'widget': message_widget,
            'role': role,
            'content_wrapper': header,
            'group_row': g['row'],
            '_toggle_btn': toggle_lbl,
            'zoom_restyle': restyle,
        })

    def add_web_search_card(self, info: dict, save_to_history: bool = True):
        """Card A — web_search results as a clickable title/url/snippet list."""
        import html as _html
        from PyQt6.QtWidgets import QFrame, QLabel

        from systema.ui.widgets.painted_icons import SearchGlyph

        query = str(info.get('query', '') or '')
        results = list(info.get('results', []) or [])
        (message_widget, outer_lay, header, summary_lbl,
         icon_lbl, toggle_lbl, detail, detail_lay) = self._web_card_shell(
            SearchGlyph(px=12, color='#5F6368'), "")

        rows = []   # (title_lbl, url_lbl, snip_lbl, sep_or_None)
        for i, r in enumerate(results):
            if i:
                sep = QFrame()
                sep.setObjectName("webSep")
                sep.setFixedHeight(1)
                detail_lay.addWidget(sep)
            else:
                sep = None
            title = str(r.get('title') or '(untitled)')
            href = str(r.get('href') or '')
            body = str(r.get('body') or '')
            t_lbl = QLabel(f'<a href="{_html.escape(href, quote=True)}">'
                           f'{_html.escape(title)}</a>')
            t_lbl.setTextFormat(Qt.TextFormat.RichText)
            t_lbl.setOpenExternalLinks(True)
            t_lbl.setWordWrap(True)
            detail_lay.addWidget(t_lbl)
            u_lbl = QLabel(href)
            u_lbl.setWordWrap(True)
            detail_lay.addWidget(u_lbl)
            s_lbl = None
            if body:
                s_lbl = QLabel(body[:220] + ("…" if len(body) > 220 else ""))
                s_lbl.setWordWrap(True)
                detail_lay.addWidget(s_lbl)
            rows.append((t_lbl, u_lbl, s_lbl, sep))

        def _restyle():
            z, t = self._card_z, self._t()
            # NOTE: _t() is the RAW theme dict (base/surface/elevated/border/
            # accent/deep) — it has no 'text'/'muted' keys. Use the shared card
            # grays like the code-exec / memory-context cards do.
            accent, muted, dim, ctx = t['accent'], "#8B949E", "#5F6368", "#C9D1D9"
            header.setStyleSheet(
                "QFrame#webHeader { background: transparent; border: none; border-radius: 6px; }"
                "QFrame#webHeader:hover { background: rgba(255,255,255,0.05); }")
            icon_lbl.set_px(z(12))         # painted magnifier — scales with zoom
            icon_lbl.set_color(dim)
            _q = _html.escape(query[:64] + ("…" if len(query) > 64 else ""))
            summary_lbl.setText(
                f'<span style="color:{muted}; font-size:{z(11)}px;">Web search · </span>'
                f'<span style="color:#E6EDF3; font-size:{z(11)}px; font-weight:600;">"{_q}"</span>'
                f'<span style="color:{dim}; font-size:{z(10)}px;">  {len(results)}</span>')
            toggle_lbl.setStyleSheet(f"color: {dim}; font-size: {z(8)}px; background: transparent;")
            detail.setStyleSheet(
                f"QFrame#webDetail {{ background: {t['base']}; border: 1px solid {t['border']};"
                f" border-radius: 8px; margin-top: 4px; }}"
                f"QFrame#webSep {{ background: {t['border']}; border: none; }}")
            for (t_lbl, u_lbl, s_lbl, sep) in rows:
                t_lbl.setStyleSheet(f"a {{ color: {accent}; text-decoration: none; }} "
                                    f"QLabel {{ font-size: {z(11)}px; font-weight: 600;"
                                    f" background: transparent; }}")
                u_lbl.setStyleSheet(f"color: {dim}; font-size: {z(9)}px; background: transparent;")
                if s_lbl is not None:
                    s_lbl.setStyleSheet(f"color: {ctx}; font-size: {z(10)}px; background: transparent;")
                if sep is not None:
                    sep.setStyleSheet(f"background: {t['border']};")

        self._web_card_finalize(message_widget, header, detail, summary_lbl,
                                icon_lbl, toggle_lbl, _restyle, 'web_search')

        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event', '_type': 'web_search',
                    'content': f'Web search: {query}',
                    '_web_query': query, '_web_results': results,
                })
            except Exception:
                pass

    def add_skill_action_card(self, info: dict, save_to_history: bool = True):
        """Card for load_skill / unload_skill.

        These were the only tools that ran invisibly — the agent could swap its
        own instructions mid-task with nothing in the transcript to show it. The
        card states the action and the skill; the detail carries the manager's
        message, which is what matters when a load is REJECTED.
        """
        import html as _html
        from PyQt6.QtWidgets import QLabel

        action = str(info.get('action') or 'load')
        skill = str(info.get('skill') or '')
        ok = bool(info.get('ok'))
        detail = str(info.get('detail') or '')

        (message_widget, outer_lay, header, summary_lbl,
         icon_lbl, toggle_lbl, detail_frame, detail_lay) = self._web_card_shell(
            skill_action_glyph(action, ok), "")

        # PLAIN text, NOT escaped. A QLabel left on AutoText only parses markup
        # when the string looks like HTML, and an escaped message never does —
        # so `Skill 'x' loaded.` rendered literally as `Skill &#x27;x&#x27;
        # loaded.`. Declaring the format is also the safer half: a manager
        # message or traceback containing <module> can't be eaten as a tag.
        detail_lbl = QLabel(detail or f"Skill '{skill}' {action}ed.")
        detail_lbl.setTextFormat(Qt.TextFormat.PlainText)
        detail_lbl.setWordWrap(True)
        detail_lay.addWidget(detail_lbl)

        def _restyle():
            z, t = self._card_z, self._t()
            dim, muted, ctx = "#5F6368", "#8B949E", "#C9D1D9"
            header.setStyleSheet(
                "QFrame#webHeader { background: transparent; border: none; border-radius: 6px; }"
                "QFrame#webHeader:hover { background: rgba(255,255,255,0.05); }")
            icon_lbl.setStyleSheet(f"color: {dim}; font-size: {z(12)}px; background: transparent;")
            verb = "Skill loaded" if action == 'load' else "Skill unloaded"
            if not ok:
                verb = "Skill load rejected" if action == 'load' else "Skill unload rejected"
            summary_lbl.setText(
                f'<span style="color:{muted}; font-size:{z(11)}px;">{verb} · </span>'
                f'<span style="color:#E6EDF3; font-size:{z(11)}px; font-weight:600;">'
                f'{_html.escape(skill)}</span>')
            toggle_lbl.setStyleSheet(f"color: {dim}; font-size: {z(8)}px; background: transparent;")
            detail_frame.setStyleSheet(
                f"QFrame#webDetail {{ background: {t['base']}; border: 1px solid {t['border']};"
                f" border-radius: 8px; margin-top: 4px; }}")
            detail_lbl.setStyleSheet(
                f"color: {ctx}; font-size: {z(10)}px; background: transparent;")

        self._web_card_finalize(message_widget, header, detail_frame, summary_lbl,
                                icon_lbl, toggle_lbl, _restyle, 'skill_action')

        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event', '_type': 'skill_action',
                    'content': f'Skill {action}: {skill}',
                    '_skill': skill, '_skill_action': action,
                    '_skill_ok': ok, '_skill_detail': detail,
                })
            except Exception:
                pass

    def add_image_attach_card(self, info: dict, save_to_history: bool = True):
        """Card for attach_image_to_chat — thumbnails of the image(s) the agent
        pinned for the user, from EITHER surface (direct tool call or the
        in-python namespace call; both route through the same dispatcher).

        Deliberately minimal: the image-pipeline redesign owns how attached
        images should really live in the chat (persistent bubbles, detach
        buttons, token accounting). This card exists so the action is visible
        and honest today.
        """
        import os
        import html as _html
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtGui import QPixmap

        paths = [str(p) for p in (info.get('paths') or [])]
        annotation = str(info.get('annotation') or '')
        (message_widget, outer_lay, header, summary_lbl,
         icon_lbl, toggle_lbl, detail, detail_lay) = self._web_card_shell("▣", "")

        thumbs, names = [], []
        for p in paths:
            # Plain, unescaped — same reason as the skill card: an escaped
            # filename ("Dad's photo.png") has no markup for AutoText to detect,
            # so the entity rendered literally.
            name_lbl = QLabel(os.path.basename(p))
            name_lbl.setTextFormat(Qt.TextFormat.PlainText)
            name_lbl.setWordWrap(True)
            detail_lay.addWidget(name_lbl)
            names.append(name_lbl)
            try:
                pm = QPixmap(p)
                if not pm.isNull():
                    thumb = QLabel()
                    thumb.setPixmap(pm.scaledToWidth(
                        min(360, self._bubble_max_width() - 40),
                        Qt.TransformationMode.SmoothTransformation))
                    detail_lay.addWidget(thumb)
                    thumbs.append(thumb)
            except Exception:
                pass

        def _restyle():
            z, t = self._card_z, self._t()
            dim, muted = "#5F6368", "#8B949E"
            header.setStyleSheet(
                "QFrame#webHeader { background: transparent; border: none; border-radius: 6px; }"
                "QFrame#webHeader:hover { background: rgba(255,255,255,0.05); }")
            icon_lbl.setStyleSheet(f"color: {dim}; font-size: {z(12)}px; background: transparent;")
            label = _html.escape(annotation) if annotation else "Image attached"
            summary_lbl.setText(
                f'<span style="color:{muted}; font-size:{z(11)}px;">{label} · </span>'
                f'<span style="color:#E6EDF3; font-size:{z(11)}px; font-weight:600;">'
                f'{len(paths)} image(s)</span>')
            toggle_lbl.setStyleSheet(f"color: {dim}; font-size: {z(8)}px; background: transparent;")
            detail.setStyleSheet(
                f"QFrame#webDetail {{ background: {t['base']}; border: 1px solid {t['border']};"
                f" border-radius: 8px; margin-top: 4px; }}")
            for lbl in names:
                lbl.setStyleSheet(f"color: {muted}; font-size: {z(10)}px; background: transparent;")

        self._web_card_finalize(message_widget, header, detail, summary_lbl,
                                icon_lbl, toggle_lbl, _restyle, 'image_attach')

        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event', '_type': 'image_attach',
                    'content': f'Attached {len(paths)} image(s)',
                    '_image_paths': paths, '_annotation': annotation,
                })
            except Exception:
                pass

    def add_thinking_card(self, text: str, save_to_history: bool = True,
                          live: bool = False):
        """Collapsible reasoning card — ONE per assistant turn, pinned to the
        TOP of the turn shell (unlike tool cards, which append below the text).

        Assistant turns are merged into a single bubble, so every response in
        that turn feeds the SAME card: repeat calls append to it, in order,
        separated by a blank line. UI-ONLY: stored as a ui_event so it survives
        reload, but ui_events are stripped from what is sent to the provider —
        the model never pays for its own past thinking.

        `live=True` returns the card handle so the streaming path can keep
        appending deltas while the reply is still arriving."""
        from PyQt6.QtWidgets import QTextEdit
        from systema.ui.widgets.painted_icons import SparkleGlyph

        text = (text or "").strip()
        if not text and not live:
            return None            # no thinking → no card at all

        # ── Reuse this turn's card if it already exists ──────────────────────
        card = self._live_thinking_card()
        if card is not None:
            if text:
                card['append'](("\n\n" if card['state']['text'] else "") + text)
            if not live:
                card['finish']()
                if save_to_history:
                    self._save_thinking_event(text)
            return card if live else card['widget']

        (message_widget, outer_lay, header, summary_lbl,
         icon_lbl, toggle_lbl, detail, detail_lay) = self._web_card_shell(
            SparkleGlyph(px=12, color='#5F6368'), "")

        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        viewer.setFrameShape(QTextEdit.Shape.NoFrame)
        viewer.setPlainText(text)
        detail_lay.addWidget(viewer)
        detail_lay.addWidget(_ResizeGrip(viewer, min_h=44, max_h=680))

        state = {'text': text, 'done': not live, 'pending': [],
                 'lines': text.count("\n") + 1, 'words': len(text.split())}

        def _restyle():
            z, t = self._card_z, self._t()
            # _t() is the RAW theme dict — no 'text' key (see add_web_search_card).
            muted, dim = "#8B949E", "#5F6368"
            k = self._get_msg_font_size() / 13.0
            header.setStyleSheet(
                "QFrame#webHeader { background: transparent; border: none; border-radius: 6px; }"
                "QFrame#webHeader:hover { background: rgba(255,255,255,0.05); }")
            icon_lbl.set_px(z(12))         # painted sparkle — scales with zoom
            icon_lbl.set_color(dim)
            _words = state['words']        # running count — never re-split the text
            _label = "Thinking" if state['done'] else "Thinking…"
            _count = (f'<span style="color:{dim}; font-size:{z(10)}px;">  {_words} words</span>'
                      if _words and state['done'] else '')
            summary_lbl.setText(
                f'<span style="color:{muted}; font-size:{z(11)}px;">{_label}</span>' + _count)
            toggle_lbl.setStyleSheet(f"color: {dim}; font-size: {z(8)}px; background: transparent;")
            detail.setStyleSheet(
                f"QFrame#webDetail {{ background: {t['base']}; border: 1px solid {t['border']};"
                f" border-radius: 8px; margin-top: 4px; }}")
            _n = state['lines']            # running count — never re-split the text
            _lh = max(12, round(17 * k))
            viewer.setFixedHeight(min(max(_n * _lh + 20, 44), 220))
            # Flat inside the card: the detail frame IS the box. A second
            # border/background here read as an ugly nested panel.
            viewer.setStyleSheet(
                f"QTextEdit {{ background: transparent; color: #C9D1D9;"
                f" border: none; padding: 0px; font-size: {z(10)}px; }}")

        self._thinking_card_finalize(message_widget, header, detail, summary_lbl,
                                     icon_lbl, toggle_lbl, _restyle)

        def _append(delta: str):
            """Buffer a reasoning delta. Painting happens in _flush() — see
            the note there; this used to setPlainText() the WHOLE reasoning
            and restyle on every single delta."""
            state['pending'].append(delta)
            # A multi-step work turn shares ONE card across responses, so
            # reasoning can resume after a finish() — the card is live again,
            # and the sparkle (and the "Thinking…" label) must say so.
            if state['done']:
                state['done'] = False
                self._sync_thinking_sparkle()

        def _flush() -> bool:
            """Paint buffered deltas. Returns True if anything was drawn.

            Appends via the text cursor (costs the chunk, not the document)
            and restyles ONCE per flush — setPlainText re-parsed the entire
            document per token, and _restyle re-split it for the word/line
            counts, so a long reasoning block became quadratic."""
            if not state['pending']:
                return False
            chunk = ''.join(state['pending'])
            state['pending'].clear()
            state['text'] += chunk
            state['lines'] += chunk.count("\n")
            state['words'] += len(chunk.split())
            cur = viewer.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertText(chunk)
            sb = viewer.verticalScrollBar()
            sb.setValue(sb.maximum())
            _restyle()
            return True

        def _finish():
            _flush()
            state['done'] = True
            _restyle()
            self._sync_thinking_sparkle()
            return state['text']

        def _discard(keep: int = 0) -> bool:
            """Abort path (user pressed Stop mid-stream): drop reasoning that was
            never persisted. `keep` = characters already saved by an earlier
            response of this same turn — those stay so the card and the session
            file keep agreeing. Returns True when the card was removed entirely.
            """
            state['pending'].clear()
            kept = state['text'][:keep] if keep else ""
            if kept.strip():
                state['text'] = kept
                state['lines'] = kept.count("\n")
                state['words'] = len(kept.split())
                try:
                    viewer.setPlainText(kept)
                except RuntimeError:
                    pass
                _finish()
                return False
            # Nothing of this card was ever saved — remove it, don't orphan it.
            self._turn_thinking_card = None
            try:
                self.message_widgets[:] = [
                    e for e in self.message_widgets
                    if e.get('widget') is not message_widget]
            except Exception:
                pass
            # _detach_chat_widget, not a bare delete: the card is a SEGMENT, so
            # dropping it can empty the turn shell — which must go with it
            # rather than linger as an avatar+name husk.
            try:
                self._detach_chat_widget(message_widget)
            except RuntimeError:
                pass
            return True

        card = {'widget': message_widget, 'append': _append, 'flush': _flush,
                'finish': _finish, 'discard': _discard, 'state': state,
                'icon': icon_lbl}
        # Remember it for the rest of this turn so later responses merge in.
        self._turn_thinking_card = card
        self._turn_thinking_group = getattr(self, '_ai_turn_group', None)
        self._sync_thinking_sparkle()

        if not live:
            _finish()
            if save_to_history:
                self._save_thinking_event(text)
            return message_widget
        return card

    def _sync_thinking_sparkle(self):
        """Start or stop the Thinking card's sparkle from the CURRENT state.

        Derived, never tracked: every caller just says "something changed" and
        this decides, so no path can leave the glyph spinning after the turn is
        over (or frozen while it is still working). It animates while:

          * reasoning is still streaming in — the streamed case; or
          * the reply's typewriter reveal is running — the non-streamed case,
            where all the reasoning arrives at once and the only thing still
            "happening" is the text appearing.

        A reloaded session hits neither: replayed cards are built finished and
        the reveal is skipped during bulk render, so a restored transcript shows
        the same resting star a completed live turn ends on.
        """
        card = getattr(self, '_turn_thinking_card', None)
        glyph = (card or {}).get('icon')
        if glyph is None or not hasattr(glyph, 'start'):
            return
        active = (not card['state'].get('done')
                  or bool(getattr(self, '_reveal_jobs', [])))
        try:
            if active:
                glyph.start()
            else:
                glyph.stop()
        except RuntimeError:
            pass                      # card torn down mid-turn

    def _live_thinking_card(self):
        """This turn's thinking card, or None. Cleared when a new turn shell
        opens or when its widgets are gone."""
        card = getattr(self, '_turn_thinking_card', None)
        if card is None:
            return None
        if getattr(self, '_turn_thinking_group', None) is not getattr(self, '_ai_turn_group', None):
            self._turn_thinking_card = None      # different turn → new card
            return None
        try:
            if card['widget'].parent() is None:
                raise RuntimeError
        except RuntimeError:
            self._turn_thinking_card = None
            return None
        return card

    def _thinking_card_finalize(self, message_widget, header, detail, summary_lbl,
                                icon_lbl, toggle_lbl, restyle):
        """Like _web_card_finalize, but pins the card to the TOP of the turn
        shell and never scroll-jumps to it (it must not fight the reply that
        is still streaming in below)."""
        def _toggle():
            showing = detail.isVisible()
            detail.setVisible(not showing)
            toggle_lbl.setText("▶" if showing else "▼")
        header.mousePressEvent = lambda e: _toggle()
        header.setMaximumWidth(self._bubble_max_width())
        detail.setMaximumWidth(self._bubble_max_width())
        restyle()
        g = self._insert_turn_segment(message_widget, at_top=True)
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'thinking',
            'content_wrapper': header,
            'group_row': g['row'],
            '_toggle_btn': toggle_lbl,
            'zoom_restyle': restyle,
        })

    def _save_thinking_event(self, text: str):
        """Persist a thinking card as a ui_event (reload-visible, never sent).

        Also the ONE place the phone learns about reasoning: this fires once per
        response on both the streaming finalize and the non-streaming path, so
        mirroring here needs no second hook and cannot double-send."""
        text = (text or "").strip()
        if not text:
            return
        try:
            self.controller.ai.conversation_history.append({
                'role': 'ui_event', '_type': 'thinking',
                'content': 'Thinking', '_thinking': text,
            })
        except Exception:
            pass
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab is not None and getattr(_ab, '_conn', None) is not None:
            try:
                _ab.add_reasoning_card(text)
            except Exception as e:
                log.debug(f"[_save_thinking_event] android mirror skipped: {e}")

    def add_web_page_card(self, info: dict, save_to_history: bool = True):
        """Card B — an opened page's clean text, or a links list, in a compact
        expandable viewer (does not balloon: capped height + resize grip)."""
        import html as _html
        from PyQt6.QtWidgets import QLabel, QTextEdit

        mode = str(info.get('mode', 'open') or 'open')
        url = str(info.get('url', '') or '')
        title = str(info.get('title', '') or url or 'Web page')
        text = str(info.get('text', '') or '')
        links = list(info.get('links', []) or [])

        (message_widget, outer_lay, header, summary_lbl,
         icon_lbl, toggle_lbl, detail, detail_lay) = self._web_card_shell("▤", "")

        # Clickable source URL at the top of the detail.
        src_lbl = QLabel(f'<a href="{_html.escape(url, quote=True)}">{_html.escape(url)} ↗</a>')
        src_lbl.setTextFormat(Qt.TextFormat.RichText)
        src_lbl.setOpenExternalLinks(True)
        src_lbl.setWordWrap(True)
        detail_lay.addWidget(src_lbl)

        viewer = None
        link_rows = []
        if mode == 'links':
            for it in links:
                lt = str(it.get('text') or '(no text)')
                lh = str(it.get('href') or '')
                row = QLabel(f'<a href="{_html.escape(lh, quote=True)}">{_html.escape(lt)}</a>'
                             f'<br><span>{_html.escape(lh)}</span>')
                row.setTextFormat(Qt.TextFormat.RichText)
                row.setOpenExternalLinks(True)
                row.setWordWrap(True)
                detail_lay.addWidget(row)
                link_rows.append(row)
        else:
            viewer = QTextEdit()
            viewer.setReadOnly(True)
            viewer.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            viewer.setFrameShape(QTextEdit.Shape.NoFrame)
            viewer.setPlainText(text)
            detail_lay.addWidget(viewer)
            detail_lay.addWidget(_ResizeGrip(viewer, min_h=44, max_h=680))

        def _restyle():
            z, t = self._card_z, self._t()
            # _t() is the RAW theme dict — no 'text' key (see add_web_search_card).
            accent, dim = t['accent'], "#5F6368"
            k = self._get_msg_font_size() / 13.0
            header.setStyleSheet(
                "QFrame#webHeader { background: transparent; border: none; border-radius: 6px; }"
                "QFrame#webHeader:hover { background: rgba(255,255,255,0.05); }")
            icon_lbl.setStyleSheet(f"color: {dim}; font-size: {z(12)}px; background: transparent;")
            dom = ''
            try:
                from urllib.parse import urlparse
                dom = urlparse(url).netloc
            except Exception:
                dom = ''
            _title = _html.escape(title[:70] + ("…" if len(title) > 70 else ""))
            _extra = (f'<span style="color:{dim}; font-size:{z(10)}px;">  {_html.escape(dom)}</span>'
                      if dom else '')
            summary_lbl.setText(
                f'<span style="color:#E6EDF3; font-size:{z(11)}px; font-weight:600;">{_title}</span>'
                + _extra)
            toggle_lbl.setStyleSheet(f"color: {dim}; font-size: {z(8)}px; background: transparent;")
            detail.setStyleSheet(
                f"QFrame#webDetail {{ background: {t['base']}; border: 1px solid {t['border']};"
                f" border-radius: 8px; margin-top: 4px; }}")
            src_lbl.setStyleSheet(f"a {{ color: {accent}; text-decoration: none; }} "
                                  f"QLabel {{ font-size: {z(9)}px; background: transparent; }}")
            for row in link_rows:
                row.setStyleSheet(f"a {{ color: {accent}; text-decoration: none; }} "
                                  f"span {{ color: {dim}; }} "
                                  f"QLabel {{ font-size: {z(10)}px; background: transparent; }}")
            if viewer is not None:
                _n = len(text.splitlines())
                _lh = max(12, round(17 * k))
                viewer.setFixedHeight(min(max(_n * _lh + 20, 44), 220))
                viewer.setStyleSheet(
                    f"QTextEdit {{ background: {t['base']}; color: #E6EDF3;"
                    f" border: 1px solid {t['border']}; border-radius: 8px;"
                    f" padding: 8px 12px; font-size: {z(10)}px; }}")

        self._web_card_finalize(message_widget, header, detail, summary_lbl,
                                icon_lbl, toggle_lbl, _restyle, 'web_page')

        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event', '_type': 'web_page',
                    'content': f'Web page: {url}',
                    '_web_mode': mode, '_web_url': url, '_web_title': title,
                    '_web_text': text, '_web_links': links,
                })
            except Exception:
                pass
