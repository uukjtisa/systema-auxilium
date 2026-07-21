"""
provider_opencode_zen.py
==========================
Custom Script Provider for Systema Auxilium.
Uses OpenCode Zen — a free, OpenAI-compatible gateway with multiple models.

Free models (current lineup, researched 2026-07-21 — https://opencode.ai/docs/zen/;
free offerings rotate, the Model dropdown in Settings is editable):
  - deepseek-v4-flash-free   (default, fast, good reasoning)
  - big-pickle               (stealth frontier model, free for a limited time)
  - mimo-v2.5-free
  - north-mini-code-free
  - nemotron-3-ultra-free    (NVIDIA's best free model)
Paid models (billed per request) are also available — type any id from the
docs into the editable Model dropdown (e.g. deepseek-v4-pro, glm-5.2, ...).

Endpoint: https://opencode.ai/zen/v1

Contract v2: unified chat() (native tools + streaming); editable settings
via Display.

Get your API key at: https://opencode.ai/zen

Point this file at:
    Settings -> AI -> Custom Script Provider
"""

import json
from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY = "YOUR_OPENCODE_API_KEY"
MODEL   = "deepseek-v4-flash-free"   # Change to any model from the list above
BASE_URL = "https://opencode.ai/zen/v1"

TEMPERATURE  = 1
TOP_P        = 0.95
MAX_TOKENS   = 16384

# ── Native tool calling ────────────────────────────────────────────────────────
# OpenCode Zen is an OpenAI-compatible gateway, so it speaks the OpenAI
# function-calling dialect. Opt into Systema's native tool-calling mode (Settings
# -> System -> Tool Calling Mode -> Native). If a chosen model ever ignores the
# tools channel, just switch that setting back to Compatibility — nothing breaks.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

# ─────────────────────────────────────────────────────────────────────────────

CONTRACT_VERSION = 2

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "Get one at opencode.ai/zen",
                 "placeholder": "sk-..."}),
    "MODEL": ("Model", "list_dropdown", [
        "deepseek-v4-flash-free", "big-pickle", "mimo-v2.5-free",
        "north-mini-code-free", "nemotron-3-ultra-free"],
        {"tooltip": "Editable — paid ids from opencode.ai/docs/zen work too",
         "item_tooltips": [
             "DeepSeek V4 Flash — fast, good reasoning (free)",
             "Big Pickle — stealth frontier model (free, limited time)",
             "Mimo V2.5 — free",
             "North Mini Code — code-focused (free)",
             "Nemotron 3 Ultra — NVIDIA's best free model"]}),
    "NOTE_1": ("NOTE: free models rotate — when one disappears, pick another "
               "or type any id from opencode.ai/docs/zen.", "info_box"),
}


def _client() -> OpenAI:
    # Built per call so Display overrides (applied after import) take effect.
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


def _payload(system_prompt: str, messages: list, tools=None) -> dict:
    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": MAX_TOKENS,
    }
    if tools is not None:
        from systema.engine import native_adapters as na
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    return payload


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point. ``tools`` (canonical defs) are converted to the
    OpenAI dialect; the parsed calls come back in "tool_calls". stream=True
    yields contract chunks — text/thinking deltas live, tool calls assembled
    from SDK deltas and emitted complete at end of stream."""
    payload = _payload(system_prompt, messages, tools)

    if stream:
        return _chat_stream(payload)

    from systema.engine import native_adapters as na
    completion = _client().chat.completions.create(**payload)
    # model_dump() gives the plain OpenAI JSON shape parse_openai() understands.
    parsed = na.parse_openai(completion.model_dump())
    msg = completion.choices[0].message if completion.choices else None
    thinking = (getattr(msg, "reasoning", None)
                or getattr(msg, "reasoning_content", None)) if msg else None
    return {
        "content": parsed.get("text") or "",
        "thinking": thinking,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": completion.choices[0].finish_reason if completion.choices else None,
    }


def _chat_stream(payload: dict):
    """SDK stream → contract chunks (text/thinking live, complete tool calls
    assembled at end) via the shared engine helper."""
    from systema.engine.provider_contract import stream_openai_chunks
    completion = _client().chat.completions.create(**payload, stream=True)
    return stream_openai_chunks(completion)


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your API key and connection before using in Systema.
#   python provider_opencode_zen.py

if __name__ == "__main__":
    print("Testing OpenCode Zen provider...")
    print(f"Model:    {MODEL}")
    print(f"Endpoint: {BASE_URL}")
    print("-" * 48)

    try:
        result = chat(
            system_prompt="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
        )
        print("Response:", result["content"])
        print("-" * 48)
        print("Test passed. Provider is working correctly.")
    except Exception as e:
        print(f"Test failed: {e}")

    # Native tool-calling smoke test — confirms the model returns real tool_calls.
    print("\nTesting native tool calling (chat with tools)...")
    try:
        demo_tools = [{
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }]
        out = chat(
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            messages=[{"role": "user", "content": "What's the weather in Tokyo? Use the tool."}],
            tools=demo_tools,
        )
        calls = out.get("tool_calls") or []
        print("Text:", out.get("content"))
        print("Tool calls:", calls)
        if calls:
            print(f"Native tool calling works — model called '{calls[0]['name']}' "
                  f"with {calls[0]['arguments']}.")
        else:
            print("No tool_calls returned. This model may not do native tools well — "
                  "use Compatibility mode for it.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")
