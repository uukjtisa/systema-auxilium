"""
Script Trigger Template
────────────────────────────────────────────────────────────────
Contract: define fire_ping() → return True to fire the AI ping,
          return False to skip this poll cycle.

The poller calls fire_ping() every N milliseconds (your Poll Rate).
Once True fires and the ping completes, polling resumes immediately.
It is YOUR responsibility to reset the condition back to False so
the ping does not keep firing on every subsequent poll cycle.

This Script is Reloaded Everytime. And not persistent! States must be saved somewhere else or on disk!
────────────────────────────────────────────────────────────────
"""


def fire_ping() -> bool:
    """
    Return True  → ping fires immediately.
    Return False → sleep for Poll Rate ms and check again.
    """
    # ── Example: fire once when a sentinel file appears ──────
    # from pathlib import Path
    # flag = Path("C:/trigger.flag")
    # if flag.exists():
    #     flag.unlink()   # ← delete it so it won't re-fire
    #     return True
    # return False

    return False
