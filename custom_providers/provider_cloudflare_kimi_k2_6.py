"""
provider_cloudflare_kimi_k2_6.py
==================================
Custom Script Provider for Systema Auxilium.
Uses Kimi K2.6 via Cloudflare Workers AI — FREE tier available.

Model: @cf/moonshotai/kimi-k2.6
Endpoint: https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1

Free tier: ~2-5 million tokens/day (10,000 neurons/day). No credit card needed.
Context window: 262,100 tokens.

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
SHOW_REASONING = False  # Set True to include <think>...</think> in the reply

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


def _strip_thinking(text: str) -> str:
    if not SHOW_REASONING and "<think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text


def _is_neuron_limit(error: RateLimitError) -> bool:
    body = str(error)
    return "4006" in body or "neurons" in body.lower() or "daily free allocation" in body.lower()


def chat(system_prompt: str, messages: list) -> str:
    """
    Send a request to Cloudflare Workers AI (Kimi K2.6) and return the reply.

    Iterates accounts sequentially, instantly skipping any marked exhausted
    in the on-disk cache. Only ONE live account is called per request.
    Neuron-limit failures are written to disk and skipped on future calls
    for 24 hours — even across script reloads and process restarts.
    """
    full_messages = (
        [{"role": "system", "content": system_prompt}] if system_prompt else []
    ) + messages

    last_error = None

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
            reply = response.choices[0].message.content or ""
            reply = _strip_thinking(reply)
            print(f"[provider] OK Response from {label}")
            return reply or "No response received."

        except RateLimitError as e:
            if _is_neuron_limit(e):
                _mark_exhausted(account["account_id"], label)
                last_error = e
                continue  # instantly try the next non-exhausted account
            raise  # different 429 (e.g. per-minute) — let it surface

        # any other exception bubbles up immediately

    # All accounts exhausted
    exhausted = ", ".join(a.get("label", a["account_id"]) for a in ACCOUNTS)
    return (
        f"All {len(ACCOUNTS)} Cloudflare Workers AI account(s) have reached "
        f"their daily free neuron limit.\n\n"
        f"Exhausted accounts: {exhausted}\n\n"
        f"Last error: {last_error}\n\n"
        "Quota resets 24 hours after hitting the limit. You can add more free "
        "accounts to the ACCOUNTS list in the provider script, or upgrade to Workers Paid."
    )


# ── Quick test ────────────────────────────────────────────────────────────────
# Run directly to verify credentials before using in Systema.
#   python provider_cloudflare_kimi_k2_6.py

if __name__ == "__main__":
    print("Testing Cloudflare Workers AI — Kimi K2.6 (sequential + disk cache)")
    print(f"Model:    {MODEL}")
    print(f"Accounts: {len(ACCOUNTS)} configured")
    print(f"Cache:    {_CACHE_FILE}")
    print("-" * 56)

    # Show current cache state
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
        print("-" * 56)

    try:
        result = chat(
            system_prompt="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Say 'Provider test successful.' and nothing else."}],
        )
        print("Response:", result)
        print("-" * 56)
        if "All" in result and "neuron limit" in result:
            print("All accounts exhausted — see message above.")
        else:
            print("Test passed. Provider is working correctly.")
    except Exception as e:
        print(f"Test failed: {e}")
