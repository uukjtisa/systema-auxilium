"""Every app-owned directory must be self-healing.

Idea "empty_folder_cleanup_local_and_origin": a fresh clone, a wiped data/
folder, or a future cleanup pass must never leave the app writing into a
directory that is not there. Any module that derives a path under
APP_ROOT/data has to create it with `mkdir(parents=True, exist_ok=True)`
before writing — READ-only consumers are exempt, but each one is listed here
with the reason it degrades safely, so a new unguarded writer cannot slip in
unnoticed.

Audited 2026-08-18: zero unguarded writers. This test keeps it that way.
"""

import re
from pathlib import Path

SYSTEMA = Path(__file__).resolve().parents[3] / "systema"

# Derives a data/ path but never CREATES anything — each degrades to empty.
READ_ONLY_CONSUMERS = {
    # sessions_dir.glob(): pathlib yields nothing for a missing directory, and
    # the caller already logs + returns [] when no file matches.
    "engine/ai_engine.py",
    # _logs_dir().glob() is wrapped in try/except -> others = [].
    "ui/windows/logs_window.py",
}

_DATA_PATH = re.compile(r'APP_ROOT\s*/\s*["\']data["\']')
_GUARD = re.compile(r'mkdir\(parents=True,\s*exist_ok=True\)|makedirs\([^)]*exist_ok=True')


def test_every_data_dir_owner_creates_its_directory():
    offenders = []
    for py in SYSTEMA.rglob("*.py"):
        rel = py.relative_to(SYSTEMA).as_posix()
        src = py.read_text(encoding="utf-8", errors="replace")
        if not _DATA_PATH.search(src):
            continue
        if rel in READ_ONLY_CONSUMERS:
            continue
        if not _GUARD.search(src):
            offenders.append(rel)

    assert not offenders, (
        "these modules build a path under data/ but never create the directory "
        "-- add mkdir(parents=True, exist_ok=True), or list the file in "
        f"READ_ONLY_CONSUMERS with the reason it is safe: {offenders}"
    )


def test_read_only_allowlist_stays_honest():
    """A file that starts creating directories must leave the allowlist."""
    for rel in READ_ONLY_CONSUMERS:
        src = (SYSTEMA / rel).read_text(encoding="utf-8", errors="replace")
        assert not _GUARD.search(src), (
            f"{rel} now creates directories -- drop it from READ_ONLY_CONSUMERS"
        )
