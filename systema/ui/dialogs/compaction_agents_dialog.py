"""
systema/ui/dialogs/compaction_agents_dialog.py
Active compaction agents — lists running per-session compaction jobs (session ·
progress · Stop). Background jobs survive session switches; Stop keeps the
progress made so far. Auto-refreshes off CompactionManager.changed + a poll.
"""
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QScrollArea, QWidget)
from PyQt6.QtCore import Qt, QTimer


class CompactionAgentsDialog(QDialog):
    def __init__(self, chat_window, manager):
        super().__init__(chat_window)
        self._chat = chat_window
        self._mgr = manager
        t = chat_window._t()
        self.setWindowTitle("Compaction agents")
        self.setMinimumSize(460, 260)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['surface']}; border: 1px solid {t['border']}; }}
            QLabel {{ color: #E8EAED; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QLabel("<b>Compaction agents</b><br>"
                      "<span style='color:#8B949E;font-size:11px;'>"
                      "Background '[Compacted]' summarization jobs — they survive session "
                      "switches. Stop keeps the progress made so far.</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border: none; background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 8px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._inner = QWidget()
        self._inner.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(self._inner)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(6)
        scroll.setWidget(self._inner)
        lay.addWidget(scroll, stretch=1)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {t['border']};
                border-radius: 6px; padding: 7px 18px; color: #8B949E; }}
            QPushButton:hover {{ color: #E6EDF3; border-color: {t['accent']}; }}
        """)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        lay.addLayout(close_row)

        try:
            manager.changed.connect(self.refresh)
        except Exception:
            pass
        # Poll too — the worker mutates its progress counts between signals.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(700)
        self.refresh()

    def _clear_rows(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def refresh(self):
        try:
            t = self._chat._t()
        except Exception:
            return
        self._clear_rows()
        try:
            jobs = self._mgr.active()
        except Exception:
            jobs = []
        if not jobs:
            empty = QLabel("No compaction agents are running.")
            empty.setStyleSheet("color: #8B949E; font-size: 12px;")
            self._rows.addWidget(empty)
            self._rows.addStretch()
            return
        for sid, name, done, total in jobs:
            self._rows.addWidget(self._build_row(sid, name, done, total, t))
        self._rows.addStretch()

    def _build_row(self, sid, name, done, total, t):
        row = QFrame()
        row.setStyleSheet(f"""
            QFrame {{ background: {t['elevated']}; border: 1px solid {t['border']};
                      border-radius: 8px; }}
        """)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(12, 8, 10, 8)
        rl.setSpacing(8)

        pct = int((done / total) * 100) if total else 0
        info = QLabel(
            f"<span style='font-size:12px;font-weight:600;'>{name}</span>"
            f"<br><span style='font-size:10px;color:#8B949E;'>"
            f"{done}/{total} outputs · {pct}%</span>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet("background: transparent; border: none;")
        info.setWordWrap(True)
        rl.addWidget(info, stretch=1)

        stop_btn = QPushButton("Stop")
        stop_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: 1px solid {t['border']};
                border-radius: 5px; color: #C9D1D9; font-size: 11px; padding: 3px 12px; }}
            QPushButton:hover {{ border-color: #E06060; color: #E06060; }}
        """)
        stop_btn.clicked.connect(lambda _=False, s=sid: (self._mgr.stop(s), self.refresh()))
        rl.addWidget(stop_btn)
        return row
