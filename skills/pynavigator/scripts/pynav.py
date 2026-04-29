#!/usr/bin/env python3
"""
PyNavigator — context-efficient code navigation & editing for AI agents.

Usage:
    python pynav.py <command> [args...]
    python pynav.py help

All output includes line numbers in the │ gutter for AI reference.
All edit commands auto-backup before modifying any file.
"""

import ast
import sys
import os
import re
import shutil
import difflib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

# Pull in the indexer (lives next to this script)
sys.path.insert(0, os.path.dirname(__file__))
try:
    from indexer import (
        ensure_index, update_index_after_edit, Indexer,
        fmt_symbol_map, fmt_index_status, fmt_dead_code, fmt_dependency_tree,
        fmt_list_imports, fmt_get_dependencies, fmt_audit_deps,
    )
    _INDEXER_AVAILABLE = True
except ImportError:
    _INDEXER_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT FORMAT
# ─────────────────────────────────────────────────────────────────────────────

def fmt(lines: List[str], start_line: int, label: str = "") -> str:
    """Format lines with line numbers in │ gutter. Used by all read commands."""
    header = f"── {label} " + "─" * max(0, 62 - len(label)) if label else "─" * 64
    result = [header]
    for i, line in enumerate(lines, start=start_line):
        result.append(f"{i:>5} │ {line.rstrip()}")
    result.append("─" * 64)
    return "\n".join(result)

# ─────────────────────────────────────────────────────────────────────────────
# FILE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def read_file(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()

def write_file(path: str, lines: List[str]):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)

def find_py_files(root: str) -> List[str]:
    skip = {"__pycache__", "node_modules", "venv", ".venv", "env", ".git", ".pynavigator"}
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py"):
                result.append(os.path.join(dirpath, fn))
    return sorted(result)

# ─────────────────────────────────────────────────────────────────────────────
# AST HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_file(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    return ast.parse(src, filename=path)

def node_start(node: ast.AST) -> int:
    """Start line including decorators."""
    if hasattr(node, "decorator_list") and node.decorator_list:
        return node.decorator_list[0].lineno
    return node.lineno

def node_end(node: ast.AST) -> int:
    return node.end_lineno

def find_named(tree: ast.Module, name: str, types) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, types) and getattr(node, "name", None) == name:
            return node
    return None

def find_method(tree: ast.Module, class_name: str, method_name: str) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return item
    return None

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
DEF_TYPES  = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# ─────────────────────────────────────────────────────────────────────────────
# BACKUP SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

def get_project_root(file_path: str) -> str:
    p = Path(file_path).resolve().parent
    while p != p.parent:
        if (p / ".git").exists() or (p / ".pynavigator").exists():
            return str(p)
        p = p.parent
    return str(Path(file_path).resolve().parent)

def get_session(project_root: str) -> str:
    """Get current session dir, creating one if needed."""
    nav_dir = Path(project_root) / ".pynavigator"
    nav_dir.mkdir(exist_ok=True)
    session_file = nav_dir / "current_session"
    if session_file.exists():
        session_path = session_file.read_text().strip()
        if Path(session_path).exists():
            return session_path
    ts = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    session_path = str(nav_dir / "backups" / f"session_{ts}")
    Path(session_path).mkdir(parents=True, exist_ok=True)
    session_file.write_text(session_path)
    return session_path

def auto_backup(file_path: str, operation: str) -> str:
    """
    Auto-backup before every edit. Returns a status string for the agent.
    Creates:
      1. Named snapshot  →  {stem}_before_{operation}[_N].py   (human-friendly recovery)
      2. Diff log entry  →  changes.log inside session folder   (audit trail)
    """
    root    = get_project_root(file_path)
    session = get_session(root)
    stem    = Path(file_path).stem

    # 1. Named snapshot — stack if name exists
    base = f"{stem}_before_{operation}"
    name = f"{base}.py"
    n = 1
    while (Path(session) / name).exists():
        name = f"{base}_{n}.py"
        n += 1
    backup_path = Path(session) / name
    shutil.copy2(file_path, backup_path)

    # 2. Diff log
    log = Path(session) / "changes.log"
    with open(log, "a") as f:
        f.write(f"\n[{datetime.now().isoformat()}] {operation} → {file_path}\n")
        f.write(f"  snapshot: {backup_path}\n")

    return (
        f"📦 Backup → {backup_path}\n"
        f"   Session → {session}"
    )

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — READ / NAVIGATE
# ─────────────────────────────────────────────────────────────────────────────

