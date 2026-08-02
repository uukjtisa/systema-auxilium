"""
tests/systema/engine/test_streaming.py

End-to-end streaming through the engine's provider path, without network or Qt:
 - a v2 script that streams gets drained on the calling thread, with live
   text/thinking deltas emitted as signals and the COMPLETE text returned to
   the normal (non-streaming) response pipeline;
 - the response timeout bounds time-to-FIRST-chunk only — a slow tail must
   never be cut off;
 - callers whose output is not the chat turn (stream_ok=False) never stream;
 - thinking is UI-only: a thinking ui_event never reaches the provider.
"""
import concurrent.futures as cf
import textwrap
import threading
import time
import types

import pytest

from systema.engine import provider_contract as pc


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


class _Engine:
    """The three AIEngine methods under test, lifted onto a bare object so the
    streaming plumbing can be exercised without constructing the real engine."""

    def __init__(self, settings=None, sig_log=None):
        from systema.engine.ai_engine import AIEngine
        self.settings_callback = (lambda: settings) if settings is not None else None
        self.tool_manager = types.SimpleNamespace(
            approval_signal=_Sig(sig_log if sig_log is not None else []))
        for name in ('_setting', '_response_timeout', '_streaming_on',
                     '_run_provider_call'):
            setattr(self, name, getattr(AIEngine, name).__get__(self, AIEngine))


def _v2_module(gen_factory, supports_native=False):
    m = types.ModuleType("streaming_provider")
    m.SUPPORTS_NATIVE_TOOLS = supports_native

    def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
        if stream:
            return gen_factory()
        return {"content": "non-stream", "thinking": None,
                "tool_calls": [], "finish_reason": "stop"}

    m.chat = chat
    return m


def _chunks(*pairs):
    def gen():
        for kind, content in pairs:
            yield {"type": kind, "content": content, "finish_reason": None}
        yield {"type": "done", "content": "", "finish_reason": "stop"}
    return gen


# ── the streaming call ───────────────────────────────────────────────────────

def test_stream_drains_to_complete_result_and_emits_live_deltas():
    log = []
    eng = _Engine(settings={'streaming_enabled': True}, sig_log=log)
    mod = _v2_module(_chunks(("thinking", "hmm "), ("thinking", "ok"),
                             ("text", "Hel"), ("text", "lo")))

    out = eng._run_provider_call(mod, "sys", [{"role": "user", "content": "hi"}])

    assert out["content"] == "Hello"
    assert out["thinking"] == "hmm ok"
    assert out["finish_reason"] == "stop"
    # exactly one started + one finished, deltas in order between them
    assert log[0][0] == "stream_started"
    assert log[-1][0] == "stream_finished"
    assert [a for n, a in log if n == "stream_text"] == ["Hel", "lo"]
    assert [a for n, a in log if n == "stream_thinking"] == ["hmm ", "ok"]
    assert sum(1 for n, _ in log if n == "stream_started") == 1


def test_streaming_disabled_by_setting_takes_the_plain_path():
    log = []
    eng = _Engine(settings={'streaming_enabled': False}, sig_log=log)
    mod = _v2_module(_chunks(("text", "streamed")))

    out = eng._run_provider_call(mod, "", [])

    assert out["content"] == "non-stream"
    assert log == []


def test_stream_ok_false_forces_non_streaming():
    log = []
    eng = _Engine(settings={'streaming_enabled': True}, sig_log=log)
    mod = _v2_module(_chunks(("text", "streamed")))

    out = eng._run_provider_call(mod, "", [], stream_ok=False)

    assert out["content"] == "non-stream"
    assert log == []


def test_a_script_written_for_the_retired_contract_raises():
    """The retired contract (`chat(sys, msgs) -> str`) is GONE, shim included —
    the user's explicit call, since there are no external users to protect.

    The accepted cost is stated here so it cannot regress into a surprise: such
    a script now fails loudly on the unexpected keyword instead of being
    silently shimmed. That is strictly better than what the marker did, which
    was to downgrade a CORRECT script that merely forgot one line — no images,
    no tools, no streaming, and no error at all."""
    eng = _Engine(settings={'streaming_enabled': True})
    retired = types.ModuleType("retired")
    retired.chat = lambda sp, msgs: "old reply"

    with pytest.raises(TypeError, match="unexpected keyword"):
        eng._run_provider_call(retired, "", [])


