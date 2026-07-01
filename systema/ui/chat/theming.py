"""
systema/ui/chat/theming.py
ThemingMixin — theme / glass / zoom application for ChatWindow.
Extracted verbatim from chat_window.py.
"""
from systema.ui.theme import THEMES as _SHARED_THEMES
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


class ThemingMixin:
    """Theme palette, glass background, and zoom (mixed into ChatWindow)."""

    def _get_msg_font_size(self) -> int:
        """Return the current message text font size, scaled by chat_zoom."""
        return max(9, int(13 * getattr(self, 'chat_zoom', 1.0)))

    def zoom_in(self):
        """Increase message font size one step and persist."""
        if getattr(self, 'chat_zoom', 1.0) < 1.8:
            self.chat_zoom = round(self.chat_zoom + 0.1, 1)
            self._apply_zoom_all()
            self.save_config()

    def zoom_out(self):
        """Decrease message font size one step and persist."""
        if getattr(self, 'chat_zoom', 1.0) > 0.6:
            self.chat_zoom = round(self.chat_zoom - 0.1, 1)
            self._apply_zoom_all()
            self.save_config()

    def _apply_zoom_all(self):
        """Apply the current zoom level to all existing message text labels."""
        fsize = self._get_msg_font_size()
        for md in self.message_widgets:
            role = md.get('role', '')
            text_color = '#E8EAED' if role == 'user' else '#BDC1C6'
            style = (
                f"QLabel {{ color: {text_color}; font-size: {fsize}px; "
                f"line-height: 1.5; background: transparent; border: none; }}"
            )
            for lbl in md.get('text_labels', []):
                try:
                    lbl.setStyleSheet(style)
                except RuntimeError:
                    pass
        # Re-apply responsive bubble widths (the cap scales with zoom)
        self._reflow_bubbles()

    # ═══════════════════════════════════════════════════════════════════════════
    # GLASS BUBBLE HELPER
    # ═══════════════════════════════════════════════════════════════════════════

    def _apply_glass_to_bubbles(self, enabled: bool):
        """Update all existing message bubble backgrounds for glass / solid mode."""
        for md in self.message_widgets:
            cw = md.get('content_wrapper')
            if cw is None:
                continue
            role = md.get('role', '')
            try:
                if enabled:
                    bg = 'rgba(37, 37, 37, 0.55)' if role == 'user' else 'rgba(42, 42, 42, 0.55)'
                    cw.setStyleSheet(f"""
                        QFrame {{
                            background-color: {bg};
                            border: 1px solid rgba(60, 60, 60, 0.4);
                            border-radius: 12px;
                        }}
                    """)
                else:
                    bg = '#1E2228' if role == 'user' else '#21262D'
                    cw.setStyleSheet(f"""
                        QFrame {{
                            background-color: {bg};
                            border: 1px solid #30363D;
                            border-radius: 12px;
                        }}
                    """)
            except RuntimeError:
                pass

    # ═══════════════════════════════════════════════════════════════════════════
    # THEME APPLICATION
    # ═══════════════════════════════════════════════════════════════════════════

    # Palette now lives in ui/theme.py (single source of truth shared by every
    # window). Kept as a class attribute so existing self._THEMES references work.
    _THEMES = _SHARED_THEMES

    def _t(self) -> dict:
        """Return the current live theme dict — always in sync with the last apply_theme call."""
        key = getattr(self, '_current_theme_key', 'obsidian_blue')
        return self._THEMES.get(key, self._THEMES['obsidian_blue'])

    def apply_theme(self, theme_key: str):
        """Apply a named colour theme to all major structural surfaces."""
        t = self._THEMES.get(theme_key, self._THEMES['obsidian_blue'])
        self._current_theme_key = theme_key   # remember for apply_glass_background
        # Mirror the theme to the Android phone if one is connected
        try:
            _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
            if _ab and _ab.isVisible():
                _ab.send_theme(t)
        except Exception:
            pass
        try:
            # Container
            self.container.setStyleSheet(f"""
                QWidget#container {{
                    background-color: {t['surface']};
                    border-radius: 12px;
                }}
                QWidget {{
                    color: #E8EAED;
                    font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                }}
            """)
            # Chat body
            self.chat_widget.setStyleSheet(
                f"QWidget {{ background-color: {t['surface']}; }}"
            )
            # Scroll area
            self.chat_scroll_area.setStyleSheet(f"""
                QScrollArea {{ border: none; background-color: {t['surface']}; }}
                QScrollBar:vertical {{
                    background: transparent; width: 12px; margin: 0;
                }}
                QScrollBar::handle:vertical {{
                    background: rgba(168,199,250,0.3); border-radius:6px; min-height:30px; margin:2px;
                }}
                QScrollBar::handle:vertical:hover  {{ background: rgba(168,199,250,0.5); }}
                QScrollBar::handle:vertical:pressed {{ background: rgba(168,199,250,0.7); }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
                QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """)
            # Header
            self.header_bar.setStyleSheet(f"""
                QFrame {{
                    background-color: {t['surface']};
                    border-bottom: 1px solid {t['border']};
                }}
                QLabel {{ background-color: transparent; }}
            """)
            # Status label
            self.status_label.setStyleSheet(f"""
                QLabel#statusLabel {{
                    color: #9AA0A6; font-style: italic; font-size: 11px;
                    padding: 5px 14px;
                    background-color: {t['deep']};
                    border-top: 1px solid {t['border']};
                }}
            """)
            # Work mode banner — recolour with active theme
            if hasattr(self, '_work_banner'):
                self._work_banner.setStyleSheet(f"""
                                QLabel#workBanner {{
                                    background-color: {t['elevated']};
                                    border-top: 1px solid {t['border']};
                                    border-bottom: 1px solid {t['border']};
                                    color: {t['accent']};
                                    font-size: 11px;
                                    font-style: italic;
                                    padding: 6px 14px;
                                }}
                            """)
            # Input container
            self.input_container.setStyleSheet(f"""
                            QFrame#inputContainer {{
                                background-color: {t['deep']};
                                border-top: 1px solid {t['border']};
                            }}
                        """)
            # Sidebar (solid themed styling)
            self._apply_sidebar_theme()
            # Input card — use stored reference first, then fallback search
            from PyQt6.QtWidgets import QFrame as _QF
            _ic = getattr(self, '_input_card_ref', None)
            if _ic is None:
                for child in self.container.findChildren(_QF):
                    if child.objectName() == "inputCard":
                        self._input_card_ref = child
                        _ic = child
                        break
            if _ic:
                try:
                    _ic.setStyleSheet(f"""
                        QFrame#inputCard {{
                            background-color: {t['input_card']};
                            border: 1px solid {t['input_card_border']};
                            border-radius: 18px;
                        }}
                    """)
                except RuntimeError:
                    self._input_card_ref = None

            # Message bubbles
            for md in self.message_widgets:
                role = md.get('role', '')
                cw = md.get('content_wrapper')
                if cw:
                    try:
                        if role == 'system':
                            # system messages use a QLabel as the styled surface
                            cw.setStyleSheet(f"""
                                QLabel {{
                                    background-color: {t['elevated']};
                                    border: 1px solid {t['border']};
                                    border-radius: 8px;
                                    padding: 10px 16px;
                                    color: #9AA0A6;
                                    font-size: 11px;
                                    line-height: 1.4;
                                }}
                            """)
                        elif role == 'memory_context':
                            try:
                                cw.setStyleSheet(f"""
                                                                QFrame {{
                                                                    background-color: {t['elevated']};
                                                                    border: 1px solid {t['border']};
                                                                    border-radius: 8px;
                                                                }}
                                                            """)
                                tb = md.get('_toggle_btn')
                                if tb:
                                    tb.setStyleSheet(f"""
                                                                    QPushButton {{
                                                                        background: transparent; border: 1px solid {t['border']};
                                                                        border-radius: 4px; font-size: 10px; color: #8B949E; padding: 0 6px;
                                                                    }}
                                                                    QPushButton:hover {{ color: {t['accent']}; border-color: {t['accent']}; }}
                                                                """)
                            except RuntimeError:
                                pass
                        else:
                            bg = t['elevated'] if role != 'user' else t['surface']
                            cw.setStyleSheet(f"""
                                                            QFrame {{
                                                                background-color: {bg};
                                                                border: 1px solid {t['border']};
                                                                border-radius: 12px;
                                                            }}
                                                        """)
                    except RuntimeError:
                        pass

            # If glass mode is active, re-apply it on top so a theme change
            # (e.g. the settings-Save broadcast) doesn't revert to solid surfaces.
            if getattr(self, '_glass_enabled', False):
                self.apply_glass_background(True, getattr(self, '_glass_opacity', 0.75))
        except Exception as e:
            log.error(f"[ChatWindow.apply_theme] Error: {e}")

    def _apply_sidebar_theme(self):
        """Apply the solid, themed styling to the sidebar. Used by apply_theme,
        and by the glass path when the sidebar is opted OUT of the overlay."""
        if not hasattr(self, 'sidebar'):
            return
        t = self._t()
        self.sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background-color: {t['base']};
                border-right: 1px solid {t['border']};
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
            }}
        """)
        from PyQt6.QtWidgets import QWidget as _QW, QLineEdit as _QLE
        for w in self.sidebar.findChildren(_QW):
            if w.objectName() == "sidebarContent":
                w.setStyleSheet(f"QWidget#sidebarContent {{ background-color: {t['base']}; }}")
            elif w.objectName() == "sidebarHero":
                w.setStyleSheet(f"QFrame#sidebarHero {{ background-color: {t['base']}; border-bottom: 1px solid {t['border']}; }}")
        for inp in self.sidebar.findChildren(_QLE):
            try:
                inp.setStyleSheet(f"""
                    QLineEdit {{ background: {t['elevated']}; border: 1px solid {t['border']};
                        border-radius: 6px; padding: 0 8px; font-size: 10px; color: #8B949E; }}
                    QLineEdit:focus {{ border-color: rgba(88,166,255,0.45); color: #E6EDF3; }}
                """)
            except RuntimeError:
                pass

    def apply_glass_background(self, enabled: bool, opacity: float = 0.75):
        """Apply or remove the glass (frosted-translucent) theme.

        Glass mode zones:
          • Main container       — fully transparent (desktop shows through)
          • Chat messages area   — semi-transparent dark backdrop (opacity from slider)
          • Scroll area          — transparent with a visible scrollbar track
          • Header bar           — matches chat body opacity (uniform look)
          • Status/thinking bar  — semi-opaque dark grey so text always readable
          • Input container      — semi-opaque dark grey frosted panel (VISIBLE, not removed)
          • inputCard pill        — solid dark, unchanged
          • Message bubbles       — semi-transparent to blend with glass body
          • Sidebar              — UNTOUCHED, always solid

        Dark (non-glass) mode restores every zone to its solid colour.
        All colours are neutral dark greys — no tints.
        """
        try:
            op = max(0.15, min(0.95, float(opacity)))
            # backdrop for messages area — darker as opacity goes up
            base = int(op * 28)
            bg_rgba = f"rgba({base},{base},{base},{op:.2f})"

            # Track state so new bubbles created while glass is active use the right style
            self._glass_enabled = enabled
            self._glass_opacity = opacity

            # Scrollbar: visible track in glass mode, solid in dark mode
            _scrollbar_glass = """
                QScrollBar:vertical {
                    background: rgba(255,255,255,0.06);
                    width: 12px; margin: 0; border-radius: 6px;
                }
                QScrollBar::handle:vertical {
                    background: rgba(168,199,250,0.35);
                    border-radius: 6px; min-height: 30px; margin: 2px;
                }
                QScrollBar::handle:vertical:hover  { background: rgba(168,199,250,0.55); }
                QScrollBar::handle:vertical:pressed { background: rgba(168,199,250,0.75); }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical { background: transparent; }
            """
            _scrollbar_solid = """
                QScrollBar:vertical {
                    background: transparent; width: 12px; margin: 0;
                }
                QScrollBar::handle:vertical {
                    background: rgba(168,199,250,0.3);
                    border-radius: 6px; min-height: 30px; margin: 2px;
                }
                QScrollBar::handle:vertical:hover  { background: rgba(168,199,250,0.5); }
                QScrollBar::handle:vertical:pressed { background: rgba(168,199,250,0.7); }
                QScrollBar::add-line:vertical,
                QScrollBar::sub-line:vertical { height: 0px; }
                QScrollBar::add-page:vertical,
                QScrollBar::sub-page:vertical { background: transparent; }
            """

            if enabled:
                # ── outer container: fully transparent ────────────────────────
                self.container.setStyleSheet("""
                    QWidget#container {
                        background-color: transparent;
                        border-radius: 12px;
                    }
                    QWidget {
                        color: #E8EAED;
                        font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                    }
                """)

                # ── chat messages backdrop ─────────────────────────────────────
                self.chat_widget.setStyleSheet(
                    f"QWidget {{ background-color: {bg_rgba}; }}"
                )
                self.chat_scroll_area.setStyleSheet(
                    "QScrollArea { border: none; background-color: transparent; }"
                    + _scrollbar_glass
                )

                # ── header: uniform with chat body (same bg_rgba) ─────────────
                self.header_bar.setStyleSheet(f"""
                    QFrame {{
                        background-color: {bg_rgba};
                        border-bottom: 1px solid rgba(50, 50, 50, 0.5);
                    }}
                    QLabel {{
                        background-color: transparent;
                    }}
                """)

                # ── status / thinking bar: frosted so text is always readable ─
                self.status_label.setStyleSheet(f"""
                    QLabel#statusLabel {{
                        color: #9AA0A6;
                        font-style: italic;
                        font-size: 11px;
                        padding: 5px 14px;
                        background-color: {bg_rgba};
                        border-top: 1px solid rgba(50, 50, 50, 0.5);
                    }}
                """)

                # ── input container: frosted opaque panel — stays visible ─────
                self.input_container.setStyleSheet("""
                    QFrame#inputContainer {
                        background-color: rgba(18, 18, 18, 0.85);
                        border-top: 1px solid rgba(50, 50, 50, 0.6);
                        border-bottom-left-radius: 12px;
                        border-bottom-right-radius: 12px;
                    }
                """)

                # ── sidebar: frost it only if the user opted the sidebar in.
                #    Otherwise leave it solid-themed (its own checklist sub-toggle).
                _sidebar_glass = True
                try:
                    _sidebar_glass = bool(self.controller.settings.get('glass_chat_sidebar', True))
                except Exception:
                    pass
                if hasattr(self, 'sidebar') and _sidebar_glass:
                    # frosted near-opaque panel so the sidebar text stays readable
                    panel_a = max(0.86, min(0.96, op + 0.18))
                    panel_rgba = f"rgba(20,20,22,{panel_a:.2f})"
                    self.sidebar.setStyleSheet(f"""
                        QFrame#sidebar {{
                            background-color: {panel_rgba};
                            border-right: 1px solid rgba(50, 50, 50, 0.5);
                            border-top-left-radius: 12px;
                            border-bottom-left-radius: 12px;
                        }}
                    """)
                    from PyQt6.QtWidgets import QWidget as _QW2
                    for w in self.sidebar.findChildren(_QW2):
                        if w.objectName() == "sidebarContent":
                            w.setStyleSheet("QWidget#sidebarContent { background-color: transparent; }")
                        elif w.objectName() == "sidebarHero":
                            w.setStyleSheet(
                                "QFrame#sidebarHero { background-color: transparent;"
                                " border-bottom: 1px solid rgba(50, 50, 50, 0.5); }"
                            )
                else:
                    # sidebar opted out of glass → keep it solid + themed
                    self._apply_sidebar_theme()

                # ── message bubbles stay solid — no change needed ─────────────

            else:
                # ── restore solid theme surfaces ─────────────────────────────
                # Delegate to apply_theme so the chosen palette is used,
                # not hardcoded Obsidian Blue colours.
                theme_key = getattr(self, '_current_theme_key', 'obsidian_blue')
                self.apply_theme(theme_key)

        except Exception as e:
            log.error(f"[ChatWindow.apply_glass_background] Error: {e}")

    def _apply_glass_from_settings(self):
        """Read glass and theme settings from controller and apply on startup."""
        try:
            settings = self.controller.settings
            # Apply theme first so _current_theme_key is set
            theme_key = settings.get('chat_theme', 'obsidian_blue')
            self.apply_theme(theme_key)
            # Then apply glass on top if enabled AND the chat window is opted in
            from systema.ui import theme as _theme
            opacity = float(settings.get('glass_background_opacity', 0.75))
            if _theme.glass_enabled_for(self.controller, 'chat'):
                self.apply_glass_background(True, opacity)
        except Exception as e:
            log.error(f"[ChatWindow._apply_glass_from_settings] Error: {e}")

