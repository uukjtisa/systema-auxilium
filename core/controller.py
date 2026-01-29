"""
Main Controller - Orchestrates the AI assistant with Voice Support
UPDATED: Voice mode integration, device management, TTS control
"""

from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from core.ai_engine import AIEngine
from core.puter_server import PuterServer
from core.system_info import get_system_info, format_system_info_for_prompt
from core.voice_handler import VoiceHandler
from ui.floating_window import FloatingWindow
from core.ai_worker import AIWorker
import os
import json
import webbrowser


class AssistantController(QObject):
    """Main controller for the AI assistant with voice support"""

    log_signal = pyqtSignal(str, str)

    # NEW SIGNALS FOR VOICE
    voice_message_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        # Settings
        self.settings_file = "assistant_settings.json"
        self.settings = self.load_settings()

        # Detect system information
        self.log("Detecting system information...")
        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)
        self.log(f"System detected: {system_info_dict['os']} {system_info_dict['os_release']}")

        # Puter server
        self.puter_server = PuterServer(port=5555, log_callback=self.log)

        # Voice handler - NEW!
        self.voice_handler = VoiceHandler(log_callback=self.log)
        self.voice_mode_active = False

        # Load ElevenLabs settings into voice handler
        elevenlabs_enabled = self.settings.get('elevenlabs_enabled', False)
        elevenlabs_voice_id = self.settings.get('elevenlabs_voice_id', '')
        self.voice_handler.set_elevenlabs_settings(elevenlabs_enabled, elevenlabs_voice_id)

        # Set voice callbacks
        self.voice_handler.on_transcription = self.handle_voice_transcription
        self.voice_handler.on_state_change = self.handle_voice_state_change

        # Initialize AI engine
        self.ai = AIEngine(
            log_callback=self.log,
            api_key=self.settings.get('api_key', ''),
            gemini_api_key=self.settings.get('gemini_api_key', ''),
            puter_server=self.puter_server,
            system_info=system_info_text,
            voice_mode=False,  # Start with voice off
            elevenlabs_enabled=self.settings.get('elevenlabs_enabled', False)
        )

        # Apply settings
        self.ai.set_provider(self.settings.get('ai_provider', 'anthropic'))
        self.ai.set_puter_model(self.settings.get('puter_model', 'gpt-4o-mini'))
        self.ai.set_gemini_model(self.settings.get('gemini_model', 'gemini-2.0-flash-exp'))
        self.ai.set_tts_provider(self.settings.get('tts_provider', 'edge-tts'))
        self.ai.set_puter_tts_model(self.settings.get('puter_tts_model', 'tts-1'))
        self.ai.set_puter_tts_voice(self.settings.get('puter_tts_voice'))

        # Auto-start Puter if selected
        if self.settings.get('ai_provider') == 'puter':
            self.start_puter_server()

        # Initialize UI
        self.ui = FloatingWindow(self)

        # Tool mode timer
        self.tool_mode_timer = QTimer()
        self.tool_mode_timer.timeout.connect(self.auto_continue_tool_mode)
        self.tool_mode_timer.setInterval(1000)

        # Track processing
        self.is_processing = False

        # NEW: Connect voice message signal to handler (main thread safe)
        self.voice_message_signal.connect(self._handle_voice_message_on_main_thread)

        self.log("System AI Assistant initialized", "SUCCESS")

    def log(self, message, level="INFO"):
        print(f"[Controller] {message}")

    def load_settings(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading settings: {e}")

        # FIRST-TIME LAUNCH DEFAULTS
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
        }

    def save_settings(self):
        self.log("Saving settings...", "INFO")
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
            self.log("Settings saved", "SUCCESS")
        except Exception as e:
            self.log(f"Error saving settings: {e}", "ERROR")

    def enable_voice_mode(self):
        """Enable voice input/output"""
        try:
            # List available devices
            input_devices, output_devices = self.voice_handler.list_audio_devices()

            # Get device selection from settings
            input_device = self.settings.get('voice_input_device')
            output_device = self.settings.get('voice_output_device')

            # Set devices
            self.voice_handler.set_devices(input_device, output_device)

            # NEW: Set VAD configuration
            webrtc_enabled = self.settings.get('vad_webrtc_enabled', True)
            silero_enabled = self.settings.get('vad_silero_enabled', False)
            webrtc_aggressiveness = self.settings.get('vad_aggressiveness', 3)
            silero_threshold = self.settings.get('vad_silero_threshold', 0.5)

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
                self.voice_handler.set_puter_server(self.puter_server)
                self.voice_handler.set_tts_provider('puter')
                self.voice_handler.set_puter_tts_settings(
                    self.settings.get('puter_tts_model', 'tts-1'),
                    self.settings.get('puter_tts_voice')
                )

                # NEW: Set ElevenLabs settings if enabled
                elevenlabs_enabled = self.get_elevenlabs_enabled()
                if elevenlabs_enabled:
                    voice_id = self.get_elevenlabs_voice_id()
                    self.voice_handler.set_elevenlabs_settings(True, voice_id)

            # NEW: Set up playback callback with null check
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.voice_handler.on_playback_started = self.ui.chat_window.on_voice_playback_started
                self.log("Voice playback callback wired to chat window", "SUCCESS")
            else:
                self.log("Chat window not available for voice callback", "WARNING")

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
                return True, message
            else:
                return False, "Failed to start voice capture"

        except Exception as e:
            self.log(f"Voice mode error: {e}", "ERROR")
            return False, f"Error: {e}"

    def disable_voice_mode(self):
        """Disable voice mode"""
        try:
            self.log("Disabling voice mode...")

            # Stop listening first
            self.voice_handler.stop_listening()

            # Then interrupt any ongoing speech
            self.voice_handler.interrupt_speech()

            # Update state
            self.voice_mode_active = False

            # Update AI engine
            self.ai.update_voice_settings(False, False)

            self.log("Voice mode disabled", "SUCCESS")
        except Exception as e:
            self.log(f"Error disabling voice: {e}", "ERROR")

    def handle_voice_transcription(self, text):
        """Handle transcribed voice input - SIGNAL VERSION"""
        self.log(f"Voice transcribed: {text}")

        # Emit signal - Qt will automatically marshal to main thread
        self.voice_message_signal.emit(text)

    def _handle_voice_message_on_main_thread(self, text):
        """Handle voice message on main Qt thread (slot for signal)"""
        # FIXED: Ensure chat window exists (create it if needed)
        if not hasattr(self.ui, 'chat_window') or self.ui.chat_window is None:
            # Create chat window but don't show it
            from ui.chat_window import ChatWindow
            self.ui.chat_window = ChatWindow(self)
            self.log("Chat window created for voice message buffering")

        # Always add to chat window (even if hidden)
        self.ui.chat_window.add_user_message(text)

        # Send to AI (works whether chat is visible or not)
        self.send_message(text)

    def handle_voice_state_change(self, state):
        """Handle voice state changes"""
        self.log(f"Voice state: {state}")

        # Update UI
        if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
            self.ui.chat_window.update_voice_status(state)

    def wait_for_voice_completion(self):
        """Wait for current voice playback to complete"""
        if not self.voice_mode_active:
            return

        max_wait = 30  # Maximum 30 seconds
        waited = 0

        while self.voice_handler.is_speaking and waited < max_wait:
            import time
            time.sleep(0.1)
            waited += 0.1

        self.log(f"Voice completed after {waited:.1f}s")

    def get_user_name(self):
        """Get user name"""
        return self.settings.get('user_name', '')

    def set_user_name(self, name):
        """Set user name"""
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
        self.settings['custom_instructions'] = instructions
        self.save_settings()
        # Regenerate system prompt
        self._update_system_prompt()
        self.log("Custom instructions updated", "SUCCESS")

    def _update_system_prompt(self):
        """Update system prompt with personalization"""
        from core.system_info import get_system_info, format_system_info_for_prompt

        system_info_dict = get_system_info()
        system_info_text = format_system_info_for_prompt(system_info_dict)

        # Add user name if set
        user_name = self.get_user_name()
        if user_name:
            system_info_text += f"\n\n**USER NAME:** {user_name}\nAddress the user by their name when appropriate."

        # Add custom instructions if set
        custom_instructions = self.get_custom_instructions()
        if custom_instructions:
            system_info_text += f"\n\n**CUSTOM USER INSTRUCTIONS:**\n{custom_instructions}"

        # Update AI engine
        self.ai.system_info = system_info_text
        self.ai.update_voice_settings(self.ai.voice_mode, self.ai.elevenlabs_enabled)

    def speak_text(self, text):
        """Speak text using TTS (only if voice mode is active and not in tool mode)"""
        if not self.voice_mode_active:
            return

        if self.ai.tool_manager.in_tool_mode:
            self.log("Skipping TTS - in tool mode")
            return

        self.log(f"Speaking: {text[:50]}...")
        self.voice_handler.speak_text_sync(text)

    def get_voice_interrupt_mode(self):
        """Get voice interrupt mode"""
        return self.settings.get('voice_interrupt_mode', 'manual')

    def set_voice_interrupt_mode(self, mode):
        """Set voice interrupt mode ('auto' or 'manual')"""
        self.settings['voice_interrupt_mode'] = mode
        self.voice_handler.set_interrupt_mode(mode)
        self.save_settings()
        self.log(f"Voice interrupt mode set to {mode}", "SUCCESS")

    def get_elevenlabs_enabled(self):
        """Get ElevenLabs enabled state"""
        return self.settings.get('elevenlabs_enabled', False)

    def set_elevenlabs_enabled(self, enabled):
        """Set ElevenLabs enabled state"""
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
        self.settings['elevenlabs_voice_id'] = voice_id
        self.save_settings()

        # CRITICAL: Update voice handler immediately
        enabled = self.get_elevenlabs_enabled()
        self.voice_handler.set_elevenlabs_settings(enabled, voice_id)

        self.log(f"ElevenLabs voice ID set", "SUCCESS")

    def get_tts_provider(self):
        return self.settings.get('tts_provider', 'edge-tts')

    def set_tts_provider(self, provider):
        self.settings['tts_provider'] = provider
        self.ai.set_tts_provider(provider)
        self.save_settings()
        self.log(f"TTS provider set to {provider}", "SUCCESS")

    def get_puter_tts_model(self):
        return self.settings.get('puter_tts_model', 'tts-1')

    def set_puter_tts_model(self, model):
        self.settings['puter_tts_model'] = model
        self.ai.set_puter_tts_model(model)
        self.save_settings()
        self.log(f"Puter TTS model set to {model}", "SUCCESS")

    def get_puter_tts_voice(self):
        return self.settings.get('puter_tts_voice')

    def set_puter_tts_voice(self, voice):
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
        self.settings['puter_email'] = email
        self.settings['puter_password'] = password
        self.save_settings()
        self.log("Puter credentials saved", "SUCCESS")

    def reset_puter_quota(self):
        """Reset Puter quota using saved credentials"""
        creds = self.get_puter_credentials()
        if not creds['email'] or not creds['password']:
            self.log("Puter credentials not set", "ERROR")
            return False

        if not self.puter_server or not self.puter_server.is_running:
            self.log("Puter server not running", "ERROR")
            return False

        self.log("Resetting Puter quota...", "INFO")
        success = self.puter_server.reset_quota(creds['email'], creds['password'])
        if success:
            self.log("✓ Quota reset successful!", "SUCCESS")
        else:
            self.log("✗ Quota reset failed", "ERROR")
        return success

    def setup_puter_account(self):
        """Open Puter for account setup"""
        if not self.puter_server or not self.puter_server.is_running:
            self.log("Starting Puter server for account setup...", "INFO")
            if not self.start_puter_server():
                return False

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
        self.settings['api_key'] = api_key
        self.ai.set_api_key(api_key)
        self.save_settings()
        self.log("API key updated", "SUCCESS")

    def get_gemini_api_key(self):
        """Get current Gemini API key"""
        return self.settings.get('gemini_api_key', '')

    def set_gemini_api_key(self, api_key):
        """Set new Gemini API key"""
        self.settings['gemini_api_key'] = api_key
        self.ai.set_gemini_api_key(api_key)
        self.save_settings()
        self.log("Gemini API key updated", "SUCCESS")

    def get_ai_provider(self):
        """Get current AI provider"""
        return self.settings.get('ai_provider', 'anthropic')

    def set_ai_provider(self, provider):
        """Set AI provider"""
        # Check if we're switching AWAY from Puter
        current_provider = self.settings.get('ai_provider', 'anthropic')
        if current_provider == 'puter' and provider != 'puter':
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
        self.settings['puter_model'] = model
        self.ai.set_puter_model(model)
        self.save_settings()
        self.log(f"Puter model set to: {model}", "SUCCESS")

    def get_gemini_model(self):
        """Get current Gemini model"""
        return self.settings.get('gemini_model', 'gemini-2.0-flash-exp')

    def set_gemini_model(self, model):
        """Set Gemini model"""
        self.settings['gemini_model'] = model
        self.ai.set_gemini_model(model)
        self.save_settings()
        self.log(f"Gemini model set to: {model}", "SUCCESS")

    def get_debug_mode(self):
        """Get debug mode setting"""
        return self.settings.get('debug_mode', False)

    def set_debug_mode(self, enabled):
        """Set debug mode"""
        self.settings['debug_mode'] = enabled
        self.save_settings()
        self.log(f"Debug mode: {'enabled' if enabled else 'disabled'}", "SUCCESS")

    def set_tts_voice(self, voice):
        """Set TTS voice"""
        self.settings['tts_voice'] = voice
        self.log(f"SAVING: Setting TTS voice to {voice}", "INFO")
        self.voice_handler.set_tts_voice(voice)
        self.save_settings()
        self.log(f"TTS voice set to {voice}", "SUCCESS")

    def set_vad_aggressiveness(self, level):
        """Set VAD aggressiveness (0-3)"""
        self.settings['vad_aggressiveness'] = level
        self.log(f"SAVING: Setting VAD aggressiveness to {level}", "INFO")
        self.voice_handler.set_vad_aggressiveness(level)
        self.save_settings()
        self.log(f"VAD aggressiveness set to {level}", "SUCCESS")

    def start_puter_server(self):
        """Start Puter.js server"""
        try:
            self.log("Starting Puter.js server...")

            if self.puter_server.is_running:
                self.log("Puter server already running")
                if self.puter_server.check_health():
                    return True
                else:
                    self.log("Server not responding, restarting...")
                    self.puter_server.stop()
                    import time
                    time.sleep(1)

            if self.puter_server.start():
                self.log("✓ Puter.js server started at http://127.0.0.1:5555", "SUCCESS")

                # Wait a moment
                import time
                time.sleep(2)

                return True
            else:
                self.log("✗ Failed to start Puter server", "ERROR")
                return False

        except Exception as e:
            self.log(f"Error starting Puter server: {e}", "ERROR")
            return False

    def stop_puter_server(self):
        """Stop Puter.js server"""
        try:
            self.puter_server.stop()
            self.log("Puter.js server stopped", "SUCCESS")
        except Exception as e:
            self.log(f"Error stopping Puter server: {e}", "ERROR")

    def send_message(self, user_message):
        """Send user message to AI (non-blocking)"""
        if not user_message.strip():
            return

        # If in tool mode and user sends message, cancel tool mode first
        if self.ai.tool_manager.in_tool_mode:
            self.log("User interrupted tool mode - canceling")
            self.tool_mode_timer.stop()
            self.ai.tool_manager.in_tool_mode = False
            self.ai.tool_manager.last_tool_output = None
            # Show system message
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message("⚠️ Tool mode canceled by new message")

        # Prevent overlapping requests
        if self.is_processing:
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True

        self.log(f"User: {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}")

        # Show thinking in UI
        self.ui.show_thinking()

        # Create worker thread
        self.current_worker = AIWorker(self.ai, 'generate', user_message)
        self.current_worker.response_ready.connect(self.handle_ai_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def send_message_with_image(self, user_message, image_path):
        """Send user message with image attachment to AI (Puter only)"""
        if not user_message.strip():
            return

        if self.get_ai_provider() != 'puter':
            self.log("Image attachment only supported with Puter provider", "ERROR")
            return

        # Prevent overlapping requests
        if self.is_processing:
            self.log("Already processing a request - ignoring")
            return

        self.is_processing = True

        self.log(f"User (with image): {user_message}")

        # Debug: Show user message
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("user", f"{user_message}\n[Image: {image_path}]")

        # Show thinking in UI
        self.ui.show_thinking()

        # Create worker thread with image
        self.current_worker = AIWorker(self.ai, 'generate_with_image', user_message, image_path)
        self.current_worker.response_ready.connect(self.handle_ai_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def handle_ai_response(self, result):
        """Handle AI response from worker thread"""
        self.ui.hide_thinking()
        self.is_processing = False

        # Debug mode: show COMPLETE raw AI response with detailed parsing
        if self.settings.get('debug_mode'):
            raw_response = self.ai.last_raw_response

            if raw_response:
                self.ui.show_debug_message("ai", f"═══ RAW AI RESPONSE ═══\n{raw_response}")

                if 'command_name' in result:
                    self.ui.show_debug_message("system",
                                               f"═══ COMMAND DETECTED ═══\n"
                                               f"Command: {result['command_name']}\n\n"
                                               f"━━━ COMMAND INPUT (Python Code) ━━━\n"
                                               f"{result['command_input']}\n"
                                               f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                               f"Visible Text to User:\n{result.get('response', '(none)')}\n\n"
                                               f"⚡ Command will execute after voice (if voice mode)")

                elif 'tool_name' in result:
                    self.ui.show_debug_message("system",
                                               f"═══ TOOL CALL DETECTED ═══\n"
                                               f"Tool: {result['tool_name']}\n\n"
                                               f"━━━ TOOL INPUT ━━━\n"
                                               f"{result['tool_input']}\n"
                                               f"━━━━━━━━━━━━━━━━━\n\n"
                                               f"Visible Text to User:\n{result.get('response', '(none)')}\n\n"
                                               f"🔧 Tool will return output to AI")

        # NEW: Handle COMMAND in voice mode specially
        if 'command_name' in result and self.voice_mode_active:
            self.log("COMMAND in voice mode - will wait for voice then execute")

            # Show message and start voice
            if result.get('response') and result['response'].strip():
                self.ui.show_ai_message(result['response'])

            # Create a thread to wait for voice, then execute command
            def execute_after_voice():
                self.log("Waiting for voice to complete before command execution...")
                self.wait_for_voice_completion()
                self.log("Voice completed - executing command NOW")

                # Execute command (this was already done in ai_engine, but we'll trigger any side effects)
                # The command was already executed, so we just need to ensure message is shown
                # Actually, we already showed the message above, so we're done
                self.log("Command execution flow complete")

            import threading
            threading.Thread(target=execute_after_voice, daemon=True).start()
            return

        # Show visible text immediately (only if not empty) - NORMAL MODE or TOOL MODE
        if result.get('response') and result['response'].strip():
            self.ui.show_ai_message(result['response'])

        # NEW: Check if AI just exited tool mode
        if result.get('exited_tool_mode'):
            self.log("AI exited tool mode - sending post-exit prompt")
            # Show system message in chat FIRST
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message(
                    "🔄 **Tool Mode Completed** - AI is now preparing its report..."
                )
            # Send post-exit prompt with delay
            QTimer.singleShot(500, self.send_post_exit_prompt)
            return

        if result['thinking']:
            # Start tool mode timer
            self.tool_mode_timer.start()

    def send_post_exit_prompt(self):
        """NEW: Send post-exit prompt to guide AI to report findings"""
        if self.is_processing:
            self.log("Already processing - skipping post-exit prompt")
            return

        self.is_processing = True
        self.ui.show_thinking()

        # Debug: Show that we're sending post-exit prompt
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("system",
                "═══ SENDING POST-EXIT PROMPT ═══\n"
                "Asking AI to report its findings to the user...")

        # Create worker thread for post-exit prompt
        self.current_worker = AIWorker(self.ai, 'post_exit')
        self.current_worker.response_ready.connect(self.handle_post_exit_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def handle_post_exit_response(self, result):
        """Handle response after post-exit prompt"""
        self.ui.hide_thinking()
        self.is_processing = False

        # Debug mode
        if self.settings.get('debug_mode'):
            raw_response = self.ai.last_raw_response
            if raw_response:
                self.ui.show_debug_message("ai",
                    f"═══ AI's REPORT TO USER ═══\n{raw_response}")

        # Show the AI's report to user
        if result.get('response') and result['response'].strip():
            self.ui.show_ai_message(result['response'])

    def handle_tool_mode_response(self, result):
        """Handle tool mode response from worker thread"""
        self.is_processing = False

        # Debug mode: show everything that happened
        if self.settings.get('debug_mode'):
            # Show tool output first (if still in tool mode)
            if self.ai.tool_manager.in_tool_mode:
                tool_output = self.ai.tool_manager.last_tool_output
                if tool_output:
                    self.ui.show_debug_message("tool", f"═══ TOOL OUTPUT ═══\n{tool_output}")

            # Show the ORIGINAL raw AI response
            raw_response = self.ai.last_raw_response
            if raw_response:
                self.ui.show_debug_message("ai", f"═══ RAW AI RESPONSE (Tool Mode) ═══\n{raw_response}")

                # Show parsed info if available
                if 'tool_name' in result:
                    if result['tool_name'] == 'exit_from_tools':
                        self.ui.show_debug_message("system",
                            f"═══ EXITING TOOL MODE ═══\n"
                            f"AI's final message to user:\n{result.get('response', '(none)')}")
                    else:
                        self.ui.show_debug_message("system",
                            f"═══ NEXT TOOL CALL ═══\n"
                            f"Tool: {result['tool_name']}\n\n"
                            f"━━━ TOOL INPUT ━━━\n"
                            f"{result['tool_input']}\n"
                            f"━━━━━━━━━━━━━━━━━")

        # Update UI
        self.ui.handle_tool_mode_update(result)

        # NEW: Check if AI just exited tool mode
        if result.get('exited_tool_mode'):
            self.log("AI exited tool mode - sending post-exit prompt")
            self.tool_mode_timer.stop()
            # Show system message FIRST
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message(
                    "🔄 **Tool Mode Completed** - AI is now preparing its report..."
                )
            # Send post-exit prompt with delay
            QTimer.singleShot(500, self.send_post_exit_prompt)
            return

        if result['thinking']:
            # Still in tool mode - restart timer
            self.tool_mode_timer.start()
        else:
            # Done with tool mode
            self.tool_mode_timer.stop()

    def handle_ai_error(self, error_message):
        """Handle AI error from worker thread"""
        self.ui.hide_thinking()
        self.ui.show_ai_message(f"Error: {error_message}")
        self.tool_mode_timer.stop()
        self.is_processing = False
        self.log(f"AI Error: {error_message}", "ERROR")

        # Debug: Show error details
        if self.settings.get('debug_mode'):
            self.ui.show_debug_message("system", f"═══ ERROR ═══\n{error_message}")

    def auto_continue_tool_mode(self):
        """Automatically continue tool mode (non-blocking)"""
        if not self.ai.tool_manager.in_tool_mode:
            self.tool_mode_timer.stop()
            return

        # Stop timer first to prevent duplicate calls
        self.tool_mode_timer.stop()

        # Check if already processing
        if self.is_processing:
            self.log("Already processing - skipping tool mode continuation")
            return

        self.is_processing = True

        # Debug: Show tool mode prompt being sent
        if self.settings.get('debug_mode'):
            tool_prompt = self.ai.tool_manager.get_tool_mode_prompt()
            self.ui.show_debug_message("system", f"═══ CONTINUING TOOL MODE ═══\nSending to AI:\n{tool_prompt}")

        # Create worker thread
        self.current_worker = AIWorker(self.ai, 'continue_tool')
        self.current_worker.response_ready.connect(self.handle_tool_mode_response)
        self.current_worker.error_occurred.connect(self.handle_ai_error)
        self.current_worker.start()

    def interrupt_tool_mode(self):
        """Interrupt and cancel tool mode"""
        if self.ai.tool_manager.in_tool_mode:
            self.log("Tool mode interrupted by user")
            self.tool_mode_timer.stop()
            self.ai.tool_manager.in_tool_mode = False
            self.ai.tool_manager.last_tool_output = None
            self.is_processing = False

            # Notify UI
            if hasattr(self.ui, 'chat_window') and self.ui.chat_window:
                self.ui.chat_window.add_system_message("⚠️ **Tool operation canceled**")
                self.ui.chat_window.hide_thinking()
                self.ui.chat_window.set_input_enabled(True)

            return True
        return False

    def show(self):
        """Show the UI"""
        self.ui.show()

    def reset_python_interpreter(self):
        """Reset the Python interpreter"""
        self.ai.tool_manager.reset_python_interpreter()
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

    # ===== OpenRouter – OpenAI OSS =====
    {'id': 'openrouter:openai/gpt-oss-120b', 'name': 'GPT-OSS 120B', 'description': 'Open-source 120B model'},
    {'id': 'openrouter:openai/gpt-oss-120b:exacto', 'name': 'GPT-OSS 120B Exacto', 'description': 'Deterministic 120B variant'},
    {'id': 'openrouter:openai/gpt-oss-20b', 'name': 'GPT-OSS 20B', 'description': 'Open-source 20B model'},
    {'id': 'openrouter:openai/gpt-oss-20b:free', 'name': 'GPT-OSS 20B Free', 'description': 'Free tier 20B model'},
    {'id': 'openrouter:openai/gpt-oss-safeguard-20b', 'name': 'GPT-OSS Safeguard 20B', 'description': 'Safety-focused OSS model'},

    # ===== Codex / Coding =====
    {'id': 'openrouter:openai/codex-mini', 'name': 'Codex Mini', 'description': 'Fast lightweight coding model'},
    {'id': 'openrouter:openai/gpt-5-codex', 'name': 'GPT-5 Codex', 'description': 'Advanced GPT-5 coding'},
    {'id': 'openrouter:openai/gpt-5.1-codex', 'name': 'GPT-5.1 Codex', 'description': 'Stable GPT-5.1 coding'},
    {'id': 'openrouter:openai/gpt-5.1-codex-max', 'name': 'GPT-5.1 Codex Max', 'description': 'Maximum-power coding model'},
    {'id': 'openrouter:openai/gpt-5.1-codex-mini', 'name': 'GPT-5.1 Codex Mini', 'description': 'Efficient coding model'},
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
                'name': 'Gemma 3 27B 🚀',
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
