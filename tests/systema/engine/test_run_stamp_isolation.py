"""
tests/systema/engine/test_run_stamp_isolation.py

The `_run` log stamp is bookkeeping for humans. It must never reach a provider.

`_get_history_with_memory` passes plain entries through by reference, so the
thing that actually protects the payload is `_extract_system_and_convo`, which
rebuilds a fresh {'role', 'content'} dict per turn. That is easy to "simplify"
into a pass-through during a future refactor, so it gets a test.
"""
from systema.common.run_context import STAMP_KEY
from systema.engine.ai_engine import AIEngine

_STAMP = {'log': 'log_2026_jul_28_tuesday_h03_m38_s16_ms53_am.txt',
          'at': '03:39:28.683', 'pid': 8104}


def _messages():
    return [
        {'role': 'system', 'content': 'you are a helpful assistant'},
        {'role': 'user', 'content': 'hello', STAMP_KEY: _STAMP},
        {'role': 'assistant', 'content': 'hi', STAMP_KEY: _STAMP},
    ]


def test_the_log_stamp_never_reaches_the_provider_payload():
    _system, convo = AIEngine._extract_system_and_convo(_messages())

    assert convo, "sanity: there should be turns to inspect"
    for turn in convo:
        assert STAMP_KEY not in turn, f"stamp leaked into a provider turn: {turn}"


def test_the_turns_still_carry_their_real_content():
    """The isolation must not be achieved by dropping the message."""
    system, convo = AIEngine._extract_system_and_convo(_messages())

    assert system == 'you are a helpful assistant'
    assert [t['content'] for t in convo] == ['hello', 'hi']


def test_a_stamped_system_entry_is_also_clean():
    msgs = [
        {'role': 'system', 'content': 'primary prompt'},
        {'role': 'system', 'content': 'a later system note', STAMP_KEY: _STAMP},
    ]

    _system, convo = AIEngine._extract_system_and_convo(msgs)

    for turn in convo:
        assert STAMP_KEY not in turn