def cmd_outline(file: str):
    """File skeleton: all classes, methods, top-level functions. No bodies."""
    tree  = parse_file(file)
    rows  = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, FUNC_TYPES):
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args = [a.arg for a in node.args.args]
            rows.append(f"{node.lineno:>5} │ {kind} {node.name}({', '.join(args)})")
        elif isinstance(node, ast.ClassDef):
            rows.append(f"{node.lineno:>5} │ class {node.name}:")
            for item in node.body:
                if isinstance(item, FUNC_TYPES):
                    kind = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    args = [a.arg for a in item.args.args]
                    rows.append(f"{item.lineno:>9} │     {kind} {item.name}({', '.join(args)})")
    header = f"── Outline: {file} " + "─" * max(0, 52 - len(file))
    print(header)
    print("\n".join(rows) if rows else "  (no definitions found)")
    print("─" * 64)

def cmd_find_function(file: str, name: str):
    tree = parse_file(file)
    node = find_named(tree, name, FUNC_TYPES)
    if not node:
        print(f"❌ Function '{name}' not found in {file}"); return
    print(f"✅ {name}  →  {file}  L{node_start(node)}–{node_end(node)}")

def cmd_find_class(file: str, name: str):
    tree = parse_file(file)
    node = find_named(tree, name, ast.ClassDef)
    if not node:
        print(f"❌ Class '{name}' not found in {file}"); return
    print(f"✅ class {name}  →  {file}  L{node.lineno}–{node.end_lineno}")

def cmd_find_method(file: str, class_name: str, method_name: str):
    tree = parse_file(file)
    node = find_method(tree, class_name, method_name)
    if not node:
        print(f"❌ Method '{class_name}.{method_name}' not found in {file}"); return
    print(f"✅ {class_name}.{method_name}  →  {file}  L{node_start(node)}–{node_end(node)}")

def cmd_read_function(file: str, name: str):
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, name, FUNC_TYPES)
    if not node:
        print(f"❌ Function '{name}' not found in {file}"); return
    s, e = node_start(node) - 1, node_end(node)
    print(fmt(raw[s:e], s + 1, f"{file} :: {name}"))

def cmd_read_class(file: str, name: str):
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, name, ast.ClassDef)
    if not node:
        print(f"❌ Class '{name}' not found in {file}"); return
    s, e = node_start(node) - 1, node_end(node)
    print(fmt(raw[s:e], s + 1, f"{file} :: class {name}"))

def cmd_read_method(file: str, class_name: str, method_name: str):
    tree = parse_file(file); raw = read_file(file)
    node = find_method(tree, class_name, method_name)
    if not node:
        print(f"❌ Method '{class_name}.{method_name}' not found in {file}"); return
    s, e = node_start(node) - 1, node_end(node)
    print(fmt(raw[s:e], s + 1, f"{file} :: {class_name}.{method_name}"))

def cmd_read_lines(file: str, start: int, end: int):
    raw = read_file(file)
    s, e = max(1, start) - 1, min(len(raw), end)
    print(fmt(raw[s:e], s + 1, f"{file}  L{start}–{end}"))

def cmd_get_signature(file: str, name: str):
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, name, DEF_TYPES)
    if not node:
        # search methods too
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef):
                for item in cls.body:
                    if isinstance(item, FUNC_TYPES) and item.name == name:
                        node = item; break
    if not node:
        print(f"❌ '{name}' not found in {file}"); return
    lines = [raw[node.lineno - 1]]
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        for dl in str(body[0].value.s).strip().split("\n"):
            lines.append(f"    # {dl.strip()}\n")
    print(fmt(lines, node.lineno, f"{file} :: {name} (signature only)"))

def cmd_get_context(file: str, line: int, n: int = 10):
    raw = read_file(file)
    s = max(0, line - n - 1)
    e = min(len(raw), line + n)
    print(fmt(raw[s:e], s + 1, f"{file}  ±{n} lines around L{line}"))

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — SEARCH / ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

_DIM   = "\033[2m"
_RESET = "\033[0m"
_CYAN  = "\033[36m"
_GREEN = "\033[32m"
_YELLOW= "\033[33m"

def cmd_search(root: str, pattern: str):
    """Regex search across all .py files under root."""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        print(f"❌ Invalid regex: {e}"); return
    files = find_py_files(root)
    total = 0
    for fp in files:
        raw = read_file(fp)
        hits = [(i + 1, line.rstrip()) for i, line in enumerate(raw) if rx.search(line)]
        if hits:
            rel = os.path.relpath(fp, root)
            print(f"\n── {rel} " + "─" * max(0, 58 - len(rel)))
            for lineno, line in hits:
                print(f"  {lineno:>5} │ {line}")
            total += len(hits)
    print(f"\n  {total} match(es) across {len(files)} file(s)")

