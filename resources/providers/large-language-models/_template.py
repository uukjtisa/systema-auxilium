"""
_template.py
============================
Copy this file, rename it, and point the Custom Script Provider at it in Settings.

CONTRACT (version 2)
--------------------
Declare the contract version and define ONE function:

    CONTRACT_VERSION = 2

    def chat(system_prompt: str, messages: list, *,
             images=None, tools=None, stream=False):

Parameters:
    system_prompt  -- The full effective system prompt as a plain string.
                      May be an empty string, never None.

    messages       -- List of {"role": ..., "content": ...} dicts.
                      Normally roles alternate "user"/"assistant" with the
                      latest user message last. (If you opt into native tools,
                      tool turns arrive already shaped for your dialect —
                      see the native section below.)

                      A message may also carry its OWN images under an
                      "images" key, but only if you declare
                      SUPPORTS_INLINE_IMAGES = True. Otherwise the app strips
                      them before calling you.

    images         -- None, or a list of absolute paths to image files (the
                      one-shot analysis queue). Attach them to the final user
                      turn. Ignore it — and set SUPPORTS_VISION = False — if
                      your provider has no vision support.

    tools          -- None, or a list of canonical tool definitions
                      (name/description/parameters). Only passed when native
                      tool calling is enabled AND you set
                      SUPPORTS_NATIVE_TOOLS = True. Ignore otherwise.

    stream         -- False: return the result dict described below.
                      True:  you MAY instead return a generator that yields
                      chunk dicts as they arrive (see STREAMING). If you
                      ignore `stream` and return the dict, everything still
                      works — the reply just appears all at once.

Return (non-stream):
    {
        "content":       str,          # the assistant's reply (required)
        "thinking":      str or None,  # reasoning tokens, shown in a
                                       # collapsible card (optional)
        "tool_calls":    [             # only when you were given `tools`
            {"id": str, "name": str, "arguments": dict}, ...
        ],
        "finish_reason": str or None,  # e.g. "stop" / "length" (optional)
    }

    Returning a plain string is also accepted (treated as {"content": ...}).
    Empty content with no tool_calls is treated as an error.
    Any uncaught exception surfaces as an error in the chat window.

STREAMING (optional, recommended)
---------------------------------
When stream=True, return a generator yielding dicts:

    {"type": "text",      "content": "...delta...", "finish_reason": None}
    {"type": "thinking",  "content": "...delta...", "finish_reason": None}
    {"type": "tool_call", "content": {"id":..., "name":..., "arguments": {...}},
                          "finish_reason": None}
    {"type": "done",      "content": "",            "finish_reason": "stop"}

Rules:
- Yield "text" deltas as they arrive — the app renders them live.
- If your API streams tool-call fragments, assemble them yourself and yield
  each COMPLETE call as one "tool_call" chunk (typically at end of stream).
- End with a "done" chunk. If the generator just ends, that's tolerated too.

SETTINGS FORM (optional, recommended): the Display dict
-------------------------------------------------------
Declare which module-level variables users may edit from Settings ▸ AI
Provider — the app auto-generates the form, persists values per provider,
and applies them on every request. Users never have to open this file.
Only expose what users genuinely need (keys, model, real knobs) — leave
internal constants out.

    Display = {
        "API_KEY": ("API Key", "secure_input",
                    {"tooltip": "Get one at example.com/keys",
                     "placeholder": "sk-..."}),
        "BASE_URL":      ("Base URL", "input"),
        "MODEL":         ("Model", "list_dropdown", ["a", "b"],
                          {"item_tooltips": ["fast", "smart"]}),
        "PROMPT_PREFIX": ("Prompt prefix", "textarea"),
        "USE_CACHE":     ("Use cache", "checkbox"),
        "TEMPERATURE":   ("Temperature", "number"),
        "CERT_FILE":     ("Certificate", "file_path"),
        "NOTE_1": ("NOTE: anything you want users to read.", "info_box"),
    }

Keys are the VARIABLE NAMES as strings (for "info_box" the key is just a
unique id — nothing is stored). Spec per entry:
    ("Label", "type")
    ("Label", "list_dropdown", [options...])
    ("Label", "type", {opts})                       # opts dict directly
    ("Label", "list_dropdown", [options...], {opts})

Types:
    input         plain one-line text, shown as typed
    secure_input  one-line text MASKED as dots, with an eye button to reveal
    textarea      multi-line text
    list_dropdown pick from your presets, or "Custom…" to type any value
    checkbox      True / False
    number        numeric entry (int or float)
    file_path     text box + Browse… file picker
    info_box      read-only note — its LABEL is the note text, nothing stored

RULE — input vs secure_input:
    Use "secure_input" for ANY value that is a credential: API keys, tokens,
    passwords, secrets, session cookies, signing keys, connection strings that
    embed a password. Nothing else.
    Use plain "input" for everything non-secret: base URLs, model ids,
    organisation/project ids, user names, regions, file names.
    Masking is decided ONLY by this type — the app never guesses from the
    variable name, so a field named API_BASE stays readable and a field named
    TOKEN is only hidden if you say "secure_input".
    A masked field is still stored in plain text in the app's settings file;
    the mask is shoulder-surfing protection, not encryption. Never put a
    secret in an "info_box" or a tooltip — those are always visible.

opts (all optional): "tooltip" (hover help on the row), "placeholder"
(faint hint text inside an empty box), "item_tooltips" (per-option hover
help for a list_dropdown, in the same order as the options).
list_dropdown is a REAL dropdown; its last entry is always "Custom…", which
reveals a free-text box so users can enter values beyond your presets.

NOTES
-----
- This file is reimported fresh on every request. Live edits take effect
  immediately; saved Display values are re-applied after each import.
- Do NOT hardcode request timeouts — the app's "AI Response" timeout setting
  governs how long a response may take (0 = unlimited).
- Keep module top-level import-light: no network calls at import time (the
  Settings window imports this file just to read Display).
- The OLD contract (chat(sys, msgs)->str, chat_image, chat_tools) still works
  unchanged — existing custom scripts need no edits. This template documents
  the current, preferred contract.

════════════════════════════════════════════════════════════════════════════════
NOT FAMILIAR WITH PYTHON? LET AN AI WRITE THIS FOR YOU
════════════════════════════════════════════════════════════════════════════════

You do NOT need to understand any of the above. Open any AI assistant
(ChatGPT, Claude, Gemini, Copilot, ...) and paste ONE of the two prompts
below. Save whatever it writes as a .py file in this folder, then select it in
Settings ▸ AI ▸ Active Provider Script.

    PROMPT A ("grill me")   — the AI interviews you first, one question at a
                              time, then writes the script. Use this if you
                              are unsure what your provider supports or what
                              you want. RECOMMENDED.
    PROMPT B ("just build") — you already know your endpoint, key and model;
                              fill in the blanks and the AI writes it in one
                              shot.

Both prompts contain the FULL contract, so the AI has everything it needs
without seeing this file.

────────────────────────────────────────────────────────────────────────────────
PROMPT A — "GRILL ME, THEN BUILD IT"   (copy everything between the lines)
────────────────────────────────────────────────────────────────────────────────
You are going to write a Python "provider script" for my desktop AI assistant
app (Systema Auxilium). Do NOT write any code yet.

FIRST, interview me. Ask me ONE question at a time, wait for my answer, and
adapt your next question to what I said. Keep questions short and in plain
language — assume I am not a programmer. Explain any jargon you use in half a
sentence. If I say "I don't know", offer a sensible default and move on. If
something I say is technically impossible or a bad idea, say so plainly and
propose the better option.

Cover at least these areas, in roughly this order:

1. WHICH SERVICE: Which AI provider/API am I connecting to (OpenAI, Anthropic,
   Google Gemini, Mistral, Groq, OpenRouter, Together, DeepSeek, Ollama, LM
   Studio, vLLM, a company-internal endpoint, something else)? Is it a cloud
   API or something running on my own machine?
2. ENDPOINT + AUTH: What is the exact base URL or full endpoint? How does
   auth work (Bearer token, x-api-key header, query param, none)? Do I want my
   real key written into the file, or a placeholder I fill in later? Should the
   key be editable from the app's settings screen (recommended)?
3. MODEL: Which model id(s)? Do I want ONE fixed model, or a dropdown of
   several I can switch between in settings? If a dropdown — which ids, and a
   one-line description of each so you can write tooltips.
4. DIALECT: Is the API OpenAI-compatible (a /chat/completions endpoint taking
   {"model", "messages"}), Anthropic-style, Gemini-style, or custom? If custom,
   ask me for one example request and one example response (I can paste the
   provider's curl example or docs snippet). Do not guess a response shape —
   ask.
5. CAPABILITIES — ask about each, and tell me what each one buys me:
     - Streaming (reply appears word-by-word instead of all at once)
     - Reasoning / "thinking" tokens (shown in a collapsible card in the app)
     - Vision / image attachments
     - Native function calling / tools
   For each: does my provider support it, and do I want it on? If I am not
   sure whether my provider supports something, tell me how to check (e.g.
   "look for `stream: true` in their docs").
6. TUNING KNOBS: temperature, top_p, max_tokens, context size, system-prompt
   handling, any provider-specific extras (reasoning_effort, safety settings,
   ...). Which of these do I want to change from the app's settings screen
   later, and which should stay fixed in the file?
7. RELIABILITY: Should the script rotate between multiple API keys/accounts
   when one hits a rate limit or quota? Should it print helpful diagnostics
   when a request fails? Anything special about my network (proxy, self-signed
   certificate)?
8. ANYTHING ELSE: Is there anything unusual about this provider I have not
   mentioned?

THEN, before writing code, show me a SHORT summary of what you are about to
build (models, capabilities, which settings will be editable in the app) and
ask me to confirm or correct it.

ONLY AFTER I confirm, write the complete script, following the CONTRACT
SPECIFICATION below EXACTLY. Output the whole file in one code block, ready to
save. After the code, tell me in 3 lines: what to name the file, where to put
it, and how to test it.

<<<PASTE THE "CONTRACT SPECIFICATION" SECTION BELOW HERE>>>
────────────────────────────────────────────────────────────────────────────────

────────────────────────────────────────────────────────────────────────────────
PROMPT B — "JUST BUILD IT"   (copy everything between the lines)
────────────────────────────────────────────────────────────────────────────────
Write a complete Python "provider script" for my desktop AI assistant app
(Systema Auxilium), following the CONTRACT SPECIFICATION below EXACTLY.

My details:
- Provider name:      [e.g. OpenAI / Mistral / Groq / Ollama / ...]
- API base URL:       [e.g. https://api.openai.com/v1/chat/completions]
- Auth:               [e.g. Bearer token in the Authorization header]
- API key:            [paste your key, or write "leave a placeholder"]
- Model(s):           [one id, or several if you want a dropdown]
- API dialect:        [OpenAI-compatible / Anthropic / Gemini / custom — if
                       custom, paste an example request and response]
- Streaming:          [yes / no / don't know]
- Vision (images):    [yes / no / don't know]
- Native tool calling:[yes / no / don't know]
- Reasoning tokens:   [yes / no / don't know]
- Anything special:   [rate limits, proxy, extra parameters, or "nothing"]

If any of my answers is missing, contradictory, or looks wrong for that
provider, ask me about it BEFORE writing the code. Otherwise output the whole
file in one code block, ready to save.

<<<PASTE THE "CONTRACT SPECIFICATION" SECTION BELOW HERE>>>
────────────────────────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════════════════════
CONTRACT SPECIFICATION  (paste this into whichever prompt you chose)
════════════════════════════════════════════════════════════════════════════════

The script is a single self-contained .py file. The app imports it fresh on
every request, so it must be safe to import repeatedly and must do NO network
calls at import time.

REQUIRED — a version marker and exactly one entry point:

    CONTRACT_VERSION = 2

    def chat(system_prompt: str, messages: list, *,
             images=None, tools=None, stream=False):
        ...

Arguments:
  system_prompt : str  — the system prompt. May be "" but is never None.
                         Send it however your API expects (a "system" message,
                         a top-level "system" field, etc.).
  messages      : list — [{"role": "user"|"assistant", "content": str}, ...],
                         chronological, the newest user message last. In a
                         multi-turn tool conversation these may ALREADY be in
                         your provider's native shape (real tool-call and
                         tool-result turns) — in that case forward them
                         verbatim; do not reshape or re-wrap them.

                         A message may ALSO carry its own images:
                             {"role": "user", "content": "look at this",
                              "images": [{"path": str, "n": int, "name": str}]}
                         but ONLY if you declare SUPPORTS_INLINE_IMAGES = True
                         (see INLINE IMAGES below). If you do not declare it,
                         the app strips them out before you are called and you
                         will never see an "images" key here.
  images        : None or list[str] — absolute paths to image files (the
                         one-shot queue: pictures the AI asked to analyse for a
                         single turn). Attach these to the FINAL user turn. If
                         the provider has no vision, ignore this argument
                         entirely and set SUPPORTS_VISION = False.
  tools         : None or list — canonical tool definitions, each
                         {"name": str, "description": str, "parameters": {...}}
                         where parameters is a JSON Schema object. Only passed
                         when native tool calling is enabled AND the script
                         sets SUPPORTS_NATIVE_TOOLS = True. Ignore otherwise.
  stream        : bool — when True you MAY return a generator (see STREAMING).
                         Returning the normal dict anyway is always valid.

RETURN VALUE (when not streaming) — a dict:

    {
      "content":       str,           # the assistant's reply text (required)
      "thinking":      str | None,    # reasoning/chain-of-thought, if the API
                                      # exposes it separately. The app shows it
                                      # in a collapsible card and NEVER sends it
                                      # back to the model.
      "tool_calls":    [              # [] when not doing tool calling
          {"id": str, "name": str, "arguments": dict}, ...
      ],
      "finish_reason": str | None,    # e.g. "stop", "length", "tool_calls"
    }

  Returning a plain string is also accepted (treated as {"content": ...}).
  Empty content with no tool_calls is treated as an error by the app.
  Raising an exception is fine — the app catches it and shows the message.

STREAMING (only if the provider supports it):
  When stream=True, return a GENERATOR that yields dicts as data arrives:

      {"type": "text",      "content": "<text delta>",   "finish_reason": None}
      {"type": "thinking",  "content": "<reasoning delta>", "finish_reason": None}
      {"type": "tool_call", "content": {"id": ..., "name": ..., "arguments": {...}},
                            "finish_reason": None}
      {"type": "done",      "content": "",  "finish_reason": "stop"}

  Rules:
   - Yield "text" deltas as they arrive; the app renders them live.
   - If the API streams tool-call FRAGMENTS (e.g. OpenAI's arguments arriving
     character by character), accumulate them inside the script and yield each
     COMPLETE call as a single "tool_call" chunk, normally at end of stream.
     Never yield a half-built call.
   - Some models emit reasoning inline as <think>...</think> inside the normal
     content. If so, split it out so the tags never reach the user.
   - Finish with a "done" chunk (a generator that simply ends is tolerated).
   - Do the streaming work lazily inside the generator, EXCEPT the initial
     request — opening the connection in the function body is fine and lets the
     app's response-timeout bound the wait for the first token.

SETTINGS FORM (strongly recommended) — the Display dict:
  Declare which module-level variables the user may edit inside the app. The
  app renders a form, saves the values per script, and applies them to the
  module before every request, so the user never edits the file again.

      Display = {
          "API_KEY": ("API Key", "secure_input",
                      {"tooltip": "Where to get one: ...",
                       "placeholder": "sk-..."}),
          "BASE_URL": ("Base URL", "input"),
          "MODEL":   ("Model", "list_dropdown", ["model-a", "model-b"],
                      {"tooltip": "Any id from the provider's docs",
                       "item_tooltips": ["fast + cheap", "best quality"]}),
          "TEMPERATURE": ("Temperature", "number"),
          "NOTE_1": ("NOTE: free models rotate; check the provider's docs.",
                     "info_box"),
      }

  - Keys are the VARIABLE NAMES exactly as they appear in the file.
  - Spec forms: ("Label", "type") | ("Label", "list_dropdown", [options]) |
    ("Label", "type", {opts}) | ("Label", "list_dropdown", [options], {opts}).
  - Types:
      "input"         plain one-line text, shown as typed
      "secure_input"  one-line text MASKED as dots, with an eye reveal button
      "textarea"      multi-line text
      "list_dropdown" pick a preset, or "Custom…" to type any value
      "checkbox"      True / False
      "number"        numeric entry (int or float)
      "file_path"     text box + Browse… file picker
      "info_box"      read-only note; its LABEL is the note text and its key
                      is just a unique id like NOTE_1 — nothing is stored
  - RULE — "input" vs "secure_input" (get this right):
      Use "secure_input" for EVERY credential: API keys, auth tokens,
      passwords, secrets, session cookies, signing keys, and connection
      strings that embed a password.
      Use "input" for everything that is not secret: base URLs, model ids,
      organisation/project ids, user names, regions, file names.
      Masking is driven ONLY by the type — never by the variable's name — so
      classify each field deliberately. If you are unsure whether a value is
      sensitive, ask me rather than guessing.
      A masked value is still saved in plain text in the app's settings file;
      the mask only stops shoulder-surfing. NEVER put a secret in an
      "info_box" or a tooltip — those are always visible on screen.
  - opts keys (all optional): "tooltip" (hover help), "placeholder" (faint
    hint text in an empty box), "item_tooltips" (per-option hover help for a
    dropdown, in the same order as the options).
  - list_dropdown always gets a trailing "Custom…" entry that reveals a
    free-text box, so users are never locked into your presets.
  - Expose ONLY things a user would reasonably change: API key(s), model,
    real tuning knobs. Do NOT expose internal constants, cache paths, or
    plumbing.
  - Because Display values are applied AFTER import, do not capture them at
    import time. Build API clients inside a small helper function called per
    request (e.g. `def _client(): return OpenAI(api_key=API_KEY)`), not once
    at module level.

NATIVE TOOL CALLING (only if the provider genuinely supports it):

      SUPPORTS_NATIVE_TOOLS = True
      NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"

  Then handle the `tools` argument inside chat(): convert the canonical tool
  defs to the provider's format, send them, and return any calls the model
  makes in "tool_calls". The app ships helpers that do both conversions:

      from systema.engine import native_adapters as na
      payload["tools"] = na.to_openai_tools(tools)     # or to_anthropic_tools
                                                       # / to_gemini_tools
      parsed = na.parse_openai(response_json)          # or parse_anthropic
                                                       # / parse_gemini
      # parsed == {"text": str|None, "tool_calls": [{"id","name","arguments"}]}

  For OpenAI-dialect providers there is also a ready-made streaming helper
  that handles text deltas, inline <think> splitting and tool-call fragment
  assembly:

      from systema.engine.provider_contract import stream_openai_chunks
      return stream_openai_chunks(sdk_stream_or_parsed_sse_events)

  If unsure whether the endpoint really performs function calling, set
  SUPPORTS_NATIVE_TOOLS = False — the app then drives tools through its
  universal prompt-based mode and nothing breaks.

VISION AND INLINE IMAGES:

      SUPPORTS_VISION        = True    # False if the model cannot see images
      SUPPORTS_INLINE_IMAGES = True    # messages may carry their own images
      IMAGE_FORMATS          = ("png", "jpg", "jpeg", "gif", "webp")
      MAX_IMAGES             = 8       # optional cap; omit for unlimited

  SUPPORTS_VISION is read BEFORE an attachment is made, so declaring it
  honestly is what lets the app warn the user instead of sending pictures to a
  model that cannot look at them. Never claim vision you do not have.

  SUPPORTS_INLINE_IMAGES is the interesting one. Without it, every image in a
  conversation gets stapled onto the NEWEST user turn on every request — so
  the model cannot tell which picture came from which message, and an image
  the user sent ten turns ago looks like it was just sent. With it, each image
  stays on the message that owns it. Render them in one line:

      from systema.engine import native_adapters as na
      messages = na.render_inline_images(messages, _encode_image, "openai")

  where `_encode_image(path)` is YOUR OWN encoder returning one content block
  in your dialect (resize and payload limits differ per backend, so that part
  stays with you). The helper places an "[Image N]" label before each picture
  so the model can be told "look at image 3" and know which one that is.

  Handle BOTH channels: inline images (history, positional) and the flat
  `images=` argument (the one-shot analysis queue, final turn). If you declare
  nothing, the app degrades inline images into the flat list with text markers
  and your script keeps working exactly as before.

HARD RULES:
  - Do NOT hardcode a request timeout. The app has its own response-timeout
    setting (0 = unlimited) and a hardcoded one fights it.
  - Do NOT do network calls, logins or heavy work at import time (the settings
    screen imports the file just to read Display).
  - Do NOT print secrets. Placeholders over real keys unless I said otherwise.
  - Keep it ONE self-contained file. Prefer the `requests` library, or the
    provider's official SDK if that is clearly simpler.
  - Use plain, readable code with short comments explaining each block — a
    non-programmer will be reading this file.
  - Include a `if __name__ == "__main__":` block that sends one test message
    and prints the reply, so the file can be run directly to verify the key
    and connection before use.
  - Handle errors usefully: raise with a clear message, or return a dict whose
    "content" explains what went wrong.

BACKWARD COMPATIBILITY (context only — do not use for new scripts):
  Older scripts defined `chat(system_prompt, messages) -> str` plus optional
  `chat_image(system_prompt, messages, image_paths)` and
  `chat_tools(system_prompt, messages, tools, images=None)`. The app still
  accepts those, but new scripts must use the single chat() above.

════════════════════════════════════════════════════════════════════════════════
"""

