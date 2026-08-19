"""The ask_user interview card.

Driven against the real QaCard widget offscreen. The behaviours pinned here are
the ones the user specified explicitly, so a refactor that quietly drops any of
them is a regression even though nothing crashes:

  - options are CHECKBOXES (tick several), radios only when multi is false
  - every question gets an 'Other' free-text box, always
  - Esc keeps whatever was already filled in
  - the card locks once answered and can never resolve twice
"""
import pytest

pytest.importorskip("PyQt6")

from systema.execution import qa_spec           # noqa: E402
from systema.ui.chat.qa_cards import QaCard     # noqa: E402

TWO_Q = """Q: Pick many
header: Multi
multi: true
- A | first
- B | second
- C | third

Q: Pick one
multi: false
- X | ex
- Y | why
"""


@pytest.fixture
def qset():
    return qa_spec.parse(TWO_Q)


@pytest.fixture
def card(qapp, qset):
    return QaCard(qset)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# -- shape -------------------------------------------------------------------

def test_one_page_per_question(card, qset):
    assert card._stack.count() == len(qset.questions) == 2
    assert "1 of 2" in card._summary_lbl.text()


def test_multi_select_uses_checkboxes(card):
    from PyQt6.QtWidgets import QCheckBox
    assert all(isinstance(b, QCheckBox) for b in card._rows[0][0])


def test_single_select_uses_radios_and_is_exclusive(card):
    from PyQt6.QtWidgets import QRadioButton
    boxes = card._rows[1][0]
    assert all(isinstance(b, QRadioButton) for b in boxes)
    boxes[0].setChecked(True)
    boxes[1].setChecked(True)
    assert [b.isChecked() for b in boxes] == [False, True]


def test_every_question_gets_an_other_box(card):
    """The escape hatch is not optional and is never one of the agent's slots."""
    for _checks, other, _g in card._rows:
        assert other is not None


def test_the_other_box_is_not_an_option(card, qset):
    assert "Other" not in qset.questions[0].option_labels()


# -- answering ---------------------------------------------------------------

def test_several_options_can_be_ticked_at_once(card, qset):
    checks = card._rows[0][0]
    checks[0].setChecked(True)
    checks[2].setChecked(True)
    card._on_next()
    assert card.answers[0]["picked"] == ["A", "C"]


def test_free_text_is_captured(card):
    card._rows[0][1].setText("something else entirely")
    card._on_next()
    assert card.answers[0]["other"] == "something else entirely"


def test_going_back_does_not_lose_a_pick(card):
    card._rows[0][0][0].setChecked(True)
    card._on_next()
    card._go(0)
    assert card.answers[0]["picked"] == ["A"]
    assert card._rows[0][0][0].isChecked()


def test_finishing_the_last_page_resolves(card):
    seen = []
    card.resolved.connect(lambda: seen.append(1))
    card._on_next()          # page 1 -> 2
    card._on_next()          # done
    assert seen == [1]
    assert card.dismissed is False


# -- Esc keeps the work ------------------------------------------------------

def test_esc_keeps_what_was_already_filled(card, qset):
    card._rows[0][0][0].setChecked(True)
    card.dismiss()
    assert card.dismissed is True
    assert qa_spec.has_any_answer(card.answers)
    out = qa_spec.serialize(qset, card.answers)
    assert "A: A" in out
    assert f"A: {qa_spec.UNANSWERED}" in out


def test_esc_on_an_untouched_card_has_nothing_to_keep(card):
    card.dismiss()
    assert not qa_spec.has_any_answer(card.answers)


def test_answer_in_chat_marks_the_untouched_ones_skipped(card):
    card._rows[0][0][0].setChecked(True)
    card._on_skip()
    assert card.answers[0]["picked"] == ["A"]
    assert card.answers[0]["skipped"] is False
    assert card.answers[1]["skipped"] is True


# -- one-shot and locking ----------------------------------------------------

def test_resolving_twice_is_impossible(card):
    seen = []
    card.resolved.connect(lambda: seen.append(1))
    card.dismiss()
    card.dismiss()
    card._on_next()
    card._on_skip()
    assert seen == [1], "the turn must never be resumed twice"


def test_the_card_locks_once_answered(card):
    card.dismiss()
    for checks, other, _g in card._rows:
        assert all(not b.isEnabled() for b in checks)
        assert other.isReadOnly()
    assert not card._next_btn.isVisible()


def test_a_locked_card_still_shows_what_was_asked(card, qset):
    """It stays in the transcript as the record of the exchange."""
    card.dismiss()
    assert card._stack.count() == len(qset.questions)


def test_it_exposes_apply_zoom_for_ctrl_scroll(card):
    """theming._zoom_rich_children finds cards BY CAPABILITY, not registration."""
    assert callable(card.apply_zoom)
    card.apply_zoom()


# -- containment: it is a card in a bubble, not a takeover --------------------
# The reported bug: with four questions of six described options each the card
# grew to the full height of the chat viewport and pushed the conversation off
# screen, and as a top-level row it spanned the whole window width.

BIG = "".join(
    "Q: Question number %d which is fairly long as these go?\nmulti: true\n" % i
    + "".join("- Option %d for q%d | A description line that adds real height\n"
              % (j, i) for j in range(6)) + "\n"
    for i in range(4))


@pytest.fixture
def big_card(qapp):
    return QaCard(qa_spec.parse(BIG), max_width=560)


def test_the_question_list_is_height_capped(big_card):
    assert big_card._scroll.maximumHeight() == QaCard.MAX_H


def test_the_card_is_width_capped_to_the_bubble(big_card):
    """The chat passes _bubble_max_width(), so it shrinks with the window."""
    assert big_card.maximumWidth() == 560


