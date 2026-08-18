"""
tests/systema/ui/test_image_bubbles.py

The chat-side half of the image pipeline: attaching puts a picture straight
into the transcript, detach/delete edit the history the model reads, and the
retired pinned-image overlay stays retired.

Driven against the real mixin methods with a light stub `self`, in the style of
test_bubble_width.py — building a whole ChatWindow is neither necessary nor
cheap for this.
"""
import os
import struct
import types
import zlib

import pytest

pytest.importorskip("PyQt6")

from systema.common import image_cache, image_refs          # noqa: E402
from systema.ui.chat.image_bubbles import ImageBubblesMixin  # noqa: E402


def _png(path, w=8, h=4):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b""))
    return path


class _Chat:
    """The smallest thing the mixin's state-change methods need."""

    def __init__(self, history):
        self._hist_list = history
        self.rendered = 0
        self.restyled = []          # image numbers repainted IN PLACE
        self.notes = []
        self.saved = 0
        self.controller = types.SimpleNamespace(
            ai=types.SimpleNamespace(conversation_history=history),
            _auto_save_session=self._save)
        # Stand-ins for the live bubbles: one per history entry that carries
        # images, shaped exactly like the real message_widgets entries so the
        # mixin's own lookup runs unmodified.
        self.chat_scroll_area = None
        self.message_widgets = []
        for entry in history:
            refs = entry.get("_images")
            if refs:
                self.message_widgets.append({
                    "_image_refs": refs,
                    "zoom_restyle": (lambda r=refs: self.restyled.extend(
                        x.get("n") for x in r)),
                })

    # mixin under test
    _history = ImageBubblesMixin._history
    toggle_image_attached = ImageBubblesMixin.toggle_image_attached
    detach_image = ImageBubblesMixin.detach_image
    reattach_image = ImageBubblesMixin.reattach_image
    delete_image = ImageBubblesMixin.delete_image
    set_all_images_attached = ImageBubblesMixin.set_all_images_attached
    delete_all_images = ImageBubblesMixin.delete_all_images
    session_image_refs = ImageBubblesMixin.session_image_refs
    _save_and_retally = ImageBubblesMixin._save_and_retally
    _restyle_image_bubbles = ImageBubblesMixin._restyle_image_bubbles
    _after_attach_change = ImageBubblesMixin._after_attach_change
    _after_structural_change = ImageBubblesMixin._after_structural_change
    _replay_keeping_scroll = ImageBubblesMixin._replay_keeping_scroll

    # collaborators
    def _save(self):
        self.saved += 1

    def render_loaded_messages(self):
        self.rendered += 1

    def _invalidate_token_estimate(self):
        pass

    def add_system_message(self, msg):
        self.notes.append(msg)


def _history(ref):
    return [{"role": "user", "content": "look at this", "_images": [ref]}]


@pytest.fixture
def cached(tmp_path):
    ref = image_cache.store(_png(tmp_path / "shot.png"), n=1)
    yield ref
    image_cache.discard(ref, still_referenced=[ref])


# ── state changes ────────────────────────────────────────────────────────────

def test_detach_takes_the_image_out_of_context_without_rebuilding(cached):
    """THE BUG THIS EXISTS FOR

    Detaching used to call render_loaded_messages(), which tears down and
    recreates every widget in the transcript. The view jumped, the scroll
    position moved, and the picture next to the one you had just clicked was
    somewhere else by the time you reached for it — reported as "it moves my
    whole chat screen ... it looks like it rebuilds the whole chat window".

    Detach is a FLAG FLIP. It must repaint the bubbles that show the image and
    touch nothing else.
    """
    chat = _Chat(_history(cached))

    chat.detach_image(1)

    assert cached["attached"] is False
    assert chat.rendered == 0, "a flag flip must never replay the transcript"
    assert chat.restyled == [1], "the bubble showing image 1 must repaint"
    assert chat.saved == 1, "the change must still be persisted"


def test_reattach_also_stays_in_place(cached):
    chat = _Chat(_history(cached))
    chat.detach_image(1)
    chat.reattach_image(1)

    assert cached["attached"] is True
    assert chat.rendered == 0
    assert image_cache.exists(cached), "detach must never delete the cached file"


def test_bulk_attach_changes_do_not_rebuild_either(tmp_path):
    """Detach-all is the same flag flip N times, not N rebuilds."""
    a = image_cache.store(_png(tmp_path / "a.png"), n=1)
    b = image_cache.store(_png(tmp_path / "b.png", w=6), n=2)
    chat = _Chat([{"role": "user", "content": "x", "_images": [a, b]}])

    chat.set_all_images_attached(False)

    assert chat.rendered == 0
    assert chat.restyled == [1, 2], "every image bubble repaints, once"
    image_cache.discard(a, still_referenced=[])
    image_cache.discard(b, still_referenced=[])


def test_only_the_bubbles_showing_that_image_repaint(tmp_path):
    """A detach in a long transcript must not touch unrelated bubbles."""
    a = image_cache.store(_png(tmp_path / "a.png"), n=1)
    b = image_cache.store(_png(tmp_path / "b.png", w=6), n=2)
    chat = _Chat([{"role": "user", "content": "x", "_images": [a]},
                  {"role": "user", "content": "y", "_images": [b]}])

    chat.detach_image(2)

    assert chat.restyled == [2], "image 1's bubble had no reason to repaint"
    image_cache.discard(a, still_referenced=[])
    image_cache.discard(b, still_referenced=[])


def test_the_eye_resyncs_even_when_the_toggle_changes_nothing(cached):
    """The eye is a CHECKABLE button — the click flips its own look before the
    handler runs. An unknown number must not leave it showing a lie."""
    chat = _Chat(_history(cached))

    chat.toggle_image_attached(1)
    assert cached["attached"] is False
    chat.toggle_image_attached(1)
    assert cached["attached"] is True, "the one handler must flip both ways"

    chat.restyled.clear()
    chat.toggle_image_attached(99)          # no such image
    assert chat.restyled == [] and chat.rendered == 0


