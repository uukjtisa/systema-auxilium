"""
SkillsPanel — sidebar panel that lists installed skills and allows
creating / deleting them.  Shown/hidden by the ⚡ Skills button in ChatWindow.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from core.logger import _make_logger, _NoOpLogger
from core.skill_manager import SkillManager

_verbose = True
log = _make_logger("SkillsPanel") if _verbose else _NoOpLogger()

# ── Palette ───────────────────────────────────────────────────────────────────
_BG          = "#1A1A1A"
_SURFACE     = "#242424"
_BORDER      = "#2E2E2E"
_ACCENT      = "#5865F2"
_TEXT        = "#E8EAED"
_MUTED       = "#9AA0A6"
_DANGER_HOVER = "#C0392B"


class _SkillRow(QWidget):
    """Collapsible row for a single skill with load/unload control."""

    def __init__(self, skill: dict, skill_manager: SkillManager, parent=None):
        super().__init__(parent)
        self._skill = skill
        self._skill_manager = skill_manager
        self._expanded = False
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {_SURFACE};
                border-radius: 6px;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────────
        header_widget = QWidget()
        header_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {_SURFACE};
                border-radius: 6px;
            }}
            QWidget:hover {{
                background-color: #2C2C2C;
            }}
        """)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(10, 8, 10, 8)
        header_layout.setSpacing(6)

        self._toggle_btn = QPushButton("▶")
        self._toggle_btn.setFixedSize(18, 18)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {_MUTED};
                font-size: 10px;
                padding: 0;
            }}
            QPushButton:hover {{ color: {_TEXT}; }}
        """)
        self._toggle_btn.clicked.connect(self._toggle)
        header_layout.addWidget(self._toggle_btn)

        name_lbl = QLabel(self._skill['name'])
        name_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px; font-weight: 500; background: transparent;")
        header_layout.addWidget(name_lbl, stretch=1)

        # ── Is Loaded badge ───────────────────────────────────────────────────
        is_loaded = self._skill.get('is_loaded', False)
        self._badge = QLabel("● Loaded" if is_loaded else "○")
        self._badge.setStyleSheet(self._badge_style(is_loaded))
        header_layout.addWidget(self._badge)

        # ── Load / Unload button ──────────────────────────────────────────────
        self._load_btn = QPushButton("Unload" if is_loaded else "Load")
        self._load_btn.setFixedHeight(22)
        self._load_btn.setStyleSheet(self._load_btn_style(is_loaded))
        self._load_btn.clicked.connect(self._toggle_load)
        header_layout.addWidget(self._load_btn)

        trash_btn = QPushButton("🗑")
        trash_btn.setFixedSize(24, 24)
        trash_btn.setToolTip(f"Delete '{self._skill['name']}'")
        trash_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                font-size: 13px;
                color: {_MUTED};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3C1A1A;
                color: #FF6B6B;
            }}
        """)
        trash_btn.clicked.connect(self._delete)
        header_layout.addWidget(trash_btn)

        outer.addWidget(header_widget)

        # ── Expanded content ──────────────────────────────────────────────────
        self._detail = QWidget()
        self._detail.setStyleSheet(f"background-color: {_SURFACE}; border-radius: 0 0 6px 6px;")
        self._detail.hide()

        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(14, 4, 14, 10)
        detail_layout.setSpacing(4)

        if self._skill.get('description'):
            desc = QLabel(self._skill['description'])
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
            detail_layout.addWidget(desc)

        if self._skill.get('files'):
            files_lbl = QLabel("\n".join(f"  📄 {f}" for f in self._skill['files'][:8]))
            files_lbl.setStyleSheet(f"""
                color: #5F6368;
                font-size: 10px;
                font-family: monospace;
                background: transparent;
            """)
            detail_layout.addWidget(files_lbl)

        outer.addWidget(self._detail)

    def _badge_style(self, loaded: bool) -> str:
        if loaded:
            return (
                f"QLabel {{ background-color: #1A2B1A; color: #4CAF50; "
                f"border-radius: 4px; font-size: 9px; padding: 1px 6px; }}"
            )
        return (
            f"QLabel {{ background-color: transparent; color: {_MUTED}; "
            f"font-size: 9px; padding: 1px 4px; }}"
        )

    def _load_btn_style(self, loaded: bool) -> str:
        if loaded:
            return (
                f"QPushButton {{ background-color: #3C1A1A; color: #C0392B; "
                f"border: 1px solid #C0392B; border-radius: 4px; "
                f"font-size: 9px; padding: 0 8px; }}"
                f"QPushButton:hover {{ background-color: #4A2020; }}"
            )
        return (
            f"QPushButton {{ background-color: #1A2B1A; color: #4CAF50; "
            f"border: 1px solid #4CAF50; border-radius: 4px; "
            f"font-size: 9px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: #223322; }}"
        )

    def _toggle_load(self):
        name = self._skill['name']
        is_loaded = self._skill_manager.is_loaded(name)
        if is_loaded:
            ok, msg = self._skill_manager.unload_skill(name)
        else:
            ok, msg = self._skill_manager.load_skill(name)

        if ok:
            new_state = not is_loaded
            self._skill['is_loaded'] = new_state
            self._badge.setText("● Loaded" if new_state else "○")
            self._badge.setStyleSheet(self._badge_style(new_state))
            self._load_btn.setText("Unload" if new_state else "Load")
            self._load_btn.setStyleSheet(self._load_btn_style(new_state))
        else:
            log.warning(f"[SkillRow._toggle_load] Failed: {msg}")

    def _toggle(self):
        self._expanded = not self._expanded
        self._toggle_btn.setText("▼" if self._expanded else "▶")
        self._detail.setVisible(self._expanded)

    def _delete(self):
        log.info(f"[SkillRow._delete] Deleting skill '{self._skill['name']}'")
        self._skill_manager.delete_skill(self._skill['name'])


