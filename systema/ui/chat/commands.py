"""
systema/ui/chat/commands.py

Slash commands typed straight into the chat input.

Running one is a UI ACTION, NOT A CONVERSATION TURN: no user bubble, no AI
turn, nothing appended to conversation_history, no tokens spent. That is the
whole point — `/tokens` must not itself cost tokens.

THREE RULES THAT SHAPE EVERYTHING HERE
  1. Read-only commands report into a floating POPUP, never into the
     transcript. A system message would call _end_ai_turn_group() and split the
     merged assistant bubble in half, so asking "what does this cost?" would
     visibly damage the reply you were reading.
  2. Every command is a thin wrapper over an existing entry point. Nothing here
     reimplements session, compaction or skill behaviour — it routes to the
     same controller/chat methods the ⋯ menu uses, so the two can never drift.
  3. Mid-turn policy is per command:
       ALWAYS  — safe any time (read-only, and the emergency exits).
       NOTIFY  — refuses while a turn is in flight and says why (/new).
       IDLE    — silently unavailable mid-turn.

KEY HANDLING
Enter always means "run the line as typed"; Tab (or Right) accepts a
completion. Enter is already overloaded in the input (bare Enter sends,
Shift+Enter newlines), and making it ALSO accept a completion would mean a
fast typist who has already typed the whole command gets a no-op instead of
execution. Escape closes the popup and is consumed WHILE OPEN ONLY — Escape is
bound to interrupt at the window level, so an unconsumed Escape would stop the
assistant just because you dismissed a dropdown.
"""

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PyQt6.QtWidgets import (QFrame, QLabel, QListWidget, QListWidgetItem,
                             QVBoxLayout)

from systema.common.logger import _make_logger

log = _make_logger("SlashCommands")

# Mid-turn policy
ALWAYS = "always"     # safe while the assistant is working
NOTIFY = "notify"     # refuses mid-turn, and explains
IDLE = "idle"         # simply unavailable mid-turn


class Command:
    """One slash command. `run(chat, arg)` returns feedback text, or None."""

    __slots__ = ("name", "args", "summary", "group", "run", "mid_turn",
                 "readonly")

    def __init__(self, name, summary, group, run, *, args="",
                 mid_turn=IDLE, readonly=False):
        self.name = name
        self.args = args
        self.summary = summary
        self.group = group
        self.run = run
        self.mid_turn = mid_turn
        self.readonly = readonly

    @property
    def usage(self) -> str:
        return f"/{self.name} {self.args}".strip()


# ── command implementations ──────────────────────────────────────────────────
# Each takes (chat, arg) and returns text to show, or None for "no feedback".

