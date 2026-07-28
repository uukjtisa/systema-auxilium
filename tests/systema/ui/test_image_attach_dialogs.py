"""
tests/systema/ui/test_image_attach_dialogs.py

The single-file and multi-file image attach prompts are a PAIR. Before
2026-07-28 they were not: the multi-file one was a styled QDialog, the
single-file one a bare QMessageBox in whatever the OS supplied, and the styled
one hardcoded a dark palette that ignored the user's theme entirely.

These tests hold the pair together — both build, both take their colours from
the live theme, and neither dies on an unreadable file (a path can be dropped
that is not really an image).

Driven with a light stub `self` in the style of test_image_bubbles.py; building
a whole ChatWindow is neither necessary nor cheap.
"""
import struct
import types
import zlib

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication, QDialog, QWidget  # noqa: E402

from systema.ui.chat.input_dock import InputDockMixin      # noqa: E402
from systema.ui.theme import THEMES                        # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _png(path, w=8, h=4):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag
                + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""))
    return path


class _Dock(InputDockMixin, QWidget):
    """Smallest stub the two dialog builders touch.

    A real QWidget because both builders parent their QDialog to `self` — in
    the app that parent is the ChatWindow.
    """

    def __init__(self, theme_key="obsidian_blue"):
        QWidget.__init__(self)
        self._theme_key = theme_key
        self.attached = []
        self.inserted = []
        self.input_field = types.SimpleNamespace(
            toPlainText=lambda: "",
            text_input=types.SimpleNamespace(setPlainText=self.inserted.append),
        )

    # the mixin reaches for these
    def _t(self):
        return THEMES[self._theme_key]

    def _show_image_preview(self, path):
        self.attached.append(path)

    def attach_images(self, paths, **kw):
        self.attached.extend(paths)

    def should_quote_path(self, p):
        return " " in str(p)


@pytest.fixture
def dock():
    return _Dock()


@pytest.fixture
def no_exec(monkeypatch):
    """Run the dialogs without blocking on a real event loop."""
    monkeypatch.setattr(QDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected.value)


# ── both build, on any theme ────────────────────────────────────────────────

@pytest.mark.parametrize("theme_key", sorted(THEMES))
def test_stylesheet_is_built_from_the_live_theme(qapp, theme_key):
    """The regression: a hardcoded #0D1117 palette ignored the user's theme."""
    qss = _Dock(theme_key)._image_dialog_qss()
    t = THEMES[theme_key]
    assert t["base"] in qss
    assert t["accent"] in qss
    assert t["border"] in qss
    assert "#0D1117" not in qss or t["base"] == "#0D1117"


def test_single_image_dialog_builds(qapp, dock, no_exec, tmp_path):
    _png(tmp_path / "shot.png")
    dock._handle_image_file_drop(str(tmp_path / "shot.png"))
    # Rejected → neither action taken, and nothing raised.
    assert dock.attached == []
    assert dock.inserted == []


def test_multi_image_dialog_builds(qapp, dock, no_exec, tmp_path):
    paths = [str(_png(tmp_path / f"s{i}.png")) for i in range(3)]
    dock._handle_multiple_image_files_dialog(paths)
    assert dock.attached == []


# ── the thumbnail helper, which is what both dialogs gained ────────────────

def test_thumbnail_reports_source_dimensions(qapp, tmp_path):
    _png(tmp_path / "a.png", w=8, h=4)
    lbl, dims = _Dock()._image_thumb(str(tmp_path / "a.png"), 44)
    assert dims == (8, 4)
    assert lbl is not None
    assert not lbl.pixmap().isNull()


def test_thumbnail_of_an_unreadable_file_is_none_not_a_crash(qapp, tmp_path):
    bogus = tmp_path / "not-an-image.png"
    bogus.write_text("this is not a PNG")
    lbl, dims = _Dock()._image_thumb(str(bogus), 44)
    assert lbl is None and dims == (0, 0)


def test_dialogs_survive_an_unreadable_file(qapp, dock, no_exec, tmp_path):
    """A dropped path may not be a real image; the prompt must still open so
    the user can choose 'insert as text' instead of hitting a traceback."""
    bogus = tmp_path / "broken.png"
    bogus.write_text("nope")
    dock._handle_image_file_drop(str(bogus))
    dock._handle_multiple_image_files_dialog([str(bogus)])


@pytest.mark.parametrize("size,expected", [
    (512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB"),
])
def test_human_size(size, expected):
    assert _Dock._human_size(size) == expected