def cmd_find_usages(root: str, name: str):
    """All usages of name. Uses index call graph + referenced_in when available."""
    if _INDEXER_AVAILABLE:
        index = ensure_index(root)
        if index:
            rec = index.lookup(name)
            if rec:
                print(f"── Usages of '{name}'  {_DIM}(index){_RESET}")
                print(f"  Defined:  {rec.file}  L{rec.line}")
                if rec.called_by:
                    print(f"  Called by:")
                    for c in rec.called_by:
                        cr = index.lookup(c)
                        loc = f"  L{cr.line}" if cr else ""
                        print(f"    {c}{loc}")
                if rec.referenced_in:
                    print(f"  Referenced in:")
                    for f in rec.referenced_in:
                        print(f"    {f}")
                if not rec.called_by and not rec.referenced_in:
                    print(f"  {_DIM}(no usages found — possible dead code){_RESET}")
                return
    # Fallback: raw regex
    cmd_search(root, rf"\b{re.escape(name)}\b")

def cmd_find_references(root: str, name: str):
    """All import/reference lines mentioning name. Index-accelerated."""
    if _INDEXER_AVAILABLE:
        index = ensure_index(root)
        if index:
            rec = index.lookup(name)
            if rec:
                print(f"── References to '{name}'  {_DIM}(index){_RESET}")
                print(f"  {rec.line:>5} │ {rec.file}  [definition]")
                for f in rec.referenced_in:
                    print(f"         │ {f}")
                who = index.files_importing(Path(rec.file).stem)
                for f in who:
                    if f not in rec.referenced_in and f != rec.file:
                        print(f"         │ {f}  {_DIM}(imports module){_RESET}")
                return
    # Fallback: file scan
    files = find_py_files(root)
    rx = re.compile(
        rf"(?:import\s+{re.escape(name)}|from\s+\S+\s+import\s+.*\b{re.escape(name)}\b|\b{re.escape(name)}\b)"
    )
    current = None
    count = 0
    for fp in files:
        raw = read_file(fp)
        hits = [(i + 1, line.rstrip()) for i, line in enumerate(raw) if rx.search(line)]
        if hits:
            rel = os.path.relpath(fp, root)
            if rel != current:
                print(f"\n── {rel}"); current = rel
            for lineno, line in hits:
                print(f"  {lineno:>5} │ {line}")
            count += len(hits)
    if count == 0:
        print(f"❌ No references to '{name}' found")
    else:
        print(f"\n  {count} reference(s) found")

def cmd_goto_declaration(root: str, name: str):
    """Find where name is declared. Uses index if available, falls back to AST walk."""
    # ── Index-fast path ───────────────────────────────────────────────────
    if _INDEXER_AVAILABLE:
        index = ensure_index(root)
        if index:
            rec = index.lookup(name)
            if rec:
                print(f"── Declaration of '{name}'  {_DIM}(index){_RESET}")
                print(f"  {rec.line:>5} │ {rec.file}  →  {rec.signature}")
                if rec.called_by:
                    print(f"  {_DIM}  called by: {', '.join(rec.called_by[:6])}{_RESET}")
                if rec.referenced_in:
                    print(f"  {_DIM}  used in:   {', '.join(rec.referenced_in[:6])}{_RESET}")
                return

    # ── AST fallback ──────────────────────────────────────────────────────
    files = find_py_files(root)
    found = []
    for fp in files:
        try:
            tree = parse_file(fp)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, DEF_TYPES) and getattr(node, "name", None) == name:
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                found.append((fp, node.lineno, f"{kind} {name}"))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name:
                        found.append((fp, node.lineno, f"{name} = ..."))
    if not found:
        print(f"❌ Declaration of '{name}' not found"); return
    print(f"── Declarations of '{name}'")
    for fp, lineno, sig in found:
        rel = os.path.relpath(fp, root)
        print(f"  {lineno:>5} │ {rel}  →  {sig}")

def cmd_call_hierarchy(file: str, target: str):
    """All functions/methods in file that call target."""
    tree = parse_file(file)
    callers = []
    for node in ast.walk(tree):
        if isinstance(node, FUNC_TYPES):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    fn = child.func
                    cname = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                    if cname == target:
                        callers.append((node.name, node_start(node), node_end(node)))
                        break
    if not callers:
        print(f"❌ No callers of '{target}' found in {file}"); return
    print(f"── Callers of '{target}' in {file}")
    for name, s, e in callers:
        print(f"  {s:>5} │ def {name}  (L{s}–{e})")

def cmd_find_todos(root: str):
    """Find all TODO / FIXME / HACK / NOTE / BUG / XXX comments."""
    rx = re.compile(r"#\s*(TODO|FIXME|HACK|NOTE|BUG|XXX)\b(.*)", re.IGNORECASE)
    files = find_py_files(root)
    total = 0
    for fp in files:
        raw = read_file(fp)
        hits = []
        for i, line in enumerate(raw):
            m = rx.search(line)
            if m:
                hits.append((i + 1, m.group(1).upper(), m.group(2).strip()))
        if hits:
            rel = os.path.relpath(fp, root)
            print(f"\n── {rel}")
            for lineno, tag, msg in hits:
                print(f"  {lineno:>5} │ [{tag}] {msg}")
            total += len(hits)
    print(f"\n  {total} item(s) found")

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — EDIT  (all auto-backup first)
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_newlines(content: str) -> List[str]:
    lines = content.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    return lines

