---
name: pynavigator
description: >
  Context-efficient Python code navigation and editing for agentic coding tasks.
  Provides surgical read/write tools AND a persistent code intelligence index.
---

# PyNavigator

A surgical code navigation, editing, and intelligence toolkit for Python.
Gives You precise line-referenced views into code without loading whole
files, safe in-place editing with automatic backups, and a persistent project
index that understands the full symbol graph and import topology.

**The golden rule:** Never read an entire Python file when
`pynav.py` can give you exactly the slice you need.

---

## Setup

Two scripts work together — both must be present in the same directory:

```
scripts/
├── pynav.py      ← CLI entry point (all commands)
└── indexer.py    ← code intelligence engine (imported by pynav.py)
```

All commands run through:

```bash
python /path/to/scripts/pynav.py <command> [args...]
python /path/to/scripts/pynav.py help      # full command list
```

---

## Recommended Workflow

### First time on any new codebase (mandatory)

```bash
python pynav.py index <root>         # ← the "git init" moment — do this first
python pynav.py symbol_map <root>    # ← understand the whole project instantly
python pynav.py list_imports <root>  # ← see all dependencies classified
```

### Every coding session after that

1. **Orient** — `symbol_map` or `outline <file>` to see structure
2. **Understand imports** — `list_imports` or `get_dependencies` for dep awareness
3. **Locate** — `goto_declaration`, `find_function`, `find_class`
4. **Read surgically** — `read_function`, `read_method`, `get_signature`
5. **Understand impact** — `who_imports`, `dependency_tree`, `call_hierarchy`
6. **Preview before editing** — `diff_preview` for any non-trivial change
7. **Edit** — `edit_lines`, `insert_after`, `insert_before`, `delete_block`
8. **Verify** — `validate <file>` after every edit
9. **Check for cleanup** — `dead_code` when refactoring
10. **Audit deps** — `audit_deps` when touching requirements.txt

---

## Commands Reference

### READ / NAVIGATE

| Command | Args | Purpose |
|---|---|---|
| `outline` | `<file>` | File skeleton: all classes + methods + functions, no bodies |
| `find_function` | `<file> <n>` | Location (L start–end) of a function |
| `find_class` | `<file> <n>` | Location of a class |
| `find_method` | `<file> <class> <method>` | Location of a method |
| `read_function` | `<file> <n>` | Full function body with │ line numbers |
| `read_class` | `<file> <n>` | Full class body with │ line numbers |
| `read_method` | `<file> <class> <method>` | Full method body with │ line numbers |
| `read_lines` | `<file> <start> <end>` | Arbitrary line range with │ numbers |
| `get_signature` | `<file> <n>` | def line + docstring only (smallest possible read) |
| `get_context` | `<file> <line> [n=10]` | N lines around a target line |

All read outputs include │ line numbers for precise follow-up edits.

---

### SEARCH / ANALYSIS

| Command | Args | Purpose |
|---|---|---|
| `search` | `<root> <pattern>` | Regex search across all .py files |
| `find_usages` | `<root> <n>` | All occurrences of identifier |
| `find_references` | `<root> <n>` | Import & usage references cross-file |
| `goto_declaration` | `<root> <n>` | Where name is defined (def/class/assign) |
| `call_hierarchy` | `<file> <target>` | All functions in file that call target |
| `find_todos` | `<root>` | All TODO / FIXME / HACK / BUG / NOTE |

---

### EDIT

> ⚠️ All edit commands auto-backup before writing. Claude will always be told
> the backup path. No edit is ever silent.

| Command | Args | Purpose |
|---|---|---|
| `diff_preview` | `<file> <start> <end> <new_content>` | Preview edit without applying |
| `edit_lines` | `<file> <start> <end> <new_content>` | Replace lines (1-indexed, inclusive) |
| `insert_after` | `<file> <target_name> <content>` | Insert after named block's last line |
| `insert_before` | `<file> <target_name> <content>` | Insert before named block's first line |
| `delete_block` | `<file> <n>` | Remove a named function/class entirely |
| `add_import` | `<file> <import_stmt>` | Safely add import (skips if already present) |
| `refactor` | `<root> <old_name> <new_name>` | Rename symbol (identifier) across whole project |
| `rename_file` | `<root> <old_module> <new_module>` | Rename a .py file + rewrite all imports across project |

