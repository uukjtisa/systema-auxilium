"""
systema/agents/compactor_agent.py
CompactorAgent — rewrites a toolcall's OUTPUT into its most concise
detail-preserving summary (the "Compact all toolcalls" session tool).

Contract (user-specified): lose NOTHING a future turn might need — every
concrete value, path, number and error survives verbatim; only redundancy,
boilerplate and decorative formatting go. The step's INPUT CODE (especially
its comments) tells the agent what the step was trying to find, so those
findings are what the summary must carry.

Provider: the main AIEngine's plain messages→text path (same pattern as
agents/code_agent.py `_default_provider`) — no tools, one call per output.
"""
from systema.common.logger import _make_logger

log = _make_logger("CompactorAgent")

_SYSTEM = """You are a context compactor for an AI assistant's tool history.
You will receive the Python code of a completed tool step and the raw output
it produced. Rewrite the OUTPUT as the most concise summary possible WITHOUT
losing any information a future conversation turn might need:

- Keep every concrete finding verbatim: values, numbers, file paths, names,
  error messages, versions, IDs.
- Remove redundant characters, repeated boilerplate, progress spam, decorative
  separators, and anything derivable from what you keep.
- The INPUT CODE's comments and structure tell you what the step was trying to
  find — those findings MUST survive in the summary.
- Plain text only. No preamble, no markdown fences, no commentary about the
  task. Output ONLY the compacted text."""


class CompactorAgent:
    """One-shot per-output compactor over the app's configured provider."""

    def __init__(self, ai_engine):
        self.ai = ai_engine

    def compact(self, code: str, output: str) -> str | None:
        """Return the compacted output text, or None when the call failed or
        produced no real gain (caller then leaves the output untouched)."""
        try:
            messages = [
                {'role': 'system', 'content': _SYSTEM},
                {'role': 'user', 'content':
                    f"INPUT CODE (comments = what mattered):\n{code}\n\n"
                    f"RAW OUTPUT TO COMPACT:\n{output}"},
            ]
            # background_call: compaction output is not the visible chat turn —
            # no streaming deltas, no thinking card, no scratch-state clobber.
            with self.ai.background_call("compactor"):
                reply = (self.ai._provider_script(messages, stream_ok=False) or "").strip()
            if not reply:
                log.warning("[CompactorAgent.compact] empty reply — skipping")
                return None
            # No-gain guard: a "summary" as big as the original is not a win.
            if len(reply) >= len(output) * 0.9:
                log.info(f"[CompactorAgent.compact] no gain "
                         f"({len(output)} → {len(reply)}) — skipping")
                return None
            return reply
        except Exception as e:
            log.error(f"[CompactorAgent.compact] {type(e).__name__}: {e}")
            return None