def test_detaching_an_unknown_number_does_nothing(cached):
    chat = _Chat(_history(cached))
    chat.detach_image(99)
    assert chat.rendered == 0 and chat.saved == 0 and chat.restyled == []


def test_detaching_twice_costs_one_save(cached):
    """No-op changes must not repaint or persist a second time."""
    chat = _Chat(_history(cached))
    chat.detach_image(1)
    chat.detach_image(1)
    assert chat.rendered == 0
    assert chat.saved == 1 and chat.restyled == [1]


def test_delete_does_replay_because_it_is_structural(cached):
    """The one case that genuinely cannot be patched in place: the ref leaves
    history, so a collage loses a tile or a bubble disappears."""
    chat = _Chat(_history(cached))

    chat.delete_image(1, confirm=False)

    assert chat.rendered == 1, "a structural change must replay the transcript"


def test_the_attach_path_has_no_route_back_to_a_rebuild():
    """A guard, not a formality: the rebuild came back once already because the
    module docstring recommended it. Detach must not reach the replay."""
    import inspect

    src = inspect.getsource(ImageBubblesMixin._after_attach_change)
    assert "render_loaded_messages" not in src

    tile = inspect.getsource(ImageBubblesMixin._build_image_tile)
    assert "toggle_image_attached" in tile, \
        "the eye must have ONE handler, not one wired per direction"
    assert "attached = bool(ref.get('attached'))" not in tile.split("def _restyle")[0], \
        "capturing attach state at build time is what froze the old tiles"


def test_delete_removes_the_image_and_its_cache_file(tmp_path):
    ref = image_cache.store(_png(tmp_path / "gone.png"), n=1)
    chat = _Chat(_history(ref))

    chat.delete_image(1, confirm=False)

    assert image_refs.find(chat._hist_list, 1) == (None, None)
    assert not image_cache.exists(ref), "delete must clear the cached bytes"


def test_delete_keeps_a_shared_cache_file_alive(tmp_path):
    """Two refs to the same picture: deleting one must not blank the other."""
    src = _png(tmp_path / "same.png")
    a = image_cache.store(src, n=1)
    b = image_cache.store(src, n=2)
    hist = [{"role": "user", "content": "x", "_images": [a, b]}]
    chat = _Chat(hist)

    chat.delete_image(1, confirm=False)

    assert image_cache.exists(b)
    image_cache.discard(b, still_referenced=[b])


def test_bulk_detach_then_attach(cached):
    chat = _Chat(_history(cached))
    assert chat.set_all_images_attached(False) == 1
    assert cached["attached"] is False
    assert chat.set_all_images_attached(True) == 1
    assert chat.set_all_images_attached(True) == 0, "no-op reports zero"


def test_delete_all_clears_the_session(tmp_path):
    a = image_cache.store(_png(tmp_path / "a.png"), n=1)
    b = image_cache.store(_png(tmp_path / "b.png", w=6), n=2)
    chat = _Chat([{"role": "user", "content": "x", "_images": [a, b]}])

    assert chat.delete_all_images(confirm=False) == 2
    assert chat.session_image_refs() == []


# ── the retired overlay ──────────────────────────────────────────────────────

def test_the_pinned_image_overlay_is_gone():
    """The overlay was the only record that an image was live, and it was pure
    UI state — which is why a reload lost every picture. It must not come back:
    images live on the history entry now.
    """
    from systema.ui.chat.input_dock import InputDockMixin

    for retired in ("_add_pinned_image_widget", "_update_pinned_overlay",
                    "_remove_pinned_image", "_clear_image_preview",
                    "_remove_one_image_preview"):
        assert not hasattr(InputDockMixin, retired), (
            f"{retired} is back — attachments must not live in UI state again")


def test_the_resize_path_calls_nothing_that_does_not_exist():
    """Guard for a whole CLASS of bug, learned the hard way.

    Deleting the pinned-image overlay also took `_position_input_handles` with
    it. That runs on every resize event, so each frame raised AttributeError;
    the crash watcher wrote a full forensic dump per frame (psutil open_files()
    ~500ms on the GUI thread) and dragging the window turned to slideshow. It
    also aborted _position_input_overlay BEFORE the line that reserves the
    chat's bottom margin, so the newest messages hid under the input pill.

    Anything the resize path calls on self must therefore exist.
    """
    import inspect
    import re

    from systema.ui.chat.input_dock import InputDockMixin
    from systema.ui.chat_window import ChatWindow

    hot = (InputDockMixin._position_input_overlay,
           InputDockMixin._position_input_overlay_settle,
           InputDockMixin._session_intro_showing,
           InputDockMixin._position_input_handles)
    missing = set()
    for fn in hot:
        for name in re.findall(r"self\.(_?[a-zA-Z][a-zA-Z0-9_]*)\(",
                               inspect.getsource(fn)):
            if not hasattr(ChatWindow, name):
                missing.add(name)
    assert not missing, f"resize path calls missing methods: {sorted(missing)}"


def test_the_compat_shims_route_into_the_new_pipeline():
    """The Android bridge and controller still call these by name."""
    calls = []

    class _C(_Chat):
        def attach_images(self, paths, origin='user', annotation=''):
            calls.append((list(paths), origin))

    chat = _C([])
    ImageBubblesMixin._show_image_preview(chat, "/tmp/x.png")
    assert calls == [(["/tmp/x.png"], "user")]
    # clear_pinned_images survives as a no-op: clearing the chat clears history.
    assert ImageBubblesMixin.clear_pinned_images(chat) is None


def test_send_message_no_longer_collects_images():
    """Images are in the conversation before Enter is pressed, so send_message
    must not gather, pin, or clear anything — the old dead ('puter',
    'custom_script') provider gate went with it."""
    import inspect

    from systema.ui.chat_window import ChatWindow

    src = inspect.getsource(ChatWindow.send_message)
    assert "puter" not in src
    assert "pinned_images" not in src
    assert "attached_images" not in src
    assert "send_message_with_image" not in src


