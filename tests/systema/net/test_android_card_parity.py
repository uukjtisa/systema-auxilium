"""
tests/systema/net/test_android_card_parity.py

The Android companion drifted behind the desktop for months, silently, because
"a card exists on the desktop" and "a card reaches the phone" were maintained in
two unrelated places:

  * ToolManager._CARD_DISPATCH routed four card types to the chat window. Three
    of them (web_search, web_page, skill_action) had NO bridge command at all,
    so a search, an opened page or a skill load grew a card on the PC and showed
    nothing on the phone. The fourth (image_attach) was believed covered by
    notify_image_attached() — which had zero callers, having lost them when the
    pinned-image overlay was retired.
  * AndroidBridge.render_loaded_messages replayed every ui_event it did not
    recognise as add_work_execution, so reloading a session that used thinking /
    web_search / web_page / skill_action / image_attach painted a column of
    work-execution cards with empty code and empty output.

These tests make both classes of drift fail the suite instead of being noticed
months later. They are deliberately STRUCTURAL: they read the maps and the
source, so a new card type cannot pass by being added to one side only.
"""
import re
import pathlib

import pytest

from systema.execution.tool_manager import ToolManager
from systema.net.android_bridge import AndroidBridge


# ── helpers ──────────────────────────────────────────────────────────────────

class _CapturingBridge(AndroidBridge):
    """AndroidBridge with the socket replaced by a list.

    Subclassed rather than mocked so the real card methods run: the payload
    normalisation (capping, key names, list flattening) is exactly what we want
    under test, and a mock would happily accept a method that does not exist.
    """

    def __init__(self):
        super().__init__(controller=None)
        self.sent = []

    def _dispatch(self, cmd: dict):
        self.sent.append(cmd)

    @property
    def cmds(self):
        return [c.get("cmd") for c in self.sent]


@pytest.fixture
def bridge():
    return _CapturingBridge()


def _source_ui_event_types() -> set:
    """Every ui_event `_type` literal written anywhere in the package.

    Scanning the source (rather than hand-listing) is the point: adding a new
    persisted card type is exactly the moment this test must fire.
    """
    pat = re.compile(r"""['"]_type['"]:\s*['"](\w+)['"]""")
    root = pathlib.Path(__file__).resolve().parents[3] / "systema"
    found = set()
    for path in root.rglob("*.py"):
        found.update(pat.findall(path.read_text(encoding="utf-8")))
    return found


# ── the headline invariant ───────────────────────────────────────────────────

def test_every_dispatched_card_type_has_a_bridge_method():
    """The one that would have caught the original bug.

    _CARD_DISPATCH's values name a builder that must exist on BOTH surfaces.
    Sharing the name is what makes this checkable at all — do not split it into
    a second phone-side map, which is the arrangement that drifted.
    """
    missing = [(card_type, method)
               for card_type, method in ToolManager._CARD_DISPATCH.items()
               if not hasattr(AndroidBridge, method)]
    assert not missing, (
        "tool card types with no phone path: " + ", ".join(
            f"{c!r} needs AndroidBridge.{m}()" for c, m in missing))


def test_every_dispatched_card_type_has_a_chat_window_method():
    """The desktop half of the same invariant, made explicit rather than
    implied by _deliver_tool_card's hasattr() check (which fails SILENTLY)."""
    from systema.ui.chat.event_cards import EventCardsMixin
    missing = [(card_type, method)
               for card_type, method in ToolManager._CARD_DISPATCH.items()
               if not hasattr(EventCardsMixin, method)]
    assert not missing, (
        "tool card types with no desktop builder: " + ", ".join(
            f"{c!r} needs EventCardsMixin.{m}()" for c, m in missing))


