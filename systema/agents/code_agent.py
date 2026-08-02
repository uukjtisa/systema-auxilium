"""
systema/agents/code_agent.py

The coding sub-agent embedded in the code-approval dialog — a minimal personal
code EDITOR for the single snippet the main AI wants to run.

It has exactly ONE tool: ``propose_edit`` — it proposes a COMPLETE revised
snippet, which the dialog renders as a diff with a one-click Apply. Any
explanation is just the agent's plain reply text (shown in the chat). There are
no other tools: no view / risks / run_python / say / finish — the dialog already
shows the code and the static risk scan, so the agent's only job is to explain
and, when asked, propose an edit.

  • Same provider — reuses the main AIEngine's provider (native OR compat).
  • Own system prompt — a focused code-editor role, NOT the main assistant.
  • Redaction — any code text sent to the provider is passed through
    ``code_guard.redact_secrets`` first, so a key in the snippet never leaves the
    machine. The agent never executes anything; edits are only ever PROPOSED.
"""

from __future__ import annotations

import re
import json
from typing import Callable

from systema.engine import provider_contract as pc
from systema.security.code_guard import (scan_code, summarize_findings,
                                         redact_secrets)

# Compat (text-fence) role. The one tool is a fenced block whose body is the full
# replacement snippet, so multi-line code never needs JSON escaping.
SUB_SYSTEM_PROMPT = """\
You are the Code Editor, a focused sub-agent inside Systema Auxilium. The main AI
wants to run one piece of code and the user must approve it. Your ONLY job: explain
it briefly and, when the user asks for a change or a safer version, propose an edit.
You are NOT the main assistant and you do NOT execute anything.

To propose an edit, reply with a short plain-text explanation, then ONE fenced block
whose body is the COMPLETE revised snippet:

```propose_edit: short reason for the change
<the ENTIRE revised snippet, verbatim, multi-line is fine>
```

Rules:
- The body is the WHOLE snippet (not a diff, not JSON, not a partial). The user sees
  a diff of your snippet vs theirs and clicks Apply.
- Keep behaviour intact unless the user asks otherwise; prefer minimal, safe edits
  (scope a delete to the app dir, add a guard, remove a hardcoded secret).
- If no change is needed, just explain in plain text — no fence.
- Never claim you executed the code.
"""

# Native role — the one tool travels through the provider's function-calling
# channel, so this prompt must NOT teach the fence syntax.
SUB_SYSTEM_PROMPT_NATIVE = """\
You are the Code Editor, a focused sub-agent inside Systema Auxilium. The main AI
wants to run one piece of code and the user must approve it. Your ONLY job: explain
it briefly and, when the user asks for a change or a safer version, propose an edit.
You are NOT the main assistant and you do NOT execute anything.

To propose an edit, CALL the propose_edit tool (a native function call — do not write
fences or JSON as text) with:
  - code : the COMPLETE revised snippet (the WHOLE thing, not a diff or partial)
  - why  : one short reason for the change
Put any explanation in your normal reply text. The user sees a diff of your snippet
vs theirs and clicks Apply. Keep behaviour intact unless asked; prefer minimal, safe
edits. If no change is needed, just explain in text. Never claim you executed the code.
"""

# The single body-fenced tool (compat) + a matcher to strip it from prose.
_PROPOSE_RE = re.compile(r"```propose_edit(?::[ \t]*(.*?))?\s*\n(.*?)\n```", re.DOTALL)
_ALL_FENCES_RE = re.compile(r"```.*?```", re.DOTALL)


