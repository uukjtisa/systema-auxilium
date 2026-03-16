"""
Debug Window - Shows AI tool usage conversations
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
                             QTextEdit, QPushButton, QLabel, QCheckBox, QFrame)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect
from PyQt6.QtGui import QFont, QRegion
from datetime import datetime
from core.hot_reload import reload_module
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

        # Hot Reload panel
        self._build_hot_reload_panel(layout)

        # Welcome message
        self.add_message("system", "╔══ DEBUG MODE ACTIVE ══╗\nAll AI/Tool exchanges will appear here in full detail")

    # ── HOT RELOAD ────────────────────────────────────────────────────────────

    def _reload_registry(self):
        """
        Registry of hot-reloadable modules.
        To add a new file: append one tuple here.
          (display_label, module_path, post_hook_method_name)

        post_hook is called after a successful reload and receives (self, controller).
        Use '_post_noop' if nothing special is needed after reload.
        Modules marked '⚠ restart' are listed but disabled — too risky to live-reload.
        """
        return [
            ("global_instructions.py", "core.global_instructions",      "_post_global_instructions"),
            ("chat_window.py",         "ui.chat_window",                 "_post_chat_window"),
            ("settings_window.py",     "ui.settings_window",             "_post_settings_window"),
            ("debug_window.py",        "ui.debug_window",                "_post_debug_window"),
            ("tool_manager.py",        "core.tool_manager",              "_post_noop"),
            ("memory_manager.py",      "core.memory_manager",            "_post_noop"),
            ("skill_manager.py",       "core.skill_manager",             "_post_noop"),
            ("ai_engine.py",           "core.ai_engine",                 None),   # None = disabled
            ("controller.py",          "core.controller",                None),   # None = disabled
        ]

    # ── Post-hooks ────────────────────────────────────────────────────────────

    def _post_noop(self, controller):
        """No special wiring needed — reload was enough."""
        pass

    def _post_global_instructions(self, controller):
        """
        Patch ai_engine's module-level names with the freshly reloaded versions,
        then rebuild the live system prompt via the existing controller path.
        """
        try:
            import core.global_instructions as gi
            import core.ai_engine as ae
            # Patch the names that ai_engine captured with `from X import Y`
            ae.get_system_prompt          = gi.get_system_prompt
            ae.get_gemini_system_prompt   = gi.get_gemini_system_prompt
            ae.POST_EXIT_PROMPT           = gi.POST_EXIT_PROMPT
            ae.POST_EXIT_PROMPT_VOICE     = gi.POST_EXIT_PROMPT_VOICE
            # Rebuild the live prompt
            controller._update_system_prompt()
        except Exception as e:
            self.add_message("system", f"[hot_reload] global_instructions post-hook error: {e}")

    def _post_chat_window(self, controller):
        """
        After reload:
        1. Patch floating_window's stale `ChatWindow` name so future open_chat()
           uses the new class.
        2. If a window was live, destroy it and re-open with the new class.
        """
        import ui.chat_window as cw_mod
        import ui.floating_window as fw_mod

        # Patch the name captured by `from ui.chat_window import ChatWindow`
        fw_mod.ChatWindow = cw_mod.ChatWindow

        ui = controller.ui
        was_visible = False
        if hasattr(ui, 'chat_window') and ui.chat_window is not None:
            try:
                was_visible = ui.chat_window.isVisible()
                ui.chat_window.hide()
                ui.chat_window.deleteLater()
            except RuntimeError:
                pass
            ui.chat_window = None

        if was_visible:
            def _reopen():
                new_win = cw_mod.ChatWindow(controller)
                ui.chat_window = new_win
                new_win.show()
                new_win.raise_()
                new_win.activateWindow()
                # Restore theme
                if hasattr(new_win, 'apply_theme'):
                    theme = controller.settings.get('chat_theme', 'obsidian_blue')
                    new_win.apply_theme(theme)
                    new_win.apply_initial_settings()
            QTimer.singleShot(100, _reopen)

    def _post_settings_window(self, controller):
        """Patch floating_window's stale SettingsWindow ref, drop cached instance."""
        import ui.settings_window as sw_mod
        import ui.floating_window as fw_mod
        fw_mod.SettingsWindow = sw_mod.SettingsWindow

        ui = controller.ui
        if hasattr(ui, 'settings_window') and ui.settings_window is not None:
            try:
                ui.settings_window.hide()
                ui.settings_window.deleteLater()
            except RuntimeError:
                pass
            ui.settings_window = None

    def _post_debug_window(self, controller):
        """
        The debug window can't really reload itself while it's running,
        but at least close and re-open so next open uses the new class.
        """
        ui = controller.ui
        if hasattr(ui, 'debug_window') and ui.debug_window is not None:
            try:
                ui.debug_window.hide()
                ui.debug_window.deleteLater()
            except RuntimeError:
                pass
            ui.debug_window = None
            # Re-open with the new class
            QTimer.singleShot(150, ui.open_debug_window)

    # ── UI builder ────────────────────────────────────────────────────────────

    def _build_hot_reload_panel(self, parent_layout):
        """Build the collapsible Hot Reload panel and append it to parent_layout."""
        self._hr_status = {}   # module_path -> (success: bool, message: str) | None
        self._hr_expanded = {}  # module_path -> bool (traceback visible)

        # ── Collapsible header ────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #0f0f0f;
                border-top: 1px solid #00ff00;
                border-bottom: 1px solid #1a1a1a;
            }
        """)
        header.setFixedHeight(36)
        header.setCursor(Qt.CursorShape.PointingHandCursor)

        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(14, 0, 14, 0)

        self._hr_toggle_label = QLabel("▶  ⚡ Hot Reload")
        self._hr_toggle_label.setStyleSheet("color: #00ff00; font-size: 11px; font-weight: bold;")
        h_lay.addWidget(self._hr_toggle_label)
        h_lay.addStretch()

        hint = QLabel("click to expand")
        hint.setStyleSheet("color: #333; font-size: 9px;")
        h_lay.addWidget(hint)

        # ── Body (hidden by default) ──────────────────────────────────────────
        self._hr_body = QFrame()
        self._hr_body.setStyleSheet("QFrame { background-color: #0a0a0a; border-top: none; }")
        self._hr_body.hide()

        body_layout = QVBoxLayout(self._hr_body)
        body_layout.setContentsMargins(12, 10, 12, 12)
        body_layout.setSpacing(4)

        registry = self._reload_registry()
        self._hr_rows = {}  # module_path -> dict of widgets

        for label, module_path, hook_name in registry:
            disabled = hook_name is None
            row_widgets = self._build_reload_row(
                body_layout, label, module_path, hook_name, disabled
            )
            self._hr_rows[module_path] = row_widgets

        # ── Wire collapse toggle ──────────────────────────────────────────────
        self._hr_panel_open = False
        def _toggle_panel(event):
            self._hr_panel_open = not self._hr_panel_open
            self._hr_body.setVisible(self._hr_panel_open)
            self._hr_toggle_label.setText(
                ("▼" if self._hr_panel_open else "▶") + "  ⚡ Hot Reload"
            )
        header.mousePressEvent = _toggle_panel

        parent_layout.addWidget(header)
        parent_layout.addWidget(self._hr_body)

    def _build_reload_row(self, parent_layout, label, module_path, hook_name, disabled):
        """Build one row for the hot reload panel. Returns dict of row widgets."""
        _G = "#00ff00"
        _R = "#ff4444"
        _M = "#333333"

        row_frame = QFrame()
        row_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {'#111' if not disabled else '#0d0d0d'};
                border: 1px solid {'#1e1e1e' if not disabled else '#161616'};
                border-radius: 4px;
            }}
        """)

        row_lay = QHBoxLayout(row_frame)
        row_lay.setContentsMargins(10, 6, 10, 6)
        row_lay.setSpacing(8)

        # File label
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet(
            f"color: {'#888' if disabled else '#c8c8c8'}; font-size: 11px; "
            f"font-family: 'Courier New'; min-width: 180px;"
        )
        row_lay.addWidget(name_lbl)

        # Module path (dim)
        path_lbl = QLabel(module_path)
        path_lbl.setStyleSheet("color: #2a2a2a; font-size: 9px; font-family: 'Courier New';")
        row_lay.addWidget(path_lbl, stretch=1)

        # Status label
        status_lbl = QLabel("—")
        status_lbl.setStyleSheet(f"color: {_M}; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row_lay.addWidget(status_lbl)

        # Reload button
        btn = QPushButton("Reload" if not disabled else "restart required")
        btn.setFixedHeight(24)
        btn.setEnabled(not disabled)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {'transparent' if disabled else '#161616'};
                border: 1px solid {'#1a1a1a' if disabled else '#2a2a2a'};
                border-radius: 4px;
                color: {'#2a2a2a' if disabled else '#00cc00'};
                font-size: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover:enabled {{
                background: #1a2a1a;
                border-color: #00ff00;
                color: #00ff00;
            }}
            QPushButton:pressed:enabled {{
                background: #0a1a0a;
            }}
        """)

        # Traceback expander (hidden until there's an error)
        tb_frame = QFrame()
        tb_frame.setStyleSheet("QFrame { background: transparent; }")
        tb_frame.hide()
        tb_lay = QVBoxLayout(tb_frame)
        tb_lay.setContentsMargins(0, 4, 0, 0)
        tb_lay.setSpacing(0)

        tb_text = QTextEdit()
        tb_text.setReadOnly(True)
        tb_text.setFixedHeight(120)
        tb_text.setStyleSheet("""
            QTextEdit {
                background: #0d0000;
                border: 1px solid #3a0000;
                border-radius: 3px;
                color: #ff6666;
                font-size: 9px;
                font-family: 'Courier New';
                padding: 4px;
            }
        """)
        tb_lay.addWidget(tb_text)

        # Build the outer container (row + traceback stacked)
        outer = QFrame()
        outer.setStyleSheet("QFrame { background: transparent; border: none; }")
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)
        outer_lay.addWidget(row_frame)
        outer_lay.addWidget(tb_frame)
        parent_layout.addWidget(outer)

        widgets = {
            'status_lbl': status_lbl,
            'btn': btn,
            'tb_frame': tb_frame,
            'tb_text': tb_text,
            'row_frame': row_frame,
        }

        if not disabled:
            def _do_reload(checked=False, mp=module_path, hn=hook_name, w=widgets):
                self._run_reload(mp, hn, w)
            btn.clicked.connect(_do_reload)

            # Clicking the row when it shows a red status expands/collapses traceback
            def _toggle_tb(event, mp=module_path, w=widgets):
                st = self._hr_status.get(mp)
                if st and not st[0]:  # only when failed
                    visible = w['tb_frame'].isVisible()
                    w['tb_frame'].setVisible(not visible)
            row_frame.mousePressEvent = _toggle_tb
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)

        row_lay.addWidget(btn)
        return widgets

    def _run_reload(self, module_path, hook_name, widgets):
        """Execute a reload and update the row's status widgets."""
        _G = "#00ff00"
        _R = "#ff4444"

        widgets['btn'].setEnabled(False)
        widgets['status_lbl'].setText("reloading…")
        widgets['status_lbl'].setStyleSheet("color: #888; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")

        success, message = reload_module(module_path)
        self._hr_status[module_path] = (success, message)

        if success:
            widgets['row_frame'].setStyleSheet("""
                QFrame { background-color: #0a140a; border: 1px solid #1a3a1a; border-radius: 4px; }
            """)
            widgets['status_lbl'].setText(message)
            widgets['status_lbl'].setStyleSheet(f"color: {_G}; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")
            widgets['tb_frame'].hide()
            self.add_message("system", f"[hot_reload] ✓ {module_path}")

            # Run post-hook
            if hook_name and hasattr(self, hook_name):
                try:
                    getattr(self, hook_name)(self.controller)
                except Exception as e:
                    import traceback as _tb
                    self.add_message("system", f"[hot_reload] post-hook error:\n{_tb.format_exc()}")
        else:
            widgets['row_frame'].setStyleSheet("""
                QFrame { background-color: #140a0a; border: 1px solid #3a1a1a; border-radius: 4px; }
            """)
            short = message.split('\n')[0]
            widgets['status_lbl'].setText(short)
            widgets['status_lbl'].setStyleSheet(f"color: {_R}; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")
            widgets['tb_text'].setPlainText(message)
            widgets['tb_frame'].show()
            self.add_message("system", f"[hot_reload] ✕ {module_path}\n{message}")

        widgets['btn'].setEnabled(True)

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