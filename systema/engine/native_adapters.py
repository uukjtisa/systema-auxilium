"""
native_adapters.py
==================================================================================
Translation layer between Systema Auxilium's **canonical** tool definitions and
the three native function-calling dialects (OpenAI, Anthropic, Gemini).

The engine and the provider scripts only ever speak two neutral shapes:

  CANONICAL TOOL  (input — what the tool registry produces)
  ----------------------------------------------------------
    {
      "name":        "python_interpreter",
      "description": "Run Python and see its output.",
      "parameters":  { ...JSON Schema object... }   # {"type":"object","properties":{...},"required":[...]}
    }

  NORMALIZED RESULT  (output — what every parse_*() returns)
  ----------------------------------------------------------
    {
      "text":       "assistant prose, or None",
      "tool_calls": [ {"id": str, "name": str, "arguments": dict}, ... ]   # may be empty
    }

A provider script therefore needs only ~5 lines:

    from systema.engine import native_adapters as na
    payload = {"model": MODEL, "messages": ..., "tools": na.to_openai_tools(tools)}
    resp = requests.post(URL, json=payload, headers=H).json()
    return na.parse_openai(resp)

The engine, when continuing a tool turn, asks this module to serialize the
assistant call + tool results back into the dialect's message shape so the next
request carries the tool conversation:

    msgs += na.openai_result_messages(assistant_text, tool_calls, tool_results)

This keeps the engine 100% dialect-agnostic and makes "support every provider"
a matter of picking the right three helpers.
==================================================================================
"""

from __future__ import annotations
import json
import uuid

DIALECTS = ("openai", "anthropic", "gemini")


# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────

def _new_call_id(prefix: str = "call") -> str:
    """Stable-ish unique id for a tool call (Gemini doesn't supply one)."""
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _coerce_args(raw) -> dict:
    """Tool-call arguments arrive as a JSON string (OpenAI) or a dict
    (Anthropic/Gemini). Always hand the engine a real dict."""
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except (json.JSONDecodeError, ValueError):
            # Model emitted malformed JSON args — surface raw so the tool can
            # decide, rather than silently dropping the call.
            return {"_malformed_arguments": raw}
    return {"value": raw}


def _normalized(text, tool_calls) -> dict:
    return {"text": text or None, "tool_calls": tool_calls or []}


# ──────────────────────────────────────────────────────────────────────────────
# 1. CANONICAL  →  dialect tool schema
# ──────────────────────────────────────────────────────────────────────────────

