"""
systema/ui/dialogs/file_history_dialog.py

"File changes" viewer over the file-tools undo journal
(systema.execution.file_journal): every AI edit/write's pre-image, newest
first, with a unified diff against the CURRENT file and one-click Restore.
"""

from __future__ import annotations

import difflib

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QListWidget, QListWidgetItem,
                             QTextEdit, QMessageBox, QSplitter, QWidget)

from systema.execution import file_journal
from systema.ui import theme
from systema.common.logger import _make_logger

log = _make_logger("FileHistoryDialog")


class FileHistoryDialog(QDialog):
    """Journal browser: entries list (left) + diff of pre-image vs current
    file (right); Restore puts the pre-image back (or deletes a file that
    did not exist before the recorded write)."""

    def __init__(self, controller=None, parent=None):
        super().__init__(parent)
        self._controller = controller
        try:
            self.p = theme.current_palette(controller)
        except Exception:
            self.p = theme.resolve_palette(theme.THEMES[theme.DEFAULT_THEME_KEY])
        p = self.p
        self.setWindowTitle("File changes — undo journal")
        self.setMinimumSize(860, 480)
        self.resize(1020, 560)
        self.setStyleSheet(
            f"QDialog {{ background-color: {p['bg']}; }}"
            f"QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        title = QLabel("File changes")
        title.setStyleSheet(f"color: {p['text']}; font-size: 15px; font-weight: 600;"
                            " background: transparent;")
        root.addWidget(title)
        sub = QLabel("Every AI file edit/write records the file's previous state. "
                     "Restore puts a file back to how it was BEFORE that change.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        root.addWidget(sub)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setStyleSheet(f"QSplitter::handle {{ background: {p['border']}; width: 2px; }}")

        left = QWidget(); left.setStyleSheet("background: transparent;")
        llay = QVBoxLayout(left); llay.setContentsMargins(0, 0, 0, 0); llay.setSpacing(6)
        self.listw = QListWidget()
        self.listw.setStyleSheet(
            f"QListWidget {{ background: {p['surface']}; border: 1px solid {p['border']};"
            f" border-radius: 8px; padding: 4px; font-size: 11px; color: {p['text']};"
            " outline: none; }"
            f"QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background: {p['surface2']};"
            f" color: {p['text']}; }}")
        self.listw.currentItemChanged.connect(lambda *_: self._show_diff())
        llay.addWidget(self.listw, stretch=1)
        split.addWidget(left)

        right = QWidget(); right.setStyleSheet("background: transparent;")
        rlay = QVBoxLayout(right); rlay.setContentsMargins(8, 0, 0, 0); rlay.setSpacing(6)
        self.diff_lbl = QLabel("Select a change to see its diff against the current file.")
        self.diff_lbl.setWordWrap(True)
        self.diff_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 11px; background: transparent;")
        rlay.addWidget(self.diff_lbl)
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.diff_view.setStyleSheet(
            f"QTextEdit {{ background: {p['surface']}; border: 1px solid {p['border']};"
            " border-radius: 8px; padding: 8px; font-family: Consolas, monospace;"
            f" font-size: 10px; color: {p['text']}; }}")
        rlay.addWidget(self.diff_view, stretch=1)
        split.addWidget(right)
        split.setSizes([420, 600])
        root.addWidget(split, stretch=1)

        foot = QHBoxLayout()
        self.count_lbl = QLabel("")
        self.count_lbl.setStyleSheet(f"color: {p['muted']}; font-size: 10px; background: transparent;")
        foot.addWidget(self.count_lbl)
        foot.addStretch()
        self.restore_btn = QPushButton("Restore this file")
        self.restore_btn.setStyleSheet(self._btn(primary=True))
        self.restore_btn.clicked.connect(self._restore_selected)
        foot.addWidget(self.restore_btn)
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._btn())
        close_btn.clicked.connect(self.close)
        foot.addWidget(close_btn)
        root.addLayout(foot)

        self._reload()

    def _btn(self, primary=False):
        p = self.p
        bg, fg, border = ((p["accent"], "#05070a", p["accent"]) if primary
                          else (p["surface2"], p["text"], p["border"]))
        return (f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
                " border-radius: 6px; padding: 7px 14px; font-size: 11px; }"
                f"QPushButton:hover {{ border: 1px solid {p['accent']}; }}")

    def _reload(self):
        self.listw.clear()
        self._entries = file_journal.entries(limit=500)
        for e in self._entries:
            tag = "new file" if not e.get("existed") else e.get("tool", "")
            it = QListWidgetItem(f"{e.get('ts', '')}   {tag:<10}  {e.get('path', '')}")
            it.setData(Qt.ItemDataRole.UserRole, e.get("id"))
            self.listw.addItem(it)
        self.count_lbl.setText(f"{len(self._entries)} recorded change(s)")
        self.restore_btn.setEnabled(bool(self._entries))
        if self._entries:
            self.listw.setCurrentRow(0)

    def _selected(self):
        it = self.listw.currentItem()
        if it is None:
            return None
        jid = it.data(Qt.ItemDataRole.UserRole)
        return next((e for e in self._entries if e.get("id") == jid), None)

    def _show_diff(self):
        e = self._selected()
        if e is None:
            self.diff_view.setPlainText("")
            return
        from systema.execution import file_tools as ft
        pre = file_journal.pre_image_text(e["id"]) if e.get("existed") else ""
        cur = ft.read_current(e.get("path", ""))
        if pre == cur:
            self.diff_view.setPlainText("(the file currently matches this pre-image — "
                                        "nothing to restore)")
            self.diff_lbl.setText(f"{e.get('path', '')} — unchanged since this snapshot")
            return
        diff = difflib.unified_diff(cur.splitlines(), pre.splitlines(),
                                    fromfile="current", tofile="after restore",
                                    lineterm="")
        self.diff_view.setPlainText("\n".join(diff) or "(binary or empty diff)")
        self.diff_lbl.setText(f"{e.get('path', '')} — what Restore would change "
                              f"(current -> pre-image)")

    def _restore_selected(self):
        e = self._selected()
        if e is None:
            return
        what = ("delete the file (it did not exist before this change)"
                if not e.get("existed") else "put the file back to its recorded state")
        ret = QMessageBox.question(
            self, "Restore file",
            f"{e.get('path', '')}\n\nThis will {what}. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok, msg = file_journal.restore(e["id"])
        QMessageBox.information(self, "Restore", msg)
        if ok:
            self._show_diff()
