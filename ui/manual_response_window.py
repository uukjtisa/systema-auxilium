"""
ui/manual_response_window.py
ManualResponseWindow — popup that lets the user type the AI's response manually.

Normal mode  : just shows a response input and Submit / Cancel.
Work mode    : left panel = system output viewer/editor (read-only by default,
               but fully editable so you can tweak results before they're sent
               back to the AI), right panel = response input.
"""

import threading
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QVBoxLayout, QFrame, QLabel,
    QPushButton, QTextEdit, QSplitter, QWidget
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from ui.base_window import BaseWindow


# ── Shared stylesheet tokens ──────────────────────────────────────────────────
_BG          = "#161B22"
_HEADER_BG   = "#0D1117"
_SURFACE     = "#21262D"
_SURFACE2    = "#2D333B"
_BORDER      = "#30363D"
_ACCENT      = "#58A6FF"
_ACCENT_HOVER= "#388BFD"
_TEXT        = "#E8EAED"
_MUTED       = "#8B949E"
_TAG_BG      = "#1F3A5F"
_TAG_TEXT    = "#79B8FF"
_DANGER      = "#F85149"
_DANGER_HOVER= "#DA3633"

_WINDOW_SS = f"""
    QWidget {{
        background-color: {_BG};
        color: {_TEXT};
        font-family: 'Segoe UI', Arial, sans-serif;
    }}
"""

_TEXTEDIT_SS = f"""
    QTextEdit {{
        background-color: {_SURFACE};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        padding: 10px;
        color: {_TEXT};
        font-size: 13px;
        line-height: 1.6;
        selection-background-color: {_ACCENT};
    }}
    QTextEdit:focus {{
        border-color: {_ACCENT};
    }}
    QScrollBar:vertical {{
        background: {_SURFACE};
        width: 6px;
        border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background: {_BORDER};
        border-radius: 3px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
"""


