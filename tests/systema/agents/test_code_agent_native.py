"""
tests/systema/agents/test_code_agent_native.py

The code-approval sub-agent's NATIVE path.

It used to call `mod.chat_tools(...)` directly — an entry point from the
retired first provider contract that no shipped provider has ever defined. The
resulting AttributeError was swallowed by a bare `except Exception`, reported
as "native tool-call error", and returned "" — so the in-dialog code editor's
native mode was a silent no-op with every bundled provider, and nothing failed
loudly enough to notice. It now goes through `provider_contract.invoke()`, the
same single call path the main engine uses.
"""
import types

import pytest

from systema.agents.code_agent import CodeAgent


def _agent(**kw):
    calls = {'messages': [], 'proposals': []}
    agent = CodeAgent(
        code="print('hello')",
        execution_type="python",
        on_message=lambda role, text: calls['messages'].append((role, text)),
        on_proposal=lambda code, why: calls['proposals'].append((code, why)),
        **kw)
    return agent, calls


def _provider(result, seen=None):
    """A provider script defining ONLY the one contract entry point."""
    m = types.ModuleType("fake_provider")
    m.SUPPORTS_NATIVE_TOOLS = True

    def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
        if seen is not None:
            seen.update(system_prompt=system_prompt, messages=messages,
                        tools=tools, stream=stream)
        return result

    m.chat = chat
    return m


# ── the fix ──────────────────────────────────────────────────────────────────

def test_native_run_reaches_a_chat_only_provider():
    """The regression: a module with chat() and no chat_tools() must work."""
    seen = {}
    mod = _provider({"content": "here is a safer version",
                     "tool_calls": [{"id": "1", "name": "propose_edit",
                                     "arguments": {"code": "print('safe')",
                                                   "why": "scoped it"}}]}, seen)
    agent, calls = _agent()

    text = agent._run_native(mod, "make it safer")

    assert text == "here is a safer version"
    assert calls['proposals'] == [("print('safe')", "scoped it")]
    assert agent.last_proposal == "print('safe')"
    assert not any(role == "system" for role, _ in calls['messages']), \
        "a swallowed provider error must not be how this path ends"
    assert seen['tools'] and seen['tools'][0]['name'] == 'propose_edit'
    assert seen['stream'] is False


def test_the_reply_is_read_from_content_not_the_retired_text_key():
    """normalize_result no longer maps the retired chat_tools 'text' key onto
    content, so reading res['text'] here would silently return nothing."""
    agent, calls = _agent()
    text = agent._run_native(_provider({"content": "explained", "tool_calls": []}),
                             "explain")
    assert text == "explained"
    assert calls['messages'] == [("agent", "explained")]


def test_a_module_defining_only_the_retired_entry_point_is_not_called():
    """chat_tools is gone. A module carrying only that must NOT be invoked
    through it — invoke() returns None and the run ends without a proposal."""
    mod = types.ModuleType("retired_provider")
    mod.SUPPORTS_NATIVE_TOOLS = True
    mod.chat_tools = lambda *a, **k: pytest.fail("the retired path was called")

    agent, calls = _agent()
    assert agent._run_native(mod, "anything") == ""
    assert calls['proposals'] == []


# ── behaviour preserved around the fix ───────────────────────────────────────

def test_string_arguments_are_json_decoded():
    """Some dialects hand tool arguments back as a JSON string."""
    mod = _provider({"content": "", "tool_calls": [
        {"id": "1", "name": "propose_edit",
         "arguments": '{"code": "print(1)", "why": "shorter"}'}]})
    agent, calls = _agent()
    agent._run_native(mod, "shorten it")
    assert calls['proposals'] == [("print(1)", "shorter")]


def test_a_fence_written_as_text_still_surfaces_as_a_proposal():
    """A native model that ignores the tools channel and writes the fence in
    prose must still produce a diff — and the fence is stripped from the text."""
    mod = _provider({"content": "sure thing\n```propose_edit: guarded\n"
                                "print('guarded')\n```", "tool_calls": []})
    agent, calls = _agent()
    text = agent._run_native(mod, "guard it")
    assert calls['proposals'] == [("print('guarded')", "guarded")]
    assert "```" not in text and text.startswith("sure thing")


def test_a_tool_call_that_is_not_propose_edit_is_ignored():
    mod = _provider({"content": "no", "tool_calls": [
        {"id": "1", "name": "run_python", "arguments": {"code": "rm -rf /"}}]})
    agent, calls = _agent()
    agent._run_native(mod, "do it")
    assert calls['proposals'] == []
    assert agent.last_proposal is None


def test_an_empty_proposal_is_refused():
    mod = _provider({"content": "", "tool_calls": [
        {"id": "1", "name": "propose_edit", "arguments": {"code": "   "}}]})
    agent, calls = _agent()
    agent._run_native(mod, "edit")
    assert calls['proposals'] == []


def test_a_raising_provider_is_reported_not_swallowed_silently():
    mod = types.ModuleType("boom_provider")
    mod.SUPPORTS_NATIVE_TOOLS = True

    def chat(*a, **k):
        raise RuntimeError("upstream 502")

    mod.chat = chat
    agent, calls = _agent()
    assert agent._run_native(mod, "x") == ""
    assert calls['messages'] and calls['messages'][0][0] == "system"
    assert "upstream 502" in calls['messages'][0][1]
