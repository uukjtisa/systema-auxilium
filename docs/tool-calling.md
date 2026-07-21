# Tool Calling: Native and Compatibility

The assistant drives its tools — **python_interpreter**, **read/edit/write files**,
**grep**, **web_search**, and **load/unload skill** — in one of two modes. Switch under
**Settings → System → Tool Calling Mode**. Tools may be **batched** (several per
response, including multiple `python_interpreter` calls); they run in order and
return one combined observation.

`web_search` (search / open / links) is a built-in, no-API-key research tool: it
searches the web and reads pages, returning clickable result cards. It works from
a direct tool call and from inside `python_interpreter` as `web_search(...)`.
Optional higher-reliability backends (Brave Search API, Tavily, a SearXNG instance)
and an optional Playwright JS renderer can be enabled in settings; without them it
runs fully keyless.

## Compatibility (universal, always works)

Tools are described in the system prompt, and the model invokes them by writing
fenced blocks, for example:

<pre>
```python_interpreter
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

A provider opts into native by declaring the markers and handling the `tools`
argument of its unified `chat()` (see [Providers](providers.md)):

```python
CONTRACT_VERSION      = 2
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"

def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
    ...
```

The engine requires `SUPPORTS_NATIVE_TOOLS = True` and a callable entry point;
`NATIVE_DIALECT` is documentation (the dialect conversion happens inside your
script). `tools` is only passed in native mode — ignore it otherwise.

- `tools` is a list of **canonical** tool definitions:
  `{"name", "description", "parameters"}` where `parameters` is a JSON-Schema
  object.
- Return the **normalized** result the engine understands:

```python
{
  "content": str,
  "thinking": str | None,
  "tool_calls": [
    {"id": str, "name": str, "arguments": dict},
    ...
  ],
  "finish_reason": str | None,
}
```

The engine reconstructs everything else (executing the tools, appending
tool-result messages, looping) from this normalized shape.

Under **streaming**, text streams live and tool calls arrive COMPLETE at end of
stream (yielded as `{"type": "tool_call", ...}` chunks) — assemble any
fragment deltas inside the provider, or let
`provider_contract.stream_openai_chunks()` do it. Tool cards still render in
call order, exactly as in non-streaming mode.

> Legacy scripts that define `chat_tools(system_prompt, messages, tools,
> images=None) -> {"text", "tool_calls"}` still work unchanged — the loader
> shims them into the shape above.

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
def chat(system_prompt, messages, *, images=None, tools=None, stream=False):
    from systema.engine import native_adapters as na
    payload = {
        "model": MODEL,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
    }
    if tools:
        payload["tools"] = na.to_openai_tools(tools)
        payload["tool_choice"] = "auto"
    resp = requests.post(API_URL, json=payload, headers=HEADERS).json()
    parsed = na.parse_openai(resp)
    return {"content": parsed["text"] or "", "thinking": None,
            "tool_calls": parsed["tool_calls"], "finish_reason": None}
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
`__main__` smoke test — it calls `chat()` with a demo tool and prints whether
real `tool_calls` came back. If none do, leave `SUPPORTS_NATIVE_TOOLS = False` and
use Compatibility mode for that model.