def to_openai_tools(tools: list) -> list:
    """Canonical → OpenAI `tools` array (also valid for the many
    OpenAI-compatible endpoints: Groq, Mistral, NVIDIA, Cloudflare, DeepSeek,
    OpenRouter, vLLM, Ollama compat, …)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def to_anthropic_tools(tools: list) -> list:
    """Canonical → Anthropic `tools` array (note: `input_schema`, not `parameters`)."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def to_gemini_tools(tools: list) -> list:
    """Canonical → Gemini `tools` array. Gemini nests all declarations under a
    single `functionDeclarations` entry and uses an OpenAPI-subset schema (the
    same JSON-Schema dict works for the common types)."""
    return [
        {
            "functionDeclarations": [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        }
    ]


def to_dialect_tools(dialect: str, tools: list) -> list:
    if dialect == "openai":
        return to_openai_tools(tools)
    if dialect == "anthropic":
        return to_anthropic_tools(tools)
    if dialect == "gemini":
        return to_gemini_tools(tools)
    raise ValueError(f"Unknown native dialect: {dialect!r} (expected one of {DIALECTS})")


# ──────────────────────────────────────────────────────────────────────────────
# 2. dialect response  →  NORMALIZED result
# ──────────────────────────────────────────────────────────────────────────────

def parse_openai(resp: dict) -> dict:
    """Parse an OpenAI chat-completions response."""
    try:
        msg = resp["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return _normalized(None, [])
    text = msg.get("content")
    calls = []
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function", {})
        calls.append({
            "id": tc.get("id") or _new_call_id(),
            "name": fn.get("name", ""),
            "arguments": _coerce_args(fn.get("arguments")),
        })
    return _normalized(text, calls)


def parse_anthropic(resp: dict) -> dict:
    """Parse an Anthropic messages response (content is a list of blocks)."""
    text_parts, calls = [], []
    for block in (resp.get("content") or []):
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            calls.append({
                "id": block.get("id") or _new_call_id(),
                "name": block.get("name", ""),
                "arguments": _coerce_args(block.get("input")),
            })
    text = "\n".join(p for p in text_parts if p) or None
    return _normalized(text, calls)


def parse_gemini(resp: dict) -> dict:
    """Parse a Gemini generateContent response. Gemini supplies no call id, so
    we synthesize one and reuse it when serializing the result."""
    text_parts, calls = [], []
    try:
        parts = resp["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        parts = []
    for part in parts:
        if "text" in part and part["text"]:
            text_parts.append(part["text"])
        fc = part.get("functionCall")
        if fc:
            calls.append({
                "id": _new_call_id("gemini"),
                "name": fc.get("name", ""),
                "arguments": _coerce_args(fc.get("args")),
            })
    text = "\n".join(text_parts) or None
    return _normalized(text, calls)


def parse_response(dialect: str, resp: dict) -> dict:
    if dialect == "openai":
        return parse_openai(resp)
    if dialect == "anthropic":
        return parse_anthropic(resp)
    if dialect == "gemini":
        return parse_gemini(resp)
    raise ValueError(f"Unknown native dialect: {dialect!r} (expected one of {DIALECTS})")


# ──────────────────────────────────────────────────────────────────────────────
# 3. NORMALIZED call + results  →  dialect message(s) for the next request
#
# `tool_calls`   : list of {"id","name","arguments"}  (as returned by parse_*)
# `tool_results` : list of {"id","name","content"}    ("content" = tool output str)
#
# Each returns the messages to APPEND to the running conversation so the model
# sees its own call and the tool's reply on the follow-up turn.
# ──────────────────────────────────────────────────────────────────────────────

def openai_result_messages(assistant_text, tool_calls: list, tool_results: list) -> list:
    assistant_msg = {
        "role": "assistant",
        "content": assistant_text or None,
        "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))},
            }
            for c in tool_calls
        ],
    }
    tool_msgs = [
        {"role": "tool", "tool_call_id": r["id"], "content": _as_text(r.get("content"))}
        for r in tool_results
    ]
    return [assistant_msg, *tool_msgs]


def anthropic_result_messages(assistant_text, tool_calls: list, tool_results: list) -> list:
    assistant_content = []
    if assistant_text:
        assistant_content.append({"type": "text", "text": assistant_text})
    for c in tool_calls:
        assistant_content.append({
            "type": "tool_use",
            "id": c["id"],
            "name": c["name"],
            "input": c.get("arguments", {}),
        })
    user_content = [
        {"type": "tool_result", "tool_use_id": r["id"], "content": _as_text(r.get("content"))}
        for r in tool_results
    ]
    return [
        {"role": "assistant", "content": assistant_content},
        {"role": "user", "content": user_content},
    ]


def gemini_result_messages(assistant_text, tool_calls: list, tool_results: list) -> list:
    model_parts = []
    if assistant_text:
        model_parts.append({"text": assistant_text})
    for c in tool_calls:
        model_parts.append({"functionCall": {"name": c["name"], "args": c.get("arguments", {})}})
    user_parts = [
        {"functionResponse": {"name": r["name"], "response": {"result": _as_text(r.get("content"))}}}
        for r in tool_results
    ]
    return [
        {"role": "model", "parts": model_parts},
        {"role": "user", "parts": user_parts},
    ]


def result_messages(dialect: str, assistant_text, tool_calls: list, tool_results: list) -> list:
    if dialect == "openai":
        return openai_result_messages(assistant_text, tool_calls, tool_results)
    if dialect == "anthropic":
        return anthropic_result_messages(assistant_text, tool_calls, tool_results)
    if dialect == "gemini":
        return gemini_result_messages(assistant_text, tool_calls, tool_results)
    raise ValueError(f"Unknown native dialect: {dialect!r} (expected one of {DIALECTS})")


def _as_text(content) -> str:
    """Tool output is normally a string; coerce anything else defensively."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return str(content)


# ──────────────────────────────────────────────────────────────────────────────
# 4. NEUTRAL convo  →  full dialect message list
#
# The engine assembles a dialect-AGNOSTIC "neutral" convo and this builder emits
# the concrete per-dialect messages, so the engine never branches on dialect and
# the provider script can forward the result verbatim.
#
# Neutral turn shapes (role + payload):
#   {"role": "user"|"assistant", "content": str}                      # plain text turn
#   {"role": "assistant", "content": str|None,                        # tool-CALL turn
#    "tool_calls": [{"id","name","arguments"}, ...]}
#   {"role": "tool", "results": [{"id","name","content"}, ...]}       # tool-RESULT turn
#
# Contract: every tool_call id in an assistant turn is answered by a result with
# the same id in the *immediately following* tool turn (the engine guarantees
# this so no dialect ever sees an unpaired tool_use / tool_call).
# ──────────────────────────────────────────────────────────────────────────────

def _text_role(role: str) -> str:
    """Only user/assistant are valid conversation roles; coerce anything else
    (a stray 'system' that wasn't hoisted out) to a user turn."""
    return role if role in ("user", "assistant") else "user"


def _openai_turn(turn: dict) -> list:
    role = turn.get("role")
    if role == "tool":
        return [
            {"role": "tool", "tool_call_id": r["id"], "content": _as_text(r.get("content"))}
            for r in turn.get("results", [])
        ]
    if role == "assistant" and turn.get("tool_calls"):
        return [{
            "role": "assistant",
            "content": turn.get("content") or None,
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                for c in turn["tool_calls"]
            ],
        }]
    out = {"role": _text_role(role), "content": _as_text(turn.get("content"))}
    if turn.get("images"):
        out["images"] = list(turn["images"])
    return [out]


def _anthropic_turn(turn: dict) -> list:
    role = turn.get("role")
    if role == "tool":
        return [{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": r["id"], "content": _as_text(r.get("content"))}
                for r in turn.get("results", [])
            ],
        }]
    if role == "assistant" and turn.get("tool_calls"):
        content = []
        if turn.get("content"):
            content.append({"type": "text", "text": turn["content"]})
        for c in turn["tool_calls"]:
            content.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                            "input": c.get("arguments", {})})
        return [{"role": "assistant", "content": content}]
    out = {"role": _text_role(role), "content": _as_text(turn.get("content"))}
    if turn.get("images"):
        out["images"] = list(turn["images"])
    return [out]


