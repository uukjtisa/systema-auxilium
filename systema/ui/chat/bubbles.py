"""
systema/ui/chat/bubbles.py
BubblesMixin — message bubbles, avatars, message menu.
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
from systema.ui.widgets.code_blocks import CodeBlockWidget, TableBlockWidget

_verbose = True
log = _make_logger("ChatWindow") if _verbose else _NoOpLogger()


class _TypingDots(QWidget):
    """Seamless in-turn 'thinking' indicator: three dots bobbing on offset
    sine phases, painted on a transparent background so it blends into the
    turn shell (no separate bubble). Sits at the bottom of the growing turn
    and is removed the moment the reply finishes."""

    def __init__(self, color: str = '#8B949E', dot_r: int = 3, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = QColor(color)
        self._r = dot_r
        self._phase = 0.0
        gap = dot_r * 4
        self.setFixedSize(gap * 3 + dot_r * 2, dot_r * 2 + 10)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(33)   # ~30 fps

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def _advance(self):
        self._phase += 0.22
        self.update()

    def stop(self):
        try:
            self._timer.stop()
        except RuntimeError:
            pass

    def paintEvent(self, event):
        import math
        from PyQt6.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gap = self._r * 4
        cy = self.height() / 2
        amp = 3.0
        for i in range(3):
            off = math.sin(self._phase - i * 0.9) * amp
            c = QColor(self._color)
            # Subtle brightness bob synced with the vertical bob.
            c.setAlpha(int(150 + 90 * (0.5 + 0.5 * math.sin(self._phase - i * 0.9))))
            p.setBrush(c)
            cx = self._r + i * gap + self._r
            p.drawEllipse(QPoint(int(cx), int(cy + off)), self._r, self._r)
        p.end()


def make_circular_pixmap(src: QPixmap, size: int) -> QPixmap:
    """Center-crop `src` (aspect-fill) and clip it to a crisp circle at `size`.
    The single source of truth for circular avatar rendering — always render
    from the full-resolution square master at the TARGET size; never rescale
    an already-clipped circle (that's what caused the soft/ragged pfp edges).

    DPI-aware: renders at size × devicePixelRatio and tags the pixmap with the
    ratio, so circles stay crisp on scaled displays (125% / 150% Windows)."""
    from PyQt6.QtGui import QPainter, QPainterPath
    from PyQt6.QtCore import QRectF
    try:
        dpr = float(QApplication.primaryScreen().devicePixelRatio())
    except Exception:
        dpr = 1.0
    dpr = max(1.0, dpr)
    px = max(1, int(round(size * dpr)))
    scaled = src.scaled(px, px,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, px, px))
    painter.setClipPath(clip)
    # centered draw — a non-square source crops equally from both sides
    painter.drawPixmap((px - scaled.width()) // 2,
                       (px - scaled.height()) // 2, scaled)
    painter.end()
    out.setDevicePixelRatio(dpr)
    return out


BUBBLE_STYLE_DEFAULT = 'blend'   # 'blend' (borderless, default) | 'compact' (bordered, fits content)

import re as _re
# Colored emoji/pictographs stripped from SYSTEM notes at display time (clean
# professional line, user request 2026-07-19). Monochrome glyphs (⚠ ✓ ⟳ …)
# survive — only the emoji planes, colored BMP symbols, and the FE0F
# emoji-presentation selector go. History keeps the original text.
_EMOJI_RX = _re.compile(
    '[\\U0001F000-\\U0001FBFF'          # emoji planes (pictographs, symbols)
    '\\u231A\\u231B\\u23E9-\\u23FA'     # watches, media/clock symbols
    '\\u2705\\u2728\\u274C\\u274E'      # colored checkmarks/crosses
    '\\u2753-\\u2755\\u2757'            # colored question/exclamation marks
    '\\u2B05-\\u2B07\\u2B1B\\u2B1C\\u2B50\\u2B55'  # colored arrows/stars/shapes
    '\\uFE0F\\u200D]'                   # emoji presentation selector, ZWJ
)


def _strip_emoji(text: str) -> str:
    """Display-side emoji removal for system notes; tidies leftover spacing."""
    try:
        s = _EMOJI_RX.sub('', text or '')
        return '\n'.join(_re.sub(r' {2,}', ' ', ln).strip()
                         for ln in s.split('\n'))
    except Exception:
        return text


def bubble_wrapper_css(role: str, t: dict, style: str, glass: bool = False,
                       in_group: bool = False) -> str:
    """Stylesheet for a message bubble's content_wrapper QFrame — the single
    source of truth used by the builders, apply_theme, glass mode, and the
    live style switch.

    blend   — AI text sits directly on the backdrop (no box); user gets a soft
              borderless fill. In GLASS mode the AI text gets a smoked panel
              (plus window acrylic, see _sync_glass_effects) so it stays
              readable over the desktop.
    compact — WhatsApp-like: bordered, hugs content width, corner tail.
    in_group — assistant segment inside a TURN SHELL (grouped work turn): in
              compact style the SHELL carries the border/bg, so the segment
              itself goes transparent (mock: .style-b .turn-body .bubble.ai).

    All rules are SCOPED to #bubbleWrap (builders set the objectName) so they
    never cascade into nested QFrames (code blocks, tables).
    """
    if style == 'compact':
        if role == 'assistant' and in_group:
            # the shell carries the border/bg (glass or not) — segment is clear
            return "QFrame#bubbleWrap { background-color: transparent; border: none; border-radius: 0px; }"
        if glass:
            bg = 'rgba(37,37,37,0.55)' if role == 'user' else 'rgba(42,42,42,0.55)'
            border = '1px solid rgba(60,60,60,0.4)'
        else:
            bg = t['surface'] if role == 'user' else t['elevated']
            border = f"1px solid {t['border']}"
        tail = ('border-bottom-right-radius: 4px;' if role == 'user'
                else 'border-top-left-radius: 4px;')
        return (f"QFrame#bubbleWrap {{ background-color: {bg}; border: {border}; "
                f"border-radius: 14px; {tail} }}")
    # ── blend ────────────────────────────────────────────────────────────────
    if role == 'user':
        bg = 'rgba(37,37,37,0.60)' if glass else '#151515'
        return f"QFrame#bubbleWrap {{ background-color: {bg}; border: none; border-radius: 16px; }}"
    if glass:
        # frosted smoked panel behind AI text (user-flagged glass backup)
        return ("QFrame#bubbleWrap { background-color: rgba(16,16,16,0.78); "
                "border: none; border-radius: 16px; }")
    return "QFrame#bubbleWrap { background-color: transparent; border: none; border-radius: 0px; }"


def bubble_wrapper_margins(role: str, style: str, glass: bool = False,
                           in_group: bool = False) -> tuple:
    """Content margins for the bubble wrapper, per role/style/glass.
    BLEND is deliberately TIGHT (boxless text needs no breathing room of its
    own); compact keeps the roomier visible-bubble padding. USER bubbles pad
    WhatsApp-tight and SYMMETRIC — the old 12-top/8-bottom read as off-center
    text."""
    if role == 'assistant':
        if style == 'blend' and not glass:
            return (0, 2, 0, 2)      # boxless — hugs the flow
        if style == 'compact' and in_group:
            return (0, 4, 0, 4)      # the shell provides the padding
        return (10, 10, 10, 10)
    return (11, 7, 11, 7)


def group_body_spacing(style: str) -> int:
    """Gap between segments inside a turn shell — tight for blend."""
    return 4 if style == 'blend' else 6


def group_shell_css(t: dict, style: str, glass: bool = False) -> str:
    """Stylesheet for an assistant TURN SHELL — the single container holding
    every segment (texts + tool/file cards) of one AI work turn.

    blend   — invisible: the segments carry the whole look.
    compact — the whole turn reads as ONE bordered card; the old per-bubble
              border + top-left tail move up here (mock: .style-b .turn-body).
    Scoped to #turnShell so it never cascades into child QFrames (chips,
    wrappers, code blocks)."""
    if style == 'compact':
        if glass:
            bg = 'rgba(42,42,42,0.55)'
            border = '1px solid rgba(60,60,60,0.4)'
        else:
            bg = t['elevated']
            border = f"1px solid {t['border']}"
        return (f"QFrame#turnShell {{ background-color: {bg}; border: {border}; "
                f"border-radius: 14px; border-top-left-radius: 4px; }}")
    return "QFrame#turnShell { background-color: transparent; border: none; }"


def group_shell_margins(style: str) -> tuple:
    """Body margins inside the turn shell."""
    return (10, 8, 12, 8) if style == 'compact' else (0, 0, 0, 0)


class BubblesMixin:
    """Message bubble builders + message actions (mixed into ChatWindow)."""

    # ── Bubble style (blend | compact) ───────────────────────────────────────

    def _bubble_style(self) -> str:
        """Current bubble style from settings; defaults to 'blend'."""
        try:
            s = self.controller.settings.get('chat_bubble_style', BUBBLE_STYLE_DEFAULT)
        except Exception:
            s = BUBBLE_STYLE_DEFAULT
        return s if s in ('blend', 'compact') else BUBBLE_STYLE_DEFAULT

    def apply_bubble_style(self, style: str = None):
        """Restyle every existing user/assistant bubble in place (live switch
        from Settings, and re-applied by apply_theme / glass toggles)."""
        style = style or self._bubble_style()
        t = self._t()
        glass = getattr(self, '_glass_enabled', False)
        seen_shells = set()
        for md in self.message_widgets:
            role = md.get('role', '')
            # ── Turn shells (one per grouped AI turn — restyle each once) ──
            grow = md.get('group_row')
            if grow is not None and id(grow) not in seen_shells:
                seen_shells.add(id(grow))
                try:
                    shell = grow.findChild(QFrame, "turnShell")
                    if shell is not None:
                        shell.setStyleSheet(group_shell_css(t, style, glass=glass))
                        if shell.layout() is not None:
                            shell.layout().setContentsMargins(*group_shell_margins(style))
                            shell.layout().setSpacing(group_body_spacing(style))
                        if style == 'compact':
                            self._refit_turn_shell(shell)   # explicit hug width
                        else:
                            # blend fills the column — clear any fixed width
                            # AND its stored hug width, or the _reflow_bubbles
                            # call below immediately re-fixes the shell.
                            shell._natural_w = None
                            shell.setMinimumWidth(0)
                            shell.setMaximumWidth(16777215)
                            shell.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                QSizePolicy.Policy.Preferred)
                except RuntimeError:
                    pass
            if role not in ('user', 'assistant'):
                continue
            cw = md.get('content_wrapper')
            if cw is None:
                continue
            in_group = role == 'assistant' and grow is not None
            try:
                cw.setStyleSheet(bubble_wrapper_css(role, t, style, glass=glass,
                                                    in_group=in_group))
                # User bubbles ALWAYS hug their content (mock parity);
                # assistant segments hug only in standalone compact mode.
                if role == 'user' or (style == 'compact' and not in_group):
                    cw.setSizePolicy(QSizePolicy.Policy.Maximum,
                                     QSizePolicy.Policy.Preferred)
                else:
                    cw.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Preferred)
                lay = md.get('content_wrapper_layout')
                if lay is not None:
                    lay.setContentsMargins(*bubble_wrapper_margins(
                        role, style, glass, in_group=in_group))
            except RuntimeError:
                pass
        self._reflow_bubbles()
        self._sync_glass_effects()

    def _bubble_max_width(self) -> int:
        """Max width for a message bubble — responsive to the chat viewport but
        capped so bubbles never span an ultra-wide window (readability). Grows
        and shrinks with the window; scales the cap with the chat zoom level."""
        try:
            vw = self.chat_scroll_area.viewport().width()
        except Exception:
            vw = 0
        if vw <= 0:
            vw = 800
        zoom = getattr(self, 'chat_zoom', 1.0) or 1.0
        cap = int(900 * zoom)             # readable upper bound (capped)
        # blend (borderless) reads well wider than the boxed compact style
        ratio = 0.88 if self._bubble_style() == 'blend' else 0.82
        responsive = int(vw * ratio)
        width = min(responsive, cap)
        # Reserve a right-edge gutter for the message navigator rail while it is
        # visible, so a full-width message's trailing ⋯ button never lands
        # UNDER the rail (where the overlay would swallow its clicks).
        nav_gutter = 0
        try:
            nav = getattr(self, '_msg_navigator', None)
            if nav is not None and nav.isVisible():
                nav_gutter = 40
        except Exception:
            nav_gutter = 0
        # Never exceed the actual viewport (minus a small margin) — otherwise a
        # narrow window pushes bubbles / cards off-screen. The 320 readability
        # floor is itself clamped to what the viewport can hold.
        hard_max = max(200, vw - 24 - nav_gutter)
        return max(min(320, hard_max), min(width, hard_max))

    def _reflow_bubbles(self):
        """Re-apply the responsive max width to every existing bubble. Cheap —
        setMaximumWidth only triggers a relayout when the value actually changes."""
        if not hasattr(self, 'message_widgets'):
            return
        maxw = self._bubble_max_width()
        style = self._bubble_style()
        for md in self.message_widgets:
            b = md.get('main_container_widget')
            if b is not None:
                try:
                    b.setMaximumWidth(maxw)
                except RuntimeError:
                    pass
            # Exact-fit bubbles (user messages): re-clamp their fixed width
            # against the new cap so a shrunken window can't cut them off.
            cw = md.get('content_wrapper')
            nat = getattr(cw, '_natural_w', None)
            if nat:
                try:
                    cap = (self._user_bubble_cap() if md.get('role') == 'user'
                           else self._assistant_seg_cap())
                    cw.setFixedWidth(min(nat, cap))
                except RuntimeError:
                    pass
            # Compact turn shells: same re-clamp (explicit hug width).
            # COMPACT ONLY — blend shells fill the column; re-fixing one from
            # a stale compact _natural_w after a live style toggle would clip
            # the (wider-capped) blend wrappers inside it.
            grow = md.get('group_row')
            if grow is not None and style == 'compact':
                try:
                    shell = grow.findChild(QFrame, "turnShell")
                    snat = getattr(shell, '_natural_w', None) if shell else None
                    if snat:
                        shell.setFixedWidth(max(64, min(snat, maxw)))
                except RuntimeError:
                    pass  # widget was deleted
            # Re-clamp any manually-resized code blocks so they can't stay wider
            # than the (possibly shrunk) bubble and push their grip off-screen.
            w = md.get('widget')
            if w is not None:
                try:
                    for cb in w.findChildren(CodeBlockWidget):
                        cb.clamp_width()
                    for tb in w.findChildren(TableBlockWidget):
                        tb.clamp_width()
                except RuntimeError:
                    pass

    def _animate_message_in(self, widget, on_settled=None):
        """
        Slide-open + fade-in a new message widget.
        Uses OutBack easing for a satisfying spring overshoot.
        Timings controlled by ANIM_MSG_IN_* constants at top of file.

        on_settled: optional callable fired AFTER the animation completes and
                    the widget is unconstrained.  Use this to trigger scroll
                    centering — at that point sb.maximum() and widget.height()
                    are accurate.
        """
        natural_h = widget.sizeHint().height()
        if natural_h < 10:
            natural_h = 300

        # Start collapsed + invisible
        widget.setMaximumHeight(0)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        # Height: 0 → natural + overshoot (OutBack gives the spring feel)
        height_anim = QPropertyAnimation(widget, b"maximumHeight")
        height_anim.setDuration(ANIM_MSG_IN_HEIGHT_MS)
        height_anim.setStartValue(0)
        height_anim.setEndValue(natural_h + ANIM_MSG_IN_OVERSHOOT_PX)
        height_anim.setEasingCurve(QEasingCurve.Type.OutBack)

        # Opacity: 0 → 1
        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(ANIM_MSG_IN_FADE_MS)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(widget)
        group.addAnimation(height_anim)
        group.addAnimation(fade_anim)

        def _on_done():
            widget.setMaximumHeight(16777215)   # Qt QWIDGETSIZE_MAX — unconstrain
            widget.setGraphicsEffect(None)
            # Fire the scroll callback now that layout is fully settled.
            # A tiny extra delay lets Qt flush the layout update so
            # sb.maximum() and widget.height() are guaranteed correct.
            if on_settled is not None:
                QTimer.singleShot(30, on_settled)

        group.finished.connect(_on_done)

        widget._anim_in_group = group
        widget._anim_in_effect = effect
        group.start()

    def _animate_message_out(self, widget, callback):
        """
        Fade-out + collapse height of a message widget, then fire callback.
        Timings controlled by ANIM_MSG_OUT_* constants at top of file.
        """
        if hasattr(widget, '_anim_in_group'):
            try:
                widget._anim_in_group.stop()
            except RuntimeError:
                pass
            widget.setMaximumHeight(16777215)
            widget.setGraphicsEffect(None)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        widget.setGraphicsEffect(effect)

        fade_anim = QPropertyAnimation(effect, b"opacity")
        fade_anim.setDuration(ANIM_MSG_OUT_FADE_MS)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        current_h = max(widget.height(), 10)
        height_anim = QPropertyAnimation(widget, b"maximumHeight")
        height_anim.setDuration(ANIM_MSG_OUT_HEIGHT_MS)
        height_anim.setStartValue(current_h)
        height_anim.setEndValue(0)
        height_anim.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QParallelAnimationGroup(widget)
        group.addAnimation(fade_anim)
        group.addAnimation(height_anim)
        group.finished.connect(callback)

        widget._anim_out_group = group
        widget._anim_out_effect = effect
        group.start()

    def _make_chat_avatar(self, target: str, size: int = 32) -> QLabel:
        """Create a 32px circular avatar label using pixmap if set, else emoji."""
        av = QLabel()
        av.setFixedSize(size, size)
        av.setAlignment(Qt.AlignmentFlag.AlignCenter)

        is_bot = (target == 'bot')
        pixmap = getattr(self, '_bot_avatar_pixmap' if is_bot else '_user_avatar_pixmap', None)

        if pixmap and not pixmap.isNull():
            # Render the circle at the TARGET size from the square master —
            # center-cropped, crisp edge at any avatar size (54, 26, ...).
            av.setPixmap(make_circular_pixmap(pixmap, size))
            av.setStyleSheet(f"""
                QLabel {{
                    border-radius: {size//2}px;
                    min-width: {size}px; min-height: {size}px;
                    max-width: {size}px; max-height: {size}px;
                }}
            """)
        else:
            emoji = (self.bot_avatar or '🤖') if is_bot else (self.user_avatar or '👤')
            bg = "#58A6FF" if is_bot else "#34A853"
            av.setText(emoji)
            av.setStyleSheet(f"""
                QLabel {{
                    background-color: {bg};
                    border-radius: {size//2}px;
                    font-size: {size//2 - 2}px;
                    min-width: {size}px; min-height: {size}px;
                    max-width: {size}px; max-height: {size}px;
                }}
            """)
        return av

    # ── Hover action row (📋 ⋯) — replaces the old in-bubble copy button and
    #    the stray ⋯ under the bubble (2026-07 redesign) ───────────────────────

    def _make_action_row(self, align_left: bool):
        """Compact action row under a message: a SINGLE ⋯ button (Copy now lives
        inside its menu), faint (25%) until the message row is hovered. Returns
        (row_widget, menu_btn, opacity_effect)."""
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(2)

        from systema.ui.chat.window_controls import DotsButton
        menu_btn = DotsButton(22)
        menu_btn.setToolTip("Message actions")
        if align_left:
            lay.addWidget(menu_btn)
            lay.addStretch()
        else:
            lay.addStretch()
            lay.addWidget(menu_btn)

        eff = QGraphicsOpacityEffect(row)
        eff.setOpacity(0.25)
        row.setGraphicsEffect(eff)
        return row, menu_btn, eff

    def _wire_action_row_hover(self, message_widget, effect):
        """Swing the action row to full opacity while the message is hovered."""
        def _enter(e, eff=effect):
            try:
                eff.setOpacity(1.0)
            except RuntimeError:
                pass
        def _leave(e, eff=effect):
            try:
                eff.setOpacity(0.25)
            except RuntimeError:
                pass
        message_widget.enterEvent = _enter
        message_widget.leaveEvent = _leave

    # ── Exact-fit bubble width ───────────────────────────────────────────────

    def _user_bubble_cap(self) -> int:
        """Width available to a USER bubble: the responsive cap minus the row
        chrome sharing its line (avatar + spacing + action row + margins) — a
        fixed-width bubble that ignores its neighbors clips past the window
        edge instead of wrapping.

        The ACTION_GUTTER is critical: the ⋯ action row lives INSIDE the same
        `main_container_widget` (capped at `_bubble_max_width()`) as the bubble.
        If the bubble is allowed to reach the full cap, bubble + action row
        overflow the container and Qt clips the bubble's RIGHT edge — its
        rounded corners vanish at wide windows. Reserving the gutter keeps them."""
        try:
            vw = self.chat_scroll_area.viewport().width()
        except Exception:
            vw = 800
        av = getattr(self, '_user_avatar_size', 32)
        ACTION_GUTTER = 34   # ⋯ button (22) + HBox spacing (6) + slack
        return max(160, min(self._bubble_max_width() - ACTION_GUTTER, vw - av - 96))

    # ⋯ action row (22) + segment spacing (6) + slack — the fixed chrome that
    # shares a shell row with an assistant text wrapper.
    _SEG_CHROME = 30

    def _assistant_seg_cap(self) -> int:
        """Width cap for an in-group assistant TEXT wrapper. Compact shells
        are fixed-width bordered cards: the wrapper shares the shell body with
        the ⋯ action row, so it must stay `shell margins + chrome` narrower
        than the shell's own cap — a wrapper capped at the full bubble max
        overflowed the card and Qt CLIPPED the text's right edge. Blend keeps
        the full bubble cap (that mode is untouched by design)."""
        maxw = self._bubble_max_width()
        if self._bubble_style() != 'compact':
            return maxw
        gm = group_shell_margins('compact')
        return max(160, maxw - gm[0] - gm[2] - self._SEG_CHROME)

    def _break_unbreakable(self, text: str, chunk: int = 42) -> str:
        """Insert zero-width break opportunities into very long non-space runs
        (pasted hashes, keyboard-mashing, long tokens) so the bubble label can
        wrap them within its width cap instead of stretching off-screen. URLs are
        left intact. Normal prose (runs ≤ chunk) is untouched."""
        import re
        ZWSP = '​'

        def _brk(m):
            w = m.group(0)
            if '://' in w or w.lower().startswith('www.'):
                return w  # keep URLs clickable / intact
            return ZWSP.join(w[i:i + chunk] for i in range(0, len(w), chunk))

        try:
            return re.sub(r'\S{%d,}' % (chunk + 1), _brk, text)
        except Exception:
            return text

    def _fit_bubble_width(self, content_wrapper, text_label, cap: int,
                          extra_min: int = 0):
        """Fix the bubble's width to EXACTLY its content (up to cap). Width
        becomes deterministic, so word-wrap height flows correctly — this is
        what kills both the too-wide box AND the truncated-text bug (an
        aligned wrapper is sized to a word-wrap label's heuristic sizeHint,
        which lies about height)."""
        try:
            import math
            from PyQt6.QtGui import QTextDocument
            doc = QTextDocument()
            # QLabel lays rich text out at documentMargin 0 — measuring at the
            # QTextDocument default (4) plus the old +6 slack padded every
            # bubble ~14px right of the text: the off-center, loose look.
            doc.setDocumentMargin(0)
            f = text_label.font()
            f.setPixelSize(self._get_msg_font_size())
            doc.setDefaultFont(f)
            doc.setHtml(text_label.text())
            doc.setTextWidth(-1)
            m = content_wrapper.layout().contentsMargins()
            w = math.ceil(doc.idealWidth()) + m.left() + m.right() + 2
            w = max(36, extra_min, w)
            content_wrapper._natural_w = w          # reflow refits on resize
            content_wrapper._fit_extra_min = extra_min   # image-row floor, survives refits
            content_wrapper.setFixedWidth(min(w, cap))
        except Exception:
            pass

    # ── Typing reveal (fake streaming) ───────────────────────────────────────

    def _reveal_enabled(self) -> bool:
        """Settings toggle: progressive text reveal on AI replies."""
        try:
            return bool(self.controller.settings.get('chat_text_reveal', True))
        except Exception:
            return True

    @staticmethod
    def _reveal_units(source_md: str):
        """Split reveal source into units: injected HTML snippets (rendered
        math images, placeholders) advance as ONE unit so the typewriter never
        slices through a tag or crawls through base64 char-by-char; everything
        else is per-character. Returns (units, offsets) where offsets[i] is the
        char position in source_md right after unit i."""
        import re
        pat = re.compile(
            r'(<div style="text-align:center.*?</div>'
            r'|<img\b[^>]*>'
            r'|<span\b[^>]*>[^<]*</span>)',
            re.DOTALL)
        units = []
        pos = 0
        for m in pat.finditer(source_md):
            units.extend(source_md[pos:m.start()])
            units.append(m.group(0))
            pos = m.end()
        units.extend(source_md[pos:])
        offsets = []
        total = 0
        for u in units:
            total += len(u)
            offsets.append(total)
        return units, offsets

    def _reveal_text_label(self, label, source_md: str, on_done=None):
        """Typewriter reveal: re-render growing slices of the markdown source
        into the label — looks like streaming without real streaming. The
        sticky-bottom rangeChanged hook keeps the view pinned as it grows.

        2026-07-20 rework (typing-lag fix): wall-clock pacing (a late/heavy
        tick catches up instead of slowing the reveal), atomic HTML units
        (math images pop in whole), and an adaptive render throttle — as the
        document grows and each re-render gets pricier, renders space out (up
        to ~300ms) while the cut keeps advancing, so per-frame cost stays
        bounded and long replies no longer stutter cumulatively."""
        # Only ONE reveal at a time: a new segment starting to type snaps any
        # still-running previous reveal to its full text first.
        self._finish_active_reveals()
        units, offsets = self._reveal_units(source_md)
        n = len(units)
        if n == 0:
            if on_done:
                on_done()
            return
        # Speed comes from Settings (chars/second); ~30ms frames.
        try:
            cps = int(self.controller.settings.get('chat_text_reveal_cps', 90))
        except Exception:
            cps = 90
        cps = max(15, min(400, cps))
        interval = 30
        import time as _time
        from PyQt6.QtCore import QElapsedTimer
        clock = QElapsedTimer()
        clock.start()
        state = {'cut': 0, 'render_ms': 0.0, 'since_render': 1e9}
        timer = QTimer(label)

        # Track the job so a user send can snap it to the full text instantly.
        if not hasattr(self, '_reveal_jobs'):
            self._reveal_jobs = []
        job = {'timer': timer, 'label': label, 'source': source_md,
               'on_done': on_done}
        self._reveal_jobs.append(job)

        def _forget():
            try:
                self._reveal_jobs.remove(job)
            except ValueError:
                pass

        def _tick():
            target = min(n, int(cps * clock.elapsed() / 1000))
            if target >= n:
                timer.stop()
                _forget()
                try:
                    # A background LaTeX render may have updated this message
                    # mid-reveal — the refresh parks the new source on the job.
                    label.setText(self.render_markdown(
                        job.get('post_refresh', source_md)))
                except RuntimeError:
                    return
                if on_done:
                    on_done()
                return
            if target <= state['cut']:
                return
            state['cut'] = target
            # Adaptive throttle: while renders are expensive, skip re-renders;
            # the cut keeps advancing so the next render catches up visually.
            state['since_render'] += interval
            gap = min(300.0, max(float(interval), state['render_ms'] * 2))
            if state['since_render'] < gap:
                return
            state['since_render'] = 0.0
            t0 = _time.perf_counter()
            try:
                label.setText(self.render_markdown(
                    source_md[:offsets[state['cut'] - 1]] + " ▌"))
            except RuntimeError:
                timer.stop()
                _forget()
                return
            state['render_ms'] = (_time.perf_counter() - t0) * 1000.0

        timer.timeout.connect(_tick)
        label._reveal_timer = timer   # keep alive with the label
        timer.start(interval)

    def _finish_active_reveals(self):
        """Snap every in-flight typing reveal to its full text — called when
        the user sends a message mid-animation."""
        jobs = list(getattr(self, '_reveal_jobs', []))
        self._reveal_jobs = []
        for job in jobs:
            try:
                job['timer'].stop()
            except (RuntimeError, TypeError):
                pass
            try:
                job['label'].setText(self.render_markdown(
                    job.get('post_refresh', job['source'])))
            except RuntimeError:
                continue
            if job.get('on_done'):
                try:
                    job['on_done']()
                except Exception:
                    pass

    def _refresh_message_latex(self, md):
        """Re-render a message's text labels after a background LaTeX render
        completed (the cache now hits). Labels with a reveal in flight defer
        the refresh to the reveal's finish via job['post_refresh']."""
        src = md.get('pre_latex_source')
        if src is None:
            return
        display = self._preprocess_latex(src)
        # Anything STILL missing re-registers this message for the next round.
        keys = self._take_latex_misses()
        if keys:
            self._register_latex_waiter(keys, md)
        md['display_content'] = display
        parts = self._split_message_parts(display)
        text_parts = [p[1] for p in parts if p and p[0] == 'text']
        labels = md.get('text_labels') or ([md['text_label']] if md.get('text_label') else [])
        active = {j['label']: j for j in getattr(self, '_reveal_jobs', [])}
        for label, part_src in zip(labels, text_parts):
            if label is None:
                continue
            job = active.get(label)
            if job is not None:
                job['post_refresh'] = part_src
                continue
            try:
                label.setText(self.render_markdown(part_src))
            except RuntimeError:
                pass

    # ── Assistant turn grouping (claude.ai-style, 2026-07 redesign) ─────────
    # ONE avatar + ONE name per turn: every assistant text and tool/file card
    # between two user messages stacks inside a single shell. Grouping is
    # purely VISUAL — message_widgets stays flat, so edit/delete/rewind and
    # session persistence are untouched.

    def _ensure_ai_turn_group(self) -> dict:
        """Return the live turn group, building the shell row (avatar + name
        + segment body) on the first assistant item of a turn. The row itself
        is not animated — segments animate in, the shell just appears."""
        g = getattr(self, '_ai_turn_group', None)
        if g is not None:
            try:
                if g['row'].parent() is not None:
                    return g
            except RuntimeError:
                pass
            self._ai_turn_group = None

        row = QFrame()
        row.setObjectName("turnRow")
        # SCOPED rule + layout margins — a generic "QFrame { padding: ... }"
        # here CASCADES into every descendant QFrame that doesn't set its own
        # padding (shell → seg → bubble wrapper = triple padding: the huge
        # gaps/indents bug). Never style unscoped QFrame on a container.
        row.setStyleSheet("QFrame#turnRow { background-color: transparent; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(16, 10, 16, 10)
        row_layout.setSpacing(12)

        avatar = self._make_chat_avatar('bot', getattr(self, '_bot_avatar_size', 32))
        row_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        col_widget = QWidget()
        col_widget.setMaximumWidth(self._bubble_max_width())
        col_widget.setStyleSheet("background: transparent;")
        col = QVBoxLayout(col_widget)
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)

        _display_name = self.controller.get_assistant_name() or "Systema Auxilium"
        name_label = QLabel(f"<b>{_display_name}</b>")
        name_label.setStyleSheet("color: #E8EAED; font-size: 12px; background: transparent;")
        col.addWidget(name_label)

        _tc = self._t()
        _style = self._bubble_style()
        _glass = getattr(self, '_glass_enabled', False)
        shell = QFrame()
        shell.setObjectName("turnShell")
        shell.setStyleSheet(group_shell_css(_tc, _style, glass=_glass))
        if _style == 'compact':
            shell.setSizePolicy(QSizePolicy.Policy.Maximum,
                                QSizePolicy.Policy.Preferred)
        body_layout = QVBoxLayout(shell)
        body_layout.setContentsMargins(*group_shell_margins(_style))
        body_layout.setSpacing(group_body_spacing(_style))
        # No alignment pin: blend shells FILL the column (text wraps at the
        # bubble-max cap, claude.ai-wide), compact shells still hug via their
        # Maximum size policy.
        col.addWidget(shell)

        # stretch=1: the column claims the row's width up to its bubble-max
        # cap — without it the word-wrap sizeHint squeezed every turn into a
        # narrow strip hugging the avatar.
        row_layout.addWidget(col_widget, 1)
        row_layout.addStretch()

        g = {'row': row, 'shell': shell, 'body_layout': body_layout,
             'col_widget': col_widget, 'name_label': name_label}
        self._ai_turn_group = g
        # A fresh turn shell gets a fresh Thinking card (one per merged bubble).
        self._turn_thinking_card = None

        # The thinking dots now live INSIDE this shell (not as a separate row),
        # so the group row always goes just above the trailing stretch.
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, row)
        return g

    def _insert_turn_segment(self, widget, at_top: bool = False) -> dict:
        """Append `widget` as a segment of the current assistant turn
        (creating the turn shell when this is the turn's first item).

        `at_top=True` pins the widget to the START of the shell instead — the
        Thinking card lives there, above the reply, no matter when it arrives.

        Ordering: while a code card is STREAMING live (created at work-start,
        before its narration text has arrived), any NEW segment is inserted
        just ABOVE that live card — so the model's "let's run this" narration
        lands before the card, matching the reload order instead of showing the
        card first and the text after it."""
        g = self._ensure_ai_turn_group()
        body = g['body_layout']
        if at_top:
            body.insertWidget(0, widget)
            widget._group_row = g['row']
            self._move_thinking_dots_to_bottom()
            if self._bubble_style() == 'compact':
                self._refit_turn_shell(g['shell'])
            return g
        lc = getattr(self, '_live_card', None)
        live_w = lc.get('widget') if lc else None
        if live_w is not None and widget is not live_w:
            idx = body.indexOf(live_w)
            if idx >= 0:
                body.insertWidget(idx, widget)
            else:
                body.addWidget(widget)
        else:
            body.addWidget(widget)
        widget._group_row = g['row']    # for _detach_chat_widget pruning
        # Keep the thinking dots riding the bottom of the growing turn.
        self._move_thinking_dots_to_bottom()
        if self._bubble_style() == 'compact':
            self._refit_turn_shell(g['shell'])
        return g

    def _refit_turn_shell(self, shell):
        """Compact style: hug the turn's content via an EXPLICIT width — the
        widest segment's natural width, capped. Policy-hugging (Maximum) let
        Qt derive height from the sizeHint width; whenever the real width
        came out narrower, wrapped text was CLIPPED mid-sentence."""
        try:
            body = shell.layout()
            if body is None:
                return
            w = 0
            for i in range(body.count()):
                it = body.itemAt(i).widget()
                if it is None:
                    continue
                nat = getattr(it, '_natural_w', None)
                w = max(w, nat if nat else it.sizeHint().width())
            m = body.contentsMargins()
            w += m.left() + m.right()
            shell._natural_w = w
            # 64 floor (was 120): a one-word reply hugs WhatsApp-tight instead
            # of sitting in a forced-wide card.
            shell.setFixedWidth(max(64, min(w, self._bubble_max_width())))
        except RuntimeError:
            pass

    def _end_ai_turn_group(self):
        """Close the current assistant turn — the next assistant item starts
        a fresh shell. Called on every user/system boundary."""
        self._ai_turn_group = None

    def _detach_chat_widget(self, widget):
        """Remove a message widget from whichever layout holds it (top-level
        chat row or turn-shell body), delete it, and prune the shell if this
        was its last segment. Shared by the delete/rewind destroy paths."""
        group_row = getattr(widget, '_group_row', None)
        try:
            self.chat_layout.removeWidget(widget)   # no-op for segments
            widget.setParent(None)                  # immediate layout detach
            widget.deleteLater()
        except RuntimeError:
            pass
        if group_row is not None:
            self._prune_empty_group(group_row)

    def _prune_empty_group(self, group_row):
        """Delete a turn shell whose last segment is gone — an avatar+name
        husk must never linger in the chat."""
        if group_row is None:
            return
        try:
            shell = group_row.findChild(QFrame, "turnShell")
            if (shell is not None and shell.layout() is not None
                    and shell.layout().count() > 0):
                return
            g = getattr(self, '_ai_turn_group', None)
            if g is not None and g.get('row') is group_row:
                self._ai_turn_group = None
            self.chat_layout.removeWidget(group_row)
            group_row.deleteLater()
        except RuntimeError:
            pass

    def add_user_message(self, message, image_paths=None):
        """Add user message with three-dot menu"""
        self._end_ai_turn_group()   # a user message always closes the AI turn
        message_widget = QFrame()
        message_widget.setObjectName("chatRow")
        # Scoped + layout margins (see turnRow note: generic QFrame padding
        # cascades into the bubble wrapper and bloats it).
        message_widget.setStyleSheet("QFrame#chatRow { background-color: transparent; }")

        message_layout = QHBoxLayout(message_widget)
        message_layout.setContentsMargins(16, 10, 16, 10)
        message_layout.setSpacing(12)

        message_layout.addStretch()

        main_container_widget = QWidget()
        main_container_widget.setMaximumWidth(self._bubble_max_width())
        main_container_widget.setStyleSheet("background: transparent;")
        # HBox: [📋 ⋯ bottom-aligned] [bubble] — the action row rides at the
        # END of the text (horizontal space, not a row below); mirrored to
        # the left since the user column is right-aligned.
        main_container = QHBoxLayout(main_container_widget)
        main_container.setSpacing(6)
        main_container.setContentsMargins(0, 0, 0, 0)

        # (no "You" name tag — mock parity: the bubble tops out level with the
        #  avatar, and the bubble ALWAYS hugs its content, both styles)
        content_wrapper = QFrame()
        content_wrapper.setObjectName("bubbleWrap")
        _tc = self._t()
        _style = self._bubble_style()
        _glass = getattr(self, '_glass_enabled', False)
        content_wrapper.setStyleSheet(bubble_wrapper_css('user', _tc, _style, glass=_glass))

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(*bubble_wrapper_margins('user', _style, _glass))
        content_wrapper_layout.setSpacing(8)

        # ── Image thumbnails (shown above text when images were attached) ─────
        if image_paths:
            img_row = QHBoxLayout()
            img_row.setSpacing(6)
            img_row.setContentsMargins(0, 0, 0, 4)
            for img_path in image_paths:
                thumb = QLabel()
                thumb.setFixedSize(64, 64)
                thumb.setStyleSheet("border-radius: 6px; background: rgba(255,255,255,0.06);")
                thumb.setScaledContents(True)
                pm = QPixmap(img_path)
                if not pm.isNull():
                    thumb.setPixmap(pm.scaled(64, 64,
                                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                              Qt.TransformationMode.SmoothTransformation))
                img_row.addWidget(thumb)
            img_row.addStretch()
            content_wrapper_layout.addLayout(img_row)
        # ─────────────────────────────────────────────────────────────────────

        fsize = self._get_msg_font_size()
        text_label = QLabel()
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setText(self.render_markdown(self._break_unbreakable(message)))
        text_label.setWordWrap(True)
        text_label.setOpenExternalLinks(True)
        text_label.setStyleSheet(f"""
                    QLabel {{
                        color: #E8EAED;
                        font-size: {fsize}px;
                        line-height: 1.5;
                        background: transparent;
                        border: none;
                    }}
                """)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        content_wrapper_layout.addWidget(text_label)

        # Exact-fit width: the bubble is FIXED to its text width (capped) —
        # no more too-wide boxes and no truncated wrap (height now flows
        # from a deterministic width). Image rows set a floor.
        _img_min = (len(image_paths) * 70 + 24) if image_paths else 0
        self._fit_bubble_width(content_wrapper, text_label,
                               self._user_bubble_cap(), extra_min=_img_min)

        # Hover action row (⋯) — rides at the bubble's bottom-left
        action_row, menu_btn, _act_eff = self._make_action_row(False)
        main_container.addWidget(action_row, alignment=Qt.AlignmentFlag.AlignBottom)
        main_container.addWidget(content_wrapper)
        self._wire_action_row_hover(message_widget, _act_eff)

        message_layout.addWidget(main_container_widget)

        # Avatar (RIGHT side for user)
        avatar = self._make_chat_avatar('user', getattr(self, '_user_avatar_size', 32))
        message_layout.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignTop)

        self.last_user_message_widget = message_widget

        message_index = len(self.message_widgets)
        message_data = {
            'widget': message_widget,
            'role': 'user',
            'content': message,
            'index': message_index,
            'text_label': text_label,
            'text_labels': [text_label],
            'content_wrapper': content_wrapper,
            'main_container_widget': main_container_widget,
        }
        self.message_widgets.append(message_data)

        menu_btn.clicked.connect(lambda: self._show_message_menu(message_data))

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        # Scroll AFTER animation completes so sb.maximum() and widget.height()
        # are accurate — firing at 120ms (mid-animation) caused the clamp
        # min(target, sb.maximum()) to cut the target short on long chats.
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))
        self._refresh_msg_navigator()

    def append_to_last_user_message(self, text):
        """Grow the most recent user bubble by one line (voice barge-in) —
        updates its stored content and re-renders its label in place. Returns
        False when no user bubble exists (caller falls back to a new bubble)."""
        self._end_ai_turn_group()   # defensive: barge-in = a user turn
        for md in reversed(self.message_widgets):
            if md.get('role') != 'user':
                continue
            md['content'] = (md.get('content') or '') + "\n" + text
            lbl = md.get('text_label')
            if lbl is not None:
                try:
                    lbl.setText(self.render_markdown(md['content']))
                except Exception:
                    lbl.setText(md['content'])
                # Re-hug: the appended line may be wider than the old fit.
                cw = md.get('content_wrapper')
                if cw is not None:
                    self._fit_bubble_width(
                        cw, lbl, self._user_bubble_cap(),
                        extra_min=getattr(cw, '_fit_extra_min', 0))
            try:
                self.scroll_to_widget(md.get('widget'))
            except Exception:
                pass
            self._refresh_msg_navigator()   # keep its preview label current
            return True
        return False

    # ── Live streaming (provider contract v2) ────────────────────────────────
    # A streamed turn paints text into a throwaway "live" segment as deltas
    # arrive, then hands off: the final full-fidelity render (markdown, code
    # blocks, LaTeX, tool cards) still goes through add_ai_message, which
    # removes the live segment first. The stream IS the reveal, so the
    # typewriter animation is skipped for that turn.

    def on_stream_started(self):
        """First chunk arrived — open the turn shell and a live text segment."""
        try:
            if getattr(self, '_stream_seg', None) is not None:
                return
            fsize = self._get_msg_font_size()
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setStyleSheet(
                f"QLabel {{ color: #BDC1C6; font-size: {fsize}px; line-height: 1.5;"
                f" background: transparent; border: none; }}")
            holder = QFrame()
            holder.setObjectName("turnSeg")
            holder.setStyleSheet("QFrame#turnSeg { background-color: transparent; }")
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(*bubble_wrapper_margins(
                'assistant', self._bubble_style(),
                getattr(self, '_glass_enabled', False), in_group=True))
            lay.setSpacing(8)
            lay.addWidget(label)
            self._insert_turn_segment(holder)
            # `pending` = deltas not yet painted; `html` = already-escaped
            # markup, so a flush only ever escapes the NEW chunk.
            self._stream_seg = {'widget': holder, 'label': label, 'text': '',
                                'html': '', 'pending': []}
            self._stream_active = True
        except Exception:
            log.warning("[ChatWindow.on_stream_started] failed", exc_info=True)
            self._stream_seg = None

    # Deltas arrive far faster than a human reads and far faster than Qt can
    # re-lay-out a growing document, so widget updates are COALESCED onto this
    # interval instead of running per delta.
    STREAM_FLUSH_MS = 70

    def on_stream_text(self, delta: str):
        """Queue a reply delta for the live segment. Nothing touches a widget
        here — `_flush_stream` does, at most ~14x/second.

        History: this used to re-escape the WHOLE accumulated reply and
        setText() it on every delta — O(n) work and a full rich-text relayout
        per token, i.e. O(n^2) over a reply. A long report (with the thinking
        card doing the same thing simultaneously) froze the UI near the end."""
        seg = getattr(self, '_stream_seg', None)
        if seg is None:
            self.on_stream_started()
            seg = getattr(self, '_stream_seg', None)
            if seg is None:
                return
        seg['pending'].append(delta)
        self._schedule_stream_flush()

    def _schedule_stream_flush(self):
        t = getattr(self, '_stream_flush_timer', None)
        if t is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(self._flush_stream)
            self._stream_flush_timer = t
        if not t.isActive():
            t.start(self.STREAM_FLUSH_MS)

    def _flush_stream(self):
        """Apply everything buffered since the last flush — ONE escape of the
        new chunk, ONE setText, ONE scroll, for both the reply and the card."""
        import html as _html
        seg = getattr(self, '_stream_seg', None)
        painted = False
        if seg is not None and seg['pending']:
            chunk = ''.join(seg['pending'])
            seg['pending'].clear()
            seg['text'] += chunk
            # Escape ONLY the new chunk and append to the rendered html.
            seg['html'] += _html.escape(chunk).replace('\n', '<br>')
            try:
                seg['label'].setText(seg['html'])
                painted = True
            except RuntimeError:
                self._stream_seg = None

        card = getattr(self, '_stream_think_card', None)
        if card is not None and card.get('flush'):
            try:
                painted = card['flush']() or painted
            except RuntimeError:
                self._stream_think_card = None

        if painted:
            self._stream_autoscroll()

    def _stop_stream_flush(self):
        t = getattr(self, '_stream_flush_timer', None)
        if t is not None:
            try:
                t.stop()
            except RuntimeError:
                pass

    def on_stream_thinking(self, delta: str):
        """Append a reasoning delta to THIS TURN's thinking card (created on
        first use, pinned to the top of the turn shell). Later responses in the
        same merged bubble keep feeding the same card."""
        card = getattr(self, '_stream_think_card', None)
        if card is None:
            try:
                card = self.add_thinking_card('', save_to_history=False, live=True)
            except Exception:
                log.warning("[ChatWindow.on_stream_thinking] card failed", exc_info=True)
                card = None
            if card is not None and card['state']['text']:
                # Reused an existing card from an earlier response in this
                # turn — separate this response's reasoning from the last.
                card['append']("\n\n")
            self._stream_think_card = card
            if card is None:
                return
        try:
            card['append'](delta)          # buffers only; painted on flush
            self._schedule_stream_flush()
        except RuntimeError:
            self._stream_think_card = None

    def on_stream_finished(self):
        """Stream ended. Freeze the thinking card and persist ONLY this
        response's reasoning (the card may hold several responses' worth; each
        is saved as its own ui_event so reload replays them in order and the
        per-turn card re-merges them)."""
        # Paint whatever is still buffered before tearing anything down.
        self._stop_stream_flush()
        try:
            self._flush_stream()
        except RuntimeError:
            pass

        card = getattr(self, '_stream_think_card', None)
        if card is not None:
            try:
                before = card['state'].get('saved_upto', 0)
                full = card['finish']()
                self._save_thinking_event(full[before:])
                card['state']['saved_upto'] = len(full)
            except RuntimeError:
                pass
            self._stream_think_card = None
        self._stream_active = False
        # The final message normally lands in the same tick; if the turn
        # produced no assistant text at all (e.g. tool-only), drop the stub.
        QTimer.singleShot(1200, self._drop_stale_stream_segment)

    def _drop_stale_stream_segment(self):
        if not getattr(self, '_stream_active', False):
            self._clear_stream_segment()

    def _clear_stream_segment(self):
        """Remove the live streaming segment (hand-off or abandonment)."""
        self._stop_stream_flush()
        seg = getattr(self, '_stream_seg', None)
        self._stream_seg = None
        if seg is None:
            return
        try:
            w = seg['widget']
            w.setParent(None)
            w.deleteLater()
        except RuntimeError:
            pass

    def _stream_autoscroll(self):
        try:
            if getattr(self, '_sticky_bottom', True):
                self.scroll_to_bottom()
        except Exception:
            pass

    def add_ai_message(self, message):
        """Add AI message as a SEGMENT of the current turn group — the shell
        (avatar + name) is shared by every assistant item of the turn; this
        builds only the text block + its own hover 📋 ⋯ action row."""
        # Hand-off from a streamed turn: the live stub is replaced by this
        # full-fidelity render, and the typewriter reveal is skipped (the
        # stream already served as the reveal).
        _was_streamed = getattr(self, '_stream_seg', None) is not None
        if _was_streamed:
            self._clear_stream_segment()
        if self.voice_enabled:
            display_message = self._clean_emotion_brackets(message)
        else:
            display_message = message

        # Keep the pre-LaTeX source: background math renders re-run the
        # preprocess from it (cache hits then) to swap placeholders for images.
        _pre_latex_src = display_message
        display_message = self._preprocess_latex(display_message)
        _latex_keys = self._take_latex_misses()

        message_widget = QFrame()
        message_widget.setObjectName("turnSeg")
        message_widget.setStyleSheet(
            "QFrame#turnSeg { background-color: transparent; }")

        # HBox: [text, stretch 1] [📋 ⋯ bottom-aligned] — the action row rides
        # at the END of the text (horizontal space, not a row below).
        seg_layout = QHBoxLayout(message_widget)
        seg_layout.setContentsMargins(0, 0, 0, 0)
        seg_layout.setSpacing(6)

        content_wrapper = QFrame()
        content_wrapper.setObjectName("bubbleWrap")
        _tc = self._t()
        _style = self._bubble_style()
        _glass = getattr(self, '_glass_enabled', False)
        content_wrapper.setStyleSheet(
            bubble_wrapper_css('assistant', _tc, _style, glass=_glass, in_group=True))
        # NO Maximum policy here: segments live inside the turn shell and must
        # FILL it in both styles (a build-time Maximum was why compact text
        # hugged a narrow strip after session reloads until a style toggle).

        content_wrapper_layout = QVBoxLayout(content_wrapper)
        content_wrapper_layout.setContentsMargins(
            *bubble_wrapper_margins('assistant', _style, _glass, in_group=True))
        content_wrapper_layout.setSpacing(8)

        parts = self._split_message_parts(display_message)
        first_text_label = None
        all_text_labels = []
        fsize = self._get_msg_font_size()

        if isinstance(parts, list):
            for part in parts:
                if part[0] == 'text':
                    text_label = QLabel()
                    text_label.setTextFormat(Qt.TextFormat.RichText)
                    text_label.setText(self.render_markdown(part[1]))
                    text_label.setWordWrap(True)
                    text_label.setOpenExternalLinks(True)
                    text_label.setStyleSheet(f"""
                        QLabel {{
                            color: #BDC1C6;
                            font-size: {fsize}px;
                            line-height: 1.5;
                            background: transparent;
                            border: none;
                        }}
                    """)
                    text_label.setTextInteractionFlags(
                        Qt.TextInteractionFlag.TextSelectableByMouse |
                        Qt.TextInteractionFlag.LinksAccessibleByMouse
                    )
                    content_wrapper_layout.addWidget(text_label)
                    all_text_labels.append(text_label)
                    if not first_text_label:
                        first_text_label = text_label
                elif part[0] == 'code':
                    code_widget = CodeBlockWidget(part[1], part[2], self._t())
                    content_wrapper_layout.addWidget(code_widget)
                elif part[0] == 'table':
                    table_widget = TableBlockWidget(part[1], self._t(), self.render_markdown)
                    content_wrapper_layout.addWidget(table_widget)
        else:
            text_label = QLabel()
            text_label.setTextFormat(Qt.TextFormat.RichText)
            text_label.setText(parts)
            text_label.setWordWrap(True)
            text_label.setOpenExternalLinks(True)
            text_label.setStyleSheet(f"""
                QLabel {{
                    color: #BDC1C6;
                    font-size: {fsize}px;
                    line-height: 1.5;
                    background: transparent;
                    border: none;
                }}
            """)
            text_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            content_wrapper_layout.addWidget(text_label)
            first_text_label = text_label
            all_text_labels.append(text_label)

        # Pure-text segments (no code/table blocks) HUG their text so the ⋯
        # sits right after the content instead of floating at the window edge
        # (blend mode). Mixed segments fill the width (code/tables need it).
        _pure_text = (first_text_label is not None and len(all_text_labels) == 1
                      and (not isinstance(parts, list)
                           or not any(p[0] in ('code', 'table') for p in parts)))

        # Natural width of this segment (compact shells hug the widest one):
        # measured from the FULL rendered text so the typing reveal can't
        # bake in a mid-animation width.
        _text_ideal = None
        if first_text_label is not None and len(all_text_labels) == 1:
            try:
                from PyQt6.QtGui import QTextDocument
                _doc = QTextDocument()
                _f = first_text_label.font()
                _f.setPixelSize(fsize)
                _doc.setDefaultFont(_f)
                _doc.setHtml(self.render_markdown(display_message))
                _doc.setTextWidth(-1)
                _m = content_wrapper_layout.contentsMargins()
                _text_ideal = int(_doc.idealWidth()) + _m.left() + _m.right()
                # Segment footprint = text + the real ⋯-row chrome; the old
                # +60 left ~30px of dead width on every compact shell.
                message_widget._natural_w = _text_ideal + self._SEG_CHROME
            except Exception:
                pass

        if _pure_text and _text_ideal:
            # Hug: fix the wrapper to the text's ideal width (capped) via the
            # reflow machinery, then let a trailing stretch pin the ⋯ close.
            content_wrapper._natural_w = _text_ideal
            content_wrapper.setFixedWidth(
                min(_text_ideal, self._assistant_seg_cap()))
            seg_layout.addWidget(content_wrapper)
        else:
            seg_layout.addWidget(content_wrapper, 1)

        # Hover action row (⋯) — per SEGMENT, docked at the end of the text:
        # hovering this block reveals only ITS button.
        action_row, menu_btn, _act_eff = self._make_action_row(True)
        seg_layout.addWidget(action_row, alignment=Qt.AlignmentFlag.AlignBottom)
        if _pure_text and _text_ideal:
            seg_layout.addStretch(1)   # keeps ⋯ tight to the text end (blend)
        self._wire_action_row_hover(message_widget, _act_eff)

        # Join the current turn group (creates the shell if this is the
        # turn's first item — e.g. a plain no-work reply).
        g = self._insert_turn_segment(message_widget)

        message_index = len(self.message_widgets)
        message_data = {
            'widget': message_widget,
            'role': 'assistant',
            'content': message,
            'display_content': display_message,
            'pre_latex_source': _pre_latex_src,
            'index': message_index,
            'text_label': first_text_label,
            'text_labels': all_text_labels,
            'content_wrapper': content_wrapper,
            'main_container_widget': g['col_widget'],
            'content_wrapper_layout': content_wrapper_layout,
            'group_row': g['row'],
        }
        self.message_widgets.append(message_data)
        if _latex_keys:
            # Math is rendering in the background — refresh this message's
            # labels when each expression lands in the cache.
            self._register_latex_waiter(_latex_keys, message_data)

        menu_btn.clicked.connect(lambda: self._show_message_menu(message_data))

        # Typing reveal for plain text replies (configurable, Settings ▸ UI):
        # the reveal IS the entrance animation — running the height pop-in on
        # top would clip the growing text. Mixed content (code/tables) keeps
        # the classic pop-in.
        _simple_text = (len(all_text_labels) == 1
                        and first_text_label is not None
                        and (not isinstance(parts, list) or len(parts) == 1))
        if (_simple_text and self._reveal_enabled()
                and not _was_streamed
                and not getattr(self, '_bulk_render', False)):
            first_text_label.setText("")
            self._reveal_text_label(
                first_text_label, display_message,
                on_done=lambda: self.scroll_to_widget(message_widget))
        else:
            self._animate_message_in(message_widget,
                                     on_settled=lambda: self.scroll_to_widget(message_widget))

    def _clean_emotion_brackets(self, text):
        """Remove ElevenLabs emotion brackets from text for display.

        Only tidies the horizontal gaps the removed brackets leave — newlines are
        preserved (collapsing '\\s+' here flattened every voice-mode message into
        one jumbled line)."""
        import re
        cleaned = re.sub(r'\[([^\]]+)\]', '', text)
        if cleaned == text:
            return text
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
        cleaned = re.sub(r'[ \t]+\n', '\n', cleaned)
        return cleaned.strip()

    def add_system_message(self, message):
        """Add system message"""
        self._end_ai_turn_group()   # a system interjection closes the AI turn
        # ── Update work mode banner if this is a Working: annotation ─────────
        import re as _re
        if "**Working:**" in message or message.startswith("Working:"):
            # Extract the annotation text (strip markdown bold/italic markers)
            clean = _re.sub(r'\*+', '', message).replace("Working:", "").strip()
            if hasattr(self, '_work_banner'):
                self._work_banner.setText(f"Working: {clean}")
                self._work_banner.show()
            # Narrate the step annotation — this is the AI's own commentary on
            # what it is doing (never raw output), queued serially with the rest
            if self.voice_enabled and clean:
                self.speak_ai_response(clean)
        # ─────────────────────────────────────────────────────────────────────
        message_widget = QFrame()
        message_widget.setObjectName("sysRow")
        message_widget.setStyleSheet("QFrame#sysRow { background-color: transparent; }")

        message_layout = QHBoxLayout(message_widget)
        message_layout.setContentsMargins(16, 4, 16, 4)

        text_label = QLabel()
        text_label.setWordWrap(True)
        text_label.setTextFormat(Qt.TextFormat.RichText)
        text_label.setOpenExternalLinks(True)
        text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        # Faint centered line (2026-07-19 redesign): no box, no border — a
        # muted note that blends into the flow like the tool-card headers.
        # Emoji stripped at display time (history keeps the original text).
        text_label.setText(self.render_markdown(_strip_emoji(message)))

        def _sys_restyle(lbl=text_label):
            lbl.setStyleSheet(
                f"QLabel {{ background: transparent; border: none; "
                f"color: #8B949E; font-size: {self._card_z(11)}px; "
                f"padding: 2px 8px; }}")
        _sys_restyle()
        message_layout.addWidget(text_label)

        # zoom_restyle keeps the note scaling with Ctrl+scroll (and is what
        # apply_theme/_apply_zoom_all call — the look itself is theme-free).
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'system',
            'content_wrapper': text_label,  # text_label IS the styled surface here
            'zoom_restyle': _sys_restyle,
        })

        self.chat_layout.insertWidget(self.chat_layout.count() - 1, message_widget)
        self._animate_message_in(message_widget,
                                 on_settled=lambda: self.scroll_to_widget(message_widget))
        # The interjection just split the turn — carry the typing indicator down
        # into the new shell so it rides the NEWEST bubble, not the old one.
        self._rehome_thinking_dots()

    def copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_label.setText("✓ Copied to clipboard")
        QTimer.singleShot(ANIM_STATUS_CLEAR_MS, lambda: self.status_label.setText(""))

    def _show_message_menu(self, message_data):
        """Show context menu for message"""
        menu = QMenu(self)
        _tc = self._t()
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 8px;
                padding: 4px;
                color: #E8EAED;
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 7px 14px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {_tc['surface']};
                color: {_tc['accent']};
            }}
            QMenu::separator {{
                height: 1px; background: {_tc['border']}; margin: 4px 6px;
            }}
        """)

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(
            lambda: self.copy_to_clipboard(
                message_data.get('display_content') or message_data.get('content', '')))
        menu.addAction(copy_action)

        edit_action = QAction("Edit message", self)
        edit_action.triggered.connect(lambda: self._edit_message(message_data))
        menu.addAction(edit_action)

        if message_data['role'] == 'assistant':
            regen_action = QAction("Regenerate response", self)
            regen_action.triggered.connect(lambda: self._regenerate_response(message_data))
            menu.addAction(regen_action)

        menu.addSeparator()

        delete_action = QAction("Delete message", self)
        delete_action.triggered.connect(lambda: self._delete_message(message_data))
        menu.addAction(delete_action)

        rewind_action = QAction("Rewind to here", self)
        rewind_action.triggered.connect(lambda: self._rewind_to_here(message_data))
        menu.addAction(rewind_action)

        menu.exec(QCursor.pos())

    def _edit_message(self, message_data):
        """Edit a message"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel

        _tc = self._t()
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Message")
        dialog.setMinimumWidth(500)
        dialog.setStyleSheet(f"""
            QDialog {{
                background-color: {_tc['surface']};
                border: 1px solid {_tc['border']};
            }}
            QLabel {{
                color: #E8EAED;
                font-size: 13px;
            }}
        """)

        layout = QVBoxLayout(dialog)

        header = QLabel(f"<b>Edit {'Your' if message_data['role'] == 'user' else 'AI'} Message</b>")
        layout.addWidget(header)

        text_edit = QTextEdit()
        text_edit.setPlainText(message_data['content'])
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {_tc['elevated']};
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                padding: 8px;
                color: #E8EAED;
                font-size: 13px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            }}
            QTextEdit:focus {{ border-color: {_tc['accent']}; }}
        """)
        text_edit.setMinimumHeight(150)
        layout.addWidget(text_edit)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: 1px solid {_tc['border']};
                border-radius: 6px;
                padding: 8px 16px;
                color: #8B949E;
            }}
            QPushButton:hover {{
                color: #E8EAED;
                background-color: {_tc['elevated']};
            }}
        """)
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)

        save_text = "Save & Regenerate" if message_data['role'] == 'user' else "Save"
        save_btn = QPushButton(save_text)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_tc['accent']};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                color: white;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {_tc.get('accent_hover', _tc['accent'])};
            }}
        """)
        save_btn.clicked.connect(lambda: self._save_edited_message(message_data, text_edit.toPlainText(), dialog))
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

        dialog.exec()

    def _save_edited_message(self, message_data, new_content, dialog):
        """Save edited message and handle consequences"""
        if not new_content.strip():
            return

        if message_data['role'] == 'user':
            # Capture old content BEFORE mutating so history search still works
            old_content = message_data['content']

            # Find where this user turn sits in conversation_history
            history_index = self._find_history_index_by_role_content('user', old_content)

            # Remove this widget and everything after it
            target_index = message_data['index']
            widgets_to_remove = [md['widget'] for md in self.message_widgets[target_index:]]
            self.message_widgets = self.message_widgets[:target_index]

            # Truncate history UP TO (not including) this user message so
            # send_message → generate_response can append it fresh (no duplicate)
            if history_index >= 0:
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index]

            # Animate out the stale widgets
            for widget in widgets_to_remove:
                def _destroy(w=widget):
                    self.chat_layout.removeWidget(w)
                    w.deleteLater()
                self._animate_message_out(widget, _destroy)

            # Re-render the user bubble with updated text, then fire AI
            self.add_user_message(new_content)
            self.controller.send_message(new_content)
            QTimer.singleShot(600, self._start_session_lock_watcher)

        else:
            # Assistant / system edit: update in-place
            history_index = self._get_history_index(message_data)
            message_data['content'] = new_content
            if history_index >= 0:
                self.controller.ai.conversation_history[history_index]['content'] = new_content
            if message_data.get('text_label'):
                message_data['text_label'].setText(self.render_markdown(new_content))

        self.controller._auto_save_session()
        dialog.accept()

    def _delete_message(self, message_data):
        """Delete a message — data removed sync, widget animated out async."""
        history_index = self._get_history_index(message_data)

        if message_data in self.message_widgets:
            self.message_widgets.remove(message_data)
        for i, msg in enumerate(self.message_widgets):
            msg['index'] = i
        if history_index >= 0:
            try:
                self.controller.ai.conversation_history.pop(history_index)
            except IndexError:
                pass
        self.controller._auto_save_session()

        widget = message_data['widget']
        def _destroy():
            self._detach_chat_widget(widget)
        self._animate_message_out(widget, _destroy)
        self._refresh_msg_navigator()

    def _rewind_to_message(self, message_data, keep_message=False):
        """Rewind conversation — data truncated sync, widgets animated out async."""
        target_index = message_data['index']
        cutoff = target_index + 1 if keep_message else target_index

        widgets_to_remove = [md['widget'] for md in self.message_widgets[cutoff:]]

        self.message_widgets = self.message_widgets[:cutoff]

        history_index = self._get_history_index(message_data)
        if history_index >= 0:
            if keep_message:
                history_index += 1
            self.controller.ai.conversation_history = \
                self.controller.ai.conversation_history[:history_index]

        self.controller._auto_save_session()

        for widget in widgets_to_remove:
            def _destroy(w=widget):
                self._detach_chat_widget(w)
            self._animate_message_out(widget, _destroy)
        self._refresh_msg_navigator()
        self._end_ai_turn_group()   # never append into a truncated turn

    def _rewind_to_here(self, message_data):
        """'Rewind to Here' from the context menu.
        Always keeps the target message itself; discards everything after it.
        - Assistant bubble: just truncates, no new request.
        - User bubble: truncates history before this turn so send_message can
          re-append it cleanly, then fires a fresh AI response."""
        target_index = message_data['index']
        role = message_data['role']

        # Widgets: keep up to and including the target
        widgets_to_remove = [md['widget'] for md in self.message_widgets[target_index + 1:]]
        self.message_widgets = self.message_widgets[:target_index + 1]

        # History: find real position by content+role
        history_index = self._get_history_index(message_data)
        if history_index >= 0:
            if role == 'user':
                # Truncate BEFORE this entry — send_message will re-append it
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index]
            else:
                # Truncate AFTER this entry — keep the assistant message
                self.controller.ai.conversation_history = \
                    self.controller.ai.conversation_history[:history_index + 1]

        self.controller._auto_save_session()

        for widget in widgets_to_remove:
            def _destroy(w=widget):
                self._detach_chat_widget(w)
            self._animate_message_out(widget, _destroy)
        self._refresh_msg_navigator()
        self._end_ai_turn_group()   # never append into a truncated turn

        # For user messages: fire a new AI response (bubble stays visible)
        if role == 'user':
            self.controller.send_message(message_data['content'])
            QTimer.singleShot(600, self._start_session_lock_watcher)

    def _regenerate_response(self, message_data):
        """Regenerate AI response — rewinds before the user message, re-renders the user
        bubble, then fires a fresh send so the AI responds again."""
        target_index = message_data['index']

        user_msg = None
        for i in range(target_index - 1, -1, -1):
            if self.message_widgets[i]['role'] == 'user':
                user_msg = self.message_widgets[i]
                break

        if not user_msg:
            return

        user_message = user_msg['content']

        # Rewind to just BEFORE the user message — removes it and everything after
        self._rewind_to_message(user_msg, keep_message=False)

        # Re-render the user bubble so it stays visible in chat
        self.add_user_message(user_message)

        # Fire AI (generate_response will append user turn to history once)
        self.controller.send_message(user_message)
        QTimer.singleShot(600, self._start_session_lock_watcher)

    def _get_history_index(self, message_data):
        """Find the real index of a message in conversation_history by matching role+content.
        Searches from the end so the most-recent occurrence is found first."""
        role = message_data.get('role')
        content = message_data.get('content', '')
        history = self.controller.ai.conversation_history
        for i in range(len(history) - 1, -1, -1):
            entry = history[i]
            if entry.get('role') == role and entry.get('content') == content:
                return i
        return -1

    def _find_history_index_by_role_content(self, role, content):
        """Like _get_history_index but takes role+content directly (used before message_data is mutated)."""
        history = self.controller.ai.conversation_history
        for i in range(len(history) - 1, -1, -1):
            entry = history[i]
            if entry.get('role') == role and entry.get('content') == content:
                return i
        return -1
