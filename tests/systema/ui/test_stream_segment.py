"""
tests/systema/ui/test_stream_segment.py

The chat window's live-streaming handlers (BubblesMixin): the throwaway text
segment deltas paint into, its hand-off to the final full-fidelity render, and
the collapsible Thinking card's live append + UI-only persistence.

These drive the mixin methods against a minimal host so no full ChatWindow
(and no controller/engine) is needed.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402

from systema.ui.chat.bubbles import BubblesMixin  # noqa: E402


class _Host(BubblesMixin, QWidget):
    """Minimal stand-in exposing only what the streaming handlers touch."""

    def __init__(self):
        super().__init__()
        self._stream_seg = None
        self._stream_think_card = None
        self._stream_active = False
        self._turn_thinking_card = None
        self._turn_thinking_group = None
        self._ai_turn_group = None
        self._live_card = None
        self._sticky_bottom = False
        self._bulk_render = False
        self.message_widgets = []
        self.saved_thinking = []
        self.segments = []
        self.chat_layout = QVBoxLayout(self)
        self.chat_layout.addStretch()

    # -- collaborators the handlers call into -------------------------------
    def _get_msg_font_size(self):
        return 13

    def _bubble_style(self):
        return 'blend'

    def _insert_turn_segment(self, widget):
        widget.setParent(self)
        self.segments.append(widget)
        return {'row': self, 'body_layout': self.chat_layout}

    def _save_thinking_event(self, text):
        self.saved_thinking.append(text)

    def add_thinking_card(self, text='', save_to_history=True, live=False):
        """Mirrors the real per-turn singleton: one card per turn, reused."""
        card = getattr(self, '_turn_thinking_card', None)
        if card is not None:
            if text:
                card['append'](("\n\n" if card['state']['text'] else "") + text)
            return card if live else card['widget']

        state = {'text': text}
        holder = QWidget(self)
        self.segments.append(holder)

        def _append(delta):
            state['text'] += delta

        def _finish():
            state['done'] = True
            return state['text']

        card = {'widget': holder, 'append': _append, 'finish': _finish,
                'state': state}
        self._turn_thinking_card = card
        return card if live else holder

    def scroll_to_bottom(self):
        pass


@pytest.fixture
def host(qapp):
    return _Host()


# ── live text segment ────────────────────────────────────────────────────────

def test_stream_started_creates_one_live_segment(host):
    host.on_stream_started()
    assert host._stream_seg is not None
    assert host._stream_active is True
    assert len(host.segments) == 1

    host.on_stream_started()          # idempotent — never a second stub
    assert len(host.segments) == 1


def test_text_deltas_accumulate_into_the_live_label(host):
    host.on_stream_started()
    for delta in ("Hel", "lo ", "world"):
        host.on_stream_text(delta)
    host._flush_stream()                     # painting is coalesced onto a timer
    assert host._stream_seg['text'] == "Hello world"
    assert "Hello world" in host._stream_seg['label'].text()


def test_text_delta_without_started_opens_the_segment(host):
    host.on_stream_text("implicit")
    assert host._stream_seg is not None
    host._flush_stream()
    assert host._stream_seg['text'] == "implicit"


def test_markup_in_deltas_is_escaped_not_interpreted(host):
    host.on_stream_started()
    host.on_stream_text("<b>not bold</b> & co")
    host._flush_stream()
    rendered = host._stream_seg['label'].text()
    assert "&lt;b&gt;" in rendered and "<b>not bold" not in rendered


def test_newlines_become_line_breaks_while_streaming(host):
    host.on_stream_started()
    host.on_stream_text("line1\nline2")
    host._flush_stream()
    assert "<br>" in host._stream_seg['label'].text()


# ── thinking card ────────────────────────────────────────────────────────────

def test_thinking_deltas_build_one_live_card(host):
    host.on_stream_thinking("step 1 ")
    host.on_stream_thinking("step 2")
    card = host._stream_think_card
    assert card is not None
    assert card['state']['text'] == "step 1 step 2"
    assert len(host.segments) == 1        # one card, not one per delta


def test_finish_persists_thinking_once_and_clears_the_card(host):
    host.on_stream_thinking("reasoning")
    host.on_stream_finished()
    assert host.saved_thinking == ["reasoning"]
    assert host._stream_think_card is None
    assert host._stream_active is False


def test_one_card_per_turn_shared_by_every_response_in_the_bubble(host):
    """Assistant turns merge into one bubble, so all responses in that turn
    feed a SINGLE thinking card, appended in order."""
    host.on_stream_thinking("first response reasoning")
    host.on_stream_finished()
    host.on_stream_thinking("second response reasoning")
    host.on_stream_finished()

    assert len(host.segments) == 1              # one card, not two
    assert host._turn_thinking_card['state']['text'] == (
        "first response reasoning\n\nsecond response reasoning")


def test_each_response_persists_only_its_own_reasoning(host):
    """Reload replays one ui_event per response, in order — the per-turn card
    re-merges them, so no text may be saved twice."""
    host.on_stream_thinking("alpha")
    host.on_stream_finished()
    host.on_stream_thinking("beta")
    host.on_stream_finished()

    assert host.saved_thinking == ["alpha", "\n\nbeta"]
    assert "".join(host.saved_thinking) == host._turn_thinking_card['state']['text']


def test_finish_without_thinking_saves_nothing(host):
    host.on_stream_started()
    host.on_stream_text("plain reply")
    host.on_stream_finished()
    assert host.saved_thinking == []


# ── hand-off ─────────────────────────────────────────────────────────────────

def test_clear_stream_segment_is_idempotent(host):
    host.on_stream_started()
    host._clear_stream_segment()
    assert host._stream_seg is None
    host._clear_stream_segment()          # second call must not raise
    assert host._stream_seg is None


def test_stale_stub_dropped_only_when_stream_is_no_longer_active(host):
    host.on_stream_started()
    host._stream_active = True
    host._drop_stale_stream_segment()
    assert host._stream_seg is not None    # still streaming — keep it

    host._stream_active = False
    host._drop_stale_stream_segment()
    assert host._stream_seg is None


# ── coalescing / no quadratic repaint (the big-reply freeze) ─────────────────

def test_deltas_do_not_touch_the_widget_until_flushed(host):
    """Painting per delta meant a full rich-text relayout per token."""
    host.on_stream_started()
    for _ in range(50):
        host.on_stream_text("token ")
    assert host._stream_seg['label'].text() == ""     # nothing painted yet
    assert len(host._stream_seg['pending']) == 50
    host._flush_stream()
    assert host._stream_seg['label'].text().count("token") == 50
    assert host._stream_seg['pending'] == []


def test_flush_escapes_only_the_new_chunk(host):
    """The old code re-escaped the WHOLE reply on every delta (O(n^2)). The
    rendered html must be built incrementally from the previous html."""
    import unittest.mock as mock
    host.on_stream_started()
    host.on_stream_text("a" * 100)
    host._flush_stream()
    first = host._stream_seg['html']

    host.on_stream_text("<b>")
    with mock.patch("html.escape", side_effect=lambda s, *a, **k: s.replace("<", "&lt;")) as esc:
        host._flush_stream()
        # exactly one escape call, and it saw ONLY the new chunk
        assert esc.call_count == 1
        assert esc.call_args[0][0] == "<b>"
    assert host._stream_seg['html'].startswith(first)


def test_flush_is_a_no_op_when_nothing_is_buffered(host):
    host.on_stream_started()
    host._flush_stream()
    painted = host._stream_seg['label'].text()
    host._flush_stream()
    assert host._stream_seg['label'].text() == painted


def test_finish_paints_everything_still_buffered(host):
    host.on_stream_started()
    host.on_stream_text("tail end of the report")
    host.on_stream_finished()                 # must not lose the buffer
    assert "tail end of the report" in host._stream_seg['label'].text()
