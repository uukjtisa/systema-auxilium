"""
core/controller.py
Main Controller - Orchestrates the AI assistant with Voice Support
MAIN INITIATOR FOR ALL UI AND CORE MODULES
UPDATED: python_interpreter integration, voice mode, device management, TTS control
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from systema.engine.ai_engine import AIEngine
from systema.common.system_info import get_system_info, format_system_info_for_prompt
from systema.agents.skill_manager import SkillManager
from systema.ui.windows.floating_window import FloatingWindow
from systema.engine.ai_worker import AIWorker
from systema.memory.session_manager import SessionManager
import os
import threading
import tkinter as tk
from systema.common.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("Controller") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
# APP_ROOT is defined once in src/__init__.py (parent of the package dir).
from systema import APP_ROOT as _APP_ROOT
from systema.common import app_config as _app_config
# ─────────────────────────────────────────────────────────────────────────────

# ── Unified restart relauncher ───────────────────────────────────────────────
# The SINGLE relaunch path shared by every restart caller (update apply, Manage
# apply, the floating-window menu, the crash notice's Force restart). The body
# now lives in systema/common/relauncher.py — a stdlib-only module so the tiny
# tkinter notice process and the crash watcher can relaunch WITHOUT importing
# this controller (PyQt + engine + providers). Keep the old private name as the
# in-module alias so existing call sites read unchanged.
from systema.common.relauncher import spawn_relauncher as _spawn_relauncher


# Single source of truth, shared with the greeting banner — two separate lists
# of placeholder names WILL drift, and the failure is silent (the assistant
# starts addressing somebody called "USER").
from systema.common.greeting import PLACEHOLDER_NAMES as _PLACEHOLDER_NAMES


def build_identity_block(assistant_name: str = "", user_name: str = "",
                         custom_instructions: str = "") -> str:
    """The identity half of the system prompt: who the assistant is, who the
    user is, and any custom instructions.

    A pure function of its three inputs — no app state, no I/O — so the exact
    wording the model receives can be asserted directly.
    """
    out = ""

    # ── who the assistant is ─────────────────────────────────────────────────
    if assistant_name:
        out += f"""

**CUSTOM ASSISTANT IDENTITY:**
Your name is **{assistant_name}**. This is the name given to you by the user and it is your primary identity.
- Always introduce yourself as {assistant_name}, never as "Systema Auxilium" unprompted.
- If asked who you are, say you are {assistant_name}.
- If the user asks whether you are Systema Auxilium, clarify: Systema Auxilium is the underlying agent platform you run on, but your identity as their personalized assistant is {assistant_name}.
- Never forget or deny your base architecture (Systema Auxilium), but your active name and personality is {assistant_name}.
- Example: "I'm {assistant_name}, your system assistant. (Systema Auxilium is the platform powering me.)"
"""
    else:
        out += """

**ASSISTANT IDENTITY:**
You are Systema Auxilium. If the user asks about your name, tell them it is Systema Auxilium — both your name and the platform you run on.
Let the user know they can give you a custom name from the sidebar (top-left ☰ menu → custom assistant name field).
"""

    # ── who the user is ──────────────────────────────────────────────────────
    # The shipped default used to be the literal placeholder "USER", which is
    # truthy — so a fresh install injected 'USER NAME: USER' plus "address the
    # user by their name", and the assistant genuinely greeted people as "USER".
    # A placeholder is not a name.
    name = (user_name or "").strip()
    if name and name.upper() not in _PLACEHOLDER_NAMES:
        out += f"\n\n**USER NAME:** {name}\nAddress the user by their name when appropriate."
    else:
        out += """

**USER NAME: NOT SET YET**
You do not know this person's name. Address them directly and naturally ("you"),
the way you would talk to someone whose name simply has not come up yet.
- NEVER call them "USER", "user", "Human", or any other placeholder. It is not a
  name and it reads as robotic.
