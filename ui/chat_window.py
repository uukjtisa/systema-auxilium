"""
Chat Window - Modern conversation interface with Voice Support
Features:
- Voice input/output toggle
- Real-time voice status indicators
- Waveform visualization (optional)
- Voice device selection
- Automatic TTS for AI responses when voice is active
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel,
                             QFrame, QMenu, QScrollArea, QApplication,
                             QGraphicsOpacityEffect, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QRect, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PyQt6.QtGui import QAction, QCursor, QRegion
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from core.skill_manager import SkillManager as _SkillManagerType


_SK_SURFACE  = "#242424"
_SK_SURFACE2 = "#2A2A2A"
_SK_BORDER   = "#2E2E2E"
_SK_ACCENT   = "#5865F2"
_SK_TEXT     = "#E8EAED"
_SK_MUTED    = "#9AA0A6"


class _SkillRow(QWidget):
    """
    One skill row: two-line layout so the name always word-wraps cleanly.
      Line 1 — chevron + skill name (full width, word-wrap)
      Line 2 — badge  +  Load/Unload btn  +  delete btn  (right-aligned)
    """

    def __init__(self, skill: dict, skill_manager, parent=None):
        super().__init__(parent)
        self._skill = skill
        self._skill_manager = skill_manager
        self._expanded = False
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            f"QWidget {{ background-color: {_SK_SURFACE}; border-radius: 6px; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── clickable header ──────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet(f"""
            QWidget {{ background-color: {_SK_SURFACE}; border-radius: 6px; }}
            QWidget:hover {{ background-color: #2C2C2C; }}
        """)
        hdr_vl = QVBoxLayout(hdr)
        hdr_vl.setContentsMargins(8, 6, 8, 6)
        hdr_vl.setSpacing(4)

        # row 1: chevron + name
        r1 = QWidget()
        r1.setStyleSheet("background: transparent;")
        r1l = QHBoxLayout(r1)
        r1l.setContentsMargins(0, 0, 0, 0)
        r1l.setSpacing(5)

        self._chevron = QPushButton("▶")
        self._chevron.setFixedSize(14, 14)
        self._chevron.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                          color: {_SK_MUTED}; font-size: 8px; padding: 0; }}
            QPushButton:hover {{ color: {_SK_TEXT}; }}
        """)
        self._chevron.clicked.connect(self._toggle_expand)
        r1l.addWidget(self._chevron)

        display_name = self._skill['name'].replace('_', '_\u200b')
        name_lbl = QLabel(display_name)
        name_lbl.setWordWrap(True)
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name_lbl.setStyleSheet(
            f"color: {_SK_TEXT}; font-size: 11px; font-weight: 500; background: transparent;")
        r1l.addWidget(name_lbl, stretch=1)
        hdr_vl.addWidget(r1)

        # row 2: badge + load-btn + del-btn  (right-aligned)
        r2 = QWidget()
        r2.setStyleSheet("background: transparent;")
        r2l = QHBoxLayout(r2)
        r2l.setContentsMargins(19, 0, 0, 0)   # 19 = chevron width + spacing
        r2l.setSpacing(4)

        is_loaded = self._skill.get('is_loaded', False)

        self._badge = QLabel("● Loaded" if is_loaded else "○ Unloaded")
        self._badge.setStyleSheet(self._badge_style(is_loaded))
        r2l.addWidget(self._badge)

        r2l.addStretch(1)

        self._load_btn = QPushButton("Unload" if is_loaded else "Load")
        self._load_btn.setFixedSize(52, 22)
        self._load_btn.setStyleSheet(self._load_btn_style(is_loaded))
        self._load_btn.clicked.connect(self._toggle_load)
        r2l.addWidget(self._load_btn)

        del_btn = QPushButton("🗑")
        del_btn.setFixedSize(22, 22)
        del_btn.setToolTip(f"Delete '{self._skill['name']}'")
        del_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none; font-size: 12px;
                          color: {_SK_MUTED}; border-radius: 4px; }}
            QPushButton:hover {{ background-color: #3C1A1A; color: #FF6B6B; }}
        """)
        del_btn.clicked.connect(self._delete)
        r2l.addWidget(del_btn)

        hdr_vl.addWidget(r2)
        outer.addWidget(hdr)

        # ── expandable detail ─────────────────────────────────────────────────
        self._detail = QWidget()
        self._detail.setStyleSheet(
            f"background-color: {_SK_SURFACE};")
        self._detail.hide()
        dl = QVBoxLayout(self._detail)
        dl.setContentsMargins(14, 2, 14, 8)
        dl.setSpacing(3)

        if self._skill.get('description'):
            d = QLabel(self._skill['description'])
            d.setWordWrap(True)
            d.setMinimumWidth(0)
            d.setStyleSheet(f"color: {_SK_MUTED}; font-size: 10px; background-color: {_SK_SURFACE};")
            dl.addWidget(d)

        if self._skill.get('files'):
            fl = QLabel("\n".join(f"  📄 {f}" for f in self._skill['files'][:8]))
            fl.setWordWrap(True)
            fl.setMinimumWidth(0)
            fl.setStyleSheet(
                f"color: #5F6368; font-size: 9px; font-family: monospace; background-color: {_SK_SURFACE};")
            dl.addWidget(fl)

        outer.addWidget(self._detail)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _badge_style(self, loaded):
        if loaded:
            return ("QLabel { background-color: #1A2B1A; color: #4CAF50; "
                    "border-radius: 4px; font-size: 9px; padding: 1px 5px; }")
        return (f"QLabel {{ background-color: transparent; color: {_SK_MUTED}; "
                "font-size: 9px; padding: 1px 3px; }")

    def _load_btn_style(self, loaded):
        if loaded:
            return ("QPushButton { background-color: #3C1A1A; color: #C0392B; "
                    "border: 1px solid #C0392B; border-radius: 4px; font-size: 9px; padding: 0; }"
                    "QPushButton:hover { background-color: #4A2020; }")
        return ("QPushButton { background-color: #1A2B1A; color: #4CAF50; "
                "border: 1px solid #4CAF50; border-radius: 4px; font-size: 9px; padding: 0; }"
                "QPushButton:hover { background-color: #223322; }")

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._chevron.setText("▼" if self._expanded else "▶")
        self._detail.setVisible(self._expanded)

    def _toggle_load(self):
        name = self._skill['name']
        if self._skill_manager.is_loaded(name):
            ok, _ = self._skill_manager.unload_skill(name)
        else:
            ok, _ = self._skill_manager.load_skill(name)
        if ok:
            new_state = not self._skill.get('is_loaded', False)
            self._skill['is_loaded'] = new_state
            self._badge.setText("● Loaded" if new_state else "○ Unloaded")
            self._badge.setStyleSheet(self._badge_style(new_state))
            self._load_btn.setText("Unload" if new_state else "Load")
            self._load_btn.setStyleSheet(self._load_btn_style(new_state))

    def _delete(self):
        self._skill_manager.delete_skill(self._skill['name'])


class SkillsSidebarSection(QWidget):
    """Collapsible ⚡ Skills block — lives inside the sidebar layout."""

    def __init__(self, skill_manager, parent=None):
        super().__init__(parent)
        self._skill_manager = skill_manager
        self._expanded = False          # default collapsed at startup
        self._build_ui()
        skill_manager.skills_changed.connect(self.refresh)
        skill_manager.loaded_skills_changed.connect(self.refresh)
        self.refresh()

    def _build_ui(self):
        self.setStyleSheet("background: transparent;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # section header — click to expand/collapse
        hdr = QWidget()
        hdr.setStyleSheet("""
            QWidget { background-color: transparent; border-radius: 4px; }
            QWidget:hover { background-color: #222222; }
        """)
        hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(4, 6, 4, 6)
        hl.setSpacing(6)

        self._sec_chevron = QLabel("▶")   # starts collapsed
        self._sec_chevron.setStyleSheet(
            f"color: {_SK_MUTED}; font-size: 9px; background: transparent;")
        hl.addWidget(self._sec_chevron)

        sec_lbl = QLabel("⚡ Skills")
        sec_lbl.setStyleSheet(
            f"color: {_SK_TEXT}; font-size: 12px; font-weight: 600; background: transparent;")
        hl.addWidget(sec_lbl, stretch=1)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(
            f"color: {_SK_MUTED}; font-size: 9px; background: transparent;")
        hl.addWidget(self._count_lbl)

        hdr.mousePressEvent = lambda e: self._toggle_section()
        root.addWidget(hdr)

        # collapsible body — hidden by default
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body.hide()      # starts hidden (collapsed)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        bl.addWidget(self._rows_widget)

        # new-skill input row
        add_w = QWidget()
        add_w.setStyleSheet("background: transparent;")
        al = QHBoxLayout(add_w)
        al.setContentsMargins(0, 2, 0, 0)
        al.setSpacing(4)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("New skill name…")
        self._name_input.setMinimumWidth(0)
        self._name_input.setStyleSheet(f"""
            QLineEdit {{ background-color: {_SK_SURFACE2}; border: 1px solid {_SK_BORDER};
                        border-radius: 5px; color: {_SK_TEXT}; font-size: 10px; padding: 4px 7px; }}
            QLineEdit:focus {{ border-color: {_SK_ACCENT}; }}
        """)
        self._name_input.returnPressed.connect(self._create_skill)
        al.addWidget(self._name_input, stretch=1)

        create_btn = QPushButton("＋")
        create_btn.setFixedSize(26, 26)
        create_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {_SK_ACCENT}; border: none; border-radius: 5px;
                          color: white; font-size: 14px; }}
            QPushButton:hover {{ background-color: #4752C4; }}
        """)
        create_btn.clicked.connect(self._create_skill)
        al.addWidget(create_btn)
        bl.addWidget(add_w)

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        open_btn = QPushButton("📁  Open skills folder")
        open_btn.setMinimumWidth(0)
        open_btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; border: 1px dashed {_SK_BORDER};
                          border-radius: 5px; color: {_SK_MUTED}; font-size: 10px;
                          padding: 5px; text-align: left; }}
            QPushButton:hover {{ border-color: #5F6368; color: {_SK_TEXT}; }}
        """)
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._skill_manager.skills_dir))))
        bl.addWidget(open_btn)

        root.addWidget(self._body)

    def _toggle_section(self):
        self._expanded = not self._expanded
        self._sec_chevron.setText("▼" if self._expanded else "▶")
        self._body.setVisible(self._expanded)

    def refresh(self):
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        skills = self._skill_manager.get_skills()
        loaded_count = sum(1 for s in skills if s.get('is_loaded'))
        self._count_lbl.setText(f"{len(skills)} · {loaded_count} loaded")

        if not skills:
            empty = QLabel("No skills installed.\nCreate one below ↓")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"color: {_SK_MUTED}; font-size: 10px; padding: 12px;")
            self._rows_layout.addWidget(empty)
        else:
            for skill in skills:
                self._rows_layout.addWidget(_SkillRow(skill, self._skill_manager))

    def _create_skill(self):
        name = self._name_input.text().strip()
        if name:
            self._skill_manager.create_skill_template(name)
            self._name_input.clear()


# Backwards-compat alias (used by controller.py skill_card messages)
SkillsPanel = SkillsSidebarSection


import re
import markdown2
import os
import json
import threading
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# ANIMATION TIMING CONSTANTS
# Tweak these values to adjust the feel of every animation in the chat window.
# ═══════════════════════════════════════════════════════════════════════════════

# --- Window ---
ANIM_WINDOW_FADE_IN_MS       = 340    # Chat window fade-in when shown (ms)

# --- Sidebar ---
ANIM_SIDEBAR_SLIDE_MS        = 360    # Sidebar slide in / out (ms)
SIDEBAR_DEFAULT_W            = 240    # Default sidebar width (px)
SIDEBAR_MIN_W                = 200    # Minimum sidebar width when dragging
SIDEBAR_MAX_W                = 420    # Maximum sidebar width when dragging

# --- Messages ---
ANIM_MSG_IN_HEIGHT_MS        = 480    # Message pop-in: height expand (ms)
ANIM_MSG_IN_FADE_MS          = 380    # Message pop-in: fade-in (ms)
ANIM_MSG_IN_OVERSHOOT_PX     = 120    # Extra pixels past natural height (OutBack spring feel)
ANIM_MSG_OUT_FADE_MS         = 220    # Message pop-out: fade-out (ms)
ANIM_MSG_OUT_HEIGHT_MS       = 280    # Message pop-out: height collapse (ms)

# --- Scroll (animated jumps, e.g. scroll-to-new-message) ---
ANIM_SCROLL_MIN_MS           = 180    # Shortest animated scroll duration (ms)
ANIM_SCROLL_MAX_MS           = 600    # Longest animated scroll duration (ms)

# --- Inertia scroll (mouse-wheel / trackpad momentum) ---
ANIM_INERTIA_INTERVAL_MS     = 14     # Tick interval (~70 fps)
ANIM_INERTIA_FRICTION        = 0.86   # Velocity multiplier per tick (lower = snappier stop)
ANIM_INERTIA_MIN_VELOCITY    = 0.5    # Stop threshold (px / tick)
ANIM_INERTIA_SCALE           = 0.38   # Wheel angleDelta → velocity scale
ANIM_INERTIA_MAX_VELOCITY    = 1400   # Max speed cap (px / tick)

# --- UI feedback timers ---
ANIM_COPY_FEEDBACK_MS        = 1500   # "✓ Copied!" button state duration (ms)
ANIM_STATUS_CLEAR_MS         = 2000   # Status-bar message clear delay (ms)

# ═══════════════════════════════════════════════════════════════════════════════

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
_APP_ROOT = Path(__file__).resolve().parent.parent
# ─────────────────────────────────────────────────────────────────────────────


class MultiLineInput(QTextEdit):
    """Custom text input with Shift+Enter support"""
    enterPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Send a message... (Shift+Enter for new line)")
        self.setMinimumHeight(24)
        self.setMaximumHeight(400)
        self.manual_resize = False

        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.textChanged.connect(self.adjust_height)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.enterPressed.emit()
                event.accept()
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        """Override paste to handle file paths"""
        if source.hasUrls():
            # Handle file drops/pastes
            for url in source.urls():
                file_path = url.toLocalFile()
                if file_path:
                    # Get the chat window to use its helper methods
                    chat_window = self.get_chat_window()
                    if chat_window:
                        cleaned_path = chat_window.clean_file_path(file_path)

                        # Check if Puter + image
                        if chat_window.controller.get_ai_provider() == 'puter':
                            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                            if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                                chat_window.attached_image = cleaned_path
                                chat_window.add_system_message(
                                    f"📎 **Image Ready:** {cleaned_path}\n\nType your message and press Enter."
                                )
                                return

                        # Quote non-image paths
                        if chat_window.should_quote_path(cleaned_path):
                            cleaned_path = f'"{cleaned_path}"'

                        self.insertPlainText(cleaned_path)
                    else:
                        # Fallback if can't find chat window
                        self.insertPlainText(file_path)
            return
        elif source.hasText():
            text = source.text().strip()

            # Get the chat window to use its helper methods
            chat_window = self.get_chat_window()
            if chat_window:
                cleaned_path = chat_window.clean_file_path(text)

                # Check if it's a valid file path
                if os.path.exists(cleaned_path):
                    # Check if Puter + image
                    if chat_window.controller.get_ai_provider() == 'puter':
                        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                        if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                            chat_window.attached_image = cleaned_path
                            chat_window.add_system_message(
                                f"📎 **Image Ready:** {cleaned_path}\n\nType your message and press Enter."
                            )
                            return

                    # Quote non-image paths
                    if chat_window.should_quote_path(cleaned_path):
                        cleaned_path = f'"{cleaned_path}"'

                    self.insertPlainText(cleaned_path)
                    return

            # If not a file path, paste normally
            super().insertFromMimeData(source)
        else:
            super().insertFromMimeData(source)

    def get_chat_window(self):
        """Get the ChatWindow parent"""
        parent = self.parent()
        while parent:
            if parent.__class__.__name__ == 'ChatWindow':
                return parent
            parent = parent.parent()
        return None

    def adjust_height(self):
        if self.manual_resize:
            return
        doc_height = self.document().size().height()
        new_height = min(max(int(doc_height) + 10, 24), self.maximumHeight())
        self.setFixedHeight(new_height)
        if self.parent():
            self.parent().updateGeometry()


class ResizableInput(QWidget):
    """Input container with manual resize handle"""
    enterPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_height = 24
        self.max_height = 400
        self.is_resizing = False
        self.resize_start_y = 0
        self.resize_start_height = 0

        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        self.resize_handle = QLabel("⋮⋮⋮")
        self.resize_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.resize_handle.setFixedHeight(8)
        self.resize_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self.resize_handle.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #4A4A4A;
                font-size: 6px;
                letter-spacing: 1px;
            }
            QLabel:hover {
                background-color: rgba(255, 255, 255, 0.1);
                color: #9AA0A6;
            }
        """)
        self.resize_handle.installEventFilter(self)
        layout.addWidget(self.resize_handle)

        self.text_input = MultiLineInput()
        self.text_input.enterPressed.connect(self.enterPressed.emit)
        self.text_input.setFixedHeight(self.min_height)
        layout.addWidget(self.text_input)

    def eventFilter(self, obj, event):
        if obj == self.resize_handle:
            if event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.is_resizing = True
                    self.resize_start_y = event.globalPosition().y()
                    self.resize_start_height = self.text_input.height()
                    self.text_input.manual_resize = True
                    return True
            elif event.type() == event.Type.MouseMove and self.is_resizing:
                delta = self.resize_start_y - event.globalPosition().y()
                new_height = self.resize_start_height + delta
                new_height = max(self.min_height, min(self.max_height, new_height))
                self.text_input.setFixedHeight(int(new_height))
                self.updateGeometry()
                if self.parent():
                    self.parent().updateGeometry()
                    if self.parent().parent():
                        self.parent().parent().updateGeometry()
                return True
            elif event.type() == event.Type.MouseButtonRelease:
                self.is_resizing = False
                return True
        return super().eventFilter(obj, event)

    def toPlainText(self):
        return self.text_input.toPlainText()

    def clear(self):
        """Clear input and maintain manual resize state if needed"""
        current_manual = self.text_input.manual_resize
        current_height = self.text_input.height() if current_manual else self.min_height

        self.text_input.clear()

        # Only reset if not manually resized
        if not current_manual:
            self.text_input.setFixedHeight(self.min_height)
        else:
            self.text_input.setFixedHeight(current_height)

        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        self.text_input.setEnabled(enabled)

    def setPlaceholderText(self, text):
        self.text_input.setPlaceholderText(text)


class CodeSyntaxHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for code blocks - supports multiple languages"""

    def __init__(self, parent, language):
        super().__init__(parent)
        self.language = language.lower()
        self.highlighting_rules = []

        # Define color scheme (similar to ChatGPT/VS Code Dark+)
        self.colors = {
            'keyword': '#C586C0',  # Purple for keywords
            'builtin': '#4EC9B0',  # Teal for built-in types
            'string': '#CE9178',  # Orange for strings
            'comment': '#6A9955',  # Green for comments
            'function': '#DCDCAA',  # Yellow for functions
            'number': '#B5CEA8',  # Light green for numbers
            'operator': '#D4D4D4',  # Light gray for operators
            'class': '#4EC9B0',  # Teal for class names
            'decorator': '#4EC9B0',  # Teal for decorators
        }

        self.setup_rules()

    def setup_rules(self):
        """Setup syntax highlighting rules based on language"""

        # Python keywords
        if self.language == 'python':
            keywords = [
                'def', 'class', 'import', 'from', 'as', 'if', 'elif', 'else',
                'for', 'while', 'break', 'continue', 'return', 'yield', 'pass',
                'try', 'except', 'finally', 'raise', 'with', 'assert', 'lambda',
                'and', 'or', 'not', 'in', 'is', 'None', 'True', 'False', 'async',
                'await', 'global', 'nonlocal', 'del'
            ]

            builtins = [
                'int', 'str', 'float', 'bool', 'list', 'dict', 'tuple', 'set',
                'object', 'type', 'super', 'self', 'len', 'range', 'print',
                'input', 'open', 'enumerate', 'zip', 'map', 'filter'
            ]

            # Keywords
            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Built-in types and functions
            builtin_format = QTextCharFormat()
            builtin_format.setForeground(QColor(self.colors['builtin']))
            for word in builtins:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), builtin_format))

            # Strings (single and double quotes)
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
            self.highlighting_rules.append((re.compile(r'""".*?"""', re.DOTALL), string_format))
            self.highlighting_rules.append((re.compile(r"'''.*?'''", re.DOTALL), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'#[^\n]*'), comment_format))

            # Function definitions
            function_format = QTextCharFormat()
            function_format.setForeground(QColor(self.colors['function']))
            self.highlighting_rules.append((re.compile(r'\bdef\s+(\w+)'), function_format))

            # Class names
            class_format = QTextCharFormat()
            class_format.setForeground(QColor(self.colors['class']))
            self.highlighting_rules.append((re.compile(r'\bclass\s+(\w+)'), class_format))

            # Decorators
            decorator_format = QTextCharFormat()
            decorator_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'@\w+'), decorator_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

        # JavaScript/TypeScript
        elif self.language in ['javascript', 'js', 'typescript', 'ts']:
            keywords = [
                'var', 'let', 'const', 'function', 'return', 'if', 'else',
                'for', 'while', 'do', 'switch', 'case', 'break', 'continue',
                'try', 'catch', 'finally', 'throw', 'new', 'this', 'typeof',
                'instanceof', 'in', 'of', 'class', 'extends', 'super', 'import',
                'export', 'default', 'async', 'await', 'yield', 'null', 'undefined',
                'true', 'false'
            ]

            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))
            self.highlighting_rules.append((re.compile(r'`[^`]*`'), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

        # Java
        elif self.language == 'java':
            keywords = [
                'abstract', 'assert', 'boolean', 'break', 'byte', 'case', 'catch',
                'char', 'class', 'const', 'continue', 'default', 'do', 'double',
                'else', 'enum', 'extends', 'final', 'finally', 'float', 'for',
                'goto', 'if', 'implements', 'import', 'instanceof', 'int', 'interface',
                'long', 'native', 'new', 'package', 'private', 'protected', 'public',
                'return', 'short', 'static', 'strictfp', 'super', 'switch', 'synchronized',
                'this', 'throw', 'throws', 'transient', 'try', 'void', 'volatile', 'while',
                'true', 'false', 'null'
            ]

            builtins = [
                'String', 'Integer', 'Boolean', 'Double', 'Float', 'Long', 'Short',
                'Byte', 'Character', 'Object', 'System', 'Math', 'List', 'ArrayList',
                'HashMap', 'HashSet', 'Map', 'Set', 'Collection', 'Arrays'
            ]

            # Keywords
            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Built-in classes
            builtin_format = QTextCharFormat()
            builtin_format.setForeground(QColor(self.colors['builtin']))
            for word in builtins:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), builtin_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Annotations
            decorator_format = QTextCharFormat()
            decorator_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'@\w+'), decorator_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*[fFdDlL]?\b'), number_format))

        # C/C++
        elif self.language in ['c', 'cpp', 'c++']:
            keywords = [
                'int', 'void', 'char', 'float', 'double', 'long', 'short',
                'unsigned', 'signed', 'if', 'else', 'for', 'while', 'do',
                'switch', 'case', 'break', 'continue', 'return', 'struct',
                'typedef', 'enum', 'union', 'const', 'static', 'extern',
                'auto', 'register', 'volatile', 'sizeof', 'class', 'public',
                'private', 'protected', 'virtual', 'namespace', 'using',
                'template', 'typename', 'bool', 'true', 'false', 'nullptr'
            ]

            keyword_format = QTextCharFormat()
            keyword_format.setForeground(QColor(self.colors['keyword']))
            keyword_format.setFontWeight(QFont.Weight.Bold)
            for word in keywords:
                self.highlighting_rules.append((re.compile(r'\b' + word + r'\b'), keyword_format))

            # Preprocessor directives
            preprocessor_format = QTextCharFormat()
            preprocessor_format.setForeground(QColor(self.colors['decorator']))
            self.highlighting_rules.append((re.compile(r'#\w+'), preprocessor_format))

            # Strings
            string_format = QTextCharFormat()
            string_format.setForeground(QColor(self.colors['string']))
            self.highlighting_rules.append((re.compile(r'"[^"\\]*(\\.[^"\\]*)*"'), string_format))
            self.highlighting_rules.append((re.compile(r"'[^'\\]*(\\.[^'\\]*)*'"), string_format))

            # Comments
            comment_format = QTextCharFormat()
            comment_format.setForeground(QColor(self.colors['comment']))
            comment_format.setFontItalic(True)
            self.highlighting_rules.append((re.compile(r'//[^\n]*'), comment_format))
            self.highlighting_rules.append((re.compile(r'/\*.*?\*/', re.DOTALL), comment_format))

            # Numbers
            number_format = QTextCharFormat()
            number_format.setForeground(QColor(self.colors['number']))
            self.highlighting_rules.append((re.compile(r'\b\d+\.?\d*\b'), number_format))

    def highlightBlock(self, text):
        """Apply syntax highlighting to a block of text"""
        for pattern, format in self.highlighting_rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), format)


class CodeBlockWidget(QWidget):
    """Widget for displaying code blocks with syntax highlighting and resize handles"""

    def __init__(self, language, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language

        # Collapse state - START COLLAPSED
        self.is_expanded = False

        # Resize state (both vertical and horizontal)
        self.is_resizing_vertical = False
        self.is_resizing_horizontal = False
        self.resize_start_y = 0
        self.resize_start_x = 0
        self.resize_start_height = 0
        self.resize_start_width = 0
        self.min_height = 60
        self.max_height = 800
        self.min_width = 300
        self.max_width = 1200

        # Use horizontal layout to accommodate right resize handle
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 6, 0, 0)
        main_layout.setSpacing(0)

        # Vertical content container
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Main container ─────────────────────────────────────────────────────
        self.main_container = QFrame()
        self.main_container.setObjectName("codeBlock")
        self.main_container.setStyleSheet("""
            QFrame#codeBlock {
                background: #1E1E1E;
                border: 1px solid rgba(168, 199, 250, 0.15);
                border-radius: 8px;
            }
        """)
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Header ─────────────────────────────────────────────────────────────
        header = QFrame()
        header.setObjectName("codeHeader")
        header.setFixedHeight(75)
        header.setStyleSheet("""
            QFrame#codeHeader {
                background-color: #252525;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid rgba(168, 199, 250, 0.1);
                border-left: none;
                border-right: none;
                border-top: none;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 7, 10, 7)
        header_layout.setSpacing(8)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        display_lang = language.upper() if language and language.lower() != 'text' else 'TEXT' #SHOW "TEXT" INSTEAD OF "CODE"
        lang_label = QLabel(display_lang)
        lang_label.setObjectName("codeLangLabel")
        lang_label.setStyleSheet("""
            QLabel#codeLangLabel {
                background: transparent;
                color: #9CDCFE;
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 1px;
                border: none;
            }
        """)
        header_layout.addWidget(lang_label)
        header_layout.addStretch()

        # Toggle button (expand/collapse)
        self.toggle_btn = QPushButton("▶")  # Start with right arrow (collapsed)
        self.toggle_btn.setObjectName("codeToggleBtn")
        self.toggle_btn.setStyleSheet("""
            QPushButton#codeToggleBtn {
                background-color: rgba(168, 199, 250, 0.08);
                border: 1px solid rgba(168, 199, 250, 0.2);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                color: #9CDCFE;
            }
            QPushButton#codeToggleBtn:hover {
                background-color: rgba(168, 199, 250, 0.15);
                border-color: rgba(168, 199, 250, 0.35);
            }
            QPushButton#codeToggleBtn:pressed {
                background-color: rgba(168, 199, 250, 0.25);
            }
        """)
        self.toggle_btn.clicked.connect(self.toggle_expand)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.toggle_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Copy button
        self.copy_btn = QPushButton("📋")
        self.copy_btn.setObjectName("codeCopyBtn")
        self.copy_btn.setStyleSheet("""
            QPushButton#codeCopyBtn {
                background-color: rgba(168, 199, 250, 0.08);
                border: 1px solid rgba(168, 199, 250, 0.2);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                color: #9CDCFE;
            }
            QPushButton#codeCopyBtn:hover {
                background-color: rgba(168, 199, 250, 0.15);
                border-color: rgba(168, 199, 250, 0.35);
            }
            QPushButton#codeCopyBtn:pressed {
                background-color: rgba(168, 199, 250, 0.25);
            }
        """)
        self.copy_btn.clicked.connect(self.copy_code)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.copy_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        container_layout.addWidget(header)

        # ── Scrollable code area ───────────────────────────────────────────────
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("codeScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea#codeScrollArea {
                background: #1E1E1E;
                border: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
            QScrollBar:horizontal {
                background: #252525;
                height: 7px;
                border: none;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal {
                background: #4A4A4A;
                border-radius: 3px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #5E5E5E;
            }
            QScrollBar:vertical {
                background: #252525;
                width: 7px;
                border: none;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #4A4A4A;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #5E5E5E;
            }
            QScrollBar::add-line, QScrollBar::sub-line { border: none; background: none; height: 0; width: 0; }
            QScrollBar::corner { background: #252525; }
        """)

        code_container = QWidget()
        code_container.setStyleSheet("QWidget { background: #1E1E1E; border: none; }")
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(14, 10, 14, 10)
        code_container_layout.setSpacing(0)

        self.code_editor = QTextEdit()
        self.code_editor.setObjectName("codeEditor")
        self.code_editor.setPlainText(code)
        self.code_editor.setReadOnly(True)
        self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_editor.setFrameShape(QTextEdit.Shape.NoFrame)
        self.code_editor.setStyleSheet("""
            QTextEdit#codeEditor {
                background: transparent;
                border: none;
                color: #D4D4D4;
                selection-background-color: rgba(168, 199, 250, 0.2);
            }
        """)

        # Match visual size of surrounding text
        font = QFont('Consolas', 10)
        if not font.exactMatch():
            font = QFont('Monaco', 10)
        if not font.exactMatch():
            font = QFont('Courier New', 10)
        self.code_editor.setFont(font)

        self.highlighter = CodeSyntaxHighlighter(self.code_editor.document(), language)
        code_container_layout.addWidget(self.code_editor)
        self.scroll_area.setWidget(code_container)

        # Auto-height based on line count
        line_count = len(code.split('\n'))
        calculated_height = min(max(line_count * 17 + 24, self.min_height), self.max_height)
        self.scroll_area.setFixedHeight(calculated_height)

        # Set initial width
        self.scroll_area.setMinimumWidth(self.min_width)
        self.scroll_area.setMaximumWidth(self.max_width)

        # START HIDDEN (collapsed)
        self.scroll_area.hide()
        self.vertical_handle_visible = False

        container_layout.addWidget(self.scroll_area)
        layout.addWidget(self.main_container)

        # ── Bottom vertical resize handle ──────────────────────────────────────
        self.vertical_handle = QFrame()
        self.vertical_handle.setObjectName("codeResizeHandle")
        self.vertical_handle.setFixedHeight(5)
        self.vertical_handle.setStyleSheet("""
            QFrame#codeResizeHandle {
                background: rgba(168, 199, 250, 0.15);
                border-radius: 0px 0px 4px 4px;
            }
            QFrame#codeResizeHandle:hover {
                background: rgba(168, 199, 250, 0.35);
            }
        """)
        self.vertical_handle.setCursor(Qt.CursorShape.SizeVerCursor)
        self.vertical_handle.mousePressEvent = self.handle_vertical_press
        self.vertical_handle.mouseMoveEvent = self.handle_vertical_move
        self.vertical_handle.mouseReleaseEvent = self.handle_vertical_release
        self.vertical_handle.hide()  # START HIDDEN
        layout.addWidget(self.vertical_handle)

        main_layout.addWidget(content_widget)

        # ── Right horizontal resize handle ─────────────────────────────────────
        self.horizontal_handle = QFrame()
        self.horizontal_handle.setObjectName("codeResizeHandleHorizontal")
        self.horizontal_handle.setFixedWidth(5)
        self.horizontal_handle.setStyleSheet("""
            QFrame#codeResizeHandleHorizontal {
                background: rgba(168, 199, 250, 0.15);
                border-radius: 4px;
            }
            QFrame#codeResizeHandleHorizontal:hover {
                background: rgba(168, 199, 250, 0.35);
            }
        """)
        self.horizontal_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self.horizontal_handle.mousePressEvent = self.handle_horizontal_press
        self.horizontal_handle.mouseMoveEvent = self.handle_horizontal_move
        self.horizontal_handle.mouseReleaseEvent = self.handle_horizontal_release
        self.horizontal_handle.hide()  # START HIDDEN
        main_layout.addWidget(self.horizontal_handle)

    def toggle_expand(self):
        """Toggle between expanded and collapsed states"""
        self.is_expanded = not self.is_expanded

        if self.is_expanded:
            # Expand - show code area and resize handles
            self.scroll_area.show()
            self.vertical_handle.show()
            self.horizontal_handle.show()
            self.toggle_btn.setText("▼")
        else:
            # Collapse - hide code area and resize handles
            self.scroll_area.hide()
            self.vertical_handle.hide()
            self.horizontal_handle.hide()
            self.toggle_btn.setText("▶")

    def handle_vertical_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing_vertical = True
            self.resize_start_y = event.globalPosition().y()
            self.resize_start_height = self.scroll_area.height()
            event.accept()

    def handle_vertical_move(self, event):
        if self.is_resizing_vertical:
            delta = event.globalPosition().y() - self.resize_start_y
            new_height = self.resize_start_height + delta
            new_height = max(self.min_height, min(new_height, self.max_height))
            self.scroll_area.setFixedHeight(int(new_height))
            event.accept()

    def handle_vertical_release(self, event):
        if self.is_resizing_vertical:
            self.is_resizing_vertical = False
            event.accept()

    def handle_horizontal_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing_horizontal = True
            self.resize_start_x = event.globalPosition().x()
            self.resize_start_width = self.scroll_area.width()
            event.accept()

    def handle_horizontal_move(self, event):
        if self.is_resizing_horizontal:
            delta = event.globalPosition().x() - self.resize_start_x
            new_width = self.resize_start_width + delta
            new_width = max(self.min_width, min(new_width, self.max_width))
            self.scroll_area.setFixedWidth(int(new_width))
            event.accept()

    def handle_horizontal_release(self, event):
        if self.is_resizing_horizontal:
            self.is_resizing_horizontal = False
            event.accept()

    def copy_code(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.code)
        self.copy_btn.setText("✓ Copied!")
        self.copy_btn.setStyleSheet("""
            QPushButton#codeCopyBtn {
                background-color: rgba(52, 168, 83, 0.15);
                border: 1px solid rgba(52, 168, 83, 0.35);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                color: #34A853;
            }
        """)
        QTimer.singleShot(ANIM_COPY_FEEDBACK_MS, self.reset_copy_button)

    def reset_copy_button(self):
        self.copy_btn.setText("📋")
        self.copy_btn.setStyleSheet("""
            QPushButton#codeCopyBtn {
                background-color: rgba(168, 199, 250, 0.08);
                border: 1px solid rgba(168, 199, 250, 0.2);
                border-radius: 4px;
                padding: 6px 10px;
                font-size: 11px;
                font-weight: 500;
                color: #9CDCFE;
            }
            QPushButton#codeCopyBtn:hover {
                background-color: rgba(168, 199, 250, 0.15);
                border-color: rgba(168, 199, 250, 0.35);
            }
            QPushButton#codeCopyBtn:pressed {
                background-color: rgba(168, 199, 250, 0.25);
            }
        """)


class ChatWindow(QWidget):
    """Modern chat window with AI conversation"""

    voice_playback_signal = pyqtSignal()  # Signal for thread-safe UI updates

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.thinking_timer = None
        self.thinking_dots = 0
        self.thinking_label_shown = False
        self.sidebar_visible = False
        # Sidebar resize state (drag handle on right edge)
        self._sidebar_w = SIDEBAR_DEFAULT_W        # current width (persists across open/close)
        self._sidebar_resize_active = False        # True while user is dragging
        self._sidebar_resize_start_x = 0          # global X at drag start
        self._sidebar_resize_start_w = 0          # sidebar width at drag start

        # ── Smooth scroll state (main chat) ───────────────────────────────
        self._scroll_anim = None
        self._inertia_velocity = 0.0
        self._inertia_timer = QTimer()
        self._inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._inertia_timer.timeout.connect(self._inertia_tick)
        self._user_scrolling = False

        # ── Smooth scroll state (sidebar) ─────────────────────────────────
        self._sidebar_scroll_anim = None
        self._sidebar_inertia_velocity = 0.0
        self._sidebar_inertia_timer = QTimer()
        self._sidebar_inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._sidebar_inertia_timer.timeout.connect(self._sidebar_inertia_tick)

        # ── Smooth scroll state (input field) ─────────────────────────────
        self._input_inertia_velocity = 0.0
        self._input_inertia_timer = QTimer()
        self._input_inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._input_inertia_timer.timeout.connect(self._input_inertia_tick)

        # ── Animation state ────────────────────────────────────────────────────
        self._sidebar_anim = None
        self._open_anim = None

        # Voice state
        self.voice_enabled = False

        # NEW: Pending message buffer for voice mode
        self.pending_voice_message = None
        self.waiting_for_playback = False

        # NEW: Connect voice playback signal
        self.voice_playback_signal.connect(self._handle_voice_playback_on_main_thread)

        # Force mode settings
        self.force_mode = None

        # Image attachment
        self.attached_image = None

        # Interrupt tracking
        self.last_sent_message = None  # Track last message for interrupt
        self.last_user_message_widget = None  # Track last user message widget for removal

        # MESSAGE CONTROL: Track all messages for edit/delete/rewind
        self.message_widgets = []  # List of {widget, role, content, history_index}

        # Window dragging state
        self.dragging = False
        self.drag_position = QPoint()
        self.resizing = False
        self.resize_edge = None
        self.resize_start_geometry = None
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.save_window_geometry)

        # Avatar settings
        self.config_file = _APP_ROOT / "chat_config.json"
        self.load_config()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Window settings - BORDERLESS (Spotify-style)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(800, 500)  # Minimum size
        self.resize(1000, 650)  # Default size (but resizable!)
        # Main container for rounded corners
        self.container = QWidget()
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #212121;
                border-radius: 12px;
            }
            QWidget {
                color: #E8EAED;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
        """)
        self.container.setObjectName("container")
        self.container.setCursor(Qt.CursorShape.ArrowCursor)
        self.container.setAcceptDrops(True)

        self.init_ui()

        # Wrap everything in container for rounded corners
        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        # Apply rounded mask
        self.apply_rounded_mask()

        self.create_resize_handles()

        # Warn about any already-loaded skills from previous session (delayed so chat renders first)
        QTimer.singleShot(800, self.warn_loaded_skills_if_any)

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.bot_avatar = config.get('bot_avatar', '🤖')
                    self.user_avatar = config.get('user_avatar', '👤')
            else:
                self.bot_avatar = '🤖'
                self.user_avatar = '👤'
        except:
            self.bot_avatar = '🤖'
            self.user_avatar = '👤'
        # Load window geometry after config is loaded
        QTimer.singleShot(100, self.load_window_geometry)

    def save_config(self):
        try:
            config = {
                'bot_avatar': self.bot_avatar,
                'user_avatar': self.user_avatar
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")

    def init_ui(self):
        """Initialize modern UI"""
        self.setAcceptDrops(True)
        main_layout = QHBoxLayout(self.container)  # Changed: use container
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ═══════════════════════════════════════════════════════════════════════
        # SIDEBAR — overlay (not in main_layout), parented to self.container.
        # Slides in/out via QPropertyAnimation on geometry.
        # ═══════════════════════════════════════════════════════════════════════
        self.sidebar = QFrame(self.container)
        self.sidebar.setFixedWidth(self._sidebar_w)
        self.sidebar.setStyleSheet("""
            QFrame {
                background-color: #171717;
                border-right: 1px solid #2A2A2A;
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
            }
        """)
        # Start the sidebar parked off-screen to the left (hidden)
        self.sidebar.setGeometry(-self._sidebar_w, 0, self._sidebar_w, 650)
        self.sidebar.hide()

        # Create main sidebar layout: scrollable content + right drag-handle
        sidebar_main_layout = QHBoxLayout(self.sidebar)
        sidebar_main_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_main_layout.setSpacing(0)

        # Inner VBox for scroll area
        sidebar_scroll_vbox = QWidget()
        sidebar_scroll_vbox.setStyleSheet("background: transparent;")
        sidebar_scroll_vbox_layout = QVBoxLayout(sidebar_scroll_vbox)
        sidebar_scroll_vbox_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_scroll_vbox_layout.setSpacing(0)
        sidebar_main_layout.addWidget(sidebar_scroll_vbox, stretch=1)

        # ── Right-edge resize handle ──────────────────────────────────────────
        self._sidebar_drag_handle = QFrame(self.sidebar)
        self._sidebar_drag_handle.setFixedWidth(6)
        self._sidebar_drag_handle.setStyleSheet("""
            QFrame {
                background-color: rgba(168, 199, 250, 0.12);
                border-radius: 3px;
            }
            QFrame:hover {
                background-color: rgba(168, 199, 250, 0.35);
            }
        """)
        self._sidebar_drag_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self._sidebar_drag_handle.setToolTip("Drag to resize sidebar")
        # wire mouse events with distinct names to avoid clashing with code-block handles
        self._sidebar_drag_handle.mousePressEvent   = self._sidebar_resize_press
        self._sidebar_drag_handle.mouseMoveEvent    = self._sidebar_resize_move
        self._sidebar_drag_handle.mouseReleaseEvent = self._sidebar_resize_release
        sidebar_main_layout.addWidget(self._sidebar_drag_handle)

        # All scroll/content setup goes into sidebar_scroll_vbox_layout
        # (we replace sidebar_main_layout usages below)


        # ── Sidebar scroll area (saved as instance var for smooth scroll) ──
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 12px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(168, 199, 250, 0.3);
                border-radius: 6px;
                min-height: 30px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(168, 199, 250, 0.5);
            }
            QScrollBar::handle:vertical:pressed {
                background: rgba(168, 199, 250, 0.7);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        # Install viewport event filter for inertia scroll
        self.sidebar_scroll.viewport().installEventFilter(self)

        # Create container widget for scrollable content
        sidebar_content = QWidget()
        sidebar_content.setStyleSheet("""
            QWidget {
                background-color: #171717;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(10)

        # Sidebar header
        sidebar_header = QLabel("🎨 Appearance")
        sidebar_header.setStyleSheet("font-size: 12px; font-weight: 600; color: #9AA0A6; padding: 8px 4px;")
        sidebar_layout.addWidget(sidebar_header)

        # Bot Avatar Selection
        bot_avatar_container = QFrame()
        bot_avatar_container.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        bot_avatar_layout = QVBoxLayout(bot_avatar_container)
        bot_avatar_layout.setSpacing(6)
        bot_avatar_layout.setContentsMargins(10, 10, 10, 10)

        bot_label = QLabel("Bot Avatar")
        bot_label.setStyleSheet("color: #9AA0A6; font-size: 11px;")
        bot_avatar_layout.addWidget(bot_label)

        self.bot_avatar_display = QLabel(self.bot_avatar)
        self.bot_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bot_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 28px;
                background-color: #1A73E8;
                border-radius: 24px;
                min-width: 48px;
                min-height: 48px;
                max-width: 48px;
                max-height: 48px;
            }
        """)
        bot_avatar_layout.addWidget(self.bot_avatar_display, alignment=Qt.AlignmentFlag.AlignCenter)

        bot_btn = QPushButton("Change Bot Avatar")
        bot_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 6px;
                font-size: 10px;
                color: #E8EAED;
            }
            QPushButton:hover {
                background-color: #333333;
            }
        """)
        bot_btn.clicked.connect(self.change_bot_avatar)
        bot_avatar_layout.addWidget(bot_btn)

        sidebar_layout.addWidget(bot_avatar_container)

        # User Avatar Selection
        user_avatar_container = QFrame()
        user_avatar_container.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        user_avatar_layout = QVBoxLayout(user_avatar_container)
        user_avatar_layout.setSpacing(6)
        bot_avatar_layout.setContentsMargins(10, 10, 10, 10)

        user_label = QLabel("User Avatar")
        user_label.setStyleSheet("color: #9AA0A6; font-size: 11px;")
        user_avatar_layout.addWidget(user_label)

        self.user_avatar_display = QLabel(self.user_avatar)
        self.user_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 28px;
                background-color: #34A853;
                border-radius: 24px;
                min-width: 48px;
                min-height: 48px;
                max-width: 48px;
                max-height: 48px;
            }
        """)
        user_avatar_layout.addWidget(self.user_avatar_display, alignment=Qt.AlignmentFlag.AlignCenter)

        user_btn = QPushButton("Change User Avatar")
        user_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 6px;
                font-size: 10px;
                color: #E8EAED;
            }
            QPushButton:hover {
                background-color: #333333;
            }
        """)
        user_btn.clicked.connect(self.change_user_avatar)
        user_avatar_layout.addWidget(user_btn)

        sidebar_layout.addWidget(user_avatar_container)

        # NEW: User Name Section
        user_name_container = QFrame()
        user_name_container.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        user_name_layout = QVBoxLayout(user_name_container)
        user_name_layout.setSpacing(6)
        user_name_layout.setContentsMargins(10, 10, 10, 10)

        user_name_label = QLabel("Your Name")
        user_name_label.setStyleSheet("color: #9AA0A6; font-size: 11px;")
        user_name_layout.addWidget(user_name_label)

        self.user_name_input = QLineEdit()
        self.user_name_input.setPlaceholderText("Enter your name...")
        self.user_name_input.setStyleSheet("""
                QLineEdit {
                    background-color: #2A2A2A;
                    border: 1px solid #3C3C3C;
                    border-radius: 6px;
                    padding: 6px;
                    font-size: 11px;
                    color: #E8EAED;
                }
            """)
        self.user_name_input.textChanged.connect(self.on_user_name_changed) # Connect to save immediately on text change
        user_name_layout.addWidget(self.user_name_input)

        sidebar_layout.addWidget(user_name_container)

        # NEW: Personalization Instructions
        personalization_container = QFrame()
        personalization_container.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        personalization_layout = QVBoxLayout(personalization_container)
        personalization_layout.setSpacing(6)
        personalization_layout.setContentsMargins(10, 10, 10, 10)

        personalization_label = QLabel("Custom Instructions")
        personalization_label.setStyleSheet("color: #9AA0A6; font-size: 11px;")
        personalization_layout.addWidget(personalization_label)

        # Single button to open instructions window
        configure_instructions_btn = QPushButton("⚙️ Configure Instructions")
        configure_instructions_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 8px;
                font-size: 10px;
                color: #E8EAED;
            }
            QPushButton:hover {
                background-color: #333333;
            }
        """)
        configure_instructions_btn.clicked.connect(self.open_instructions_window)
        personalization_layout.addWidget(configure_instructions_btn)

        sidebar_layout.addWidget(personalization_container)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #2A2A2A; max-height: 1px; margin: 8px 0;")
        sidebar_layout.addWidget(separator)

        # ═══════════════════════════════════════════════════════════
        # ⚡ SKILLS SECTION (integrated in sidebar)
        # ═══════════════════════════════════════════════════════════
        skill_manager = getattr(self.controller, 'skill_manager', None)
        if skill_manager:
            self._skills_section = SkillsSidebarSection(skill_manager)
            sidebar_layout.addWidget(self._skills_section)
        else:
            no_skills_lbl = QLabel("Skills unavailable")
            no_skills_lbl.setStyleSheet(
                "color: #9AA0A6; font-size: 10px; padding: 8px;")
            sidebar_layout.addWidget(no_skills_lbl)

        # ═══════════════════════════════════════════════════════════
        # SESSION HISTORY SECTION
        # ═══════════════════════════════════════════════════════════

        # Clickable header (collapses list when ≥20 sessions)
        session_section_hdr = QWidget()
        session_section_hdr.setStyleSheet("""
            QWidget { background-color: transparent; border-radius: 4px; }
            QWidget:hover { background-color: #222222; }
        """)
        session_section_hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        ssh_layout = QHBoxLayout(session_section_hdr)
        ssh_layout.setContentsMargins(4, 6, 4, 6)
        ssh_layout.setSpacing(6)

        self._session_chevron = QLabel("▼")
        self._session_chevron.setStyleSheet(
            "color: #9AA0A6; font-size: 9px; background: transparent;")
        ssh_layout.addWidget(self._session_chevron)

        session_header_lbl = QLabel("📁 Session History")
        session_header_lbl.setStyleSheet(
            "font-size: 12px; font-weight: 600; color: #9AA0A6; "
            "background: transparent; margin-top: 4px;")
        ssh_layout.addWidget(session_header_lbl, stretch=1)

        self._session_count_lbl = QLabel("")
        self._session_count_lbl.setStyleSheet(
            "color: #9AA0A6; font-size: 9px; background: transparent;")
        ssh_layout.addWidget(self._session_count_lbl)

        self._session_list_expanded = True   # collapses automatically at ≥
        self._session_list_auto_collapsed = False  # only auto-collapse once20
        session_section_hdr.mousePressEvent = lambda e: self._toggle_session_list()
        sidebar_layout.addWidget(session_section_hdr)

        # New Session Button
        new_session_btn = QPushButton("➕ New Session")
        new_session_btn.setMinimumWidth(0)
        new_session_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A73E8;
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-size: 11px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #1557B0; }
        """)
        new_session_btn.clicked.connect(lambda: self.controller.create_new_session())
        new_session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sidebar_layout.addWidget(new_session_btn)

        # Session list container — wrapped in a collapsible body widget
        self._session_list_body = QWidget()
        self._session_list_body.setStyleSheet("background-color: transparent;")
        slb_layout = QVBoxLayout(self._session_list_body)
        slb_layout.setContentsMargins(0, 0, 0, 0)
        slb_layout.setSpacing(0)

        session_list_container = QWidget()
        session_list_container.setStyleSheet("background-color: transparent;")
        self.session_list_layout = QVBoxLayout(session_list_container)
        self.session_list_layout.setContentsMargins(0, 0, 0, 0)
        self.session_list_layout.setSpacing(2)
        slb_layout.addWidget(session_list_container)

        sidebar_layout.addWidget(self._session_list_body)

        # Load initial sessions
        QTimer.singleShot(100, self.refresh_session_list)

        # At the end of sidebar content, add stretch
        sidebar_layout.addStretch()

        # Set the content widget to the scroll area
        self.sidebar_scroll.setWidget(sidebar_content)

        # Add scroll area to sidebar scroll vbox
        sidebar_scroll_vbox_layout.addWidget(self.sidebar_scroll)

        # NOTE: sidebar is NOT added to main_layout — it is an overlay.

        # === MAIN CHAT AREA ===
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Header bar
        header_bar = QFrame()
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet("""
            QFrame {
                background-color: #212121;
                border-bottom: 1px solid #2A2A2A;
            }
        """)

        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        # ── Toggle sidebar button ─────────────────────────────────────────────
        self.toggle_sidebar_btn = QPushButton("☰", self.container)
        self.toggle_sidebar_btn.setFixedSize(32, 32)
        self.toggle_sidebar_btn.setGeometry(16, 9, 32, 32)
        self.toggle_sidebar_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 18px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background: #2A2A2A;
                color: #E8EAED;
            }
        """)
        self.toggle_sidebar_btn.clicked.connect(self.toggle_sidebar)
        self.toggle_sidebar_btn.raise_()
        self.toggle_sidebar_btn.show()

        # Spacer in the header so the title stays correctly indented
        from PyQt6.QtWidgets import QSpacerItem, QSizePolicy
        header_layout.addItem(QSpacerItem(48, 32, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed))

        # Title
        title = QLabel("Systema Auxilium")
        title.setStyleSheet("""
            QLabel {
                font-size: 15px;
                font-weight: 600;
                color: #E8EAED;
                margin-left: 8px;
            }
        """)
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Voice status label
        self.voice_status_label = QLabel("")
        self.voice_status_label.setStyleSheet("""
                    QLabel {
                        font-size: 10px;
                        color: #9AA0A6;
                        margin: 0 8px;
                    }
                """)
        header_layout.addWidget(self.voice_status_label)

        # Skills are now integrated in the sidebar — no header button needed.
        self.skills_panel = None   # kept as attribute for any legacy references

        # ==========Window control buttons==========

        # Minimize button
        minimize_btn = QPushButton("−")
        minimize_btn.setFixedSize(32, 32)
        minimize_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 18px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background: #2A2A2A;
                color: #E8EAED;
            }
        """)
        minimize_btn.clicked.connect(self.showMinimized)
        header_layout.addWidget(minimize_btn)

        # Close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 22px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background: #EA4335;
                color: white;
            }
        """)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        chat_layout.addWidget(header_bar)

        # Chat display with scroll
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("""
                    QScrollArea {
                        border: none;
                        background-color: #212121;
                    }
                    QScrollBar:vertical {
                        background: transparent;
                        width: 12px;
                        margin: 0;
                    }
                    QScrollBar::handle:vertical {
                        background: rgba(168, 199, 250, 0.3);
                        border-radius: 6px;
                        min-height: 30px;
                        margin: 2px;
                    }
                    QScrollBar::handle:vertical:hover {
                        background: rgba(168, 199, 250, 0.5);
                    }
                    QScrollBar::handle:vertical:pressed {
                        background: rgba(168, 199, 250, 0.7);
                    }
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                        height: 0px;
                    }
                    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                        background: transparent;
                    }
                """)

        # Chat messages container
        self.chat_widget = QWidget()
        self.chat_widget.setStyleSheet("""
            QWidget {
                background-color: #212121;
            }
        """)
        self.chat_widget.setAcceptDrops(True)
        self.chat_layout = QVBoxLayout(self.chat_widget)
        self.chat_layout.setContentsMargins(0, 16, 0, 16)
        self.chat_layout.setSpacing(0)
        self.chat_layout.addStretch()

        self.chat_scroll_area = scroll_area
        scroll_area.setWidget(self.chat_widget)
        chat_layout.addWidget(scroll_area)

        # Install event filter on the viewport for smooth inertia scrolling (main chat)
        scroll_area.viewport().installEventFilter(self)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #9AA0A6;
                font-style: italic;
                font-size: 11px;
                padding: 6px 16px;
            }
        """)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chat_layout.addWidget(self.status_label)

        # Input area
        input_container = QFrame()
        input_container.setStyleSheet("""
            QFrame {
                background-color: #1F1F1F;
                border-top: 1px solid #2A2A2A;
                padding: 12px 16px;
            }
        """)

        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(8)

        # Mode selector and input combined
        combined_container = QFrame()
        combined_container.setStyleSheet("""
            QFrame {
                background-color: #1F1F1F;
                border: 1px solid #3C3C3C;
                border-radius: 12px;
            }
        """)

        combined_layout = QHBoxLayout(combined_container)
        combined_layout.setContentsMargins(12, 4, 12, 4)
        combined_layout.setSpacing(8)
        combined_layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)

        # File browse button
        browse_btn = QPushButton("📁")
        browse_btn.setFixedSize(32, 32)
        browse_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: 2px solid #5F5F5F;
                    border-radius: 16px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background: #2A2A2A;
                    border-color: #7F7F7F;
                }
            """)
        browse_btn.clicked.connect(self.browse_for_file)
        browse_btn.setToolTip("Browse for files")
        combined_layout.addWidget(browse_btn)

        # Mode dropdown (ChatGPT-style)
        self.mode_dropdown = QPushButton("💬")
        self.mode_dropdown.setFixedSize(32, 32)
        self.mode_dropdown.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 6px;
                font-size: 16px;
            }
            QPushButton:hover {
                background-color: #3C3C3C;
            }
        """)
        self.mode_dropdown.clicked.connect(self.show_mode_menu)
        combined_layout.addWidget(self.mode_dropdown)

        # Text input
        self.input_field = ResizableInput()
        self.input_field.text_input.setStyleSheet("""
            QTextEdit {
                background-color: #1F1F1F;
                border: none;
                color: #E8EAED;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                padding: 8px 4px;
                line-height: 1.6;
            }
            QTextEdit:focus {
                background-color: #1F1F1F;
            }
        """)
        self.input_field.enterPressed.connect(self.send_message)
        combined_layout.addWidget(self.input_field, 1)

        # Install inertia scroll on the input field's viewport
        self.input_field.text_input.viewport().installEventFilter(self)

        # NEW: Voice button inside message box
        self.voice_btn_inline = QPushButton("🎤")
        self.voice_btn_inline.setFixedSize(32, 32)
        self.voice_btn_inline.setCheckable(True)
        self.voice_btn_inline.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 2px solid #5F5F5F;
                border-radius: 16px;
                font-size: 16px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background: #2A2A2A;
                border-color: #7F7F7F;
                color: #E8EAED;
            }
            QPushButton:checked {
                background: #34A853;
                border-color: #34A853;
                color: white;
            }
        """)
        self.voice_btn_inline.clicked.connect(self.toggle_voice)
        combined_layout.addWidget(self.voice_btn_inline)

        # NEW: Voice interrupt button (only shown during TTS in manual mode)
        self.voice_interrupt_btn = QPushButton("🔇")
        self.voice_interrupt_btn.setFixedSize(32, 32)
        self.voice_interrupt_btn.setStyleSheet("""
            QPushButton {
                background: #EA4335;
                border: none;
                border-radius: 16px;
                font-size: 16px;
                color: white;
            }
            QPushButton:hover {
                background: #C5372C;
            }
        """)
        self.voice_interrupt_btn.clicked.connect(self.interrupt_voice)
        self.voice_interrupt_btn.hide()  # Hidden by default
        combined_layout.addWidget(self.voice_interrupt_btn)

        # Interrupt response button
        self.interrupt_btn = QPushButton("⏹")
        self.interrupt_btn.setFixedSize(32, 32)
        self.interrupt_btn.setStyleSheet("""
            QPushButton {
                background-color: #EA4335;
                border: none;
                border-radius: 16px;
                font-size: 16px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #C5372C;
            }
            QPushButton:pressed {
                background-color: #A62C23;
            }
        """)
        self.interrupt_btn.clicked.connect(self.interrupt_response)
        self.interrupt_btn.hide()  # Hidden by default
        combined_layout.addWidget(self.interrupt_btn)

        # Send button
        self.send_btn = QPushButton("↑")
        self.send_btn.setFixedSize(32, 32)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A73E8;
                border: none;
                border-radius: 16px;
                font-size: 16px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1557B0;
            }
            QPushButton:pressed {
                background-color: #103F7C;
            }
            QPushButton:disabled {
                background-color: #3C3C3C;
                color: #5F5F5F;
            }
        """)
        self.send_btn.clicked.connect(self.send_message)
        combined_layout.addWidget(self.send_btn)

        input_layout.addWidget(combined_container)

        chat_layout.addWidget(input_container)

        main_layout.addWidget(chat_container)

        # Re-raise the toggle button so it sits above chat_container in Z-order.
        self.toggle_sidebar_btn.raise_()

        # Load personalization
        self.load_personalization()

        # Notify if admin priveleges are available
        self.check_admin_mode()

        # Welcome message
        self.add_system_message(
            "👋 **Welcome to Systema Auxilium!**\n\n"
            "I can execute Python code and control your system. "
            "Click the 💬 icon to enforce tool usage."
        )

    # ═══════════════════════════════════════════════════════════
    # ANIMATION METHODS
    # ═══════════════════════════════════════════════════════════

    def showEvent(self, event):
        """Fade the window in every time it becomes visible."""
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        self._open_anim = QPropertyAnimation(self, b"windowOpacity")
        self._open_anim.setDuration(ANIM_WINDOW_FADE_IN_MS)
        self._open_anim.setStartValue(0.0)
        self._open_anim.setEndValue(1.0)
        self._open_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_anim.start()

    def toggle_sidebar(self):
        """Toggle sidebar visibility with a smooth slide animation."""
        self.sidebar_visible = not self.sidebar_visible
        self._animate_sidebar(self.sidebar_visible)

    # ── Sidebar right-edge resize ──────────────────────────────────────────────
    # These are distinct from the code-block handles (handle_vertical_press etc.)

    def _sidebar_resize_press(self, event):
        """Start sidebar width drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._sidebar_resize_active = True
            self._sidebar_resize_start_x = event.globalPosition().toPoint().x()
            self._sidebar_resize_start_w = self._sidebar_w
            event.accept()

    def _sidebar_resize_move(self, event):
        """Update sidebar width while dragging."""
        if not self._sidebar_resize_active:
            return
        dx = event.globalPosition().toPoint().x() - self._sidebar_resize_start_x
        new_w = max(SIDEBAR_MIN_W, min(SIDEBAR_MAX_W, self._sidebar_resize_start_w + dx))
        if new_w != self._sidebar_w:
            self._sidebar_w = new_w
            container_h = self.container.height()
            self.sidebar.setFixedWidth(new_w)
            self.sidebar.setGeometry(0, 0, new_w, container_h)
        event.accept()

    def _sidebar_resize_release(self, event):
        """Finish sidebar width drag."""
        self._sidebar_resize_active = False
        event.accept()

    def resizeEvent(self, event):
        """Keep sidebar height in sync with container on window resize."""
        super().resizeEvent(event)
        # sidebar height tracks container
        if self.sidebar_visible and self.sidebar.isVisible():
            container_h = self.container.height()
            self.sidebar.setGeometry(0, 0, self._sidebar_w, container_h)


    def _animate_sidebar(self, show: bool):
        """Slide the sidebar in (show=True) or out (show=False)."""
        if self._sidebar_anim is not None:
            if self._sidebar_anim.state() == QPropertyAnimation.State.Running:
                self._sidebar_anim.stop()

        container_h = self.container.height()
        sidebar_w = self._sidebar_w

        if show:
            self.sidebar.setFixedWidth(sidebar_w)
            self.sidebar.setGeometry(-sidebar_w, 0, sidebar_w, container_h)
            self.sidebar.show()
            self.sidebar.raise_()
            self.toggle_sidebar_btn.raise_()
            start_geo = QRect(-sidebar_w, 0, sidebar_w, container_h)
            end_geo   = QRect(0,          0, sidebar_w, container_h)
        else:
            start_geo = QRect(0,          0, sidebar_w, container_h)
            end_geo   = QRect(-sidebar_w, 0, sidebar_w, container_h)

        self._sidebar_anim = QPropertyAnimation(self.sidebar, b"geometry")
        self._sidebar_anim.setDuration(ANIM_SIDEBAR_SLIDE_MS)
        self._sidebar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sidebar_anim.setStartValue(start_geo)
        self._sidebar_anim.setEndValue(end_geo)

        if not show:
            self._sidebar_anim.finished.connect(lambda: self.sidebar.hide())

        self._sidebar_anim.start()

    def _animate_message_in(self, widget, on_settled=None):
        """
        Slide-open + fade-in a new message widget.
        Uses OutBack easing for a satisfying spring overshoot.
        Timings controlled by ANIM_MSG_IN_* constants at top of file.

        on_settled: optional callable fired AFTER the animation completes and
                    the widget is unconstrained.  Use this to trigger scroll
                    centering — at that point sb.maximum() and widget.height()
                    are accurate.
        """
        natural_h = widget.sizeHint().height()
        if natural_h < 10:
            natural_h = 300

        # Start collapsed + invisible
        widget.setMaximumHeight(0)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        # Height: 0 → natural + overshoot (OutBack gives the spring feel)
        height_anim = QPropertyAnimation(widget, b"maximumHeight")
        height_anim.setDuration(ANIM_MSG_IN_HEIGHT_MS)
        height_anim.setStartValue(0)
        height_anim.setEndValue(natural_h + ANIM_MSG_IN_OVERSHOOT_PX)
        height_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        # Opacity: 0 → 1
        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(ANIM_MSG_IN_FADE_MS)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(widget)
        group.addAnimation(height_anim)
        group.addAnimation(fade_anim)

        def _on_done():
            widget.setMaximumHeight(16777215)   # Qt QWIDGETSIZE_MAX — unconstrain
            widget.setGraphicsEffect(None)
            # Fire the scroll callback now that layout is fully settled.
            # A tiny extra delay lets Qt flush the layout update so
            # sb.maximum() and widget.height() are guaranteed correct.
            if on_settled is not None:
                QTimer.singleShot(30, on_settled)

        group.finished.connect(_on_done)

        widget._anim_in_group = group
        widget._anim_in_effect = effect
        group.start()

    def _animate_message_out(self, widget, callback):
        """
        Fade-out + collapse height of a message widget, then fire callback.
        Timings controlled by ANIM_MSG_OUT_* constants at top of file.
        """
        if hasattr(widget, '_anim_in_group'):
            try:
                widget._anim_in_group.stop()
            except RuntimeError:
                pass
            widget.setMaximumHeight(16777215)
            widget.setGraphicsEffect(None)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)

        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(ANIM_MSG_OUT_FADE_MS)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        current_h = max(widget.height(), 10)
        height_anim = QPropertyAnimation(widget, b"maximumHeight")
        height_anim.setDuration(ANIM_MSG_OUT_HEIGHT_MS)
        height_anim.setStartValue(current_h)
        height_anim.setEndValue(0)
        height_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QParallelAnimationGroup(widget)
        group.addAnimation(fade_anim)
        group.addAnimation(height_anim)
        group.finished.connect(callback)

        widget._anim_out_group = group
        widget._anim_out_effect = effect
        group.start()

    def open_instructions_window(self):
        """Open custom instructions configuration window"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel

        dialog = QDialog(self)
        dialog.setWindowTitle("Custom Assistant Instructions")
        dialog.setModal(True)
        dialog.setMinimumSize(500, 400)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #212121;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Title
        title = QLabel("Custom Instructions")
        title.setStyleSheet("""
            QLabel {
                color: #E8EAED;
                font-size: 16px;
                font-weight: 600;
                margin-bottom: 8px;
            }
        """)
        layout.addWidget(title)

        # Description
        desc = QLabel("Customize how the assistant responds to you:")
        desc.setStyleSheet("color: #9AA0A6; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(desc)

        # Text area
        text_edit = QTextEdit()
        text_edit.setPlaceholderText(
            "Example:\n"
            "- Always be enthusiastic and encouraging\n"
            "- Use emojis when appropriate\n"
            "- Explain technical concepts simply\n"
            "- Be concise in your responses"
        )
        text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1A1A1A;
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                color: #E8EAED;
                line-height: 1.6;
            }
            QTextEdit:focus {
                border: 1px solid #1A73E8;
                background-color: #151515;
            }
        """)

        # Load existing instructions
        current_instructions = self.controller.get_custom_instructions()
        if current_instructions:
            text_edit.setPlainText(current_instructions)

        layout.addWidget(text_edit, 1)  # Stretch to fill space

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 12px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background-color: #2A2A2A;
                color: #E8EAED;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A73E8;
                border: none;
                border-radius: 6px;
                padding: 10px 24px;
                font-size: 12px;
                color: white;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #1557B0;
            }
        """)

        def save_and_close():
            instructions = text_edit.toPlainText().strip()
            self.controller.set_custom_instructions(instructions)
            self.add_system_message("✓ **Custom Instructions Saved**")
            dialog.accept()

        save_btn.clicked.connect(save_and_close)

        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def show_mode_menu(self):
        """Show mode selection menu"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3C3C3C;
            }
            QMenu::separator {
                height: 1px;
                background: #3C3C3C;
                margin: 4px 0;
            }
        """)

        # Normal mode
        normal_action = QAction("💬 Normal Mode", self)
        normal_action.triggered.connect(lambda: self.set_force_mode(None))
        menu.addAction(normal_action)

        menu.addSeparator()

        # Tool mode
        tool_action = QAction("🔧 Use Work Environment", self)
        tool_action.triggered.connect(lambda: self.set_force_mode('work_environment'))
        menu.addAction(tool_action)

        # Command mode
        command_action = QAction("⚡ Execute a Code", self)
        command_action.triggered.connect(lambda: self.set_force_mode('execute_code'))
        menu.addAction(command_action)

        # Show menu below button
        button_pos = self.mode_dropdown.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(button_pos.x(), button_pos.y() - menu.sizeHint().height()))

    def set_force_mode(self, mode):
        """Set force mode"""
        self.force_mode = mode

        if mode == 'work_environment':
            self.mode_dropdown.setText("🔧")
            self.add_system_message("🔧 **Work Environment** - AI will enter its work environment to do some complex task.")
        elif mode == 'execute_code':
            self.mode_dropdown.setText("⚡")
            self.add_system_message("⚡ **Single Execution** - AI will execute a single request.")
        else:
            self.mode_dropdown.setText("💬")
            self.add_system_message("💬 **Normal Mode** - AI decides when to use Work environment or Single Execution")

    def toggle_voice(self):
        """Toggle voice mode on/off"""
        if not self.voice_btn_inline.isChecked():
            # Disable voice
            self.disable_voice()
        else:
            # Enable voice
            self.enable_voice()

    def enable_voice(self):
        """Enable voice mode"""
        success, message = self.controller.enable_voice_mode()

        if success:
            self.voice_enabled = True
            self.voice_btn_inline.setChecked(True)
            self.add_system_message(f"🎤 **Voice Mode Enabled**\n\n{message}")
            self.update_voice_status("Ready")
        else:
            self.voice_enabled = False
            self.voice_btn_inline.setChecked(False)
            self.add_system_message(f"❌ **Voice Mode Failed**\n\n{message}")

    def disable_voice(self):
        """Disable voice mode"""
        self.controller.disable_voice_mode()
        self.voice_enabled = False
        self.voice_btn_inline.setChecked(False)
        self.update_voice_status("")
        self.add_system_message("🔇 **Voice Mode Disabled**")

    def load_personalization(self):
        """Load personalization settings"""
        user_name = self.controller.get_user_name()
        if user_name:
            self.user_name_input.setText(user_name)

    def update_voice_status(self, status):
        """Update voice status indicator"""
        status_styles = {
            'listening': ('🔴 Listening...', 'color: #EA4335; font-weight: bold;'),
            'processing': ('🟡 Processing...', 'color: #FBBC04; font-weight: bold;'),
            'speaking': ('🟢 Speaking...', 'color: #34A853; font-weight: bold;'),
            'inactive': ('', ''),
            'Ready': ('🎤 Ready', 'color: #9AA0A6;')
        }

        text, style = status_styles.get(status, ('', ''))
        self.voice_status_label.setText(text)
        self.voice_status_label.setStyleSheet(f"QLabel {{ font-size: 10px; margin: 0 8px; {style} }}")

        # Show interrupt button during speaking (manual mode only)
        if status == 'speaking' and self.controller.get_voice_interrupt_mode() == 'manual':
            self.voice_interrupt_btn.show()
        else:
            self.voice_interrupt_btn.hide()

    def closeEvent(self, event):
        """Handle window close - just hide, don't close app"""
        self.hide()
        event.ignore()  # Prevent the window from actually closing

    def on_user_name_changed(self, text):
        """Called when user name input changes"""
        user_name = text.strip()
        self.controller.set_user_name(user_name)

    def change_bot_avatar(self):
        """Change bot avatar"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 6px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 18px;
            }
            QMenu::item:selected {
                background-color: #3C3C3C;
            }
        """)

        emojis = ['🤖', '🦾', '🧠', '👾', '🤵', '🦊', '🐺', '🦁', '🐼', '🐨']
        for emoji in emojis:
            action = QAction(emoji, self)
            action.triggered.connect(lambda checked, e=emoji: self.set_bot_avatar(e))
            menu.addAction(action)

        menu.exec(QCursor.pos())

    def change_user_avatar(self):
        """Change user avatar"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 6px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 18px;
            }
            QMenu::item:selected {
                background-color: #3C3C3C;
            }
        """)

        emojis = ['👤', '👨', '👩', '🧑', '😊', '😎', '🤓', '🧙', '🦸', '🥷']
        for emoji in emojis:
            action = QAction(emoji, self)
            action.triggered.connect(lambda checked, e=emoji: self.set_user_avatar(e))
            menu.addAction(action)

        menu.exec(QCursor.pos())

    def set_bot_avatar(self, emoji):
        """Set bot avatar"""
        self.bot_avatar = emoji
        self.bot_avatar_display.setText(emoji)
        self.save_config()

    def set_user_avatar(self, emoji):
        """Set user avatar"""
        self.user_avatar = emoji
        self.user_avatar_display.setText(emoji)
        self.save_config()

    def _latex_to_base64_img(self, latex_expr, display=True):
        """Render a LaTeX expression to a tight base64-encoded PNG using matplotlib."""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import io, base64

            fig = plt.figure(figsize=(0.01, 0.01))
            fig.patch.set_alpha(0)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_axis_off()
            ax.patch.set_alpha(0)

            fontsize = 13 if display else 11
            ax.text(0.5, 0.5, f'${latex_expr}$',
                    fontsize=fontsize, color='#BDC1C6',
                    ha='center', va='center',
                    transform=ax.transAxes)

            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                        transparent=True, pad_inches=0.05, facecolor='none')
            plt.close(fig)
            buf.seek(0)
            b64 = base64.b64encode(buf.read()).decode('utf-8')

            source_html = (
                f'<span style="font-family:monospace;font-size:9px;'
                f'color:#4A4A4A;user-select:text;">{latex_expr}</span>'
            )

            if display:
                return (
                    f'<br>'
                    f'<div style="text-align:center;margin:4px 0 2px 0;">'
                    f'<img src="data:image/png;base64,{b64}" style="max-height:40px;">'
                    f'</div>'
                    f'<div style="text-align:center;margin:0 0 4px 0;">{source_html}</div>'
                    f'<br>'
                )
            else:
                return (
                    f'<img src="data:image/png;base64,{b64}" '
                    f'style="vertical-align:middle;margin:0 2px;max-height:22px;">'
                    f'&nbsp;{source_html}'
                )
        except Exception:
            return f'<code>{latex_expr}</code>'

    def _preprocess_latex(self, text):
        """Detect and replace LaTeX math expressions with rendered PNG images."""
        import re

        def replace_display(m):
            return self._latex_to_base64_img(m.group(1).strip(), display=True)
        text = re.sub(r'\\\[(.*?)\\\]', replace_display, text, flags=re.DOTALL)

        def replace_inline(m):
            return self._latex_to_base64_img(m.group(1).strip(), display=False)
        text = re.sub(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)', replace_inline, text)

        def replace_bracket(m):
            inner = m.group(1).strip()
            has_latex = bool(re.search(r'\\[a-zA-Z]+|[_^{}]|\bfrac\b|\bsum\b|\bint\b', inner))
            if has_latex:
                return self._latex_to_base64_img(inner, display=True)
            return m.group(0)
        text = re.sub(r'^\[\s*(.+?)\s*\]\s*$', replace_bracket, text, flags=re.MULTILINE)

        return text

    def render_markdown(self, text):
        """Render markdown to HTML"""
        try:
            html = markdown2.markdown(text, extras=["fenced-code-blocks", "tables", "break-on-newline"])
            return html
        except:
            return text.replace('\n', '<br>')

    def render_markdown_with_code_blocks(self, text):
        """Render markdown with special handling for code blocks"""
        import re

        parts = []
        last_end = 0

        code_pattern = r'```(\w+)?\n(.*?)```'

        for match in re.finditer(code_pattern, text, re.DOTALL):
            if match.start() > last_end:
                before_text = text[last_end:match.start()]
                if before_text.strip():
                    parts.append(('text', before_text))

            language = match.group(1) or 'text'
            code_content = match.group(2)
            parts.append(('code', language, code_content))

            last_end = match.end()

        if last_end < len(text):
            remaining = text[last_end:]
            if remaining.strip():
                parts.append(('text', remaining))

        if not any(p[0] == 'code' for p in parts):
            return self.render_markdown(text)

        return parts

    def clear_chat(self):
        """Clear chat history WITH notification"""
        self._clear_chat_internal()
        self.add_system_message("🔄 **Chat Cleared** - Ready for a new conversation!")

    def clear_chat_silent(self):
        """Clear chat history WITHOUT notification (for session loading)"""
        self._clear_chat_internal()

    def _clear_chat_internal(self):
        """Internal method to clear chat widgets"""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.message_widgets = []

    def render_loaded_messages(self, messages):
        """Render messages from loaded session"""
        for msg in messages:
            role = msg['role']
            content = msg['content']

            if role == 'user':
                self.add_user_message(content)
            elif role == 'assistant':
                #EXTRA CLEANER IF ANYTHIN LEAKS THE FIRST TIME
                cleaned_content = self._remove_tool_usage_format(content)
                if not cleaned_content.strip() == "":
                    self.add_ai_message(cleaned_content)

    def _remove_tool_usage_format(self, content):
        """Remove tool usage JSON blocks from AI message"""
        import re
        cleaned = re.sub(
            r'```json\s*\{[^}]*(?:"work_environment"|"execute_code"|"set_session_name")[^}]*\}.*?```',
            '',
            content,
            flags=re.DOTALL
        )
        cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)
        return cleaned.strip()

    def _toggle_session_list(self):
        """Toggle session list collapsed/expanded."""
        if not hasattr(self, '_session_list_body'):
            return
        self._session_list_expanded = not self._session_list_expanded
        self._session_chevron.setText("▼" if self._session_list_expanded else "▶")
        self._session_list_body.setVisible(self._session_list_expanded)

    def refresh_session_list(self):
        """Refresh the session list in sidebar."""
        if not hasattr(self, 'session_list_layout'):
            return

        while self.session_list_layout.count() > 0:
            item = self.session_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sessions = self.controller.get_session_list()
        count = len(sessions)

        # Update count label
        if hasattr(self, '_session_count_lbl'):
            self._session_count_lbl.setText(str(count) if count else "")

        # Auto-collapse when ≥20 sessions (only on the first refresh that crosses the threshold)
        if hasattr(self, '_session_list_body') and hasattr(self, '_session_list_expanded'):
            if count >= 20 and self._session_list_expanded and not self._session_list_auto_collapsed:
                self._session_list_auto_collapsed = True
                self._session_list_expanded = False
                self._session_chevron.setText("▶")
                self._session_list_body.setVisible(False)

        for session in sessions:
            session_item = self._create_session_item(
                session['id'],
                session['name'],
                session['date'],
                is_active=(session['id'] == self.controller.current_session_id)
            )
            self.session_list_layout.addWidget(session_item)

        self.session_list_layout.addStretch()


    def _create_session_item(self, session_id, session_name, creation_date, is_active=False):
        """Create a session list item widget"""
        item_widget = QFrame()
        item_widget.setStyleSheet(f"""
            QFrame {{
                background-color: {"#2A2A2A" if is_active else "transparent"};
                border-radius: 6px;
                padding: 8px;
                margin: 2px 0px;
            }}
            QFrame:hover {{
                background-color: {"#2A2A2A" if is_active else "#252525"};
            }}
        """)

        layout = QHBoxLayout(item_widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)

        name_label = QLabel(session_name)
        name_label.setStyleSheet(f"""
            color: {"#E8EAED" if is_active else "#9AA0A6"};
            font-size: 12px;
            font-weight: {"bold" if is_active else "normal"};
        """)
        name_label.setWordWrap(True)
        content_layout.addWidget(name_label)

        date_label = QLabel(creation_date)
        date_label.setStyleSheet("color: #5F5F5F; font-size: 10px;")
        content_layout.addWidget(date_label)

        layout.addLayout(content_layout, 1)

        delete_btn = QPushButton("🗑️")
        delete_btn.setFixedSize(24, 24)
        delete_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 14px;
            }
            QPushButton:hover {
                background: rgba(234, 67, 53, 0.2);
                border-radius: 12px;
            }
        """)
        delete_btn.clicked.connect(lambda: self._delete_session_clicked(session_id))
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(delete_btn)

        item_widget.mousePressEvent = lambda e: self._load_session_clicked(session_id)
        item_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        return item_widget

    def _load_session_clicked(self, session_id):
        """Load session when clicked"""
        if session_id == self.controller.current_session_id:
            return

        self.controller.load_session(session_id)

    def _delete_session_clicked(self, session_id):
        """Delete session when delete button clicked"""
        if session_id == self.controller.current_session_id:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                'Delete Active Session',
                'Are you sure you want to delete the current session?\nA new session will be created.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.controller.delete_session(session_id)
        else:
            self.controller.delete_session(session_id)

    def add_user_message(self, message):
        """Add user message with three-dot menu"""
        message_widget = QFrame()
        message_widget.setStyleSheet("""
            QFrame {
                background-color: transparent;
                padding: 12px 16px;
            }
        """)

        message_layout = QHBoxLayout(message_widget)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(12)

        message_layout.addStretch()

        main_container_widget = QWidget()
        main_container_widget.setMaximumWidth(600)
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel("<b>You</b>")
        name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px;")
        main_container.addWidget(name_label)

        content_wrapper = QFrame()
        content_wrapper.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border: 1px solid #3C3C3C;
                border-radius: 12px;
            }
        """)

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(12, 12, 12, 8)
        content_wrapper_layout.setSpacing(8)

        text_label = QLabel()
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setText(self.render_markdown(message))
        text_label.setWordWrap(True)
        text_label.setOpenExternalLinks(True)
        text_label.setStyleSheet("""
                    QLabel {
                        color: #E8EAED;
                        font-size: 13px;
                        line-height: 1.5;
                        background: transparent;
                        border: none;
                    }
                """)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        content_wrapper_layout.addWidget(text_label)

        copy_btn_container = QHBoxLayout()
        copy_btn_container.addStretch()

        copy_btn = QPushButton("📋")
        copy_btn.setFixedSize(28, 28)
        copy_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.05);
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 6px;
                        font-size: 13px;
                        color: #9AA0A6;
                    }
                    QPushButton:hover {
                        background-color: rgba(255, 255, 255, 0.1);
                        border-color: rgba(168, 199, 250, 0.4);
                        color: #E8EAED;
                    }
                """)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(message))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn_container.addWidget(copy_btn)

        content_wrapper_layout.addLayout(copy_btn_container)

        main_container.addWidget(content_wrapper)

        # THREE-DOT MENU — below the bubble, right-aligned
        menu_row = QHBoxLayout()
        menu_row.setContentsMargins(0, 2, 0, 0)
        menu_btn = QPushButton("⋯")
        menu_btn.setFixedSize(24, 24)
        menu_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 18px;
                color: #5F5F5F;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                color: #9AA0A6;
            }
        """)
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        menu_row.addStretch()
        menu_row.addWidget(menu_btn)
        main_container.addLayout(menu_row)

        message_layout.addWidget(main_container_widget)

        # Avatar (RIGHT side for user)
        avatar = QLabel(self.user_avatar)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("""
            QLabel {
                background-color: #34A853;
                border-radius: 16px;
                font-size: 20px;
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
            }
        """)
        message_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        self.last_user_message_widget = message_widget

        message_index = len(self.message_widgets)
        message_data = {
            'widget': message_widget,
            'role': 'user',
            'content': message,
            'index': message_index,
            'text_label': text_label
        }
        self.message_widgets.append(message_data)

        menu_btn.clicked.connect(lambda: self._show_message_menu(message_data))

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        # Scroll AFTER animation completes so sb.maximum() and widget.height()
        # are accurate — firing at 120ms (mid-animation) caused the clamp
        # min(target, sb.maximum()) to cut the target short on long chats.
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))

    def add_ai_message(self, message):
        """Add AI message with markdown rendering, code blocks, and three-dot menu"""
        if self.voice_enabled:
            display_message = self._clean_emotion_brackets(message)
        else:
            display_message = message

        display_message = self._preprocess_latex(display_message)

        message_widget = QFrame()
        message_widget.setStyleSheet("""
            QFrame {
                background-color: transparent;
                padding: 12px 16px;
            }
        """)

        message_layout = QHBoxLayout(message_widget)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.setSpacing(12)

        # Avatar (LEFT side for AI)
        avatar = QLabel(self.bot_avatar)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet("""
            QLabel {
                background-color: #1A73E8;
                border-radius: 16px;
                font-size: 20px;
                min-width: 32px;
                min-height: 32px;
                max-width: 32px;
                max-height: 32px;
            }
        """)
        message_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        main_container_widget = QWidget()
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel("<b>Systema Auxilium</b>")
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px;")
        main_container.addWidget(name_label)

        content_wrapper = QFrame()
        content_wrapper.setStyleSheet("""
            QFrame {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 12px;
            }
        """)

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(10, 10, 10, 10)
        content_wrapper_layout.setSpacing(8)

        parts = self.render_markdown_with_code_blocks(display_message)
        first_text_label = None

        if isinstance(parts, list):
            for part in parts:
                if part[0] == 'text':
                    text_label = QLabel()
                    text_label.setTextFormat(Qt.TextFormat.RichText)
                    text_label.setText(self.render_markdown(part[1]))
                    text_label.setWordWrap(True)
                    text_label.setOpenExternalLinks(True)
                    text_label.setStyleSheet("""
                        QLabel {
                            color: #BDC1C6;
                            font-size: 13px;
                            line-height: 1.5;
                            background: transparent;
                            border: none;
                        }
                    """)
                    text_label.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse |
                        Qt.TextInteractionFlag.LinksAccessibleByMouse
                    )
                    content_wrapper_layout.addWidget(text_label)
                    if not first_text_label:
                        first_text_label = text_label
                elif part[0] == 'code':
                    code_widget = CodeBlockWidget(part[1], part[2])
                    content_wrapper_layout.addWidget(code_widget)
        else:
            text_label = QLabel()
            text_label.setTextFormat(Qt.TextFormat.RichText)
            text_label.setText(parts)
            text_label.setWordWrap(True)
            text_label.setOpenExternalLinks(True)
            text_label.setStyleSheet("""
                QLabel {
                    color: #BDC1C6;
                    font-size: 13px;
                    line-height: 1.5;
                    background: transparent;
                    border: none;
                }
            """)
            text_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            content_wrapper_layout.addWidget(text_label)
            first_text_label = text_label

        copy_btn_container = QHBoxLayout()
        copy_btn = QPushButton("📋")
        copy_btn.setFixedSize(28, 28)
        copy_btn.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.05);
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 6px;
                        font-size: 13px;
                        color: #9AA0A6;
                    }
                    QPushButton:hover {
                        background-color: rgba(255, 255, 255, 0.1);
                        border-color: rgba(168, 199, 250, 0.4);
                        color: #E8EAED;
                    }
                """)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(display_message))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn_container.addWidget(copy_btn)
        copy_btn_container.addStretch()
        content_wrapper_layout.addLayout(copy_btn_container)

        main_container.addWidget(content_wrapper)

        # THREE-DOT MENU — below the bubble, left-aligned
        ai_menu_row = QHBoxLayout()
        ai_menu_row.setContentsMargins(0, 2, 0, 0)
        menu_btn = QPushButton("⋯")
        menu_btn.setFixedSize(24, 24)
        menu_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                font-size: 18px;
                color: #5F5F5F;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                color: #9AA0A6;
            }
        """)
        menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ai_menu_row.addWidget(menu_btn)
        ai_menu_row.addStretch()
        main_container.addLayout(ai_menu_row)

        message_layout.addWidget(main_container_widget)
        message_layout.addStretch()

        message_index = len(self.message_widgets)
        message_data = {
            'widget': message_widget,
            'role': 'assistant',
            'content': message,
            'display_content': display_message,
            'index': message_index,
            'text_label': first_text_label,
            'content_wrapper_layout': content_wrapper_layout
        }
        self.message_widgets.append(message_data)

        menu_btn.clicked.connect(lambda: self._show_message_menu(message_data))

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))

    def _clean_emotion_brackets(self, text):
        """Remove ElevenLabs emotion brackets from text for display"""
        import re
        cleaned = re.sub(r'\[([^\]]+)\]', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    def add_system_message(self, message):
        """Add system message"""
        message_widget = QFrame()
        message_widget.setStyleSheet("""
            QFrame {
                background-color: transparent;
                padding: 8px 16px;
            }
        """)

        message_layout = QHBoxLayout(message_widget)
        message_layout.setContentsMargins(0, 0, 0, 0)

        text_label = QLabel()
        text_label.setWordWrap(True)
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setOpenExternalLinks(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        text_label.setText(self.render_markdown(message))
        text_label.setStyleSheet("""
            QLabel {
                background-color: rgba(42, 42, 42, 0.5);
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 10px 16px;
                color: #9AA0A6;
                font-size: 11px;
                line-height: 1.4;
            }
        """)
        message_layout.addWidget(text_label)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))

    def add_skill_card_message(self, skill_name: str, loaded: bool):
        """
        Add a skill-status entity card to the chat message list.
        Shows the skill name, a loaded/unloaded badge, and an Unload/Load button
        for manual user control.
        """
        from PyQt6.QtWidgets import QSizePolicy

        # Determine styling based on state
        if loaded:
            badge_text = "● Loaded"
            badge_color = "#4CAF50"
            badge_bg = "#1A2B1A"
            action_text = "Unload"
            action_color = "#C0392B"
            action_bg = "#3C1A1A"
            action_hover = "#4A2020"
            icon = "⚡"
        else:
            badge_text = "○ Unloaded"
            badge_color = "#9AA0A6"
            badge_bg = "#2A2A2A"
            action_text = "Load"
            action_color = "#4CAF50"
            action_bg = "#1A2B1A"
            action_hover = "#223322"
            icon = "⚡"

        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: #1E1E2E;
                border: 1px solid #3C3C5C;
                border-radius: 8px;
                margin: 2px 40px;
            }}
        """)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(10)

        # Icon
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("color: #7C7CFF; font-size: 14px; background: transparent;")
        icon_lbl.setFixedWidth(18)
        card_layout.addWidget(icon_lbl)

        # Skill name
        name_lbl = QLabel(f"<b>{skill_name}</b>")
        name_lbl.setStyleSheet(f"color: #C8CAFF; font-size: 12px; background: transparent;")
        card_layout.addWidget(name_lbl, stretch=1)

        # Badge
        badge_lbl = QLabel(badge_text)
        badge_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {badge_bg};
                color: {badge_color};
                border-radius: 4px;
                font-size: 10px;
                padding: 2px 8px;
            }}
        """)
        card_layout.addWidget(badge_lbl)

        # Action button
        action_btn = QPushButton(action_text)
        action_btn.setFixedHeight(24)
        action_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {action_bg};
                color: {action_color};
                border: 1px solid {action_color};
                border-radius: 4px;
                font-size: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover {{
                background-color: {action_hover};
            }}
        """)

        # Capture current state in closure
        _loaded = loaded
        _name = skill_name

        def _on_action():
            skill_mgr = None
            if hasattr(self, 'controller') and self.controller:
                skill_mgr = getattr(self.controller.ai, 'skill_manager', None)
            if skill_mgr is None:
                return
            if _loaded:
                ok, msg = skill_mgr.unload_skill(_name)
            else:
                ok, msg = skill_mgr.load_skill(_name)
            if ok:
                # Update badge and button to reflect new state
                new_loaded = not _loaded
                if new_loaded:
                    badge_lbl.setText("● Loaded")
                    badge_lbl.setStyleSheet(f"""
                        QLabel {{
                            background-color: #1A2B1A; color: #4CAF50;
                            border-radius: 4px; font-size: 10px; padding: 2px 8px;
                        }}
                    """)
                    action_btn.setText("Unload")
                    action_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: #3C1A1A; color: #C0392B;
                            border: 1px solid #C0392B; border-radius: 4px;
                            font-size: 10px; padding: 0 10px;
                        }}
                        QPushButton:hover {{ background-color: #4A2020; }}
                    """)
                else:
                    badge_lbl.setText("○ Unloaded")
                    badge_lbl.setStyleSheet(f"""
                        QLabel {{
                            background-color: #2A2A2A; color: #9AA0A6;
                            border-radius: 4px; font-size: 10px; padding: 2px 8px;
                        }}
                    """)
                    action_btn.setText("Load")
                    action_btn.setStyleSheet(f"""
                        QPushButton {{
                            background-color: #1A2B1A; color: #4CAF50;
                            border: 1px solid #4CAF50; border-radius: 4px;
                            font-size: 10px; padding: 0 10px;
                        }}
                        QPushButton:hover {{ background-color: #223322; }}
                    """)
                # Rewire button with new state
                action_btn.clicked.disconnect()
                _new_loaded_ref = [new_loaded]

                def _on_action_updated(loaded_ref=_new_loaded_ref):
                    smgr = None
                    if hasattr(self, 'controller') and self.controller:
                        smgr = getattr(self.controller.ai, 'skill_manager', None)
                    if smgr is None:
                        return
                    if loaded_ref[0]:
                        ok2, _ = smgr.unload_skill(_name)
                        if ok2:
                            loaded_ref[0] = False
                            badge_lbl.setText("○ Unloaded")
                            badge_lbl.setStyleSheet("""
                                QLabel {
                                    background-color: #2A2A2A; color: #9AA0A6;
                                    border-radius: 4px; font-size: 10px; padding: 2px 8px;
                                }
                            """)
                            action_btn.setText("Load")
                            action_btn.setStyleSheet("""
                                QPushButton {
                                    background-color: #1A2B1A; color: #4CAF50;
                                    border: 1px solid #4CAF50; border-radius: 4px;
                                    font-size: 10px; padding: 0 10px;
                                }
                                QPushButton:hover { background-color: #223322; }
                            """)
                    else:
                        ok2, _ = smgr.load_skill(_name)
                        if ok2:
                            loaded_ref[0] = True
                            badge_lbl.setText("● Loaded")
                            badge_lbl.setStyleSheet("""
                                QLabel {
                                    background-color: #1A2B1A; color: #4CAF50;
                                    border-radius: 4px; font-size: 10px; padding: 2px 8px;
                                }
                            """)
                            action_btn.setText("Unload")
                            action_btn.setStyleSheet("""
                                QPushButton {
                                    background-color: #3C1A1A; color: #C0392B;
                                    border: 1px solid #C0392B; border-radius: 4px;
                                    font-size: 10px; padding: 0 10px;
                                }
                                QPushButton:hover { background-color: #4A2020; }
                            """)

                action_btn.clicked.connect(_on_action_updated)
            else:
                # Show brief error feedback on badge
                badge_lbl.setText(f"⚠ {msg[:30]}")
                badge_lbl.setStyleSheet("""
                    QLabel {
                        background-color: #3C2A00; color: #FFC107;
                        border-radius: 4px; font-size: 10px; padding: 2px 8px;
                    }
                """)

        action_btn.clicked.connect(_on_action)
        card_layout.addWidget(action_btn)

        # Outer wrapper for spacing
        outer = QWidget()
        outer.setStyleSheet("background: transparent;")
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(0, 2, 0, 2)
        outer_layout.addWidget(card)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, outer)
        self._animate_message_in(outer, on_settled=lambda: self.scroll_to_widget(outer))

    def copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText("✓ Copied to clipboard")
        QTimer.singleShot(ANIM_STATUS_CLEAR_MS, lambda: self.status_label.setText(""))

    def warn_loaded_skills_if_any(self):
        """
        If any skills are currently loaded, emit a light system warning message
        in the chat so the user knows context window is being used.
        Called on startup and on new-session creation.
        """
        skill_manager = None
        if hasattr(self, 'controller') and self.controller:
            skill_manager = getattr(self.controller.ai, 'skill_manager', None)
        if not skill_manager:
            return
        loaded = skill_manager.get_loaded_skills()
        if not loaded:
            return
        names = list(loaded.keys())
        if len(names) == 1:
            label = f"**{names[0]}**"
        else:
            label = ", ".join(f"**{n}**" for n in names)
        msg = (
            f"⚡ Skill{'s' if len(names) > 1 else ''} {label} "
            f"{'are' if len(names) > 1 else 'is'} still loaded from a previous session. "
            f"This uses extra LLM context window space on every message. "
            f"Unload in the sidebar under ⚡ Skills, or tell Systema Auxilium to unload "
            f"{'them' if len(names) > 1 else 'it'}."
        )
        self.add_system_message(msg)

    def scroll_to_bottom(self):
        """Legacy helper — scrolls to absolute bottom (used by voice/thinking flows)."""
        QTimer.singleShot(50, self._do_scroll)

    def _do_scroll(self):
        """Instant scroll to bottom (legacy, non-animated)."""
        if hasattr(self, 'chat_scroll_area'):
            sb = self.chat_scroll_area.verticalScrollBar()
            self._animated_scroll_to(sb.maximum())
        else:
            scroll_area = self.chat_widget.parent().parent()
            if isinstance(scroll_area, QScrollArea):
                sb = scroll_area.verticalScrollBar()
                sb.setValue(sb.maximum())

    # ── Smart scroll-to-message ────────────────────────────────────────────

    def scroll_to_widget(self, widget):
        """
        Scroll so the new message is optimally visible.

        IMPORTANT: must only be called AFTER the message's pop-in animation
        has finished, so that:
          - widget.height() returns the real rendered height
          - sb.maximum() reflects the full content including the new message
        Calling this mid-animation causes sb.maximum() to be stale (too small),
        making the clamp cut the target short on long chats.
        """
        if not hasattr(self, 'chat_scroll_area'):
            return
        if self._user_scrolling:
            self._user_scrolling = False
            return

        try:
            if not widget.isVisible():
                return
        except RuntimeError:
            return  # Widget already deleted

        sb = self.chat_scroll_area.verticalScrollBar()
        viewport_h = self.chat_scroll_area.viewport().height()

        try:
            pos_in_content = widget.mapTo(self.chat_widget, widget.rect().topLeft())
        except RuntimeError:
            return

        widget_top = pos_in_content.y()

        # Use actual rendered height — only valid after animation completes.
        # sizeHint() can be stale; widget.height() reflects the live layout.
        widget_h = widget.height()
        if widget_h < 20:
            widget_h = widget.sizeHint().height()
        if widget_h < 20:
            widget_h = 200  # safe fallback

        if widget_h <= viewport_h - 40:
            # Small message: centre it vertically in the viewport
            target = widget_top - (viewport_h - widget_h) // 2
        else:
            # Tall message: show from top with a small padding
            target = widget_top - 16

        # sb.maximum() is now accurate because the animation is complete.
        target = max(0, min(target, sb.maximum()))
        self._animated_scroll_to(target)

    def _animated_scroll_to(self, target_value: int):
        """Animate the chat scrollbar to target_value using QPropertyAnimation."""
        if not hasattr(self, 'chat_scroll_area'):
            return

        sb = self.chat_scroll_area.verticalScrollBar()
        current = sb.value()
        if abs(current - target_value) < 4:
            return

        if self._scroll_anim is not None:
            if self._scroll_anim.state() == QPropertyAnimation.State.Running:
                self._scroll_anim.stop()

        anim = QPropertyAnimation(sb, b"value")
        distance = abs(target_value - current)
        duration = max(ANIM_SCROLL_MIN_MS, min(ANIM_SCROLL_MAX_MS, distance // 2))
        anim.setDuration(duration)
        anim.setStartValue(current)
        anim.setEndValue(target_value)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._scroll_anim = anim
        anim.start()

    def _animated_sidebar_scroll_to(self, target_value: int):
        """Animate the sidebar scrollbar to target_value."""
        if not hasattr(self, 'sidebar_scroll'):
            return

        sb = self.sidebar_scroll.verticalScrollBar()
        current = sb.value()
        if abs(current - target_value) < 4:
            return

        if self._sidebar_scroll_anim is not None:
            if self._sidebar_scroll_anim.state() == QPropertyAnimation.State.Running:
                self._sidebar_scroll_anim.stop()

        anim = QPropertyAnimation(sb, b"value")
        distance = abs(target_value - current)
        duration = max(ANIM_SCROLL_MIN_MS, min(ANIM_SCROLL_MAX_MS, distance // 2))
        anim.setDuration(duration)
        anim.setStartValue(current)
        anim.setEndValue(target_value)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._sidebar_scroll_anim = anim
        anim.start()

    # ── Inertia scroll — main chat ─────────────────────────────────────────

    def _inertia_tick(self):
        """Called ~70fps while inertia is active for main chat."""
        if not hasattr(self, 'chat_scroll_area'):
            self._inertia_timer.stop()
            return

        sb = self.chat_scroll_area.verticalScrollBar()
        self._inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._inertia_timer.stop()
            self._inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    # ── Inertia scroll — sidebar ───────────────────────────────────────────

    def _sidebar_inertia_tick(self):
        """Called ~70fps while inertia is active for sidebar."""
        if not hasattr(self, 'sidebar_scroll'):
            self._sidebar_inertia_timer.stop()
            return

        sb = self.sidebar_scroll.verticalScrollBar()
        self._sidebar_inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._sidebar_inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._sidebar_inertia_timer.stop()
            self._sidebar_inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._sidebar_inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    # ── Inertia scroll — input field ───────────────────────────────────────

    def _input_inertia_tick(self):
        """Called ~70fps while inertia is active for input field."""
        if not hasattr(self, 'input_field'):
            self._input_inertia_timer.stop()
            return

        sb = self.input_field.text_input.verticalScrollBar()
        self._input_inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._input_inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._input_inertia_timer.stop()
            self._input_inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._input_inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    # ═══════════════════════════════════════════════════════════
    # MESSAGE CONTROL METHODS (Edit, Delete, Rewind, Regenerate)
    # ═══════════════════════════════════════════════════════════

    def _show_message_menu(self, message_data):
        """Show context menu for message"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3C3C3C;
            }
        """)

        edit_action = QAction("✏️ Edit Message", self)
        edit_action.triggered.connect(lambda: self._edit_message(message_data))
        menu.addAction(edit_action)

        if message_data['role'] == 'assistant':
            regen_action = QAction("🔄 Regenerate Response", self)
            regen_action.triggered.connect(lambda: self._regenerate_response(message_data))
            menu.addAction(regen_action)

        menu.addSeparator()

        delete_action = QAction("🗑️ Delete Message", self)
        delete_action.triggered.connect(lambda: self._delete_message(message_data))
        menu.addAction(delete_action)

        rewind_action = QAction("⏪ Rewind to Here", self)
        rewind_action.triggered.connect(lambda: self._rewind_to_message(message_data))
        menu.addAction(rewind_action)

        menu.exec(QCursor.pos())

    def _edit_message(self, message_data):
        """Edit a message"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Message")
        dialog.setMinimumWidth(500)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #212121;
            }
            QLabel {
                color: #E8EAED;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(dialog)

        header = QLabel(f"<b>Edit {'Your' if message_data['role'] == 'user' else 'AI'} Message</b>")
        layout.addWidget(header)

        text_edit = QTextEdit()
        text_edit.setPlainText(message_data['content'])
        text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 8px;
                color: #E8EAED;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            }
        """)
        text_edit.setMinimumHeight(150)
        layout.addWidget(text_edit)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 6px;
                padding: 8px 16px;
                color: #E8EAED;
            }
            QPushButton:hover {
                background-color: #3C3C3C;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)

        save_text = "Save & Regenerate" if message_data['role'] == 'user' else "Save"
        save_btn = QPushButton(save_text)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #1A73E8;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #1557B0;
            }
        """)
        save_btn.clicked.connect(lambda: self._save_edited_message(message_data, text_edit.toPlainText(), dialog))
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def _save_edited_message(self, message_data, new_content, dialog):
        """Save edited message and handle consequences"""
        if not new_content.strip():
            return

        message_data['content'] = new_content

        history_index = self._get_history_index(message_data)
        if history_index >= 0:
            self.controller.ai.conversation_history[history_index]['content'] = new_content

        if message_data.get('text_label'):
            message_data['text_label'].setText(self.render_markdown(new_content))

        if message_data['role'] == 'user':
            self._rewind_to_message(message_data, keep_message=True)
            self.controller.send_message(new_content)

        self.controller._auto_save_session()

        dialog.accept()

    def _delete_message(self, message_data):
        """Delete a message — data removed sync, widget animated out async."""
        history_index = self._get_history_index(message_data)

        if message_data in self.message_widgets:
            self.message_widgets.remove(message_data)
        for i, msg in enumerate(self.message_widgets):
            msg['index'] = i
        if history_index >= 0:
            try:
                self.controller.ai.conversation_history.pop(history_index)
            except IndexError:
                pass
        self.controller._auto_save_session()

        widget = message_data['widget']
        def _destroy():
            self.chat_layout.removeWidget(widget)
            widget.deleteLater()
        self._animate_message_out(widget, _destroy)

    def _rewind_to_message(self, message_data, keep_message=False):
        """Rewind conversation — data truncated sync, widgets animated out async."""
        target_index = message_data['index']
        cutoff = target_index + 1 if keep_message else target_index

        widgets_to_remove = [md['widget'] for md in self.message_widgets[cutoff:]]

        self.message_widgets = self.message_widgets[:cutoff]

        history_index = self._get_history_index(message_data)
        if history_index >= 0:
            if keep_message:
                history_index += 1
            self.controller.ai.conversation_history = \
                self.controller.ai.conversation_history[:history_index]

        self.controller._auto_save_session()

        for widget in widgets_to_remove:
            def _destroy(w=widget):
                self.chat_layout.removeWidget(w)
                w.deleteLater()
            self._animate_message_out(widget, _destroy)


    def _regenerate_response(self, message_data):
        """Regenerate AI response"""
        target_index = message_data['index']

        user_msg = None
        for i in range(target_index - 1, -1, -1):
            if self.message_widgets[i]['role'] == 'user':
                user_msg = self.message_widgets[i]
                break

        if not user_msg:
            return

        self._rewind_to_message(user_msg, keep_message=True)

        user_message = user_msg['content']
        self.controller.send_message(user_message)

    def _get_history_index(self, message_data):
        """Get the index of a message in conversation_history."""
        idx = message_data.get('index', -1)
        if idx < 0 or idx >= len(self.controller.ai.conversation_history):
            return -1
        return idx

    def send_message(self):
        """Send message"""
        message = self.input_field.toPlainText().strip()
        if not message:
            return

        # Sending is an explicit user action — always scroll to the new bubble
        # regardless of whether the user was scrolling up to read older messages.
        self._user_scrolling = False

        was_manually_resized = self.input_field.text_input.manual_resize
        stored_height = self.input_field.text_input.height() if was_manually_resized else None

        image_path = None
        if self.controller.get_ai_provider() == 'puter' and self.attached_image:
            image_path = self.attached_image
            self.add_system_message(f"📎 Image attached: {image_path}")

        if self.force_mode == 'work_environment':
            message = "[VERY CRITICAL THE USER HAS ENFORCED: work_environment ONLY and FULFILL THIS TASK EFFICIENTLY (ignore if the message of the user doesn't request of anything)] " + message
        elif self.force_mode == 'execute_code':
            message = "[VERY CRITICAL THE USER HAS ENFORCED: execute_code ONLY and RUN a SINGLE PYTHON CODE TO DO THIS REQUEST(ignore if the message of the user doesn't request of anything)] " + message

        display_message = self.input_field.toPlainText().strip()

        self.last_sent_message = display_message

        self.add_user_message(display_message)

        self.input_field.clear()
        self.attached_image = None

        if was_manually_resized and stored_height:
            self.input_field.text_input.manual_resize = True
            self.input_field.text_input.setFixedHeight(stored_height)

        if image_path:
            self.controller.send_message_with_image(message, image_path)
        else:
            self.controller.send_message(message)

    def browse_for_file(self):
        """Alternative to drag & drop - works in admin mode"""
        from PyQt6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select File",
            "",
            "All Files (*.*)"
        )

        if file_path:
            file_path = self.clean_file_path(file_path)

            if self.controller.get_ai_provider() == 'puter':
                valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                if any(file_path.lower().endswith(ext) for ext in valid_extensions):
                    self.attached_image = file_path
                    self.add_system_message(
                        f"📎 **Image Ready:** {file_path}\n\nType your message and press Enter."
                    )
                    return

            if self.should_quote_path(file_path):
                file_path = f'"{file_path}"'

            current_text = self.input_field.toPlainText()
            if current_text:
                self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
            else:
                self.input_field.text_input.setPlainText(file_path)

    def set_input_enabled(self, enabled):
        """Enable/disable input"""
        self.input_field.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.input_field.setPlaceholderText("Send a message... (Shift+Enter for new line)")
            # Restore focus so the user can type immediately without clicking
            self.input_field.text_input.setFocus()
        else:
            self.input_field.setPlaceholderText("AI is working... please wait")

    def show_ai_message(self, message):
        if self.voice_enabled and not self.controller.ai.tool_manager.in_work_mode:
            self.log("[Voice] Buffering message, starting TTS...")

            self.pending_voice_message = message
            self.waiting_for_playback = True

            self.start_thinking_animation()

            self.speak_ai_response(message)
        else:
            self.add_ai_message(message)

    def log(self, msg):
        """Helper for logging"""
        print(f"[ChatWindow] {msg}")

    def on_voice_playback_started(self):
        """Called when TTS playback actually starts (from background thread)"""
        self.log("[Voice] Playback started callback received")
        self.voice_playback_signal.emit()

    def _handle_voice_playback_on_main_thread(self):
        """Handle voice playback on main Qt thread"""
        self.log("[Voice] Processing playback on main thread")

        if self.waiting_for_playback and self.pending_voice_message:
            self.log("[Voice] Displaying buffered message NOW")
            self.waiting_for_playback = False

            self.stop_thinking_animation()

            self.add_ai_message(self.pending_voice_message)

            self.pending_voice_message = None

            self.log("[Voice] Message displayed successfully")
        else:
            self.log("[Voice] No pending message to display")

    def speak_ai_response(self, text):
        """Speak AI response using TTS (in background thread)"""

        def _speak():
            self.controller.speak_text(text)

        thread = threading.Thread(target=_speak, daemon=True)
        thread.start()

    def handle_ai_response(self, result):
        """Handle AI response"""
        if not result['thinking'] and result.get('response'):
            self.add_ai_message(result['response'])

    def start_thinking_animation(self):
        """Start thinking animation"""
        if self.thinking_timer is None:
            self.thinking_timer = QTimer()
            self.thinking_timer.timeout.connect(self.update_thinking_animation)

        self.thinking_dots = 0
        self.thinking_timer.start(500)

    def stop_thinking_animation(self):
        """Stop thinking animation"""
        if self.thinking_timer:
            self.thinking_timer.stop()
        self.status_label.setText("")

    def update_thinking_animation(self):
        """Update thinking animation"""
        self.thinking_dots = (self.thinking_dots + 1) % 4
        dots = "●" * self.thinking_dots + "○" * (3 - self.thinking_dots)
        self.status_label.setText(f"AI is thinking {dots}")

    def interrupt_response(self):
        """Interrupt current AI response and restore message to input"""
        if not self.controller.is_processing and not self.controller.ai.tool_manager.in_work_mode:
            return

        success = self.controller.interrupt_request()

        if success:
            if self.last_user_message_widget:
                self.chat_layout.removeWidget(self.last_user_message_widget)
                self.last_user_message_widget.deleteLater()
                self.last_user_message_widget = None

            if self.last_sent_message:
                current_text = self.input_field.toPlainText()
                if current_text:
                    self.input_field.text_input.setPlainText(self.last_sent_message + "\n\n" + current_text)
                else:
                    self.input_field.text_input.setPlainText(self.last_sent_message)
                self.last_sent_message = None

            self.interrupt_btn.hide()
            self.send_btn.show()

            self.hide_thinking()

            self.add_system_message("⚡️ **Response interrupted - message returned to input**")

    def interrupt_work_mode(self):
        """Legacy method - now redirects to interrupt_response"""
        self.interrupt_response()

    def interrupt_voice(self):
        """Interrupt TTS playback"""
        self.controller.voice_handler.interrupt_speech()
        self.voice_interrupt_btn.hide()

    def show_thinking(self):
        """Show thinking animation"""
        self.start_thinking_animation()
        self.thinking_label_shown = True
        self.set_input_enabled(False)
        self.send_btn.hide()
        self.interrupt_btn.show()

    def hide_thinking(self):
        """Hide thinking animation"""
        self.stop_thinking_animation()
        self.thinking_label_shown = False
        self.set_input_enabled(True)
        self.interrupt_btn.hide()
        self.send_btn.show()

    def apply_rounded_mask(self):
        """Apply rounded corners mask"""
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, event):
        """Handle window resize."""
        super().resizeEvent(event)
        self.apply_rounded_mask()
        if hasattr(self, 'chat_widget'):
            self.chat_widget.updateGeometry()

        if hasattr(self, 'sidebar') and hasattr(self, 'container'):
            container_h = self.container.height()
            if self.sidebar_visible:
                self.sidebar.setGeometry(0, 0, 240, container_h)
            else:
                self.sidebar.setGeometry(-240, 0, 240, container_h)

        if hasattr(self, 'toggle_sidebar_btn'):
            self.toggle_sidebar_btn.raise_()

        if hasattr(self, 'resize_handles'):
            self.position_resize_handles()

        if hasattr(self, 'resize_timer'):
            self.resize_timer.stop()
            self.resize_timer.start(1000)

    def save_window_geometry(self):
        """Save window size and position to config"""
        try:
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

            config['window_geometry'] = {
                'x': self.x(),
                'y': self.y(),
                'width': self.width(),
                'height': self.height()
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving window geometry: {e}")

    def load_window_geometry(self):
        """Load window size and position from config"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    geometry = config.get('window_geometry')
                    if geometry:
                        self.setGeometry(
                            geometry['x'],
                            geometry['y'],
                            geometry['width'],
                            geometry['height']
                        )
        except Exception as e:
            print(f"Error loading window geometry: {e}")

    def header_mouse_press(self, event):
        """Handle mouse press on header for dragging"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def header_mouse_move(self, event):
        """Handle mouse move on header for dragging"""
        if self.dragging:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def header_mouse_release(self, event):
        """Handle mouse release"""
        self.dragging = False
        event.accept()

    def create_resize_handles(self):
        """Create invisible resize handles around window edges"""
        handle_size = 8
        corner_size = 16

        self.resize_handles = {}

        edges = {
            'top': (0, 0, 0, handle_size, Qt.CursorShape.SizeVerCursor),
            'bottom': (0, 0, 0, handle_size, Qt.CursorShape.SizeVerCursor),
            'left': (0, 0, handle_size, 0, Qt.CursorShape.SizeHorCursor),
            'right': (0, 0, handle_size, 0, Qt.CursorShape.SizeHorCursor),
        }

        for edge_name, (l, t, w, h, cursor) in edges.items():
            handle = QFrame(self)
            handle.setStyleSheet("background-color: transparent;")
            handle.setCursor(cursor)
            handle.edge_type = edge_name
            handle.installEventFilter(self)
            self.resize_handles[edge_name] = handle
            handle.raise_()

        corners = {
            'top-left': (0, 0, corner_size, corner_size, Qt.CursorShape.SizeFDiagCursor),
            'top-right': (0, 0, corner_size, corner_size, Qt.CursorShape.SizeBDiagCursor),
            'bottom-left': (0, 0, corner_size, corner_size, Qt.CursorShape.SizeBDiagCursor),
            'bottom-right': (0, 0, corner_size, corner_size, Qt.CursorShape.SizeFDiagCursor),
        }

        for corner_name, (l, t, w, h, cursor) in corners.items():
            handle = QFrame(self)
            handle.setStyleSheet("background-color: transparent;")
            handle.setCursor(cursor)
            handle.edge_type = corner_name
            handle.installEventFilter(self)
            self.resize_handles[corner_name] = handle
            handle.raise_()

        self.position_resize_handles()

    def position_resize_handles(self):
        """Position resize handles based on window size"""
        w = self.width()
        h = self.height()
        handle_size = 8
        corner_size = 16
        header_height = 50

        self.resize_handles['top'].setGeometry(corner_size, header_height, w - 2 * corner_size, handle_size)
        self.resize_handles['bottom'].setGeometry(corner_size, h - handle_size, w - 2 * corner_size, handle_size)
        self.resize_handles['left'].setGeometry(0, corner_size, handle_size, h - 2 * corner_size)
        self.resize_handles['right'].setGeometry(w - handle_size, corner_size, handle_size, h - 2 * corner_size)

        self.resize_handles['top-left'].setGeometry(0, header_height, corner_size, corner_size)
        self.resize_handles['top-right'].setGeometry(w - corner_size, header_height, corner_size, corner_size)
        self.resize_handles['bottom-left'].setGeometry(0, h - corner_size, corner_size, corner_size)
        self.resize_handles['bottom-right'].setGeometry(w - corner_size, h - corner_size, corner_size, corner_size)

    def eventFilter(self, obj, event):
        """Handle resize handle events and smooth scroll viewport events."""
        from PyQt6.QtCore import QEvent

        # ── Smooth inertia scroll — SIDEBAR viewport ───────────────────────
        if hasattr(self, 'sidebar_scroll') and obj is self.sidebar_scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                if self._sidebar_scroll_anim is not None:
                    try:
                        if self._sidebar_scroll_anim.state() == QPropertyAnimation.State.Running:
                            self._sidebar_scroll_anim.stop()
                    except RuntimeError:
                        pass

                delta = event.angleDelta().y()
                self._sidebar_inertia_velocity -= delta * ANIM_INERTIA_SCALE
                self._sidebar_inertia_velocity = max(
                    -ANIM_INERTIA_MAX_VELOCITY,
                    min(ANIM_INERTIA_MAX_VELOCITY, self._sidebar_inertia_velocity)
                )
                if not self._sidebar_inertia_timer.isActive():
                    self._sidebar_inertia_timer.start()
                return True

            elif event.type() == QEvent.Type.MouseButtonPress:
                if self._sidebar_scroll_anim is not None:
                    try:
                        if self._sidebar_scroll_anim.state() == QPropertyAnimation.State.Running:
                            self._sidebar_scroll_anim.stop()
                    except RuntimeError:
                        pass

        # ── Smooth inertia scroll — INPUT FIELD viewport ───────────────────
        if hasattr(self, 'input_field') and obj is self.input_field.text_input.viewport():
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                self._input_inertia_velocity -= delta * ANIM_INERTIA_SCALE
                self._input_inertia_velocity = max(
                    -ANIM_INERTIA_MAX_VELOCITY,
                    min(ANIM_INERTIA_MAX_VELOCITY, self._input_inertia_velocity)
                )
                if not self._input_inertia_timer.isActive():
                    self._input_inertia_timer.start()
                return True

        # ── Smooth inertia scroll — MAIN CHAT viewport ────────────────────
        if hasattr(self, 'chat_scroll_area') and obj is self.chat_scroll_area.viewport():
            if event.type() == QEvent.Type.Wheel:
                if self._scroll_anim is not None:
                    try:
                        if self._scroll_anim.state() == QPropertyAnimation.State.Running:
                            self._scroll_anim.stop()
                    except RuntimeError:
                        pass
                self._user_scrolling = True

                delta = event.angleDelta().y()
                self._inertia_velocity -= delta * ANIM_INERTIA_SCALE
                self._inertia_velocity = max(-ANIM_INERTIA_MAX_VELOCITY, min(ANIM_INERTIA_MAX_VELOCITY, self._inertia_velocity))

                if not self._inertia_timer.isActive():
                    self._inertia_timer.start()

                return True

            elif event.type() == QEvent.Type.MouseButtonPress:
                self._user_scrolling = True
                if self._scroll_anim is not None:
                    try:
                        if self._scroll_anim.state() == QPropertyAnimation.State.Running:
                            self._scroll_anim.stop()
                    except RuntimeError:
                        pass

        # ── Window resize handle events ────────────────────────────────────
        if hasattr(obj, 'edge_type'):
            if event.type() == event.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.resizing = True
                    self.resize_edge = obj.edge_type
                    self.resize_start_geometry = self.geometry()
                    self.resize_start_pos = event.globalPosition().toPoint()
                    return True

            elif event.type() == event.Type.MouseButtonRelease:
                if self.resizing:
                    self.resizing = False
                    self.resize_edge = None
                    return True

            elif event.type() == event.Type.MouseMove and self.resizing:
                delta = event.globalPosition().toPoint() - self.resize_start_pos
                new_geo = QRect(self.resize_start_geometry)

                if 'left' in self.resize_edge:
                    new_geo.setLeft(self.resize_start_geometry.left() + delta.x())
                if 'right' in self.resize_edge:
                    new_geo.setRight(self.resize_start_geometry.right() + delta.x())
                if 'top' in self.resize_edge:
                    new_geo.setTop(self.resize_start_geometry.top() + delta.y())
                if 'bottom' in self.resize_edge:
                    new_geo.setBottom(self.resize_start_geometry.bottom() + delta.y())

                if new_geo.width() >= self.minimumWidth() and new_geo.height() >= self.minimumHeight():
                    self.setGeometry(new_geo)
                return True

        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        """Handle drag enter"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle file drop"""
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        if not files:
            return

        file_path = files[0]

        file_path = self.clean_file_path(file_path)

        if self.controller.get_ai_provider() == 'puter':
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
            if any(file_path.lower().endswith(ext) for ext in valid_extensions):
                self.attached_image = file_path
                self.add_system_message(
                    f"📎 **Image Ready:** {file_path}\n\nType your message and press Enter to send with image.")
                return

        if self.should_quote_path(file_path):
            file_path = f'"{file_path}"'

        current_text = self.input_field.toPlainText()
        if current_text:
            self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
        else:
            self.input_field.text_input.setPlainText(file_path)

    def clean_file_path(self, path):
        """Clean file path by removing file:/// prefix and normalizing"""
        if path.startswith('file:///'):
            path = path[8:]
        elif path.startswith('file://'):
            path = path[7:]

        path = path.replace('/', '\\')

        return path

    def should_quote_path(self, path):
        """Check if path should be quoted (not an image)"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        return not any(path.lower().endswith(ext) for ext in image_extensions)

    def keyPressEvent(self, event):
        """Handle paste of file paths"""
        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            clipboard = QApplication.clipboard()
            text = clipboard.text().strip()

            cleaned_path = self.clean_file_path(text)

            if os.path.exists(cleaned_path):
                if self.controller.get_ai_provider() == 'puter':
                    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                    if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                        self.attached_image = cleaned_path
                        self.add_system_message(
                            f"📎 **Image Ready:** {cleaned_path}\n\nType your message and press Enter."
                        )
                        event.accept()
                        return

                if self.should_quote_path(cleaned_path):
                    cleaned_path = f'"{cleaned_path}"'

                self.input_field.text_input.insertPlainText(cleaned_path)
                event.accept()
                return

        super().keyPressEvent(event)

    def check_admin_mode(self):
        """Check if running as admin and notify user"""
        import ctypes
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if is_admin:
                self.add_system_message(
                    "⚠️ **Administrator Mode Enabled**\n\n"
                    "This Agent is now running with elevated system privileges and can perform high-level system changes and tasks.\n\n"
                    "Some Windows security features (UIPI) may restrict drag-and-drop behavior in some instances.\n"
                    "If drag & drop does not work, please use the 📁 file browser button instead."
                )

        except:
            pass