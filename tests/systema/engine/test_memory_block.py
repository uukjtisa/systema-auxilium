"""
Tests for AIEngine._build_memory_block — the inject-all token cap.

The method is exercised unbound on a stub, so no engine, provider, or Qt
machinery is constructed. Importing ai_engine still needs its module-level
dependencies, hence the PyQt6 importorskip.
"""
import pytest

pytest.importorskip("PyQt6.QtCore")

from systema.engine.ai_engine import AIEngine


class _FakeMM:
    def __init__(self, texts):
        self.is_ready = True
        self._texts = texts

    def get_all(self):
        # Index 0 = newest, matching the real get_all() (sorted newest-first).
        return [{"id": str(i), "text": t,
                 "created_at": f"2026-07-{19 - (i % 19):02d}T00:00:00",
                 "edited": False}
                for i, t in enumerate(self._texts)]


class _Stub:
    _MEM_BLOCK_START = AIEngine._MEM_BLOCK_START
    _MEM_BLOCK_END = AIEngine._MEM_BLOCK_END

    def __init__(self, texts, settings):
        self.memory_manager = _FakeMM(texts)
        self.settings_callback = lambda: settings


def _block(texts, **overrides):
    settings = {"memory_enabled": True, "memory_recall_mode": "inject_all",
                "memory_inject_cap_tokens": 2000}
    settings.update(overrides)
    return AIEngine._build_memory_block(_Stub(texts, settings))


def test_block_lists_all_memories_under_cap():
    b = _block(["Alpha\n\nshort.", "Beta\n\nshort."])
    assert b is not None
    assert "- Alpha" in b and "- Beta" in b
    assert "omitted" not in b


def test_block_caps_newest_first_and_appends_omitted_line():
    texts = [f"Memory {i}\n\n" + ("x" * 400) for i in range(10)]
    b = _block(texts, memory_inject_cap_tokens=300)
    assert "[8 older memories omitted - use search_memory()]" in b
    assert "Memory 0" in b          # newest kept
    assert "Memory 9" not in b      # oldest dropped


def test_block_always_includes_newest_even_over_cap():
    b = _block(["Huge\n\n" + ("y" * 4000)], memory_inject_cap_tokens=100)
    assert b is not None
    assert "Huge" in b
    assert "omitted" not in b


def test_block_none_in_rag_mode():
    assert _block(["A\n\nb."], memory_recall_mode="rag") is None


def test_block_none_when_memory_disabled():
    assert _block(["A\n\nb."], memory_enabled=False) is None


# ── Send-path: the block must actually reach the effective prompt ─────────────
# Regression guard for the bug that shipped "done" — _build_memory_block was
# correct, but _get_effective_system_prompt read the block from a freshly
# recomposed base (which never has it) instead of self.system_prompt, so
# inject_all was a silent no-op. The old suite only tested _build_memory_block
# in isolation and never that it reaches the prompt.

class _EngStub:
    _MEM_BLOCK_START = AIEngine._MEM_BLOCK_START
    _MEM_BLOCK_END = AIEngine._MEM_BLOCK_END
    _strip_memory_block = AIEngine._strip_memory_block
    _get_memory_block = AIEngine._get_memory_block
    _get_effective_system_prompt = AIEngine._get_effective_system_prompt

    def __init__(self, system_prompt, base):
        self.system_prompt = system_prompt
        self.system_prompt_hijacked = False
        self.custom_system_prompt = ""
        self._base = base

    def _compose_system_prompt(self):
        return self._base

    def _render_active_skills(self):
        return ""


def _mk_block(body="- Alpha"):
    return (f"\n\n{AIEngine._MEM_BLOCK_START}\n\nALL MEMORIES:\n{body}\n"
            f"{AIEngine._MEM_BLOCK_END}")


def test_effective_prompt_grafts_block_from_system_prompt():
    stub = _EngStub(system_prompt="BASE" + _mk_block(), base="FRESH COMPOSE")
    out = stub._get_effective_system_prompt()
    assert AIEngine._MEM_BLOCK_START in out   # block actually ships
    assert "- Alpha" in out
    assert "FRESH COMPOSE" in out             # fresh base preserved (live tool-mode)


def test_effective_prompt_no_block_when_system_prompt_lacks_one():
    stub = _EngStub(system_prompt="BASE, no memory block here", base="FRESH")
    out = stub._get_effective_system_prompt()
    assert AIEngine._MEM_BLOCK_START not in out
    assert out == "FRESH"


# ── summarize_memory_injection (single-source cap + readout counter) ──────────

def test_summarize_prefix_cut_and_totals():
    from systema.engine.ai_engine import summarize_memory_injection
    mems = [{"text": f"Memory {i}\n\n" + ("x" * 400)} for i in range(10)]
    s = summarize_memory_injection(mems, 300)
    assert s["kept"] == 2 and s["omitted"] == 8
    assert s["total_tokens"] > s["used_tokens"]        # total counts all, used only kept
    assert s["entries"][0].startswith("- Memory 0")    # newest-first prefix


def test_summarize_always_keeps_newest_over_cap():
    from systema.engine.ai_engine import summarize_memory_injection
    s = summarize_memory_injection([{"text": "Huge\n\n" + "y" * 4000}], 100)
    assert s["kept"] == 1 and s["omitted"] == 0


def test_summarize_handles_empty_text():
    from systema.engine.ai_engine import summarize_memory_injection
    s = summarize_memory_injection([{"text": ""}], 5000)   # must not IndexError
    assert s["kept"] == 1
