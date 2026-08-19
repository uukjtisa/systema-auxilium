"""
systema/updater/messages.py

Developer notes carried INSIDE commit messages, surfaced in the update window.

WHY
---
A commit subject says what changed in the code. It cannot say "after you take
this one, tick the e_mailman skill too or it will break against the new
architecture" -- that is a note to the person applying the update, not a
description of the diff, and burying it in a body nobody reads means it is not
communicated at all.

So a commit may carry:

    fix(engine): Rework the provider contract

    <update_message>
    The e_mailman skill must be updated alongside this one -- tick it in the
    file list or it will fail against the new provider contract.
    </update_message>

Everything inside the tag is shown in the update window as a dismissible notice.
Several commits between the installed version and HEAD each contributing one
means several notices, stacked newest-first, because a user who is three
versions behind needs all three.

The data path already existed: gitplucker's `pending_commits()` returns
``{sha, message, date, author}`` for exactly the commit range an update would
bring, and the window was already fetching it -- it just rendered the subject
line and dropped the body.

DISMISSAL IS PER-NOTE AND PERSISTENT. Keyed by sha AND position within the
commit, so dismissing one notice never hides another -- not a different
commit's, and not a second note in the same commit. A dismissed note stays gone
across restarts; the store lives next to the rest of the updater state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from systema import APP_ROOT
from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("UpdateMessages") if _verbose else _NoOpLogger()

TAG = "update_message"

# Tolerant on purpose: a human types this by hand into a commit message, so
# accept any capitalisation, stray spaces in the tags, and an unclosed tag at
# the end of the body (which just runs to the end).
_RE = re.compile(
    r"<\s*%s\s*>(.*?)(?:<\s*/\s*%s\s*>|\Z)" % (TAG, TAG),
    re.IGNORECASE | re.DOTALL)

_STORE = APP_ROOT / "data" / "updates" / "dismissed_messages.json"


@dataclass
class UpdateNote:
    sha: str
    text: str
    subject: str = ""
    date: str = ""
    author: str = ""
    index: int = 0        # nth note within this commit

    @property
    def key(self) -> str:
        """Identity for dismissal.

        sha AND position, not sha alone: a commit may carry more than one note,
        and dismissing the first must not silently hide the second. The position
        is stable because a commit message never changes.
        """
        return f"{(self.sha or '')[:12]}#{int(self.index)}"


def extract(message: str) -> list:
    """Every update_message body in one commit message, in order."""
    out = []
    for m in _RE.finditer(str(message or "")):
        body = m.group(1).strip()
        if body:
            out.append(body)
    return out


def strip(message: str) -> str:
    """The commit message with its notes removed -- what the commit list shows.

    The note is rendered as its own notice, so leaving it in the commit body
    would print it twice.
    """
    return _RE.sub("", str(message or "")).strip()


def notes_from_commits(commits) -> list:
    """[UpdateNote, ...] for a pending_commits() payload, newest first."""
    out = []
    for c in (commits or []):
        if not isinstance(c, dict):
            continue
        msg = c.get("message", "") or ""
        subject = (msg.splitlines() or [""])[0]
        for i, body in enumerate(extract(msg)):
            out.append(UpdateNote(sha=str(c.get("sha", "") or ""), text=body,
                                  subject=subject, date=str(c.get("date", "") or ""),
                                  author=str(c.get("author", "") or ""), index=i))
    return out


# -- dismissal ---------------------------------------------------------------

def _load() -> dict:
    try:
        if _STORE.exists():
            data = json.loads(_STORE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError) as e:
        log.warning(f"[messages._load] {e}")
    return {}


def _save(data: dict) -> None:
    try:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        # temp + replace: a crash mid-write must not leave a truncated JSON file
        # that silently resets every dismissal.
        tmp = _STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_STORE)
    except OSError as e:
        log.warning(f"[messages._save] {e}")


def is_dismissed(note) -> bool:
    key = note.key if isinstance(note, UpdateNote) else str(note or "")
    return bool(_load().get(key))


def dismiss(note) -> None:
    key = note.key if isinstance(note, UpdateNote) else str(note or "")
    if not key:
        return
    data = _load()
    data[key] = True
    _save(data)
    log.info(f"[messages.dismiss] {key}")


def undismiss_all() -> None:
    _save({})


def pending(commits) -> list:
    """The notes worth showing: every one in range that is not dismissed."""
    return [n for n in notes_from_commits(commits) if not is_dismissed(n)]
