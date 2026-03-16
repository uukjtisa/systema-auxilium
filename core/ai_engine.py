"""
AI Engine - Handles AI interactions
REFACTORED: Provider-specific generate/continue_work_mode methods merged into
            unified _call_provider() + thin HTTP helpers (_http_anthropic,
            _http_gemini, _http_puter).  All callers (generate_response,
            generate_response_with_image, continue_work_mode,
            send_post_exit_prompt) now share one code-path.
"""

import requests
from core.logger import _make_logger, _NoOpLogger
from core.tool_manager import ToolManager
from core.memory_manager import get_memory_manager
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
                 voice_mode=False, elevenlabs_enabled=False, settings_callback=None, skill_manager=None):
        log.info("[AIEngine.__init__] ── Initializing AI Engine ──────────────────────────────")
        log.debug(f"[AIEngine.__init__] Parameters: voice_mode={voice_mode} | "
                  f"elevenlabs_enabled={elevenlabs_enabled} | "
                  f"has_api_key={bool(api_key)} | has_gemini_key={bool(gemini_api_key)} | "
                  f"has_puter_server={puter_server is not None} | "
                  f"has_settings_callback={settings_callback is not None}")

        self.log_callback = log_callback
        self.settings_callback = settings_callback
        self.conversation_history = []
        log.debug("[AIEngine.__init__] conversation_history initialized (empty)")

        self.api_key = api_key
        self.gemini_api_key = gemini_api_key
        self.puter_server = puter_server
        log.debug("[AIEngine.__init__] API keys and puter_server reference stored")

        log.debug("[AIEngine.__init__] Creating ToolManager instance")
        self.tool_manager = ToolManager(
            settings_callback=settings_callback,
            ai_engine=self
        )
        log.info("[AIEngine.__init__] ToolManager created and attached")

        self.memory_manager = get_memory_manager()
        self._pending_memory_context = ""
        log.info(f"[AIEngine.__init__] MemoryManager attached | ready={self.memory_manager.is_ready}")

        self.system_info = system_info
        log.debug(f"[AIEngine.__init__] system_info stored | length={len(system_info)} chars")

        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled
        log.debug(f"[AIEngine.__init__] Voice flags set: voice_mode={voice_mode} | "
                  f"elevenlabs_enabled={elevenlabs_enabled}")

        self.skill_manager = skill_manager
        if skill_manager:
            skill_manager.skills_changed.connect(self._on_skills_changed)
            skill_manager.loaded_skills_changed.connect(self._on_skills_changed)
            log.info("[AIEngine.__init__] SkillManager wired — skills_changed + loaded_skills_changed connected")
        self.system_prompt = get_system_prompt(
            system_info, voice_mode, elevenlabs_enabled,
            skills=skill_manager.get_skills() if skill_manager else []
        )
        log.info(f"[AIEngine.__init__] System prompt generated | length={len(self.system_prompt)} chars")

        self.last_raw_response = None

        # Provider settings
        self.ai_provider = 'anthropic'
        self.puter_model = 'gpt-4o-mini'
        self.gemini_model = 'gemini-2.0-flash-exp'

        self.tts_provider = 'edge-tts'
        self.puter_tts_model = 'tts-1'
        self.puter_tts_voice = None
        self.puter_timeout = 30

        # API endpoints
        self.anthropic_api_url = "https://api.anthropic.com/v1/messages"
        self.gemini_api_url_template = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

        log.info(f"[AIEngine.__init__] ── Initialization complete | provider='{self.ai_provider}' | "
                 f"puter_model='{self.puter_model}' | gemini_model='{self.gemini_model}' | "
                 f"timeout={self.puter_timeout}s ──")

    # ═══════════════════════════════════════════════════════════════════════════
    # VOICE / VOICE-SETTINGS METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def set_voice_mode(self, enabled):
        """Enable/disable voice mode flag"""
        log.info(f"[AIEngine.set_voice_mode] voice_mode → {enabled} | regenerating system prompt")
        self.voice_mode = enabled
        self.system_prompt = get_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled,
                                               skills=self.skill_manager.get_skills() if self.skill_manager else [])
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
        self.system_prompt = get_system_prompt(self.system_info, voice_mode, elevenlabs_enabled,
                                               skills=self.skill_manager.get_skills() if self.skill_manager else [])
        log.debug(f"[AIEngine.update_voice_settings] Prompt regenerated | "
                  f"length={len(self.system_prompt)} chars")
        self.log(f"Voice settings updated: voice_mode={voice_mode}, elevenlabs={elevenlabs_enabled}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SKILL METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_skills_changed(self):
        """Regenerate system prompt when the skills folder changes."""
        log.info("[AIEngine._on_skills_changed] Skills changed — regenerating system prompt")
        self.system_prompt = get_system_prompt(
            self.system_info,
            self.voice_mode,
            self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else []
        )
        log.info(f"[AIEngine._on_skills_changed] System prompt regenerated | "
                 f"length={len(self.system_prompt)} chars")

    def _render_active_skills(self) -> str:
        """Build the skill injection block appended to the system prompt."""
        active = self.skill_manager.get_loaded_skills() if self.skill_manager else {}
        if not active:
            return ""
        lines = [
            "",
            "═══════════════════════════════════════════════════════════",
            "LOADED SKILLS (currently active)",
            "═══════════════════════════════════════════════════════════",
        ]
        for name, content in active.items():
            lines.append(f"\n── SKILL: {name} ──────────────────────────────────────")
            lines.append(content.strip())
        lines.append("═══════════════════════════════════════════════════════════")
        return "\n".join(lines)

    def _get_effective_system_prompt(self) -> str:
        """Return the system prompt enriched with any currently active skills."""
        return self.system_prompt + self._render_active_skills()

    # ═══════════════════════════════════════════════════════════════════════════
    # PROVIDER SETTERS
    # ═══════════════════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════════════════
    # MESSAGE BUILDERS  (one per provider format)
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_messages(self):
        """Build Anthropic-format message list from conversation history."""
        log.debug(f"[AIEngine._build_messages] Building Anthropic message list | "
                  f"history_len={len(self.conversation_history)}")
        messages = [
            {'role': 'system', 'content': self._get_effective_system_prompt()},
            {'role': 'assistant',
             'content': 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'}
        ]

        history_copy = list(self.conversation_history)
        if self._pending_memory_context and history_copy:
            insert_at = None
            for i in range(len(history_copy) - 1, -1, -1):
                if history_copy[i]['role'] == 'user':
                    insert_at = i
                    break
            if insert_at is not None:
                history_copy.insert(insert_at, {
                    'role': 'system',
                    'content': self._pending_memory_context
                })

        messages.extend(history_copy)
        log.debug(f"[AIEngine._build_messages] Built {len(messages)} total messages")
        return messages

    def _build_gemini_messages(self):
        """Build Gemini-format message list from conversation history."""
        log.debug(f"[AIEngine._build_gemini_messages] Building Gemini message list | "
                  f"history_len={len(self.conversation_history)}")
        messages = []

        gemini_prompt = get_gemini_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled)
        active_skills_block = self._render_active_skills()
        if active_skills_block:
            gemini_prompt = gemini_prompt + active_skills_block

        messages.append({
            "parts": [{"text": gemini_prompt}],
            "role": "system"
        })
        messages.append({
            "parts": [{"text": 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'}],
            "role": "model"
        })

        history_copy = list(self.conversation_history)
        if self._pending_memory_context and history_copy:
            insert_at = None
            for i in range(len(history_copy) - 1, -1, -1):
                if history_copy[i]['role'] == 'user':
                    insert_at = i
                    break
            if insert_at is not None:
                history_copy.insert(insert_at, {
                    'role': 'system',
                    'content': self._pending_memory_context
                })

        for msg in history_copy:
            role = "model" if msg['role'] == 'assistant' else "user"
            messages.append({
                "parts": [{"text": msg['content']}],
                "role": role
            })

        log.debug(f"[AIEngine._build_gemini_messages] Built {len(messages)} total messages")
        return messages

    def _build_puter_messages(self):
        """Build Puter.js-format message list from conversation history."""
        log.debug(f"[AIEngine._build_puter_messages] Building Puter message list | "
                  f"history_len={len(self.conversation_history)}")
        messages = [
            {"role": "system", "content": self._get_effective_system_prompt()},
            {"role": "assistant",
             "content": "I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions."}
        ]

        history_copy = list(self.conversation_history)
        if self._pending_memory_context and history_copy:
            insert_at = None
            for i in range(len(history_copy) - 1, -1, -1):
                if history_copy[i]['role'] == 'user':
                    insert_at = i
                    break
            if insert_at is not None:
                history_copy.insert(insert_at, {
                    'role': 'system',
                    'content': self._pending_memory_context
                })

        for msg in history_copy:
            messages.append({"role": msg['role'], "content": msg['content']})

        log.debug(f"[AIEngine._build_puter_messages] Built {len(messages)} total messages")
        return messages

    # ═══════════════════════════════════════════════════════════════════════════
    # THIN HTTP HELPERS  (one per provider)
    # ═══════════════════════════════════════════════════════════════════════════

    def _http_anthropic(self, messages) -> str | None:
        """Make an Anthropic API call. Returns raw text or None on failure."""
        log.debug(f"[AIEngine._http_anthropic] Sending request | messages={len(messages)} | "
                  f"timeout={self.puter_timeout}s")
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
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 2000, "messages": messages},
                timeout=self.puter_timeout
            )
            log.debug(f"[AIEngine._http_anthropic] Status: {response.status_code}")

            if response.status_code == 200:
                text = response.json()['content'][0]['text']
                log.info(f"[AIEngine._http_anthropic] ✓ Response received | length={len(text)} chars")
                return text
            else:
                log.error(f"[AIEngine._http_anthropic] ✗ HTTP {response.status_code}")
                self.log(f"API Error: {response.status_code}", "ERROR")
                return None

        except Exception as e:
            log.error(f"[AIEngine._http_anthropic] ✗ {type(e).__name__}: {e}")
            self.log(f"Error: {e}", "ERROR")
            return None

    def _http_gemini(self, messages) -> str | None:
        """Make a Gemini API call. Returns raw text or None on failure."""
        log.debug(f"[AIEngine._http_gemini] Sending request | model='{self.gemini_model}' | "
                  f"messages={len(messages)} | timeout={self.puter_timeout}s")
        try:
            if not self.gemini_api_key:
                log.error("[AIEngine._http_gemini] ✗ No Gemini API key")
                return None

            api_url = self.gemini_api_url_template.format(model=self.gemini_model)
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.gemini_api_key
            }

            response = requests.post(
                api_url,
                headers=headers,
                json={"contents": messages},
                timeout=self.puter_timeout
            )
            log.debug(f"[AIEngine._http_gemini] Status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    parts = data['candidates'][0]['content']['parts']
                    text = ''.join(part.get('text', '') for part in parts)
                    log.info(f"[AIEngine._http_gemini] ✓ Response received | length={len(text)} chars")
                    return text
                log.warning("[AIEngine._http_gemini] ✗ Unexpected response format")
                return None
            else:
                log.error(f"[AIEngine._http_gemini] ✗ HTTP {response.status_code}")
                self.log(f"Gemini API Error: {response.status_code}", "ERROR")
                return None

        except Exception as e:
            log.error(f"[AIEngine._http_gemini] ✗ {type(e).__name__}: {e}")
            self.log(f"Gemini Error: {e}", "ERROR")
            return None

    def _http_puter(self, messages, image=None) -> str | None:
        """Make a Puter.js API call. Returns raw text or None on failure.
        The puter_server.send_chat_request handles image upload internally."""
        log.debug(f"[AIEngine._http_puter] Sending request | model='{self.puter_model}' | "
                  f"messages={len(messages)} | has_image={image is not None} | timeout={self.puter_timeout}s")
        try:
            if not self.puter_server:
                log.error("[AIEngine._http_puter] ✗ puter_server is None")
                return None
            if not self.puter_server.is_running:
                log.error("[AIEngine._http_puter] ✗ puter_server not running")
                return None

            result = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                image=image,
                timeout=self.puter_timeout
            )

            if result:
                log.info(f"[AIEngine._http_puter] ✓ Response received | length={len(result)} chars")
            else:
                log.error("[AIEngine._http_puter] ✗ No response from Puter server")

            return result

        except Exception as e:
            log.error(f"[AIEngine._http_puter] ✗ {type(e).__name__}: {e}")
            self.log(f"Puter error: {e}", "ERROR")
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # UNIFIED PROVIDER DISPATCHER
    # ═══════════════════════════════════════════════════════════════════════════

    def _call_provider(self, image=None) -> str | None:
        """Build messages for the active provider and make the API call.
        Returns raw AI text, or None on failure.
        This is the single place that knows about provider differences."""
        log.debug(f"[AIEngine._call_provider] provider='{self.ai_provider}' | has_image={image is not None}")
        if self.ai_provider == 'anthropic':
            return self._http_anthropic(self._build_messages())
        elif self.ai_provider == 'gemini':
            return self._http_gemini(self._build_gemini_messages())
        elif self.ai_provider == 'puter':
            return self._http_puter(self._build_puter_messages(), image=image)
        else:
            log.error(f"[AIEngine._call_provider] Unknown provider: '{self.ai_provider}'")
            return None

    def _make_error_result(self, message="Error: No response from AI") -> dict:
        """Return a standard error result dict."""
        return {
            'response': message,
            'has_work_call': False,
            'in_work_mode': False,
            'thinking': False
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # PUBLIC GENERATION METHODS  (now provider-agnostic)
    # ═══════════════════════════════════════════════════════════════════════════

    def generate_response(self, user_message):
        """Generate a response to a user message."""
        msg_preview = user_message[:80].replace('\n', '↵')
        log.info(f"[AIEngine.generate_response] ── New request | provider='{self.ai_provider}' | "
                 f"history_len={len(self.conversation_history)} | msg='{msg_preview}' ──")

        self._inject_memories(user_message)
        self.conversation_history.append({'role': 'user', 'content': user_message})
        log.debug(f"[AIEngine.generate_response] User message appended | "
                  f"total history entries={len(self.conversation_history)}")

        ai_text = self._call_provider()
        if not ai_text:
            log.error("[AIEngine.generate_response] ✗ No AI text returned")
            return self._make_error_result(f"Error: No response from {self.ai_provider} provider")

        log.info(f"[AIEngine.generate_response] ✓ Got response | length={len(ai_text)} chars | "
                 f"→ _process_ai_response()")
        return self._process_ai_response(ai_text)

    def generate_response_with_image(self, user_message, image_path):
        """Generate response with image attachment (Puter only)."""
        log.info(f"[AIEngine.generate_response_with_image] provider='{self.ai_provider}' | "
                 f"image='{image_path}' | msg_len={len(user_message)}")
        if self.ai_provider != 'puter':
            log.warning(f"[AIEngine.generate_response_with_image] Image attachment requires Puter, "
                        f"current provider='{self.ai_provider}' — returning error")
            return self._make_error_result("Error: Image attachment only supported with Puter")

        self.conversation_history.append({'role': 'user', 'content': user_message})
        log.debug(f"[AIEngine.generate_response_with_image] User message appended | "
                  f"history_len={len(self.conversation_history)}")

        ai_text = self._call_provider(image=image_path)
        if not ai_text:
            log.error("[AIEngine.generate_response_with_image] ✗ No AI text returned")
            return self._make_error_result("Error: No response from Puter server")

        log.info(f"[AIEngine.generate_response_with_image] ✓ Got response | "
                 f"length={len(ai_text)} chars | → _process_ai_response()")
        return self._process_ai_response(ai_text)

    def continue_work_mode(self):
        """Continue work mode execution with AI analyzing previous output."""
        log.info(f"[AIEngine.continue_work_mode] in_work_mode={self.tool_manager.in_work_mode} | "
                 f"provider='{self.ai_provider}'")
        if not self.tool_manager.in_work_mode:
            log.warning("[AIEngine.continue_work_mode] Not in work mode — returning early")
            return self._make_error_result("Not in work mode")

        work_prompt = self.tool_manager.get_work_mode_prompt()
        self.conversation_history.append({'role': 'system', 'content': work_prompt})
        log.debug(f"[AIEngine.continue_work_mode] Work mode prompt appended | "
                  f"prompt_len={len(work_prompt)} | history_len={len(self.conversation_history)}")

        ai_text = self._call_provider()
        if not ai_text:
            log.error("[AIEngine.continue_work_mode] ✗ No AI text returned")
            return self._make_error_result(f"Error: No response from {self.ai_provider} provider")

        log.info(f"[AIEngine.continue_work_mode] ✓ Got response | length={len(ai_text)} chars | "
                 f"→ _process_work_mode_response()")
        return self._process_work_mode_response(ai_text)

    def send_post_exit_prompt(self):
        """Send post-exit prompt to guide AI to report findings."""
        log.info(f"[AIEngine.send_post_exit_prompt] voice_mode={self.voice_mode} | "
                 f"provider='{self.ai_provider}'")
        self.log("Sending post-exit prompt")

        prompt = POST_EXIT_PROMPT_VOICE if self.voice_mode else POST_EXIT_PROMPT
        self.conversation_history.append({'role': 'system', 'content': prompt})
        log.debug(f"[AIEngine.send_post_exit_prompt] Using "
                  f"{'POST_EXIT_PROMPT_VOICE' if self.voice_mode else 'POST_EXIT_PROMPT'} | "
                  f"history_len={len(self.conversation_history)}")

        ai_text = self._call_provider()
        if not ai_text:
            log.error("[AIEngine.send_post_exit_prompt] ✗ No AI text returned")
            return self._make_error_result(f"Error: No response from {self.ai_provider} provider")

        log.info(f"[AIEngine.send_post_exit_prompt] ✓ Got response | length={len(ai_text)} chars | "
                 f"→ _process_ai_response()")
        return self._process_ai_response(ai_text)

    def _get_ai_response_internal(self, prompt):
        """Get a single-turn AI response without touching conversation history.
        Used for code explanation dialogs, approval dialogs, etc."""
        log.debug(f"[AIEngine._get_ai_response_internal] provider='{self.ai_provider}' | "
                  f"prompt_len={len(prompt)} chars")
        try:
            if self.ai_provider == 'anthropic':
                result = self._http_anthropic([{'role': 'user', 'content': prompt}])
            elif self.ai_provider == 'gemini':
                result = self._http_gemini([{"parts": [{"text": prompt}], "role": "user"}])
            elif self.ai_provider == 'puter':
                result = self._http_puter([{"role": "user", "content": prompt}])
            else:
                log.error(f"[AIEngine._get_ai_response_internal] Unknown provider: '{self.ai_provider}'")
                return f"Error: Unknown provider '{self.ai_provider}'"

            if result:
                log.debug(f"[AIEngine._get_ai_response_internal] ✓ Got response | length={len(result)} chars")
                return result
            else:
                log.error("[AIEngine._get_ai_response_internal] ✗ No response returned")
                return "Error getting explanation: no response from provider"

        except Exception as e:
            log.error(f"[AIEngine._get_ai_response_internal] ✗ {type(e).__name__}: {e}")
            return f"Error getting explanation: {str(e)}"

    # ═══════════════════════════════════════════════════════════════════════════
    # RESPONSE PROCESSING  (unchanged — already unified)
    # ═══════════════════════════════════════════════════════════════════════════

    def _process_ai_response(self, ai_text):
        """
        Process AI response and handle work_environment, execute_code, or set_session_name calls.

        Returns:
            dict: Response data with execution status
        """
        log.info(f"[AIEngine._process_ai_response] ── Processing response | "
                 f"length={len(ai_text)} chars ──")
        log.debug(f"[AIEngine._process_ai_response] Preview: '{ai_text[:120].replace(chr(10), '↵')}'")
        self._clear_memory_context()
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
            ai_text = remaining_text

        memorize_result = self.tool_manager.parse_memorize(ai_text)
        if memorize_result:
            memory_text, ai_text = memorize_result
            self.memory_manager.memorize(memory_text)
            log.info(f"[AIEngine] Memory stored: '{memory_text[:60]}'")

        # ── Check for load_skill call (now valid anywhere) ────────────────────
        load_skill_result = self.tool_manager.parse_load_skill(ai_text)
        if load_skill_result:
            from core.global_instructions import SKILL_LOADED_CHAT_PROMPT, SKILL_ALREADY_LOADED_PROMPT
            skill_name, remaining_text = load_skill_result
            log.info(f"[AIEngine._process_ai_response] load_skill detected (outside work) → '{skill_name}'")
            if self.skill_manager:
                success, msg = self.skill_manager.load_skill(skill_name)
            else:
                success, msg = False, "ERROR: No skill manager"
            if not success:
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({
                    'role': 'system',
                    'content': SKILL_ALREADY_LOADED_PROMPT.format(skill_name=skill_name, reason=msg)
                })
            else:
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({
                    'role': 'system',
                    'content': SKILL_LOADED_CHAT_PROMPT.format(skill_name=skill_name)
                })
            return {
                'response': remaining_text.strip() if remaining_text and remaining_text.strip()
                            else (f"✅ Skill '{skill_name}' loaded." if success else f"⚠ {msg}"),
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'skill_loaded': skill_name if success else None,
            }

        # ── Check for unload_skill call (valid anywhere) ──────────────────────
        unload_skill_result = self.tool_manager.parse_unload_skill(ai_text)
        if unload_skill_result:
            from core.global_instructions import SKILL_UNLOADED_CHAT_PROMPT, SKILL_NOT_LOADED_PROMPT
            skill_name, remaining_text = unload_skill_result
            log.info(f"[AIEngine._process_ai_response] unload_skill detected (outside work) → '{skill_name}'")
            if self.skill_manager:
                success, msg = self.skill_manager.unload_skill(skill_name)
            else:
                success, msg = False, "ERROR: No skill manager"
            if not success:
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({
                    'role': 'system',
                    'content': SKILL_NOT_LOADED_PROMPT.format(skill_name=skill_name, reason=msg)
                })
            else:
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({
                    'role': 'system',
                    'content': SKILL_UNLOADED_CHAT_PROMPT.format(skill_name=skill_name)
                })
            return {
                'response': remaining_text.strip() if remaining_text and remaining_text.strip()
                            else (f"🗑 Skill '{skill_name}' unloaded." if success else f"⚠ {msg}"),
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'skill_unloaded': skill_name if success else None,
            }

        # Check for work_environment call
        log.debug("[AIEngine._process_ai_response] Checking for work_environment call...")
        work_call = self.tool_manager.parse_work_environment(ai_text)
        if work_call:
            code, visible_text = work_call
            log.info(f"[AIEngine._process_ai_response] work_environment detected | "
                     f"code_len={len(code)} | visible_text_len={len(visible_text)}")
            self.log(f"Work environment call detected")

            log.debug("[AIEngine._process_ai_response] → Calling tool_manager.run_work_environment()")
            work_output = self.tool_manager.run_work_environment(code)

            if work_output == "EXITED_WORK_MODE":
                log.info("[AIEngine._process_ai_response] Work environment returned EXITED_WORK_MODE — "
                         "clearing work mode flag (skills persist)")
                self.tool_manager.in_work_mode = False

                if ai_text and ai_text.strip():
                    self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                    log.debug("[AIEngine._process_ai_response] Full ai_text appended to history (exit path)")

                return {
                    'response': visible_text if visible_text.strip() else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True,
                    'session_name': session_name
                }

            log.debug(f"[AIEngine._process_ai_response] Storing work output | "
                      f"length={len(work_output)} chars | in_work_mode → True")
            self.tool_manager.last_work_output = work_output
            self.tool_manager.in_work_mode = True

            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
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
            self.log(f"Execute code call detected")

            log.debug("[AIEngine._process_ai_response] → Calling tool_manager.run_execute_code()")
            result = self.tool_manager.run_execute_code(code, self.log_callback)
            log.info(f"[AIEngine._process_ai_response] execute_code result: success={result['success']} | "
                     f"has_error={bool(result.get('error'))}")

            self.conversation_history.append({'role': 'assistant', 'content': ai_text})

            response_text = visible_text
            if not result['success'] and result.get('error'):
                error_block = f"\n\n```Error\n{result['error']}\n```"
                response_text = response_text + error_block
                log.warning(f"[AIEngine._process_ai_response] Execution failed — error appended to response")

            return {
                'response': response_text,
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
        self.conversation_history.append({'role': 'assistant', 'content': ai_text})
        log.debug("assistant response appended to history | "
                  f"total history entries={len(self.conversation_history)}")

        # Safety net: strip any remaining tool JSON from display text
        display_text = self.tool_manager.strip_tool_calls(ai_text)
        if display_text != ai_text:
            log.warning(f"[AIEngine._process_ai_response] Safety net stripped residual tool JSON | "
                        f"before={len(ai_text)} → after={len(display_text)} chars")
            if not session_name:
                missed_call = self.tool_manager.parse_set_session_name(ai_text)
                if missed_call:
                    session_name = missed_call[0]
                    log.info(f"[AIEngine._process_ai_response] Safety net recovered session_name: "
                             f"'{session_name}'")

        return {
            'response': display_text,
            'has_work_call': False,
            'in_work_mode': False,
            'thinking': False,
            'session_name': session_name
        }

    def _process_work_mode_response(self, ai_text):
        """Process AI response while in work mode."""
        log.info(f"[AIEngine._process_work_mode_response] Processing work mode response | "
                 f"length={len(ai_text)} chars")
        log.debug(f"[AIEngine._process_work_mode_response] Preview: "
                  f"'{ai_text[:100].replace(chr(10), '↵')}'")
        self.last_raw_response = ai_text

        log.debug("[AIEngine._process_work_mode_response] Checking for consecutive work_environment call...")

        # ── Check for load_skill call (work_environment exclusive) ────────────
        load_skill_result = self.tool_manager.parse_load_skill(ai_text)
        if load_skill_result:
            from core.global_instructions import SKILL_LOADED_WORK_PROMPT, SKILL_ALREADY_LOADED_PROMPT
            skill_name, remaining_text = load_skill_result
            log.info(f"[AIEngine._process_work_mode_response] load_skill detected → '{skill_name}'")

            if self.skill_manager:
                success, msg = self.skill_manager.load_skill(skill_name)
            else:
                success, msg = False, f"ERROR: No skill manager — cannot load '{skill_name}'"

            if not success:
                log.warning(f"[AIEngine._process_work_mode_response] load_skill failed: {msg}")
                already_msg = SKILL_ALREADY_LOADED_PROMPT.format(skill_name=skill_name, reason=msg)
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({'role': 'system', 'content': already_msg})
                return {
                    'response': f"⚠ {msg}",
                    'has_work_call': True,
                    'in_work_mode': True,
                    'thinking': True,
                }

            log.info(f"[AIEngine._process_work_mode_response] Skill '{skill_name}' loaded | "
                     f"total loaded: {list(self.skill_manager.get_loaded_skills().keys())}")

            if remaining_text and remaining_text.strip():
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})

            work_output = self.tool_manager.last_work_output or "No previous output"
            skill_loaded_msg = SKILL_LOADED_WORK_PROMPT.format(
                skill_name=skill_name,
                work_output=work_output
            )
            self.conversation_history.append({'role': 'system', 'content': skill_loaded_msg})
            log.debug("[AIEngine._process_work_mode_response] Skill-loaded work prompt appended to history")

            return {
                'response': f"Loading skill: {skill_name}...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'skill_loaded': skill_name,
            }

        # ── Check for unload_skill call ────────────────────────────────────────
        unload_skill_result = self.tool_manager.parse_unload_skill(ai_text)
        if unload_skill_result:
            from core.global_instructions import SKILL_UNLOADED_WORK_PROMPT, SKILL_NOT_LOADED_PROMPT
            skill_name, remaining_text = unload_skill_result
            log.info(f"[AIEngine._process_work_mode_response] unload_skill detected → '{skill_name}'")

            if self.skill_manager:
                success, msg = self.skill_manager.unload_skill(skill_name)
            else:
                success, msg = False, "ERROR: No skill manager"

            if not success:
                log.warning(f"[AIEngine._process_work_mode_response] unload_skill failed: {msg}")
                not_loaded_msg = SKILL_NOT_LOADED_PROMPT.format(skill_name=skill_name, reason=msg)
                self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                self.conversation_history.append({'role': 'system', 'content': not_loaded_msg})
                return {
                    'response': f"⚠ {msg}",
                    'has_work_call': True,
                    'in_work_mode': True,
                    'thinking': True,
                }

            log.info(f"[AIEngine._process_work_mode_response] Skill '{skill_name}' unloaded")
            work_output = self.tool_manager.last_work_output or "No previous output"
            unloaded_msg = SKILL_UNLOADED_WORK_PROMPT.format(
                skill_name=skill_name,
                work_output=work_output
            )
            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
            self.conversation_history.append({'role': 'system', 'content': unloaded_msg})
            return {
                'response': f"Unloading skill: {skill_name}...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'skill_unloaded': skill_name,
            }

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
                         "clearing in_work_mode flag (skills persist)")
                self.tool_manager.in_work_mode = False

                if ai_text and ai_text.strip():
                    self.conversation_history.append({'role': 'assistant', 'content': ai_text})
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

            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
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
                     "AI is done, clearing in_work_mode (skills persist)")
            self.tool_manager.in_work_mode = False

            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
            log.debug("[AIEngine._process_work_mode_response] Normal response appended to history | "
                      f"total entries={len(self.conversation_history)}")

            return {
                'response': ai_text,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    # ═══════════════════════════════════════════════════════════════════════════
    # MEMORY METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _inject_memories(self, user_message: str):
        """Perform semantic recall and stage memory context for the next API call."""
        self._pending_memory_context = ""

        if not self.memory_manager or not self.memory_manager.is_ready:
            return

        memory_enabled = True
        memory_threshold = 0.4
        memory_max = 5

        if self.settings_callback:
            settings = self.settings_callback()
            memory_enabled = settings.get('memory_enabled', True)
            memory_threshold = float(settings.get('memory_threshold', 0.4))
            memory_max = int(settings.get('memory_max_results', 5))

        if not memory_enabled:
            return

        recalled = self.memory_manager.recall(
            query=user_message,
            threshold=memory_threshold,
            max_results=memory_max
        )

        if recalled:
            lines = "\n".join(f"- {m['text']}" for m in recalled)
            self._pending_memory_context = (
                f"<SYSTEM_MEM_RECALL>\n"
                f"MEMORIES DETECTED:\n"
                f"{lines}\n"
                f"<SYSTEM_MEM_RECALL/>"
            )
            log.info(f"[AIEngine._inject_memories] Staged {len(recalled)} memories as system message")
        else:
            log.debug("[AIEngine._inject_memories] No memories passed threshold — skipping injection")

    def _clear_memory_context(self):
        """Call after the API response is received to remove temporary memory context."""
        self._pending_memory_context = ""

    # ═══════════════════════════════════════════════════════════════════════════
    # CONVERSATION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════

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
