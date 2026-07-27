"""
ui/settings_window.py
Settings Window - Configure API key, AI provider, Puter.js settings, Google Gemini, and Voice
FIXED: Voice settings now actually work - VAD and TTS voice selection are functional
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QComboBox, QGroupBox,
                             QCheckBox, QScrollArea, QFrame, QSlider, QSpinBox, QDoubleSpinBox,
                             QPlainTextEdit, QFileDialog, QStackedWidget, QMessageBox,
                             QInputDialog)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect, QRectF, QThread, pyqtSignal
from PyQt6.QtGui import QRegion, QPainter, QColor, QFont, QPen
from systema.ui.base_window import BaseWindow
from systema.ui import theme as _theme
from systema.common.logger import _make_logger

log = _make_logger("SettingsWindow")

# itemData marking the "Custom…" entry of a provider Display list_dropdown —
# selecting it reveals a free-text input (house dependent-visibility pattern,
# same as the memory embed-model / inject-cap rows).
_CUSTOM_SENTINEL = '__custom__'


class _Spinner(QWidget):
    """Minimal painted loading spinner — a rotating arc. No emoji, no image."""

    def __init__(self, parent=None, size=46, color='#5A9CF8'):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start(16)   # ~60 fps

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._angle = (self._angle + 7) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = 4.0
        rect = QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m)
        track = QPen(QColor(255, 255, 255, 38), 3.0)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        arc = QPen(self._color, 3.0)
        arc.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(arc)
        p.drawArc(rect, -self._angle * 16, 110 * 16)   # 110° moving sweep
        p.end()


class _SettingsSaveWorker(QThread):
    """Persists settings to disk (json write + memory-block rebuild) off the
    GUI thread so clicking Save can't freeze the UI."""
    done = pyqtSignal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self._controller = controller

    def run(self):
        try:
            self._controller.save_settings()
        except Exception:
            pass
        self.done.emit()


class _SegmentedTabs(QWidget):
    """Stretch-to-fill segmented tab bar + stacked pages. Drop-in for the parts of
    QTabWidget this window uses (addTab / setCurrentIndex / currentIndex / count).
    Buttons expand equally to fill the width — no scroll arrows, no empty band.
    Colours are passed in from the window's active-theme palette."""

    def __init__(self, surface, elev, text, muted, parent=None):
        super().__init__(parent)
        self._surface, self._elev, self._text, self._muted = surface, elev, text, muted
        self._buttons = []
        self.on_change = None  # optional callback(index) fired on tab switch
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._bar = QWidget()
        self._bar.setStyleSheet(f"background: {surface};")
        self._bar_lay = QHBoxLayout(self._bar)
        self._bar_lay.setContentsMargins(10, 7, 10, 7)
        self._bar_lay.setSpacing(6)
        root.addWidget(self._bar)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

    def addTab(self, widget, label):
        idx = len(self._buttons)
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {self._muted}; border: none;"
            f" border-radius: 8px; padding: 9px 6px; font-size: 11px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.06); color: {self._text}; }}"
            f"QPushButton:checked {{ background: {self._elev}; color: #FFFFFF; font-weight: 600; }}"
        )
        btn.clicked.connect(lambda _=False, i=idx: self.setCurrentIndex(i))
        self._bar_lay.addWidget(btn, 1)   # equal stretch → tabs fill the width
        self._buttons.append(btn)
        self._stack.addWidget(widget)
        if idx == 0:
            self.setCurrentIndex(0)
        return idx

    def setCurrentIndex(self, i):
        if 0 <= i < len(self._buttons):
            self._stack.setCurrentIndex(i)
            for j, b in enumerate(self._buttons):
                b.setChecked(j == i)
            if self.on_change:
                try:
                    self.on_change(i)
                except Exception:
                    pass

    def currentIndex(self):
        return self._stack.currentIndex()

    def count(self):
        return self._stack.count()

    def widget(self, i):
        return self._stack.widget(i)


