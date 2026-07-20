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
