"""
ui/base_window.py
BaseWindow — shared frameless window chrome for all top-level windows.

Provides:
  - Drag-to-move  (header_mouse_press / move / release)
  - Edge + corner resize handles  (create_resize_handles / position_resize_handles)
  - Rounded mask  (apply_rounded_mask / resizeEvent)
  - Save geometry stub  (save_window_geometry)
  - Toggle-maximise helper  (toggle_maximize)

Usage
-----
    class MyWindow(BaseWindow):
        # Optional: override header height used by resize handles
        _header_height: int = 50        # default; set to 44 in AppearanceSettingsWindow

        def __init__(self):
            super().__init__()
            # ── init your dragging / resizing state ──────────────────────
            self._init_chrome_state()
            # ... rest of your __init__

        def resizeEvent(self, event):
            super().resizeEvent(event)   # handles mask + handles
            # add your window-specific layout adjustments here

Subclasses that override eventFilter MUST call super().eventFilter(obj, event)
at the end so the resize-handle logic is always reached.
"""

from PyQt6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton)
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QRegion


class BaseWindow(QWidget):
    """Frameless, resizable, draggable window base class."""

    # Override in subclasses that have a shorter / taller header bar
    _header_height: int = 50

    def showEvent(self, event):
        """Give every scrollable view in this window the house wheel behaviour.

        Done here rather than at each QScrollArea construction site so a NEW
        window inherits it automatically — scrolling that differs per window is
        exactly how the app ended up with views you could not aim in.
        Idempotent (install_smooth_scroll no-ops on an area it already owns), so
        re-showing a window costs nothing.
        """
        try:
            from PyQt6.QtWidgets import QAbstractScrollArea
            from systema.ui.widgets.smooth_scroll import install_smooth_scroll
            for area in self.findChildren(QAbstractScrollArea):
                install_smooth_scroll(area)
        except Exception:
            pass
        super().showEvent(event)

    # ── Chrome state bootstrap ─────────────────────────────────────────────────

    def _init_chrome_state(self):
        """
        Call once in __init__ BEFORE init_ui() / any UI build.
        Initialises all instance variables used by the chrome methods so that
        resizeEvent / eventFilter never hit AttributeError during early paint.
        """
        self.dragging = False
        self.drag_position = QPoint()
        self.resizing = False
        self.resize_edge = None
        self.resize_start_geometry = None
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.save_window_geometry)

    # ── Rounded mask ───────────────────────────────────────────────────────────

    # Subclasses whose corner-touching children all carry matching
    # border-radii can opt in: the antialiased stylesheet corners then render
    # SMOOTH with no mask. Default stays masked — a 1-bit mask is jagged, but
    # a square child painting past the container's radius is worse.
    _smooth_corners = False

    def apply_rounded_mask(self):
        """Round the window corners: smooth stylesheet corners for opted-in
        windows (mask cleared), 12-px polygon clip mask otherwise."""
        if getattr(self, '_smooth_corners', False) and self.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground):
            self.clearMask()
            return
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def resizeEvent(self, event):
        """
        Refresh mask + handle positions on every resize.
        Subclasses that have additional resize logic should call super() first:

            def resizeEvent(self, event):
                super().resizeEvent(event)   # mask + handles
                # ... extra layout work
        """
        super().resizeEvent(event)
        self.apply_rounded_mask()
        if hasattr(self, 'resize_handles'):
            self.position_resize_handles()
        if hasattr(self, 'resize_timer'):
            self.resize_timer.stop()
            self.resize_timer.start(1000)

    # ── Geometry persistence stub ──────────────────────────────────────────────

    def save_window_geometry(self):
        """
        Persist window size & position.
        Base implementation is a no-op; override in windows that actually save.
        """
        pass

    # ── Drag-to-move ───────────────────────────────────────────────────────────

    def header_mouse_press(self, event):
        """Assign this to your header QFrame's mousePressEvent."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def header_mouse_move(self, event):
        """Assign this to your header QFrame's mouseMoveEvent."""
        if self.dragging:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def header_mouse_release(self, event):
        """Assign this to your header QFrame's mouseReleaseEvent."""
        self.dragging = False
        event.accept()

    # ── Toggle maximise ────────────────────────────────────────────────────────

    def toggle_maximize(self):
        """
        Toggle between maximised and normal state.
        Expects a self.maximize_btn (QPushButton); override if your button
        is named differently or doesn't exist.
        """
        if self.isMaximized():
            self.showNormal()
            if hasattr(self, 'maximize_btn'):
                self.maximize_btn.setText('□')
        else:
            self.showMaximized()
            if hasattr(self, 'maximize_btn'):
                self.maximize_btn.setText('❐')

    # ── Resize handles ─────────────────────────────────────────────────────────

    def create_resize_handles(self):
        """Create 8 invisible edge/corner resize handles and install event filter.

        Edge strips are 5px (was 8): they sit ON TOP of the window content, and
        a wider right-edge strip swallowed most scrollbar clicks — users kept
        grabbing resize instead of the scrollbar. Corners stay 16px (easy to
        hit, nothing scrollable lives there)."""

        self.resize_handles = {}

        edges = {
            'top':    Qt.CursorShape.SizeVerCursor,
            'bottom': Qt.CursorShape.SizeVerCursor,
            'left':   Qt.CursorShape.SizeHorCursor,
            'right':  Qt.CursorShape.SizeHorCursor,
        }
        corners = {
            'top-left':     Qt.CursorShape.SizeFDiagCursor,
            'top-right':    Qt.CursorShape.SizeBDiagCursor,
            'bottom-left':  Qt.CursorShape.SizeBDiagCursor,
            'bottom-right': Qt.CursorShape.SizeFDiagCursor,
        }

        for name, cursor in {**edges, **corners}.items():
            handle = QFrame(self)
            handle.setStyleSheet('background-color: transparent;')
            handle.setCursor(cursor)
            handle.edge_type = name
            handle.installEventFilter(self)
            self.resize_handles[name] = handle
            handle.raise_()

        self.position_resize_handles()

    def position_resize_handles(self):
        """Reposition all 8 handles to match the current window size."""
        w = self.width()
        h = self.height()
        hs = 5   # handle_size (keep in sync with create_resize_handles)
        cs = 16  # corner_size

        self.resize_handles['top'].setGeometry(cs, 0, w - 2 * cs, hs)
        self.resize_handles['bottom'].setGeometry(cs, h - hs, w - 2 * cs, hs)
        self.resize_handles['left'].setGeometry(0, cs, hs, h - 2 * cs)
        self.resize_handles['right'].setGeometry(w - hs, cs, hs, h - 2 * cs)

        self.resize_handles['top-left'].setGeometry(0, 0, cs, cs)
        self.resize_handles['top-right'].setGeometry(w - cs, 0, cs, cs)
        self.resize_handles['bottom-left'].setGeometry(0, h - cs, cs, cs)
        self.resize_handles['bottom-right'].setGeometry(w - cs, h - cs, cs, cs)

    def eventFilter(self, obj, event):
        """
        Handle resize-handle mouse events.

        Subclasses that override eventFilter for their own purposes MUST
        call super().eventFilter(obj, event) at the end so this logic runs.
        """
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

                if (new_geo.width() >= self.minimumWidth() and
                        new_geo.height() >= self.minimumHeight()):
                    self.setGeometry(new_geo)
                return True

        return super().eventFilter(obj, event)

    # ══════════════════════════════════════════════════════════════════════════
    # Reusable window shell — the "ultimate parent" chrome.
    #
    # New windows should build themselves with these instead of hand-rolling a
    # header + buttons. Minimal usage:
    #
    #     class MyWindow(BaseWindow):
    #         def __init__(self, controller):
    #             super().__init__()
    #             from systema.ui import theme
    #             p = theme.current_palette(controller)
    #             body = self.build_shell(p, "My Window", min_size=(560, 460))
    #             body.addWidget(self.make_button("Do it", p, kind="primary"))
    #
    # `build_shell` returns the body QVBoxLayout to populate; it wires the
    # frameless flags, rounded container, draggable title bar, resize handles
    # and a professional close/min/max button set.
    # ══════════════════════════════════════════════════════════════════════════

    def build_shell(self, palette, title, min_size=(560, 460),
                    buttons=("close",), radius=12):
        """One-call window setup. Returns the body QVBoxLayout to add content to."""
        self._pal_cache = palette
        self._shell_radius = radius
        self._init_chrome_state()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(*min_size)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._shell = QFrame()
        self._shell.setObjectName("bwShell")
        self._shell.setStyleSheet(
            f"#bwShell {{ background-color: {palette['bg']};"
            f" border: 1px solid {palette['border']}; border-radius: {radius}px; }}")
        outer.addWidget(self._shell)

        root = QVBoxLayout(self._shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.build_title_bar(title, palette, buttons, radius))

        body = QVBoxLayout()
        body.setContentsMargins(18, 14, 18, 16)
        body.setSpacing(10)
        root.addLayout(body)

        self.create_resize_handles()
        return body

    def build_title_bar(self, title, palette, buttons=("close",), radius=12):
        """A draggable custom title bar: title on the left, chrome buttons right."""
        bar = QFrame()
        bar.setFixedHeight(self._header_height)
        bar.setStyleSheet(
            f"QFrame {{ background-color: {palette['surface']};"
            f" border-top-left-radius: {radius}px; border-top-right-radius: {radius}px; }}")
        bar.mousePressEvent = self.header_mouse_press
        bar.mouseMoveEvent = self.header_mouse_move
        bar.mouseReleaseEvent = self.header_mouse_release
        if "maximize" in buttons:
            bar.mouseDoubleClickEvent = lambda e: self.toggle_maximize()

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 8, 0)
        lay.setSpacing(4)
        self._title_label = QLabel(title)
        self._title_label.setStyleSheet(
            f"color: {palette['text']}; font-size: 13px; font-weight: 600;"
            f" background: transparent; border: none;")
        lay.addWidget(self._title_label)
        lay.addStretch()

        for kind in buttons:
            if kind == "minimize":
                lay.addWidget(self._chrome_button("–", palette, self.showMinimized))
            elif kind == "maximize":
                self.maximize_btn = self._chrome_button("□", palette, self.toggle_maximize)
                lay.addWidget(self.maximize_btn)
            elif kind == "close":
                lay.addWidget(self._chrome_button("✕", palette, self.close, danger=True))
        return bar

    def set_title(self, text: str):
        if getattr(self, "_title_label", None) is not None:
            self._title_label.setText(text)

    def _chrome_button(self, glyph, palette, on_click, danger=False):
        """Painted chrome button (icon overhaul 2026-07-21): the text glyph
        argument now only SELECTS the painted class — '–' minimize, '□'
        maximize, anything else = close (X, red hover pill). `palette` is
        accepted for signature compatibility; icons use the house grays."""
        from systema.ui.widgets.painted_icons import (
            MinimizeButton, MaximizeButton, CloseButton)
        if glyph == "–":
            btn = MinimizeButton((32, 30), tooltip="Minimize")
        elif glyph == "□":
            btn = MaximizeButton((32, 30), tooltip="Maximize / restore")
        else:
            btn = CloseButton((32, 30), tooltip="Close", pill=True)
        btn.clicked.connect(on_click)
        return btn

    @staticmethod
    def make_button(text, palette, kind="secondary"):
        """A consistently-styled body button. kind: primary | secondary | ghost."""
        from systema.ui import theme  # local import avoids any import cycle
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(34)
        if kind == "primary":
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {palette['accent']}; color: #05070a;"
                f" border: none; border-radius: 8px; padding: 8px 18px; font-size: 12px;"
                f" font-weight: 600; }}"
                f"QPushButton:hover {{ background-color: {theme.lighten(palette['accent'], 0.18)}; }}"
                f"QPushButton:pressed {{ background-color: {theme.darken(palette['accent'], 0.12)}; }}"
                f"QPushButton:disabled {{ background-color: {palette['surface2']};"
                f" color: {palette['muted']}; }}")
        elif kind == "ghost":
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {palette['text']};"
                f" border: none; border-radius: 8px; padding: 8px 14px; font-size: 12px; }}"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.06); }}"
                f"QPushButton:disabled {{ color: {palette['muted']}; }}")
        else:  # secondary
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {palette['surface2']}; color: {palette['text']};"
                f" border: 1px solid {palette['border']}; border-radius: 8px; padding: 8px 16px;"
                f" font-size: 12px; }}"
                f"QPushButton:hover {{ border-color: {palette['accent']};"
                f" background-color: {theme.lighten(palette['surface2'], 0.06)}; }}"
                f"QPushButton:disabled {{ color: {palette['muted']}; border-color: {palette['border']}; }}")
        return btn

    def center_on_screen(self):
        """Center this window on the screen it will appear on."""
        try:
            screen = self.screen() or (self.windowHandle() and self.windowHandle().screen())
            geo = (screen.availableGeometry() if screen else None)
            if geo is not None:
                fg = self.frameGeometry()
                fg.moveCenter(geo.center())
                self.move(fg.topLeft())
        except Exception:
            pass
