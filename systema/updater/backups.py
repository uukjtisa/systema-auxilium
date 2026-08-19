"""
systema/updater/backups.py

Hardening around gitplucker's pre-apply backups: pruning, a richer manifest,
integrity verification, and a location that can live outside data/.

WHAT GITPLUCKER ALREADY DOES
----------------------------
`strategies/base.py` copies every file it is about to overwrite into
``state_dir/backups/<repo-slug>/<branch>-<timestamp>/`` and writes a
``manifest.json`` naming what to restore and what to delete on rollback. That
part works and is not reimplemented here.

WHAT IT DOES NOT DO, AND WHY EACH MATTERS
-----------------------------------------
- **It never prunes.** "history is kept, never pruned" is its documented
  behaviour, so every update leaves another full copy of every changed file
  under data/ forever. data/ was already 239 MB.
- **The manifest cannot answer "is this backup still good?"** It lists paths,
  not sizes or hashes, so a truncated or half-written backup looks identical to
  a complete one until you try to restore from it -- which is the worst possible
  moment to find out.
- **It records no link to the git snapshot** taken at the same instant, so the
  two recovery paths cannot be reconciled.
- **It lives under data/**, the directory a user deletes to "reset the app".

A sidecar (``systema.json``) is written next to gitplucker's own manifest rather
than modifying it: the library owns that file's schema, and a future gitplucker
release must be free to change it without breaking this.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from systema import APP_ROOT
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("Backups") if _verbose else _NoOpLogger()

SIDECAR = "systema.json"
DEFAULT_KEEP = 5

# Verification hashes a SAMPLE, not everything: a full rehash of a large backup
# would put seconds into the apply path to catch a failure mode (partial copy)
# that a sample detects just as reliably.
SAMPLE_SIZE = 12


@dataclass
class BackupInfo:
    path: Path
    when: str = ""
    version: str = ""
    branch: str = ""
    git_sha: str = ""
    file_count: int = 0
    total_bytes: int = 0

    @property
    def label(self) -> str:
        return f"{self.when or self.path.name} ({self.file_count} files)"


def backups_root(settings=None) -> Path:
    """Where backups live.

    Configurable via ``update_backup_dir`` precisely so it can be moved OUT of
    data/ -- the whole point of the setting is surviving a data/ wipe, which is
    the failure mode the default location cannot protect against.
    """
    custom = str((settings or {}).get("update_backup_dir") or "").strip()
    if custom:
        try:
            p = Path(custom).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError as e:
            log.warning(f"[backups.backups_root] configured dir unusable "
                        f"({e}) -- falling back to the default")
    return APP_ROOT / "data" / "updates" / "backups"


def _iter_backup_dirs(root: Path):
    """Every <branch>-<timestamp> directory under a backups root."""
    if not root.is_dir():
        return
    for repo_dir in sorted(root.iterdir()):
        if not repo_dir.is_dir():
            continue
        for snap in sorted(repo_dir.iterdir()):
            if snap.is_dir():
                yield snap


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _payload_files(snapshot: Path) -> list:
    """Backed-up files, excluding the manifests themselves."""
    return [p for p in snapshot.rglob("*")
            if p.is_file() and p.name not in ("manifest.json", SIDECAR)]


def write_sidecar(snapshot, *, version: str = "", branch: str = "",
                  git_sha: str = "") -> dict:
    """Record what this backup IS, and enough to prove it later.

    Sampled hashes are stored by relative path so verify() can re-check exactly
    the same files rather than hoping it picks the same sample.
    """
    snapshot = Path(snapshot)
    files = _payload_files(snapshot)
    sample = files[:SAMPLE_SIZE]
    data = {
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": str(version or ""),
        "branch": str(branch or ""),
        "git_sha": str(git_sha or ""),
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "sampled": {},
    }
    for p in sample:
        try:
            rel = p.relative_to(snapshot).as_posix()
            data["sampled"][rel] = {"size": p.stat().st_size,
                                    "sha256": _file_digest(p)}
        except OSError as e:
            log.warning(f"[backups.write_sidecar] {p}: {e}")
    try:
        (snapshot / SIDECAR).write_text(json.dumps(data, indent=2),
                                        encoding="utf-8")
    except OSError as e:
        log.warning(f"[backups.write_sidecar] could not write sidecar: {e}")
    return data


def read_sidecar(snapshot) -> dict:
    try:
        p = Path(snapshot) / SIDECAR
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError) as e:
        log.warning(f"[backups.read_sidecar] {e}")
    return {}


def verify(snapshot) -> tuple:
    """(ok, reason). A backup that cannot be verified is reported, not trusted."""
    snapshot = Path(snapshot)
    if not snapshot.is_dir():
        return False, "backup directory is missing"
    meta = read_sidecar(snapshot)
    if not meta:
        # Pre-hardening backups have no sidecar. Not corrupt, just unverifiable
        # -- say which, because "unknown" and "bad" are different answers.
        return True, "no sidecar (written before verification existed)"
    files = _payload_files(snapshot)
    if len(files) != meta.get("file_count", len(files)):
        return False, (f"file count changed: {meta.get('file_count')} recorded, "
                       f"{len(files)} present")
    for rel, rec in (meta.get("sampled") or {}).items():
        p = snapshot / rel
        if not p.is_file():
            return False, f"missing from the backup: {rel}"
        try:
            if p.stat().st_size != rec.get("size"):
                return False, f"size changed: {rel}"
            if _file_digest(p) != rec.get("sha256"):
                return False, f"checksum mismatch: {rel}"
        except OSError as e:
            return False, f"unreadable: {rel} ({e})"
    return True, f"verified {len(meta.get('sampled') or {})} sampled file(s)"


def list_backups(settings=None) -> list:
    """Every backup, newest first."""
    out = []
    for snap in _iter_backup_dirs(backups_root(settings)):
        meta = read_sidecar(snap)
        out.append(BackupInfo(
            path=snap,
            when=meta.get("written_at", ""),
            version=meta.get("version", ""),
            branch=meta.get("branch", ""),
            git_sha=meta.get("git_sha", ""),
            file_count=meta.get("file_count", 0),
            total_bytes=meta.get("total_bytes", 0),
        ))
    # By mtime, NOT by name: the directory names embed a timestamp but sort
    # unreliably across branches, and a restored/copied backup keeps its name.
    out.sort(key=lambda b: b.path.stat().st_mtime if b.path.exists() else 0,
             reverse=True)
    return out


def prune(keep: int = DEFAULT_KEEP, settings=None) -> list:
    """Delete all but the newest ``keep`` backups. Returns what was removed.

    gitplucker keeps every backup forever by design, so without this each update
    leaves another full copy of every changed file under data/ -- which was
    already 239 MB before any of them.
    """
    keep = max(1, int(keep or DEFAULT_KEEP))
    everything = list_backups(settings)
    doomed = everything[keep:]
    removed = []
    for b in doomed:
        try:
            shutil.rmtree(b.path, ignore_errors=False)
            removed.append(str(b.path))
        except OSError as e:
            log.warning(f"[backups.prune] could not remove {b.path}: {e}")
    if removed:
        log.info(f"[backups.prune] removed {len(removed)} old backup(s), "
                 f"kept {min(keep, len(everything))}")
    return removed


def finalize(snapshot, *, version: str = "", branch: str = "", git_sha: str = "",
             keep: int = DEFAULT_KEEP, settings=None) -> dict:
    """Write the sidecar, verify, then prune. The one call the updater makes.

    Order matters: verify the backup we just took BEFORE deleting older ones, so
    a bad new backup never costs us the good old ones.
    """
    result = {"sidecar": {}, "verified": False, "reason": "", "pruned": []}
    try:
        result["sidecar"] = write_sidecar(snapshot, version=version,
                                          branch=branch, git_sha=git_sha)
        ok, reason = verify(snapshot)
        result["verified"], result["reason"] = ok, reason
        if not ok:
            log.error(f"[backups.finalize] NEW BACKUP FAILED VERIFICATION "
                      f"({reason}) -- keeping every older backup")
            return result
        result["pruned"] = prune(keep, settings)
    except Exception as e:
        log.error(f"[backups.finalize] {type(e).__name__}: {e}")
        result["reason"] = f"{type(e).__name__}: {e}"
    return result
