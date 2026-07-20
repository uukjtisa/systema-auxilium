"""
Tests for the LaTeX cache + async render pipeline and the typing-reveal
tokenizer (systema/ui/chat/rendering.py + bubbles.py).

The reveal tokenizer must keep injected HTML (math <img>, centered display
<div>, placeholder <span>) as ATOMIC units so the typewriter never slices a tag
or crawls through base64 — the root of the long-response typing lag. The cache
must round-trip through disk and fall back gracefully on a render failure.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.ui.chat.rendering import RenderingMixin  # noqa: E402
from systema.ui.chat.bubbles import BubblesMixin      # noqa: E402


class _R(RenderingMixin):
    pass


def _mixin(tmp_path):
    obj = _R()
    obj._latex_cache_dir = lambda: tmp_path      # redirect cache off APP_ROOT
    return obj


# ── reveal tokenizer ──────────────────────────────────────────────────────────
def test_reveal_units_keeps_img_atomic():
    src = "before <img src='data:image/png;base64,AAAA' title='x'> after"
    units, offsets = BubblesMixin._reveal_units(src)
    assert "<img src='data:image/png;base64,AAAA' title='x'>" in units
    # offsets are monotonic and end at the full length.
    assert offsets[-1] == len(src)
    assert offsets == sorted(offsets)


def test_reveal_units_plain_text_is_per_char():
    units, offsets = BubblesMixin._reveal_units("abc")
    assert units == ["a", "b", "c"]
    assert offsets == [1, 2, 3]


def test_reveal_units_display_div_atomic():
    src = 'x <div style="text-align:center;margin:6px 0;"><img src="d"></div> y'
    units, _ = BubblesMixin._reveal_units(src)
    assert any(u.startswith('<div style="text-align:center') for u in units)


# ── cache ──────────────────────────────────────────────────────────────────────
def test_latex_key_is_stable_and_mode_sensitive(tmp_path):
    m = _mixin(tmp_path)
    m._latex_init()
    k1 = m._latex_key("E=mc^2", True)
    k2 = m._latex_key("E=mc^2", True)
    k3 = m._latex_key("E=mc^2", False)
    assert k1 == k2 and k1 != k3


def test_cache_hit_reads_disk_without_rendering(tmp_path, monkeypatch):
    from systema.ui.chat import rendering as rmod
    m = _mixin(tmp_path)
    m._latex_init()
    key = m._latex_key("E=mc^2", True)
    (tmp_path / f"{key}.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

    def _boom(*a, **k):
        raise AssertionError("must not render on a cache hit")
    monkeypatch.setattr(rmod, "_render_latex_png", _boom)

    html = m._latex_to_base64_img("E=mc^2", display=True)
    assert "<img" in html and "base64" in html


def test_render_failure_falls_back_to_code(tmp_path, monkeypatch):
    from systema.ui.chat import rendering as rmod
    m = _mixin(tmp_path)
    m._latex_init()
    monkeypatch.setattr(rmod, "_render_latex_png",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no mpl")))
    html = m._latex_to_base64_img("\\frac{1}{2}", display=False)
    assert "<code>" in html   # graceful fallback, never crashes


def test_preprocess_emits_placeholder_and_queues_miss(tmp_path, monkeypatch):
    from systema.ui.chat import rendering as rmod
    m = _mixin(tmp_path)
    m._latex_init()
    # Never actually render — we only assert the miss is queued + placeholdered.
    monkeypatch.setattr(rmod, "_render_latex_png", lambda *a, **k: b"PNG")
    out = m._preprocess_latex("Here: $$E=mc^2$$ done")
    assert "rendering math" in out            # async placeholder inserted
    assert len(m._take_latex_misses()) == 1   # one expression queued
    assert m._take_latex_misses() == []       # drained
