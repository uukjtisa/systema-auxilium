"""
ui/settings_window.py
Settings Window - Configure API key, AI provider, Puter.js settings, Google Gemini, and Voice
FIXED: Voice settings now actually work - VAD and TTS voice selection are functional
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QComboBox, QGroupBox,
                             QCheckBox, QScrollArea, QFrame, QSlider, QSpinBox, QDoubleSpinBox,
                             QPlainTextEdit, QFileDialog)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect, QRectF
from PyQt6.QtGui import QRegion, QPainter, QColor, QFont, QPen
from ui.base_window import BaseWindow
from ui import theme as _theme


class _TokenGraphCanvas(QWidget):
    """Simple bar graph for token usage data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self._mode = "Daily"
        # Paint palette (overridden by set_palette to follow the active theme).
        self._c_bg = "#161B22"
        self._c_grid = "#21262D"
        self._c_accent = "#58A6FF"
        self._c_accent_dim = "#1A2D4A"
        self._c_muted = "#555555"

    def set_palette(self, bg, grid, accent, accent_dim, muted):
        self._c_bg, self._c_grid = bg, grid
        self._c_accent, self._c_accent_dim, self._c_muted = accent, accent_dim, muted
        self.update()

    def set_data(self, data, mode):
        self._data = data
        self._mode = mode
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 40, 10, 12, 26
        graph_w = w - pad_l - pad_r
        graph_h = h - pad_t - pad_b

        painter.fillRect(0, 0, w, h, QColor(self._c_bg))

        if not self._data:
            painter.setPen(QColor(self._c_muted))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(0, 0, w, h),
                             Qt.AlignmentFlag.AlignCenter,
                             "No token data yet.\nSend a message to start tracking.")
            painter.end()
            return

        raw_max = max(v for _, v in self._data) or 1
        padding = max(raw_max * 0.1, 10)
        max_val = raw_max + padding
        n = len(self._data)
        gap = graph_w / n
        bar_w = max(2, min(int(gap * 0.65), 48))

        # Grid lines
        painter.setPen(QPen(QColor(self._c_grid), 1))
        for i in range(1, 4):
            y = pad_t + graph_h - int(graph_h * i / 3)
            painter.drawLine(pad_l, y, pad_l + graph_w, y)

        def _fmt(v):
            return f"{v//1000}k" if v >= 1000 else str(v)

        painter.setPen(QColor(self._c_muted))
        painter.setFont(QFont("Segoe UI", 7))
        for i in range(0, 4):
            v = int(max_val * i / 3)
            y = pad_t + graph_h - int(graph_h * i / 3)
            painter.drawText(QRectF(0, y - 8, pad_l - 4, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             _fmt(v))

        accent = QColor(self._c_accent)
        accent_dim = QColor(self._c_accent_dim)
        for i, (lbl, val) in enumerate(self._data):
            x = pad_l + int(i * gap) + int((gap - bar_w) / 2)
            bar_h = max(2, int(graph_h * val / max_val))
            y = pad_t + graph_h - bar_h
            painter.fillRect(x, pad_t, bar_w, graph_h, accent_dim)
            painter.fillRect(x, y, bar_w, bar_h, accent)
            # X label — only show every other label if crowded
            if n <= 16 or i % max(1, n // 12) == 0:
                painter.setPen(QColor(self._c_muted))
                lbl_short = lbl[-5:] if len(lbl) > 5 else lbl
                painter.drawText(QRectF(x - gap / 2, pad_t + graph_h + 3, gap * 2, pad_b),
                                 Qt.AlignmentFlag.AlignCenter, lbl_short)

        painter.end()


class SettingsWindow(BaseWindow):
    """Settings window for configuring the AI assistant"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        # Window chrome state
        self._init_chrome_state()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Window settings - BORDERLESS (resizable like chat window)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(600, 500)
        self.resize(600, 750)  # Default size (but resizable!)

        # Main container for rounded corners
        self.container = QWidget()
        self.container.setAutoFillBackground(True)
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #0D1117;
                border-radius: 12px;
            }
            QWidget {
                color: #E6EDF3;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
            QScrollArea {
                background-color: #161B22;
            }
            QScrollArea > QWidget {
                background-color: #161B22;
            }
        """)
        self.container.setObjectName("container")

        self.init_ui()
        self.load_settings()

        # Wrap everything in container for rounded corners
        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        # Apply rounded mask
        self.apply_rounded_mask()
        self.create_resize_handles()
        self._sync_glass()

    def _sync_glass(self):
        """No-op by design: the Settings window always stays opaque for the
        readability of its dense forms, so the glass overlay never applies here.
        (It's intentionally excluded from the glass window checklist.)"""
        return

    def _palette(self):
        """Return the active theme's colours as the 7-tuple this window uses:
        (base, surface, elevated, border, accent, text, muted)."""
        p = _theme.current_palette(self.controller)
        return p['bg'], p['surface'], p['surface2'], p['border'], p['accent'], p['text'], p['muted']

    def init_ui(self):
        """Initialize tabbed settings UI"""
        from PyQt6.QtWidgets import QTabWidget, QSlider, QRadioButton, QButtonGroup, QGridLayout
        from PyQt6.QtCore import Qt as _Qt

        # ── Palette (from the active theme) ─────────────────────────────────
        _BASE, _SURFACE, _ELEV, _BORDER, _ACCENT, _TEXT, _MUTED = self._palette()

        _INPUT = f"""
            QLineEdit, QTextEdit {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 12px;
                color: {_TEXT};
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border-color: {_ACCENT};
            }}
        """
        _COMBO = f"""
            QComboBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                color: {_TEXT};
                selection-background-color: {_ACCENT};
                selection-color: #000;
            }}
        """
        _BTN = f"""
            QPushButton {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QPushButton:hover {{
                background-color: {_theme.lighten(_ELEV, 0.10)};
                border-color: {_ACCENT};
                color: {_ACCENT};
            }}
        """
        _BTN_PRIMARY = f"""
            QPushButton {{
                background-color: {_ACCENT};
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: 600;
                color: {_theme.darken(_ACCENT, 0.80)};
            }}
            QPushButton:hover {{ background-color: {_theme.lighten(_ACCENT, 0.20)}; }}
            QPushButton:pressed {{ background-color: {_theme.darken(_ACCENT, 0.15)}; }}
        """
        _GROUP = f"""
            QGroupBox {{
                color: {_TEXT};
                font-weight: 600;
                font-size: 11px;
                border: 1px solid {_BORDER};
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 14px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {_MUTED};
            }}
        """
        _CHECK = f"""
            QCheckBox {{ color: {_TEXT}; font-size: 11px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border-radius: 4px;
                border: 1px solid {_BORDER};
                background: {_ELEV};
            }}
            QCheckBox::indicator:checked {{
                background: {_ACCENT};
                border-color: {_ACCENT};
            }}
        """
        _SCROLL = f"""
            QScrollArea {{ border: none; background: {_SURFACE}; }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER}; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

        def _label(text, muted=False, bold=False, top_margin=0):
            lbl = QLabel(text)
            size = "10px" if muted else "11px"
            color = _MUTED if muted else _TEXT
            weight = "600" if bold else "400"
            lbl.setStyleSheet(
                f"color:{color}; font-size:{size}; font-weight:{weight}; margin-top:{top_margin}px;")
            lbl.setWordWrap(True)
            return lbl

        def _info_box(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color:{_MUTED}; font-size:10px; background:{_ELEV}; "
                f"border:1px solid {_BORDER}; border-radius:6px; padding:10px;")
            return lbl

        def _make_scroll_tab():
            """Return (scroll_area, inner_layout)."""
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet(_SCROLL)
            inner = QWidget()
            inner.setStyleSheet(f"QWidget {{ background:{_SURFACE}; }}")
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(18, 16, 18, 16)
            lay.setSpacing(14)
            scroll.setWidget(inner)
            return scroll, lay

        # ── Update the container stylesheet to Obsidian Blue ────────────────
        self.container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_BASE};
                border-radius: 12px;
            }}
            QWidget {{ color: {_TEXT}; font-family: 'Segoe UI', system-ui, sans-serif; }}
        """)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        header_bar = QFrame()
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent   = self.header_mouse_press
        header_bar.mouseMoveEvent    = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {_BASE};
                border-bottom: 1px solid {_BORDER};
            }}
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        title_lbl = QLabel("⚙️  Settings")
        title_lbl.setStyleSheet(f"font-size:15px; font-weight:600; color:{_TEXT}; background:transparent;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        for text, slot, danger in [("−", self.showMinimized, False), ("×", self.hide, True)]:
            btn = QPushButton(text)
            btn.setFixedSize(32, 32)
            hover_bg = "#EA4335" if danger else _ELEV
            btn.setStyleSheet(f"""
                QPushButton {{ background:transparent; border:none; border-radius:6px;
                               font-size:{"20" if danger else "18"}px; color:{_MUTED}; }}
                QPushButton:hover {{ background:{hover_bg}; color:white; }}
            """)
            btn.clicked.connect(slot)
            header_layout.addWidget(btn)

        main_layout.addWidget(header_bar)

        # ── Tab widget ───────────────────────────────────────────────────────
        tabs = QTabWidget()
        self._tabs = tabs
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: none;
                background: {_SURFACE};
            }}
            QTabBar::tab {{
                background: {_BASE};
                color: {_MUTED};
                border: none;
                padding: 10px 18px;
                font-size: 11px;
                font-weight: 500;
            }}
            QTabBar::tab:selected {{
                color: {_ACCENT};
                border-bottom: 2px solid {_ACCENT};
                background: {_SURFACE};
            }}
            QTabBar::tab:hover:!selected {{ color: {_TEXT}; background: {_ELEV}; }}
        """)

        # ════════════════════════════════════════════════════════════════════
        # TAB 1 — AI
        # ════════════════════════════════════════════════════════════════════
        ai_scroll, ai_lay = _make_scroll_tab()

        # ── LLM Provider Script List ────────────────────────────────────────────
        provider_group = QGroupBox("AI Provider")
        provider_group.setStyleSheet(_GROUP)
        pg_lay = QVBoxLayout(provider_group)
        pg_lay.addWidget(_label("Active Provider Script:", bold=True, top_margin=3))
        _prov_row = QHBoxLayout()
        self.provider_script_combo = QComboBox()
        self.provider_script_combo.setStyleSheet(_COMBO)
        _prov_row.addWidget(self.provider_script_combo, stretch=1)
        _prov_refresh_btn = QPushButton("⟳")
        _prov_refresh_btn.setFixedWidth(48)
        _prov_refresh_btn.setStyleSheet(_BTN)
        _prov_refresh_btn.setToolTip("Refresh provider scripts list")
        _prov_refresh_btn.clicked.connect(self._refresh_llm_provider_scripts)
        _prov_row.addWidget(_prov_refresh_btn)
        pg_lay.addLayout(_prov_row)
        _open_prov_btn = QPushButton("📂  Open Providers Folder")
        _open_prov_btn.setStyleSheet(_BTN)

        def _open_llm_providers_folder():
            import subprocess, sys as _sys
            folder = self.controller.get_llm_providers_folder()
            if _sys.platform == 'win32':
                subprocess.Popen(['explorer', folder])
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])

        _open_prov_btn.clicked.connect(_open_llm_providers_folder)
        pg_lay.addWidget(_open_prov_btn)
        self._refresh_llm_provider_scripts()
        self.provider_script_combo.currentIndexChanged.connect(self._on_llm_provider_changed)
        ai_lay.addWidget(provider_group)

        # ── Manual provider copy-prompt helper ───────────────────────────────────
        self.manual_group = QGroupBox("Manual Provider Helper")
        self.manual_group.setStyleSheet(_GROUP)
        mn_lay = QVBoxLayout(self.manual_group)
        mn_lay.addWidget(_info_box(
            "Set  self.ai_provider = 'manual'  in controller.py if you want to type responses by hand.\n"
            "Use this button to copy the current system prompt to clipboard."
        ))
        copy_sys_btn = QPushButton("📋 Copy Current System Prompt")
        copy_sys_btn.setStyleSheet(_BTN)
        copy_sys_btn.setToolTip("Copy the full effective system prompt (base + loaded skills) to clipboard")

        def _copy_system_prompt():
            from PyQt6.QtWidgets import QApplication
            try:
                prompt = self.controller.ai._get_effective_system_prompt()
            except Exception as e:
                prompt = f"[Error retrieving system prompt: {e}]"
            QApplication.clipboard().setText(prompt)
            copy_sys_btn.setText("✅ Copied!")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: copy_sys_btn.setText("📋 Copy Current System Prompt"))

        copy_sys_btn.clicked.connect(_copy_system_prompt)
        mn_lay.addWidget(copy_sys_btn)
        ai_lay.addWidget(self.manual_group)

        # ── Conversation Prefilling ──────────────────────────────────────────
        pf_group = QGroupBox("Conversation Prefilling")
        pf_group.setStyleSheet(_GROUP)
        pf_lay = QVBoxLayout(pf_group)

        self.prefilling_checkbox = QCheckBox(
            "Enable Conversation Prefilling (reinforces tool format via fake history)")
        self.prefilling_checkbox.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        self.prefilling_checkbox.setChecked(True)
        pf_lay.addWidget(self.prefilling_checkbox)

        # Source radios
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        self.pf_source_group = QButtonGroup(self)
        self.pf_radio_premade = QRadioButton("Use premade script  (PREFILLING in global_instructions.py)")
        self.pf_radio_session = QRadioButton("Use a saved session as prefilling history")
        for rb in (self.pf_radio_premade, self.pf_radio_session):
            rb.setStyleSheet(f"color:{_TEXT}; font-size:10pt; margin-left:16px;")
        self.pf_source_group.addButton(self.pf_radio_premade, 0)
        self.pf_source_group.addButton(self.pf_radio_session, 1)
        self.pf_radio_premade.setChecked(True)
        pf_lay.addWidget(self.pf_radio_premade)
        pf_lay.addWidget(self.pf_radio_session)

        # Session picker (visible only when session radio is selected)
        self.pf_session_widget = QWidget()
        _pf_sess_row = QHBoxLayout(self.pf_session_widget)
        _pf_sess_row.setContentsMargins(32, 0, 0, 0)
        self.pf_session_combo = QComboBox()
        self.pf_session_combo.setStyleSheet(_COMBO)

        self.pf_session_combo.setMinimumWidth(260)
        _pf_refresh_btn = QPushButton("⟳")
        _pf_refresh_btn.setStyleSheet(_BTN)
        _pf_refresh_btn.setFixedWidth(48)
        _pf_refresh_btn.setToolTip("Refresh session list")
        _pf_refresh_btn.clicked.connect(self._refresh_prefilling_sessions)
        _pf_sess_row.addWidget(QLabel("Session:"))
        _pf_sess_row.addWidget(self.pf_session_combo)
        _pf_sess_row.addWidget(_pf_refresh_btn)
        _pf_sess_row.addStretch()
        self.pf_session_widget.setVisible(False)
        pf_lay.addWidget(self.pf_session_widget)

        # Wire visibility + enable/disable
        self.pf_radio_session.toggled.connect(self.pf_session_widget.setVisible)
        self.prefilling_checkbox.toggled.connect(self.pf_radio_premade.setEnabled)
        self.prefilling_checkbox.toggled.connect(self.pf_radio_session.setEnabled)
        self.prefilling_checkbox.toggled.connect(
            lambda checked: self.pf_session_widget.setVisible(
                checked and self.pf_radio_session.isChecked()))

        pf_lay.addWidget(_label(
            "💡 Premade: edit PREFILLING in global_instructions.py.  "
            "Session: the full chat history of the chosen session is injected "
            "before every request — the AI \"remembers\" doing things that way.",
            muted=True))
        ai_lay.addWidget(pf_group)
        # ────────────────────────────────────────────────────────────────────

        # ── Token Usage Graph ────────────────────────────────────────────────
        tok_group = QGroupBox("Token Usage")
        tok_group.setStyleSheet(_GROUP)
        tg_lay = QVBoxLayout(tok_group)
        tg_lay.addWidget(_label("Estimated tokens consumed over time.", muted=True))

        _tok_mode_row = QHBoxLayout()
        _tok_modes = ["Minutes", "Hourly", "Daily", "Weekly", "Monthly", "Yearly", "All"]
        self._tok_mode_btns = {}
        self._tok_graph_mode = "Daily"
        for _m in _tok_modes:
            _mb = QPushButton(_m)
            _mb.setFixedHeight(22)
            _mb.setCheckable(True)
            _mb.setChecked(_m == "Daily")
            _mb.setStyleSheet(f"""
                QPushButton {{
                    background: {_ELEV}; border: 1px solid {_BORDER};
                    border-radius: 4px; font-size: 9px; color: {_MUTED}; padding: 0 6px;
                }}
                QPushButton:checked {{
                    background: {_ACCENT}; border-color: {_ACCENT}; color: #000;
                }}
                QPushButton:hover:!checked {{ border-color: {_ACCENT}; color: {_TEXT}; }}
            """)
            _mb.clicked.connect(lambda _checked, m=_m: self._set_tok_graph_mode(m))
            _tok_mode_row.addWidget(_mb)
            self._tok_mode_btns[_m] = _mb
        tg_lay.addLayout(_tok_mode_row)

        self._tok_canvas = _TokenGraphCanvas()
        self._tok_canvas.setMinimumHeight(160)
        self._tok_canvas.setMaximumHeight(320)
        self._tok_canvas.setSizePolicy(
            self._tok_canvas.sizePolicy().horizontalPolicy(),
            __import__('PyQt6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding
        )
        self._tok_canvas.setStyleSheet(f"background: {_ELEV}; border-radius: 6px;")
        tg_lay.addWidget(self._tok_canvas)

        self._tok_summary_lbl = QLabel("Open this tab to load data.")
        self._tok_summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 9px;")
        tg_lay.addWidget(self._tok_summary_lbl)

        tg_lay.addWidget(_label("Output Tokens", muted=True))
        self._tok_out_canvas = _TokenGraphCanvas()
        self._tok_out_canvas.setMinimumHeight(160)
        self._tok_out_canvas.setMaximumHeight(320)
        self._tok_out_canvas.setStyleSheet(f"background: {_ELEV}; border-radius: 6px;")
        tg_lay.addWidget(self._tok_out_canvas)
        self._tok_out_summary_lbl = QLabel("")
        self._tok_out_summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 9px;")
        tg_lay.addWidget(self._tok_out_summary_lbl)

        # Theme the graph canvases (bg / grid / bars) to the active palette.
        # All values must be hex — QColor() can't parse CSS rgba() strings.
        _grid = _theme.lighten(_ELEV, 0.10)
        _accent_dim = _theme.darken(_ACCENT, 0.60)
        for _cv in (self._tok_canvas, self._tok_out_canvas):
            _cv.set_palette(_ELEV, _grid, _ACCENT, _accent_dim, _MUTED)

        ai_lay.addWidget(tok_group)

        ai_lay.addStretch()

        # ════════════════════════════════════════════════════════════════════
        # TAB 0 — General
        # ════════════════════════════════════════════════════════════════════
        gen_scroll, gen_lay = _make_scroll_tab()

        # ── "Trying to find something?" — quick-jump shortcuts ───────────────
        # Many settings live in non-obvious tabs (e.g. Tool Calling Mode and Code
        # Execution are under System). These buttons take the user straight there.
        gen_find_group = QGroupBox("Trying to find something?")
        gen_find_group.setStyleSheet(_GROUP)
        gf_lay = QVBoxLayout(gen_find_group)
        gf_lay.addWidget(_info_box(
            "Jump straight to a setting — each shortcut opens the right tab for you."))

        _jump_style = f"""
            QPushButton {{
                background: {_BASE};
                color: {_TEXT};
                border: 1px solid {_ELEV};
                border-radius: 8px;
                padding: 9px 12px;
                font-size: 10pt;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 1px solid {_ACCENT};
                color: {_ACCENT};
                background: {_ELEV};
            }}
        """

        # (button label, target tab index)  — General=0, AI=1, Voice=2, UI=3,
        # Memory=4, Security=5, System=6.
        _gen_shortcuts = [
            ("🔧  Tool Calling Mode (Native / Compatibility)", 6),
            ("⚡  Code Execution", 6),
            ("🤖  AI Provider & Model Script", 1),
            ("💬  Conversation Prefilling", 1),
            ("🎤  Voice & Speech (TTS)", 2),
            ("🎨  Theme & Appearance", 3),
            ("🧠  Memory", 4),
            ("🔒  Security & Approvals", 5),
        ]

        def _make_tab_jump(idx):
            return lambda: self._tabs.setCurrentIndex(idx)

        gf_grid = QGridLayout()
        gf_grid.setHorizontalSpacing(8)
        gf_grid.setVerticalSpacing(8)
        for _gi, (_glabel, _gtab) in enumerate(_gen_shortcuts):
            _gbtn = QPushButton(_glabel)
            _gbtn.setStyleSheet(_jump_style)
            _gbtn.clicked.connect(_make_tab_jump(_gtab))
            gf_grid.addWidget(_gbtn, _gi // 2, _gi % 2)
        gf_lay.addLayout(gf_grid)
        gen_lay.addWidget(gen_find_group)

        gen_startup_group = QGroupBox("Startup")
        gen_startup_group.setStyleSheet(_GROUP)
        gen_s_lay = QVBoxLayout(gen_startup_group)
        self.open_chat_on_startup_checkbox = QCheckBox("Open chat window on startup")
        self.open_chat_on_startup_checkbox.setStyleSheet(_CHECK)
        gen_s_lay.addWidget(self.open_chat_on_startup_checkbox)
        gen_s_lay.addWidget(_info_box(
            "When enabled, the chat window will automatically open when the app starts.\n"
            "When disabled, it still pre-loads in the background for a faster first open."))
        self.open_packet_on_startup_checkbox = QCheckBox(
            "Open packet automatically on startup  (Android Bridge — Systema Auxilium Android Module)")
        self.open_packet_on_startup_checkbox.setStyleSheet(_CHECK)
        gen_s_lay.addWidget(self.open_packet_on_startup_checkbox)
        gen_s_lay.addWidget(_info_box(
            "When enabled, the Android Bridge TCP server starts automatically on launch.\n"
            "Connect the Systema Auxilium Android app over Wi-Fi LAN to remote-control the assistant."))
        gen_lay.addWidget(gen_startup_group)

        gen_display_group = QGroupBox("Chat Display")
        gen_display_group.setStyleSheet(_GROUP)
        gd_lay = QVBoxLayout(gen_display_group)
        self.show_token_count_checkbox = QCheckBox("Show token estimate in message input")
        self.show_token_count_checkbox.setStyleSheet(_CHECK)
        self.show_token_count_checkbox.setToolTip(
            "Shows a small live token counter inside the message input.\n"
            "Grows as the conversation gets longer.")
        gd_lay.addWidget(self.show_token_count_checkbox)
        gd_lay.addWidget(_info_box(
            "The estimate = your current input + entire conversation history. "
            "Useful for knowing when you're approaching model context limits."))
        gen_lay.addWidget(gen_display_group)

        gen_lay.addStretch()
        tabs.addTab(gen_scroll, "⚙️  General")

        tabs.addTab(ai_scroll, "🤖  AI")

        # ════════════════════════════════════════════════════════════════════
        # TAB 2 — Voice
        # ════════════════════════════════════════════════════════════════════
        voice_scroll, voice_lay = _make_scroll_tab()

        # Devices
        dev_group = QGroupBox("Audio Devices")
        dev_group.setStyleSheet(_GROUP)
        dv_lay = QVBoxLayout(dev_group)
        dv_lay.addWidget(_label("🎙️ Microphone:", bold=True))
        self.input_device_combo = QComboBox()
        self.input_device_combo.setStyleSheet(_COMBO)
        dv_lay.addWidget(self.input_device_combo)
        dv_lay.addWidget(_label("🔊 Speaker:", bold=True, top_margin=8))
        self.output_device_combo = QComboBox()
        self.output_device_combo.setStyleSheet(_COMBO)
        dv_lay.addWidget(self.output_device_combo)
        refresh_btn = QPushButton("⟳ Refresh Devices")
        refresh_btn.setStyleSheet(_BTN)
        refresh_btn.clicked.connect(self.refresh_audio_devices)
        dv_lay.addWidget(refresh_btn)
        voice_lay.addWidget(dev_group)

        # Interrupt mode
        int_group = QGroupBox("Voice Interruption")
        int_group.setStyleSheet(_GROUP)
        int_lay = QVBoxLayout(int_group)
        self.interrupt_mode_combo = QComboBox()
        self.interrupt_mode_combo.addItem("Manual (Button in Chat)", "manual")
        self.interrupt_mode_combo.addItem("Automatic (When Speaking)", "auto")
        self.interrupt_mode_combo.setStyleSheet(_COMBO)
        int_lay.addWidget(self.interrupt_mode_combo)
        int_lay.addWidget(_label(
            "💡 Manual: click 🔇 in chat  ·  Automatic: TTS stops when you speak", muted=True))
        voice_lay.addWidget(int_group)

        # TTS
        tts_group = QGroupBox("Text-to-Speech")
        tts_group.setStyleSheet(_GROUP)
        tts_lay = QVBoxLayout(tts_group)
        tts_lay.addWidget(_label("TTS Provider:"))
        _tts_combo_row = QHBoxLayout()
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.setStyleSheet(_COMBO)
        # Edge TTS is always the first built-in option
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        # Custom script providers are added by _refresh_tts_provider_scripts()
        self.tts_provider_combo.currentIndexChanged.connect(self.on_tts_provider_changed)
        _tts_combo_row.addWidget(self.tts_provider_combo, stretch=1)
        _tts_refresh_btn = QPushButton("⟳")
        _tts_refresh_btn.setFixedWidth(48)
        _tts_refresh_btn.setStyleSheet(_BTN)
        _tts_refresh_btn.setToolTip("Refresh TTS provider scripts")
        _tts_refresh_btn.clicked.connect(self._refresh_tts_provider_scripts)
        _tts_combo_row.addWidget(_tts_refresh_btn)
        tts_lay.addLayout(_tts_combo_row)
        tts_lay.addWidget(_info_box(
            "📁  Custom TTS scripts live in  providers/text-to-speech/\n"
            "Each .py must define:  speak(text: str, save_to: str) -> bool\n"
            "When a custom script is selected the voice dropdown below is hidden "
            "(voice config lives inside the script)."
        ))

        # Edge TTS voice sub-group (hidden when a custom script is active)
        self.edge_tts_group = QWidget()
        etg_lay = QVBoxLayout(self.edge_tts_group)
        etg_lay.setContentsMargins(0, 6, 0, 0)
        etg_lay.addWidget(_label("Edge TTS Voice:"))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setStyleSheet(_COMBO)
        for vid, vname in [
            ("en-US-GuyNeural", "Male, American (Guy)"), ("en-US-JennyNeural", "Female, American (Jenny)"),
            ("en-GB-RyanNeural", "Male, British (Ryan)"), ("en-GB-SoniaNeural", "Female, British (Sonia)"),
            ("en-AU-NatashaNeural", "Female, Australian (Natasha)"),
            ("en-AU-WilliamNeural", "Male, Australian (William)"),
            ("en-CA-LiamNeural", "Male, Canadian (Liam)"), ("en-CA-ClaraNeural", "Female, Canadian (Clara)"),
            ("en-IN-NeerjaNeural", "Female, Indian (Neerja)"), ("en-IN-PrabhatNeural", "Male, Indian (Prabhat)"),
        ]:
            self.tts_voice_combo.addItem(vname, vid)
        etg_lay.addWidget(self.tts_voice_combo)
        tts_lay.addWidget(self.edge_tts_group)

        _open_tts_btn = QPushButton("📂  Open TTS Providers Folder")
        _open_tts_btn.setStyleSheet(_BTN)

        def _open_tts_providers_folder():
            import subprocess, sys as _sys
            folder = self.controller.get_tts_providers_folder()
            if _sys.platform == 'win32':
                subprocess.Popen(['explorer', folder])
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])

        _open_tts_btn.clicked.connect(_open_tts_providers_folder)
        tts_lay.addWidget(_open_tts_btn)

        # ── ElevenLabs speech tag toggle ─────────────────────────────────────
        self.elevenlabs_tags_checkbox = QCheckBox("Make Agent Use ElevenLabs Speech Tags")
        self.elevenlabs_tags_checkbox.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        self.elevenlabs_tags_checkbox.setToolTip(
            "Inserts an instruction into the Agent's system prompt to use expressive speech tags "
            "like [laugh], [sigh], [gasp], etc.\n"
            "Highly recommended when using an ElevenLabs-compatible TTS provider — "
            "makes responses sound more natural and expressive."
        )
        tts_lay.addWidget(self.elevenlabs_tags_checkbox)
        # ─────────────────────────────────────────────────────────────────────

        voice_lay.addWidget(tts_group)

        # VAD
        vad_group = QGroupBox("Voice Detection (VAD)")
        vad_group.setStyleSheet(_GROUP)
        vad_lay = QVBoxLayout(vad_group)
        _vt_row = QHBoxLayout()
        _vt_row.addWidget(_label("VAD Engine:"))
        self.webrtc_vad_checkbox = QCheckBox("WebRTC VAD")
        self.webrtc_vad_checkbox.setChecked(True)
        self.webrtc_vad_checkbox.setStyleSheet(_CHECK)
        self.webrtc_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        _vt_row.addWidget(self.webrtc_vad_checkbox)
        self.silero_vad_checkbox = QCheckBox("Silero VAD")
        self.silero_vad_checkbox.setStyleSheet(_CHECK)
        self.silero_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        _vt_row.addWidget(self.silero_vad_checkbox)
        _vt_row.addStretch()
        vad_lay.addLayout(_vt_row)

        self.webrtc_settings_widget = QWidget()
        ww_lay = QVBoxLayout(self.webrtc_settings_widget)
        ww_lay.setContentsMargins(16, 4, 0, 4)
        _wr = QHBoxLayout()
        _wr.addWidget(_label("WebRTC Aggressiveness:"))
        self.vad_combo = QComboBox()
        self.vad_combo.addItem("Level 0 – Least aggressive", 0)
        self.vad_combo.addItem("Level 1 – Low", 1)
        self.vad_combo.addItem("Level 2 – Medium", 2)
        self.vad_combo.addItem("Level 3 – High (default)", 3)
        self.vad_combo.setCurrentIndex(3)
        self.vad_combo.setStyleSheet(_COMBO)
        _wr.addWidget(self.vad_combo)
        ww_lay.addLayout(_wr)
        ww_lay.addWidget(_label("💡 Higher = less sensitive to background noise", muted=True))
        vad_lay.addWidget(self.webrtc_settings_widget)

        self.silero_settings_widget = QWidget()
        sw_lay = QVBoxLayout(self.silero_settings_widget)
        sw_lay.setContentsMargins(16, 4, 0, 4)
        _sr = QHBoxLayout()
        _sr.addWidget(_label("Silero Threshold:"))
        self.silero_threshold_combo = QComboBox()
        self.silero_threshold_combo.addItem("0.3 (Very Sensitive)", 0.3)
        self.silero_threshold_combo.addItem("0.5 (Balanced)", 0.5)
        self.silero_threshold_combo.addItem("0.7 (Conservative)", 0.7)
        self.silero_threshold_combo.addItem("0.9 (Very Conservative)", 0.9)
        self.silero_threshold_combo.setCurrentIndex(1)
        self.silero_threshold_combo.setStyleSheet(_COMBO)
        _sr.addWidget(self.silero_threshold_combo)
        sw_lay.addLayout(_sr)
        sw_lay.addWidget(_label("💡 Higher = fewer false positives", muted=True))
        self.silero_settings_widget.hide()
        vad_lay.addWidget(self.silero_settings_widget)
        voice_lay.addWidget(vad_group)

        voice_lay.addStretch()
        tabs.addTab(voice_scroll, "🎤  Voice")

        # ════════════════════════════════════════════════════════════════════
        # TAB 3 — UI / Appearance
        # ════════════════════════════════════════════════════════════════════
        ui_scroll, ui_lay = _make_scroll_tab()

        # ── Theme section ───────────────────────────────────────────────────
        theme_group = QGroupBox("Main Theme")
        theme_group.setStyleSheet(_GROUP)
        th_lay = QVBoxLayout(theme_group)
        th_lay.addWidget(_label(
            "Choose a color palette. Applied immediately on Save.", muted=True))
        th_lay.addSpacing(6)

        THEMES = [
            ("obsidian_blue",  "Obsidian Blue",  "Deep navy · sky blue",      ["#0D1117","#161B22","#21262D","#58A6FF"]),
            ("onyx",           "Onyx",            "Zinc gray · indigo",         ["#18181B","#1C1C1F","#27272A","#6366F1"]),
            ("carbon",         "Carbon",          "Deep dark · blurple",        ["#111214","#1E1F22","#2B2D31","#5865F2"]),
            ("midnight_rose",  "Midnight Rose",   "Purple tint · violet",       ["#120F1A","#1D1825","#2A2436","#A78BFA"]),
            ("emerald",        "Emerald",         "Forest dark · green",        ["#0D1210","#131A15","#1C2B1F","#3FB950"]),
            ("copper",         "Copper",          "Warm charcoal · orange",     ["#110D09","#1A1310","#261D17","#E8834A"]),
            ("crimson",        "Crimson",         "Volcanic dark · red",        ["#120A0A","#1C1010","#2A1515","#FF4C4C"]),
            ("arctic",         "Arctic",          "Deep ocean · cyan",          ["#0A0E12","#111620","#192030","#67E8F9"]),
            ("golden",         "Golden",          "Midnight warm · gold",       ["#0F0D08","#19160A","#252010","#F5C518"]),
            ("slate",          "Slate",           "Cool neutral · silver",      ["#0C0E12","#141820","#1E2330","#94A3B8"]),
            # Monochrome series
            ("void",           "Void",            "Pure black · monochrome",    ["#000000","#0d0d0d","#111111","#555555"]),
            ("mono_obsidian",  "Obsidian",        "Soft near-black · mono",     ["#0a0a0a","#101010","#171717","#666666"]),
            ("mono_charcoal",  "Charcoal",        "Cool dark · mono",           ["#0e0e10","#141416","#1c1c1f","#606063"]),
            ("ember",          "Ember",           "Warm dark · mono",           ["#0f0d0b","#161310","#1e1a16","#6e665c"]),
        ]

        self._theme_btn_group = QButtonGroup(self)
        self._theme_btn_group.setExclusive(True)
        self._selected_theme = self.controller.settings.get("chat_theme", "obsidian_blue")

        themes_grid = QWidget()
        themes_grid.setStyleSheet("background:transparent;")
        tg_lay = QGridLayout(themes_grid)
        tg_lay.setContentsMargins(0, 0, 0, 0)
        tg_lay.setSpacing(10)

        for idx, (key, name, desc, swatches) in enumerate(THEMES):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {_ELEV};
                    border: 2px solid {"" + _ACCENT + "" if key == self._selected_theme else _BORDER};
                    border-radius: 10px;
                }}
                QFrame:hover {{
                    border-color: {_ACCENT};
                }}
            """)
            card.setCursor(_Qt.CursorShape.PointingHandCursor)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(6)

            # Swatch row
            sw_row = QHBoxLayout()
            sw_row.setSpacing(4)
            for color in swatches:
                sw = QFrame()
                sw.setFixedSize(20, 20)
                sw.setStyleSheet(f"QFrame {{ background:{color}; border-radius:4px; border:none; }}")
                sw_row.addWidget(sw)
            sw_row.addStretch()
            cl.addLayout(sw_row)

            # Radio + Name
            rb = QRadioButton(name)
            rb.setChecked(key == self._selected_theme)
            rb.setStyleSheet(f"""
                QRadioButton {{ color:{_TEXT}; font-size:12px; font-weight:600; background:transparent; }}
                QRadioButton::indicator {{ width:14px; height:14px;
                    border-radius:7px; border:2px solid {_BORDER}; background:{_BASE}; }}
                QRadioButton::indicator:checked {{
                    background:{_ACCENT}; border-color:{_ACCENT};
                }}
            """)
            rb.setProperty("theme_key", key)
            rb.toggled.connect(lambda checked, k=key, c=card: self._on_theme_selected(checked, k, c))
            self._theme_btn_group.addButton(rb)
            cl.addWidget(rb)
            cl.addWidget(_label(desc, muted=True))

            # Make whole card clickable
            def _make_click(r): return lambda e: r.setChecked(True)
            card.mousePressEvent = _make_click(rb)

            tg_lay.addWidget(card, idx // 2, idx % 2)

        self._theme_cards = {}
        for idx, (key, *_) in enumerate(THEMES):
            self._theme_cards[key] = tg_lay.itemAtPosition(idx // 2, idx % 2).widget()

        th_lay.addWidget(themes_grid)
        ui_lay.addWidget(theme_group)

        # ── Glass overlay section ────────────────────────────────────────────
        glass_group = QGroupBox("🪟 Glass Overlay")
        glass_group.setStyleSheet(_GROUP)
        gl_lay = QVBoxLayout(glass_group)
        gl_lay.addWidget(_info_box(
            "Applies a semi-transparent frosted effect that lets your desktop "
            "wallpaper show through, on top of whichever theme is selected. "
            "Text-heavy panels (sidebar, logs, cards) stay frosted-opaque so "
            "they remain readable.\n\n"
            "Use the checklist below to choose which windows get the overlay. "
            "The Settings window is intentionally excluded — it always stays "
            "opaque for readability."))

        self.glass_enabled_checkbox = QCheckBox("Enable glass background")
        self.glass_enabled_checkbox.setStyleSheet(_CHECK)
        gl_lay.addWidget(self.glass_enabled_checkbox)

        # ── Per-window checklist ──────────────────────────────────────────────
        gl_lay.addWidget(_label("Apply glass to these windows:", muted=True, top_margin=6))
        self.glass_window_checkboxes = {}
        for _wkey in _theme.GLASS_WINDOWS:
            _cb = QCheckBox(_theme.GLASS_WINDOW_LABELS.get(_wkey, _wkey))
            _cb.setStyleSheet(_CHECK)
            self.glass_window_checkboxes[_wkey] = _cb
            if _wkey == 'chat':
                # Chat row carries an extra "(sidebar)" sub-toggle so the sidebar
                # can stay solid even when the rest of the chat window is glass.
                _row = QHBoxLayout()
                _row.setContentsMargins(0, 0, 0, 0)
                _row.setSpacing(10)
                _row.addWidget(_cb)
                self.glass_sidebar_checkbox = QCheckBox("(sidebar)")
                self.glass_sidebar_checkbox.setStyleSheet(_CHECK)
                _row.addWidget(self.glass_sidebar_checkbox)
                _row.addStretch()
                gl_lay.addLayout(_row)
            else:
                gl_lay.addWidget(_cb)

        # Disable the checklist when the master glass toggle is off; the sidebar
        # sub-toggle additionally requires the Chat window to be checked.
        def _sync_glass_checklist_enabled():
            on = self.glass_enabled_checkbox.isChecked()
            for _cb in self.glass_window_checkboxes.values():
                _cb.setEnabled(on)
            if hasattr(self, 'glass_sidebar_checkbox'):
                self.glass_sidebar_checkbox.setEnabled(
                    on and self.glass_window_checkboxes['chat'].isChecked())
        self.glass_enabled_checkbox.toggled.connect(lambda _=None: _sync_glass_checklist_enabled())
        self.glass_window_checkboxes['chat'].toggled.connect(
            lambda _=None: _sync_glass_checklist_enabled())
        self._sync_glass_checklist_enabled = _sync_glass_checklist_enabled

        _op_row = QHBoxLayout()
        _op_row.addWidget(_label("Opacity:"))
        self.glass_opacity_slider = QSlider(_Qt.Orientation.Horizontal)
        self.glass_opacity_slider.setMinimum(10)
        self.glass_opacity_slider.setMaximum(98)
        self.glass_opacity_slider.setValue(75)
        self.glass_opacity_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height:4px; background:{_BORDER}; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:{_ACCENT}; border:none;
                width:14px; height:14px; margin:-5px 0; border-radius:7px;
            }}
            QSlider::sub-page:horizontal {{ background:{_ACCENT}; border-radius:2px; }}
        """)
        _op_row.addWidget(self.glass_opacity_slider, stretch=1)
        self.glass_opacity_value_label = QLabel("75%")
        self.glass_opacity_value_label.setFixedWidth(38)
        self.glass_opacity_value_label.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        _op_row.addWidget(self.glass_opacity_value_label)
        gl_lay.addLayout(_op_row)
        self.glass_opacity_slider.valueChanged.connect(
            lambda v: self.glass_opacity_value_label.setText(f"{v}%"))

        ui_lay.addWidget(glass_group)
        ui_lay.addStretch()
        tabs.addTab(ui_scroll, "🎨  UI")

        # ════════════════════════════════════════════════════════════════════
        # TAB 4 — Memory
        # ════════════════════════════════════════════════════════════════════
        mem_scroll, mem_lay = _make_scroll_tab()

        mem_group = QGroupBox("🧠 Memory (RAG)")
        mem_group.setStyleSheet(_GROUP)
        mg_lay = QVBoxLayout(mem_group)
        mg_lay.addWidget(_info_box(
            "Persistent semantic memory across sessions. "
            "The assistant recalls relevant past information and injects it automatically."))
        self.memory_enabled_checkbox = QCheckBox("Enable persistent memory across sessions")
        self.memory_enabled_checkbox.setStyleSheet(_CHECK)
        mg_lay.addWidget(self.memory_enabled_checkbox)

        _thr_row = QHBoxLayout()
        _thr_row.addWidget(_label("Similarity threshold:"))
        self.memory_threshold_combo = QComboBox()
        self.memory_threshold_combo.setStyleSheet(_COMBO)
        for lbl, val in [
            ("0.3 – More (less strict)", 0.3),
            ("0.4 – Balanced (default)", 0.4),
            ("0.5 – Fewer (stricter)", 0.5),
            ("0.6 – Very strict", 0.6),
            ("0.7 – Highly relevant only", 0.7),
        ]:
            self.memory_threshold_combo.addItem(lbl, val)
        _thr_row.addWidget(self.memory_threshold_combo)
        mg_lay.addLayout(_thr_row)

        _max_row = QHBoxLayout()
        _max_row.addWidget(_label("Max memories per message:"))
        self.memory_max_combo = QComboBox()
        self.memory_max_combo.setStyleSheet(_COMBO)
        for n in [3, 5, 8, 10, 15]:
            self.memory_max_combo.addItem(str(n), n)
        _max_row.addWidget(self.memory_max_combo)
        mg_lay.addLayout(_max_row)

        _recall_row = QHBoxLayout()
        _recall_row.addWidget(_label("Memory recall mode:"))
        self.memory_recall_mode_combo = QComboBox()
        self.memory_recall_mode_combo.setStyleSheet(_COMBO)
        self.memory_recall_mode_combo.addItem("Inject all into system prompt", 'inject_all')
        self.memory_recall_mode_combo.addItem("RAG semantic recall", 'rag')
        _recall_row.addWidget(self.memory_recall_mode_combo)
        mg_lay.addLayout(_recall_row)

        open_mem_btn = QPushButton("🧠  Open Memory Manager")
        open_mem_btn.setStyleSheet(f"""
            QPushButton {{ background:#0E1F0E; border:1px solid #1E4A1E; border-radius:6px;
                          color:#3FB950; padding:7px 14px; font-size:11px; }}
            QPushButton:hover {{ background:#122712; }}
        """)
        open_mem_btn.clicked.connect(self._open_memory_window)
        mg_lay.addWidget(open_mem_btn)

        mem_lay.addWidget(mem_group)
        mem_lay.addStretch()
        tabs.addTab(mem_scroll, "🧠  Memory")

        # ════════════════════════════════════════════════════════════════════
        # TAB 5 — Security
        # ════════════════════════════════════════════════════════════════════
        sec_scroll, sec_lay = _make_scroll_tab()

        sec_group = QGroupBox("🔒 Code Execution Safety")
        sec_group.setStyleSheet(_GROUP)
        sg_lay = QVBoxLayout(sec_group)
        self.supervised_checkbox = QCheckBox("Enable Supervised Execution (Recommended)")
        self.supervised_checkbox.setChecked(True)
        self.supervised_checkbox.setStyleSheet(_CHECK)
        sg_lay.addWidget(self.supervised_checkbox)
        sg_lay.addWidget(_info_box(
            "When enabled, you review all code before it runs — edit, explain, or reject it.\n\n"
            "⚠️ Disabling allows automatic code execution without review. "
            "Only disable if you fully trust the AI."))
        sec_lay.addWidget(sec_group)

        dbg_group = QGroupBox("🐛 Debug")
        dbg_group.setStyleSheet(_GROUP)
        dg_lay = QVBoxLayout(dbg_group)
        self.debug_checkbox = QCheckBox("Enable Debug Mode")
        self.debug_checkbox.setStyleSheet(_CHECK)
        dg_lay.addWidget(self.debug_checkbox)
        dg_lay.addWidget(_info_box(
            "Shows tool usage conversations in a separate window so you can see "
            "what the AI is doing, its inputs, outputs, and decision-making process."))
        sec_lay.addWidget(dbg_group)
        sec_lay.addStretch()
        tabs.addTab(sec_scroll, "🔒  Security")

        # ════════════════════════════════════════════════════════════════════
        # TAB 6 — System
        # ════════════════════════════════════════════════════════════════════
        sys_scroll, sys_lay = _make_scroll_tab()

        tl_group = QGroupBox("🔒 Tool Execution Locking")
        tl_group.setStyleSheet(_GROUP)
        tl_lay = QVBoxLayout(tl_group)
        self.tool_exec_lockout_checkbox = QCheckBox("Enable Tool Execution Lockout")
        self.tool_exec_lockout_checkbox.setStyleSheet(_CHECK)
        tl_lay.addWidget(self.tool_exec_lockout_checkbox)
        tl_lay.addWidget(_info_box(
            "When enabled, the agent will no longer be able to do anything but generate chat responses."))
        sys_lay.addWidget(tl_group)

        sp_group = QGroupBox("🤖 System Prompt Hijacking")
        sp_group.setStyleSheet(_GROUP)
        sp_lay = QVBoxLayout(sp_group)
        self.system_prompt_hijack_checkbox = QCheckBox("Enable System Prompt Hijack")
        self.system_prompt_hijack_checkbox.setStyleSheet(_CHECK)
        sp_lay.addWidget(self.system_prompt_hijack_checkbox)
        sp_lay.addWidget(_info_box(
            "Replace the system prompt with the one below."))
        self.system_prompt_hijack_input = QTextEdit()
        self.system_prompt_hijack_input.setPlaceholderText("Enter your custom system prompt here...")
        self.system_prompt_hijack_input.setFixedHeight(120)
        self.system_prompt_hijack_input.setStyleSheet(_INPUT)
        sp_lay.addWidget(self.system_prompt_hijack_input)
        sys_lay.addWidget(sp_group)

        sp_extras_group = QGroupBox("🧩 Optional System Prompt Sections (Main AI Engine)")
        sp_extras_group.setStyleSheet(_GROUP)
        sp_extras_lay = QVBoxLayout(sp_extras_group)
        self.include_image_tools_checkbox = QCheckBox("Inject Image Tools into system prompt")
        self.include_image_tools_checkbox.setStyleSheet(_CHECK)
        self.include_image_tools_checkbox.setToolTip(
            "Adds image tool instructions to the main AI engine's system prompt.\n"
            "Does NOT affect task sessions — those have their own toggle in the task editor."
        )
        self.include_controller_ref_checkbox = QCheckBox("Inject Controller Reference into system prompt")
        self.include_controller_ref_checkbox.setStyleSheet(_CHECK)
        self.include_controller_ref_checkbox.setToolTip(
            "Adds controller usage reference into the main AI engine's system prompt."
        )
        self.include_notify_tool_checkbox = QCheckBox("Inject Notify Tool into system prompt")
        self.include_notify_tool_checkbox.setStyleSheet(_CHECK)
        self.include_notify_tool_checkbox.setToolTip(
            "Adds notify tool instructions into the main AI engine's system prompt."
        )
        sp_extras_lay.addWidget(self.include_image_tools_checkbox)
        sp_extras_lay.addWidget(self.include_controller_ref_checkbox)
        sp_extras_lay.addWidget(self.include_notify_tool_checkbox)
        sys_lay.addWidget(sp_extras_group)

        # ── Tool Calling Mode ───────────────────────────────────────────────
        tc_group = QGroupBox("🛠 Tool Calling Mode")
        tc_group.setStyleSheet(_GROUP)
        tc_lay = QVBoxLayout(tc_group)
        self.tool_calling_mode_combo = QComboBox()
        self.tool_calling_mode_combo.addItem(
            "Compatibility — fenced tool format in the prompt (works with ANY model)", 'compat')
        self.tool_calling_mode_combo.addItem(
            "Native — provider function calling (lighter prompt; needs a supporting provider)", 'native')
        self.tool_calling_mode_combo.setStyleSheet(_COMBO)
        tc_lay.addWidget(self.tool_calling_mode_combo)
        tc_lay.addWidget(_info_box(
            "Compatibility teaches tools via the system prompt and parses fenced replies — universal, "
            "but costs prompt tokens and can be mis-formatted by weak models.\n"
            "Native passes tools through the provider's function-calling API: smaller prompt and "
            "guaranteed-valid calls — but only works on providers that declare native support. "
            "Providers without it automatically fall back to Compatibility."))
        sys_lay.addWidget(tc_group)

        # ── Code Execution (moved here from General) ─────────────────────────
        exec_group = QGroupBox("Code Execution")
        exec_group.setStyleSheet(_GROUP)
        ex_lay = QVBoxLayout(exec_group)

        _timeout_row = QHBoxLayout()
        _timeout_row.addWidget(_label("Tool execution timeout (seconds):"))
        self.exec_timeout_spin = QSpinBox()
        self.exec_timeout_spin.setRange(10, 3600)
        self.exec_timeout_spin.setValue(300)
        self.exec_timeout_spin.setSingleStep(10)
        self.exec_timeout_spin.setSuffix(" s")
        self.exec_timeout_spin.setFixedWidth(120)
        self.exec_timeout_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QSpinBox:focus {{ border-color: {_ACCENT}; }}
        """)
        _timeout_row.addWidget(self.exec_timeout_spin)
        _timeout_row.addStretch()
        ex_lay.addLayout(_timeout_row)
        ex_lay.addWidget(_info_box(
            "When code execution runs longer than this, you'll be asked "
            "whether to extend the timeout or kill the operation."))
        sys_lay.addWidget(exec_group)

        sys_lay.addStretch()
        tabs.addTab(sys_scroll, "💻 System")

        main_layout.addWidget(tabs, stretch=1)

        # ── Footer (always visible, pinned) ──────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(58)
        footer.setStyleSheet(f"""
            QFrame {{
                background-color: {_BASE};
                border-top: 1px solid {_BORDER};
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }}
        """)
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(16, 0, 16, 0)
        footer_lay.setSpacing(10)

        self.footer_status_label = QLabel("")
        self.footer_status_label.setStyleSheet(f"color:{_MUTED}; font-size:11px; background:transparent;")
        footer_lay.addWidget(self.footer_status_label, stretch=1)

        save_btn = QPushButton("💾  Save Settings")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.setMinimumWidth(140)
        save_btn.clicked.connect(self.save_settings)
        footer_lay.addWidget(save_btn)

        main_layout.addWidget(footer)

    def _on_theme_selected(self, checked, key, card):
        """Handle theme radio button toggle — update card borders."""
        if not checked:
            return
        self._selected_theme = key
        # Update all card borders
        _, _, _ELEV, _BORDER, _ACCENT, _, _ = self._palette()
        for k, c in self._theme_cards.items():
            try:
                c.setStyleSheet(f"""
                    QFrame {{
                        background: {_ELEV};
                        border: 2px solid {"" + _ACCENT + "" if k == key else _BORDER};
                        border-radius: 10px;
                    }}
                    QFrame:hover {{ border-color: {_ACCENT}; }}
                """)
            except RuntimeError:
                pass

    def on_vad_settings_changed(self):
        """Update VAD settings visibility based on toggles"""
        webrtc_enabled = self.webrtc_vad_checkbox.isChecked()
        silero_enabled = self.silero_vad_checkbox.isChecked()

        # Show/hide settings based on toggles
        if webrtc_enabled:
            self.webrtc_settings_widget.show()
        else:
            self.webrtc_settings_widget.hide()

        if silero_enabled:
            self.silero_settings_widget.show()
        else:
            self.silero_settings_widget.hide()

    def log_status(self, message):
        """Helper to log status messages"""
        print(f"[Settings] {message}")

    def _open_memory_window(self):
        """Open the memory manager window."""
        if not hasattr(self, '_memory_window') or not self._memory_window.isVisible():
            from ui.memory_window import MemoryWindow
            self._memory_window = MemoryWindow(self.controller)
        self._memory_window.show()
        self._memory_window.raise_()
        self._memory_window.refresh_memories()

    def _on_llm_provider_changed(self, index):
        """Show the Manual Provider Helper only when Manual Response is selected."""
        is_manual = self.provider_script_combo.currentData() == ""
        self.manual_group.setVisible(is_manual)

    def on_tts_provider_changed(self, index):
        """Show Edge TTS voice dropdown only when Edge TTS is selected."""
        provider_data = self.tts_provider_combo.currentData()
        if provider_data == 'edge-tts':
            self.edge_tts_group.show()
        else:
            self.edge_tts_group.hide()

    def _refresh_prefilling_sessions(self):
        """Populate the prefilling session combo with all saved sessions.
        The currently active session is excluded — if it's selected as the prefill
        source, the engine falls back to the premade PREFILLING automatically."""
        self.pf_session_combo.clear()
        try:
            active_id = getattr(self.controller, 'current_session_id', None)
            sessions = self.controller.get_session_list()
            for s in sessions:
                if s.get('id') == active_id:
                    continue
                label = s.get('name') or s.get('id', '?')
                self.pf_session_combo.addItem(label, s.get('id', ''))
        except Exception as e:
            self.pf_session_combo.addItem(f"(error: {e})", "")

    def _refresh_llm_provider_scripts(self):
        """Populate the LLM provider script combo from providers/large-language-models/."""
        scripts = self.controller.get_llm_provider_scripts()
        current = self.provider_script_combo.currentData() if self.provider_script_combo.count() else ''
        self.provider_script_combo.blockSignals(True)
        self.provider_script_combo.clear()
        self.provider_script_combo.addItem("Manual Response (Debug)", "")
        for s in scripts:
            self.provider_script_combo.addItem(s['name'], s['path'])
        for i in range(self.provider_script_combo.count()):
            if self.provider_script_combo.itemData(i) == current:
                self.provider_script_combo.setCurrentIndex(i)
                break
        self.provider_script_combo.blockSignals(False)

    def _refresh_tts_provider_scripts(self):
        """Populate TTS combo: built-in Edge TTS + scripts from providers/text-to-speech/."""
        scripts = self.controller.get_tts_provider_scripts()
        current = self.tts_provider_combo.currentData() if self.tts_provider_combo.count() else 'edge-tts'
        self.tts_provider_combo.blockSignals(True)
        self.tts_provider_combo.clear()
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        for s in scripts:
            self.tts_provider_combo.addItem(f"📝  {s['name']}", s['path'])
        restored = False
        for i in range(self.tts_provider_combo.count()):
            if self.tts_provider_combo.itemData(i) == current:
                self.tts_provider_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored:
            self.tts_provider_combo.setCurrentIndex(0)
        self.tts_provider_combo.blockSignals(False)
        self.on_tts_provider_changed(self.tts_provider_combo.currentIndex())

    def refresh_audio_devices(self):
        """Refresh audio device lists"""
        try:
            input_devices, output_devices = self.controller.get_voice_devices()

            # Clear combos
            self.input_device_combo.clear()
            self.output_device_combo.clear()

            # Add default option
            self.input_device_combo.addItem("Default Input Device", None)
            self.output_device_combo.addItem("Default Output Device", None)

            # Add input devices
            for device in input_devices:
                self.input_device_combo.addItem(
                    f"{device['name']} ({device['channels']} ch)",
                    device['id']
                )

            # Add output devices
            for device in output_devices:
                self.output_device_combo.addItem(
                    f"{device['name']} ({device['channels']} ch)",
                    device['id']
                )

            self.show_status_message("✓ Audio devices refreshed!")

        except Exception as e:
            self.show_status_message(f"✗ Error refreshing devices: {e}")

    def load_settings(self):
        """Load settings from controller"""
        # Load General settings
        self.open_chat_on_startup_checkbox.setChecked(
            self.controller.settings.get('open_chat_on_startup', False)
        )
        self.open_packet_on_startup_checkbox.setChecked(
            self.controller.settings.get('open_packet_on_startup', False)
        )
        self.show_token_count_checkbox.setChecked(
            self.controller.settings.get('show_token_count', True)
        )
        self.exec_timeout_spin.setValue(
            self.controller.settings.get('tool_execution_timeout_seconds', 300)
        )
        _tc_mode = self.controller.settings.get('tool_calling_mode', 'compat')
        _tc_idx = self.tool_calling_mode_combo.findData(_tc_mode)
        self.tool_calling_mode_combo.setCurrentIndex(_tc_idx if _tc_idx >= 0 else 0)

        # Load active LLM provider script
        self._refresh_llm_provider_scripts()
        self._on_llm_provider_changed(self.provider_script_combo.currentIndex())
        saved_script_path = self.controller.get_custom_script_path()
        if saved_script_path:
            for i in range(self.provider_script_combo.count()):
                if self.provider_script_combo.itemData(i) == saved_script_path:
                    self.provider_script_combo.setCurrentIndex(i)
                    break

        # Load debug mode
        debug_mode = self.controller.get_debug_mode()
        self.debug_checkbox.setChecked(debug_mode)

        # Load supervised execution mode
        supervised_mode = self.controller.settings.get('supervised_execution', True)  # Default ON
        self.supervised_checkbox.setChecked(supervised_mode)

        # Load Tool lockout switch
        tool_exec_locked = self.controller.settings.get('tool_execution_lockout', False)
        self.tool_exec_lockout_checkbox.setChecked(tool_exec_locked)

        # Load system prompt hijacking
        sys_prompt_hijacked = self.controller.settings.get('system_prompt_hijacked', False)
        self.system_prompt_hijack_checkbox.setChecked(sys_prompt_hijacked)
        self.include_image_tools_checkbox.setChecked(
            self.controller.settings.get('include_image_tools', False))
        self.include_controller_ref_checkbox.setChecked(
            self.controller.settings.get('include_controller_ref', False))
        self.include_notify_tool_checkbox.setChecked(
            self.controller.settings.get('include_notify_tool', False))

        # Load system prompt
        self.system_prompt_hijack_input.setPlainText(
            self.controller.settings.get('custom_system_prompt', '')
        )

        # Load interrupt mode
        interrupt_mode = self.controller.settings.get('voice_interrupt_mode', 'manual')
        index = self.interrupt_mode_combo.findData(interrupt_mode)
        if index >= 0:
            self.interrupt_mode_combo.setCurrentIndex(index)

        # Load voice devices
        self.refresh_audio_devices()

        # Load voice settings
        input_device = self.controller.settings.get('voice_input_device')
        output_device = self.controller.settings.get('voice_output_device')

        # Load ElevenLabs speech tag toggle
        self.elevenlabs_tags_checkbox.setChecked(
            self.controller.settings.get('elevenlabs_enabled', False)
        )

        # Load TTS provider/script
        self._refresh_tts_provider_scripts()
        tts_provider = self.controller.get_tts_provider()
        tts_script = self.controller.settings.get('tts_script_path', '')
        restore_data = tts_script if (tts_provider == 'custom_script' and tts_script) else tts_provider
        for i in range(self.tts_provider_combo.count()):
            if self.tts_provider_combo.itemData(i) == restore_data:
                self.tts_provider_combo.setCurrentIndex(i)
                break

        # Load prefilling settings
        self.prefilling_checkbox.setChecked(
            self.controller.settings.get('prefilling_enabled', True)
        )
        _pf_mode = self.controller.settings.get('prefilling_mode', 'premade')
        if _pf_mode == 'session':
            self.pf_radio_session.setChecked(True)
        else:
            self.pf_radio_premade.setChecked(True)
        self._refresh_prefilling_sessions()
        _pf_sid = self.controller.settings.get('prefilling_session_id', '')
        for i in range(self.pf_session_combo.count()):
            if self.pf_session_combo.itemData(i) == _pf_sid:
                self.pf_session_combo.setCurrentIndex(i)
                break

        # Load memory engine settings
        self.memory_enabled_checkbox.setChecked(
            self.controller.settings.get('memory_enabled', True)
        )
        threshold = self.controller.settings.get('memory_threshold', 0.4)
        for i in range(self.memory_threshold_combo.count()):
            if self.memory_threshold_combo.itemData(i) == threshold:
                self.memory_threshold_combo.setCurrentIndex(i)
                break
        max_results = self.controller.settings.get('memory_max_results', 5)
        for i in range(self.memory_max_combo.count()):
            if self.memory_max_combo.itemData(i) == max_results:
                self.memory_max_combo.setCurrentIndex(i)
                break
        recall_mode = self.controller.settings.get('memory_recall_mode', 'inject_all')
        for i in range(self.memory_recall_mode_combo.count()):
            if self.memory_recall_mode_combo.itemData(i) == recall_mode:
                self.memory_recall_mode_combo.setCurrentIndex(i)
                break

        # Load selected theme
        saved_theme = self.controller.settings.get('chat_theme', 'obsidian_blue')
        self._selected_theme = saved_theme
        if hasattr(self, '_theme_btn_group'):
            for btn in self._theme_btn_group.buttons():
                if btn.property('theme_key') == saved_theme:
                    btn.setChecked(True)
                    break
        if hasattr(self, '_theme_cards'):
            _, _, _ELEV, _BORDER, _ACCENT, _, _ = self._palette()
            for k, c in self._theme_cards.items():
                try:
                    c.setStyleSheet(
                        f"QFrame {{ background:{_ELEV}; border:2px solid {(_ACCENT if k == saved_theme else _BORDER)}; border-radius:10px; }}"
                        f"QFrame:hover {{ border-color:{_ACCENT}; }}"
                    )
                except RuntimeError:
                    pass

        # Load glass background settings
        glass_enabled = self.controller.settings.get('glass_background_enabled', False)
        self.glass_enabled_checkbox.setChecked(glass_enabled)
        glass_opacity = self.controller.settings.get('glass_background_opacity', 0.75)
        self.glass_opacity_slider.setValue(int(glass_opacity * 100))
        self.glass_opacity_value_label.setText(f"{int(glass_opacity * 100)}%")
        # Per-window glass checklist
        _glass_wins = _theme.glass_windows(self.controller)
        for _wkey, _cb in getattr(self, 'glass_window_checkboxes', {}).items():
            _cb.setChecked(_wkey in _glass_wins)
        if hasattr(self, 'glass_sidebar_checkbox'):
            self.glass_sidebar_checkbox.setChecked(
                self.controller.settings.get('glass_chat_sidebar', True))
        if hasattr(self, '_sync_glass_checklist_enabled'):
            self._sync_glass_checklist_enabled()

        if input_device is not None:
            index = self.input_device_combo.findData(input_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        if output_device is not None:
            index = self.output_device_combo.findData(output_device)
            if index >= 0:
                self.output_device_combo.setCurrentIndex(index)

        # CRITICAL FIX: Load VAD and TTS voice properly
        vad_level = self.controller.settings.get('vad_aggressiveness', 3)
        for i in range(self.vad_combo.count()):
            if self.vad_combo.itemData(i) == vad_level:
                self.vad_combo.setCurrentIndex(i)
                break

        tts_voice = self.controller.settings.get('tts_voice', 'en-CA-ClaraNeural')
        for i in range(self.tts_voice_combo.count()):
            if self.tts_voice_combo.itemData(i) == tts_voice:
                self.tts_voice_combo.setCurrentIndex(i)
                break

        # Load VAD settings
        webrtc_enabled = self.controller.settings.get('vad_webrtc_enabled', True)
        silero_enabled = self.controller.settings.get('vad_silero_enabled', False)
        silero_threshold = self.controller.settings.get('vad_silero_threshold', 0.5)

        self.webrtc_vad_checkbox.setChecked(webrtc_enabled)
        self.silero_vad_checkbox.setChecked(silero_enabled)

        # Set WebRTC aggressiveness
        vad_level = self.controller.settings.get('vad_aggressiveness', 3)
        for i in range(self.vad_combo.count()):
            if self.vad_combo.itemData(i) == vad_level:
                self.vad_combo.setCurrentIndex(i)
                break

        # Set Silero threshold
        for i in range(self.silero_threshold_combo.count()):
            if self.silero_threshold_combo.itemData(i) == silero_threshold:
                self.silero_threshold_combo.setCurrentIndex(i)
                break

        # Update visibility
        self.on_vad_settings_changed()

    def save_settings(self):
        """Save settings to controller"""
        # Save General settings
        self.controller.settings['open_chat_on_startup'] = self.open_chat_on_startup_checkbox.isChecked()
        self.controller.settings['open_packet_on_startup'] = self.open_packet_on_startup_checkbox.isChecked()
        self.controller.settings['show_token_count'] = self.show_token_count_checkbox.isChecked()
        try:
            chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
            if chat_win and hasattr(chat_win, '_token_count_lbl'):
                chat_win._token_count_lbl.setVisible(self.show_token_count_checkbox.isChecked())
        except Exception:
            pass
        self.controller.settings['tool_execution_timeout_seconds'] = self.exec_timeout_spin.value()
        self.controller.settings['tool_calling_mode'] = self.tool_calling_mode_combo.currentData()

        # Save active LLM provider script
        script_path = self.provider_script_combo.currentData() or ''
        if not script_path:
            self.controller.set_ai_provider('manual')
        elif script_path:
            self.controller.set_ai_provider('custom_script')
            self.controller.set_custom_script_path(script_path)

        # Save debug mode
        debug_mode = self.debug_checkbox.isChecked()
        self.controller.set_debug_mode(debug_mode)

        # Save supervised execution mode
        supervised_mode = self.supervised_checkbox.isChecked()
        self.controller.settings['supervised_execution'] = supervised_mode

        # Save system prompt hijacking and Tool lockout switch
        self.controller.set_tool_execution_lockout(self.tool_exec_lockout_checkbox.isChecked())
        self.controller.set_system_prompt_hijack(
            self.system_prompt_hijack_checkbox.isChecked(),
            self.system_prompt_hijack_input.toPlainText()
        )
        self.controller.set_system_prompt_extras(
            include_image_tools=self.include_image_tools_checkbox.isChecked(),
            include_controller_ref=self.include_controller_ref_checkbox.isChecked(),
            include_notify_tool=self.include_notify_tool_checkbox.isChecked(),
        )

        # Save custom system prompt
        self.controller.settings['custom_system_prompt'] = self.system_prompt_hijack_input.toPlainText()

        # Save voice devices
        input_device = self.input_device_combo.currentData()
        self.controller.set_voice_input_device(input_device)

        output_device = self.output_device_combo.currentData()
        self.controller.set_voice_output_device(output_device)

        # Save TTS provider / script
        tts_data = self.tts_provider_combo.currentData() or 'edge-tts'
        if tts_data == 'edge-tts':
            self.controller.set_tts_provider('edge-tts')
        else:
            # tts_data is a script path
            self.controller.set_tts_provider('custom_script')
            self.controller.set_tts_script_path(tts_data)

        # Save ElevenLabs speech tag toggle
        elevenlabs_enabled = self.elevenlabs_tags_checkbox.isChecked()
        self.controller.settings['elevenlabs_enabled'] = elevenlabs_enabled
        self.controller.ai.update_voice_settings(self.controller.ai.voice_mode, elevenlabs_enabled)

        # Save TTS voice (edge-tts only)
        tts_voice = self.tts_voice_combo.currentData()
        self.controller.set_tts_voice(tts_voice)

        # Save VAD aggressiveness
        vad_level = self.vad_combo.currentData()
        self.controller.set_vad_aggressiveness(vad_level)

        # Save interrupt mode
        interrupt_mode = self.interrupt_mode_combo.currentData()
        self.controller.set_voice_interrupt_mode(interrupt_mode)

        # Save memory engine settings
        self.controller.settings['prefilling_enabled'] = self.prefilling_checkbox.isChecked()
        self.controller.settings['prefilling_mode'] = (
            'session' if self.pf_radio_session.isChecked() else 'premade')
        self.controller.settings['prefilling_session_id'] = (
                self.pf_session_combo.currentData() or '')
        self.controller.settings['memory_enabled'] = self.memory_enabled_checkbox.isChecked()
        self.controller.settings['memory_recall_mode'] = self.memory_recall_mode_combo.currentData()
        self.controller.settings['memory_threshold'] = self.memory_threshold_combo.currentData()
        self.controller.settings['memory_max_results'] = self.memory_max_combo.currentData()

        # Save selected theme (broadcast happens at the end, after all widget
        # reads + persistence, so the live retint can't disturb this save).
        theme = getattr(self, '_selected_theme', 'obsidian_blue')
        self.controller.settings['chat_theme'] = theme

        # Save glass background settings
        glass_enabled = self.glass_enabled_checkbox.isChecked()
        glass_opacity = self.glass_opacity_slider.value() / 100.0
        glass_windows = [
            _wkey for _wkey, _cb in getattr(self, 'glass_window_checkboxes', {}).items()
            if _cb.isChecked()
        ]
        self.controller.settings['glass_background_enabled'] = glass_enabled
        self.controller.settings['glass_background_opacity'] = glass_opacity
        self.controller.settings['glass_windows'] = glass_windows
        if hasattr(self, 'glass_sidebar_checkbox'):
            self.controller.settings['glass_chat_sidebar'] = self.glass_sidebar_checkbox.isChecked()

        # Apply glass to the chat window immediately — gated by its checklist entry.
        try:
            if hasattr(self.controller, 'ui') and self.controller.ui:
                chat_win = getattr(self.controller.ui, 'chat_window', None)
                if chat_win and hasattr(chat_win, 'apply_glass_background'):
                    chat_glass = glass_enabled and ('chat' in glass_windows)
                    chat_win.apply_glass_background(chat_glass, glass_opacity)
        except Exception as _ge:
            pass

        # Save VAD settings
        webrtc_enabled = self.webrtc_vad_checkbox.isChecked()
        silero_enabled = self.silero_vad_checkbox.isChecked()
        vad_level = self.vad_combo.currentData()
        silero_threshold = self.silero_threshold_combo.currentData()

        self.controller.settings['vad_webrtc_enabled'] = webrtc_enabled
        self.controller.settings['vad_silero_enabled'] = silero_enabled
        self.controller.settings['vad_aggressiveness'] = vad_level
        self.controller.settings['vad_silero_threshold'] = silero_threshold
        self.controller.save_settings()

        # Apply to voice handler
        self.controller.set_vad_aggressiveness(vad_level)

        # Broadcast the theme to every open window (incl. this one) for instant
        # unity. Done last so the live retint can't disturb any widget reads.
        try:
            if hasattr(self.controller, 'broadcast_theme'):
                self.controller.broadcast_theme(theme)
            else:
                chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
                if chat_win and hasattr(chat_win, 'apply_theme'):
                    chat_win.apply_theme(theme)
        except Exception:
            pass

        # Show confirmation (footer was just rebuilt by the retint — safe)
        self.show_status_message("✓ Settings saved successfully!")

    def _set_tok_graph_mode(self, mode):
        """Switch the token graph time mode and refresh."""
        self._tok_graph_mode = mode
        for m, btn in self._tok_mode_btns.items():
            btn.setChecked(m == mode)
        self._refresh_tok_graph()

    def _refresh_tok_graph(self):
        """Reload token usage data from disk and repaint the graph."""
        try:
            from core.token_est import get_usage_data, get_output_usage_data
            mode = getattr(self, '_tok_graph_mode', 'Daily')
            data = get_usage_data(mode)
            self._tok_canvas.set_data(data, mode)
            total = sum(v for _, v in data)
            self._tok_summary_lbl.setText(
                f"Input ({mode.lower()}): ~{total:,} tokens  ·  {len(data)} bucket(s)")
            out_data = get_output_usage_data(mode)
            self._tok_out_canvas.set_data(out_data, mode)
            out_total = sum(v for _, v in out_data)
            self._tok_out_summary_lbl.setText(
                f"Output ({mode.lower()}): ~{out_total:,} tokens  ·  {len(out_data)} bucket(s)")
        except Exception:
            pass

    def show_status_message(self, message):
        """Show a temporary status message in the footer."""
        print(f"[Settings] {message}")
        try:
            if hasattr(self, 'footer_status_label'):
                self.footer_status_label.setText(message)
                QTimer.singleShot(3000, lambda: (
                    self.footer_status_label.setText("") if hasattr(self, 'footer_status_label') else None
                ))
        except RuntimeError:
            pass

    def apply_rounded_mask(self):
        """Apply rounded corners mask"""
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def showEvent(self, event):
        """Sync container background to the active theme on show."""
        super().showEvent(event)
        try:
            self._refresh_tok_graph()
        except Exception:
            pass
        try:
            p = _theme.current_palette(self.controller)
            self.container.setStyleSheet(f"""
                QWidget#container {{
                    background-color: {p['bg']};
                    border-radius: 12px;
                }}
                QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}
                QScrollArea {{ background-color: {p['surface']}; }}
                QScrollArea > QWidget {{ background-color: {p['surface']}; }}
            """)
        except Exception:
            pass

    def apply_theme(self, theme_key=None):
        """Live-retint the settings window. Rebuilds the tabbed UI in place from
        the active theme, preserving the current tab and re-loading values."""
        try:
            saved_tab = self._tabs.currentIndex() if hasattr(self, '_tabs') else 0
            old = self.container
            self.layout().removeWidget(old)
            old.deleteLater()

            p = _theme.current_palette(self.controller)
            self.container = QWidget()
            self.container.setObjectName("container")
            self.container.setAutoFillBackground(True)
            self.container.setStyleSheet(f"""
                QWidget#container {{ background-color: {p['bg']}; border-radius: 12px; }}
                QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}
            """)
            self.init_ui()
            self.load_settings()
            self.layout().addWidget(self.container)
            if hasattr(self, '_tabs') and 0 <= saved_tab < self._tabs.count():
                self._tabs.setCurrentIndex(saved_tab)
            # Repopulate the token graph — the rebuild creates fresh empty canvases
            # and showEvent won't fire (the window is already visible on retint).
            self._refresh_tok_graph()
            self.apply_rounded_mask()
            self._sync_glass()
        except Exception as e:
            print(f"[SettingsWindow.apply_theme] {e}")

