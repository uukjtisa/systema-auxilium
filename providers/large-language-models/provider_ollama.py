"""
provider_ollama.py
============================
Local Ollama provider — any locally pulled model (vision + tools + thinking +
streaming, model-dependent). Default: qwen3.5:9b. Set the model name in
Settings to whatever `ollama list` shows.

Contract v2: unified chat() with streaming; editable settings via Display.
Inline <think>...</think> reasoning is split out as "thinking" — the app
renders it in a collapsible card and never sends it back to the model.

Point the Custom Script Provider at this file in Systema Auxilium Settings.
"""

import os
import base64
import mimetypes
import requests


# ── Configure your provider here (editable from Settings via Display) ────────

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = "qwen3.5:9b"
NUM_CTX = 12288  # context window tokens (model supports up to 262k)

# Tell Systema this provider supports native tool calling and vision.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT = "openai"
SUPPORTS_VISION = True

CONTRACT_VERSION = 2

Display = {
    "BASE_URL": ("Ollama URL", "input",
                 {"tooltip": "Where the local Ollama server listens",
                  "placeholder": "http://localhost:11434"}),
    "MODEL":    ("Model", "input",
                 {"tooltip": "Any model you have pulled — run `ollama list`",
                  "placeholder": "qwen3.5:9b"}),
    "NUM_CTX":  ("Context tokens", "number",
                 {"tooltip": "Context window per request — bigger uses more RAM/VRAM"}),
    "NOTE_1":   ("NOTE: requires a running local Ollama install. Vision/tools/"
                 "thinking depend on the chosen model.", "info_box"),
}


def _model_options() -> dict:
    """Return Ollama runtime options applied to every request."""
    return {"num_ctx": NUM_CTX}


# ─────────────────────────────────────────────────────────────────────────────

# Preload the model so the first real request doesn't wait 2+ minutes for a
# cold load. Kicked off on the FIRST chat() call, never at import: the app
# imports this file just to read Display (settings screen, provider list), and
# a network call there would stall the UI and fire for a provider you aren't
# even using.
import threading

_warmed = False


def _preload_model():
    try:
        resp = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "initializing..."}],
                "stream": False,
                "options": {"num_ctx": 512},
            },
            timeout=600,  # warm-up only — real requests use the app's timeout
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"[provider_ollama] Warm-up failed (non-fatal): {exc}")


def _warm_up_once():
    global _warmed
    if _warmed:
        return
    _warmed = True
    threading.Thread(target=_preload_model, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────

def _encode_image(path: str) -> dict:
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
    }


def _build_messages(system_prompt: str, messages: list, image_paths: list = None) -> list:
    """Convert Systema messages into OpenAI-compatible messages for Ollama's API."""
    out = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Attach images only to the latest user message.
        if image_paths and role == "user" and i == len(messages) - 1:
            content_blocks = []
            for path in image_paths:
                content_blocks.append(_encode_image(path))
            content_blocks.append({"type": "text", "text": content})
            out.append({"role": role, "content": content_blocks})
        else:
            out.append({"role": role, "content": content})

    return out


def _payload(system_prompt: str, messages: list, image_paths=None, tools=None,
             stream=False) -> dict:
    payload = {
        "model": MODEL,
        "messages": _build_messages(system_prompt, messages, image_paths=image_paths),
        "stream": stream,
        "options": _model_options(),
    }
    if tools is not None:
        from systema.engine import native_adapters as na
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    return payload


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point for the local Ollama model (text/vision/tools).
    No hardcoded timeout — the app's "AI Response" timeout setting governs."""
    _warm_up_once()
    payload = _payload(system_prompt, messages, image_paths=images,
                       tools=tools, stream=stream)
    if stream:
        return _chat_stream(payload)

    from systema.engine import native_adapters as na
    from systema.engine.provider_contract import split_think_tags

    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload)
    resp.raise_for_status()
    data = resp.json()
    parsed = na.parse_openai(data)
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    thinking = msg.get("reasoning") or msg.get("reasoning_content")
    inline_think, content = split_think_tags(parsed.get("text") or "")
    return {
        "content": content,
        "thinking": thinking or inline_think,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason"),
    }


def _sse_events(resp):
    """Parse an OpenAI-style SSE response into plain event dicts."""
    import json as _json
    for line in resp.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            yield _json.loads(data)
        except Exception:
            continue


def _chat_stream(payload: dict):
    """Ollama's OpenAI-compatible SSE stream → contract chunks via the shared
    engine helper (inline <think> split, complete tool calls at end)."""
    from systema.engine.provider_contract import stream_openai_chunks
    resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, stream=True)
    resp.raise_for_status()
    return stream_openai_chunks(_sse_events(resp))


# Optional sanity-check entrypoint if run directly.
if __name__ == "__main__":
    print(f"Ollama provider (model: {MODEL}). Base URL: {BASE_URL}")
    print("Models available:", requests.get(f"{BASE_URL}/v1/models", timeout=10).json())
