"""
provider_nvidia.py
===================
Custom Script Provider for Systema Auxilium.
ONE provider for the NVIDIA Inference API — pick the model in Settings.

Endpoint: https://integrate.api.nvidia.com/v1

Unified chat() with streaming, native tool calling and inline (positional)
images; editable settings via Display.

Reasoning is returned separately as "thinking" — the app renders it in a
collapsible card above the reply and never sends it back to the model. It is
switchable per request via the THINKING checkbox, and how deeply via
REASONING_EFFORT.

CAPABILITIES ARE A TABLE, NOT A GUESS (_MODEL_INFO, researched 2026-08-18).
Both halves of this file used to infer capability from the model STRING, and
both were wrong in ways nothing reported:

  * Vision was a substring match on the id ("vl", "vision", "omni", ...), so
    `minimaxai/minimax-m3` — which NVIDIA's own reference page documents as
    accepting text, image AND video — was declared blind, and the Model
    dropdown's own tooltip said so out loud while the item tooltip beside it
    said "vision capable". Both were shipped, contradicting each other.
  * Thinking was `if "deepseek" in MODEL`, so every non-DeepSeek model got
    GLM's `{enable_thinking, clear_thinking}` spelling whether or not it
    understood it — including MiniMax, whose knob is `thinking_mode`, and
    Inkling, whose knob is `reasoning_effort`.

Chat-template kwargs are per FAMILY and not interchangeable; DeepSeek V4 in
particular HANGS rather than erroring when its keys are missing. Unknown ids
(anything typed into Custom…) still fall back to the string heuristics, which
is a guess and is documented as one at the call site.

Point this file at:
    Settings → AI → Custom Script Provider
"""

import base64
import mimetypes
import os

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY       = "YOUR_NVIDIA_API_KEY"   # Replace with your NVIDIA API key
MODEL            = "deepseek-ai/deepseek-v4-flash-0731"
TEMPERATURE      = 1
TOP_P            = 1
MAX_TOKENS       = 16384
THINKING         = True     # Extended reasoning on/off — see _thinking_kwargs()
REASONING_EFFORT = "high"   # How deep, when THINKING is on

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

# ── Per-model capability table ────────────────────────────────────────────────
# Catalog researched 2026-08-18 against build.nvidia.com, docs.api.nvidia.com
# and the NVIDIA developer forums. Two facts per model, because both were
# previously inferred from the id string and both were wrong for someone:
#
#   vision  — does the HOSTED endpoint accept image_url content blocks. The
#             gateway speaks the format at the transport level regardless;
#             this is about the MODEL, and the app reads it BEFORE an
#             attachment so the user is warned instead of failing inside the
#             base64 encoder.
#   think   — which chat-template family the model belongs to. NOT cosmetic:
#             the keys are per family and a model given the wrong ones either
#             silently ignores them (no reasoning, no error) or, for DeepSeek
#             V4, HANGS until the request times out.
#
# RETIRED — deliberately absent, do not re-add: z-ai/glm5 (deprecated
# 2026-04-20), z-ai/glm-5.1 (superseded by 5.2; it was this file's default
# and was not even in its own dropdown), moonshotai/kimi-k2.5,
# minimaxai/minimax-m2.5 and -m2.7, qwen/qwen3.5-397b-a17b (endpoint retired),
# nvidia/cosmos-reason1-7b (deprecated 2026-03-24).
_MODEL_INFO = {
    # ── vision, MEASURED: each read "47" off a green test card ────────────
    "minimaxai/minimax-m3":                          (True,  "minimax"),
    "thinkingmachines/inkling":                      (True,  "inkling"),
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning": (True,  "nemotron"),
    "nvidia/nemotron-nano-12b-v2-vl":                (True,  "nemotron"),
    # ── text only, MEASURED ───────────────────────────────────────────────
    # glm-5.2 is the reason this table exists in place of a substring guess:
    # it ACCEPTS image blocks without error and answers "Unknown unknown".
    # No exception is raised anywhere, so only asking it settles the question.
    "z-ai/glm-5.2":                                  (False, "glm"),
    "deepseek-ai/deepseek-v4-flash-0731":            (False, "deepseek_v4"),
    "nvidia/nemotron-3-ultra-550b-a55b":             (False, "nemotron"),
    "nvidia/nemotron-3-super-120b-a12b":             (False, "nemotron"),
    "nvidia/nemotron-3.5-lightning-30b-a3b":         (False, "nemotron"),
    "openai/gpt-oss-120b":                           (False, "qwen"),
    "stepfun-ai/step-3.7-flash":                     (False, None),
    "google/gemma-4-31b-it":                         (False, None),
    # Still served, kept for anyone who types them into Custom…
    "deepseek-ai/deepseek-v3.2":                     (False, "deepseek_v3"),
    "moonshotai/kimi-k2.6":                          (True,  "kimi"),
}

