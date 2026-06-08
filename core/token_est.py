"""
core/token_est.py
Token Estimator — centralized token estimation and usage logging.
Rule of thumb: ~4 characters per token for English text.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
_USAGE_FILE = _APP_ROOT / "data" / "token_usage.json"


# ─────────────────────────── Estimation ──────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count for a single string. ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_history_tokens(chat_history: list) -> int:
    """Estimate total tokens for a list of {role, content} message dicts."""
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


def get_output_usage_data(mode: str = "Daily") -> list:
    """Same bucketing as get_usage_data() but for output tokens."""
    entries = _load_output_raw()
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
        else:
            key = dt.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + n

    result = sorted(buckets.items(), key=lambda x: x[0])
    if mode == "Monthly":
        result = result[-12:]
    max_pts = {"Minutes": 30, "Hourly": 24, "Daily": 30, "Weekly": 12,
               "Monthly": 12, "Yearly": 20, "All": 24}.get(mode, 20)
    return result[-max_pts:]

def get_usage_data(mode: str = "Daily") -> list:
    """
    Aggregate token usage by mode.
    Returns list of (label: str, tokens: int) for the graph.
    mode options: Minutes | Hourly | Daily | Weekly | Monthly | Yearly | All
    """
    entries = _load_raw()
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

    max_pts = {"Minutes": 30, "Hourly": 24, "Daily": 30, "Weekly": 12,
               "Monthly": 12, "Yearly": 20, "All": 24}.get(mode, 20)
    return result[-max_pts:]