"""
ui/dialogs/dialog_utils.py
Shared dialog placement helpers.

Every custom dialog used to center on ``self.parent()``. When that parent was
the small always-on-top floating window, the dialog landed on top of it and got
stuck under/over the tiny widget — sometimes impossible to interact with. The
rule now: center on the CHAT window (the large one the user is actually looking
at), or, if it isn't open, on the middle of its screen — and ALWAYS clamp the
result into the screen's available area, so a dialog bigger than a corner-parked
chat window can never open truncated off-screen.
"""

from PyQt6.QtWidgets import QApplication


def _find_chat_window():
    """Return the visible ChatWindow top-level, or None.

    Matched by class name rather than import to avoid a circular import
    (chat_window imports plenty of ui modules)."""
    try:
        for w in QApplication.topLevelWidgets():
            if type(w).__name__ == "ChatWindow" and w.isVisible():
                return w
    except Exception:
        pass
    return None


def center_on_primary(widget):
    """Center ``widget`` on the chat window if it's open, else on the screen —
    clamped so the dialog is always FULLY visible on the screen that contains
    the anchor. Safe to call from a ``QTimer.singleShot(0, ...)`` right after
    the dialog is constructed (so its final size is known). Never raises."""
    try:
        anchor = _find_chat_window()

        # The screen that actually contains the anchor (multi-monitor: clamp to
        # the display the chat lives on, not blindly the primary).
        screen = None
        if anchor is not None:
            screen = anchor.screen() or (anchor.windowHandle()
                                         and anchor.windowHandle().screen())
        if screen is None:
            screen = widget.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()

        # A dialog larger than the screen shrinks to fit first (Qt clamps the
        # resize to minimumSize; fixed-size dialogs are unaffected).
        fg = widget.frameGeometry()
        frame_w = fg.width() - widget.width()
        frame_h = fg.height() - widget.height()
        if fg.width() > avail.width() or fg.height() > avail.height():
            widget.resize(min(widget.width(), avail.width() - frame_w - 8),
                          min(widget.height(), avail.height() - frame_h - 8))
            fg = widget.frameGeometry()

        target = anchor.frameGeometry().center() if anchor is not None else avail.center()
        fg.moveCenter(target)

        # Clamp fully inside the available area.
        fg.moveLeft(max(avail.left(), min(fg.left(), avail.right() - fg.width() + 1)))
        fg.moveTop(max(avail.top(), min(fg.top(), avail.bottom() - fg.height() + 1)))

        widget.move(fg.topLeft())
    except Exception:
        pass
