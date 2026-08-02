"""
tests/systema/engine/test_provider_contract.py

The provider-script contract layer (engine/provider_contract.py): the ONE
call path (a script is valid if it defines a callable chat, with NO version
marker), Display validation incl. the extended opts forms, override
application, explicit secret typing, <think>-tag splitting (whole and
incremental/streamed), the shared OpenAI-events chunk generator, and
drain_stream collapse.
"""
import inspect
import types


from systema.engine import provider_contract as pc


def _mod(**attrs):
    m = types.ModuleType("fake_provider")
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


# ── one contract, no version marker ──────────────────────────────────────────

def _v2_chat(sp, msgs, *, images=None, tools=None, stream=False):
    return {"content": "ok"}


def test_a_marker_less_script_gets_the_full_treatment():
    """THE regression this whole removal is about.

    `CONTRACT_VERSION` used to default to the retired contract when absent, so a
    correctly written script that merely forgot one line was silently downgraded
    — no images, no tools, no streaming, reported as vision-less, and never an
    error. A script is now valid on `chat` alone.
    """
    seen = {}

    def chat(sp, msgs, *, images=None, tools=None, stream=False):
        seen.update(images=images, tools=tools, stream=stream)
        return {"content": "ok"}

    m = _mod(chat=chat, SUPPORTS_NATIVE_TOOLS=True)     # no marker anywhere
    assert pc.supports_native(m)
    assert pc.supports_images(m)
    out = pc.invoke(m, "s", [], images=["i"], tools=[{"name": "x"}])
    assert out["content"] == "ok"
    assert seen == {"images": ["i"], "tools": [{"name": "x"}], "stream": False}


def test_a_leftover_version_marker_is_ignored_never_an_error():
    """Someone's old script may still carry the line. It means nothing now."""
    m = _mod(CONTRACT_VERSION=2, chat=_v2_chat)
    assert pc.invoke(m, "", [])["content"] == "ok"
    m99 = _mod(CONTRACT_VERSION=99, chat=_v2_chat)
    assert pc.invoke(m99, "", [])["content"] == "ok"


def test_supports_native_is_the_flag_plus_a_chat():
    assert pc.supports_native(_mod(chat=_v2_chat, SUPPORTS_NATIVE_TOOLS=True))
    assert not pc.supports_native(_mod(chat=_v2_chat))          # no flag
    assert not pc.supports_native(_mod(SUPPORTS_NATIVE_TOOLS=True))   # no chat
    assert not pc.supports_native(None)


def test_supports_images_defaults_true_and_opts_out_explicitly():
    assert pc.supports_images(_mod(chat=_v2_chat))
    assert not pc.supports_images(_mod(chat=_v2_chat, SUPPORTS_VISION=False))
    assert not pc.supports_images(None)


def test_supports_vision_may_be_a_callable_for_per_model_answers():
    """One backend hosts models with different capabilities. A per-PROVIDER
    flag either hides the models that can see or fails inside the encoder on
    the ones that can't, so a script may answer per selected model."""
    m = _mod(chat=_v2_chat, MODEL="text-only")
    m.SUPPORTS_VISION = lambda: m.MODEL.endswith("-vl")
    assert not pc.supports_images(m)
    m.MODEL = "big-vl"                      # what the settings form setattrs
    assert pc.supports_images(m)


def test_a_raising_vision_probe_means_no_vision_not_a_crash():
    """A capability probe must never take the app down."""
    def boom():
        raise RuntimeError("bad model id")

    m = _mod(chat=_v2_chat)
    m.SUPPORTS_VISION = boom
    assert pc.supports_images(m) is False


def test_supports_inline_images_is_the_flag_alone():
    assert pc.supports_inline_images(_mod(chat=_v2_chat,
                                          SUPPORTS_INLINE_IMAGES=True))
    assert not pc.supports_inline_images(_mod(chat=_v2_chat))


def test_the_retired_entry_points_are_gone():
    """No second calling convention may come back: two of them is what let
    code_agent call chat_tools() on a module that never defines it."""
    assert not hasattr(pc, "is_v2")
    src = inspect.getsource(pc)
    assert "chat_tools" not in src.split('"""', 2)[2]   # docstring may name it
    assert "chat_image" not in src.split('"""', 2)[2]


