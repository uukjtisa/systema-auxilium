"""
provider_opencode_zen.py
==========================
Custom Script Provider for Systema Auxilium.
Uses OpenCode Zen — a free, OpenAI-compatible gateway with multiple models.

Free models (current lineup, researched 2026-07-21 — https://opencode.ai/docs/zen/;
free offerings rotate, the Model dropdown in Settings is editable):
  - deepseek-v4-flash-free   (default, fast, good reasoning)
  - big-pickle               (stealth frontier model, free for a limited time)
  - mimo-v2.5-free
  - north-mini-code-free
  - nemotron-3-ultra-free    (NVIDIA's best free model)
Paid models (billed per request) are also available — type any id from the
docs into the editable Model dropdown (e.g. deepseek-v4-pro, glm-5.2, ...).

Endpoint: https://opencode.ai/zen/v1

Unified chat() (native tools + streaming); editable settings
via Display.

Get your API key at: https://opencode.ai/zen

Point this file at:
    Settings -> AI -> Custom Script Provider
"""

import base64
import mimetypes

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY = "YOUR_OPENCODE_API_KEY"
MODEL   = "deepseek-v4-flash-free"   # Change to any model from the list above
BASE_URL = "https://opencode.ai/zen/v1"

TEMPERATURE  = 1
TOP_P        = 0.95
MAX_TOKENS   = 16384

# ── Native tool calling ────────────────────────────────────────────────────────
# OpenCode Zen is an OpenAI-compatible gateway, so it speaks the OpenAI
# function-calling dialect. Opt into Systema's native tool-calling mode (Settings
# -> System -> Tool Calling Mode -> Native). If a chosen model ever ignores the
# tools channel, just switch that setting back to Compatibility — nothing breaks.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

# ── Vision: per model ─────────────────────────────────────────────────────────
# Every FREE model in the lineup is text/coding only, which is why
# provider_mixed_opencode.cloudflare.py exists — it routes image turns to
# Cloudflare instead. But OpenCode Zen also proxies paid frontier models that
# very much can see, and a blanket False hid them: typing `claude-opus-5` or
# `gemini-3.5-flash` into the Model box got you a provider the app believed was
# blind, so attachments were refused before they were ever sent.
#
# Declaring this honestly is load-bearing, not paperwork: the app reads it to
# warn BEFORE an attachment is made rather than failing inside a base64 encoder.
# The free lineup was researched MODEL BY MODEL on 2026-08-02 rather than
# assumed — "free implies text-only" is FALSE, which is exactly the kind of
# silent wrongness this whole per-model change exists to kill:
#
#   mimo-v2.5-free        VISION — natively omni-modal (729M ViT; image, video,
#                         audio). The one free model here that can see.
#   deepseek-v4-flash-free  no — the API path exposes no image modality
#   big-pickle              no — stealth coding model, no built-in vision
#   laguna-s-2.1-free       no — Poolside coding MoE, vision is a known gap
#   ling-3.0-flash-free     no — text-only; Ming is Ant's multimodal line
#   north-mini-code-free    no — Cohere, explicitly text-in/text-out
#   nemotron-3-ultra-free   no — planning/reasoning; Nano Omni is the multimodal one
_VISION_MODELS = frozenset({"mimo-v2.5-free"})

# The PAID catalog versions fast (gpt-5.4 → gpt-5.6 → …), so those match by
# family prefix instead: an exact-id set would silently rot into "no vision"
# every time a vendor shipped a point release.
_VISION_MODEL_PREFIXES = ("claude-", "gpt-5", "gemini-", "grok-", "qwen3.",
                          "kimi-k", "minimax-m", "mimo-")


def SUPPORTS_VISION() -> bool:
    """True when the SELECTED model accepts images — free or paid."""
    m = (MODEL or "").lower()
    return m in _VISION_MODELS or m.startswith(_VISION_MODEL_PREFIXES)


# Images ride the message that owns them rather than being stapled onto the
# newest user turn.
SUPPORTS_INLINE_IMAGES = True

