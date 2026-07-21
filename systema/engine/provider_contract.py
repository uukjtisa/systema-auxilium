"""
engine/provider_contract.py
The provider-script contract layer: ONE normalized way to call any LLM
provider script, old or new.

V2 contract (the current one — see providers/large-language-models/_template.py):

    CONTRACT_VERSION = 2
    SUPPORTS_NATIVE_TOOLS = bool          # optional capability flags
    NATIVE_DIALECT = "openai"             # "openai" | "anthropic" | "gemini"
    Display = {"VAR_NAME": ("Label", "type"[, extra]), ...}   # optional

    def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
        # non-stream -> {"content": str, "thinking": str|None,
        #                "tool_calls": [ {"id","name","arguments"} ],
        #                "finish_reason": str|None}
        # stream=True -> generator yielding
        #   {"type": "text"|"thinking"|"tool_call"|"done",
        #    "content": ..., "finish_reason": None|...}

Legacy contract (chat(sys, msgs)->str, optional chat_image()->str, optional
chat_tools()->{"text","tool_calls"}) keeps working forever: invoke() wraps it
into the same normalized result. Third-party custom scripts never break.

`Display` drives the auto-generated per-provider settings form (Settings ▸ AI
Provider): the app persists values per script and setattr()s them onto the
freshly imported module before every call, so users configure providers
without editing code.
"""

import importlib.util
import os
import re
import traceback

from systema.engine import native_adapters as na
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("ProviderContract") if _verbose else _NoOpLogger()

DISPLAY_TYPES = ("input", "secure_input", "textarea", "list_dropdown",
                 "checkbox", "number", "file_path", "info_box")

# Types whose value is a credential: rendered masked, with a reveal toggle.
SECRET_TYPES = ("secure_input",)

_THINK_RE = re.compile(r"<think>(.*?)</think>\s*", re.DOTALL | re.IGNORECASE)


# ── module loading ───────────────────────────────────────────────────────────

