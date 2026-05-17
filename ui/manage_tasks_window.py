"""
ui/manage_tasks_window.py
Manage Tasks Window — Create, edit, and review scheduled task sessions.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QLineEdit, QTextEdit, QCheckBox,
    QSpinBox, QStackedWidget, QSizePolicy, QApplication,
    QTimeEdit, QRadioButton, QDateTimeEdit,
)
from PyQt6.QtCore import Qt, QTime, QDateTime, QPoint
from PyQt6.QtGui import QFont
from ui.base_window import BaseWindow

_BG       = "#0D1117"
_SURFACE  = "#161B22"
_SURFACE2 = "#21262D"
_BORDER   = "#30363D"
_ACCENT   = "#58A6FF"
_TEXT     = "#E6EDF3"
_MUTED    = "#8B949E"
_RED      = "#F85149"
_GREEN    = "#3FB950"

_BTN = f"""
    QPushButton {{
        background: {_SURFACE2}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 6px;
        padding: 6px 14px; font-size: 12px;
    }}
    QPushButton:hover {{ background: #2D333B; border-color: {_ACCENT}; }}
    QPushButton:pressed {{ background: #1C2128; }}
"""
_BTN_ACCENT = f"""
    QPushButton {{
        background: {_ACCENT}; color: #0D1117;
        border: none; border-radius: 6px;
        padding: 6px 14px; font-size: 12px; font-weight: 600;
    }}
    QPushButton:hover {{ background: #79B8FF; }}
    QPushButton:pressed {{ background: #388BFD; }}
"""
_BTN_RED = f"""
    QPushButton {{
        background: transparent; color: {_RED};
        border: 1px solid {_RED}; border-radius: 6px;
        padding: 5px 10px; font-size: 11px;
    }}
    QPushButton:hover {{ background: rgba(248,81,73,0.15); }}
"""
_INPUT = f"""
    QLineEdit, QTextEdit, QSpinBox, QTimeEdit {{
        background: {_SURFACE2}; color: {_TEXT};
        border: 1px solid {_BORDER}; border-radius: 6px;
        padding: 6px 10px; font-size: 12px;
    }}
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QTimeEdit:focus {{
        border-color: {_ACCENT};
    }}
"""
_CHECK = f"""
    QCheckBox {{ color: {_TEXT}; font-size: 12px; spacing: 6px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
        border: 1px solid {_BORDER}; background: {_SURFACE2}; }}
    QCheckBox::indicator:checked {{ background: {_ACCENT}; border-color: {_ACCENT}; }}
"""
_SEC = f"color: {_MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
_LBL = f"color: {_TEXT}; font-size: 12px;"


def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet(f"background: {_BORDER}; max-height: 1px;")
    return f


class ManageTasksWindow(BaseWindow):
    """Main manage-tasks window — task list, editor, and session viewer."""

    _header_height: int = 44  # matches title bar height for resize handles

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._init_chrome_state()
        self.controller = controller
        self._task_mgr = getattr(controller, 'task_manager', None)
        self._editing_task_id = None   # None = creating new
        self._sessions_expanded = {}   # task_id → bool

        self.setWindowTitle("Manage Tasks")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(720, 600)
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
        root.addWidget(self._build_title_bar())

        # ── Stack: 0=list  1=editor  2=session viewer ────────────────────────
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        self._stack.addWidget(self._build_list_page())    # index 0
        self._stack.addWidget(self._build_editor_page())  # index 1
        self._stack.addWidget(self._build_viewer_page())  # index 2

        self._stack.setCurrentIndex(0)
        self._refresh_list()

        self.setMinimumSize(680, 480)
        self.create_resize_handles()

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
        vl.setContentsMargins(20, 20, 20, 20)
        vl.setSpacing(12)

        # Header
        hl = QHBoxLayout()
        hl.addStretch()
        new_btn = QPushButton("＋  New Task")
        new_btn.setStyleSheet(_BTN_ACCENT)
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.clicked.connect(self._new_task)
        hl.addWidget(new_btn)
        vl.addLayout(hl)

        vl.addWidget(_sep())

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
        if not tasks:
            lbl = QLabel("No tasks yet. Click ＋ New Task to create one.")
            lbl.setStyleSheet(f"color: {_MUTED}; padding: 20px;")
            self._list_layout.insertWidget(0, lbl)
            return

        for i, task in enumerate(tasks):
            row = self._build_task_row(task)
            self._list_layout.insertWidget(i, row)

    def _build_task_row(self, task: dict) -> QWidget:
        task_id = task['id']
        container = QWidget()
        container.setObjectName("taskRowCard")
        container.setStyleSheet(f"""
                    QWidget#taskRowCard {{
                        background: {_SURFACE};
                        border-radius: 8px;
                        border: 1px solid rgba(88, 166, 255, 0.18);
                    }}
                """)
        vl = QVBoxLayout(container)
        vl.setContentsMargins(14, 12, 14, 12)
        vl.setSpacing(6)

        # ── Top row: name + buttons ───────────────────────────────────────────
        top = QHBoxLayout()

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name_lbl = QLabel(task.get('name', 'Unnamed Task'))
        name_lbl.setStyleSheet(
            f"color: {_TEXT}; font-size: 14px; font-weight: 700; background: transparent; border: none;")
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
            interval = task.get('interval_minutes', 30)
            sched = task.get('daily_schedule', {})
            sched_text = "Whole day" if sched.get('whole_day') else f"{sched.get('start', '?')} – {sched.get('end', '?')}"
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

        is_active = task.get('active', True)
        active_btn = QPushButton("● Active" if is_active else "○ Inactive")
        active_btn.setFixedHeight(26)
        active_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {_GREEN}; border: 1px solid {_GREEN}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
            if is_active else
            f"QPushButton {{ background: transparent; color: {_MUTED}; border: 1px solid {_MUTED}; "
            f"border-radius: 6px; padding: 4px 10px; font-size: 11px; }}"
        )
        active_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        active_btn.clicked.connect(lambda _, tid=task_id, btn=active_btn: self._toggle_task_active(tid, btn))
        top.addWidget(active_btn)

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
        return container

    def _populate_sessions_panel(self, layout: QVBoxLayout, task_id: str):
        # Clear existing
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._task_mgr is None:
            return

        sessions = self._task_mgr.get_task_sessions(task_id)

        # Clear all button
        clear_btn = QPushButton("✕  Clear All Sessions")
        clear_btn.setStyleSheet(_BTN_RED)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(lambda: self._clear_all_sessions(task_id, layout))
        layout.addWidget(clear_btn)

        if not sessions:
            layout.addWidget(QLabel("No sessions yet."))
            return

        for date_str in sessions:
            row = QHBoxLayout()
            lbl = QLabel(date_str)
            lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; background: transparent;")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = lambda e, tid=task_id, d=date_str: self._view_session(tid, d)
            row.addWidget(lbl, stretch=1)

            x_btn = QPushButton("✕")
            x_btn.setFixedSize(24, 24)
            x_btn.setStyleSheet(_BTN_RED)
            x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            x_btn.clicked.connect(lambda _, tid=task_id, d=date_str, lo=layout: self._delete_session(tid, d, lo))
            row.addWidget(x_btn)
            layout.addLayout(row)

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

    def _delete_task(self, task_id: str):
        if self._task_mgr:
            self._task_mgr.delete_task(task_id)
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
        back_btn = QPushButton("← Back")
        back_btn.setStyleSheet(_BTN)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        bl.addWidget(back_btn)
        self._editor_title = QLabel("New Task")
        self._editor_title.setStyleSheet(
            f"color: {_TEXT}; font-size: 14px; font-weight: 600; padding-left: 12px;"
        )
        bl.addWidget(self._editor_title, stretch=1)
        save_btn = QPushButton("Save Task")
        save_btn.setStyleSheet(_BTN_ACCENT)
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save_task)
        bl.addWidget(save_btn)
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
        def _card(*inner_widgets):
            card = QWidget()
            card.setObjectName("editorCard")
            card.setStyleSheet(
                f"QWidget#editorCard {{ background: {_SURFACE}; border-radius: 8px;"
                f" border: 1px solid {_BORDER}; }}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 14, 16, 14)
            cl.setSpacing(10)
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
        fl.addWidget(_card(
            _field(
                "INSTRUCTION",
                self._f_instruction,
                "What should the agent do at each ping? Supports {{ Python }} inline blocks.",
            )
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
            "Example: app opens at 4:31 PM, interval 30 min → next ping at 5:01 PM."
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

        # Specific ping toggle
        self._f_specific_ping = QCheckBox("Use Specific Ping Times  (disables the interval above)")
        self._f_specific_ping.setStyleSheet(_CHECK)

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

        self._f_specific_ping.toggled.connect(lambda on: (
            self._f_interval.setEnabled(not on),
            ping_mode_row.setEnabled(not on),
            self._ping_times_panel.setVisible(on),
        ))

        schedule_lbl = QLabel("DAILY SCHEDULE")
        schedule_lbl.setStyleSheet(_SEC)
        interval_lbl = QLabel("PING INTERVAL / TIMES")
        interval_lbl.setStyleSheet(_SEC)
        mode_lbl = QLabel("INTERVAL MODE")
        mode_lbl.setStyleSheet(_SEC)

        self._daily_schedule_card = _card(
            schedule_lbl,
            self._f_whole_day,
            time_row,
            interval_lbl,
            _field("", self._f_interval),
            mode_lbl,
            ping_mode_row,
            self._f_specific_ping,
            self._ping_times_panel,
        )
        fl.addWidget(self._daily_schedule_card)

        # ══ CARD 3b: One-Time Schedule ════════════════════════════════════════
        one_time_lbl = QLabel("ONE TIME SCHEDULE")
        one_time_lbl.setStyleSheet(_SEC)
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
            one_time_lbl,
            one_time_hint,
            self._f_one_time_enabled,
            self._one_time_dt_panel,
        ))

        # ══ CARD 4: Permissions ═══════════════════════════════════════════════
        perm_lbl = QLabel("AGENT PERMISSIONS")
        perm_lbl.setStyleSheet(_SEC)
        perm_hint = QLabel("Controls what the agent is allowed to do during each ping.")
        perm_hint.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")

        self._f_perm_workmode   = QCheckBox("Allow Work Mode  (can use work_environment to run code & see output)")
        self._f_perm_exec_code  = QCheckBox("Allow Execute Code  (can run fire-and-forget Python)")
        self._f_perm_skill_mgmt = QCheckBox("Allow Skill Load / Unload  (can modify its own skill context)")

        self._f_perm_workmode.setToolTip(
            "Lets the agent use the work_environment tool to write and execute code\n"
            "in an isolated workspace and observe its full output.\n\n"
            "⚠  Bypasses the 'Supervised Execution' setting.\n"
            "Code runs automatically in the background — no approval prompt."
        )
        self._f_perm_exec_code.setToolTip(
            "Lets the agent call execute_code to fire-and-forget Python snippets\n"
            "(e.g. launch a process, write a file, send a notification).\n\n"
            "⚠  Bypasses the 'Supervised Execution' setting.\n"
            "Code runs automatically in the background — no approval prompt."
        )
        self._f_perm_skill_mgmt.setToolTip(
            "Lets the agent call load_skill / unload_skill during a ping session,\n"
            "dynamically swapping which skills are active in its context.\n\n"
            "The skill changes only last for that session and do not affect your main window."
        )

        for cb in (self._f_perm_workmode, self._f_perm_exec_code, self._f_perm_skill_mgmt):
            cb.setStyleSheet(_CHECK)

        fl.addWidget(_card(perm_lbl, perm_hint,
                           self._f_perm_workmode, self._f_perm_exec_code, self._f_perm_skill_mgmt))

        # ══ CARD 5: Pre-loaded Skills ══════════════════════════════════════════
        skills_lbl = QLabel("PRE-LOADED SKILLS")
        skills_lbl.setStyleSheet(_SEC)
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

        fl.addWidget(_card(skills_lbl, skills_hint, self._skill_toggle_btn, self._skill_checklist_panel))

        # ══ CARD 6: Context / Memory ══════════════════════════════════════════
        limit_lbl = QLabel("SESSION CONTEXT LIMIT")
        limit_lbl.setStyleSheet(_SEC)
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

        fl.addWidget(_card(limit_lbl, limit_hint, self._f_limit_enabled, limit_row))

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
        bar.setStyleSheet(f"background: {_SURFACE}; border-bottom: 1px solid {_BORDER};")
        bar.setFixedHeight(48)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)

        back_btn = QPushButton("← Back")
        back_btn.setStyleSheet(_BTN)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        bl.addWidget(back_btn)

        self._viewer_title = QLabel("Session")
        self._viewer_title.setStyleSheet(f"color: {_TEXT}; font-size: 14px; font-weight: 600; padding-left: 12px;")
        bl.addWidget(self._viewer_title, stretch=1)
        vl.addWidget(bar)

        # Read-only chat area
        self._viewer_area = QScrollArea()
        self._viewer_area.setWidgetResizable(True)
        self._viewer_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._viewer_body = QWidget()
        self._viewer_body.setStyleSheet(f"background: {_BG};")
        self._viewer_body_layout = QVBoxLayout(self._viewer_body)
        self._viewer_body_layout.setContentsMargins(20, 16, 20, 16)
        self._viewer_body_layout.setSpacing(10)
        self._viewer_body_layout.addStretch()
        self._viewer_area.setWidget(self._viewer_body)
        vl.addWidget(self._viewer_area, stretch=1)
        return page

    def _view_session(self, task_id: str, date_str: str):
        if self._task_mgr is None:
            return
        session = self._task_mgr.load_task_session(task_id, date_str)
        if session is None:
            return

        # Clear viewer
        layout = self._viewer_body_layout
        while layout.count() > 1:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._viewer_title.setText(f"Session — {date_str}  ({session.get('session_name', '')})")

        for msg in session.get('chat_history', []):
            role = msg.get('role', 'system')
            content = msg.get('content', '')
            bubble = QFrame()
            is_system = role == 'system'
            bubble.setStyleSheet(f"""
                            QFrame {{
                                background: {'#0D1117' if is_system else _SURFACE2};
                                border: 1px solid {'rgba(88, 166, 255, 0.12)' if not is_system else _BORDER};
                                border-radius: 8px;
                                padding: 8px;
                            }}
                        """)
            bl = QVBoxLayout(bubble)
            bl.setContentsMargins(10, 8, 10, 8)
            role_lbl = QLabel("📥 Ping" if is_system else "🤖 Agent")
            role_lbl.setStyleSheet(f"color: {_ACCENT if not is_system else _MUTED}; font-size: 10px; font-weight: 600;")
            bl.addWidget(role_lbl)
            text_lbl = QLabel(content)
            text_lbl.setWordWrap(True)
            text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
            bl.addWidget(text_lbl)
            layout.insertWidget(layout.count() - 1, bubble)

        self._stack.setCurrentIndex(2)

    # ═══════════════════════════════════════════════════════════════════════════
    # Editor logic
    # ═══════════════════════════════════════════════════════════════════════════

    # ── Skill checklist helpers ────────────────────────────────────────────────

    def _get_available_skill_names(self) -> list:
        """Fetch available skill names from the skill manager."""
        try:
            sm = getattr(self.controller, 'skill_manager', None)
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
        rm_btn = QPushButton("✕")
        rm_btn.setFixedSize(24, 24)
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
        self._f_perm_exec_code.setChecked(False)
        self._f_perm_skill_mgmt.setChecked(False)
        self._f_mode_startup.setChecked(True)
        self._f_specific_ping.setChecked(False)
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
        self._stack.setCurrentIndex(1)

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
        self._f_specific_ping.setChecked(use_specific)
        self._f_interval.setEnabled(not use_specific)
        self._clear_ping_times_list()
        self._ping_hint_lbl.setText("⏰  Add ping times within your active schedule window.")
        for t_str in task.get('specific_ping_times', []):
            self._render_ping_time_row(t_str)
        self._ping_times_panel.setVisible(use_specific)

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
        self._f_perm_workmode.setChecked(perms.get('allow_workmode', False))
        self._f_perm_exec_code.setChecked(perms.get('allow_execute_code', False))
        self._f_perm_skill_mgmt.setChecked(perms.get('allow_skill_load_unload', False))

        self._populate_skill_checklist(task.get('loaded_skills', []))
        self._skill_checklist_panel.setVisible(False)
        self._update_skill_toggle_label()

        limit = task.get('limit_session_messages', {})
        self._f_limit_enabled.setChecked(limit.get('enabled', False))
        self._f_limit_max.setValue(limit.get('max_messages', 5))

        one_time = task.get('one_time_schedule', {})
        one_time_on = one_time.get('enabled', False)
        self._f_one_time_enabled.setChecked(one_time_on)
        self._one_time_dt_panel.setVisible(one_time_on)
        self._daily_schedule_card.setEnabled(not one_time_on)
        self._clear_one_time_dt_list()
        for dt_str in one_time.get('datetimes', []):
            self._render_one_time_dt_row(dt_str)
        self._one_time_dt_picker.setDateTime(QDateTime.currentDateTime().addSecs(3600))

        self._stack.setCurrentIndex(1)

    def _save_task(self):
        if self._task_mgr is None:
            return

        name = self._f_name.text().strip()
        if not name:
            self._f_name.setPlaceholderText("⚠ Name required!")
            return

        start_t = self._f_start.time()
        end_t   = self._f_end.time()

        use_specific = self._f_specific_ping.isChecked()
        specific_times = sorted(self._get_current_ping_times()) if use_specific else []

        ping_mode = 'schedule_relative' if self._f_mode_schedule.isChecked() else 'startup_relative'
        task_dict = {
            "name": name,
            "active": self._f_active_editor.isChecked(),
            "instruction": self._f_instruction.toPlainText(),
            "interval_minutes": self._f_interval.value(),
            "ping_mode": ping_mode,
            "use_specific_ping_times": use_specific,
            "specific_ping_times": specific_times,
            "daily_schedule": {
                "whole_day": self._f_whole_day.isChecked(),
                "start": f"{start_t.hour():02d}:{start_t.minute():02d}",
                "end": f"{end_t.hour():02d}:{end_t.minute():02d}",
            },
            "permissions": {
                "allow_workmode": self._f_perm_workmode.isChecked(),
                "allow_execute_code": self._f_perm_exec_code.isChecked(),
                "allow_skill_load_unload": self._f_perm_skill_mgmt.isChecked(),
            },
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

        if self._editing_task_id:
            self._task_mgr.update_task(self._editing_task_id, task_dict)
        else:
            self._task_mgr.add_task(task_dict)

        self._stack.setCurrentIndex(0)
        self._refresh_list()