> **`refactor` vs `rename_file`:** `refactor` is for symbols (function names, class names, variables). `rename_file` is for renaming a `.py` file on disk — it takes dotted module paths (e.g. `core.session_manager`), moves the file, and rewrites all `import` / `from … import` references across the whole project. Both auto-backup every touched file.

---

### METRICS

| Command | Args | Purpose |
|---|---|---|
| `line_count` | `<file>` | Lines in one file — total, code, comments, blank with bar chart |
| `line_count_project` | `<root>` | Full project table sorted by size, code/comment/blank columns |

---

### SAFETY

| Command | Args | Purpose |
|---|---|---|
| `validate` | `<file>` | Syntax-check without executing |

**Always run `validate` after any edit.**

---

### BACKUP MANAGEMENT

| Command | Args | Purpose |
|---|---|---|
| `backup_snapshot` | `<file> <label>` | Manually name a snapshot for human recovery |
| `list_backups` | `<root>` | Show all sessions and their snapshots |
| `new_session` | `<root>` | Start a fresh session folder |

Backups are stored at `.pynavigator/backups/session_YYYY-MM-DD_HHhMM/` as
named snapshots (`auth_before_edit_lines_L42-46.py`) plus a `changes.log`
audit trail. Three layers: session folder (A) + named snapshots (D) + diff
log (C). Stacks automatically: `_1`, `_2`, etc.

---

### INDEX  ★

> The index is PyNavigator's brain. Run `index <root>` once at the start
> of every session on a new codebase. All search/goto commands auto-trigger
> it if missing.

| Command | Args | Purpose |
|---|---|---|
| `index` | `<root>` | **The git-init moment.** Full project crawl → `.pynavigator/index.json` |
| `index_status` | `<root>` | Health check: staleness, file count, symbol count, last indexed |
| `symbol_map` | `<root>` | **Full codebase in one shot.** Every file with classified imports, classes, functions, signatures — instant codebase awareness without reading source |
| `who_imports` | `<root> <module>` | Which files import a given module |
| `dependency_tree` | `<root> <file>` | Full recursive import chain for a file |
| `dead_code` | `<root>` | Symbols defined but never called — cleanup opportunities |

How the index stays current: edits patch the edited file + all callers
instantly on the main thread. Stale files (detected by mtime) are re-indexed
in a background daemon thread — commands never block. Atomic writes (temp →
rename) mean the index is never corrupt mid-update.

---

### IMPORT AWARENESS  ★

> These commands answer the question `symbol_map` previously couldn't:
> *what does this project actually depend on, and is it correct?*

| Command | Args | Purpose |
|---|---|---|
| `list_imports` | `<root>` | **Full import picture.** Every module classified as `stdlib` / `third-party` / `local`, with frequency bar and which files use it |
| `get_dependencies` | `<root>` | Third-party packages only — what belongs in `requirements.txt`, sorted by usage frequency |
| `audit_deps` | `<root>` | Cross-reference detected imports against `requirements.txt`. Flags: missing from requirements, and in requirements but never imported |

#### Import classification

- **`third-party`** — packages requiring `pip install` (PyQt6, requests, numpy…)
- **`local`** — `.py` files inside the project itself
- **`stdlib`** — Python's standard library (os, threading, json…)

Uses `sys.stdlib_module_names` on Python 3.10+ for perfect accuracy, with a
comprehensive hardcoded fallback for older versions.

`audit_deps` catches real mismatches: `PIL` vs `pillow`, `speech_recognition`
vs `SpeechRecognition`, `webrtcvad` vs `webrtcvad_wheels`, and packages in
requirements.txt that are never actually imported (dead dependencies).

---

## Agent Tips

- `symbol_map` + `list_imports` at session start = complete situational awareness, zero source files read
- `get_signature` before `read_function` when you only need the interface, not the body
- `call_hierarchy` before deleting a function — confirm nothing calls it first
- After `refactor`, run `find_usages <root> <old_name>` to confirm no stragglers
- Use `rename_file` (not `refactor`) when renaming a `.py` file — it moves the file AND fixes all imports
- After `rename_file`, run `search <root> <old_module_name>` to catch any string literals that reference the old name
- `line_count_project` before refactoring — know which files are biggest and most worth splitting
- `audit_deps` before any dependency change — know what's real vs stale
- `dead_code` has false positives for polymorphic calls (`action.execute()`) — treat as hints, not facts
