"""
ui/settings_window.py
Settings Window - Configure API key, AI provider, Puter.js settings, Google Gemini, and Voice
FIXED: Voice settings now actually work - VAD and TTS voice selection are functional
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QComboBox, QGroupBox,
                             QCheckBox, QScrollArea, QFrame, QSlider, QSpinBox, QDoubleSpinBox,
                             QPlainTextEdit, QFileDialog)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect
from PyQt6.QtGui import QRegion
from core.puter_server import DEBUG_MODE
from ui.base_window import BaseWindow


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

    def init_ui(self):
        """Initialize tabbed settings UI"""
        from PyQt6.QtWidgets import QTabWidget, QSlider, QRadioButton, QButtonGroup, QGridLayout
        from PyQt6.QtCore import Qt as _Qt

        # ── Palette ─────────────────────────────────────────────────────────
        _BASE    = "#0D1117"
        _SURFACE = "#161B22"
        _ELEV    = "#21262D"
        _BORDER  = "#30363D"
        _ACCENT  = "#58A6FF"
        _TEXT    = "#E6EDF3"
        _MUTED   = "#8B949E"

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
                background-color: #2D333B;
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
                color: #000d1a;
            }}
            QPushButton:hover {{ background-color: #79BFFF; }}
            QPushButton:pressed {{ background-color: #388BFD; }}
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

        # Provider
        provider_group = QGroupBox("AI Provider")
        provider_group.setStyleSheet(_GROUP)
        pg_lay = QVBoxLayout(provider_group)
        pg_lay.addWidget(_label("Select AI Provider:"))
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["anthropic", "gemini", "puter", "manual", "custom_script"])
        self.provider_combo.currentTextChanged.connect(self.on_provider_changed)
        self.provider_combo.setStyleSheet(_COMBO)
        pg_lay.addWidget(self.provider_combo)
        pg_lay.addWidget(_info_box(
            "💡 Anthropic: Claude API  |  Gemini: Google AI  |  Puter.js: Free browser-based AI"))
        ai_lay.addWidget(provider_group)

        # Anthropic
        self.anthropic_group = QGroupBox("Anthropic (Claude) API")
        self.anthropic_group.setStyleSheet(_GROUP)
        an_lay = QVBoxLayout(self.anthropic_group)
        an_lay.addWidget(_label("API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-ant-...")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setStyleSheet(_INPUT)
        an_lay.addWidget(self.api_key_input)
        _show_row = QHBoxLayout()
        self.show_key_btn = QPushButton("👁️ Show")
        self.show_key_btn.setMaximumWidth(80)
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setStyleSheet(_BTN)
        self.show_key_btn.toggled.connect(self.toggle_api_key_visibility)
        _show_row.addWidget(self.show_key_btn)
        _show_row.addStretch()
        an_lay.addLayout(_show_row)
        # Model
        an_lay.addWidget(_label("Model:", top_margin=6))
        self.anthropic_model_combo = QComboBox()
        self.anthropic_model_combo.setStyleSheet(_COMBO)
        for model in self.controller.get_anthropic_models():
            self.anthropic_model_combo.addItem(model["name"], model["id"])
        an_lay.addWidget(self.anthropic_model_combo)
        self.anthropic_model_desc = QLabel()
        self.anthropic_model_desc.setWordWrap(True)
        self.anthropic_model_desc.setStyleSheet(f"color:{_MUTED}; font-size:9pt; font-style:italic;")
        self.anthropic_model_combo.currentIndexChanged.connect(self.update_anthropic_model_description)
        an_lay.addWidget(self.anthropic_model_desc)
        # Temperature
        an_lay.addWidget(_label("Temperature  (0 = precise/deterministic · 1 = creative):", top_margin=6))
        _an_temp_row = QHBoxLayout()
        self.anthropic_temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.anthropic_temp_slider.setRange(0, 100)
        self.anthropic_temp_slider.setValue(100)
        self.anthropic_temp_slider.setTickInterval(10)
        self.anthropic_temp_label = QLabel("1.00")
        self.anthropic_temp_label.setFixedWidth(38)
        self.anthropic_temp_label.setStyleSheet(f"color:{_ACCENT}; font-weight:bold;")
        self.anthropic_temp_slider.valueChanged.connect(
            lambda v: self.anthropic_temp_label.setText(f"{v/100:.2f}"))
        _an_temp_row.addWidget(self.anthropic_temp_slider)
        _an_temp_row.addWidget(self.anthropic_temp_label)
        an_lay.addLayout(_an_temp_row)
        # Auto tokens
        self.anthropic_auto_tokens_cb = QCheckBox(
            "🤖  Auto response length  (AI figures out a smart limit — recommended)")
        self.anthropic_auto_tokens_cb.setChecked(True)
        self.anthropic_auto_tokens_cb.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        an_lay.addWidget(self.anthropic_auto_tokens_cb)
        # Manual max tokens (shown/hidden based on checkbox)
        _an_tok_row = QHBoxLayout()
        _an_tok_row.addWidget(_label("Max response tokens:"))
        self.anthropic_max_tokens_spin = QSpinBox()
        self.anthropic_max_tokens_spin.setRange(256, 64000)
        self.anthropic_max_tokens_spin.setValue(8192)
        self.anthropic_max_tokens_spin.setSingleStep(512)
        self.anthropic_max_tokens_spin.setStyleSheet(_INPUT)
        self.anthropic_max_tokens_spin.setFixedWidth(110)
        _an_tok_row.addWidget(self.anthropic_max_tokens_spin)
        _an_tok_row.addStretch()
        self.anthropic_max_tokens_widget = QWidget()
        self.anthropic_max_tokens_widget.setLayout(_an_tok_row)
        self.anthropic_max_tokens_widget.setVisible(False)
        an_lay.addWidget(self.anthropic_max_tokens_widget)
        self.anthropic_auto_tokens_cb.toggled.connect(
            lambda checked: self.anthropic_max_tokens_widget.setVisible(not checked))
        an_lay.addWidget(_label(
            "Get your key: console.anthropic.com", muted=True, top_margin=6))
        ai_lay.addWidget(self.anthropic_group)

        # Gemini
        self.gemini_group = QGroupBox("Google Gemini (AI Studio)")
        self.gemini_group.setStyleSheet(_GROUP)
        gm_lay = QVBoxLayout(self.gemini_group)
        gm_lay.addWidget(_info_box(
            "🔸 Free tier available!\nGet your key from Google AI Studio."))
        gm_lay.addWidget(_label("API Key:"))
        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setPlaceholderText("AIza...")
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setStyleSheet(_INPUT)
        gm_lay.addWidget(self.gemini_key_input)
        _gshow_row = QHBoxLayout()
        self.show_gemini_key_btn = QPushButton("👁️ Show")
        self.show_gemini_key_btn.setMaximumWidth(80)
        self.show_gemini_key_btn.setCheckable(True)
        self.show_gemini_key_btn.setStyleSheet(_BTN)
        self.show_gemini_key_btn.toggled.connect(self.toggle_gemini_key_visibility)
        _gshow_row.addWidget(self.show_gemini_key_btn)
        _gshow_row.addStretch()
        gm_lay.addLayout(_gshow_row)
        # Model
        gm_lay.addWidget(_label("Model:", top_margin=6))
        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.setStyleSheet(_COMBO)
        for model in self.controller.get_gemini_models():
            self.gemini_model_combo.addItem(model["name"], model["id"])
        gm_lay.addWidget(self.gemini_model_combo)
        self.gemini_model_desc = QLabel()
        self.gemini_model_desc.setWordWrap(True)
        self.gemini_model_desc.setStyleSheet(f"color:{_MUTED}; font-size:9pt; font-style:italic;")
        self.gemini_model_combo.currentIndexChanged.connect(self.update_gemini_model_description)
        gm_lay.addWidget(self.gemini_model_desc)
        # Temperature (Gemini supports 0–2)
        gm_lay.addWidget(_label("Temperature  (0 = precise · 2 = very creative):", top_margin=6))
        _gm_temp_row = QHBoxLayout()
        self.gemini_temp_slider = QSlider(Qt.Orientation.Horizontal)
        self.gemini_temp_slider.setRange(0, 200)
        self.gemini_temp_slider.setValue(100)
        self.gemini_temp_slider.setTickInterval(20)
        self.gemini_temp_label = QLabel("1.00")
        self.gemini_temp_label.setFixedWidth(38)
        self.gemini_temp_label.setStyleSheet(f"color:{_ACCENT}; font-weight:bold;")
        self.gemini_temp_slider.valueChanged.connect(
            lambda v: self.gemini_temp_label.setText(f"{v/100:.2f}"))
        _gm_temp_row.addWidget(self.gemini_temp_slider)
        _gm_temp_row.addWidget(self.gemini_temp_label)
        gm_lay.addLayout(_gm_temp_row)
        # Auto tokens
        self.gemini_auto_tokens_cb = QCheckBox(
            "🤖  Auto response length  (AI figures out a smart limit — recommended)")
        self.gemini_auto_tokens_cb.setChecked(True)
        self.gemini_auto_tokens_cb.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        gm_lay.addWidget(self.gemini_auto_tokens_cb)
        # Manual max tokens
        _gm_tok_row = QHBoxLayout()
        _gm_tok_row.addWidget(_label("Max response tokens:"))
        self.gemini_max_tokens_spin = QSpinBox()
        self.gemini_max_tokens_spin.setRange(256, 65536)
        self.gemini_max_tokens_spin.setValue(8192)
        self.gemini_max_tokens_spin.setSingleStep(512)
        self.gemini_max_tokens_spin.setStyleSheet(_INPUT)
        self.gemini_max_tokens_spin.setFixedWidth(110)
        _gm_tok_row.addWidget(self.gemini_max_tokens_spin)
        _gm_tok_row.addStretch()
        self.gemini_max_tokens_widget = QWidget()
        self.gemini_max_tokens_widget.setLayout(_gm_tok_row)
        self.gemini_max_tokens_widget.setVisible(False)
        gm_lay.addWidget(self.gemini_max_tokens_widget)
        self.gemini_auto_tokens_cb.toggled.connect(
            lambda checked: self.gemini_max_tokens_widget.setVisible(not checked))
        # Advanced: top_p / top_k (Gemini-exclusive)
        _gm_adv_header = QHBoxLayout()
        self.gemini_adv_toggle_btn = QPushButton("▸  Advanced sampling (top_p / top_k)")
        self.gemini_adv_toggle_btn.setCheckable(True)
        self.gemini_adv_toggle_btn.setStyleSheet(
            f"QPushButton{{background:transparent; border:none; color:{_MUTED}; "
            f"font-size:9pt; text-align:left; padding:0;}}"
            f"QPushButton:hover{{color:{_TEXT};}}")
        _gm_adv_header.addWidget(self.gemini_adv_toggle_btn)
        _gm_adv_header.addStretch()
        gm_lay.addLayout(_gm_adv_header)
        self.gemini_adv_widget = QWidget()
        _gm_adv_lay = QVBoxLayout(self.gemini_adv_widget)
        _gm_adv_lay.setContentsMargins(0, 0, 0, 0)
        _gm_adv_lay.addWidget(_label(
            "top_p  (nucleus sampling — 0 to 1, lower = more focused, None = API default):", muted=True))
        _gm_top_p_row = QHBoxLayout()
        self.gemini_top_p_enable = QCheckBox("Enable")
        self.gemini_top_p_enable.setStyleSheet(f"color:{_TEXT};")
        self.gemini_top_p_spin = QDoubleSpinBox()
        self.gemini_top_p_spin.setRange(0.0, 1.0)
        self.gemini_top_p_spin.setSingleStep(0.05)
        self.gemini_top_p_spin.setValue(0.95)
        self.gemini_top_p_spin.setDecimals(2)
        self.gemini_top_p_spin.setStyleSheet(_INPUT)
        self.gemini_top_p_spin.setFixedWidth(80)
        self.gemini_top_p_spin.setEnabled(False)
        self.gemini_top_p_enable.toggled.connect(self.gemini_top_p_spin.setEnabled)
        _gm_top_p_row.addWidget(self.gemini_top_p_enable)
        _gm_top_p_row.addWidget(self.gemini_top_p_spin)
        _gm_top_p_row.addStretch()
        _gm_adv_lay.addLayout(_gm_top_p_row)
        _gm_adv_lay.addWidget(_label(
            "top_k  (vocabulary sampling — integer, lower = stricter, None = API default):", muted=True))
        _gm_top_k_row = QHBoxLayout()
        self.gemini_top_k_enable = QCheckBox("Enable")
        self.gemini_top_k_enable.setStyleSheet(f"color:{_TEXT};")
        self.gemini_top_k_spin = QSpinBox()
        self.gemini_top_k_spin.setRange(1, 200)
        self.gemini_top_k_spin.setValue(40)
        self.gemini_top_k_spin.setStyleSheet(_INPUT)
        self.gemini_top_k_spin.setFixedWidth(80)
        self.gemini_top_k_spin.setEnabled(False)
        self.gemini_top_k_enable.toggled.connect(self.gemini_top_k_spin.setEnabled)
        _gm_top_k_row.addWidget(self.gemini_top_k_enable)
        _gm_top_k_row.addWidget(self.gemini_top_k_spin)
        _gm_top_k_row.addStretch()
        _gm_adv_lay.addLayout(_gm_top_k_row)
        self.gemini_adv_widget.setVisible(False)
        gm_lay.addWidget(self.gemini_adv_widget)
        self.gemini_adv_toggle_btn.toggled.connect(lambda checked: (
            self.gemini_adv_widget.setVisible(checked),
            self.gemini_adv_toggle_btn.setText(
                ("▾  Advanced sampling (top_p / top_k)" if checked
                 else "▸  Advanced sampling (top_p / top_k)"))
        ))
        lnk = QLabel(f'<a href="https://aistudio.google.com/apikey" style="color:{_ACCENT};">Get key: aistudio.google.com</a>')
        lnk.setOpenExternalLinks(True)
        gm_lay.addWidget(lnk)
        ai_lay.addWidget(self.gemini_group)

        # Puter
        self.puter_group = QGroupBox("Puter.js Configuration")
        self.puter_group.setStyleSheet(_GROUP)
        pt_lay = QVBoxLayout(self.puter_group)
        pt_lay.addWidget(_info_box(
            "Uses Puter.js free rate-limited API. [HIGHLY SUPPORTED]"))
        pt_lay.addWidget(_label("Model:"))
        self.puter_model_combo = QComboBox()
        self.puter_model_combo.setStyleSheet(_COMBO)
        for model in self.controller.get_puter_models():
            self.puter_model_combo.addItem(model['name'], model['id'])
        pt_lay.addWidget(self.puter_model_combo)
        self.puter_model_desc = QLabel()
        self.puter_model_desc.setWordWrap(True)
        self.puter_model_desc.setStyleSheet(f"color:{_MUTED}; font-size:9pt; font-style:italic;")
        self.puter_model_combo.currentIndexChanged.connect(self.update_model_description)
        pt_lay.addWidget(self.puter_model_desc)
        _pbt_row = QHBoxLayout()
        start_puter_btn = QPushButton("▶  Start Server")
        start_puter_btn.clicked.connect(self.start_puter_server)
        start_puter_btn.setStyleSheet(f"""
            QPushButton {{ background:#1A3A1A; border:1px solid #2a5a2a; border-radius:6px;
                          color:#4CAF50; padding:7px 14px; font-size:11px; }}
            QPushButton:hover {{ background:#223322; }}
        """)
        _pbt_row.addWidget(start_puter_btn)
        open_puter_btn = QPushButton("🌐  Open Interface")
        open_puter_btn.clicked.connect(self.open_puter_interface)
        open_puter_btn.setStyleSheet(_BTN)
        _pbt_row.addWidget(open_puter_btn)
        _pbt_row.addStretch()
        pt_lay.addLayout(_pbt_row)
        ai_lay.addWidget(self.puter_group)

        # Puter debug account group
        from core.puter_server import DEBUG_MODE
        if DEBUG_MODE:
            self.puter_account_group = QGroupBox("Puter.js Account")
            self.puter_account_group.setStyleSheet(_GROUP)
            pa_lay = QVBoxLayout(self.puter_account_group)
            pa_lay.addWidget(_label("Email:"))
            self.puter_email_input = QLineEdit()
            self.puter_email_input.setStyleSheet(_INPUT)
            pa_lay.addWidget(self.puter_email_input)
            pa_lay.addWidget(_label("Password:"))
            self.puter_password_input = QLineEdit()
            self.puter_password_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.puter_password_input.setStyleSheet(_INPUT)
            pa_lay.addWidget(self.puter_password_input)
            _ppw_row = QHBoxLayout()
            self.show_puter_password_btn = QPushButton("👁️ Show")
            self.show_puter_password_btn.setMaximumWidth(80)
            self.show_puter_password_btn.setCheckable(True)
            self.show_puter_password_btn.setStyleSheet(_BTN)
            self.show_puter_password_btn.toggled.connect(self.toggle_puter_password_visibility)
            _ppw_row.addWidget(self.show_puter_password_btn)
            _ppw_row.addStretch()
            pa_lay.addLayout(_ppw_row)
            pa_lay.addWidget(_label("Response Timeout (seconds):", bold=True, top_margin=10))
            _to_row = QHBoxLayout()
            self.puter_timeout_input = QLineEdit()
            self.puter_timeout_input.setPlaceholderText("30")
            self.puter_timeout_input.setMaximumWidth(90)
            self.puter_timeout_input.setStyleSheet(_INPUT)
            _to_row.addWidget(self.puter_timeout_input)
            _to_row.addWidget(_label("How long to wait for AI response", muted=True))
            _to_row.addStretch()
            pa_lay.addLayout(_to_row)
            _pa_btns = QHBoxLayout()
            setup_account_btn = QPushButton("🆕 Setup Account")
            setup_account_btn.setStyleSheet(_BTN)
            setup_account_btn.clicked.connect(self.setup_puter_account)
            _pa_btns.addWidget(setup_account_btn)
            reset_quota_btn = QPushButton("♻️ Reset Quota")
            reset_quota_btn.setStyleSheet(f"""
                QPushButton {{ background:#3A2000; border:1px solid #ff9900; border-radius:6px;
                              color:#ff9900; padding:7px 14px; font-size:11px; }}
                QPushButton:hover {{ background:#4A2800; }}
            """)
            reset_quota_btn.clicked.connect(self.reset_puter_quota)
            _pa_btns.addWidget(reset_quota_btn)
            _pa_btns.addStretch()
            pa_lay.addLayout(_pa_btns)
            ai_lay.addWidget(self.puter_account_group)

        # Recommended
        self.recommended_group = QGroupBox("💡 Recommended Free API")
        self.recommended_group.setStyleSheet(_GROUP)
        rec_lay = QVBoxLayout(self.recommended_group)
        rec_text = (
            "✅ Puter.js is the recommended provider.\n\n"
            "Free rate-limited API — no account or API key required to get started."
        )
        rec_lay.addWidget(_info_box(rec_text))
        ai_lay.addWidget(self.recommended_group)

        # Manual provider group
        self.manual_group = QGroupBox("Manual Provider")
        self.manual_group.setStyleSheet(_GROUP)
        mn_lay = QVBoxLayout(self.manual_group)
        mn_lay.addWidget(_info_box(
            "Manual mode — responses are entered by hand in the manual response window."
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

        # ── Custom Script provider group ──────────────────────────────────────
        self.custom_script_group = QGroupBox("Custom Script Provider")
        self.custom_script_group.setStyleSheet(_GROUP)
        cs_lay = QVBoxLayout(self.custom_script_group)

        cs_lay.addWidget(_info_box(
            "Runs your own Python script as the AI provider. "
            "The script is reimported on every request so live edits take effect immediately."
        ))

        # Contract display — always visible when this provider is selected
        _CONTRACT_TEXT = (
            "Required function signature:\n"
            "\n"
            "  def chat(system_prompt: str, messages: list[dict]) -> str:\n"
            "\n"
            "Parameters:\n"
            "  system_prompt  — full effective system prompt string (may be empty, never None)\n"
            "  messages       — list of {\"role\": ..., \"content\": ...} dicts\n"
            "                   roles are only \"user\" or \"assistant\", alternating\n"
            "                   the latest user message is always the last entry\n"
            "\n"
            "Return:\n"
            "  A non-empty string. Returning None or \"\" is treated as an error.\n"
            "  Any exception raised will surface as an error in the chat window."
        )
        contract_box = QPlainTextEdit()
        contract_box.setPlainText(_CONTRACT_TEXT)
        contract_box.setReadOnly(True)
        contract_box.setFixedHeight(200)
        contract_box.setStyleSheet(f"""
                    QPlainTextEdit {{
                        background: #0d0d0d;
                        color: #a0c8a0;
                        border: 1px solid #333;
                        border-radius: 6px;
                        padding: 8px;
                        font-family: monospace;
                        font-size: 11px;
                    }}
                """)
        cs_lay.addWidget(contract_box)

        cs_lay.addWidget(_label("Script path:", top_margin=6))
        self.custom_script_path_input = QLineEdit()
        self.custom_script_path_input.setReadOnly(True)
        self.custom_script_path_input.setPlaceholderText("No script selected")
        self.custom_script_path_input.setStyleSheet(_INPUT)
        cs_lay.addWidget(self.custom_script_path_input)

        _cs_btn_row = QHBoxLayout()
        browse_btn = QPushButton("📂 Browse…")
        browse_btn.setStyleSheet(_BTN)

        def _browse_script():
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Python Script", "", "Python Files (*.py)"
            )
            if path:
                self.custom_script_path_input.setText(path)
                self.controller.set_custom_script_path(path)

        browse_btn.clicked.connect(_browse_script)
        _cs_btn_row.addWidget(browse_btn)

        clear_btn = QPushButton("✖ Clear")
        clear_btn.setStyleSheet(_BTN)

        def _clear_script():
            self.custom_script_path_input.clear()
            self.controller.set_custom_script_path("")

        clear_btn.clicked.connect(_clear_script)
        _cs_btn_row.addWidget(clear_btn)

        cs_lay.addLayout(_cs_btn_row)
        ai_lay.addWidget(self.custom_script_group)

        ai_lay.addStretch()
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
        refresh_btn = QPushButton("🔄 Refresh Devices")
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
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItem("Puter.js TTS (Free)", "puter")
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        self.tts_provider_combo.addItem("pyttsx3 (Offline, Free)", "pyttsx3")
        self.tts_provider_combo.setStyleSheet(_COMBO)
        self.tts_provider_combo.currentIndexChanged.connect(self.on_tts_provider_changed)
        tts_lay.addWidget(self.tts_provider_combo)

        # Edge TTS sub-group
        self.edge_tts_group = QWidget()
        etg_lay = QVBoxLayout(self.edge_tts_group)
        etg_lay.setContentsMargins(0, 6, 0, 0)
        etg_lay.addWidget(_label("Edge TTS Voice:"))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setStyleSheet(_COMBO)
        for vid, vname in [
            ("en-US-GuyNeural","Male, American (Guy)"),("en-US-JennyNeural","Female, American (Jenny)"),
            ("en-GB-RyanNeural","Male, British (Ryan)"),("en-GB-SoniaNeural","Female, British (Sonia)"),
            ("en-AU-NatashaNeural","Female, Australian (Natasha)"),("en-AU-WilliamNeural","Male, Australian (William)"),
            ("en-CA-LiamNeural","Male, Canadian (Liam)"),("en-CA-ClaraNeural","Female, Canadian (Clara)"),
            ("en-IN-NeerjaNeural","Female, Indian (Neerja)"),("en-IN-PrabhatNeural","Male, Indian (Prabhat)"),
        ]:
            self.tts_voice_combo.addItem(vname, vid)
        etg_lay.addWidget(self.tts_voice_combo)
        tts_lay.addWidget(self.edge_tts_group)

        # Puter TTS sub-group
        self.puter_tts_group = QWidget()
        ptg_lay = QVBoxLayout(self.puter_tts_group)
        ptg_lay.setContentsMargins(0, 6, 0, 0)
        ptg_lay.addWidget(_label("Puter TTS Model:"))
        self.puter_tts_model_combo = QComboBox()
        self.puter_tts_model_combo.setStyleSheet(_COMBO)
        ptg_lay.addWidget(self.puter_tts_model_combo)
        ptg_lay.addWidget(_label("Puter Voice (optional):"))
        self.puter_tts_voice_input = QLineEdit()
        self.puter_tts_voice_input.setPlaceholderText("Leave empty for default")
        self.puter_tts_voice_input.setStyleSheet(_INPUT)
        ptg_lay.addWidget(self.puter_tts_voice_input)

        self.elevenlabs_checkbox = QCheckBox("🎙️ Use ElevenLabs TTS (Premium)")
        self.elevenlabs_checkbox.setStyleSheet(_CHECK)
        self.elevenlabs_checkbox.stateChanged.connect(self.on_elevenlabs_toggled)
        ptg_lay.addWidget(self.elevenlabs_checkbox)

        self.elevenlabs_settings = QWidget()
        el_lay = QVBoxLayout(self.elevenlabs_settings)
        el_lay.setContentsMargins(16, 0, 0, 0)
        el_lay.addWidget(_info_box(
            "ElevenLabs: premium TTS with 70+ languages, emotional control with [brackets].\n"
            "Get your voice ID from: https://elevenlabs.io"))
        el_lay.addWidget(_label("Voice ID:"))
        self.elevenlabs_voice_input = QLineEdit()
        self.elevenlabs_voice_input.setPlaceholderText("e.g., 21m00Tcm4TlvDq8ikWAM")
        self.elevenlabs_voice_input.setStyleSheet(_INPUT)
        el_lay.addWidget(self.elevenlabs_voice_input)
        self.elevenlabs_settings.hide()
        ptg_lay.addWidget(self.elevenlabs_settings)
        tts_lay.addWidget(self.puter_tts_group)
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
            "Applies a semi-transparent frosted effect over the chat area, "
            "letting your desktop wallpaper show through. "
            "Works on top of whichever main theme is selected."))

        self.glass_enabled_checkbox = QCheckBox("Enable glass background")
        self.glass_enabled_checkbox.setStyleSheet(_CHECK)
        gl_lay.addWidget(self.glass_enabled_checkbox)

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

        # Initial provider visibility
        initial_provider = self.provider_combo.currentText()
        self.on_provider_changed(initial_provider)

    def _on_theme_selected(self, checked, key, card):
        """Handle theme radio button toggle — update card borders."""
        if not checked:
            return
        self._selected_theme = key
        # Update all card borders
        _ACCENT  = "#58A6FF"
        _BORDER  = "#30363D"
        for k, c in self._theme_cards.items():
            try:
                _ELEV = "#21262D"
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

    def on_provider_changed(self, provider):
        """Handle provider change - DYNAMIC VISIBILITY"""
        # Hide all provider-specific groups first
        self.anthropic_group.hide()
        self.gemini_group.hide()
        self.manual_group.hide()
        self.puter_group.hide()
        self.custom_script_group.hide()

        # CRITICAL: Always hide Puter-specific sections first
        if DEBUG_MODE:
            self.puter_account_group.hide()
        self.recommended_group.hide()
        self.puter_tts_group.hide()

        # Show relevant sections based on provider
        if provider == 'puter':
            self.puter_group.show()
            if DEBUG_MODE:
                self.puter_account_group.show()
            self.recommended_group.show()
            self.update_tts_provider_options(show_puter=True)
        elif provider == 'gemini':
            self.gemini_group.show()
            self.update_tts_provider_options(show_puter=False)
        elif provider == 'manual':
            self.manual_group.show()
            self.update_tts_provider_options(show_puter=False)
        elif provider == 'custom_script':
            self.custom_script_group.show()
            self.update_tts_provider_options(show_puter=False)
        else:  # anthropic
            self.anthropic_group.show()
            self.update_tts_provider_options(show_puter=False)

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

    def on_elevenlabs_toggled(self, state):
        """Handle ElevenLabs checkbox toggle"""
        if state == 2:  # Checked
            self.elevenlabs_settings.show()
        else:
            self.elevenlabs_settings.hide()

    def update_anthropic_model_description(self):
        """Update Anthropic model description"""
        try:
            models = self.controller.get_anthropic_models()
            selected_id = self.anthropic_model_combo.currentData()
            for model in models:
                if model['id'] == selected_id:
                    self.anthropic_model_desc.setText(f"ℹ️ {model['description']}")
                    break
        except Exception as e:
            print(f"Error updating Anthropic description: {e}")

    def update_model_description(self):
        """Update Puter model description"""
        try:
            models = self.controller.get_puter_models()
            selected_id = self.puter_model_combo.currentData()

            for model in models:
                if model['id'] == selected_id:
                    self.puter_model_desc.setText(f"ℹ️ {model['description']}")
                    break
        except Exception as e:
            print(f"Error updating description: {e}")

    def update_tts_provider_options(self, show_puter):
        """Update TTS provider dropdown based on AI provider"""
        current_provider = self.tts_provider_combo.currentData()

        # Block signals to prevent recursive calls
        self.tts_provider_combo.blockSignals(True)

        # Clear and rebuild
        self.tts_provider_combo.clear()

        if show_puter:
            # Show all options when Puter is AI provider
            self.tts_provider_combo.addItem("Puter.js TTS (Free)", "puter")
            self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")

            # Try to restore previous selection
            index = self.tts_provider_combo.findData(current_provider)
            if index >= 0:
                self.tts_provider_combo.setCurrentIndex(index)
            else:
                # Default to Edge TTS
                self.tts_provider_combo.setCurrentIndex(1)
        else:
            # ONLY show Edge TTS when Puter is NOT the AI provider
            self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")

            # FORCE selection to Edge TTS (only option)
            self.tts_provider_combo.setCurrentIndex(0)

        # Unblock signals and trigger update
        self.tts_provider_combo.blockSignals(False)

        # CRITICAL: Trigger visibility update for TTS settings
        self.on_tts_provider_changed(self.tts_provider_combo.currentIndex())

    def update_gemini_model_description(self):
        """Update Gemini model description"""
        try:
            models = self.controller.get_gemini_models()
            selected_id = self.gemini_model_combo.currentData()

            for model in models:
                if model['id'] == selected_id:
                    self.gemini_model_desc.setText(f"ℹ️ {model['description']}")
                    break
        except Exception as e:
            print(f"Error updating Gemini description: {e}")

    def toggle_api_key_visibility(self, checked):
        """Toggle API key visibility"""
        if checked:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("🙈 Hide")
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("👁️ Show")

    def toggle_gemini_key_visibility(self, checked):
        """Toggle Gemini API key visibility"""
        if checked:
            self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_gemini_key_btn.setText("🙈 Hide")
        else:
            self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_gemini_key_btn.setText("👁️ Show")

    def toggle_puter_password_visibility(self, checked):
        """Toggle Puter password visibility"""
        if not DEBUG_MODE:
            return
        if checked:
            self.puter_password_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_puter_password_btn.setText("🙈 Hide")
        else:
            self.puter_password_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_puter_password_btn.setText("👁️ Show")

    def on_tts_provider_changed(self, index):
        """Handle TTS provider change - DYNAMIC VISIBILITY"""
        provider = self.tts_provider_combo.currentData()

        if provider == 'puter':
            # Show Puter TTS settings (including ElevenLabs option)
            self.puter_tts_group.show()
            self.edge_tts_group.hide()

            # Load Puter TTS models if not loaded
            if self.puter_tts_model_combo.count() == 0:
                models = self.controller.get_puter_tts_models()
                self.puter_tts_model_combo.clear()
                for model in models:
                    self.puter_tts_model_combo.addItem(model['name'], model['id'])

        elif provider == 'edge-tts':
            # Show Edge TTS settings
            self.puter_tts_group.hide()
            self.edge_tts_group.show()

    def setup_puter_account(self):
        """Setup new Puter account"""
        self.controller.setup_puter_account()
        self.show_status_message("✓ Opening Puter for account setup...")

    def reset_puter_quota(self):
        """Reset Puter quota"""
        # Save credentials first
        email = self.puter_email_input.text().strip()
        password = self.puter_password_input.text().strip()

        if not email or not password:
            self.show_status_message("✗ Please enter email and password first")
            return

        self.controller.set_puter_credentials(email, password)

        # Reset quota
        success = self.controller.reset_puter_quota()
        if success:
            self.show_status_message("✓ Quota reset successful!")
        else:
            self.show_status_message("✗ Quota reset failed")

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
        # Load provider and trigger visibility update
        provider = self.controller.get_ai_provider()
        self.provider_combo.setCurrentText(provider)
        # CRITICAL: Trigger visibility update on load
        self.on_provider_changed(provider)

        saved_script_path = self.controller.get_custom_script_path()
        if saved_script_path:
            self.custom_script_path_input.setText(saved_script_path)

        # Load API key
        api_key = self.controller.get_api_key()
        if api_key:
            self.api_key_input.setText(api_key)

        # Load Anthropic model + generation params
        anthropic_model = self.controller.get_anthropic_model()
        index = self.anthropic_model_combo.findData(anthropic_model)
        if index >= 0:
            self.anthropic_model_combo.setCurrentIndex(index)
        self.update_anthropic_model_description()
        an_temp = self.controller.get_anthropic_temperature()
        self.anthropic_temp_slider.setValue(int(round(an_temp * 100)))
        an_auto = self.controller.get_anthropic_auto_tokens()
        self.anthropic_auto_tokens_cb.setChecked(an_auto)
        self.anthropic_max_tokens_widget.setVisible(not an_auto)
        self.anthropic_max_tokens_spin.setValue(self.controller.get_anthropic_max_tokens())

        # Load Gemini API key
        gemini_key = self.controller.get_gemini_api_key()
        if gemini_key:
            self.gemini_key_input.setText(gemini_key)

        # Load Puter model
        puter_model = self.controller.get_puter_model()
        index = self.puter_model_combo.findData(puter_model)
        if index >= 0:
            self.puter_model_combo.setCurrentIndex(index)

        # Load Gemini model + generation params
        gemini_model = self.controller.get_gemini_model()
        index = self.gemini_model_combo.findData(gemini_model)
        if index >= 0:
            self.gemini_model_combo.setCurrentIndex(index)
        gm_temp = self.controller.get_gemini_temperature()
        self.gemini_temp_slider.setValue(int(round(gm_temp * 100)))
        gm_auto = self.controller.get_gemini_auto_tokens()
        self.gemini_auto_tokens_cb.setChecked(gm_auto)
        self.gemini_max_tokens_widget.setVisible(not gm_auto)
        self.gemini_max_tokens_spin.setValue(self.controller.get_gemini_max_tokens())
        gm_top_p = self.controller.get_gemini_top_p()
        if gm_top_p is not None:
            self.gemini_top_p_enable.setChecked(True)
            self.gemini_top_p_spin.setValue(gm_top_p)
        gm_top_k = self.controller.get_gemini_top_k()
        if gm_top_k is not None:
            self.gemini_top_k_enable.setChecked(True)
            self.gemini_top_k_spin.setValue(gm_top_k)

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

        # Load system prompt
        self.system_prompt_hijack_input.setPlainText(
            self.controller.settings.get('custom_system_prompt', '')
        )

        # Load interrupt mode
        interrupt_mode = self.controller.settings.get('voice_interrupt_mode', 'manual')
        index = self.interrupt_mode_combo.findData(interrupt_mode)
        if index >= 0:
            self.interrupt_mode_combo.setCurrentIndex(index)

        # Load ElevenLabs settings
        elevenlabs_enabled = self.controller.get_elevenlabs_enabled()
        self.elevenlabs_checkbox.setChecked(elevenlabs_enabled)

        elevenlabs_voice = self.controller.get_elevenlabs_voice_id()
        if elevenlabs_voice:
            self.elevenlabs_voice_input.setText(elevenlabs_voice)

        # Load voice devices
        self.refresh_audio_devices()

        # Load voice settings
        input_device = self.controller.settings.get('voice_input_device')
        output_device = self.controller.settings.get('voice_output_device')

        # Load Puter credentials
        creds = self.controller.get_puter_credentials()
        if DEBUG_MODE:
            self.puter_email_input.setText(creds['email'])
            self.puter_password_input.setText(creds['password'])

        # Load Puter timeout
        puter_timeout = self.controller.settings.get('puter_timeout', 30)
        if DEBUG_MODE:
            self.puter_timeout_input.setText(str(puter_timeout))

        # Load TTS provider
        tts_provider = self.controller.get_tts_provider()
        index = self.tts_provider_combo.findData(tts_provider)
        if index >= 0:
            self.tts_provider_combo.setCurrentIndex(index)

        # Load Puter TTS settings
        puter_tts_model = self.controller.get_puter_tts_model()
        puter_tts_voice = self.controller.get_puter_tts_voice()

        #Load memory engine settings
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

        # Load selected theme
        saved_theme = self.controller.settings.get('chat_theme', 'obsidian_blue')
        self._selected_theme = saved_theme
        if hasattr(self, '_theme_btn_group'):
            for btn in self._theme_btn_group.buttons():
                if btn.property('theme_key') == saved_theme:
                    btn.setChecked(True)
                    break
        if hasattr(self, '_theme_cards'):
            _ACCENT = "#58A6FF"; _BORDER = "#30363D"; _ELEV = "#21262D"
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

        # Load models
        models = self.controller.get_puter_tts_models()
        self.puter_tts_model_combo.clear()
        for model in models:
            self.puter_tts_model_combo.addItem(model['name'], model['id'])

        index = self.puter_tts_model_combo.findData(puter_tts_model)
        if index >= 0:
            self.puter_tts_model_combo.setCurrentIndex(index)

        if puter_tts_voice:
            self.puter_tts_voice_input.setText(puter_tts_voice)

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
        """Save settings to controller - FIXED: Actually applies voice settings!"""
        # Save provider
        provider = self.provider_combo.currentText()
        self.controller.set_ai_provider(provider)

        # Save API key
        api_key = self.api_key_input.text().strip()
        self.controller.set_api_key(api_key)

        # Save Anthropic model + generation params
        anthropic_model = self.anthropic_model_combo.currentData()
        if anthropic_model:
            self.controller.set_anthropic_model(anthropic_model)
        self.controller.set_anthropic_temperature(self.anthropic_temp_slider.value() / 100.0)
        self.controller.set_anthropic_auto_tokens(self.anthropic_auto_tokens_cb.isChecked())
        self.controller.set_anthropic_max_tokens(self.anthropic_max_tokens_spin.value())

        # Save Gemini API key
        gemini_key = self.gemini_key_input.text().strip()
        self.controller.set_gemini_api_key(gemini_key)

        # Save Puter model
        puter_model = self.puter_model_combo.currentData()
        self.controller.set_puter_model(puter_model)

        # Save Gemini model + generation params
        gemini_model = self.gemini_model_combo.currentData()
        self.controller.set_gemini_model(gemini_model)
        self.controller.set_gemini_temperature(self.gemini_temp_slider.value() / 100.0)
        self.controller.set_gemini_auto_tokens(self.gemini_auto_tokens_cb.isChecked())
        self.controller.set_gemini_max_tokens(self.gemini_max_tokens_spin.value())
        # top_p / top_k: only save if enabled
        top_p = self.gemini_top_p_spin.value() if self.gemini_top_p_enable.isChecked() else None
        self.controller.set_gemini_top_p(top_p)
        top_k = self.gemini_top_k_spin.value() if self.gemini_top_k_enable.isChecked() else None
        self.controller.set_gemini_top_k(top_k)

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

        # Save custom system prompt
        self.controller.settings['custom_system_prompt'] = self.system_prompt_hijack_input.toPlainText()

        # Save voice devices
        input_device = self.input_device_combo.currentData()
        self.controller.set_voice_input_device(input_device)

        output_device = self.output_device_combo.currentData()
        self.controller.set_voice_output_device(output_device)

        # Save Puter credentials
        if DEBUG_MODE:
            email = self.puter_email_input.text().strip()
            password = self.puter_password_input.text().strip()
            self.controller.set_puter_credentials(email, password)

        # Save Puter timeout
        if DEBUG_MODE:
            timeout_text = self.puter_timeout_input.text().strip()
            if timeout_text:
                try:
                    timeout = int(timeout_text)
                    if timeout > 0:
                        self.controller.set_puter_timeout(timeout)
                    else:
                        self.controller.set_puter_timeout(30)
                except ValueError:
                    self.controller.set_puter_timeout(30)
            else:
                self.controller.set_puter_timeout(30)

        # Save TTS provider
        tts_provider = self.tts_provider_combo.currentData()
        self.controller.set_tts_provider(tts_provider)

        # Save Puter TTS settings
        puter_tts_model = self.puter_tts_model_combo.currentData()
        self.controller.set_puter_tts_model(puter_tts_model)

        puter_tts_voice = self.puter_tts_voice_input.text().strip()
        if puter_tts_voice:
            self.controller.set_puter_tts_voice(puter_tts_voice)

        # Save TTS voice
        tts_voice = self.tts_voice_combo.currentData()
        self.controller.set_tts_voice(tts_voice)

        # Save VAD aggressiveness
        vad_level = self.vad_combo.currentData()
        self.controller.set_vad_aggressiveness(vad_level)

        # Save interrupt mode
        interrupt_mode = self.interrupt_mode_combo.currentData()
        self.controller.set_voice_interrupt_mode(interrupt_mode)

        # Save ElevenLabs settings
        elevenlabs_enabled = self.elevenlabs_checkbox.isChecked()
        self.controller.set_elevenlabs_enabled(elevenlabs_enabled)

        elevenlabs_voice = self.elevenlabs_voice_input.text().strip()
        if elevenlabs_voice:
            self.controller.set_elevenlabs_voice_id(elevenlabs_voice)

        # Save memory engine settings
        self.controller.settings['memory_enabled'] = self.memory_enabled_checkbox.isChecked()
        self.controller.settings['memory_threshold'] = self.memory_threshold_combo.currentData()
        self.controller.settings['memory_max_results'] = self.memory_max_combo.currentData()

        # Save selected theme
        theme = getattr(self, '_selected_theme', 'obsidian_blue')
        self.controller.settings['chat_theme'] = theme
        try:
            chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
            if chat_win and hasattr(chat_win, 'apply_theme'):
                chat_win.apply_theme(theme)
        except Exception:
            pass

        # Save glass background settings
        glass_enabled = self.glass_enabled_checkbox.isChecked()
        glass_opacity = self.glass_opacity_slider.value() / 100.0
        self.controller.settings['glass_background_enabled'] = glass_enabled
        self.controller.settings['glass_background_opacity'] = glass_opacity

        # Apply glass background to chat window immediately
        try:
            if hasattr(self.controller, 'ui') and self.controller.ui:
                chat_win = getattr(self.controller.ui, 'chat_window', None)
                if chat_win and hasattr(chat_win, 'apply_glass_background'):
                    chat_win.apply_glass_background(glass_enabled, glass_opacity)
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

        # Show confirmation
        self.show_status_message("✓ Settings saved successfully!")

    def start_puter_server(self):
        """Start Puter server"""
        success = self.controller.start_puter_server()
        if success:
            self.show_status_message("✓ Server started! Click PLAY, watch for 🔊 in tab!")
        else:
            self.show_status_message("✗ Failed to start Puter server")

    def open_puter_interface(self):
        """Open Puter interface in browser"""
        try:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:8888')
            self.show_status_message("✓ Opening Puter interface...")
        except Exception as e:
            self.show_status_message(f"✗ Error: {e}")

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
        """Sync container background to the current chat window theme on show."""
        super().showEvent(event)
        try:
            ui = getattr(self.controller, 'ui', None)
            chat_win = getattr(ui, 'chat_window', None) if ui else None
            if chat_win and hasattr(chat_win, '_t'):
                t = chat_win._t()
                self.container.setStyleSheet(f"""
                    QWidget#container {{
                        background-color: {t['base']};
                        border-radius: 12px;
                    }}
                    QWidget {{ color: #E6EDF3; font-family: 'Segoe UI', system-ui, sans-serif; }}
                    QScrollArea {{ background-color: {t['surface']}; }}
                    QScrollArea > QWidget {{ background-color: {t['surface']}; }}
                """)
        except Exception:
            pass