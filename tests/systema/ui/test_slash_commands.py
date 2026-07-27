"""
tests/systema/ui/test_slash_commands.py

Slash commands typed into the chat input.

The invariants worth locking:
  * a command is a UI ACTION, not a turn — nothing reaches conversation_history
    and no message is sent;
  * it runs BEFORE the _send_allowed gate, because the commands you most need
    mid-turn (/shutdown, /restart) are exactly the ones the gate blocks;
  * /new refuses mid-turn and EXPLAINS, rather than discarding a reply that is
    still arriving;
  * read-only commands report into a popup, never a system message — a system
    message calls _end_ai_turn_group() and would split the merged assistant
    bubble you are reading;
  * Enter runs the line as typed, Tab accepts a completion.
"""
import types

import pytest

pytest.importorskip("PyQt6")

from systema.ui.chat import commands as C          # noqa: E402


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    ("/help", ("help", "")),
    ("/HELP", ("help", "")),
    ("/rename my session", ("rename", "my session")),
    ("/compact toolcalls", ("compact", "toolcalls")),
    ("hello /help", None),          # only a LEADING slash counts
    ("/", None),
    ("", None),
    ("what is 3/4", None),
])
def test_parse(line, expected):
    assert C.parse(line) == expected


def test_matches_is_prefix_based():
    assert [c.name for c in C.matches("re")] == ["rename", "restart",
                                                 "restore", "revert"]
    assert C.matches("zzz") == []


def test_every_command_is_declared_coherently():
    seen = set()
    for cmd in C.COMMANDS:
        assert cmd.name not in seen, f"duplicate command /{cmd.name}"
        seen.add(cmd.name)
        assert cmd.mid_turn in (C.ALWAYS, C.NOTIFY, C.IDLE)
        assert cmd.group in ("Session", "Context", "Tools", "Meta")
        assert callable(cmd.run)
        assert cmd.summary and not cmd.summary.endswith(".")


def test_every_command_target_exists():
    """THE bug this exists for: /settings and /shutdown shipped calling methods
    that do not exist ("Settings is not available"), because the target names
    were guessed rather than looked up. controller.ui IS the FloatingWindow —
    there is no controller.ui.floating_window, and open_settings lives on the
    window, not the controller.

    Walks the attribute each command reaches for and asserts it is real.
    """
    from systema.app.controller import AssistantController
    from systema.agents.skill_manager import SkillManager
    from systema.memory.session_manager import SessionManager
    from systema.ui.chat_window import ChatWindow
    from systema.ui.windows.floating_window import FloatingWindow

    hosts = {
        'controller': AssistantController,
        'floating': FloatingWindow,
        'chat': ChatWindow,
        'skills': SkillManager,
        'sessions': SessionManager,
    }
    # (command, host, attribute) — the call each one actually makes.
    targets = [
        ("new", 'controller', 'create_new_session'),
        ("rename", 'sessions', 'rename_session'),
        ("sessions", 'chat', 'toggle_sidebar'),
        # Both go through the ONE exit gate — not the restart_app/shutdown_app
        # aliases, which are now just thin routes into it.
        ("restart", 'controller', 'request_exit'),
        ("shutdown", 'controller', 'request_exit'),
        ("compact", 'controller', 'compact_all_toolcalls'),
        ("stopcompact", 'controller', 'stop_compaction'),
        ("restore", 'controller', 'restore_all_compacted'),
        ("clear", 'chat', '_clear_all_tool_outputs'),
        ("revert", 'controller', 'revert_cleared_outputs'),
        ("images", 'chat', '_open_image_attachments_dialog'),
        ("files", 'chat', '_open_session_files_dialog'),
        ("skills", 'skills', 'get_skills'),
        ("load", 'skills', 'load_skill'),
        ("unload", 'skills', 'unload_skill'),
        ("settings", 'floating', 'open_settings'),
        ("debug", 'floating', 'open_debug_window'),
        ("memory", 'chat', '_open_memory_window'),
        ("update", 'controller', 'open_update_window'),
    ]

    missing = [f"/{cmd} -> {host}.{attr}"
               for cmd, host, attr in targets
               if not hasattr(hosts[host], attr)]
    assert not missing, f"commands pointing at nothing: {missing}"

    # ...and every declared command is covered by the table above.
    covered = {c for c, _h, _a in targets} | {"tokens", "help"}
    uncovered = {c.name for c in C.COMMANDS} - covered
    assert not uncovered, f"no target asserted for: {sorted(uncovered)}"


