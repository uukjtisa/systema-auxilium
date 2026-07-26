"""
tests/systema/ui/test_thinking_sparkle.py

The Thinking card's icon was QLabel("◇") — a hollow outline that read as an
empty box beside the filled card glyphs, and could not show that anything was
happening. It is now a painted SparkleGlyph that animates *while the assistant
is actually thinking* and freezes solid when it stops.

"Actually thinking" means two different things depending on the mode, and both
are covered here:

  * STREAMED — reasoning deltas are still arriving, so the card is not done;
  * NON-STREAMED — all the reasoning lands at once, so the only thing still
    happening is the reply's typewriter reveal, and the sparkle rides that.

And a third case that must NOT animate: a reloaded session. Replayed cards are
built finished and the reveal is skipped during bulk render, so a restored
transcript shows the same resting star a completed live turn ends on. That falls
out of the state rule rather than being special-cased, which is what these
tests pin.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPixmap                                    # noqa: E402
from PyQt6.QtWidgets import QWidget                                # noqa: E402

from systema.ui.chat.event_cards import EventCardsMixin            # noqa: E402
from systema.ui.widgets.painted_icons import SparkleGlyph          # noqa: E402


# ── the glyph itself ─────────────────────────────────────────────────────────

@pytest.fixture
def glyph(qapp):
    g = SparkleGlyph(px=12, color='#5F6368')
    yield g
    g.stop()


def test_it_does_not_animate_until_asked(glyph):
    assert glyph.is_running() is False


def test_start_and_stop(glyph):
    glyph.start()
    assert glyph.is_running() is True
    glyph.stop()
    assert glyph.is_running() is False


def test_start_is_idempotent(glyph):
    """A second start must not restart the cycle — the phase would jump, which
    is visible as a stutter every time a delta arrives."""
    glyph.start()
    glyph._clock = _FixedClock(400)
    before = glyph._phases()
    glyph.start()
    assert glyph._phases() == before


def test_stopping_leaves_the_resting_shape(glyph):
    """The resting frame is what a finished turn and a reloaded session both
    show, so it must be the full, solid star — not whatever frame it stopped on."""
    glyph.start()
    glyph._clock = _FixedClock(int(SparkleGlyph.BREATH_MS * 0.5))
    assert glyph._phases()[0] < 1.0            # mid-breath, arms retracted

    glyph.stop()

    arm, glow, orbit = glyph._phases()
    assert (arm, glow, orbit) == (1.0, 1.0, 0.0)


def test_hiding_stops_the_timer(glyph):
    """A card scrolled away or a turn torn down mid-stream must not leave a
    repaint timer running."""
    glyph.show()
    glyph.start()
    glyph.hide()
    assert glyph.is_running() is False


def test_the_arms_never_collapse_or_overshoot(glyph):
    glyph.start()
    for ms in range(0, int(SparkleGlyph.BREATH_MS * 2), 37):
        glyph._clock = _FixedClock(ms)
        arm, glow, _ = glyph._phases()
        assert SparkleGlyph.MIN_ARM <= arm <= 1.0
        assert SparkleGlyph.MIN_GLOW <= glow <= 1.0


def test_it_paints_at_every_phase(glyph, qapp):
    """Guard rail on the painter maths — a negative radius or a bad alpha
    raises here rather than in the middle of a live turn."""
    glyph.start()
    canvas = QPixmap(glyph.width(), glyph.height())
    for ms in range(0, int(SparkleGlyph.ORBIT_MS), 53):
        glyph._clock = _FixedClock(ms)
        glyph.render(canvas)
    glyph.stop()
    glyph.render(canvas)


def test_zoom_resizes_it(glyph):
    glyph.set_px(12)
    small = glyph.width()
    glyph.set_px(22)
    assert glyph.width() > small
    assert glyph.width() == glyph.height(), "sparks need square room to orbit"


class _FixedClock:
    def __init__(self, ms):
        self._ms = ms

    def elapsed(self):
        return self._ms


# ── the state rule ───────────────────────────────────────────────────────────

class _Host(EventCardsMixin):
    """Just enough to drive _sync_thinking_sparkle — no chat window needed."""

    def __init__(self, glyph, done):
        self._turn_thinking_card = {'icon': glyph, 'state': {'done': done}}
        self._reveal_jobs = []


@pytest.fixture
def host_glyph(qapp):
    g = SparkleGlyph(px=12)
    yield g
    g.stop()


def test_streaming_reasoning_sparkles(host_glyph):
    host = _Host(host_glyph, done=False)
    host._sync_thinking_sparkle()
    assert host_glyph.is_running() is True


def test_it_stops_when_the_reasoning_finishes(host_glyph):
    host = _Host(host_glyph, done=False)
    host._sync_thinking_sparkle()
    host._turn_thinking_card['state']['done'] = True
    host._sync_thinking_sparkle()
    assert host_glyph.is_running() is False


def test_a_finished_card_sparkles_while_the_reply_is_typing(host_glyph):
    """The non-streamed case: the reasoning arrived in one lump, so the only
    live signal left is the typewriter reveal."""
    host = _Host(host_glyph, done=True)
    host._reveal_jobs = [{'timer': None}]

    host._sync_thinking_sparkle()

    assert host_glyph.is_running() is True


def test_it_stops_when_the_typing_finishes(host_glyph):
    host = _Host(host_glyph, done=True)
    host._reveal_jobs = [{'timer': None}]
    host._sync_thinking_sparkle()

    host._reveal_jobs = []
    host._sync_thinking_sparkle()

    assert host_glyph.is_running() is False


def test_a_reloaded_transcript_never_animates(host_glyph):
    """Replayed cards are built finished and bulk render skips the reveal, so
    both signals are off and the star rests."""
    host = _Host(host_glyph, done=True)
    host._sync_thinking_sparkle()
    assert host_glyph.is_running() is False


def test_no_card_is_not_an_error(qapp):
    host = _Host(None, done=True)
    host._turn_thinking_card = None
    host._sync_thinking_sparkle()


def test_a_plain_label_icon_is_left_alone(qapp):
    """Older cards (and any card whose icon is a text glyph) have no start()."""
    host = _Host(QWidget(), done=False)
    host._sync_thinking_sparkle()