# Everything Pillow can decode — NOT just what the endpoint accepts. The
# encoder below re-encodes every picture to JPEG/PNG on the way out, so the
# input extension only has to be readable, not wire-legal. A narrow list here
# refuses a .jfif at the attach dialog for no reason.
IMAGE_FORMATS = ("png", "jpg", "jpeg", "jfif", "jpe", "gif", "webp", "bmp",
                 "dib", "tif", "tiff", "ico", "tga", "ppm", "pgm", "pbm",
                 "avif", "heic", "heif")

# Longest side an image is downscaled to before upload.
MAX_IMAGE_DIMENSION = 1280

# ─────────────────────────────────────────────────────────────────────────────

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "Get one at opencode.ai/zen",
                 "placeholder": "sk-..."}),
    "MODEL": ("Model", "list_dropdown", [
        ("MiMo V2.5 (free, Vision)",        "mimo-v2.5-free"),
        ("DeepSeek V4 Flash (free)",        "deepseek-v4-flash-free"),
        ("Big Pickle (free)",               "big-pickle"),
        ("Laguna S 2.1 (free)",             "laguna-s-2.1-free"),
        ("Ling 3.0 Flash (free)",           "ling-3.0-flash-free"),
        ("North Mini Code (free)",          "north-mini-code-free"),
        ("Nemotron 3 Ultra (free)",         "nemotron-3-ultra-free"),
        ("Claude Sonnet 5 (paid, Vision)",  "claude-sonnet-5"),
        ("Gemini 3.5 Flash (paid, Vision)", "gemini-3.5-flash"),
        ("GPT-5.6 Luna (paid, Vision)",     "gpt-5.6-luna")],
        {"tooltip": "Editable — any id from opencode.ai/docs/zen works. (Vision) "
                    "entries accept image attachments; MiMo V2.5 is the one FREE "
                    "model that can see.",
         "item_tooltips": [
             "mimo-v2.5-free — VISION. Natively omni-modal (image, video, audio), 310B MoE, 1M context. The only free model here that sees.",
             "deepseek-v4-flash-free — fast, good reasoning. Text only via the API. Default.",
             "big-pickle — stealth frontier model, free for a limited time. Text only.",
             "laguna-s-2.1-free — Poolside agentic-coding MoE. Text only. (New since 2026-07-21.)",
             "ling-3.0-flash-free — Ant Group MoE, 262K context. Text only. (New since 2026-07-21.)",
             "north-mini-code-free — Cohere, code-focused. Text in, text out.",
             "nemotron-3-ultra-free — NVIDIA 550B planning/reasoning. Text only.",
             "claude-sonnet-5 — PAID, billed per request. Vision + tools.",
             "gemini-3.5-flash — PAID, billed per request. Vision + tools.",
             "gpt-5.6-luna — PAID, billed per request. Vision + tools."]}),
    "NOTE_1": ("NOTE: free models rotate — when one disappears, pick another "
               "or type any id from opencode.ai/docs/zen. Most free models are "
               "text-only; MiMo V2.5 is the exception and accepts images at no "
               "cost. Paid multimodal ids (claude-*, gpt-5*, gemini-*) also see, "
               "and bill per request.", "info_box"),
}


def _client() -> OpenAI:
    # Built per call so Display overrides (applied after import) take effect.
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


def _encode_image(path: str) -> dict:
    """One image file -> an OpenAI-compatible base64 image_url content block.

    ALWAYS re-encodes through Pillow rather than shipping the file's own bytes.
    That is what makes the odd formats work: a .jfif, .bmp, .tif or a CMYK jpeg
    is decoded and written back out as a plain RGB JPEG, so the endpoint only
    ever sees a format it accepts. Also downscales past MAX_IMAGE_DIMENSION to
    keep the base64 payload sane.

    Without Pillow the raw bytes are sent with a guessed mime type — a missing
    optional dependency should cost quality, not the whole feature.
    """
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/jpeg"
    try:
        import io as _io
        from PIL import Image

        with Image.open(path) as img:
            has_alpha = img.mode in ("RGBA", "LA", "P")
            if has_alpha:
                img = img.convert("RGBA")
                fmt, mime_type = "PNG", "image/png"
            else:
                img = img.convert("RGB")
                fmt, mime_type = "JPEG", "image/jpeg"
            w, h = img.size
            if max(w, h) > MAX_IMAGE_DIMENSION:
                scale = MAX_IMAGE_DIMENSION / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = _io.BytesIO()
            if fmt == "JPEG":
                img.save(buf, format="JPEG", quality=85, optimize=True)
            else:
                img.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
    except ImportError:
        with open(path, "rb") as f:
            data = f.read()

    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,"
                             f"{base64.b64encode(data).decode()}"},
    }


