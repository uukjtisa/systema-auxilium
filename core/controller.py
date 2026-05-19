"""
core/controller.py
Main Controller - Orchestrates the AI assistant with Voice Support
MAIN INITIATOR FOR ALL UI AND CORE MODULES
UPDATED: work_environment and execute_code integration, voice mode, device management, TTS control
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from core.ai_engine import AIEngine
from core.system_info import get_system_info, format_system_info_for_prompt
from core.skill_manager import SkillManager
from ui.floating_window import FloatingWindow
from core.ai_worker import AIWorker
from core.session_manager import SessionManager
from pathlib import Path
import os
import json
import random
import socket
import threading
from core.logger import _make_logger, _NoOpLogger


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("Controller") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

# ── Anchor to app root at import time — immune to os.chdir() ─────────────────
# controller.py lives in core/, so parent = core/, parent.parent = app root
_APP_ROOT = Path(__file__).resolve().parent.parent
# ─────────────────────────────────────────────────────────────────────────────


class AssistantController(QObject):
    """Main controller for the AI assistant with voice support"""

    log_signal = pyqtSignal(str, str)
    voice_message_signal = pyqtSignal(str)

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

    def __init__(self):
        super().__init__()
        log.info("[AssistantController.__init__] ── Initializing AssistantController ──────────────")

        # Settings — absolute path anchored to app root, safe after os.chdir()
        self.settings_file = _APP_ROOT / "assistant_settings.json"
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
        from core.voice_handler import VoiceHandler
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

        # Wire chat window bridge into tool_manager so it can call add_system_message etc.
        self.ai.tool_manager._get_chat = lambda: self._chat
        self.ai.tool_manager._get_android_bridge = lambda: getattr(getattr(self, 'ui', None), 'android_bridge', None)

        # META CONTROL: Inject controller reference into Python interpreter namespace
        # so the AI can do: controller.current_session_id, controller.settings, etc.
        self.ai.tool_manager.tools['python'].namespace['controller'] = self

        # ── Agent Image Attach Hijacker ───────────────────────────────────────
        # Exposes attach_image() and take_screenshot() into the Python interpreter
        # namespace so the AI can call them from code execution on any thread.
        # Uses bridge_attach_image_signal to safely hop to the main Qt thread.

        def _agent_attach_image(path_or_paths):
            """Attach image(s) to the chat input as pinned context images.
            Can be called from any thread (uses Qt signal for thread safety).

            Usage inside code execution:
                attach_image(r"C:\\path\\to\\image.png")
                attach_image([r"C:\\img1.png", r"C:\\img2.png"])
            """
            if isinstance(path_or_paths, str):
                paths = [path_or_paths]
            else:
                paths = list(path_or_paths)
            self.bridge_attach_image_signal.emit(paths)
            return f"[attach_image] Queued {len(paths)} image(s) for attachment."

        def _agent_take_screenshot(save_path=None, attach=True):
            """Take a screenshot of the current screen.
            Saves to a temp file and optionally attaches it to the chat.

            Args:
                save_path: optional path to save screenshot (default: auto temp file)
                attach: if True, automatically pins the screenshot to the chat

            Returns:
                str: path to the saved screenshot file

            Usage inside code execution:
                path = take_screenshot()              # auto-attach
                path = take_screenshot(attach=False)  # just get the path
                path = take_screenshot(r"C:\\my_shot.png")
            """
            import time, os, tempfile
            try:
                from PIL import ImageGrab
                if save_path is None:
                    ts = int(time.time())
                    save_path = os.path.join(
                        tempfile.gettempdir(), f"agent_screenshot_{ts}.png"
                    )
                img = ImageGrab.grab()
                img.save(save_path)
                if attach:
                    _agent_attach_image(save_path)
                return save_path
            except ImportError:
                # Fallback: use pyautogui if Pillow/ImageGrab unavailable
                try:
                    import pyautogui, os, time, tempfile
                    if save_path is None:
                        ts = int(time.time())
                        save_path = os.path.join(
                            tempfile.gettempdir(), f"agent_screenshot_{ts}.png"
                        )
                    pyautogui.screenshot(save_path)
                    if attach:
                        _agent_attach_image(save_path)
                    return save_path
                except Exception as e2:
                    return f"[take_screenshot] ERROR: {e2}"
            except Exception as e:
                return f"[take_screenshot] ERROR: {e}"

        self.ai.tool_manager.tools['python'].namespace['attach_image'] = _agent_attach_image
        self.ai.tool_manager.tools['python'].namespace['take_screenshot'] = _agent_take_screenshot
        # ─────────────────────────────────────────────────────────────────────

        # Apply settings
        log.debug("[AssistantController.__init__] Applying AI provider settings...")
        self.ai.set_provider(self.settings.get('ai_provider', 'manual'))
        self.ai.set_tool_execution_lockout(self.settings.get('tool_execution_lockout', False))
        self.ai.set_system_prompt_hijack(
            self.settings.get('system_prompt_hijacked', False),
            self.settings.get('custom_system_prompt', '')
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
        self.work_mode_timer = QTimer()
        self.work_mode_timer.timeout.connect(self.auto_continue_work_mode)
        self.work_mode_timer.setInterval(1000)
        log.debug("[AssistantController.__init__] work_mode_timer created | interval=1000ms")

        # Track processing
        self.is_processing = False
        self._request_generation = 0  # incremented on each new request and on cancel

        # Connect voice message signal to handler (main thread safe)
        self.voice_message_signal.connect(self._handle_voice_message_on_main_thread)
        log.debug("[AssistantController.__init__] voice_message_signal connected to main thread handler")

        # Connect
        self.bridge_send_signal.connect(self.send_message)
        self.bridge_user_bubble_signal.connect(self._add_user_bubble_to_chat)
        self.bridge_load_session_signal.connect(self.load_session)
        self.bridge_new_session_signal.connect(self.create_new_session)
        self.bridge_attach_image_signal.connect(self._handle_bridge_attach_image)
        self.bridge_detach_image_signal.connect(self._handle_bridge_detach_image)
        self.task_message_signal.connect(self._handle_task_message)
        self.ai.manual_response_fn = self._request_manual_response
        self.manual_response_signal.connect(self._show_manual_response_window)
        log.debug("[AssistantController.__init__] manual_response_signal connected")

        # SESSION MANAGEMENT - Initialize session manager
        log.debug("[AssistantController.__init__] Initializing SessionManager...")
        self.session_manager = SessionManager()
        self.current_session_id = None
        self.session_has_messages = False

        # Create initial session
        self._create_new_session()
        log.debug(f"[AssistantController.__init__] Initial session created: '{self.current_session_id}'")

        # PATH SYNCER + MEMORY MANAGER — deferred until after the UI is painted
        # so the floating window appears immediately at startup
        self.memory_manager = None  # will be set by _deferred_bg_init
        QTimer.singleShot(300, self._deferred_bg_init)
        self.log("System AI Assistant initialized", "SUCCESS")
        log.info(f"[AssistantController.__init__] ✓ AssistantController ready | "
                 f"session='{self.current_session_id}' ──")

    # ── EXTRA INIT METHODS (Separated to load the UI faster) ───────────────────────────────────────────────────

    def _deferred_bg_init(self):
        """Heavy background services — runs after the UI is visible."""
        log.info("[AssistantController._deferred_bg_init] Starting deferred background init...")

        # PATH SYNCER
        try:
            from core.path_syncer import get_syncer
            get_syncer().start()
            log.info("[AssistantController._deferred_bg_init] ✓ PathSyncer started")
        except Exception as e:
            log.warning(f"[AssistantController._deferred_bg_init] PathSyncer failed (non-fatal): {e}")

        # MEMORY MANAGER
        try:
            from core.memory_manager import get_memory_manager
            self.memory_manager = get_memory_manager()
            log.info(f"[AssistantController._deferred_bg_init] ✓ MemoryManager ready | "
                     f"count={self.memory_manager.count()} | is_ready={self.memory_manager.is_ready}")
        except Exception as e:
            log.error(f"[AssistantController._deferred_bg_init] ✗ MemoryManager failed: {e}")
            self.memory_manager = None

        # TASK MANAGER — start after memory manager so settings are ready
        try:
            from core.task_manager import TaskManager
            self.task_manager = TaskManager(self)
            self.task_manager.start()
            log.info("[AssistantController._deferred_bg_init] ✓ TaskManager started")
        except Exception as e:
            log.error(f"[AssistantController._deferred_bg_init] ✗ TaskManager failed: {e}")
            self.task_manager = None

        log.info("[AssistantController._deferred_bg_init] ✓ Deferred init complete")

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

    def log(self, message, level="INFO"):
        """Emit message to UI log panel."""
        self.log_signal.emit(message, level)

    def _add_user_bubble_to_chat(self, text):
        """Thread-safe slot: add user bubble to PC chat window from Android."""
        if self._chat:
            self._chat.add_user_message(text)

    def load_settings(self):
        log.debug(f"[AssistantController.load_settings] Loading from '{self.settings_file}'")
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                log.info(f"[AssistantController.load_settings] ✓ Settings loaded | "
                         f"provider='{settings.get('ai_provider')}' | "
                         f"has_api_key={bool(settings.get('api_key'))}")
                return settings
        except Exception as e:
            log.error(f"[AssistantController.load_settings] ✗ Error loading settings: "
                      f"{type(e).__name__}: {e}")
            print(f"Error loading settings: {e}")

        # FIRST-TIME LAUNCH DEFAULTS
        log.info("[AssistantController.load_settings] No settings file found — using first-time defaults")
        default_tts = 'edge-tts'

        return {
            'ai_provider': None,
            'voice_input_device': None,
            'voice_output_device': None,
            'voice_tts_provider': default_tts,
            'voice_tts_voice': 'en-US-GuyNeural',
            'tts_provider': default_tts,
            'tts_script_path': '',
            'voice_vad_aggressiveness': 3,
            'voice_interrupt_mode': 'manual',
            'user_name': 'USER',
            'custom_instructions': '',
            'vad_webrtc_enabled': True,
            'vad_silero_enabled': False,
            'vad_aggressiveness': 3,
            'vad_silero_threshold': 0.5,
            'supervised_execution': True,  # Default ON for safety
            'memory_enabled': True,
            'memory_threshold': 0.4,  # float 0.0–1.0
            'memory_max_results': 5,
            'glass_background_enabled': False,
            'glass_background_opacity': 0.75,
            'custom_script_path': '',
            'tool_execution_lockout': False,
            'system_prompt_hijacked': False,
            'custom_system_prompt': ''
        }

    def save_settings(self):
        log.debug(f"[AssistantController.save_settings] Writing to '{self.settings_file}'")
        self.log("Saving settings...", "INFO")
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            self.log("Settings saved", "SUCCESS")
            log.info(f"[AssistantController.save_settings] ✓ Settings saved to '{self.settings_file}'")
        except Exception as e:
            log.error(f"[AssistantController.save_settings] ✗ Failed: {type(e).__name__}: {e}")
            self.log(f"Error saving settings: {e}", "ERROR")

    def enable_voice_mode(self):
        """Enable voice input/output"""
        log.info("[AssistantController.enable_voice_mode] ── Enabling voice mode ──────────────")
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

            # Get device names for display
            input_name = "Default"
            output_name = "Default"

            if input_device is not None:
                for dev in input_devices:
                    if dev['id'] == input_device:
                        input_name = dev['name']
                        break

            if output_device is not None:
                for dev in output_devices:
                    if dev['id'] == output_device:
                        output_name = dev['name']
                        break

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

                # Update AI engine with voice settings
                self.ai.update_voice_settings(True)

                message = (
                    f"**Input Device:** {input_name}\n"
                    f"**Output Device:** {output_name}\n\n"
                    "Start speaking! I'll listen and respond naturally."
                )

                self.log("Voice mode enabled", "SUCCESS")
                log.info(f"[AssistantController.enable_voice_mode] ✓ Voice mode active | "
                         f"input='{input_name}' | output='{output_name}'")
                return True, message
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

            # Then interrupt any ongoing speech
            log.debug("[AssistantController.disable_voice_mode] Interrupting speech...")
            self.voice_handler.interrupt_speech()

            # Update state
            self.voice_mode_active = False

            # Update AI engine
            self.ai.update_voice_settings(False, False)

            self.log("Voice mode disabled", "SUCCESS")
            log.info("[AssistantController.disable_voice_mode] ✓ Voice mode disabled")
        except Exception as e:
            log.error(f"[AssistantController.disable_voice_mode] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Error disabling voice: {e}", "ERROR")

    def _request_manual_response(self, context: str, work_mode: bool, work_output: str):
        """Called from the AIWorker thread — stores result_holder + done_event on
        self (so they stay as real references), then signals the main thread to
        show the popup and blocks until the user submits or cancels."""
        self._manual_result_holder = []
        self._manual_done_event = threading.Event()
        # Emit only display data — the window will read result_holder from self
        self.manual_response_signal.emit(context, work_mode, work_output)
        # Block the worker thread until the window is dismissed (by PC or Android)
        self._manual_done_event.wait()
        result = self._manual_result_holder[0] if self._manual_result_holder else None
        # Dismiss Android's dialog regardless of who responded first
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.dismiss_manual_response()
        return result

    def _show_manual_response_window(self, context, work_mode, work_output):
        """Slot — always runs on the main thread.  Creates and shows the popup."""
        from ui.manual_response_window import ManualResponseWindow
        win = ManualResponseWindow(context, work_mode, work_output,
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
            ab.request_manual_response(context, work_mode, work_output, on_android_manual_response)

    def _handle_bridge_attach_image(self, paths):
        """Slot — always runs on main thread. Pins images sent from the Android app."""
        if not self._chat:
            return
        for p in (paths or []):
            try:
                self._chat._show_image_preview(p)
            except Exception as e:
                log.error(f"[AssistantController._handle_bridge_attach_image] ✗ {e}")

    def _handle_bridge_detach_image(self, path):
        """Slot — always runs on main thread. Removes a pinned image the Android app detached."""
        if not self._chat or not path:
            return
        try:
            for pi in list(getattr(self._chat, 'pinned_images', [])):
                if pi.get('path') == path:
                    self._chat._remove_pinned_image(pi, notify=False)
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
        """Handle voice message on main Qt thread (slot for signal)"""
        log.debug(f"[AssistantController._handle_voice_message_on_main_thread] text='{text[:80]}'")
        # FIXED: Ensure chat window exists (create it if needed)
        if not hasattr(self.ui, 'chat_window') or self.ui.chat_window is None:
            from ui.chat_window import ChatWindow
            self.ui.chat_window = ChatWindow(self)
            log.debug("[AssistantController._handle_voice_message_on_main_thread] "
                      "Chat window created for voice message buffering")
            self.log("Chat window created for voice message buffering")

        # Always add to chat window (even if hidden)
        self._chat.add_user_message(text)

        # Send to AI (works whether chat is visible or not)
        log.debug("[AssistantController._handle_voice_message_on_main_thread] → send_message()")
        self.send_message(text)

    def handle_voice_state_change(self, state):
        """Handle voice state changes"""
        log.debug(f"[AssistantController.handle_voice_state_change] state='{state}'")
        self.log(f"Voice state: {state}")

        # Update UI
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
        from core.system_info import get_system_info, format_system_info_for_prompt

        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)

        # Add assistant name if set
        assistant_name = self.get_assistant_name()
        if assistant_name:
            log.debug(f"[AssistantController._update_system_prompt] Injecting assistant_name='{assistant_name}'")
            system_info_text += f"""

