"""
Floating Window - Main chat interface with configurable appearance
Based on the prototype test_accessing_app.py
"""

from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QMenu
from PyQt6.QtGui import QAction, QCursor, QPainter, QColor, QPen, QLinearGradient, QBrush
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QRectF
from ui.chat_window import ChatWindow
from ui.settings_window import SettingsWindow
from ui.floating_window_settings import AppearanceSettingsWindow
import pystray
from ui.debug_window import DebugWindow
from PIL import Image, ImageDraw
import json
import os
import math
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
_APP_ROOT = Path(__file__).resolve().parent.parent
# ─────────────────────────────────────────────────────────────────────────────

class FloatingWindow(QWidget):
    """Floating AI assistant icon with configurable appearance"""

    # Default settings
    DEFAULT_SETTINGS = {
        'icon_type': 'emoji',  # 'emoji' or 'letter'
        'icon_text': '🤖',
        'font_family': 'Segoe UI Emoji',  # Default font for icon
        'background_color': (100, 100, 255, 200),
        'is_transparent': False,
        'letter_color': (255, 255, 255),
        'size': 50,                          # legacy – kept for compat, not used for geometry anymore
        'hitbox_mode': 'follow',             # 'follow' or 'custom'
        'hitbox_size': 50,                   # legacy single-value – kept for compat
        'position': None,                    # (x, y) or None for center

        # ── NEW: independent hitbox W / H / offset ──
        'hitbox_width': 50,
        'hitbox_height': 50,
        'hitbox_offset_x': 0,               # pixels from centre
        'hitbox_offset_y': 0,

        # ── NEW: independent background (visual widget) size ──
        'bg_width': 50,
        'bg_height': 50,

        # ── NEW: icon font-size (the emoji/letter glyph itself) ──
        'icon_font_size': 32,

        # ── NEW: shape  'circle' | 'box' | 'star' ──
        'shape': 'circle',

        # ── NEW: gradient  ──
        'use_gradient': False,
        'gradient_color2': (255, 100, 100, 200),   # second colour (RGBA)
        'gradient_direction': 0,                    # degrees: 0=top→bottom, 90=left→right, etc.
    }

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.chat_window = None
        self.settings_window = None
        self.appearance_window = None
        self.tray_icon = None
        self.debug_window = None

        # Load settings
        self.settings = self.load_settings()

        # Window settings
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Set size from bg dimensions (the visual footprint that the widget occupies)
        bg_w = self.settings.get('bg_width', self.settings['size'])
        bg_h = self.settings.get('bg_height', self.settings['size'])
        # The widget must be large enough to contain both the bg shape AND the hitbox (which can extend beyond)
        self._recalc_widget_size()

        # Icon button – no fixed size; we size it to the widget
        self.button = QPushButton(self.settings['icon_text'], self)
        self.update_button_style()
        self.button.setFixedSize(self.width(), self.height())
        self.button.clicked.connect(self.on_button_click)
        self.button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.button.customContextMenuRequested.connect(self.show_context_menu)

        # Dragging - with click detection
        self._is_dragging = False
        self._drag_started = False
        self._drag_offset = QPoint()
        self._click_start_pos = QPoint()
        self._drag_threshold = 5  # pixels to move before considering it a drag
        self.button.installEventFilter(self)

        # Position window
        if self.settings['position']:
            self.move(*self.settings['position'])
        else:
            QTimer.singleShot(100, self.move_to_center)

        # Timer to keep window on top of children
        self.raise_timer = QTimer(self)
        self.raise_timer.timeout.connect(self.ensure_on_top)
        self.raise_timer.start(50)  # Check every 50ms

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_bg_rect(self):
        """QRectF of the background shape, centred in the widget."""
        bg_w = self.settings.get('bg_width', self.settings['size'])
        bg_h = self.settings.get('bg_height', self.settings['size'])
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        return QRectF(cx - bg_w / 2.0, cy - bg_h / 2.0, float(bg_w), float(bg_h))

    def _get_hitbox_rect(self):
        """QRect of the hitbox, centred + offset in the widget."""
        if self.settings['hitbox_mode'] == 'follow':
            # follow = same rect as bg
            bg_w = self.settings.get('bg_width', self.settings['size'])
            bg_h = self.settings.get('bg_height', self.settings['size'])
            hw, hh = bg_w, bg_h
            ox, oy = 0, 0
        else:
            hw = self.settings.get('hitbox_width', self.settings.get('hitbox_size', 50))
            hh = self.settings.get('hitbox_height', self.settings.get('hitbox_size', 50))
            ox = self.settings.get('hitbox_offset_x', 0)
            oy = self.settings.get('hitbox_offset_y', 0)
        cx = self.width() // 2
        cy = self.height() // 2
        return QRect(cx - hw // 2 + ox, cy - hh // 2 + oy, hw, hh)

    def _recalc_widget_size(self):
        """Set widget size to the bounding box that contains bg AND hitbox."""
        bg_w = self.settings.get('bg_width', self.settings['size'])
        bg_h = self.settings.get('bg_height', self.settings['size'])

        if self.settings['hitbox_mode'] == 'custom':
            hw = self.settings.get('hitbox_width', bg_w)
            hh = self.settings.get('hitbox_height', bg_h)
            ox = self.settings.get('hitbox_offset_x', 0)
            oy = self.settings.get('hitbox_offset_y', 0)
        else:
            hw, hh, ox, oy = bg_w, bg_h, 0, 0

        # Each dimension: max of bg and (hitbox shifted by offset)
        # bg is centred at 0; hitbox centre is at (ox, oy)
        # Half-extents from centre:
        bg_left = bg_w / 2.0
        bg_right = bg_w / 2.0
        bg_top = bg_h / 2.0
        bg_bottom = bg_h / 2.0

        hb_left = hw / 2.0 - ox
        hb_right = hw / 2.0 + ox
        hb_top = hh / 2.0 - oy
        hb_bottom = hh / 2.0 + oy

        total_w = int(max(bg_left, hb_left) + max(bg_right, hb_right))
        total_h = int(max(bg_top, hb_top) + max(bg_bottom, hb_bottom))

        # Minimum 1
        total_w = max(total_w, 1)
        total_h = max(total_h, 1)

        self.setFixedSize(total_w, total_h)

    # ── persistence ──────────────────────────────────────────────────────

    def load_settings(self):
        """Load appearance settings from file"""
        settings_file = _APP_ROOT / "floating_window_config.json"
        try:
            if os.path.exists(settings_file):
                with open(settings_file, 'r') as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle new settings
                    settings = self.DEFAULT_SETTINGS.copy()
                    settings.update(loaded)
                    return settings
        except Exception as e:
            print(f"Error loading settings: {e}")
        return self.DEFAULT_SETTINGS.copy()

    def save_settings(self):
        """Save appearance settings to file"""
        settings_file = _APP_ROOT / "floating_window_config.json"
        try:
            # Save current position
            self.settings['position'] = (self.x(), self.y())

            with open(settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")

    # ── appearance ───────────────────────────────────────────────────────

    def update_button_style(self):
        """Update button appearance based on settings.
        The button is now fully transparent; all visual drawing happens in paintEvent."""
        # We still set the font for the text so QPushButton renders it,
        # but the background is always transparent here – paintEvent draws the shape.
        font_family = self.settings.get('font_family', 'Segoe UI Emoji')
        icon_font_size = self.settings.get('icon_font_size', 32)

        if self.settings['icon_type'] == 'letter':
            letter_color = self.settings['letter_color']
            color_style = f'color: rgb({letter_color[0]}, {letter_color[1]}, {letter_color[2]});'
        else:
            color_style = ''

        self.button.setStyleSheet(f"""
            QPushButton {{
                font-size: {icon_font_size}px;
                font-family: '{font_family}';
                border: none;
                background-color: transparent;
                {color_style}
                /* prevent Qt from clipping text */
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: transparent;
            }}
        """)

    # ── star path helper ─────────────────────────────────────────────────

    @staticmethod
    def _star_polygon(rect: QRectF, points=5):
        """Return a list of QPointF forming a star inscribed in rect."""
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF
        cx = rect.center().x()
        cy = rect.center().y()
        outer_r = min(rect.width(), rect.height()) / 2.0
        inner_r = outer_r * 0.382  # classic 5-point star ratio
        polygon = QPolygonF()
        for i in range(points * 2):
            angle_deg = -90 + i * (180.0 / points)
            angle_rad = math.radians(angle_deg)
            r = outer_r if i % 2 == 0 else inner_r
            polygon.append(QPointF(cx + r * math.cos(angle_rad),
                                   cy + r * math.sin(angle_rad)))
        return polygon

    # ── painting ─────────────────────────────────────────────────────────

    def paintEvent(self, event):
        """Draw the background shape + hitbox debug overlay."""
        from PyQt6.QtGui import QPainterPath

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_rect = self._get_bg_rect()
        shape = self.settings.get('shape', 'circle')

        # ── build brush ──
        if self.settings.get('is_transparent', False):
            brush = QBrush(Qt.BrushStyle.NoBrush)
        elif self.settings.get('use_gradient', False):
            c1 = self.settings['background_color']
            c2 = self.settings.get('gradient_color2', (255, 100, 100, 200))
            deg = self.settings.get('gradient_direction', 0)
            rad = math.radians(deg)
            # Map direction onto the rect
            cx, cy = bg_rect.center().x(), bg_rect.center().y()
            hw, hh = bg_rect.width() / 2.0, bg_rect.height() / 2.0
            grad = QLinearGradient(
                cx - hw * math.sin(rad), cy - hh * math.cos(rad),
                cx + hw * math.sin(rad), cy + hh * math.cos(rad)
            )
            grad.setColorAt(0.0, QColor(c1[0], c1[1], c1[2], c1[3]))
            grad.setColorAt(1.0, QColor(c2[0], c2[1], c2[2], c2[3] if len(c2) > 3 else 200))
            brush = QBrush(grad)
        else:
            bg_color = self.settings['background_color']
            brush = QBrush(QColor(bg_color[0], bg_color[1], bg_color[2], bg_color[3]))

        painter.setBrush(brush)
        painter.setPen(QPen(Qt.PenStyle.NoPen))

        # ── draw shape ──
        if shape == 'circle':
            painter.drawEllipse(bg_rect)
        elif shape == 'box':
            painter.drawRect(bg_rect)
        elif shape == 'star':
            poly = self._star_polygon(bg_rect)
            painter.drawPolygon(poly)

        # ── hover highlight (subtle white overlay on hover) ──
        # We rely on mouseMoveEvent + a flag if needed; skip for simplicity,
        # the original hover was done via stylesheet which no longer applies to bg.
        # A minimal implementation: nothing extra needed, keeps it simple.

        # ── debug hitbox overlay ──
        if self.controller.get_debug_mode() and self.settings['hitbox_mode'] == 'custom':
            hitbox_rect = self._get_hitbox_rect()

            # Grid
            pen = QPen(QColor(255, 0, 0, 100))
            pen.setWidth(1)
            painter.setPen(pen)
            for i in range(0, self.width(), 10):
                painter.drawLine(i, 0, i, self.height())
            for i in range(0, self.height(), 10):
                painter.drawLine(0, i, self.width(), i)

            # Hitbox boundary
            pen = QPen(QColor(255, 0, 0, 200))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor(255, 0, 0, 40)))
            painter.drawRect(hitbox_rect)

        painter.end()

    # ── apply ────────────────────────────────────────────────────────────

    def apply_settings(self, new_settings):
        """Apply new appearance settings"""
        self.settings.update(new_settings)

        # Recalculate widget geometry
        self._recalc_widget_size()

        # Update button size to fill widget
        self.button.setFixedSize(self.width(), self.height())

        # Update icon text (no length limit)
        self.button.setText(self.settings['icon_text'])

        # Update style
        self.update_button_style()

        # Save settings
        self.save_settings()

        # Trigger repaint
        self.update()

    # ── hitbox test ──────────────────────────────────────────────────────

    def ensure_on_top(self):
        """Ensure floating window is always on top of child windows"""
        if self.isVisible():
            # Check if any child window is active
            child_active = False
            if self.chat_window and self.chat_window.isVisible():
                child_active = True
            if self.settings_window and self.settings_window.isVisible():
                child_active = True
            if self.debug_window and self.debug_window.isVisible():
                child_active = True
            if self.appearance_window and self.appearance_window.isVisible():
                child_active = True

            # If any child is visible, raise this window
            if child_active:
                self.raise_()

    def move_to_center(self):
        """Move window to center of screen"""
        from PyQt6.QtWidgets import QApplication
        screen_geometry = QApplication.primaryScreen().geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

    def is_point_in_hitbox(self, pos):
        """Check if a point is within the custom hitbox"""
        if self.settings['hitbox_mode'] == 'follow':
            return True  # Always clickable when following size
        return self._get_hitbox_rect().contains(pos)

    # ── event filter (drag / click) ──────────────────────────────────────

    def eventFilter(self, source, event):
        """Handle dragging with click detection"""
        if source == self.button:
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                # Check if click is within hitbox
                if self.is_point_in_hitbox(event.pos()):
                    self._drag_started = False
                    self._is_dragging = False
                    self._drag_offset = event.pos()
                    self._click_start_pos = self.mapToGlobal(event.pos())
                    return False
                else:
                    # Outside hitbox, ignore click
                    return True

            elif event.type() == event.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
                current_pos = self.mapToGlobal(event.pos())
                distance = (current_pos - self._click_start_pos).manhattanLength()

                if distance > self._drag_threshold:
                    self._drag_started = True
                    self._is_dragging = True

                if self._is_dragging:
                    self.move(self.mapToGlobal(event.pos() - self._drag_offset))
                    return True

            elif event.type() == event.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                was_dragging = self._drag_started
                self._is_dragging = False
                self._drag_started = False

                # Save position after drag
                if was_dragging:
                    self.save_settings()
                    return True  # Consume event, don't trigger click

        return super().eventFilter(source, event)

    def on_button_click(self):
        """Handle button click (only triggers if not dragging)"""
        if not self._drag_started:
            self.open_chat()

    def show_context_menu(self, pos):
        """Show right-click menu"""
        menu = QMenu()
        menu.addAction(QAction("💬 Open Chat", self, triggered=self.open_chat))
        menu.addAction(QAction("🎨 Appearance", self, triggered=self.open_appearance_settings))
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
            self.update()
        else:  # Now disabled
            if self.debug_window:
                self.debug_window.hide()
            if self.chat_window:
                self.chat_window.add_system_message("Debug mode disabled")
            self.update()

    def open_appearance_settings(self):
        """Open appearance settings window"""
        if self.appearance_window is None:
            self.appearance_window = AppearanceSettingsWindow(self)
        self.appearance_window.show()
        self.appearance_window.raise_()
        self.appearance_window.activateWindow()

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
        """Create system tray icon with RGBA image (required on Windows)."""
        # RGBA required — RGB silently fails to show on Windows tray
        image = Image.new('RGBA', (64, 64), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([4, 4, 60, 60], fill=(100, 100, 255, 255))
        draw.ellipse([18, 18, 46, 46], fill=(255, 255, 255, 255))

        menu_items = [
            pystray.MenuItem("Show floating window", self._tray_show_floating),
            pystray.MenuItem("Open Chat",            self._tray_open_chat),
            pystray.MenuItem("Appearance",           self._tray_open_appearance),
            pystray.MenuItem("Settings",             self._tray_open_settings),
        ]

        menu_items.append(
            pystray.MenuItem(
                "Debug Mode",
                self.toggle_debug_from_tray,
                checked=lambda item: self.controller.get_debug_mode()
            )
        )

        menu_items.append(
            pystray.MenuItem(
                "Open Debug Window",
                self._tray_open_debug,
                visible=lambda item: self.controller.get_debug_mode()
            )
        )

        menu_items.extend([
            pystray.MenuItem("Reset Python", self._tray_reset_python),
            pystray.MenuItem("Shutdown",     self._tray_shutdown),
        ])

        menu = pystray.Menu(*menu_items)
        self.tray_icon = pystray.Icon(
            "Systema Auxilium", image, "Systema Auxilium", menu
        )

    # ── Thread-safe tray callbacks ─────────────────────────────────────────────
    # pystray calls these from its own thread; Qt UI work must be marshalled
    # back to the main thread via QTimer.singleShot(0, fn).

    def _tray_show_floating(self, icon=None, item=None):
        QTimer.singleShot(0, self._do_show_from_tray)

    def _do_show_from_tray(self):
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_open_chat(self, icon=None, item=None):
        QTimer.singleShot(0, self._do_open_chat_from_tray)

    def _do_open_chat_from_tray(self):
        if self.chat_window is None:
            self.chat_window = ChatWindow(self.controller)
        self.chat_window.show()
        self.chat_window.raise_()
        self.chat_window.activateWindow()

    def _tray_open_settings(self, icon=None, item=None):
        QTimer.singleShot(0, self.open_settings)

    def _tray_open_appearance(self, icon=None, item=None):
        QTimer.singleShot(0, self.open_appearance_settings)

    def _tray_open_debug(self, icon=None, item=None):
        QTimer.singleShot(0, self.open_debug_window)

    def _tray_reset_python(self, icon=None, item=None):
        QTimer.singleShot(0, self.reset_python)

    def _tray_shutdown(self, icon=None, item=None):
        QTimer.singleShot(0, self.shutdown_app)

    def toggle_debug_from_tray(self, icon, item):
        """Toggle debug mode from tray icon"""
        self.toggle_debug()

    def put_to_tray(self):
        """Hide floating icon and show in system tray."""
        if self.tray_icon is None:
            self.create_tray_icon()
        self.hide()
        import threading
        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()

    def show_from_tray(self, icon=None, item=None):
        """Legacy — prefer _tray_show_floating for tray callbacks."""
        QTimer.singleShot(0, self._do_show_from_tray)

    def shutdown_app(self):
        """Properly shutdown the application — close all child windows first."""
        self.save_settings()
        # Forcibly destroy child windows that use event.ignore() in closeEvent
        for attr in ('chat_window', 'settings_window', 'debug_window', 'appearance_window'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
                    w.close()
                    w.deleteLater()
                except RuntimeError:
                    pass
                setattr(self, attr, None)
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def handle_work_mode_update(self, result):
        """Handle tool mode updates"""
        if self.chat_window:
            self.chat_window.handle_ai_response(result)