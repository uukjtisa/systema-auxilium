"""
systema/ui/windows/logs_window.py — the session's log files, made findable.

A session file records WHAT was rendered; a log file records WHAT HAPPENED.
Nothing linked them, so working out why a card came out wrong meant eyeballing
timestamps across several files by hand.

Every history entry now carries a `_run` stamp (`systema/common/run_context.py`)
naming the log file it was written during. This window reads those stamps back:
the top list is the files THIS session actually spans — usually more than one,
because the app restarts — with the rest of `data/logs` underneath.

The preview TAILS the selected file. It never reads a whole log into memory:
these run to megabytes and the interesting part is almost always the end.
"""
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from systema import APP_ROOT
from systema.common import run_context
from systema.common.logger import _make_logger
from systema.ui.base_window import BaseWindow

log = _make_logger("LogsWindow")

_TAIL_BYTES = 96 * 1024


def _logs_dir() -> Path:
    return APP_ROOT / "data" / "logs"


def reveal_in_folder(path: Path) -> bool:
    """Open the OS file manager with `path` selected. Cross-OS per the house
    rule — every platform gets its own call, none of them assume Windows."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
        return True
    except Exception:
        log.warning(f"[reveal_in_folder] failed for {path}", exc_info=True)
        return False


def open_in_editor(path: Path) -> bool:
    """Hand the file to whatever the OS considers its default text handler."""
    try:
        if sys.platform == "win32":
            import os
            os.startfile(str(path))            # noqa: S606 - intended shell open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        log.warning(f"[open_in_editor] failed for {path}", exc_info=True)
        return False


class LogsWindow(BaseWindow):
    """Log browser for the active session. Opened by `/logs` and the Debug window."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._selected: Path | None = None

        pal = self._palette()
        body = self.build_shell(pal, "Session Logs", min_size=(880, 560),
                                buttons=("minimize", "close"))

        self._subtitle = QLabel("")
        self._subtitle.setStyleSheet(f"color:{pal['muted']}; font-size:11px;")
        body.addWidget(self._subtitle)

        split = QHBoxLayout()
        split.setSpacing(12)
        body.addLayout(split, stretch=1)

        left = QVBoxLayout()
        left.setSpacing(6)
        self._list = QListWidget()
        self._list.setStyleSheet(
            f"QListWidget {{ background:{pal['surface']}; border:1px solid {pal['border']};"
            f" border-radius:8px; color:{pal['text']}; font-size:11px; padding:4px; }}"
            f"QListWidget::item {{ padding:6px 8px; border-radius:5px; }}"
            f"QListWidget::item:selected {{ background:{pal['accent']}; color:#000; }}")
        self._list.currentItemChanged.connect(self._on_pick)
        left.addWidget(self._list, stretch=1)
        _leftw = QWidget()
        _leftw.setLayout(left)
        _leftw.setFixedWidth(330)
        split.addWidget(_leftw)

        right = QVBoxLayout()
        right.setSpacing(6)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setStyleSheet(
            f"QPlainTextEdit {{ background:{pal['surface']}; border:1px solid {pal['border']};"
            f" border-radius:8px; color:{pal['text']};"
            f" font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size:11px;"
            f" padding:8px; }}")
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, stretch=1)
        _rightw = QWidget()
        _rightw.setLayout(right)
        split.addWidget(_rightw, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for label, slot in (("Open in editor", self._open_selected),
                            ("Reveal in folder", self._reveal_selected),
                            ("Copy path", self._copy_selected),
                            ("Refresh", self.reload)):
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"QPushButton {{ background:{pal['surface']}; border:1px solid {pal['border']};"
                f" border-radius:6px; padding:7px 14px; font-size:11px; color:{pal['text']}; }}"
                f"QPushButton:hover {{ border-color:{pal['accent']}; color:{pal['accent']}; }}")
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        actions.addStretch(1)
        body.addLayout(actions)

        self.reload()

    # ── data ────────────────────────────────────────────────────────────────

    def _palette(self):
        try:
            from systema.ui import theme as _theme
            return _theme.current_palette(self.controller)
        except Exception:
            # A themed window must still open if theming is unavailable.
            return {'bg': '#0D1117', 'surface': '#161B22', 'border': '#2A313C',
                    'text': '#E6EDF3', 'muted': '#8B949E', 'accent': '#8AB4F8'}

    def _history(self):
        try:
            return self.controller.ai.conversation_history or []
        except Exception:
            return []

    def reload(self):
        """Rebuild the list: session files first, then everything else."""
        self._list.clear()
        spanned = run_context.logs_in(self._history())
        spanned_names = {r['log'] for r in spanned}

        session_name = ''
        try:
            session_name = self.controller.session_manager.session_metadata.get(
                self.controller.current_session_id, {}).get('name', '')
        except Exception:
            pass
        if spanned:
            self._subtitle.setText(
                f"{session_name or 'This session'} spans {len(spanned)} log "
                f"file{'s' if len(spanned) != 1 else ''}.")
        else:
            self._subtitle.setText(
                f"{session_name or 'This session'} has no log stamps yet — it "
                f"predates log cross-referencing, or nothing has been said in it.")

        self._add_header("This session spans")
        if not spanned:
            self._add_note("(nothing stamped)")
        for rec in spanned:
            pids = ", ".join(str(p) for p in rec['pids']) or "?"
            self._add_file(rec['log'],
                           f"    {rec['entries']} entries · pid {pids}")

        self._add_header("All logs")
        try:
            others = sorted(_logs_dir().glob("log_*.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            others = []
        listed = 0
        for p in others:
            if p.name in spanned_names:
                continue
            self._add_file(p.name, None)
            listed += 1
        if not listed:
            self._add_note("(none)")

    def _add_header(self, text):
        it = QListWidgetItem(text.upper())
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        self._list.addItem(it)

    def _add_note(self, text):
        it = QListWidgetItem(text)
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self._list.addItem(it)

    def _add_file(self, name, detail):
        it = QListWidgetItem(name if detail is None else f"{name}\n{detail}")
        it.setData(Qt.ItemDataRole.UserRole, name)
        self._list.addItem(it)

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_pick(self, current, _previous=None):
        if current is None:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not name:
            return
        self._selected = _logs_dir() / name
        self._preview.setPlainText(self._tail(self._selected))

    @staticmethod
    def _tail(path: Path, tail_bytes: int = _TAIL_BYTES) -> str:
        """Last `tail_bytes` of the file. Never the whole thing — logs run to
        megabytes and loading one would freeze the GUI thread."""
        try:
            size = path.stat().st_size
            with open(path, "rb") as f:
                f.seek(max(0, size - tail_bytes))
                data = f.read()
            text = data.decode("utf-8", errors="replace")
            if size > tail_bytes:
                text = (f"… showing the last {tail_bytes // 1024} KB of "
                        f"{size // 1024} KB …\n\n") + text
            return text
        except Exception as e:
            return f"Could not read {path.name}: {type(e).__name__}: {e}"

    def _open_selected(self):
        if self._selected:
            open_in_editor(self._selected)

    def _reveal_selected(self):
        if self._selected:
            reveal_in_folder(self._selected)

    def _copy_selected(self):
        if not self._selected:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(str(self._selected))
        except Exception:
            pass
