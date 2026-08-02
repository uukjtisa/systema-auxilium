"""
provider_mixed_opencode.cloudflare.py
==========================================
Custom Script Provider for Systema Auxilium.
MIXED provider — best of both worlds:

    TEXT / REASONING  ->  OpenCode Zen (deepseek-v4-flash-free, or any model
                           from the OpenCode Zen lineup). Fast, free, great
                           general reasoning — but NO vision support.

    VISION            ->  Kimi K2.7-code via Cloudflare Workers AI (multi
                           -account sequential failover + disk-persisted
                           exhaustion cache). Kimi natively supports images,
                           deepseek does not, so any request with an attached
                           image is routed here instead.

Everything else (tool calling, error handling, account rotation) is carried
over unchanged from the two source providers this file merges:
    - provider_opencode_zen.py   (text engine)
    - provider_cloudflare.py     (vision engine)

Contract v2: unified chat() (text + vision + native tools + streaming);
BOTH engine models are editable in Settings via Display (text model =
OpenCode Zen id, vision model = Cloudflare @cf/... id). Reasoning is
returned separately as "thinking" — the app renders it in a collapsible card.

Setup:
    Nothing to edit for normal use. Open
        Settings -> AI -> Provider Settings
    and paste your OpenCode API key, plus a Cloudflare Account ID and API
    token (dash.cloudflare.com, Workers AI) for the vision side.

    Own several Cloudflare accounts? Change CF_ACCOUNTS below from 1 to how
    many you have, save this file, then reopen Settings: that many ID/token
    pairs appear in the form.

Point this file at:
    Settings -> AI -> Custom Script Provider
"""

import re
import os
import json
import time
import base64
import mimetypes
import datetime
from openai import OpenAI, RateLimitError


# ═════════════════════════════════════════════════════════════════════════════
# TEXT ENGINE — OpenCode Zen (deepseek-v4-flash-free)
# ═════════════════════════════════════════════════════════════════════════════

OPENCODE_API_KEY = ""   # Fill this in under Settings ▸ AI ▸ Provider Settings
OPENCODE_MODEL   = "deepseek-v4-flash-free"   # Change to any model from OpenCode Zen's lineup
OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"

OPENCODE_TEMPERATURE = 1
OPENCODE_TOP_P       = 0.95
OPENCODE_MAX_TOKENS  = 16384


def _opencode_client() -> OpenAI:
    # Built per call so Display overrides (applied after import) take effect.
    return OpenAI(base_url=OPENCODE_BASE_URL, api_key=OPENCODE_API_KEY)


# ═════════════════════════════════════════════════════════════════════════════
# VISION ENGINE — Kimi K2.7-code via Cloudflare Workers AI
# ═════════════════════════════════════════════════════════════════════════════

# HOW MANY CLOUDFLARE ACCOUNTS TO USE (vision side only).
#
# One is all you need, and one is the default. You fill it in from
# Settings ▸ AI ▸ Provider Settings — no need to touch this file at all.
#
# If you happen to own several Cloudflare accounts, change this number to how
# many you have, save the file, and reopen Settings: that many "Account N ID"
# + "Account N token" pairs appear in the form. They are tried in order, and
# an account that hits its daily free neuron limit is skipped automatically
# for 24 hours — so extra accounts simply stretch the free tier further.
#
# Turning the number back down never loses anything: the app keeps values it
# already saved, so raising it again brings those pairs back exactly as they
# were.
CF_ACCOUNTS = 1

# The variables the Settings form fills in — CF_ACCOUNT_1_ID,
# CF_ACCOUNT_1_TOKEN, CF_ACCOUNT_2_ID, ... — created from the number above so
# that number stays the only thing you ever edit. They start empty on purpose:
# the app applies your saved values to this module before every request.
for _slot in range(1, int(CF_ACCOUNTS) + 1):
    globals()[f"CF_ACCOUNT_{_slot}_ID"] = ""
    globals()[f"CF_ACCOUNT_{_slot}_TOKEN"] = ""


