"""The ask_user question format.

qa_spec owns both halves of the tool: reading what the agent wrote, and writing
what the user answered. The serializer is deliberately shared between the two
exits -- a completed interview and an Esc-cancelled one must render identically,
because the cancelled one gets prepended to the user's own reply and the agent
must not have to recognise two shapes.
"""
import pytest

from systema.execution import qa_spec

LINE_FORM = """Q: Which environments should the migration run against?
header: Deploy target
multi: true
- Staging | Safe to break; mirrors the prod schema.
- Production | Live data. Requires the backup step first.
- Local docker | Fast iteration, no network calls.

Q: What is the rollback plan?
multi: false
- Snapshot the volume | Restore point before anything runs.
- Down-migration script | Reversible, but must be written first.
"""


# -- parsing: the line form --------------------------------------------------

def test_the_line_form_parses():
    s = qa_spec.parse(LINE_FORM)
    assert len(s) == 2
    assert not s.warnings
    assert s.questions[0].header == "Deploy target"
    assert s.questions[0].multi is True
    assert s.questions[1].multi is False
    assert s.questions[0].option_labels() == ["Staging", "Production", "Local docker"]
    assert s.questions[0].options[0].description.startswith("Safe to break")


def test_options_may_use_asterisks():
    s = qa_spec.parse("Q: Pick\n* A | first\n* B | second\n")
    assert s.questions[0].option_labels() == ["A", "B"]


def test_an_option_without_a_description_is_fine():
    s = qa_spec.parse("Q: Pick\n- A\n- B\n")
    assert s.questions[0].option_labels() == ["A", "B"]
    assert s.questions[0].options[0].description == ""


def test_multi_defaults_to_true():
    """Checkable-by-default is the whole point; a model that omits the flag must
    not silently get radio buttons."""
    assert qa_spec.parse("Q: Pick\n- A\n- B\n").questions[0].multi is True


def test_options_before_any_question_line_are_salvaged():
    """A model that forgets the 'Q:' marker still gets its question asked."""
    s = qa_spec.parse("Which environment?\n- Staging\n- Production\n")
    assert len(s) == 1
    assert s.questions[0].question == "Which environment?"


# -- parsing: JSON -----------------------------------------------------------

def test_json_form_parses():
    s = qa_spec.parse('[{"question":"Pick one","multiSelect":false,'
                      '"options":[{"label":"A","description":"first"},{"label":"B"}]}]')
    assert len(s) == 1
    assert s.questions[0].multi is False
    assert s.questions[0].option_labels() == ["A", "B"]


def test_json_wrapped_in_a_questions_key():
    s = qa_spec.parse('{"questions":[{"question":"Pick","options":['
                      '{"label":"A"},{"label":"B"}]}]}')
    assert len(s) == 1


def test_json_and_line_form_agree():
    a = qa_spec.parse('[{"question":"Pick","options":[{"label":"A"},{"label":"B"}]}]')
    b = qa_spec.parse("Q: Pick\n- A\n- B\n")
    assert a.questions[0].question == b.questions[0].question
    assert a.questions[0].option_labels() == b.questions[0].option_labels()


# -- limits and malformed input ---------------------------------------------

def test_a_question_with_one_option_is_not_a_question():
    s = qa_spec.parse("Q: Pick\n- OnlyOne\n")
    assert len(s) == 0
    assert any("at least 2" in w for w in s.warnings)


def test_more_than_four_questions_are_trimmed():
    body = "".join(f"Q: Q{i}\n- A\n- B\n\n" for i in range(7))
    s = qa_spec.parse(body)
    assert len(s) == qa_spec.MAX_QUESTIONS
    assert any("only the first" in w for w in s.warnings)


def test_an_empty_body_warns_instead_of_raising():
    s = qa_spec.parse("")
    assert len(s) == 0 and s.warnings


def test_total_nonsense_never_raises():
    for junk in ("????", "```", "{", "[]", "null", "   \n\n  "):
        assert len(qa_spec.parse(junk)) == 0


def test_warnings_name_the_problem_so_the_model_can_retry():
    s = qa_spec.parse("Q: Pick\n- OnlyOne\n")
    assert any("option" in w for w in s.warnings)


# -- serializing -------------------------------------------------------------

def test_a_completed_interview_serializes_as_q_and_a():
    s = qa_spec.parse(LINE_FORM)
    out = qa_spec.serialize(s, [{"picked": ["Staging", "Local docker"]},
                                {"picked": ["Snapshot the volume"]}])
    assert "Q: Which environments should the migration run against?" in out
    assert "A: Staging, Local docker" in out
    assert "A: Snapshot the volume" in out


def test_free_text_is_labelled_as_other():
    s = qa_spec.parse(LINE_FORM)
    out = qa_spec.serialize(s, [{"picked": [], "other": "both, but staged"}, {}])
    assert 'A: Other: "both, but staged"' in out


def test_picks_and_other_coexist():
    s = qa_spec.parse(LINE_FORM)
    out = qa_spec.serialize(s, [{"picked": ["Staging"], "other": "and a dry run"}, {}])
    assert 'A: Staging, Other: "and a dry run"' in out


def test_a_half_filled_card_serializes_like_a_finished_one():
    """The Esc path. Same function, same shape -- only the answers differ."""
    s = qa_spec.parse(LINE_FORM)
    out = qa_spec.serialize(s, [{"picked": ["Staging"]}])
    assert "A: Staging" in out
    assert f"A: {qa_spec.UNANSWERED}" in out
    assert out.count("Q: ") == 2, "every question is shown, answered or not"


def test_a_skipped_question_says_so():
    s = qa_spec.parse(LINE_FORM)
    out = qa_spec.serialize(s, [{"skipped": True}, {"picked": ["Snapshot the volume"]}])
    assert qa_spec.SKIPPED in out


def test_serialize_tolerates_missing_answer_entries():
    s = qa_spec.parse(LINE_FORM)
    assert qa_spec.serialize(s, []).count("Q: ") == 2
    assert qa_spec.serialize(s, None).count("Q: ") == 2


# -- has_any_answer ----------------------------------------------------------

@pytest.mark.parametrize("answers,expected", [
    ([{"picked": ["A"]}], True),
    ([{"picked": [], "other": "typed"}], True),
    ([{"picked": [], "other": ""}], False),
    ([{"skipped": True}], False),
    ([{"picked": [""]}], False),
    ([], False),
    (None, False),
])
def test_has_any_answer(answers, expected):
    assert qa_spec.has_any_answer(answers) is expected


def test_a_skipped_question_does_not_count_as_an_answer():
    """'Answer in chat' must not look like a filled-in interview, or a dismissed
    card would be prepended to the input as a wall of skips."""
    assert qa_spec.has_any_answer([{"skipped": True}, {"skipped": True}]) is False
