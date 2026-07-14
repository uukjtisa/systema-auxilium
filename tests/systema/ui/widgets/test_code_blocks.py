"""
Tests for systema/ui/widgets/code_blocks.py

Headless (offscreen) construction checks for the redesigned code block and the
wrap-by-default table. Requires PyQt6 via the `qapp` fixture; the module skips
cleanly when Qt is unavailable.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QTextEdit  # noqa: E402

from systema.ui.widgets.code_blocks import (  # noqa: E402
    CodeBlockWidget, TableBlockWidget,
)

THEME = {"base": "#0D1117", "elevated": "#21262D",
         "border": "#30363D", "accent": "#58A6FF"}


def test_code_block_expanded_by_default(qapp):
    cb = CodeBlockWidget("python", "def f():\n    return 1\n", THEME)
    assert cb.is_expanded is True
    assert cb.scroll_area.isVisibleTo(cb.main_container)


def test_code_block_has_wrap_copy_hide_chips(qapp):
    cb = CodeBlockWidget("python", "x = 1\n", THEME)
    assert cb.wrap_btn is not None
    assert cb.copy_btn is not None
    assert cb.toggle_btn.text() == "Hide"


def test_code_block_keeps_syntax_highlighter(qapp):
    cb = CodeBlockWidget("python", "import os\n", THEME)
    assert cb.highlighter is not None


def test_code_block_toggle_collapses(qapp):
    cb = CodeBlockWidget("python", "print(1)\n", THEME)
    cb.toggle_expand()
    assert cb.is_expanded is False
    assert cb.toggle_btn.text() == "Show"


def test_code_block_wrap_toggle(qapp):
    cb = CodeBlockWidget("python", "print(1)\n", THEME)
    cb.wrap_btn.setChecked(True)
    cb._toggle_wrap()
    assert cb.code_editor.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth


def test_table_wraps_by_default_and_has_no_wrap_button(qapp):
    md = "| a | b |\n|---|---|\n| 1 | 2 |"
    tb = TableBlockWidget(md, THEME, lambda m: "<table><tr><td>1</td></tr></table>")
    assert tb.view.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
    assert not hasattr(tb, "wrap_btn")