class _TokenGraphCanvas(QWidget):
    """Simple bar graph for token usage data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = []
        self._mode = "Daily"
        # Paint palette (overridden by set_palette to follow the active theme).
        self._c_bg = "#161B22"
        self._c_grid = "#21262D"
        self._c_accent = "#58A6FF"
        self._c_accent_dim = "#1A2D4A"
        self._c_muted = "#555555"

    def set_palette(self, bg, grid, accent, accent_dim, muted):
        self._c_bg, self._c_grid = bg, grid
        self._c_accent, self._c_accent_dim, self._c_muted = accent, accent_dim, muted
        self.update()

    def set_data(self, data, mode):
        self._data = data
        self._mode = mode
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 40, 10, 12, 26
        graph_w = w - pad_l - pad_r
        graph_h = h - pad_t - pad_b

        painter.fillRect(0, 0, w, h, QColor(self._c_bg))

        if not self._data:
            painter.setPen(QColor(self._c_muted))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(0, 0, w, h),
                             Qt.AlignmentFlag.AlignCenter,
                             "No token data yet.\nSend a message to start tracking.")
            painter.end()
            return

        raw_max = max(v for _, v in self._data) or 1
        padding = max(raw_max * 0.1, 10)
        max_val = raw_max + padding
        n = len(self._data)
        gap = graph_w / n
        bar_w = max(2, min(int(gap * 0.65), 48))

        # Grid lines
        painter.setPen(QPen(QColor(self._c_grid), 1))
        for i in range(1, 4):
            y = pad_t + graph_h - int(graph_h * i / 3)
            painter.drawLine(pad_l, y, pad_l + graph_w, y)

        def _fmt(v):
            return f"{v//1000}k" if v >= 1000 else str(v)

        painter.setPen(QColor(self._c_muted))
        painter.setFont(QFont("Segoe UI", 7))
        for i in range(0, 4):
            v = int(max_val * i / 3)
            y = pad_t + graph_h - int(graph_h * i / 3)
            painter.drawText(QRectF(0, y - 8, pad_l - 4, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             _fmt(v))

        accent = QColor(self._c_accent)
        accent_dim = QColor(self._c_accent_dim)
        for i, (lbl, val) in enumerate(self._data):
            x = pad_l + int(i * gap) + int((gap - bar_w) / 2)
            bar_h = max(2, int(graph_h * val / max_val))
            y = pad_t + graph_h - bar_h
            painter.fillRect(x, pad_t, bar_w, graph_h, accent_dim)
            painter.fillRect(x, y, bar_w, bar_h, accent)
            # X label — only show every other label if crowded
            if n <= 16 or i % max(1, n // 12) == 0:
                painter.setPen(QColor(self._c_muted))
                lbl_short = lbl[-5:] if len(lbl) > 5 else lbl
                painter.drawText(QRectF(x - gap / 2, pad_t + graph_h + 3, gap * 2, pad_b),
                                 Qt.AlignmentFlag.AlignCenter, lbl_short)

        painter.end()


class SettingsWindow(BaseWindow):
    """Settings window for configuring the AI assistant"""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        # Window chrome state
        self._init_chrome_state()

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

        # Window settings - BORDERLESS (resizable like chat window)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("Settings")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(600, 500)
        self.resize(600, 750)  # Default size (but resizable!)
        self.setMaximumWidth(900)  # cap horizontal stretch so it never sprawls

        # Main container for rounded corners
        self.container = QWidget()
        self.container.setAutoFillBackground(True)
        self.container.setStyleSheet("""
            QWidget#container {
                background-color: #0D1117;
                border-radius: 12px;
            }
            QWidget {
                color: #E6EDF3;
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }
            QScrollArea {
                background-color: #161B22;
            }
            QScrollArea > QWidget {
                background-color: #161B22;
            }
        """)
        self.container.setObjectName("container")

        self._suppress_dirty = False   # True while programmatically populating widgets
        self._dirty = False
        self.init_ui()
        self.load_settings()

        # Wrap everything in container for rounded corners
        wrapper_layout = QVBoxLayout(self)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self.container)

        # Wire AFTER the container is parented to self, or findChildren() sees nothing.
        self._wire_dirty_tracking()

        # Apply rounded mask
        self.apply_rounded_mask()
        self.create_resize_handles()
        self._sync_glass()

    def _sync_glass(self):
        """No-op by design: the Settings window always stays opaque for the
        readability of its dense forms, so the glass overlay never applies here.
        (It's intentionally excluded from the glass window checklist.)"""
        return

    def _palette(self):
        """Return the active theme's colours as the 7-tuple this window uses:
        (base, surface, elevated, border, accent, text, muted)."""
        p = _theme.current_palette(self.controller)
        return p['bg'], p['surface'], p['surface2'], p['border'], p['accent'], p['text'], p['muted']

    # ── quick-jump shortcuts (General tab) ──────────────────────────────────
    @staticmethod
    def _norm_title(s: str) -> str:
        """Lower-case, keep only ascii letters/digits/spaces so emoji or extra
        glyphs in a group title never break anchor matching."""
        return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()

    def _find_group(self, page, title: str):
        """First QGroupBox under ``page`` whose title contains ``title``."""
        want = self._norm_title(title)
        for gb in page.findChildren(QGroupBox):
            if want in self._norm_title(gb.title()):
                return gb
        return None

    def _jump_to(self, target, anchor: str | None = None):
        """Switch to the destination tab (or open the updater) and, when an
        anchor group is given, scroll it into view and blink-highlight it."""
        if target == "updates":
            self.controller.open_update_window(parent=self)
            return
        self._tabs.setCurrentIndex(target)
        if not anchor:
            return

        def _go():
            page = self._tabs.widget(target)
            if page is None:
                return
            grp = self._find_group(page, anchor)
            if grp is None:
                return
            sa = page if isinstance(page, QScrollArea) else page.findChild(QScrollArea)
            if sa is not None:
                sa.ensureWidgetVisible(grp, 0, 60)
            self._flash_widget(grp)

        # Let the stacked page lay out before measuring / scrolling.
        QTimer.singleShot(70, _go)

    def _flash_widget(self, w):
        """Theme-seamless 3-second accent blink over a widget (3 pulses).

        Non-destructive: overlays an accent border+tint keyed to the widget's
        objectName and restores the original stylesheet when finished."""
        from PyQt6.QtCore import QVariantAnimation

        accent = QColor(self._palette()[4])
        base_style = w.styleSheet()
        if not w.objectName():
            w.setObjectName(f"_flash_{id(w)}")
        name = w.objectName()

        anim = QVariantAnimation(self)
        anim.setDuration(3000)
        anim.setStartValue(0.0)
        anim.setEndValue(0.0)
        # Three on/off pulses across the 3 seconds.
        for k in range(7):
            anim.setKeyValueAt(k / 6.0, 1.0 if k % 2 else 0.0)

        def _apply(v):
            v = float(v)
            br = f"rgba({accent.red()},{accent.green()},{accent.blue()},{0.18 + 0.72 * v:.3f})"
            bg = f"rgba({accent.red()},{accent.green()},{accent.blue()},{0.04 + 0.16 * v:.3f})"
            w.setStyleSheet(base_style +
                            f"\nQGroupBox#{name} {{ border: 1px solid {br};"
                            f" background-color: {bg}; }}")

        def _done():
            w.setStyleSheet(base_style)
            self._flash_anim = None

        anim.valueChanged.connect(_apply)
        anim.finished.connect(_done)
        self._flash_anim = anim   # keep a ref so it isn't garbage-collected
        anim.start()

    def init_ui(self):
        """Initialize tabbed settings UI"""
        from PyQt6.QtWidgets import QTabWidget, QSlider, QRadioButton, QButtonGroup, QGridLayout
        from PyQt6.QtCore import Qt as _Qt

        # ── Palette (from the active theme) ─────────────────────────────────
        _BASE, _SURFACE, _ELEV, _BORDER, _ACCENT, _TEXT, _MUTED = self._palette()

        _INPUT = f"""
            QLineEdit, QTextEdit {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 12px;
                color: {_TEXT};
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border-color: {_ACCENT};
            }}
        """
        _COMBO = f"""
            QComboBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                color: {_TEXT};
                selection-background-color: {_ACCENT};
                selection-color: #000;
            }}
        """
        _BTN = f"""
            QPushButton {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QPushButton:hover {{
                background-color: {_theme.lighten(_ELEV, 0.10)};
                border-color: {_ACCENT};
                color: {_ACCENT};
            }}
        """
        _BTN_PRIMARY = f"""
            QPushButton {{
                background-color: {_ACCENT};
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-size: 12px;
                font-weight: 600;
                color: {_theme.darken(_ACCENT, 0.80)};
            }}
            QPushButton:hover {{ background-color: {_theme.lighten(_ACCENT, 0.20)}; }}
            QPushButton:pressed {{ background-color: {_theme.darken(_ACCENT, 0.15)}; }}
        """
        _GROUP = f"""
            QGroupBox {{
                color: {_TEXT};
                font-weight: 600;
                font-size: 11px;
                border: 1px solid {_BORDER};
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 14px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {_MUTED};
            }}
        """
        _CHECK = f"""
            QCheckBox {{ color: {_TEXT}; font-size: 11px; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border-radius: 4px;
                border: 1px solid {_BORDER};
                background: {_ELEV};
            }}
            QCheckBox::indicator:checked {{
                background: {_ACCENT};
                border-color: {_ACCENT};
            }}
        """
        _SCROLL = f"""
            QScrollArea {{ border: none; background: {_SURFACE}; }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER}; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """

        def _label(text, muted=False, bold=False, top_margin=0):
            lbl = QLabel(text)
            size = "10px" if muted else "11px"
            color = _MUTED if muted else _TEXT
            weight = "600" if bold else "400"
            lbl.setStyleSheet(
                f"color:{color}; font-size:{size}; font-weight:{weight}; margin-top:{top_margin}px;")
            lbl.setWordWrap(True)
            return lbl

        def _info_box(text):
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color:{_MUTED}; font-size:10px; background:{_ELEV}; "
                f"border:1px solid {_BORDER}; border-radius:6px; padding:10px;")
            return lbl

        def _make_scroll_tab():
            """Return (scroll_area, inner_layout)."""
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet(_SCROLL)
            inner = QWidget()
            inner.setStyleSheet(f"QWidget {{ background:{_SURFACE}; }}")
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(18, 16, 18, 16)
            lay.setSpacing(14)
            scroll.setWidget(inner)
            return scroll, lay

        # ── Update the container stylesheet to Obsidian Blue ────────────────
        self.container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_BASE};
                border-radius: 12px;
            }}
            QWidget {{ color: {_TEXT}; font-family: 'Segoe UI', system-ui, sans-serif; }}
        """)

        main_layout = QVBoxLayout(self.container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────────
        header_bar = QFrame()
        header_bar.setFixedHeight(50)
        header_bar.mousePressEvent   = self.header_mouse_press
        header_bar.mouseMoveEvent    = self.header_mouse_move
        header_bar.mouseReleaseEvent = self.header_mouse_release
        header_bar.setStyleSheet(f"""
            QFrame {{
                background-color: {_BASE};
                border-bottom: 1px solid {_BORDER};
            }}
        """)
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(16, 0, 16, 0)

        title_lbl = QLabel("Settings")
        title_lbl.setStyleSheet(f"font-size:15px; font-weight:600; color:{_TEXT}; background:transparent;")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        # Painted chrome (icon overhaul): − / × text glyphs → glyph buttons
        from systema.ui.widgets.painted_icons import MinimizeButton, CloseButton
        _min_btn = MinimizeButton(32, tooltip="Minimize")
        _min_btn.clicked.connect(self.showMinimized)
        header_layout.addWidget(_min_btn)
        _close_btn = CloseButton(32, tooltip="Close", pill=True)
        _close_btn.clicked.connect(self.hide)
        header_layout.addWidget(_close_btn)

        main_layout.addWidget(header_bar)

        # ── Tab widget ───────────────────────────────────────────────────────
        # Custom segmented tab bar: buttons stretch equally to fill the width
        # (Qt ignores QTabBar.setExpanding when a stylesheet is applied), no scroll
        # arrows, and the strip shares the content surface — no dark empty band.
        tabs = _SegmentedTabs(_SURFACE, _ELEV, _TEXT, _MUTED)
        self._tabs = tabs
        tabs.on_change = self._on_settings_tab_changed

        # ════════════════════════════════════════════════════════════════════
        # TAB 1 — AI
        # ════════════════════════════════════════════════════════════════════
        ai_scroll, ai_lay = _make_scroll_tab()

        # ── LLM Provider Script List ────────────────────────────────────────────
        provider_group = QGroupBox("AI Provider")
        provider_group.setStyleSheet(_GROUP)
        pg_lay = QVBoxLayout(provider_group)
        pg_lay.addWidget(_label("Active Provider Script:", bold=True, top_margin=3))
        _prov_row = QHBoxLayout()
        self.provider_script_combo = QComboBox()
        self.provider_script_combo.setStyleSheet(_COMBO)
        _prov_row.addWidget(self.provider_script_combo, stretch=1)
        _prov_refresh_btn = QPushButton("⟳")
        _prov_refresh_btn.setFixedWidth(48)
        _prov_refresh_btn.setStyleSheet(_BTN)
        _prov_refresh_btn.setToolTip("Refresh provider scripts list")
        _prov_refresh_btn.clicked.connect(self._refresh_llm_provider_scripts)
        _prov_row.addWidget(_prov_refresh_btn)
        pg_lay.addLayout(_prov_row)
        _open_prov_btn = QPushButton("Open Providers Folder")
        _open_prov_btn.setStyleSheet(_BTN)

        def _open_llm_providers_folder():
            import subprocess, sys as _sys
            folder = self.controller.get_llm_providers_folder()
            if _sys.platform == 'win32':
                subprocess.Popen(['explorer', folder])
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])

        _open_prov_btn.clicked.connect(_open_llm_providers_folder)
        pg_lay.addWidget(_open_prov_btn)

        # ── Dynamic per-provider settings form (Display contract) ────────────
        # Rendered from the selected script's Display dict; values persist per
        # script in settings['provider_display_values'] and are setattr()'d
        # onto the module before every request (engine _load_provider_module).
        # Editable combos embed a QLineEdit; the plain _INPUT rule would give
        # that child its own border/background and the row would read as a
        # text box instead of a dropdown. Style the inner editor flat and keep
        # the arrow region explicit.
        _COMBO_EDIT = _COMBO + f"""
            QComboBox {{ padding-right: 26px; }}
            QComboBox:editable {{ background-color: {_ELEV}; }}
            QComboBox:focus {{ border-color: {_ACCENT}; }}
            QComboBox QLineEdit {{
                background: transparent;
                border: none;
                padding: 0;
                margin: 0;
                font-size: 11px;
                color: {_TEXT};
                selection-background-color: {_ACCENT};
                selection-color: #000;
            }}
            QComboBox::drop-down {{ width: 0; border: none; }}
        """
        self._prov_form_styles = {'input': _INPUT, 'combo': _COMBO,
                                  'combo_edit': _COMBO_EDIT, 'btn': _BTN,
                                  'check': _CHECK, 'text': _TEXT, 'muted': _MUTED,
                                  'border': _BORDER, 'accent': _ACCENT,
                                  'elev': _ELEV}
        self._prov_display_cache = {}    # script_key → unsaved form edits
        self._prov_display_widgets = {}  # var → (ftype, widget, extra)
        self._prov_display_script_key = None
        self._prov_display_container = QWidget()
        _pdc_lay = QVBoxLayout(self._prov_display_container)
        _pdc_lay.setContentsMargins(0, 8, 0, 0)
        _pdc_lay.setSpacing(6)
        self._prov_display_container.setVisible(False)
        pg_lay.addWidget(self._prov_display_container)

        self._refresh_llm_provider_scripts()
        self.provider_script_combo.currentIndexChanged.connect(self._on_llm_provider_changed)
        ai_lay.addWidget(provider_group)

        # ── Manual provider copy-prompt helper ───────────────────────────────────
        self.manual_group = QGroupBox("Manual Provider Helper")
        self.manual_group.setStyleSheet(_GROUP)
        mn_lay = QVBoxLayout(self.manual_group)
        mn_lay.addWidget(_info_box(
            "Set  self.ai_provider = 'manual'  in controller.py if you want to type responses by hand.\n"
            "Use this button to copy the current system prompt to clipboard."
        ))
        copy_sys_btn = QPushButton("Copy Current System Prompt")
        copy_sys_btn.setStyleSheet(_BTN)
        copy_sys_btn.setToolTip("Copy the full effective system prompt (base + loaded skills) to clipboard")

        def _copy_system_prompt():
            from PyQt6.QtWidgets import QApplication
            try:
                prompt = self.controller.ai._get_effective_system_prompt()
            except Exception as e:
                prompt = f"[Error retrieving system prompt: {e}]"
            QApplication.clipboard().setText(prompt)
            copy_sys_btn.setText("Copied ✓")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: copy_sys_btn.setText("Copy Current System Prompt"))

        copy_sys_btn.clicked.connect(_copy_system_prompt)
        mn_lay.addWidget(copy_sys_btn)
        ai_lay.addWidget(self.manual_group)

        # ── Conversation Prefilling ──────────────────────────────────────────
        pf_group = QGroupBox("Conversation Prefilling")
        pf_group.setStyleSheet(_GROUP)
        pf_lay = QVBoxLayout(pf_group)

        self.prefilling_checkbox = QCheckBox(
            "Enable Conversation Prefilling (reinforces tool format via fake history)")
        self.prefilling_checkbox.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        self.prefilling_checkbox.setChecked(True)
        pf_lay.addWidget(self.prefilling_checkbox)

        # Source radios
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        self.pf_source_group = QButtonGroup(self)
        self.pf_radio_premade = QRadioButton("Use premade script  (PREFILLING in engine/prompts/compat.py, native.py)")
        self.pf_radio_session = QRadioButton("Use a saved session as prefilling history")
        for rb in (self.pf_radio_premade, self.pf_radio_session):
            rb.setStyleSheet(f"color:{_TEXT}; font-size:10pt; margin-left:16px;")
        self.pf_source_group.addButton(self.pf_radio_premade, 0)
        self.pf_source_group.addButton(self.pf_radio_session, 1)
        self.pf_radio_premade.setChecked(True)

        # Radios + session picker live in one container so the whole option set
        # HIDES while prefilling is disabled (useless when off).
        self.pf_options_widget = QWidget()
        _pf_opts_lay = QVBoxLayout(self.pf_options_widget)
        _pf_opts_lay.setContentsMargins(0, 0, 0, 0)

        # Session picker (visible only when session radio is selected)
        self.pf_session_widget = QWidget()
        _pf_sess_row = QHBoxLayout(self.pf_session_widget)
        _pf_sess_row.setContentsMargins(32, 0, 0, 0)
        self.pf_session_combo = QComboBox()
        self.pf_session_combo.setStyleSheet(_COMBO)

        self.pf_session_combo.setMinimumWidth(260)
        _pf_refresh_btn = QPushButton("⟳")
        _pf_refresh_btn.setStyleSheet(_BTN)
        _pf_refresh_btn.setFixedWidth(48)
        _pf_refresh_btn.setToolTip("Refresh session list")
        _pf_refresh_btn.clicked.connect(self._refresh_prefilling_sessions)
        _pf_sess_row.addWidget(QLabel("Session:"))
        _pf_sess_row.addWidget(self.pf_session_combo)
        _pf_sess_row.addWidget(_pf_refresh_btn)
        _pf_sess_row.addStretch()
        self.pf_session_widget.setVisible(False)
        _pf_opts_lay.addWidget(self.pf_radio_premade)
        _pf_opts_lay.addWidget(self.pf_radio_session)
        _pf_opts_lay.addWidget(self.pf_session_widget)
        pf_lay.addWidget(self.pf_options_widget)

        # Wire visibility — sub-options hide entirely while prefilling is off
        self.pf_radio_session.toggled.connect(self.pf_session_widget.setVisible)
        self.prefilling_checkbox.toggled.connect(self.pf_options_widget.setVisible)

        pf_lay.addWidget(_label(
            "Premade: edit PREFILLING in engine/prompts/compat.py (or PREFILLING_NATIVE "
            "in native.py).  Session: the full chat history of the chosen session is "
            "injected before every request — the AI \"remembers\" doing things that way.",
            muted=True))
        ai_lay.addWidget(pf_group)
        # ────────────────────────────────────────────────────────────────────

        # ── Token Usage Graph ────────────────────────────────────────────────
        tok_group = QGroupBox("Token Usage")
        tok_group.setStyleSheet(_GROUP)
        tg_lay = QVBoxLayout(tok_group)
        tg_lay.addWidget(_label("Estimated tokens consumed over time.", muted=True))

        _tok_mode_row = QHBoxLayout()
        _tok_modes = ["Minutes", "Hourly", "Daily", "Weekly", "Monthly", "Yearly", "All"]
        self._tok_mode_btns = {}
        self._tok_graph_mode = "Daily"
        for _m in _tok_modes:
            _mb = QPushButton(_m)
            _mb.setFixedHeight(22)
            _mb.setCheckable(True)
            _mb.setChecked(_m == "Daily")
            _mb.setStyleSheet(f"""
                QPushButton {{
                    background: {_ELEV}; border: 1px solid {_BORDER};
                    border-radius: 4px; font-size: 9px; color: {_MUTED}; padding: 0 6px;
                }}
                QPushButton:checked {{
                    background: {_ACCENT}; border-color: {_ACCENT}; color: #000;
                }}
                QPushButton:hover:!checked {{ border-color: {_ACCENT}; color: {_TEXT}; }}
            """)
            _mb.clicked.connect(lambda _checked, m=_m: self._set_tok_graph_mode(m))
            _tok_mode_row.addWidget(_mb)
            self._tok_mode_btns[_m] = _mb
        tg_lay.addLayout(_tok_mode_row)

        self._tok_canvas = _TokenGraphCanvas()
        self._tok_canvas.setMinimumHeight(160)
        self._tok_canvas.setMaximumHeight(320)
        self._tok_canvas.setSizePolicy(
            self._tok_canvas.sizePolicy().horizontalPolicy(),
            __import__('PyQt6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding
        )
        self._tok_canvas.setStyleSheet(f"background: {_ELEV}; border-radius: 6px;")
        tg_lay.addWidget(self._tok_canvas)

        self._tok_summary_lbl = QLabel("Open this tab to load data.")
        self._tok_summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 9px;")
        tg_lay.addWidget(self._tok_summary_lbl)

        tg_lay.addWidget(_label("Output Tokens", muted=True))
        self._tok_out_canvas = _TokenGraphCanvas()
        self._tok_out_canvas.setMinimumHeight(160)
        self._tok_out_canvas.setMaximumHeight(320)
        self._tok_out_canvas.setStyleSheet(f"background: {_ELEV}; border-radius: 6px;")
        tg_lay.addWidget(self._tok_out_canvas)
        self._tok_out_summary_lbl = QLabel("")
        self._tok_out_summary_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 9px;")
        tg_lay.addWidget(self._tok_out_summary_lbl)

        # Theme the graph canvases (bg / grid / bars) to the active palette.
        # All values must be hex — QColor() can't parse CSS rgba() strings.
        _grid = _theme.lighten(_ELEV, 0.10)
        _accent_dim = _theme.darken(_ACCENT, 0.60)
        for _cv in (self._tok_canvas, self._tok_out_canvas):
            _cv.set_palette(_ELEV, _grid, _ACCENT, _accent_dim, _MUTED)

        ai_lay.addWidget(tok_group)

        ai_lay.addStretch()

        # ════════════════════════════════════════════════════════════════════
        # TAB 0 — General
        # ════════════════════════════════════════════════════════════════════
        gen_scroll, gen_lay = _make_scroll_tab()

        # ── "Trying to find something?" — quick-jump shortcuts ───────────────
        # Many settings live in non-obvious tabs (e.g. Tool Calling Mode and Code
        # Execution are under System). These buttons take the user straight there.
        gen_find_group = QGroupBox("Trying to find something?")
        gen_find_group.setStyleSheet(_GROUP)
        gf_lay = QVBoxLayout(gen_find_group)
        gf_lay.addWidget(_info_box(
            "Jump straight to a setting — each shortcut opens the right tab for you."))

        _jump_style = f"""
            QPushButton {{
                background: {_ELEV};
                color: {_MUTED};
                border: 1px solid {_BORDER};
                border-radius: 7px;
                padding: 7px 11px;
                font-size: 9.5pt;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 1px solid {_ACCENT};
                color: {_TEXT};
                background: {_theme.lighten(_ELEV, 0.06)};
            }}
        """
        # Accent variant for the primary action (updates).
        _jump_style_accent = f"""
            QPushButton {{
                background: {_theme.darken(_ACCENT, 0.55)};
                color: {_TEXT};
                border: 1px solid {_theme.darken(_ACCENT, 0.30)};
                border-radius: 7px;
                padding: 7px 11px;
                font-size: 9.5pt;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                border: 1px solid {_ACCENT};
                color: #ffffff;
                background: {_theme.darken(_ACCENT, 0.35)};
            }}
        """

        # (label, target, anchor)  target = tab index (General=0, AI=1, Voice=2,
        # UI=3, Memory=4, Security=5, System=6) OR the string "updates" to open the
        # updater. anchor = a substring of the destination QGroupBox title (emoji /
        # spacing ignored); the jump scrolls to it and blink-highlights it.
        # Keep this list EVEN — the grid is two columns, so an odd count
        # leaves a ragged hole in the last row.
        _gen_shortcuts = [
            ("Check for Updates",         "updates", None),
            ("AI Provider & Model",       1, "AI Provider"),
            ("Streaming & Response Wait", 6, "AI Response"),
            ("Tool Calling Mode",         6, "Tool Calling Mode"),
            ("Code Execution",            6, "Code Execution"),
            ("Security & Approvals",      5, "Execution Policy"),
            ("File Edits — history/undo", 6, "File Tools"),
            ("Memory",                    4, "Memory"),
            ("Conversation Prefilling",   1, "Conversation Prefilling"),
            ("Token Usage",               1, "Token Usage"),
            ("Voice & Speech (TTS)",      2, "Text-to-Speech"),
            ("Theme & Appearance",        3, "Main Theme"),
            ("Chat Bubbles & Typing",     3, "Chat Bubbles"),
            ("Glass Overlay",             3, "Glass Overlay"),
            ("Android Packet Port",       6, "Android Packet"),
            ("Start at Login",            0, "Start at Login"),
        ]

        def _make_jump(target, anchor):
            return lambda: self._jump_to(target, anchor)

        gf_grid = QGridLayout()
        gf_grid.setHorizontalSpacing(7)
        gf_grid.setVerticalSpacing(7)
        for _gi, (_glabel, _gtarget, _ganchor) in enumerate(_gen_shortcuts):
            _gbtn = QPushButton(_glabel)
            _gbtn.setCursor(_Qt.CursorShape.PointingHandCursor)
            _gbtn.setStyleSheet(_jump_style_accent if _gtarget == "updates" else _jump_style)
            _gbtn.clicked.connect(_make_jump(_gtarget, _ganchor))
            gf_grid.addWidget(_gbtn, _gi // 2, _gi % 2)
        gf_grid.setColumnStretch(0, 1)
        gf_grid.setColumnStretch(1, 1)
        gf_lay.addLayout(gf_grid)
        gen_lay.addWidget(gen_find_group)

        gen_startup_group = QGroupBox("Startup")
        gen_startup_group.setStyleSheet(_GROUP)
        gen_s_lay = QVBoxLayout(gen_startup_group)
        self.open_chat_on_startup_checkbox = QCheckBox("Open chat window on startup")
        self.open_chat_on_startup_checkbox.setStyleSheet(_CHECK)
        gen_s_lay.addWidget(self.open_chat_on_startup_checkbox)
        gen_s_lay.addWidget(_info_box(
            "When enabled, the chat window will automatically open when the app starts.\n"
            "When disabled, it still pre-loads in the background for a faster first open."))
        self.open_packet_on_startup_checkbox = QCheckBox(
            "Open packet automatically on startup  (Android Bridge — Systema Auxilium Android Module)")
        self.open_packet_on_startup_checkbox.setStyleSheet(_CHECK)
        gen_s_lay.addWidget(self.open_packet_on_startup_checkbox)
        gen_s_lay.addWidget(_info_box(
            "When enabled, the Android Bridge TCP server starts automatically on launch.\n"
            "Connect the Systema Auxilium Android app over Wi-Fi LAN to remote-control the assistant."))
        gen_lay.addWidget(gen_startup_group)

        gen_display_group = QGroupBox("Chat Display")
        gen_display_group.setStyleSheet(_GROUP)
        gd_lay = QVBoxLayout(gen_display_group)
        self.show_token_count_checkbox = QCheckBox("Show token estimate in message input")
        self.show_token_count_checkbox.setStyleSheet(_CHECK)
        self.show_token_count_checkbox.setToolTip(
            "Shows a small live token counter inside the message input.\n"
            "Grows as the conversation gets longer.")
        gd_lay.addWidget(self.show_token_count_checkbox)
        gd_lay.addWidget(_info_box(
            "The estimate = your current input + entire conversation history. "
            "Useful for knowing when you're approaching model context limits."))
        gen_lay.addWidget(gen_display_group)

        # ── Desktop Shortcut (cross-OS, admin/normal hot-toggle) ─────────────
        sc_group = QGroupBox("Desktop Shortcut")
        sc_group.setStyleSheet(_GROUP)
        sc_lay = QVBoxLayout(sc_group)
        sc_lay.addWidget(_info_box(
            "Create a Systema Auxilium shortcut on your Desktop. Choose whether it "
            "launches normally or elevated — switching is applied instantly to the "
            "existing shortcut (Windows: UAC shield, Linux: pkexec, macOS: admin prompt)."))
        # Segmented toggle — the selected privilege is clearly highlighted and
        # prefixed with a check, and reflects the existing shortcut's real state.
        self._sc_priv_group = QButtonGroup(self)
        self._sc_priv_group.setExclusive(True)
        _priv_css = f"""
            QPushButton {{ background-color: {_ELEV}; color: {_MUTED};
                border: 1px solid {_BORDER}; border-radius: 8px;
                padding: 10px 16px; font-size: 12px; text-align: left; }}
            QPushButton:hover {{ border-color: {_ACCENT}; color: {_TEXT}; }}
            QPushButton:checked {{ background-color: {_ACCENT}; color: #05070a;
                border-color: {_ACCENT}; font-weight: 600; }}
        """
        self._sc_radio_normal = QPushButton("Normal privileges")
        self._sc_radio_admin = QPushButton("Run as administrator")
        for _pb in (self._sc_radio_normal, self._sc_radio_admin):
            _pb.setCheckable(True)
            _pb.setCursor(_Qt.CursorShape.PointingHandCursor)
            _pb.setStyleSheet(_priv_css)
            self._sc_priv_group.addButton(_pb)
        self._sc_radio_normal.setChecked(True)
        self._sc_priv_group.buttonToggled.connect(lambda *_: self._update_priv_labels())
        _sc_radio_row = QHBoxLayout()
        _sc_radio_row.addWidget(self._sc_radio_normal, stretch=1)
        _sc_radio_row.addWidget(self._sc_radio_admin, stretch=1)
        sc_lay.addLayout(_sc_radio_row)
        self._update_priv_labels()
        _sc_btn_row = QHBoxLayout()
        self._sc_apply_btn = QPushButton("Create Shortcut")
        self._sc_apply_btn.setStyleSheet(_BTN)
        self._sc_apply_btn.clicked.connect(self._apply_shortcut)
        _sc_remove_btn = QPushButton("Remove")
        _sc_remove_btn.setStyleSheet(_BTN)
        _sc_remove_btn.clicked.connect(self._remove_shortcut)
        _sc_btn_row.addWidget(self._sc_apply_btn, stretch=1)
        _sc_btn_row.addWidget(_sc_remove_btn)
        sc_lay.addLayout(_sc_btn_row)
        self._sc_status_lbl = QLabel("")
        self._sc_status_lbl.setWordWrap(True)
        self._sc_status_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; background: transparent; padding-top: 4px;")
        sc_lay.addWidget(self._sc_status_lbl)
        gen_lay.addWidget(sc_group)
        self._refresh_shortcut_status()

        # ── Start at Login (cross-OS, per-user; normal OR elevated) ──────────
        as_group = QGroupBox("Start at Login")
        as_group.setStyleSheet(_GROUP)
        as_lay = QVBoxLayout(as_group)
        as_lay.addWidget(_info_box(
            "Launch Systema Auxilium automatically when you log in (for THIS user). "
            "Choose normal or elevated: Windows normal = a Startup shortcut, elevated = "
            "a logon Scheduled Task (admin); Linux/macOS elevate via pkexec / an admin "
            "prompt. On Linux, when the app runs elevated the entry is installed for the "
            "invoking login user (not root). Use “Test now” to verify it."))
        # Privilege toggle (normal / administrator) — segmented, mirrors the shortcut.
        self._as_priv_group = QButtonGroup(self)
        self._as_priv_group.setExclusive(True)
        _as_priv_css = (
            f"QPushButton {{ background-color: {_ELEV}; color: {_MUTED};"
            f" border: 1px solid {_BORDER}; border-radius: 8px;"
            f" padding: 8px 14px; font-size: 12px; text-align: left; }}"
            f"QPushButton:hover {{ border-color: {_ACCENT}; color: {_TEXT}; }}"
            f"QPushButton:checked {{ background-color: {_ACCENT}; color: #05070a;"
            f" border-color: {_ACCENT}; font-weight: 600; }}")
        self._as_priv_normal = QPushButton("Normal privileges")
        self._as_priv_admin = QPushButton("Start as administrator")
        for _pb in (self._as_priv_normal, self._as_priv_admin):
            _pb.setCheckable(True)
            _pb.setCursor(_Qt.CursorShape.PointingHandCursor)
            _pb.setStyleSheet(_as_priv_css)
            self._as_priv_group.addButton(_pb)
        self._as_priv_normal.setChecked(True)
        self._as_priv_group.buttonToggled.connect(lambda *_: self._update_priv_labels())
        _as_priv_row = QHBoxLayout()
        _as_priv_row.addWidget(self._as_priv_normal, stretch=1)
        _as_priv_row.addWidget(self._as_priv_admin, stretch=1)
        as_lay.addLayout(_as_priv_row)
        # Linux only: choose the mechanism. XDG (default) is the standard GUI method;
        # systemd is a DE-independent fallback if XDG doesn't fire on this machine.
        import sys as _sys
        self._as_method = "xdg"
        if _sys.platform.startswith("linux"):
            try:
                from systema.common import autostart as _as0
                _cur_method = _as0.status().get("method")
            except Exception:
                _cur_method = None
            _m_row = QHBoxLayout()
            _m_lbl = QLabel("Method:")
            _m_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
            self._as_radio_xdg = QRadioButton("Desktop entry (XDG)")
            self._as_radio_systemd = QRadioButton("systemd user service")
            for _r in (self._as_radio_xdg, self._as_radio_systemd):
                _r.setStyleSheet(f"QRadioButton {{ color: {_TEXT}; font-size: 11px;"
                                 " background: transparent; }")
            if _cur_method == "systemd":
                self._as_radio_systemd.setChecked(True); self._as_method = "systemd"
            else:
                self._as_radio_xdg.setChecked(True)
            self._as_radio_xdg.toggled.connect(lambda on: on and setattr(self, "_as_method", "xdg"))
            self._as_radio_systemd.toggled.connect(lambda on: on and setattr(self, "_as_method", "systemd"))
            _m_row.addWidget(_m_lbl)
            _m_row.addWidget(self._as_radio_xdg)
            _m_row.addWidget(self._as_radio_systemd)
            _m_row.addStretch()
            as_lay.addLayout(_m_row)
        _as_btn_row = QHBoxLayout()
        self._as_enable_btn = QPushButton("Enable")
        self._as_enable_btn.setStyleSheet(_BTN)
        self._as_enable_btn.clicked.connect(self._apply_autostart)
        _as_disable_btn = QPushButton("Disable")
        _as_disable_btn.setStyleSheet(_BTN)
        _as_disable_btn.clicked.connect(self._remove_autostart)
        _as_test_btn = QPushButton("Test now")
        _as_test_btn.setStyleSheet(_BTN)
        _as_test_btn.clicked.connect(self._test_autostart)
        _as_btn_row.addWidget(self._as_enable_btn, stretch=1)
        _as_btn_row.addWidget(_as_disable_btn, stretch=1)
        _as_btn_row.addWidget(_as_test_btn)
        as_lay.addLayout(_as_btn_row)
        # Doctor row: Diagnose reports what decides whether autostart fires; View log
        # shows the login-time breadcrumb the Linux launcher writes.
        _as_btn_row2 = QHBoxLayout()
        _as_diag_btn = QPushButton("Diagnose")
        _as_diag_btn.setStyleSheet(_BTN)
        _as_diag_btn.clicked.connect(self._diagnose_autostart)
        _as_log_btn = QPushButton("View log")
        _as_log_btn.setStyleSheet(_BTN)
        _as_log_btn.clicked.connect(self._view_autostart_log)
        _as_btn_row2.addWidget(_as_diag_btn, stretch=1)
        _as_btn_row2.addWidget(_as_log_btn, stretch=1)
        as_lay.addLayout(_as_btn_row2)
        self._as_status_lbl = QLabel("")
        self._as_status_lbl.setWordWrap(True)
        self._as_status_lbl.setStyleSheet(
            f"color: {_MUTED}; font-size: 10px; background: transparent; padding-top: 4px;")
        as_lay.addWidget(self._as_status_lbl)
        gen_lay.addWidget(as_group)
        self._refresh_autostart_status()

        gen_lay.addStretch()
        tabs.addTab(gen_scroll, "General")

        tabs.addTab(ai_scroll, "AI")

        # ════════════════════════════════════════════════════════════════════
        # TAB 2 — Voice
        # ════════════════════════════════════════════════════════════════════
        voice_scroll, voice_lay = _make_scroll_tab()

        # Devices
        from systema.ui.widgets.mic_level_meter import MicLevelMeter
        dev_group = QGroupBox("Audio Devices")
        dev_group.setStyleSheet(_GROUP)
        dv_lay = QVBoxLayout(dev_group)
        dv_lay.addWidget(_label("Microphone:", bold=True))
        self.input_device_combo = QComboBox()
        self.input_device_combo.setStyleSheet(_COMBO)
        self.input_device_combo.currentIndexChanged.connect(self._on_mic_combo_changed)
        dv_lay.addWidget(self.input_device_combo)
        # Live input level meter — confirms the selected mic is actually heard.
        # Starts/stops with the Voice tab's visibility (see _on_settings_tab_changed).
        self.mic_meter = MicLevelMeter(self.controller)
        dv_lay.addWidget(self.mic_meter)
        # The list auto-refreshes (no manual button); poll every 2s while the
        # Voice tab is showing, rebuilding only when the device set changes.
        self.voice_setup_prompt_checkbox = QCheckBox(
            "Show microphone setup when enabling voice mode")
        self.voice_setup_prompt_checkbox.setStyleSheet(_CHECK)
        dv_lay.addWidget(self.voice_setup_prompt_checkbox)
        voice_lay.addWidget(dev_group)

        # Interrupt mode
        int_group = QGroupBox("Voice Interruption")
        int_group.setStyleSheet(_GROUP)
        int_lay = QVBoxLayout(int_group)
        self.interrupt_mode_combo = QComboBox()
        self.interrupt_mode_combo.addItem("Manual (Button in Chat)", "manual")
        self.interrupt_mode_combo.addItem("Automatic (When Speaking)", "auto")
        self.interrupt_mode_combo.setStyleSheet(_COMBO)
        int_lay.addWidget(self.interrupt_mode_combo)
        int_lay.addWidget(_label(
            "Manual: click mute in chat  ·  Automatic: TTS ducks when you speak "
            "and stops once real words are heard", muted=True))
        # Wrapped so the whole row can be HIDDEN in Manual mode (useless there).
        self.bargein_widget = QWidget()
        _bg_lay = QVBoxLayout(self.bargein_widget)
        _bg_lay.setContentsMargins(0, 0, 0, 0)
        _bg_lay.addWidget(_label("Barge-in sensitivity (Automatic mode):"))
        self.bargein_sensitivity_combo = QComboBox()
        self.bargein_sensitivity_combo.addItem("Relaxed - fewest false interrupts", "relaxed")
        self.bargein_sensitivity_combo.addItem("Balanced (default)", "balanced")
        self.bargein_sensitivity_combo.addItem("Eager - fastest reaction", "eager")
        self.bargein_sensitivity_combo.setStyleSheet(_COMBO)
        _bg_lay.addWidget(self.bargein_sensitivity_combo)
        int_lay.addWidget(self.bargein_widget)
        self.interrupt_mode_combo.currentIndexChanged.connect(
            self._update_bargein_combo_enabled)
        voice_lay.addWidget(int_group)

        # TTS
        tts_group = QGroupBox("Text-to-Speech")
        tts_group.setStyleSheet(_GROUP)
        tts_lay = QVBoxLayout(tts_group)
        tts_lay.addWidget(_label("TTS Provider:"))
        _tts_combo_row = QHBoxLayout()
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.setStyleSheet(_COMBO)
        # Edge TTS is always the first built-in option
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        # Custom script providers are added by _refresh_tts_provider_scripts()
        self.tts_provider_combo.currentIndexChanged.connect(self.on_tts_provider_changed)
        _tts_combo_row.addWidget(self.tts_provider_combo, stretch=1)
        _tts_refresh_btn = QPushButton("⟳")
        _tts_refresh_btn.setFixedWidth(48)
        _tts_refresh_btn.setStyleSheet(_BTN)
        _tts_refresh_btn.setToolTip("Refresh TTS provider scripts")
        _tts_refresh_btn.clicked.connect(self._refresh_tts_provider_scripts)
        _tts_combo_row.addWidget(_tts_refresh_btn)
        tts_lay.addLayout(_tts_combo_row)
        tts_lay.addWidget(_info_box(
            "Custom TTS scripts live in  providers/text-to-speech/\n"
            "Each .py must define:  speak(text: str, save_to: str) -> bool\n"
            "When a custom script is selected the voice dropdown below is hidden "
            "(voice config lives inside the script)."
        ))

        # Edge TTS voice sub-group (hidden when a custom script is active)
        self.edge_tts_group = QWidget()
        etg_lay = QVBoxLayout(self.edge_tts_group)
        etg_lay.setContentsMargins(0, 6, 0, 0)
        etg_lay.addWidget(_label("Edge TTS Voice:"))
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setStyleSheet(_COMBO)
        for vid, vname in [
            ("en-US-GuyNeural", "Male, American (Guy)"), ("en-US-JennyNeural", "Female, American (Jenny)"),
            ("en-GB-RyanNeural", "Male, British (Ryan)"), ("en-GB-SoniaNeural", "Female, British (Sonia)"),
            ("en-AU-NatashaNeural", "Female, Australian (Natasha)"),
            ("en-AU-WilliamNeural", "Male, Australian (William)"),
            ("en-CA-LiamNeural", "Male, Canadian (Liam)"), ("en-CA-ClaraNeural", "Female, Canadian (Clara)"),
            ("en-IN-NeerjaNeural", "Female, Indian (Neerja)"), ("en-IN-PrabhatNeural", "Male, Indian (Prabhat)"),
        ]:
            self.tts_voice_combo.addItem(vname, vid)
        etg_lay.addWidget(self.tts_voice_combo)
        tts_lay.addWidget(self.edge_tts_group)

        _open_tts_btn = QPushButton("Open TTS Providers Folder")
        _open_tts_btn.setStyleSheet(_BTN)

        def _open_tts_providers_folder():
            import subprocess, sys as _sys
            folder = self.controller.get_tts_providers_folder()
            if _sys.platform == 'win32':
                subprocess.Popen(['explorer', folder])
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', folder])
            else:
                subprocess.Popen(['xdg-open', folder])

        _open_tts_btn.clicked.connect(_open_tts_providers_folder)
        tts_lay.addWidget(_open_tts_btn)

        # ── ElevenLabs speech tag toggle ─────────────────────────────────────
        self.elevenlabs_tags_checkbox = QCheckBox("Make Agent Use ElevenLabs Speech Tags")
        self.elevenlabs_tags_checkbox.setStyleSheet(f"color:{_TEXT}; font-size:10pt;")
        self.elevenlabs_tags_checkbox.setToolTip(
            "Inserts an instruction into the Agent's system prompt to use expressive speech tags "
            "like [laugh], [sigh], [gasp], etc.\n"
            "Highly recommended when using an ElevenLabs-compatible TTS provider — "
            "makes responses sound more natural and expressive."
        )
        tts_lay.addWidget(self.elevenlabs_tags_checkbox)
        # ─────────────────────────────────────────────────────────────────────

        # ── Filler interjections toggle ──────────────────────────────────────
        self.voice_fillers_checkbox = QCheckBox("Natural filler sounds while the voice thinks")
        self.voice_fillers_checkbox.setStyleSheet(_CHECK)
        self.voice_fillers_checkbox.setToolTip(
            "When the next part of a long reply isn't ready yet, plays a short\n"
            "interjection (\"Hmm...\", \"And...\") rendered in the same voice so the\n"
            "speech never goes dead-silent. Clips are cached per voice in\n"
            "data/voice_fillers and reused."
        )
        tts_lay.addWidget(self.voice_fillers_checkbox)

        voice_lay.addWidget(tts_group)

        # VAD
        vad_group = QGroupBox("Voice Detection (VAD)")
        vad_group.setStyleSheet(_GROUP)
        vad_lay = QVBoxLayout(vad_group)
        _vt_row = QHBoxLayout()
        _vt_row.addWidget(_label("VAD Engine:"))
        self.webrtc_vad_checkbox = QCheckBox("WebRTC VAD")
        self.webrtc_vad_checkbox.setChecked(True)
        self.webrtc_vad_checkbox.setStyleSheet(_CHECK)
        self.webrtc_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        _vt_row.addWidget(self.webrtc_vad_checkbox)
        self.silero_vad_checkbox = QCheckBox("Silero VAD")
        self.silero_vad_checkbox.setStyleSheet(_CHECK)
        self.silero_vad_checkbox.stateChanged.connect(self.on_vad_settings_changed)
        _vt_row.addWidget(self.silero_vad_checkbox)
        _vt_row.addStretch()
        vad_lay.addLayout(_vt_row)

        self.webrtc_settings_widget = QWidget()
        ww_lay = QVBoxLayout(self.webrtc_settings_widget)
        ww_lay.setContentsMargins(16, 4, 0, 4)
        _wr = QHBoxLayout()
        _wr.addWidget(_label("WebRTC Aggressiveness:"))
        self.vad_combo = QComboBox()
        self.vad_combo.addItem("Level 0 – Least aggressive", 0)
        self.vad_combo.addItem("Level 1 – Low", 1)
        self.vad_combo.addItem("Level 2 – Medium", 2)
        self.vad_combo.addItem("Level 3 – High (default)", 3)
        self.vad_combo.setCurrentIndex(3)
        self.vad_combo.setStyleSheet(_COMBO)
        _wr.addWidget(self.vad_combo)
        ww_lay.addLayout(_wr)
        ww_lay.addWidget(_label("Higher = less sensitive to background noise", muted=True))
        vad_lay.addWidget(self.webrtc_settings_widget)

        self.silero_settings_widget = QWidget()
        sw_lay = QVBoxLayout(self.silero_settings_widget)
        sw_lay.setContentsMargins(16, 4, 0, 4)
        _sr = QHBoxLayout()
        _sr.addWidget(_label("Silero Threshold:"))
        self.silero_threshold_combo = QComboBox()
        self.silero_threshold_combo.addItem("0.3 (Very Sensitive)", 0.3)
        self.silero_threshold_combo.addItem("0.5 (Balanced)", 0.5)
        self.silero_threshold_combo.addItem("0.7 (Conservative)", 0.7)
        self.silero_threshold_combo.addItem("0.9 (Very Conservative)", 0.9)
        self.silero_threshold_combo.setCurrentIndex(1)
        self.silero_threshold_combo.setStyleSheet(_COMBO)
        _sr.addWidget(self.silero_threshold_combo)
        sw_lay.addLayout(_sr)
        sw_lay.addWidget(_label("Higher = fewer false positives", muted=True))
        self.silero_settings_widget.hide()
        vad_lay.addWidget(self.silero_settings_widget)
        voice_lay.addWidget(vad_group)

        voice_lay.addStretch()
        tabs.addTab(voice_scroll, "Voice")

        # ════════════════════════════════════════════════════════════════════
        # TAB 3 — UI / Appearance
        # ════════════════════════════════════════════════════════════════════
        ui_scroll, ui_lay = _make_scroll_tab()

        # ── Theme section ───────────────────────────────────────────────────
        theme_group = QGroupBox("Main Theme")
        theme_group.setStyleSheet(_GROUP)
        th_lay = QVBoxLayout(theme_group)
        th_lay.addWidget(_label(
            "Choose a color palette. Applied immediately on Save.", muted=True))
        th_lay.addSpacing(6)

        THEMES = [
            ("obsidian_blue",  "Obsidian Blue",  "Deep navy · sky blue",      ["#0D1117","#161B22","#21262D","#58A6FF"]),
            ("onyx",           "Onyx",            "Zinc gray · indigo",         ["#18181B","#1C1C1F","#27272A","#6366F1"]),
            ("carbon",         "Carbon",          "Deep dark · blurple",        ["#111214","#1E1F22","#2B2D31","#5865F2"]),
            ("midnight_rose",  "Midnight Rose",   "Purple tint · violet",       ["#120F1A","#1D1825","#2A2436","#A78BFA"]),
            ("emerald",        "Emerald",         "Forest dark · green",        ["#0D1210","#131A15","#1C2B1F","#3FB950"]),
            ("copper",         "Copper",          "Warm charcoal · orange",     ["#110D09","#1A1310","#261D17","#E8834A"]),
            ("crimson",        "Crimson",         "Volcanic dark · red",        ["#120A0A","#1C1010","#2A1515","#FF4C4C"]),
            ("arctic",         "Arctic",          "Deep ocean · cyan",          ["#0A0E12","#111620","#192030","#67E8F9"]),
            ("golden",         "Golden",          "Midnight warm · gold",       ["#0F0D08","#19160A","#252010","#F5C518"]),
            ("slate",          "Slate",           "Cool neutral · silver",      ["#0C0E12","#141820","#1E2330","#94A3B8"]),
            # Monochrome series
            ("void",           "Void",            "Pure black · monochrome",    ["#000000","#0d0d0d","#111111","#555555"]),
            ("mono_obsidian",  "Obsidian",        "Soft near-black · mono",     ["#0a0a0a","#101010","#171717","#666666"]),
            ("mono_charcoal",  "Charcoal",        "Cool dark · mono",           ["#0e0e10","#141416","#1c1c1f","#606063"]),
            ("ember",          "Ember",           "Warm dark · mono",           ["#0f0d0b","#161310","#1e1a16","#6e665c"]),
        ]

        self._theme_btn_group = QButtonGroup(self)
        self._theme_btn_group.setExclusive(True)
        self._selected_theme = self.controller.settings.get("chat_theme", "obsidian_blue")

        themes_grid = QWidget()
        themes_grid.setStyleSheet("background:transparent;")
        tg_lay = QGridLayout(themes_grid)
        tg_lay.setContentsMargins(0, 0, 0, 0)
        tg_lay.setSpacing(10)

        for idx, (key, name, desc, swatches) in enumerate(THEMES):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {_ELEV};
                    border: 2px solid {"" + _ACCENT + "" if key == self._selected_theme else _BORDER};
                    border-radius: 10px;
                }}
                QFrame:hover {{
                    border-color: {_ACCENT};
                }}
            """)
            card.setCursor(_Qt.CursorShape.PointingHandCursor)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(6)

            # Swatch row
            sw_row = QHBoxLayout()
            sw_row.setSpacing(4)
            for color in swatches:
                sw = QFrame()
                sw.setFixedSize(20, 20)
                sw.setStyleSheet(f"QFrame {{ background:{color}; border-radius:4px; border:none; }}")
                sw_row.addWidget(sw)
            sw_row.addStretch()
            cl.addLayout(sw_row)

            # Radio + Name
            rb = QRadioButton(name)
            rb.setChecked(key == self._selected_theme)
            rb.setStyleSheet(f"""
                QRadioButton {{ color:{_TEXT}; font-size:12px; font-weight:600; background:transparent; }}
                QRadioButton::indicator {{ width:14px; height:14px;
                    border-radius:7px; border:2px solid {_BORDER}; background:{_BASE}; }}
                QRadioButton::indicator:checked {{
                    background:{_ACCENT}; border-color:{_ACCENT};
                }}
            """)
            rb.setProperty("theme_key", key)
            rb.toggled.connect(lambda checked, k=key, c=card: self._on_theme_selected(checked, k, c))
            self._theme_btn_group.addButton(rb)
            cl.addWidget(rb)
            cl.addWidget(_label(desc, muted=True))

            # Make whole card clickable
            def _make_click(r): return lambda e: r.setChecked(True)
            card.mousePressEvent = _make_click(rb)

            tg_lay.addWidget(card, idx // 2, idx % 2)

        self._theme_cards = {}
        for idx, (key, *_) in enumerate(THEMES):
            self._theme_cards[key] = tg_lay.itemAtPosition(idx // 2, idx % 2).widget()

        th_lay.addWidget(themes_grid)
        ui_lay.addWidget(theme_group)

        # ── Chat bubble style section ────────────────────────────────────────
        bubble_group = QGroupBox("Chat Bubbles")
        bubble_group.setStyleSheet(_GROUP)
        bs_lay = QVBoxLayout(bubble_group)
        bs_lay.addWidget(_label(
            "How chat messages are drawn. Applied immediately on Save.", muted=True))
        bs_lay.addSpacing(6)

        BUBBLE_STYLES = [
            ("blend",   "Borderless blend",  "AI text sits directly on the backdrop; user gets a soft borderless fill (default)"),
            ("compact", "Compact bordered",  "WhatsApp-style: bordered bubbles that hug their content width"),
        ]
        self._bubble_btn_group = QButtonGroup(self)
        self._bubble_btn_group.setExclusive(True)
        self._selected_bubble_style = self.controller.settings.get("chat_bubble_style", "blend")
        if self._selected_bubble_style not in ("blend", "compact"):
            self._selected_bubble_style = "blend"

        bubbles_grid = QWidget()
        bubbles_grid.setStyleSheet("background:transparent;")
        bg_lay = QGridLayout(bubbles_grid)
        bg_lay.setContentsMargins(0, 0, 0, 0)
        bg_lay.setSpacing(10)

        self._bubble_style_cards = {}
        for idx, (key, name, desc) in enumerate(BUBBLE_STYLES):
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {_ELEV};
                    border: 2px solid {_ACCENT if key == self._selected_bubble_style else _BORDER};
                    border-radius: 10px;
                }}
                QFrame:hover {{
                    border-color: {_ACCENT};
                }}
            """)
            card.setCursor(_Qt.CursorShape.PointingHandCursor)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.setSpacing(6)

            rb = QRadioButton(name)
            rb.setChecked(key == self._selected_bubble_style)
            rb.setStyleSheet(f"""
                QRadioButton {{ color:{_TEXT}; font-size:12px; font-weight:600; background:transparent; }}
                QRadioButton::indicator {{ width:14px; height:14px;
                    border-radius:7px; border:2px solid {_BORDER}; background:{_BASE}; }}
                QRadioButton::indicator:checked {{
                    background:{_ACCENT}; border-color:{_ACCENT};
                }}
            """)
            rb.toggled.connect(lambda checked, k=key, c=card: self._on_bubble_style_selected(checked, k, c))
            self._bubble_btn_group.addButton(rb)
            cl.addWidget(rb)
            _d = _label(desc, muted=True)
            _d.setWordWrap(True)
            cl.addWidget(_d)

            def _make_bs_click(r): return lambda e: r.setChecked(True)
            card.mousePressEvent = _make_bs_click(rb)

            bg_lay.addWidget(card, 0, idx)
            self._bubble_style_cards[key] = card

        bs_lay.addWidget(bubbles_grid)

        # Typing reveal — fake-streaming typewriter animation on AI replies.
        # Pointless while real streaming is active (the stream IS the reveal),
        # so the whole block hides then — dependent-visibility rule.
        self._typing_reveal_widget = QWidget()
        _typing_lay = QVBoxLayout(self._typing_reveal_widget)
        _typing_lay.setContentsMargins(0, 0, 0, 0)
        _typing_lay.setSpacing(6)
        self.text_reveal_checkbox = QCheckBox("Typing reveal animation for AI replies")
        self.text_reveal_checkbox.setStyleSheet(_CHECK)
        self.text_reveal_checkbox.setChecked(
            bool(self.controller.settings.get('chat_text_reveal', True)))
        _typing_lay.addWidget(self.text_reveal_checkbox)

        # Typing speed (characters per second)
        _tr_row = QHBoxLayout()
        _tr_row.addWidget(_label("Typing speed:"))
        self.text_reveal_speed_slider = QSlider(_Qt.Orientation.Horizontal)
        self.text_reveal_speed_slider.setMinimum(15)
        self.text_reveal_speed_slider.setMaximum(400)
        try:
            _cps = int(self.controller.settings.get('chat_text_reveal_cps', 90))
        except Exception:
            _cps = 90
        self.text_reveal_speed_slider.setValue(max(15, min(400, _cps)))
        self.text_reveal_speed_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height:4px; background:{_BORDER}; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:{_ACCENT}; border:none;
                width:14px; height:14px; margin:-5px 0; border-radius:7px;
            }}
            QSlider::sub-page:horizontal {{ background:{_ACCENT}; border-radius:2px; }}
        """)
        _tr_row.addWidget(self.text_reveal_speed_slider, stretch=1)
        self.text_reveal_speed_label = QLabel(f"{self.text_reveal_speed_slider.value()} ch/s")
        self.text_reveal_speed_label.setFixedWidth(56)
        self.text_reveal_speed_label.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        _tr_row.addWidget(self.text_reveal_speed_label)
        self.text_reveal_speed_slider.valueChanged.connect(
            lambda v: self.text_reveal_speed_label.setText(f"{v} ch/s"))
        _typing_lay.addLayout(_tr_row)
        bs_lay.addWidget(self._typing_reveal_widget)

        # Shown INSTEAD of the typing block while streaming is live.
        self._typing_streaming_note = _info_box(
            "Replies stream in live from the provider, so the typing animation "
            "is not used. Turn streaming off (System ▸ AI Response) to use it.")
        bs_lay.addWidget(self._typing_streaming_note)

        # Shown ALONGSIDE the typing block when streaming is switched on but
        # the active provider script can't actually stream.
        self._typing_fallback_note = _info_box(
            "Streaming is enabled (System ▸ AI Response), but the active "
            "provider script does not support it — replies arrive whole, so "
            "the typing animation below is what you will see.")
        bs_lay.addWidget(self._typing_fallback_note)

        ui_lay.addWidget(bubble_group)

        # ── Glass overlay section ────────────────────────────────────────────
        glass_group = QGroupBox("Glass Overlay")
        glass_group.setStyleSheet(_GROUP)
        gl_lay = QVBoxLayout(glass_group)
        gl_lay.addWidget(_info_box(
            "Applies a semi-transparent frosted effect that lets your desktop "
            "wallpaper show through, on top of whichever theme is selected. "
            "Text-heavy panels (sidebar, logs, cards) stay frosted-opaque so "
            "they remain readable.\n\n"
            "Use the checklist below to choose which windows get the overlay. "
            "The Settings window is intentionally excluded — it always stays "
            "opaque for readability."))

        self.glass_enabled_checkbox = QCheckBox("Enable glass background")
        self.glass_enabled_checkbox.setStyleSheet(_CHECK)
        gl_lay.addWidget(self.glass_enabled_checkbox)

        # ── Per-window checklist + opacity — one container so everything HIDES
        #    while the master glass toggle is off (useless options rule) ───────
        self.glass_options_widget = QWidget()
        glo_lay = QVBoxLayout(self.glass_options_widget)
        glo_lay.setContentsMargins(0, 0, 0, 0)
        glo_lay.addWidget(_label("Apply glass to these windows:", muted=True, top_margin=6))
        self.glass_window_checkboxes = {}
        for _wkey in _theme.GLASS_WINDOWS:
            _cb = QCheckBox(_theme.GLASS_WINDOW_LABELS.get(_wkey, _wkey))
            _cb.setStyleSheet(_CHECK)
            self.glass_window_checkboxes[_wkey] = _cb
            if _wkey == 'chat':
                # Chat row carries an extra "(sidebar)" sub-toggle so the sidebar
                # can stay solid even when the rest of the chat window is glass.
                _row = QHBoxLayout()
                _row.setContentsMargins(0, 0, 0, 0)
                _row.setSpacing(10)
                _row.addWidget(_cb)
                self.glass_sidebar_checkbox = QCheckBox("(sidebar)")
                self.glass_sidebar_checkbox.setStyleSheet(_CHECK)
                _row.addWidget(self.glass_sidebar_checkbox)
                _row.addStretch()
                glo_lay.addLayout(_row)
            else:
                glo_lay.addWidget(_cb)

        # Hide the whole option set when the master glass toggle is off; the
        # sidebar sub-toggle additionally requires the Chat window to be checked.
        def _sync_glass_checklist_enabled():
            on = self.glass_enabled_checkbox.isChecked()
            self.glass_options_widget.setVisible(on)
            if hasattr(self, 'glass_sidebar_checkbox'):
                self.glass_sidebar_checkbox.setVisible(
                    self.glass_window_checkboxes['chat'].isChecked())
        self.glass_enabled_checkbox.toggled.connect(lambda _=None: _sync_glass_checklist_enabled())
        self.glass_window_checkboxes['chat'].toggled.connect(
            lambda _=None: _sync_glass_checklist_enabled())
        self._sync_glass_checklist_enabled = _sync_glass_checklist_enabled

        _op_row = QHBoxLayout()
        _op_row.addWidget(_label("Opacity:"))
        self.glass_opacity_slider = QSlider(_Qt.Orientation.Horizontal)
        self.glass_opacity_slider.setMinimum(10)
        self.glass_opacity_slider.setMaximum(98)
        self.glass_opacity_slider.setValue(75)
        self.glass_opacity_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height:4px; background:{_BORDER}; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:{_ACCENT}; border:none;
                width:14px; height:14px; margin:-5px 0; border-radius:7px;
            }}
            QSlider::sub-page:horizontal {{ background:{_ACCENT}; border-radius:2px; }}
        """)
        _op_row.addWidget(self.glass_opacity_slider, stretch=1)
        self.glass_opacity_value_label = QLabel("75%")
        self.glass_opacity_value_label.setFixedWidth(38)
        self.glass_opacity_value_label.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        _op_row.addWidget(self.glass_opacity_value_label)
        glo_lay.addLayout(_op_row)
        self.glass_opacity_slider.valueChanged.connect(
            lambda v: self.glass_opacity_value_label.setText(f"{v}%"))
        gl_lay.addWidget(self.glass_options_widget)

        ui_lay.addWidget(glass_group)
        ui_lay.addStretch()
        tabs.addTab(ui_scroll, "UI")

        # ════════════════════════════════════════════════════════════════════
        # TAB 4 — Memory
        # ════════════════════════════════════════════════════════════════════
        mem_scroll, mem_lay = _make_scroll_tab()

        mem_group = QGroupBox("Memory (RAG)")
        mem_group.setStyleSheet(_GROUP)
        mg_lay = QVBoxLayout(mem_group)
        mg_lay.addWidget(_info_box(
            "Persistent semantic memory across sessions. "
            "The assistant recalls relevant past information and injects it automatically."))
        self.memory_enabled_checkbox = QCheckBox("Enable persistent memory across sessions")
        self.memory_enabled_checkbox.setStyleSheet(_CHECK)
        mg_lay.addWidget(self.memory_enabled_checkbox)

        # Each row lives in a QWidget wrapper so dependent rows can be HIDDEN
        # outright (mode/enable gating), not just greyed out.
        _recall_roww = QWidget()
        _recall_row = QHBoxLayout(_recall_roww)
        _recall_row.setContentsMargins(0, 0, 0, 0)
        _recall_row.addWidget(_label("Memory recall mode:"))
        self.memory_recall_mode_combo = QComboBox()
        self.memory_recall_mode_combo.setStyleSheet(_COMBO)
        self.memory_recall_mode_combo.addItem("Inject all into system prompt", 'inject_all')
        self.memory_recall_mode_combo.addItem("RAG semantic recall", 'rag')
        _recall_row.addWidget(self.memory_recall_mode_combo)
        mg_lay.addWidget(_recall_roww)
        self._memory_recall_mode_row = _recall_roww

        _thr_roww = QWidget()
        _thr_row = QHBoxLayout(_thr_roww)
        _thr_row.setContentsMargins(0, 0, 0, 0)
        _thr_row.addWidget(_label("Similarity threshold:"))
        self.memory_threshold_combo = QComboBox()
        self.memory_threshold_combo.setStyleSheet(_COMBO)
        for lbl, val in [
            ("0.3 – More (less strict)", 0.3),
            ("0.4 – Balanced (default)", 0.4),
            ("0.5 – Fewer (stricter)", 0.5),
            ("0.6 – Very strict", 0.6),
            ("0.7 – Highly relevant only", 0.7),
        ]:
            self.memory_threshold_combo.addItem(lbl, val)
        _thr_row.addWidget(self.memory_threshold_combo)
        mg_lay.addWidget(_thr_roww)
        self._memory_threshold_row = _thr_roww

        _max_roww = QWidget()
        _max_row = QHBoxLayout(_max_roww)
        _max_row.setContentsMargins(0, 0, 0, 0)
        _max_row.addWidget(_label("Max memories per message:"))
        self.memory_max_combo = QComboBox()
        self.memory_max_combo.setStyleSheet(_COMBO)
        for n in [3, 5, 8, 10, 15]:
            self.memory_max_combo.addItem(str(n), n)
        _max_row.addWidget(self.memory_max_combo)
        mg_lay.addWidget(_max_roww)
        self._memory_max_row = _max_roww

        _cap_roww = QWidget()
        _cap_row = QHBoxLayout(_cap_roww)
        _cap_row.setContentsMargins(0, 0, 0, 0)
        _cap_row.addWidget(_label("Inject cap (approx tokens):"))
        self.memory_cap_combo = QComboBox()
        self.memory_cap_combo.setStyleSheet(_COMBO)
        for n in [5000, 9000, 12000]:
            self.memory_cap_combo.addItem(f"{n:,}", n)
        self.memory_cap_combo.addItem("Custom…", '__custom__')
        _cap_row.addWidget(self.memory_cap_combo)
        mg_lay.addWidget(_cap_roww)
        self._memory_cap_row = _cap_roww

        # Custom cap entry — revealed only when 'Custom…' is selected
        # (dependent-visibility rule, mirrors the custom embedding-model row).
        from PyQt6.QtGui import QIntValidator
        _capcustom_roww = QWidget()
        _capcustom_row = QHBoxLayout(_capcustom_roww)
        _capcustom_row.setContentsMargins(0, 0, 0, 0)
        _capcustom_row.addWidget(_label("Custom cap (tokens):"))
        self.memory_cap_custom_input = QLineEdit()
        self.memory_cap_custom_input.setPlaceholderText("e.g. 20000")
        self.memory_cap_custom_input.setValidator(QIntValidator(100, 200000, self))
        self.memory_cap_custom_input.setStyleSheet(_INPUT)
        _capcustom_row.addWidget(self.memory_cap_custom_input, stretch=1)
        mg_lay.addWidget(_capcustom_roww)
        self._memory_cap_custom_row = _capcustom_roww

        # Live token-usage readout (inject-all only): what the current memory set
        # costs vs. the selected cap. Kept in sync by _update_memory_rows_visibility.
        self._memory_cap_readout = _label("", muted=True)
        mg_lay.addWidget(self._memory_cap_readout)

        _model_roww = QWidget()
        _model_row = QHBoxLayout(_model_roww)
        _model_row.setContentsMargins(0, 0, 0, 0)
        _model_row.addWidget(_label("Embedding model:"))
        self.memory_model_combo = QComboBox()
        self.memory_model_combo.setStyleSheet(_COMBO)
        for lbl, val in [
            ("MiniLM-L6-v2 - fastest, default (~90 MB)",
             "sentence-transformers/all-MiniLM-L6-v2"),
            ("bge-small-en-v1.5 - better retrieval, same size (~70 MB)",
             "BAAI/bge-small-en-v1.5"),
            ("bge-base-en-v1.5 - strongest of the small models (~210 MB)",
             "BAAI/bge-base-en-v1.5"),
            ("arctic-embed-s - Snowflake small retriever (~130 MB)",
             "snowflake/snowflake-arctic-embed-s"),
            ("jina-v2-small-en - long-context small (~120 MB)",
             "jinaai/jina-embeddings-v2-small-en"),
            ("Custom fastembed model...", "__custom__"),
        ]:
            self.memory_model_combo.addItem(lbl, val)
        _model_row.addWidget(self.memory_model_combo, stretch=1)
        mg_lay.addWidget(_model_roww)
        self._memory_model_row = _model_roww

        _mcustom_roww = QWidget()
        _mcustom_row = QHBoxLayout(_mcustom_roww)
        _mcustom_row.setContentsMargins(0, 0, 0, 0)
        _mcustom_row.addWidget(_label("Custom model name:"))
        self.memory_model_custom_input = QLineEdit()
        self.memory_model_custom_input.setPlaceholderText("e.g. mixedbread-ai/mxbai-embed-large-v1")
        self.memory_model_custom_input.setStyleSheet(_INPUT)
        _mcustom_row.addWidget(self.memory_model_custom_input, stretch=1)
        mg_lay.addWidget(_mcustom_roww)
        self._memory_model_custom_row = _mcustom_roww

        self._memory_model_info = _info_box(
            "Model names come from fastembed's supported list — see\n"
            "https://qdrant.github.io/fastembed/examples/Supported_Models/\n"
            "or run  TextEmbedding.list_supported_models()  in Python.\n"
            "Changes take effect on the next app start: a not-yet-downloaded "
            "model is fetched in the background after startup, then memories "
            "re-embed automatically; a broken download falls back to the "
            "default model.")
        mg_lay.addWidget(self._memory_model_info)

        self._memory_model_restart_note = _label(
            "Model change takes effect after an app restart.", muted=True)
        mg_lay.addWidget(self._memory_model_restart_note)
        self._memory_model_restart_note.setVisible(False)
        self.memory_model_custom_input.textChanged.connect(
            lambda _=None: self._update_memory_rows_visibility())

        self.memory_enabled_checkbox.toggled.connect(
            lambda _=None: self._update_memory_rows_visibility())
        self.memory_recall_mode_combo.currentIndexChanged.connect(
            lambda _=None: self._update_memory_rows_visibility())
        self.memory_model_combo.currentIndexChanged.connect(
            lambda _=None: self._update_memory_rows_visibility())
        self.memory_cap_combo.currentIndexChanged.connect(
            lambda _=None: self._update_memory_rows_visibility())
        self.memory_cap_custom_input.textChanged.connect(
            lambda _=None: self._update_memory_rows_visibility())

        open_mem_btn = QPushButton("Open Memory Manager")
        open_mem_btn.setStyleSheet(f"""
            QPushButton {{ background:#0E1F0E; border:1px solid #1E4A1E; border-radius:6px;
                          color:#3FB950; padding:7px 14px; font-size:11px; }}
            QPushButton:hover {{ background:#122712; }}
        """)
        open_mem_btn.clicked.connect(self._open_memory_window)
        mg_lay.addWidget(open_mem_btn)

        mem_lay.addWidget(mem_group)
        mem_lay.addStretch()
        tabs.addTab(mem_scroll, "Memory")

        # ════════════════════════════════════════════════════════════════════
        # TAB 5 — Security
        # ════════════════════════════════════════════════════════════════════
        sec_scroll, sec_lay = _make_scroll_tab()

        sec_group = QGroupBox("Master Security Switch")
        sec_group.setStyleSheet(_GROUP)
        sg_lay = QVBoxLayout(sec_group)
        self.supervised_checkbox = QCheckBox(
            "Enable Supervised Execution — OFF disables ALL security")
        self.supervised_checkbox.setChecked(True)
        self.supervised_checkbox.setStyleSheet(_CHECK)
        sg_lay.addWidget(self.supervised_checkbox)
        sg_lay.addWidget(_info_box(
            "The single kill-switch for the entire security layer.\n\n"
            "ON: risky code and file changes are scanned and prompted for review "
            "before they run — edit, explain, or reject them. Obviously-safe "
            "snippets (plain print, math, a bare import) run without a prompt; "
            "tick 'Review even safe code' below to be asked for everything.\n\n"
            "OFF: the AI runs code and writes, edits, or deletes files with NO "
            "prompts and NO rules — the whole policy below is ignored, including "
            "'deny' categories. Only turn this off if you fully trust the AI."))
        sec_lay.addWidget(sec_group)

        # ── Execution policy + approval memory + audit ──────────────────────
        from systema.security import code_guard as _guard
        pol_group = QGroupBox("Execution Policy & Audit")
        pol_group.setStyleSheet(_GROUP)
        pol_lay = QVBoxLayout(pol_group)
        pol_lay.addWidget(_info_box(
            "A safety net modeled on AI-agent harnesses. Every file, process, "
            "network, dynamic-code, system and credential operation is sorted into "
            "a category you control here. Each category can be set to: ask (prompt "
            "for approval), allow (run without a prompt), or deny (always block). "
            "This policy only applies while Supervised Execution is ON — turning "
            "the master switch off disables all of it. Pick a preset for a quick "
            "start, or fine-tune each row. Works on Windows, Linux, and macOS."))

        # ── Preset row ──────────────────────────────────────────────────────
        _preset_row = QHBoxLayout()
        _preset_lbl = QLabel("Preset:")
        _preset_lbl.setStyleSheet(f"color:{_TEXT}; font-size:11px; font-weight:600;")
        _preset_row.addWidget(_preset_lbl)
        self._preset_combo = QComboBox()
        self._preset_combo.setStyleSheet(_COMBO)
        _preset_row.addWidget(self._preset_combo, 1)
        self._preset_apply = QPushButton("Apply")
        self._preset_apply.setStyleSheet(_BTN)
        self._preset_apply.setToolTip("Load the selected preset into the rows below.")
        self._preset_apply.clicked.connect(self._apply_policy_preset)
        _preset_row.addWidget(self._preset_apply)
        self._preset_save = QPushButton("Save as…")
        self._preset_save.setStyleSheet(_BTN)
        self._preset_save.setToolTip("Save the current rows as a new named preset.")
        self._preset_save.clicked.connect(self._save_policy_preset)
        _preset_row.addWidget(self._preset_save)
        self._preset_del = QPushButton("Delete")
        self._preset_del.setStyleSheet(_BTN)
        self._preset_del.setToolTip("Delete the selected custom preset (built-ins can't be removed).")
        self._preset_del.clicked.connect(self._delete_policy_preset)
        _preset_row.addWidget(self._preset_del)
        pol_lay.addLayout(_preset_row)

        # ── Per-category rules, grouped so the finer list stays readable ─────
        self._policy_combos = {}
        for _grp_title, _grp_cats in _guard.CATEGORY_GROUPS:
            _sub = QLabel(_grp_title)
            _sub.setStyleSheet(f"color:{_MUTED}; font-size:10px; font-weight:700;"
                               "letter-spacing:1px; margin-top:6px;")
            pol_lay.addWidget(_sub)
            for _cat in _grp_cats:
                _row = QHBoxLayout()
                _lbl = QLabel(_guard.CATEGORY_LABELS.get(_cat, _cat))
                _lbl.setStyleSheet(f"color:{_TEXT}; font-size:11px;")
                _lbl.setWordWrap(True)
                _row.addWidget(_lbl, 1)
                _combo = QComboBox()
                _combo.addItems(["ask", "allow", "deny"])
                _combo.setStyleSheet(_COMBO)
                _combo.setMaximumWidth(120)
                _row.addWidget(_combo)
                self._policy_combos[_cat] = _combo
                pol_lay.addLayout(_row)
        self._refresh_preset_combo()

        self.review_safe_code_checkbox = QCheckBox(
            "Review even safe code (prompt for everything, incl. plain print)")
        self.review_safe_code_checkbox.setChecked(False)
        self.review_safe_code_checkbox.setStyleSheet(_CHECK)
        self.review_safe_code_checkbox.setToolTip(
            "Sub-option of Supervised Execution — only has effect while Supervised "
            "is ON.\nBy default, code with no risky operations (plain print, math, a "
            "bare import) runs without a prompt; only caution/danger operations are "
            "reviewed.\nTick this to be prompted for EVERY code execution, no matter "
            "how trivial.")
        pol_lay.addWidget(self.review_safe_code_checkbox)

        _forget_btn = QPushButton("Clear this session's allow-list")
        _forget_btn.setStyleSheet(_BTN)
        _forget_btn.setToolTip(
            "Undo any \"Don't ask again this session\" choices made in the code "
            "approval dialog. Does not touch your saved policy.")
        _forget_btn.clicked.connect(self._forget_approvals)
        pol_lay.addWidget(_forget_btn)

        _audit_hdr = QLabel("Recent execution audit")
        _audit_hdr.setStyleSheet(f"color:{_TEXT}; font-size:11px; font-weight:600;")
        pol_lay.addWidget(_audit_hdr)
        self.audit_view = QTextEdit()
        self.audit_view.setReadOnly(True)
        self.audit_view.setMaximumHeight(150)
        self.audit_view.setStyleSheet(
            f"QTextEdit {{ background:{_ELEV}; border:1px solid {_BORDER}; border-radius:6px;"
            f" padding:8px; font-family:Consolas,monospace; font-size:10px; color:{_TEXT}; }}")
        pol_lay.addWidget(self.audit_view)
        _audit_row = QHBoxLayout()
        _audit_refresh = QPushButton("Refresh")
        _audit_refresh.setStyleSheet(_BTN)
        _audit_refresh.clicked.connect(self._refresh_audit_view)
        _audit_clear = QPushButton("Clear log")
        _audit_clear.setStyleSheet(_BTN)
        _audit_clear.clicked.connect(self._clear_audit_log)
        _audit_row.addWidget(_audit_refresh)
        _audit_row.addWidget(_audit_clear)
        _audit_row.addStretch()
        pol_lay.addLayout(_audit_row)
        sec_lay.addWidget(pol_group)

        # ── Voice mode approval ─────────────────────────────────────────────
        va_group = QGroupBox("Voice Mode Approval")
        va_group.setStyleSheet(_GROUP)
        va_lay = QVBoxLayout(va_group)
        self.voice_approval_checkbox = QCheckBox(
            "Answer code approval prompts by voice")
        self.voice_approval_checkbox.setStyleSheet(_CHECK)
        va_lay.addWidget(self.voice_approval_checkbox)
        va_lay.addWidget(_info_box(
            "While voice mode is on and an approval window is open, spoken command "
            "words act on it: deny words reject instantly; approve words must be the "
            "whole utterance. Approving code with danger findings asks you to say "
            "\"confirm\" first."))
        # Sub-options wrapped so they can be HIDDEN while voice approval is off.
        self.va_opts_widget = QWidget()
        vao_lay = QVBoxLayout(self.va_opts_widget)
        vao_lay.setContentsMargins(0, 0, 0, 0)
        self.voice_approval_mode_combo = QComboBox()
        self.voice_approval_mode_combo.addItem(
            "Basic - command words only", "basic")
        self.voice_approval_mode_combo.addItem(
            "Advanced - other speech goes to the Code Reviewer", "advanced")
        self.voice_approval_mode_combo.setStyleSheet(_COMBO)
        vao_lay.addWidget(_label("Mode:"))
        vao_lay.addWidget(self.voice_approval_mode_combo)
        self.voice_approval_confirm_checkbox = QCheckBox(
            "Require a spoken \"confirm\" before executing code with danger findings")
        self.voice_approval_confirm_checkbox.setStyleSheet(_CHECK)
        vao_lay.addWidget(self.voice_approval_confirm_checkbox)
        self.voice_approval_announce_checkbox = QCheckBox(
            "Announce the approval window by voice when it opens")
        self.voice_approval_announce_checkbox.setStyleSheet(_CHECK)
        vao_lay.addWidget(self.voice_approval_announce_checkbox)
        va_lay.addWidget(self.va_opts_widget)
        self.approval_mini_checkbox = QCheckBox(
            "Compact approval notification when the chat window is closed")
        self.approval_mini_checkbox.setStyleSheet(_CHECK)
        va_lay.addWidget(self.approval_mini_checkbox)
        va_lay.addWidget(_info_box(
            "When the chat window is closed or minimized, approvals appear as a small "
            "corner card showing the risk findings (not the code) with Expand / Deny / "
            "Approve. Code with danger findings always requires expanding to review. "
            "Opening the chat window expands to the full approval automatically."))

        # Custom-word editor wrapped for the same hide gating.
        self.va_words_widget = QWidget()
        vaw_lay = QVBoxLayout(self.va_words_widget)
        vaw_lay.setContentsMargins(0, 0, 0, 0)
        vaw_lay.addWidget(_label("Custom command words:"))
        from PyQt6.QtWidgets import QListWidget
        self.voice_approval_words_list = QListWidget()
        self.voice_approval_words_list.setStyleSheet(
            f"QListWidget {{ background:{_ELEV}; border:1px solid {_BORDER};"
            f" border-radius:6px; padding:4px; font-family:Consolas,monospace;"
            f" font-size:10px; color:{_TEXT}; outline:none; }}"
            f"QListWidget::item {{ padding:2px 4px; }}")
        self.voice_approval_words_list.setMaximumHeight(96)
        vaw_lay.addWidget(self.voice_approval_words_list)
        _va_row = QHBoxLayout()
        self.voice_approval_word_input = QLineEdit()
        self.voice_approval_word_input.setPlaceholderText("Word or phrase")
        self.voice_approval_word_input.setStyleSheet(_INPUT)
        _va_row.addWidget(self.voice_approval_word_input, stretch=1)
        self.voice_approval_word_action = QComboBox()
        self.voice_approval_word_action.addItem("Approve", "approve")
        self.voice_approval_word_action.addItem("Deny", "deny")
        self.voice_approval_word_action.setStyleSheet(_COMBO)
        _va_row.addWidget(self.voice_approval_word_action)
        _va_add = QPushButton("Add")
        _va_add.setStyleSheet(_BTN)
        _va_add.clicked.connect(self._add_voice_approval_word)
        _va_row.addWidget(_va_add)
        _va_remove = QPushButton("Remove selected")
        _va_remove.setStyleSheet(_BTN)
        _va_remove.clicked.connect(self._remove_voice_approval_word)
        _va_row.addWidget(_va_remove)
        vaw_lay.addLayout(_va_row)
        va_lay.addWidget(self.va_words_widget)
        self.voice_approval_checkbox.toggled.connect(
            self._update_voice_approval_enabled)
        # 'Expand' is only meaningful while the compact card can appear.
        self.approval_mini_checkbox.toggled.connect(
            self._update_voice_approval_expand_action)
        sec_lay.addWidget(va_group)

        dbg_group = QGroupBox("Debug")
        dbg_group.setStyleSheet(_GROUP)
        dg_lay = QVBoxLayout(dbg_group)
        self.debug_checkbox = QCheckBox("Enable Debug Mode")
        self.debug_checkbox.setStyleSheet(_CHECK)
        dg_lay.addWidget(self.debug_checkbox)
        dg_lay.addWidget(_info_box(
            "Shows tool usage conversations in a separate window so you can see "
            "what the AI is doing, its inputs, outputs, and decision-making process."))
        sec_lay.addWidget(dbg_group)
        sec_lay.addStretch()
        tabs.addTab(sec_scroll, "Security")

        # ════════════════════════════════════════════════════════════════════
        # TAB 6 — System
        # ════════════════════════════════════════════════════════════════════
        sys_scroll, sys_lay = _make_scroll_tab()

        tl_group = QGroupBox("Tool Execution Locking")
        tl_group.setStyleSheet(_GROUP)
        tl_lay = QVBoxLayout(tl_group)
        self.tool_exec_lockout_checkbox = QCheckBox("Enable Tool Execution Lockout")
        self.tool_exec_lockout_checkbox.setStyleSheet(_CHECK)
        tl_lay.addWidget(self.tool_exec_lockout_checkbox)
        tl_lay.addWidget(_info_box(
            "When enabled, the agent will no longer be able to do anything but generate chat responses."))
        sys_lay.addWidget(tl_group)

        sp_group = QGroupBox("System Prompt Hijacking")
        sp_group.setStyleSheet(_GROUP)
        sp_lay = QVBoxLayout(sp_group)
        self.system_prompt_hijack_checkbox = QCheckBox("Enable System Prompt Hijack")
        self.system_prompt_hijack_checkbox.setStyleSheet(_CHECK)
        sp_lay.addWidget(self.system_prompt_hijack_checkbox)
        sp_lay.addWidget(_info_box(
            "Replace the system prompt with the one below."))
        self.system_prompt_hijack_input = QTextEdit()
        self.system_prompt_hijack_input.setPlaceholderText("Enter your custom system prompt here...")
        self.system_prompt_hijack_input.setFixedHeight(120)
        self.system_prompt_hijack_input.setStyleSheet(_INPUT)
        sp_lay.addWidget(self.system_prompt_hijack_input)
        # The prompt editor is useless while the hijack is off — hide it.
        self.system_prompt_hijack_checkbox.toggled.connect(
            self.system_prompt_hijack_input.setVisible)
        self.system_prompt_hijack_input.setVisible(False)
        sys_lay.addWidget(sp_group)

        sp_extras_group = QGroupBox("Optional System Prompt Sections (Main AI Engine)")
        sp_extras_group.setStyleSheet(_GROUP)
        sp_extras_lay = QVBoxLayout(sp_extras_group)
        self.include_image_tools_checkbox = QCheckBox("Inject Image Tools into system prompt")
        self.include_image_tools_checkbox.setStyleSheet(_CHECK)
        self.include_image_tools_checkbox.setToolTip(
            "Adds image tool instructions to the main AI engine's system prompt.\n"
            "Does NOT affect task sessions — those have their own toggle in the task editor."
        )
        self.include_controller_ref_checkbox = QCheckBox("Inject Controller Reference into system prompt")
        self.include_controller_ref_checkbox.setStyleSheet(_CHECK)
        self.include_controller_ref_checkbox.setToolTip(
            "Adds controller usage reference into the main AI engine's system prompt."
        )
        self.include_notify_tool_checkbox = QCheckBox("Inject Notify Tool into system prompt")
        self.include_notify_tool_checkbox.setStyleSheet(_CHECK)
        self.include_notify_tool_checkbox.setToolTip(
            "Adds notify tool instructions into the main AI engine's system prompt."
        )
        sp_extras_lay.addWidget(self.include_image_tools_checkbox)
        sp_extras_lay.addWidget(self.include_controller_ref_checkbox)
        sp_extras_lay.addWidget(self.include_notify_tool_checkbox)
        sys_lay.addWidget(sp_extras_group)

        # ── File tools & history (read_file / edit_file / write_file) ────────
        fh_group = QGroupBox("File Tools — history and undo")
        fh_group.setStyleSheet(_GROUP)
        fh_lay = QVBoxLayout(fh_group)
        fh_lay.addWidget(_info_box(
            "The AI's surgical file tools (read_file / edit_file / write_file) are a "
            "core part of work mode and are always available. Every edit or write "
            "records the file's previous state here so you can undo it."))
        self.file_history_checkbox = QCheckBox("Keep file history (undo journal)")
        self.file_history_checkbox.setStyleSheet(_CHECK)
        self.file_history_checkbox.setToolTip(
            "Before every tool edit/write, save the file's previous content to "
            "data/cache/file_history so the change can be reverted.")
        fh_lay.addWidget(self.file_history_checkbox)
        self.file_history_git_checkbox = QCheckBox("Deep history via git (when installed)")
        self.file_history_git_checkbox.setStyleSheet(_CHECK)
        self.file_history_git_checkbox.setToolTip(
            "Additionally commit every change to a local shadow git repository in "
            "data/cache/file_history/shadow — full change history beyond the last undo point.")
        fh_lay.addWidget(self.file_history_git_checkbox)
        prune_row = QHBoxLayout()
        # Prune label+combo wrapped so they HIDE while history is off; the
        # "View file changes…" button stays — browsing past history is always valid.
        self._fh_prune_widget = QWidget()
        _fhp_lay = QHBoxLayout(self._fh_prune_widget)
        _fhp_lay.setContentsMargins(0, 0, 0, 0)
        _fhp_lay.addWidget(_label("Prune undo history after"))
        self.file_history_days_combo = QComboBox()
        for d in (7, 14, 30, 90):
            self.file_history_days_combo.addItem(f"{d} days", d)
        self.file_history_days_combo.setStyleSheet(_COMBO)
        _fhp_lay.addWidget(self.file_history_days_combo)
        prune_row.addWidget(self._fh_prune_widget)
        prune_row.addStretch()
        self.view_file_history_btn = QPushButton("View file changes…")
        self.view_file_history_btn.setStyleSheet(_BTN)
        self.view_file_history_btn.clicked.connect(self._open_file_history)
        prune_row.addWidget(self.view_file_history_btn)
        fh_lay.addLayout(prune_row)

        def _update_fh_enabled():
            on = self.file_history_checkbox.isChecked()
            self.file_history_git_checkbox.setVisible(on)
            self._fh_prune_widget.setVisible(on)
        self.file_history_checkbox.stateChanged.connect(lambda _s: _update_fh_enabled())
        self._update_fh_enabled = _update_fh_enabled
        sys_lay.addWidget(fh_group)

        # ── Tool Calling Mode ───────────────────────────────────────────────
        tc_group = QGroupBox("Tool Calling Mode")
        tc_group.setStyleSheet(_GROUP)
        tc_lay = QVBoxLayout(tc_group)
        self.tool_calling_mode_combo = QComboBox()
        self.tool_calling_mode_combo.addItem(
            "Compatibility — fenced tool format in the prompt (works with ANY model)", 'compat')
        self.tool_calling_mode_combo.addItem(
            "Native — provider function calling (lighter prompt; needs a supporting provider)", 'native')
        self.tool_calling_mode_combo.setStyleSheet(_COMBO)
        tc_lay.addWidget(self.tool_calling_mode_combo)
        tc_lay.addWidget(_info_box(
            "Compatibility teaches tools via the system prompt and parses fenced replies — universal, "
            "but costs prompt tokens and can be mis-formatted by weak models.\n"
            "Native passes tools through the provider's function-calling API: smaller prompt and "
            "guaranteed-valid calls — but only works on providers that declare native support. "
            "Providers without it automatically fall back to Compatibility."))
        sys_lay.addWidget(tc_group)

        # ── Work Mode ────────────────────────────────────────────────────────
        # The work-continuation ping is re-sent on EVERY step of a work loop, so
        # its length is paid per step, not once. That is why the length is worth
        # a setting of its own.
        wm_group = QGroupBox("Work Mode")
        wm_group.setStyleSheet(_GROUP)
        wm_lay = QVBoxLayout(wm_group)
        self.work_mode_prompt_style_combo = QComboBox()
        self.work_mode_prompt_style_combo.addItem(
            "Detailed continuation prompt — full guidance every step (recommended)",
            'detailed')
        self.work_mode_prompt_style_combo.addItem(
            "Compact continuation prompt — shredded to the essentials",
            'compact')
        self.work_mode_prompt_style_combo.setStyleSheet(_COMBO)
        wm_lay.addWidget(self.work_mode_prompt_style_combo)
        wm_lay.addWidget(_info_box(
            "What the assistant is told between steps of a multi-step task.\n"
            "Detailed sends the full block: the decision checklist, the list of "
            "tools it can chain, and the anti-patterns. Most reliable, and the "
            "default.\n"
            "Compact keeps only the 'this message is internal' warning and the "
            "directory-safety rules, dropping the checklist and the tool list "
            "(both of which the system prompt already covers). Saves roughly "
            "375 tokens on every step of every task — but a weaker model may "
            "chain less thoroughly without the checklist."))
        sys_lay.addWidget(wm_group)

        # ── Code Execution (moved here from General) ─────────────────────────
        exec_group = QGroupBox("Code Execution")
        exec_group.setStyleSheet(_GROUP)
        ex_lay = QVBoxLayout(exec_group)

        _timeout_row = QHBoxLayout()
        _timeout_row.addWidget(_label("Tool execution timeout (seconds):"))
        self.exec_timeout_spin = QSpinBox()
        self.exec_timeout_spin.setRange(10, 3600)
        self.exec_timeout_spin.setValue(300)
        self.exec_timeout_spin.setSingleStep(10)
        self.exec_timeout_spin.setSuffix(" s")
        self.exec_timeout_spin.setFixedWidth(120)
        self.exec_timeout_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QSpinBox:focus {{ border-color: {_ACCENT}; }}
        """)
        _timeout_row.addWidget(self.exec_timeout_spin)
        _timeout_row.addStretch()
        ex_lay.addLayout(_timeout_row)
        ex_lay.addWidget(_info_box(
            "When code execution runs longer than this, you'll be asked "
            "whether to extend the timeout or kill the operation."))
        sys_lay.addWidget(exec_group)

        # ── AI Response timeout (E3) ─────────────────────────────────────────
        resp_group = QGroupBox("AI Response")
        resp_group.setStyleSheet(_GROUP)
        rp_lay = QVBoxLayout(resp_group)
        _resp_row = QHBoxLayout()
        _resp_row.addWidget(_label("Response wait timeout (seconds):"))
        self.response_timeout_spin = QSpinBox()
        self.response_timeout_spin.setRange(0, 3600)
        self.response_timeout_spin.setValue(0)
        self.response_timeout_spin.setSingleStep(10)
        self.response_timeout_spin.setSuffix(" s")
        self.response_timeout_spin.setSpecialValueText("Unlimited")
        self.response_timeout_spin.setFixedWidth(120)
        self.response_timeout_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QSpinBox:focus {{ border-color: {_ACCENT}; }}
        """)
        _resp_row.addWidget(self.response_timeout_spin)
        _resp_row.addStretch()
        rp_lay.addLayout(_resp_row)
        rp_lay.addWidget(_info_box(
            "How long to wait for the AI provider to respond before giving up. "
            "Set to Unlimited (0) to wait forever.\n"
            "While streaming, this bounds the wait for the FIRST piece of the "
            "reply — once text starts arriving, a long answer is never cut off."))

        # Streaming — hidden entirely when the selected provider script can't
        # stream (dependent-visibility rule), replaced by a note saying so.
        self._streaming_widget = QWidget()
        _stream_lay = QVBoxLayout(self._streaming_widget)
        _stream_lay.setContentsMargins(0, 0, 0, 0)
        _stream_lay.setSpacing(6)
        self.streaming_checkbox = QCheckBox("Stream replies as they are generated")
        self.streaming_checkbox.setStyleSheet(_CHECK)
        self.streaming_checkbox.setChecked(True)
        _stream_lay.addWidget(self.streaming_checkbox)
        _stream_lay.addWidget(_info_box(
            "Show the reply word-by-word as the model writes it, instead of "
            "waiting for the whole response."))
        rp_lay.addWidget(self._streaming_widget)

        self._streaming_unsupported_note = _info_box(
            "This provider script does not support streaming, so replies "
            "arrive all at once. Scripts using the current provider contract "
            "(see providers/large-language-models/_template.py) can stream.")
        rp_lay.addWidget(self._streaming_unsupported_note)
        sys_lay.addWidget(resp_group)

        # ── Android Bridge / Packet ─────────────────────────────────────────
        packet_group = QGroupBox("Android Packet")
        packet_group.setStyleSheet(_GROUP)
        pk_lay = QVBoxLayout(packet_group)
        _port_row = QHBoxLayout()
        _port_row.addWidget(_label("Packet port (Android Bridge):"))
        self.packet_port_spin = QSpinBox()
        self.packet_port_spin.setRange(1024, 65535)
        self.packet_port_spin.setValue(1111)
        self.packet_port_spin.setSingleStep(1)
        self.packet_port_spin.setFixedWidth(120)
        self.packet_port_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {_ELEV};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 11px;
                color: {_TEXT};
            }}
            QSpinBox:focus {{ border-color: {_ACCENT}; }}
        """)
        _port_row.addWidget(self.packet_port_spin)
        _port_row.addStretch()
        pk_lay.addLayout(_port_row)
        pk_lay.addWidget(_info_box(
            "TCP port the Android app connects to over Wi-Fi LAN (default 1111).\n"
            "Enter this as IP:port in the phone app. A change takes effect the next "
            "time you open the packet (close & reopen it from the floating icon)."))
        sys_lay.addWidget(packet_group)

        # ── Software Updates (self-update via gitplucker) ────────────────────
        upd_group = QGroupBox("Software Updates")
        upd_group.setStyleSheet(_GROUP)
        upd_lay = QVBoxLayout(upd_group)
        upd_lay.addWidget(_info_box(
            "Check GitHub for a newer version of Systema Auxilium, review the exact changes, "
            "and choose which files to update. Your local edits to tracked files are merged "
            "where possible (conflicts are marked), new Python dependencies are auto-installed, "
            "and a backup is saved to data/updates/ before anything is written. Your settings "
            "and data/ are never touched.\n"
            "Source: github.com/uukjtisa/systema-auxilium"))
        _upd_btn = QPushButton("Check for Updates")
        _upd_btn.setStyleSheet(_BTN)
        _upd_btn.clicked.connect(lambda: self.controller.open_update_window(parent=self))
        upd_lay.addWidget(_upd_btn)

        # Auto-updater preferences (persisted; gate the startup probe + notice).
        _svc = getattr(self.controller, "updater_service", None)
        self.update_autocheck_checkbox = QCheckBox("Automatically check for updates on startup")
        self.update_autocheck_checkbox.setStyleSheet(_CHECK)
        self.update_notify_checkbox = QCheckBox("Notify me when an update is available")
        self.update_notify_checkbox.setStyleSheet(_CHECK)
        self.update_autodeps_checkbox = QCheckBox("Auto-install new Python dependencies when applying")
        self.update_autodeps_checkbox.setStyleSheet(_CHECK)
        if _svc is not None:
            self.update_autocheck_checkbox.setChecked(_svc.auto_check_enabled)
            self.update_notify_checkbox.setChecked(_svc.notify_enabled)
            self.update_autodeps_checkbox.setChecked(_svc.auto_install_deps)
            self.update_autocheck_checkbox.toggled.connect(_svc.set_auto_check_enabled)
            self.update_notify_checkbox.toggled.connect(_svc.set_notify_enabled)
            self.update_autodeps_checkbox.toggled.connect(_svc.set_auto_install_deps)
            # Notifications are meaningless without the startup check — keep them linked.
            self.update_notify_checkbox.setEnabled(_svc.auto_check_enabled)
            self.update_autocheck_checkbox.toggled.connect(
                self.update_notify_checkbox.setEnabled)
        else:
            for _cb in (self.update_autocheck_checkbox, self.update_notify_checkbox,
                        self.update_autodeps_checkbox):
                _cb.setEnabled(False)
        upd_lay.addWidget(self.update_autocheck_checkbox)
        upd_lay.addWidget(self.update_notify_checkbox)
        upd_lay.addWidget(self.update_autodeps_checkbox)
        upd_lay.addWidget(_info_box(
            "Startup checks are silent and skipped automatically in a developer working copy. "
            "Turn off auto-check to only look for updates when you click the button above."))
        sys_lay.addWidget(upd_group)

        sys_lay.addStretch()
        tabs.addTab(sys_scroll, "System")

        main_layout.addWidget(tabs, stretch=1)

        # ── Footer (always visible, pinned) ──────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(58)
        footer.setStyleSheet(f"""
            QFrame {{
                background-color: {_BASE};
                border-top: 1px solid {_BORDER};
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }}
        """)
        footer_lay = QHBoxLayout(footer)
        footer_lay.setContentsMargins(16, 0, 16, 0)
        footer_lay.setSpacing(10)

        self.footer_status_label = QLabel("")
        self.footer_status_label.setStyleSheet(f"color:{_MUTED}; font-size:11px; background:transparent;")
        footer_lay.addWidget(self.footer_status_label, stretch=1)

        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.setMinimumWidth(140)
        save_btn.clicked.connect(self.save_settings)
        footer_lay.addWidget(save_btn)

        main_layout.addWidget(footer)

    def _on_bubble_style_selected(self, checked, key, card):
        """Handle bubble-style radio toggle — update card borders."""
        if not checked:
            return
        self._selected_bubble_style = key
        _, _, _ELEV, _BORDER, _ACCENT, _, _ = self._palette()
        for k, c in getattr(self, '_bubble_style_cards', {}).items():
            try:
                c.setStyleSheet(f"""
                    QFrame {{
                        background: {_ELEV};
                        border: 2px solid {_ACCENT if k == key else _BORDER};
                        border-radius: 10px;
                    }}
                    QFrame:hover {{
                        border-color: {_ACCENT};
                    }}
                """)
            except RuntimeError:
                pass

    def _on_theme_selected(self, checked, key, card):
        """Handle theme radio button toggle — update card borders."""
        if not checked:
            return
        self._selected_theme = key
        # Update all card borders
        _, _, _ELEV, _BORDER, _ACCENT, _, _ = self._palette()
        for k, c in self._theme_cards.items():
            try:
                c.setStyleSheet(f"""
                    QFrame {{
                        background: {_ELEV};
                        border: 2px solid {"" + _ACCENT + "" if k == key else _BORDER};
                        border-radius: 10px;
                    }}
                    QFrame:hover {{ border-color: {_ACCENT}; }}
                """)
            except RuntimeError:
                pass

    def on_vad_settings_changed(self):
        """Update VAD settings visibility based on toggles"""
        webrtc_enabled = self.webrtc_vad_checkbox.isChecked()
        silero_enabled = self.silero_vad_checkbox.isChecked()

        # Show/hide settings based on toggles
        if webrtc_enabled:
            self.webrtc_settings_widget.show()
        else:
            self.webrtc_settings_widget.hide()

        if silero_enabled:
            self.silero_settings_widget.show()
        else:
            self.silero_settings_widget.hide()

    def log_status(self, message):
        """Helper to log status messages"""
        print(f"[Settings] {message}")

    def _open_memory_window(self):
        """Open the memory manager window."""
        if not hasattr(self, '_memory_window') or not self._memory_window.isVisible():
            from systema.ui.windows.memory_window import MemoryWindow
            self._memory_window = MemoryWindow(self.controller)
        self._memory_window.show()
        self._memory_window.raise_()
        self._memory_window.refresh_memories()

    def _on_llm_provider_changed(self, index):
        """Show the Manual Provider Helper only when Manual Response is selected,
        and rebuild the per-provider Display settings form."""
        is_manual = self.provider_script_combo.currentData() == ""
        self.manual_group.setVisible(is_manual)
        self._rebuild_provider_display_form()
        # Streaming support is a property of the selected script.
        if hasattr(self, '_streaming_widget'):
            self._update_streaming_visibility()

    def _active_provider_streams(self) -> bool:
        """True when the selected provider script can stream (contract v2).
        Legacy scripts and the Manual provider cannot."""
        from systema.engine import provider_contract as pc
        path = self.provider_script_combo.currentData()
        if not path:
            return False                      # Manual Response
        cached = getattr(self, '_stream_support_cache', {})
        if path in cached:
            return cached[path]
        ok = pc.is_v2(pc.load_module(path))
        cached[path] = ok
        self._stream_support_cache = cached
        return ok

    def _update_streaming_visibility(self):
        """Dependent visibility for streaming:
          - the streaming toggle hides (note instead) when the provider can't
            stream;
          - the typing-reveal block hides while streaming is actually live,
            because the stream itself IS the reveal;
          - if streaming is ON but the provider can't stream, the typing block
            STAYS and says why (that combination is exactly when a user would
            otherwise wonder which animation they are getting).
        Hidden, never greyed out (house rule)."""
        try:
            supported = self._active_provider_streams()
            enabled = self.streaming_checkbox.isChecked()
            self._streaming_widget.setVisible(supported)
            self._streaming_unsupported_note.setVisible(not supported)
            streaming_live = supported and enabled
            self._typing_reveal_widget.setVisible(not streaming_live)
            self._typing_streaming_note.setVisible(streaming_live)
            self._typing_fallback_note.setVisible(enabled and not supported)
        except Exception:
            log.warning("[settings] streaming visibility sync failed", exc_info=True)

    def _rebuild_provider_display_form(self):
        """(Re)build the dynamic settings form from the selected script's
        Display dict. Unsaved edits are cached per script so switching the
        combo back keeps them; values persist only on Save."""
        from systema.engine import provider_contract as pc
        cont = getattr(self, '_prov_display_container', None)
        if cont is None:
            return
        self._stash_provider_display_cache()
        lay = cont.layout()
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._prov_display_widgets = {}
        self._prov_display_script_key = None

        path = self.provider_script_combo.currentData()
        if not path:
            cont.setVisible(False)
            return
        module = pc.load_module(path)
        display = pc.validate_display(module) if module else {}
        if not display:
            cont.setVisible(False)
            return

        import os as _os
        key = _os.path.basename(path)
        self._prov_display_script_key = key
        saved = (self.controller.settings.get('provider_display_values') or {}).get(key, {})
        cached = self._prov_display_cache.get(key, {})

        from PyQt6.QtWidgets import QLabel
        st = self._prov_form_styles
        header = QLabel("Provider Settings")
        header.setStyleSheet(f"color:{st['muted']}; font-size:10px; font-weight:600; "
                             f"border-top:1px solid {st['border']}; padding-top:8px;")
        lay.addWidget(header)
        for var, (label, ftype, extra, dopts) in display.items():
            current = cached.get(var, saved.get(var, getattr(module, var, '')))
            lay.addWidget(self._build_display_row(var, label, ftype, extra, dopts, current))
        cont.setVisible(True)

    def _build_display_row(self, var, label, ftype, extra, dopts, current):
        """One form row for a Display entry. Registers the editor widget in
        self._prov_display_widgets for collection on Save. `dopts` is the
        optional per-entry dict: tooltip / placeholder / item_tooltips."""
        from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLineEdit,
                                     QPlainTextEdit, QComboBox, QCheckBox,
                                     QPushButton, QLabel, QFileDialog)
        from PyQt6.QtCore import Qt as _Qt
        from systema.engine import provider_contract as pc
        st = self._prov_form_styles
        dopts = dopts or {}
        tooltip = dopts.get('tooltip') or ''
        placeholder = dopts.get('placeholder') or ''

        # info_box: read-only note — no input, nothing registered/persisted.
        if ftype == 'info_box':
            note = QLabel(label)
            note.setWordWrap(True)
            note.setStyleSheet(
                f"color:{st['muted']}; font-size:10px; background:transparent; "
                f"border:1px solid {st['border']}; border-radius:6px; padding:8px;")
            if tooltip:
                note.setToolTip(tooltip)
            return note

        row = QWidget()
        lay = QHBoxLayout(row) if ftype != 'textarea' else QVBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{st['text']}; font-size:11px;")
        if ftype != 'textarea':
            lbl.setFixedWidth(120)
        if tooltip:
            lbl.setToolTip(tooltip)
        lay.addWidget(lbl)

        if ftype == 'checkbox':
            w = QCheckBox()
            w.setStyleSheet(st['check'])
            w.setChecked(bool(current))
            lay.addWidget(w, stretch=1)
        elif ftype == 'list_dropdown':
            from systema.ui.widgets.painted_icons import ChevronCombo
            # A REAL dropdown (never editable — clicking must open the list,
            # not drop a text cursor). Values outside the presets go through
            # the house "Custom…" pattern: a hidden input revealed only when
            # Custom… is picked (see the memory embed-model rows).
            w = ChevronCombo()
            w.setStyleSheet(st.get('combo_edit') or st['combo'])
            opts = [str(o) for o in (extra or [])]
            for opt in opts:
                w.addItem(opt, opt)
            w.addItem("Custom…", _CUSTOM_SENTINEL)
            item_tips = dopts.get('item_tooltips') or []
            for i, tip in enumerate(item_tips[:len(opts)]):
                if tip:
                    w.setItemData(i, str(tip), _Qt.ItemDataRole.ToolTipRole)
            w.setItemData(w.count() - 1,
                          "Type any value the provider accepts",
                          _Qt.ItemDataRole.ToolTipRole)
            lay.addWidget(w, stretch=1)

            custom = QLineEdit()
            custom.setStyleSheet(st['input'])
            custom.setPlaceholderText(placeholder or "Type a custom value")
            lay.addWidget(custom, stretch=1)

            def _sync_custom(_=None, _w=w, _c=custom):
                is_custom = _w.currentData() == _CUSTOM_SENTINEL
                _c.setVisible(is_custom)

            cur = '' if current is None else str(current)
            if cur and cur in opts:
                w.setCurrentIndex(opts.index(cur))
            elif cur:
                w.setCurrentIndex(w.count() - 1)      # Custom…
                custom.setText(cur)
            w.currentIndexChanged.connect(_sync_custom)
            _sync_custom()
            self._prov_display_widgets[var] = (ftype, w, custom)
            return row
        elif ftype == 'textarea':
            w = QPlainTextEdit()
            w.setStyleSheet(st['input'].replace('QLineEdit, QTextEdit', 'QPlainTextEdit'))
            w.setFixedHeight(72)
            w.setPlainText('' if current is None else str(current))
            if placeholder:
                w.setPlaceholderText(placeholder)
            lay.addWidget(w)
        else:  # input / secure_input / number / file_path
            w = QLineEdit()
            w.setStyleSheet(st['input'])
            w.setText('' if current is None else str(current))
            if placeholder:
                w.setPlaceholderText(placeholder)
            lay.addWidget(w, stretch=1)
            if pc.is_secret_type(ftype):
                from systema.ui.widgets.painted_icons import EyeButton
                # Masking is opt-in per field (`secure_input`), never guessed
                # from the variable name. Reveal is a painted eye — a text
                # "Show"/"Hide" button clipped its letters in this narrow row.
                w.setEchoMode(QLineEdit.EchoMode.Password)
                reveal = EyeButton()

                def _toggle_reveal(on, _w=w):
                    _w.setEchoMode(QLineEdit.EchoMode.Normal if on
                                   else QLineEdit.EchoMode.Password)

                reveal.toggled.connect(_toggle_reveal)
                lay.addWidget(reveal)
            elif ftype == 'file_path':
                browse = QPushButton("Browse")
                browse.setFixedWidth(70)
                browse.setStyleSheet(st['btn'])

                def _browse(_w=w):
                    fn, _ = QFileDialog.getOpenFileName(self, f"Select {label}", _w.text() or "")
                    if fn:
                        _w.setText(fn)

                browse.clicked.connect(_browse)
                lay.addWidget(browse)

        if tooltip:
            w.setToolTip(tooltip)
        self._prov_display_widgets[var] = (ftype, w, extra)
        return row

    def _collect_provider_display_values(self) -> dict:
        """Read the current Display form into a JSON-ready dict."""
        vals = {}
        for var, (ftype, w, extra) in self._prov_display_widgets.items():
            try:
                if ftype in ('input', 'secure_input', 'file_path'):
                    vals[var] = w.text()
                elif ftype == 'textarea':
                    vals[var] = w.toPlainText()
                elif ftype == 'checkbox':
                    vals[var] = w.isChecked()
                elif ftype == 'number':
                    t = w.text().strip()
                    if not t:
                        continue
                    try:
                        vals[var] = int(t)
                    except ValueError:
                        vals[var] = float(t)
                elif ftype == 'list_dropdown':
                    # `extra` holds the revealed Custom… input for this row.
                    if w.currentData() == _CUSTOM_SENTINEL:
                        txt = extra.text().strip() if extra is not None else ''
                        if not txt:
                            continue          # Custom… picked but left blank
                        vals[var] = txt
                    else:
                        vals[var] = w.currentData()
            except Exception:
                continue
        return vals

    def _stash_provider_display_cache(self):
        """Snapshot the live form into the per-script cache (pre-rebuild/save)."""
        key = getattr(self, '_prov_display_script_key', None)
        if key and getattr(self, '_prov_display_widgets', None):
            try:
                self._prov_display_cache[key] = self._collect_provider_display_values()
            except Exception:
                pass

    def on_tts_provider_changed(self, index):
        """Show Edge TTS voice dropdown only when Edge TTS is selected; the
        ElevenLabs speech-tag toggle only matters for custom TTS scripts."""
        provider_data = self.tts_provider_combo.currentData()
        if provider_data == 'edge-tts':
            self.edge_tts_group.show()
        else:
            self.edge_tts_group.hide()
        self.elevenlabs_tags_checkbox.setVisible(provider_data != 'edge-tts')

    def _refresh_prefilling_sessions(self):
        """Populate the prefilling session combo with all saved sessions.
        The currently active session is excluded — if it's selected as the prefill
        source, the engine falls back to the premade PREFILLING automatically."""
        self.pf_session_combo.clear()
        try:
            active_id = getattr(self.controller, 'current_session_id', None)
            sessions = self.controller.get_session_list()
            for s in sessions:
                if s.get('id') == active_id:
                    continue
                label = s.get('name') or s.get('id', '?')
                self.pf_session_combo.addItem(label, s.get('id', ''))
        except Exception as e:
            self.pf_session_combo.addItem(f"(error: {e})", "")

    def _refresh_llm_provider_scripts(self):
        """Populate the LLM provider script combo from providers/large-language-models/."""
        scripts = self.controller.get_llm_provider_scripts()
        current = self.provider_script_combo.currentData() if self.provider_script_combo.count() else ''
        self.provider_script_combo.blockSignals(True)
        self.provider_script_combo.clear()
        self.provider_script_combo.addItem("Manual Response (Debug)", "")
        for s in scripts:
            self.provider_script_combo.addItem(s['name'], s['path'])
        for i in range(self.provider_script_combo.count()):
            if self.provider_script_combo.itemData(i) == current:
                self.provider_script_combo.setCurrentIndex(i)
                break
        self.provider_script_combo.blockSignals(False)

    def _refresh_tts_provider_scripts(self):
        """Populate TTS combo: built-in Edge TTS + scripts from providers/text-to-speech/."""
        scripts = self.controller.get_tts_provider_scripts()
        current = self.tts_provider_combo.currentData() if self.tts_provider_combo.count() else 'edge-tts'
        self.tts_provider_combo.blockSignals(True)
        self.tts_provider_combo.clear()
        self.tts_provider_combo.addItem("Edge TTS (Microsoft, Free)", "edge-tts")
        for s in scripts:
            self.tts_provider_combo.addItem(f"{s['name']}", s['path'])
        restored = False
        for i in range(self.tts_provider_combo.count()):
            if self.tts_provider_combo.itemData(i) == current:
                self.tts_provider_combo.setCurrentIndex(i)
                restored = True
                break
        if not restored:
            self.tts_provider_combo.setCurrentIndex(0)
        self.tts_provider_combo.blockSignals(False)
        self.on_tts_provider_changed(self.tts_provider_combo.currentIndex())

    def _update_memory_rows_visibility(self):
        """Show only the memory rows that matter right now: nothing while memory
        is disabled; threshold+max in RAG mode; the inject cap in inject_all;
        the custom-model input only when 'Custom' is picked."""
        enabled = self.memory_enabled_checkbox.isChecked()
        is_rag = self.memory_recall_mode_combo.currentData() == 'rag'
        self._memory_recall_mode_row.setVisible(enabled)
        self._memory_threshold_row.setVisible(enabled and is_rag)
        self._memory_max_row.setVisible(enabled and is_rag)
        self._memory_cap_row.setVisible(enabled and not is_rag)
        self._memory_cap_custom_row.setVisible(
            enabled and not is_rag
            and self.memory_cap_combo.currentData() == '__custom__')
        self._memory_cap_readout.setVisible(enabled and not is_rag)
        self._refresh_memory_cap_readout()
        # Embedding model only matters for RAG recall — inject-all never
        # embeds, so the whole model group hides with it (dependent-visibility
        # rule: hidden, never greyed out).
        self._memory_model_row.setVisible(enabled and is_rag)
        self._memory_model_custom_row.setVisible(
            enabled and is_rag
            and self.memory_model_combo.currentData() == '__custom__')
        self._memory_model_info.setVisible(enabled and is_rag)
        # Restart note — only when the selection differs from the LIVE model.
        live = ''
        try:
            mm = getattr(self.controller, 'memory_manager', None)
            live = mm.model_name if (mm and mm.is_ready) else ''
        except Exception:
            live = ''
        sel = self.memory_model_combo.currentData()
        if sel == '__custom__':
            sel = self.memory_model_custom_input.text().strip()
        self._memory_model_restart_note.setVisible(
            bool(enabled and is_rag and live and sel and sel != live))

    def _current_inject_cap(self) -> int:
        """The inject cap the widgets currently express (preset or Custom…)."""
        sel = self.memory_cap_combo.currentData()
        if sel == '__custom__':
            try:
                return max(100, int(self.memory_cap_custom_input.text().strip()))
            except (TypeError, ValueError):
                return 5000
        try:
            return int(sel)
        except (TypeError, ValueError):
            return 5000

    def _refresh_memory_cap_readout(self):
        """Update the live 'N tokens / M memories / within cap' readout. Uses the
        SAME summarize_memory_injection() the engine's cap logic uses, so the
        displayed number can never drift from what actually gets injected."""
        lbl = getattr(self, '_memory_cap_readout', None)
        if lbl is None:
            return
        try:
            mm = getattr(self.controller, 'memory_manager', None)
            if mm is None or not getattr(mm, 'store_ready', False):
                lbl.setText("memory initializing…")
                return
            mems = mm.get_all()
            if not mems:
                lbl.setText("no memories stored yet")
                return
            from systema.engine.ai_engine import summarize_memory_injection
            cap = self._current_inject_cap()
            s = summarize_memory_injection(mems, cap)
            n = len(mems)
            if s['omitted'] > 0:
                lbl.setText(f"≈ {s['total_tokens']:,} tokens · {n} memories · "
                            f"{s['omitted']} omitted (over {cap:,} cap)")
            else:
                lbl.setText(f"≈ {s['total_tokens']:,} tokens · {n} memories · within cap")
        except Exception as e:
            lbl.setText("")
            log.debug(f"[SettingsWindow._refresh_memory_cap_readout] {e}")

    def _update_bargein_combo_enabled(self):
        """Barge-in sensitivity only matters in Automatic mode — hide it otherwise."""
        self.bargein_widget.setVisible(
            self.interrupt_mode_combo.currentData() == 'auto')

    # ── Voice mode approval helpers ──────────────────────────────────────────
    def _open_file_history(self):
        """Open the file-tools undo-journal browser (File changes)."""
        try:
            from systema.ui.dialogs.file_history_dialog import FileHistoryDialog
            dlg = FileHistoryDialog(controller=self.controller, parent=self)
            dlg.exec()
        except Exception as e:
            log.error(f"[SettingsWindow._open_file_history] {e}")

    def _update_voice_approval_enabled(self):
        """Voice-approval sub-options are useless while the feature is off — hide them."""
        on = self.voice_approval_checkbox.isChecked()
        self.va_opts_widget.setVisible(on)
        self.va_words_widget.setVisible(on)

    def _update_voice_approval_expand_action(self):
        """The 'Expand' custom-word action only exists while the compact
        approval card is enabled (there is nothing to expand otherwise)."""
        combo = self.voice_approval_word_action
        idx = combo.findData('expand')
        if self.approval_mini_checkbox.isChecked():
            if idx < 0:
                combo.addItem("Expand", "expand")
        elif idx >= 0:
            if combo.currentIndex() == idx:
                combo.setCurrentIndex(0)
            combo.removeItem(idx)

    def _voice_approval_words_dict(self):
        """Parse the list rows ('word  ->  action') back into a dict."""
        out = {}
        for i in range(self.voice_approval_words_list.count()):
            row = self.voice_approval_words_list.item(i).text()
            if '->' in row:
                word, _, action = row.partition('->')
                word, action = word.strip(), action.strip()
                if word and action in ('approve', 'deny', 'expand'):
                    out[word] = action
        return out

    def _add_voice_approval_word(self):
        word = self.voice_approval_word_input.text().strip()
        if not word:
            return
        action = self.voice_approval_word_action.currentData()
        words = self._voice_approval_words_dict()
        words[word] = action
        self.voice_approval_words_list.clear()
        for w, a in words.items():
            self.voice_approval_words_list.addItem(f"{w}  ->  {a}")
        self.voice_approval_word_input.clear()

    def _remove_voice_approval_word(self):
        for item in self.voice_approval_words_list.selectedItems():
            self.voice_approval_words_list.takeItem(
                self.voice_approval_words_list.row(item))

    def refresh_audio_devices(self):
        """Rebuild the microphone dropdown from the de-duplicated device list.

        Stores each item's DATA as the device NAME (stable across sessions,
        unlike PortAudio ids) — None = system default. Preserves the current
        selection across rebuilds."""
        try:
            input_devices, _outputs = self.controller.get_voice_devices()

            prev = self.input_device_combo.currentData() if self.input_device_combo.count() else None
            self.input_device_combo.blockSignals(True)
            self.input_device_combo.clear()
            self.input_device_combo.addItem("Default Microphone", None)
            for device in input_devices:
                label = device['name'] + ("  - Default" if device.get('is_default') else "")
                self.input_device_combo.addItem(label, device['name'])
            idx = self.input_device_combo.findData(prev)
            self.input_device_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.input_device_combo.blockSignals(False)
        except Exception as e:
            self.show_status_message(f"Error refreshing devices: {e}")

    # ── Live mic meter + auto-refresh (Voice tab) ─────────────────────────────

    def _resolve_selected_mic_id(self):
        """Live PortAudio id for the mic currently picked in the combo (None =
        system default), via VoiceHandler's name→id resolver."""
        name = self.input_device_combo.currentData()
        try:
            return self.controller.voice_handler._resolve_input_id(name)
        except Exception:
            return None

    def _start_mic_meter(self):
        if hasattr(self, 'mic_meter'):
            self.mic_meter.start(self._resolve_selected_mic_id())

    def _stop_mic_meter(self):
        if hasattr(self, 'mic_meter'):
            self.mic_meter.stop()

    def _on_mic_combo_changed(self, _index):
        # Only restart the live meter if the Voice tab is actually showing.
        if hasattr(self, '_tabs') and self._tabs.currentIndex() == 2:
            self._start_mic_meter()

    def _on_settings_tab_changed(self, index):
        """Voice tab (index 2): start the live meter + device auto-refresh poll.
        Any other tab: stop the meter and the poll to release the mic."""
        if index == 2:
            # Rescan first so devices hotplugged while the tab was closed appear
            # (PortAudio's list is frozen until re-init; meter isn't running yet).
            try:
                self.controller.rescan_audio_devices()
            except Exception:
                pass
            self._hotplug_signature = None  # re-baseline the hotplug probe
            self.refresh_audio_devices()
            self._start_mic_meter()
            self._ensure_device_poll_timer()
            self._device_poll_timer.start()
        else:
            self._stop_mic_meter()
            if hasattr(self, '_device_poll_timer'):
                self._device_poll_timer.stop()

    def _ensure_device_poll_timer(self):
        if not hasattr(self, '_device_poll_timer'):
            from PyQt6.QtCore import QTimer
            self._device_poll_timer = QTimer(self)
            self._device_poll_timer.setInterval(2000)
            self._device_poll_timer.timeout.connect(self._on_device_poll)

    def _on_device_poll(self):
        """Detect device hotplug and refresh the list. PortAudio freezes its
        device snapshot at init, so plain re-querying never sees new devices —
        we probe the LIVE population (winmm counts on Windows) and, on change,
        re-initialize PortAudio (meter stopped first — its stream must not
        survive the re-init), then rebuild the combo and restart the meter.
        Where no live probe exists (POSIX), rescan every 5th tick instead."""
        try:
            sig = self.controller.audio_hotplug_signature()
            if sig is not None:
                if sig == getattr(self, '_hotplug_signature', None):
                    return
                changed = getattr(self, '_hotplug_signature', None) is not None
                self._hotplug_signature = sig
                if not changed:
                    return  # first tick: just record the baseline
            else:
                # No live probe on this platform — periodic rescan.
                self._poll_tick = getattr(self, '_poll_tick', 0) + 1
                if self._poll_tick % 5:
                    return
            self._stop_mic_meter()
            self.controller.rescan_audio_devices()
            self.refresh_audio_devices()
            self._start_mic_meter()
        except Exception:
            pass

    # ── Desktop shortcut (cross-OS) ─────────────────────────────────────────
    def _update_priv_labels(self):
        """Prefix a check mark on the selected privilege so it's unmistakable.
        The shortcut and autostart toggles are updated independently — this runs
        once during the shortcut build, before the autostart radios exist."""
        try:
            self._sc_radio_normal.setText(
                ("✓  " if self._sc_radio_normal.isChecked() else "") + "Normal privileges")
            self._sc_radio_admin.setText(
                ("✓  " if self._sc_radio_admin.isChecked() else "") + "Run as administrator")
        except Exception:
            pass
        try:
            self._as_priv_normal.setText(
                ("✓  " if self._as_priv_normal.isChecked() else "") + "Normal privileges")
            self._as_priv_admin.setText(
                ("✓  " if self._as_priv_admin.isChecked() else "") + "Start as administrator")
        except Exception:
            pass

    def _sync_shortcut_radios(self):
        """Reflect the existing shortcut's real privilege + relabel the apply button
        (Create ⇄ Update) — WITHOUT touching the status text, so an apply/remove
        message stays visible."""
        exists = False
        try:
            from systema.common import shortcuts as _sc
            st = _sc.status()
            exists = bool(st["exists"])
            if st["exists"] and st["admin"] is not None:
                (self._sc_radio_admin if st["admin"] else self._sc_radio_normal).setChecked(True)
        except Exception:
            pass
        try:
            self._sc_apply_btn.setText("Update Shortcut" if exists else "Create Shortcut")
        except Exception:
            pass
        self._update_priv_labels()

    def _refresh_shortcut_status(self):
        try:
            from systema.common import shortcuts as _sc
            st = _sc.status()
            if st["exists"]:
                lvl = "administrator" if st["admin"] else "normal"
                self._sc_status_lbl.setText(f"✓ Shortcut on Desktop  ·  {lvl}")
            else:
                self._sc_status_lbl.setText("No desktop shortcut yet.")
        except Exception as e:
            self._sc_status_lbl.setText(f"Shortcut status unavailable: {e}")
        self._sync_shortcut_radios()

    def _apply_shortcut(self):
        try:
            from systema.common import shortcuts as _sc
            ok, msg = _sc.set_admin(self._sc_radio_admin.isChecked())
            self._sc_status_lbl.setText(("✓  " if ok else "✗  ") + msg)
        except Exception as e:
            self._sc_status_lbl.setText(f"✗  {e}")
        self._sync_shortcut_radios()

    def _remove_shortcut(self):
        try:
            from systema.common import shortcuts as _sc
            ok, msg = _sc.remove_shortcut()
            self._sc_status_lbl.setText(("✓  " if ok else "✗  ") + msg)
            self._sc_radio_normal.setChecked(True)
        except Exception as e:
            self._sc_status_lbl.setText(f"✗  {e}")
        self._sync_shortcut_radios()

    # ── Start at login (cross-OS, per-user, normal OR elevated) ─────────────
    def _sync_autostart_ui(self):
        """Relabel Enable ⇄ Update and reflect the existing entry's real privilege —
        WITHOUT touching the status text, so an apply/remove message stays visible."""
        enabled = False
        admin = None
        try:
            from systema.common import autostart as _as
            st = _as.status()
            enabled = bool(st.get("enabled"))
            admin = st.get("admin")
        except Exception:
            pass
        try:
            self._as_enable_btn.setText("Update" if enabled else "Enable")
        except Exception:
            pass
        try:
            if enabled and admin is not None:
                (self._as_priv_admin if admin else self._as_priv_normal).setChecked(True)
        except Exception:
            pass
        self._update_priv_labels()

    def _refresh_autostart_status(self):
        try:
            from systema.common import autostart as _as
            st = _as.status()
            if st.get("enabled"):
                m = st.get("method")
                extra = f"  [{m}]" if m and m not in ("native", None) else ""
                admin = st.get("admin")
                lvl = (f"  ·  {'administrator' if admin else 'normal'}"
                       if admin is not None else "")
                self._as_status_lbl.setText(
                    f"✓ Starts automatically at login (this user).{extra}{lvl}")
            else:
                self._as_status_lbl.setText("Not set to start at login.")
        except Exception as e:
            self._as_status_lbl.setText(f"Autostart status unavailable: {e}")
        self._sync_autostart_ui()

    def _apply_autostart(self):
        try:
            from systema.common import autostart as _as
            method = getattr(self, "_as_method", "xdg")
            as_admin = self._as_priv_admin.isChecked()
            ok, msg = _as.enable_autostart(as_admin=as_admin, method=method)
            self._as_status_lbl.setText(("✓  " if ok else "✗  ") + msg)
        except Exception as e:
            self._as_status_lbl.setText(f"✗  {e}")
        self._sync_autostart_ui()

    def _diagnose_autostart(self):
        """Show the autostart 'doctor' report — the facts that decide whether the
        login-time autostart fires, plus the tail of the launcher log."""
        try:
            from systema.common import autostart as _as
            report = _as.diagnose()
        except Exception as e:
            report = f"Could not run diagnostics: {e}"
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Autostart diagnostics")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Autostart doctor — what decides whether it fires at login "
                    "(open Details):")
        box.setDetailedText(report)
        box.exec()

    def _view_autostart_log(self):
        """Show the login-time breadcrumb log written by the Linux launcher."""
        try:
            from systema.common import autostart as _as
            logf = _as.autostart_log_path()
        except Exception as e:
            self._as_status_lbl.setText(f"✗  {e}")
            return
        if not logf.exists():
            self._as_status_lbl.setText(
                "No autostart log yet — it's written when the login autostart fires "
                "(Linux). Enable autostart, then log out and back in.")
            return
        try:
            text = logf.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            self._as_status_lbl.setText(f"✗  could not read log: {e}")
            return
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox(self)
        box.setWindowTitle("Autostart log")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(str(logf))
        box.setDetailedText(text[-6000:] or "(empty)")
        box.exec()

    def _remove_autostart(self):
        try:
            from systema.common import autostart as _as
            ok, msg = _as.disable_autostart()
            self._as_status_lbl.setText(("✓  " if ok else "✗  ") + msg)
            self._as_priv_normal.setChecked(True)
        except Exception as e:
            self._as_status_lbl.setText(f"✗  {e}")
        self._sync_autostart_ui()

    def _test_autostart(self):
        """Launch the app right now via the same run script autostart uses, so the
        user can confirm the launch path works without logging out."""
        try:
            from systema.common import autostart as _as
            ok, msg = _as.run_now()
            self._as_status_lbl.setText(("✓  " if ok else "✗  ") + msg
                                        + ("  (if already running you'll see the "
                                           "'already running' notice)" if ok else ""))
        except Exception as e:
            self._as_status_lbl.setText(f"✗  {e}")

    def load_settings(self):
        """Load settings from controller"""
        # Populating widgets programmatically must NOT trip the 'unsaved' flag.
        self._suppress_dirty = True
        try:
            self._load_settings_impl()
        finally:
            self._suppress_dirty = False
            self._dirty = False
            # Loaded values become the new baseline to diff future edits against.
            if hasattr(self, '_tracked_widgets'):
                self._capture_baseline()
            try:
                self.footer_status_label.setText("")
                self.footer_status_label.setStyleSheet(
                    "color:transparent; font-size:11px; background:transparent;")
            except Exception:
                pass

    def _load_settings_impl(self):
        # Load General settings
        self.open_chat_on_startup_checkbox.setChecked(
            self.controller.settings.get('open_chat_on_startup', False)
        )
        self.open_packet_on_startup_checkbox.setChecked(
            self.controller.settings.get('open_packet_on_startup', False)
        )
        self.show_token_count_checkbox.setChecked(
            self.controller.settings.get('show_token_count', True)
        )
        self.exec_timeout_spin.setValue(
            self.controller.settings.get('tool_execution_timeout_seconds', 300)
        )
        self.response_timeout_spin.setValue(
            self.controller.settings.get('response_timeout_seconds', 0)
        )
        self.streaming_checkbox.setChecked(
            self.controller.settings.get('streaming_enabled', True)
        )
        # Sync AFTER values are loaded (house pattern), and keep the typing
        # block in step whenever the toggle changes.
        self.streaming_checkbox.toggled.connect(
            lambda _: self._update_streaming_visibility())
        self._update_streaming_visibility()
        self.packet_port_spin.setValue(
            self.controller.settings.get('packet_port', 1111)
        )
        _tc_mode = self.controller.settings.get('tool_calling_mode', 'compat')
        _tc_idx = self.tool_calling_mode_combo.findData(_tc_mode)
        self.tool_calling_mode_combo.setCurrentIndex(_tc_idx if _tc_idx >= 0 else 0)

        _wm_style = self.controller.settings.get('work_mode_prompt_style', 'detailed')
        _wm_idx = self.work_mode_prompt_style_combo.findData(_wm_style)
        self.work_mode_prompt_style_combo.setCurrentIndex(_wm_idx if _wm_idx >= 0 else 0)

        # Load active LLM provider script (drop stale unsaved Display edits so
        # the form re-reads persisted values)
        self._prov_display_cache = {}
        self._refresh_llm_provider_scripts()
        self._on_llm_provider_changed(self.provider_script_combo.currentIndex())
        saved_script_path = self.controller.get_custom_script_path()
        if saved_script_path:
            for i in range(self.provider_script_combo.count()):
                if self.provider_script_combo.itemData(i) == saved_script_path:
                    self.provider_script_combo.setCurrentIndex(i)
                    break

        # Load debug mode
        debug_mode = self.controller.get_debug_mode()
        self.debug_checkbox.setChecked(debug_mode)

        # Load supervised execution mode
        supervised_mode = self.controller.settings.get('supervised_execution', True)  # Default ON
        self.supervised_checkbox.setChecked(supervised_mode)

        # Load execution policy + audit view
        try:
            _pol = self.controller.settings.get('security_exec_policy', {}) or {}
            for _cat, _combo in getattr(self, '_policy_combos', {}).items():
                _val = _pol.get(_cat, 'ask')
                _idx = _combo.findText(_val if _val in ('ask', 'allow', 'deny') else 'ask')
                _combo.setCurrentIndex(max(0, _idx))
            self.review_safe_code_checkbox.setChecked(
                self.controller.settings.get('security_review_safe_code', False))
            self._refresh_preset_combo()
            self._refresh_audit_view()
        except Exception:
            pass
        # Load voice mode approval
        try:
            s = self.controller.settings
            self.voice_approval_checkbox.setChecked(
                s.get('voice_approval_enabled', True))
            _i = self.voice_approval_mode_combo.findData(
                s.get('voice_approval_mode', 'basic'))
            if _i >= 0:
                self.voice_approval_mode_combo.setCurrentIndex(_i)
            self.voice_approval_confirm_checkbox.setChecked(
                s.get('voice_approval_confirm_risky', True))
            self.voice_approval_announce_checkbox.setChecked(
                s.get('voice_approval_announce', False))
            self.voice_approval_words_list.clear()
            for _w, _a in (s.get('voice_approval_custom_words') or {}).items():
                self.voice_approval_words_list.addItem(f"{_w}  ->  {_a}")
            self.approval_mini_checkbox.setChecked(
                s.get('approval_mini_enabled', True))
            self._update_voice_approval_enabled()
            self._update_voice_approval_expand_action()
        except Exception:
            pass

        # Load Tool lockout switch
        tool_exec_locked = self.controller.settings.get('tool_execution_lockout', False)
        self.tool_exec_lockout_checkbox.setChecked(tool_exec_locked)

        # Load system prompt hijacking
        sys_prompt_hijacked = self.controller.settings.get('system_prompt_hijacked', False)
        self.system_prompt_hijack_checkbox.setChecked(sys_prompt_hijacked)
        self.include_image_tools_checkbox.setChecked(
            self.controller.settings.get('include_image_tools', False))
        self.include_controller_ref_checkbox.setChecked(
            self.controller.settings.get('include_controller_ref', False))
        self.include_notify_tool_checkbox.setChecked(
            self.controller.settings.get('include_notify_tool', False))

        # Load file-tools history options
        self.file_history_checkbox.setChecked(
            self.controller.settings.get('file_history_enabled', True))
        self.file_history_git_checkbox.setChecked(
            self.controller.settings.get('file_history_git', False))
        _days_idx = self.file_history_days_combo.findData(
            self.controller.settings.get('file_history_keep_days', 14))
        self.file_history_days_combo.setCurrentIndex(max(0, _days_idx))
        self._update_fh_enabled()

        # Load system prompt
        self.system_prompt_hijack_input.setPlainText(
            self.controller.settings.get('custom_system_prompt', '')
        )

        # Load interrupt mode
        interrupt_mode = self.controller.settings.get('voice_interrupt_mode', 'manual')
        index = self.interrupt_mode_combo.findData(interrupt_mode)
        if index >= 0:
            self.interrupt_mode_combo.setCurrentIndex(index)

        # Load barge-in sensitivity (only meaningful in automatic mode)
        bargein = self.controller.settings.get('voice_bargein_sensitivity', 'balanced')
        index = self.bargein_sensitivity_combo.findData(bargein)
        if index >= 0:
            self.bargein_sensitivity_combo.setCurrentIndex(index)
        self._update_bargein_combo_enabled()

        # Load voice devices
        self.refresh_audio_devices()

        # Load voice settings (voice_input_device is stored as a device NAME)
        input_device = self.controller.settings.get('voice_input_device')

        # Load the "show setup popup on enable" toggle (default ON)
        self.voice_setup_prompt_checkbox.setChecked(
            self.controller.settings.get('voice_setup_prompt_enabled', True)
        )

        # Load ElevenLabs speech tag toggle
        self.elevenlabs_tags_checkbox.setChecked(
            self.controller.settings.get('elevenlabs_enabled', False)
        )

        # Load filler interjections toggle (default ON)
        self.voice_fillers_checkbox.setChecked(
            self.controller.settings.get('voice_fillers_enabled', True)
        )

        # Load TTS provider/script
        self._refresh_tts_provider_scripts()
        tts_provider = self.controller.get_tts_provider()
        tts_script = self.controller.settings.get('tts_script_path', '')
        restore_data = tts_script if (tts_provider == 'custom_script' and tts_script) else tts_provider
        for i in range(self.tts_provider_combo.count()):
            if self.tts_provider_combo.itemData(i) == restore_data:
                self.tts_provider_combo.setCurrentIndex(i)
                break

        # Load prefilling settings
        self.prefilling_checkbox.setChecked(
            self.controller.settings.get('prefilling_enabled', True)
        )
        _pf_mode = self.controller.settings.get('prefilling_mode', 'premade')
        if _pf_mode == 'session':
            self.pf_radio_session.setChecked(True)
        else:
            self.pf_radio_premade.setChecked(True)
        self._refresh_prefilling_sessions()
        _pf_sid = self.controller.settings.get('prefilling_session_id', '')
        for i in range(self.pf_session_combo.count()):
            if self.pf_session_combo.itemData(i) == _pf_sid:
                self.pf_session_combo.setCurrentIndex(i)
                break

        # Load memory engine settings
        self.memory_enabled_checkbox.setChecked(
            self.controller.settings.get('memory_enabled', True)
        )
        threshold = self.controller.settings.get('memory_threshold', 0.4)
        for i in range(self.memory_threshold_combo.count()):
            if self.memory_threshold_combo.itemData(i) == threshold:
                self.memory_threshold_combo.setCurrentIndex(i)
                break
        max_results = self.controller.settings.get('memory_max_results', 3)
        for i in range(self.memory_max_combo.count()):
            if self.memory_max_combo.itemData(i) == max_results:
                self.memory_max_combo.setCurrentIndex(i)
                break
        recall_mode = self.controller.settings.get('memory_recall_mode', 'inject_all')
        for i in range(self.memory_recall_mode_combo.count()):
            if self.memory_recall_mode_combo.itemData(i) == recall_mode:
                self.memory_recall_mode_combo.setCurrentIndex(i)
                break
        cap_tokens = self.controller.settings.get('memory_inject_cap_tokens', 5000)
        _cap_idx = self.memory_cap_combo.findData(cap_tokens)
        if _cap_idx >= 0:
            self.memory_cap_combo.setCurrentIndex(_cap_idx)
        else:
            _ci = self.memory_cap_combo.findData('__custom__')
            if _ci >= 0:
                self.memory_cap_combo.setCurrentIndex(_ci)
            self.memory_cap_custom_input.setText(str(cap_tokens))
        embed_model = self.controller.settings.get(
            'memory_embed_model', 'sentence-transformers/all-MiniLM-L6-v2')
        _mi = self.memory_model_combo.findData(embed_model)
        if _mi >= 0:
            self.memory_model_combo.setCurrentIndex(_mi)
            self.memory_model_custom_input.setText("")
        else:
            self.memory_model_combo.setCurrentIndex(
                self.memory_model_combo.findData('__custom__'))
            self.memory_model_custom_input.setText(embed_model)
        self._update_memory_rows_visibility()

        # Load selected theme
        saved_theme = self.controller.settings.get('chat_theme', 'obsidian_blue')
        self._selected_theme = saved_theme
        if hasattr(self, '_theme_btn_group'):
            for btn in self._theme_btn_group.buttons():
                if btn.property('theme_key') == saved_theme:
                    btn.setChecked(True)
                    break
        if hasattr(self, '_theme_cards'):
            _, _, _ELEV, _BORDER, _ACCENT, _, _ = self._palette()
            for k, c in self._theme_cards.items():
                try:
                    c.setStyleSheet(
                        f"QFrame {{ background:{_ELEV}; border:2px solid {(_ACCENT if k == saved_theme else _BORDER)}; border-radius:10px; }}"
                        f"QFrame:hover {{ border-color:{_ACCENT}; }}"
                    )
                except RuntimeError:
                    pass

        # Load glass background settings
        glass_enabled = self.controller.settings.get('glass_background_enabled', False)
        self.glass_enabled_checkbox.setChecked(glass_enabled)
        glass_opacity = self.controller.settings.get('glass_background_opacity', 0.75)
        self.glass_opacity_slider.setValue(int(glass_opacity * 100))
        self.glass_opacity_value_label.setText(f"{int(glass_opacity * 100)}%")
        # Per-window glass checklist
        _glass_wins = _theme.glass_windows(self.controller)
        for _wkey, _cb in getattr(self, 'glass_window_checkboxes', {}).items():
            _cb.setChecked(_wkey in _glass_wins)
        if hasattr(self, 'glass_sidebar_checkbox'):
            self.glass_sidebar_checkbox.setChecked(
                self.controller.settings.get('glass_chat_sidebar', True))
        if hasattr(self, '_sync_glass_checklist_enabled'):
            self._sync_glass_checklist_enabled()

        if input_device is not None:
            index = self.input_device_combo.findData(input_device)
            if index >= 0:
                self.input_device_combo.setCurrentIndex(index)

        # CRITICAL FIX: Load VAD and TTS voice properly
        vad_level = self.controller.settings.get('vad_aggressiveness', 3)
        for i in range(self.vad_combo.count()):
            if self.vad_combo.itemData(i) == vad_level:
                self.vad_combo.setCurrentIndex(i)
                break

        tts_voice = self.controller.settings.get('tts_voice', 'en-CA-ClaraNeural')
        for i in range(self.tts_voice_combo.count()):
            if self.tts_voice_combo.itemData(i) == tts_voice:
                self.tts_voice_combo.setCurrentIndex(i)
                break

        # Load VAD settings
        webrtc_enabled = self.controller.settings.get('vad_webrtc_enabled', True)
        silero_enabled = self.controller.settings.get('vad_silero_enabled', False)
        silero_threshold = self.controller.settings.get('vad_silero_threshold', 0.5)

        self.webrtc_vad_checkbox.setChecked(webrtc_enabled)
        self.silero_vad_checkbox.setChecked(silero_enabled)

        # Set WebRTC aggressiveness
        vad_level = self.controller.settings.get('vad_aggressiveness', 3)
        for i in range(self.vad_combo.count()):
            if self.vad_combo.itemData(i) == vad_level:
                self.vad_combo.setCurrentIndex(i)
                break

        # Set Silero threshold
        for i in range(self.silero_threshold_combo.count()):
            if self.silero_threshold_combo.itemData(i) == silero_threshold:
                self.silero_threshold_combo.setCurrentIndex(i)
                break

        # Update visibility
        self.on_vad_settings_changed()

    # ── execution-policy presets ────────────────────────────────────────────
    def _current_policy_rules(self) -> dict:
        """Read the per-category combos into a {category: policy} map."""
        return {_cat: _combo.currentText()
                for _cat, _combo in getattr(self, '_policy_combos', {}).items()}

    def _set_policy_rules(self, rules: dict):
        """Push a {category: policy} map onto the per-category combos."""
        for _cat, _combo in getattr(self, '_policy_combos', {}).items():
            _val = rules.get(_cat, 'ask')
            _idx = _combo.findText(_val if _val in ('ask', 'allow', 'deny') else 'ask')
            _combo.setCurrentIndex(max(0, _idx))

    def refresh_security_policy_ui(self):
        """Re-sync the per-category policy combos from the saved
        security_exec_policy. Called by the controller after the code-approval
        dialog persists an ask->allow promotion ("Always allow these operations"),
        so an open Settings window reflects the change live. Only categories
        present in the saved policy are touched, so unsaved edits are preserved."""
        try:
            _pol = (getattr(self.controller, 'settings', {}) or {}).get(
                'security_exec_policy', {}) or {}
            for _cat, _combo in getattr(self, '_policy_combos', {}).items():
                if _cat not in _pol:
                    continue
                _val = _pol.get(_cat, 'ask')
                _idx = _combo.findText(_val if _val in ('ask', 'allow', 'deny') else 'ask')
                _combo.setCurrentIndex(max(0, _idx))
        except Exception:
            pass

    def _refresh_preset_combo(self, select: str | None = None):
        """Repopulate the preset dropdown (built-ins first, then custom)."""
        try:
            from systema.security import code_guard as _guard
            combo = getattr(self, '_preset_combo', None)
            if combo is None:
                return
            settings = getattr(self.controller, 'settings', {}) or {}
            builtin = list(_guard.BUILTIN_PRESETS.keys())
            custom = [n for n in (settings.get(_guard._PRESETS_KEY) or {})
                      if n not in _guard.BUILTIN_PRESETS]
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(builtin)
            if custom:
                combo.insertSeparator(len(builtin))
                combo.addItems(sorted(custom))
            if select:
                _i = combo.findText(select)
                if _i >= 0:
                    combo.setCurrentIndex(_i)
            combo.blockSignals(False)
        except Exception:
            pass

    def _apply_policy_preset(self):
        """Load the selected preset's rules into the category combos."""
        try:
            from systema.security import code_guard as _guard
            name = self._preset_combo.currentText()
            presets = _guard.load_presets(getattr(self.controller, 'settings', {}) or {})
            if name in presets:
                self._set_policy_rules(presets[name])
        except Exception as e:
            QMessageBox.warning(self, "Could not apply preset", str(e))

    def _save_policy_preset(self):
        """Save the current category rows as a new named custom preset."""
        try:
            from systema.security import code_guard as _guard
            name, ok = QInputDialog.getText(self, "Save preset",
                                            "Name for this policy preset:")
            if not ok or not name.strip():
                return
            _guard.save_custom_preset(self.controller.settings, name.strip(),
                                      self._current_policy_rules())
            self.controller.save_settings()
            self._refresh_preset_combo(select=name.strip())
            QMessageBox.information(self, "Preset saved",
                                    f"Saved preset '{name.strip()}'.")
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
        except Exception as e:
            QMessageBox.warning(self, "Could not save preset", str(e))

    def _delete_policy_preset(self):
        """Delete the selected custom preset (built-ins are protected)."""
        try:
            from systema.security import code_guard as _guard
            name = self._preset_combo.currentText()
            if name in _guard.BUILTIN_PRESETS:
                QMessageBox.information(self, "Built-in preset",
                                        f"'{name}' is a built-in preset and can't be deleted.")
                return
            if QMessageBox.question(self, "Delete preset?",
                                    f"Delete the custom preset '{name}'?") \
                    != QMessageBox.StandardButton.Yes:
                return
            _guard.delete_custom_preset(self.controller.settings, name)
            self.controller.save_settings()
            self._refresh_preset_combo()
        except Exception as e:
            QMessageBox.warning(self, "Could not delete preset", str(e))

    def _forget_approvals(self):
        """Clear the ephemeral session allow-list — the operation categories the
        user chose "Don't ask again this session" for in the approval dialog."""
        try:
            tm = getattr(getattr(self.controller, 'ai', None), 'tool_manager', None)
            cats = getattr(tm, 'session_allowed_categories', None) if tm else None
            n = len(cats) if cats else 0
            if cats:
                cats.clear()
            QMessageBox.information(self, "Session allow-list cleared",
                                    f"Cleared {n} session-allowed operation type(s). "
                                    "These will be prompted for again.")
        except Exception as e:
            QMessageBox.warning(self, "Could not clear", str(e))

    def _refresh_audit_view(self):
        """Load the tail of the execution audit log into the viewer."""
        try:
            from systema.security.code_guard import AuditLog
            rows = AuditLog.tail(50)
            if not rows:
                self.audit_view.setPlainText("No executions recorded yet.")
                return
            lines = [f"{r.get('ts','')}  {r.get('decision','?'):>8} "
                     f"[{r.get('source','?')}]  {r.get('type','?')}  "
                     f"risk: {r.get('risk','')}" for r in rows]
            self.audit_view.setPlainText("\n".join(lines))
        except Exception as e:
            self.audit_view.setPlainText(f"(could not read audit log: {e})")

    def _clear_audit_log(self):
        """Truncate the execution audit log after confirmation."""
        if QMessageBox.question(self, "Clear audit log?",
                                "Erase the recorded execution history?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            from systema.security.code_guard import AuditLog
            AuditLog._FILE.write_text("", encoding="utf-8")
        except Exception:
            pass
        self._refresh_audit_view()

    # ── unsaved-changes indicator (real-time, value-compared) ───────────────
    _DIRTY_SIGNAL = {}   # populated lazily in _wire_dirty_tracking

    def _wire_dirty_tracking(self):
        """Track every editable setting widget and, on ANY change, recompute
        whether the current values still match the baseline captured at load.
        This way undoing a change (toggling back to the saved value) clears the
        'not saved' indicator in real time, instead of latching on forever."""
        from PyQt6.QtWidgets import (QCheckBox, QRadioButton, QComboBox, QSpinBox,
                                     QDoubleSpinBox, QSlider, QLineEdit,
                                     QPlainTextEdit, QTextEdit)
        self._tracked_widgets = []

        def _wire(widgets, signal_name, skip_readonly=False):
            for w in widgets:
                if skip_readonly and w.isReadOnly():
                    continue
                self._tracked_widgets.append(w)
                # recompute on every change (value/text signals fire on programmatic
                # edits too, but _recompute_dirty compares to baseline and the load
                # guard prevents spurious flips during population).
                getattr(w, signal_name).connect(self._recompute_dirty)

        _wire(self.findChildren(QCheckBox), 'toggled')
        _wire(self.findChildren(QRadioButton), 'toggled')
        _wire(self.findChildren(QComboBox), 'currentIndexChanged')
        _wire(self.findChildren(QLineEdit), 'textChanged')
        _wire(self.findChildren((QSpinBox, QDoubleSpinBox)), 'valueChanged')
        _wire(self.findChildren(QSlider), 'valueChanged')
        _wire(self.findChildren((QPlainTextEdit, QTextEdit)), 'textChanged', skip_readonly=True)
        self._capture_baseline()

    def _widget_value(self, w):
        """Comparable snapshot of a widget's current value; None if it's gone."""
        from PyQt6.QtWidgets import (QCheckBox, QRadioButton, QComboBox, QSpinBox,
                                     QDoubleSpinBox, QSlider, QLineEdit,
                                     QPlainTextEdit, QTextEdit)
        try:
            if isinstance(w, (QCheckBox, QRadioButton)):
                return w.isChecked()
            if isinstance(w, QComboBox):
                return w.currentIndex()
            if isinstance(w, (QSpinBox, QDoubleSpinBox, QSlider)):
                return w.value()
            if isinstance(w, QLineEdit):
                return w.text()
            if isinstance(w, (QPlainTextEdit, QTextEdit)):
                return w.toPlainText()
        except RuntimeError:
            return None
        return None

    def _capture_baseline(self):
        """Snapshot current widget values as the 'saved' reference to diff against."""
        self._baseline = {id(w): self._widget_value(w)
                          for w in getattr(self, '_tracked_widgets', [])}

    def _recompute_dirty(self, *args):
        """Show the indicator iff any tracked widget differs from the baseline."""
        if getattr(self, '_suppress_dirty', False):
            return
        base = getattr(self, '_baseline', {})
        dirty = False
        for w in getattr(self, '_tracked_widgets', []):
            try:
                if self._widget_value(w) != base.get(id(w)):
                    dirty = True
                    break
            except RuntimeError:
                continue
        self._dirty = dirty
        if not hasattr(self, 'footer_status_label'):
            return
        if dirty:
            try:
                _ACCENT = self._palette()[4]
            except Exception:
                _ACCENT = "#e0a95f"
            self.footer_status_label.setStyleSheet(
                f"color:{_ACCENT}; font-size:11px; font-weight:600; background:transparent;")
            self.footer_status_label.setText(
                "● Settings changed — not saved yet. Click \"Save Settings\".")
        else:
            self.footer_status_label.setStyleSheet(
                "color:transparent; font-size:11px; background:transparent;")
            self.footer_status_label.setText("")

    def save_settings(self):
        """Persist settings WITHOUT freezing the UI: read widgets + apply set_*()
        on the GUI thread (per-setting disk writes suppressed via
        controller._settings_batch), then write to disk ONCE on a worker thread
        behind a spinner, and apply the theme/bubble UI when it finishes."""
        if getattr(self, '_saving_in_progress', False):
            return   # a save is already mid-flight — ignore the double-click
        self._saving_in_progress = True
        # Show + PAINT the spinner BEFORE the widget-collect phase: the set_*()
        # side effects below block the GUI thread, and running them first meant
        # the overlay only ever appeared for the (short) disk write — i.e. never.
        self._show_save_spinner(True)
        QTimer.singleShot(0, self._collect_then_save)

    def _collect_then_save(self):
        """Collect widgets (fast dict writes), then apply only the CHANGED
        set_*() side effects one event-loop tick at a time — the spinner keeps
        animating and the app stays responsive between steps. The single disk
        write then runs on the worker thread."""
        import time as _time
        t0 = _time.perf_counter()
        self.controller._settings_batch = True
        try:
            snap = dict(self.controller.settings)   # pre-save values (change-gating)
            self._write_settings_from_widgets()
            queue = self._build_side_effect_queue(snap)
            self._theme_changed = (snap.get('chat_theme')
                                   != self.controller.settings.get('chat_theme'))
            self._bubble_changed = (snap.get('chat_bubble_style', 'blend')
                                    != self.controller.settings.get('chat_bubble_style', 'blend'))
        except Exception:
            self.controller._settings_batch = False
            self._show_save_spinner(False)
            self._saving_in_progress = False
            raise
        log.info(f"[save] collect {(_time.perf_counter() - t0) * 1000:.0f} ms, "
                 f"{len(queue)} changed side effect(s): {[l for l, _ in queue]}")
        self._run_side_effects(queue)

    def _run_side_effects(self, queue):
        """Apply one queued setter per event-loop tick (logging slow ones), then
        hand off to the disk-write worker. Yielding between setters is what
        keeps the spinner spinning and every window responsive mid-save."""
        if not queue:
            self.controller._settings_batch = False
            self._start_threaded_save()
            return
        import time as _time
        label, fn = queue.pop(0)
        t0 = _time.perf_counter()
        try:
            fn()
        except Exception as e:
            log.error(f"[_run_side_effects] '{label}' failed: {e}")
        ms = (_time.perf_counter() - t0) * 1000
        (log.info if ms >= 50 else log.debug)(f"[save] {label}: {ms:.0f} ms")
        QTimer.singleShot(0, lambda: self._run_side_effects(queue))

    def _start_threaded_save(self):
        """Persist once, off the GUI thread, behind the blocking spinner overlay."""
        self._save_worker = _SettingsSaveWorker(self.controller)
        self._save_worker.done.connect(self._on_settings_saved)
        self._save_worker.start()

    def _on_settings_saved(self):
        """Worker finished the disk write — drop the spinner, then apply the
        theme / bubble UI (GUI thread) and release the guard."""
        self._show_save_spinner(False)
        self._saving_in_progress = False

        theme = self.controller.settings.get('chat_theme', 'obsidian_blue')
        bubble_style = self.controller.settings.get('chat_bubble_style', 'blend')
        # Broadcast the theme to every open window — ONLY when it actually
        # changed. The full-app restyle is the one save step Qt can't do
        # off-thread, so an unrelated save must never pay for it.
        if getattr(self, '_theme_changed', True):
            try:
                if hasattr(self.controller, 'broadcast_theme'):
                    self.controller.broadcast_theme(theme)
                else:
                    chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
                    if chat_win and hasattr(chat_win, 'apply_theme'):
                        chat_win.apply_theme(theme)
            except Exception:
                pass
        # Apply the bubble style live — same change-gate reasoning.
        if getattr(self, '_bubble_changed', True):
            try:
                chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
                if chat_win and hasattr(chat_win, 'apply_bubble_style'):
                    chat_win.apply_bubble_style(bubble_style)
            except Exception:
                pass
        # Re-baseline so the just-saved values count as the new 'clean' state.
        self._dirty = False
        if hasattr(self, '_tracked_widgets'):
            self._capture_baseline()
        self.show_status_message("Settings saved")

    def _show_save_spinner(self, show):
        """Translucent overlay + centred spinner that also blocks interaction
        while the disk write runs (so the dialog 'waits' for it). No emoji."""
        if show:
            try:
                accent = _theme.current_palette(self.controller).get('accent', '#5A9CF8')
            except Exception:
                accent = '#5A9CF8'
            ov = QWidget(self)
            ov.setStyleSheet("background: rgba(0,0,0,0.38);")
            col = QVBoxLayout(ov)
            col.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.setSpacing(12)
            sp = _Spinner(ov, size=46, color=accent)
            col.addWidget(sp, alignment=Qt.AlignmentFlag.AlignCenter)
            lbl = QLabel("Saving")
            lbl.setStyleSheet("color:#E8EAED; font-size:12px; background:transparent;")
            col.addWidget(lbl, alignment=Qt.AlignmentFlag.AlignCenter)
            ov.setGeometry(self.rect())
            ov.raise_()
            ov.show()
            sp.start()
            # Synchronous first paint — the collect phase blocks the GUI thread
            # right after this, and a queued paint event would never get to run
            # before it (the spinner freezes mid-collect but stays visible).
            ov.repaint()
            self._save_overlay = ov
            self._save_spinner = sp
        else:
            ov = getattr(self, '_save_overlay', None)
            if ov is not None:
                try:
                    self._save_spinner.stop()
                    ov.hide()
                    ov.deleteLater()
                except RuntimeError:
                    pass
                self._save_overlay = None
                self._save_spinner = None

    def _write_settings_from_widgets(self):
        """Read every widget into controller.settings — PLAIN dict writes and
        cheap GUI touches only. The live set_*() side effects are queued by
        _build_side_effect_queue() and applied one event-loop tick at a time
        (see _collect_then_save), so this stays fast on the GUI thread."""
        s = self.controller.settings

        # General
        s['open_chat_on_startup'] = self.open_chat_on_startup_checkbox.isChecked()
        s['open_packet_on_startup'] = self.open_packet_on_startup_checkbox.isChecked()
        s['show_token_count'] = self.show_token_count_checkbox.isChecked()
        try:
            chat_win = getattr(getattr(self.controller, 'ui', None), 'chat_window', None)
            if chat_win and hasattr(chat_win, '_token_count_lbl'):
                chat_win._token_count_lbl.setVisible(self.show_token_count_checkbox.isChecked())
        except Exception:
            pass
        s['tool_execution_timeout_seconds'] = self.exec_timeout_spin.value()
        s['response_timeout_seconds'] = self.response_timeout_spin.value()
        s['streaming_enabled'] = self.streaming_checkbox.isChecked()
        s['packet_port'] = self.packet_port_spin.value()
        s['tool_calling_mode'] = self.tool_calling_mode_combo.currentData()
        s['work_mode_prompt_style'] = self.work_mode_prompt_style_combo.currentData()

        # Per-provider Display values — settings['provider_display_values']
        # keyed by script file name, so every provider keeps its OWN saved
        # fields. MERGED per script (never wholesale-replaced): switching
        # provider in the combo must not wipe another script's values.
        # No side-effect queue entry needed: the engine re-imports the script
        # every request and setattr's these onto it.
        try:
            self._stash_provider_display_cache()
            pdv = dict(s.get('provider_display_values') or {})
            for _key, _vals in self._prov_display_cache.items():
                if not _key:
                    continue
                _merged = dict(pdv.get(_key) or {})
                _merged.update(_vals)
                pdv[_key] = _merged
            s['provider_display_values'] = pdv
        except Exception:
            log.error("[settings] provider_display_values collect failed", exc_info=True)

        # Supervised execution + policy
        s['supervised_execution'] = self.supervised_checkbox.isChecked()
        try:
            s['security_exec_policy'] = {
                _cat: _combo.currentText()
                for _cat, _combo in getattr(self, '_policy_combos', {}).items()}
            s['security_review_safe_code'] = \
                self.review_safe_code_checkbox.isChecked()
        except Exception:
            pass

        # Voice mode approval
        try:
            s['voice_approval_enabled'] = self.voice_approval_checkbox.isChecked()
            s['voice_approval_mode'] = self.voice_approval_mode_combo.currentData()
            s['voice_approval_confirm_risky'] = \
                self.voice_approval_confirm_checkbox.isChecked()
            s['voice_approval_announce'] = \
                self.voice_approval_announce_checkbox.isChecked()
            s['voice_approval_custom_words'] = self._voice_approval_words_dict()
            s['approval_mini_enabled'] = self.approval_mini_checkbox.isChecked()
        except Exception:
            pass

        # Custom system prompt text (the hijack SETTER is queued when changed)
        s['custom_system_prompt'] = self.system_prompt_hijack_input.toPlainText()

        # File-tools history
        s['file_history_enabled'] = self.file_history_checkbox.isChecked()
        s['file_history_git'] = self.file_history_git_checkbox.isChecked()
        s['file_history_keep_days'] = self.file_history_days_combo.currentData() or 14

        # Voice toggles whose live pushes are queued when changed
        s['voice_setup_prompt_enabled'] = self.voice_setup_prompt_checkbox.isChecked()
        s['elevenlabs_enabled'] = self.elevenlabs_tags_checkbox.isChecked()
        s['voice_fillers_enabled'] = self.voice_fillers_checkbox.isChecked()

        # Memory engine
        s['prefilling_enabled'] = self.prefilling_checkbox.isChecked()
        s['prefilling_mode'] = (
            'session' if self.pf_radio_session.isChecked() else 'premade')
        s['prefilling_session_id'] = (self.pf_session_combo.currentData() or '')
        s['memory_enabled'] = self.memory_enabled_checkbox.isChecked()
        s['memory_recall_mode'] = self.memory_recall_mode_combo.currentData()
        s['memory_threshold'] = self.memory_threshold_combo.currentData()
        s['memory_max_results'] = self.memory_max_combo.currentData()
        _cap_sel = self.memory_cap_combo.currentData()
        if _cap_sel == '__custom__':
            try:
                s['memory_inject_cap_tokens'] = max(100, int(self.memory_cap_custom_input.text().strip()))
            except (TypeError, ValueError):
                s['memory_inject_cap_tokens'] = 5000
        else:
            s['memory_inject_cap_tokens'] = _cap_sel
        _mm_sel = self.memory_model_combo.currentData()
        if _mm_sel == '__custom__':
            _mm_custom = self.memory_model_custom_input.text().strip()
            s['memory_embed_model'] = _mm_custom or s.get(
                'memory_embed_model', 'sentence-transformers/all-MiniLM-L6-v2')
        else:
            s['memory_embed_model'] = _mm_sel

        # Theme / bubble / typing reveal (the broadcast is change-gated in
        # _on_settings_saved so an unrelated save never restyles everything).
        s['chat_theme'] = getattr(self, '_selected_theme', 'obsidian_blue')
        s['chat_bubble_style'] = getattr(self, '_selected_bubble_style', 'blend')
        if hasattr(self, 'text_reveal_checkbox'):
            s['chat_text_reveal'] = self.text_reveal_checkbox.isChecked()
        if hasattr(self, 'text_reveal_speed_slider'):
            s['chat_text_reveal_cps'] = int(self.text_reveal_speed_slider.value())

        # Glass background (live apply queued when changed)
        s['glass_background_enabled'] = self.glass_enabled_checkbox.isChecked()
        s['glass_background_opacity'] = self.glass_opacity_slider.value() / 100.0
        s['glass_windows'] = [
            _wkey for _wkey, _cb in getattr(self, 'glass_window_checkboxes', {}).items()
            if _cb.isChecked()]
        if hasattr(self, 'glass_sidebar_checkbox'):
            s['glass_chat_sidebar'] = self.glass_sidebar_checkbox.isChecked()

        # VAD flags (webrtc/silero toggles are read at voice start; the live
        # aggressiveness push is queued when changed)
        s['vad_webrtc_enabled'] = self.webrtc_vad_checkbox.isChecked()
        s['vad_silero_enabled'] = self.silero_vad_checkbox.isChecked()
        s['vad_aggressiveness'] = self.vad_combo.currentData()
        s['vad_silero_threshold'] = self.silero_threshold_combo.currentData()
        # Theme / bubble UI + baseline + "Settings saved" status are applied in
        # _on_settings_saved(), AFTER the off-thread disk write completes.

    def _build_side_effect_queue(self, snap):
        """[(label, fn)] of live set_*() side effects whose value actually
        CHANGED vs the pre-save snapshot ``snap``. Unchanged settings queue
        nothing — a routine save applies little or nothing, which is what keeps
        Save from freezing the app."""
        c = self.controller
        s = c.settings
        q = []

        # AI provider script (module reload — historically the heavy one)
        script_path = self.provider_script_combo.currentData() or ''
        if not script_path:
            if snap.get('ai_provider') != 'manual':
                q.append(('ai_provider=manual', lambda: c.set_ai_provider('manual')))
        else:
            if snap.get('ai_provider') != 'custom_script':
                q.append(('ai_provider=custom_script',
                          lambda: c.set_ai_provider('custom_script')))
            if snap.get('custom_script_path') != script_path:
                q.append(('custom_script_path',
                          lambda p=script_path: c.set_custom_script_path(p)))

        # Debug mode (set_debug_mode is also change-gated internally)
        dbg = self.debug_checkbox.isChecked()
        if bool(snap.get('debug_mode', False)) != dbg:
            q.append(('debug_mode', lambda v=dbg: c.set_debug_mode(v)))

        # Tool lockout + system-prompt hijack/extras
        lock = self.tool_exec_lockout_checkbox.isChecked()
        if bool(snap.get('tool_execution_lockout', False)) != lock:
            q.append(('tool_execution_lockout',
                      lambda v=lock: c.set_tool_execution_lockout(v)))
        hij = self.system_prompt_hijack_checkbox.isChecked()
        hij_txt = self.system_prompt_hijack_input.toPlainText()
        if (bool(snap.get('system_prompt_hijacked', False)) != hij
                or snap.get('custom_system_prompt', '') != hij_txt):
            q.append(('system_prompt_hijack',
                      lambda e=hij, t=hij_txt: c.set_system_prompt_hijack(e, t)))
        extras = (self.include_image_tools_checkbox.isChecked(),
                  self.include_controller_ref_checkbox.isChecked(),
                  self.include_notify_tool_checkbox.isChecked())
        if (bool(snap.get('include_image_tools', False)),
                bool(snap.get('include_controller_ref', False)),
                bool(snap.get('include_notify_tool', False))) != extras:
            q.append(('system_prompt_extras',
                      lambda x=extras: c.set_system_prompt_extras(
                          include_image_tools=x[0], include_controller_ref=x[1],
                          include_notify_tool=x[2])))

        # Voice input device (input only; output uses the system default now)
        input_device = self.input_device_combo.currentData()
        if snap.get('voice_input_device') != input_device:
            q.append(('voice_input_device',
                      lambda v=input_device: c.set_voice_input_device(v)))

        # TTS provider / script
        tts_data = self.tts_provider_combo.currentData() or 'edge-tts'
        if tts_data == 'edge-tts':
            if snap.get('tts_provider', 'edge-tts') != 'edge-tts':
                q.append(('tts_provider=edge-tts',
                          lambda: c.set_tts_provider('edge-tts')))
        else:
            # tts_data is a script path
            if snap.get('tts_provider', 'edge-tts') != 'custom_script':
                q.append(('tts_provider=custom_script',
                          lambda: c.set_tts_provider('custom_script')))
            if snap.get('tts_script_path') != tts_data:
                q.append(('tts_script_path',
                          lambda p=tts_data: c.set_tts_script_path(p)))

        # ElevenLabs speech tags -> engine voice settings
        el = self.elevenlabs_tags_checkbox.isChecked()
        if bool(snap.get('elevenlabs_enabled', False)) != el:
            q.append(('elevenlabs_tags',
                      lambda v=el: c.ai.update_voice_settings(c.ai.voice_mode, v)))

        # Filler interjections (push live to the running handler)
        fill = self.voice_fillers_checkbox.isChecked()
        if bool(snap.get('voice_fillers_enabled', False)) != fill:
            def _apply_fillers(v=fill):
                try:
                    c.voice_handler.fillers_enabled = v
                    if v and c.voice_mode_active:
                        c.voice_handler.ensure_fillers_async()
                except Exception:
                    pass
            q.append(('voice_fillers', _apply_fillers))

        # TTS voice / VAD aggressiveness / interrupt mode / barge-in
        tts_voice = self.tts_voice_combo.currentData()
        if snap.get('tts_voice') != tts_voice:
            q.append(('tts_voice', lambda v=tts_voice: c.set_tts_voice(v)))
        vad_level = self.vad_combo.currentData()
        if snap.get('vad_aggressiveness') != vad_level:
            q.append(('vad_aggressiveness',
                      lambda v=vad_level: c.set_vad_aggressiveness(v)))
        imode = self.interrupt_mode_combo.currentData()
        if snap.get('voice_interrupt_mode') != imode:
            q.append(('voice_interrupt_mode',
                      lambda v=imode: c.set_voice_interrupt_mode(v)))
        bsen = self.bargein_sensitivity_combo.currentData()
        if snap.get('voice_bargein_sensitivity') != bsen:
            q.append(('voice_bargein_sensitivity',
                      lambda v=bsen: c.set_voice_bargein_sensitivity(v)))

        # Glass background — a GUI restyle, so only when its settings changed
        glass_keys = ('glass_background_enabled', 'glass_background_opacity',
                      'glass_windows', 'glass_chat_sidebar')
        if any(snap.get(k) != s.get(k) for k in glass_keys):
            def _apply_glass():
                try:
                    chat_win = getattr(getattr(c, 'ui', None), 'chat_window', None)
                    if chat_win and hasattr(chat_win, 'apply_glass_background'):
                        chat_glass = (s.get('glass_background_enabled')
                                      and 'chat' in (s.get('glass_windows') or []))
                        chat_win.apply_glass_background(
                            bool(chat_glass), s.get('glass_background_opacity', 0.9))
                except Exception:
                    pass
            q.append(('glass_background', _apply_glass))

        # Memory settings — re-inject (or strip) the system-prompt memory block.
        # Cheap no-op when the assembled block is unchanged.
        mem_keys = ('memory_enabled', 'memory_recall_mode', 'memory_inject_cap_tokens')
        if any(snap.get(k) != s.get(k) for k in mem_keys):
            q.append(('memory_settings', lambda: c.refresh_memory_block()))

        # (memory_embed_model has NO live side effect by design — it applies on
        #  the next app start; the Memory tab shows a restart note instead.)

        return q

    def _set_tok_graph_mode(self, mode):
        """Switch the token graph time mode and refresh."""
        self._tok_graph_mode = mode
        for m, btn in self._tok_mode_btns.items():
            btn.setChecked(m == mode)
        self._refresh_tok_graph()

    def _refresh_tok_graph(self):
        """Reload token usage data from disk and repaint the graph."""
        try:
            from systema.common.token_est import get_usage_data, get_output_usage_data
            mode = getattr(self, '_tok_graph_mode', 'Daily')
            data = get_usage_data(mode)
            self._tok_canvas.set_data(data, mode)
            total = sum(v for _, v in data)
            self._tok_summary_lbl.setText(
                f"Input ({mode.lower()}): ~{total:,} tokens  ·  {len(data)} bucket(s)")
            out_data = get_output_usage_data(mode)
            self._tok_out_canvas.set_data(out_data, mode)
            out_total = sum(v for _, v in out_data)
            self._tok_out_summary_lbl.setText(
                f"Output ({mode.lower()}): ~{out_total:,} tokens  ·  {len(out_data)} bucket(s)")
        except Exception:
            pass

    def show_status_message(self, message):
        """Show a temporary status message in the footer."""
        print(f"[Settings] {message}")
        try:
            if hasattr(self, 'footer_status_label'):
                self.footer_status_label.setText(message)
                QTimer.singleShot(3000, lambda: (
                    self.footer_status_label.setText("") if hasattr(self, 'footer_status_label') else None
                ))
        except RuntimeError:
            pass

    def apply_rounded_mask(self):
        """Apply rounded corners mask"""
        from PyQt6.QtGui import QPainterPath
        from PyQt6.QtCore import QRectF

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)

    def showEvent(self, event):
        """Sync container background to the active theme on show."""
        super().showEvent(event)
        try:
            self._refresh_tok_graph()
        except Exception:
            pass
        # Re-read shortcut / autostart state every time the window is shown, so
        # entries created out-of-band (e.g. by setup.py) are detected without an
        # app restart — the status is otherwise only read once at construction.
        try:
            self._refresh_shortcut_status()
        except Exception:
            pass
        try:
            self._refresh_autostart_status()
        except Exception:
            pass
        try:
            p = _theme.current_palette(self.controller)
            self.container.setStyleSheet(f"""
                QWidget#container {{
                    background-color: {p['bg']};
                    border-radius: 12px;
                }}
                QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}
                QScrollArea {{ background-color: {p['surface']}; }}
                QScrollArea > QWidget {{ background-color: {p['surface']}; }}
            """)
        except Exception:
            pass

    def hideEvent(self, event):
        """Release the mic meter + stop the device poll whenever the window is
        hidden/closed, so voice mode isn't blocked by a lingering input stream."""
        try:
            self._stop_mic_meter()
        except Exception:
            pass
        try:
            if hasattr(self, '_device_poll_timer'):
                self._device_poll_timer.stop()
        except Exception:
            pass
        super().hideEvent(event)

    def apply_theme(self, theme_key=None):
        """Live-retint the settings window. Rebuilds the tabbed UI in place from
        the active theme, preserving the current tab and re-loading values."""
        try:
            saved_tab = self._tabs.currentIndex() if hasattr(self, '_tabs') else 0
            old = self.container
            self.layout().removeWidget(old)
            old.deleteLater()

            p = _theme.current_palette(self.controller)
            self.container = QWidget()
            self.container.setObjectName("container")
            self.container.setAutoFillBackground(True)
            self.container.setStyleSheet(f"""
                QWidget#container {{ background-color: {p['bg']}; border-radius: 12px; }}
                QWidget {{ color: {p['text']}; font-family: 'Segoe UI', system-ui, sans-serif; }}
            """)
            self.init_ui()
            self.load_settings()
            self.layout().addWidget(self.container)
            self._wire_dirty_tracking()   # after re-parenting, or findChildren sees nothing
            if hasattr(self, '_tabs') and 0 <= saved_tab < self._tabs.count():
                self._tabs.setCurrentIndex(saved_tab)
            # Repopulate the token graph — the rebuild creates fresh empty canvases
            # and showEvent won't fire (the window is already visible on retint).
            self._refresh_tok_graph()
            self.apply_rounded_mask()
            self._sync_glass()
        except Exception as e:
            print(f"[SettingsWindow.apply_theme] {e}")

