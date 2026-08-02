"""
providers/large-language-models/anthropic_provider.py
Anthropic Claude — modular LLM provider (contract v2)

Text + vision + native tool calling + streaming, all through one chat().
Extended thinking is returned separately as "thinking" — the app shows it in
a collapsible card and never sends it back to the model.

SETUP: put your key in Settings ▸ AI ▸ Provider Settings (or edit API_KEY
below). Get one at https://console.anthropic.com
Requires: pip install anthropic
"""

# ─── Configuration (editable from Settings via Display) ───────────────────────
API_KEY     = "sk-ant-api03-YOUR-KEY-HERE"
MODEL       = "claude-sonnet-4-5-20250929"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
THINKING_BUDGET = 0        # >0 enables extended thinking (tokens); 0 = off
# ──────────────────────────────────────────────────────────────────────────────


# Claude performs real function calling, so opt into Systema's native tool-calling
# mode (Settings -> System -> Tool Calling Mode -> Native). Tools then travel
# through Anthropic's own tools API instead of the fenced compat format.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "anthropic"

# Vision (contract v2.1). SUPPORTS_INLINE_IMAGES means a message may carry its
# own `images`, so an attachment stays anchored to the turn it arrived in
# instead of being re-stapled onto the newest user turn every request.
SUPPORTS_VISION        = True
SUPPORTS_INLINE_IMAGES = True
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "gif", "webp")

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "From console.anthropic.com",
                 "placeholder": "sk-ant-api03-..."}),
    "MODEL": ("Model", "list_dropdown", [
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-5-20250929",
        "claude-haiku-4-5-20251001"],
        {"tooltip": "Editable — any model id from Anthropic's docs",
         "item_tooltips": ["Balanced flagship", "Deepest reasoning",
                           "Fastest / cheapest"]}),
    "MAX_TOKENS":  ("Max tokens", "number"),
    "TEMPERATURE": ("Temperature", "number"),
    "THINKING_BUDGET": ("Thinking budget", "number",
                        {"tooltip": "Tokens of extended thinking; 0 disables it. "
                                    "Requires temperature 1."}),
}


def _client():
    # Built per call so Display overrides (applied after import) take effect,
    # and so importing this file never touches the network.
    import anthropic
    return anthropic.Anthropic(api_key=API_KEY)


def _merge_alternating(messages: list) -> list:
    """Anthropic requires strictly alternating user/assistant turns; merge any
    consecutive same-role messages into one. Structured content (tool blocks,
    images) is passed through untouched."""
    merged = []
    for msg in messages:
        if (merged and merged[-1]["role"] == msg["role"]
                and isinstance(merged[-1].get("content"), str)
                and isinstance(msg.get("content"), str)):
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))
    return merged


_MEDIA_MAP = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".gif": "image/gif", ".webp": "image/webp"}


def _image_block(path: str) -> dict:
    """One image file -> one Anthropic base64 image block."""
    import base64, os
    media_type = _MEDIA_MAP.get(os.path.splitext(path)[1].lower(), "image/jpeg")
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return {"type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data}}


def _image_blocks(paths: list) -> list:
    import os
    return [_image_block(p) for p in paths if os.path.isfile(p)]


def _kwargs(system_prompt, messages, images=None, tools=None) -> dict:
    # Inline images first (contract v2.1): a picture stays on the turn it was
    # sent in. _merge_alternating passes structured content through untouched,
    # so rendering before the merge preserves those positions.
    from systema.engine import native_adapters as na
    messages = na.render_inline_images(messages, _image_block, "anthropic")

    convo = _merge_alternating(messages)
    if images:
        blocks = _image_blocks(images)
        if blocks:
            # Attach the images to the final user turn.
            if convo and convo[-1]["role"] == "user" and isinstance(convo[-1]["content"], str):
                last = convo.pop()
                blocks.append({"type": "text", "text": last["content"]})
                convo.append({"role": "user", "content": blocks})
            else:
                convo.append({"role": "user", "content": blocks})

    kwargs = {"model": MODEL, "max_tokens": MAX_TOKENS, "messages": convo,
              "temperature": TEMPERATURE}
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        from systema.engine import native_adapters as na
        kwargs["tools"] = na.to_anthropic_tools(tools)
    if THINKING_BUDGET and int(THINKING_BUDGET) > 0:
        # Extended thinking requires temperature 1.
        kwargs["temperature"] = 1.0
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": int(THINKING_BUDGET)}
    return kwargs


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point — text, vision, native tools, streaming."""
    from systema.engine import native_adapters as na

    kwargs = _kwargs(system_prompt, messages, images, tools)
    if stream:
        return _chat_stream(kwargs)

    response = _client().messages.create(**kwargs)
    data = response.model_dump()
    parsed = na.parse_anthropic(data)
    thinking = "".join(
        b.get("thinking") or "" for b in (data.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "thinking")
    return {
        "content": parsed.get("text") or "",
        "thinking": thinking or None,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": data.get("stop_reason"),
    }


def _chat_stream(kwargs: dict):
    """Anthropic's streaming events → contract chunks. Tool-call input arrives
    as partial JSON fragments, so calls are assembled and emitted COMPLETE at
    the end of the stream."""
    from systema.engine import native_adapters as na

    pending = {}          # block index → {"id", "name", "args"}
    stop_reason = None
    with _client().messages.stream(**kwargs) as stream:
        for event in stream:
            etype = getattr(event, "type", "")
            if etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if getattr(block, "type", "") == "tool_use":
                    pending[event.index] = {"id": getattr(block, "id", None),
                                            "name": getattr(block, "name", ""),
                                            "args": ""}
            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                dtype = getattr(delta, "type", "")
                if dtype == "text_delta":
                    yield {"type": "text", "content": delta.text, "finish_reason": None}
                elif dtype == "thinking_delta":
                    yield {"type": "thinking", "content": delta.thinking,
                           "finish_reason": None}
                elif dtype == "input_json_delta" and event.index in pending:
                    pending[event.index]["args"] += delta.partial_json
            elif etype == "message_delta":
                stop_reason = getattr(getattr(event, "delta", None),
                                      "stop_reason", None) or stop_reason

    for idx in sorted(pending):
        slot = pending[idx]
        yield {"type": "tool_call",
               "content": {"id": slot["id"] or na._new_call_id(),
                           "name": slot["name"],
                           "arguments": na._coerce_args(slot["args"])},
               "finish_reason": None}
    yield {"type": "done", "content": "", "finish_reason": stop_reason or "stop"}


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your key before selecting it in Systema:
#   python anthropic_provider.py

if __name__ == "__main__":
    print(f"Testing Anthropic provider... model={MODEL}")
    print("-" * 60)
    try:
        out = chat(system_prompt="You are a helpful assistant.",
                   messages=[{"role": "user",
                              "content": "Say 'Provider test successful.' and nothing else."}])
        print("Response:", out["content"])
        print("Test passed.")
    except Exception as e:
        print(f"Test failed: {e}")

    print("\nTesting native tool calling...")
    demo_tools = [{
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    }]
    try:
        out = chat(
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            messages=[{"role": "user", "content": "What's the weather in Tokyo? Use the tool."}],
            tools=demo_tools,
        )
        calls = out.get("tool_calls") or []
        print("Text:", out.get("content"))
        print("Tool calls:", calls)
        print("Native tool calling works." if calls else "No tool_calls returned.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")
