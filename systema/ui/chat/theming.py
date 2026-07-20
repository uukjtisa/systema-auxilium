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

    def _card_z(self, px: float) -> int:
        """Scale a tool-card / system-note font size by the current chat zoom
        (13px message base), so cards resize with Ctrl+scroll exactly like the
        message text. Floors at 7px so tiny chips stay legible."""
        return max(7, round(px * self._get_msg_font_size() / 13.0))

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
            # Tool/file/memory cards + system notes register a restyle closure
            # that re-applies every font-bearing style at the current zoom.
            rs = md.get('zoom_restyle')
            if rs is not None:
                try:
                    rs()
                except RuntimeError:
                    pass
        # Re-apply responsive bubble widths (the cap scales with zoom)
        self._reflow_bubbles()

    # ═══════════════════════════════════════════════════════════════════════════
    # GLASS BUBBLE HELPER
    # ═══════════════════════════════════════════════════════════════════════════

    def _apply_glass_to_bubbles(self, enabled: bool):
        """Update all existing message bubble backgrounds for glass / solid mode.
        Delegates to apply_bubble_style, which reads self._glass_enabled and
        renders the current style (blend | compact) accordingly."""
        self._glass_enabled = enabled
        self.apply_bubble_style()

    def _sync_glass_effects(self):
        """DWM acrylic RETIRED (2026-07-17): on current Win11 builds the
        undocumented SetWindowCompositionAttribute accent renders the whole
        translucent window BLACK instead of frosted (user-reproduced). Glass
        mode is pure Qt translucency again; blend readability comes from the
        smoked rgba text panels that bubble_wrapper_css applies in glass mode.
        Kept as a best-effort CLEANUP so any accent left by an older run is
        removed from the window."""
        try:
            from systema.ui.win_effects import disable_blur_behind
            disable_blur_behind(int(self.winId()))
        except Exception:
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
        """Apply a named colour theme to all major structural surfaces.

        GLASS-AWARE: when the glass overlay is active it OWNS the window
        chrome (container, chat backdrop, scroll area, input band, sidebar),
        so those zones are delegated to apply_glass_background instead of
        being painted solid first. The old paint-solid-then-reglass double
        pass is what made glass toggles look broken and left the window
        hanging glassy for a beat on long chats."""
        t = self._THEMES.get(theme_key, self._THEMES['obsidian_blue'])
        self._current_theme_key = theme_key   # remember for apply_glass_background
        # Mirror the theme to the Android phone if one is connected
        try:
            _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
            if _ab and _ab.isVisible():
                _ab.send_theme(t)
        except Exception:
            pass
        _glass_on = getattr(self, '_glass_enabled', False)
        try:
            if not _glass_on:
                # Container carries the WHOLE backdrop (theme gradient) —
                # it is a plain QWidget, where background + border-radius
                # compose into smooth antialiased corners. Backgrounds on the
                # scroll area do NOT round (Qt paints them on the viewport,
                # which ignores the frame radius — the square-corner bug).
                from systema.ui.theme import lighten as _lighten
                _grad_top = _lighten(t['base'], 0.025)
                _grad_bottom = t.get('deep', t['base'])
                self.container.setStyleSheet(f"""
                    QWidget#container {{
                        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 {_grad_top}, stop:0.55 {t['base']},
                            stop:1 {_grad_bottom});
                        border-radius: 12px;
                    }}
                    QWidget {{
                        color: #E8EAED;
                        font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                    }}
                """)
                # Chat body + scroll area — fully transparent; the container's
                # gradient shows through with rounded corners intact.
                self.chat_widget.setStyleSheet(
                    "QWidget { background-color: transparent; }"
                )
                self.chat_scroll_area.setStyleSheet("""
                    QScrollArea { border: none; background: transparent; }
                    QScrollArea > QWidget > QWidget { background: transparent; }
                    QScrollBar:vertical { width: 0px; }
                """)
                # Status + work labels — compact inline chips inside the input pill
                self.status_label.setStyleSheet("""
                    QLabel#statusLabel {
                        color: #C7CBD1; font-style: italic; font-size: 10px;
                        background: transparent; padding: 0 4px;
                    }
                """)
                if hasattr(self, '_work_banner'):
                    self._work_banner.setStyleSheet(f"""
                        QLabel#workBanner {{
                            color: {t['accent']}; font-size: 10px; font-style: italic;
                            background: transparent; padding: 0 4px;
                        }}
                    """)
                # Input container — the OUTER band is fully transparent so the chat
                # shows through around the pill (the pill itself stays solid).
                self.input_container.setStyleSheet("""
                                QFrame#inputContainer {
                                    background: transparent;
                                    border: none;
                                }
                            """)
                # Sidebar (solid themed styling)
                self._apply_sidebar_theme()
                # Input card — SOLID (opaque) pill so the text stays crisp
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

            # Bottom fade strip above the pill follows the theme's base tone
            # in BOTH modes.
            _fade = getattr(self, '_chat_fade', None)
            if _fade is not None:
                _fade.set_color(t['base'])

            # Message bubbles
            for md in self.message_widgets:
                role = md.get('role', '')
                cw = md.get('content_wrapper')
                if cw:
                    try:
                        if role in ('system', 'memory_context'):
                            # Both register a zoom_restyle closure that reads
                            # the LIVE theme + zoom — one call restyles fully.
                            # (System notes are faint transparent text now —
                            # no theme surface of their own.)
                            rs = md.get('zoom_restyle')
                            if rs is not None:
                                try:
                                    rs()
                                except RuntimeError:
                                    pass
                        # user/assistant bubbles are handled by
                        # apply_bubble_style() below (blend | compact).
                    except RuntimeError:
                        pass

            if _glass_on:
                # Glass owns the chrome — re-affirm it with the new palette in
                # ONE pass (ends with apply_bubble_style → bubbles + acrylic).
                self.apply_glass_background(True, getattr(self, '_glass_opacity', 0.75))
            else:
                # Re-style user/assistant bubbles for the new palette in the
                # currently selected bubble style (also syncs glass effects).
                self.apply_bubble_style()
            # Repaint the message navigator so its accent/panel follow the theme.
            try:
                nav = getattr(self, '_msg_navigator', None)
                if nav is not None:
                    nav.update()
            except (RuntimeError, AttributeError):
                pass
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
        # Inner panels stay TRANSPARENT — the #sidebar frame paints the themed
        # bg with its rounded left corners; opaque children squared them off.
        for w in self.sidebar.findChildren(_QW):
            if w.objectName() == "sidebarContent":
                w.setStyleSheet("QWidget#sidebarContent { background-color: transparent; }")
            elif w.objectName() == "sidebarHero":
                w.setStyleSheet(f"QFrame#sidebarHero {{ background-color: transparent; border-bottom: 1px solid {t['border']}; }}")
        for inp in self.sidebar.findChildren(_QLE):
            try:
                inp.setStyleSheet(f"""
                    QLineEdit {{ background: {t['elevated']}; border: 1px solid {t['border']};
                        border-radius: 6px; padding: 0 8px; font-size: 10px; color: #8B949E; }}
                    QLineEdit:focus {{ border-color: rgba(88,166,255,0.45); color: #E6EDF3; }}
                """)
            except RuntimeError:
                pass
        # Re-theme the Skills block so it follows the active palette (it draws
        # its own accent/surfaces rather than inheriting the sidebar sheet).
        sk = getattr(self, '_skills_section', None)
        if sk is not None:
            try:
                from systema.ui.theme import resolve_palette
                sk.apply_palette(resolve_palette(t))
            except (RuntimeError, AttributeError):
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

            # Scrollbar: hidden in both modes — the MessageNavigator overlay
            # replaces it (2026-07 redesign).
            _scrollbar_glass = "QScrollBar:vertical { width: 0px; }"
            _scrollbar_solid = "QScrollBar:vertical { width: 0px; }"

            if enabled:
                t = self._t()
                # ── outer container: carries the translucent backdrop — a
                #    plain QWidget rounds background + radius correctly (a
                #    scroll-area background paints square on the viewport) ────
                self.container.setStyleSheet(f"""
                    QWidget#container {{
                        background-color: {bg_rgba};
                        border-radius: 12px;
                    }}
                    QWidget {{
                        color: #E8EAED;
                        font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
                    }}
                """)

                # ── chat area: fully transparent over the container ───────────
                self.chat_widget.setStyleSheet(
                    "QWidget { background-color: transparent; }"
                )
                self.chat_scroll_area.setStyleSheet(
                    "QScrollArea { border: none; background: transparent; }"
                    + _scrollbar_glass
                )

                # (header bar removed in the 2026-07 redesign — no glass styling)

                # ── status + work chips: compact, inside the pill (no bar) ────
                self.status_label.setStyleSheet("""
                    QLabel#statusLabel {
                        color: #C7CBD1; font-style: italic; font-size: 10px;
                        background: transparent; padding: 0 4px;
                    }
                """)
                if hasattr(self, '_work_banner'):
                    self._work_banner.setStyleSheet(f"""
                        QLabel#workBanner {{
                            color: {t['accent']}; font-size: 10px; font-style: italic;
                            background: transparent; padding: 0 4px;
                        }}
                    """)

                # ── input container: fully SEE-THROUGH band (chat/desktop shows
                #    through around the pill) ───────────────────────────────────
                self.input_container.setStyleSheet("""
                    QFrame#inputContainer {
                        background: transparent;
                        border: none;
                        border-bottom-left-radius: 12px;
                        border-bottom-right-radius: 12px;
                    }
                """)
                # ── input pill: SOLID (opaque) even in glass mode ─────────────
                _ic = getattr(self, '_input_card_ref', None)
                if _ic is None:
                    from PyQt6.QtWidgets import QFrame as _QF2
                    for child in self.container.findChildren(_QF2):
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

                # ── message bubbles: restyle for glass (blend gets the smoked
                #    AI panel; compact gets translucent bordered cards) ────────
                self.apply_bubble_style()

            else:
                # ── restore solid theme surfaces ─────────────────────────────
                # Drop the DWM acrylic IMMEDIATELY — the restyle below takes a
                # moment on long chats, and a lingering accent is what made
                # disabling glass hang "glassy" before snapping to normal.
                self._sync_glass_effects()
                # Delegate to apply_theme so the chosen palette is used,
                # not hardcoded Obsidian Blue colours. (_glass_enabled is
                # already False, so apply_theme runs its solid path.)
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

