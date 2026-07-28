# Providers

Every AI backend in Systema Auxilium is a **self-contained Python script** you
drop into a folder. Nothing is hardcoded: no provider list in the codebase, no
edits, no restart. Drop a script in the right folder, hit Refresh in Settings,
and it appears instantly.

- LLM providers live in `resources/providers/large-language-models/`.
- TTS providers live in `resources/providers/text-to-speech/`.

Each folder ships a `_template.py` skeleton with full docstrings and a
paste-ready prompt you can hand to any AI to generate a working provider.

## LLM provider contract (version 2)

An LLM provider script declares the contract version and defines ONE function:

```python
CONTRACT_VERSION = 2

def chat(system_prompt: str, messages: list, *,
         images=None, tools=None, stream=False)
```

- `system_prompt` — the full effective system prompt as a plain string (may be
  empty, never `None`).
- `messages` — a list of `{"role": "user"|"assistant", "content": str}` dicts,
  with the latest user message last. In a native tool conversation these arrive
  already shaped for your dialect (real tool_use / tool_result turns).
- `images` — `None`, or a list of absolute image paths the user attached.
  Ignore it if your provider has no vision.
- `tools` — `None`, or canonical tool defs; only passed when native tool calling
  is on and you set `SUPPORTS_NATIVE_TOOLS = True`.
- `stream` — when `True` you *may* return a generator of chunks instead of the
  result dict (see below). Ignoring it still works.

Non-stream return:

```python
{"content": str,             # the reply (required)
 "thinking": str | None,     # reasoning tokens → collapsible card, UI-only
 "tool_calls": [{"id":..., "name":..., "arguments": {...}}],
 "finish_reason": str | None}
```

A plain string return is also accepted. The file is re-imported fresh on every
request, so live edits take effect immediately.

### Optional: streaming

With `stream=True`, return a generator yielding:

```python
{"type": "text",      "content": "...delta..."}
{"type": "thinking",  "content": "...delta..."}
{"type": "tool_call", "content": {"id":..., "name":..., "arguments": {...}}}
{"type": "done",      "content": "", "finish_reason": "stop"}
```

Text/thinking deltas render live. If your API streams tool-call *fragments*,
assemble them yourself and yield each COMPLETE call (typically at end of
stream). For OpenAI-dialect providers,
`systema.engine.provider_contract.stream_openai_chunks(events)` does all of
this for you — pass it SDK chunks or parsed SSE dicts.

### Optional: the `Display` settings form

Declare which module-level variables users may edit from Settings ▸ AI Provider.
The app auto-generates the form, persists values per script, and applies them to
the module before every request — users never open the file.

```python
Display = {
    "API_KEY": ("API Key", "input",
                {"tooltip": "Get one at example.com", "placeholder": "sk-..."}),
    "MODEL":   ("Model", "list_dropdown", ["fast-model", "smart-model"],
                {"item_tooltips": ["cheap + quick", "best quality"]}),
    "NOTE_1":  ("NOTE: free models rotate.", "info_box"),
}
```

Keys are variable NAMES. Types: `input`, `textarea`, `list_dropdown`,
`checkbox`, `number`, `file_path`, `info_box` (a read-only note — nothing
stored). The optional trailing dict supports `tooltip`, `placeholder`, and
`item_tooltips`. Key-ish names are password-masked with a Show/Hide toggle, and
`list_dropdown` is editable so users can type ids beyond your presets. Expose
only what users genuinely need — leave internal constants out.

### Optional: native function calling

```python
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"
```

Then handle the `tools` argument inside `chat()`. See
[Tool Calling](tool-calling.md). `systema/engine/native_adapters.py` converts
canonical tools to your dialect and parses responses back, so this is a few
lines.

### Legacy contract (still supported)

Scripts written against the old contract — `chat(system_prompt, messages) -> str`
plus optional `chat_image(...)` / `chat_tools(...)` — keep working untouched.
The loader wraps them into the same normalized result. They simply cannot
stream. New scripts should use the v2 contract above.

## Included LLM providers

All of these ship configured with placeholder keys — add your own key and select
the script. All are contract v2 with streaming, and support native tool calling
unless noted. Model choice is a dropdown in Settings, so ONE script covers a
whole backend.

| Script | Backend | Notes |
| --- | --- | --- |
| `openai_provider.py` | OpenAI (and any OpenAI-compatible endpoint) | Native (openai dialect); vision. Point `BASE_URL` at Groq / Mistral / OpenRouter / vLLM / LM Studio to reuse it unchanged. |
| `anthropic_provider.py` | Anthropic Claude | Native (anthropic dialect); vision; extended thinking. |
| `gemini_provider.py` | Google Gemini | Native (gemini dialect, REST); vision. |
| `provider_cloudflare.py` | Cloudflare Workers AI | Whole `@cf/...` catalog in one dropdown (Kimi, GLM, gpt-oss, Nemotron, Llama-4, Gemma, Qwen…). Multi-account failover + 24h exhaustion cache; vision on vision-capable models. |
| `provider_nvidia.py` | NVIDIA Inference API | GLM-5.1 / DeepSeek V4 Pro / Flash in one dropdown; reasoning tokens surface as thinking. |
| `provider_opencode_zen.py` | OpenCode Zen (free OpenAI-compatible gateway) | Free-model lineup in a dropdown; paid ids typeable. |
| `provider_mixed_opencode.cloudflare.py` | OpenCode Zen (text) + Cloudflare (vision) | Routes by attachment: text → OpenCode, images → Cloudflare. Both models configurable. |
| `provider_ollama.py` | Local Ollama | Any pulled model (default `qwen3.5:9b`); inline `<think>` split into thinking. Fully offline. |
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
python resources/providers/large-language-models/provider_opencode_zen.py
```