def _btn(text, accent=False, danger=False):
    """Build a styled QPushButton."""
    btn = QPushButton(text)
    if danger:
        bg, bg_h = _DANGER, _DANGER_HOVER
    elif accent:
        bg, bg_h = _ACCENT, _ACCENT_HOVER
    else:
        bg, bg_h = _SURFACE2, "#3D444D"
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {bg};
            border: {'none' if accent or danger else f'1px solid {_BORDER}'};
            border-radius: 7px;
            padding: 8px 20px;
            color: {'white' if accent or danger else _TEXT};
            font-size: 13px;
            font-weight: {'600' if accent else '400'};
        }}
        QPushButton:hover {{
            background-color: {bg_h};
        }}
        QPushButton:pressed {{
            background-color: {bg};
        }}
    """)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class ManualResponseWindow(BaseWindow):
    """
    Pop-up for the 'manual' provider.

    Parameters
    ----------
    context       : str   — the user's last message (shown as context)
    work_mode     : bool  — whether the AI was in work mode
    work_output   : str   — the system tool output (only used if work_mode=True)
    result_holder : list  — single-element list; window puts the response here
    done_event    : threading.Event — released when the user submits / cancels
    """

    def __init__(self, context: str, work_mode: bool,
                 work_output: str, result_holder: list,
                 done_event: threading.Event):
        super().__init__()
        self._init_chrome_state()

        self._context      = context
        self._work_mode    = work_mode
        self._work_output  = work_output
        self._result       = result_holder
        self._done         = done_event
        self._submitted    = False

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setStyleSheet(_WINDOW_SS)

        if work_mode:
            self.setMinimumSize(920, 560)
            self.resize(960, 600)
        else:
            self.setMinimumSize(520, 400)
            self.resize(580, 460)

        self._build_ui()
        self.create_resize_handles()

        # Centre on screen
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Outer card frame
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)
        root.addWidget(card)

        # ── Header ────────────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(50)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {_HEADER_BG};
                border-bottom: 1px solid {_BORDER};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
        """)
        header.mousePressEvent   = self.header_mouse_press
        header.mouseMoveEvent    = self.header_mouse_move
        header.mouseReleaseEvent = self.header_mouse_release
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 12, 0)

        # Mode tag
        tag_text = "📋  System Message" if self._work_mode else "✏️  Manual Response"
        tag = QLabel(tag_text)
        tag.setStyleSheet(f"""
            QLabel {{
                background-color: {_TAG_BG};
                color: {_TAG_TEXT};
                font-size: 11px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 10px;
                border: 1px solid #1A4A8A;
            }}
        """)
        h_lay.addWidget(tag)
        h_lay.addStretch()

        title = QLabel("Manual Provider")
        title.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        h_lay.addWidget(title)
        h_lay.addSpacing(12)

        close_btn = _btn("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {_MUTED};
                font-size: 14px;
                border-radius: 14px;
            }}
            QPushButton:hover {{ background: rgba(248,81,73,0.2); color: {_DANGER}; }}
        """)
        close_btn.clicked.connect(self._cancel)
        h_lay.addWidget(close_btn)
        card_lay.addWidget(header)

        # ── Body ──────────────────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(16, 14, 16, 14)
        body_lay.setSpacing(10)

        # Context strip (last user message)
        ctx_frame = QFrame()
        ctx_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-left: 3px solid {_ACCENT};
                border-radius: 6px;
                padding: 2px;
            }}
        """)
        ctx_lay = QHBoxLayout(ctx_frame)
        ctx_lay.setContentsMargins(10, 6, 10, 6)
        ctx_label = QLabel(f"<b style='color:{_MUTED};font-size:11px'>USER:</b> "
                           f"<span style='color:{_TEXT};font-size:12px'>"
                           f"{self._context[:160].replace('<','&lt;')}"
                           f"{'…' if len(self._context) > 160 else ''}</span>")
        ctx_label.setWordWrap(True)
        ctx_label.setStyleSheet("background: transparent; border: none;")
        ctx_lay.addWidget(ctx_label)
        body_lay.addWidget(ctx_frame)

        # Main content area
        if self._work_mode:
            body_lay.addLayout(self._build_split_layout())
        else:
            body_lay.addLayout(self._build_simple_layout())

        # ── Footer buttons ────────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.addStretch()

        cancel_btn = _btn("Cancel")
        cancel_btn.clicked.connect(self._cancel)
        footer.addWidget(cancel_btn)
        footer.addSpacing(8)

        self._submit_btn = _btn("Submit Response  ↵", accent=True)
        self._submit_btn.clicked.connect(self._submit)
        footer.addWidget(self._submit_btn)

        body_lay.addLayout(footer)
        card_lay.addWidget(body)

    def _build_simple_layout(self):
        """Normal mode — just a response text field."""
        lay = QVBoxLayout()
        lay.setSpacing(6)

        lbl = QLabel("Your response:")
        lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        lay.addWidget(lbl)

        self._response_field = QTextEdit()
        self._response_field.setPlaceholderText("Type the AI's response here…")
        self._response_field.setStyleSheet(_TEXTEDIT_SS)
        self._response_field.setFont(QFont("Consolas, Courier New", 13))
        self._response_field.setAcceptRichText(False)
        lay.addWidget(self._response_field, stretch=1)

        # Ctrl+Enter to submit
        self._response_field.installEventFilter(self)

        return lay

    def _build_split_layout(self):
        """Work mode — splitter: left = system output viewer, right = response field."""
        lay = QVBoxLayout()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle { background: #30363D; width: 2px; }
        """)

        # LEFT — system output panel
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 6, 0)
        left_lay.setSpacing(6)

        sys_header = QHBoxLayout()
        sys_lbl = QLabel("📋  System Message")
        sys_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px; font-weight: 600; background:transparent;")
        sys_header.addWidget(sys_lbl)
        sys_header.addStretch()

        copy_btn = QPushButton("📋 Copy")
        copy_btn.setFixedHeight(24)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE2};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                padding: 0 10px;
                color: {_MUTED};
                font-size: 11px;
            }}
            QPushButton:hover {{ color: {_TEXT}; border-color: {_MUTED}; }}
        """)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sys_header.addWidget(copy_btn)
        left_lay.addLayout(sys_header)

        self._work_output_field = QTextEdit()
        self._work_output_field.setPlainText(self._work_output or "(no system output)")
        self._work_output_field.setStyleSheet(_TEXTEDIT_SS)
        self._work_output_field.setFont(QFont("Consolas", 11))
        left_lay.addWidget(self._work_output_field)

        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self._work_output_field.toPlainText())
        )

        splitter.addWidget(left)

        # RIGHT — response field
        right = QWidget()
        right.setStyleSheet("background: transparent;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(6, 0, 0, 0)
        right_lay.setSpacing(6)

        resp_lbl = QLabel("Your response:")
        resp_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px; font-weight: 600; background:transparent;")
        right_lay.addWidget(resp_lbl)

        self._response_field = QTextEdit()
        self._response_field.setPlaceholderText("Type the AI's response here…")
        self._response_field.setStyleSheet(_TEXTEDIT_SS)
        self._response_field.setFont(QFont("Consolas", 13))
        self._response_field.setAcceptRichText(False)
        self._response_field.installEventFilter(self)
        right_lay.addWidget(self._response_field)

        splitter.addWidget(right)
        splitter.setSizes([420, 480])

        lay.addWidget(splitter, stretch=1)
        return lay

    # ── Actions ───────────────────────────────────────────────────────────────

    def _submit(self):
        text = self._response_field.toPlainText().strip()
        if not text:
            self._response_field.setStyleSheet(
                _TEXTEDIT_SS + f"\nQTextEdit {{ border-color: {_DANGER}; }}"
            )
            QTimer.singleShot(1200, lambda: self._response_field.setStyleSheet(_TEXTEDIT_SS))
            return
        self._result.append(text)
        self._submitted = True
        self._done.set()
        self.close()

    def _cancel(self):
        self._result.append(None)
        self._done.set()
        self.close()

    def closeEvent(self, event):
        # If window is closed without submit/cancel, treat as cancel
        if not self._done.is_set():
            self._result.append(None)
            self._done.set()
        super().closeEvent(event)

    # ── Ctrl+Enter to submit ──────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        if (obj is self._response_field and
                event.type() == QEvent.Type.KeyPress):
            ke = event
            if (ke.key() == Qt.Key.Key_Return and
                    ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._submit()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
