"""
systema/ui/dialogs/code_approval_dialog.py

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
  • "Don't ask again for identical code" remembers the approved hash
    (systema.security.code_guard.ApprovalMemory) so the same snippet skips the
    prompt next time.

Compatibility: the static ``get_approval(code, execution_type, ai_engine,
parent=None) -> (approved, modified_code)`` API and the ``code_edit`` / ``result``
/ ``modified_code`` / ``accept`` / ``close`` attributes are preserved — the
tool_manager and the Android bridge drive the dialog through them.
"""

from __future__ import annotations

import difflib

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QTextEdit, QListWidget, QListWidgetItem, QSplitter,
                             QWidget, QCheckBox, QFrame)

from systema.agents.code_agent import CodeAgent
from systema.security.code_guard import (scan_code, summarize_findings, redact_secrets,
                                         ApprovalMemory, SEV_DANGER, SEV_CAUTION,
                                         SEV_INFO)
from systema.ui import theme


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


class _CodeAgentWorker(QThread):
    """Runs one Code Reviewer instruction off the GUI thread."""
    message = pyqtSignal(str, str)      # (role, text)
    proposal = pyqtSignal(str, str)     # (replacement_code, why)
    tokens = pyqtSignal(str)            # token-meter summary
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, code, execution_type, ai_engine, task, meter):
        super().__init__()
        self._code = code
        self._etype = execution_type
        self._ai = ai_engine
        self._task = task
        self._meter = meter

    def run(self):
        try:
            agent = CodeAgent(
                self._code, self._etype, ai_engine=self._ai, meter=self._meter,
                on_message=lambda role, text: self.message.emit(role, text),
                on_proposal=lambda code, why: self.proposal.emit(code, why or ""))
            summary = agent.run(self._task)
            self.tokens.emit(self._meter.summary() if self._meter else "")
            self.finished_ok.emit(summary or "")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class CodeApprovalDialog(QDialog):
    """Review, edit, and approve one code snippet before it runs."""

    _SEV_COLOR_KEY = {SEV_DANGER: "red", SEV_CAUTION: "yellow", SEV_INFO: "muted"}

    def __init__(self, code, execution_type, ai_engine, parent=None):
        super().__init__(parent)
        self.code = code
        self.execution_type = execution_type
        self.ai_engine = ai_engine
        self.result = None                 # 'accept' | 'reject' | None
        self.modified_code = code

        self._worker = None
        self._typing_timer = None
        self._typing_dots = 0
        self._pending_proposal = None      # (code, why) awaiting Apply/Dismiss

        # Live theme palette (falls back to the default theme if no controller).
        controller = getattr(ai_engine, "controller", None)
        self._controller = controller
        try:
            self.p = theme.current_palette(controller)
        except Exception:
            self.p = theme.resolve_palette(theme.THEMES[theme.DEFAULT_THEME_KEY])

        from systema.common.token_meter import TokenMeter
        self._meter = TokenMeter("Code Reviewer")
        self._memory = ApprovalMemory()

        self._build()
        self._refresh_security()

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self):
        p = self.p
        self.setWindowTitle("Code Approval Required")
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

        title = QLabel("Code execution approval")
        title.setStyleSheet(f"color: {p['text']}; font-size: 15px; font-weight: 600;"
                            " background: transparent;")
        root.addWidget(title)

        where = ("work environment" if self.execution_type == "work_environment"
                 else "direct execution")
        desc = QLabel(f"The AI wants to run the code below ({where}). Review, edit if "
                      "needed, and approve. You can turn this prompt off in Settings.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        root.addWidget(desc)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")
        split.addWidget(self._build_code_side())
        split.addWidget(self._build_side_panel())
        split.setSizes([680, 420])
        root.addWidget(split, stretch=1)

        # footer: memory checkbox + Reject / Accept
        foot = QHBoxLayout()
        self.remember_cb = QCheckBox("Don't ask again for identical code")
        self.remember_cb.setStyleSheet(
            f"QCheckBox {{ color: {p['muted']}; font-size: 11px; background: transparent; }}"
            f"QCheckBox::indicator {{ width: 14px; height: 14px; }}")
        foot.addWidget(self.remember_cb)
        foot.addStretch()

        reject_btn = QPushButton("Reject")
        reject_btn.setStyleSheet(self._btn(kind="danger"))
        reject_btn.clicked.connect(self.on_reject)
        foot.addWidget(reject_btn)

        self.accept_btn = QPushButton("Accept and execute")
        self.accept_btn.setStyleSheet(self._btn(primary=True))
        self.accept_btn.clicked.connect(self.on_accept)
        self.accept_btn.setDefault(True)
        foot.addWidget(self.accept_btn)
        root.addLayout(foot)

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
        lay.addWidget(self.code_edit, stretch=1)

        # Proposed-edit panel (hidden until the agent proposes something).
        self.proposal_box = QFrame()
        self.proposal_box.setStyleSheet(
            f"QFrame {{ background: {p['surface']}; border: 1px solid {p['accent']};"
            " border-radius: 8px; }}")
        pbl = QVBoxLayout(self.proposal_box)
        pbl.setContentsMargins(10, 8, 10, 10); pbl.setSpacing(6)
        self.proposal_why = QLabel("")
        self.proposal_why.setWordWrap(True)
        self.proposal_why.setStyleSheet(
            f"color: {p['accent']}; font-size: 11px; font-weight: 600; background: transparent;")
        pbl.addWidget(self.proposal_why)
        self.proposal_diff = QTextEdit()
        self.proposal_diff.setReadOnly(True)
        self.proposal_diff.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.proposal_diff.setStyleSheet(self._edit_style(mono=True))
        self.proposal_diff.setMaximumHeight(190)
        pbl.addWidget(self.proposal_diff)
        prow = QHBoxLayout(); prow.setSpacing(6); prow.addStretch()
        dismiss_btn = QPushButton("Dismiss")
        dismiss_btn.setStyleSheet(self._btn())
        dismiss_btn.clicked.connect(self._dismiss_proposal)
        prow.addWidget(dismiss_btn)
        apply_btn = QPushButton("Apply edit")
        apply_btn.setStyleSheet(self._btn(primary=True))
        apply_btn.clicked.connect(self._apply_proposal)
        prow.addWidget(apply_btn)
        pbl.addLayout(prow)
        self.proposal_box.hide()
        lay.addWidget(self.proposal_box)
        return w

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

    def _refresh_security(self):
        p = self.p
        code = self.code_edit.toPlainText()
        findings = scan_code(code)
        self.risk_list.clear()
        for f in findings:
            snippet, _ = redact_secrets(f.snippet)
            it = QListWidgetItem(f"[{f.severity}] {f.category}  L{f.line}: {f.note}"
                                 + (f"   {snippet.strip()}" if snippet.strip() else ""))
            it.setForeground(QColor(p[self._SEV_COLOR_KEY.get(f.severity, "text")]))
            self.risk_list.addItem(it)

        known = self._memory.is_approved(code)
        summary = summarize_findings(findings)
        if known:
            self.scan_lbl.setText(f"{summary}. This exact code was approved before.")
        else:
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

    def _run_agent(self, task):
        if self._worker is not None and self._worker.isRunning():
            self._append_chat("system", "Please wait — the Code Reviewer is still "
                                        "responding to your last message.")
            return
        if self.ai_engine is None:
            self._append_chat("system", "No AI engine available.")
            return
        self._ai_busy(True)
        w = _CodeAgentWorker(self.code_edit.toPlainText(), self.execution_type,
                             self.ai_engine, task, self._meter)
        w.message.connect(self._append_chat)
        w.proposal.connect(self._on_proposal)
        w.tokens.connect(lambda s: self.token_lbl.setText(s or self.token_lbl.text()))
        w.finished_ok.connect(self._on_agent_done)
        w.failed.connect(self._on_agent_failed)
        self._worker = w
        w.start()

    def _on_agent_done(self, _summary):
        self._ai_busy(False)

    def _on_agent_failed(self, msg):
        self._ai_busy(False)
        self._append_chat("system", f"Error: {msg}")

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
        p = self.p
        who = {"you": p["accent"], "agent": p["text"], "system": p["muted"]}.get(role, p["text"])
        label = {"you": "You", "agent": "Code Reviewer", "system": "System"}.get(role, role)
        self.chat.append(
            f'<div style="margin:6px 0"><span style="color:{who};font-weight:600">'
            f'{label}:</span><div style="color:{p["text"]}">{_md(text)}</div></div>')
        cur = self.chat.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        self.chat.setTextCursor(cur)

    # ── proposed edit -> diff + one-click apply ──────────────────────────────
    def _on_proposal(self, new_code, why):
        self._pending_proposal = (new_code, why)
        self.proposal_why.setText("Proposed edit" + (f": {why}" if why else ""))
        self.proposal_diff.setHtml(self._render_diff(self.code_edit.toPlainText(), new_code))
        self.proposal_box.show()

    def _render_diff(self, old, new):
        p = self.p
        rows = []
        diff = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                    lineterm="", n=2)
        for ln in diff:
            if ln.startswith("+++") or ln.startswith("---"):
                continue
            if ln.startswith("@@"):
                fg, bg = p["muted"], "transparent"
            elif ln.startswith("+"):
                fg, bg = "#c6ecc6", "rgba(80,200,120,0.14)"
            elif ln.startswith("-"):
                fg, bg = "#f2b8b5", "rgba(230,90,90,0.14)"
            else:
                fg, bg = p["text"], "transparent"
            rows.append(f'<div style="background:{bg};color:{fg};white-space:pre;'
                        f'font-family:Consolas,monospace;padding:0 6px">'
                        f'{_esc(ln) or "&nbsp;"}</div>')
        if not rows:
            rows.append(f'<div style="color:{p["muted"]}">No textual difference.</div>')
        return "".join(rows)

    def _apply_proposal(self):
        if not self._pending_proposal:
            return
        new_code = self._pending_proposal[0]
        self.code_edit.setPlainText(new_code)      # triggers _refresh_security
        self._dismiss_proposal()
        self._append_chat("system", "Applied the proposed edit. Re-scanned; review "
                                    "before approving.")

    def _dismiss_proposal(self):
        self._pending_proposal = None
        self.proposal_box.hide()

    # ── accept / reject ──────────────────────────────────────────────────────
    def on_reject(self):
        self._stop_worker()
        self.result = 'reject'
        self.close()

    def on_accept(self):
        self._stop_worker()
        self.result = 'accept'
        self.modified_code = self.code_edit.toPlainText().strip()
        if self.remember_cb.isChecked():
            try:
                self._memory.remember(self.modified_code, persist=True,
                                      note=f"approved via dialog ({self.execution_type})")
            except Exception:
                pass
            # Also relax the execution policy: set the risk categories this code
            # uses to 'allow' so they show up (and are editable) in Settings >
            # Security. "Don't ask again" then applies both to this exact code
            # (hash memory) and to these operation types (policy).
            self._allow_categories_in_policy()
        self.accept()

    def _allow_categories_in_policy(self):
        """Flip every risk category present in the current code to 'allow' in the
        saved execution policy (persisted via the controller). No-op if there's no
        controller or no risky categories."""
        controller = self._controller
        if controller is None or not hasattr(controller, "settings"):
            return
        try:
            from systema.security.code_guard import (scan_code, PolicyEngine,
                                                     POLICY_ALLOW)
            cats = sorted({f.category for f in scan_code(self.code_edit.toPlainText())})
            if not cats:
                return
            rules = dict(PolicyEngine(controller.settings).rules)   # full current set
            for c in cats:
                if c in rules:
                    rules[c] = POLICY_ALLOW
            PolicyEngine.save(controller.settings, rules)           # writes security_exec_policy
            if hasattr(controller, "save_settings"):
                controller.save_settings()
        except Exception:
            pass

    # ── lifecycle ────────────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def _stop_worker(self):
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.message.disconnect(); w.proposal.disconnect()
                w.finished_ok.disconnect(); w.failed.disconnect()
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