def test_tool_calls_arrive_complete_at_end_of_stream():
    eng = _Engine(settings={'streaming_enabled': True})
    mod = _v2_module(_chunks(
        ("text", "running it"),
        ("tool_call", {"id": "c1", "name": "read_file", "arguments": {"path": "x"}})))

    out = eng._run_provider_call(mod, "", [], tools=[{"name": "read_file"}])

    assert out["content"] == "running it"
    assert out["tool_calls"] == [{"id": "c1", "name": "read_file",
                                  "arguments": {"path": "x"}}]


# ── timeout semantics ────────────────────────────────────────────────────────

def test_timeout_bounds_first_chunk_only_slow_tail_survives():
    """A stream that starts promptly but finishes slowly must NOT be killed."""
    def gen():
        yield {"type": "text", "content": "fast-start", "finish_reason": None}
        time.sleep(0.35)                      # longer than the 0.2s timeout
        yield {"type": "text", "content": " slow-tail", "finish_reason": None}
        yield {"type": "done", "content": "", "finish_reason": "stop"}

    out = pc.start_stream(gen, timeout=0.2)
    assert pc.drain_stream(out)["content"] == "fast-start slow-tail"


def test_timeout_trips_when_first_chunk_never_arrives():
    # The abandoned provider call is released the instant the assertion is
    # done: start_stream deliberately does NOT wait for it (a hung provider
    # must not re-block the turn), so a test that left it sleeping would leak
    # a live thread into the rest of the suite.
    release = threading.Event()

    def slow_start():
        release.wait(10)
        yield {"type": "text", "content": "too late", "finish_reason": None}

    try:
        with pytest.raises(cf.TimeoutError):
            pc.start_stream(slow_start, timeout=0.2)
    finally:
        release.set()


def test_zero_timeout_means_unlimited():
    out = pc.start_stream(_chunks(("text", "ok")), timeout=0)
    assert pc.drain_stream(out)["content"] == "ok"


def test_start_stream_passes_through_non_generator_results():
    assert pc.start_stream(lambda: {"content": "plain"}, timeout=1) == {"content": "plain"}


def test_start_stream_handles_immediately_exhausted_generator():
    def empty():
        return
        yield
    out = pc.start_stream(empty, timeout=1)
    assert pc.drain_stream(out)["content"] == ""


# ── thinking stays UI-only ───────────────────────────────────────────────────

def test_thinking_ui_event_is_stripped_from_provider_history():
    from systema.engine.ai_engine import AIEngine

    eng = object.__new__(AIEngine)
    eng.conversation_history = [
        {'role': 'user', 'content': 'hi'},
        {'role': 'ui_event', '_type': 'thinking', 'content': 'Thinking',
         '_thinking': 'secret reasoning the model must not be re-billed for'},
        {'role': 'assistant', 'content': 'hello'},
    ]
    out = AIEngine._get_history_with_memory(eng)

    assert [m['role'] for m in out] == ['user', 'assistant']
    assert not any('secret reasoning' in str(m) for m in out)


def test_memory_context_ui_event_still_promoted():
    """Guard rail: the thinking strip must not break the one ui_event type
    that IS meant to reach the provider."""
    from systema.engine.ai_engine import AIEngine

    eng = object.__new__(AIEngine)
    eng.conversation_history = [
        {'role': 'ui_event', '_type': 'memory_context', 'content': 'MEMORY BLOCK'},
        {'role': 'user', 'content': 'hi'},
    ]
    out = AIEngine._get_history_with_memory(eng)

    assert out[0] == {'role': 'system', 'content': 'MEMORY BLOCK'}


# ── provider scripts on disk ─────────────────────────────────────────────────

def _fake_provider(tmp_path, name, default_model="default-model"):
    script = tmp_path / name
    script.write_text(textwrap.dedent(f'''
        API_KEY = "placeholder"
        MODEL = "{default_model}"
        Display = {{
            "API_KEY": ("API Key", "input"),
            "MODEL": ("Model", "list_dropdown", ["{default_model}", "other"]),
        }}
        def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
            return {{"content": MODEL, "thinking": None, "tool_calls": [],
                    "finish_reason": "stop"}}
    '''), encoding="utf-8")
    return script