def cmd_diff_preview(file: str, start: int, end: int, new_content: str):
    """Show what edit_lines would look like — no file changes."""
    raw      = read_file(file)
    new_lns  = _ensure_newlines(new_content)
    modified = raw[:start - 1] + new_lns + raw[end:]
    diff     = list(difflib.unified_diff(
        raw, modified,
        fromfile=f"{file} (original)",
        tofile=f"{file} (preview)",
        lineterm=""
    ))
    print("\n".join(diff) if diff else "  (no changes)")

def cmd_edit_lines(file: str, start: int, end: int, new_content: str):
    """Replace lines start–end (1-indexed, inclusive) with new_content."""
    print(auto_backup(file, f"edit_lines_L{start}-{end}"))
    raw      = read_file(file)
    new_lns  = _ensure_newlines(new_content)
    modified = raw[:start - 1] + new_lns + raw[end:]
    write_file(file, modified)
    print(f"✅ Replaced L{start}–{end} in {file}  ({len(new_lns)} line(s) written)")
    if _INDEXER_AVAILABLE:
        root = get_project_root(file)
        update_index_after_edit(root, str(Path(file).resolve()))

def cmd_insert_after(file: str, target_name: str, content: str):
    """Insert content after the closing line of a named block."""
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, target_name, DEF_TYPES)
    if not node:
        print(f"❌ '{target_name}' not found in {file}"); return
    print(auto_backup(file, f"insert_after_{target_name}"))
    e        = node_end(node)
    new_lns  = _ensure_newlines(content)
    modified = raw[:e] + ["\n"] + new_lns + raw[e:]
    write_file(file, modified)
    print(f"✅ Inserted {len(new_lns)} line(s) after L{e} (end of '{target_name}') in {file}")
    if _INDEXER_AVAILABLE:
        update_index_after_edit(get_project_root(file), str(Path(file).resolve()))

def cmd_insert_before(file: str, target_name: str, content: str):
    """Insert content before the start (incl. decorators) of a named block."""
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, target_name, DEF_TYPES)
    if not node:
        print(f"❌ '{target_name}' not found in {file}"); return
    print(auto_backup(file, f"insert_before_{target_name}"))
    s        = node_start(node) - 1
    new_lns  = _ensure_newlines(content)
    modified = raw[:s] + new_lns + ["\n"] + raw[s:]
    write_file(file, modified)
    print(f"✅ Inserted {len(new_lns)} line(s) before L{s + 1} ('{target_name}') in {file}")
    if _INDEXER_AVAILABLE:
        update_index_after_edit(get_project_root(file), str(Path(file).resolve()))

def cmd_delete_block(file: str, name: str):
    """Delete a named function/class block (including decorators)."""
    tree = parse_file(file); raw = read_file(file)
    node = find_named(tree, name, DEF_TYPES)
    if not node:
        print(f"❌ '{name}' not found in {file}"); return
    print(auto_backup(file, f"delete_{name}"))
    s = node_start(node) - 1
    e = node_end(node)
    if s > 0 and raw[s - 1].strip() == "":
        s -= 1
    modified = raw[:s] + raw[e:]
    write_file(file, modified)
    print(f"✅ Deleted '{name}' (L{s + 1}–{e}) from {file}")
    if _INDEXER_AVAILABLE:
        update_index_after_edit(get_project_root(file), str(Path(file).resolve()))

def cmd_add_import(file: str, import_stmt: str):
    """Add import statement if not already present."""
    raw     = read_file(file)
    content = "".join(raw)
    stmt    = import_stmt.strip()
    if stmt in content:
        print(f"ℹ️  Import already present: {stmt}"); return
    print(auto_backup(file, "add_import"))
    last_import = 0
    for i, line in enumerate(raw):
        if line.strip().startswith(("import ", "from ")):
            last_import = i
    insert_at = last_import + 1
    modified  = raw[:insert_at] + [stmt + "\n"] + raw[insert_at:]
    write_file(file, modified)
    print(f"✅ Added at L{insert_at + 1}: {stmt}")
    if _INDEXER_AVAILABLE:
        update_index_after_edit(get_project_root(file), str(Path(file).resolve()))

