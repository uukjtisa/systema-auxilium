"""
provider_nvidia.py
===================
Custom Script Provider for Systema Auxilium.
ONE provider for the NVIDIA Inference API — pick the model in Settings
(merged from the old per-model scripts: glm51 / deepseek_v4_pro /
deepseek_v4_flash).

Models: z-ai/glm-5.1, deepseek-ai/deepseek-v4-pro, deepseek-ai/deepseek-v4-flash
Endpoint: https://integrate.api.nvidia.com/v1

All three support extended reasoning. Reasoning is returned separately as
"thinking" — the app renders it in a collapsible card above the reply and
never sends it back to the model. REASONING_EFFORT applies to the DeepSeek
models only (GLM ignores it).

Unified chat() with streaming, native tool calling and inline (positional)
images; editable settings via Display.

The endpoint is OpenAI-compatible, so tools and vision both work at the
TRANSPORT level — whether a given request succeeds depends on the model you
picked in Settings. The curated GLM/DeepSeek defaults are reasoning models;
select a vision-capable model via Custom… if you want to send images.

Point this file at:
    Settings → AI → Custom Script Provider
"""

import base64
import mimetypes
import os

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY       = "YOUR_NVIDIA_API_KEY"   # Replace with your NVIDIA API key
MODEL            = "z-ai/glm-5.1"
TEMPERATURE      = 1
TOP_P            = 1
MAX_TOKENS       = 16384
REASONING_EFFORT = "high"   # DeepSeek models only — "low" for speed

# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://integrate.api.nvidia.com/v1"

# ── Native tool calling + vision ──────────────────────────────────────────────
# integrate.api.nvidia.com is an OpenAI-compatible gateway, so it speaks both
# the function-calling dialect and the image_url content-block format. Tools
# ride the one chat(tools=...) entry point — there is no second one.
# If a chosen model ignores the tools channel, switch Settings -> System ->
# Tool Calling Mode back to Compatibility; nothing breaks.
SUPPORTS_NATIVE_TOOLS  = True
NATIVE_DIALECT         = "openai"

# Vision is PER MODEL. The gateway speaks the image_url format at the TRANSPORT
# level, which is what the old blanket `SUPPORTS_VISION = True` was really
# describing — but the three curated defaults are REASONING models that cannot
# see, so the app cheerfully accepted attachments and the request failed
# upstream. Prefix-matched: NVIDIA's catalog versions fast and an exact-id set
# would silently rot. Pick a VLM via Custom… to send images.
# (Catalog checked 2026-08-02 — https://build.nvidia.com/models.)
_VISION_MODEL_MARKERS = ("vl", "vision", "omni", "multimodal", "nano-omni")


def SUPPORTS_VISION() -> bool:
    """True when the selected model id looks like a vision/multimodal one."""
    m = (MODEL or "").lower()
    tail = m.split("/")[-1]
    return any(mark in tail for mark in _VISION_MODEL_MARKERS)


SUPPORTS_INLINE_IMAGES = True
# Everything Pillow can DECODE, not just what the endpoint accepts: the encoder
# re-encodes every picture to JPEG/PNG on the way out, so the input extension
# only has to be readable. A narrow list refused a .jfif at the attach dialog
# for no reason at all.
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "jfif", "jpe", "gif",
                          "webp", "bmp", "dib", "tif", "tiff", "ico", "tga",
                          "ppm", "pgm", "pbm", "avif", "heic", "heif")

# Longest side an image is downscaled to before upload. Keeps the base64
# payload sane on an endpoint that rejects oversized requests outright.
MAX_IMAGE_DIMENSION = 1280

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "Free key at build.nvidia.com",
                 "placeholder": "nvapi-..."}),
    "MODEL": ("Model", "list_dropdown", [
        ("GLM 5.1",             "z-ai/glm-5.1"),
        ("DeepSeek V4 Pro",     "deepseek-ai/deepseek-v4-pro"),
        ("DeepSeek V4 Flash",   "deepseek-ai/deepseek-v4-flash")],
        {"tooltip": "Editable — type any model id from the NVIDIA catalog. None "
                    "of the three curated defaults accept images; pick a "
                    "vision/VL/omni model via Custom… if you need that.",
         "item_tooltips": [
             "z-ai/glm-5.1 — strong all-round reasoning flagship. Text only.",
             "deepseek-ai/deepseek-v4-pro — deepest reasoning, slower. Text only.",
             "deepseek-ai/deepseek-v4-flash — fast, lighter reasoning. Text only."]}),
    "REASONING_EFFORT": ("Reasoning effort", "list_dropdown", ["low", "high"],
        {"tooltip": "DeepSeek models only — GLM ignores this"}),
}


