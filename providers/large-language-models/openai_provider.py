"""
providers/large-language-models/openai_provider.py
OpenAI — modular LLM provider (contract v2)

Text + vision + native tool calling + streaming, all through one chat().
Talks the plain chat/completions REST API with `requests`, so it also works
unchanged against any OpenAI-COMPATIBLE endpoint (Groq, Mistral, Together,
OpenRouter, DeepSeek, vLLM, LM Studio, …) — just change BASE_URL and MODEL.

SETUP: put your key in Settings ▸ AI ▸ Provider Settings (or edit API_KEY
below). Get one at https://platform.openai.com/api-keys
"""

import base64
import json
import mimetypes

import requests

# ─── Configuration (editable from Settings via Display) ───────────────────────
API_KEY     = "sk-YOUR-KEY-HERE"
BASE_URL    = "https://api.openai.com/v1"
MODEL       = "gpt-5.4"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
# ──────────────────────────────────────────────────────────────────────────────

CONTRACT_VERSION = 2

# OpenAI performs real function calling — opt into Systema's native mode
# (Settings -> System -> Tool Calling Mode -> Native).
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

# Vision (contract v2.1). SUPPORTS_INLINE_IMAGES means a message may carry its
# own `images`, so an attachment stays anchored to the turn it arrived in
# instead of being re-stapled onto the newest user turn every request.
SUPPORTS_VISION        = True
SUPPORTS_INLINE_IMAGES = True
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "gif", "webp")

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "From platform.openai.com/api-keys",
                 "placeholder": "sk-..."}),
    "BASE_URL": ("Base URL", "input",
                 {"tooltip": "Change this to use any OpenAI-compatible "
                             "endpoint (Groq, Mistral, OpenRouter, vLLM, …)",
                  "placeholder": "https://api.openai.com/v1"}),
    "MODEL": ("Model", "list_dropdown",
              ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.3-codex"],
              {"tooltip": "Editable — any model id your endpoint serves",
               "item_tooltips": ["Flagship", "Cheaper / faster",
                                 "Cheapest", "Coding-tuned"]}),
    "MAX_TOKENS":  ("Max tokens", "number"),
    "TEMPERATURE": ("Temperature", "number"),
    "NOTE_1": ("NOTE: this script works with ANY OpenAI-compatible API — "
               "point Base URL at your provider and type its model id.",
               "info_box"),
}


def _headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _image_blocks(paths: list) -> list:
    return [_image_block(p) for p in paths]


def _image_block(path: str) -> dict:
    """One image file -> one OpenAI base64 image_url content block."""
    mime, _ = mimetypes.guess_type(path)
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return {"type": "image_url",
            "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{data}"}}


def _payload(system_prompt, messages, images=None, tools=None, stream=False) -> dict:
    # Inline images first (contract v2.1): a picture stays on the turn it was
    # sent in, instead of every image being re-stapled onto the newest user
    # turn on every request.
    from systema.engine import native_adapters as na
    convo = list(na.render_inline_images(messages, _image_block, "openai"))

    if images and convo:
        # Rewrite the final user turn as multimodal content blocks. This is the
        # EPHEMERAL one-shot queue (attach_image_to_context), which is why it
        # still rides the last turn rather than being anchored.
        last = convo[-1]
        if last.get("role") == "user" and isinstance(last.get("content"), str):
            convo = convo[:-1] + [{
                "role": "user",
                "content": _image_blocks(images) + [{"type": "text",
                                                     "text": last["content"]}],
            }]
        elif last.get("role") == "user" and isinstance(last.get("content"), list):
            # Turn already multimodal (it carried inline images) — prepend.
            convo = convo[:-1] + [{
                "role": "user",
                "content": _image_blocks(images) + last["content"],
            }]
        else:
            convo = convo + [{"role": "user", "content": _image_blocks(images)}]

    payload = {
        "model": MODEL,
        "messages": ([{"role": "system", "content": system_prompt}]
                     if system_prompt else []) + convo,
        "max_completion_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    if tools:
        from systema.engine import native_adapters as na
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    if stream:
        payload["stream"] = True
    return payload


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point — text, vision, native tools, streaming."""
    from systema.engine import native_adapters as na

    payload = _payload(system_prompt, messages, images, tools, stream)
    url = f"{BASE_URL.rstrip('/')}/chat/completions"

    if stream:
        return _chat_stream(url, payload)

    response = requests.post(url, json=payload, headers=_headers())
    response.raise_for_status()
    data = response.json()
    parsed = na.parse_openai(data)
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "content": parsed.get("text") or "",
        "thinking": msg.get("reasoning_content") or msg.get("reasoning"),
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason"),
    }


def _chat_stream(url: str, payload: dict):
    """SSE stream → contract chunks, via the shared engine helper (text and
    reasoning deltas live; tool-call fragments assembled and emitted complete
    at end of stream)."""
    from systema.engine.provider_contract import stream_openai_chunks

    def _events():
        with requests.post(url, json=payload, headers=_headers(),
                           stream=True) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except Exception:
                    continue

    return stream_openai_chunks(_events())


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your key before selecting it in Systema:
#   python openai_provider.py

if __name__ == "__main__":
    print(f"Testing OpenAI provider... model={MODEL} @ {BASE_URL}")
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