def test_help_lists_every_command():
    text = C._cmd_help(None, "")
    for cmd in C.COMMANDS:
        assert f"/{cmd.name}" in text


# ── execution ────────────────────────────────────────────────────────────────

class _Chat:
    """Minimal host for the mixin's execution path."""

    def __init__(self, busy=False):
        self.results = []
        self.system_messages = []
        self.cleared = 0
        self.ran = []
        self.controller = types.SimpleNamespace(
            is_processing=busy,
            create_new_session=lambda: self.ran.append("new"),
            # The unified gate both /restart and /shutdown call. Returns True
            # (the exit is going ahead) so the command reports no cancellation.
            request_exit=lambda mode="shutdown", confirm=True: (
                self.ran.append(mode) or True),
        )

    # mixin under test
    try_run_command = C.SlashCommandsMixin.try_run_command
    _clear_input_after_command = C.SlashCommandsMixin._clear_input_after_command

    # collaborators
    def show_command_result(self, text):
        self.results.append(text)

    def hide_command_completer(self):
        pass

    def add_system_message(self, msg, intro=False):
        self.system_messages.append(msg)


def test_a_plain_message_is_not_a_command():
    chat = _Chat()
    assert chat.try_run_command("hello there") is False
    assert chat.results == []


# ── the dropdown never outlives the text that summoned it ────────────────────

class _TrackingChat(_Chat):
    def __init__(self, busy=False):
        super().__init__(busy)
        self.hidden = 0

    def hide_command_completer(self):
        self.hidden += 1


@pytest.mark.parametrize("line", ["/", "/reno", "/help", "hello there", ""])
def test_the_completer_is_hidden_whatever_the_line_turns_out_to_be(line):
    """The rogue-stay bug: `parse` returns None for a bare "/", so the old
    early return skipped the hide and the list sat there after the text was
    already sent."""
    chat = _TrackingChat()
    chat.try_run_command(line)
    assert chat.hidden >= 1, f"the dropdown survived {line!r}"


def test_a_lone_slash_explains_the_keys_instead_of_being_sent():
    """Enter instead of Tab is the accident that started this. Sending a bare
    slash to the assistant helps nobody."""
    chat = _TrackingChat()

    assert chat.try_run_command("/") is True, "must not reach the AI"
    msg = chat.results[0]
    assert "Tab" in msg and "Enter" in msg and "Esc" in msg


def test_an_unknown_command_points_at_tab():
    chat = _TrackingChat()
    chat.try_run_command("/reno")
    assert "Tab" in chat.results[0]


def test_the_completer_carries_a_visible_key_hint(qapp):
    """The only place Tab-vs-Enter is discoverable is while the list is up."""
    from PyQt6.QtWidgets import QWidget

    from systema.ui.chat.commands import CommandCompleter
    from systema.ui.theme import THEMES

    class _Host(QWidget):
        input_container = None

        def _t(self):
            return THEMES["obsidian_blue"]

        def _card_z(self, px):
            return int(px)

    comp = CommandCompleter(_Host())
    assert comp.repopulate("") is True
    hint = comp._hint.text()
    assert "Tab" in hint and "Enter" in hint and "Esc" in hint
    comp.hide()


def test_an_unknown_command_suggests_near_matches():
    chat = _Chat()
    assert chat.try_run_command("/reno") is True
    assert "Unknown command" in chat.results[0]
    assert "/rename" in chat.results[0]


def test_a_command_never_writes_to_the_transcript():
    """It is a UI action. A system message would split the merged assistant
    bubble; the popup is the whole reason read-only commands exist."""
    chat = _Chat()
    chat.try_run_command("/help")
    assert chat.results, "feedback should go to the popup"
    assert chat.system_messages == [], "nothing may enter the transcript"


def test_emergency_exits_run_mid_turn():
    chat = _Chat(busy=True)
    assert chat.try_run_command("/restart") is True
    assert chat.ran == ["restart"], "/restart must work while a turn is running"


