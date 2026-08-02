"""
providers/large-language-models/gemini_provider.py
Google Gemini — modular LLM provider (contract v2)

Text + vision + native tool calling + streaming, all through one chat().
Uses the REST generateContent endpoint so request/response shapes line up
exactly with systema.engine.native_adapters (functionDeclarations in,
functionCall parts out) — no SDK required, just `requests`.

SETUP: put your key in Settings ▸ AI ▸ Provider Settings (or edit API_KEY
below). Get one at https://aistudio.google.com/apikey
"""

# ─── Configuration (editable from Settings via Display) ───────────────────────
API_KEY     = "AIza-YOUR-KEY-HERE"
MODEL       = "gemini-2.5-flash"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
TOP_P       = None   # float 0–1, or None for the API default
TOP_K       = None   # int, or None for the API default
# ──────────────────────────────────────────────────────────────────────────────


# Gemini performs real function calling — opt into Systema's native mode
# (Settings -> System -> Tool Calling Mode -> Native).
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "gemini"

# Vision (contract v2.1). SUPPORTS_INLINE_IMAGES means a message may carry its
# own `images`, so an attachment stays anchored to the turn it arrived in
# instead of being re-stapled onto the newest user turn every request.
SUPPORTS_VISION        = True
SUPPORTS_INLINE_IMAGES = True
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "gif", "webp")

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "From aistudio.google.com/apikey",
                 "placeholder": "AIza..."}),
    "MODEL": ("Model", "list_dropdown", [
        "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
        {"tooltip": "Editable — any model id from Google's docs",
         "item_tooltips": ["Fast, cheap, capable", "Deepest reasoning",
                           "Previous-gen fast model"]}),
    "MAX_TOKENS":  ("Max tokens", "number"),
    "TEMPERATURE": ("Temperature", "number"),
}

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _generation_config() -> dict:
    cfg = {"maxOutputTokens": MAX_TOKENS, "temperature": TEMPERATURE}
    if TOP_P is not None:
        cfg["topP"] = TOP_P
    if TOP_K is not None:
        cfg["topK"] = TOP_K
    return cfg


def _inline_image(path: str) -> dict:
    import base64, mimetypes
    mime, _ = mimetypes.guess_type(path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return {"inline_data": {"mime_type": mime or "image/jpeg", "data": data}}


def _body(system_prompt, messages, images=None, tools=None) -> dict:
    # Inline images first (contract v2.1): a picture stays on the turn it was
    # sent in. The renderer emits Gemini `parts`, which the loop below forwards
    # verbatim, so positions survive.
    from systema.engine import native_adapters as na
    messages = na.render_inline_images(messages, _inline_image, "gemini")

    contents = []
    for msg in messages:
        role = "model" if msg.get("role") == "assistant" else "user"
        if isinstance(msg.get("parts"), list):
            # Already Gemini-shaped (inline images) — forward verbatim.
            contents.append({"role": role, "parts": msg["parts"]})
            continue
        content = msg.get("content")
        if isinstance(content, list):
            # Already dialect-shaped (tool turns) — forward verbatim.
            contents.append({"role": role, "parts": content})
        else:
            contents.append({"role": role, "parts": [{"text": content}]})

    if images and contents:
        # Attach the images to the final user turn.
        parts = [_inline_image(p) for p in images]
        if contents[-1]["role"] == "user":
            contents[-1]["parts"] = parts + contents[-1]["parts"]
        else:
            contents.append({"role": "user", "parts": parts})

    body = {"contents": contents, "generationConfig": _generation_config()}
    if system_prompt:
        body["system_instruction"] = {"parts": [{"text": system_prompt}]}
    if tools:
        from systema.engine import native_adapters as na
        body["tools"] = na.to_gemini_tools(tools)
    return body


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point — text, vision, native tools, streaming."""
    import requests
    from systema.engine import native_adapters as na

    body = _body(system_prompt, messages, images, tools)
    if stream:
        return _chat_stream(body)

    url = f"{_BASE}/{MODEL}:generateContent"
    response = requests.post(url, params={"key": API_KEY}, json=body)
    response.raise_for_status()
    data = response.json()
    parsed = na.parse_gemini(data)
    cand = (data.get("candidates") or [{}])[0]
    # Gemini marks reasoning parts with "thought": true.
    thinking = "".join(
        p.get("text") or "" for p in ((cand.get("content") or {}).get("parts") or [])
        if isinstance(p, dict) and p.get("thought"))
    return {
        "content": parsed.get("text") or "",
        "thinking": thinking or None,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": cand.get("finishReason"),
    }


def _chat_stream(body: dict):
    """streamGenerateContent (SSE) → contract chunks. Function calls arrive
    whole in Gemini's stream, so they are emitted as they appear."""
    import json
    import requests
    from systema.engine import native_adapters as na

    url = f"{_BASE}/{MODEL}:streamGenerateContent"
    calls, finish = [], None
    with requests.post(url, params={"key": API_KEY, "alt": "sse"},
                       json=body, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
                cand = (event.get("candidates") or [{}])[0]
            except Exception:
                continue
            finish = cand.get("finishReason") or finish
            for part in ((cand.get("content") or {}).get("parts") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("functionCall"):
                    fc = part["functionCall"]
                    calls.append({"id": na._new_call_id(),
                                  "name": fc.get("name", ""),
                                  "arguments": na._coerce_args(fc.get("args") or {})})
                elif part.get("text"):
                    kind = "thinking" if part.get("thought") else "text"
                    yield {"type": kind, "content": part["text"], "finish_reason": None}

    for call in calls:
        yield {"type": "tool_call", "content": call, "finish_reason": None}
    yield {"type": "done", "content": "", "finish_reason": finish or "stop"}


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your key before selecting it in Systema:
#   python gemini_provider.py

if __name__ == "__main__":
    print(f"Testing Gemini provider... model={MODEL}")
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
