"""
systema/ui/dialogs/update_manage_dialog.py

Line/hunk-level update review — the safe way to apply an update to PROTECTED files
(provider accounts/keys) without losing data.

Left  : files under review + their hunks (each hunk = a changed block).
Right : the selected hunk — Keep mine / Take update / Edit, or Split to lines for
        per-line control.

On Save, each file's chosen resolution is assembled and handed back through
``on_save(dict[path -> text])`` for the caller to persist (with a backup).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                             QListWidget, QListWidgetItem, QTextEdit, QPushButton,
                             QSplitter, QWidget, QMessageBox)

from systema.updater.hunks import (ReviewSession,
                                    _DEFAULT_DECISION as _DEFAULT_HUNK_DECISION)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
        self._cur_file = None
        self._cur_hunk = None
        self._split_mode = False

        self.setWindowTitle("Manage Update — line by line")
        self.resize(1080, 720)
        self.setStyleSheet(f"QDialog {{ background-color: {palette['bg']}; }}")
        self._build()
        self._reload_files()

        # Center on the chat window / primary screen (never the floating widget)
        from PyQt6.QtCore import QTimer
        from systema.ui.dialogs.dialog_utils import center_on_primary
        QTimer.singleShot(0, lambda: center_on_primary(self))

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

        left_pane = self._build_left()
        mid_pane = self._build_middle()
        left_pane.setMinimumWidth(230)     # files/hunks never squeezed to a sliver
        mid_pane.setMinimumWidth(380)      # nor the hunk detail/preview
        split.addWidget(left_pane)
        split.addWidget(mid_pane)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)       # the detail/preview gets the extra width
        split.setSizes([300, 760])
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
