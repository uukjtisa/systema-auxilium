"""
tests/systema/engine/test_subagent_isolation.py

Sub-agents share the MAIN AIEngine instance, so a careless provider call from
one of them paints into the visible chat turn. Two ways that bit us:

  * the session namer / compactor streamed their tokens into the live bubble and
    the turn's thinking card (the namer runs on its own QThread, so this could
    land in the MIDDLE of an unrelated reply);
  * even unstreamed, they clobbered the per-turn scratch state
    (`_pending_thinking`, `_pending_native`, `last_sent_messages`, ...) the main
    turn was still using.

`AIEngine.background_call()` closes both holes structurally. These tests lock
the behaviour AND the call sites, so a NEW sub-agent cannot reintroduce it.
"""
import ast
import types
from pathlib import Path

import pytest

import systema
from systema.engine.ai_engine import AIEngine


# ── helpers ──────────────────────────────────────────────────────────────────

class _Sig:
    """Stand-in for ApprovalSignal: records emissions instead of using Qt."""
    def __init__(self, log):
        self._log = log

    class _Slot:
        def __init__(self, log, name):
            self._log, self._name = log, name

        def emit(self, *args):
            self._log.append((self._name, args[0] if args else None))

    def __getattr__(self, name):
        return _Sig._Slot(self._log, name)


def _streaming_module(text="Streamed Title", thinking="secret reasoning"):
    """A contract-v2 script that streams when asked to."""
    m = types.ModuleType("streaming_provider")
    m.CONTRACT_VERSION = 2

    def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
        if stream:
            def gen():
                yield {"type": "thinking", "content": thinking, "finish_reason": None}
                for word in text.split():
                    yield {"type": "text", "content": word + " ", "finish_reason": None}
                yield {"type": "done", "content": "", "finish_reason": "stop"}
            return gen()
        return {"content": text, "thinking": thinking, "tool_calls": [],
                "finish_reason": "stop"}

    m.chat = chat
    return m


def _engine(sig_log, module=None, settings=None):
    """A partial AIEngine carrying exactly the methods the provider path needs."""
    eng = object.__new__(AIEngine)
    eng.settings_callback = lambda: (settings if settings is not None
                                     else {'streaming_enabled': True})
    eng.tool_manager = types.SimpleNamespace(approval_signal=_Sig(sig_log))
    eng.log = lambda *a, **k: None
    eng.custom_script_path = "fake.py"
    eng.ai_provider = 'custom_script'
    eng._background_depth = 0
    eng._background_lock = __import__("threading").RLock()
    eng._pending_native = None
    eng._pending_thinking = None
    eng.last_tool_transport = 'compat'
    eng.last_native_tool_calls = 0
    eng.last_sent_messages = None
    eng.last_raw_provider_result = None
    eng._load_provider_module = lambda: (module if module is not None
                                         else _streaming_module())
    return eng


# ── the guard itself ─────────────────────────────────────────────────────────

def test_background_call_forces_streaming_off():
    eng = _engine([])
    mod = _streaming_module()

    assert eng._streaming_on(mod) is True          # baseline: it WOULD stream
    with eng.background_call("namer"):
        assert eng.in_background_call is True
        assert eng._streaming_on(mod) is False
    assert eng.in_background_call is False
    assert eng._streaming_on(mod) is True          # ...and it streams again after


def test_background_call_is_reentrant():
    eng = _engine([])
    mod = _streaming_module()
    with eng.background_call("outer"):
        with eng.background_call("inner"):
            assert eng._streaming_on(mod) is False
        # the inner exit must NOT re-enable streaming for the outer sub-agent
        assert eng._streaming_on(mod) is False
    assert eng._streaming_on(mod) is True


def test_background_call_restores_scratch_state():
    """A sub-agent's provider call must not leave ANY per-turn state behind —
    the main turn may be mid-flight on another thread."""
    eng = _engine([])
    eng._pending_thinking = "main turn reasoning"
    eng._pending_native = {"tool_calls": ["main"]}
    eng.last_sent_messages = ["main convo"]
    eng.last_tool_transport = 'native'
    eng.last_raw_provider_result = "main result"

    with eng.background_call("namer"):
        eng._pending_thinking = "namer reasoning"
        eng._pending_native = {"tool_calls": ["namer"]}
        eng.last_sent_messages = ["namer convo"]
        eng.last_tool_transport = 'compat'
        eng.last_raw_provider_result = "namer result"

    assert eng._pending_thinking == "main turn reasoning"
    assert eng._pending_native == {"tool_calls": ["main"]}
    assert eng.last_sent_messages == ["main convo"]
    assert eng.last_tool_transport == 'native'
    assert eng.last_raw_provider_result == "main result"


def test_background_call_restores_state_even_when_the_call_raises():
    eng = _engine([])
    eng._pending_thinking = "main"
    with pytest.raises(RuntimeError):
        with eng.background_call("boom"):
            eng._pending_thinking = "sub-agent"
            raise RuntimeError("provider exploded")
    assert eng._pending_thinking == "main"
    assert eng.in_background_call is False


# ── the real sub-agents ──────────────────────────────────────────────────────

