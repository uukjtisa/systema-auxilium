"""
tests/systema/execution/test_skill_action_cards.py

load_skill / unload_skill were the only agent actions that ran INVISIBLY — the
agent could swap its own instructions mid-task and the transcript showed
nothing. Every action now emits a card through the central dispatcher, including
the rejected ones (a refused load is exactly when you want to see why).
"""
import types

import pytest


@pytest.fixture
def rig(tool_manager):
    from systema.engine.ai_engine import AIEngine

    tm = tool_manager
    tm.allow_workmode = True
    cards = []
    tm.approval_signal.tool_card.connect(cards.append)

    eng = object.__new__(AIEngine)
    eng.tool_manager = tm
    eng._malformed_step_retries = 0
    eng._emit_work_narration = lambda text: False
    eng._append_assistant = lambda text: {}
    eng._inject_pending_format_reminder = lambda: None
    eng.include_image_tools = False
    return types.SimpleNamespace(tm=tm, eng=eng, cards=cards)


def _call(tool, skill):
    return [{'tool': tool, 'spec': skill, 'call_id': None, 'annotation': None}]


def test_loading_a_skill_emits_a_card(rig):
    from systema.engine.ai_engine import AIEngine
    rig.eng.skill_manager = types.SimpleNamespace(
        load_skill=lambda n: (True, "loaded"),
        unload_skill=lambda n: (True, "unloaded"))

    AIEngine._run_tool_batch(rig.eng, "", _call('load_skill', 'pptx'), "", None)

    assert len(rig.cards) == 1
    card = rig.cards[0]
    assert card['card_type'] == 'skill_action'
    assert card['action'] == 'load'
    assert card['skill'] == 'pptx'
    assert card['ok'] is True


def test_unloading_a_skill_emits_a_card(rig):
    from systema.engine.ai_engine import AIEngine
    rig.eng.skill_manager = types.SimpleNamespace(
        load_skill=lambda n: (True, "loaded"),
        unload_skill=lambda n: (True, "unloaded"))

    AIEngine._run_tool_batch(rig.eng, "", _call('unload_skill', 'pptx'), "", None)

    assert rig.cards[0]['action'] == 'unload'
    assert rig.cards[0]['skill'] == 'pptx'


def test_a_rejected_load_still_shows_a_card_with_the_reason(rig):
    """The most important case to see: the agent asked for a skill and did NOT
    get it."""
    from systema.engine.ai_engine import AIEngine
    rig.eng.skill_manager = types.SimpleNamespace(
        load_skill=lambda n: (False, "skill not found"),
        unload_skill=lambda n: (True, ""))

    AIEngine._run_tool_batch(rig.eng, "", _call('load_skill', 'ghost'), "", None)

    card = rig.cards[0]
    assert card['ok'] is False
    assert "not found" in card['detail']
    assert "REJECTED" in rig.tm.work.last_output


def test_no_skill_manager_is_reported_not_swallowed(rig):
    from systema.engine.ai_engine import AIEngine
    rig.eng.skill_manager = None

    AIEngine._run_tool_batch(rig.eng, "", _call('load_skill', 'pptx'), "", None)

    assert rig.cards[0]['ok'] is False


def test_every_call_in_a_skill_batch_gets_its_own_card(rig):
    from systema.engine.ai_engine import AIEngine
    rig.eng.skill_manager = types.SimpleNamespace(
        load_skill=lambda n: (True, ""), unload_skill=lambda n: (True, ""))

    batch = _call('load_skill', 'a') + _call('unload_skill', 'b')
    AIEngine._run_tool_batch(rig.eng, "", batch, "", None)

    assert [(c['action'], c['skill']) for c in rig.cards] == [
        ('load', 'a'), ('unload', 'b')]
