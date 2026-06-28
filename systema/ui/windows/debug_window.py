"""
ui/debug_window.py
Debug Window - Shows AI tool usage conversations
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
                             QTextEdit, QPushButton, QLabel, QCheckBox, QFrame)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect
from PyQt6.QtGui import QFont, QRegion
from datetime import datetime
from systema.common.hot_reload import reload_module
from systema.ui.base_window import BaseWindow
from systema.ui import theme as _theme
import sys
import ctypes


# ── Theme palette (rebound per active theme by _refresh_palette) ──────────────
_BG       = "#0a0a0a"
_SURFACE  = "#1a1a1a"
_SURFACE2 = "#111111"
_BORDER   = "#30363D"
_ACCENT   = "#00ff00"
_ACCENT_DIM = "#00cc00"
_TEXT     = "#E8EFF8"
_MUTED    = "#888888"
_FAINT    = "#333333"
_RED      = "#ff4444"
_GREEN    = "#34D058"

# Functional per-message colours — kept stable across themes so each message
# type stays recognisable at a glance.
_MSG_COLORS = {'system': '#888888', 'ai': '#00ffff', 'tool': '#ffff00', 'user': '#ff00ff'}


def _refresh_palette(controller):
    """Rebind module-level colour constants to the active theme. Called at the
    top of __init__ and from apply_theme(); styling reads these globals at build
    time, so refreshing before (re)building retints the whole window."""
    global _BG, _SURFACE, _SURFACE2, _BORDER, _ACCENT, _ACCENT_DIM
    global _TEXT, _MUTED, _FAINT, _RED, _GREEN
    p = _theme.current_palette(controller)
    _BG, _SURFACE, _SURFACE2 = p['bg'], p['surface'], p['surface2']
    _BORDER, _ACCENT = p['border'], p['accent']
    _ACCENT_DIM = _theme.darken(p['accent'], 0.18)
    _TEXT, _MUTED = p['text'], p['muted']
    _FAINT = _theme.darken(p['muted'], 0.45)
    _RED, _GREEN = p['red'], p['green']


class DebugWindow(BaseWindow):
    """Debug window showing tool conversations"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        _refresh_palette(controller)   # match active theme before building UI

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

        # Window chrome state
        self._init_chrome_state()

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
        self.container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_SURFACE};
                border-radius: 12px;
            }}
            QWidget {{
                color: {_TEXT};
            }}
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
        self._sync_glass()

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
        header_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border-bottom: 1px solid {_BORDER};
            }}
        """)

        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        # Title
        title = QLabel("🔧 Debug - Full AI/Tool Exchanges")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {_TEXT};")
        header_layout.addWidget(title)

        header_layout.addStretch()

        _icon_btn = f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 6px;
                font-size: 16px; color: {_MUTED};
            }}
            QPushButton:hover {{ background: {_SURFACE2}; color: {_TEXT}; }}
        """

        # Clear button
        clear_btn = QPushButton("🗑️")
        clear_btn.setFixedSize(32, 32)
        clear_btn.setStyleSheet(_icon_btn)
        clear_btn.clicked.connect(self.clear_debug)
        clear_btn.setToolTip("Clear debug log")
        header_layout.addWidget(clear_btn)

        # NEW: CMD toggle button (only if launched from CMD)
        if self.launched_from_cmd:
            self.cmd_toggle_btn = QPushButton("🪟")
            self.cmd_toggle_btn.setFixedSize(32, 32)
            self.cmd_toggle_btn.setStyleSheet(_icon_btn)
            self.cmd_toggle_btn.clicked.connect(self.toggle_cmd_window)
            self.cmd_toggle_btn.setToolTip("Toggle CMD window")
            header_layout.addWidget(self.cmd_toggle_btn)

        # Minimize button
        minimize_btn = QPushButton("−")
        minimize_btn.setFixedSize(32, 32)
        minimize_btn.setStyleSheet(_icon_btn.replace("font-size: 16px", "font-size: 18px"))
        minimize_btn.clicked.connect(self.showMinimized)
        header_layout.addWidget(minimize_btn)

        # Maximize button
        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setFixedSize(32, 32)
        self.maximize_btn.setStyleSheet(_icon_btn)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        header_layout.addWidget(self.maximize_btn)

        # Close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: 6px;
                font-size: 22px; color: {_MUTED};
            }}
            QPushButton:hover {{ background: #EA4335; color: white; }}
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
        info.setStyleSheet(f"color: {_MUTED}; font-size: 10pt; margin: 5px 0;")
        content_layout.addWidget(info)

        # Filter section
        filter_layout = QHBoxLayout()
        filter_label = QLabel("Filters:")
        filter_label.setStyleSheet(f"color: {_ACCENT}; font-weight: bold;")
        filter_layout.addWidget(filter_label)

        # Filter checkboxes — keep the functional per-type colours
        self.user_checkbox = QCheckBox("👤 User")
        self.user_checkbox.setChecked(self.filters.get('user', True))
        self.user_checkbox.setStyleSheet(f"color: {_MSG_COLORS['user']};")
        self.user_checkbox.stateChanged.connect(lambda: self.update_filter('user', self.user_checkbox.isChecked()))
        filter_layout.addWidget(self.user_checkbox)

        self.ai_checkbox = QCheckBox("🤖 AI")
        self.ai_checkbox.setChecked(self.filters.get('ai', True))
        self.ai_checkbox.setStyleSheet(f"color: {_MSG_COLORS['ai']};")
        self.ai_checkbox.stateChanged.connect(lambda: self.update_filter('ai', self.ai_checkbox.isChecked()))
        filter_layout.addWidget(self.ai_checkbox)

        self.tool_checkbox = QCheckBox("🔧 Tool")
        self.tool_checkbox.setChecked(self.filters.get('tool', True))
        self.tool_checkbox.setStyleSheet(f"color: {_MSG_COLORS['tool']};")
        self.tool_checkbox.stateChanged.connect(lambda: self.update_filter('tool', self.tool_checkbox.isChecked()))
        filter_layout.addWidget(self.tool_checkbox)

        self.system_checkbox = QCheckBox("⚙️ System")
        self.system_checkbox.setChecked(self.filters.get('system', True))
        self.system_checkbox.setStyleSheet(f"color: {_MSG_COLORS['system']};")
        self.system_checkbox.stateChanged.connect(lambda: self.update_filter('system', self.system_checkbox.isChecked()))
        filter_layout.addWidget(self.system_checkbox)

        filter_layout.addStretch()

        # System Prompt viewer button
        sys_prompt_btn = QPushButton("📋 View System Prompt")
        sys_prompt_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE2};
                border: 1px solid {_ACCENT_DIM};
                border-radius: 5px;
                color: {_ACCENT};
                font-size: 9pt;
                padding: 3px 10px;
            }}
            QPushButton:hover {{
                background: {_theme.rgba(_ACCENT, 0.12)};
                border-color: {_ACCENT};
                color: {_ACCENT};
            }}
            QPushButton:pressed {{
                background: {_theme.rgba(_ACCENT, 0.20)};
            }}
        """)
        sys_prompt_btn.setToolTip("Show the full system prompt that will be sent to the API (base + all loaded skills)")
        sys_prompt_btn.clicked.connect(self.show_system_prompt)
        filter_layout.addWidget(sys_prompt_btn)
        content_layout.addLayout(filter_layout)

        # Debug display
        self.debug_display = QTextEdit()
        self.debug_display.setReadOnly(True)
        self.debug_display.setStyleSheet(f"""
            QTextEdit {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                padding: 10px;
                font-family: 'Courier New', monospace;
                font-size: 10px;
                color: {_TEXT};
                line-height: 1.4;
            }}
        """)
        self.debug_display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        content_layout.addWidget(self.debug_display)

        layout.addWidget(content_widget)

        # Hot Reload panel
        self._build_hot_reload_panel(layout)

        # Welcome message
        self.add_message("system", "╔══ DEBUG MODE ACTIVE ══╗\nAll AI/Tool exchanges will appear here in full detail")

    def apply_theme(self, theme_key=None):
        """Live-retint the window to the active theme. Refreshes the palette and
        rebuilds the container in place, preserving the current debug log and
        the Hot Reload panel's open/closed state."""
        try:
            _refresh_palette(self.controller)
            saved_html = self.debug_display.toHtml() if hasattr(self, 'debug_display') else None
            saved_hr_open = getattr(self, '_hr_panel_open', False)

            old = self.container
            self.layout().removeWidget(old)
            old.deleteLater()

            self.container = QWidget()
            self.container.setObjectName("container")
            self.container.setStyleSheet(f"""
                QWidget#container {{ background-color: {_SURFACE}; border-radius: 12px; }}
                QWidget {{ color: {_TEXT}; }}
            """)
            self.init_ui()
            self.layout().addWidget(self.container)

            if saved_html is not None:
                self.debug_display.setHtml(saved_html)
                self.scroll_to_bottom()
            if saved_hr_open and hasattr(self, '_hr_body'):
                self._hr_panel_open = True
                self._hr_body.setVisible(True)
                self._hr_toggle_label.setText("▼  ⚡ Hot Reload")

            self.apply_rounded_mask()
            self._sync_glass()
        except Exception as e:
            try:
                self.add_message("system", f"[apply_theme] error: {e}")
            except Exception:
                pass

    def _sync_glass(self):
        """Overlay a translucent backdrop when glass mode is on (the log area
        goes frosted; messages stay readable)."""
        try:
            if not _theme.glass_enabled_for(self.controller, 'debug'):
                return
            _, op = _theme.glass_state(self.controller)
            bd = _theme.glass_backdrop(op)
            panel = _theme.glass_panel(op)
            self.container.setStyleSheet(
                f"QWidget#container {{ background-color: {bd}; border-radius: 12px; }}"
                f"QWidget {{ color: {_TEXT}; }}"
            )
            # Log view → frosted near-opaque panel so the text stays readable
            if hasattr(self, 'debug_display'):
                self.debug_display.setStyleSheet(
                    f"QTextEdit {{ background-color: {panel}; border: 1px solid {_BORDER};"
                    f" border-radius: 5px; padding: 10px; font-family: 'Courier New', monospace;"
                    f" font-size: 10px; color: {_TEXT}; line-height: 1.4; }}"
                )
        except Exception as e:
            print(f"[DebugWindow._sync_glass] {e}")

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
            ("global_instructions.py", "systema.engine.prompts.global_instructions",      "_post_global_instructions"),
            ("chat_window.py",         "systema.ui.chat_window",                 "_post_chat_window"),
            ("settings_window.py",     "systema.ui.windows.settings_window",             "_post_settings_window"),
            ("debug_window.py",        "systema.ui.windows.debug_window",                "_post_debug_window"),
            ("tool_manager.py",        "systema.execution.tool_manager",              "_post_noop"),
            ("memory_manager.py",      "systema.memory.memory_manager",            "_post_noop"),
            ("skill_manager.py",       "systema.agents.skill_manager",             "_post_noop"),
            ("base_window.py",         "systema.ui.base_window",                 "_post_noop"),
            ("ai_engine.py",           "systema.engine.ai_engine",                 None),   # None = disabled
            ("controller.py",          "systema.app.controller",                None),   # None = disabled
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
            import systema.engine.prompts.global_instructions as gi
            import systema.engine.ai_engine as ae
            # Patch the names that ai_engine captured with `from X import Y`
            ae.get_system_prompt          = gi.get_system_prompt
            ae.get_gemini_system_prompt   = gi.get_gemini_system_prompt

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
        import systema.ui.chat_window as cw_mod
        import systema.ui.windows.floating_window as fw_mod

        # Patch the name captured by `from systema.ui.chat_window import ChatWindow`
        fw_mod.ChatWindow = cw_mod.ChatWindow

        ui = controller.ui
        was_visible = False
        if hasattr(ui, 'chat_window') and systema.ui.chat_window is not None:
            try:
                was_visible = systema.ui.chat_window.isVisible()
                systema.ui.chat_window.hide()
                systema.ui.chat_window.deleteLater()
            except RuntimeError:
                pass
            systema.ui.chat_window = None

        if was_visible:
            def _reopen():
                new_win = cw_mod.ChatWindow(controller)
                systema.ui.chat_window = new_win
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
        import systema.ui.windows.settings_window as sw_mod
        import systema.ui.windows.floating_window as fw_mod
        fw_mod.SettingsWindow = sw_mod.SettingsWindow

        ui = controller.ui
        if hasattr(ui, 'settings_window') and systema.ui.windows.settings_window is not None:
            try:
                systema.ui.windows.settings_window.hide()
                systema.ui.windows.settings_window.deleteLater()
            except RuntimeError:
                pass
            systema.ui.windows.settings_window = None

    def _post_debug_window(self, controller):
        """
        The debug window can't really reload itself while it's running,
        but at least close and re-open so next open uses the new class.
        """
        ui = controller.ui
        if hasattr(ui, 'debug_window') and systema.ui.windows.debug_window is not None:
            try:
                systema.ui.windows.debug_window.hide()
                systema.ui.windows.debug_window.deleteLater()
            except RuntimeError:
                pass
            systema.ui.windows.debug_window = None
            # Re-open with the new class
            QTimer.singleShot(150, ui.open_debug_window)

    # ── UI builder ────────────────────────────────────────────────────────────

    def _build_hot_reload_panel(self, parent_layout):
        """Build the collapsible Hot Reload panel and append it to parent_layout."""
        self._hr_status = {}   # module_path -> (success: bool, message: str) | None
        self._hr_expanded = {}  # module_path -> bool (traceback visible)

        # ── Collapsible header ────────────────────────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE2};
                border-top: 1px solid {_BORDER};
                border-bottom: 1px solid {_SURFACE};
            }}
        """)
        header.setFixedHeight(36)
        header.setCursor(Qt.CursorShape.PointingHandCursor)

        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(14, 0, 14, 0)

        self._hr_toggle_label = QLabel("▶  ⚡ Hot Reload")
        self._hr_toggle_label.setStyleSheet(f"color: {_ACCENT}; font-size: 11px; font-weight: bold;")
        h_lay.addWidget(self._hr_toggle_label)
        h_lay.addStretch()

        hint = QLabel("click to expand")
        hint.setStyleSheet(f"color: {_FAINT}; font-size: 9px;")
        h_lay.addWidget(hint)

        # ── Body (hidden by default) ──────────────────────────────────────────
        self._hr_body = QFrame()
        self._hr_body.setStyleSheet(f"QFrame {{ background-color: {_BG}; border-top: none; }}")
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
        _M = _FAINT

        row_frame = QFrame()
        row_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE2 if not disabled else _BG};
                border: 1px solid {_BORDER if not disabled else _theme.darken(_BORDER, 0.25)};
                border-radius: 4px;
            }}
        """)

        row_lay = QHBoxLayout(row_frame)
        row_lay.setContentsMargins(10, 6, 10, 6)
        row_lay.setSpacing(8)

        # File label
        name_lbl = QLabel(label)
        name_lbl.setStyleSheet(
            f"color: {_MUTED if disabled else _TEXT}; font-size: 11px; "
            f"font-family: 'Courier New'; min-width: 180px;"
        )
        row_lay.addWidget(name_lbl)

        # Module path (dim)
        path_lbl = QLabel(module_path)
        path_lbl.setStyleSheet(f"color: {_FAINT}; font-size: 9px; font-family: 'Courier New';")
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
                background: {'transparent' if disabled else _SURFACE2};
                border: 1px solid {_theme.darken(_BORDER, 0.3) if disabled else _BORDER};
                border-radius: 4px;
                color: {_FAINT if disabled else _ACCENT};
                font-size: 10px;
                padding: 0 10px;
            }}
            QPushButton:hover:enabled {{
                background: {_theme.rgba(_ACCENT, 0.12)};
                border-color: {_ACCENT};
                color: {_ACCENT};
            }}
            QPushButton:pressed:enabled {{
                background: {_theme.rgba(_ACCENT, 0.20)};
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
        tb_text.setStyleSheet(f"""
            QTextEdit {{
                background: {_theme.rgba(_RED, 0.08)};
                border: 1px solid {_theme.rgba(_RED, 0.35)};
                border-radius: 3px;
                color: {_theme.lighten(_RED, 0.2)};
                font-size: 9px;
                font-family: 'Courier New';
                padding: 4px;
            }}
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
        _G = _GREEN
        _R = _RED

        widgets['btn'].setEnabled(False)
        widgets['status_lbl'].setText("reloading…")
        widgets['status_lbl'].setStyleSheet(f"color: {_MUTED}; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")

        success, message = reload_module(module_path)
        self._hr_status[module_path] = (success, message)

        if success:
            widgets['row_frame'].setStyleSheet(f"""
                QFrame {{ background-color: {_theme.rgba(_GREEN, 0.08)}; border: 1px solid {_theme.rgba(_GREEN, 0.35)}; border-radius: 4px; }}
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
            widgets['row_frame'].setStyleSheet(f"""
                QFrame {{ background-color: {_theme.rgba(_RED, 0.08)}; border: 1px solid {_theme.rgba(_RED, 0.35)}; border-radius: 4px; }}
            """)
            short = message.split('\n')[0]
            widgets['status_lbl'].setText(short)
            widgets['status_lbl'].setStyleSheet(f"color: {_R}; font-size: 10px; font-family: 'Courier New'; min-width: 120px;")
            widgets['tb_text'].setPlainText(message)
            widgets['tb_frame'].show()
            self.add_message("system", f"[hot_reload] ✕ {module_path}\n{message}")

        widgets['btn'].setEnabled(True)

    def show_system_prompt(self):
        """Open a popup showing the full effective system prompt (base + loaded skills)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
        from PyQt6.QtGui import QClipboard
        from PyQt6.QtWidgets import QApplication

        try:
            prompt = self.controller.ai._get_effective_system_prompt()
        except Exception as e:
            prompt = f"[Error retrieving system prompt: {e}]"

        char_count = len(prompt)
        token_estimate = char_count // 4  # rough estimate

        dlg = QDialog(self)
        dlg.setWindowTitle("Full System Prompt")
        dlg.setMinimumSize(760, 600)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {_SURFACE};
            }}
            QLabel {{
                color: {_TEXT};
            }}
        """)

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(8)

        # Header
        header = QLabel(
            f"📋  Full Effective System Prompt  —  "
            f"{char_count:,} chars  ·  ~{token_estimate:,} tokens est."
        )
        header.setStyleSheet(f"color: {_ACCENT}; font-size: 11pt; font-weight: bold;")
        lay.addWidget(header)

        sub = QLabel("Base system prompt + all currently loaded skills (frontmatter stripped), exactly as sent to the API.")
        sub.setStyleSheet(f"color: {_MUTED}; font-size: 9pt;")
        lay.addWidget(sub)

        # Text view
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(prompt)
        txt.setStyleSheet(f"""
            QTextEdit {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                padding: 10px;
                font-family: 'Courier New', monospace;
                font-size: 9pt;
                color: {_TEXT};
                line-height: 1.4;
            }}
        """)
        lay.addWidget(txt)

        # Buttons
        btn_row = QHBoxLayout()

        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_SURFACE2};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_ACCENT};
                font-size: 10pt;
                padding: 6px 16px;
            }}
            QPushButton:hover {{ background: {_theme.rgba(_ACCENT, 0.12)}; border-color: {_ACCENT}; color: {_ACCENT}; }}
        """)
        def _copy():
            QApplication.clipboard().setText(prompt)
            copy_btn.setText("✅ Copied!")
            QTimer.singleShot(2000, lambda: copy_btn.setText("📋 Copy to Clipboard"))
        copy_btn.clicked.connect(_copy)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid {_BORDER};
                border-radius: 5px;
                color: {_MUTED};
                font-size: 10pt;
                padding: 6px 16px;
            }}
            QPushButton:hover {{ border-color: {_MUTED}; color: {_TEXT}; }}
        """)
        close_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        dlg.exec()

    def update_filter(self, filter_type, enabled):
        """NEW: Update filter state"""
        self.filters[filter_type] = enabled

    def add_message(self, sender, message):
        """Add debug message with full content"""
        if not self.filters.get(sender, True):
            return

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        icons = {
            'system': '⚙️',
            'ai': '🤖',
            'tool': '🔧',
            'user': '👤'
        }

        color = _MSG_COLORS.get(sender, _ACCENT)
        icon = icons.get(sender, '•')

        message_escaped = message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        html = f'''
        <div style="margin: 10px 0; padding: 10px; background: {_theme.rgba(color, 0.06)}; border-left: 4px solid {color}; border-radius: 3px;">
            <div style="color: {color}; font-weight: bold; margin-bottom: 8px;">
                {icon} {sender.upper()} <span style="color: {_MUTED}; font-size: 8pt; font-weight: normal;">[{timestamp}]</span>
            </div>
            <div style="color: {_TEXT}; white-space: pre-wrap; font-size: 9pt; font-family: 'Courier New', monospace;">
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