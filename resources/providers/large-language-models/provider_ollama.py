"""
provider_ollama.py
============================
Local Ollama provider — any locally pulled model (vision + tools + thinking +
streaming, model-dependent). Default: qwen3.5:9b. Set the model name in
Settings to whatever `ollama list` shows.

Unified chat() with streaming; editable settings via Display.
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

# Vision is PER MODEL — you choose what Ollama has pulled locally, and most
# local models are text-only. The old blanket True meant the app accepted an
# attachment for a text-only model and the picture was silently ignored by the
# runtime, which reads as "vision is broken" rather than "wrong model".
# Name-matched against the vision-capable families Ollama ships, because there
# is no local capability API to ask and the tag is all we have.
_VISION_NAME_MARKERS = ("llava", "vision", "-vl", "bakllava", "moondream",
                        "minicpm-v", "gemma3", "qwen2.5vl", "qwen3-vl")


def SUPPORTS_VISION() -> bool:
    """True when the local model's name marks it as vision-capable."""
    m = (MODEL or "").lower()
    return any(mark in m for mark in _VISION_NAME_MARKERS)


# Longest side an image is downscaled to before upload.
MAX_IMAGE_DIMENSION = 1280

# Messages may carry their own `images`, so an attachment stays
# anchored to the turn it arrived in rather than being moved onto the newest
# user turn on every request. Requires a vision-capable local model (llava,
# qwen-vl, llama3.2-vision, …) — a text-only model will simply ignore them.
SUPPORTS_INLINE_IMAGES = True
# Everything Pillow can DECODE, not just what the endpoint accepts: the encoder
# re-encodes every picture to JPEG/PNG on the way out, so the input extension
# only has to be readable. A narrow list refused a .jfif at the attach dialog
# for no reason at all.
IMAGE_FORMATS = ("png", "jpg", "jpeg", "jfif", "jpe", "gif", "webp", "bmp",
                 "dib", "tif", "tiff", "ico", "tga", "ppm", "pgm", "pbm",
                 "avif", "heic", "heif")

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
                 "thinking depend on the chosen model. Image attachments are "
                 "offered only for models whose name marks them vision-capable "
                 "(llava, *-vl, gemma3, moondream, minicpm-v, *vision*).",
                 "info_box"),
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
    """One image file -> an OpenAI-compatible base64 image_url content block.

    Re-encodes through Pillow rather than shipping the file's own bytes, which
    is what makes the odd extensions work: a .jfif, .bmp, .tif or a CMYK jpeg
    is decoded and written back out as plain RGB JPEG (or PNG when it has an
    alpha channel), so the runtime only ever sees a format it accepts. Falls
    back to the raw bytes when Pillow is absent — a missing optional dependency
    should cost quality, not the feature.
    """
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "image/jpeg"
    try:
        import io as _io
        from PIL import Image

        with Image.open(path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img, fmt, mime_type = img.convert("RGBA"), "PNG", "image/png"
            else:
                img, fmt, mime_type = img.convert("RGB"), "JPEG", "image/jpeg"
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
    b64 = base64.b64encode(data).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{b64}"},
    }


def _build_messages(system_prompt: str, messages: list, image_paths: list = None) -> list:
    """Convert Systema messages into OpenAI-compatible messages for Ollama's API.

    Two image channels: INLINE images ride the message that owns them
    (rendered first), while the flat `image_paths` queue from
    attach_image_to_context() is ephemeral and still rides the final user turn.
    """
    from systema.engine import native_adapters as na
    messages = na.render_inline_images(messages, _encode_image, "openai")

    out = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Attach the ephemeral queue only to the latest user message.
        if image_paths and role == "user" and i == len(messages) - 1:
            content_blocks = [_encode_image(path) for path in image_paths]
            # `content` is already a block LIST when this turn also carried
            # inline images — extend in that case, or the rendered blocks would
            # end up nested inside a text block and silently lost.
            if isinstance(content, list):
                content_blocks.extend(content)
            else:
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
    """The one entry point for the local Ollama model (text/vision/tools).
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
