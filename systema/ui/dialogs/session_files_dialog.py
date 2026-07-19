"""
systema/ui/dialogs/session_files_dialog.py
Files touched this session — reversible state stepper (chat ⋯ menu).

Lists every file the CURRENT session edited/wrote (from the file journal) and
lets the user walk each file's state: ◀ steps back through the journaled
pre-images, ▶ un-does the reversal (back toward the current state). The
"current" bytes are stashed ON DISK on the first step back (file_journal
revert-state), so cursors survive dialog close/reopen AND app restart; a file
parked in a stepped-back state shows a "reverted -N" badge.
"""
from pathlib import Path

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QScrollArea, QWidget)
from PyQt6.QtCore import Qt

from systema.execution import file_journal
from systema.common.logger import _make_logger

log = _make_logger("SessionFilesDialog")


class SessionFilesDialog(QDialog):
    def __init__(self, chat_window):
        super().__init__(chat_window)
        self._chat = chat_window
        t = chat_window._t()
        self.setWindowTitle("Files touched this session")
        self.setMinimumSize(560, 320)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['surface']}; border: 1px solid {t['border']}; }}
            QLabel {{ color: #E8EAED; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QLabel("<b>± Files touched this session</b>"
                      "<br><span style='color:#8B949E;font-size:11px;'>"
                      "◀ steps a file back through its edit history · ▶ brings it forward again"
                      "</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)

        # ── Journal entries for THIS session, grouped per path (newest first) ─
        try:
            sid = chat_window.controller.ai.tool_manager._session_id()
        except Exception:
            sid = ""
        by_path = {}
        for e in file_journal.entries():
            if e.get("session_id") != sid or not sid:
                continue
            if e.get("tool") not in ("edit_file", "write_file"):
                continue
            by_path.setdefault(e.get("path", ""), []).append(e)  # newest → oldest

        # Cursors are PERSISTENT (file_journal revert-state): path -> steps back
        # (0 = current). The 'current' bytes stash lives on disk too, so a file
        # left stepped-back stays that way across reopen and app restart.
        self._sid = sid
        self._cursor = {p: int(e.get("cursor", 0))
                        for p, e in file_journal.revert_state(sid).items()
                        if int(e.get("cursor", 0)) > 0}

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        rows = QVBoxLayout(inner)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)

        if not by_path:
            empty = QLabel("No files were edited or written in this session.")
            empty.setStyleSheet("color: #8B949E; font-size: 12px;")
            rows.addWidget(empty)

        for path, ents in by_path.items():
            rows.addWidget(self._build_row(path, ents, t))
        rows.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {t['border']};
                border-radius: 6px; padding: 7px 18px; color: #8B949E; }}
            QPushButton:hover {{ color: #E6EDF3; border-color: {t['accent']}; }}
        """)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        lay.addLayout(close_row)

    # ── one file row ─────────────────────────────────────────────────────────

    def _build_row(self, path, ents, t):
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{ background: {t['elevated']}; border: 1px solid {t['border']};
                      border-radius: 8px; }}
        """)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 8, 10, 8)
        rl.setSpacing(8)

        name = Path(path).name
        info = QLabel(
            f"<span style='font-size:12px;font-weight:600;'>{name}</span>&nbsp;&nbsp;"
            f"<span style='font-size:10px;color:#8B949E;'>{path}</span>"
            f"<br><span style='font-size:10px;color:#5F6368;'>{len(ents)} "
            f"op{'s' if len(ents) != 1 else ''} this session · newest {ents[0].get('ts','')}</span>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet("background: transparent; border: none;")
        info.setWordWrap(True)
        rl.addWidget(info, stretch=1)

        state_lbl = QLabel("current")
        state_lbl.setStyleSheet(
            f"background: transparent; border: none; color: {t['accent']}; "
            f"font-size: 10px; font-weight: 600;")
        state_lbl.setFixedWidth(92)
        state_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        _btn_css = f"""
            QPushButton {{ background: transparent; border: 1px solid {t['border']};
                border-radius: 5px; color: #C9D1D9; font-size: 12px; padding: 2px 10px; }}
            QPushButton:hover {{ border-color: {t['accent']}; color: {t['accent']}; }}
            QPushButton:disabled {{ color: #484F58; border-color: #22272E; }}
        """
        back_btn = QPushButton("◀")
        back_btn.setStyleSheet(_btn_css)
        back_btn.setToolTip("Step this file BACK one journaled state")
        fwd_btn = QPushButton("▶")
        fwd_btn.setStyleSheet(_btn_css)
        fwd_btn.setToolTip("Undo the reversal — step forward again")
        fwd_btn.setEnabled(False)

        # A pruned journal can leave a saved cursor deeper than what remains.
        if self._cursor.get(path, 0) > len(ents):
            self._cursor[path] = len(ents)

        def _refresh():
            p = self._cursor.get(path, 0)
            if p == 0:
                state_lbl.setText("current")
                state_lbl.setStyleSheet(
                    f"background: transparent; border: none; color: {t['accent']}; "
                    f"font-size: 10px; font-weight: 600;")
            else:
                # Actively-reverted badge — amber so a parked file stands out.
                state_lbl.setText(f"reverted -{p}")
                state_lbl.setStyleSheet(
                    "background: transparent; border: none; color: #E3B341; "
                    "font-size: 10px; font-weight: 700;")
            back_btn.setEnabled(p < len(ents))
            fwd_btn.setEnabled(p > 0)

        def _step_back(checked=False):
            p = self._cursor.get(path, 0)
            if p >= len(ents):
                return
            if p == 0:
                # Stash the live bytes ON DISK so ▶ can come all the way back
                # even after a dialog close or app restart.
                if not file_journal.stash_current(self._sid, path):
                    state_lbl.setText("failed")
                    log.error(f"stash failed for {path}")
                    return
            ok, msg = file_journal.restore(ents[p]["id"])
            if ok:
                self._cursor[path] = p + 1
                file_journal.set_revert_cursor(self._sid, path, p + 1)
            else:
                state_lbl.setText("failed")
                log.error(f"restore failed: {msg}")
            _refresh()

        def _step_fwd(checked=False):
            p = self._cursor.get(path, 0)
            if p <= 0:
                return
            if p == 1:
                found, data = file_journal.stash_read(self._sid, path)
                if not found:
                    state_lbl.setText("failed")
                    log.error(f"redo stash missing for {path}")
                    return
                try:
                    f = Path(path)
                    if data is None:
                        if f.is_file():
                            f.unlink()   # current state was "did not exist"
                    else:
                        f.parent.mkdir(parents=True, exist_ok=True)
                        f.write_bytes(data)
                except Exception as e:
                    log.error(f"redo write failed for {path}: {e}")
                    state_lbl.setText("failed")
                    return
                self._cursor[path] = 0
                file_journal.set_revert_cursor(self._sid, path, 0)
            else:
                ok, msg = file_journal.restore(ents[p - 2]["id"])
                if not ok:
                    state_lbl.setText("failed")
                    log.error(f"redo restore failed: {msg}")
                    return
                self._cursor[path] = p - 1
                file_journal.set_revert_cursor(self._sid, path, p - 1)
            _refresh()

        back_btn.clicked.connect(_step_back)
        fwd_btn.clicked.connect(_step_fwd)
        rl.addWidget(back_btn)
        rl.addWidget(state_lbl)
        rl.addWidget(fwd_btn)
        _refresh()
        return row
