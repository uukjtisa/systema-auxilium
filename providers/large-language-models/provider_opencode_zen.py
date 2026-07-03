"""
provider_opencode_zen.py
==========================
Custom Script Provider for Systema Auxilium.
Uses OpenCode Zen — a free, OpenAI-compatible gateway with multiple models.

Models available (all free):
  - deepseek-v4-flash-free   (default, fast, good reasoning)
  - minimax-m2.5-free        (strong all-rounder)
  - kimi-k2-free             (long context, strong reasoning)
  - glm-5-free               (good for Chinese + English)
  - nemotron-3-ultra-free    (NVIDIA's best free model)

Endpoint: https://opencode.ai/zen/v1

Get your API key at: https://opencode.ai/zen

Point this file at:
    Settings -> AI -> Custom Script Provider
"""

import json
from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY = "sk-YOUR-API-KEY"
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

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a request to OpenCode Zen and return the reply text."""

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": MAX_TOKENS,
    }

    completion = client.chat.completions.create(**payload)

    return completion.choices[0].message.content or "No response received."


def chat_tools(system_prompt: str, messages: list[dict], tools: list,
               images=None) -> dict:
    """Native function-calling entrypoint.

    ``tools`` are Systema's CANONICAL tool defs (name/description/parameters).
    We convert them to the OpenAI dialect, call the gateway, and return the
    NORMALIZED result the app expects::

        {"text": str | None,
         "tool_calls": [{"id": str, "name": str, "arguments": dict}, ...]}

    ``systema.engine.native_adapters`` does both conversions.
    """
    from systema.engine import native_adapters as na

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        "tools": na.to_openai_tools(tools),
        "tool_choice": "auto",
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": MAX_TOKENS,
    }

    completion = client.chat.completions.create(**payload)
    # The OpenAI SDK returns a pydantic object; model_dump() gives the plain
    # OpenAI JSON shape that parse_openai() understands.
    return na.parse_openai(completion.model_dump())


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
        print("Response:", result)
        print("-" * 48)
        print("Test passed. Provider is working correctly.")
    except Exception as e:
        print(f"Test failed: {e}")

    # Native tool-calling smoke test — confirms the model returns real tool_calls.
    print("\nTesting native tool calling (chat_tools)...")
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
        out = chat_tools(
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            messages=[{"role": "user", "content": "What's the weather in Tokyo? Use the tool."}],
            tools=demo_tools,
        )
        calls = out.get("tool_calls") or []
        print("Text:", out.get("text"))
        print("Tool calls:", calls)
        if calls:
            print(f"Native tool calling works — model called '{calls[0]['name']}' "
                  f"with {calls[0]['arguments']}.")
        else:
            print("No tool_calls returned. This model may not do native tools well — "
                  "use Compatibility mode for it.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")
