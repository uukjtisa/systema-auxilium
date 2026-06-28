"""
core/path_syncer.py
Path Syncer - Live system environment merger
Reads real system environment variables (registry on Windows, config files on Linux/macOS)
and merges any NEW paths/vars into the running Python process's os.environ.

- Does NOT overwrite existing process vars (read-only from system side)
- Deduplicates PATH entries on every merge
- Runs an initial merge on start()
- Ticks every SYNC_INTERVAL seconds in a background daemon thread
- Also called explicitly before every PythonInterpreter.execute() call
"""

import os
import sys
import threading
from systema.common.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = False
log = _make_logger("PathSyncer") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

SYNC_INTERVAL = 20  # seconds between background ticks


# ─────────────────────────── Platform Readers ────────────────────────────────

def _read_windows_env() -> dict:
    """
    Read live system + user environment from the Windows registry.
    This is the only reliable way to get vars added AFTER process launch.

    Reads:
      HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment  (system-wide)
      HKCU\\Environment  (current user)

    User values WIN on key collision (mirrors how Windows resolves env on login),
    except for PATH which is concatenated system + user.
    """
    import winreg

    def _read_key(hive, subkey) -> dict:
        result = {}
        try:
            key = winreg.OpenKey(hive, subkey)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    result[name] = value
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"[_read_windows_env] Registry read failed for '{subkey}': {e}")
        return result

    # System env first
    system_env = _read_key(
        winreg.HKEY_LOCAL_MACHINE,
        r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
    )
    log.debug(f"[_read_windows_env] HKLM: {len(system_env)} var(s) read")

    # User env second
    user_env = _read_key(winreg.HKEY_CURRENT_USER, r'Environment')
    log.debug(f"[_read_windows_env] HKCU: {len(user_env)} var(s) read")

    # Merge: PATH is special — concatenate system + user
    merged = dict(system_env)
    for key, value in user_env.items():
        if key.upper() == 'PATH':
            # Find whatever key name system used (could be 'Path' or 'PATH')
            existing_key = next((k for k in merged if k.upper() == 'PATH'), None)
            if existing_key:
                merged[existing_key] = merged[existing_key] + os.pathsep + value
            else:
                merged[key] = value
        else:
            merged[key] = value  # User overrides system for non-PATH vars

    log.debug(f"[_read_windows_env] Merged total: {len(merged)} var(s)")
    return merged


