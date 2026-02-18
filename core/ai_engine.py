"""
AI Engine - Handles AI interactions
FIXED: Removed voice mode prompt logic, using single prompt for all modes
UPDATED: Added supervised execution support with code approval dialogs
UPDATED: Appends error traceback to execute_code failures
"""

import requests
from core.logger import _make_logger, _NoOpLogger
from core.tool_manager import ToolManager
from core.global_instructions import (
    get_system_prompt,
    POST_EXIT_PROMPT,
    POST_EXIT_PROMPT_VOICE,
    get_gemini_system_prompt
)


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("AIEngine") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class AIEngine:
    """AI conversation engine"""

    def __init__(self, log_callback=None, api_key='', puter_server=None, gemini_api_key='', system_info='',
                 voice_mode=False, elevenlabs_enabled=False, settings_callback=None):
        log.info("[AIEngine.__init__] ── Initializing AI Engine ──────────────────────────────")
        log.debug(f"[AIEngine.__init__] Parameters: voice_mode={voice_mode} | "
                  f"elevenlabs_enabled={elevenlabs_enabled} | "
                  f"has_api_key={bool(api_key)} | has_gemini_key={bool(gemini_api_key)} | "
                  f"has_puter_server={puter_server is not None} | "
                  f"has_settings_callback={settings_callback is not None}")

        self.log_callback = log_callback
        self.conversation_history = []
        log.debug("[AIEngine.__init__] conversation_history initialized (empty)")

        # Store these first so tool_manager can access them
        self.api_key = api_key
        self.gemini_api_key = gemini_api_key
        self.puter_server = puter_server
        log.debug("[AIEngine.__init__] API keys and puter_server reference stored")

        # Initialize tool_manager with settings callback and AI engine reference
        log.debug("[AIEngine.__init__] Creating ToolManager instance")
        self.tool_manager = ToolManager(
            settings_callback=settings_callback,
            ai_engine=self  # Pass self so dialog can call AI for explanations
        )
        log.info("[AIEngine.__init__] ToolManager created and attached")

        # LLaMA provider removed

        # Store system information
        self.system_info = system_info
        log.debug(f"[AIEngine.__init__] system_info stored | length={len(system_info)} chars")

        # Voice mode flags
        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled
        log.debug(f"[AIEngine.__init__] Voice flags set: voice_mode={voice_mode} | "
                  f"elevenlabs_enabled={elevenlabs_enabled}")

        # Generate system prompt with voice flags
        log.debug("[AIEngine.__init__] Generating system prompt via get_system_prompt()")
        self.system_prompt = get_system_prompt(system_info, voice_mode, elevenlabs_enabled)
        log.info(f"[AIEngine.__init__] System prompt generated | length={len(self.system_prompt)} chars")

        # Store last raw response
        self.last_raw_response = None

        # Provider settings
        self.ai_provider = 'anthropic'
        self.puter_model = 'gpt-4o-mini'
        self.gemini_model = 'gemini-2.0-flash-exp'

        self.tts_provider = 'edge-tts'  # Can be 'edge-tts' or 'puter'
        self.puter_tts_model = 'tts-1'
        self.puter_tts_voice = None
        self.puter_timeout = 30  # Default timeout in seconds

        # API endpoints
        self.anthropic_api_url = "https://api.anthropic.com/v1/messages"
        self.gemini_api_url_template = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        log.info(f"[AIEngine.__init__] ── Initialization complete | provider='{self.ai_provider}' | "
                 f"puter_model='{self.puter_model}' | gemini_model='{self.gemini_model}' | "
                 f"timeout={self.puter_timeout}s ──")

    def set_voice_mode(self, enabled):
        """Enable/disable voice mode flag"""
        log.info(f"[AIEngine.set_voice_mode] voice_mode → {enabled} | "
                 f"regenerating system prompt")
        self.voice_mode = enabled
        # Regenerate prompt
        self.system_prompt = get_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled)
        log.debug(f"[AIEngine.set_voice_mode] System prompt regenerated | "
                  f"length={len(self.system_prompt)} chars")
        if enabled:
            self.log("Voice mode ENABLED", "INFO")
        else:
            self.log("Voice mode DISABLED", "INFO")

    def update_voice_settings(self, voice_mode, elevenlabs_enabled):
        """Update voice mode settings and regenerate prompt"""
        log.info(f"[AIEngine.update_voice_settings] voice_mode={voice_mode} | "
                 f"elevenlabs_enabled={elevenlabs_enabled} | regenerating prompt")
        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled
        self.system_prompt = get_system_prompt(self.system_info, voice_mode, elevenlabs_enabled)
        log.debug(f"[AIEngine.update_voice_settings] Prompt regenerated | "
                  f"length={len(self.system_prompt)} chars")
        self.log(f"Voice settings updated: voice_mode={voice_mode}, elevenlabs={elevenlabs_enabled}")

    def _get_ai_response_internal(self, prompt):
        """Get AI response without adding to conversation history"""
        log.debug(f"[AIEngine._get_ai_response_internal] provider='{self.ai_provider}' | "
                  f"prompt_len={len(prompt)} chars")
        # This is similar to get_response but doesn't modify conversation_history
        if self.ai_provider == 'anthropic':
            return self._get_anthropic_response_internal(prompt)
        elif self.ai_provider == 'gemini':
            return self._get_gemini_response_internal(prompt)
        elif self.ai_provider == 'puter':
            return self._get_puter_response_internal(prompt)
        else:
            log.error(f"[AIEngine._get_ai_response_internal] Unknown provider: '{self.ai_provider}'")
            return "Error: Unknown provider"

    def _get_anthropic_response_internal(self, prompt):
        """Get Anthropic response for internal use (explanation)"""
        log.debug(f"[AIEngine._get_anthropic_response_internal] Sending internal Anthropic request | "
                  f"prompt_len={len(prompt)} chars")
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }

            data = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]
            }

            response = requests.post(url, headers=headers, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            reply = result['content'][0]['text']
            log.debug(f"[AIEngine._get_anthropic_response_internal] ✓ Response received | "
                      f"length={len(reply)} chars")
            return reply

        except Exception as e:
            log.error(f"[AIEngine._get_anthropic_response_internal] ✗ Failed: {type(e).__name__}: {e}")
            return f"Error getting explanation: {str(e)}"

    def _get_gemini_response_internal(self, prompt):
        """Get Gemini response for internal use (explanation)"""
        log.debug(f"[AIEngine._get_gemini_response_internal] Sending internal Gemini request | "
                  f"model='{self.gemini_model}' | prompt_len={len(prompt)} chars")
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"

            data = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }

            response = requests.post(url, json=data, timeout=30)
            response.raise_for_status()
            result = response.json()
            reply = result['candidates'][0]['content']['parts'][0]['text']
            log.debug(f"[AIEngine._get_gemini_response_internal] ✓ Response received | "
                      f"length={len(reply)} chars")
            return reply

        except Exception as e:
            log.error(f"[AIEngine._get_gemini_response_internal] ✗ Failed: {type(e).__name__}: {e}")
            return f"Error getting explanation: {str(e)}"

    def _get_puter_response_internal(self, prompt):
        """Get Puter response for internal use (explanation)"""
        log.debug(f"[AIEngine._get_puter_response_internal] Delegating to generate_response | "
                  f"prompt_len={len(prompt)} chars")
        try:
            response = self.generate_response(prompt)
            log.debug(f"[AIEngine._get_puter_response_internal] ✓ Got response")
            return response

        except Exception as e:
            log.error(f"[AIEngine._get_puter_response_internal] ✗ Failed: {type(e).__name__}: {e}")
            return f"Error getting explanation: {str(e)}"

    def set_api_key(self, api_key):
        log.info("[AIEngine.set_api_key] Anthropic API key updated")
        self.api_key = api_key
        self.log("Anthropic API key updated")

    def set_gemini_api_key(self, api_key):
        log.info("[AIEngine.set_gemini_api_key] Gemini API key updated")
        self.gemini_api_key = api_key
        self.log("Gemini API key updated")

    def set_provider(self, provider):
        log.info(f"[AIEngine.set_provider] AI provider changing: '{self.ai_provider}' → '{provider}'")
        self.ai_provider = provider
        self.log(f"AI provider set to: {provider}")

    def set_puter_model(self, model):
        log.info(f"[AIEngine.set_puter_model] Puter model: '{self.puter_model}' → '{model}'")
        self.puter_model = model
        self.log(f"Puter.js model set to: {model}")

    def set_gemini_model(self, model):
        log.info(f"[AIEngine.set_gemini_model] Gemini model: '{self.gemini_model}' → '{model}'")
        self.gemini_model = model
        self.log(f"Gemini model set to: {model}")

    def set_tts_provider(self, provider):
        log.info(f"[AIEngine.set_tts_provider] TTS provider: '{self.tts_provider}' → '{provider}'")
        self.tts_provider = provider
        self.log(f"TTS provider set to: {provider}")

    def set_puter_tts_model(self, model):
        log.info(f"[AIEngine.set_puter_tts_model] Puter TTS model: '{self.puter_tts_model}' → '{model}'")
        self.puter_tts_model = model
        self.log(f"Puter TTS model set to: {model}")

    def set_puter_tts_voice(self, voice):
        log.info(f"[AIEngine.set_puter_tts_voice] Puter TTS voice → '{voice}'")
        self.puter_tts_voice = voice
        self.log(f"Puter TTS voice set to: {voice}")

    def set_puter_timeout(self, timeout):
        """Set Puter.js server timeout in seconds"""
        log.info(f"[AIEngine.set_puter_timeout] Timeout: {self.puter_timeout}s → {timeout}s")
        self.puter_timeout = int(timeout)
        self.log(f"Puter timeout set to: {timeout} seconds")

    def log(self, message, level="INFO"):
        _level_fn = {
            "INFO": log.info, "ERROR": log.error,
            "WARNING": log.warning, "SUCCESS": log.info, "DEBUG": log.debug
        }.get(level, log.info)
        _level_fn(f"[AIEngine] {message}")
        if self.log_callback:
            self.log_callback(message, level)

    def generate_response(self, user_message):
        msg_preview = user_message[:80].replace('\n', '↵')
        log.info(f"[AIEngine.generate_response] ── New request | provider='{self.ai_provider}' | "
                 f"history_len={len(self.conversation_history)} | "
                 f"msg='{msg_preview}' ──")

        self.conversation_history.append({
            'role': 'user',
            'content': user_message
        })
        log.debug(f"[AIEngine.generate_response] User message appended to history | "
                  f"total history entries={len(self.conversation_history)}")

        if self.ai_provider == 'puter':
            log.debug("[AIEngine.generate_response] → Dispatching to _generate_puter_response()")
            return self._generate_puter_response()
        elif self.ai_provider == 'gemini':
            log.debug("[AIEngine.generate_response] → Dispatching to _generate_gemini_response()")
            return self._generate_gemini_response()
        else:
            log.debug("[AIEngine.generate_response] → Dispatching to _generate_anthropic_response()")
            return self._generate_anthropic_response()

    def generate_response_with_image(self, user_message, image_path):
        """Generate response with image attachment (Puter only)"""
        log.info(f"[AIEngine.generate_response_with_image] provider='{self.ai_provider}' | "
                 f"image='{image_path}' | msg_len={len(user_message)}")
        if self.ai_provider != 'puter':
            log.warning(f"[AIEngine.generate_response_with_image] Image attachment requires Puter, "
                        f"current provider='{self.ai_provider}' — returning error")
            return {
                'response': "Error: Image attachment only supported with Puter",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

        self.conversation_history.append({
            'role': 'user',
            'content': user_message
        })
        log.debug(f"[AIEngine.generate_response_with_image] User message appended | "
                  f"history_len={len(self.conversation_history)}")

        return self._generate_puter_response_with_image(image_path)

    def _generate_puter_response_with_image(self, image_path):
        """Generate Puter response with image"""
        log.info(f"[AIEngine._generate_puter_response_with_image] image='{image_path}'")
        try:
            if not self.puter_server:
                log.error("[AIEngine._generate_puter_response_with_image] puter_server is None")
                return {
                    'response': "Error: Puter server not initialized",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
                log.error("[AIEngine._generate_puter_response_with_image] puter_server.is_running=False")
                return {
                    'response': "Error: Puter server not running",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            # NEW FORMAT: Build messages array
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions."}
            ]

            # Add conversation history
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            log.debug(f"[AIEngine._generate_puter_response_with_image] Sending to Puter | "
                      f"model='{self.puter_model}' | messages={len(messages)} | "
                      f"timeout={self.puter_timeout}s | image='{image_path}'")

            # Send with image
            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                image=image_path,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                log.error("[AIEngine._generate_puter_response_with_image] ✗ No reply from Puter server")
                return {
                    'response': "Error: No response from Puter server",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            log.info(f"[AIEngine._generate_puter_response_with_image] ✓ Reply received | "
                     f"length={len(ai_reply)} chars | → _process_ai_response()")
            return self._process_ai_response(ai_reply)

        except Exception as e:
            log.error(f"[AIEngine._generate_puter_response_with_image] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _generate_puter_response(self):
        log.info(f"[AIEngine._generate_puter_response] Starting | model='{self.puter_model}' | "
                 f"history_len={len(self.conversation_history)} | timeout={self.puter_timeout}s")
        try:
            if not self.puter_server:
                log.error("[AIEngine._generate_puter_response] puter_server is None")
                return {
                    'response': "Error: Puter server not initialized",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
                log.error("[AIEngine._generate_puter_response] puter_server.is_running=False")
                return {
                    'response': "Error: Puter server not running",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            # NEW FORMAT: Build messages array with system prompt and history
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions."}
            ]

            # Add conversation history
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            log.debug(f"[AIEngine._generate_puter_response] Sending to Puter | "
                      f"total messages={len(messages)}")

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                log.error("[AIEngine._generate_puter_response] ✗ No reply from Puter server")
                return {
                    'response': "Error: No response from Puter server",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            log.info(f"[AIEngine._generate_puter_response] ✓ Reply received | "
                     f"length={len(ai_reply)} chars | → _process_ai_response()")
            return self._process_ai_response(ai_reply)

        except Exception as e:
            log.error(f"[AIEngine._generate_puter_response] ✗ Exception: {type(e).__name__}: {e}")
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _generate_gemini_response(self):
        log.info(f"[AIEngine._generate_gemini_response] Starting | model='{self.gemini_model}' | "
                 f"history_len={len(self.conversation_history)} | timeout={self.puter_timeout}s")
        try:
            if not self.gemini_api_key:
                log.error("[AIEngine._generate_gemini_response] gemini_api_key not set")
                return {
                    'response': "Error: Gemini API key not set",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            messages = self._build_gemini_messages()
            api_url = self.gemini_api_url_template.format(model=self.gemini_model)
            log.debug(f"[AIEngine._generate_gemini_response] Built {len(messages)} message(s) | "
                      f"url='{api_url}'")

            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.gemini_api_key
            }

            request_body = {
                "contents": messages
            }

            log.debug(f"[AIEngine._generate_gemini_response] POSTing to Gemini API...")
            response = requests.post(
                api_url,
                headers=headers,
                json=request_body,
                timeout=self.puter_timeout
            )

            log.debug(f"[AIEngine._generate_gemini_response] Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    candidate = data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        ai_text = ''.join([part.get('text', '') for part in parts])
                        log.info(f"[AIEngine._generate_gemini_response] ✓ Got response | "
                                 f"length={len(ai_text)} chars | → _process_ai_response()")
                        return self._process_ai_response(ai_text)

                log.warning("[AIEngine._generate_gemini_response] ✗ Unexpected response format — "
                            "no candidates or content parts found")
                return {
                    'response': "Error: Unexpected Gemini response format",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }
            else:
                error_msg = f"Gemini API Error: {response.status_code}"
                log.error(f"[AIEngine._generate_gemini_response] ✗ HTTP {response.status_code}")
                self.log(error_msg, "ERROR")
                return {
                    'response': f"Error: {error_msg}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Gemini Error: {e}"
            log.error(f"[AIEngine._generate_gemini_response] ✗ Exception: {type(e).__name__}: {e}")
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _generate_anthropic_response(self):
        log.info(f"[AIEngine._generate_anthropic_response] Starting | "
                 f"history_len={len(self.conversation_history)} | timeout={self.puter_timeout}s | "
                 f"has_api_key={bool(self.api_key)}")
        messages = self._build_messages()
        log.debug(f"[AIEngine._generate_anthropic_response] Built {len(messages)} message(s)")

        try:
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            }

            if self.api_key:
                headers["x-api-key"] = self.api_key

            log.debug(f"[AIEngine._generate_anthropic_response] POSTing to {self.anthropic_api_url}")
            response = requests.post(
                self.anthropic_api_url,
                headers=headers,
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": messages
                },
                timeout=self.puter_timeout
            )
            log.debug(f"[AIEngine._generate_anthropic_response] Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                ai_text = data['content'][0]['text']
                log.info(f"[AIEngine._generate_anthropic_response] ✓ Got response | "
                         f"length={len(ai_text)} chars | → _process_ai_response()")
                return self._process_ai_response(ai_text)
            else:
                error_msg = f"API Error: {response.status_code}"
                log.error(f"[AIEngine._generate_anthropic_response] ✗ HTTP {response.status_code}")
                self.log(error_msg, "ERROR")
                return {
                    'response': f"Error: {error_msg}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Error: {e}"
            log.error(f"[AIEngine._generate_anthropic_response] ✗ Exception: {type(e).__name__}: {e}")
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _process_ai_response(self, ai_text):
        """
        Process AI response and handle work_environment, execute_code, or set_session_name calls

        Returns:
            dict: Response data with execution status
        """
        log.info(f"[AIEngine._process_ai_response] ── Processing response | "
                 f"length={len(ai_text)} chars ──")
        log.debug(f"[AIEngine._process_ai_response] Preview: '{ai_text[:120].replace(chr(10), '↵')}'")
        self.last_raw_response = ai_text

        # Check for set_session_name call (handle first as it's simple)
        session_name = None
        log.debug("[AIEngine._process_ai_response] Checking for set_session_name call...")
        session_name_call = self.tool_manager.parse_set_session_name(ai_text)
        if session_name_call:
            session_name, remaining_text = session_name_call
            log.info(f"[AIEngine._process_ai_response] set_session_name detected → '{session_name}' | "
                     f"remaining text length={len(remaining_text)}")
            self.log(f"Set session name call detected: {session_name}")
            # Use the cleaned text for further processing
            ai_text = remaining_text

        # Check for work_environment call
        log.debug("[AIEngine._process_ai_response] Checking for work_environment call...")
        work_call = self.tool_manager.parse_work_environment(ai_text)
        if work_call:
            code, visible_text = work_call
            log.info(f"[AIEngine._process_ai_response] work_environment detected | "
                     f"code_len={len(code)} | visible_text_len={len(visible_text)}")
            log.debug(f"[AIEngine._process_ai_response] Code preview: '{code[:100].replace(chr(10), '↵')}'")
            self.log(f"Work environment call detected")

            # Execute code in work mode
            log.debug("[AIEngine._process_ai_response] → Calling tool_manager.run_work_environment()")
            work_output = self.tool_manager.run_work_environment(code)

            # Check if AI exited work mode
            if work_output == "EXITED_WORK_MODE":
                log.info("[AIEngine._process_ai_response] Work environment returned EXITED_WORK_MODE — "
                         "clearing work mode flag")
                self.tool_manager.in_work_mode = False

                # Add FULL AI TEXT to history for consistency
                if ai_text and ai_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': ai_text
                    })
                    log.debug("[AIEngine._process_ai_response] Full ai_text appended to history (exit path)")

                return {
                    'response': visible_text if visible_text.strip() else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True,
                    'session_name': session_name
                }

            # Store output for next iteration
            log.debug(f"[AIEngine._process_ai_response] Storing work output | "
                      f"length={len(work_output)} chars | in_work_mode → True")
            self.tool_manager.last_work_output = work_output
            self.tool_manager.in_work_mode = True

            # Add FULL AI TEXT to history (with JSON) so AI remembers tool usage
            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text  # Keep original with JSON for AI's memory!
            })
            log.debug("[AIEngine._process_ai_response] Full ai_text (with JSON) appended to history")

            return {
                'response': visible_text if visible_text else "Working...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'code': code,
                'session_name': session_name
            }

        # Check for execute_code call
        log.debug("[AIEngine._process_ai_response] Checking for execute_code call...")
        execute_call = self.tool_manager.parse_execute_code(ai_text)
        if execute_call:
            code, visible_text = execute_call
            log.info(f"[AIEngine._process_ai_response] execute_code detected | "
                     f"code_len={len(code)} | visible_text_len={len(visible_text)}")
            log.debug(f"[AIEngine._process_ai_response] Code preview: '{code[:100].replace(chr(10), '↵')}'")
            self.log(f"Execute code call detected")

            # Execute code (AI doesn't see output)
            log.debug("[AIEngine._process_ai_response] → Calling tool_manager.run_execute_code()")
            result = self.tool_manager.run_execute_code(code, self.log_callback)
            log.info(f"[AIEngine._process_ai_response] execute_code result: success={result['success']} | "
                     f"has_error={bool(result.get('error'))}")

            # Add FULL AI TEXT to history (with JSON) so AI remembers tool usage
            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text  # Keep original with JSON for AI's memory!
            })

            # If execution failed, append error traceback to visible text
            response_text = visible_text
            if not result['success'] and result.get('error'):
                # Append error block at the very bottom
                error_block = f"\n\n```Error\n{result['error']}\n```"
                response_text = response_text + error_block
                log.warning(f"[AIEngine._process_ai_response] Execution failed — error appended to response")

            return {
                'response': response_text,  # Return clean text (with error if failed) to display in chat
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'executed': True,
                'execution_success': result['success'],
                'session_name': session_name
            }

        # No execution calls - normal response
        log.info(f"[AIEngine._process_ai_response] No tool calls detected — normal text response | "
                 f"length={len(ai_text)} chars")
        self.conversation_history.append({
            'role': 'assistant',
            'content': ai_text
        })
        log.debug("[AIEngine._process_ai_response] Response appended to history | "
                  f"total history entries={len(self.conversation_history)}")

        return {
            'response': ai_text,
            'has_work_call': False,
            'in_work_mode': False,
            'thinking': False,
            'session_name': session_name
        }

    def continue_work_mode(self):
        """Continue work mode execution with AI analyzing previous output"""
        log.info(f"[AIEngine.continue_work_mode] in_work_mode={self.tool_manager.in_work_mode} | "
                 f"provider='{self.ai_provider}'")
        if not self.tool_manager.in_work_mode:
            log.warning("[AIEngine.continue_work_mode] Not in work mode — returning early")
            return {
                'response': "Not in work mode",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

            # Add work mode prompt to conversation
        work_prompt = self.tool_manager.get_work_mode_prompt()
        self.conversation_history.append({
            'role': 'system',
            'content': work_prompt
        })
        log.debug(f"[AIEngine.continue_work_mode] Work mode prompt appended | "
                  f"prompt_len={len(work_prompt)} | history_len={len(self.conversation_history)}")

        # Generate next response based on provider
        if self.ai_provider == 'puter':
            log.debug("[AIEngine.continue_work_mode] → _continue_work_mode_puter()")
            return self._continue_work_mode_puter()
        elif self.ai_provider == 'gemini':
            log.debug("[AIEngine.continue_work_mode] → _continue_work_mode_gemini()")
            return self._continue_work_mode_gemini()
        else:
            log.debug("[AIEngine.continue_work_mode] → _continue_work_mode_anthropic()")
            return self._continue_work_mode_anthropic()

    def send_post_exit_prompt(self):
        """Send post-exit prompt (same for all modes now)"""
        log.info(f"[AIEngine.send_post_exit_prompt] Sending post-exit prompt | "
                 f"voice_mode={self.voice_mode} | provider='{self.ai_provider}'")
        self.log("Sending post-exit prompt")
        if self.voice_mode:
            self.conversation_history.append({
                'role': 'system',
                'content': POST_EXIT_PROMPT_VOICE
            })
            log.debug("[AIEngine.send_post_exit_prompt] Using POST_EXIT_PROMPT_VOICE")
        else:
            self.conversation_history.append({
                'role': 'system',
                'content': POST_EXIT_PROMPT
            })
            log.debug("[AIEngine.send_post_exit_prompt] Using POST_EXIT_PROMPT")

        log.debug(f"[AIEngine.send_post_exit_prompt] History length after append: "
                  f"{len(self.conversation_history)}")

        if self.ai_provider == 'puter':
            return self._continue_work_mode_puter()
        elif self.ai_provider == 'gemini':
            return self._continue_work_mode_gemini()
        else:
            return self._continue_work_mode_anthropic()

    def _continue_work_mode_puter(self):
        log.info(f"[AIEngine._continue_work_mode_puter] model='{self.puter_model}' | "
                 f"history_len={len(self.conversation_history)}")
        try:
            # NEW FORMAT: Build full message history
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions."}
            ]

            # Add conversation history (includes the tool prompt)
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            log.debug(f"[AIEngine._continue_work_mode_puter] Sending {len(messages)} messages to Puter")

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                log.error("[AIEngine._continue_work_mode_puter] ✗ No reply from Puter server")
                return {
                    'response': "Error: No response",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            log.info(f"[AIEngine._continue_work_mode_puter] ✓ Reply received | "
                     f"length={len(ai_reply)} chars | → _process_work_mode_response()")
            return self._process_work_mode_response(ai_reply)

        except Exception as e:
            log.error(f"[AIEngine._continue_work_mode_puter] ✗ Exception: {type(e).__name__}: {e}")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _continue_work_mode_gemini(self):
        log.info(f"[AIEngine._continue_work_mode_gemini] model='{self.gemini_model}' | "
                 f"history_len={len(self.conversation_history)}")
        try:
            messages = self._build_gemini_messages()
            api_url = self.gemini_api_url_template.format(model=self.gemini_model)
            log.debug(f"[AIEngine._continue_work_mode_gemini] Built {len(messages)} messages | "
                      f"POSTing to Gemini API")

            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.gemini_api_key
            }

            request_body = {
                "contents": messages
            }

            response = requests.post(
                api_url,
                headers=headers,
                json=request_body,
                timeout=self.puter_timeout
            )
            log.debug(f"[AIEngine._continue_work_mode_gemini] Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    candidate = data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        ai_text = ''.join([part.get('text', '') for part in parts])
                        log.info(f"[AIEngine._continue_work_mode_gemini] ✓ Got response | "
                                 f"length={len(ai_text)} chars")
                        return self._process_work_mode_response(ai_text)

                log.warning("[AIEngine._continue_work_mode_gemini] ✗ Unexpected response format")
                return {
                    'response': "Error: Unexpected response format",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }
            else:
                log.error(f"[AIEngine._continue_work_mode_gemini] ✗ HTTP {response.status_code}")
                return {
                    'response': f"Gemini API Error: {response.status_code}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            log.error(f"[AIEngine._continue_work_mode_gemini] ✗ Exception: {type(e).__name__}: {e}")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _continue_work_mode_anthropic(self):
        log.info(f"[AIEngine._continue_work_mode_anthropic] history_len={len(self.conversation_history)}")
        messages = self._build_messages()
        log.debug(f"[AIEngine._continue_work_mode_anthropic] Built {len(messages)} messages | "
                  f"POSTing to Anthropic API")

        try:
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            }

            if self.api_key:
                headers["x-api-key"] = self.api_key

            response = requests.post(
                self.anthropic_api_url,
                headers=headers,
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 2000,
                    "messages": messages
                },
                timeout=self.puter_timeout
            )
            log.debug(f"[AIEngine._continue_work_mode_anthropic] Response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                ai_text = data['content'][0]['text']
                log.info(f"[AIEngine._continue_work_mode_anthropic] ✓ Got response | "
                         f"length={len(ai_text)} chars")
                return self._process_work_mode_response(ai_text)
            else:
                log.error(f"[AIEngine._continue_work_mode_anthropic] ✗ HTTP {response.status_code}")
                return {
                    'response': f"API Error: {response.status_code}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            log.error(f"[AIEngine._continue_work_mode_anthropic] ✗ Exception: {type(e).__name__}: {e}")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _process_work_mode_response(self, ai_text):
        """Process AI response while in work mode"""
        log.info(f"[AIEngine._process_work_mode_response] Processing work mode response | "
                 f"length={len(ai_text)} chars")
        log.debug(f"[AIEngine._process_work_mode_response] Preview: "
                  f"'{ai_text[:100].replace(chr(10), '↵')}'")
        self.last_raw_response = ai_text

        log.debug("[AIEngine._process_work_mode_response] Checking for consecutive work_environment call...")
        work_call = self.tool_manager.parse_work_environment(ai_text)

        if work_call:
            code, visible_text = work_call
            log.info(f"[AIEngine._process_work_mode_response] Consecutive work_environment call | "
                     f"code_len={len(code)} | visible_text_len={len(visible_text)}")
            self.log(f"Consecutive work environment call detected")

            log.debug("[AIEngine._process_work_mode_response] → run_work_environment()")
            work_output = self.tool_manager.run_work_environment(code)

            if work_output == "EXITED_WORK_MODE":
                log.info("[AIEngine._process_work_mode_response] Work environment exited — "
                         "clearing in_work_mode flag")
                self.tool_manager.in_work_mode = False

                # Add FULL AI TEXT to history for consistency
                if ai_text and ai_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': ai_text
                    })
                    log.debug("[AIEngine._process_work_mode_response] ai_text appended to history (exit)")

                return {
                    'response': visible_text if (visible_text and visible_text.strip()) else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True
                }

            log.debug(f"[AIEngine._process_work_mode_response] Work output received | "
                      f"output_len={len(work_output)} chars | storing for next iteration")
            self.tool_manager.last_work_output = work_output

            # Add FULL AI TEXT to history (with JSON) so AI remembers tool usage
            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text  # Keep original with JSON for AI's memory!
            })
            log.debug("[AIEngine._process_work_mode_response] ai_text (with JSON) appended to history")

            return {
                'response': visible_text if visible_text else "Working...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'code': code
            }

        else:
            log.info("[AIEngine._process_work_mode_response] No more work_environment calls — "
                     "AI is done, clearing in_work_mode")
            self.tool_manager.in_work_mode = False

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })
            log.debug("[AIEngine._process_work_mode_response] Normal response appended to history | "
                      f"total entries={len(self.conversation_history)}")

            return {
                'response': ai_text,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _build_messages(self):
        log.debug(f"[AIEngine._build_messages] Building Anthropic message list | "
                  f"history_len={len(self.conversation_history)}")
        messages = [
            {
                'role': 'system',
                'content': self.system_prompt
            },
            {
                'role': 'assistant',
                'content': 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'
            }
        ]

        messages.extend(self.conversation_history)
        log.debug(f"[AIEngine._build_messages] Built {len(messages)} total messages")
        return messages

    def _build_gemini_messages(self):
        log.debug(f"[AIEngine._build_gemini_messages] Building Gemini message list | "
                  f"history_len={len(self.conversation_history)}")
        messages = []

        # Use voice-aware prompt
        gemini_prompt = get_gemini_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled)
        log.debug(f"[AIEngine._build_gemini_messages] Gemini system prompt length={len(gemini_prompt)}")

        messages.append({
            "parts": [{"text": gemini_prompt}],
            "role": "system"
        })

        messages.append({
            "parts": [{"text": 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'}],
            "role": "model"
        })

        for msg in self.conversation_history:
            role = "model" if msg['role'] == 'assistant' else "user"
            messages.append({
                "parts": [{"text": msg['content']}],
                "role": role
            })

        log.debug(f"[AIEngine._build_gemini_messages] Built {len(messages)} total messages")
        return messages

    def clear_history(self):
        """Clear conversation history and reset work mode"""
        log.info(f"[AIEngine.clear_history] Clearing conversation history | "
                 f"was {len(self.conversation_history)} entries | "
                 f"in_work_mode={self.tool_manager.in_work_mode}")
        self.conversation_history = []
        self.tool_manager.in_work_mode = False
        self.tool_manager.last_work_output = None
        self.last_raw_response = None
        log.debug("[AIEngine.clear_history] ✓ History cleared, work mode reset, last_raw_response cleared")

    def remove_last_user_message(self):
        """Remove the last user message from conversation history"""
        log.debug(f"[AIEngine.remove_last_user_message] Scanning {len(self.conversation_history)} "
                  f"entries for last user message")
        # Find and remove the last user message
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i]['role'] == 'user':
                removed_msg = self.conversation_history.pop(i)
                log.info(f"[AIEngine.remove_last_user_message] ✓ Removed user message at index {i} | "
                         f"preview='{removed_msg['content'][:60].replace(chr(10), '↵')}'")
                self.log(f"Removed user message from history: {removed_msg['content'][:50]}...")
                return True
        log.warning("[AIEngine.remove_last_user_message] No user message found in history")
        return False

    def reset_python_interpreter(self):
        """Reset the Python interpreter state"""
        log.info("[AIEngine.reset_python_interpreter] Resetting Python interpreter via tool_manager")
        self.tool_manager.reset_python()
        self.log("Python interpreter reset")
        log.info("[AIEngine.reset_python_interpreter] ✓ Python interpreter reset complete")