# ── greeting banner ──────────────────────────────────────────────────────────

def test_the_greeting_is_time_aware_and_varies():
    import random
    from datetime import datetime

    from systema.common.greeting import greeting, time_bucket

    assert time_bucket(datetime(2026, 7, 27, 7)) == "morning"
    assert time_bucket(datetime(2026, 7, 27, 14)) == "afternoon"
    assert time_bucket(datetime(2026, 7, 27, 19)) == "evening"
    assert time_bucket(datetime(2026, 7, 27, 2)) == "night"

    noon = datetime(2026, 7, 27, 14)
    rng = random.Random(3)
    lines = {greeting("Thirdy", noon, rng) for _ in range(60)}
    assert len(lines) > 1, "a daily user should not see one identical line"
    assert all("Thirdy" in ln for ln in lines)


def test_the_greeting_reads_cleanly_with_no_name_set():
    """An unset name must not leave a dangling comma."""
    from datetime import datetime

    from systema.common.greeting import greeting

    for hour in (7, 14, 19, 2):
        line = greeting("", datetime(2026, 7, 27, hour))
        assert line and not line.rstrip().endswith(",")
        assert "{name}" not in line


def test_every_phrasing_has_a_nameless_twin():
    """Paired by index across ALL THREE pools (time, day, day+time) so they
    cannot drift — a named line with no twin would fall back to something in a
    different tone."""
    from systema.common.greeting import all_phrasings

    pairs = list(all_phrasings())
    assert len(pairs) >= 250, "the pools should carry real variety"
    for named, anon in pairs:
        # A few privilege notices are deliberately impersonal ("Privilege
        # level: administrator. Proceed with care") — a compliance-register
        # line does not address anyone. Those are name-free on BOTH halves.
        if "{name}" in named:
            assert named.format(name="X") != anon, f"{named!r} twin is identical"
        else:
            assert named == anon, f"{named!r} is name-free but has a different twin"
        assert "{name}" not in anon, f"{anon!r} is not name-less"
        assert anon and not anon.rstrip().endswith(",")
        assert not anon.rstrip().endswith("—")


def test_every_day_and_hour_has_plenty_to_draw_from():
    """A daily user should not see the same opening twice in a row, on any day
    of the week or at any hour."""
    from datetime import datetime, timedelta

    from systema.common.greeting import pool_for

    monday = datetime(2026, 7, 27)          # a Monday
    for day in range(7):
        for hour in (8, 14, 19, 2):
            when = monday + timedelta(days=day, hours=hour)
            assert len(pool_for(when)) >= 12, f"thin pool for {when}"


def test_the_day_of_the_week_shows_up_in_the_greeting():
    """Friday should be able to say so — that is the point of the day pool."""
    import random
    from datetime import datetime, timedelta

    from systema.common.greeting import greeting

    friday_evening = datetime(2026, 7, 27) + timedelta(days=4, hours=19)
    rng = random.Random(0)
    lines = {greeting("Thirdy", friday_evening, rng) for _ in range(300)}

    assert any("Friyay" in ln for ln in lines)
    assert any("Friday" in ln for ln in lines)
    # ...and the generic evening lines are still in the mix.
    assert any("evening" in ln.lower() or "Evening" in ln for ln in lines)


def test_the_night_belongs_to_the_day_it_started_on():
    """THE BUG THIS EXISTS FOR

    At 02:00 on a Tuesday the banner read "Happy Tuesday night, Thirdy." The
    calendar agrees and nobody else does — at 2am you are still living MONDAY
    night. Reported by the user at 2am, sleep-deprived, which is exactly the
    audience for the small-hours lines this was crowding out.
    """
    from datetime import datetime

    from systema.common.greeting import greeting_weekday

    MON, TUE, SUN = 0, 1, 6

    # 2am Tuesday is Monday night.
    assert greeting_weekday(datetime(2026, 7, 28, 2, 0)) == MON
    # ...right up to the edge of the small hours.
    assert greeting_weekday(datetime(2026, 7, 28, 4, 59)) == MON
    # 05:00 is morning: the new day has genuinely begun.
    assert greeting_weekday(datetime(2026, 7, 28, 5, 0)) == TUE
    # Late Tuesday evening is still Tuesday.
    assert greeting_weekday(datetime(2026, 7, 28, 23, 30)) == TUE
    # And it wraps across the week boundary too: 1am Monday is Sunday night.
    assert greeting_weekday(datetime(2026, 7, 27, 1, 0)) == SUN


def test_two_am_never_names_tomorrow():
    """The whole pool, not just the helper: no line drawn at 2am on a Tuesday
    may say "Tuesday", because that is the day the user has not lived yet."""
    import random
    from datetime import datetime

    from systema.common.greeting import greeting

    two_am_tuesday = datetime(2026, 7, 28, 2, 0)
    rng = random.Random(0)
    lines = {greeting("Thirdy", two_am_tuesday, rng) for _ in range(400)}

    assert not any("Tuesday" in ln for ln in lines), \
        "the small hours must not name the day that just started"
    assert any("Monday" in ln for ln in lines), \
        "the night it actually is should still be nameable"


def test_the_weekend_signal_uses_the_same_rule():
    """1am Monday is Sunday night — still the weekend to whoever is awake."""
    from datetime import datetime

    from systema.common.greeting import collect_signals

    assert "weekend" in collect_signals([], datetime(2026, 7, 27, 1, 0))
    assert "weekend" not in collect_signals([], datetime(2026, 7, 27, 10, 0))


def test_midnight_counts_as_the_dead_of_night():
    """00:30 used to fall through to ordinary night lines because the signal
    started at 01:00."""
    from datetime import datetime

    from systema.common.greeting import collect_signals

    for hour in (0, 1, 3, 4):
        assert "dead_of_night" in collect_signals(
            [], datetime(2026, 7, 28, hour, 30)), f"{hour}:30 is the small hours"
    assert "dead_of_night" not in collect_signals([], datetime(2026, 7, 28, 5, 30))


