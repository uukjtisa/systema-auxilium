"""
systema/agents/update_agent.py

A self-contained AI SUB-AGENT that helps the user review and resolve an update,
one hunk (or one line) at a time — especially for PROTECTED files that hold
configured provider accounts/keys, where a blind overwrite is data loss.

Design (as requested):
  • Same provider  — it reuses the main AIEngine's active provider/model. It does
    NOT open a new connection or need separate credentials; it calls the same
    provider script through ``ai_engine._provider_script(messages)``.
  • Sub system prompt — its own role/instructions (``SUB_SYSTEM_PROMPT``), NOT the
    main assistant's prompt.
  • Sub toolset — a small, dedicated set of JSON-fence actions that operate only
    on the review session (list/view/recommend/set decisions/edit/finish).
  • Sub interpreter — its OWN isolated Python namespace (``run_python``) for
    analysis (e.g. scanning a hunk for secrets), separate from the app's main
    interpreter, with read-only helpers and no filesystem/network builtins.

The agent is UI-agnostic: it emits events through callbacks so a Qt dialog (or a
test) can render them. Run it off the GUI thread.
"""

from __future__ import annotations

import io
import json
import re
import contextlib
from dataclasses import dataclass, field
from typing import Callable

from systema.agents.update_hunks import Segment, build_segments, assemble

# ── the sub-agent's own system prompt ───────────────────────────────────────
SUB_SYSTEM_PROMPT = """\
You are the Update Manager, a focused sub-agent inside Systema Auxilium. Your ONLY
job is to help the user safely apply a software update by reviewing changed files
hunk by hunk. You are NOT the main assistant; ignore any unrelated requests.

Guiding rule — never cause data loss. Some files are PROTECTED: they hold the
user's configured provider accounts, API keys, tokens or emails. For a protected
hunk, KEEP the user's version unless the change is clearly cosmetic and does not
touch a secret. When unsure, keep the user's line and say why.

Each file is split into hunks. For every hunk you decide one of:
  local   — keep what is on the user's disk now (default for edits/conflicts)
  update  — take the upstream version (default for plain updates)
  edited  — supply replacement text yourself

You act by emitting one or more action fences. An action fence is a ```action
block whose body is a single JSON object with a "tool" field:

```action
{"tool": "list_files"}
```
```action
{"tool": "get_hunks", "file": "providers/x.py"}
```
```action
{"tool": "view", "file": "providers/x.py", "hunk": 2}
```
```action
{"tool": "set_decision", "file": "providers/x.py", "hunk": 2, "choice": "local"}
```
```action
{"tool": "edit_hunk", "file": "providers/x.py", "hunk": 2, "text": "API_KEY = \\"...\\"\\n"}
```
```action
{"tool": "recommend", "file": "providers/x.py"}
```
```action
{"tool": "auto_approve_safe", "file": "providers/x.py"}
```
```action
{"tool": "say", "text": "one short explanation for the user"}
```
```action
{"tool": "finish", "summary": "what you did and what still needs the user"}
```

For free analysis you may run isolated Python in your own sandbox:
```run_python
for h in hunks("providers/x.py"):
    note(f"hunk {h['index']} {h['kind']} secrets={h['secrets']}")
```
Available in run_python: files(), hunks(file), hunk_text(file, index, side),
note(msg). No imports, file, open, network, or app access.

After each action you receive an OBSERVATION. Continue until you emit "finish".
Keep prose minimal — prefer actions. Never invent a file or hunk index that
list_files / get_hunks did not report.
"""

_ACTION_RE = re.compile(r"```action\s*\n(.*?)\n```", re.DOTALL)
_PY_RE = re.compile(r"```run_python\s*\n(.*?)\n```", re.DOTALL)


@dataclass
class FileReview:
    """One file under review: its ordered segments + convenience accessors."""
    path: str
    segments: list[Segment]
    sensitive: bool = False

    @property
    def hunks(self):
        return [s.hunk for s in self.segments if s.kind == "hunk"]

    def hunk(self, index: int):
        for h in self.hunks:
            if h.index == index:
                return h
        return None

    def assembled(self) -> str:
        return assemble(self.segments)


