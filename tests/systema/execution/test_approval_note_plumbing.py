"""
tests/systema/execution/test_approval_note_plumbing.py

The approval window's two optional messages have to REACH the model, in the
observation for the step they belong to:

  * Reject → "Code execution rejected by user / REASON: ..."   (already existed)
  * Accept → "USER NOTE (approved with a message — follow it): ..."  (new)

Both are one-shot. A note left over from a previous approval turning up on a
later step would read as an instruction the user never gave for that step, so
these check the clearing as hard as the delivery.

The dialog itself is covered in tests/systema/ui/test_approval_messages.py;
here the dialog is bypassed and the ToolManager field set directly, which is
what the main thread does before releasing the worker.
"""
import pytest

from systema.execution import capabilities as caps


def _observation(tm, code="1 + 1"):
    """Run a step with supervision off, so the approval ladder auto-approves
    and we see the observation the model would get."""
    tm.supervised_execution = False
    return tm.run_python_interpreter(code)


# ── the approval note ────────────────────────────────────────────────────────

def test_the_note_reaches_the_observation(tool_manager):
    tool_manager._last_approval_note = "write to data/ not the desktop"

    out = _observation(tool_manager)

    assert "USER NOTE" in out
    assert "write to data/ not the desktop" in out


def test_the_note_is_labelled_as_an_instruction_not_output(tool_manager):
    """It sits beside STDOUT/RESULT, so it has to announce what it is or it
    reads as something the program printed."""
    tool_manager._last_approval_note = "keep it read-only"

    out = _observation(tool_manager)

    assert "approved with a message" in out
    assert "follow it" in out


def test_the_note_is_consumed_after_one_step(tool_manager):
    """Otherwise it re-attaches to every later step in the same work loop."""
    tool_manager._last_approval_note = "just this once"

    first = _observation(tool_manager)
    second = _observation(tool_manager)

    assert "just this once" in first
    assert "just this once" not in second


def test_no_note_means_no_section(tool_manager):
    out = _observation(tool_manager, "print('plain')")
    assert "USER NOTE" not in out


def test_an_empty_note_is_not_a_section(tool_manager):
    tool_manager._last_approval_note = "   "
    assert "USER NOTE" not in _observation(tool_manager)


def test_the_note_rides_alongside_real_output(tool_manager):
    """It must not replace or hide what the step actually did.

    Checked through RESULT rather than STDOUT: stdout capture needs the wrapper
    main.py installs on sys.stdout, which pytest's own capture displaces, so a
    STDOUT assertion here would pass whether or not the output survived.
    """
    tool_manager._last_approval_note = "noted"

    out = _observation(tool_manager, "6 * 7")

    assert "RESULT" in out and "42" in out
    assert "noted" in out


# ── the two fields stay independent ──────────────────────────────────────────

def test_the_fields_start_empty(tool_manager):
    assert tool_manager._last_approval_note == ""
    assert tool_manager._last_reject_reason == ""


def test_a_rejection_reason_is_not_delivered_as_an_approval_note(tool_manager):
    """They are separate fields for a reason — a refusal explanation must never
    be handed to the model as an instruction it should follow."""
    tool_manager._last_reject_reason = "that path is wrong"

    out = _observation(tool_manager)

    assert "USER NOTE" not in out
    assert "that path is wrong" not in out