def test_day_lines_also_drop_the_name_cleanly():
    import random
    from datetime import datetime, timedelta

    from systema.common.greeting import greeting

    friday = datetime(2026, 7, 27) + timedelta(days=4, hours=19)
    rng = random.Random(0)
    lines = {greeting("USER", friday, rng) for _ in range(300)}

    assert "Happy Friyay!" in lines
    assert all("USER" not in ln for ln in lines)


@pytest.mark.parametrize("name", ["USER", "user", " User ", "USERNAME",
                                  "Administrator", "none", ""])
def test_a_placeholder_name_is_treated_as_unset(name):
    """The shipped default was the literal "USER" — "Still going, USER?" is
    worse than no name at all."""
    from datetime import datetime

    from systema.common.greeting import greeting

    line = greeting(name, datetime(2026, 7, 27, 19))
    assert name.strip() not in line or not name.strip()
    assert "," not in line.rstrip(",") or not line.endswith(",")


def test_a_real_name_is_used():
    from datetime import datetime

    from systema.common.greeting import greeting

    assert "Thirdy" in greeting("Thirdy", datetime(2026, 7, 27, 19))


def test_the_placeholder_list_has_one_home():
    """controller.py imports it rather than keeping a second copy: two lists of
    placeholder names drift, and the failure is silent."""
    from systema.app import controller
    from systema.common.greeting import PLACEHOLDER_NAMES

    assert controller._PLACEHOLDER_NAMES is PLACEHOLDER_NAMES


# ── real widget construction ─────────────────────────────────────────────────
# The stubs above exercise the state machine; these build the actual Qt widgets
# offscreen, which is where a typo in a stylesheet or a layout call would
# otherwise hide until the app was run by hand.

@pytest.fixture
def widget_host(qapp):
    from PyQt6.QtWidgets import QVBoxLayout, QWidget

    from systema.ui.theme import THEMES

    from systema.ui.chat.bubbles import BubblesMixin

    class Host(QWidget, ImageBubblesMixin, BubblesMixin):
        def __init__(self):
            super().__init__()
            self.chat_layout = QVBoxLayout(self)
            self.chat_layout.addStretch()
            self.message_widgets = []
            self.controller = types.SimpleNamespace(
                get_user_name=lambda: "Thirdy",
                get_assistant_name=lambda: "Kimi")

        font_px = 13          # the 13px message base; raised to model zoom
        # Turn-shell collaborators (avatar row) for the assistant-turn tests.
        bot_avatar = "B"
        user_avatar = "U"
        _bot_avatar_size = 32

        def _t(self):
            return THEMES["obsidian_blue"]

        def _get_msg_font_size(self):
            return self.font_px

        def _card_z(self, px):
            return max(7, round(px * self._get_msg_font_size() / 13.0))

        def _bubble_max_width(self):
            return 620

        def _end_ai_turn_group(self):
            pass

        def _ensure_ai_turn_group(self):
            pass

        def _insert_turn_segment(self, w, at_top=False):
            self.chat_layout.insertWidget(0, w)

        def _animate_message_in(self, w, on_settled=None):
            if on_settled:
                on_settled()

        def scroll_to_widget(self, w, force=False):
            pass

        # add_system_message collaborators
        voice_enabled = False

        def render_markdown(self, text):
            return text

        def _rehome_thinking_dots(self):
            pass

        def is_elevated(self):
            return False

        def _greeting_signals(self):
            return set()

        def _session_intro_showing(self):
            from systema.ui.chat.input_dock import InputDockMixin
            return InputDockMixin._session_intro_showing(self)

        def _position_input_overlay(self):
            pass

    return Host()


@pytest.fixture
def many_refs(tmp_path):
    refs = [image_cache.store(_png(tmp_path / f"i{i}.png", 40 + i * 4, 30), n=i + 1)
            for i in range(6)]
    yield refs
    for r in refs:
        image_cache.discard(r, still_referenced=[r])


@pytest.mark.parametrize("count,origin", [(1, "user"), (3, "user"),
                                          (6, "user"), (1, "agent")])
def test_image_bubbles_build(widget_host, many_refs, count, origin):
    """Single, small collage, overflowing collage (+N tile) and the agent side."""
    assert widget_host.add_image_bubble(many_refs[:count], origin=origin) is not None
    assert widget_host.message_widgets


def test_a_detached_tile_still_renders(widget_host, many_refs):
    """You have to be able to SEE what you detached in order to put it back."""
    many_refs[1]["attached"] = False
    assert widget_host.add_image_bubble(many_refs[:3], origin="user") is not None


def test_attaching_an_image_dismisses_the_greeting(widget_host, tmp_path):
    """THE BUG THIS EXISTS FOR

    The greeting only went away on a sent message. Attaching an image puts a
    bubble straight into the transcript with no user message and no assistant
    turn, so it slid in UNDERNEATH a banner that just sat there — and the
    banner expands to fill the window, so the new bubble got whatever was left.
    """
    widget_host.add_greeting_banner()
    assert any(e.get("_intro") for e in widget_host.message_widgets)

    ref = image_cache.store(_png(tmp_path / "att.png"), n=1)
    try:
        widget_host.add_image_bubble([ref], origin="user")
        assert not any(e.get("_intro") for e in widget_host.message_widgets), \
            "an image bubble must clear the empty-session intro"
    finally:
        image_cache.discard(ref, still_referenced=[])


