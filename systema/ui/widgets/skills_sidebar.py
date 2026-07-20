"""
systema/ui/widgets/skills_sidebar.py
Skills sidebar widgets — _SkillRow + SkillsSidebarSection.

Theme-aware: every colour is pulled from the live chat palette (passed in as a
resolved palette dict), so the block matches whatever theme is active instead
of the old hard-coded GitHub-blue. Rows sort recently-used first; an Unload all
control clears every loaded skill at once.
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QSizePolicy)
from PyQt6.QtCore import Qt


# Fallback palette (obsidian-blue-ish) for callers that pass none — keeps the
# widget usable standalone / in tests. Real use always passes the live palette.
_FALLBACK_PALETTE = {
    'surface': "#161B22", 'surface2': "#21262D", 'border': "#30363D",
    'accent': "#58A6FF", 'accent_lt': "#79b8ff", 'text': "#E6EDF3",
    'muted': "#8B949E", 'green': "#3FB950", 'red': "#F85149",
}


def _pal(palette: dict | None) -> dict:
    p = dict(_FALLBACK_PALETTE)
    if palette:
        p.update({k: v for k, v in palette.items() if v})
    return p


class _SkillRow(QWidget):
    """
    One skill row: two-line layout so the name always word-wraps cleanly.
      Line 1 — chevron + skill name (full width, word-wrap)
      Line 2 — badge  +  Load/Unload btn  +  delete btn  (right-aligned)
    """

    def __init__(self, skill: dict, skill_manager, palette: dict | None = None, parent=None):
        super().__init__(parent)
        self._skill = skill
        self._skill_manager = skill_manager
        self._p = _pal(palette)
        self._expanded = False
        self._build_ui()

    def _build_ui(self):
        p = self._p
        self.setStyleSheet(
            f"QWidget {{ background-color: {p['surface']}; border-radius: 6px; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── clickable header ──────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setStyleSheet(f"""
            QWidget {{ background-color: {p['surface']}; border-radius: 6px; }}
            QWidget:hover {{ background-color: {p['surface2']}; }}
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
                          color: {p['muted']}; font-size: 8px; padding: 0; }}
            QPushButton:hover {{ color: {p['text']}; }}
        """)
        self._chevron.clicked.connect(self._toggle_expand)
        r1l.addWidget(self._chevron)

        display_name = self._skill['name'].replace('_', '_​')
        name_lbl = QLabel(display_name)
        name_lbl.setWordWrap(True)
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 11px; font-weight: 500; background: transparent;")
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
                          color: {p['muted']}; border-radius: 4px; }}
            QPushButton:hover {{ background-color: {self._tint(p['red'], 0.16)};
                          color: {p['red']}; }}
        """)
        del_btn.clicked.connect(self._delete)
        r2l.addWidget(del_btn)

        hdr_vl.addWidget(r2)
        outer.addWidget(hdr)

        # ── expandable detail ─────────────────────────────────────────────────
        self._detail = QWidget()
        self._detail.setStyleSheet(f"background-color: {p['surface']};")
        self._detail.hide()
        dl = QVBoxLayout(self._detail)
        dl.setContentsMargins(14, 2, 14, 8)
        dl.setSpacing(3)

        if self._skill.get('description'):
            d = QLabel(self._skill['description'])
            d.setWordWrap(True)
            d.setMinimumWidth(0)
            d.setStyleSheet(f"color: {p['muted']}; font-size: 10px; background-color: {p['surface']};")
            dl.addWidget(d)

        if self._skill.get('files'):
            fl = QLabel("\n".join(f"  📄 {f}" for f in self._skill['files'][:8]))
            fl.setWordWrap(True)
            fl.setMinimumWidth(0)
            fl.setStyleSheet(
                f"color: {p['muted']}; font-size: 9px; font-family: monospace; background-color: {p['surface']};")
            dl.addWidget(fl)

        outer.addWidget(self._detail)

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _tint(hex_color: str, alpha: float) -> str:
        """A translucent rgba() wash of a hex colour (for hover fills)."""
        try:
            h = hex_color.lstrip('#')
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha:.2f})"
        except Exception:
            return f"rgba(88,166,255,{alpha:.2f})"

    def _badge_style(self, loaded):
        p = self._p
        if loaded:
            return (f"QLabel {{ background-color: {self._tint(p['green'], 0.15)}; color: {p['green']}; "
                    "border-radius: 4px; font-size: 9px; padding: 1px 5px; }}")
        return (f"QLabel {{ background-color: transparent; color: {p['muted']}; "
                "font-size: 9px; padding: 1px 3px; }}")

    def _load_btn_style(self, loaded):
        p = self._p
        if loaded:
            return (f"QPushButton {{ background-color: {self._tint(p['red'], 0.14)}; color: {p['red']}; "
                    f"border: 1px solid {self._tint(p['red'], 0.55)}; border-radius: 4px; font-size: 9px; padding: 0; }}"
                    f"QPushButton:hover {{ background-color: {self._tint(p['red'], 0.24)}; }}")
        return (f"QPushButton {{ background-color: {self._tint(p['accent'], 0.14)}; color: {p['accent']}; "
                f"border: 1px solid {self._tint(p['accent'], 0.55)}; border-radius: 4px; font-size: 9px; padding: 0; }}"
                f"QPushButton:hover {{ background-color: {self._tint(p['accent'], 0.24)}; }}")

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

    def __init__(self, skill_manager, palette: dict | None = None, parent=None):
        super().__init__(parent)
        self._skill_manager = skill_manager
        self._p = _pal(palette)
        self._expanded = False          # default collapsed at startup
        self._build_ui()
        skill_manager.skills_changed.connect(self.refresh)
        skill_manager.loaded_skills_changed.connect(self.refresh)
        self.refresh()

    def apply_palette(self, palette: dict):
        """Re-theme to a new palette (called on a live theme switch)."""
        self._p = _pal(palette)
        self._restyle_chrome()
        self.refresh()

    def _build_ui(self):
        p = self._p
        self.setStyleSheet("background: transparent;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # section header — click to expand/collapse
        self._hdr = QWidget()
        self._hdr.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self._hdr)
        hl.setContentsMargins(4, 6, 4, 6)
        hl.setSpacing(6)

        self._sec_chevron = QLabel("▶")   # starts collapsed
        hl.addWidget(self._sec_chevron)
        self._sec_lbl = QLabel("⚡ Skills")
        hl.addWidget(self._sec_lbl, stretch=1)
        self._count_lbl = QLabel("")
        hl.addWidget(self._count_lbl)

        self._hdr.mousePressEvent = lambda e: self._toggle_section()
        root.addWidget(self._hdr)

        # collapsible body — hidden by default
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body.hide()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)

        # ── Search + Unload-all row ─────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        self._skill_search = QLineEdit()
        self._skill_search.setPlaceholderText("Search skills…")
        self._skill_search.setFixedHeight(26)
        self._skill_search.textChanged.connect(self._on_skill_search_changed)
        top_row.addWidget(self._skill_search, stretch=1)
        self._unload_all_btn = QPushButton("Unload all")
        self._unload_all_btn.setFixedHeight(26)
        self._unload_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unload_all_btn.setToolTip("Unload every currently-loaded skill.")
        self._unload_all_btn.clicked.connect(self._unload_all)
        top_row.addWidget(self._unload_all_btn)
        bl.addLayout(top_row)

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
        self._sk_show_more_btn = QPushButton("Show 10 more")
        self._sk_show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sk_show_more_btn.clicked.connect(self._skill_show_more)
        self._sk_show_all_btn = QPushButton("Show all")
        self._sk_show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sk_show_all_btn.clicked.connect(self._skill_show_all)
        self._sk_sep = QLabel("·")
        _sf_lay.addWidget(self._sk_show_more_btn)
        _sf_lay.addWidget(self._sk_sep)
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
        self._name_input.returnPressed.connect(self._create_skill)
        al.addWidget(self._name_input, stretch=1)
        self._create_btn = QPushButton("＋")
        self._create_btn.setFixedSize(26, 26)
        self._create_btn.clicked.connect(self._create_skill)
        al.addWidget(self._create_btn)
        bl.addWidget(add_w)

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        self._open_btn = QPushButton("📁  Open skills folder")
        self._open_btn.setMinimumWidth(0)
        self._open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._skill_manager.skills_dir))))
        bl.addWidget(self._open_btn)

        root.addWidget(self._body)
        self._restyle_chrome()

    def _restyle_chrome(self):
        """(Re)apply palette-driven styles to the fixed chrome (not the rows —
        those are rebuilt by refresh())."""
        p = self._p
        self._hdr.setStyleSheet(f"""
            QWidget {{ background-color: transparent; border-radius: 4px; }}
            QWidget:hover {{ background-color: {p['surface2']}; }}
        """)
        self._sec_chevron.setStyleSheet(
            f"color: {p['muted']}; font-size: 9px; background: transparent;")
        self._sec_lbl.setStyleSheet(
            f"color: {p['text']}; font-size: 12px; font-weight: 600; background: transparent;")
        self._count_lbl.setStyleSheet(
            f"color: {p['muted']}; font-size: 9px; background: transparent;")
        _field = (f"QLineEdit {{ background-color: {p['surface2']}; border: 1px solid {p['border']};"
                  f" border-radius: 5px; color: {p['text']}; font-size: 10px; padding: 0 7px; }}"
                  f"QLineEdit:focus {{ border-color: {p['accent']}; color: {p['text']}; }}")
        self._skill_search.setStyleSheet(_field)
        self._name_input.setStyleSheet(_field.replace("padding: 0 7px", "padding: 4px 7px"))
        _ghost = (f"QPushButton {{ background: transparent; border: none;"
                  f" color: {p['muted']}; font-size: 9px; padding: 0; }}"
                  f"QPushButton:hover {{ color: {p['text']}; }}")
        self._sk_show_more_btn.setStyleSheet(_ghost)
        self._sk_show_all_btn.setStyleSheet(_ghost)
        self._sk_sep.setStyleSheet(f"color: {p['border']}; background: transparent; font-size: 9px;")
        self._unload_all_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: 1px solid {p['border']};"
            f" border-radius: 5px; color: {p['muted']}; font-size: 10px; padding: 0 8px; }}"
            f"QPushButton:hover {{ border-color: {p['accent']}; color: {p['text']}; }}"
            f"QPushButton:disabled {{ color: {p['border']}; border-color: {p['border']}; }}")
        self._create_btn.setStyleSheet(
            f"QPushButton {{ background-color: {p['accent']}; border: none; border-radius: 5px;"
            f" color: #05070a; font-size: 14px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: {p.get('accent_lt', p['accent'])}; }}")
        self._open_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; border: 1px dashed {p['border']};"
            f" border-radius: 5px; color: {p['muted']}; font-size: 10px;"
            f" padding: 5px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {p['accent']}; color: {p['text']}; }}")

    def _toggle_section(self):
        self._expanded = not self._expanded
        self._sec_chevron.setText("▼" if self._expanded else "▶")
        self._body.setVisible(self._expanded)

    def refresh(self):
        p = self._p
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_skills = self._skill_manager.get_skills()
        loaded_count = sum(1 for s in all_skills if s.get('is_loaded'))
        self._count_lbl.setText(f"{len(all_skills)} · {loaded_count} loaded")
        if hasattr(self, '_unload_all_btn'):
            self._unload_all_btn.setEnabled(loaded_count > 0)

        # Default ordering = recently used first: the most recently loaded
        # skills float to the top (loaded-but-never-timestamped next), then
        # everything else alphabetically. Search still filters the same set.
        all_skills.sort(key=lambda s: (-float(s.get('last_used', 0.0) or 0.0),
                                       s['name'].lower()))

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
            empty.setStyleSheet(f"color: {p['muted']}; font-size: 10px; padding: 12px;")
            self._rows_layout.addWidget(empty)
            if hasattr(self, '_sk_footer'):
                self._sk_footer.hide()
            return

        vis = getattr(self, '_sk_visible_count', 10)
        for skill in skills[:vis]:
            self._rows_layout.addWidget(_SkillRow(skill, self._skill_manager, self._p))

        remaining = len(skills) - vis
        if hasattr(self, '_sk_footer') and hasattr(self, '_sk_show_more_btn'):
            if remaining > 0:
                self._sk_show_more_btn.setText(f"Show {min(10, remaining)} more")
                self._sk_show_all_btn.setText(f"Show all ({len(skills)})")
                self._sk_footer.show()
            else:
                self._sk_footer.hide()

    def _unload_all(self):
        n = self._skill_manager.unload_all_skills()
        if not n:
            return
        self.refresh()

    def _on_skill_search_changed(self):
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
