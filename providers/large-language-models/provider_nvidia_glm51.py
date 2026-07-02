"""
provider_nvidia_glm51.py
=========================
Custom Script Provider for Systema Auxilium.
Uses NVIDIA's GLM-5.1 model via the NVIDIA Inference API.

Model: z-ai/glm-5.1
Endpoint: https://integrate.api.nvidia.com/v1

GLM-5.1 supports extended reasoning (chain-of-thought). Reasoning content
is stripped from the returned string — only the final reply is returned to
the assistant. If you want to see reasoning in the chat window, set
SHOW_REASONING = True below.

Point this file at:
    Settings → AI → Custom Script Provider
"""

from openai import OpenAI


# ── Configure here ────────────────────────────────────────────────────────────

API_KEY       = "nvapi-YOUR-NVIDIA-API-KEY"   # Replace with your NVIDIA API key
MODEL         = "z-ai/glm-5.1"
TEMPERATURE   = 1
TOP_P         = 1
MAX_TOKENS    = 16384
SHOW_REASONING = False  # Set True to include <think>...</think> in the reply

# ─────────────────────────────────────────────────────────────────────────────

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=API_KEY,
)


def chat(system_prompt: str, messages: list[dict]) -> str:
    """Send a streaming request to NVIDIA GLM-5.1 and return the reply."""

    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    completion = client.chat.completions.create(
        model=MODEL,
        messages=full_messages,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        max_tokens=MAX_TOKENS,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": True,
                "clear_thinking": False,
            }
        },
        stream=True,
    )

    reasoning_parts = []
    content_parts   = []

    for chunk in completion:
        if not getattr(chunk, "choices", None):
            continue
        if not chunk.choices or getattr(chunk.choices[0], "delta", None) is None:
            continue

        delta = chunk.choices[0].delta

        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            reasoning_parts.append(reasoning)

        if getattr(delta, "content", None) is not None:
            content_parts.append(delta.content)

    reply = "".join(content_parts).strip()

    if SHOW_REASONING and reasoning_parts:
        reasoning_text = "".join(reasoning_parts).strip()
        reply = f"<think>\n{reasoning_text}\n</think>\n\n{reply}"

    return reply or "No response received."


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify your API key and connection before using in Systema.
#   python provider_nvidia_glm51.py

if __name__ == "__main__":
    print("Testing NVIDIA GLM-5.1 provider...")
    print(f"Model:    {MODEL}")
    print(f"Endpoint: https://integrate.api.nvidia.com/v1")
    print("-" * 48)

    try:
        result = chat(
            system_prompt="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
        )
        print("Response:", result)
        print("-" * 48)
        print("✅ Test passed. Provider is working correctly.")
    except Exception as e:
        print(f"❌ Test failed: {e}")