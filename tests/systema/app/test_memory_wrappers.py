"""
Tests for the controller's 4-function AI-facing memory toolset — pure logic
against a fake backend; no QApplication and no real embeddings.

Importing systema.app.controller pulls in PyQt6, so the module skips cleanly
where Qt is absent (same policy as the qapp fixture).
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.app.controller import AssistantController


class _FakeBackend:
    def __init__(self, texts):
        self.is_ready = True
        self._m = [{"id": f"id{i}", "text": t,
                    "created_at": "2026-07-19T12:00:00", "edited": False}
                   for i, t in enumerate(texts)]
        self.updated = []          # (id, new_text) calls

    def get_all(self):
        return [dict(m) for m in self._m]

    def delete(self, mid):
        before = len(self._m)
        self._m = [m for m in self._m if m["id"] != mid]
        return len(self._m) < before

    def update(self, mid, new_text):
        self.updated.append((mid, new_text))
        for m in self._m:
            if m["id"] == mid:
                m["text"] = new_text
                return True
        return False

    def recall(self, query, threshold=0.4, max_results=3):
        return []


class _Ctrl:
    """Borrows the real wrapper methods; fakes the collaborators they touch."""
    _split_memory_blob = staticmethod(AssistantController._split_memory_blob)
    memorize = AssistantController.memorize
    search_memory = AssistantController.search_memory
    update_memory = AssistantController.update_memory
    forget_memory = AssistantController.forget_memory

    def __init__(self, texts):
        self.memory_manager = _FakeBackend(texts)
        self.refreshed = 0

    def refresh_memory_block(self):
        self.refreshed += 1


BLOBS = [
    "Coffee preference\n\nLikes strong espresso.\n\nTags: coffee, preferences",
    "Coffee machine model\n\nUses a Gaggia Classic.\n\nTags: hardware",
    "Sleep schedule\n\nTargets 11pm-7am.",
]


def test_search_memory_empty_query_lists_all_titles():
    c = _Ctrl(BLOBS)
    out = c.search_memory("")
    assert "All 3 memory title(s)" in out
    assert "Coffee preference" in out and "Sleep schedule" in out
    assert "Likes strong espresso" not in out      # titles only, no bodies


def test_update_memory_ambiguous_changes_nothing():
    c = _Ctrl(BLOBS)
    out = c.update_memory("Coffee", new_body="x")
    assert "Ambiguous" in out
    assert not c.memory_manager.updated
    assert c.refreshed == 0


def test_update_memory_preserves_unspecified_parts():
    c = _Ctrl(BLOBS)
    out = c.update_memory("Sleep schedule", new_body="Targets 10pm-6am.")
    assert "Updated" in out
    (mid, text), = c.memory_manager.updated
    assert text.startswith("Sleep schedule\n\n")
    assert "10pm-6am" in text
    assert c.refreshed == 1


def test_update_memory_can_retitle_and_keep_tags():
    c = _Ctrl(BLOBS)
    c.update_memory("Coffee machine", new_title="Espresso machine model")
    (_, text), = c.memory_manager.updated
    assert text.split("\n")[0] == "Espresso machine model"
    assert text.rstrip().endswith("Tags: hardware")


def test_update_memory_requires_a_change():
    c = _Ctrl(BLOBS)
    out = c.update_memory("Sleep schedule")
    assert "Nothing to change" in out
    assert not c.memory_manager.updated


def test_forget_memory_exact_title_deletes_exactly_one():
    c = _Ctrl(BLOBS)
    out = c.forget_memory("coffee preference")     # case-insensitive exact title
    assert "exact title" in out
    remaining = [m["text"].split("\n")[0] for m in c.memory_manager._m]
    assert remaining == ["Coffee machine model", "Sleep schedule"]
    assert c.refreshed == 1


def test_forget_memory_substring_bulk_deletes_and_names_titles():
    c = _Ctrl(BLOBS)
    out = c.forget_memory("coffee")
    assert "Bulk-deleted 2" in out
    assert "Coffee preference" in out and "Coffee machine model" in out
    assert len(c.memory_manager._m) == 1
