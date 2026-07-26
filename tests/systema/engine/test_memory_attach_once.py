"""
tests/systema/engine/test_memory_attach_once.py

RAG recall runs on EVERY user turn, so a memory that stays relevant across
consecutive messages was attached again each time: duplicate memory cards in the
chat, the same text repeated in the context the model reads, and tokens paid for
it per turn.

The attach-once guard derives "already attached" from the conversation history
itself, so it survives a session reload and correctly RE-allows a memory whose
entry was later removed (compaction / revert) — at that point it really is gone
from the context.
"""
import types

from systema.engine.ai_engine import AIEngine


def _engine(recalled):
    eng = object.__new__(AIEngine)
    eng.conversation_history = []
    eng._pending_memory_context = ""
    eng._pending_memory_widget = None
    eng.controller = None
    eng.memory_manager = types.SimpleNamespace(
        is_ready=True, recall=lambda **kw: list(recalled))
    eng.settings_callback = lambda: {
        'memory_enabled': True, 'memory_recall_mode': 'rag',
        'memory_threshold': 0.4, 'memory_max_results': 3}
    return eng


MEM_A = {'id': 'a1', 'text': 'user prefers dark themes', 'created_at': '',
         'similarity': 0.9}
MEM_B = {'id': 'b2', 'text': 'user lives in Manila', 'created_at': '',
         'similarity': 0.8}


def _memory_events(eng):
    return [m for m in eng.conversation_history
            if m.get('role') == 'ui_event' and m.get('_type') == 'memory_context']


def test_first_recall_attaches_the_memory():
    eng = _engine([MEM_A])
    AIEngine._inject_memories(eng, "what theme do I like?")
    assert len(_memory_events(eng)) == 1


def test_the_same_memory_is_not_attached_twice():
    eng = _engine([MEM_A])
    AIEngine._inject_memories(eng, "turn 1")
    AIEngine._inject_memories(eng, "turn 2")
    AIEngine._inject_memories(eng, "turn 3")

    assert len(_memory_events(eng)) == 1, \
        "the same memory was attached again on a later turn"


def test_a_new_memory_still_attaches_alongside_an_old_one():
    eng = _engine([MEM_A])
    AIEngine._inject_memories(eng, "turn 1")
    eng.memory_manager.recall = lambda **kw: [MEM_A, MEM_B]

    AIEngine._inject_memories(eng, "turn 2")

    events = _memory_events(eng)
    assert len(events) == 2
    # ...and the second card carries ONLY the new memory, not the repeat.
    second = [p['id'] for p in events[1]['_memories_preview']]
    assert second == ['b2']


def test_no_card_is_created_when_everything_was_already_attached():
    eng = _engine([MEM_A])
    AIEngine._inject_memories(eng, "turn 1")
    eng._pending_memory_widget = "stale"

    AIEngine._inject_memories(eng, "turn 2")

    assert len(_memory_events(eng)) == 1
    assert eng._pending_memory_widget is None, \
        "a skipped recall still queued a widget for the UI"


def test_a_memory_becomes_eligible_again_once_its_entry_is_gone():
    """Compaction / revert can remove the entry — the memory is then genuinely
    out of context, so re-attaching it is correct."""
    eng = _engine([MEM_A])
    AIEngine._inject_memories(eng, "turn 1")
    eng.conversation_history = [m for m in eng.conversation_history
                                if m.get('_type') != 'memory_context']

    AIEngine._inject_memories(eng, "turn 2")

    assert len(_memory_events(eng)) == 1


def test_legacy_sessions_without_ids_still_dedup_on_text():
    eng = _engine([MEM_A])
    eng.conversation_history.append({
        'role': 'ui_event', '_type': 'memory_context',
        'content': 'old', '_memories_preview': ['user prefers dark themes'],
    })

    AIEngine._inject_memories(eng, "turn 1")

    assert len(_memory_events(eng)) == 1     # only the legacy one


def test_inject_all_mode_does_no_recall_at_all():
    eng = _engine([MEM_A])
    eng.settings_callback = lambda: {'memory_enabled': True,
                                     'memory_recall_mode': 'inject_all'}
    AIEngine._inject_memories(eng, "turn 1")
    assert _memory_events(eng) == []
