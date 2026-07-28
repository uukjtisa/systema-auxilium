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
    mod = sys.modules.get(module_path)

    if mod is None:
        # Not imported YET — not an error. Several windows are imported lazily,
        # the first time they are opened (settings_window at
        # floating_window.py:655, for one), so in a run where you never opened
        # Settings there is simply nothing loaded to replace. The old message
        # asked "was it ever imported, or is the path wrong?", which sent you
        # hunting a typo in a path that was perfectly correct.
        #
        # Importing it IS the reload: it reads the current file from disk, which
        # is the entire point of the button, and the post-hook then runs against
        # a real module.
        try:
            importlib.import_module(module_path)
        except Exception:
            tb = traceback.format_exc()
            ts = datetime.now().strftime("%H:%M:%S")
            return False, f"Failed to import at {ts}\n\n{tb}"
        ts = datetime.now().strftime("%H:%M:%S")
        return True, f"Loaded at {ts} (was not imported yet)"

    try:
        importlib.reload(mod)
        ts = datetime.now().strftime("%H:%M:%S")
        return True, f"Reloaded at {ts}"
    except Exception:
        tb = traceback.format_exc()
        ts = datetime.now().strftime("%H:%M:%S")
        return False, f"Failed at {ts}\n\n{tb}"
