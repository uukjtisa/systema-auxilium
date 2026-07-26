"""
tests/systema/ui/test_smooth_scroll.py

The old velocity+friction inertia was smooth but not AIMABLE: one wheel notch
added 120*0.38 = 45.6 px/tick of velocity which, under 0.86 friction, integrated
to roughly 326 px — about a third of a viewport. Anything closer than that was
unreachable: nudge up and you sailed past it, nudge back down and you sailed
past it again. Long outputs had positions you simply could not stop on.

These tests pin the properties that fix makes true: an exact, predictable step
per notch, accumulation across rapid notches, clamping at both ends, and — for
the chat — that scrolling UP releases the sticky-bottom pin immediately so a
streaming reply cannot drag you back down mid-read.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, QPointF, Qt                       # noqa: E402
from PyQt6.QtGui import QWheelEvent                                # noqa: E402
from PyQt6.QtWidgets import QLabel, QScrollArea                    # noqa: E402

from systema.ui.widgets.smooth_scroll import (WHEEL_STEP_PX,       # noqa: E402
                                              install_smooth_scroll)


@pytest.fixture
def area(qapp):
    """A scroll area with content far taller than its viewport.

    The inner widget is given an explicit tall size: offscreen, a plain QLabel
    may never be laid out, leaving the scrollbar range at 0 and making every
    assertion here vacuously pass.
    """
    a = QScrollArea()
    inner = QLabel("\n".join(f"line {i}" for i in range(400)))
    inner.setFixedSize(280, 8000)
    a.setWidget(inner)
    a.resize(300, 400)
    a.show()
    qapp.processEvents()
    assert a.verticalScrollBar().maximum() > 1000, "fixture is not scrollable"
    yield a
    a.hide()


def _wheel(notches: int, pixel_delta: QPoint = None):
    return QWheelEvent(
        QPointF(10, 10), QPointF(10, 10),
        pixel_delta or QPoint(0, 0), QPoint(0, notches * 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False)


def test_a_deliberate_notch_moves_exactly_one_step(area, qapp):
    """Slow, gentle scrolling is where precision matters: an isolated notch
    must travel EXACTLY one step, with no acceleration applied."""
    scroller = install_smooth_scroll(area)
    start = area.verticalScrollBar().value()

    scroller.eventFilter(area.viewport(), _wheel(-1))     # one notch down

    assert scroller._target == pytest.approx(start + WHEEL_STEP_PX)


def test_notches_accumulate_instead_of_restarting(area, qapp):
    """Three quick notches must ADD UP — not three glides that each restart
    from the current mid-flight position (that restart is what felt choppy).
    Rapid notches also accelerate, so the total is at least three steps."""
    scroller = install_smooth_scroll(area)
    start = area.verticalScrollBar().value()

    for _ in range(3):
        scroller.eventFilter(area.viewport(), _wheel(-1))

    assert scroller._target >= start + 3 * WHEEL_STEP_PX


def test_the_step_is_small_enough_to_aim_with(area):
    """The regression in one number: the old inertia moved ~326px per notch,
    so any target closer than that was unreachable."""
    assert WHEEL_STEP_PX <= 160, (
        "a wheel notch this large makes nearby positions unreachable again")


def test_every_position_is_reachable(area, qapp):
    """The user-visible property: gentle notches land where you aim. Step down
    one, then back up one, and you are exactly where you began — no overshoot
    in either direction."""
    import time
    scroller = install_smooth_scroll(area)
    bar = area.verticalScrollBar()
    start = bar.value()

    scroller.eventFilter(area.viewport(), _wheel(-1))
    assert scroller._target == pytest.approx(start + WHEEL_STEP_PX)

    time.sleep(0.25)          # deliberate, unhurried second movement
    scroller.eventFilter(area.viewport(), _wheel(1))

    assert _destination(scroller, bar) == pytest.approx(start)


def _destination(scroller, bar):
    """Where the view will end up: the pending glide target, or the current
    value when there is nothing left to glide to (already at an edge)."""
    return bar.value() if scroller._target is None else scroller._target


def test_scrolling_clamps_at_the_top(area, qapp):
    scroller = install_smooth_scroll(area)
    bar = area.verticalScrollBar()
    for _ in range(50):
        scroller.eventFilter(area.viewport(), _wheel(1))
    assert _destination(scroller, bar) == bar.minimum()


def test_scrolling_clamps_at_the_bottom(area, qapp):
    scroller = install_smooth_scroll(area)
    bar = area.verticalScrollBar()
    for _ in range(200):
        scroller.eventFilter(area.viewport(), _wheel(-1))
    assert _destination(scroller, bar) == bar.maximum()


def test_ctrl_wheel_is_left_alone_for_zoom(area, qapp):
    """Ctrl+wheel belongs to the zoom handler — consuming it here would break
    Ctrl+scroll zoom in the chat."""
    scroller = install_smooth_scroll(area)
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0),
                     QPoint(0, 120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.ControlModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)

    assert scroller.eventFilter(area.viewport(), ev) is False


def test_a_trackpad_gesture_is_applied_directly(area, qapp):
    """Precision devices already report exact pixels; animating on top of a
    continuous gesture adds lag."""
    scroller = install_smooth_scroll(area)
    bar = area.verticalScrollBar()
    start = bar.value()

    scroller.eventFilter(area.viewport(), _wheel(0, QPoint(0, -37)))

    assert bar.value() == start + 37


def test_install_is_idempotent(area):
    first = install_smooth_scroll(area)
    assert install_smooth_scroll(area) is first


def test_a_non_scrollable_area_is_ignored(qapp):
    a = QScrollArea()
    a.setWidget(QLabel("tiny"))
    a.resize(300, 400)
    a.show()
    qapp.processEvents()
    scroller = install_smooth_scroll(a)

    assert scroller.eventFilter(a.viewport(), _wheel(-1)) is False
    a.hide()


# ── the sticky-bottom release ────────────────────────────────────────────────

def test_scrolling_up_reports_a_negative_delta(area, qapp):
    """The chat uses this callback to drop its bottom pin the moment the user
    heads up — position alone re-armed the pin and yanked the view back down
    on the next streamed chunk."""
    seen = []
    scroller = install_smooth_scroll(area, on_user_scroll=seen.append)

    scroller.eventFilter(area.viewport(), _wheel(1))     # up
    scroller.eventFilter(area.viewport(), _wheel(-1))    # down

    assert seen[0] < 0, "an upward wheel must report a negative delta"
    assert seen[1] > 0, "a downward wheel must report a positive delta"