def test_session_namer_emits_no_stream_signals():
    """The reported bug: the namer's title + reasoning streamed into the main
    chat (title flashed in a bubble, reasoning landed in the thinking card)."""
    from systema.agents.session_namer_agent import SessionNamerAgent

    log = []
    eng = _engine(log, module=_streaming_module("Bootloader Tool Scan"))

    title = SessionNamerAgent(eng).generate("user: hello\nassistant: hi")

    assert title == "Bootloader Tool Scan"
    assert log == [], f"session namer leaked UI signals into the chat: {log}"


def test_compactor_emits_no_stream_signals():
    from systema.agents.compactor_agent import CompactorAgent

    log = []
    eng = _engine(log, module=_streaming_module("tiny"))

    out = CompactorAgent(eng).compact("print(1)", "x" * 500)

    assert out == "tiny"
    assert log == [], f"compactor leaked UI signals into the chat: {log}"


def test_subagent_does_not_clobber_a_live_turns_thinking():
    """End-to-end version of the scratch-state hole: the namer's provider reply
    carries its own `thinking`, which used to overwrite the main turn's."""
    from systema.agents.session_namer_agent import SessionNamerAgent

    eng = _engine([], module=_streaming_module("A Title", thinking="namer brain"))
    eng._pending_thinking = "the main turn's reasoning"

    SessionNamerAgent(eng).generate("digest")

    assert eng._pending_thinking == "the main turn's reasoning"


def test_raw_call_runs_isolated():
    """Task agents go through raw_call — same isolation requirement."""
    log = []
    eng = _engine(log, module=_streaming_module("task reply"))
    eng._pending_thinking = "main"

    out = AIEngine.raw_call(eng, "sys", [{'role': 'user', 'content': 'go'}])

    assert out == "task reply"
    assert log == []
    assert eng._pending_thinking == "main"


# ── call-site guard: a FUTURE sub-agent cannot forget ────────────────────────

def _agent_sources():
    folder = Path(systema.__file__).resolve().parent / "agents"
    return sorted(p for p in folder.glob("*.py") if p.name != "__init__.py")


def _agent_cases():
    """(path, n_provider_calls) for every agent module.

    The count rides along so the CASE ID says what was checked — with -v the
    run reads `code_agent.py-1-provider-call` / `skill_manager.py-no-provider-
    call`, instead of a bare filename that tells you nothing about whether the
    guard had anything to bite on.
    """
    out = []
    for path in _agent_sources():
        n = len(_provider_calls(ast.parse(path.read_text(encoding="utf-8"))))
        out.append((path, n))
    return out


def _case_id(case):
    path, n = case
    return f"{path.name}-{n}-provider-call" if n else f"{path.name}-no-provider-call"


def _provider_calls(tree):
    """Every `<x>._provider_script(...)` Call node in the tree."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_provider_script"]


def _is_background_with(node):
    return isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "background_call"
        for item in node.items)


@pytest.mark.parametrize("case", _agent_cases(), ids=_case_id)
def test_every_agent_provider_call_is_isolated(case):
    """Every provider call made from systema/agents/ must be (a) unstreamed and
    (b) wrapped in background_call(). Both, not either: stream_ok=False alone
    silences the deltas but still clobbers the main turn's scratch state.

    A module with no provider call ASSERTS that, rather than skipping. Three of
    them are in that state for real reasons — skill_manager watches a folder,
    compaction_manager delegates to compactor_agent, and task_manager drives its
    OWN AIEngine (which is exactly why it needs no background_call: it shares no
    scratch state with the main turn). A skip reported "nothing was verified
    here"; the assertion reports the truth, which is "verified: this module
    makes no unisolated call", and the case ID says which of the two it was.
    """
    path, expected_calls = case
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = _provider_calls(tree)
    assert len(calls) == expected_calls, "collection and run disagree — stale parse?"

    if not calls:
        # Nothing to isolate, and that is the finding. If a provider call is
        # ever added here the ID flips to "-N-provider-call" and every
        # assertion below starts applying to it.
        assert calls == []
        return

    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "stream_ok" in kw, (
            f"{path.name}:{call.lineno} calls _provider_script without "
            f"stream_ok=False — a sub-agent must never stream into the chat")
        assert isinstance(kw["stream_ok"], ast.Constant) and kw["stream_ok"].value is False, \
            f"{path.name}:{call.lineno} passes a non-False stream_ok"

    guarded = {id(c) for node in ast.walk(tree) if _is_background_with(node)
               for c in _provider_calls(node)}
    for call in calls:
        assert id(call) in guarded, (
            f"{path.name}:{call.lineno} calls _provider_script outside a "
            f"`with <engine>.background_call(...)` block — the main turn's "
            f"scratch state would be clobbered")


def test_the_guard_looks_at_every_agent_module():
    """A filter bug that quietly dropped a module would look exactly like a
    clean pass, so the case list is checked against the folder itself."""
    covered = {path.name for path, _ in _agent_cases()}
    on_disk = {p.name for p in _agent_sources()}
    assert covered == on_disk
    assert covered, "no agent modules found — the folder path is wrong"
