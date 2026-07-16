"""
Tests for the NATIVE tool-calling pipeline purity
(systema/engine/ai_engine.py :: _process_native_response & friends).

Native turns must dispatch their STRUCTURED tool calls — never fence-parse
the reconstructed text — and nothing fence-shaped may leak into what the
provider sees on later turns. The session keeps BOTH representations
(content fences for compat replay + _tool_calls/_assistant_text metadata).
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

BT = "`" * 3


@pytest.fixture
def eng(qapp):
    from systema.engine.ai_engine import AIEngine
    return AIEngine(settings_callback=lambda: {"tool_calling_mode": "native"})


def _forbid_parsing(eng, monkeypatch):
    """Native dispatch must never touch the compat fence machinery."""
    def _boom(*a, **k):
        raise AssertionError("compat fence machinery invoked on a native turn")
    monkeypatch.setattr(eng.tool_manager, "_parse_fence", _boom)
    monkeypatch.setattr(eng.tool_manager, "recover_unclosed_tool_fence", _boom)
    monkeypatch.setattr(eng.tool_manager, "enforce_single_exec_policy", _boom)


def test_native_naming_turn_needs_no_parser(eng, monkeypatch):
    _forbid_parsing(eng, monkeypatch)
    eng.last_tool_transport = "native"
    eng._pending_native = {
        "text": "Hello there!",
        "tool_calls": [{"id": "c1", "name": "set_session_name",
                        "arguments": {"name": "My Chat"}}],
    }
    ai_text = f"Hello there!\n\n{BT}set_session_name\nMy Chat\n{BT}"
    r = eng._process_ai_response(ai_text)
    assert r["session_name"] == "My Chat"
    assert r["response"] == "Hello there!"
    assert r["is_working"] is False
    entry = eng.conversation_history[-1]
    assert entry["role"] == "assistant"
    assert entry["_tool_calls"][0]["name"] == "set_session_name"
    # the compat view (fences) is preserved for a cross-mode reload
    assert entry["content"] == ai_text


def test_native_read_file_dispatches_structurally(eng, monkeypatch, sample_tree):
    _forbid_parsing(eng, monkeypatch)
    eng.last_tool_transport = "native"
    target = sample_tree / "src" / "a.py"
    eng._pending_native = {
        "text": "",
        "tool_calls": [{"id": "c1", "name": "read_file",
                        "arguments": {"path": str(target)}}],
    }
    r = eng._process_ai_response(f"{BT}read_file\n{target}\n{BT}")
    assert r["has_work_call"] is True and r["is_working"] is True
    assert "def send" in eng.tool_manager.work.last_output
    assert eng.tool_manager.work.last_tool == "read_file"


def test_native_text_only_turn_never_recovers_fences(eng, monkeypatch):
    _forbid_parsing(eng, monkeypatch)
    eng.last_tool_transport = "native"
    eng._pending_native = None
    # An unclosed imitation fence in plain text must NOT be auto-closed + run
    # (compat's recovery guard rail stays compat-only).
    ai_text = "I'll check the file\n```readfile: x\nsome text"
    r = eng._process_ai_response(ai_text)
    assert r["is_working"] is False
    assert eng.tool_manager.work.is_working is False
    assert r["response"] == ai_text


def test_native_violation_prompt_precedes_assistant_turn(eng, monkeypatch, sample_tree):
    from systema.engine.prompts.global_instructions import (
        EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
    _forbid_parsing(eng, monkeypatch)
    eng.last_tool_transport = "native"
    target = sample_tree / "notes.txt"
    eng._pending_native = {
        "text": "", "violation": True,
        "tool_calls": [{"id": "c1", "name": "read_file",
                        "arguments": {"path": str(target)}}],
    }
    eng._process_ai_response(f"{BT}read_file\n{target}\n{BT}")
    assert eng.conversation_history[0]["role"] == "system"
    assert (eng.conversation_history[0]["content"]
            == EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
    assert eng.conversation_history[1]["role"] == "assistant"


def test_native_parallel_naming_plus_exec(eng, monkeypatch, sample_tree):
    _forbid_parsing(eng, monkeypatch)
    eng.last_tool_transport = "native"
    target = sample_tree / "notes.txt"
    eng._pending_native = {
        "text": "Reading it now.",
        "tool_calls": [
            {"id": "c1", "name": "set_session_name", "arguments": {"name": "Notes"}},
            {"id": "c2", "name": "read_file", "arguments": {"path": str(target)}},
        ],
    }
    r = eng._process_ai_response("reconstructed-goes-here")
    assert r["session_name"] == "Notes"
    assert r["is_working"] is True
    assert r["response"] == "Reading it now."
    # both calls persisted in the native metadata for next-turn pairing
    names = [c["name"] for c in eng.conversation_history[-1]["_tool_calls"]]
    assert set(names) == {"set_session_name", "read_file"}


def test_build_native_convo_strips_dirty_history(eng):
    dirty = f"Look:\n{BT}read_file\nfoo.txt\n{BT}\ndone"
    eng.conversation_history = [
        {"role": "system", "content": "SYS PROMPT"},
        {"role": "user", "content": "hi"},
        # a pre-fix session: imitation fence saved inside _assistant_text
        {"role": "assistant", "content": "reconstructed",
         "_tool_calls": [{"id": "a", "name": "python_interpreter",
                          "arguments": {"code": "print(1)"}}],
         "_assistant_text": dirty},
        {"role": "system", "content": "OUTPUT: 1"},   # the tool result
        {"role": "assistant", "content": dirty},       # compat-origin turn
        {"role": "system", "content": f"note\n{BT}python_interpreter\nx\n{BT}"},
    ]
    system_prompt, neutral = eng._build_native_convo(eng.conversation_history)
    assert system_prompt == "SYS PROMPT"
    # tool RESULTS stay verbatim (results are data, not instructions)
    tool_msgs = [m for m in neutral if m.get("role") == "tool"]
    assert tool_msgs and tool_msgs[0]["results"][0]["content"] == "OUTPUT: 1"
    # everything else that goes over the wire is fence-free
    for m in neutral:
        if m.get("role") == "assistant":
            assert "read_file" not in (m.get("content") or "")
        if m.get("role") == "user" and str(m.get("content", "")).startswith("[SYSTEM]"):
            assert "python_interpreter" not in m["content"]


def test_render_active_skills_native_neutralizes_fences(eng):
    class _Skills:
        @staticmethod
        def get_loaded_skills():
            return {"meta": ("---\nname: meta\n---\n"
                             "Do X:\n\n```python_interpreter: [Opening chat]\n"
                             "print('hi')\n```\n")}
    eng.skill_manager = _Skills()
    block = eng._render_active_skills()
    assert "```python_interpreter" not in block
    assert "run via the python_interpreter tool" in block
    assert "NATIVE tool-calling mode" in block


def test_render_active_skills_compat_keeps_fences(qapp):
    from systema.engine.ai_engine import AIEngine
    eng = AIEngine(settings_callback=lambda: {"tool_calling_mode": "compat"})

    class _Skills:
        @staticmethod
        def get_loaded_skills():
            return {"meta": "```python_interpreter: [X]\nprint('hi')\n```\n"}
    eng.skill_manager = _Skills()
    block = eng._render_active_skills()
    assert "```python_interpreter" in block
    assert "NATIVE tool-calling mode" not in block
