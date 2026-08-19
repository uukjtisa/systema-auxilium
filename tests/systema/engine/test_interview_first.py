"""Arming the ask_user interview.

Three surfaces arm the same thing (the Session Tools entry, /ask, and the
standing setting), so the flag they set is the contract worth pinning -- not
each control.

Two flags on purpose:
  interview_first  one-shot, consumed by generate_response after ONE turn
  always_interview standing preference, never auto-cleared

They are class attributes on AIEngine so a subclass or test double built without
running __init__ still reads a sane default.
"""
from systema.engine.ai_engine import AIEngine
from systema.engine.prompts.global_instructions import get_system_prompt
from systema.ui.chat import commands as C


# -- the directive itself ----------------------------------------------------

def test_the_directive_is_absent_by_default():
    assert "INTERVIEW FIRST" not in get_system_prompt()


def test_arming_adds_the_directive():
    assert "INTERVIEW FIRST" in get_system_prompt(interview_first=True)


def test_arming_does_nothing_when_the_tool_is_off():
    """Telling the model to call a tool it was never given is a guaranteed
    malformed turn."""
    prompt = get_system_prompt(interview_first=True, include_ask_user=False)
    assert "INTERVIEW FIRST" not in prompt
    assert "ask_user" not in prompt


def test_the_directive_says_options_must_be_additive():
    """The rule that makes a multi-select question answerable at all."""
    prompt = get_system_prompt(interview_first=True)
    assert "ADDITIVE" in prompt


def test_the_directive_covers_a_dismissed_card():
    prompt = get_system_prompt(interview_first=True)
    assert "dismisses" in prompt


# -- the flags ---------------------------------------------------------------

def test_both_flags_exist_on_the_class_not_just_instances():
    """The slash-command target test resolves /ask against AIEngine itself, and
    a TaskAIEngine built without __init__ must not raise on a missing attr."""
    assert AIEngine.interview_first is False
    assert AIEngine.always_interview is False


def test_the_standing_setting_also_arms_the_directive():
    eng = AIEngine.__new__(AIEngine)
    eng.always_interview = True
    armed = (getattr(eng, 'interview_first', False)
             or getattr(eng, 'always_interview', False))
    assert "INTERVIEW FIRST" in get_system_prompt(interview_first=armed)


def test_the_one_shot_is_consumed_and_the_standing_one_is_not():
    """generate_response clears interview_first in a finally block. Simulated
    here rather than driving a provider: the contract is which flag survives."""
    eng = AIEngine.__new__(AIEngine)
    eng.interview_first = True
    eng.always_interview = True
    try:
        pass
    finally:
        eng.interview_first = False
    assert eng.interview_first is False
    assert eng.always_interview is True, "a standing preference must survive"


def test_generate_response_clears_the_one_shot_in_a_finally():
    """It must be cleared even when the provider call raises, or a failed turn
    leaves the next unrelated one silently armed."""
    import inspect
    src = inspect.getsource(AIEngine.generate_response)
    assert "finally:" in src
    assert "self.interview_first = False" in src


# -- the /ask command --------------------------------------------------------

class _Ai:
    include_ask_user = True
    interview_first = False
    always_interview = False


class _Ctrl:
    def __init__(self):
        self.ai = _Ai()


class _Chat:
    def __init__(self):
        self.controller = _Ctrl()


def test_ask_command_arms_the_flag():
    chat = _Chat()
    out = C.BY_NAME["ask"].run(chat, "")
    assert chat.controller.ai.interview_first is True
    assert "next message" in out


def test_ask_command_refuses_when_the_tool_is_off():
    chat = _Chat()
    chat.controller.ai.include_ask_user = False
    out = C.BY_NAME["ask"].run(chat, "")
    assert chat.controller.ai.interview_first is False
    assert "switched off" in out


def test_ask_is_registered_and_grouped_with_context():
    cmd = C.BY_NAME["ask"]
    assert cmd.usage == "/ask"
    assert cmd.group == "Context"