def test_every_row_enters_the_transcript_through_one_door():
    """A card added next year must not have to REMEMBER to dismiss the intro.
    Dismissal was wired per-add_* and the newest card type (image bubbles)
    silently missed it; this keeps that impossible."""
    from pathlib import Path

    ui = Path(__file__).resolve().parents[3] / "systema" / "ui"
    offenders = []
    for path in ui.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8",
                                                errors="replace").splitlines(), 1):
            if "chat_layout.insertWidget" not in line:
                continue
            if path.name == "bubbles.py":
                continue          # the helper itself lives here
            offenders.append(f"{path.name}:{i}")

    assert not offenders, (
        "these insert into the chat directly instead of via _insert_chat_row, "
        f"so they will not dismiss the greeting: {offenders}")


def test_a_live_tile_follows_its_ref_without_being_rebuilt(widget_host, tmp_path):
    """The mechanism that makes an in-place detach possible, on real widgets:
    repaint the bubble and the eye must agree with the ref — and it must be the
    SAME button object, because a repaint is not a rebuild."""
    from systema.ui.widgets.painted_icons import ContextEyeButton

    ref = image_cache.store(_png(tmp_path / "live.png"), n=1)
    try:
        widget_host.add_image_bubble([ref], origin="user")
        entry = widget_host.message_widgets[-1]
        eye = entry["widget"].findChild(ContextEyeButton)
        assert eye is not None and eye.isChecked()
        assert "Detach" in eye.toolTip()

        ref["attached"] = False
        entry["zoom_restyle"]()                    # the in-place repaint

        assert not eye.isChecked(), "the eye must read its state from the ref"
        assert eye is entry["widget"].findChild(ContextEyeButton), \
            "the tile was recreated — that is the rebuild this fix removed"
        assert "Re-attach" in eye.toolTip(), \
            "the tooltip must flip with the state, not stay as built"
    finally:
        image_cache.discard(ref, still_referenced=[])


def test_image_bubbles_register_a_zoom_restyle(widget_host, many_refs):
    """A card without one silently stops scaling with Ctrl+scroll."""
    widget_host.add_image_bubble(many_refs, origin="user")
    entry = widget_host.message_widgets[-1]
    assert callable(entry.get("zoom_restyle"))
    entry["zoom_restyle"]()          # must not raise


def test_a_thumbnail_is_sized_relative_to_the_bubble_not_fixed(widget_host):
    """A fixed 320px tile dwarfed the text around it. The size is a fraction of
    the bubble width, clamped — a picture is one element of a conversation, not
    the conversation."""
    single = widget_host._tile_side(single=True)
    tile = widget_host._tile_side(single=False)

    assert single <= widget_host._bubble_max_width() * 0.45
    assert tile < single, "a collage tile must be smaller than a lone image"
    assert single >= 150 and tile >= 90, "clamped so a narrow window stays legible"


def test_thumbnails_scale_with_ctrl_scroll_zoom(widget_host):
    """A card that ignores the zoom silently stops matching everything around
    it — the same rule as every other chat card's zoom_restyle."""
    widget_host.font_px = 13
    base_single = widget_host._tile_side(single=True)
    base_tile = widget_host._tile_side(single=False)

    widget_host.font_px = 20
    assert widget_host._tile_side(single=True) > base_single
    assert widget_host._tile_side(single=False) > base_tile


def _shown_pixmap(bubble):
    from PyQt6.QtWidgets import QLabel

    return [c for c in bubble.findChildren(QLabel)
            if c.pixmap() and not c.pixmap().isNull()][0].pixmap()


def test_a_portrait_image_is_not_letterboxed_into_a_square(widget_host, tmp_path):
    """Sizing the holder to a fixed square left a tall photo padded with dead
    space, which is what made the bubble look enormous. The tile hugs the
    scaled pixmap instead."""
    tall = image_cache.store(_png(tmp_path / "tall.png", w=12, h=20), n=1)
    bubble = widget_host.add_image_bubble([tall], origin="user")
    widget_host.message_widgets[-1]["zoom_restyle"]()

    shown = _shown_pixmap(bubble)
    assert shown.height() > shown.width(), "aspect ratio must be preserved"
    assert shown.height() <= widget_host._tile_side(single=True)
    assert bubble.sizeHint().width() < widget_host._bubble_max_width()

    image_cache.discard(tall, still_referenced=[])


def test_a_wordmark_strip_gets_extra_room(widget_host, tmp_path):
    """A ~5:1 logo fitted to the normal budget becomes a strip too short to
    read, with nowhere for its buttons to sit. Extreme aspects get a boost."""
    strip = image_cache.store(_png(tmp_path / "wordmark.png", w=48, h=10), n=1)
    bubble = widget_host.add_image_bubble([strip], origin="user")
    widget_host.message_widgets[-1]["zoom_restyle"]()

    shown = _shown_pixmap(bubble)
    budget = widget_host._tile_side(single=True)
    assert shown.width() > budget, "a strip should be allowed past the budget"
    assert shown.width() <= budget * widget_host.THIN_BOOST + 1
    assert bubble.sizeHint().width() <= widget_host._bubble_max_width()

    image_cache.discard(strip, still_referenced=[])


def test_every_thumbnail_keeps_its_buttons_on_screen(widget_host, tmp_path):
    """Both tiles in a collage need a detach AND a delete button, inside the
    tile — on a thin logo they were landing outside the holder and vanishing."""
    from PyQt6.QtWidgets import QWidget

    strip = image_cache.store(_png(tmp_path / "strip.png", w=48, h=10), n=1)
    square = image_cache.store(_png(tmp_path / "square.png", w=20, h=20), n=2)
    bubble = widget_host.add_image_bubble([strip, square], origin="user")
    widget_host.message_widgets[-1]["zoom_restyle"]()

    bars = [w for w in bubble.findChildren(QWidget)
            if w.objectName() == "imgTileBtns"]
    assert len(bars) == 2, "every image needs its own detach + delete pair"
    for bar in bars:
        holder = bar.parent()
        assert bar.x() >= 0 and bar.y() >= 0
        assert bar.x() + bar.width() <= holder.width()
        assert bar.y() + bar.height() <= holder.height()
        assert len(bar.findChildren(QWidget)) >= 2   # detach + delete

    for r in (strip, square):
        image_cache.discard(r, still_referenced=[])


