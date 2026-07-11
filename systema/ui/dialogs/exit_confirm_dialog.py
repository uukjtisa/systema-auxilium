"""
ui/dialogs/exit_confirm_dialog.py
Exit Confirm Dialog - Shown when Restart/Shutdown is requested while the
assistant is still busy (generating a response, running work mode, or
speaking). Cancel keeps everything running; Confirm gracefully stops all
activity first, then proceeds with the restart/shutdown.
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QWidget)
from PyQt6.QtCore import Qt, QTimer


class ExitConfirmDialog(QDialog):
    """Frameless Cancel/Confirm modal shown before restart/shutdown while busy.

    exec() returns truthy (Accepted) when the user confirms.
    """

    def __init__(self, action, reason, parent=None):
        """action: 'Restart' or 'Shutdown'; reason: human-readable busy reason."""
        super().__init__(parent)
        self._init_ui(action, reason)

    def _init_ui(self, action, reason):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Dialog
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(540, 190)
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
        inner.setSpacing(14)

        title = QLabel(f"{action} while the assistant is busy?")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_TEXT}; background: transparent;")
        inner.addWidget(title)

        desc = QLabel(
            f"The assistant is still active: {reason}.\n"
            f"Stop everything and {action.lower()} anyway?"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size: 12px; color: {_MUTED}; background: transparent; "
            f"padding: 0px; margin: 0px;")
        inner.addWidget(desc)

        inner.addStretch()

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
        _BTN_CONFIRM = f"""
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
        cancel_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = QPushButton(f"Stop and {action}")
        confirm_btn.setStyleSheet(_BTN_CONFIRM)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        inner.addLayout(btn_row)

        from systema.ui.dialogs.dialog_utils import center_on_primary
        QTimer.singleShot(0, lambda: center_on_primary(self))