def _cf_accounts() -> list:
    """The slots you actually filled in, in order, as dicts.

    Read fresh on every call, never at import: the app applies saved Settings
    values to this module AFTER importing it, so reading them any earlier
    would only ever see the empty strings above.
    """
    found = []
    for i in range(1, int(CF_ACCOUNTS) + 1):
        account_id = str(globals().get(f"CF_ACCOUNT_{i}_ID") or "").strip()
        api_token  = str(globals().get(f"CF_ACCOUNT_{i}_TOKEN") or "").strip()
        if account_id and api_token:   # a half-filled pair is skipped, not sent
            found.append({
                "account_id": account_id,
                "api_token":  api_token,
                "label":      f"Account {i}",   # only ever appears in the log
            })
    return found


def _cf_account_rows() -> dict:
    """Sister of the loop above: one Settings row per slot variable it made."""
    rows = {}
    for i in range(1, int(CF_ACCOUNTS) + 1):
        rows[f"CF_ACCOUNT_{i}_ID"] = (
            f"Account {i} ID", "input",
            {"tooltip": "dash.cloudflare.com ▸ Workers AI — the hex string in "
                        "the dashboard URL",
             "placeholder": "32-character hex account id"})
        rows[f"CF_ACCOUNT_{i}_TOKEN"] = (
            f"Account {i} token", "secure_input",
            {"tooltip": "An API token with Workers AI access, from the same "
                        "dashboard",
             "placeholder": "cfut_..."})
    return rows


# Was @cf/moonshotai/kimi-k2.7-code until 2026-08-02, when Cloudflare moved the
# Kimi models off the free allocation onto the Workers PAID plan — which broke
# this script's entire premise, since the Cloudflare half exists precisely to
# get FREE vision. Llama 4 Scout is the replacement: free allocation, natively
# multimodal, and it keeps tool calling.
CF_MODEL          = "@cf/meta/llama-4-scout-17b-16e-instruct"
CF_MAX_TOKENS     = 16384


