"""
core/memory_manager.py
Memory Manager - Persistent RAG-based memory system
Uses fastembed (sentence-transformers/all-MiniLM-L6-v2) for embeddings.
fastembed runs the model via ONNX Runtime — no torch, no DLL conflicts.
Uses plain JSON + numpy for storage — no ChromaDB, no hnswlib, no native crashes.

Install deps:
    pip install fastembed numpy

Storage: data/memories/memories.json (auto-created)

Threading note:
    fastembed (ONNX Runtime) crashes with 0xC0000005 on Windows when called
    from a QThread. ALL operations run on a single dedicated worker thread
    (_worker) that is created and stays alive for the lifetime of the app.
    Public methods submit work to that thread and block for the result.
    numpy cosine similarity replaces ChromaDB/hnswlib — zero native threading
    issues and fast enough for any realistic number of memories.
"""

import os
import uuid
import json
import shutil
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from core.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("MemoryManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

_APP_ROOT = Path(__file__).resolve().parent.parent


class MemoryManager:
    """
    Persistent semantic memory store for the AI assistant.

    - memorize(text)           → embed + store a memory
    - recall(query, ...)       → cosine similarity search
    - get_all()                → list all memories (for UI)
    - update(id, new_text)     → re-embed + overwrite a memory
    - delete(id)               → remove by ID
    - clear()                  → wipe everything
    - count()                  → number of stored memories

    Storage: JSON file with embeddings.
    ALL operations run on a single dedicated worker thread.
    """

    def __init__(self):
        self._model = None
        self._memories = []   # list of dicts: {id, text, embedding, created_at, edited}
        self._ready = False
        self._unavailable_reason: str = ""

        # Single worker thread — fastembed ONNX lives here forever
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MemWorker")

        self.memories_dir = _APP_ROOT / "data" / "memories"
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.memories_dir / "memories.json"
        log.info(f"[MemoryManager.__init__] Store path: '{self.store_path}'")

        # Init on worker thread so ONNX session is created and stays there
        self._worker.submit(self._init).result()

    # ── Internal: runs on worker thread only ─────────────────────────────────

    def _init(self):
        """Load model + data — called on worker thread."""
        try:
            log.info("[MemoryManager._init] Loading fastembed model: sentence-transformers/all-MiniLM-L6-v2")
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

            # Warmup: force ONNX InferenceSession to fully initialize on THIS
            # thread so it never lazy-inits on a QThread → 0xC0000005 on Windows.
            list(self._model.embed(["warmup"]))
            log.info("[MemoryManager._init] ✓ Model loaded and warmed up")

            self._load_store()
            self._ready = True
            log.info(f"[MemoryManager._init] ✓ Ready | {len(self._memories)} existing memories")

        except ImportError as e:
            missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
            _pip = {"fastembed": "fastembed", "numpy": "numpy"}.get(missing, missing)
            self._unavailable_reason = f"Missing package — run: pip install {_pip}"
            log.error(f"[MemoryManager._init] ✗ Missing dependency: {e}")
        except Exception as e:
            self._unavailable_reason = f"{type(e).__name__}: {e}"
            log.error(f"[MemoryManager._init] ✗ Failed: {type(e).__name__}: {e}")

    def _load_store(self):
        """Load memories from JSON file."""
        if not self.store_path.exists():
            self._memories = []
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                self._memories = json.load(f)
            log.info(f"[MemoryManager._load_store] Loaded {len(self._memories)} memories")
        except Exception as e:
            log.warning(f"[MemoryManager._load_store] Failed to load ({e}) — backing up and starting fresh")
            backup = self.store_path.with_suffix(".json.bak")
            if self.store_path.exists():
                shutil.copy(self.store_path, backup)
            self._memories = []

    def _save_store(self):
        """Save memories to JSON file."""
        try:
            tmp = self.store_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._memories, f, ensure_ascii=False, indent=2)
            tmp.replace(self.store_path)  # atomic replace
        except Exception as e:
            log.error(f"[MemoryManager._save_store] ✗ Failed: {type(e).__name__}: {e}")

    def _embed(self, text: str) -> list:
        """Embed text — must only be called on the worker thread."""
        return [float(x) for x in next(iter(self._model.embed([text])))]

    def _cosine_similarity(self, a: list, b: list) -> float:
        va, vb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        if denom == 0:
            return 0.0
        return float(np.dot(va, vb) / denom)

    # ── _do_* methods: all run on worker thread ───────────────────────────────

    def _do_memorize(self, text: str) -> bool:
        try:
            memory_id = str(uuid.uuid4())
            embedding = self._embed(text)
            self._memories.append({
                "id": memory_id,
                "text": text,
                "embedding": embedding,
                "created_at": datetime.now().isoformat(),
                "edited": False
            })
            self._save_store()
            log.info(f"[MemoryManager.memorize] ✓ Stored | id={memory_id[:8]}... | "
                     f"text='{text[:60]}{'...' if len(text) > 60 else ''}'")
            return True
        except Exception as e:
            log.error(f"[MemoryManager.memorize] ✗ Failed: {type(e).__name__}: {e}")
            return False

    def _do_recall(self, query: str, threshold: float, max_results: int) -> list:
        try:
            if not self._memories:
                return []
            q_emb = self._embed(query)
            results = []
            for m in self._memories:
                sim = self._cosine_similarity(q_emb, m["embedding"])
                if sim >= threshold:
                    results.append({
                        "id": m["id"],
                        "text": m["text"],
                        "similarity": round(sim, 4),
                        "created_at": m["created_at"]
                    })
            results.sort(key=lambda x: x["similarity"], reverse=True)
            results = results[:max_results]
            log.info(f"[MemoryManager.recall] searched={len(self._memories)} | "
                     f"passed={len(results)} | threshold={threshold}")
            return results
        except Exception as e:
            log.error(f"[MemoryManager.recall] ✗ Failed: {type(e).__name__}: {e}")
            return []

    def _do_get_all(self) -> list:
        try:
            memories = [
                {
                    "id": m["id"],
                    "text": m["text"],
                    "created_at": m["created_at"],
                    "edited": m.get("edited", False)
                }
                for m in self._memories
            ]
            memories.sort(key=lambda x: x["created_at"], reverse=True)
            return memories
        except Exception as e:
            log.error(f"[MemoryManager.get_all] ✗ Failed: {type(e).__name__}: {e}")
            return []

    def _do_update(self, memory_id: str, new_text: str) -> bool:
        try:
            for m in self._memories:
                if m["id"] == memory_id:
                    m["text"] = new_text
                    m["embedding"] = self._embed(new_text)
                    m["created_at"] = datetime.now().isoformat()
                    m["edited"] = True
                    self._save_store()
                    log.info(f"[MemoryManager.update] ✓ Updated | id={memory_id[:8]}...")
                    return True
            log.warning(f"[MemoryManager.update] id={memory_id[:8]}... not found")
            return False
        except Exception as e:
            log.error(f"[MemoryManager.update] ✗ Failed: {type(e).__name__}: {e}")
            return False

    def _do_delete(self, memory_id: str) -> bool:
        try:
            before = len(self._memories)
            self._memories = [m for m in self._memories if m["id"] != memory_id]
            if len(self._memories) < before:
                self._save_store()
                log.info(f"[MemoryManager.delete] ✓ Deleted | id={memory_id[:8]}...")
                return True
            return False
        except Exception as e:
            log.error(f"[MemoryManager.delete] ✗ Failed: {type(e).__name__}: {e}")
            return False

    def _do_clear(self) -> bool:
        try:
            self._memories = []
            self._save_store()
            log.info("[MemoryManager.clear] ✓ All memories cleared")
            return True
        except Exception as e:
            log.error(f"[MemoryManager.clear] ✗ Failed: {type(e).__name__}: {e}")
            return False

    def _do_count(self) -> int:
        return len(self._memories)

    # ── Public API (safe to call from any thread) ─────────────────────────────

    def memorize(self, text: str) -> bool:
        if not self._ready:
            log.warning("[MemoryManager.memorize] Not ready — skipping")
            return False
        text = text.strip()
        if not text:
            return False
        return self._worker.submit(self._do_memorize, text).result()

    def recall(self, query: str, threshold: float = 0.4, max_results: int = 5) -> list:
        if not self._ready:
            return []
        return self._worker.submit(self._do_recall, query, threshold, max_results).result()

    def get_all(self) -> list:
        if not self._ready:
            return []
        return self._worker.submit(self._do_get_all).result()

    def update(self, memory_id: str, new_text: str) -> bool:
        if not self._ready:
            return False
        new_text = new_text.strip()
        if not new_text:
            return False
        return self._worker.submit(self._do_update, memory_id, new_text).result()

    def delete(self, memory_id: str) -> bool:
        if not self._ready:
            return False
        return self._worker.submit(self._do_delete, memory_id).result()

    def clear(self) -> bool:
        if not self._ready:
            return False
        return self._worker.submit(self._do_clear).result()

    def count(self) -> int:
        if not self._ready:
            return 0
        return self._worker.submit(self._do_count).result()

    @property
    def is_ready(self) -> bool:
        return self._ready


# ─────────────────────────── Global Singleton ────────────────────────────────

_instance: MemoryManager | None = None
_instance_lock = threading.Lock()


def get_memory_manager() -> MemoryManager:
    """Return the global MemoryManager singleton. Thread-safe."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = MemoryManager()
    return _instance