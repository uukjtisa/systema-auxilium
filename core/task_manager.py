"""
core/task_manager.py
Task Manager - Background automation layer for scheduled AI task sessions.
Each task runs in its own daemon thread with an isolated AI caller.
"""

import json
import threading
import re
import io
import sys
import traceback
import uuid
from datetime import datetime, date, timedelta
import math
from pathlib import Path
from core.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("TaskManager") if _verbose else _NoOpLogger()

_APP_ROOT = Path(__file__).resolve().parent.parent


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _now_stamp():
    """Returns e.g. 'Monday, May 19, 2025 - 10:27 PM'"""
    return datetime.now().strftime("%A, %B %d, %Y - %I:%M %p")


# ── {{code}} block resolver ───────────────────────────────────────────────────

def _resolve_instruction(instruction: str) -> tuple:
    """
    Finds all {{...}} blocks in instruction text, executes each one,
    and replaces the block with its output inline.

    Returns:
        (resolved_text: str, error_traceback: str | None)
        If any block errors, returns original instruction + traceback.
    """
    pattern = re.compile(r'\{\{(.*?)\}\}', re.DOTALL)
    errors = []

    def _run_block(match):
        code = match.group(1).strip()
        lines = code.splitlines()
        out_buf = io.StringIO()
        old_stdout = sys.stdout
        result_holder = [None]
        try:
            # Split into body + final expression for auto-return
            if lines:
                try:
                    compile(lines[-1], '<expr>', 'eval')
                    exec_body = '\n'.join(lines[:-1])
                    eval_expr = lines[-1]
                except SyntaxError:
                    exec_body = code
                    eval_expr = None
            else:
                exec_body = code
                eval_expr = None

            ns = {}
            sys.stdout = out_buf
            if exec_body.strip():
                exec(exec_body, ns)  # noqa: S102
            if eval_expr:
                result_holder[0] = eval(eval_expr, ns)  # noqa: S307
            sys.stdout = old_stdout
        except Exception:
            sys.stdout = old_stdout
            errors.append(traceback.format_exc())
            return match.group(0)  # leave block unchanged on error

        printed = out_buf.getvalue()
        if result_holder[0] is not None:
            return f"{printed.rstrip()}\n{result_holder[0]}" if printed else str(result_holder[0])
        return printed.rstrip() if printed else ''

    resolved = pattern.sub(_run_block, instruction)
    if errors:
        return instruction, '\n'.join(errors)
    return resolved, None


# ── Task AI Engine ────────────────────────────────────────────────────────────