# Model lists researched 2026-07-21 (both dropdowns are EDITABLE — type any id):
# free OpenCode Zen lineup from https://opencode.ai/docs/zen/ (rotates);
# vision-capable Cloudflare-hosted models from
# https://developers.cloudflare.com/workers-ai/models/.
Display = {
    "OPENCODE_API_KEY": ("OpenCode API Key", "secure_input",
                         {"tooltip": "Get one at opencode.ai/zen",
                          "placeholder": "sk-..."}),
    "OPENCODE_MODEL": ("Text model (OpenCode)", "list_dropdown", [
        ("DeepSeek V4 Flash (free)",  "deepseek-v4-flash-free"),
        ("Big Pickle (free)",         "big-pickle"),
        ("Laguna S 2.1 (free)",       "laguna-s-2.1-free"),
        ("Ling 3.0 Flash (free)",     "ling-3.0-flash-free"),
        ("North Mini Code (free)",    "north-mini-code-free"),
        ("Nemotron 3 Ultra (free)",   "nemotron-3-ultra-free")],
        {"tooltip": "Handles every text-only turn; image turns go to the "
                    "Cloudflare engine below. Editable: paid ids from "
                    "opencode.ai/docs/zen work too. NOTE: mimo-v2.5-free is "
                    "deliberately absent — it can see on its own, so if you want "
                    "it, use provider_opencode_zen.py and skip this split script.",
         "item_tooltips": [
             "deepseek-v4-flash-free — fast, good reasoning. Default.",
             "big-pickle — stealth frontier model, free for a limited time",
             "laguna-s-2.1-free — Poolside agentic-coding MoE (new since 2026-07-21)",
             "ling-3.0-flash-free — Ant Group MoE, 262K context (new since 2026-07-21)",
             "north-mini-code-free — Cohere, code-focused",
             "nemotron-3-ultra-free — NVIDIA 550B planning/reasoning"]}),
    "CF_MODEL": ("Vision model (Cloudflare)", "list_dropdown", [
        ("Llama 4 Scout (Vision)",     "@cf/meta/llama-4-scout-17b-16e-instruct"),
        ("Gemma 4 26B (Vision)",       "@cf/google/gemma-4-26b-a4b-it"),
        ("Mistral Small 3.1 (Vision)", "@cf/mistralai/mistral-small-3.1-24b-instruct"),
        ("Llama 3.2 11B (Vision)",     "@cf/meta/llama-3.2-11b-vision-instruct")],
        {"tooltip": "Handles any turn with attached images — every entry here is "
                    "vision-capable and on Cloudflare's FREE allocation. Kimi was "
                    "removed 2026-08-02: it now needs the Workers PAID plan. "
                    "Editable — type a Kimi id if you are on the paid plan.",
         "item_tooltips": [
             "@cf/meta/llama-4-scout-17b-16e-instruct — natively multimodal MoE; vision + tools. Default.",
             "@cf/google/gemma-4-26b-a4b-it — vision + tools + reasoning",
             "@cf/mistralai/mistral-small-3.1-24b-instruct — vision + tools, 128k context",
             "@cf/meta/llama-3.2-11b-vision-instruct — vision only, no tool calling"]}),
    # One ID + token pair per slot, generated from CF_ACCOUNTS at the top.
    # Only the vision half of this provider uses them.
    **_cf_account_rows(),
    "NOTE_1": ("NOTE: text goes to OpenCode Zen, images to Cloudflare.",
               "info_box"),
    "NOTE_2": ("NOTE: both Cloudflare values come from dash.cloudflare.com ▸ "
               "Workers AI — the Account ID is the hex string in the "
               "dashboard URL, and the token is an API token with Workers AI "
               "access. Free tier, no credit card. Text-only chats never "
               "touch them.", "info_box"),
    "NOTE_3": ("NOTE: got more than one Cloudflare account? Open this script "
               "(providers/large-language-models/"
               "provider_mixed_opencode.cloudflare.py), change CF_ACCOUNTS "
               "near the top from 1 to how many you have, then reopen this "
               "window — that many ID/token pairs appear right here. They are "
               "tried in order and an account that hits its daily free neuron "
               "limit is skipped automatically for 24h.", "info_box"),
}

# Cache file lives next to this script so it persists across reloads/restarts.
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".exhaustion_cache.json"
)


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"[provider] Warning: could not write exhaustion cache: {e}")


def _reset_after_24h() -> float:
    return time.time() + 86400


def _hours_left(reset_ts: float) -> str:
    secs = max(0, reset_ts - time.time())
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _mark_exhausted(account_id: str, label: str) -> None:
    cache = _load_cache()
    reset_ts = _reset_after_24h()
    cache[account_id] = reset_ts
    _save_cache(cache)
    reset_str = datetime.datetime.fromtimestamp(
        reset_ts, tz=datetime.timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    hrs = _hours_left(reset_ts)
    print(f"[provider] x {label} exhausted — resets at {reset_str} ({hrs} from now)")


def _is_account_available(account: dict) -> bool:
    aid = account["account_id"]
    cache = _load_cache()
    reset_ts = cache.get(aid)
    if reset_ts is None:
        return True
    if time.time() >= reset_ts:
        del cache[aid]
        _save_cache(cache)
        print(f"[provider] {account.get('label', aid)} unblocked after 24h cooldown.")
        return True
    return False


def _make_cf_client(account: dict) -> OpenAI:
    return OpenAI(
        base_url=(
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{account['account_id']}/ai/v1"
        ),
        api_key=account["api_token"],
    )


def _split_reasoning(message) -> tuple:
    """(content, thinking) from a response message — structured reasoning
    field first, legacy inline <think> blocks split out as fallback."""
    content = (getattr(message, "content", None) or "").strip()
    reasoning = (getattr(message, "reasoning", None)
                 or getattr(message, "reasoning_content", None) or "")
    m = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
    if m:
        if not reasoning:
            reasoning = m.group(1)
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content, (reasoning.strip() or None)


def _is_neuron_limit(error: RateLimitError) -> bool:
    body = str(error)
    return (
        "4006" in body
        or "neurons" in body.lower()
        or "daily free allocation" in body.lower()
    )


def _encode_image(image_path: str) -> dict:
    """
    Encode a single image file as a base64 data-URI image_url content block.
    Resizes/compresses large images to stay under Cloudflare's payload limit.
    """
    from PIL import Image
    import io as _io

    MAX_DIMENSION = 1280
    MAX_SIZE_KB   = 2048

    original_kb = os.path.getsize(image_path) / 1024
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"

    with Image.open(image_path) as img:
        original_size = img.size

        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
            mime_type = "image/jpeg"

        w, h = img.size
        if max(w, h) > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(
                f"[provider] Image resized: {original_size[0]}x{original_size[1]}"
                f" → {new_w}x{new_h}  (original: {original_kb:.1f} KB)"
            )

        quality = 85
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)

        while buf.tell() > MAX_SIZE_KB * 1024 and quality > 40:
            quality -= 10
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)

        final_kb = buf.tell() / 1024
        image_b64 = base64.b64encode(buf.getvalue()).decode()
        mime_type = "image/jpeg"

        print(
            f"[provider] Image encoded: {final_kb:.1f} KB"
            f" (quality={quality}, b64={len(image_b64):,} chars)"
        )

    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_b64}",
        },
    }


