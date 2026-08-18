"""Finishing a stream must not move the chat view.

When a streamed reply is replaced by its markdown render, the content was
already on screen at full height. The hand-off used to run the normal entrance
animation, which collapses a widget to zero height and springs it open — so
every bubble below shifted twice and a long chat appeared to scroll wildly at
the exact moment the reply finished. The viewport is pinned across the swap
instead: follow the bottom if the reader was at the bottom, otherwise do not
move at all.
"""

from systema.ui.chat.bubbles import BubblesMixin

_capture = BubblesMixin._capture_scroll_pin
_restore = BubblesMixin._restore_scroll_pin


class _Bar:
    def __init__(self, value, maximum):
        self._v, self._m = value, maximum

    def value(self):
        return self._v

    def maximum(self):
        return self._m

    def setValue(self, v):
        self._v = v


class _Area:
    def __init__(self, bar):
        self._bar = bar

    def verticalScrollBar(self):
        return self._bar


class _Chat:
    def __init__(self, bar=None):
        if bar is not None:
            self.chat_scroll_area = _Area(bar)


def test_reader_scrolled_up_is_left_exactly_where_they_were():
    bar = _Bar(value=1200, maximum=5000)
    chat = _Chat(bar)

    pin = _capture(chat)
    assert pin == (1200, False)

    bar._m = 7300          # the swap grew the content
    _restore(chat, pin)

    assert bar.value() == 1200, "back-reading must not be dragged by a hand-off"


def test_reader_at_the_bottom_follows_the_new_bottom():
    bar = _Bar(value=5000, maximum=5000)
    chat = _Chat(bar)

    pin = _capture(chat)
    assert pin == (5000, True)

    bar._m = 7300
    _restore(chat, pin)

    assert bar.value() == 7300, "someone at the bottom should stay at the bottom"


def test_near_bottom_counts_as_bottom():
    """A few pixels of slack — a scrollbar rarely lands exactly on maximum."""
    bar = _Bar(value=4997, maximum=5000)
    assert _capture(_Chat(bar))[1] is True


def test_missing_scroll_area_is_survivable():
    assert _capture(_Chat()) is None
    _restore(_Chat(), None)          # must not raise
    _restore(_Chat(), (10, False))   # no scroll area — still must not raise
