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

Contract v2: unified chat() with streaming; editable settings via Display.

Point this file at:
    Settings → AI → Custom Script Provider
"""

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY       = "YOUR_NVIDIA_API_KEY"   # Replace with your NVIDIA API key
MODEL            = "z-ai/glm-5.1"
TEMPERATURE      = 1
TOP_P            = 1
MAX_TOKENS       = 16384
REASONING_EFFORT = "high"   # DeepSeek models only — "low" for speed

# ─────────────────────────────────────────────────────────────────────────────

CONTRACT_VERSION = 2
BASE_URL = "https://integrate.api.nvidia.com/v1"

Display = {
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "Free key at build.nvidia.com",
                 "placeholder": "nvapi-..."}),
    "MODEL": ("Model", "list_dropdown", [
        "z-ai/glm-5.1",
        "deepseek-ai/deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash"],
        {"tooltip": "Editable — type any model id from the NVIDIA catalog",
         "item_tooltips": [
             "GLM-5.1 — strong all-round reasoning flagship",
             "DeepSeek V4 Pro — deepest reasoning, slower",
             "DeepSeek V4 Flash — fast, lighter reasoning"]}),
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


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified v2 entry point — always streams from NVIDIA; yields chunks when
    stream=True, otherwise collapses them into the result dict."""

    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    completion = _client().chat.completions.create(
        model=MODEL,
        messages=full_messages,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        extra_body={"chat_template_kwargs": _thinking_kwargs()},
        stream=True,
    )

    if stream:
        return _chunks(completion)

    from systema.engine.provider_contract import drain_stream
    result = drain_stream(_chunks(completion))
    result["content"] = result["content"].strip() or "No response received."
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