def test_detach_uses_an_eye_not_a_repeat_arrow(widget_host, tmp_path):
    """Detaching is about whether the model can SEE the image — circular
    restart arrows said nothing about visibility."""
    from systema.ui.widgets.painted_icons import ContextEyeButton

    img = image_cache.store(_png(tmp_path / "eye.png", w=20, h=20), n=1)
    bubble = widget_host.add_image_bubble([img], origin="user")

    eyes = bubble.findChildren(ContextEyeButton)
    assert eyes, "the detach control should be the eye glyph"
    assert eyes[0].isChecked() is True, "attached = eye open"

    image_cache.discard(img, still_referenced=[])


def test_the_greeting_wraps_rather_than_clipping(widget_host):
    """The greeting used to be setWordWrap(False), which traded one bug for
    another: it could not collapse mid-phrase any more, but a long phrasing was
    CLIPPED at the window edge instead ("...Let's prove tha").

    Wrapping is safe here because the label is added to the column WITHOUT an
    alignment flag. That was the real cause of the old collapse — an alignment
    flag hands a widget its own sizeHint width, and a wrapping QLabel's
    sizeHint width is deliberately tiny. Full column width plus AlignCenter on
    the TEXT centres it without shrinking it.
    """
    from PyQt6.QtCore import Qt
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    entry = widget_host.message_widgets[-1]
    label = entry["content_wrapper"]
    assert label.wordWrap() is True
    assert label.alignment() & Qt.AlignmentFlag.AlignHCenter

    layout = label.parentWidget().layout()
    item = next(layout.itemAt(i) for i in range(layout.count())
                if layout.itemAt(i).widget() is label)
    assert int(item.alignment()) == 0, (
        "an alignment flag hands the label its own tiny wrapped sizeHint — "
        "this is what collapsed the greeting into a narrow ribbon")


def test_the_greeting_font_shrinks_to_fit_a_long_phrasing(widget_host):
    """Sizing off window width alone assumed an average-length greeting. The
    pool spans roughly 4x, so the long ones overflowed and were cut off."""
    from PyQt6.QtGui import QFont, QFontMetrics
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    entry = widget_host.message_widgets[-1]
    label = entry["content_wrapper"]
    row = label.parentWidget()
    row.resize(700, row.height() or 200)

    def size_for(text):
        label.setText(text)
        entry["zoom_restyle"]()
        for part in label.styleSheet().split(";"):
            if "font-size" in part:
                return int(part.split(":")[1].strip().removesuffix("px"))
        raise AssertionError("no font-size in the stylesheet")

    short = size_for("Evening.")
    long = size_for("Nothing good happens at this hour, Thirdy. "
                    "Let's prove that wrong, and then some, at length.")
    assert long < short, "a long greeting must be sized down, not clipped"
    assert long >= 20, "but never below title size — it wraps instead"

    # When the fit SUCCEEDS (it stopped above the floor) the text must actually
    # be inside the two-line budget. When it bottoms out at the floor it is
    # allowed to need a third line: wrapping at title size is the intended
    # failure, shrinking into body copy is not.
    probe = QFont(label.font())
    probe.setPixelSize(long)
    if long > 20:
        budget = int((row.width() - 48) * 1.9)
        assert QFontMetrics(probe).horizontalAdvance(label.text()) <= budget


def test_the_greeting_banner_builds_and_scales(widget_host):
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    entry = widget_host.message_widgets[-1]
    label = entry["content_wrapper"]

    assert "Thirdy" in label.text()
    assert callable(entry["zoom_restyle"])
    entry["zoom_restyle"]()
    assert "font-size" in label.styleSheet()


def test_the_intro_is_dismissed_by_the_first_real_content(widget_host):
    """The banner EXPANDS to fill an empty session, so leaving it in place once
    content arrives pushed the real conversation down and out of view.

    The intro group is the banner plus the startup notices (intro=True). They
    go together, and the first row that is NOT one of them clears them — a
    message, a card, or an attached image, whichever arrives first. It used to
    be "the first user message" only, which is how an attached image ended up
    rendering underneath a banner that stayed put.
    """
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    BubblesMixin.add_system_message(widget_host, "Administrator Privileges Granted",
                                    intro=True)
    assert sum(1 for e in widget_host.message_widgets if e.get("_intro")) == 2, \
        "the admin notice JOINS the intro rather than dismissing it"

    BubblesMixin.add_system_message(widget_host, "a normal note")   # real content

    assert not any(e.get("_intro") for e in widget_host.message_widgets)
    assert len(widget_host.message_widgets) == 1, "a normal note must survive"


def test_dismissing_twice_is_harmless(widget_host):
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    for _ in range(5):
        BubblesMixin.dismiss_session_intro(widget_host)      # must not raise
    assert widget_host.message_widgets == []


def test_an_assistant_turn_also_clears_the_intro(widget_host):
    """A background task agent can post into a FRESH session, with no user
    message first. The banner expands to fill the chat, so that message would
    be squeezed into whatever was left over. Opening an assistant turn clears
    the intro too, so content from any source gets the room it needs.
    """
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)
    assert any(e.get("_intro") for e in widget_host.message_widgets)

    group = BubblesMixin._ensure_ai_turn_group(widget_host)

    assert group and "row" in group, "the turn shell must still be built"
    assert not any(e.get("_intro") for e in widget_host.message_widgets)


def test_a_greeting_failure_can_never_block_a_message(widget_host):
    """Decoration must not be able to stop delivering content."""
    from systema.ui.chat.bubbles import BubblesMixin

    BubblesMixin.add_greeting_banner(widget_host)

    def _boom():
        raise RuntimeError("wrapped C++ object deleted")
    widget_host.dismiss_session_intro = _boom

    group = BubblesMixin._ensure_ai_turn_group(widget_host)
    assert group and "row" in group


