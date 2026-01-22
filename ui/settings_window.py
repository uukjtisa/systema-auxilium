"""
Settings Window - Configure API key, AI provider, Puter.js settings, Google Gemini, and Voice
FIXED: Voice settings now actually work - VAD and TTS voice selection are functional
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QComboBox, QGroupBox, QCheckBox, QScrollArea)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QFont


class SettingsWindow(QWidget):
    """Settings window for configuring the AI assistant"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        # Window settings
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedSize(600, 750)
        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
        """)

        # Dragging
        self._is_dragging = False
        self._drag_offset = QPoint()

        self.init_ui()
        self.load_settings()

    def init_ui(self):
        """Initialize UI"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # Header
        header = QHBoxLayout()
        title = QLabel("⚙️ Settings")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header.addWidget(title)

        header.addStretch()

        # Close button
        close_btn = QPushButton("✖")
        close_btn.setMaximumWidth(30)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #ff5555;
                border: none;
                border-radius: 3px;
                padding: 5px;
            }
            QPushButton:hover {
                background: #ff7777;
            }
        """)
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)

        main_layout.addLayout(header)

        # Create scroll area for all settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #1e1e1e;
            }
            QScrollBar:vertical {
                background: #2d2d2d;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #5555ff;
                border-radius: 5px;
            }
        """)

        # Container widget for scroll area
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(15)

        # AI Provider Selection
        provider_group = QGroupBox("AI Provider")
        provider_layout = QVBoxLayout()

        provider_label = QLabel("Select AI Provider:")
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["llama", "anthropic", "gemini", "puter"])  # LLaMA first!
        self.provider_combo.currentTextChanged.connect(self.on_provider_changed)
        provider_layout.addWidget(provider_label)
        provider_layout.addWidget(self.provider_combo)

        provider_info = QLabel("💡 Anthropic: Claude API | Gemini: Google AI | Puter.js: Free browser-based AI")
        provider_info.setWordWrap(True)
        provider_info.setStyleSheet("color: #888; font-size: 9pt;")
        provider_layout.addWidget(provider_info)

        provider_group.setLayout(provider_layout)
        scroll_layout.addWidget(provider_group)

        # Anthropic API Key Section
        self.anthropic_group = QGroupBox("Anthropic (Claude) API Configuration")
        api_layout = QVBoxLayout()

        api_label = QLabel("API Key:")
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-ant-...")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
                font-family: monospace;
            }
        """)
        api_layout.addWidget(api_label)
        api_layout.addWidget(self.api_key_input)

        # Show/Hide button
        show_key_layout = QHBoxLayout()
        self.show_key_btn = QPushButton("👁️ Show")
        self.show_key_btn.setMaximumWidth(80)
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:checked {
                background-color: #5555ff;
            }
        """)
        self.show_key_btn.toggled.connect(self.toggle_api_key_visibility)
        show_key_layout.addWidget(self.show_key_btn)
        show_key_layout.addStretch()
        api_layout.addLayout(show_key_layout)

        api_info = QLabel("Get your API key from: https://console.anthropic.com")
        api_info.setStyleSheet("color: #888; font-size: 9pt;")
        api_layout.addWidget(api_info)

        self.anthropic_group.setLayout(api_layout)
        scroll_layout.addWidget(self.anthropic_group)

        # Google Gemini API Key Section
        self.gemini_group = QGroupBox("Google Gemini (AI Studio) Configuration")
        gemini_layout = QVBoxLayout()

        gemini_info = QLabel(
            "🌟 Google Gemini API (formerly Google AI Studio)\n\n"
            "Free tier available with generous quotas!\n"
            "Get your API key from Google AI Studio"
        )
        gemini_info.setWordWrap(True)
        gemini_info.setStyleSheet("color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        gemini_layout.addWidget(gemini_info)

        gemini_key_label = QLabel("API Key:")
        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setPlaceholderText("AIza...")
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
                font-family: monospace;
            }
        """)
        gemini_layout.addWidget(gemini_key_label)
        gemini_layout.addWidget(self.gemini_key_input)

        # Show/Hide button for Gemini
        show_gemini_key_layout = QHBoxLayout()
        self.show_gemini_key_btn = QPushButton("👁️ Show")
        self.show_gemini_key_btn.setMaximumWidth(80)
        self.show_gemini_key_btn.setCheckable(True)
        self.show_gemini_key_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:checked {
                background-color: #5555ff;
            }
        """)
        self.show_gemini_key_btn.toggled.connect(self.toggle_gemini_key_visibility)
        show_gemini_key_layout.addWidget(self.show_gemini_key_btn)
        show_gemini_key_layout.addStretch()
        gemini_layout.addLayout(show_gemini_key_layout)

        # Model selection for Gemini
        gemini_model_label = QLabel("Select Model:")
        self.gemini_model_combo = QComboBox()

        # Load Gemini models
        models = self.controller.get_gemini_models()
        for model in models:
            self.gemini_model_combo.addItem(model['name'], model['id'])

        gemini_layout.addWidget(gemini_model_label)
        gemini_layout.addWidget(self.gemini_model_combo)

        # Model description
        self.gemini_model_desc = QLabel()
        self.gemini_model_desc.setWordWrap(True)
        self.gemini_model_desc.setStyleSheet("color: #888; font-size: 9pt; font-style: italic;")
        self.gemini_model_combo.currentIndexChanged.connect(self.update_gemini_model_description)
        gemini_layout.addWidget(self.gemini_model_desc)

        gemini_link = QLabel('<a href="https://aistudio.google.com/apikey" style="color: #5555ff;">Get API key: https://aistudio.google.com/apikey</a>')
        gemini_link.setOpenExternalLinks(True)
        gemini_link.setStyleSheet("color: #888; font-size: 9pt;")
        gemini_layout.addWidget(gemini_link)

        self.gemini_group.setLayout(gemini_layout)
        scroll_layout.addWidget(self.gemini_group)

        # LLaMA Configuration (Free, Offline) - SAFE INITIALIZATION
        self.llama_group = QGroupBox("LLaMA (Free, Offline AI)")
        llama_layout = QVBoxLayout()

        llama_info = QLabel(
            "🆓 **100% Free & Offline AI**\n\n"
            "LLaMA runs entirely on your computer - no API key, no internet needed!\n"
            "Perfect for privacy and unlimited usage."
        )
        llama_info.setWordWrap(True)
        llama_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        llama_layout.addWidget(llama_info)

        # SAFE: Don't check availability during init - defer to load_settings
        self.llama_status_label = QLabel("Checking LLaMA availability...")
        self.llama_status_label.setStyleSheet("color: #888; font-style: italic;")
        llama_layout.addWidget(self.llama_status_label)

        self.llama_models_container = QWidget()
        self.llama_models_layout = QVBoxLayout(self.llama_models_container)
        self.llama_models_layout.setContentsMargins(0, 0, 0, 0)
        llama_layout.addWidget(self.llama_models_container)

        # LLaMA Generation Settings
        llama_gen_label = QLabel("⚙️ Generation Settings")
        llama_gen_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        llama_layout.addWidget(llama_gen_label)

        # Max Tokens
        tokens_layout = QHBoxLayout()
        tokens_layout.addWidget(QLabel("Max Tokens:"))

        self.llama_tokens_input = QLineEdit()
        self.llama_tokens_input.setPlaceholderText("2000")
        self.llama_tokens_input.setMaximumWidth(100)
        self.llama_tokens_input.setStyleSheet("""
                    QLineEdit {
                        background-color: #2d2d2d;
                        border: 1px solid #3d3d3d;
                        border-radius: 5px;
                        padding: 6px;
                        font-size: 11px;
                    }
                """)
        tokens_layout.addWidget(self.llama_tokens_input)

        tokens_info = QLabel("(Higher = More output, slower)")
        tokens_info.setStyleSheet("color: #888; font-size: 9pt;")
        tokens_layout.addWidget(tokens_info)
        tokens_layout.addStretch()
        llama_layout.addLayout(tokens_layout)

        # Temperature
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(QLabel("Temperature:"))

        self.llama_temp_combo = QComboBox()
        self.llama_temp_combo.addItem("0.1 (Precise)", 0.1)
        self.llama_temp_combo.addItem("0.3 (Focused)", 0.3)
        self.llama_temp_combo.addItem("0.5 (Balanced)", 0.5)
        self.llama_temp_combo.addItem("0.7 (Creative)", 0.7)
        self.llama_temp_combo.addItem("1.0 (Very Creative)", 1.0)
        self.llama_temp_combo.setCurrentIndex(3)  # Default 0.7
        self.llama_temp_combo.setStyleSheet("""
                    QComboBox {
                        background-color: #2d2d2d;
                        border: 1px solid #3d3d3d;
                        border-radius: 5px;
                        padding: 6px;
                        font-size: 11px;
                    }
                """)
        temp_layout.addWidget(self.llama_temp_combo)
        temp_layout.addStretch()
        llama_layout.addLayout(temp_layout)

        # Top-P
        topp_layout = QHBoxLayout()
        topp_layout.addWidget(QLabel("Top-P (Nucleus):"))

        self.llama_topp_combo = QComboBox()
        self.llama_topp_combo.addItem("0.7 (Strict)", 0.7)
        self.llama_topp_combo.addItem("0.8 (Balanced)", 0.8)
        self.llama_topp_combo.addItem("0.9 (Default)", 0.9)
        self.llama_topp_combo.addItem("0.95 (Diverse)", 0.95)
        self.llama_topp_combo.setCurrentIndex(2)  # Default 0.9
        self.llama_topp_combo.setStyleSheet("""
                    QComboBox {
                        background-color: #2d2d2d;
                        border: 1px solid #3d3d3d;
                        border-radius: 5px;
                        padding: 6px;
                        font-size: 11px;
                    }
                """)
        topp_layout.addWidget(self.llama_topp_combo)
        topp_layout.addStretch()
        llama_layout.addLayout(topp_layout)

        # Override Checkbox
        self.llama_override_checkbox = QCheckBox("⚠️ Override Context Limits (Use with caution!)")
        self.llama_override_checkbox.setStyleSheet("color: #ff9900; font-size: 11pt;")
        self.llama_override_checkbox.stateChanged.connect(self.on_llama_override_toggled)
        llama_layout.addWidget(self.llama_override_checkbox)

        # Warning label (hidden by default)
        self.llama_warning_label = QLabel(
            "⚠️ <b>Warning:</b> High token counts can cause:\n"
            "• Slow generation (1-2 min per response)\n"
            "• High RAM usage (4-8GB+)\n"
            "• System slowdown\n\n"
            "<b>Estimate:</b> 1000 tokens ≈ 750 words ≈ 30 seconds\n"
            "Recommended max: 4000 tokens"
        )
        self.llama_warning_label.setWordWrap(True)
        self.llama_warning_label.setStyleSheet("""
                    QLabel {
                        color: #ff9900;
                        font-size: 9pt;
                        padding: 10px;
                        background: #2d1a00;
                        border: 1px solid #ff9900;
                        border-radius: 5px;
                        margin-left: 20px;
                    }
                """)
        self.llama_warning_label.hide()
        llama_layout.addWidget(self.llama_warning_label)

        # Model Management
        llama_mgmt_label = QLabel("🔄 Model Management")
        llama_mgmt_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        llama_layout.addWidget(llama_mgmt_label)

        # Model selector
        model_select_layout = QHBoxLayout()
        model_select_layout.addWidget(QLabel("Current Model:"))

        self.llama_model_combo = QComboBox()
        self.llama_model_combo.setStyleSheet("""
                    QComboBox {
                        background-color: #2d2d2d;
                        border: 1px solid #3d3d3d;
                        border-radius: 5px;
                        padding: 6px;
                        font-size: 11px;
                    }
                """)
        model_select_layout.addWidget(self.llama_model_combo, 1)
        llama_layout.addLayout(model_select_layout)

        # Model action buttons
        model_btn_layout = QHBoxLayout()

        self.llama_reload_btn = QPushButton("🔄 Reload Model")
        self.llama_reload_btn.clicked.connect(self.reload_llama_model)
        self.llama_reload_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #3d3d3d;
                        border: none;
                        border-radius: 5px;
                        padding: 8px;
                        font-size: 11px;
                    }
                    QPushButton:hover {
                        background-color: #4d4d4d;
                    }
                """)
        model_btn_layout.addWidget(self.llama_reload_btn)

        self.llama_switch_btn = QPushButton("🔀 Switch Model")
        self.llama_switch_btn.clicked.connect(self.switch_llama_model)
        self.llama_switch_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #3d3d3d;
                        border: none;
                        border-radius: 5px;
                        padding: 8px;
                        font-size: 11px;
                    }
                    QPushButton:hover {
                        background-color: #4d4d4d;
                    }
                """)
        model_btn_layout.addWidget(self.llama_switch_btn)

        llama_layout.addLayout(model_btn_layout)

        self.llama_group.setLayout(llama_layout)
        scroll_layout.addWidget(self.llama_group)

        # Puter.js Configuration
        self.puter_group = QGroupBox("Puter.js Configuration")
        puter_layout = QVBoxLayout()

        puter_info = QLabel(
            "🚀 Puter.js runs in a local browser window (no API key needed!)\n\n"
            "When you select Puter.js:\n"
            "• A Flask server starts on port 5555\n"
            "• A browser window opens automatically\n"
            "• Click PLAY on the visible audio player (it LOOPS FOREVER!)\n"
            "• You'll see a 🔊 speaker icon in the browser tab (that's good!)\n"
            "• Minimize the window (DON'T CLOSE THE TAB!)\n"
            "• The audio keeps playing infinitely, keeping the tab active\n"
            "• All AI requests are FREE and work all day long"
        )
        puter_info.setWordWrap(True)
        puter_info.setStyleSheet("color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        puter_layout.addWidget(puter_info)

        # Model selection
        model_label = QLabel("Select Model:")
        self.puter_model_combo = QComboBox()

        # Load models
        models = self.controller.get_puter_models()
        for model in models:
            self.puter_model_combo.addItem(model['name'], model['id'])

        puter_layout.addWidget(model_label)
        puter_layout.addWidget(self.puter_model_combo)

        # Model description
        self.puter_model_desc = QLabel()
        self.puter_model_desc.setWordWrap(True)
        self.puter_model_desc.setStyleSheet("color: #888; font-size: 9pt; font-style: italic;")
        self.puter_model_combo.currentIndexChanged.connect(self.update_model_description)
        puter_layout.addWidget(self.puter_model_desc)

        # Puter control buttons
        puter_btn_layout = QHBoxLayout()

        start_puter_btn = QPushButton("▶️ Start Puter Server")
        start_puter_btn.clicked.connect(self.start_puter_server)
        start_puter_btn.setStyleSheet("""
            QPushButton {
                background-color: #55ff55;
                color: #000;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #66ff66;
            }
        """)
        puter_btn_layout.addWidget(start_puter_btn)

        open_puter_btn = QPushButton("🌐 Open Interface")
        open_puter_btn.clicked.connect(self.open_puter_interface)
        open_puter_btn.setStyleSheet("""
            QPushButton {
                background-color: #5555ff;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #6666ff;
            }
        """)
        puter_btn_layout.addWidget(open_puter_btn)

        puter_layout.addLayout(puter_btn_layout)

        self.puter_group.setLayout(puter_layout)
        scroll_layout.addWidget(self.puter_group)

        # Puter Account & Quota Management
        self.puter_account_group = QGroupBox("Puter Account Management")
        puter_account_layout = QVBoxLayout()

        puter_account_info = QLabel(
            "💡 Manage your Puter.js account and API quotas"
        )
        puter_account_info.setWordWrap(True)
        puter_account_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        puter_account_layout.addWidget(puter_account_info)

        # Email
        email_label = QLabel("Email:")
        self.puter_email_input = QLineEdit()
        self.puter_email_input.setPlaceholderText("your@email.com")
        self.puter_email_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
            }
        """)
        puter_account_layout.addWidget(email_label)
        puter_account_layout.addWidget(self.puter_email_input)

        # Password
        password_label = QLabel("Password:")
        self.puter_password_input = QLineEdit()
        self.puter_password_input.setPlaceholderText("Your password")
        self.puter_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.puter_password_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
            }
        """)
        puter_account_layout.addWidget(password_label)
        puter_account_layout.addWidget(self.puter_password_input)

        # Show password toggle
        show_puter_password_layout = QHBoxLayout()
        self.show_puter_password_btn = QPushButton("👁️ Show")
        self.show_puter_password_btn.setMaximumWidth(80)
        self.show_puter_password_btn.setCheckable(True)
        self.show_puter_password_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                border: none;
                border-radius: 5px;
                padding: 5px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
            QPushButton:checked {
                background-color: #5555ff;
            }
        """)
        self.show_puter_password_btn.toggled.connect(self.toggle_puter_password_visibility)
        show_puter_password_layout.addWidget(self.show_puter_password_btn)
        show_puter_password_layout.addStretch()
        puter_account_layout.addLayout(show_puter_password_layout)

        # Account action buttons
        puter_action_layout = QHBoxLayout()

        setup_account_btn = QPushButton("🆕 Setup New Account")
        setup_account_btn.clicked.connect(self.setup_puter_account)
        setup_account_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
        """)
        puter_action_layout.addWidget(setup_account_btn)

        reset_quota_btn = QPushButton("♻️ Reset Quota")
        reset_quota_btn.clicked.connect(self.reset_puter_quota)
        reset_quota_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9900;
                color: #000;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ffaa22;
            }
        """)
        puter_action_layout.addWidget(reset_quota_btn)

        puter_account_layout.addLayout(puter_action_layout)

        self.puter_account_group.setLayout(puter_account_layout)
        scroll_layout.addWidget(self.puter_account_group)

        # Recommended API Provider info
        self.recommended_group = QGroupBox("💡 Recommended Free API")
        recommended_layout = QVBoxLayout()

        recommended_info = QLabel(
            "**Recommended: Puter.js + TempMail**\n\n"
            "1. Get a temporary email from: https://temp-mail.org\n"
            "2. Click 'Setup New Account' above\n"
            "3. Use the temp email to create account\n"
            "4. Copy email & password here\n"
            "5. Use 'Reset Quota' button when you run out\n\n"
            "✨ Unlimited free usage by creating new accounts!"
        )
        recommended_info.setWordWrap(True)
        recommended_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        recommended_layout.addWidget(recommended_info)

        self.recommended_group.setLayout(recommended_layout)
        scroll_layout.addWidget(self.recommended_group)

        # Voice Configuration - COMPLETE REWRITE
        voice_group = QGroupBox("🎤 Voice Configuration")
        voice_layout = QVBoxLayout()

        voice_info = QLabel(
            "🆓 **FREE Voice Features:**\n"
            "• Google Speech Recognition (no API key needed!)\n"
            "• Microsoft Edge TTS (completely free!)\n\n"
            "Select your audio devices below:"
        )
        voice_info.setWordWrap(True)
        voice_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
        voice_layout.addWidget(voice_info)

        # Input device
        input_label = QLabel("🎙️ Input Device (Microphone):")
        input_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        voice_layout.addWidget(input_label)

        self.input_device_combo = QComboBox()
        self.input_device_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        voice_layout.addWidget(self.input_device_combo)

        # Output device
        output_label = QLabel("🔊 Output Device (Speaker):")
        output_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        voice_layout.addWidget(output_label)

        self.output_device_combo = QComboBox()
        self.output_device_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        voice_layout.addWidget(self.output_device_combo)

        # Refresh devices button
        refresh_btn = QPushButton("🔄 Refresh Devices")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #3d3d3d;
                border: none;
                border-radius: 5px;
                padding: 8px;
                font-size: 11px;
                margin-top: 8px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_audio_devices)
        voice_layout.addWidget(refresh_btn)

        # NEW: Voice Interrupt Mode
        interrupt_label = QLabel("🔇 Voice Interruption:")
        interrupt_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        voice_layout.addWidget(interrupt_label)

        self.interrupt_mode_combo = QComboBox()
        self.interrupt_mode_combo.addItem("Manual (Button in Chat)", "manual")
        self.interrupt_mode_combo.addItem("Automatic (When Speaking)", "auto")
        self.interrupt_mode_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        voice_layout.addWidget(self.interrupt_mode_combo)

        interrupt_info = QLabel(
            "💡 Manual: Click 🔇 button in chat to stop TTS\n"
            "💡 Automatic: TTS stops when you start speaking"
        )
        interrupt_info.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        interrupt_info.setWordWrap(True)
        voice_layout.addWidget(interrupt_info)

        # TTS Provider selection
        tts_provider_label = QLabel("🔊 TTS Provider:")
        tts_provider_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        voice_layout.addWidget(tts_provider_label)

        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItem("Puter.js TTS (Free)", "puter")
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        self.tts_provider_combo.addItem("pyttsx3 (Offline, Free)", "pyttsx3")
        self.tts_provider_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        self.tts_provider_combo.currentIndexChanged.connect(self.on_tts_provider_changed)
        voice_layout.addWidget(self.tts_provider_combo)

        # Edge TTS Voice selection (shown when Edge TTS selected)
        self.edge_tts_group = QWidget()
        edge_tts_layout = QVBoxLayout(self.edge_tts_group)
        edge_tts_layout.setContentsMargins(0, 0, 0, 0)

        tts_label = QLabel("🎵 Edge TTS Voice:")
        tts_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        edge_tts_layout.addWidget(tts_label)

        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)

        # Store voice ID as data, display name as text
        tts_voices = [
            ("en-US-GuyNeural", "Male, American (Guy)"),
            ("en-US-JennyNeural", "Female, American (Jenny)"),
            ("en-GB-RyanNeural", "Male, British (Ryan)"),
            ("en-GB-SoniaNeural", "Female, British (Sonia)"),
            ("en-AU-NatashaNeural", "Female, Australian (Natasha)"),
            ("en-AU-WilliamNeural", "Male, Australian (William)"),
            ("en-CA-LiamNeural", "Male, Canadian (Liam)"),
            ("en-CA-ClaraNeural", "Female, Canadian (Clara)"),
            ("en-IN-NeerjaNeural", "Female, Indian (Neerja)"),
            ("en-IN-PrabhatNeural", "Male, Indian (Prabhat)")
        ]

        for voice_id, voice_name in tts_voices:
            self.tts_voice_combo.addItem(voice_name, voice_id)

        edge_tts_layout.addWidget(self.tts_voice_combo)
        voice_layout.addWidget(self.edge_tts_group)

        # Puter TTS settings (shown when Puter selected)
        self.puter_tts_group = QWidget()
        puter_tts_layout = QVBoxLayout(self.puter_tts_group)
        puter_tts_layout.setContentsMargins(0, 0, 0, 0)

        puter_tts_model_label = QLabel("🔊 Puter TTS Model:")
        puter_tts_model_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        puter_tts_layout.addWidget(puter_tts_model_label)

        self.puter_tts_model_combo = QComboBox()
        self.puter_tts_model_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        puter_tts_layout.addWidget(self.puter_tts_model_combo)

        puter_tts_voice_label = QLabel("🎵 Puter Voice (optional):")
        puter_tts_layout.addWidget(puter_tts_voice_label)

        self.puter_tts_voice_input = QLineEdit()
        self.puter_tts_voice_input.setPlaceholderText("Leave empty for default")
        self.puter_tts_voice_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        puter_tts_layout.addWidget(self.puter_tts_voice_input)

        # NEW: ElevenLabs option
        self.elevenlabs_checkbox = QCheckBox("🎙️ Use ElevenLabs TTS (Premium)")
        self.elevenlabs_checkbox.setStyleSheet("color: #ffffff; font-size: 11pt; margin-top: 10px;")
        self.elevenlabs_checkbox.stateChanged.connect(self.on_elevenlabs_toggled)
        puter_tts_layout.addWidget(self.elevenlabs_checkbox)

        self.elevenlabs_settings = QWidget()
        elevenlabs_layout = QVBoxLayout(self.elevenlabs_settings)
        elevenlabs_layout.setContentsMargins(20, 0, 0, 0)

        elevenlabs_info = QLabel(
            "ElevenLabs offers premium quality TTS with:\n"
            "• 70+ languages\n"
            "• Emotional voice control with [brackets]\n"
            "• Ultra-realistic voices\n\n"
            "Get your voice ID from: https://elevenlabs.io"
        )
        elevenlabs_info.setWordWrap(True)
        elevenlabs_info.setStyleSheet(
            "color: #888; font-size: 9pt; padding: 8px; background: #252525; border-radius: 5px;")
        elevenlabs_layout.addWidget(elevenlabs_info)

        elevenlabs_voice_label = QLabel("Voice ID:")
        elevenlabs_layout.addWidget(elevenlabs_voice_label)

        self.elevenlabs_voice_input = QLineEdit()
        self.elevenlabs_voice_input.setPlaceholderText("e.g., 21m00Tcm4TlvDq8ikWAM")
        self.elevenlabs_voice_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
            }
        """)
        elevenlabs_layout.addWidget(self.elevenlabs_voice_input)

        self.elevenlabs_settings.hide()
        puter_tts_layout.addWidget(self.elevenlabs_settings)

        voice_layout.addWidget(self.puter_tts_group)

        # Advanced options - ENHANCED VAD CONTROLS
        advanced_label = QLabel("⚙️ Voice Detection Options:")
        advanced_label.setStyleSheet("font-weight: bold; margin-top: 15px;")
        voice_layout.addWidget(advanced_label)

        # VAD Type Selection
        vad_type_layout = QHBoxLayout()
        vad_type_label = QLabel("VAD Engine:")
        vad_type_layout.addWidget(vad_type_label)

        # WebRTC VAD toggle
        self.webrtc_vad_checkbox = QCheckBox("WebRTC VAD")
        self.webrtc_vad_checkbox.setChecked(True)  # Default on
        self.webrtc_vad_checkbox.setStyleSheet("color: #ffffff;")
        self.webrtc_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        vad_type_layout.addWidget(self.webrtc_vad_checkbox)

        # Silero VAD toggle
        self.silero_vad_checkbox = QCheckBox("Silero VAD")
        self.silero_vad_checkbox.setChecked(False)  # Default off
        self.silero_vad_checkbox.setStyleSheet("color: #ffffff;")
        self.silero_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        vad_type_layout.addWidget(self.silero_vad_checkbox)

        vad_type_layout.addStretch()
        voice_layout.addLayout(vad_type_layout)

        # WebRTC VAD Aggressiveness (dynamic visibility)
        self.webrtc_settings_widget = QWidget()
        webrtc_settings_layout = QVBoxLayout(self.webrtc_settings_widget)
        webrtc_settings_layout.setContentsMargins(20, 5, 0, 5)

        webrtc_agg_layout = QHBoxLayout()
        webrtc_agg_layout.addWidget(QLabel("WebRTC Aggressiveness:"))
        self.vad_combo = QComboBox()
        self.vad_combo.addItem("0 (Least)", 0)
        self.vad_combo.addItem("1 (Low)", 1)
        self.vad_combo.addItem("2 (Medium)", 2)
        self.vad_combo.addItem("3 (Highest)", 3)
        self.vad_combo.setCurrentIndex(3)
        self.vad_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 4px;
                font-size: 10px;
            }
        """)
        webrtc_agg_layout.addWidget(self.vad_combo)
        webrtc_settings_layout.addLayout(webrtc_agg_layout)

        webrtc_info = QLabel("💡 Higher = Less sensitive to background noise")
        webrtc_info.setStyleSheet("color: #888; font-size: 9pt;")
        webrtc_settings_layout.addWidget(webrtc_info)

        voice_layout.addWidget(self.webrtc_settings_widget)

        # Silero VAD Threshold (dynamic visibility)
        self.silero_settings_widget = QWidget()
        silero_settings_layout = QVBoxLayout(self.silero_settings_widget)
        silero_settings_layout.setContentsMargins(20, 5, 0, 5)

        silero_threshold_layout = QHBoxLayout()
        silero_threshold_layout.addWidget(QLabel("Silero Threshold:"))
        self.silero_threshold_combo = QComboBox()
        self.silero_threshold_combo.addItem("0.3 (Very Sensitive)", 0.3)
        self.silero_threshold_combo.addItem("0.5 (Balanced)", 0.5)
        self.silero_threshold_combo.addItem("0.7 (Conservative)", 0.7)
        self.silero_threshold_combo.addItem("0.9 (Very Conservative)", 0.9)
        self.silero_threshold_combo.setCurrentIndex(1)  # Default 0.5
        self.silero_threshold_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 4px;
                font-size: 10px;
            }
        """)
        silero_threshold_layout.addWidget(self.silero_threshold_combo)
        silero_settings_layout.addLayout(silero_threshold_layout)

        silero_info = QLabel("💡 Higher threshold = Less false positives")
        silero_info.setStyleSheet("color: #888; font-size: 9pt;")
        silero_settings_layout.addWidget(silero_info)

        self.silero_settings_widget.hide()  # Hidden by default
        voice_layout.addWidget(self.silero_settings_widget)

        voice_group.setLayout(voice_layout)
        scroll_layout.addWidget(voice_group)

        # Debug Mode Section
        debug_group = QGroupBox("🔧 Debug Options")
        debug_layout = QVBoxLayout()

        self.debug_checkbox = QCheckBox("Enable Debug Mode")
        self.debug_checkbox.setStyleSheet("""
            QCheckBox {
                color: #ffffff;
                font-size: 11pt;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        debug_layout.addWidget(self.debug_checkbox)

        debug_info = QLabel(
            "When enabled:\n"
            "• Shows tool usage conversations in a separate window\n"
            "• See what the AI is doing behind the scenes\n"
            "• View tool inputs and outputs\n"
            "• Monitor the AI's decision-making process"
        )
        debug_info.setWordWrap(True)
        debug_info.setStyleSheet("color: #888; font-size: 9pt; margin-top: 5px;")
        debug_layout.addWidget(debug_info)

        debug_group.setLayout(debug_layout)
        scroll_layout.addWidget(debug_group)

        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

        # Save button
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        save_btn = QPushButton("💾 Save Settings")
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #55ff55;
                color: #000;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #66ff66;
            }
        """)
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)

        main_layout.addLayout(button_layout)

        self.setLayout(main_layout)
        # CRITICAL: Set initial visibility based on default provider
        # This ensures proper state before settings are loaded
        initial_provider = self.provider_combo.currentText()
        self.on_provider_changed(initial_provider)

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

    def mousePressEvent(self, event):
        """Handle mouse press for dragging"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_offset = event.pos()

    def mouseMoveEvent(self, event):
        """Handle mouse move for dragging"""
        if self._is_dragging:
            self.move(self.mapToGlobal(event.pos() - self._drag_offset))

    def mouseReleaseEvent(self, event):
        """Handle mouse release"""
        self._is_dragging = False

    def on_provider_changed(self, provider):
        """Handle provider change - DYNAMIC VISIBILITY + AUTO-SWITCH"""
        # Hide all provider-specific groups first
        self.anthropic_group.hide()
        self.gemini_group.hide()
        self.puter_group.hide()
        self.llama_group.hide()  # NEW

        # CRITICAL: Always hide Puter-specific sections first
        self.puter_account_group.hide()
        self.recommended_group.hide()
        self.puter_tts_group.hide()

        # Show relevant sections based on provider
        if provider == 'puter':
            self.puter_group.show()
            self.puter_account_group.show()
            self.recommended_group.show()
            self.update_tts_provider_options(show_puter=True)
        elif provider == 'gemini':
            self.gemini_group.show()
            self.update_tts_provider_options(show_puter=False)
        elif provider == 'llama':  # NEW
            self.llama_group.show()
            self.update_tts_provider_options(show_puter=False)
        else:  # anthropic
            self.anthropic_group.show()
            self.update_tts_provider_options(show_puter=False)

    def log_status(self, message):
        """Helper to log status messages"""
        print(f"[Settings] {message}")

    def on_elevenlabs_toggled(self, state):
        """Handle ElevenLabs checkbox toggle"""
        if state == 2:  # Checked
            self.elevenlabs_settings.show()
        else:
            self.elevenlabs_settings.hide()

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

    def check_llama_status(self):
        """Check LLaMA status safely - called after window is shown"""
        try:
            # Clear previous content
            while self.llama_models_layout.count():
                item = self.llama_models_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # Check availability
            if self.controller.get_llama_available():
                self.llama_status_label.setText("✓ LLaMA is installed and ready!")
                self.llama_status_label.setStyleSheet("color: #55ff55; font-weight: bold;")

                # List models
                models = self.controller.get_llama_models()
                if models:
                    model_list = QLabel(f"**Available Models:** {len(models)}")
                    self.llama_models_layout.addWidget(model_list)

                    for model in models:
                        model_label = QLabel(f"  • {model['name']} ({model['size_mb']} MB)")
                        model_label.setStyleSheet("color: #ccc; font-size: 9pt;")
                        self.llama_models_layout.addWidget(model_label)
                else:
                    no_models = QLabel("⚠️ No models found. Download a model to get started:")
                    self.llama_models_layout.addWidget(no_models)

                    instructions = QTextEdit()
                    instructions.setPlainText(self.controller.get_llama_download_instructions())
                    instructions.setReadOnly(True)
                    instructions.setMaximumHeight(150)
                    instructions.setStyleSheet("""
                        QTextEdit {
                            background-color: #2d2d2d;
                            border: 1px solid #3d3d3d;
                            border-radius: 5px;
                            padding: 8px;
                            font-size: 10px;
                            color: #E8EAED;
                            font-family: monospace;
                        }
                    """)
                    self.llama_models_layout.addWidget(instructions)
            else:
                self.llama_status_label.setText("⚠️ LLaMA not installed")
                self.llama_status_label.setStyleSheet("color: #ff5555; font-family: monospace;")

                llama_error = QLabel("Install with: pip install llama-cpp-python")
                llama_error.setStyleSheet("color: #ff5555; font-family: monospace; font-size: 9pt;")
                self.llama_models_layout.addWidget(llama_error)

        except Exception as e:
            self.llama_status_label.setText(f"⚠️ Error checking LLaMA: {str(e)[:50]}")
            self.llama_status_label.setStyleSheet("color: #ff5555;")

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

        # Load API key
        api_key = self.controller.get_api_key()
        if api_key:
            self.api_key_input.setText(api_key)

        # Load Gemini API key
        gemini_key = self.controller.get_gemini_api_key()
        if gemini_key:
            self.gemini_key_input.setText(gemini_key)

        # Load Puter model
        puter_model = self.controller.get_puter_model()
        index = self.puter_model_combo.findData(puter_model)
        if index >= 0:
            self.puter_model_combo.setCurrentIndex(index)

        # Load Gemini model
        gemini_model = self.controller.get_gemini_model()
        index = self.gemini_model_combo.findData(gemini_model)
        if index >= 0:
            self.gemini_model_combo.setCurrentIndex(index)

        # Load debug mode
        debug_mode = self.controller.get_debug_mode()
        self.debug_checkbox.setChecked(debug_mode)

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
        self.puter_email_input.setText(creds['email'])
        self.puter_password_input.setText(creds['password'])

        # Load TTS provider
        tts_provider = self.controller.get_tts_provider()
        index = self.tts_provider_combo.findData(tts_provider)
        if index >= 0:
            self.tts_provider_combo.setCurrentIndex(index)

        # Load Puter TTS settings
        puter_tts_model = self.controller.get_puter_tts_model()
        puter_tts_voice = self.controller.get_puter_tts_voice()

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

        # Load LLaMA settings
        llama_settings = self.controller.get_llama_settings()
        self.llama_tokens_input.setText(str(llama_settings['max_tokens']))

        for i in range(self.llama_temp_combo.count()):
            if self.llama_temp_combo.itemData(i) == llama_settings['temperature']:
                self.llama_temp_combo.setCurrentIndex(i)
                break

        for i in range(self.llama_topp_combo.count()):
            if self.llama_topp_combo.itemData(i) == llama_settings['top_p']:
                self.llama_topp_combo.setCurrentIndex(i)
                break

        self.llama_override_checkbox.setChecked(llama_settings['override_enabled'])

        # Load LLaMA models into combo
        models = self.controller.get_llama_models()
        self.llama_model_combo.clear()
        for model in models:
            self.llama_model_combo.addItem(model['name'], model['path'])

        # NEW: Check LLaMA status AFTER window is fully loaded (delayed)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self.check_llama_status)

    def save_settings(self):
        """Save settings to controller - FIXED: Actually applies voice settings!"""
        # Save provider
        provider = self.provider_combo.currentText()
        self.controller.set_ai_provider(provider)

        # Save API key
        api_key = self.api_key_input.text().strip()
        self.controller.set_api_key(api_key)

        # Save Gemini API key
        gemini_key = self.gemini_key_input.text().strip()
        self.controller.set_gemini_api_key(gemini_key)

        # Save Puter model
        puter_model = self.puter_model_combo.currentData()
        self.controller.set_puter_model(puter_model)

        # Save Gemini model
        gemini_model = self.gemini_model_combo.currentData()
        self.controller.set_gemini_model(gemini_model)

        # Save debug mode
        debug_mode = self.debug_checkbox.isChecked()
        self.controller.set_debug_mode(debug_mode)

        # Save voice devices
        input_device = self.input_device_combo.currentData()
        self.controller.set_voice_input_device(input_device)

        output_device = self.output_device_combo.currentData()
        self.controller.set_voice_output_device(output_device)

        # Save Puter credentials
        email = self.puter_email_input.text().strip()
        password = self.puter_password_input.text().strip()
        self.controller.set_puter_credentials(email, password)

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

        # Save LLaMA settings
        try:
            max_tokens = int(self.llama_tokens_input.text() or "2000")
        except:
            max_tokens = 2000

        temperature = self.llama_temp_combo.currentData()
        top_p = self.llama_topp_combo.currentData()
        override_enabled = self.llama_override_checkbox.isChecked()

        self.controller.set_llama_settings(max_tokens, temperature, top_p, override_enabled)

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
            webbrowser.open('http://127.0.0.1:5555')
            self.show_status_message("✓ Opening Puter interface...")
        except Exception as e:
            self.show_status_message(f"✗ Error: {e}")

    def on_llama_override_toggled(self, state):
        """Show/hide warning when override is toggled"""
        if state == 2:  # Checked
            self.llama_warning_label.show()
        else:
            self.llama_warning_label.hide()

    def reload_llama_model(self):
        """Reload current LLaMA model"""
        success, message = self.controller.reload_llama_model()
        self.show_status_message(message)

    def switch_llama_model(self):
        """Switch to selected LLaMA model"""
        model_path = self.llama_model_combo.currentData()
        if not model_path:
            self.show_status_message("No model selected")
            return

        success, message = self.controller.switch_llama_model(model_path)
        self.show_status_message(message)

    def show_status_message(self, message):
        """Show a temporary status message"""
        print(f"[Settings] {message}")