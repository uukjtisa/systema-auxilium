"""
tests/systema/engine/test_provider_failure_message.py

A provider that returns "" used to unwind into
`Error: No response from custom_script provider` — which names nothing the user
recognises ('custom_script' is the literal provider MODE, not the script), gives
no cause, and left nothing in the log to debug from. Empty returns became
indistinguishable from the app hanging.

Now every silent-failure path records a NAMED, actionable message.
"""
import types


from systema.engine.ai_engine import AIEngine


def _engine(module=None, settings=None):
    eng = object.__new__(AIEngine)
    eng.settings_callback = lambda: (settings if settings is not None
                                     else {'streaming_enabled': False})
    eng.tool_manager = types.SimpleNamespace(approval_signal=None)
    eng.logged = []
    eng.log = lambda msg, level=None: eng.logged.append((level, msg))
    eng.ai_provider = 'custom_script'
    eng.custom_script_path = r"C:\providers\provider_opencode_zen.py"
    eng.last_provider_error = None
    eng._background_depth = 0
    eng.last_sent_messages = None
    eng.last_raw_provider_result = None
    eng._pending_thinking = None
    eng._load_provider_module = lambda: module
    return eng


def _module(returns):
    m = types.ModuleType("provider_opencode_zen")
    m.chat = lambda sp, msgs, **kw: returns
    return m


# ── the reported bug ─────────────────────────────────────────────────────────

def test_empty_content_produces_a_named_error():
    mod = _module({"content": "", "thinking": None, "tool_calls": [],
                   "finish_reason": "stop"})
    eng = _engine(mod)

    assert AIEngine._provider_script(eng, [{'role': 'user', 'content': 'hi'}]) is None

    err = eng.last_provider_error
    assert err, "an empty provider return recorded no error at all"
    assert "provider_opencode_zen" in err
    assert "empty response" in err
    # ...and it tells the user what to DO about it.
    assert "switch providers" in err.lower()


def test_the_error_reaches_the_turn_result():
    mod = _module({"content": "", "tool_calls": [], "finish_reason": "stop"})
    eng = _engine(mod)
    AIEngine._provider_script(eng, [{'role': 'user', 'content': 'hi'}])

    msg = AIEngine._provider_failure_message(eng)

    assert "provider_opencode_zen" in msg
    assert "custom_script" not in msg, \
        "the user-facing error still names the provider MODE, not the script"


def test_the_failure_is_logged_for_debugging():
    mod = _module({"content": "", "tool_calls": []})
    eng = _engine(mod)
    AIEngine._provider_script(eng, [{'role': 'user', 'content': 'hi'}])

    assert any(lvl == "ERROR" and "provider_opencode_zen" in text
               for lvl, text in eng.logged)


def test_a_raising_provider_is_named_too():
    mod = types.ModuleType("provider_opencode_zen")

    def _boom(sp, msgs, **kw):
        raise ConnectionError("connection reset by peer")
    mod.chat = _boom
    eng = _engine(mod)

    assert AIEngine._provider_script(eng, [{'role': 'user', 'content': 'hi'}]) is None
    assert "provider_opencode_zen" in eng.last_provider_error
    assert "ConnectionError" in eng.last_provider_error


def test_the_message_is_consumed_once():
    """A stale error must not be pinned to the NEXT turn's failure."""
    eng = _engine()
    eng.last_provider_error = "Provider 'x' returned an empty response."

    first = AIEngine._provider_failure_message(eng)
    second = AIEngine._provider_failure_message(eng)

    assert "returned an empty response" in first
    assert "returned an empty response" not in second
    assert "provider_opencode_zen" in second      # falls back to the named generic


def test_a_good_reply_records_no_error():
    mod = _module({"content": "all good", "tool_calls": [], "finish_reason": "stop"})
    eng = _engine(mod)

    assert AIEngine._provider_script(eng, [{'role': 'user', 'content': 'hi'}]) == "all good"
    assert eng.last_provider_error is None
