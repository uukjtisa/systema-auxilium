"""
systema/voice/approval_commands.py
Voice command matching for the code-approval dialog.

Pure stdlib and headlessly testable. Matching is WHOLE-UTTERANCE only: the
normalized token tuple of the transcript must exactly equal a known phrase's
token tuple. Substring matching is deliberately unsupported — a command word
misheard inside a longer sentence must never approve code.
"""

import re

# Built-in command vocabularies. Multi-word phrases are allowed; they are
# compared as normalized token tuples.
APPROVE_WORDS = (
    "approve", "approved", "accept", "accepted", "yes", "yeah", "yep",
    "ok", "okay", "run it", "go ahead", "proceed", "execute",
)
DENY_WORDS = (
    "no", "nope", "deny", "denied", "reject", "rejected", "decline",
    "declined", "cancel", "stop", "negative", "don't", "do not",
)
CONFIRM_WORDS = ("confirm", "confirmed")
APPLY_WORDS = ("apply", "apply it", "apply changes", "apply the changes")
DISMISS_WORDS = ("dismiss", "discard")
EXPAND_WORDS = ("expand", "expand it", "show me", "open it", "show details")

_BUILTIN = {}


def _tokens(text):
    """Normalized token tuple of a word/phrase (lowercase, punctuation
    stripped). Mirrors VoiceHandler._normalize_words but keeps order."""
    return tuple(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _build_builtin():
    table = {}
    for words, action in (
        (APPROVE_WORDS, "approve"),
        (DENY_WORDS, "deny"),
        (CONFIRM_WORDS, "confirm"),
        (APPLY_WORDS, "apply"),
        (DISMISS_WORDS, "dismiss"),
        (EXPAND_WORDS, "expand"),
    ):
        for w in words:
            table[_tokens(w)] = action
    return table


_BUILTIN = _build_builtin()


def match_command(text, custom_words=None):
    """Match a transcript against the command vocabularies.

    Returns 'approve' | 'deny' | 'confirm' | 'apply' | 'dismiss' | 'expand' | None.
    custom_words is a {word_or_phrase: 'approve'|'deny'|'expand'} mapping from
    settings; custom entries are checked first so the user can override a built-in.
    """
    toks = _tokens(text)
    if not toks:
        return None
    if custom_words:
        for phrase, action in custom_words.items():
            if action in ("approve", "deny", "expand") and _tokens(phrase) == toks:
                return action
    return _BUILTIN.get(toks)