def load_module(path):
    """Import a provider script fresh (live edits take effect). Returns the
    module or None on missing path / load error. No Display overrides are
    applied here — callers that want them use apply_display_overrides()."""
    if not path or not os.path.isfile(path):
        log.error(f"[provider_contract.load_module] ✗ Not a file: '{path}'")
        return None
    try:
        spec = importlib.util.spec_from_file_location("custom_provider", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        log.error(f"[provider_contract.load_module] ✗ Failed to load '{path}': "
                  f"{e}\n{traceback.format_exc()}")
        return None


def is_v2(module) -> bool:
    """True when the script declares the unified contract."""
    return (
        module is not None
        and int(getattr(module, "CONTRACT_VERSION", 1) or 1) >= 2
        and callable(getattr(module, "chat", None))
    )


def supports_native(module) -> bool:
    """Script can carry native tool calls (v2 chat(tools=...) or legacy chat_tools)."""
    return (
        module is not None
        and bool(getattr(module, "SUPPORTS_NATIVE_TOOLS", False))
        and (is_v2(module) or callable(getattr(module, "chat_tools", None)))
    )


# ── Display descriptor ───────────────────────────────────────────────────────

def validate_display(module) -> dict:
    """Return {var_name: (label, type, extra, opts)} from the module's Display
    dict, skipping (and logging) malformed entries. {} when absent/invalid.

    Accepted spec shapes per entry (tuple or list):
        ("Label", "type")
        ("Label", "list_dropdown", [options...])
        ("Label", "type", {opts})                      # opts directly at [2]
        ("Label", "list_dropdown", [options...], {opts})

    `opts` is an optional dict: {"tooltip": str, "placeholder": str,
    "item_tooltips": [str, ...]} (item_tooltips pair with a dropdown's
    options). Type "info_box" renders a read-only note — no input, nothing
    persisted; its label is the note text and the var name is just a unique
    key (e.g. "NOTE_1").
    """
    display = getattr(module, "Display", None)
    if not isinstance(display, dict):
        return {}
    valid = {}
    for name, spec in display.items():
        try:
            if not (isinstance(name, str) and name.isidentifier()):
                raise ValueError("key must be a variable-name string")
            if not (isinstance(spec, (tuple, list)) and len(spec) >= 2):
                raise ValueError("spec must be (label, type[, extra][, opts])")
            label, ftype = spec[0], spec[1]
            extra, opts = None, {}
            if len(spec) > 2:
                if isinstance(spec[2], dict):
                    opts = spec[2]
                else:
                    extra = spec[2]
            if len(spec) > 3:
                if not isinstance(spec[3], dict):
                    raise ValueError("4th element must be an opts dict")
                opts = spec[3]
            if not isinstance(label, str) or ftype not in DISPLAY_TYPES:
                raise ValueError(f"bad label/type (type must be one of {DISPLAY_TYPES})")
            if ftype == "list_dropdown" and not isinstance(extra, (tuple, list)):
                raise ValueError("list_dropdown needs an options list")
            valid[name] = (label, ftype, extra, opts if isinstance(opts, dict) else {})
        except Exception as e:
            log.warning(f"[provider_contract.validate_display] Skipping Display "
                        f"entry {name!r}: {e}")
    return valid


def apply_display_overrides(module, values: dict) -> None:
    """setattr saved Display values onto a freshly imported module. Only names
    the module's Display declares are applied — a stale/foreign key in the
    settings store can never inject arbitrary attributes."""
    if not values:
        return
    declared = validate_display(module)
    for name, value in values.items():
        spec = declared.get(name)
        if spec and spec[1] != "info_box":
            try:
                setattr(module, name, value)
            except Exception as e:
                log.warning(f"[provider_contract.apply_display_overrides] "
                            f"setattr {name} failed: {e}")


def is_secret_type(ftype: str) -> bool:
    """True for Display types that hold a credential — masked in the form.

    Masking is EXPLICIT (declare `secure_input`), never guessed from the
    variable name: name-sniffing masked ordinary fields that merely mentioned
    "key" and left real secrets in the clear when they didn't.
    """
    return ftype in SECRET_TYPES


# ── result normalization ─────────────────────────────────────────────────────

def normalize_result(raw) -> dict | None:
    """Coerce a provider return (v2 dict or legacy str / chat_tools dict) into
    {"content", "thinking", "tool_calls", "finish_reason"}. None on garbage."""
    if isinstance(raw, str):
        return {"content": raw, "thinking": None, "tool_calls": [], "finish_reason": None}
    if isinstance(raw, dict):
        content = raw.get("content")
        if content is None:
            content = raw.get("text")  # legacy chat_tools key
        return {
            "content": content if isinstance(content, str) else ("" if content is None else str(content)),
            "thinking": raw.get("thinking") or None,
            "tool_calls": raw.get("tool_calls") or [],
            "finish_reason": raw.get("finish_reason"),
        }
    return None


def split_think_tags(text: str) -> tuple:
    """(thinking, content) for models that inline <think>...</think> in the
    reply. thinking is None when no tag is present."""
    if not text or "<think" not in text.lower():
        return None, text
    thinks = _THINK_RE.findall(text)
    if not thinks:
        return None, text
    content = _THINK_RE.sub("", text).strip()
    return "\n\n".join(t.strip() for t in thinks if t.strip()) or None, content


class ThinkTagStreamSplitter:
    """Incremental splitter for providers whose models inline reasoning as a
    leading <think>...</think> block in streamed content deltas (qwen,
    deepseek-distills via Ollama, ...). Tags may arrive split across deltas.

        feed(delta) -> [("thinking"|"text", chunk), ...]
        flush()     -> same, at end of stream (unclosed think drains as thinking)
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self._mode = "detect"   # detect | think | text
        self._buf = ""

    def feed(self, delta: str) -> list:
        self._buf += delta
        out = []
        while True:
            if self._mode == "detect":
                probe = self._buf.lstrip()
                if not probe:
                    return out
                if probe.startswith(self.OPEN):
                    self._buf = probe[len(self.OPEN):]
                    self._mode = "think"
                    continue
                if self.OPEN.startswith(probe):
                    return out          # could still become the opener — wait
                self._mode = "text"
                continue
            if self._mode == "think":
                idx = self._buf.find(self.CLOSE)
                if idx >= 0:
                    if self._buf[:idx]:
                        out.append(("thinking", self._buf[:idx]))
                    self._buf = self._buf[idx + len(self.CLOSE):].lstrip("\n")
                    self._mode = "text"
                    continue
                # emit all but a tail that might be a partial closing tag
                emit, keep = self._buf, ""
                for k in range(len(self.CLOSE) - 1, 0, -1):
                    if self._buf.endswith(self.CLOSE[:k]):
                        emit, keep = self._buf[:-k], self._buf[-k:]
                        break
                if emit:
                    out.append(("thinking", emit))
                self._buf = keep
                return out
            # text mode — pass everything through
            if self._buf:
                out.append(("text", self._buf))
                self._buf = ""
            return out

    def flush(self) -> list:
        out = []
        if self._buf:
            out.append(("thinking" if self._mode == "think" else "text", self._buf))
            self._buf = ""
        return out


def stream_openai_chunks(events, split_think: bool = True):
    """OpenAI-style streaming events → contract chunks.

    `events` yields either OpenAI-SDK chunk objects or plain dicts of the same
    shape ({"choices": [{"delta": {...}, "finish_reason": ...}]}) — SSE-line
    parsing stays in the provider. Emits text/thinking deltas live (optionally
    splitting inline <think> tags), accumulates tool-call fragments per stream
    index, emits COMPLETE tool_call chunks at end of stream, then done.
    One implementation for every OpenAI-dialect provider script.
    """
    def g(obj, key):
        return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)

    finish = None
    pending = {}   # stream index → {"id", "name", "args"}
    splitter = ThinkTagStreamSplitter() if split_think else None
    for event in events:
        choices = g(event, "choices")
        if not choices:
            continue
        choice = choices[0]
        finish = g(choice, "finish_reason") or finish
        delta = g(choice, "delta")
        if delta is None:
            continue
        reasoning = g(delta, "reasoning") or g(delta, "reasoning_content")
        if reasoning:
            yield {"type": "thinking", "content": reasoning, "finish_reason": None}
        content = g(delta, "content")
        if content:
            if splitter is not None:
                for kind, piece in splitter.feed(content):
                    yield {"type": "thinking" if kind == "thinking" else "text",
                           "content": piece, "finish_reason": None}
            else:
                yield {"type": "text", "content": content, "finish_reason": None}
        for tc in (g(delta, "tool_calls") or []):
            idx = g(tc, "index") or 0
            slot = pending.setdefault(idx, {"id": None, "name": "", "args": ""})
            if g(tc, "id"):
                slot["id"] = g(tc, "id")
            fn = g(tc, "function")
            if fn is not None:
                if g(fn, "name"):
                    slot["name"] += g(fn, "name")
                if g(fn, "arguments"):
                    slot["args"] += g(fn, "arguments")

    if splitter is not None:
        for kind, piece in splitter.flush():
            yield {"type": "thinking" if kind == "thinking" else "text",
                   "content": piece, "finish_reason": None}
    for idx in sorted(pending):
        slot = pending[idx]
        yield {"type": "tool_call",
               "content": {"id": slot["id"] or na._new_call_id(),
                           "name": slot["name"],
                           "arguments": na._coerce_args(slot["args"])},
               "finish_reason": None}
    yield {"type": "done", "content": "", "finish_reason": finish or "stop"}


_EXHAUSTED = object()


def start_stream(call, timeout: float = 0):
    """Run `call()` (which returns a generator or a plain result) and, when it
    IS a generator, pull its FIRST chunk — all under `timeout` seconds.

    This is the response-timeout semantic for streaming: the timer bounds
    time-to-first-chunk (connection + first token). Once the stream is
    flowing it runs untimed, so a long reply is never cut off mid-sentence.
    `timeout` of 0/None means unlimited. Raises concurrent.futures.TimeoutError.

    Returns the plain result unchanged, or a generator re-primed with its
    first chunk.
    """
    import concurrent.futures as _cf

    def _start():
        out = call()
        if not hasattr(out, "__next__"):
            return out, None
        try:
            return out, next(out)
        except StopIteration:
            return out, _EXHAUSTED

    if timeout and timeout > 0:
        ex = _cf.ThreadPoolExecutor(max_workers=1)
        try:
            gen, first = ex.submit(_start).result(timeout=timeout)
        finally:
            # Never wait: on timeout the provider call may still be blocked,
            # and shutdown(wait=True) would re-block the very turn we are
            # trying to abandon.
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        gen, first = _start()

    if first is None and not hasattr(gen, "__next__"):
        return gen

    def _primed():
        if first is not _EXHAUSTED:
            yield first
        yield from gen

    return _primed()


def drain_stream(gen, on_text=None, on_thinking=None) -> dict:
    """Consume a v2 chunk generator into a normalized result dict. Optional
    callbacks fire per text/thinking delta (the engine's live-stream hooks);
    with no callbacks this simply collapses a stream into a whole reply."""
    text_parts, think_parts, tool_calls = [], [], []
    finish = None
    try:
        for chunk in gen:
            if not isinstance(chunk, dict):
                continue
            finish = chunk.get("finish_reason") or finish
            ctype = chunk.get("type")
            if ctype == "text":
                delta = str(chunk.get("content") or "")
                if delta:
                    text_parts.append(delta)
                    if on_text:
                        on_text(delta)
            elif ctype == "thinking":
                delta = str(chunk.get("content") or "")
                if delta:
                    think_parts.append(delta)
                    if on_thinking:
                        on_thinking(delta)
            elif ctype == "tool_call":
                call = chunk.get("content")
                if isinstance(call, dict):
                    tool_calls.append(call)
    finally:
        close = getattr(gen, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    return {"content": "".join(text_parts), "thinking": "".join(think_parts) or None,
            "tool_calls": tool_calls, "finish_reason": finish}


# ── the ONE call site ────────────────────────────────────────────────────────

def invoke(module, system_prompt: str, messages: list, *,
           images=None, tools=None, stream=False):
    """Call a provider script, any contract version.

    Returns a normalized result dict, a chunk generator (v2 script that honors
    stream=True), or None on failure. Exceptions from the script propagate to
    the caller (the engine's retry/timeout wrapper owns error policy).
    """
    if is_v2(module):
        raw = module.chat(system_prompt or "", messages,
                          images=images, tools=tools, stream=stream)
        if hasattr(raw, "__next__"):     # generator — engine consumes chunks
            return raw
        return normalize_result(raw)

    # Legacy shim — never streams.
    if tools is not None and callable(getattr(module, "chat_tools", None)):
        raw = module.chat_tools(system_prompt or "", messages, tools, images=images)
        return normalize_result(raw)
    if images and callable(getattr(module, "chat_image", None)):
        return normalize_result(module.chat_image(system_prompt or "", messages, images))
    if callable(getattr(module, "chat", None)):
        return normalize_result(module.chat(system_prompt or "", messages))
    log.error("[provider_contract.invoke] ✗ Script defines no callable chat()")
    return None
