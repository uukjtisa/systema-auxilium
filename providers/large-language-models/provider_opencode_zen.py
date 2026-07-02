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