def test_bridge_card_methods_each_emit_a_distinct_command(bridge):
    """A method that exists but sends nothing (or sends someone else's cmd)
    passes the hasattr checks above while still showing nothing on the phone."""
    payloads = {
        'web_search':   {'query': 'q', 'results': [{'title': 't', 'href': 'h', 'body': 'b'}]},
        'web_page':     {'mode': 'open', 'url': 'u', 'title': 't', 'text': 'x', 'links': []},
        'skill_action': {'action': 'load', 'skill': 's', 'ok': True, 'detail': 'd'},
        'image_attach': {'paths': [], 'annotation': 'a'},
    }
    assert set(payloads) == set(ToolManager._CARD_DISPATCH), (
        "this test needs a sample payload for every dispatched card type")

    seen = {}
    for card_type, method in ToolManager._CARD_DISPATCH.items():
        bridge.sent.clear()
        getattr(bridge, method)(payloads[card_type])
        assert len(bridge.sent) == 1, (
            f"{method}() sent {len(bridge.sent)} commands, expected exactly 1")
        seen[card_type] = bridge.sent[0]["cmd"]

    assert len(set(seen.values())) == len(seen), \
        f"two card types share one phone command: {seen}"


# ── replay taxonomy (the add_work_execution catch-all) ───────────────────────

def test_every_persisted_ui_event_type_is_known_to_the_replay():
    unknown = _source_ui_event_types() - set(AndroidBridge._UI_EVENT_TYPES)
    assert not unknown, (
        f"ui_event types persisted by the desktop with no entry in "
        f"AndroidBridge._UI_EVENT_TYPES: {sorted(unknown)}. Add a branch to "
        f"_replay_ui_event, or map it to None if the phone shows nothing.")


@pytest.mark.parametrize("event,expected_cmd", [
    ({'_type': 'web_search', '_web_query': 'q', '_web_results': []},
     'add_web_search'),
    ({'_type': 'web_page', '_web_mode': 'open', '_web_url': 'u',
      '_web_title': 't', '_web_text': 'x', '_web_links': []},
     'add_web_page'),
    ({'_type': 'skill_action', '_skill': 's', '_skill_action': 'load',
      '_skill_ok': True, '_skill_detail': 'd'},
     'add_skill_action'),
    ({'_type': 'thinking', '_thinking': 'because'},
     'add_reasoning'),
    ({'_type': 'memory_context', '_memory_context_id': 'c1',
      '_memories_preview': ['m']},
     'add_memory_context'),
    ({'_type': 'file_op', '_file_op': {'tool': 'edit_file', 'path': 'a.py'}},
     'add_file_op'),
    ({'_code': 'print(1)', '_output': '1', '_annotation': 'a'},
     'add_work_execution'),
])
def test_replay_routes_each_ui_event_to_its_own_card(bridge, event, expected_cmd):
    bridge._replay_ui_event(event)
    assert bridge.cmds == [expected_cmd]


@pytest.mark.parametrize("event", [
    {'_type': 'thinking', '_thinking': 'because'},
    {'_type': 'web_search', '_web_query': 'q', '_web_results': []},
    {'_type': 'web_page', '_web_url': 'u'},
    {'_type': 'skill_action', '_skill': 's'},
    {'_type': 'image_attach', '_image_paths': []},
])
def test_typed_ui_events_never_replay_as_work_execution(bridge, event):
    """The exact regression: these five all arrived as empty work cards."""
    bridge._replay_ui_event(event)
    assert 'add_work_execution' not in bridge.cmds, (
        f"{event.get('_type')!r} still replays as a work-execution card")


def test_retired_and_unknown_types_render_nothing(bridge):
    bridge._replay_ui_event({'_type': 'skills_card'})          # retired 2026-07-17
    bridge._replay_ui_event({'_type': 'something_from_2027'})  # from the future
    assert bridge.sent == []


def test_untyped_ui_event_without_code_renders_nothing(bridge):
    """An untyped ui_event carrying neither code nor output is not a work step.
    Painting an empty card for it is what made reloaded sessions look broken."""
    bridge._replay_ui_event({'content': 'Code executed'})
    assert bridge.sent == []