def cmd_refactor(root: str, old_name: str, new_name: str):
    """Rename identifier across entire project."""
    files   = find_py_files(root)
    rx      = re.compile(rf"\b{re.escape(old_name)}\b")
    changed = []
    for fp in files:
        raw      = read_file(fp)
        new_lns  = [rx.sub(new_name, line) for line in raw]
        if new_lns != raw:
            auto_backup(fp, f"refactor_{old_name}_to_{new_name}")
            write_file(fp, new_lns)
            changed.append(fp)
    if changed:
        print(f"✅ '{old_name}' → '{new_name}'  in {len(changed)} file(s):")
        for fp in changed:
            print(f"   {os.path.relpath(fp, root)}")
        if _INDEXER_AVAILABLE:
            # Rebuild fully — refactor touches many files
            print(f"  {_DIM}↺  Rebuilding index after rename...{_RESET}", end="", flush=True)
            Indexer(root).build(verbose=False)
            print(f" done.")
    else:
        print(f"❌ '{old_name}' not found in any file under {root}")


def cmd_line_count(file: str):
    """Count lines in a single file with a breakdown of blank/comment/code."""
    fp = os.path.abspath(file)
    if not os.path.isfile(fp):
        print(f"❌ File not found: {fp}")
        return
    lines = read_file(fp)
    total   = len(lines)
    blank   = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("#"))
    code    = total - blank - comment
    rel     = os.path.relpath(fp)
    bar_w   = 40
    code_w    = round(bar_w * code    / max(total, 1))
    comment_w = round(bar_w * comment / max(total, 1))
    blank_w   = bar_w - code_w - comment_w
    bar = f"{'█' * code_w}{'░' * comment_w}{'·' * blank_w}"
    print(f"\n── {rel} {'─' * max(0, 62 - len(rel))}")
    print(f"  Total    {total:>6,} lines")
    print(f"  Code     {code:>6,}  ({round(100*code/max(total,1))}%)")
    print(f"  Comments {comment:>6,}  ({round(100*comment/max(total,1))}%)")
    print(f"  Blank    {blank:>6,}  ({round(100*blank/max(total,1))}%)")
    print(f"  [{bar}]")


def cmd_line_count_project(root: str):
    """Count lines across all .py files in the project, sorted by size."""
    files = find_py_files(root)
    if not files:
        print(f"❌ No .py files found under {root}")
        return
    rows = []
    for fp in files:
        lines   = read_file(fp)
        total   = len(lines)
        blank   = sum(1 for l in lines if not l.strip())
        comment = sum(1 for l in lines if l.strip().startswith("#"))
        code    = total - blank - comment
        rows.append((total, code, comment, blank, fp))
    rows.sort(reverse=True)
    grand_total   = sum(r[0] for r in rows)
    grand_code    = sum(r[1] for r in rows)
    grand_comment = sum(r[2] for r in rows)
    grand_blank   = sum(r[3] for r in rows)
    bar_w = 30
    max_lines = rows[0][0] if rows else 1
    print(f"\n── Line count: {os.path.basename(root)} {'─' * 40}")
    print(f"  {'File':<40} {'Total':>7}  {'Code':>6}  {'Cmnt':>6}  {'Blank':>6}")
    print(f"  {'─'*40} {'─'*7}  {'─'*6}  {'─'*6}  {'─'*6}")
    for total, code, comment, blank, fp in rows:
        rel  = os.path.relpath(fp, root)
        filled = round(bar_w * total / max_lines)
        bar  = "█" * filled + "·" * (bar_w - filled)
        print(f"  {rel:<40} {total:>7,}  {code:>6,}  {comment:>6,}  {blank:>6,}  [{bar}]")
    print(f"  {'─'*40} {'─'*7}  {'─'*6}  {'─'*6}  {'─'*6}")
    print(f"  {'TOTAL':<40} {grand_total:>7,}  {grand_code:>6,}  {grand_comment:>6,}  {grand_blank:>6,}")
    print(f"\n  {len(files)} file(s)  •  {grand_total:,} total lines  •  {grand_code:,} code lines")


