"""
core/token_est.py
Token Estimator — centralized token estimation and usage logging.
Rule of thumb: ~4 characters per token for English text.
"""

import json
from datetime import datetime, timezone

from systema import APP_ROOT as _APP_ROOT
_USAGE_FILE = _APP_ROOT / "data" / "token_usage.json"


# ─────────────────────────── Estimation ──────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count for a single string. ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── Image tokens ─────────────────────────────────────────────────────────────
# The tile formula OpenAI and Anthropic both document, and which every serious
# token counter implements: clamp the long side, then the short side, then
# charge a flat base plus a per-512px-tile rate.
#
# This matters more than it looks. An attached image is re-sent on EVERY turn
# for as long as it stays in context, so a handful of screenshots can quietly
# become the largest line item in a request — and until now the token pill
# counted exactly none of it.
IMAGE_BASE_TOKENS = 85       # flat cost per image
IMAGE_TILE_TOKENS = 170      # per 512x512 tile
IMAGE_MAX_LONG = 2048        # long side is clamped to this first
IMAGE_MAX_SHORT = 768        # then the short side to this
IMAGE_TILE_PX = 512


def estimate_image_tokens(width: int, height: int) -> int:
    """Approximate token cost of one image at the given pixel dimensions.

    Falls back to a single-tile estimate when dimensions are unknown (0), which
    is the honest floor — better than reporting zero for an image that is
    definitely costing something.
    """
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return IMAGE_BASE_TOKENS + IMAGE_TILE_TOKENS
    if w <= 0 or h <= 0:
        return IMAGE_BASE_TOKENS + IMAGE_TILE_TOKENS

    # Clamp the long side, preserving aspect.
    if max(w, h) > IMAGE_MAX_LONG:
        scale = IMAGE_MAX_LONG / max(w, h)
        w, h = max(1, int(w * scale)), max(1, int(h * scale))
    # Then the short side.
    if min(w, h) > IMAGE_MAX_SHORT:
        scale = IMAGE_MAX_SHORT / min(w, h)
        w, h = max(1, int(w * scale)), max(1, int(h * scale))

    tiles = -(-w // IMAGE_TILE_PX) * -(-h // IMAGE_TILE_PX)   # ceil-div both
    return IMAGE_BASE_TOKENS + IMAGE_TILE_TOKENS * tiles


def estimate_refs_tokens(refs) -> int:
    """Total image tokens for a list of ImageRefs, counting only ATTACHED ones.

    A detached image costs a one-line marker, not a picture — charging for it
    would make the readout lie in the direction that matters least to the user.
    """
    total = 0
    for ref in (refs or []):
        if isinstance(ref, dict) and ref.get('attached'):
            total += estimate_image_tokens(ref.get('w', 0), ref.get('h', 0))
    return total


def estimate_history_tokens(chat_history: list) -> int:
    """Estimate total tokens for a list of {role, content} message dicts.

    Includes attached images (`_images`), since they ride the entry they belong
    to and are re-sent with every request.
    """
    total = 0
    for msg in chat_history:
        content = msg.get('content', '')
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += estimate_tokens(
                        block.get('text', '') or block.get('content', ''))
        elif isinstance(content, str):
            total += estimate_tokens(content)
        total += estimate_refs_tokens(msg.get('_images'))
        total += 4   # per-message framing overhead
    return total


def estimate_next_message_tokens(input_text: str, chat_history: list) -> int:
    """Estimate tokens that will be sent with the next user message."""
    return estimate_tokens(input_text) + estimate_history_tokens(chat_history)


# ─────────────────────────── Usage Logging ───────────────────────────────────

def log_tokens(token_count: int) -> None:
    """Append a token usage entry to the usage log file."""
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        entries = _load_raw()
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "n": int(token_count)
        })
        if len(entries) > 100_000:
            entries = entries[-100_000:]
        with open(_USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(entries, f)
    except Exception:
        pass


def _load_raw() -> list:
    try:
        if _USAGE_FILE.exists():
            with open(_USAGE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


_OUTPUT_USAGE_FILE = _APP_ROOT / "data" / "token_usage_output.json"


def log_output_tokens(token_count: int) -> None:
    """Append an output token usage entry to the output log file."""
    try:
        _OUTPUT_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        entries = _load_output_raw()
        entries.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "n": int(token_count)
        })
        if len(entries) > 100_000:
            entries = entries[-100_000:]
        with open(_OUTPUT_USAGE_FILE, 'w', encoding='utf-8') as f:
            json.dump(entries, f)
    except Exception:
        pass


