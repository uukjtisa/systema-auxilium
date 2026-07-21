"""
common/app_config.py
One settings.json at APP_ROOT holds every persisted app config, split into
named sections:

    {
      "settings":               { ... },   # controller / assistant settings
      "chat_window_config":     { ... },   # avatars, zoom, window geometry
      "floating_window_config": { ... }    # floating button appearance/position
    }

Per-provider Display values live inside the "settings" section, under
`provider_display_values` keyed by script file name.

Replaces the three legacy root files (assistant_settings.json, chat_config.json,
floating_window_config.json). On first load, any legacy file found is merged
into settings.json and — only after the merged file is written and verified to
parse back — deleted. A legacy file that fails to parse is skipped and left on
disk untouched.

Callers use load_section(name) / save_section(name, data); the JSON shape
INSIDE each section is unchanged from the legacy per-file era. Writes are
atomic (temp file + os.replace) and read-merge-write under a module lock, so
saving one section can never clobber another.
"""

import json
import os
import threading
import tempfile

from systema import APP_ROOT
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("AppConfig") if _verbose else _NoOpLogger()

CONFIG_FILE = APP_ROOT / "settings.json"

# section name → legacy root file it replaces
LEGACY_FILES = {
    "settings": "assistant_settings.json",
    "chat_window_config": "chat_config.json",
    "floating_window_config": "floating_window_config.json",
}

_lock = threading.Lock()


def _read_json(path) -> dict | None:
    """Parse a JSON file; None on missing/unreadable/non-dict."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.error(f"[app_config._read_json] ✗ {path}: {type(e).__name__}: {e}")
        return None


def _atomic_write(path, data: dict) -> bool:
    """Write JSON via temp file + os.replace so a crash mid-write can never
    leave a truncated settings.json."""
    try:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except Exception as e:
        log.error(f"[app_config._atomic_write] ✗ {path}: {type(e).__name__}: {e}")
        return False


def _migrate(config_file) -> dict:
    """One-time merge of the legacy per-file configs into settings.json.
    Returns the merged dict (possibly empty on a fresh install). Legacy files
    are deleted only for sections that made it into a verified settings.json."""
    legacy_dir = config_file.parent
    merged, migrated_paths = {}, []
    for section, filename in LEGACY_FILES.items():
        path = legacy_dir / filename
        data = _read_json(path)
        if data is not None:
            merged[section] = data
            migrated_paths.append(path)
    if not migrated_paths:
        return {}

    if not _atomic_write(config_file, merged):
        return merged  # keep working off the in-memory merge; legacy stays put
    if _read_json(config_file) != merged:
        log.error("[app_config._migrate] ✗ Verify-read of merged settings.json "
                  "failed — legacy files kept")
        return merged

    for path in migrated_paths:
        try:
            os.remove(path)
        except OSError as e:
            log.warning(f"[app_config._migrate] Could not delete legacy '{path}': {e}")
    log.info(f"[app_config._migrate] ✓ Migrated {len(migrated_paths)} legacy config "
             f"file(s) into '{config_file}'")
    return merged


def _load_all(config_file) -> dict:
    data = _read_json(config_file)
    if data is not None:
        return data
    return _migrate(config_file)


def load_section(name: str, config_file=None) -> dict:
    """Return one section's dict ({} when absent — callers keep their own
    defaults). First call on a legacy install performs the migration."""
    config_file = config_file or CONFIG_FILE
    with _lock:
        section = _load_all(config_file).get(name)
    return section if isinstance(section, dict) else {}


def save_section(name: str, data: dict, config_file=None) -> bool:
    """Persist one section, preserving all others (read-merge-write)."""
    config_file = config_file or CONFIG_FILE
    with _lock:
        whole = _load_all(config_file)
        whole[name] = data
        return _atomic_write(config_file, whole)
