"""
Script Trigger — Discord Listener
────────────────────────────────────────────────────────────────
Fires when host_bot.py writes ~/discord_ping.json, which happens
whenever the Discord bot receives a DM or a mention.

Contract  : fire_ping() → True  = ping fires now
                        → False = skip, wait for next poll cycle.

Deletion is handled HERE (not in host_bot.py).
After detecting the file this poller spawns a background thread
that waits _DELETE_AFTER seconds then removes it — identical to
the email_listener pattern. This guarantees the poller always
sees the file before it disappears, and host_bot.py uses the
file's presence as a "busy" lock to block overwrites.
────────────────────────────────────────────────────────────────
"""

import json
import threading
import time
from pathlib import Path

# ── Path ───────────────────────────────────────────────────────
_PING_FILE     = Path.home() / "discord_ping.json"
_DELETE_AFTER  = 5   # seconds to wait before deleting the ping file


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _schedule_delete():
    """Spawn a daemon thread that deletes the ping file after _DELETE_AFTER seconds."""
    def _do_delete():
        time.sleep(_DELETE_AFTER)
        try:
            _PING_FILE.unlink(missing_ok=True)
            print(f"[discord_listener] Ping file deleted after {_DELETE_AFTER}s.")
        except Exception as exc:
            print(f"[discord_listener] Could not delete ping file: {exc}")

    threading.Thread(target=_do_delete, daemon=True).start()


# ──────────────────────────────────────────────────────────────
# Main trigger
# ──────────────────────────────────────────────────────────────

def fire_ping() -> bool:
    """
    Return True  → discord_ping.json found → ping fires now.
    Return False → no file yet → wait for next poll cycle.

    On True the file is scheduled for deletion after _DELETE_AFTER
    seconds via a background thread. host_bot.py treats the file's
    presence as a busy-lock so it won't overwrite until we delete.
    """
    if not _PING_FILE.exists():
        return False

    try:
        data = json.loads(_PING_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        # Partial write / race condition — skip this cycle, try next poll.
        print(f"[discord_listener] Could not read ping file (will retry): {exc}")
        return False

    event_type   = data.get("type", "unknown")
    author       = data.get("author_display_name") or data.get("author_name", "?")
    channel_name = data.get("channel_name", "DM")
    guild        = data.get("guild_name") or "DM"
    content      = data.get("content", "")

    print(
        f"[discord_listener] PING ▶  type={event_type} | "
        f"from={author} | channel={channel_name} | guild={guild}\n"
        f"                   content={content[:120]!r}"
    )

    # Schedule deletion — this also releases the busy-lock in host_bot.py
    _schedule_delete()
    return True
