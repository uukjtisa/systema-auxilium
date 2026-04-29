"""
indexer.py — PyNavigator's persistent code intelligence engine.

Builds and maintains a .pynavigator/index.json containing:
  - Full symbol table (functions, classes, methods) with signatures + docstrings
  - Call graph  (what each function calls)
  - Import graph (what each file imports)
  - Cross-references (where each symbol is used across files)

Threading model:
  - Queries always return immediately from the cached index
  - Stale-file detection triggers a background thread to re-index dirty files
  - Atomic writes (temp → rename) prevent corrupt reads mid-update

Update strategy (balanced):
  - On edit: re-index the edited file + all files that import it
  - Background thread handles the rest without blocking the agent
"""

import ast
import os
import json
import time
import threading
import tempfile
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime


# ─── CONSTANTS ───────────────────────────────────────────────────────────────

INDEX_DIR  = ".pynavigator"
INDEX_FILE = "index.json"
SKIP_DIRS  = {"__pycache__", ".git", ".venv", "venv", "env", "node_modules",
               ".pynavigator", "dist", "build", ".eggs"}

ANSI_CYAN   = "\033[36m"
ANSI_GREEN  = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED    = "\033[31m"
ANSI_DIM    = "\033[2m"
ANSI_BOLD   = "\033[1m"
ANSI_RESET  = "\033[0m"


# ─── STDLIB DETECTION ────────────────────────────────────────────────────────
# Primary: sys.stdlib_module_names (Python 3.10+, perfectly accurate).
# Fallback: comprehensive hardcoded set covering 3.7–3.12 for older Pythons.

def _build_stdlib_set() -> frozenset:
    import sys
    if hasattr(sys, "stdlib_module_names"):
        return frozenset(sys.stdlib_module_names)
    return frozenset({
        "__future__", "_thread", "abc", "aifc", "argparse", "array", "ast",
        "asynchat", "asyncio", "asyncore", "atexit", "audioop", "base64",
        "bdb", "binascii", "binhex", "bisect", "builtins", "bz2", "calendar",
        "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs", "codeop",
        "colorsys", "compileall", "concurrent", "configparser", "contextlib",
        "contextvars", "copy", "copyreg", "cProfile", "csv", "ctypes",
        "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib",
        "dis", "distutils", "doctest", "email", "encodings", "enum",
        "errno", "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch",
        "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
        "gettext", "glob", "grp", "gzip", "hashlib", "heapq", "hmac",
        "html", "http", "idlelib", "imaplib", "imghdr", "importlib",
        "inspect", "io", "ipaddress", "itertools", "json", "keyword",
        "lib2to3", "linecache", "locale", "logging", "lzma", "mailbox",
        "mailcap", "marshal", "math", "mimetypes", "mmap", "modulefinder",
        "multiprocessing", "netrc", "nis", "nntplib", "numbers", "operator",
        "optparse", "os", "ossaudiodev", "pathlib", "pdb", "pickle",
        "pickletools", "pipes", "pkgutil", "platform", "plistlib", "poplib",
        "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd",
        "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random",
        "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
        "sched", "secrets", "select", "selectors", "shelve", "shlex",
        "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr",
        "socket", "socketserver", "spwd", "sqlite3", "ssl", "stat",
        "statistics", "string", "stringprep", "struct", "subprocess",
        "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
        "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
        "threading", "time", "timeit", "tkinter", "token", "tokenize",
        "tomllib", "trace", "traceback", "tracemalloc", "tty", "turtle",
        "types", "typing", "unicodedata", "unittest", "urllib", "uu",
        "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
        "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
        "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
    })

STDLIB_MODULES: frozenset = _build_stdlib_set()


def _classify_import(module: str, local_stems: set) -> str:
    """Returns 'stdlib', 'local', or 'third-party'."""
    if module in STDLIB_MODULES:
        return "stdlib"
    if module in local_stems:
        return "local"
    return "third-party"


# ─── DATA STRUCTURES ─────────────────────────────────────────────────────────

