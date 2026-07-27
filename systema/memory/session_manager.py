"""
core/session_manager.py
Session Manager - Handles session save/load/delete operations
"""

import json
from systema.common.logger import _make_logger, _NoOpLogger
from datetime import datetime
from pathlib import Path
import re


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = False
log = _make_logger("SessionManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
# APP_ROOT is defined once in src/__init__.py (parent of the package dir).
from systema import APP_ROOT as _APP_ROOT
# ─────────────────────────────────────────────────────────────────────────────


class SessionManager:
    """Manages chat sessions - save, load, list, delete, rename"""

    def __init__(self):
        log.info("[SessionManager.__init__] Initializing SessionManager")
        # Use absolute path anchored to app root — safe even after os.chdir()
        self.sessions_dir = _APP_ROOT / "data" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.active_session_id = None
        self.session_metadata = {}  # Cache for session names/dates
        # list_sessions() metadata cache: filename -> ((mtime_ns, size), meta).
        # Lets a sidebar refresh skip re-parsing unchanged session files.
        self._list_cache = {}
        log.info(f"[SessionManager.__init__] Sessions directory: '{self.sessions_dir}' | "
                 f"active_session_id=None | metadata cache empty")

    def create_session(self):
        """Create a new session with timestamp-based ID"""
        log.info("[SessionManager.create_session] Creating new session")
        now = datetime.now()
        # Format: 2_15_2026_15_07_22_45
        session_id = now.strftime("%m_%d_%Y_%H_%M_%S_%f")[:-4]  # Remove last 2 digits of microseconds

        creation_time = now.strftime("%B %d, %Y - %I:%M%p").replace(" 0", " ")  # Remove leading zero from hour
        log.debug(f"[SessionManager.create_session] Generated session_id: '{session_id}' | "
                  f"creation_time: '{creation_time}'")

        session_data = {
            "session_name": "New Session",
            "creation_time_and_date": creation_time,
            "id": session_id,
            "chat_history": []
        }

        # Don't save empty session yet - will be saved when first message is sent
        self.session_metadata[session_id] = {
            "name": "New Session",
            "date": creation_time,
            "id": session_id
        }
        log.debug(f"[SessionManager.create_session] Metadata cached for new session — "
                  f"NOT persisted to disk yet (waiting for first message)")
        log.info(f"[SessionManager.create_session] Session created in memory: id='{session_id}'")
        return session_id

    def save_session(self, session_id, chat_history, session_name=None,
                     next_image_n=None):
        """Save session to JSON file.

        `next_image_n` is the session's image counter. It is stored ALONGSIDE
        the history rather than recomputed from it on load, because image
        numbers must never be reused — deleting the highest-numbered image
        would otherwise recycle its number on the next attachment. Optional and
        additive: sessions written before it existed load fine and simply
        derive a floor from their history.
        """
        log.info(f"[SessionManager.save_session] Saving session id='{session_id}' | "
                 f"history_len={len(chat_history)} | name_override={repr(session_name)}")

        if not session_id:
            log.warning("[SessionManager.save_session] No session_id provided — aborting save")
            return False

        # Get metadata
        metadata = self.session_metadata.get(session_id, {})
        log.debug(f"[SessionManager.save_session] Metadata from cache: {metadata}")

        # Use provided name or cached name
        if session_name:
            metadata['name'] = session_name
            log.debug(f"[SessionManager.save_session] Name overridden to: '{session_name}'")

        session_data = {
            "session_name": metadata.get('name', 'New Session'),
            "creation_time_and_date": metadata.get('date', ''),
            "id": session_id,
            "chat_history": chat_history
        }
        if next_image_n is not None:
            try:
                session_data["next_image_n"] = int(next_image_n)
            except (TypeError, ValueError):
                pass

        # Get current filename
        session_file = self._get_session_file(session_id)
        log.debug(f"[SessionManager.save_session] Target file: '{session_file}'")

        try:
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            log.info(f"[SessionManager.save_session] ✓ Saved successfully → '{session_file.name}' "
                     f"| name='{session_data['session_name']}'")
            return True
        except Exception as e:
            log.error(f"[SessionManager.save_session] ✗ Failed to write file '{session_file}': "
                      f"{type(e).__name__}: {e}")
            return False

    def load_session(self, session_id):
        """Load session from JSON file"""
        log.info(f"[SessionManager.load_session] Loading session id='{session_id}'")
        session_file = self._get_session_file(session_id)
        log.debug(f"[SessionManager.load_session] Resolved file path: '{session_file}'")

        if not session_file.exists():
            log.warning(f"[SessionManager.load_session] File not found: '{session_file}' — returning None")
            return None

        log.debug(f"[SessionManager.load_session] File exists — reading JSON")
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)

            history_len = len(session_data.get('chat_history', []))
            log.debug(f"[SessionManager.load_session] JSON parsed — "
                      f"session_name='{session_data.get('session_name')}' | "
                      f"chat_history entries={history_len}")

            # Update metadata cache
            self.session_metadata[session_id] = {
                "name": session_data.get('session_name', 'New Session'),
                "date": session_data.get('creation_time_and_date', ''),
                "id": session_id
            }
            log.debug("[SessionManager.load_session] Metadata cache updated")
            log.info(f"[SessionManager.load_session] ✓ Loaded: name='{session_data.get('session_name')}' | "
                     f"history={history_len} entries")
            return session_data
        except Exception as e:
            log.error(f"[SessionManager.load_session] ✗ Failed to read/parse '{session_file}': "
                      f"{type(e).__name__}: {e}")
            return None

    def list_sessions(self):
        """List all sessions, sorted by date (newest first).

        Metadata is cached per file keyed by (mtime_ns, size): only new or
        changed files are re-parsed. Session files hold whole chat histories
        (MBs), and this runs on the GUI thread on every sidebar refresh /
        auto-save — the full-directory parse was the top-ranked UI hitch."""
        log.info("[SessionManager.list_sessions] Scanning sessions directory")
        sessions = []
        seen = set()

        for file_path in self.sessions_dir.glob("*.json"):
            fname = file_path.name
            try:
                st = file_path.stat()
                key = (st.st_mtime_ns, st.st_size)
                seen.add(fname)
                cached = self._list_cache.get(fname)
                if cached and cached[0] == key:
                    sessions.append(dict(cached[1]))
                    continue

                with open(file_path, 'r', encoding='utf-8') as f:
                    session_data = json.load(f)

                meta = {
                    'id': session_data.get('id', file_path.stem),
                    'name': session_data.get('session_name', 'Unnamed'),
                    'date': session_data.get('creation_time_and_date', ''),
                    'file': fname
                }
                self._list_cache[fname] = (key, meta)
                sessions.append(dict(meta))
                log.debug(f"[SessionManager.list_sessions] Parsed: id='{meta['id']}' | "
                          f"name='{meta['name']}'")
            except Exception as e:
                log.warning(f"[SessionManager.list_sessions] Skipping unreadable file '{fname}': "
                            f"{type(e).__name__}: {e}")

        # Forget cache entries for files deleted from the directory.
        for gone in set(self._list_cache) - seen:
            del self._list_cache[gone]

        # Sort by ID (which is timestamp-based) - newest first
        sessions.sort(key=lambda x: x['id'], reverse=True)
        log.info(f"[SessionManager.list_sessions] Found {len(sessions)} session(s) — sorted newest first")
        return sessions

    def delete_session(self, session_id):
        """Delete a session file"""
        log.info(f"[SessionManager.delete_session] Deleting session id='{session_id}'")
        session_file = self._get_session_file(session_id)
        log.debug(f"[SessionManager.delete_session] Target file: '{session_file}'")

        if session_file.exists():
            log.debug(f"[SessionManager.delete_session] File confirmed — proceeding with unlink")
            try:
                session_file.unlink()
                if session_id in self.session_metadata:
                    del self.session_metadata[session_id]
                    log.debug("[SessionManager.delete_session] Metadata cache entry removed")
                log.info(f"[SessionManager.delete_session] ✓ Deleted '{session_file.name}'")
                return True
            except Exception as e:
                log.error(f"[SessionManager.delete_session] ✗ Failed to delete '{session_file}': "
                          f"{type(e).__name__}: {e}")
                return False

        log.warning(f"[SessionManager.delete_session] File not found — nothing to delete for id='{session_id}'")
        return False

    def rename_session(self, session_id, new_name):
        """Rename a session (updates filename and JSON)"""
        log.info(f"[SessionManager.rename_session] Renaming session id='{session_id}' → '{new_name}'")
        old_file = self._get_session_file(session_id)
        log.debug(f"[SessionManager.rename_session] Current file: '{old_file}'")

        if not old_file.exists():
            log.warning(f"[SessionManager.rename_session] File not found: '{old_file}' — aborting rename")
            return False

        # Sanitize name for filename
        clean_name = self._sanitize_filename(new_name)
        log.debug(f"[SessionManager.rename_session] Sanitized filename component: '{clean_name}'")

        # Create new filename: Name_2_15_2026_15_07_22_45.json
        new_filename = f"{clean_name}_{session_id}.json"
        new_file = self.sessions_dir / new_filename
        log.debug(f"[SessionManager.rename_session] New target file: '{new_file}'")

        try:
            # Load current data
            log.debug("[SessionManager.rename_session] Loading existing session data")
            with open(old_file, 'r', encoding='utf-8') as f:
                session_data = json.load(f)

            # Update name in data
            session_data['session_name'] = new_name
            log.debug(f"[SessionManager.rename_session] session_name updated in data object")

            # Save to new filename
            with open(new_file, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            log.debug(f"[SessionManager.rename_session] Written to new file: '{new_file.name}'")

            # Delete old file if different
            if old_file != new_file:
                old_file.unlink()
                log.debug(f"[SessionManager.rename_session] Old file removed: '{old_file.name}'")

            # Update metadata cache
            self.session_metadata[session_id] = {
                "name": new_name,
                "date": session_data.get('creation_time_and_date', ''),
                "id": session_id
            }
            log.debug("[SessionManager.rename_session] Metadata cache updated with new name")
            log.info(f"[SessionManager.rename_session] ✓ Renamed → '{new_file.name}'")
            return True
        except Exception as e:
            log.error(f"[SessionManager.rename_session] ✗ Failed: {type(e).__name__}: {e}")
            return False

    def _get_session_file(self, session_id):
        """Get the file path for a session ID"""
        log.debug(f"[SessionManager._get_session_file] Resolving path for id='{session_id}'")

        # Check if file exists with prefix (renamed)
        for file_path in self.sessions_dir.glob(f"*{session_id}.json"):
            log.debug(f"[SessionManager._get_session_file] Matched existing file: '{file_path.name}'")
            return file_path

        # Default filename (not renamed yet)
        default = self.sessions_dir / f"{session_id}.json"
        log.debug(f"[SessionManager._get_session_file] No match found — defaulting to: '{default.name}'")
        return default

    def _sanitize_filename(self, name):
        """Sanitize session name for use in filename"""
        log.debug(f"[SessionManager._sanitize_filename] Input: '{name}'")
        # Remove or replace invalid characters
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        # Replace spaces with underscores
        name = name.replace(' ', '_')
        # Limit length
        name = name[:50]
        # Remove leading/trailing underscores
        name = name.strip('_')
        result = name if name else "Session"
        log.debug(f"[SessionManager._sanitize_filename] Result: '{result}'")
        return result

    def get_session_name(self, session_id):
        """Get the name of a session"""
        log.debug(f"[SessionManager.get_session_name] Fetching name for id='{session_id}'")

        if session_id in self.session_metadata:
            name = self.session_metadata[session_id].get('name', 'New Session')
            log.debug(f"[SessionManager.get_session_name] Cache hit → '{name}'")
            return name

        log.debug("[SessionManager.get_session_name] Cache miss — loading from file")
        # Load from file
        session_data = self.load_session(session_id)
        if session_data:
            name = session_data.get('session_name', 'New Session')
            log.debug(f"[SessionManager.get_session_name] Loaded from file → '{name}'")
            return name

        log.warning(f"[SessionManager.get_session_name] Session '{session_id}' not found anywhere — "
                    f"returning default 'New Session'")
        return 'New Session'

    def get_session_date(self, session_id):
        """Get the creation date of a session"""
        log.debug(f"[SessionManager.get_session_date] Fetching date for id='{session_id}'")

        if session_id in self.session_metadata:
            date = self.session_metadata[session_id].get('date', '')
            log.debug(f"[SessionManager.get_session_date] Cache hit → '{date}'")
            return date

        log.debug("[SessionManager.get_session_date] Cache miss — loading from file")
        # Load from file
        session_data = self.load_session(session_id)
        if session_data:
            date = session_data.get('creation_time_and_date', '')
            log.debug(f"[SessionManager.get_session_date] Loaded from file → '{date}'")
            return date

        log.warning(f"[SessionManager.get_session_date] Session '{session_id}' not found — returning ''")
        return ''