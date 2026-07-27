"""
systema/ui/dialogs/approval_window.py

Code Approval dialog — the gate before the app runs AI-authored code in
supervised mode. Rebuilt as a small *personal coding agent*:

  • Live theme palette (no hard-coded colours, no emojis).
  • Editable code panel — you can tweak the code before approving.
  • Risk panel — an automatic static scan (systema.security.code_guard) that
    re-runs whenever the code changes ("delta re-approval").
  • Code Reviewer sub-agent ([[code_agent]]) chat: Explain, Suggest a safer
    version, or free-form questions. Same provider, own prompt/tools/interpreter.
    Shows a "responding" indicator and locks input while it works. Per-request
    token usage is displayed.
  • Proposed edits arrive as a DIFF with one-click Apply / Dismiss.
  • Tag-based "don't ask again" — approve, then either silence these operation
    types for the rest of the session (ephemeral) or "always allow these
    operations" (promotes them ask->allow in the saved security policy). No code
    hashes are stored.
  • Reject carries an optional reason back to the AI (REASON: <text>).

Compatibility: the static ``get_approval(code, execution_type, ai_engine,
parent=None) -> (approved, modified_code)`` API and the ``code_edit`` / ``result``
/ ``modified_code`` / ``accept`` / ``close`` attributes are preserved — the
tool_manager and the Android bridge drive the dialog through them.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QTextEdit, QListWidget, QListWidgetItem, QSplitter,
                             QWidget, QCheckBox, QFrame, QLineEdit, QStackedWidget,
                             QScrollArea, QGridLayout, QSizePolicy)

from systema.agents.code_agent import CodeAgent
from systema.security.code_guard import (scan_code, refine_file_ops,
                                         annotate_relative_paths, summarize_findings,
                                         redact_secrets, SEV_DANGER, SEV_CAUTION, SEV_INFO)
from systema.ui import theme
from systema.updater.hunks import build_segments, assemble


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md(text: str) -> str:
    """Render a chat message as markdown -> HTML (Qt rich text)."""
    try:
        import markdown2
        return markdown2.markdown(
            text or "", extras=["fenced-code-blocks", "tables", "break-on-newline"])
    except Exception:
        return _esc(text or "").replace("\n", "<br>")


def _tag_two_way(old: str, new: str) -> list[tuple[str, str]]:
    """Tag a 2-way old/new diff in the shape systema.updater.hunks expects
    (update_del = current code, update_add = the proposal), so the proven
    ReviewSession/segment model drives the inline proposal review."""
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    tagged: list[tuple[str, str]] = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            tagged.extend(("same", ln) for ln in a[a0:a1])
        else:
            tagged.extend(("update_del", ln) for ln in a[a0:a1])
            tagged.extend(("update_add", ln) for ln in b[b0:b1])
    return tagged


class _CodeAgentWorker(QThread):
    """Runs one Code Reviewer instruction off the GUI thread."""
    message = pyqtSignal(str, str)      # (role, text)
    proposal = pyqtSignal(str, str)     # (replacement_code, why)
    tokens = pyqtSignal(str)            # token-meter summary
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, code, execution_type, ai_engine, task, meter,
                 history=None, voice=False):
        super().__init__()
        self._code = code
        self._etype = execution_type
        self._ai = ai_engine
        self._task = task
        self._meter = meter
        self._history = list(history or [])
        self.voice = voice   # request came in by voice -> replies are narrated

    def run(self):
        try:
            agent = CodeAgent(
                self._code, self._etype, ai_engine=self._ai, meter=self._meter,
                history=self._history,
                on_message=lambda role, text: self.message.emit(role, text),
                on_proposal=lambda code, why: self.proposal.emit(code, why or ""))
            summary = agent.run(self._task)
            self.tokens.emit(self._meter.summary() if self._meter else "")
            self.finished_ok.emit(summary or "")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class _HunkWidget(QFrame):
    """One reviewable change block: current code (red) vs proposal (green),
    with Keep mine / Take edit / Edit controls (Manage-dialog verbs). The
    ACTIVE decision renders as a filled accent button with a check glyph, so
    every click gives visible feedback (hunks arrive pre-set to Take edit)."""

    def __init__(self, hunk, palette, parent=None, on_change=None):
        super().__init__(parent)
        self.hunk = hunk
        self.p = palette
        self._on_change = on_change or (lambda: None)
        p = palette
        self.setStyleSheet(
            f"QFrame {{ background: {p['surface']}; border: 1px solid {p['border']};"
            " border-radius: 8px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(4)

        head = QHBoxLayout(); head.setSpacing(6)
        title = QLabel(f"Change {hunk.index + 1}   "
                       f"-{len(hunk.local_lines)}/+{len(hunk.update_lines)}")
        title.setStyleSheet(f"color: {p['muted']}; font-size: 10px; font-weight: 600;"
                            " background: transparent; border: none;")
        head.addWidget(title)
        head.addStretch()
        self.keep_btn = QPushButton("Keep mine")
        self.take_btn = QPushButton("Take edit")
        self.edit_btn = QPushButton("Edit")
        for b in (self.keep_btn, self.take_btn, self.edit_btn):
            b.setFixedHeight(22)
            head.addWidget(b)
        self.keep_btn.clicked.connect(lambda: self._decide("local"))
        self.take_btn.clicked.connect(lambda: self._decide("update"))
        self.edit_btn.clicked.connect(self._toggle_editor)
        lay.addLayout(head)

        mono = "font-family: Consolas, monospace; font-size: 10px;"
        self.old_lbl = QLabel()
        self.old_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.old_lbl.setText("".join(hunk.local_lines) or " ")
        self.old_lbl.setWordWrap(False)
        self.new_lbl = QLabel()
        self.new_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.new_lbl.setText("".join(hunk.update_lines) or " ")
        self.new_lbl.setWordWrap(False)
        lay.addWidget(self.old_lbl)
        lay.addWidget(self.new_lbl)
        self._mono = mono

        self.editor = QTextEdit()
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.editor.setStyleSheet(
            f"QTextEdit {{ background: {p['bg']}; border: 1px solid {p['accent']};"
            f" border-radius: 6px; padding: 4px; {mono} color: {p['text']}; }}")
        self.editor.setMaximumHeight(120)
        self.editor.hide()
        self.editor.textChanged.connect(self._edited)
        lay.addWidget(self.editor)

        self._restyle()

    def _decide(self, decision):
        self.hunk.decision = decision
        self.editor.hide()
        self._restyle()
        self._on_change()

    def _toggle_editor(self):
        if self.editor.isVisible():
            self.editor.hide()
            self._restyle()
            return
        self.editor.blockSignals(True)
        self.editor.setPlainText(self.hunk.resolved_text())
        self.editor.blockSignals(False)
        self.editor.show()
        self._restyle()

    def _edited(self):
        self.hunk.decision = "edited"
        self.hunk.edited_text = self.editor.toPlainText()
        self._restyle()
        self._on_change()

    def _verb_css(self, active):
        p = self.p
        if active:
            return (f"QPushButton {{ background: {p['accent']}; color: #05070a;"
                    f" border: 1px solid {p['accent']}; border-radius: 6px;"
                    " padding: 2px 10px; font-size: 10px; font-weight: 700; }}")
        return (f"QPushButton {{ background: {p['surface2']}; color: {p['muted']};"
                f" border: 1px solid {p['border']}; border-radius: 6px;"
                " padding: 2px 10px; font-size: 10px; }}"
                f"QPushButton:hover {{ border: 1px solid {p['accent']};"
                f" color: {p['text']}; }}")

    def _restyle(self):
        p = self.p
        d = self.hunk.decision
        # The chosen side is vivid; the other dims. Edited dims both (the
        # editor's text is what will be applied).
        old_on = d == "local"
        new_on = d == "update"
        edited = d == "edited"
        self.old_lbl.setStyleSheet(
            f"background: rgba(230,90,90,{0.16 if old_on else 0.05});"
            f" color: {'#f2b8b5' if old_on else p['muted']};"
            f" border: none; border-radius: 4px; padding: 3px 6px; {self._mono}")
        self.new_lbl.setStyleSheet(
            f"background: rgba(80,200,120,{0.16 if new_on else 0.05});"
            f" color: {'#c6ecc6' if new_on else p['muted']};"
            f" border: none; border-radius: 4px; padding: 3px 6px; {self._mono}")
        # The active verb is filled + checked; the others are outlined.
        self.keep_btn.setText(("✓ " if old_on else "") + "Keep mine")
        self.take_btn.setText(("✓ " if new_on else "") + "Take edit")
        self.edit_btn.setText(("✓ " if edited else "") + ("Edited" if edited else "Edit"))
        self.keep_btn.setStyleSheet(self._verb_css(old_on))
        self.take_btn.setStyleSheet(self._verb_css(new_on))
        self.edit_btn.setStyleSheet(self._verb_css(edited))


class _ProposalReviewView(QWidget):
    """Page 1 of the code stack: the whole snippet rendered inline — dimmed
    context plus per-hunk review widgets — over the hunks.py segment model.
    Apply resolved composes every hunk's decision back into the code editor."""

    def __init__(self, dialog):
        super().__init__()
        self._dlg = dialog
        self.segments = []
        p = dialog.p
        self.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.why_lbl = QLabel("")
        self.why_lbl.setWordWrap(True)
        self.why_lbl.setStyleSheet(
            f"color: {p['accent']}; font-size: 11px; font-weight: 600;"
            " background: transparent;")
        lay.addWidget(self.why_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ background: {p['surface']}; border: 1px solid {p['border']};"
            " border-radius: 8px; }}")
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._col = QVBoxLayout(self._inner)
        self._col.setContentsMargins(8, 8, 8, 8)
        self._col.setSpacing(6)
        self.scroll.setWidget(self._inner)
        lay.addWidget(self.scroll, stretch=1)

        row = QHBoxLayout(); row.setSpacing(6)
        self.hint_lbl = QLabel("All changes are taken by default — adjust any, then apply.")
        self.hint_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 10px; background: transparent;")
        row.addWidget(self.hint_lbl)
        row.addStretch()
        self.dismiss_btn = QPushButton("Dismiss")
        self.dismiss_btn.setStyleSheet(dialog._btn())
        self.dismiss_btn.setToolTip("Discard the proposal and go back to the code.")
        self.dismiss_btn.clicked.connect(lambda: dialog._finish_review(apply=False))
        row.addWidget(self.dismiss_btn)
        self.apply_btn = QPushButton("Apply edit")
        self.apply_btn.setStyleSheet(dialog._btn(primary=True))
        self.apply_btn.setToolTip("Write the resolved changes into the code editor.")
        self.apply_btn.clicked.connect(lambda: dialog._finish_review(apply=True))
        row.addWidget(self.apply_btn)
        lay.addLayout(row)

        # File-op mode: Apply/Dismiss here would duplicate the footer Approve/Reject
        # (and the reviewer chat they log to doesn't exist). Hide them; the footer
        # drives apply-on-approve / discard-on-reject.
        if dialog._file_edit is not None:
            self.hint_lbl.setText("Adjust any hunk below, then use Approve / Reject.")
            self.dismiss_btn.hide()
            self.apply_btn.hide()

    def load(self, segments, why):
        self.segments = segments
        self.why_lbl.setText("Proposed edit" + (f": {why}" if why else ""))
        while self._col.count():
            item = self._col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        p = self._dlg.p
        mono = "font-family: Consolas, monospace; font-size: 10px;"
        for seg in segments:
            if seg.kind == "context":
                lbl = QLabel()
                lbl.setTextFormat(Qt.TextFormat.PlainText)
                lbl.setText(self._trim_context(seg.text))
                lbl.setWordWrap(False)
                lbl.setStyleSheet(f"color: {p['muted']}; background: transparent;"
                                  f" border: none; padding: 0 6px; {mono}")
                self._col.addWidget(lbl)
            elif seg.hunk is not None:
                self._col.addWidget(_HunkWidget(seg.hunk, p,
                                                on_change=self.refresh_footer))
        self._col.addStretch()
        self.refresh_footer()

    def refresh_footer(self):
        """Live Apply label: what pressing it will actually do."""
        taken = kept = edited = 0
        for seg in self.segments:
            if seg.kind != "hunk" or seg.hunk is None:
                continue
            d = seg.hunk.decision
            if d == "update":
                taken += 1
            elif d == "edited":
                edited += 1
            else:
                kept += 1
        total = taken + kept + edited
        if total and taken == total:
            label = f"Apply edit (all {total} taken)" if total > 1 else "Apply edit"
        else:
            parts = []
            if taken:
                parts.append(f"{taken} taken")
            if edited:
                parts.append(f"{edited} edited")
            if kept:
                parts.append(f"{kept} kept")
            label = "Apply edit (" + " · ".join(parts) + ")" if parts else "Apply edit"
        self.apply_btn.setText(label)

    @staticmethod
    def _trim_context(text, keep=3):
        lines = text.splitlines()
        if len(lines) <= keep * 2 + 1:
            return text.rstrip("\n")
        omitted = len(lines) - keep * 2
        return "\n".join(lines[:keep] + [f"... {omitted} unchanged lines ..."]
                         + lines[-keep:])

    def take_all_remaining(self):
        """Voice 'apply' / one-shot apply: every unedited hunk takes the proposal."""
        for seg in self.segments:
            if seg.kind == "hunk" and seg.hunk is not None and seg.hunk.decision != "edited":
                seg.hunk.decision = "update"

    def assembled(self):
        return assemble(self.segments)


