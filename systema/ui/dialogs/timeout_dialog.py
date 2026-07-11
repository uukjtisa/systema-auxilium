"""
ui/timeout_dialog.py
Timeout Dialog - Shown when tool execution exceeds the configured timeout
Prompts user to extend or kill the running code.
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QWidget, QSpinBox, QPlainTextEdit)
from PyQt6.QtCore import Qt, QTimer


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

        _BASE    = "#0D1117"
        _SURFACE = "#161B22"
        _ELEV    = "#21262D"
        _BORDER  = "#30363D"
        _ACCENT  = "#58A6FF"
        _TEXT    = "#E6EDF3"
        _MUTED   = "#8B949E"
        _RED     = "#F85149"

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
            QPushButton:hover {{ background-color: #2D333B; border-color: {_ACCENT}; }}
        """
        _BTN_KILL = f"""
            QPushButton {{
                background-color: #2D1517;
                border: 1px solid {_RED};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_RED};
            }}
            QPushButton:hover {{ background-color: #3D1A1C; }}
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
    Auto-dismisses via 200ms poll when work_code_running goes False.
    Returns reason_text on accept."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reason = ""
        self._init_ui()

    def _init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(540, 260)
        self.setModal(True)

        _SURFACE = "#161B22"
        _ELEV    = "#21262D"
        _BORDER  = "#30363D"
        _ACCENT  = "#58A6FF"
        _TEXT    = "#E6EDF3"
        _MUTED   = "#8B949E"
        _RED     = "#F85149"

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
        title = QLabel("Stop ongoing work?")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_TEXT}; background: transparent;")
        title_row.addWidget(title)
        title_row.addStretch()
        inner.addLayout(title_row)

        # Description
        desc = QLabel(
            "(Optional) Write a reason for the Agent to exit."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 12px; color: {_MUTED}; background: transparent; "
            f"padding: 0px; margin: 0px;")
        inner.addWidget(desc)

        # Reason input
        self._reason_input = QPlainTextEdit()
        self._reason_input.setPlaceholderText("Why did you interrupt? (optional)")
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
            QPushButton:hover {{ background-color: #2D333B; border-color: {_ACCENT}; }}
        """
        _BTN_KILL = f"""
            QPushButton {{
                background-color: #2D1517;
                border: 1px solid {_RED};
                border-radius: 6px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 500;
                color: {_RED};
            }}
            QPushButton:hover {{ background-color: #3D1A1C; }}
        """

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN_CANCEL)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        kill_btn = QPushButton("Kill & Exit")
        kill_btn.setStyleSheet(_BTN_KILL)
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