@dataclass
class SymbolRecord:
    kind:          str            # "function" | "class" | "method"
    file:          str            # relative path
    line:          int
    end_line:      int
    signature:     str            # full def/class line
    docstring:     str            = ""
    calls:         List[str]      = field(default_factory=list)   # names this symbol calls
    called_by:     List[str]      = field(default_factory=list)   # names that call this symbol
    methods:       List[str]      = field(default_factory=list)   # class → method names
    parent_class:  Optional[str]  = None                          # method → parent class
    referenced_in: List[str]      = field(default_factory=list)   # files that reference this name


@dataclass
class FileRecord:
    path:           str          # relative path
    mtime:          float        # last modified time
    imports:        List[str]    = field(default_factory=list)  # module stems (backward compat)
    symbols:        List[str]    = field(default_factory=list)  # top-level symbol names
    import_details: List[dict]   = field(default_factory=list)  # rich per-import info (v3+)


@dataclass
class IndexMeta:
    project_root:  str
    indexed_at:    str
    file_count:    int  = 0
    symbol_count:  int  = 0
    index_version: str  = "2"


class CodeIndex:
    """
    In-memory representation of the project index.
    Serialises to / deserialises from index.json.
    """

    def __init__(self, root: str):
        self.root        = str(Path(root).resolve())
        self.meta        = IndexMeta(project_root=self.root, indexed_at="")
        self.files:   Dict[str, FileRecord]   = {}
        self.symbols: Dict[str, SymbolRecord] = {}
        self.call_graph:   Dict[str, List[str]] = {}   # caller → [callees]
        self.import_graph: Dict[str, List[str]] = {}   # file   → [modules]
        self._lock = threading.RLock()

    # ── Persistence ──────────────────────────────────────────────────────────

    @property
    def index_path(self) -> Path:
        return Path(self.root) / INDEX_DIR / INDEX_FILE

    def save(self):
        """Atomic write: serialise to temp file then rename."""
        with self._lock:
            self.meta.file_count   = len(self.files)
            self.meta.symbol_count = len(self.symbols)
            self.meta.indexed_at   = datetime.now().isoformat(timespec="seconds")

            data = {
                "meta":         asdict(self.meta),
                "files":        {k: asdict(v) for k, v in self.files.items()},
                "symbols":      {k: asdict(v) for k, v in self.symbols.items()},
                "call_graph":   self.call_graph,
                "import_graph": self.import_graph,
            }

            idx_path = self.index_path
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = idx_path.with_suffix(".tmp.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, idx_path)   # atomic on POSIX + Windows

    def load(self) -> bool:
        """Load index from disk. Returns False if not found or corrupt."""
        if not self.index_path.exists():
            return False
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                m = data.get("meta", {})
                self.meta = IndexMeta(**{k: m[k] for k in IndexMeta.__dataclass_fields__ if k in m})
                self.files = {
                    k: FileRecord(**v) for k, v in data.get("files", {}).items()
                }
                self.symbols = {
                    k: SymbolRecord(**v) for k, v in data.get("symbols", {}).items()
                }
                self.call_graph   = data.get("call_graph",   {})
                self.import_graph = data.get("import_graph", {})
            return True
        except Exception:
            return False

    # ── Staleness ────────────────────────────────────────────────────────────

    def stale_files(self) -> List[str]:
        """Return relative paths of files that are newer than their index entry."""
        stale = []
        for rel, rec in self.files.items():
            abs_path = os.path.join(self.root, rel)
            try:
                if os.path.getmtime(abs_path) > rec.mtime + 0.01:
                    stale.append(rel)
            except FileNotFoundError:
                stale.append(rel)

        # Also pick up new files not in index yet
        for abs_path in _find_py_files(self.root):
            rel = os.path.relpath(abs_path, self.root)
            if rel not in self.files:
                stale.append(rel)
        return stale

    def is_fresh(self) -> bool:
        return len(self.stale_files()) == 0

    # ── Query helpers ─────────────────────────────────────────────────────────

    def lookup(self, name: str) -> Optional[SymbolRecord]:
        with self._lock:
            return self.symbols.get(name)

    def symbols_in_file(self, rel_path: str) -> List[str]:
        rec = self.files.get(rel_path)
        return rec.symbols if rec else []

    def files_importing(self, module_name: str) -> List[str]:
        return [f for f, mods in self.import_graph.items() if module_name in mods]

    def dead_symbols(self) -> List[Tuple[str, SymbolRecord]]:
        """
        Symbols that are:
          - defined in the project
          - never appear in any call graph as a callee
          - not referenced in any other file
          - not a dunder, not a class, not __main__ entry
        """
        all_callees: Set[str] = set()
        for callees in self.call_graph.values():
            all_callees.update(callees)

        dead = []
        for name, rec in self.symbols.items():
            if name.startswith("__"):
                continue
            if rec.kind == "class":
                continue
            if not rec.referenced_in and name not in all_callees and not rec.called_by:
                dead.append((name, rec))
        return sorted(dead, key=lambda x: x[1].file)


# ─── AST EXTRACTION ──────────────────────────────────────────────────────────

def _find_py_files(root: str) -> List[str]:
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                result.append(os.path.join(dirpath, fn))
    return sorted(result)


def _local_stems(root: str, files: List[str] = None) -> set:
    """
    Build the complete set of local module/package names for a project.
    Includes both:
      - .py file stems  (e.g. 'ai_engine', 'controller')
      - package dirs    (any directory containing __init__.py, e.g. 'core', 'ui')
    This prevents package directories from being misclassified as third-party.
    """
    if files is None:
        files = _find_py_files(root)
    stems = {Path(f).stem for f in files}
    # Add any directory that is a Python package (contains __init__.py)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        if "__init__.py" in filenames:
            stems.add(Path(dirpath).name)
    return stems


def _signature(node: ast.AST, src_lines: List[str]) -> str:
    """Extract the def/class line(s) as a clean string."""
    line = src_lines[node.lineno - 1].rstrip()
    # Multi-line signatures (args across lines) – grab up to the colon
    i = node.lineno
    while ":" not in line and i < len(src_lines):
        line += " " + src_lines[i].strip()
        i += 1
    return line.strip()


def _docstring(node: ast.AST) -> str:
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr):
        val = body[0].value
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            return val.value.strip().splitlines()[0][:120]   # first line, capped
    return ""


