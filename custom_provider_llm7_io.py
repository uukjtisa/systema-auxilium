"""
llm7_io.py
===========
Custom Script Provider for Systema Auxilium — llm7.io (authenticated).

Pick a model from the list below, set it as MODEL, save, and the next
message will use it immediately (script is reimported on every request).

You will also need an API key, get it from: https://token.llm7.io/
"""

import json
import urllib.request
import urllib.error

# ── Available models (as of April 2026) ──────────────────────────────────────
#
#   DEEPSEEK
#     "deepseek-v3-0324"              ← default, strong general model
#     "deepseek-r1"                   ← reasoning/chain-of-thought
#     "deepseek-r1-0528"
#     "deepseek-r1-distill-llama-70b"
#     "deepseek-r1-distill-qwen-32b"
#     "deepseek-r1-distill-qwen-14b"
#     "deepseek-r1-distill-qwen-7b"
#     "deepseek-r1-distill-qwen-1.5b"
#     "deepseek-v2.5"
#
#   QWEN
#     "qwen3-235b-a22b"               ← Qwen3 flagship MoE
#     "qwen3-30b-a3b"
#     "qwen3-32b"
#     "qwen3-14b"
#     "qwen3-8b"
#     "qwen3-4b"
#     "qwen3-1.7b"
#     "qwen3-0.6b"
#     "qwen2.5-72b-instruct"
#     "qwen2.5-32b-instruct"
#     "qwen2.5-coder-32b-instruct"
#
#   LLAMA
#     "llama-3.3-70b-instruct"
#     "llama-3.1-8b-instruct"
#     "llama-4-maverick"
#     "llama-4-scout"
#
#   OTHER
#     "gemma-3-27b-it"
#     "phi-4"
#     "mistral-small-3.1-24b-instruct"
#     "glm-4-32b"
#
# ─────────────────────────────────────────────────────────────────────────────

MODEL       = "deepseek-v3-0324"   # ← change this to switch models
TEMPERATURE = 0.7
MAX_TOKENS  = 2048
TIMEOUT     = 60

# ── Auth ──────────────────────────────────────────────────────────────────────

_API_KEY  = "REPLACE ME!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
_BASE_URL = "https://api.llm7.io/v1"
_HEADERS  = {
    "Content-Type":    "application/json",
    "Accept":          "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Authorization":   f"Bearer {_API_KEY}",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
}

# ─────────────────────────────────────────────────────────────────────────────

def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a chat request to llm7.io and return the reply text."""

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