# NOT OFFERED, and each for a measured reason (2026-08-19, this account):
#   moonshotai/kimi-k2.6                 404 "not found for account". It IS
#                                        vision-capable (MoonViT) — hence the
#                                        True above for paid accounts — but it
#                                        cannot be called here.
#   meta/llama-3.2-90b-vision-instruct   timed out on all four probes.
#   poolside/laguna-xs-2.1               503 ResourceExhausted.
#   mistralai/mistral-nemotron           read timeout.
#
# GONE from GET /v1/models entirely — never re-add without re-checking:
#   deepseek-ai/deepseek-v4-pro, deepseek-ai/deepseek-v4-flash (superseded by
#   the dated -0731 snapshot), qwen/* (the whole Qwen endpoint was retired),
#   z-ai/glm-5.1 (this file's own former default, not even in its own
#   dropdown), z-ai/glm5.
# `GET /v1/models` is the authority — third-party catalog lists were wrong
# about four of these on the day they were copied.

# Fallback for ids typed into Custom… — a GUESS, and the only place one is
# made. The table above is authoritative for everything shipped in the
# dropdown; this exists so a VLM released after today still works without an
# edit. Being wrong here costs a refused attachment or an ignored kwarg, never
# a hallucinated answer: the app still routes real pixels when it sends any.
_VISION_MODEL_MARKERS = ("vl", "vision", "omni", "multimodal")
_THINK_FAMILY_MARKERS = (
    ("deepseek-v4", "deepseek_v4"), ("deepseek", "deepseek_v3"),
    ("glm", "glm"), ("kimi", "kimi"), ("minimax", "minimax"),
    ("inkling", "inkling"), ("nemotron", "nemotron"), ("qwen", "qwen"),
)


def _model_id() -> str:
    return (MODEL or "").strip().lower()


def SUPPORTS_VISION() -> bool:
    """True when the SELECTED model accepts images."""
    known = _MODEL_INFO.get(_model_id())
    if known is not None:
        return known[0]
    tail = _model_id().split("/")[-1]
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
        ("DeepSeek V4 Flash",              "deepseek-ai/deepseek-v4-flash-0731"),
        ("MiniMax M3 (Vision)",            "minimaxai/minimax-m3"),
        ("Inkling (Vision)",               "thinkingmachines/inkling"),
        ("Nemotron 3 Nano Omni (Vision)",  "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
        ("Nemotron Nano 12B VL (Vision)",  "nvidia/nemotron-nano-12b-v2-vl"),
        ("GLM 5.2",                        "z-ai/glm-5.2"),
        ("Nemotron 3 Ultra 550B",          "nvidia/nemotron-3-ultra-550b-a55b"),
        ("Nemotron 3 Super 120B",          "nvidia/nemotron-3-super-120b-a12b"),
        ("Nemotron 3.5 Lightning 30B",     "nvidia/nemotron-3.5-lightning-30b-a3b"),
        ("GPT-OSS 120B",                   "openai/gpt-oss-120b"),
        ("Step 3.7 Flash",                 "stepfun-ai/step-3.7-flash"),
        ("Gemma 4 31B",                    "google/gemma-4-31b-it")],
        {"tooltip": "Editable — type any model id from the NVIDIA catalog. Every "
                    "entry here was called live on 2026-08-19 and answered; "
                    "(Vision) entries actually read a number off a test picture. "
                    "The rest are text-only and the app says so before you attach.",
         "item_tooltips": [
             "deepseek-ai/deepseek-v4-flash-0731 — fast, strong reasoning, native tools. Text only. Default. (The undated id was retired; NVIDIA now serves this dated snapshot.)",
             "minimaxai/minimax-m3 — VISION (image + video). 1M context, switchable thinking. Popular, so it rate-limits (429) more than the rest.",
             "thinkingmachines/inkling — VISION (image + audio). Mamba-hybrid MoE; thinking effort is a full ladder, none..max.",
             "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning — VISION (image, video, audio). 30B MoE, 256K context.",
             "nvidia/nemotron-nano-12b-v2-vl — VISION. Small, quick vision-language model; good when the big ones are busy.",
             "z-ai/glm-5.2 — reasoning + native tools. Replaces GLM 5 and 5.1, both retired. Text only: it ACCEPTS images and answers without seeing them, so the app blocks attachments.",
             "nvidia/nemotron-3-ultra-550b-a55b — NVIDIA's 550B/55B-active reasoning flagship. Text only.",
             "nvidia/nemotron-3-super-120b-a12b — mid-size Nemotron 3, lighter than Ultra. Text only.",
             "nvidia/nemotron-3.5-lightning-30b-a3b — fastest Nemotron here. Text only.",
             "openai/gpt-oss-120b — open-weight GPT-OSS, reasoning. Text only.",
             "stepfun-ai/step-3.7-flash — fast; always reasons and ignores the Extended reasoning switch. Text only.",
             "google/gemma-4-31b-it — no extended reasoning at all. Text only."]}),
    "THINKING": ("Extended reasoning", "checkbox",
        {"tooltip": "Ask the model to think before answering. Off is faster and "
                    "cheaper; the thinking card disappears. Models with no "
                    "reasoning mode (the Llama vision ones) ignore this."}),
    "REASONING_EFFORT": ("Reasoning effort", "list_dropdown",
        ["low", "medium", "high", "max"],
        {"tooltip": "How deep to think, when Extended reasoning is on. Read by "
                    "DeepSeek V4 (high/max only — lower is clamped up), Inkling "
                    "(full ladder) and MiniMax M3 (low/medium pick its adaptive "
                    "mode). Other families are simply on or off and ignore this."}),
    "NOTE_1": ("NOTE: the NVIDIA catalog rotates and retires ids — GLM 5, GLM "
               "5.1, Kimi K2.5, MiniMax M2.x and the Qwen 3.5 endpoint are all "
               "gone. If a model starts erroring, pick another here or type a "
               "current id from build.nvidia.com into Custom….", "info_box"),
}


