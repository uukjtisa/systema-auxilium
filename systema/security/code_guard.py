"""
systema/security/code_guard.py

The code-execution safety layer, modeled on how mature AI-agent harnesses gate
tool use (static analysis -> policy -> informed approval -> audit):

  scan_code(code)            AST + text risk scanner -> list[Finding]
  PolicyEngine               per-category allow / ask / deny rules (from settings)
  ApprovalMemory             "don't ask again for identical code" (hash-keyed)
  AuditLog                   append-only JSONL of every execution + approval
  redact_secrets(text)       strip API keys/tokens/emails before text leaves
                             the machine (e.g. inside AI-helper prompts)
  code_hash(code)            canonical hash used by memory + audit

Everything is pure Python + stdlib so it is trivially unit-testable and reusable
by the tool manager, the approval dialog, and the sub-agents.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from systema import APP_ROOT as _APP_ROOT

_SEC_DIR = _APP_ROOT / "data" / "security"

# ── severity / category vocabulary ──────────────────────────────────────────
SEV_INFO, SEV_CAUTION, SEV_DANGER = "info", "caution", "danger"

CAT_PROCESS = "process"        # subprocess / os.system / shell
CAT_FILE_DELETE = "file_delete"  # rmtree / unlink / rmdir / remove
CAT_FILE_WRITE = "file_write"   # open(w/a/x) / write_text / touch / mkdir
CAT_FILE_MOVE = "file_move"     # rename / replace / shutil.move
CAT_FILE_COPY = "file_copy"     # copy / copy2 / copytree
CAT_NETWORK = "network"        # requests / urllib / sockets / http
CAT_DYNAMIC = "dynamic"        # eval / exec / compile / __import__
CAT_SYSTEM = "system"          # registry / ctypes / env mutation / kill
CAT_SECRETS = "secrets"        # literal API keys / tokens in the code

CATEGORY_LABELS = {
    CAT_PROCESS: "Spawns processes / shell commands",
    CAT_FILE_DELETE: "Deletes files or directories",
    CAT_FILE_WRITE: "Writes / creates / edits files",
    CAT_FILE_MOVE: "Moves / renames files",
    CAT_FILE_COPY: "Copies files or directory trees",
    CAT_NETWORK: "Talks to the network",
    CAT_DYNAMIC: "Runs dynamically-built code",
    CAT_SYSTEM: "Touches OS internals (registry / ctypes / processes)",
    CAT_SECRETS: "Contains what looks like a credential",
}

# categories are grouped in the settings UI so the (now finer) list never
# overwhelms — each tuple is (group title, [categories in that group]).
CATEGORY_GROUPS: list[tuple[str, list[str]]] = [
    ("File operations", [CAT_FILE_WRITE, CAT_FILE_MOVE, CAT_FILE_COPY, CAT_FILE_DELETE]),
    ("Code & processes", [CAT_PROCESS, CAT_DYNAMIC]),
    ("System & network", [CAT_SYSTEM, CAT_NETWORK]),
    ("Credentials", [CAT_SECRETS]),
]


@dataclass
class Finding:
    category: str
    severity: str
    line: int
    snippet: str
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


# call-name -> (category, severity, note). Matched against the dotted name of
# Call nodes (both `os.system` and bare `system` after `from os import system`).
_CALL_RULES: dict[str, tuple[str, str, str]] = {
    # processes
    "os.system": (CAT_PROCESS, SEV_DANGER, "runs a shell command"),
    "os.popen": (CAT_PROCESS, SEV_DANGER, "runs a shell command"),
    "os.exec": (CAT_PROCESS, SEV_DANGER, "replaces the process image"),
    "os.spawn": (CAT_PROCESS, SEV_DANGER, "spawns a process"),
    "subprocess.run": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    "subprocess.Popen": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    "subprocess.call": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    "subprocess.check_output": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    "subprocess.check_call": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    # deletion
    "shutil.rmtree": (CAT_FILE_DELETE, SEV_DANGER, "recursively deletes a directory"),
    "os.remove": (CAT_FILE_DELETE, SEV_CAUTION, "deletes a file"),
    "os.unlink": (CAT_FILE_DELETE, SEV_CAUTION, "deletes a file"),
    "os.rmdir": (CAT_FILE_DELETE, SEV_CAUTION, "removes a directory"),
    "os.removedirs": (CAT_FILE_DELETE, SEV_CAUTION, "removes directories"),
    "Path.unlink": (CAT_FILE_DELETE, SEV_CAUTION, "deletes a file"),
    "Path.rmdir": (CAT_FILE_DELETE, SEV_CAUTION, "removes a directory"),
    # copies
    "shutil.copy": (CAT_FILE_COPY, SEV_CAUTION, "copies a file"),
    "shutil.copy2": (CAT_FILE_COPY, SEV_CAUTION, "copies a file"),
    "shutil.copyfile": (CAT_FILE_COPY, SEV_CAUTION, "copies a file"),
    "shutil.copytree": (CAT_FILE_COPY, SEV_CAUTION, "copies a directory tree"),
    # moves / renames
    "shutil.move": (CAT_FILE_MOVE, SEV_CAUTION, "moves a file/directory"),
    # content writes
    "Path.write_text": (CAT_FILE_WRITE, SEV_CAUTION, "writes a file"),
    "Path.write_bytes": (CAT_FILE_WRITE, SEV_CAUTION, "writes a file"),
    # network
    "requests.get": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "requests.post": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "requests.put": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "requests.delete": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "requests.request": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "urllib.request.urlopen": (CAT_NETWORK, SEV_CAUTION, "opens a URL"),
    "socket.socket": (CAT_NETWORK, SEV_CAUTION, "raw socket"),
    "socket.create_connection": (CAT_NETWORK, SEV_CAUTION, "opens a connection"),
    # dynamic code
    "eval": (CAT_DYNAMIC, SEV_DANGER, "evaluates dynamic code"),
    "exec": (CAT_DYNAMIC, SEV_DANGER, "executes dynamic code"),
    "compile": (CAT_DYNAMIC, SEV_CAUTION, "compiles dynamic code"),
    "__import__": (CAT_DYNAMIC, SEV_CAUTION, "dynamic import"),
    "importlib.import_module": (CAT_DYNAMIC, SEV_CAUTION, "dynamic import"),
    # OS internals — cross-platform
    "os.kill": (CAT_SYSTEM, SEV_DANGER, "kills a process"),
    "os.killpg": (CAT_SYSTEM, SEV_DANGER, "kills a process group"),
    "os.putenv": (CAT_SYSTEM, SEV_INFO, "mutates environment"),
    "os.fork": (CAT_PROCESS, SEV_CAUTION, "forks the process"),
    # OS internals — Windows
    "winreg.SetValue": (CAT_SYSTEM, SEV_DANGER, "writes the Windows registry"),
    "winreg.SetValueEx": (CAT_SYSTEM, SEV_DANGER, "writes the Windows registry"),
    "winreg.DeleteKey": (CAT_SYSTEM, SEV_DANGER, "deletes a registry key"),
    "winreg.DeleteValue": (CAT_SYSTEM, SEV_DANGER, "deletes a registry value"),
    "ctypes.windll": (CAT_SYSTEM, SEV_CAUTION, "direct WinAPI call"),
    # OS internals — Linux / macOS (POSIX)
    "os.chmod": (CAT_SYSTEM, SEV_CAUTION, "changes file permissions"),
    "os.chown": (CAT_SYSTEM, SEV_CAUTION, "changes file ownership"),
    "os.setuid": (CAT_SYSTEM, SEV_DANGER, "changes the user id (privilege)"),
    "os.setgid": (CAT_SYSTEM, SEV_DANGER, "changes the group id (privilege)"),
    "os.seteuid": (CAT_SYSTEM, SEV_DANGER, "changes the effective user id"),
    "os.setegid": (CAT_SYSTEM, SEV_DANGER, "changes the effective group id"),
    "ctypes.CDLL": (CAT_SYSTEM, SEV_CAUTION, "loads a native library"),
    "ctypes.cdll": (CAT_SYSTEM, SEV_CAUTION, "loads a native library"),
    "ctypes.WinDLL": (CAT_SYSTEM, SEV_CAUTION, "loads a native library"),
    "ctypes.util.find_library": (CAT_SYSTEM, SEV_INFO, "locates a native library"),
    "os._exit": (CAT_SYSTEM, SEV_DANGER, "hard-exits the process immediately"),
    "os.abort": (CAT_SYSTEM, SEV_DANGER, "aborts the process"),
    "os.chdir": (CAT_SYSTEM, SEV_INFO, "changes the working directory"),
    "os.unsetenv": (CAT_SYSTEM, SEV_INFO, "mutates environment"),
    "os.environ.update": (CAT_SYSTEM, SEV_INFO, "mutates environment"),
    "os.setsid": (CAT_SYSTEM, SEV_CAUTION, "starts a new session"),
    "signal.signal": (CAT_SYSTEM, SEV_INFO, "installs a signal handler"),
    "mmap.mmap": (CAT_SYSTEM, SEV_CAUTION, "maps memory directly"),
    "resource.setrlimit": (CAT_SYSTEM, SEV_CAUTION, "changes resource limits"),
    # more processes
    "subprocess.getoutput": (CAT_PROCESS, SEV_CAUTION, "runs a shell command"),
    "subprocess.getstatusoutput": (CAT_PROCESS, SEV_CAUTION, "runs a shell command"),
    "os.execv": (CAT_PROCESS, SEV_DANGER, "replaces the process image"),
    "os.execl": (CAT_PROCESS, SEV_DANGER, "replaces the process image"),
    "os.execvp": (CAT_PROCESS, SEV_DANGER, "replaces the process image"),
    "os.execlp": (CAT_PROCESS, SEV_DANGER, "replaces the process image"),
    "os.spawnl": (CAT_PROCESS, SEV_DANGER, "spawns a process"),
    "os.spawnv": (CAT_PROCESS, SEV_DANGER, "spawns a process"),
    "os.posix_spawn": (CAT_PROCESS, SEV_DANGER, "spawns a process"),
    "pty.spawn": (CAT_PROCESS, SEV_DANGER, "spawns a process in a pty"),
    "multiprocessing.Process": (CAT_PROCESS, SEV_CAUTION, "spawns a process"),
    # more deletion / writes / creates / moves
    "os.truncate": (CAT_FILE_DELETE, SEV_CAUTION, "truncates a file"),
    "os.rename": (CAT_FILE_MOVE, SEV_CAUTION, "renames/moves a path"),
    "os.replace": (CAT_FILE_MOVE, SEV_CAUTION, "replaces a path"),
    "os.mkdir": (CAT_FILE_WRITE, SEV_CAUTION, "creates a directory"),
    "os.makedirs": (CAT_FILE_WRITE, SEV_CAUTION, "creates directories"),
    "os.symlink": (CAT_FILE_WRITE, SEV_CAUTION, "creates a symlink"),
    "os.link": (CAT_FILE_WRITE, SEV_CAUTION, "creates a hard link"),
    "Path.rename": (CAT_FILE_MOVE, SEV_CAUTION, "renames/moves a path"),
    "Path.replace": (CAT_FILE_MOVE, SEV_CAUTION, "replaces a path"),
    "Path.mkdir": (CAT_FILE_WRITE, SEV_CAUTION, "creates a directory"),
    "Path.touch": (CAT_FILE_WRITE, SEV_CAUTION, "creates a file"),
    "shutil.make_archive": (CAT_FILE_WRITE, SEV_CAUTION, "creates an archive"),
    "shutil.unpack_archive": (CAT_FILE_WRITE, SEV_CAUTION, "extracts an archive"),
    # more network
    "requests.patch": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "requests.head": (CAT_NETWORK, SEV_INFO, "HTTP request"),
    "urllib.request.urlretrieve": (CAT_NETWORK, SEV_CAUTION, "downloads a URL to a file"),
    "http.client.HTTPConnection": (CAT_NETWORK, SEV_CAUTION, "opens an HTTP connection"),
    "http.client.HTTPSConnection": (CAT_NETWORK, SEV_CAUTION, "opens an HTTPS connection"),
    "smtplib.SMTP": (CAT_NETWORK, SEV_CAUTION, "sends email"),
    "ftplib.FTP": (CAT_NETWORK, SEV_CAUTION, "opens an FTP connection"),
    "socket.create_server": (CAT_NETWORK, SEV_CAUTION, "listens on a socket"),
    "asyncio.open_connection": (CAT_NETWORK, SEV_CAUTION, "opens a connection"),
    "httpx.get": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    "httpx.post": (CAT_NETWORK, SEV_CAUTION, "HTTP request"),
    # more dynamic code / unsafe deserialization
    "pickle.load": (CAT_DYNAMIC, SEV_CAUTION, "deserializes data (can execute code)"),
    "pickle.loads": (CAT_DYNAMIC, SEV_DANGER, "deserializes untrusted data (code-exec risk)"),
    "marshal.load": (CAT_DYNAMIC, SEV_CAUTION, "deserializes code objects"),
    "marshal.loads": (CAT_DYNAMIC, SEV_DANGER, "deserializes untrusted code objects"),
    "yaml.load": (CAT_DYNAMIC, SEV_DANGER, "unsafe YAML load (can execute code)"),
    "dill.loads": (CAT_DYNAMIC, SEV_DANGER, "deserializes untrusted data (code-exec risk)"),
    "types.FunctionType": (CAT_DYNAMIC, SEV_CAUTION, "builds a function object dynamically"),
}

# import-name -> (category, severity) — flag risky modules even if the call
# pattern isn't recognized.
_IMPORT_RULES: dict[str, tuple[str, str]] = {
    "subprocess": (CAT_PROCESS, SEV_INFO),
    "multiprocessing": (CAT_PROCESS, SEV_INFO),
    "pty": (CAT_PROCESS, SEV_INFO),
    "ctypes": (CAT_SYSTEM, SEV_INFO),
    "winreg": (CAT_SYSTEM, SEV_CAUTION),
    "mmap": (CAT_SYSTEM, SEV_INFO),
    "fcntl": (CAT_SYSTEM, SEV_INFO),
    "resource": (CAT_SYSTEM, SEV_INFO),
    "signal": (CAT_SYSTEM, SEV_INFO),
    "socket": (CAT_NETWORK, SEV_INFO),
    "requests": (CAT_NETWORK, SEV_INFO),
    "httpx": (CAT_NETWORK, SEV_INFO),
    "urllib": (CAT_NETWORK, SEV_INFO),
    "http": (CAT_NETWORK, SEV_INFO),
    "ftplib": (CAT_NETWORK, SEV_INFO),
    "smtplib": (CAT_NETWORK, SEV_INFO),
    "telnetlib": (CAT_NETWORK, SEV_INFO),
    "pickle": (CAT_DYNAMIC, SEV_INFO),
    "marshal": (CAT_DYNAMIC, SEV_INFO),
    "dill": (CAT_DYNAMIC, SEV_INFO),
}

# secrets that must never appear in code (or leave the machine).
_SECRET_PATTERNS: list[tuple[str, str]] = [
    ("NVIDIA API key", r"nvapi-[A-Za-z0-9_\-]{20,}"),
    ("Cloudflare token", r"cfut_[A-Za-z0-9]{20,}"),
    ("Anthropic key", r"sk-ant-[A-Za-z0-9\-]{24,}"),
    ("API key", r"sk-[A-Za-z0-9]{30,}"),
    ("Google key", r"AIza[A-Za-z0-9_\-]{30,}"),
    ("GitHub token", r"ghp_[A-Za-z0-9]{20,}"),
    ("AWS access key", r"AKIA[0-9A-Z]{16}"),
    ("PyPI token", r"pypi-[A-Za-z0-9_\-]{30,}"),
    ("JWT", r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"),
    ("Bearer header", r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]


def _dotted_name(node: ast.AST) -> str:
    """Best-effort dotted name of a call target: os.path.join -> 'os.path.join'."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _match_call(name: str) -> tuple[str, str, str] | None:
    """Match a dotted call name against the rules (longest suffix wins, so both
    'os.system' and aliased/bare forms like 'system' still register)."""
    if name in _CALL_RULES:
        return _CALL_RULES[name]
    for rule, meta in _CALL_RULES.items():
        # suffix match: `p.unlink()` on a Path -> matches 'Path.unlink' tail
        tail = rule.split(".")[-1]
        if name == tail or name.endswith("." + tail):
            # only fuzzy-match names that are distinctive enough
            # NB: only distinctive tails — generic method names like `replace`
            # (str.replace), `copy` (list/dict.copy) or `move` are intentionally
            # excluded to avoid false positives; those stay exact-match only.
            if tail in ("rmtree", "unlink", "rmdir", "removedirs", "system", "popen",
                        "urlopen", "eval", "exec", "rename", "truncate",
                        "chmod", "chown", "setuid", "setgid", "symlink",
                        "mkdir", "makedirs", "touch", "write_text", "write_bytes"):
                return meta
    return None