def test_a_big_interview_does_not_grow_without_bound(big_card):
    """4 questions x 6 described options must still fit a card, not a screen."""
    assert big_card.sizeHint().height() <= QaCard.MAX_H + 120


def test_answering_collapses_it_to_a_summary(big_card):
    """Settled = folded to the header line, the same shape every other card
    takes once its detail is closed."""
    tall = big_card.sizeHint().height()
    big_card._rows[0][0][0].setChecked(True)
    for _ in range(len(big_card.qset.questions)):
        big_card._on_next()
    assert not big_card._detail.isVisibleTo(big_card), "the form must fold away"
    assert "answered" in big_card._summary_lbl.text()
    assert big_card.sizeHint().height() < tall, "a settled question must shrink"


def test_revise_brings_the_form_back(big_card):
    big_card.dismiss()
    assert not big_card._detail.isVisibleTo(big_card)
    big_card._on_revise()
    assert big_card._detail.isVisibleTo(big_card)


def test_the_summary_escapes_user_text(qapp):
    """The Other box is free text and lands in an HTML label."""
    card = QaCard(qa_spec.parse("Q: Pick\n- A\n- B\n"))
    card._rows[0][1].setText("<img src=x onerror=alert(1)>")
    card._on_next()
    assert "<img" not in card._summary_lbl.text()
    assert "&lt;img" in card._summary_lbl.text()


# -- the agent's own "Other" option must not duplicate the box ---------------

def test_a_model_written_other_option_is_dropped(qapp):
    """Screenshot bug: the card painted a dead 'Other' radio right above its own
    free-text Other box."""
    s = qa_spec.parse(
        "Q: What does it refer to?\nmulti: false\n"
        "- Hindu-Arabic numeral system | maths\n"
        "- Binary | digital logic\n"
        "- Other | Specify what it refers to\n")
    assert s.questions[0].option_labels() == ["Hindu-Arabic numeral system", "Binary"]
    assert s.questions[0].allow_other is True


def test_a_real_option_starting_with_other_survives(qapp):
    s = qa_spec.parse("Q: Pick\n- Other services | a real choice\n- Email\n")
    assert "Other services" in s.questions[0].option_labels()


# -- theme, indicators, resize -----------------------------------------------
# All three were reported from a screenshot: the card ignored the user's theme,
# the SELECTED radio had no visible marker at all (its indicator vanished the
# moment the option was picked), and it kept the width of the window it was
# built in.

PURPLE = {"base": "#120F1A", "surface": "#1D1825", "elevated": "#2A2436",
          "border": "#3E3556", "accent": "#A78BFA", "deep": "#0C0910"}


@pytest.fixture
def themed(qapp):
    return QaCard(qa_spec.parse("Q: Pick\nmulti: false\n- A | first\n- B | second\n"),
                  theme=lambda: PURPLE, max_width=500)


def test_it_uses_the_live_theme(themed):
    css = themed.styleSheet()
    assert PURPLE["accent"] in css
    assert PURPLE["border"] in css
    assert PURPLE["base"] in css, "the detail panel uses the theme's base"


def test_it_has_no_hardcoded_palette_left(themed):
    """It sat inside a themed bubble as the one element ignoring the theme."""
    assert "#5A9CF8" not in themed.styleSheet()


def test_a_theme_change_restyles_it(themed):
    green = dict(PURPLE, accent="#4ADE80")
    themed._theme = lambda: green
    themed.apply_zoom()                      # the restyle hook
    assert "#4ADE80" in themed.styleSheet()


@pytest.mark.parametrize("selector", [
    "QRadioButton::indicator",
    "QRadioButton::indicator:checked",
    "QRadioButton::indicator:checked:disabled",
    "QCheckBox::indicator",
    "QCheckBox::indicator:checked",
    "QCheckBox::indicator:checked:disabled",
])
def test_every_indicator_state_is_drawn(themed, selector):
    """Styling these widgets at all makes Qt drop its NATIVE indicator, so any
    state left unstyled renders as nothing. The checked state did exactly that:
    picking an option made its marker disappear."""
    assert selector in themed.styleSheet()


def test_a_locked_card_still_shows_which_option_was_picked(themed):
    """checked:disabled must keep the accent, or answering erases the answer."""
    themed._rows[0][0][0].setChecked(True)
    themed._on_next()
    css = themed.styleSheet()
    block = css[css.index("QRadioButton::indicator:checked:disabled"):]
    assert PURPLE["accent"] in block[:160]


def test_it_follows_the_window_width(themed):
    themed.set_max_width(720)
    assert themed.maximumWidth() == 720
    themed.set_max_width(380)
    assert themed.maximumWidth() == 380


def test_reflow_finds_it_by_capability(themed):
    """ChatWindow._reflow_bubbles looks for set_max_width rather than a
    registration list, so a card added later is covered for free."""
    assert callable(getattr(themed, "set_max_width", None))


def test_the_wrapper_is_borderless_like_every_other_card(themed):
    """It shipped as its own boxed frame and was the one element in the turn
    that looked foreign. Only the DETAIL panel carries a border, exactly as in
    _web_card_shell."""
    css = themed.styleSheet()
    assert "QFrame#qaCard { background: transparent; border: none; }" in css
    assert "QFrame#qaHeader:hover" in css, "header needs the house hover tint"


def test_a_live_question_starts_expanded(themed):
    """The deliberate difference from the other cards: this one is not a summary
    of something that happened, it is a prompt the turn is blocked on."""
    assert themed._detail.isVisibleTo(themed)


def test_the_header_toggles_like_the_other_cards(themed):
    themed._toggle()
    assert not themed._detail.isVisibleTo(themed)
    themed._toggle()
    assert themed._detail.isVisibleTo(themed)