# ── invoke ───────────────────────────────────────────────────────────────────

def test_invoke_passes_kwargs_and_returns_dict():
    seen = {}

    def chat(sp, msgs, *, images=None, tools=None, stream=False):
        seen.update(images=images, tools=tools, stream=stream)
        return {"content": "ok", "thinking": "why", "tool_calls": [],
                "finish_reason": "stop"}

    m = _mod(chat=chat)
    out = pc.invoke(m, "s", [], images=["i"], tools=[{"name": "x"}])
    assert out["content"] == "ok" and out["thinking"] == "why"
    assert seen == {"images": ["i"], "tools": [{"name": "x"}], "stream": False}


def test_invoke_stream_returns_generator():
    def chat(sp, msgs, *, images=None, tools=None, stream=False):
        def gen():
            yield {"type": "text", "content": "a", "finish_reason": None}
            yield {"type": "done", "content": "", "finish_reason": "stop"}
        return gen() if stream else {"content": "a"}

    m = _mod(chat=chat)
    out = pc.invoke(m, "", [], stream=True)
    assert hasattr(out, "__next__")
    assert pc.drain_stream(out)["content"] == "a"


def test_invoke_plain_string_reply_normalized():
    m = _mod(chat=lambda sp, msgs, **kw: "hello")
    out = pc.invoke(m, "sys", [{"role": "user", "content": "hi"}])
    assert out == {"content": "hello", "thinking": None,
                   "tool_calls": [], "finish_reason": None}


def test_invoke_no_chat_returns_none():
    assert pc.invoke(_mod(), "", []) is None


# ── Display validation ───────────────────────────────────────────────────────

def test_validate_display_basic_and_extended_forms():
    m = _mod(Display={
        "A": ("Plain", "input"),
        "B": ("Drop", "list_dropdown", ["x", "y"]),
        "C": ("With opts", "input", {"tooltip": "t", "placeholder": "p"}),
        "D": ("Drop opts", "list_dropdown", ["x"], {"item_tooltips": ["ix"]}),
        "N": ("A note.", "info_box"),
    })
    d = pc.validate_display(m)
    assert set(d) == {"A", "B", "C", "D", "N"}
    assert d["A"] == ("Plain", "input", None, {})
    assert d["B"][2] == ["x", "y"]
    assert d["C"][3]["tooltip"] == "t"
    assert d["D"][3]["item_tooltips"] == ["ix"]
    assert d["N"][1] == "info_box"


def test_validate_display_skips_malformed_entries():
    m = _mod(Display={
        "OK": ("Fine", "input"),
        "BAD_TYPE": ("Nope", "slider"),
        "NO_OPTIONS": ("Drop", "list_dropdown"),
        "BAD_OPTS": ("X", "input", ["not-a-dict"], ["also-not"]),
        123: ("Not a name", "input"),
        "NOT_IDENT": None,
    })
    assert set(pc.validate_display(m)) == {"OK"}


def test_validate_display_absent_or_invalid():
    assert pc.validate_display(_mod()) == {}
    assert pc.validate_display(_mod(Display=["not", "dict"])) == {}


def test_apply_display_overrides_declared_only_and_skips_info_box():
    m = _mod(API_KEY="old", SECRET="keep",
             Display={"API_KEY": ("API Key", "input"),
                      "NOTE": ("note text", "info_box")})
    pc.apply_display_overrides(m, {"API_KEY": "new", "SECRET": "evil",
                                   "NOTE": "clobber"})
    assert m.API_KEY == "new"
    assert m.SECRET == "keep"          # undeclared → never applied
    assert m.Display["NOTE"][0] == "note text"


def test_is_secret_type_is_explicit_not_name_based():
    assert pc.is_secret_type("secure_input")
    assert not pc.is_secret_type("input")
    assert not pc.is_secret_type("textarea")
    # secure_input is a first-class Display type
    assert "secure_input" in pc.DISPLAY_TYPES


def test_validate_display_accepts_secure_input():
    m = _mod(Display={"API_KEY": ("API Key", "secure_input",
                                  {"placeholder": "sk-..."})})
    d = pc.validate_display(m)
    assert d["API_KEY"][1] == "secure_input"
    assert d["API_KEY"][3]["placeholder"] == "sk-..."


