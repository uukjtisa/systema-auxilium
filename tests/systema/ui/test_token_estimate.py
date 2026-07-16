"""
Tests for the input pill's token-per-request estimate
(systema/ui/chat_window.py :: ChatWindow._update_token_count).

The estimate must measure the EFFECTIVE system prompt (base + loaded skills +
memory block) — the payload the provider actually receives — and refresh
immediately when skills load/unload, not just on keystrokes. Regression: the
pill sat at the fresh-session value (base prompt only) while the Debug window
showed the real, skills-inclusive count.
"""
import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QLabel  # noqa: E402

from systema.ui.chat_window import ChatWindow  # noqa: E402


class _Ai:
    def __init__(self):
        self.system_prompt = "base " * 100                # the OLD (wrong) source
        self.effective = "base " * 100 + "SKILL " * 4000  # what the API really sees
        self.conversation_history = []
        self.chat_history = []

    def _get_effective_system_prompt(self):
        return self.effective


class _Input:
    @staticmethod
    def toPlainText():
        return ""


def _build(qapp):
    stub = types.SimpleNamespace(
        controller=types.SimpleNamespace(ai=_Ai()),
        input_field=_Input(),
        _token_count_lbl=QLabel("~0"),
    )
    stub._token_count_lbl.show()
    stub._update_token_count = types.MethodType(
        ChatWindow._update_token_count, stub)
    stub._invalidate_token_estimate = types.MethodType(
        ChatWindow._invalidate_token_estimate, stub)
    return stub


def _shown_tokens(stub):
    num = stub._token_count_lbl.text().split(" ")[0].lstrip("~")
    return float(num[:-1]) * 1000 if num.endswith("k") else float(num)


def test_estimate_uses_effective_prompt_not_base(qapp):
    stub = _build(qapp)
    stub._update_token_count()
    shown = _shown_tokens(stub)
    base_only = len(stub.controller.ai.system_prompt) // 4
    assert shown > base_only * 4  # loaded skills dominate the estimate


def test_estimate_refreshes_on_skill_change(qapp):
    stub = _build(qapp)
    stub._update_token_count()
    before = _shown_tokens(stub)

    stub.controller.ai.effective += "MORE SKILL " * 8000

    stub._update_token_count()  # keystroke path — cached system part, unchanged
    assert _shown_tokens(stub) == before

    stub._invalidate_token_estimate()  # the loaded_skills_changed path
    assert _shown_tokens(stub) > before
