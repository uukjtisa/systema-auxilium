"""
Settings Window - Configure API key, AI provider, Puter.js settings, Google Gemini, and Voice
FIXED: Voice settings now actually work - VAD and TTS voice selection are functional
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QComboBox, QGroupBox, QCheckBox, QScrollArea, QFrame)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect
from PyQt6.QtGui import QRegion


class SettingsWindow(QWidget):
    """Settings window for configuring the AI assistant"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        # Window dragging and resizing state
        self.dragging = False
        self.drag_position = QPoint()
        self.resizing = False
        self.resize_edge = None
        self.resize_start_geometry = None
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.save_window_geometry)

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
        self.container.setAutoFillBackground(True)  # ADD THIS LINE
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #1e1e1e;
                border-radius: 12px;
            }
            QWidget {
                color: #ffffff;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
            QScrollArea {
                background-color: #1e1e1e;
            }
            QScrollArea > QWidget {
                background-color: #1e1e1e;
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
        """Initialize UI"""
        main_layout = QVBoxLayout(self.container)  # Changed: use container
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header bar (draggable)
        header_bar = QFrame()
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border-bottom: 1px solid #2A2A2A;
            }
        """)

        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        # Title
        title = QLabel("⚙️ Settings")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        header_layout.addWidget(title)

        header_layout.addStretch()

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

        # Maximize button
        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setFixedSize(32, 32)
        self.maximize_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background: #2A2A2A;
                color: #E8EAED;
            }
        """)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        header_layout.addWidget(self.maximize_btn)

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
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        main_layout.addWidget(header_bar)

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
        scroll_widget.setStyleSheet("""
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QGroupBox {
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title {
                color: #ffffff;
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QLabel {
                color: #ffffff;
            }
        """)
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(15, 15, 15, 15)  # Add padding
        scroll_layout.setSpacing(15)

        # AI Provider Selection
        provider_group = QGroupBox("AI Provider")
        provider_layout = QVBoxLayout()

        provider_label = QLabel("Select AI Provider:")
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["anthropic", "gemini", "puter"])
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
                color: #ffffff;
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
                color: #ffffff;
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
        gemini_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
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
                color: #ffffff;
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
                color: #ffffff;
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

        gemini_link = QLabel(
            '<a href="https://aistudio.google.com/apikey" style="color: #5555ff;">Get API key: https://aistudio.google.com/apikey</a>')
        gemini_link.setOpenExternalLinks(True)
        gemini_link.setStyleSheet("color: #888; font-size: 9pt;")
        gemini_layout.addWidget(gemini_link)

        self.gemini_group.setLayout(gemini_layout)
        scroll_layout.addWidget(self.gemini_group)

        # LLaMA support removed

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
        puter_info.setStyleSheet(
            "color: #ccc; font-size: 10pt; padding: 10px; background: #2d2d2d; border-radius: 5px;")
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
                color: #ffffff;
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
                color: #ffffff;
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
                color: #ffffff;
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
                color: #ffffff;
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
                color: #ffffff;
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

        # Response Timeout
        timeout_label = QLabel("⏱️ Response Timeout (seconds):")
        timeout_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
        puter_account_layout.addWidget(timeout_label)

        timeout_container = QHBoxLayout()
        self.puter_timeout_input = QLineEdit()
        self.puter_timeout_input.setPlaceholderText("30")
        self.puter_timeout_input.setMaximumWidth(100)
        self.puter_timeout_input.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 8px;
                font-size: 12px;
                color: #ffffff;
            }
        """)
        timeout_container.addWidget(self.puter_timeout_input)

        timeout_info = QLabel("How long to wait for AI response")
        timeout_info.setStyleSheet("color: #888; font-size: 9pt; margin-left: 10px;")
        timeout_container.addWidget(timeout_info)
        timeout_container.addStretch()

        puter_account_layout.addLayout(timeout_container)

        timeout_desc = QLabel(
            "💡 Increase this if using ultra-smart models that take longer to respond\n"
            "   Default: 30 seconds. Try 60-90 for complex queries."
        )
        timeout_desc.setWordWrap(True)
        timeout_desc.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        puter_account_layout.addWidget(timeout_desc)

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
                color: #ffffff;
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
                color: #ffffff;
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
        input_label.setStyleSheet("font-weight: bold; margin-top: 10px; color: #ffffff;")
        voice_layout.addWidget(input_label)

        self.input_device_combo = QComboBox()
        self.input_device_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
                color: #ffffff;
            }
        """)
        voice_layout.addWidget(self.input_device_combo)

        # Output device
        output_label = QLabel("🔊 Output Device (Speaker):")
        output_label.setStyleSheet("font-weight: bold; margin-top: 10px; color: #ffffff;")
        voice_layout.addWidget(output_label)

        self.output_device_combo = QComboBox()
        self.output_device_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
                color: #ffffff;
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
                color: #ffffff;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_audio_devices)
        voice_layout.addWidget(refresh_btn)

        # NEW: Voice Interrupt Mode
        interrupt_label = QLabel("🔇 Voice Interruption:")
        interrupt_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
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
                color: #ffffff;
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
        tts_provider_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
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
                color: #ffffff;
            }
        """)
        self.tts_provider_combo.currentIndexChanged.connect(self.on_tts_provider_changed)
        voice_layout.addWidget(self.tts_provider_combo)

        # Edge TTS Voice selection (shown when Edge TTS selected)
        self.edge_tts_group = QWidget()
        edge_tts_layout = QVBoxLayout(self.edge_tts_group)
        edge_tts_layout.setContentsMargins(0, 0, 0, 0)

        tts_label = QLabel("🎵 Edge TTS Voice:")
        tts_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
        edge_tts_layout.addWidget(tts_label)

        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
                color: #ffffff;
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
        puter_tts_model_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
        puter_tts_layout.addWidget(puter_tts_model_label)

        self.puter_tts_model_combo = QComboBox()
        self.puter_tts_model_combo.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                padding: 6px;
                font-size: 11px;
                color: #ffffff;
            }
        """)
        puter_tts_layout.addWidget(self.puter_tts_model_combo)

        puter_tts_voice_label = QLabel("🎵 Puter Voice (optional):")
        puter_tts_voice_label.setStyleSheet("color: #ffffff;")
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
                color: #ffffff;
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
        elevenlabs_voice_label.setStyleSheet("color: #ffffff;")
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
                color: #ffffff;
            }
        """)
        elevenlabs_layout.addWidget(self.elevenlabs_voice_input)

        self.elevenlabs_settings.hide()
        puter_tts_layout.addWidget(self.elevenlabs_settings)

        voice_layout.addWidget(self.puter_tts_group)

        # Advanced options - ENHANCED VAD CONTROLS
        advanced_label = QLabel("⚙️ Voice Detection Options:")
        advanced_label.setStyleSheet("font-weight: bold; margin-top: 15px; color: #ffffff;")
        voice_layout.addWidget(advanced_label)

        # VAD Type Selection
        vad_type_layout = QHBoxLayout()
        vad_type_label = QLabel("VAD Engine:")
        vad_type_label.setStyleSheet("color: #ffffff;")
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
        webrtc_agg_label = QLabel("WebRTC Aggressiveness:")
        webrtc_agg_label.setStyleSheet("color: #ffffff;")
        webrtc_agg_layout.addWidget(webrtc_agg_label)
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
                color: #ffffff;
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
        silero_threshold_label = QLabel("Silero Threshold:")
        silero_threshold_label.setStyleSheet("color: #ffffff;")
        silero_threshold_layout.addWidget(silero_threshold_label)
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
                color: #ffffff;
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
                color: #ffffff;
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

    def on_provider_changed(self, provider):
        """Handle provider change - DYNAMIC VISIBILITY"""
        # Hide all provider-specific groups first
        self.anthropic_group.hide()
        self.gemini_group.hide()
        self.puter_group.hide()

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

        # Load Puter timeout
        puter_timeout = self.controller.settings.get('puter_timeout', 30)
        self.puter_timeout_input.setText(str(puter_timeout))

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

        # Save Puter timeout
        timeout_text = self.puter_timeout_input.text().strip()
        if timeout_text:
            try:
                timeout = int(timeout_text)
                if timeout > 0:
                    self.controller.set_puter_timeout(timeout)
                else:
                    self.controller.set_puter_timeout(30)  # Default if invalid
            except ValueError:
                self.controller.set_puter_timeout(30)  # Default if invalid
        else:
            self.controller.set_puter_timeout(30)  # Default if empty

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

    def show_status_message(self, message):
        """Show a temporary status message"""
        print(f"[Settings] {message}")

    def apply_rounded_mask(self):
        """Apply rounded corners mask"""
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, event):
        """Handle window resize"""
        super().resizeEvent(event)
        self.apply_rounded_mask()
        if hasattr(self, 'resize_handles'):
            self.position_resize_handles()
        if hasattr(self, 'resize_timer'):
            self.resize_timer.stop()
            self.resize_timer.start(1000)

    def save_window_geometry(self):
        """Save window geometry - placeholder for future implementation"""
        pass

    def toggle_maximize(self):
        """Toggle maximize/restore"""
        if self.isMaximized():
            self.showNormal()
            self.maximize_btn.setText("□")
        else:
            self.showMaximized()
            self.maximize_btn.setText("❐")

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
        """Handle resize handle events"""
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