class TaskAIEngine:
    """
    Isolated AI caller for background task sessions.

    Owns a dedicated AIEngine instance — completely separate from the main
    conversation engine so task pings never block or interfere with the user.

    Settings are synced from the controller before every call, so any
    provider/model change the user makes is picked up automatically.
    """

    # Matches: {"tool": "send_message_main", "input": "..."}
    _SEND_MSG_RE = re.compile(
        r'\{[^{}]*"tool"\s*:\s*"send_message_main"[^{}]*"input"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL
    )

    def __init__(self, controller):
        self._controller = controller
        self._engine = None
        self._pending_context_images = []  # image paths queued by agent, flushed each work step
        self._init_engine()

    def _init_engine(self):
        """Spin up a private AIEngine instance using current controller settings."""
        try:
            from core.ai_engine import AIEngine
            s = self._controller.settings
            self._engine = AIEngine(
                api_key=s.get('api_key', ''),
                gemini_api_key=s.get('gemini_api_key', ''),
                puter_server=getattr(self._controller, 'puter_server', None),
                settings_callback=lambda: self._controller.settings,
            )
            # Background mode — no UI, no approval dialogs
            self._engine.tool_manager._get_chat = lambda: None
            self._engine.tool_manager._get_android_bridge = lambda: None
            self._engine.tool_manager.supervised_execution = False
            log.info("[TaskAIEngine._init_engine] ✓ Dedicated AIEngine instance created")
            # ── Screen-capture tools for the task agent's Python namespace ────
            _task_ai_ref = self

            def _task_take_screenshot(save_path=None):
                """Take a screenshot and queue it to be passed as an image to the
                AI on the next work step via chat_image(). Returns the saved path."""
                import os, time, tempfile
                try:
                    from PIL import ImageGrab
                    if save_path is None:
                        save_path = os.path.join(
                            tempfile.gettempdir(), f"task_shot_{int(time.time())}.png"
                        )
                    ImageGrab.grab().save(save_path)
                    _task_ai_ref._pending_context_images.append(save_path)
                    return save_path
                except Exception as _e1:
                    try:
                        import pyautogui
                        if save_path is None:
                            save_path = os.path.join(
                                tempfile.gettempdir(), f"task_shot_{int(time.time())}.png"
                            )
                        pyautogui.screenshot(save_path)
                        _task_ai_ref._pending_context_images.append(save_path)
                        return save_path
                    except Exception as _e2:
                        return f"[take_screenshot ERROR] {_e2}"

            def _attach_image_to_context(path):
                """Queue an existing image file to be passed to chat_image() on the next work step."""
                import os
                if not os.path.isfile(path):
                    return f"[attach_image_to_context] File not found: {path}"
                _task_ai_ref._pending_context_images.append(path)
                return f"[attach_image_to_context] Queued: {path}"

            self._engine.tool_manager.tools['python'].namespace['take_screenshot'] = _task_take_screenshot
            self._engine.tool_manager.tools['python'].namespace['attach_image_to_context'] = _attach_image_to_context
            # ─────────────────────────────────────────────────────────────────

        except Exception as e:
            log.error(f"[TaskAIEngine._init_engine] ✗ {type(e).__name__}: {e}")
            self._engine = None

    def _sync_settings(self):
        """Copy all current provider/model settings from controller into our engine."""
        if self._engine is None:
            self._init_engine()
        if self._engine is None:
            return
        s = self._controller.settings
        self._engine.api_key                = s.get('api_key', '')
        self._engine.gemini_api_key         = s.get('gemini_api_key', '')
        self._engine.ai_provider            = s.get('ai_provider', 'anthropic')
        self._engine.anthropic_model        = s.get('anthropic_model', 'claude-sonnet-4-5-20250929')
        self._engine.anthropic_temperature  = float(s.get('anthropic_temperature', 1.0))
        self._engine.anthropic_max_tokens   = int(s.get('anthropic_max_tokens', 8192))
        self._engine.anthropic_auto_tokens  = bool(s.get('anthropic_auto_tokens', True))
        self._engine.gemini_model           = s.get('gemini_model', 'gemini-2.5-flash')
        self._engine.gemini_temperature     = float(s.get('gemini_temperature', 1.0))
        self._engine.gemini_max_tokens      = int(s.get('gemini_max_tokens', 8192))
        self._engine.gemini_auto_tokens     = bool(s.get('gemini_auto_tokens', True))
        self._engine.gemini_top_p           = s.get('gemini_top_p', None)
        self._engine.gemini_top_k           = s.get('gemini_top_k', None)
        self._engine.puter_model            = s.get('puter_model', 'gpt-4o-mini')
        self._engine.puter_timeout          = int(s.get('puter_timeout', 30))
        self._engine.custom_script_path     = s.get('custom_script_path', '')

    def call(self, system_prompt: str, history: list) -> str | None:
        """Call the AI using the same provider the user has configured (raw, no tool processing)."""
        self._sync_settings()
        if self._engine is None:
            log.error("[TaskAIEngine.call] No engine available")
            return None
        return self._engine.raw_call(system_prompt, history)

    def run_full_ping(self, ping_content: str, history: list, task_system_prompt: str, max_iterations: int = 20) -> tuple:
        """
        Run a full ping using the same pipeline as the main session:
        generate_response + continue_work_mode loop.
        Supports work_environment, execute_code, memorize, load_skill, etc.

        Returns:
            (response_text: str | None, send_main_calls: list[str], updated_history: list)
        """
        self._sync_settings()
        if self._engine is None:
            log.error("[TaskAIEngine.run_full_ping] No engine available")
            return None, [], history

        # Inject task system prompt (includes full user system prompt + task context)
        self._engine.system_prompt_hijacked = True
        self._engine.custom_system_prompt = task_system_prompt
        # Ensure background mode persists after sync
        self._engine.tool_manager.supervised_execution = False

        # Restore session history into the engine
        self._engine.conversation_history = list(history)

        all_responses = []
        send_main_calls = []

        # First call — generate_response handles tool calls internally
        try:
            result = self._engine.generate_response(ping_content)
        except Exception as e:
            log.error(f"[TaskAIEngine.run_full_ping] generate_response failed: {e}")
            return None, [], list(self._engine.conversation_history)

        if result.get('response'):
            all_responses.append(result['response'])
            send_msg, _ = self.extract_send_message_main(result['response'])
            if send_msg:
                send_main_calls.append(send_msg)

        # Work mode continuation loop (mirrors controller.auto_continue_work_mode)
        iteration = 0
        while result.get('thinking') and iteration < max_iterations:
            iteration += 1
            try:
                if self._pending_context_images:
                    # Agent queued images via take_screenshot() or attach_image_to_context().
                    # Do this work step manually so we can pass them into chat_image().
                    # _call_provider(images=...) → _http_custom_script → chat_image(sys, convo, images)
                    import os as _os
                    pending = list(self._pending_context_images)
                    self._pending_context_images.clear()
                    work_prompt = self._engine.tool_manager.get_work_mode_prompt()
                    self._engine.conversation_history.append({'role': 'system', 'content': work_prompt})
                    ai_text = self._engine._call_provider(images=pending)
                    for _p in pending:
                        try: _os.remove(_p)
                        except Exception: pass
                    if not ai_text:
                        log.error("[TaskAIEngine.run_full_ping] No response during image work step")
                        break
                    result = self._engine._process_work_mode_response(ai_text)
                else:
                    result = self._engine.continue_work_mode()
            except Exception as e:
                log.error(f"[TaskAIEngine.run_full_ping] continue_work_mode failed: {e}")
                break

            if result.get('response'):
                all_responses.append(result['response'])
                send_msg, _ = self.extract_send_message_main(result['response'])
                if send_msg:
                    send_main_calls.append(send_msg)

            if result.get('exited_work_mode'):
                log.info(f"[TaskAIEngine.run_full_ping] Work mode exited after {iteration} iteration(s)")
                break

        # Cleanup any leftover queued images that never got flushed (e.g. agent
        # queued on the last step then exited before another iteration ran)
        import os as _os
        for _p in self._pending_context_images:
            try: _os.remove(_p)
            except Exception: pass
        self._pending_context_images.clear()

        final_text = '\n'.join(r for r in all_responses if r)
        return final_text or None, send_main_calls, list(self._engine.conversation_history)

    def extract_send_message_main(self, text: str) -> tuple:
        """
        Pull the first send_message_main call out of AI response text.

        Returns:
            (message: str | None, cleaned_text: str)
        """
        match = self._SEND_MSG_RE.search(text)
        if not match:
            return None, text
        msg = match.group(1).replace('\\"', '"').replace('\\n', '\n')
        cleaned = self._SEND_MSG_RE.sub('', text).strip()
        return msg, cleaned


