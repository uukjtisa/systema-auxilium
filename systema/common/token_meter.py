"""
systema/common/token_meter.py

Per-request token accounting for the in-app AI helpers (code-approval agent and
the update-manager sub-agent). Wraps a provider call, estimates prompt/response
tokens via token_est, keeps a running session total, and logs into the same
usage files the main chat uses — so the dialogs can show "last request: X in /
Y out" and "this session: Z" without touching the provider protocol.

Estimates are heuristic (~4 chars/token) — good enough for a live meter; if a
provider ever returns exact usage we can prefer that.
"""

from __future__ import annotations

from dataclasses import dataclass

from systema.common.token_est import estimate_tokens, log_tokens, log_output_tokens


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class TokenMeter:
    """Tracks token usage across a dialog's AI requests.

    ``account(prompt_text, response_text)`` records one request and returns its
    Usage; ``last`` and ``session`` expose the latest and cumulative totals, and
    ``summary()`` renders the one-line label the dialogs display.
    """

    def __init__(self, label: str = "AI"):
        self.label = label
        self.last = Usage()
        self.session = Usage()
        self.requests = 0

    def account(self, prompt_text: str, response_text: str) -> Usage:
        pin = estimate_tokens(prompt_text or "")
        out = estimate_tokens(response_text or "")
        self.last = Usage(pin, out)
        self.session = Usage(self.session.prompt + pin,
                             self.session.completion + out)
        self.requests += 1
        # Feed the app-wide usage graphs (input + output streams).
        try:
            log_tokens(pin)
            log_output_tokens(out)
        except Exception:
            pass
        return self.last

    def summary(self) -> str:
        if self.requests == 0:
            return "tokens — no requests yet"
        return (f"last request: {self.last.prompt:,} in / {self.last.completion:,} out"
                f"  ·  session: {self.session.total:,} tok over {self.requests} req")