def _gemini_turn(turn: dict) -> list:
    role = turn.get("role")
    if role == "tool":
        return [{
            "role": "user",
            "parts": [
                {"functionResponse": {"name": r.get("name", ""),
                                      "response": {"result": _as_text(r.get("content"))}}}
                for r in turn.get("results", [])
            ],
        }]
    grole = "model" if role == "assistant" else "user"
    if role == "assistant" and turn.get("tool_calls"):
        parts = []
        if turn.get("content"):
            parts.append({"text": turn["content"]})
        for c in turn["tool_calls"]:
            parts.append({"functionCall": {"name": c["name"], "args": c.get("arguments", {})}})
        return [{"role": grole, "parts": parts}]
    out = {"role": grole, "parts": [{"text": _as_text(turn.get("content"))}]}
    if turn.get("images"):
        # Kept under "images" (not folded into parts) so render_inline_images
        # can place them with their [Image N] labels — the provider script owns
        # encoding, this layer only carries the reference through.
        out["images"] = list(turn["images"])
    return [out]


def _as_block_list(content) -> list:
    """Normalize an Anthropic message's content to a list of blocks for merging."""
    if isinstance(content, list):
        return list(content)
    return [{"type": "text", "text": content}] if content else []


def _merge_alternation(dialect: str, msgs: list) -> list:
    """Anthropic and Gemini require strict user/assistant(model) alternation, so
    fold consecutive same-role messages into one. OpenAI tolerates repeats (and a
    tool-call message must be followed by its tool messages), so it is left as-is."""
    if dialect == "anthropic":
        out = []
        for m in msgs:
            if out and out[-1]["role"] == m["role"]:
                out[-1]["content"] = _as_block_list(out[-1]["content"]) + _as_block_list(m["content"])
                # Carry pending inline images across the merge — rebuilding the
                # dict from role+content alone silently dropped them.
                if m.get("images"):
                    out[-1].setdefault("images", []).extend(m["images"])
            else:
                merged = {"role": m["role"], "content": m["content"]}
                if m.get("images"):
                    merged["images"] = list(m["images"])
                out.append(merged)
        return out
    if dialect == "gemini":
        out = []
        for m in msgs:
            if out and out[-1]["role"] == m["role"]:
                out[-1]["parts"] = list(out[-1]["parts"]) + list(m["parts"])
                if m.get("images"):
                    out[-1].setdefault("images", []).extend(m["images"])
            else:
                merged = {"role": m["role"], "parts": list(m["parts"])}
                if m.get("images"):
                    merged["images"] = list(m["images"])
                out.append(merged)
        return out
    return msgs


