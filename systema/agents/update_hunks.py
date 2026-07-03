"""
systema/agents/update_hunks.py

Turn an origin-tagged 3-way review (from gitplucker's review_change /
UpdaterService.review) into reviewable HUNKS the user can approve, keep, or edit
one block — or one line — at a time, then re-assemble the resulting file text.

Everything here is pure Python (no Qt, no gitplucker) so it is trivially unit
tested and reusable by both the UI dialog and the AI sub-agent.

Input: a list of ``(tag, line)`` pairs where each ``line`` keeps its trailing
newline. Tags come from gitplucker.merge.annotate_three_way:

    same
    update_add / update_del      (an update-only region: del = base, add = remote)
    local_add  / local_del       (a local-only region:  del = base, add = local)
    conflict_marker / conflict_local / conflict_base / conflict_remote
    header / hunk / info          (metadata from the 2-way fallback — ignored here)

A contiguous run of non-``same`` change lines is exactly one region (annotate
separates regions with synchronized ``same`` lines), so each run becomes one
Hunk. From the tags we derive, for every hunk, the LOCAL text (what is on disk
now) and the UPDATE text (what upstream wants):

    update region  -> local = update_del lines,  update = update_add lines
    local region   -> local = local_add lines,   update = local_del  lines
    conflict       -> local = conflict_local,     update = conflict_remote
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Tags that are pure metadata and never contribute to file content.
_META = {"header", "hunk", "info"}

# Default decision per hunk kind: normal updates apply, local edits are kept,
# conflicts stay on the user's side until they choose.
_DEFAULT_DECISION = {"update": "update", "local": "local", "conflict": "local"}


@dataclass
class LineChoice:
    """One line inside a split hunk: text on each side + which side to keep."""
    local: str | None      # local line (None if only the update has it)
    update: str | None     # update line (None if only local has it)
    take: str = "local"    # "local" | "update"


@dataclass
class Hunk:
    index: int
    kind: str                          # "update" | "local" | "conflict"
    local_lines: list[str] = field(default_factory=list)
    update_lines: list[str] = field(default_factory=list)
    base_lines: list[str] = field(default_factory=list)
    decision: str = "local"            # "local" | "update" | "edited"
    edited_text: str | None = None     # used when decision == "edited"
    lines: list[LineChoice] | None = None   # populated by split()

    # -- content the hunk contributes to the assembled file --------------------
    def resolved_text(self) -> str:
        if self.decision == "edited" and self.edited_text is not None:
            return self.edited_text
        if self.decision == "update":
            return "".join(self.update_lines)
        if self.decision == "line" and self.lines is not None:
            out = []
            for lc in self.lines:
                pick = lc.update if lc.take == "update" else lc.local
                if pick is not None:
                    out.append(pick)
            return "".join(out)
        return "".join(self.local_lines)    # "local" (default / keep mine)

    @property
    def touches_secrets(self) -> bool:
        """Heuristic: does either side look like it carries a credential/account?
        Used by the sub-agent to refuse auto-approving risky blocks."""
        blob = ("".join(self.local_lines) + "".join(self.update_lines)).lower()
        needles = ("api_key", "apikey", "api-key", "secret", "token", "password",
                   "passwd", "bearer", "authorization", "auth_token", "account",
                   "email", "username", "client_secret", "private_key", "sk-")
        return any(n in blob for n in needles)

    def split(self) -> list[LineChoice]:
        """Expand the hunk into per-line choices (local vs update), so the user
        (or agent) can pick individual lines. Idempotent."""
        if self.lines is None:
            self.lines = _line_choices(self.local_lines, self.update_lines)
        return self.lines

    def summary(self) -> str:
        return (f"hunk {self.index} [{self.kind}] "
                f"-{len(self.local_lines)}/+{len(self.update_lines)}"
                + ("  (touches secrets)" if self.touches_secrets else ""))


@dataclass
class Segment:
    """Ordered piece of the file: either shared context or a reviewable hunk."""
    kind: str                  # "context" | "hunk"
    text: str = ""             # for context
    hunk: Hunk | None = None    # for hunk


def _line_choices(local_lines: list[str], update_lines: list[str]) -> list[LineChoice]:
    """Line-level diff between a hunk's local and update text -> LineChoices.
    Equal lines default to keeping local (identical either way)."""
    sm = SequenceMatcher(a=local_lines, b=update_lines, autojunk=False)
    out: list[LineChoice] = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            for k in range(a1 - a0):
                out.append(LineChoice(local=local_lines[a0 + k],
                                      update=update_lines[b0 + k], take="local"))
        else:
            # Deleted-by-update lines default to keeping local; added-by-update
            # lines default to taking the update (that's the "apply update" read).
            for k in range(a0, a1):
                out.append(LineChoice(local=local_lines[k], update=None, take="local"))
            for k in range(b0, b1):
                out.append(LineChoice(local=None, update=update_lines[k], take="update"))
    return out


def _classify(buffer: list[tuple[str, str]]) -> Hunk | None:
    """Turn one contiguous run of change lines into a Hunk."""
    tags = {t for t, _ in buffer}
    if tags & {"conflict_local", "conflict_base", "conflict_remote", "conflict_marker"}:
        local = [ln for t, ln in buffer if t == "conflict_local"]
        update = [ln for t, ln in buffer if t == "conflict_remote"]
        base = [ln for t, ln in buffer if t == "conflict_base"]
        return Hunk(0, "conflict", local, update, base)
    if tags & {"local_add", "local_del"}:
        local = [ln for t, ln in buffer if t == "local_add"]
        base = [ln for t, ln in buffer if t == "local_del"]
        return Hunk(0, "local", local_lines=local, update_lines=list(base), base_lines=base)
    if tags & {"update_add", "update_del"}:
        base = [ln for t, ln in buffer if t == "update_del"]
        update = [ln for t, ln in buffer if t == "update_add"]
        return Hunk(0, "update", local_lines=list(base), update_lines=update, base_lines=base)
    return None


def build_segments(tagged: list[tuple[str, str]]) -> list[Segment]:
    """Split a tagged review into ordered context/hunk segments."""
    segments: list[Segment] = []
    ctx: list[str] = []
    buf: list[tuple[str, str]] = []
    idx = 0

    def _flush_ctx():
        if ctx:
            segments.append(Segment("context", text="".join(ctx)))
            ctx.clear()

    def _flush_hunk():
        nonlocal idx
        if not buf:
            return
        h = _classify(buf)
        buf.clear()
        if h is None:
            return
        h.index = idx
        h.decision = _DEFAULT_DECISION.get(h.kind, "local")
        idx += 1
        segments.append(Segment("hunk", hunk=h))

    for tag, line in tagged:
        if tag in _META:
            continue
        if tag == "same":
            _flush_hunk()
            ctx.append(line)
        else:
            _flush_ctx()
            buf.append((tag, line))
    _flush_hunk()
    _flush_ctx()
    return segments


def build_hunks(tagged: list[tuple[str, str]]) -> list[Hunk]:
    return [s.hunk for s in build_segments(tagged) if s.kind == "hunk"]


def assemble(segments: list[Segment]) -> str:
    """Reconstruct the file text from segments + each hunk's current decision."""
    out = []
    for s in segments:
        if s.kind == "context":
            out.append(s.text)
        elif s.hunk is not None:
            out.append(s.hunk.resolved_text())
    return "".join(out)


def apply_decisions(segments: list[Segment], decisions: dict[int, str]) -> str:
    """Set decisions by hunk index (``{index: 'local'|'update'|...}``) and
    return the assembled text. Unknown indices are ignored."""
    for s in segments:
        if s.kind == "hunk" and s.hunk is not None and s.hunk.index in decisions:
            s.hunk.decision = decisions[s.hunk.index]
    return assemble(segments)


# ── review session model (used by the Manage-Update dialog) ───────────────────
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
    """The whole review: several files the user resolves together."""
    files: dict[str, FileReview] = field(default_factory=dict)

    def add(self, path: str, tagged: list[tuple[str, str]], sensitive: bool = False):
        self.files[path] = FileReview(path, build_segments(tagged), sensitive)

    def summary(self) -> dict:
        return {p: {"sensitive": fr.sensitive,
                    "hunks": len(fr.hunks),
                    "secrets": sum(1 for h in fr.hunks if h.touches_secrets)}
                for p, fr in self.files.items()}