def _thinking_kwargs() -> dict:
    """Per-family chat_template_kwargs — GLM and DeepSeek spell it differently."""
    if "deepseek" in MODEL.lower():
        return {"thinking": True, "reasoning_effort": REASONING_EFFORT}
    return {"enable_thinking": True, "clear_thinking": False}


def _client() -> OpenAI:
    # Built per call so Display overrides (applied after import) take effect.
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


def _chunks(completion):
    """SDK stream → contract chunks (thinking + text deltas, then done)."""
    from systema.engine.provider_contract import stream_openai_chunks
    return stream_openai_chunks(completion)


def _encode_image(image_path: str) -> dict:
    """One image file -> an OpenAI-compatible base64 image_url content block.

    Downscales past MAX_IMAGE_DIMENSION when Pillow is available; without
    Pillow the file is sent as-is, since a missing optional dependency should
    degrade quality, not break the feature.
    """
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"

    try:
        import io as _io
        from PIL import Image

        with Image.open(image_path) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
                mime_type = "image/jpeg"
            w, h = img.size
            if max(w, h) > MAX_IMAGE_DIMENSION:
                scale = MAX_IMAGE_DIMENSION / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            mime_type = "image/jpeg"
            data = buf.getvalue()
        print(f"[provider] Image encoded: {len(data) / 1024:.1f} KB "
              f"({os.path.basename(image_path)})")
    except ImportError:
        with open(image_path, "rb") as f:
            data = f.read()

    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,"
                             f"{base64.b64encode(data).decode()}"},
    }


def _build_full_messages(system_prompt: str, messages: list, image_paths=None) -> list:
    """Assemble the wire messages.

    INLINE images stay on the message that owns them, so an
    attachment remains anchored to the turn it arrived in. The flat
    `image_paths` queue from attach_image_to_context() is one-shot and rides
    the final user turn instead.
    """
    from systema.engine import native_adapters as na
    messages = na.render_inline_images(messages, _encode_image, "openai")

    if image_paths:
        tail_text = messages[-1].get("content", "") if messages else ""
        prior = messages[:-1] if messages else []
        blocks = [_encode_image(p) for p in image_paths]
        if isinstance(tail_text, list):
            blocks.extend(tail_text)
        else:
            blocks.append({"type": "text", "text": tail_text})
        messages = prior + [{"role": "user", "content": blocks}]

    return ([{"role": "system", "content": system_prompt}]
            if system_prompt else []) + messages


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """The one entry point — always streams from NVIDIA; yields chunks when
    stream=True, otherwise collapses them into the result dict."""
    from systema.engine import native_adapters as na

    if isinstance(images, str):
        images = [images]
    full_messages = _build_full_messages(system_prompt, messages, image_paths=images)

    kwargs = dict(
        model=MODEL,
        messages=full_messages,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        extra_body={"chat_template_kwargs": _thinking_kwargs()},
        stream=True,
    )
    if tools:
        kwargs["tools"] = na.to_openai_tools(tools)
        kwargs["tool_choice"] = "auto"

    completion = _client().chat.completions.create(**kwargs)

    if stream:
        return _chunks(completion)

    from systema.engine.provider_contract import drain_stream
    result = drain_stream(_chunks(completion))
    # A native tool turn legitimately carries NO prose. Only call an empty
    # reply a failure when there are no tool calls either — otherwise the
    # placeholder would become the assistant's visible text for every
    # tool-only response.
    result["content"] = result["content"].strip()
    if not result["content"] and not result.get("tool_calls"):
        result["content"] = "No response received."
    return result


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your API key and connection before using in Systema.
#   python provider_nvidia.py

if __name__ == "__main__":
    print("Testing NVIDIA provider...")
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