def _build_vision_message(image_paths: list[str], user_text: str) -> dict:
    content_blocks = [_encode_image(p) for p in image_paths]
    # `user_text` is already a BLOCK LIST when this turn also carried inline
    # images — splice, don't nest, or those blocks are silently dropped.
    if isinstance(user_text, list):
        content_blocks.extend(user_text)
    else:
        content_blocks.append({"type": "text", "text": user_text})
    return {"role": "user", "content": content_blocks}


def _dump_verbose_error(
    error: Exception,
    full_messages: list,
    raw_system_prompt: str,
    raw_messages: list,
    image_paths: list[str] | None = None,
) -> None:
    SEP = "=" * 72
    print()
    print(SEP)
    print("[provider] !! ERROR — VERBOSE DUMP FOLLOWS")
    print(SEP)
    print()
    print("── ERROR ──────────────────────────────────────────────────────────")
    print(f"  Type : {type(error).__name__}")
    print(f"  Value: {error}")
    print()
    print("── RAW INPUTS (app → custom script) ───────────────────────────────")
    print(f"  system_prompt ({len(raw_system_prompt)} chars):")
    print(f"  messages ({len(raw_messages)} turns)")
    print()
    print("── ASSEMBLED MESSAGES SENT TO API ───────────────────────────────────")
    for i, msg in enumerate(full_messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        print(f"  [{i}] role={role!r}")
        if isinstance(content, str):
            preview = content[:500] + ("…" if len(content) > 500 else "")
            print(f"       content ({len(content)} chars): {preview}")
        elif isinstance(content, list):
            print(f"       content ({len(content)} block(s))")
            for j, block in enumerate(content):
                btype = block.get("type", "?")
                if btype == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    truncated = url[:60] + f"…[{len(url)} chars total]" if len(url) > 60 else url
                    print(f"         block[{j}] type=image_url  data={truncated}")
                elif btype == "text":
                    text = block.get("text", "")
                    preview = text[:300] + ("…" if len(text) > 300 else "")
                    print(f"         block[{j}] type=text  ({len(text)} chars): {preview}")
        print()
    if image_paths:
        print("── IMAGE LIST SUMMARY ──────────────────────────────────────────────")
        total_kb = 0.0
        for idx, p in enumerate(image_paths):
            mime, _ = mimetypes.guess_type(p)
            if os.path.isfile(p):
                kb = os.path.getsize(p) / 1024
                total_kb += kb
                print(f"  [{idx}] {os.path.basename(p)}  {kb:.1f} KB  type={mime or 'unknown'}")
            else:
                print(f"  [{idx}] {p}  << FILE NOT FOUND >>")
        print(f"  Total image data: {total_kb:.1f} KB across {len(image_paths)} file(s)")
        print()
    print(SEP)
    print("[provider] !! END OF VERBOSE DUMP")
    print(SEP)
    print()


def _call_cf_accounts(
    full_messages: list,
    raw_system_prompt: str = "",
    raw_messages: list = None,
    image_paths: list[str] | None = None,
    tools: list | None = None,
    stream: bool = False,
) -> object:
    """Core dispatcher for the Cloudflare vision engine — sequential
    multi-account failover with disk-persisted exhaustion cache. Returns the
    raw response object (or SDK stream when stream=True); an all-exhausted
    condition returns a human-readable STRING."""
    raw_messages = raw_messages or []
    last_error = None

    accounts = _cf_accounts()
    if not accounts:
        return (
            "This turn has images, which go to the Cloudflare vision engine — "
            "but no Cloudflare account is set up yet.\n\n"
            "Open Settings ▸ AI ▸ Provider Settings and fill in "
            "\"Account 1 ID\" and \"Account 1 token\". Both come from "
            "dash.cloudflare.com ▸ Workers AI: the account ID is the hex "
            "string in the dashboard URL, and the token is an API token with "
            "Workers AI access. The free tier needs no credit card.\n\n"
            "Text-only chats keep working without this — they go to OpenCode "
            "Zen instead."
        )

    for account in accounts:
        label = account.get("label", account["account_id"])

        if not _is_account_available(account):
            cache = _load_cache()
            hrs = _hours_left(cache.get(account["account_id"], 0))
            print(f"[provider] — Skipping {label} (exhausted, resets in {hrs})")
            continue

        print(f"[provider] Trying {label} ({account['account_id'][:8]}...)")

        try:
            client = _make_cf_client(account)
            kwargs = dict(
                model=CF_MODEL,
                messages=full_messages,
                max_tokens=CF_MAX_TOKENS,
                stream=stream,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            return client.chat.completions.create(**kwargs)

        except RateLimitError as e:
            if _is_neuron_limit(e):
                _mark_exhausted(account["account_id"], label)
                last_error = e
                continue
            _dump_verbose_error(e, full_messages, raw_system_prompt, raw_messages, image_paths)
            raise

        except Exception as e:
            _dump_verbose_error(e, full_messages, raw_system_prompt, raw_messages, image_paths)
            raise

    exhausted = ", ".join(a["label"] for a in accounts)
    return (
        f"All {len(accounts)} Cloudflare Workers AI account(s) have reached "
        f"their daily free neuron limit.\n\n"
        f"Exhausted accounts: {exhausted}\n\n"
        f"Last error: {last_error}\n\n"
        "Quota resets 24 hours after hitting the limit. You can add another "
        "free account — raise CF_ACCOUNTS at the top of the provider script "
        "and fill in the new pair in Settings — or upgrade to Workers Paid."
    )


# ═════════════════════════════════════════════════════════════════════════════
# NATIVE TOOL CALLING
# ═════════════════════════════════════════════════════════════════════════════
# Both engines speak the OpenAI function-calling dialect, so native tool
# calling works end to end regardless of which engine handles a given turn.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"

# ── Vision (contract v2.1) ────────────────────────────────────────────────────
# Vision is real here even though the TEXT engine has none — any turn carrying
# images is routed to the Cloudflare engine instead, which is the whole reason
# this mixed script exists.
# Vision depends on the CF_MODEL you picked, not on the text model: a text-only
# Cloudflare id leaves this script with no vision path at all, and saying
# otherwise fails inside the base64 encoder. (Catalog verified 2026-08-02.)
_CF_VISION_MODELS = frozenset({
    "@cf/meta/llama-4-scout-17b-16e-instruct",
    "@cf/meta/llama-3.2-11b-vision-instruct",
    "@cf/google/gemma-4-26b-a4b-it",
    "@cf/mistralai/mistral-small-3.1-24b-instruct",
    "@cf/moonshotai/kimi-k2.6",        # Workers PAID plan only
    "@cf/moonshotai/kimi-k2.7-code",   # Workers PAID plan only
})


def SUPPORTS_VISION() -> bool:
    """True when the selected Cloudflare vision engine can actually see."""
    return CF_MODEL in _CF_VISION_MODELS
SUPPORTS_INLINE_IMAGES = True
# Everything Pillow can DECODE, not just what the endpoint accepts: the
# encoder re-encodes to JPEG/PNG on the way out, so the input extension
# only has to be readable. A narrow list refused a .jfif for no reason.
IMAGE_FORMATS          = ("png", "jpg", "jpeg", "jfif", "jpe", "gif", "webp", "bmp",
                 "dib", "tif", "tiff", "ico", "tga", "ppm", "pgm", "pbm",
                 "avif", "heic", "heif")


def _has_inline_images(messages: list) -> bool:
    """True when any message carries its own images (contract v2.1).

    The engine choice below cannot look only at the flat `images=` kwarg any
    more: a conversation whose pictures are anchored to earlier turns arrives
    with that kwarg EMPTY, and routing such a turn to the text engine would
    silently drop every image in the history.
    """
    return any(isinstance(m, dict) and m.get("images") for m in (messages or []))


# ═════════════════════════════════════════════════════════════════════════════
# PUBLIC API — this is the contract the app calls into
# ═════════════════════════════════════════════════════════════════════════════

def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """
    Unified v2 entry point.

    - No images     -> OpenCode Zen text engine (OPENCODE_MODEL).
    - With image(s) -> Cloudflare vision engine (CF_MODEL), since the text
                       engine has no vision capability. Multi-account
                       failover + exhaustion cache apply. "With images" covers
                       BOTH the flat one-shot queue and images anchored to
                       earlier turns.

    Both engines stream (stream=True yields contract chunks) and both carry
    native tool calls when `tools` is provided.
    """
    from systema.engine import native_adapters as na
    from systema.engine.provider_contract import stream_openai_chunks

    oai_tools = na.to_openai_tools(tools) if tools else None
    inline = _has_inline_images(messages)

    if images or inline:
        # ── Vision engine (Cloudflare) ────────────────────────────────────
        if isinstance(images, str):
            images = [images]
        messages = na.render_inline_images(messages, _encode_image, "openai")

        if images:
            last_user_text = messages[-1]["content"] if messages else ""
            prior_messages = messages[:-1] if len(messages) > 1 else []
            tail = [_build_vision_message(images, last_user_text)]
        else:
            prior_messages, tail = messages, []
        full_messages = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + prior_messages + tail

        n = len(images or [])
        print(f"[provider] Vision request — {n} queued image(s)"
              f"{' + inline history images' if inline else ''}. "
              f"Routing to {CF_MODEL} (Cloudflare).")

        response = _call_cf_accounts(
            full_messages,
            raw_system_prompt=system_prompt,
            raw_messages=messages,
            image_paths=images,
            tools=oai_tools,
            stream=stream,
        )

        if isinstance(response, str):
            # All CF accounts exhausted — the notice becomes the reply.
            if stream:
                def _exhausted_stream(text=response):
                    yield {"type": "text", "content": text, "finish_reason": None}
                    yield {"type": "done", "content": "", "finish_reason": "stop"}
                return _exhausted_stream()
            return {"content": response, "thinking": None, "tool_calls": [],
                    "finish_reason": "stop"}

        if stream:
            return stream_openai_chunks(response)
        return _result_from_completion(response)

    # ── Text engine (OpenCode Zen) ────────────────────────────────────────
    payload = {
        "model": OPENCODE_MODEL,
        "messages": (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        ) + messages,
        "temperature": OPENCODE_TEMPERATURE,
        "top_p": OPENCODE_TOP_P,
        "max_tokens": OPENCODE_MAX_TOKENS,
    }
    if oai_tools:
        payload["tools"] = oai_tools
        payload["tool_choice"] = "auto"

    if stream:
        completion = _opencode_client().chat.completions.create(**payload, stream=True)
        return stream_openai_chunks(completion)
    completion = _opencode_client().chat.completions.create(**payload)
    return _result_from_completion(completion)


def _result_from_completion(completion) -> dict:
    """Non-stream SDK response → normalized v2 result dict."""
    from systema.engine import native_adapters as na
    parsed = na.parse_openai(completion.model_dump())
    msg = completion.choices[0].message if completion.choices else None
    content, thinking = _split_reasoning(msg) if msg else ("", None)
    return {
        "content": content,
        "thinking": thinking,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": completion.choices[0].finish_reason if completion.choices else None,
    }


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to sanity-check both engines before using in Systema.
#
#   TEXT TEST (OpenCode Zen):
#       python "provider_mixed_opencode.cloudflare.py"
#
#   VISION TEST (Kimi K2.7 / Cloudflare):
#       python "provider_mixed_opencode.cloudflare.py" --vision /path/to/image.jpg

if __name__ == "__main__":
    import sys

    # Running this file directly means the app is not here to apply your saved
    # Settings values, so the key and the account slots are empty. Fill them
    # from the environment instead — e.g. on Windows:
    #     set OPENCODE_API_KEY=sk-...
    #     set CF_ACCOUNT_1_ID=...
    #     set CF_ACCOUNT_1_TOKEN=...
    # Inside Systema Auxilium none of this applies; Settings supplies them.
    OPENCODE_API_KEY = OPENCODE_API_KEY or os.environ.get("OPENCODE_API_KEY", "")
    for _i in range(1, int(CF_ACCOUNTS) + 1):
        for _part in ("ID", "TOKEN"):
            if not globals().get(f"CF_ACCOUNT_{_i}_{_part}"):
                globals()[f"CF_ACCOUNT_{_i}_{_part}"] = os.environ.get(
                    f"CF_ACCOUNT_{_i}_{_part}", "")

    _configured = _cf_accounts()

    print("Testing MIXED provider — OpenCode Zen (text) + Cloudflare (vision)")
    print(f"Text model:   {OPENCODE_MODEL}  @ {OPENCODE_BASE_URL}")
    print(f"Vision model: {CF_MODEL}  "
          f"({len(_configured)} of {CF_ACCOUNTS} CF slot(s) filled)")
    print("-" * 60)

    if "--vision" in sys.argv:
        if not _configured:
            print("No Cloudflare account credentials found.")
            print("Set CF_ACCOUNT_1_ID and CF_ACCOUNT_1_TOKEN in your "
                  "environment to test vision standalone,")
            print("or fill them in under Settings ▸ AI ▸ Provider Settings "
                  "and test from inside the app.")
            sys.exit(1)

        idx = sys.argv.index("--vision")
        image_args = sys.argv[idx + 1:]

        if not image_args:
            print("Usage: python \"provider_mixed_opencode.cloudflare.py\" --vision /path/to/image.jpg [img2.png ...]")
            sys.exit(1)

        missing = [p for p in image_args if not os.path.isfile(p)]
        if missing:
            print(f"Error: file(s) not found: {missing}")
            sys.exit(1)

        n = len(image_args)
        prompt = "Describe this image in one sentence." if n == 1 else (
            f"I'm sending you {n} images. Please describe each one in a single sentence, numbered."
        )

        try:
            out = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
                images=image_args,
            )
            result = out["content"]
            print("Response:", result)
            print("-" * 60)
            if "neuron limit" in result:
                print("All Cloudflare accounts exhausted — see message above.")
            else:
                print(f"Vision test passed. {CF_MODEL} (Cloudflare) is working correctly.")
        except Exception as e:
            print(f"Vision test failed: {e}")

    else:
        if not OPENCODE_API_KEY:
            print("No OpenCode API key found.")
            print("Set OPENCODE_API_KEY in your environment to test text "
                  "standalone, or fill it in under")
            print("Settings ▸ AI ▸ Provider Settings and test from inside "
                  "the app.")
            sys.exit(1)

        try:
            out = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
            )
            result = out["content"]
            print("Response:", result)
            print("-" * 60)
            print("Text test passed. OpenCode Zen is working correctly.")
        except Exception as e:
            print(f"Text test failed: {e}")