# ── Task Thread ───────────────────────────────────────────────────────────────

class TaskThread(threading.Thread):
    """One daemon thread per task — runs the scheduled ping loop independently."""

    def __init__(self, task: dict, controller, skill_manager, send_main_callback, set_inactive_callback=None):
        super().__init__(daemon=True, name=f"Task-{task['id'][:8]}")
        self._task = task
        self._controller = controller
        self._settings = lambda: controller.settings   # kept for any internal use
        self._skill_manager = skill_manager
        self._send_main = send_main_callback   # fn(str) → appends to main session
        self._set_inactive = set_inactive_callback   # fn(task_id) → marks task inactive after one-shot
        self._stop_event = threading.Event()
        self._ai = TaskAIEngine(controller)
        self._sessions_root = _APP_ROOT / "data" / "task-sessions"
        self._sessions_root.mkdir(parents=True, exist_ok=True)
        log.info(f"[TaskThread.__init__] Ready | task='{task['name']}' id={task['id'][:8]}")

    def stop(self):
        self._stop_event.set()

    # ── Session I/O ───────────────────────────────────────────────────────────

    def _session_path(self, task_id: str, date_str: str) -> Path:
        folder = self._sessions_root / task_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{date_str}.json"

    def _load_session(self, task_id: str, date_str: str) -> dict | None:
        p = self._session_path(task_id, date_str)
        if not p.exists():
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"[TaskThread._load_session] {e}")
            return None

    def _save_session(self, session: dict, task_id: str, date_str: str):
        p = self._session_path(task_id, date_str)
        try:
            tmp = p.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
            tmp.replace(p)
        except Exception as e:
            log.error(f"[TaskThread._save_session] {e}")

    def _new_session(self) -> dict:
        task = self._task
        now = datetime.now()
        session_id = now.strftime("%m_%d_%Y_%H_%M_%S_") + str(now.microsecond)[:2]
        return {
            "session_name": f"task-session-{task['name']}",
            "creation_time_and_date": _now_stamp(),
            "id": session_id,
            "task_id": task['id'],
            "date": date.today().isoformat(),
            "ended_at": None,
            "chat_history": []
        }

    # ── System prompt ─────────────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        task = self._task
        perms = task.get('permissions', {})

        # ── Base: full system prompt from controller (same as main session) ──
        base_prompt = ""
        try:
            base_prompt = self._controller.ai._get_effective_system_prompt()
        except Exception as e:
            log.warning(f"[TaskThread._build_system_prompt] Could not get controller prompt: {e}")

        # Build permissions block
        perm_lines = []
        if perms.get('allow_workmode'):
            perm_lines.append("- You MAY use the work_environment tool to perform agentic tasks.")
        if perms.get('allow_execute_code'):
            perm_lines.append("- You MAY use execute_code to run Python code.")
        if perms.get('allow_skill_load_unload'):
            perm_lines.append("- You MAY use load_skill and unload_skill.")
        if not perm_lines:
            perm_lines.append("- READ-ONLY mode. Do not attempt to execute code or use tools.")
        perm_block = '\n'.join(perm_lines)

        # Task-specific context appended after the full system prompt
        task_section = (
            f"\n\n=== BACKGROUND TASK CONTEXT ===\n"
            f"You are currently running as an automated background task agent — NOT in a live user conversation.\n"
            f"Task name: {task['name']}\n\n"
            f"Task Permissions:\n{perm_block}\n\n"
            f"To notify the user, emit EXACTLY this JSON on its own line:\n"
            f'{{"tool": "send_message_main", "input": "your message to the user"}}\n\n'
            f"Only message the user when something genuinely needs their attention.\n"
            f"Each ping message has a timestamp appended for your temporal awareness.\n"
            f"=== END BACKGROUND TASK CONTEXT ===\n"
        )

        # ── Inject pre-loaded skill content ───────────────────────────────────
        loaded_skills = task.get('loaded_skills', [])
        skill_injections = []
        for skill_name in loaded_skills:
            content = self._load_skill_content(skill_name)
            if content:
                skill_injections.append(
                    f"\n\n=== PRE-LOADED SKILL: {skill_name} ===\n"
                    f"{content}\n"
                    f"=== END SKILL: {skill_name} ===\n"
                )
                log.info(f"[TaskThread._build_system_prompt] Injected skill '{skill_name}'")
            else:
                log.warning(f"[TaskThread._build_system_prompt] Skill '{skill_name}' not found — skipped")

        skills_section = "".join(skill_injections)
        return base_prompt + skills_section + task_section

    # ── Schedule helpers ──────────────────────────────────────────────────────

    def _in_window(self) -> bool:
        sched = self._task.get('daily_schedule', {})
        if sched.get('whole_day', False):
            return True
        try:
            now = datetime.now().time()
            start = datetime.strptime(sched.get('start', '00:00'), '%H:%M').time()
            end   = datetime.strptime(sched.get('end',   '23:59'), '%H:%M').time()
            return start <= now <= end
        except Exception:
            return True

    def _wait_seconds_until_start(self) -> float:
        sched = self._task.get('daily_schedule', {})
        if sched.get('whole_day', False):
            return 0
        try:
            now = datetime.now()
            start = datetime.strptime(sched.get('start', '00:00'), '%H:%M')
            target = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if target <= now:
                target = target.replace(day=target.day + 1)
            return (target - now).total_seconds()
        except Exception:
            return 0

    def _seconds_until_window_end(self) -> float:
        sched = self._task.get('daily_schedule', {})
        if sched.get('whole_day', False):
            now = datetime.now()
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
            return max(0.0, (end_of_day - now).total_seconds())
        try:
            now = datetime.now()
            end = datetime.strptime(sched.get('end', '23:59'), '%H:%M')
            target = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
            return max(0.0, (target - now).total_seconds())
        except Exception:
            return 86400.0

    # ── History builder ───────────────────────────────────────────────────────

    def _build_api_history(self, session: dict) -> list:
        task = self._task
        limit_cfg = task.get('limit_session_messages', {})
        history = [m for m in session.get('chat_history', []) if m.get('role') in ('system', 'assistant')]
        if limit_cfg.get('enabled', False):
            max_n = max(1, int(limit_cfg.get('max_messages', 5)))
            history = history[-max_n:]
        return history

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _load_skill_content(self, skill_name: str) -> str | None:
        """
        Load the instruction content of a skill by name.
        Tries skill_manager API first, then falls back to common file paths on disk.
        Returns the skill instruction text, or None if not found.
        """
        # ── Try skill_manager API ─────────────────────────────────────────────
        if self._skill_manager is not None:
            for method_name in ('get_skill_instructions', 'get_skill_prompt',
                                'get_skill_content', 'load_skill_content'):
                fn = getattr(self._skill_manager, method_name, None)
                if callable(fn):
                    try:
                        content = fn(skill_name)
                        if content:
                            return str(content)
                    except Exception:
                        pass

        # ── Fall back to reading skill instruction files from disk ────────────
        skills_dir = _APP_ROOT / "skills"
        for fname in ("instructions.md", "SKILL.md", "skill.md",
                      "instructions.txt", "prompt.md", "system_prompt.txt"):
            p = skills_dir / skill_name / fname
            if p.exists():
                try:
                    content = p.read_text(encoding='utf-8')
                    log.info(f"[TaskThread._load_skill_content] Loaded '{skill_name}' from '{p.name}'")
                    return content
                except Exception as e:
                    log.warning(f"[TaskThread._load_skill_content] Could not read '{p}': {e}")

        log.warning(f"[TaskThread._load_skill_content] Skill '{skill_name}' content not found anywhere")
        return None

    def _next_specific_ping_seconds(self) -> float | None:
        """
        For specific ping times mode: returns seconds until the next
        scheduled ping time today that hasn't passed yet.
        Returns None if all scheduled pings for today have already passed.
        """
        ping_times = self._task.get('specific_ping_times', [])
        if not ping_times:
            return None
        now = datetime.now()
        future_pings = []
        for t_str in ping_times:
            try:
                h, m = map(int, t_str.split(':'))
                ping_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if ping_dt > now:
                    future_pings.append(ping_dt)
            except Exception:
                pass
        if not future_pings:
            return None
        return (min(future_pings) - now).total_seconds()

    def _next_schedule_relative_seconds(self) -> float:
        """
        Returns seconds until the next interval-aligned ping, anchored to the
        schedule window's start time (or midnight for whole-day tasks).

        Example: window 04:00–19:00, interval 120 min, current time 16:31
          elapsed since 04:00 = 751 min
          next slot = floor(751/120)+1 = 7 → 7×120 = 840 min after 04:00 = 18:00
          wait = 18:00 − 16:31 = 89 min = 5340 s
        """
        task = self._task
        interval_sec = task.get('interval_minutes', 30) * 60
        sched = task.get('daily_schedule', {})
        now = datetime.now()

        if sched.get('whole_day', False):
            start_h, start_m = 0, 0
        else:
            try:
                start_h, start_m = map(int, sched.get('start', '00:00').split(':'))
            except Exception:
                start_h, start_m = 0, 0

        start_today = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

        if now <= start_today:
            # Haven't reached window start yet — first ping fires at window open
            return max(0.0, (start_today - now).total_seconds())

        elapsed_sec = (now - start_today).total_seconds()
        slots_passed = math.floor(elapsed_sec / interval_sec)
        next_ping = start_today + timedelta(seconds=(slots_passed + 1) * interval_sec)
        return max(0.0, (next_ping - now).total_seconds())

    def run(self):
        task = self._task
        task_id = task['id']
        interval_sec = task.get('interval_minutes', 30) * 60
        log.info(f"[TaskThread.run] Starting | task='{task['name']}' interval={interval_sec}s")

        # ── One-shot task handling ────────────────────────────────────────────
        one_time = task.get('one_time_schedule', {})
        if one_time.get('enabled'):
            datetimes = sorted(one_time.get('datetimes', []))
            if not datetimes:
                log.warning(f"[TaskThread.run] One-shot: no datetimes configured for '{task['name']}' — marking inactive")
                if self._set_inactive:
                    self._set_inactive(task_id)
                return

            # Find the earliest datetime that hasn't passed yet
            now = datetime.now()
            target_dt = None
            for dt_str in datetimes:
                try:
                    candidate = datetime.strptime(dt_str, '%Y-%m-%dT%H:%M')
                    if candidate > now:
                        target_dt = candidate
                        break
                    else:
                        log.info(f"[TaskThread.run] One-shot: '{dt_str}' already passed — trying next backup")
                except Exception:
                    log.warning(f"[TaskThread.run] One-shot: invalid datetime '{dt_str}' — skipping")

            if target_dt is None:
                log.info(f"[TaskThread.run] One-shot: all times passed for '{task['name']}' — marking inactive")
                if self._set_inactive:
                    self._set_inactive(task_id)
                return

            wait_sec = (target_dt - now).total_seconds()
            target_str = target_dt.strftime('%Y-%m-%dT%H:%M')
            log.info(f"[TaskThread.run] One-shot: waiting {wait_sec:.0f}s until '{target_str}'")
            self._stop_event.wait(wait_sec)
            if self._stop_event.is_set():
                return

            # ── Fire the ping ─────────────────────────────────────────────────
            today = date.today().isoformat()
            session = self._load_session(task_id, today) or self._new_session()
            self._save_session(session, task_id, today)
            instruction = task.get('instruction', '')
            resolved, error = _resolve_instruction(instruction)
            if error:
                log.error(f"[TaskThread.run] One-shot code block error: {error[:200]}")
                try:
                    self._send_main(
                        f"⚠️ **Task '{task['name']}' one-shot — code block error.**\n\n```\n{error}\n```"
                    )
                except Exception:
                    pass
            else:
                system_prompt = self._build_system_prompt()
                api_history = self._build_api_history(session)
                ping_content = (
                    f"<SYSTEM_AUTOMATED_TASK_PING>\nTHIS IS AN AUTOMATED "
                    f"SYSTEM MESSAGE, YOUR TASK IS STATED BELOW:\n\n{resolved}\n{_now_stamp()}\n\n"
                    f"</SYSTEM_AUTOMATED_TASK_PING>"
                )
                session['chat_history'].append({'role': 'system', 'content': ping_content})
                _unlimited = task.get('unlimited_work_iterations', False)
                _max_iter  = 999999 if _unlimited else task.get('max_work_iterations', 20)
                response_text, send_main_calls, _ = self._ai.run_full_ping(
                    ping_content, api_history, system_prompt, max_iterations=_max_iter
                )
                # Dispatch all messages — run_full_ping is fully done by this point
                for msg in send_main_calls:
                    try:
                        self._send_main(msg)
                    except Exception as e:
                        log.error(f"[TaskThread.run] One-shot send_main failed: {e}")
                if response_text:
                    session['chat_history'].append({
                        'role': 'assistant',
                        'content': f"{response_text}\n{_now_stamp()}"
                    })
                self._save_session(session, task_id, today)
                log.info(f"[TaskThread.run] One-shot ping complete for task='{task['name']}'")
            # ── AI is fully done — NOW mark inactive ──────────────────────────
            if self._set_inactive:
                self._set_inactive(task_id)
            return

        while not self._stop_event.is_set():

            # ── Wait until inside schedule window ────────────────────────────
            if not self._in_window():
                wait = self._wait_seconds_until_start()
                log.info(f"[TaskThread.run] Outside window — sleeping {wait:.0f}s")
                self._stop_event.wait(wait)
                if self._stop_event.is_set():
                    break
                if not self._in_window():
                    continue

            today = date.today().isoformat()

            # ── Load or create today's session ───────────────────────────────
            session = self._load_session(task_id, today)
            if session is None:
                session = self._new_session()
                self._save_session(session, task_id, today)
                log.info(f"[TaskThread.run] New session for task='{task['name']}' date={today}")

            # ── Wait for the interval or next specific ping time ─────────────
            if self._task.get('use_specific_ping_times', False):
                wait_sec = self._next_specific_ping_seconds()
                if wait_sec is None:
                    log.info(f"[TaskThread.run] No more specific pings today — waiting for next window")
                    session['ended_at'] = _now_stamp()
                    self._save_session(session, task_id, today)
                    sleep_secs = self._wait_seconds_until_start() or 86400
                    self._stop_event.wait(sleep_secs)
                    continue
                log.info(f"[TaskThread.run] Next specific ping in {wait_sec:.0f}s")
                self._stop_event.wait(wait_sec)
            else:
                use_sched_rel = task.get('ping_mode', 'startup_relative') == 'schedule_relative'
                if use_sched_rel:
                    wait_sec = self._next_schedule_relative_seconds()
                    log.info(f"[TaskThread.run] Schedule-relative: next ping in {wait_sec:.0f}s")
                else:
                    wait_sec = interval_sec
                    log.info(f"[TaskThread.run] Startup-relative: waiting {interval_sec}s before next ping")
                self._stop_event.wait(wait_sec)
            if self._stop_event.is_set():
                break

            # ── Check if window closed during wait ───────────────────────────
            if not self._in_window():
                session['ended_at'] = _now_stamp()
                self._save_session(session, task_id, today)
                log.info(f"[TaskThread.run] Window closed — ending session")
                wait = self._wait_seconds_until_start()
                self._stop_event.wait(wait)
                continue

            # ── If date rolled over, start fresh next iteration ──────────────
            if date.today().isoformat() != today:
                continue

            # ── Resolve {{code}} blocks in instruction ───────────────────────
            instruction = task.get('instruction', '')
            resolved, error = _resolve_instruction(instruction)

            if error:
                log.error(f"[TaskThread.run] Code block error in '{task['name']}': {error[:200]}")
                err_msg = (
                    f"⚠️ **Task '{task['name']}' — code block error, ping skipped.**\n\n"
                    f"```\n{error}\n```"
                )
                try:
                    self._send_main(err_msg)
                except Exception as e:
                    log.error(f"[TaskThread.run] Failed to forward error: {e}")
                continue

            # ── Rebuild system prompt now (picks up any user changes since last ping) ─
            system_prompt = self._build_system_prompt()

            # ── Build history BEFORE appending ping (generate_response appends it) ──
            api_history = self._build_api_history(session)

            # ── Append ping to session record ─────────────────────────────────
            ping_content = f"<SYSTEM_AUTOMATED_TASK_PING>\nTHIS IS AN AUTOMATED " \
                           f"SYSTEM MESSAGE, YOUR TASK IS STATED BELOW:\n\n{resolved}\n{_now_stamp()}\n\n" \
                           f"</SYSTEM_AUTOMATED_TASK_PING>"
            session['chat_history'].append({'role': 'system', 'content': ping_content})

            # ── Resolve configured iteration limit ────────────────────────────
            _unlimited = task.get('unlimited_work_iterations', False)
            _max_iter  = 999999 if _unlimited else task.get('max_work_iterations', 20)

            # ── Call AI with full tool support (work_environment, execute_code, etc.) ──
            response_text, send_main_calls, _ = self._ai.run_full_ping(
                ping_content, api_history, system_prompt, max_iterations=_max_iter
            )

            if response_text is None:
                log.warning(f"[TaskThread.run] AI returned None for task '{task['name']}'")
                continue

            # ── Dispatch all send_message_main calls ──────────────────────────
            for send_msg in send_main_calls:
                try:
                    self._send_main(send_msg)
                    log.info(f"[TaskThread.run] send_message_main dispatched")
                except Exception as e:
                    log.error(f"[TaskThread.run] send_message_main failed: {e}")

            # ── Append stamped response to session ────────────────────────────
            stamped = f"{response_text}\n{_now_stamp()}"
            session['chat_history'].append({'role': 'assistant', 'content': stamped})
            self._save_session(session, task_id, today)
            log.info(f"[TaskThread.run] Ping complete for task='{task['name']}'")

        log.info(f"[TaskThread.run] Thread exiting for task='{task['name']}'")


