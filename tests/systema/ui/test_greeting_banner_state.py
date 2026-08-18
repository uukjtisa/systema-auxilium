"""
tests/systema/ui/test_greeting_banner_state.py

The greeting banner is an EMPTY-SESSION state, and it has to behave like one
in BOTH directions.

It used to be a one-way door: `dismiss_session_intro` removed it on the first
message and only a brand-new session ever built another. Deleting or rewinding
away every message therefore left a blank slate — the exact state that gets a
greeting on startup, rendered as nothing at all.

Driven against the real mixin method with a light stub `self`, in the style of
test_image_bubbles.py: a whole ChatWindow is neither necessary nor cheap here.
"""
import types

import pytest

pytest.importorskip("PyQt6")

from systema.ui.chat.bubbles import BubblesMixin   # noqa: E402


class _Chat:
    """The smallest thing restore_session_intro_if_empty touches."""

    def __init__(self, widgets):
        self.message_widgets = list(widgets)
        self.banners = 0
        self.groups_ended = 0

    def add_greeting_banner(self):
        self.banners += 1
        self.message_widgets.append({'role': 'system', '_intro': True})

    def _end_ai_turn_group(self):
        self.groups_ended += 1

    restore_session_intro_if_empty = \
        BubblesMixin.restore_session_intro_if_empty


def _msg(role, **kw):
    return dict(role=role, **kw)


def test_an_emptied_transcript_gets_the_greeting_back():
    chat = _Chat([])
    chat.restore_session_intro_if_empty()
    assert chat.banners == 1


def test_a_transcript_with_a_user_message_is_left_alone():
    chat = _Chat([_msg('user')])
    chat.restore_session_intro_if_empty()
    assert chat.banners == 0


def test_a_transcript_with_only_an_assistant_reply_is_left_alone():
    chat = _Chat([_msg('assistant')])
    chat.restore_session_intro_if_empty()
    assert chat.banners == 0


def test_leftover_cards_and_system_notes_still_count_as_empty():
    """A session holding only a stray tool card reads to the user as an empty
    conversation, so it should look like one."""
    chat = _Chat([_msg('system'), _msg('tool'), _msg('file_op')])
    chat.restore_session_intro_if_empty()
    assert chat.banners == 1


def test_it_does_not_stack_a_second_banner():
    chat = _Chat([])
    chat.restore_session_intro_if_empty()
    chat.restore_session_intro_if_empty()
    chat.restore_session_intro_if_empty()
    assert chat.banners == 1


def test_the_open_turn_shell_is_closed_first():
    """Deleting the last message can leave an assistant turn shell open; the
    banner must not be nested inside it."""
    chat = _Chat([])
    chat.restore_session_intro_if_empty()
    assert chat.groups_ended == 1


def test_a_failure_never_breaks_the_delete():
    """The banner is decoration. A delete that removed the message must not
    then raise on the way out."""
    chat = _Chat([])
    chat.add_greeting_banner = types.MethodType(
        lambda self: (_ for _ in ()).throw(RuntimeError("boom")), chat)
    chat.restore_session_intro_if_empty()      # must not raise


def test_every_deletion_path_restores_it():
    """_delete_message, _rewind_to_message and _rewind_to_here can each empty
    the transcript. Wiring only one of them leaves the blank slate reachable."""
    import inspect
    for name in ('_delete_message', '_rewind_to_message', '_rewind_to_here'):
        src = inspect.getsource(getattr(BubblesMixin, name))
        assert 'restore_session_intro_if_empty' in src, (
            f"{name} can empty the transcript but never restores the greeting")
