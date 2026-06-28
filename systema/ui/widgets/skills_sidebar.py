"""
systema/ui/widgets/skills_sidebar.py
Skills sidebar widgets — _SkillRow + SkillsSidebarSection.
Extracted verbatim from chat_window.py.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt


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
