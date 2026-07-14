"""
Tests for the inline status / work indicators in systema/ui/chat_window.py

The thinking dots and "Working:" text render inside the input pill's bottom
row via InlineStatus labels that auto-hide when their text is cleared (they no
longer float over the chat). Requires PyQt6 via the `qapp` fixture.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.ui.chat_window import InlineStatus  # noqa: E402


def test_inline_status_hidden_when_empty(qapp):
    lbl = InlineStatus()
    lbl.show()
    lbl.setText("")
    assert not lbl.isVisible()


def test_inline_status_shows_on_text(qapp):
    lbl = InlineStatus()
    lbl.setText("••• Working…")
    assert lbl.isVisible()
    assert lbl.text() == "••• Working…"


def test_inline_status_toggles_with_text(qapp):
    lbl = InlineStatus()
    lbl.setText("thinking")
    assert lbl.isVisible()
    lbl.setText("")
    assert not lbl.isVisible()
    lbl.setText("back")
    assert lbl.isVisible()
