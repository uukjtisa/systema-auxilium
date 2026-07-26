"""
tests/systema/ui/test_turn_shell_prune.py

An assistant turn is drawn as ONE shell: avatar + name + a body that every
segment (reply text, thinking card, tool card) stacks into. Segments are
removed on their own by a dozen paths — stop mid-stream, discard an unsaved
thinking card, edit-and-resend, delete, rewind. Any of those can empty the
body, and an empty body means a husk: an avatar and a name with nothing under
them, which is exactly what the user saw stacked above their edited prompt.

`_prune_empty_group` is the one place that decides this, and it is deliberately
conservative — a shell that still holds anything is never touched. These tests
pin both halves of that: the husk always goes, and a turn with real content
never does.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QWidget      # noqa: E402

from systema.ui.chat.bubbles import BubblesMixin              # noqa: E402
from systema.ui.chat.theming import ThemingMixin              # noqa: E402


class _Controller:
    settings = {}

    def get_assistant_name(self):
        return "Kimi"


class _Host(ThemingMixin, BubblesMixin, QWidget):
    """Minimal chat window: the REAL turn-shell builder, nothing else faked.

    The point is to exercise `_ensure_ai_turn_group` / `_prune_empty_group`
    as they actually run, so a change to the shell's structure (the
    objectName the prune looks up, say) fails here instead of shipping.
    """

    def __init__(self):
        super().__init__()
        self.controller = _Controller()
        self.bot_avatar = "🤖"
        self.user_avatar = "👤"
        self.message_widgets = []
        self._ai_turn_group = None
        self._turn_thinking_card = None
        self._live_card = None
        self._stream_seg = None
        self._stream_think_card = None
        self._stream_active = False
        self._stream_suppressed = False
        self._thinking_bubble_widget = None
        self._thinking_bubble_label = None
        self._thinking_bubble_group = None
        self._glass_enabled = False
        self.chat_layout = QVBoxLayout(self)
        self.chat_layout.addStretch()

    def _move_thinking_dots_to_bottom(self):
        pass

    def scroll_to_bottom(self):
        pass

    def _save_thinking_event(self, text):
        pass


@pytest.fixture
def host(qapp):
    return _Host()


def _shell_rows(host):
    """Every turn shell currently in the chat column."""
    return [host.chat_layout.itemAt(i).widget()
            for i in range(host.chat_layout.count())
            if host.chat_layout.itemAt(i).widget() is not None
            and host.chat_layout.itemAt(i).widget().objectName() == "turnRow"]


def _segment(host):
    seg = QFrame()
    host._insert_turn_segment(seg)
    return seg


# ── the husk ─────────────────────────────────────────────────────────────────

def test_a_turn_that_loses_its_only_segment_is_removed(host):
    seg = _segment(host)
    assert len(_shell_rows(host)) == 1

    host._detach_chat_widget(seg)

    assert _shell_rows(host) == [], "avatar+name husk left behind"


def test_the_live_turn_reference_is_dropped_with_the_shell(host):
    """A stale `_ai_turn_group` would make the NEXT segment try to append into
    a deleted widget."""
    seg = _segment(host)
    host._detach_chat_widget(seg)

    assert host._ai_turn_group is None


def test_a_pruned_turn_does_not_block_the_next_one(host):
    host._detach_chat_widget(_segment(host))
    _segment(host)

    assert len(_shell_rows(host)) == 1


# ── the conservative half ────────────────────────────────────────────────────

def test_a_turn_that_still_holds_content_survives(host):
    """A completed thinking card or tool card must outlive its neighbours —
    pruning on any removal would erase real, saved output."""
    kept, dropped = _segment(host), _segment(host)

    host._detach_chat_widget(dropped)

    assert len(_shell_rows(host)) == 1
    assert kept.parent() is not None


def test_only_the_emptied_turn_is_pruned(host):
    first = _segment(host)
    host._end_ai_turn_group()
    second = _segment(host)
    assert len(_shell_rows(host)) == 2

    host._detach_chat_widget(second)

    rows = _shell_rows(host)
    assert len(rows) == 1 and rows[0] is first._group_row


def test_pruning_an_unknown_row_is_harmless(host):
    host._prune_empty_group(None)
    host._prune_empty_group(QFrame())


# ── stop mid-stream ──────────────────────────────────────────────────────────

def test_stopping_a_stream_leaves_no_husk(host):
    """The reported bug: Stop deleted the half-written reply segment and left
    its avatar+name shell sitting in the chat."""
    host.on_stream_started()
    host.on_stream_text("half a sen")
    assert len(_shell_rows(host)) == 1

    assert host.abort_stream() is True

    assert host._stream_seg is None
    assert _shell_rows(host) == []


def test_stopping_keeps_a_turn_that_already_produced_output(host):
    """Stop during a multi-step work turn: the finished cards above the aborted
    step are real output and stay, shell included."""
    done = _segment(host)
    host.on_stream_started()

    host.abort_stream()

    assert len(_shell_rows(host)) == 1
    assert done.parent() is not None


def test_aborting_with_nothing_in_flight_reports_false(host):
    _segment(host)
    assert host.abort_stream() is False
    assert len(_shell_rows(host)) == 1


def test_the_handoff_to_the_final_render_does_not_strand_a_shell(host):
    """_clear_stream_segment also runs on the normal hand-off path; pruning
    there must not leave the turn unable to receive its final text."""
    host.on_stream_started()
    host._clear_stream_segment()

    seg = _segment(host)
    assert seg.parent() is not None
    assert len(_shell_rows(host)) == 1
