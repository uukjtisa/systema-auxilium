"""
tests/systema/execution/test_batch_card_attribution.py

Two python_interpreter calls in ONE response used to produce two cards that
both lied:

  * card 1 froze with an EMPTY output (it was finalized from whatever the 120ms
    live poll had captured, which for a fast step is nothing);
  * card 2 was overwritten with the WHOLE combined observation
    ("=== Result 1/2 ... === Result 2/2 ...");
  * both cards carried the SAME annotation, because every call in a batch is
    parsed before any of them runs and the label lived in one shared slot.

Observed in session `Huawei_Bootloader_Tool_Scan_07_24_2026...json`. The fix
gives each step its own card via ApprovalSignal.work_step_output. These tests
pin the pairing: card k shows call k's code, call k's output and call k's label.
"""
import types

import pytest


@pytest.fixture
def rig(tool_manager, monkeypatch):
    """A real ToolManager + AIEngine._run_tool_batch, with the interpreter and
    the chat window replaced by recorders."""
    from systema.engine.ai_engine import AIEngine

    tm = tool_manager
    tm.allow_workmode = True
    # Approve everything: this test is about card plumbing, not the guard.
    tm._check_supervised_execution = lambda code, kind: (True, code)

    outputs = {}          # code -> stdout the fake interpreter produces

    class _Interp:
        def execute(self, code, timeout=None, timeout_callback=None):
            return {'success': True, 'stdout': outputs.get(code, ''),
                    'stderr': '', 'result': None, 'error': None}

        def peek_live_output(self):
            return ''

    tm.tools['python'] = _Interp()

    class _Chat:
        """Records exactly what each card was finalized with."""
        def __init__(self):
            self.cards = []

        def finalize_code_step(self, code, output, annotation):
            self.cards.append({'code': code, 'output': output,
                               'annotation': annotation})

    chat = _Chat()
    # The real delivery path: work_step_output -> ToolManager._deliver_work_step_output
    # -> chat.finalize_code_step. Same-thread emission delivers synchronously.
    tm._get_chat = lambda: chat

    eng = object.__new__(AIEngine)
    eng.tool_manager = tm
    eng.skill_manager = None
    eng._malformed_step_retries = 0
    eng._emit_work_narration = lambda text: False
    eng._append_assistant = lambda text: {}
    eng._inject_pending_format_reminder = lambda: None
    return types.SimpleNamespace(tm=tm, eng=eng, chat=chat, outputs=outputs)


def _batch(*calls):
    return [{'tool': 'python_interpreter', 'spec': code, 'call_id': None,
             'annotation': ann} for code, ann in calls]


def test_each_interpreter_call_gets_its_own_card(rig):
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('one')"] = "ONE"
    rig.outputs["print('two')"] = "TWO"
    batch = _batch(("print('one')", "list executables"),
                   ("print('two')", "digital signature check"))

    AIEngine._run_tool_batch(rig.eng, "ai text", batch, "", None)

    cards = rig.chat.cards
    assert len(cards) == 2
    assert cards[0]['code'] == "print('one')"
    assert cards[1]['code'] == "print('two')"


def test_first_call_output_is_not_empty(rig):
    """The headline symptom: call 1's card showed ''."""
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('one')"] = "ONE"
    rig.outputs["print('two')"] = "TWO"

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print('one')", "a"),
                                                 ("print('two')", "b")), "", None)

    assert "ONE" in rig.chat.cards[0]['output']
    assert rig.chat.cards[0]['output'].strip() != ""


def test_no_card_carries_the_combined_batch_observation(rig):
    """Call 2's card used to hold '=== Result 1/2 ...' — every call's output
    glued together — making it impossible to tell which output was whose."""
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('one')"] = "ONE"
    rig.outputs["print('two')"] = "TWO"

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print('one')", "a"),
                                                 ("print('two')", "b")), "", None)

    for card in rig.chat.cards:
        assert "=== Result" not in card['output'], \
            f"a card was finalized with the whole batch observation: {card}"
    # ...while the MODEL still receives the combined observation.
    assert "=== Result 1/2" in rig.tm.work.last_output
    assert "=== Result 2/2" in rig.tm.work.last_output


def test_outputs_are_not_cross_attributed(rig):
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('one')"] = "ONE"
    rig.outputs["print('two')"] = "TWO"

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print('one')", "a"),
                                                 ("print('two')", "b")), "", None)

    assert "TWO" not in rig.chat.cards[0]['output']
    assert "ONE" not in rig.chat.cards[1]['output']


def test_each_card_keeps_its_own_annotation(rig):
    """Both cards used to show the label of whichever call was parsed last."""
    from systema.engine.ai_engine import AIEngine

    AIEngine._run_tool_batch(
        rig.eng, "", _batch(("print(1)", "list executables"),
                            ("print(2)", "digital signature check")), "", None)

    assert rig.chat.cards[0]['annotation'] == "list executables"
    assert rig.chat.cards[1]['annotation'] == "digital signature check"


def test_annotation_snapshot_survives_a_later_call_relabelling(rig):
    """The root cause, directly: converting call 2 overwrites the shared slot
    BEFORE call 1 executes. The batch entry's snapshot must win."""
    from systema.engine.ai_engine import AIEngine

    rig.tm.work.interpreter.last_annotation = "stale label from call 2"

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print(1)", "call one label")),
                             "", None)

    assert rig.chat.cards[0]['annotation'] == "call one label"


def test_blocked_call_still_gets_a_card(rig):
    """A call that never reaches execution (no live card, step_seq unchanged)
    must still be visible — not silently swallowed by the dedup guard."""
    from systema.engine.ai_engine import AIEngine

    rig.tm.allow_workmode = False

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print(1)", "blocked")), "", None)

    assert len(rig.chat.cards) == 1
    assert "disabled for this session" in rig.chat.cards[0]['output']


def test_single_call_keeps_its_raw_output(rig):
    """One call must not gain '=== Result 1/1 ===' framing anywhere."""
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('solo')"] = "SOLO"

    AIEngine._run_tool_batch(rig.eng, "", _batch(("print('solo')", "solo")), "", None)

    assert "SOLO" in rig.chat.cards[0]['output']
    assert "=== Result" not in rig.tm.work.last_output


def test_mixed_batch_only_cards_the_interpreter_calls(rig):
    """A batch of grep + interpreter must produce exactly one code card."""
    from systema.engine.ai_engine import AIEngine

    rig.outputs["print('x')"] = "X"
    batch = [{'tool': 'grep', 'spec': {'pattern': 'zzz', 'path': '.',
                                       'error': 'grep needs a regex pattern.'},
              'call_id': None, 'annotation': 'search'},
             {'tool': 'python_interpreter', 'spec': "print('x')",
              'call_id': None, 'annotation': 'run it'}]

    AIEngine._run_tool_batch(rig.eng, "", batch, "", None)

    assert len(rig.chat.cards) == 1
    assert rig.chat.cards[0]['code'] == "print('x')"
    assert rig.chat.cards[0]['annotation'] == "run it"