class CodeAgent:
    """Explains and edits one code snippet against the shared provider.

    ``on_message(role, text)`` streams the explanation to the chat;
    ``on_proposal(code, why)`` fires when the agent proposes a replacement (the
    dialog renders the diff + Apply). One tool only: propose_edit.
    """

    def __init__(self, code: str, execution_type: str, ai_engine=None,
                 provider_fn: Callable[[list], str] | None = None,
                 on_message: Callable[[str, str], None] | None = None,
                 on_proposal: Callable[[str, str], None] | None = None,
                 meter=None, history: list | None = None):
        self.code = code
        self.execution_type = execution_type
        self.ai = ai_engine
        self._using_default = provider_fn is None
        self._provider_fn = provider_fn or self._default_provider
        self.on_message = on_message or (lambda role, text: None)
        self.on_proposal = on_proposal or (lambda code, why: None)
        self.meter = meter
        # Prior dialog turns ({'role': 'user'|'assistant', 'content': str}) so
        # follow-up questions keep their context across single-shot runs.
        self.history = [m for m in (history or [])
                        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        and (m.get("content") or "").strip()]
        self.findings = scan_code(code)
        self.last_proposal: str | None = None

    # -- provider (same as the app) -------------------------------------------
    def _default_provider(self, messages: list) -> str:
        if self.ai is None:
            raise RuntimeError("no provider available")
        # background_call + stream_ok=False: this agent runs inside the approval
        # dialog while a chat turn is suspended mid-batch — its tokens must never
        # appear in that turn, and it must not clobber the turn's scratch state.
        with self.ai.background_call("code-agent"):
            text = self.ai._provider_script(messages, stream_ok=False)
            if not text and hasattr(self.ai, "_get_ai_response_internal"):
                user = next((m["content"] for m in reversed(messages)
                             if m["role"] == "user"), "")
                text = self.ai._get_ai_response_internal(user)
        return text or ""

    def _ask(self, convo: list) -> str:
        reply = (self._provider_fn(convo) or "").strip()
        if self.meter is not None:
            try:
                self.meter.account("\n".join(m.get("content", "") for m in convo), reply)
            except Exception:
                pass
        return reply

    # -- the single tool ------------------------------------------------------
    def _tool_schemas(self) -> list[dict]:
        return [{
            "name": "propose_edit",
            "description": "Propose the COMPLETE revised replacement snippet "
                           "(shown to the user as a diff to apply).",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "description": "the full replacement snippet"},
                "why": {"type": "string", "description": "one short reason for the change"},
            }, "required": ["code"]},
        }]

    def _emit_proposal(self, code, why) -> bool:
        if not isinstance(code, str) or not code.strip():
            return False
        self.last_proposal = code
        self.on_proposal(code, str(why or "").strip())
        return True

    def _native_module(self):
        """The provider module if native tool calling is active, else None.

        Obeys the app-wide `tool_calling_mode` setting first: in 'compat' mode
        the sub-agent must use the fence path even when the provider could do
        native calls — same mode selection as the main engine."""
        if self.ai is None or not self._using_default:
            return None
        try:
            if getattr(self.ai, "_tool_mode", lambda: "compat")() != "native":
                return None
            mod = self.ai._load_provider_module()
            if mod is not None and self.ai._native_provider_supported(mod):
                return mod
        except Exception:
            pass
        return None

    # -- run (single round-trip) ----------------------------------------------
    def run(self, task: str) -> str:
        red_code, nred = redact_secrets(self.code)
        preamble = (f"Execution type: {self.execution_type}. "
                    f"Static scan: {summarize_findings(self.findings)}."
                    + (f" ({nred} secret(s) redacted from the copy you see.)" if nred else ""))
        user0 = f"{task}\n\n{preamble}\n\nCode:\n```\n{red_code}\n```"
        mod = self._native_module()
        return self._run_native(mod, user0) if mod is not None else self._run_compat(user0)

    def _run_native(self, mod, user0: str) -> str:
        convo = list(self.history) + [{"role": "user", "content": user0}]
        try:
            # THE call path — the same pc.invoke() the main engine uses. This
            # used to call mod.chat_tools() directly: an entry point from the
            # retired first contract that no shipped provider has ever defined,
            # so every native run raised AttributeError, was swallowed below,
            # and the sub-agent's native mode was a silent no-op.
            res = pc.invoke(mod, SUB_SYSTEM_PROMPT_NATIVE, convo,
                            tools=self._tool_schemas())
        except Exception as e:
            self.on_message("system", f"native tool-call error: {e}")
            return ""
        if not isinstance(res, dict):
            return ""
        text = (res.get("content") or "").strip()
        calls = res.get("tool_calls") or []
        if self.meter is not None:
            try:
                self.meter.account(SUB_SYSTEM_PROMPT_NATIVE + "\n" + user0,
                                   text + json.dumps(calls))
            except Exception:
                pass

        proposed = False
        for c in calls:
            if (c.get("name") or "") != "propose_edit":
                continue
            args = c.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            proposed = self._emit_proposal(args.get("code"), args.get("why")) or proposed

        # Fallback: a native model that ignored the tools channel and wrote the
        # fence as text — parse it so the edit still surfaces as a diff.
        if not proposed and _PROPOSE_RE.search(text):
            for why, code in _PROPOSE_RE.findall(text):
                proposed = self._emit_proposal(code, why) or proposed
            text = _ALL_FENCES_RE.sub("", text).strip()

        if text:
            self.on_message("agent", text)
        return text

    def _run_compat(self, user0: str) -> str:
        convo = ([{"role": "system", "content": SUB_SYSTEM_PROMPT}]
                 + list(self.history)
                 + [{"role": "user", "content": user0}])
        reply = self._ask(convo)
        if not reply:
            return ""
        for why, code in _PROPOSE_RE.findall(reply):
            self._emit_proposal(code, why)
        prose = _ALL_FENCES_RE.sub("", reply).strip()
        if prose:
            self.on_message("agent", prose)
        return prose