def _load_output_raw() -> list:
    try:
        if _OUTPUT_USAGE_FILE.exists():
            with open(_OUTPUT_USAGE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


MAX_POINTS = {"Minutes": 30, "Hourly": 24, "Daily": 30, "Weekly": 12,
              "Monthly": 12, "Yearly": 20, "All": 24}


def bucket_entries(entries: list, mode: str = "Daily") -> list:
    """Aggregate `{"ts", "n"}` log entries into [(label, total), ...] for a graph.

    THE one bucketing implementation. Input tokens, output tokens, API requests
    and API responses all log the same shape and all want the same time
    windows, so they all come through here — this was copied verbatim per
    series, which is how the four graphs would have drifted apart the first
    time someone adjusted a window.

    mode: Minutes | Hourly | Daily | Weekly | Monthly | Yearly | All
    """
    if not entries:
        return []

    parsed = []
    for e in entries:
        try:
            dt = datetime.fromisoformat(e['ts'])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            parsed.append((dt, int(e.get('n', 0))))
        except Exception:
            continue

    if not parsed:
        return []

    now = datetime.now(timezone.utc)
    buckets = {}

    for dt, n in parsed:
        if mode == "Minutes":
            if int((now - dt).total_seconds() / 60) > 59:
                continue
            key = dt.strftime("%H:%M")
        elif mode == "Hourly":
            if int((now - dt).total_seconds() / 3600) > 23:
                continue
            key = dt.strftime("%H:00")
        elif mode == "Daily":
            if (now.date() - dt.date()).days > 29:
                continue
            key = dt.strftime("%m/%d")
        elif mode == "Weekly":
            if (now.date() - dt.date()).days > 83:
                continue
            key = f"W{dt.isocalendar().week}"
        elif mode == "Monthly":
            key = dt.strftime("%b %y")
        elif mode == "Yearly":
            key = dt.strftime("%Y")
        else:  # All
            key = dt.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + n

    result = sorted(buckets.items(), key=lambda x: x[0])
    if mode == "Monthly":
        result = result[-12:]
    return result[-MAX_POINTS.get(mode, 20):]


def get_output_usage_data(mode: str = "Daily") -> list:
    """Output-token totals per time bucket."""
    return bucket_entries(_load_output_raw(), mode)


def get_usage_data(mode: str = "Daily") -> list:
    """Input-token totals per time bucket."""
    return bucket_entries(_load_raw(), mode)


# ── API call counts ──────────────────────────────────────────────────────────
# Counted separately from tokens because they answer a different question:
# tokens say how much a turn COST, these say whether the provider is actually
# answering. A request logged with no matching response is a failure, a timeout
# or a retry — the gap between the two series is the interesting signal, which
# is why they are two series and not one.

_REQUEST_FILE = _APP_ROOT / "data" / "api_requests.json"
_RESPONSE_FILE = _APP_ROOT / "data" / "api_responses.json"


def _log_event(path, n: int = 1) -> None:
    """Append one `{"ts", "n"}` entry. Never raises — telemetry must not be
    able to break a chat turn."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = _load_events(path)
        entries.append({"ts": datetime.now(timezone.utc).isoformat(), "n": int(n)})
        if len(entries) > 100_000:
            entries = entries[-100_000:]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(entries, f)
    except Exception:
        pass


def _load_events(path) -> list:
    try:
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


def log_request() -> None:
    """One outbound call to a provider."""
    _log_event(_REQUEST_FILE)


def log_response() -> None:
    """One reply actually received back from a provider."""
    _log_event(_RESPONSE_FILE)


def get_request_data(mode: str = "Daily") -> list:
    """Requests sent per time bucket."""
    return bucket_entries(_load_events(_REQUEST_FILE), mode)


def get_response_data(mode: str = "Daily") -> list:
    """Responses received per time bucket."""
    return bucket_entries(_load_events(_RESPONSE_FILE), mode)