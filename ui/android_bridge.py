"""
ui/android_bridge.py
Android Bridge — mirrors the app over LAN to the Android remote controller.
Same JSON RPC protocol as ChatWindowTUI, but listens for a phone instead of
spawning a terminal.
"""
from __future__ import annotations
import json
import socket as _socket
import threading

DEFAULT_PORT = 1111


class AndroidBridge:
    """
    Accepts one TCP connection from the Android app over Wi-Fi LAN.
    FloatingWindow holds an instance of this and broadcasts every UI update
    to it in parallel with the regular ChatWindow.
    """

    def __init__(self, controller):
        self.controller  = controller
        self._visible    = False
        self._server     = None
        self._conn       = None
        self._port       = DEFAULT_PORT
        self._local_ip   = ""
        self._send_lock  = threading.Lock()
        self._stop_event = threading.Event()
        self._pending_manual_response_cb = None

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def show(self):
        """Start listening for an Android connection."""
        if self._visible:
            return
        self._visible = True
        self._stop_event.clear()
        self._conn = None
        self._start_server()

    def hide(self):
        """Stop server and close any active connection."""
        self._dispatch({"cmd": "quit"})
        self._visible = False
        self._stop_event.set()
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        self._conn = None

    def _start_server(self):
        self._server = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._server.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", self._port))  # LAN, not just localhost
        # Detect the LAN IP for display in the button label
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            self._local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            self._local_ip = "unknown"
        self._server.listen(1)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        print(f"[AndroidBridge] Listening on {self._local_ip}:{self._port}")

    def _accept_loop(self):
        try:
            self._server.settimeout(None)  # block until phone connects
            conn, addr = self._server.accept()
            print(f"[AndroidBridge] Phone connected from {addr}")
            self._conn = conn
            self._conn.settimeout(None)
            threading.Thread(target=self._recv_loop, daemon=True).start()
            self.render_loaded_messages()   # replay current session into phone
        except Exception as exc:
            if not self._stop_event.is_set():
                print(f"[AndroidBridge] accept failed: {exc}")

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
                            self._handle_msg(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            self._conn = None
            print("[AndroidBridge] Phone disconnected")
            if not self._stop_event.is_set():
                threading.Thread(target=self._accept_loop, daemon=True).start()

    def _handle_msg(self, msg: dict):
        """Incoming commands from Android."""
        cmd = msg.get("cmd")
        if cmd == "send_message":
            text = msg.get("text", "").strip()
            if text:
                self.add_user_message(text)
                self.controller.bridge_user_bubble_signal.emit(text)
                self.controller.bridge_send_signal.emit(text)
        elif cmd == "interrupt":
            self.controller.interrupt_request()
        elif cmd == "closed":
            self._conn = None
        elif cmd == "code_approval_result":
            approved = msg.get("approved", False)
            try:
                self.controller.ai.tool_manager.approval_signal.close_approval_dialog.emit(
                    bool(approved), msg.get("modified_code", "")
                )
            except Exception as e:
                print(f"[AndroidBridge] code_approval_result error: {e}")
        elif cmd == "manual_response_result":
            text = msg.get("text", "").strip()
            cb = self._pending_manual_response_cb
            self._pending_manual_response_cb = None
            if cb:
                cb(text if text else None)
        elif cmd == "get_sessions":
            self._send_sessions_page(msg.get("page", 0), msg.get("search", ""))
        elif cmd == "load_session":
            sid = msg.get("session_id")
            if sid:
                self.controller.bridge_load_session_signal.emit(sid)
        elif cmd == "new_session":
            self.controller.bridge_new_session_signal.emit()
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
                except Exception as e:
                    print(f"[AndroidBridge] toggle_skill error: {e}")
        elif cmd == "open_memory":
                    self._send_memories()
        elif cmd == "open_instructions":
            self._send_instructions()
        elif cmd == "set_instructions":
            try:
                self.controller.set_custom_instructions(msg.get("text", ""))
            except Exception as e:
                print(f"[AndroidBridge] set_instructions error: {e}")
        elif cmd == "open_names":
            self._send_names()
        elif cmd == "set_names":
            try:
                self.controller.set_user_name(msg.get("user_name", ""))
                self.controller.set_assistant_name(msg.get("asst_name", ""))
                self.render_loaded_messages()
            except Exception as e:
                print(f"[AndroidBridge] set_names error: {e}")

    def _dispatch(self, cmd: dict):
        """Send a JSON command to Android (thread-safe). Identical to ChatWindowTUI._dispatch."""
        with self._send_lock:
            if self._conn is None:
                return
            try:
                self._conn.sendall((json.dumps(cmd) + "\n").encode())
            except Exception:
                self._conn = None

    # ── Session / Skill helpers (mirrors ChatWindowTUI) ───────────────────────

    _SIDEBAR_PAGE_SIZE = 10

    def _send_sessions_page(self, page: int, search: str):
        try:
            all_sessions = self.controller.get_session_list()
            if search:
                q = search.lower()
                all_sessions = [s for s in all_sessions if q in (s.get("name") or "").lower()]
            total  = len(all_sessions)
            pages  = max(1, (total + self._SIDEBAR_PAGE_SIZE - 1) // self._SIDEBAR_PAGE_SIZE)
            page   = max(0, min(page, pages - 1))
            chunk  = all_sessions[page * self._SIDEBAR_PAGE_SIZE:(page + 1) * self._SIDEBAR_PAGE_SIZE]
            self._dispatch({
                "cmd": "sessions_data",
                "sessions": chunk,
                "page": page,
                "pages": pages,
                "current_session_id": self.controller.current_session_id,
            })
        except Exception as e:
            print(f"[AndroidBridge] _send_sessions_page error: {e}")

    def _send_skills(self):
        try:
            sm = self.controller.skill_manager
            all_skills = sm.get_skills()
            loaded    = [s['name'] for s in all_skills if s['is_loaded']]
            available = [s['name'] for s in all_skills if not s['is_loaded']]
        except Exception:
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

    def _send_memories(self):
        try:
            mm = self.controller.memory_manager
            memories = mm.get_all() if (mm and mm.is_ready) else []
        except Exception:
            memories = []
        self._dispatch({"cmd": "memories_data", "memories": memories})

    # ── UI mirror interface (same signatures as ChatWindowTUI) ────────────────

    def isVisible(self) -> bool:
        return self._visible

    def get_connection_info(self) -> str:
        """Returns 'IP:PORT' string for display on the floating window button."""
        return f"{self._local_ip}:{self._port}"

    def add_user_message(self, text: str):
        if text.strip():
            self._dispatch({"cmd": "add_user", "text": text})

    def add_ai_message(self, text: str):
        if text.strip():
            self._dispatch({"cmd": "add_ai", "text": text})

    def show_ai_message(self, message: str):
        self.add_ai_message(message)

    def add_system_message(self, text: str):
        self._dispatch({"cmd": "add_system", "text": text})

    def show_thinking(self):
        self._dispatch({"cmd": "show_thinking"})

    def hide_thinking(self):
        self._dispatch({"cmd": "hide_thinking"})

    def start_thinking_animation(self):
        self.show_thinking()

    def stop_thinking_animation(self):
        self.hide_thinking()

    def set_input_enabled(self, enabled: bool):
        self._dispatch({"cmd": "set_input_enabled", "enabled": enabled})

    def clear_chat_silent(self):
        self._dispatch({"cmd": "clear"})

    def handle_ai_response(self, result: dict):
        if not result.get("thinking") and result.get("response"):
            self.add_ai_message(result["response"])

    def render_loaded_messages(self):
        """Replay current session history into the phone."""
        try:
            self.clear_chat_silent()
            history = self.controller.ai.conversation_history
            for msg in history:
                role    = msg.get("role", "")
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
            print(f"[AndroidBridge] render_loaded_messages error: {e}")

    def refresh_session_list(self):
        self._send_sessions_page(0, "")

    def request_manual_response(self, context: str, work_mode: bool, work_output: str, callback):
        """Send manual response request to Android phone."""
        self._pending_manual_response_cb = callback
        self._dispatch({
            "cmd": "show_manual_response",
            "context": context,
            "work_mode": work_mode,
            "work_output": work_output or ""
        })

    def dismiss_manual_response(self):
        """Tell Android to close its manual response dialog."""
        self._pending_manual_response_cb = None
        self._dispatch({"cmd": "dismiss_manual_response"})

    def update_voice_status(self, status: str):
        self._dispatch({"cmd": "voice_status", "status": status})