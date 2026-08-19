"""
systema/execution/qa_spec.py

The ask_user question format -- parsing what the agent wrote, and serializing
what the user answered. Pure stdlib (no PyQt), so both the ToolManager and the
tests can use it headlessly, exactly like tool_registry.

WHY A TEXT FORMAT AT ALL
------------------------
Every canonical tool parameter in this app is a STRING (see
ToolManager.get_canonical_tools -- it emits {'type': 'string'} for every
property), and native tool calls are reconstructed into fences before parsing
so both calling modes share one pipeline. A nested questions/options schema
therefore cannot travel as real JSON structure through the native channel; it
has to arrive as text either way.

So the body accepts BOTH, and tries them in that order:

  1. JSON -- a list of question objects, or {"questions": [...]}. Native-mode
     models reach for this on their own, and it costs nothing to accept.
  2. The line format below, which a compat-mode model can write by hand
     without escaping anything:

        Q: Which environments should the migration run against?
        header: Deploy target
        multi: true
        - Staging | Safe to break; mirrors the prod schema.
        - Production | Live data. Requires the backup step first.

        Q: What is the rollback plan?
        - Snapshot the volume | Restore point before anything runs.
        - Down-migration script | Reversible, but must be written first.

Blank lines separate questions; 'Q:' starts one. Option lines begin with '-'
or '*', and '|' splits the label from its description.

THE ANSWER FORMAT IS DELIBERATELY THE SAME ON BOTH EXITS. When the user answers
the card, the serialized text goes back to the agent as the tool result. When
the user presses Esc partway through, the SAME serializer renders what they had
filled in and prepends it to their typed reply. One format, so a half-answered
interview reads identically whether it completed or not, and the agent never
has to be taught two shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Mirrors the constraints of the tool this is modelled on: a card with more than
# four questions stops being an interview and starts being a form, and a
# question with one option is not a question.
MAX_QUESTIONS = 4
MAX_OPTIONS = 8
OTHER_LABEL = "Other"


@dataclass
class Option:
    label: str
    description: str = ""


@dataclass
class Question:
    question: str
    options: list = field(default_factory=list)
    header: str = ""
    multi: bool = True
    allow_other: bool = True

    def option_labels(self) -> list:
        return [o.label for o in self.options]


@dataclass
class QuestionSet:
    questions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.questions)

    def __len__(self) -> int:
        return len(self.questions)


# -- parsing -----------------------------------------------------------------

def _truthy(text) -> bool:
    return str(text).strip().lower() in ("1", "true", "yes", "y", "on", "multi")


def _clean(text) -> str:
    return str(text or "").strip()


def _option_from(raw):
    """One option from either a dict (JSON form) or a 'label | description' line."""
    if isinstance(raw, dict):
        label = _clean(raw.get("label") or raw.get("name") or raw.get("option"))
        desc = _clean(raw.get("description") or raw.get("desc") or raw.get("detail"))
    else:
        text = _clean(raw)
        label, _, desc = text.partition("|")
        label, desc = _clean(label), _clean(desc)
    return Option(label, desc) if label else None


# An option the model wrote that just means "something else". The card ALWAYS
# renders a free-text Other box, so keeping these as options paints the escape
# hatch twice -- once as a dead radio the user cannot type into, once as the
# real box. Matched conservatively: the bare word, optionally with a
# parenthetical or a trailing marker. "Other services" is a real option and
# must survive.
_OTHER_ALIASES = ("other", "others", "something else", "none of these",
                  "none of the above")


def _is_redundant_other(label: str) -> bool:
    text = _clean(label).lower().rstrip(".:!").strip()
    if text in _OTHER_ALIASES:
        return True
    for alias in _OTHER_ALIASES:
        # "other (specify)", "other - explain", "other..."
        if text.startswith(alias) and text[len(alias):].lstrip() [:1] in ("(", "-", "–", "—", "."):
            return True
    return False


def _question_from_dict(raw: dict):
    text = _clean(raw.get("question") or raw.get("q") or raw.get("text"))
    if not text:
        return None
    opts = []
    for o in (raw.get("options") or raw.get("choices") or []):
        opt = _option_from(o)
        if opt is not None:
            opts.append(opt)
    multi_raw = raw.get("multiSelect", raw.get("multi", raw.get("multiple", True)))
    return Question(
        question=text,
        options=opts[:MAX_OPTIONS],
        header=_clean(raw.get("header") or raw.get("label"))[:24],
        multi=multi_raw if isinstance(multi_raw, bool) else _truthy(multi_raw),
        allow_other=bool(raw.get("allow_other", True)),
    )


def _parse_json(body: str):
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        data = data.get("questions") or data.get("items") or []
    if not isinstance(data, list):
        return None
    out = []
    for item in data:
        if isinstance(item, dict):
            q = _question_from_dict(item)
            if q is not None:
                out.append(q)
    return QuestionSet(out) if out else None


def _parse_lines(body: str) -> QuestionSet:
    questions = []
    cur = None

    def flush():
        nonlocal cur
        if cur is not None and cur.question:
            questions.append(cur)
        cur = None

    for line in str(body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()

        if low.startswith(("q:", "question:")):
            flush()
            cur = Question(question=_clean(stripped.split(":", 1)[1]))
            continue
        if stripped.startswith(("-", "*")) or low.startswith("option:"):
            raw = (stripped.split(":", 1)[1] if low.startswith("option:")
                   else stripped[1:])
            if cur is None:
                # Options before any 'Q:' -- salvage rather than discard; the
                # question text is likelier to be missing than the options.
                cur = Question(question="")
            opt = _option_from(raw)
            if opt is not None and len(cur.options) < MAX_OPTIONS:
                cur.options.append(opt)
            continue
        if low.startswith("header:") and cur is not None:
            cur.header = _clean(stripped.split(":", 1)[1])[:24]
            continue
        if low.startswith(("multi:", "multiselect:")) and cur is not None:
            cur.multi = _truthy(stripped.split(":", 1)[1])
            continue
        if low.startswith("other:") and cur is not None:
            cur.allow_other = _truthy(stripped.split(":", 1)[1])
            continue
        # A bare line with no marker: the question text for an option-only
        # block, otherwise the start of a new question.
        if cur is None or cur.options:
            flush()
            cur = Question(question=stripped)
        elif not cur.question:
            cur.question = stripped
    flush()
    return QuestionSet(questions)


def parse(body: str) -> QuestionSet:
    """Parse an ask_user body. Never raises -- a malformed body yields an empty
    set plus warnings, which the caller reports to the agent as a tool error
    rather than crashing a turn."""
    body = str(body or "").strip()
    if not body:
        return QuestionSet([], ["empty ask_user body -- nothing to ask"])

    qs = _parse_json(body) or _parse_lines(body)
    warnings = list(qs.warnings)

    kept = []
    for q in qs.questions:
        if not q.question:
            warnings.append("dropped a question with no text")
            continue
        # Drop a model-written "Other" option: the card renders its own.
        trimmed = [o for o in q.options if not _is_redundant_other(o.label)]
        if len(trimmed) != len(q.options):
            q.options = trimmed
            q.allow_other = True
        if len(q.options) < 2:
            warnings.append(
                "question %r had %d option(s); it needs at least 2 to be a choice"
                % (q.question[:40], len(q.options)))
            continue
        kept.append(q)

    if len(kept) > MAX_QUESTIONS:
        warnings.append("%d questions asked; only the first %d are shown"
                        % (len(kept), MAX_QUESTIONS))
        kept = kept[:MAX_QUESTIONS]
    if not kept:
        warnings.append("no usable questions found -- expected 'Q:' lines with "
                        "'- option' lines under each, or a JSON list")
    return QuestionSet(kept, warnings)


# -- serializing the answers -------------------------------------------------

UNANSWERED = "(unanswered)"
SKIPPED = "(skipped -- answered in chat)"


def format_answer(picked=None, other_text: str = "", skipped: bool = False) -> str:
    """One question's answer as a single line."""
    if skipped:
        return SKIPPED
    parts = [str(p).strip() for p in (picked or []) if str(p).strip()]
    other_text = _clean(other_text)
    if other_text:
        parts.append('%s: "%s"' % (OTHER_LABEL, other_text))
    return ", ".join(parts) if parts else UNANSWERED