def _calls_in(node: ast.AST) -> List[str]:
    """All function/method names called directly inside node."""
    names: List[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Name):
                names.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.append(fn.attr)
    return list(dict.fromkeys(names))   # dedup, preserve order


def _imports_in(tree: ast.Module, src_lines: List[str] = None,
                local_stems: set = None) -> tuple:
    """
    Returns (module_stems: List[str], import_details: List[dict]).
    import_details entries: {module, names, kind, raw}
    """
    if local_stems is None:
        local_stems = set()
    stems   = []
    details = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stem = alias.name.split(".")[0]
                if stem not in stems:
                    stems.append(stem)
                raw  = f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else "")
                details.append({
                    "module": stem,
                    "names":  [],
                    "kind":   _classify_import(stem, local_stems),
                    "raw":    raw,
                })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                stem = node.module.split(".")[0]
                if stem not in stems:
                    stems.append(stem)
                names = [alias.name for alias in node.names if alias.name != "*"]
                raw   = f"from {node.module} import {', '.join(n.name if hasattr(n,'name') else str(n) for n in node.names)}"
                details.append({
                    "module": stem,
                    "names":  names,
                    "kind":   _classify_import(stem, local_stems),
                    "raw":    raw,
                })
    return list(dict.fromkeys(stems)), details