import json

import requests


# ── Configure your provider here (editable from Settings via Display) ────────

CONTRACT_VERSION = 2

# Vision. Set SUPPORTS_VISION = False if your model cannot see images — the app
# reads it to warn the user BEFORE an attachment is made rather than sending
# pictures nothing will look at. SUPPORTS_INLINE_IMAGES says each image stays
# on the message that owns it (see chat() below); without it the app folds
# them all onto the newest user turn with text markers instead.
SUPPORTS_VISION        = True
SUPPORTS_INLINE_IMAGES = True
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "gif", "webp")

API_URL = "https://api.example.com/v1/chat/completions"
API_KEY = "your-api-key-here"
MODEL   = "your-model-name"

Display = {
    "API_URL": ("API URL", "input",
                {"placeholder": "https://api.example.com/v1/chat/completions"}),
    "API_KEY": ("API Key", "secure_input",
                {"tooltip": "Your provider's API key — stored per provider, "
                            "applied on every request", "placeholder": "sk-..."}),
    "MODEL":   ("Model", "input", {"placeholder": "your-model-name"}),
}


# ─────────────────────────────────────────────────────────────────────────────

def _headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _payload(system_prompt, messages, stream=False):
    return {
        "model": MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        **({"stream": True} if stream else {}),
    }


