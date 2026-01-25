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
                             QFrame, QMenu, QScrollArea, QApplication)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QRect
from PyQt6.QtGui import QAction, QCursor, QRegion
import markdown2
import os
import json
import threading


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
        self.text_input.clear()
        self.text_input.manual_resize = False
        self.text_input.setFixedHeight(self.min_height)
        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled):
        self.text_input.setEnabled(enabled)

    def setPlaceholderText(self, text):
        self.text_input.setPlaceholderText(text)


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
        self.config_file = "chat_config.json"
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

    def resizeEvent(self, event):
        """Handle window resize to maintain layout"""
        super().resizeEvent(event)
        # Force layout update when window is resized
        if hasattr(self, 'chat_widget'):
            self.chat_widget.updateGeometry()

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

        # === SIDEBAR ===
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(240)
        self.sidebar.setStyleSheet("""
            QFrame {
                background-color: #171717;
                border-right: 1px solid #2A2A2A;
            }
        """)
        self.sidebar.hide()  # Start hidden

        # Create main sidebar layout (contains scroll area)
        sidebar_main_layout = QVBoxLayout(self.sidebar)
        sidebar_main_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_main_layout.setSpacing(0)

        # Create scroll area for sidebar content
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setStyleSheet("""
                QScrollArea {
                    border: none;
                    background-color: transparent;
                }
                QScrollBar:vertical {
                    background: #171717;
                    width: 8px;
                    border-radius: 4px;
                }
                QScrollBar::handle:vertical {
                    background: #3C3C3C;
                    border-radius: 4px;
                    min-height: 20px;
                }
                QScrollBar::handle:vertical:hover {
                    background: #4A4A4A;
                }
            """)

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

        # NEW: Personalization Instructions - Replace the entire personalization_container section with this:
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

        # Mode info
        mode_info = QLabel(
            "<b>Force Modes:</b><br>"
            "Use the dropdown in the<br>"
            "message box to force AI<br>"
            "to use specific modes."
        )
        mode_info.setWordWrap(True)
        mode_info.setStyleSheet("""
            QLabel {
                color: #9AA0A6;
                font-size: 10px;
                padding: 8px;
                background-color: #252525;
                border-radius: 6px;
                line-height: 1.4;
            }
        """)
        sidebar_layout.addWidget(mode_info)

        # At the end of sidebar content, add stretch
        sidebar_layout.addStretch()

        # Add clear button at the bottom
        clear_btn = QPushButton("🗑️ Clear Chat")
        clear_btn.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: 1px solid #3C3C3C;
                    border-radius: 6px;
                    padding: 8px;
                    font-size: 11px;
                    color: #9AA0A6;
                }
                QPushButton:hover {
                    background-color: #2A2A2A;
                    color: #E8EAED;
                }
            """)
        clear_btn.clicked.connect(self.clear_chat)
        sidebar_layout.addWidget(clear_btn)

        # Set the content widget to the scroll area
        sidebar_scroll.setWidget(sidebar_content)

        # Add scroll area to main sidebar layout
        sidebar_main_layout.addWidget(sidebar_scroll)

        main_layout.addWidget(self.sidebar)

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

        # Toggle sidebar button
        self.toggle_sidebar_btn = QPushButton("☰")
        self.toggle_sidebar_btn.setFixedSize(32, 32)
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
        header_layout.addWidget(self.toggle_sidebar_btn)

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

        # Window control buttons

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
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

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
                background: #212121;
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #3C3C3C;
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #4A4A4A;
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

        scroll_area.setWidget(self.chat_widget)
        chat_layout.addWidget(scroll_area)

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

        # Mode selector and input combined - Update the combined_container styling
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

        # Text input - Replace the section where self.input_field is created and styled
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

        # Interrupt button (only shown during tool mode)
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
        self.interrupt_btn.clicked.connect(self.interrupt_tool_mode)
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

        # Load personalization
        self.load_personalization()

        # Welcome message
        self.add_system_message(
            "👋 **Welcome to Systema Auxilium!**\n\n"
            "I can execute Python code and control your system. "
            "Click the 💬 icon to force specific modes:\n"
            "• **Tools** - Operations that return values\n"
            "• **Commands** - Quick actions without feedback"
        )

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
            self.voice_btn_inline.setChecked(True)  # Changed
            self.add_system_message(f"🎤 **Voice Mode Enabled**\n\n{message}")
            self.update_voice_status("Ready")
        else:
            self.voice_enabled = False
            self.voice_btn_inline.setChecked(False)  # Changed
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
        # Remove custom_instructions loading since it's now in the dialog
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

        # NEW: Show interrupt button during speaking (manual mode only)
        if status == 'speaking' and self.controller.get_voice_interrupt_mode() == 'manual':
            self.voice_interrupt_btn.show()
        else:
            self.voice_interrupt_btn.hide()

    def closeEvent(self, event):
        """Handle window close - just hide, don't close app"""
        self.hide()
        event.ignore()  # Prevent the window from actually closing

    def toggle_sidebar(self):
        """Toggle sidebar visibility"""
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.sidebar.show()
            # Force layout update
            self.sidebar.updateGeometry()
            self.layout().update()
        else:
            self.sidebar.hide()
            # Force layout update
            self.layout().update()

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

        # Text area - Update the text_edit section with better styling
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
        tool_action = QAction("🔧 Use Tools", self)
        tool_action.triggered.connect(lambda: self.set_force_mode('tool'))
        menu.addAction(tool_action)

        # Command mode
        command_action = QAction("⚡ Use Commands", self)
        command_action.triggered.connect(lambda: self.set_force_mode('command'))
        menu.addAction(command_action)

        # Show menu below button
        button_pos = self.mode_dropdown.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(button_pos.x(), button_pos.y() - menu.sizeHint().height()))

    def set_force_mode(self, mode):
        """Set force mode"""
        self.force_mode = mode

        if mode == 'tool':
            self.mode_dropdown.setText("🔧")
            self.add_system_message("🔧 **Tool Mode** - AI will use tools for operations")
        elif mode == 'command':
            self.mode_dropdown.setText("⚡")
            self.add_system_message("⚡ **Command Mode** - AI will use commands for quick actions")
        else:
            self.mode_dropdown.setText("💬")
            self.add_system_message("💬 **Normal Mode** - AI decides when to use tools or commands")

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

    def render_markdown(self, text):
        """Render markdown to HTML"""
        try:
            html = markdown2.markdown(text, extras=["fenced-code-blocks", "tables", "break-on-newline"])
            return html
        except:
            return text.replace('\n', '<br>')

    def clear_chat(self):
        """Clear chat history"""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.controller.ai.clear_history()
        self.add_system_message("🔄 **Chat Cleared** - Ready for a new conversation!")

    def add_user_message(self, message):
        """Add user message"""
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

        # Add stretch to push message to the right
        message_layout.addStretch()

        # Container for name and content (with max width for readability)
        main_container_widget = QWidget()
        main_container_widget.setMaximumWidth(600)  # Max width for readability
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        # Name header (OUTSIDE border)
        name_label = QLabel("<b>You</b>")
        name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px;")
        main_container.addWidget(name_label)

        # Message content container (with border) - using QFrame for positioning
        content_wrapper = QFrame()
        content_wrapper.setStyleSheet("""
            QFrame {
                background-color: #252525;
                border: 1px solid #3C3C3C;
                border-radius: 12px;
            }
        """)

        # Use absolute positioning for copy button
        content_wrapper_layout = QHBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(10, 10, 10, 10)
        content_wrapper_layout.setSpacing(8)

        # Copy button on the LEFT side for user messages
        copy_btn = QPushButton("📋")
        copy_btn.setFixedSize(24, 24)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background-color: #3C3C3C;
                color: #E8EAED;
            }
        """)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(message))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        content_wrapper_layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignTop)

        # Text label
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
        content_wrapper_layout.addWidget(text_label, 1)

        main_container.addWidget(content_wrapper)
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

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self.scroll_to_bottom()

    def add_ai_message(self, message):
        """Add AI message with markdown rendering"""
        # NEW: Remove emotion brackets for DISPLAY only
        display_message = self._clean_emotion_brackets(message)

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

        # Container for name and content (with max width for readability)
        main_container_widget = QWidget()
        main_container_widget.setMaximumWidth(600)  # Max width for readability
        main_container = QVBoxLayout(main_container_widget)
        main_container.setSpacing(4)
        main_container.setContentsMargins(0, 0, 0, 0)

        # Name header (OUTSIDE border)
        name_label = QLabel("<b>Systema Auxilium</b>")
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px;")
        main_container.addWidget(name_label)

        # Message content with border
        content_wrapper = QFrame()
        content_wrapper.setStyleSheet("""
            QFrame {
                background-color: #2A2A2A;
                border: 1px solid #3C3C3C;
                border-radius: 12px;
            }
        """)

        content_wrapper_layout = QHBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(10, 10, 10, 10)
        content_wrapper_layout.setSpacing(8)

        # Text label
        text_label = QLabel()
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setText(self.render_markdown(display_message))
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
        content_wrapper_layout.addWidget(text_label, 1)

        # Copy button on the RIGHT side for AI messages
        copy_btn = QPushButton("📋")
        copy_btn.setFixedSize(24, 24)
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                color: #9AA0A6;
            }
            QPushButton:hover {
                background-color: #3C3C3C;
                color: #E8EAED;
            }
        """)
        copy_btn.clicked.connect(lambda: self.copy_to_clipboard(display_message))
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        content_wrapper_layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignTop)

        main_container.addWidget(content_wrapper)
        message_layout.addWidget(main_container_widget)

        # Add stretch to keep AI messages on the left
        message_layout.addStretch()

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self.scroll_to_bottom()

    def _clean_emotion_brackets(self, text):
        """
        Remove ElevenLabs emotion brackets from text for display
        Examples: [happy] → removed, [giggles] → removed
        """
        import re
        cleaned = re.sub(r'\[([^\]]+)\]', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)  # Clean double spaces
        return cleaned.strip()

    def add_system_message(self, message):
        """Add system message"""
        # Store current input height before layout changes
        current_height = self.input_field.text_input.height()

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
        )  # ADD THIS LINE
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
        self.scroll_to_bottom()

        # Restore input height after layout update
        QTimer.singleShot(10, lambda: self.input_field.text_input.setFixedHeight(current_height))

    def copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        # Optional: Show a brief feedback message
        self.status_label.setText("✓ Copied to clipboard")
        QTimer.singleShot(2000, lambda: self.status_label.setText(""))

    def scroll_to_bottom(self):
        """Scroll to bottom"""
        QTimer.singleShot(50, self._do_scroll)

    def _do_scroll(self):
        """Perform scroll"""
        scroll_area = self.chat_widget.parent().parent()
        if isinstance(scroll_area, QScrollArea):
            scrollbar = scroll_area.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def send_message(self):
        """Send message"""
        message = self.input_field.toPlainText().strip()
        if not message:
            return

        # Check if Puter provider and has attached image
        image_path = None
        if self.controller.get_ai_provider() == 'puter' and self.attached_image:
            image_path = self.attached_image
            self.add_system_message(f"📎 Image attached: {image_path}")

        # Add mode instruction if forced
        if self.force_mode == 'tool':
            message = "[VERY CRITICAL: USE TOOLS ONLY] " + message
        elif self.force_mode == 'command':
            message = "[VERY CRITICAL: USE COMMANDS ONLY] " + message

        display_message = self.input_field.toPlainText().strip()
        self.add_user_message(display_message)

        self.input_field.clear()
        self.attached_image = None  # Clear after sending

        # Send with image if available
        if image_path:
            self.controller.send_message_with_image(message, image_path)
        else:
            self.controller.send_message(message)

    def set_input_enabled(self, enabled):
        """Enable/disable input"""
        self.input_field.setEnabled(enabled)
        self.send_btn.setEnabled(enabled)
        if enabled:
            self.input_field.setPlaceholderText("Send a message... (Shift+Enter for new line)")
        else:
            self.input_field.setPlaceholderText("AI is working... please wait")

    def show_ai_message(self, message):
        if not message or not message.strip():
            return

        # In voice mode and NOT in tool mode, start voice and wait for callback
        if self.voice_enabled and not self.controller.ai.tool_manager.in_tool_mode:
            self.log("[Voice] Buffering message, starting TTS...")

            # Buffer message
            self.pending_voice_message = message
            self.waiting_for_playback = True

            # Use the standard thinking animation instead
            self.start_thinking_animation()

            # Start TTS (will trigger callback when playback begins)
            self.speak_ai_response(message)
        else:
            # Not in voice mode OR in tool mode - display immediately
            self.add_ai_message(message)

    def log(self, msg):
        """Helper for logging"""
        print(f"[ChatWindow] {msg}")

    def on_voice_playback_started(self):
        """Called when TTS playback actually starts (from background thread)"""
        self.log("[Voice] Playback started callback received")
        # Emit signal to handle on main thread
        self.voice_playback_signal.emit()

    def _handle_voice_playback_on_main_thread(self):
        """Handle voice playback on main Qt thread"""
        self.log("[Voice] Processing playback on main thread")

        if self.waiting_for_playback and self.pending_voice_message:
            self.log("[Voice] Displaying buffered message NOW")
            self.waiting_for_playback = False

            # Stop thinking animation
            self.stop_thinking_animation()

            # NOW display the message
            self.add_ai_message(self.pending_voice_message)

            # Clear pending message
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

    def interrupt_tool_mode(self):
        """Interrupt current tool operation"""
        if self.controller.interrupt_tool_mode():
            self.interrupt_btn.hide()
            self.send_btn.show()

    def interrupt_voice(self):
        """Interrupt TTS playback"""
        self.controller.voice_handler.interrupt_speech()
        self.voice_interrupt_btn.hide()

    def show_thinking(self):
        """Show thinking animation"""
        self.start_thinking_animation()
        self.thinking_label_shown = True
        self.set_input_enabled(False)
        # Show interrupt button, hide send button
        self.send_btn.hide()
        self.interrupt_btn.show()

    def hide_thinking(self):
        """Hide thinking animation"""
        self.stop_thinking_animation()
        self.thinking_label_shown = False
        self.set_input_enabled(True)
        # Hide interrupt button, show send button
        self.interrupt_btn.hide()
        self.send_btn.show()

    def apply_rounded_mask(self):
        """Apply rounded corners mask"""
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)  # 12px radius to match CSS
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, event):
        """Handle window resize to maintain layout and rounded corners"""
        super().resizeEvent(event)
        self.apply_rounded_mask()
        if hasattr(self, 'chat_widget'):
            self.chat_widget.updateGeometry()

        # Reposition resize handles
        if hasattr(self, 'resize_handles'):
            self.position_resize_handles()

        # Debounced save
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
                        # Update maximize button state after loading geometry
                        if self.isMaximized():
                            self.maximize_btn.setText("❐")
                        else:
                            self.maximize_btn.setText("□")
        except Exception as e:
            print(f"Error loading window geometry: {e}")

    def toggle_maximize(self):
        """Toggle maximize/restore"""
        if self.isMaximized():
            self.showNormal()
            self.maximize_btn.setText("□")  # Square for maximize
        else:
            self.showMaximized()
            self.maximize_btn.setText("❐")  # Double square for restore

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

        # Edge handles
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
            handle.raise_()  # ADD THIS - bring to front

        # Corner handles
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
            handle.raise_()  # ADD THIS - bring to front

        self.position_resize_handles()

    def position_resize_handles(self):
        """Position resize handles based on window size"""
        w = self.width()
        h = self.height()
        handle_size = 8
        corner_size = 16
        header_height = 50  # Exclude header from top resize

        # Edges
        self.resize_handles['top'].setGeometry(corner_size, header_height, w - 2 * corner_size, handle_size)
        self.resize_handles['bottom'].setGeometry(corner_size, h - handle_size, w - 2 * corner_size, handle_size)
        self.resize_handles['left'].setGeometry(0, corner_size, handle_size, h - 2 * corner_size)
        self.resize_handles['right'].setGeometry(w - handle_size, corner_size, handle_size, h - 2 * corner_size)

        # Corners
        self.resize_handles['top-left'].setGeometry(0, header_height, corner_size, corner_size)
        self.resize_handles['top-right'].setGeometry(w - corner_size, header_height, corner_size, corner_size)
        self.resize_handles['bottom-left'].setGeometry(0, h - corner_size, corner_size, corner_size)
        self.resize_handles['bottom-right'].setGeometry(w - corner_size, h - corner_size, corner_size, corner_size)

    def eventFilter(self, obj, event):
        """Handle resize handle events"""
        # Check if this is a resize handle
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

                # Handle resizing based on edge type
                if 'left' in self.resize_edge:
                    new_geo.setLeft(self.resize_start_geometry.left() + delta.x())
                if 'right' in self.resize_edge:
                    new_geo.setRight(self.resize_start_geometry.right() + delta.x())
                if 'top' in self.resize_edge:
                    new_geo.setTop(self.resize_start_geometry.top() + delta.y())
                if 'bottom' in self.resize_edge:
                    new_geo.setBottom(self.resize_start_geometry.bottom() + delta.y())

                # Enforce minimum size
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

        # For Puter provider with images, use attachment mode
        if self.controller.get_ai_provider() == 'puter':
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
            if any(file_path.lower().endswith(ext) for ext in valid_extensions):
                self.attached_image = file_path
                self.add_system_message(
                    f"📎 **Image Ready:** {file_path}\n\nType your message and press Enter to send with image.")
                return

        # Otherwise, insert file path into text input
        current_text = self.input_field.toPlainText()
        if current_text:
            self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
        else:
            self.input_field.text_input.setPlainText(file_path)