**CUSTOM ASSISTANT IDENTITY:**
Your name is **{assistant_name}**. This is the name given to you by the user and it is your primary identity.
- Always introduce yourself as {assistant_name}, never as "Systema Auxilium" unprompted.
- If asked who you are, say you are {assistant_name}.
- If the user asks whether you are Systema Auxilium, clarify: Systema Auxilium is the underlying agent platform you run on, but your identity as their personalized assistant is {assistant_name}.
- Never forget or deny your base architecture (Systema Auxilium), but your active name and personality is {assistant_name}.
- Example: "I'm {assistant_name}, your system assistant. (Systema Auxilium is the platform powering me.)"
"""
        else:
            system_info_text += """

**ASSISTANT IDENTITY:**
You are Systema Auxilium. If the user asks about your name, tell them it is Systema Auxilium — both your name and the platform you run on.
Let the user know they can give you a custom name from the sidebar (top-left ☰ menu → custom assistant name field).
"""

        # Add username if set
        user_name = self.get_user_name()
        if user_name:
            log.debug(f"[AssistantController._update_system_prompt] Injecting user_name='{user_name}'")
            system_info_text += f"\n\n**USER NAME:** {user_name}\nAddress the user by their name when appropriate."

        # Add custom instructions if set
        custom_instructions = self.get_custom_instructions()
        if custom_instructions:
            log.debug(f"[AssistantController._update_system_prompt] Injecting custom_instructions "
                      f"({len(custom_instructions)} chars)")
            system_info_text += f"\n\n**CUSTOM USER INSTRUCTIONS:**\n{custom_instructions}"

        # Update AI engine
        self.ai.system_info = system_info_text
        self.ai.update_voice_settings(self.ai.voice_mode, self.ai.elevenlabs_enabled)
        log.info("[AssistantController._update_system_prompt] ✓ System prompt updated")

    def speak_text(self, text):
        """Speak text using TTS (only if voice mode is active and not in tool mode)"""
        log.debug(f"[AssistantController.speak_text] voice_mode_active={self.voice_mode_active} | "
                  f"in_work_mode={self.ai.tool_manager.in_work_mode} | "
                  f"text_preview='{text[:50]}'")
        if not self.voice_mode_active:
            return

        if self.ai.tool_manager.in_work_mode:
            log.debug("[AssistantController.speak_text] Skipping TTS — in work mode")
            self.log("Skipping TTS - in tool mode")
            return

        log.debug("[AssistantController.speak_text] Speaking...")
        self.log(f"Speaking: {text[:50]}...")
        self.voice_handler.speak_text_sync(text)

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
        folder = _APP_ROOT / 'providers' / 'large-language-models'
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder)

    def get_tts_providers_folder(self):
        """Return absolute path to TTS providers folder, creating it if needed."""
        folder = _APP_ROOT / 'providers' / 'text-to-speech'
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

    def set_voice_input_device(self, device_id):
        """Set voice input device"""
        self.settings['voice_input_device'] = device_id
        self.save_settings()
        self.log(f"Voice input device SAVED", "SUCCESS")

    def set_voice_output_device(self, device_id):
        """Set voice output device"""
        self.log(f"{self.settings}", "DEBUG")
        self.settings['voice_output_device'] = device_id
        self.save_settings()
        self.log(f"Voice output device SAVED", "SUCCESS")

    def set_ai_provider(self, provider):
        """Set AI provider"""
        log.info(f"[AssistantController.set_ai_provider] provider='{provider}'")
        self.settings['ai_provider'] = provider
        self.ai.set_provider(provider)
        self.save_settings()
        self.log(f"AI provider set to: {provider}", "SUCCESS")

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
        self.save_settings()

    def get_debug_mode(self):
        """Get debug mode setting"""
        return self.settings.get('debug_mode', False)

    def set_debug_mode(self, enabled):
        """Set debug mode"""
        log.info(f"[AssistantController.set_debug_mode] enabled={enabled}")
        self.settings['debug_mode'] = enabled
        self.save_settings()
        self.log(f"Debug mode: {'enabled' if enabled else 'disabled'}", "SUCCESS")

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

        # If in tool mode and user sends message, cancel tool mode first
        if self.ai.tool_manager.in_work_mode:
            log.warning("[AssistantController.send_message] Message received during work mode — "
                        "canceling work mode")
            self.log("User interrupted tool mode - canceling")
            self.work_mode_timer.stop()
            self.ai.tool_manager.in_work_mode = False
            self.ai.tool_manager.last_work_output = None
            # Show system message
            if self._chat:
                self._chat.add_system_message("⚡️ Tool mode canceled by new message")

        # Prevent overlapping requests
        if self.is_processing:
            log.warning("[AssistantController.send_message] Already processing — ignoring request")
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True
        log.debug(f"[AssistantController.send_message] is_processing=True | "
                  f"provider='{self.ai.ai_provider}'")

        self.log(f"User: {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}")

        # Show thinking in UI
        self.ui.show_thinking()

        # Collect pinned images so Android-sent messages also reach the AI with images
        image_paths = []
        if self.get_ai_provider() == 'custom_script' and self._chat:
            image_paths = [pi['path'] for pi in getattr(self._chat, 'pinned_images', [])]
            if image_paths:
                log.debug(f"[AssistantController.send_message] Found {len(image_paths)} pinned image(s) — "
                          f"upgrading to generate_with_image")

        # Create worker thread
        if image_paths:
            log.debug("[AssistantController.send_message] Creating AIWorker for 'generate_with_image'...")
            self.current_worker = AIWorker(self.ai, 'generate_with_image', user_message, image_paths)
        else:
            log.debug("[AssistantController.send_message] Creating AIWorker for 'generate'...")
            self.current_worker = AIWorker(self.ai, 'generate', user_message)
        self._request_generation += 1
        _gen = self._request_generation
        self.current_worker.response_ready.connect(
            lambda result, g=_gen: self._dispatch_ai_response(result, g))
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.send_message] AIWorker started")

    def send_message_with_image(self, user_message, image_paths):
        """Send user message with one or more image attachments."""
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
        log.debug("[AssistantController.send_message_with_image] Creating AIWorker for 'generate_with_image'...")
        self.current_worker = AIWorker(self.ai, 'generate_with_image', user_message, image_paths)
        self._request_generation += 1
        _gen = self._request_generation
        self.current_worker.response_ready.connect(
            lambda result, g=_gen: self._dispatch_ai_response(result, g))
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.send_message_with_image] AIWorker started")

    def _dispatch_ai_response(self, result, generation):
        """Route a worker response to handle_ai_response only if it's still current."""
        if generation != self._request_generation:
            log.warning(f"[AssistantController._dispatch_ai_response] Stale response discarded "
                        f"(gen={generation}, current={self._request_generation})")
            return
        self.handle_ai_response(result)

    def _dispatch_work_mode_response(self, result, generation):
        """Route a work-mode response to handle_work_mode_response only if it's still current."""
        if generation != self._request_generation:
            log.warning(f"[AssistantController._dispatch_work_mode_response] Stale work-mode response discarded "
                        f"(gen={generation}, current={self._request_generation})")
            return
        self.handle_work_mode_response(result)

    def handle_ai_response(self, result):
        """Handle AI response from worker thread"""
        log.info(f"[AssistantController.handle_ai_response] ── Response received | "
                 f"executed={result.get('executed', False)} | "
                 f"has_work_call={result.get('has_work_call', False)} | "
                 f"exited_work_mode={result.get('exited_work_mode', False)} | "
                 f"thinking={result.get('thinking', False)} ──")
        self.ui.hide_thinking()
        self.is_processing = False
        log.debug("[AssistantController.handle_ai_response] is_processing=False | thinking hidden")

        # Debug mode: show COMPLETE raw AI response with detailed parsing
        if self.settings.get('debug_mode'):
            raw_response = self.ai.last_raw_response

            if raw_response:
                self.ui.show_debug_message("ai", f"••• RAW AI RESPONSE •••\n{raw_response}")

                if result.get('executed'):
                    self.ui.show_debug_message("system",
                                               f"••• EXECUTE_CODE DETECTED •••\n"
                                               f"Success: {result.get('execution_success', False)}\n\n"
                                               f"━━━ CODE ━━━\n"
                                               f"{result.get('code', 'N/A')}\n"
                                               f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                               f"Visible Text to User:\n{result.get('response', '(none)')}\n\n"
                                               f"⚡ Code executed immediately (no output to AI)")

                elif result.get('has_work_call'):
                    self.ui.show_debug_message("system",
                                               f"••• WORK_ENVIRONMENT CALL DETECTED •••\n"
                                               f"In Work Mode: {result.get('in_work_mode', False)}\n\n"
                                               f"━━━ CODE ━━━\n"
                                               f"{result.get('code', 'N/A')}\n"
                                               f"━━━━━━━━━━━━━━━━━\n\n"
                                               f"Visible Text to User:\n{result.get('response', '(none)')}\n\n"
                                               f"🐛 AI will see output and can chain more executions")

        # Handle execute_code in voice mode specially
        if result.get('executed') and self.voice_mode_active:
            log.debug("[AssistantController.handle_ai_response] execute_code in voice mode")
            self.log("EXECUTE_CODE in voice mode - will wait for voice then execute")

            if result.get('response') and result['response'].strip():
                self.ui.show_ai_message(result['response'])

            self.log("Execute_code completed")
            return

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

        # Show visible text immediately (only if not empty) - NORMAL MODE or TOOL MODE
        if result.get('response'):
            log.debug(f"[AssistantController.handle_ai_response] Showing AI message | "
                      f"len={len(result['response'])}")
            self.ui.show_ai_message(result['response'])

            # SESSION SAVE: Mark session as having messages and auto-save
            if not self.session_has_messages:
                self.session_has_messages = True
                log.debug("[AssistantController.handle_ai_response] session_has_messages=True")
            self._auto_save_session()

        # Check if AI set a session name
        if result.get('session_name'):
            log.debug(f"[AssistantController.handle_ai_response] AI set session name: "
                      f"'{result['session_name']}'")
            self.set_session_name(result['session_name'])

        # Show skill loaded/unloaded card in chat
        if result.get('skill_loaded'):
            skill_name = result['skill_loaded']
            if self._chat:
                self._chat.add_skill_card_message(skill_name, loaded=True)

        if result.get('skill_unloaded'):
            skill_name = result['skill_unloaded']
            if self._chat:
                self._chat.add_skill_card_message(skill_name, loaded=False)

        # Check if AI just exited tool mode — summary is already in the response above
        if result.get('exited_work_mode'):
            log.info(
                "[AssistantController.handle_ai_response] AI exited work mode — summary included in response")
            self.work_mode_timer.stop()
            return

        if result['thinking']:
            log.debug("[AssistantController.handle_ai_response] thinking=True — starting work_mode_timer")
            # Show code execution widget for the FIRST work_environment call
            if result.get('has_work_call') and result.get('code'):
                try:
                    tm_output = self.ai.tool_manager.last_work_output or ''
                    if tm_output and self._chat:
                        self._chat.add_code_execution_note(result['code'], tm_output)
                except Exception:
                    pass
            # Start tool mode timer
            self.work_mode_timer.start()

    def handle_work_mode_response(self, result):
        """Handle tool mode response from worker thread"""
        log.info(f"[AssistantController.handle_work_mode_response] ── Response received | "
                 f"exited_work_mode={result.get('exited_work_mode', False)} | "
                 f"has_work_call={result.get('has_work_call', False)} | "
                 f"thinking={result.get('thinking', False)} ──")
        self.is_processing = False
        log.debug("[AssistantController.handle_work_mode_response] is_processing=False")

        # Debug mode: show everything that happened
        if self.settings.get('debug_mode'):
            # Show work environment output first (if still in work mode)
            if self.ai.tool_manager.in_work_mode:
                work_output = self.ai.tool_manager.last_work_output
                if work_output:
                    self.ui.show_debug_message("tool", f"••• WORK ENVIRONMENT OUTPUT •••\n{work_output}")

            # Show the ORIGINAL raw AI response
            raw_response = self.ai.last_raw_response
            if raw_response:
                self.ui.show_debug_message("ai", f"••• RAW AI RESPONSE (Work Mode) •••\n{raw_response}")

                # Show parsed info if available
                if result.get('exited_work_mode'):
                    self.ui.show_debug_message("system",
                        f"••• EXITING WORK MODE •••\n"
                        f"AI's final message to user:\n{result.get('response', '(none)')}")
                elif result.get('has_work_call'):
                    self.ui.show_debug_message("system",
                        f"••• NEXT WORK_ENVIRONMENT CALL •••\n"
                        f"Code: {result.get('code', 'N/A')}\n\n"
                        f"━━━ CHAINING EXECUTION ━━━\n"
                        f"AI is analyzing output and chaining more executions...")

        # Check if AI set a session name
        if result.get('session_name'):
            log.debug(f"[AssistantController.handle_work_mode_response] AI set session name: "
                      f"'{result['session_name']}'")
            self.set_session_name(result['session_name'])

        # Update UI
        self.ui.handle_work_mode_update(result)

        # Check if AI just exited tool mode — summary is already in the response
        if result.get('exited_work_mode'):
            self.log("AI exited tool mode")
            self.work_mode_timer.stop()
            # SESSION SAVE: Persist the completed work session
            if not self.session_has_messages:
                self.session_has_messages = True
            self._auto_save_session()
            return

        if result['thinking']:
            # Still in tool mode - restart timer
            self.work_mode_timer.start()
        else:
            # Done with tool mode
            self.work_mode_timer.stop()
            # SESSION SAVE: Persist session when work mode finishes
            if not self.session_has_messages:
                self.session_has_messages = True
            self._auto_save_session()

    def handle_ai_error(self, error_message):
        """Handle AI error from worker thread"""
        log.error(f"[AssistantController.handle_ai_error] ✗ Error received: '{error_message[:120]}'")
        self.ui.hide_thinking()
        self.ui.show_ai_message(f"Error: {error_message}")
        self.work_mode_timer.stop()
        self.is_processing = False
        log.debug("[AssistantController.handle_ai_error] is_processing=False | timer stopped")
        self.log(f"AI Error: {error_message}", "ERROR")

        # Debug: Show error details
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("system", f"••• ERROR •••\n{error_message}")

    def auto_continue_work_mode(self):
        """Automatically continue tool mode (non-blocking)"""
        log.debug("[AssistantController.auto_continue_work_mode] Timer fired | "
                  f"in_work_mode={self.ai.tool_manager.in_work_mode} | "
                  f"is_processing={self.is_processing}")
        if not self.ai.tool_manager.in_work_mode:
            log.debug("[AssistantController.auto_continue_work_mode] Not in work mode — stopping timer")
            self.work_mode_timer.stop()
            return

        # Stop timer first to prevent duplicate calls
        self.work_mode_timer.stop()

        # Check if already processing
        if self.is_processing:
            log.warning("[AssistantController.auto_continue_work_mode] Already processing — skipping")
            self.log("Already processing - skipping tool mode continuation")
            return

        self.is_processing = True
        log.info("[AssistantController.auto_continue_work_mode] ── Continuing work mode ──────────────")

        # Debug: Show tool mode prompt being sent
        if self.settings.get('debug_mode'):
            tool_prompt = self.ai.tool_manager.get_work_mode_prompt()
            self.ui.show_debug_message("system", f"••• CONTINUING TOOL MODE •••\nSending to AI:\n{tool_prompt}")

        # Create worker thread
        log.debug("[AssistantController.auto_continue_work_mode] Creating AIWorker for 'continue_tool'")
        self.current_worker = AIWorker(self.ai, 'continue_tool')
        self._request_generation += 1
        _gen = self._request_generation
        self.current_worker.response_ready.connect(
            lambda result, g=_gen: self._dispatch_work_mode_response(result, g))
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.auto_continue_work_mode] Worker started")

    def interrupt_work_mode(self):
        """Interrupt and cancel tool mode"""
        log.info(f"[AssistantController.interrupt_work_mode] in_work_mode="
                 f"{self.ai.tool_manager.in_work_mode}")
        if self.ai.tool_manager.in_work_mode:
            log.warning("[AssistantController.interrupt_work_mode] Interrupting tool mode by user")
            self.log("Tool mode interrupted by user")
            self.work_mode_timer.stop()
            self.ai.tool_manager.in_work_mode = False
            self.ai.tool_manager.last_work_output = None
            self.is_processing = False

            # Notify UI
            if self._chat:
                self._chat.add_system_message("⚡️ **Tool operation canceled**")
                self._chat.hide_thinking()
                self._chat.set_input_enabled(True)

            return True
        log.debug("[AssistantController.interrupt_work_mode] Not in work mode — nothing to interrupt")
        return False

    def interrupt_request(self):
        """Interrupt current AI request and clean up state"""
        log.info(f"[AssistantController.interrupt_request] Interrupting | "
                 f"is_processing={self.is_processing} | "
                 f"in_work_mode={self.ai.tool_manager.in_work_mode}")
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
        if self.ai.tool_manager.in_work_mode:
            log.warning("[AssistantController.interrupt_request] Canceling active work mode")
            self.log("Tool mode interrupted by user")
            self.work_mode_timer.stop()
            self.ai.tool_manager.in_work_mode = False
            self.ai.tool_manager.last_work_output = None
            interrupted = True

        # Clear processing flag
        if self.is_processing:
            self.is_processing = False
            interrupted = True
            log.debug("[AssistantController.interrupt_request] is_processing cleared")

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
            chat_history
        )

        if success:
            if self._chat:
                self._chat.refresh_session_list()
            log.info(f"[AssistantController._auto_save_session] ✓ Session saved: '{self.current_session_id}'")
            self.log(f"Session auto-saved: {self.current_session_id}")
        else:
            log.error(f"[AssistantController._auto_save_session] ✗ Save failed: '{self.current_session_id}'")
            self.log(f"Failed to save session: {self.current_session_id}", "ERROR")

    def create_new_session(self):
        """Create new session (called from UI)"""
        log.info("[AssistantController.create_new_session] Creating new session from UI")
        # Save + create new session FIRST (while AI history is still intact)
        self._create_new_session()

        # NOW clear AI history and UI (after the old session has been saved)
        log.debug("[AssistantController.create_new_session] Clearing AI history...")
        self.ai.clear_history()

        if self._chat:
            self._chat.clear_chat_silent()
            self._chat.clear_pinned_images()  # detach all pinned images on new session

        # Refresh UI
        if self._chat:
            self._chat.refresh_session_list()
            self._chat.add_system_message("🆕 **New Session Created**")
            self._chat.warn_loaded_skills_if_any()
        log.info(f"[AssistantController.create_new_session] ✓ New session ready: '{self.current_session_id}'")
        ab = getattr(getattr(self, 'ui', None), 'android_bridge', None)
        if ab and ab.isVisible():
            ab.render_loaded_messages()

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

        # Load chat history into AI
        for msg in session_data['chat_history']:
            self.ai.conversation_history.append(msg)
        log.debug(f"[AssistantController.load_session] {history_len} messages loaded into AI history")

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
            # Clear chat
            if self._chat:
                self._chat.clear_chat_silent()

            # Clear AI history
            self.ai.clear_history()

            # Delete the session
            self.session_manager.delete_session(session_id)

            # Create new session
            self._create_new_session()

            # Refresh UI
            if self._chat:
                self._chat.refresh_session_list()
                self._chat.add_system_message("🗑️ **Session Deleted** - New session created")
            log.info(f"[AssistantController.delete_session] ✓ Active session deleted and replaced")
        else:
            log.debug(f"[AssistantController.delete_session] Deleting non-active session '{session_id}'")
            # Just delete the session
            self.session_manager.delete_session(session_id)

            # Refresh UI
            if self._chat:
                self._chat.refresh_session_list()
            log.info(f"[AssistantController.delete_session] ✓ Session deleted")

    def set_session_name(self, name):
        """Set name for current session (called by AI tool)"""
        log.info(f"[AssistantController.set_session_name] name='{name}' | "
                 f"session_id='{self.current_session_id}'")
        if not self.current_session_id:
            log.warning("[AssistantController.set_session_name] No current_session_id — aborting")
            return

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
            self.log(f"Failed to rename session", "ERROR")

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

    def reset_python_interpreter(self):
        """Reset the Python interpreter"""
        log.info("[AssistantController.reset_python_interpreter] Resetting Python interpreter...")
        self.ai.tool_manager.reset_python()
        log.info("[AssistantController.reset_python_interpreter] ✓ Reset complete")
        self.log("Python interpreter reset", "SUCCESS")

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
            log.info(f"[AssistantController.detach_memory_context] ✓ Done")
        else:
            log.warning(f"[AssistantController.detach_memory_context] Entry id='{context_id}' not found in history — session saved anyway")

    def get_ai_provider(self):
        """Get current AI provider"""
        return self.settings.get('ai_provider', 'custom_script')