def _encode_image(image_path):
    """One image file -> one OpenAI-style image_url content block.

    This is the encoder the app's inline-image renderer calls. Keep it as ONE
    image in, ONE block out — resizing and compression policy belongs here,
    because payload limits differ wildly between backends.
    """
    import base64
    import mimetypes

    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
    }


def _vision_messages(messages, image_paths):
    """Rewrite the last user message into OpenAI-style image_url blocks.

    This is the EPHEMERAL channel — the one-shot analysis queue, which by
    design belongs to the current turn. Images that belong to the conversation
    are handled by render_inline_images() in chat() instead.
    """
    last_user_text = messages[-1]["content"] if messages else ""
    prior_messages = messages[:-1] if len(messages) > 1 else []

    content_blocks = [_encode_image(p) for p in image_paths]
    # The last turn's content is already a BLOCK LIST when it also carried
    # inline images — splice rather than nest, or those blocks are lost.
    if isinstance(last_user_text, list):
        content_blocks.extend(last_user_text)
    else:
        content_blocks.append({"type": "text", "text": last_user_text})
    return prior_messages + [{"role": "user", "content": content_blocks}]


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """Unified entry point — text, optional images, optional streaming.

    This sample speaks the OpenAI-compatible chat/completions dialect. `tools`
    is ignored here (SUPPORTS_NATIVE_TOOLS is not set) — see the native
    section at the bottom for how to opt in.

    Both image channels are handled: inline images stay on the message that
    owns them, the one-shot `images` queue rides the final turn.
    """
    from systema.engine import native_adapters as na
    msgs = na.render_inline_images(messages, _encode_image, "openai")
    if images:
        msgs = _vision_messages(msgs, images)

    if stream:
        return _chat_stream(system_prompt, msgs)

    response = requests.post(API_URL, json=_payload(system_prompt, msgs),
                             headers=_headers())
    response.raise_for_status()
    data = response.json()
    choice = data["choices"][0]
    return {
        "content": choice["message"]["content"] or "",
        "thinking": choice["message"].get("reasoning_content"),
        "tool_calls": [],
        "finish_reason": choice.get("finish_reason"),
    }


