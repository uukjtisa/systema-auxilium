"""
tests/systema/common/test_image_pipeline.py

The image pipeline's load-bearing rules, at the layer that has no Qt:
identity (numbers are never recycled), the cache (originals are never
touched), context state (detach is reversible, delete is not), the provider
contract shim, and token accounting.

The bug these exist to prevent: an image used to be a per-CALL argument, so a
work-mode continuation — which calls the provider with no image argument —
silently lost every picture, and the assistant would insist the user had never
sent one. `test_images_survive_a_work_mode_continuation` is that regression.
"""
import struct
import zlib

import pytest

from systema.common import image_cache, image_refs
from systema.common.token_est import (estimate_history_tokens,
                                      estimate_image_tokens,
                                      estimate_refs_tokens)


def _png(path, w=8, h=4):
    """A real, minimal PNG — the header parser and QPixmap both accept it."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""))
    return path


# ── cache ────────────────────────────────────────────────────────────────────

def test_storing_an_image_never_touches_the_original(tmp_path):
    src = _png(tmp_path / "mine.png")
    before = src.read_bytes()

    ref = image_cache.store(src, n=1)

    assert ref is not None
    assert src.is_file(), "the user's own file must survive being attached"
    assert src.read_bytes() == before
    assert image_cache.exists(ref)
    image_cache.discard(ref, still_referenced=[ref])


def test_dimensions_are_read_without_pillow(tmp_path):
    """Dimensions drive token estimation, so they must not require an optional
    dependency — the header parser is the floor."""
    src = _png(tmp_path / "dims.png", w=8, h=4)
    assert image_cache._dims_via_header(src) == (8, 4)


def test_the_same_picture_twice_shares_one_cache_file(tmp_path):
    src = _png(tmp_path / "dup.png")
    a = image_cache.store(src, n=1)
    b = image_cache.store(src, n=2)

    assert a["path"] == b["path"], "cache is content-addressed"
    # Deleting one must not blank the other.
    assert image_cache.discard(a, still_referenced=[a, b]) is False
    assert image_cache.exists(b)
    image_cache.discard(b, still_referenced=[b])


def test_an_unsupported_format_is_refused_at_the_door(tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not an image", encoding="utf-8")
    assert image_cache.store(bad, n=1) is None


# ── identity ─────────────────────────────────────────────────────────────────

def _hist():
    return [
        {"role": "user", "content": "look",
         "_images": [{"n": 1, "attached": True, "path": "p1"},
                     {"n": 2, "attached": True, "path": "p2"}]},
        {"role": "assistant", "content": "seen"},
        {"role": "ui_event", "_type": "image_attach", "content": "",
         "_images": [{"n": 3, "attached": True, "path": "p3"}]},
    ]


def test_numbers_are_global_across_both_sides():
    """One counter for the user's attachments and the assistant's, so "image 3"
    is unambiguous no matter who added it."""
    assert [r["n"] for r in image_refs.all_refs(_hist())] == [1, 2, 3]


def test_a_deleted_number_is_never_reissued():
    hist = _hist()
    image_refs.remove(hist, 3)
    # 4, not 3 — an earlier "image 3 shows the header" in the transcript must
    # never come to mean a different picture.
    assert image_refs.next_number(hist, stored_next=4) == 4


def test_the_stored_counter_beats_the_history_floor():
    """Deleting the HIGHEST image is the case a max(n)+1 scan gets wrong."""
    hist = [{"role": "user", "content": "",
             "_images": [{"n": 1, "attached": True, "path": "p"}]}]
    assert image_refs.next_number(hist, stored_next=9) == 9


def test_deleting_drops_an_image_only_entry_but_keeps_a_text_one():
    hist = _hist()
    image_refs.remove(hist, 3)
    assert all(m.get("_type") != "image_attach" for m in hist), \
        "an image-only event with no images left is an empty bubble"

    image_refs.remove(hist, 1)
    image_refs.remove(hist, 2)
    assert hist[0] == {"role": "user", "content": "look"}, \
        "a turn that still has text keeps its place"


# ── context state ────────────────────────────────────────────────────────────

def test_detach_is_reversible_and_delete_is_not():
    hist = _hist()

    assert image_refs.set_attached(hist, 2, False) is True
    assert [r["n"] for r in image_refs.attached_refs(hist)] == [1, 3]
    assert image_refs.set_attached(hist, 2, True) is True
    assert [r["n"] for r in image_refs.attached_refs(hist)] == [1, 2, 3]

    image_refs.remove(hist, 2)
    assert image_refs.find(hist, 2) == (None, None)


def test_setting_the_same_state_twice_reports_no_change():
    """Callers skip a re-render and a session save on False."""
    hist = _hist()
    assert image_refs.set_attached(hist, 1, True) is False


def test_bulk_detach_and_attach():
    hist = _hist()
    assert image_refs.set_all_attached(hist, False) == 3
    assert image_refs.attached_refs(hist) == []
    assert image_refs.set_all_attached(hist, True) == 3


def test_the_detached_marker_tells_the_model_not_to_guess():
    marker = image_refs.detached_marker({"n": 3})
    assert "Image 3" in marker
    assert "no longer visible" in marker
    assert "never guess" in marker


# ── token accounting ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("w,h,expected", [
    (512, 512, 85 + 170),            # one tile
    (1024, 1024, 85 + 170 * 4),      # the canonical documented example
    (0, 0, 85 + 170),                # unknown dimensions: honest floor, not 0
])
def test_image_tokens_follow_the_tile_formula(w, h, expected):
    assert estimate_image_tokens(w, h) == expected


def test_an_oversized_image_is_clamped_before_being_counted():
    assert estimate_image_tokens(3840, 2160) == estimate_image_tokens(1920, 1080)


def test_only_attached_images_cost_tokens():
    refs = [{"attached": True, "w": 512, "h": 512},
            {"attached": False, "w": 512, "h": 512}]
    assert estimate_refs_tokens(refs) == estimate_image_tokens(512, 512)


def test_history_tokens_include_attached_images():
    hist = [{"role": "user", "content": "hi",
             "_images": [{"attached": True, "w": 512, "h": 512}]}]
    assert estimate_history_tokens(hist) > estimate_image_tokens(512, 512)


# ── provider contract ────────────────────────────────────────────────────────

def test_a_script_without_the_flag_sees_exactly_the_old_shape():
    """Third-party provider scripts must not break. They keep receiving the
    flat images= list, with the pictures replaced by [Image N] text markers."""
    from systema.engine import provider_contract as pc

    seen = {}

    class Legacy:
        CONTRACT_VERSION = 2

        @staticmethod
        def chat(sp, msgs, *, images=None, tools=None, stream=False):
            seen["msgs"], seen["images"] = msgs, images
            return {"content": "ok", "thinking": None, "tool_calls": [],
                    "finish_reason": "stop"}

    convo = [{"role": "user", "content": "first",
              "images": [{"path": "/c/a.png", "n": 1}]},
             {"role": "user", "content": "second"}]
    pc.invoke(Legacy, "sys", convo, images=["/tmp/oneshot.png"])

    assert not any("images" in m for m in seen["msgs"])
    assert seen["msgs"][0]["content"].startswith("[Image 1]")
    assert seen["images"] == ["/c/a.png", "/tmp/oneshot.png"]
    assert "images" in convo[0], "the caller's own list must not be mutated"


def test_a_script_with_the_flag_gets_positions():
    from systema.engine import provider_contract as pc

    seen = {}

    class Inline:
        CONTRACT_VERSION = 2
        SUPPORTS_INLINE_IMAGES = True

        @staticmethod
        def chat(sp, msgs, *, images=None, tools=None, stream=False):
            seen["msgs"], seen["images"] = msgs, images
            return {"content": "ok", "thinking": None, "tool_calls": [],
                    "finish_reason": "stop"}

    convo = [{"role": "user", "content": "first",
              "images": [{"path": "/c/a.png", "n": 1}]}]
    pc.invoke(Inline, "sys", convo, images=["/tmp/oneshot.png"])

    assert seen["msgs"][0]["images"] == [{"path": "/c/a.png", "n": 1}]
    assert seen["images"] == ["/tmp/oneshot.png"], \
        "the one-shot queue stays separate from anchored history images"


def test_a_provider_that_declares_no_vision_is_believed():
    from systema.engine import provider_contract as pc

    class Blind:
        CONTRACT_VERSION = 2
        SUPPORTS_VISION = False

        @staticmethod
        def chat(sp, msgs, **kw):
            return "ok"

    caps = pc.image_capabilities(Blind)
    assert caps.vision is False
    assert caps.accepts("png") is False


def test_format_support_is_enforced_from_the_declaration():
    from systema.engine import provider_contract as pc

    class Picky:
        CONTRACT_VERSION = 2
        SUPPORTS_VISION = True
        IMAGE_FORMATS = ("png",)

        @staticmethod
        def chat(sp, msgs, **kw):
            return "ok"

    caps = pc.image_capabilities(Picky)
    assert caps.accepts("png") is True
    assert caps.accepts(".png") is True
    assert caps.accepts("webp") is False


# ── the regression ───────────────────────────────────────────────────────────

def test_images_survive_a_work_mode_continuation(tmp_path, monkeypatch):
    """THE bug this whole redesign exists for.

    continue_work() calls the provider with no image argument. When images were
    a per-call kwarg, that meant the model saw a picture on the first turn and
    then, one tool step later, could not — so it told the user no image had
    ever been sent. Living on the history entry, an image is rebuilt into every
    call for free.
    """
    import types

    from systema.engine.ai_engine import AIEngine

    src = _png(tmp_path / "shot.png")
    ref = image_cache.store(src, n=1)

    module = types.SimpleNamespace(
        CONTRACT_VERSION=2, chat=lambda *a, **k: None,
        SUPPORTS_VISION=True, SUPPORTS_INLINE_IMAGES=True)

    class Stub:
        conversation_history = [
            {"role": "user", "content": "analyse this", "_images": [ref]},
            {"role": "assistant", "content": "a red square"},
            # The work-mode ping: the turn that used to lose the picture.
            {"role": "system", "content": "Previous output: ...",
             "_is_work_prompt": True},
        ]
        _load_provider_module = lambda self: module
        _image_caps = AIEngine._image_caps
        _render_entry_images = AIEngine._render_entry_images
        _with_images = AIEngine._with_images
        _get_history_with_memory = AIEngine._get_history_with_memory

    built = Stub()._get_history_with_memory()
    carried = [m for m in built if m.get("images")]
    assert carried, "the image must still reach the provider during work mode"
    assert carried[0]["images"][0]["n"] == 1

    # Detached: no picture, but the model is TOLD, rather than left to wonder.
    ref["attached"] = False
    built = Stub()._get_history_with_memory()
    assert not any(m.get("images") for m in built)
    assert "no longer visible" in built[0]["content"]

    # A provider with no vision degrades the same way, with its own reason.
    ref["attached"] = True
    module.SUPPORTS_VISION = False
    built = Stub()._get_history_with_memory()
    assert not any(m.get("images") for m in built)
    assert "cannot see images" in built[0]["content"]

    image_cache.discard(ref, still_referenced=[ref])


def test_the_history_entry_is_never_mutated_while_building_a_payload(tmp_path):
    """_with_images returns a COPY. The old code forwarded history entries by
    reference, so anything that touched `content` downstream was editing the
    stored conversation."""
    import types

    from systema.engine.ai_engine import AIEngine

    src = _png(tmp_path / "copy.png")
    ref = image_cache.store(src, n=1)
    ref["attached"] = False          # forces a marker to be appended

    class Stub:
        _load_provider_module = lambda self: types.SimpleNamespace(
            CONTRACT_VERSION=2, chat=lambda *a, **k: None, SUPPORTS_VISION=True)
        _image_caps = AIEngine._image_caps
        _render_entry_images = AIEngine._render_entry_images
        _with_images = AIEngine._with_images

    entry = {"role": "user", "content": "original", "_images": [ref]}
    out = Stub()._with_images(entry, Stub()._image_caps())

    assert entry["content"] == "original"
    assert out["content"].startswith("original")
    assert "no longer visible" in out["content"]

    image_cache.discard(ref, still_referenced=[ref])
