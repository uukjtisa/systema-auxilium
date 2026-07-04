"""
_template.py
============================
Copy this file, rename it, and point the Custom Script Provider at it in Settings.

CONTRACT
--------
You must define exactly one function:

    def chat(system_prompt: str, messages: list[dict]) -> str

Parameters:
    system_prompt  -- The full effective system prompt as a plain string.
                      May be an empty string, never None.

    messages       -- List of {"role": ..., "content": ...} dicts.
                      Roles are only "user" or "assistant", alternating.
                      The latest user message is always the last entry.

Return:
    A non-empty string containing the assistant's reply.
    Returning None or "" will be treated as an error.
    Any uncaught exception will surface as an error in the chat window.

OPTIONAL functions (all purely opt-in — omit any you do not need):
    chat_image(system_prompt, messages, image_paths) -> str   # vision input
    chat_tools(system_prompt, messages, tools, images=None) -> dict  # native tools

NOTES
-----
- This file is reimported fresh on every request. Live edits take effect immediately.
- All configuration (API keys, URLs, models) lives here — the app forwards nothing.
- You can import any library available in your Python environment.
- You can also just use AI to build you a custom provider script. See below.

================================================================================
NOT FAMILIAR WITH PYTHON? LET AN AI WRITE THIS FOR YOU
================================================================================

You can ask any AI assistant (ChatGPT, Claude, Gemini, etc.) to build your
custom provider script. Copy and paste the prompt below, fill in the brackets,
and save whatever it produces:

--------------------------------------------------------------------------------
Write me a single self-contained Python file that acts as a custom LLM provider
for my desktop AI assistant "Systema Auxilium".

It MUST define this function:

    def chat(system_prompt: str, messages: list[dict]) -> str

where:
- system_prompt is a plain string of instructions (may be empty, never None),
- messages is a list of {"role": "user"|"assistant", "content": "..."} dicts,
  alternating, with the latest user message last,
- the function returns the assistant's reply as a non-empty string,
- any exception raised is caught and shown as an error by the app.

Optionally also define:
- def chat_image(system_prompt, messages, image_paths) -> str  (image_paths is a
  list of absolute image file paths) if my provider supports vision, and
- native function calling: set module-level SUPPORTS_NATIVE_TOOLS = True and
  define def chat_tools(system_prompt, messages, tools, images=None) -> dict that
  returns {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}.

My API details:
- Provider: [e.g. OpenAI / Mistral / Groq / OpenRouter / a local server]
- API URL: [e.g. https://api.openai.com/v1/chat/completions]
- API key: [your key, or tell the AI to leave a placeholder]
- Model: [e.g. gpt-4o / mistral-large / llama-3.1-70b]
- Does it support native function calling? [yes/no — if unsure, say no]

Use only the `requests` library (no SDK) unless I say otherwise. Keep it simple,
self-contained, and put all configuration in clearly-labeled constants at the top.
--------------------------------------------------------------------------------

Then save the script, and point the Custom Script Provider at it in Settings.
That's it.

================================================================================
"""

import requests


# == Configure your provider here =============================================

API_URL = "https://api.example.com/v1/chat/completions"
API_KEY = "your-api-key-here"
MODEL   = "your-model-name"


def _headers() -> dict:
    """Auth + content headers reused by every request below."""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


# == Required: plain text chat ================================================

def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a request to the configured provider and return the reply text."""
    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
    }

    response = requests.post(API_URL, json=payload, headers=_headers(), timeout=60)
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]


# == Optional: image / vision input ===========================================
# Define chat_image() only if your provider accepts images. When the user
# attaches an image the app calls chat_image() instead of chat(); if it is not
# defined, attachments silently fall back to chat() with the text only.

def chat_image(system_prompt: str, messages: list[dict], image_paths: list[str]) -> str:
    """Send the latest turn together with one or more images (OpenAI vision
    format: base64 data URLs). Works with GPT-4o, Kimi K2 vision, and most modern
    vision APIs."""
    import base64
    import mimetypes

    last_user_text = messages[-1]["content"] if messages else ""
    prior_messages = messages[:-1] if len(messages) > 1 else []

    # One image_url block per image, then the text prompt at the end.
    content_blocks = []
    for image_path in image_paths:
        mime_type, _ = mimetypes.guess_type(image_path)
        mime_type = mime_type or "image/jpeg"
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode()
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
        })
    content_blocks.append({"type": "text", "text": last_user_text})

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + prior_messages + [{"role": "user", "content": content_blocks}],
    }

    response = requests.post(API_URL, json=payload, headers=_headers(), timeout=60)
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]


# == Optional: native tool calling ============================================
# By default Systema Auxilium drives tools with its universal "compat" format
# (fenced tool calls described in the system prompt), which works with ANY model.
# If your provider genuinely performs function calling, opt into native mode so
# tools travel through the provider's own tools API: a lighter system prompt and
# more reliable invocation. The app auto-falls-back to compat if a provider does
# not declare native support or a model ignores the tools channel, so this is
# always safe to leave on.
#
# Set SUPPORTS_NATIVE_TOOLS below to True to enable it (leave it False if your
# endpoint accepts a `tools` param but the model just chats / writes a fence as
# text — verify with the smoke test at the bottom of this file first).

SUPPORTS_NATIVE_TOOLS = False           # set True once you've confirmed real tool_calls
NATIVE_DIALECT        = "openai"        # "openai" | "anthropic" | "gemini"


def chat_tools(system_prompt: str, messages: list[dict], tools: list,
               images=None) -> dict:
    """Native function-calling entrypoint.

    ``tools`` are Systema's CANONICAL tool definitions (name / description /
    parameters). Convert them to your provider's dialect, call the API, and
    return the NORMALIZED result the app expects:

        {"text": str | None,
         "tool_calls": [{"id": str, "name": str, "arguments": dict}, ...]}

    The helper module ``systema.engine.native_adapters`` does both the request
    conversion and the response parsing for all three dialects, so a native
    provider is only a few lines. Swap the two calls below to match NATIVE_DIALECT:
        openai    -> na.to_openai_tools     / na.parse_openai
        anthropic -> na.to_anthropic_tools  / na.parse_anthropic
        gemini    -> na.to_gemini_tools     / na.parse_gemini
    """
    from systema.engine import native_adapters as na

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        "tools": na.to_openai_tools(tools),
        "tool_choice": "auto",
    }

    response = requests.post(API_URL, json=payload, headers=_headers(), timeout=60)
    response.raise_for_status()
    return na.parse_openai(response.json())


# == Quick test ===============================================================
# Run this file directly to verify your key and connection before selecting it
# in Systema:  python _template.py

if __name__ == "__main__":
    print(f"Testing provider... model={MODEL} endpoint={API_URL}")
    print("-" * 60)
    try:
        reply = chat(
            system_prompt="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
        )
        print("Response:", reply)
        print("Test passed.")
    except Exception as e:
        print(f"Test failed: {e}")

    if SUPPORTS_NATIVE_TOOLS:
        print("\nTesting native tool calling (chat_tools)...")
        demo_tools = [{
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
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
                  "No tool_calls returned — keep SUPPORTS_NATIVE_TOOLS = False for this model.")
        except Exception as e:
            print(f"Native tool-calling test failed: {e}")
