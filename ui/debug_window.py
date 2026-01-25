"""
Debug Window - Shows AI tool usage conversations
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QPushButton, QLabel, QCheckBox, QFrame)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect
from PyQt6.QtGui import QFont, QRegion
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

        # Window dragging state
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

        # Borderless window with stay on top
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(600, 400)
        self.resize(800, 700)

        # Main container for rounded corners
        self.container = QWidget()
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #1a1a1a;
                border-radius: 12px;
            }
            QWidget {
                color: #00ff00;
            }
        """)
        self.container.setObjectName("container")

        self.init_ui()

        # Wrap everything in container for rounded corners
        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        # Apply rounded mask
        self.apply_rounded_mask()
        self.create_resize_handles()

    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar (draggable)
        header_bar = QFrame()
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border-bottom: 1px solid #00ff00;
            }
        """)

        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        # Title
        title = QLabel("🔧 Debug - Full AI/Tool Exchanges")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #00ff00;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Clear button
        clear_btn = QPushButton("🗑️")
        clear_btn.setFixedSize(32, 32)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                color: #00ff00;
            }
            QPushButton:hover {
                background: #2A2A2A;
            }
        """)
        clear_btn.clicked.connect(self.clear_debug)
        clear_btn.setToolTip("Clear debug log")
        header_layout.addWidget(clear_btn)

        # NEW: CMD toggle button (only if launched from CMD)
        if self.launched_from_cmd:
            self.cmd_toggle_btn = QPushButton("🪟")
            self.cmd_toggle_btn.setFixedSize(32, 32)
            self.cmd_toggle_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    font-size: 16px;
                    color: #00ff00;
                }
                QPushButton:hover {
                    background: #2A2A2A;
                }
            """)
            self.cmd_toggle_btn.clicked.connect(self.toggle_cmd_window)
            self.cmd_toggle_btn.setToolTip("Toggle CMD window")
            header_layout.addWidget(self.cmd_toggle_btn)

        # Minimize button
        minimize_btn = QPushButton("−")
        minimize_btn.setFixedSize(32, 32)
        minimize_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 6px;
                font-size: 18px;
                color: #00ff00;
            }
            QPushButton:hover {
                background: #2A2A2A;
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
                color: #00ff00;
            }
            QPushButton:hover {
                background: #2A2A2A;
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
                color: #00ff00;
            }
            QPushButton:hover {
                background: #EA4335;
                color: white;
            }
        """)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn)

        layout.addWidget(header_bar)

        # Content area
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(15, 15, 15, 15)

        # Info label
        info = QLabel("All raw exchanges between AI and tools are shown here")
        info.setStyleSheet("color: #888; font-size: 10pt; margin: 5px 0;")
        content_layout.addWidget(info)

        # Filter section
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
        content_layout.addLayout(filter_layout)

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
        self.debug_display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        content_layout.addWidget(self.debug_display)

        layout.addWidget(content_widget)

        # Welcome message
        self.add_message("system", "╔══ DEBUG MODE ACTIVE ══╗\nAll AI/Tool exchanges will appear here in full detail")

    def update_filter(self, filter_type, enabled):
        """NEW: Update filter state"""
        self.filters[filter_type] = enabled

    def add_message(self, sender, message):
        """Add debug message with full content"""
        if not self.filters.get(sender, True):
            return

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

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
        self.add_message("system", "╔══ DEBUG LOG CLEARED ══╗")

    def closeEvent(self, event):
        """Handle window close - just hide, don't close app"""
        self.hide()
        event.ignore()

    def toggle_cmd_window(self):
        """Toggle CMD window visibility (Windows only)"""
        if sys.platform != 'win32':
            return

        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()

            if hwnd:
                if self.cmd_visible:
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
                    self.cmd_visible = False
                    self.cmd_toggle_btn.setToolTip("Show CMD window")
                else:
                    ctypes.windll.user32.ShowWindow(hwnd, 5)
                    self.cmd_visible = True
                    self.cmd_toggle_btn.setToolTip("Hide CMD window")
        except Exception as e:
            self.add_message("system", f"Error toggling CMD: {e}")

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