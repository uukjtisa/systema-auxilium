"""
systema/agents/session_namer_agent.py
SessionNamerAgent — titles a conversation automatically in the background (the
claude.ai/ChatGPT pattern). The MAIN model no longer names sessions; this
separate lightweight call does, from a short digest of the conversation.

Provider: the main AIEngine's plain messages->text path (same pattern as
agents/compactor_agent.py) — no tools, one cheap call per naming.
"""
from systema.common.logger import _make_logger

log = _make_logger("SessionNamerAgent")

_SYSTEM = """You title chat conversations. Given a short digest of a conversation,
reply with a concise, specific title.

RULES:
- 2 to 5 words. Title Case. No quotes, no trailing punctuation, no emoji.
- Be specific to the ACTUAL topic (e.g. "Desktop File Sorting Script", not
  "Coding Help").
- If a CURRENT TITLE is given, KEEP IT unless the conversation has clearly moved
  to a different topic — only then propose a new one.
- Output ONLY the title text. No preamble, no explanation, no markdown, no code
  fences."""


class SessionNamerAgent:
    """One-shot conversation titler over the app's configured provider."""

    def __init__(self, ai_engine):
        self.ai = ai_engine

    def generate(self, digest: str, current_title: str = "") -> str | None:
        """Return a short title, or None on failure / when the title should not
        change (the caller then keeps the existing title)."""
        try:
            user = f"CONVERSATION DIGEST:\n{digest}"
            if current_title:
                user = f"CURRENT TITLE: {current_title}\n\n{user}"
            messages = [
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content': user},
            ]
            reply = (self.ai._provider_script(messages) or "").strip()
            if not reply:
                log.warning("[SessionNamerAgent.generate] empty reply")
                return None
            # Sanitize: first non-empty line, strip quotes/fences/punctuation,
            # cap to a few words.
            title = ""
            for line in reply.splitlines():
                s = line.strip().strip('"\'`').strip()
                if s:
                    title = s
                    break
            title = title.rstrip('.:;,').strip()
            if not title:
                return None
            words = title.split()
            if len(words) > 6:
                title = " ".join(words[:6])
            title = title[:48].strip()
            if not title:
                return None
            if current_title and title.lower() == current_title.strip().lower():
                return None  # unchanged — keep the existing title
            log.info(f"[SessionNamerAgent.generate] title='{title}'")
            return title
        except Exception as e:
            log.error(f"[SessionNamerAgent.generate] {type(e).__name__}: {e}")
            return None
