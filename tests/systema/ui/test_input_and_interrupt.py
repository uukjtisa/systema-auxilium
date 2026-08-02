"""
tests/systema/ui/test_input_and_interrupt.py

Three related changes to what happens while the assistant is busy:

  * the text box stays TYPABLE — only sending is held back, so you can draft
    your next message instead of waiting with a dead cursor;
  * because it is typable, the send gate had to move out of "the widget is
    disabled" and into send_message itself, or Enter would fire a second
    request into a turn already in flight;
  * Esc interrupts, so stopping does not mean reaching for the mouse.

The workmode interrupt dialog is covered here too: it stops the tool call that
is running RIGHT NOW, keeps its partial output and hands it back — it does not
kill the turn, which is what its old "Stop ongoing work? / Kill & Exit" copy
implied.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QPushButton                    # noqa: E402

from systema.ui.chat.input_dock import InputDockMixin              # noqa: E402
from systema.ui.dialogs.timeout_dialog import (                    # noqa: E402
    WorkmodeInterruptDialog)


# ── the input stays typable while busy ───────────────────────────────────────

class _Field:
    def __init__(self):
        self.enabled = None
        self.placeholder = ""
        self.text_input = self

    def setEnabled(self, v):
        self.enabled = v

    def setPlaceholderText(self, t):
        self.placeholder = t

    def setFocus(self):
        pass


class _Btn:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, v):
        self.enabled = v


class _Dock(InputDockMixin):
    def __init__(self):
        self.input_field = _Field()
        self.send_btn = _Btn()
        self.controller = type("C", (), {"ui": None})()


def test_the_box_stays_typable_while_busy():
    """The reported ask: the send button already holds the message back, so
    killing the text box too just threw away keystrokes."""
    d = _Dock()
    d.set_input_enabled(False)

    assert d.input_field.enabled is True, "the text box was disabled"
    assert d.send_btn.enabled is False, "sending must still be blocked"


def test_the_placeholder_says_you_can_type_ahead():
    d = _Dock()
    d.set_input_enabled(False)
    assert "Processing Request" in d.input_field.placeholder
    assert "type ahead" in d.input_field.placeholder


def test_the_placeholder_advertises_esc():
    """Esc-to-interrupt is invisible otherwise."""
    d = _Dock()
    d.set_input_enabled(False)
    assert "Esc" in d.input_field.placeholder


def test_enabling_restores_the_normal_placeholder():
    d = _Dock()
    d.set_input_enabled(False)
    d.set_input_enabled(True)
    assert "Send a message" in d.input_field.placeholder
    assert d.send_btn.enabled is True


def test_the_send_gate_is_recorded_for_send_message():
    """send_message reads this flag — with the widget always enabled, it is the
    only thing standing between Enter and a duplicate request."""
    d = _Dock()
    d.set_input_enabled(False)
    assert d._send_allowed is False
    d.set_input_enabled(True)
    assert d._send_allowed is True


def test_send_message_refuses_while_a_turn_is_in_flight():
    """Driven against the real method, so a future edit that drops the guard
    fails here."""
    from systema.ui.chat_window import ChatWindow

    calls = []

    class _Win:
        _send_allowed = False
        input_field = type("F", (), {"toPlainText": staticmethod(lambda: "hi")})()

        # send_message now checks for a slash command BEFORE the send gate
        # (see test_slash_commands). A plain message is not one.
        def try_run_command(self, text):
            return False

        def _finish_active_reveals(self):
            calls.append("ran")

    ChatWindow.send_message(_Win())
    assert calls == [], "send_message proceeded while sending was blocked"


# ── the workmode interrupt dialog ────────────────────────────────────────────

@pytest.fixture
def interrupt_dialog(qapp):
    made = []

    def _make(tool_name=""):
        d = WorkmodeInterruptDialog(None, tool_name=tool_name)
        made.append(d)
        return d

    yield _make
    for d in made:
        d.deleteLater()


def _texts(d):
    return " ".join(l.text() for l in d.findChildren(QLabel))


def test_it_names_the_step_it_will_stop(interrupt_dialog):
    d = interrupt_dialog("digital signature check")
    assert "digital signature check" in _texts(d)


def test_it_still_reads_sensibly_with_no_step_name(interrupt_dialog):
    d = interrupt_dialog()
    assert "the tool call" in _texts(d)


def test_it_says_the_conversation_survives(interrupt_dialog):
    """The old copy ("Stop ongoing work?" / "Kill & Exit") read like it killed
    the whole turn. It stops one step."""
    body = _texts(d := interrupt_dialog()).lower()
    assert "not the whole conversation" in body
    assert "kept" in body, "must say partial output is preserved"
    assert d is not None


def test_the_buttons_describe_their_action(interrupt_dialog):
    labels = [b.text() for b in interrupt_dialog().findChildren(QPushButton)]
    assert "Let it finish" in labels
    assert any("Interrupt" in t for t in labels)
    assert not any("Kill" in t for t in labels), (
        "nothing is killed but the current step, and the agent is not exited")


def test_no_colour_is_hardcoded_in_either_dialog():
    """Both dialogs in this module sat at the obsidian-blue set, so they stayed
    blue-grey under every other theme. Comments are stripped first — the ones
    explaining the fix legitimately quote the old literals."""
    import re
    from pathlib import Path
    from systema.ui.dialogs import timeout_dialog

    code = []
    for line in Path(timeout_dialog.__file__).read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        code.append(stripped)
    offenders = [m for line in code for m in re.findall(r"#[0-9A-Fa-f]{6}\b", line)]

    assert offenders == [], f"hardcoded colours instead of theme values: {offenders}"


def test_both_dialogs_pull_from_the_live_palette():
    import inspect
    from systema.ui.dialogs import timeout_dialog
    for cls in (timeout_dialog.TimeoutDialog,
                timeout_dialog.WorkmodeInterruptDialog):
        assert "_live_palette" in inspect.getsource(cls), cls.__name__


def test_a_reason_is_optional_and_trimmed(interrupt_dialog):
    d = interrupt_dialog()
    d._reason_input.setPlainText("  wrong folder  ")
    d._confirm()
    assert d.reason_text == "wrong folder"

    d2 = interrupt_dialog()
    d2._confirm()
    assert d2.reason_text == ""