def scan_code(code: str) -> list[Finding]:
    """Static risk scan: AST for calls/imports + regex for secrets and shell-isms.

    Returns findings sorted danger-first. Never raises — un-parseable code gets a
    single caution finding (the dialog still shows the raw code).
    """
    findings: list[Finding] = []
    lines = code.splitlines()

    def snippet(lineno: int) -> str:
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()[:120]
        return ""

    # -- secrets (text level, works even if the code doesn't parse) ----------
    for label, pat in _SECRET_PATTERNS:
        for m in re.finditer(pat, code):
            line = code[: m.start()].count("\n") + 1
            findings.append(Finding(CAT_SECRETS, SEV_DANGER, line, snippet(line),
                                    f"literal {label} in the code"))

    # -- destructive shell text inside strings (rm -rf, del /s, format) ------
    for pat, note in ((r"rm\s+-rf?\b", "destructive shell delete"),
                      (r"del\s+/[sq]", "destructive shell delete"),
                      (r"rmdir\s+/s", "destructive shell delete"),
                      (r"format\s+[a-zA-Z]:", "formats a drive")):
        for m in re.finditer(pat, code):
            line = code[: m.start()].count("\n") + 1
            findings.append(Finding(CAT_FILE_DELETE, SEV_DANGER, line,
                                    snippet(line), note))

    # -- AST pass -------------------------------------------------------------
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        findings.append(Finding(CAT_DYNAMIC, SEV_CAUTION, e.lineno or 1,
                                snippet(e.lineno or 1),
                                "code does not parse as pure Python "
                                "(may be shell / partial)"))
        tree = None

    if tree is not None:
        seen_imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", None)
                names = [mod] if mod else []
                names += [a.name for a in getattr(node, "names", [])]
                for n in names:
                    root = (n or "").split(".")[0]
                    if root in _IMPORT_RULES and root not in seen_imports:
                        seen_imports.add(root)
                        cat, sev = _IMPORT_RULES[root]
                        findings.append(Finding(cat, sev, node.lineno,
                                                snippet(node.lineno),
                                                f"imports {root}"))
            elif isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                meta = _match_call(name)
                if meta:
                    cat, sev, note = meta
                    findings.append(Finding(cat, sev, node.lineno,
                                            snippet(node.lineno),
                                            f"{name}() — {note}"))
                elif name == "open":
                    # open(..., 'w'/'a'/'x') is a write
                    mode = ""
                    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = str(kw.value.value)
                    if any(c in mode for c in "wax+"):
                        _verb = ("creates" if "x" in mode else
                                 "appends to" if "a" in mode else "writes")
                        findings.append(Finding(CAT_FILE_WRITE, SEV_CAUTION, node.lineno,
                                                snippet(node.lineno),
                                                f"open(mode={mode!r}) — {_verb} a file"))

    order = {SEV_DANGER: 0, SEV_CAUTION: 1, SEV_INFO: 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.line))
    return findings