@dataclass
class ReviewSession:
    """The whole review: several files the agent (and user) resolve together."""
    files: dict[str, FileReview] = field(default_factory=dict)

    def add(self, path: str, tagged: list[tuple[str, str]], sensitive: bool = False):
        self.files[path] = FileReview(path, build_segments(tagged), sensitive)

    def summary(self) -> dict:
        return {p: {"sensitive": fr.sensitive,
                    "hunks": len(fr.hunks),
                    "secrets": sum(1 for h in fr.hunks if h.touches_secrets)}
                for p, fr in self.files.items()}


class UpdateAgent:
    """Runs the review conversation against the shared provider with its own
    prompt, toolset and interpreter.

    ``provider_fn`` takes a messages list ``[{role, content}, ...]`` (with a
    leading system message) and returns the model's text. By default this is
    ``ai_engine._provider_script`` — i.e. the exact same provider the app uses.
    """

    MAX_STEPS = 24

    def __init__(self, session: ReviewSession, ai_engine=None,
                 provider_fn: Callable[[list], str] | None = None,
                 on_message: Callable[[str, str], None] | None = None,
                 on_state: Callable[[], None] | None = None):
        self.session = session
        self.ai = ai_engine
        self._provider_fn = provider_fn or self._default_provider
        self.on_message = on_message or (lambda role, text: None)
        self.on_state = on_state or (lambda: None)
        self._ns = self._make_interpreter()
        self._notes: list[str] = []

    # -- provider (same one the app uses) -------------------------------------
    def _default_provider(self, messages: list) -> str:
        if self.ai is None:
            raise RuntimeError("no provider available")
        text = self.ai._provider_script(messages)
        if not text and hasattr(self.ai, "_get_ai_response_internal"):
            # Fallback: single-turn without a custom system prompt.
            user = next((m["content"] for m in reversed(messages)
                         if m["role"] == "user"), "")
            text = self.ai._get_ai_response_internal(user)
        return text or ""

    # -- isolated sub interpreter ---------------------------------------------
    def _make_interpreter(self) -> dict:
        """A minimal, sandboxed namespace: read-only review helpers, no builtins
        that touch the filesystem, network, imports or the app."""
        def files():
            return list(self.session.files.keys())

        def hunks(path):
            fr = self.session.files.get(path)
            if fr is None:
                return []
            return [{"index": h.index, "kind": h.kind, "decision": h.decision,
                     "secrets": h.touches_secrets,
                     "local_lines": len(h.local_lines),
                     "update_lines": len(h.update_lines)} for h in fr.hunks]

        def hunk_text(path, index, side="local"):
            fr = self.session.files.get(path)
            if fr is None:
                return ""
            h = fr.hunk(index)
            if h is None:
                return ""
            if side == "update":
                return "".join(h.update_lines)
            if side == "base":
                return "".join(h.base_lines)
            return "".join(h.local_lines)

        def note(msg):
            self._notes.append(str(msg))

        safe_builtins = {
            "len": len, "range": range, "enumerate": enumerate, "sorted": sorted,
            "min": min, "max": max, "sum": sum, "any": any, "all": all,
            "str": str, "int": int, "float": float, "bool": bool, "list": list,
            "dict": dict, "set": set, "tuple": tuple, "print": print,
            "abs": abs, "round": round, "zip": zip, "map": map, "filter": filter,
        }
        return {
            "__builtins__": safe_builtins,
            "files": files, "hunks": hunks, "hunk_text": hunk_text, "note": note,
        }

    def _run_python(self, code: str) -> str:
        self._notes = []
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<update_agent>", "exec"), self._ns)
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"
        out = buf.getvalue().strip()
        notes = "\n".join(self._notes)
        return ("\n".join(x for x in (out, notes) if x)) or "(no output)"

    # -- sub toolset ----------------------------------------------------------
    def _tool(self, action: dict) -> str:
        tool = str(action.get("tool", "")).strip()
        path = action.get("file")
        fr = self.session.files.get(path) if path else None

        if tool == "list_files":
            return json.dumps(self.session.summary())
        if tool == "get_hunks":
            if fr is None:
                return f"error: unknown file {path!r}"
            return json.dumps([{"index": h.index, "kind": h.kind,
                                "decision": h.decision, "secrets": h.touches_secrets,
                                "summary": h.summary()} for h in fr.hunks])
        if tool == "view":
            if fr is None:
                return f"error: unknown file {path!r}"
            hi = action.get("hunk")
            h = fr.hunk(int(hi)) if hi is not None else None
            if h is None:
                return f"error: unknown hunk {hi!r} in {path!r}"
            return json.dumps({"index": h.index, "kind": h.kind,
                               "decision": h.decision, "secrets": h.touches_secrets,
                               "local": "".join(h.local_lines),
                               "update": "".join(h.update_lines)})
        if tool == "set_decision":
            if fr is None:
                return f"error: unknown file {path!r}"
            h = fr.hunk(int(action.get("hunk", -1)))
            choice = str(action.get("choice", "")).strip()
            if h is None or choice not in ("local", "update"):
                return "error: need a valid hunk and choice in {local, update}"
            h.decision = choice
            self.on_state()
            return f"ok: hunk {h.index} -> {choice}"
        if tool == "edit_hunk":
            if fr is None:
                return f"error: unknown file {path!r}"
            h = fr.hunk(int(action.get("hunk", -1)))
            text = action.get("text")
            if h is None or text is None:
                return "error: need a valid hunk and replacement text"
            h.decision = "edited"
            h.edited_text = text if text.endswith("\n") or text == "" else text + "\n"
            self.on_state()
            return f"ok: hunk {h.index} edited ({len(h.edited_text)} chars)"
        if tool == "recommend":
            return self._recommend(fr)
        if tool == "auto_approve_safe":
            return self._auto_approve_safe(fr)
        if tool in ("say", "explain"):
            msg = str(action.get("text", "")).strip()
            if msg:
                self.on_message("agent", msg)
            return "ok"
        if tool == "finish":
            return "__finish__"
        return f"error: unknown tool {tool!r}"

    def _recommend(self, fr: FileReview | None) -> str:
        targets = [fr] if fr else self.session.files.values()
        recs = []
        for f in targets:
            for h in f.hunks:
                if h.kind == "update" and not h.touches_secrets:
                    rec = "update"      # plain, safe upstream change
                elif h.touches_secrets:
                    rec = "local"       # protect the user's secret
                elif h.kind == "conflict":
                    rec = "local"       # keep user's side of a conflict
                else:
                    rec = "local"       # a local-only edit — preserve it
                recs.append({"file": f.path, "hunk": h.index, "recommend": rec,
                             "secrets": h.touches_secrets, "kind": h.kind})
        return json.dumps(recs)

    def _auto_approve_safe(self, fr: FileReview | None) -> str:
        targets = [fr] if fr else self.session.files.values()
        applied, flagged = [], []
        for f in targets:
            for h in f.hunks:
                if h.kind == "update" and not h.touches_secrets:
                    h.decision = "update"
                    applied.append(f"{f.path}#{h.index}")
                elif h.touches_secrets:
                    flagged.append(f"{f.path}#{h.index}")
        self.on_state()
        return json.dumps({"auto_approved": applied, "flagged_for_you": flagged})

    # -- run loop -------------------------------------------------------------
    def run(self, task: str) -> str:
        """Drive the review for one instruction (e.g. 'explain file X',
        'recommend', 'auto-approve safe hunks'). Returns the final summary."""
        convo = [
            {"role": "system", "content": SUB_SYSTEM_PROMPT},
            {"role": "user", "content":
                task + "\n\nFiles under review:\n" + json.dumps(self.session.summary())},
        ]
        final = ""
        for _ in range(self.MAX_STEPS):
            reply = (self._provider_fn(convo) or "").strip()
            if not reply:
                break
            convo.append({"role": "assistant", "content": reply})

            # Surface any prose the model wrote outside of action fences.
            prose = _ACTION_RE.sub("", _PY_RE.sub("", reply)).strip()
            if prose:
                self.on_message("agent", prose)

            observations = []
            done = False
            for code in _PY_RE.findall(reply):
                observations.append("run_python -> " + self._run_python(code))
            for raw in _ACTION_RE.findall(reply):
                try:
                    action = json.loads(raw)
                except Exception as e:
                    observations.append(f"error: invalid action JSON: {e}")
                    continue
                result = self._tool(action)
                if result == "__finish__":
                    final = str(action.get("summary", "")).strip()
                    if final:
                        self.on_message("agent", final)
                    done = True
                    break
                observations.append(f"{action.get('tool')} -> {result}")

            if done:
                break
            if not observations:
                # No tools and no finish -> treat the prose as the final answer.
                final = prose
                break
            convo.append({"role": "user",
                          "content": "OBSERVATION:\n" + "\n".join(observations)})
        return final
