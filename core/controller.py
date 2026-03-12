"""
Main Controller - Orchestrates the AI assistant with Voice Support
MAIN INITIATOR FOR ALL UI AND CORE MODULES
UPDATED: work_environment and execute_code integration, voice mode, device management, TTS control
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from core.ai_engine import AIEngine
from core.puter_server import PuterServer
from core.system_info import get_system_info, format_system_info_for_prompt
from core.voice_handler import VoiceHandler
from core.skill_manager import SkillManager
from ui.floating_window import FloatingWindow
from core.ai_worker import AIWorker
from core.session_manager import SessionManager
from pathlib import Path
import os
import json
import random
import socket
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

    # NEW SIGNALS FOR VOICE
    voice_message_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        log.info("[AssistantController.__init__] ── Initializing AssistantController ──────────────")

        # Settings — absolute path anchored to app root, safe after os.chdir()
        self.settings_file = _APP_ROOT / "assistant_settings.json"
        log.debug(f"[AssistantController.__init__] Loading settings from '{self.settings_file}'")
        self.settings = self.load_settings()
        log.info(f"[AssistantController.__init__] Settings loaded | "
                 f"provider='{self.settings.get('ai_provider')}' | "
                 f"has_api_key={bool(self.settings.get('api_key'))}")

        # Detect system information
        self.log("Detecting system information...")
        log.debug("[AssistantController.__init__] Calling get_system_info()...")
        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)
        self.log(f"System detected: {system_info_dict['os']} {system_info_dict['os_release']}")
        log.info(f"[AssistantController.__init__] System info formatted | "
                 f"os='{system_info_dict['os']} {system_info_dict['os_release']}' | "
                 f"text_len={len(system_info_text)}")

        # Puter server
        _puter_port_file = _APP_ROOT / 'puter_port.txt'
        log.debug("[AssistantController.__init__] Checking puter_port.txt...")
        if not _puter_port_file.exists():
            _puter_port_file.write_text(
                '5555,8888,7777 #DO NOT DELETE THIS #ADD ANY PORT AS YOU WANT, IT WILL PRIORITIZE THE FIRST ON THE LEFT, SEPARATE WITH COMMAS. #ADD PORTS ON TOP ONLY. #CONTEXT IT IS BETTER IF THE PORT USED IS ONE THAT HAS BEEN USED BEFORE, SO IT WONT ASK FOR PUTER CONFIRMATION FOR THE FIRST MESSAGE.'
                )
            log.debug("[AssistantController.__init__] puter_port.txt created with defaults")

        ports = self._parse_ports()
        log.debug(f"[AssistantController.__init__] Parsed {len(ports)} port candidate(s): {ports}")

        port = None
        for p in ports:
            if self._is_open_port(p):
                if not p:
                    continue
                else:
                    port = p
                    break
        if not port:
            self.log("No available ports within puter_port.txt was available, resorting to random ports instead")
            log.warning("[AssistantController.__init__] No configured port available — will use random port")

        self.free_port = port if port else self.get_a_port()
        log.info(f"[AssistantController.__init__] Selected port: {self.free_port}")
        self.puter_server = PuterServer(port=self.free_port, log_callback=self.log)
        log.debug("[AssistantController.__init__] PuterServer instance created")

        # Voice handler
        log.debug("[AssistantController.__init__] Creating VoiceHandler...")
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
        self.skill_manager.start_watching()
        log.info(f"[AssistantController.__init__] SkillManager started | dir='{_skills_dir}'")

        # Initialize AI engine
        log.debug("[AssistantController.__init__] Creating AIEngine...")
        self.ai = AIEngine(
            log_callback=self.log,
            api_key=self.settings.get('api_key', ''),
            gemini_api_key=self.settings.get('gemini_api_key', ''),
            puter_server=self.puter_server,
            system_info=system_info_text,
            voice_mode=False,  # Start with voice off
            elevenlabs_enabled=self.settings.get('elevenlabs_enabled', False),
            settings_callback=lambda: self.settings,  # Pass settings getter
            skill_manager=self.skill_manager
        )
        log.info("[AssistantController.__init__] AIEngine created")

        # Apply settings
        log.debug("[AssistantController.__init__] Applying AI provider settings...")
        self.ai.set_provider(self.settings.get('ai_provider', 'anthropic'))
        self.ai.set_puter_model(self.settings.get('puter_model', 'gpt-4o-mini'))
        self.ai.set_gemini_model(self.settings.get('gemini_model', 'gemini-2.0-flash-exp'))
        self.ai.set_tts_provider(self.settings.get('tts_provider', 'edge-tts'))
        self.ai.set_puter_tts_model(self.settings.get('puter_tts_model', 'tts-1'))
        self.ai.set_puter_tts_voice(self.settings.get('puter_tts_voice'))
        self.ai.set_puter_timeout(self.settings.get('puter_timeout', 30))
        log.debug("[AssistantController.__init__] All AI settings applied")

        # Auto-start Puter if selected
        if self.settings.get('ai_provider') == 'puter':
            log.info("[AssistantController.__init__] ai_provider='puter' — auto-starting Puter server")
            self.start_puter_server()

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

        # Connect voice message signal to handler (main thread safe)
        self.voice_message_signal.connect(self._handle_voice_message_on_main_thread)
        log.debug("[AssistantController.__init__] voice_message_signal connected to main thread handler")

        # SESSION MANAGEMENT - Initialize session manager
        log.debug("[AssistantController.__init__] Initializing SessionManager...")
        self.session_manager = SessionManager()
        self.current_session_id = None
        self.session_has_messages = False

        # Create initial session
        self._create_new_session()
        log.debug(f"[AssistantController.__init__] Initial session created: '{self.current_session_id}'")

        # MEMORY MANAGER - Persistent RAG memory
        log.debug("[AssistantController.__init__] Initializing MemoryManager...")
        try:
            from core.memory_manager import get_memory_manager
            self.memory_manager = get_memory_manager()
            log.info(f"[AssistantController.__init__] ✓ MemoryManager ready | "
                     f"count={self.memory_manager.count()} | is_ready={self.memory_manager.is_ready}")
        except Exception as e:
            log.error(f"[AssistantController.__init__] ✗ MemoryManager failed: {type(e).__name__}: {e}")
            self.memory_manager = None

        self.log("System AI Assistant initialized", "SUCCESS")
        log.info(f"[AssistantController.__init__] ✓ AssistantController ready | "
                 f"provider='{self.ai.ai_provider}' | port={self.free_port} | "
                 f"session='{self.current_session_id}' ──")

    def _parse_ports(self):
        """
        Parse port numbers from puter_port.txt file
        """
        filepath = _APP_ROOT / 'puter_port.txt'
        ports = []
        log.debug(f"[AssistantController._parse_ports] Reading port config from '{filepath}'")

        try:
            with open(filepath, 'r') as f:
                f.seek(0)
                for line in f:
                    line = line.split('#')[0].strip()
                    if not line:
                        continue

                    port_strings = line.split(',')
                    for port_str in port_strings:
                        port_str = port_str.strip()
                        if port_str:
                            try:
                                port = int(port_str)
                                ports.append(port)
                            except ValueError:
                                log.warning(f"[AssistantController._parse_ports] Skipping invalid "
                                            f"port value: '{port_str}'")
                                print(f"Warning: Skipping invalid port '{port_str}'")
                                continue

        except FileNotFoundError:
            log.warning(f"[AssistantController._parse_ports] File not found: '{filepath}'")
            print(f"Warning: {filepath} not found, returning empty list")
            return []
        except Exception as e:
            log.error(f"[AssistantController._parse_ports] Error parsing ports: {type(e).__name__}: {e}")
            print(f"Error parsing ports: {e}")
            return []

        self.log(f"CHECKING PORTS:  {ports}")
        log.info(f"[AssistantController._parse_ports] Parsed {len(ports)} port(s): {ports}")
        return ports

    def _is_open_port(self, port):
        log.debug(f"[AssistantController._is_open_port] Testing port {port}...")
        # Check if port is actually available (not bound AND not in zombie state)
        test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        test.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            # Try to bind - this checks if something is actively using the port
            test.bind(("127.0.0.1", port))
            test.close()

            # Double check - try to connect to see if something is actually listening
            test2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test2.settimeout(0.1)
            result = test2.connect_ex(("127.0.0.1", port))
            test2.close()

            if result == 0:
                log.warning(f"[AssistantController._is_open_port] Port {port} — something is listening, NOT free")
                self.log(f"-----Port {port} is not available-----", "WARNING")
                return False
            log.debug(f"[AssistantController._is_open_port] Port {port} — confirmed free")
            self.log(f"-----Port {port} is available and will now be used-----", "SUCCESS")
            return True

        except OSError:
            log.warning(f"[AssistantController._is_open_port] Port {port} — bind failed (in use)")
            self.log(f"-----Port {port} is not available-----", "WARNING")
            return False
        finally:
            try:
                test.close()
            except:
                pass

    def get_a_port(self):
        log.debug("[AssistantController.get_a_port] Searching for a random free port...")
        while True:
            port = random.randint(1000, 9999)
            if self._is_open_port(port):
                log.info(f"[AssistantController.get_a_port] Found free random port: {port}")
                return port
            else:
                continue

    def log(self, message, level="INFO"):
        """Emit message to UI log panel."""
        self.log_signal.emit(message, level)

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
        default_provider = 'anthropic'
        default_tts = 'edge-tts'

        return {
            'api_key': '',
            'gemini_api_key': '',
            'ai_provider': default_provider,
            'puter_model': 'gpt-4o-mini',
            'gemini_model': 'gemini-2.0-flash-exp',
            'voice_input_device': None,
            'voice_output_device': None,
            'voice_tts_provider': default_tts,
            'voice_tts_voice': 'en-US-GuyNeural',
            'tts_provider': default_tts,  # pyttsx3 if LLaMA, else edge-tts
            'puter_tts_model': 'tts-1',
            'puter_tts_voice': None,
            'puter_email': '',
            'puter_password': '',
            'puter_timeout': 30,  # Default timeout in seconds for Puter.js server
            'voice_vad_aggressiveness': 3,
            'voice_interrupt_mode': 'manual',
            'elevenlabs_enabled': False,
            'elevenlabs_voice_id': '',
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

            if self.settings.get('tts_provider') == 'puter':
                log.debug("[AssistantController.enable_voice_mode] TTS provider is 'puter' — configuring Puter TTS")
                self.voice_handler.set_puter_server(self.puter_server)
                self.voice_handler.set_tts_provider('puter')
                self.voice_handler.set_puter_tts_settings(
                    self.settings.get('puter_tts_model', 'tts-1'),
                    self.settings.get('puter_tts_voice')
                )

                # Set ElevenLabs settings if enabled
                elevenlabs_enabled = self.get_elevenlabs_enabled()
                if elevenlabs_enabled:
                    voice_id = self.get_elevenlabs_voice_id()
                    self.voice_handler.set_elevenlabs_settings(True, voice_id)
                    log.debug(f"[AssistantController.enable_voice_mode] ElevenLabs configured | "
                              f"has_voice_id={bool(voice_id)}")

            # Set up playback callback with null check
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.voice_handler.on_playback_started = self.ui.chat_window.on_voice_playback_started
                self.log("Voice playback callback wired to chat window", "SUCCESS")
                log.debug("[AssistantController.enable_voice_mode] Playback callback wired to chat window")
            else:
                self.log("Chat window not available for voice callback", "WARNING")
                log.warning("[AssistantController.enable_voice_mode] Chat window not available for callback")

            log.debug("[AssistantController.enable_voice_mode] Starting voice listener...")
            if self.voice_handler.start_listening():
                self.voice_mode_active = True

                # Update AI engine with voice settings
                elevenlabs = self.get_elevenlabs_enabled()
                self.ai.update_voice_settings(True, elevenlabs)

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
            # Create chat window but don't show it
            from ui.chat_window import ChatWindow
            self.ui.chat_window = ChatWindow(self)
            log.debug("[AssistantController._handle_voice_message_on_main_thread] "
                      "Chat window created for voice message buffering")
            self.log("Chat window created for voice message buffering")

        # Always add to chat window (even if hidden)
        self.ui.chat_window.add_user_message(text)

        # Send to AI (works whether chat is visible or not)
        log.debug("[AssistantController._handle_voice_message_on_main_thread] → send_message()")
        self.send_message(text)

    def handle_voice_state_change(self, state):
        """Handle voice state changes"""
        log.debug(f"[AssistantController.handle_voice_state_change] state='{state}'")
        self.log(f"Voice state: {state}")

        # Update UI
        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            self.ui.chat_window.update_voice_status(state)

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

        # Add user name if set
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

    def get_elevenlabs_enabled(self):
        """Get ElevenLabs enabled state"""
        return self.settings.get('elevenlabs_enabled', False)

    def set_elevenlabs_enabled(self, enabled):
        """Set ElevenLabs enabled state"""
        log.info(f"[AssistantController.set_elevenlabs_enabled] enabled={enabled}")
        self.settings['elevenlabs_enabled'] = enabled
        self.save_settings()

        # CRITICAL: Update voice handler immediately
        voice_id = self.get_elevenlabs_voice_id()
        self.voice_handler.set_elevenlabs_settings(enabled, voice_id)

        self.log(f"ElevenLabs {'enabled' if enabled else 'disabled'}", "SUCCESS")

    def get_elevenlabs_voice_id(self):
        """Get ElevenLabs voice ID"""
        return self.settings.get('elevenlabs_voice_id', '')

    def set_elevenlabs_voice_id(self, voice_id):
        """Set ElevenLabs voice ID"""
        log.info(f"[AssistantController.set_elevenlabs_voice_id] has_voice_id={bool(voice_id)}")
        self.settings['elevenlabs_voice_id'] = voice_id
        self.save_settings()

        # CRITICAL: Update voice handler immediately
        enabled = self.get_elevenlabs_enabled()
        self.voice_handler.set_elevenlabs_settings(enabled, voice_id)

        self.log(f"ElevenLabs voice ID set", "SUCCESS")

    def get_tts_provider(self):
        return self.settings.get('tts_provider', 'edge-tts')

    def set_tts_provider(self, provider):
        log.info(f"[AssistantController.set_tts_provider] provider='{provider}'")
        self.settings['tts_provider'] = provider
        self.ai.set_tts_provider(provider)
        self.save_settings()
        self.log(f"TTS provider set to {provider}", "SUCCESS")

    def get_puter_tts_model(self):
        return self.settings.get('puter_tts_model', 'tts-1')

    def set_puter_tts_model(self, model):
        log.info(f"[AssistantController.set_puter_tts_model] model='{model}'")
        self.settings['puter_tts_model'] = model
        self.ai.set_puter_tts_model(model)
        self.save_settings()
        self.log(f"Puter TTS model set to {model}", "SUCCESS")

    def get_puter_tts_voice(self):
        return self.settings.get('puter_tts_voice')

    def set_puter_tts_voice(self, voice):
        log.info(f"[AssistantController.set_puter_tts_voice] voice='{voice}'")
        self.settings['puter_tts_voice'] = voice
        self.ai.set_puter_tts_voice(voice)
        self.save_settings()
        self.log(f"Puter TTS voice set to {voice}", "SUCCESS")

    def get_puter_credentials(self):
        return {
            'email': self.settings.get('puter_email', ''),
            'password': self.settings.get('puter_password', '')
        }

    def set_puter_credentials(self, email, password):
        log.info(f"[AssistantController.set_puter_credentials] email='{email}' | has_password={bool(password)}")
        self.settings['puter_email'] = email
        self.settings['puter_password'] = password
        self.save_settings()
        self.log("Puter credentials saved", "SUCCESS")

    def set_puter_timeout(self, timeout):
        """Set Puter.js server timeout in seconds"""
        log.info(f"[AssistantController.set_puter_timeout] timeout={timeout}s")
        self.settings['puter_timeout'] = timeout
        self.ai.set_puter_timeout(timeout)
        self.save_settings()
        self.log(f"Puter timeout set to {timeout} seconds", "SUCCESS")

    def reset_puter_quota(self):
        """Reset Puter quota using saved credentials"""
        log.info("[AssistantController.reset_puter_quota] Attempting quota reset...")
        creds = self.get_puter_credentials()
        if not creds['email'] or not creds['password']:
            log.warning("[AssistantController.reset_puter_quota] No credentials set — aborting")
            self.log("Puter credentials not set", "ERROR")
            return False

        if not self.puter_server or not self.puter_server.is_running:
            log.warning("[AssistantController.reset_puter_quota] Puter server not running — aborting")
            self.log("Puter server not running", "ERROR")
            return False

        self.log("Resetting Puter quota...", "INFO")
        success = self.puter_server.reset_quota(creds['email'], creds['password'])
        if success:
            log.info("[AssistantController.reset_puter_quota] ✓ Quota reset successful")
            self.log("✓ Quota reset successful!", "SUCCESS")
        else:
            log.error("[AssistantController.reset_puter_quota] ✗ Quota reset failed")
            self.log("✗ Quota reset failed", "ERROR")
        return success

    def setup_puter_account(self):
        """Open Puter for account setup"""
        log.info("[AssistantController.setup_puter_account] Starting Puter account setup...")
        if not self.puter_server or not self.puter_server.is_running:
            log.info("[AssistantController.setup_puter_account] Puter server not running — starting it first")
            self.log("Starting Puter server for account setup...", "INFO")
            if not self.start_puter_server():
                log.error("[AssistantController.setup_puter_account] ✗ Failed to start Puter server")
                return False

        log.debug("[AssistantController.setup_puter_account] → puter_server.setup_account()")
        return self.puter_server.setup_account()

    def get_puter_tts_models(self):
        """Get available Puter TTS models"""
        return [
            {'id': 'tts-1', 'name': 'TTS-1 (Standard)', 'description': 'Standard quality, fast'},
            {'id': 'tts-1-hd', 'name': 'TTS-1-HD (High Quality)', 'description': 'Higher quality, slower'},
            {'id': 'gpt-4o-mini-tts', 'name': 'GPT-4o Mini TTS', 'description': 'Advanced neural TTS'}
        ]

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

    def get_api_key(self):
        """Get current API key"""
        return self.settings.get('api_key', '')

    def set_api_key(self, api_key):
        """Set new API key"""
        log.info(f"[AssistantController.set_api_key] has_key={bool(api_key)}")
        self.settings['api_key'] = api_key
        self.ai.set_api_key(api_key)
        self.save_settings()
        self.log("API key updated", "SUCCESS")

    def get_gemini_api_key(self):
        """Get current Gemini API key"""
        return self.settings.get('gemini_api_key', '')

    def set_gemini_api_key(self, api_key):
        """Set new Gemini API key"""
        log.info(f"[AssistantController.set_gemini_api_key] has_key={bool(api_key)}")
        self.settings['gemini_api_key'] = api_key
        self.ai.set_gemini_api_key(api_key)
        self.save_settings()
        self.log("Gemini API key updated", "SUCCESS")

    def get_ai_provider(self):
        """Get current AI provider"""
        return self.settings.get('ai_provider', 'anthropic')

    def set_ai_provider(self, provider):
        """Set AI provider"""
        log.info(f"[AssistantController.set_ai_provider] provider='{provider}'")
        # Check if we're switching AWAY from Puter
        current_provider = self.settings.get('ai_provider', 'anthropic')
        if current_provider == 'puter' and provider != 'puter':
            log.info(f"[AssistantController.set_ai_provider] Switching away from Puter — stopping server")
            # Switching away from Puter - close the browser
            self.log("Switching away from Puter - closing browser window...", "INFO")
            self.stop_puter_server()

        self.settings['ai_provider'] = provider
        self.ai.set_provider(provider)
        self.save_settings()
        self.log(f"AI provider set to: {provider}", "SUCCESS")

        # Auto-start Puter if selected
        if provider == 'puter' and not self.puter_server.is_running:
            self.start_puter_server()

    def get_puter_model(self):
        """Get current Puter model"""
        return self.settings.get('puter_model', 'gpt-4o-mini')

    def set_puter_model(self, model):
        """Set Puter model"""
        log.info(f"[AssistantController.set_puter_model] model='{model}'")
        self.settings['puter_model'] = model
        self.ai.set_puter_model(model)
        self.save_settings()
        self.log(f"Puter model set to: {model}", "SUCCESS")

    def get_gemini_model(self):
        """Get current Gemini model"""
        return self.settings.get('gemini_model', 'gemini-2.0-flash-exp')

    def set_gemini_model(self, model):
        """Set Gemini model"""
        log.info(f"[AssistantController.set_gemini_model] model='{model}'")
        self.settings['gemini_model'] = model
        self.ai.set_gemini_model(model)
        self.save_settings()
        self.log(f"Gemini model set to: {model}", "SUCCESS")

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

    def start_puter_server(self):
        """Start Puter.js server"""
        log.info("[AssistantController.start_puter_server] ── Starting Puter.js server ──────────────")
        try:
            self.log("Starting Puter.js server...")

            if self.puter_server.is_running:
                log.debug("[AssistantController.start_puter_server] Server already running — checking health")
                self.log("Puter server already running")
                if self.puter_server.check_health():
                    log.info("[AssistantController.start_puter_server] ✓ Already healthy — skipping start")
                    return True
                else:
                    log.warning("[AssistantController.start_puter_server] Not responding — restarting...")
                    self.log("Server not responding, restarting...")
                    self.puter_server.stop()
                    import time
                    time.sleep(1)

            if self.puter_server.start():
                log.info(f"[AssistantController.start_puter_server] ✓ Started at http://127.0.0.1:{self.free_port}")
                self.log(f"✓ Puter.js server started at http://127.0.0.1:{self.free_port}", "SUCCESS")

                # Wait a moment
                import time
                time.sleep(2)

                return True
            else:
                log.error("[AssistantController.start_puter_server] ✗ puter_server.start() returned False")
                self.log("✗ Failed to start Puter server", "ERROR")
                return False

        except Exception as e:
            log.error(f"[AssistantController.start_puter_server] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Error starting Puter server: {e}", "ERROR")
            return False

    def stop_puter_server(self):
        """Stop Puter.js server"""
        log.info("[AssistantController.stop_puter_server] Stopping Puter.js server...")
        try:
            self.puter_server.stop()
            log.info("[AssistantController.stop_puter_server] ✓ Server stopped")
            self.log("Puter.js server stopped", "SUCCESS")
        except Exception as e:
            log.error(f"[AssistantController.stop_puter_server] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Error stopping Puter server: {e}", "ERROR")

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
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message("⚡️ Tool mode canceled by new message")

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

        # Create worker thread
        log.debug("[AssistantController.send_message] Creating AIWorker for 'generate'...")
        self.current_worker = AIWorker(self.ai, 'generate', user_message)
        self.current_worker.response_ready.connect(self.handle_ai_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.send_message] AIWorker started")

    def send_message_with_image(self, user_message, image_path):
        """Send user message with image attachment to AI (Puter only)"""
        log.info(f"[AssistantController.send_message_with_image] ── Incoming image message | "
                 f"image_path='{image_path}' | msg_preview='{user_message[:60].replace(chr(10), '↵')}' ──")

        if not user_message.strip():
            log.debug("[AssistantController.send_message_with_image] Empty message — ignoring")
            return

        if self.get_ai_provider() != 'puter':
            log.warning("[AssistantController.send_message_with_image] Non-Puter provider — image not supported")
            self.log("Image attachment only supported with Puter provider", "ERROR")
            return

        # Prevent overlapping requests
        if self.is_processing:
            log.warning("[AssistantController.send_message_with_image] Already processing — ignoring request")
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True
        log.debug(f"[AssistantController.send_message_with_image] is_processing=True | "
                  f"provider='{self.ai.ai_provider}'")

        self.log(f"User (with image): {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}\n[Image: {image_path}]")

        # Show thinking in UI
        self.ui.show_thinking()

        # Create worker thread with image
        log.debug("[AssistantController.send_message_with_image] Creating AIWorker for 'generate_with_image'...")
        self.current_worker = AIWorker(self.ai, 'generate_with_image', user_message, image_path)
        self.current_worker.response_ready.connect(self.handle_ai_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()
        log.debug("[AssistantController.send_message_with_image] AIWorker started")

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
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_skill_card_message(skill_name, loaded=True)

        if result.get('skill_unloaded'):
            skill_name = result['skill_unloaded']
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_skill_card_message(skill_name, loaded=False)

        # Check if AI just exited tool mode
        if result.get('exited_work_mode'):
            log.info("[AssistantController.handle_ai_response] AI exited work mode — "
                     "scheduling post-exit prompt in 500ms")
            self.log("AI exited work mode - sending post-exit prompt")
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message(
                    "🔄 **Work Completed** - AI is now preparing its report..."
                )
            QTimer.singleShot(500, self.send_post_exit_prompt)
            return

        if result['thinking']:
            log.debug("[AssistantController.handle_ai_response] thinking=True — starting work_mode_timer")
            # Start tool mode timer
            self.work_mode_timer.start()

    def send_post_exit_prompt(self):
        """NEW: Send post-exit prompt to guide AI to report findings"""
        log.info("[AssistantController.send_post_exit_prompt] ── Sending post-exit prompt ──────────────")
        if self.is_processing:
            log.warning("[AssistantController.send_post_exit_prompt] Already processing — skipping")
            self.log("Already processing - skipping post-exit prompt")
            return

        self.is_processing = True
        log.debug("[AssistantController.send_post_exit_prompt] is_processing=True | showing thinking")
        self.ui.show_thinking()

        # Debug: Show that we're sending post-exit prompt
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("system",
                "••• SENDING POST-EXIT PROMPT •••\n"
                "Asking AI to report its findings to the user...")

        # Create worker thread for post-exit prompt
        self.current_worker = AIWorker(self.ai, 'post_exit')
        self.current_worker.response_ready.connect(self.handle_post_exit_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def handle_post_exit_response(self, result):
        """Handle response after post-exit prompt"""
        log.info(f"[AssistantController.handle_post_exit_response] ── Response received | "
                 f"has_response={bool(result.get('response'))} | "
                 f"session_name={repr(result.get('session_name'))} ──")
        self.ui.hide_thinking()
        self.is_processing = False
        log.debug("[AssistantController.handle_post_exit_response] is_processing=False | thinking hidden")

        # Debug mode
        if self.settings.get('debug_mode'):
            raw_response = self.ai.last_raw_response
            if raw_response:
                self.ui.show_debug_message("ai",
                    f"••• AI's REPORT TO USER •••\n{raw_response}")

        # Show the AI's report to user
        if result.get('response'):
            log.debug(f"[AssistantController.handle_post_exit_response] Showing AI message | "
                      f"len={len(result['response'])}")
            self.ui.show_ai_message(result['response'])

            # SESSION SAVE: Mark session as having messages and auto-save
            if not self.session_has_messages:
                self.session_has_messages = True
                log.debug("[AssistantController.handle_post_exit_response] session_has_messages=True")
            self._auto_save_session()

        # Handle session rename (AI may set name in post-exit report)
        if result.get('session_name'):
            log.debug(f"[AssistantController.handle_post_exit_response] AI set session name: "
                      f"'{result['session_name']}'")
            self.set_session_name(result['session_name'])

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

        # Update UI
        self.ui.handle_work_mode_update(result)

        # NEW: Check if AI just exited tool mode
        if result.get('exited_work_mode'):
            self.log("AI exited tool mode - sending post-exit prompt")
            self.work_mode_timer.stop()
            # Show system message FIRST
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message(
                    "🔄 **Work Completed** - AI is now preparing its report..."
                )
            # Send post-exit prompt with delay
            QTimer.singleShot(500, self.send_post_exit_prompt)
            return

        if result['thinking']:
            # Still in tool mode - restart timer
            self.work_mode_timer.start()
        else:
            # Done with tool mode
            self.work_mode_timer.stop()

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
        self.current_worker.response_ready.connect(self.handle_work_mode_response)
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
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message("⚡️ **Tool operation canceled**")
                self.ui.chat_window.hide_thinking()
                self.ui.chat_window.set_input_enabled(True)

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
            self.ui.chat_window.refresh_session_list()
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

        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            self.ui.chat_window.clear_chat_silent()

        # Refresh UI
        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            self.ui.chat_window.refresh_session_list()
            self.ui.chat_window.add_system_message("🆕 **New Session Created**")
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
        session_data = self.session_manager.load_session(session_id)

        if not session_data:
            log.error(f"[AssistantController.load_session] ✗ Failed to load session: '{session_id}'")
            self.log(f"Failed to load session: {session_id}", "ERROR")
            return False

        history_len = len(session_data.get('chat_history', []))
        log.debug(f"[AssistantController.load_session] Session data loaded | "
                  f"name='{session_data.get('session_name')}' | history={history_len} entries")

        # Clear chat UI
        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            self.ui.chat_window.clear_chat_silent()

        # Clear AI history
        self.ai.clear_history()

        # Load chat history into AI
        for msg in session_data['chat_history']:
            self.ai.conversation_history.append(msg)
        log.debug(f"[AssistantController.load_session] {history_len} messages loaded into AI history")

        # Render messages in UI — strip tool call JSON from assistant messages
        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            display_history = []
            for msg in session_data['chat_history']:
                if msg.get('role') == 'assistant':
                    cleaned_content = self.ai.tool_manager.strip_tool_calls(
                        msg.get('content', '')
                    )
                    display_history.append({**msg, 'content': cleaned_content})
                else:
                    display_history.append(msg)
            self.ui.chat_window.render_loaded_messages(display_history)
            log.debug("[AssistantController.load_session] Messages rendered in UI (tool calls stripped)")

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
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.clear_chat_silent()

            # Clear AI history
            self.ai.clear_history()

            # Delete the session
            self.session_manager.delete_session(session_id)

            # Create new session
            self._create_new_session()

            # Refresh UI
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.refresh_session_list()
                self.ui.chat_window.add_system_message("🗑️ **Session Deleted** - New session created")
            log.info(f"[AssistantController.delete_session] ✓ Active session deleted and replaced")
        else:
            log.debug(f"[AssistantController.delete_session] Deleting non-active session '{session_id}'")
            # Just delete the session
            self.session_manager.delete_session(session_id)

            # Refresh UI
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.refresh_session_list()
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
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.refresh_session_list()
        else:
            log.error(f"[AssistantController.set_session_name] ✗ Failed to rename to '{name}'")
            self.log(f"Failed to rename session", "ERROR")

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

    def get_puter_models(self):
        """Get available Puter.js models"""
        return [
            # ===== GPT-5.x (Latest) =====
            {'id': 'gpt-5.2-chat', 'name': 'GPT-5.2 Chat (Recommended)', 'description': 'Latest flagship chat model'},
            {'id': 'gpt-5.2-pro', 'name': 'GPT-5.2 Pro', 'description': 'Highest capability GPT-5.2'},
            {'id': 'gpt-5.2', 'name': 'GPT-5.2', 'description': 'Base GPT-5.2 model'},

            {'id': 'gpt-5.1-chat-latest', 'name': 'GPT-5.1 Chat', 'description': 'Stable GPT-5.1 chat model'},
            {'id': 'gpt-5.1', 'name': 'GPT-5.1', 'description': 'Base GPT-5.1 model'},

            {'id': 'gpt-5-chat-latest', 'name': 'GPT-5 Chat', 'description': 'Original GPT-5 chat model'},
            {'id': 'gpt-5', 'name': 'GPT-5', 'description': 'Base GPT-5 model'},
            {'id': 'gpt-5-mini', 'name': 'GPT-5 Mini', 'description': 'Fast and efficient GPT-5'},
            {'id': 'gpt-5-nano', 'name': 'GPT-5 Nano', 'description': 'Ultra-light, ultra-fast GPT-5'},

            # ===== GPT-4.x =====
            {'id': 'gpt-4.5-preview', 'name': 'GPT-4.5 Preview', 'description': 'Preview build of GPT-4.5'},
            {'id': 'gpt-4.1', 'name': 'GPT-4.1', 'description': 'Enhanced GPT-4'},
            {'id': 'gpt-4.1-mini', 'name': 'GPT-4.1 Mini', 'description': 'Fast GPT-4.1'},
            {'id': 'gpt-4.1-nano', 'name': 'GPT-4.1 Nano', 'description': 'Lightweight GPT-4.1'},

            # ===== Omni =====
            {'id': 'gpt-4o', 'name': 'GPT-4o', 'description': 'Omni-modal GPT-4'},
            {'id': 'gpt-4o-mini', 'name': 'GPT-4o Mini', 'description': 'Efficient omni-modal model'},

            # ===== Reasoning (o-series) =====
            {'id': 'o4-mini', 'name': 'o4 Mini', 'description': 'Latest compact reasoning model'},
            {'id': 'o3', 'name': 'o3', 'description': 'Advanced reasoning'},
            {'id': 'o3-mini', 'name': 'o3 Mini', 'description': 'Compact reasoning'},
            {'id': 'o1-pro', 'name': 'o1 Pro', 'description': 'High-tier reasoning'},
            {'id': 'o1', 'name': 'o1', 'description': 'Reasoning model'},
            {'id': 'o1-mini', 'name': 'o1 Mini', 'description': 'Lightweight reasoning'},

            # ===== OpenRouter - OpenAI OSS =====
            {'id': 'openrouter:openai/gpt-oss-120b', 'name': 'GPT-OSS 120B', 'description': 'Open-source 120B model'},
            {'id': 'openrouter:openai/gpt-oss-120b:exacto', 'name': 'GPT-OSS 120B Exacto',
             'description': 'Deterministic 120B variant'},
            {'id': 'openrouter:openai/gpt-oss-20b', 'name': 'GPT-OSS 20B', 'description': 'Open-source 20B model'},
            {'id': 'openrouter:openai/gpt-oss-20b:free', 'name': 'GPT-OSS 20B Free',
             'description': 'Free tier 20B model'},
            {'id': 'openrouter:openai/gpt-oss-safeguard-20b', 'name': 'GPT-OSS Safeguard 20B',
             'description': 'Safety-focused OSS model'},

            # ===== Codex / Coding =====
            {'id': 'openrouter:openai/codex-mini', 'name': 'Codex Mini',
             'description': 'Fast lightweight coding model'},
            {'id': 'openrouter:openai/gpt-5-codex', 'name': 'GPT-5 Codex', 'description': 'Advanced GPT-5 coding'},
            {'id': 'openrouter:openai/gpt-5.1-codex', 'name': 'GPT-5.1 Codex', 'description': 'Stable GPT-5.1 coding'},
            {'id': 'openrouter:openai/gpt-5.1-codex-max', 'name': 'GPT-5.1 Codex Max',
             'description': 'Maximum-power coding model'},
            {'id': 'openrouter:openai/gpt-5.1-codex-mini', 'name': 'GPT-5.1 Codex Mini',
             'description': 'Efficient coding model'},

            # ===== Claude (Direct) =====
            {'id': 'claude-sonnet-4', 'name': 'Claude Sonnet 4', 'description': 'Anthropic Sonnet 4'},
            {'id': 'claude-sonnet-4-5', 'name': 'Claude Sonnet 4.5', 'description': 'Anthropic Sonnet 4.5'},
            {'id': 'claude-opus-4', 'name': 'Claude Opus 4', 'description': 'Anthropic Opus 4 flagship'},
            {'id': 'claude-opus-4-1', 'name': 'Claude Opus 4.1', 'description': 'Anthropic Opus 4.1'},
            {'id': 'claude-opus-4-5', 'name': 'Claude Opus 4.5', 'description': 'Anthropic Opus 4.5'},
            {'id': 'claude-opus-4-6', 'name': 'Claude Opus 4.6', 'description': 'Anthropic Opus 4.6'},
            {'id': 'claude-haiku-4-5', 'name': 'Claude Haiku 4.5',
             'description': 'Anthropic Haiku 4.5 - fast and efficient'},

            # ===== DeepSeek (Direct) =====
            {'id': 'deepseek/deepseek-chat', 'name': 'DeepSeek Chat', 'description': 'DeepSeek general chat model'},
            {'id': 'deepseek/deepseek-chat-v3-0324', 'name': 'DeepSeek Chat v3 (03-24)',
             'description': 'DeepSeek Chat v3 March 2024'},
            {'id': 'deepseek/deepseek-chat-v3.1', 'name': 'DeepSeek Chat v3.1', 'description': 'DeepSeek Chat v3.1'},
            {'id': 'deepseek/deepseek-r1', 'name': 'DeepSeek R1', 'description': 'DeepSeek reasoning model'},
            {'id': 'deepseek/deepseek-r1-0528', 'name': 'DeepSeek R1 (05-28)',
             'description': 'DeepSeek R1 May 2028 update'},
            {'id': 'deepseek/deepseek-r1-0528:free', 'name': 'DeepSeek R1 (05-28) Free',
             'description': 'Free tier R1 May update'},
            {'id': 'deepseek/deepseek-r1-distill-llama-70b', 'name': 'DeepSeek R1 Distill LLaMA 70B',
             'description': 'R1 distilled into LLaMA 70B'},
            {'id': 'deepseek/deepseek-r1-distill-qwen-32b', 'name': 'DeepSeek R1 Distill Qwen 32B',
             'description': 'R1 distilled into Qwen 32B'},
            {'id': 'deepseek/deepseek-reasoner', 'name': 'DeepSeek Reasoner',
             'description': 'DeepSeek dedicated reasoning model'},
            {'id': 'deepseek/deepseek-v3.1-terminus', 'name': 'DeepSeek V3.1 Terminus',
             'description': 'DeepSeek V3.1 Terminus variant'},
            {'id': 'deepseek/deepseek-v3.1-terminus:exacto', 'name': 'DeepSeek V3.1 Terminus Exacto',
             'description': 'Deterministic Terminus variant'},
            {'id': 'deepseek/deepseek-v3.2', 'name': 'DeepSeek V3.2', 'description': 'DeepSeek V3.2'},
            {'id': 'deepseek/deepseek-v3.2-exp', 'name': 'DeepSeek V3.2 Experimental',
             'description': 'Experimental DeepSeek V3.2'},
            {'id': 'deepseek/deepseek-v3.2-speciale', 'name': 'DeepSeek V3.2 Speciale',
             'description': 'Special edition DeepSeek V3.2'},

            # ===== Gemini (Direct) =====
            {'id': 'gemini-3-pro-preview', 'name': 'Gemini 3 Pro Preview',
             'description': 'Google Gemini 3 Pro Preview'},
            {'id': 'gemini-3-flash-preview', 'name': 'Gemini 3 Flash Preview',
             'description': 'Google Gemini 3 Flash Preview'},
            {'id': 'gemini-2.5-pro-preview-05-06', 'name': 'Gemini 2.5 Pro Preview (05-06)',
             'description': 'Gemini 2.5 Pro Preview May 2025'},
            {'id': 'gemini-2.5-pro-preview', 'name': 'Gemini 2.5 Pro Preview', 'description': 'Gemini 2.5 Pro Preview'},
            {'id': 'gemini-2.5-pro', 'name': 'Gemini 2.5 Pro', 'description': 'Google Gemini 2.5 Pro'},
            {'id': 'gemini-2.5-flash-preview-09-2025', 'name': 'Gemini 2.5 Flash Preview (Sep 2025)',
             'description': 'Gemini 2.5 Flash Sep 2025 Preview'},
            {'id': 'gemini-2.5-flash-lite-preview-09-2025', 'name': 'Gemini 2.5 Flash Lite Preview (Sep 2025)',
             'description': 'Gemini 2.5 Flash Lite Sep 2025 Preview'},
            {'id': 'gemini-2.5-flash-lite', 'name': 'Gemini 2.5 Flash Lite', 'description': 'Gemini 2.5 Flash Lite'},
            {'id': 'gemini-2.5-flash', 'name': 'Gemini 2.5 Flash', 'description': 'Google Gemini 2.5 Flash'},
            {'id': 'gemini-2.0-flash-lite-001', 'name': 'Gemini 2.0 Flash Lite 001',
             'description': 'Gemini 2.0 Flash Lite stable release'},
            {'id': 'gemini-2.0-flash-lite', 'name': 'Gemini 2.0 Flash Lite', 'description': 'Gemini 2.0 Flash Lite'},
            {'id': 'gemini-2.0-flash-exp:free', 'name': 'Gemini 2.0 Flash Exp (Free)',
             'description': 'Experimental Gemini 2.0 Flash - free'},
            {'id': 'gemini-2.0-flash-001', 'name': 'Gemini 2.0 Flash 001',
             'description': 'Gemini 2.0 Flash stable release'},
            {'id': 'gemini-2.0-flash', 'name': 'Gemini 2.0 Flash', 'description': 'Google Gemini 2.0 Flash'},

            # ===== xAI Grok (Direct) =====
            {'id': 'x-ai/grok-vision-beta', 'name': 'Grok Vision Beta', 'description': 'Grok with vision capabilities'},
            {'id': 'x-ai/grok-code-fast-1', 'name': 'Grok Code Fast 1', 'description': 'Grok fast coding model'},
            {'id': 'x-ai/grok-beta', 'name': 'Grok Beta', 'description': 'Grok beta model'},
            {'id': 'x-ai/grok-4.1-fast', 'name': 'Grok 4.1 Fast', 'description': 'Fast Grok 4.1'},
            {'id': 'x-ai/grok-4-fast', 'name': 'Grok 4 Fast', 'description': 'Fast Grok 4'},
            {'id': 'x-ai/grok-4', 'name': 'Grok 4', 'description': 'xAI Grok 4 flagship'},
            {'id': 'x-ai/grok-3-mini-fast', 'name': 'Grok 3 Mini Fast', 'description': 'Fast Grok 3 Mini'},
            {'id': 'x-ai/grok-3-mini-beta', 'name': 'Grok 3 Mini Beta', 'description': 'Grok 3 Mini Beta'},
            {'id': 'x-ai/grok-3-mini', 'name': 'Grok 3 Mini', 'description': 'Compact Grok 3'},
            {'id': 'x-ai/grok-3-fast', 'name': 'Grok 3 Fast', 'description': 'Fast Grok 3'},
            {'id': 'x-ai/grok-3-beta', 'name': 'Grok 3 Beta', 'description': 'Grok 3 Beta'},
            {'id': 'x-ai/grok-3', 'name': 'Grok 3', 'description': 'xAI Grok 3'},
            {'id': 'x-ai/grok-2-vision', 'name': 'Grok 2 Vision', 'description': 'Grok 2 with vision'},
            {'id': 'x-ai/grok-2', 'name': 'Grok 2', 'description': 'xAI Grok 2'},

            # ===== Moonshot Kimi (Direct) =====
            {'id': 'moonshotai/kimi-k2', 'name': 'Kimi K2', 'description': 'Moonshot Kimi K2'},
            {'id': 'moonshotai/kimi-k2-0905', 'name': 'Kimi K2 (09-05)', 'description': 'Kimi K2 September update'},
            {'id': 'moonshotai/kimi-k2-thinking', 'name': 'Kimi K2 Thinking',
             'description': 'Kimi K2 with reasoning mode'},
            {'id': 'moonshotai/kimi-k2.5', 'name': 'Kimi K2.5', 'description': 'Moonshot Kimi K2.5'},

            # ===== TNG Tech (Direct) =====
            {'id': 'tngtech/tng-r1t-chimera', 'name': 'TNG R1T Chimera', 'description': 'TNG R1T Chimera model'},
            {'id': 'tngtech/tng-r1t-chimera:free', 'name': 'TNG R1T Chimera (Free)',
             'description': 'TNG R1T Chimera - free tier'},
            {'id': 'tngtech/deepseek-r1t-chimera', 'name': 'DeepSeek R1T Chimera',
             'description': 'TNG DeepSeek R1T Chimera'},
            {'id': 'tngtech/deepseek-r1t-chimera:free', 'name': 'DeepSeek R1T Chimera (Free)',
             'description': 'TNG DeepSeek R1T Chimera - free'},
            {'id': 'tngtech/deepseek-r1t2-chimera', 'name': 'DeepSeek R1T2 Chimera',
             'description': 'TNG DeepSeek R1T2 Chimera'},
            {'id': 'tngtech/deepseek-r1t2-chimera:free', 'name': 'DeepSeek R1T2 Chimera (Free)',
             'description': 'TNG DeepSeek R1T2 Chimera - free'},

            # ===== OpenRouter - Agentica / AI21 / Aion =====
            {'id': 'openrouter:agentica-org/deepcoder-14b-preview', 'name': 'DeepCoder 14B Preview',
             'description': 'Agentica DeepCoder 14B'},
            {'id': 'openrouter:agentica-org/deepcoder-14b-preview:free', 'name': 'DeepCoder 14B Preview (Free)',
             'description': 'Agentica DeepCoder 14B - free'},
            {'id': 'openrouter:ai21/jamba-large-1.7', 'name': 'Jamba Large 1.7', 'description': 'AI21 Jamba Large 1.7'},
            {'id': 'openrouter:aion-labs/aion-1.0', 'name': 'Aion 1.0', 'description': 'Aion Labs Aion 1.0'},
            {'id': 'openrouter:aion-labs/aion-1.0-mini', 'name': 'Aion 1.0 Mini',
             'description': 'Aion Labs compact model'},
            {'id': 'openrouter:aion-labs/aion-rp-llama-3.1-8b', 'name': 'Aion RP LLaMA 3.1 8B',
             'description': 'Aion roleplay LLaMA 3.1 8B'},

            # ===== OpenRouter - Coding Specialists =====
            {'id': 'openrouter:alfredpros/codellama-7b-instruct-solidity', 'name': 'CodeLLaMA 7B Solidity',
             'description': 'Solidity-tuned CodeLLaMA 7B'},

            # ===== OpenRouter - Alibaba / AllenAI =====
            {'id': 'openrouter:alibaba/tongyi-deepresearch-30b-a3b', 'name': 'Tongyi DeepResearch 30B',
             'description': 'Alibaba Tongyi research model'},
            {'id': 'openrouter:allenai/olmo-3-32b-thinking', 'name': 'OLMo 3 32B Thinking',
             'description': 'AllenAI OLMo 3 32B with thinking'},
            {'id': 'openrouter:allenai/olmo-3-7b-instruct', 'name': 'OLMo 3 7B Instruct',
             'description': 'AllenAI OLMo 3 7B instruction-tuned'},
            {'id': 'openrouter:allenai/olmo-3-7b-think', 'name': 'OLMo 3 7B Think',
             'description': 'AllenAI OLMo 3 7B with reasoning'},
            {'id': 'openrouter:allenai/olmo-3.1-32b-think:free', 'name': 'OLMo 3.1 32B Think (Free)',
             'description': 'AllenAI OLMo 3.1 32B thinking - free'},
            {'id': 'openrouter:alpindale/goliath-120b', 'name': 'Goliath 120B',
             'description': 'Alpindale Goliath 120B'},

            # ===== OpenRouter - Amazon Nova =====
            {'id': 'openrouter:amazon/nova-2-lite-v1', 'name': 'Amazon Nova 2 Lite',
             'description': 'Amazon Nova 2 Lite v1'},
            {'id': 'openrouter:amazon/nova-lite-v1', 'name': 'Amazon Nova Lite', 'description': 'Amazon Nova Lite v1'},
            {'id': 'openrouter:amazon/nova-micro-v1', 'name': 'Amazon Nova Micro',
             'description': 'Amazon Nova Micro v1'},
            {'id': 'openrouter:amazon/nova-premier-v1', 'name': 'Amazon Nova Premier',
             'description': 'Amazon Nova Premier v1'},
            {'id': 'openrouter:amazon/nova-pro-v1', 'name': 'Amazon Nova Pro', 'description': 'Amazon Nova Pro v1'},

            # ===== OpenRouter - Anthracite =====
            {'id': 'openrouter:anthracite-org/magnum-v2-72b', 'name': 'Magnum v2 72B',
             'description': 'Anthracite Magnum v2 72B'},
            {'id': 'openrouter:anthracite-org/magnum-v4-72b', 'name': 'Magnum v4 72B',
             'description': 'Anthracite Magnum v4 72B'},

            # ===== OpenRouter - Anthropic Claude =====
            {'id': 'openrouter:anthropic/claude-3-haiku', 'name': 'Claude 3 Haiku (OR)',
             'description': 'Claude 3 Haiku via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3-opus', 'name': 'Claude 3 Opus (OR)',
             'description': 'Claude 3 Opus via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.5-haiku', 'name': 'Claude 3.5 Haiku (OR)',
             'description': 'Claude 3.5 Haiku via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.5-haiku-20241022', 'name': 'Claude 3.5 Haiku 2024-10-22 (OR)',
             'description': 'Claude 3.5 Haiku Oct 2024 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.5-sonnet', 'name': 'Claude 3.5 Sonnet (OR)',
             'description': 'Claude 3.5 Sonnet via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.5-sonnet-20240620', 'name': 'Claude 3.5 Sonnet 2024-06-20 (OR)',
             'description': 'Claude 3.5 Sonnet Jun 2024 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.7-sonnet', 'name': 'Claude 3.7 Sonnet (OR)',
             'description': 'Claude 3.7 Sonnet via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-3.7-sonnet:thinking', 'name': 'Claude 3.7 Sonnet Thinking (OR)',
             'description': 'Claude 3.7 Sonnet with extended thinking'},
            {'id': 'openrouter:anthropic/claude-haiku-4.5', 'name': 'Claude Haiku 4.5 (OR)',
             'description': 'Claude Haiku 4.5 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-opus-4', 'name': 'Claude Opus 4 (OR)',
             'description': 'Claude Opus 4 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-opus-4.1', 'name': 'Claude Opus 4.1 (OR)',
             'description': 'Claude Opus 4.1 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-opus-4.5', 'name': 'Claude Opus 4.5 (OR)',
             'description': 'Claude Opus 4.5 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-opus-4.6', 'name': 'Claude Opus 4.6 (OR)',
             'description': 'Claude Opus 4.6 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-sonnet-4', 'name': 'Claude Sonnet 4 (OR)',
             'description': 'Claude Sonnet 4 via OpenRouter'},
            {'id': 'openrouter:anthropic/claude-sonnet-4.5', 'name': 'Claude Sonnet 4.5 (OR)',
             'description': 'Claude Sonnet 4.5 via OpenRouter'},

            # ===== OpenRouter - Arcee AI =====
            {'id': 'openrouter:arcee-ai/coder-large', 'name': 'Arcee Coder Large',
             'description': 'Arcee AI large coding model'},
            {'id': 'openrouter:arcee-ai/maestro-reasoning', 'name': 'Arcee Maestro Reasoning',
             'description': 'Arcee AI reasoning model'},
            {'id': 'openrouter:arcee-ai/spotlight', 'name': 'Arcee Spotlight',
             'description': 'Arcee AI Spotlight model'},
            {'id': 'openrouter:arcee-ai/virtuoso-large', 'name': 'Arcee Virtuoso Large',
             'description': 'Arcee AI large general model'},

            # ===== OpenRouter - ArliAI =====
            {'id': 'openrouter:arliai/qwq-32b-arliai-rpr-v1', 'name': 'QwQ 32B ArliAI RPR v1',
             'description': 'ArliAI roleplay QwQ 32B v1'},
            {'id': 'openrouter:arliai/qwq-32b-arliai-rpr-v1:free', 'name': 'QwQ 32B ArliAI RPR v1 (Free)',
             'description': 'ArliAI roleplay QwQ 32B - free'},

            # ===== OpenRouter - Baidu ERNIE =====
            {'id': 'openrouter:baidu/ernie-4.5-21b-a3b', 'name': 'ERNIE 4.5 21B', 'description': 'Baidu ERNIE 4.5 21B'},
            {'id': 'openrouter:baidu/ernie-4.5-21b-a3b-thinking', 'name': 'ERNIE 4.5 21B Thinking',
             'description': 'Baidu ERNIE 4.5 21B with thinking'},
            {'id': 'openrouter:baidu/ernie-4.5-300b-a47b', 'name': 'ERNIE 4.5 300B',
             'description': 'Baidu ERNIE 4.5 300B flagship'},
            {'id': 'openrouter:baidu/ernie-4.5-vl-28b-a3b', 'name': 'ERNIE 4.5 VL 28B',
             'description': 'Baidu ERNIE 4.5 vision-language 28B'},
            {'id': 'openrouter:baidu/ernie-4.5-vl-424b-a47b', 'name': 'ERNIE 4.5 VL 424B',
             'description': 'Baidu ERNIE 4.5 vision-language 424B'},

            # ===== OpenRouter - ByteDance Seed =====
            {'id': 'openrouter:bytedance-seed/seed-1.6', 'name': 'Seed 1.6', 'description': 'ByteDance Seed 1.6'},
            {'id': 'openrouter:bytedance-seed/seed-1.6-flash', 'name': 'Seed 1.6 Flash',
             'description': 'ByteDance Seed 1.6 Flash'},
            {'id': 'openrouter:bytedance-seed/seedream-4.5', 'name': 'Seedream 4.5',
             'description': 'ByteDance Seedream 4.5'},
            {'id': 'openrouter:bytedance/seed-oss-36b-instruct', 'name': 'Seed OSS 36B Instruct',
             'description': 'ByteDance open-source 36B'},
            {'id': 'openrouter:bytedance/ui-tars-1.5-7b', 'name': 'UI-TARS 1.5 7B',
             'description': 'ByteDance UI agent model'},

            # ===== OpenRouter - Cognitive Computations =====
            {'id': 'openrouter:cognitivecomputations/dolphin-mistral-24b-venice-edition:free',
             'name': 'Dolphin Mistral 24B Venice (Free)', 'description': 'Dolphin Mistral 24B Venice - free'},
            {'id': 'openrouter:cognitivecomputations/dolphin-mixtral-8x22b', 'name': 'Dolphin Mixtral 8x22B',
             'description': 'Dolphin Mixtral 8x22B'},
            {'id': 'openrouter:cognitivecomputations/dolphin3.0-mistral-24b', 'name': 'Dolphin 3.0 Mistral 24B',
             'description': 'Dolphin 3.0 on Mistral 24B'},
            {'id': 'openrouter:cognitivecomputations/dolphin3.0-mistral-24b:free',
             'name': 'Dolphin 3.0 Mistral 24B (Free)', 'description': 'Dolphin 3.0 Mistral 24B - free'},
            {'id': 'openrouter:cognitivecomputations/dolphin3.0-r1-mistral-24b', 'name': 'Dolphin 3.0 R1 Mistral 24B',
             'description': 'Dolphin 3.0 R1 reasoning on Mistral 24B'},
            {'id': 'openrouter:cognitivecomputations/dolphin3.0-r1-mistral-24b:free',
             'name': 'Dolphin 3.0 R1 Mistral 24B (Free)', 'description': 'Dolphin 3.0 R1 Mistral 24B - free'},

            # ===== OpenRouter - Cohere =====
            {'id': 'openrouter:cohere/command', 'name': 'Command', 'description': 'Cohere Command'},
            {'id': 'openrouter:cohere/command-a', 'name': 'Command A', 'description': 'Cohere Command A'},
            {'id': 'openrouter:cohere/command-r', 'name': 'Command R', 'description': 'Cohere Command R'},
            {'id': 'openrouter:cohere/command-r-03-2024', 'name': 'Command R (Mar 2024)',
             'description': 'Cohere Command R March 2024'},
            {'id': 'openrouter:cohere/command-r-08-2024', 'name': 'Command R (Aug 2024)',
             'description': 'Cohere Command R August 2024'},
            {'id': 'openrouter:cohere/command-r-plus', 'name': 'Command R+', 'description': 'Cohere Command R Plus'},
            {'id': 'openrouter:cohere/command-r-plus-04-2024', 'name': 'Command R+ (Apr 2024)',
             'description': 'Cohere Command R+ April 2024'},
            {'id': 'openrouter:cohere/command-r-plus-08-2024', 'name': 'Command R+ (Aug 2024)',
             'description': 'Cohere Command R+ August 2024'},
            {'id': 'openrouter:cohere/command-r7b-12-2024', 'name': 'Command R7B (Dec 2024)',
             'description': 'Cohere Command R7B December 2024'},

            # ===== OpenRouter - DeepCogito / DeepSeek =====
            {'id': 'openrouter:deepcogito/cogito-v2-preview-deepseek-671b', 'name': 'Cogito v2 DeepSeek 671B',
             'description': 'DeepCogito Cogito v2 on DeepSeek 671B'},
            {'id': 'openrouter:deepseek/deepseek-chat', 'name': 'DeepSeek Chat (OR)',
             'description': 'DeepSeek Chat via OpenRouter'},
            {'id': 'openrouter:deepseek/deepseek-chat-v3-0324', 'name': 'DeepSeek Chat v3 (03-24) (OR)',
             'description': 'DeepSeek Chat v3 March 2024 via OR'},
            {'id': 'openrouter:deepseek/deepseek-chat-v3-0324:free', 'name': 'DeepSeek Chat v3 (03-24) Free',
             'description': 'DeepSeek Chat v3 March 2024 - free'},
            {'id': 'openrouter:deepseek/deepseek-chat-v3.1', 'name': 'DeepSeek Chat v3.1 (OR)',
             'description': 'DeepSeek Chat v3.1 via OR'},
            {'id': 'openrouter:deepseek/deepseek-chat-v3.1:free', 'name': 'DeepSeek Chat v3.1 (Free)',
             'description': 'DeepSeek Chat v3.1 - free'},
            {'id': 'openrouter:deepseek/deepseek-prover-v2', 'name': 'DeepSeek Prover v2',
             'description': 'DeepSeek math/proof reasoning model'},
            {'id': 'openrouter:deepseek/deepseek-r1', 'name': 'DeepSeek R1 (OR)',
             'description': 'DeepSeek R1 via OpenRouter'},
            {'id': 'openrouter:deepseek/deepseek-r1-0528', 'name': 'DeepSeek R1 (05-28) (OR)',
             'description': 'DeepSeek R1 May 2028 via OR'},
            {'id': 'openrouter:deepseek/deepseek-r1-0528-qwen3-8b', 'name': 'DeepSeek R1 0528 Qwen3 8B',
             'description': 'R1 0528 distilled into Qwen3 8B'},
            {'id': 'openrouter:deepseek/deepseek-r1-0528-qwen3-8b:free', 'name': 'DeepSeek R1 0528 Qwen3 8B (Free)',
             'description': 'R1 0528 Qwen3 8B - free'},
            {'id': 'openrouter:deepseek/deepseek-r1-0528:free', 'name': 'DeepSeek R1 (05-28) Free',
             'description': 'DeepSeek R1 May 2028 - free'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-llama-70b', 'name': 'DeepSeek R1 Distill LLaMA 70B (OR)',
             'description': 'R1 distilled LLaMA 70B via OR'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-llama-70b:free',
             'name': 'DeepSeek R1 Distill LLaMA 70B (Free)', 'description': 'R1 distilled LLaMA 70B - free'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-llama-8b', 'name': 'DeepSeek R1 Distill LLaMA 8B',
             'description': 'R1 distilled into LLaMA 8B'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-qwen-14b', 'name': 'DeepSeek R1 Distill Qwen 14B',
             'description': 'R1 distilled into Qwen 14B'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-qwen-14b:free',
             'name': 'DeepSeek R1 Distill Qwen 14B (Free)', 'description': 'R1 distilled Qwen 14B - free'},
            {'id': 'openrouter:deepseek/deepseek-r1-distill-qwen-32b', 'name': 'DeepSeek R1 Distill Qwen 32B (OR)',
             'description': 'R1 distilled Qwen 32B via OR'},
            {'id': 'openrouter:deepseek/deepseek-r1:free', 'name': 'DeepSeek R1 (Free)',
             'description': 'DeepSeek R1 - free tier'},
            {'id': 'openrouter:deepseek/deepseek-v3.1-base', 'name': 'DeepSeek V3.1 Base',
             'description': 'DeepSeek V3.1 base model'},
            {'id': 'openrouter:deepseek/deepseek-v3.1-terminus', 'name': 'DeepSeek V3.1 Terminus (OR)',
             'description': 'DeepSeek V3.1 Terminus via OR'},
            {'id': 'openrouter:deepseek/deepseek-v3.2', 'name': 'DeepSeek V3.2 (OR)',
             'description': 'DeepSeek V3.2 via OR'},
            {'id': 'openrouter:deepseek/deepseek-v3.2-exp', 'name': 'DeepSeek V3.2 Experimental (OR)',
             'description': 'Experimental DeepSeek V3.2 via OR'},
            {'id': 'openrouter:deepseek/deepseek-v3.2-speciale', 'name': 'DeepSeek V3.2 Speciale (OR)',
             'description': 'Special edition DeepSeek V3.2 via OR'},

            # ===== OpenRouter - EleutherAI =====
            {'id': 'openrouter:eleutherai/llemma_7b', 'name': 'LLemma 7B', 'description': 'EleutherAI math LLM 7B'},

            # ===== OpenRouter - Google Gemini =====
            {'id': 'openrouter:google/gemini-2.0-flash-001', 'name': 'Gemini 2.0 Flash 001 (OR)',
             'description': 'Gemini 2.0 Flash stable via OR'},
            {'id': 'openrouter:google/gemini-2.0-flash-exp:free', 'name': 'Gemini 2.0 Flash Exp (Free)',
             'description': 'Experimental Gemini 2.0 Flash - free'},
            {'id': 'openrouter:google/gemini-2.0-flash-lite-001', 'name': 'Gemini 2.0 Flash Lite 001 (OR)',
             'description': 'Gemini 2.0 Flash Lite stable via OR'},
            {'id': 'openrouter:google/gemini-2.5-flash', 'name': 'Gemini 2.5 Flash (OR)',
             'description': 'Gemini 2.5 Flash via OR'},
            {'id': 'openrouter:google/gemini-2.5-flash-image', 'name': 'Gemini 2.5 Flash Image',
             'description': 'Gemini 2.5 Flash with image generation'},
            {'id': 'openrouter:google/gemini-2.5-flash-image-preview', 'name': 'Gemini 2.5 Flash Image Preview',
             'description': 'Gemini 2.5 Flash image preview'},
            {'id': 'openrouter:google/gemini-2.5-flash-image-preview:free',
             'name': 'Gemini 2.5 Flash Image Preview (Free)', 'description': 'Gemini 2.5 Flash image preview - free'},
            {'id': 'openrouter:google/gemini-2.5-flash-lite', 'name': 'Gemini 2.5 Flash Lite (OR)',
             'description': 'Gemini 2.5 Flash Lite via OR'},
            {'id': 'openrouter:google/gemini-2.5-flash-lite-preview-06-17',
             'name': 'Gemini 2.5 Flash Lite Preview (Jun 17)', 'description': 'Gemini 2.5 Flash Lite Preview Jun 2025'},
            {'id': 'openrouter:google/gemini-2.5-flash-lite-preview-09-2025',
             'name': 'Gemini 2.5 Flash Lite Preview (Sep 2025)',
             'description': 'Gemini 2.5 Flash Lite Sep 2025 Preview via OR'},
            {'id': 'openrouter:google/gemini-2.5-flash-preview-09-2025',
             'name': 'Gemini 2.5 Flash Preview (Sep 2025) (OR)',
             'description': 'Gemini 2.5 Flash Sep 2025 Preview via OR'},
            {'id': 'openrouter:google/gemini-2.5-pro', 'name': 'Gemini 2.5 Pro (OR)',
             'description': 'Gemini 2.5 Pro via OR'},
            {'id': 'openrouter:google/gemini-2.5-pro-exp-03-25', 'name': 'Gemini 2.5 Pro Exp (03-25)',
             'description': 'Gemini 2.5 Pro Experimental Mar 2025'},
            {'id': 'openrouter:google/gemini-2.5-pro-preview', 'name': 'Gemini 2.5 Pro Preview (OR)',
             'description': 'Gemini 2.5 Pro Preview via OR'},
            {'id': 'openrouter:google/gemini-2.5-pro-preview-05-06', 'name': 'Gemini 2.5 Pro Preview (05-06) (OR)',
             'description': 'Gemini 2.5 Pro Preview May 2025 via OR'},
            {'id': 'openrouter:google/gemini-3-flash-preview', 'name': 'Gemini 3 Flash Preview (OR)',
             'description': 'Gemini 3 Flash Preview via OR'},
            {'id': 'openrouter:google/gemini-embedding-001', 'name': 'Gemini Embedding 001',
             'description': 'Google Gemini embedding model'},
            {'id': 'openrouter:google/gemini-flash-1.5', 'name': 'Gemini Flash 1.5',
             'description': 'Google Gemini Flash 1.5'},
            {'id': 'openrouter:google/gemini-flash-1.5-8b', 'name': 'Gemini Flash 1.5 8B',
             'description': 'Gemini Flash 1.5 8B'},
            {'id': 'openrouter:google/gemini-pro-1.5', 'name': 'Gemini Pro 1.5',
             'description': 'Google Gemini Pro 1.5'},

            # ===== OpenRouter - Google Gemma =====
            {'id': 'openrouter:google/gemma-2-27b-it', 'name': 'Gemma 2 27B Instruct',
             'description': 'Google Gemma 2 27B instruction-tuned'},
            {'id': 'openrouter:google/gemma-2-9b-it', 'name': 'Gemma 2 9B Instruct',
             'description': 'Google Gemma 2 9B instruction-tuned'},
            {'id': 'openrouter:google/gemma-2-9b-it:free', 'name': 'Gemma 2 9B Instruct (Free)',
             'description': 'Gemma 2 9B instruction-tuned - free'},
            {'id': 'openrouter:google/gemma-3-12b-it', 'name': 'Gemma 3 12B Instruct',
             'description': 'Google Gemma 3 12B'},
            {'id': 'openrouter:google/gemma-3-12b-it:free', 'name': 'Gemma 3 12B Instruct (Free)',
             'description': 'Gemma 3 12B - free'},
            {'id': 'openrouter:google/gemma-3-27b-it', 'name': 'Gemma 3 27B Instruct',
             'description': 'Google Gemma 3 27B'},
            {'id': 'openrouter:google/gemma-3-27b-it:free', 'name': 'Gemma 3 27B Instruct (Free)',
             'description': 'Gemma 3 27B - free'},
            {'id': 'openrouter:google/gemma-3-4b-it', 'name': 'Gemma 3 4B Instruct',
             'description': 'Google Gemma 3 4B'},
            {'id': 'openrouter:google/gemma-3-4b-it:free', 'name': 'Gemma 3 4B Instruct (Free)',
             'description': 'Gemma 3 4B - free'},
            {'id': 'openrouter:google/gemma-3n-e2b-it:free', 'name': 'Gemma 3n E2B Instruct (Free)',
             'description': 'Gemma 3n E2B - free'},
            {'id': 'openrouter:google/gemma-3n-e4b-it', 'name': 'Gemma 3n E4B Instruct',
             'description': 'Google Gemma 3n E4B'},
            {'id': 'openrouter:google/gemma-3n-e4b-it:free', 'name': 'Gemma 3n E4B Instruct (Free)',
             'description': 'Gemma 3n E4B - free'},

            # ===== OpenRouter - Gryphe / IBM =====
            {'id': 'openrouter:gryphe/mythomax-l2-13b', 'name': 'MythoMax L2 13B',
             'description': 'Gryphe MythoMax LLaMA 2 13B'},
            {'id': 'openrouter:ibm-granite/granite-4.0-h-micro', 'name': 'Granite 4.0 H Micro',
             'description': 'IBM Granite 4.0 H Micro'},

            # ===== OpenRouter - Inception Mercury =====
            {'id': 'openrouter:inception/mercury', 'name': 'Mercury', 'description': 'Inception Mercury model'},
            {'id': 'openrouter:inception/mercury-coder', 'name': 'Mercury Coder',
             'description': 'Inception Mercury coding model'},

            # ===== OpenRouter - InclusionAI / Infermatic / Inflection =====
            {'id': 'openrouter:inclusionai/ling-1t', 'name': 'Ling 1T', 'description': 'InclusionAI Ling 1 Trillion'},
            {'id': 'openrouter:inclusionai/ring-1t', 'name': 'Ring 1T', 'description': 'InclusionAI Ring 1 Trillion'},
            {'id': 'openrouter:infermatic/mn-inferor-12b', 'name': 'MN Inferor 12B',
             'description': 'Infermatic MN Inferor 12B'},
            {'id': 'openrouter:inflection/inflection-3-pi', 'name': 'Inflection 3 Pi',
             'description': 'Inflection Pi model'},
            {'id': 'openrouter:inflection/inflection-3-productivity', 'name': 'Inflection 3 Productivity',
             'description': 'Inflection productivity model'},

            # ===== OpenRouter - Embedding Models =====
            {'id': 'openrouter:intfloat/e5-base-v2', 'name': 'E5 Base v2',
             'description': 'intfloat E5 base embedding model'},
            {'id': 'openrouter:intfloat/e5-large-v2', 'name': 'E5 Large v2',
             'description': 'intfloat E5 large embedding model'},
            {'id': 'openrouter:intfloat/multilingual-e5-large', 'name': 'Multilingual E5 Large',
             'description': 'Multilingual E5 large embedding'},

            # ===== OpenRouter - KwaiPilot / Liquid =====
            {'id': 'openrouter:kwaipilot/kat-coder-pro:free', 'name': 'KAT Coder Pro (Free)',
             'description': 'KwaiPilot KAT Coder Pro - free'},
            {'id': 'openrouter:liquid/lfm-2.2-6b', 'name': 'LFM 2.2 6B', 'description': 'Liquid LFM 2.2 6B'},
            {'id': 'openrouter:liquid/lfm-3b', 'name': 'LFM 3B', 'description': 'Liquid LFM 3B'},
            {'id': 'openrouter:liquid/lfm-7b', 'name': 'LFM 7B', 'description': 'Liquid LFM 7B'},
            {'id': 'openrouter:liquid/lfm2-8b-a1b', 'name': 'LFM2 8B A1B', 'description': 'Liquid LFM2 8B A1B'},

            # ===== OpenRouter - Mancer / Meituan =====
            {'id': 'openrouter:mancer/weaver', 'name': 'Weaver', 'description': 'Mancer Weaver creative model'},
            {'id': 'openrouter:meituan/longcat-flash-chat', 'name': 'LongCat Flash Chat',
             'description': 'Meituan LongCat Flash Chat'},

            # ===== OpenRouter - Meta LLaMA 3 =====
            {'id': 'openrouter:meta-llama/llama-3-70b-instruct', 'name': 'LLaMA 3 70B Instruct',
             'description': 'Meta LLaMA 3 70B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3-8b-instruct', 'name': 'LLaMA 3 8B Instruct',
             'description': 'Meta LLaMA 3 8B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.1-405b', 'name': 'LLaMA 3.1 405B',
             'description': 'Meta LLaMA 3.1 405B'},
            {'id': 'openrouter:meta-llama/llama-3.1-405b-instruct', 'name': 'LLaMA 3.1 405B Instruct',
             'description': 'Meta LLaMA 3.1 405B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.1-405b-instruct:free', 'name': 'LLaMA 3.1 405B Instruct (Free)',
             'description': 'LLaMA 3.1 405B Instruct - free'},
            {'id': 'openrouter:meta-llama/llama-3.1-70b-instruct', 'name': 'LLaMA 3.1 70B Instruct',
             'description': 'Meta LLaMA 3.1 70B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.1-8b-instruct', 'name': 'LLaMA 3.1 8B Instruct',
             'description': 'Meta LLaMA 3.1 8B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.2-11b-vision-instruct', 'name': 'LLaMA 3.2 11B Vision',
             'description': 'Meta LLaMA 3.2 11B vision model'},
            {'id': 'openrouter:meta-llama/llama-3.2-1b-instruct', 'name': 'LLaMA 3.2 1B Instruct',
             'description': 'Meta LLaMA 3.2 1B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.2-3b-instruct', 'name': 'LLaMA 3.2 3B Instruct',
             'description': 'Meta LLaMA 3.2 3B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.2-3b-instruct:free', 'name': 'LLaMA 3.2 3B Instruct (Free)',
             'description': 'LLaMA 3.2 3B Instruct - free'},
            {'id': 'openrouter:meta-llama/llama-3.2-90b-vision-instruct', 'name': 'LLaMA 3.2 90B Vision',
             'description': 'Meta LLaMA 3.2 90B vision model'},
            {'id': 'openrouter:meta-llama/llama-3.3-70b-instruct', 'name': 'LLaMA 3.3 70B Instruct',
             'description': 'Meta LLaMA 3.3 70B instruction-tuned'},
            {'id': 'openrouter:meta-llama/llama-3.3-70b-instruct:free', 'name': 'LLaMA 3.3 70B Instruct (Free)',
             'description': 'LLaMA 3.3 70B Instruct - free'},
            {'id': 'openrouter:meta-llama/llama-3.3-8b-instruct:free', 'name': 'LLaMA 3.3 8B Instruct (Free)',
             'description': 'LLaMA 3.3 8B Instruct - free'},

            # ===== OpenRouter - Meta LLaMA 4 =====
            {'id': 'openrouter:meta-llama/llama-4-maverick', 'name': 'LLaMA 4 Maverick',
             'description': 'Meta LLaMA 4 Maverick'},
            {'id': 'openrouter:meta-llama/llama-4-maverick:free', 'name': 'LLaMA 4 Maverick (Free)',
             'description': 'LLaMA 4 Maverick - free'},
            {'id': 'openrouter:meta-llama/llama-4-scout', 'name': 'LLaMA 4 Scout', 'description': 'Meta LLaMA 4 Scout'},
            {'id': 'openrouter:meta-llama/llama-4-scout:free', 'name': 'LLaMA 4 Scout (Free)',
             'description': 'LLaMA 4 Scout - free'},
            {'id': 'openrouter:meta-llama/llama-guard-2-8b', 'name': 'LLaMA Guard 2 8B',
             'description': 'Meta content safety model v2'},
            {'id': 'openrouter:meta-llama/llama-guard-3-8b', 'name': 'LLaMA Guard 3 8B',
             'description': 'Meta content safety model v3'},
            {'id': 'openrouter:meta-llama/llama-guard-4-12b', 'name': 'LLaMA Guard 4 12B',
             'description': 'Meta content safety model v4'},

            # ===== OpenRouter - Microsoft =====
            {'id': 'openrouter:microsoft/mai-ds-r1', 'name': 'MAI-DS R1',
             'description': 'Microsoft MAI Data Science R1'},
            {'id': 'openrouter:microsoft/mai-ds-r1:free', 'name': 'MAI-DS R1 (Free)',
             'description': 'Microsoft MAI-DS R1 - free'},
            {'id': 'openrouter:microsoft/phi-3-medium-128k-instruct', 'name': 'Phi-3 Medium 128K',
             'description': 'Microsoft Phi-3 Medium 128K context'},
            {'id': 'openrouter:microsoft/phi-3-mini-128k-instruct', 'name': 'Phi-3 Mini 128K',
             'description': 'Microsoft Phi-3 Mini 128K context'},
            {'id': 'openrouter:microsoft/phi-3.5-mini-128k-instruct', 'name': 'Phi-3.5 Mini 128K',
             'description': 'Microsoft Phi-3.5 Mini 128K context'},
            {'id': 'openrouter:microsoft/phi-4', 'name': 'Phi-4', 'description': 'Microsoft Phi-4'},
            {'id': 'openrouter:microsoft/phi-4-multimodal-instruct', 'name': 'Phi-4 Multimodal',
             'description': 'Microsoft Phi-4 multimodal instruction'},
            {'id': 'openrouter:microsoft/phi-4-reasoning-plus', 'name': 'Phi-4 Reasoning+',
             'description': 'Microsoft Phi-4 enhanced reasoning'},
            {'id': 'openrouter:microsoft/wizardlm-2-8x22b', 'name': 'WizardLM 2 8x22B',
             'description': 'Microsoft WizardLM 2 MoE 8x22B'},

            # ===== OpenRouter - MiniMax =====
            {'id': 'openrouter:minimax/minimax-01', 'name': 'MiniMax 01', 'description': 'MiniMax 01'},
            {'id': 'openrouter:minimax/minimax-m1', 'name': 'MiniMax M1', 'description': 'MiniMax M1'},
            {'id': 'openrouter:minimax/minimax-m2', 'name': 'MiniMax M2', 'description': 'MiniMax M2'},
            {'id': 'openrouter:minimax/minimax-m2.1', 'name': 'MiniMax M2.1', 'description': 'MiniMax M2.1'},
            {'id': 'openrouter:minimax/minimax-m2:free', 'name': 'MiniMax M2 (Free)',
             'description': 'MiniMax M2 - free'},

            # ===== OpenRouter - Mistral =====
            {'id': 'openrouter:mistralai/codestral-2501', 'name': 'Codestral 2501',
             'description': 'Mistral Codestral Jan 2025'},
            {'id': 'openrouter:mistralai/codestral-2508', 'name': 'Codestral 2508',
             'description': 'Mistral Codestral Aug 2025'},
            {'id': 'openrouter:mistralai/codestral-embed-2505', 'name': 'Codestral Embed 2505',
             'description': 'Mistral Codestral embedding May 2025'},
            {'id': 'openrouter:mistralai/devstral-2512', 'name': 'Devstral 2512',
             'description': 'Mistral Devstral Dec 2025'},
            {'id': 'openrouter:mistralai/devstral-2512:free', 'name': 'Devstral 2512 (Free)',
             'description': 'Devstral Dec 2025 - free'},
            {'id': 'openrouter:mistralai/devstral-medium', 'name': 'Devstral Medium',
             'description': 'Mistral Devstral Medium'},
            {'id': 'openrouter:mistralai/devstral-small', 'name': 'Devstral Small',
             'description': 'Mistral Devstral Small'},
            {'id': 'openrouter:mistralai/devstral-small-2505', 'name': 'Devstral Small 2505',
             'description': 'Mistral Devstral Small May 2025'},
            {'id': 'openrouter:mistralai/devstral-small-2505:free', 'name': 'Devstral Small 2505 (Free)',
             'description': 'Devstral Small May 2025 - free'},
            {'id': 'openrouter:mistralai/magistral-medium-2506', 'name': 'Magistral Medium 2506',
             'description': 'Mistral Magistral Medium Jun 2025'},
            {'id': 'openrouter:mistralai/magistral-medium-2506:thinking', 'name': 'Magistral Medium 2506 Thinking',
             'description': 'Magistral Medium with thinking mode'},
            {'id': 'openrouter:mistralai/magistral-small-2506', 'name': 'Magistral Small 2506',
             'description': 'Mistral Magistral Small Jun 2025'},
            {'id': 'openrouter:mistralai/ministral-14b-2512', 'name': 'Ministral 14B 2512',
             'description': 'Mistral Ministral 14B Dec 2025'},
            {'id': 'openrouter:mistralai/ministral-3b', 'name': 'Ministral 3B', 'description': 'Mistral Ministral 3B'},
            {'id': 'openrouter:mistralai/ministral-3b-2512', 'name': 'Ministral 3B 2512',
             'description': 'Mistral Ministral 3B Dec 2025'},
            {'id': 'openrouter:mistralai/ministral-8b', 'name': 'Ministral 8B', 'description': 'Mistral Ministral 8B'},
            {'id': 'openrouter:mistralai/ministral-8b-2512', 'name': 'Ministral 8B 2512',
             'description': 'Mistral Ministral 8B Dec 2025'},
            {'id': 'openrouter:mistralai/mistral-7b-instruct', 'name': 'Mistral 7B Instruct',
             'description': 'Mistral 7B instruction-tuned'},
            {'id': 'openrouter:mistralai/mistral-7b-instruct-v0.1', 'name': 'Mistral 7B Instruct v0.1',
             'description': 'Mistral 7B Instruct v0.1'},
            {'id': 'openrouter:mistralai/mistral-7b-instruct-v0.3', 'name': 'Mistral 7B Instruct v0.3',
             'description': 'Mistral 7B Instruct v0.3'},
            {'id': 'openrouter:mistralai/mistral-7b-instruct:free', 'name': 'Mistral 7B Instruct (Free)',
             'description': 'Mistral 7B - free'},
            {'id': 'openrouter:mistralai/mistral-embed-2312', 'name': 'Mistral Embed 2312',
             'description': 'Mistral embedding model Dec 2023'},
            {'id': 'openrouter:mistralai/mistral-large', 'name': 'Mistral Large', 'description': 'Mistral Large'},
            {'id': 'openrouter:mistralai/mistral-large-2407', 'name': 'Mistral Large (Jul 2024)',
             'description': 'Mistral Large July 2024'},
            {'id': 'openrouter:mistralai/mistral-large-2411', 'name': 'Mistral Large (Nov 2024)',
             'description': 'Mistral Large November 2024'},
            {'id': 'openrouter:mistralai/mistral-large-2512', 'name': 'Mistral Large (Dec 2025)',
             'description': 'Mistral Large December 2025'},
            {'id': 'openrouter:mistralai/mistral-medium-3', 'name': 'Mistral Medium 3',
             'description': 'Mistral Medium 3'},
            {'id': 'openrouter:mistralai/mistral-medium-3.1', 'name': 'Mistral Medium 3.1',
             'description': 'Mistral Medium 3.1'},
            {'id': 'openrouter:mistralai/mistral-nemo', 'name': 'Mistral Nemo', 'description': 'Mistral Nemo'},
            {'id': 'openrouter:mistralai/mistral-nemo:free', 'name': 'Mistral Nemo (Free)',
             'description': 'Mistral Nemo - free'},
            {'id': 'openrouter:mistralai/mistral-saba', 'name': 'Mistral Saba',
             'description': 'Mistral Saba Middle East/South Asia optimized'},
            {'id': 'openrouter:mistralai/mistral-small', 'name': 'Mistral Small', 'description': 'Mistral Small'},
            {'id': 'openrouter:mistralai/mistral-small-24b-instruct-2501', 'name': 'Mistral Small 24B (Jan 2025)',
             'description': 'Mistral Small 24B Jan 2025'},
            {'id': 'openrouter:mistralai/mistral-small-24b-instruct-2501:free',
             'name': 'Mistral Small 24B (Jan 2025) Free', 'description': 'Mistral Small 24B Jan 2025 - free'},
            {'id': 'openrouter:mistralai/mistral-small-3.1-24b-instruct', 'name': 'Mistral Small 3.1 24B',
             'description': 'Mistral Small 3.1 24B'},
            {'id': 'openrouter:mistralai/mistral-small-3.1-24b-instruct:free', 'name': 'Mistral Small 3.1 24B (Free)',
             'description': 'Mistral Small 3.1 24B - free'},
            {'id': 'openrouter:mistralai/mistral-small-3.2-24b-instruct', 'name': 'Mistral Small 3.2 24B',
             'description': 'Mistral Small 3.2 24B'},
            {'id': 'openrouter:mistralai/mistral-small-3.2-24b-instruct:free', 'name': 'Mistral Small 3.2 24B (Free)',
             'description': 'Mistral Small 3.2 24B - free'},
            {'id': 'openrouter:mistralai/mistral-small-creative', 'name': 'Mistral Small Creative',
             'description': 'Mistral Small tuned for creative tasks'},
            {'id': 'openrouter:mistralai/mixtral-8x22b-instruct', 'name': 'Mixtral 8x22B Instruct',
             'description': 'Mistral Mixtral MoE 8x22B'},
            {'id': 'openrouter:mistralai/mixtral-8x7b-instruct', 'name': 'Mixtral 8x7B Instruct',
             'description': 'Mistral Mixtral MoE 8x7B'},
            {'id': 'openrouter:mistralai/pixtral-12b', 'name': 'Pixtral 12B',
             'description': 'Mistral Pixtral multimodal 12B'},
            {'id': 'openrouter:mistralai/pixtral-large-2411', 'name': 'Pixtral Large (Nov 2024)',
             'description': 'Mistral Pixtral Large November 2024'},

            # ===== OpenRouter - Moonshot Kimi =====
            {'id': 'openrouter:moonshotai/kimi-k2', 'name': 'Kimi K2 (OR)', 'description': 'Kimi K2 via OpenRouter'},
            {'id': 'openrouter:moonshotai/kimi-k2-0905', 'name': 'Kimi K2 (09-05) (OR)',
             'description': 'Kimi K2 Sep update via OR'},
            {'id': 'openrouter:moonshotai/kimi-k2-thinking', 'name': 'Kimi K2 Thinking (OR)',
             'description': 'Kimi K2 with reasoning via OR'},
            {'id': 'openrouter:moonshotai/kimi-k2:free', 'name': 'Kimi K2 (Free)',
             'description': 'Kimi K2 - free tier'},
            {'id': 'openrouter:moonshotai/kimi-linear-48b-a3b-instruct', 'name': 'Kimi Linear 48B Instruct',
             'description': 'Moonshot Kimi Linear 48B'},
            {'id': 'openrouter:moonshotai/kimi-vl-a3b-thinking', 'name': 'Kimi VL A3B Thinking',
             'description': 'Kimi vision-language thinking model'},
            {'id': 'openrouter:moonshotai/kimi-vl-a3b-thinking:free', 'name': 'Kimi VL A3B Thinking (Free)',
             'description': 'Kimi VL thinking - free'},

            # ===== OpenRouter - Morph =====
            {'id': 'openrouter:morph/morph-v3-fast', 'name': 'Morph v3 Fast', 'description': 'Morph v3 fast model'},
            {'id': 'openrouter:morph/morph-v3-large', 'name': 'Morph v3 Large', 'description': 'Morph v3 large model'},

            # ===== OpenRouter - NeverSleep =====
            {'id': 'openrouter:neversleep/llama-3-lumimaid-70b', 'name': 'Lumimaid LLaMA 3 70B',
             'description': 'NeverSleep Lumimaid LLaMA 3 70B'},
            {'id': 'openrouter:neversleep/llama-3.1-lumimaid-8b', 'name': 'Lumimaid LLaMA 3.1 8B',
             'description': 'NeverSleep Lumimaid LLaMA 3.1 8B'},
            {'id': 'openrouter:neversleep/noromaid-20b', 'name': 'Noromaid 20B',
             'description': 'NeverSleep Noromaid 20B'},

            # ===== OpenRouter - Nex-AGI / NousResearch =====
            {'id': 'openrouter:nex-agi/deepseek-v3.1-nex-n1:free', 'name': 'DeepSeek V3.1 Nex N1 (Free)',
             'description': 'Nex-AGI DeepSeek V3.1 - free'},
            {'id': 'openrouter:nousresearch/deephermes-3-llama-3-8b-preview:free',
             'name': 'DeepHermes 3 LLaMA 3 8B (Free)', 'description': 'NousResearch DeepHermes 3 8B - free'},
            {'id': 'openrouter:nousresearch/deephermes-3-mistral-24b-preview', 'name': 'DeepHermes 3 Mistral 24B',
             'description': 'NousResearch DeepHermes 3 on Mistral 24B'},
            {'id': 'openrouter:nousresearch/hermes-2-pro-llama-3-8b', 'name': 'Hermes 2 Pro LLaMA 3 8B',
             'description': 'NousResearch Hermes 2 Pro on LLaMA 3 8B'},
            {'id': 'openrouter:nousresearch/hermes-3-llama-3.1-405b', 'name': 'Hermes 3 LLaMA 3.1 405B',
             'description': 'NousResearch Hermes 3 405B'},
            {'id': 'openrouter:nousresearch/hermes-3-llama-3.1-70b', 'name': 'Hermes 3 LLaMA 3.1 70B',
             'description': 'NousResearch Hermes 3 70B'},
            {'id': 'openrouter:nousresearch/hermes-4-405b', 'name': 'Hermes 4 405B',
             'description': 'NousResearch Hermes 4 405B'},
            {'id': 'openrouter:nousresearch/hermes-4-70b', 'name': 'Hermes 4 70B',
             'description': 'NousResearch Hermes 4 70B'},
            {'id': 'openrouter:nousresearch/nous-hermes-2-mixtral-8x7b-dpo', 'name': 'Nous Hermes 2 Mixtral 8x7B DPO',
             'description': 'NousResearch Hermes 2 on Mixtral 8x7B'},

            # ===== OpenRouter - NVIDIA =====
            {'id': 'openrouter:nvidia/llama-3.1-nemotron-70b-instruct', 'name': 'Nemotron 70B Instruct',
             'description': 'NVIDIA Nemotron LLaMA 3.1 70B'},
            {'id': 'openrouter:nvidia/llama-3.1-nemotron-ultra-253b-v1', 'name': 'Nemotron Ultra 253B',
             'description': 'NVIDIA Nemotron Ultra 253B'},
            {'id': 'openrouter:nvidia/llama-3.1-nemotron-ultra-253b-v1:free', 'name': 'Nemotron Ultra 253B (Free)',
             'description': 'NVIDIA Nemotron Ultra 253B - free'},
            {'id': 'openrouter:nvidia/llama-3.3-nemotron-super-49b-v1', 'name': 'Nemotron Super 49B v1',
             'description': 'NVIDIA Nemotron Super 49B v1'},
            {'id': 'openrouter:nvidia/llama-3.3-nemotron-super-49b-v1.5', 'name': 'Nemotron Super 49B v1.5',
             'description': 'NVIDIA Nemotron Super 49B v1.5'},
            {'id': 'openrouter:nvidia/nemotron-3-nano-30b-a3b', 'name': 'Nemotron 3 Nano 30B',
             'description': 'NVIDIA Nemotron 3 Nano 30B'},
            {'id': 'openrouter:nvidia/nemotron-3-nano-30b-a3b:free', 'name': 'Nemotron 3 Nano 30B (Free)',
             'description': 'NVIDIA Nemotron 3 Nano 30B - free'},
            {'id': 'openrouter:nvidia/nemotron-nano-12b-v2-vl', 'name': 'Nemotron Nano 12B v2 VL',
             'description': 'NVIDIA Nemotron Nano 12B vision-language'},
            {'id': 'openrouter:nvidia/nemotron-nano-12b-v2-vl:free', 'name': 'Nemotron Nano 12B v2 VL (Free)',
             'description': 'NVIDIA Nemotron Nano 12B VL - free'},
            {'id': 'openrouter:nvidia/nemotron-nano-9b-v2', 'name': 'Nemotron Nano 9B v2',
             'description': 'NVIDIA Nemotron Nano 9B v2'},
            {'id': 'openrouter:nvidia/nemotron-nano-9b-v2:free', 'name': 'Nemotron Nano 9B v2 (Free)',
             'description': 'NVIDIA Nemotron Nano 9B v2 - free'},

            # ===== OpenRouter - OpenAI (full) =====
            {'id': 'openrouter:openai/chatgpt-4o-latest', 'name': 'ChatGPT-4o Latest',
             'description': 'Latest ChatGPT-4o via OR'},
            {'id': 'openrouter:openai/gpt-3.5-turbo', 'name': 'GPT-3.5 Turbo', 'description': 'OpenAI GPT-3.5 Turbo'},
            {'id': 'openrouter:openai/gpt-3.5-turbo-0613', 'name': 'GPT-3.5 Turbo (Jun 2023)',
             'description': 'GPT-3.5 Turbo June 2023'},
            {'id': 'openrouter:openai/gpt-3.5-turbo-16k', 'name': 'GPT-3.5 Turbo 16K',
             'description': 'GPT-3.5 Turbo extended context'},
            {'id': 'openrouter:openai/gpt-3.5-turbo-instruct', 'name': 'GPT-3.5 Turbo Instruct',
             'description': 'GPT-3.5 Turbo instruction-tuned'},
            {'id': 'openrouter:openai/gpt-4', 'name': 'GPT-4', 'description': 'OpenAI GPT-4'},
            {'id': 'openrouter:openai/gpt-4-0314', 'name': 'GPT-4 (Mar 2023)', 'description': 'GPT-4 March 2023'},
            {'id': 'openrouter:openai/gpt-4-1106-preview', 'name': 'GPT-4 Turbo Preview (Nov 2023)',
             'description': 'GPT-4 November 2023 preview'},
            {'id': 'openrouter:openai/gpt-4-turbo', 'name': 'GPT-4 Turbo', 'description': 'OpenAI GPT-4 Turbo'},
            {'id': 'openrouter:openai/gpt-4-turbo-preview', 'name': 'GPT-4 Turbo Preview',
             'description': 'OpenAI GPT-4 Turbo Preview'},
            {'id': 'openrouter:openai/gpt-4.1', 'name': 'GPT-4.1 (OR)', 'description': 'GPT-4.1 via OpenRouter'},
            {'id': 'openrouter:openai/gpt-4.1-mini', 'name': 'GPT-4.1 Mini (OR)',
             'description': 'GPT-4.1 Mini via OpenRouter'},
            {'id': 'openrouter:openai/gpt-4.1-nano', 'name': 'GPT-4.1 Nano (OR)',
             'description': 'GPT-4.1 Nano via OpenRouter'},
            {'id': 'openrouter:openai/gpt-4o', 'name': 'GPT-4o (OR)', 'description': 'GPT-4o via OpenRouter'},
            {'id': 'openrouter:openai/gpt-4o-2024-05-13', 'name': 'GPT-4o (May 2024)',
             'description': 'GPT-4o May 2024'},
            {'id': 'openrouter:openai/gpt-4o-2024-08-06', 'name': 'GPT-4o (Aug 2024)',
             'description': 'GPT-4o August 2024'},
            {'id': 'openrouter:openai/gpt-4o-2024-11-20', 'name': 'GPT-4o (Nov 2024)',
             'description': 'GPT-4o November 2024'},
            {'id': 'openrouter:openai/gpt-4o-audio-preview', 'name': 'GPT-4o Audio Preview',
             'description': 'GPT-4o with audio capabilities'},
            {'id': 'openrouter:openai/gpt-4o-mini', 'name': 'GPT-4o Mini (OR)',
             'description': 'GPT-4o Mini via OpenRouter'},
            {'id': 'openrouter:openai/gpt-4o-mini-2024-07-18', 'name': 'GPT-4o Mini (Jul 2024)',
             'description': 'GPT-4o Mini July 2024'},
            {'id': 'openrouter:openai/gpt-4o-mini-search-preview', 'name': 'GPT-4o Mini Search Preview',
             'description': 'GPT-4o Mini with search'},
            {'id': 'openrouter:openai/gpt-4o-search-preview', 'name': 'GPT-4o Search Preview',
             'description': 'GPT-4o with search'},
            {'id': 'openrouter:openai/gpt-4o:extended', 'name': 'GPT-4o Extended',
             'description': 'GPT-4o extended context'},
            {'id': 'openrouter:openai/gpt-5', 'name': 'GPT-5 (OR)', 'description': 'GPT-5 via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5-chat', 'name': 'GPT-5 Chat (OR)',
             'description': 'GPT-5 Chat via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5-image', 'name': 'GPT-5 Image',
             'description': 'GPT-5 with image generation'},
            {'id': 'openrouter:openai/gpt-5-image-mini', 'name': 'GPT-5 Image Mini',
             'description': 'Compact GPT-5 image model'},
            {'id': 'openrouter:openai/gpt-5-mini', 'name': 'GPT-5 Mini (OR)',
             'description': 'GPT-5 Mini via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5-nano', 'name': 'GPT-5 Nano (OR)',
             'description': 'GPT-5 Nano via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5-pro', 'name': 'GPT-5 Pro (OR)', 'description': 'GPT-5 Pro via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5.1', 'name': 'GPT-5.1 (OR)', 'description': 'GPT-5.1 via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5.1-chat', 'name': 'GPT-5.1 Chat (OR)',
             'description': 'GPT-5.1 Chat via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5.2', 'name': 'GPT-5.2 (OR)', 'description': 'GPT-5.2 via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5.2-chat', 'name': 'GPT-5.2 Chat (OR)',
             'description': 'GPT-5.2 Chat via OpenRouter'},
            {'id': 'openrouter:openai/gpt-5.2-pro', 'name': 'GPT-5.2 Pro (OR)',
             'description': 'GPT-5.2 Pro via OpenRouter'},
            {'id': 'openrouter:openai/o1', 'name': 'o1 (OR)', 'description': 'o1 reasoning via OpenRouter'},
            {'id': 'openrouter:openai/o1-mini', 'name': 'o1 Mini (OR)', 'description': 'o1 Mini via OpenRouter'},
            {'id': 'openrouter:openai/o1-mini-2024-09-12', 'name': 'o1 Mini (Sep 2024)',
             'description': 'o1 Mini September 2024'},
            {'id': 'openrouter:openai/o1-pro', 'name': 'o1 Pro (OR)', 'description': 'o1 Pro via OpenRouter'},
            {'id': 'openrouter:openai/o3', 'name': 'o3 (OR)', 'description': 'o3 via OpenRouter'},
            {'id': 'openrouter:openai/o3-deep-research', 'name': 'o3 Deep Research',
             'description': 'o3 with deep research capability'},
            {'id': 'openrouter:openai/o3-mini', 'name': 'o3 Mini (OR)', 'description': 'o3 Mini via OpenRouter'},
            {'id': 'openrouter:openai/o3-mini-high', 'name': 'o3 Mini High',
             'description': 'o3 Mini high reasoning effort'},
            {'id': 'openrouter:openai/o3-pro', 'name': 'o3 Pro', 'description': 'o3 Pro via OpenRouter'},
            {'id': 'openrouter:openai/o4-mini', 'name': 'o4 Mini (OR)', 'description': 'o4 Mini via OpenRouter'},
            {'id': 'openrouter:openai/o4-mini-deep-research', 'name': 'o4 Mini Deep Research',
             'description': 'o4 Mini with deep research'},
            {'id': 'openrouter:openai/o4-mini-high', 'name': 'o4 Mini High', 'description': 'o4 Mini high effort'},
            {'id': 'openrouter:openai/text-embedding-3-large', 'name': 'Text Embedding 3 Large',
             'description': 'OpenAI text embedding large'},
            {'id': 'openrouter:openai/text-embedding-3-small', 'name': 'Text Embedding 3 Small',
             'description': 'OpenAI text embedding small'},
            {'id': 'openrouter:openai/text-embedding-ada-002', 'name': 'Text Embedding Ada 002',
             'description': 'OpenAI Ada embedding model'},

            # ===== OpenRouter - OpenGVLab / OpenRouter Meta =====
            {'id': 'openrouter:opengvlab/internvl3-14b', 'name': 'InternVL3 14B',
             'description': 'OpenGVLab InternVL3 14B vision-language'},
            {'id': 'openrouter:openrouter/auto', 'name': 'OpenRouter Auto', 'description': 'Automatic model routing'},
            {'id': 'openrouter:openrouter/bert-nebulon-alpha', 'name': 'BERT Nebulon Alpha',
             'description': 'OpenRouter BERT Nebulon Alpha'},
            {'id': 'openrouter:openrouter/polaris-alpha', 'name': 'Polaris Alpha',
             'description': 'OpenRouter Polaris Alpha'},

            # ===== OpenRouter - Perplexity =====
            {'id': 'openrouter:perplexity/sonar', 'name': 'Sonar', 'description': 'Perplexity Sonar'},
            {'id': 'openrouter:perplexity/sonar-pro', 'name': 'Sonar Pro', 'description': 'Perplexity Sonar Pro'},
            {'id': 'openrouter:perplexity/sonar-pro-search', 'name': 'Sonar Pro Search',
             'description': 'Perplexity Sonar Pro with live search'},
            {'id': 'openrouter:perplexity/sonar-deep-research', 'name': 'Sonar Deep Research',
             'description': 'Perplexity deep research model'},
            {'id': 'openrouter:perplexity/sonar-reasoning-pro', 'name': 'Sonar Reasoning Pro',
             'description': 'Perplexity Sonar with advanced reasoning'},

            # ===== OpenRouter - Prime Intellect / Pygmalion =====
            {'id': 'openrouter:prime-intellect/intellect-3', 'name': 'Intellect 3', 'description': 'Prime Intellect 3'},
            {'id': 'openrouter:pygmalionai/mythalion-13b', 'name': 'Mythalion 13B',
             'description': 'PygmalionAI Mythalion 13B'},

            # ===== OpenRouter - Qwen =====
            {'id': 'openrouter:qwen/qwen-2.5-72b-instruct', 'name': 'Qwen 2.5 72B Instruct',
             'description': 'Alibaba Qwen 2.5 72B'},
            {'id': 'openrouter:qwen/qwen-2.5-72b-instruct:free', 'name': 'Qwen 2.5 72B Instruct (Free)',
             'description': 'Qwen 2.5 72B - free'},
            {'id': 'openrouter:qwen/qwen-2.5-7b-instruct', 'name': 'Qwen 2.5 7B Instruct',
             'description': 'Alibaba Qwen 2.5 7B'},
            {'id': 'openrouter:qwen/qwen-2.5-coder-32b-instruct', 'name': 'Qwen 2.5 Coder 32B',
             'description': 'Qwen 2.5 coding specialist 32B'},
            {'id': 'openrouter:qwen/qwen-2.5-coder-32b-instruct:free', 'name': 'Qwen 2.5 Coder 32B (Free)',
             'description': 'Qwen 2.5 Coder 32B - free'},
            {'id': 'openrouter:qwen/qwen-2.5-vl-7b-instruct', 'name': 'Qwen 2.5 VL 7B',
             'description': 'Qwen 2.5 vision-language 7B'},
            {'id': 'openrouter:qwen/qwen-max', 'name': 'Qwen Max', 'description': 'Alibaba Qwen Max'},
            {'id': 'openrouter:qwen/qwen-plus', 'name': 'Qwen Plus', 'description': 'Alibaba Qwen Plus'},
            {'id': 'openrouter:qwen/qwen-plus-2025-07-28', 'name': 'Qwen Plus (Jul 2025)',
             'description': 'Qwen Plus July 2025'},
            {'id': 'openrouter:qwen/qwen-plus-2025-07-28:thinking', 'name': 'Qwen Plus (Jul 2025) Thinking',
             'description': 'Qwen Plus July 2025 with thinking'},
            {'id': 'openrouter:qwen/qwen-turbo', 'name': 'Qwen Turbo', 'description': 'Alibaba Qwen Turbo'},
            {'id': 'openrouter:qwen/qwen-vl-max', 'name': 'Qwen VL Max', 'description': 'Qwen vision-language Max'},
            {'id': 'openrouter:qwen/qwen-vl-plus', 'name': 'Qwen VL Plus', 'description': 'Qwen vision-language Plus'},
            {'id': 'openrouter:qwen/qwen2.5-vl-32b-instruct', 'name': 'Qwen 2.5 VL 32B',
             'description': 'Qwen 2.5 vision-language 32B'},
            {'id': 'openrouter:qwen/qwen2.5-vl-32b-instruct:free', 'name': 'Qwen 2.5 VL 32B (Free)',
             'description': 'Qwen 2.5 VL 32B - free'},
            {'id': 'openrouter:qwen/qwen2.5-vl-72b-instruct', 'name': 'Qwen 2.5 VL 72B',
             'description': 'Qwen 2.5 vision-language 72B'},
            {'id': 'openrouter:qwen/qwen2.5-vl-72b-instruct:free', 'name': 'Qwen 2.5 VL 72B (Free)',
             'description': 'Qwen 2.5 VL 72B - free'},
            {'id': 'openrouter:qwen/qwen3-14b', 'name': 'Qwen3 14B', 'description': 'Alibaba Qwen3 14B'},
            {'id': 'openrouter:qwen/qwen3-14b:free', 'name': 'Qwen3 14B (Free)', 'description': 'Qwen3 14B - free'},
            {'id': 'openrouter:qwen/qwen3-235b-a22b', 'name': 'Qwen3 235B A22B',
             'description': 'Qwen3 MoE 235B active 22B'},
            {'id': 'openrouter:qwen/qwen3-235b-a22b-2507', 'name': 'Qwen3 235B A22B (Jul 2025)',
             'description': 'Qwen3 235B Jul 2025'},
            {'id': 'openrouter:qwen/qwen3-235b-a22b-thinking-2507', 'name': 'Qwen3 235B A22B Thinking (Jul 2025)',
             'description': 'Qwen3 235B with thinking Jul 2025'},
            {'id': 'openrouter:qwen/qwen3-235b-a22b:free', 'name': 'Qwen3 235B A22B (Free)',
             'description': 'Qwen3 235B - free'},
            {'id': 'openrouter:qwen/qwen3-30b-a3b', 'name': 'Qwen3 30B A3B', 'description': 'Qwen3 MoE 30B active 3B'},
            {'id': 'openrouter:qwen/qwen3-30b-a3b-instruct-2507', 'name': 'Qwen3 30B A3B Instruct (Jul 2025)',
             'description': 'Qwen3 30B Instruct Jul 2025'},
            {'id': 'openrouter:qwen/qwen3-30b-a3b-thinking-2507', 'name': 'Qwen3 30B A3B Thinking (Jul 2025)',
             'description': 'Qwen3 30B Thinking Jul 2025'},
            {'id': 'openrouter:qwen/qwen3-30b-a3b:free', 'name': 'Qwen3 30B A3B (Free)',
             'description': 'Qwen3 30B - free'},
            {'id': 'openrouter:qwen/qwen3-32b', 'name': 'Qwen3 32B', 'description': 'Alibaba Qwen3 32B'},
            {'id': 'openrouter:qwen/qwen3-4b:free', 'name': 'Qwen3 4B (Free)', 'description': 'Qwen3 4B - free'},
            {'id': 'openrouter:qwen/qwen3-8b', 'name': 'Qwen3 8B', 'description': 'Alibaba Qwen3 8B'},
            {'id': 'openrouter:qwen/qwen3-8b:free', 'name': 'Qwen3 8B (Free)', 'description': 'Qwen3 8B - free'},
            {'id': 'openrouter:qwen/qwen3-coder', 'name': 'Qwen3 Coder', 'description': 'Qwen3 coding specialist'},
            {'id': 'openrouter:qwen/qwen3-coder-30b-a3b-instruct', 'name': 'Qwen3 Coder 30B Instruct',
             'description': 'Qwen3 Coder 30B instruction-tuned'},
            {'id': 'openrouter:qwen/qwen3-coder-flash', 'name': 'Qwen3 Coder Flash',
             'description': 'Fast Qwen3 coding model'},
            {'id': 'openrouter:qwen/qwen3-coder-plus', 'name': 'Qwen3 Coder Plus',
             'description': 'Enhanced Qwen3 coding model'},
            {'id': 'openrouter:qwen/qwen3-coder:free', 'name': 'Qwen3 Coder (Free)',
             'description': 'Qwen3 Coder - free'},
            {'id': 'openrouter:qwen/qwen3-embedding-0.6b', 'name': 'Qwen3 Embedding 0.6B',
             'description': 'Qwen3 compact embedding model'},
            {'id': 'openrouter:qwen/qwen3-embedding-4b', 'name': 'Qwen3 Embedding 4B',
             'description': 'Qwen3 medium embedding model'},
            {'id': 'openrouter:qwen/qwen3-embedding-8b', 'name': 'Qwen3 Embedding 8B',
             'description': 'Qwen3 large embedding model'},
            {'id': 'openrouter:qwen/qwen3-max', 'name': 'Qwen3 Max', 'description': 'Qwen3 Max flagship'},
            {'id': 'openrouter:qwen/qwen3-next-80b-a3b-instruct', 'name': 'Qwen3 Next 80B Instruct',
             'description': 'Qwen3 Next 80B instruction-tuned'},
            {'id': 'openrouter:qwen/qwen3-next-80b-a3b-thinking', 'name': 'Qwen3 Next 80B Thinking',
             'description': 'Qwen3 Next 80B with thinking'},
            {'id': 'openrouter:qwen/qwen3-vl-30b-a3b-instruct', 'name': 'Qwen3 VL 30B Instruct',
             'description': 'Qwen3 vision-language 30B'},
            {'id': 'openrouter:qwen/qwen3-vl-30b-a3b-thinking', 'name': 'Qwen3 VL 30B Thinking',
             'description': 'Qwen3 VL 30B with thinking'},
            {'id': 'openrouter:qwen/qwen3-vl-8b-instruct', 'name': 'Qwen3 VL 8B Instruct',
             'description': 'Qwen3 vision-language 8B'},
            {'id': 'openrouter:qwen/qwen3-vl-8b-thinking', 'name': 'Qwen3 VL 8B Thinking',
             'description': 'Qwen3 VL 8B with thinking'},
            {'id': 'openrouter:qwen/qwq-32b', 'name': 'QwQ 32B', 'description': 'Qwen QwQ 32B reasoning model'},
            {'id': 'openrouter:qwen/qwq-32b-preview', 'name': 'QwQ 32B Preview', 'description': 'Qwen QwQ 32B preview'},
            {'id': 'openrouter:qwen/qwq-32b:free', 'name': 'QwQ 32B (Free)', 'description': 'QwQ 32B - free'},

            # ===== OpenRouter - Raifle / RekaAI / Relace =====
            {'id': 'openrouter:raifle/sorcererlm-8x22b', 'name': 'SorcererLM 8x22B',
             'description': 'Raifle SorcererLM MoE 8x22B'},
            {'id': 'openrouter:rekaai/reka-flash-3:free', 'name': 'Reka Flash 3 (Free)',
             'description': 'RekaAI Reka Flash 3 - free'},
            {'id': 'openrouter:relace/relace-apply-3', 'name': 'Relace Apply 3', 'description': 'Relace Apply 3 model'},

            # ===== OpenRouter - SAO10K =====
            {'id': 'openrouter:sao10k/l3-euryale-70b', 'name': 'L3 Euryale 70B',
             'description': 'SAO10K LLaMA 3 Euryale 70B'},
            {'id': 'openrouter:sao10k/l3-lunaris-8b', 'name': 'L3 Lunaris 8B',
             'description': 'SAO10K LLaMA 3 Lunaris 8B'},
            {'id': 'openrouter:sao10k/l3.1-euryale-70b', 'name': 'L3.1 Euryale 70B',
             'description': 'SAO10K LLaMA 3.1 Euryale 70B'},
            {'id': 'openrouter:sao10k/l3.3-euryale-70b', 'name': 'L3.3 Euryale 70B',
             'description': 'SAO10K LLaMA 3.3 Euryale 70B'},

            # ===== OpenRouter - SarvamAI / SCB10X / Shisa / Sopho / Switchpoint =====
            {'id': 'openrouter:sarvamai/sarvam-m:free', 'name': 'Sarvam M (Free)',
             'description': 'SarvamAI Sarvam M - free'},
            {'id': 'openrouter:scb10x/llama3.1-typhoon2-70b-instruct', 'name': 'Typhoon2 70B Instruct',
             'description': 'SCB10X Typhoon2 LLaMA 3.1 70B'},
            {'id': 'openrouter:shisa-ai/shisa-v2-llama3.3-70b', 'name': 'Shisa V2 LLaMA 3.3 70B',
             'description': 'Shisa AI v2 on LLaMA 3.3 70B'},
            {'id': 'openrouter:shisa-ai/shisa-v2-llama3.3-70b:free', 'name': 'Shisa V2 LLaMA 3.3 70B (Free)',
             'description': 'Shisa V2 70B - free'},
            {'id': 'openrouter:sophosympatheia/midnight-rose-70b', 'name': 'Midnight Rose 70B',
             'description': 'Sophosympatheia Midnight Rose 70B'},
            {'id': 'openrouter:switchpoint/router', 'name': 'Switchpoint Router',
             'description': 'Switchpoint intelligent router'},

            # ===== OpenRouter - Tencent / TheDrummer =====
            {'id': 'openrouter:tencent/hunyuan-a13b-instruct', 'name': 'HunYuan A13B Instruct',
             'description': 'Tencent HunYuan A13B'},
            {'id': 'openrouter:tencent/hunyuan-a13b-instruct:free', 'name': 'HunYuan A13B Instruct (Free)',
             'description': 'Tencent HunYuan A13B - free'},
            {'id': 'openrouter:thedrummer/anubis-70b-v1.1', 'name': 'Anubis 70B v1.1',
             'description': 'TheDrummer Anubis 70B v1.1'},
            {'id': 'openrouter:thedrummer/anubis-pro-105b-v1', 'name': 'Anubis Pro 105B v1',
             'description': 'TheDrummer Anubis Pro 105B'},
            {'id': 'openrouter:thedrummer/cydonia-24b-v4.1', 'name': 'Cydonia 24B v4.1',
             'description': 'TheDrummer Cydonia 24B v4.1'},
            {'id': 'openrouter:thedrummer/rocinante-12b', 'name': 'Rocinante 12B',
             'description': 'TheDrummer Rocinante 12B'},
            {'id': 'openrouter:thedrummer/skyfall-36b-v2', 'name': 'Skyfall 36B v2',
             'description': 'TheDrummer Skyfall 36B v2'},
            {'id': 'openrouter:thedrummer/unslopnemo-12b', 'name': 'UnslopNemo 12B',
             'description': 'TheDrummer UnslopNemo 12B'},

            # ===== OpenRouter - Embedding Models (GTE / THUDM) =====
            {'id': 'openrouter:thenlper/gte-base', 'name': 'GTE Base', 'description': 'GTE base embedding model'},
            {'id': 'openrouter:thenlper/gte-large', 'name': 'GTE Large', 'description': 'GTE large embedding model'},
            {'id': 'openrouter:thudm/glm-4-32b', 'name': 'GLM-4 32B', 'description': 'THUDM GLM-4 32B'},
            {'id': 'openrouter:thudm/glm-4.1v-9b-thinking', 'name': 'GLM-4.1V 9B Thinking',
             'description': 'THUDM GLM-4.1V 9B vision thinking'},
            {'id': 'openrouter:thudm/glm-z1-32b', 'name': 'GLM-Z1 32B', 'description': 'THUDM GLM-Z1 32B'},

            # ===== OpenRouter - TNG Tech =====
            {'id': 'openrouter:tngtech/deepseek-r1t-chimera', 'name': 'DeepSeek R1T Chimera (OR)',
             'description': 'TNG DeepSeek R1T Chimera via OR'},
            {'id': 'openrouter:tngtech/deepseek-r1t-chimera:free', 'name': 'DeepSeek R1T Chimera (OR, Free)',
             'description': 'TNG DeepSeek R1T Chimera - free'},
            {'id': 'openrouter:tngtech/deepseek-r1t2-chimera:free', 'name': 'DeepSeek R1T2 Chimera (OR, Free)',
             'description': 'TNG DeepSeek R1T2 Chimera via OR - free'},
            {'id': 'openrouter:tngtech/tng-r1t-chimera', 'name': 'TNG R1T Chimera (OR)',
             'description': 'TNG R1T Chimera via OR'},
            {'id': 'openrouter:tngtech/tng-r1t-chimera:free', 'name': 'TNG R1T Chimera (OR, Free)',
             'description': 'TNG R1T Chimera via OR - free'},

            # ===== OpenRouter - Undi95 / xAI =====
            {'id': 'openrouter:undi95/remm-slerp-l2-13b', 'name': 'ReMM SLERP L2 13B',
             'description': 'Undi95 ReMM SLERP LLaMA 2 13B'},
            {'id': 'openrouter:x-ai/grok-2-1212', 'name': 'Grok 2 (12-12) (OR)',
             'description': 'Grok 2 December 2024 via OR'},
            {'id': 'openrouter:x-ai/grok-2-vision-1212', 'name': 'Grok 2 Vision (12-12)',
             'description': 'Grok 2 Vision December 2024'},
            {'id': 'openrouter:x-ai/grok-3', 'name': 'Grok 3 (OR)', 'description': 'Grok 3 via OpenRouter'},
            {'id': 'openrouter:x-ai/grok-3-beta', 'name': 'Grok 3 Beta (OR)', 'description': 'Grok 3 Beta via OR'},
            {'id': 'openrouter:x-ai/grok-3-mini', 'name': 'Grok 3 Mini (OR)', 'description': 'Grok 3 Mini via OR'},
            {'id': 'openrouter:x-ai/grok-3-mini-beta', 'name': 'Grok 3 Mini Beta (OR)',
             'description': 'Grok 3 Mini Beta via OR'},
            {'id': 'openrouter:x-ai/grok-4', 'name': 'Grok 4 (OR)', 'description': 'Grok 4 via OpenRouter'},
            {'id': 'openrouter:x-ai/grok-4-fast:free', 'name': 'Grok 4 Fast (Free)',
             'description': 'Grok 4 Fast - free tier'},
            {'id': 'openrouter:x-ai/grok-4.1-fast:free', 'name': 'Grok 4.1 Fast (Free)',
             'description': 'Grok 4.1 Fast - free tier'},
            {'id': 'openrouter:x-ai/grok-code-fast-1', 'name': 'Grok Code Fast 1 (OR)',
             'description': 'Grok coding model via OR'},
            {'id': 'openrouter:x-ai/grok-vision-beta', 'name': 'Grok Vision Beta (OR)',
             'description': 'Grok Vision Beta via OR'},

            # ===== OpenRouter - Xiaomi / Z-AI =====
            {'id': 'openrouter:xiaomi/mimo-v2-flash:free', 'name': 'MiMo V2 Flash (Free)',
             'description': 'Xiaomi MiMo V2 Flash - free'},
            {'id': 'openrouter:z-ai/glm-4-32b', 'name': 'GLM-4 32B (Z-AI)', 'description': 'Z-AI GLM-4 32B'},
            {'id': 'openrouter:z-ai/glm-4.5', 'name': 'GLM-4.5', 'description': 'Z-AI GLM-4.5'},
            {'id': 'openrouter:z-ai/glm-4.5-air', 'name': 'GLM-4.5 Air', 'description': 'Z-AI GLM-4.5 Air'},
            {'id': 'openrouter:z-ai/glm-4.5-air:free', 'name': 'GLM-4.5 Air (Free)',
             'description': 'Z-AI GLM-4.5 Air - free'},
            {'id': 'openrouter:z-ai/glm-4.5v', 'name': 'GLM-4.5V', 'description': 'Z-AI GLM-4.5 vision model'},
            {'id': 'openrouter:z-ai/glm-4.6', 'name': 'GLM-4.6', 'description': 'Z-AI GLM-4.6'},
            {'id': 'openrouter:z-ai/glm-4.6:exacto', 'name': 'GLM-4.6 Exacto',
             'description': 'Z-AI GLM-4.6 deterministic variant'},
            {'id': 'openrouter:z-ai/glm-4.6v', 'name': 'GLM-4.6V', 'description': 'Z-AI GLM-4.6 vision model'},
            {'id': 'openrouter:z-ai/glm-4.7', 'name': 'GLM-4.7', 'description': 'Z-AI GLM-4.7'},
        ]

    def get_gemini_models(self):
        """Get available Gemini models"""
        return [
            # GEMINI 2.5 MODELS
            {
                'id': 'gemini-2.5-flash',
                'name': 'Gemini 2.5 Flash ⚡ (RECOMMENDED)',
                'description': '✅ FREE: 5 RPM, 20/day - Current model, best balance'
            },
            {
                'id': 'gemini-2.5-flash-lite',
                'name': 'Gemini 2.5 Flash Lite ⚡⚡',
                'description': '✅ FREE: 10 RPM, 20/day - Faster, lightweight version'
            },
            # GEMINI 3 MODEL
            {
                'id': 'gemini-3-flash',
                'name': 'Gemini 3 Flash 🆕',
                'description': '✅ FREE: 5 RPM, 20/day - NEWEST model (just released!)'
            },
            # GEMMA MODELS
            {
                'id': 'gemma-3-27b',
                'name': 'Gemma 3 27B 🔥',
                'description': '✅ FREE: 30 RPM, 14,400/day - Highest limits! Open source'
            },
            {
                'id': 'gemma-3-12b',
                'name': 'Gemma 3 12B',
                'description': '✅ FREE: 30 RPM, 14,400/day - Open source, very fast'
            },
            {
                'id': 'gemma-3-4b',
                'name': 'Gemma 3 4B',
                'description': '✅ FREE: 30 RPM, 14,400/day - Smallest, ultra-fast'
            },
        ]