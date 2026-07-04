"""
provider_nvidia_glm51.py
=========================
Custom Script Provider for Systema Auxilium.
Uses NVIDIA's GLM-5.1 model via the NVIDIA Inference API.

Model: z-ai/glm-5.1
Endpoint: https://integrate.api.nvidia.com/v1

GLM-5.1 supports extended reasoning (chain-of-thought). Reasoning content
is stripped from the returned string — only the final reply is returned to
the assistant. If you want to see reasoning in the chat window, set
SHOW_REASONING = True below.

Point this file at:
    Settings → AI → Custom Script Provider
"""

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY       = "nvapi-YOUR-NVIDIA-API-KEY"   # Replace with your NVIDIA API key
MODEL         = "z-ai/glm-5.1"
TEMPERATURE   = 1
TOP_P         = 1
MAX_TOKENS    = 16384
SHOW_REASONING = False  # Set True to include <think>...</think> in the reply

# ── Native tool calling ────────────────────────────────────────────────────────
# NVIDIA's Inference API is OpenAI-compatible, so it speaks the OpenAI
# function-calling dialect. Opt into Systema's native tool-calling mode (Settings
# -> System -> Tool Calling Mode -> Native). If a model ever ignores the tools
# channel, switch that setting back to Compatibility — nothing breaks.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

# ─────────────────────────────────────────────────────────────────────────────

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=API_KEY,
)


def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a streaming request to NVIDIA GLM-5.1 and return the reply."""

    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    completion = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": True,
                "clear_thinking": False,
            }
        },
        stream=True,
    )

    reasoning_parts = []
    content_parts   = []

    for chunk in completion:
        if not getattr(chunk, "choices", None):
            continue
        if not chunk.choices or getattr(chunk.choices[0], "delta", None) is None:
            continue

        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_parts.append(reasoning)

        if getattr(delta, "content", None) is not None:
            content_parts.append(delta.content)

    reply = "".join(content_parts).strip()

    if SHOW_REASONING and reasoning_parts:
        reasoning_text = "".join(reasoning_parts).strip()
        reply = f"<think>\n{reasoning_text}\n</think>\n\n{reply}"

    return reply or "No response received."


def chat_tools(system_prompt: str, messages: list[dict], tools: list,
               images=None) -> dict:
    """Native function-calling entrypoint (OpenAI dialect, non-streaming).

    ``tools`` are Systema's CANONICAL tool definitions; native_adapters converts
    them and parses the reply into the normalized result the app expects:
        {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}
    """
    from systema.engine import native_adapters as na

    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    completion = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        tools=na.to_openai_tools(tools),
        tool_choice="auto",
    )
    return na.parse_openai(completion.model_dump())


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your API key and connection before using in Systema.
#   python provider_nvidia_glm51.py

if __name__ == "__main__":
    print("Testing NVIDIA GLM-5.1 provider...")
    print(f"Model:    {MODEL}")
    print(f"Endpoint: https://integrate.api.nvidia.com/v1")
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

    print("\nTesting native tool calling (chat_tools)...")
    demo_tools = [{
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    }]
    try:
        out = chat_tools(
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            messages=[{"role": "user", "content": "What's the weather in Tokyo? Use the tool."}],
            tools=demo_tools,
        )
        calls = out.get("tool_calls") or []
        print("Text:", out.get("text"))
        print("Tool calls:", calls)
        print("Native tool calling works." if calls else
              "No tool_calls returned — use Compatibility mode for this model.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")