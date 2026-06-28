"""
native_adapters.py
==================================================================================
Translation layer between Systema Auxilium's **canonical** tool definitions and
the three native function-calling dialects (OpenAI, Anthropic, Gemini).

The engine and the provider scripts only ever speak two neutral shapes:

  CANONICAL TOOL  (input — what the tool registry produces)
  ----------------------------------------------------------
    {
      "name":        "work_environment",
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
