"""
core/hot_reload.py
Hot Reload — pure reload mechanism. Owns nothing, knows nothing about
the app structure. The registry and post-hooks live in debug_window.py.

This file must never be reloaded itself — it is the stable host.
If you change this file, restart the app.
"""

import importlib
import sys
import traceback
from datetime import datetime


def reload_module(module_path: str) -> tuple[bool, str]:
    """
    Reload a module by its dotted path string.

    Returns (success: bool, detail_message: str).
    On failure the old module stays live — nothing breaks.
    """
    if module_path not in sys.modules:
        return False, (
            f"Module '{module_path}' not in sys.modules.\n"
            "Was it ever imported, or is the path wrong?"
        )

    try:
        mod = sys.modules[module_path]
        importlib.reload(mod)
        ts = datetime.now().strftime("%H:%M:%S")
        return True, f"Reloaded at {ts}"
    except Exception:
        tb = traceback.format_exc()
        ts = datetime.now().strftime("%H:%M:%S")
        return False, f"Failed at {ts}\n\n{tb}"
