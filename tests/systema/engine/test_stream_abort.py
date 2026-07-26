"""
tests/systema/engine/test_stream_abort.py

Pressing Stop mid-stream must be a CLEAN abort. The half-written assistant turn
used to survive in conversation_history — so it was written to the session JSON,
came back on reload as a truncated ghost message, and was re-sent to the
provider as though the model had actually said it.

`AIEngine.discard_streamed_partial()` removes exactly that fragment and nothing
else: the user's prompt, completed earlier steps of a work chain, tool cards and
prior turns all stay.
"""
from systema.engine.ai_engine import AIEngine


def _engine(history):
    eng = object.__new__(AIEngine)
    eng.conversation_history = list(history)
    return eng


def test_partial_assistant_turn_is_dropped():
    eng = _engine([
        {'role': 'user', 'content': 'write me a long report'},
        {'role': 'assistant', 'content': 'Here is the begin'},   # cut mid-word
    ])

    assert AIEngine.discard_streamed_partial(eng) is True
    assert [m['role'] for m in eng.conversation_history] == ['user']


def test_the_aborted_turns_thinking_card_goes_with_it():
    """The thinking ui_event is persisted by the GUI when the stream ends, so
    it lands AFTER the worker's assistant entry — both belong to the aborted
    turn and both must go."""
    eng = _engine([
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': 'partial'},
        {'role': 'ui_event', '_type': 'thinking', 'content': 'Thinking',
         '_thinking': 'half a thought'},
    ])

    AIEngine.discard_streamed_partial(eng)

    assert [m['role'] for m in eng.conversation_history] == ['user']


def test_an_earlier_steps_thinking_card_is_never_eaten():
    """Guard rail on the rule above: a thinking card that PRECEDES the partial
    assistant entry belongs to an earlier, completed response of the same
    merged turn — removing it would delete reasoning the user already saw
    finish (and which the session file still holds)."""
    eng = _engine([
        {'role': 'user', 'content': 'hi'},
        {'role': 'ui_event', '_type': 'thinking', 'content': 'Thinking',
         '_thinking': 'step 1 reasoning, completed'},
        {'role': 'assistant', 'content': 'partial'},
    ])

    AIEngine.discard_streamed_partial(eng)

    assert [m['role'] for m in eng.conversation_history] == ['user', 'ui_event']
    assert eng.conversation_history[-1]['_thinking'] == 'step 1 reasoning, completed'


def test_the_users_prompt_is_never_removed():
    eng = _engine([{'role': 'user', 'content': 'keep me'}])

    assert AIEngine.discard_streamed_partial(eng) is False
    assert eng.conversation_history == [{'role': 'user', 'content': 'keep me'}]


def test_only_the_trailing_turn_is_dropped():
    """An earlier COMPLETED exchange must survive the abort untouched."""
    eng = _engine([
        {'role': 'user', 'content': 'first question'},
        {'role': 'assistant', 'content': 'first complete answer'},
        {'role': 'user', 'content': 'second question'},
        {'role': 'assistant', 'content': 'second partial'},
    ])

    AIEngine.discard_streamed_partial(eng)

    assert [m['content'] for m in eng.conversation_history] == [
        'first question', 'first complete answer', 'second question']


def test_completed_work_steps_survive_an_interrupted_chain():
    """Interrupting a work chain must not erase the tool cards / steps the user
    already watched complete — only the reply that was mid-flight."""
    eng = _engine([
        {'role': 'user', 'content': 'do the thing'},
        {'role': 'assistant', 'content': 'step 1 tool call'},
        {'role': 'ui_event', 'content': 'Code executed', '_code': 'print(1)',
         '_output': 'ONE'},
        {'role': 'assistant', 'content': 'step 2 partial'},
    ])

    AIEngine.discard_streamed_partial(eng)

    kept = eng.conversation_history
    assert len(kept) == 3
    assert kept[-1]['content'] == 'Code executed'
    assert kept[-1]['_output'] == 'ONE'


def test_nothing_to_discard_on_an_empty_history():
    eng = _engine([])
    assert AIEngine.discard_streamed_partial(eng) is False


def test_a_code_card_alone_is_not_mistaken_for_a_partial_reply():
    eng = _engine([
        {'role': 'user', 'content': 'run it'},
        {'role': 'ui_event', 'content': 'Code executed', '_code': 'x=1',
         '_output': ''},
    ])

    assert AIEngine.discard_streamed_partial(eng) is False
    assert len(eng.conversation_history) == 2
