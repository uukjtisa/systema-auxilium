---
name: pynavigator
description: map and analyze a Python codebase's structure, symbols, imports, and call graph
---

# PyNavigator

A code-intelligence toolkit for Python. Its job is the **navigation map**: the
project's symbol graph, import topology, call hierarchy, and structural outline —
the questions plain file reading can't answer cheaply.

> **Reading, editing, writing, and text search are NOT this skill's job.** YOU
> already have first-class tools for those — use them directly:
> - **`read_file`** — read a file (or a line window) as numbered lines.
> - **`edit_file`** — surgical anchor / line-range edits.
> - **`write_file`** — create or fully rewrite a file.
> - **`grep`** — ripgrep-style regex search across the tree.
>
> Reach for PyNavigator when you need to understand *structure and impact*
> (what exists, what imports what, who calls what), then use the native tools to
> actually read and change the code.

---

## Setup

Two scripts work together — both must be present in the same directory:

```
scripts/
├── pynav.py      ← CLI entry point (all commands)
└── indexer.py    ← code intelligence engine (imported by pynav.py)
```

All commands run through:

```python
import subprocess, sys
script = rf"{skills_path}\pynavigator\scripts\pynav.py"
result = subprocess.run([sys.executable, script, "<command>", "<args...>"],
                        capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
if result.returncode != 0:
    print("SKILL ERROR:", result.stderr)
```

`python pynav.py help` lists every command.

---

## Recommended Workflow

### First time on a codebase
```bash
python pynav.py index <root>         # build the index — do this first
python pynav.py symbol_map <root>    # whole project structure in one shot
python pynav.py list_imports <root>  # every dependency, classified
```

### Then, to work on a change
1. **Orient** — `symbol_map` / `outline <file>` for structure.
2. **Locate** — `goto_declaration`, `find_function`, `find_class`, `find_method`.
3. **Assess impact** — `who_imports`, `dependency_tree`, `call_hierarchy`, `find_references`.
4. **Read the code** — with **`read_file`** (native), guided by the line numbers PyNavigator reported.
5. **Change the code** — with **`edit_file`** / **`write_file`** (native).
6. **Search for text** — with **`grep`** (native).
7. **Sanity-check** — `dead_code` when refactoring, `audit_deps` when touching requirements.

---

## Commands (structure & intelligence only)

### STRUCTURE / LOCATE
| Command | Args | Purpose |
|---|---|---|
| `outline` | `<file>` | File skeleton: classes + methods + functions, no bodies |
| `find_function` | `<file> <n>` | Location (L start–end) of a function |
| `find_class` | `<file> <n>` | Location of a class |
| `find_method` | `<file> <class> <method>` | Location of a method |
| `get_signature` | `<file> <n>` | def line + docstring only (interface, not body) |

> To read the located code, call **`read_file`** with the reported line range.

### SYMBOL GRAPH / IMPACT
| Command | Args | Purpose |
|---|---|---|
| `goto_declaration` | `<root> <n>` | Where a name is defined (def/class/assign) |
| `find_references` | `<root> <n>` | Import & usage references across files |
| `call_hierarchy` | `<file> <target>` | Functions in a file that call target |
| `who_imports` | `<root> <module>` | Which files import a given module |
| `dependency_tree` | `<root> <file>` | Full recursive import chain for a file |
| `dead_code` | `<root>` | Symbols defined but never called (hints, not facts) |

### INDEX / IMPORTS
| Command | Args | Purpose |
|---|---|---|
| `index` | `<root>` | Build/refresh `.pynavigator/index.json` (the brain) |
| `index_status` | `<root>` | Staleness, file/symbol counts, last indexed |
| `symbol_map` | `<root>` | Every file with classified imports, classes, functions, signatures |
| `list_imports` | `<root>` | Every module classified stdlib / third-party / local, with usage |
| `get_dependencies` | `<root>` | Third-party packages only — what belongs in requirements.txt |
| `audit_deps` | `<root>` | Cross-check detected imports against requirements.txt |

### METRICS / SAFETY
| Command | Args | Purpose |
|---|---|---|
| `line_count` | `<file>` | Lines in one file (total/code/comment/blank) |
| `line_count_project` | `<root>` | Project table sorted by size |
| `validate` | `<file>` | Syntax-check a file without executing it |

---

## Agent Tips

- `symbol_map` + `list_imports` at session start = full situational awareness, zero source files read.
- `get_signature` before reading a whole function when you only need the interface.
- `call_hierarchy` / `find_references` before deleting or renaming — confirm impact first.
- Use `goto_declaration` to jump to a definition, then **`read_file`** at that line range.
- For text/regex search use **`grep`**, not a PyNavigator command.
- For any edit use **`edit_file`** / **`write_file`**; run `validate <file>` afterward.
- `audit_deps` before any dependency change; `dead_code` is a hint list (false positives on polymorphic calls).
