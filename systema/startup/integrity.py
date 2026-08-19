"""
systema/startup/integrity.py

Is this working copy actually runnable? Answered BEFORE AssistantController is
built, so a half-applied update is caught at the door instead of crashing the
app into a stack trace the user cannot act on.

Pure stdlib -- no PyQt, no systema imports beyond APP_ROOT. It has to work on a
copy where the UI layer itself may be the broken part, and it has to be
testable headlessly.

FAST BY CONSTRUCTION. This runs in front of the first window, so it never walks
the tree: it compiles a short, fixed list of load-bearing modules. Compiling all
~430 source files would put seconds in front of startup to catch a case that is
already covered -- if one of these files is broken the app cannot start anyway,
and if some leaf module is broken the app starts and reports it normally.
"""

from __future__ import annotations

import py_compile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from systema import APP_ROOT

# Marker for a developer working copy. Their files are intentionally ahead of
# upstream and mid-edit, so a syntax error here is a Tuesday, not a disaster.
DEV_MARKER = ".dev-copy"

# The modules the app cannot start without. Keep this SHORT and load-bearing --
# every entry costs startup time, and the point is "can it boot", not "is every
# file perfect".
CRITICAL_FILES = (
    "main.py",
    "systema/__init__.py",
    "systema/app/controller.py",
    "systema/engine/ai_engine.py",
    "systema/execution/tool_manager.py",
    "systema/execution/python_interpreter.py",
    "systema/execution/tool_registry.py",
    "systema/ui/chat_window.py",
    "systema/ui/windows/floating_window.py",
    "systema/memory/session_manager.py",
)

# Written before an update applies and removed after it finishes. Its presence
# at startup means the last apply did not complete -- the process was killed,
# lost power, or crashed partway through writing files.
APPLY_MARKER = APP_ROOT / "data" / "updates" / ".apply-in-progress"


@dataclass
class Problem:
    kind: str          # 'missing' | 'conflict' | 'syntax' | 'interrupted'
    path: str
    detail: str

    def describe(self) -> str:
        return f"{self.path}: {self.detail}" if self.path else self.detail


@dataclass
class Report:
    problems: list = field(default_factory=list)
    skipped: str = ""          # non-empty = the check did not run, and why

    @property
    def healthy(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        if self.skipped:
            return f"integrity check skipped ({self.skipped})"
        if self.healthy:
            return "working copy looks healthy"
        return f"{len(self.problems)} problem(s): " + "; ".join(
            p.describe() for p in self.problems[:4])


def in_dev_environment(root=None) -> bool:
    return (Path(root or APP_ROOT) / DEV_MARKER).exists()


def check_missing(root=None) -> list:
    root = Path(root or APP_ROOT)
    out = []
    for rel in CRITICAL_FILES:
        if not (root / rel).is_file():
            out.append(Problem("missing", rel, "file is missing"))
    return out


def check_syntax(root=None) -> list:
    """py_compile each critical file that exists.

    cfile goes to a temp path, never next to the source: writing .pyc into the
    working copy during a health check would itself modify the thing being
    checked, and on a read-only or damaged copy it would fail for the wrong
    reason.
    """
    root = Path(root or APP_ROOT)
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, rel in enumerate(CRITICAL_FILES):
            src = root / rel
            if not src.is_file():
                continue        # already reported by check_missing
            try:
                # quiet=1, NOT 2. py_compile's quiet=2 means "no output AND no
                # exception" -- with doraise=True that combination silently
                # swallows every syntax error, which made this check pass on a
                # file that could not be imported. quiet=1 is silent but still
                # raises.
                py_compile.compile(str(src), cfile=str(Path(tmp) / f"{i}.pyc"),
                                   doraise=True, quiet=1)
            except py_compile.PyCompileError as e:
                detail = str(getattr(e, "msg", "") or e).strip().splitlines()
                out.append(Problem("syntax", rel,
                                   detail[-1] if detail else "syntax error"))
            except (OSError, ValueError) as e:
                out.append(Problem("syntax", rel, f"{type(e).__name__}: {e}"))
    return out


# A 3-way merge that could not reconcile writes these into the file itself
# (gitplucker's conflict_policy="mark"). Spelled via chr() so this file can never
# be mistaken for containing a real conflict, and so a marker scan of the source
# tree does not flag the scanner.
_CONFLICT_MARKERS = (chr(60) * 7, chr(61) * 7, chr(62) * 7, chr(124) * 7)


def check_conflict_markers(root=None) -> list:
    """Unresolved merge markers left in a source file by an update.

    This is what actually broke a real install on 2026-08-19: an update wrote
    "<<<<<<< systema/ui/windows/floating_window.py (local)" into the file and the
    app died at import with a bare "SyntaxError: invalid syntax". py_compile does
    catch it, but naming it precisely is worth the extra read -- "unresolved
    conflict markers from an update" tells the user which recovery option helps,
    where a syntax error does not.
    """
    root = Path(root or APP_ROOT)
    out = []
    for rel in CRITICAL_FILES:
        src = root / rel
        if not src.is_file():
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for marker in _CONFLICT_MARKERS:
            if any(line.startswith(marker) for line in text.splitlines()):
                out.append(Problem(
                    "conflict", rel,
                    "unresolved merge conflict markers from an update"))
                break
    return out


def check_interrupted_apply(root=None) -> list:
    """An update that started and never finished leaves its marker behind.

    This is the case a syntax check CANNOT find: an apply killed between two
    file writes can leave every individual file syntactically valid while the
    set of them is a mix of two versions. The marker is the only honest signal.
    """
    marker = (Path(root) / "data" / "updates" / ".apply-in-progress"
              if root else APPLY_MARKER)
    if not marker.is_file():
        return []
    try:
        detail = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        detail = ""
    return [Problem("interrupted", "",
                    "the last update did not finish"
                    + (f" ({detail})" if detail else ""))]


def mark_apply_started(detail: str = "", root=None) -> bool:
    marker = (Path(root) / "data" / "updates" / ".apply-in-progress"
              if root else APPLY_MARKER)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(detail or ""), encoding="utf-8")
        return True
    except OSError:
        return False


def mark_apply_finished(root=None) -> bool:
    marker = (Path(root) / "data" / "updates" / ".apply-in-progress"
              if root else APPLY_MARKER)
    try:
        marker.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run_full_check(root=None, skip_dev: bool = True) -> Report:
    """The one call main.py makes. Never raises -- a health check that can crash
    the app is worse than no health check."""
    try:
        if skip_dev and in_dev_environment(root):
            return Report(skipped="developer working copy")
        problems = check_missing(root)
        # Markers BEFORE syntax: both fire on the same file, and "conflict
        # markers from an update" is the actionable phrasing.
        problems += check_conflict_markers(root)
        _conflicted = {p.path for p in problems if p.kind == "conflict"}
        problems += [p for p in check_syntax(root) if p.path not in _conflicted]
        problems += check_interrupted_apply(root)
        return Report(problems=problems)
    except Exception as e:                      # noqa: BLE001 - see docstring
        return Report(skipped=f"check failed: {type(e).__name__}: {e}")
