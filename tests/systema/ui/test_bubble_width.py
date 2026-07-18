"""
Tests for ChatWindow._bubble_max_width (systema/ui/chat_window.py).

A message bubble must never be wider than the chat viewport — otherwise a
narrow window pushes it off-screen. Binds the real method onto a stub holding a
real scroll-area viewport of a chosen width.
"""
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QScrollArea, QWidget  # noqa: E402

from systema.ui.chat_window import ChatWindow  # noqa: E402


def _stub_with_viewport(qapp, width):
    scroll = QScrollArea()
    scroll.setWidget(QWidget())
    scroll.resize(width, 400)
    scroll.show()
    qapp.processEvents()
    stub = types.SimpleNamespace(chat_scroll_area=scroll, chat_zoom=1.0,
                                 _bubble_style=lambda: 'blend')
    stub._bubble_max_width = types.MethodType(ChatWindow._bubble_max_width, stub)
    return stub, scroll.viewport().width()


def test_bubble_never_exceeds_narrow_viewport(qapp):
    stub, vw = _stub_with_viewport(qapp, 280)
    assert stub._bubble_max_width() <= vw


def test_bubble_never_exceeds_wide_viewport(qapp):
    stub, vw = _stub_with_viewport(qapp, 1600)
    assert stub._bubble_max_width() <= vw


def test_bubble_reasonable_on_medium_viewport(qapp):
    stub, vw = _stub_with_viewport(qapp, 800)
    w = stub._bubble_max_width()
    assert 0 < w <= vw