def _cmd_help(chat, arg):
    lines = []
    for group in ("Session", "Context", "Tools", "Meta"):
        members = [c for c in COMMANDS if c.group == group]
        if not members:
            continue
        lines.append(group.upper())
        for c in members:
            lines.append(f"   {c.usage:<22} {c.summary}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _cmd_new(chat, arg):
    chat.controller.create_new_session()
    return None                      # the greeting banner is the feedback


def _cmd_rename(chat, arg):
    if not arg:
        return "Usage: /rename <name>"
    try:
        sid = chat.controller.current_session_id
        chat.controller.session_manager.rename_session(sid, arg)
        chat.refresh_session_list()
        return f"Session renamed to '{arg}'."
    except Exception as e:
        return f"Could not rename: {e}"


def _cmd_sessions(chat, arg):
    try:
        chat.toggle_sidebar()
    except Exception:
        pass
    return None


def _cmd_shutdown(chat, arg):
    # ONE exit pipeline for every caller (menu, tray, command, updater), so
    # the "a response is still generating" prompt is asked once and honoured.
    if not chat.controller.request_exit("shutdown"):
        return "Shutdown cancelled."
    return None


def _cmd_restart(chat, arg):
    if not chat.controller.request_exit("restart"):
        return "Restart cancelled."
    return None


def _cmd_ask(chat, arg):
    """Arm the interview: the AI asks before it acts, for the NEXT turn only."""
    ai = chat.controller.ai
    if not getattr(ai, 'include_ask_user', True):
        return ("The Q&A tool is switched off. Turn on \"Inject the Q&A tool\" in "
                "Settings - System - Optional System Prompt Sections first.")
    ai.interview_first = True
    return ("Armed. The AI will ask before it acts on your next message. "
            "(One turn only - use the standing setting to make it permanent.)")


def _cmd_compact(chat, arg):
    chat.controller.compact_all_toolcalls()
    return None                      # it reports its own progress


def _cmd_stop_compact(chat, arg):
    chat.controller.stop_compaction()
    return None


def _cmd_restore(chat, arg):
    chat.controller.restore_all_compacted()
    return None


def _cmd_clear_outputs(chat, arg):
    chat._clear_all_tool_outputs()
    return None


def _cmd_revert_outputs(chat, arg):
    chat.controller.revert_cleared_outputs()
    return None


def _cmd_tokens(chat, arg):
    from systema.common.token_est import (estimate_history_tokens,
                                          estimate_refs_tokens, estimate_tokens)
    ai = chat.controller.ai
    history = ai.conversation_history
    try:
        sys_tokens = estimate_tokens(ai._get_effective_system_prompt())
    except Exception:
        sys_tokens = estimate_tokens(getattr(ai, 'system_prompt', '') or '')
    hist_tokens = estimate_history_tokens(history)

    from systema.common import image_refs
    refs = image_refs.all_refs(history)
    img_tokens = estimate_refs_tokens(refs)
    live = sum(1 for r in refs if r.get('attached'))

    return (f"Estimated cost of the next request\n\n"
            f"   system prompt     ~{sys_tokens:,}\n"
            f"   conversation      ~{hist_tokens:,}\n"
            f"   of which images   ~{img_tokens:,}  ({live} of {len(refs)} attached)\n"
            f"   ------------------------------\n"
            f"   total             ~{sys_tokens + hist_tokens:,}")


def _cmd_images(chat, arg):
    chat._open_image_attachments_dialog()
    return None


def _cmd_files(chat, arg):
    chat._open_session_files_dialog()
    return None


def _cmd_skills(chat, arg):
    try:
        skills = chat.controller.skill_manager.get_skills()
    except Exception as e:
        return f"Could not read skills: {e}"
    loaded = [s['name'] for s in skills if s.get('is_loaded')]
    avail = [s['name'] for s in skills if not s.get('is_loaded')]
    out = ["LOADED"] + ([f"   {n}" for n in loaded] or ["   (none)"])
    out += ["", "AVAILABLE"] + ([f"   {n}" for n in avail] or ["   (none)"])
    return "\n".join(out)


def _cmd_load(chat, arg):
    if not arg:
        return "Usage: /load <skill>"
    # load_skill returns (ok, message) — it does not raise on a bad name.
    ok, msg = chat.controller.skill_manager.load_skill(arg)
    return msg or (f"Loaded skill '{arg}'." if ok
                   else f"Could not load '{arg}'.")


def _cmd_unload(chat, arg):
    if not arg:
        return "Usage: /unload <skill>"
    ok, msg = chat.controller.skill_manager.unload_skill(arg)
    return msg or (f"Unloaded skill '{arg}'." if ok
                   else f"Could not unload '{arg}'.")


# ── window openers ───────────────────────────────────────────────────────────
# Each names its real host. These were guessed once and shipped broken
# (/settings and /shutdown both looked for methods that do not exist), which is
# why test_every_command_target_exists now walks all of them.

def _cmd_settings(chat, arg):
    win = getattr(chat.controller, 'ui', None)      # the FloatingWindow
    if win is None or not hasattr(win, 'open_settings'):
        return "Settings is not available."
    win.open_settings()
    return None


def _cmd_debug(chat, arg):
    win = getattr(chat.controller, 'ui', None)
    if win is None or not hasattr(win, 'open_debug_window'):
        return "The debug window is not available."
    win.open_debug_window()
    return None


def _cmd_logs(chat, arg):
    """Open the log browser for this session.

    Deliberately a window, not printed text: the useful answer is usually "which
    files does this session span, and what does the end of that one say", and a
    chat bubble is the wrong shape for both.
    """
    if not hasattr(chat, '_open_logs_window'):
        return "The logs window is not available."
    chat._open_logs_window()
    return None


def _cmd_memory(chat, arg):
    # Lives on the chat window itself, not the controller.
    if not hasattr(chat, '_open_memory_window'):
        return "The memory window is not available."
    chat._open_memory_window()
    return None


def _cmd_update(chat, arg):
    fn = getattr(chat.controller, 'open_update_window', None)
    if not callable(fn):
        return "The updater is not available."
    fn()
    return None


COMMANDS = [
    # ── Session ──────────────────────────────────────────────────────────────
    Command("new", "Start a fresh session", "Session", _cmd_new,
            mid_turn=NOTIFY),
    Command("rename", "Rename this session", "Session", _cmd_rename,
            args="<name>", mid_turn=ALWAYS, readonly=True),
    Command("sessions", "Show the session list", "Session", _cmd_sessions,
            mid_turn=ALWAYS, readonly=True),
    Command("restart", "Restart the app", "Session", _cmd_restart,
            mid_turn=ALWAYS),
    Command("shutdown", "Close the app", "Session", _cmd_shutdown,
            mid_turn=ALWAYS),
    # ── Context and tokens ───────────────────────────────────────────────────
    Command("compact", "Summarise chunky toolcall outputs in the background",
            "Context", _cmd_compact, args="toolcalls"),
    Command("stopcompact", "Stop the running compaction job", "Context",
            _cmd_stop_compact, mid_turn=ALWAYS),
    Command("restore", "Undo compaction, restoring original outputs",
            "Context", _cmd_restore),
    Command("clear", "Replace every toolcall output with a stub", "Context",
            _cmd_clear_outputs, args="toolcalls"),
    Command("revert", "Undo /clear, restoring original outputs", "Context",
            _cmd_revert_outputs),
    Command("tokens", "What the next request costs", "Context", _cmd_tokens,
            mid_turn=ALWAYS, readonly=True),
    Command("images", "Manage attached images", "Context", _cmd_images,
            mid_turn=ALWAYS, readonly=True),
    # ── Tools and skills ─────────────────────────────────────────────────────
    Command("skills", "List loaded and available skills", "Tools", _cmd_skills,
            mid_turn=ALWAYS, readonly=True),
    Command("load", "Load a skill", "Tools", _cmd_load, args="<skill>"),
    Command("unload", "Unload a skill", "Tools", _cmd_unload, args="<skill>"),
    Command("files", "Files touched this session", "Tools", _cmd_files,
            mid_turn=ALWAYS, readonly=True),
    # ── Meta ─────────────────────────────────────────────────────────────────
    Command("ask", "Make the AI interview you before it acts (next turn)",
            "Context", _cmd_ask, mid_turn=IDLE),
    Command("help", "Show this list", "Meta", _cmd_help,
            mid_turn=ALWAYS, readonly=True),
    Command("settings", "Open Settings", "Meta", _cmd_settings,
            mid_turn=ALWAYS, readonly=True),
    Command("memory", "Open the Memory window", "Meta", _cmd_memory,
            mid_turn=ALWAYS, readonly=True),
    Command("debug", "Open the Debug window", "Meta", _cmd_debug,
            mid_turn=ALWAYS, readonly=True),
    Command("logs", "Browse this session's log files", "Meta", _cmd_logs,
            mid_turn=ALWAYS, readonly=True),
    Command("update", "Check for updates", "Meta", _cmd_update,
            mid_turn=ALWAYS, readonly=True),
]

BY_NAME = {c.name: c for c in COMMANDS}


def parse(text: str):
    """('name', 'argument') for a slash line, or None when it is not one.

    Only a line whose FIRST character is '/' counts, so a message that merely
    mentions a path or a date is never swallowed.
    """
    if not text or not text.startswith("/"):
        return None
    body = text[1:].strip()
    if not body:
        return None
    head, _, arg = body.partition(" ")
    return head.lower(), arg.strip()


def matches(prefix: str):
    """Commands whose name starts with `prefix` (no leading slash)."""
    prefix = (prefix or "").lower()
    return [c for c in COMMANDS if c.name.startswith(prefix)]


def near(name: str, limit: int = 4):
    """Best guesses for a MISTYPED command.

    Prefix matching alone is useless here — "/reno" shares no prefix with
    "rename", which is obviously what was meant. This falls back to shorter and
    shorter prefixes, then to substring containment, so a typo in the tail
    still finds its command.
    """
    name = (name or "").lower()
    if not name:
        return []
    for cut in range(len(name), 1, -1):
        found = matches(name[:cut])
        if found:
            return found[:limit]
    return [c for c in COMMANDS if name[:2] in c.name][:limit]


# ── feedback popup ───────────────────────────────────────────────────────────

class CommandPopup(QFrame):
    """Floating panel for command output.

    Read-only commands report HERE rather than into the transcript. A system
    message calls _end_ai_turn_group(), which splits the merged assistant
    bubble — so asking "/tokens" while reading a reply would visibly cut that
    reply in two. A popup leaves the conversation untouched.

    It DISMISSES ITSELF. Requiring a click to clear a one-line confirmation
    ("Session renamed") is friction for no benefit — but /help is a screenful
    you are actually reading, so the delay scales with how much there is to
    read, and hovering holds it open indefinitely.

    Click or Escape still dismiss immediately, and opening a new one replaces
    the old.
    """

    FADE_MS = 320
    MIN_HOLD_MS = 2600          # a one-liner is legible well inside this
    MAX_HOLD_MS = 16000         # /help is long, but not permanent
    CHARS_PER_SEC = 28          # unhurried reading pace

    def __init__(self, chat):
        super().__init__(chat)
        self._chat = chat
        self.setObjectName("cmdPopup")
        self.setWindowFlags(Qt.WindowType.ToolTip)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(False)      # output is pre-formatted columns
        lay.addWidget(self._label)

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._begin_fade)
        self._fade = None

    # ── lifetime ─────────────────────────────────────────────────────────────

    def _hold_ms(self, text: str) -> int:
        """How long to stay up, from how much there is to read."""
        reading = (len(text or "") / self.CHARS_PER_SEC) * 1000
        return int(max(self.MIN_HOLD_MS, min(self.MAX_HOLD_MS, reading)))

    def _stop_fade(self):
        if self._fade is not None:
            try:
                self._fade.stop()
            except RuntimeError:
                pass
            self._fade = None

    def _begin_fade(self):
        """Fade out, then hide. A hard hide would blink away mid-glance."""
        self._stop_fade()
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(self.FADE_MS)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.hide)
        self._fade = anim
        anim.start()

    def enterEvent(self, event):
        # Reading it: hold indefinitely and undo any fade already under way.
        self._hold.stop()
        self._stop_fade()
        self.setWindowOpacity(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        # Cursor left — restart a short countdown rather than vanishing.
        self._hold.start(self.MIN_HOLD_MS)
        super().leaveEvent(event)

    def hide(self):
        self._hold.stop()
        self._stop_fade()
        super().hide()

    def show_text(self, text: str):
        t = self._chat._t()
        z = self._chat._card_z
        self.setStyleSheet(
            f"QFrame#cmdPopup {{ background: {t['elevated']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}")
        self._label.setStyleSheet(
            f"QLabel {{ color: #E6EDF3; font-size: {z(11)}px;"
            f" font-family: Consolas, monospace; background: transparent; }}")
        self._label.setText(text)
        self.adjustSize()
        self._anchor()

        self._stop_fade()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        self._hold.start(self._hold_ms(text))

    def _anchor(self):
        """Sit just above the input pill, centred on it. The pill is a floating
        overlay whose geometry moves (it centres itself for an empty session),
        so this reads its LIVE rect rather than assuming the bottom edge."""
        try:
            ic = self._chat.input_container
            top_left = ic.mapToGlobal(QPoint(0, 0))
            x = top_left.x() + (ic.width() - self.width()) // 2
            y = top_left.y() - self.height() - 8
            self.move(max(0, x), max(0, y))
        except (AttributeError, RuntimeError):
            pass

    def mousePressEvent(self, event):
        self.hide()
        super().mousePressEvent(event)


# ── autocomplete ─────────────────────────────────────────────────────────────

class CommandCompleter(QFrame):
    """The '/' dropdown: a list plus a permanent key hint.

    A framed panel rather than a bare QListWidget so the hint can live INSIDE
    it. Enter and Tab do different things here, and the only place that is
    discoverable is the moment the list is on screen — so the footer says so
    every time rather than relying on anyone having read a docstring.
    """

    ROW_H = 22
    MAX_ROWS = 8
    WIDTH = 440

    def __init__(self, chat):
        super().__init__(chat)
        self._chat = chat
        self.setObjectName("cmdCompleter")
        self.setWindowFlags(Qt.WindowType.ToolTip)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 3)
        lay.setSpacing(3)

        self._list = QListWidget(self)
        self._list.setObjectName("cmdCompleterList")
        self._list.setUniformItemSizes(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        lay.addWidget(self._list)

        self._hint = QLabel(self)
        self._hint.setObjectName("cmdCompleterHint")
        self._hint.setTextFormat(Qt.TextFormat.PlainText)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._hint)

        self._commands = []

    def repopulate(self, prefix: str) -> bool:
        """Fill from `prefix`. Returns True when there is anything to show."""
        self._commands = matches(prefix)
        if not self._commands:
            self.hide()
            return False
        t = self._chat._t()
        z = self._chat._card_z
        self.setStyleSheet(f"""
            QFrame#cmdCompleter {{
                background: {t['elevated']}; border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QListWidget#cmdCompleterList {{
                background: transparent; border: none; outline: none;
                color: #E6EDF3; font-size: {z(11)}px;
            }}
            QListWidget#cmdCompleterList::item {{
                padding: 3px 8px; border-radius: 5px;
            }}
            QListWidget#cmdCompleterList::item:selected {{
                background: {t['accent']}; color: #0D1117;
            }}
            QLabel#cmdCompleterHint {{
                color: #6E7681; font-size: {z(9)}px;
                background: transparent; padding: 2px 0 1px 0;
                border-top: 1px solid {t['border']};
            }}
        """)
        self._hint.setText("Tab to complete   ·   Enter runs the line as typed"
                           "   ·   Esc closes")

        self._list.clear()
        for c in self._commands:
            QListWidgetItem(f"{c.usage:<24}{c.summary}", self._list)
        self._list.setCurrentRow(0)

        rows = min(len(self._commands), self.MAX_ROWS)
        self._list.setFixedHeight(rows * self.ROW_H + 6)
        self.setFixedWidth(self.WIDTH)
        self.adjustSize()
        self._anchor()
        self.show()
        self.raise_()
        return True

    def _anchor(self):
        try:
            ic = self._chat.input_container
            top_left = ic.mapToGlobal(QPoint(0, 0))
            x = top_left.x() + (ic.width() - self.width()) // 2
            y = top_left.y() - self.height() - 6
            self.move(max(0, x), max(0, y))
        except (AttributeError, RuntimeError):
            pass

    def current_command(self):
        row = self._list.currentRow()
        if 0 <= row < len(self._commands):
            return self._commands[row]
        return None

    def move_selection(self, delta: int):
        if not self._commands:
            return
        row = (self._list.currentRow() + delta) % len(self._commands)
        self._list.setCurrentRow(row)


