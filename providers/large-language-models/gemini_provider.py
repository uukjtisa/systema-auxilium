"""
providers/large-language-models/gemini_provider.py
Google Gemini — modular LLM provider

SETUP: Replace API_KEY and MODEL below with your values.
Get your key: https://aistudio.google.com/apikey
"""

# ─── Configuration ─────────────────────────────────────────────────────────────
API_KEY     = "AIza-YOUR-KEY-HERE"
MODEL       = "gemini-2.5-flash"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
TOP_P       = None   # Set to float (0–1) or leave None for API default
TOP_K       = None   # Set to int or leave None for API default
# ──────────────────────────────────────────────────────────────────────────────

# Gemini performs real function calling, so opt into Systema's native tool-calling
# mode (Settings -> System -> Tool Calling Mode -> Native).
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "gemini"


def chat(system_prompt: str, messages: list) -> str:
    """
    Required signature for all LLM provider scripts.

    Args:
        system_prompt : Full effective system prompt (may be empty, never None).
        messages      : List of {"role": "user"|"assistant", "content": str} dicts.

    Returns:
        A non-empty string response.
    """
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=API_KEY)

    # Convert role 'assistant' → 'model' for Gemini
    contents = []
    for msg in messages:
        gemini_role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": msg["content"]}]})

    config_kwargs = {
        "max_output_tokens": MAX_TOKENS,
        "temperature":       TEMPERATURE,
    }
    if system_prompt:
        config_kwargs["system_instruction"] = system_prompt
    if TOP_P is not None:
        config_kwargs["top_p"] = TOP_P
    if TOP_K is not None:
        config_kwargs["top_k"] = TOP_K

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    return response.text


def chat_tools(system_prompt: str, messages: list, tools: list, images=None) -> dict:
    """Native function-calling entrypoint (Gemini dialect).

    Uses Gemini's REST generateContent endpoint so the request/response shapes
    line up exactly with ``systema.engine.native_adapters`` (functionDeclarations
    in, functionCall parts out). Returns the normalized result the app expects:
        {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}
    """
    import requests
    from systema.engine import native_adapters as na

    contents = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    generation_config = {"maxOutputTokens": MAX_TOKENS, "temperature": TEMPERATURE}
    if TOP_P is not None:
        generation_config["topP"] = TOP_P
    if TOP_K is not None:
        generation_config["topK"] = TOP_K

    body = {
        "contents": contents,
        "tools": na.to_gemini_tools(tools),
        "generationConfig": generation_config,
    }
    if system_prompt:
        body["system_instruction"] = {"parts": [{"text": system_prompt}]}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    response = requests.post(url, params={"key": API_KEY}, json=body, timeout=60)
    response.raise_for_status()
    return na.parse_gemini(response.json())


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your key before selecting it in Systema:
#   python gemini_provider.py

if __name__ == "__main__":
    print(f"Testing Gemini provider... model={MODEL}")
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