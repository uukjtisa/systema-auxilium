"""
providers/large-language-models/anthropic_provider.py
Anthropic Claude — modular LLM provider

SETUP: Replace API_KEY and MODEL below with your values.
Get your key: https://console.anthropic.com
"""

# ─── Configuration ─────────────────────────────────────────────────────────────
API_KEY     = "sk-ant-api03-YOUR-KEY-HERE"
MODEL       = "claude-sonnet-4-5-20250929"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
# ──────────────────────────────────────────────────────────────────────────────

# Claude performs real function calling, so opt into Systema's native tool-calling
# mode (Settings -> System -> Tool Calling Mode -> Native). Tools then travel
# through Anthropic's own tools API instead of the fenced compat format.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "anthropic"


def _merge_alternating(messages: list) -> list:
    """Anthropic requires strictly alternating user/assistant turns; merge any
    consecutive same-role messages into one."""
    merged = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))
    return merged


def chat(system_prompt: str, messages: list) -> str:
    """
    Required signature for all LLM provider scripts.

    Args:
        system_prompt : Full effective system prompt (may be empty, never None).
        messages      : List of {"role": "user"|"assistant", "content": str} dicts.
                        The latest user message is always the last entry.

    Returns:
        A non-empty string response. Raising an exception surfaces as an error.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=API_KEY)

    kwargs = {
        "model":       MODEL,
        "max_tokens":  MAX_TOKENS,
        "messages":    _merge_alternating(messages),
        "temperature": TEMPERATURE,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)
    return response.content[0].text


def chat_tools(system_prompt: str, messages: list, tools: list, images=None) -> dict:
    """Native function-calling entrypoint (Anthropic dialect).

    ``tools`` are Systema's CANONICAL tool definitions; native_adapters converts
    them to Anthropic's `input_schema` shape and parses the tool_use blocks back
    into the normalized result the app expects:
        {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}
    """
    import anthropic
    from systema.engine import native_adapters as na

    client = anthropic.Anthropic(api_key=API_KEY)

    kwargs = {
        "model":       MODEL,
        "max_tokens":  MAX_TOKENS,
        "messages":    _merge_alternating(messages),
        "temperature": TEMPERATURE,
        "tools":       na.to_anthropic_tools(tools),
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    # model_dump() turns the SDK object into the plain messages-API JSON
    # (content = list of text / tool_use blocks) that parse_anthropic() reads.
    response = client.messages.create(**kwargs)
    return na.parse_anthropic(response.model_dump())


def chat_image(system_prompt: str, messages: list, images: list) -> str:
    """
    Optional: called when the user attaches images.

    Args:
        system_prompt : Same as chat().
        messages      : Same as chat().
        images        : List of local file paths to images.

    Returns:
        A non-empty string response.
    """
    import anthropic, base64, os

    client = anthropic.Anthropic(api_key=API_KEY)

    # Build the last user message with images
    image_content = []
    for path in images:
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        image_content.append({"type": "image", "source": {"type": "base64",
                                                            "media_type": media_type,
                                                            "data": data}})

    # Attach images to the last user message
    convo = list(messages)
    if convo and convo[-1]["role"] == "user":
        last = convo.pop()
        image_content.append({"type": "text", "text": last["content"]})
        convo.append({"role": "user", "content": image_content})
    else:
        convo.append({"role": "user", "content": image_content})

    kwargs = {
        "model":      MODEL,
        "max_tokens": MAX_TOKENS,
        "messages":   convo,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)
    return response.content[0].text


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your key before selecting it in Systema:
#   python anthropic_provider.py

if __name__ == "__main__":
    print(f"Testing Anthropic provider... model={MODEL}")
    print("-" * 60)
    try:
        result = chat(
            system_prompt="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
        )
        print("Response:", result)
        print("Test passed.")
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
              "No tool_calls returned.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")