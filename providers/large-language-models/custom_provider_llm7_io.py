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

# ── Native tool calling (BETA) ──────────────────────────────────────────────
# llm7.io exposes a standard OpenAI-compatible /chat/completions endpoint, so it
# speaks the "openai" tool dialect. With this enabled, Systema Auxilium's Native
# Tool Calling mode routes tools through the API instead of the fenced compat
# format. NOTE: actual function-calling support depends on the chosen MODEL —
# strong instruct models (qwen3, llama-3.3-70b, deepseek-v3) handle it well;
# tiny models may ignore `tools` and just chat (the app still works, just without
# native calls). If a model misbehaves, switch back to Compatibility mode.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

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


def chat_tools(system_prompt: str, messages: list[dict], tools: list,
               images: list[str] | None = None) -> dict:
    """Native (function-calling) entrypoint — used when Tool Calling Mode = Native.

    `tools` are CANONICAL tool defs (name/description/parameters); they're
    converted to the OpenAI dialect here, and the response is parsed back into the
    NORMALIZED result the engine expects:
        {"text": str | None, "tool_calls": [{"id","name","arguments"}, ...]}

    src.engine.native_adapters handles both conversions. Vision is not supported by this
    provider, so `images` is accepted for contract parity but ignored.
    """
    from src.engine import native_adapters as na

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
    if tools:
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"

    req = urllib.request.Request(
        url=f"{_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=_HEADERS,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return na.parse_openai(data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"llm7.io HTTP {e.code}: {body}") from e
