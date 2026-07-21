"""
ui/memory_window.py
Memory Window - UI for managing AI persistent memories
Matches the dark theme and style of existing windows in the project.

Features:
    - View all memories (newest first)
    - Edit a memory inline
    - Delete individual memories
    - Clear all memories with confirmation
    - Shows memory count and ready status
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QTextEdit, QMessageBox, QSizePolicy, QComboBox, QCheckBox, QGroupBox
)
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QRegion
from systema.ui import theme as _theme


# ── Theme palette (rebound per active theme by _refresh_palette) ──────────────
_BG       = "#0D1117"
_SURFACE  = "#161B22"
_SURFACE2 = "#21262D"
_BORDER   = "#30363D"
_ACCENT   = "#58A6FF"
_TEXT     = "#E6EDF3"
_MUTED    = "#8B949E"
_GREEN    = "#3FB950"
_RED      = "#F28B82"

_COMBO_STYLE = ""  # rebuilt by _refresh_palette()


def _refresh_palette(controller):
    """Rebind module colour constants + composed styles to the active theme.
    Styling reads these globals at call time, so refreshing before (re)building
    retints the whole window."""
    global _BG, _SURFACE, _SURFACE2, _BORDER, _ACCENT, _TEXT, _MUTED, _GREEN, _RED
    global _COMBO_STYLE
    p = _theme.current_palette(controller)
    _BG, _SURFACE, _SURFACE2 = p['bg'], p['surface'], p['surface2']
    _BORDER, _ACCENT = p['border'], p['accent']
    _TEXT, _MUTED = p['text'], p['muted']
    _GREEN, _RED = p['green'], p['red']
    _COMBO_STYLE = f"""
        QComboBox {{
            background-color: {_BG};
            border: 1px solid {_theme.rgba(_ACCENT, 0.18)};
            border-radius: 5px;
            color: {_TEXT};
            padding: 4px 8px;
            font-size: 12px;
            min-width: 200px;
        }}
        QComboBox:hover {{ border-color: {_theme.rgba(_ACCENT, 0.45)}; }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView {{
            background-color: {_SURFACE};
            border: 1px solid {_theme.rgba(_ACCENT, 0.18)};
            color: {_TEXT};
            selection-background-color: {_SURFACE2};
        }}
    """
# ─────────────────────────────────────────────────────────────────────────────


class MemoryWindow(QWidget):
    """Window for viewing and managing AI memories."""

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.memory_manager = controller.memory_manager
        _refresh_palette(controller)   # match active theme before building UI

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowTitle("Memory Manager")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(520, 450)
        self.resize(580, 680)

        # Drag / resize state
        self.dragging = False
        self.drag_position = QPoint()
        self.resizing = False
        self.resize_edge = None
        self.resize_start_geometry = None
        self.resize_start_pos = None

        # Container
        self.container = QWidget()
        self.container.setObjectName("container")
        self.container.setAutoFillBackground(True)
        self.container.setStyleSheet(f"""
            QWidget#container {{
                background-color: {_SURFACE};
                border-radius: 12px;
            }}
            QWidget {{
                color: {_TEXT};
                font-family: 'Segoe UI', -apple-system, system-ui, sans-serif;
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {_SURFACE2}; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {_BORDER}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addWidget(self.container)

        self._build_ui()
        self._apply_rounded_mask()
        self._create_resize_handles()
        self.refresh_memories()
        self._sync_glass()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._make_header())
        layout.addWidget(self._make_status_bar())
        layout.addWidget(self._make_memory_list_area(), stretch=1)
        layout.addWidget(self._make_footer())

    def _make_header(self):
        header = QFrame()
        header.setFixedHeight(54)
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
                border-bottom: 1px solid {_theme.rgba(_ACCENT, 0.18)};
            }}
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 12, 0)
        hl.setSpacing(8)

        # Traffic-light dots
        for _col in ("#FF5F57", "#FEBC2E", "#28C840"):
            dot = QFrame()
            dot.setFixedSize(11, 11)
            dot.setStyleSheet(f"QFrame {{ background: {_col}; border-radius: 5px; border: none; }}")
            hl.addWidget(dot)

        hl.addSpacing(10)

        title = QLabel("Memory Manager")
        title.setStyleSheet(f"""
            font-size: 13px; font-weight: 600;
            color: {_TEXT}; background: transparent; border: none;
        """)

        from systema.ui.widgets.painted_icons import CloseButton
        close_btn = CloseButton(28, tooltip="Close", pill=True)
        close_btn.clicked.connect(self.close)

        hl.addWidget(title)
        hl.addStretch()
        hl.addWidget(close_btn)

        header.mousePressEvent   = self._header_press
        header.mouseMoveEvent    = self._header_move
        header.mouseReleaseEvent = self._header_release
        return header

    def _make_status_bar(self):
        bar = QFrame()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"""
            QFrame {{ background-color: {_BG}; border-bottom: 1px solid {_theme.rgba(_ACCENT, 0.12)}; }}
        """)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 0, 16, 0)

        self.ready_dot = QLabel("●")
        self.ready_dot.setStyleSheet("font-size: 9px; background: transparent;")

        self.status_label = QLabel()
        self.status_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")

        hl.addWidget(self.ready_dot)
        hl.addSpacing(6)
        hl.addWidget(self.status_label)
        hl.addStretch()
        return bar

    def _make_memory_list_area(self):
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.list_container = QWidget()
        self.list_container.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(12, 12, 12, 12)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch()

        self.scroll_area.setWidget(self.list_container)
        return self.scroll_area

    def _make_footer(self):
        footer = QFrame()
        footer.setFixedHeight(56)
        footer.setStyleSheet(f"""
            QFrame {{
                background-color: {_BG};
                border-top: 1px solid {_theme.rgba(_ACCENT, 0.12)};
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
            }}
        """)
        hl = QHBoxLayout(footer)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(8)

        refresh_btn = self._make_btn("↻  Refresh", False)
        refresh_btn.clicked.connect(self.refresh_memories)

        clear_btn = self._make_btn("🗑  Clear All", True)
        clear_btn.clicked.connect(self._confirm_clear_all)

        hl.addWidget(refresh_btn)
        hl.addStretch()
        hl.addWidget(clear_btn)
        return footer

    def _make_btn(self, text, danger=False):
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if danger:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {_theme.rgba(_RED, 0.35)};
                    border-radius: 6px; color: {_RED};
                    font-size: 12px; padding: 6px 14px;
                }}
                QPushButton:hover {{
                    background: {_theme.rgba(_RED, 0.1)};
                    border-color: {_RED};
                }}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {_BORDER};
                    border-radius: 6px; color: {_MUTED};
                    font-size: 12px; padding: 6px 14px;
                }}
                QPushButton:hover {{
                    background: {_theme.rgba(_ACCENT, 0.08)};
                    border-color: {_theme.rgba(_ACCENT, 0.35)};
                    color: {_TEXT};
                }}
            """)
        return btn

    # ── Memory Cards ──────────────────────────────────────────────────────────

    def refresh_memories(self):
        """Reload all memories and rebuild the card list."""
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.memory_manager or not self.memory_manager.is_ready:
            self._update_status(ready=False, count=0)
            reason = getattr(self.memory_manager, '_unavailable_reason', '') if self.memory_manager else ''
            msg = f"⚠ Memory system unavailable.\n{reason}" if reason else "⚠ Memory system unavailable.\nRun: pip install chromadb fastembed"
            self._show_empty(msg)
            return

        memories = self.memory_manager.get_all()
        self._update_status(ready=True, count=len(memories))

        if not memories:
            self._show_empty("No memories yet.\nThe AI will store memories automatically as you chat.")
            return

        for mem in memories:
            self.list_layout.insertWidget(self.list_layout.count() - 1, self._make_card(mem))

    def _update_status(self, ready: bool, count: int):
        if ready:
            self.ready_dot.setStyleSheet(f"font-size: 10px; color: {_GREEN}; background: transparent;")
            self.status_label.setText(f"{count} {'memory' if count == 1 else 'memories'} stored")
        else:
            self.ready_dot.setStyleSheet(f"font-size: 10px; color: {_RED}; background: transparent;")
            self.status_label.setText("Memory system unavailable")

    def _show_empty(self, message: str):
        lbl = QLabel(message)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {_MUTED}; font-size: 13px; background: transparent; padding: 40px;")
        lbl.setWordWrap(True)
        self.list_layout.insertWidget(0, lbl)

    def _make_card(self, mem: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE2};
                border: 1px solid {_theme.rgba(_ACCENT, 0.12)};
                border-radius: 8px;
            }}
            QFrame:hover {{ border-color: {_theme.rgba(_ACCENT, 0.28)}; }}
        """)

        vl = QVBoxLayout(card)
        vl.setContentsMargins(12, 10, 12, 10)
        vl.setSpacing(6)

        text_edit = QTextEdit()
        text_edit.setPlainText(mem["text"])
        text_edit.setReadOnly(True)
        text_edit.setFixedHeight(72)
        text_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background: transparent; border: none;
                color: {_TEXT}; font-size: 13px;
                selection-background-color: {_theme.rgba(_ACCENT, 0.25)};
            }}
        """)

        date_str = mem.get("created_at", "")
        if date_str:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(date_str)
                date_str = dt.strftime("%b %d, %Y %I:%M %p")
            except Exception:
                pass
        edited_tag = "  ✏ edited" if mem.get("edited") else ""
        date_lbl = QLabel(f"📅 {date_str}{edited_tag}")
        date_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")

        edit_btn   = QPushButton("Edit")
        save_btn   = QPushButton("Save")
        delete_btn = QPushButton("Delete")
        save_btn.setVisible(False)

        for btn in (edit_btn, save_btn, delete_btn):
            btn.setFixedHeight(24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        edit_btn.setStyleSheet(f"""
            QPushButton {{ background: {_theme.rgba(_GREEN, 0.08)}; border: 1px solid {_theme.rgba(_GREEN, 0.25)};
                border-radius: 4px; color: {_GREEN}; font-size: 11px; padding: 0 10px; }}
            QPushButton:hover {{ background: {_theme.rgba(_GREEN, 0.16)}; border-color: {_GREEN}; }}
        """)
        save_btn.setStyleSheet(f"""
            QPushButton {{ background: {_theme.rgba(_ACCENT, 0.08)}; border: 1px solid {_theme.rgba(_ACCENT, 0.25)};
                border-radius: 4px; color: {_ACCENT}; font-size: 11px; padding: 0 10px; }}
            QPushButton:hover {{ background: {_theme.rgba(_ACCENT, 0.16)}; border-color: {_ACCENT}; }}
        """)
        delete_btn.setStyleSheet(f"""
            QPushButton {{ background: {_theme.rgba(_RED, 0.06)}; border: 1px solid {_theme.rgba(_RED, 0.22)};
                border-radius: 4px; color: {_RED}; font-size: 11px; padding: 0 10px; }}
            QPushButton:hover {{ background: {_theme.rgba(_RED, 0.14)}; border-color: {_RED}; }}
        """)

        mem_id = mem["id"]

        def on_edit():
            text_edit.setReadOnly(False)
            text_edit.setStyleSheet(f"""
                QTextEdit {{
                    background: {_BG}; border: 1px solid {_theme.rgba(_ACCENT, 0.40)};
                    border-radius: 4px; color: {_TEXT}; font-size: 13px;
                }}
            """)
            text_edit.setFocus()
            edit_btn.setVisible(False)
            save_btn.setVisible(True)

        def on_save():
            new_text = text_edit.toPlainText().strip()
            if new_text and self.memory_manager:
                if self.memory_manager.update(mem_id, new_text):
                    text_edit.setReadOnly(True)
                    text_edit.setStyleSheet(f"""
                        QTextEdit {{
                            background: transparent; border: none;
                            color: {_TEXT}; font-size: 13px;
                        }}
                    """)
                    save_btn.setVisible(False)
                    edit_btn.setVisible(True)
                    if "✏ edited" not in date_lbl.text():
                        date_lbl.setText(date_lbl.text() + "  ✏ edited")
                else:
                    self._flash_error("Failed to save memory.")
            else:
                self._flash_error("Memory text cannot be empty.")

        def on_delete():
            if self.memory_manager:
                self.memory_manager.delete(mem_id)
            card.setParent(None)
            card.deleteLater()
            self._decrement_count()

        edit_btn.clicked.connect(on_edit)
        save_btn.clicked.connect(on_save)
        delete_btn.clicked.connect(on_delete)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch()
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)

        vl.addWidget(text_edit)
        vl.addWidget(date_lbl)
        vl.addLayout(btn_row)
        return card

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _decrement_count(self):
        count = self.memory_manager.count() if self.memory_manager else 0
        self._update_status(ready=True, count=count)
        if count == 0:
            self._show_empty("No memories yet.\nThe AI will store memories automatically as you chat.")

    def _confirm_clear_all(self):
        count = self.memory_manager.count() if self.memory_manager else 0
        if count == 0:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Clear All Memories")
        dialog.setText(f"Delete all {count} {'memory' if count == 1 else 'memories'}?\n\nThis cannot be undone.")
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setStyleSheet(f"""
            QMessageBox {{ background-color: {_SURFACE}; color: {_TEXT}; }}
            QPushButton {{
                background: {_SURFACE2}; border: 1px solid {_BORDER};
                border-radius: 5px; color: {_TEXT};
                padding: 6px 16px; min-width: 70px;
            }}
            QPushButton:hover {{ background: {_theme.lighten(_SURFACE2, 0.1)}; }}
        """)
        if dialog.exec() == QMessageBox.StandardButton.Yes:
            if self.memory_manager:
                self.memory_manager.clear()
            self.refresh_memories()

    def _flash_error(self, msg: str):
        original_text  = self.status_label.text()
        original_style = self.status_label.styleSheet()
        self.status_label.setStyleSheet("color: #ff6b6b; font-size: 12px; background: transparent;")
        self.status_label.setText(f"⚠ {msg}")
        QTimer.singleShot(3000, lambda: (
            self.status_label.setStyleSheet(original_style),
            self.status_label.setText(original_text),
        ))

    # ── Settings section (used by settings_window.py) ─────────────────────────

    @staticmethod
    def make_settings_section(controller, open_callback):
        """
        Returns a QGroupBox with memory settings controls.
        Call this from settings_window.py instead of duplicating the widget code.

        Usage in settings_window.py:
            from systema.ui.windows.memory_window import MemoryWindow
            section, refs = MemoryWindow.make_settings_section(
                self.controller, self._open_memory_window
            )
            # refs keys: 'enabled_checkbox', 'threshold_combo', 'max_combo'
        """
        _refresh_palette(controller)
        group = QGroupBox("🧠 Memory")
        group.setStyleSheet(f"""
            QGroupBox {{
                color: {_TEXT}; border: 1px solid {_BORDER}; border-radius: 8px;
                margin-top: 8px; padding-top: 8px; font-weight: 600;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}
        """)
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        enabled_cb = QCheckBox("Enable persistent memory (RAG)")
        enabled_cb.setStyleSheet(f"color: {_TEXT};")
        enabled_cb.setChecked(controller.settings.get('memory_enabled', True))
        layout.addWidget(enabled_cb)

        # Threshold row
        thr_row = QHBoxLayout()
        thr_lbl = QLabel("Similarity threshold:")
        thr_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        thr_combo = QComboBox()
        for label, val in [
            ("0.3 – More memories (less strict)", 0.3),
            ("0.4 – Balanced (default)",          0.4),
            ("0.5 – Fewer memories (stricter)",   0.5),
            ("0.6 – Very strict",                 0.6),
            ("0.7 – Highly relevant only",        0.7),
        ]:
            thr_combo.addItem(label, val)
        thr_combo.setStyleSheet(_COMBO_STYLE)
        current_thr = controller.settings.get('memory_threshold', 0.4)
        for i in range(thr_combo.count()):
            if thr_combo.itemData(i) == current_thr:
                thr_combo.setCurrentIndex(i)
                break
        thr_row.addWidget(thr_lbl)
        thr_row.addWidget(thr_combo)
        layout.addLayout(thr_row)

        # Max results row
        max_row = QHBoxLayout()
        max_lbl = QLabel("Max memories per message:")
        max_lbl.setStyleSheet(f"color: {_MUTED}; font-size: 12px;")
        max_combo = QComboBox()
        for n in [3, 5, 8, 10, 15]:
            max_combo.addItem(str(n), n)
        max_combo.setStyleSheet(_COMBO_STYLE)
        current_max = controller.settings.get('memory_max_results', 5)
        for i in range(max_combo.count()):
            if max_combo.itemData(i) == current_max:
                max_combo.setCurrentIndex(i)
                break
        max_row.addWidget(max_lbl)
        max_row.addWidget(max_combo)
        layout.addLayout(max_row)

        # Open manager button
        open_btn = QPushButton("Open Memory Manager →")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {_theme.rgba(_ACCENT, 0.08)}; border: 1px solid {_theme.rgba(_ACCENT, 0.30)};
                border-radius: 6px; color: {_ACCENT};
                font-size: 12px; padding: 6px 12px;
            }}
            QPushButton:hover {{ background: {_theme.rgba(_ACCENT, 0.16)}; }}
        """)
        open_btn.clicked.connect(open_callback)
        layout.addWidget(open_btn)

        refs = {
            'enabled_checkbox': enabled_cb,
            'threshold_combo':  thr_combo,
            'max_combo':        max_combo,
        }
        return group, refs

    def apply_theme(self, theme_key=None):
        """Live-retint the memory window. Refreshes the palette and rebuilds the
        container in place."""
        try:
            _refresh_palette(self.controller)
            old = self.container
            self.layout().removeWidget(old)
            old.deleteLater()
            self.container = QWidget()
            self.container.setObjectName("container")
            self.container.setAutoFillBackground(True)
            self.container.setStyleSheet(f"""
                QWidget#container {{ background-color: {_SURFACE}; border-radius: 12px; }}
                QWidget {{ color: {_TEXT}; }}
            """)
            self._build_ui()
            self.layout().addWidget(self.container)
            self.refresh_memories()
            self._apply_rounded_mask()
            self._sync_glass()
        except Exception as e:
            print(f"[MemoryWindow.apply_theme] {e}")

    def _sync_glass(self):
        """Overlay a translucent backdrop when glass mode is on (cards stay solid)."""
        try:
            if not _theme.glass_enabled_for(self.controller, 'memory'):
                return
            _, op = _theme.glass_state(self.controller)
            bd = _theme.glass_backdrop(op)
            self.container.setStyleSheet(
                f"QWidget#container {{ background-color: {bd}; border-radius: 12px; }}"
                f"QWidget {{ color: {_TEXT}; }}"
            )
            if hasattr(self, 'scroll_area'):
                self.scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        except Exception as e:
            print(f"[MemoryWindow._sync_glass] {e}")

    # ── Drag ──────────────────────────────────────────────────────────────────

    def _header_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _header_move(self, event):
        if self.dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)

    def _header_release(self, event):
        self.dragging = False

    # ── Rounded corners + resize ──────────────────────────────────────────────

    def _apply_rounded_mask(self):
        region = QRegion(self.rect(), QRegion.RegionType.Rectangle)
        self.setMask(region)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_mask()
        if hasattr(self, 'resize_handles'):
            self._position_resize_handles()

    def _create_resize_handles(self):
        self.resize_handles = {}
        edges = {
            'top': Qt.CursorShape.SizeVerCursor, 'bottom': Qt.CursorShape.SizeVerCursor,
            'left': Qt.CursorShape.SizeHorCursor, 'right': Qt.CursorShape.SizeHorCursor,
            'top-left': Qt.CursorShape.SizeFDiagCursor, 'top-right': Qt.CursorShape.SizeBDiagCursor,
            'bottom-left': Qt.CursorShape.SizeBDiagCursor, 'bottom-right': Qt.CursorShape.SizeFDiagCursor,
        }
        for name, cursor in edges.items():
            h = QFrame(self)
            h.setStyleSheet("background: transparent;")
            h.setCursor(cursor)
            h.edge_type = name
            h.installEventFilter(self)
            self.resize_handles[name] = h
            h.raise_()
        self._position_resize_handles()

    def _position_resize_handles(self):
        w, h, hs, cs, hh = self.width(), self.height(), 8, 16, 52
        self.resize_handles['top'].setGeometry(cs, hh, w - 2*cs, hs)
        self.resize_handles['bottom'].setGeometry(cs, h - hs, w - 2*cs, hs)
        self.resize_handles['left'].setGeometry(0, cs, hs, h - 2*cs)
        self.resize_handles['right'].setGeometry(w - hs, cs, hs, h - 2*cs)
        self.resize_handles['top-left'].setGeometry(0, hh, cs, cs)
        self.resize_handles['top-right'].setGeometry(w - cs, hh, cs, cs)
        self.resize_handles['bottom-left'].setGeometry(0, h - cs, cs, cs)
        self.resize_handles['bottom-right'].setGeometry(w - cs, h - cs, cs, cs)

    def eventFilter(self, obj, event):
        if hasattr(obj, 'edge_type'):
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.resizing = True
                self.resize_edge = obj.edge_type
                self.resize_start_geometry = self.geometry()
                self.resize_start_pos = event.globalPosition().toPoint()
                return True
            elif event.type() == event.Type.MouseButtonRelease and self.resizing:
                self.resizing = False
                return True
            elif event.type() == event.Type.MouseMove and self.resizing:
                delta = event.globalPosition().toPoint() - self.resize_start_pos
                geo = QRect(self.resize_start_geometry)
                if 'left'   in self.resize_edge: geo.setLeft(self.resize_start_geometry.left() + delta.x())
                if 'right'  in self.resize_edge: geo.setRight(self.resize_start_geometry.right() + delta.x())
                if 'top'    in self.resize_edge: geo.setTop(self.resize_start_geometry.top() + delta.y())
                if 'bottom' in self.resize_edge: geo.setBottom(self.resize_start_geometry.bottom() + delta.y())
                if geo.width() >= self.minimumWidth() and geo.height() >= self.minimumHeight():
                    self.setGeometry(geo)
                return True
        return super().eventFilter(obj, event)