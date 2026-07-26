"""
tests/systema/ui/test_card_icons.py

One rule across the card set: the glyph depicts the ACTION, never the tool —
every card already prints its tool name right next to the icon, so spending the
icon on it again said nothing. `±` sat on every edit whether it added 91 lines
or removed 116, and load_skill and unload_skill both showed `▤`, so two
OPPOSITE events were indistinguishable in the transcript.

Sharing a glyph is allowed where the action really is the same (⌕ searching, ▤
a page you read, ⊘ refused). What is not allowed is one glyph across opposite
actions — the tests below pin exactly that line.

Both rules are pure functions of the card's own persisted data, which is what
makes reloads free: a card rebuilt from a session file derives the identical
glyph, so there is no icon state to save and none that can drift.
"""
import pytest

from systema.ui.chat.event_cards import file_op_glyph, skill_action_glyph


# ── write-like: the glyph is the net change ──────────────────────────────────

def test_an_edit_that_adds_shows_a_plus():
    assert file_op_glyph('edit_file', added=91, removed=0) == '+'


def test_an_edit_that_removes_shows_a_minus():
    assert file_op_glyph('edit_file', added=4, removed=120) == '−'


def test_the_sign_follows_the_NET_not_either_count():
    """+120 −4 and +4 −120 touch the same number of lines in total; the icon
    has to disagree about them."""
    assert file_op_glyph('edit_file', added=120, removed=4) == '+'
    assert file_op_glyph('edit_file', added=4, removed=120) == '−'


def test_a_balanced_edit_stays_neutral():
    assert file_op_glyph('edit_file', added=7, removed=7) == '±'


def test_unknown_counts_stay_neutral():
    """A tool that reported no numbers must not claim a direction."""
    assert file_op_glyph('write_file') == '±'


def test_a_new_file_is_its_own_thing():
    """Creating a file is not 'an edit that happened to add lines'."""
    assert file_op_glyph('write_file', added=255, removed=0, created=True) == '⊕'
    assert file_op_glyph('write_file', added=255, removed=0) == '+'


def test_a_rejected_op_beats_every_other_signal():
    """Nothing was written, so the counts describe a change that never landed."""
    assert file_op_glyph('edit_file', added=91, removed=0, rejected=True) == '⊘'
    assert file_op_glyph('write_file', created=True, rejected=True) == '⊘'


# ── read-like: the glyph is what you looked at ───────────────────────────────

def test_a_read_is_a_page():
    assert file_op_glyph('read_file', added=None, removed=None) == '▤'


def test_a_grep_is_a_search():
    assert file_op_glyph('grep') == '⌕'


def test_a_read_never_takes_a_write_glyph():
    """read_file carries a line RANGE in the same field shape; it must not be
    mistaken for a change."""
    assert file_op_glyph('read_file', added=283, removed=0) == '▤'


# ── skills: opposite actions must not look alike ─────────────────────────────

def test_load_and_unload_point_opposite_ways():
    load, unload = skill_action_glyph('load'), skill_action_glyph('unload')
    assert load == '↧' and unload == '↥'
    assert load != unload, "opposite actions sharing one glyph is the bug"


def test_a_refused_skill_load_matches_a_refused_file_op():
    """Deliberate reuse: both are 'you asked, it did not happen'."""
    assert skill_action_glyph('load', ok=False) == '⊘'
    assert skill_action_glyph('unload', ok=False) == '⊘'
    assert skill_action_glyph('load', ok=False) == file_op_glyph(
        'edit_file', rejected=True)


# ── the shared-glyph policy, stated once ─────────────────────────────────────

def test_searching_shares_one_glyph_on_purpose():
    """grep and web_search are both a search — the web-search card uses ⌕ too."""
    assert file_op_glyph('grep') == '⌕'


def test_no_glyph_spans_two_opposite_actions():
    added = file_op_glyph('edit_file', added=10, removed=0)
    removed = file_op_glyph('edit_file', added=0, removed=10)
    created = file_op_glyph('write_file', created=True)
    refused = file_op_glyph('edit_file', rejected=True)
    assert len({added, removed, created, refused}) == 4


# ── reload parity ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("info", [
    {'tool': 'edit_file', 'added': 91, 'removed': 0},
    {'tool': 'edit_file', 'added': 4, 'removed': 120},
    {'tool': 'write_file', 'added': 255, 'removed': 0, 'created': True},
    {'tool': 'read_file'},
    {'tool': 'grep'},
    {'tool': 'edit_file', 'added': 3, 'removed': 3, 'rejected': True},
])
def test_the_glyph_replays_from_the_persisted_dict(info):
    """add_file_op_card stores its whole info dict as the ui_event payload and
    rebuilds from it on reload — so deriving beats storing."""
    live = file_op_glyph(info['tool'], info.get('added'), info.get('removed'),
                         info.get('created', False), info.get('rejected', False))
    replayed = file_op_glyph(info['tool'], info.get('added'), info.get('removed'),
                             info.get('created', False), info.get('rejected', False))
    assert live == replayed
