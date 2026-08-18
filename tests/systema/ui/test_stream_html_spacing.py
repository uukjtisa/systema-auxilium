"""
tests/systema/ui/test_stream_html_spacing.py

The live streaming segment must not reserve blank space the finished render
will not keep.

Deltas are escaped and appended as raw `<br>`s, so a reply that opens with a
newline — plenty of models do; stepfun-ai/step-3.7-flash answers "\\nhello" —
painted two empty lines between the assistant's name and its first word, and
any run of three or more newlines opened a gap markdown then closed. The reply
visibly reflowed the moment streaming ended, which reads as a bug even though
the end state was right.

Trailing newlines are held rather than emitted: without that the label gains a
blank line after every delta that ends on one, and the view jitters as it is
added and pushed down again.
"""
import pytest

from systema.ui.chat.bubbles import BubblesMixin


def render(*chunks) -> str:
    seg = {"html": ""}
    for chunk in chunks:
        BubblesMixin._append_stream_html(seg, chunk)
    return seg["html"]


@pytest.mark.parametrize("chunks, expected", [
    (("\n\nAw, Thirdy",), "Aw, Thirdy"),
    (("\nhello",), "hello"),
    (("  \n\n  Hi",), "Hi"),
])
def test_leading_blank_space_is_never_painted(chunks, expected):
    assert render(*chunks) == expected


def test_a_normal_paragraph_break_survives():
    assert render("Para one.\n\nPara two.") == "Para one.<br><br>Para two."


def test_a_single_newline_survives():
    assert render("line one\nline two") == "line one<br>line two"


def test_long_blank_runs_collapse_like_markdown():
    assert render("Para one.\n\n\n\n\nPara two.") == "Para one.<br><br>Para two."


def test_a_run_split_across_deltas_still_collapses():
    """Deltas arrive at token granularity, so a blank run is routinely split.
    Normalising each chunk in isolation misses it."""
    assert render("a\n", "\n", "\n\nb") == "a<br><br>b"


def test_trailing_newlines_are_held_not_painted():
    assert render("done.\n\n") == "done."


def test_held_newlines_reappear_when_text_follows():
    assert render("done.\n\n", "more") == "done.<br><br>more"


def test_html_is_escaped():
    assert render("<b>&") == "&lt;b&gt;&amp;"


def test_pure_whitespace_deltas_paint_nothing():
    seg = {"html": ""}
    assert BubblesMixin._append_stream_html(seg, "\n\n") is False
    assert seg["html"] == ""
