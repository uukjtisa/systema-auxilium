"""
Appearance Settings Window - Configure floating window appearance
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QLineEdit, QSlider, QRadioButton,
                             QButtonGroup, QGroupBox, QCheckBox, QGridLayout,
                             QScrollArea, QFrame, QComboBox, QFontComboBox)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QRegion, QImage, QPolygonF, QBrush, QLinearGradient
import math
from ui.base_window import BaseWindow

# ── ColorPicker2D (unchanged) ────────────────────────────────────────────────

class ColorPicker2D(QWidget):
    """2D Color picker widget - solid colors only (optimized version)"""

    colorChanged = pyqtSignal()

    def __init__(self, initial_color=(255, 255, 255)):
        super().__init__()
        self.setFixedSize(200, 200)
        self.color = QColor(*initial_color[:3])
        self.hue = self.color.hue() if self.color.hue() >= 0 else 0
        self.saturation = self.color.saturation()
        self.value = self.color.value()

        self.gradient_image = None
        self.update_gradient()

    def update_gradient(self):
        self.gradient_image = QImage(self.width(), self.height(), QImage.Format.Format_RGB32)
        for x in range(self.width()):
            for y in range(self.height()):
                sat = int(255 * x / self.width())
                val = int(255 * (1 - y / self.height()))
                color = QColor.fromHsv(self.hue, sat, val)
                self.gradient_image.setPixelColor(x, y, color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.gradient_image:
            painter.drawImage(0, 0, self.gradient_image)
        x = int(self.saturation * self.width() / 255)
        y = int((255 - self.value) * self.height() / 255)
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.drawEllipse(x - 5, y - 5, 10, 10)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(x - 4, y - 4, 8, 8)

    def mousePressEvent(self, event):
        self.updateColor(event.pos())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.updateColor(event.pos())

    def updateColor(self, pos):
        x = max(0, min(pos.x(), self.width() - 1))
        y = max(0, min(pos.y(), self.height() - 1))
        self.saturation = int(255 * x / self.width())
        self.value = int(255 * (1 - y / self.height()))
        self.color = QColor.fromHsv(self.hue, self.saturation, self.value)
        self.update()
        self.colorChanged.emit()

    def set_hue(self, hue):
        self.hue = hue
        self.color = QColor.fromHsv(self.hue, self.saturation, self.value)
        self.update_gradient()
        self.colorChanged.emit()

    def get_rgb(self):
        return (self.color.red(), self.color.green(), self.color.blue())

    def set_color(self, r, g, b):
        self.color = QColor(r, g, b)
        self.hue = self.color.hue() if self.color.hue() >= 0 else 0
        self.saturation = self.color.saturation()
        self.value = self.color.value()
        self.update_gradient()
        self.colorChanged.emit()


# ── HitboxPreview  (rewritten: W/H/offset independent) ──────────────────────

class HitboxPreview(QWidget):
    """Widget to preview and drag-resize the hitbox with independent W, H, offset."""

    # Emitted whenever the user drags a handle: (w, h, offset_x, offset_y)
    hitboxChanged = pyqtSignal(int, int, int, int)

    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 220)

        # Icon visual size (square, set from outside)
        self.icon_w = 50
        self.icon_h = 50

        # Hitbox dims & offset (centre-relative)
        self.hitbox_w = 50
        self.hitbox_h = 50
        self.hitbox_ox = 0   # offset from centre, pixels
        self.hitbox_oy = 0

        self.icon_text = "🤖"
        self.font_family = "Segoe UI Emoji"
        self.setMouseTracking(True)

        self._dragging = False
        self._drag_edge = None
        self._drag_start_pos = QPoint()
        self._drag_start_w = 0
        self._drag_start_h = 0
        self._drag_start_ox = 0
        self._drag_start_oy = 0

    # ── setters (called from settings window) ──

    def set_icon_size(self, w, h=None):
        self.icon_w = w
        self.icon_h = h if h is not None else w
        self.update()

    def set_hitbox_size(self, w, h=None):
        self.hitbox_w = w
        self.hitbox_h = h if h is not None else w
        self.update()

    def set_hitbox_offset(self, ox, oy):
        self.hitbox_ox = ox
        self.hitbox_oy = oy
        self.update()

    def set_icon_text(self, text):
        self.icon_text = text
        self.update()

    def set_font_family(self, font_family):
        self.font_family = font_family
        self.update()

    # ── geometry helpers ──

    def _centre(self):
        return self.width() // 2, self.height() // 2

    def _icon_rect(self):
        cx, cy = self._centre()
        return QRect(cx - self.icon_w // 2, cy - self.icon_h // 2, self.icon_w, self.icon_h)

    def _hitbox_rect(self):
        cx, cy = self._centre()
        return QRect(cx - self.hitbox_w // 2 + self.hitbox_ox,
                     cy - self.hitbox_h // 2 + self.hitbox_oy,
                     self.hitbox_w, self.hitbox_h)

    # ── painting ──

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background grid
        pen = QPen(QColor(200, 200, 200))
        painter.setPen(pen)
        for i in range(0, self.width(), 10):
            painter.drawLine(i, 0, i, self.height())
        for i in range(0, self.height(), 10):
            painter.drawLine(0, i, self.width(), i)

        # Icon bg (circle preview)
        ir = self._icon_rect()
        painter.setBrush(QColor(100, 100, 255, 100))
        painter.setPen(QPen(QColor(100, 100, 255), 2))
        painter.drawEllipse(ir)

        # Icon text
        font = QFont(self.font_family)
        font.setPixelSize(max(8, int(min(self.icon_w, self.icon_h) * 0.6)))
        painter.setFont(font)
        painter.setPen(QPen(QColor(230, 230, 230)))
        painter.drawText(ir, Qt.AlignmentFlag.AlignCenter, self.icon_text)

        # Hitbox overlay
        hr = self._hitbox_rect()
        painter.setBrush(QColor(255, 0, 0, 50))
        painter.setPen(QPen(QColor(255, 0, 0, 200), 2))
        painter.drawRect(hr)

        # Resize handles
        hs = 8
        painter.setBrush(QColor(255, 0, 0))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        handles = self._handle_rects(hr, hs)
        for name, rect in handles.items():
            painter.drawRect(rect)

        # Centre-of-hitbox dot (drag to offset)
        painter.setBrush(QColor(0, 200, 0))
        painter.drawEllipse(hr.center().x() - 5, hr.center().y() - 5, 10, 10)

        painter.end()

    @staticmethod
    def _handle_rects(hr, hs):
        """Return dict of handle name → QRect for all 8 edge/corner handles."""
        x, y, w, h = hr.x(), hr.y(), hr.width(), hr.height()
        half = hs // 2
        return {
            'top-left':     QRect(x - half, y - half, hs, hs),
            'top':          QRect(x + w // 2 - half, y - half, hs, hs),
            'top-right':    QRect(x + w - half, y - half, hs, hs),
            'left':         QRect(x - half, y + h // 2 - half, hs, hs),
            'right':        QRect(x + w - half, y + h // 2 - half, hs, hs),
            'bottom-left':  QRect(x - half, y + h - half, hs, hs),
            'bottom':       QRect(x + w // 2 - half, y + h - half, hs, hs),
            'bottom-right': QRect(x + w - half, y + h - half, hs, hs),
        }

    # ── mouse interaction ──

    def _hit_test(self, pos):
        """Return handle name if pos is on a handle, 'centre' if on centre dot, else None."""
        hr = self._hitbox_rect()
        hs = 8
        handles = self._handle_rects(hr, hs)
        for name, rect in handles.items():
            if rect.contains(pos):
                return name
        # centre dot (green)
        centre = hr.center()
        if (pos - centre).manhattanLength() < 8:
            return 'centre'
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_test(event.pos())
            if hit:
                self._dragging = True
                self._drag_edge = hit
                self._drag_start_pos = event.pos()
                self._drag_start_w = self.hitbox_w
                self._drag_start_h = self.hitbox_h
                self._drag_start_ox = self.hitbox_ox
                self._drag_start_oy = self.hitbox_oy

    def mouseMoveEvent(self, event):
        if not self._dragging:
            # cursor feedback
            hit = self._hit_test(event.pos())
            if hit == 'centre':
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif hit in ('top', 'bottom'):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif hit in ('left', 'right'):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit in ('top-left', 'bottom-right'):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ('top-right', 'bottom-left'):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        delta = event.pos() - self._drag_start_pos
        dx, dy = delta.x(), delta.y()
        edge = self._drag_edge

        new_w = self._drag_start_w
        new_h = self._drag_start_h
        new_ox = self._drag_start_ox
        new_oy = self._drag_start_oy

        if edge == 'centre':
            # move offset
            new_ox = self._drag_start_ox + dx
            new_oy = self._drag_start_oy + dy
        else:
            # resize: each edge/corner adjusts w/h
            if 'right' in edge:
                new_w = max(10, self._drag_start_w + dx * 2)
            if 'left' in edge:
                new_w = max(10, self._drag_start_w - dx * 2)
            if 'bottom' in edge:
                new_h = max(10, self._drag_start_h + dy * 2)
            if 'top' in edge:
                new_h = max(10, self._drag_start_h - dy * 2)

        self.hitbox_w = int(min(new_w, 300))
        self.hitbox_h = int(min(new_h, 300))
        self.hitbox_ox = int(max(-100, min(new_ox, 100)))
        self.hitbox_oy = int(max(-100, min(new_oy, 100)))
        self.update()
        self.hitboxChanged.emit(self.hitbox_w, self.hitbox_h, self.hitbox_ox, self.hitbox_oy)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_edge = None


# ── BackgroundPreview (NEW: lets user drag bg W × H) ────────────────────────

class BackgroundPreview(QWidget):
    """Drag-resize preview for the background rectangle/oval/star."""

    bgChanged = pyqtSignal(int, int)  # (w, h)

    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 220)
        self.bg_w = 50
        self.bg_h = 50
        self.icon_text = "🤖"
        self.font_family = "Segoe UI Emoji"
        self.shape = 'circle'
        self.setMouseTracking(True)
        self._dragging = False
        self._drag_edge = None
        self._drag_start_pos = QPoint()
        self._drag_start_w = 50
        self._drag_start_h = 50

    def set_bg_size(self, w, h):
        self.bg_w = w
        self.bg_h = h
        self.update()

    def set_icon_text(self, t):
        self.icon_text = t
        self.update()

    def set_font_family(self, f):
        self.font_family = f
        self.update()

    def set_shape(self, s):
        self.shape = s
        self.update()

    def _bg_rect(self):
        cx, cy = self.width() // 2, self.height() // 2
        return QRect(cx - self.bg_w // 2, cy - self.bg_h // 2, self.bg_w, self.bg_h)

    @staticmethod
    def _star_polygon(rect):
        cx = rect.center().x()
        cy = rect.center().y()
        outer_r = min(rect.width(), rect.height()) / 2.0
        inner_r = outer_r * 0.382
        poly = QPolygonF()
        from PyQt6.QtCore import QPointF
        for i in range(10):
            angle_deg = -90 + i * 36
            angle_rad = math.radians(angle_deg)
            r = outer_r if i % 2 == 0 else inner_r
            poly.append(QPointF(cx + r * math.cos(angle_rad),
                                cy + r * math.sin(angle_rad)))
        return poly

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # grid
        pen = QPen(QColor(200, 200, 200))
        painter.setPen(pen)
        for i in range(0, self.width(), 10):
            painter.drawLine(i, 0, i, self.height())
        for i in range(0, self.height(), 10):
            painter.drawLine(0, i, self.width(), i)

        br = self._bg_rect()
        brf = QRectF(br)

        # shape fill
        painter.setBrush(QColor(100, 100, 255, 120))
        painter.setPen(QPen(QColor(100, 100, 255), 2))
        if self.shape == 'circle':
            painter.drawEllipse(brf)
        elif self.shape == 'box':
            painter.drawRect(brf)
        elif self.shape == 'star':
            painter.drawPolygon(self._star_polygon(br))

        # text
        font = QFont(self.font_family)
        font.setPixelSize(max(8, int(min(self.bg_w, self.bg_h) * 0.5)))
        painter.setFont(font)
        painter.setPen(QPen(QColor(230, 230, 230)))
        painter.drawText(br, Qt.AlignmentFlag.AlignCenter, self.icon_text)

        # resize handles (corners only, simpler)
        hs = 8
        half = hs // 2
        x, y, w, h = br.x(), br.y(), br.width(), br.height()
        painter.setBrush(QColor(80, 180, 80))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        for rx, ry in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]:
            painter.drawRect(rx - half, ry - half, hs, hs)

        painter.end()

    # ── mouse ──

    def _corner_hit(self, pos):
        br = self._bg_rect()
        hs = 10
        x, y, w, h = br.x(), br.y(), br.width(), br.height()
        corners = {
            'top-left': QRect(x - hs // 2, y - hs // 2, hs, hs),
            'top-right': QRect(x + w - hs // 2, y - hs // 2, hs, hs),
            'bottom-left': QRect(x - hs // 2, y + h - hs // 2, hs, hs),
            'bottom-right': QRect(x + w - hs // 2, y + h - hs // 2, hs, hs),
        }
        for name, rect in corners.items():
            if rect.contains(pos):
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._corner_hit(event.pos())
            if hit:
                self._dragging = True
                self._drag_edge = hit
                self._drag_start_pos = event.pos()
                self._drag_start_w = self.bg_w
                self._drag_start_h = self.bg_h

    def mouseMoveEvent(self, event):
        if not self._dragging:
            hit = self._corner_hit(event.pos())
            if hit:
                if hit in ('top-left', 'bottom-right'):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                else:
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        delta = event.pos() - self._drag_start_pos
        dx, dy = delta.x(), delta.y()
        edge = self._drag_edge

        new_w = self._drag_start_w
        new_h = self._drag_start_h

        if 'right' in edge:
            new_w = max(20, self._drag_start_w + dx * 2)
        if 'left' in edge:
            new_w = max(20, self._drag_start_w - dx * 2)
        if 'bottom' in edge:
            new_h = max(20, self._drag_start_h + dy * 2)
        if 'top' in edge:
            new_h = max(20, self._drag_start_h - dy * 2)

        self.bg_w = int(min(new_w, 300))
        self.bg_h = int(min(new_h, 300))
        self.update()
        self.bgChanged.emit(self.bg_w, self.bg_h)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_edge = None


# ── Main settings window ─────────────────────────────────────────────────────

class AppearanceSettingsWindow(BaseWindow):
    """Window for configuring floating window appearance - Frameless grey style"""

    _header_height: int = 44  # shorter header than default 50

    EMOJI_OPTIONS = ['🤖', '💬', '✨', '🎯', '🚀', '💡', '🔮', '🌟',
                     '⚡', '🎨', '🔥', '💎', '🎭', '🦾', '👁️', '🧠',
                     '🌈', '🦄', '🐉', '🎪', '🎬', '📱', '💻', '⚙️']

    def __init__(self, floating_window):
        super().__init__()
        self.floating_window = floating_window
        self.settings = floating_window.settings.copy()

        # Window chrome state
        self._init_chrome_state()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(700, 550)
        self.resize(900, 800)

        # Main container
        self.container = QWidget()
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #161B22;
                border-radius: 12px;
            }
            QWidget {
                color: #E6EDF3;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
            QLabel {
                color: #E6EDF3;
            }
            QGroupBox {
                color: #E6EDF3;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: 600;
                font-size: 11px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #8B949E;
            }
            QPushButton {
                background-color: transparent;
                color: #E6EDF3;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: rgba(88, 166, 255, 0.08);
                border-color: rgba(88, 166, 255, 0.4);
                color: #58A6FF;
            }
            QPushButton:pressed {
                background-color: rgba(88, 166, 255, 0.14);
            }
            QLineEdit {
                background-color: #0D1117;
                color: #E6EDF3;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 5px;
                padding: 5px 8px;
            }
            QLineEdit:focus {
                border-color: rgba(88, 166, 255, 0.55);
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #21262D;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #58A6FF;
                border: none;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #79BBFF;
            }
            QSlider::sub-page:horizontal {
                background: rgba(88, 166, 255, 0.35);
                border-radius: 2px;
            }
            QRadioButton {
                color: #E6EDF3;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid #30363D;
                background: #0D1117;
            }
            QRadioButton::indicator:checked {
                background: #58A6FF;
                border-color: #58A6FF;
            }
            QCheckBox {
                color: #E6EDF3;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 3px;
                border: 2px solid #30363D;
                background: #0D1117;
            }
            QCheckBox::indicator:checked {
                background: #58A6FF;
                border-color: #58A6FF;
            }
            QComboBox, QFontComboBox {
                background-color: #0D1117;
                color: #E6EDF3;
                border: 1px solid rgba(88, 166, 255, 0.18);
                border-radius: 5px;
                padding: 5px 8px;
            }
            QComboBox:hover, QFontComboBox:hover {
                border-color: rgba(88, 166, 255, 0.4);
            }
            QComboBox::drop-down, QFontComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow, QFontComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8B949E;
                margin-right: 6px;
            }
            QScrollArea {
                border: none;
                background-color: #161B22;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #21262D;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #30363D;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.container.setObjectName("container")

        self.init_ui()

        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        self.apply_rounded_mask()
        self.create_resize_handles()

    # ── UI construction ──────────────────────────────────────────────────

    def init_ui(self):
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header bar ── (FIX: use addStretch properly so title+buttons don't clip)
        header_bar = QFrame()
        header_bar.setFixedHeight(48)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet("""
            QFrame {
                background-color: #161B22;
                border-bottom: 1px solid rgba(88, 166, 255, 0.18);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }
        """)

        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 10, 0)
        header_layout.setSpacing(8)

        # Traffic-light dots
        for _col in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_col}; border-radius: 5px; border: none; }}")
            header_layout.addWidget(dot)

        header_layout.addSpacing(10)

        title = QLabel("Appearance Settings")
        title.setStyleSheet("font-size: 13px; font-weight: 600; color: #E6EDF3; white-space: nowrap; background: transparent;")
        header_layout.addWidget(title)

        header_layout.addStretch(1)

        minimize_btn = QPushButton("−")
        minimize_btn.setFixedSize(28, 28)
        minimize_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                border-radius: 5px; font-size: 18px;
                color: #8B949E; padding: 0px; margin: 0px;
            }
            QPushButton:hover { background: #21262D; color: #E6EDF3; }
        """)
        minimize_btn.clicked.connect(self.showMinimized)
        header_layout.addWidget(minimize_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent; border: none;
                border-radius: 5px; font-size: 20px;
                color: #8B949E; padding: 0px; margin: 0px;
            }
            QPushButton:hover { background: #EA4335; color: white; }
        """)
        close_btn.clicked.connect(self.hide)
        header_layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(header_bar)

        # ── Scrollable content ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        content_layout = QVBoxLayout(container)
        content_layout.setSpacing(15)
        content_layout.setContentsMargins(15, 15, 15, 15)

        # ──────── Icon Type ────────
        icon_group = QGroupBox("Icon Type")
        icon_layout = QVBoxLayout()

        self.icon_type_group = QButtonGroup()
        self.emoji_radio = QRadioButton("Emoji")
        self.letter_radio = QRadioButton("Letter")
        self.icon_type_group.addButton(self.emoji_radio, 0)
        self.icon_type_group.addButton(self.letter_radio, 1)

        if self.settings['icon_type'] == 'emoji':
            self.emoji_radio.setChecked(True)
        else:
            self.letter_radio.setChecked(True)

        self.emoji_radio.toggled.connect(self.on_icon_type_changed)

        icon_layout.addWidget(self.emoji_radio)
        icon_layout.addWidget(self.letter_radio)
        icon_group.setLayout(icon_layout)
        content_layout.addWidget(icon_group)

        # ──────── Icon Content ────────
        input_group = QGroupBox("Icon Content")
        input_layout = QVBoxLayout()

        # Quick emoji selector  (FIX: buttons 44×44, font 22px so emoji is not clipped)
        self.emoji_container = QWidget()
        emoji_grid = QGridLayout(self.emoji_container)
        emoji_grid.setSpacing(4)

        emoji_label = QLabel("Quick Select:")
        emoji_label.setStyleSheet("margin-bottom: 4px;")
        input_layout.addWidget(emoji_label)

        cols = 8
        for i, emoji in enumerate(self.EMOJI_OPTIONS):
            btn = QPushButton(emoji)
            btn.setFixedSize(44, 44)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 22px;
                    padding: 0px;
                    background-color: #0D1117;
                    border: 1px solid rgba(88, 166, 255, 0.15);
                    border-radius: 6px;
                }
                QPushButton:hover {
                    background-color: rgba(88, 166, 255, 0.1);
                    border-color: rgba(88, 166, 255, 0.45);
                }
                QPushButton:pressed {
                    background-color: rgba(88, 166, 255, 0.18);
                }
            """)
            btn.clicked.connect(lambda checked, e=emoji: self.select_emoji(e))
            emoji_grid.addWidget(btn, i // cols, i % cols)

        input_layout.addWidget(self.emoji_container)

        # Custom text input – NO maxLength so any length is allowed
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel("Custom:"))
        self.icon_input = QLineEdit()
        self.icon_input.setText(self.settings['icon_text'])
        # maxLength removed – unlimited
        self.icon_input.textChanged.connect(self.on_icon_text_changed)
        text_layout.addWidget(self.icon_input)
        input_layout.addLayout(text_layout)

        input_group.setLayout(input_layout)
        content_layout.addWidget(input_group)

        # ──────── Font ────────
        font_group = QGroupBox("Font")
        font_layout = QVBoxLayout()

        font_select_layout = QHBoxLayout()
        font_select_layout.addWidget(QLabel("Font Family:"))
        self.font_combo = QFontComboBox()
        if 'font_family' in self.settings:
            self.font_combo.setCurrentFont(QFont(self.settings['font_family']))
        self.font_combo.currentFontChanged.connect(self.on_font_changed)
        font_select_layout.addWidget(self.font_combo)
        font_layout.addLayout(font_select_layout)

        font_group.setLayout(font_layout)
        content_layout.addWidget(font_group)

        # ──────── Icon Font Size (NEW) ────────
        icon_fs_group = QGroupBox("Icon Font Size")
        icon_fs_layout = QVBoxLayout()

        icon_fs_slider_layout = QHBoxLayout()
        icon_fs_slider_layout.addWidget(QLabel("Font Size:"))
        self.icon_font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.icon_font_size_slider.setRange(8, 200)
        self.icon_font_size_slider.setValue(self.settings.get('icon_font_size', 32))
        icon_fs_slider_layout.addWidget(self.icon_font_size_slider)
        self.icon_font_size_label = QLabel(f"{self.settings.get('icon_font_size', 32)}px")
        icon_fs_slider_layout.addWidget(self.icon_font_size_label)
        self.icon_font_size_slider.valueChanged.connect(self.on_icon_font_size_changed)
        icon_fs_layout.addLayout(icon_fs_slider_layout)

        icon_fs_group.setLayout(icon_fs_layout)
        content_layout.addWidget(icon_fs_group)

        # ──────── Letter Color ────────
        self.letter_color_group = QGroupBox("Letter Color")
        letter_color_layout = QVBoxLayout()

        self.letter_color_picker = ColorPicker2D(self.settings['letter_color'])
        letter_color_layout.addWidget(self.letter_color_picker)

        hue_layout = QHBoxLayout()
        hue_layout.addWidget(QLabel("Hue:"))
        self.letter_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.letter_hue_slider.setRange(0, 359)
        self.letter_hue_slider.setValue(self.letter_color_picker.hue)
        self.letter_hue_slider.valueChanged.connect(self.letter_color_picker.set_hue)
        hue_layout.addWidget(self.letter_hue_slider)
        letter_color_layout.addLayout(hue_layout)

        self.letter_color_preview = QLabel("Preview")
        self.letter_color_preview.setFixedHeight(30)
        self.letter_color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_letter_color_preview()
        letter_color_layout.addWidget(self.letter_color_preview)

        self.letter_color_group.setLayout(letter_color_layout)
        content_layout.addWidget(self.letter_color_group)

        self.letter_color_picker.colorChanged.connect(self.update_letter_color_preview)

        # ──────── Shape (NEW) ────────
        shape_group = QGroupBox("Shape")
        shape_layout = QVBoxLayout()

        self.shape_btn_group = QButtonGroup()
        self.shape_circle_radio = QRadioButton("Circle / Oval")
        self.shape_box_radio = QRadioButton("Box / Rectangle")
        self.shape_star_radio = QRadioButton("Star")
        self.shape_btn_group.addButton(self.shape_circle_radio, 0)
        self.shape_btn_group.addButton(self.shape_box_radio, 1)
        self.shape_btn_group.addButton(self.shape_star_radio, 2)

        current_shape = self.settings.get('shape', 'circle')
        if current_shape == 'box':
            self.shape_box_radio.setChecked(True)
        elif current_shape == 'star':
            self.shape_star_radio.setChecked(True)
        else:
            self.shape_circle_radio.setChecked(True)

        self.shape_btn_group.buttonToggled.connect(self.on_shape_changed)

        shape_layout.addWidget(self.shape_circle_radio)
        shape_layout.addWidget(self.shape_box_radio)
        shape_layout.addWidget(self.shape_star_radio)
        shape_group.setLayout(shape_layout)
        content_layout.addWidget(shape_group)

        # ──────── Background ────────
        bg_group = QGroupBox("Background")
        bg_layout = QVBoxLayout()

        self.transparent_check = QCheckBox("Transparent Background")
        self.transparent_check.setChecked(self.settings['is_transparent'])
        self.transparent_check.toggled.connect(self.on_transparent_toggled)
        bg_layout.addWidget(self.transparent_check)

        self.bg_color_container = QWidget()
        bg_color_layout = QVBoxLayout(self.bg_color_container)

        self.bg_color_picker = ColorPicker2D(self.settings['background_color'][:3])
        bg_color_layout.addWidget(self.bg_color_picker)

        hue_layout2 = QHBoxLayout()
        hue_layout2.addWidget(QLabel("Hue:"))
        self.bg_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_hue_slider.setRange(0, 359)
        self.bg_hue_slider.setValue(self.bg_color_picker.hue)
        self.bg_hue_slider.valueChanged.connect(self.bg_color_picker.set_hue)
        hue_layout2.addWidget(self.bg_hue_slider)
        bg_color_layout.addLayout(hue_layout2)

        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.settings['background_color'][3])
        opacity_layout.addWidget(self.opacity_slider)
        self.opacity_label = QLabel(f"{self.settings['background_color'][3]}")
        opacity_layout.addWidget(self.opacity_label)
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_label.setText(str(v)))
        bg_color_layout.addLayout(opacity_layout)

        self.bg_color_preview = QLabel("Preview")
        self.bg_color_preview.setFixedHeight(30)
        self.bg_color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_bg_color_preview()
        bg_color_layout.addWidget(self.bg_color_preview)

        bg_layout.addWidget(self.bg_color_container)

        # ── Gradient toggle (NEW) ──
        self.gradient_check = QCheckBox("Enable Gradient")
        self.gradient_check.setChecked(self.settings.get('use_gradient', False))
        self.gradient_check.toggled.connect(self.on_gradient_toggled)
        bg_layout.addWidget(self.gradient_check)

        self.gradient_container = QWidget()
        grad_layout = QVBoxLayout(self.gradient_container)

        grad_label = QLabel("Second Color:")
        grad_layout.addWidget(grad_label)

        c2 = self.settings.get('gradient_color2', (255, 100, 100, 200))
        self.grad_color_picker = ColorPicker2D(c2[:3])
        grad_layout.addWidget(self.grad_color_picker)

        grad_hue_layout = QHBoxLayout()
        grad_hue_layout.addWidget(QLabel("Hue:"))
        self.grad_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_hue_slider.setRange(0, 359)
        self.grad_hue_slider.setValue(self.grad_color_picker.hue)
        self.grad_hue_slider.valueChanged.connect(self.grad_color_picker.set_hue)
        grad_hue_layout.addWidget(self.grad_hue_slider)
        grad_layout.addLayout(grad_hue_layout)

        grad_opacity_layout = QHBoxLayout()
        grad_opacity_layout.addWidget(QLabel("Opacity:"))
        self.grad_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_opacity_slider.setRange(0, 255)
        self.grad_opacity_slider.setValue(c2[3] if len(c2) > 3 else 200)
        grad_opacity_layout.addWidget(self.grad_opacity_slider)
        self.grad_opacity_label = QLabel(str(self.grad_opacity_slider.value()))
        grad_opacity_layout.addWidget(self.grad_opacity_label)
        self.grad_opacity_slider.valueChanged.connect(lambda v: self.grad_opacity_label.setText(str(v)))
        grad_layout.addLayout(grad_opacity_layout)

        grad_dir_layout = QHBoxLayout()
        grad_dir_layout.addWidget(QLabel("Direction (°):"))
        self.grad_dir_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_dir_slider.setRange(0, 359)
        self.grad_dir_slider.setValue(self.settings.get('gradient_direction', 0))
        grad_dir_layout.addWidget(self.grad_dir_slider)
        self.grad_dir_label = QLabel(f"{self.settings.get('gradient_direction', 0)}°")
        grad_dir_layout.addWidget(self.grad_dir_label)
        self.grad_dir_slider.valueChanged.connect(lambda v: self.grad_dir_label.setText(f"{v}°"))
        grad_layout.addLayout(grad_dir_layout)

        bg_layout.addWidget(self.gradient_container)

        bg_group.setLayout(bg_layout)
        content_layout.addWidget(bg_group)

        self.bg_color_picker.colorChanged.connect(self.update_bg_color_preview)
        self.opacity_slider.valueChanged.connect(self.update_bg_color_preview)

        # ──────── Background Size (NEW) ────────
        bg_size_group = QGroupBox("Background Size")
        bg_size_layout = QVBoxLayout()

        # W slider
        bg_w_layout = QHBoxLayout()
        bg_w_layout.addWidget(QLabel("Width:"))
        self.bg_w_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_w_slider.setRange(20, 300)
        self.bg_w_slider.setValue(self.settings.get('bg_width', self.settings['size']))
        bg_w_layout.addWidget(self.bg_w_slider)
        self.bg_w_label = QLabel(f"{self.bg_w_slider.value()}px")
        bg_w_layout.addWidget(self.bg_w_label)
        self.bg_w_slider.valueChanged.connect(self.on_bg_size_changed)
        bg_size_layout.addLayout(bg_w_layout)

        # H slider
        bg_h_layout = QHBoxLayout()
        bg_h_layout.addWidget(QLabel("Height:"))
        self.bg_h_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_h_slider.setRange(20, 300)
        self.bg_h_slider.setValue(self.settings.get('bg_height', self.settings['size']))
        bg_h_layout.addWidget(self.bg_h_slider)
        self.bg_h_label = QLabel(f"{self.bg_h_slider.value()}px")
        bg_h_layout.addWidget(self.bg_h_label)
        self.bg_h_slider.valueChanged.connect(self.on_bg_size_changed)
        bg_size_layout.addLayout(bg_h_layout)

        # BG preview widget
        self.bg_preview = BackgroundPreview()
        self.bg_preview.set_bg_size(self.bg_w_slider.value(), self.bg_h_slider.value())
        self.bg_preview.set_icon_text(self.settings['icon_text'])
        self.bg_preview.set_font_family(self.settings.get('font_family', 'Segoe UI Emoji'))
        self.bg_preview.set_shape(self.settings.get('shape', 'circle'))
        self.bg_preview.bgChanged.connect(self.on_bg_preview_dragged)
        bg_size_layout.addWidget(self.bg_preview, alignment=Qt.AlignmentFlag.AlignCenter)

        bg_info = QLabel("Green handles = drag to resize background")
        bg_info.setStyleSheet("color: #8B949E; font-style: italic;")
        bg_size_layout.addWidget(bg_info)

        bg_size_group.setLayout(bg_size_layout)
        content_layout.addWidget(bg_size_group)

        # ──────── Size (legacy label kept, now controls icon_font_size context) ────────
        size_group = QGroupBox("Window Size (legacy)")
        size_layout = QVBoxLayout()

        size_slider_layout = QHBoxLayout()
        size_slider_layout.addWidget(QLabel("Size:"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(30, 150)
        self.size_slider.setValue(self.settings['size'])
        size_slider_layout.addWidget(self.size_slider)
        self.size_label = QLabel(f"{self.settings['size']}px")
        size_slider_layout.addWidget(self.size_label)
        self.size_slider.valueChanged.connect(self.on_size_changed)
        size_layout.addLayout(size_slider_layout)

        size_group.setLayout(size_layout)
        content_layout.addWidget(size_group)

        # ──────── Hitbox ────────
        hitbox_group = QGroupBox("Hitbox (Clickable Area)")
        hitbox_layout = QVBoxLayout()

        self.hitbox_mode_group = QButtonGroup()
        self.hitbox_follow_radio = QRadioButton("Follow background size")
        self.hitbox_custom_radio = QRadioButton("Custom (independent W / H / offset)")
        self.hitbox_mode_group.addButton(self.hitbox_follow_radio, 0)
        self.hitbox_mode_group.addButton(self.hitbox_custom_radio, 1)

        if self.settings['hitbox_mode'] == 'follow':
            self.hitbox_follow_radio.setChecked(True)
        else:
            self.hitbox_custom_radio.setChecked(True)

        self.hitbox_follow_radio.toggled.connect(self.on_hitbox_mode_changed)

        hitbox_layout.addWidget(self.hitbox_follow_radio)
        hitbox_layout.addWidget(self.hitbox_custom_radio)

        # Custom hitbox controls
        self.hitbox_custom_container = QWidget()
        hitbox_custom_layout = QVBoxLayout(self.hitbox_custom_container)

        # Preview widget
        self.hitbox_preview = HitboxPreview()
        self.hitbox_preview.set_icon_size(
            self.settings.get('bg_width', self.settings['size']),
            self.settings.get('bg_height', self.settings['size'])
        )
        self.hitbox_preview.set_hitbox_size(
            self.settings.get('hitbox_width', self.settings.get('hitbox_size', 50)),
            self.settings.get('hitbox_height', self.settings.get('hitbox_size', 50))
        )
        self.hitbox_preview.set_hitbox_offset(
            self.settings.get('hitbox_offset_x', 0),
            self.settings.get('hitbox_offset_y', 0)
        )
        self.hitbox_preview.set_icon_text(self.settings['icon_text'])
        self.hitbox_preview.set_font_family(self.settings.get('font_family', 'Segoe UI Emoji'))
        self.hitbox_preview.hitboxChanged.connect(self.on_hitbox_preview_dragged)
        hitbox_custom_layout.addWidget(self.hitbox_preview, alignment=Qt.AlignmentFlag.AlignCenter)

        info_label = QLabel("Red = clickable area | Green dot = drag to offset | Drag edges to resize")
        info_label.setStyleSheet("color: #8B949E; font-style: italic;")
        hitbox_custom_layout.addWidget(info_label)

        # W slider
        hb_w_layout = QHBoxLayout()
        hb_w_layout.addWidget(QLabel("Width:"))
        self.hitbox_w_slider = QSlider(Qt.Orientation.Horizontal)
        self.hitbox_w_slider.setRange(10, 300)
        self.hitbox_w_slider.setValue(self.settings.get('hitbox_width', self.settings.get('hitbox_size', 50)))
        hb_w_layout.addWidget(self.hitbox_w_slider)
        self.hitbox_w_label = QLabel(f"{self.hitbox_w_slider.value()}px")
        hb_w_layout.addWidget(self.hitbox_w_label)
        self.hitbox_w_slider.valueChanged.connect(self.on_hitbox_w_changed)
        hitbox_custom_layout.addLayout(hb_w_layout)

        # H slider
        hb_h_layout = QHBoxLayout()
        hb_h_layout.addWidget(QLabel("Height:"))
        self.hitbox_h_slider = QSlider(Qt.Orientation.Horizontal)
        self.hitbox_h_slider.setRange(10, 300)
        self.hitbox_h_slider.setValue(self.settings.get('hitbox_height', self.settings.get('hitbox_size', 50)))
        hb_h_layout.addWidget(self.hitbox_h_slider)
        self.hitbox_h_label = QLabel(f"{self.hitbox_h_slider.value()}px")
        hb_h_layout.addWidget(self.hitbox_h_label)
        self.hitbox_h_slider.valueChanged.connect(self.on_hitbox_h_changed)
        hitbox_custom_layout.addLayout(hb_h_layout)

        # Offset X
        hb_ox_layout = QHBoxLayout()
        hb_ox_layout.addWidget(QLabel("Offset X:"))
        self.hitbox_ox_slider = QSlider(Qt.Orientation.Horizontal)
        self.hitbox_ox_slider.setRange(-100, 100)
        self.hitbox_ox_slider.setValue(self.settings.get('hitbox_offset_x', 0))
        hb_ox_layout.addWidget(self.hitbox_ox_slider)
        self.hitbox_ox_label = QLabel(f"{self.hitbox_ox_slider.value()}px")
        hb_ox_layout.addWidget(self.hitbox_ox_label)
        self.hitbox_ox_slider.valueChanged.connect(self.on_hitbox_ox_changed)
        hitbox_custom_layout.addLayout(hb_ox_layout)

        # Offset Y
        hb_oy_layout = QHBoxLayout()
        hb_oy_layout.addWidget(QLabel("Offset Y:"))
        self.hitbox_oy_slider = QSlider(Qt.Orientation.Horizontal)
        self.hitbox_oy_slider.setRange(-100, 100)
        self.hitbox_oy_slider.setValue(self.settings.get('hitbox_offset_y', 0))
        hb_oy_layout.addWidget(self.hitbox_oy_slider)
        self.hitbox_oy_label = QLabel(f"{self.hitbox_oy_slider.value()}px")
        hb_oy_layout.addWidget(self.hitbox_oy_label)
        self.hitbox_oy_slider.valueChanged.connect(self.on_hitbox_oy_changed)
        hitbox_custom_layout.addLayout(hb_oy_layout)

        hitbox_layout.addWidget(self.hitbox_custom_container)
        hitbox_group.setLayout(hitbox_layout)
        content_layout.addWidget(hitbox_group)

        # ──────── Buttons ────────
        button_layout = QHBoxLayout()

        preview_btn = QPushButton("Apply")
        preview_btn.clicked.connect(self.preview_settings)
        button_layout.addWidget(preview_btn)

        reset_btn = QPushButton("🔄 Reset to Defaults")
        reset_btn.clicked.connect(self.reset_to_defaults)
        button_layout.addWidget(reset_btn)

        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(cancel_btn)

        apply_btn = QPushButton("Close App")
        apply_btn.clicked.connect(self.apply_settings)
        apply_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(88, 166, 255, 0.14);
                color: #58A6FF;
                font-weight: 600;
                border: 1px solid rgba(88, 166, 255, 0.40);
                border-radius: 6px;
                padding: 7px 16px;
            }
            QPushButton:hover {
                background-color: rgba(88, 166, 255, 0.24);
                border-color: #58A6FF;
                color: #79BBFF;
            }
        """)
        button_layout.addWidget(apply_btn)

        content_layout.addLayout(button_layout)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # Initial visibility
        self.on_icon_type_changed()
        self.on_transparent_toggled()
        self.on_hitbox_mode_changed()
        self.on_gradient_toggled()

    # ── slot handlers ────────────────────────────────────────────────────

    def select_emoji(self, emoji):
        self.icon_input.setText(emoji)

    def on_icon_type_changed(self):
        is_emoji = self.emoji_radio.isChecked()
        self.emoji_container.setVisible(is_emoji)
        self.letter_color_group.setVisible(not is_emoji)

    def on_icon_text_changed(self):
        txt = self.icon_input.text()
        self.hitbox_preview.set_icon_text(txt)
        self.hitbox_preview.set_font_family(self.font_combo.currentFont().family())
        self.bg_preview.set_icon_text(txt)
        self.bg_preview.set_font_family(self.font_combo.currentFont().family())

    def on_font_changed(self):
        self.hitbox_preview.set_font_family(self.font_combo.currentFont().family())
        self.bg_preview.set_font_family(self.font_combo.currentFont().family())

    def on_icon_font_size_changed(self, value):
        self.icon_font_size_label.setText(f"{value}px")

    def on_transparent_toggled(self):
        self.bg_color_container.setVisible(not self.transparent_check.isChecked())

    def on_gradient_toggled(self):
        self.gradient_container.setVisible(self.gradient_check.isChecked() and not self.transparent_check.isChecked())

    def on_shape_changed(self):
        if self.shape_circle_radio.isChecked():
            s = 'circle'
        elif self.shape_box_radio.isChecked():
            s = 'box'
        else:
            s = 'star'
        self.bg_preview.set_shape(s)

    # ── bg size ──

    def on_bg_size_changed(self):
        w = self.bg_w_slider.value()
        h = self.bg_h_slider.value()
        self.bg_w_label.setText(f"{w}px")
        self.bg_h_label.setText(f"{h}px")
        self.bg_preview.set_bg_size(w, h)
        # Update hitbox preview icon size to match bg
        self.hitbox_preview.set_icon_size(w, h)

    def on_bg_preview_dragged(self, w, h):
        self.bg_w_slider.setValue(w)
        self.bg_h_slider.setValue(h)

    # ── hitbox ──

    def on_hitbox_mode_changed(self):
        is_custom = self.hitbox_custom_radio.isChecked()
        self.hitbox_custom_container.setVisible(is_custom)

    def on_hitbox_w_changed(self, value):
        self.hitbox_w_label.setText(f"{value}px")
        self.hitbox_preview.set_hitbox_size(value, self.hitbox_h_slider.value())

    def on_hitbox_h_changed(self, value):
        self.hitbox_h_label.setText(f"{value}px")
        self.hitbox_preview.set_hitbox_size(self.hitbox_w_slider.value(), value)

    def on_hitbox_ox_changed(self, value):
        self.hitbox_ox_label.setText(f"{value}px")
        self.hitbox_preview.set_hitbox_offset(value, self.hitbox_oy_slider.value())

    def on_hitbox_oy_changed(self, value):
        self.hitbox_oy_label.setText(f"{value}px")
        self.hitbox_preview.set_hitbox_offset(self.hitbox_ox_slider.value(), value)

    def on_hitbox_preview_dragged(self, w, h, ox, oy):
        self.hitbox_w_slider.setValue(w)
        self.hitbox_h_slider.setValue(h)
        self.hitbox_ox_slider.setValue(ox)
        self.hitbox_oy_slider.setValue(oy)

    # ── size (legacy) ──

    def on_size_changed(self, value):
        self.size_label.setText(f"{value}px")
        # legacy: also update bg if user hasn't touched bg sliders independently
        # We keep it for backward compat but bg sliders are the real control now.

    # ── colour previews ──

    def update_letter_color_preview(self):
        r, g, b = self.letter_color_picker.get_rgb()
        self.letter_color_preview.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); "
            f"color: {'white' if (r + g + b) < 384 else 'black'}; "
            f"font-weight: bold; border-radius: 5px;"
        )

    def update_bg_color_preview(self):
        r, g, b = self.bg_color_picker.get_rgb()
        a = self.opacity_slider.value()
        self.bg_color_preview.setStyleSheet(
            f"background-color: rgba({r}, {g}, {b}, {a}); "
            f"color: {'white' if (r + g + b) < 384 else 'black'}; "
            f"font-weight: bold; border-radius: 5px;"
        )

    # ── apply / get ──────────────────────────────────────────────────────

    def preview_settings(self):
        settings = self.get_current_settings()
        self.floating_window.apply_settings(settings)

    def get_current_settings(self):
        c2_rgb = self.grad_color_picker.get_rgb()
        c2_a = self.grad_opacity_slider.value()

        settings = {
            'icon_type': 'emoji' if self.emoji_radio.isChecked() else 'letter',
            'icon_text': self.icon_input.text(),
            'font_family': self.font_combo.currentFont().family(),
            'letter_color': self.letter_color_picker.get_rgb(),
            'is_transparent': self.transparent_check.isChecked(),
            'background_color': self.bg_color_picker.get_rgb() + (self.opacity_slider.value(),),
            'size': self.size_slider.value(),                        # legacy
            'hitbox_mode': 'follow' if self.hitbox_follow_radio.isChecked() else 'custom',
            'hitbox_size': self.hitbox_w_slider.value(),             # legacy compat
            'position': self.floating_window.settings.get('position'),

            # ── new keys ──
            'hitbox_width': self.hitbox_w_slider.value(),
            'hitbox_height': self.hitbox_h_slider.value(),
            'hitbox_offset_x': self.hitbox_ox_slider.value(),
            'hitbox_offset_y': self.hitbox_oy_slider.value(),

            'bg_width': self.bg_w_slider.value(),
            'bg_height': self.bg_h_slider.value(),

            'icon_font_size': self.icon_font_size_slider.value(),

            'shape': ('circle' if self.shape_circle_radio.isChecked()
                      else 'box' if self.shape_box_radio.isChecked()
                      else 'star'),

            'use_gradient': self.gradient_check.isChecked(),
            'gradient_color2': c2_rgb + (c2_a,),
            'gradient_direction': self.grad_dir_slider.value(),
        }
        return settings

    def apply_settings(self):
        settings = self.get_current_settings()
        self.floating_window.apply_settings(settings)
        self.close()

    def reset_to_defaults(self):
        defaults = self.floating_window.DEFAULT_SETTINGS

        if defaults['icon_type'] == 'emoji':
            self.emoji_radio.setChecked(True)
        else:
            self.letter_radio.setChecked(True)

        self.icon_input.setText(defaults['icon_text'])

        if 'font_family' in defaults:
            self.font_combo.setCurrentFont(QFont(defaults['font_family']))

        r, g, b = defaults['letter_color']
        self.letter_color_picker.set_color(r, g, b)

        self.transparent_check.setChecked(defaults['is_transparent'])

        r, g, b, a = defaults['background_color']
        self.bg_color_picker.set_color(r, g, b)
        self.opacity_slider.setValue(a)

        self.size_slider.setValue(defaults['size'])

        if defaults['hitbox_mode'] == 'follow':
            self.hitbox_follow_radio.setChecked(True)
        else:
            self.hitbox_custom_radio.setChecked(True)

        self.hitbox_w_slider.setValue(defaults.get('hitbox_width', defaults['hitbox_size']))
        self.hitbox_h_slider.setValue(defaults.get('hitbox_height', defaults['hitbox_size']))
        self.hitbox_ox_slider.setValue(defaults.get('hitbox_offset_x', 0))
        self.hitbox_oy_slider.setValue(defaults.get('hitbox_offset_y', 0))

        self.bg_w_slider.setValue(defaults.get('bg_width', defaults['size']))
        self.bg_h_slider.setValue(defaults.get('bg_height', defaults['size']))

        self.icon_font_size_slider.setValue(defaults.get('icon_font_size', 32))

        current_shape = defaults.get('shape', 'circle')
        if current_shape == 'box':
            self.shape_box_radio.setChecked(True)
        elif current_shape == 'star':
            self.shape_star_radio.setChecked(True)
        else:
            self.shape_circle_radio.setChecked(True)

        self.gradient_check.setChecked(defaults.get('use_gradient', False))
        c2 = defaults.get('gradient_color2', (255, 100, 100, 200))
        self.grad_color_picker.set_color(*c2[:3])
        self.grad_opacity_slider.setValue(c2[3] if len(c2) > 3 else 200)
        self.grad_dir_slider.setValue(defaults.get('gradient_direction', 0))

        self.update_letter_color_preview()
        self.update_bg_color_preview()