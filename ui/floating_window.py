"""
Floating Window - Main chat interface
Based on the prototype test_accessing_app.py
"""

from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QMenu
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtCore import Qt, QTimer, QPoint
from ui.chat_window import ChatWindow
from ui.settings_window import SettingsWindow
import pystray
from ui.debug_window import DebugWindow
from PIL import Image, ImageDraw


class FloatingWindow(QWidget):
    """Floating AI assistant icon"""
    
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.chat_window = None
        self.settings_window = None
        self.tray_icon = None
        self.debug_window = None
        
        # Window settings
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(50, 50)
        
        # Icon button
        self.button = QPushButton("🤖", self)
        self.button.setStyleSheet("""
            QPushButton {
                font-size: 32px;
                border: none;
                background-color: rgba(100, 100, 255, 200);
                border-radius: 25px;
            }
            QPushButton:hover {
                background-color: rgba(120, 120, 255, 220);
            }
        """)
        self.button.setFixedSize(50, 50)
        self.button.clicked.connect(self.open_chat)
        self.button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.button.customContextMenuRequested.connect(self.show_context_menu)
        
        # Dragging
        self._is_dragging = False
        self._drag_offset = QPoint()
        self.button.installEventFilter(self)
        
        # Center on screen
        QTimer.singleShot(100, self.move_to_center)
    
    def move_to_center(self):
        """Move window to center of screen"""
        from PyQt6.QtWidgets import QApplication
        screen_geometry = QApplication.primaryScreen().geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)
    
    def eventFilter(self, source, event):
        """Handle dragging"""
        if source == self.button:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._is_dragging = True
                self._drag_offset = event.pos()
            elif event.type() == event.Type.MouseMove and self._is_dragging:
                self.move(self.mapToGlobal(event.pos() - self._drag_offset))
            elif event.type() == event.Type.MouseButtonRelease:
                self._is_dragging = False
        return super().eventFilter(source, event)

    def show_context_menu(self, pos):
        """Show right-click menu"""
        menu = QMenu()
        menu.addAction(QAction("💬 Open Chat", self, triggered=self.open_chat))
        menu.addAction(QAction("⚙️ Settings", self, triggered=self.open_settings))

        # Debug toggle
        debug_action = QAction("🔧 Debug Mode", self, checkable=True, triggered=self.toggle_debug)
        debug_action.setChecked(self.controller.get_debug_mode())
        menu.addAction(debug_action)

        # "Open Debug Window" button - only shown if debug mode is enabled
        if self.controller.get_debug_mode():
            menu.addAction(QAction("🪟 Open Debug Window", self, triggered=self.open_debug_window))

        menu.addAction(QAction("🔄 Reset Python", self, triggered=self.reset_python))
        menu.addAction(QAction("📥 Put in Tray", self, triggered=self.put_to_tray))
        menu.addAction(QAction("🔴 Shutdown", self, triggered=self.shutdown_app))
        menu.exec(QCursor.pos())

    def toggle_debug(self):
        """Toggle debug mode"""
        current = self.controller.get_debug_mode()
        self.controller.set_debug_mode(not current)

        if not current:  # Now enabled
            self.open_debug_window()
            if self.chat_window:
                self.chat_window.add_system_message("🔧 Debug mode enabled - Tool conversations visible")
        else:  # Now disabled
            if self.debug_window:
                self.debug_window.hide()
            if self.chat_window:
                self.chat_window.add_system_message("Debug mode disabled")

    def open_debug_window(self):
        """Open debug window"""
        if self.debug_window is None:
            self.debug_window = DebugWindow(self.controller)
        self.debug_window.show()
        self.debug_window.raise_()
        self.debug_window.activateWindow()

    def show_debug_message(self, sender, message):
        """Show debug message (called from controller)"""
        if self.controller.get_debug_mode():
            if self.debug_window is None:
                self.open_debug_window()
            self.debug_window.add_message(sender, message)

    def show_thinking(self):
        """Show thinking in chat window"""
        if self.chat_window:
            self.chat_window.show_thinking()

    def hide_thinking(self):
        """Hide thinking in chat window"""
        if self.chat_window:
            self.chat_window.hide_thinking()

    def show_ai_message(self, message):
        """Show AI message in chat window"""
        if self.chat_window:
            self.chat_window.show_ai_message(message)

    def open_chat(self):
        """Open chat window"""
        if self.chat_window is None:
            self.chat_window = ChatWindow(self.controller)
        if not self.chat_window.isVisible():
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
        else:
            self.chat_window.hide()

    def open_settings(self):
        """Open settings window"""
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.controller)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def reset_python(self):
        """Reset Python interpreter"""
        self.controller.reset_python_interpreter()
        if self.chat_window:
            self.chat_window.add_system_message("Python interpreter reset")

    def create_tray_icon(self):
        """Create system tray icon"""
        # Create a simple icon image
        image = Image.new('RGB', (64, 64), color=(100, 100, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse([8, 8, 56, 56], fill='white')

        # Create tray icon menu with all options
        menu_items = [
            pystray.MenuItem("Show as Floating Window", self.show_from_tray),
            pystray.MenuItem("Open Chat", self.open_chat),
            pystray.MenuItem("Settings", self.open_settings),
        ]

        # Add Debug Mode toggle
        def get_debug_state(icon):
            return self.controller.get_debug_mode()

        menu_items.append(
            pystray.MenuItem(
                "Debug Mode",
                self.toggle_debug_from_tray,
                checked=lambda item: self.controller.get_debug_mode()
            )
        )

        # Add "Open Debug Window" if debug mode is enabled
        def should_show_debug_window(item):
            return self.controller.get_debug_mode()

        menu_items.append(
            pystray.MenuItem(
                "Open Debug Window",
                self.open_debug_window,
                visible=should_show_debug_window
            )
        )

        # Add remaining options
        menu_items.extend([
            pystray.MenuItem("Reset Python", self.reset_python),
            pystray.MenuItem("Shutdown", self.shutdown_app)
        ])

        menu = pystray.Menu(*menu_items)

        self.tray_icon = pystray.Icon("System AI Assistant", image, "System AI Assistant", menu)

    def toggle_debug_from_tray(self, icon, item):
        """Toggle debug mode from tray icon"""
        self.toggle_debug()
    
    def put_to_tray(self):
        """Hide floating icon and show in system tray"""
        self.hide()
        if self.tray_icon is None:
            self.create_tray_icon()
        
        # Run tray icon in a separate thread
        import threading
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()
    
    def show_from_tray(self, icon=None, item=None):
        """Show floating icon and hide from tray"""
        self.show()
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
    
    def shutdown_app(self, icon=None, item=None):
        """Properly shutdown the application"""
        if self.tray_icon:
            self.tray_icon.stop()
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()
    
    def handle_tool_mode_update(self, result):
        """Handle tool mode updates"""
        if self.chat_window:
            self.chat_window.handle_ai_response(result)