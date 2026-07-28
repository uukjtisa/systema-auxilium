"""
provider_cloudflare.py
=======================
Custom Script Provider for Systema Auxilium.
ONE provider for Cloudflare Workers AI — pick any Cloudflare-hosted model in
Settings (merged from the old per-model kimi scripts). FREE tier available.

Endpoint: https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1
Model catalog: https://developers.cloudflare.com/workers-ai/models/
(The Display dropdown lists the current text-generation catalog — it is
editable, so type any @cf/... id when Cloudflare ships new models.)

Contract v2: unified chat() (text + vision + native tools + streaming);
editable settings via Display. Reasoning is returned separately as
"thinking" — the app renders it in a collapsible card.

Free tier: ~2-5 million tokens/day (10,000 neurons/day). No credit card needed.
Vision: model-dependent (Kimi K2.6/K2.7, Llama-4-Scout, Gemma-4,
Mistral-Small-3.1, Llama-3.2-11b-vision) — multiple images per request via
base64 image_url content blocks.

Multi-account support (sequential + disk-persisted exhaustion cache):
    - Accounts that have hit the daily neuron limit are written to a JSON
      file on disk (.exhaustion_cache.json, next to this script) so the
      cache survives script reloads and process restarts.
    - Exhausted accounts are instantly skipped on every call — no wasted
      requests or neurons.
    - Cached accounts are automatically unblocked 24 hours after being marked
      exhausted (i.e. when Cloudflare's per-account rolling window resets).
    - Only ONE account is ever called per request.
    - When ALL accounts are exhausted, returns a human-readable error
      instead of crashing.

API notes:
    - Reasoning output: response.choices[0].message.reasoning (reasoning
      models); legacy inline <think> blocks are split out as a fallback.
    - Supported image formats: JPEG, PNG, GIF, WEBP
    - Multiple images per request: YES (pass a list via images=)

Setup:
    Nothing to edit for normal use. Open
        Settings -> AI -> Provider Settings
    and paste your Account ID and API token — both come from
    dash.cloudflare.com (Workers AI).

    Own several Cloudflare accounts? Change ACCOUNTS below from 1 to how many
    you have, save this file, then reopen Settings: that many ID/token pairs
    appear in the form.

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


# ── Configure here ────────────────────────────────────────────────────────────

# HOW MANY CLOUDFLARE ACCOUNTS TO USE.
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
ACCOUNTS = 1

# The variables the Settings form fills in — ACCOUNT_1_ID, ACCOUNT_1_TOKEN,
# ACCOUNT_2_ID, ACCOUNT_2_TOKEN, ... — created from the number above so that
# number stays the only thing you ever edit. They start empty on purpose: the
# app applies your saved values to this module before every request.
for _slot in range(1, int(ACCOUNTS) + 1):
    globals()[f"ACCOUNT_{_slot}_ID"] = ""
    globals()[f"ACCOUNT_{_slot}_TOKEN"] = ""


def _accounts() -> list:
    """The slots you actually filled in, in order, as dicts.

    Read fresh on every call, never at import: the app applies saved Settings
    values to this module AFTER importing it, so reading them any earlier
    would only ever see the empty strings above.
    """
    found = []
    for i in range(1, int(ACCOUNTS) + 1):
        account_id = str(globals().get(f"ACCOUNT_{i}_ID") or "").strip()
        api_token  = str(globals().get(f"ACCOUNT_{i}_TOKEN") or "").strip()
        if account_id and api_token:   # a half-filled pair is skipped, not sent
            found.append({
                "account_id": account_id,
                "api_token":  api_token,
                "label":      f"Account {i}",   # only ever appears in the log
            })
    return found


def _account_rows() -> dict:
    """Sister of the loop above: one Settings row per slot variable it made."""
    rows = {}
    for i in range(1, int(ACCOUNTS) + 1):
        rows[f"ACCOUNT_{i}_ID"] = (
            f"Account {i} ID", "input",
            {"tooltip": "dash.cloudflare.com ▸ Workers AI — the hex string in "
                        "the dashboard URL",
             "placeholder": "32-character hex account id"})
        rows[f"ACCOUNT_{i}_TOKEN"] = (
            f"Account {i} token", "secure_input",
            {"tooltip": "An API token with Workers AI access, from the same "
                        "dashboard",
             "placeholder": "cfut_..."})
    return rows

MODEL          = "@cf/moonshotai/kimi-k2.6"
MAX_TOKENS     = 16384   # Raise up to 16384 if needed — watch your neuron budget

CONTRACT_VERSION = 2

# Current Cloudflare-hosted text-generation catalog (researched 2026-07-21,
# https://developers.cloudflare.com/workers-ai/models/). Editable dropdown —
# type any @cf/... id for models added after this list was compiled.
Display = {
    "MODEL": ("Model", "list_dropdown", [
        "@cf/moonshotai/kimi-k2.6",
        "@cf/moonshotai/kimi-k2.7-code",
        "@cf/zai-org/glm-5.2",
        "@cf/zai-org/glm-4.7-flash",
        "@cf/openai/gpt-oss-120b",
        "@cf/openai/gpt-oss-20b",
        "@cf/nvidia/nemotron-3-120b-a12b",
        "@cf/meta/llama-4-scout-17b-16e-instruct",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "@cf/meta/llama-3.2-11b-vision-instruct",
        "@cf/google/gemma-4-26b-a4b-it",
        "@cf/mistralai/mistral-small-3.1-24b-instruct",
        "@cf/qwen/qwen3-30b-a3b-fp8",
        "@cf/qwen/qwen2.5-coder-32b-instruct",
        "@cf/qwen/qwq-32b",
        "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"],
        {"tooltip": "Editable — type any @cf/... id from the Workers AI catalog",
         "item_tooltips": [
             "Kimi K2.6 — 1T frontier model; vision + tools + reasoning",
             "Kimi K2.7 Code — agentic/code tuned; vision + tools",
             "GLM-5.2 — tools + reasoning",
             "GLM-4.7 Flash — fast multilingual; tools + reasoning",
             "gpt-oss-120b — OpenAI open-weight; tools + reasoning",
             "gpt-oss-20b — lighter gpt-oss; tools + reasoning",
             "Nemotron-3 120B — NVIDIA MoE; tools + reasoning",
             "Llama-4 Scout — multimodal MoE; vision + tools",
             "Llama-3.3 70B fast — tools",
             "Llama-3.2 11B Vision — vision",
             "Gemma-4 26B — vision + tools",
             "Mistral Small 3.1 — vision",
             "Qwen3 30B — tools + reasoning",
             "Qwen2.5 Coder 32B — code-focused",
             "QwQ 32B — reasoning-focused",
             "DeepSeek R1 distill 32B — reasoning"]}),
    "MAX_TOKENS": ("Max tokens", "number",
                   {"tooltip": "Response cap — higher burns the daily free "
                               "neuron budget faster"}),
    # One ID + token pair per slot, generated from ACCOUNTS at the top.
    **_account_rows(),
    "NOTE_1": ("NOTE: both account values come from dash.cloudflare.com ▸ "
               "Workers AI — the Account ID is the hex string in the "
               "dashboard URL, and the token is an API token with Workers AI "
               "access. Free tier, no credit card.", "info_box"),
    "NOTE_2": ("NOTE: got more than one Cloudflare account? Open this script "
               "(providers/large-language-models/provider_cloudflare.py), "
               "change ACCOUNTS near the top from 1 to how many you have, "
               "then reopen this window — that many ID/token pairs appear "
               "right here. They are tried in order and an account that hits "
               "its daily free neuron limit is skipped automatically for 24h.",
               "info_box"),
    "NOTE_3": ("NOTE: vision needs a vision-capable model — Kimi, "
               "Llama-4-Scout, Gemma-4, Mistral-Small or Llama-3.2-Vision.",
               "info_box"),
}

# ── Native tool calling (function calling) ──────────────────────────────────
# ENABLED (2026-06-28, verified on Kimi). Cloudflare Workers AI's OpenAI-
# compatible endpoint returns real tool_calls for its function-calling models
# (Kimi, GLM, gpt-oss, Nemotron, Llama-4/3.3, Gemma-4, Qwen3 — see catalog).
# Smaller/older models in the dropdown may ignore the tools channel — if one
# does, switch Tool Calling Mode back to Compatibility; nothing breaks.
SUPPORTS_NATIVE_TOOLS = True
NATIVE_DIALECT        = "openai"   # Cloudflare Workers AI speaks the OpenAI dialect

# Vision (contract v2.1). SUPPORTS_INLINE_IMAGES means a message may carry its
# own `images`, so an attachment stays anchored to the turn it arrived in
# instead of being re-stapled onto the newest user turn every request.
SUPPORTS_VISION       = True
SUPPORTS_INLINE_IMAGES = True
IMAGE_FORMATS         = ("png", "jpg", "jpeg", "gif", "webp")

# ─────────────────────────────────────────────────────────────────────────────

# Cache file lives next to this script so it persists across reloads/restarts.
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".exhaustion_cache.json"
)


def _load_cache() -> dict:
    """Read the exhaustion cache from disk. Returns {} if missing or corrupt."""
    try:
        with open(_CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    """Write the exhaustion cache to disk."""
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError as e:
        print(f"[provider] Warning: could not write exhaustion cache: {e}")


def _reset_after_24h() -> float:
    """Return the Unix timestamp 24 hours from now."""
    return time.time() + 86400


def _hours_left(reset_ts: float) -> str:
    """Human-readable time remaining until a reset timestamp."""
    secs = max(0, reset_ts - time.time())
    h, rem = divmod(int(secs), 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _mark_exhausted(account_id: str, label: str) -> None:
    """Record an account as exhausted for 24 hours from now. Persisted to disk."""
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
    """
    Returns True if the account is not exhausted.
    Automatically unblocks accounts whose 24-hour reset period has passed.
    """
    aid = account["account_id"]
    cache = _load_cache()
    reset_ts = cache.get(aid)
    if reset_ts is None:
        return True
    if time.time() >= reset_ts:
        # 24-hour cooldown has passed — unblock and remove from cache
        del cache[aid]
        _save_cache(cache)
        print(f"[provider] {account.get('label', aid)} unblocked after 24h cooldown.")
        return True
    return False


def _make_client(account: dict) -> OpenAI:
    return OpenAI(
        base_url=(
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{account['account_id']}/ai/v1"
        ),
        api_key=account["api_token"],
    )


def _split_reasoning(message) -> tuple:
    """(content, thinking) from a Kimi response message.

    K2.6+ puts reasoning in message.reasoning (was reasoning_content /
    inline <think>...</think> in the body). Legacy inline blocks are split
    out as a fallback in case a middleware proxy still emits them.
    """
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

    Supported formats: JPEG, PNG, GIF, WEBP.
    Returns an OpenAI-compatible image_url content block dict.

    Large images are automatically resized and compressed before encoding to
    stay within Cloudflare Workers AI's request payload limit (~4 MB base64).
    Cloudflare returns a cryptic 400 "Disaggregated request / bootstrap room id"
    error when the payload is too large — resizing prevents that entirely.

    Limits applied:
        MAX_DIMENSION : 1280 px on the longest side (model downsamples anyway)
        MAX_SIZE_KB   : 2048 KB after JPEG compression
        Quality floor : 40 (drops in steps of 10 until under the size cap)
    """
    from PIL import Image
    import io as _io

    MAX_DIMENSION = 1280   # px — longest side
    MAX_SIZE_KB   = 2048   # KB ceiling after compression

    original_kb = os.path.getsize(image_path) / 1024
    mime_type, _ = mimetypes.guess_type(image_path)
    mime_type = mime_type or "image/jpeg"

    with Image.open(image_path) as img:
        original_size = img.size

        # Convert palette / RGBA / other modes → RGB so JPEG save always works
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
            mime_type = "image/jpeg"

        # Downscale if either dimension exceeds the cap
        w, h = img.size
        if max(w, h) > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            print(
                f"[provider] Image resized: {original_size[0]}x{original_size[1]}"
                f" → {new_w}x{new_h}  (original: {original_kb:.1f} KB)"
            )

        # Compress to JPEG in memory; drop quality until under the size cap
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
    """
    Build an OpenAI-compatible multimodal user message with one OR MORE
    embedded base64 images, using the standard image_url content block format
    that Cloudflare Workers AI's OpenAI-compatible endpoint accepts.

    Multiple images are fully supported by Kimi K2.6 — each image gets its
    own image_url content block. The user text is appended as a final text block.

    Supported image formats: JPEG, PNG, GIF, WEBP.

    Parameters:
        image_paths  -- List of absolute paths to image files on disk.
                        Pass a list with one item for a single-image request.
        user_text    -- The user's text prompt accompanying the image(s).

    Returns:
        A {"role": "user", "content": [...]} dict ready to include in messages.
    """
    content_blocks = []

    for image_path in image_paths:
        content_blocks.append(_encode_image(image_path))

    # `user_text` is already a BLOCK LIST when this same turn also carried
    # inline images (contract v2.1) — splice those blocks in rather than
    # stuffing a list into a text field, which would silently drop them.
    if isinstance(user_text, list):
        content_blocks.extend(user_text)
        return {"role": "user", "content": content_blocks}

    content_blocks.append({
        "type": "text",
        "text": user_text,
    })

    return {
        "role": "user",
        "content": content_blocks,
    }