def _references_to(name: str, tree: ast.Module) -> bool:
    """Does this file reference `name` at all?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


def extract_file(abs_path: str, root: str,
                 local_stems: set = None) -> Tuple[FileRecord, Dict[str, SymbolRecord]]:
    """
    Parse one Python file. Returns (FileRecord, {name: SymbolRecord}).
    Raises SyntaxError if the file can't be parsed.
    local_stems: set of project module names for import classification.
    """
    rel = os.path.relpath(abs_path, root)
    with open(abs_path, encoding="utf-8", errors="replace") as f:
        src = f.read()
    src_lines = src.splitlines()
    tree      = ast.parse(src, filename=abs_path)

    imports, import_details = _imports_in(tree, src_lines, local_stems or set())
    mtime    = os.path.getmtime(abs_path)
    file_rec = FileRecord(path=rel, mtime=mtime, imports=imports,
                          import_details=import_details)
    symbols: Dict[str, SymbolRecord] = {}

    FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

    for node in ast.iter_child_nodes(tree):

        # ── Top-level function ────────────────────────────────────────────
        if isinstance(node, FUNC_TYPES):
            name = node.name
            dec_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            rec = SymbolRecord(
                kind      = "function",
                file      = rel,
                line      = dec_line,
                end_line  = node.end_lineno,
                signature = _signature(node, src_lines),
                docstring = _docstring(node),
                calls     = _calls_in(node),
            )
            symbols[name] = rec
            file_rec.symbols.append(name)

        # ── Class ─────────────────────────────────────────────────────────
        elif isinstance(node, ast.ClassDef):
            cls_name = node.name
            dec_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            method_names = []

            for item in node.body:
                if isinstance(item, FUNC_TYPES):
                    mname    = item.name
                    full_key = f"{cls_name}.{mname}"
                    mdec     = item.decorator_list[0].lineno if item.decorator_list else item.lineno
                    mrec = SymbolRecord(
                        kind         = "method",
                        file         = rel,
                        line         = mdec,
                        end_line     = item.end_lineno,
                        signature    = _signature(item, src_lines),
                        docstring    = _docstring(item),
                        calls        = _calls_in(item),
                        parent_class = cls_name,
                    )
                    symbols[full_key] = mrec
                    method_names.append(mname)

            cls_rec = SymbolRecord(
                kind      = "class",
                file      = rel,
                line      = dec_line,
                end_line  = node.end_lineno,
                signature = _signature(node, src_lines),
                docstring = _docstring(node),
                methods   = method_names,
            )
            symbols[cls_name] = cls_rec
            file_rec.symbols.append(cls_name)

    return file_rec, symbols


# ─── INDEXER (builds + updates CodeIndex) ────────────────────────────────────

class Indexer:
    """
    Owns a CodeIndex and knows how to build/update it.
    Thread-safe: background updates run in a daemon thread.
    """

    def __init__(self, root: str):
        self.root  = str(Path(root).resolve())
        self.index = CodeIndex(self.root)
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_lock   = threading.Lock()

    # ── Full build ────────────────────────────────────────────────────────────

    def build(self, verbose: bool = True) -> CodeIndex:
        """
        Full index build. Blocks until complete.
        Called by `pynav.py index <root>`.
        """
        files    = _find_py_files(self.root)
        n        = len(files)
        all_syms: Dict[str, SymbolRecord] = {}
        all_recs: Dict[str, FileRecord]   = {}

        if verbose:
            print(f"\n{ANSI_CYAN}⚙  Indexing {n} file(s)...{ANSI_RESET}")

        # Compute local module stems for import classification
        local_stems = _local_stems(self.root, files)

        errors = 0
        for i, abs_path in enumerate(files, 1):
            rel = os.path.relpath(abs_path, self.root)
            if verbose:
                print(f"  {ANSI_DIM}[{i:>3}/{n}] {rel}{ANSI_RESET}", end="\r", flush=True)
            try:
                frec, syms = extract_file(abs_path, self.root, local_stems)
                all_recs[rel] = frec
                all_syms.update(syms)
            except SyntaxError as e:
                errors += 1
                if verbose:
                    print(f"\n  {ANSI_YELLOW}⚠  Skipping {rel}: {e.msg} L{e.lineno}{ANSI_RESET}")
            except Exception as e:
                errors += 1

        with self.index._lock:
            self.index.files        = all_recs
            self.index.symbols      = all_syms
            self.index.call_graph   = {n: s.calls for n, s in all_syms.items() if s.calls}
            self.index.import_graph = {r: f.imports for r, f in all_recs.items()}

        # Cross-reference pass
        self._build_cross_refs()
        self.index.save()

        if verbose:
            print(f"\n{ANSI_GREEN}✅ Indexed {n} files, "
                  f"{len(all_syms)} symbols"
                  f"{f', {errors} error(s)' if errors else ''}.{ANSI_RESET}")
            print(f"   {ANSI_DIM}{self.index.index_path}{ANSI_RESET}")

        return self.index

    def _build_cross_refs(self):
        """
        Populate `called_by` and `referenced_in` for every symbol.
        Run after all files are parsed.
        """
        with self.index._lock:
            # Reset
            for rec in self.index.symbols.values():
                rec.called_by     = []
                rec.referenced_in = []

            # called_by: invert the call graph
            for caller, callees in self.index.call_graph.items():
                for callee in callees:
                    if callee in self.index.symbols:
                        self.index.symbols[callee].called_by.append(caller)

            # referenced_in: which files mention each symbol name
            name_set = set(self.index.symbols.keys())
            for rel, frec in self.index.files.items():
                abs_path = os.path.join(self.root, rel)
                try:
                    with open(abs_path, encoding="utf-8", errors="replace") as f:
                        src = f.read()
                    tree = ast.parse(src)
                    for sym_name in name_set:
                        base = sym_name.split(".")[0]   # handle Class.method
                        if _references_to(base, tree):
                            sym = self.index.symbols[sym_name]
                            if rel != sym.file and rel not in sym.referenced_in:
                                sym.referenced_in.append(rel)
                except Exception:
                    pass

    # ── Partial update ────────────────────────────────────────────────────────

    def update_file(self, abs_path: str):
        """
        Re-index one file + all files that import it (balanced strategy).
        Called immediately after any edit command.
        """
        rel        = os.path.relpath(abs_path, self.root)
        stem       = Path(abs_path).stem   # module name (no .py)
        to_reindex = {rel}

        # Find all files that import this module
        for other_rel, imports in self.index.import_graph.items():
            if stem in imports:
                to_reindex.add(other_rel)

        self._reindex_files(list(to_reindex), announce=False)

    def _reindex_files(self, rel_paths: List[str], announce: bool = True):
        """Re-index a specific list of files and rebuild cross-refs."""
        if announce and rel_paths:
            print(f"  {ANSI_DIM}↺  Re-indexing {len(rel_paths)} file(s)...{ANSI_RESET}",
                  end="", flush=True)

        updated = 0
        with self.index._lock:
            for rel in rel_paths:
                abs_path = os.path.join(self.root, rel)
                # Remove stale symbols for this file
                self.index.symbols = {
                    k: v for k, v in self.index.symbols.items()
                    if v.file != rel
                }
                if not os.path.exists(abs_path):
                    self.index.files.pop(rel, None)
                    continue
                try:
                    local_stems = _local_stems(self.root)
                    frec, syms = extract_file(abs_path, self.root, local_stems)
                    self.index.files[rel]   = frec
                    self.index.symbols.update(syms)
                    updated += 1
                except Exception:
                    pass

            # Rebuild derived structures
            self.index.call_graph   = {n: s.calls for n, s in self.index.symbols.items() if s.calls}
            self.index.import_graph = {r: f.imports for r, f in self.index.files.items()}

        self._build_cross_refs()
        self.index.save()

        if announce and rel_paths:
            print(f" {ANSI_GREEN}done.{ANSI_RESET}")

    # ── Background staleness resolution ───────────────────────────────────────

    def maybe_reindex_background(self) -> List[str]:
        """
        Check for stale files. If found, start a background thread.
        Returns list of stale files (for informational warning to agent).
        Non-blocking.
        """
        stale = self.index.stale_files()
        if not stale:
            return []

        with self._bg_lock:
            if self._bg_thread and self._bg_thread.is_alive():
                return stale   # already running

            t = threading.Thread(
                target=self._bg_reindex,
                args=(stale,),
                daemon=True,
                name="pynav-indexer",
            )
            self._bg_thread = t
            t.start()

        return stale

    def _bg_reindex(self, stale: List[str]):
        try:
            self._reindex_files(stale, announce=False)
        except Exception:
            pass   # background thread must never crash the process

    def wait_for_background(self, timeout: float = 30.0):
        """Block until background thread finishes (used by index_status)."""
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=timeout)


# ─── PUBLIC ENTRY POINTS (called by pynav.py) ────────────────────────────────

def ensure_index(root: str, auto_init: bool = True) -> Optional[CodeIndex]:
    """
    Load or create the index for `root`.
    If no index exists and auto_init=True, build it now (with warning).
    Triggers background thread if stale files found.
    Returns the CodeIndex, or None if unavailable.
    """
    idx = Indexer(root)

    if not idx.index.load():
        if not auto_init:
            return None
        print(f"\n{ANSI_YELLOW}⚠  No index found for this project.{ANSI_RESET}")
        print(f"   {ANSI_DIM}Auto-initialising — run `pynav.py index <root>` "
              f"once to avoid this delay.{ANSI_RESET}\n")
        idx.build(verbose=True)
        return idx.index

    stale = idx.maybe_reindex_background()
    if stale:
        print(f"  {ANSI_DIM}↺  {len(stale)} file(s) stale — "
              f"re-indexing in background...{ANSI_RESET}")

    return idx.index


def update_index_after_edit(root: str, abs_path: str):
    """
    Called by pynav.py after every edit command.
    Patches the edited file + its importers without a full rebuild.
    """
    idx = Indexer(root)
    if idx.index.load():
        idx.update_file(abs_path)


# ─── QUERY FORMATTERS (used by pynav.py commands) ────────────────────────────

RESET = ANSI_RESET
DIM   = ANSI_DIM
BOLD  = ANSI_BOLD
CYAN  = ANSI_CYAN
GREEN = ANSI_GREEN
YELLOW= ANSI_YELLOW
RED   = ANSI_RED




def fmt_index_status(index: CodeIndex, stale: List[str]) -> str:
    lines = [
        f"{BOLD}── Index Status ──────────────────────────────────────{RESET}",
        f"  Root:      {index.root}",
        f"  Files:     {index.meta.file_count}",
        f"  Symbols:   {index.meta.symbol_count}",
        f"  Indexed:   {index.meta.indexed_at}",
        f"  Status:    {GREEN+'✅ Fresh' if not stale else YELLOW+f'⚠ {len(stale)} stale file(s)'}{RESET}",
    ]
    if stale:
        for f in stale[:10]:
            lines.append(f"    {DIM}• {f}{RESET}")
        if len(stale) > 10:
            lines.append(f"    {DIM}  … and {len(stale)-10} more{RESET}")
    return "\n".join(lines)


def fmt_dead_code(dead: List[Tuple[str, SymbolRecord]]) -> str:
    if not dead:
        return f"{GREEN}✅ No dead code detected.{RESET}"
    lines = [
        f"{BOLD}── Dead Code ({len(dead)} symbol(s)) ──────────────────────{RESET}",
        f"  {DIM}Defined but never called or referenced externally.{RESET}\n",
    ]
    current_file = None
    for name, rec in dead:
        if rec.file != current_file:
            lines.append(f"  {CYAN}{rec.file}{RESET}")
            current_file = rec.file
        lines.append(f"    {rec.line:>5} │ {DIM}{rec.kind}{RESET}  {name}")
    return "\n".join(lines)


def fmt_dependency_tree(index: CodeIndex, rel_path: str, depth: int = 0,
                        seen: Optional[Set[str]] = None) -> str:
    if seen is None:
        seen = set()
    if rel_path in seen or depth > 6:
        return ""
    seen.add(rel_path)

    indent  = "  " * depth
    imports = index.import_graph.get(rel_path, [])
    lines   = [f"{indent}{CYAN}{rel_path}{RESET}"]

    for mod in imports:
        # Try to resolve to a project file
        candidates = [f for f in index.files if Path(f).stem == mod]
        if candidates:
            sub = fmt_dependency_tree(index, candidates[0], depth + 1, seen)
            if sub:
                lines.append(sub)
        else:
            lines.append(f"  {indent}{DIM}{mod}  (external){RESET}")

    return "\n".join(lines)


def fmt_symbol_map(index: CodeIndex, show_methods: bool = False) -> str:
    """
    Compact full-project topology dump.
    Designed to be pasted into an AI context window in one shot.
    Now shows classified imports per file header.
    """
    lines = [
        f"{BOLD}── Symbol Map: {Path(index.root).name} ──────────────────────────{RESET}",
        f"  {DIM}{index.meta.file_count} files  •  {index.meta.symbol_count} symbols  "
        f"•  indexed {index.meta.indexed_at}{RESET}\n",
    ]

    for rel, frec in sorted(index.files.items()):
        lines.append(f"{CYAN}  {rel}{RESET}")

        # ── Import summary for this file ──────────────────────────────────
        if frec.import_details:
            third  = [d for d in frec.import_details if d.get("kind") == "third-party"]
            stdlib = [d for d in frec.import_details if d.get("kind") == "stdlib"]
            local  = [d for d in frec.import_details if d.get("kind") == "local"]
            parts  = []
            if third:
                mods = list(dict.fromkeys(d["module"] for d in third))
                parts.append(f"{YELLOW}third-party:{RESET} {', '.join(mods)}")
            if local:
                mods = list(dict.fromkeys(d["module"] for d in local))
                parts.append(f"{GREEN}local:{RESET} {', '.join(mods)}")
            if stdlib:
                mods = list(dict.fromkeys(d["module"] for d in stdlib))
                parts.append(f"{DIM}stdlib: {', '.join(mods)}{RESET}")
            if parts:
                lines.append(f"    {DIM}imports →{RESET} " + "  |  ".join(parts))
        elif frec.imports:
            lines.append(f"    {DIM}imports: {', '.join(frec.imports)}{RESET}")

        # ── Symbols ───────────────────────────────────────────────────────
        for sym_name in frec.symbols:
            rec = index.symbols.get(sym_name)
            if not rec:
                continue
            if rec.kind == "class":
                methods_str = ", ".join(rec.methods) if rec.methods else "—"
                lines.append(f"    {BOLD}class {sym_name}{RESET}  "
                             f"{DIM}L{rec.line}  methods: {methods_str}{RESET}")
                if show_methods:
                    for mname in rec.methods:
                        key  = f"{sym_name}.{mname}"
                        mrec = index.symbols.get(key)
                        if mrec:
                            lines.append(f"      {DIM}{mrec.signature}  L{mrec.line}{RESET}")
            else:
                calls_str = ", ".join(rec.calls[:4]) + ("…" if len(rec.calls) > 4 else "")
                lines.append(f"    {rec.signature}  {DIM}L{rec.line}  "
                             f"calls: [{calls_str}]{RESET}")
        lines.append("")

    return "\n".join(lines)


def fmt_list_imports(index: CodeIndex) -> str:
    """
    Full import picture: every module classified as stdlib/third-party/local,
    with frequency count and which files use it.
    """
    from collections import defaultdict
    # module -> {kind, files: [rel_path], names: set}
    agg: dict = defaultdict(lambda: {"kind": "third-party", "files": [], "names": set()})

    for rel, frec in index.files.items():
        for d in frec.import_details:
            mod  = d["module"]
            kind = d.get("kind", "third-party")
            agg[mod]["kind"] = kind
            if rel not in agg[mod]["files"]:
                agg[mod]["files"].append(rel)
            agg[mod]["names"].update(d.get("names", []))

    # If no import_details (old index), fall back to import_graph
    if not agg:
        for rel, mods in index.import_graph.items():
            for mod in mods:
                agg[mod]["files"].append(rel)

    # Group by kind
    groups = {"third-party": [], "local": [], "stdlib": []}
    for mod, info in sorted(agg.items()):
        groups[info["kind"]].append((mod, info))
    # Sort each group by file count desc
    for k in groups:
        groups[k].sort(key=lambda x: -len(x[1]["files"]))

    lines = [
        f"{BOLD}── Import Map: {Path(index.root).name} {'─'*38}{RESET}",
        f"  {DIM}All imports classified across {index.meta.file_count} files.{RESET}\n",
    ]

    labels = [
        ("third-party", YELLOW, "📦 Third-Party"),
        ("local",       GREEN,  "🏠 Local / Project"),
        ("stdlib",      DIM,    "🐍 Standard Library"),
    ]
    for kind, color, title in labels:
        items = groups.get(kind, [])
        if not items:
            continue
        lines.append(f"  {color}{BOLD}{title}  ({len(items)} module(s)){RESET}")
        for mod, info in items:
            bar   = "█" * min(len(info["files"]), 20)
            files = info["files"]
            names = sorted(info["names"])
            names_str = f"  {DIM}({', '.join(names[:5])}{'…' if len(names)>5 else ''}){RESET}" if names else ""
            lines.append(f"    {color}{mod:<28}{RESET} {DIM}{bar}{RESET} {len(files)} file(s){names_str}")
            for f in sorted(files)[:4]:
                lines.append(f"      {DIM}↳ {f}{RESET}")
            if len(files) > 4:
                lines.append(f"      {DIM}  … and {len(files)-4} more{RESET}")
        lines.append("")

    return "\n".join(lines)


def fmt_get_dependencies(index: CodeIndex) -> str:
    """
    Third-party packages only — what you'd put in requirements.txt.
    Sorted by usage frequency.
    """
    from collections import defaultdict
    deps: dict = defaultdict(list)

    for rel, frec in index.files.items():
        for d in frec.import_details:
            if d.get("kind") == "third-party":
                mod = d["module"]
                if rel not in deps[mod]:
                    deps[mod].append(rel)

    if not deps:
        # Fallback: guess from import_graph by exclusion
        local_stems = _local_stems(index.root)
        for rel, mods in index.import_graph.items():
            for mod in mods:
                if mod not in STDLIB_MODULES and mod not in local_stems:
                    if rel not in deps[mod]:
                        deps[mod].append(rel)

    if not deps:
        return f"{GREEN}✅ No third-party dependencies detected.{RESET}"

    sorted_deps = sorted(deps.items(), key=lambda x: -len(x[1]))
    lines = [
        f"{BOLD}── External Dependencies: {Path(index.root).name} {'─'*30}{RESET}",
        f"  {DIM}{len(sorted_deps)} package(s) detected — suitable for requirements.txt{RESET}\n",
    ]
    for mod, files in sorted_deps:
        bar  = "█" * min(len(files), 15)
        lines.append(f"  {YELLOW}{mod:<30}{RESET}  {DIM}{bar}{RESET}  {len(files)} file(s)")
        for f in sorted(files):
            lines.append(f"    {DIM}↳ {f}{RESET}")
    lines.append(f"\n  {DIM}Hint: run `pip freeze | grep -iF <name>` to get pinned version.{RESET}")
    return "\n".join(lines)


def fmt_audit_deps(index: CodeIndex, root: str) -> str:
    """
    Cross-reference detected third-party imports against requirements.txt.
    Flags: missing from requirements, and in requirements but never imported.
    """
    from collections import defaultdict
    import re as _re

    req_path = Path(root) / "requirements.txt"
    if not req_path.exists():
        return f"{YELLOW}⚠  No requirements.txt found at {req_path}{RESET}"

    # Parse requirements.txt — strip version specifiers, extras, comments
    req_pkgs: set = set()
    for line in req_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip extras [extra], version specifiers, URLs
        pkg = _re.split(r"[>=<!\[;@ \t]", line)[0].strip().lower()
        if pkg:
            # Normalise: PyQt6 → pyqt6, sentence-transformers → sentence_transformers
            req_pkgs.add(pkg.replace("-", "_"))

    # Detected third-party imports
    detected: dict = defaultdict(list)
    for rel, frec in index.files.items():
        for d in frec.import_details:
            if d.get("kind") == "third-party":
                mod = d["module"].lower().replace("-", "_")
                if rel not in detected[mod]:
                    detected[mod].append(rel)

    # Fallback if no import_details
    if not detected:
        local_stems = _local_stems(index.root)
        for rel, mods in index.import_graph.items():
            for mod in mods:
                nm = mod.lower().replace("-", "_")
                if nm not in STDLIB_MODULES and mod not in local_stems:
                    if rel not in detected[nm]:
                        detected[nm].append(rel)

    imported_pkgs = set(detected.keys())

    missing  = imported_pkgs - req_pkgs    # used in code, NOT in requirements
    unused   = req_pkgs - imported_pkgs    # in requirements, NOT found in code

    lines = [
        f"{BOLD}── Dependency Audit: {Path(root).name} {'─'*35}{RESET}",
        f"  {DIM}requirements.txt: {len(req_pkgs)} package(s)  "
        f"|  detected imports: {len(imported_pkgs)} package(s){RESET}\n",
    ]

    if not missing and not unused:
        lines.append(f"  {GREEN}✅ Everything checks out — imports match requirements.txt{RESET}")
        return "\n".join(lines)

    if missing:
        lines.append(f"  {RED}{BOLD}❌ Imported but missing from requirements.txt "
                     f"({len(missing)}){RESET}")
        for mod in sorted(missing):
            files = detected.get(mod, [])
            lines.append(f"    {RED}{mod}{RESET}")
            for f in sorted(files):
                lines.append(f"      {DIM}↳ {f}{RESET}")
        lines.append("")

    if unused:
        lines.append(f"  {YELLOW}{BOLD}⚠  In requirements.txt but never imported "
                     f"({len(unused)}){RESET}")
        for pkg in sorted(unused):
            lines.append(f"    {YELLOW}{pkg}{RESET}  {DIM}(possibly transitive dep or unused){RESET}")

    return "\n".join(lines)

