"""
systema/ui/chat/sidebar.py
SidebarMixin — sidebar build, toggle/animation, session list.
Extracted verbatim from chat_window.py (full-split pass, 2026-07-17).
"""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QTextEdit, QLineEdit, QPushButton, QLabel,
                             QFrame, QMenu, QScrollArea, QApplication,
                             QGraphicsOpacityEffect, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QRect, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PyQt6.QtGui import QAction, QCursor, QRegion, QPixmap
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from systema.common.logger import _make_logger, _NoOpLogger
from systema.ui.chat.constants import *
from systema import APP_ROOT as _APP_ROOT
from systema.ui.widgets.skills_sidebar import SkillsSidebarSection, SkillsPanel
from systema.ui.chat.window_controls import PanelToggleButton

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


class SidebarMixin:
    """Sidebar overlay + session history (mixed into ChatWindow)."""

    def toggle_sidebar(self):
        """Toggle sidebar visibility with a smooth slide animation."""
        self.sidebar_visible = not self.sidebar_visible
        self._animate_sidebar(self.sidebar_visible)

    def _sidebar_resize_press(self, event):
        """Start sidebar width drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._sidebar_resize_active = True
            self._sidebar_resize_start_x = event.globalPosition().toPoint().x()
            self._sidebar_resize_start_w = self._sidebar_w
            event.accept()

    def _sidebar_resize_move(self, event):
        """Update sidebar width while dragging. Dragging well PAST the minimum
        (raw request below SIDEBAR_CLOSE_TRIGGER_W) is a close gesture: the
        drag ends and the sidebar slides shut, keeping its pre-drag width for
        the next open."""
        if not self._sidebar_resize_active:
            return
        dx = event.globalPosition().toPoint().x() - self._sidebar_resize_start_x
        raw_w = self._sidebar_resize_start_w + dx
        if raw_w < SIDEBAR_CLOSE_TRIGGER_W and self.sidebar_visible:
            self._sidebar_resize_active = False
            self.toggle_sidebar()   # slides out at its current (dragged) width
            self._sidebar_w = self._sidebar_resize_start_w  # next open = pre-drag width
            event.accept()
            return
        new_w = max(SIDEBAR_MIN_W, min(SIDEBAR_MAX_W, raw_w))
        if new_w != self._sidebar_w:
            self._sidebar_w = new_w
            container_h = self.container.height()
            self.sidebar.setFixedWidth(new_w)
            self.sidebar.setGeometry(0, 0, new_w, container_h)
        event.accept()

    def _sidebar_resize_release(self, event):
        """Finish sidebar width drag."""
        self._sidebar_resize_active = False
        event.accept()

    def _animate_sidebar(self, show: bool):
        """Slide the sidebar in (show=True) or out (show=False)."""
        if self._sidebar_anim is not None:
            if self._sidebar_anim.state() == QPropertyAnimation.State.Running:
                self._sidebar_anim.stop()

        container_h = self.container.height()
        sidebar_w = self._sidebar_w

        if show:
            self.sidebar.setFixedWidth(sidebar_w)
            self.sidebar.setGeometry(-sidebar_w, 0, sidebar_w, container_h)
            self.sidebar.show()
            self.sidebar.raise_()
            # The floating toggle hides while the sidebar is open — its
            # hero-docked twin (◁ on hover) takes over. Overlap bug fixed.
            self.toggle_sidebar_btn.hide()
            start_geo = QRect(-sidebar_w, 0, sidebar_w, container_h)
            end_geo   = QRect(0,          0, sidebar_w, container_h)
        else:
            start_geo = QRect(0,          0, sidebar_w, container_h)
            end_geo   = QRect(-sidebar_w, 0, sidebar_w, container_h)

        self._sidebar_anim = QPropertyAnimation(self.sidebar, b"geometry")
        self._sidebar_anim.setDuration(ANIM_SIDEBAR_SLIDE_MS)
        self._sidebar_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._sidebar_anim.setStartValue(start_geo)
        self._sidebar_anim.setEndValue(end_geo)

        if not show:
            def _on_closed():
                self.sidebar.hide()
                # floating toggle returns once the panel has fully left
                self.toggle_sidebar_btn.show()
                self.toggle_sidebar_btn.raise_()
            self._sidebar_anim.finished.connect(_on_closed)

        self._sidebar_anim.start()

    def _refresh_hero_labels(self):
        """Update the hero name labels after identity changes."""
        if hasattr(self, '_hero_bot_name'):
            name = self.controller.get_assistant_name() or "Systema Auxilium"
            self._hero_bot_name.setText(name)
        if hasattr(self, '_hero_user_name'):
            user = self.controller.get_user_name() or "You"
            self._hero_user_name.setText(user)

    def _start_session_lock_watcher(self):
        """Lock the session list then start a QTimer on the main thread that polls
        until both is_processing and work.is_working are False, then unlocks."""
        self.set_session_list_locked(True, "AI is responding…")

        if hasattr(self, '_lock_watcher_timer') and self._lock_watcher_timer is not None:
            try:
                self._lock_watcher_timer.stop()
            except Exception:
                pass

        self._lock_watcher_timer = QTimer(self)
        self._lock_watcher_timer.setInterval(250)
        self._lock_watcher_timer.timeout.connect(self._check_session_lock_state)
        self._lock_watcher_timer.start()

    def _check_session_lock_state(self):
        """Called every 250 ms (main thread) to check if the AI is still busy."""
        try:
            processing = getattr(self.controller, 'is_processing', False)
            try:
                in_work = self.controller.ai.tool_manager.work.is_working
            except Exception:
                in_work = False
            if not processing and not in_work:
                self._lock_watcher_timer.stop()
                self._lock_watcher_timer = None
                self.set_session_list_locked(False)
        except Exception:
            try:
                self._lock_watcher_timer.stop()
                self._lock_watcher_timer = None
            except Exception:
                pass
            self.set_session_list_locked(False)

    def _session_show_more(self):
        self._session_visible_count += 10
        self.refresh_session_list()

    def _session_show_all(self):
        self._session_visible_count = 99999
        self.refresh_session_list()

    def _session_collapse(self):
        self._session_visible_count = 10
        self.refresh_session_list()

    def _toggle_session_list(self):
        pass

    def set_session_list_locked(self, locked: bool, reason: str = ""):
        """
        Lock or unlock the session list.
        On lock: rebuilds all items as grayed-out disabled widgets (mousePressEvent = noop).
        On unlock: rebuilds all items as normal clickable widgets.
        """
        self._session_switching_locked = locked
        self.refresh_session_list()

        # Disable the New Session button while locked
        if hasattr(self, '_new_session_btn'):
            self._new_session_btn.setEnabled(not locked)

        # Show a brief status bar hint when the user is blocked
        if locked and reason:
            self.status_label.setText(f"⏳ {reason}")
        elif not locked:
            current = self.status_label.text()
            if current.startswith("⏳"):
                self.status_label.setText("")

    def refresh_session_list(self):
        """Refresh the session list with search, sort and show-more pagination."""
        if not hasattr(self, 'session_list_layout'):
            return

        while self.session_list_layout.count() > 0:
            item = self.session_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sessions = self.controller.get_session_list()

        # ── Search filter ──────────────────────────────────────────────────
        query = ""
        if hasattr(self, '_session_search'):
            query = self._session_search.text().strip().lower()
        if query:
            sessions = [s for s in sessions if query in s['name'].lower()]

        # ── Sort ──────────────────────────────────────────────────────────
        sort_idx = getattr(self, '_session_sort_idx', 0)
        if sort_idx == 1:
            sessions = sorted(sessions, key=lambda s: s['name'].lower())
        elif sort_idx == 2:
            sessions = sorted(sessions, key=lambda s: s['name'].lower(), reverse=True)

        total = len(sessions)
        visible_count = getattr(self, '_session_visible_count', 10)

        if hasattr(self, '_session_count_lbl'):
            if query:
                self._session_count_lbl.setText(f"{total} result{'s' if total != 1 else ''}")
            else:
                self._session_count_lbl.setText(f"{total} session{'s' if total != 1 else ''}")

        shown = sessions[:visible_count]
        remaining = total - len(shown)

        is_locked = getattr(self, '_session_switching_locked', False)
        for session in shown:
            session_item = self._create_session_item(
                session['id'], session['name'], session['date'],
                is_active=(session['id'] == self.controller.current_session_id),
                disabled=is_locked
            )
            self.session_list_layout.addWidget(session_item)

        # Footer: show more / show all / collapse
        if hasattr(self, '_session_footer'):
            show_footer = total > 10
            self._session_footer.setVisible(show_footer)
            if show_footer:
                # "Show more" only visible if there are hidden items
                self._show_more_btn.setVisible(remaining > 0)
                self._show_more_btn.setText(f"Show {min(remaining, 10)} more")
                # "Show all" only if not already showing all
                self._show_all_btn.setVisible(remaining > 0)
                self._show_all_btn.setText(f"Show all ({total})")
                # "Collapse" only if showing more than 10
                self._collapse_list_btn.setVisible(visible_count > 10 or remaining == 0)
                # Also reset visible count on new search
                if query and self._session_visible_count > total:
                    self._session_visible_count = 10

        # A rename or session switch changes the idle (non-spinning) title.
        # Skip while the spinner is live so a rename mid-generation can't
        # stomp the current spinner frame.
        if not (self.title_spinner_timer and self.title_spinner_timer.isActive()):
            self._apply_window_title()

    def _create_session_item(self, session_id, session_name, creation_date, is_active=False, disabled=False):
        """Create a session list item widget. Pass disabled=True to gray it out and block clicks."""
        if disabled:
            # ── Locked / grayed-out appearance ────────────────────────────
            item_widget = QFrame()
            item_widget.setStyleSheet("""
                QFrame {
                    background-color: transparent;
                    border-radius: 6px;
                    padding: 8px;
                    margin: 2px 0px;
                    opacity: 0.4;
                }
            """)
            layout = QHBoxLayout(item_widget)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(8)

            content_layout = QVBoxLayout()
            content_layout.setSpacing(2)

            name_label = QLabel(session_name)
            name_label.setStyleSheet("color: #4A5060; font-size: 12px; font-weight: normal;")
            name_label.setWordWrap(True)
            content_layout.addWidget(name_label)

            date_label = QLabel(creation_date)
            date_label.setStyleSheet("color: #3A3F4A; font-size: 10px;")
            content_layout.addWidget(date_label)

            layout.addLayout(content_layout, 1)

            # Lock icon instead of delete button
            lock_lbl = QLabel("")
            lock_lbl.setFixedSize(24, 24)
            lock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lock_lbl.setStyleSheet("font-size: 11px; color: #3A3F4A;")
            layout.addWidget(lock_lbl)

            # Apply opacity effect to the whole item
            opacity_effect = QGraphicsOpacityEffect(item_widget)
            opacity_effect.setOpacity(0.4)
            item_widget.setGraphicsEffect(opacity_effect)

            # Blocked cursor + no-op click
            item_widget.setCursor(Qt.CursorShape.ForbiddenCursor)
            item_widget.mousePressEvent = lambda e: None

            return item_widget

        # ── Normal (enabled) appearance ────────────────────────────────────
        item_widget = QFrame()
        item_widget.setStyleSheet(f"""
            QFrame {{
                background-color: {"#21262D" if is_active else "transparent"};
                border-radius: 6px;
                padding: 8px;
                margin: 2px 0px;
            }}
            QFrame:hover {{
                background-color: {"#21262D" if is_active else "#1E2228"};
            }}
        """)

        layout = QHBoxLayout(item_widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        content_layout = QVBoxLayout()
        content_layout.setSpacing(2)

        name_label = QLabel(session_name)
        name_label.setStyleSheet(f"""
            color: {"#E8EAED" if is_active else "#9AA0A6"};
            font-size: 12px;
            font-weight: {"bold" if is_active else "normal"};
        """)
        name_label.setWordWrap(True)
        content_layout.addWidget(name_label)

        date_label = QLabel(creation_date)
        date_label.setStyleSheet("color: #5F5F5F; font-size: 10px;")
        content_layout.addWidget(date_label)

        layout.addLayout(content_layout, 1)

        from systema.ui.widgets.painted_icons import TrashButton
        delete_btn = TrashButton(24, tooltip="Delete session")
        delete_btn.clicked.connect(lambda: self._delete_session_clicked(session_id))
        layout.addWidget(delete_btn)

        item_widget.mousePressEvent = lambda e: self._load_session_clicked(session_id)
        item_widget.setCursor(Qt.CursorShape.PointingHandCursor)

        return item_widget

    def _load_session_clicked(self, session_id):
        """Load session when clicked — guarded against concurrent loads, AI generation, and work mode."""

        # ── Guard 1: already loading — items are rebuilt as disabled so this
        #    lambda should never fire, but flag check is the final safety net ─
        if getattr(self, '_session_switching_locked', False):
            return

        # ── Guard 2: AI is currently generating a response ─────────────────
        if getattr(self.controller, 'is_processing', False):
            self.status_label.setText("⏳ Cannot switch sessions while AI is responding…")
            QTimer.singleShot(2500, lambda: (
                self.status_label.setText("")
                if self.status_label.text().startswith("⏳") else None
            ))
            return

        # ── Guard 3: AI is in work / tool-use mode ─────────────────────────
        try:
            in_work = self.controller.ai.tool_manager.work.is_working
        except Exception:
            in_work = False
        if in_work:
            self.status_label.setText("⏳ Cannot switch sessions while AI is working…")
            QTimer.singleShot(2500, lambda: (
                self.status_label.setText("")
                if self.status_label.text().startswith("⏳") else None
            ))
            return

        # ── All clear: lock (rebuilds items as grayed disabled), load, unlock ─
        self.set_session_list_locked(True, "Loading session…")
        try:
            self.controller.load_session(session_id)
        finally:
            QTimer.singleShot(1500, lambda: self.set_session_list_locked(False))

    def _delete_session_clicked(self, session_id):
        """Delete session when delete button clicked"""
        if session_id == self.controller.current_session_id:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                'Delete Active Session',
                'Are you sure you want to delete the current session?\nA new session will be created.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.controller.delete_session(session_id)
        else:
            self.controller.delete_session(session_id)

    def _animated_sidebar_scroll_to(self, target_value: int):
        """Glide the sidebar scrollbar to target_value.

        Same rule as the chat viewport: the area's SmoothScroller owns the
        position, so a wheel notch during a programmatic glide blends into it
        instead of two animators stomping on one scrollbar.
        """
        if not hasattr(self, 'sidebar_scroll'):
            return

        sb = self.sidebar_scroll.verticalScrollBar()
        current = sb.value()
        if abs(current - target_value) < 4:
            return

        from systema.ui.widgets.smooth_scroll import scroller_for, SMOOTH_TIME_PROGRAM
        s = scroller_for(self.sidebar_scroll)
        if s is None:
            sb.setValue(int(target_value))
            return

        distance = abs(target_value - current)
        span = max(1, ANIM_SCROLL_MAX_MS - ANIM_SCROLL_MIN_MS)
        frac = min(1.0, max(0.0, (distance // 2 - ANIM_SCROLL_MIN_MS) / span))
        s.scroll_to(target_value,
                    smooth_time=SMOOTH_TIME_PROGRAM * (0.55 + 0.45 * frac))

    def _sidebar_inertia_tick(self):
        """Called ~70fps while inertia is active for sidebar."""
        if not hasattr(self, 'sidebar_scroll'):
            self._sidebar_inertia_timer.stop()
            return

        sb = self.sidebar_scroll.verticalScrollBar()
        self._sidebar_inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._sidebar_inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._sidebar_inertia_timer.stop()
            self._sidebar_inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._sidebar_inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    def _restyle_skills_block(self):
        """Re-theme the sidebar's Skills header + body from the LIVE palette.

        The rows inside are the SkillsSidebarSection's own job (apply_palette);
        this is the chrome around them, which used to be styled inline from
        local variables and so could never be reached again after construction.
        Safe to call before the block exists — the sidebar builds in pieces.
        """
        hdr = getattr(self, '_skills_hdr', None)
        body = getattr(self, '_skills_body', None)
        chrome = getattr(self, '_skills_chrome', None)
        if hdr is None or body is None or chrome is None:
            return
        try:
            t = self._t()
            from systema.ui.theme import resolve_palette
            p = resolve_palette(t)
            hdr.setStyleSheet(
                "QWidget { background: transparent; }"
                f"QWidget:hover {{ background: {t['surface']}; }}")
            body.setStyleSheet(f"background: {t['base']};")
            chrome['icon'].setStyleSheet(
                f"font-size: 14px; background: transparent; color: {p['muted']};")
            chrome['text'].setStyleSheet(
                f"font-size: 11px; color: {p['text']}; background: transparent;")
            # The badge carried the obsidian accent as a literal, so it stayed
            # blue under every other theme — the "background that isn't
            # following the theme" in the skills list.
            chrome['count'].setStyleSheet(
                f"background: {p['surface2']}; color: {p['accent']}; font-size: 9px;"
                " border-radius: 4px; padding: 1px 6px;")
            chrome['chevron'].setStyleSheet(
                f"color: {p['border']}; font-size: 14px; background: transparent;")
        except (RuntimeError, KeyError, AttributeError):
            pass

    def _build_sidebar(self):
        """Build the sidebar overlay (hero, nav rows, skills, session history).
        Extracted verbatim from init_ui (full-split pass, 2026-07-17)."""
        # ═══════════════════════════════════════════════════════════════════════
        # SIDEBAR — overlay (not in main_layout), parented to self.container.
        # Slides in/out via QPropertyAnimation on geometry.
        # ═══════════════════════════════════════════════════════════════════════
        self.sidebar = QFrame(self.container)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(self._sidebar_w)
        _tc = self._t()
        self.sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background-color: {_tc['base']};
                border-right: 1px solid {_tc['border']};
                border-top-left-radius: 12px;
                border-bottom-left-radius: 12px;
            }}
        """)
        self.sidebar.setGeometry(-self._sidebar_w, 0, self._sidebar_w, 650)
        self.sidebar.hide()

        sidebar_main_layout = QHBoxLayout(self.sidebar)
        sidebar_main_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_main_layout.setSpacing(0)

        sidebar_scroll_vbox = QWidget()
        sidebar_scroll_vbox.setStyleSheet("background: transparent;")
        sidebar_scroll_vbox_layout = QVBoxLayout(sidebar_scroll_vbox)
        sidebar_scroll_vbox_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_scroll_vbox_layout.setSpacing(0)
        sidebar_main_layout.addWidget(sidebar_scroll_vbox, stretch=1)

        self._sidebar_drag_handle = QFrame(self.sidebar)
        self._sidebar_drag_handle.setFixedWidth(6)
        self._sidebar_drag_handle.setStyleSheet("""
            QFrame { background-color: rgba(255,255,255,0.07); border-radius: 3px; }
            QFrame:hover { background-color: rgba(255,255,255,0.18); }
        """)
        self._sidebar_drag_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self._sidebar_drag_handle.setToolTip("Drag to resize sidebar")
        self._sidebar_drag_handle.mousePressEvent   = self._sidebar_resize_press
        self._sidebar_drag_handle.mouseMoveEvent    = self._sidebar_resize_move
        self._sidebar_drag_handle.mouseReleaseEvent = self._sidebar_resize_release
        sidebar_main_layout.addWidget(self._sidebar_drag_handle)

        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Same aimable wheel behaviour as the chat (shared implementation).
        from systema.ui.widgets.smooth_scroll import install_smooth_scroll
        install_smooth_scroll(self.sidebar_scroll)
        self.sidebar_scroll.setStyleSheet("""
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.13); border-radius: 5px; min-height: 24px; }
            QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.26); }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        self.sidebar_scroll.viewport().installEventFilter(self)

        sidebar_content = QWidget()
        sidebar_content.setObjectName("sidebarContent")
        # Transparent: the #sidebar frame paints the bg with its rounded left
        # corners — an opaque child here overpaints them square.
        sidebar_content.setStyleSheet("QWidget#sidebarContent { background-color: transparent; }")
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # ─────────────────────────────────────────────────────────────────────
        # HERO — avatar cluster + name + 3 action pills
        # ─────────────────────────────────────────────────────────────────────
        hero = QFrame()
        hero.setObjectName("sidebarHero")
        hero.setStyleSheet(f"""
            QFrame#sidebarHero {{
                background-color: transparent;
                border-bottom: 1px solid {_tc['border']};
            }}
        """)
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(16, 18, 16, 14)
        hero_lay.setSpacing(12)

        # Avatar cluster row
        av_name_row = QHBoxLayout()
        av_name_row.setSpacing(12)

        # Stacked avatars widget (bot big, user badge)
        av_stack = QWidget()
        av_stack.setFixedSize(56, 56)
        av_stack.setStyleSheet("background: transparent;")

        self.bot_avatar_display = QLabel(self.bot_avatar)
        self.bot_avatar_display.setParent(av_stack)
        self.bot_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bot_avatar_display.setGeometry(0, 0, 48, 48)
        self.bot_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 24px;
                background-color: #1a2a3a;
                border-radius: 24px;
                border: 2px solid transparent;
            }
        """)

        self.user_avatar_display = QLabel(self.user_avatar)
        self.user_avatar_display.setParent(av_stack)
        self.user_avatar_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.user_avatar_display.setGeometry(28, 30, 26, 26)
        self.user_avatar_display.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                background-color: #1a2a1a;
                border-radius: 13px;
                border: 2px solid {_tc['base']};
            }}
        """)
        av_stack.show()

        av_name_row.addWidget(av_stack)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        name_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        _assistant_display = self.controller.get_assistant_name() or "Systema Auxilium"
        self._hero_bot_name = QLabel(_assistant_display)
        self._hero_bot_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #E6EDF3; background: transparent;")
        name_col.addWidget(self._hero_bot_name)

        _user_display = self.controller.get_user_name() or "You"
        self._hero_user_name = QLabel(_user_display)
        self._hero_user_name.setStyleSheet("font-size: 10px; color: #555; background: transparent;")
        name_col.addWidget(self._hero_user_name)

        av_name_row.addLayout(name_col)
        av_name_row.addStretch()

        # Sidebar toggle DOCKED in the hero while the panel is open (the
        # floating toggle hides) — fixes the old toggle-over-pfp overlap.
        self._sidebar_dock_toggle = PanelToggleButton('close')
        self._sidebar_dock_toggle.setToolTip("Close sidebar")
        self._sidebar_dock_toggle.clicked.connect(self.toggle_sidebar)
        av_name_row.addWidget(self._sidebar_dock_toggle,
                              alignment=Qt.AlignmentFlag.AlignTop)

        hero_lay.addLayout(av_name_row)

        # 3 action pills — no borders, subtle text-only pill style
        pills_row = QHBoxLayout()
        pills_row.setSpacing(6)

        def _action_pill(icon, label, slot):
            btn = QPushButton(f"{icon}  {label}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_tc['elevated']};
                    border: none;
                    border-radius: 20px;
                    padding: 5px 10px;
                    font-size: 10px;
                    color: #8B949E;
                }}
                QPushButton:hover {{
                    background-color: rgba(88,166,255,0.12);
                    color: #58A6FF;
                }}
                QPushButton:pressed {{
                    background-color: rgba(88,166,255,0.18);
                }}
            """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(slot)
            return btn

        pills_row.addWidget(_action_pill("🖼", "Avatars", self._open_avatars_dialog))
        pills_row.addWidget(_action_pill("🏷", "Names", self._open_names_dialog))
        pills_row.addWidget(_action_pill("⚙️", "Instructions", self.open_instructions_window))
        hero_lay.addLayout(pills_row)

        sidebar_layout.addWidget(hero)

        # ─────────────────────────────────────────────────────────────────────
        # PERSONALIZE rows — no section header, just the rows
        # ─────────────────────────────────────────────────────────────────────
        def _sec_header(icon, text):
            lbl = QLabel(f"{icon}  {text}")
            lbl.setStyleSheet(f"""
                QLabel {{
                    font-size: 11px; font-weight: 700;
                    color: #9AA0A6;
                    background: transparent;
                    padding: 14px 16px 4px;
                }}
            """)
            return lbl

        def _side_row(icon, label, slot=None, badge_text=None, arrow=True):
            row = QWidget()
            row.setStyleSheet(f"""
                QWidget {{ background: transparent; }}
                QWidget:hover {{ background: {_tc['surface']}; }}
            """)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 8, 16, 8)
            rl.setSpacing(10)

            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(18)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_lbl.setStyleSheet("font-size: 14px; background: transparent; color: #8B949E;")
            rl.addWidget(icon_lbl)

            text_lbl = QLabel(label)
            text_lbl.setStyleSheet("font-size: 11px; color: #C9D1D9; background: transparent;")
            rl.addWidget(text_lbl, stretch=1)

            if badge_text:
                badge = QLabel(badge_text)
                badge.setStyleSheet("background: #21262D; color: #58A6FF; font-size: 9px; border-radius: 4px; padding: 1px 6px;")
                rl.addWidget(badge)

            if arrow:
                arr = QLabel("›")
                arr.setStyleSheet("color: #30363D; font-size: 14px; background: transparent;")
                rl.addWidget(arr)

            if slot:
                row.mousePressEvent = lambda e: slot()
            return row

        sidebar_layout.addWidget(_side_row("🧠", "Manage Memories", self._open_memory_window))
        sidebar_layout.addWidget(_side_row("⚙", "Manage Tasks", self._open_manage_tasks_window))

        # ── Skills — inline collapsible, matching _side_row style ────────────
        skill_manager = getattr(self.controller, 'skill_manager', None)
        if skill_manager:
            # Build a custom skills row that matches _side_row exactly
            skills_wrapper = QWidget()
            skills_wrapper.setStyleSheet("background: transparent;")
            sw_lay = QVBoxLayout(skills_wrapper)
            sw_lay.setContentsMargins(0, 0, 0, 0)
            sw_lay.setSpacing(0)

            # Header row — same layout as _side_row.
            # Every widget here is kept on `self` and styled by
            # _restyle_skills_block(), NOT inline: as locals styled once at
            # construction they were unreachable from apply_theme, so the block
            # kept whatever theme was active when the sidebar was built. The
            # count badge was worse still — hardcoded #21262D/#58A6FF, i.e. the
            # obsidian-blue accent, which stayed blue under every other theme.
            skills_hdr = QWidget()
            skills_hdr.setCursor(Qt.CursorShape.PointingHandCursor)
            sh_lay = QHBoxLayout(skills_hdr)
            sh_lay.setContentsMargins(16, 8, 16, 8)
            sh_lay.setSpacing(10)

            sk_icon = QLabel("⚡")
            sk_icon.setFixedWidth(18)
            sk_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sh_lay.addWidget(sk_icon)

            sk_text = QLabel("Skills")
            sh_lay.addWidget(sk_text, stretch=1)

            sk_count = QLabel("")
            sh_lay.addWidget(sk_count)

            sk_chevron = QLabel("›")
            sh_lay.addWidget(sk_chevron)

            sw_lay.addWidget(skills_hdr)

            # Body — hidden by default, contains the original SkillsSidebarSection internals
            skills_body = QWidget()
            skills_body.hide()

            self._skills_hdr = skills_hdr
            self._skills_body = skills_body
            self._skills_chrome = {'icon': sk_icon, 'text': sk_text,
                                   'count': sk_count, 'chevron': sk_chevron}
            self._restyle_skills_block()
            sb_lay = QVBoxLayout(skills_body)
            sb_lay.setContentsMargins(8, 4, 8, 8)
            sb_lay.setSpacing(4)
            from systema.ui.theme import resolve_palette
            self._skills_section = SkillsSidebarSection(
                skill_manager, palette=resolve_palette(self._t()))
            # Hide the SkillsSidebarSection's own header — we have our own
            self._skills_section.layout().itemAt(0).widget().hide()
            # Force-show the internal body (our outer skills_body handles hide/show)
            self._skills_section._body.show()
            self._skills_section._expanded = True
            sb_lay.addWidget(self._skills_section)
            sw_lay.addWidget(skills_body)

            # Refresh count label
            def _refresh_skill_count():
                try:
                    skills = skill_manager.get_skills()
                    loaded = sum(1 for s in skills if s.get('is_loaded'))
                    sk_count.setText(f"{len(skills)} · {loaded} loaded" if skills else "none")
                except RuntimeError:
                    # sk_count label was deleted (sidebar rebuilt) — disconnect signals
                    try:
                        skill_manager.skills_changed.disconnect(_refresh_skill_count)
                        skill_manager.loaded_skills_changed.disconnect(_refresh_skill_count)
                    except Exception:
                        pass
            _refresh_skill_count()
            skill_manager.skills_changed.connect(_refresh_skill_count)
            skill_manager.loaded_skills_changed.connect(_refresh_skill_count)

            # Toggle expand
            _sk_open = [False]
            def _toggle_skills():
                _sk_open[0] = not _sk_open[0]
                skills_body.setVisible(_sk_open[0])
                sk_chevron.setText("▼" if _sk_open[0] else "›")
                if _sk_open[0]:
                    self._skills_section.refresh()
            skills_hdr.mousePressEvent = lambda e: _toggle_skills()

            sidebar_layout.addWidget(skills_wrapper)
        else:
            sidebar_layout.addWidget(_side_row("⚡", "Skills", None, badge_text="unavailable", arrow=False))

        # ─────────────────────────────────────────────────────────────────────
        # SESSION HISTORY section
        # ─────────────────────────────────────────────────────────────────────
        sidebar_layout.addWidget(_sec_header("📁", "Session History"))

        # Search + sort row — compact, matches bg
        search_sort_row = QHBoxLayout()
        search_sort_row.setContentsMargins(16, 2, 16, 6)
        search_sort_row.setSpacing(6)

        self._session_search = QLineEdit()
        self._session_search.setPlaceholderText("Search sessions…")
        self._session_search.setFixedHeight(28)
        self._session_search.setStyleSheet(f"""
            QLineEdit {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                padding: 0 8px;
                font-size: 10px;
                color: #8B949E;
            }}
            QLineEdit:focus {{
                border-color: rgba(88,166,255,0.45);
                color: #E6EDF3;
            }}
        """)
        self._session_search.textChanged.connect(self.refresh_session_list)
        search_sort_row.addWidget(self._session_search, stretch=1)

        # Cycling sort button
        self._session_sort_modes = ["Time", "A→Z", "Z→A"]
        self._session_sort_idx   = 0
        self._session_sort_btn = QPushButton("Time")
        self._session_sort_btn.setFixedHeight(28)
        self._session_sort_btn.setFixedWidth(56)
        self._session_sort_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                font-size: 9px;
                color: #8B949E;
                padding: 0 4px;
            }}
            QPushButton:hover {{ border-color: rgba(88,166,255,0.35); color: #E6EDF3; }}
        """)
        def _cycle_sort():
            self._session_sort_idx = (self._session_sort_idx + 1) % len(self._session_sort_modes)
            # Icon overhaul: labels only — the A→Z/Z→A text already carries the
            # direction; the ↕↑↓ glyph prefix was redundant chrome.
            self._session_sort_btn.setText(
                self._session_sort_modes[self._session_sort_idx])
            self.refresh_session_list()
        self._session_sort_btn.clicked.connect(_cycle_sort)
        search_sort_row.addWidget(self._session_sort_btn)

        sidebar_layout.addLayout(search_sort_row)

        # New session button — accent, full width
        self._new_session_btn = QPushButton("New Session")
        new_session_btn = self._new_session_btn
        new_session_btn.setFixedHeight(32)
        new_session_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(88,166,255,0.10);
                border: 1px solid rgba(88,166,255,0.22);
                border-radius: 7px;
                font-size: 11px;
                font-weight: 500;
                color: #58A6FF;
                margin: 0 16px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(88,166,255,0.18);
                border-color: rgba(88,166,255,0.4);
            }
        """)
        new_session_btn.clicked.connect(lambda: self.controller.create_new_session())
        new_session_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sidebar_layout.addWidget(new_session_btn)

        # Session count label (updated by refresh)
        self._session_count_lbl = QLabel("")
        self._session_count_lbl.setStyleSheet("color: #30363D; font-size: 9px; background: transparent; padding: 2px 16px 0;")
        sidebar_layout.addWidget(self._session_count_lbl)

        # Session list container
        self._session_list_body = QWidget()
        self._session_list_body.setStyleSheet("background: transparent;")
        slb_layout = QVBoxLayout(self._session_list_body)
        slb_layout.setContentsMargins(0, 0, 0, 0)
        slb_layout.setSpacing(0)

        session_list_container = QWidget()
        session_list_container.setStyleSheet("background: transparent;")
        self.session_list_layout = QVBoxLayout(session_list_container)
        self.session_list_layout.setContentsMargins(0, 0, 0, 0)
        self.session_list_layout.setSpacing(0)
        slb_layout.addWidget(session_list_container)
        sidebar_layout.addWidget(self._session_list_body)

        # Show more / Show all / Collapse buttons (hidden until needed)
        session_footer = QWidget()
        session_footer.setStyleSheet("background: transparent;")
        sf_lay = QHBoxLayout(session_footer)
        sf_lay.setContentsMargins(16, 2, 16, 6)
        sf_lay.setSpacing(8)

        _footer_btn_ss = """
            QPushButton { background: transparent; border: none;
                color: #555; font-size: 10px; padding: 0; }
            QPushButton:hover { color: #8B949E; }
        """
        self._show_more_btn = QPushButton("Show more")
        self._show_more_btn.setStyleSheet(_footer_btn_ss)
        self._show_more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_more_btn.clicked.connect(self._session_show_more)

        self._show_all_btn = QPushButton("Show all")
        self._show_all_btn.setStyleSheet(_footer_btn_ss)
        self._show_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._show_all_btn.clicked.connect(self._session_show_all)

        self._collapse_list_btn = QPushButton("Collapse")
        self._collapse_list_btn.setStyleSheet(_footer_btn_ss)
        self._collapse_list_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_list_btn.clicked.connect(self._session_collapse)

        sep_dot = QLabel("·")
        sep_dot.setStyleSheet("color: #333; background: transparent; font-size: 10px;")
        sf_lay.addWidget(self._show_more_btn)
        sf_lay.addWidget(sep_dot)
        sf_lay.addWidget(self._show_all_btn)
        sf_lay.addStretch()
        sf_lay.addWidget(self._collapse_list_btn)

        session_footer.hide()
        self._session_footer = session_footer
        sidebar_layout.addWidget(session_footer)

        # State for pagination
        self._session_visible_count = 10
        self._session_list_expanded = True
        self._session_list_auto_collapsed = False

        QTimer.singleShot(100, self.refresh_session_list)

        sidebar_layout.addStretch()
        self.sidebar_scroll.setWidget(sidebar_content)
        sidebar_scroll_vbox_layout.addWidget(self.sidebar_scroll)

        # NOTE: sidebar is NOT added to main_layout — it is an overlay.