# ── Verbose error dump ────────────────────────────────────────────────────────

def _dump_verbose_error(
    error: Exception,
    full_messages: list,
    raw_system_prompt: str,
    raw_messages: list,
    image_paths: list[str] | None = None,
) -> None:
    """
    Print a full verbose dump when a non-neuron-limit error occurs.

    Prints four sections:
      1. The error itself.
      2. The raw inputs received from the app (system_prompt + messages + image_paths).
      3. The fully assembled messages list sent to the API provider
         (base64 image data truncated so the log stays readable).
      4. Image list summary (paths + sizes), if applicable.
    """
    SEP  = "=" * 72
    SEP2 = "-" * 72

    print()
    print(SEP)
    print("[provider] !! ERROR — VERBOSE DUMP FOLLOWS")
    print(SEP)

    # ── 1. Error ─────────────────────────────────────────────────────────────
    print()
    print("── ERROR ──────────────────────────────────────────────────────────")
    print(f"  Type : {type(error).__name__}")
    print(f"  Value: {error}")
    print()

    # ── 2. Raw inputs from the app ────────────────────────────────────────────
    print("── RAW INPUTS (app → custom script) ───────────────────────────────")
    print()
    print(f"  system_prompt ({len(raw_system_prompt)} chars):")
    if raw_system_prompt:
        for line in raw_system_prompt.splitlines():
            print(f"    {line}")
    else:
        print("    (empty)")
    print()

    print(f"  messages ({len(raw_messages)} turns):")
    for i, msg in enumerate(raw_messages):
        role    = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            preview = content[:300] + ("…" if len(content) > 300 else "")
            print(f"    [{i}] role={role!r}  content ({len(content)} chars):")
            for line in preview.splitlines():
                print(f"         {line}")
        elif isinstance(content, list):
            print(f"    [{i}] role={role!r}  content (multimodal, {len(content)} blocks):")
            for j, block in enumerate(content):
                btype = block.get("type", "?")
                if btype == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    # Truncate base64 blob for readability
                    if len(url) > 80:
                        url = url[:60] + f"…[{len(url)} chars total]"
                    print(f"         block[{j}] type=image_url  url={url}")
                elif btype == "text":
                    text = block.get("text", "")
                    preview = text[:200] + ("…" if len(text) > 200 else "")
                    print(f"         block[{j}] type=text  ({len(text)} chars): {preview!r}")
                else:
                    print(f"         block[{j}] type={btype!r}  {block}")
        else:
            print(f"    [{i}] role={role!r}  content: {str(content)[:200]!r}")
        print()

    if image_paths:
        print(f"  image_paths ({len(image_paths)} item(s)):")
        for p in image_paths:
            if os.path.isfile(p):
                size_kb = os.path.getsize(p) / 1024
                mime, _ = mimetypes.guess_type(p)
                print(f"    {p}  [{mime or 'unknown'}  {size_kb:.1f} KB]")
            else:
                print(f"    {p}  [FILE NOT FOUND]")
    else:
        print("  image_paths: (none — text-only request)")
    print()

    # ── 3. Full assembled messages sent to the API ────────────────────────────
    print("── ASSEMBLED MESSAGES (custom script → API provider) ───────────────")
    print()
    print(f"  model      : {MODEL}")
    print(f"  max_tokens : {MAX_TOKENS}")
    print(f"  total turns: {len(full_messages)}")
    print()
    for i, msg in enumerate(full_messages):
        role    = msg.get("role", "?")
        content = msg.get("content", "")
        print(f"  [{i}] role={role!r}")
        if isinstance(content, str):
            preview = content[:500] + ("…" if len(content) > 500 else "")
            print(f"       content ({len(content)} chars):")
            for line in preview.splitlines():
                print(f"         {line}")
        elif isinstance(content, list):
            print(f"       content ({len(content)} block(s)):")
            for j, block in enumerate(content):
                btype = block.get("type", "?")
                if btype == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    truncated = url[:60] + f"…[{len(url)} chars total]" if len(url) > 60 else url
                    print(f"         block[{j}] type=image_url  data={truncated}")
                elif btype == "text":
                    text = block.get("text", "")
                    preview = text[:300] + ("…" if len(text) > 300 else "")
                    print(f"         block[{j}] type=text  ({len(text)} chars):")
                    for line in preview.splitlines():
                        print(f"           {line}")
                else:
                    print(f"         block[{j}] {block}")
        else:
            print(f"       content: {str(content)[:300]!r}")
        print()

    # ── 4. Image list summary ─────────────────────────────────────────────────
    if image_paths:
        print("── IMAGE LIST SUMMARY ──────────────────────────────────────────────")
        print()
        total_kb = 0.0
        for idx, p in enumerate(image_paths):
            mime, _ = mimetypes.guess_type(p)
            if os.path.isfile(p):
                kb = os.path.getsize(p) / 1024
                total_kb += kb
                print(f"  [{idx}] {os.path.basename(p)}")
                print(f"       Path : {p}")
                print(f"       Type : {mime or 'unknown'}")
                print(f"       Size : {kb:.1f} KB")
            else:
                print(f"  [{idx}] {p}  << FILE NOT FOUND >>")
            print()
        print(f"  Total image data: {total_kb:.1f} KB across {len(image_paths)} file(s)")
        print()

    print(SEP)
    print("[provider] !! END OF VERBOSE DUMP")
    print(SEP)
    print()