- Do NOT invent a name for them, and do NOT open by demanding one.
- If it comes up naturally — or if they ask how to personalise things — you may
  mention ONCE, briefly and without nagging, that the sidebar lets them set their
  own name, give you a custom name, and write custom instructions that shape your
  personality and how you work. Frame it as entirely optional: they lose nothing
  by ignoring it."""

    if custom_instructions:
        out += f"\n\n**CUSTOM USER INSTRUCTIONS:**\n{custom_instructions}"
    return out


class AssistantController(QObject):
    """Main controller for the AI assistant with voice support"""

    log_signal = pyqtSignal(str, str)
    voice_message_signal = pyqtSignal(str)
    # Voice state changes originate on VoiceHandler worker threads — marshal
    # them to the GUI thread before touching any widget.
    voice_state_signal = pyqtSignal(str)

    # Manual provider — fires on the main thread to show the response popup
    # Only carries display data; result_holder + done_event live on self
    manual_response_signal = pyqtSignal(str, bool, str)
    bridge_send_signal = pyqtSignal(str)
    bridge_user_bubble_signal = pyqtSignal(str)
    bridge_load_session_signal = pyqtSignal(str)
    bridge_new_session_signal = pyqtSignal()
    bridge_attach_image_signal = pyqtSignal(object)  # list[str] — paths to pin on main thread
    task_message_signal = pyqtSignal(str)  # fires when a task sends a message to main
    bridge_detach_image_signal = pyqtSignal(str)  # path to unpin on main thread
    bridge_run_on_main = pyqtSignal(object)  # run an arbitrary callable on the GUI thread

    def __init__(self):
        super().__init__()
        log.info("[AssistantController.__init__] ── Initializing AssistantController ──────────────")

        # Settings — the 'settings' section of the consolidated settings.json
        # (common/app_config.py; legacy assistant_settings.json auto-migrates)
        self.settings_file = _app_config.CONFIG_FILE
        log.debug(f"[AssistantController.__init__] Loading settings from '{self.settings_file}'")
        self.settings = self.load_settings()
        log.info(f"[AssistantController.__init__] Settings loaded | "
                 f"provider='{self.settings.get('ai_provider')}' | ")

        # Detect system information
        self.log("Detecting system information...")
        log.debug("[AssistantController.__init__] Calling get_system_info()...")
        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)
        self.log(f"System detected: {system_info_dict['os']} {system_info_dict['os_release']}")
        log.info(f"[AssistantController.__init__] System info formatted | "
                 f"os='{system_info_dict['os']} {system_info_dict['os_release']}' | "
                 f"text_len={len(system_info_text)}")

        # Voice handler — import deferred so numpy/sounddevice/pygame don't
        # block the Qt app from starting while controller.py is being imported
        log.debug("[AssistantController.__init__] Creating VoiceHandler...")
        from systema.voice.voice_handler import VoiceHandler
        self.voice_handler = VoiceHandler(log_callback=self.log)
        self.voice_mode_active = False
        log.debug("[AssistantController.__init__] VoiceHandler created | voice_mode_active=False")

        # Load ElevenLabs settings into voice handler
        elevenlabs_enabled = self.settings.get('elevenlabs_enabled', False)
        elevenlabs_voice_id = self.settings.get('elevenlabs_voice_id', '')
        self.voice_handler.set_elevenlabs_settings(elevenlabs_enabled, elevenlabs_voice_id)
        log.debug(f"[AssistantController.__init__] ElevenLabs settings applied: "
                  f"enabled={elevenlabs_enabled} | has_voice_id={bool(elevenlabs_voice_id)}")

        # Set voice callbacks
        self.voice_handler.on_transcription = self.handle_voice_transcription
        self.voice_handler.on_state_change = self.handle_voice_state_change
        log.debug("[AssistantController.__init__] Voice callbacks wired")

        # Initialize SkillManager
        log.debug("[AssistantController.__init__] Creating SkillManager...")
        _skills_dir = _APP_ROOT / "skills"
        self.skill_manager = SkillManager(_skills_dir)
        # Delay file-system watcher so it doesn't compete with startup I/O
        QTimer.singleShot(500, self.skill_manager.start_watching)
        log.info(f"[AssistantController.__init__] SkillManager created | dir='{_skills_dir}' | "
                 f"watcher deferred 500ms")

        # Initialize AI engine
        log.debug("[AssistantController.__init__] Creating AIEngine...")
        self.ai = AIEngine(
            log_callback=self.log,
            system_info=system_info_text,
            voice_mode=False,  # Start with voice off
            elevenlabs_enabled=self.settings.get('elevenlabs_enabled', False),
            settings_callback=lambda: self.settings,  # Pass settings getter
            skill_manager=self.skill_manager,
            controller = self
        )
        log.info("[AssistantController.__init__] AIEngine created")

        # Wire skills changes to refresh memory block in system prompt
        self.skill_manager.skills_changed.connect(self.refresh_memory_block)
        self.skill_manager.loaded_skills_changed.connect(self.refresh_memory_block)

        # Wire chat window bridge into tool_manager so it can call add_system_message etc.
        self.ai.tool_manager._get_chat = lambda: self._chat
        self.ai.tool_manager._get_android_bridge = lambda: getattr(getattr(self, 'ui', None), 'android_bridge', None)

        # Track active code execution for interrupt button state
        self.ai.tool_manager.approval_signal.work_code_active.connect(self._on_work_code_active)
        self.ai.tool_manager.approval_signal.work_narration.connect(self._on_work_narration)

        # ── Agent Image Attach ───────────────────────────────────────
        # Exposes attach_image() and take_screenshot() into the Python interpreter
        # namespace so the AI can call them from code execution on any thread.
        # Uses bridge_attach_image_signal to safely hop to the main Qt thread.

        def _agent_attach_image(path_or_paths, annotation=''):
            """Put image(s) INTO the chat as assistant-side attachments.
            Can be called from any thread (uses Qt signal for thread safety).

            They become numbered images in the transcript, sharing ONE counter
            with the user's own attachments — so "image 3" means the same
            picture whoever added it.

            Usage inside code execution:
                attach_image_to_chat(r"C:\\path\\to\\image.png")
                attach_image_to_chat([r"C:\\img1.png", r"C:\\img2.png"])
            """
            if isinstance(path_or_paths, str):
                paths = [path_or_paths]
            else:
                paths = list(path_or_paths)
            self.bridge_attach_image_signal.emit(
                {'paths': paths, 'origin': 'agent',
                 'annotation': annotation or ''})
            return f"[attach_image] Queued {len(paths)} image(s) for attachment."

        def _agent_take_screenshot(save_path=None):
            """Take a screenshot of the current screen.
            Saves to app's own data/temp/ folder (never the Windows temp folder).
            Returns the path — use attach_image_to_chat(path) to pin it to chat.

            Args:
                save_path: optional path to save screenshot (default: auto file in data/temp/)

            Returns:
                str: path to the saved screenshot file

            Usage inside code execution:
                path = take_screenshot()
                attach_image_to_chat(path)
            """
            import uuid
            try:
                from PIL import ImageGrab
                if save_path is None:
                    _temp_dir = _APP_ROOT / "data" / "temp"
                    _temp_dir.mkdir(parents=True, exist_ok=True)
                    _unique = uuid.uuid4().hex[:12]
                    save_path = str(_temp_dir / f"screenshot_{_unique}.png")
                img = ImageGrab.grab()
                img.save(save_path)
                return save_path
            except ImportError:
                # Fallback: use pyautogui if Pillow/ImageGrab unavailable
                try:
                    import pyautogui, uuid
                    if save_path is None:
                        _temp_dir = _APP_ROOT / "data" / "temp"
                        _temp_dir.mkdir(parents=True, exist_ok=True)
                        _unique = uuid.uuid4().hex[:12]
                        save_path = str(_temp_dir / f"screenshot_{_unique}.png")
                    pyautogui.screenshot(save_path)
                    return save_path
                except Exception as e2:
                    return f"[take_screenshot] ERROR: {e2}"
            except Exception as e:
                return f"[take_screenshot] ERROR: {e}"

        def _agent_attach_image_to_context(path):
            """Feed an image to the AI as PRIVATE, one-turn context (NOT pinned to
            the chat, not shown to the user). It is sent to the provider on the
            next interpreter step, then DETACHED FROM CONTEXT — the tokens are
            spent exactly once. The file on disk is never modified or removed.
            Requires a provider that supports image analysis.

            Usage inside code execution:
                attach_image_to_context(r"C:\\path\\to\\image.png")
            """
            import os
            if not os.path.isfile(path):
                return f"[attach_image_to_context] File not found: {path}"
            if not self.ai.supports_image_analysis():
                return ("[attach_image_to_context] The active provider cannot accept "
                        "images. Use attach_image_to_chat() to pin the image for the "
                        "user instead.")
            self.ai.queue_context_image(path)
            return (f"[attach_image_to_context] Queued for ONE-TURN analysis: {path}. "
                    "Describe what you see in your very next reply — it is detached "
                    "from your context after this turn.")

        self._agent_attach_image = _agent_attach_image
        self._agent_take_screenshot = _agent_take_screenshot
        self._agent_attach_image_to_context = _agent_attach_image_to_context
        # Delivery binding for the attach_image_to_chat TOOL surface. Both
        # surfaces (tool call and in-python call) go through this one function,
        # so they cannot diverge — the dual-surface rule from the capability
        # manifest. The tasker supplies its own binding for the same capability.
        self.ai.tool_manager._attach_image_binding = _agent_attach_image
        # Guarantee namespaces are re-injected on ANY interpreter reset path.
        self.ai.tool_manager._namespace_injector = self._inject_interpreter_namespaces
        self._inject_interpreter_namespaces()

        # Apply settings
        log.debug("[AssistantController.__init__] Applying AI provider settings...")
        self.ai.set_provider(self.settings.get('ai_provider', 'manual'))
        self.ai.set_tool_execution_lockout(self.settings.get('tool_execution_lockout', False))
        self.ai.set_system_prompt_hijack(
            self.settings.get('system_prompt_hijacked', False),
            self.settings.get('custom_system_prompt', '')
        )
        self.ai.set_system_prompt_extras(
            include_image_tools=self.settings.get('include_image_tools', False),
            include_controller_ref=self.settings.get('include_controller_ref', False),
            include_notify_tool=self.settings.get('include_notify_tool', False),
        )

        self.ai.set_tts_provider(self.settings.get('tts_provider', 'edge-tts'))
        self.ai.set_custom_script_path(self.settings.get('custom_script_path', ''))
        self._update_system_prompt()
        log.debug("[AssistantController.__init__] All AI settings applied")

        # Initialize UI
        log.debug("[AssistantController.__init__] Creating FloatingWindow UI...")
        self.ui = FloatingWindow(self)
        log.debug("[AssistantController.__init__] FloatingWindow created")

        # Tool mode timer
        self.work_timer = QTimer()
        self.work_timer.timeout.connect(self.auto_continue_work)
        self.work_timer.setInterval(1000)
        log.debug("[AssistantController.__init__] work_timer created | interval=1000ms")

        # Live work-mode output streaming — polls the interpreter buffer while code runs
        self._live_output_timer = QTimer()
        self._live_output_timer.timeout.connect(self._poll_live_output)
        self._live_output_timer.setInterval(120)
        log.debug("[AssistantController.__init__] live_output_timer created | interval=120ms")

        # Track processing
        self.is_processing = False
        self._request_generation = 0  # incremented on each new request and on cancel

        # Connect voice message signal to handler (main thread safe)
        self.voice_message_signal.connect(self._handle_voice_message_on_main_thread)
        self.voice_state_signal.connect(self._handle_voice_state_on_main_thread)
        log.debug("[AssistantController.__init__] voice_message_signal connected to main thread handler")

        # Connect
        self.bridge_send_signal.connect(self.send_message)
        self.bridge_user_bubble_signal.connect(self._add_user_bubble_to_chat)
        self.bridge_load_session_signal.connect(self.load_session)
        self.bridge_new_session_signal.connect(self.create_new_session)
        self.bridge_attach_image_signal.connect(self._handle_bridge_attach_image)
        self.bridge_detach_image_signal.connect(self._handle_bridge_detach_image)
        self.bridge_run_on_main.connect(lambda fn: fn())
        self.task_message_signal.connect(self._handle_task_message)
        self.ai.manual_response_fn = self._request_manual_response
        self.manual_response_signal.connect(self._show_manual_response_window)
        log.debug("[AssistantController.__init__] manual_response_signal connected")

        # SESSION MANAGEMENT - Initialize session manager
        log.debug("[AssistantController.__init__] Initializing SessionManager...")
        self.session_manager = SessionManager()
        self.current_session_id = None
        self._settings_batch = False   # suppresses redundant disk writes during a Settings save
        # Background auto-naming (SessionNamerAgent): per-session user-turn count,
        # sessions the user renamed by hand (never auto-overwrite), sessions with a
        # naming call in flight, and live worker refs.
        self._session_user_turns = {}
        self._session_manually_named = set()
        self._autoname_in_flight = set()
        self._autoname_threads = set()
        self.session_has_messages = False

        # Create initial session
        self._create_new_session()
        log.debug(f"[AssistantController.__init__] Initial session created: '{self.current_session_id}'")

        # PATH SYNCER + MEMORY MANAGER — deferred until after the UI is painted
        # so the floating window appears immediately at startup
        self.memory_manager = None  # will be set by _deferred_bg_init
        self.updater_service = None  # will be set by _deferred_bg_init
        self._update_window = None   # lazily created by open_update_window()
        QTimer.singleShot(300, self._deferred_bg_init)
        # GUI-thread hitch detector — logs a WARNING with the blocked duration
        # whenever the event loop stalls (see common/perf_monitor.py). Always on.
        try:
            from systema.common.perf_monitor import HitchMonitor
            self._hitch_monitor = HitchMonitor(self)
            self._hitch_monitor.start()
        except Exception as e:
            log.warning(f"[AssistantController.__init__] HitchMonitor failed (non-fatal): {e}")
        self.log("System AI Assistant initialized", "SUCCESS")
        log.info(f"[AssistantController.__init__] ✓ AssistantController ready | "
                 f"session='{self.current_session_id}' ──")

    # ── EXTRA INIT METHODS (Separated to load the UI faster) ───────────────────────────────────────────────────

    def _deferred_bg_init(self):
        """Heavy background services — runs after the UI is visible."""
        log.info("[AssistantController._deferred_bg_init] Starting deferred background init...")

        # PATH SYNCER
        try:
            from systema.execution.path_syncer import get_syncer
            get_syncer().start()
            log.info("[AssistantController._deferred_bg_init] ✓ PathSyncer started")
        except Exception as e:
            log.warning(f"[AssistantController._deferred_bg_init] PathSyncer failed (non-fatal): {e}")

        # MEMORY MANAGER
        try:
            from systema.memory.memory_manager import get_memory_manager
            self.memory_manager = get_memory_manager()
            log.info(f"[AssistantController._deferred_bg_init] ✓ MemoryManager ready | "
                     f"count={self.memory_manager.count()} | is_ready={self.memory_manager.is_ready}")
        except Exception as e:
            log.error(f"[AssistantController._deferred_bg_init] ✗ MemoryManager failed: {e}")
            self.memory_manager = None

        # TASK MANAGER — start after memory manager so settings are ready
        try:
            from systema.agents.task_manager import TaskManager
            self.task_manager = TaskManager(self)
            self.task_manager.start()
            log.info("[AssistantController._deferred_bg_init] ✓ TaskManager started")
        except Exception as e:
            log.error(f"[AssistantController._deferred_bg_init] ✗ TaskManager failed: {e}")
            self.task_manager = None

        # UPDATER SERVICE — self-update via the gitplucker library (optional dep)
        try:
            from systema.updater.service import UpdaterService
            self.updater_service = UpdaterService(self)
            self.updater_service.update_available.connect(self._on_update_available)
            # Establish a merge baseline once (background) so 3-way review works
            # from first install; skips the dev working copy. Then the update probe.
            QTimer.singleShot(2500, self.updater_service.maybe_seed_baseline_on_startup)
            QTimer.singleShot(4000, self.updater_service.check_startup_notify)
            log.info("[AssistantController._deferred_bg_init] ✓ UpdaterService ready")
        except Exception as e:
            log.warning(f"[AssistantController._deferred_bg_init] UpdaterService failed (non-fatal): {e}")
            self.updater_service = None

        log.info("[AssistantController._deferred_bg_init] ✓ Deferred init complete")

    # ── Software updates ───────────────────────────────────────────────────────

    # ═══════════════════════════════════════════════════════════════════════
    # THE EXIT PIPELINE — one gate, one teardown, every caller
    # ═══════════════════════════════════════════════════════════════════════
    # Restart and Shutdown differ by exactly one step (spawning a relauncher),
    # so they are ONE function with a mode rather than two flows that drifted.
    #
    # They HAD drifted, and it cost a cancelled restart: the old restart_app
    # spawned the relauncher and armed a 3s os._exit BEFORE calling
    # ui.shutdown_app(), which is where the "a response is still generating,
    # continue?" prompt lived. Declining it returned early exactly as written —
    # but a detached relauncher was already waiting on this pid and the kill
    # timer was already ticking, so the app restarted anyway.
    #
    # RULE: nothing irreversible happens until the exit has been agreed.
    #   1. re-entrancy guard   2. confirm ONCE   3. commit   4. tear down

    def request_exit(self, mode: str = "shutdown", confirm: bool = True) -> bool:
        """THE entry point for quitting. `mode` is 'shutdown' or 'restart'.

        Returns True when the exit is going ahead. `confirm=False` is for
        callers that have already asked (they own the decision); it never
        skips the teardown, only the prompt.
        """
        import os
        from pathlib import Path

        from systema import APP_ROOT

        if getattr(self, "_exit_in_progress", False):
            return True                     # already going down; don't re-ask
        mode = "restart" if str(mode).lower() == "restart" else "shutdown"

        # ── 1. Confirm, once, in one place ───────────────────────────────────
        if confirm and not self._confirm_exit(mode):
            log.info(f"[AssistantController.request_exit] {mode} cancelled at the "
                     f"busy prompt — nothing spawned, nothing torn down")
            return False

        # ── 2. Commit ────────────────────────────────────────────────────────
        if mode == "restart":
            # Single-instance: the new process must not start until this one has
            # exited and released the lock, so the relauncher waits on our pid.
            if not _spawn_relauncher(os.getpid(), Path(APP_ROOT)):
                log.error("[AssistantController.request_exit] relauncher would not "
                          "spawn — staying up rather than quitting into nothing")
                return False
            log.info(f"[AssistantController.request_exit] relauncher spawned "
                     f"(waits for pid {os.getpid()} to exit)")
            self._arm_force_exit()

        self._exit_in_progress = True

        # ── 3. Tear down (settings save + child windows + tray + quit) ───────
        try:
            ui = getattr(self, "ui", None)
            if ui is not None and hasattr(ui, "perform_teardown"):
                ui.perform_teardown()
            else:
                from PyQt6.QtWidgets import QApplication
                QApplication.quit()
        except Exception:
            log.error("[AssistantController.request_exit] teardown raised — "
                      "quitting anyway", exc_info=True)
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
        return True

    def _arm_force_exit(self, delay: float = 3.0):
        """Arm the fallback that hard-exits if the graceful quit stalls.

        If a lingering non-daemon thread or the tray outlives the polite Qt
        quit, the relauncher would sit forever waiting on a pid that never
        dies. A daemon timer cannot keep the process up by itself, so this only
        ever fires when something else already refused to.

        ITS OWN METHOD, deliberately: armed inline, a real 3-second
        `os._exit(0)` fires inside the TEST process too. It did — the suite was
        killed partway through by its own exit-pipeline tests, and because
        os._exit(0) reports success, the run looked like a clean pass while
        silently skipping most of it. Overriding one method is how a test
        asserts the fallback was armed without arming a live kill.
        """
        import os
        import threading

        timer = threading.Timer(delay, lambda: os._exit(0))
        timer.daemon = True
        timer.start()
        return timer

    def _confirm_exit(self, mode: str) -> bool:
        """The busy prompt, asked in exactly one place. True to proceed."""
        ui = getattr(self, "ui", None)
        hook = getattr(ui, "_confirm_exit_if_busy", None) if ui else None
        if not callable(hook):
            return True                     # headless / tests
        return bool(hook(mode.capitalize()))

    def restart_app(self, confirm: bool = True):
        """Relaunch and quit. Thin alias over the exit pipeline, kept because
        the updater and older call sites use this name."""
        return self.request_exit("restart", confirm=confirm)

    def shutdown_app(self, confirm: bool = True):
        """Quit. Thin alias over the exit pipeline."""
        return self.request_exit("shutdown", confirm=confirm)

    def agent_activity(self):
        """Return (busy, reason) describing whether the AI is mid-task.

        Used to warn before disruptive actions (e.g. applying an update, which
        rewrites source files the running agent may be using).
        """
        reasons = []
        try:
            tm = self.ai.tool_manager
            if getattr(tm.work, "is_working", False):
                reasons.append("the agent is in work mode")
            if getattr(tm.work.interpreter, "is_running", False):
                reasons.append("code execution is running")
        except Exception:
            pass
        if getattr(self, "is_processing", False):
            reasons.append("a response from the API is still pending")
        return bool(reasons), "; ".join(reasons)

    def exit_busy_reason(self):
        """Reason string if a restart/shutdown would cut off live activity, else None.

        Wraps agent_activity() and additionally counts pending voice output as
        activity (a restart mid-speech would cut the audio off abruptly)."""
        busy, reason = self.agent_activity()
        if busy:
            return reason
        try:
            vh = getattr(self, 'voice_handler', None)
            if vh is not None and (getattr(vh, 'speech_busy', False)
                                   or getattr(vh, 'is_speaking', False)):
                return "voice output is still playing"
        except Exception:
            pass
        if self.compaction_active():
            return "a toolcall compaction is still running"
        return None

    def graceful_stop_for_exit(self):
        """Drain all in-flight activity cleanly before a restart/shutdown.

        Reuses the existing interrupt mechanics: stop the work-mode timer,
        invalidate/terminate any in-flight worker, raise KeyboardInterrupt in
        actively running code (partial output preserved, work_code_active(False)
        fires so the live console is torn down), stop voice output, clear the
        processing/work-mode flags, and save the session.
        """
        log.info("[AssistantController.graceful_stop_for_exit] Draining activity before exit")
        try:
            self.work_timer.stop()
        except Exception:
            pass
        self._request_generation += 1  # discard any late worker response
        try:
            if getattr(self, 'current_worker', None) and self.current_worker.isRunning():
                log.warning("[AssistantController.graceful_stop_for_exit] Terminating in-flight worker")
                self.current_worker.terminate()
                self.current_worker.wait(1000)
        except Exception:
            pass
        try:
            tm = self.ai.tool_manager
            if getattr(tm.work.interpreter, 'is_running', False):
                log.warning("[AssistantController.graceful_stop_for_exit] Interrupting running code")
                tm.interrupt_running_code(
                    "Interrupted: the user is restarting or shutting down the app.")
            tm.work.reset()
        except Exception:
            pass
        self.is_processing = False
        try:
            vh = getattr(self, 'voice_handler', None)
            if vh is not None:
                if hasattr(vh, 'stop_all'):
                    vh.stop_all()
                else:
                    vh.interrupt_speech()
        except Exception:
            pass
        # Cancel any running compaction jobs — per-step saves preserve progress.
        try:
            self.compaction_manager.stop_all()
        except Exception:
            pass
        try:
            self._auto_save_session()
        except Exception:
            pass
        log.info("[AssistantController.graceful_stop_for_exit] ✓ Activity drained")

    def _on_update_available(self, has_update: bool, branch: str, commits: list = None):
        """Startup probe result — offer to open the updater if something is new.

        ``commits`` is the stacked list of pending commit messages (newest first)
        so the notification shows what actually changed, even when several updates
        piled up unapplied.
        """
        if not has_update:
            return
        svc = getattr(self, "updater_service", None)
        if svc is not None and not svc.notify_enabled:
            log.info("[AssistantController._on_update_available] suppressed (notifications off)")
            return
        try:
            from PyQt6.QtWidgets import QMessageBox
            commits = commits or []
            n = len(commits)
            headline = (f"A newer version of Systema Auxilium is available on '{branch}'."
                        if n <= 1 else
                        f"{n} updates have stacked up on '{branch}' since you last updated.")
            box = QMessageBox()
            box.setWindowTitle("Update available")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText(headline + "\n\nOpen the updater to review and choose what to apply?")
            if commits:
                # Show each commit's -m subject line, newest first (stacked).
                lines = []
                for c in commits[:12]:
                    subj = (c.get("message", "") or "").splitlines()[0] if c.get("message") else "(no message)"
                    lines.append(f"• {subj}")
                if n > 12:
                    lines.append(f"… and {n - 12} more")
                box.setDetailedText("Changes since your version:\n\n" + "\n".join(lines))
            open_btn = box.addButton("Open Updater", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
            box.setModal(False)
            box.finished.connect(
                lambda *_: self.open_update_window() if box.clickedButton() is open_btn else None)
            box.show()
            self._update_notif_box = box  # keep a reference so it isn't GC'd
        except Exception as e:
            log.error(f"[AssistantController._on_update_available] {e}")

    def open_update_window(self, parent=None):
        """Open the self-update dialog (Settings > System > Check for Updates).

        ``parent`` (the caller window, e.g. Settings) makes the update window a
        top-level owned by it, so it stacks ABOVE the caller instead of behind.
        """
        svc = getattr(self, "updater_service", None)
        if svc is None or not svc.available:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                parent, "Updater unavailable",
                "The self-updater isn't available.\n\n"
                "The 'updater-gitplucker' library must be installed in this "
                "environment:\n    pip install updater-gitplucker")
            return
        try:
            from systema.ui.windows.update_window import UpdateWindow
            # Recreate if missing or if the owning parent changed.
            if self._update_window is None or not self._update_window.isVisible():
                self._update_window = UpdateWindow(self, parent=parent)
            self._update_window.show()
            self._update_window.raise_()
            self._update_window.activateWindow()
        except Exception as e:
            log.error(f"[AssistantController.open_update_window] failed: {e}")

    # ── Convenience property ───────────────────────────────────────────────────

    @property
    def _chat(self):
        """
        Return the live ChatWindow instance, or None if it doesn't exist yet.

        Replaces the repetitive guard:
            if self._chat:
        with the cleaner:
            if self._chat:
                self._chat.some_method()
        """
        return getattr(getattr(self, 'ui', None), 'chat_window', None) or None

    def broadcast_theme(self, theme_key: str):
        """Live-apply a theme to every open window so the whole app stays in
        visual unity. Safe to call from the settings-save path; each window's
        apply_theme() is guarded so a missing/closed window is skipped.

        Windows live in two places: the FloatingWindow (settings, appearance,
        debug) and the ChatWindow (its cached task / memory sub-windows).
        """
        floating = getattr(self, 'ui', None)
        chat = self._chat

        targets = [chat, floating]
        if floating is not None:
            targets += [
                getattr(floating, 'settings_window', None),
                getattr(floating, 'appearance_window', None),
                getattr(floating, 'debug_window', None),
            ]
        if chat is not None:
            targets += [
                getattr(chat, '_tasks_window', None),
                getattr(chat, '_memory_window', None),
            ]

        for win in targets:
            if win is None:
                continue
            fn = getattr(win, 'apply_theme', None)
            if callable(fn):
                try:
                    fn(theme_key)
                except Exception as e:
                    log.error(f"[broadcast_theme] {type(win).__name__}: {e}")

    def refresh_open_settings_security(self):
        """Live-refresh an open Settings window's security policy combos after the
        approval dialog persists a policy change (ask->allow via "Always allow
        these operations"). No-op if Settings isn't open or lacks the hook. Called
        from the approval dialog on the GUI thread, so it is thread-safe."""
        floating = getattr(self, 'ui', None)
        win = getattr(floating, 'settings_window', None) if floating else None
        if win is None:
            return
        fn = getattr(win, 'refresh_security_policy_ui', None)
        if callable(fn):
            try:
                fn()
            except Exception as e:
                log.error(f"[refresh_open_settings_security] {type(win).__name__}: {e}")

    def log(self, message, level="INFO"):
        """Emit message to UI log panel."""
        self.log_signal.emit(message, level)

    def _add_user_bubble_to_chat(self, text):
        """Thread-safe slot: add user bubble to PC chat window from Android."""
        if self._chat:
            self._chat.add_user_message(text)

    # Provider scripts moved from `providers/` to `resources/providers/` on
    # 2026-07-28. The two script paths are stored ABSOLUTE, so an install that
    # updates across the move keeps pointing at the old location and the
    # provider silently stops loading. Repoint them on load, but only when the
    # old path is actually gone and the new one exists — a user who deliberately
    # keeps scripts outside the app must never be rewritten out from under.
    _MOVED_PATH_KEYS = ('custom_script_path', 'tts_script_path')

    def _adopt_moved_provider_paths(self, settings: dict) -> None:
        import re
        from pathlib import Path as _Path
        _pat = re.compile(r"(?<!resources[\\/])\bproviders([\\/])", re.IGNORECASE)
        changed = False
        for key in self._MOVED_PATH_KEYS:
            old = settings.get(key)
            if not isinstance(old, str) or not old:
                continue
            if _Path(old).is_file():
                continue                      # still resolves — leave it alone
            new = _pat.sub(lambda m: f"resources{m.group(1)}providers{m.group(1)}", old)
            if new != old and _Path(new).is_file():
                settings[key] = new
                changed = True
                log.info(f"[AssistantController] Repointed {key} to resources/providers/")
        if changed:
            try:
                _app_config.save_section('settings', settings)
            except Exception as e:
                log.debug(f"[AssistantController] provider-path adoption not persisted: {e}")

    def load_settings(self):
        log.debug(f"[AssistantController.load_settings] Loading from '{self.settings_file}'")
        try:
            settings = _app_config.load_section('settings')
            if settings:
                self._adopt_moved_provider_paths(settings)
                log.info(f"[AssistantController.load_settings] ✓ Settings loaded | "
                         f"provider='{settings.get('ai_provider')}' | "
                         f"has_api_key={bool(settings.get('api_key'))}")
                return settings
        except Exception as e:
            log.error(f"[AssistantController.load_settings] ✗ Error loading settings: "
                      f"{type(e).__name__}: {e}")

        # FIRST-TIME LAUNCH DEFAULTS
        log.info("[AssistantController.load_settings] No settings file found — using first-time defaults")
        default_tts = 'edge-tts'

        return {
            'ai_provider': None,
            'voice_input_device': None,
            'voice_output_device': None,
            'voice_setup_prompt_enabled': True,
            'voice_fillers_enabled': True,
            'voice_tts_provider': default_tts,
            'voice_tts_voice': 'en-US-GuyNeural',
            'tts_provider': default_tts,
            'tts_script_path': '',
            'voice_vad_aggressiveness': 3,
            'voice_interrupt_mode': 'manual',
            'voice_bargein_sensitivity': 'balanced',
            'voice_approval_enabled': True,
            'voice_approval_mode': 'basic',
            'voice_approval_announce': False,
            'voice_approval_confirm_risky': True,
            'voice_approval_custom_words': {},
            'approval_mini_enabled': True,
            'file_history_enabled': True,
            'file_history_git': False,
            'file_history_keep_days': 14,
            # Empty = not set. Never ship a placeholder here: it is truthy, so
            # it reached the prompt as a real name and the assistant addressed
            # people as "USER". (_update_system_prompt also treats the old
            # 'USER' value as unset, for installs that already saved it.)
            'user_name': '',
            'custom_instructions': '',
            'vad_webrtc_enabled': True,
            'vad_silero_enabled': False,
            'vad_aggressiveness': 3,
            'vad_silero_threshold': 0.5,
            # 'detailed' (full continuation ping) or 'compact' (shredded).
            # Re-sent every work step, so this trades tokens for guidance.
            'work_mode_prompt_style': 'detailed',
            'supervised_execution': True,  # Default ON for safety
            'memory_enabled': True,
            'memory_recall_mode': 'inject_all',  # 'inject_all' or 'rag'
            'memory_threshold': 0.4,  # float 0.0–1.0
            'memory_max_results': 3,
            'memory_inject_cap_tokens': 5000,  # hard cap for inject_all mode (presets 5k/9k/12k + Custom)
            'memory_embed_model': 'sentence-transformers/all-MiniLM-L6-v2',
            'glass_background_enabled': False,
            'glass_background_opacity': 0.75,
            'custom_script_path': '',
            'tool_execution_lockout': False,
            'system_prompt_hijacked': False,
            'custom_system_prompt': '',
            'tool_execution_timeout_seconds': 300
        }

    def save_settings(self):
        if getattr(self, '_settings_batch', False):
            # A batched Settings-window save is in flight — skip the repeated disk
            # write + memory-block rebuild that each set_*() would otherwise trigger.
            # The Settings window persists ONCE, off the GUI thread, at the end.
            return
        log.debug(f"[AssistantController.save_settings] Writing to '{self.settings_file}'")
        self.log("Saving settings...", "INFO")
        try:
            if not _app_config.save_section('settings', self.settings):
                raise OSError(f"save_section failed for '{self.settings_file}'")
            self.log("Settings saved", "SUCCESS")
            log.info(f"[AssistantController.save_settings] ✓ Settings saved to '{self.settings_file}'")
            self.refresh_memory_block()
        except Exception as e:
            log.error(f"[AssistantController.save_settings] ✗ Failed: {type(e).__name__}: {e}")
            self.log(f"Error saving settings: {e}", "ERROR")

    def enable_voice_mode(self):
        """Enable voice input/output"""
        log.info("[AssistantController.enable_voice_mode] ── Enabling voice mode ──────────────")
        # Idempotent: starting an already-active listener fails ("voice failed to
        # start"). If voice is already on (e.g. enabled from the phone), no-op.
        if getattr(self, 'voice_mode_active', False):
            log.info("[AssistantController.enable_voice_mode] Already active — skipping re-start")
            return True, ""
        try:
            # List available devices
            input_devices, output_devices = self.voice_handler.list_audio_devices()
            log.debug(f"[AssistantController.enable_voice_mode] Available devices: "
                      f"input={len(input_devices)} | output={len(output_devices)}")

            # Get device selection from settings
            input_device = self.settings.get('voice_input_device')
            output_device = self.settings.get('voice_output_device')
            log.debug(f"[AssistantController.enable_voice_mode] Device selection: "
                      f"input_device={input_device} | output_device={output_device}")

            # Set devices
            self.voice_handler.set_devices(input_device, output_device)

            # Set VAD configuration
            webrtc_enabled = self.settings.get('vad_webrtc_enabled', True)
            silero_enabled = self.settings.get('vad_silero_enabled', False)
            webrtc_aggressiveness = self.settings.get('vad_aggressiveness', 3)
            silero_threshold = self.settings.get('vad_silero_threshold', 0.5)
            log.debug(f"[AssistantController.enable_voice_mode] VAD config: "
                      f"webrtc={webrtc_enabled}(agg={webrtc_aggressiveness}) | "
                      f"silero={silero_enabled}(thresh={silero_threshold})")

            self.voice_handler.set_vad_configuration(
                webrtc_enabled,
                silero_enabled,
                webrtc_aggressiveness,
                silero_threshold
            )

            # Apply the saved interrupt mode — previously only pushed on a
            # Settings save, so a fresh session silently ran 'manual' and
            # auto-interrupt never fired until the user re-saved settings.
            self.voice_handler.set_interrupt_mode(
                self.settings.get('voice_interrupt_mode', 'manual'))
            self.voice_handler.set_bargein_sensitivity(
                self.settings.get('voice_bargein_sensitivity', 'balanced'))

            # Apply TTS provider settings
            tts_provider = self.settings.get('tts_provider', 'edge-tts')
            self.voice_handler.set_tts_provider(tts_provider)
            if tts_provider == 'custom_script':
                tts_script = self.settings.get('tts_script_path', '')
                self.voice_handler.set_tts_script_path(tts_script)
                log.debug(f"[AssistantController.enable_voice_mode] Custom TTS script: '{tts_script}'")

            # Set up playback callback with null check
            if self._chat:
                self.voice_handler.on_playback_started = self._chat.on_voice_playback_started
                self.log("Voice playback callback wired to chat window", "SUCCESS")
                log.debug("[AssistantController.enable_voice_mode] Playback callback wired to chat window")
            else:
                self.log("Chat window not available for voice callback", "WARNING")
                log.warning("[AssistantController.enable_voice_mode] Chat window not available for callback")

            log.debug("[AssistantController.enable_voice_mode] Starting voice listener...")
            if self.voice_handler.start_listening():
                self.voice_mode_active = True

                # Filler interjections (bridge chunk gaps in the same voice):
                # apply the toggle and warm the per-voice cache in the background.
                self.voice_handler.fillers_enabled = self.settings.get(
                    'voice_fillers_enabled', True)
                if self.voice_handler.fillers_enabled:
                    self.voice_handler.ensure_fillers_async()

                # Update AI engine with voice settings
                self.ai.update_voice_settings(True)

                self.log("Voice mode enabled", "SUCCESS")
                log.info("[AssistantController.enable_voice_mode] ✓ Voice mode active")
                return True, ""
            else:
                log.error("[AssistantController.enable_voice_mode] ✗ start_listening() returned False")
                return False, "Failed to start voice capture"

        except Exception as e:
            log.error(f"[AssistantController.enable_voice_mode] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Voice mode error: {e}", "ERROR")
            return False, f"Error: {e}"

    def disable_voice_mode(self):
        """Disable voice mode"""
        log.info("[AssistantController.disable_voice_mode] Disabling voice mode...")
        try:
            self.log("Disabling voice mode...")

            # Stop listening first
            log.debug("[AssistantController.disable_voice_mode] Stopping listener...")
            self.voice_handler.stop_listening()

            # Then clear the speech queue and interrupt any ongoing speech
            log.debug("[AssistantController.disable_voice_mode] Stopping speech queue...")
            self.voice_handler.stop_all()

            # Update state
            self.voice_mode_active = False

            # Update AI engine
            self.ai.update_voice_settings(False, False)

            self.log("Voice mode disabled", "SUCCESS")
            log.info("[AssistantController.disable_voice_mode] ✓ Voice mode disabled")
        except Exception as e:
            log.error(f"[AssistantController.disable_voice_mode] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Error disabling voice: {e}", "ERROR")

    def _request_manual_response(self, context: str, is_working: bool, work_output: str):
        """Called from the AIWorker thread — stores result_holder + done_event on
        self (so they stay as real references), then signals the main thread to
        show the popup and blocks until the user submits or cancels."""
        self._manual_result_holder = []
        self._manual_done_event = threading.Event()
        # Emit only display data — the window will read result_holder from self
        self.manual_response_signal.emit(context, is_working, work_output)
        # Block the worker thread until the window is dismissed (by PC or Android)
        self._manual_done_event.wait()
        result = self._manual_result_holder[0] if self._manual_result_holder else None
        # Dismiss Android's dialog regardless of who responded first
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.dismiss_manual_response()
        return result

    def _show_manual_response_window(self, context, is_working, work_output):
        """Slot — always runs on the main thread.  Creates and shows the popup."""
        from systema.ui.windows.manual_response_window import ManualResponseWindow
        win = ManualResponseWindow(context, is_working, work_output,
                                   self._manual_result_holder,
                                   self._manual_done_event)
        # Keep a reference so it isn't garbage-collected before the user responds
        self._manual_response_win = win
        win.show()
        win.raise_()
        win.activateWindow()

        # ── Also show on Android if a phone is connected ──────────────────────
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible() and getattr(ab, '_conn', None) is not None:
            def on_android_manual_response(text):
                """Called from recv_loop thread when Android submits or cancels."""
                if not self._manual_done_event.is_set():
                    self._manual_result_holder.append(text if text else None)
                    self._manual_done_event.set()
                    # Close the PC window from the main thread
                    from PyQt6.QtCore import QTimer
                    close_win = getattr(self, '_manual_response_win', None)
                    if close_win:
                        QTimer.singleShot(0, close_win.close)
            ab.request_manual_response(context, is_working, work_output, on_android_manual_response)

    def _handle_bridge_attach_image(self, payload):
        """Slot — always runs on main thread. Puts images into the transcript.

        Serves BOTH producers, which is why it takes a payload rather than a
        bare list: the Android app (a plain list of paths, treated as the
        user's own attachment) and the assistant's attach_image_to_chat (a dict
        that also carries origin and the annotation to caption it with).
        """
        if not self._chat:
            return
        if isinstance(payload, dict):
            paths = payload.get('paths') or []
            origin = payload.get('origin') or 'user'
            annotation = payload.get('annotation') or ''
        else:
            paths, origin, annotation = list(payload or []), 'user', ''
        try:
            self._chat.attach_images(paths, origin=origin, annotation=annotation)
        except Exception as e:
            log.error(f"[AssistantController._handle_bridge_attach_image] ✗ {e}",
                      exc_info=True)

    def _handle_bridge_detach_image(self, path):
        """Slot — always runs on main thread. Detaches an image the Android app
        dropped, by matching its cached path back to an image number."""
        if not self._chat or not path:
            return
        try:
            from systema.common import image_refs
            for ref in image_refs.all_refs(self.ai.conversation_history):
                if ref.get('path') == path or ref.get('name') == path:
                    self._chat.detach_image(ref.get('n'))
                    break
        except Exception as e:
            log.error(f"[AssistantController._handle_bridge_detach_image] ✗ {e}")

    def handle_voice_transcription(self, text):
        """Handle transcribed voice input - SIGNAL VERSION"""
        log.info(f"[AssistantController.handle_voice_transcription] Transcribed: '{text[:80]}'")
        self.log(f"Voice transcribed: {text}")
        # Emit signal - Qt will automatically marshal to main thread
        self.voice_message_signal.emit(text)

    def _handle_voice_message_on_main_thread(self, text):
        """Handle voice message on main Qt thread (slot for signal).

        Supports BARGE-IN: if a request is still in flight (LLM generating),
        speaking again cancels it and resends the previous prompt plus a newline
        plus the new transcript as ONE message — the existing user bubble grows
        a line instead of a duplicate bubble appearing."""
        log.debug(f"[AssistantController._handle_voice_message_on_main_thread] text='{text[:80]}'")
        # FIXED: Ensure chat window exists (create it if needed)
        if not hasattr(self.ui, 'chat_window') or self.ui.chat_window is None:
            from systema.ui.chat_window import ChatWindow
            self.ui.chat_window = ChatWindow(self)
            log.debug("[AssistantController._handle_voice_message_on_main_thread] "
                      "Chat window created for voice message buffering")
            self.log("Chat window created for voice message buffering")

        # ── Voice mode approval: an open code-approval dialog captures the
        # transcript (command words act; advanced mode chats with the reviewer).
        # dialog.exec() runs a nested event loop on this thread, so this slot
        # still fires mid-approval.
        try:
            dlg = getattr(self.ai.tool_manager, '_active_approval_dialog', None)
            if dlg is not None and dlg.isVisible():
                log.info("[AssistantController._handle_voice_message_on_main_thread] "
                         "Approval dialog open — routing transcript to it")
                dlg.handle_voice_transcript(text)
                return
        except Exception as e:
            log.warning(f"[AssistantController._handle_voice_message_on_main_thread] "
                        f"approval-dialog routing failed: {type(e).__name__}: {e}")

        # Flush any AI reply still buffered "until playback starts" — the
        # auto-interrupt that preceded this transcription may have killed its
        # playback during synthesis (idempotent; no-op when nothing waits).
        try:
            self._chat._handle_voice_playback_on_main_thread()
        except Exception:
            pass

        # A new voice message always halts current speech, in BOTH interrupt
        # modes — the user is addressing the bot; the old reply's audio must not
        # keep talking over the new exchange. (Auto mode additionally halts at
        # speech ONSET via the handler's auto-interrupt.)
        try:
            if self.voice_handler.speech_busy:
                log.info("[AssistantController._handle_voice_message_on_main_thread] "
                         "New voice message — stopping current speech")
                self.voice_handler.stop_all()
        except Exception:
            pass

        # ── Barge-in: cancel the in-flight request and merge prompts ─────────
        if (self.is_processing and not self.ai.tool_manager.work.is_working
                and getattr(self, '_last_sent_user_text', None)):
            prev = self._last_sent_user_text
            log.info("[AssistantController._handle_voice_message_on_main_thread] "
                     "Barge-in — interrupting in-flight request and merging prompts")
            self.log("Voice barge-in: canceling current request and appending new speech")
            self.interrupt_request()  # kills worker, pops last user msg from history, stops TTS
            combined = prev + "\n" + text
            appended = False
            try:
                appended = self._chat.append_to_last_user_message(text)
            except Exception:
                pass
            if not appended:
                self._chat.add_user_message(combined)
            self.send_message(combined)  # re-records _last_sent_user_text = combined
            return

        # Always add to chat window (even if hidden)
        self._chat.add_user_message(text)

        # Send to AI (works whether chat is visible or not)
        log.debug("[AssistantController._handle_voice_message_on_main_thread] → send_message()")
        self.send_message(text)

    def handle_voice_state_change(self, state):
        """VoiceHandler on_state_change callback — fires on voice worker threads,
        so marshal to the GUI thread via signal before touching widgets."""
        log.debug(f"[AssistantController.handle_voice_state_change] state='{state}'")
        self.voice_state_signal.emit(state)

    def _handle_voice_state_on_main_thread(self, state):
        """Apply a voice state change on the GUI thread."""
        self.log(f"Voice state: {state}")
        if self._chat:
            self._chat.update_voice_status(state)

    def wait_for_voice_completion(self):
        """Wait for current voice playback to complete"""
        log.debug(f"[AssistantController.wait_for_voice_completion] voice_mode_active={self.voice_mode_active}")
        if not self.voice_mode_active:
            return

        max_wait = 30  # Maximum 30 seconds
        waited = 0

        while self.voice_handler.is_speaking and waited < max_wait:
            import time
            time.sleep(0.1)
            waited += 0.1

        log.debug(f"[AssistantController.wait_for_voice_completion] Completed after {waited:.1f}s")
        self.log(f"Voice completed after {waited:.1f}s")

    def get_user_name(self):
        """Get user name"""
        return self.settings.get('user_name', '')

    def set_user_name(self, name):
        """Set user name"""
        log.info(f"[AssistantController.set_user_name] name='{name}'")
        self.settings['user_name'] = name
        self.save_settings()
        # Regenerate system info with new name
        self._update_system_prompt()
        self.log(f"User name set to: {name}", "SUCCESS")

    def get_assistant_name(self):
        """Get custom assistant name (empty = use default 'Systema Auxilium')"""
        return self.settings.get('assistant_name', '')

    def set_assistant_name(self, name):
        """Set custom assistant name"""
        log.info(f"[AssistantController.set_assistant_name] name='{name}'")
        self.settings['assistant_name'] = name.strip()
        self.save_settings()
        self._update_system_prompt()
        self.log(f"Assistant name set to: {name or '(default)'}", "SUCCESS")

    def get_custom_instructions(self):
        """Get custom instructions"""
        return self.settings.get('custom_instructions', '')

    def set_custom_instructions(self, instructions):
        """Set custom instructions"""
        log.info(f"[AssistantController.set_custom_instructions] len={len(instructions)} chars")
        self.settings['custom_instructions'] = instructions
        self.save_settings()
        # Regenerate system prompt
        self._update_system_prompt()
        self.log("Custom instructions updated", "SUCCESS")

    def _update_system_prompt(self):
        """Update system prompt with personalization"""
        log.info("[AssistantController._update_system_prompt] Rebuilding system prompt...")
        from systema.common.system_info import get_system_info, format_system_info_for_prompt

        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)

        system_info_text += build_identity_block(
            assistant_name=self.get_assistant_name(),
            user_name=self.get_user_name(),
            custom_instructions=self.get_custom_instructions(),
        )

        # Update AI engine
        self.ai.system_info = system_info_text
        self.ai.update_voice_settings(self.ai.voice_mode, self.ai.elevenlabs_enabled)
        self.ai._inject_memories_into_prompt()
        log.info("[AssistantController._update_system_prompt] ✓ System prompt updated")

    def build_task_system_prompt(self, task_dict: dict) -> str:
        """
        Centralized task system prompt builder — single source of truth.
        Called by TaskThread._build_system_prompt AND manage_tasks_window preview.

        Includes: system info, assistant name, user name, custom instructions,
                  task permissions, task context block, and any pre-loaded skills.

        Args:
            task_dict:    The task dict (must have 'permissions', 'name', 'loaded_skills').
            skill_loader: Optional callable(skill_name: str) -> str | None.
        Returns:
            Full system prompt string ready for the task agent.
        """
        log.info(f"[AssistantController.build_task_system_prompt] Building for task='{task_dict.get('name', '?')}'")
        from systema.engine.prompts.global_instructions import get_system_prompt as _gsp

        perms = task_dict.get('permissions', {})

        # ── System info: already contains assistant name, user name, custom instructions ──
        try:
            system_info = self.ai.system_info
        except Exception:
            system_info = ""

        _allow_workmode  = perms.get('allow_workmode',      False)
        _any_code        = _allow_workmode

        # Match the active Tool Calling Mode. Tasks run through their own AIEngine
        # whose _call_provider already dispatches natively when the setting is
        # 'native' — but the task prompt is hijacked, so if we DON'T build the
        # native variant here, the agent gets a fence-heavy prompt that fights the
        # native channel (fence leaks). Build the matching prompt so both agree.
        _native = (self.settings.get('tool_calling_mode', 'compat') == 'native')
        # Memory PARITY with the main session: inject-all recall mode drops
        # search_memory (redundant) for taskers too and injects the full block
        # below; RAG mode keeps search_memory and injects no block.
        _mem_inject_all = (self.settings.get('memory_enabled', True)
                           and self.settings.get('memory_recall_mode', 'inject_all') == 'inject_all')

        base_prompt = _gsp(
            is_task_session_prompt=True,
            system_info                = system_info,
            voice_mode                 = False,   # tasks are silent — no voice
            elevenlabs_enabled         = False,
            skills                     = None,    # task skills injected separately below
            include_memory             = True,    # memorize is always useful for tasks
            memory_inject_all          = _mem_inject_all,
            include_execution_tools    = _any_code,     # python interpreter section
            include_fence_syntax       = _any_code,     # fence syntax guide
            include_interpreter_mode_rules    = _allow_workmode,
            include_must_remember      = _any_code,     # reminder block references tools
            include_image_tools        = perms.get('inject_image_tools',    False),
            include_controller_ref     = perms.get('inject_controller_ref', False),
            include_notify_tool        = perms.get('inject_notify_tool',    False),
            native_tools               = _native,       # native mode → fence-free prompt
        )

        # ── Memory block (inject-all parity with the main session) ────────────
        # The main engine grafts this same block at the tail of its effective
        # prompt; reuse its builder so the tasker sees the identical memory set
        # the inject-all memory section refers to. RAG mode → no block.
        if _mem_inject_all and getattr(self, 'ai', None) is not None:
            try:
                _mem_block = self.ai._build_memory_block()
                if _mem_block:
                    base_prompt += _mem_block
            except Exception as _e:
                log.warning(f"[AssistantController.build_task_system_prompt] "
                            f"memory block skipped: {type(_e).__name__}: {_e}")

        # ── Permissions block ─────────────────────────────────────────────────
        perm_lines = []
        if perms.get('allow_workmode'):
            perm_lines.append("- You MAY use the python_interpreter tool to perform agentic tasks.")
            # Summarize the task's security policy so the agent knows its hard
            # limits up front. Denied categories are blocked non-interactively —
            # there is no one to approve a prompt in a background task.
            _policy = perms.get('task_security_policy')
            if isinstance(_policy, dict) and _policy:
                _denied = sorted(k for k, v in _policy.items() if v == 'deny')
                if _denied:
                    perm_lines.append(
                        "- Security policy for this task DENIES these operation "
                        "categories: " + ", ".join(_denied) + ". Any code or file "
                        "op needing one is BLOCKED automatically (no approval "
                        "prompt exists in a background task). Plan around them — "
                        "do not retry a blocked operation.")
                else:
                    perm_lines.append("- Security policy allows all operation categories.")
        if perms.get('allow_skill_load_unload'):
            perm_lines.append("- You MAY use load_skill and unload_skill.")
        if not perm_lines:
            perm_lines.append("- READ-ONLY mode. Do not attempt to execute code or use tools.")
        perm_block = '\n'.join(perm_lines)

        # ── Task context block ────────────────────────────────────────────────
        # How the agent reaches the user's main chat depends on whether it can run
        # code. With code execution it calls the send_message_main() function in its
        # Python namespace — works identically in compat AND native tool modes.
        # Read-only tasks (no code execution) fall back to the JSON-on-its-own-line
        # form since they can't call a Python function.
        if _any_code:
            send_main_block = (
                "Sending a message to the main chat session:\n"
                "  Call the send_message_main(message) function from inside python_interpreter. "
                "It is available in your Python namespace. Example:\n"
                "    send_message_main(\"Your Discord friend just messaged you.\")\n"
                "  Delivers immediately. Do NOT write it as a code fence or JSON in your reply "
                "text — just call the function in your executed code.\n\n"
            )
        else:
            send_main_block = (
                "To send a message to the main chat session, emit EXACTLY this JSON on its own line:\n"
                '{"tool": "send_message_main", "input": "your message to the user"}\n\n'
            )

        task_section = (
            f"\n\n=== BACKGROUND TASK CONTEXT ===\n"
            f"You are currently running as an automated background task agent — NOT in a live user conversation.\n"
            f"Task name: {task_dict.get('name', '?')}\n\n"
            f"Task Permissions:\n{perm_block}\n\n"
            f"{send_main_block}"
            f"Only message the user when something genuinely needs their attention.\n"
            f"Each ping message has a timestamp appended for your temporal awareness.\n"
            f"=== END BACKGROUND TASK CONTEXT ===\n"
        )

        # ── Skill injections — use controller's own skill_manager directly ────
        skills_section = ""
        loaded_skills = task_dict.get('loaded_skills', [])
        if loaded_skills and hasattr(self, 'skill_manager') and self.skill_manager:
            skill_injections = []
            for skill_name in loaded_skills:
                try:
                    content = self.skill_manager.get_skill_content(skill_name)
                    if content and not content.startswith("ERROR:"):
                        skill_injections.append(
                            f"\n\n=== PRE-LOADED SKILL: {skill_name} ===\n"
                            f"{content}\n"
                            f"=== END SKILL: {skill_name} ===\n"
                        )
                        log.info(f"[AssistantController.build_task_system_prompt] Injected skill '{skill_name}'")
                    else:
                        log.warning(f"[AssistantController.build_task_system_prompt] Skill '{skill_name}' not found — skipped")
                except Exception as _e:
                    log.warning(f"[AssistantController.build_task_system_prompt] Skill '{skill_name}' error: {_e}")
            skills_section = "".join(skill_injections)

        # ── Skill path rule — injected when task has preloaded skills ─────────
        # get_system_prompt() is called with skills=None for tasks (skills are
        # injected manually below), so the rule is never auto-generated there.
        # We generate it here explicitly when loaded_skills is non-empty.
        skill_path_rule_block = ""
        if loaded_skills:
            from systema.engine.prompts.global_instructions import get_skill_path_rule as _gpr
            skill_path_rule_block = "\n\n" + _gpr() + "\n\n"

        log.info(f"[AssistantController.build_task_system_prompt] ✓ Built | {len(base_prompt + skills_section + task_section):,} chars")
        return base_prompt + skill_path_rule_block + skills_section + task_section

    def speak_text(self, text):
        """Enqueue text on the serialized speech queue (voice mode only).

        Non-blocking; utterances play strictly one at a time. Interpreter-mode
        commentary is narrated too — callers must only ever pass the AI's
        message text here, never raw tool output or code."""
        log.debug(f"[AssistantController.speak_text] voice_mode_active={self.voice_mode_active} | "
                  f"text_preview='{text[:50]}'")
        if not self.voice_mode_active:
            return
        self.log(f"Queueing speech: {text[:50]}...")
        self.voice_handler.speak(text)

    def voice_busy(self):
        """True while any speech is pending (synthesizing, playing, or queued)."""
        try:
            return bool(self.voice_mode_active and self.voice_handler.speech_busy)
        except Exception:
            return False

    def get_voice_interrupt_mode(self):
        """Get voice interrupt mode"""
        return self.settings.get('voice_interrupt_mode', 'manual')

    def set_voice_interrupt_mode(self, mode):
        """Set voice interrupt mode ('auto' or 'manual')"""
        log.info(f"[AssistantController.set_voice_interrupt_mode] mode='{mode}'")
        self.settings['voice_interrupt_mode'] = mode
        self.voice_handler.set_interrupt_mode(mode)
        self.save_settings()
        self.log(f"Voice interrupt mode set to {mode}", "SUCCESS")

    def get_voice_bargein_sensitivity(self):
        """Get auto barge-in sensitivity preset"""
        return self.settings.get('voice_bargein_sensitivity', 'balanced')

    def set_voice_bargein_sensitivity(self, preset):
        """Set auto barge-in sensitivity ('relaxed' | 'balanced' | 'eager')"""
        log.info(f"[AssistantController.set_voice_bargein_sensitivity] preset='{preset}'")
        self.settings['voice_bargein_sensitivity'] = preset
        self.voice_handler.set_bargein_sensitivity(preset)
        self.save_settings()
        self.log(f"Barge-in sensitivity set to {preset}", "SUCCESS")

    def get_tts_provider(self):
        return self.settings.get('tts_provider', 'edge-tts')

    def set_tts_provider(self, provider):
        log.info(f"[AssistantController.set_tts_provider] provider='{provider}'")
        self.settings['tts_provider'] = provider
        self.ai.set_tts_provider(provider)
        self.save_settings()
        self.log(f"TTS provider set to {provider}", "SUCCESS")

    def get_llm_providers_folder(self):
        """Return absolute path to LLM providers folder, creating it if needed."""
        folder = _APP_ROOT / 'resources' / 'providers' / 'large-language-models'
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def get_tts_providers_folder(self):
        """Return absolute path to TTS providers folder, creating it if needed."""
        folder = _APP_ROOT / 'resources' / 'providers' / 'text-to-speech'
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def get_llm_provider_scripts(self):
        """Return list of dicts {name, path} for .py scripts in LLM providers folder."""
        import glob
        folder = self.get_llm_providers_folder()
        paths = sorted(glob.glob(os.path.join(folder, '*.py')))
        return [{'name': os.path.splitext(os.path.basename(p))[0], 'path': p} for p in paths]

    def get_tts_provider_scripts(self):
        """Return list of dicts {name, path} for .py scripts in TTS providers folder."""
        import glob
        folder = self.get_tts_providers_folder()
        paths = sorted(glob.glob(os.path.join(folder, '*.py')))
        return [{'name': os.path.splitext(os.path.basename(p))[0], 'path': p} for p in paths]

    def set_tts_script_path(self, path):
        """Persist and apply a custom TTS provider script path."""
        log.info(f"[AssistantController.set_tts_script_path] path='{path}'")
        self.settings['tts_script_path'] = path
        if hasattr(self, 'voice_handler') and self.voice_handler:
            self.voice_handler.set_tts_script_path(path)
        self.save_settings()
        self.log(f"TTS script path set to: {path}", "SUCCESS")

    def get_voice_devices(self):
        """Get available audio devices"""
        return self.voice_handler.list_audio_devices()

    def rescan_audio_devices(self):
        """Re-initialize PortAudio so hotplugged devices (e.g. Bluetooth earbuds)
        appear. Callers must stop any mic meter stream first. True on success."""
        return self.voice_handler.rescan_devices()

    def audio_hotplug_signature(self):
        """Live device-population probe (None where unsupported)."""
        return self.voice_handler.hotplug_signature()

    def set_voice_input_device(self, device_id):
        """Set voice input device"""
        self.settings['voice_input_device'] = device_id
        self.save_settings()
        self.log("Voice input device SAVED", "SUCCESS")

    def set_voice_output_device(self, device_id):
        """Set voice output device"""
        self.log(f"{self.settings}", "DEBUG")
        self.settings['voice_output_device'] = device_id
        self.save_settings()
        self.log("Voice output device SAVED", "SUCCESS")

    def set_ai_provider(self, provider):
        """Set AI provider"""
        log.info(f"[AssistantController.set_ai_provider] provider='{provider}'")
        self.settings['ai_provider'] = provider
        self.ai.set_provider(provider)
        self.save_settings()
        self.log(f"AI provider set to: {provider}", "SUCCESS")
        # Mirror to a connected phone so its Settings panel stays in sync
        _ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if _ab and _ab.isVisible():
            try:
                _ab._send_settings()
            except Exception:
                pass

    def get_custom_script_path(self):
        """Get custom script provider path"""
        return self.settings.get('custom_script_path', '')

    def set_custom_script_path(self, path):
        """Set custom script provider path"""
        log.info(f"[AssistantController.set_custom_script_path] path='{path}'")
        self.settings['custom_script_path'] = path
        self.ai.set_custom_script_path(path)
        self.save_settings()
        self.log(f"Custom script path set to: {path}", "SUCCESS")

    def set_tool_execution_lockout(self, value: bool):
        self.settings['tool_execution_lockout'] = value
        self.ai.set_tool_execution_lockout(value)
        self.save_settings()

    def set_system_prompt_hijack(self, enabled: bool, custom_prompt: str = ""):
        self.settings['system_prompt_hijacked'] = enabled
        self.settings['custom_system_prompt'] = custom_prompt
        self.ai.set_system_prompt_hijack(enabled, custom_prompt)

    def set_system_prompt_extras(self, include_image_tools: bool = False,
                                  include_controller_ref: bool = False,
                                  include_notify_tool: bool = False):
        self.settings['include_image_tools'] = include_image_tools
        self.settings['include_controller_ref'] = include_controller_ref
        self.settings['include_notify_tool'] = include_notify_tool
        self.ai.set_system_prompt_extras(
            include_image_tools=include_image_tools,
            include_controller_ref=include_controller_ref,
            include_notify_tool=include_notify_tool,
        )
        self.save_settings()

    def get_debug_mode(self):
        """Get debug mode setting"""
        return self.settings.get('debug_mode', False)

    def set_debug_mode(self, enabled):
        """Set debug mode — the single source of truth for both the floating-window
        menu and the Settings window. Centralizes the UI side-effects (open/close
        the debug window, repaint, post a chat notice) in FloatingWindow.apply_debug_mode
        so both entry points behave identically. Change-gated so re-saving Settings
        with debug already in its current state doesn't re-pop the debug window."""
        enabled = bool(enabled)
        if self.settings.get('debug_mode', False) == enabled:
            return
        log.info(f"[AssistantController.set_debug_mode] enabled={enabled}")
        self.settings['debug_mode'] = enabled
        self.save_settings()
        self.log(f"Debug mode: {'enabled' if enabled else 'disabled'}", "SUCCESS")
        try:
            if self.ui is not None:
                self.ui.apply_debug_mode(enabled)
        except Exception as e:
            log.warning(f"[AssistantController.set_debug_mode] UI side-effect failed: {e}")

    def set_tts_voice(self, voice):
        """Set TTS voice"""
        log.info(f"[AssistantController.set_tts_voice] voice='{voice}'")
        self.settings['tts_voice'] = voice
        self.log(f"SAVING: Setting TTS voice to {voice}", "INFO")
        self.voice_handler.set_tts_voice(voice)
        self.save_settings()
        self.log(f"TTS voice set to {voice}", "SUCCESS")

    def set_vad_aggressiveness(self, level):
        """Set VAD aggressiveness (0-3)"""
        log.info(f"[AssistantController.set_vad_aggressiveness] level={level}")
        self.settings['vad_aggressiveness'] = level
        self.log(f"SAVING: Setting VAD aggressiveness to {level}", "INFO")
        self.voice_handler.set_vad_aggressiveness(level)
        self.save_settings()
        self.log(f"VAD aggressiveness set to {level}", "SUCCESS")

    def send_message(self, user_message):
        """Send user message to AI (non-blocking)"""
        log.info(f"[AssistantController.send_message] ── Incoming message | "
                 f"len={len(user_message)} | preview='{user_message[:60].replace(chr(10), '↵')}' ──")

        if not user_message.strip():
            log.debug("[AssistantController.send_message] Empty message — ignoring")
            return

        # Reject messages during work mode — use the Stop button or timeout dialog instead
        if self.ai.tool_manager.work.is_working:
            log.warning("[AssistantController.send_message] Message rejected — input locked during work mode")
            self.log("Input locked during work mode — use the Stop button to cancel.")
            if self._chat:
                self._chat.add_system_message("⏳ **Input locked** — the interpreter is running. Use the **Stop** button or timeout dialog to cancel.")
            return

        # Prevent overlapping requests
        if self.is_processing:
            log.warning("[AssistantController.send_message] Already processing — ignoring request")
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True
        # Auto-naming cadence: count user turns for the active session.
        if self.current_session_id:
            self._session_user_turns[self.current_session_id] = \
                self._session_user_turns.get(self.current_session_id, 0) + 1
        # Remembered for voice barge-in: speaking while this request is in
        # flight cancels it and resends "<this text>\n<new speech>" as one.
        self._last_sent_user_text = user_message
        log.debug(f"[AssistantController.send_message] is_processing=True | "
                  f"provider='{self.ai.ai_provider}'")

        self.log(f"User: {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}")

        # Show thinking in UI
        self.ui.show_thinking()

        # No image collection here. Images already sit on the history entries
        # that own them (see systema/ui/chat/image_bubbles.py), so a plain
        # 'generate' carries every attached picture — the old "scan the pinned
        # list and upgrade to generate_with_image" step was the mechanism that
        # made images survive only the turns routed through this method.
        self._create_and_start_worker('generate', user_message)

    def send_message_with_image(self, user_message, image_paths):
        """Send a message whose images arrive WITH it.

        The chat UI no longer uses this: attaching puts a picture into the
        conversation immediately, so by send time it is already there. Kept for
        callers whose text and images genuinely arrive together (the Android
        bridge, and any external caller); it caches them onto the new user turn
        rather than passing them as a one-shot argument.
        """
        # Normalize: accept a single path string or a list
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        image_paths = [p for p in image_paths if p]

        log.info(f"[AssistantController.send_message_with_image] ── Incoming image message | "
                 f"images={len(image_paths)} | msg_preview='{user_message[:60].replace(chr(10), '↵')}' ──")

        if not user_message.strip():
            log.debug("[AssistantController.send_message_with_image] Empty message — ignoring")
            return

            # Prevent overlapping requests
        if self.is_processing:
            log.warning("[AssistantController.send_message_with_image] Already processing — ignoring request")
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True
        log.debug(f"[AssistantController.send_message_with_image] is_processing=True | "
                  f"provider='{self.ai.ai_provider}'")

        self.log(f"User (with {len(image_paths)} image(s)): {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}\n[Images: {', '.join(image_paths)}]")

        # Show thinking in UI
        self.ui.show_thinking()

        # Create worker thread with image list
        self._create_and_start_worker('generate_with_image', user_message, image_paths)

    def _create_and_start_worker(self, method, user_message, image_paths=None):
        """Create an AIWorker, connect signals, and start it."""
        if image_paths:
            self.current_worker = AIWorker(self.ai, method, user_message, image_paths)
        else:
            self.current_worker = AIWorker(self.ai, method, user_message)
        self._request_generation += 1
        _gen = self._request_generation
        self.current_worker.response_ready.connect(
            lambda result, g=_gen: self._dispatch_ai_response(result, g))
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def _dispatch_ai_response(self, result, generation):
        """Route a worker response to handle_ai_response only if it's still current."""
        if generation != self._request_generation:
            log.warning(f"[AssistantController._dispatch_ai_response] Stale response discarded "
                        f"(gen={generation}, current={self._request_generation})")
            return
        self.handle_ai_response(result)

    def _dispatch_work_response(self, result, generation):
        """Route a work-mode response to handle_work_response only if it's still current."""
        if generation != self._request_generation:
            log.warning(f"[AssistantController._dispatch_work_response] Stale work-mode response discarded "
                        f"(gen={generation}, current={self._request_generation})")
            return
        self.handle_work_response(result)

    # ── Debug-window provider observability ─────────────────────────────────────
    @staticmethod
    def _truncate_structure(obj, maxlen=800):
        """Recursively copy a messages/result structure, truncating long strings so
        the STRUCTURE (roles, tool_calls, tool_result linkage) stays readable in the
        Debug window without dumping full code/content bodies."""
        if isinstance(obj, str):
            return obj if len(obj) <= maxlen else obj[:maxlen] + f"…(+{len(obj) - maxlen} chars)"
        if isinstance(obj, dict):
            return {k: AssistantController._truncate_structure(v, maxlen) for k, v in obj.items()}
        if isinstance(obj, list):
            return [AssistantController._truncate_structure(v, maxlen) for v in obj]
        return obj

    def _push_provider_debug(self):
        """Surface, in the Debug window, HOW the last turn talked to the provider:
        the tool transport (native vs compat — so the user can confirm native is
        live), the exact SENT payload structure, and the RAW received response.
        The SENT/RECEIVED entries are pushed UNFILTERED (always shown, regardless of
        the type-filter checkboxes)."""
        import json
        ai = self.ai
        # 1) transport indicator
        transport = getattr(ai, 'last_tool_transport', 'compat')
        if transport == 'native':
            n = getattr(ai, 'last_native_tool_calls', 0)
            self.ui.show_debug_message(
                "system", f"NATIVE tool-calling active — provider returned {n} tool call(s)")
        elif transport == 'compat':
            self.ui.show_debug_message("system", "compat (fenced) tool-calling")
        else:
            self.ui.show_debug_message("system", f"NATIVE requested, but {transport} — used fenced calls")
        # 2) SENT payload structure (unfiltered)
        try:
            sent = getattr(ai, 'last_sent_messages', None)
            if sent is not None:
                pretty = json.dumps(self._truncate_structure(sent), indent=2, ensure_ascii=False)
                self.ui.show_debug_message(
                    "system", f"••• SENT → provider (message structure) •••\n{pretty}", unfiltered=True)
        except Exception as e:
            self.ui.show_debug_message("system", f"[SENT payload format error: {e}]", unfiltered=True)
        # 3) RAW received response (unfiltered)
        try:
            raw = getattr(ai, 'last_raw_provider_result', None)
            if isinstance(raw, dict):
                body = json.dumps(self._truncate_structure(raw), indent=2, ensure_ascii=False)
            else:
                body = str(raw)
            self.ui.show_debug_message(
                "system", f"••• RECEIVED ← provider (raw) •••\n{body}", unfiltered=True)
        except Exception as e:
            self.ui.show_debug_message("system", f"[RECEIVED response format error: {e}]", unfiltered=True)

    def handle_ai_response(self, result):
        """Handle AI response from worker thread"""
        log.info(f"[AssistantController.handle_ai_response] ── Response received | "
                 f"executed={result.get('executed', False)} | "
                 f"has_work_call={result.get('has_work_call', False)} | "
                 f"finished_working={result.get('finished_working', False)} | "
                 f"thinking={result.get('thinking', False)} ──")
        if not result.get('thinking'):
            self.ui.hide_thinking()
        self.is_processing = False
        log.debug("[AssistantController.handle_ai_response] is_processing=False")

        # Debug mode: show COMPLETE raw AI response with detailed parsing
        if self.settings.get('debug_mode'):
            self._push_provider_debug()
            raw_response = self.ai.last_raw_response

            if raw_response:
                self.ui.show_debug_message("ai", f"••• RAW AI RESPONSE •••\n{raw_response}")

                if result.get('has_work_call'):
                    self.ui.show_debug_message("system",
                                               f"••• PYTHON_INTERPRETER CALL DETECTED •••\n"
                                               f"In Work Mode: {result.get('is_working', False)}\n\n"
                                               f"━━━ CODE ━━━\n"
                                               f"{result.get('code', 'N/A')}\n"
                                               f"━━━━━━━━━━━━━━━━━\n\n"
                                               f"Visible Text to User:\n{result.get('response', '(none)')}\n\n"
                                               f"🐛 AI will see output and can chain more executions")

        # Show memory context card BEFORE the AI message so it appears above it
        if result.get('memory_context') and self._chat:
            try:
                ctx_id, memories = result['memory_context']
                # save_to_history=False: _inject_memories already added the ui_event entry
                if ctx_id and isinstance(ctx_id, str):
                    self._chat.add_memory_context_widget(ctx_id, memories, save_to_history=False)
                else:
                    log.warning(
                        f"[AssistantController.handle_ai_response] Skipping memory widget — invalid ctx_id={ctx_id!r}")
                log.info(f"[AssistantController.handle_ai_response] Memory context widget shown | id={ctx_id}")
            except Exception as e:
                log.error(f"[AssistantController.handle_ai_response] Failed to show memory widget: {e}")

        # Show visible text immediately (only if not empty) - NORMAL MODE or TOOL MODE.
        # Synthetic work placeholders ("Working...", skill-load echoes) are not
        # narration — filter them so only real model text becomes a bubble.
        _resp = (result.get('response') or '').strip()
        _synthetic = result.get('thinking') and (
            _resp in ('Working...', 'Working…')
            or _resp.startswith(('Loading skill:', 'Unloading skill:')))
        if _resp and not _synthetic:
            # narration_shown → the engine already surfaced this text before the
            # tool card (ordering fix); don't render it a second time here.
            if not result.get('narration_shown'):
                log.debug(f"[AssistantController.handle_ai_response] Showing AI message | "
                          f"len={len(result['response'])}")
                self.ui.show_ai_message(result['response'])

            # SESSION SAVE: Mark session as having messages and auto-save
            if not self.session_has_messages:
                self.session_has_messages = True
                log.debug("[AssistantController.handle_ai_response] session_has_messages=True")
            self._auto_save_session()

        # Show (or scroll to) the unified skills card in chat
        if result.get('skill_loaded') or result.get('skill_unloaded'):
            if self._chat:
                self._chat.add_loaded_skills_card()

        # Check if AI just exited tool mode — summary is already in the response above
        if result.get('finished_working'):
            log.info(
                "[AssistantController.handle_ai_response] AI exited work mode — summary included in response")
            self.work_timer.stop()
            self._on_turn_complete()
            return

        # ── First work call branch ───────────────────────────────────────────────
        # Stores work.last_output, shows code execution note, starts work_timer
        if result['thinking']:
            log.debug("[AssistantController.handle_ai_response] thinking=True — starting work_timer")
            from PyQt6.QtCore import QTimer
            if self._chat:
                QTimer.singleShot(0, lambda: self._chat.interrupt_btn.setToolTip("Interrupt work"))
            # Show code execution widget for the FIRST python_interpreter call
            if result.get('has_work_call') and result.get('code'):
                try:
                    tm_output = self.ai.tool_manager.work.last_output or ''
                    if tm_output and self._chat:
                        self._chat.add_code_execution_note(result['code'], tm_output)
                    # Any tool card spawned from inside that python step (web_search)
                    # is emitted now — AFTER the interpreter card, so order is
                    # python-first then the tool's card.
                    self.ai.tool_manager.flush_interp_cards()
                except Exception:
                    pass
            # Start tool mode timer
            self.work_timer.start()

        if not result.get('thinking'):
            # Normal (no-work) reply — the turn is complete.
            self._on_turn_complete()

    def handle_work_response(self, result):
        """Handle tool mode response from worker thread"""
        log.info(f"[AssistantController.handle_work_response] ── Response received | "
                  f"finished_working={result.get('finished_working', False)} | "
                  f"has_work_call={result.get('has_work_call', False)} | "
                  f"thinking={result.get('thinking', False)} ──")

        # Debug mode: show everything that happened
        if self.settings.get('debug_mode'):
            self._push_provider_debug()
            if self.ai.tool_manager.work.is_working:
                work_output = self.ai.tool_manager.work.last_output
                if work_output:
                    self.ui.show_debug_message("tool", f"••• PYTHON INTERPRETER OUTPUT •••\n{work_output}")

            raw_response = self.ai.last_raw_response
            if raw_response:
                self.ui.show_debug_message("ai", f"••• RAW AI RESPONSE (Work Mode) •••\n{raw_response}")

                if result.get('finished_working'):
                    self.ui.show_debug_message("system",
                        f"••• EXITING WORK MODE •••\n"
                        f"AI's final message to user:\n{result.get('response', '(none)')}")
                elif result.get('has_work_call'):
                    self.ui.show_debug_message("system",
                        f"••• NEXT PYTHON_INTERPRETER CALL •••\n"
                        f"Code: {result.get('code', 'N/A')}\n\n"
                        f"━━━ CHAINING EXECUTION ━━━\n"
                        f"AI is analyzing output and chaining more executions...")

        # Show thinking bubble before the final summary when work mode exits
        exited = result.get('finished_working')
        if exited:
            self.ui.show_thinking()
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

        self.ui.handle_work_update(result)

        # ── Exited work mode ─────────────────────────────────────────────────────
        if exited:
            self.log("AI exited tool mode")
            self.work_timer.stop()
            self.ai.tool_manager.work.is_working = False
            self.is_processing = False
            self.ui.hide_thinking()
            log.debug("[AssistantController.handle_work_response] is_processing=False (work mode exited)")
            if not self.session_has_messages:
                self.session_has_messages = True
            self._auto_save_session()
            self._on_turn_complete()
            return

        # ── Still in work mode ────────────────────────────────────────────────────
        if result['thinking']:
            # Still thinking — is_processing stays False to allow timer to fire
            self.ui.set_work_state(True)
            self.is_processing = False
            self.work_timer.start()
        else:
            # Done with work mode (final result received)
            self.work_timer.stop()
            self.ai.tool_manager.work.is_working = False
            self.is_processing = False
            log.debug("[AssistantController.handle_work_response] is_processing=False (work mode done)")
            QTimer.singleShot(0, self.ui.hide_thinking)
            if not self.session_has_messages:
                self.session_has_messages = True
            self._auto_save_session()

    def handle_ai_error(self, error_message):
        """Handle AI error from worker thread"""
        log.error(f"[AssistantController.handle_ai_error] ✗ Error received: '{error_message[:120]}'")
        self.ui.hide_thinking()
        self.ui.show_ai_message(f"Error: {error_message}")
        self.work_timer.stop()
        self.is_processing = False
        log.debug("[AssistantController.handle_ai_error] is_processing=False | timer stopped")
        self.log(f"AI Error: {error_message}", "ERROR")

        # Debug: Show error details
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("system", f"••• ERROR •••\n{error_message}")

    def auto_continue_work(self):
        """Automatically continue tool mode (non-blocking)"""
        log.debug("[AssistantController.auto_continue_work] Timer fired | "
                  f"is_working={self.ai.tool_manager.work.is_working} | "
                  f"is_processing={self.is_processing}")
        if not self.ai.tool_manager.work.is_working:
            log.debug("[AssistantController.auto_continue_work] Not in work mode — stopping timer")
            self.work_timer.stop()
            return

        # Voice gating: never advance the chain (and thus never execute the next
        # step) while narration is pending. Returning WITHOUT stopping the timer
        # keeps it polling every second — the chain resumes the moment speech
        # finishes, or instantly when the user skips it (stop_all clears the queue).
        if self.voice_busy():
            log.debug("[AssistantController.auto_continue_work] Speech pending — deferring continuation")
            return

        # Stop timer first to prevent duplicate calls
        self.work_timer.stop()

        # Check if already processing
        if self.is_processing:
            log.warning("[AssistantController.auto_continue_work] Already processing — skipping")
            self.log("Already processing - skipping tool mode continuation")
            return

        self.is_processing = True
        log.info("[AssistantController.auto_continue_work] ── Continuing work mode ──────────────")

        # Debug: Show tool mode prompt being sent
        if self.settings.get('debug_mode'):
            tool_prompt = self.ai.tool_manager.get_work_prompt()
            self.ui.show_debug_message("system", f"••• CONTINUING TOOL MODE •••\nSending to AI:\n{tool_prompt}")

        # Create worker thread
        log.debug("[AssistantController.auto_continue_work] Creating AIWorker for 'continue_tool'")
        self.current_worker = AIWorker(self.ai, 'continue_tool')
        self._request_generation += 1
        _gen = self._request_generation
        self.current_worker.response_ready.connect(
            lambda result, g=_gen: self._dispatch_work_response(result, g))
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.auto_continue_work] Worker started")

    def interrupt_work(self):
        """Interrupt and cancel tool mode.

        Handles cancellation during the 'thinking' gap (code finished, AI
        analyzing) or while awaiting the model's next step: terminates any
        in-flight continuation worker, invalidates its pending response,
        stops the timer, and clears all work-mode state. The original user
        prompt is preserved in history (unlike interrupt_request())."""
        log.info(f"[AssistantController.interrupt_work] is_working="
                 f"{self.ai.tool_manager.work.is_working}")
        if self.ai.tool_manager.work.is_working:
            log.warning("[AssistantController.interrupt_work] Interrupting tool mode by user")
            self.log("Tool mode interrupted by user")
            self.work_timer.stop()

            # Terminate the in-flight continuation worker if it's generating the
            # next step ("awaiting AI model response"), then discard its result.
            if getattr(self, 'current_worker', None) and self.current_worker.isRunning():
                log.warning("[AssistantController.interrupt_work] Terminating in-flight continuation worker")
                self.current_worker.terminate()
                self.current_worker.wait(1000)
            self._request_generation += 1  # invalidate any late worker signal

            self.ai.tool_manager.work.reset()
            self.is_processing = False

            # Same clean abort as interrupt_request: a work step interrupted
            # while its reply was streaming must not leave a half-written
            # segment on screen or a partial assistant entry in the session.
            self._abort_active_stream()

            # Cancel any pending narration along with the work it described
            try:
                self.voice_handler.stop_all()
            except Exception:
                pass
            try:
                if self._chat:
                    self._chat.flush_pending_voice_message()
            except Exception:
                pass

            # Notify UI
            if self._chat:
                try:
                    self._chat.set_session_list_locked(False)
                except Exception:
                    pass
                self._chat.add_system_message("**Tool operation canceled**")
                self._chat.hide_thinking()
                self._chat.set_input_enabled(True)

            self._auto_save_session()
            return True
        log.debug("[AssistantController.interrupt_work] Not in work mode — nothing to interrupt")
        return False

    def interrupt_request(self):
        """Interrupt current AI request and clean up state"""
        log.info(f"[AssistantController.interrupt_request] Interrupting | "
                 f"is_processing={self.is_processing} | "
                 f"is_working={self.ai.tool_manager.work.is_working}")
        interrupted = False

        # Stop the current worker thread if it exists
        if hasattr(self, 'current_worker') and self.current_worker and self.current_worker.isRunning():
            log.warning("[AssistantController.interrupt_request] Terminating running AIWorker thread")
            self.log("Interrupting AI worker thread...")
            self.current_worker.terminate()  # Force terminate the thread
            self.current_worker.wait(1000)  # Wait up to 1 second
            interrupted = True
            log.debug("[AssistantController.interrupt_request] Worker terminated")

        # Cancel work mode if active
        if self.ai.tool_manager.work.is_working:
            log.warning("[AssistantController.interrupt_request] Canceling active work mode")
            self.log("Tool mode interrupted by user")
            self.work_timer.stop()
            self.ai.tool_manager.work.reset()
            interrupted = True

        # Clear processing flag
        if self.is_processing:
            self.is_processing = False
            interrupted = True
            log.debug("[AssistantController.interrupt_request] is_processing cleared")

        # Cancel any pending narration along with the interrupted request
        if interrupted:
            try:
                self.voice_handler.stop_all()
            except Exception:
                pass
            # Killing the audio also kills the callback that would have shown a
            # voice-buffered reply — surface it now instead of stranding it.
            try:
                if self._chat:
                    self._chat.flush_pending_voice_message()
            except Exception:
                pass

        # Streaming stop = clean abort: erase the half-written reply and its
        # live thinking card from the UI, then purge the matching partial entry
        # from history so it is never saved to the session or re-sent.
        if self._abort_active_stream():
            interrupted = True

        # Remove the last user message from conversation history
        if interrupted:
            removed = self.ai.remove_last_user_message()
            if removed:
                log.info("[AssistantController.interrupt_request] ✓ Last user message removed from history")
                self.log("Removed last user message from conversation history")

        log.info(f"[AssistantController.interrupt_request] ✓ Interrupt complete | "
                 f"interrupted={interrupted}")

        # Always clean up UI regardless of whether anything was interrupted
        try:
            self._request_generation += 1  # invalidates all in-flight worker signals
            self._chat.hide_thinking()
            self._chat.set_input_enabled(True)
        except Exception:
            pass

        return interrupted

    def _abort_active_stream(self) -> bool:
        """Tear down an in-flight streamed turn and purge its partial history
        entry. No-op (False) when nothing was streaming, so the normal
        non-streaming interrupt paths are unaffected.

        Must run AFTER the worker has been terminated and waited on — otherwise
        the dying worker could append its partial entry back after the purge.
        """
        if not self._chat:
            return False
        try:
            had_stream = self._chat.abort_stream()
        except Exception:
            log.warning("[AssistantController._abort_active_stream] UI abort failed",
                        exc_info=True)
            return False
        if not had_stream:
            return False
        try:
            self.ai.discard_streamed_partial()
        except Exception:
            log.warning("[AssistantController._abort_active_stream] history purge failed",
                        exc_info=True)
        log.info("[AssistantController._abort_active_stream] ✓ streamed turn aborted cleanly")
        return True

    def _on_work_narration(self, text):
        """Slot for ApprovalSignal.work_narration — a work step's narration, sent
        by the engine BEFORE the tool executes.

        Render DIRECTLY (this slot already runs on the GUI thread, via the queued
        cross-thread signal) so the narration lands in THIS event-loop pass. The
        tool card's signal (work_code_active for the interpreter, file_op for
        read/edit/write/grep) is emitted LATER, so it is processed in a later pass
        — text therefore always renders first. Do NOT defer this via singleShot:
        _deliver_file_op renders its card directly, so a deferred narration would
        lose the race and the card would appear above the text (the long-standing
        card-before-text bug)."""
        self._show_work_narration(text)

    def _show_work_narration(self, text):
        try:
            if self._chat:
                self._chat.show_ai_message(text)
        except Exception:
            pass

    def _on_work_code_active(self, active):
        """Slot connected to ApprovalSignal.work_code_active — thread-safe delegation via QTimer.singleShot."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._update_interrupt_btn(active))
        QTimer.singleShot(0, lambda: self._update_live_output_stream(active))

    def _update_live_output_stream(self, active):
        """Start/stop the live work-mode output console (runs on the GUI thread).

        On start: opens a transient streaming console and begins polling the
        interpreter buffer. On stop: does a final flush and tears the console
        down — the permanent collapsed note is added by the normal flow."""
        if not self._chat:
            return
        _ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if active:
            code = self.ai.tool_manager.work.interpreter.last_code or ""
            try:
                self._chat.start_live_output(code)
            except Exception as e:
                log.debug(f"[AssistantController._update_live_output_stream] start failed: {e}")
            if _ab and _ab.isVisible():
                try: _ab.start_live_output(code)
                except Exception: pass
            self._live_output_timer.start()
        else:
            self._live_output_timer.stop()
            try:
                self._poll_live_output()  # final flush of any tail output
            except Exception:
                pass
            try:
                self._chat.end_live_output()
            except Exception:
                pass
            if _ab and _ab.isVisible():
                try: _ab.end_live_output()
                except Exception: pass

    def _poll_live_output(self):
        """Timer tick — pull current output from the interpreter into the live console."""
        if not self._chat:
            return
        try:
            interp = self.ai.tool_manager.tools.get('python')
            if interp is None:
                return
            _text = interp.peek_live_output()
            self._chat.update_live_output(_text)
            _ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
            if _ab and _ab.isVisible():
                try: _ab.update_live_output(_text)
                except Exception: pass
        except Exception:
            pass

    def _update_interrupt_btn(self, active):
        """Enable/disable the interrupt button based on interpreter code execution state.

        Stays enabled throughout work mode (not just while code is running) so the
        user can cancel during the 'thinking' gap or while awaiting the next step."""
        if self._chat:
            enabled = active or self.ai.tool_manager.work.is_working
            self._chat.interrupt_btn.setEnabled(enabled)

    # ═══════════════════════════════════════════════════════════
    # SESSION MANAGEMENT METHODS
    # ═══════════════════════════════════════════════════════════

    def _create_new_session(self):
        """Create a new session"""
        log.info(f"[AssistantController._create_new_session] current_session_id='{self.current_session_id}' | "
                 f"session_has_messages={self.session_has_messages}")
        # Save current session if it has messages
        if self.current_session_id and self.session_has_messages:
            log.debug("[AssistantController._create_new_session] Saving existing session before replacement")
            self._auto_save_session()

        # Delete current session if it's empty
        if self.current_session_id and not self.session_has_messages:
            log.debug(f"[AssistantController._create_new_session] Deleting empty session: "
                      f"'{self.current_session_id}'")
            self.session_manager.delete_session(self.current_session_id)

        # Create new session
        self.current_session_id = self.session_manager.create_session()
        self.session_has_messages = False
        log.info(f"[AssistantController._create_new_session] ✓ New session created: "
                 f"'{self.current_session_id}'")
        self.log(f"Created new session: {self.current_session_id}")

    def rewrite_tool_output(self, old: str, replacement: str, save: bool = True,
                            stash_original: bool = True) -> int:
        """Token-saving history surgery: replace a toolcall's OUTPUT everywhere
        it lives — the ui_event's `_output` (card + reload display) AND every
        entry whose content embeds it (the live `_is_work_prompt` ping, slimmed
        output-only work prompts, skill work outputs) — so the model stops
        paying for it. Saves the session unless `save=False` (bulk callers save
        once at the end). Returns the number of entries touched.

        When `replacement` is a compaction/clear STUB, the pre-rewrite original is
        stashed on the ui_event as `_output_original` (unless already present or
        `stash_original=False`) so Restore/Revert can reverse it later — this
        replaces the old `.precompact.bak` file. `_output_original` is UI metadata,
        never sent to the model, so it costs zero request tokens."""
        if not isinstance(old, str) or len(old.strip()) < 8:
            return 0
        _is_stub = (replacement.startswith('[Compacted]')
                    or replacement.strip() == 'Output cleared by the user')
        touched = 0
        try:
            for entry in self.ai.conversation_history:
                if entry.get('_output') == old:
                    if stash_original and _is_stub and '_output_original' not in entry:
                        entry['_output_original'] = old
                    entry['_output'] = replacement
                    touched += 1
                if entry.get('_work_output') == old:
                    entry['_work_output'] = replacement
                c = entry.get('content')
                if isinstance(c, str) and old in c:
                    entry['content'] = c.replace(old, replacement)
                    touched += 1
        except Exception as e:
            log.error(f"[AssistantController.rewrite_tool_output] {e}")
            return touched
        if touched and save:
            self._auto_save_session()
        log.info(f"[AssistantController.rewrite_tool_output] replaced in {touched} "
                 f"entr{'y' if touched == 1 else 'ies'} | old_len={len(old)} → "
                 f"new_len={len(replacement)}")
        return touched

    @property
    def compaction_manager(self):
        """Lazily-created CompactionManager (runs per-session background jobs)."""
        if getattr(self, '_compaction_manager', None) is None:
            from systema.agents.compaction_manager import CompactionManager
            self._compaction_manager = CompactionManager(self)
        return self._compaction_manager

    def compact_all_toolcalls(self):
        """Session tool: start a background compaction job for the CURRENT session
        via the CompactionManager. Each chunky toolcall output becomes a
        detail-preserving '[Compacted]' summary LIVE (the token pill drops per
        step); the original is stashed in-history for Restore; the job SURVIVES
        session switches and is listed + stoppable in the Compaction agents dialog."""
        from PyQt6.QtWidgets import QMessageBox
        from systema.common.token_est import estimate_tokens
        chat = self._chat
        if chat is None:
            return
        sid = self.current_session_id
        if self.compaction_manager.is_active(sid):
            chat.add_system_message("This session is already being compacted — "
                                    "open Compaction agents… to stop it.")
            return

        _STUBS = ('[Compacted]', 'Output cleared by the user')
        targets = []
        for e in self.ai.conversation_history:
            if e.get('role') != 'ui_event':
                continue
            out = e.get('_output')
            if (isinstance(out, str) and len(out) > 400
                    and not out.strip().startswith(_STUBS[0])
                    and out.strip() != _STUBS[1]):
                targets.append((e.get('_code', '') or '', out))
        if not targets:
            chat.add_system_message("Nothing to compact — no chunky tool outputs here.")
            return

        total_tok = sum(estimate_tokens(o) for _, o in targets)
        ret = QMessageBox.question(
            chat, "Compact all toolcalls",
            f"Summarize {len(targets)} toolcall output(s) (~{total_tok:,} tokens) into\n"
            f"concise detail-preserving versions? Runs in the background — keep\n"
            f"working, Stop it, or Restore later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return

        if self.compaction_manager.start(sid, targets):
            chat.add_system_message(
                f"Compacting {len(targets)} toolcall output(s) in the background…")
            self.open_compaction_agents_dialog()

    def compaction_active(self) -> bool:
        """True when the CURRENT session has a compaction job running (the menu
        branches on this — a job in another session must not flip this menu).
        A missing session id means "no job here" — is_active(None) would fall
        back to the global any-session check and resurrect the old bug."""
        sid = self.current_session_id
        return bool(sid) and self.compaction_manager.is_active(sid)

    def stop_compaction(self):
        """Stop the CURRENT session's compaction job (progress is kept)."""
        self.compaction_manager.stop(self.current_session_id)
        if self._chat:
            self._chat.add_system_message("Stopping compaction after the current item…")

    def open_compaction_agents_dialog(self):
        """Show the active-compaction-agents list (session · progress · Stop)."""
        chat = self._chat
        if chat is None:
            return
        try:
            from systema.ui.dialogs.compaction_agents_dialog import CompactionAgentsDialog
            dlg = getattr(self, '_compaction_dialog', None)
            if dlg is None:
                dlg = CompactionAgentsDialog(chat, self.compaction_manager)
                self._compaction_dialog = dlg
            dlg.refresh()
            dlg.show()
            dlg.raise_()
        except Exception as e:
            log.error(f"[open_compaction_agents_dialog] {e}")

    def _rewrite_output_in_session_file(self, session_id, old, new) -> int:
        """Background compaction of a NON-loaded session: rewrite the output stub
        in that session's FILE (its history isn't in memory). Mirrors
        rewrite_tool_output's fields + the `_output_original` stash. Per-step
        load+save keeps the file current so switching INTO it mid-job is safe."""
        if not isinstance(old, str) or len(old.strip()) < 8:
            return 0
        try:
            data = self.session_manager.load_session(session_id)
        except Exception:
            return 0
        if not data:
            return 0
        hist = data.get('chat_history', [])
        _is_stub = (new.startswith('[Compacted]')
                    or new.strip() == 'Output cleared by the user')
        touched = 0
        for entry in hist:
            if entry.get('_output') == old:
                if _is_stub and '_output_original' not in entry:
                    entry['_output_original'] = old
                entry['_output'] = new
                touched += 1
            if entry.get('_work_output') == old:
                entry['_work_output'] = new
            c = entry.get('content')
            if isinstance(c, str) and old in c:
                entry['content'] = c.replace(old, new)
                touched += 1
        if touched:
            try:
                self.session_manager.save_session(session_id, hist)
            except Exception:
                return 0
        return touched

    def restore_all_compacted(self):
        """Reverse every '[Compacted]' output back to its stashed original."""
        return self._restore_stashed(stub_prefix='[Compacted]', label='compacted')

    def revert_cleared_outputs(self):
        """Reverse every 'Output cleared by the user' output back to its original."""
        return self._restore_stashed(exact='Output cleared by the user', label='cleared')

    def _restore_stashed(self, stub_prefix=None, exact=None, label=''):
        """Reverse compaction/clear stubs that carry an `_output_original` stash."""
        chat = self._chat
        n = 0
        for entry in list(self.ai.conversation_history):
            out = entry.get('_output')
            orig = entry.get('_output_original')
            if not isinstance(out, str) or not isinstance(orig, str):
                continue
            matched = ((stub_prefix and out.strip().startswith(stub_prefix))
                       or (exact and out.strip() == exact))
            if not matched:
                continue
            if self.rewrite_tool_output(out, orig, save=False, stash_original=False):
                entry.pop('_output_original', None)
                n += 1
        if n:
            self._auto_save_session()
            if chat:
                chat.render_loaded_messages()
                if hasattr(chat, '_update_token_count'):
                    chat._update_token_count()
        if chat:
            chat.add_system_message(
                f"Restored {n} {label} output(s)." if n
                else f"No {label} outputs to restore.")
        return n

    def _auto_save_session(self):
        """Automatically save current session after AI response"""
        log.debug(f"[AssistantController._auto_save_session] session_id='{self.current_session_id}' | "
                  f"session_has_messages={self.session_has_messages}")
        if not self.current_session_id:
            log.debug("[AssistantController._auto_save_session] No session_id — skipping")
            return

        if not self.session_has_messages:
            log.debug("[AssistantController._auto_save_session] No messages yet — skipping save")
            return  # Don't save empty sessions

        # Get conversation history (already excludes system prompt - that's in construction)
        chat_history = self.ai.conversation_history.copy()
        log.debug(f"[AssistantController._auto_save_session] Saving {len(chat_history)} history entries")

        # Save session
        success = self.session_manager.save_session(
            self.current_session_id,
            chat_history,
            next_image_n=getattr(self.ai, 'next_image_n', None)
        )

        if success:
            if self._chat:
                self._chat.refresh_session_list()
            log.info(f"[AssistantController._auto_save_session] ✓ Session saved: '{self.current_session_id}'")
            self.log(f"Session auto-saved: {self.current_session_id}")
        else:
            log.error(f"[AssistantController._auto_save_session] ✗ Save failed: '{self.current_session_id}'")
            self.log(f"Failed to save session: {self.current_session_id}", "ERROR")

    def _present_new_session(self):
        """Put a freshly-created session on screen.

        ONE implementation, shared by `create_new_session()` and the
        delete-the-active-session path. Those two used to hand-roll this
        separately and the copies drifted: deleting the current session posted
        the retired "Session Deleted - New session created" grey line and never
        showed the greeting banner, long after the banner replaced that line
        everywhere else. Any future step in "start a fresh session" belongs
        here, so it cannot go missing from one entry point.
        """
        if not self._chat:
            return
        self._chat.clear_chat_silent()
        self._chat.clear_pinned_images()   # detach all pinned images on new session
        self._chat.refresh_session_list()
        # The greeting banner IS the new-session notice — it replaces the old
        # "**New Session Created**" grey line rather than joining it. Same
        # lifecycle: UI-only, never in history, cleared with the chat. The
        # elevated-privileges notice stays its own separate line.
        self._chat.add_greeting_banner()
        self._chat.warn_loaded_skills_if_any()
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.render_loaded_messages()

    def create_new_session(self):
        """Create new session (called from UI)"""
        log.info("[AssistantController.create_new_session] Creating new session from UI")
        # Save + create new session FIRST (while AI history is still intact)
        self._create_new_session()

        # NOW clear AI history and UI (after the old session has been saved)
        log.debug("[AssistantController.create_new_session] Clearing AI history...")
        self.ai.clear_history()
        self._present_new_session()
        log.info(f"[AssistantController.create_new_session] ✓ New session ready: '{self.current_session_id}'")

    def load_session(self, session_id):
        """Load a session"""
        log.info(f"[AssistantController.load_session] Loading session_id='{session_id}'")
        # Save current session first
        if self.current_session_id and self.session_has_messages:
            log.debug("[AssistantController.load_session] Auto-saving current session first")
            self._auto_save_session()

        # Delete current empty session
        if self.current_session_id and not self.session_has_messages:
            log.debug(f"[AssistantController.load_session] Deleting empty current session: "
                      f"'{self.current_session_id}'")
            self.session_manager.delete_session(self.current_session_id)

        # Load session data
        from systema.common.perf_monitor import span
        with span(f"load_session.disk[{session_id}]"):
            session_data = self.session_manager.load_session(session_id)

        if not session_data:
            log.error(f"[AssistantController.load_session] ✗ Failed to load session: '{session_id}'")
            self.log(f"Failed to load session: {session_id}", "ERROR")
            return False

        history_len = len(session_data.get('chat_history', []))
        log.debug(f"[AssistantController.load_session] Session data loaded | "
                  f"name='{session_data.get('session_name')}' | history={history_len} entries")

        # Clear chat UI and detach any pinned images from the previous session
        if self._chat:
            self._chat.clear_chat_silent()
            self._chat.clear_pinned_images()

        # Clear AI history
        self.ai.clear_history()

        # Load chat history into AI. append_loaded, NOT append: these entries
        # were written during earlier runs and already carry (or predate) their
        # own log stamp. Re-stamping them with today's log would be a confident
        # lie about where to look for them.
        for msg in session_data['chat_history']:
            _append = getattr(self.ai.conversation_history, 'append_loaded',
                              self.ai.conversation_history.append)
            _append(msg)
        log.debug(f"[AssistantController.load_session] {history_len} messages loaded into AI history")

        # Restore the image counter. Sessions written before it existed fall
        # back to a floor derived from their history, so old files still load;
        # the stored value wins when present, which is what keeps a deleted
        # image's number retired across a reload.
        try:
            from systema.common.image_refs import next_number
            self.ai.next_image_n = next_number(
                self.ai.conversation_history, session_data.get('next_image_n'))
        except Exception:
            self.ai.next_image_n = 1

        # Render messages in UI — strip tool call JSON from assistant messages
        if self._chat:
            self._chat.render_loaded_messages()
        log.debug("[AssistantController.load_session] Messages rendered in UI (tool calls stripped)")
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.render_loaded_messages()

        # Update current session
        self.current_session_id = session_id
        self.session_has_messages = len(session_data['chat_history']) > 0

        log.info(f"[AssistantController.load_session] ✓ Session loaded: '{session_id}' | "
                 f"history={history_len} | session_has_messages={self.session_has_messages}")
        self.log(f"Session loaded: {session_id}")
        return True

    def delete_session(self, session_id):
        """Delete a session"""
        log.info(f"[AssistantController.delete_session] session_id='{session_id}' | "
                 f"is_active={session_id == self.current_session_id}")
        # If deleting active session, create new one
        if session_id == self.current_session_id:
            log.debug("[AssistantController.delete_session] Deleting active session — clearing UI and creating new")
            self.ai.clear_history()
            self.session_manager.delete_session(session_id)

            # There is no current session any more — say so, and BOTH of
            # _create_new_session()'s guards fall away cleanly:
            #   * it will not _auto_save_session() the id we just deleted.
            #     clear_history() empties the transcript but does NOT reset
            #     session_has_messages, so without this it saw "has messages"
            #     and re-wrote the JUST-DELETED session with the now-empty
            #     history — the deleted session came back as an empty one.
            #   * it will not run its "delete the empty current session" branch
            #     either, which would delete the same id a second time.
            self.current_session_id = None
            self.session_has_messages = False

            self._create_new_session()
            # Same presentation as any other new session — greeting banner
            # included. This used to post a "Session Deleted" grey line instead
            # and no banner at all.
            self._present_new_session()
            log.info("[AssistantController.delete_session] ✓ Active session deleted and replaced")
        else:
            log.debug(f"[AssistantController.delete_session] Deleting non-active session '{session_id}'")
            # Just delete the session
            self.session_manager.delete_session(session_id)

            # Refresh UI
            if self._chat:
                self._chat.refresh_session_list()
            log.info("[AssistantController.delete_session] ✓ Session deleted")

    def set_session_name(self, name, manual: bool = False):
        """Rename the current session. manual=True marks it user-set so the
        background auto-namer never overwrites it. The AI no longer names
        sessions — the SessionNamerAgent and the sidebar rename call this."""
        log.info(f"[AssistantController.set_session_name] name='{name}' manual={manual} | "
                 f"session_id='{self.current_session_id}'")
        if not self.current_session_id:
            log.warning("[AssistantController.set_session_name] No current_session_id — aborting")
            return
        if manual:
            self._session_manually_named.add(self.current_session_id)

        # Rename session
        success = self.session_manager.rename_session(self.current_session_id, name)

        if success:
            log.info(f"[AssistantController.set_session_name] ✓ Renamed to '{name}'")
            self.log(f"Session renamed to: {name}")

            # Refresh UI session list
            if self._chat:
                self._chat.refresh_session_list()
        else:
            log.error(f"[AssistantController.set_session_name] ✗ Failed to rename to '{name}'")
            self.log("Failed to rename session", "ERROR")

    def _on_turn_complete(self):
        """A conversation turn just finished — refresh the input token pill (the
        history grew, so the next request costs more) and try a background
        auto-title."""
        try:
            if self._chat and hasattr(self._chat, '_update_token_count'):
                self._chat._update_token_count()
        except Exception:
            pass
        self._maybe_autoname_session()

    def _autoname_digest(self, max_msgs: int = 8, max_chars: int = 1600) -> str:
        """A compact plain-text digest of the recent conversation for the namer."""
        parts = []
        try:
            for m in self.ai.conversation_history:
                if m.get('role') not in ('user', 'assistant'):
                    continue
                c = m.get('content')
                if not isinstance(c, str) or not c.strip():
                    continue
                try:
                    c = self.ai.tool_manager.strip_tool_calls(c)
                except Exception:
                    pass
                c = c.strip()
                if c:
                    parts.append(f"{m['role']}: {c[:400]}")
        except Exception:
            pass
        return "\n".join(parts[-max_msgs:])[:max_chars]

    def _maybe_autoname_session(self):
        """Auto-title the current session in the background (SessionNamerAgent):
        once after the first completed exchange, then a gentle refresh every 5
        user turns. Silent; respects a manual rename; gated by a setting."""
        if not self.settings.get('session_autoname_enabled', True):
            return
        sid = self.current_session_id
        if (not sid or sid in self._session_manually_named
                or sid in self._autoname_in_flight):
            return
        turns = self._session_user_turns.get(sid, 0)
        if not (turns == 1 or (turns >= 5 and turns % 5 == 0)):
            return
        digest = self._autoname_digest()
        if not digest.strip():
            return
        try:
            current_title = self.session_manager.get_session_name(sid) or ""
        except Exception:
            current_title = ""

        from PyQt6.QtCore import QThread, QObject, pyqtSignal
        ctrl = self
        self._autoname_in_flight.add(sid)

        class _Namer(QObject):
            done = pyqtSignal(str, str)   # sid, title

            def run(self):
                from systema.agents.session_namer_agent import SessionNamerAgent
                title = ""
                try:
                    title = SessionNamerAgent(ctrl.ai).generate(digest, current_title) or ""
                except Exception:
                    title = ""
                self.done.emit(sid, title)

        thread = QThread()
        worker = _Namer()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _on_done(_sid, title):
            try:
                ctrl._autoname_in_flight.discard(_sid)
                if (title and _sid == ctrl.current_session_id
                        and _sid not in ctrl._session_manually_named):
                    ctrl.set_session_name(title)   # auto (manual=False)
            finally:
                thread.quit()
                thread.wait(1500)
                ctrl._autoname_threads.discard((thread, worker))

        worker.done.connect(_on_done)
        self._autoname_threads.add((thread, worker))
        thread.start()

    def _handle_task_message(self, message: str):
        """
        Slot — called on the main thread when a task sends a message to main session.
        Renders the message as an AI bubble in the chat window.
        """
        if self._chat:
            self._chat.add_ai_message(f"**[Task Notification]:** {message}")
        log.info(f"[AssistantController._handle_task_message] Rendered task message | "
                 f"preview='{message[:60]}'")

    def get_session_list(self):
        """Get list of all sessions"""
        return self.session_manager.list_sessions()

    def show(self):
        """Show the UI"""
        self.ui.show()

    def _inject_interpreter_namespaces(self):
        """Build the main session's python namespace FROM the capability manifest.

        The names and their gates live in systema/execution/capabilities.py; this
        method only supplies the bindings. The tasker builds its namespace the
        same way with its own bindings, so a capability can never mean one thing
        here and something else in a background task — and a capability the
        prompt hides can no longer be silently injected anyway (which is exactly
        what used to happen to notify/memory in taskers).
        """
        from systema.execution import capabilities as caps
        tm = self.ai.tool_manager
        # The main session's NAMESPACE is deliberately permissive: every one of
        # these names has always been present in the interpreter regardless of
        # the prompt's include_* switches, and the user's own snippets rely on
        # that. Over-provisioning the namespace is not a lie (nothing advertises
        # what isn't there) — the strict gating that matters is on the TOOL
        # surface and in the prompt, which is where the drift actually hurt.
        # Flip these to the settings lookups to make the namespace strict too.
        gates = caps.gates_for_chat(
            allow_workmode=bool(getattr(tm, 'allow_workmode', True)),
            has_skills=bool(getattr(self, 'skill_manager', None)),
            include_image_tools=True,
            include_notify_tool=True,
            include_memory=True,
            include_controller_ref=True,
        )
        bindings = {
            'controller': self,
            # Dual-surface mirrors: these route through the SAME run_* the tool
            # call uses, so an in-python call spawns the same card and returns
            # the same observation (the web_search bar).
            'web_search': tm.interp_web_search,
            'attach_image_to_chat': tm.interp_attach_image_to_chat,
            'attach_image_to_context': self._agent_attach_image_to_context,
            'take_screenshot': self._agent_take_screenshot,
            'notify': self.notify,
            'memorize': self.memorize,
            'search_memory': self.search_memory,
            'update_memory': self.update_memory,
            'forget_memory': self.forget_memory,
            'app_root': str(_APP_ROOT),
            'skills_path': str(_APP_ROOT / "skills"),
        }
        ns = tm.tools['python'].namespace
        ns.update(caps.build_namespace(caps.CHAT, gates, bindings))

        # What the PROMPT is allowed to name is a stricter set than what is
        # bound above — the include_* switches are real there. Record it so the
        # work-mode ping's recap lists exactly what the system prompt taught,
        # instead of re-advertising an option the user switched off.
        tm.prompt_context = caps.CHAT
        tm.documented_gates = caps.gates_for_chat(
            allow_workmode=bool(getattr(tm, 'allow_workmode', True)),
            has_skills=bool(getattr(self, 'skill_manager', None)),
            include_image_tools=bool(self.settings.get('include_image_tools', False)),
            include_notify_tool=bool(self.settings.get('include_notify_tool', False)),
            include_memory=bool(self.settings.get('memory_enabled', True)),
            include_controller_ref=bool(self.settings.get('include_controller_ref', False)),
        )

    def reset_python_interpreter(self):
        """Reset the Python interpreter and reinject namespaces"""
        log.info("[AssistantController.reset_python_interpreter] Resetting Python interpreter...")
        self.ai.tool_manager.reset_python()
        self._inject_interpreter_namespaces()
        log.info("[AssistantController.reset_python_interpreter] ✓ Reset complete with namespace reinjection")
        self.log("Python interpreter reset", "SUCCESS")

    def refresh_memory_block(self):
        """Refresh the memory block in the system prompt after memories or settings change."""
        if hasattr(self.ai, 'refresh_memory_block'):
            self.ai.refresh_memory_block()

    # ── Memory namespace helpers ──────────────────────────────────────────────

    @staticmethod
    def _split_memory_blob(text: str):
        """Split a stored memory blob into (title, body, tags).
        Blob format: title line, body paragraphs, optional trailing 'Tags: ...'."""
        lines = text.split('\n')
        title = lines[0].strip()
        rest = lines[1:]
        while rest and not rest[-1].strip():
            rest = rest[:-1]
        tags = ""
        if rest and rest[-1].strip().lower().startswith("tags:"):
            tags = rest[-1].strip()[5:].strip()
            rest = rest[:-1]
        body = "\n".join(rest).strip()
        return title, body, tags

    def memorize(self, title: str, body: str, tags: str = "") -> str:
        """Store a structured memory. Call from python_interpreter:
           memorize('Title', 'Body sentence(s).', 'tag1, tag2')"""
        if not self.memory_manager or not self.memory_manager.is_ready:
            return "[memorize] Memory manager not ready."
        title = str(title).strip()
        body = str(body).strip()
        tags = str(tags).strip()
        if not title:
            return "[memorize] Title is required."
        if not body:
            return "[memorize] Body is required."
        parts = [title, body]
        if tags:
            parts.append(f"Tags: {tags}")
        text = "\n\n".join(parts)
        success = self.memory_manager.memorize(text)
        if success:
            self.refresh_memory_block()
            log.info(f"[AssistantController.memorize] ✓ Stored: '{title}'")
            return f"[memorize] ✓ Stored: \"{title}\""
        return "[memorize] ✗ Failed to store memory."

    def search_memory(self, query: str = "", threshold: float = 0.4, max_results: int = 3) -> str:
        """Search memories by topic. Empty query lists ALL memory titles.
           Call from python_interpreter: search_memory('query') or search_memory()"""
        if not self.memory_manager or not self.memory_manager.is_ready:
            return "[search_memory] Memory manager not ready."
        query = str(query).strip()
        if not query:
            memories = self.memory_manager.get_all()
            if not memories:
                return "[search_memory] No memories stored yet."
            lines = [f"[search_memory] All {len(memories)} memory title(s):"]
            for i, m in enumerate(memories, 1):
                title = m['text'].split('\n')[0].strip()
                edited = " [edited]" if m.get('edited') else ""
                lines.append(f"  {i}. {title}{edited}")
            return "\n".join(lines)
        results = self.memory_manager.recall(query, threshold=threshold, max_results=max_results)
        if not results:
            return f"[search_memory] No memories found for query: \"{query}\""
        lines = [f"[search_memory] {len(results)} result(s) for \"{query}\":"]
        for i, m in enumerate(results, 1):
            lines.append(f"  {i}. (score={m['similarity']}) {m['text']}")
        return "\n".join(lines)

    def update_memory(self, title: str, new_title: str = None, new_body: str = None,
                      new_tags: str = None) -> str:
        """Update ONE memory found by its title (case-insensitive substring).
        Unspecified parts are preserved; new_tags="" clears the tags.
        Ambiguous matches change nothing and return the candidate titles.
        Call from python_interpreter: update_memory('Old title', new_body='...')"""
        if not self.memory_manager or not self.memory_manager.is_ready:
            return "[update_memory] Memory manager not ready."
        title = str(title).strip()
        if not title:
            return "[update_memory] title is required."
        if new_title is None and new_body is None and new_tags is None:
            return "[update_memory] Nothing to change — pass new_title, new_body and/or new_tags."
        all_memories = self.memory_manager.get_all()
        matches = [m for m in all_memories
                   if title.lower() in m['text'].split('\n')[0].strip().lower()]
        if not matches:
            return f"[update_memory] No memory found with title matching '{title}'"
        if len(matches) > 1:
            lines = [f"[update_memory] Ambiguous — {len(matches)} titles match "
                     f"'{title}'; nothing changed:"]
            for m in matches:
                t = m['text'].split('\n')[0].strip()
                lines.append(f"  - {t}")
            lines.append("Call again with a more specific title.")
            return "\n".join(lines)
        target = matches[0]
        old_title, old_body, old_tags = self._split_memory_blob(target['text'])
        final_title = str(new_title).strip() if new_title is not None and str(new_title).strip() else old_title
        final_body = str(new_body).strip() if new_body is not None and str(new_body).strip() else old_body
        final_tags = str(new_tags).strip() if new_tags is not None else old_tags
        parts = [final_title, final_body] if final_body else [final_title]
        if final_tags:
            parts.append(f"Tags: {final_tags}")
        success = self.memory_manager.update(target['id'], "\n\n".join(parts))
        if success:
            self.refresh_memory_block()
            log.info(f"[AssistantController.update_memory] ✓ Updated: '{final_title}'")
            return f"[update_memory] ✓ Updated: \"{final_title}\""
        return "[update_memory] ✗ Failed to update memory."

    def forget_memory(self, identifier: str) -> str:
        """Delete memory. An exact title match (case-insensitive) deletes that
        ONE memory; otherwise EVERY memory whose text contains identifier is
        deleted. Call from python_interpreter: forget_memory('title or text')"""
        if not self.memory_manager or not self.memory_manager.is_ready:
            return "[forget_memory] Memory manager not ready."
        identifier = str(identifier).strip()
        if not identifier:
            return "[forget_memory] identifier is required."
        all_memories = self.memory_manager.get_all()
        # Strategy 1 — exact full-title match: delete exactly one
        for m in all_memories:
            stored_title = m['text'].split('\n')[0].strip()
            if stored_title.lower() == identifier.lower():
                self.memory_manager.delete(m['id'])
                self.refresh_memory_block()
                log.info(f"[AssistantController.forget_memory] ✓ Deleted by exact title: '{stored_title}'")
                return f"[forget_memory] ✓ Deleted 1 memory by exact title: \"{stored_title}\""
        # Strategy 2 — substring match on full text: bulk delete
        matches = [m for m in all_memories if identifier.lower() in m['text'].lower()]
        if not matches:
            return f"[forget_memory] No memories found matching '{identifier}'"
        deleted_titles = []
        for m in matches:
            self.memory_manager.delete(m['id'])
            deleted_titles.append(m['text'].split('\n')[0].strip())
        self.refresh_memory_block()
        log.info(f"[AssistantController.forget_memory] ✓ Bulk-deleted {len(deleted_titles)} "
                 f"memory/memories matching '{identifier}'")
        titles = "; ".join(f'"{t}"' for t in deleted_titles)
        return (f"[forget_memory] ✓ Bulk-deleted {len(deleted_titles)} memory/memories "
                f"matching '{identifier}': {titles}")

    def detach_memory_context(self, context_id: str):
        """Remove a memory context ui_event from history, save session, and sync Android."""
        log.info(f"[AssistantController.detach_memory_context] id='{context_id}'")
        removed = self.ai.detach_memory_context(context_id)
        # Always save — even if the entry wasn't found, a stale session file is worse
        self._auto_save_session()
        if removed:
            ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
            if ab and ab.isVisible():
                ab.remove_memory_context_card(context_id)
            log.info("[AssistantController.detach_memory_context] ✓ Done")
        else:
            log.warning(f"[AssistantController.detach_memory_context] Entry id='{context_id}' not found in history — session saved anyway")

    def get_ai_provider(self):
        """Get current AI provider"""
        return self.settings.get('ai_provider', 'custom_script')

    # ── Fire-and-forget Desktop Notification ─────────────────────────────────

    _NOTIF_THEMES = {
        "modern": {
            "bg": "#0d1117", "bg_card": "#161b22", "border": "#30363d",
            "fg_main": "#e6edf3", "fg_sub": "#8b949e", "fg_dim": "#484f58",
            "accent": "#58a6ff", "btn_hover": "#1f6feb", "btn_fg": "#0d1117",
            "dot_color": "#3fb950", "dots_idle": "#30363d",
        },
        "brutalist-darkmode": {
            "bg": "#000000", "bg_card": "#0a0a0a", "border": "#ffffff",
            "fg_main": "#ffffff", "fg_sub": "#aaaaaa", "fg_dim": "#555555",
            "accent": "#ffffff", "btn_hover": "#dddddd", "btn_fg": "#000000",
            "dot_color": "#ffffff", "dots_idle": "#333333",
        },
        "girly-pinkish": {
            "bg": "#1a0a12", "bg_card": "#2d1020", "border": "#c2185b",
            "fg_main": "#fce4ec", "fg_sub": "#f48fb1", "fg_dim": "#ad1457",
            "accent": "#f06292", "btn_hover": "#e91e63", "btn_fg": "#1a0a12",
            "dot_color": "#f06292", "dots_idle": "#4a1530",
        },
        "flower-girl": {
            "bg": "#fdf6f0", "bg_card": "#fff9f5", "border": "#f8bbd0",
            "fg_main": "#4a1942", "fg_sub": "#ad6b8d", "fg_dim": "#c9a0b4",
            "accent": "#e91e8c", "btn_hover": "#c2185b", "btn_fg": "#ffffff",
            "dot_color": "#e91e8c", "dots_idle": "#f8bbd0",
        },
    }

    def notify(
        self,
        title: str = "Systema Auxilium",
        body: str = "",
        closing_time: int = 10,
        theme: str = "modern",
        close_button_text: str = "Close",
    ) -> None:
        """
        Fire-and-forget desktop notification popup.
        Runs in a daemon thread — never blocks the caller.

        Can be called from anywhere:
            controller.notify("Done!", "Task finished.")
            controller.notify("Alert", "Something happened.", theme="brutalist-darkmode")

        Also injected into the Python interpreter namespace as notify():
            notify("Hello", "World", closing_time=5)

        Themes: modern | brutalist-darkmode | girly-pinkish | flower-girl
        """
        log.info(f"[AssistantController.notify] title='{title}' | body='{body[:40]}' | "
                 f"theme='{theme}' | closing_time={closing_time}")

        def _run():
            t = self._NOTIF_THEMES.get(theme, self._NOTIF_THEMES["modern"])

            root = tk.Tk()
            root.title("Systema Auxilium")
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            root.geometry(f"420x220+{sw - 440}+{sh - 270}")
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.attributes("-alpha", 0.97)
            root.configure(bg=t["bg"])

            outer = tk.Frame(root, bg=t["border"], padx=1, pady=1)
            outer.pack(fill="both", expand=True)

            frame = tk.Frame(outer, bg=t["bg_card"], padx=20, pady=16)
            frame.pack(fill="both", expand=True)

            # Top bar
            top_bar = tk.Frame(frame, bg=t["bg_card"])
            top_bar.pack(fill="x", pady=(0, 10))

            dot_canvas = tk.Canvas(top_bar, width=10, height=10,
                                   bg=t["bg_card"], highlightthickness=0)
            dot_canvas.create_oval(1, 1, 9, 9, fill=t["dot_color"], outline="")
            dot_canvas.pack(side="left", padx=(0, 7), pady=2)

            tk.Label(top_bar, text="SYSTEMA AUXILIUM", bg=t["bg_card"],
                     fg=t["fg_sub"], font=("Segoe UI", 8, "bold")).pack(side="left")
            tk.Label(top_bar, text="●  ●  ●", bg=t["bg_card"],
                     fg=t["dots_idle"], font=("Segoe UI", 8)).pack(side="right")

            # Divider
            tk.Frame(frame, bg=t["border"], height=1).pack(fill="x", pady=(0, 12))

            # Title
            tk.Label(frame, text=title, bg=t["bg_card"], fg=t["fg_main"],
                     font=("Segoe UI", 13, "bold"),
                     justify="left", anchor="w").pack(fill="x")

            # Body
            tk.Label(frame, text=body, bg=t["bg_card"], fg=t["fg_sub"],
                     font=("Segoe UI", 9), justify="left", anchor="w",
                     wraplength=380).pack(fill="x", pady=(4, 0))

            # Bottom row
            bottom = tk.Frame(frame, bg=t["bg_card"])
            bottom.pack(fill="x", pady=(14, 0))

            countdown_label = tk.Label(
                bottom, text=f"Notification closing in {closing_time}s",
                bg=t["bg_card"], fg=t["fg_dim"], font=("Segoe UI", 8)
            )
            countdown_label.pack(side="left", anchor="s")

            btn = tk.Button(
                bottom, text=f"  {close_button_text}  ",
                command=root.destroy,
                bg=t["accent"], fg=t["btn_fg"],
                font=("Segoe UI", 9, "bold"),
                activebackground=t["btn_hover"], activeforeground="#ffffff",
                bd=0, padx=14, pady=5, cursor="hand2", relief="flat"
            )
            btn.pack(side="right")
            btn.bind("<Enter>", lambda e: btn.config(bg=t["btn_hover"], fg="#ffffff"))
            btn.bind("<Leave>", lambda e: btn.config(bg=t["accent"], fg=t["btn_fg"]))

            # Countdown logic
            _ct = [closing_time]
            def _tick():
                _ct[0] -= 1
                if _ct[0] <= 0:
                    root.destroy()
                    return
                countdown_label.config(text=f"Notification closing in {_ct[0]}s")
                root.after(1000, _tick)
            root.after(1000, _tick)

            root.mainloop()

        t = threading.Thread(target=_run, daemon=True, name="NotifPopup")
        t.start()