# ── the mixin ────────────────────────────────────────────────────────────────

class SlashCommandsMixin:
    """Slash-command handling for ChatWindow."""

    def _cmd_popup(self) -> CommandPopup:
        popup = getattr(self, '_command_popup', None)
        if popup is None:
            popup = CommandPopup(self)
            self._command_popup = popup
        return popup

    def _cmd_completer(self) -> CommandCompleter:
        comp = getattr(self, '_command_completer', None)
        if comp is None:
            comp = CommandCompleter(self)
            self._command_completer = comp
        return comp

    def command_popup_visible(self) -> bool:
        comp = getattr(self, '_command_completer', None)
        try:
            return bool(comp is not None and comp.isVisible())
        except RuntimeError:
            return False

    # ── autocomplete ─────────────────────────────────────────────────────────

    def refresh_command_completer(self):
        """Called on every keystroke. Shows the dropdown only while the line is
        a bare '/word' with no argument yet — once you start typing arguments
        the list is noise."""
        try:
            text = self.input_field.toPlainText()
        except (AttributeError, RuntimeError):
            return
        if not text.startswith("/") or " " in text or "\n" in text:
            self.hide_command_completer()
            return
        self._cmd_completer().repopulate(text[1:])

    def hide_command_completer(self):
        comp = getattr(self, '_command_completer', None)
        if comp is not None:
            try:
                comp.hide()
            except RuntimeError:
                pass

    def accept_command_completion(self) -> bool:
        """Tab / Right. True when a completion was taken."""
        if not self.command_popup_visible():
            return False
        cmd = self._cmd_completer().current_command()
        if cmd is None:
            return False
        try:
            self.input_field.text_input.setPlainText(
                f"/{cmd.name}{' ' if cmd.args else ''}")
            cursor = self.input_field.text_input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.input_field.text_input.setTextCursor(cursor)
        except (AttributeError, RuntimeError):
            return False
        self.hide_command_completer()
        return True

    def move_command_selection(self, delta: int) -> bool:
        if not self.command_popup_visible():
            return False
        self._cmd_completer().move_selection(delta)
        return True

    # ── execution ────────────────────────────────────────────────────────────

    def show_command_result(self, text: str):
        self._cmd_popup().show_text(text)

    def try_run_command(self, text: str) -> bool:
        """Run `text` as a slash command. True when it WAS one (whether or not
        it succeeded), so the caller must not also send it as a message.

        Never raises: a broken command must not swallow the user's input path.
        """
        # Hide FIRST, unconditionally. Bailing out before this is what left the
        # dropdown stranded on screen after Enter: `parse` returns None for a
        # bare "/", so the old early return skipped the hide and the list
        # outlived the text that summoned it.
        self.hide_command_completer()

        # A lone "/" is somebody who opened the list and hit Enter instead of
        # Tab. Sending a bare slash to the assistant helps nobody — say what
        # the key actually is.
        if text.strip() == "/":
            self.show_command_result(
                "Nothing to run yet.\n\n"
                "Tab     completes the highlighted command\n"
                "Enter   runs the line exactly as typed\n"
                "Esc     closes the list\n\n"
                "Type /help for everything available.")
            self._clear_input_after_command()
            return True

        parsed = parse(text)
        if parsed is None:
            return False
        name, arg = parsed

        cmd = BY_NAME.get(name)
        if cmd is None:
            guesses = near(name)
            hint = ("\n\nDid you mean:  "
                    + ",  ".join(f"/{c.name}" for c in guesses)) if guesses else ""
            self.show_command_result(
                f"Unknown command: /{name}{hint}\n\n"
                f"Press Tab to complete from the list, not Enter.\n"
                f"Type /help for everything available.")
            self._clear_input_after_command()
            return True

        busy = bool(getattr(self.controller, 'is_processing', False))
        if busy and cmd.mid_turn != ALWAYS:
            if cmd.mid_turn == NOTIFY:
                # /new is the destructive one: refuse and EXPLAIN, rather than
                # silently discarding a reply that is still arriving.
                self.show_command_result(
                    f"Cannot run /{cmd.name} right now.\n\n"
                    f"Reason:      a response is still being generated.\n"
                    f"Suggestion:  press Escape to interrupt it, then try again.")
            else:
                self.show_command_result(
                    f"/{cmd.name} is unavailable while the assistant is working.\n\n"
                    f"Suggestion:  press Escape to interrupt, or wait for it to finish.")
            self._clear_input_after_command()
            return True

        try:
            feedback = cmd.run(self, arg)
        except Exception as e:                              # noqa: BLE001
            log.error(f"[try_run_command] /{name} failed: {e}", exc_info=True)
            feedback = f"/{name} failed: {type(e).__name__}: {e}"
        if feedback:
            self.show_command_result(feedback)
        self._clear_input_after_command()
        return True

    def _clear_input_after_command(self):
        self.hide_command_completer()
        try:
            self.input_field.clear()
        except (AttributeError, RuntimeError):
            pass