# ── Core dispatcher ───────────────────────────────────────────────────────────

def _call_accounts(
    full_messages: list,
    raw_system_prompt: str = "",
    raw_messages: list = None,
    image_paths: list[str] | None = None,
    tools: list | None = None,
    stream: bool = False,
) -> object:
    """
    Core dispatcher: iterate accounts, skip exhausted ones, call the first
    available one.

    Args:
        full_messages   -- The assembled messages list sent to the API.
        raw_system_prompt -- Original system prompt (for error dumps).
        raw_messages    -- Original messages (for error dumps).
        image_paths     -- Image paths (for error dumps).
        tools           -- Optional list of OpenAI-format tool definitions.
                           Passed through to the API when provided.
        stream          -- Request an SDK stream instead of a completed
                           response. A neuron-limit 429 still raises at
                           create(), so account failover works unchanged.

    Returns:
        The raw OpenAI response object (or SDK stream when stream=True).
        Returns an all-exhausted error STRING when all accounts are depleted —
        callers must check isinstance(..., str).
    """
    raw_messages = raw_messages or []
    last_error   = None

    accounts = _accounts()
    if not accounts:
        return (
            "No Cloudflare account is set up yet.\n\n"
            "Open Settings ▸ AI ▸ Provider Settings and fill in "
            "\"Account 1 ID\" and \"Account 1 token\". Both come from "
            "dash.cloudflare.com ▸ Workers AI: the account ID is the hex "
            "string in the dashboard URL, and the token is an API token with "
            "Workers AI access. The free tier needs no credit card."
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
            client = _make_client(account)
            kwargs = dict(
                model=MODEL,
                messages=full_messages,
                max_tokens=MAX_TOKENS,
                stream=stream,
            )
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"   # nudge the model to actually call
            return client.chat.completions.create(**kwargs)

        except RateLimitError as e:
            if _is_neuron_limit(e):
                _mark_exhausted(account["account_id"], label)
                last_error = e
                continue  # instantly try the next non-exhausted account
            # Different 429 (e.g. per-minute rate limit) — dump then re-raise
            _dump_verbose_error(e, full_messages, raw_system_prompt, raw_messages, image_paths)
            raise

        except Exception as e:
            # Any unexpected error — dump then re-raise
            _dump_verbose_error(e, full_messages, raw_system_prompt, raw_messages, image_paths)
            raise

    # All accounts exhausted — no verbose dump needed, just a friendly message
    exhausted = ", ".join(a["label"] for a in accounts)
    return (
        f"All {len(accounts)} Cloudflare Workers AI account(s) have reached "
        f"their daily free neuron limit.\n\n"
        f"Exhausted accounts: {exhausted}\n\n"
        f"Last error: {last_error}\n\n"
        "Quota resets 24 hours after hitting the limit. You can add another "
        "free account — raise ACCOUNTS at the top of the provider script and "
        "fill in the new pair in Settings — or upgrade to Workers Paid."
    )


# ── Public API (contract v2) ──────────────────────────────────────────────────

def _build_full_messages(system_prompt: str, messages: list, image_paths=None) -> list:
    """Assemble the wire messages.

    Two image channels, deliberately separate:

    * INLINE (contract v2.1) — a message may carry its own `images`, which stay
      at that message's position in the conversation. This is how attachments
      persist: the picture belongs to the turn it was sent in, not to whatever
      the newest user turn happens to be.
    * FLAT `image_paths` — the one-shot queue from attach_image_to_context().
      Ephemeral by design, so it still rides the FINAL user turn.
    """
    from systema.engine import native_adapters as na

    messages = na.render_inline_images(messages, _encode_image, "openai")

    if image_paths:
        last_user_text = messages[-1]["content"] if messages else ""
        prior = messages[:-1] if len(messages) > 1 else []
        tail = [_build_vision_message(image_paths, last_user_text)]
    else:
        prior, tail = messages, []
    return (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + prior + tail


def chat(system_prompt: str, messages: list, *, images=None, tools=None, stream=False):
    """
    Unified v2 entry point — text, vision (multiple images), native tools and
    streaming in ONE path.

    Iterates accounts sequentially, instantly skipping any marked exhausted in
    the on-disk cache; only ONE live account is called per request. When ALL
    accounts are exhausted the human-readable notice becomes the reply text.
    On any non-neuron-limit error a full verbose dump is printed.
    """
    from systema.engine import native_adapters as na

    if isinstance(images, str):
        images = [images]
    full_messages = _build_full_messages(system_prompt, messages, image_paths=images)
    oai_tools = na.to_openai_tools(tools) if tools else None
    if images:
        n = len(images)
        print(f"[provider] Vision request — {n} image{'s' if n > 1 else ''} attached.")

    response = _call_accounts(
        full_messages,
        raw_system_prompt=system_prompt,
        raw_messages=messages,
        image_paths=images if images else None,
        tools=oai_tools,
        stream=stream,
    )

    # All accounts exhausted → a human-readable string becomes the reply.
    if isinstance(response, str):
        if stream:
            def _exhausted_stream(text=response):
                yield {"type": "text", "content": text, "finish_reason": None}
                yield {"type": "done", "content": "", "finish_reason": "stop"}
            return _exhausted_stream()
        return {"content": response, "thinking": None, "tool_calls": [],
                "finish_reason": "stop"}

    if stream:
        return _chunks(response)

    parsed = na.parse_openai(response.model_dump())
    msg = response.choices[0].message if response.choices else None
    content, thinking = _split_reasoning(msg) if msg else ("", None)
    return {
        "content": content,
        "thinking": thinking,
        "tool_calls": parsed.get("tool_calls") or [],
        "finish_reason": response.choices[0].finish_reason if response.choices else None,
    }


def _chunks(completion):
    """SDK stream → contract chunks via the shared engine helper. Reasoning
    arrives via delta.reasoning (K2.6+) or legacy inline <think> tags (split
    incrementally); complete tool calls are emitted at end of stream."""
    from systema.engine.provider_contract import stream_openai_chunks
    return stream_openai_chunks(completion)


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify credentials before using in Systema.
#
#   TEXT TEST (default):
#       python provider_cloudflare_kimi.py
#
#   SINGLE-IMAGE TEST:
#       python provider_cloudflare_kimi.py --vision /path/to/image.jpg
#
#   MULTI-IMAGE TEST:
#       python provider_cloudflare_kimi.py --vision /path/to/img1.jpg /path/to/img2.png
#
#   SHOW CACHE STATE ONLY:
#       python provider_cloudflare_kimi.py --cache

if __name__ == "__main__":
    import sys

    # Running this file directly means the app is not here to apply your saved
    # Settings values, so the slots are empty. Fill them from the environment
    # instead — e.g. on Windows:
    #     set CF_ACCOUNT_1_ID=...
    #     set CF_ACCOUNT_1_TOKEN=...
    # Inside Systema Auxilium none of this applies; Settings supplies them.
    for _i in range(1, int(ACCOUNTS) + 1):
        for _part in ("ID", "TOKEN"):
            if not globals().get(f"ACCOUNT_{_i}_{_part}"):
                globals()[f"ACCOUNT_{_i}_{_part}"] = os.environ.get(
                    f"CF_ACCOUNT_{_i}_{_part}", "")

    _configured = _accounts()

    print("Testing Cloudflare Workers AI — Kimi (sequential + disk cache)")
    print(f"Model:    {MODEL}")
    print(f"Accounts: {len(_configured)} filled of {ACCOUNTS} slot(s)")
    print(f"Cache:    {_CACHE_FILE}")
    print("-" * 60)

    if not _configured:
        print("No account credentials found.")
        print("Set CF_ACCOUNT_1_ID and CF_ACCOUNT_1_TOKEN in your environment "
              "to test this script standalone,")
        print("or just fill them in under Settings ▸ AI ▸ Provider Settings "
              "and test from inside the app.")
        sys.exit(1)

    # ── Show current cache state ───────────────────────────────────────────
    cache = _load_cache()
    if cache:
        print("Currently exhausted accounts (from disk cache):")
        for aid, ts in cache.items():
            reset_str = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            label = next((a["label"] for a in _configured if a["account_id"] == aid), aid)
            hrs = _hours_left(ts)
            print(f"  - {label}: resets at {reset_str} ({hrs} from now)")
        print("-" * 60)
    else:
        print("No exhausted accounts in cache — all accounts available.")
        print("-" * 60)

    # ── --cache: just print the state and exit ─────────────────────────────
    if "--cache" in sys.argv:
        sys.exit(0)

    # ── Vision test ────────────────────────────────────────────────────────
    # Usage: python provider_cloudflare_kimi.py --vision img1.jpg [img2.png ...]
    if "--vision" in sys.argv:
        idx = sys.argv.index("--vision")
        image_args = sys.argv[idx + 1:]

        if not image_args:
            print("Usage:")
            print("  Single image:  python provider_cloudflare_kimi.py --vision /path/to/image.jpg")
            print("  Multi-image:   python provider_cloudflare_kimi.py --vision /path/img1.jpg /path/img2.png")
            sys.exit(1)

        # Validate all paths before sending
        missing = [p for p in image_args if not os.path.isfile(p)]
        if missing:
            print(f"Error: file(s) not found: {missing}")
            sys.exit(1)

        n = len(image_args)
        plural = "s" if n > 1 else ""
        print(f"Vision test — {n} image{plural}:")
        for p in image_args:
            size_kb = os.path.getsize(p) / 1024
            print(f"  [{os.path.basename(p)}]  {size_kb:.1f} KB")
        print("-" * 60)

        # Craft a prompt that makes sense for 1 or N images
        if n == 1:
            prompt = "Describe this image in one sentence."
        else:
            prompt = (
                f"I'm sending you {n} images. "
                "Please describe each one in a single sentence, numbered."
            )

        try:
            out = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
                images=image_args,   # list — single or multi
            )
            result = out["content"]
            print("Response:", result)
            print("-" * 60)
            if "neuron limit" in result:
                print("All accounts exhausted — see message above.")
            else:
                print(f"Vision test passed ({n} image{plural}). Provider is working correctly.")
        except Exception as e:
            print(f"Vision test failed: {e}")

    # ── Standard text test ─────────────────────────────────────────────────
    else:
        try:
            out = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
            )
            result = out["content"]
            print("Response:", result)
            print("-" * 60)
            if "All" in result and "neuron limit" in result:
                print("All accounts exhausted — see message above.")
            else:
                print("Text test passed. Provider is working correctly.")
        except Exception as e:
            print(f"Test failed: {e}")