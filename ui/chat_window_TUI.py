"""
ui/chat_window_TUI.py
"""
from __future__ import annotations
import sys
import subprocess
import threading
import time
from pathlib import Path
import json
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Header, Footer, Static, Button, TextArea, Input
)
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual import work


# ── App-root anchor (immune to os.chdir) ──────────────────────────────────────
_APP_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT   = Path(__file__).resolve()          # this file itself


class ChatWindowTUI:
    """
    Drop-in replacement for ChatWindow.

    Spawns a system terminal running the Textual TUI and communicates with it
    over a local TCP socket.  Every method that FloatingWindow / the controller
    calls on chat_window is implemented here and forwarded as a JSON command.

    Supports Windows (cmd), macOS (Terminal.app), and common Linux terminals.
    """

    def __init__(self, controller):
        self.controller  = controller
        self._visible    = False
        self._server     = None
        self._conn       = None
        self._proc       = None
        self._port       = 0
        self._send_lock  = threading.Lock()
        self._stop_event = threading.Event()
        self.on_voice_playback_started = lambda: None


    # ── PyQt6-compatible public interface ─────────────────────────────────────
    #
    # Every method below mirrors what the controller / FloatingWindow calls on
    # the original ChatWindow.  Keep the signatures identical.

    # · Visibility ·············································

    _SIDEBAR_PAGE_SIZE = 10

    def _send_sessions_page(self, page: int, search: str):
        try:
            all_sessions = self.controller.get_session_list()
            if search:
                q = search.lower()
                all_sessions = [s for s in all_sessions
                                if q in (s.get("name") or "").lower()]
            total = len(all_sessions)
            pages = max(1, (total + self._SIDEBAR_PAGE_SIZE - 1) // self._SIDEBAR_PAGE_SIZE)
            page = max(0, min(page, pages - 1))
            chunk = all_sessions[page * self._SIDEBAR_PAGE_SIZE:(page + 1) * self._SIDEBAR_PAGE_SIZE]
            self._dispatch({
                "cmd": "sessions_data",
                "sessions": chunk,
                "page": page,
                "pages": pages,
                "current_session_id": self.controller.current_session_id,
            })
        except Exception as e:
            print(f"[ChatWindowTUI] _send_sessions_page error: {e}")

    def _send_skills(self):
        try:
            sm = self.controller.skill_manager
            all_skills = sm.get_skills()
            loaded = [s['name'] for s in all_skills if s['is_loaded']]
            available = [s['name'] for s in all_skills if not s['is_loaded']]
        except Exception as e:
            print(f"[ChatWindowTUI] _send_skills error: {e}")
            loaded, available = [], []
        self._dispatch({"cmd": "skills_data", "loaded": loaded, "available": available})

    def _send_instructions(self):
        try:
            text = self.controller.get_custom_instructions()
        except Exception:
            text = ""
        self._dispatch({"cmd": "instructions_data", "text": text})

    def _send_names(self):
        try:
            user_name = self.controller.get_user_name()
            asst_name = self.controller.get_assistant_name()
        except Exception:
            user_name, asst_name = "", ""
        self._dispatch({"cmd": "names_data", "user_name": user_name, "asst_name": asst_name})

    def _send_instructions(self):
        try:
            text = self.controller.get_custom_instructions()
        except Exception:
            text = ""
        self._dispatch({"cmd": "instructions_data", "text": text})

    def _send_names(self):
        try:
            user_name = self.controller.get_user_name()
            asst_name = self.controller.get_assistant_name()
        except Exception:
            user_name, asst_name = "", ""
        self._dispatch({"cmd": "names_data", "user_name": user_name, "asst_name": asst_name})

    def _send_memories(self):
        try:
            mm = self.controller.memory_manager
            memories = mm.get_all() if (mm and mm.is_ready) else []
        except Exception:
            memories = []
        self._dispatch({"cmd": "memories_data", "memories": memories})

    def _dispatch(self, cmd: dict):
        """Send a JSON command to the TUI subprocess (thread-safe)."""
        with self._send_lock:
            if self._conn is None:
                return
            try:
                self._conn.sendall((json.dumps(cmd) + "\n").encode())
            except Exception:
                self._conn = None

    def show(self):
        if self._visible:
            return
        self._visible = True
        if self._server is not None:
            self._stop_event.set()
            try:
                self._server.close()
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._conn = None
        self._stop_event.clear()
        self._start_server()
        self._launch_terminal()

    def hide(self):
        self._dispatch({"cmd": "quit"})
        self._visible = False
        self._stop_event.set()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._conn = None

    def _start_server(self):
        import socket as _socket
        self._server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._server.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._port = self._server.getsockname()[1]
        self._server.listen(1)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        try:
            self._server.settimeout(30)
            conn, _ = self._server.accept()
            self._conn = conn
            self._conn.settimeout(None)
            threading.Thread(target=self._recv_loop, daemon=True).start()
            time.sleep(0.3)  # give TUI a moment to finish mounting
            self.render_loaded_messages()
        except Exception as exc:
            if not self._stop_event.is_set():
                print(f"[ChatWindowTUI] Server accept failed: {exc}")

    def _recv_loop(self):
        buf = ""
        try:
            while True:
                chunk = self._conn.recv(4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            self._handle_tui_msg(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            self._visible = False
            self._conn = None

    def _handle_tui_msg(self, msg: dict):
        cmd = msg.get("cmd")
        if cmd == "send_message":
            text = msg.get("text", "").strip()
            if text:
                # ✅ Echo user message into TUI chat immediately (safe: _dispatch uses a lock)
                self.add_user_message(text)
                # ✅ Emit signal — thread-safe, guaranteed to run on Qt main thread
                self.controller.tui_send_signal.emit(text)
        elif cmd == "interrupt":
            # ✅ Same fix
            self.controller.tui_interrupt_signal.emit()
        elif cmd == "closed":
            self._visible = False
            self._conn = None
        elif cmd == "get_sessions":
            self._send_sessions_page(msg.get("page", 0), msg.get("search", ""))
        elif cmd == "load_session":
            sid = msg.get("session_id")
            if sid:
                self.controller.tui_load_session_signal.emit(sid)
                # TUI side will request the refresh via set_timer — no threading.Timer needed here
        elif cmd == "new_session":
            self.controller.tui_new_session_signal.emit()
        elif cmd == "get_skills":
            self._send_skills()
        elif cmd == "toggle_skill":
            skill_name = msg.get("skill_name")
            action = msg.get("action")
            if skill_name:
                try:
                    sm = self.controller.skill_manager
                    if action == "load":
                        sm.load_skill(skill_name)
                    else:
                        sm.unload_skill(skill_name)
                    # Do NOT call _send_skills() here — called from recv bg thread
                    # and sm.get_skills() silently fails, returning empty lists.
                    # The TUI requests a refresh via set_timer instead.
                except Exception as e:
                    print(f"[ChatWindowTUI] toggle_skill error: {e}")
        elif cmd == "open_memory":
            self.controller.tui_open_memory_signal.emit()
        elif cmd == "open_instructions":
            self._send_instructions()
        elif cmd == "open_names":
            self._send_names()
        elif cmd == "set_instructions":
            try:
                self.controller.set_custom_instructions(msg.get("text", ""))
            except Exception as e:
                print(f"[ChatWindowTUI] set_instructions error: {e}")
        elif cmd == "set_names":
            try:
                self.controller.set_user_name(msg.get("user_name", ""))
                self.controller.set_assistant_name(msg.get("asst_name", ""))
                self.render_loaded_messages()
            except Exception as e:
                print(f"[ChatWindowTUI] set_names error: {e}")

    def _launch_terminal(self):
        cmd_args = [sys.executable, str(_SCRIPT), "--tui", "--port", str(self._port)]
        if sys.platform == "win32":
            self._proc = subprocess.Popen(
                cmd_args,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        elif sys.platform == "darwin":
            joined = " ".join(f'"{a}"' for a in cmd_args)
            script = (
                f'tell application "Terminal"\n'
                f'    do script "{joined}"\n'
                f'    activate\nend tell'
            )
            self._proc = subprocess.Popen(["osascript", "-e", script])
        else:
            for term, extra in [
                ("gnome-terminal", ["--"]),
                ("xfce4-terminal", ["-e"]),
                ("konsole", ["-e"]),
                ("xterm", ["-e"]),
            ]:
                try:
                    self._proc = subprocess.Popen([term] + extra + cmd_args)
                    break
                except FileNotFoundError:
                    continue

    def isVisible(self) -> bool:
        """Return True if the TUI terminal has been launched and not yet hidden.

        Deliberately does NOT require _conn to be set — the terminal takes a
        moment to connect after launch, and checking _conn here caused show()
        to be called a second time during that window, spawning a duplicate
        terminal on a different port and producing WinError 10038 spam.
        """
        return self._visible

    def raise_(self):
        """No-op – terminal windows handle their own z-order."""

    def activateWindow(self):
        """No-op."""

    def closeEvent(self, event=None):
        self.hide()

    # · Thinking animation ·····································

    def show_thinking(self):
        self._dispatch({"cmd": "show_thinking"})

    def hide_thinking(self):
        self._dispatch({"cmd": "hide_thinking"})

    def start_thinking_animation(self):
        self.show_thinking()

    def stop_thinking_animation(self):
        self.hide_thinking()

    def update_thinking_animation(self):
        """No-op – animation is managed inside the Textual App."""

    # · Input state ·············································

    def set_input_enabled(self, enabled: bool):
        self._input_enabled = enabled
        self._dispatch({"cmd": "set_input_enabled", "enabled": enabled})

    # · Message display ·········································

    def add_user_message(self, text: str):
        if text.strip():
            self._dispatch({"cmd": "add_user", "text": text})

    def add_ai_message(self, text: str):
        if text.strip():
            self._dispatch({"cmd": "add_ai", "text": text})

    def show_ai_message(self, message: str):
        """FloatingWindow / controller calls this to display an AI reply."""
        self.add_ai_message(message)

    def add_system_message(self, text: str):
        self._dispatch({"cmd": "add_system", "text": text})

    def add_skill_card_message(self, skill_name: str, loaded: bool):
        emoji  = "⚡" if loaded else "📦"
        status = "loaded" if loaded else "unloaded"
        self.add_system_message(f"{emoji} Skill **{skill_name}** {status}.")

    # · Work-mode / response handling ···························

    def handle_ai_response(self, result: dict):
        """Called by FloatingWindow.handle_work_mode_update."""
        if not result.get("thinking") and result.get("response"):
            self.add_ai_message(result["response"])

    # · Session management ······································

    def clear_chat_silent(self):
        """Clear chat without notification (used when loading a session)."""
        self._dispatch({"cmd": "clear"})

    def render_loaded_messages(self, _param_catcher=None):
        """Replay session into the TUI chat."""
        try:
            self.clear_chat_silent()
            self.warn_loaded_skills_if_any()
            history = self.controller.ai.conversation_history
            for msg in history:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                content = self.controller.ai.tool_manager.strip_tool_calls(content)
                if content:
                    if role == "user":
                        self._dispatch({"cmd": "add_user", "text": content})
                    elif role == "assistant":
                        self._dispatch({"cmd": "add_ai", "text": content})
        except Exception as e:
            print(f"[ChatWindowTUI] _send_history error: {e}")

    def refresh_session_list(self):
        self._send_sessions_page(0, "")

    # · Skills warning ··········································

    def warn_loaded_skills_if_any(self):
        try:
            sm = self.controller.ai.skill_manager
            loaded = sm.get_loaded_skills()
            if not loaded:
                return
            names = list(loaded.keys())
            label = ", ".join(f"**{n}**" for n in names)
            self.add_system_message(
                f"⚡ Skill{'s' if len(names) > 1 else ''} {label} "
                f"{'are' if len(names) > 1 else 'is'} still loaded from a previous session. "
                f"This uses extra LLM context window space."
            )
        except Exception:
            pass

    # · Voice ···················································

    def update_voice_status(self, status: str):
        self._dispatch({"cmd": "voice_status", "status": status})

    def enable_voice(self):
        self.add_system_message("🎤 Voice mode **enabled**")

    def disable_voice(self):
        self.add_system_message("🔇 Voice mode **disabled**")

    # · Misc ····················································

    def log(self, msg: str):
        print(f"[ChatWindowTUI] {msg}")

    def interrupt_response(self):
        """Mirror of ChatWindow.interrupt_response — delegates to controller."""
        try:
            self.controller.interrupt_request()
        except Exception:
            pass

    def send_message(self):
        """Called if something tries to trigger send programmatically; no-op."""

    # voice_playback_signal compatibility:
    # The controller does:  self.voice_handler.on_playback_started = self._chat.on_voice_playback_started
    # We expose it as a plain callable attribute (set in __init__).
    # Nothing here needs emitting — TTS flows through the controller, not the UI.

class ExpandingTextArea(TextArea):
    """Multiline chat input.

    - Enter        → send message
    - Shift+Enter  → insert newline
    - Auto-expands vertically as lines are added (max 8 lines)
    """

    MIN_HEIGHT = 3   # 1 content line + 2 border rows
    MAX_HEIGHT = 10  # 8 content lines + 2 border rows

    def on_text_area_changed(self, event: "TextArea.Changed") -> None:
        line_count = self.document.line_count
        new_height = max(self.MIN_HEIGHT, min(line_count + 2, self.MAX_HEIGHT))
        self.styles.height = new_height

class ChatMessage(Static):
        """A styled chat bubble for one message."""


        DEFAULT_CSS = """
        ChatMessage {
            width: 100%;
            padding: 1 2;
            margin-bottom: 1;
        }
        ChatMessage.user {
            background: #1a2f4a;
            border-right: tall $accent;
            margin-left: 40;
            margin-right: 0;
            text-align: right;
        }
        ChatMessage.ai {
            background: #1a2b1a;
            border-left: tall green;
            margin-right: 12;
        }
        ChatMessage.system {
            background: #2a2710;
            border: solid #555533;
            margin: 0 4;
            text-style: italic;
        }
        ChatMessage.thinking {
            background: #1e1a2e;
            border-left: tall purple;
        }
        ChatMessage.skill {
            background: #1a1a2e;
            border-left: tall blue;
            margin: 0 4;
        }
        """

        ROLE_PREFIX = {
            "user":     ("You",        "bold cyan"),
            "ai":       (f"{json.load(open(f'{_APP_ROOT}/assistant_settings.json'))['assistant_name']}",  "bold green"),
            "system":   ("System",     "bold yellow"),
            "thinking": ("Thinking",   "bold purple"),
            "skill":    ("Skill",      "bold blue"),
        }

        def __init__(self, role: str, text: str, **kwargs):
            import re
            clean = text
            # Escape any raw [ so they don't clash with Rich markup we add below
            clean = clean.replace("[", "\\[")
            # Code blocks (``` ... ```) — do these first before inline code
            clean = re.sub(
                r'```(?:\w+)?\n?(.*?)```',
                lambda m: '[bold yellow on #1a1a2e]\n' + m.group(1).strip() + '\n[/bold yellow on #1a1a2e]',
                clean, flags=re.DOTALL
            )
            # Headings: ## Title
            clean = re.sub(
                r'^#{1,6}\s*(.+)$',
                r'[bold underline]\1[/bold underline]',
                clean, flags=re.MULTILINE
            )
            # Bold + italic: ***text***
            clean = re.sub(r'\*\*\*(.+?)\*\*\*', r'[bold italic]\1[/bold italic]', clean)
            # Bold: **text**
            clean = re.sub(r'\*\*(.+?)\*\*', r'[bold]\1[/bold]', clean)
            # Italic: *text*
            clean = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'[italic]\1[/italic]', clean)
            # Inline code: `code`
            clean = re.sub(r'`([^`]+)`', r'[bold yellow]\1[/bold yellow]', clean)
            # Bullet points: - item or * item at line start
            clean = re.sub(r'^[ \t]*[\-\*]\s+', '  • ', clean, flags=re.MULTILINE)
            clean = clean.strip()

            label, color = self.ROLE_PREFIX.get(role, (role, "bold white"))
            if role == "ai":
                try:
                    import json as _json
                    label = _json.load(
                        open(f'{_APP_ROOT}/assistant_settings.json')
                    ).get('assistant_name', label)
                except Exception:
                    pass
            content = f"[{color}]{label}[/{color}]\n{clean}"
            super().__init__(content, classes=role, markup=True, **kwargs)

        # ── Main App ───────────────────────────────────────────────────────────────


class SessionsModal(ModalScreen):
    """Full-screen session picker overlay."""

    def __init__(self, app_ref: "App"):
        super().__init__()
        self._app_ref = app_ref
        self._page = 0
        self._pages = 1

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="sessions-modal-outer"):
            with Horizontal(id="modal-header"):
                yield Static("📁  Sessions", id="modal-title")
                yield Button("✕", id="modal-close", classes="modal-close-btn")
            yield Input(placeholder="🔍 Search sessions…", id="modal-search")
            with VerticalScroll(id="modal-list"):
                yield Static("  Loading…", classes="sidebar-empty")
            with Horizontal(id="modal-page-nav"):
                yield Button("◀", id="modal-prev-page")
                yield Static("1 / 1", id="modal-page-label")
                yield Button("▶", id="modal-next-page")
            yield Button("＋ New Session", id="modal-new-session", variant="success")

    def on_mount(self) -> None:
        self.query_one("#modal-search", Input).focus()
        self._app_ref._emit({"cmd": "get_sessions", "page": 0, "search": ""})

    def refresh_data(self, sessions: list, page: int, pages: int, current_id: str) -> None:
        self._page = page
        self._pages = pages
        try:
            self.query_one("#modal-page-label", Static).update(f"{page + 1} / {pages}")
            ml = self.query_one("#modal-list", VerticalScroll)
            for child in list(ml.children):
                child.remove()
            if not sessions:
                ml.mount(Static("  (none)", classes="sidebar-empty"))
                return
            for s in sessions:
                sid = s.get("id", "")
                sname = (s.get("name") or sid[:16] or "Untitled").strip()
                label = f"● {sname}" if sid == current_id else f"  {sname}"
                ml.mount(Button(label, id=f"msess-{sid}", classes="session-btn"))
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "modal-search":
            self._app_ref._emit({"cmd": "get_sessions", "page": 0, "search": event.value})

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "modal-close":
            self.dismiss()
        elif bid == "modal-prev-page":
            if self._page > 0:
                search = self.query_one("#modal-search", Input).value
                self._app_ref._emit({"cmd": "get_sessions",
                                     "page": self._page - 1, "search": search})
        elif bid == "modal-next-page":
            if self._page < self._pages - 1:
                search = self.query_one("#modal-search", Input).value
                self._app_ref._emit({"cmd": "get_sessions",
                                     "page": self._page + 1, "search": search})
        elif bid == "modal-new-session":
            self._app_ref._emit({"cmd": "new_session"})
            self.dismiss()
        elif event.button.classes and "session-btn" in event.button.classes:
            sid = event.button.id.removeprefix("msess-")
            self._app_ref._emit({"cmd": "load_session", "session_id": sid})
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class SkillsModal(ModalScreen):
    """Full-screen skill toggler overlay."""

    def __init__(self, app_ref: "App"):
        super().__init__()
        self._app_ref = app_ref

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="skills-modal-outer"):
            with Horizontal(id="modal-header"):
                yield Static("⚡  Skills", id="modal-title")
                yield Button("✕", id="modal-close", classes="modal-close-btn")
            with VerticalScroll(id="modal-list"):
                yield Static("  Loading…", classes="sidebar-empty")

    def on_mount(self) -> None:
        self._app_ref._emit({"cmd": "get_skills"})

    def refresh_data(self, loaded: list, available: list) -> None:
        try:
            ml = self.query_one("#modal-list", VerticalScroll)
            for child in list(ml.children):
                child.remove()
            if not loaded and not available:
                ml.mount(Static("  (none)", classes="sidebar-empty"))
                return
            if loaded:
                ml.mount(Static("  ── Loaded  (click to unload) ──",
                                classes="modal-section-label"))
            for name in loaded:
                ml.mount(Button(f"⚡ {name}", id=f"skill-{name}",
                                classes="skill-btn skill-loaded"))
            unloaded = [n for n in available if n not in loaded]
            if unloaded:
                ml.mount(Static("  ── Available  (click to load) ──",
                                classes="modal-section-label"))
            for name in unloaded:
                ml.mount(Button(f"· {name}", id=f"skill-{name}",
                                classes="skill-btn skill-available"))
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "modal-close":
            self.dismiss()
        elif event.button.classes and "skill-btn" in event.button.classes:
            skill_name = event.button.id.removeprefix("skill-")
            action = "unload" if "skill-loaded" in event.button.classes else "load"
            self._app_ref._emit({"cmd": "toggle_skill",
                                 "skill_name": skill_name, "action": action})
            self.set_timer(0.5, lambda: self._app_ref._emit({"cmd": "get_skills"}))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class InstructionsModal(ModalScreen):
    """Edit custom instructions overlay."""

    def __init__(self, app_ref: "App"):
        super().__init__()
        self._app_ref = app_ref

    def compose(self) -> ComposeResult:
        with Vertical(id="instructions-modal-outer"):
            with Horizontal(id="modal-header"):
                yield Static("📋  Custom Instructions", id="modal-title")
                yield Button("✕", id="modal-close", classes="modal-close-btn")
            yield Static("Added to every prompt:", classes="modal-section-label")
            yield TextArea("", id="instructions-text", language=None,
                           show_line_numbers=False, soft_wrap=True)
            with Horizontal(id="instructions-btn-row"):
                yield Button("💾 Save", id="instructions-save-btn", variant="success")
                yield Button("Cancel", id="instructions-cancel-btn")

    def on_mount(self) -> None:
        self._app_ref._emit({"cmd": "open_instructions"})

    def refresh_data(self, text: str) -> None:
        try:
            self.query_one("#instructions-text", TextArea).load_text(text)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid in ("modal-close", "instructions-cancel-btn"):
            self.dismiss()
        elif bid == "instructions-save-btn":
            try:
                text = self.query_one("#instructions-text", TextArea).text
                self._app_ref._emit({"cmd": "set_instructions", "text": text})
            except Exception:
                pass
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class NamesModal(ModalScreen):
    """Edit user/assistant names overlay."""

    def __init__(self, app_ref: "App"):
        super().__init__()
        self._app_ref = app_ref

    def compose(self) -> ComposeResult:
        with Vertical(id="names-modal-outer"):
            with Horizontal(id="modal-header"):
                yield Static("✏️   Names", id="modal-title")
                yield Button("✕", id="modal-close", classes="modal-close-btn")
            yield Static("Your name:", classes="modal-section-label")
            yield Input(placeholder="Your name…", id="names-user-input")
            yield Static("Assistant name:", classes="modal-section-label")
            yield Input(placeholder="Assistant name…", id="names-asst-input")
            with Horizontal(id="names-btn-row"):
                yield Button("💾 Save", id="names-save-btn", variant="success")
                yield Button("Cancel", id="names-cancel-btn")

    def on_mount(self) -> None:
        self._app_ref._emit({"cmd": "open_names"})
        self.query_one("#names-user-input", Input).focus()

    def refresh_data(self, user_name: str, asst_name: str) -> None:
        try:
            self.query_one("#names-user-input", Input).value = user_name
            self.query_one("#names-asst-input", Input).value = asst_name
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid in ("modal-close", "names-cancel-btn"):
            self.dismiss()
        elif bid == "names-save-btn":
            try:
                user_name = self.query_one("#names-user-input", Input).value.strip()
                asst_name = self.query_one("#names-asst-input", Input).value.strip()
                self._app_ref._emit({"cmd": "set_names",
                                     "user_name": user_name, "asst_name": asst_name})
            except Exception:
                pass
            self.dismiss()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss()


class _SystemaAuxilium(App):
    """Systema Auxilium – Terminal Chat Interface"""

    CSS = """
            Screen {
                background: #0d1117;
            }
            #custom-header {
                height: 3;
                background: #161b22;
                border-bottom: solid #30363d;
            }
            #header-title {
                width: 1fr;
                content-align: center middle;
                color: #58a6ff;
                text-style: bold;
                text-align: center;
            }
            #header-clock {
                width: 20;
                content-align: right middle;
                color: #8b949e;
                padding: 0 2;
                text-align: right;
            }
            .sidebar-toggle {
                width: 5;
                height: 3;
                background: #161b22;
                border: none;
                color: #8b949e;
            }
            .sidebar-toggle:hover {
                background: #1f2937;
                color: #e6edf3;
            }
            Footer {
                background: #161b22;
            }
            #main-layout {
                height: 1fr;
            }
            #chat-col {
                width: 1fr;
                height: 1fr;
            }
            #sidebar {
                width: 22;
                height: 1fr;
                background: #161b22;
                border-right: solid #30363d;
                padding: 1 0;
                display: none;
            }
            .top-btn {
                width: 100%;
                height: 3;
                background: #161b22;
                border: none;
                color: #8b949e;
                padding: 0 2;
            }
            .top-btn:hover {
                background: #1f2937;
                color: #e6edf3;
            }
            #new-session-btn {
                width: 100%;
                height: 3;
                margin-top: 1;
            }
            #chat-scroll {
                width: 1fr;
                border: solid #30363d;
                margin: 0 0 1 0;
                scrollbar-color: #30363d #0d1117;
            }
            #status-bar {
                height: 1;
                background: #161b22;
                color: #8b949e;
                padding: 0 1;
                content-align: center middle;
            }
            #input-row {
                height: auto;
                min-height: 3;
            }
            #msg-input {
                width: 1fr;
                height: 3;
                background: #161b22;
                border: tall #30363d;
                color: #e6edf3;
                padding: 0 1;
            }
            #msg-input:focus {
                border: tall #58a6ff;
            }
            #msg-input .text-area--cursor {
                background: #58a6ff;
                color: #0d1117;
            }
            #msg-input .text-area--selection {
                background: #1f3b5a;
            }
            #send-btn {
                width: 12;
                background: #238636;
                border: tall #2ea043;
                color: white;
                margin-left: 1;
            }
            #send-btn:hover {
                background: #2ea043;
            }
            #interrupt-btn {
                width: 15;
                background: #8b1a1a;
                border: tall #c0392b;
                color: white;
                margin-left: 1;
                display: none;
            }
            #interrupt-btn:hover {
                background: #c0392b;
            }

            /* ── modal shared styles ─────────────────────────────────────────── */

            SessionsModal, SkillsModal {
                align: center middle;
            }
            #sessions-modal-outer {
                width: 70;
                height: 36;
                background: #161b22;
                border: thick #30363d;
                padding: 1 2;
            }
            #skills-modal-outer {
                width: 58;
                height: 28;
                background: #161b22;
                border: thick #30363d;
                padding: 1 2;
            }
            #modal-header {
                height: 3;
                margin-bottom: 1;
            }
            #modal-title {
                width: 1fr;
                color: #58a6ff;
                text-style: bold;
                content-align: left middle;
            }
            .modal-close-btn {
                width: 5;
                height: 3;
                background: #3a1a1a;
                border: tall #c0392b;
                color: white;
            }
            .modal-close-btn:hover { background: #c0392b; }
            #modal-search {
                height: 3;
                background: #0d1117;
                border: tall #30363d;
                color: #e6edf3;
                margin-bottom: 1;
            }
            #modal-list {
                height: 1fr;
                border: solid #30363d;
                margin-bottom: 1;
            }
            #modal-page-nav {
                height: 3;
                margin-bottom: 1;
            }
            #modal-page-label {
                width: 1fr;
                content-align: center middle;
                color: #8b949e;
            }
            #modal-page-nav Button {
                width: 5;
                min-width: 5;
                background: #161b22;
                border: none;
            }
            #modal-new-session { width: 100%; }
            .session-btn {
                width: 100%;
                height: 1;
                background: transparent;
                border: none;
                color: #8b949e;
                padding: 0 1;
                margin: 0;
                min-height: 1;
            }
            .session-btn:hover {
                background: #1f2937;
                color: #e6edf3;
            }
            .skill-btn {
                width: 100%;
                height: 1;
                min-height: 1;
                background: transparent;
                border: none;
                padding: 0 1;
                margin: 0;
            }
            .skill-btn.skill-loaded    { color: #f0c040; }
            .skill-btn.skill-available { color: #8b949e; }
            .skill-btn:hover { background: #1f2937; color: #e6edf3; }
            .sidebar-empty       { color: #444; padding: 0 1; }
            .modal-section-label { color: #8b949e; padding: 0 1; text-style: italic; }

            InstructionsModal, NamesModal {
                align: center middle;
            }
            #instructions-modal-outer {
                width: 72;
                height: 30;
                background: #161b22;
                border: thick #30363d;
                padding: 1 2;
            }
            #instructions-text {
                height: 1fr;
                background: #0d1117;
                border: tall #30363d;
                color: #e6edf3;
                margin-bottom: 1;
            }
            #instructions-btn-row {
                height: 3;
            }
            #instructions-btn-row Button { margin-right: 1; }
            #names-modal-outer {
                width: 52;
                height: 22;
                background: #161b22;
                border: thick #30363d;
                padding: 1 2;
            }
            #names-user-input, #names-asst-input {
                height: 3;
                background: #0d1117;
                border: tall #30363d;
                color: #e6edf3;
                margin-bottom: 1;
            }
            #names-btn-row {
                height: 3;
                margin-top: 1;
            }
            #names-btn-row Button { margin-right: 1; }
            """

    TITLE = "Systema Auxilium"

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Close", show=False, priority=True),
        Binding("escape", "try_interrupt", "Interrupt", show=True, priority=True),
        Binding("ctrl+e", "send_message", "Send", show=True, priority=True),
        Binding("pageup", "scroll_up_chat", "Scroll ↑", show=True, priority=True),
        Binding("pagedown", "scroll_down_chat", "Scroll ↓", show=True, priority=True),
    ]

    def __init__(self, port: int, **kwargs):
        super().__init__(**kwargs)
        self._port = port
        self._sock = None
        self._thinking_widget: ChatMessage | None = None
        self._thinking_timer = None
        self._dot_count = 0
        self._is_enabled = True
        self._sessions_modal: SessionsModal | None = None
        self._skills_modal: SkillsModal | None = None
        self._instructions_modal: InstructionsModal | None = None
        self._names_modal: NamesModal | None = None
    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-header"):
            yield Button("☰", id="sidebar-toggle-btn", classes="sidebar-toggle")
            yield Static("Systema Auxilium", id="header-title")
            yield Static("", id="header-clock")
        with Horizontal(id="main-layout"):
            with Vertical(id="sidebar"):
                yield Button("📁 Sessions", id="open-sessions-btn", classes="top-btn")
                yield Button("⚡ Skills", id="open-skills-btn", classes="top-btn")
                yield Button("🧠 Memory", id="memory-btn", classes="top-btn")
                yield Button("📋 Instructions", id="instructions-btn", classes="top-btn")
                yield Button("✏️  Names", id="names-btn", classes="top-btn")
                yield Button("＋ New Session", id="new-session-btn", variant="success")
            with Vertical(id="chat-col"):
                with VerticalScroll(id="chat-scroll", can_focus=False):
                    pass
        yield Static(
            "Ready  ·  Ctrl+E to send  ·  Enter for newline  ·  Esc to interrupt",
            id="status-bar"
        )
        with Horizontal(id="input-row"):
            yield ExpandingTextArea(
                "", id="msg-input", language=None, theme="monokai",
                show_line_numbers=False, soft_wrap=True,
            )
            yield Button("  Send ", id="send-btn", variant="success")
            yield Button("⚡ Interrupt", id="interrupt-btn", variant="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#msg-input", ExpandingTextArea).focus()
        self._start_reader()
        self.set_interval(1.0, self._tick_clock)

    def _tick_clock(self) -> None:
        from datetime import datetime
        now = datetime.now().strftime("%I:%M:%S %p")
        try:
            self.query_one("#header-clock").update(now)
        except Exception:
            pass

    def _populate_sessions(self, sessions: list, page: int, pages: int, current_id: str) -> None:
        """Route incoming session data to the open modal (if any)."""
        if self._sessions_modal is not None:
            self._sessions_modal.refresh_data(sessions, page, pages, current_id)

    def _populate_skills(self, loaded: list, available: list) -> None:
        """Route incoming skill data to the open modal (if any)."""
        if self._skills_modal is not None:
            self._skills_modal.refresh_data(loaded, available)

    def _populate_instructions(self, text: str) -> None:
        if self._instructions_modal is not None:
            self._instructions_modal.refresh_data(text)

    def _populate_names(self, user_name: str, asst_name: str) -> None:
        if self._names_modal is not None:
            self._names_modal.refresh_data(user_name, asst_name)

    @work(thread=True)
    def _start_reader(self):
        import socket as _socket, time as _time
        for _ in range(20):
            try:
                s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                s.connect(("127.0.0.1", self._port))
                self._sock = s
                break
            except ConnectionRefusedError:
                _time.sleep(0.5)

        if self._sock is None:
            self.call_from_thread(self.action_quit_app)
            return

        buf = ""
        try:
            while True:
                data = self._sock.recv(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            msg = json.loads(line)
                            self.call_from_thread(self._handle_cmd, msg)
                        except Exception:
                            pass
        except Exception:
            pass
        self.call_from_thread(self.action_quit_app)

    # ── Command handler ────────────────────────────────────────────────────

    def _handle_cmd(self, msg: dict) -> None:
        cmd = msg.get("cmd")

        if cmd == "add_user":
            self._append(ChatMessage("user", msg.get("text", "")))
        elif cmd == "add_ai":
            self._remove_thinking()
            self._append(ChatMessage("ai", msg.get("text", "")))
        elif cmd == "add_system":
            self._append(ChatMessage("system", msg.get("text", "")))
        elif cmd == "skill_card":
            name = msg.get("name", "")
            loaded = msg.get("loaded", True)
            emoji = "⚡" if loaded else "📦"
            text = f"{emoji} Skill '{name}' {'loaded' if loaded else 'unloaded'}."
            self._append(ChatMessage("skill", text))
        elif cmd == "show_thinking":
            self._show_thinking()
            self._set_enabled(False)
        elif cmd == "hide_thinking":
            self._remove_thinking()
            self._set_enabled(True)
        elif cmd == "set_input_enabled":
            self._set_enabled(msg.get("enabled", True))
        elif cmd == "voice_status":
            status = msg.get("status", "")
            icons = {
                "listening": "🔴 Listening…",
                "processing": "🟡 Processing…",
                "speaking": "🟢 Speaking…",
                "inactive": "",
                "Ready": "🎤 Ready",
            }
            self.query_one("#status-bar").update(icons.get(status, status))
        elif cmd == "quit":
            self.action_quit_app()
        elif cmd == "clear":
            self._clear_chat()
        elif cmd == "sessions_data":
            self._populate_sessions(
                msg.get("sessions", []),
                msg.get("page", 0),
                msg.get("pages", 1),
                msg.get("current_session_id", ""),
            )
        elif cmd == "skills_data":
            self._populate_skills(msg.get("loaded", []), msg.get("available", []))
        elif cmd == "instructions_data":
            self._populate_instructions(msg.get("text", ""))
        elif cmd == "names_data":
            self._populate_names(msg.get("user_name", ""), msg.get("asst_name", ""))

    # ── Chat helpers ───────────────────────────────────────────────────────

    def _clear_chat(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        for child in list(scroll.children):
            child.remove()

    def _append(self, widget: ChatMessage) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.mount(widget)
        self.call_after_refresh(lambda: scroll.scroll_end(animate=False))

    def _show_thinking(self) -> None:
        if self._thinking_widget is not None:
            return
        self._dot_count = 0
        self._thinking_widget = ChatMessage("thinking", "Working…")
        self._append(self._thinking_widget)
        self._thinking_timer = self.set_interval(
            0.45, self._tick_thinking, name="thinking"
        )

    def _tick_thinking(self) -> None:
        self._dot_count = (self._dot_count + 1) % 4
        dots = "·" * self._dot_count or " "
        if self._thinking_widget:
            self._thinking_widget.update(
                f"[bold purple]Thinking[/bold purple]\nWorking{dots}"
            )

    def _remove_thinking(self) -> None:
        if self._thinking_timer:
            try:
                self._thinking_timer.stop()
            except Exception:
                pass
            self._thinking_timer = None
        if self._thinking_widget:
            try:
                self._thinking_widget.remove()
            except Exception:
                pass
            self._thinking_widget = None

    def _set_enabled(self, enabled: bool) -> None:
        self._is_enabled = enabled
        inp = self.query_one("#msg-input", ExpandingTextArea)
        send = self.query_one("#send-btn", Button)
        intr = self.query_one("#interrupt-btn", Button)
        inp.disabled = not enabled
        send.disabled = not enabled
        if enabled:
            intr.styles.display = "none"
            send.styles.display = "block"
            self.query_one("#status-bar").update(
                "Ready  ·  Ctrl+E to send  ·  Enter for newline  ·  Esc to interrupt"
            )
            inp.focus()
        else:
            intr.styles.display = "block"
            send.styles.display = "none"
            self.query_one("#status-bar").update(
                "AI is working…  ·  Esc / click ⚡ Interrupt to cancel"
            )

    # ── User actions ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "send-btn":
            self._do_send()
        elif bid == "sidebar-toggle-btn":
            self.action_toggle_sidebar()
        elif bid == "interrupt-btn":
            self._emit({"cmd": "interrupt"})
        elif bid == "open-sessions-btn":
            modal = SessionsModal(self)
            self._sessions_modal = modal
            self.push_screen(modal,
                             callback=lambda _: setattr(self, "_sessions_modal", None))
        elif bid == "open-skills-btn":
            modal = SkillsModal(self)
            self._skills_modal = modal
            self.push_screen(modal,
                             callback=lambda _: setattr(self, "_skills_modal", None))
        elif bid == "new-session-btn":
            self._emit({"cmd": "new_session"})
        elif bid == "memory-btn":
            self._emit({"cmd": "open_memory"})
        elif bid == "instructions-btn":
            modal = InstructionsModal(self)
            self._instructions_modal = modal
            self.push_screen(modal,
                             callback=lambda _: setattr(self, "_instructions_modal", None))
        elif bid == "names-btn":
            modal = NamesModal(self)
            self._names_modal = modal
            self.push_screen(modal,
                             callback=lambda _: setattr(self, "_names_modal", None))

    def _do_send(self) -> None:
        inp = self.query_one("#msg-input", ExpandingTextArea)
        text = inp.text.strip()
        if not text or not self._is_enabled:
            return
        inp.load_text("")
        inp.styles.height = inp.MIN_HEIGHT
        self._emit({"cmd": "send_message", "text": text})

    def _emit(self, data: dict) -> None:
        """Send a JSON command back to the main process via socket."""
        if self._sock is None:
            return
        try:
            self._sock.sendall((json.dumps(data) + "\n").encode())
        except Exception:
            pass

    # ── Bindings ───────────────────────────────────────────────────────────

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_quit_app(self) -> None:
        self._emit({"cmd": "closed"})
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self.exit()

    def action_try_interrupt(self) -> None:
        if not self._is_enabled:
            self._emit({"cmd": "interrupt"})

    def action_send_message(self) -> None:
        self._do_send()

    def action_scroll_up_chat(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_relative(y=-10, animate=True)

    def action_scroll_down_chat(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_relative(y=10, animate=True)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Subprocess entry-point
    # ═══════════════════════════════════════════════════════════════════════════════


def _run_tui(port: int):
    _SystemaAuxilium(port=port).run()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--tui", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.tui and args.port:
        _run_tui(args.port)