def cmd_rename_file(root: str, old_module: str, new_module: str):
    """Rename a .py file and update all import references across the project.

    old_module / new_module are dotted module paths relative to root,
    e.g.  core.auth_handler  or just  auth_handler.

    Handles all four import forms:
      import old_module
      import old_module as x
      from old_module import x
      from pkg.old_module import x   (any prefix depth)
    The actual file is renamed on disk and the index is rebuilt.
    """
    # ── 1. Locate the file ──────────────────────────────────────────────────
    old_rel_path = old_module.replace(".", os.sep) + ".py"
    old_abs      = os.path.join(root, old_rel_path)
    if not os.path.isfile(old_abs):
        print(f"❌ File not found: {old_abs}")
        print(f"   (expected module '{old_module}' → '{old_rel_path}')")
        return

    new_rel_path = new_module.replace(".", os.sep) + ".py"
    new_abs      = os.path.join(root, new_rel_path)
    if os.path.exists(new_abs):
        print(f"❌ Target already exists: {new_abs}")
        return

    # ── 2. Rename the file ──────────────────────────────────────────────────
    auto_backup(old_abs, f"rename_file_{old_module}_to_{new_module}")
    os.makedirs(os.path.dirname(new_abs), exist_ok=True)
    shutil.move(old_abs, new_abs)
    print(f"  📁 Renamed:  {old_rel_path}  →  {new_rel_path}")

    # ── 3. Rewrite imports in all .py files ─────────────────────────────────
    # Extract the simple name part (last segment) for each
    old_simple = old_module.split(".")[-1]
    new_simple = new_module.split(".")[-1]

    # Build patterns that match any of:
    #   import <old_module>
    #   import <old_module> as ...
    #   from <old_module> import ...
    #   from <pkg>.<old_module> import ...   (old_simple at end of dotted path)
    import_rx  = re.compile(
        rf"^(\s*import\s+){re.escape(old_module)}(\s|$|\s+as\s)",
        re.MULTILINE,
    )
    import_rx2 = re.compile(
        rf"^(\s*import\s+)((?:\w+\.)*){re.escape(old_simple)}(\s|$|\s+as\s)",
        re.MULTILINE,
    )
    from_rx = re.compile(
        rf"^(\s*from\s+)((?:\w+\.)*){re.escape(old_simple)}(\s+import\s)",
        re.MULTILINE,
    )

    files   = find_py_files(root)
    changed = []
    for fp in files:
        raw  = read_file(fp)
        text = "\n".join(raw)

        # Replace dotted full path first (most specific)
        new_text = re.sub(
            rf"\b{re.escape(old_module)}\b",
            new_module,
            text,
        )
        # Then handle simple-name tail replacements in import/from lines
        # (for when only the filename changes, not the package prefix)
        if old_simple != new_simple:
            def _replace_import(m):
                prefix = m.group(2)  # e.g. "core."
                suffix = m.group(3)  # whitespace / "as "
                return m.group(1) + prefix + new_simple + suffix
            new_text = import_rx2.sub(_replace_import, new_text)
            new_text = from_rx.sub(
                lambda m: m.group(1) + m.group(2) + new_simple + m.group(3),
                new_text,
            )

        if new_text != text:
            auto_backup(fp, f"rename_file_{old_module}_to_{new_module}")
            write_file(fp, new_text.split("\n"))
            changed.append(fp)

    if changed:
        print(f"  ✏️  Updated imports in {len(changed)} file(s):")
        for fp in changed:
            print(f"     {os.path.relpath(fp, root)}")
    else:
        print(f"  ℹ️  No import references found to update.")

    # ── 4. Rebuild index ────────────────────────────────────────────────────
    if _INDEXER_AVAILABLE:
        print(f"  ↺  Rebuilding index...", end="", flush=True)
        Indexer(root).build(verbose=False)
        print(" done.")
    print(f"\n✅ Rename complete: '{old_module}' → '{new_module}'")

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — SAFETY
# ─────────────────────────────────────────────────────────────────────────────

def cmd_validate(file: str):
    """Check Python syntax without executing."""
    try:
        with open(file) as f:
            src = f.read()
        ast.parse(src, filename=file)
        print(f"✅ {file} — syntax OK")
    except SyntaxError as e:
        print(f"❌ {file} — SyntaxError at L{e.lineno}: {e.msg}")

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — BACKUP MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def cmd_backup_snapshot(file: str, label: str):
    """Manually create a named human-friendly snapshot."""
    root    = get_project_root(file)
    session = get_session(root)
    stem    = Path(file).stem
    name    = f"{stem}_snapshot_{label}.py"
    n = 1
    while (Path(session) / name).exists():
        name = f"{stem}_snapshot_{label}_{n}.py"; n += 1
    dest = Path(session) / name
    shutil.copy2(file, dest)
    print(f"📸 Snapshot saved: {dest}")

def cmd_list_backups(root: str):
    """List all sessions and their backup files."""
    nav_dir = Path(root) / ".pynavigator" / "backups"
    if not nav_dir.exists():
        print("No backups found."); return
    sessions = sorted(nav_dir.iterdir())
    for session in sessions:
        if not session.is_dir(): continue
        files = sorted(session.glob("*.py"))
        log   = session / "changes.log"
        print(f"\n📁 {session.name}  ({len(files)} snapshot(s))")
        for f in files:
            print(f"   {f.name}")
        if log.exists():
            print(f"   changes.log ✓")

def cmd_new_session(root: str):
    """Force-start a new backup session (previous session is preserved)."""
    nav_dir = Path(root) / ".pynavigator"
    nav_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%Hh%M")
    session_path = str(nav_dir / "backups" / f"session_{ts}")
    Path(session_path).mkdir(parents=True, exist_ok=True)
    (nav_dir / "current_session").write_text(session_path)
    print(f"✅ New session: {session_path}")

# ─────────────────────────────────────────────────────────────────────────────
# COMMANDS — INDEX (requires indexer.py)
# ─────────────────────────────────────────────────────────────────────────────

