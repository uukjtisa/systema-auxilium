"""
ui/timeout_dialog.py
Timeout Dialog - Shown when tool execution exceeds the configured timeout
Prompts user to extend or kill the running code.
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QWidget, QSpinBox, QPlainTextEdit)
from PyQt6.QtCore import Qt, QTimer


def _live_palette(widget) -> dict:
    """The ACTIVE theme's palette, asked of the parent's controller.

    Both dialogs in this module used to hardcode the obsidian-blue set, so they
    stayed blue-grey under every other theme the user picked. Falls back to the
    default palette when there is no parent to ask (standalone / tests).
    """
    from systema.ui import theme as _theme
    try:
        return _theme.current_palette(getattr(widget.parent(), 'controller', None))
    except Exception:
        return _theme.resolve_palette(_theme.THEMES[_theme.DEFAULT_THEME_KEY])


def _mix(over: str, onto: str, amount: float) -> str:
    """Blend `over` into `onto`. Hover states and the danger button's fill are
    DERIVED from the theme rather than being literals, so they stay in family
    whatever palette is active."""
    from PyQt6.QtGui import QColor
    a, b = QColor(over), QColor(onto)
    return QColor(
        round(b.red() + (a.red() - b.red()) * amount),
        round(b.green() + (a.green() - b.green()) * amount),
        round(b.blue() + (a.blue() - b.blue()) * amount)).name()


class TimeoutDialog(QDialog):
    """Modal dialog shown when code execution times out."""

    EXTEND_30 = 30
    EXTEND_60 = 60
    KILL = 0

    def __init__(self, elapsed_seconds, parent=None):
        super().__init__(parent)
        self._result = self.KILL
        self._init_ui(elapsed_seconds)

    def _init_ui(self, elapsed):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(540, 210)
        self.setModal(True)

        _p = _live_palette(self)
        _BASE    = _p['bg']
        _SURFACE = _p['surface']
        _ELEV    = _p['surface2']
        _BORDER  = _p['border']
        _ACCENT  = _p['accent']
        _TEXT    = _p['text']
        _MUTED   = _p['muted']
        _RED     = _p['red']
        _ELEV_HOVER = _mix(_TEXT, _ELEV, 0.10)
        _RED_FILL   = _mix(_RED, _SURFACE, 0.14)
        _RED_HOVER  = _mix(_RED, _SURFACE, 0.24)

        container = QWidget()
        container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
            QWidget {{ color: {_TEXT}; font-family: 'Segoe UI', system-ui, sans-serif; }}
        """)
        container.setObjectName("container")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(container)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(24, 20, 24, 20)
        inner.setSpacing(14)

        # Icon + Title
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        icon_lbl = QLabel("\u23F1")  # stopwatch emoji
        icon_lbl.setStyleSheet(f"font-size: 24px; background: transparent;")
        title_row.addWidget(icon_lbl)
        title = QLabel("Execution Timeout")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_TEXT}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        inner.addLayout(title_row)

        # Description
        desc = QLabel(
            f"Code execution has been running for <b>{elapsed}</b> seconds.\n"
            "What would you like to do?"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 12px; color: {_MUTED}; background: transparent; "
            f"padding: 0px; margin: 0px;")
        inner.addWidget(desc)

        # Spacer
        inner.addStretch()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        _BTN = f"""
            QPushButton {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_TEXT};
            }}
            QPushButton:hover {{ background-color: {_ELEV_HOVER}; border-color: {_ACCENT}; }}
        """
        _BTN_KILL = f"""
            QPushButton {{
                background-color: {_RED_FILL};
                border: 1px solid {_RED};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_RED};
            }}
            QPushButton:hover {{ background-color: {_RED_HOVER}; }}
        """

        extend_30 = QPushButton("30s")
        extend_30.setStyleSheet(_BTN)
        extend_30.setToolTip("Extend by 30 seconds")
        extend_30.clicked.connect(lambda: self._done(self.EXTEND_30))
        btn_row.addWidget(extend_30)

        extend_60 = QPushButton("60s")
        extend_60.setStyleSheet(_BTN)
        extend_60.setDefault(True)
        extend_60.setToolTip("Extend by 60 seconds")
        extend_60.clicked.connect(lambda: self._done(self.EXTEND_60))
        btn_row.addWidget(extend_60)

        # Custom extend
        custom_lbl = QLabel("Custom:")
        custom_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        btn_row.addWidget(custom_lbl)

        self._custom_spin = QSpinBox()
        self._custom_spin.setRange(1, 3600)
        self._custom_spin.setValue(120)
        self._custom_spin.setSingleStep(10)
        self._custom_spin.setSuffix(" s")
        self._custom_spin.setFixedWidth(90)
        self._custom_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 6px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QSpinBox:focus {{ border-color: {_ACCENT}; }}
        """)
        btn_row.addWidget(self._custom_spin)

        custom_btn = QPushButton("Extend")
        custom_btn.setStyleSheet(_BTN)
        custom_btn.clicked.connect(lambda: self._done(self._custom_spin.value()))
        btn_row.addWidget(custom_btn)

        btn_row.addStretch()

        kill_btn = QPushButton("Kill")
        kill_btn.setStyleSheet(_BTN_KILL)
        kill_btn.setToolTip("Kill execution")
        kill_btn.clicked.connect(lambda: self._done(self.KILL))
        btn_row.addWidget(kill_btn)

        inner.addLayout(btn_row)

        # Center on the chat window / primary screen (never the floating widget)
        from systema.ui.dialogs.dialog_utils import center_on_primary
        QTimer.singleShot(0, lambda: center_on_primary(self))

    def _done(self, result):
        self._result = result
        self.accept()

    @property
    def result_value(self):
        return self._result


class WorkmodeInterruptDialog(QDialog):
    """Frameless dialog with reason input for workmode interrupt.
    Auto-dismisses via 200ms poll when work.interpreter.is_running goes False.
    Returns reason_text on accept."""

    def __init__(self, parent=None, tool_name: str = ""):
        super().__init__(parent)
        self._reason = ""
        # What the agent is actually running right now, so the dialog can name
        # it instead of talking about "work" in the abstract.
        self._tool_name = (tool_name or "").strip()
        self._init_ui()

    def _init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 290)
        self.setModal(True)

        _p = _live_palette(self)
        _SURFACE = _p['surface']
        _ELEV    = _p['surface2']
        _BORDER  = _p['border']
        _ACCENT  = _p['accent']
        _TEXT    = _p['text']
        _MUTED   = _p['muted']
        _RED     = _p['red']
        _ELEV_HOVER = _mix(_TEXT, _ELEV, 0.10)
        _RED_FILL   = _mix(_RED, _SURFACE, 0.14)
        _RED_HOVER  = _mix(_RED, _SURFACE, 0.24)

        container = QWidget()
        container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
            QWidget {{ color: {_TEXT}; font-family: 'Segoe UI', system-ui, sans-serif; }}
        """)
        container.setObjectName("container")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(container)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(24, 20, 24, 20)
        inner.setSpacing(12)

        # Icon + Title
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        icon_lbl = QLabel("\U0001F6D1")
        icon_lbl.setStyleSheet(f"font-size: 24px; background: transparent;")
        title_row.addWidget(icon_lbl)
        _what = f"`{self._tool_name}`" if self._tool_name else "the tool call"
        title = QLabel("Interrupt the running tool call?")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_TEXT}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        inner.addLayout(title_row)

        # Description — says what actually happens. The old copy ("Stop ongoing
        # work? / Write a reason for the Agent to exit") read like it killed the
        # whole turn, when it stops only the step that is executing right now:
        # the partial output is KEPT, handed to the agent with your reason, and
        # the agent then wraps up on its own.
        desc = QLabel(
            f"This stops {_what} the agent is executing right now — not the "
            "whole conversation. Whatever it produced so far is kept and handed "
            "back to the agent along with your reason, and it wraps up from there."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 12px; color: {_MUTED}; background: transparent; "
            f"padding: 0px; margin: 0px;")
        inner.addWidget(desc)

        # Reason input
        self._reason_input = QPlainTextEdit()
        self._reason_input.setPlaceholderText(
            "Why? e.g. \"wrong folder — stop and re-check the path\" (optional)")
        self._reason_input.setFixedHeight(70)
        self._reason_input.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
                color: {_TEXT};
            }}
            QPlainTextEdit:focus {{ border-color: {_ACCENT}; }}
        """)
        inner.addWidget(self._reason_input)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        _BTN_CANCEL = f"""
            QPushButton {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_TEXT};
            }}
            QPushButton:hover {{ background-color: {_ELEV_HOVER}; border-color: {_ACCENT}; }}
        """
        _BTN_KILL = f"""
            QPushButton {{
                background-color: {_RED_FILL};
                border: 1px solid {_RED};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_RED};
            }}
            QPushButton:hover {{ background-color: {_RED_HOVER}; }}
        """

        cancel_btn = QPushButton("Let it finish")
        cancel_btn.setStyleSheet(_BTN_CANCEL)
        cancel_btn.setToolTip("Close this and leave the agent running.")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        # "Kill & Exit" overstated it — nothing is killed but the current step,
        # and the agent is not exited, it is told to wrap up.
        kill_btn = QPushButton("Interrupt the tool call")
        kill_btn.setStyleSheet(_BTN_KILL)
        kill_btn.setToolTip(
            "Stop the step that is running now. Partial output is kept and "
            "given to the agent with your reason.")
        kill_btn.clicked.connect(self._confirm)
        btn_row.addWidget(kill_btn)

        inner.addLayout(btn_row)

        # Center on the chat window / primary screen (never the floating widget)
        from systema.ui.dialogs.dialog_utils import center_on_primary
        QTimer.singleShot(0, lambda: center_on_primary(self))

    def _confirm(self):
        self._reason = self._reason_input.toPlainText().strip()
        self.accept()

    @property
    def reason_text(self):
        return self._reason
