"""
Tests for systema/memory/memory_manager.py — hybrid recall scoring and the
store-format migration.

A fake `_embed` avoids loading the ONNX model, and the worker-thread
indirection is bypassed by calling the _do_* methods directly, so these run
fast and offline. numpy is the only non-stdlib dependency.
"""
import json

import pytest

pytest.importorskip("numpy")

from systema.memory.memory_manager import MemoryManager


def _fake_embed(text):
    # Deterministic 4-dim embedding: crude letter-bucket counts, so identical
    # texts embed identically and different texts differ.
    v = [1.0, 0.0, 0.0, 0.0]
    for ch in text.lower():
        v[ord(ch) % 4] += 1.0
    return v


def _mm(tmp_path):
    """A MemoryManager shell: real methods, no model, no worker round-trips."""
    mm = MemoryManager.__new__(MemoryManager)
    mm._model = None
    mm._model_name = "BAAI/bge-small-en-v1.5"
    mm._memories = []
    mm._ready = True
    mm._unavailable_reason = ""
    mm.memories_dir = tmp_path
    mm.store_path = tmp_path / "memories.json"
    mm._embed = _fake_embed
    return mm


def _mem(text, mid="id1", created="2026-07-19T12:00:00"):
    return {"id": mid, "text": text, "embedding": _fake_embed(text),
            "created_at": created, "edited": False}


# ── keyword overlap ──────────────────────────────────────────────────────────

def test_keyword_overlap_title_and_tags_count_double():
    m = "Drone hub PCB\n\nSome body words here.\n\nTags: kicad, esp32"
    strong = MemoryManager._keyword_overlap("kicad drone", m)
    weak = MemoryManager._keyword_overlap("body words", m)
    assert strong == 1.0            # both tokens hit title/tags (double weight)
    assert 0.0 < weak <= 0.5        # body-only tokens score half


def test_keyword_overlap_stopword_only_query_scores_zero():
    m = "Title\n\nBody.\n\nTags: t"
    assert MemoryManager._keyword_overlap("what is the", m) == 0.0


# ── hybrid recall ────────────────────────────────────────────────────────────

def test_hybrid_recall_ranks_keyword_match_first(tmp_path):
    mm = _mm(tmp_path)
    mm._embed = lambda text: [1.0, 1.0, 1.0, 1.0]
    mm._memories = [
        _mem("Unrelated topic\n\nNothing shared at all.", "a"),
        _mem("Kicad routing tip\n\nUse the push router.\n\nTags: kicad", "b"),
    ]
    # Identical stored vectors -> cosine ties; the keyword term must decide.
    for m in mm._memories:
        m["embedding"] = [1.0, 1.0, 1.0, 1.0]
    res = mm._do_recall("kicad routing", threshold=0.1, max_results=2)
    assert res and res[0]["id"] == "b"
    assert "created_at" in res[0]


def test_recency_tiebreak_prefers_newer(tmp_path):
    mm = _mm(tmp_path)
    mm._embed = lambda text: [1.0, 0.0, 0.0, 0.0]
    old = _mem("Alpha\n\nSame body.", "old", created="2024-07-20T12:00:00")
    new = _mem("Beta\n\nSame body.", "new", created="2026-07-19T12:00:00")
    for m in (old, new):
        m["embedding"] = [1.0, 0.0, 0.0, 0.0]
    mm._memories = [old, new]
    res = mm._do_recall("zzz nomatch", threshold=0.0, max_results=2)
    assert [r["id"] for r in res][0] == "new"


# ── store format + migration ─────────────────────────────────────────────────

def test_load_store_migrates_legacy_bare_list(tmp_path):
    mm = _mm(tmp_path)
    legacy = [_mem("Old memory\n\nBody.", "x")]
    legacy[0]["embedding"] = [9.9, 9.9, 9.9, 9.9]   # stale model's vectors
    mm.store_path.write_text(json.dumps(legacy), encoding="utf-8")
    calls = []
    mm._embed = lambda t: calls.append(t) or _fake_embed(t)
    mm._load_store()
    assert len(mm._memories) == 1
    assert calls, "legacy store must be re-embedded"
    data = json.loads(mm.store_path.read_text(encoding="utf-8"))
    assert data["model_name"] == mm._model_name
    assert isinstance(data["memories"], list)


def test_load_store_same_model_skips_reembed(tmp_path):
    mm = _mm(tmp_path)
    payload = {"model_name": mm._model_name, "memories": [_mem("A\n\nB.", "x")]}
    mm.store_path.write_text(json.dumps(payload), encoding="utf-8")
    calls = []
    mm._embed = lambda t: calls.append(t) or _fake_embed(t)
    mm._load_store()
    assert len(mm._memories) == 1
    assert not calls


def test_save_store_records_model_name(tmp_path):
    mm = _mm(tmp_path)
    mm._memories = [_mem("A\n\nB.")]
    mm._save_store()
    data = json.loads(mm.store_path.read_text(encoding="utf-8"))
    assert set(data) == {"model_name", "memories"}
