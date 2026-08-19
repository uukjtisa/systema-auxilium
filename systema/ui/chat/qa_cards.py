"""
systema/ui/chat/qa_cards.py

The ask_user interview card -- the in-chat surface for the ask_user tool.

It is a normal row in the transcript (same _insert_turn_segment path as every
other card), NOT a floating dialog, because three of its requirements only work
that way: the answers persist in the session file, a reloaded session re-renders
the card read-only, and a past card can be re-opened to revise what was picked.

CHROME MATCHES THE OTHER CARDS
------------------------------
Same structure as _web_card_shell (thinking / web search / memory): a
TRANSPARENT wrapper, a borderless header row with a hover tint, and a bordered
detail panel that only exists while expanded. This card first shipped with its
own boxed frame and stood out as the one element in the turn that looked
foreign. The one deliberate difference: a LIVE question starts EXPANDED, because
unlike the other cards it is not a summary of something that already happened --
it is a prompt the turn is blocked on.

BLOCKING
--------
The agent's turn must not continue past a question it just asked, so the tool
call parks until the card resolves. The waiting is done exactly the way the code
approval card does it: the worker thread blocks on a threading.Event while the
GUI thread spins a LOCAL QEventLoop. A modal exec() would freeze the rest of the
app; this way the user can still scroll, switch sessions, or open settings while
the question sits there.

ESC KEEPS THE WORK
------------------
Dismissing does not discard what was already filled in. Whatever the user had
picked is serialized through the same qa_spec.serialize() the completed path
uses and prepended to the input box as a Q:/A: block, so a half-finished
interview is never lost and reads identically to a finished one.

PERSISTENCE
-----------
On resolve the card writes a ui_event into conversation_history
(_type == "ask_user"). ui_events are saved with the session and STRIPPED before
the provider sees them, which is exactly right: the answers already reached the
model once as the tool observation, and storing them again would double them in
context. render_loaded_messages rebuilds the card from that entry, locked.

REVISING
--------
A resolved card can be re-opened, but changing it does NOT rewrite history --
the agent already acted on the original answers, and silently editing the record
of what it was told would make the transcript a lie. Re-answering puts the
updated Q:/A: block in the input box instead, so the correction travels as a
normal message the user chooses to send.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFrame, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QRadioButton,
                             QScrollArea, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

from systema.common.logger import _make_logger, _NoOpLogger
from systema.execution import qa_spec

_verbose = True
log = _make_logger("QaCard") if _verbose else _NoOpLogger()


def _esc(text) -> str:
    """Answers are user-authored free text -- never interpolate it raw."""
    from html import escape
    return escape(str(text or ""))


# Type colours only, and the same two the other cards hardcode. Surfaces and the
# accent come from the LIVE theme dict -- theme.py carries surfaces and the
# accent, not type colours, and that split is house standard.
_MUTED = "#8B949E"
_DIM = "#5F6368"
_TEXT = "#E8EAED"

# Used only when no theme accessor was supplied (bare construction in tests).
_FALLBACK_THEME = {"base": "#0F1319", "surface": "#161A21", "elevated": "#1B2029",
                   "border": "#2A313C", "accent": "#5A9CF8", "deep": "#0F1319"}


class QaCard(QFrame):
    """One interview. ``resolved`` fires once per answering pass."""

    resolved = pyqtSignal()
    revised = pyqtSignal()          # a LOCKED card was re-answered

    # A question list is unbounded -- the model can write four questions with
    # six described options each. Left to its natural height the card ate the
    # entire chat viewport and pushed the conversation off screen. It is a card
    # in the flow, not a takeover, so the options scroll INSIDE it.
    MAX_H = 300
    MAX_W = 620          # fallback only; the chat passes _bubble_max_width()

    def __init__(self, qset, zoom=lambda n: n, parent=None,
                 answers=None, locked=False, max_width=0, max_height=0,
                 theme=None):
        super().__init__(parent)
        self.qset = qset
        self._z = zoom
        self._theme = theme if callable(theme) else (lambda: _FALLBACK_THEME)
        self._max_w = int(max_width or 0)
        self._max_h = int(max_height or 0)
        self.answers = qa_spec.normalize_answers(qset, answers)
        self.dismissed = False
        self._done = False
        self._revising = False
        self._expanded = True     # explicit; see _toggle
        self._page = 0
        self._rows = []          # per question: (checks, other_box, group)

        self.setObjectName("qaCard")
        self._build()
        self._restyle()
        if answers:
            self._restore(self.answers)
        if locked:
            self._done = True
            self._lock()

    # -- construction --------------------------------------------------------

    def _build(self):
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- header: borderless, hover-tinted, clickable (house pattern) ------
        self._header = QFrame()
        self._header.setObjectName("qaHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        head = QHBoxLayout(self._header)
        head.setContentsMargins(4, 2, 6, 2)
        head.setSpacing(6)
        self._summary_lbl = QLabel()
        self._summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet("background: transparent; border: none;")
        head.addWidget(self._summary_lbl, 1)
        self._toggle_lbl = QLabel("\u25bc")
        head.addWidget(self._toggle_lbl)
        self._header.mousePressEvent = lambda e: self._toggle()
        outer.addWidget(self._header)

        # -- detail: the only part with a border, like every other card -------
        self._detail = QFrame()
        self._detail.setObjectName("qaDetail")
        det = QVBoxLayout(self._detail)
        det.setContentsMargins(12, 9, 12, 9)
        det.setSpacing(7)

        self._stack = QStackedWidget()
        for q in self.qset.questions:
            self._stack.addWidget(self._build_page(q))
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._stack)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)
        det.addWidget(self._scroll)

        foot = QHBoxLayout()
        self._skip_btn = QPushButton("Answer in chat")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        self._back_btn = QPushButton("< Back")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(lambda: self._go(self._page - 1))
        self._next_btn = QPushButton("Next >")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.clicked.connect(self._on_next)
        self._revise_btn = QPushButton("Revise")
        self._revise_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._revise_btn.clicked.connect(self._on_revise)
        self._revise_btn.setVisible(False)
        foot.addWidget(self._skip_btn)
        foot.addStretch(1)
        foot.addWidget(self._revise_btn)
        foot.addWidget(self._back_btn)
        foot.addWidget(self._next_btn)
        det.addLayout(foot)
        outer.addWidget(self._detail)

        self._apply_bounds()
        self._sync_nav()

    def _build_page(self, q):
        page = QWidget()
        page.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 6, 0)      # room for the scrollbar
        lay.setSpacing(3)

        if q.header:
            chip = QLabel(q.header)
            chip.setObjectName("qaChip")
            lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)

        prompt = QLabel(q.question)
        prompt.setObjectName("qaPrompt")
        prompt.setWordWrap(True)
        lay.addWidget(prompt)

        checks = []
        # A single-select question still has to be answerable by clicking, so it
        # gets radios in an exclusive group; multi-select gets plain checkboxes.
        group = QButtonGroup(page) if not q.multi else None
        if group is not None:
            group.setExclusive(True)
        for opt in q.options:
            box = QCheckBox(opt.label) if q.multi else QRadioButton(opt.label)
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            if group is not None:
                group.addButton(box)
            lay.addWidget(box)
            checks.append(box)
            if opt.description:
                sub = QLabel(opt.description)
                sub.setObjectName("qaDesc")
                sub.setWordWrap(True)
                lay.addWidget(sub)

        other_box = None
        if q.allow_other:
            # Always present, never an option in the list: the agent must not be
            # able to omit the escape hatch, and it must not be able to spend one
            # of its option slots on it either.
            other_box = QLineEdit()
            other_box.setObjectName("qaOther")
            other_box.setPlaceholderText("Other - type your own answer")
            lay.addWidget(other_box)

        self._rows.append((checks, other_box, group))
        return page

    # -- state ---------------------------------------------------------------

    def _restore(self, answers):
        """Tick the widgets to match stored answers (session reload / revise)."""
        for i, (checks, other_box, _g) in enumerate(self._rows):
            a = answers[i] if i < len(answers) else {}
            picked = set(a.get("picked") or [])
            q = self.qset.questions[i]
            for j, box in enumerate(checks):
                if j < len(q.options):
                    box.setChecked(q.options[j].label in picked)
            if other_box is not None:
                other_box.setText(a.get("other", "") or "")

    def _capture(self, index):
        """Read one page's widgets into self.answers. Called on every move, so
        Back/Next never loses a pick and Esc always has the latest state."""
        if not (0 <= index < len(self._rows)):
            return
        checks, other_box, _group = self._rows[index]
        q = self.qset.questions[index]
        picked = [q.options[i].label for i, b in enumerate(checks)
                  if b.isChecked() and i < len(q.options)]
        self.answers[index]['picked'] = picked
        self.answers[index]['other'] = other_box.text().strip() if other_box else ''

    def _go(self, index):
        self._capture(self._page)
        self._page = max(0, min(index, len(self.qset.questions) - 1))
        self._stack.setCurrentIndex(self._page)
        self._sync_nav()

    def _toggle(self):
        """Collapse/expand, like every other card's header.

        Driven by an explicit flag, NOT by querying isVisible(): a widget whose
        window has not been shown yet reports isVisible() False even while it is
        laid out and expanded, so reading it inverted the FIRST click for any
        card built while the chat window was closed.
        """
        self._set_expanded(not self._expanded)

    def _set_expanded(self, on: bool):
        self._expanded = bool(on)
        self._detail.setVisible(self._expanded)
        self._toggle_lbl.setText("\u25bc" if self._expanded else "\u25b6")

    def _sync_nav(self):
        total = len(self.qset.questions)
        self._back_btn.setVisible(self._page > 0)
        self._next_btn.setText("Done" if self._page >= total - 1 else "Next >")
        self._render_summary()

    def _render_summary(self):
        """The header line. Unanswered: what is being asked and where you are.
        Answered: the answers themselves, so a settled card reads at a glance."""
        z = self._z
        total = len(self.qset.questions)
        if not self._done:
            state = f"{self._page + 1} of {total}" if total > 1 else "1 question"
            self._summary_lbl.setText(
                f'<span style="color:{_MUTED}; font-size:{z(11)}px;">Q&amp;A</span>'
                f'<span style="color:{_DIM}; font-size:{z(10)}px;">  {state}</span>')
            return
        bits = []
        for i, q in enumerate(self.qset.questions):
            a = self.answers[i] if i < len(self.answers) else {}
            label = q.header or q.question
            if len(label) > 34:
                label = label[:33].rstrip() + "\u2026"
            answer = qa_spec.format_answer(a.get("picked"), a.get("other", ""),
                                           a.get("skipped", False))
            bits.append(f'<span style="color:{_DIM}">{_esc(label)}</span>'
                        f'<span style="color:{_TEXT}">  {_esc(answer)}</span>')
        word = "dismissed" if self.dismissed else "answered"
        sep = f'<span style="color:{_DIM}">  &middot;  </span>'
        self._summary_lbl.setText(
            f'<span style="color:{_MUTED}; font-size:{z(11)}px;">Q&amp;A</span>'
            f'<span style="color:{_DIM}; font-size:{z(10)}px;">  {word}  &middot;  </span>'
            f'<span style="font-size:{z(10)}px;">' + sep.join(bits) + '</span>')

    # -- exits ---------------------------------------------------------------

    def _on_next(self):
        if self._done:
            return
        if self._page < len(self.qset.questions) - 1:
            self._go(self._page + 1)
            return
        self._capture(self._page)
        self._finish(dismissed=False)

    def _on_skip(self):
        """'Answer in chat' -- the user would rather type it. Everything already
        filled in is kept; only the questions never reached are marked skipped."""
        if self._done:
            return
        self._capture(self._page)
        for a in self.answers:
            if not a['picked'] and not a['other']:
                a['skipped'] = True
        self._finish(dismissed=True)

    def dismiss(self):
        """Esc / external cancel. Partial answers survive -- see the module
        docstring; the caller prepends them to the input box."""
        if self._done:
            return
        self._capture(self._page)
        self._finish(dismissed=True)

    def _finish(self, dismissed):
        if self._done:
            return                      # resolved is a one-shot per pass
        self._done = True
        self.dismissed = bool(dismissed)
        self._lock()
        if self._revising:
            self._revising = False
            self.revised.emit()
        else:
            self.resolved.emit()

    def _on_revise(self):
        """Unlock a resolved card so the user can change what they picked."""
        self._done = False
        self._revising = True
        self.dismissed = False
        self._page = 0
        self._stack.setCurrentIndex(0)
        for checks, other_box, _g in self._rows:
            for b in checks:
                b.setEnabled(True)
            if other_box is not None:
                other_box.setReadOnly(False)
        for a in self.answers:
            a['skipped'] = False
        self._revise_btn.setVisible(False)
        self._skip_btn.setVisible(True)
        self._next_btn.setVisible(True)
        self._scroll.setVisible(True)
        self._set_expanded(True)
        self._sync_nav()

    def _lock(self):
        """Freeze the card and COLLAPSE it.

        A settled question showing six greyed-out radios wastes most of a turn's
        height on a decision already made, so the answer moves into the header
        line and the form folds away -- the same shape every other card takes
        once its detail is closed. The chevron (or Revise) brings it back.
        """
        for checks, other_box, _g in self._rows:
            for b in checks:
                b.setEnabled(False)
            if other_box is not None:
                other_box.setReadOnly(True)
        self._skip_btn.setVisible(False)
        self._next_btn.setVisible(False)
        self._back_btn.setVisible(False)
        self._revise_btn.setVisible(True)
        self._set_expanded(False)
        self._render_summary()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and not self._done:
            self.dismiss()
            event.accept()
            return
        super().keyPressEvent(event)

    # -- look ----------------------------------------------------------------

    def _apply_bounds(self):
        """Clamp the card so it stays a widget in the flow.

        Height is capped and the questions scroll inside; width is capped for
        the same reason message bubbles are -- a full-window-width card is
        harder to read and stops looking like part of the conversation.
        """
        z = self._z
        try:
            w = self._max_w or z(self.MAX_W)
            self.setMaximumWidth(w)
            self._header.setMaximumWidth(w)
            self._detail.setMaximumWidth(w)
            self._scroll.setMaximumHeight(self._max_h or z(self.MAX_H))
        except (RuntimeError, AttributeError):
            pass

    def set_max_width(self, width: int):
        """Follow the window. Called from ChatWindow._reflow_bubbles, which finds
        this BY CAPABILITY (hasattr) rather than by registration -- the card was
        built once at the old window width and never re-clamped, so resizing the
        chat left it the wrong size."""
        self._max_w = int(width or 0)
        self._apply_bounds()

    def _restyle(self):
        z = self._z
        try:
            t = dict(_FALLBACK_THEME)
            t.update(self._theme() or {})
        except Exception:
            t = dict(_FALLBACK_THEME)
        accent = t.get("accent", _FALLBACK_THEME["accent"])
        border = t.get("border", _FALLBACK_THEME["border"])
        base = t.get("base", _FALLBACK_THEME["base"])

        # INDICATORS ARE DRAWN EXPLICITLY, and that is not decoration.
        # Styling a QRadioButton/QCheckBox at all makes Qt drop its native
        # indicator rendering, so any state left unstyled renders as NOTHING --
        # this card shipped with the CHECKED state unstyled and picking an
        # option made its marker vanish. Every state is spelled out, including
        # the disabled pair: a locked card must still show what was chosen.
        ind = z(13)
        self.setStyleSheet(f"""
            QFrame#qaCard {{ background: transparent; border: none; }}
            QFrame#qaHeader {{
                background: transparent; border: none; border-radius: 6px;
            }}
            QFrame#qaHeader:hover {{ background: rgba(255,255,255,0.05); }}
            QFrame#qaDetail {{
                background: {base}; border: 1px solid {border};
                border-radius: 8px; margin-top: 4px;
            }}
            QLabel {{ color: {_TEXT}; background: transparent; border: none; }}
            QLabel#qaChip {{
                color: {accent}; font-size: {z(9)}px; font-weight: 600;
                letter-spacing: 1px;
            }}
            QLabel#qaPrompt {{ font-size: {z(12)}px; font-weight: 600; }}
            QLabel#qaDesc {{
                color: {_MUTED}; font-size: {z(10)}px; padding-left: {ind + 8}px;
            }}

            QCheckBox, QRadioButton {{
                color: {_TEXT}; font-size: {z(11)}px; spacing: 7px;
                background: transparent; padding: 1px 0;
            }}
            QCheckBox:disabled, QRadioButton:disabled {{ color: {_MUTED}; }}

            QRadioButton::indicator, QCheckBox::indicator {{
                width: {ind}px; height: {ind}px;
                border: 2px solid {border}; background: {base};
            }}
            QRadioButton::indicator {{ border-radius: {ind // 2 + 2}px; }}
            QCheckBox::indicator {{ border-radius: 3px; }}
            QRadioButton::indicator:hover, QCheckBox::indicator:hover {{
                border-color: {accent};
            }}
            QRadioButton::indicator:checked,
            QRadioButton::indicator:checked:disabled {{
                border: {max(3, ind // 3)}px solid {accent}; background: {base};
            }}
            QCheckBox::indicator:checked,
            QCheckBox::indicator:checked:disabled {{
                background: {accent}; border-color: {accent};
            }}
            QRadioButton::indicator:unchecked:disabled,
            QCheckBox::indicator:unchecked:disabled {{
                border-color: {border}; background: transparent;
            }}

            QLineEdit#qaOther {{
                background: {t.get('deep', base)}; border: 1px solid {border};
                border-radius: 6px; color: {_TEXT};
                font-size: {z(11)}px; padding: 5px 8px; margin-top: 3px;
            }}
            QLineEdit#qaOther:focus {{ border-color: {accent}; }}
            QPushButton {{
                background: transparent; border: 1px solid {border};
                border-radius: 6px; color: {_MUTED};
                font-size: {z(10)}px; padding: 4px 11px;
            }}
            QPushButton:hover {{ border-color: {accent}; color: {accent}; }}
            QScrollArea {{ background: transparent; border: none; }}
        """)
        self._toggle_lbl.setStyleSheet(
            f"color: {_DIM}; font-size: {z(8)}px; background: transparent;")
        self._render_summary()

    def apply_zoom(self):
        """Ctrl+scroll hook -- found BY CAPABILITY by theming._zoom_rich_children,
        so this card scales without having to register anywhere."""
        self._restyle()
        self._apply_bounds()


class QaCardsMixin:
    """ChatWindow half of the ask_user surface."""

    def add_qa_card(self, qset, answers=None, locked=False,
                    save_to_history=False):
        """Insert an interview card and return it.

        Live call: ``locked=False``, and the caller owns the wait.
        Session reload: ``locked=True`` with stored answers, nothing to wait on.
        """
        card = QaCard(qset, zoom=self._card_z, answers=answers, locked=locked,
                      max_width=self._bubble_max_width(), theme=self._t)
        card.revised.connect(lambda c=card: self._on_qa_revised(c))
        # The question belongs TO the AI's turn, so it stacks inside the shared
        # turn shell like the tool and memory cards -- not as a top-level row.
        # As its own row it rendered full-window-width and read as a takeover
        # of the chat rather than a step in the reply.
        self._insert_turn_segment(card)
        self.message_widgets.append({
            'widget': card,
            'role': 'system',
            'content_wrapper': card,
            'zoom_restyle': card.apply_zoom,
            '_qa': True,
        })
        if not locked:
            self._animate_message_in(
                card, on_settled=lambda: self.scroll_to_widget(card))
            card.setFocus()
        if save_to_history:
            self.save_qa_to_history(card)
        return card

    def save_qa_to_history(self, card):
        """Persist a resolved card as a ui_event so a reload can rebuild it.

        ui_events are saved with the session and stripped before the provider
        sees them -- the answers already reached the model as the tool
        observation, so this is a UI record, not context.
        """
        try:
            self.controller.ai.conversation_history.append({
                'role': 'ui_event',
                'content': '',
                '_type': 'ask_user',
                '_qa_questions': qa_spec.to_payload(card.qset),
                '_qa_answers': card.answers,
                '_qa_dismissed': bool(card.dismissed),
            })
        except Exception as e:
            log.error(f"[QaCardsMixin.save_qa_to_history] {type(e).__name__}: {e}")

    def _on_qa_revised(self, card):
        """A resolved card was re-answered.

        The agent already acted on the ORIGINAL answers, so history is left
        alone -- rewriting it would misrepresent what the agent was told. The
        correction goes into the input box instead, as a message the user sends
        deliberately.
        """
        try:
            self.prepend_qa_to_input(card.qset, card.answers,
                                     prefix="Correction to my earlier answers:")
        except Exception as e:
            log.error(f"[QaCardsMixin._on_qa_revised] {type(e).__name__}: {e}")

    def prepend_qa_to_input(self, qset, answers, prefix=""):
        """Push an interview's answers into the input box.

        The Claude-web behaviour the user asked for: pressing Esc must not throw
        away what was already filled in -- it turns into the top of their reply,
        so they can keep typing underneath it.
        """
        try:
            block = qa_spec.serialize(qset, answers)
            if not block:
                return
            if prefix:
                block = prefix + "\n" + block
            box = getattr(self, 'input_field', None)
            if box is None:
                return
            if hasattr(box, 'toPlainText'):
                existing = box.toPlainText()
                box.setPlainText(block + "\n\n" + (existing or ""))
            else:
                existing = box.text()
                box.setText(block + "\n\n" + (existing or ""))
            box.setFocus()
        except Exception as e:
            log.error(f"[QaCardsMixin.prepend_qa_to_input] {type(e).__name__}: {e}")
