"""
systema/ui/dialogs/approval_notif.py

Compact code-approval notification card — the PyQt6 sibling of the tkinter
startup notice (ui/startup_notif.py): a frameless, always-on-top toast in the
bottom-right corner. Shown INSTEAD of the full CodeApprovalDialog when the chat
window is closed or minimized (setting: approval_mini_enabled).

It shows the DANGERS, not the code: execution type, a findings summary and the
top findings. Buttons: Expand (full dialog) / Deny / Approve. When the scan
found danger-severity findings, Approve becomes "Review to approve" and expands
instead — code with dangers is never one-click approvable blind.

The card owns no decision logic. It polls the underlying CodeApprovalDialog:
voice commands (routed by the controller to dialog.handle_voice_transcript)
set dialog.result or dialog._expand_requested, and the card closes to match.
Opening the chat window while the card is up auto-expands to the full dialog.

Outcome after exec(): self.outcome in ('accept', 'reject', 'expand').
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QWidget)

from systema.security.code_guard import SEV_DANGER, SEV_CAUTION
from systema.ui import theme


class ApprovalNotifCard(QDialog):
    """Minimal approval notification (dangers only, no code)."""

    _W = 472

    def __init__(self, dialog, controller=None, chat_getter=None):
        super().__init__()
        self._dlg = dialog                 # the full CodeApprovalDialog (hidden)
        self._chat_getter = chat_getter    # callable -> chat window or None
        self.outcome = None                # 'accept' | 'reject' | 'expand'

        try:
            self.p = theme.current_palette(controller)
        except Exception:
            self.p = theme.resolve_palette(theme.THEMES[theme.DEFAULT_THEME_KEY])

        findings = list(getattr(dialog, "_last_findings", []) or [])
        self._has_danger = any(f.severity == SEV_DANGER for f in findings)

        self._build(findings)
        self._place_bottom_right()

        # Poll the full dialog: a voice decision, a voice/advanced expand
        # request, or the chat window opening all resolve this card.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._tick)
        self._poll.start(150)

        self._drag_pos = None

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self, findings):
        p = self.p
        accent = p["red"] if self._has_danger else p["accent"]
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setWindowTitle("Code approval required")
        self.setModal(False)
        self.setWindowOpacity(0.98)
        self.setFixedWidth(self._W)
        self.setStyleSheet(f"QDialog {{ background: {p['border']}; }}"
                           f"QWidget {{ font-family: 'Segoe UI', system-ui, sans-serif; }}")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        bar = QFrame()
        bar.setFixedWidth(4)
        bar.setStyleSheet(f"background: {accent};")
        outer.addWidget(bar)

        card = QWidget()
        card.setStyleSheet(f"background: {p['surface']};")
        outer.addWidget(card, stretch=1)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(0)

        hdr = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background: {accent}; border-radius: 4px;")
        hdr.addWidget(dot)
        brand = QLabel("SYSTEMA AUXILIUM")
        brand.setStyleSheet(f"color: {p['muted']}; font-size: 8pt; font-weight: 700;"
                            " background: transparent; padding-left: 6px;")
        hdr.addWidget(brand)
        hdr.addStretch()
        lay.addLayout(hdr)

        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {p['border']};")
        lay.addSpacing(10)
        lay.addWidget(div)
        lay.addSpacing(11)

        title = QLabel("Code approval required")
        title.setStyleSheet(f"color: {p['text']}; font-size: 13pt; font-weight: 700;"
                            " background: transparent;")
        lay.addWidget(title)

        where = ("work environment" if getattr(self._dlg, "execution_type", "")
                 == "work_environment" else "direct execution")
        sub = QLabel(f"The AI wants to run code ({where}). "
                     "Expand to review the code itself.")
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {p['muted']}; font-size: 9pt; background: transparent;")
        lay.addSpacing(5)
        lay.addWidget(sub)

        # Findings summary: counts + top 3 findings, dangers first.
        n_danger = sum(1 for f in findings if f.severity == SEV_DANGER)
        n_caution = sum(1 for f in findings if f.severity == SEV_CAUTION)
        if findings:
            counts = (f"{len(findings)} finding{'s' if len(findings) != 1 else ''}"
                      f"  ({n_danger} danger, {n_caution} caution)")
        else:
            counts = "No risky operations found by the static scan."
        counts_lbl = QLabel(counts)
        counts_lbl.setStyleSheet(
            f"color: {p['red'] if n_danger else p['text']}; font-size: 9pt;"
            " font-weight: 600; background: transparent;")
        lay.addSpacing(8)
        lay.addWidget(counts_lbl)

        sev_color = {SEV_DANGER: p["red"], SEV_CAUTION: p["yellow"]}
        shown = sorted(findings, key=lambda f: 0 if f.severity == SEV_DANGER else 1)[:3]
        for f in shown:
            row = QLabel(f"[{f.severity}] {f.category}  L{f.line}: {f.note}")
            row.setWordWrap(True)
            row.setStyleSheet(
                f"color: {sev_color.get(f.severity, p['muted'])}; font-size: 8pt;"
                " font-family: Consolas, monospace; background: transparent;")
            lay.addSpacing(2)
            lay.addWidget(row)
        if len(findings) > len(shown):
            more = QLabel(f"... and {len(findings) - len(shown)} more - expand to see all.")
            more.setStyleSheet(f"color: {p['muted']}; font-size: 8pt; background: transparent;")
            lay.addSpacing(2)
            lay.addWidget(more)

        foot = QHBoxLayout()
        hint = QLabel("Waiting for your decision")
        hint.setStyleSheet(f"color: {p['muted']}; font-size: 8pt; background: transparent;")
        foot.addWidget(hint)
        foot.addStretch()

        expand_btn = QPushButton("Expand")
        expand_btn.setStyleSheet(self._btn())
        expand_btn.clicked.connect(lambda: self._finish("expand"))
        foot.addWidget(expand_btn)

        deny_btn = QPushButton("Deny")
        deny_btn.setStyleSheet(self._btn(kind="danger"))
        deny_btn.clicked.connect(lambda: self._finish("reject"))
        foot.addWidget(deny_btn)

        if self._has_danger:
            # Danger-severity code is never one-click approvable blind.
            approve_btn = QPushButton("Review to approve")
            approve_btn.setStyleSheet(self._btn(primary=True))
            approve_btn.clicked.connect(lambda: self._finish("expand"))
        else:
            approve_btn = QPushButton("Approve")
            approve_btn.setStyleSheet(self._btn(primary=True))
            approve_btn.clicked.connect(lambda: self._finish("accept"))
        foot.addWidget(approve_btn)

        lay.addSpacing(13)
        lay.addLayout(foot)

    def _btn(self, primary=False, kind="secondary"):
        p = self.p
        if primary:
            bg, fg, border = p["accent"], "#05070a", p["accent"]
        elif kind == "danger":
            bg, fg, border = "transparent", p["red"], p["red"]
        else:
            bg, fg, border = p["surface2"], p["text"], p["border"]
        return (f"QPushButton {{ background: {bg}; color: {fg}; border: 1px solid {border};"
                f" border-radius: 6px; padding: 6px 14px; font-size: 9pt; }}"
                f"QPushButton:hover {{ border: 1px solid {p['accent']}; }}")

    def _place_bottom_right(self):
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 24,
                  geo.bottom() - self.height() - 56)

    # ── resolution ────────────────────────────────────────────────────────────
    def _chat_visible(self):
        try:
            chat = self._chat_getter() if callable(self._chat_getter) else None
            return (chat is not None and chat.isVisible()
                    and not chat.isMinimized())
        except Exception:
            return False

    def _tick(self):
        dlg = self._dlg
        # A voice command already decided on the full dialog.
        if getattr(dlg, "result", None) in ("accept", "reject"):
            self._finish(dlg.result)
            return
        # Voice "expand" / advanced-mode speech asked for the full view.
        if getattr(dlg, "_expand_requested", False):
            self._finish("expand")
            return
        # The chat window opened -> return to the normal code approval.
        if self._chat_visible():
            self._finish("expand")

    def _finish(self, outcome):
        if self.outcome is not None:
            return
        self.outcome = outcome
        self._poll.stop()
        self.accept()

    def closeEvent(self, event):
        # Closing the toast without deciding = expand (never silently deny).
        if self.outcome is None:
            self.outcome = "expand"
        self._poll.stop()
        super().closeEvent(event)

    # ── drag-to-move (frameless) ──────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (event.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)