def test_new_refuses_mid_turn_and_explains():
    """The one destructive command. It must NOT silently discard an in-flight
    reply — it says why, and what to do instead."""
    chat = _Chat(busy=True)

    assert chat.try_run_command("/new") is True

    assert chat.ran == [], "/new must not act while a response is arriving"
    msg = chat.results[0]
    assert "Cannot run /new" in msg
    assert "Reason:" in msg and "Suggestion:" in msg
    assert "Escape" in msg


def test_new_runs_normally_when_idle():
    chat = _Chat(busy=False)
    assert chat.try_run_command("/new") is True
    assert chat.ran == ["new"]


def test_idle_only_commands_are_refused_mid_turn():
    chat = _Chat(busy=True)
    assert chat.try_run_command("/restore") is True
    assert "unavailable while the assistant is working" in chat.results[0]


def test_a_failing_command_reports_instead_of_raising():
    """A broken command must not swallow the user's input path."""
    chat = _Chat()
    boom = C.Command("boom", "explodes", "Meta",
                     lambda c, a: (_ for _ in ()).throw(ValueError("nope")),
                     mid_turn=C.ALWAYS)
    C.BY_NAME["boom"] = boom
    try:
        assert chat.try_run_command("/boom") is True
        assert "failed" in chat.results[0] and "ValueError" in chat.results[0]
    finally:
        C.BY_NAME.pop("boom", None)


# ── the send path ────────────────────────────────────────────────────────────

def test_send_message_checks_commands_before_the_send_gate():
    """Ordering matters: /shutdown and /restart are most needed exactly when a
    turn is in flight, which is when _send_allowed is False."""
    import inspect

    from systema.ui.chat_window import ChatWindow

    src = inspect.getsource(ChatWindow.send_message)
    cmd_at = src.index("try_run_command")
    gate_at = src.index("_send_allowed")
    assert cmd_at < gate_at, "the command branch must precede the send gate"


def test_the_input_consumes_escape_only_while_the_popup_is_open():
    """Escape is bound to interrupt at the window level. Dismissing a dropdown
    must not stop the assistant."""
    import inspect

    from systema.ui.widgets.inputs import MultiLineInput

    src = inspect.getsource(MultiLineInput.keyPressEvent)
    assert "command_popup_visible" in src
    assert "Key_Escape" in src
    # Escape handling must sit INSIDE the popup-visible branch.
    assert src.index("command_popup_visible") < src.index("Key_Escape")


def test_the_result_popup_dismisses_itself(qapp):
    """Requiring a click to clear "Session renamed" is friction for no benefit.
    The delay scales with how much there is to read, so a one-liner goes
    quickly and /help stays long enough to actually read."""
    from PyQt6.QtWidgets import QWidget

    from systema.ui.chat.commands import CommandPopup
    from systema.ui.theme import THEMES

    class _Chat(QWidget):
        input_container = None

        def _t(self):
            return THEMES["obsidian_blue"]

        def _card_z(self, px):
            return int(px)

    popup = CommandPopup(_Chat())

    short = popup._hold_ms("Session renamed to 'x'.")
    long = popup._hold_ms(C._cmd_help(None, ""))

    assert short == popup.MIN_HOLD_MS, "a one-liner should not linger"
    assert long > short, "a screenful should stay up longer"
    assert long <= popup.MAX_HOLD_MS, "but never permanently"


def test_hovering_the_popup_holds_it_open(qapp):
    """Reading /help must not be interrupted by the countdown."""
    from PyQt6.QtWidgets import QWidget

    from systema.ui.chat.commands import CommandPopup
    from systema.ui.theme import THEMES

    class _Chat(QWidget):
        input_container = None

        def _t(self):
            return THEMES["obsidian_blue"]

        def _card_z(self, px):
            return int(px)

    popup = CommandPopup(_Chat())
    popup.show_text("something worth reading")
    assert popup._hold.isActive()

    popup.enterEvent(None)
    assert not popup._hold.isActive(), "hover must cancel the countdown"

    popup.leaveEvent(None)
    assert popup._hold.isActive(), "leaving restarts it"
    popup.hide()


def test_enter_is_never_repurposed_as_accept_completion():
    """A fully typed command must execute on Enter, not no-op into a
    completion."""
    import inspect

    from systema.ui.widgets.inputs import MultiLineInput

    src = inspect.getsource(MultiLineInput.keyPressEvent)
    enter_at = src.index("Key_Return")
    accept_at = src.index("accept_command_completion")
    assert accept_at < enter_at, "Tab handling comes first; Enter always sends"
