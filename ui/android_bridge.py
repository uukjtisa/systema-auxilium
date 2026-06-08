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

from core.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("AndroidBridge") if _verbose else _NoOpLogger()


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
        self._pending_image_paths: list = []   # images queued for next send
        # Ensure received-files directory exists
        from pathlib import Path
        self._received_dir = Path(__file__).resolve().parent.parent / "data" / "received"
        self._received_dir.mkdir(parents=True, exist_ok=True)
        self._browse_root = Path.home()

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
        log.info(f"[AndroidBridge] Listening on {self._local_ip}:{self._port}")

    def _accept_loop(self):
        try:
            self._server.settimeout(None)  # block until phone connects
            conn, addr = self._server.accept()
            log.info(f"[AndroidBridge] Phone connected from {addr}")
            self._conn = conn
            self._conn.settimeout(None)
            threading.Thread(target=self._recv_loop, daemon=True).start()
            self.render_loaded_messages()   # replay current session into phone
        except Exception as exc:
            if not self._stop_event.is_set():
                log.error(f"[AndroidBridge] accept failed: {exc}")

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
            log.info("[AndroidBridge] Phone disconnected")
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
        elif cmd == "attach_image_path":
            paths = msg.get("paths", [])
            if isinstance(paths, str):
                paths = [paths] if paths else []
            if paths:
                # Use signal so the UI call is marshalled safely to the main thread
                self.controller.bridge_attach_image_signal.emit(paths)
        elif cmd == "remove_image_path" or cmd == "detach_image_path":
            path = msg.get("path", "")
            if path:
                # Use signal so the UI call is marshalled safely to the main thread
                self.controller.bridge_detach_image_signal.emit(path)
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
                log.error(f"[AndroidBridge] code_approval_result error: {e}")
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
                    log.error(f"[AndroidBridge] toggle_skill error: {e}")
        elif cmd == "open_memory":
                    self._send_memories()
        elif cmd == "open_instructions":
            self._send_instructions()
        elif cmd == "set_instructions":
            try:
                self.controller.set_custom_instructions(msg.get("text", ""))
            except Exception as e:
                log.error(f"[AndroidBridge] set_instructions error: {e}")
        elif cmd == "open_names":
            self._send_names()
        elif cmd == "set_names":
            try:
                self.controller.set_user_name(msg.get("user_name", ""))
                self.controller.set_assistant_name(msg.get("asst_name", ""))
                self.render_loaded_messages()
            except Exception as e:
                log.error(f"[AndroidBridge] set_names error: {e}")
        elif cmd == "upload_file":
            self._handle_file_upload(msg)
        elif cmd == "browse_files":
            self._send_file_list(msg.get("path", ""))
        elif cmd == "detach_memory_context":
            context_id = msg.get("context_id", "")
            if context_id:
                try:
                    self.controller.detach_memory_context(context_id)
                except Exception as e:
                    log.error(f"[AndroidBridge] detach_memory_context error: {e}")

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
            log.error(f"[AndroidBridge] _send_sessions_page error: {e}")

    def _remove_pinned_by_path(self, chat, path: str):
        """Remove a pinned image card from the PC chat window by path.
        Passes notify=False so the removal is not echoed back to Android
        (Android already removed the card locally before sending detach_image_path).
        """
        try:
            for pi in list(getattr(chat, 'pinned_images', [])):
                if pi.get('path') == path:
                    chat._remove_pinned_image(pi, notify=False)
                    return
        except Exception as e:
            log.error(f"[AndroidBridge] _remove_pinned_by_path error: {e}")

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

    def _handle_file_upload(self, msg: dict):
        """Receive a base64-encoded file from Android, save to data/received/, reply with path."""
        import base64, os
        filename = msg.get("filename", "received_file")
        data_b64 = msg.get("data", "")
        try:
            raw = base64.b64decode(data_b64)
            # Avoid collisions by prefixing with timestamp
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_")
            safe_name = ts + os.path.basename(filename)
            dest = self._received_dir / safe_name
            dest.write_bytes(raw)
            abs_path = str(dest.resolve())
            log.info(f"[AndroidBridge] File received → {abs_path}")
            self._dispatch({
                "cmd": "file_received",
                "filename": safe_name,
                "path": abs_path,
            })
        except Exception as e:
            log.error(f"[AndroidBridge] _handle_file_upload error: {e}")
            self._dispatch({"cmd": "file_upload_error", "error": str(e)})

    def _send_file_list(self, path_str: str):
        """Send directory listing of a host path to the Android file browser."""
        from pathlib import Path
        import os
        try:
            if path_str:
                target = Path(path_str)
            else:
                target = self._browse_root
            if not target.exists() or not target.is_dir():
                target = self._browse_root
            entries = []
            # Parent entry (go up)
            parent = str(target.parent) if target != target.parent else ""
            for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                try:
                    entries.append({
                        "name": item.name,
                        "path": str(item.resolve()),
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else 0,
                    })
                except PermissionError:
                    pass
            self._dispatch({
                "cmd": "file_list",
                "current_path": str(target.resolve()),
                "parent_path": parent,
                "entries": entries,
            })
        except Exception as e:
            log.error(f"[AndroidBridge] _send_file_list error: {e}")
            self._dispatch({"cmd": "file_list_error", "error": str(e)})

    def _handle_file_upload(self, msg: dict):
        """Receive a base64-encoded file from Android, save to data/received/, reply with path."""
        import base64, os
        filename = msg.get("filename", "received_file")
        data_b64 = msg.get("data", "")
        try:
            raw = base64.b64decode(data_b64)
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_")
            safe_name = ts + os.path.basename(filename)
            dest = self._received_dir / safe_name
            dest.write_bytes(raw)
            abs_path = str(dest.resolve())
            log.info(f"[AndroidBridge] File received → {abs_path}")
            _img_exts = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.jfif')
            if any(safe_name.lower().endswith(ext) for ext in _img_exts):
                try:
                    from PyQt6.QtCore import QTimer
                    chat = self.controller._chat
                    if chat:
                        QTimer.singleShot(0, lambda p=abs_path: chat._handle_image_file_drop(p))
                except Exception as e:
                    log.error(f"[AndroidBridge] image routing error: {e}")
                # Also notify Android so the attach dialog appears on phone too
                self._dispatch({"cmd": "file_received", "filename": safe_name, "path": abs_path})
                return
            self._dispatch({"cmd": "file_received", "filename": safe_name, "path": abs_path})
        except Exception as e:
            log.error(f"[AndroidBridge] _handle_file_upload error: {e}")
            self._dispatch({"cmd": "file_upload_error", "error": str(e)})

    def _send_file_list(self, path_str: str):
        """Send directory listing of a host path to the Android file browser."""
        from pathlib import Path
        import sys
        try:
            # Empty path → show drive list on Windows, filesystem root on Unix
            if not path_str:
                if sys.platform == "win32":
                    import string
                    entries = []
                    for letter in string.ascii_uppercase:
                        drive = Path(f"{letter}:\\")
                        if drive.exists():
                            entries.append({
                                "name": f"{letter}:\\",
                                "path": str(drive),
                                "type": "dir",
                                "size": 0,
                            })
                    self._dispatch({
                        "cmd": "file_list",
                        "current_path": "",
                        "parent_path": "",
                        "entries": entries,
                    })
                else:
                    self._dispatch({
                        "cmd": "file_list",
                        "current_path": "/",
                        "parent_path": "",
                        "entries": [{"name": "/", "path": "/", "type": "dir", "size": 0}],
                    })
                return
            target = Path(path_str)
            if not target.exists() or not target.is_dir():
                target = self._browse_root
            parent = str(target.parent) if target != target.parent else ""
            entries = []
            for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                try:
                    entries.append({
                        "name": item.name,
                        "path": str(item.resolve()),
                        "type": "dir" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else 0,
                    })
                except PermissionError:
                    pass
            self._dispatch({
                "cmd": "file_list",
                "current_path": str(target.resolve()),
                "parent_path": parent,
                "entries": entries,
            })
        except Exception as e:
            log.error(f"[AndroidBridge] _send_file_list error: {e}")
            self._dispatch({"cmd": "file_list_error", "error": str(e)})

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

    # ── Thumbnail helper ──────────────────────────────────────────────────────

    @staticmethod
    def _make_thumb_b64(path: str, size: int = 80) -> str:
        """Generate a small base64 JPEG thumbnail from an image path."""
        try:
            from PyQt6.QtGui import QImage
            from PyQt6.QtCore import QByteArray, QBuffer
            import base64
            img = QImage(path)
            if img.isNull():
                return ""
            small = img.scaled(size, size)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QBuffer.OpenModeFlag.WriteOnly)
            small.save(buf, "JPEG", 70)
            buf.close()
            return base64.b64encode(ba.data()).decode("ascii")
        except Exception:
            return ""

    def notify_image_attached(self, path: str, send_every: bool = True):
        """Tell Android a new image card was pinned on the PC side."""
        self._dispatch({
            "cmd": "image_attached",
            "path": path,
            "send_every": send_every,
            "thumb_b64": self._make_thumb_b64(path),
        })

    def notify_image_detached(self, path: str):
        """Tell Android an image card was removed on the PC side."""
        self._dispatch({"cmd": "image_detached", "path": path})

    def add_user_message(self, text: str, image_paths: list | None = None):
        if text.strip():
            if image_paths:
                thumbs = [t for t in (self._make_thumb_b64(p) for p in image_paths) if t]
                self._dispatch({"cmd": "add_user", "text": text, "images": thumbs})
            else:
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
        self.hide_work_banner()

    def start_thinking_animation(self):
        self.show_thinking()

    def stop_thinking_animation(self):
        self.hide_thinking()

    def set_input_enabled(self, enabled: bool):
        self._dispatch({"cmd": "set_input_enabled", "enabled": enabled})

    def clear_chat_silent(self):
        self._dispatch({"cmd": "clear"})
        self.hide_work_banner()

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
                if role == "ui_event":
                    if msg.get("_type") == "memory_context":
                        self._dispatch({
                            "cmd": "add_memory_context",
                            "context_id": msg.get("_memory_context_id", ""),
                            "memories": msg.get("_memories_preview", []),
                        })
                    else:
                        self._dispatch({
                            "cmd": "add_work_execution",
                            "code": msg.get("_code", ""),
                            "output": msg.get("_output", ""),
                        })
                elif content:
                    if role == "user":
                        self._dispatch({"cmd": "add_user", "text": content})
                    elif role == "assistant":
                        self._dispatch({"cmd": "add_ai", "text": content})
        except Exception as e:
            log.error(f"[AndroidBridge] render_loaded_messages error: {e}")

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

    def show_work_banner(self, annotation: str = ""):
        """Tell Android to show the work-mode banner with optional annotation text."""
        self._dispatch({"cmd": "show_work_banner", "text": annotation or "Working…"})

    def hide_work_banner(self):
        """Tell Android to hide the work-mode banner."""
        self._dispatch({"cmd": "hide_work_banner"})

    def add_work_execution(self, code: str, output: str, annotation: str = ""):
        """Send a code-block + its stdout/stderr output to the Android client."""
        self._dispatch({"cmd": "add_work_execution", "code": code, "output": output, "annotation": annotation})

    def show_work_banner(self, annotation: str = ""):
        """Tell Android to show the work-mode banner with optional annotation text."""
        self._dispatch({"cmd": "show_work_banner", "text": annotation or "Working…"})

    def hide_work_banner(self):
        """Tell Android to hide the work-mode banner."""
        self._dispatch({"cmd": "hide_work_banner"})

    def update_voice_status(self, status: str):
        self._dispatch({"cmd": "voice_status", "status": status})

    def add_memory_context_card(self, context_id: str, memories: list):
        """Send a memory context card to the Android client."""
        self._dispatch({
            "cmd": "add_memory_context",
            "context_id": context_id,
            "memories": memories,
        })

    def remove_memory_context_card(self, context_id: str):
        """Tell Android to remove a memory context card by ID."""
        self._dispatch({
            "cmd": "remove_memory_context",
            "context_id": context_id,
        })