def _no_indexer():
    print("❌ indexer.py not found next to pynav.py. Cannot use index commands.")

def cmd_index(root: str):
    """Build (or rebuild) the full project index. Run this first on any new codebase."""
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    Indexer(root).build(verbose=True)

def cmd_index_status(root: str):
    """Show index health: staleness, file count, symbol count, last indexed time."""
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    idx = Indexer(root)
    if not idx.index.load():
        print(f"❌ No index found. Run: python pynav.py index {root}"); return
    stale = idx.index.stale_files()
    print(fmt_index_status(idx.index, stale))
    if stale:
        print(f"\n  {_DIM}Re-indexing stale files in background...{_RESET}")
        idx.maybe_reindex_background()

def cmd_symbol_map(root: str):
    """
    Dump the full project topology to stdout in one shot.
    Gives an AI complete codebase awareness without reading any source files.
    """
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        print("❌ Could not load index."); return
    print(fmt_symbol_map(index, show_methods=True))

def cmd_who_imports(root: str, module: str):
    """List all files that import a given module name."""
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    files = index.files_importing(module)
    if not files:
        print(f"❌ No files import '{module}'"); return
    print(f"── Files importing '{module}'")
    for f in sorted(files):
        print(f"  {f}")

def cmd_dependency_tree(root: str, file: str):
    """Show the full import dependency tree for a file."""
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    abs_root = os.path.abspath(root)
    abs_file = os.path.abspath(file)
    rel_path = os.path.relpath(abs_file, abs_root)
    # Also match bare filename like "gameplay.py"
    if rel_path not in index.import_graph:
        for key in index.import_graph:
            if key == rel_path or key.endswith(os.sep + rel_path) or os.path.basename(key) == rel_path:
                rel_path = key
                break
    print(f"── Dependency tree: {rel_path}")
    print(fmt_dependency_tree(index, rel_path))

def cmd_dead_code(root: str):
    """
    Detect symbols defined but never called or referenced anywhere.
    Helps agents identify cleanup opportunities.
    """
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    dead = index.dead_symbols()
    print(fmt_dead_code(dead))


def cmd_list_imports(root: str):
    """
    Full import picture across the entire project.
    Every module classified as stdlib / third-party / local,
    with frequency (how many files use it) and which files.
    """
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    print(fmt_list_imports(index))


def cmd_get_dependencies(root: str):
    """
    Third-party packages only — what belongs in requirements.txt.
    Sorted by how many files import them.
    Skips stdlib and local project modules.
    """
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    print(fmt_get_dependencies(index))


def cmd_audit_deps(root: str):
    """
    Cross-reference detected imports against requirements.txt.
    Flags packages imported but missing from requirements,
    and packages in requirements but never actually imported.
    """
    if not _INDEXER_AVAILABLE:
        _no_indexer(); return
    index = ensure_index(root)
    if not index:
        return
    print(fmt_audit_deps(index, root))

# ─────────────────────────────────────────────────────────────────────────────
# COMMAND REGISTRY & DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

# Format: "command": (function, [arg_spec, ...])
# arg_spec: "name" (str), "name:int" (int), "?name:int=N" (optional int with default)
REGISTRY = {
    # Read / Navigate
    "outline":          (cmd_outline,          ["file"]),
    "find_function":    (cmd_find_function,     ["file", "name"]),
    "find_class":       (cmd_find_class,        ["file", "name"]),
    "find_method":      (cmd_find_method,       ["file", "class_name", "method_name"]),
    "read_function":    (cmd_read_function,     ["file", "name"]),
    "read_class":       (cmd_read_class,        ["file", "name"]),
    "read_method":      (cmd_read_method,       ["file", "class_name", "method_name"]),
    "read_lines":       (cmd_read_lines,        ["file", "start:int", "end:int"]),
    "get_signature":    (cmd_get_signature,     ["file", "name"]),
    "get_context":      (cmd_get_context,       ["file", "line:int", "?n:int=10"]),
    # Search / Analysis
    "search":           (cmd_search,            ["root", "pattern"]),
    "find_usages":      (cmd_find_usages,       ["root", "name"]),
    "find_references":  (cmd_find_references,   ["root", "name"]),
    "goto_declaration": (cmd_goto_declaration,  ["root", "name"]),
    "call_hierarchy":   (cmd_call_hierarchy,    ["file", "target"]),
    "find_todos":       (cmd_find_todos,        ["root"]),
    # Edit
    "diff_preview":     (cmd_diff_preview,      ["file", "start:int", "end:int", "new_content"]),
    "edit_lines":       (cmd_edit_lines,        ["file", "start:int", "end:int", "new_content"]),
    "insert_after":     (cmd_insert_after,      ["file", "target_name", "content"]),
    "insert_before":    (cmd_insert_before,     ["file", "target_name", "content"]),
    "delete_block":     (cmd_delete_block,      ["file", "name"]),
    "add_import":       (cmd_add_import,        ["file", "import_stmt"]),
    "refactor":         (cmd_refactor,          ["root", "old_name", "new_name"]),
    "rename_file":      (cmd_rename_file,       ["root", "old_module", "new_module"]),
    "line_count":       (cmd_line_count,        ["file"]),
    "line_count_project": (cmd_line_count_project, ["root"]),
    # Safety
    "validate":         (cmd_validate,          ["file"]),
    # Backup
    "backup_snapshot":  (cmd_backup_snapshot,   ["file", "label"]),
    "list_backups":     (cmd_list_backups,      ["root"]),
    "new_session":      (cmd_new_session,       ["root"]),
    # Index (requires indexer.py)
    "index":            (cmd_index,             ["root"]),
    "index_status":     (cmd_index_status,      ["root"]),
    "symbol_map":       (cmd_symbol_map,        ["root"]),
    "who_imports":      (cmd_who_imports,       ["root", "module"]),
    "dependency_tree":  (cmd_dependency_tree,   ["root", "file"]),
    "dead_code":        (cmd_dead_code,         ["root"]),
    "list_imports":     (cmd_list_imports,      ["root"]),
    "get_dependencies": (cmd_get_dependencies,  ["root"]),
    "audit_deps":       (cmd_audit_deps,        ["root"]),
}

