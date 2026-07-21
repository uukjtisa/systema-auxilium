"""
systema/common/data_paths.py

Central data-directory conventions (2026-07-21 layout consolidation):

    data/cache/<name>/        — ALL reusable caches live here (latex renders,
                                the file-history undo journal, future caches).
                                New cache = one cache_dir("name") call; never
                                invent another data/<something>_cache folder.
    data/logs/                — session logs (+ crash_dumps/ bundles inside).

`migrate_legacy()` transparently moves a pre-consolidation folder into its new
home on first use, so existing installs and OLD SESSIONS keep every byte
(journal ids, cached PNGs, dump bundles all resolve exactly as before).
stdlib-only, import-cheap.
"""

import shutil
from pathlib import Path

from systema import APP_ROOT

DATA_DIR = APP_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"


def migrate_legacy(old: Path, new: Path) -> None:
    """One-time move of a legacy folder to its new location. Never raises.

    - old missing            -> nothing to do
    - new missing            -> fast whole-folder rename
    - both exist (downgrade/ -> per-file merge: missing files move over,
      re-upgrade edge case)     *.jsonl manifests are APPENDED (no lost
                                journal lines), existing files win; the old
                                tree is removed once emptied.
    """
    try:
        old, new = Path(old), Path(new)
        if not old.is_dir() or old == new:
            return
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            return
        for src in sorted(old.rglob("*")):
            if src.is_dir():
                continue
            dst = new / src.relative_to(old)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.move(str(src), str(dst))
            elif src.suffix == ".jsonl":
                with open(src, encoding="utf-8") as f_in, \
                        open(dst, "a", encoding="utf-8", newline="") as f_out:
                    f_out.write(f_in.read())
                src.unlink()
        # prune what's left (empty dirs / superseded duplicates)
        shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass    # migration is best-effort — the caller's dir still gets made


def cache_dir(name: str, legacy=None) -> Path:
    """data/cache/<name>, created (parents included). `legacy` = the folder's
    pre-consolidation location (str relative to data/, or a Path) — migrated
    in on first call."""
    d = CACHE_DIR / name
    if legacy is not None:
        legacy_path = DATA_DIR / legacy if isinstance(legacy, str) else Path(legacy)
        migrate_legacy(legacy_path, d)
    d.mkdir(parents=True, exist_ok=True)
    return d
