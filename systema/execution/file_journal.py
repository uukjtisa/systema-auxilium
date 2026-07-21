"""
systema/execution/file_journal.py

Reverse file-state journal for the surgical file tools (read/edit/write).

Before every mutation the CURRENT file bytes (the pre-image) are copied into
``data/cache/file_history/objects/<id>`` and one JSON line is appended to
``data/cache/file_history/manifest.jsonl`` (consolidated cache layout
2026-07-21; the old ``data/file_history`` auto-migrates in on first use, so
every old session's revert entries survive):

    {"id", "ts", "session_id", "tool", "path", "existed", "pre_hash", "size"}

Restoring an entry copies the pre-image back (or DELETES the file when the
entry recorded a brand-new file, ``existed: false``). Pure stdlib, cross-OS,
thread-safe appends. An optional git "shadow" mirror gives deep history when
git is installed; the journal remains the only restore path.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from systema import APP_ROOT
from systema.common.logger import _make_logger

log = _make_logger("FileJournal")

HISTORY_DIR = APP_ROOT / "data" / "cache" / "file_history"
OBJECTS_DIR = HISTORY_DIR / "objects"
MANIFEST = HISTORY_DIR / "manifest.jsonl"
SHADOW_DIR = HISTORY_DIR / "shadow"

# Migrate the pre-consolidation data/file_history in AT IMPORT — readers
# (the history dialog's entries()) never call _ensure_dirs, and journal ids
# are dir-relative, so after the move every old session's entries restore.
from systema.common.data_paths import migrate_legacy as _migrate_legacy
_migrate_legacy(APP_ROOT / "data" / "file_history", HISTORY_DIR)

_lock = threading.Lock()


def _ensure_dirs():
    OBJECTS_DIR.mkdir(parents=True, exist_ok=True)


def record(path, tool, session_id=None):
    """Snapshot the pre-image of ``path`` before a mutation. Returns the journal
    id, or None when journaling failed (a journal failure never blocks the op)."""
    try:
        _ensure_dirs()
        p = Path(path)
        existed = p.is_file()
        data = p.read_bytes() if existed else b""
        jid = f"{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"
        if existed:
            (OBJECTS_DIR / jid).write_bytes(data)
        entry = {
            "id": jid,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "session_id": str(session_id or ""),
            "tool": str(tool),
            "path": str(p.resolve() if existed else p),
            "existed": existed,
            "pre_hash": hashlib.sha1(data).hexdigest() if existed else "",
            "size": len(data),
        }
        with _lock:
            with open(MANIFEST, "a", encoding="utf-8", newline="") as f:
                f.write(json.dumps(entry) + "\n")
        log.info(f"[FileJournal.record] {tool} pre-image saved | id={jid} | "
                 f"path={entry['path']} | existed={existed}")
        return jid
    except Exception as e:
        log.error(f"[FileJournal.record] journaling failed (op continues): {e}")
        return None


def entries(limit=500):
    """Newest-first manifest entries."""
    try:
        if not MANIFEST.is_file():
            return []
        out = []
        with open(MANIFEST, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
        out.reverse()
        return out[:limit]
    except Exception as e:
        log.error(f"[FileJournal.entries] {e}")
        return []


def get(jid):
    for e in entries(limit=100000):
        if e.get("id") == jid:
            return e
    return None


def pre_image_text(jid):
    """The stored pre-image as text ('' for a new-file entry)."""
    obj = OBJECTS_DIR / jid
    if obj.is_file():
        return obj.read_bytes().decode("utf-8", errors="replace")
    return ""


def restore(jid):
    """Put the file back to its pre-image state. Returns (ok, message)."""
    e = get(jid)
    if e is None:
        return False, f"journal entry {jid} not found"
    target = Path(e["path"])
    try:
        if e.get("existed"):
            obj = OBJECTS_DIR / jid
            if not obj.is_file():
                return False, f"pre-image object missing for {jid}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(obj, target)
            log.info(f"[FileJournal.restore] restored {target} from {jid}")
            return True, f"Restored {target}"
        # The entry recorded a file that did NOT exist -> revert = delete.
        if target.is_file():
            target.unlink()
            log.info(f"[FileJournal.restore] deleted {target} (was created by {jid})")
            return True, f"Deleted {target} (it did not exist before)"
        return True, f"{target} already absent"
    except Exception as ex:
        return False, f"restore failed: {ex}"


def prune(keep_days=14):
    """Drop manifest entries + objects older than the cutoff. Returns count."""
    try:
        if not MANIFEST.is_file():
            return 0
        cutoff = time.time() - keep_days * 86400
        kept, dropped = [], []
        for e in entries(limit=100000):
            try:
                ts = time.mktime(time.strptime(e["ts"], "%Y-%m-%d %H:%M:%S"))
            except Exception:
                ts = 0
            (kept if ts >= cutoff else dropped).append(e)
        if not dropped:
            return 0
        kept.reverse()  # back to oldest-first file order
        with _lock:
            with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
                for e in kept:
                    f.write(json.dumps(e) + "\n")
        for e in dropped:
            try:
                (OBJECTS_DIR / e["id"]).unlink(missing_ok=True)
            except Exception:
                pass
        log.info(f"[FileJournal.prune] pruned {len(dropped)} entries older than "
                 f"{keep_days} days")
        return len(dropped)
    except Exception as e:
        log.error(f"[FileJournal.prune] {e}")
        return 0


# ── Persistent revert-state (Files-touched dialog cursors) ───────────────────
# The dialog's per-file "steps back" cursor + the stashed live bytes survive a
# dialog close AND an app restart. State lives beside the manifest:
#   revert_state.json = {session_id: {path: {"cursor", "stash_id", "stash_existed"}}}
# Stash objects use an "rs-" id prefix inside objects/ — prune() never touches
# them (it only unlinks ids listed in dropped manifest entries).

REVERT_STATE = HISTORY_DIR / "revert_state.json"
_RS_PREFIX = "rs-"


def _load_revert_state():
    try:
        if REVERT_STATE.is_file():
            with open(REVERT_STATE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
    except Exception as e:
        log.error(f"[FileJournal._load_revert_state] {e}")
    return {}


def _save_revert_state(d):
    try:
        _ensure_dirs()
        with _lock:
            with open(REVERT_STATE, "w", encoding="utf-8", newline="") as f:
                json.dump(d, f, indent=1)
    except Exception as e:
        log.error(f"[FileJournal._save_revert_state] {e}")


def revert_state(session_id):
    """{path: {"cursor": int, "stash_id": …, "stash_existed": bool}} for a session."""
    return _load_revert_state().get(str(session_id or ""), {})


def stash_current(session_id, path):
    """Snapshot the LIVE bytes of ``path`` (called before the first step back)
    into an rs- object so stepping forward can reach 'current' again after a
    restart. Replaces any previous stash for the path. Returns True on success."""
    sid = str(session_id or "")
    if not sid:
        return False
    try:
        _ensure_dirs()
        p = Path(path)
        existed = p.is_file()
        rs_id = f"{_RS_PREFIX}{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"
        if existed:
            (OBJECTS_DIR / rs_id).write_bytes(p.read_bytes())
        d = _load_revert_state()
        ent = d.setdefault(sid, {}).setdefault(str(path), {})
        old = ent.get("stash_id")
        if old:
            try:
                (OBJECTS_DIR / old).unlink(missing_ok=True)
            except Exception:
                pass
        ent.update({"stash_id": rs_id if existed else None,
                    "stash_existed": existed})
        _save_revert_state(d)
        return True
    except Exception as e:
        log.error(f"[FileJournal.stash_current] {e}")
        return False


def stash_read(session_id, path):
    """(found, bytes_or_None) — the stashed 'current' bytes for a reverted file.
    found=True with None bytes means the current state was 'file absent'."""
    ent = revert_state(session_id).get(str(path))
    if not ent or "stash_existed" not in ent:
        return False, None
    if not ent.get("stash_existed"):
        return True, None
    obj = OBJECTS_DIR / (ent.get("stash_id") or "")
    if obj.is_file():
        return True, obj.read_bytes()
    return False, None


def set_revert_cursor(session_id, path, cursor):
    """Persist a file's steps-back cursor. cursor <= 0 means the file is back at
    'current': the entry AND its stash object are dropped."""
    sid = str(session_id or "")
    if not sid:
        return
    d = _load_revert_state()
    sess = d.setdefault(sid, {})
    key = str(path)
    if cursor <= 0:
        ent = sess.pop(key, None) or {}
        rs_id = ent.get("stash_id")
        if rs_id:
            try:
                (OBJECTS_DIR / rs_id).unlink(missing_ok=True)
            except Exception:
                pass
        if not sess:
            d.pop(sid, None)
    else:
        sess.setdefault(key, {})["cursor"] = int(cursor)
    _save_revert_state(d)


# ── Optional git shadow (deep history; never the restore path) ───────────────

def _git_ok():
    return shutil.which("git") is not None


def _shadow_rel(path):
    """Encode an absolute path into a shadow-repo-relative path
    (``D:\\proj\\a.py`` -> ``D/proj/a.py``)."""
    p = Path(path).resolve()
    parts = list(p.parts)
    if parts and parts[0].endswith(("\\", "/", ":")):        # drive / root
        parts[0] = parts[0].rstrip(":\\/") or "root"
    return Path(*parts)


def shadow_commit(path, tool):
    """Mirror the file's CURRENT (post-change) content into the shadow repo and
    commit. Silently a no-op when git is unavailable or anything fails."""
    try:
        if not _git_ok():
            return False
        src = Path(path)
        if not src.is_file():
            return False
        SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        if not (SHADOW_DIR / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=SHADOW_DIR, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            subprocess.run(["git", "config", "user.name", "Systema Auxilium"],
                           cwd=SHADOW_DIR, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            subprocess.run(["git", "config", "user.email", "systema@local"],
                           cwd=SHADOW_DIR, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dst = SHADOW_DIR / _shadow_rel(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        rel = dst.relative_to(SHADOW_DIR).as_posix()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(["git", "add", "--", rel], cwd=SHADOW_DIR, check=True,
                       creationflags=flags)
        subprocess.run(["git", "commit", "-q", "-m", f"{tool}: {rel}"],
                       cwd=SHADOW_DIR, creationflags=flags)
        return True
    except Exception as e:
        log.debug(f"[FileJournal.shadow_commit] skipped: {e}")
        return False
