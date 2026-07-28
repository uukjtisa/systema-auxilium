"""
systema/common/run_context.py — facts about THIS run of the app.

The session logger owns the log file, but it is created inside
`main.py._setup_session_logger()` and never published, so nothing downstream
could name it. Two things suffered:

  * `crash_watcher` had to GUESS which file the run was writing to, by picking
    the newest `*.txt` in `data/logs`.
  * Session history had no way to record which log a message was written during,
    which is exactly what you need when a card comes out wrong and you are
    trying to find the moment it happened.

Both read from here now.

Stdlib only, and it imports nothing from the package — `main.py` sets this up
BEFORE the heavy imports, for the same reason the logger it describes is
installed first.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

_log_path: Path | None = None
_pid: int = os.getpid()

#: Key each history entry carries its provenance under.
STAMP_KEY = '_run'


def set_log_path(path) -> None:
    """Publish this run's log file. Called once, from `_setup_session_logger`."""
    global _log_path
    _log_path = Path(path) if path else None


def log_path() -> Path | None:
    """Full path to this run's log file, or None before the logger is up."""
    return _log_path


def log_name() -> str:
    """Basename of this run's log file ('' before the logger is up).

    Basename, never the full path: a session file is portable between the
    working dir and the repo copy, and an absolute path recorded on one machine
    is noise at best and actively misleading at worst.
    """
    return _log_path.name if _log_path else ""


def pid() -> int:
    return _pid


def stamp() -> dict:
    """The provenance blob written onto a history entry.

    `at` deliberately matches the log's own line prefix (`03:39:28.683 │ DEBUG
    │ …`) so the value can be pasted straight into a search of the named file.
    The log FILENAME carries the date, so time-of-day alone is unambiguous.
    """
    return {
        'log': log_name(),
        'at': datetime.now().strftime('%H:%M:%S.%f')[:-3],
        'pid': _pid,
    }


class StampedHistory(list):
    """`conversation_history` that records which log file each entry was written
    during, at the moment it is appended.

    A list subclass rather than edits at the call sites: entries are appended
    from 28 places across `ai_engine`, `event_cards`, `controller` and
    `task_manager`, and a 29th added later would silently lose its provenance.
    Overriding `append` is the one place that cannot be forgotten.

    Constructing from an existing history never re-stamps it — `list.__init__`
    does not route through `append` — so re-wrapping on reassignment is safe.
    """

    def __getitem__(self, item):
        """Keep the type across slicing.

        `history[:n]` on a plain list subclass returns a bare `list`, and the
        rewind/truncate paths in `ui/chat/bubbles.py` assign such a slice back
        over `conversation_history`. Without this the type silently degrades on
        the first rewind and every later append loses its stamp — the kind of
        failure that leaves no trace until you need the data.
        """
        result = super().__getitem__(item)
        return StampedHistory(result) if isinstance(item, slice) else result

    def append(self, entry):
        if isinstance(entry, dict) and STAMP_KEY not in entry:
            entry[STAMP_KEY] = stamp()
        super().append(entry)

    def append_loaded(self, entry):
        """Replay an entry from disk WITHOUT stamping it.

        An entry written during an earlier run belongs to that run's log, and
        stamping it with today's would be a confident lie about where to look.
        Entries written before this feature existed simply carry no stamp, which
        is honest and reads correctly in the Logs window.
        """
        super().append(entry)


def logs_in(history) -> list[dict]:
    """Which log files a session spans, newest first.

    Returns `[{'log': name, 'entries': n, 'pids': [...]}, ...]`. This is the
    multi-log support: the app restarts often, so a single session routinely has
    entries written across several files, and a lone "current log" pointer would
    be wrong for everything before the last restart.
    """
    seen: dict[str, dict] = {}
    for entry in (history or []):
        if not isinstance(entry, dict):
            continue
        run = entry.get(STAMP_KEY)
        if not isinstance(run, dict):
            continue
        name = run.get('log') or ''
        if not name:
            continue
        rec = seen.setdefault(name, {'log': name, 'entries': 0, 'pids': []})
        rec['entries'] += 1
        _p = run.get('pid')
        if _p is not None and _p not in rec['pids']:
            rec['pids'].append(_p)
    # Ordered by FIRST APPEARANCE in the history, then reversed for newest-first.
    # Deliberately not a sort on the filename: those read
    # `log_2026_jul_28_tuesday_h03_m38_s16_ms53_am.txt`, where the month is a
    # NAME ("aug" sorts before "jul") and the am/pm marker trails the digits, so
    # lexicographic order is not chronological order. The history list is append
    # -ordered, which already is.
    return list(reversed(list(seen.values())))