# ── Task Manager ──────────────────────────────────────────────────────────────

class TaskManager:
    """
    Loads/saves tasks.json and owns one TaskThread per task.
    Call start() once on app launch.
    """

    TASKS_FILE = _APP_ROOT / "data" / "tasks" / "tasks.json"

    def __init__(self, controller):
        self._controller = controller
        self._tasks: list = []
        self._threads: dict = {}           # task_id → TaskThread
        self.TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load_tasks()
        log.info(f"[TaskManager.__init__] Ready | {len(self._tasks)} task(s)")

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_tasks(self):
        if not self.TASKS_FILE.exists():
            self._tasks = []
            return
        try:
            with open(self.TASKS_FILE, 'r', encoding='utf-8') as f:
                self._tasks = json.load(f)
        except Exception as e:
            log.error(f"[TaskManager._load_tasks] ✗ {e}")
            self._tasks = []

    def _save_tasks(self):
        try:
            tmp = self.TASKS_FILE.with_suffix('.json.tmp')
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._tasks, f, indent=2, ensure_ascii=False)
            tmp.replace(self.TASKS_FILE)
        except Exception as e:
            log.error(f"[TaskManager._save_tasks] ✗ {e}")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def get_tasks(self) -> list:
        return list(self._tasks)

    def add_task(self, task_dict: dict) -> dict:
        task_dict['id'] = str(uuid.uuid4())
        task_dict['created_at'] = datetime.now().isoformat()
        self._tasks.append(task_dict)
        self._save_tasks()
        self._start_thread(task_dict)
        log.info(f"[TaskManager.add_task] ✓ '{task_dict['name']}'")
        return task_dict

    def update_task(self, task_id: str, updated: dict) -> bool:
        for i, t in enumerate(self._tasks):
            if t['id'] == task_id:
                updated['id'] = task_id
                updated['created_at'] = t.get('created_at', datetime.now().isoformat())
                self._tasks[i] = updated
                self._save_tasks()
                self._stop_thread(task_id)
                self._start_thread(updated)
                log.info(f"[TaskManager.update_task] ✓ '{updated['name']}'")
                return True
        return False

    def delete_task(self, task_id: str) -> bool:
        self._stop_thread(task_id)
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t['id'] != task_id]
        if len(self._tasks) < before:
            self._save_tasks()
            return True
        return False

    # ── Thread management ─────────────────────────────────────────────────────

    def set_task_inactive(self, task_id: str):
        """Mark a task as inactive in storage. Called by TaskThread after a one-shot fires."""
        for task in self._tasks:
            if task['id'] == task_id:
                task['active'] = False
                self._save_tasks()
                log.info(f"[TaskManager.set_task_inactive] Task '{task['name']}' set to inactive")
                return

    def _start_thread(self, task: dict):
        if not task.get('active', True):
            log.info(f"[TaskManager._start_thread] Skipping inactive task '{task['name']}'")
            return
        t = TaskThread(
            task=task,
            controller=self._controller,
            skill_manager=getattr(self._controller, 'skill_manager', None),
            send_main_callback=self._send_to_main,
            set_inactive_callback=self.set_task_inactive,
        )
        t.start()
        self._threads[task['id']] = t

    def _stop_thread(self, task_id: str):
        t = self._threads.pop(task_id, None)
        if t:
            t.stop()

    def start(self):
        """Start all task threads. Call once after controller is ready."""
        for task in self._tasks:
            self._start_thread(task)
        log.info(f"[TaskManager.start] ✓ {len(self._tasks)} thread(s) started")

    def stop(self):
        for task_id in list(self._threads.keys()):
            self._stop_thread(task_id)

    # ── Session file API (used by the UI window) ──────────────────────────────

    def get_task_sessions(self, task_id: str) -> list:
        """Returns list of date strings (YYYY-MM-DD) that have saved sessions."""
        folder = _APP_ROOT / "data" / "task-sessions" / task_id
        if not folder.exists():
            return []
        return sorted([p.stem for p in folder.glob("*.json")], reverse=True)

    def load_task_session(self, task_id: str, date_str: str) -> dict | None:
        p = _APP_ROOT / "data" / "task-sessions" / task_id / f"{date_str}.json"
        if not p.exists():
            return None
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def delete_task_session(self, task_id: str, date_str: str) -> bool:
        p = _APP_ROOT / "data" / "task-sessions" / task_id / f"{date_str}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def delete_all_task_sessions(self, task_id: str):
        folder = _APP_ROOT / "data" / "task-sessions" / task_id
        if folder.exists():
            for f in folder.glob("*.json"):
                f.unlink()

    # ── Bridge to main session ────────────────────────────────────────────────

    def _send_to_main(self, message: str):
        """
        Injects a message into the main conversation as an assistant turn.
        Emits task_message_signal so the chat window renders it on the main thread.
        Called from background TaskThread — the signal hop makes it Qt-safe.
        """
        try:
            ctrl = self._controller
            stamped = f"[Task Notification]: {message}"
            ctrl.ai.conversation_history.append({'role': 'assistant', 'content': stamped})
            if hasattr(ctrl, '_auto_save_session'):
                ctrl._auto_save_session()
            if hasattr(ctrl, 'task_message_signal'):
                ctrl.task_message_signal.emit(message)
            log.info(f"[TaskManager._send_to_main] ✓ | preview='{message[:60]}'")
        except Exception as e:
            log.error(f"[TaskManager._send_to_main] ✗ {e}")

