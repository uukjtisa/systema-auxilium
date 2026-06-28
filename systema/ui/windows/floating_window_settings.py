"""
ui/floating_window_settings.py
Appearance Settings Window - Configure floating window appearance
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QLineEdit, QSlider, QRadioButton,
                             QButtonGroup, QGroupBox, QCheckBox, QGridLayout,
                             QScrollArea, QFrame, QComboBox, QFontComboBox,
                             QStackedWidget)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QRegion, QImage, QPolygonF, QBrush, QLinearGradient
import math
from systema.ui.base_window import BaseWindow
from systema.ui import theme as _theme

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
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMinimumSize(700, 550)
        self.resize(900, 800)

        # Main container
        self.container = QWidget()
        self.container.setStyleSheet(self._container_qss())
        self.container.setObjectName("container")

        self.init_ui()

        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        self.apply_rounded_mask()
        self.create_resize_handles()
        self._sync_glass()

    # ── Theme integration ────────────────────────────────────────────────

    def _palette(self):
        """Resolved palette for the active app theme."""
        ctrl = getattr(self.floating_window, 'controller', None)
        if ctrl is not None:
            return _theme.current_palette(ctrl)
        return _theme.resolve_palette(_theme.THEMES[_theme.DEFAULT_THEME_KEY])

    def _acc_lbl(self, min_width=36):
        """Accent-coloured value readout label style."""
        return f"color: {self._palette()['accent']}; min-width: {min_width}px;"

    def _hint_lbl(self):
        """Muted italic hint label style."""
        return f"color: {self._palette()['muted']}; font-style: italic; font-size: 11px;"

    def _container_qss(self):
        """Full container stylesheet built from the active theme."""
        p = self._palette()
        acc = p['accent']
        return f"""
            QWidget#container {{
                background-color: {p['surface']};
                border-radius: 12px;
            }}
            QWidget {{
                color: {p['text']};
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }}
            QLabel {{ color: {p['text']}; }}
            QGroupBox {{
                color: {p['text']};
                border: 1px solid {_theme.rgba(acc, 0.18)};
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: 600;
                font-size: 11px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: {p['muted']};
            }}
            QPushButton {{
                background-color: transparent;
                color: {p['text']};
                border: 1px solid {p['border']};
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {_theme.rgba(acc, 0.08)};
                border-color: {_theme.rgba(acc, 0.4)};
                color: {acc};
            }}
            QPushButton:pressed {{
                background-color: {_theme.rgba(acc, 0.14)};
            }}
            QLineEdit {{
                background-color: {p['bg']};
                color: {p['text']};
                border: 1px solid {_theme.rgba(acc, 0.18)};
                border-radius: 5px;
                padding: 5px 8px;
            }}
            QLineEdit:focus {{
                border-color: {_theme.rgba(acc, 0.55)};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {p['surface2']};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {acc};
                border: none;
                width: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {_theme.lighten(acc, 0.20)};
            }}
            QSlider::sub-page:horizontal {{
                background: {_theme.rgba(acc, 0.35)};
                border-radius: 2px;
            }}
            QRadioButton {{
                color: {p['text']};
                spacing: 8px;
            }}
            QRadioButton::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 2px solid {p['border']};
                background: {p['bg']};
            }}
            QRadioButton::indicator:checked {{
                background: {acc};
                border-color: {acc};
            }}
            QCheckBox {{
                color: {p['text']};
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 3px;
                border: 2px solid {p['border']};
                background: {p['bg']};
            }}
            QCheckBox::indicator:checked {{
                background: {acc};
                border-color: {acc};
            }}
            QComboBox, QFontComboBox {{
                background-color: {p['bg']};
                color: {p['text']};
                border: 1px solid {_theme.rgba(acc, 0.18)};
                border-radius: 5px;
                padding: 5px 8px;
            }}
            QComboBox:hover, QFontComboBox:hover {{
                border-color: {_theme.rgba(acc, 0.4)};
            }}
            QComboBox::drop-down, QFontComboBox::drop-down {{
                border: none;
            }}
            QComboBox::down-arrow, QFontComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {p['muted']};
                margin-right: 6px;
            }}
            QScrollArea {{
                border: none;
                background-color: {p['surface']};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {p['surface2']};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {p['border']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """

    def _nav_btn_ss(self):
        p = self._palette()
        return f"""
            QPushButton {{
                text-align: left; padding: 7px 10px; border-radius: 6px;
                border: none; background: transparent; color: {p['muted']};
                font-size: 12px;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }}
            QPushButton:checked {{
                background: {p['surface']}; color: {p['text']};
                border: 1px solid {p['border']};
            }}
            QPushButton:hover:!checked {{
                background: {_theme.rgba(p['muted'], 0.08)};
                color: {_theme.lighten(p['text'], 0.0)};
            }}
        """

    def _section_title_ss(self):
        p = self._palette()
        return (
            f"font-size: 10px; font-weight: 600; color: {p['muted']};"
            f" letter-spacing: 0.06em; text-transform: uppercase;"
            f" border-bottom: 1px solid {p['surface2']}; padding-bottom: 5px; margin-bottom: 2px;"
        )

    def apply_theme(self, theme_key=None):
        """Live-retint the appearance window. Rebuilds the UI in place from the
        active theme, preserving the current nav page."""
        try:
            saved_page = self._stack.currentIndex() if hasattr(self, '_stack') else 0
            old = self.container
            self.layout().removeWidget(old)
            old.deleteLater()
            self.container = QWidget()
            self.container.setObjectName("container")
            self.container.setStyleSheet(self._container_qss())
            self.init_ui()
            self.layout().addWidget(self.container)
            if hasattr(self, '_stack') and 0 <= saved_page < self._stack.count():
                self._switch_page(saved_page)
            self.apply_rounded_mask()
            self._sync_glass()
        except Exception as e:
            print(f"[AppearanceSettingsWindow.apply_theme] {e}")

    def _sync_glass(self):
        """Overlay a frosted-glass backdrop when glass mode is on. This window's
        body / sidebar / stack / header / footer all paint solid surfaces that
        otherwise cover the container, so they're made transparent here and the
        container is given a readable frosted panel that shows through them."""
        try:
            ctrl = getattr(self.floating_window, 'controller', None)
            if ctrl is None:
                return
            if not _theme.glass_enabled_for(ctrl, 'appearance'):
                return
            _, op = _theme.glass_state(ctrl)
            panel = _theme.glass_panel(op)   # readable frosted (dense forms)
            p = self._palette()
            self.container.setStyleSheet(
                f"QWidget#container {{ background-color: {panel}; border-radius: 12px; }}"
                f"QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}"
            )
            # Transparent-ize the structural surfaces so the frosted container
            # shows through uniformly (keep the sidebar's separator line).
            for _w in (getattr(self, '_body_widget', None),
                       getattr(self, '_header_bar', None),
                       getattr(self, '_footer_widget', None),
                       getattr(self, '_stack', None)):
                if _w is not None:
                    _w.setStyleSheet("background: transparent;")
            if getattr(self, '_sidebar_widget', None) is not None:
                self._sidebar_widget.setStyleSheet(
                    f"QWidget {{ background: transparent; border-right: 1px solid {p['border']}; }}"
                )
            from PyQt6.QtWidgets import QScrollArea
            for sa in self.container.findChildren(QScrollArea):
                sa.setStyleSheet("QScrollArea { background: transparent; border: none; }")
                if sa.viewport():
                    sa.viewport().setStyleSheet("background: transparent;")
                if sa.widget():
                    sa.widget().setStyleSheet("background: transparent;")
        except Exception as e:
            print(f"[AppearanceSettingsWindow._sync_glass] {e}")

    # ── Windows 10 transparent background fix ────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.end()

    # ── UI construction ──────────────────────────────────────────────────

    # ── Shared nav button style ──────────────────────────────────────────
    def _make_scroll_page(self):
        """Return (QScrollArea, QVBoxLayout) for a content page."""
        p = self._palette()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {p['bg']}; border: none; }}"
            f"QScrollBar:vertical {{ background: transparent; width: 6px; border-radius: 3px; }}"
            f"QScrollBar::handle:vertical {{ background: {p['surface2']}; border-radius: 3px; min-height: 20px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {p['border']}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        )
        scroll.viewport().setStyleSheet(f"background: {p['bg']};")
        inner = QWidget()
        inner.setStyleSheet(f"background: {p['bg']}; color: {p['text']};")
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(18)
        scroll.setWidget(inner)
        return scroll, vbox

    def _section_label(self, text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(self._section_title_ss())
        return lbl

    def _switch_page(self, index):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == index)

    def init_ui(self):
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header bar ──────────────────────────────────────────────────────
        header_bar = QFrame()
        self._header_bar = header_bar      # ref for glass overlay
        header_bar.setFixedHeight(44)
        header_bar.mousePressEvent = self.header_mouse_press
        header_bar.mouseMoveEvent = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        _p = self._palette()
        header_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {_p['surface']};
                border-bottom: 1px solid {_p['surface2']};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(14, 0, 10, 0)
        header_layout.setSpacing(8)

        for _col in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_col}; border-radius: 5px; border: none; }}")
            header_layout.addWidget(dot)

        header_layout.addSpacing(10)
        title = QLabel("Appearance Settings")
        title.setStyleSheet(
            f"font-size: 12.5px; font-weight: 600; color: {_p['text']};"
            f" letter-spacing: 0.01em; background: transparent;"
        )
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        for symbol, size, slot in [("−", 17, self.showMinimized), ("×", 19, self.hide)]:
            b = QPushButton(symbol)
            b.setFixedSize(26, 26)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none; border-radius: 5px;
                    color: {_p['muted']}; padding: 0;
                }}
                QPushButton:hover {{ background: {_p['surface2']}; color: {_p['text']}; }}
            """)
            b.setFont(QFont("Segoe UI", size))
            b.clicked.connect(slot)
            header_layout.addWidget(b, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(header_bar)

        # ── Body: sidebar + stacked content ─────────────────────────────────
        body = QWidget()
        self._body_widget = body           # ref for glass overlay
        body.setStyleSheet(f"background: {_p['bg']};")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # ── Sidebar ──
        sidebar = QWidget()
        self._sidebar_widget = sidebar     # ref for glass overlay
        sidebar.setFixedWidth(172)
        sidebar.setStyleSheet(
            f"QWidget {{ background: {_p['bg']}; border-right: 1px solid {_p['surface2']}; }}"
        )
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 14, 10, 14)
        sidebar_layout.setSpacing(2)

        nav_labels = ["  Icon", "  Font", "  Shape", "  Background", "  Size", "  Hitbox"]
        self._nav_buttons = []
        for i, label in enumerate(nav_labels):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setStyleSheet(self._nav_btn_ss())
            btn.clicked.connect(lambda _checked, idx=i: self._switch_page(idx))
            self._nav_buttons.append(btn)
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()
        body_layout.addWidget(sidebar)

        # ── Stacked pages ──
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background: {_p['bg']};")
        body_layout.addWidget(self._stack, stretch=1)

        layout.addWidget(body, stretch=1)

        # ── Footer ──────────────────────────────────────────────────────────
        footer = QFrame()
        self._footer_widget = footer       # ref for glass overlay
        footer.setFixedHeight(50)
        footer.setStyleSheet(
            f"QFrame {{ background: {_p['bg']}; border-top: 1px solid {_p['surface2']};"
            f" border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }}"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 0, 18, 0)
        footer_layout.setSpacing(8)

        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(self.reset_to_defaults)
        footer_layout.addWidget(reset_btn)

        footer_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.close)
        footer_layout.addWidget(cancel_btn)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply_settings)
        apply_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {_theme.rgba(_p['accent'], 0.14)};
                color: {_p['accent']};
                font-weight: 600;
                border: 1px solid {_theme.rgba(_p['accent'], 0.40)};
                border-radius: 6px;
                padding: 7px 16px;
            }}
            QPushButton:hover {{
                background-color: {_theme.rgba(_p['accent'], 0.24)};
                border-color: {_p['accent']};
                color: {_theme.lighten(_p['accent'], 0.20)};
            }}
        """)
        footer_layout.addWidget(apply_btn)
        layout.addWidget(footer)

        # ── Build pages and add to stack ────────────────────────────────────
        self._stack.addWidget(self._build_icon_page())      # 0
        self._stack.addWidget(self._build_font_page())      # 1
        self._stack.addWidget(self._build_shape_page())     # 2
        self._stack.addWidget(self._build_bg_page())        # 3
        self._stack.addWidget(self._build_size_page())      # 4
        self._stack.addWidget(self._build_hitbox_page())    # 5

        # Initial visibility
        self.on_icon_type_changed()
        self.on_transparent_toggled()
        self.on_hitbox_mode_changed()
        self.on_gradient_toggled()

    # ── Page builders ────────────────────────────────────────────────────

    def _build_icon_page(self):
        """Page 0 – Icon type, emoji grid, custom input, letter color."""
        page, vbox = self._make_scroll_page()

        # ── Icon type ──
        vbox.addWidget(self._section_label("Icon type"))
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

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_row.addWidget(self.emoji_radio)
        type_row.addWidget(self.letter_radio)
        type_row.addStretch()
        vbox.addLayout(type_row)

        # ── Emoji grid (visible only when emoji mode) ──
        self.emoji_container = QWidget()
        emoji_grid = QGridLayout(self.emoji_container)
        emoji_grid.setSpacing(4)
        cols = 8
        for i, emoji in enumerate(self.EMOJI_OPTIONS):
            btn = QPushButton(emoji)
            btn.setFixedSize(44, 44)
            _pe = self._palette()
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 22px; padding: 0;
                    background: {_pe['surface']};
                    border: 1px solid {_pe['surface2']};
                    border-radius: 6px;
                }}
                QPushButton:hover {{
                    background: {_theme.rgba(_pe['accent'],0.1)};
                    border-color: {_theme.rgba(_pe['accent'],0.45)};
                }}
                QPushButton:pressed {{ background: {_theme.rgba(_pe['accent'],0.18)}; }}
            """)
            btn.clicked.connect(lambda _checked, e=emoji: self.select_emoji(e))
            emoji_grid.addWidget(btn, i // cols, i % cols)
        vbox.addWidget(self.emoji_container)

        # Custom text input
        vbox.addWidget(self._section_label("Custom"))
        text_row = QHBoxLayout()
        self.icon_input = QLineEdit()
        self.icon_input.setText(self.settings['icon_text'])
        self.icon_input.textChanged.connect(self.on_icon_text_changed)
        text_row.addWidget(self.icon_input)
        vbox.addLayout(text_row)

        # ── Letter color (hidden when emoji mode) ──
        self.letter_color_group = QWidget()
        lc_vbox = QVBoxLayout(self.letter_color_group)
        lc_vbox.setContentsMargins(0, 0, 0, 0)
        lc_vbox.setSpacing(8)
        lc_vbox.addWidget(self._section_label("Letter color"))

        self.letter_color_picker = ColorPicker2D(self.settings['letter_color'])
        lc_vbox.addWidget(self.letter_color_picker)

        hue_row = QHBoxLayout()
        hue_row.addWidget(QLabel("Hue:"))
        self.letter_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.letter_hue_slider.setRange(0, 359)
        self.letter_hue_slider.setValue(self.letter_color_picker.hue)
        self.letter_hue_slider.valueChanged.connect(self.letter_color_picker.set_hue)
        hue_row.addWidget(self.letter_hue_slider)
        lc_vbox.addLayout(hue_row)

        self.letter_color_preview = QLabel("Preview")
        self.letter_color_preview.setFixedHeight(30)
        self.letter_color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_letter_color_preview()
        lc_vbox.addWidget(self.letter_color_preview)

        self.letter_color_picker.colorChanged.connect(self.update_letter_color_preview)
        vbox.addWidget(self.letter_color_group)

        vbox.addStretch()
        return page

    def _build_font_page(self):
        """Page 1 – Font family + icon font size."""
        page, vbox = self._make_scroll_page()

        vbox.addWidget(self._section_label("Font family"))
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Family:"))
        self.font_combo = QFontComboBox()
        if 'font_family' in self.settings:
            self.font_combo.setCurrentFont(QFont(self.settings['font_family']))
        self.font_combo.currentFontChanged.connect(self.on_font_changed)
        font_row.addWidget(self.font_combo)
        vbox.addLayout(font_row)

        vbox.addWidget(self._section_label("Icon font size"))
        fs_row = QHBoxLayout()
        fs_row.addWidget(QLabel("Size:"))
        self.icon_font_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.icon_font_size_slider.setRange(8, 200)
        self.icon_font_size_slider.setValue(self.settings.get('icon_font_size', 32))
        fs_row.addWidget(self.icon_font_size_slider)
        self.icon_font_size_label = QLabel(f"{self.settings.get('icon_font_size', 32)}px")
        self.icon_font_size_label.setStyleSheet(self._acc_lbl(36))
        fs_row.addWidget(self.icon_font_size_label)
        self.icon_font_size_slider.valueChanged.connect(self.on_icon_font_size_changed)
        vbox.addLayout(fs_row)

        vbox.addStretch()
        return page

    def _build_shape_page(self):
        """Page 2 – Shape selection + background preview."""
        page, vbox = self._make_scroll_page()

        vbox.addWidget(self._section_label("Shape"))

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
        for r in (self.shape_circle_radio, self.shape_box_radio, self.shape_star_radio):
            vbox.addWidget(r)

        vbox.addWidget(self._section_label("Preview"))
        self.bg_preview = BackgroundPreview()
        self.bg_preview.set_bg_size(
            self.settings.get('bg_width', self.settings['size']),
            self.settings.get('bg_height', self.settings['size'])
        )
        self.bg_preview.set_icon_text(self.settings['icon_text'])
        self.bg_preview.set_font_family(self.settings.get('font_family', 'Segoe UI Emoji'))
        self.bg_preview.set_shape(self.settings.get('shape', 'circle'))
        self.bg_preview.bgChanged.connect(self.on_bg_preview_dragged)
        vbox.addWidget(self.bg_preview, alignment=Qt.AlignmentFlag.AlignCenter)

        hint = QLabel("Green handles = drag to resize background")
        hint.setStyleSheet(self._hint_lbl())
        vbox.addWidget(hint)

        vbox.addStretch()
        return page

    def _build_bg_page(self):
        """Page 3 – Background color, opacity, gradient."""
        page, vbox = self._make_scroll_page()

        # Transparent toggle
        self.transparent_check = QCheckBox("Transparent Background")
        self.transparent_check.setChecked(self.settings['is_transparent'])
        self.transparent_check.toggled.connect(self.on_transparent_toggled)
        vbox.addWidget(self.transparent_check)

        # Colour container
        self.bg_color_container = QWidget()
        bcc_vbox = QVBoxLayout(self.bg_color_container)
        bcc_vbox.setContentsMargins(0, 0, 0, 0)
        bcc_vbox.setSpacing(8)

        bcc_vbox.addWidget(self._section_label("Color"))
        self.bg_color_picker = ColorPicker2D(self.settings['background_color'][:3])
        bcc_vbox.addWidget(self.bg_color_picker)

        hue_row2 = QHBoxLayout()
        hue_row2.addWidget(QLabel("Hue:"))
        self.bg_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_hue_slider.setRange(0, 359)
        self.bg_hue_slider.setValue(self.bg_color_picker.hue)
        self.bg_hue_slider.valueChanged.connect(self.bg_color_picker.set_hue)
        hue_row2.addWidget(self.bg_hue_slider)
        bcc_vbox.addLayout(hue_row2)

        op_row = QHBoxLayout()
        op_row.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 255)
        self.opacity_slider.setValue(self.settings['background_color'][3])
        op_row.addWidget(self.opacity_slider)
        self.opacity_label = QLabel(f"{self.settings['background_color'][3]}")
        self.opacity_label.setStyleSheet(self._acc_lbl(30))
        op_row.addWidget(self.opacity_label)
        self.opacity_slider.valueChanged.connect(lambda v: self.opacity_label.setText(str(v)))
        bcc_vbox.addLayout(op_row)

        self.bg_color_preview = QLabel("Preview")
        self.bg_color_preview.setFixedHeight(30)
        self.bg_color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_bg_color_preview()
        bcc_vbox.addWidget(self.bg_color_preview)

        vbox.addWidget(self.bg_color_container)

        self.bg_color_picker.colorChanged.connect(self.update_bg_color_preview)
        self.opacity_slider.valueChanged.connect(self.update_bg_color_preview)

        # Gradient
        self.gradient_check = QCheckBox("Enable Gradient")
        self.gradient_check.setChecked(self.settings.get('use_gradient', False))
        self.gradient_check.toggled.connect(self.on_gradient_toggled)
        vbox.addWidget(self.gradient_check)

        self.gradient_container = QWidget()
        grad_vbox = QVBoxLayout(self.gradient_container)
        grad_vbox.setContentsMargins(0, 0, 0, 0)
        grad_vbox.setSpacing(8)

        grad_vbox.addWidget(self._section_label("Second color"))
        c2 = self.settings.get('gradient_color2', (255, 100, 100, 200))
        self.grad_color_picker = ColorPicker2D(c2[:3])
        grad_vbox.addWidget(self.grad_color_picker)

        gh_row = QHBoxLayout()
        gh_row.addWidget(QLabel("Hue:"))
        self.grad_hue_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_hue_slider.setRange(0, 359)
        self.grad_hue_slider.setValue(self.grad_color_picker.hue)
        self.grad_hue_slider.valueChanged.connect(self.grad_color_picker.set_hue)
        gh_row.addWidget(self.grad_hue_slider)
        grad_vbox.addLayout(gh_row)

        go_row = QHBoxLayout()
        go_row.addWidget(QLabel("Opacity:"))
        self.grad_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_opacity_slider.setRange(0, 255)
        self.grad_opacity_slider.setValue(c2[3] if len(c2) > 3 else 200)
        go_row.addWidget(self.grad_opacity_slider)
        self.grad_opacity_label = QLabel(str(self.grad_opacity_slider.value()))
        self.grad_opacity_label.setStyleSheet(self._acc_lbl(30))
        go_row.addWidget(self.grad_opacity_label)
        self.grad_opacity_slider.valueChanged.connect(
            lambda v: self.grad_opacity_label.setText(str(v))
        )
        grad_vbox.addLayout(go_row)

        gd_row = QHBoxLayout()
        gd_row.addWidget(QLabel("Direction (°):"))
        self.grad_dir_slider = QSlider(Qt.Orientation.Horizontal)
        self.grad_dir_slider.setRange(0, 359)
        self.grad_dir_slider.setValue(self.settings.get('gradient_direction', 0))
        gd_row.addWidget(self.grad_dir_slider)
        self.grad_dir_label = QLabel(f"{self.settings.get('gradient_direction', 0)}°")
        self.grad_dir_label.setStyleSheet(self._acc_lbl(30))
        gd_row.addWidget(self.grad_dir_label)
        self.grad_dir_slider.valueChanged.connect(
            lambda v: self.grad_dir_label.setText(f"{v}°")
        )
        grad_vbox.addLayout(gd_row)
        vbox.addWidget(self.gradient_container)

        vbox.addStretch()
        return page

    def _build_size_page(self):
        """Page 4 – Background W/H + legacy size slider."""
        page, vbox = self._make_scroll_page()

        vbox.addWidget(self._section_label("Background size"))

        bg_w_row = QHBoxLayout()
        bg_w_row.addWidget(QLabel("Width:"))
        self.bg_w_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_w_slider.setRange(20, 300)
        self.bg_w_slider.setValue(self.settings.get('bg_width', self.settings['size']))
        bg_w_row.addWidget(self.bg_w_slider)
        self.bg_w_label = QLabel(f"{self.bg_w_slider.value()}px")
        self.bg_w_label.setStyleSheet(self._acc_lbl(36))
        bg_w_row.addWidget(self.bg_w_label)
        self.bg_w_slider.valueChanged.connect(self.on_bg_size_changed)
        vbox.addLayout(bg_w_row)

        bg_h_row = QHBoxLayout()
        bg_h_row.addWidget(QLabel("Height:"))
        self.bg_h_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_h_slider.setRange(20, 300)
        self.bg_h_slider.setValue(self.settings.get('bg_height', self.settings['size']))
        bg_h_row.addWidget(self.bg_h_slider)
        self.bg_h_label = QLabel(f"{self.bg_h_slider.value()}px")
        self.bg_h_label.setStyleSheet(self._acc_lbl(36))
        bg_h_row.addWidget(self.bg_h_label)
        self.bg_h_slider.valueChanged.connect(self.on_bg_size_changed)
        vbox.addLayout(bg_h_row)

        vbox.addWidget(self._section_label("Legacy window size"))

        sz_row = QHBoxLayout()
        sz_row.addWidget(QLabel("Size:"))
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(30, 150)
        self.size_slider.setValue(self.settings['size'])
        sz_row.addWidget(self.size_slider)
        self.size_label = QLabel(f"{self.settings['size']}px")
        self.size_label.setStyleSheet(self._acc_lbl(36))
        sz_row.addWidget(self.size_label)
        self.size_slider.valueChanged.connect(self.on_size_changed)
        vbox.addLayout(sz_row)

        vbox.addStretch()
        return page

    def _build_hitbox_page(self):
        """Page 5 – Hitbox mode, custom W/H/offset + preview."""
        page, vbox = self._make_scroll_page()

        vbox.addWidget(self._section_label("Hitbox mode"))

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
        vbox.addWidget(self.hitbox_follow_radio)
        vbox.addWidget(self.hitbox_custom_radio)

        # Custom controls container
        self.hitbox_custom_container = QWidget()
        hc_vbox = QVBoxLayout(self.hitbox_custom_container)
        hc_vbox.setContentsMargins(0, 0, 0, 0)
        hc_vbox.setSpacing(8)

        hc_vbox.addWidget(self._section_label("Preview"))
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
        hc_vbox.addWidget(self.hitbox_preview, alignment=Qt.AlignmentFlag.AlignCenter)

        info_lbl = QLabel("Red = clickable area  |  Green dot = drag to offset  |  Drag edges to resize")
        info_lbl.setStyleSheet(self._hint_lbl())
        hc_vbox.addWidget(info_lbl)

        hc_vbox.addWidget(self._section_label("Dimensions"))

        for attr, label, sig, range_ in [
            ('hitbox_w_slider', 'Width:',    'on_hitbox_w_changed',  (10, 300)),
            ('hitbox_h_slider', 'Height:',   'on_hitbox_h_changed',  (10, 300)),
            ('hitbox_ox_slider','Offset X:', 'on_hitbox_ox_changed', (-100, 100)),
            ('hitbox_oy_slider','Offset Y:', 'on_hitbox_oy_changed', (-100, 100)),
        ]:
            key_map = {
                'hitbox_w_slider':  ('hitbox_width',    'hitbox_size', 50),
                'hitbox_h_slider':  ('hitbox_height',   'hitbox_size', 50),
                'hitbox_ox_slider': ('hitbox_offset_x', None,          0),
                'hitbox_oy_slider': ('hitbox_offset_y', None,          0),
            }
            k, k2, default = key_map[attr]
            val = self.settings.get(k, self.settings.get(k2, default) if k2 else default)

            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(*range_)
            slider.setValue(val)
            row.addWidget(slider)
            lbl_name = attr.replace('_slider', '_label')
            lbl = QLabel(f"{val}px")
            lbl.setStyleSheet(self._acc_lbl(36))
            row.addWidget(lbl)
            setattr(self, attr, slider)
            setattr(self, lbl_name, lbl)
            slider.valueChanged.connect(getattr(self, sig))
            hc_vbox.addLayout(row)

        vbox.addWidget(self.hitbox_custom_container)
        vbox.addStretch()
        return page

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