def test_the_banner_never_clips_its_admin_subtitle(widget_host):
    """The row is Expanding and so is chat_layout's trailing stretch, so the
    two SPLIT the free space — anything taller than its share was cut off, and
    the wrapped elevated-privileges subtitle lost its second line."""
    from systema.ui.chat.bubbles import BubblesMixin

    widget_host.is_elevated = lambda: True
    row = BubblesMixin.add_greeting_banner(widget_host)
    widget_host.message_widgets[-1]["zoom_restyle"]()

    assert row.minimumHeight() >= row.sizeHint().height(), \
        "the banner must reserve room for everything it draws"


def test_the_admin_notice_lives_inside_the_banner(widget_host):
    """It used to be a second stacked grey line, which cluttered the opener and
    made the greeting read as off-centre."""
    from PyQt6.QtWidgets import QLabel

    from systema.ui.chat.bubbles import BubblesMixin

    widget_host.is_elevated = lambda: True
    row = BubblesMixin.add_greeting_banner(widget_host)

    texts = [c.text() for c in row.findChildren(QLabel) if c.text()]
    assert len(texts) == 2, "greeting plus its privileges subtitle, one widget"

    # The subtitle is one of the elevated-privileges phrasings (not every one
    # of them contains the word "privileges" — "Admin mode — I can reach
    # anything on this machine" is in the pool too).
    #
    # BOTH pools, deliberately: add_greeting_banner calls admin_note with
    # root=(sys.platform != "win32"), so this is "Running as root…" on Linux and
    # "Running with administrator privileges…" on Windows. Checking only
    # _ADMIN_LINES passed locally and failed in CI. This test is about the
    # notice living INSIDE the banner as one widget, not about which pool the
    # platform selects — re-encoding that branch here would just duplicate
    # production logic into the test.
    from systema.common.greeting import _ADMIN_LINES, _ROOT_LINES
    stems = [named.split("{name}")[0].strip(" ,")
             for named, _ in _ADMIN_LINES + _ROOT_LINES]
    assert any(any(t.startswith(stem) for stem in stems) for t in texts)

    # ...and only ONE intro entry, not a banner plus a separate system line.
    assert sum(1 for e in widget_host.message_widgets if e.get("_intro")) == 1


def test_admin_notes_vary_and_drop_the_name_cleanly():
    import random

    from systema.common.greeting import admin_note

    rng = random.Random(4)
    named = {admin_note("Thirdy", rng=rng) for _ in range(300)}
    anon = {admin_note("USER", rng=rng) for _ in range(300)}
    root = {admin_note("Thirdy", root=True, rng=rng) for _ in range(300)}

    assert len(named) >= 10 and len(root) >= 8, "the notice should vary too"
    # Registers are mixed on purpose: warm lines address you by name, clinical
    # ones ("Privilege level: administrator") deliberately address nobody.
    assert any("Thirdy" in n for n in named)
    assert all("USER" not in a for a in anon)

    # Whatever the register, EVERY line names the responsibility — that is what
    # this subtitle is for.
    duty = ("responsib", "discretion", "deliberate", "care", "careful",
            "measure twice", "safety net", "think before", "out of reach",
            "in effect", "use it well", "double-check")
    for pool in (named, root):
        for line in pool:
            assert any(w in line.lower() for w in duty), line


def test_greetings_are_punctuated():
    """An unpunctuated line reads as though it got cut off. Punctuation is
    added when a phrasing does not carry its own, so new lines stay easy to
    write."""
    from systema.common.greeting import _punctuate, all_phrasings

    assert _punctuate("Good evening, Thirdy") == "Good evening, Thirdy."
    assert _punctuate("Happy Friyay!") == "Happy Friyay!"
    assert _punctuate("Still up?") == "Still up?"
    for named, anon in all_phrasings():
        for line in (_punctuate(named), _punctuate(anon)):
            assert line[-1] in ".!?…:", line


def test_the_input_pill_floats_mid_window_only_during_the_intro(widget_host):
    from systema.ui.chat.bubbles import BubblesMixin

    assert widget_host._session_intro_showing() is False
    BubblesMixin.add_greeting_banner(widget_host)
    assert widget_host._session_intro_showing() is True
    BubblesMixin.dismiss_session_intro(widget_host)
    assert widget_host._session_intro_showing() is False


def test_the_banner_fills_the_session_without_being_shrinkable(widget_host):
    """MinimumExpanding, not Expanding.

    Expanding lets the layout shrink the row below its sizeHint when something
    else wants the space — which is how the wrapped subtitle kept losing its
    last line. MinimumExpanding makes the sizeHint a FLOOR and still lets the
    banner grow to fill an empty session.
    """
    from PyQt6.QtWidgets import QSizePolicy

    from systema.ui.chat.bubbles import BubblesMixin

    row = BubblesMixin.add_greeting_banner(widget_host)
    assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.MinimumExpanding


def test_the_banner_reports_enough_height_at_every_width(widget_host):
    """A height that is correct at 900px is wrong at 500px, and a static
    minimum cannot know that.

    This used to assert `minimumHeight() >= layout().heightForWidth(w)` after
    resizing the row, on the theory that the resize hook recomputed the floor.
    It did not: **resizeEvent never fires on a widget that has never been
    shown**, so the loop measured the ONE value set at construction and the
    assertion passed or failed on whether the randomly-chosen greeting happened
    to be short. It was testing nothing.

    The row now answers heightForWidth itself, which the parent layout asks for
    whether or not an event ever arrives — so THAT is what gets checked.
    """
    from systema.ui.chat.bubbles import BubblesMixin

    row = BubblesMixin.add_greeting_banner(widget_host)
    assert callable(row.on_resize)          # still there: it refits the font
    assert row.hasHeightForWidth()
    assert row.sizePolicy().hasHeightForWidth(), (
        "the layout only asks widgets whose size policy opts in")

    for width in (1200, 900, 620, 460, 360):
        needed = row.layout().heightForWidth(width)
        assert row.heightForWidth(width) >= needed, f"would clip at {width}px"


