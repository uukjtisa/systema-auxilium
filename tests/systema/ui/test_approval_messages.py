"""
tests/systema/ui/test_approval_messages.py

The approval window used to offer ONE optional message, on Reject: "here's why
I said no". Approving was all-or-nothing — the only way to say "yes, but write
to data/ not the desktop" was to reject and re-explain the whole step.

So there are two fields now, one per decision, and exactly one of them is ever
sent: the reason on Reject, the note on Accept. A stale value in the other must
never leak into the observation, which is what these pin.

Also pinned: the window shows the step's ANNOTATION at the top. The title only
ever says "Code execution approval" — always true, tells you nothing — while
the annotation is the line the model itself wrote about what the step is for.
"""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from systema.ui.dialogs.approval_window import CodeApprovalDialog     # noqa: E402


@pytest.fixture
def dialog(qapp):
    """A real approval window over a harmless snippet."""
    made = []

    def _make(annotation="", code="print('hi')"):
        d = CodeApprovalDialog(code, 'python_interpreter', ai_engine=None,
                               annotation=annotation)
        made.append(d)
        return d

    yield _make
    for d in made:
        d._stop_worker()
        d.deleteLater()


# ── the two optional messages ────────────────────────────────────────────────

def test_approving_sends_the_note_and_no_reason(dialog):
    d = dialog()
    d.note_edit.setText("  fine, but write to data/ not the desktop  ")
    d.reason_edit.setText("stale text nobody meant to send")

    d.on_accept()

    assert d.result == 'accept'
    assert d.accept_note == "fine, but write to data/ not the desktop"
    assert d.reject_reason == "", "the reject field leaked into an approval"


def test_rejecting_sends_the_reason_and_no_note(dialog):
    d = dialog()
    d.reason_edit.setText("  that path is wrong  ")
    d.note_edit.setText("stale text nobody meant to send")

    d.on_reject()

    assert d.result == 'reject'
    assert d.reject_reason == "that path is wrong"
    assert d.accept_note == "", "the approve field leaked into a rejection"


def test_both_messages_are_optional(dialog):
    d = dialog()
    d.on_accept()
    assert d.result == 'accept' and d.accept_note == ""

    d2 = dialog()
    d2.on_reject()
    assert d2.result == 'reject' and d2.reject_reason == ""


def test_enter_in_a_field_commits_that_field_s_decision(dialog):
    """Return in the reject box rejects; return in the note box approves —
    pressing Enter must never trigger the opposite button."""
    d = dialog()
    d.note_edit.setText("go on")
    d.note_edit.returnPressed.emit()
    assert d.result == 'accept'

    d2 = dialog()
    d2.reason_edit.setText("no")
    d2.reason_edit.returnPressed.emit()
    assert d2.result == 'reject'


def test_the_fields_are_distinct_widgets(dialog):
    d = dialog()
    assert d.note_edit is not d.reason_edit
    assert "reject" in d.reason_edit.placeholderText().lower()
    assert "approv" in d.note_edit.placeholderText().lower()


# ── each message sits with the button it feeds ───────────────────────────────

def _grid_of(d, widget):
    """(layout, row, column) for a widget inside a QGridLayout."""
    from PyQt6.QtWidgets import QGridLayout
    for lay in d.findChildren(QGridLayout):
        idx = lay.indexOf(widget)
        if idx >= 0:
            row, col, _, _ = lay.getItemPosition(idx)
            return lay, row, col
    return None, None, None


def test_each_field_sits_directly_above_its_own_button(dialog):
    """Strung along one row they read as four unrelated controls — it was not
    obvious the left box feeds Reject and the right one feeds Accept."""
    d = dialog()
    lay, r_row, r_col = _grid_of(d, d.reason_edit)
    _, rb_row, rb_col = _grid_of(d, d.reject_btn)
    _, n_row, n_col = _grid_of(d, d.note_edit)
    _, ab_row, ab_col = _grid_of(d, d.accept_btn)

    assert lay is not None, "the decision row is no longer a grid"
    assert (r_col, rb_col) == (0, 0), "reason and Reject are not in one column"
    assert (n_col, ab_col) == (1, 1), "note and Accept are not in one column"
    assert r_row == n_row and rb_row == ab_row, "the two columns are not level"
    assert rb_row > r_row, "the buttons should sit under their fields"


def test_the_two_columns_are_symmetric(dialog):
    """Equal stretch, so the halves match instead of one hugging its label."""
    d = dialog()
    lay, _, _ = _grid_of(d, d.reason_edit)
    assert lay.columnStretch(0) == lay.columnStretch(1) != 0


def test_the_buttons_share_a_height(dialog):
    d = dialog()
    assert d.reject_btn.minimumHeight() == d.accept_btn.minimumHeight() > 0


# ── the annotation band ──────────────────────────────────────────────────────

def _labels(d):
    from PyQt6.QtWidgets import QLabel
    return [w.text() for w in d.findChildren(QLabel)]


def test_the_annotation_is_shown(dialog):
    d = dialog(annotation="Attaching ValleyMed logo for review")
    assert "Attaching ValleyMed logo for review" in _labels(d)


def test_the_annotation_sits_above_the_title(dialog):
    """It has to beat the title to the eye — the title is boilerplate."""
    d = dialog(annotation="Resizing the logo to 512px")
    texts = _labels(d)
    assert texts.index("Resizing the logo to 512px") < texts.index(
        "Code execution approval")


def _band(d):
    """The annotation band, identified by its tooltip — or None."""
    from PyQt6.QtWidgets import QLabel
    for w in d.findChildren(QLabel):
        if w.toolTip() == "What the AI said this step is for":
            return w
    return None


def test_no_band_when_the_step_had_no_annotation(dialog):
    """An empty accent band is worse than none — the widget isn't built."""
    assert _band(dialog()) is None
    assert _band(dialog(annotation="   ")) is None


def test_the_band_exists_when_there_is_something_to_say(dialog):
    band = _band(dialog(annotation="Attaching the logo"))
    assert band is not None and band.text() == "Attaching the logo"


def test_the_annotation_is_whitespace_trimmed(dialog):
    assert dialog(annotation="   padded   ").annotation == "padded"


def test_the_annotation_is_never_parsed_as_markup(dialog):
    """The model writes it, so it can contain anything — <module>, a path, an
    unclosed angle bracket."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel
    raw = "reading <module> from C:\\x & checking"
    d = dialog(annotation=raw)
    band = [w for w in d.findChildren(QLabel) if w.text() == raw]
    assert band, "the annotation was mangled before it reached the label"
    assert band[0].textFormat() == Qt.TextFormat.PlainText
