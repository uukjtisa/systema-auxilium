"""
systema/net/android_bridge.py
Android Bridge — mirrors the app over LAN to the Android remote controller.
Same JSON RPC protocol as ChatWindowTUI, but listens for a phone instead of
spawning a terminal.
"""
from __future__ import annotations
import json
import socket as _socket
import threading

DEFAULT_PORT = 1111

from systema.common.logger import _make_logger, _NoOpLogger

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
        self._pending_timeout_cb = None
        self._pending_image_paths: list = []   # images queued for next send
        # Ensure received-files directory exists
        from pathlib import Path
        from systema import APP_ROOT as _APP_ROOT
        self._received_dir = _APP_ROOT / "data" / "received"
        self._received_dir.mkdir(parents=True, exist_ok=True)
        self._browse_root = Path.home()

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def show(self):
        """Start listening for an Android connection."""
        if self._visible:
            return
        # Read the configured packet port (System settings); falls back to default.
        try:
            self._port = int((self.controller.settings or {}).get('packet_port', DEFAULT_PORT))
        except Exception:
            self._port = DEFAULT_PORT
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
            self.send_theme()               # match the phone palette to the PC theme
            self.render_loaded_messages()   # replay current session into phone
            self.send_token_usage()         # show context size on the phone
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
        elif cmd == "input_sync":
            text = msg.get("text", "")
            self._run_on_main(lambda t=text: self._apply_input_sync(t))
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
        elif cmd == "timeout_result":
            try:
                seconds = int(msg.get("seconds", 0))
            except (TypeError, ValueError):
                seconds = 0
            cb = self._pending_timeout_cb
            self._pending_timeout_cb = None
            if cb:
                cb(seconds)
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
        elif cmd == "memory_add":
            text = msg.get("text", "").strip()
            if text:
                try:
                    mm = self.controller.memory_manager
                    if mm and mm.is_ready:
                        mm.memorize(text)
                except Exception as e:
                    log.error(f"[AndroidBridge] memory_add error: {e}")
            self._send_memories()
        elif cmd == "memory_delete":
            mid = msg.get("id", "")
            if mid:
                try:
                    mm = self.controller.memory_manager
                    if mm and mm.is_ready:
                        mm.delete(mid)
                except Exception as e:
                    log.error(f"[AndroidBridge] memory_delete error: {e}")
            self._send_memories()
        elif cmd == "memory_edit":
            mid = msg.get("id", "")
            text = msg.get("text", "").strip()
            if mid and text:
                try:
                    mm = self.controller.memory_manager
                    if mm and mm.is_ready:
                        mm.update(mid, text)
                except Exception as e:
                    log.error(f"[AndroidBridge] memory_edit error: {e}")
            self._send_memories()
        elif cmd == "get_tasks":
            self._send_tasks()
        elif cmd == "task_toggle":
            tid = msg.get("id", "")
            try:
                tm = self.controller.task_manager
                for t in tm.get_tasks():
                    if t.get('id') == tid:
                        upd = dict(t)
                        upd['active'] = not upd.get('active', True)
                        tm.update_task(tid, upd)
                        break
            except Exception as e:
                log.error(f"[AndroidBridge] task_toggle error: {e}")
            self._send_tasks()
        elif cmd == "task_delete":
            tid = msg.get("id", "")
            if tid:
                try:
                    self.controller.task_manager.delete_task(tid)
                except Exception as e:
                    log.error(f"[AndroidBridge] task_delete error: {e}")
            self._send_tasks()
        elif cmd == "task_save":
            task = msg.get("task")
            tid = msg.get("id", "")
            if isinstance(task, dict) and task.get("name"):
                try:
                    tm = self.controller.task_manager
                    if tid:
                        tm.update_task(tid, task)
                    else:
                        tm.add_task(task)
                except Exception as e:
                    log.error(f"[AndroidBridge] task_save error: {e}")
            self._send_tasks()
        elif cmd == "open_settings":
            self._send_settings()
        elif cmd == "open_debug":
            self._send_debug()
        elif cmd == "setting_theme":
            value = msg.get("value", "")
            if value:
                def _apply():
                    try:
                        self.controller.settings['chat_theme'] = value
                        chat = self.controller._chat
                        if chat and hasattr(chat, 'apply_theme'):
                            chat.apply_theme(value)   # also mirrors theme back to phone
                        self.controller.save_settings()
                    except Exception as e:
                        log.error(f"[AndroidBridge] setting_theme error: {e}")
                    self._refresh_pc_settings_window()
                    self._send_settings()
                self._run_on_main(_apply)
        elif cmd == "setting_timeout":
            try:
                secs = int(msg.get("value", 300))
                self.controller.settings['tool_execution_timeout_seconds'] = max(1, secs)
                self.controller.save_settings()
            except Exception as e:
                log.error(f"[AndroidBridge] setting_timeout error: {e}")
            self._run_on_main(self._refresh_pc_settings_window)
            self._send_settings()
        elif cmd == "setting_provider":
            value = msg.get("value", "")
            if value:
                def _apply_p():
                    try:
                        self.controller.set_ai_provider(value)
                    except Exception as e:
                        log.error(f"[AndroidBridge] setting_provider error: {e}")
                    self._refresh_pc_settings_window()
                    self._send_settings()
                self._run_on_main(_apply_p)
        elif cmd == "setting_voice":
            enabled = bool(msg.get("enabled", False))
            def _apply_v():
                try:
                    # Route through the chat's voice methods so the PC button +
                    # status stay in sync (they call enable/disable_voice_mode).
                    chat = self.controller._chat
                    if chat and hasattr(chat, 'enable_voice'):
                        if enabled:
                            chat.enable_voice()
                        else:
                            chat.disable_voice()
                    else:
                        if enabled:
                            self.controller.enable_voice_mode()
                        else:
                            self.controller.disable_voice_mode()
                except Exception as e:
                    log.error(f"[AndroidBridge] setting_voice error: {e}")
                self._refresh_pc_settings_window()
                self._send_settings()
            self._run_on_main(_apply_v)
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

    def _run_on_main(self, fn):
        """Marshal a callable onto the GUI thread (recv thread has no Qt event loop)."""
        try:
            self.controller.bridge_run_on_main.emit(fn)
        except Exception as e:
            log.error(f"[AndroidBridge] _run_on_main error: {e}")

    def _apply_input_sync(self, text: str):
        """Set the PC chat input to mirror the phone, suppressing the echo back."""
        try:
            chat = self.controller._chat
            if not chat:
                return
            field = chat.input_field
            if field.toPlainText() == text:
                return
            chat._suppress_input_sync = True
            try:
                field.text_input.setPlainText(text)
                cur = field.text_input.textCursor()
                cur.movePosition(cur.MoveOperation.End)
                field.text_input.setTextCursor(cur)
            finally:
                chat._suppress_input_sync = False
        except Exception as e:
            log.error(f"[AndroidBridge] _apply_input_sync error: {e}")

    def _refresh_pc_settings_window(self):
        """If the PC Settings window is open, reload its widgets so a phone-side
        change is reflected live. Must run on the GUI thread."""
        try:
            win = getattr(getattr(self.controller, 'ui', None), 'settings_window', None)
            if win is not None and win.isVisible() and hasattr(win, 'load_settings'):
                win.load_settings()
        except Exception as e:
            log.error(f"[AndroidBridge] _refresh_pc_settings_window error: {e}")

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

    def send_theme(self, theme: dict | None = None):
        """Push the active colour theme to the phone so its palette matches the PC.
        Pass the resolved palette dict directly (from apply_theme) to avoid any
        settings-timing race; otherwise the current chat_theme setting is read."""
        try:
            if theme is None:
                from systema.ui.theme import THEMES, DEFAULT_THEME_KEY
                key = DEFAULT_THEME_KEY
                try:
                    key = self.controller.settings.get('chat_theme', DEFAULT_THEME_KEY)
                except Exception:
                    pass
                theme = THEMES.get(key, THEMES[DEFAULT_THEME_KEY])
            keys = ('base', 'surface', 'elevated', 'border',
                    'accent', 'deep', 'input_card', 'input_card_border')
            payload = {k: theme.get(k, '') for k in keys}
            self._dispatch({"cmd": "theme_data", "theme": payload})
        except Exception as e:
            log.error(f"[AndroidBridge] send_theme error: {e}")

    def send_token_usage(self):
        """Push the current context size (history + system prompt) to the phone.
        The phone shows this as a header chip; per-message input estimate is left
        to each device since the input text differs."""
        try:
            from systema.common.token_est import estimate_next_message_tokens, estimate_tokens
            ai = getattr(self.controller, 'ai', None)
            if not ai:
                return
            hist = getattr(ai, 'chat_history', None) or getattr(ai, 'conversation_history', []) or []
            sys_tokens = estimate_tokens(getattr(ai, 'system_prompt', '') or '')
            total = estimate_next_message_tokens("", hist) + sys_tokens
            self._dispatch({"cmd": "token_usage", "tokens": int(total)})
        except Exception as e:
            log.error(f"[AndroidBridge] send_token_usage error: {e}")

    def _send_debug(self):
        """Send a concise read-only diagnostics snapshot to the phone."""
        try:
            from systema.common.token_est import estimate_tokens
            c = self.controller
            ai = getattr(c, 'ai', None)
            s = c.settings or {}
            rows = []
            rows.append(["AI provider", str(s.get('ai_provider', '—'))])
            rows.append(["TTS provider", str(s.get('tts_provider', '—'))])
            try:
                rows.append(["Session", str(getattr(c, 'current_session_id', '') or '—')[:24]])
            except Exception:
                pass
            try:
                rows.append(["Messages", str(len(getattr(ai, 'conversation_history', []) or []))])
            except Exception:
                pass
            try:
                mm = c.memory_manager
                rows.append(["Memories", str(mm.count() if (mm and mm.is_ready) else 0)])
            except Exception:
                pass
            try:
                loaded = [sk['name'] for sk in c.skill_manager.get_skills() if sk.get('is_loaded')]
                rows.append(["Loaded skills", ", ".join(loaded) if loaded else "none"])
            except Exception:
                pass
            try:
                tm = ai.tool_manager
                state = "work mode" if getattr(tm.work, 'is_working', False) else \
                        ("processing" if getattr(tm, 'is_processing', False) else "idle")
                rows.append(["State", state])
            except Exception:
                pass
            # Use the SAME effective prompt the PC debug window shows
            # (base + all loaded skills, frontmatter stripped, as sent to the API).
            try:
                sysp = ai._get_effective_system_prompt()
            except Exception:
                sysp = getattr(ai, 'system_prompt', '') or ''
            rows.append(["System prompt", f"{len(sysp):,} chars · ~{estimate_tokens(sysp):,} tok"])
            rows.append(["Connection", self.get_connection_info()])
            # Send the full effective prompt so it matches the PC verbatim (mark
            # only if it exceeds a generous safety cap).
            _cap = 200000
            _payload_prompt = sysp if len(sysp) <= _cap else sysp[:_cap] + "\n… [truncated]"
            self._dispatch({"cmd": "debug_data", "rows": rows, "system_prompt": _payload_prompt})
        except Exception as e:
            log.error(f"[AndroidBridge] _send_debug error: {e}")

    def _send_settings(self):
        """Send the SAFE settings subset to the phone (no secrets/API keys)."""
        try:
            from systema.ui.theme import THEMES, DEFAULT_THEME_KEY
            s = self.controller.settings or {}
            try:
                providers = [p['name'] for p in self.controller.get_llm_provider_scripts()]
            except Exception:
                providers = []
            try:
                voice_on = bool(getattr(self.controller._chat, 'voice_enabled', False))
            except Exception:
                voice_on = False
            self._dispatch({
                "cmd": "settings_data",
                "theme": s.get('chat_theme', DEFAULT_THEME_KEY),
                "themes": list(THEMES.keys()),
                "timeout": int(s.get('tool_execution_timeout_seconds', 300)),
                "provider": s.get('ai_provider', 'manual'),
                "providers": providers,
                "voice": voice_on,
            })
        except Exception as e:
            log.error(f"[AndroidBridge] _send_settings error: {e}")

    def _send_tasks(self):
        """Send the full task dicts to the phone (edits round-trip losslessly)."""
        try:
            tasks = self.controller.task_manager.get_tasks()
        except Exception:
            tasks = []
        self._dispatch({"cmd": "tasks_data", "tasks": tasks})

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
            self.send_token_usage()   # context grew — refresh the phone chip

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
        """Replay current session history into the phone.

        Mirrors the desktop: work-mode chatter (assistant narration between
        entering a python_interpreter and exiting it) is hidden; the step execution
        notes and the exit summary are kept."""
        try:
            import re
            _WE_RE = re.compile(r'```[ \t]*python_interpreter\b[^\n]*\n(.*?)```', re.DOTALL)
            self.clear_chat_silent()
            tm = self.controller.ai.tool_manager
            history = self.controller.ai.conversation_history
            in_work_step = False
            for msg in history:
                role    = msg.get("role", "")
                raw = msg.get("content", "")
                if isinstance(raw, list):
                    raw = " ".join(
                        block.get("text", "") for block in raw
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if not isinstance(raw, str):
                    raw = ""

                # Exit sentinel removed: a work step is an assistant turn running a
                # python_interpreter fence (narration hidden); work mode ends at the
                # first assistant turn WITHOUT a python_interpreter code fence — that
                # turn is the report, rendered below. (Legacy bare `exit` fence still
                # counts as a finishing turn.)
                if role == "assistant":
                    _we = _WE_RE.search(raw)
                    if _we and _we.group(1).strip().lower() not in ("exit", ""):
                        in_work_step = True   # a work step — hide the narration
                        continue
                    in_work_step = False      # finish / normal reply — fall through to render
                elif role == "user":
                    in_work_step = False

                content = tm.strip_tool_calls(raw)
                if role == "ui_event":
                    if msg.get("_type") == "memory_context":
                        self._dispatch({
                            "cmd": "add_memory_context",
                            "context_id": msg.get("_memory_context_id", ""),
                            "memories": msg.get("_memories_preview", []),
                        })
                    elif msg.get("_type") == "file_op":
                        self.add_file_op(msg.get("_file_op") or {})
                    else:
                        self._dispatch({
                            "cmd": "add_work_execution",
                            "code": msg.get("_code", ""),
                            "output": msg.get("_output", ""),
                            "annotation": msg.get("_annotation", ""),
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

    def request_manual_response(self, context: str, is_working: bool, work_output: str, callback):
        """Send manual response request to Android phone."""
        self._pending_manual_response_cb = callback
        self._dispatch({
            "cmd": "show_manual_response",
            "context": context,
            "work_mode": is_working,
            "work_output": work_output or ""
        })

    def dismiss_manual_response(self):
        """Tell Android to close its manual response dialog."""
        self._pending_manual_response_cb = None
        self._dispatch({"cmd": "dismiss_manual_response"})

    def request_timeout(self, elapsed: int, callback):
        """Show the execution-timeout prompt on the phone. callback(seconds) is
        invoked from the recv thread when the phone answers (0 = kill)."""
        self._pending_timeout_cb = callback
        self._dispatch({"cmd": "show_timeout", "elapsed": int(elapsed)})

    def dismiss_timeout(self):
        """Tell Android to close its timeout dialog (PC or phone already decided)."""
        self._pending_timeout_cb = None
        self._dispatch({"cmd": "dismiss_timeout"})

    def show_work_banner(self, annotation: str = ""):
        """Tell Android to show the work-mode banner with optional annotation text."""
        self._dispatch({"cmd": "show_work_banner", "text": annotation or "Working…"})

    def hide_work_banner(self):
        """Tell Android to hide the work-mode banner."""
        self._dispatch({"cmd": "hide_work_banner"})

    def add_work_execution(self, code: str, output: str, annotation: str = ""):
        """Send a code-block + its stdout/stderr output to the Android client."""
        self._dispatch({"cmd": "add_work_execution", "code": code, "output": output, "annotation": annotation})

    def add_file_op(self, info: dict):
        """Send a file-op card (read_file / edit_file / write_file) to the phone.
        Mirrors chat_window.add_file_op_card — the phone renders a compact card
        with the path, +added/-removed counts (or the read range), and an
        expandable diff/content view. `added`/`removed` are -1 when absent
        (read_file has no counts) so the phone shows the read range instead."""
        _added = info.get("added")
        _removed = info.get("removed")
        self._dispatch({
            "cmd": "add_file_op",
            "tool": info.get("tool", "edit_file"),
            "path": info.get("display") or info.get("path", ""),
            "full_path": info.get("path", ""),
            "added": -1 if _added is None else int(_added),
            "removed": -1 if _removed is None else int(_removed),
            "created": bool(info.get("created")),
            "rejected": bool(info.get("rejected")),
            "read_range": info.get("read_range", "") or "",
            "detail": (info.get("detail") or "")[:20000],
        })

    # ── Live work-mode output streaming (mirrors chat_window) ─────────────────

    def start_live_output(self, code: str = ""):
        """Open a transient streaming console on the phone for the running code."""
        self._live_last = ""
        self._dispatch({"cmd": "live_output_start"})

    def update_live_output(self, text: str):
        """Push the current stdout/stderr buffer; skips unchanged ticks."""
        if text == getattr(self, "_live_last", None):
            return
        self._live_last = text
        self._dispatch({"cmd": "live_output_update", "text": text or ""})

    def end_live_output(self):
        """Tear down the phone's streaming console (final card arrives separately)."""
        self._live_last = ""
        self._dispatch({"cmd": "live_output_end"})

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