def _load(script, settings):
    from systema.engine.ai_engine import AIEngine
    eng = object.__new__(AIEngine)
    eng.custom_script_path = str(script)
    eng.settings_callback = lambda: settings
    eng.log = lambda *a, **k: None
    return AIEngine._load_provider_module(eng)


def test_saved_display_values_reach_the_loaded_module(tmp_path):
    """The persistence loop: values saved under settings ▸
    provider_display_values ▸ <file name> are setattr'd onto the freshly
    imported module before every call."""
    script = _fake_provider(tmp_path, "provider_fake.py")
    settings = {'provider_display_values': {
        'provider_fake.py': {"API_KEY": "sk-live", "MODEL": "other"}}}

    mod = _load(script, settings)

    assert mod.API_KEY == "sk-live"
    assert mod.MODEL == "other"
    # ...and the override is what the provider actually answers with.
    assert pc.invoke(mod, "", [])["content"] == "other"


def test_each_provider_script_keeps_its_own_values(tmp_path):
    """Per-script isolation: two providers configured side by side never read
    each other's values, and one with no saved entry keeps its file defaults."""
    a = _fake_provider(tmp_path, "provider_a.py", "a-default")
    b = _fake_provider(tmp_path, "provider_b.py", "b-default")
    c = _fake_provider(tmp_path, "provider_c.py", "c-default")
    settings = {'provider_display_values': {
        'provider_a.py': {"MODEL": "a-chosen", "API_KEY": "key-a"},
        'provider_b.py': {"MODEL": "b-chosen", "API_KEY": "key-b"},
    }}

    assert _load(a, settings).MODEL == "a-chosen"
    assert _load(b, settings).MODEL == "b-chosen"
    assert _load(a, settings).API_KEY == "key-a"
    assert _load(c, settings).MODEL == "c-default"      # untouched defaults


def test_missing_or_empty_display_values_leave_defaults(tmp_path):
    script = _fake_provider(tmp_path, "provider_fake.py")
    for settings in ({}, {'provider_display_values': None},
                     {'provider_display_values': {}},
                     {'provider_display_values': {'other.py': {"MODEL": "x"}}}):
        mod = _load(script, settings)
        assert mod.MODEL == "default-model"
        assert mod.API_KEY == "placeholder"


def test_undeclared_key_in_saved_values_is_ignored(tmp_path):
    """A stale/foreign key in the store must never inject a module attribute."""
    script = _fake_provider(tmp_path, "provider_fake.py")
    settings = {'provider_display_values': {
        'provider_fake.py': {"MODEL": "other", "SECRET_BACKDOOR": "nope"}}}

    mod = _load(script, settings)

    assert mod.MODEL == "other"
    assert not hasattr(mod, "SECRET_BACKDOOR")


def test_bundled_providers_are_callable_and_expose_display():
    """Every shipped LLM provider script must import, define the ONE entry
    point, and expose a Display form — and must NOT carry a CONTRACT_VERSION
    marker, which no longer exists (a stray one is harmless at runtime but
    would teach the next author to copy it)."""
    from systema import APP_ROOT
    folder = APP_ROOT / "resources" / "providers" / "large-language-models"
    scripts = sorted(p for p in folder.glob("*.py"))
    if not scripts:
        pytest.skip("no provider scripts installed")
    for path in scripts:
        mod = pc.load_module(str(path))
        assert mod is not None, f"{path.name} failed to import"
        assert callable(getattr(mod, "chat", None)), f"{path.name} defines no chat()"
        assert not hasattr(mod, "CONTRACT_VERSION"), \
            f"{path.name} still declares the retired CONTRACT_VERSION marker"
        assert pc.validate_display(mod), \
            f"{path.name} exposes no Display fields"


def test_bundled_providers_do_no_network_at_import(monkeypatch):
    """Importing a provider must be side-effect free: the settings screen and
    the provider list import every script just to read `Display`. A network
    call there stalls the UI (and fires for providers you aren't using)."""
    import socket
    from systema import APP_ROOT

    calls = []

    def _blocked(self, *a, **k):
        calls.append(a[0] if a else None)
        raise AssertionError("provider opened a socket at import time")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)

    for path in sorted((APP_ROOT / "resources" / "providers" / "large-language-models").glob("*.py")):
        assert pc.load_module(str(path)) is not None, f"{path.name} failed to import"
    assert not calls