class CodeApprovalDialog(QDialog):
    """Review, edit, and approve one code snippet before it runs."""

    _SEV_COLOR_KEY = {SEV_DANGER: "red", SEV_CAUTION: "yellow", SEV_INFO: "muted"}

    # Size band for ONE decision column (a message box and the button under it).
    # Both columns get the SAME band, which is what makes them equal regardless
    # of their placeholders being different lengths — a grid with equal stretch
    # still starts from each column's own size hint.
    DECIDE_COL_MIN_W = 190
    DECIDE_COL_MAX_W = 340
    # Widest the whole block may grow: two columns plus the gap between them.
    # Past this the controls only get emptier, not more usable.
    DECIDE_MAX_W = DECIDE_COL_MAX_W * 2 + 10

    def __init__(self, code, execution_type, ai_engine, parent=None,
                 file_edit=None, annotation=""):
        super().__init__(parent)
        self.code = code
        self.execution_type = execution_type
        self.ai_engine = ai_engine
        # The step's own one-line label ("Attaching ValleyMed logo for review").
        # The model already wrote it to narrate the step, so it is the fastest
        # way to see WHAT you are being asked to approve — shown as a band above
        # the title rather than left buried in the code.
        self.annotation = (annotation or "").strip()
        self.result = None                 # 'accept' | 'reject' | None
        self.modified_code = code
        self.reject_reason = ""            # optional text captured on Reject
        self.accept_note = ""              # optional text captured on Accept
        self._last_findings = []           # latest static-scan findings
        # File-edit mode: reviewing a PROPOSED FILE CHANGE (read/edit/write
        # subsystem) instead of code to execute. `code` holds the proposed NEW
        # content; the review page opens immediately on the old->new diff and
        # Approve auto-applies any unresolved hunks.
        self._file_edit = file_edit if isinstance(file_edit, dict) else None

        self._worker = None
        self._typing_timer = None
        self._typing_dots = 0
        self._agent_history = []           # prior reviewer turns (context for follow-ups)
        self._awaiting_voice_confirm = False
        self._review_why = ""
        self._closed = False
        self._mini_card = None             # ApprovalNotifCard while the compact toast is up
        self._expand_requested = False     # set by voice 'expand' / advanced speech; the card polls it

        # Live theme palette (falls back to the default theme if no controller).
        controller = getattr(ai_engine, "controller", None)
        self._controller = controller
        try:
            self.p = theme.current_palette(controller)
        except Exception:
            self.p = theme.resolve_palette(theme.THEMES[theme.DEFAULT_THEME_KEY])

        from systema.common.token_meter import TokenMeter
        self._meter = TokenMeter("Code Reviewer")

        self._build()
        self._refresh_security()

        # File-edit mode: the editor holds the CURRENT file content and the
        # proposed new content opens immediately as a per-hunk review. Read the
        # NEW content from the payload — setPlainText(old) fires textChanged,
        # which rewrites self.code to the old text before we can use it.
        if self._file_edit is not None:
            proposed_new = self._file_edit.get('new') or self.code
            self.code_edit.setPlainText(self._file_edit.get('old') or '')
            self._on_proposal(proposed_new,
                              f"proposed change to {self._file_edit.get('path', '')}")

        # Center on the chat window / primary screen (never the floating widget)
        from systema.ui.dialogs.dialog_utils import center_on_primary
        QTimer.singleShot(0, lambda: center_on_primary(self))

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self):
        p = self.p
        self.setWindowTitle("File Change Approval" if self._file_edit is not None
                            else "Code Approval Required")
        self.setModal(True)
        self.setMinimumSize(940, 560)
        self.resize(1200, 660)
        self.setWindowFlags(Qt.WindowType.Dialog
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setStyleSheet(
            f"QDialog {{ background-color: {p['bg']}; }}"
            f"QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        # Title + blurb vary by what is being approved: a code run vs a file
        # write/edit (each file tool gets its own wording).
        if self._file_edit is not None:
            _is_create = not (self._file_edit.get('old'))
            _pathname = self._file_edit.get('path', 'a file')
            if self.execution_type == 'write_file':
                title_text = "File write approval"
                _verb = "create" if _is_create else "overwrite"
                desc_text = (f"The AI wants to {_verb} {_pathname}. Review the full "
                             "contents below and approve to save the file.")
            else:  # edit_file (surgical change)
                title_text = "File edit approval"
                desc_text = (f"The AI wants to edit {_pathname}. Review the diff, adjust "
                             "any block, and approve to write the changes.")
        else:
            title_text = "Code execution approval"
            where = ("python interpreter" if self.execution_type == "python_interpreter"
                     else "direct execution")
            desc_text = (f"The AI wants to run the code below ({where}). Review, edit if "
                         "needed, and approve. You can turn this prompt off in Settings.")

        # ── What this step IS, first thing on the page ───────────────────────
        # Above the title on purpose: the title only says "code execution
        # approval" (always true, tells you nothing), while the annotation says
        # what the model is actually trying to do. Accent band so it reads at a
        # glance without opening the code. Omitted entirely when the step
        # carried no annotation — an empty band is worse than none.
        if self.annotation:
            ann = QLabel(self.annotation)
            ann.setWordWrap(True)
            ann.setTextFormat(Qt.TextFormat.PlainText)
            ann.setStyleSheet(
                f"QLabel {{ color: {p['text']}; font-size: 13px; font-weight: 600;"
                f" background: {p['glow']};"
                f" border: 1px solid {p['accent_dk']}; border-left: 3px solid {p['accent']};"
                f" border-radius: 6px; padding: 8px 12px; }}")
            ann.setToolTip("What the AI said this step is for")
            root.addWidget(ann)

        title = QLabel(title_text)
        title.setStyleSheet(f"color: {p['text']}; font-size: 15px; font-weight: 600;"
                            " background: transparent;")
        root.addWidget(title)
        desc = QLabel(desc_text)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        root.addWidget(desc)

        # File ops are "just files, not code" — no static risk scan, no Code
        # Reviewer sub-agent. Show only the diff/content side, full width.
        if self._file_edit is not None:
            root.addWidget(self._build_code_side(), stretch=1)
        else:
            split = QSplitter(Qt.Orientation.Horizontal)
            split.setChildrenCollapsible(False)
            split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")
            split.addWidget(self._build_code_side())
            split.addWidget(self._build_side_panel())
            split.setSizes([680, 420])
            root.addWidget(split, stretch=1)

        # ── "Don't ask again" controls, on their own line ────────────────────
        foot = QHBoxLayout()
        cb_css = (f"QCheckBox {{ color: {p['muted']}; font-size: 11px; background: transparent; }}"
                  f"QCheckBox::indicator {{ width: 14px; height: 14px; }}")
        self.session_allow_cb = QCheckBox("Don't ask again this session")
        self.session_allow_cb.setStyleSheet(cb_css)
        self.session_allow_cb.setToolTip(
            "Auto-approve these operation types for the rest of this session "
            "(ephemeral — cleared on restart).")
        self.persist_allow_cb = QCheckBox("Always allow these operations")
        self.persist_allow_cb.setStyleSheet(cb_css)
        self.persist_allow_cb.setToolTip(
            "Set these operation types to 'allow' in your saved security policy "
            "(Settings > Security). Never overrides a 'deny'.")
        foot.addWidget(self.session_allow_cb)
        foot.addWidget(self.persist_allow_cb)
        foot.addStretch()
        root.addLayout(foot)

        # ── The decision: two symmetric columns, each message above its own
        #    button ────────────────────────────────────────────────────────────
        # Two fields, not one: the reason explains a refusal ("that path is
        # wrong"), the note steers an approval ("go ahead, but log to stderr").
        # Only the one matching the button you press is sent, so a stale value
        # in the other can never leak into the observation.
        #
        # A GRID, so each field sits directly over the button it belongs to and
        # both columns are exactly the same width. Strung along one row they
        # read as four unrelated controls, and it was not obvious that the left
        # box feeds Reject and the right one feeds Accept.
        # The pair is CENTERED and capped, not stretched across the window. On a
        # 1200px dialog, full-width columns gave each control ~590px — a text
        # box that wide for a one-line reason, and a "Reject" button with a
        # field of empty space around its label. It still adapts: the cap is
        # what it may grow TO, and the columns shrink below it on a narrow
        # window.
        self._decide_box = QWidget()
        self._decide_box.setStyleSheet("background: transparent;")
        self._decide_box.setMaximumWidth(self.DECIDE_MAX_W)
        # Expanding UP TO the cap: without this the box settles on its size
        # hint, and the two columns end up different widths because the two
        # placeholders are different lengths. Taking the cap lets the equal
        # column stretch actually split it into matching halves.
        self._decide_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Preferred)
        decide = QGridLayout(self._decide_box)
        decide.setContentsMargins(0, 0, 0, 0)
        decide.setHorizontalSpacing(10)
        decide.setVerticalSpacing(6)
        # Equal stretch INSIDE the capped box keeps the two halves symmetric.
        decide.setColumnStretch(0, 1)
        decide.setColumnStretch(1, 1)

        _note_css = (f"QLineEdit {{ background: {p['bg']}; color: {p['text']}; "
                     f"border: 1px solid {p['border']}; border-radius: 6px; "
                     f"padding: 5px 8px; font-size: 11px; }}"
                     f"QLineEdit:focus {{ border-color: {p['accent_dk']}; }}")

        self.reason_edit = QLineEdit()
        self.reason_edit.setPlaceholderText("Reason for rejecting (optional)")
        self.reason_edit.setStyleSheet(_note_css)
        self.reason_edit.setToolTip(
            "Sent to the AI only if you Reject, so it knows WHY and can try "
            "something else instead of repeating itself.")
        self.reason_edit.returnPressed.connect(self.on_reject)
        self.reason_edit.setMinimumWidth(self.DECIDE_COL_MIN_W)
        self.reason_edit.setMaximumWidth(self.DECIDE_COL_MAX_W)
        decide.addWidget(self.reason_edit, 0, 0)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Note to the AI when approving (optional)")
        self.note_edit.setStyleSheet(_note_css)
        self.note_edit.setToolTip(
            "Sent to the AI only if you Approve — steer the step without "
            "stopping it (\"fine, but write to data/ not the desktop\").")
        self.note_edit.returnPressed.connect(self.on_accept)
        self.note_edit.setMinimumWidth(self.DECIDE_COL_MIN_W)
        self.note_edit.setMaximumWidth(self.DECIDE_COL_MAX_W)
        decide.addWidget(self.note_edit, 0, 1)

        self.reject_btn = QPushButton("Reject")
        self.reject_btn.setStyleSheet(self._btn(kind="danger"))
        self.reject_btn.clicked.connect(self.on_reject)
        self.reject_btn.setMinimumHeight(34)
        decide.addWidget(self.reject_btn, 1, 0)

        if self._file_edit is not None:
            _accept_label = ("Approve write" if self.execution_type == 'write_file'
                             else "Approve edit")
        else:
            _accept_label = "Accept and execute"
        self.accept_btn = QPushButton(_accept_label)
        self.accept_btn.setStyleSheet(self._btn(primary=True))
        self.accept_btn.clicked.connect(self.on_accept)
        self.accept_btn.setDefault(True)
        self.accept_btn.setMinimumHeight(34)
        decide.addWidget(self.accept_btn, 1, 1)

        # Centered under the content rather than pinned to either edge — the
        # two choices carry equal weight, so neither should sit in a corner.
        # The box needs a stretch share of its own, or the two side stretches
        # take everything and it collapses back to its size hint.
        _decide_row = QHBoxLayout()
        _decide_row.setContentsMargins(0, 0, 0, 0)
        _decide_row.addStretch(1)
        _decide_row.addWidget(self._decide_box, 10)
        _decide_row.addStretch(1)
        root.addLayout(_decide_row)

    def _build_code_side(self) -> QWidget:
        p = self.p
        w = QWidget(); w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(self._h("Code to run"))

        self.code_edit = QTextEdit()
        self.code_edit.setPlainText(self.code)
        self.code_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_edit.setStyleSheet(self._edit_style(mono=True))
        font = QFont("Consolas", 10); font.setStyleHint(QFont.StyleHint.Monospace)
        self.code_edit.setFont(font)
        self.code_edit.textChanged.connect(self._on_code_changed)

        # One stacked area: page 0 = the editable code, page 1 = the inline
        # per-hunk proposal review (replaces the old bottom diff panel).
        self.code_stack = QStackedWidget()
        self.code_stack.setStyleSheet("background: transparent;")
        self.code_stack.addWidget(self.code_edit)
        self.review_view = _ProposalReviewView(self)
        self.code_stack.addWidget(self.review_view)
        # Structural invariant: Approve is disabled EXACTLY while the review
        # page is showing (approving mid-review would run the pre-proposal
        # code). Keyed off the page change itself so no code path can leave
        # the button stuck disabled.
        self.code_stack.currentChanged.connect(self._on_code_page_changed)
        lay.addWidget(self.code_stack, stretch=1)
        return w

    def _on_code_page_changed(self, _idx):
        reviewing = self.code_stack.currentWidget() is self.review_view
        if self._file_edit is not None:
            # File-edit mode: approving FROM the review is the normal flow —
            # on_accept composes the resolved hunks itself.
            self.accept_btn.setEnabled(True)
            self.accept_btn.setToolTip(
                "Approve writes the resolved changes to the file."
                if reviewing else "")
            return
        self.accept_btn.setEnabled(not reviewing)
        self.accept_btn.setToolTip(
            "Resolve or dismiss the proposed edit first." if reviewing else "")

    def _build_side_panel(self) -> QWidget:
        p = self.p
        w = QWidget(); w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 0, 0, 0); lay.setSpacing(6)

        # ── risk panel ────────────────────────────────────────────────────
        lay.addWidget(self._h("Static risk scan"))
        self.scan_lbl = QLabel("")
        self.scan_lbl.setWordWrap(True)
        self.scan_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        lay.addWidget(self.scan_lbl)
        self.risk_list = QListWidget()
        self.risk_list.setStyleSheet(self._list())
        self.risk_list.setMaximumHeight(150)
        lay.addWidget(self.risk_list)

        # ── code reviewer sub-agent ───────────────────────────────────────
        lay.addWidget(self._h("Code Reviewer (AI)"))
        row = QHBoxLayout(); row.setSpacing(6)
        self.explain_btn = QPushButton("Explain and assess")
        self.explain_btn.setStyleSheet(self._btn())
        self.explain_btn.clicked.connect(self._ai_explain)
        self.improve_btn = QPushButton("Suggest a safer version")
        self.improve_btn.setStyleSheet(self._btn())
        self.improve_btn.clicked.connect(self._ai_improve)
        row.addWidget(self.explain_btn); row.addWidget(self.improve_btn)
        lay.addLayout(row)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setStyleSheet(self._edit_style())
        lay.addWidget(self.chat, stretch=1)

        self.token_lbl = QLabel("tokens — no requests yet")
        self.token_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 10px; background: transparent;")
        lay.addWidget(self.token_lbl)

        self.typing_lbl = QLabel("")
        self.typing_lbl.setStyleSheet(
            f"color: #05070a; background: {p['accent']}; font-size: 12px; font-weight: 700;"
            " border-radius: 6px; padding: 7px 10px;")
        self.typing_lbl.setVisible(False)
        lay.addWidget(self.typing_lbl)

        ask = QHBoxLayout(); ask.setSpacing(6)
        self.ask_field = QTextEdit()
        self.ask_field.setPlaceholderText("Ask the Code Reviewer…")
        self.ask_field.setMaximumHeight(52)
        self.ask_field.setStyleSheet(self._edit_style())
        ask.addWidget(self.ask_field, stretch=1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet(self._btn())
        self.send_btn.clicked.connect(self._ai_ask)
        ask.addWidget(self.send_btn)
        lay.addLayout(ask)
        return w

    # ── security scan (auto on open + on every edit = delta re-approval) ─────
    def _on_code_changed(self):
        self.code = self.code_edit.toPlainText()
        # A debounce keeps typing smooth; snippets are small so 250 ms is plenty.
        if getattr(self, "_scan_timer", None) is None:
            self._scan_timer = QTimer(self)
            self._scan_timer.setSingleShot(True)
            self._scan_timer.timeout.connect(self._refresh_security)
        self._scan_timer.start(250)

    def _scan(self, code):
        """Static scan + namespace-aware create/edit/write refinement, so the
        categories shown here (and acted on by 'always allow') match exactly what
        the execution gate will apply."""
        findings = scan_code(code)
        try:
            ns = None
            tm = getattr(self.ai_engine, "tool_manager", None)
            py = (getattr(tm, "tools", {}) or {}).get("python") if tm else None
            ns = getattr(py, "namespace", None)
            findings = refine_file_ops(findings, code, ns)
            findings = annotate_relative_paths(findings, code, ns)
        except Exception:
            pass
        return findings

    def _refresh_security(self):
        # File-op dialogs have no static-scan panel (dropped for simplicity).
        if getattr(self, 'risk_list', None) is None:
            return
        p = self.p
        code = self.code_edit.toPlainText()
        findings = self._scan(code)
        self._last_findings = findings
        self.risk_list.clear()
        for f in findings:
            snippet, _ = redact_secrets(f.snippet)
            it = QListWidgetItem(f"[{f.severity}] {f.category}  L{f.line}: {f.note}"
                                 + (f"   {snippet.strip()}" if snippet.strip() else ""))
            it.setForeground(QColor(p[self._SEV_COLOR_KEY.get(f.severity, "text")]))
            self.risk_list.addItem(it)

        summary = summarize_findings(findings)
        self.scan_lbl.setText(summary + (". Review before approving."
                                         if findings else "."))

    # ── AI panel ─────────────────────────────────────────────────────────────
    def _ai_busy(self, busy):
        for b in (self.explain_btn, self.improve_btn, self.send_btn):
            b.setEnabled(not busy)
        self.ask_field.setReadOnly(busy)
        self.typing_lbl.setVisible(busy)
        if busy:
            self._typing_dots = 0
            if self._typing_timer is None:
                self._typing_timer = QTimer(self)
                self._typing_timer.timeout.connect(self._tick_typing)
            self._tick_typing()
            self._typing_timer.start(450)
        elif self._typing_timer is not None:
            self._typing_timer.stop()

    _SPIN = ("◐", "◓", "◑", "◒")

    def _tick_typing(self):
        self._typing_dots = (self._typing_dots + 1) % 4
        frame = self._SPIN[self._typing_dots]
        self.typing_lbl.setText(f"  {frame}   Code Reviewer is responding, please wait"
                                + "." * self._typing_dots + "   ")

    def _worker_running(self):
        """True only while a live worker thread is running. A finished worker is
        deleteLater'd — touching its methods afterwards raises RuntimeError, so
        this is the ONLY place allowed to poke at self._worker's state."""
        w = self._worker
        if w is None:
            return False
        try:
            return w.isRunning()
        except RuntimeError:            # wrapped C++ object already deleted
            self._worker = None
            return False

    def _on_worker_finished(self, w):
        """Drop our reference BEFORE the QThread object is destroyed, so no
        later click can touch a dead wrapper (the 'Approve looks disabled' bug)."""
        if self._worker is w:
            self._worker = None
        w.deleteLater()

    def _run_agent(self, task, voice=False):
        if self._worker_running():
            self._append_chat("system", "Please wait — the Code Reviewer is still "
                                        "responding to your last message.")
            return
        if self.ai_engine is None:
            self._append_chat("system", "No AI engine available.")
            return
        self._ai_busy(True)
        w = _CodeAgentWorker(self.code_edit.toPlainText(), self.execution_type,
                             self.ai_engine, task, self._meter,
                             history=list(self._agent_history), voice=voice)
        w.message.connect(lambda role, text, v=voice: self._on_agent_message(role, text, v))
        w.proposal.connect(self._on_proposal)
        w.tokens.connect(lambda s: self.token_lbl.setText(s or self.token_lbl.text()))
        w.finished_ok.connect(self._on_agent_done)
        w.failed.connect(self._on_agent_failed)
        w.finished.connect(lambda w=w: self._on_worker_finished(w))
        self._worker = w
        self._push_history("user", task)
        w.start()

    def _push_history(self, role, content):
        """Keep a capped rolling context so follow-up questions aren't
        context-free (the agent itself is single-shot)."""
        if content and content.strip():
            self._agent_history.append({"role": role, "content": content})
            self._agent_history = self._agent_history[-16:]

    def _on_agent_message(self, role, text, voice=False):
        self._append_chat(role, text)
        if role == "agent":
            self._push_history("assistant", text)
            if voice:
                self._speak(text)

    def _on_agent_done(self, _summary):
        self._ai_busy(False)

    def _on_agent_failed(self, msg):
        self._ai_busy(False)
        self._append_chat("system", f"Error: {msg}")
        try:
            self.token_lbl.setText(self._meter.summary() or self.token_lbl.text())
        except Exception:
            pass

    def _ai_explain(self):
        self._append_chat("you", "Explain this code and assess its safety.")
        self._run_agent("Explain in plain language what this code does and whether it "
                        "is safe, risky, or dangerous. Do not propose an edit unless "
                        "there is a real risk to fix.")

    def _ai_improve(self):
        self._append_chat("you", "Suggest a safer version of this code.")
        self._run_agent("Make this code safer without changing its intended behaviour "
                        "(scope deletes to the app dir, add guards, remove hardcoded "
                        "secrets, avoid shell=True). Propose the edit as the COMPLETE "
                        "revised snippet with a short reason. If it is already fine, "
                        "just say so.")

    def _ai_ask(self):
        q = self.ask_field.toPlainText().strip()
        if not q:
            return
        self.ask_field.clear()
        self._append_chat("you", q)
        self._run_agent(q)

    def _append_chat(self, role, text):
        # File-op dialogs have no Code Reviewer panel — self.chat is never built,
        # so every reviewer-chat message (incl. the ones _finish_review emits) is
        # a safe no-op here. Without this guard, approving a file edit throws.
        if getattr(self, 'chat', None) is None:
            return
        p = self.p
        who = {"you": p["accent"], "you-voice": p["accent"], "agent": p["text"],
               "system": p["muted"]}.get(role, p["text"])
        label = {"you": "You", "you-voice": "You (voice)", "agent": "Code Reviewer",
                 "system": "System"}.get(role, role)
        self.chat.append(
            f'<div style="margin:6px 0"><span style="color:{who};font-weight:600">'
            f'{label}:</span><div style="color:{p["text"]}">{_md(text)}</div></div>')
        cur = self.chat.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.chat.setTextCursor(cur)

    # ── proposed edit -> inline per-hunk review ──────────────────────────────
    def _review_active(self):
        return self.code_stack.currentWidget() is self.review_view

    def _on_proposal(self, new_code, why):
        if not isinstance(new_code, str) or not new_code.strip():
            return
        old = self.code_edit.toPlainText()
        tagged = _tag_two_way(old, new_code)
        segments = build_segments(tagged)
        if not any(s.kind == "hunk" for s in segments):
            self._append_chat("system", "The proposal is identical to the current code.")
            return
        if self._review_active():
            self._append_chat("system", "A new proposal replaced the one under review.")
        self._review_why = why or ""
        self._push_history("assistant",
                           f"(proposed an edit{': ' + why if why else ''})")
        self.review_view.load(segments, why)
        self.code_stack.setCurrentWidget(self.review_view)   # disables Approve
                                                             # via _on_code_page_changed

    def _finish_review(self, apply):
        applied = False
        if self._review_active() and apply:
            self.code_edit.setPlainText(self.review_view.assembled())
            self._append_chat("system", "Applied the resolved edit. Re-scanned; "
                                        "review before approving.")
            applied = True
        elif self._review_active():
            self._append_chat("system", "Proposal dismissed.")
        self.code_stack.setCurrentWidget(self.code_edit)     # re-enables Approve
        self._review_why = ""
        if applied:
            self._flash_code_edit()

    def _flash_code_edit(self):
        """Brief accent pulse on the editor so an applied edit is unmissable."""
        base = self._edit_style(mono=True)
        self.code_edit.setStyleSheet(
            base + f"QTextEdit {{ border: 2px solid {self.p['accent']}; }}")
        QTimer.singleShot(700, lambda: self.code_edit.setStyleSheet(base))

    # Thin wrappers kept for callers and the voice commands: 'apply' takes every
    # remaining hunk, 'dismiss' discards the review.
    def _apply_proposal(self):
        if not self._review_active():
            return
        self.review_view.take_all_remaining()
        self._finish_review(apply=True)

    def _dismiss_proposal(self):
        if not self._review_active():
            return
        self._finish_review(apply=False)

    # ── voice approval ───────────────────────────────────────────────────────
    def _speak(self, text):
        """Narrate via the app's voice pipeline (no-op when voice mode is off)."""
        try:
            if self._controller is not None:
                self._controller.speak_text(text)
        except Exception:
            pass

    def announce_for_voice(self):
        """Spoken heads-up when the dialog opens (setting: voice_approval_announce)."""
        findings = self._last_findings or []
        risky = sum(1 for f in findings if f.severity in (SEV_DANGER, SEV_CAUTION))
        self._speak(f"Approval required. {len(findings)} finding"
                    f"{'s' if len(findings) != 1 else ''}, {risky} risky.")

    def handle_voice_transcript(self, text):
        """Route a voice transcript that arrived while this dialog is open.

        Command words act directly (deny instantly; approve only as an exact
        whole utterance, with a spoken confirm gate over danger findings). In
        advanced mode, anything else becomes a Code Reviewer request whose
        reply is narrated back.
        """
        if self._closed:
            return
        settings = getattr(self._controller, "settings", None) or {}
        if not settings.get("voice_approval_enabled", True):
            return
        from systema.voice.approval_commands import match_command
        cmd = match_command(text, settings.get("voice_approval_custom_words") or {})

        if cmd == "deny":
            if self._awaiting_voice_confirm:
                self._awaiting_voice_confirm = False
                self._append_chat("system", "Voice: approval cancelled.")
                self._speak("Cancelled.")
                return
            self._append_chat("system", "Voice: rejected.")
            self.on_reject()
            return

        if cmd == "confirm":
            if self._awaiting_voice_confirm:
                self._awaiting_voice_confirm = False
                self._append_chat("system", "Voice: confirmed — executing.")
                self.on_accept()
            else:
                self._append_chat("system", "Voice: nothing is awaiting confirmation.")
            return

        if cmd == "approve":
            danger = [f for f in (self._last_findings or [])
                      if f.severity == SEV_DANGER]
            if danger and settings.get("voice_approval_confirm_risky", True):
                self._awaiting_voice_confirm = True
                msg = (f"This code has {len(danger)} danger finding"
                       f"{'s' if len(danger) != 1 else ''}. Say confirm to execute, "
                       "or a deny word to cancel.")
                self._append_chat("system", "Voice: " + msg)
                self._speak(msg)
            else:
                self._append_chat("system", "Voice: approved — executing.")
                self.on_accept()
            return

        if cmd == "apply":
            if self._review_active():
                self._append_chat("system", "Voice: applying the proposed edit.")
                self._apply_proposal()
            else:
                self._append_chat("system", "Voice: no proposal to apply.")
            return

        if cmd == "dismiss":
            if self._review_active():
                self._append_chat("system", "Voice: proposal dismissed.")
                self._dismiss_proposal()
            else:
                self._append_chat("system", "Voice: no proposal to dismiss.")
            return

        if cmd == "expand":
            if self._mini_visible():
                self._append_chat("system", "Voice: expanding to the full review.")
                self._expand_requested = True
            # Already expanded: nothing to do.
            return

        # Not a command word.
        if settings.get("voice_approval_mode", "basic") == "advanced":
            # With the compact toast up you'd want to SEE the reviewer exchange
            # (and any proposal), so expand alongside routing the request.
            if self._mini_visible():
                self._expand_requested = True
            self._append_chat("you-voice", text)
            self._run_agent(text, voice=True)
        else:
            self._append_chat("system", 'Voice: not a recognized command. Say '
                                        '"approve" or "deny".')

    def _mini_visible(self):
        """True while the compact approval toast is showing instead of us."""
        card = self._mini_card
        try:
            return card is not None and card.isVisible()
        except Exception:
            return False

    # ── accept / reject ──────────────────────────────────────────────────────
    def _risky_categories(self):
        """Operation categories a "don't ask again" choice should act on. For a
        file op it is the file-subsystem category (file_edit / file_create) — NOT
        a scan of the file's contents (which would mis-classify the file body as
        executable code). For a code run it is the caution/danger scan findings."""
        if self._file_edit is not None:
            try:
                from systema.security.code_guard import CAT_FILE_EDIT, CAT_FILE_CREATE
                return [CAT_FILE_EDIT if self._file_edit.get('old') else CAT_FILE_CREATE]
            except Exception:
                return ['file_edit' if self._file_edit.get('old') else 'file_create']
        try:
            findings = self._last_findings or self._scan(self.code_edit.toPlainText())
            return sorted({f.category for f in findings
                           if f.severity in (SEV_DANGER, SEV_CAUTION)})
        except Exception:
            return []

    def on_reject(self):
        self._stop_worker()
        self.result = 'reject'
        try:
            self.reject_reason = self.reason_edit.text().strip()
        except Exception:
            self.reject_reason = ""
        self.accept_note = ""        # only the pressed button's field is sent
        self.close()

    def on_accept(self):
        self._stop_worker()
        # File-edit mode: approving straight from the review composes the
        # resolved hunks into the editor first (that text becomes the file).
        if self._file_edit is not None and self._review_active():
            self._finish_review(apply=True)
        self.result = 'accept'
        try:
            self.accept_note = self.note_edit.text().strip()
        except Exception:
            self.accept_note = ""
        self.reject_reason = ""      # only the pressed button's field is sent
        self.modified_code = self.code_edit.toPlainText().strip()
        cats = self._risky_categories()
        if cats and self.session_allow_cb.isChecked():
            self._session_allow_categories(cats)
        if cats and self.persist_allow_cb.isChecked():
            self._allow_categories_in_policy(cats)
        self.accept()

    def _session_allow_categories(self, cats):
        """Add these operation categories to the interpreter's ephemeral session
        allow-list (ToolManager.session_allowed_categories) so identical-category
        runs skip the prompt until restart. No-op if unreachable."""
        try:
            tm = getattr(self.ai_engine, "tool_manager", None)
            if tm is None:
                return
            if getattr(tm, "session_allowed_categories", None) is None:
                tm.session_allowed_categories = set()
            tm.session_allowed_categories.update(cats)
        except Exception:
            pass

    def _allow_categories_in_policy(self, cats=None):
        """Promote the given risk categories from 'ask' to 'allow' in the saved
        execution policy (persisted via the controller). Only ask->allow is
        promoted — an explicit 'deny' is never overridden. Live-refreshes an open
        Settings window. No-op without a controller or promotable categories."""
        controller = self._controller
        if controller is None or not hasattr(controller, "settings"):
            return
        try:
            from systema.security.code_guard import (PolicyEngine, POLICY_ALLOW,
                                                     POLICY_ASK)
            if cats is None:
                cats = self._risky_categories()
            if not cats:
                return
            rules = dict(PolicyEngine(controller.settings).rules)   # full current set
            changed = False
            for c in cats:
                if rules.get(c) == POLICY_ASK:      # never override allow/deny
                    rules[c] = POLICY_ALLOW
                    changed = True
            if not changed:
                return
            PolicyEngine.save(controller.settings, rules)           # writes security_exec_policy
            if hasattr(controller, "save_settings"):
                controller.save_settings()
            # Live-refresh an open Settings window's policy combos, if any.
            if hasattr(controller, "refresh_open_settings_security"):
                controller.refresh_open_settings_security()
        except Exception:
            pass

    # ── lifecycle ────────────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def _stop_worker(self):
        """Accept/Reject/close MUST never be blocked by agent state — every
        worker access is guarded, all failures swallowed."""
        self._closed = True   # late signals must never touch a dead dialog
        try:
            if self._worker_running():
                w = self._worker
                try:
                    w.message.disconnect(); w.proposal.disconnect()
                    w.finished_ok.disconnect(); w.failed.disconnect()
                    w.tokens.disconnect()
                except Exception:
                    pass
                # The blocking provider call can't be killed (same as the main
                # engine); deleteLater fires when it eventually finishes.
        except Exception:
            pass
        if self._typing_timer is not None:
            self._typing_timer.stop()

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    # ── styling helpers ──────────────────────────────────────────────────────
    def _h(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {self.p['text']}; font-size: 12px; font-weight: 600;"
                          " background: transparent;")
        return lbl

    def _btn(self, primary=False, kind="secondary"):
        p = self.p
        if primary:
            bg, fg, border = p["accent"], "#05070a", p["accent"]
        elif kind == "danger":
            bg, fg, border = "transparent", p["red"], p["red"]
        else:
            bg, fg, border = p["surface2"], p["text"], p["border"]
        return (f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
                f" border-radius: 6px; padding: 7px 14px; font-size: 11px; }}"
                f"QPushButton:hover {{ border: 1px solid {p['accent']}; }}"
                f"QPushButton:disabled {{ color: {p['muted']}; border-color: {p['border']}; }}")

    def _list(self):
        p = self.p
        return (f"QListWidget {{ background: {p['surface']}; border: 1px solid {p['border']};"
                f" border-radius: 8px; padding: 4px; font-family: Consolas, monospace;"
                f" font-size: 10px; color: {p['text']}; outline: none; }}"
                f"QListWidget::item {{ padding: 2px 4px; }}")

    def _edit_style(self, mono=False):
        p = self.p
        fam = "font-family: Consolas, monospace;" if mono else ""
        return (f"QTextEdit {{ background: {p['surface']}; border: 1px solid {p['border']};"
                f" border-radius: 8px; padding: 8px; {fam} font-size: 11px; color: {p['text']}; }}"
                f"QTextEdit:focus {{ border: 1px solid {p['accent']}; }}")

    # ── static entry point (unchanged contract) ──────────────────────────────
    @staticmethod
    def get_approval(code, execution_type, ai_engine, parent=None):
        """Show the dialog modally and return ``(approved, modified_code)``."""
        dialog = CodeApprovalDialog(code, execution_type, ai_engine, parent)
        dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()
        approved = dialog.result == 'accept'
        modified_code = dialog.modified_code if approved else code
        return approved, modified_code
