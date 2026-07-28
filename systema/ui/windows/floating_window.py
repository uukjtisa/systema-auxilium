"""
ui/floating_window.py
Floating Window - Main chat interface with configurable appearance
Based on the prototype test_accessing_app.py
"""
import threading

from PyQt6.QtWidgets import QWidget, QPushButton, QVBoxLayout, QMenu, QLabel, QApplication, QMessageBox
from PyQt6.QtGui import QAction, QCursor, QPainter, QColor, QPen, QLinearGradient, QBrush
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QRectF
from systema.ui.windows.floating_window_settings import AppearanceSettingsWindow
from systema.ui.windows.debug_window import DebugWindow
import json
import os
import math
from systema.common.logger import _make_logger, _NoOpLogger
from systema.ui import theme as _theme


_verbose = True
log = _make_logger("FloatingWindow") if _verbose else _NoOpLogger()
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
from systema import APP_ROOT as _APP_ROOT
from systema.common import app_config as _app_config
# ─────────────────────────────────────────────────────────────────────────────

class FloatingWindow(QWidget):
    """Floating AI assistant icon with configurable appearance"""

    # Default settings
    DEFAULT_SETTINGS = {
        # 'app' = the painted Systema Auxilium mark (default), 'emoji', 'letter'.
        # Everything here stays user-configurable in the Appearance window.
        'icon_type': 'app',
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
        self.android_bridge = None  # AndroidBridge instance for phone remote

        # Load settings
        self.settings = self.load_settings()

        # Window settings
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setWindowTitle("Systema Auxilium")
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

        # Crash-watchdog heartbeat (idea #12) — a DEDICATED tiny timer, NOT
        # piggybacked on raise_timer's 50ms z-order churn. Stamps
        # data/logs/crash_dumps/heartbeat.txt from the UI thread; the CrashWatcher
        # daemon thread autopsies + force-restarts if the stamp goes stale
        # (event loop hung or windows silently gone).
        from systema.ui import crash_watcher
        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.timeout.connect(crash_watcher.beat)
        self.heartbeat_timer.start(crash_watcher.HEARTBEAT_INTERVAL_MS)
        crash_watcher.start_watcher()

        # Pre-initialize chat window at startup (warms up the widget)
        QTimer.singleShot(600, self._startup_chat_init)

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
        """Load appearance settings — 'floating_window_config' section of settings.json"""
        try:
            loaded = _app_config.load_section('floating_window_config')
            if loaded:
                # Merge with defaults to handle new settings
                settings = self.DEFAULT_SETTINGS.copy()
                settings.update(loaded)
                return settings
        except Exception as e:
            log.error(f"[FloatingWindow.load_settings] Error loading settings: {e}")
        return self.DEFAULT_SETTINGS.copy()

    def save_settings(self):
        """Save appearance settings to the 'floating_window_config' section"""
        try:
            # Save current position
            self.settings['position'] = (self.x(), self.y())
            _app_config.save_section('floating_window_config', self.settings)
        except Exception as e:
            log.error(f"[FloatingWindow.save_settings] Error saving settings: {e}")

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
        # 'app' mode has no glyph text — paintEvent draws the mark instead.
        self.button.setText('' if self.settings['icon_type'] == 'app'
                            else self.settings['icon_text'])

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

        # ── app mark (default icon) ──────────────────────────────────────────
        # Painted, not blitted, so it stays crisp at any size the user picks.
        if self.settings.get('icon_type') == 'app':
            from systema.ui.widgets.painted_icons import draw_app_mark
            span = float(self.settings.get('icon_font_size', 32)) * 1.55
            span = max(12.0, min(span, min(bg_rect.width(), bg_rect.height()) * 1.4))
            c = bg_rect.center()
            mark = QRectF(c.x() - span / 2, c.y() - span / 2, span, span)
            draw_app_mark(painter, mark, detail=span >= 26)

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

    def _menu_style(self) -> str:
        """Context menu stylesheet, themed to the active app theme."""
        p = _theme.current_palette(self.controller)
        return f"""
            QMenu {{
                background-color: {p['surface']};
                border: 1px solid {p['border']};
                border-radius: 6px;
                padding: 4px 0;
                font-size: 12px;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }}
            QMenu::item {{
                padding: 6px 24px 6px 16px;
                color: {p['text']};
            }}
            QMenu::item:selected {{
                background-color: {p['surface2']};
                color: {_theme.lighten(p['text'], 0.2)};
            }}
            QMenu::item:checked {{
                color: {p['accent']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {p['border']};
                margin: 4px 8px;
            }}
        """

    def show_context_menu(self, pos):
        """Show right-click menu"""
        menu = QMenu()
        menu.setStyleSheet(self._menu_style())

        menu.addAction(QAction("Open Chat", self, triggered=self.open_chat))
        # Android bridge toggle — label changes to show IP:port when active
        _bridge_on = self.android_bridge is not None and self.android_bridge.isVisible()
        if _bridge_on:
            _phone_label = f"Close Packet ({self.android_bridge.get_connection_info()})"
        else:
            _phone_label = "Open Packet"
        menu.addAction(QAction(_phone_label, self, triggered=self.toggle_android_bridge))
        menu.addAction(QAction("Appearance", self, triggered=self.open_appearance_settings))
        menu.addAction(QAction("Settings", self, triggered=self.open_settings))

        # Debug toggle
        debug_action = QAction("Debug Mode", self, checkable=True, triggered=self.toggle_debug)
        debug_action.setChecked(self.controller.get_debug_mode())
        menu.addAction(debug_action)

        # Debug Window button - only shown if debug mode is enabled
        if self.controller.get_debug_mode():
            menu.addAction(QAction("Debug Window", self, triggered=self.open_debug_window))

        menu.addAction(QAction("Reset Python", self, triggered=self.reset_python))
        menu.addAction(QAction("Put in Tray", self, triggered=self.put_to_tray))
        menu.addSeparator()
        menu.addAction(QAction("Restart", self, triggered=self.restart_app))
        menu.addAction(QAction("Shutdown", self, triggered=self.shutdown_app))
        menu.exec(QCursor.pos())

    def toggle_debug(self):
        """Toggle debug mode. The UI side-effects (open/close the debug window,
        repaint, post a chat notice) are applied centrally by the controller via
        apply_debug_mode(), so this path and the Settings-window save path behave
        identically."""
        self.controller.set_debug_mode(not self.controller.get_debug_mode())

    def apply_debug_mode(self, enabled):
        """Apply the debug-mode UI side-effects. Called by
        AssistantController.set_debug_mode() on an actual state change, regardless
        of whether the change originated from this window's context menu, the
        system-tray menu, or the Settings window's Save button."""
        if enabled:
            self.open_debug_window()
            if self.chat_window:
                self.chat_window.add_system_message("Debug mode enabled - Tool conversations visible")
        else:
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

    def show_debug_message(self, sender, message, unfiltered=False):
        """Show debug message (called from controller). unfiltered=True bypasses
        the type-filter checkboxes so the entry is always shown (used for the
        SENT/RECEIVED wire panels)."""
        if self.controller.get_debug_mode():
            if self.debug_window is None:
                self.open_debug_window()
            self.debug_window.add_message(sender, message, force=unfiltered)

    def show_thinking(self):
        """Show thinking in chat window"""
        if self.chat_window:
            self.chat_window.show_thinking()
        if self.android_bridge and self.android_bridge.isVisible():
            self.android_bridge.show_thinking()

    def hide_thinking(self):
        """Hide thinking in chat window"""
        if self.chat_window:
            self.chat_window.hide_thinking()
        if self.android_bridge and self.android_bridge.isVisible():
            self.android_bridge.hide_thinking()

    def set_work_state(self, working: bool):
        """Switch between 'Working...' (code exec) and 'AI is thinking...' state."""
        if self.chat_window:
            placeholder = "Working..." if working else "AI is thinking..."
            self.chat_window.set_input_placeholder(placeholder)
            if working and hasattr(self.chat_window, '_work_banner'):
                self.chat_window._work_banner.setText("Working…")
                self.chat_window._work_banner.show()

    def show_ai_message(self, message):
        """Show AI message in chat window"""
        if self.chat_window:
            self.chat_window.show_ai_message(message)
        if self.android_bridge and self.android_bridge.isVisible():
            self.android_bridge.show_ai_message(message)

    def _startup_chat_init(self):
        """Pre-initialize and briefly show the chat window at startup."""
        if self.chat_window is None:
            from systema.ui.chat_window import ChatWindow
            self.chat_window = ChatWindow(self.controller)
        self.chat_window.show()
        self.chat_window.raise_()
        open_on_startup = self.controller.settings.get('open_chat_on_startup', False)
        if not open_on_startup:
            QTimer.singleShot(500, self.chat_window.hide)

        # Auto-open Android Bridge if setting is enabled
        open_packet_on_startup = self.controller.settings.get('open_packet_on_startup', False)
        if open_packet_on_startup and (self.android_bridge is None or not self.android_bridge.isVisible()):
            from systema.net.android_bridge import AndroidBridge
            if self.android_bridge is None:
                self.android_bridge = AndroidBridge(self.controller)
            self.android_bridge.show()
            log.info("[FloatingWindow] Android Bridge auto-started on startup")

    def open_chat(self):
        """Open chat window"""
        if self.chat_window is None:
            from systema.ui.chat_window import ChatWindow
            self.chat_window = ChatWindow(self.controller)
        if not self.chat_window.isVisible():
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
        else:
            self.chat_window.hide()

    def show_toast(self, message, duration=2000):
        QApplication.setQuitOnLastWindowClosed(False)  # 👈 key fix

        msg = QMessageBox()
        msg.setWindowTitle("Info")
        msg.setText(message)
        msg.setStandardButtons(QMessageBox.StandardButton.Close)
        msg.show()

        self._toast = msg

        QTimer.singleShot(duration, msg.close)

    def open_settings(self):
        """Open settings window"""
        if self.settings_window is None:
            from systema.ui.windows.settings_window import SettingsWindow
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
        from PIL import Image
        import pystray

        # Load the app icon
        icon_path = _APP_ROOT / "assets" / "systema_auxilium.ico"
        try:
            image = Image.open(icon_path).convert("RGBA")
            # pystray works best with 64x64
            image = image.resize((64, 64), Image.LANCZOS)
        except Exception:
            # Fallback: plain circle if icon file is missing
            from PIL import ImageDraw
            image = Image.new('RGBA', (64, 64), color=(0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse([4, 4, 60, 60], fill=(100, 100, 255, 255))
            draw.ellipse([18, 18, 46, 46], fill=(255, 255, 255, 255))

        menu_items = [pystray.MenuItem("Toggle Chat", self._tray_toggle_chat, default=True, visible=False),
                      pystray.MenuItem("Show floating window", self._tray_show_floating),
                      pystray.MenuItem("Open Chat", self._tray_open_chat),
                      pystray.MenuItem("Appearance", self._tray_open_appearance),
                      pystray.MenuItem("Settings", self._tray_open_settings), pystray.MenuItem(
                "Debug Mode",
                self.toggle_debug_from_tray,
                checked=lambda item: self.controller.get_debug_mode()
            ), pystray.MenuItem(
                "Open Debug Window",
                self._tray_open_debug,
                visible=lambda item: self.controller.get_debug_mode()
            )]

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

    def _tray_toggle_chat(self, icon=None, item=None):
        """Left-click on tray icon — toggle chat window on/off."""
        QTimer.singleShot(0, self._do_toggle_chat_from_tray)

    def _do_toggle_chat_from_tray(self):
        if self.chat_window is None:
            from systema.ui.chat_window import ChatWindow
            self.chat_window = ChatWindow(self.controller)
        if self.chat_window.isVisible():
            self.chat_window.hide()
        else:
            self.chat_window.show()
            self.chat_window.raise_()
            self.chat_window.activateWindow()
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
            from systema.ui.chat_window import ChatWindow
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
        """Toggle debug mode from tray icon — marshalled to main thread"""
        QTimer.singleShot(0, self.toggle_debug)
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

    def _confirm_exit_if_busy(self, action):
        """Return True to proceed with Restart/Shutdown.

        If a response is generating, work mode is running, or voice output is
        pending, show a Cancel/Confirm modal first; Confirm gracefully stops all
        activity (controller.graceful_stop_for_exit) before proceeding."""
        try:
            reason = self.controller.exit_busy_reason()
        except Exception:
            reason = None
        if not reason:
            return True
        # Match the dialog to the active chat theme (falls back to defaults).
        _theme = None
        try:
            cw = getattr(self, 'chat_window', None)
            if cw is not None and hasattr(cw, '_t'):
                _theme = cw._t()
        except Exception:
            _theme = None
        from systema.ui.dialogs.exit_confirm_dialog import ExitConfirmDialog
        dlg = ExitConfirmDialog(action, reason, parent=self, theme=_theme)
        if not dlg.exec():
            return False
        try:
            self.controller.graceful_stop_for_exit()
        except Exception:
            pass
        return True

    # ── Exit: policy lives in the controller, mechanism lives here ───────────
    # Both of these are thin. controller.request_exit() is the ONE gate that
    # asks "a response is still generating, continue?" — asking in more than
    # one place is what let a cancelled Restart restart anyway.

    def restart_app(self):
        """Menu/tray Restart. Routes through the single exit pipeline."""
        return self.controller.request_exit("restart")

    def shutdown_app(self):
        """Menu/tray Shutdown. Routes through the single exit pipeline."""
        return self.controller.request_exit("shutdown")

    def perform_teardown(self):
        """MECHANISM ONLY — no prompting, no policy.

        Saves settings, force-closes the child windows that swallow their own
        close events, stops the tray, quits the app. Called by
        controller.request_exit() once the exit has actually been agreed;
        calling it directly skips the busy check, which is exactly the mistake
        this split exists to prevent.
        """
        self.save_settings()
        # Forcibly destroy child windows that use event.ignore() in closeEvent
        for attr in ('chat_window', 'settings_window', 'debug_window', 'appearance_window'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if hasattr(w, 'setAttribute'):
                        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
                    if hasattr(w, 'deleteLater'):
                        w.close()
                        w.deleteLater()
                    else:
                        w.hide()
                except (RuntimeError, Exception):
                    pass
                setattr(self, attr, None)
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def handle_work_update(self, result):
        """Handle tool mode updates"""
        # ── Show code + output block in chat window when code ran ──────────────
        code   = result.get('code', '')
        output = getattr(getattr(self, '_controller_ref', None), '_last_work_output_snapshot', '') \
                 if not code else ''
        # Grab output from the tool_manager directly
        try:
            tm_output = self.controller.ai.tool_manager.work.last_output or ''
        except Exception:
            tm_output = ''

        # Narration FIRST, then the code note — the model says what it's doing
        # before the tool runs, and the merged turn bubble mirrors that order.
        if self.chat_window:
            self.chat_window.handle_ai_response(result)
        if self.android_bridge and self.android_bridge.isVisible():
            self.android_bridge.handle_ai_response(result)

        if code and tm_output:
            if self.chat_window:
                self.chat_window.add_code_execution_note(code, tm_output)
            # NO android_bridge.add_work_execution() here. ToolManager already
            # mirrors the step to the phone at the end of run_python_interpreter
            # (tool_manager.py, "Mirror code + output to Android bridge"), so
            # sending it again here rendered every tool card TWICE on the phone
            # while the desktop showed one. That path is the correct survivor:
            # it carries the step annotation and fires per CALL, so a batch with
            # several interpreter steps reports each one — this path sees only
            # the last combined observation and would under-report the batch.
        # Flush any tool cards spawned from inside that python step (web_search)
        # AFTER the interpreter card, so ordering is python-first then tool card.
        try:
            self.controller.ai.tool_manager.flush_interp_cards()
        except Exception:
            pass

        # ── Clear work banner when work mode finishes ──────────────────────────
        exited   = result.get('finished_working', False)
        thinking = result.get('thinking', False)
        if exited or not thinking:
            if self.chat_window and hasattr(self.chat_window, '_work_banner'):
                self.chat_window._work_banner.setText("")
                self.chat_window._work_banner.hide()
            if self.android_bridge and self.android_bridge.isVisible():
                self.android_bridge.hide_work_banner()

    def toggle_android_bridge(self):
        """Start or stop the Android phone bridge. Called from context menu."""
        if self.android_bridge is not None and self.android_bridge.isVisible():
            self.android_bridge.hide()
            self.android_bridge = None
        else:
            from systema.net.android_bridge import AndroidBridge
            if self.android_bridge is None:
                self.android_bridge = AndroidBridge(self.controller)
            self.android_bridge.show()
            self.show_toast(f"Packet opened - {self.android_bridge.get_connection_info()}", 1500)