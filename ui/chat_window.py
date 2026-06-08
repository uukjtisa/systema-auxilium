"""
ui/chat_window.py
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
from PyQt6.QtGui import QAction, QCursor, QRegion, QPixmap
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from core.skill_manager import SkillManager as _SkillManagerType
from ui.base_window import BaseWindow


_SK_SURFACE  = "#161B22"
_SK_SURFACE2 = "#21262D"
_SK_BORDER   = "#30363D"
_SK_ACCENT   = "#58A6FF"
_SK_TEXT     = "#E6EDF3"
_SK_MUTED    = "#8B949E"


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
            QWidget:hover {{ background-color: #1E2530; }}
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

        # ── Search box ────────────────────────────────────────────────────────
        self._skill_search = QLineEdit()
        self._skill_search.setPlaceholderText("Search skills…")
        self._skill_search.setFixedHeight(26)
        self._skill_search.setStyleSheet(f"""
            QLineEdit {{ background-color: {_SK_SURFACE2}; border: 1px solid {_SK_BORDER};
                        border-radius: 5px; color: {_SK_TEXT}; font-size: 10px; padding: 0 7px; }}
            QLineEdit:focus {{ border-color: {_SK_ACCENT}; color: {_SK_TEXT}; }}
        """)
        self._skill_search.textChanged.connect(self._on_skill_search_changed)
        bl.addWidget(self._skill_search)

        self._rows_widget = QWidget()
        self._rows_widget.setStyleSheet("background: transparent;")
        self._rows_layout = QVBoxLayout(self._rows_widget)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        bl.addWidget(self._rows_widget)

        # ── Pagination footer ─────────────────────────────────────────────────
        self._sk_footer = QWidget()
        self._sk_footer.setStyleSheet("background: transparent;")
        _sf_lay = QHBoxLayout(self._sk_footer)
        _sf_lay.setContentsMargins(0, 2, 0, 0)
        _sf_lay.setSpacing(6)
        _sk_btn_ss = f"""
            QPushButton {{ background: transparent; border: none;
                          color: {_SK_MUTED}; font-size: 9px; padding: 0; }}
            QPushButton:hover {{ color: #E6EDF3; }}
        """
        self._sk_show_more_btn = QPushButton("Show 10 more")
        self._sk_show_more_btn.setStyleSheet(_sk_btn_ss)
        self._sk_show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sk_show_more_btn.clicked.connect(self._skill_show_more)
        self._sk_show_all_btn = QPushButton("Show all")
        self._sk_show_all_btn.setStyleSheet(_sk_btn_ss)
        self._sk_show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sk_show_all_btn.clicked.connect(self._skill_show_all)
        _sep = QLabel("·")
        _sep.setStyleSheet("color: #333; background: transparent; font-size: 9px;")
        _sf_lay.addWidget(self._sk_show_more_btn)
        _sf_lay.addWidget(_sep)
        _sf_lay.addWidget(self._sk_show_all_btn)
        _sf_lay.addStretch()
        self._sk_footer.hide()
        bl.addWidget(self._sk_footer)
        self._sk_visible_count = 10

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

        all_skills = self._skill_manager.get_skills()
        loaded_count = sum(1 for s in all_skills if s.get('is_loaded'))
        self._count_lbl.setText(f"{len(all_skills)} · {loaded_count} loaded")

        # Filter by search query
        q = ""
        if hasattr(self, '_skill_search'):
            q = self._skill_search.text().strip().lower()
        if q:
            skills = [s for s in all_skills
                      if q in s['name'].lower() or q in s.get('description', '').lower()]
        else:
            skills = all_skills

        if not skills:
            msg = "No matching skills." if q else "No skills installed.\nCreate one below ↓"
            empty = QLabel(msg)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {_SK_MUTED}; font-size: 10px; padding: 12px;")
            self._rows_layout.addWidget(empty)
            if hasattr(self, '_sk_footer'):
                self._sk_footer.hide()
            return

        vis = getattr(self, '_sk_visible_count', 10)
        for skill in skills[:vis]:
            self._rows_layout.addWidget(_SkillRow(skill, self._skill_manager))

        remaining = len(skills) - vis
        if hasattr(self, '_sk_footer') and hasattr(self, '_sk_show_more_btn'):
            if remaining > 0:
                self._sk_show_more_btn.setText(f"Show {min(10, remaining)} more")
                self._sk_show_all_btn.setText(f"Show all ({len(skills)})")
                self._sk_footer.show()
            else:
                self._sk_footer.hide()

    def _on_skill_search_changed(self):
        """Reset pagination and refresh when the search query changes."""
        self._sk_visible_count = 10
        self.refresh()

    def _skill_show_more(self):
        self._sk_visible_count = getattr(self, '_sk_visible_count', 10) + 10
        self.refresh()

    def _skill_show_all(self):
        self._sk_visible_count = 99999
        self.refresh()

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
SIDEBAR_DEFAULT_W            = 290    # Default sidebar width (px) — wide enough for hero + pills
SIDEBAR_MIN_W                = 280    # Minimum — wide enough for 3 hero pills without clipping
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

                        # Check if image — prompt user via dialog
                        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                        if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                            QTimer.singleShot(0, lambda p=cleaned_path: chat_window._handle_image_file_drop(p))
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
                    # Check if image — prompt user via dialog
                    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
                    if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                        QTimer.singleShot(0, lambda p=cleaned_path: chat_window._handle_image_file_drop(p))
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
                color: #8B949E;
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

    # ── Obsidian Blue palette constants ───────────────────────────────────────
    _CB_BASE    = "#0D1117"
    _CB_HEADER  = "#161B22"
    _CB_BORDER  = "rgba(88, 166, 255, 0.18)"
    _CB_ACCENT  = "#58A6FF"
    _CB_ACCENT2 = "rgba(88, 166, 255, 0.12)"
    _CB_ACCENT3 = "rgba(88, 166, 255, 0.28)"
    _CB_TEXT    = "#E6EDF3"
    _CB_MUTED   = "#8B949E"
    _CB_SB      = "#21262D"

    def __init__(self, language, code, parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language

        self.is_expanded        = False
        self.is_resizing        = False
        self.resize_start_pos   = None
        self.resize_start_size  = None   # (width, height) of main_container at drag start
        self.min_height = 60
        self.max_height = 800
        self.min_width  = 300
        self.max_width  = 1200

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 6, 0, 0)
        root_layout.setSpacing(0)

        # ── Main container ────────────────────────────────────────────────────
        self.main_container = QFrame()
        self.main_container.setObjectName("codeBlock")
        self.main_container.setStyleSheet(f"""
            QFrame#codeBlock {{
                background: {self._CB_BASE};
                border: 1px solid {self._CB_BORDER};
                border-radius: 10px;
            }}
        """)
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Header (taller so buttons are never clipped) ──────────────────────
        header = QFrame()
        header.setObjectName("codeHeader")
        header.setFixedHeight(54)
        header.setStyleSheet(f"""
            QFrame#codeHeader {{
                background-color: {self._CB_HEADER};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom: 1px solid {self._CB_BORDER};
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 12, 0)
        header_layout.setSpacing(6)
        header_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Traffic-light dots
        for _color in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_color}; border-radius: 5px; border: none; }}")
            header_layout.addWidget(dot)

        header_layout.addSpacing(10)

        # Language label — always visible
        display_lang = language.strip().upper() if language and language.strip().lower() not in ('', 'text') else 'TEXT'
        lang_label = QLabel(display_lang)
        lang_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        lang_label.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                color: {self._CB_MUTED};
                font-size: 11px;
                font-weight: 700;
                border: none;
                padding: 0 4px;
            }}
        """)
        header_layout.addWidget(lang_label)
        header_layout.addStretch()

        _btn_style = f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                font-weight: 500;
                color: {self._CB_MUTED};
                min-width: 64px;
            }}
            QPushButton:hover {{
                background-color: {self._CB_ACCENT2};
                border-color: {self._CB_ACCENT3};
                color: {self._CB_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {self._CB_ACCENT3};
            }}
        """

        self.toggle_btn = QPushButton("▶  Show")
        self.toggle_btn.setObjectName("codeToggleBtn")
        self.toggle_btn.setStyleSheet(_btn_style)
        self.toggle_btn.clicked.connect(self.toggle_expand)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.toggle_btn)

        self.wrap_btn = QPushButton("↵  Wrap")
        self.wrap_btn.setObjectName("codeWrapBtn")
        self.wrap_btn.setStyleSheet(_btn_style)
        self.wrap_btn.setCheckable(True)
        self.wrap_btn.setChecked(False)
        self.wrap_btn.clicked.connect(self._toggle_wrap)
        self.wrap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.wrap_btn)

        self.copy_btn = QPushButton("📋  Copy")
        self.copy_btn.setObjectName("codeCopyBtn")
        self.copy_btn.setStyleSheet(_btn_style)
        self.copy_btn.clicked.connect(self.copy_code)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_layout.addWidget(self.copy_btn)

        container_layout.addWidget(header)

        # ── Scrollable code area ──────────────────────────────────────────────
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("codeScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea#codeScrollArea {{
                background: {self._CB_BASE};
                border: none;
            }}
            QScrollBar:horizontal {{
                background: transparent; height: 6px; border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {self._CB_SB}; border-radius: 3px; min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: #30363D; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {self._CB_SB}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: #30363D; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ border: none; background: none; height: 0; width: 0; }}
            QScrollBar::corner {{ background: transparent; }}
        """)

        code_container = QWidget()
        code_container.setStyleSheet(f"QWidget {{ background: {self._CB_BASE}; border: none; }}")
        code_container_layout = QVBoxLayout(code_container)
        code_container_layout.setContentsMargins(16, 12, 16, 12)
        code_container_layout.setSpacing(0)

        self.code_editor = QTextEdit()
        self.code_editor.setObjectName("codeEditor")
        self.code_editor.setPlainText(code)
        self.code_editor.setReadOnly(True)
        self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.code_editor.setFrameShape(QTextEdit.Shape.NoFrame)
        # Disable the QTextEdit's own scrollbars — QScrollArea handles all scrolling
        self.code_editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_editor.setStyleSheet(f"""
            QTextEdit#codeEditor {{
                background: transparent; border: none;
                color: {self._CB_TEXT};
                selection-background-color: rgba(88, 166, 255, 0.25);
            }}
        """)

        font = QFont('Consolas', 10)
        if not font.exactMatch():
            font = QFont('Monaco', 10)
        if not font.exactMatch():
            font = QFont('Courier New', 10)
        self.code_editor.setFont(font)

        self.code_editor.viewport().installEventFilter(self)

        self.highlighter = CodeSyntaxHighlighter(self.code_editor.document(), language)
        code_container_layout.addWidget(self.code_editor)
        self.scroll_area.setWidget(code_container)

        line_count = len(code.split('\n'))
        calculated_height = min(max(line_count * 17 + 24, self.min_height), self.max_height)
        self.scroll_area.setFixedHeight(calculated_height)
        self.scroll_area.hide()
        container_layout.addWidget(self.scroll_area)

        # ── Resize grip — inside the container, bottom-right ──────────────────
        # A transparent footer row that floats at the bottom of the black box.
        grip_row = QWidget()
        grip_row.setObjectName("codeGripRow")
        grip_row.setStyleSheet("QWidget#codeGripRow { background: transparent; }")
        grip_row.setFixedHeight(28)
        grip_row.hide()
        grip_row_layout = QHBoxLayout(grip_row)
        grip_row_layout.setContentsMargins(0, 0, 8, 4)
        grip_row_layout.setSpacing(0)
        grip_row_layout.addStretch()

        self.corner_grip = QLabel("⤡  Resize")
        self.corner_grip.setStyleSheet(f"""
            QLabel {{
                background: rgba(88,166,255,0.08);
                color: rgba(88,166,255,0.55);
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border: 1px solid rgba(88,166,255,0.20);
                border-radius: 4px;
            }}
            QLabel:hover {{
                background: rgba(88,166,255,0.18);
                color: {self._CB_ACCENT};
                border-color: rgba(88,166,255,0.45);
            }}
        """)
        self.corner_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.corner_grip.mousePressEvent   = self._corner_press
        self.corner_grip.mouseMoveEvent    = self._corner_move
        self.corner_grip.mouseReleaseEvent = self._corner_release
        grip_row_layout.addWidget(self.corner_grip)

        self._grip_row = grip_row
        container_layout.addWidget(grip_row)
        root_layout.addWidget(self.main_container)

    # ── Ctrl+Scroll ───────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self.code_editor.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                chat_win = self._find_chat_window()
                if chat_win:
                    if event.angleDelta().y() > 0:
                        chat_win.zoom_in()
                    else:
                        chat_win.zoom_out()
                return True
        return super().eventFilter(obj, event)

    def _find_chat_window(self):
        p = self.parent()
        while p:
            if p.__class__.__name__ == 'ChatWindow':
                return p
            p = p.parent()
        return None

    # ── Toggle ────────────────────────────────────────────────────────────────

    def toggle_expand(self):
        self.is_expanded = not self.is_expanded
        if self.is_expanded:
            self.scroll_area.show()
            self._grip_row.show()
            self.toggle_btn.setText("▼  Hide")
        else:
            self.scroll_area.hide()
            self._grip_row.hide()
            self.toggle_btn.setText("▶  Show")

    # ── Corner resize (width + height) ───────────────────────────────────────
    # We resize main_container width so the whole block (header + scroll + bar)
    # moves together, rather than just the inner scroll area.

    def _corner_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing     = True
            self.resize_start_pos  = event.globalPosition()
            self.resize_start_size = (self.main_container.width(),
                                      self.scroll_area.height())
            event.accept()

    def _corner_move(self, event):
        if self.is_resizing:
            dx    = event.globalPosition().x() - self.resize_start_pos.x()
            dy    = event.globalPosition().y() - self.resize_start_pos.y()
            new_w = max(self.min_width,  min(self.resize_start_size[0] + dx, self.max_width))
            new_h = max(self.min_height, min(self.resize_start_size[1] + dy, self.max_height))
            # Fix the outer container width so header + scroll + bar all follow
            self.main_container.setFixedWidth(int(new_w))
            self.scroll_area.setFixedHeight(int(new_h))
            event.accept()

    def _corner_release(self, event):
        self.is_resizing = False
        event.accept()

    # ── Copy ─────────────────────────────────────────────────────────────────

    def _toggle_wrap(self):
        """Toggle word-wrap on this code block's editor."""
        if self.wrap_btn.isChecked():
            self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.wrap_btn.setText("↵  Wrap ✓")
        else:
            self.code_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.wrap_btn.setText("↵  Wrap")

    def copy_code(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.code)
        self.copy_btn.setText("✓  Copied!")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: rgba(52, 168, 83, 0.12);
                border: 1px solid rgba(52, 168, 83, 0.3);
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                min-width: 64px;
                color: #3FB950;
            }}
        """)
        QTimer.singleShot(ANIM_COPY_FEEDBACK_MS, self.reset_copy_button)

    def reset_copy_button(self):
        self.copy_btn.setText("📋  Copy")
        self.copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 4px 11px;
                font-size: 11px;
                font-weight: 500;
                min-width: 64px;
                color: {self._CB_MUTED};
            }}
            QPushButton:hover {{
                background-color: {self._CB_ACCENT2};
                border-color: {self._CB_ACCENT3};
                color: {self._CB_ACCENT};
            }}
            QPushButton:pressed {{
                background-color: {self._CB_ACCENT3};
            }}
        """)


