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


def _whitespace_tolerant_replace(content, old_text, new_text, replace_all, _re):
    """Retry an anchor match ignoring leading indentation and trailing whitespace
    on each line. Returns (new_content, None) on a clean apply, (None, error) when
    the normalized match is ambiguous, or (None, None) when it still doesn't match
    (so the caller falls back to its not-found hint). Exact matching is done by
    the caller first — this only runs when that misses."""
    old_lines = old_text.split('\n')
    while len(old_lines) > 1 and old_lines[-1].strip() == '':
        old_lines.pop()               # don't require a trailing newline
    if not any(ln.strip() for ln in old_lines):
        return None, None             # nothing anchorable
    parts = []
    for ln in old_lines:
        s = ln.strip()
        parts.append(r'[ \t]*' if s == '' else r'[ \t]*' + _re.escape(s) + r'[ \t]*')
    pattern = r'\n'.join(parts)
    matches = list(_re.finditer(pattern, content))
    if not matches:
        return None, None
    if len(matches) > 1 and not replace_all:
        return None, (f"OLD text matches {len(matches)} places once whitespace is "
                      f"normalized — add more surrounding lines to make it unique, "
                      f"or set flags: all")
    new_content = content
    for m in reversed(matches):        # right-to-left keeps earlier spans valid
        new_content = new_content[:m.start()] + new_text + new_content[m.end():]
    return new_content, None


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
        # ── Whitespace-tolerant fallback ─────────────────────────────────────
        # Exact match failed — retry ignoring leading indentation and trailing
        # whitespace on each line (the #1 cause of "the edit keeps fighting me").
        # Only applied when it resolves UNAMBIGUOUSLY; exact match always wins.
        import re as _re
        ws_new, ws_err = _whitespace_tolerant_replace(
            content, old_text, new_text, replace_all, _re)
        if ws_new is not None:
            return ws_new, None, diff_stats(content, ws_new)
        if ws_err:                       # ambiguous normalized match
            return None, ws_err, None
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


# ── grep (ripgrep-style recursive search) ────────────────────────────────────

# Directories pruned by default (ignore_common=True) — build/vendor/VCS noise.
_GREP_IGNORE_DIRS = frozenset({
    '.venv', 'venv', 'env', '.env', '__pycache__', '.git', 'node_modules',
    '.mypy_cache', '.pytest_cache', '.ruff_cache', '.idea', '.vscode',
    '.gradle', 'build', 'dist', '.tox', '.next', '.nuxt', '.svn', '.hg',
    '.cache', 'target', '.dart_tool', 'Pods', '.terraform',
})
_GREP_LINE_CHARS = 400        # truncate very long output lines
_GREP_ABS_CAP    = 5000       # hard ceiling regardless of head_limit

# --type filters (a professional subset, like ripgrep --type).
_GREP_TYPES = {
    'py': ('.py', '.pyi'), 'python': ('.py', '.pyi'),
    'js': ('.js', '.jsx', '.mjs', '.cjs'), 'ts': ('.ts', '.tsx'),
    'java': ('.java',), 'kotlin': ('.kt', '.kts'), 'kt': ('.kt', '.kts'),
    'c': ('.c', '.h'), 'cpp': ('.cpp', '.cc', '.cxx', '.hpp', '.hh', '.h'),
    'cs': ('.cs',), 'go': ('.go',), 'rust': ('.rs',), 'rs': ('.rs',),
    'rb': ('.rb',), 'ruby': ('.rb',), 'php': ('.php',), 'swift': ('.swift',),
    'html': ('.html', '.htm'), 'css': ('.css', '.scss', '.sass', '.less'),
    'json': ('.json',), 'yaml': ('.yaml', '.yml'), 'toml': ('.toml',),
    'xml': ('.xml',), 'md': ('.md', '.markdown'), 'sh': ('.sh', '.bash', '.zsh'),
    'sql': ('.sql',), 'txt': ('.txt',), 'dart': ('.dart',),
}


