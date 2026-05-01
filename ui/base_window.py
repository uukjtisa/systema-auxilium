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

from PyQt6.QtWidgets import QWidget, QFrame
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QRegion


class BaseWindow(QWidget):
    """Frameless, resizable, draggable window base class."""

    # Override in subclasses that have a shorter / taller header bar
    _header_height: int = 50

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

    def apply_rounded_mask(self):
        """Apply a 12-px rounded-corner clip mask to the window."""
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
        """Create 8 invisible edge/corner resize handles and install event filter."""
        handle_size = 8
        corner_size = 16

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
        hs = 8   # handle_size
        cs = 16  # corner_size
        hh = self._header_height

        self.resize_handles['top'].setGeometry(cs, hh, w - 2 * cs, hs)
        self.resize_handles['bottom'].setGeometry(cs, h - hs, w - 2 * cs, hs)
        self.resize_handles['left'].setGeometry(0, cs, hs, h - 2 * cs)
        self.resize_handles['right'].setGeometry(w - hs, cs, hs, h - 2 * cs)

        self.resize_handles['top-left'].setGeometry(0, hh, cs, cs)
        self.resize_handles['top-right'].setGeometry(w - cs, hh, cs, cs)
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
