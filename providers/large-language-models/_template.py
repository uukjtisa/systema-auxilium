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

NOTES
-----
- This file is reimported fresh on every request. Live edits take effect immediately.
- All configuration (API keys, URLs, models) lives here — the app forwards nothing.
- You can import any library available in your Python environment.
- You can also just use AI to build you a custom provider script. See below.

════════════════════════════════════════════════════════════════════════════════
NOT A FAMILIAR WITH PYTHON? LET AN AI WRITE THIS FOR YOU
════════════════════════════════════════════════════════════════════════════════

You can ask any AI assistant (ChatGPT, Claude, Gemini, etc.) to build your
custom provider script. Just copy and paste this prompt:

──────────────────────────────────────────────────────────────────────────────
I need a Python script that connects to [your AI provider name] API and acts
as a custom provider for my desktop AI assistant app.

The script must define exactly this function:

    def chat(system_prompt: str, messages: list[dict]) -> str

Where:
- system_prompt is a plain string (the AI's instructions), may be empty
- messages is a list of {"role": "user"/"assistant", "content": "..."} dicts,
  alternating, with the latest user message last
- The function must return the AI's reply as a non-empty string
- Any exception raised will be caught and shown as an error

My API details:
- Provider: [e.g. OpenAI / Mistral / Groq / etc.]
- API URL: [e.g. https://api.openai.com/v1/chat/completions]
- API Key: [your key here, or tell the AI to leave a placeholder]
- Model: [e.g. gpt-4o / mistral-large / etc.]

Use only the requests library (no SDK). Keep it simple and self-contained.
──────────────────────────────────────────────────────────────────────────────

Then save the script it gives you, and point the Custom Script Provider at it
in Settings. That's it.

════════════════════════════════════════════════════════════════════════════════
"""

import requests


# ── Configure your provider here ─────────────────────────────────────────────

API_URL = "https://api.example.com/v1/chat/completions"
API_KEY = "your-api-key-here"
MODEL   = "your-model-name"


# ─────────────────────────────────────────────────────────────────────────────

def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a request to the configured provider and return the reply text."""

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]


# ─────────────────────────────────────────────────────────────────────────────
# OPTIONAL: Image / vision support
#
# Define chat_image() if your provider supports image input.
# When the user attaches an image, the app calls chat_image() instead of chat().
# If chat_image() is NOT defined here, image attachments silently fall back to
# chat() with the text only — so this function is purely opt-in.
# ─────────────────────────────────────────────────────────────────────────────

def chat_image(system_prompt: str, messages: list[dict], image_paths: list[str]) -> str:
    """Send a request with one or more images to the configured provider.

    Parameters:
        system_prompt  -- Same as chat().
        messages       -- Same as chat(). The latest user message is last.
        image_paths    -- List of absolute paths to image files on disk.
                          Supports JPEG, PNG, GIF, WEBP.

    Return:
        A non-empty string containing the assistant's reply.

    Uses the OpenAI-compatible vision format (base64 inline images).
    Works with GPT-4o, Mistral-vision, Kimi K2.6, and most modern vision APIs.
    """
    import base64
    import mimetypes

    last_user_text = messages[-1]["content"] if messages else ""
    prior_messages = messages[:-1] if len(messages) > 1 else []

    # One image_url block per image, then the text prompt at the end
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

    vision_message = {"role": "user", "content": content_blocks}

    payload = {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + prior_messages + [vision_message],
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"]