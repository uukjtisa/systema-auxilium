"""
tests/systema/engine/test_think_tag_recovery.py

Reasoning must never be shown to the user as the model's ANSWER.

Several chat templates pre-fill the opening `<think>` themselves, so the only
tag the model ever emits is the closer. Both splitters required BOTH tags, so
for those models the entire chain-of-thought was returned as reply text.
Measured live 2026-08-18 on `@cf/qwen/qwq-32b`: streaming produced 257 text
chunks and zero thinking chunks, and the non-streaming reply to "say hello"
was nine hundred characters of "Okay, the user wants me to...".

Two Workers AI response quirks are covered too, both reproduced against the
live endpoint the same day.
"""
import types

import pytest

from systema import APP_ROOT
from systema.engine import provider_contract as pc

CF = (APP_ROOT / "resources" / "providers" / "large-language-models"
      / "provider_cloudflare.py")


# ── the shared splitters ─────────────────────────────────────────────────────

def test_closer_with_no_opener_is_reasoning_not_reply():
    thinking, content = pc.split_think_tags(
        "Okay, the user wants one word. So: hello.</think>\n\nhello")
    assert content == "hello"
    assert thinking and thinking.startswith("Okay,")


def test_matched_tags_still_work():
    assert pc.split_think_tags("<think>abc</think>hi") == ("abc", "hi")


def test_plain_text_is_untouched():
    assert pc.split_think_tags("just a reply") == (None, "just a reply")


def test_a_closer_much_later_in_the_reply_still_splits():
    """The closer is not required to be near the start — a long monologue is
    exactly the case that hurt."""
    thinking, content = pc.split_think_tags("x" * 4000 + "</think>answer")
    assert content == "answer"
    assert len(thinking) == 4000


def test_stream_splitter_assume_open_routes_the_head_to_thinking():
    s = pc.ThinkTagStreamSplitter(assume_open=True)
    assert s.feed("Okay reasoning") == [("thinking", "Okay reasoning")]
    assert s.feed("</think>hello") == [("text", "hello")]
    assert s.flush() == []


def test_stream_splitter_default_is_unchanged():
    """assume_open must default off — every other provider relies on detection
    and an always-on assumption would label ordinary replies as thinking."""
    s = pc.ThinkTagStreamSplitter()
    assert s.feed("hello there") == [("text", "hello there")]


def test_stream_openai_chunks_threads_the_flag():
    def events(text):
        yield {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

    out = list(pc.stream_openai_chunks(events("thought</think>reply"),
                                       assume_think_open=True))
    kinds = {c["type"] for c in out}
    assert "thinking" in kinds, "headless reasoning leaked into the reply"
    thinking = "".join(c["content"] for c in out if c["type"] == "thinking")
    reply = "".join(c["content"] for c in out if c["type"] == "text")
    assert thinking == "thought"
    assert reply == "reply"


# ── provider_cloudflare's own splitter ───────────────────────────────────────

def _msg(**kw):
    kw.setdefault("content", None)
    kw.setdefault("reasoning", None)
    kw.setdefault("reasoning_content", None)
    kw.setdefault("tool_calls", None)
    return types.SimpleNamespace(**kw)


@pytest.fixture(scope="module")
def cf():
    mod = pc.load_module(str(CF))
    assert mod is not None, "provider_cloudflare failed to import"
    return mod


def test_cf_headless_closer(cf):
    content, thinking = cf._split_reasoning(
        _msg(content="Okay, let me think.</think>hello"))
    assert content == "hello"
    assert thinking == "Okay, let me think."


def test_cf_harmony_transcript_is_unwrapped(cf):
    """Workers AI sometimes hands back gpt-oss's raw harmony transcript instead
    of parsing it. Without this the channel markers paint into the bubble."""
    raw = "analysis: just multiply. 17*23 = 391.<|end|><|start|>assistantfinal391"
    content, thinking = cf._split_reasoning(_msg(content=raw))
    assert content == "391"
    assert "391" in thinking and "<|" not in thinking


def test_cf_answer_returned_as_reasoning_is_promoted(cf):
    """qwen3-30b with enable_thinking:false answered in the REASONING field and
    left content null — an empty bubble with the answer hidden in a collapsed
    card."""
    content, thinking = cf._split_reasoning(
        _msg(content=None, reasoning="17 * 23 = 391"))
    assert content == "17 * 23 = 391"
    assert thinking is None


def test_cf_tool_call_turn_may_be_contentless(cf):
    """A native tool call legitimately carries no prose — promoting reasoning
    into content there would invent an assistant message."""
    content, thinking = cf._split_reasoning(
        _msg(content=None, reasoning="picking a tool", tool_calls=[{"id": "1"}]))
    assert content == ""
    assert thinking == "picking a tool"


def test_cf_normal_reply_untouched(cf):
    content, thinking = cf._split_reasoning(
        _msg(content="391", reasoning="17*23"))
    assert content == "391"
    assert thinking == "17*23"


# ── the declarations that drive the above ────────────────────────────────────

def test_prefilled_think_models_are_declared_not_guessed(cf):
    assert "@cf/qwen/qwq-32b" in cf._PREFILLED_THINK


def test_unreachable_vision_model_is_not_offered(cf):
    """@cf/meta/llama-3.2-11b-vision-instruct is a real vision model that
    returns 403 on the free plan. Offering it produces a confusing failure one
    step after the user attaches a picture."""
    dead = "@cf/meta/llama-3.2-11b-vision-instruct"
    assert dead not in cf._VISION_MODELS
    options = (pc.validate_display(cf) or {})["MODEL"][2]
    values = [o[1] if isinstance(o, (tuple, list)) else o for o in options]
    assert dead not in values


def test_every_offered_model_has_a_reasoning_answer(cf):
    """_reasoning_params must return something coherent for every shipped id —
    an empty dict for models with no reasoning mode, never a stray empty
    chat_template_kwargs block."""
    options = (pc.validate_display(cf) or {})["MODEL"][2]
    original = cf.MODEL
    try:
        for opt in options:
            cf.MODEL = opt[1] if isinstance(opt, (tuple, list)) else opt
            for flag in (True, False):
                cf.THINKING = flag
                params = cf._reasoning_params()
                assert isinstance(params, dict)
                assert params.get("chat_template_kwargs") != {}
    finally:
        cf.MODEL = original
        cf.THINKING = True
