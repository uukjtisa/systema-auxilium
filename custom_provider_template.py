"""
custom_provider_template.py
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