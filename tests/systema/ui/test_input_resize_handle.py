"""
tests/systema/ui/test_input_resize_handle.py

The two width-resize grab bars live in the input pill's 14px side gutters. They
used to be placed only by _position_input_handles(), which runs after
_position_input_overlay() calls setGeometry() — and that is not the only thing
that resizes the container.

The pill's layout uses SetMinimumSize. When the bottom row's content grows (the
token estimate going from "~0 token per request" to "~8.3k token per request", a
work banner appearing mid-turn) the layout pushes a WIDER minimumSize onto the
container and Qt resizes it on the spot, with no setGeometry call. Nothing
repositioned the bars, so the right one stayed where the container's old right
edge had been — a stray vertical line sitting next to the mic button, inside the
pill.

So the handle tracks its parent itself. These tests resize the container by the
routes that actually occur, never through the chat window, and assert the bars
are still in the gutters.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel,          # noqa: E402
                             QVBoxLayout, QWidget)

from systema.ui.chat.input_dock import _InputResizeHandle          # noqa: E402

GUTTER = 4          # px inset from the container edge (see reposition)


@pytest.fixture
def pill(qapp):
    """A stand-in for the input pill: a container whose VBox is pinned to its
    content with SetMinimumSize, exactly like the real one, holding a row whose
    width can grow."""
    host = QWidget()
    host.resize(1400, 300)

    container = QFrame(host)
    lay = QVBoxLayout(container)
    lay.setContentsMargins(14, 8, 14, 12)
    lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

    card = QFrame()
    row = QHBoxLayout(card)
    token = QLabel("~0 token per request")
    token.setMinimumWidth(120)
    row.addWidget(token)
    lay.addWidget(card)

    host.show()
    qapp.processEvents()
    yield container, lay, row, token
    host.hide()


def _handles(container):
    owner = _Owner()
    hs = [_InputResizeHandle(owner, 'left', container),
          _InputResizeHandle(owner, 'right', container)]
    for h in hs:
        h.reposition()
    return hs


class _Owner:
    """The mixin methods a handle calls during a drag — unused by these tests,
    which only exercise placement."""
    input_container = None
    _chat_container = None


def _assert_in_gutters(container, left, right):
    assert left.x() == GUTTER
    assert right.x() == container.width() - right.W - GUTTER, (
        "the right grab bar is not on the container's right edge — it strands "
        "inside the pill, next to the mic button")


def test_the_bars_start_in_the_gutters(pill):
    container, *_ = pill
    left, right = _handles(container)
    _assert_in_gutters(container, left, right)


def test_a_container_that_resizes_itself_keeps_its_bars(pill):
    """The reported bug. The container is NOT in a parent layout, so nothing
    calls setGeometry on it here — the layout's growing minimumSize resizes it,
    which is exactly how it happens in the app."""
    container, lay, row, token = pill
    left, right = _handles(container)
    before = container.width()

    token.setText("~8.3k token per request")
    token.setMinimumWidth(700)
    row.invalidate(); row.activate()
    lay.invalidate(); lay.activate()

    assert container.width() > before, "fixture did not reproduce the growth"
    _assert_in_gutters(container, left, right)


def test_an_explicit_resize_moves_the_bars(pill):
    container, *_ = pill
    left, right = _handles(container)

    container.setGeometry(0, 0, max(container.width() + 200, 900), 70)

    _assert_in_gutters(container, left, right)


def test_the_bars_stay_vertically_centred(pill):
    container, *_ = pill
    left, right = _handles(container)

    container.resize(container.width(), 160)

    for hd in (left, right):
        assert hd.y() == (container.height() - hd.H) // 2


def test_a_container_shorter_than_the_bar_never_goes_negative(qapp):
    """Mid-relayout the container can measure shorter than the bar itself."""
    holder = QWidget()
    holder.resize(400, 300)
    container = QFrame(holder)
    left, _right = _handles(container)

    container.resize(300, 10)

    assert left.y() == 0


def test_reposition_without_a_parent_is_harmless(qapp):
    """Teardown order: the parent can go first."""
    holder = QWidget()
    hd = _InputResizeHandle(_Owner(), 'left', holder)
    hd.setParent(None)
    hd.reposition()