def _chat_stream(system_prompt, messages):
    """Generator over OpenAI-style SSE lines → contract chunks."""
    response = requests.post(API_URL, json=_payload(system_prompt, messages, stream=True),
                             headers=_headers(), stream=True)
    response.raise_for_status()
    finish = None
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            event = json.loads(payload)
            choice = event["choices"][0]
        except Exception:
            continue
        delta = choice.get("delta") or {}
        finish = choice.get("finish_reason") or finish
        if delta.get("reasoning_content"):
            yield {"type": "thinking", "content": delta["reasoning_content"],
                   "finish_reason": None}
        if delta.get("content"):
            yield {"type": "text", "content": delta["content"], "finish_reason": None}
    yield {"type": "done", "content": "", "finish_reason": finish or "stop"}


# ═════════════════════════════════════════════════════════════════════════════
# OPTIONAL: Native tool calling
#
# By default Systema Auxilium drives tools via its universal "compat" format
# (fenced tool calls described in the system prompt). If your provider/model
# genuinely supports native function calling, opt in so tools travel through
# the provider's tools API instead — lighter system prompt, more reliable
# invocation:
#
#     SUPPORTS_NATIVE_TOOLS = True
#     NATIVE_DIALECT        = "openai"   # "openai" | "anthropic" | "gemini"
#
# Then handle the `tools` parameter inside chat(): convert the canonical tool
# defs to your dialect, and return the parsed calls in "tool_calls". The
# helper module systema.engine.native_adapters does both for all 3 dialects:
#
#     from systema.engine import native_adapters as na
#     payload["tools"] = na.to_openai_tools(tools)      # request side
#     parsed = na.parse_openai(resp)                    # response side
#     return {"content": parsed["text"] or "", "thinking": None,
#             "tool_calls": parsed["tool_calls"], "finish_reason": None}
#
# In a multi-turn tool conversation `messages` arrives ALREADY in your
# dialect's shape (real tool_use / tool_result turns) — forward it verbatim
# after prepending the system message; do not reshape it.
#
# ⚠ Only enable this for endpoints that REALLY perform function calling. Many
# accept the `tools` param but ignore it — in that case leave
# SUPPORTS_NATIVE_TOOLS = False and the universal compat path is used.
# ═════════════════════════════════════════════════════════════════════════════