def _read_unix_env() -> dict:
    """
    Read environment from common Unix config files.
    On Linux/macOS there's no single authoritative registry, so we read
    the most common static sources.
    """
    env = {}

    sources = [
        '/etc/environment',         # Debian/Ubuntu global env
        '/etc/profile.d/*.sh',      # Handled specially below
    ]

    # /etc/environment — simple KEY=VALUE format
    try:
        with open('/etc/environment', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, _, v = line.partition('=')
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    env[k] = v
        log.debug(f"[_read_unix_env] /etc/environment: {len(env)} var(s)")
    except FileNotFoundError:
        log.debug("[_read_unix_env] /etc/environment not found — skipping")
    except Exception as e:
        log.warning(f"[_read_unix_env] /etc/environment read error: {e}")

    # /etc/profile.d/ shell scripts — extract export statements
    import glob
    for sh_file in glob.glob('/etc/profile.d/*.sh'):
        try:
            with open(sh_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Match: export KEY=VALUE or export KEY="VALUE"
                    if line.startswith('export ') and '=' in line:
                        rest = line[len('export '):].strip()
                        k, _, v = rest.partition('=')
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k:
                            env[k] = v
        except Exception as e:
            log.debug(f"[_read_unix_env] Skipping '{sh_file}': {e}")

    log.debug(f"[_read_unix_env] Total after profile.d: {len(env)} var(s)")
    return env


def _read_system_env() -> dict:
    """Dispatch to the correct platform reader"""
    if sys.platform == 'win32':
        return _read_windows_env()
    else:
        return _read_unix_env()


# ─────────────────────────── Core Merge Logic ────────────────────────────────

def _merge_path_string(system_path_str: str) -> int:
    """
    Merge entries from system_path_str into os.environ['PATH'].
    Deduplicates — never adds a path that's already present.

    Returns:
        int: number of new entries added
    """
    sep = os.pathsep
    current_raw = os.environ.get('PATH', '')
    current_entries = [p for p in current_raw.split(sep) if p]
    current_set = set(current_entries)

    added = 0
    for entry in system_path_str.split(sep):
        entry = entry.strip()
        if entry and entry not in current_set:
            current_entries.append(entry)
            current_set.add(entry)
            added += 1

    os.environ['PATH'] = sep.join(current_entries)
    return added


# ─────────────────────────── PathSyncer Class ────────────────────────────────

class PathSyncer:
    """
    Singleton-style class that keeps the running process's environment
    in sync with the live system environment.

    Usage:
        syncer = PathSyncer()
        syncer.start()          # initial merge + starts 20s background tick
        syncer.merge()          # manual merge (called by PythonInterpreter)
        syncer.stop()           # clean shutdown
    """

    def __init__(self):
        self._lock = threading.Lock()           # Protects os.environ writes
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._merge_count = 0
        log.info("[PathSyncer.__init__] PathSyncer ready | "
                 f"platform={sys.platform} | interval={SYNC_INTERVAL}s")

    # ── Public API ────────────────────────────────────────────────────────────

    def merge(self):
        """
        Read live system environment and merge any NEW entries into os.environ.
        - PATH entries: deduplicated append
        - All other vars: only added if the key doesn't already exist in os.environ
          (we never overwrite what the process already has)
        """
        with self._lock:
            self._merge_count += 1
            count = self._merge_count
            log.debug(f"[PathSyncer.merge] ── Merge #{count} start ──")

            try:
                system_env = _read_system_env()
            except Exception as e:
                log.error(f"[PathSyncer.merge] Failed to read system env: {e}")
                return

            added_paths  = 0
            added_vars   = 0
            skipped_vars = 0

            for key, value in system_env.items():
                if key.upper() == 'PATH':
                    # Expand any %SystemRoot%-style variables on Windows
                    if sys.platform == 'win32':
                        try:
                            value = os.path.expandvars(value)
                        except Exception:
                            pass
                    added_paths += _merge_path_string(value)

                else:
                    # Only inject vars that don't exist yet
                    if key not in os.environ:
                        try:
                            os.environ[key] = str(value)
                            added_vars += 1
                        except Exception as e:
                            log.warning(f"[PathSyncer.merge] Could not set '{key}': {e}")
                    else:
                        skipped_vars += 1

            if added_paths > 0 or added_vars > 0:
                log.info(f"[PathSyncer.merge] #{count} — "
                         f"+{added_paths} PATH entries | "
                         f"+{added_vars} new vars | "
                         f"{skipped_vars} already-present vars skipped")
            else:
                log.debug(f"[PathSyncer.merge] #{count} — nothing new "
                          f"({skipped_vars} vars already present)")

    def start(self):
        """
        Perform an initial merge immediately, then launch the background
        tick thread that re-merges every SYNC_INTERVAL seconds.
        Safe to call multiple times — will not start a second thread.
        """
        if self._thread and self._thread.is_alive():
            log.warning("[PathSyncer.start] Already running — ignoring duplicate start()")
            return

        log.info("[PathSyncer.start] Starting PathSyncer — initial merge...")
        self.merge()  # Immediate merge at launch

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._tick_loop,
            name="PathSyncerTick",
            daemon=True  # Automatically dies when the main process exits
        )
        self._thread.start()
        log.info(f"[PathSyncer.start] Tick thread launched | interval={SYNC_INTERVAL}s")

    def stop(self):
        """Gracefully stop the background tick thread."""
        log.info("[PathSyncer.stop] Stopping PathSyncer tick thread...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                log.warning("[PathSyncer.stop] Tick thread did not stop within 5s timeout")
            else:
                log.info("[PathSyncer.stop] Tick thread stopped cleanly")
        self._thread = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tick_loop(self):
        """Background thread — sleeps SYNC_INTERVAL seconds, then merges, repeat."""
        log.info(f"[PathSyncer._tick_loop] Thread running | pid={os.getpid()}")
        while not self._stop_event.wait(SYNC_INTERVAL):
            log.debug("[PathSyncer._tick_loop] Tick fired")
            try:
                self.merge()
            except Exception as e:
                log.error(f"[PathSyncer._tick_loop] Unexpected error during tick: "
                          f"{type(e).__name__}: {e}")
        log.info("[PathSyncer._tick_loop] Thread exiting")


# ─────────────────────────── Global Singleton ────────────────────────────────

_instance: PathSyncer | None = None
_instance_lock = threading.Lock()


def get_syncer() -> PathSyncer:
    """
    Return the global PathSyncer singleton.
    Creates it on first call. Thread-safe.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PathSyncer()
    return _instance
