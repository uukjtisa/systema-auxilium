"""
systema/ui/dialogs/image_attachments_dialog.py
Images attached this session — context manager (chat ⋯ menu).

One place to see every picture the conversation holds and what it is costing,
with the same two-level detach the bubbles offer:

  * Detach / Attach — a TWIN button per row. Reversible: the picture stays in
    the transcript and on disk, and the model is told in one line that it can
    no longer see it.
  * Delete — permanent. Removes it from the transcript and the cache; its
    number is retired and never reissued.

Modelled on SessionFilesDialog (same shell, same theming hook, same
scroll-of-cards layout) so the two session tools look like siblings.
"""

import os

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from systema.common.logger import _make_logger

log = _make_logger("ImageAttachmentsDialog")


class ImageAttachmentsDialog(QDialog):
    def __init__(self, chat_window):
        super().__init__(chat_window)
        self._chat = chat_window
        t = chat_window._t()
        self.setWindowTitle("Images attached this session")
        self.setMinimumSize(600, 420)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {t['surface']}; border: 1px solid {t['border']}; }}
            QLabel {{ color: #E8EAED; }}
        """)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(16, 14, 16, 14)
        self._lay.setSpacing(10)
        self._build()

    # ── building ─────────────────────────────────────────────────────────────

    def _btn(self, text, tooltip, danger=False):
        t = self._chat._t()
        b = QPushButton(text)
        b.setToolTip(tooltip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        hover = ("rgba(234,67,53,0.25)" if danger
                 else "rgba(255,255,255,0.10)")
        fg = "#EA4335" if danger else "#C9D1D9"
        b.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.05);
                border: 1px solid {t['border']};
                border-radius: 6px; color: {fg};
                font-size: 11px; padding: 5px 12px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:disabled {{ color: #4A5058; border-color: #2A2E35; }}
        """)
        return b

    def _clear(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build(self):
        from systema.common.token_est import estimate_refs_tokens
        self._clear()
        refs = self._chat.session_image_refs()
        live = [r for r in refs if r.get('attached')]

        head = QLabel(
            "<b>Images attached this session</b>"
            "<br><span style='color:#8B949E;font-size:11px;'>"
            "Detach removes a picture from the assistant's context but keeps it "
            "here, reversibly. Delete removes it from the chat and the cache "
            "for good — its number is never reused."
            "</span>")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setWordWrap(True)
        self._lay.addWidget(head)

        # ── Top row: the three bulk actions ──────────────────────────────────
        top = QWidget()
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(8)

        b_detach_all = self._btn("Detach all", "Take every image out of context "
                                               "(reversible)")
        b_attach_all = self._btn("Attach all", "Put every image back in context")
        b_delete_all = self._btn("Delete all", "Permanently delete every image "
                                               "from the chat and the cache",
                                 danger=True)
        b_detach_all.clicked.connect(lambda: self._bulk(False))
        b_attach_all.clicked.connect(lambda: self._bulk(True))
        b_delete_all.clicked.connect(self._delete_all)
        b_detach_all.setEnabled(bool(live))
        b_attach_all.setEnabled(len(live) < len(refs))
        b_delete_all.setEnabled(bool(refs))
        for b in (b_detach_all, b_attach_all, b_delete_all):
            top_lay.addWidget(b)
        top_lay.addStretch()

        cost = estimate_refs_tokens(refs)
        tally = QLabel(f"{len(live)} of {len(refs)} in context · ~{cost:,} "
                       f"image tokens per request")
        tally.setStyleSheet("color:#8B949E; font-size:11px; background:transparent;")
        top_lay.addWidget(tally)
        self._lay.addWidget(top)

        # ── Card list ────────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)

        if not refs:
            empty = QLabel("No images in this session yet.")
            empty.setStyleSheet("color:#8B949E; font-size:12px; padding:24px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(empty)
        else:
            for ref in refs:
                col.addWidget(self._card(ref))
        col.addStretch()

        try:
            from systema.ui.widgets.smooth_scroll import install_smooth_scroll
            install_smooth_scroll(scroll)
        except Exception:
            pass

        scroll.setWidget(holder)
        self._lay.addWidget(scroll, stretch=1)
        self._scroll = scroll

    def _card(self, ref):
        from systema.common import image_cache
        from systema.common.token_est import estimate_image_tokens

        t = self._chat._t()
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {t['base']}; border: 1px solid {t['border']}; "
            f"border-radius: 8px; }}")
        row = QHBoxLayout(card)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(12)

        attached = bool(ref.get('attached'))
        exists = image_cache.exists(ref)

        thumb = QLabel()
        thumb.setFixedSize(56, 56)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("border: none; background: transparent; "
                            "color: #8B949E; font-size: 9px;")
        if exists:
            pm = QPixmap(ref.get('path', ''))
            if not pm.isNull():
                thumb.setPixmap(pm.scaled(
                    56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                thumb.setText("no\npreview")
        else:
            thumb.setText("missing")
        row.addWidget(thumb)

        w, h = ref.get('w') or 0, ref.get('h') or 0
        dims = f"{w}x{h}" if w and h else "size unknown"
        if not exists:
            state = "file missing from cache"
        elif attached:
            state = f"in context · ~{estimate_image_tokens(w, h):,} tokens/request"
        else:
            state = "detached from context"

        info = QLabel(
            f"<b>Image {ref.get('n')}</b> · {os.path.basename(ref.get('name') or '')}"
            f"<br><span style='color:#8B949E;font-size:11px;'>{dims} · {state}"
            f" · {'assistant' if ref.get('origin') == 'agent' else 'you'}</span>")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("border: none; background: transparent;")
        row.addWidget(info, stretch=1)

        n = ref.get('n')
        twin = self._btn("Detach" if attached else "Attach",
                         "Take this image out of the assistant's context"
                         if attached else "Put this image back in context")
        twin.clicked.connect(
            (lambda _=False, k=n: self._one(k, False)) if attached
            else (lambda _=False, k=n: self._one(k, True)))
        row.addWidget(twin)

        dele = self._btn("Delete", "Permanently delete this image from the chat "
                                   "and the cache", danger=True)
        dele.clicked.connect(lambda _=False, k=n: self._delete_one(k))
        row.addWidget(dele)
        return card

    # ── actions ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        """Rebuild the card list WITHOUT losing the reader's place.

        Every action here changes more than one row (the tally, the enabled
        state of the bulk buttons, the twin button's own label), so this list is
        rebuilt rather than patched. That is fine — it is a short list in its
        own window — but a plain _build() scrolled back to the top, so acting on
        the twelfth image threw you back to the first. Same complaint the chat
        transcript had; same answer.
        """
        keep = 0
        old = getattr(self, '_scroll', None)
        try:
            if old is not None:
                keep = old.verticalScrollBar().value()
        except (RuntimeError, AttributeError):
            keep = 0

        self._build()

        def _restore():
            try:
                bar = self._scroll.verticalScrollBar()
                bar.setValue(min(keep, bar.maximum()))
            except (RuntimeError, AttributeError):
                pass

        _restore()
        QTimer.singleShot(0, _restore)   # again, once the cards have laid out

    def _one(self, n, attach: bool):
        if attach:
            self._chat.reattach_image(n)
        else:
            self._chat.detach_image(n)
        self._rebuild()

    def _bulk(self, attach: bool):
        self._chat.set_all_images_attached(attach)
        self._rebuild()

    def _delete_one(self, n):
        self._chat.delete_image(n)
        self._rebuild()

    def _delete_all(self):
        self._chat.delete_all_images()
        self._rebuild()
