"""Developer notes carried inside commit messages.

A commit subject describes the diff. It cannot say "tick the e_mailman skill
alongside this one or it will break" -- that is an instruction to whoever
applies the update, and burying it in a body nobody reads means it was not
communicated. <update_message> blocks are lifted out of the commit range an
update would bring and shown as dismissible notices.
"""
import pytest

from systema.updater import messages as m


@pytest.fixture(autouse=True)
def clean_store(tmp_path, monkeypatch):
    """Never touch the real dismissal store."""
    monkeypatch.setattr(m, "_STORE", tmp_path / "dismissed.json")


COMMITS = [
    {"sha": "abc1234567", "date": "2026-08-19T10:00:00Z", "author": "Niccc2007",
     "message": "fix(engine): Rework the provider contract\n\n"
                "<update_message>\nTick the e_mailman skill too.\n</update_message>\n\n"
                "Ordinary body text."},
    {"sha": "def7654321", "date": "2026-08-18T10:00:00Z", "author": "Niccc2007",
     "message": "perf(ui): Lazy task editor\n\nNo note here."},
]


# -- extraction --------------------------------------------------------------

def test_a_note_is_lifted_out_of_the_body():
    assert m.extract(COMMITS[0]["message"]) == ["Tick the e_mailman skill too."]


def test_a_commit_without_a_note_yields_nothing():
    assert m.extract(COMMITS[1]["message"]) == []


@pytest.mark.parametrize("raw", [
    "<update_message>hi</update_message>",
    "<UPDATE_MESSAGE>hi</UPDATE_MESSAGE>",
    "< update_message >hi< / update_message >",
    "<update_message>\n  hi  \n</update_message>",
])
def test_the_tag_is_tolerant_of_how_a_human_types_it(raw):
    assert m.extract(raw) == ["hi"]


def test_an_unclosed_tag_still_delivers_the_note():
    """Forgetting the closing tag must not silently swallow the message."""
    assert m.extract("subject\n<update_message>runs to the end") == \
        ["runs to the end"]


def test_an_empty_note_is_ignored():
    assert m.extract("<update_message>   </update_message>") == []


def test_several_notes_in_one_commit_all_survive():
    raw = "<update_message>first</update_message><update_message>second</update_message>"
    assert m.extract(raw) == ["first", "second"]


# -- strip -------------------------------------------------------------------

def test_strip_removes_the_note_but_keeps_the_message():
    out = m.strip(COMMITS[0]["message"])
    assert "Tick the e_mailman" not in out, "the note is rendered separately"
    assert "Rework the provider contract" in out
    assert "Ordinary body text." in out


def test_strip_is_a_no_op_without_a_note():
    assert m.strip(COMMITS[1]["message"]) == COMMITS[1]["message"].strip()


# -- the pending set ---------------------------------------------------------

def test_notes_carry_their_commit_context():
    note = m.notes_from_commits(COMMITS)[0]
    assert note.sha == "abc1234567"
    assert note.subject.startswith("fix(engine)")
    assert note.author == "Niccc2007"


def test_every_note_in_range_is_offered_not_just_the_newest():
    """Someone three versions behind needs all three notes."""
    commits = [{"sha": f"sha{i}", "message": f"s{i}\n<update_message>n{i}</update_message>"}
               for i in range(3)]
    assert [n.text for n in m.pending(commits)] == ["n0", "n1", "n2"]


def test_junk_entries_do_not_break_the_scan():
    assert m.notes_from_commits([None, "nope", {}, 42]) == []


# -- dismissal ---------------------------------------------------------------

def test_dismissing_hides_it_from_pending():
    notes = m.notes_from_commits(COMMITS)
    assert len(m.pending(COMMITS)) == 1
    m.dismiss(notes[0])
    assert m.pending(COMMITS) == []


def test_dismissal_is_per_note_not_per_commit():
    """Two notes in one commit must dismiss independently."""
    commits = [{"sha": "same111", "message": "s\n<update_message>one</update_message>"
                                             "<update_message>two</update_message>"}]
    notes = m.notes_from_commits(commits)
    assert notes[0].key != notes[1].key
    m.dismiss(notes[0])
    assert [n.text for n in m.pending(commits)] == ["two"]


def test_dismissing_one_commit_does_not_hide_another():
    commits = [{"sha": "aaa", "message": "a\n<update_message>A</update_message>"},
               {"sha": "bbb", "message": "b\n<update_message>B</update_message>"}]
    m.dismiss(m.notes_from_commits(commits)[0])
    assert [n.text for n in m.pending(commits)] == ["B"]


def test_dismissal_survives_a_restart():
    """Persisted to disk, not held in memory."""
    notes = m.notes_from_commits(COMMITS)
    m.dismiss(notes[0])
    assert m.is_dismissed(notes[0])
    assert m.is_dismissed(m.notes_from_commits(COMMITS)[0]), "re-parsed note stays gone"


def test_undismiss_all_brings_them_back():
    m.dismiss(m.notes_from_commits(COMMITS)[0])
    m.undismiss_all()
    assert len(m.pending(COMMITS)) == 1


def test_a_corrupt_store_does_not_crash_the_window(tmp_path, monkeypatch):
    store = tmp_path / "dismissed.json"
    store.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(m, "_STORE", store)
    assert len(m.pending(COMMITS)) == 1, "unreadable store means nothing dismissed"