def grep(pattern, path='.', glob=None, type=None,
         output_mode='files_with_matches', case_insensitive=False,
         line_numbers=True, before=0, after=0, context=0,
         only_matching=False, multiline=False, head_limit=None,
         ignore_common=True, base=None):
    """ripgrep-style recursive content search. Returns an observation string.

    output_mode:
      files_with_matches (default) — paths that contain >=1 match, one per line
      content                      — matching lines `path:line:text` (+/-A/-B/-C context)
      count                        — per-file match counts `path:count`

    Full regex `pattern`. `glob` (e.g. '**/*.py') and `type` (e.g. 'py') filter files.
    `-A/-B/-C` = after/before/context lines (content mode). `head_limit` caps output
    (default 250; 0 = unlimited). `ignore_common` prunes build/VCS dirs. Cross-OS,
    pure stdlib; binary/undecodable files are skipped."""
    import os
    import re as _re
    import fnmatch

    root = resolve_path(path, base)
    if not root.exists():
        return f"ERROR:\nsearch path not found: {root}"

    flags = 0
    if case_insensitive:
        flags |= _re.IGNORECASE
    if multiline:
        flags |= _re.MULTILINE | _re.DOTALL
    try:
        rx = _re.compile(pattern, flags)
    except _re.error as e:
        return f"ERROR:\ninvalid regex {pattern!r}: {e}"

    if context:
        before = after = int(context)
    before = max(0, int(before or 0))
    after = max(0, int(after or 0))

    output_mode = (output_mode or 'files_with_matches').strip()
    if output_mode not in ('files_with_matches', 'content', 'count'):
        output_mode = 'files_with_matches'

    if head_limit is None:
        cap = 250
    else:
        try:
            hl = int(head_limit)
            cap = _GREP_ABS_CAP if hl == 0 else max(1, min(hl, _GREP_ABS_CAP))
        except (TypeError, ValueError):
            cap = 250

    glob_pat = (glob or '').replace('\\', '/').strip()
    _match_all = glob_pat in ('', '*', '**', '**/*', '**/**')
    _tail = glob_pat.rsplit('/', 1)[-1] if glob_pat else ''

    type_exts = None
    if type:
        type_exts = _GREP_TYPES.get(str(type).strip().lower())
        if type_exts is None:
            return (f"ERROR:\nunknown type {type!r}. Known types: "
                    + ", ".join(sorted(set(_GREP_TYPES))))

    def _keep(rel, name):
        if type_exts is not None and not name.lower().endswith(type_exts):
            return False
        if _match_all:
            return True
        return fnmatch.fnmatch(rel, glob_pat) or fnmatch.fnmatch(name, _tail)

    if root.is_file():
        walker = [(str(root.parent), [], [root.name])]
        rel_base = str(root.parent)
    else:
        walker = os.walk(str(root))
        rel_base = str(root)

    targets = []
    for dirpath, dirnames, filenames in walker:
        if ignore_common:
            dirnames[:] = [d for d in dirnames if d not in _GREP_IGNORE_DIRS]
        for fn in filenames:
            fpath = os.path.join(dirpath, fn)
            rel = os.path.relpath(fpath, rel_base).replace('\\', '/')
            if _keep(rel, fn):
                targets.append((fpath, rel))
    targets.sort(key=lambda t: t[1])

    def _read(fpath):
        try:
            with open(fpath, 'r', encoding='utf-8', errors='strict') as fh:
                return fh.read()
        except (UnicodeDecodeError, OSError, ValueError):
            return None

    def _match_linenos(txt, lines):
        if multiline:
            return sorted({txt.count('\n', 0, m.start()) + 1 for m in rx.finditer(txt)})
        return [i + 1 for i, ln in enumerate(lines) if rx.search(ln)]

    # ── files_with_matches (default) ──────────────────────────────────────────
    if output_mode == 'files_with_matches':
        hits = []
        for fpath, rel in targets:
            txt = _read(fpath)
            if txt is None:
                continue
            found = rx.search(txt) is not None if multiline \
                else any(rx.search(ln) for ln in txt.splitlines())
            if found:
                hits.append(rel)
                if len(hits) >= cap:
                    break
        head = f"grep {pattern!r} | {len(hits)} file(s) with matches"
        return head + ("\n" + "\n".join(hits) if hits else "")

    # ── count ─────────────────────────────────────────────────────────────────
    if output_mode == 'count':
        rows, total = [], 0
        for fpath, rel in targets:
            txt = _read(fpath)
            if txt is None:
                continue
            c = len(rx.findall(txt)) if multiline \
                else sum(1 for ln in txt.splitlines() if rx.search(ln))
            if c:
                rows.append(f"{rel}:{c}")
                total += c
                if len(rows) >= cap:
                    break
        head = f"grep {pattern!r} | {total} match(es) in {len(rows)} file(s)"
        return head + ("\n" + "\n".join(rows) if rows else "")

    # ── content ───────────────────────────────────────────────────────────────
    out, match_count, files_hit, truncated = [], 0, set(), False
    for fpath, rel in targets:
        txt = _read(fpath)
        if txt is None:
            continue
        lines = txt.splitlines()
        mls = _match_linenos(txt, lines)
        if not mls:
            continue
        files_hit.add(rel)
        printed = set()
        for lineno in mls:
            lo, hi = max(1, lineno - before), min(len(lines), lineno + after)
            for j in range(lo, hi + 1):
                if j in printed:
                    continue
                printed.add(j)
                is_match = (j == lineno)
                text = lines[j - 1]
                if is_match and only_matching and not multiline:
                    mm = rx.search(text)
                    if mm:
                        text = mm.group(0)
                text = text[:_GREP_LINE_CHARS]
                sep = ':' if is_match else '-'
                out.append(f"{rel}{sep}{j}{sep}{text}" if line_numbers
                           else f"{rel}{sep}{text}")
            match_count += 1
            if len(out) >= cap:
                truncated = True
                break
        if truncated:
            break
    head = (f"grep {pattern!r} | {match_count} match(es) in {len(files_hit)} file(s)"
            + (f" | output capped at {cap} lines" if truncated else ""))
    return head + ("\n" + "\n".join(out) if out else "")