CATEGORIES = [
    ("READ / NAVIGATE",   ["outline","find_function","find_class","find_method",
                           "read_function","read_class","read_method","read_lines",
                           "get_signature","get_context"]),
    ("SEARCH / ANALYSIS", ["search","find_usages","find_references","goto_declaration",
                           "call_hierarchy","find_todos"]),
    ("EDIT",              ["diff_preview","edit_lines","insert_after","insert_before",
                           "delete_block","add_import","refactor","rename_file"]),
    ("METRICS",           ["line_count","line_count_project"]),
    ("SAFETY",            ["validate"]),
    ("BACKUP",            ["backup_snapshot","list_backups","new_session"]),
    ("INDEX  ★",          ["index","index_status","symbol_map","who_imports",
                           "dependency_tree","dead_code",
                           "list_imports","get_dependencies","audit_deps"]),
]

def print_help():
    print("PyNavigator — context-efficient code nav & editing for AI agents\n")
    print("Usage: python pynav.py <command> [args...]\n")
    for cat, cmds in CATEGORIES:
        print(f"  {cat}")
        for cmd in cmds:
            _, specs = REGISTRY[cmd]
            args_str = " ".join(
                f"[{s.lstrip('?').split(':')[0]}]" if s.startswith("?") else f"<{s.split(':')[0]}>"
                for s in specs
            )
            print(f"    {cmd:<20} {args_str}")
        print()
    print("Notes:")
    print("  • All read outputs include │ line numbers for agent reference.")
    print("  • All edit commands auto-backup to .pynavigator/backups/ before writing.")
    print("  • <new_content> and <content> args: pass multi-line text as a single")
    print("    quoted string with \\n, or pipe via stdin in a shell wrapper.")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print_help(); return

    cmd = sys.argv[1]
    if cmd not in REGISTRY:
        print(f"❌ Unknown command: '{cmd}'")
        print(f"   Run 'python pynav.py help' for a full list."); return

    func, specs = REGISTRY[cmd]
    raw_args    = sys.argv[2:]
    parsed      = []

    for i, spec in enumerate(specs):
        optional = spec.startswith("?")
        spec     = spec.lstrip("?")
        parts    = spec.split(":")
        name     = parts[0]
        type_    = parts[1].split("=")[0] if len(parts) > 1 else "str"
        default  = parts[1].split("=")[1] if len(parts) > 1 and "=" in parts[1] else None

        # Last required arg swallows all remaining argv (for multiline content)
        is_last  = i == len(specs) - 1
        if is_last and not optional and len(raw_args) > i:
            val = " ".join(raw_args[i:])
        elif i < len(raw_args):
            val = raw_args[i]
        elif optional:
            val = default
        else:
            print(f"❌ Missing required argument: <{name}>")
            _, sp = REGISTRY[cmd]
            usage = " ".join(
                f"[{s.lstrip('?').split(':')[0]}]" if s.startswith("?") else f"<{s.split(':')[0]}>"
                for s in sp
            )
            print(f"   Usage: python pynav.py {cmd} {usage}"); return

        if val is not None and type_ == "int":
            try:
                val = int(val)
            except ValueError:
                print(f"❌ <{name}> must be an integer, got: {val!r}"); return

        parsed.append(val)

    try:
        func(*parsed)
    except FileNotFoundError as e:
        print(f"❌ File not found: {e.filename}")
    except SyntaxError as e:
        print(f"❌ Syntax error in {e.filename} at L{e.lineno}: {e.msg}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()