# ── think-tag splitting ──────────────────────────────────────────────────────

def test_split_think_tags():
    t, c = pc.split_think_tags("<think>reason</think>reply")
    assert t == "reason" and c == "reply"
    t2, c2 = pc.split_think_tags("no tags here")
    assert t2 is None and c2 == "no tags here"


def test_think_splitter_tags_across_deltas():
    s = pc.ThinkTagStreamSplitter()
    out = []
    for delta in ["<thi", "nk>rea", "soning</th", "ink>hel", "lo"]:
        out.extend(s.feed(delta))
    out.extend(s.flush())
    thinking = "".join(c for k, c in out if k == "thinking")
    text = "".join(c for k, c in out if k == "text")
    assert thinking == "reasoning"
    assert text == "hello"


def test_think_splitter_plain_text_passthrough():
    s = pc.ThinkTagStreamSplitter()
    out = list(s.feed("just a normal reply")) + list(s.flush())
    assert out == [("text", "just a normal reply")]


def test_think_splitter_unclosed_think_drains_as_thinking():
    s = pc.ThinkTagStreamSplitter()
    out = list(s.feed("<think>never closed")) + list(s.flush())
    assert all(k == "thinking" for k, _ in out)
    assert "".join(c for _, c in out) == "never closed"


# ── shared OpenAI-events chunk generator ─────────────────────────────────────

def _delta_event(**delta):
    return {"choices": [{"delta": delta, "finish_reason": None}]}


def test_stream_openai_chunks_text_thinking_and_tool_assembly():
    events = [
        _delta_event(reasoning="think1"),
        _delta_event(content="hel"),
        _delta_event(content="lo"),
        _delta_event(tool_calls=[{"index": 0, "id": "c1",
                                  "function": {"name": "read_file",
                                               "arguments": '{"pa'}}]),
        _delta_event(tool_calls=[{"index": 0,
                                  "function": {"arguments": 'th": "x"}'}}]),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    chunks = list(pc.stream_openai_chunks(events))
    text = "".join(c["content"] for c in chunks if c["type"] == "text")
    thinking = "".join(c["content"] for c in chunks if c["type"] == "thinking")
    calls = [c["content"] for c in chunks if c["type"] == "tool_call"]
    assert text == "hello" and thinking == "think1"
    assert calls == [{"id": "c1", "name": "read_file", "arguments": {"path": "x"}}]
    assert chunks[-1] == {"type": "done", "content": "",
                          "finish_reason": "tool_calls"}


def test_stream_openai_chunks_inline_think_split():
    events = [_delta_event(content="<think>hm</think>ok")]
    chunks = list(pc.stream_openai_chunks(events))
    assert "".join(c["content"] for c in chunks if c["type"] == "thinking") == "hm"
    assert "".join(c["content"] for c in chunks if c["type"] == "text") == "ok"


# ── drain_stream ─────────────────────────────────────────────────────────────

def test_drain_stream_collapses_and_fires_callbacks():
    def gen():
        yield {"type": "thinking", "content": "t1", "finish_reason": None}
        yield {"type": "text", "content": "a", "finish_reason": None}
        yield {"type": "text", "content": "b", "finish_reason": None}
        yield {"type": "tool_call", "content": {"id": "1", "name": "n",
                                                "arguments": {}},
               "finish_reason": None}
        yield {"type": "done", "content": "", "finish_reason": "stop"}

    seen_text, seen_think = [], []
    out = pc.drain_stream(gen(), on_text=seen_text.append,
                          on_thinking=seen_think.append)
    assert out["content"] == "ab"
    assert out["thinking"] == "t1"
    assert out["tool_calls"][0]["name"] == "n"
    assert out["finish_reason"] == "stop"
    assert seen_text == ["a", "b"] and seen_think == ["t1"]


def test_normalize_result_shapes():
    assert pc.normalize_result("s")["content"] == "s"
    d = pc.normalize_result({"content": "t", "tool_calls": [1]})
    assert d["content"] == "t" and d["tool_calls"] == [1]
    # "text" was the retired chat_tools key — it is NOT content any more.
    assert pc.normalize_result({"text": "t"})["content"] == ""
    assert pc.normalize_result(42) is None
