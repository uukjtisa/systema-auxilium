"""
ui/chat_window.py
Chat Window - Modern conversation interface with Voice Support
Features:
- Voice input/output toggle
- Real-time voice status indicators
- Waveform visualization (optional)
- Voice device selection
- Automatic TTS for AI responses when voice is active
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QFrame,
    QScrollArea,
    QApplication)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve
from systema.ui.base_window import BaseWindow
from systema.common.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

from systema.ui.chat.rendering import RenderingMixin
from systema.ui.chat.theming import ThemingMixin
from systema.ui.chat.constants import (
    ANIM_INERTIA_FRICTION,
    ANIM_INERTIA_INTERVAL_MS,
    ANIM_INERTIA_MIN_VELOCITY,
    ANIM_SCROLL_MAX_MS,
    ANIM_SCROLL_MIN_MS,
    ANIM_WINDOW_FADE_IN_MS,
    SIDEBAR_DEFAULT_W)
from systema.ui.chat.sidebar import SidebarMixin
from systema.ui.chat.input_dock import InputDockMixin, InlineStatus
from systema.ui.chat.bubbles import BubblesMixin, make_circular_pixmap, _TypingDots
from systema.ui.chat.event_cards import EventCardsMixin
from systema.ui.chat.image_bubbles import ImageBubblesMixin
from systema.ui.chat.commands import SlashCommandsMixin
from systema.ui.chat.window_controls import WindowControlsMixin

import os
import sys


# (animation/sidebar constants moved to systema/ui/chat/constants.py)

# ═══════════════════════════════════════════════════════════════════════════════

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
from systema import APP_ROOT as _APP_ROOT
from systema.common import app_config as _app_config
# ─────────────────────────────────────────────────────────────────────────────



class ChatWindow(BaseWindow, RenderingMixin, ThemingMixin,
                 SidebarMixin, InputDockMixin, BubblesMixin, EventCardsMixin,
                 ImageBubblesMixin, SlashCommandsMixin, WindowControlsMixin):
    """Modern chat window with AI conversation"""

    # Smooth antialiased corners (no 1-bit mask): every corner-touching child
    # carries a matching 12px radius (scroll area, sidebar, bottom fade) —
    # holds in glass mode too, since the rgba backdrop sits on the rounded
    # viewport and the DWM acrylic experiment is retired.
    _smooth_corners = True

    voice_playback_signal = pyqtSignal()  # Signal for thread-safe UI updates

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.thinking_timer = None
        self.thinking_dots = 0
        self.title_spinner_timer = None
        self.title_spinner_frame = 0
        self.thinking_label_shown = False
        self._thinking_bubble_widget = None
        self._thinking_bubble_label = None
        self._thinking_bubble_timer = None
        self._thinking_bubble_dots = 0
        self._thinking_bubble_group = None
        self.sidebar_visible = False
        # Sidebar resize state (drag handle on right edge)
        self._sidebar_w = SIDEBAR_DEFAULT_W        # current width (persists across open/close)
        self._sidebar_resize_active = False        # True while user is dragging
        self._sidebar_resize_start_x = 0          # global X at drag start
        self._sidebar_resize_start_w = 0          # sidebar width at drag start

        # ── Smooth scroll state (main chat) ───────────────────────────────
        # The chat viewport's position is owned by its SmoothScroller (see
        # _chat_scroller); there is no separate scroll animation object any
        # more, because two animators over one scrollbar fought each other.
        self._inertia_velocity = 0.0
        self._inertia_timer = QTimer()
        self._inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._inertia_timer.timeout.connect(self._inertia_tick)
        self._user_scrolling = False   # inertia mechanics only (no longer gates auto-scroll)
        # Adaptive sticky-bottom: True while the view is pinned to the newest
        # content. Released by scrolling away, re-engaged by scrolling back —
        # the single gate for every auto-scroll (2026-07 redesign).
        self._stick_to_bottom = True

        # ── Smooth scroll state (sidebar) ─────────────────────────────────
        # (sidebar position is likewise owned by its SmoothScroller)
        self._sidebar_inertia_velocity = 0.0
        self._sidebar_inertia_timer = QTimer()
        self._sidebar_inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._sidebar_inertia_timer.timeout.connect(self._sidebar_inertia_tick)

        # ── Smooth scroll state (input field) ─────────────────────────────
        self._input_inertia_velocity = 0.0
        self._input_inertia_timer = QTimer()
        self._input_inertia_timer.setInterval(ANIM_INERTIA_INTERVAL_MS)
        self._input_inertia_timer.timeout.connect(self._input_inertia_tick)

        # ── Animation state ────────────────────────────────────────────────────
        self._sidebar_anim = None
        self._open_anim = None

        # Voice state
        self.voice_enabled = False

        # NEW: Pending message buffer for voice mode
        self.pending_voice_message = None
        self.waiting_for_playback = False

        # NEW: Connect voice playback signal
        self.voice_playback_signal.connect(self._handle_voice_playback_on_main_thread)


        # Session switching lock — prevents spamming, blocks during AI generation / work mode
        self._session_switching_locked = False

        # Image attachment (multi-image + persistent pinned images)
        self.attached_image = None  # backward compat — last added image
        # Images no longer live in UI state. They are ImageRefs on the history
        # entry that owns them (see ui/chat/image_bubbles.py) — which is what
        # makes them survive a reload and a work-mode continuation. These two
        # lists remain only so any straggling `getattr(chat, 'pinned_images')`
        # reader sees an empty list instead of raising.
        self.attached_images = []
        self.pinned_images = []

        # Interrupt tracking
        self.last_sent_message = None  # Track last message for interrupt
        self.last_user_message_widget = None  # Track last user message widget for removal

        # MESSAGE CONTROL: Track all messages for edit/delete/rewind
        self.message_widgets = []  # List of {widget, role, content, history_index}
        # Current assistant TURN GROUP (claude.ai-style merged work turn) —
        # None between turns; see BubblesMixin._ensure_ai_turn_group.
        self._ai_turn_group = None
        # Live-streaming state: the throwaway text segment deltas paint into,
        # and the in-flight Thinking card. `_stream_think_pending` holds
        # reasoning deltas that have arrived but carry no content yet — the
        # card is not built until one does (see on_stream_thinking).
        self._stream_seg = None
        self._stream_think_card = None
        self._stream_think_pending = ''
        self._stream_active = False
        # ONE Thinking card per merged assistant bubble (reset per turn shell).
        self._turn_thinking_card = None
        self._turn_thinking_group = None
        self._skills_ui_card_widget = None   # Single per-session skills card (only one allowed)
        self._skills_ui_card_timer = None    # 500ms live-sync timer for that card

        # Window chrome state
        self._init_chrome_state()

        # Avatar settings — 'chat_window_config' section of settings.json
        self.load_config()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Window settings - BORDERLESS (Spotify-style)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("New Session")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(800, 500)  # Minimum size
        self.resize(1000, 650)  # Default size (but resizable!)
        # Main container for rounded corners
        self.container = QWidget()
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #161B22;
                border-radius: 12px;
            }
            QWidget {
                color: #E8EAED;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
        """)
        self.container.setObjectName("container")
        self.container.setCursor(Qt.CursorShape.ArrowCursor)
        # NO setAcceptDrops here: Qt delivers a drag to the FIRST ancestor with
        # acceptDrops=True and stops there — a plain QWidget acceptor with no
        # dragEnterEvent silently ignores the drag (forbidden cursor) instead of
        # letting it fall through to ChatWindow's real handlers below.

        self.init_ui()

        # Wrap everything in container for rounded corners
        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        # Apply rounded mask
        self.apply_rounded_mask()

        self.create_resize_handles()

        # Warn about any already-loaded skills from previous session (delayed so chat renders first)
        QTimer.singleShot(800, self.warn_loaded_skills_if_any)

    def load_config(self):
        try:
            config = _app_config.load_section('chat_window_config')
            self.bot_avatar = config.get('bot_avatar', '🤖')
            self.user_avatar = config.get('user_avatar', '👤')
            self.chat_zoom = float(config.get('chat_zoom', 1.0))
            self._bot_avatar_image_path  = config.get('bot_avatar_image_path', '')
            self._user_avatar_image_path = config.get('user_avatar_image_path', '')
            self._bot_avatar_size  = int(config.get('bot_avatar_size', 32))
            self._user_avatar_size = int(config.get('user_avatar_size', 32))
            self._avatar_size_uniform = bool(config.get('avatar_size_uniform', False))
            self._input_box_width = int(config.get('input_box_geometry', 640))
            for _t in ('bot', 'user'):
                setattr(self, f'_{_t}_avatar_zoom', float(config.get(f'{_t}_avatar_zoom', 1.0)))
                setattr(self, f'_{_t}_avatar_ox', float(config.get(f'{_t}_avatar_ox', 0.5)))
                setattr(self, f'_{_t}_avatar_oy', float(config.get(f'{_t}_avatar_oy', 0.5)))
        except Exception:
            self.bot_avatar = '🤖'
            self.user_avatar = '👤'
            self.chat_zoom = 1.0
            self._bot_avatar_image_path  = ''
            self._user_avatar_image_path = ''
            self._bot_avatar_size  = 32
            self._user_avatar_size = 32
            self._avatar_size_uniform = False
            self._input_box_width = 640
        if not hasattr(self, '_input_box_width'):
            self._input_box_width = 640
        # Avatar crop transform defaults (unwritten configs / load failures).
        for _t in ('bot', 'user'):
            if not hasattr(self, f'_{_t}_avatar_zoom'):
                setattr(self, f'_{_t}_avatar_zoom', 1.0)
                setattr(self, f'_{_t}_avatar_ox', 0.5)
                setattr(self, f'_{_t}_avatar_oy', 0.5)
        self._bot_avatar_pixmap  = None
        self._user_avatar_pixmap = None
        # Clamp zoom to safe range
        self.chat_zoom = max(0.6, min(1.8, self.chat_zoom))
        self._glass_enabled = False
        self._glass_opacity = 0.75
        QTimer.singleShot(100, self.load_window_geometry)
        # Restore saved image avatars after UI is built
        QTimer.singleShot(200, self._restore_avatar_images)

    def _avatar_crop_square(self, pixmap, zoom, ox, oy):
        """Crop `pixmap` to a square using the same zoom + centre-fraction math
        as the avatar editor — the ONE crop implementation shared by the editor
        (Apply) and restore-on-startup, so a reloaded avatar is framed exactly
        as saved. `zoom`>=1, `ox`/`oy` in 0..1 (centre of the visible window)."""
        try:
            z = max(1.0, float(zoom))
            src_w = int(pixmap.width() / z)
            src_h = int(pixmap.height() / z)
            src_x = int((pixmap.width()  - src_w) * ox)
            src_y = int((pixmap.height() - src_h) * oy)
            src_x = max(0, min(src_x, pixmap.width()  - src_w))
            src_y = max(0, min(src_y, pixmap.height() - src_h))
            crop = pixmap.copy(src_x, src_y, src_w, src_h)
            side = min(crop.width(), crop.height())
            if side <= 0:
                return pixmap
            return crop.copy((crop.width() - side) // 2,
                             (crop.height() - side) // 2, side, side)
        except Exception:
            return pixmap

    def _restore_avatar_images(self):
        """Load saved avatar image paths back into pixmaps after UI is ready.

        The stored master (`_bot/_user_avatar_pixmap`) is a FULL-RESOLUTION
        center-cropped square — every display site clips its own circle at the
        target size via make_circular_pixmap, so edges stay crisp at any size."""
        from PyQt6.QtGui import QPixmap

        def _load_square(path, target):
            px = QPixmap(path)
            if px.isNull():
                return None
            # Re-apply the SAVED crop transform (zoom + centre) so the framing
            # the user chose survives restart, instead of a naive centre crop
            # that dropped their zoom + horizontal position.
            return self._avatar_crop_square(
                px,
                getattr(self, f'_{target}_avatar_zoom', 1.0),
                getattr(self, f'_{target}_avatar_ox', 0.5),
                getattr(self, f'_{target}_avatar_oy', 0.5))

        if self._bot_avatar_image_path:
            pm = _load_square(self._bot_avatar_image_path, 'bot')
            if pm:
                self._bot_avatar_pixmap = pm
                self.bot_avatar = ''
                if hasattr(self, 'bot_avatar_display'):
                    # 44, not 48: the label's 2px border shrinks its content
                    # rect — a full-size pixmap gets edge-clipped (ragged rim).
                    self.bot_avatar_display.setPixmap(make_circular_pixmap(pm, 44))
                    self.bot_avatar_display.setText('')

        if self._user_avatar_image_path:
            pm = _load_square(self._user_avatar_image_path, 'user')
            if pm:
                self._user_avatar_pixmap = pm
                self.user_avatar = ''
                if hasattr(self, 'user_avatar_display'):
                    # 22, not 26 — same 2px-border content-rect clipping fix
                    self.user_avatar_display.setPixmap(make_circular_pixmap(pm, 22))
                    self.user_avatar_display.setText('')

    def save_config(self):
        try:
            config = _app_config.load_section('chat_window_config')
            config['bot_avatar']  = self.bot_avatar
            config['user_avatar'] = self.user_avatar
            config['chat_zoom']   = self.chat_zoom
            config['bot_avatar_image_path']  = getattr(self, '_bot_avatar_image_path', '')
            config['user_avatar_image_path'] = getattr(self, '_user_avatar_image_path', '')
            config['bot_avatar_size']  = getattr(self, '_bot_avatar_size', 32)
            config['user_avatar_size'] = getattr(self, '_user_avatar_size', 32)
            config['avatar_size_uniform'] = getattr(self, '_avatar_size_uniform', False)
            config['input_box_geometry'] = int(getattr(self, '_input_box_width', 640))
            # Avatar crop transform (zoom + centre fraction) — persisted so the
            # framing survives restart instead of reverting to a naive centre
            # crop (was: only the source path was saved, so zoom + X were lost).
            for _t in ('bot', 'user'):
                config[f'{_t}_avatar_zoom'] = float(getattr(self, f'_{_t}_avatar_zoom', 1.0))
                config[f'{_t}_avatar_ox']   = float(getattr(self, f'_{_t}_avatar_ox', 0.5))
                config[f'{_t}_avatar_oy']   = float(getattr(self, f'_{_t}_avatar_oy', 0.5))
            _app_config.save_section('chat_window_config', config)
        except Exception as e:
            log.error(f"[ChatWindow.save_config] Error saving config: {e}")

    def init_ui(self):
        """Initialize modern UI"""
        self.setAcceptDrops(True)
        main_layout = QHBoxLayout(self.container)  # Changed: use container
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._build_sidebar()

        # === MAIN CHAT AREA ===
        chat_container = QWidget()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # NO HEADER BAR — the title bar is gone (2026-07 redesign). Floating
        # overlay controls are built in _build_window_controls(); the top
        # DRAG_STRIP_H px of the chat viewport act as the drag surface
        # (handled in eventFilter).
        self.skills_panel = None   # kept as attribute for any legacy references

        # Chat display with scroll — the scrollbar is fully hidden (2026-07
        # redesign): the MessageNavigator overlay replaces it visually, while
        # the scrollbar object keeps driving wheel/inertia/animated scrolls.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Never pan horizontally: fixed-width bubbles from a wider window clip
        # for a beat until the debounced reflow re-fits them (resizeEvent).
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Transparent — the CONTAINER paints the backdrop (a scroll-area
        # background renders square on the viewport, breaking the window's
        # rounded corners; apply_theme owns the real styling).
        scroll_area.setStyleSheet("""
                    QScrollArea {
                        border: none;
                        background: transparent;
                    }
                    QScrollBar:vertical { width: 0px; }
                """)

        # Chat messages container
        self.chat_widget = QWidget()
        self.chat_widget.setStyleSheet("""
            QWidget {
                background-color: #161B22;
            }
        """)
        # NO setAcceptDrops: it would intercept file drags over the whole chat
        # surface and ignore them (no handler here) — drags must fall through to
        # ChatWindow.dragEnterEvent/dropEvent (the window-level handlers).
        self.chat_layout = QVBoxLayout(self.chat_widget)
        # Top margin 50: with the title bar gone the first message needs
        # clearance under the floating toggle / minimize / close buttons.
        self.chat_layout.setContentsMargins(0, 50, 0, 16)
        self.chat_layout.setSpacing(0)
        self.chat_layout.addStretch()

        self.chat_scroll_area = scroll_area
        scroll_area.setWidget(self.chat_widget)
        chat_layout.addWidget(scroll_area)

        # Sticky-bottom wiring: recompute the pin from the live position, and
        # re-pin instantly whenever streaming content grows the scroll range.
        _sb = scroll_area.verticalScrollBar()
        _sb.valueChanged.connect(self._update_stick_to_bottom)
        _sb.rangeChanged.connect(self._on_scroll_range_changed)

        # Drag-strip / press handling still rides on this filter…
        scroll_area.viewport().installEventFilter(self)
        # …while the WHEEL is owned by the shared smooth scroller.
        self._install_smooth_scrolling(scroll_area)

        self._build_input_dock(chat_container)

        # Message navigator overlay (replaces the scrollbar) — right-centre of
        # the chat area; rebuilt via _refresh_msg_navigator whenever the set of
        # user messages changes.
        from systema.ui.chat.navigator import MessageNavigator
        self._msg_navigator = MessageNavigator(self, chat_container)

        main_layout.addWidget(chat_container)

        # Floating overlay chrome (sidebar toggle + minimize/close) — built
        # last so it sits above chat_container in Z-order.
        self._build_window_controls()

        # Load personalization
        self.load_personalization()

        # Apply glass background from saved settings (deferred so widgets are ready)
        QTimer.singleShot(200, self._apply_glass_from_settings)

        # Greeting banner — deferred past theme init so _current_theme_key is
        # set. It carries the elevated-privileges note as its own subtitle now,
        # so there is no second timer for that.
        QTimer.singleShot(210, self._show_welcome_message)

    def _show_welcome_message(self):
        """Open the startup session with the greeting banner.

        Was a plain system line built from three hardcoded time buckets. It is
        now the same banner a new session gets (four buckets, a rotating pool
        of phrasings, painted glyph) so "fresh session" looks identical however
        you arrived at one.

        Suppressed when the transcript already has real messages: a restored
        session must not get a greeting stapled to the BOTTOM of its history.
        """
        if any(m.get('role') in ('user', 'assistant')
               for m in getattr(self, 'message_widgets', [])):
            return
        self.add_greeting_banner()

    # ═══════════════════════════════════════════════════════════
    # ANIMATION METHODS
    # ═══════════════════════════════════════════════════════════

    def showEvent(self, event):
        """Fade the window in every time it becomes visible."""
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        self._open_anim = QPropertyAnimation(self, b"windowOpacity")
        self._open_anim.setDuration(ANIM_WINDOW_FADE_IN_MS)
        self._open_anim.setStartValue(0.0)
        self._open_anim.setEndValue(1.0)
        self._open_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open_anim.start()
        # Grab focus on open so the input is usable immediately — X11 won't focus a
        # frameless / always-on-top window on its own. Deferred so it lands after
        # the window is actually mapped. Preserves the always-on-top intent.
        # Non-Windows only: Windows focuses on click natively and this used to
        # disturb its native behavior, so there we keep the original fade-in-only.
        if sys.platform != "win32":
            def _grab_focus():
                try:
                    self.raise_()
                    self.activateWindow()
                    self.input_field.text_input.setFocus()
                except Exception:
                    pass
            QTimer.singleShot(0, _grab_focus)


    # ── Sidebar right-edge resize ──────────────────────────────────────────────
    # These are distinct from the code-block handles (handle_vertical_press etc.)



    # ── Responsive message bubbles ────────────────────────────────────────────



    def open_instructions_window(self):
        """Open custom instructions window with personality presets and persona block tools."""
        import json as _json
        from pathlib import Path as _Path
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QHBoxLayout,
            QPushButton,
            QTextEdit,
            QLabel,
            QScrollArea,
            QWidget,
            QFrame,
            QInputDialog,
            QSplitter)

        PRESETS_FILE = _Path(_APP_ROOT) / "data" / "instruction_presets.json"
        PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)

        _tc = self._t()

        # ── Shared styles ─────────────────────────────────────────────────────
        _DLG_SS = f"""
            QDialog {{ background-color: {_tc['base']}; color: #E6EDF3;
                       font-family: 'Segoe UI', system-ui, sans-serif; }}
            QLabel {{ background: transparent; color: #E6EDF3; }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: {_tc['elevated']}; border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """
        def _chip(text, accent=False):
            btn = QPushButton(text)
            if accent:
                btn.setStyleSheet("""
                    QPushButton { background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                        border-radius: 5px; padding: 4px 10px; font-size: 10px; color: #58A6FF; }
                    QPushButton:hover { background: rgba(88,166,255,0.24); border-color: #58A6FF; }
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{ background: {_tc['elevated']}; border: 1px solid {_tc['border']};
                        border-radius: 5px; padding: 4px 10px; font-size: 10px; color: #8B949E; }}
                    QPushButton:hover {{ background: rgba(88,166,255,0.08); border-color: rgba(88,166,255,0.35); color: #E6EDF3; }}
                """)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            return btn

        dialog = QDialog(self)
        dialog.setWindowTitle("Custom Instructions")
        dialog.setModal(True)
        dialog.setMinimumSize(680, 560)
        dialog.resize(780, 640)
        dialog.setStyleSheet(_DLG_SS)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QFrame()
        hdr.setFixedHeight(50)
        hdr.setStyleSheet(f"QFrame {{ background: {_tc['surface']}; border-bottom: 1px solid {_tc['border']}; }}")
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(20, 0, 20, 0)
        t_lbl = QLabel("Custom Instructions")
        t_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #E6EDF3;")
        hdr_l.addWidget(t_lbl)
        hdr_l.addStretch()
        root.addWidget(hdr)

        # Main split: left = tools panel, right = editor
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter { background: transparent; } QSplitter::handle { background: transparent; }")

        # ── LEFT PANEL: presets + inserts ────────────────────────────────────
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(230)
        left_scroll.setStyleSheet(f"QScrollArea {{ background: {_tc['base']}; border: none; border-right: 1px solid {_tc['border']}; }}")
        left_w = QWidget()
        left_w.setStyleSheet(f"QWidget {{ background: {_tc['base']}; }}")
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(12, 14, 12, 14)
        left_lay.setSpacing(14)

        def _section_hdr(txt):
            l = QLabel(txt)
            l.setStyleSheet("color: #8B949E; font-size: 9px; font-weight: 700; letter-spacing: 1px; background: transparent;")
            return l

        # Personality presets
        left_lay.addWidget(_section_hdr("PERSONALITY PRESETS"))
        PERSONALITY_PRESETS = [
            ("🥰 Cute & bubbly",
             "Be cute, enthusiastic, and bubbly! Use emojis frequently 🌟✨ Use lots of exclamation marks! "
             "Be warm, encouraging, and playful. Express genuine excitement about helping!"),
            ("💅 Girly & sassy",
             "Be confident, witty, and a little sassy. Use casual, fun language. Don't be afraid to add "
             "personality and light humor. Think of yourself as a smart, stylish best friend."),
            ("💼 Male professional",
             "Be precise, professional, and direct. Favor concise answers over lengthy explanations. "
             "Use formal language. Prioritize efficiency and clarity in every response."),
            ("🧑‍🎓 Patient teacher",
             "Explain everything step-by-step as if teaching a beginner. Never assume prior knowledge. "
             "Use analogies and simple language. Encourage questions and be endlessly patient."),
            ("🔬 Technical expert",
             "Be highly technical and detailed. Assume strong technical background. Use precise terminology. "
             "Don't over-simplify. Provide depth, nuance, and cite edge-cases when relevant."),
            ("😂 Witty & humorous",
             "Be funny and keep things light. Add clever humor and witty observations naturally. "
             "Don't force jokes, but don't miss a good opportunity either. Still be helpful — just fun about it."),
            ("🧘 Calm & thoughtful",
             "Be calm, measured, and thoughtful in all responses. Never rush. Take a reflective tone. "
             "Acknowledge complexity, be empathetic, and never be dismissive."),
            ("⚡ Speed mode",
             "Ultra-concise. No fluff, no filler, no pleasantries. Answer in the fewest possible words. "
             "Bullet points only when needed. Prioritize speed and density of information."),
        ]
        for label, text in PERSONALITY_PRESETS:
            btn = _chip(label)
            btn.setToolTip("Click to insert into editor")
            btn.clicked.connect(lambda _, t=text: _insert(t))
            left_lay.addWidget(btn)

        # Persona block insert
        left_lay.addSpacing(6)
        left_lay.addWidget(_section_hdr("PERSONA TEMPLATE"))
        persona_btn = _chip("📝 Insert Persona Block", accent=True)
        PERSONA_BLOCK = (
            "## Assistant Persona\n"
            "Personality: [Describe personality traits]\n"
            "Speaking style: [How does the assistant talk?]\n"
            "Tone: [Formal / Casual / Playful / Professional]\n"
            "Special behaviors: [Any quirks, habits, rules]\n"
            "Background: [Optional backstory or context]\n"
            "\n## Boundaries\n"
            "- Always [rule 1]\n"
            "- Never [rule 2]\n"
        )
        persona_btn.clicked.connect(lambda: _insert(PERSONA_BLOCK))
        left_lay.addWidget(persona_btn)

        # Custom saved presets
        left_lay.addSpacing(6)
        left_lay.addWidget(_section_hdr("SAVED PRESETS"))

        saved_presets_container = QWidget()
        saved_presets_container.setStyleSheet("background: transparent;")
        self._saved_presets_layout = QVBoxLayout(saved_presets_container)
        self._saved_presets_layout.setContentsMargins(0, 0, 0, 0)
        self._saved_presets_layout.setSpacing(4)

        def _load_saved_presets():
            # Clear
            while self._saved_presets_layout.count():
                item = self._saved_presets_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            try:
                if PRESETS_FILE.exists():
                    presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8'))
                else:
                    presets = {}
            except Exception:
                presets = {}

            for name, content in presets.items():
                row = QWidget()
                row.setStyleSheet("background: transparent;")
                row_l = QHBoxLayout(row)
                row_l.setContentsMargins(0, 0, 0, 0)
                row_l.setSpacing(4)
                load_btn = _chip(name)
                load_btn.clicked.connect(lambda _, c=content: _insert(c))
                row_l.addWidget(load_btn, stretch=1)
                from systema.ui.widgets.painted_icons import CloseButton as _XBtn
                del_btn = _XBtn(22, tooltip="Delete preset", pill=False)
                del_btn.clicked.connect(lambda _, n=name: _delete_preset(n))
                row_l.addWidget(del_btn)
                self._saved_presets_layout.addWidget(row)

            if not presets:
                empty = QLabel("No saved presets yet")
                empty.setStyleSheet("color: #30363D; font-size: 10px; background: transparent;")
                self._saved_presets_layout.addWidget(empty)

        def _delete_preset(name):
            try:
                presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8')) if PRESETS_FILE.exists() else {}
                presets.pop(name, None)
                PRESETS_FILE.write_text(_json.dumps(presets, indent=2, ensure_ascii=False), encoding='utf-8')
                _load_saved_presets()
            except Exception as e:
                log.error(f"[instructions] delete preset error: {e}")

        def _save_preset():
            text = text_edit.toPlainText().strip()
            if not text:
                return
            name, ok = QInputDialog.getText(dialog, "Save Preset", "Preset name:")
            if ok and name.strip():
                try:
                    presets = _json.loads(PRESETS_FILE.read_text(encoding='utf-8')) if PRESETS_FILE.exists() else {}
                    presets[name.strip()] = text
                    PRESETS_FILE.write_text(_json.dumps(presets, indent=2, ensure_ascii=False), encoding='utf-8')
                    _load_saved_presets()
                except Exception as e:
                    log.error(f"[instructions] save preset error: {e}")

        left_lay.addWidget(saved_presets_container)
        save_preset_btn = _chip("💾 Save current as preset", accent=True)
        save_preset_btn.clicked.connect(_save_preset)
        left_lay.addWidget(save_preset_btn)
        left_lay.addStretch()
        left_scroll.setWidget(left_w)
        body.addWidget(left_scroll)

        # ── RIGHT PANEL: editor ───────────────────────────────────────────────
        right_w = QWidget()
        right_w.setStyleSheet(f"QWidget {{ background: {_tc['base']}; }}")
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(16, 14, 16, 14)
        right_lay.setSpacing(8)

        desc = QLabel("These instructions shape how the assistant behaves for you. Click a preset on the left to insert text.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent;")
        right_lay.addWidget(desc)

        text_edit = QTextEdit()
        text_edit.setPlaceholderText(
            "Example:\n"
            "- Always be enthusiastic and encouraging\n"
            "- Use emojis when appropriate\n"
            "- Explain technical concepts simply"
        )
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                color: #E6EDF3;
                line-height: 1.6;
            }}
            QTextEdit:focus {{ border-color: #58A6FF; }}
        """)
        current = self.controller.get_custom_instructions()
        if current:
            text_edit.setPlainText(current)
        right_lay.addWidget(text_edit, 1)
        body.addWidget(right_w)
        body.setSizes([230, 520])
        root.addWidget(body)

        # ── Insert helper ─────────────────────────────────────────────────────
        def _insert(text):
            cursor = text_edit.textCursor()
            if cursor.hasSelection():
                cursor.removeSelectedText()
            if text_edit.toPlainText() and not text_edit.toPlainText().endswith('\n'):
                text_edit.insertPlainText('\n')
            text_edit.insertPlainText(text)
            text_edit.setFocus()

        # Footer buttons
        footer = QFrame()
        footer.setFixedHeight(54)
        footer.setStyleSheet(f"QFrame {{ background: {_tc['surface']}; border-top: 1px solid {_tc['border']}; }}")
        foot_l = QHBoxLayout(footer)
        foot_l.setContentsMargins(16, 0, 16, 0)
        foot_l.setSpacing(8)

        clear_btn = _chip("🗑 Clear")
        clear_btn.clicked.connect(text_edit.clear)
        foot_l.addWidget(clear_btn)
        foot_l.addStretch()

        cancel_btn = _chip("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        foot_l.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("""
            QPushButton { background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                border-radius: 6px; padding: 8px 24px; font-size: 12px; color: #58A6FF; font-weight: 600; }
            QPushButton:hover { background: rgba(88,166,255,0.24); border-color: #58A6FF; color: #79BBFF; }
        """)
        save_btn.clicked.connect(lambda: (
            self.controller.set_custom_instructions(text_edit.toPlainText().strip()),
            self.add_system_message("✓ **Custom instructions saved**"),
            dialog.accept()
        ))
        foot_l.addWidget(save_btn)
        root.addWidget(footer)

        _load_saved_presets()
        dialog.exec()

    def toggle_voice(self):
        """Toggle voice mode on/off"""
        if not self.voice_btn_inline.isChecked():
            # Disable voice
            self.disable_voice()
        else:
            # Enable voice
            self.enable_voice()

    def enable_voice(self):
        """Enable voice mode. When settings['voice_setup_prompt_enabled'] is on
        (default), show the microphone setup popup first — Cancel aborts and
        leaves voice OFF; Save persists the chosen mic and proceeds."""
        if self.controller.settings.get('voice_setup_prompt_enabled', True):
            from systema.ui.dialogs.voice_setup_dialog import VoiceSetupDialog
            dlg = VoiceSetupDialog(self.controller, parent=self)
            if not dlg.exec():
                # Cancelled — do not enable voice; revert the toggle button.
                self.voice_enabled = False
                self.voice_btn_inline.setChecked(False)
                self._sync_voice_to_phone()
                return

        success, message = self.controller.enable_voice_mode()

        if success:
            self.voice_enabled = True
            self.voice_btn_inline.setChecked(True)
            self.update_voice_status("Ready")
        else:
            self.voice_enabled = False
            self.voice_btn_inline.setChecked(False)
            self.add_system_message(f"**Voice Mode Failed**\n\n{message}")
        self._sync_voice_to_phone()

    def disable_voice(self):
        """Disable voice mode"""
        self.controller.disable_voice_mode()
        self.voice_enabled = False
        self.voice_btn_inline.setChecked(False)
        self.update_voice_status("")
        self._sync_voice_to_phone()

    def _sync_voice_to_phone(self):
        """Push the safe settings subset (incl. voice state) to a connected phone."""
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            try:
                _ab._send_settings()
            except Exception:
                pass

    def _on_input_changed_sync(self):
        """Mirror the PC input box to the phone as the user types. Suppressed while
        applying a phone-driven update so the two don't echo each other."""
        if getattr(self, '_suppress_input_sync', False):
            return
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            try:
                _ab._dispatch({"cmd": "input_sync", "text": self.input_field.toPlainText()})
            except Exception:
                pass

    def load_personalization(self):
        """Load personalization settings"""
        user_name = self.controller.get_user_name()
        if user_name and hasattr(self, 'user_name_input'):
            self.user_name_input.setText(user_name)
        assistant_name = self.controller.get_assistant_name()
        if assistant_name and hasattr(self, 'assistant_name_input'):
            self.assistant_name_input.setText(assistant_name)
        # Always refresh hero labels — they exist in the new sidebar
        self._refresh_hero_labels()

    def _on_assistant_name_changed(self, text):
        """Called when assistant name input changes"""
        self.controller.set_assistant_name(text.strip())
        self._refresh_hero_labels()

    def update_voice_status(self, status):
        """Update voice status indicator + drive voice-derived UI gating"""
        status_styles = {
            'listening': ('• Listening...', 'color: #EA4335; font-weight: bold;'),
            'processing': ('• Processing...', 'color: #FBBC04; font-weight: bold;'),
            'synthesizing': ('• Synthesizing...', 'color: #FBBC04; font-weight: bold;'),
            'speaking': ('• Speaking...', 'color: #34A853; font-weight: bold;'),
            'inactive': ('', ''),
            'Ready': ('Voice ready', 'color: #9AA0A6;')
        }

        text, style = status_styles.get(status, ('', ''))
        self.voice_status_label.setText(text)
        self.voice_status_label.setStyleSheet(f"QLabel {{ font-size: 10px; margin: 0 8px; {style} }}")
        # Mirror to Android phone if connected
        _ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            _ab.update_voice_status(status)

        # Show the skip/stop button while speech is pending — synthesis AND
        # playback (manual interrupt mode only)
        if status in ('synthesizing', 'speaking') \
                and self.controller.get_voice_interrupt_mode() == 'manual':
            self.voice_interrupt_btn.show()
        else:
            self.voice_interrupt_btn.hide()

        # ── Voice gating: synthesizing/speaking block input + session switching
        # (like work mode does). Release only when nothing else is busy.
        if status in ('synthesizing', 'speaking'):
            self.set_input_enabled(False)
            self.set_input_placeholder("Voice output in progress... please wait")
            try:
                self.set_session_list_locked(True)
            except Exception:
                pass
        elif status in ('listening', 'inactive'):
            try:
                still_busy = (self.controller.is_processing
                              or self.controller.ai.tool_manager.work.is_working
                              or self.controller.voice_busy())
            except Exception:
                still_busy = False
            if not still_busy:
                self.set_input_enabled(True)
                try:
                    self.set_session_list_locked(False)
                except Exception:
                    pass

    def closeEvent(self, event):
        """Handle window close - just hide, don't close app"""
        # Persist the final position/size before hiding so the next open restores it.
        try:
            self.save_window_geometry()
        except Exception:
            pass
        self.hide()
        event.ignore()  # Prevent the window from actually closing

    def on_user_name_changed(self, text):
        """Called when user name input changes"""
        user_name = text.strip()
        self.controller.set_user_name(user_name)
        self._refresh_hero_labels()

    def _show_avatar_picker(self, emojis, on_select, title="Pick Avatar"):
        """Shared grid-based avatar picker popup."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QGridLayout, QWidget
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setModal(True)
        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: {_tc['base']};
            }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        hdr = QLabel(title)
        hdr.setStyleSheet("color: #E6EDF3; font-size: 13px; font-weight: 600; background: transparent;")
        lay.addWidget(hdr)

        grid_widget = QWidget()
        grid_widget.setStyleSheet("background: transparent;")
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        cols = 8
        for i, emoji in enumerate(emojis):
            btn = QPushButton(emoji)
            btn.setFixedSize(40, 40)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 20px; padding: 0;
                    background: {_tc['elevated']};
                    border: 1px solid {_tc['border']};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: rgba(88,166,255,0.15);
                    border-color: #58A6FF;
                }}
            """)
            btn.clicked.connect(lambda _, e=emoji: (on_select(e), dlg.accept()))
            grid.addWidget(btn, i // cols, i % cols)
        lay.addWidget(grid_widget)
        dlg.exec()

    def change_bot_avatar(self):
        """Change bot avatar — grid picker"""
        emojis = [
            '🤖','🦾','🧠','👾','🤵','🦊','🐺','🦁',
            '🐼','🐨','🦝','🦔','🦉','🐉','👽','🌟',
            '⚡','🔮','🎭','🎪','🦸','🧙','🥷','👻',
            '🌈','🔥','💎','🪄','🛸','🧬','⚙️','🎯',
            '🌙','☀️','🌊','🍀','🦋','🌸','🎵','🏆',
        ]
        self._show_avatar_picker(emojis, self.set_bot_avatar, "Assistant Avatar")

    def change_user_avatar(self):
        """Change user avatar — grid picker"""
        emojis = [
            '👤','👨','👩','🧑','😊','😎','🤓','🧙',
            '🦸','🥷','👸','🤴','🧝','🧜','🧚','🧞',
            '🕵️','👨‍💻','👩‍💻','🧑‍🚀','🧑‍🎨','🧑‍🎤','🧑‍🍳','🧑‍🔬',
            '😄','😏','🥳','🤩','😈','👿','🤠','🥸',
            '🐱','🐶','🦊','🐸','🐯','🦊','🐧','🦄',
        ]
        self._show_avatar_picker(emojis, self.set_user_avatar, "User Avatar")

    def set_bot_avatar(self, emoji):
        """Set bot avatar to emoji, clearing any saved picture."""
        self.bot_avatar = emoji
        self._bot_avatar_pixmap = None
        self._bot_avatar_image_path = ''
        self.bot_avatar_display.setStyleSheet("""
            QLabel {
                font-size: 24px;
                background-color: #1a2a3a;
                border-radius: 24px;
                border: 2px solid transparent;
            }
        """)
        self.bot_avatar_display.setText(emoji)
        self.save_config()

    def set_user_avatar(self, emoji):
        """Set user avatar to emoji, clearing any saved picture."""
        self.user_avatar = emoji
        self._user_avatar_pixmap = None
        self._user_avatar_image_path = ''
        _base = self._t()['base']
        self.user_avatar_display.setStyleSheet(f"""
            QLabel {{
                font-size: 12px;
                background-color: #1a2a1a;
                border-radius: 13px;
                border: 2px solid {_base};
            }}
        """)
        self.user_avatar_display.setText(emoji)
        self.save_config()

    def _upload_avatar(self, target: str):
        """Open file picker then show a crop/position editor for avatar pictures."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                     QLabel, QSlider, QFileDialog)
        from PyQt6.QtGui import QPixmap, QPainter, QPainterPath
        from PyQt6.QtCore import Qt, QRectF

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Avatar Picture", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.add_system_message("⚠️ Could not load that image file.")
            return

        # ── Picture editor dialog ─────────────────────────────────────────────
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Adjust Avatar Picture")
        dlg.setModal(True)
        dlg.setFixedSize(340, 420)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        # Live preview label (96×96 circle)
        PREVIEW_SIZE = 96
        preview = QLabel()
        preview.setFixedSize(PREVIEW_SIZE, PREVIEW_SIZE)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)

        # State
        state = {'zoom': 1.0, 'ox': 0.5, 'oy': 0.5}   # ox/oy = centre as fraction 0-1

        def _render():
            """Re-render the circular preview from current state."""
            z = state['zoom']
            src_w = int(pixmap.width() / z)
            src_h = int(pixmap.height() / z)
            src_x = int((pixmap.width()  - src_w) * state['ox'])
            src_y = int((pixmap.height() - src_h) * state['oy'])
            src_x = max(0, min(src_x, pixmap.width()  - src_w))
            src_y = max(0, min(src_y, pixmap.height() - src_h))
            crop = pixmap.copy(src_x, src_y, src_w, src_h)
            scaled = crop.scaled(PREVIEW_SIZE, PREVIEW_SIZE,
                                 Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            # Crop to square centred
            cx = (scaled.width()  - PREVIEW_SIZE) // 2
            cy = (scaled.height() - PREVIEW_SIZE) // 2
            sq = scaled.copy(cx, cy, PREVIEW_SIZE, PREVIEW_SIZE)
            # Circular mask
            out = QPixmap(PREVIEW_SIZE, PREVIEW_SIZE)
            out.fill(Qt.GlobalColor.transparent)
            painter = QPainter(out)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addEllipse(QRectF(0, 0, PREVIEW_SIZE, PREVIEW_SIZE))
            painter.setClipPath(path)
            painter.drawPixmap(0, 0, sq)
            painter.end()
            preview.setPixmap(out)
            return out

        _render()

        def _make_slider_row(label_text, min_v, max_v, init, on_change):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #8B949E; font-size: 11px; background: transparent; min-width: 60px;")
            row.addWidget(lbl)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(min_v, max_v)
            sl.setValue(init)
            sl.setStyleSheet(f"""
                QSlider::groove:horizontal {{ height: 4px; background: {_tc['elevated']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: #58A6FF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
                QSlider::sub-page:horizontal {{ background: rgba(88,166,255,0.4); border-radius: 2px; }}
            """)
            sl.valueChanged.connect(on_change)
            row.addWidget(sl)
            return row

        def _on_zoom(v):
            state['zoom'] = 1.0 + v / 100.0
            _render()

        def _on_x(v):
            state['ox'] = v / 100.0
            _render()

        def _on_y(v):
            state['oy'] = v / 100.0
            _render()

        lay.addLayout(_make_slider_row("Zoom",     0, 300, 0,  _on_zoom))
        lay.addLayout(_make_slider_row("Horizontal", 0, 100, 50, _on_x))
        lay.addLayout(_make_slider_row("Vertical",   0, 100, 50, _on_y))

        note = QLabel("Adjust zoom and position, then click Apply.")
        note.setStyleSheet("color: #555; font-size: 10px; background: transparent;")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch()

        foot = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {_tc['border']}; border-radius:6px; padding:8px 18px; color:#8B949E; }} QPushButton:hover {{ color:#E6EDF3; }}")
        cancel_btn.clicked.connect(dlg.reject)
        foot.addWidget(cancel_btn)
        foot.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet("QPushButton { background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4); border-radius:6px; padding:8px 24px; color:#58A6FF; font-weight:600; } QPushButton:hover { background: rgba(88,166,255,0.24); }")
        foot.addWidget(apply_btn)
        lay.addLayout(foot)

        result_pixmap = [None]

        def _final_square():
            """Full-resolution square crop from the current editor state — the
            shared crop helper is also used on restore, so the saved framing is
            reproduced exactly."""
            return self._avatar_crop_square(
                pixmap, state['zoom'], state['ox'], state['oy'])

        def _apply():
            result_pixmap[0] = _final_square()
            dlg.accept()

        apply_btn.clicked.connect(_apply)
        dlg.exec()

        if result_pixmap[0] is None:
            return

        final = result_pixmap[0]

        # Persist the crop transform so restart reproduces this exact framing.
        setattr(self, f'_{target}_avatar_zoom', state['zoom'])
        setattr(self, f'_{target}_avatar_ox', state['ox'])
        setattr(self, f'_{target}_avatar_oy', state['oy'])

        if target == 'bot':
            self.bot_avatar = ''
            self._bot_avatar_pixmap = final
            self._bot_avatar_image_path = path
            display = self.bot_avatar_display
            # 44/22, not 48/26: the hero labels' 2px borders shrink their
            # content rects — full-size pixmaps get edge-clipped (ragged rim).
            display.setPixmap(make_circular_pixmap(final, 44))
            display.setText('')
        else:
            self.user_avatar = ''
            self._user_avatar_pixmap = final
            self._user_avatar_image_path = path
            display = self.user_avatar_display
            display.setPixmap(make_circular_pixmap(final, 22))
            display.setText('')
        self.save_config()

    def clear_chat(self):
        """Clear chat history WITH notification"""
        self._clear_chat_internal()
        self.add_system_message("🔄 **Chat Cleared** - Ready for a new conversation!")

    def clear_chat_silent(self):
        """Clear chat history WITHOUT notification (for session loading)"""
        self._clear_chat_internal()

    def _clear_chat_internal(self):
        """Internal method to clear chat widgets"""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.message_widgets = []
        self._ai_turn_group = None   # widgets are gone — never append into them
        self._stream_seg = None      # ditto for any in-flight stream widgets
        self._stream_think_card = None
        self._stream_think_pending = ''
        # Clearing removes the greeting banner too, so the empty-session state
        # just ended — re-anchor the floating input. Without this the pill kept
        # whatever position it last computed, which is why loading an existing
        # session left it suspended in the middle of the window with no
        # greeting in sight.
        try:
            self._position_input_overlay()
        except (AttributeError, RuntimeError):
            pass
        self._stream_active = False
        self._turn_thinking_card = None
        self._turn_thinking_group = None
        # Stop the live-sync timer and drop the card reference so a new session gets a fresh card
        if hasattr(self, '_skills_ui_card_timer') and self._skills_ui_card_timer is not None:
            try:
                self._skills_ui_card_timer.stop()
            except Exception:
                pass
        self._skills_ui_card_widget = None
        self._skills_ui_card_timer = None
        self._refresh_msg_navigator()

    def _refresh_msg_navigator(self):
        """Rebuild the message navigator from message_widgets (safe no-op
        before init_ui / after teardown). Skipped during a bulk session replay
        — every add_* calls this, which made loading N messages rebuild the
        rail N times (O(n^2)); render_loaded_messages does ONE rebuild at the
        end instead."""
        if getattr(self, '_bulk_render', False):
            return
        nav = getattr(self, '_msg_navigator', None)
        if nav is not None:
            was_visible = nav.isVisible()
            try:
                nav.rebuild()
            except RuntimeError:
                pass
            # When the rail first appears / disappears, the bubble width gutter
            # changes — reflow so trailing ⋯ buttons clear the rail.
            try:
                if nav.isVisible() != was_visible:
                    self._reflow_bubbles()
            except (RuntimeError, AttributeError):
                pass

    def render_loaded_messages(self):
        """Render messages from loaded session.

        Work-mode narration is RENDERED since the grouped-turn redesign
        (2026-07-17): the agent's commentary between tool runs is part of the
        merged turn bubble — strip_tool_calls removes the code fences, and
        whatever text remains joins the turn like it did live. (The old
        behavior dropped every work-step assistant bubble on reload.)"""
        from systema.common.perf_monitor import span
        self._bulk_render = True   # no typing-reveal / per-message anim / nav rebuilds
        # Batch repaints: without this every bubble insertion triggers its own
        # layout+paint pass, which is most of the session-switch stall.
        self.setUpdatesEnabled(False)
        _n = len(self.controller.ai.conversation_history)
        _sp = span(f"render_loaded_messages[{_n} msgs]")
        _sp.__enter__()
        try:
            self.clear_chat_silent()
            tm = self.controller.ai.tool_manager
            history = self.controller.ai.conversation_history

            # Pre-2026-07-20 sessions stored the memory ui_event BEFORE its user
            # message; rendered in place, the recall card lands in the PREVIOUS
            # turn's bubble. Defer those cards and flush them right after the
            # user message so they open the following AI turn, like live.
            _deferred_mem = []

            def _flush_deferred_mem():
                for _m in _deferred_mem:
                    self.add_memory_context_widget(
                        context_id=_m.get("_memory_context_id", ""),
                        memories=_m.get("_memories_preview", []),
                        save_to_history=False,
                    )
                _deferred_mem.clear()

            for _i, msg in enumerate(history):
                role = msg.get("role", "")
                raw = msg.get("content", "")
                if isinstance(raw, list):
                    raw = " ".join(
                        block.get("text", "") for block in raw
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if not isinstance(raw, str):
                    raw = ""

                content = tm.strip_tool_calls(raw)
                if role == "ui_event":
                    if msg.get("_type") == "memory_context":
                        _ctx_id = msg.get("_memory_context_id", "")
                        if not _ctx_id or not isinstance(_ctx_id, str):
                            log.warning(
                                f"[ChatWindow.render_loaded_messages] Skipping memory_context with invalid id: {_ctx_id!r}")
                        elif (_i + 1 < len(history)
                                and history[_i + 1].get("role") == "user"):
                            _deferred_mem.append(msg)   # old ordering — defer
                        else:
                            self.add_memory_context_widget(
                                context_id=_ctx_id,
                                memories=msg.get("_memories_preview", []),
                                save_to_history=False,
                            )
                    elif msg.get("_type") == "skills_card":
                        self.add_loaded_skills_card(save_to_history=False)
                    elif msg.get("_type") == "file_op":
                        self.add_file_op_card(msg.get("_file_op") or {},
                                              save_to_history=False)
                    elif msg.get("_type") == "web_search":
                        self.add_web_search_card(
                            {'query': msg.get("_web_query", ""),
                             'results': msg.get("_web_results", [])},
                            save_to_history=False)
                    elif msg.get("_type") == "thinking":
                        self.add_thinking_card(msg.get("_thinking", ""),
                                               save_to_history=False)
                    elif msg.get("_type") == "skill_action":
                        self.add_skill_action_card(
                            {'skill': msg.get("_skill", ""),
                             'action': msg.get("_skill_action", "load"),
                             'ok': msg.get("_skill_ok", True),
                             'detail': msg.get("_skill_detail", "")},
                            save_to_history=False)
                    elif msg.get("_type") == "image_attach":
                        # New shape: real ImageRefs, rendered as a bubble with
                        # working detach/delete. Old sessions stored only a
                        # list of paths under _image_paths and still render
                        # through the original card — never break a session
                        # file that was written before the redesign.
                        if msg.get("_images"):
                            self.add_image_bubble(msg["_images"],
                                                  origin='agent',
                                                  annotation=msg.get("_annotation", ""))
                        else:
                            self.add_image_attach_card(
                                {'paths': msg.get("_image_paths", []),
                                 'annotation': msg.get("_annotation", "")},
                                save_to_history=False)
                    elif msg.get("_type") == "web_page":
                        self.add_web_page_card(
                            {'mode': msg.get("_web_mode", "open"),
                             'url': msg.get("_web_url", ""),
                             'title': msg.get("_web_title", ""),
                             'text': msg.get("_web_text", ""),
                             'links': msg.get("_web_links", [])},
                            save_to_history=False)
                    else:
                        self.add_code_execution_note(
                            msg.get("_code", ""),
                            msg.get("_output", ""),
                            save_to_history=False,
                            annotation=msg.get("_annotation", ""),
                        )
                elif msg.get("_images") and role == "user":
                    # A user turn carrying images. The bubble comes first so it
                    # keeps its place in the transcript; any text on the same
                    # entry follows as its own bubble.
                    self.add_image_bubble(msg["_images"], origin='user')
                    if content:
                        self.add_user_message(content)
                    _flush_deferred_mem()
                elif content:
                    if role == "user":
                        self.add_user_message(content)
                        _flush_deferred_mem()
                    elif role == "assistant":
                        self.add_ai_message(content)
            _flush_deferred_mem()   # safety net: never drop a card
        except Exception as e:
            log.error(f"[ChatWindow.render_loaded_messages] render_loaded_messages error: {e}")
        finally:
            self._bulk_render = False
            self.setUpdatesEnabled(True)
            _sp.__exit__(None, None, None)
        # ONE navigator rebuild for the whole replay (was once per message).
        self._refresh_msg_navigator()
        # Re-anchor the floating pill against the transcript we just replayed.
        # The empty-session position is computed from whether the greeting is
        # up; after a load it never is, so this settles it back to the bottom.
        try:
            self._position_input_overlay()
        except (AttributeError, RuntimeError):
            pass

    def _remove_tool_usage_format(self, content):
        """Remove tool usage JSON blocks from AI message"""
        cleaned = self.controller.ai.tool_manager.strip_tool_calls(content)
        return cleaned

    def _open_names_dialog(self):
        """Quick inline dialog to edit user + assistant names."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Names")
        dlg.setModal(True)
        dlg.setFixedWidth(320)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        _ss_lbl = "color: #8B949E; font-size: 10px; background: transparent;"
        _ss_inp = f"""
            QLineEdit {{ background: {_tc['elevated']}; border: 1px solid {_tc['border']};
                border-radius: 6px; padding: 7px 10px; font-size: 12px; color: #E6EDF3; }}
            QLineEdit:focus {{ border-color: rgba(88,166,255,0.55); }}
        """

        lbl_u = QLabel("Your name")
        lbl_u.setStyleSheet(_ss_lbl)
        lay.addWidget(lbl_u)
        inp_u = QLineEdit()
        inp_u.setPlaceholderText("Enter your name…")
        inp_u.setStyleSheet(_ss_inp)
        inp_u.setText(self.controller.get_user_name())
        lay.addWidget(inp_u)

        lbl_a = QLabel("Assistant name (leave blank for default)")
        lbl_a.setStyleSheet(_ss_lbl)
        lay.addWidget(lbl_a)
        inp_a = QLineEdit()
        inp_a.setPlaceholderText("e.g. Kim, Nova, Aria…")
        inp_a.setStyleSheet(_ss_inp)
        inp_a.setText(self.controller.get_assistant_name())
        lay.addWidget(inp_a)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(f"QPushButton {{ background: transparent; border: 1px solid {_tc['border']}; border-radius:6px; padding:7px 18px; color:#8B949E; }} QPushButton:hover {{ color:#E6EDF3; }}")
        cancel.clicked.connect(dlg.reject)
        save = QPushButton("Save")
        save.setStyleSheet("QPushButton { background: rgba(88,166,255,0.14); border:1px solid rgba(88,166,255,0.4); border-radius:6px; padding:7px 24px; color:#58A6FF; font-weight:600; } QPushButton:hover { background:rgba(88,166,255,0.24); }")

        def _save():
            self.controller.set_user_name(inp_u.text().strip())
            self.controller.set_assistant_name(inp_a.text().strip())
            self._refresh_hero_labels()
            dlg.accept()

        save.clicked.connect(_save)
        btns.addWidget(cancel)
        btns.addStretch()
        btns.addWidget(save)
        lay.addLayout(btns)
        dlg.exec()

    def _open_avatars_dialog(self):
        """Combined avatar editor for both bot and user."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                     QLabel, QTabWidget, QWidget)
        _tc = self._t()
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Avatars")
        dlg.setModal(True)
        dlg.setFixedWidth(380)
        dlg.setStyleSheet(f"QDialog {{ background: {_tc['base']}; color: #E6EDF3; font-family: 'Segoe UI', system-ui; }}")

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: none; background: {_tc['base']}; }}
            QTabBar::tab {{ background: {_tc['elevated']}; color: #8B949E; border: none;
                padding: 8px 20px; font-size: 11px; }}
            QTabBar::tab:selected {{ background: {_tc['surface']}; color: #E6EDF3; font-weight: 600; }}
            QTabBar::tab:hover {{ color: #E6EDF3; }}
        """)

        def _make_avatar_tab(target, current_emoji, display_label, emojis):
            w = QWidget()
            w.setStyleSheet(f"background: {_tc['base']};")
            wl = QVBoxLayout(w)
            wl.setContentsMargins(16, 16, 16, 16)
            wl.setSpacing(12)

            # Current preview
            preview_row = QHBoxLayout()
            av_preview = QLabel()
            av_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            av_preview.setFixedSize(56, 56)
            bg_color = "#1a2a3a" if target == 'bot' else "#1a2a1a"
            av_preview.setStyleSheet(f"font-size: 28px; background: {bg_color}; border-radius: 28px;")

            # Show current pixmap or emoji
            existing_pm = getattr(self, '_bot_avatar_pixmap' if target == 'bot' else '_user_avatar_pixmap', None)
            if existing_pm and not existing_pm.isNull():
                av_preview.setPixmap(make_circular_pixmap(existing_pm, 56))
            else:
                av_preview.setText(current_emoji or ('🤖' if target == 'bot' else '👤'))

            preview_row.addWidget(av_preview)
            preview_row.addSpacing(12)
            info_col = QVBoxLayout()
            info_col.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            lbl1 = QLabel(display_label)
            lbl1.setStyleSheet("font-size: 12px; font-weight: 600; color: #E6EDF3; background: transparent;")
            lbl2 = QLabel("Pick an emoji or upload a picture")
            lbl2.setStyleSheet("font-size: 10px; color: #555; background: transparent;")
            info_col.addWidget(lbl1)
            info_col.addWidget(lbl2)
            preview_row.addLayout(info_col)
            preview_row.addStretch()
            wl.addLayout(preview_row)

            # Emoji grid
            from PyQt6.QtWidgets import QGridLayout
            grid_w = QWidget()
            grid_w.setStyleSheet("background: transparent;")
            grid = QGridLayout(grid_w)
            grid.setSpacing(5)
            cols = 8
            for i, em in enumerate(emojis):
                btn = QPushButton(em)
                btn.setFixedSize(36, 36)
                btn.setStyleSheet(f"""
                    QPushButton {{ font-size: 18px; padding: 0; background: {_tc['elevated']};
                        border: 1px solid {_tc['border']}; border-radius: 6px; }}
                    QPushButton:hover {{ background: rgba(88,166,255,0.15); border-color: #58A6FF; }}
                """)
                def _pick(checked=False, e=em, p=av_preview, t=target):
                    bg = '#1a2a3a' if t == 'bot' else '#1a2a1a'
                    p.setStyleSheet(f"font-size: 28px; background: {bg}; border-radius: 28px;")
                    p.setText(e)
                    if t == 'bot':
                        self.set_bot_avatar(e)
                    else:
                        self.set_user_avatar(e)
                btn.clicked.connect(_pick)
                grid.addWidget(btn, i // cols, i % cols)
            wl.addWidget(grid_w)

            # Upload picture button
            upload_btn = QPushButton("Upload custom picture…")
            upload_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; border: 1px solid {_tc['border']};
                    border-radius: 7px; padding: 9px; font-size: 11px; color: #8B949E; }}
                QPushButton:hover {{ border-color: rgba(88,166,255,0.4); color: #58A6FF; }}
            """)
            def _do_upload(t=target, p=av_preview):
                self._upload_avatar(t)
                pm2 = getattr(self, '_bot_avatar_pixmap' if t == 'bot' else '_user_avatar_pixmap', None)
                if pm2 and not pm2.isNull():
                    p.setPixmap(make_circular_pixmap(pm2, 56))
                    p.setText('')
                    size_row.setVisible(True)
            upload_btn.clicked.connect(lambda: _do_upload())

            # Size slider — visible only when a picture is active
            size_row = QWidget()
            size_row.setStyleSheet("background: transparent;")
            from PyQt6.QtWidgets import QHBoxLayout as _HBL, QSlider as _SL
            sr_lay = _HBL(size_row)
            sr_lay.setContentsMargins(0, 0, 0, 0)
            sr_lay.setSpacing(8)

            size_lbl = QLabel("Picture size")
            size_lbl.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent; min-width: 70px;")
            sr_lay.addWidget(size_lbl)

            _current_size = getattr(self, f'_{"bot" if target == "bot" else "user"}_avatar_size', 32)
            size_slider = _SL(Qt.Orientation.Horizontal)
            size_slider.setRange(20, 72)
            size_slider.setValue(_current_size)
            size_slider.setStyleSheet(f"""
                QSlider::groove:horizontal {{ height: 4px; background: {_tc['elevated']}; border-radius: 2px; }}
                QSlider::handle:horizontal {{ background: #58A6FF; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; border: none; }}
                QSlider::sub-page:horizontal {{ background: rgba(88,166,255,0.4); border-radius: 2px; }}
            """)
            sr_lay.addWidget(size_slider, stretch=1)

            size_val_lbl = QLabel(f"{_current_size}px")
            size_val_lbl.setStyleSheet("color: #8B949E; font-size: 10px; background: transparent; min-width: 30px;")
            sr_lay.addWidget(size_val_lbl)

            def _on_size(v, t=target, svl=size_val_lbl):
                svl.setText(f"{v}px")
                if t == 'bot':
                    self._bot_avatar_size = v
                else:
                    self._user_avatar_size = v
                # Sync the other slider if uniform is checked
                if _uniform_state[0]:
                    other = _all_sliders[1] if t == 'bot' else _all_sliders[0]
                    if other and other.value() != v:
                        other.blockSignals(True)
                        other.setValue(v)
                        other.blockSignals(False)
                        if t == 'bot':
                            self._user_avatar_size = v
                        else:
                            self._bot_avatar_size = v
                self.save_config()
            size_slider.valueChanged.connect(_on_size)
            _all_sliders.append(size_slider)

            # Only show if picture is already active
            has_picture = bool(existing_pm and not existing_pm.isNull())
            size_row.setVisible(has_picture)

            wl.addWidget(upload_btn)
            wl.addWidget(size_row)
            return w

        _all_sliders = []      # [bot_slider, user_slider] — filled as tabs are built
        _uniform_state = [getattr(self, '_avatar_size_uniform', False)]

        bot_emojis = [
            '🤖','🦾','🧠','👾','🤵','🦊','🐺','🦁',
            '🐼','🐨','🦝','🦔','🦉','🐉','👽','🌟',
            '⚡','🔮','🎭','🎪','🦸','🧙','🥷','👻',
            '🌈','🔥','💎','🪄','🛸','🧬','⚙️','🎯',
        ]
        user_emojis = [
            '👤','👨','👩','🧑','😊','😎','🤓','🧙',
            '🦸','🥷','👸','🤴','🧝','🧜','🧚','🧞',
            '🕵️','👨‍💻','👩‍💻','🧑‍🚀','🧑‍🎨','🧑‍🎤','🧑‍🍳','🧑‍🔬',
            '😄','😏','🥳','🤩','😈','🤠','🥸','🦄',
        ]

        tabs.addTab(_make_avatar_tab('bot',  self.bot_avatar,  "Assistant avatar", bot_emojis),  "🤖  Assistant")
        tabs.addTab(_make_avatar_tab('user', self.user_avatar, "Your avatar",       user_emojis), "👤  You")
        lay.addWidget(tabs)

        # Uniform size checkbox
        from PyQt6.QtWidgets import QCheckBox
        uniform_row = QWidget()
        uniform_row.setStyleSheet(f"background: {_tc['surface']}; border-top: 1px solid {_tc['border']};")
        ur_lay = QHBoxLayout(uniform_row)
        ur_lay.setContentsMargins(16, 10, 16, 10)
        uniform_cb = QCheckBox("Uniform size — keep both avatars the same size")
        uniform_cb.setChecked(_uniform_state[0])
        uniform_cb.setStyleSheet(f"""
            QCheckBox {{ color: #8B949E; font-size: 10px; background: transparent; spacing: 8px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid {_tc['border']}; background: {_tc['elevated']}; }}
            QCheckBox::indicator:checked {{ background: #58A6FF; border-color: #58A6FF; }}
        """)
        def _on_uniform(state):
            checked = bool(state)
            _uniform_state[0] = checked
            self._avatar_size_uniform = checked
            self.save_config()
            # Immediately sync user size to bot size when turned on
            if checked and _all_sliders:
                bot_val = _all_sliders[0].value() if _all_sliders else self._bot_avatar_size
                if len(_all_sliders) > 1:
                    _all_sliders[1].setValue(bot_val)
        uniform_cb.stateChanged.connect(_on_uniform)
        ur_lay.addWidget(uniform_cb)
        lay.addWidget(uniform_row)

        close_btn = QPushButton("Done")
        close_btn.setStyleSheet("""
            QPushButton { background: rgba(88,166,255,0.14); border: 1px solid rgba(88,166,255,0.4);
                margin: 12px 16px; border-radius: 7px; padding: 9px; font-size: 12px;
                color: #58A6FF; font-weight: 600; }
            QPushButton:hover { background: rgba(88,166,255,0.24); }
        """)
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn)
        dlg.exec()



    # ── OS-level window title (taskbar / Alt-Tab) ─────────────────────────────
    # Spinner-only-while-busy: idle title is just the session name; a braille
    # spinner glyph is prefixed while the AI is actively generating. Same
    # frame set as ManageTasksWindow._SPINNER_FRAMES, kept in sync visually.
    _TITLE_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _apply_window_title(self, spinner_frame: str = None):
        """Set the OS-level window title. Pass a spinner glyph while the AI is
        generating; omit (None) for the idle title — plain session name."""
        try:
            session_id = getattr(self.controller, 'current_session_id', None)
            name = self.controller.session_manager.get_session_name(session_id) if session_id else "New Session"
        except Exception:
            name = "New Session"
        prefix = f"{spinner_frame} " if spinner_frame else ""
        self.setWindowTitle(f"{prefix}{name}")

    def _start_title_spinner(self):
        if self.title_spinner_timer is None:
            self.title_spinner_timer = QTimer(self)
            self.title_spinner_timer.timeout.connect(self._tick_title_spinner)
        self.title_spinner_frame = 0
        self._tick_title_spinner()   # first frame immediately, don't wait for the interval
        self.title_spinner_timer.start(120)

    def _tick_title_spinner(self):
        frame = self._TITLE_SPINNER_FRAMES[self.title_spinner_frame % len(self._TITLE_SPINNER_FRAMES)]
        self.title_spinner_frame += 1
        self._apply_window_title(frame)

    def _stop_title_spinner(self):
        if self.title_spinner_timer:
            self.title_spinner_timer.stop()
        self._apply_window_title()



    # ── Adaptive sticky-bottom autoscroll ──────────────────────────────────
    # Replaces the old _user_scrolling suppression flag (which swallowed the
    # next auto-scroll after ANY manual touch — hence "auto-snap rarely
    # works"). The pin now mirrors the actual scroll position: near the
    # bottom = follow new content, away = back-reading in peace.

    def _install_smooth_scrolling(self, scroll_area):
        """Shared target-based wheel scrolling for the chat viewport, plus the
        pin release: scrolling UP must drop sticky-bottom IMMEDIATELY.

        Position alone is not enough to decide the pin — a glide that happens to
        end inside the bottom tolerance re-armed it, so the next streamed chunk
        yanked the view back down while the user was reading. Intent (which way
        the wheel turned) is authoritative; position only re-arms the pin when
        the user deliberately scrolls back down to the end.
        """
        from systema.ui.widgets.smooth_scroll import install_smooth_scroll

        def _on_user_scroll(dy):
            self._user_scrolling = True
            self._last_user_scroll_dy = dy
            if dy < 0:                       # upward → user is back-reading
                self._stick_to_bottom = False

        install_smooth_scroll(scroll_area, on_user_scroll=_on_user_scroll)

    STICKY_ZONE_PX = 60   # "near the bottom" tolerance

    def _update_stick_to_bottom(self, _value=None):
        """Recompute the pin on scroll movement. Skipped while our own
        animated scroll is in flight — a programmatic glide to a centred
        message must not release the pin it is serving."""
        if self._chat_scroller_animating():
            return
        try:
            sb = self.chat_scroll_area.verticalScrollBar()
        except (AttributeError, RuntimeError):
            return
        near_bottom = (sb.maximum() - sb.value()) <= self.STICKY_ZONE_PX
        # Do not re-arm the pin from POSITION alone while the user is heading
        # up: a small upward nudge that leaves you still inside the tolerance
        # would otherwise re-pin instantly, and the next streamed chunk would
        # drag you back down mid-read. Only a deliberate downward move re-arms.
        if near_bottom and getattr(self, '_last_user_scroll_dy', 0) < 0:
            return
        self._stick_to_bottom = near_bottom

    def _chat_scroller(self):
        """The SmoothScroller owning the chat viewport's vertical position.

        Every programmatic scroll goes through it rather than running its own
        animation: one spring means a user wheel notch arriving mid-glide
        blends into the motion instead of two animators fighting over
        setValue().
        """
        try:
            from systema.ui.widgets.smooth_scroll import scroller_for
            return scroller_for(getattr(self, 'chat_scroll_area', None))
        except (ImportError, RuntimeError):
            return None

    def _chat_scroller_animating(self) -> bool:
        s = self._chat_scroller()
        try:
            return bool(s is not None and s.is_animating)
        except RuntimeError:
            return False

    def _on_scroll_range_changed(self, _min=0, _max=0):
        """Content grew (streaming text / work cards): while sticky, track the
        new bottom so long AI streams stay glued.

        This used to be a bare setValue(maximum()), which teleported the view
        once per streamed chunk — the crawl of a long reply came out as a
        stutter. follow_bottom() re-aims the spring at the live maximum every
        frame instead, so growth reads as continuous motion.
        """
        if not getattr(self, '_stick_to_bottom', True):
            return
        s = self._chat_scroller()
        if s is not None:
            try:
                s.follow_bottom()
                return
            except RuntimeError:
                pass
        try:
            sb = self.chat_scroll_area.verticalScrollBar()
            sb.setValue(sb.maximum())
        except (AttributeError, RuntimeError):
            pass

    def scroll_to_bottom(self):
        """Legacy helper — scrolls to absolute bottom (used by voice/thinking flows)."""
        QTimer.singleShot(50, self._do_scroll)

    def _do_scroll(self):
        """Instant scroll to bottom (legacy, non-animated). Sticky-gated: never
        fights the user while they are back-reading."""
        if not getattr(self, '_stick_to_bottom', True):
            return
        if hasattr(self, 'chat_scroll_area'):
            sb = self.chat_scroll_area.verticalScrollBar()
            self._animated_scroll_to(sb.maximum())
        else:
            scroll_area = self.chat_widget.parent().parent()
            if isinstance(scroll_area, QScrollArea):
                sb = scroll_area.verticalScrollBar()
                sb.setValue(sb.maximum())

    # ── Smart scroll-to-message ────────────────────────────────────────────

    def scroll_to_widget(self, widget, force=False):
        """
        Scroll so the new message is optimally visible.

        Auto-follow callers (new bubbles, work cards) leave force=False and are
        gated by the sticky-bottom pin; explicit navigation (the message
        navigator) passes force=True and always scrolls.

        IMPORTANT: must only be called AFTER the message's pop-in animation
        has finished, so that:
          - widget.height() returns the real rendered height
          - sb.maximum() reflects the full content including the new message
        Calling this mid-animation causes sb.maximum() to be stale (too small),
        making the clamp cut the target short on long chats.
        """
        if not hasattr(self, 'chat_scroll_area'):
            return
        if not force and not getattr(self, '_stick_to_bottom', True):
            return

        try:
            if not widget.isVisible():
                return
        except RuntimeError:
            return  # Widget already deleted

        sb = self.chat_scroll_area.verticalScrollBar()
        viewport_h = self.chat_scroll_area.viewport().height()

        try:
            pos_in_content = widget.mapTo(self.chat_widget, widget.rect().topLeft())
        except RuntimeError:
            return

        widget_top = pos_in_content.y()

        # Use actual rendered height — only valid after animation completes.
        # sizeHint() can be stale; widget.height() reflects the live layout.
        widget_h = widget.height()
        if widget_h < 20:
            widget_h = widget.sizeHint().height()
        if widget_h < 20:
            widget_h = 200  # safe fallback

        if widget_h <= viewport_h - 40:
            # Small message: centre it vertically in the viewport
            target = widget_top - (viewport_h - widget_h) // 2
        else:
            # Tall message: show from top with a small padding
            target = widget_top - 16

        # sb.maximum() is now accurate because the animation is complete.
        target = max(0, min(target, sb.maximum()))
        self._animated_scroll_to(target)

    def _animated_scroll_to(self, target_value: int):
        """Glide the chat scrollbar to target_value.

        Delegates to the viewport's SmoothScroller instead of running its own
        QPropertyAnimation. Two animators over one scrollbar meant a wheel
        notch during a scroll-to-message either got stomped or stomped the
        glide; feeding the same spring makes them blend. The distance-scaled
        duration is preserved as a distance-scaled SMOOTHING TIME so a long
        jump still takes visibly longer than a short one.
        """
        if not hasattr(self, 'chat_scroll_area'):
            return

        sb = self.chat_scroll_area.verticalScrollBar()
        current = sb.value()
        if abs(current - target_value) < 4:
            return

        s = self._chat_scroller()
        if s is None:
            sb.setValue(int(target_value))
            return

        from systema.ui.widgets.smooth_scroll import SMOOTH_TIME_PROGRAM
        distance = abs(target_value - current)
        # Map the old ANIM_SCROLL_MIN/MAX_MS envelope onto smoothing time: a
        # near jump settles fast, a page-long one gets the full glide.
        span = max(1, ANIM_SCROLL_MAX_MS - ANIM_SCROLL_MIN_MS)
        frac = min(1.0, max(0.0, (distance // 2 - ANIM_SCROLL_MIN_MS) / span))
        smooth = SMOOTH_TIME_PROGRAM * (0.55 + 0.45 * frac)
        s.scroll_to(target_value, smooth_time=smooth)


    # ── Inertia scroll — main chat ─────────────────────────────────────────

    def _inertia_tick(self):
        """Called ~70fps while inertia is active for main chat."""
        if not hasattr(self, 'chat_scroll_area'):
            self._inertia_timer.stop()
            return

        sb = self.chat_scroll_area.verticalScrollBar()
        self._inertia_velocity *= ANIM_INERTIA_FRICTION
        if abs(self._inertia_velocity) < ANIM_INERTIA_MIN_VELOCITY:
            self._inertia_timer.stop()
            self._inertia_velocity = 0.0
            return

        new_val = sb.value() + int(self._inertia_velocity)
        new_val = max(0, min(new_val, sb.maximum()))
        sb.setValue(new_val)

    # ── Inertia scroll — sidebar ───────────────────────────────────────────


    # ── Inertia scroll — input field ───────────────────────────────────────


    # ═══════════════════════════════════════════════════════════
    # MESSAGE CONTROL METHODS (Edit, Delete, Rewind, Regenerate)
    # ═══════════════════════════════════════════════════════════



    def send_message(self):
        """Send the typed message.

        Images are NOT collected here any more. Attaching one puts it straight
        into the conversation (its own bubble, its own history entry, its own
        number), so by the time you press Enter every picture is already part
        of the transcript. That is what killed the old "attached, pending send"
        limbo: an image was only real for the single request it rode along
        with, and any turn that did not go through this method lost it.
        """
        message = self.input_field.toPlainText().strip()
        if not message:
            return

        # SLASH COMMANDS RUN BEFORE THE SEND GATE, deliberately. A command is a
        # UI action, not a turn — and the ones you most need mid-turn are
        # exactly the ones the gate would block (/shutdown, /restart). Each
        # command carries its own mid-turn policy instead.
        if self.try_run_command(message):
            return

        # The text box stays typable while the assistant works (see
        # set_input_enabled), so the gate that used to be "the widget is
        # disabled" has to live here — otherwise Enter would fire a second
        # request into a turn already in flight.
        if not getattr(self, '_send_allowed', True):
            return

        # Sending re-engages the pin: jump to your own message and follow the reply.
        self._stick_to_bottom = True
        self._user_scrolling = False
        # A send snaps any in-flight typing reveal to its full text.
        self._finish_active_reveals()

        self.last_sent_message = message
        self.add_user_message(message)

        ab = getattr(getattr(self.controller, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.add_user_message(message)

        self.input_field.clear()   # collapses to one line (drops any drag floor)
        self.controller.send_message(message)

        # ── Auto-lock session list while AI is busy, then auto-unlock ────────
        QTimer.singleShot(600, self._start_session_lock_watcher)



    # ── Image preview helpers ─────────────────────────────────────────────────



    # ─────────────────────────────────────────────────────────────────────────



    def show_ai_message(self, message):
        if self.voice_enabled and not self.controller.ai.tool_manager.work.is_working:
            self.log("[Voice] Buffering message, starting TTS...")

            self.pending_voice_message = message
            self.waiting_for_playback = True

            self.start_thinking_animation()
            # Keep the in-turn typing dots alive through synthesis. The
            # controller hides them the moment the response lands, but in voice
            # mode nothing is shown until playback starts — leaving a dead gap
            # where the indicator had vanished and no text had appeared yet.
            self.show_thinking_bubble()

            self.speak_ai_response(message)
        else:
            self.add_ai_message(message)
            # Work mode: display immediately AND narrate the AI's commentary
            # (the TTS filter strips code fences; raw output is never passed here)
            if self.voice_enabled:
                self.speak_ai_response(message)

    def log(self, msg):
        """Helper for logging"""
        log.info(f"[ChatWindow] {msg}")

    def on_voice_playback_started(self):
        """Called when TTS playback actually starts (from background thread)"""
        self.log("[Voice] Playback started callback received")
        self.voice_playback_signal.emit()

    def _handle_voice_playback_on_main_thread(self):
        """Handle voice playback on main Qt thread"""
        self.log("[Voice] Processing playback on main thread")

        if self.waiting_for_playback and self.pending_voice_message:
            self.log("[Voice] Displaying buffered message NOW")
            self.waiting_for_playback = False

            self.stop_thinking_animation()
            # The dots handed the turn over to the speech-synced reveal.
            self.hide_thinking_bubble()

            self.add_ai_message(self.pending_voice_message)

            self.pending_voice_message = None

            self.log("[Voice] Message displayed successfully")
        else:
            self.log("[Voice] No pending message to display")

    def flush_pending_voice_message(self):
        """Display a reply still waiting on TTS playback.

        Voice mode holds the text back until playback starts. If the audio is
        killed first (auto barge-in, or the user hitting Stop), that callback
        never arrives — the reply stayed invisible while the synthesis dots span
        forever, even though the message was already in the session. Idempotent;
        also clears stale dots when nothing is pending.
        """
        if self.waiting_for_playback and self.pending_voice_message:
            self.log("[Voice] Playback aborted — displaying buffered message now")
            self._handle_voice_playback_on_main_thread()
            return True
        self.hide_thinking_bubble()
        return False

    def speak_ai_response(self, text):
        """Queue AI response for TTS (serialized speech queue — non-blocking,
        utterances never overlap)."""
        self.controller.speak_text(text)

    def handle_ai_response(self, result):
        """Handle AI response (work-mode updates route here) — go through
        show_ai_message so voice narration/buffering applies uniformly.

        Work-step narration is SHOWN since the grouped-turn redesign: the
        agent's commentary between tool runs joins the merged turn bubble.
        Only synthetic placeholders (no real model text) are filtered."""
        resp = (result.get('response') or '').strip()
        if not resp:
            return
        if result.get('narration_shown'):
            return  # already surfaced before the tool card (ordering fix)
        if result.get('thinking') and (
                resp in ('Working...', 'Working…')
                or resp.startswith(('Loading skill:', 'Unloading skill:'))):
            return  # engine placeholder, not narration
        self.show_ai_message(resp)



    def start_thinking_animation(self):
        """Start thinking animation"""
        if self.thinking_timer is None:
            self.thinking_timer = QTimer()
            self.thinking_timer.timeout.connect(self.update_thinking_animation)

        self.thinking_dots = 0
        self.thinking_timer.start(500)

    def stop_thinking_animation(self):
        """Stop thinking animation"""
        if self.thinking_timer:
            self.thinking_timer.stop()
        self.status_label.setText("")

    def update_thinking_animation(self):
        """Update thinking animation"""
        self.thinking_dots = (self.thinking_dots + 1) % 4
        dots = "●" * self.thinking_dots + "○" * (3 - self.thinking_dots)
        self.status_label.setText(f"{dots}")

    def interrupt_response(self):
        """Interrupt current AI response and restore message to input.
        Workmode branch: shows WorkmodeInterruptDialog with auto-dismiss polling;
        normal branch: delegates to controller.interrupt_request()."""
        if not self.controller.is_processing and not self.controller.ai.tool_manager.work.is_working and not self.controller.ai.tool_manager.work.interpreter.is_running:
            return

        # If in work mode, only show dialog when code is actively executing
        if self.controller.ai.tool_manager.work.is_working or self.controller.ai.tool_manager.work.interpreter.is_running:
            if not self.controller.ai.tool_manager.work.interpreter.is_running:
                # In work mode but no code is actively running — this is the
                # "thinking" gap (AI analyzing the last output) or we're awaiting
                # the model's next step. Cleanly cancel the whole work-mode
                # operation (interrupt_work handles UI teardown).
                self.controller.interrupt_work()
            else:
                from systema.ui.dialogs.timeout_dialog import WorkmodeInterruptDialog
                from PyQt6.QtWidgets import QDialog
                from PyQt6.QtCore import QTimer

                # Name the step in the dialog — the annotation the model wrote
                # for it if there is one ("digital signature check"), else the
                # tool. Vague is what made the old copy read like it killed the
                # whole turn.
                _work = self.controller.ai.tool_manager.work
                _label = (_work.interpreter.last_annotation
                          or _work.last_tool or "")
                dialog = WorkmodeInterruptDialog(self, tool_name=_label)

                # Auto-dismiss if code finishes while dialog is open
                _poll = QTimer()
                _poll.setInterval(200)
                def _check():
                    if not self.controller.ai.tool_manager.work.interpreter.is_running:
                        _poll.stop()
                        dialog.reject()
                _poll.timeout.connect(_check)
                _poll.start()

                try:
                    accepted = dialog.exec() == QDialog.DialogCode.Accepted
                finally:
                    _poll.stop()

                if accepted:
                    reason = dialog.reason_text

                    notice = "ERROR:\nUser interrupted the interpreter. You must exit immediately."
                    if reason:
                        notice += f"\nReason: {reason}"
                    else:
                        notice += "\nNo specified reason. Just exit."

                    # Interrupt the *running code* (not the worker): partial output
                    # is preserved and the notice is appended to it by
                    # run_python_interpreter, so both you and the AI can see where it
                    # stopped. The worker then finishes its cycle naturally — the
                    # AI analyzes the interrupted output and exits. No orphaned
                    # thread, and work_code_active(False) fires to clear the live
                    # console.
                    delivered = self.controller.ai.tool_manager.interrupt_running_code(notice)
                    if not delivered:
                        # Code already finished between accept and here — fall back
                        # to a clean work-mode cancel so we never get stuck.
                        self.controller.interrupt_work()
                    else:
                        # Keep the stop button — it now cancels the AI's exit
                        # response. A second click hard-cancels the whole op.
                        self.interrupt_btn.show()
                        self.interrupt_btn.setEnabled(True)
                        self.interrupt_btn.setToolTip("Cancel AI response")
                # else: dialog auto-dismissed or cancelled → workmode continues
            return

        success = self.controller.interrupt_request()

        if success:
            if self.last_user_message_widget:
                # Also drop its message_widgets entry (it used to linger as a
                # stale dead-widget record) and re-index the tail, so the
                # navigator and rewind slicing stay truthful.
                w = self.last_user_message_widget
                self.message_widgets = [md for md in self.message_widgets
                                        if md.get('widget') is not w]
                for i, md in enumerate(self.message_widgets):
                    md['index'] = i
                self.chat_layout.removeWidget(w)
                w.deleteLater()
                self.last_user_message_widget = None
                self._refresh_msg_navigator()
            canceled_turn = getattr(self, '_ai_turn_group', None)
            self._end_ai_turn_group()   # defensive: never append into a canceled turn

            if self.last_sent_message:
                current_text = self.input_field.toPlainText()
                # Return old message for easy editing
                if current_text:
                    self.input_field.text_input.setPlainText(self.last_sent_message + "\n\n" + current_text)
                else:
                    self.input_field.text_input.setPlainText(self.last_sent_message)
                self.last_sent_message = None

            self.interrupt_btn.hide()
            self.send_btn.show()

            self.hide_thinking()
            self.hide_thinking_bubble()
            # The canceled turn may now be an empty shell — an avatar+name husk
            # with nothing under it. hide_thinking_bubble only prunes the shell
            # the DOTS were in, which is not always this one (a system
            # interjection can have split the turn since).
            if canceled_turn is not None:
                try:
                    self._prune_empty_group(canceled_turn['row'])
                except Exception:
                    pass

    def interrupt_work(self):
        """Legacy method - now redirects to interrupt_response"""
        self.interrupt_response()

    def interrupt_voice(self):
        """Skip/stop ALL pending speech — clears the queue and interrupts the
        current utterance (whether still synthesizing or already playing)."""
        self.controller.voice_handler.stop_all()
        self.voice_interrupt_btn.hide()

    def show_thinking(self):
        """Show thinking animation.
        Interrupt btn enabled only when work.interpreter.is_running is True during work mode;
        tooltip toggles between 'Interrupt work' and 'Cancel AI response'."""
        self.start_thinking_animation()
        self._start_title_spinner()
        self.thinking_label_shown = True
        self.set_input_enabled(False)
        self.send_btn.hide()
        self.interrupt_btn.show()
        # Always cancellable while shown — including the work-mode "thinking" gap
        # (code finished, AI analyzing) and while awaiting the model's next step.
        self.interrupt_btn.setEnabled(True)
        self.interrupt_btn.setToolTip(
            "Interrupt work" if self.controller.ai.tool_manager.work.is_working
            else "Cancel AI response"
        )
        self.show_thinking_bubble()
        # Show work banner if already in work mode
        if hasattr(self, '_work_banner') and self.controller.ai.tool_manager.work.is_working:
            if not self._work_banner.text():
                self._work_banner.setText("Working…")
            self._work_banner.show()

    def hide_thinking(self):
        """Hide thinking animation"""
        self.stop_thinking_animation()
        self._stop_title_spinner()
        self.thinking_label_shown = False
        self.set_input_enabled(True)
        self.interrupt_btn.hide()
        self.send_btn.show()
        self.hide_thinking_bubble()
        # Clear and hide the work mode banner
        if hasattr(self, '_work_banner'):
            self._work_banner.setText("")
            self._work_banner.hide()

    def show_thinking_bubble(self):
        """Show the seamless in-turn typing indicator: three animated dots at
        the BOTTOM of the current assistant turn shell — same avatar/name shell
        the reply lands in, so there's no separate bubble and no layout jump.
        The dots ride below whatever segments (text / tool / event cards) the
        turn accumulates (see _insert_turn_segment) and vanish when it ends."""
        if self._thinking_bubble_widget is not None:
            return  # Already showing
        try:
            g = self._ensure_ai_turn_group()
            dots = _TypingDots(color='#8B949E', dot_r=3)
            # Hosted in a thin left-aligned row so the dots hug the shell's
            # left edge like the text does, not center.
            host = QFrame()
            host.setObjectName("thinkDotsHost")
            host.setStyleSheet("QFrame#thinkDotsHost { background: transparent; }")
            _hl = QHBoxLayout(host)
            _hl.setContentsMargins(2, 2, 2, 2)
            _hl.setSpacing(0)
            _hl.addWidget(dots)
            _hl.addStretch()
            g['body_layout'].addWidget(host)
            self._thinking_bubble_widget = host
            self._thinking_bubble_label = dots
            self._thinking_bubble_group = g
            self.scroll_to_bottom()
        except Exception:
            log.warning("[ChatWindow.show_thinking_bubble] failed", exc_info=True)
            self._thinking_bubble_widget = None
            self._thinking_bubble_label = None

    def _move_thinking_dots_to_bottom(self):
        """Keep the dots at the END of the turn shell body as new segments are
        appended, so the indicator always rides the bottom of the growing turn.
        Called from _insert_turn_segment.

        A system interjection (notification card) closes the turn the dots were
        living in — the next segment opens a FRESH shell. So the dots are also
        MIGRATED into the current shell whenever it differs, otherwise they
        strand on the pre-split bubble instead of the newest one."""
        host = self._thinking_bubble_widget
        old = getattr(self, '_thinking_bubble_group', None)
        cur = getattr(self, '_ai_turn_group', None) or old
        if host is None or cur is None:
            return
        try:
            body = cur['body_layout']
            if body is None:
                return
            if cur is old and host.parent() is None:
                return          # detached husk, nothing to reorder
            if old is not None and old is not cur:
                try:
                    old['body_layout'].removeWidget(host)
                except (RuntimeError, KeyError, TypeError):
                    pass
            body.removeWidget(host)
            body.addWidget(host)
            self._thinking_bubble_group = cur
        except RuntimeError:
            return
        if old is not None and old is not cur:
            try:
                self._prune_empty_group(old['row'])   # no husk left behind
            except Exception:
                pass

    def _rehome_thinking_dots(self):
        """Move the typing indicator into a FRESH turn shell at the bottom of
        the chat. Called right after a system interjection is inserted so the
        dots immediately follow the notification card instead of sitting on the
        bubble that card just split off. No-op when no dots are showing."""
        if self._thinking_bubble_widget is None:
            return
        try:
            self._ensure_ai_turn_group()      # closed by the interjection → new shell
            self._move_thinking_dots_to_bottom()
            self.scroll_to_bottom()
        except Exception:
            log.debug("[ChatWindow._rehome_thinking_dots] skipped", exc_info=True)

    def hide_thinking_bubble(self):
        """Remove the in-turn typing indicator. If the dots were the ONLY thing
        in the turn shell (an empty response never produced a segment), prune
        the husk so no avatar+name shell lingers."""
        dots = self._thinking_bubble_label
        if dots is not None:
            try:
                dots.stop()
            except (RuntimeError, AttributeError):
                pass
        g = getattr(self, '_thinking_bubble_group', None)
        if self._thinking_bubble_widget is not None:
            widget = self._thinking_bubble_widget
            self._thinking_bubble_widget = None
            self._thinking_bubble_label = None
            self._thinking_bubble_group = None
            try:
                widget.setParent(None)
                widget.deleteLater()
            except RuntimeError:
                pass
            if g is not None:
                try:
                    self._prune_empty_group(g['row'])
                except Exception:
                    pass

    def resizeEvent(self, event):
        """Handle window resize."""
        super().resizeEvent(event)  # handles mask, resize handles, save timer

        if hasattr(self, 'chat_widget'):
            self.chat_widget.updateGeometry()

        if hasattr(self, 'sidebar') and hasattr(self, 'container'):
            container_h = self.container.height()
            sw = getattr(self, '_sidebar_w', SIDEBAR_DEFAULT_W)
            if self.sidebar_visible:
                self.sidebar.setGeometry(0, 0, sw, container_h)
            else:
                self.sidebar.setGeometry(-sw, 0, sw, container_h)

        self._position_window_controls()
        self._raise_window_controls()

        # Debounced content re-fit: bubbles/shells carry fixed widths computed
        # for the OLD window size — re-clamp them once the resize settles so
        # everything adapts to the new space (no overflow past the edge).
        if not hasattr(self, '_reflow_timer'):
            self._reflow_timer = QTimer(self)
            self._reflow_timer.setSingleShot(True)
            self._reflow_timer.timeout.connect(self._reflow_bubbles)
        self._reflow_timer.stop()
        self._reflow_timer.start(120)

    def moveEvent(self, event):
        """Persist position when the window is dragged (debounced)."""
        super().moveEvent(event)
        # Ignore moves before the saved geometry has been restored, so an early
        # default-position move doesn't overwrite the user's saved location.
        if getattr(self, '_geometry_restored', False) and hasattr(self, 'resize_timer'):
            self.resize_timer.stop()
            self.resize_timer.start(600)

    def save_window_geometry(self):
        """Save window size and position to config"""
        # Don't save until the saved geometry has been restored, otherwise the
        # transient default geometry during startup would clobber the real value.
        if not getattr(self, '_geometry_restored', False):
            return
        if self.isMinimized() or self.isMaximized():
            return  # don't persist a minimized/maximized transient geometry
        try:
            config = _app_config.load_section('chat_window_config')
            config['window_geometry'] = {
                'x': self.x(),
                'y': self.y(),
                'width': self.width(),
                'height': self.height()
            }
            _app_config.save_section('chat_window_config', config)
        except Exception as e:
            log.error(f"[ChatWindow.save_window_geometry] Error saving window geometry: {e}")

    def load_window_geometry(self):
        """Load window size and position from config"""
        try:
            geometry = _app_config.load_section('chat_window_config').get('window_geometry')
            if geometry:
                self.setGeometry(
                    geometry['x'],
                    geometry['y'],
                    geometry['width'],
                    geometry['height']
                )
        except Exception as e:
            log.error(f"[ChatWindow.load_window_geometry] Error loading window geometry: {e}")
        finally:
            # Restore is done (even if there was nothing to restore) — from now on
            # move/resize should persist.
            self._geometry_restored = True

    def eventFilter(self, obj, event):
        """Handle resize handle events and smooth scroll viewport events."""
        from PyQt6.QtCore import QEvent

        # ── Re-anchor the floating input when the chat area resizes ─────────
        # (fires with the correct new width, unlike the window resizeEvent).
        if obj is getattr(self, '_chat_container', None):
            if event.type() == QEvent.Type.Resize:
                self._position_input_overlay()
                nav = getattr(self, '_msg_navigator', None)
                if nav is not None:
                    nav.reposition()

        # ── Smooth inertia scroll — SIDEBAR viewport ───────────────────────
        if hasattr(self, 'sidebar_scroll') and obj is self.sidebar_scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                # Handled by the shared SmoothScroller (installed on show).
                return False

            elif event.type() == QEvent.Type.MouseButtonPress:
                # A click takes the wheel — stop any glide dead.
                try:
                    from systema.ui.widgets.smooth_scroll import scroller_for
                    s = scroller_for(self.sidebar_scroll)
                    if s is not None:
                        s.stop()
                except (ImportError, RuntimeError):
                    pass

        # ── Smooth inertia scroll — INPUT FIELD viewport ───────────────────
        if hasattr(self, 'input_field') and obj is self.input_field.text_input.viewport():
            if event.type() == QEvent.Type.Wheel:
                return False        # shared SmoothScroller owns the wheel

        # ── Smooth inertia scroll — MAIN CHAT viewport ────────────────────
        if hasattr(self, 'chat_scroll_area') and obj is self.chat_scroll_area.viewport():
            # ── Drag-to-move strip: the top DRAG_STRIP_H px replace the old
            #    title bar as the window drag surface ─────────────────────────
            if event.type() == QEvent.Type.MouseButtonPress:
                if (event.button() == Qt.MouseButton.LeftButton
                        and event.position().y() <= self.DRAG_STRIP_H):
                    self._drag_strip_active = True
                    self.header_mouse_press(event)
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if getattr(self, '_drag_strip_active', False):
                    self.header_mouse_move(event)
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if getattr(self, '_drag_strip_active', False):
                    self._drag_strip_active = False
                    self.header_mouse_release(event)
                    return True

            if event.type() == QEvent.Type.Wheel:
                # Ctrl+Scroll → zoom in / out
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self.zoom_in()
                    else:
                        self.zoom_out()
                    return True
                # Plain wheel is handled by the shared SmoothScroller installed
                # on this viewport (see _install_smooth_scrolling). Let it fall
                # through — the old velocity/friction inertia moved ~326px per
                # notch, which made positions less than a third of a viewport
                # away impossible to land on.
                return False

            elif event.type() == QEvent.Type.MouseButtonPress:
                # A click is the user taking the wheel: kill any glide in
                # flight so the view stops dead under the cursor.
                self._user_scrolling = True
                s = self._chat_scroller()
                if s is not None:
                    try:
                        s.stop()
                    except RuntimeError:
                        pass

        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):
        """Handle drag enter"""
        log.debug(f"[ChatWindow.dragEnterEvent] hasUrls={event.mimeData().hasUrls()} "
                  f"formats={event.mimeData().formats()[:6]}")
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle file drop — supports multiple files."""
        raw_files = [u.toLocalFile() for u in event.mimeData().urls()]
        log.debug(f"[ChatWindow.dropEvent] {len(raw_files)} file(s) dropped")
        self._ingest_dropped_files(raw_files)

    def _ingest_dropped_files(self, raw_files):
        """Shared drop ingestion — the Qt dropEvent AND the elevated
        WM_DROPFILES bridge both land here: images → attach prompt, other
        files → quoted path into the input, multiple files supported."""
        raw_files = [p for p in raw_files if p]
        if not raw_files:
            return

        valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        image_files = [self.clean_file_path(p) for p in raw_files
                       if any(p.lower().endswith(ext) for ext in valid_extensions)]
        non_images  = [self.clean_file_path(p) for p in raw_files
                       if not any(p.lower().endswith(ext) for ext in valid_extensions)]

        if image_files:
            if len(image_files) == 1:
                self._handle_image_file_drop(image_files[0])
            else:
                self._handle_multiple_image_files_dialog(image_files)

        for file_path in non_images:
            if self.should_quote_path(file_path):
                file_path = f'"{file_path}"'
            current_text = self.input_field.toPlainText()
            if current_text:
                self.input_field.text_input.setPlainText(current_text + "\n" + file_path)
            else:
                self.input_field.text_input.setPlainText(file_path)

    def clean_file_path(self, path):
        """Normalize a pasted/dropped path. Strips surrounding quotes (Windows
        "Copy as path" wraps in them — they silently broke the os.path.exists
        checks and killed the image-attach prompt), unwraps file:// URIs (KDE/
        GNOME drops, %-decoded), and converts separators ONLY on Windows — the
        old unconditional '/'→'\\' replace corrupted POSIX paths."""
        path = path.strip().strip('"').strip("'")
        if path.startswith('file://'):
            from urllib.parse import unquote
            path = unquote(path)
            # file:///C:/x → C:/x on Windows; file:///home/x → /home/x on POSIX.
            path = path[8:] if sys.platform == 'win32' else path[7:]
        if sys.platform == 'win32':
            path = path.replace('/', '\\')
        return path

    def should_quote_path(self, path):
        """Check if path should be quoted (not an image)"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
        return not any(path.lower().endswith(ext) for ext in image_extensions)

    def keyPressEvent(self, event):
        """Handle paste of file paths, zoom shortcuts and Esc-to-interrupt"""
        # ── Esc interrupts whatever is in flight ────────────────────────────
        # Same action as the Stop button, so it inherits its rules: mid-work it
        # opens the interrupt dialog, otherwise it cancels the response. Gated
        # on the button's own visible+enabled state, so Esc does nothing when
        # there is nothing to stop (and never eats the key from a child).
        # QTextEdit ignores Escape, so this still fires while you are typing
        # ahead in the input box.
        if event.key() == Qt.Key.Key_Escape:
            btn = getattr(self, 'interrupt_btn', None)
            try:
                live = btn is not None and btn.isVisible() and btn.isEnabled()
            except RuntimeError:
                live = False
            if live:
                self.interrupt_response()
                event.accept()
                return

        # Ctrl++ / Ctrl+= → zoom in
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self.zoom_in()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Minus:
                self.zoom_out()
                event.accept()
                return
            if event.key() == Qt.Key.Key_0:
                self.chat_zoom = 1.0
                self._apply_zoom_all()
                self.save_config()
                event.accept()
                return

        if event.key() == Qt.Key.Key_V and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            clipboard = QApplication.clipboard()
            text = clipboard.text().strip()

            # ── Multi-path support: clipboard may contain multiple lines ─────
            valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif']
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            existing_paths = [self.clean_file_path(l) for l in lines if os.path.exists(self.clean_file_path(l))]

            if len(existing_paths) > 1:
                image_files = [p for p in existing_paths if any(p.lower().endswith(e) for e in valid_extensions)]
                non_images = [p for p in existing_paths if p not in image_files]
                if image_files:
                    self._handle_multiple_image_files_dialog(image_files)
                for file_path in non_images:
                    if self.should_quote_path(file_path):
                        file_path = f'"{file_path}"'
                    self.input_field.text_input.insertPlainText(file_path + "\n")
                event.accept()
                return

            # ── Single path fallback ──────────────────────────────────────────
            cleaned_path = self.clean_file_path(text)

            if os.path.exists(cleaned_path):
                if any(cleaned_path.lower().endswith(ext) for ext in valid_extensions):
                    self._handle_image_file_drop(cleaned_path)
                    event.accept()
                    return

                if self.should_quote_path(cleaned_path):
                    cleaned_path = f'"{cleaned_path}"'

                self.input_field.text_input.insertPlainText(cleaned_path)
                event.accept()
                return

        super().keyPressEvent(event)

    # ═══════════════════════════════════════════════════════════════════════════
    # ZOOM METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _open_manage_tasks_window(self):
        """Open the task management window."""
        try:
            from systema.ui.windows.manage_tasks_window import ManageTasksWindow
            if not hasattr(self, '_tasks_window') or self._tasks_window is None:
                self._tasks_window = ManageTasksWindow(self.controller)
            self._tasks_window.show()
            self._tasks_window.raise_()
            self._tasks_window.activateWindow()
        except Exception as e:
            self.add_system_message(f"⚠️ Could not open Manage Tasks window: {e}")

    def _open_memory_window(self):
        """Open the memory management window."""
        try:
            from systema.ui.windows.memory_window import MemoryWindow
            if not hasattr(self, '_memory_window') or self._memory_window is None:
                self._memory_window = MemoryWindow(self.controller)
            self._memory_window.show()
            self._memory_window.raise_()
            self._memory_window.activateWindow()
        except Exception as e:
            self.add_system_message(f"⚠️ Could not open Memory window: {e}")

    def _open_logs_window(self):
        """Open the session log browser (`/logs`, and the Debug window's button).

        Reuses one instance and reloads it, so reopening reflects a session
        switch or a restart that started a new log file.
        """
        try:
            from systema.ui.windows.logs_window import LogsWindow
            if getattr(self, '_logs_window', None) is None:
                self._logs_window = LogsWindow(self.controller)
            else:
                self._logs_window.reload()
            self._logs_window.show()
            self._logs_window.raise_()
            self._logs_window.activateWindow()
        except Exception as e:
            self.add_system_message(f"Could not open the Logs window: {e}")

    def is_elevated(self) -> bool:
        """Running as administrator (Windows) / root (POSIX)?

        Cached: the greeting banner asks on every new session, and a WinAPI
        call per banner is pointless — elevation cannot change without a
        restart.
        """
        cached = getattr(self, '_is_elevated_cache', None)
        if cached is not None:
            return cached
        elevated = False
        try:
            if sys.platform == "win32":
                import ctypes
                elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
            else:
                elevated = (os.geteuid() == 0)
        except Exception:
            log.warning("[ChatWindow.is_elevated] Elevated check failed", exc_info=True)
        self._is_elevated_cache = elevated
        return elevated

    def check_admin_mode(self):
        """Retained for callers that still ask; the notice itself now lives as
        a varied subtitle inside the greeting banner (see add_greeting_banner).

        It used to add its own grey system line under the banner. Two stacked
        notices made the opener look cluttered and pushed the greeting
        off-centre, and the line never varied.
        """
        return self.is_elevated()