class ChatWindow(BaseWindow):
    """Modern chat window with AI conversation"""

    voice_playback_signal = pyqtSignal()  # Signal for thread-safe UI updates

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.thinking_timer = None
        self.thinking_dots = 0
        self.thinking_label_shown = False
        self._thinking_bubble_widget = None
        self._thinking_bubble_label = None
        self._thinking_bubble_timer = None
        self._thinking_bubble_dots = 0
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

        # Session switching lock — prevents spamming, blocks during AI generation / work mode
        self._session_switching_locked = False

        # Image attachment (multi-image + persistent pinned images)
        self.attached_image = None  # backward compat — last added image
        self.attached_images = []  # list of paths queued in input bar
        self.pinned_images = []  # list of {path, widget, row_wrapper, auto_detach}

        # Interrupt tracking
        self.last_sent_message = None  # Track last message for interrupt
        self.last_user_message_widget = None  # Track last user message widget for removal

        # MESSAGE CONTROL: Track all messages for edit/delete/rewind
        self.message_widgets = []  # List of {widget, role, content, history_index}
        self._skills_ui_card_widget = None   # Single per-session skills card (only one allowed)
        self._skills_ui_card_timer = None    # 500ms live-sync timer for that card

        # Window chrome state
        self._init_chrome_state()

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
                background-color: #161B22;
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
                    self.chat_zoom = float(config.get('chat_zoom', 1.0))
                    self._bot_avatar_image_path  = config.get('bot_avatar_image_path', '')
                    self._user_avatar_image_path = config.get('user_avatar_image_path', '')
                    self._bot_avatar_size  = int(config.get('bot_avatar_size', 32))
                    self._user_avatar_size = int(config.get('user_avatar_size', 32))
                    self._avatar_size_uniform = bool(config.get('avatar_size_uniform', False))
            else:
                self.bot_avatar = '🤖'
                self.user_avatar = '👤'
                self.chat_zoom = 1.0
                self._bot_avatar_image_path  = ''
                self._user_avatar_image_path = ''
                self._bot_avatar_size  = 32
                self._user_avatar_size = 32
                self._avatar_size_uniform = False
        except:
            self.bot_avatar = '🤖'
            self.user_avatar = '👤'
            self.chat_zoom = 1.0
            self._bot_avatar_image_path  = ''
            self._user_avatar_image_path = ''
            self._bot_avatar_size  = 32
            self._user_avatar_size = 32
            self._avatar_size_uniform = False
        self._bot_avatar_pixmap  = None
        self._user_avatar_pixmap = None
        # Clamp zoom to safe range
        self.chat_zoom = max(0.6, min(1.8, self.chat_zoom))
        self._glass_enabled = False
        self._glass_opacity = 0.75
        QTimer.singleShot(100, self.load_window_geometry)
        # Restore saved image avatars after UI is built
        QTimer.singleShot(200, self._restore_avatar_images)

    def _restore_avatar_images(self):
        """Load saved avatar image paths back into pixmaps after UI is ready."""
        from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
        from PyQt6.QtCore import QRectF

        def _load_circular(path, size):
            px = QPixmap(path)
            if px.isNull():
                return None
            scaled = px.scaled(size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            cx = (scaled.width()  - size) // 2
            cy = (scaled.height() - size) // 2
            sq = scaled.copy(cx, cy, size, size)
            out = QPixmap(size, size)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path2 = QPainterPath()
            path2.addEllipse(QRectF(0, 0, size, size))
            painter.setClipPath(path2)
            painter.drawPixmap(0, 0, sq)
            painter.end()
            return out

        if self._bot_avatar_image_path:
            pm = _load_circular(self._bot_avatar_image_path, 48)
            if pm:
                self._bot_avatar_pixmap = pm
                self.bot_avatar = ''
                if hasattr(self, 'bot_avatar_display'):
                    self.bot_avatar_display.setPixmap(
                        pm.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
                    self.bot_avatar_display.setText('')

        if self._user_avatar_image_path:
            pm = _load_circular(self._user_avatar_image_path, 48)
            if pm:
                self._user_avatar_pixmap = pm
                self.user_avatar = ''
                if hasattr(self, 'user_avatar_display'):
                    self.user_avatar_display.setPixmap(
                        pm.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))
                    self.user_avatar_display.setText('')

    def save_config(self):
        try:
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
            config['bot_avatar']  = self.bot_avatar
            config['user_avatar'] = self.user_avatar
            config['chat_zoom']   = self.chat_zoom
            config['bot_avatar_image_path']  = getattr(self, '_bot_avatar_image_path', '')
            config['user_avatar_image_path'] = getattr(self, '_user_avatar_image_path', '')
            config['bot_avatar_size']  = getattr(self, '_bot_avatar_size', 32)
            config['user_avatar_size'] = getattr(self, '_user_avatar_size', 32)
            config['avatar_size_uniform'] = getattr(self, '_avatar_size_uniform', False)
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
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(self._sidebar_w)
        _tc = self._t()
        self.sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background-color: {_tc['base']};
                border-right: 1px solid {_tc['border']};
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
            }}
        """)
        self.sidebar.setGeometry(-self._sidebar_w, 0, self._sidebar_w, 650)
        self.sidebar.hide()

        sidebar_main_layout = QHBoxLayout(self.sidebar)
        sidebar_main_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_main_layout.setSpacing(0)

        sidebar_scroll_vbox = QWidget()
        sidebar_scroll_vbox.setStyleSheet("background: transparent;")
        sidebar_scroll_vbox_layout = QVBoxLayout(sidebar_scroll_vbox)
        sidebar_scroll_vbox_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_scroll_vbox_layout.setSpacing(0)
        sidebar_main_layout.addWidget(sidebar_scroll_vbox, stretch=1)

        self._sidebar_drag_handle = QFrame(self.sidebar)
        self._sidebar_drag_handle.setFixedWidth(6)
        self._sidebar_drag_handle.setStyleSheet("""
            QFrame { background-color: rgba(168,199,250,0.12); border-radius: 3px; }
            QFrame:hover { background-color: rgba(168,199,250,0.35); }
        """)
        self._sidebar_drag_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self._sidebar_drag_handle.setToolTip("Drag to resize sidebar")
        self._sidebar_drag_handle.mousePressEvent   = self._sidebar_resize_press
        self._sidebar_drag_handle.mouseMoveEvent    = self._sidebar_resize_move
        self._sidebar_drag_handle.mouseReleaseEvent = self._sidebar_resize_release
        sidebar_main_layout.addWidget(self._sidebar_drag_handle)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
            QScrollBar::handle:vertical { background: rgba(168,199,250,0.25); border-radius: 3px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: rgba(168,199,250,0.45); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self.sidebar_scroll.viewport().installEventFilter(self)

        sidebar_content = QWidget()
        sidebar_content.setObjectName("sidebarContent")
        sidebar_content.setStyleSheet(f"QWidget#sidebarContent {{ background-color: {_tc['base']}; }}")
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # ─────────────────────────────────────────────────────────────────────
        # HERO — avatar cluster + name + 3 action pills
        # ─────────────────────────────────────────────────────────────────────
        hero = QFrame()
        hero.setObjectName("sidebarHero")
        hero.setStyleSheet(f"""
            QFrame#sidebarHero {{
                background-color: {_tc['base']};
                border-bottom: 1px solid {_tc['border']};
            }}
        """)
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(16, 18, 16, 14)
        hero_lay.setSpacing(12)

        # Avatar cluster row
        av_name_row = QHBoxLayout()
        av_name_row.setSpacing(12)

        # Stacked avatars widget (bot big, user badge)
        av_stack = QWidget()
        av_stack.setFixedSize(56, 56)
        av_stack.setStyleSheet("background: transparent;")

        self.bot_avatar_display = QLabel(self.bot_avatar)
        self.bot_avatar_display.setParent(av_stack)
        self.bot_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bot_avatar_display.setGeometry(0, 0, 48, 48)
        self.bot_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 24px;
                background-color: #1a2a3a;
                border-radius: 24px;
                border: 2px solid transparent;
            }
        """)

        self.user_avatar_display = QLabel(self.user_avatar)
        self.user_avatar_display.setParent(av_stack)
        self.user_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_avatar_display.setGeometry(28, 30, 26, 26)
        self.user_avatar_display.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                background-color: #1a2a1a;
                border-radius: 13px;
                border: 2px solid {_tc['base']};
            }}
        """)
        av_stack.show()

        av_name_row.addWidget(av_stack)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        _assistant_display = self.controller.get_assistant_name() or "Systema Auxilium"
        self._hero_bot_name = QLabel(_assistant_display)
        self._hero_bot_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #E6EDF3; background: transparent;")
        name_col.addWidget(self._hero_bot_name)

        _user_display = self.controller.get_user_name() or "You"
        self._hero_user_name = QLabel(_user_display)
        self._hero_user_name.setStyleSheet("font-size: 10px; color: #555; background: transparent;")
        name_col.addWidget(self._hero_user_name)

        av_name_row.addLayout(name_col)
        av_name_row.addStretch()
        hero_lay.addLayout(av_name_row)

        # 3 action pills — no borders, subtle text-only pill style
        pills_row = QHBoxLayout()
        pills_row.setSpacing(6)

        def _action_pill(icon, label, slot):
            btn = QPushButton(f"{icon}  {label}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_tc['elevated']};
                    border: none;
                    border-radius: 20px;
                    padding: 5px 10px;
                    font-size: 10px;
                    color: #8B949E;
                }}
                QPushButton:hover {{
                    background-color: rgba(88,166,255,0.12);
                    color: #58A6FF;
                }}
                QPushButton:pressed {{
                    background-color: rgba(88,166,255,0.18);
                }}
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            return btn

        pills_row.addWidget(_action_pill("🖼", "Avatars", self._open_avatars_dialog))
        pills_row.addWidget(_action_pill("🏷", "Names", self._open_names_dialog))
        pills_row.addWidget(_action_pill("⚙️", "Instructions", self.open_instructions_window))
        hero_lay.addLayout(pills_row)

        sidebar_layout.addWidget(hero)

        # ─────────────────────────────────────────────────────────────────────
        # PERSONALIZE rows — no section header, just the rows
        # ─────────────────────────────────────────────────────────────────────
        def _sec_header(icon, text):
            lbl = QLabel(f"{icon}  {text}")
            lbl.setStyleSheet(f"""
                QLabel {{
                    font-size: 11px; font-weight: 700;
                    color: #9AA0A6;
                    background: transparent;
                    padding: 14px 16px 4px;
                }}
            """)
            return lbl

        def _side_row(icon, label, slot=None, badge_text=None, arrow=True):
            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{ background: transparent; }}
                QWidget:hover {{ background: {_tc['surface']}; }}
            """)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 8, 16, 8)
            rl.setSpacing(10)

            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(18)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("font-size: 14px; background: transparent; color: #8B949E;")
            rl.addWidget(icon_lbl)

            text_lbl = QLabel(label)
            text_lbl.setStyleSheet("font-size: 11px; color: #C9D1D9; background: transparent;")
            rl.addWidget(text_lbl, stretch=1)

            if badge_text:
                badge = QLabel(badge_text)
                badge.setStyleSheet("background: #21262D; color: #58A6FF; font-size: 9px; border-radius: 4px; padding: 1px 6px;")
                rl.addWidget(badge)

            if arrow:
                arr = QLabel("›")
                arr.setStyleSheet("color: #30363D; font-size: 14px; background: transparent;")
                rl.addWidget(arr)

            if slot:
                row.mousePressEvent = lambda e: slot()
            return row

        sidebar_layout.addWidget(_side_row("🧠", "Manage Memories", self._open_memory_window))
        sidebar_layout.addWidget(_side_row("⚙", "Manage Tasks", self._open_manage_tasks_window))

        # ── Skills — inline collapsible, matching _side_row style ────────────
        skill_manager = getattr(self.controller, 'skill_manager', None)
        if skill_manager:
            # Build a custom skills row that matches _side_row exactly
            skills_wrapper = QWidget()
            skills_wrapper.setStyleSheet("background: transparent;")
            sw_lay = QVBoxLayout(skills_wrapper)
            sw_lay.setContentsMargins(0, 0, 0, 0)
            sw_lay.setSpacing(0)

            # Header row — same layout as _side_row
            skills_hdr = QWidget()
            skills_hdr.setStyleSheet(f"""
                QWidget {{ background: transparent; }}
                QWidget:hover {{ background: {_tc['surface']}; }}
            """)
            skills_hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            sh_lay = QHBoxLayout(skills_hdr)
            sh_lay.setContentsMargins(16, 8, 16, 8)
            sh_lay.setSpacing(10)

            sk_icon = QLabel("⚡")
            sk_icon.setFixedWidth(18)
            sk_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sk_icon.setStyleSheet("font-size: 14px; background: transparent; color: #8B949E;")
            sh_lay.addWidget(sk_icon)

            sk_text = QLabel("Skills")
            sk_text.setStyleSheet("font-size: 11px; color: #C9D1D9; background: transparent;")
            sh_lay.addWidget(sk_text, stretch=1)

            sk_count = QLabel("")
            sk_count.setStyleSheet("background: #21262D; color: #58A6FF; font-size: 9px; border-radius: 4px; padding: 1px 6px;")
            sh_lay.addWidget(sk_count)

            sk_chevron = QLabel("›")
            sk_chevron.setStyleSheet("color: #30363D; font-size: 14px; background: transparent;")
            sh_lay.addWidget(sk_chevron)

            sw_lay.addWidget(skills_hdr)

            # Body — hidden by default, contains the original SkillsSidebarSection internals
            skills_body = QWidget()
            skills_body.setStyleSheet(f"background: {_tc['base']};")
            skills_body.hide()
            sb_lay = QVBoxLayout(skills_body)
            sb_lay.setContentsMargins(8, 4, 8, 8)
            sb_lay.setSpacing(4)
            self._skills_section = SkillsSidebarSection(skill_manager)
            # Hide the SkillsSidebarSection's own header — we have our own
            self._skills_section.layout().itemAt(0).widget().hide()
            # Force-show the internal body (our outer skills_body handles hide/show)
            self._skills_section._body.show()
            self._skills_section._expanded = True
            sb_lay.addWidget(self._skills_section)
            sw_lay.addWidget(skills_body)

            # Refresh count label
            def _refresh_skill_count():
                try:
                    skills = skill_manager.get_skills()
                    loaded = sum(1 for s in skills if s.get('is_loaded'))
                    sk_count.setText(f"{len(skills)} · {loaded} loaded" if skills else "none")
                except RuntimeError:
                    # sk_count label was deleted (sidebar rebuilt) — disconnect signals
                    try:
                        skill_manager.skills_changed.disconnect(_refresh_skill_count)
                        skill_manager.loaded_skills_changed.disconnect(_refresh_skill_count)
                    except Exception:
                        pass
            _refresh_skill_count()
            skill_manager.skills_changed.connect(_refresh_skill_count)
            skill_manager.loaded_skills_changed.connect(_refresh_skill_count)

            # Toggle expand
            _sk_open = [False]
            def _toggle_skills():
                _sk_open[0] = not _sk_open[0]
                skills_body.setVisible(_sk_open[0])
                sk_chevron.setText("▼" if _sk_open[0] else "›")
                if _sk_open[0]:
                    self._skills_section.refresh()
            skills_hdr.mousePressEvent = lambda e: _toggle_skills()

            sidebar_layout.addWidget(skills_wrapper)
        else:
            sidebar_layout.addWidget(_side_row("⚡", "Skills", None, badge_text="unavailable", arrow=False))

        # ─────────────────────────────────────────────────────────────────────
        # SESSION HISTORY section
        # ─────────────────────────────────────────────────────────────────────
        sidebar_layout.addWidget(_sec_header("📁", "Session History"))

        # Search + sort row — compact, matches bg
        search_sort_row = QHBoxLayout()
        search_sort_row.setContentsMargins(16, 2, 16, 6)
        search_sort_row.setSpacing(6)

        self._session_search = QLineEdit()
        self._session_search.setPlaceholderText("Search sessions…")
        self._session_search.setFixedHeight(28)
        self._session_search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 10px;
                color: #8B949E;
            }}
            QLineEdit:focus {{
                border-color: rgba(88,166,255,0.45);
                color: #E6EDF3;
            }}
        """)
        self._session_search.textChanged.connect(self.refresh_session_list)
        search_sort_row.addWidget(self._session_search, stretch=1)

        # Cycling sort button
        self._session_sort_modes = ["Time", "A→Z", "Z→A"]
        self._session_sort_idx   = 0
        self._session_sort_btn = QPushButton("↕ Time")
        self._session_sort_btn.setFixedHeight(28)
        self._session_sort_btn.setFixedWidth(56)
        self._session_sort_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                font-size: 9px;
                color: #8B949E;
                padding: 0 4px;
            }}
            QPushButton:hover {{ border-color: rgba(88,166,255,0.35); color: #E6EDF3; }}
        """)
        def _cycle_sort():
            self._session_sort_idx = (self._session_sort_idx + 1) % len(self._session_sort_modes)
            icons = ["↕", "↑", "↓"]
            lbl = self._session_sort_modes[self._session_sort_idx]
            self._session_sort_btn.setText(f"{icons[self._session_sort_idx]} {lbl}")
            self.refresh_session_list()
        self._session_sort_btn.clicked.connect(_cycle_sort)
        search_sort_row.addWidget(self._session_sort_btn)

        sidebar_layout.addLayout(search_sort_row)

        # New session button — accent, full width
        self._new_session_btn = QPushButton("➕  New Session")
        new_session_btn = self._new_session_btn
        new_session_btn.setFixedHeight(32)
        new_session_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(88,166,255,0.10);
                border: 1px solid rgba(88,166,255,0.22);
                border-radius: 7px;
                font-size: 11px;
                font-weight: 500;
                color: #58A6FF;
                margin: 0 16px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(88,166,255,0.18);
                border-color: rgba(88,166,255,0.4);
            }
        """)
        new_session_btn.clicked.connect(lambda: self.controller.create_new_session())
        new_session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sidebar_layout.addWidget(new_session_btn)

        # Session count label (updated by refresh)
        self._session_count_lbl = QLabel("")
        self._session_count_lbl.setStyleSheet("color: #30363D; font-size: 9px; background: transparent; padding: 2px 16px 0;")
        sidebar_layout.addWidget(self._session_count_lbl)

        # Session list container
        self._session_list_body = QWidget()
        self._session_list_body.setStyleSheet("background: transparent;")
        slb_layout = QVBoxLayout(self._session_list_body)
        slb_layout.setContentsMargins(0, 0, 0, 0)
        slb_layout.setSpacing(0)

        session_list_container = QWidget()
        session_list_container.setStyleSheet("background: transparent;")
        self.session_list_layout = QVBoxLayout(session_list_container)
        self.session_list_layout.setContentsMargins(0, 0, 0, 0)
        self.session_list_layout.setSpacing(0)
        slb_layout.addWidget(session_list_container)
        sidebar_layout.addWidget(self._session_list_body)

        # Show more / Show all / Collapse buttons (hidden until needed)
        session_footer = QWidget()
        session_footer.setStyleSheet("background: transparent;")
        sf_lay = QHBoxLayout(session_footer)
        sf_lay.setContentsMargins(16, 2, 16, 6)
        sf_lay.setSpacing(8)

        _footer_btn_ss = """
            QPushButton { background: transparent; border: none;
                color: #555; font-size: 10px; padding: 0; }
            QPushButton:hover { color: #8B949E; }
        """
        self._show_more_btn = QPushButton("Show more")
        self._show_more_btn.setStyleSheet(_footer_btn_ss)
        self._show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_more_btn.clicked.connect(self._session_show_more)

        self._show_all_btn = QPushButton("Show all")
        self._show_all_btn.setStyleSheet(_footer_btn_ss)
        self._show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_all_btn.clicked.connect(self._session_show_all)

        self._collapse_list_btn = QPushButton("Collapse")
        self._collapse_list_btn.setStyleSheet(_footer_btn_ss)
        self._collapse_list_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_list_btn.clicked.connect(self._session_collapse)

        sep_dot = QLabel("·")
        sep_dot.setStyleSheet("color: #333; background: transparent; font-size: 10px;")
        sf_lay.addWidget(self._show_more_btn)
        sf_lay.addWidget(sep_dot)
        sf_lay.addWidget(self._show_all_btn)
        sf_lay.addStretch()
        sf_lay.addWidget(self._collapse_list_btn)

        session_footer.hide()
        self._session_footer = session_footer
        sidebar_layout.addWidget(session_footer)

        # State for pagination
        self._session_visible_count = 10
        self._session_list_expanded = True
        self._session_list_auto_collapsed = False

        QTimer.singleShot(100, self.refresh_session_list)

        sidebar_layout.addStretch()
        self.sidebar_scroll.setWidget(sidebar_content)
        sidebar_scroll_vbox_layout.addWidget(self.sidebar_scroll)

        # NOTE: sidebar is NOT added to main_layout — it is an overlay.

        # === MAIN CHAT AREA ===
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Header bar
        header_bar = QFrame()
        self.header_bar = header_bar          # stored for glass background toggle
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet("""
            QFrame {
                background-color: #161B22;
                border-bottom: 1px solid #21262D;
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
                background: #21262D;
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
                background: transparent;
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
                background: #21262D;
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
                        background-color: #161B22;
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
                background-color: #161B22;
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
        # Status label (thinking indicator)
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setStyleSheet("""
            QLabel#statusLabel {
                color: #9AA0A6;
                font-style: italic;
                font-size: 11px;
                padding: 5px 14px;
                background-color: #0D1117;
                border-top: 1px solid #21262D;
            }
        """)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chat_layout.addWidget(self.status_label)

        # ── Work Mode Banner ─────────────────────────────────────────────────
        self._work_banner = QLabel("")
        self._work_banner.setObjectName("workBanner")
        self._work_banner.setWordWrap(True)
        self._work_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._work_banner.setStyleSheet("""
                    QLabel#workBanner {
                        background-color: #1A1F2E;
                        border-top: 1px solid #2D3A5C;
                        border-bottom: 1px solid #2D3A5C;
                        color: #7EB8F7;
                        font-size: 11px;
                        font-style: italic;
                        padding: 6px 14px;
                    }
                """)
        self._work_banner.hide()
        chat_layout.addWidget(self._work_banner)
        # ─────────────────────────────────────────────────────────────────────

        # Input area
        input_container = QFrame()
        input_container.setObjectName("inputContainer")
        input_container.setStyleSheet("""
            QFrame#inputContainer {
                background-color: transparent;
                border-top: none;
            }
        """)

        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(14, 8, 14, 12)
        input_layout.setSpacing(0)

        # ── Pill-shaped input card ────────────────────────────────────────────
        combined_container = QFrame()
        combined_container.setObjectName("inputCard")
        combined_container.setStyleSheet("""
            QFrame#inputCard {
                background-color: #1C2128;
                border: 1px solid #2D333B;
                border-radius: 18px;
            }
        """)

        combined_layout = QVBoxLayout(combined_container)
        combined_layout.setContentsMargins(0, 0, 0, 0)
        combined_layout.setSpacing(0)
        combined_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        # ── Text input area ───────────────────────────────────────────────────
        text_row = QWidget()
        text_row.setObjectName("inputTextRow")
        text_row.setStyleSheet("QWidget#inputTextRow { background: transparent; }")
        text_row_layout = QHBoxLayout(text_row)
        text_row_layout.setContentsMargins(16, 10, 16, 4)
        text_row_layout.setSpacing(0)
        text_row_layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)

        self.input_field = ResizableInput()
        self.input_field.text_input.setStyleSheet("""
            QTextEdit {
                background-color: transparent;
                border: none;
                color: #E8EAED;
                font-size: 13px;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                padding: 2px 0;
                line-height: 1.6;
            }
            QTextEdit:focus { background-color: transparent; }
        """)
        self.input_field.resize_handle.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #30363D;
                font-size: 6px;
                letter-spacing: 2px;
            }
            QLabel:hover {
                color: #8B949E;
                background-color: rgba(255,255,255,0.05);
                border-radius: 2px;
            }
        """)
        self.input_field.enterPressed.connect(self.send_message)
        self.input_field.text_input.textChanged.connect(self._update_token_count)
        text_row_layout.addWidget(self.input_field, 1)
        combined_layout.addWidget(text_row)

        # Install inertia scroll on the input field's viewport
        self.input_field.text_input.viewport().installEventFilter(self)

        # ── Bottom action row: [attach][mode]  ·····  [voice][interrupt][send] ──
        bottom_row = QWidget()
        bottom_row.setObjectName("inputBottomRow")
        bottom_row.setStyleSheet("QWidget#inputBottomRow { background: transparent; }")
        bottom_row_layout = QHBoxLayout(bottom_row)
        bottom_row_layout.setContentsMargins(10, 0, 10, 8)
        bottom_row_layout.setSpacing(4)

        # ── LEFT: attach + mode ───────────────────────────────────────────────
        browse_btn = QPushButton("📎")
        browse_btn.setFixedSize(30, 30)
        browse_btn.setToolTip("Attach file")
        browse_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                font-size: 13px;
                color: #6E7280;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.1);
                border-color: rgba(255,255,255,0.2);
                color: #9AA0A6;
            }
        """)
        browse_btn.clicked.connect(self.browse_for_file)
        bottom_row_layout.addWidget(browse_btn)

        self.mode_dropdown = QPushButton("💬")
        self.mode_dropdown.setFixedSize(30, 30)
        self.mode_dropdown.setToolTip("Set execution mode")
        self.mode_dropdown.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                font-size: 13px;
                color: #6E7280;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.1);
                border-color: rgba(255,255,255,0.2);
                color: #9AA0A6;
            }
        """)
        self.mode_dropdown.clicked.connect(self.show_mode_menu)
        bottom_row_layout.addWidget(self.mode_dropdown)

        # ── Token estimate label ──────────────────────────────────────────────
        self._token_count_lbl = QLabel("~0 token per message")
        self._token_count_lbl.setStyleSheet(
            "QLabel { color: #3D4450; font-size: 9px; background: transparent; padding: 0 4px; }")
        self._token_count_lbl.setToolTip(
            "Estimated tokens for next message (your input + full conversation history)")
        bottom_row_layout.addWidget(self._token_count_lbl)
        _show_tk = getattr(self.controller, 'settings', {}).get('show_token_count', True)
        self._token_count_lbl.setVisible(_show_tk)
        self._token_refresh_timer = QTimer(self)
        self._token_refresh_timer.setInterval(2000)
        self._token_refresh_timer.timeout.connect(self._update_token_count)
        self._token_refresh_timer.start()

        bottom_row_layout.addStretch()

        # ── RIGHT: voice + interrupt + send ──────────────────────────────────
        self.voice_btn_inline = QPushButton("🎙️")
        self.voice_btn_inline.setFixedSize(30, 30)
        self.voice_btn_inline.setCheckable(True)
        self.voice_btn_inline.setToolTip("Toggle voice mode")
        self.voice_btn_inline.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                font-size: 13px;
                color: #6E7280;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.1);
                border-color: rgba(255,255,255,0.2);
                color: #9AA0A6;
            }
            QPushButton:checked {
                background: rgba(52,168,83,0.22);
                border-color: rgba(52,168,83,0.5);
                color: #4CAF50;
            }
        """)
        self.voice_btn_inline.clicked.connect(self.toggle_voice)
        bottom_row_layout.addWidget(self.voice_btn_inline)

        self.voice_interrupt_btn = QPushButton("🔇")
        self.voice_interrupt_btn.setFixedSize(30, 30)
        self.voice_interrupt_btn.setStyleSheet("""
            QPushButton {
                background: rgba(234,67,53,0.18);
                border: 1px solid rgba(234,67,53,0.45);
                border-radius: 8px;
                font-size: 13px;
                color: #F07070;
            }
            QPushButton:hover { background: rgba(234,67,53,0.3); }
        """)
        self.voice_interrupt_btn.clicked.connect(self.interrupt_voice)
        self.voice_interrupt_btn.hide()
        bottom_row_layout.addWidget(self.voice_interrupt_btn)

        self.interrupt_btn = QPushButton("■")
        self.interrupt_btn.setFixedSize(30, 30)
        self.interrupt_btn.setToolTip("Cancel AI response")
        self.interrupt_btn.setStyleSheet("""
            QPushButton {
                background: rgba(234,67,53,0.18);
                border: 1px solid rgba(234,67,53,0.45);
                border-radius: 8px;
                font-size: 14px;
                color: #F07070;
                font-weight: bold;
            }
            QPushButton:hover { background: rgba(234,67,53,0.3); }
        """)
        self.interrupt_btn.clicked.connect(self.interrupt_response)
        self.interrupt_btn.hide()
        bottom_row_layout.addWidget(self.interrupt_btn)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(30, 30)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.08);
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 8px;
                font-size: 14px;
                color: #E6EDF3;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.15);
                border-color: rgba(255,255,255,0.3);
            }
            QPushButton:pressed {
                background: rgba(255,255,255,0.22);
            }
            QPushButton:disabled {
                background: transparent;
                border-color: rgba(255,255,255,0.05);
                color: #5F5F5F;
            }
        """)
        self.send_btn.clicked.connect(self.send_message)
        bottom_row_layout.addWidget(self.send_btn)

        # ── Image preview bar — multi-image scrollable strip ─────────────────
        self._img_preview_bar = QFrame()
        self._img_preview_bar.setObjectName("imgPreviewBar")
        self._img_preview_bar.setFixedHeight(44)
        self._img_preview_bar.setStyleSheet(
            "QFrame#imgPreviewBar { background: transparent; border-top: 1px solid #2D333B; }")
        _img_bar_outer = QHBoxLayout(self._img_preview_bar)
        _img_bar_outer.setContentsMargins(10, 6, 10, 6)
        _img_bar_outer.setSpacing(6)

        from PyQt6.QtWidgets import QScrollArea as _SA
        _thumb_scroll = _SA()
        _thumb_scroll.setFixedHeight(38)
        _thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        _thumb_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        _thumb_scroll.setWidgetResizable(True)

        self._img_thumbs_widget = QWidget()
        self._img_thumbs_widget.setStyleSheet("background: transparent;")
        self._img_thumbs_layout = QHBoxLayout(self._img_thumbs_widget)
        self._img_thumbs_layout.setContentsMargins(0, 0, 0, 0)
        self._img_thumbs_layout.setSpacing(6)
        self._img_thumbs_layout.addStretch()
        _thumb_scroll.setWidget(self._img_thumbs_widget)
        _img_bar_outer.addWidget(_thumb_scroll, stretch=1)

        _img_clear_all_btn = QPushButton("✕ Clear all")
        _img_clear_all_btn.setFixedHeight(24)
        _img_clear_all_btn.setToolTip("Remove all image attachments")
        _img_clear_all_btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(255,255,255,0.06);
                        border: 1px solid rgba(255,255,255,0.12);
                        border-radius: 6px;
                        color: #8B949E; font-size: 10px; padding: 0 8px;
                    }
                    QPushButton:hover {
                        background: rgba(234,67,53,0.25);
                        color: #EA4335; border-color: rgba(234,67,53,0.5);
                    }
                """)
        _img_clear_all_btn.clicked.connect(self._clear_image_preview)
        _img_bar_outer.addWidget(_img_clear_all_btn)

        self._img_preview_bar.hide()
        combined_layout.addWidget(self._img_preview_bar)
        # ─────────────────────────────────────────────────────────────────────

        combined_layout.addWidget(bottom_row)
        input_layout.addWidget(combined_container)

        # ── Pinned images area: floating overlay, horizontal scrolling strip ──
        self._pinned_area = QWidget(self.container)
        self._pinned_area.setObjectName("pinnedArea")
        self._pinned_area.setStyleSheet("QWidget#pinnedArea { background: transparent; }")
        self._pinned_area.setFixedHeight(80)

        _pa_outer_lay = QVBoxLayout(self._pinned_area)
        _pa_outer_lay.setContentsMargins(0, 0, 0, 0)
        _pa_outer_lay.setSpacing(0)

        from PyQt6.QtWidgets import QScrollArea as _PinnedSA
        self._pinned_scroll = _PinnedSA()
        self._pinned_scroll.setWidgetResizable(True)
        self._pinned_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._pinned_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pinned_scroll.setStyleSheet("""
                    QScrollArea { background: transparent; border: none; }
                    QScrollBar:horizontal {
                        background: rgba(255,255,255,0.04); height: 6px;
                        border: none; border-radius: 3px; margin: 0;
                    }
                    QScrollBar::handle:horizontal {
                        background: rgba(168,199,250,0.3); border-radius: 3px; min-width: 20px;
                    }
                    QScrollBar::handle:horizontal:hover { background: rgba(168,199,250,0.55); }
                    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
                """)

        _pinned_inner = QWidget()
        _pinned_inner.setStyleSheet("background: transparent;")
        self._pinned_area_layout = QHBoxLayout(_pinned_inner)
        self._pinned_area_layout.setContentsMargins(14, 6, 14, 6)
        self._pinned_area_layout.setSpacing(8)
        self._pinned_area_layout.addStretch()  # trailing stretch keeps cards left-aligned

        self._pinned_scroll.setWidget(_pinned_inner)
        _pa_outer_lay.addWidget(self._pinned_scroll)
        self._pinned_area.hide()
        # ─────────────────────────────────────────────────────────────────────

        self.input_container = input_container  # stored for glass background toggle
        self.input_container.installEventFilter(self)
        chat_layout.addWidget(input_container)

        main_layout.addWidget(chat_container)

        # Re-raise the toggle button so it sits above chat_container in Z-order.
        self.toggle_sidebar_btn.raise_()

        # Load personalization
        self.load_personalization()

        # Apply glass background from saved settings (deferred so widgets are ready)
        QTimer.singleShot(200, self._apply_glass_from_settings)

        # Notify if admin privileges are available — deferred past theme init
        QTimer.singleShot(210, self.check_admin_mode)

        # Welcome message — deferred past theme init so _current_theme_key is set
        QTimer.singleShot(220, lambda: self.add_system_message(
            "👋 **Welcome to Systema Auxilium!**\n\n"
            "I can execute Python code and control your system. "
            "Click the 💬 icon to enforce tool usage."
        ))

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
        if hasattr(self, '_session_list_overlay') and hasattr(self, '_session_list_body'):
            self._session_list_overlay.setGeometry(self._session_list_body.rect())

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
        """Open custom instructions window with personality presets and persona block tools."""
        import json as _json
        from pathlib import Path as _Path
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                     QTextEdit, QLabel, QScrollArea, QWidget, QFrame,
                                     QInputDialog, QMessageBox, QSplitter)

        PRESETS_FILE = _Path(_APP_ROOT) / "data" / "instruction_presets.json"
        PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)

        _tc = self._t()

        # ── Shared styles ─────────────────────────────────────────────────────
        _DLG_SS = f"""
            QDialog {{ background-color: {_tc['base']}; color: #E6EDF3;
                       font-family: 'Segoe UI', system-ui, sans-serif; }}
            QLabel {{ background: transparent; color: #E6EDF3; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: {_tc['elevated']}; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """
        def _chip(text, accent=False):
            btn = QPushButton(text)
            if accent:
                btn.setStyleSheet(f"""
                    QPushButton {{ background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                        border-radius: 5px; padding: 4px 10px; font-size: 10px; color: #58A6FF; }}
                    QPushButton:hover {{ background: rgba(88,166,255,0.24); border-color: #58A6FF; }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{ background: {_tc['elevated']}; border: 1px solid {_tc['border']};
                        border-radius: 5px; padding: 4px 10px; font-size: 10px; color: #8B949E; }}
                    QPushButton:hover {{ background: rgba(88,166,255,0.08); border-color: rgba(88,166,255,0.35); color: #E6EDF3; }}
                """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            return btn

        dialog = QDialog(self)
        dialog.setWindowTitle("Custom Instructions")
        dialog.setModal(True)
        dialog.setMinimumSize(680, 560)
        dialog.resize(780, 640)
        dialog.setStyleSheet(_DLG_SS)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setFixedHeight(50)
        hdr.setStyleSheet(f"QFrame {{ background: {_tc['surface']}; border-bottom: 1px solid {_tc['border']}; }}")
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(20, 0, 20, 0)
        t_lbl = QLabel("Custom Instructions")
        t_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #E6EDF3;")
        hdr_l.addWidget(t_lbl)
        hdr_l.addStretch()
        root.addWidget(hdr)

        # Main split: left = tools panel, right = editor
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter { background: transparent; } QSplitter::handle { background: transparent; }")

        # ── LEFT PANEL: presets + inserts ────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(230)
        left_scroll.setStyleSheet(f"QScrollArea {{ background: {_tc['base']}; border: none; border-right: 1px solid {_tc['border']}; }}")
        left_w = QWidget()
        left_w.setStyleSheet(f"QWidget {{ background: {_tc['base']}; }}")
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(12, 14, 12, 14)
        left_lay.setSpacing(14)

        def _section_hdr(txt):
            l = QLabel(txt)
            l.setStyleSheet("color: #8B949E; font-size: 9px; font-weight: 700; letter-spacing: 1px; background: transparent;")
            return l

        # Personality presets
        left_lay.addWidget(_section_hdr("PERSONALITY PRESETS"))
        PERSONALITY_PRESETS = [
            ("🥰 Cute & bubbly",
             "Be cute, enthusiastic, and bubbly! Use emojis frequently 🌟✨ Use lots of exclamation marks! "
             "Be warm, encouraging, and playful. Express genuine excitement about helping!"),
            ("💅 Girly & sassy",
             "Be confident, witty, and a little sassy. Use casual, fun language. Don't be afraid to add "
             "personality and light humor. Think of yourself as a smart, stylish best friend."),
            ("💼 Male professional",
             "Be precise, professional, and direct. Favor concise answers over lengthy explanations. "
             "Use formal language. Prioritize efficiency and clarity in every response."),
            ("🧑‍🎓 Patient teacher",
             "Explain everything step-by-step as if teaching a beginner. Never assume prior knowledge. "
             "Use analogies and simple language. Encourage questions and be endlessly patient."),
            ("🔬 Technical expert",
             "Be highly technical and detailed. Assume strong technical background. Use precise terminology. "
             "Don't over-simplify. Provide depth, nuance, and cite edge-cases when relevant."),
            ("😂 Witty & humorous",
             "Be funny and keep things light. Add clever humor and witty observations naturally. "
             "Don't force jokes, but don't miss a good opportunity either. Still be helpful — just fun about it."),
            ("🧘 Calm & thoughtful",
             "Be calm, measured, and thoughtful in all responses. Never rush. Take a reflective tone. "
             "Acknowledge complexity, be empathetic, and never be dismissive."),
            ("⚡ Speed mode",
             "Ultra-concise. No fluff, no filler, no pleasantries. Answer in the fewest possible words. "
             "Bullet points only when needed. Prioritize speed and density of information."),
        ]
        for label, text in PERSONALITY_PRESETS:
            btn = _chip(label)
            btn.setToolTip("Click to insert into editor")
            btn.clicked.connect(lambda _, t=text: _insert(t))
            left_lay.addWidget(btn)

        # Persona block insert
        left_lay.addSpacing(6)
        left_lay.addWidget(_section_hdr("PERSONA TEMPLATE"))
        persona_btn = _chip("📝 Insert Persona Block", accent=True)
        PERSONA_BLOCK = (
            "## Assistant Persona\n"
            "Personality: [Describe personality traits]\n"
            "Speaking style: [How does the assistant talk?]\n"
            "Tone: [Formal / Casual / Playful / Professional]\n"
            "Special behaviors: [Any quirks, habits, rules]\n"
            "Background: [Optional backstory or context]\n"
            "\n## Boundaries\n"
            "- Always [rule 1]\n"
            "- Never [rule 2]\n"
        )
        persona_btn.clicked.connect(lambda: _insert(PERSONA_BLOCK))
        left_lay.addWidget(persona_btn)

        # Custom saved presets
        left_lay.addSpacing(6)
        left_lay.addWidget(_section_hdr("SAVED PRESETS"))

        saved_presets_container = QWidget()
        saved_presets_container.setStyleSheet("background: transparent;")
        self._saved_presets_layout = QVBoxLayout(saved_presets_container)
        self._saved_presets_layout.setContentsMargins(0, 0, 0, 0)
        self._saved_presets_layout.setSpacing(4)

        def _load_saved_presets():
            # Clear
            while self._saved_presets_layout.count():
                item = self._saved_presets_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            try:
                if PRESETS_FILE.exists():
                    presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8'))
                else:
                    presets = {}
            except Exception:
                presets = {}

            for name, content in presets.items():
                row = QWidget()
                row.setStyleSheet("background: transparent;")
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(4)
                load_btn = _chip(f"📂 {name}")
                load_btn.clicked.connect(lambda _, c=content: _insert(c))
                row_l.addWidget(load_btn, stretch=1)
                del_btn = QPushButton("✕")
                del_btn.setFixedSize(22, 22)
                del_btn.setStyleSheet(f"""
                    QPushButton {{ background: transparent; border: none; color: #8B949E; font-size: 11px; border-radius: 4px; }}
                    QPushButton:hover {{ background: rgba(242,139,130,0.15); color: #F28B82; }}
                """)
                del_btn.clicked.connect(lambda _, n=name: _delete_preset(n))
                row_l.addWidget(del_btn)
                self._saved_presets_layout.addWidget(row)

            if not presets:
                empty = QLabel("No saved presets yet")
                empty.setStyleSheet("color: #30363D; font-size: 10px; background: transparent;")
                self._saved_presets_layout.addWidget(empty)

        def _delete_preset(name):
            try:
                presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8')) if PRESETS_FILE.exists() else {}
                presets.pop(name, None)
                PRESETS_FILE.write_text(_json.dumps(presets, indent=2, ensure_ascii=False), encoding='utf-8')
                _load_saved_presets()
            except Exception as e:
                print(f"[instructions] delete preset error: {e}")

        def _save_preset():
            text = text_edit.toPlainText().strip()
            if not text:
                return
            name, ok = QInputDialog.getText(dialog, "Save Preset", "Preset name:")
            if ok and name.strip():
                try:
                    presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8')) if PRESETS_FILE.exists() else {}
                    presets[name.strip()] = text
                    PRESETS_FILE.write_text(_json.dumps(presets, indent=2, ensure_ascii=False), encoding='utf-8')
                    _load_saved_presets()
                except Exception as e:
                    print(f"[instructions] save preset error: {e}")

        left_lay.addWidget(saved_presets_container)
        save_preset_btn = _chip("💾 Save current as preset", accent=True)
        save_preset_btn.clicked.connect(_save_preset)
        left_lay.addWidget(save_preset_btn)
        left_lay.addStretch()
        left_scroll.setWidget(left_w)
        body.addWidget(left_scroll)

        # ── RIGHT PANEL: editor ───────────────────────────────────────────────
        right_w = QWidget()
        right_w.setStyleSheet(f"QWidget {{ background: {_tc['base']}; }}")
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(16, 14, 16, 14)
        right_lay.setSpacing(8)

        desc = QLabel("These instructions shape how the assistant behaves for you. Click a preset on the left to insert text.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent;")
        right_lay.addWidget(desc)

        text_edit = QTextEdit()
        text_edit.setPlaceholderText(
            "Example:\n"
            "- Always be enthusiastic and encouraging\n"
            "- Use emojis when appropriate\n"
            "- Explain technical concepts simply"
        )
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                color: #E6EDF3;
                line-height: 1.6;
            }}
            QTextEdit:focus {{ border-color: #58A6FF; }}
        """)
        current = self.controller.get_custom_instructions()
        if current:
            text_edit.setPlainText(current)
        right_lay.addWidget(text_edit, 1)
        body.addWidget(right_w)
        body.setSizes([230, 520])
        root.addWidget(body)

        # ── Insert helper ─────────────────────────────────────────────────────
        def _insert(text):
            cursor = text_edit.textCursor()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            if text_edit.toPlainText() and not text_edit.toPlainText().endswith('\n'):
                text_edit.insertPlainText('\n')
            text_edit.insertPlainText(text)
            text_edit.setFocus()

        # Footer buttons
        footer = QFrame()
        footer.setFixedHeight(54)
        footer.setStyleSheet(f"QFrame {{ background: {_tc['surface']}; border-top: 1px solid {_tc['border']}; }}")
        foot_l = QHBoxLayout(footer)
        foot_l.setContentsMargins(16, 0, 16, 0)
        foot_l.setSpacing(8)

        clear_btn = _chip("🗑 Clear")
        clear_btn.clicked.connect(text_edit.clear)
        foot_l.addWidget(clear_btn)
        foot_l.addStretch()

        cancel_btn = _chip("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        foot_l.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                border-radius: 6px; padding: 8px 24px; font-size: 12px; color: #58A6FF; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(88,166,255,0.24); border-color: #58A6FF; color: #79BBFF; }}
        """)
        save_btn.clicked.connect(lambda: (
            self.controller.set_custom_instructions(text_edit.toPlainText().strip()),
            self.add_system_message("✓ **Custom instructions saved**"),
            dialog.accept()
        ))
        foot_l.addWidget(save_btn)
        root.addWidget(footer)

        _load_saved_presets()
        dialog.exec()

    def show_mode_menu(self):
        """Show mode selection menu"""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2D333B;
            }
            QMenu::separator {
                height: 1px;
                background: #2D333B;
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
        if user_name and hasattr(self, 'user_name_input'):
            self.user_name_input.setText(user_name)
        assistant_name = self.controller.get_assistant_name()
        if assistant_name and hasattr(self, 'assistant_name_input'):
            self.assistant_name_input.setText(assistant_name)
        # Always refresh hero labels — they exist in the new sidebar
        self._refresh_hero_labels()

    def _on_assistant_name_changed(self, text):
        """Called when assistant name input changes"""
        self.controller.set_assistant_name(text.strip())
        self._refresh_hero_labels()

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
        self._refresh_hero_labels()

    def _show_avatar_picker(self, emojis, on_select, title="Pick Avatar"):
        """Shared grid-based avatar picker popup."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QGridLayout, QWidget
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {_tc['base']};
            }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel(title)
        hdr.setStyleSheet(f"color: #E6EDF3; font-size: 13px; font-weight: 600; background: transparent;")
        lay.addWidget(hdr)

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        cols = 8
        for i, emoji in enumerate(emojis):
            btn = QPushButton(emoji)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 20px; padding: 0;
                    background: {_tc['elevated']};
                    border: 1px solid {_tc['border']};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: rgba(88,166,255,0.15);
                    border-color: #58A6FF;
                }}
            """)
            btn.clicked.connect(lambda _, e=emoji: (on_select(e), dlg.accept()))
            grid.addWidget(btn, i // cols, i % cols)
        lay.addWidget(grid_widget)
        dlg.exec()

    def change_bot_avatar(self):
        """Change bot avatar — grid picker"""
        emojis = [
            '🤖','🦾','🧠','👾','🤵','🦊','🐺','🦁',
            '🐼','🐨','🦝','🦔','🦉','🐉','👽','🌟',
            '⚡','🔮','🎭','🎪','🦸','🧙','🥷','👻',
            '🌈','🔥','💎','🪄','🛸','🧬','⚙️','🎯',
            '🌙','☀️','🌊','🍀','🦋','🌸','🎵','🏆',
        ]
        self._show_avatar_picker(emojis, self.set_bot_avatar, "Assistant Avatar")

    def change_user_avatar(self):
        """Change user avatar — grid picker"""
        emojis = [
            '👤','👨','👩','🧑','😊','😎','🤓','🧙',
            '🦸','🥷','👸','🤴','🧝','🧜','🧚','🧞',
            '🕵️','👨‍💻','👩‍💻','🧑‍🚀','🧑‍🎨','🧑‍🎤','🧑‍🍳','🧑‍🔬',
            '😄','😏','🥳','🤩','😈','👿','🤠','🥸',
            '🐱','🐶','🦊','🐸','🐯','🦊','🐧','🦄',
        ]
        self._show_avatar_picker(emojis, self.set_user_avatar, "User Avatar")

    def set_bot_avatar(self, emoji):
        """Set bot avatar to emoji, clearing any saved picture."""
        self.bot_avatar = emoji
        self._bot_avatar_pixmap = None
        self._bot_avatar_image_path = ''
        self.bot_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 24px;
                background-color: #1a2a3a;
                border-radius: 24px;
                border: 2px solid transparent;
            }
        """)
        self.bot_avatar_display.setText(emoji)
        self.save_config()

    def set_user_avatar(self, emoji):
        """Set user avatar to emoji, clearing any saved picture."""
        self.user_avatar = emoji
        self._user_avatar_pixmap = None
        self._user_avatar_image_path = ''
        _base = self._t()['base']
        self.user_avatar_display.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                background-color: #1a2a1a;
                border-radius: 13px;
                border: 2px solid {_base};
            }}
        """)
        self.user_avatar_display.setText(emoji)
        self.save_config()

    def _upload_avatar(self, target: str):
        """Open file picker then show a crop/position editor for avatar pictures."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                     QLabel, QSlider, QFileDialog)
        from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
        from PyQt6.QtCore import Qt, QRectF

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Avatar Picture", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.add_system_message("⚠️ Could not load that image file.")
            return

        # ── Picture editor dialog ─────────────────────────────────────────────
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Adjust Avatar Picture")
        dlg.setModal(True)
        dlg.setFixedSize(340, 420)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        # Live preview label (96×96 circle)
        PREVIEW_SIZE = 96
        preview = QLabel()
        preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)

        # State
        state = {'zoom': 1.0, 'ox': 0.5, 'oy': 0.5}   # ox/oy = centre as fraction 0-1

        def _render():
            """Re-render the circular preview from current state."""
            z = state['zoom']
            src_w = int(pixmap.width() / z)
            src_h = int(pixmap.height() / z)
            src_x = int((pixmap.width()  - src_w) * state['ox'])
            src_y = int((pixmap.height() - src_h) * state['oy'])
            src_x = max(0, min(src_x, pixmap.width()  - src_w))
            src_y = max(0, min(src_y, pixmap.height() - src_h))
            crop = pixmap.copy(src_x, src_y, src_w, src_h)
            scaled = crop.scaled(PREVIEW_SIZE, PREVIEW_SIZE,
                                 Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            # Crop to square centred
            cx = (scaled.width()  - PREVIEW_SIZE) // 2
            cy = (scaled.height() - PREVIEW_SIZE) // 2
            sq = scaled.copy(cx, cy, PREVIEW_SIZE, PREVIEW_SIZE)
            # Circular mask
            out = QPixmap(PREVIEW_SIZE, PREVIEW_SIZE)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(QRectF(0, 0, PREVIEW_SIZE, PREVIEW_SIZE))
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, sq)
            painter.end()
            preview.setPixmap(out)
            return out

        _render()

        def _make_slider_row(label_text, min_v, max_v, init, on_change):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #8B949E; font-size: 11px; background: transparent; min-width: 60px;")
            row.addWidget(lbl)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(min_v, max_v)
            sl.setValue(init)
            sl.setStyleSheet(f"""
                QSlider::groove:horizontal {{ height: 4px; background: {_tc['elevated']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: #58A6FF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
                QSlider::sub-page:horizontal {{ background: rgba(88,166,255,0.4); border-radius: 2px; }}
            """)
            sl.valueChanged.connect(on_change)
            row.addWidget(sl)
            return row

        def _on_zoom(v):
            state['zoom'] = 1.0 + v / 100.0
            _render()

        def _on_x(v):
            state['ox'] = v / 100.0
            _render()

        def _on_y(v):
            state['oy'] = v / 100.0
            _render()

        lay.addLayout(_make_slider_row("Zoom",     0, 300, 0,  _on_zoom))
        lay.addLayout(_make_slider_row("Horizontal", 0, 100, 50, _on_x))
        lay.addLayout(_make_slider_row("Vertical",   0, 100, 50, _on_y))

        note = QLabel("Adjust zoom and position, then click Apply.")
        note.setStyleSheet("color: #555; font-size: 10px; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()

        foot = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {_tc['border']}; border-radius:6px; padding:8px 18px; color:#8B949E; }} QPushButton:hover {{ color:#E6EDF3; }}")
        cancel_btn.clicked.connect(dlg.reject)
        foot.addWidget(cancel_btn)
        foot.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet("QPushButton { background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4); border-radius:6px; padding:8px 24px; color:#58A6FF; font-weight:600; } QPushButton:hover { background: rgba(88,166,255,0.24); }")
        foot.addWidget(apply_btn)
        lay.addLayout(foot)

        result_pixmap = [None]

        def _apply():
            result_pixmap[0] = _render()
            dlg.accept()

        apply_btn.clicked.connect(_apply)
        dlg.exec()

        if result_pixmap[0] is None:
            return

        final = result_pixmap[0]

        if target == 'bot':
            self.bot_avatar = ''
            self._bot_avatar_pixmap = final
            self._bot_avatar_image_path = path
            display = self.bot_avatar_display
            display.setPixmap(final.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            display.setText('')
        else:
            self.user_avatar = ''
            self._user_avatar_pixmap = final
            self._user_avatar_image_path = path
            display = self.user_avatar_display
            display.setPixmap(final.scaled(26, 26, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            display.setText('')
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
                f'color:#8B949E;user-select:text;">{latex_expr}</span>'
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
        # Stop the live-sync timer and drop the card reference so a new session gets a fresh card
        if hasattr(self, '_skills_ui_card_timer') and self._skills_ui_card_timer is not None:
            try:
                self._skills_ui_card_timer.stop()
            except Exception:
                pass
        self._skills_ui_card_widget = None
        self._skills_ui_card_timer = None

    def render_loaded_messages(self):
        """Render messages from loaded session"""
        try:
            self.clear_chat_silent()
            history = self.controller.ai.conversation_history
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                content = self.controller.ai.tool_manager.strip_tool_calls(content)
                if role == "ui_event":
                    if msg.get("_type") == "memory_context":
                        _ctx_id = msg.get("_memory_context_id", "")
                        if not _ctx_id or not isinstance(_ctx_id, str):
                            print(
                                f"[ChatWindow.render_loaded_messages] Skipping memory_context with invalid id: {_ctx_id!r}")
                        else:
                            self.add_memory_context_widget(
                                context_id=_ctx_id,
                                memories=msg.get("_memories_preview", []),
                                save_to_history=False,
                            )
                    elif msg.get("_type") == "skills_card":
                        self.add_loaded_skills_card(save_to_history=False)
                    else:
                        self.add_code_execution_note(
                            msg.get("_code", ""),
                            msg.get("_output", ""),
                            save_to_history=False,
                            annotation=msg.get("_annotation", ""),
                        )
                elif content:
                    if role == "user":
                        self.add_user_message(content)
                    elif role == "assistant":
                        self.add_ai_message(content)
        except Exception as e:
            print(f"[ChatWindow.render_loaded_messages] render_loaded_messages error: {e}")

    def _remove_tool_usage_format(self, content):
        """Remove tool usage JSON blocks from AI message"""
        cleaned = self.controller.ai.tool_manager.strip_tool_calls(content)
        return cleaned

    def _open_names_dialog(self):
        """Quick inline dialog to edit user + assistant names."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Names")
        dlg.setModal(True)
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        _ss_lbl = "color: #8B949E; font-size: 10px; background: transparent;"
        _ss_inp = f"""
            QLineEdit {{ background: {_tc['elevated']}; border: 1px solid {_tc['border']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px; color: #E6EDF3; }}
            QLineEdit:focus {{ border-color: rgba(88,166,255,0.55); }}
        """

        lbl_u = QLabel("Your name")
        lbl_u.setStyleSheet(_ss_lbl)
        lay.addWidget(lbl_u)
        inp_u = QLineEdit()
        inp_u.setPlaceholderText("Enter your name…")
        inp_u.setStyleSheet(_ss_inp)
        inp_u.setText(self.controller.get_user_name())
        lay.addWidget(inp_u)

        lbl_a = QLabel("Assistant name (leave blank for default)")
        lbl_a.setStyleSheet(_ss_lbl)
        lay.addWidget(lbl_a)
        inp_a = QLineEdit()
        inp_a.setPlaceholderText("e.g. Kim, Nova, Aria…")
        inp_a.setStyleSheet(_ss_inp)
        inp_a.setText(self.controller.get_assistant_name())
        lay.addWidget(inp_a)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {_tc['border']}; border-radius:6px; padding:7px 18px; color:#8B949E; }} QPushButton:hover {{ color:#E6EDF3; }}")
        cancel.clicked.connect(dlg.reject)
        save = QPushButton("Save")
        save.setStyleSheet("QPushButton { background: rgba(88,166,255,0.14); border:1px solid rgba(88,166,255,0.4); border-radius:6px; padding:7px 24px; color:#58A6FF; font-weight:600; } QPushButton:hover { background:rgba(88,166,255,0.24); }")

        def _save():
            self.controller.set_user_name(inp_u.text().strip())
            self.controller.set_assistant_name(inp_a.text().strip())
            self._refresh_hero_labels()
            dlg.accept()

        save.clicked.connect(_save)
        btns.addWidget(cancel)
        btns.addStretch()
        btns.addWidget(save)
        lay.addLayout(btns)
        dlg.exec()

    def _open_avatars_dialog(self):
        """Combined avatar editor for both bot and user."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                     QLabel, QTabWidget, QWidget)
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Avatars")
        dlg.setModal(True)
        dlg.setFixedWidth(380)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; background: {_tc['base']}; }}
            QTabBar::tab {{ background: {_tc['elevated']}; color: #8B949E; border: none;
                padding: 8px 20px; font-size: 11px; }}
            QTabBar::tab:selected {{ background: {_tc['surface']}; color: #E6EDF3; font-weight: 600; }}
            QTabBar::tab:hover {{ color: #E6EDF3; }}
        """)

        def _make_avatar_tab(target, current_emoji, display_label, emojis):
            w = QWidget()
            w.setStyleSheet(f"background: {_tc['base']};")
            wl = QVBoxLayout(w)
            wl.setContentsMargins(16, 16, 16, 16)
            wl.setSpacing(12)

            # Current preview
            preview_row = QHBoxLayout()
            av_preview = QLabel()
            av_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            av_preview.setFixedSize(56, 56)
            bg_color = "#1a2a3a" if target == 'bot' else "#1a2a1a"
            av_preview.setStyleSheet(f"font-size: 28px; background: {bg_color}; border-radius: 28px;")

            # Show current pixmap or emoji
            existing_pm = getattr(self, '_bot_avatar_pixmap' if target == 'bot' else '_user_avatar_pixmap', None)
            if existing_pm and not existing_pm.isNull():
                av_preview.setPixmap(existing_pm.scaled(56, 56,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                av_preview.setText(current_emoji or ('🤖' if target == 'bot' else '👤'))

            preview_row.addWidget(av_preview)
            preview_row.addSpacing(12)
            info_col = QVBoxLayout()
            info_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            lbl1 = QLabel(display_label)
            lbl1.setStyleSheet("font-size: 12px; font-weight: 600; color: #E6EDF3; background: transparent;")
            lbl2 = QLabel("Pick an emoji or upload a picture")
            lbl2.setStyleSheet("font-size: 10px; color: #555; background: transparent;")
            info_col.addWidget(lbl1)
            info_col.addWidget(lbl2)
            preview_row.addLayout(info_col)
            preview_row.addStretch()
            wl.addLayout(preview_row)

            # Emoji grid
            from PyQt6.QtWidgets import QGridLayout
            grid_w = QWidget()
            grid_w.setStyleSheet("background: transparent;")
            grid = QGridLayout(grid_w)
            grid.setSpacing(5)
            cols = 8
            for i, em in enumerate(emojis):
                btn = QPushButton(em)
                btn.setFixedSize(36, 36)
                btn.setStyleSheet(f"""
                    QPushButton {{ font-size: 18px; padding: 0; background: {_tc['elevated']};
                        border: 1px solid {_tc['border']}; border-radius: 6px; }}
                    QPushButton:hover {{ background: rgba(88,166,255,0.15); border-color: #58A6FF; }}
                """)
                def _pick(checked=False, e=em, p=av_preview, t=target):
                    bg = '#1a2a3a' if t == 'bot' else '#1a2a1a'
                    p.setStyleSheet(f"font-size: 28px; background: {bg}; border-radius: 28px;")
                    p.setText(e)
                    if t == 'bot':
                        self.set_bot_avatar(e)
                    else:
                        self.set_user_avatar(e)
                btn.clicked.connect(_pick)
                grid.addWidget(btn, i // cols, i % cols)
            wl.addWidget(grid_w)

            # Upload picture button
            upload_btn = QPushButton("🖼  Upload custom picture…")
            upload_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: 1px solid {_tc['border']};
                    border-radius: 7px; padding: 9px; font-size: 11px; color: #8B949E; }}
                QPushButton:hover {{ border-color: rgba(88,166,255,0.4); color: #58A6FF; }}
            """)
            def _do_upload(t=target, p=av_preview):
                self._upload_avatar(t)
                pm2 = getattr(self, '_bot_avatar_pixmap' if t == 'bot' else '_user_avatar_pixmap', None)
                if pm2 and not pm2.isNull():
                    p.setPixmap(pm2.scaled(56, 56,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation))
                    p.setText('')
                    size_row.setVisible(True)
            upload_btn.clicked.connect(lambda: _do_upload())

            # Size slider — visible only when a picture is active
            size_row = QWidget()
            size_row.setStyleSheet("background: transparent;")
            from PyQt6.QtWidgets import QHBoxLayout as _HBL, QSlider as _SL
            sr_lay = _HBL(size_row)
            sr_lay.setContentsMargins(0, 0, 0, 0)
            sr_lay.setSpacing(8)

            size_lbl = QLabel("Picture size")
            size_lbl.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent; min-width: 70px;")
            sr_lay.addWidget(size_lbl)

            _current_size = getattr(self, f'_{"bot" if target == "bot" else "user"}_avatar_size', 32)
            size_slider = _SL(Qt.Orientation.Horizontal)
            size_slider.setRange(20, 72)
            size_slider.setValue(_current_size)
            size_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{ height: 4px; background: {_tc['elevated']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: #58A6FF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; border: none; }}
                QSlider::sub-page:horizontal {{ background: rgba(88,166,255,0.4); border-radius: 2px; }}
            """)
            sr_lay.addWidget(size_slider, stretch=1)

            size_val_lbl = QLabel(f"{_current_size}px")
            size_val_lbl.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent; min-width: 30px;")
            sr_lay.addWidget(size_val_lbl)

            def _on_size(v, t=target, svl=size_val_lbl):
                svl.setText(f"{v}px")
                if t == 'bot':
                    self._bot_avatar_size = v
                else:
                    self._user_avatar_size = v
                # Sync the other slider if uniform is checked
                if _uniform_state[0]:
                    other = _all_sliders[1] if t == 'bot' else _all_sliders[0]
                    if other and other.value() != v:
                        other.blockSignals(True)
                        other.setValue(v)
                        other.blockSignals(False)
                        if t == 'bot':
                            self._user_avatar_size = v
                        else:
                            self._bot_avatar_size = v
                self.save_config()
            size_slider.valueChanged.connect(_on_size)
            _all_sliders.append(size_slider)

            # Only show if picture is already active
            has_picture = bool(existing_pm and not existing_pm.isNull())
            size_row.setVisible(has_picture)

            wl.addWidget(upload_btn)
            wl.addWidget(size_row)
            return w

        _all_sliders = []      # [bot_slider, user_slider] — filled as tabs are built
        _uniform_state = [getattr(self, '_avatar_size_uniform', False)]

        bot_emojis = [
            '🤖','🦾','🧠','👾','🤵','🦊','🐺','🦁',
            '🐼','🐨','🦝','🦔','🦉','🐉','👽','🌟',
            '⚡','🔮','🎭','🎪','🦸','🧙','🥷','👻',
            '🌈','🔥','💎','🪄','🛸','🧬','⚙️','🎯',
        ]
        user_emojis = [
            '👤','👨','👩','🧑','😊','😎','🤓','🧙',
            '🦸','🥷','👸','🤴','🧝','🧜','🧚','🧞',
            '🕵️','👨‍💻','👩‍💻','🧑‍🚀','🧑‍🎨','🧑‍🎤','🧑‍🍳','🧑‍🔬',
            '😄','😏','🥳','🤩','😈','🤠','🥸','🦄',
        ]

        tabs.addTab(_make_avatar_tab('bot',  self.bot_avatar,  "Assistant avatar", bot_emojis),  "🤖  Assistant")
        tabs.addTab(_make_avatar_tab('user', self.user_avatar, "Your avatar",       user_emojis), "👤  You")
        lay.addWidget(tabs)

        # Uniform size checkbox
        from PyQt6.QtWidgets import QCheckBox
        uniform_row = QWidget()
        uniform_row.setStyleSheet(f"background: {_tc['surface']}; border-top: 1px solid {_tc['border']};")
        ur_lay = QHBoxLayout(uniform_row)
        ur_lay.setContentsMargins(16, 10, 16, 10)
        uniform_cb = QCheckBox("Uniform size — keep both avatars the same size")
        uniform_cb.setChecked(_uniform_state[0])
        uniform_cb.setStyleSheet(f"""
            QCheckBox {{ color: #8B949E; font-size: 10px; background: transparent; spacing: 8px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid {_tc['border']}; background: {_tc['elevated']}; }}
            QCheckBox::indicator:checked {{ background: #58A6FF; border-color: #58A6FF; }}
        """)
        def _on_uniform(state):
            checked = bool(state)
            _uniform_state[0] = checked
            self._avatar_size_uniform = checked
            self.save_config()
            # Immediately sync user size to bot size when turned on
            if checked and _all_sliders:
                bot_val = _all_sliders[0].value() if _all_sliders else self._bot_avatar_size
                if len(_all_sliders) > 1:
                    _all_sliders[1].setValue(bot_val)
        uniform_cb.stateChanged.connect(_on_uniform)
        ur_lay.addWidget(uniform_cb)
        lay.addWidget(uniform_row)

        close_btn = QPushButton("Done")
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                margin: 12px 16px; border-radius: 7px; padding: 9px; font-size: 12px;
                color: #58A6FF; font-weight: 600; }}
            QPushButton:hover {{ background: rgba(88,166,255,0.24); }}
        """)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()

    def _refresh_hero_labels(self):
        """Update the hero name labels after identity changes."""
        if hasattr(self, '_hero_bot_name'):
            name = self.controller.get_assistant_name() or "Systema Auxilium"
            self._hero_bot_name.setText(name)
        if hasattr(self, '_hero_user_name'):
            user = self.controller.get_user_name() or "You"
            self._hero_user_name.setText(user)

    def _start_session_lock_watcher(self):
        """Lock the session list then start a QTimer on the main thread that polls
        until both is_processing and in_work_mode are False, then unlocks."""
        self.set_session_list_locked(True, "AI is responding…")

        if hasattr(self, '_lock_watcher_timer') and self._lock_watcher_timer is not None:
            try:
                self._lock_watcher_timer.stop()
            except Exception:
                pass

        self._lock_watcher_timer = QTimer(self)
        self._lock_watcher_timer.setInterval(250)
        self._lock_watcher_timer.timeout.connect(self._check_session_lock_state)
        self._lock_watcher_timer.start()

    def _check_session_lock_state(self):
        """Called every 250 ms (main thread) to check if the AI is still busy."""
        try:
            processing = getattr(self.controller, 'is_processing', False)
            try:
                in_work = self.controller.ai.tool_manager.in_work_mode
            except Exception:
                in_work = False
            if not processing and not in_work:
                self._lock_watcher_timer.stop()
                self._lock_watcher_timer = None
                self.set_session_list_locked(False)
        except Exception:
            try:
                self._lock_watcher_timer.stop()
                self._lock_watcher_timer = None
            except Exception:
                pass
            self.set_session_list_locked(False)

    def _session_show_more(self):
        self._session_visible_count += 10
        self.refresh_session_list()

    def _session_show_more(self):
        self._session_visible_count += 10
        self.refresh_session_list()

    def _session_show_all(self):
        self._session_visible_count = 99999
        self.refresh_session_list()

    def _session_collapse(self):
        self._session_visible_count = 10
        self.refresh_session_list()

    def _toggle_session_list(self):
        pass

    def set_session_list_locked(self, locked: bool, reason: str = ""):
        """
        Lock or unlock the session list.
        On lock: rebuilds all items as grayed-out disabled widgets (mousePressEvent = noop).
        On unlock: rebuilds all items as normal clickable widgets.
        """
        self._session_switching_locked = locked
        self.refresh_session_list()

        # Disable the New Session button while locked
        if hasattr(self, '_new_session_btn'):
            self._new_session_btn.setEnabled(not locked)

        # Show a brief status bar hint when the user is blocked
        if locked and reason:
            self.status_label.setText(f"⏳ {reason}")
        elif not locked:
            current = self.status_label.text()
            if current.startswith("⏳"):
                self.status_label.setText("")

    def refresh_session_list(self):
        """Refresh the session list with search, sort and show-more pagination."""
        if not hasattr(self, 'session_list_layout'):
            return

        while self.session_list_layout.count() > 0:
            item = self.session_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sessions = self.controller.get_session_list()

        # ── Search filter ──────────────────────────────────────────────────
        query = ""
        if hasattr(self, '_session_search'):
            query = self._session_search.text().strip().lower()
        if query:
            sessions = [s for s in sessions if query in s['name'].lower()]

        # ── Sort ──────────────────────────────────────────────────────────
        sort_idx = getattr(self, '_session_sort_idx', 0)
        if sort_idx == 1:
            sessions = sorted(sessions, key=lambda s: s['name'].lower())
        elif sort_idx == 2:
            sessions = sorted(sessions, key=lambda s: s['name'].lower(), reverse=True)

        total = len(sessions)
        visible_count = getattr(self, '_session_visible_count', 10)

        if hasattr(self, '_session_count_lbl'):
            if query:
                self._session_count_lbl.setText(f"{total} result{'s' if total != 1 else ''}")
            else:
                self._session_count_lbl.setText(f"{total} session{'s' if total != 1 else ''}")

        shown = sessions[:visible_count]
        remaining = total - len(shown)

        is_locked = getattr(self, '_session_switching_locked', False)
        for session in shown:
            session_item = self._create_session_item(
                session['id'], session['name'], session['date'],
                is_active=(session['id'] == self.controller.current_session_id),
                disabled=is_locked
            )
            self.session_list_layout.addWidget(session_item)

        # Footer: show more / show all / collapse
        if hasattr(self, '_session_footer'):
            show_footer = total > 10
            self._session_footer.setVisible(show_footer)
            if show_footer:
                # "Show more" only visible if there are hidden items
                self._show_more_btn.setVisible(remaining > 0)
                self._show_more_btn.setText(f"Show {min(remaining, 10)} more")
                # "Show all" only if not already showing all
                self._show_all_btn.setVisible(remaining > 0)
                self._show_all_btn.setText(f"Show all ({total})")
                # "Collapse" only if showing more than 10
                self._collapse_list_btn.setVisible(visible_count > 10 or remaining == 0)
                # Also reset visible count on new search
                if query and self._session_visible_count > total:
                    self._session_visible_count = 10


    def _create_session_item(self, session_id, session_name, creation_date, is_active=False, disabled=False):
        """Create a session list item widget. Pass disabled=True to gray it out and block clicks."""
        if disabled:
            # ── Locked / grayed-out appearance ────────────────────────────
            item_widget = QFrame()
            item_widget.setStyleSheet("""
                QFrame {
                    background-color: transparent;
                    border-radius: 6px;
                    padding: 8px;
                    margin: 2px 0px;
                    opacity: 0.4;
                }
            """)
            layout = QHBoxLayout(item_widget)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)

            content_layout = QVBoxLayout()
            content_layout.setSpacing(2)

            name_label = QLabel(session_name)
            name_label.setStyleSheet("color: #4A5060; font-size: 12px; font-weight: normal;")
            name_label.setWordWrap(True)
            content_layout.addWidget(name_label)

            date_label = QLabel(creation_date)
            date_label.setStyleSheet("color: #3A3F4A; font-size: 10px;")
            content_layout.addWidget(date_label)

            layout.addLayout(content_layout, 1)

            # Lock icon instead of delete button
            lock_lbl = QLabel("")
            lock_lbl.setFixedSize(24, 24)
            lock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lock_lbl.setStyleSheet("font-size: 11px; color: #3A3F4A;")
            layout.addWidget(lock_lbl)

            # Apply opacity effect to the whole item
            opacity_effect = QGraphicsOpacityEffect(item_widget)
            opacity_effect.setOpacity(0.4)
            item_widget.setGraphicsEffect(opacity_effect)

            # Blocked cursor + no-op click
            item_widget.setCursor(Qt.CursorShape.ForbiddenCursor)
            item_widget.mousePressEvent = lambda e: None

            return item_widget

        # ── Normal (enabled) appearance ────────────────────────────────────
        item_widget = QFrame()
        item_widget.setStyleSheet(f"""
            QFrame {{
                background-color: {"#21262D" if is_active else "transparent"};
                border-radius: 6px;
                padding: 8px;
                margin: 2px 0px;
            }}
            QFrame:hover {{
                background-color: {"#21262D" if is_active else "#1E2228"};
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
        """Load session when clicked — guarded against concurrent loads, AI generation, and work mode."""

        # ── Guard 1: already loading — items are rebuilt as disabled so this
        #    lambda should never fire, but flag check is the final safety net ─
        if getattr(self, '_session_switching_locked', False):
            return

        # ── Guard 2: AI is currently generating a response ─────────────────
        if getattr(self.controller, 'is_processing', False):
            self.status_label.setText("⏳ Cannot switch sessions while AI is responding…")
            QTimer.singleShot(2500, lambda: (
                self.status_label.setText("")
                if self.status_label.text().startswith("⏳") else None
            ))
            return

        # ── Guard 3: AI is in work / tool-use mode ─────────────────────────
        try:
            in_work = self.controller.ai.tool_manager.in_work_mode
        except Exception:
            in_work = False
        if in_work:
            self.status_label.setText("⏳ Cannot switch sessions while AI is working…")
            QTimer.singleShot(2500, lambda: (
                self.status_label.setText("")
                if self.status_label.text().startswith("⏳") else None
            ))
            return

        # ── All clear: lock (rebuilds items as grayed disabled), load, unlock ─
        self.set_session_list_locked(True, "Loading session…")
        try:
            self.controller.load_session(session_id)
        finally:
            QTimer.singleShot(1500, lambda: self.set_session_list_locked(False))

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

    def _make_chat_avatar(self, target: str, size: int = 32) -> QLabel:
        """Create a 32px circular avatar label using pixmap if set, else emoji."""
        av = QLabel()
        av.setFixedSize(size, size)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)

        is_bot = (target == 'bot')
        pixmap = getattr(self, '_bot_avatar_pixmap' if is_bot else '_user_avatar_pixmap', None)

        if pixmap and not pixmap.isNull():
            # Scale the circular pixmap to the requested size
            from PyQt6.QtGui import QPainter, QPainterPath
            from PyQt6.QtCore import QRectF
            out = QPixmap(size, size)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            p = QPainterPath()
            p.addEllipse(QRectF(0, 0, size, size))
            painter.setClipPath(p)
            painter.drawPixmap(0, 0, pixmap.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
            painter.end()
            av.setPixmap(out)
            av.setStyleSheet(f"""
                QLabel {{
                    border-radius: {size//2}px;
                    min-width: {size}px; min-height: {size}px;
                    max-width: {size}px; max-height: {size}px;
                }}
            """)
        else:
            emoji = (self.bot_avatar or '🤖') if is_bot else (self.user_avatar or '👤')
            bg = "#58A6FF" if is_bot else "#34A853"
            av.setText(emoji)
            av.setStyleSheet(f"""
                QLabel {{
                    background-color: {bg};
                    border-radius: {size//2}px;
                    font-size: {size//2 - 2}px;
                    min-width: {size}px; min-height: {size}px;
                    max-width: {size}px; max-height: {size}px;
                }}
            """)
        return av

    def add_user_message(self, message, image_paths=None):
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
        main_container_widget.setMaximumWidth(int(600 * self.chat_zoom))
        main_container_widget.setStyleSheet("background: transparent;")
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel("<b>You</b>")
        name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px; background: transparent;")
        main_container.addWidget(name_label)

        content_wrapper = QFrame()
        _tc = self._t()
        content_wrapper.setStyleSheet(f"""
                    QFrame {{
                        background-color: {_tc['surface']};
                        border: none;
                        border-radius: 12px;
                    }}
                """)

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(12, 12, 12, 8)
        content_wrapper_layout.setSpacing(8)

        # ── Image thumbnails (shown above text when images were attached) ─────
        if image_paths:
            img_row = QHBoxLayout()
            img_row.setSpacing(6)
            img_row.setContentsMargins(0, 0, 0, 4)
            for img_path in image_paths:
                thumb = QLabel()
                thumb.setFixedSize(64, 64)
                thumb.setStyleSheet("border-radius: 6px; background: rgba(255,255,255,0.06);")
                thumb.setScaledContents(True)
                pm = QPixmap(img_path)
                if not pm.isNull():
                    thumb.setPixmap(pm.scaled(64, 64,
                                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation))
                img_row.addWidget(thumb)
            img_row.addStretch()
            content_wrapper_layout.addLayout(img_row)
        # ─────────────────────────────────────────────────────────────────────

        fsize = self._get_msg_font_size()
        text_label = QLabel()
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setText(self.render_markdown(message))
        text_label.setWordWrap(True)
        text_label.setOpenExternalLinks(True)
        text_label.setStyleSheet(f"""
                    QLabel {{
                        color: #E8EAED;
                        font-size: {fsize}px;
                        line-height: 1.5;
                        background: transparent;
                        border: none;
                    }}
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
        avatar = self._make_chat_avatar('user', getattr(self, '_user_avatar_size', 32))
        message_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        self.last_user_message_widget = message_widget

        message_index = len(self.message_widgets)
        message_data = {
            'widget': message_widget,
            'role': 'user',
            'content': message,
            'index': message_index,
            'text_label': text_label,
            'text_labels': [text_label],
            'content_wrapper': content_wrapper,
            'main_container_widget': main_container_widget,
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
        avatar = self._make_chat_avatar('bot', getattr(self, '_bot_avatar_size', 32))
        message_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        main_container_widget = QWidget()
        main_container_widget.setStyleSheet("background: transparent;")
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        _display_name = self.controller.get_assistant_name() or "Systema Auxilium"
        name_label = QLabel(f"<b>{_display_name}</b>")
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px; background: transparent;")
        main_container.addWidget(name_label)

        content_wrapper = QFrame()
        _tc = self._t()
        content_wrapper.setStyleSheet(f"""
            QFrame {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 12px;
            }}
        """)

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(10, 10, 10, 10)
        content_wrapper_layout.setSpacing(8)

        parts = self.render_markdown_with_code_blocks(display_message)
        first_text_label = None
        all_text_labels = []
        fsize = self._get_msg_font_size()

        if isinstance(parts, list):
            for part in parts:
                if part[0] == 'text':
                    text_label = QLabel()
                    text_label.setTextFormat(Qt.TextFormat.RichText)
                    text_label.setText(self.render_markdown(part[1]))
                    text_label.setWordWrap(True)
                    text_label.setOpenExternalLinks(True)
                    text_label.setStyleSheet(f"""
                        QLabel {{
                            color: #BDC1C6;
                            font-size: {fsize}px;
                            line-height: 1.5;
                            background: transparent;
                            border: none;
                        }}
                    """)
                    text_label.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse |
                        Qt.TextInteractionFlag.LinksAccessibleByMouse
                    )
                    content_wrapper_layout.addWidget(text_label)
                    all_text_labels.append(text_label)
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
            text_label.setStyleSheet(f"""
                QLabel {{
                    color: #BDC1C6;
                    font-size: {fsize}px;
                    line-height: 1.5;
                    background: transparent;
                    border: none;
                }}
            """)
            text_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            content_wrapper_layout.addWidget(text_label)
            first_text_label = text_label
            all_text_labels.append(text_label)

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
            'text_labels': all_text_labels,
            'content_wrapper': content_wrapper,
            'main_container_widget': main_container_widget,
            'content_wrapper_layout': content_wrapper_layout
        }
        self.message_widgets.append(message_data)

        menu_btn.clicked.connect(lambda: self._show_message_menu(message_data))

        if self._thinking_bubble_widget is not None:
            idx = self.chat_layout.indexOf(self._thinking_bubble_widget)
            self.chat_layout.insertWidget(idx, message_widget)
        else:
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
        # ── Update work mode banner if this is a Working: annotation ─────────
        import re as _re
        if "**Working:**" in message or message.startswith("Working:"):
            # Extract the annotation text (strip markdown bold/italic markers)
            clean = _re.sub(r'\*+', '', message).replace("Working:", "").strip()
            if hasattr(self, '_work_banner'):
                self._work_banner.setText(f"⚙ Working: {clean}")
                self._work_banner.show()
        # ─────────────────────────────────────────────────────────────────────
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
        _tc = self._t()
        text_label.setStyleSheet(f"""
            QLabel {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                padding: 10px 16px;
                color: #9AA0A6;
                font-size: 11px;
                line-height: 1.4;
            }}
        """)
        message_layout.addWidget(text_label)

        # Track so apply_theme can restyle retroactively
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'system',
            'content_wrapper': text_label,  # text_label IS the styled surface here
        })

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
            badge_bg = "#21262D"
            action_text = "Load"
            action_color = "#4CAF50"
            action_bg = "#1A2B1A"
            action_hover = "#223322"
            icon = "⚡"

        card = QFrame()
        _tc = self._t()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
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
                            background-color: #21262D; color: #9AA0A6;
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
                                    background-color: #21262D; color: #9AA0A6;
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

    def add_loaded_skills_card(self, save_to_history: bool = True):
        """
        Show (or refresh) the single per-session loaded-skills card.
        If the card already exists in this session, scrolls to it instead of creating a new one.
        A QTimer fires every 500 ms to sync badges/buttons with live skill state.
        Persisted as a ui_event so it survives session save/load.
        """
        # ── Only one card allowed per session ─────────────────────────────────
        if self._skills_ui_card_widget is not None:
            self.scroll_to_widget(self._skills_ui_card_widget)
            return

        skill_mgr = None
        if hasattr(self, 'controller') and self.controller:
            skill_mgr = getattr(self.controller.ai, 'skill_manager', None)
        if skill_mgr is None:
            return

        _tc = self._t()

        # ── Outer wrapper ──────────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet(
            "QFrame { background-color: transparent; padding: 4px 16px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header row (always visible) ────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
            }}
        """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 6, 10, 6)
        header_lay.setSpacing(8)

        icon_lbl = QLabel("⚡")
        icon_lbl.setStyleSheet(
            "font-size: 13px; background: transparent; border: none; color: #7C7CFF;")
        icon_lbl.setFixedWidth(18)
        header_lay.addWidget(icon_lbl)

        summary_lbl = QLabel()
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)

        toggle_btn = QPushButton("▶ Show")
        toggle_btn.setFixedSize(58, 20)
        toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {_tc['border']};
                border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
            }}
            QPushButton:hover {{ color: {_tc['accent']}; border-color: {_tc['accent']}; }}
        """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header)

        # ── Expandable skills list ─────────────────────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(4, 4, 4, 4)
        detail_lay.setSpacing(4)
        outer_lay.addWidget(detail)

        # ── Per-skill rows (added/removed dynamically by the sync timer) ───────
        _skill_rows = {}  # name → {'row': QFrame, 'badge': QLabel, 'btn': QPushButton}

        def _row_style_loaded():
            return (
                "QPushButton { background-color: #3C1A1A; color: #C0392B; "
                "border: 1px solid #C0392B; border-radius: 4px; font-size: 9px; padding: 0; }"
                "QPushButton:hover { background-color: #4A2020; }"
            )

        def _row_style_unloaded():
            return (
                "QPushButton { background-color: #1A2B1A; color: #4CAF50; "
                "border: 1px solid #4CAF50; border-radius: 4px; font-size: 9px; padding: 0; }"
                "QPushButton:hover { background-color: #223322; }"
            )

        def _badge_style_loaded():
            return ("QLabel { background-color: #1A2B1A; color: #4CAF50; "
                    "border-radius: 4px; font-size: 9px; padding: 1px 6px; }")

        def _badge_style_unloaded():
            return ("QLabel { background-color: #21262D; color: #9AA0A6; "
                    "border-radius: 4px; font-size: 9px; padding: 1px 6px; }")

        def _make_toggle(n):
            def _toggle_skill():
                smgr = None
                if hasattr(self, 'controller') and self.controller:
                    smgr = getattr(self.controller.ai, 'skill_manager', None)
                if smgr is None:
                    return
                if smgr.is_loaded(n):
                    smgr.unload_skill(n)
                else:
                    smgr.load_skill(n)

            return _toggle_skill

        def _update_summary():
            if skill_mgr is None:
                return
            names = list(skill_mgr.get_loaded_skills().keys())
            count = len(names)
            if count == 0:
                preview = "none loaded"
                extra = ""
            else:
                first = names[0]
                preview = first[:40] + ("…" if len(first) > 40 else "")
                extra = f"&nbsp;<span style='font-size:10px;color:{_tc['accent']};'>+{count - 1} more</span>" if count > 1 else ""
            summary_lbl.setText(
                f"<span style='color:{_tc['accent']};font-size:11px;font-weight:600;'>"
                f"Skills loaded</span>"
                f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
                f"<span style='font-size:10px;color:#8B949E;'>{preview}</span>{extra}")

        def _build_or_refresh_rows():
            if skill_mgr is None:
                return
            current_loaded = skill_mgr.get_loaded_skills()  # {name: content}
            all_skills = {s['name']: s for s in skill_mgr.get_skills()}

            # Remove rows for skills that are no longer in the skill list at all
            gone = [n for n in list(_skill_rows.keys()) if n not in all_skills]
            for name in gone:
                row_data = _skill_rows.pop(name)
                try:
                    row_data['row'].deleteLater()
                except Exception:
                    pass

            # Add rows for skills that don't have a row yet
            for name in all_skills:
                if name not in _skill_rows:
                    is_loaded = name in current_loaded
                    row = QFrame()
                    row.setStyleSheet(f"""
                        QFrame {{
                            background: {_tc['elevated']};
                            border: 1px solid {_tc['accent']}33;
                            border-radius: 6px;
                        }}
                    """)
                    row_lay = QHBoxLayout(row)
                    row_lay.setContentsMargins(10, 5, 8, 5)
                    row_lay.setSpacing(8)

                    name_lbl = QLabel(f"<b>{name}</b>")
                    name_lbl.setStyleSheet(
                        "font-size: 11px; color: #C8CAFF; background: transparent; border: none;")
                    name_lbl.setWordWrap(True)
                    row_lay.addWidget(name_lbl, stretch=1)

                    badge = QLabel("● Loaded" if is_loaded else "○ Unloaded")
                    badge.setStyleSheet(_badge_style_loaded() if is_loaded else _badge_style_unloaded())
                    row_lay.addWidget(badge)

                    btn = QPushButton("Unload" if is_loaded else "Load")
                    btn.setFixedSize(52, 20)
                    btn.setStyleSheet(_row_style_loaded() if is_loaded else _row_style_unloaded())
                    btn.clicked.connect(_make_toggle(name))
                    row_lay.addWidget(btn)

                    detail_lay.addWidget(row)
                    _skill_rows[name] = {'row': row, 'badge': badge, 'btn': btn}

            # Sync badge + button text/style for every existing row
            for name, rd in _skill_rows.items():
                is_loaded = name in current_loaded
                if is_loaded:
                    rd['badge'].setText("● Loaded")
                    rd['badge'].setStyleSheet(_badge_style_loaded())
                    rd['btn'].setText("Unload")
                    rd['btn'].setStyleSheet(_row_style_loaded())
                else:
                    rd['badge'].setText("○ Unloaded")
                    rd['badge'].setStyleSheet(_badge_style_unloaded())
                    rd['btn'].setText("Load")
                    rd['btn'].setStyleSheet(_row_style_unloaded())

        # Initial population
        _update_summary()
        _build_or_refresh_rows()

        # ── Toggle expand / collapse ───────────────────────────────────────────
        def _toggle():
            if detail.isHidden():
                detail.show()
                toggle_btn.setText("▼ Hide")
            else:
                detail.hide()
                toggle_btn.setText("▶ Show")

        toggle_btn.clicked.connect(_toggle)

        # ── 500 ms live-sync timer ─────────────────────────────────────────────
        sync_timer = QTimer()
        sync_timer.setInterval(500)

        def _sync():
            if message_widget is None:
                return
            try:
                _update_summary()
                _build_or_refresh_rows()
            except Exception:
                pass

        sync_timer.timeout.connect(_sync)
        sync_timer.start()
        self._skills_ui_card_timer = sync_timer

        # ── Insert before trailing spacer ──────────────────────────────────────
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(
            message_widget,
            on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track ──────────────────────────────────────────────────────────────
        self._skills_ui_card_widget = message_widget
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'skills_card',
            'content_wrapper': header,
        })

        # ── Persist to conversation history (skipped on reload) ────────────────
        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    '_type': 'skills_card',
                    'content': '',
                })
            except Exception:
                pass

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
                background-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2D333B;
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
        rewind_action.triggered.connect(lambda: self._rewind_to_here(message_data))
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
                background-color: #161B22;
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
                background-color: #21262D;
                border: 1px solid #30363D;
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
                background-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 8px 16px;
                color: #E8EAED;
            }
            QPushButton:hover {
                background-color: #2D333B;
            }
        """)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)

        save_text = "Save & Regenerate" if message_data['role'] == 'user' else "Save"
        save_btn = QPushButton(save_text)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #58A6FF;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #388BFD;
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

        if message_data['role'] == 'user':
            # Capture old content BEFORE mutating so history search still works
            old_content = message_data['content']

            # Find where this user turn sits in conversation_history
            history_index = self._find_history_index_by_role_content('user', old_content)

            # Remove this widget and everything after it
            target_index = message_data['index']
            widgets_to_remove = [md['widget'] for md in self.message_widgets[target_index:]]
            self.message_widgets = self.message_widgets[:target_index]

            # Truncate history UP TO (not including) this user message so
            # send_message → generate_response can append it fresh (no duplicate)
            if history_index >= 0:
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index]

            # Animate out the stale widgets
            for widget in widgets_to_remove:
                def _destroy(w=widget):
                    self.chat_layout.removeWidget(w)
                    w.deleteLater()
                self._animate_message_out(widget, _destroy)

            # Re-render the user bubble with updated text, then fire AI
            self.add_user_message(new_content)
            self.controller.send_message(new_content)
            QTimer.singleShot(600, self._start_session_lock_watcher)

        else:
            # Assistant / system edit: update in-place
            history_index = self._get_history_index(message_data)
            message_data['content'] = new_content
            if history_index >= 0:
                self.controller.ai.conversation_history[history_index]['content'] = new_content
            if message_data.get('text_label'):
                message_data['text_label'].setText(self.render_markdown(new_content))

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


    def _rewind_to_here(self, message_data):
        """'Rewind to Here' from the context menu.
        Always keeps the target message itself; discards everything after it.
        - Assistant bubble: just truncates, no new request.
        - User bubble: truncates history before this turn so send_message can
          re-append it cleanly, then fires a fresh AI response."""
        target_index = message_data['index']
        role = message_data['role']

        # Widgets: keep up to and including the target
        widgets_to_remove = [md['widget'] for md in self.message_widgets[target_index + 1:]]
        self.message_widgets = self.message_widgets[:target_index + 1]

        # History: find real position by content+role
        history_index = self._get_history_index(message_data)
        if history_index >= 0:
            if role == 'user':
                # Truncate BEFORE this entry — send_message will re-append it
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index]
            else:
                # Truncate AFTER this entry — keep the assistant message
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index + 1]

        self.controller._auto_save_session()

        for widget in widgets_to_remove:
            def _destroy(w=widget):
                self.chat_layout.removeWidget(w)
                w.deleteLater()
            self._animate_message_out(widget, _destroy)

        # For user messages: fire a new AI response (bubble stays visible)
        if role == 'user':
            self.controller.send_message(message_data['content'])
            QTimer.singleShot(600, self._start_session_lock_watcher)

    def _regenerate_response(self, message_data):
        """Regenerate AI response — rewinds before the user message, re-renders the user
        bubble, then fires a fresh send so the AI responds again."""
        target_index = message_data['index']

        user_msg = None
        for i in range(target_index - 1, -1, -1):
            if self.message_widgets[i]['role'] == 'user':
                user_msg = self.message_widgets[i]
                break

        if not user_msg:
            return

        user_message = user_msg['content']

        # Rewind to just BEFORE the user message — removes it and everything after
        self._rewind_to_message(user_msg, keep_message=False)

        # Re-render the user bubble so it stays visible in chat
        self.add_user_message(user_message)

        # Fire AI (generate_response will append user turn to history once)
        self.controller.send_message(user_message)
        QTimer.singleShot(600, self._start_session_lock_watcher)

    def _get_history_index(self, message_data):
        """Find the real index of a message in conversation_history by matching role+content.
        Searches from the end so the most-recent occurrence is found first."""
        role = message_data.get('role')
        content = message_data.get('content', '')
        history = self.controller.ai.conversation_history
        for i in range(len(history) - 1, -1, -1):
            entry = history[i]
            if entry.get('role') == role and entry.get('content') == content:
                return i
        return -1

    def _find_history_index_by_role_content(self, role, content):
        """Like _get_history_index but takes role+content directly (used before message_data is mutated)."""
        history = self.controller.ai.conversation_history
        for i in range(len(history) - 1, -1, -1):
            entry = history[i]
            if entry.get('role') == role and entry.get('content') == content:
                return i
        return -1


    def send_message(self):
        """Send message — supports multi-image and persistent pinned images."""
        message = self.input_field.toPlainText().strip()
        if not message:
            return

        self._user_scrolling = False

        was_manually_resized = self.input_field.text_input.manual_resize
        stored_height = self.input_field.text_input.height() if was_manually_resized else None

        # ── Collect all images for this send ─────────────────────────────────
        # pinned first (persistent context), then newly attached from input bar
        provider = self.controller.get_ai_provider()
        images_for_send = []
        if provider in ('puter', 'custom_script'):
            images_for_send = (
                    [pi['path'] for pi in self.pinned_images] + list(self.attached_images)
            )
        # ─────────────────────────────────────────────────────────────────────

        if self.force_mode == 'work_environment':
            message = "[VERY CRITICAL THE USER HAS ENFORCED: work_environment ONLY and FULFILL THIS TASK EFFICIENTLY (ignore if the message of the user doesn't request of anything)] " + message
        elif self.force_mode == 'execute_code':
            message = "[VERY CRITICAL THE USER HAS ENFORCED: execute_code ONLY and RUN a SINGLE PYTHON CODE TO DO THIS REQUEST(ignore if the message of the user doesn't request of anything)] " + message

        display_message = self.input_field.toPlainText().strip()
        self.last_sent_message = display_message
        self.add_user_message(display_message, image_paths=images_for_send if images_for_send else None)

        ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.add_user_message(display_message, image_paths=images_for_send if images_for_send else None)

        # ── Pin newly attached images, then clear input bar ───────────────────
        newly_attached = list(self.attached_images)  # snapshot before clear
        self.input_field.clear()
        self._clear_image_preview()

        if was_manually_resized and stored_height:
            self.input_field.text_input.manual_resize = True
            self.input_field.text_input.setFixedHeight(stored_height)

        # Add newly attached images as persistent pinned widgets
        if provider in ('puter', 'custom_script'):
            for p in newly_attached:
                self._add_pinned_image_widget(p, auto_detach=False)

        # Remove any pinned images that were set to send-once (auto_detach=True)
        # We already captured them in images_for_send above, so it's safe to remove now
        for pi in list(self.pinned_images):
            if pi.get('auto_detach', False):
                self._remove_pinned_image(pi)
        # ─────────────────────────────────────────────────────────────────────

        if images_for_send:
            self.controller.send_message_with_image(message, images_for_send)
        else:
            self.controller.send_message(message)

        # ── Auto-lock session list while AI is busy, then auto-unlock ────────
        QTimer.singleShot(600, self._start_session_lock_watcher)


    def _update_token_count(self):
        """Update the token estimate label whenever the input text changes."""
        try:
            if not hasattr(self, '_token_count_lbl') or not self._token_count_lbl.isVisible():
                return
            from core.token_est import estimate_next_message_tokens, estimate_tokens
            text = self.input_field.toPlainText()
            hist = []
            sys_tokens = 0
            ai = getattr(self.controller, 'ai', None)
            if ai:
                hist = getattr(ai, 'chat_history', []) or getattr(ai, 'conversation_history', [])
                sys_tokens = estimate_tokens(getattr(ai, 'system_prompt', '') or '')
            total = estimate_next_message_tokens(text, hist) + sys_tokens
            lbl = f"~{total/1000:.1f}k token per message" if total >= 1000 else f"~{total} token per message"
            if total > 50000:
                color = "#FF6B6B"
            elif total > 20000:
                color = "#E8833A"
            elif total > 5000:
                color = "#8B949E"
            else:
                color = "#3D4450"
            self._token_count_lbl.setStyleSheet(
                f"QLabel {{ color: {color}; font-size: 9px; background: transparent; padding: 0 4px; }}")
            self._token_count_lbl.setText(lbl)
        except Exception:
            pass

    # ── Image preview helpers ─────────────────────────────────────────────────

    def _show_image_preview(self, path):
        """Attach an image — goes straight to the pinned card overlay above the input."""
        self._add_pinned_image_widget(path, auto_detach=False)

    def _remove_one_image_preview(self, path, card_widget):
        """Remove a single image card from the input-bar strip."""
        if path in self.attached_images:
            self.attached_images.remove(path)
        self.attached_image = self.attached_images[-1] if self.attached_images else None
        self._img_thumbs_layout.removeWidget(card_widget)
        card_widget.deleteLater()
        if not self.attached_images:
            self._img_preview_bar.hide()

    def _clear_image_preview(self):
        """Remove ALL images from the input-bar strip."""
        self.attached_images.clear()
        self.attached_image = None
        while self._img_thumbs_layout.count() > 1:  # keep trailing stretch
            item = self._img_thumbs_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._img_preview_bar.hide()

    def _add_pinned_image_widget(self, path, auto_detach=False):
        """Add a persistent pinned-image card above the input area.

        The card shows a thumbnail, filename, a 🔁 toggle (persistent vs send-once),
        and an ✕ button to manually detach the image from context.
        As long as the card is visible the image is re-sent with every message.
        """
        import os
        # No duplicates
        for pi in self.pinned_images:
            if pi['path'] == path:
                return

        _tc = self._t()
        outer = QFrame()
        outer.setObjectName("pinnedImgCard")
        outer.setStyleSheet(f"""
                        QFrame#pinnedImgCard {{
                            background: {_tc['input_card']};
                            border: 1px solid {_tc['input_card_border']};
                            border-radius: 10px;
                        }}
                    """)
        outer.setMaximumWidth(300)

        row = QHBoxLayout(outer)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)

        # Thumbnail
        thumb = QLabel()
        thumb.setFixedSize(40, 40)
        thumb.setStyleSheet(f"border-radius: 5px; background: {_tc['input_card_border']};")
        thumb.setScaledContents(True)
        pm = QPixmap(path)
        if not pm.isNull():
            thumb.setPixmap(pm.scaled(40, 40,
                                      Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                      Qt.TransformationMode.SmoothTransformation))
        row.addWidget(thumb)

        # Name + status
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        name_lbl = QLabel(os.path.basename(path))
        name_lbl.setStyleSheet("color: #C9D1D9; font-size: 10px; background: transparent;")
        name_lbl.setWordWrap(True)
        info_col.addWidget(name_lbl)
        status_lbl = QLabel("🔁 Sending with every message")
        status_lbl.setStyleSheet(f"color: {_tc['accent']}; font-size: 9px; background: transparent;")
        info_col.addWidget(status_lbl)
        row.addLayout(info_col, stretch=1)

        pin_info = {'path': path, 'widget': outer, 'auto_detach': auto_detach}

        # 🔁 toggle button
        toggle_btn = QPushButton("🔁")
        toggle_btn.setFixedSize(26, 26)
        toggle_btn.setToolTip("Toggle: send every message / send once then detach")
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(not auto_detach)
        toggle_btn.setStyleSheet(f"""
                        QPushButton {{ background: {_tc['elevated']}; border: 1px solid {_tc['input_card_border']};
                            border-radius: 6px; font-size: 11px; color: {_tc['accent']}; }}
                        QPushButton:checked {{ background: {_tc['input_card_border']}; }}
                        QPushButton:hover   {{ background: {_tc['elevated']}; border-color: {_tc['accent']}; }}
                    """)

        def _on_toggle(checked, pi=pin_info, sl=status_lbl):
            pi['auto_detach'] = not checked
            sl.setText("🔁 Sending with every message" if checked
                       else "1️⃣ Sending once (then detach)")

        toggle_btn.toggled.connect(_on_toggle)
        row.addWidget(toggle_btn)

        # ✕ detach button
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(22, 22)
        x_btn.setToolTip("Detach image from context")
        x_btn.setStyleSheet("""
                QPushButton { background: rgba(255,255,255,0.05);
                    border: 1px solid rgba(255,255,255,0.1);
                    border-radius: 6px; color: #8B949E; font-size: 10px; }
                QPushButton:hover { background: rgba(234,67,53,0.25); color: #EA4335;
                    border-color: rgba(234,67,53,0.5); }
            """)
        x_btn.clicked.connect(lambda _, pi=pin_info: self._remove_pinned_image(pi))
        row.addWidget(x_btn)

        pin_info['row_wrapper'] = outer
        self.pinned_images.append(pin_info)

        # Insert before the trailing stretch so cards stay left-aligned
        count = self._pinned_area_layout.count()
        self._pinned_area_layout.insertWidget(count - 1, outer)
        self._pinned_area.show()
        QTimer.singleShot(10, self._update_pinned_overlay)
        # ── Sync to Android ──────────────────────────────────────────────────
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            _ab.notify_image_attached(path, send_every=not auto_detach)

    def _update_pinned_overlay(self):
        """Reposition the pinned-image overlay to float just above the input container."""
        if not hasattr(self, '_pinned_area') or not hasattr(self, 'input_container'):
            return
        if not hasattr(self, 'container'):
            return
        if not self._pinned_area.isVisible():
            return
        try:
            ic_pos = self.input_container.mapTo(
                self.container, self.input_container.rect().topLeft()
            )
            pinned_h = self._pinned_area.height()
            self._pinned_area.setGeometry(
                0,
                ic_pos.y() - pinned_h,
                self.container.width(),
                pinned_h,
            )
            self._pinned_area.raise_()
        except Exception:
            pass

    def _remove_pinned_image(self, pin_info, notify=True):
        """Remove a single pinned image card.

        notify=False when called from the Android bridge (detach initiated by
        Android) so we don't echo image_detached back to the phone.
        """
        _detached_path = pin_info.get('path', '')
        if pin_info in self.pinned_images:
            self.pinned_images.remove(pin_info)
        wrapper = pin_info.get('row_wrapper')
        if wrapper:
            self._pinned_area_layout.removeWidget(wrapper)
            wrapper.deleteLater()
        if not self.pinned_images:
            self._pinned_area.hide()
        else:
            QTimer.singleShot(10, self._update_pinned_overlay)
        # ── Sync to Android (only when host initiated the removal) ───────────
        if notify and _detached_path:
            _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
            if _ab and _ab.isVisible():
                _ab.notify_image_detached(_detached_path)

    def clear_pinned_images(self):
        """Remove ALL pinned image cards. Called on session switch or load."""
        for pi in list(self.pinned_images):
            wrapper = pi.get('row_wrapper')
            if wrapper:
                self._pinned_area_layout.removeWidget(wrapper)
                wrapper.deleteLater()
        self.pinned_images.clear()
        self._pinned_area.hide()

    def _handle_image_file_drop(self, path):
        """Prompt the user to attach an image file or insert its path as text."""
        from PyQt6.QtWidgets import QMessageBox
        import os
        file_name = os.path.basename(path)
        msg = QMessageBox(self)
        msg.setWindowTitle("Attach as Image?")
        msg.setText(f'Attach "{file_name}" as an image?')
        msg.setInformativeText(
            "Yes — send as image to the AI\n"
            "No — insert file path as text instead")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            self._show_image_preview(path)
        else:
            quoted = f'"{path}"' if self.should_quote_path(path) else path
            current = self.input_field.toPlainText()
            if current:
                self.input_field.text_input.setPlainText(current + "\n" + quoted)
            else:
                self.input_field.text_input.setPlainText(quoted)

    # ─────────────────────────────────────────────────────────────────────────

    def browse_for_file(self):
        """Alternative to drag & drop - supports multiple files"""
        from PyQt6.QtWidgets import QFileDialog

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select File(s)",
            "",
            "All Files (*.*)"
        )

        if not file_paths:
            return

        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        image_files = [self.clean_file_path(p) for p in file_paths
                       if any(p.lower().endswith(ext) for ext in valid_extensions)]
        non_images  = [self.clean_file_path(p) for p in file_paths
                       if not any(p.lower().endswith(ext) for ext in valid_extensions)]

        if image_files:
            if len(image_files) == 1:
                self._handle_image_file_drop(image_files[0])
            else:
                self._handle_multiple_image_files_dialog(image_files)

        for file_path in non_images:
            if self.should_quote_path(file_path):
                file_path = f'"{file_path}"'
            current_text = self.input_field.toPlainText()
            if current_text:
                self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
            else:
                self.input_field.text_input.setPlainText(file_path)

    def _handle_multiple_image_files_dialog(self, image_paths):
        """Show a checkbox dialog for multiple image files — attach as image or path."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QCheckBox, QPushButton, QLabel, QScrollArea, QWidget)

        dlg = QDialog(self)
        dlg.setWindowTitle("Attach Images")
        dlg.setMinimumWidth(480)
        dlg.setStyleSheet("""
            QDialog { background: #0D1117; color: #E6EDF3; }
            QLabel  { color: #E6EDF3; font-size: 12px; }
            QCheckBox { color: #E6EDF3; font-size: 12px; padding: 3px 0; }
            QPushButton {
                background: #21262D; border: 1px solid #30363D;
                border-radius: 6px; color: #E6EDF3;
                padding: 6px 14px; font-size: 12px;
            }
            QPushButton:hover { background: #30363D; }
            QPushButton#primaryBtn {
                background: #1F6FEB; border-color: #388BFD;
            }
            QPushButton#primaryBtn:hover { background: #388BFD; }
        """)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 14, 16, 14)

        lay.addWidget(QLabel(f"Found {len(image_paths)} image file(s). Choose how to attach:"))

        # Scroll area with checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(280)
        scroll.setStyleSheet("QScrollArea { border: 1px solid #21262D; border-radius: 6px; background: #161B22; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(10, 8, 10, 8)
        inner_lay.setSpacing(4)
        scroll.setWidget(inner)

        import os
        checkboxes = []
        for p in image_paths:
            cb = QCheckBox(os.path.basename(p))
            cb.setChecked(True)
            cb.setProperty("filepath", p)
            checkboxes.append(cb)
            inner_lay.addWidget(cb)

        lay.addWidget(scroll)

        # Select all / none row
        sel_row = QHBoxLayout()
        sel_all_btn  = QPushButton("Select All")
        sel_none_btn = QPushButton("Unselect All")
        sel_all_btn.clicked.connect(lambda: [cb.setChecked(True)  for cb in checkboxes])
        sel_none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in checkboxes])
        sel_row.addWidget(sel_all_btn)
        sel_row.addWidget(sel_none_btn)
        sel_row.addStretch()
        lay.addLayout(sel_row)

        lay.addWidget(QLabel("For checked files, attach as:"))

        # Action buttons
        btn_row = QHBoxLayout()
        img_btn  = QPushButton("🖼 Attach as Image(s)")
        img_btn.setObjectName("primaryBtn")
        path_btn = QPushButton("📄 Insert Path(s)")
        cancel_btn = QPushButton("Cancel")

        btn_row.addWidget(img_btn)
        btn_row.addWidget(path_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        result = {"action": None}

        img_btn.clicked.connect(lambda: (result.__setitem__("action", "image"),  dlg.accept()))
        path_btn.clicked.connect(lambda: (result.__setitem__("action", "path"),   dlg.accept()))
        cancel_btn.clicked.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = [cb.property("filepath") for cb in checkboxes if cb.isChecked()]
        if not selected:
            return

        if result["action"] == "image":
            for p in selected:
                self._show_image_preview(p)
        else:
            lines = []
            for p in selected:
                lines.append(f'"{p}"' if self.should_quote_path(p) else p)
            combined = "\n".join(lines)
            current = self.input_field.toPlainText()
            if current:
                self.input_field.text_input.setPlainText(current + "\n" + combined)
            else:
                self.input_field.text_input.setPlainText(combined)

    def set_input_enabled(self, enabled):
        """Enable/disable input"""
        self.input_field.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.input_field.setPlaceholderText("Send a message... (Shift+Enter for new line)")
            # Restore focus so the user can type immediately without clicking
            self.input_field.text_input.setFocus()
        else:
            self.input_field.setPlaceholderText("Processing Request... please wait")

    def set_input_placeholder(self, text):
        """Update placeholder text on the input field."""
        self.input_field.setPlaceholderText(text)

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

    def add_work_execution_widget(self, code: str, output: str):
        """Add a collapsible code+output block to the chat for work environment execution."""
        from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QWidget, QSizePolicy
        from PyQt6.QtGui import QFont

        _tc = self._t()
        outer = QFrame()
        outer.setStyleSheet(f"""
                    QFrame {{
                        background: {_tc['base']};
                        border: 1px solid {_tc['border']};
                        border-radius: 10px;
                        margin: 2px 0;
                    }}
                """)
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # Header row
        header = QFrame()
        header.setStyleSheet(f"""
                    QFrame {{
                        background: {_tc['surface']};
                        border-top-left-radius: 10px;
                        border-top-right-radius: 10px;
                        border-bottom: 1px solid {_tc['border']};
                    }}
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(14, 8, 12, 8)
        header_lay.setSpacing(8)

        lbl = QLabel("⚙  Code executed")
        lbl.setStyleSheet(
            f"color: {_tc['accent']}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        header_lay.addWidget(lbl)
        header_lay.addStretch()

        toggle_btn = QPushButton("▶  Show")
        toggle_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: 1px solid transparent;
                border-radius: 5px; padding: 3px 10px;
                font-size: 11px; color: #8B949E;
            }
            QPushButton:hover { background: rgba(88,166,255,0.12); color: #58A6FF; border-color: rgba(88,166,255,0.28); }
        """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header)

        # Body (hidden by default)
        body = QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        body.hide()

        mono_font = QFont('Consolas', 10)
        if not mono_font.exactMatch():
            mono_font = QFont('Courier New', 10)

        # Code section
        if code.strip():
            code_lbl = QLabel("CODE")
            code_lbl.setStyleSheet("color: #8B949E; font-size: 10px; font-weight: 700; padding: 6px 14px 2px 14px; background: transparent; border: none;")
            body_lay.addWidget(code_lbl)
            from PyQt6.QtWidgets import QTextEdit
            code_edit = QTextEdit()
            code_edit.setPlainText(code)
            code_edit.setReadOnly(True)
            code_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            code_edit.setFont(mono_font)
            code_edit.setStyleSheet(f"QTextEdit {{ background: {_tc['base']}; color: #E6EDF3; border: none; padding: 8px 14px; }}")
            code_edit.setFrameShape(QTextEdit.Shape.NoFrame)
            code_edit.setFixedHeight(min(max(len(code.splitlines()) * 17 + 24, 60), 300))
            body_lay.addWidget(code_edit)

        # Output section
        if output.strip():
            sep = QFrame()
            sep.setStyleSheet("background: rgba(88,166,255,0.10); border: none;")
            sep.setFixedHeight(1)
            body_lay.addWidget(sep)
            out_lbl = QLabel("STDOUT / STDERR")
            out_lbl.setStyleSheet("color: #8B949E; font-size: 10px; font-weight: 700; padding: 6px 14px 2px 14px; background: transparent; border: none;")
            body_lay.addWidget(out_lbl)
            from PyQt6.QtWidgets import QTextEdit
            out_edit = QTextEdit()
            out_edit.setPlainText(output)
            out_edit.setReadOnly(True)
            out_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            out_edit.setFont(mono_font)
            out_edit.setStyleSheet(f"QTextEdit {{ background: {_tc['deep']}; color: #8FBC8F; border: none; padding: 8px 14px; border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}")
            out_edit.setFrameShape(QTextEdit.Shape.NoFrame)
            out_edit.setFixedHeight(min(max(len(output.splitlines()) * 17 + 24, 60), 200))
            body_lay.addWidget(out_edit)

        outer_lay.addWidget(body)

        def _toggle():
            if body.isHidden():
                body.show()
                toggle_btn.setText("▼  Hide")
            else:
                body.hide()
                toggle_btn.setText("▶  Show")

        toggle_btn.clicked.connect(_toggle)

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, outer)
        self.scroll_to_bottom()

    def add_code_execution_note(self, code: str, output: str, save_to_history: bool = True, annotation: str = None):
        """Compact inline code-execution note — styled like a system message.
        Saves itself to conversation_history as a ui_event so it persists across reloads."""
        from PyQt6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QTextEdit
        from PyQt6.QtGui import QFont

        _tc = self._t()

        # ── Outer wrapper ─────────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet("QFrame { background-color: transparent; padding: 4px 16px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header row (always visible) ───────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
                    QFrame {{
                        background-color: {_tc['elevated']};
                        border: 1px solid {_tc['border']};
                        border-radius: 8px;
                    }}
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 6, 10, 6)
        header_lay.setSpacing(8)

        icon_lbl = QLabel("⚙")
        icon_lbl.setStyleSheet(
            f"color: {_tc['accent']}; font-size: 11px; background: transparent; border: none;")
        icon_lbl.setFixedWidth(14)
        header_lay.addWidget(icon_lbl)

        # Use the Working: annotation as the label if available
        if annotation is None:
            try:
                annotation = self.controller.ai.tool_manager.last_work_annotation or ""
            except Exception:
                annotation = ""
        header_label = f"{annotation}" if annotation else "Code executed"
        first_line = (code.strip().splitlines()[0] if code.strip() else "no code")
        preview = first_line[:60] + ("…" if len(first_line) > 60 else "")
        summary_lbl = QLabel(
            f"<span style='color:{_tc['accent']};font-size:11px;'>{header_label}</span>"
            f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
            f"<span style='font-family:monospace;font-size:10px;color:#8B949E;'>{preview}</span>")
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)

        toggle_btn = QPushButton("▶ Show")
        toggle_btn.setFixedSize(58, 20)
        toggle_btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; border: 1px solid {_tc['border']};
                        border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
                    }}
                    QPushButton:hover {{ color: {_tc['accent']}; border-color: {_tc['accent']}; }}
                """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)
        outer_lay.addWidget(header)

        # ── Expandable detail (hidden by default) ─────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(0, 4, 0, 0)
        detail_lay.setSpacing(4)

        mono = QFont('Consolas', 9)
        if not mono.exactMatch():
            mono = QFont('Courier New', 9)

        if code.strip():
            from PyQt6.QtWidgets import QTextEdit as _QTE
            code_edit = _QTE()
            code_edit.setPlainText(code.strip())
            code_edit.setReadOnly(True)
            code_edit.setLineWrapMode(_QTE.LineWrapMode.NoWrap)
            code_edit.setFont(mono)
            code_edit.setStyleSheet(
                f"QTextEdit {{ background: {_tc['base']}; color: #E6EDF3; "
                f"border: 1px solid {_tc['border']}; border-radius: 6px; padding: 6px 10px; }}")
            code_edit.setFrameShape(_QTE.Shape.NoFrame)
            code_edit.setFixedHeight(min(max(len(code.strip().splitlines()) * 16 + 20, 50), 200))
            detail_lay.addWidget(code_edit)

        if output.strip():
            from PyQt6.QtWidgets import QTextEdit as _QTE2
            out_edit = _QTE2()
            out_edit.setPlainText(output.strip())
            out_edit.setReadOnly(True)
            out_edit.setLineWrapMode(_QTE2.LineWrapMode.WidgetWidth)
            out_edit.setFont(mono)
            out_edit.setStyleSheet(
                f"QTextEdit {{ background: {_tc['deep']}; color: #8FBC8F; "
                f"border: 1px solid {_tc['border']}; border-radius: 6px; padding: 6px 10px; }}")
            out_edit.setFrameShape(_QTE2.Shape.NoFrame)
            out_edit.setFixedHeight(min(max(len(output.strip().splitlines()) * 16 + 20, 50), 150))
            detail_lay.addWidget(out_edit)

        outer_lay.addWidget(detail)

        def _toggle():
            if detail.isHidden():
                detail.show()
                toggle_btn.setText("▼ Hide")
            else:
                detail.hide()
                toggle_btn.setText("▶ Show")

        toggle_btn.clicked.connect(_toggle)

        # ── Insert before thinking bubble (or before input if no bubble) ───────
        if self._thinking_bubble_widget is not None:
            idx = self.chat_layout.indexOf(self._thinking_bubble_widget)
            self.chat_layout.insertWidget(idx, message_widget)
        else:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track in message_widgets ──────────────────────────────────────────
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'code_exec',
            'content_wrapper': header,
        })

        # ── Persist to conversation_history so session save/load works ────────
        if save_to_history:
            try:
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    'content': '⚙ Code executed',
                    '_code': code,
                    '_output': output,
                    '_annotation': annotation,
                })
            except Exception:
                pass

    def add_memory_context_widget(self, context_id: str, memories: list,
                                   save_to_history: bool = True):
        """Render a memory-context card with a Detach button.

        Visually distinct from code execution notes — uses a brain icon and
        amber/gold accent so users know it's memory, not code.
        When Detach is clicked the widget animates out AND the corresponding
        ui_event is removed from conversation_history via the controller.

        Parameters
        ----------
        context_id  : short UUID-derived string stored on the ui_event
        memories    : list of raw memory strings shown in the card
        save_to_history : False when replaying from a loaded session (entry
                          already exists in conversation_history)
        """
        if not context_id or not isinstance(context_id, str):
            return

        from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout,
                                     QPushButton, QLabel)

        _tc = self._t()

        # Follow the active theme
        _mem_accent = _tc['accent']
        _mem_bg = _tc['elevated']
        _mem_border = _tc['border']

        # ── Outer wrapper ──────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setStyleSheet(
            "QFrame { background-color: transparent; padding: 4px 16px; }")
        outer_lay = QVBoxLayout(message_widget)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)

        # ── Header row (always visible) ────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
                    QFrame {{
                        background-color: {_mem_bg};
                        border: 1px solid {_mem_border};
                        border-radius: 8px;
                    }}
                """)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 6, 10, 6)
        header_lay.setSpacing(8)

        icon_lbl = QLabel("🧠")
        icon_lbl.setStyleSheet(
            "font-size: 13px; background: transparent; border: none;")
        icon_lbl.setFixedWidth(18)
        header_lay.addWidget(icon_lbl)

        # Preview: first memory title, trimmed
        preview_text = memories[0][:72] + ("…" if memories and len(memories[0]) > 72 else "") \
            if memories else "Memory recalled"
        count_label = f" +{len(memories) - 1} more" if len(memories) > 1 else ""

        summary_lbl = QLabel(
            f"<span style='color:{_mem_accent};font-size:11px;font-weight:600;'>"
            f"Memory recalled</span>"
            f"&nbsp;&nbsp;<span style='color:#5F6368;'>·</span>&nbsp;&nbsp;"
            f"<span style='font-size:10px;color:#8B949E;'>{preview_text}</span>"
            f"<span style='font-size:10px;color:{_mem_accent};'>{count_label}</span>")
        summary_lbl.setTextFormat(Qt.TextFormat.RichText)
        summary_lbl.setStyleSheet("background: transparent; border: none;")
        header_lay.addWidget(summary_lbl, stretch=1)

        # ── Show / Hide toggle ─────────────────────────────────────────────
        toggle_btn = QPushButton("▶ Show")
        toggle_btn.setFixedSize(58, 20)
        toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid {_tc['border']};
                border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
            }}
            QPushButton:hover {{ color: {_mem_accent}; border-color: {_mem_accent}; }}
        """)
        toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(toggle_btn)

        # ── Detach button ──────────────────────────────────────────────────
        detach_btn = QPushButton("⊗ Detach")
        detach_btn.setFixedSize(62, 20)
        detach_btn.setToolTip(
            "Remove this memory from the conversation context.\n"
            "The AI will no longer see it in this session.")
        detach_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px solid #6B3030;
                border-radius: 4px; font-size: 10px; color: #8B6060; padding: 0 6px;
            }}
            QPushButton:hover {{ color: #E06060; border-color: #E06060; }}
        """)
        detach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        header_lay.addWidget(detach_btn)

        outer_lay.addWidget(header)

        # ── Expandable memory list ─────────────────────────────────────────
        detail = QFrame()
        detail.setStyleSheet("background: transparent; border: none;")
        detail.hide()
        detail_lay = QVBoxLayout(detail)
        detail_lay.setContentsMargins(4, 4, 4, 0)
        detail_lay.setSpacing(4)

        for mem_text in memories:
            row = QFrame()
            row.setStyleSheet(f"""
                QFrame {{
                    background: {_mem_bg};
                    border: 1px solid {_mem_accent}33;
                    border-radius: 6px;
                    padding: 0px;
                }}
            """)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(10, 6, 10, 6)
            lbl = QLabel(mem_text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"font-size: 11px; color: {_mem_accent}; background: transparent; border: none;")
            row_lay.addWidget(lbl)
            detail_lay.addWidget(row)

        outer_lay.addWidget(detail)

        # ── Toggle logic ───────────────────────────────────────────────────
        def _toggle():
            if detail.isHidden():
                detail.show()
                toggle_btn.setText("▼ Hide")
            else:
                detail.hide()
                toggle_btn.setText("▶ Show")

        toggle_btn.clicked.connect(_toggle)

        # ── Detach logic ───────────────────────────────────────────────────
        def _detach(_cid=context_id, _w=message_widget):
            try:
                self.controller.detach_memory_context(_cid)
            except Exception as e:
                print(f"[ChatWindow._detach] Error calling detach_memory_context: {e}")
            # Remove widget from message_widgets tracking list
            self.message_widgets[:] = [
                mw for mw in self.message_widgets if mw.get('widget') is not _w
            ]
            # Animate out and destroy
            self._animate_message_out(_w, callback=_w.deleteLater)

        detach_btn.clicked.connect(lambda: _detach())

        # ── Insert before the trailing spacer ─────────────────────────────
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(
            message_widget,
            on_settled=lambda: self.scroll_to_widget(message_widget))

        # ── Track in message_widgets ───────────────────────────────────────
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'memory_context',
            'context_id': context_id,
            'content_wrapper': header,
            '_toggle_btn': toggle_btn,
        })

        # ── Persist to history (only on first insertion, not on reload) ────
        if save_to_history:
            try:
                import uuid as _uuid
                self.controller.ai.conversation_history.append({
                    'role': 'ui_event',
                    '_type': 'memory_context',
                    '_memory_context_id': context_id,
                    'content': '',          # content is stored per-memory
                    '_memories_preview': memories,
                })
            except Exception:
                pass

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
        self.status_label.setText(f"{dots}")

    def interrupt_response(self):
        """Interrupt current AI response and restore message to input"""
        if not self.controller.is_processing and not self.controller.ai.tool_manager.in_work_mode and not self.controller.ai.tool_manager.work_code_running:
            return

        # If in work mode, only show dialog when code is actively executing
        if self.controller.ai.tool_manager.in_work_mode or self.controller.ai.tool_manager.work_code_running:
            if not self.controller.ai.tool_manager.work_code_running:
                # Button is disabled / no active code — fall through to normal cancel
                pass
            else:
                from ui.timeout_dialog import WorkmodeInterruptDialog
                from PyQt6.QtWidgets import QDialog
                from PyQt6.QtCore import QTimer

                dialog = WorkmodeInterruptDialog(self)

                # Auto-dismiss if code finishes while dialog is open
                _poll = QTimer()
                _poll.setInterval(200)
                def _check():
                    if not self.controller.ai.tool_manager.work_code_running:
                        _poll.stop()
                        dialog.reject()
                _poll.timeout.connect(_check)
                _poll.start()

                try:
                    accepted = dialog.exec() == QDialog.DialogCode.Accepted
                finally:
                    _poll.stop()

                if accepted:
                    reason = dialog.reason_text

                    error_msg = "ERROR:\nUser interrupted workmode. You must exit immediately."
                    if reason:
                        error_msg += f"\nReason: {reason}"
                    else:
                        error_msg += "\nNo specified reason. Just exit."

                    self.controller.ai.tool_manager.last_work_output = error_msg

                    # Terminate any running worker
                    if (hasattr(self.controller, 'current_worker')
                            and self.controller.current_worker
                            and self.controller.current_worker.isRunning()):
                        self.controller.current_worker.terminate()
                        self.controller.current_worker.wait(1000)
                        self.controller.is_processing = False

                    self.controller.work_mode_timer.stop()

                    # Show execution note for the interrupted code (like timeout does)
                    exec_code = self.controller.ai.tool_manager.last_work_code
                    if exec_code:
                        self.add_code_execution_note(exec_code, error_msg)

                    QTimer.singleShot(100, self.controller.auto_continue_work_mode)

                    self.interrupt_btn.hide()
                # else: dialog auto-dismissed or cancelled → workmode continues
            return

        success = self.controller.interrupt_request()

        if success:
            if self.last_user_message_widget:
                self.chat_layout.removeWidget(self.last_user_message_widget)
                self.last_user_message_widget.deleteLater()
                self.last_user_message_widget = None

            if self.last_sent_message:
                current_text = self.input_field.toPlainText()
                # Return old message for easy editing
                if current_text:
                    self.input_field.text_input.setPlainText(self.last_sent_message + "\n\n" + current_text)
                else:
                    self.input_field.text_input.setPlainText(self.last_sent_message)
                self.last_sent_message = None

            self.interrupt_btn.hide()
            self.send_btn.show()

            self.hide_thinking()
            self.hide_thinking_bubble()

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
        self.interrupt_btn.setEnabled(
            self.controller.ai.tool_manager.work_code_running
            if self.controller.ai.tool_manager.in_work_mode else True
        )
        self.interrupt_btn.setToolTip(
            "Interrupt work" if self.controller.ai.tool_manager.in_work_mode
            else "Cancel AI response"
        )
        self.show_thinking_bubble()
        # Show work banner if already in work mode
        if hasattr(self, '_work_banner') and self.controller.ai.tool_manager.in_work_mode:
            if not self._work_banner.text():
                self._work_banner.setText("⚙ Working…")
            self._work_banner.show()

    def hide_thinking(self):
        """Hide thinking animation"""
        self.stop_thinking_animation()
        self.thinking_label_shown = False
        self.set_input_enabled(True)
        self.interrupt_btn.hide()
        self.send_btn.show()
        self.hide_thinking_bubble()
        # Clear and hide the work mode banner
        if hasattr(self, '_work_banner'):
            self._work_banner.setText("")
            self._work_banner.hide()

    def show_thinking_bubble(self):
        """Show an animated three-dot typing indicator as an AI chat bubble."""
        if self._thinking_bubble_widget is not None:
            return  # Already showing

        bubble = QFrame()
        bubble.setStyleSheet("""
            QFrame {
                background-color: transparent;
                padding: 12px 16px;
            }
        """)

        layout = QHBoxLayout(bubble)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Avatar — left side, same as AI messages
        avatar = self._make_chat_avatar('bot', getattr(self, '_bot_avatar_size', 32))
        layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        # Dot bubble
        _tc = self._t()
        content = QFrame()
        content.setStyleSheet(f"""
                    QFrame {{
                        background-color: {_tc['elevated']};
                        border: 1px solid {_tc['border']};
                        border-radius: 12px;
                    }}
                """)
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 12)

        dot_label = QLabel("● ○ ○")
        dot_label.setStyleSheet(
            "color: #8B949E; font-size: 15px; background: transparent; letter-spacing: 6px;"
        )
        content_layout.addWidget(dot_label)

        layout.addWidget(content)
        layout.addStretch()

        self._thinking_bubble_widget = bubble
        self._thinking_bubble_label = dot_label
        self._thinking_bubble_dots = 0

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self.scroll_to_bottom()

        # Animate
        self._thinking_bubble_timer = QTimer()
        self._thinking_bubble_timer.timeout.connect(self._update_thinking_bubble)
        self._thinking_bubble_timer.start(420)

    def _update_thinking_bubble(self):
        """Cycle the three dots animation inside the typing bubble."""
        if self._thinking_bubble_label is None:
            return
        self._thinking_bubble_dots = (self._thinking_bubble_dots + 1) % 4
        states = ["● ○ ○", "● ● ○", "● ● ●", "○ ● ●"]
        try:
            self._thinking_bubble_label.setText(states[self._thinking_bubble_dots])
        except RuntimeError:
            pass

    def hide_thinking_bubble(self):
        """Remove the animated three-dot typing indicator bubble."""
        if self._thinking_bubble_timer is not None:
            self._thinking_bubble_timer.stop()
            self._thinking_bubble_timer = None

        if self._thinking_bubble_widget is not None:
            widget = self._thinking_bubble_widget
            self._thinking_bubble_widget = None
            self._thinking_bubble_label = None
            try:
                self.chat_layout.removeWidget(widget)
                widget.deleteLater()
            except RuntimeError:
                pass

    def resizeEvent(self, event):
        """Handle window resize."""
        super().resizeEvent(event)  # handles mask, resize handles, save timer

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

        self._update_pinned_overlay()

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

    def eventFilter(self, obj, event):
        """Handle resize handle events and smooth scroll viewport events."""
        from PyQt6.QtCore import QEvent

        # ── Reposition pinned overlay when input_container height changes ──
        if hasattr(self, 'input_container') and obj is self.input_container:
            if event.type() == QEvent.Type.Resize:
                self._update_pinned_overlay()

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
                # Ctrl+Scroll → zoom in / out
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self.zoom_in()
                    else:
                        self.zoom_out()
                    return True

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

        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        """Handle drag enter"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle file drop — supports multiple files"""
        raw_files = [u.toLocalFile() for u in event.mimeData().urls()]
        if not raw_files:
            return

        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        image_files = [self.clean_file_path(p) for p in raw_files
                       if any(p.lower().endswith(ext) for ext in valid_extensions)]
        non_images  = [self.clean_file_path(p) for p in raw_files
                       if not any(p.lower().endswith(ext) for ext in valid_extensions)]

        if image_files:
            if len(image_files) == 1:
                self._handle_image_file_drop(image_files[0])
            else:
                self._handle_multiple_image_files_dialog(image_files)

        for file_path in non_images:
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
        """Handle paste of file paths and zoom shortcuts"""
        # Ctrl++ / Ctrl+= → zoom in
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.zoom_in()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Minus:
                self.zoom_out()
                event.accept()
                return
            if event.key() == Qt.Key.Key_0:
                self.chat_zoom = 1.0
                self._apply_zoom_all()
                self.save_config()
                event.accept()
                return

        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            clipboard = QApplication.clipboard()
            text = clipboard.text().strip()

            # ── Multi-path support: clipboard may contain multiple lines ─────
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            existing_paths = [self.clean_file_path(l) for l in lines if os.path.exists(self.clean_file_path(l))]

            if len(existing_paths) > 1:
                image_files = [p for p in existing_paths if any(p.lower().endswith(e) for e in valid_extensions)]
                non_images = [p for p in existing_paths if p not in image_files]
                if image_files:
                    self._handle_multiple_image_files_dialog(image_files)
                for file_path in non_images:
                    if self.should_quote_path(file_path):
                        file_path = f'"{file_path}"'
                    self.input_field.text_input.insertPlainText(file_path + "\n")
                event.accept()
                return

            # ── Single path fallback ──────────────────────────────────────────
            cleaned_path = self.clean_file_path(text)

            if os.path.exists(cleaned_path):
                if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                    self._handle_image_file_drop(cleaned_path)
                    event.accept()
                    return

                if self.should_quote_path(cleaned_path):
                    cleaned_path = f'"{cleaned_path}"'

                self.input_field.text_input.insertPlainText(cleaned_path)
                event.accept()
                return

        super().keyPressEvent(event)

    # ═══════════════════════════════════════════════════════════════════════════
    # ZOOM METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _get_msg_font_size(self) -> int:
        """Return the current message text font size, scaled by chat_zoom."""
        return max(9, int(13 * getattr(self, 'chat_zoom', 1.0)))

    def zoom_in(self):
        """Increase message font size one step and persist."""
        if getattr(self, 'chat_zoom', 1.0) < 1.8:
            self.chat_zoom = round(self.chat_zoom + 0.1, 1)
            self._apply_zoom_all()
            self.save_config()

    def zoom_out(self):
        """Decrease message font size one step and persist."""
        if getattr(self, 'chat_zoom', 1.0) > 0.6:
            self.chat_zoom = round(self.chat_zoom - 0.1, 1)
            self._apply_zoom_all()
            self.save_config()

    def _apply_zoom_all(self):
        """Apply the current zoom level to all existing message text labels."""
        fsize = self._get_msg_font_size()
        for md in self.message_widgets:
            role = md.get('role', '')
            text_color = '#E8EAED' if role == 'user' else '#BDC1C6'
            style = (
                f"QLabel {{ color: {text_color}; font-size: {fsize}px; "
                f"line-height: 1.5; background: transparent; border: none; }}"
            )
            for lbl in md.get('text_labels', []):
                try:
                    lbl.setStyleSheet(style)
                except RuntimeError:
                    pass
            # Also update main_container_widget max width for user bubbles
            if role == 'user':
                mcw = md.get('main_container_widget')
                if mcw:
                    try:
                        mcw.setMaximumWidth(int(600 * self.chat_zoom))
                    except RuntimeError:
                        pass

    # ═══════════════════════════════════════════════════════════════════════════
    # GLASS BUBBLE HELPER
    # ═══════════════════════════════════════════════════════════════════════════

    def _apply_glass_to_bubbles(self, enabled: bool):
        """Update all existing message bubble backgrounds for glass / solid mode."""
        for md in self.message_widgets:
            cw = md.get('content_wrapper')
            if cw is None:
                continue
            role = md.get('role', '')
            try:
                if enabled:
                    bg = 'rgba(37, 37, 37, 0.55)' if role == 'user' else 'rgba(42, 42, 42, 0.55)'
                    cw.setStyleSheet(f"""
                        QFrame {{
                            background-color: {bg};
                            border: 1px solid rgba(60, 60, 60, 0.4);
                            border-radius: 12px;
                        }}
                    """)
                else:
                    bg = '#1E2228' if role == 'user' else '#21262D'
                    cw.setStyleSheet(f"""
                        QFrame {{
                            background-color: {bg};
                            border: 1px solid #30363D;
                            border-radius: 12px;
                        }}
                    """)
            except RuntimeError:
                pass

    # ═══════════════════════════════════════════════════════════════════════════
    # THEME APPLICATION
    # ═══════════════════════════════════════════════════════════════════════════

    _THEMES = {
        'obsidian_blue': {
            'base':    '#0D1117', 'surface': '#161B22', 'elevated': '#21262D',
            'border':  '#30363D', 'accent':  '#58A6FF', 'deep': '#0D1117',
            'input_card': '#1C2128', 'input_card_border': '#2D333B',
        },
        'onyx': {
            'base':    '#18181B', 'surface': '#1C1C1F', 'elevated': '#27272A',
            'border':  '#3F3F46', 'accent':  '#6366F1', 'deep': '#101013',
            'input_card': '#1C1C20', 'input_card_border': '#3A3A40',
        },
        'carbon': {
            'base':    '#111214', 'surface': '#1E1F22', 'elevated': '#2B2D31',
            'border':  '#3B3D43', 'accent':  '#5865F2', 'deep': '#0B0C0E',
            'input_card': '#1A1B1E', 'input_card_border': '#35373D',
        },
        'midnight_rose': {
            'base':    '#120F1A', 'surface': '#1D1825', 'elevated': '#2A2436',
            'border':  '#3E3556', 'accent':  '#A78BFA', 'deep': '#0C0910',
            'input_card': '#1A1625', 'input_card_border': '#382E50',
        },
        'emerald': {
            'base':    '#0D1210', 'surface': '#131A15', 'elevated': '#1C2B1F',
            'border':  '#274D30', 'accent':  '#3FB950', 'deep': '#090E0B',
            'input_card': '#162019', 'input_card_border': '#243D28',
        },
        'copper': {
            'base':    '#110D09', 'surface': '#1A1310', 'elevated': '#261D17',
            'border':  '#4A3020', 'accent':  '#E8834A', 'deep': '#0D0A07',
            'input_card': '#201812', 'input_card_border': '#3D2818',
        },
        'crimson': {
            'base':    '#120A0A', 'surface': '#1C1010', 'elevated': '#2A1515',
            'border':  '#4D1F1F', 'accent':  '#FF4C4C', 'deep': '#0D0707',
            'input_card': '#201212', 'input_card_border': '#3D1A1A',
        },
        'arctic': {
            'base':    '#0A0E12', 'surface': '#111620', 'elevated': '#192030',
            'border':  '#243348', 'accent':  '#67E8F9', 'deep': '#070B0F',
            'input_card': '#141D2C', 'input_card_border': '#1F2E42',
        },
        'golden': {
            'base':    '#0F0D08', 'surface': '#19160A', 'elevated': '#252010',
            'border':  '#473D18', 'accent':  '#F5C518', 'deep': '#0A0905',
            'input_card': '#1E1A0D', 'input_card_border': '#3A3214',
        },
        'slate': {
            'base':    '#0C0E12', 'surface': '#141820', 'elevated': '#1E2330',
            'border':  '#2C3444', 'accent':  '#94A3B8', 'deep': '#090B0F',
            'input_card': '#181D28', 'input_card_border': '#252D3E',
        },
        # ── Monochrome dark series ────────────────────────────────────────────
        'void': {
            'base':    '#000000', 'surface': '#090909', 'elevated': '#111111',
            'border':  '#1c1c1c', 'accent':  '#555555', 'deep': '#000000',
            'input_card': '#0d0d0d', 'input_card_border': '#1c1c1c',
        },
        'mono_obsidian': {
            'base':    '#0a0a0a', 'surface': '#101010', 'elevated': '#171717',
            'border':  '#222222', 'accent':  '#666666', 'deep': '#060606',
            'input_card': '#131313', 'input_card_border': '#222222',
        },
        'mono_charcoal': {
            'base':    '#0e0e10', 'surface': '#141416', 'elevated': '#1c1c1f',
            'border':  '#252528', 'accent':  '#606063', 'deep': '#0b0b0d',
            'input_card': '#181819', 'input_card_border': '#252528',
        },
        'ember': {
            'base':    '#0f0d0b', 'surface': '#161310', 'elevated': '#1e1a16',
            'border':  '#2a2520', 'accent':  '#6e665c', 'deep': '#0c0a08',
            'input_card': '#1a1714', 'input_card_border': '#2a2520',
        },
    }

    def _t(self) -> dict:
        """Return the current live theme dict — always in sync with the last apply_theme call."""
        key = getattr(self, '_current_theme_key', 'obsidian_blue')
        return self._THEMES.get(key, self._THEMES['obsidian_blue'])

    def apply_theme(self, theme_key: str):
        """Apply a named colour theme to all major structural surfaces."""
        t = self._THEMES.get(theme_key, self._THEMES['obsidian_blue'])
        self._current_theme_key = theme_key   # remember for apply_glass_background
        try:
            # Container
            self.container.setStyleSheet(f"""
                QWidget#container {{
                    background-color: {t['surface']};
                    border-radius: 12px;
                }}
                QWidget {{
                    color: #E8EAED;
                    font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                }}
            """)
            # Chat body
            self.chat_widget.setStyleSheet(
                f"QWidget {{ background-color: {t['surface']}; }}"
            )
            # Scroll area
            self.chat_scroll_area.setStyleSheet(f"""
                QScrollArea {{ border: none; background-color: {t['surface']}; }}
                QScrollBar:vertical {{
                    background: transparent; width: 12px; margin: 0;
                }}
                QScrollBar::handle:vertical {{
                    background: rgba(168,199,250,0.3); border-radius:6px; min-height:30px; margin:2px;
                }}
                QScrollBar::handle:vertical:hover  {{ background: rgba(168,199,250,0.5); }}
                QScrollBar::handle:vertical:pressed {{ background: rgba(168,199,250,0.7); }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """)
            # Header
            self.header_bar.setStyleSheet(f"""
                QFrame {{
                    background-color: {t['surface']};
                    border-bottom: 1px solid {t['border']};
                }}
                QLabel {{ background-color: transparent; }}
            """)
            # Status label
            self.status_label.setStyleSheet(f"""
                QLabel#statusLabel {{
                    color: #9AA0A6; font-style: italic; font-size: 11px;
                    padding: 5px 14px;
                    background-color: {t['deep']};
                    border-top: 1px solid {t['border']};
                }}
            """)
            # Work mode banner — recolour with active theme
            if hasattr(self, '_work_banner'):
                self._work_banner.setStyleSheet(f"""
                                QLabel#workBanner {{
                                    background-color: {t['elevated']};
                                    border-top: 1px solid {t['border']};
                                    border-bottom: 1px solid {t['border']};
                                    color: {t['accent']};
                                    font-size: 11px;
                                    font-style: italic;
                                    padding: 6px 14px;
                                }}
                            """)
            # Input container
            self.input_container.setStyleSheet(f"""
                            QFrame#inputContainer {{
                                background-color: {t['deep']};
                                border-top: 1px solid {t['border']};
                            }}
                        """)
            # Sidebar
            if hasattr(self, 'sidebar'):
                self.sidebar.setStyleSheet(f"""
                    QFrame#sidebar {{
                        background-color: {t['base']};
                        border-right: 1px solid {t['border']};
                        border-top-left-radius: 12px;
                        border-bottom-left-radius: 12px;
                    }}
                """)
                from PyQt6.QtWidgets import QWidget as _QW, QFrame as _QF2, QLineEdit as _QLE
                for w in self.sidebar.findChildren(_QW):
                    if w.objectName() == "sidebarContent":
                        w.setStyleSheet(f"QWidget#sidebarContent {{ background-color: {t['base']}; }}")
                    elif w.objectName() == "sidebarHero":
                        w.setStyleSheet(f"QFrame#sidebarHero {{ background-color: {t['base']}; border-bottom: 1px solid {t['border']}; }}")
                for inp in self.sidebar.findChildren(_QLE):
                    try:
                        inp.setStyleSheet(f"""
                            QLineEdit {{ background: {t['elevated']}; border: 1px solid {t['border']};
                                border-radius: 6px; padding: 0 8px; font-size: 10px; color: #8B949E; }}
                            QLineEdit:focus {{ border-color: rgba(88,166,255,0.45); color: #E6EDF3; }}
                        """)
                    except RuntimeError:
                        pass
            # Input card — use stored reference first, then fallback search
            from PyQt6.QtWidgets import QFrame as _QF
            _ic = getattr(self, '_input_card_ref', None)
            if _ic is None:
                for child in self.container.findChildren(_QF):
                    if child.objectName() == "inputCard":
                        self._input_card_ref = child
                        _ic = child
                        break
            if _ic:
                try:
                    _ic.setStyleSheet(f"""
                        QFrame#inputCard {{
                            background-color: {t['input_card']};
                            border: 1px solid {t['input_card_border']};
                            border-radius: 18px;
                        }}
                    """)
                except RuntimeError:
                    self._input_card_ref = None

            # Message bubbles
            for md in self.message_widgets:
                role = md.get('role', '')
                cw = md.get('content_wrapper')
                if cw:
                    try:
                        if role == 'system':
                            # system messages use a QLabel as the styled surface
                            cw.setStyleSheet(f"""
                                QLabel {{
                                    background-color: {t['elevated']};
                                    border: 1px solid {t['border']};
                                    border-radius: 8px;
                                    padding: 10px 16px;
                                    color: #9AA0A6;
                                    font-size: 11px;
                                    line-height: 1.4;
                                }}
                            """)
                        elif role == 'memory_context':
                            try:
                                cw.setStyleSheet(f"""
                                                                QFrame {{
                                                                    background-color: {t['elevated']};
                                                                    border: 1px solid {t['border']};
                                                                    border-radius: 8px;
                                                                }}
                                                            """)
                                tb = md.get('_toggle_btn')
                                if tb:
                                    tb.setStyleSheet(f"""
                                                                    QPushButton {{
                                                                        background: transparent; border: 1px solid {t['border']};
                                                                        border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
                                                                    }}
                                                                    QPushButton:hover {{ color: {t['accent']}; border-color: {t['accent']}; }}
                                                                """)
                            except RuntimeError:
                                pass
                        else:
                            bg = t['elevated'] if role != 'user' else t['surface']
                            cw.setStyleSheet(f"""
                                                            QFrame {{
                                                                background-color: {bg};
                                                                border: 1px solid {t['border']};
                                                                border-radius: 12px;
                                                            }}
                                                        """)
                    except RuntimeError:
                        pass
        except Exception as e:
            print(f"[ChatWindow.apply_theme] Error: {e}")

    def apply_glass_background(self, enabled: bool, opacity: float = 0.75):
        """Apply or remove the glass (frosted-translucent) theme.

        Glass mode zones:
          • Main container       — fully transparent (desktop shows through)
          • Chat messages area   — semi-transparent dark backdrop (opacity from slider)
          • Scroll area          — transparent with a visible scrollbar track
          • Header bar           — matches chat body opacity (uniform look)
          • Status/thinking bar  — semi-opaque dark grey so text always readable
          • Input container      — semi-opaque dark grey frosted panel (VISIBLE, not removed)
          • inputCard pill        — solid dark, unchanged
          • Message bubbles       — semi-transparent to blend with glass body
          • Sidebar              — UNTOUCHED, always solid

        Dark (non-glass) mode restores every zone to its solid colour.
        All colours are neutral dark greys — no tints.
        """
        try:
            op = max(0.15, min(0.95, float(opacity)))
            # backdrop for messages area — darker as opacity goes up
            base = int(op * 28)
            bg_rgba = f"rgba({base},{base},{base},{op:.2f})"

            # Track state so new bubbles created while glass is active use the right style
            self._glass_enabled = enabled
            self._glass_opacity = opacity

            # Scrollbar: visible track in glass mode, solid in dark mode
            _scrollbar_glass = """
                QScrollBar:vertical {
                    background: rgba(255,255,255,0.06);
                    width: 12px; margin: 0; border-radius: 6px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(168,199,250,0.35);
                    border-radius: 6px; min-height: 30px; margin: 2px;
                }
                QScrollBar::handle:vertical:hover  { background: rgba(168,199,250,0.55); }
                QScrollBar::handle:vertical:pressed { background: rgba(168,199,250,0.75); }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical { background: transparent; }
            """
            _scrollbar_solid = """
                QScrollBar:vertical {
                    background: transparent; width: 12px; margin: 0;
                }
                QScrollBar::handle:vertical {
                    background: rgba(168,199,250,0.3);
                    border-radius: 6px; min-height: 30px; margin: 2px;
                }
                QScrollBar::handle:vertical:hover  { background: rgba(168,199,250,0.5); }
                QScrollBar::handle:vertical:pressed { background: rgba(168,199,250,0.7); }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical { background: transparent; }
            """

            if enabled:
                # ── outer container: fully transparent ────────────────────────
                self.container.setStyleSheet("""
                    QWidget#container {
                        background-color: transparent;
                        border-radius: 12px;
                    }
                    QWidget {
                        color: #E8EAED;
                        font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                    }
                """)

                # ── chat messages backdrop ─────────────────────────────────────
                self.chat_widget.setStyleSheet(
                    f"QWidget {{ background-color: {bg_rgba}; }}"
                )
                self.chat_scroll_area.setStyleSheet(
                    "QScrollArea { border: none; background-color: transparent; }"
                    + _scrollbar_glass
                )

                # ── header: uniform with chat body (same bg_rgba) ─────────────
                self.header_bar.setStyleSheet(f"""
                    QFrame {{
                        background-color: {bg_rgba};
                        border-bottom: 1px solid rgba(50, 50, 50, 0.5);
                    }}
                    QLabel {{
                        background-color: transparent;
                    }}
                """)

                # ── status / thinking bar: frosted so text is always readable ─
                self.status_label.setStyleSheet(f"""
                    QLabel#statusLabel {{
                        color: #9AA0A6;
                        font-style: italic;
                        font-size: 11px;
                        padding: 5px 14px;
                        background-color: {bg_rgba};
                        border-top: 1px solid rgba(50, 50, 50, 0.5);
                    }}
                """)

                # ── input container: frosted opaque panel — stays visible ─────
                self.input_container.setStyleSheet("""
                    QFrame#inputContainer {
                        background-color: rgba(18, 18, 18, 0.85);
                        border-top: 1px solid rgba(50, 50, 50, 0.6);
                        border-bottom-left-radius: 12px;
                        border-bottom-right-radius: 12px;
                    }
                """)

                # ── message bubbles stay solid — no change needed ─────────────

            else:
                # ── restore solid theme surfaces ─────────────────────────────
                # Delegate to apply_theme so the chosen palette is used,
                # not hardcoded Obsidian Blue colours.
                theme_key = getattr(self, '_current_theme_key', 'obsidian_blue')
                self.apply_theme(theme_key)

        except Exception as e:
            print(f"[ChatWindow.apply_glass_background] Error: {e}")

    def _apply_glass_from_settings(self):
        """Read glass and theme settings from controller and apply on startup."""
        try:
            settings = self.controller.settings
            # Apply theme first so _current_theme_key is set
            theme_key = settings.get('chat_theme', 'obsidian_blue')
            self.apply_theme(theme_key)
            # Then apply glass on top if enabled
            enabled = settings.get('glass_background_enabled', False)
            opacity = float(settings.get('glass_background_opacity', 0.75))
            if enabled:
                self.apply_glass_background(enabled, opacity)
        except Exception as e:
            print(f"[ChatWindow._apply_glass_from_settings] Error: {e}")

    def _open_manage_tasks_window(self):
        """Open the task management window."""
        try:
            from ui.manage_tasks_window import ManageTasksWindow
            if not hasattr(self, '_tasks_window') or self._tasks_window is None:
                self._tasks_window = ManageTasksWindow(self.controller)
            self._tasks_window.show()
            self._tasks_window.raise_()
            self._tasks_window.activateWindow()
        except Exception as e:
            self.add_system_message(f"⚠️ Could not open Manage Tasks window: {e}")

    def _open_memory_window(self):
        """Open the memory management window."""
        try:
            from ui.memory_window import MemoryWindow
            if not hasattr(self, '_memory_window') or self._memory_window is None:
                self._memory_window = MemoryWindow(self.controller)
            self._memory_window.show()
            self._memory_window.raise_()
            self._memory_window.activateWindow()
        except Exception as e:
            self.add_system_message(f"⚠️ Could not open Memory window: {e}")

    def check_admin_mode(self):
        """Check if running as admin and notify user"""
        import ctypes
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if is_admin:
                self.add_system_message(
                    "⚠️ **Administrator Privileges Granted**\n\n"
                    "This Agent is now running with elevated system privileges and can perform high-level system "
                    "changes and tasks."
                )
        except:
            pass