def test_a_long_greeting_needs_more_height_than_a_short_one(widget_host):
    """The point of the whole mechanism: a wrapped line is taller, and the row
    has to say so or the last line is cut off."""
    from systema.ui.chat.bubbles import BubblesMixin

    row = BubblesMixin.add_greeting_banner(widget_host)
    label = widget_host.message_widgets[-1]["content_wrapper"]

    label.setText("Evening.")
    widget_host.message_widgets[-1]["zoom_restyle"]()
    short = row.layout().heightForWidth(420)

    label.setText("Nothing good happens at this hour, Thirdy. Let's prove "
                  "that wrong, and then some, at considerable length.")
    widget_host.message_widgets[-1]["zoom_restyle"]()
    long = row.layout().heightForWidth(420)

    assert long > short
    assert row.heightForWidth(420) >= long


# ── pasting file paths ───────────────────────────────────────────────────────

def test_pasting_several_copied_images_offers_to_attach_them(qapp, tmp_path):
    """Windows "Copy as path" on a multi-selection yields QUOTED,
    newline-separated TEXT. That whole blob used to be tested as one path,
    failed os.path.exists, and got pasted raw — so copying two images silently
    inserted two paths instead of offering to attach them.
    """
    from systema.ui.widgets.inputs import MultiLineInput

    a = _png(tmp_path / "LOGO ONE.png")
    b = _png(tmp_path / "LOGO TWO.png")
    seen = {}

    class _Chat:
        def clean_file_path(self, p):
            return p.strip().strip('"')

        def should_quote_path(self, p):
            return " " in p

        def _handle_image_file_drop(self, p):
            seen["single"] = p

        def _handle_multiple_image_files_dialog(self, ps):
            seen["many"] = list(ps)

    box = MultiLineInput()
    box.get_chat_window = lambda: _Chat()

    pasted = f'"{a}"\n"{b}"'
    handled = box._route_dropped_paths(
        [ln.strip().strip('"') for ln in pasted.splitlines()])
    qapp.processEvents()          # the router defers via QTimer.singleShot(0)

    assert handled is True
    assert "many" in seen, "two copied images must reach the multi-image dialog"
    names = [os.path.basename(p) for p in seen["many"]]
    assert names == ["LOGO ONE.png", "LOGO TWO.png"]
    assert "single" not in seen


def test_pasting_one_copied_image_still_prompts(qapp, tmp_path):
    from systema.ui.widgets.inputs import MultiLineInput

    a = _png(tmp_path / "solo.png")
    seen = {}

    class _Chat:
        def clean_file_path(self, p):
            return p.strip().strip('"')

        def should_quote_path(self, p):
            return " " in p

        def _handle_image_file_drop(self, p):
            seen["single"] = p

        def _handle_multiple_image_files_dialog(self, ps):
            seen["many"] = list(ps)

    box = MultiLineInput()
    box.get_chat_window = lambda: _Chat()
    assert box._route_dropped_paths([str(a)]) is True
    qapp.processEvents()          # the router defers via QTimer.singleShot(0)
    assert "single" in seen and "many" not in seen


def test_non_image_paths_are_still_inserted_as_text(qapp, tmp_path):
    from systema.ui.widgets.inputs import MultiLineInput

    doc = tmp_path / "notes.txt"
    doc.write_text("x", encoding="utf-8")

    class _Chat:
        def clean_file_path(self, p):
            return p.strip().strip('"')

        def should_quote_path(self, p):
            return " " in p

        def _handle_image_file_drop(self, p):
            raise AssertionError("a .txt is not an image")

        def _handle_multiple_image_files_dialog(self, ps):
            raise AssertionError("a .txt is not an image")

    box = MultiLineInput()
    box.get_chat_window = lambda: _Chat()
    box._route_dropped_paths([str(doc)])
    assert "notes.txt" in box.toPlainText()


# ── the attachments manager ──────────────────────────────────────────────────

def test_the_attachments_dialog_lists_and_edits(qapp, tmp_path):
    """Builds the real dialog over a session that includes an attached image,
    a detached one, and one whose cached file has gone missing — the last is
    the case that must render a placeholder rather than crash a reload."""
    from PyQt6.QtWidgets import QWidget

    from systema.ui.dialogs.image_attachments_dialog import ImageAttachmentsDialog
    from systema.ui.theme import THEMES

    live = image_cache.store(_png(tmp_path / "live.png"), n=1)
    off = image_cache.store(_png(tmp_path / "off.png", w=6), n=2, origin="agent")
    off["attached"] = False
    gone = {"id": "img_3", "n": 3, "path": str(tmp_path / "never.png"),
            "name": "never.png", "origin": "user", "attached": True,
            "w": 10, "h": 10}
    hist = [{"role": "user", "content": "x", "_images": [live, off, gone]}]

    class Chat(QWidget, ImageBubblesMixin):
        def __init__(self):
            super().__init__()
            self.controller = types.SimpleNamespace(
                ai=types.SimpleNamespace(conversation_history=hist),
                _auto_save_session=lambda: None)

        def _t(self):
            return THEMES["obsidian_blue"]

        def render_loaded_messages(self):
            pass

        def _invalidate_token_estimate(self):
            pass

        def add_system_message(self, msg):
            pass

    chat = Chat()
    dlg = ImageAttachmentsDialog(chat)
    assert dlg.isHidden()                      # offscreen: never isVisible()
    assert [r["n"] for r in chat.session_image_refs()] == [1, 2, 3]

    dlg._one(1, False)
    assert [r["n"] for r in chat.session_image_refs() if r["attached"]] == [3]

    dlg._bulk(True)
    assert len([r for r in chat.session_image_refs() if r["attached"]]) == 3

    chat.delete_image(1, confirm=False)
    dlg._build()
    assert [r["n"] for r in chat.session_image_refs()] == [2, 3]

    image_cache.discard(off, still_referenced=[])
