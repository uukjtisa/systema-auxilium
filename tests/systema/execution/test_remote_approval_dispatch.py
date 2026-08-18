"""A phone's approve/deny must land even when only the notification card is up.

When the chat window is closed or minimised the FULL approval dialog is built
but never shown — only the compact card is, and the card owns the event loop
that blocks the tool call. `_close_active_approval_dialog` used to gate on
`dialog.isVisible()`, so every remote decision in that state was dropped: the
PC stayed stuck and the phone notification stayed pending. That is the common
case, since the phone is what gets used when nobody is at the keyboard.
"""

import pytest

from systema.execution.tool_manager import ToolManager

_close = ToolManager._close_active_approval_dialog


class _Editor:
    def __init__(self, text=""):
        self._text = text

    def setPlainText(self, t):
        self._text = t

    def toPlainText(self):
        return self._text


class _Card:
    def __init__(self):
        self.resolved = None

    def resolve(self, outcome):
        self.resolved = outcome


class _Field:
    def __init__(self):
        self._text = ""

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class _Dialog:
    def __init__(self, visible=False, card=None):
        self._visible = visible
        self._mini_card = card
        self.code_edit = _Editor("original")
        self.note_edit = _Field()
        self.reason_edit = _Field()
        self.result = None
        self.modified_code = None
        self.accepted = False
        self.closed = False

    def isVisible(self):
        return self._visible

    def accept(self):
        self.accepted = True

    def close(self):
        self.closed = True


class _TM:
    def __init__(self, dialog):
        self._active_approval_dialog = dialog


@pytest.mark.parametrize("approved,expected", [(True, "accept"), (False, "reject")])
def test_card_only_state_is_resolved_through_the_card(approved, expected):
    card = _Card()
    dialog = _Dialog(visible=False, card=card)

    _close(_TM(dialog), approved, "")

    assert card.resolved == expected, (
        "the card owns the blocking event loop — a remote decision that does "
        "not finish it leaves the PC hung"
    )
    assert not dialog.accepted and not dialog.closed, (
        "the never-shown dialog must not be driven directly; the post-loop "
        "code applies the outcome via on_accept/on_reject"
    )


def test_phone_edited_code_reaches_the_editor():
    card = _Card()
    dialog = _Dialog(visible=False, card=card)

    _close(_TM(dialog), True, "edited on the phone")

    # on_accept() reads modified_code out of the editor, so one path applies it
    # no matter which surface the decision came from.
    assert dialog.code_edit.toPlainText() == "edited on the phone"


def test_visible_dialog_path_is_unchanged():
    dialog = _Dialog(visible=True, card=None)

    _close(_TM(dialog), True, "from phone")

    assert dialog.result == "accept"
    assert dialog.modified_code == "from phone"
    assert dialog.accepted


def test_reject_on_a_visible_dialog_closes_it():
    dialog = _Dialog(visible=True, card=None)

    _close(_TM(dialog), False, "")

    assert dialog.result == "reject"
    assert dialog.closed


def test_no_active_dialog_is_a_no_op():
    _close(_TM(None), True, "x")      # must not raise


def test_rejection_reason_from_the_phone_reaches_the_dialog_field():
    """on_reject() reads reason_edit, so the agent gets the phone's reason the
    same way it gets one typed on the PC."""
    dialog = _Dialog(visible=False, card=_Card())

    _close(_TM(dialog), False, "", "not on production, please")

    assert dialog.reason_edit.text() == "not on production, please"
    assert dialog.note_edit.text() == "", "only the pressed button's field is set"


def test_approval_note_from_the_phone_reaches_the_dialog_field():
    dialog = _Dialog(visible=False, card=_Card())

    _close(_TM(dialog), True, "", "go ahead, but log it")

    assert dialog.note_edit.text() == "go ahead, but log it"
    assert dialog.reason_edit.text() == ""


def test_message_is_optional():
    dialog = _Dialog(visible=False, card=_Card())

    _close(_TM(dialog), True, "code")      # no message argument at all

    assert dialog.note_edit.text() == ""
