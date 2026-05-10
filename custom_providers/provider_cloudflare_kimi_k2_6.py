"""
provider_cloudflare_kimi_k2_6.py
==================================
Custom Script Provider for Systema Auxilium.
Uses Kimi K2.6 via Cloudflare Workers AI — FREE tier available.

Model: @cf/moonshotai/kimi-k2.6
Endpoint: https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1

Free tier: ~2-5 million tokens/day (10,000 neurons/day). No credit card needed.
Context window: 262,144 tokens.
Vision: YES — supports MULTIPLE images per request via base64 image_url content blocks.

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

Kimi K2.6 API notes (differs from K2.5):
    - Reasoning control:  chat_template_kwargs.thinking  (was enable_thinking)
    - Reasoning output:   response.choices[0].message.reasoning  (was reasoning_content)
    - Supported image formats: JPEG, PNG, GIF, WEBP
    - Multiple images per request: YES (pass a list to chat_image)

Setup:
    Add each Cloudflare account as a dict in ACCOUNTS below:
        {"account_id": "...", "api_token": "...", "label": "..."}

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

ACCOUNTS = [
    {
        "account_id": "account id",
        "api_token":  "api token",
        "label":      "any appropriate name for identification",
    },
]

MODEL          = "@cf/moonshotai/kimi-k2.6"
MAX_TOKENS     = 4096   # Raise up to 16384 if needed — watch your neuron budget
SHOW_REASONING = False  # Set True to include reasoning content in the reply

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


def _extract_reply(response) -> str:
    """
    Extract the reply text from a Kimi K2.6 response.

    K2.6 change vs K2.5:
        - Reasoning content is now in response.choices[0].message.reasoning
          (was reasoning_content / <think>...</think> in the body).
        - Main reply is still in response.choices[0].message.content.

    If SHOW_REASONING is True, the reasoning block is prepended to the reply.
    Fallback: also strips legacy <think>...</think> tags from content in case
    they still appear (e.g. when proxied through a middleware).
    """
    message = response.choices[0].message
    content = message.content or ""

    # Grab the structured reasoning field (K2.6+)
    reasoning = getattr(message, "reasoning", None) or ""

    # Fallback: strip legacy <think> blocks from content body (K2.5 style)
    if not SHOW_REASONING:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    if SHOW_REASONING and reasoning:
        return f"<think>\n{reasoning.strip()}\n</think>\n\n{content}".strip()

    return content.strip()


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
) -> str:
    """
    Core dispatcher: iterate accounts, skip exhausted ones, call the first
    available one. Returns the reply string or an all-exhausted error message.

    On any non-neuron-limit error the full verbose dump is printed before
    re-raising so you can see exactly what was sent.
    """
    raw_messages = raw_messages or []
    last_error   = None

    for account in ACCOUNTS:
        label = account.get("label", account["account_id"])

        if not _is_account_available(account):
            cache = _load_cache()
            hrs = _hours_left(cache.get(account["account_id"], 0))
            print(f"[provider] — Skipping {label} (exhausted, resets in {hrs})")
            continue

        print(f"[provider] Trying {label} ({account['account_id'][:8]}...)")

        try:
            client = _make_client(account)
            response = client.chat.completions.create(
                model=MODEL,
                messages=full_messages,
                max_tokens=MAX_TOKENS,
                stream=False,
            )
            reply = _extract_reply(response)
            print(f"[provider] OK Response from {label}")
            return reply or "No response received."

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
    exhausted = ", ".join(a.get("label", a["account_id"]) for a in ACCOUNTS)
    return (
        f"All {len(ACCOUNTS)} Cloudflare Workers AI account(s) have reached "
        f"their daily free neuron limit.\n\n"
        f"Exhausted accounts: {exhausted}\n\n"
        f"Last error: {last_error}\n\n"
        "Quota resets 24 hours after hitting the limit. You can add more free "
        "accounts to the ACCOUNTS list in the provider script, or upgrade to Workers Paid."
    )


# ── Public API ────────────────────────────────────────────────────────────────

def chat(system_prompt: str, messages: list) -> str:
    """
    Send a text-only request to Cloudflare Workers AI (Kimi K2.6) and return
    the reply.

    Iterates accounts sequentially, instantly skipping any marked exhausted
    in the on-disk cache. Only ONE live account is called per request.
    Neuron-limit failures are written to disk and skipped on future calls
    for 24 hours — even across script reloads and process restarts.

    On any non-neuron-limit error, a full verbose dump is printed showing
    the raw inputs from the app and the assembled messages sent to the API.
    """
    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    return _call_accounts(
        full_messages,
        raw_system_prompt=system_prompt,
        raw_messages=messages,
        image_paths=None,
    )


def chat_image(
    system_prompt: str,
    messages: list,
    image_paths: list[str] | str,
) -> str:
    """
    Send a vision request (text + one or more images) to Cloudflare Workers AI
    (Kimi K2.6).

    Kimi K2.6 natively supports vision inputs — including MULTIPLE images in a
    single request — via Cloudflare's OpenAI-compatible /v1/chat/completions
    endpoint using base64 image_url content blocks.

    Parameters:
        system_prompt  -- Same as chat(). May be empty.
        messages       -- Same as chat(). The latest user message is last.
        image_paths    -- One of:
                            • A list of absolute paths to image files on disk.
                            • A single path string (auto-wrapped into a list).
                          All images are sent in the SAME request as separate
                          image_url content blocks — Kimi K2.6 can reason over
                          all of them simultaneously.
                          Supported formats: JPEG, PNG, GIF, WEBP.

    Return:
        A non-empty string containing the assistant's reply.

    How it works:
        - Prior conversation turns (all messages except the final user turn)
          are passed through as plain text messages so that conversational
          context is preserved.
        - The final user turn is replaced with a multimodal message containing:
            1. One image_url block per image (base64 data-URI).
            2. The user's text as a final text block.
        - The same multi-account failover + exhaustion cache logic used by
          chat() applies here too.
        - On any non-neuron-limit error, a full verbose dump is printed showing
          the raw inputs from the app and the assembled messages sent to the API.

    Single-image example:
        chat_image(system, msgs, "/path/to/photo.jpg")
        chat_image(system, msgs, ["/path/to/photo.jpg"])   # equivalent

    Multi-image example:
        chat_image(system, msgs, ["/path/to/img1.png", "/path/to/img2.jpg"])
    """
    # Accept a bare string for single-image callers (backwards compat)
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    if not image_paths:
        raise ValueError("chat_image() requires at least one image path.")

    # The last entry in messages is always the current user prompt.
    last_user_text = messages[-1]["content"] if messages else ""
    prior_messages = messages[:-1] if len(messages) > 1 else []

    vision_message = _build_vision_message(image_paths, last_user_text)

    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + prior_messages + [vision_message]

    n = len(image_paths)
    print(f"[provider] Vision request — {n} image{'s' if n > 1 else ''} attached.")

    return _call_accounts(
        full_messages,
        raw_system_prompt=system_prompt,
        raw_messages=messages,
        image_paths=image_paths,
    )


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify credentials before using in Systema.
#
#   TEXT TEST (default):
#       python provider_cloudflare_kimi_k2_6.py
#
#   SINGLE-IMAGE TEST:
#       python provider_cloudflare_kimi_k2_6.py --vision /path/to/image.jpg
#
#   MULTI-IMAGE TEST:
#       python provider_cloudflare_kimi_k2_6.py --vision /path/to/img1.jpg /path/to/img2.png
#
#   SHOW CACHE STATE ONLY:
#       python provider_cloudflare_kimi_k2_6.py --cache

if __name__ == "__main__":
    import sys

    print("Testing Cloudflare Workers AI — Kimi K2.6 (sequential + disk cache)")
    print(f"Model:    {MODEL}")
    print(f"Accounts: {len(ACCOUNTS)} configured")
    print(f"Cache:    {_CACHE_FILE}")
    print("-" * 60)

    # ── Show current cache state ───────────────────────────────────────────
    cache = _load_cache()
    if cache:
        print("Currently exhausted accounts (from disk cache):")
        for aid, ts in cache.items():
            reset_str = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
            label = next((a["label"] for a in ACCOUNTS if a["account_id"] == aid), aid)
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
    # Usage: python provider_cloudflare_kimi_k2_6.py --vision img1.jpg [img2.png ...]
    if "--vision" in sys.argv:
        idx = sys.argv.index("--vision")
        image_args = sys.argv[idx + 1:]

        if not image_args:
            print("Usage:")
            print("  Single image:  python provider_cloudflare_kimi_k2_6.py --vision /path/to/image.jpg")
            print("  Multi-image:   python provider_cloudflare_kimi_k2_6.py --vision /path/img1.jpg /path/img2.png")
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
            result = chat_image(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
                image_paths=image_args,   # list — single or multi
            )
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
            result = chat(
                system_prompt="You are a helpful assistant.",
                messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
            )
            print("Response:", result)
            print("-" * 60)
            if "All" in result and "neuron limit" in result:
                print("All accounts exhausted — see message above.")
            else:
                print("Text test passed. Provider is working correctly.")
        except Exception as e:
            print(f"Test failed: {e}")