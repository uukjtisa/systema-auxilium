"""
ui/manage_tasks_window.py
Manage Tasks Window — Create, edit, and review scheduled task sessions.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QTextEdit, QCheckBox,
    QSpinBox, QStackedWidget, QSizePolicy, QApplication,
    QTimeEdit, QRadioButton, QDateTimeEdit, QMessageBox, QComboBox,
)
from systema.security import code_guard as _guard
from systema.common.logger import _make_logger, _NoOpLogger
from PyQt6.QtCore import Qt, QTime, QDateTime, QPoint, QThread, pyqtSignal, QTimer, QFileSystemWatcher
from PyQt6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor
import re
from systema.ui.base_window import BaseWindow
from systema.ui import theme as _theme


# ── Per-task security policy (allow/deny only — background agents can't prompt) ──
# All code_guard categories. A new task allows the non-destructive ops and denies
# the genuinely dangerous ones, so a typical fetch/compute/report task works out
# of the box while deletes / process spawns / dynamic exec / OS-internals / secrets
# stay blocked until the user opts in.
_TASK_POLICY_DENY_BY_DEFAULT = (
    _guard.CAT_FILE_DELETE, _guard.CAT_PROCESS, _guard.CAT_DYNAMIC,
    _guard.CAT_SYSTEM, _guard.CAT_SECRETS,
)


def _all_policy_categories():
    cats = []
    for _title, _grp in _guard.CATEGORY_GROUPS:
        cats.extend(_grp)
    return cats


def _default_task_policy():
    return {c: ('deny' if c in _TASK_POLICY_DENY_BY_DEFAULT else 'allow')
            for c in _all_policy_categories()}


def _task_policy_presets():
    """Built-in allow/deny presets for the per-task policy grid (background tasks
    have no 'ask'). Returned as an ordered (label, {cat: 'allow'|'deny'}) list."""
    cats = _all_policy_categories()
    fetch = {c: 'deny' for c in cats}
    if _guard.CAT_NETWORK in fetch:
        fetch[_guard.CAT_NETWORK] = 'allow'
    return [
        ("Standard  —  safe file ops + network, dangerous ops blocked", _default_task_policy()),
        ("Fetch & report  —  network only, everything else blocked", fetch),
        ("Locked down  —  block everything", {c: 'deny' for c in cats}),
        ("Trusted  —  allow everything (use with care)", {c: 'allow' for c in cats}),
    ]


def _migrate_task_policy(perms: dict) -> dict:
    """Derive a per-category allow/deny policy for an OLD task dict that predates
    the policy grid. bypass_supervised=True ran everything → all allow; a plain
    workmode task only ever ran finding-free 'safe' code (risky ops hung on a
    dialog nobody answered) → all deny; no workmode → all deny (moot, tool off)."""
    if isinstance(perms.get('task_security_policy'), dict):
        pol = _default_task_policy()
        pol.update({k: v for k, v in perms['task_security_policy'].items()
                    if v in ('allow', 'deny')})
        return pol
    all_allow = bool(perms.get('bypass_supervised', False))
    return {c: ('allow' if all_allow else 'deny') for c in _all_policy_categories()}

# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("Manage Task Window") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


_BG       = "#080D14"
_SURFACE  = "#0F1620"
_SURFACE2 = "#1A2233"
_BORDER   = "#252E40"
_ACCENT   = "#4F9EF8"
_TEXT     = "#E8EFF8"
_MUTED    = "#637080"
_RED      = "#F0524F"
_GREEN    = "#34D058"
_YELLOW   = "#E3B341"
_PURPLE   = "#A78BFA"
_GLOW     = "rgba(79, 158, 248, 0.12)"
_GLOW_GN  = "rgba(52, 208, 88, 0.15)"

_BTN = f"""
    QPushButton {{
        background: {_SURFACE2}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 8px;
        padding: 6px 16px; font-size: 12px;
    }}
    QPushButton:hover {{ background: #22304A; border-color: {_ACCENT}; color: {_ACCENT}; }}
    QPushButton:pressed {{ background: #111A28; }}
"""
_BTN_ACCENT = f"""
    QPushButton {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #5AA8FF, stop:1 {_ACCENT});
        color: #060C14;
        border: none; border-radius: 8px;
        padding: 6px 18px; font-size: 12px; font-weight: 700;
    }}
    QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 #7BBEFF, stop:1 #5AA8FF); }}
    QPushButton:pressed {{ background: #388BFD; }}
"""
_BTN_RED = f"""
    QPushButton {{
        background: transparent; color: {_RED};
        border: 1px solid rgba(240,82,79,0.45); border-radius: 8px;
        padding: 5px 12px; font-size: 11px;
    }}
    QPushButton:hover {{ background: rgba(240,82,79,0.12); border-color: {_RED}; }}
"""
_BTN_GHOST = f"""
    QPushButton {{
        background: transparent; color: {_MUTED};
        border: 1px solid {_BORDER}; border-radius: 8px;
        padding: 5px 12px; font-size: 11px;
    }}
    QPushButton:hover {{ background: {_SURFACE2}; color: {_TEXT}; border-color: {_ACCENT}; }}
"""
_INPUT = f"""
    QLineEdit, QTextEdit, QSpinBox, QTimeEdit {{
        background: {_SURFACE2}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 8px;
        padding: 7px 12px; font-size: 12px;
    }}
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QTimeEdit:focus {{
        border-color: {_ACCENT}; background: #1E2D45;
    }}
"""
_CHECK = f"""
    QCheckBox {{ color: {_TEXT}; font-size: 12px; spacing: 8px; }}
    QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px;
        border: 1px solid {_BORDER}; background: {_SURFACE2}; }}
    QCheckBox::indicator:checked {{
        background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
            stop:0 #5AA8FF, stop:1 {_ACCENT});
        border-color: {_ACCENT}; }}
    QCheckBox::indicator:hover {{ border-color: {_ACCENT}; }}
"""
_SEC = f"color: {_ACCENT}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
_LBL = f"color: {_TEXT}; font-size: 12px;"


def _refresh_palette(controller):
    """Rebind the module-level colour constants + composed stylesheets to match
    the user's currently selected theme (controller.settings['chat_theme']).

    The defaults above keep this module importable standalone; this is called at
    the top of ManageTasksWindow.__init__ so the window is built in the active
    theme. All styling here reads these globals at call time, so refreshing them
    before the UI is built is enough to retheme the whole window.
    """
    global _BG, _SURFACE, _SURFACE2, _BORDER, _ACCENT, _TEXT, _MUTED
    global _RED, _GREEN, _YELLOW, _PURPLE, _GLOW, _GLOW_GN
    global _BTN, _BTN_ACCENT, _BTN_RED, _BTN_GHOST, _INPUT, _CHECK, _SEC, _LBL

    p = _theme.current_palette(controller)
    _BG, _SURFACE, _SURFACE2 = p['bg'], p['surface'], p['surface2']
    _BORDER, _ACCENT = p['border'], p['accent']
    _TEXT, _MUTED = p['text'], p['muted']
    _RED, _GREEN, _YELLOW, _PURPLE = p['red'], p['green'], p['yellow'], p['purple']
    _GLOW = p['glow']
    _GLOW_GN = _theme.rgba(p['green'], 0.15)

    _BTN        = _theme.btn(p)
    _BTN_ACCENT = _theme.btn_accent(p)
    _BTN_RED    = _theme.btn_red(p)
    _BTN_GHOST  = _theme.btn_ghost(p)
    _INPUT      = _theme.input_qss(p)
    _CHECK      = _theme.check_qss(p)
    _SEC = f"color: {_ACCENT}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;"
    _LBL = f"color: {_TEXT}; font-size: 12px;"


class _PythonHighlighter(QSyntaxHighlighter):
    """Minimal Python syntax highlighter for the function code editor."""

    def __init__(self, document):
        super().__init__(document)

        def _fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(700)
            if italic:
                f.setFontItalic(True)
            return f

        self._rules = [
            (re.compile(
                r'\b(def|class|return|if|else|elif|for|while|try|except|finally|'
                r'with|import|from|as|pass|break|continue|raise|yield|and|or|not|'
                r'in|is|lambda|global|nonlocal|del|assert|True|False|None)\b'
            ), _fmt("#C792EA", bold=True)),
            (re.compile(
                r'\b(print|len|range|type|str|int|float|list|dict|set|tuple|bool|'
                r'open|enumerate|zip|map|filter|sorted|reversed|sum|min|max|abs|'
                r'round|hasattr|getattr|setattr|isinstance|super)\b'
            ), _fmt("#82AAFF")),
            (re.compile(r'\bself\b'),       _fmt("#F07178")),
            (re.compile(r'\bcontroller\b'), _fmt("#FFCB6B")),
            (re.compile(r'@\w+'),           _fmt("#FFCB6B")),
            (re.compile(r'\b\d+\.?\d*\b'),  _fmt("#F78C6C")),
            (re.compile(r'#.*$'),           _fmt("#546E7A", italic=True)),
        ]
        self._str_fmt = _fmt("#C3E88D")
        self._str_patterns = [
            re.compile(r'""".*?"""'),
            re.compile(r"'''.*?'''"),
            re.compile(r'"(?:[^"\\]|\\.)*"'),
            re.compile(r"'(?:[^'\\]|\\.)*'"),
        ]

    def highlightBlock(self, text):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), fmt)
        for pattern in self._str_patterns:
            for m in pattern.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), self._str_fmt)


def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"background: {_BORDER}; max-height: 1px;")
    return f

def _extract_after_code_block(raw: str) -> str:
    """Return any text that follows the closing ``` of a ```python_interpreter block."""
    open_idx = raw.find('```python_interpreter')
    if open_idx == -1:
        return ''
    close_idx = raw.find('```', open_idx + len('```python_interpreter'))
    if close_idx == -1:
        return ''
    return raw[close_idx + 3:].strip()

def _split_work_message(content: str):
    """For an assistant message with a tool fence, return (visible_text, code, tool).
    tool is '' when the message has no python_interpreter/execute_code fence."""
    for tool in ('python_interpreter', 'execute_code'):
        marker = f'```{tool}'
        idx = content.find(marker)
        if idx != -1:
            visible = content[:idx].strip()
            rest = content[idx + len(marker):]
            end = rest.find('```')
            code = (rest[:end] if end != -1 else rest).strip()
            return visible, code, tool
    return content.strip(), '', ''


def _viewer_group_history(history: list) -> list:
    """
    Groups session chat history for viewer display.
    Consecutive assistant messages that all contain a ```python_interpreter block
    are merged into a single display unit so the viewer shows one 'Interpreter Session'
    bubble instead of N separate raw-code bubbles.
    Each group: {'role': str, 'content': str, '_step_count': int}
    _step_count > 1 means it is a merged interpreter-session group.
    Session history is never modified.
    """
    groups = []
    i = 0
    while i < len(history):
        msg = history[i]
        role = msg.get('role', '')
        content = msg.get('content', '')
        if role == 'assistant' and '```python_interpreter' in content:
            # Collect all consecutive python_interpreter assistant messages
            collected = [msg]
            i += 1
            while i < len(history):
                nxt = history[i]
                if (nxt.get('role') == 'assistant'
                        and '```python_interpreter' in nxt.get('content', '')):
                    collected.append(nxt)
                    i += 1
                else:
                    break
            # Build a merged display entry
            if len(collected) == 1:
                raw1 = collected[0].get('content', '')
                split1 = raw1.find('```python_interpreter')
                vis1 = raw1[:split1].strip() if split1 != -1 else raw1.strip()
                inline_tail = _extract_after_code_block(raw1)
                tail1 = inline_tail
                if (not tail1 and i < len(history)
                        and history[i].get('role') == 'assistant'
                        and '```python_interpreter' not in history[i].get('content', '')):
                    tail1 = history[i].get('content', '').strip()
                    i += 1
                groups.append({
                    'role': 'assistant',
                    'content': vis1 if vis1 else '(executing…)',
                    '_step_count': 1,
                    '_tail': tail1,
                })
            else:
                # Extract visible text before each python_interpreter block for display
                parts = []
                for step_i, step_msg in enumerate(collected, 1):
                    raw = step_msg.get('content', '')
                    # Grab text before the first ```python_interpreter marker
                    split_idx = raw.find('```python_interpreter')
                    visible = raw[:split_idx].strip() if split_idx != -1 else raw.strip()
                    if visible:
                        parts.append(f"[Step {step_i}] {visible}")
                    else:
                        parts.append(f"[Step {step_i}] (executing…)")
                merged_content = '\n\n'.join(parts)

                # Capture inline tail from the last step (text after its closing ```)
                last_raw = collected[-1].get('content', '')
                inline_tail = _extract_after_code_block(last_raw)

                # Also check the next message if no inline tail found yet
                tail_content = inline_tail
                if (not tail_content and i < len(history)
                        and history[i].get('role') == 'assistant'
                        and '```python_interpreter' not in history[i].get('content', '')):
                    tail_content = history[i].get('content', '').strip()
                    i += 1  # consume it so it won't also render as a separate bubble

                groups.append({
                    'role': 'assistant',
                    'content': merged_content,
                    '_step_count': len(collected),
                    '_tail': tail_content,
                })
        else:
            # Hide work-mode system prompts from the viewer.
            # Only actual task pings (SYSTEM_AUTOMATED_TASK_PING) are shown.
            # Non-ping system messages stay in history for the AI — just not displayed.
            if role == 'system' and 'SYSTEM_AUTOMATED_TASK_PING' not in content:
                i += 1
                continue
            groups.append({'role': role, 'content': content, '_step_count': 1})
            i += 1
    return groups


class _VerifyWorker(QThread):
    """Runs {{ }} block verification off the main thread so the UI never freezes."""
    done = pyqtSignal(dict)

    def __init__(self, text: str, functions: list, controller, blocking_patterns: list):
        super().__init__()
        self._text = text
        self._functions = functions
        self._controller = controller
        self._blocking_patterns = blocking_patterns

    def run(self):
        import ast
        raw_blocks = re.findall(r'\{\{(.*?)\}\}', self._text, re.DOTALL)
        if not raw_blocks:
            self.done.emit({'blocks': [], 'has_errors': False, 'has_blocking': False})
            return

        try:
            from systema.execution.python_interpreter import PythonInterpreter
            interp = PythonInterpreter()
            interp.namespace['controller'] = self._controller
            for _fn in self._functions:
                try:
                    interp.execute(_fn['code'])
                except Exception:
                    pass
        except Exception as e:
            self.done.emit({
                'blocks': [{'expr': '(interpreter)', '_raw': '', 'result': '',
                            'error': f"Could not load Python interpreter: {e}", 'blocking': None}],
                'has_errors': True,
                'has_blocking': False,
            })
            return

        results = []
        has_errors = False
        has_blocking = False

        for raw in raw_blocks:
            expr = raw.strip()
            blocking_reason = None
            error = None
            result_str = ''

            for pattern, reason in self._blocking_patterns:
                if re.search(pattern, expr):
                    blocking_reason = reason
                    has_blocking = True
                    break

            if blocking_reason:
                results.append({'expr': expr, '_raw': raw, 'result': '', 'error': None, 'blocking': blocking_reason})
                continue

            try:
                ast.parse(expr)
            except SyntaxError as e:
                has_errors = True
                results.append({'expr': expr, '_raw': raw, 'result': '', 'error': f"SyntaxError: {e}", 'blocking': None})
                continue

            try:
                out = interp.execute(expr)
                if out.get('error'):
                    last_line = out['error'].strip().splitlines()[-1] if out['error'].strip() else 'Error'
                    error = last_line
                    has_errors = True
                elif out.get('stdout'):
                    result_str = out['stdout'].strip()
                elif out.get('result') is not None:
                    result_str = repr(out['result'])
                else:
                    result_str = '(no output)'
            except Exception as e:
                error = str(e)
                has_errors = True

            results.append({'expr': expr, '_raw': raw, 'result': result_str, 'error': error, 'blocking': None})

        self.done.emit({'blocks': results, 'has_errors': has_errors, 'has_blocking': has_blocking})


class ManageTasksWindow(BaseWindow):
    """Main manage-tasks window — task list, editor, and session viewer."""

    _header_height: int = 44  # matches title bar height for resize handles

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        _refresh_palette(controller)   # match the active app theme before building UI
        self._skill_manager = getattr(controller, 'skill_manager', None)
        self._init_chrome_state()
        self._controller = controller
        self._task_mgr = getattr(controller, 'task_manager', None)
        self._editing_task_id = None  # None = creating new
        self._verify_worker = None  # background verify QThread (Verify button)
        self._save_verify_worker = None  # background verify QThread (Save path)
        self._save_spinner_timer = None  # QTimer animating the Save button
        self._save_spinner_frame = 0
        self._sessions_expanded = {}  # task_id → bool
        self._selected_script_name = ''  # currently selected script in Script Trigger mode
        self._viewer_task_id: str | None = None     # task currently open in session viewer
        self._viewer_date_str: str | None = None    # session date currently open
        self._viewer_msg_count: int = 0             # message count at last render
        self._viewer_ongoing: bool = False          # whether the open session is mid-ping
        self._viewer_refresh_timer: QTimer | None = None  # live-refresh timer

        self.setWindowTitle("Manage Tasks")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(860, 680)
        self.setStyleSheet("background: transparent;")

        self.container = QWidget(self)
        self.container.setObjectName("mtContainer")
        self.container.setStyleSheet(f"""
                    QWidget#mtContainer {{
                        background-color: {_SURFACE};
                        border-radius: 12px;
                    }}
                """)

        _wrapper = QVBoxLayout(self)
        _wrapper.setContentsMargins(0, 0, 0, 0)
        _wrapper.addWidget(self.container)

        root = QVBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Draggable title bar ───────────────────────────────────────────────
        self._title_bar = self._build_title_bar()
        root.addWidget(self._title_bar)

        # ── Stack: 0=list  1=editor  2=session viewer ────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        self._stack.addWidget(self._build_list_page())    # index 0
        self._stack.addWidget(self._build_editor_page())  # index 1
        self._stack.addWidget(self._build_viewer_page())  # index 2

        self._stack.setCurrentIndex(0)
        self._refresh_list()

        # ── File watcher: auto-reload when data files change externally ───────
        self._file_watcher = QFileSystemWatcher(self)
        if self._task_mgr is not None:
            try:
                import os as _os
                _tf = str(self._task_mgr.TASKS_FILE)
                _ff = str(self._task_mgr.FUNCTIONS_FILE)
                _sd = str(self._task_mgr.TASKS_FILE.parent.parent / 'task-sessions')
                for _p in (_tf, _ff):
                    if _os.path.exists(_p):
                        self._file_watcher.addPath(_p)
                if _os.path.isdir(_sd):
                    self._file_watcher.addPath(_sd)
            except Exception:
                pass
        self._file_watcher.fileChanged.connect(self._on_watched_file_changed)
        self._file_watcher.directoryChanged.connect(self._on_watched_dir_changed)

        self.setMinimumSize(680, 480)
        self.create_resize_handles()
        self._sync_glass()

    # ═══════════════════════════════════════════════════════════════════════════
    # WINDOW CHROME
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_title_bar(self) -> QFrame:
        bar = QFrame()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f"""
                QFrame {{
                    background: {_SURFACE};
                    border-top-left-radius: 12px;
                    border-top-right-radius: 12px;
                    border-bottom: 1px solid {_BORDER};
                }}
            """)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 0, 12, 0)
        hl.setSpacing(8)

        for _col in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_col}; border-radius: 5px; border: none; }}")
            hl.addWidget(dot)

        title = QLabel("Manage Tasks")
        title.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; font-weight: 600; background: transparent; border: none;"
        )
        hl.addWidget(title, stretch=1)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; border: none;
                        color: {_MUTED}; font-size: 18px; border-radius: 5px;
                    }}
                    QPushButton:hover {{ background: #EA4335; color: white; }}
                """)
        close_btn.clicked.connect(self.close)
        hl.addWidget(close_btn)

        bar.mousePressEvent = self.header_mouse_press
        bar.mouseMoveEvent = self.header_mouse_move
        bar.mouseReleaseEvent = self.header_mouse_release
        return bar

    def apply_rounded_mask(self):
        pass  # CSS border-radius on container handles this cleanly

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'resize_handles'):
            self.position_resize_handles()

    def apply_theme(self, theme_key=None):
        """Live-retint the whole window to the active theme.

        Called by controller.broadcast_theme() the instant the user saves a new
        theme in Settings. Refreshes the palette, then rebuilds the title bar and
        stacked pages in place (preserving the current page) so every surface,
        button and input picks up the new colours.
        """
        try:
            _refresh_palette(self._controller)
            # Container surface
            self.container.setStyleSheet(f"""
                QWidget#mtContainer {{
                    background-color: {_SURFACE};
                    border-radius: 12px;
                }}
            """)
            root = self.container.layout()
            # Replace the title bar
            new_bar = self._build_title_bar()
            root.replaceWidget(self._title_bar, new_bar)
            self._title_bar.deleteLater()
            self._title_bar = new_bar
            # Rebuild the stacked pages, keeping the current page selected
            idx = self._stack.currentIndex()
            while self._stack.count():
                w = self._stack.widget(0)
                self._stack.removeWidget(w)
                w.deleteLater()
            self._stack.addWidget(self._build_list_page())    # 0
            self._stack.addWidget(self._build_editor_page())  # 1
            self._stack.addWidget(self._build_viewer_page())  # 2
            self._stack.setCurrentIndex(idx if 0 <= idx <= 2 else 0)
            self._refresh_list()
            self._sync_glass()
        except Exception as e:
            log.error(f"[ManageTasksWindow.apply_theme] {e}")

    def _sync_glass(self):
        """If glass mode is on, overlay a translucent backdrop on the window so
        the desktop shows through behind the content panels (cards stay solid)."""
        try:
            if not _theme.glass_enabled_for(self._controller, 'manage_tasks'):
                return
            _, op = _theme.glass_state(self._controller)
            bd = _theme.glass_backdrop(op)
            self.container.setStyleSheet(
                f"QWidget#mtContainer {{ background-color: {bd}; border-radius: 12px; }}"
            )
            self._title_bar.setStyleSheet(
                f"QFrame {{ background: {bd}; border-top-left-radius: 12px;"
                f" border-top-right-radius: 12px;"
                f" border-bottom: 1px solid rgba(50,50,50,0.5); }}"
            )
            for i in range(self._stack.count()):
                pg = self._stack.widget(i)
                if pg is not None:
                    pg.setStyleSheet("background: transparent;")
        except Exception as e:
            log.error(f"[ManageTasksWindow._sync_glass] {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE 0 — Task List
    # ═══════════════════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE 0 — Task List
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(22, 18, 22, 18)
        vl.setSpacing(14)

        # Header
        hl = QHBoxLayout()
        hl.setSpacing(12)
        _title = QLabel("Scheduled Tasks")
        _title.setStyleSheet(
            f"color: {_TEXT}; font-size: 16px; font-weight: 700; background: transparent;"
        )
        self._tasks_header_count = QLabel("")
        self._tasks_header_count.setStyleSheet(
            f"color: {_MUTED}; font-size: 11px; background: transparent;"
        )
        hl.addWidget(_title)
        hl.addWidget(self._tasks_header_count)
        hl.addStretch()
        new_btn = QPushButton("＋  New Task")
        new_btn.setStyleSheet(_BTN_ACCENT)
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._new_task)
        hl.addWidget(new_btn)
        vl.addLayout(hl)

        # Thin accent divider
        _div = QFrame()
        _div.setFrameShape(QFrame.Shape.HLine)
        _div.setFixedHeight(1)
        _div.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
                           f"stop:0 {_ACCENT}, stop:0.5 {_BORDER}, stop:1 {_BG});")
        vl.addWidget(_div)

        # Scrollable task list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_container)
        vl.addWidget(scroll, stretch=1)

        return page

    def _refresh_list(self):
        """Rebuild the task list from TaskManager."""
        # Clear existing rows (keep the trailing stretch)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._task_mgr is None:
            lbl = QLabel("Task Manager is not available.")
            lbl.setStyleSheet(f"color: {_MUTED}; padding: 20px;")
            self._list_layout.insertWidget(0, lbl)
            return

        tasks = self._task_mgr.get_tasks()
        if hasattr(self, '_tasks_header_count'):
            _active = sum(1 for t in tasks if t.get('active', True))
            self._tasks_header_count.setText(
                f"  {_active} active · {len(tasks)} total" if tasks else ""
            )
        if not tasks:
            _empty = QWidget()
            _empty.setStyleSheet(f"background: {_SURFACE}; border-radius: 10px; border: 1px solid {_BORDER};")
            _el = QVBoxLayout(_empty)
            _el.setContentsMargins(24, 28, 24, 28)
            _ei = QLabel("🗂")
            _ei.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _ei.setStyleSheet("font-size: 32px; background: transparent; border: none;")
            _et = QLabel("No tasks yet")
            _et.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _et.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 600; background: transparent; border: none;")
            _eh = QLabel("Click ＋ New Task to create your first scheduled agent task.")
            _eh.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _eh.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent; border: none;")
            _el.addWidget(_ei)
            _el.addWidget(_et)
            _el.addWidget(_eh)
            self._list_layout.insertWidget(0, _empty)
            return

        for i, task in enumerate(tasks):
            row = self._build_task_row(task)
            self._list_layout.insertWidget(i, row)

    def _build_task_row(self, task: dict) -> QWidget:
        task_id = task['id']
        is_active = task.get('active', True)
        _status_color = _GREEN if is_active else _MUTED

        # Outer wrapper for the left status bar effect
        wrapper = QWidget()
        wrapper.setObjectName("taskRowWrapper")
        wrapper.setStyleSheet(f"""
            QWidget#taskRowWrapper {{
                background: transparent;
            }}
        """)
        _wl = QHBoxLayout(wrapper)
        _wl.setContentsMargins(0, 0, 0, 0)
        _wl.setSpacing(0)

        # Left color bar
        _bar = QFrame()
        _bar.setFixedWidth(3)
        _bar.setStyleSheet(f"background: {_status_color}; border-radius: 2px;")
        _wl.addWidget(_bar)

        container = QWidget()
        container.setObjectName("taskRowCard")
        container.setStyleSheet(f"""
            QWidget#taskRowCard {{
                background: {_SURFACE};
                border-radius: 0 10px 10px 0;
                border: 1px solid {_BORDER};
                border-left: none;
            }}
            QWidget#taskRowCard:hover {{
                background: {_SURFACE2};
                border-color: rgba(79,158,248,0.3);
            }}
        """)
        _wl.addWidget(container, stretch=1)

        vl = QVBoxLayout(container)
        vl.setContentsMargins(16, 12, 14, 12)
        vl.setSpacing(6)

        # ── Top row: name + buttons ───────────────────────────────────────────
        top = QHBoxLayout()

        name_col = QVBoxLayout()
        name_col.setSpacing(3)
        name_lbl = QLabel(task.get('name', 'Unnamed Task'))
        name_lbl.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; font-weight: 700; background: transparent; border: none;")
        name_col.addWidget(name_lbl)

        one_time = task.get('one_time_schedule', {})
        if one_time.get('enabled'):
            dts = one_time.get('datetimes', [])
            fired = not task.get('active', True)
            if fired:
                info_lbl = QLabel(f"One-time: {dts[0] if dts else '?'}  ·  fired ✓")
            else:
                count = len(dts)
                label = dts[0] if dts else '?'
                if count > 1:
                    label += f"  +{count - 1} backup{'s' if count > 2 else ''}"
                info_lbl = QLabel(f"One-time: {label}")
        else:
            _pim = task.get('ping_interval_mode', '')
            if not _pim:
                _pim = 'specific_times' if task.get('use_specific_ping_times', False) else 'timed'
            sched = task.get('daily_schedule', {})
            sched_text = "Whole day" if sched.get('whole_day') else f"{sched.get('start', '?')} – {sched.get('end', '?')}"
            if _pim == 'script_trigger':
                _sn = task.get('script_name', '?')
                info_lbl = QLabel(f"Scripted ping ({_sn})  ·  {sched_text}")
            elif _pim == 'specific_times':
                _times = task.get('specific_ping_times', [])
                _times_str = ', '.join(_times[:3]) + ('…' if len(_times) > 3 else '')
                info_lbl = QLabel(f"Specific times: {_times_str or '?'}  ·  {sched_text}")
            else:
                interval = task.get('interval_minutes', 30)
                info_lbl = QLabel(f"Every {interval} min  ·  {sched_text}")
        info_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 10px; background: transparent; border: none;")
        name_col.addWidget(info_lbl)
        top.addLayout(name_col, stretch=1)

        edit_btn = QPushButton("Edit")
        edit_btn.setFixedSize(52, 26)
        edit_btn.setStyleSheet(_BTN)
        edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        edit_btn.clicked.connect(lambda _, t=task: self._edit_task(t))
        top.addWidget(edit_btn)

        del_btn = QPushButton("Delete")
        del_btn.setFixedSize(56, 26)
        del_btn.setStyleSheet(_BTN_RED)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda _, tid=task_id: self._delete_task(tid))
        top.addWidget(del_btn)

        active_btn = QPushButton("● Active" if is_active else "○ Inactive")
        active_btn.setFixedHeight(26)
        active_btn.setStyleSheet(
            f"QPushButton {{ background: {_GLOW_GN}; color: {_GREEN}; "
            f"border: 1px solid rgba(52,208,88,0.4); "
            f"border-radius: 8px; padding: 4px 12px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: rgba(52,208,88,0.22); }}"
            if is_active else
            f"QPushButton {{ background: transparent; color: {_MUTED}; "
            f"border: 1px solid {_BORDER}; "
            f"border-radius: 8px; padding: 4px 12px; font-size: 11px; }}"
            f"QPushButton:hover {{ background: {_SURFACE2}; color: {_TEXT}; }}"
        )
        active_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        active_btn.clicked.connect(lambda _, tid=task_id, btn=active_btn: self._toggle_task_active(tid, btn))
        top.addWidget(active_btn)

        ping_btn = QPushButton("⚡ Ping")
        ping_btn.setFixedHeight(26)
        ping_btn.setStyleSheet(
            f"QPushButton {{ background: {_GLOW}; color: {_ACCENT}; "
            f"border: 1px solid rgba(79,158,248,0.45); "
            f"border-radius: 8px; padding: 4px 12px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: rgba(79,158,248,0.22); }}"
        )
        ping_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ping_btn.setToolTip("Fire an immediate test ping — edit the prompt, then send it to the AI now.")
        ping_btn.clicked.connect(lambda _, t=task: self._open_manual_ping_dialog(t))
        top.addWidget(ping_btn)

        prompt_btn = QPushButton("👁  Task System Prompt")
        prompt_btn.setStyleSheet(_BTN)
        prompt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prompt_btn.setToolTip("Preview the system prompt this task session will receive.")
        prompt_btn.clicked.connect(lambda _, t=task: self._show_system_prompt_preview(task_dict=t))
        top.addWidget(prompt_btn)

        sess_btn = QPushButton("▶  Sessions")
        sess_btn.setFixedHeight(26)
        sess_btn.setStyleSheet(_BTN)
        sess_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        vl.addLayout(top)

        # ── Sessions dropdown (hidden by default) ─────────────────────────────
        sess_panel = QWidget()
        sess_panel.setStyleSheet(f"background: {_BG}; border-radius: 6px; border: 1px solid {_BORDER};")
        sess_panel.setVisible(False)
        sess_vl = QVBoxLayout(sess_panel)
        sess_vl.setContentsMargins(8, 8, 8, 8)
        sess_vl.setSpacing(4)

        def _toggle_sessions():
            visible = not sess_panel.isVisible()
            sess_panel.setVisible(visible)
            sess_btn.setText("▼  Sessions" if visible else "▶  Sessions")
            if visible:
                self._populate_sessions_panel(sess_vl, task_id)

        sess_btn.clicked.connect(_toggle_sessions)
        top.addWidget(sess_btn)

        vl.addWidget(sess_panel)
        return wrapper

    # Past this many session rows the inline list switches to a scroll area so a
    # task with months of history never balloons the card.
    _SESSIONS_VISIBLE_CAP = 6
    _SESSION_ROW_H = 34

    def _populate_sessions_panel(self, layout: QVBoxLayout, task_id: str):
        # Clear existing
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count():
                    si = sub.takeAt(0)
                    if si.widget():
                        si.widget().deleteLater()

        if self._task_mgr is None:
            return

        sessions = self._task_mgr.get_task_sessions(task_id)
        sessions = sorted(sessions, reverse=True)   # newest first

        # ── Header: count + Clear All ─────────────────────────────────────────
        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 2, 0)
        count_lbl = QLabel(f"{len(sessions)} session{'s' if len(sessions) != 1 else ''}")
        count_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 10px; font-weight: 600; background: transparent;")
        head.addWidget(count_lbl)
        head.addStretch()
        if sessions:
            clear_btn = QPushButton("✕  Clear all")
            clear_btn.setFixedHeight(24)
            clear_btn.setStyleSheet(_BTN_RED)
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(lambda: self._clear_all_sessions(task_id, layout))
            head.addWidget(clear_btn)
        layout.addLayout(head)

        if not sessions:
            empty = QLabel("No sessions yet — fire a ⚡ Ping to start one.")
            empty.setStyleSheet(f"color: {_MUTED}; font-size: 11px; padding: 8px 2px; background: transparent;")
            layout.addWidget(empty)
            return

        # ── Rows (capped height → scrolls when long) ──────────────────────────
        rows_host = QWidget()
        rows_host.setStyleSheet("background: transparent;")
        rows_vl = QVBoxLayout(rows_host)
        rows_vl.setContentsMargins(0, 0, 0, 0)
        rows_vl.setSpacing(4)
        for date_str in sessions:
            rows_vl.addWidget(self._build_session_row(task_id, date_str, layout))
        rows_vl.addStretch()

        if len(sessions) > self._SESSIONS_VISIBLE_CAP:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(rows_host)
            scroll.setFixedHeight(self._SESSIONS_VISIBLE_CAP * (self._SESSION_ROW_H + 4))
            scroll.setStyleSheet(
                "QScrollArea { border: none; background: transparent; }"
                f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}"
                f"QScrollBar::handle:vertical {{ background: {_BORDER}; border-radius: 4px; min-height: 24px; }}"
                f"QScrollBar::handle:vertical:hover {{ background: {_MUTED}; }}"
                "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
            )
            layout.addWidget(scroll)
        else:
            layout.addWidget(rows_host)

    def _build_session_row(self, task_id: str, date_str: str, panel_layout: QVBoxLayout) -> QWidget:
        """One clickable session row inside the sessions dropdown."""
        row = QWidget()
        row.setObjectName("sessRow")
        row.setFixedHeight(self._SESSION_ROW_H)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(
            f"QWidget#sessRow {{ background: {_SURFACE}; border: 1px solid {_BORDER}; border-radius: 7px; }}"
            f"QWidget#sessRow:hover {{ background: {_SURFACE2}; border-color: rgba(79,158,248,0.4); }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 0, 6, 0)
        rl.setSpacing(8)

        lbl = QLabel(self._format_session_date(date_str))
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        rl.addWidget(lbl)
        rl.addStretch()

        x_btn = QPushButton("✕")
        x_btn.setFixedSize(22, 22)
        x_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_MUTED}; border: none; "
            f"border-radius: 5px; font-size: 13px; font-weight: 700; padding: 0; }}"
            f"QPushButton:hover {{ background: {_RED}; color: #ffffff; }}"
        )
        x_btn.setToolTip("Delete this session")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(lambda _, tid=task_id, d=date_str, lo=panel_layout: self._delete_session(tid, d, lo))
        rl.addWidget(x_btn)

        # Click anywhere on the row (except the ✕) opens the viewer.
        row.mousePressEvent = lambda e, tid=task_id, d=date_str: self._view_session(tid, d)
        return row

    @staticmethod
    def _format_session_date(date_str: str) -> str:
        """'2026-06-27' → 'Sat, Jun 27 2026' (falls back to raw on parse error)."""
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(date_str, "%Y-%m-%d")
            today = _dt.now().date()
            if d.date() == today:
                return f"Today · {d.strftime('%b %d')}"
            if (today - d.date()).days == 1:
                return f"Yesterday · {d.strftime('%b %d')}"
            return d.strftime("%a, %b %d %Y")
        except Exception:
            return date_str

    def _clear_all_sessions(self, task_id: str, layout: QVBoxLayout):
        if self._task_mgr:
            self._task_mgr.delete_all_task_sessions(task_id)
            self._populate_sessions_panel(layout, task_id)

    def _delete_session(self, task_id: str, date_str: str, layout: QVBoxLayout):
        if self._task_mgr:
            self._task_mgr.delete_task_session(task_id, date_str)
            self._populate_sessions_panel(layout, task_id)

    def _toggle_task_active(self, task_id: str, btn: QPushButton):
        if self._task_mgr is None:
            return
        tasks = self._task_mgr.get_tasks()
        task = next((t for t in tasks if t['id'] == task_id), None)
        if task is None:
            return
        task['active'] = not task.get('active', True)
        self._task_mgr.update_task(task_id, task)
        is_active = task['active']
        btn.setText("● Active" if is_active else "○ Inactive")
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_GREEN}; border: 1px solid {_GREEN}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
            if is_active else
            f"QPushButton {{ background: transparent; color: {_MUTED}; border: 1px solid {_MUTED}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
        )
        self._refresh_list()  # Rebuild rows so the left color bar also updates

    def _delete_task(self, task_id: str):
        if self._task_mgr:
            self._task_mgr.delete_task(task_id)
            self._refresh_list()

    def _on_watched_file_changed(self, path: str):
        """Reload tasks/functions from disk when tasks.json or functions.json changes externally."""
        if self._task_mgr is not None:
            try:
                self._task_mgr.reload_from_disk()
            except Exception:
                pass
        self._refresh_list()
        # Re-add path: atomic saves (tmp → rename) cause the watcher to lose track of the file
        try:
            import os as _os
            if _os.path.exists(path):
                self._file_watcher.addPath(path)
        except Exception:
            pass

    def _on_watched_dir_changed(self, _path: str):
        """Refresh the list when the task-sessions directory changes."""
        self._refresh_list()

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — Task Editor
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QWidget()
        bar.setStyleSheet(f"background: {_SURFACE}; border-bottom: 1px solid {_BORDER};")
        bar.setFixedHeight(48)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)
        self._back_btn = QPushButton("← Back")
        self._back_btn.setStyleSheet(_BTN)
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        bl.addWidget(self._back_btn)
        self._editor_title = QLabel("New Task")
        self._editor_title.setStyleSheet(
            f"color: {_TEXT}; font-size: 14px; font-weight: 600; padding-left: 12px;"
        )
        bl.addWidget(self._editor_title, stretch=1)

        _preview_btn = QPushButton("👁  Task System Prompt")
        _preview_btn.setStyleSheet(_BTN)
        _preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _preview_btn.setToolTip("Preview the full system prompt this task session will receive.")
        _preview_btn.clicked.connect(lambda: self._show_system_prompt_preview(task_dict={
            'name': self._f_name.text().strip() or 'New Task (Preview)',
            'permissions': {
                'allow_workmode':          self._f_perm_workmode.isChecked(),
                'allow_skill_load_unload': False,
                'inject_image_tools':      self._f_perm_image_tools.isChecked(),
                'inject_controller_ref':   self._f_perm_controller_ref.isChecked(),
                'inject_notify_tool':      self._f_perm_notify_tool.isChecked(),
                'task_security_policy':    self._read_task_policy(),
            },
            'loaded_skills': self._get_checked_skill_names(),
        }))
        bl.addWidget(_preview_btn)

        self._save_btn = QPushButton("Save Task")
        self._save_btn.setStyleSheet(_BTN_ACCENT)
        self._save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_btn.clicked.connect(self._save_task)
        bl.addWidget(self._save_btn)
        outer.addWidget(bar)

        # ── Scrollable form ───────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        form = QWidget()
        form.setStyleSheet(f"background: {_BG};")
        fl = QVBoxLayout(form)
        fl.setContentsMargins(24, 20, 24, 20)
        fl.setSpacing(14)

        # ── Helper: labelled field ────────────────────────────────────────────
        def _field(label_text, widget, hint=None):
            grp = QWidget()
            grp.setStyleSheet("background: transparent;")
            gl = QVBoxLayout(grp)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(4)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(_SEC)
            gl.addWidget(lbl)
            if hint:
                h = QLabel(hint)
                h.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
                h.setWordWrap(True)
                gl.addWidget(h)
            gl.addWidget(widget)
            return grp

        # ── Helper: section card ──────────────────────────────────────────────
        def _card(*inner_widgets, title=None, icon=None):
            card = QWidget()
            card.setObjectName("editorCard")
            card.setStyleSheet(
                f"QWidget#editorCard {{ background: {_SURFACE}; border-radius: 10px;"
                f" border: 1px solid {_BORDER}; }}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 14, 18, 16)
            cl.setSpacing(12)
            if title:
                hdr = QLabel(f"{icon + '  ' if icon else ''}{title}")
                hdr.setStyleSheet(
                    f"color: {_TEXT}; font-size: 12px; font-weight: 700; "
                    f"letter-spacing: 0.4px; background: transparent; border: none;"
                )
                cl.addWidget(hdr)
                sep = QFrame()
                sep.setFixedHeight(1)
                sep.setStyleSheet(f"background: {_BORDER}; border: none;")
                cl.addWidget(sep)
            for w in inner_widgets:
                if isinstance(w, QWidget):
                    cl.addWidget(w)
                else:
                    cl.addLayout(w)
            return card

        # ══ CARD 1: Identity ══════════════════════════════════════════════════
        self._f_name = QLineEdit()
        self._f_name.setPlaceholderText("e.g. System Monitor, Daily Report…")
        self._f_name.setStyleSheet(_INPUT)

        self._f_active_editor = QCheckBox("Task is Active  (uncheck to pause without deleting)")
        self._f_active_editor.setChecked(True)
        self._f_active_editor.setStyleSheet(_CHECK)

        fl.addWidget(_card(
            _field("TASK NAME", self._f_name),
            self._f_active_editor,
            title="Identity", icon="🪪",
        ))

        # ══ CARD 2: Instruction ═══════════════════════════════════════════════
        self._f_instruction = QTextEdit()
        self._f_instruction.setPlaceholderText(
            "Describe what the agent should check or do.\n\n"
            "Embed live Python with {{ expr }} — its output replaces the block at ping time:\n"
            "  Current CPU: {{ import psutil; psutil.cpu_percent() }}%\n"
            "  Uptime:      {{ import uptime; uptime.uptime() }} seconds"
        )
        self._f_instruction.setMinimumHeight(130)
        self._f_instruction.setStyleSheet(_INPUT)
        # ── Verify button row ─────────────────────────────────────────────────
        verify_row = QWidget()
        verify_row.setStyleSheet("background: transparent;")
        vr = QHBoxLayout(verify_row)
        vr.setContentsMargins(0, 4, 0, 0)
        vr.setSpacing(8)
        verify_btn = QPushButton("Verify instruction")
        verify_btn.setStyleSheet(_BTN)
        verify_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        verify_btn.clicked.connect(self._run_verify_and_display)
        vr.addWidget(verify_btn)
        vr.addStretch()

        self._verify_result_box = QTextEdit()
        self._verify_result_box.setReadOnly(True)
        self._verify_result_box.setFixedHeight(90)
        self._verify_result_box.setStyleSheet(
            f"QTextEdit {{ background: {_BG}; color: {_TEXT}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; padding: 6px 8px; font-size: 11px; font-family: monospace; }}"
        )
        self._verify_result_box.setVisible(False)

        fl.addWidget(_card(
            _field(
                "INSTRUCTION",
                self._f_instruction,
                "What should the agent do at each ping? Supports {{ Python }} inline blocks.",
            ),
            verify_row,
            self._verify_result_box,
            title="Instruction", icon="📝",
        ))

        # ══ CARD 2b: Functions Library ════════════════════════════════════════
        fn_hint_lbl = QLabel(
            "Write reusable multi-line Python functions. Reference them in instructions with {{ my_func() }}. "
            "Functions are shared across all tasks."
        )
        fn_hint_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        fn_hint_lbl.setWordWrap(True)

        self._fn_list_widget = QWidget()
        self._fn_list_widget.setStyleSheet("background: transparent;")
        self._fn_list_layout = QVBoxLayout(self._fn_list_widget)
        self._fn_list_layout.setContentsMargins(0, 0, 0, 0)
        self._fn_list_layout.setSpacing(4)

        add_fn_btn = QPushButton("＋  Add Function")
        add_fn_btn.setStyleSheet(_BTN)
        add_fn_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._fn_editor_panel = QWidget()
        self._fn_editor_panel.setStyleSheet(
            f"background: {_BG}; border-radius: 6px; border: 1px solid {_BORDER};"
        )
        self._fn_editor_panel.setVisible(False)
        fe_layout = QVBoxLayout(self._fn_editor_panel)
        fe_layout.setContentsMargins(10, 10, 10, 10)
        fe_layout.setSpacing(6)

        fn_name_row = QWidget()
        fn_name_row.setStyleSheet("background: transparent;")
        fn_name_rl = QHBoxLayout(fn_name_row)
        fn_name_rl.setContentsMargins(0, 0, 0, 0)
        fn_name_rl.setSpacing(6)
        fn_name_lbl2 = QLabel("Function Name:  (optional — auto-detected from def)")
        fn_name_lbl2.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        self._fn_name_input = QLineEdit()
        self._fn_name_input.setPlaceholderText("auto-detected from  def name():  …")
        self._fn_name_input.setStyleSheet(_INPUT)
        self._fn_name_input.setFixedWidth(260)
        fn_name_rl.addWidget(fn_name_lbl2)
        fn_name_rl.addWidget(self._fn_name_input)
        fn_name_rl.addStretch()
        fe_layout.addWidget(fn_name_row)

        self._fn_code_editor = QTextEdit()
        self._fn_code_editor.setPlaceholderText(
            "def check_chat():\n    if controller.ui.chat_window.isVisible():\n        print('true')\n    else:\n        print('false')"
        )
        self._fn_code_editor.setMinimumHeight(130)
        self._fn_code_editor.setTabStopDistance(28)
        self._fn_code_editor.setStyleSheet(
            f"QTextEdit {{ background: {_BG}; color: {_TEXT}; border: 1px solid {_BORDER};"
            f" border-radius: 6px; padding: 6px 8px; font-size: 12px;"
            f" font-family: 'Courier New', 'Consolas', monospace; }}"
            f"QTextEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        fe_layout.addWidget(self._fn_code_editor)
        self._fn_highlighter = _PythonHighlighter(self._fn_code_editor.document())

        def _auto_detect_fn_name():
            txt = self._fn_code_editor.toPlainText()
            m = re.search(r'^\s*def\s+([a-zA-Z_]\w*)\s*\(', txt, re.MULTILINE)
            if m and not self._fn_name_input.text().strip():
                self._fn_name_input.setText(m.group(1))

        self._fn_code_editor.textChanged.connect(_auto_detect_fn_name)

        fn_btn_row = QWidget()
        fn_btn_row.setStyleSheet("background: transparent;")
        fn_br = QHBoxLayout(fn_btn_row)
        fn_br.setContentsMargins(0, 0, 0, 0)
        fn_br.setSpacing(6)
        fn_save_btn = QPushButton("Save Function")
        fn_save_btn.setStyleSheet(_BTN_ACCENT)
        fn_save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fn_save_btn.clicked.connect(self._save_function_from_editor)
        fn_br.addWidget(fn_save_btn)
        fn_cancel_btn = QPushButton("Cancel")
        fn_cancel_btn.setStyleSheet(_BTN)
        fn_cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fn_cancel_btn.clicked.connect(lambda: (
            self._fn_editor_panel.setVisible(False),
            self._fn_name_input.clear(),
            self._fn_code_editor.clear(),
        ))
        fn_br.addWidget(fn_cancel_btn)
        fn_br.addStretch()
        fe_layout.addWidget(fn_btn_row)

        self._fn_error_lbl = QLabel("")
        self._fn_error_lbl.setStyleSheet(
            f"color: {_RED}; font-size: 11px; background: transparent; padding: 2px 0px;"
        )
        self._fn_error_lbl.setWordWrap(True)
        self._fn_error_lbl.setVisible(False)
        fe_layout.addWidget(self._fn_error_lbl)

        add_fn_btn.clicked.connect(lambda: (
            self._fn_editor_panel.setVisible(True),
            self._fn_name_input.setFocus(),
        ))

        fl.addWidget(_card(
            fn_hint_lbl,
            self._fn_list_widget,
            add_fn_btn,
            self._fn_editor_panel,
            title="Function Library", icon="🧰",
        ))

        # ══ CARD 3: Schedule ══════════════════════════════════════════════════
        self._f_whole_day = QCheckBox("Whole Day  (12:00 AM – 11:59 PM)")
        self._f_whole_day.setStyleSheet(_CHECK)

        time_row = QWidget()
        time_row.setStyleSheet("background: transparent;")
        tr = QHBoxLayout(time_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(10)
        tr.addWidget(QLabel("Start"))
        self._f_start = QTimeEdit()
        self._f_start.setDisplayFormat("hh:mm AP")
        self._f_start.setStyleSheet(_INPUT)
        self._f_start.setFixedWidth(115)
        tr.addWidget(self._f_start)
        tr.addWidget(QLabel("  End"))
        self._f_end = QTimeEdit()
        self._f_end.setDisplayFormat("hh:mm AP")
        self._f_end.setStyleSheet(_INPUT)
        self._f_end.setFixedWidth(115)
        self._f_end.setTime(QTime(21, 0))
        tr.addWidget(self._f_end)
        tr.addStretch()

        self._f_whole_day.toggled.connect(time_row.setDisabled)

        # Interval
        self._f_interval = QSpinBox()
        self._f_interval.setRange(1, 1440)
        self._f_interval.setValue(30)
        self._f_interval.setSuffix("  minutes")
        self._f_interval.setStyleSheet(_INPUT)

        # ── Ping mode: Startup Relative vs Schedule Relative ──────────────────
        ping_mode_row = QWidget()
        ping_mode_row.setStyleSheet("background: transparent;")
        pmr = QHBoxLayout(ping_mode_row)
        pmr.setContentsMargins(0, 0, 0, 0)
        pmr.setSpacing(20)

        _RADIO = (
            f"QRadioButton {{ color: {_TEXT}; font-size: 12px; spacing: 6px; }}"
            f"QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px;"
            f" border: 1px solid {_BORDER}; background: {_SURFACE2}; }}"
            f"QRadioButton::indicator:checked {{ background: {_ACCENT}; border-color: {_ACCENT}; }}"
        )

        self._f_mode_startup = QRadioButton("Startup Relative")
        self._f_mode_startup.setChecked(True)
        self._f_mode_startup.setStyleSheet(_RADIO)
        self._f_mode_startup.setToolTip(
            "Interval counts down from when the app/thread starts.\n"
            "Example: app/thread opens at 4:31 PM, interval 30 min → next ping at 5:01 PM."
        )

        self._f_mode_schedule = QRadioButton("Schedule Relative")
        self._f_mode_schedule.setStyleSheet(_RADIO)
        self._f_mode_schedule.setToolTip(
            "Interval aligns to the schedule window's start time.\n"
            "Example: window is 4:00 AM – 7:00 PM, interval 2 h → pings at 4:00, 6:00, 8:00 … 18:00.\n"
            "If you open the app at 4:31 PM, the next aligned ping is 6:00 PM — not 6:31 PM."
        )

        pmr.addWidget(self._f_mode_startup)
        pmr.addWidget(self._f_mode_schedule)
        pmr.addStretch()

        self._ping_times_panel = QWidget()
        self._ping_times_panel.setStyleSheet(
            f"background: {_BG}; border-radius: 6px; border: 1px solid {_BORDER};"
        )
        self._ping_times_panel.setVisible(False)
        ptl = QVBoxLayout(self._ping_times_panel)
        ptl.setContentsMargins(12, 10, 12, 10)
        ptl.setSpacing(6)
        self._ping_hint_lbl = QLabel("⏰  Add ping times within your active schedule window.")
        self._ping_hint_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        self._ping_hint_lbl.setWordWrap(True)
        ptl.addWidget(self._ping_hint_lbl)
        add_row = QWidget()
        add_row.setStyleSheet("background: transparent;")
        ar = QHBoxLayout(add_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(8)
        self._f_ping_time_picker = QTimeEdit()
        self._f_ping_time_picker.setDisplayFormat("hh:mm AP")
        self._f_ping_time_picker.setStyleSheet(_INPUT)
        self._f_ping_time_picker.setFixedWidth(115)
        ar.addWidget(self._f_ping_time_picker)
        add_ping_btn = QPushButton("＋ Add Time")
        add_ping_btn.setStyleSheet(_BTN_ACCENT)
        add_ping_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_ping_btn.clicked.connect(self._add_ping_time)
        ar.addWidget(add_ping_btn)
        ar.addStretch()
        ptl.addWidget(add_row)
        self._ping_times_list_layout = QVBoxLayout()
        self._ping_times_list_layout.setSpacing(4)
        ptl.addLayout(self._ping_times_list_layout)

        # ── Card 3: Daily Session Schedule ────────────────────────────────────
        schedule_hint = QLabel(
            "Sets the daily active window for this task. "
            "A fresh session starts when the window opens each day."
        )
        schedule_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        schedule_hint.setWordWrap(True)

        self._daily_schedule_card = _card(
            schedule_hint,
            self._f_whole_day,
            time_row,
            title="Daily Session Schedule", icon="🗓️",
        )
        fl.addWidget(self._daily_schedule_card)


        _SEG_ON = (
            f"QPushButton {{ background: {_ACCENT}; color: #0D1117; border: 1px solid {_ACCENT};"
            f" border-radius: 5px; padding: 4px 14px; font-size: 11px; font-weight: 600; }}"
        )
        _SEG_OFF = (
            f"QPushButton {{ background: transparent; color: {_MUTED}; border: 1px solid {_BORDER};"
            f" border-radius: 5px; padding: 4px 14px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {_TEXT}; border-color: {_ACCENT}; }}"
        )

        seg_row = QWidget()
        seg_row.setStyleSheet("background: transparent;")
        seg_rl = QHBoxLayout(seg_row)
        seg_rl.setContentsMargins(0, 0, 0, 0)
        seg_rl.setSpacing(4)
        self._seg_timed_btn    = QPushButton("Timed Interval")
        self._seg_specific_btn = QPushButton("Specific Ping Times")
        self._seg_script_btn   = QPushButton("Script Trigger")
        for _b in (self._seg_timed_btn, self._seg_specific_btn, self._seg_script_btn):
            _b.setCheckable(True)
            _b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._seg_timed_btn.setChecked(True)
        self._seg_timed_btn.setStyleSheet(_SEG_ON)
        self._seg_specific_btn.setStyleSheet(_SEG_OFF)
        self._seg_script_btn.setStyleSheet(_SEG_OFF)
        seg_rl.addWidget(self._seg_timed_btn)
        seg_rl.addWidget(self._seg_specific_btn)
        seg_rl.addWidget(self._seg_script_btn)
        seg_rl.addStretch()

        # ── Timed interval sub-section ────────────────────────────────────────
        self._timed_interval_section = QWidget()
        self._timed_interval_section.setStyleSheet("background: transparent;")
        tis_vl = QVBoxLayout(self._timed_interval_section)
        tis_vl.setContentsMargins(0, 0, 0, 0)
        tis_vl.setSpacing(6)
        timer_behavior_lbl = QLabel("TIMER BEHAVIOR")
        timer_behavior_lbl.setStyleSheet(_SEC)
        timer_inline_row = QWidget()
        timer_inline_row.setStyleSheet("background: transparent;")
        tir_hl = QHBoxLayout(timer_inline_row)
        tir_hl.setContentsMargins(0, 0, 0, 0)
        tir_hl.setSpacing(12)
        self._f_interval.setFixedWidth(150)
        tir_hl.addWidget(self._f_interval)
        tir_hl.addWidget(self._f_mode_startup)
        tir_hl.addWidget(self._f_mode_schedule)
        tir_hl.addStretch()
        tis_vl.addWidget(timer_behavior_lbl)
        tis_vl.addWidget(timer_inline_row)

        # ── Script Trigger sub-section ────────────────────────────────────────
        self._script_trigger_section = QWidget()
        self._script_trigger_section.setStyleSheet("background: transparent;")
        self._script_trigger_section.setVisible(False)
        sts_vl = QVBoxLayout(self._script_trigger_section)
        sts_vl.setContentsMargins(0, 0, 0, 0)
        sts_vl.setSpacing(8)

        # Script library header row (label + Open Folder + Refresh)
        _sl_hdr = QWidget()
        _sl_hdr.setStyleSheet("background: transparent;")
        _sl_hdr_hl = QHBoxLayout(_sl_hdr)
        _sl_hdr_hl.setContentsMargins(0, 0, 0, 0)
        _sl_hdr_hl.setSpacing(6)
        _sl_lbl = QLabel("SCRIPT LIBRARY")
        _sl_lbl.setStyleSheet(_SEC)
        _sl_hdr_hl.addWidget(_sl_lbl)
        _sl_hdr_hl.addStretch()
        _sl_open_btn = QPushButton("📂  Open Script Folder")
        _sl_open_btn.setStyleSheet(_BTN)
        _sl_open_btn.setFixedHeight(24)
        _sl_open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _sl_open_btn.clicked.connect(self._open_scripts_folder)
        _sl_hdr_hl.addWidget(_sl_open_btn)
        _sl_refresh_btn = QPushButton("↺")
        _sl_refresh_btn.setFixedSize(50, 50)
        _sl_refresh_btn.setStyleSheet(_BTN)
        _sl_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _sl_refresh_btn.setToolTip("Refresh script list")
        _sl_refresh_btn.clicked.connect(self._refresh_script_library)
        _sl_hdr_hl.addWidget(_sl_refresh_btn)
        sts_vl.addWidget(_sl_hdr)

        _sl_hint = QLabel(
            "Each script must define  fire_ping() → bool.  "
            "Click a card to select it.  The library auto-refreshes while this mode is active."
        )
        _sl_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        _sl_hint.setWordWrap(True)
        sts_vl.addWidget(_sl_hint)

        # Script cards container
        self._script_lib_container = QWidget()
        self._script_lib_container.setStyleSheet("background: transparent;")
        self._script_lib_layout = QVBoxLayout(self._script_lib_container)
        self._script_lib_layout.setContentsMargins(0, 0, 0, 0)
        self._script_lib_layout.setSpacing(4)
        sts_vl.addWidget(self._script_lib_container)

        # Poll Rate row
        _pr_row = QWidget()
        _pr_row.setStyleSheet("background: transparent;")
        _pr_hl = QHBoxLayout(_pr_row)
        _pr_hl.setContentsMargins(0, 4, 0, 0)
        _pr_hl.setSpacing(8)
        _pr_lbl = QLabel("POLL RATE")
        _pr_lbl.setStyleSheet(_SEC)
        _pr_hl.addWidget(_pr_lbl)
        self._f_script_poll_ms = QSpinBox()
        self._f_script_poll_ms.setRange(100, 60000)
        self._f_script_poll_ms.setValue(1000)
        self._f_script_poll_ms.setSuffix("  ms")
        self._f_script_poll_ms.setFixedWidth(140)
        self._f_script_poll_ms.setStyleSheet(_INPUT)
        self._f_script_poll_ms.setToolTip(
            "How often fire_ping() is called, in milliseconds.\n"
            "Lower = faster reaction; higher = less CPU use.\n"
            "Minimum: 100 ms."
        )
        _pr_hl.addWidget(self._f_script_poll_ms)
        _pr_desc = QLabel("— how often fire_ping() is called")
        _pr_desc.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        _pr_hl.addWidget(_pr_desc)
        _pr_hl.addStretch()
        sts_vl.addWidget(_pr_row)

        # How to use button
        _howto_btn = QPushButton("?  How to use")
        _howto_btn.setStyleSheet(_BTN)
        _howto_btn.setFixedHeight(26)
        _howto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _howto_btn.clicked.connect(self._show_script_trigger_help)
        sts_vl.addWidget(_howto_btn)

        # Auto-refresh timer (runs only while Script Trigger mode is visible)
        self._script_lib_refresh_timer = QTimer(self)
        self._script_lib_refresh_timer.setInterval(2500)
        self._script_lib_refresh_timer.timeout.connect(self._refresh_script_library)

        # ── Segment select handler — three modes ──────────────────────────────
        def _seg_select_mode(mode: str):
            """mode: 'timed' | 'specific_times' | 'script_trigger'"""
            for _b, _m in (
                (self._seg_timed_btn,    'timed'),
                (self._seg_specific_btn, 'specific_times'),
                (self._seg_script_btn,   'script_trigger'),
            ):
                _b.blockSignals(True)
                _b.setChecked(_m == mode)
                _b.setStyleSheet(_SEG_ON if _m == mode else _SEG_OFF)
                _b.blockSignals(False)

            self._timed_interval_section.setVisible(mode == 'timed')
            self._ping_times_panel.setVisible(mode == 'specific_times')
            self._script_trigger_section.setVisible(mode == 'script_trigger')

            if mode == 'script_trigger':
                self._refresh_script_library()
                self._script_lib_refresh_timer.start()
            else:
                self._script_lib_refresh_timer.stop()

        self._seg_timed_btn.clicked.connect(lambda: _seg_select_mode('timed'))
        self._seg_specific_btn.clicked.connect(lambda: _seg_select_mode('specific_times'))
        self._seg_script_btn.clicked.connect(lambda: _seg_select_mode('script_trigger'))

        # Legacy compatibility shims (still called by _new_task, _edit_task)
        self._is_specific_ping  = lambda: self._seg_specific_btn.isChecked()
        self._set_specific_ping = lambda use_s: _seg_select_mode('specific_times' if use_s else 'timed')
        self._get_ping_interval_mode_ui = lambda: (
            'script_trigger' if self._seg_script_btn.isChecked() else
            'specific_times' if self._seg_specific_btn.isChecked() else
            'timed'
        )
        self._set_ping_interval_mode_ui = _seg_select_mode

        fl.addWidget(_card(
            seg_row,
            self._timed_interval_section,
            self._ping_times_panel,
            self._script_trigger_section,
            title="Ping Interval", icon="⏱️",
        ))

        # ══ CARD 3b: One-Time Schedule ════════════════════════════════════════
        one_time_hint = QLabel(
            "Fire once at a specific date and time. Add backup times in case the primary is missed — "
            "whichever time fires and completes first will immediately deactivate this task. "
            "All earlier times that were already past are skipped automatically."
        )
        one_time_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        one_time_hint.setWordWrap(True)

        self._f_one_time_enabled = QCheckBox("Use One-Time Schedule  (overrides Daily Schedule above)")
        self._f_one_time_enabled.setStyleSheet(_CHECK)

        self._one_time_dt_panel = QWidget()
        self._one_time_dt_panel.setStyleSheet(
            f"background: {_BG}; border-radius: 6px; border: 1px solid {_BORDER};"
        )
        self._one_time_dt_panel.setVisible(False)
        ot_vl = QVBoxLayout(self._one_time_dt_panel)
        ot_vl.setContentsMargins(12, 10, 12, 10)
        ot_vl.setSpacing(6)

        self._one_time_status_lbl = QLabel("⚠  Any completed ping immediately deactivates this task.")
        self._one_time_status_lbl.setStyleSheet(
            "color: #FEBC2E; font-size: 10px; background: transparent;"
        )
        self._one_time_status_lbl.setWordWrap(True)
        ot_vl.addWidget(self._one_time_status_lbl)

        add_ot_row = QWidget()
        add_ot_row.setStyleSheet("background: transparent;")
        aot = QHBoxLayout(add_ot_row)
        aot.setContentsMargins(0, 0, 0, 0)
        aot.setSpacing(8)
        self._one_time_dt_picker = QDateTimeEdit()
        self._one_time_dt_picker.setDisplayFormat("MM/dd/yyyy  hh:mm AP")
        self._one_time_dt_picker.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self._one_time_dt_picker.setCalendarPopup(True)
        self._one_time_dt_picker.setStyleSheet(_INPUT)
        aot.addWidget(self._one_time_dt_picker)
        add_ot_btn = QPushButton("＋ Add Time")
        add_ot_btn.setStyleSheet(_BTN_ACCENT)
        add_ot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_ot_btn.clicked.connect(self._add_one_time_dt)
        aot.addWidget(add_ot_btn)
        aot.addStretch()
        ot_vl.addWidget(add_ot_row)

        self._one_time_dt_list_layout = QVBoxLayout()
        self._one_time_dt_list_layout.setSpacing(4)
        ot_vl.addLayout(self._one_time_dt_list_layout)

        self._f_one_time_enabled.toggled.connect(self._one_time_dt_panel.setVisible)
        self._f_one_time_enabled.toggled.connect(
            lambda on: self._daily_schedule_card.setEnabled(not on)
        )

        fl.addWidget(_card(
            one_time_hint,
            self._f_one_time_enabled,
            self._one_time_dt_panel,
            title="One-Time Schedule", icon="📌",
        ))

        # ══ CARD 4: Permissions ═══════════════════════════════════════════════
        perm_hint = QLabel("Controls what the agent is allowed to do during each ping.")
        perm_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")

        self._f_perm_workmode = QCheckBox("Allow Python Interpreter  (run code & see output; enables the file tools)")
        self._f_perm_image_tools = QCheckBox("Inject Image Tools on the system prompt")
        self._f_perm_controller_ref = QCheckBox("Inject Controller Reference on the system prompt")
        self._f_perm_notify_tool = QCheckBox("Inject Notify Tool on the system prompt")

        # ── Work iterations row (sits under the workmode checkbox) ────────────
        _iter_row = QWidget()
        _iter_row.setStyleSheet("background: transparent;")
        _ir = QHBoxLayout(_iter_row)
        _ir.setContentsMargins(22, 0, 0, 0)   # indent to align under checkbox text
        _ir.setSpacing(8)
        _iter_lbl = QLabel("Max iterations:")
        _iter_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        _ir.addWidget(_iter_lbl)
        self._f_max_iterations = QSpinBox()
        self._f_max_iterations.setRange(1, 9999)
        self._f_max_iterations.setValue(20)
        self._f_max_iterations.setFixedWidth(72)
        self._f_max_iterations.setStyleSheet(_INPUT)
        _ir.addWidget(self._f_max_iterations)
        self._f_unlimited_iterations = QCheckBox("Unlimited")
        self._f_unlimited_iterations.setStyleSheet(_CHECK)
        self._f_unlimited_iterations.setToolTip(
            "Remove the iteration cap entirely.\n"
            "⚠  Only enable this if your task instruction has a clear exit condition,\n"
            "otherwise the ping loop may run indefinitely."
        )
        self._f_unlimited_iterations.toggled.connect(
            lambda on: self._f_max_iterations.setDisabled(on)
        )
        _ir.addWidget(self._f_unlimited_iterations)
        _ir.addStretch()
        # disable the row when the interpreter is not allowed
        self._f_perm_workmode.toggled.connect(_iter_row.setEnabled)
        _iter_row.setEnabled(False)   # starts disabled until the interpreter is checked
        # ─────────────────────────────────────────────────────────────────────

        self._f_perm_workmode.setToolTip(
            "Lets this background task use the python_interpreter tool (and the file\n"
            "tools) to write and run code and observe its output.\n\n"
            "What the code may DO is governed by the per-category security policy\n"
            "below — background tasks never show an approval dialog, so each category\n"
            "is simply allowed or denied."
        )
        self._f_perm_image_tools.setToolTip(
            "Injects the image tool instructions into this task session's system prompt.\n"
            "Only affects this task — does not change the main AI engine or other tasks."
        )
        self._f_perm_controller_ref.setToolTip(
            "Injects the controller reference instructions into this task session's system prompt.\n"
            "Only affects this task — does not change the main AI engine or other tasks."
        )
        self._f_perm_notify_tool.setToolTip(
            "Injects the notify tool instructions into this task session's system prompt.\n"
            "Only affects this task — does not change the main AI engine or other tasks."
        )

        for cb in (self._f_perm_workmode,
                   self._f_perm_image_tools,
                   self._f_perm_controller_ref, self._f_perm_notify_tool):
            cb.setStyleSheet(_CHECK)

        # ── Per-category security policy grid (allow/deny only) ───────────────
        _policy_hdr = QLabel("Security policy")
        _policy_hdr.setStyleSheet(_SEC + " margin-top:4px;")
        _policy_note = QLabel(
            "A safety net modeled on AI-agent harnesses. Every file, process, "
            "network, dynamic-code, system and credential operation is sorted into "
            "a category below. A background task can't answer a prompt, so each "
            "category is simply ALLOWED (runs) or DENIED (blocked — the agent is "
            "told why and asked to find another way, and you're alerted in the main "
            "chat). Pick a preset to start, or fine-tune each row.")
        _policy_note.setWordWrap(True)
        _policy_note.setStyleSheet(
            f"color:{_MUTED}; font-size:10px; background:{_SURFACE2}; "
            f"border:1px solid {_BORDER}; border-radius:6px; padding:10px;")
        self._f_policy_box = self._build_task_policy_grid()
        self._f_perm_workmode.toggled.connect(self._f_policy_box.setEnabled)
        self._f_policy_box.setEnabled(False)

        fl.addWidget(_card(perm_hint,
                           self._f_perm_workmode, _iter_row,
                           _policy_hdr, _policy_note, self._f_policy_box,
                           title="Agent Permissions", icon="🔐"))

        # ══ CARD 4b: Optional Tools (prompt injections) ═══════════════════════
        _opt_hint = QLabel("Extra tool instructions injected into THIS task's system "
                           "prompt only. Off by default to keep the prompt lean.")
        _opt_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        _opt_hint.setWordWrap(True)
        fl.addWidget(_card(_opt_hint,
                           self._f_perm_image_tools,
                           self._f_perm_controller_ref, self._f_perm_notify_tool,
                           title="Optional Tools", icon="🧩"))

        # ══ CARD 5: Pre-loaded Skills ══════════════════════════════════════════
        skills_hint = QLabel(
            "These skills will be injected into the agent's system prompt automatically "
            "at the start of every ping session — no load_skill call needed."
        )
        skills_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        skills_hint.setWordWrap(True)

        # Expandable skill checklist
        self._skill_toggle_btn = QPushButton("▶  Select Skills  (0 selected)")
        self._skill_toggle_btn.setStyleSheet(_BTN)
        self._skill_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self._skill_checklist_panel = QWidget()
        self._skill_checklist_panel.setStyleSheet(
            f"background: {_BG}; border-radius: 6px; border: 1px solid {_BORDER};"
        )
        self._skill_checklist_panel.setVisible(False)
        scl = QVBoxLayout(self._skill_checklist_panel)
        scl.setContentsMargins(12, 8, 12, 8)
        scl.setSpacing(4)
        self._skill_checklist_layout = scl
        self._skill_checkboxes: dict = {}   # skill_name → QCheckBox

        def _toggle_skill_panel():
            visible = not self._skill_checklist_panel.isVisible()
            self._skill_checklist_panel.setVisible(visible)
            self._skill_toggle_btn.setText(
                ("▼" if visible else "▶") +
                f"  Select Skills  ({self._count_checked_skills()} selected)"
            )
        self._skill_toggle_btn.clicked.connect(_toggle_skill_panel)

        fl.addWidget(_card(skills_hint, self._skill_toggle_btn, self._skill_checklist_panel,
                           title="Pre-loaded Skills", icon="🧩"))

        # ══ CARD 6: Context / Memory ══════════════════════════════════════════
        limit_hint = QLabel("Cap how many prior messages are fed back to the AI each ping to save tokens.")
        limit_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        limit_hint.setWordWrap(True)

        self._f_limit_enabled = QCheckBox("Enable message limit")
        self._f_limit_enabled.setStyleSheet(_CHECK)

        limit_row = QWidget()
        limit_row.setStyleSheet("background: transparent;")
        lr = QHBoxLayout(limit_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(8)
        lr.addWidget(QLabel("Max messages:"))
        self._f_limit_max = QSpinBox()
        self._f_limit_max.setRange(1, 999)
        self._f_limit_max.setValue(5)
        self._f_limit_max.setFixedWidth(80)
        self._f_limit_max.setStyleSheet(_INPUT)
        lr.addWidget(self._f_limit_max)
        lr.addStretch()
        limit_row.setEnabled(False)
        self._f_limit_enabled.toggled.connect(limit_row.setEnabled)

        fl.addWidget(_card(limit_hint, self._f_limit_enabled, limit_row,
                           title="Session Context Limit", icon="🧠"))

        fl.addStretch()
        scroll.setWidget(form)
        outer.addWidget(scroll, stretch=1)
        return page

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE 2 — Session Viewer
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_viewer_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet(f"background: {_BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # Toolbar
        bar = QWidget()
        bar.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {_SURFACE2}, stop:1 {_SURFACE});"
            f"border-bottom: 1px solid {_BORDER};"
        )
        bar.setFixedHeight(52)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)
        bl.setSpacing(10)

        back_btn = QPushButton("← Back")
        back_btn.setStyleSheet(_BTN)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(self._close_viewer)
        bl.addWidget(back_btn)

        self._viewer_title = QLabel("Session")
        self._viewer_title.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; font-weight: 600; padding-left: 8px;"
        )
        bl.addWidget(self._viewer_title, stretch=1)

        # Live indicator dot
        self._viewer_live_dot = QLabel("● LIVE")
        self._viewer_live_dot.setStyleSheet(
            f"color: {_GREEN}; font-size: 10px; font-weight: 700; background: transparent;"
        )
        self._viewer_live_dot.setVisible(False)
        bl.addWidget(self._viewer_live_dot)

        # Blink timer for live dot
        self._viewer_blink_timer = QTimer(self)
        self._viewer_blink_timer.setInterval(900)
        self._viewer_blink_state = True
        def _blink():
            self._viewer_blink_state = not self._viewer_blink_state
            self._viewer_live_dot.setStyleSheet(
                f"color: {'#34D058' if self._viewer_blink_state else '#1A4028'}; "
                f"font-size: 10px; font-weight: 700; background: transparent;"
            )
        self._viewer_blink_timer.timeout.connect(_blink)

        vl.addWidget(bar)

        # Read-only chat area
        self._viewer_area = QScrollArea()
        self._viewer_area.setWidgetResizable(True)
        self._viewer_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._viewer_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._viewer_body = QWidget()
        self._viewer_body.setStyleSheet(f"background: {_BG};")
        self._viewer_body_layout = QVBoxLayout(self._viewer_body)
        self._viewer_body_layout.setContentsMargins(22, 18, 22, 18)
        self._viewer_body_layout.setSpacing(10)
        self._viewer_body_layout.addStretch()
        self._viewer_area.setWidget(self._viewer_body)
        vl.addWidget(self._viewer_area, stretch=1)

        # Live refresh timer (fires every 2.5s while viewer is open)
        self._viewer_refresh_timer = QTimer(self)
        self._viewer_refresh_timer.setInterval(2500)
        self._viewer_refresh_timer.timeout.connect(self._refresh_viewer_tick)

        return page

    def _view_session(self, task_id: str, date_str: str):
        if self._task_mgr is None:
            return
        session = self._task_mgr.load_task_session(task_id, date_str)
        if session is None:
            return

        self._viewer_task_id = task_id
        self._viewer_date_str = date_str
        self._viewer_pending = None   # cleared during render below

        history = session.get('chat_history', [])
        ongoing = bool(session.get('ongoing', False))
        self._viewer_msg_count = len(history)
        self._viewer_ongoing = ongoing
        task_name = session.get('session_name', '')
        self._viewer_title.setText(f"{task_name}  ·  {date_str}")

        self._render_session_bubbles(history)   # clears + full render
        self._update_viewer_pending(history, ongoing)

        # Show live indicator and start timers
        self._viewer_live_dot.setVisible(True)
        self._viewer_blink_timer.start()
        self._viewer_refresh_timer.start()

        self._stack.setCurrentIndex(2)
        # Scroll to bottom after render
        QTimer.singleShot(80, lambda: self._viewer_area.verticalScrollBar().setValue(
            self._viewer_area.verticalScrollBar().maximum()
        ))

    def _add_text_bubble(self, role_icon: str, role_text: str, role_color: str,
                         content: str, kind: str):
        """Add a simple role-labelled text bubble to the viewer."""
        layout = self._viewer_body_layout
        bubble = QFrame()
        bubble.setObjectName("viewerBubble")
        if kind == 'agent':
            border = _ACCENT
            bg = _SURFACE2
            bstyle = f"border: 1px solid rgba(79,158,248,0.25); border-left: 3px solid {_ACCENT};"
        elif kind == 'ping':
            border = _YELLOW
            bg = _SURFACE
            bstyle = f"border: 1px solid {_BORDER}; border-left: 3px solid {_YELLOW};"
        else:
            border = _PURPLE
            bg = _SURFACE
            bstyle = f"border: 1px solid {_BORDER}; border-left: 3px solid {_PURPLE};"
        bubble.setStyleSheet(f"QFrame#viewerBubble {{ background: {bg}; {bstyle} border-radius: 0 10px 10px 0; }}")
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(6)
        role_lbl = QLabel(f"{role_icon}  {role_text}")
        role_lbl.setStyleSheet(
            f"color: {role_color}; font-size: 10px; font-weight: 700; "
            f"background: transparent; border: none; letter-spacing: 0.5px;"
        )
        bl.addWidget(role_lbl)
        text_lbl = QLabel(content)
        text_lbl.setWordWrap(True)
        text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_lbl.setStyleSheet(
            f"color: {_TEXT}; font-size: 12px; line-height: 1.5; background: transparent; border: none;"
        )
        bl.addWidget(text_lbl)
        layout.insertWidget(layout.count() - 1, bubble)

    def _add_exec_widget(self, code: str, output: str, annotation: str):
        """Add a collapsible code+output execution block (like the main chat's UI events)."""
        layout = self._viewer_body_layout
        frame = QFrame()
        frame.setObjectName("viewerExec")
        frame.setStyleSheet(
            f"QFrame#viewerExec {{ background: #0D1117; border: 1px solid {_BORDER}; border-radius: 8px; }}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.setSpacing(0)

        # Header row: label + toggle
        header = QFrame()
        header.setStyleSheet(f"background: {_SURFACE}; border: none; border-radius: 8px 8px 0 0;")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 7, 10, 7)
        title = QLabel(annotation.strip() if annotation.strip() else "Executed code")
        title.setStyleSheet(
            f"color: {_ACCENT}; font-size: 10px; font-weight: 700; letter-spacing: 0.5px; "
            f"background: transparent; border: none;"
        )
        hl.addWidget(title)
        hl.addStretch()
        toggle = QPushButton("Show")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet(
            f"QPushButton {{ color: {_MUTED}; background: {_SURFACE2}; border: 1px solid {_BORDER}; "
            f"border-radius: 5px; padding: 2px 10px; font-size: 10px; }}"
            f"QPushButton:hover {{ color: {_ACCENT}; border-color: {_ACCENT}; }}"
        )
        hl.addWidget(toggle)
        fl.addWidget(header)

        # Collapsible body: code + output
        body = QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(0, 0, 0, 0)
        body_l.setSpacing(0)

        def _mono_block(text, color, bg, header_text, header_color):
            wrap = QFrame()
            wrap.setStyleSheet(f"background: {bg}; border: none;")
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(12, 8, 12, 8)
            wl.setSpacing(4)
            hdr = QLabel(header_text)
            hdr.setStyleSheet(f"color: {header_color}; font-size: 9px; font-weight: 700; "
                              f"letter-spacing: 0.5px; background: transparent; border: none;")
            wl.addWidget(hdr)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setStyleSheet(f"color: {color}; font-family: 'Cascadia Code','Consolas',monospace; "
                              f"font-size: 11px; background: transparent; border: none;")
            wl.addWidget(lbl)
            return wrap

        if code:
            body_l.addWidget(_mono_block(code, "#E6EDF3", "#0D1117", "CODE", _MUTED))
        if output:
            body_l.addWidget(_mono_block(output, "#8FBC8F", "#0A0F15", "OUTPUT", "#4CD780"))
        body.setVisible(False)
        fl.addWidget(body)

        def _toggle():
            vis = not body.isVisible()
            body.setVisible(vis)
            toggle.setText("Hide" if vis else "Show")
        toggle.clicked.connect(_toggle)

        layout.insertWidget(layout.count() - 1, frame)

    def _render_session_bubbles(self, history: list):
        """Render the viewer from full session history: ping bubbles, agent text, and
        collapsible code+output execution blocks (paired from work fences + outputs)."""
        # Clear existing bubbles (keep the trailing stretch at the end).
        layout = self._viewer_body_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        i = 0
        n = len(history)
        while i < n:
            msg = history[i]
            role = msg.get('role', '')
            content = msg.get('content', '') or ''

            if role == 'ui_event' and msg.get('_type') == 'work_exec':
                self._add_exec_widget(msg.get('_code', ''), msg.get('_output', ''), msg.get('_annotation', ''))
                i += 1
                continue

            if role == 'system':
                if msg.get('_is_work_prompt'):
                    # Standalone work-output feedback with no preceding code — show
                    # its output on its own (rare; normally paired below).
                    out = msg.get('_work_output', '')
                    if out:
                        self._add_exec_widget('', out, '')
                    i += 1
                    continue
                if content.strip():
                    self._add_text_bubble("⏰", "Task Ping", _YELLOW, content.strip(), 'ping')
                i += 1
                continue

            if role == 'assistant':
                visible, code, tool = _split_work_message(content)
                if tool:
                    # Reasoning text (if any), then a collapsible code+output block.
                    if visible:
                        self._add_text_bubble("●", "Agent", _ACCENT, visible, 'agent')
                    output = ''
                    if i + 1 < n and history[i + 1].get('role') == 'system' and history[i + 1].get('_is_work_prompt'):
                        output = history[i + 1].get('_work_output', '') or ''
                        i += 1  # consume the paired output message
                    self._add_exec_widget(code, output, tool.replace('_', ' '))
                else:
                    if content.strip():
                        self._add_text_bubble("●", "Agent Response", _ACCENT, content.strip(), 'agent')
                i += 1
                continue

            # Any other role with text
            if content.strip():
                self._add_text_bubble("•", role or "note", _MUTED, content.strip(), 'other')
            i += 1

    def _refresh_viewer_tick(self):
        """Called every 2.5s while session viewer is open. Re-renders if new messages appeared."""
        if self._task_mgr is None or not self._viewer_task_id or not self._viewer_date_str:
            return
        try:
            session = self._task_mgr.load_task_session(self._viewer_task_id, self._viewer_date_str)
            if session is None:
                return
            history = session.get('chat_history', [])
            ongoing = bool(session.get('ongoing', False))
            # Re-render on any change to the history length OR the ongoing flag.
            if len(history) == self._viewer_msg_count and ongoing == getattr(self, '_viewer_ongoing', False):
                return  # nothing changed
            self._viewer_msg_count = len(history)
            self._viewer_ongoing = ongoing
            self._render_session_bubbles(history)   # full re-render (handles code/output pairing)
            self._update_viewer_pending(history, ongoing)
            # Scroll to bottom
            QTimer.singleShot(60, lambda: self._viewer_area.verticalScrollBar().setValue(
                self._viewer_area.verticalScrollBar().maximum()
            ))
        except Exception:
            pass

    def _update_viewer_pending(self, history: list, ongoing: bool = False):
        """
        Show an '(Ongoing) Agent is working…' pill at the bottom while the ping is
        still running (session['ongoing']), or when a ping went out with no agent
        reply yet. Lets live pings read as 'thinking' instead of looking done.
        """
        # Tear down any existing pending pill.
        existing = getattr(self, '_viewer_pending', None)
        if existing is not None:
            existing.deleteLater()
            self._viewer_pending = None

        if not ongoing:
            # Fall back: awaiting an agent reply after a ping with no response yet.
            last_role = None
            for m in reversed(history):
                if m.get('content'):
                    last_role = m.get('role')
                    break
            if last_role not in ('system', 'user'):
                return

        pill = QFrame()
        pill.setObjectName("viewerPending")
        pill.setStyleSheet(f"""
            QFrame#viewerPending {{
                background: {_SURFACE2};
                border: 1px dashed rgba(79,158,248,0.45);
                border-radius: 10px;
            }}
        """)
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(14, 9, 14, 9)
        label_text = "● (Ongoing)  Agent is working…" if ongoing else "Agent is working…"
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color: {_ACCENT}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
        pl.addWidget(lbl)
        pl.addStretch()
        self._viewer_body_layout.insertWidget(self._viewer_body_layout.count() - 1, pill)
        self._viewer_pending = pill

    def _close_viewer(self):
        """Stop live-refresh timers and go back to the task list."""
        if self._viewer_refresh_timer:
            self._viewer_refresh_timer.stop()
        if hasattr(self, '_viewer_blink_timer') and self._viewer_blink_timer:
            self._viewer_blink_timer.stop()
        if hasattr(self, '_viewer_live_dot'):
            self._viewer_live_dot.setVisible(False)
        self._viewer_task_id = None
        self._viewer_date_str = None
        self._viewer_msg_count = 0
        self._viewer_ongoing = False
        self._stack.setCurrentIndex(0)

    # ═══════════════════════════════════════════════════════════════════════════
    # System Prompt Preview
    # ═══════════════════════════════════════════════════════════════════════════

    def _show_system_prompt_preview(self, task_dict: dict | None):
        """
        Build and display the effective system prompt for a task.
        task_dict=None → read permissions from the currently open editor form.
        task_dict=<saved task> → use that task's saved permissions.
        """
        perms = task_dict.get('permissions', {}) if task_dict else {}
        task_name = task_dict.get('name', '?') if task_dict else '?'
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
        try:
            full_prompt = self._controller.build_task_system_prompt(task_dict)
        except Exception as e:
            full_prompt = f"[Could not build preview: {e}]"
            task_name = "?"

        # ── Dialog ────────────────────────────────────────────────────────────
        dlg = QDialog(self)
        dlg.setWindowTitle("System Prompt Preview")
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.resize(680, 560)

        outer = QWidget(dlg)
        outer.setObjectName("spOuter")
        outer.setStyleSheet(f"""
            QWidget#spOuter {{
                background: {_SURFACE};
                border-radius: 12px;
                border: 1px solid {_BORDER};
            }}
        """)
        _dlg_vl = QVBoxLayout(dlg)
        _dlg_vl.setContentsMargins(0, 0, 0, 0)
        _dlg_vl.addWidget(outer)

        ol = QVBoxLayout(outer)
        ol.setContentsMargins(20, 16, 20, 16)
        ol.setSpacing(12)

        # Header
        _hl = QHBoxLayout()
        _title = QLabel(f"System Prompt Preview  ·  {task_name}")
        _title.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 700;")
        _hint = QLabel(f"{len(full_prompt):,} chars")
        _hint.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        _hl.addWidget(_title)
        _hl.addStretch()
        _hl.addWidget(_hint)
        ol.addLayout(_hl)

        # Text area
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(full_prompt)
        txt.setStyleSheet(f"""
            QTextEdit {{
                background: {_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 10px; font-size: 11px;
                font-family: 'Consolas', 'Courier New', monospace;
            }}
        """)
        ol.addWidget(txt, stretch=1)

        # Footer buttons
        _fl = QHBoxLayout()
        _copy_btn = QPushButton("📋  Copy")
        _copy_btn.setStyleSheet(_BTN)
        _copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        def _copy():
            QApplication.clipboard().setText(full_prompt)
            _copy_btn.setText("✅  Copied!")
            QTimer.singleShot(1800, lambda: _copy_btn.setText("📋  Copy"))
        _copy_btn.clicked.connect(_copy)
        _close_btn = QPushButton("Close")
        _close_btn.setStyleSheet(_BTN_ACCENT)
        _close_btn.setFixedWidth(80)
        _close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _close_btn.clicked.connect(dlg.accept)
        _fl.addWidget(_copy_btn)
        _fl.addStretch()
        _fl.addWidget(_close_btn)
        ol.addLayout(_fl)

        dlg.exec()

    def _open_manual_ping_dialog(self, task: dict):
        """
        Mini confirmation/editor for firing an immediate test ping.

        Pops a small window prefilled with the task's instruction. The user can
        tweak the prompt (or leave it) and hit Send — the ping fires right away,
        unscheduled, and we open that day's session viewer so the result streams
        in live. Handy when scripted pings render nothing and you just want to
        poke the AI.
        """
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel

        task_id = task['id']
        task_name = task.get('name', '?')

        dlg = QDialog(self)
        dlg.setWindowTitle("Manual Ping")
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.resize(560, 420)

        outer = QWidget(dlg)
        outer.setObjectName("mpOuter")
        outer.setStyleSheet(f"""
            QWidget#mpOuter {{
                background: {_SURFACE};
                border-radius: 12px;
                border: 1px solid {_BORDER};
            }}
        """)
        _dlg_vl = QVBoxLayout(dlg)
        _dlg_vl.setContentsMargins(0, 0, 0, 0)
        _dlg_vl.addWidget(outer)

        ol = QVBoxLayout(outer)
        ol.setContentsMargins(20, 16, 20, 16)
        ol.setSpacing(10)

        # Header
        _title = QLabel(f"⚡ Manual Ping  ·  {task_name}")
        _title.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 700;")
        ol.addWidget(_title)

        _sub = QLabel("Fires one immediate, unscheduled ping. Edit the prompt below "
                      "or send as-is to test the AI.")
        _sub.setWordWrap(True)
        _sub.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
        ol.addWidget(_sub)

        # Prompt editor (prefilled with the task instruction)
        editor = QTextEdit()
        editor.setPlainText(task.get('instruction', ''))
        editor.setPlaceholderText("Prompt to send to the AI for this test ping…")
        editor.setStyleSheet(f"""
            QTextEdit {{
                background: {_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 10px; font-size: 12px;
            }}
            QTextEdit:focus {{ border-color: {_ACCENT}; }}
        """)
        ol.addWidget(editor, stretch=1)

        # Footer
        _fl = QHBoxLayout()
        _cancel = QPushButton("Cancel")
        _cancel.setStyleSheet(_BTN_GHOST)
        _cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        _cancel.clicked.connect(dlg.reject)
        _send = QPushButton("⚡ Send Ping")
        _send.setStyleSheet(_BTN_ACCENT)
        _send.setCursor(Qt.CursorShape.PointingHandCursor)

        def _fire():
            text = editor.toPlainText().strip()
            override = text if text else None
            try:
                date_str = self._controller.task_manager.fire_manual_ping(task_id, override)
            except Exception as e:
                QMessageBox.warning(self, "Manual Ping Failed", str(e))
                return
            dlg.accept()
            if date_str:
                # Open the session viewer so the live result streams in.
                QTimer.singleShot(250, lambda: self._view_session(task_id, date_str))

        _send.clicked.connect(_fire)
        _fl.addStretch()
        _fl.addWidget(_cancel)
        _fl.addWidget(_send)
        ol.addLayout(_fl)

        dlg.exec()

    # ═══════════════════════════════════════════════════════════════════════════
    # Editor logic
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Skill checklist helpers ────────────────────────────────────────────────
    
    def _load_skill_content(self, skill_name: str) -> str | None:
        """
        Load the instruction content of a skill by name.
        Tries skill_manager API first, then falls back to common file paths on disk.
        Returns the skill instruction text, or None if not found.
        """
        # ── Try skill_manager API ─────────────────────────────────────────────
        if self._skill_manager is not None:
            fn = getattr(self._skill_manager, 'get_skill_content', None)
            if callable(fn):
                try:
                    content = fn(skill_name, "TaskThread._load_skill_content")
                    if content:
                        return str(content)
                except Exception:
                    pass

        log.warning(f"[TaskThread._load_skill_content] Skill '{skill_name}' content not found anywhere")
        return None

    def _get_available_skill_names(self) -> list:
        """Fetch available skill names from the skill manager."""
        try:
            sm = getattr(self._controller, 'skill_manager', None)
            if sm is None:
                return []
            skills = sm.get_skills()
            return sorted(s['name'] for s in (skills or []))
        except Exception:
            return []

    def _populate_skill_checklist(self, pre_checked: list = None):
        """
        Rebuild the skill checkbox list from available skills.
        pre_checked: list of skill names to mark as checked.
        """
        pre_checked = pre_checked or []
        # Clear existing checkboxes
        while self._skill_checklist_layout.count():
            item = self._skill_checklist_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._skill_checkboxes.clear()

        names = self._get_available_skill_names()
        if not names:
            lbl = QLabel("No skills found. Add skills to your skills directory.")
            lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px;")
            self._skill_checklist_layout.addWidget(lbl)
            return

        for name in names:
            cb = QCheckBox(name)
            cb.setChecked(name in pre_checked)
            cb.setStyleSheet(_CHECK)
            cb.toggled.connect(self._update_skill_toggle_label)
            self._skill_checklist_layout.addWidget(cb)
            self._skill_checkboxes[name] = cb

        self._update_skill_toggle_label()

    def _update_skill_toggle_label(self):
        n = self._count_checked_skills()
        visible = self._skill_checklist_panel.isVisible()
        self._skill_toggle_btn.setText(
            ("▼" if visible else "▶") + f"  Select Skills  ({n} selected)"
        )

    def _count_checked_skills(self) -> int:
        return sum(1 for cb in self._skill_checkboxes.values() if cb.isChecked())

    def _get_checked_skills(self) -> list:
        return [name for name, cb in self._skill_checkboxes.items() if cb.isChecked()]

    # ── Specific ping time helpers ─────────────────────────────────────────────

    def _add_ping_time(self):
        t = self._f_ping_time_picker.time()
        t_str = f"{t.hour():02d}:{t.minute():02d}"
        if t_str in self._get_current_ping_times():
            self._ping_hint_lbl.setText(f"⚠  {t_str} is already in the list.")
            return
        if not self._f_whole_day.isChecked():
            st = self._f_start.time()
            et = self._f_end.time()
            start_str = f"{st.hour():02d}:{st.minute():02d}"
            end_str = f"{et.hour():02d}:{et.minute():02d}"
            if not (start_str <= t_str <= end_str):
                self._ping_hint_lbl.setText(
                    f"⚠  {t_str} is outside your active window ({start_str} – {end_str}). Not added."
                )
                return
        self._ping_hint_lbl.setText("⏰  Pick times that fall inside your active schedule window, then click Add.")
        self._render_ping_time_row(t_str)

    def _get_current_ping_times(self) -> list:
        times = []
        for i in range(self._ping_times_list_layout.count()):
            item = self._ping_times_list_layout.itemAt(i)
            if item and item.widget():
                lbl = item.widget().findChild(QLabel)
                if lbl:
                    times.append(lbl.text())
        return times

    def _render_ping_time_row(self, t_str: str):
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        lbl = QLabel(t_str)
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; background: transparent;")
        rl.addWidget(lbl, stretch=1)
        rm_btn = QPushButton("✕  Remove")
        rm_btn.setStyleSheet(_BTN_RED)
        rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rm_btn.clicked.connect(row.deleteLater)
        rl.addWidget(rm_btn)
        self._ping_times_list_layout.addWidget(row)

    def _clear_ping_times_list(self):
        while self._ping_times_list_layout.count():
            item = self._ping_times_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_one_time_dt(self):
        dt = self._one_time_dt_picker.dateTime()
        dt_str = dt.toString("yyyy-MM-ddTHH:mm")
        if dt_str in self._get_one_time_datetimes():
            self._one_time_status_lbl.setText(
                f"⚠  {dt.toString('MM/dd/yyyy hh:mm AP')} is already in the list."
            )
            return
        self._one_time_status_lbl.setText("⚠  Any completed ping immediately deactivates this task.")
        self._render_one_time_dt_row(dt_str)

    def _get_one_time_datetimes(self) -> list:
        result = []
        for i in range(self._one_time_dt_list_layout.count()):
            item = self._one_time_dt_list_layout.itemAt(i)
            if item and item.widget():
                result.append(item.widget().objectName())
        return result

    def _collect_one_time_datetimes(self) -> list:
        """Return the one-time datetime list, auto-adding the picker value if the list is empty."""
        if not self._f_one_time_enabled.isChecked():
            return []
        dts = self._get_one_time_datetimes()
        if not dts:
            # User set the picker but forgot to click ＋ Add Time — grab it automatically
            dt_str = self._one_time_dt_picker.dateTime().toString("yyyy-MM-ddTHH:mm")
            dts = [dt_str]
        return sorted(dts)

    def _render_one_time_dt_row(self, dt_str: str):
        try:
            dt = QDateTime.fromString(dt_str, "yyyy-MM-ddTHH:mm")
            display = dt.toString("MM/dd/yyyy  hh:mm AP") if dt.isValid() else dt_str
        except Exception:
            display = dt_str
        row = QWidget()
        row.setObjectName(dt_str)   # stores ISO string for _get_one_time_datetimes()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        lbl = QLabel(display)
        lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; background: transparent;")
        rl.addWidget(lbl, stretch=1)
        rm_btn = QPushButton("✕")
        rm_btn.setFixedSize(24, 24)
        rm_btn.setStyleSheet(_BTN_RED)
        rm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rm_btn.clicked.connect(row.deleteLater)
        rl.addWidget(rm_btn)
        self._one_time_dt_list_layout.addWidget(row)

    def _clear_one_time_dt_list(self):
        while self._one_time_dt_list_layout.count():
            item = self._one_time_dt_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _new_task(self):
        self._editing_task_id = None
        self._editor_title.setText("New Task")
        self._f_name.clear()
        self._f_instruction.clear()
        self._f_active_editor.setChecked(True)
        self._f_interval.setValue(30)
        self._f_interval.setEnabled(True)
        self._f_whole_day.setChecked(False)
        self._f_start.setTime(QTime(0, 0))
        self._f_end.setTime(QTime(21, 0))
        self._f_perm_workmode.setChecked(False)
        self._f_perm_image_tools.setChecked(False)
        self._f_perm_controller_ref.setChecked(False)
        self._f_perm_notify_tool.setChecked(False)
        self._apply_task_policy(_default_task_policy())
        self._f_policy_box.setEnabled(False)
        self._f_max_iterations.setValue(20)
        self._f_max_iterations.setEnabled(True)
        self._f_unlimited_iterations.setChecked(False)
        self._f_mode_startup.setChecked(True)
        self._set_specific_ping(False)
        self._clear_ping_times_list()
        self._ping_hint_lbl.setText("⏰  Add ping times within your active schedule window.")
        self._populate_skill_checklist([])
        self._skill_checklist_panel.setVisible(False)
        self._update_skill_toggle_label()
        self._f_limit_enabled.setChecked(False)
        self._f_limit_max.setValue(5)
        self._f_one_time_enabled.setChecked(False)
        self._one_time_dt_panel.setVisible(False)
        self._clear_one_time_dt_list()
        self._one_time_dt_picker.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self._daily_schedule_card.setEnabled(True)
        self._selected_script_name = ''
        self._f_script_poll_ms.setValue(1000)
        self._refresh_functions_list()
        self._stack.setCurrentIndex(1)

    def _build_task_policy_grid(self):
        """Preset quick-pick + per-category allow/deny combos, grouped by
        CATEGORY_GROUPS — styled to match Settings ▸ Security ▸ Execution Policy
        (minus 'ask', which a background task can't answer)."""
        box = QWidget(); box.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(box); lay.setContentsMargins(0, 2, 0, 0); lay.setSpacing(6)

        # ── Preset quick-pick row ─────────────────────────────────────────────
        preset_row = QHBoxLayout(); preset_row.setSpacing(8)
        _pl = QLabel("Preset:")
        _pl.setStyleSheet(f"color:{_TEXT}; font-size:11px; font-weight:600;")
        preset_row.addWidget(_pl)
        self._f_policy_preset = QComboBox()
        self._f_policy_preset.setStyleSheet(_INPUT)
        for _name, _ in _task_policy_presets():
            self._f_policy_preset.addItem(_name)
        preset_row.addWidget(self._f_policy_preset, 1)
        _apply_btn = QPushButton("Apply")
        _apply_btn.setStyleSheet(_BTN)
        _apply_btn.setToolTip("Load the selected preset into the rows below.")
        _apply_btn.clicked.connect(self._apply_task_policy_preset)
        preset_row.addWidget(_apply_btn)
        lay.addLayout(preset_row)

        # ── Per-category rows, grouped so the finer list stays readable ───────
        self._f_policy_combos = {}
        for grp_title, grp_cats in _guard.CATEGORY_GROUPS:
            sub = QLabel(grp_title)
            sub.setStyleSheet(f"color:{_MUTED}; font-size:10px; font-weight:700;"
                              " letter-spacing:1px; margin-top:8px;")
            lay.addWidget(sub)
            for cat in grp_cats:
                row = QHBoxLayout()
                row.setContentsMargins(4, 0, 0, 0); row.setSpacing(10)
                lbl = QLabel(_guard.CATEGORY_LABELS.get(cat, cat))
                lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
                lbl.setWordWrap(True)
                row.addWidget(lbl, 1)
                combo = QComboBox(); combo.addItems(["allow", "deny"])
                combo.setStyleSheet(_INPUT); combo.setFixedWidth(120)
                row.addWidget(combo)
                self._f_policy_combos[cat] = combo
                lay.addLayout(row)
        return box

    def _apply_task_policy_preset(self):
        """Load the chosen built-in preset into the policy combos."""
        presets = dict(_task_policy_presets())
        name = self._f_policy_preset.currentText()
        if name in presets:
            self._apply_task_policy(presets[name])

    def _apply_task_policy(self, policy: dict):
        for cat, combo in getattr(self, '_f_policy_combos', {}).items():
            val = policy.get(cat, 'deny')
            idx = combo.findText(val if val in ('allow', 'deny') else 'deny')
            combo.setCurrentIndex(max(0, idx))

    def _read_task_policy(self) -> dict:
        return {cat: combo.currentText()
                for cat, combo in getattr(self, '_f_policy_combos', {}).items()}

    def _edit_task(self, task: dict):
        self._editing_task_id = task['id']
        self._editor_title.setText(f"Edit — {task.get('name', '')}")
        self._f_name.setText(task.get('name', ''))
        self._f_instruction.setPlainText(task.get('instruction', ''))
        self._f_active_editor.setChecked(task.get('active', True))

        self._f_interval.setValue(task.get('interval_minutes', 30))

        ping_mode = task.get('ping_mode', 'startup_relative')
        self._f_mode_schedule.setChecked(ping_mode == 'schedule_relative')
        self._f_mode_startup.setChecked(ping_mode != 'schedule_relative')

        use_specific = task.get('use_specific_ping_times', False)
        self._set_specific_ping(use_specific)
        self._clear_ping_times_list()
        self._ping_hint_lbl.setText("⏰  Add ping times within your active schedule window.")
        for t_str in task.get('specific_ping_times', []):
            self._render_ping_time_row(t_str)

        sched = task.get('daily_schedule', {})
        self._f_whole_day.setChecked(sched.get('whole_day', False))
        try:
            h, m = map(int, sched.get('start', '00:00').split(':'))
            self._f_start.setTime(QTime(h, m))
        except Exception:
            self._f_start.setTime(QTime(0, 0))
        try:
            h, m = map(int, sched.get('end', '21:00').split(':'))
            self._f_end.setTime(QTime(h, m))
        except Exception:
            self._f_end.setTime(QTime(21, 0))

        perms = task.get('permissions', {})
        _allow_interp = perms.get('allow_workmode', False)
        self._f_perm_workmode.setChecked(_allow_interp)
        self._f_perm_image_tools.setChecked(perms.get('inject_image_tools', False))
        self._f_perm_controller_ref.setChecked(perms.get('inject_controller_ref', False))
        self._f_perm_notify_tool.setChecked(perms.get('inject_notify_tool', False))
        # Per-category policy: use the saved grid, or migrate an old task's flags.
        self._apply_task_policy(_migrate_task_policy(perms))
        self._f_policy_box.setEnabled(_allow_interp)

        self._f_max_iterations.setValue(task.get('max_work_iterations', 20))
        unlimited = task.get('unlimited_work_iterations', False)
        self._f_unlimited_iterations.setChecked(unlimited)
        self._f_max_iterations.setEnabled(not unlimited)

        self._populate_skill_checklist(task.get('loaded_skills', []))
        self._skill_checklist_panel.setVisible(False)
        self._update_skill_toggle_label()

        limit = task.get('limit_session_messages', {})
        self._f_limit_enabled.setChecked(limit.get('enabled', False))
        self._f_limit_max.setValue(limit.get('max_messages', 5))

        # ── Ping interval mode (unambiguous restore) ──────────────────────────
        _pim = task.get('ping_interval_mode', '')
        if not _pim:
            _pim = 'specific_times' if task.get('use_specific_ping_times', False) else 'timed'
        self._set_ping_interval_mode_ui(_pim)
        self._selected_script_name = task.get('script_name', '')
        self._f_script_poll_ms.setValue(task.get('script_poll_ms', 1000))
        if _pim == 'script_trigger':
            self._refresh_script_library()

        one_time = task.get('one_time_schedule', {})
        one_time_on = one_time.get('enabled', False)
        self._f_one_time_enabled.setChecked(one_time_on)
        self._one_time_dt_panel.setVisible(one_time_on)
        self._daily_schedule_card.setEnabled(not one_time_on)
        self._clear_one_time_dt_list()
        for dt_str in one_time.get('datetimes', []):
            self._render_one_time_dt_row(dt_str)
        self._one_time_dt_picker.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self._refresh_functions_list()
        self._stack.setCurrentIndex(1)

    # ═══════════════════════════════════════════════════════════════════════════
    # FUNCTIONS LIBRARY HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    # ═══════════════════════════════════════════════════════════════════════════
    # SCRIPT TRIGGER HELPERS
    # ═══════════════════════════════════════════════════════════════════════════

    def _refresh_script_library(self):
        """Rebuild the script cards in the Script Trigger library panel."""
        if not hasattr(self, '_script_lib_layout'):
            return

        while self._script_lib_layout.count():
            item = self._script_lib_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._task_mgr is None:
            return

        scripts = self._task_mgr.get_script_names()

        if not scripts:
            _empty = QLabel("No scripts found.  Click 📂 Open Script Folder to add one.")
            _empty.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
            _empty.setWordWrap(True)
            self._script_lib_layout.addWidget(_empty)
            return

        for _sname in scripts:
            _is_sel = (_sname == getattr(self, '_selected_script_name', ''))
            _card_w = QWidget()
            _card_w.setObjectName("scriptCard")
            _card_w.setStyleSheet(
                f"QWidget#scriptCard {{ background: {_ACCENT}22; border-radius: 5px;"
                f" border: 1px solid {_ACCENT}; }}"
                if _is_sel else
                f"QWidget#scriptCard {{ background: {_SURFACE2}; border-radius: 5px;"
                f" border: 1px solid {_BORDER}; }}"
            )
            _card_w.setCursor(Qt.CursorShape.PointingHandCursor)
            _cr = QHBoxLayout(_card_w)
            _cr.setContentsMargins(8, 5, 8, 5)
            _cr.setSpacing(6)
            _name_lbl = QLabel(_sname)
            _name_lbl.setStyleSheet(
                f"color: {_ACCENT}; font-size: 11px; font-family: 'Courier New', monospace;"
                f" background: transparent; border: none;"
                if _is_sel else
                f"color: {_TEXT}; font-size: 11px; font-family: 'Courier New', monospace;"
                f" background: transparent; border: none;"
            )
            _cr.addWidget(_name_lbl, stretch=1)
            if _is_sel:
                _sel_lbl = QLabel("✓ selected")
                _sel_lbl.setStyleSheet(
                    f"color: {_ACCENT}; font-size: 10px; background: transparent; border: none;"
                )
                _cr.addWidget(_sel_lbl)

            def _make_select(sn):
                def _do(_ev=None):
                    self._selected_script_name = sn
                    self._refresh_script_library()
                return _do

            _card_w.mousePressEvent = _make_select(_sname)
            self._script_lib_layout.addWidget(_card_w)

    def _open_scripts_folder(self):
        """Delegate to TaskManager to open the interval-scripts folder."""
        if self._task_mgr:
            self._task_mgr.open_scripts_folder()

    def _show_script_trigger_help(self):
        """Open a small informational dialog explaining Script Trigger mode."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextBrowser
        dlg = QDialog(self)
        dlg.setWindowTitle("Script Trigger — How to use")
        dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        dlg.resize(520, 440)

        outer = QWidget(dlg)
        outer.setObjectName("helpOuter")
        outer.setStyleSheet(f"""
            QWidget#helpOuter {{
                background: {_SURFACE};
                border-radius: 10px;
                border: 1px solid {_BORDER};
            }}
        """)
        _dlg_vl = QVBoxLayout(dlg)
        _dlg_vl.setContentsMargins(0, 0, 0, 0)
        _dlg_vl.addWidget(outer)

        _ol = QVBoxLayout(outer)
        _ol.setContentsMargins(20, 16, 20, 16)
        _ol.setSpacing(12)

        _title = QLabel("Script Trigger — How it works")
        _title.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 700;")
        _ol.addWidget(_title)

        _browser = QTextBrowser()
        _browser.setStyleSheet(f"""
            QTextBrowser {{
                background: {_BG}; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 6px;
                padding: 10px; font-size: 12px;
            }}
        """)
        _browser.setOpenExternalLinks(False)
        _browser.setHtml(f"""
        <style>
            body  {{ color: {_TEXT}; font-size: 12px; line-height: 1.6; }}
            h3    {{ color: {_ACCENT}; margin-bottom: 2px; margin-top: 10px; }}
            code  {{ color: #C3E88D; background: #1a1f28; padding: 1px 4px;
                     border-radius: 3px; font-family: Consolas, monospace; }}
            li    {{ margin-bottom: 4px; }}
        </style>
        <h3>Overview</h3>
        <p>Instead of a fixed timer, the agent waits for a Python script to signal it.
        The script is polled at your <b>Poll Rate</b>. When it returns <code>True</code>,
        the full AI ping fires — exactly like a timed ping — then polling resumes.</p>

        <h3>The contract</h3>
        <p>Your script must define exactly one function:</p>
        <p><code>def fire_ping() -> bool:</code></p>
        <ul>
            <li>Return <code>True</code> → ping fires immediately.</li>
            <li>Return <code>False</code> → sleep for Poll Rate ms and check again.</li>
        </ul>

        <h3>Your responsibility</h3>
        <p>Once <code>fire_ping()</code> returns <code>True</code> and the ping fires,
        polling resumes. <b>You must reset the condition to False inside your script</b>
        (e.g. delete a flag file, clear a variable) — otherwise it keeps firing every
        poll cycle.</p>

        <h3>Thread safety — built in</h3>
        <ul>
            <li><b>Script guard</b> — if a previous <code>fire_ping()</code> is still
            running when the next tick arrives, that tick is skipped. Hangs never stack.</li>
            <li><b>Ping guard</b> — if a ping is already in progress, any
            <code>True</code> signals received during it are discarded. Only one ping
            at a time.</li>
        </ul>

        <h3>How to add a script</h3>
        <ol>
            <li>Click <b>📂 Open Script Folder</b> →
            <code>data/tasks/interval-scripts/</code>.</li>
            <li>Copy <code>_template.py</code>, rename it (e.g.
            <code>watch_file.py</code>).</li>
            <li>Edit <code>fire_ping()</code> with your condition.</li>
            <li>Click <b>↺</b> or switch modes — your script appears as a card.
            Click it to select.</li>
        </ol>
        """)
        _ol.addWidget(_browser, stretch=1)

        _close_btn = QPushButton("Close")
        _close_btn.setStyleSheet(_BTN_ACCENT)
        _close_btn.setFixedWidth(90)
        _close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _close_btn.clicked.connect(dlg.accept)
        _ol.addWidget(_close_btn, alignment=Qt.AlignmentFlag.AlignRight)

        dlg.exec()

    def _refresh_functions_list(self):
        """Rebuild the function pills in the Functions Library card."""
        if not hasattr(self, '_fn_list_layout'):
            return
        while self._fn_list_layout.count():
            item = self._fn_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._task_mgr is None:
            return

        functions = self._task_mgr.get_functions()
        if not functions:
            empty_lbl = QLabel("No functions yet. Click ＋ Add Function to write one.")
            empty_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
            self._fn_list_layout.addWidget(empty_lbl)
            return

        for fn in functions:
            row = QWidget()
            row.setStyleSheet(
                f"background: {_SURFACE2}; border-radius: 5px; border: 1px solid {_BORDER};"
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 8, 4)
            rl.setSpacing(6)
            name_lbl = QLabel(f"def {fn['name']}(...)")
            name_lbl.setStyleSheet(
                f"color: {_ACCENT}; font-size: 11px; font-family: 'Courier New', monospace;"
                f" background: transparent; border: none;"
            )
            rl.addWidget(name_lbl, stretch=1)
            edit_btn = QPushButton("Edit")
            edit_btn.setFixedSize(54, 24)
            edit_btn.setStyleSheet(_BTN)
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.clicked.connect(lambda _, f=fn: self._edit_function_entry(f))
            rl.addWidget(edit_btn)
            del_btn = QPushButton("✕  Delete")
            del_btn.setStyleSheet(_BTN_RED)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda _, n=fn['name']: self._delete_function_entry(n))
            rl.addWidget(del_btn)
            self._fn_list_layout.addWidget(row)

    def _edit_function_entry(self, fn: dict):
        """Load an existing function into the inline editor."""
        self._fn_name_input.setText(fn['name'])
        self._fn_code_editor.setPlainText(fn['code'])
        self._fn_editor_panel.setVisible(True)
        self._fn_name_input.setFocus()

    def _save_function_from_editor(self):
        """Validate and save the function from the inline editor."""
        if self._task_mgr is None:
            return
        import ast

        code = self._fn_code_editor.toPlainText().strip()
        name = self._fn_name_input.text().strip()
        self._fn_error_lbl.setVisible(False)

        if not code:
            self._fn_error_lbl.setText("⚠  Code cannot be empty.")
            self._fn_error_lbl.setVisible(True)
            return

        # Auto-extract name from def if field was left blank
        if not name:
            m = re.search(r'^\s*def\s+([a-zA-Z_]\w*)\s*\(', code, re.MULTILINE)
            if m:
                name = m.group(1)
                self._fn_name_input.setText(name)
            else:
                self._fn_error_lbl.setText(
                    "⚠  No function name given and none detected. "
                    "Either fill in the name field or make sure your code starts with  def name():"
                )
                self._fn_error_lbl.setVisible(True)
                return

        # Syntax check with full visible error
        try:
            ast.parse(code)
        except SyntaxError as e:
            self._fn_error_lbl.setText(
                f"⚠  SyntaxError on line {e.lineno}: {e.msg}  —  "
                f"check your quotes, colons, and indentation."
            )
            self._fn_error_lbl.setVisible(True)
            return

        self._task_mgr.save_function(name, code)
        self._fn_editor_panel.setVisible(False)
        self._fn_name_input.clear()
        self._fn_code_editor.clear()
        self._fn_error_lbl.setVisible(False)
        self._refresh_functions_list()

    def _delete_function_entry(self, name: str):
        """Delete a function from the library."""
        if self._task_mgr is None:
            return
        self._task_mgr.delete_function(name)
        self._refresh_functions_list()

    # ═══════════════════════════════════════════════════════════════════════════
    # INSTRUCTION VERIFIER
    # ═══════════════════════════════════════════════════════════════════════════

    _BLOCKING_PATTERNS = [
        (r'\binput\s*\(',          "input() — blocks waiting for keyboard input"),
        (r'\braw_input\s*\(',      "raw_input() — blocks waiting for keyboard input"),
        (r'\btkinter\b',           "tkinter — would launch a blocking GUI window"),
        (r'\bimport\s+wx\b',       "wx — would launch a blocking GUI window"),
        (r'\bQApplication\s*\(',   "QApplication() — would launch a blocking Qt GUI"),
        (r'\bplt\.show\s*\(',      "plt.show() — opens a blocking plot window"),
        (r'\bmatplotlib.*\.show\s*\(', "matplotlib.show() — opens a blocking plot window"),
        (r'\bpygame\.display\b',   "pygame.display — would launch a blocking game window"),
        (r'\bgtk\.main\s*\(',      "gtk.main() — blocking GTK event loop"),
    ]

    def _verify_instruction(self) -> dict:
        """
        Parse all {{ }} blocks from the instruction field and test-execute each one.
        Returns a dict:
            {
              'blocks': [ {'expr': str, '_raw': str, 'result': str, 'error': str|None, 'blocking': str|None} ],
              'has_errors': bool,
              'has_blocking': bool,
            }
        """
        import ast
        text = self._f_instruction.toPlainText()
        raw_blocks = re.findall(r'\{\{(.*?)\}\}', text, re.DOTALL)

        if not raw_blocks:
            return {'blocks': [], 'has_errors': False, 'has_blocking': False}

        try:
            from systema.execution.python_interpreter import PythonInterpreter
            interp = PythonInterpreter()
            # Inject controller so {{ controller.ui.chat_window.isVisible() }} works
            interp.namespace['controller'] = self._controller
            # Pre-inject saved functions so {{ my_func() }} resolves correctly
            if self._task_mgr is not None:
                for _fn in self._task_mgr.get_functions():
                    try:
                        interp.execute(_fn['code'])
                    except Exception:
                        pass
        except Exception as e:
            return {
                'blocks': [{'expr': '(interpreter)', '_raw': '', 'result': '',
                            'error': f"Could not load Python interpreter: {e}", 'blocking': None}],
                'has_errors': True,
                'has_blocking': False,
            }

        results = []
        has_errors = False
        has_blocking = False

        for raw in raw_blocks:
            expr = raw.strip()
            blocking_reason = None
            error = None
            result_str = ''

            # ── Check for blocking / GUI patterns ─────────────────────────────
            for pattern, reason in self._BLOCKING_PATTERNS:
                if re.search(pattern, expr):
                    blocking_reason = reason
                    has_blocking = True
                    break

            if blocking_reason:
                results.append({'expr': expr, '_raw': raw, 'result': '', 'error': None, 'blocking': blocking_reason})
                continue

            # ── Syntax check ──────────────────────────────────────────────────
            try:
                ast.parse(expr)
            except SyntaxError as e:
                has_errors = True
                results.append(
                    {'expr': expr, '_raw': raw, 'result': '', 'error': f"SyntaxError: {e}", 'blocking': None})
                continue

            # ── Execute ───────────────────────────────────────────────────────
            try:
                out = interp.execute(expr)
                if out.get('error'):
                    last_line = out['error'].strip().splitlines()[-1] if out['error'].strip() else 'Error'
                    error = last_line
                    has_errors = True
                elif out.get('stdout'):
                    result_str = out['stdout'].strip()
                elif out.get('result') is not None:
                    result_str = repr(out['result'])
                else:
                    result_str = '(no output)'
            except Exception as e:
                error = str(e)
                has_errors = True

            results.append({'expr': expr, '_raw': raw, 'result': result_str, 'error': error, 'blocking': None})

        return {'blocks': results, 'has_errors': has_errors, 'has_blocking': has_blocking}
    def _run_verify_and_display(self):
        """Called by the Verify button — offloads execution to a background thread so the UI never freezes."""
        # Ignore double-click while a verify is already running
        if self._verify_worker and self._verify_worker.isRunning():
            return

        text = self._f_instruction.toPlainText()
        raw_blocks = re.findall(r'\{\{(.*?)\}\}', text, re.DOTALL)

        self._verify_result_box.setVisible(True)
        if not raw_blocks:
            self._verify_result_box.setHtml(
                f"<span style='color:{_MUTED};'>No {{{{ }}}} blocks found in instruction.</span>"
            )
            return

        self._verify_result_box.setPlainText("⏳  Running verification…")

        functions = self._task_mgr.get_functions() if self._task_mgr else []
        self._verify_worker = _VerifyWorker(text, functions, self._controller, self._BLOCKING_PATTERNS)
        self._verify_worker.done.connect(lambda data: self._on_verify_done(text, data))
        self._verify_worker.start()

    def _on_verify_done(self, text: str, data: dict):
        """Slot called when _VerifyWorker finishes — always executes on the main thread via Qt signal."""
        import html as _html

        # ── Build rendered preview: replace each {{ }} with its evaluated value ──
        rendered = text
        for b in data['blocks']:
            placeholder = '{{' + b['_raw'] + '}}'
            if b['blocking']:
                substitution = f"[⛔ {b['blocking']}]"
            elif b['error']:
                substitution = f"[✗ {b['error']}]"
            else:
                substitution = b['result'] if b['result'] not in ('', '(no output)') else '(no output)'
            rendered = rendered.replace(placeholder, substitution, 1)

        rendered_escaped = _html.escape(rendered).replace('\n', '<br>')
        lines = []

        # ── Section 1: full rendered instruction ──────────────────────────────
        lines.append(
            f"<div style='margin-bottom:6px;'>"
            f"<span style='color:{_MUTED}; font-size:10px; font-weight:600; letter-spacing:1px;'>RENDERED PREVIEW</span><br>"
            f"<span style='color:{_TEXT};'>{rendered_escaped}</span>"
            f"</div>"
        )

        # ── Section 2: per-block status ───────────────────────────────────────
        lines.append(
            f"<span style='color:{_MUTED}; font-size:10px; font-weight:600; letter-spacing:1px;'>BLOCKS</span>"
        )
        for i, b in enumerate(data['blocks'], 1):
            preview = b['expr'][:60].replace('\n', ' ')
            if len(b['expr']) > 60:
                preview += '…'
            if b['blocking']:
                lines.append(
                    f"<span style='color:{_RED};'>⛔ [{i}] <code>{_html.escape(preview)}</code>"
                    f" → Blocked: {_html.escape(b['blocking'])}</span>"
                )
            elif b['error']:
                lines.append(
                    f"<span style='color:{_RED};'>✗ [{i}] <code>{_html.escape(preview)}</code>"
                    f" → {_html.escape(b['error'])}</span>"
                )
            else:
                lines.append(
                    f"<span style='color:{_GREEN};'>✓ [{i}] <code>{_html.escape(preview)}</code>"
                    f" → {_html.escape(b['result'])}</span>"
                )

        self._verify_result_box.setFixedHeight(max(110, min(220, 70 + len(data['blocks']) * 22)))
        self._verify_result_box.setHtml("<br>".join(lines))

    _SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _start_save_spinner(self):
        """Disable Save + Back and start the braille spinner animation."""
        self._save_btn.setEnabled(False)
        self._back_btn.setEnabled(False)
        self._save_spinner_frame = 0
        if self._save_spinner_timer is None:
            self._save_spinner_timer = QTimer(self)
            self._save_spinner_timer.timeout.connect(self._tick_save_spinner)
        self._save_spinner_timer.start(80)

    def _tick_save_spinner(self):
        frame = self._SPINNER_FRAMES[self._save_spinner_frame % len(self._SPINNER_FRAMES)]
        self._save_btn.setText(f"{frame}  Saving…")
        self._save_spinner_frame += 1

    def _stop_save_spinner(self):
        """Stop animation and restore Save + Back to normal state."""
        if self._save_spinner_timer:
            self._save_spinner_timer.stop()
        self._save_btn.setText("Save Task")
        self._save_btn.setStyleSheet(_BTN_ACCENT)
        self._save_btn.setEnabled(True)
        self._back_btn.setEnabled(True)

    def _do_save(self, task_dict: dict, editing_id):
        """Persist the task then navigate back to the list."""
        try:
            if editing_id:
                self._task_mgr.update_task(editing_id, task_dict)
            else:
                self._task_mgr.add_task(task_dict)
        except Exception:
            pass  # task_mgr already logs internally
        finally:
            self._stop_save_spinner()
            # Rebuild the list regardless of visibility — the widget tree is always
            # alive, so this is safe. The user will see the correct list next open.
            self._refresh_list()
            if self.isVisible():
                self._stack.setCurrentIndex(0)

    def _on_save_verify_done(self, task_dict: dict, editing_id, data: dict):
        """Slot fired when the background save-path verify thread finishes."""
        if data['has_blocking'] or data['has_errors']:
            self._stop_save_spinner()
            issues = []
            for b in data['blocks']:
                if b['blocking']:
                    issues.append(f"• Blocking call: {b['blocking']}")
                elif b['error']:
                    issues.append(f"• {b['error']}")
            msg = "Cannot save — issues found in {{ }} blocks:\n\n" + "\n".join(issues)
            if hasattr(self, 'controller') and hasattr(self._controller, 'notify'):
                self._controller.notify("Task Validation Failed", msg, level="warning")
            else:
                QMessageBox.warning(self, "Task Validation Failed", msg)
            self._run_verify_and_display()
            return
        self._do_save(task_dict, editing_id)

    def _save_task(self):
        if self._task_mgr is None:
            return

        # Ignore click if a save verify is already running
        if self._save_verify_worker and self._save_verify_worker.isRunning():
            return

        name = self._f_name.text().strip()
        if not name:
            self._f_name.setPlaceholderText("⚠ Name required!")
            return

        # ── Snapshot ALL form state NOW before any async gap ─────────────────
        start_t = self._f_start.time()
        end_t   = self._f_end.time()
        _pim_ui        = self._get_ping_interval_mode_ui()
        use_specific   = (_pim_ui == 'specific_times')
        specific_times = sorted(self._get_current_ping_times()) if use_specific else []
        ping_mode      = 'schedule_relative' if self._f_mode_schedule.isChecked() else 'startup_relative'
        task_dict = {
            "name": name,
            "active": self._f_active_editor.isChecked(),
            "instruction": self._f_instruction.toPlainText(),
            "interval_minutes": self._f_interval.value(),
            "ping_mode": ping_mode,
            "ping_interval_mode": _pim_ui,
            "use_specific_ping_times": use_specific,
            "specific_ping_times": specific_times,
            "script_name": self._selected_script_name if _pim_ui == 'script_trigger' else '',
            "script_poll_ms": self._f_script_poll_ms.value(),
            "daily_schedule": {
                "whole_day": self._f_whole_day.isChecked(),
                "start": f"{start_t.hour():02d}:{start_t.minute():02d}",
                "end": f"{end_t.hour():02d}:{end_t.minute():02d}",
            },
            "permissions": {
                "allow_workmode": self._f_perm_workmode.isChecked(),
                "inject_image_tools": self._f_perm_image_tools.isChecked(),
                "inject_controller_ref": self._f_perm_controller_ref.isChecked(),
                "inject_notify_tool": self._f_perm_notify_tool.isChecked(),
                "task_security_policy": self._read_task_policy(),
            },
            "max_work_iterations": self._f_max_iterations.value(),
            "unlimited_work_iterations": self._f_unlimited_iterations.isChecked(),
            "loaded_skills": self._get_checked_skills(),
            "limit_session_messages": {
                "enabled": self._f_limit_enabled.isChecked(),
                "max_messages": self._f_limit_max.value(),
            },
            "one_time_schedule": {
                "enabled": self._f_one_time_enabled.isChecked(),
                "datetimes": self._collect_one_time_datetimes(),
            },
        }
        editing_id = self._editing_task_id   # snapshot before any async gap

        # ── If instruction has {{ }} blocks, verify off-thread first ──────────
        if re.search(r'\{\{.*?\}\}', task_dict['instruction'], re.DOTALL):
            self._start_save_spinner()
            functions = self._task_mgr.get_functions() if self._task_mgr else []
            self._save_verify_worker = _VerifyWorker(
                task_dict['instruction'], functions, self._controller, self._BLOCKING_PATTERNS
            )
            self._save_verify_worker.done.connect(
                lambda data: self._on_save_verify_done(task_dict, editing_id, data)
            )
            self._save_verify_worker.start()
        else:
            # No {{ }} blocks — save is instant, go straight through
            self._do_save(task_dict, editing_id)