# ─────────────────────────────────────────────────────────────────────────────

class SkillsPanel(QWidget):
    """Floating panel that shows installed skills and lets you manage them."""

    def __init__(self, skill_manager: SkillManager, parent=None):
        super().__init__(parent)
        self._skill_manager = skill_manager
        self._skill_manager.skills_changed.connect(self.refresh)
        self._skill_manager.loaded_skills_changed.connect(self.refresh)
        self._build_ui()
        self.refresh()
        log.info("[SkillsPanel.__init__] Panel ready")

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(f"""
            SkillsPanel {{
                background-color: {_BG};
                border-left: 1px solid {_BORDER};
            }}
        """)
        self.setMinimumWidth(260)
        self.setMaximumWidth(320)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background-color: {_SURFACE}; border-bottom: 1px solid {_BORDER};")
        header.setFixedHeight(52)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(14, 0, 10, 0)

        title = QLabel("⚡ Skills")
        title.setStyleSheet(f"color: {_TEXT}; font-size: 13px; font-weight: 600;")
        h_layout.addWidget(title, stretch=1)

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 10px;")
        h_layout.addWidget(self._count_lbl)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {_MUTED};
                font-size: 13px;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: #3C3C3C;
                color: {_TEXT};
            }}
        """)
        close_btn.clicked.connect(self.hide)
        h_layout.addWidget(close_btn)

        root.addWidget(header)

        # Live indicator strip
        live_strip = QLabel("  ● Skills persist — unload manually or via AI")
        live_strip.setStyleSheet(f"""
            background-color: #1A2B1A;
            color: #4CAF50;
            font-size: 10px;
            padding: 3px 14px;
            border-bottom: 1px solid {_BORDER};
        """)
        root.addWidget(live_strip)

        # ── Scroll area for skill rows ─────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {_BG}; width: 5px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: #3C3C3C; border-radius: 2px; min-height: 20px;
            }}
        """)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background-color: {_BG};")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(10, 10, 10, 6)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()

        self._scroll.setWidget(self._list_widget)
        root.addWidget(self._scroll, stretch=1)

        # ── Footer ────────────────────────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet(f"background-color: {_SURFACE}; border-top: 1px solid {_BORDER};")
        f_layout = QVBoxLayout(footer)
        f_layout.setContentsMargins(10, 10, 10, 10)
        f_layout.setSpacing(8)

        # Add new skill — inline input
        add_container = QWidget()
        add_container.setStyleSheet("background: transparent;")
        add_layout = QHBoxLayout(add_container)
        add_layout.setContentsMargins(0, 0, 0, 0)
        add_layout.setSpacing(6)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("New skill name…")
        self._name_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: #2A2A2A;
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_TEXT};
                font-size: 11px;
                padding: 5px 8px;
            }}
            QLineEdit:focus {{
                border-color: {_ACCENT};
            }}
        """)
        self._name_input.returnPressed.connect(self._create_skill)
        add_layout.addWidget(self._name_input, stretch=1)

        create_btn = QPushButton("＋")
        create_btn.setFixedSize(28, 28)
        create_btn.setToolTip("Create skill template")
        create_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_ACCENT};
                border: none;
                border-radius: 5px;
                color: white;
                font-size: 15px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #4752C4; }}
        """)
        create_btn.clicked.connect(self._create_skill)
        add_layout.addWidget(create_btn)

        f_layout.addWidget(add_container)

        # Open folder button
        open_btn = QPushButton("📁  Open skills folder")
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px dashed {_BORDER};
                border-radius: 5px;
                color: {_MUTED};
                font-size: 11px;
                padding: 6px;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: #5F6368;
                color: {_TEXT};
            }}
        """)
        open_btn.clicked.connect(self._open_folder)
        f_layout.addWidget(open_btn)

        root.addWidget(footer)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        """Rebuild the skill list from SkillManager."""
        log.debug("[SkillsPanel.refresh] Refreshing skill rows")

        # Remove all existing rows (keep the stretch at the end)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        skills = self._skill_manager.get_skills()
        loaded_count = sum(1 for s in skills if s.get('is_loaded'))
        self._count_lbl.setText(f"{len(skills)} skills · {loaded_count} loaded")

        if not skills:
            empty = QLabel("No skills installed yet.\nCreate one below ↓")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {_MUTED}; font-size: 11px; padding: 20px;")
            self._list_layout.insertWidget(0, empty)
        else:
            for i, skill in enumerate(skills):
                row = _SkillRow(skill, self._skill_manager)
                self._list_layout.insertWidget(i, row)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _create_skill(self):
        name = self._name_input.text().strip()
        if not name:
            return
        log.info(f"[SkillsPanel._create_skill] Creating '{name}'")
        self._skill_manager.create_skill_template(name)
        self._name_input.clear()

    def _open_folder(self):
        url = QUrl.fromLocalFile(str(self._skill_manager.skills_dir))
        QDesktopServices.openUrl(url)
        log.debug(f"[SkillsPanel._open_folder] Opening '{self._skill_manager.skills_dir}'")