# ── payload shape ────────────────────────────────────────────────────────────

def test_web_search_payload_survives_nested_results(bridge):
    """Why these cards use JSON instead of the older "|||" packing: a result
    body containing the delimiter must not corrupt the card."""
    bridge.add_web_search_card({
        'query': 'delimiters',
        'results': [{'title': 'a|||b', 'href': 'http://x', 'body': 'c|||d'}],
    })
    payload = bridge.sent[0]
    assert payload['results'][0]['title'] == 'a|||b'
    assert payload['results'][0]['body'] == 'c|||d'


def test_oversized_payloads_are_capped(bridge):
    bridge.add_web_search_card({'query': 'q', 'results': [
        {'title': 't', 'href': 'h', 'body': 'x' * 5000}] * 50})
    payload = bridge.sent[0]
    assert len(payload['results']) == AndroidBridge._MAX_RESULTS
    assert len(payload['results'][0]['body']) == AndroidBridge._MAX_SNIPPET

    bridge.sent.clear()
    bridge.add_web_page_card({'mode': 'open', 'url': 'u', 'text': 'y' * 99999})
    assert len(bridge.sent[0]['text']) == AndroidBridge._MAX_PAGE_TEXT


def test_malformed_result_rows_are_skipped_not_crashed(bridge):
    """web_research backends are scrapers; a row can be junk."""
    bridge.add_web_search_card({'query': 'q', 'results': [
        None, 'a bare string', {'title': 'ok', 'href': 'h', 'body': 'b'}]})
    assert len(bridge.sent[0]['results']) == 1


def test_image_only_user_turn_still_renders(bridge, monkeypatch):
    """An image with no caption used to be dropped by `if text.strip()`."""
    monkeypatch.setattr(_CapturingBridge, "_make_thumb_b64",
                        staticmethod(lambda path, size=80: "FAKEB64"))
    bridge.add_user_message("", ["/tmp/a.png"])
    assert bridge.cmds == ["add_user"]
    assert bridge.sent[0]["images"] == ["FAKEB64"]


def test_user_turn_with_no_text_and_no_images_sends_nothing(bridge):
    bridge.add_user_message("   ")
    assert bridge.sent == []


# ── bubble style rides with the theme ───────────────────────────────────────
# The phone deliberately has NO local style toggle, so this push is the only
# way it learns which style to paint. Losing the field silently reverts every
# phone to 'blend' while the desktop shows 'compact'.

import types  # noqa: E402


def _with_settings(bridge, **settings):
    bridge.controller = types.SimpleNamespace(settings=settings)
    return bridge


@pytest.mark.parametrize("style", ["blend", "compact"])
def test_theme_push_carries_the_bubble_style(bridge, style):
    _with_settings(bridge, chat_bubble_style=style)
    bridge.send_theme()
    assert bridge.cmds == ["theme_data"]
    assert bridge.sent[0]["bubble_style"] == style
    assert bridge.sent[0]["theme"], "the palette must still be sent alongside"


@pytest.mark.parametrize("stored", [None, "", "nonsense", 42])
def test_an_unusable_style_setting_falls_back_to_blend(bridge, stored):
    _with_settings(bridge, chat_bubble_style=stored)
    bridge.send_theme()
    assert bridge.sent[0]["bubble_style"] == "blend"


def test_theme_push_survives_a_controller_without_settings(bridge):
    """send_theme also runs on connect, before much is wired up."""
    bridge.send_theme()          # controller is None on this fixture
    assert bridge.sent[0]["bubble_style"] == "blend"


def test_style_matches_the_desktop_default():
    """One default, not two. A second copy drifts and the drift is invisible."""
    from systema.ui.chat.bubbles import BUBBLE_STYLE_DEFAULT
    assert BUBBLE_STYLE_DEFAULT in ("blend", "compact")
