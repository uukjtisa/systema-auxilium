# Tool Calling: Native and Compatibility

The assistant drives its tools — **work mode**, **execute code**, **load/unload
skill**, and **session naming** — in one of two modes. Switch under
**Settings → System → Tool Calling Mode**.

## Compatibility (universal, always works)

Tools are described in the system prompt, and the model invokes them by writing
fenced blocks, for example:

<pre>
```work_environment
print("hello")
```
</pre>

- Works with **any** model or provider — no special API support needed.
- Costs prompt tokens, and a weaker model can occasionally mis-format a call.
- The app includes recovery safeguards for malformed calls.
- This is the automatic fallback: if a provider does not declare native support
  (or a model ignores the `tools` channel), the app uses Compatibility so nothing
  breaks.

## Native (structured, more reliable)

Tool calls travel through the provider's own function-calling API. The system
prompt is rebuilt **fence-free**, so the model relies purely on the structured
tools channel.

- Invocations are well-formed by construction — there is no fenced format to
  mis-write, and nothing leaks into chat.
- Requires a provider/model that genuinely performs function calling.
- Background [tasks](tasks.md) work in native too.

> Quality still varies by model. If a particular model misbehaves in native mode,
> switch to Compatibility, which always works.

## The native contract

A provider opts into native by declaring two markers and one function:

```python
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"

def chat_tools(system_prompt, messages, tools, images=None) -> dict:
    ...
```

The engine only requires `SUPPORTS_NATIVE_TOOLS = True` and a callable
`chat_tools`; `NATIVE_DIALECT` is documentation (the dialect conversion happens
inside `chat_tools`).

- `tools` is a list of **canonical** tool definitions:
  `{"name", "description", "parameters"}` where `parameters` is a JSON-Schema
  object.
- `chat_tools` must return the **normalized** result the engine understands:

```python
{
  "text": str | None,
  "tool_calls": [
    {"id": str, "name": str, "arguments": dict},
    ...
  ],
}
```

The engine reconstructs everything else (executing the tools, appending
tool-result messages, looping) from this normalized shape.

## `native_adapters` — do it in a few lines

`systema/engine/native_adapters.py` handles schema conversion and response
parsing for all three dialects, so you rarely write parsing by hand.

Convert canonical tools **to** a dialect:

- `to_openai_tools(tools)` — OpenAI `tools` array (also valid for the many
  OpenAI-compatible endpoints: Groq, Mistral, NVIDIA, Cloudflare, DeepSeek,
  OpenRouter, vLLM, Ollama-compat, …).
- `to_anthropic_tools(tools)` — Anthropic `tools` (uses `input_schema`).
- `to_gemini_tools(tools)` — Gemini `functionDeclarations`.

Parse a raw response **back** into the normalized dict:

- `parse_openai(resp)` / `parse_anthropic(resp)` / `parse_gemini(resp)`.

Dispatch helpers `to_dialect_tools(dialect, tools)` and
`parse_response(dialect, resp)` pick by name.

### Example: an OpenAI-compatible provider

```python
def chat_tools(system_prompt, messages, tools, images=None) -> dict:
    from systema.engine import native_adapters as na
    payload = {
        "model": MODEL,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
        "tools": na.to_openai_tools(tools),
        "tool_choice": "auto",
    }
    resp = requests.post(API_URL, json=payload, headers=HEADERS, timeout=60).json()
    return na.parse_openai(resp)
```

Anthropic and Gemini are identical in shape — swap `to_openai_tools` →
`to_anthropic_tools` / `to_gemini_tools` and `parse_openai` → `parse_anthropic`
/ `parse_gemini`. Note Anthropic and the Google SDK return objects; call
`.model_dump()` (Anthropic) to get plain JSON, or use the REST endpoint (Gemini)
so the shapes line up with the parsers. The included `anthropic_provider.py` and
`gemini_provider.py` show both.

## Verifying native support on a new endpoint

Some endpoints accept a `tools` parameter but the model just chats or writes a
fence as text. Before trusting native on a new endpoint, run the provider's
`__main__` smoke test — it calls `chat_tools` with a demo tool and prints whether
real `tool_calls` came back. If none do, leave `SUPPORTS_NATIVE_TOOLS = False` and
use Compatibility mode for that model.
