"""
providers/large-language-models/anthropic_provider.py
Anthropic Claude — modular LLM provider

SETUP: Replace API_KEY and MODEL below with your values.
Get your key: https://console.anthropic.com
"""

# ─── Configuration ─────────────────────────────────────────────────────────────
API_KEY     = "sk-ant-api03-YOUR-KEY-HERE"
MODEL       = "claude-sonnet-4-5-20250929"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
# ──────────────────────────────────────────────────────────────────────────────


def chat(system_prompt: str, messages: list) -> str:
    """
    Required signature for all LLM provider scripts.

    Args:
        system_prompt : Full effective system prompt (may be empty, never None).
        messages      : List of {"role": "user"|"assistant", "content": str} dicts.
                        The latest user message is always the last entry.

    Returns:
        A non-empty string response. Raising an exception surfaces as an error.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=API_KEY)

    # Merge consecutive same-role turns (Anthropic requires strict alternation)
    merged = []
    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n" + msg["content"]
        else:
            merged.append(dict(msg))

    kwargs = {
        "model":       MODEL,
        "max_tokens":  MAX_TOKENS,
        "messages":    merged,
        "temperature": TEMPERATURE,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)
    return response.content[0].text


def chat_image(system_prompt: str, messages: list, images: list) -> str:
    """
    Optional: called when the user attaches images.

    Args:
        system_prompt : Same as chat().
        messages      : Same as chat().
        images        : List of local file paths to images.

    Returns:
        A non-empty string response.
    """
    import anthropic, base64, os

    client = anthropic.Anthropic(api_key=API_KEY)

    # Build the last user message with images
    image_content = []
    for path in images:
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        image_content.append({"type": "image", "source": {"type": "base64",
                                                            "media_type": media_type,
                                                            "data": data}})

    # Attach images to the last user message
    convo = list(messages)
    if convo and convo[-1]["role"] == "user":
        last = convo.pop()
        image_content.append({"type": "text", "text": last["content"]})
        convo.append({"role": "user", "content": image_content})
    else:
        convo.append({"role": "user", "content": image_content})

    kwargs = {
        "model":      MODEL,
        "max_tokens": MAX_TOKENS,
        "messages":   convo,
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)
    return response.content[0].text