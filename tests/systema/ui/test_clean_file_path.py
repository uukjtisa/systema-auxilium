"""
Tests for ChatWindow.clean_file_path — the path normalizer behind the image
drag/paste prompt. It must strip the surrounding quotes Windows "Copy as path"
adds (the bug that silently killed the attach-vs-path prompt), unwrap file://
URIs, and NOT mangle POSIX separators off-Windows.
"""
import sys
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.ui.chat_window import ChatWindow  # noqa: E402

# clean_file_path uses only `self`-free logic + sys.platform, so an unbound call
# with a throwaway self is enough (no heavy ChatWindow construction).
_clean = ChatWindow.clean_file_path
_dummy = object()


def test_strips_surrounding_double_quotes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _clean(_dummy, '"C:\\Users\\me\\pic.png"') == "C:\\Users\\me\\pic.png"


def test_strips_single_quotes(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _clean(_dummy, "'C:\\a\\b.png'") == "C:\\a\\b.png"


def test_windows_forward_slashes_normalized(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _clean(_dummy, "C:/a/b.png") == "C:\\a\\b.png"


def test_posix_path_not_mangled(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _clean(_dummy, "/home/me/pic.png") == "/home/me/pic.png"


def test_file_uri_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert _clean(_dummy, "file:///home/me/a%20b.png") == "/home/me/a b.png"


def test_file_uri_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _clean(_dummy, "file:///C:/Users/me/pic.png") == "C:\\Users\\me\\pic.png"
