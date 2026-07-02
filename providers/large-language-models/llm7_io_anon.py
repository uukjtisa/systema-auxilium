"""
llm7_io_anon.py
================
Custom Script Provider for Systema Auxilium — llm7.io (no API key required).

llm7.io is a free, anonymous OpenAI-compatible API.
Cloudflare blocks Python's default User-Agent, so we spoof a browser string.

Tweak MODEL below to switch models. Run test_llm7.py to list all available ones.
"""

import json
import urllib.request
import urllib.error

# ── Configure here ────────────────────────────────────────────────────────────

MODEL       = "deepseek-v3-0324"
TEMPERATURE = 0.7
MAX_TOKENS  = 2048
TIMEOUT     = 60

# ─────────────────────────────────────────────────────────────────────────────

_BASE_URL = "https://api.llm7.io/v1"

# Cloudflare blocks Python's default User-Agent with a 1010 error.
_HEADERS = {
    "Content-Type":   "application/json",
    "Accept":         "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
}


def chat(system_prompt: str, messages: list[dict]) -> str:
    """
    Send a chat request to llm7.io and return the reply text.

    system_prompt  -- injected as the first system message if non-empty
    messages       -- list of {"role": "user"|"assistant", "content": str}
    """
    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    payload = {
        "model":       MODEL,
        "messages":    full_messages,
        "temperature": TEMPERATURE,
        "max_tokens":  MAX_TOKENS,
        "stream":      False,
    }

    req = urllib.request.Request(
        url=f"{_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_HEADERS,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"llm7.io HTTP {e.code}: {body}") from e
