"""
systema/common/image_refs.py

Pure functions over the ImageRefs embedded in a conversation history. No state,
no Qt, no engine import — so the UI, the tool manager and the engine can all
share ONE definition of "which images does this conversation have, and what is
image 3".

WHERE REFS LIVE
---------------
An ImageRef (see `systema.common.image_cache`) sits on the history entry it
belongs to, under `_images`:

    {'role': 'user', 'content': 'look at this', '_images': [ref, ...]}
    {'role': 'ui_event', '_type': 'image_attach', '_images': [ref, ...]}

That is the whole point of the redesign — an image is positional content of a
message, not a floating attachment on the newest turn. Everything here reads
that one representation; there is deliberately no second index to fall out of
sync with it.

NUMBERING
---------
Every image gets a session-global `n`, assigned in attach order across BOTH
sides (the user's attachments and the agent's `attach_image_to_chat`), so
"image 3" is unambiguous no matter who added it.

Numbers are NEVER REUSED. Deleting image 3 leaves a gap; the next attachment
becomes 5, not 3. This costs pretty contiguous numbering and buys correctness:
an earlier line in the transcript saying "image 3 shows the header" must never
silently start pointing at a different picture. That is also why the counter is
PERSISTED (`next_image_n` in the session file) instead of being derived from
max(n)+1 — deleting the highest-numbered image would otherwise recycle it.
"""


def iter_refs(history):
    """Yield (entry, ref) for every ImageRef in the history, in order."""
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        for ref in entry.get("_images") or []:
            if isinstance(ref, dict):
                yield entry, ref


def all_refs(history) -> list:
    """Every ImageRef in the conversation, in attach order."""
    return [ref for _entry, ref in iter_refs(history)]


def attached_refs(history) -> list:
    """Only the refs currently IN the model's context."""
    return [ref for ref in all_refs(history) if ref.get("attached")]


def detached_refs(history) -> list:
    return [ref for ref in all_refs(history) if not ref.get("attached")]


def next_number(history, stored_next=None) -> int:
    """The number the next attachment should take.

    `stored_next` is the session's persisted counter and WINS when it is ahead
    of everything in history — that is what keeps a deleted image's number
    retired. The history scan is only a floor, for sessions written before the
    counter existed.
    """
    floor = 1
    for ref in all_refs(history):
        try:
            floor = max(floor, int(ref.get("n", 0)) + 1)
        except (TypeError, ValueError):
            continue
    try:
        if stored_next is not None:
            return max(floor, int(stored_next))
    except (TypeError, ValueError):
        pass
    return floor


def find(history, n):
    """(entry, ref) for image `n`, or (None, None)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None, None
    for entry, ref in iter_refs(history):
        if int(ref.get("n", -1)) == n:
            return entry, ref
    return None, None


def set_attached(history, n, attached: bool) -> bool:
    """Flip an image in/out of the model's context. Reversible both ways.

    Returns True when a ref actually changed, so callers can skip a re-render
    and a session save when nothing happened.
    """
    _entry, ref = find(history, n)
    if ref is None or bool(ref.get("attached")) == bool(attached):
        return False
    ref["attached"] = bool(attached)
    return True


def set_all_attached(history, attached: bool) -> int:
    """Bulk detach/attach. Returns how many refs changed."""
    changed = 0
    for ref in all_refs(history):
        if bool(ref.get("attached")) != bool(attached):
            ref["attached"] = bool(attached)
            changed += 1
    return changed


def remove(history, n) -> dict | None:
    """Drop image `n` from the conversation entirely and return its ref.

    The number is NOT recycled — see the module docstring. An entry left with
    no images and no text is dropped too, so a delete cannot leave an empty
    bubble behind; an entry that still carries text keeps its place.
    """
    entry, ref = find(history, n)
    if ref is None:
        return None
    imgs = entry.get("_images") or []
    entry["_images"] = [r for r in imgs if r is not ref]
    if not entry["_images"]:
        entry.pop("_images", None)
        is_image_event = (entry.get("role") == "ui_event"
                          and entry.get("_type") == "image_attach")
        if is_image_event or not str(entry.get("content") or "").strip():
            try:
                history.remove(entry)
            except ValueError:
                pass
    return ref


# ── model-facing text ────────────────────────────────────────────────────────
# These strings are what the AI actually reads. Kept here (not in the prompt
# package) because they are per-image data, not prompt policy, and both the
# compat and native payload builders must emit the IDENTICAL text — image
# behaviour differing by tool-calling mode would violate the parity rule.

def attached_marker(ref: dict) -> str:
    """Label placed immediately before an image block so the model can name it."""
    return f"[Image {ref.get('n')}]"


def detached_marker(ref: dict) -> str:
    """Replaces the image block once the user detaches it from context.

    Deliberately short and instructive: the model needs to know the picture is
    gone and that guessing at its contents is not acceptable, in as few tokens
    as that can be said.
    """
    return (f"[Image {ref.get('n')} was detached from your context and is no "
            f"longer visible to you. If asked about it, say so and offer to "
            f"have it re-attached; never guess its contents.]")


def missing_marker(ref: dict) -> str:
    """Cached bytes are gone (cache cleared, session moved between machines)."""
    return (f"[Image {ref.get('n')} is no longer available on disk. "
            f"Ask the user to re-attach it if you need to see it.]")
