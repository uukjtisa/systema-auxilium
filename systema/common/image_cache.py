"""
systema/common/image_cache.py

Persistent store for every image that enters a conversation, and the definition
of the `ImageRef` record the rest of the app passes around.

WHY THIS EXISTS
---------------
Images used to be paths held in UI state and handed to the provider as a flat
`images=[...]` kwarg for exactly ONE call. Nothing was persisted, so reloading a
session lost every picture, and a work-mode continuation (which calls the
provider WITHOUT that kwarg) lost them mid-conversation — the model would
acknowledge an image and then, a turn later, say it could not see one.

Images are now content that lives ON the history entry it belongs to. That
needs a stable file the session can point at, because the user's original may
be a temp screenshot, a file they later move, or something on a drive that is
not mounted next boot. So every attachment is COPIED into
`data/cache/images/<sha1>.<ext>` and the history stores that path.

RULES
-----
* The user's original file is NEVER touched — not moved, not deleted. A
  context-management feature has no business deleting somebody's screenshots,
  and an earlier version of this pipeline did exactly that.
* Content-addressed: attaching the same picture twice reuses one cache file.
  Two ImageRefs may therefore share a `path`; `discard()` accounts for that.
* stdlib + Pillow-optional. Dimensions come from Pillow when available and fall
  back to a small header parser, because dimensions feed token estimation and
  must not require a heavy import on the GUI thread.

`ImageRef` is a plain JSON-safe dict so it can sit directly inside
`conversation_history` and round-trip through the session file untouched:

    {'id':   'img_7',          # stable within a session
     'n':    7,                # the number the AI and user refer to it by
     'path': '<cache>/ab….png',
     'name': 'screenshot.png', # original basename, for display
     'origin': 'user'|'agent',
     'attached': True,         # False = detached from context (reversible)
     'w': 1920, 'h': 1080}
"""

import hashlib
import shutil
import struct
from pathlib import Path

from systema.common.data_paths import cache_dir
from systema.common.logger import _make_logger

log = _make_logger("ImageCache")

# Formats we will accept into the cache. Anything else is rejected at the door
# rather than failing later inside a provider's base64 encoder.
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def images_dir() -> Path:
    """data/cache/images — created on first use."""
    return cache_dir("images")


# ── dimensions ───────────────────────────────────────────────────────────────

def _dims_via_pillow(path: Path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def _dims_via_header(path: Path):
    """Minimal header parse for the common formats.

    Pillow is optional in this app, and image DIMENSIONS drive token
    estimation — so a missing Pillow must degrade to a wrong-but-present
    number, never to an exception on the GUI thread.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            if head[:2] == b"BM":
                f.seek(18)
                w, h = struct.unpack("<ii", f.read(8))
                return abs(int(w)), abs(int(h))
            if head[:2] == b"\xff\xd8":          # JPEG — walk the segments
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        break
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    if not marker:
                        break
                    if marker[0] in range(0xC0, 0xCF) and marker[0] not in (0xC4, 0xC8, 0xCC):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return int(w), int(h)
                    size = f.read(2)
                    if len(size) < 2:
                        break
                    f.seek(struct.unpack(">H", size)[0] - 2, 1)
    except Exception:
        pass
    return None


def dimensions(path) -> tuple[int, int]:
    """(width, height), best effort. (0, 0) when it cannot be determined."""
    p = Path(path)
    return _dims_via_pillow(p) or _dims_via_header(p) or (0, 0)


# ── store / discard ──────────────────────────────────────────────────────────

def _sha1_of(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def store(src_path, *, n: int, origin: str = "user") -> dict | None:
    """Copy an image into the cache and return its ImageRef. None on failure.

    The ORIGINAL IS LEFT ALONE. `n` is the session-global image number the
    caller allocated; this function never assigns one, because numbering is a
    property of the conversation, not of the cache.
    """
    try:
        src = Path(src_path)
        if not src.is_file():
            log.warning(f"[store] Not a file: {src_path}")
            return None
        ext = src.suffix.lower()
        if ext not in SUPPORTED_EXTS:
            log.warning(f"[store] Unsupported image type '{ext}': {src_path}")
            return None

        digest = _sha1_of(src)
        dest = images_dir() / f"{digest}{ext}"
        if not dest.exists():
            shutil.copy2(src, dest)

        w, h = dimensions(dest)
        return {
            "id": f"img_{n}",
            "n": int(n),
            "path": str(dest),
            "name": src.name,
            "origin": origin,
            "attached": True,
            "w": w,
            "h": h,
        }
    except OSError as e:
        log.error(f"[store] Could not cache '{src_path}': {e}")
        return None


def discard(ref: dict, *, still_referenced=()) -> bool:
    """Delete an ImageRef's cached file — the FULL-DELETE path only.

    Content addressing means two refs can share one file, so pass the refs that
    are still alive; the file survives while any of them points at it. Without
    this check, deleting one of two copies of the same picture would blank the
    other.
    """
    try:
        path = ref.get("path")
        if not path:
            return False
        for other in still_referenced:
            if other is not ref and other.get("path") == path:
                return False        # another ref still needs the bytes
        Path(path).unlink()
        return True
    except OSError:
        return False


def sweep(live_refs) -> int:
    """Delete cache files no surviving ImageRef points at. Returns the count.

    Cheap orphan collection for the cases the normal delete path cannot cover:
    a session file deleted outside the app, a crash between copy and save.
    """
    try:
        keep = {Path(r.get("path", "")).name for r in live_refs if r.get("path")}
        removed = 0
        for f in images_dir().iterdir():
            if f.is_file() and f.name not in keep:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
    except OSError:
        return 0


def exists(ref: dict) -> bool:
    """True when the ref's cached bytes are still on disk. A session restored
    onto a machine whose cache was cleared must render a placeholder rather
    than crash the reload."""
    try:
        return bool(ref.get("path")) and Path(ref["path"]).is_file()
    except OSError:
        return False