def summarize_findings(findings: list[Finding]) -> str:
    """One-line human summary, e.g. '2 danger, 3 caution, 1 info'."""
    if not findings:
        return "no risky operations detected"
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    return ", ".join(f"{counts[s]} {s}" for s in (SEV_DANGER, SEV_CAUTION, SEV_INFO)
                     if s in counts)


# ── outbound secret redaction ────────────────────────────────────────────────
def redact_secrets(text: str) -> tuple[str, int]:
    """Replace credential-looking substrings with placeholders.

    Used on any text sent to an AI provider (explanations, edit requests) so a
    key sitting in reviewed code never leaves the machine. Returns
    ``(redacted_text, replacements)``.
    """
    n = 0
    for label, pat in _SECRET_PATTERNS:
        text, k = re.subn(pat, f"<REDACTED {label}>", text)
        n += k
    return text, n


# ── canonical code hash ──────────────────────────────────────────────────────
def code_hash(code: str) -> str:
    """Whitespace-normalized SHA-256 so trailing-space/EOL noise doesn't defeat
    the approval memory, but ANY real change produces a new hash."""
    norm = "\n".join(ln.rstrip() for ln in code.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()


# ── policy engine ────────────────────────────────────────────────────────────
POLICY_ALLOW, POLICY_ASK, POLICY_DENY = "allow", "ask", "deny"
_POLICY_KEY = "security_exec_policy"      # settings key: {category: allow|ask|deny}
_DEFAULT_POLICY = {
    CAT_PROCESS: POLICY_ASK,
    CAT_FILE_DELETE: POLICY_ASK,
    CAT_FILE_WRITE: POLICY_ASK,
    CAT_FILE_MOVE: POLICY_ASK,
    CAT_FILE_COPY: POLICY_ASK,
    CAT_NETWORK: POLICY_ASK,
    CAT_DYNAMIC: POLICY_ASK,
    CAT_SYSTEM: POLICY_ASK,
    CAT_SECRETS: POLICY_ASK,
}

# Built-in policy presets. A preset is a partial map {category: allow|ask|deny};
# anything it omits falls back to _DEFAULT_POLICY ('ask'). The UI also lets the
# user save their own named presets (settings['security_policy_presets']).
_PRESETS_KEY = "security_policy_presets"   # settings key: {name: {cat: policy}}
BUILTIN_PRESETS: dict[str, dict[str, str]] = {
    # Prompt on everything risky — the safest interactive default.
    "Strict": {c: POLICY_ASK for c in _DEFAULT_POLICY},
    # Let routine file writes/copies through; still review moves, deletes,
    # processes, network, dynamic code and system calls.
    "Balanced": {
        **{c: POLICY_ASK for c in _DEFAULT_POLICY},
        CAT_FILE_WRITE: POLICY_ALLOW,
        CAT_FILE_COPY: POLICY_ALLOW,
    },
    # Auto-approve all local file operations and network I/O; still prompt for
    # process spawns, deletes, dynamic code and OS-internal calls.
    "Trusting": {
        **{c: POLICY_ASK for c in _DEFAULT_POLICY},
        CAT_FILE_WRITE: POLICY_ALLOW,
        CAT_FILE_COPY: POLICY_ALLOW,
        CAT_FILE_MOVE: POLICY_ALLOW,
        CAT_NETWORK: POLICY_ALLOW,
    },
    # Prompt on everything and hard-block the two highest-risk classes:
    # literal credentials and dynamically-built code.
    "Paranoid": {
        **{c: POLICY_ASK for c in _DEFAULT_POLICY},
        CAT_SECRETS: POLICY_DENY,
        CAT_DYNAMIC: POLICY_DENY,
    },
}


def normalize_policy(rules: dict | None) -> dict:
    """Return a full {category: policy} map: defaults overlaid with valid keys
    from ``rules`` (a preset or a saved policy). Unknown keys/values dropped."""
    out = dict(_DEFAULT_POLICY)
    for k, v in (rules or {}).items():
        if k in out and v in (POLICY_ALLOW, POLICY_ASK, POLICY_DENY):
            out[k] = v
    return out


def load_presets(settings: dict | None) -> dict:
    """Built-in presets plus any user-saved ones (user names can't shadow a
    built-in). Every returned preset is normalized to a full policy map."""
    presets = {name: normalize_policy(rules)
               for name, rules in BUILTIN_PRESETS.items()}
    for name, rules in ((settings or {}).get(_PRESETS_KEY) or {}).items():
        if name and name not in BUILTIN_PRESETS and isinstance(rules, dict):
            presets[name] = normalize_policy(rules)
    return presets


def save_custom_preset(settings: dict, name: str, rules: dict) -> None:
    """Persist a user preset. Refuses to overwrite a built-in name."""
    name = (name or "").strip()
    if not name or name in BUILTIN_PRESETS:
        raise ValueError("Choose a name that isn't one of the built-in presets.")
    store = dict((settings or {}).get(_PRESETS_KEY) or {})
    store[name] = {k: v for k, v in normalize_policy(rules).items()}
    settings[_PRESETS_KEY] = store


def delete_custom_preset(settings: dict, name: str) -> None:
    """Remove a user preset (built-ins can't be deleted)."""
    store = dict((settings or {}).get(_PRESETS_KEY) or {})
    if name in store:
        del store[name]
        settings[_PRESETS_KEY] = store


class PolicyEngine:
    """Per-category execution rules. 'ask' is the default everywhere — 'deny'
    hard-blocks before the dialog, 'allow' means the category alone never
    forces a prompt (supervision setting still applies)."""

    def __init__(self, settings: dict | None):
        self.rules = normalize_policy((settings or {}).get(_POLICY_KEY))

    def decide(self, findings: list[Finding]) -> tuple[str, list[str]]:
        """Overall decision for a scan: ('deny'|'ask'|'allow', hit categories).
        deny wins over ask wins over allow."""
        cats = sorted({f.category for f in findings})
        if any(self.rules.get(c) == POLICY_DENY for c in cats):
            denied = [c for c in cats if self.rules.get(c) == POLICY_DENY]
            return POLICY_DENY, denied
        if any(self.rules.get(c, POLICY_ASK) == POLICY_ASK for c in cats):
            return POLICY_ASK, cats
        return POLICY_ALLOW, cats

    @staticmethod
    def save(settings: dict, rules: dict) -> None:
        settings[_POLICY_KEY] = {k: v for k, v in rules.items()
                                 if k in _DEFAULT_POLICY}


# ── approval memory ──────────────────────────────────────────────────────────
class ApprovalMemory:
    """Remembers approved code by hash so identical re-runs skip the prompt.

    Session scope always applies; ``persist=True`` entries survive restarts in
    data/security/approved_hashes.json. Any code change = new hash = new prompt.
    """

    _FILE = _SEC_DIR / "approved_hashes.json"

    def __init__(self):
        self._session: set[str] = set()
        self._persistent: dict[str, dict] = {}
        try:
            if self._FILE.exists():
                self._persistent = json.loads(self._FILE.read_text(encoding="utf-8"))
        except Exception:
            self._persistent = {}

    def is_approved(self, code: str) -> bool:
        h = code_hash(code)
        return h in self._session or h in self._persistent

    def remember(self, code: str, persist: bool = False, note: str = "") -> None:
        h = code_hash(code)
        self._session.add(h)
        if persist:
            self._persistent[h] = {"ts": time.time(), "note": note[:200]}
            self._save()

    def forget_all(self) -> None:
        self._session.clear()
        self._persistent.clear()
        self._save()

    @property
    def persistent_count(self) -> int:
        return len(self._persistent)

    def _save(self) -> None:
        try:
            self._FILE.parent.mkdir(parents=True, exist_ok=True)
            self._FILE.write_text(json.dumps(self._persistent, indent=1),
                                  encoding="utf-8")
        except Exception:
            pass


# ── audit log ────────────────────────────────────────────────────────────────
class AuditLog:
    """Append-only JSONL of every gated execution: what ran, its hash, how it
    was approved (user / memory / policy / agent-edit), and the scan summary."""

    _FILE = _SEC_DIR / "audit.jsonl"

    @classmethod
    def record(cls, *, code: str, execution_type: str, decision: str,
               source: str, findings: list[Finding] | None = None,
               extra: dict | None = None) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "hash": code_hash(code)[:16],
            "type": execution_type,
            "decision": decision,          # approved | rejected | denied | auto
            "source": source,              # user | memory | policy | unsupervised
            "risk": summarize_findings(findings or []),
            "lines": code.count("\n") + 1,
        }
        if extra:
            entry.update(extra)
        try:
            cls._FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(cls._FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    @classmethod
    def tail(cls, n: int = 50) -> list[dict]:
        try:
            lines = cls._FILE.read_text(encoding="utf-8").splitlines()[-n:]
            return [json.loads(x) for x in lines if x.strip()]
        except Exception:
            return []
