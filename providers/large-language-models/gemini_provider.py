"""
providers/large-language-models/gemini_provider.py
Google Gemini — modular LLM provider

SETUP: Replace API_KEY and MODEL below with your values.
Get your key: https://aistudio.google.com/apikey
"""

# ─── Configuration ─────────────────────────────────────────────────────────────
API_KEY     = "AIza-YOUR-KEY-HERE"
MODEL       = "gemini-2.5-flash"
MAX_TOKENS  = 8192
TEMPERATURE = 1.0
TOP_P       = None   # Set to float (0–1) or leave None for API default
TOP_K       = None   # Set to int or leave None for API default
# ──────────────────────────────────────────────────────────────────────────────


def chat(system_prompt: str, messages: list) -> str:
    """
    Required signature for all LLM provider scripts.

    Args:
        system_prompt : Full effective system prompt (may be empty, never None).
        messages      : List of {"role": "user"|"assistant", "content": str} dicts.

    Returns:
        A non-empty string response.
    """
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=API_KEY)

    # Convert role 'assistant' → 'model' for Gemini
    contents = []
    for msg in messages:
        gemini_role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": msg["content"]}]})

    config_kwargs = {
        "max_output_tokens": MAX_TOKENS,
        "temperature":       TEMPERATURE,
    }
    if system_prompt:
        config_kwargs["system_instruction"] = system_prompt
    if TOP_P is not None:
        config_kwargs["top_p"] = TOP_P
    if TOP_K is not None:
        config_kwargs["top_k"] = TOP_K

    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    return response.text