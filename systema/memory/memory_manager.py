"""
core/memory_manager.py
Memory Manager - Persistent RAG-based memory system
Embeddings via fastembed (ONNX Runtime — no torch, no DLL conflicts). The
model is CONFIGURABLE (settings key `memory_embed_model`, default
all-MiniLM-L6-v2); any name from fastembed's supported list works, and
switching re-embeds every stored memory on the worker thread.
Recall is hybrid: 0.75 * cosine + 0.25 * keyword overlap (title/tags double)
+ a tiny recency tiebreak.
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
import re
import uuid
import json
import shutil
import threading
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from systema.common.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("MemoryManager") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

from systema import APP_ROOT as _APP_ROOT

#: Fallback embedding model — fastembed's smallest solid English retriever.
DEFAULT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


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

    #: Tokens too generic to signal relevance in keyword scoring.
    _STOPWORDS = frozenset(
        "a an and are as at be by do does for from has have how i in is it me "
        "my of on or our so that the this to was we what when which who will "
        "with you your".split()
    )

    def __init__(self, model_name: str | None = None):
        self._model = None
        self._model_name = (model_name or DEFAULT_EMBED_MODEL).strip()
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
        """Load model + data — called on worker thread.

        Resilience rules (a broken model must NEVER take memory down):
        - A configured non-default model that is not fully cached does not
          block startup: we start on the default model and fetch the target in
          the background, switching (+ re-embedding) when the download lands.
        - A cache that exists but fails to load (partial download, e.g. the app
          was closed mid-fetch) is PURGED so the next attempt can re-download,
          and we fall back to the default model for this run."""
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            missing = str(e).split("'")[-2] if "'" in str(e) else str(e)
            _pip = {"fastembed": "fastembed", "numpy": "numpy"}.get(missing, missing)
            self._unavailable_reason = f"Missing package — run: pip install {_pip}"
            log.error(f"[MemoryManager._init] ✗ Missing dependency: {e}")
            return

        target = self._model_name
        deferred_switch = False
        if target != DEFAULT_EMBED_MODEL and not self._cache_looks_complete(target):
            log.info(f"[MemoryManager._init] '{target}' not (fully) cached — starting on "
                     f"'{DEFAULT_EMBED_MODEL}'; downloading '{target}' in the background")
            self._purge_model_cache(target)   # clear partial leftovers first
            self._model_name = DEFAULT_EMBED_MODEL
            deferred_switch = True

        try:
            self._model = self._load_model(TextEmbedding, self._model_name)
        except Exception as e:
            log.error(f"[MemoryManager._init] ✗ '{self._model_name}' failed to load: "
                      f"{type(e).__name__}: {e}")
            self._purge_model_cache(self._model_name)
            if self._model_name != DEFAULT_EMBED_MODEL:
                deferred_switch = True   # retry the target via background fetch
                self._model_name = DEFAULT_EMBED_MODEL
                try:
                    self._model = self._load_model(TextEmbedding, DEFAULT_EMBED_MODEL)
                except Exception as e2:
                    self._unavailable_reason = f"{type(e2).__name__}: {e2}"
                    log.error(f"[MemoryManager._init] ✗ Default model failed too: {e2}")
                    return
            else:
                self._unavailable_reason = f"{type(e).__name__}: {e}"
                return

        try:
            self._load_store()
            self._ready = True
            log.info(f"[MemoryManager._init] ✓ Ready | {len(self._memories)} existing memories "
                     f"| model='{self._model_name}'")
        except Exception as e:
            self._unavailable_reason = f"{type(e).__name__}: {e}"
            log.error(f"[MemoryManager._init] ✗ Failed: {type(e).__name__}: {e}")
            return

        if deferred_switch:
            threading.Thread(target=self._background_fetch_and_switch,
                             args=(target,), daemon=True).start()

    @staticmethod
    def _load_model(TextEmbedding, name: str):
        log.info(f"[MemoryManager] Loading fastembed model: {name}")
        model = TextEmbedding(model_name=name)
        # Warmup: force ONNX InferenceSession to fully initialize on THIS
        # thread so it never lazy-inits on a QThread → 0xC0000005 on Windows.
        list(model.embed(["warmup"]))
        log.info(f"[MemoryManager] ✓ Model loaded and warmed up: {name}")
        return model

    @staticmethod
    def _cache_root() -> Path:
        import tempfile
        return Path(tempfile.gettempdir()) / "fastembed_cache"

    @classmethod
    def _cache_looks_complete(cls, model_name: str) -> bool:
        """True when the model's fastembed cache dir exists and holds at least
        one ONNX file. A partial download (dir present, .onnx missing) returns
        False. Heuristic only — an actual load failure still self-heals via
        _purge_model_cache + default fallback."""
        try:
            short = model_name.split("/")[-1].lower()
            root = cls._cache_root()
            if not short or not root.is_dir():
                return False
            for d in root.iterdir():
                if d.is_dir() and short in d.name.lower() and any(d.rglob("*.onnx")):
                    return True
            return False
        except Exception:
            return False

    @classmethod
    def _purge_model_cache(cls, model_name: str):
        """Best-effort removal of a (possibly partially downloaded) fastembed
        cache dir for `model_name`, so the next attempt re-downloads instead of
        tripping over missing files forever."""
        try:
            root = cls._cache_root()
            short = model_name.split("/")[-1].lower()
            if not short or not root.is_dir():
                return
            for d in root.iterdir():
                if d.is_dir() and short in d.name.lower():
                    shutil.rmtree(d, ignore_errors=True)
                    log.info(f"[MemoryManager] Purged model cache: '{d.name}'")
        except Exception as e:
            log.warning(f"[MemoryManager] Cache purge failed (non-fatal): {e}")

    def _background_fetch_and_switch(self, target: str):
        """Download `target` OFF the worker thread (a throwaway instance just
        populates the cache — its ONNX session is discarded, never used), then
        run the real switch on the worker. Chat recalls keep answering on the
        current model instead of queueing behind a 100+ MB download."""
        try:
            from fastembed import TextEmbedding
            log.info(f"[MemoryManager] Background download of '{target}' started")
            _throwaway = TextEmbedding(model_name=target)
            del _throwaway
            log.info(f"[MemoryManager] ✓ '{target}' downloaded — switching on the worker")
            self._worker.submit(self._do_set_model, target)
        except Exception as e:
            self._purge_model_cache(target)
            log.error(f"[MemoryManager] ✗ Background download of '{target}' failed "
                      f"(staying on '{self._model_name}'): {type(e).__name__}: {e}")

    def _load_store(self):
        """Load memories from JSON file. Accepts the legacy bare-list format and
        the current {"model_name", "memories"} format; embeddings from a
        different (or unknown legacy) model are regenerated on the spot."""
        if not self.store_path.exists():
            self._memories = []
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                stored_model = None  # legacy format — pre-dates model tracking
                self._memories = data
            else:
                stored_model = data.get("model_name")
                self._memories = data.get("memories", [])
            log.info(f"[MemoryManager._load_store] Loaded {len(self._memories)} memories")
        except Exception as e:
            log.warning(f"[MemoryManager._load_store] Failed to load ({e}) — backing up and starting fresh")
            backup = self.store_path.with_suffix(".json.bak")
            if self.store_path.exists():
                shutil.copy(self.store_path, backup)
            self._memories = []
            return

        if self._memories and stored_model != self._model_name:
            # Migration failure must never wipe the store — keep stale
            # embeddings (degraded recall) rather than losing memories.
            try:
                log.info(f"[MemoryManager._load_store] Embeddings from "
                         f"'{stored_model or 'legacy store'}' != '{self._model_name}' "
                         f"— re-embedding {len(self._memories)} memories")
                for m in self._memories:
                    m["embedding"] = self._embed(m["text"])
                self._save_store()
                log.info("[MemoryManager._load_store] ✓ Re-embed migration complete")
            except Exception as e:
                log.error(f"[MemoryManager._load_store] ✗ Re-embed migration failed "
                          f"(keeping old embeddings): {type(e).__name__}: {e}")

    def _save_store(self):
        """Save memories to JSON file (with the embedding model recorded)."""
        try:
            tmp = self.store_path.with_suffix(".json.tmp")
            data = {"model_name": self._model_name, "memories": self._memories}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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

    @classmethod
    def _keyword_overlap(cls, query: str, memory_text: str) -> float:
        """Share of (non-stopword) query tokens found in the memory text, with
        title and tags tokens counted double. Normalized to 0..1 — a cheap
        lexical complement to cosine similarity."""
        q_tokens = set(re.findall(r"[a-z0-9]+", query.lower())) - cls._STOPWORDS
        if not q_tokens:
            return 0.0
        lines = memory_text.split('\n')
        title = lines[0].lower() if lines else ""
        tags = ""
        for line in lines[1:]:
            if line.strip().lower().startswith("tags:"):
                tags = line.strip()[5:].lower()
        strong = set(re.findall(r"[a-z0-9]+", f"{title} {tags}"))
        weak = set(re.findall(r"[a-z0-9]+", memory_text.lower()))
        score = 0.0
        for tok in q_tokens:
            if tok in strong:
                score += 2.0
            elif tok in weak:
                score += 1.0
        return min(1.0, score / (2.0 * len(q_tokens)))

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
            now_ts = datetime.now().timestamp()
            results = []
            for m in self._memories:
                cosine = self._cosine_similarity(q_emb, m["embedding"])
                keywords = self._keyword_overlap(query, m["text"])
                try:
                    age_days = (now_ts - datetime.fromisoformat(m["created_at"]).timestamp()) / 86400.0
                except Exception:
                    age_days = 365.0
                recency = 0.01 * max(0.0, 1.0 - age_days / 365.0)
                score = 0.75 * cosine + 0.25 * keywords + recency
                if score >= threshold:
                    results.append({
                        "id": m["id"],
                        "text": m["text"],
                        "similarity": round(score, 4),
                        "created_at": m["created_at"]
                    })
            results.sort(key=lambda x: x["similarity"], reverse=True)
            results = results[:max_results]
            log.info(f"[MemoryManager.recall] searched={len(self._memories)} | "
                     f"passed={len(results)} | threshold={threshold} | "
                     f"score=0.75*cosine+0.25*keywords+recency")
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

    def _do_set_model(self, model_name: str) -> bool:
        """Switch embedding model — load, warm up, re-embed everything, save.
        Runs on the worker thread; failure keeps the current model intact."""
        try:
            log.info(f"[MemoryManager.set_model] Switching model: "
                     f"'{self._model_name}' -> '{model_name}'")
            from fastembed import TextEmbedding
            new_model = TextEmbedding(model_name=model_name)
            # Warmup on THIS thread (same 0xC0000005 rule as _init).
            list(new_model.embed(["warmup"]))
            self._model = new_model
            self._model_name = model_name
            for m in self._memories:
                m["embedding"] = self._embed(m["text"])
            self._save_store()
            log.info(f"[MemoryManager.set_model] ✓ Now using '{model_name}' | "
                     f"re-embedded {len(self._memories)} memories")
            return True
        except Exception as e:
            log.error(f"[MemoryManager.set_model] ✗ Failed to switch to '{model_name}' "
                      f"(keeping '{self._model_name}'): {type(e).__name__}: {e}")
            return False

    # ── Public API (safe to call from any thread) ─────────────────────────────

    def memorize(self, text: str) -> bool:
        if not self._ready:
            log.warning("[MemoryManager.memorize] Not ready — skipping")
            return False
        text = text.strip()
        if not text:
            return False
        return self._worker.submit(self._do_memorize, text).result()

    def recall(self, query: str, threshold: float = 0.4, max_results: int = 3) -> list:
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
    def model_name(self) -> str:
        return self._model_name

    @property
    def is_ready(self) -> bool:
        return self._ready


# ─────────────────────────── Global Singleton ────────────────────────────────

_instance: MemoryManager | None = None
_instance_lock = threading.Lock()


def get_memory_manager(model_name: str | None = None) -> MemoryManager:
    """Return the global MemoryManager singleton. Thread-safe.
    `model_name` only matters on the FIRST call (creation); later callers get
    the existing instance — use set_model() to switch after that."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = MemoryManager(model_name)
    return _instance