"""
Debug Window - Shows AI tool usage conversations
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QPushButton, QLabel, QCheckBox)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QFont
from datetime import datetime
import sys
import ctypes

class DebugWindow(QWidget):
    """Debug window showing tool conversations"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        # NEW: Track if launched from CMD
        self.launched_from_cmd = hasattr(sys, 'frozen') or sys.stdout is not None
        self.cmd_visible = False  # Will be set during startup detection

        # Filter state - NEW: Controls what messages are shown
        self.filters = {
            'user': True,
            'ai': True,
            'tool': True,
            'system': True
        }


        # Normal window with frame, resizable, stays on top
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumSize(600, 400)
        self.resize(800, 700)  # Default size
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                color: #00ff00;
            }
        """)

        self.init_ui()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)

        # Header
        header = QHBoxLayout()
        title = QLabel("🔧 Debug - Full AI/Tool Exchanges")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #00ff00;")
        header.addWidget(title)

        header.addStretch()

        # Clear button
        clear_btn = QPushButton("🗑️ Clear")
        clear_btn.setMaximumWidth(70)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #333;
                border: none;
                border-radius: 3px;
                padding: 5px;
                color: #00ff00;
            }
            QPushButton:hover {
                background: #444;
            }
        """)
        clear_btn.clicked.connect(self.clear_debug)
        header.addWidget(clear_btn)

        # NEW: CMD toggle button (only if launched from CMD)
        if self.launched_from_cmd:
            self.cmd_toggle_btn = QPushButton("🪟 Show CMD")
            self.cmd_toggle_btn.setMaximumWidth(100)
            self.cmd_toggle_btn.setStyleSheet("""
                QPushButton {
                    background: #3a5a3a;
                    border: none;
                    border-radius: 3px;
                    padding: 5px;
                    color: #00ff00;
                }
                QPushButton:hover {
                    background: #4a6a4a;
                }
            """)
            self.cmd_toggle_btn.clicked.connect(self.toggle_cmd_window)
            header.addWidget(self.cmd_toggle_btn)

        layout.addLayout(header)

        # Info label
        info = QLabel("All raw exchanges between AI and tools are shown here")
        info.setStyleSheet("color: #888; font-size: 10pt; margin: 5px 0;")
        layout.addWidget(info)

        # NEW: Filter section
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filters:")
        filter_label.setStyleSheet("color: #00ff00; font-weight: bold;")
        filter_layout.addWidget(filter_label)

        # User checkbox
        self.user_checkbox = QCheckBox("👤 User")
        self.user_checkbox.setChecked(True)
        self.user_checkbox.setStyleSheet("color: #ff00ff;")
        self.user_checkbox.stateChanged.connect(lambda: self.update_filter('user', self.user_checkbox.isChecked()))
        filter_layout.addWidget(self.user_checkbox)

        # AI checkbox
        self.ai_checkbox = QCheckBox("🤖 AI")
        self.ai_checkbox.setChecked(True)
        self.ai_checkbox.setStyleSheet("color: #00ffff;")
        self.ai_checkbox.stateChanged.connect(lambda: self.update_filter('ai', self.ai_checkbox.isChecked()))
        filter_layout.addWidget(self.ai_checkbox)

        # Tool checkbox
        self.tool_checkbox = QCheckBox("🔧 Tool")
        self.tool_checkbox.setChecked(True)
        self.tool_checkbox.setStyleSheet("color: #ffff00;")
        self.tool_checkbox.stateChanged.connect(lambda: self.update_filter('tool', self.tool_checkbox.isChecked()))
        filter_layout.addWidget(self.tool_checkbox)

        # System checkbox
        self.system_checkbox = QCheckBox("⚙️ System")
        self.system_checkbox.setChecked(True)
        self.system_checkbox.setStyleSheet("color: #888888;")
        self.system_checkbox.stateChanged.connect(lambda: self.update_filter('system', self.system_checkbox.isChecked()))
        filter_layout.addWidget(self.system_checkbox)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # Debug display
        self.debug_display = QTextEdit()
        self.debug_display.setReadOnly(True)
        self.debug_display.setStyleSheet("""
            QTextEdit {
                background-color: #0a0a0a;
                border: 1px solid #00ff00;
                border-radius: 5px;
                padding: 10px;
                font-family: 'Courier New', monospace;
                font-size: 10px;
                color: #00ff00;
                line-height: 1.4;
            }
        """)

        # Enable word wrap for better readability
        self.debug_display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        layout.addWidget(self.debug_display)

        self.setLayout(layout)

        # Welcome message
        self.add_message("system", "═══ DEBUG MODE ACTIVE ═══\nAll AI/Tool exchanges will appear here in full detail")

    def update_filter(self, filter_type, enabled):
        """NEW: Update filter state"""
        self.filters[filter_type] = enabled

    def add_message(self, sender, message):
        """Add debug message with full content"""
        # NEW: Check if this message type should be shown
        if not self.filters.get(sender, True):
            return

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Include milliseconds

        # Color coding
        colors = {
            'system': '#888888',
            'ai': '#00ffff',
            'tool': '#ffff00',
            'user': '#ff00ff'
        }

        icons = {
            'system': '⚙️',
            'ai': '🤖',
            'tool': '🔧',
            'user': '👤'
        }

        color = colors.get(sender, '#00ff00')
        icon = icons.get(sender, '•')

        # Escape HTML in message content
        message_escaped = message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        html = f'''
        <div style="margin: 10px 0; padding: 10px; background: rgba(0,255,0,0.05); border-left: 4px solid {color}; border-radius: 3px;">
            <div style="color: {color}; font-weight: bold; margin-bottom: 8px;">
                {icon} {sender.upper()} <span style="color: #555; font-size: 8pt; font-weight: normal;">[{timestamp}]</span>
            </div>
            <div style="color: #00ff00; white-space: pre-wrap; font-size: 9pt; font-family: 'Courier New', monospace;">
{message_escaped}
            </div>
        </div>
        '''
        self.debug_display.append(html)
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        """Scroll to bottom"""
        scrollbar = self.debug_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_debug(self):
        """Clear debug display"""
        self.debug_display.clear()
        self.add_message("system", "═══ DEBUG LOG CLEARED ═══")

    def closeEvent(self, event):
        """Handle window close - just hide, don't close app"""
        self.hide()
        event.ignore()  # Prevent the window from actually closing

    def toggle_cmd_window(self):
        """Toggle CMD window visibility (Windows only)"""
        if sys.platform != 'win32':
            return

        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()

            if hwnd:
                if self.cmd_visible:
                    # Hide CMD
                    ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                    self.cmd_visible = False
                    self.cmd_toggle_btn.setText("🪟 Show CMD")
                else:
                    # Show CMD
                    ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
                    self.cmd_visible = True
                    self.cmd_toggle_btn.setText("🪟 Hide CMD")
        except Exception as e:
            self.add_message("system", f"Error toggling CMD: {e}")