def _build_messages(system_prompt: str, messages: list, image_paths=None) -> list:
    """Assemble the wire messages, including any pictures.

    Two image channels, and BOTH must be handled or attachments silently turn
    into hallucinations: INLINE images ride the message that owns them
    (na.render_inline_images), while the flat `image_paths` queue rides the
    final user turn.
    """
    from systema.engine import native_adapters as na
    messages = na.render_inline_images(messages, _encode_image, "openai")

    if image_paths:
        tail = messages[-1].get("content", "") if messages else ""
        prior = messages[:-1] if messages else []
        blocks = [_encode_image(p) for p in image_paths]
        if isinstance(tail, list):
            blocks.extend(tail)
        else:
            blocks.append({"type": "text", "text": tail})
        messages = prior + [{"role": "user", "content": blocks}]

    return ([{"role": "system", "content": system_prompt}]
            if system_prompt else []) + messages


def _payload(system_prompt: str, messages: list, tools=None, image_paths=None) -> dict:
    payload = {
        "model": MODEL,
        "messages": _build_messages(system_prompt, messages, image_paths),
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "max_tokens": MAX_TOKENS,
    }
    if tools is not None:
        from systema.engine import native_adapters as na
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    return payload


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """The one entry point. ``tools`` (canonical defs) are converted to the
    OpenAI dialect; the parsed calls come back in "tool_calls". stream=True
    yields contract chunks — text/thinking deltas live, tool calls assembled
    from SDK deltas and emitted complete at end of stream.

    `images` used to be accepted and then DROPPED on the floor: the parameter
    existed, nothing ever read it, and the model received only the `[Image N]`
    text marker the app leaves behind. It answered by guessing, confidently,
    which is worse than refusing. Fixed 2026-08-02 along with declaring vision
    per model.
    """
    if isinstance(images, str):
        images = [images]
    payload = _payload(system_prompt, messages, tools, image_paths=images)

    if stream:
        return _chat_stream(payload)

    from systema.engine import native_adapters as na
    completion = _client().chat.completions.create(**payload)
    # model_dump() gives the plain OpenAI JSON shape parse_openai() understands.
    parsed = na.parse_openai(completion.model_dump())
    msg = completion.choices[0].message if completion.choices else None
    thinking = (getattr(msg, "reasoning", None)
                or getattr(msg, "reasoning_content", None)) if msg else None
    return {
        "content": parsed.get("text") or "",
        "thinking": thinking,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": completion.choices[0].finish_reason if completion.choices else None,
    }


def _chat_stream(payload: dict):
    """SDK stream → contract chunks (text/thinking live, complete tool calls
    assembled at end) via the shared engine helper."""
    from systema.engine.provider_contract import stream_openai_chunks
    completion = _client().chat.completions.create(**payload, stream=True)
    return stream_openai_chunks(completion)


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
        print("Response:", result["content"])
        print("-" * 48)
        print("Test passed. Provider is working correctly.")
    except Exception as e:
        print(f"Test failed: {e}")

    # Native tool-calling smoke test — confirms the model returns real tool_calls.
    print("\nTesting native tool calling (chat with tools)...")
    try:
        demo_tools = [{
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }]
        out = chat(
            system_prompt="You are a helpful assistant. Use tools when appropriate.",
            messages=[{"role": "user", "content": "What's the weather in Tokyo? Use the tool."}],
            tools=demo_tools,
        )
        calls = out.get("tool_calls") or []
        print("Text:", out.get("content"))
        print("Tool calls:", calls)
        if calls:
            print(f"Native tool calling works — model called '{calls[0]['name']}' "
                  f"with {calls[0]['arguments']}.")
        else:
            print("No tool_calls returned. This model may not do native tools well — "
                  "use Compatibility mode for it.")
    except Exception as e:
        print(f"Native tool-calling test failed: {e}")
