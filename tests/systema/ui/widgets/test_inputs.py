"""
Tests for systema/ui/widgets/inputs.py — the auto-growing message input.

The pill must expand as lines are typed and reset to one line after send.
Requires PyQt6 via the `qapp` fixture.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QWidget, QVBoxLayout  # noqa: E402

from systema.ui.widgets.inputs import ResizableInput  # noqa: E402


def _shown_input(qapp):
    host = QWidget()
    lay = QVBoxLayout(host)
    ri = ResizableInput()
    lay.addWidget(ri)
    host.resize(500, 300)
    host.show()
    qapp.processEvents()
    return host, ri


def test_input_grows_with_multiple_lines(qapp):
    _host, ri = _shown_input(qapp)
    h0 = ri.text_input.height()
    ri.text_input.setPlainText("l1\nl2\nl3\nl4\nl5")
    qapp.processEvents()
    assert ri.text_input.height() > h0


def test_input_resets_after_clear(qapp):
    _host, ri = _shown_input(qapp)
    ri.text_input.setPlainText("l1\nl2\nl3\nl4")
    qapp.processEvents()
    grown = ri.text_input.height()
    assert grown > 24
    ri.clear()
    qapp.processEvents()
    assert ri.text_input.height() == ri.min_height


def test_input_collapses_when_emptied_by_hand(qapp):
    """Paste big, then delete it all (NOT via send/clear) — it must collapse back
    to one line, never stay stuck tall."""
    _host, ri = _shown_input(qapp)
    ri.text_input.setPlainText("\n".join(f"line {i}" for i in range(8)))
    qapp.processEvents()
    assert ri.text_input.height() > 60
    ri.text_input.setPlainText("")   # manual empty
    qapp.processEvents()
    assert ri.text_input.height() <= ri.min_height + 8   # ~one line


def test_manual_floor_still_allows_autogrow_above_it(qapp):
    """A drag sets a temporary floor; typing past it still grows the box."""
    _host, ri = _shown_input(qapp)
    ri.text_input._user_floor = 120          # simulate a drag to 120px
    ri.text_input.setFixedHeight(120)
    ri.text_input.setPlainText("one line")   # content < floor -> stays at floor
    qapp.processEvents()
    assert ri.text_input.height() >= 120
    ri.text_input.setPlainText("\n".join(f"l{i}" for i in range(14)))  # content > floor
    qapp.processEvents()
    assert ri.text_input.height() > 120      # grew above the floor


def test_manual_floor_resets_on_empty(qapp):
    """The drag floor is temporary — emptying the field clears it."""
    _host, ri = _shown_input(qapp)
    ri.text_input._user_floor = 150
    ri.text_input.setFixedHeight(150)
    ri.text_input.setPlainText("x")
    qapp.processEvents()
    assert ri.text_input.height() >= 150
    ri.text_input.setPlainText("")           # empty -> floor cleared
    qapp.processEvents()
    assert ri.text_input._user_floor == 0
    assert ri.text_input.height() <= ri.min_height + 8