def serialize(qset: QuestionSet, answers) -> str:
    """Render the interview as the Q:/A: block the agent receives.

    ``answers`` is one dict per question: {'picked': [...], 'other': str,
    'skipped': bool}. Short entries are tolerated so a half-filled card (the
    user pressed Esc) serializes exactly like a completed one -- that identical
    shape is the whole point, see the module docstring.
    """
    lines = []
    for i, q in enumerate(qset.questions):
        a = answers[i] if answers and i < len(answers) else {}
        lines.append("Q: %s" % q.question)
        lines.append("A: %s" % format_answer(a.get("picked"),
                                             a.get("other", ""),
                                             a.get("skipped", False)))
        lines.append("")
    return "\n".join(lines).strip()


def has_any_answer(answers) -> bool:
    """True if the user filled in anything at all -- the test for whether an
    Esc-cancelled card is worth prepending to their reply instead of discarded."""
    for a in (answers or []):
        if not isinstance(a, dict):
            continue
        if a.get("skipped"):
            continue
        if [p for p in (a.get("picked") or []) if str(p).strip()]:
            return True
        if _clean(a.get("other", "")):
            return True
    return False


# -- session round-trip ------------------------------------------------------
# A resolved card is stored as a ui_event in conversation_history so a reloaded
# session can re-render it read-only. ui_event entries are saved with the
# session and STRIPPED before the provider sees them (ai_engine: "All other
# ui_event subtypes are silently dropped"), which is what we want -- the answers
# already reached the model once, as the tool observation. Storing them again
# would duplicate them in context.


def to_payload(qset) -> list:
    """JSON-able form of a QuestionSet, in the exact shape parse() accepts back."""
    return [{
        "question": q.question,
        "header": q.header,
        "multiSelect": bool(q.multi),
        "allow_other": bool(q.allow_other),
        "options": [{"label": o.label, "description": o.description}
                    for o in q.options],
    } for q in (getattr(qset, "questions", None) or [])]


def from_payload(data) -> QuestionSet:
    """Rebuild a QuestionSet from to_payload output.

    Goes back through parse() rather than reconstructing the dataclasses by
    hand, so a stored card is validated by the same rules a fresh one is and
    there is no second code path to keep in step.
    """
    try:
        return parse(json.dumps(data or []))
    except (TypeError, ValueError):
        return QuestionSet([], ["unreadable stored question set"])


def normalize_answers(qset, answers) -> list:
    """One well-formed answer dict per question, whatever was stored.

    Old or truncated payloads must not make a reloaded card raise -- a session
    file is the one thing that outlives every refactor.
    """
    out = []
    for i in range(len(getattr(qset, "questions", None) or [])):
        a = answers[i] if answers and i < len(answers) and isinstance(answers[i], dict) else {}
        out.append({
            "picked": [str(p) for p in (a.get("picked") or []) if str(p).strip()],
            "other": _clean(a.get("other", "")),
            "skipped": bool(a.get("skipped", False)),
        })
    return out
