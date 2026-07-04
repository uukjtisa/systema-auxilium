# Providers

Every AI backend in Systema Auxilium is a **self-contained Python script** you
drop into a folder. Nothing is hardcoded: no provider list in the codebase, no
edits, no restart. Drop a script in the right folder, hit Refresh in Settings,
and it appears instantly.

- LLM providers live in `providers/large-language-models/`.
- TTS providers live in `providers/text-to-speech/`.

Each folder ships a `_template.py` skeleton with full docstrings and a
paste-ready prompt you can hand to any AI to generate a working provider.

## LLM provider contract

An LLM provider script must define one function:

```python
def chat(system_prompt: str, messages: list[dict]) -> str
```

- `system_prompt` — the full effective system prompt as a plain string (may be
  empty, never `None`).
- `messages` — a list of `{"role": "user"|"assistant", "content": str}` dicts,
  alternating, with the latest user message last.
- Returns the assistant's reply as a **non-empty string**. Returning `None`/`""`
  or raising surfaces as an error in the chat window.

The file is re-imported fresh on every request, so live edits take effect
immediately. All configuration (keys, URLs, models) lives in the script — the app
forwards nothing.

### Optional: vision

```python
def chat_image(system_prompt, messages, image_paths) -> str
```

Called instead of `chat()` when the user attaches images. `image_paths` is a list
of absolute file paths. If it is not defined, attachments fall back to `chat()`
with text only. The included template uses the OpenAI base64 vision format.

### Optional: native function calling

```python
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"

def chat_tools(system_prompt, messages, tools, images=None) -> dict
```

See [Tool Calling](tool-calling.md) for the full explanation. In short: convert
the canonical `tools` to your provider's dialect, call the API, and return the
normalized `{"text": ..., "tool_calls": [...]}` result. The helper module
`systema/engine/native_adapters.py` does the conversion and parsing for all three
dialects, so `chat_tools` is only a few lines.

## Included LLM providers

All of these ship configured with placeholder keys — add your own key and select
the script. All support native tool calling unless noted.

| Script | Backend | Notes |
| --- | --- | --- |
| `anthropic_provider.py` | Anthropic Claude | Native (anthropic dialect); vision. |
| `gemini_provider.py` | Google Gemini | Native (gemini dialect, REST). |
| `provider_cloudflare_kimi_k2_6.py` | Cloudflare Workers AI / Kimi K2.6 | Native (openai); vision-aware `chat_tools`. |
| `provider_cloudflare_kimi_k2_7_code.py` | Cloudflare Workers AI / Kimi K2.7 (coding) | Native (openai). |
| `provider_nvidia_glm51.py` | NVIDIA Inference API / GLM-5.1 | Native (openai); streaming chat with reasoning. |
| `provider_nvidia_deepseek_v4_flash.py` | NVIDIA / DeepSeek V4 Flash | Native (openai). |
| `provider_nvidia_deepseek_v4_pro.py` | NVIDIA / DeepSeek V4 Pro | Native (openai). |
| `provider_opencode_zen.py` | OpenCode Zen (free OpenAI-compatible gateway) | Native (openai); several free models. |
| `llama-cpp-provider.py` | Local GGUF via `llama-cpp-python` | Fully offline, no API key. Native tool calling is **opt-in** (`SUPPORTS_NATIVE_TOOLS = False` by default) because it depends on the model + chat template. |

## TTS provider contract

A TTS provider script must define:

```python
def speak(text: str, save_to: str) -> bool
```

- `text` — the cleaned string to synthesize (punctuation already stripped by the
  voice handler; never `None`).
- `save_to` — an absolute path ending in `.mp3`; write valid MP3 bytes there.
- Returns `True` on success, `False` on failure. Raising is caught and logged.

Included: `elevenlabs_tts.py` (expressive voice synthesis). See
[Voice & TTS](voice-and-tts.md).

## Let an AI write your provider

Both `_template.py` files contain a fill-in-the-brackets prompt. Paste it into
any AI assistant, describe your API, and save whatever it produces — then select
it in Settings. The barrier to adding a provider is essentially zero.

## Verifying a provider

Every included provider (and the template) has a `__main__` block. Run the file
directly to test your key and connection, and — if it declares native support —
to confirm the model returns real tool calls:

```bash
python providers/large-language-models/provider_opencode_zen.py
```
