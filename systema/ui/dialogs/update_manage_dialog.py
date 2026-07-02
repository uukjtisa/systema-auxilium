"""
systema/ui/dialogs/update_manage_dialog.py

Line/hunk-level update review with an integrated AI sub-agent — the safe way to
apply an update to PROTECTED files (provider accounts/keys) without losing data.

Left  : files under review + their hunks (each hunk = a changed block).
Middle: the selected hunk — Keep mine / Take update / Edit, or Split to lines for
        per-line control.
Right : the Update Manager sub-agent (same provider, own prompt/tools/interpreter)
        — Explain, Recommend, or Auto-approve safe + flag risky. Follows the same
        pattern as code_approval_dialog's AI panel.

On Save, each file's chosen resolution is assembled and handed back through
``on_save(dict[path -> text])`` for the caller to persist (with a backup).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QListWidget, QListWidgetItem, QTextEdit, QPushButton,
                             QSplitter, QWidget, QMessageBox)

from systema.agents.update_agent import ReviewSession, UpdateAgent
from systema.agents.update_hunks import _DEFAULT_DECISION as _DEFAULT_HUNK_DECISION


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _AgentWorker(QThread):
    """Runs one UpdateAgent instruction off the GUI thread."""
    message = pyqtSignal(str, str)   # (role, text)
    state_changed = pyqtSignal()
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, session: ReviewSession, ai_engine, task: str):
        super().__init__()
        self._session = session
        self._ai = ai_engine
        self._task = task

    def run(self):
        try:
            agent = UpdateAgent(
                self._session, ai_engine=self._ai,
                on_message=lambda role, text: self.message.emit(role, text),
                on_state=lambda: self.state_changed.emit())
            summary = agent.run(self._task)
            self.finished_ok.emit(summary or "")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")


class UpdateManageDialog(QDialog):
    _DECISION_LABEL = {"local": "Keep mine", "update": "Take update",
                       "edited": "Edited", "line": "Per-line"}

    def __init__(self, controller, session: ReviewSession, palette: dict,
                 on_save, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.session = session
        self.p = palette
        self._on_save = on_save
        self._worker = None
        self._typing_timer = None
        self._cur_file = None
        self._cur_hunk = None
        self._split_mode = False

        self.setWindowTitle("Manage Update — line by line")
        self.resize(1080, 720)
        self.setStyleSheet(f"QDialog {{ background-color: {palette['bg']}; }}")
        self._build()
        self._reload_files()

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        p = self.p
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel("Resolve the update one hunk — or one line — at a time. "
                       "Protected files start on your side; nothing is written until you Save.")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        root.addWidget(title)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")

        split.addWidget(self._build_left())
        split.addWidget(self._build_middle())
        split.addWidget(self._build_ai())
        split.setSizes([300, 460, 320])
        root.addWidget(split, stretch=1)

        foot = QHBoxLayout()
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        foot.addWidget(self.status_lbl)
        foot.addStretch()
        save_btn = QPushButton("Save resolved files")
        save_btn.setStyleSheet(self._btn(primary=True))
        save_btn.clicked.connect(self._on_save_clicked)
        foot.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(self._btn())
        cancel_btn.clicked.connect(self.reject)
        foot.addWidget(cancel_btn)
        root.addLayout(foot)

    def _build_left(self) -> QWidget:
        p = self.p
        w = QWidget(); w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(self._h("Files"))
        self.file_combo = QComboBox()
        self.file_combo.setStyleSheet(self._combo())
        self.file_combo.currentTextChanged.connect(self._on_file_changed)
        lay.addWidget(self.file_combo)
        lay.addWidget(self._h("Hunks"))
        self.hunk_list = QListWidget()
        self.hunk_list.setStyleSheet(self._list())
        self.hunk_list.currentRowChanged.connect(self._on_hunk_row)
        lay.addWidget(self.hunk_list, stretch=1)
        return w

    def _build_middle(self) -> QWidget:
        p = self.p
        w = QWidget(); w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)

        bar = QHBoxLayout(); bar.setSpacing(6)
        self.keep_btn = QPushButton("Keep mine")
        self.keep_btn.setStyleSheet(self._btn())
        self.keep_btn.clicked.connect(lambda: self._set_decision("local"))
        self.take_btn = QPushButton("Take update")
        self.take_btn.setStyleSheet(self._btn())
        self.take_btn.clicked.connect(lambda: self._set_decision("update"))
        self.split_btn = QPushButton("Split to lines")
        self.split_btn.setStyleSheet(self._btn())
        self.split_btn.setCheckable(True)
        self.split_btn.toggled.connect(self._on_split_toggled)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setStyleSheet(self._btn())
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self._on_edit_toggled)
        for b in (self.keep_btn, self.take_btn, self.split_btn, self.edit_btn):
            bar.addWidget(b)
        bar.addStretch()
        lay.addLayout(bar)

        # Rich read-only view of the hunk (local vs update, coloured).
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.detail.setStyleSheet(self._edit_style())
        lay.addWidget(self.detail, stretch=1)

        # Per-line checkboxes (shown in split mode).
        self.line_list = QListWidget()
        self.line_list.setStyleSheet(self._list())
        self.line_list.itemChanged.connect(self._on_line_toggled)
        self.line_list.hide()
        lay.addWidget(self.line_list, stretch=1)

        # Free-form editor (shown in edit mode).
        self.editor = QTextEdit()
        self.editor.setStyleSheet(self._edit_style())
        self.editor.hide()
        self.editor.textChanged.connect(self._on_editor_changed)
        lay.addWidget(self.editor, stretch=1)
        return w

    def _build_ai(self) -> QWidget:
        p = self.p
        w = QWidget(); w.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(w); lay.setContentsMargins(12, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(self._h("Update Manager (AI)"))

        row = QHBoxLayout(); row.setSpacing(6)
        self.explain_btn = QPushButton("Explain")
        self.explain_btn.setStyleSheet(self._btn())
        self.explain_btn.clicked.connect(self._ai_explain)
        self.recommend_btn = QPushButton("Recommend")
        self.recommend_btn.setStyleSheet(self._btn())
        self.recommend_btn.clicked.connect(self._ai_recommend)
        for b in (self.explain_btn, self.recommend_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self.auto_btn = QPushButton("Auto-approve safe + flag risky")
        self.auto_btn.setStyleSheet(self._btn(primary=True))
        self.auto_btn.clicked.connect(self._ai_auto)
        lay.addWidget(self.auto_btn)

        self.chat = QTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setStyleSheet(self._edit_style())
        lay.addWidget(self.chat, stretch=1)

        # "Responding" indicator — shown while the sub-agent is working so it's
        # obvious a reply is coming and input is locked.
        self.typing_lbl = QLabel("")
        self.typing_lbl.setStyleSheet(
            f"color: {p['accent']}; font-size: 11px; font-style: italic;"
            " background: transparent;")
        self.typing_lbl.setVisible(False)
        lay.addWidget(self.typing_lbl)

        ask = QHBoxLayout(); ask.setSpacing(6)
        self.ask_field = QTextEdit()
        self.ask_field.setPlaceholderText("Ask the Update Manager…")
        self.ask_field.setMaximumHeight(52)
        self.ask_field.setStyleSheet(self._edit_style())
        ask.addWidget(self.ask_field, stretch=1)
        self.send_btn = QPushButton("Send")
        self.send_btn.setStyleSheet(self._btn())
        self.send_btn.clicked.connect(self._ai_ask)
        ask.addWidget(self.send_btn)
        lay.addLayout(ask)
        return w

    # ── data population ────────────────────────────────────────────────────────
    def _reload_files(self):
        self.file_combo.blockSignals(True)
        self.file_combo.clear()
        for path, fr in self.session.files.items():
            tag = "  [PROTECTED]" if fr.sensitive else ""
            self.file_combo.addItem(path + tag, path)
        self.file_combo.blockSignals(False)
        if self.file_combo.count():
            self._on_file_changed(self.file_combo.currentText())

    def _current_path(self):
        return self.file_combo.currentData()

    def _on_file_changed(self, _text):
        self._cur_file = self._current_path()
        self._reload_hunks()

    def _reload_hunks(self):
        self.hunk_list.blockSignals(True)
        self.hunk_list.clear()
        fr = self.session.files.get(self._cur_file)
        if fr:
            for h in fr.hunks:
                sec = "  •secrets" if h.touches_secrets else ""
                dec = self._DECISION_LABEL.get(h.decision, h.decision)
                it = QListWidgetItem(f"hunk {h.index} [{h.kind}] -{len(h.local_lines)}/"
                                     f"+{len(h.update_lines)}{sec}   → {dec}")
                if h.touches_secrets:
                    from PyQt6.QtGui import QColor
                    it.setForeground(QColor("#e0a24e"))
                self.hunk_list.addItem(it)
        self.hunk_list.blockSignals(False)
        if self.hunk_list.count():
            self.hunk_list.setCurrentRow(0)
        else:
            self._cur_hunk = None
            self.detail.setHtml("")

    def _on_hunk_row(self, row):
        fr = self.session.files.get(self._cur_file)
        if not fr or row < 0 or row >= len(fr.hunks):
            self._cur_hunk = None
            return
        self._cur_hunk = fr.hunks[row]
        # Reset transient modes when switching hunks.
        self.split_btn.blockSignals(True); self.split_btn.setChecked(self._cur_hunk.decision == "line"); self.split_btn.blockSignals(False)
        self.edit_btn.blockSignals(True); self.edit_btn.setChecked(self._cur_hunk.decision == "edited"); self.edit_btn.blockSignals(False)
        self._split_mode = self._cur_hunk.decision == "line"
        self._render_hunk()

    def _render_hunk(self):
        h = self._cur_hunk
        if h is None:
            return
        edit = h.decision == "edited"
        split = h.decision == "line"
        self.editor.setVisible(edit)
        self.line_list.setVisible(split)
        self.detail.setVisible(not edit and not split)

        if edit:
            self.editor.blockSignals(True)
            self.editor.setPlainText(h.edited_text if h.edited_text is not None
                                     else "".join(h.local_lines))
            self.editor.blockSignals(False)
        elif split:
            self._render_line_list(h)
        else:
            self._render_detail(h)

    def _render_detail(self, h):
        p = self.p
        rows = []
        def block(title, text, fg, bg):
            rows.append(f'<div style="color:{p["muted"]};font-size:10px;'
                        f'padding:4px 0 2px">{title}</div>')
            for ln in (text.splitlines() or [""]):
                rows.append(f'<div style="background:{bg};color:{fg};white-space:pre;'
                            f'font-family:Consolas,monospace;padding:1px 8px">{_esc(ln) or "&nbsp;"}</div>')
        block("YOURS (on disk)", "".join(h.local_lines),
              "#bfe0ff", "rgba(90,160,230,0.14)")
        block("UPDATE (upstream)", "".join(h.update_lines),
              "#c6ecc6", "rgba(80,200,120,0.14)")
        self.detail.setHtml("".join(rows))

    def _render_line_list(self, h):
        self.line_list.blockSignals(True)
        self.line_list.clear()
        for i, lc in enumerate(h.split()):
            if lc.local is not None and lc.update is None:
                text = "  keep yours: " + lc.local.rstrip("\n")
                keep = lc.take == "local"
            elif lc.update is not None and lc.local is None:
                text = "  take update: " + lc.update.rstrip("\n")
                keep = lc.take == "update"
            else:  # identical on both sides
                text = "  = " + (lc.local or "").rstrip("\n")
                keep = True
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.line_list.addItem(it)
        self.line_list.blockSignals(False)

    # ── decisions ──────────────────────────────────────────────────────────────
    def _set_decision(self, choice):
        if self._cur_hunk is None:
            return
        self.edit_btn.blockSignals(True); self.edit_btn.setChecked(False); self.edit_btn.blockSignals(False)
        self.split_btn.blockSignals(True); self.split_btn.setChecked(False); self.split_btn.blockSignals(False)
        self._cur_hunk.decision = choice
        self._render_hunk()
        self._reload_hunks_keep_row()

    def _on_split_toggled(self, on):
        if self._cur_hunk is None:
            return
        if on:
            self.edit_btn.blockSignals(True); self.edit_btn.setChecked(False); self.edit_btn.blockSignals(False)
            self._cur_hunk.split()
            self._cur_hunk.decision = "line"
        else:
            if self._cur_hunk.decision == "line":
                self._cur_hunk.decision = "local"
        self._render_hunk()
        self._reload_hunks_keep_row()

    def _on_edit_toggled(self, on):
        if self._cur_hunk is None:
            return
        if on:
            self.split_btn.blockSignals(True); self.split_btn.setChecked(False); self.split_btn.blockSignals(False)
            if self._cur_hunk.edited_text is None:
                self._cur_hunk.edited_text = "".join(self._cur_hunk.local_lines)
            self._cur_hunk.decision = "edited"
        else:
            if self._cur_hunk.decision == "edited":
                self._cur_hunk.decision = "local"
        self._render_hunk()
        self._reload_hunks_keep_row()

    def _on_editor_changed(self):
        if self._cur_hunk is not None and self._cur_hunk.decision == "edited":
            self._cur_hunk.edited_text = self.editor.toPlainText()

    def _on_line_toggled(self, item):
        if self._cur_hunk is None or self._cur_hunk.lines is None:
            return
        i = item.data(Qt.ItemDataRole.UserRole)
        lc = self._cur_hunk.lines[i]
        keep = item.checkState() == Qt.CheckState.Checked
        if lc.local is not None and lc.update is None:
            lc.take = "local" if keep else "update"     # update side is empty -> drops
        elif lc.update is not None and lc.local is None:
            lc.take = "update" if keep else "local"
        # identical lines: leave as-is (always kept)

    def _reload_hunks_keep_row(self):
        row = self.hunk_list.currentRow()
        self._reload_hunks()
        if 0 <= row < self.hunk_list.count():
            self.hunk_list.setCurrentRow(row)

    # ── AI panel ────────────────────────────────────────────────────────────────
    def _ai_busy(self, busy):
        # Lock every input that would start a second run, so you can't fire the
        # agent again while it's mid-reply.
        for b in (self.explain_btn, self.recommend_btn, self.auto_btn, self.send_btn):
            b.setEnabled(not busy)
        self.ask_field.setReadOnly(busy)
        self.typing_lbl.setVisible(busy)
        self.status_lbl.setText("Update Manager is responding…" if busy else "")
        if busy:
            self._typing_dots = 0
            if getattr(self, "_typing_timer", None) is None:
                from PyQt6.QtCore import QTimer
                self._typing_timer = QTimer(self)
                self._typing_timer.timeout.connect(self._tick_typing)
            self._tick_typing()
            self._typing_timer.start(450)
        elif getattr(self, "_typing_timer", None) is not None:
            self._typing_timer.stop()

    def _tick_typing(self):
        self._typing_dots = (getattr(self, "_typing_dots", 0) + 1) % 4
        self.typing_lbl.setText("Update Manager is responding, please wait"
                                + "." * self._typing_dots)

    def _run_agent(self, task):
        if self._worker is not None and self._worker.isRunning():
            self._append_chat("system", "Please wait — the Update Manager is still "
                                        "responding to your last message.")
            return
        ai = getattr(self.controller, "ai", None)
        if ai is None:
            self._append_chat("system", "No AI engine available.")
            return
        self._ai_busy(True)
        w = _AgentWorker(self.session, ai, task)
        w.message.connect(lambda role, text: self._append_chat(role, text))
        w.state_changed.connect(self._reload_hunks_keep_row)
        w.finished_ok.connect(self._on_agent_done)
        w.failed.connect(self._on_agent_failed)
        self._worker = w
        w.start()

    def _on_agent_done(self, summary):
        self._ai_busy(False)
        self._reload_hunks_keep_row()
        # Make decision changes unmistakable even when they landed on other files.
        n = sum(1 for fr in self.session.files.values()
                for h in fr.hunks if h.decision != _DEFAULT_HUNK_DECISION.get(h.kind))
        self._append_chat("system", f"Decisions updated — {n} hunk(s) now differ from "
                                    "the default. Check the hunk list (→ shows each choice).")

    def _on_agent_failed(self, msg):
        self._ai_busy(False)
        self._append_chat("system", f"Error: {msg}")

    def _ai_explain(self):
        path = self._current_path()
        hi = self._cur_hunk.index if self._cur_hunk else None
        where = f"file '{path}'" + (f", focus on hunk {hi}" if hi is not None else "")
        self._append_chat("you", f"Explain the changes in {where}.")
        self._run_agent(f"Explain, in plain language, what this update changes in "
                        f"{where}, and whether it is safe to take or should be kept. "
                        "Use 'say' for each explanation. Do not change any decisions.")

    def _ai_recommend(self):
        self._append_chat("you", "Recommend what to do for each hunk.")
        self._run_agent("Review every file. Use 'recommend' and then 'say' a short "
                        "rationale for the protected files. Do not change decisions.")

    def _ai_auto(self):
        self._append_chat("you", "Auto-approve the clearly-safe hunks and flag the risky ones.")
        self._run_agent("Use 'auto_approve_safe' across all files, then 'say' which "
                        "hunks you approved and which you left for me because they "
                        "touch my accounts/keys, then 'finish'.")

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
        label = {"you": "You", "agent": "Update Manager", "system": "System"}.get(role, role)
        self.chat.append(
            f'<div style="margin:4px 0"><span style="color:{who};font-weight:600">'
            f'{label}:</span> <span style="color:{p["text"]}">{_esc(text)}</span></div>')

    # ── save ────────────────────────────────────────────────────────────────────
    def _on_save_clicked(self):
        resolved = {path: fr.assembled() for path, fr in self.session.files.items()}
        n = len(resolved)
        box = QMessageBox(self)
        box.setWindowTitle("Save resolved files?")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Write your resolved version of {n} file(s) to disk?\n\n"
                    "A backup of each is saved first. Restart afterwards.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            self._on_save(resolved)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.accept()

    def closeEvent(self, event):
        # If the agent is still running, detach its signals so late emits don't
        # touch a destroyed dialog, and let the thread finish on its own.
        w = self._worker
        if w is not None and w.isRunning():
            try:
                w.message.disconnect()
                w.state_changed.disconnect()
                w.finished_ok.disconnect()
                w.failed.disconnect()
            except Exception:
                pass
        if getattr(self, "_typing_timer", None) is not None:
            self._typing_timer.stop()
        super().closeEvent(event)

    # ── styling helpers ──────────────────────────────────────────────────────────
    def _h(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {self.p['text']}; font-size: 12px; font-weight: 600;"
                          " background: transparent;")
        return lbl

    def _btn(self, primary=False):
        p = self.p
        bg = p["accent"] if primary else p["surface2"]
        fg = "#05070a" if primary else p["text"]
        return (f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {p['border']};"
                f" border-radius: 6px; padding: 6px 11px; font-size: 11px; }}"
                f"QPushButton:hover {{ border: 1px solid {p['accent']}; }}"
                f"QPushButton:checked {{ background: {p['accent']}; color: #05070a; }}"
                f"QPushButton:disabled {{ color: {p['muted']}; }}")

    def _combo(self):
        p = self.p
        return (f"QComboBox {{ background: {p['surface2']}; border: 1px solid {p['border']};"
                f" border-radius: 6px; padding: 6px 10px; font-size: 11px; color: {p['text']}; }}"
                f"QComboBox QAbstractItemView {{ background: {p['surface2']}; color: {p['text']};"
                f" selection-background-color: {p['accent']}; selection-color: #05070a; }}")

    def _list(self):
        p = self.p
        return (f"QListWidget {{ background: {p['surface']}; border: 1px solid {p['border']};"
                f" border-radius: 8px; padding: 4px; font-family: Consolas, monospace;"
                f" font-size: 11px; color: {p['text']}; outline: none; }}"
                f"QListWidget::item {{ padding: 3px 4px; border-radius: 4px; }}"
                f"QListWidget::item:selected {{ background: {p['surface2']}; color: {p['text']}; }}")

    def _edit_style(self):
        p = self.p
        return (f"QTextEdit {{ background: {p['surface']}; border: 1px solid {p['border']};"
                f" border-radius: 8px; padding: 8px; font-family: Consolas, monospace;"
                f" font-size: 11px; color: {p['text']}; }}")
