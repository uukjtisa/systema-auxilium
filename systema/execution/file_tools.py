"""
systema/execution/file_tools.py

The surgical file-editing subsystem: read_file / edit_file / write_file.

These are DEFAULT-ON foundation tools (part of work mode, alongside the Python
interpreter): reading is a numbered window into a file; editing is anchor-based
(the old text must match EXACTLY and uniquely, like professional agent
harnesses) with an explicit line-range fallback; writing is verbatim content.

Pure stdlib. Everything returns observation STRINGS shaped for the model
("ERROR: ..." on failure) — the ToolManager owns approval gating and the
journal hook; this module only computes content.
"""

from __future__ import annotations

import difflib
from pathlib import Path

_MAX_READ_LINES = 2000
_MAX_LINE_CHARS = 500


def resolve_path(path, base=None):
    """Absolute Path for a tool-supplied path string (cross-OS separators)."""
    s = str(path or "").strip().strip('"').strip("'")
    p = Path(s.replace("\\", "/")) if "/" in s.replace("\\", "/") else Path(s)
    if not p.is_absolute():
        p = Path(base or Path.cwd()) / p
    return p


def diff_stats(old_text, new_text):
    """(added, removed) line counts between two texts."""
    a = old_text.splitlines()
    b = new_text.splitlines()
    added = removed = 0
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed += a1 - a0
        if op in ("replace", "insert"):
            added += b1 - b0
    return added, removed


def read_file(path, start_line=1, max_lines=200, base=None):
    """Numbered read window. Returns an observation string."""
    p = resolve_path(path, base)
    if not p.is_file():
        return f"ERROR:\nfile not found: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"ERROR:\ncould not read {p}: {e}"
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(start_line or 1))
    count = min(int(max_lines or 200), _MAX_READ_LINES)
    window = lines[start - 1:start - 1 + count]
    if not window and total:
        return (f"ERROR:\nstart_line {start} is past the end of {p.name} "
                f"({total} lines total)")
    body = "\n".join(
        f"{start + i:>6}\t{ln[:_MAX_LINE_CHARS]}" for i, ln in enumerate(window))
    shown_to = start + len(window) - 1
    head = f"FILE: {p} | lines {start}-{shown_to} of {total}"
    if shown_to < total:
        head += f" | {total - shown_to} more line(s) — read again with start_line={shown_to + 1}"
    return head + ("\n" + body if body else "\n(empty file)")


def prepare_edit(path, old_text, new_text, replace_all=False, base=None):
    """Anchor-based edit. Returns (new_content | None, error | None, stats).

    The anchor must match EXACTLY; 0 matches or an ambiguous (>1) match without
    replace_all is an error the model can act on."""
    p = resolve_path(path, base)
    if not p.is_file():
        return None, f"file not found: {p}", None
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return None, f"could not read {p}: {e}", None
    if not old_text:
        return None, "OLD block is empty — provide the exact text to replace", None
    n = content.count(old_text)
    if n == 0:
        hint = ""
        first = next((ln for ln in old_text.splitlines() if ln.strip()), "").strip()
        if first:
            close = difflib.get_close_matches(
                first, [ln.strip() for ln in content.splitlines()], n=1, cutoff=0.75)
            if close:
                hint = (f" Closest line in the file: '{close[0][:120]}' — re-read the "
                        f"file and copy the exact current text (whitespace matters).")
        return None, f"OLD text not found in {p.name}.{hint}", None
    if n > 1 and not replace_all:
        return None, (f"OLD text matches {n} places in {p.name} — add more "
                      f"surrounding lines to make it unique, or set flags: all"), None
    new_content = content.replace(old_text, new_text)
    return new_content, None, diff_stats(content, new_content)


def prepare_edit_lines(path, start, end, new_text, base=None):
    """Line-range fallback: replace lines start..end (1-based, inclusive)."""
    p = resolve_path(path, base)
    if not p.is_file():
        return None, f"file not found: {p}", None
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return None, f"could not read {p}: {e}", None
    lines = content.splitlines(keepends=True)
    total = len(lines)
    if not (1 <= start <= end <= total):
        return None, (f"line range {start}-{end} is out of bounds "
                      f"({p.name} has {total} lines) — re-read the file"), None
    repl = new_text
    if repl and not repl.endswith("\n"):
        repl += "\n"
    new_content = "".join(lines[:start - 1]) + repl + "".join(lines[end:])
    return new_content, None, diff_stats(content, new_content)


def apply_write(path, content, base=None):
    """Write content verbatim (UTF-8, newline-preserving). Returns
    (ok, existed_before, error)."""
    p = resolve_path(path, base)
    existed = p.is_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return True, existed, None
    except Exception as e:
        return False, existed, str(e)


def read_current(path, base=None):
    """Current text of the file, or '' when absent."""
    p = resolve_path(path, base)
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def short_display(path):
    """~parent/file.py — the compact card label."""
    p = Path(str(path))
    parent = p.parent.name or p.anchor.rstrip(":\\/")
    return f"~{parent}/{p.name}" if parent else p.name