# ── Inline (positional) images — contract v2.1 ───────────────────────────────

def render_inline_images(messages: list, encoder, dialect: str = "openai") -> list:
    """Turn per-message `images` lists into dialect-native multimodal content.

    Contract v2.1 lets a message carry the pictures that belong to IT:

        {"role": "user", "content": "look at this",
         "images": [{"path": "...", "n": 3, "name": "shot.png"}, ...]}

    which is what makes an image stay at its real place in the conversation
    instead of being stapled onto whatever the newest user turn happens to be.
    A script opts in with `SUPPORTS_INLINE_IMAGES = True` and calls this once;
    scripts that don't are handled by provider_contract.flatten_inline_images()
    and never see an `images` key at all.

    `encoder(path)` is the SCRIPT'S OWN image encoder, returning one ready
    dialect-native block — resize policy, compression and payload limits differ
    per backend (Cloudflare dies above ~4 MB base64, others do not), so that
    stays with the provider. This function only handles placement.

    Each picture is preceded by an `[Image N]` text block so the model can be
    told "look at image 3" and know which one that is. An image whose encoder
    raises is replaced by a text note rather than killing the whole request —
    one unreadable file must not cost the user their turn.
    """
    out = []
    for entry in messages or []:
        if not isinstance(entry, dict) or not entry.get("images"):
            out.append(entry)
            continue

        blocks = []
        for img in entry["images"]:
            path = img.get("path") if isinstance(img, dict) else img
            if not path:
                continue
            n = img.get("n") if isinstance(img, dict) else None
            label = f"[Image {n}]" if n is not None else "[Image]"
            try:
                block = encoder(path)
            except Exception as e:                      # noqa: BLE001
                blocks.append(_text_block(
                    f"{label} could not be read ({type(e).__name__}).", dialect))
                continue
            blocks.append(_text_block(label, dialect))
            blocks.append(block)

        # Whatever the turn already held goes AFTER the pictures. Read it from
        # the dialect's own field: a Gemini turn keeps its text in `parts`, and
        # looking only at `content` silently dropped every word of it.
        blocks.extend(_existing_blocks(entry, dialect))

        if not blocks:
            out.append({k: v for k, v in entry.items() if k != "images"})
            continue

        clone = {k: v for k, v in entry.items() if k not in ("images", "content", "parts")}
        if dialect == "gemini":
            clone["parts"] = blocks
        else:
            clone["content"] = blocks
        out.append(clone)
    return out


def _text_block(text: str, dialect: str) -> dict:
    """One text block in the dialect's content-block shape."""
    if dialect == "gemini":
        return {"text": text}
    return {"type": "text", "text": text}


def _existing_blocks(entry: dict, dialect: str) -> list:
    """The turn's pre-existing content as a list of dialect blocks.

    A turn reaching render_inline_images may already be shaped three ways:
    plain string content, an OpenAI/Anthropic block list, or Gemini `parts`.
    Reading only `content` loses the text of the last two.
    """
    if dialect == "gemini":
        parts = entry.get("parts")
        if isinstance(parts, list):
            return list(parts)
        text = str(entry.get("content") or "")
        return [{"text": text}] if text else []
    content = entry.get("content")
    if isinstance(content, list):
        return list(content)
    text = str(content or "")
    return [{"type": "text", "text": text}] if text else []


def build_messages(dialect: str, neutral_convo: list) -> list:
    """Turn a neutral, dialect-agnostic convo (see turn shapes above) into the
    concrete message list for `dialect`. The system prompt is NOT included here —
    the provider script still receives it separately and prepends/passes it."""
    if dialect == "openai":
        per_turn = _openai_turn
    elif dialect == "anthropic":
        per_turn = _anthropic_turn
    elif dialect == "gemini":
        per_turn = _gemini_turn
    else:
        raise ValueError(f"Unknown native dialect: {dialect!r} (expected one of {DIALECTS})")

    msgs = []
    for turn in (neutral_convo or []):
        msgs.extend(per_turn(turn))
    return _merge_alternation(dialect, msgs)