# ── Reasoning: per-family chat_template_kwargs ────────────────────────────────
# Every family spells this differently and the keys do NOT carry across. Two
# things depend on getting it right:
#   * DeepSeek V4 HANGS — not errors, hangs — when its keys are absent, which
#     is why the "off" variant still sends the block with thinking False
#     rather than sending nothing at all.
#   * A model handed another family's keys ignores them silently. That is how
#     `if "deepseek" in MODEL` shipped GLM's spelling to MiniMax and Inkling:
#     no reasoning, no card, no error, nothing to notice.
# `None` means the model has no reasoning mode; it gets an empty block.

def _effort() -> str:
    e = (REASONING_EFFORT or "high").strip().lower()
    return e if e in ("none", "minimal", "low", "medium", "high", "max") else "high"


def _think_family():
    """Which chat-template family the SELECTED model belongs to."""
    known = _MODEL_INFO.get(_model_id())
    if known is not None:
        return known[1]
    tail = _model_id().split("/")[-1]
    for marker, family in _THINK_FAMILY_MARKERS:
        if marker in tail:
            return family
    return None


def _thinking_kwargs() -> dict:
    family = _think_family()
    on = bool(THINKING)
    if family is None:
        return {}
    if family == "deepseek_v4":
        # Both keys on purpose: the reference lists `thinking`, the hang report
        # names `enable_thinking` too, and an unused chat-template kwarg is
        # harmless while a missing one costs the whole request.
        if not on:
            return {"thinking": False, "enable_thinking": False}
        effort = _effort()
        return {"thinking": True, "enable_thinking": True,
                "reasoning_effort": effort if effort in ("high", "max") else "high"}
    if family == "deepseek_v3":
        return {"thinking": on}
    if family == "glm":
        return {"enable_thinking": True, "clear_thinking": False} if on \
            else {"enable_thinking": False}
    if family == "kimi":
        return {"thinking": on}
    if family == "minimax":
        if not on:
            return {"thinking_mode": "disabled"}
        return {"thinking_mode": "adaptive" if _effort() in ("none", "minimal", "low", "medium")
                else "enabled"}
    if family == "inkling":
        return {"reasoning_effort": _effort() if on else "none"}
    # nemotron / qwen / gpt-oss
    return {"enable_thinking": on}


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
        stream=True,
    )
    # Only sent when the model HAS a reasoning mode — a model with none (the
    # Llama vision pair) should not receive an empty chat_template_kwargs block.
    think = _thinking_kwargs()
    if think:
        kwargs["extra_body"] = {"chat_template_kwargs": think}
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