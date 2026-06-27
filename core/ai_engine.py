"""
core/ai_engine.py
AI Engine - Handles AI interactions
UNIFIED: Single _build_messages() for all providers. Each _http_* helper
         converts the standard message format to its provider's requirements.
         Anthropic uses the `anthropic` SDK, Gemini uses `google-genai` SDK,
         Puter uses raw HTTP to the local puter_server.
"""

from core.logger import _make_logger, _NoOpLogger
from core.tool_manager import ToolManager
from core.memory_manager import get_memory_manager
from core.global_instructions import (
    get_system_prompt,
    EMPTY_EXIT_SUMMARY_PROMPT,
    PREFILLING,
    WORK_MODE_OUTPUT_ONLY_PROMPT,
)

# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("AIEngine") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

# Priming acknowledgement prepended to every conversation as an assistant turn
_ASSISTANT_PRIMING = (
    "I understand. I will use My Work Environment when I need to complete "
    "complex tasks or need information, and Execute Single commands for quick actions."
)


class AIEngine:
    """AI conversation engine"""

    def __init__(self, log_callback=None, system_info='',
                 voice_mode=False, elevenlabs_enabled=False, settings_callback=None, skill_manager=None,
                 controller=None):
        log.info("[AIEngine.__init__] ── Initializing AI Engine ──────────────────────────────")
        log.debug(f"[AIEngine.__init__] Parameters: voice_mode={voice_mode} | "
                  f"elevenlabs_enabled={elevenlabs_enabled} | "
                  f"has_settings_callback={settings_callback is not None}")

        self.log_callback = log_callback
        self.settings_callback = settings_callback
        self.controller = controller
        if controller:
            log.debug(f"[AIEngine.__init__] Controller passed successfully! | {self.controller}")
        self.conversation_history = []
        log.debug("[AIEngine.__init__] conversation_history initialized (empty)")


        log.debug("[AIEngine.__init__] Creating ToolManager instance")
        self.tool_manager = ToolManager(
            settings_callback=settings_callback,
            ai_engine=self
        )
        log.info("[AIEngine.__init__] ToolManager created and attached")

        self.memory_manager = get_memory_manager()
        self._pending_memory_context = ""
        self._pending_exec_violation_prompt = None
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
            system_info=system_info, voice_mode=voice_mode, elevenlabs_enabled=elevenlabs_enabled,
            skills=skill_manager.get_skills() if skill_manager else []
        )
        self.custom_system_prompt = ""
        log.info(f"[AIEngine.__init__] System prompt generated | length={len(self.system_prompt)} chars")

        self.last_raw_response = None

        # Provider settings — all providers are now external scripts in providers/
        # 'manual' is the only special built-in provider; everything else uses custom_script_path
        self.ai_provider = 'custom_script'
        self.tts_provider = 'edge-tts'

        # Manual provider — set by controller to a callable that blocks until
        # the user types a response.  Signature:
        #   fn(context: str, work_mode: bool, work_output: str) -> str | None
        self.manual_response_fn = None

        # Custom script provider — path to user's .py file
        self.custom_script_path = ""
        # System tab flags
        self.tool_execution_lockout = False
        self.system_prompt_hijacked = False
        self.custom_system_prompt = ""
        # Optional system prompt section flags (main engine only)
        self.include_image_tools = False
        self.include_controller_ref = False
        self.include_notify_tool = False

        log.info(f"[AIEngine.__init__] ── Initialization complete | provider='{self.ai_provider}'")

    # ═══════════════════════════════════════════════════════════════════════════
    # VOICE / VOICE-SETTINGS METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def set_voice_mode(self, enabled):
        """Enable/disable voice mode flag"""
        log.info(f"[AIEngine.set_voice_mode] voice_mode → {enabled} | regenerating system prompt")
        self.voice_mode = enabled
        self.system_prompt = get_system_prompt(
            system_info=self.system_info, voice_mode=self.voice_mode, elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
        )
        log.debug(f"[AIEngine.set_voice_mode] System prompt regenerated | "
                  f"length={len(self.system_prompt)} chars")
        if enabled:
            self.log("Voice mode ENABLED", "INFO")
        else:
            self.log("Voice mode DISABLED", "INFO")

    def update_voice_settings(self, voice_mode, elevenlabs_enabled=False):
        """Update voice mode settings and regenerate prompt"""
        log.info(f"[AIEngine.update_voice_settings] voice_mode={voice_mode} | "
                 f"elevenlabs_enabled={elevenlabs_enabled} | regenerating prompt")
        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled
        self.system_prompt = get_system_prompt(
            system_info=self.system_info, voice_mode=voice_mode, elevenlabs_enabled=elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
        )
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
            system_info=self.system_info,
            voice_mode=self.voice_mode,
            elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
        )
        log.info(f"[AIEngine._on_skills_changed] System prompt regenerated | "
                 f"length={len(self.system_prompt)} chars")

    def _render_active_skills(self) -> str:
        """Build the skill injection block appended to the system prompt.

        Format sent to the API:
            SKILL: media-converter
            <instructions — frontmatter stripped>

            SKILL: another-skill
            <instructions — frontmatter stripped>
        """
        active = self.skill_manager.get_loaded_skills() if self.skill_manager else {}
        if not active:
            return ""

        from core.skill_manager import SkillManager

        lines = [
            "",
            "═══════════════════════════════════════════════════════════",
            "LOADED SKILLS (currently active)",
            "═══════════════════════════════════════════════════════════",
        ]
        for name, content in active.items():
            instructions = SkillManager.strip_frontmatter(content)
            lines.append(f"\nSKILL: {name}")
            lines.append(instructions)
        lines.append("═══════════════════════════════════════════════════════════")
        return "\n".join(lines)

    def _tool_mode(self) -> str:
        """Active tool-calling mode: 'compat' (fenced, universal) or 'native'
        (provider function calling). Read live from settings each call."""
        try:
            s = self.settings_callback() if self.settings_callback else None
            return (s or {}).get('tool_calling_mode', 'compat')
        except Exception:
            return 'compat'

    def _compose_system_prompt(self) -> str:
        """Build the base system prompt with the current tool mode. In native mode
        the fence-format + fence-syntax sections are dropped (the tools travel
        through the provider's function-calling channel instead), trimming tokens."""
        native = self._tool_mode() == 'native'
        return get_system_prompt(
            system_info=self.system_info,
            voice_mode=self.voice_mode,
            elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
            # Native mode: drop fence sections + add the native-invocation override.
            native_tools=native,
        )

    def _get_effective_system_prompt(self) -> str:
        """Return the system prompt enriched with any currently active skills and memory block."""
        if self.system_prompt_hijacked and self.custom_system_prompt.strip():
            base = self.custom_system_prompt
        else:
            # Rebuild fresh so a live Tool Calling Mode switch takes effect immediately.
            base = self._compose_system_prompt() + self._render_active_skills()
        mem_block = self._get_memory_block(base)
        if mem_block:
            base = self._strip_memory_block(base) + f"\n\n{mem_block}"
        return base

    # ═══════════════════════════════════════════════════════════════════════════
    # PROVIDER SETTERS
    # ═══════════════════════════════════════════════════════════════════════════

    def set_provider(self, provider):
        log.info(f"[AIEngine.set_provider] AI provider changing: '{self.ai_provider}' → '{provider}'")
        self.ai_provider = provider
        self.log(f"AI provider set to: {provider}")

    def set_tool_execution_lockout(self, value: bool):
        self.tool_execution_lockout = value

    def set_system_prompt_hijack(self, enabled: bool, custom_prompt: str = ""):
        self.system_prompt_hijacked = enabled
        self.custom_system_prompt = custom_prompt

    def set_system_prompt_extras(self, include_image_tools: bool = False,
                                  include_controller_ref: bool = False,
                                  include_notify_tool: bool = False):
        self.include_image_tools = include_image_tools
        self.include_controller_ref = include_controller_ref
        self.include_notify_tool = include_notify_tool
        self.system_prompt = get_system_prompt(
            system_info=self.system_info,
            voice_mode=self.voice_mode,
            elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
        )

    def set_custom_script_path(self, path):
        log.info(f"[AIEngine.set_custom_script_path] path → '{path}'")
        self.custom_script_path = path
        self.log(f"Custom script path set to: {path}")

    def set_tts_provider(self, provider):
        log.info(f"[AIEngine.set_tts_provider] TTS provider: '{self.tts_provider}' → '{provider}'")
        self.tts_provider = provider
        self.log(f"TTS provider set to: {provider}")

    def log(self, message, level="INFO"):
        _level_fn = {
            "INFO": log.info, "ERROR": log.error,
            "WARNING": log.warning, "SUCCESS": log.info, "DEBUG": log.debug
        }.get(level, log.info)
        _level_fn(f"[AIEngine] {message}")
        if self.log_callback:
            self.log_callback(message, level)

    # ═══════════════════════════════════════════════════════════════════════════
    # MESSAGE BUILDER  (unified — OpenAI-compatible format)
    # Each _http_* helper converts this standard format to its own API shape.
    # ═══════════════════════════════════════════════════════════════════════════

    def _load_session_prefilling(self, session_id: str) -> list:
        """Load a saved session file and return its chat history as prefilling messages.
        Only user and assistant turns are kept — system messages (work mode prompts,
        memory injections, etc.) are stripped so they don't pollute the prefill."""
        import json
        from pathlib import Path
        _APP_ROOT = Path(__file__).resolve().parent.parent
        sessions_dir = _APP_ROOT / "data" / "sessions"
        session_file = None
        for f in sessions_dir.glob(f"*{session_id}.json"):
            session_file = f
            break
        if not session_file or not session_file.exists():
            log.warning(f"[AIEngine._load_session_prefilling] File not found for id='{session_id}'")
            return []
        try:
            with open(session_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            history = data.get('chat_history', [])
            msgs = [
                {'role': m['role'], 'content': m['content']}
                for m in history
                if m.get('role') in ('user', 'assistant')
                   and isinstance(m.get('content'), str)
                   and m['content'].strip()
            ]
            log.info(f"[AIEngine._load_session_prefilling] ✓ Loaded {len(msgs)} messages "
                     f"from '{session_file.name}'")
            return msgs
        except Exception as e:
            log.error(f"[AIEngine._load_session_prefilling] ✗ Failed: {type(e).__name__}: {e}")
            return []

    def _get_history_with_memory(self) -> list:
        """Return conversation_history ready for the API.
        memory_context ui_events are promoted to system messages at their natural
        position (right before the user message they belong to).
        All other ui_event entries are stripped out."""
        history_copy = []
        for m in self.conversation_history:
            if m.get('role') == 'ui_event':
                if m.get('_type') == 'memory_context' and m.get('content'):
                    # Promote to a system message at this exact position
                    history_copy.append({'role': 'system', 'content': m['content']})
                # All other ui_event subtypes are silently dropped
            else:
                history_copy.append(m)
        return history_copy

    def _build_messages(self):
        """Build unified standard message list (OpenAI-compatible format).
        Structure: [{role: system/assistant/user, content: str}, ...]
        All three provider http-helpers consume this and convert as needed.

        Prefilling pairs from PREFILLING are injected here as fake history to
        reinforce instruction-following.  They are NEVER appended to
        conversation_history, so session_manager.py never sees them."""
        log.debug(f"[AIEngine._build_messages] Building message list | "
                  f"history_len={len(self.conversation_history)}")

        messages = [
            {'role': 'system', 'content': self._get_effective_system_prompt()},
        ]

        # ── Conversation prefilling (fake prior turns) ─────────────────────
        prefilling_enabled = True
        prefilling_mode = 'premade'
        prefilling_session_id = ''
        if self.settings_callback:
            _s = self.settings_callback()
            prefilling_enabled = _s.get('prefilling_enabled', True)
            prefilling_mode = _s.get('prefilling_mode', 'premade')
            prefilling_session_id = _s.get('prefilling_session_id', '')

        if prefilling_enabled:
            _active_id = self.controller.current_session_id if self.controller else None

            if prefilling_mode == 'session' and prefilling_session_id and prefilling_session_id != _active_id:
                prefill_msgs = self._load_session_prefilling(prefilling_session_id)
                log.debug(f"[AIEngine._build_messages] Session prefilling: "
                          f"{len(prefill_msgs)} messages from '{prefilling_session_id[:8]}'")
            else:
                if prefilling_mode == 'session' and prefilling_session_id == _active_id:
                    log.warning("[AIEngine._build_messages] Seed session is active — "
                                "falling back to premade PREFILLING to avoid self-reference")
                prefill_msgs = [
                    {'role': m['role'], 'content': m['content']}
                    for m in PREFILLING.get('messages', [])
                ]
                log.debug(f"[AIEngine._build_messages] Premade prefilling: "
                          f"{len(prefill_msgs)} messages")
            for msg in prefill_msgs:
                messages.append(msg)
            messages.append({"role": "system", "content": "[SYSTEM NOTICE]\nTHE MESSAGES FROM ABOVE ARE PRIMERS, NOT "
                                                          "REAL MESSAGES, DO NOT MENTION ANY OF THIS TO THE USER IN THE "
                                                          "INCOMING MESSAGES TO COME!\n\nTHE NEXT MESSAGE TO THIS IS "
                                                          "THE STARTING POINT FOR THE REAL MESSAGES."})
        else:
            messages.append({'role': 'assistant', 'content': _ASSISTANT_PRIMING})
            log.debug("[AIEngine._build_messages] Prefilling disabled — using legacy priming")
        # ──────────────────────────────────────────────────────────────────

        messages.extend(self._get_history_with_memory())
        log.debug(f"[AIEngine._build_messages] Built {len(messages)} total messages")
        return messages

    # ═══════════════════════════════════════════════════════════════════════════
    # THIN HTTP HELPERS  (one per provider — convert unified format as needed)
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_system_and_convo(messages):
        """Split unified message list into (system_prompt, conversation_list).
        The first 'system' role becomes the system prompt string.
        Any subsequent 'system' role messages are wrapped as user turns.
        Consecutive messages of the same role are merged (required by Anthropic)."""
        system_prompt = None
        convo = []
        for msg in messages:
            role, content = msg['role'], msg['content']
            if role == 'system':
                if system_prompt is None:
                    system_prompt = content
                else:
                    # Inject as a user message so the conversation stays valid
                    convo.append({'role': 'user', 'content': f'[SYSTEM]: {content}'})
            else:
                convo.append({'role': role, 'content': content})

        # Merge consecutive same-role turns (Anthropic & Gemini both require alternation)
        merged = []
        for msg in convo:
            if merged and merged[-1]['role'] == msg['role']:
                merged[-1]['content'] += '\n' + msg['content']
            else:
                merged.append(dict(msg))

        return system_prompt, merged

    # ═══════════════════════════════════════════════════════════════════════════
    # UNIFIED PROVIDER DISPATCHER
    # ═══════════════════════════════════════════════════════════════════════════

    def _provider_manual(self) -> str | None:
        """Manual provider — blocks the worker thread until the user submits a response.

        Shows the side panel whenever the most-recently appended history entry has
        role='system' (covers work-mode output, post-exit prompt, and any other
        system injection).  The panel displays the FULL system message so the user
        can see exactly what the real model would have received.
        """
        if not self.manual_response_fn:
            log.error("[AIEngine._http_manual] manual_response_fn not set")
            return None

        # Walk back through history to find what was just injected
        has_system_msg = False
        system_content = ""
        context = ""

        for entry in reversed(self.conversation_history):
            role = entry.get('role', '')
            content = entry.get('content', '')
            if role == 'system' and not has_system_msg:
                # First system entry from the end — this is what the AI would see
                has_system_msg = True
                system_content = content
            elif role == 'user' and not context:
                context = content
            if has_system_msg and context:
                break

        log.info(f"[AIEngine._http_manual] Requesting manual response | "
                 f"has_system_msg={has_system_msg} | context_len={len(context)}")
        result = self.manual_response_fn(context, has_system_msg, system_content)
        if result is None:
            log.warning("[AIEngine._http_manual] User cancelled manual response")
            return None
        log.info(f"[AIEngine._http_manual] Got manual response | length={len(result)}")
        return result

    def _load_provider_module(self):
        """Import the user's custom provider .py fresh (live edits take effect).
        Returns the module, or None on missing path / load error."""
        import importlib.util, traceback, os
        if not self.custom_script_path:
            log.error("[AIEngine._load_provider_module] ✗ No custom script path set")
            self.log("Custom script path is not set", "ERROR")
            return None
        if not os.path.isfile(self.custom_script_path):
            log.error(f"[AIEngine._load_provider_module] ✗ File not found: '{self.custom_script_path}'")
            self.log(f"Custom script not found: {self.custom_script_path}", "ERROR")
            return None
        try:
            spec = importlib.util.spec_from_file_location("custom_provider", self.custom_script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except Exception as e:
            log.error(f"[AIEngine._load_provider_module] ✗ Failed to load script: {e}\n{traceback.format_exc()}")
            self.log(f"Custom script load error: {e}", "ERROR")
            return None

    def _native_provider_supported(self, module) -> bool:
        """True if the active mode is native AND the provider script opts in."""
        return (
            self._tool_mode() == 'native'
            and module is not None
            and getattr(module, 'SUPPORTS_NATIVE_TOOLS', False)
            and callable(getattr(module, 'chat_tools', None))
        )

    def _provider_script_native(self, module, messages, images=None) -> str | None:
        """Native tool-calling path. Calls the provider's chat_tools() with the
        canonical tool registry, then reconstructs the equivalent fence text so
        the rest of the engine processes it exactly like a compat response."""
        import traceback
        system_prompt, convo = self._extract_system_and_convo(messages)
        # Keep the native tools list in lockstep with the prompt. Hijacked prompts
        # are background tasks, which disable session naming — so don't offer
        # set_session_name there (it would contradict the task prompt).
        tools = self.tool_manager.get_canonical_tools(
            include_session_naming=not self.system_prompt_hijacked,
            include_skills=bool(self.skill_manager),
        )
        try:
            result = module.chat_tools(system_prompt or "", convo, tools, images=images)
        except Exception as e:
            log.error(f"[AIEngine._provider_script_native] ✗ chat_tools() raised: {e}\n{traceback.format_exc()}")
            self.log(f"Custom script chat_tools() error: {e}", "ERROR")
            return None
        if not isinstance(result, dict):
            log.error("[AIEngine._provider_script_native] ✗ chat_tools() must return a dict "
                      "{'text', 'tool_calls'}")
            return None

        text = (result.get('text') or "").strip()
        fences = self.tool_manager.tool_calls_to_fences(result.get('tool_calls') or [])
        ai_text = (text + ("\n\n" if text and fences else "") + fences).strip()
        log.info(f"[AIEngine._provider_script_native] ✓ native result | text_len={len(text)} | "
                 f"tool_calls={len(result.get('tool_calls') or [])} | reconstructed_len={len(ai_text)}")
        return ai_text or None

    def _provider_script(self, messages, images=None, module=None) -> str | None:
        """Custom script provider — reimports the user's .py file on every call
        and invokes its chat(system_prompt, messages) function.
        If images (list) is provided and the script defines chat_image(), that is called instead."""
        import traceback

        if module is None:
            module = self._load_provider_module()
        if module is None:
            return None

        if not hasattr(module, 'chat') or not callable(module.chat):
            log.error("[AIEngine._http_custom_script] ✗ Script has no callable chat() function")
            self.log("Custom script must define a chat(system_prompt, messages) function", "ERROR")
            return None

        system_prompt, convo = self._extract_system_and_convo(messages)

        try:
            if images and hasattr(module, 'chat_image') and callable(module.chat_image):
                log.debug(f"[AIEngine._http_custom_script] Calling chat_image() | images={images}")
                result = module.chat_image(system_prompt or "", convo, images)
            else:
                result = module.chat(system_prompt or "", convo)
        except Exception as e:
            log.error(f"[AIEngine._http_custom_script] ✗ chat/chat_image() raised: {e}\n{traceback.format_exc()}")
            self.log(f"Custom script error: {e}", "ERROR")
            return None

        if not result or not isinstance(result, str):
            log.error("[AIEngine._http_custom_script] ✗ chat() returned empty or non-string value")
            self.log("Custom script chat() must return a non-empty string", "ERROR")
            return None

        log.info(f"[AIEngine._http_custom_script] ✓ Response | length={len(result)} chars")
        return result

    def _call_provider(self, image=None, images=None) -> str | None:
        """Build the unified message list and dispatch to the active provider script.
        The 'manual' provider is the only built-in special case.
        All other providers are loaded from the custom_script_path.
        """
        if images is None and image is not None:
            images = [image]

        log.debug(f"[AIEngine._call_provider] script='{self.custom_script_path}' | "
                  f"images={len(images) if images else 0}")
        messages = self._build_messages()

        try:
            from core.token_est import log_tokens, estimate_history_tokens
            log_tokens(estimate_history_tokens(messages))
        except Exception:
            pass

        if self.ai_provider == 'manual':
            return self._provider_manual()
        elif self.ai_provider == 'custom_script':
            # Optional resilience: background callers (task agent) set
            # provider_max_retries > 0 so a transient provider hiccup (None
            # response or exception) is retried instead of failing the turn.
            # The main session leaves it at 0 → identical single-attempt behaviour.
            max_retries = getattr(self, 'provider_max_retries', 0)
            backoff = getattr(self, 'provider_retry_backoff', 2.0)

            # Decide compat vs native ONCE per call. Only load the module up-front
            # when native mode is on (keeps the compat path free of extra loads).
            _native_module = None
            if self._tool_mode() == 'native':
                _native_module = self._load_provider_module()
                if not self._native_provider_supported(_native_module):
                    log.info("[AIEngine._call_provider] Native mode requested but provider "
                             "lacks chat_tools/SUPPORTS_NATIVE_TOOLS — falling back to compat")
                    _native_module = None

            attempt = 0
            result = None
            while True:
                try:
                    if _native_module is not None:
                        result = self._provider_script_native(_native_module, messages, images=images)
                    else:
                        result = self._provider_script(messages, images=images)
                except Exception as e:
                    log.error(f"[AIEngine._call_provider] provider raised: {type(e).__name__}: {e}")
                    result = None
                if result or attempt >= max_retries:
                    break
                attempt += 1
                wait = backoff * attempt
                log.warning(f"[AIEngine._call_provider] empty/failed response — "
                            f"retry {attempt}/{max_retries} in {wait:.1f}s")
                import time as _t
                _t.sleep(wait)
            if result:
                try:
                    from core.token_est import log_output_tokens, estimate_tokens
                    log_output_tokens(estimate_tokens(result))
                except Exception:
                    pass
            return result
        return

    def raw_call(self, system_prompt: str, history: list) -> str | None:
        """
        Stateless one-shot call for external callers (e.g. task engine).
        Uses the current provider script. Does NOT touch self.conversation_history.
        """
        log.info(f"[AIEngine.raw_call] script='{self.custom_script_path}' | history={len(history)} msgs")
        if self.ai_provider == 'manual':
            log.warning("[AIEngine.raw_call] 'manual' provider cannot be used in background tasks — returning None")
            return None
        messages = [{'role': 'system', 'content': system_prompt}]
        messages.extend(
            {'role': m['role'], 'content': m['content']}
            for m in history
            if m.get('role') in ('user', 'assistant')
        )
        try:
            from core.token_est import log_tokens, estimate_history_tokens
            log_tokens(estimate_history_tokens(messages))
        except Exception:
            pass
        result = self._provider_script(messages)
        if result:
            try:
                from core.token_est import log_output_tokens, estimate_tokens
                log_output_tokens(estimate_tokens(result))
            except Exception:
                pass
        return result

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

    def generate_response_with_image(self, user_message, image_paths):
        """Generate response with one or more image attachments (Puter / custom_script).

        image_paths: str (single, backward compat) or list[str] (multi-image).
        Puter only uses the first image; custom_script receives all of them.
        """
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        image_paths = [p for p in image_paths if p]

        log.info(f"[AIEngine.generate_response_with_image] provider='{self.ai_provider}' | "
                 f"images={len(image_paths)} | msg_len={len(user_message)}")

        self._inject_memories(user_message)
        self.conversation_history.append({'role': 'user', 'content': user_message})
        log.debug(f"[AIEngine.generate_response_with_image] User message appended | "
                  f"history_len={len(self.conversation_history)}")

        ai_text = self._call_provider(images=image_paths)
        if not ai_text:
            log.error("[AIEngine.generate_response_with_image] ✗ No AI text returned")
            return self._make_error_result("Error: No response from provider")

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

        # Slim down the previous work-mode system message so only the latest ping
        # carries the full instruction block — everything before becomes output-only.
        for entry in reversed(self.conversation_history):
            if entry.get('role') == 'system' and entry.get('_is_work_prompt'):
                prev_output = entry.get('_work_output', '')
                entry['content'] = WORK_MODE_OUTPUT_ONLY_PROMPT.format(work_output=prev_output)
                del entry['_is_work_prompt']
                break

        # Capture output now — before get_work_mode_prompt() — to store alongside the entry
        _current_work_output = self.tool_manager.last_work_output or "No previous output"
        work_prompt = self.tool_manager.get_work_mode_prompt()
        self.conversation_history.append({
            'role': 'system',
            'content': work_prompt,
            '_is_work_prompt': True,      # marks this as the "live" work ping
            '_work_output': _current_work_output,  # stored so we can slim it next iteration
        })
        log.debug(f"[AIEngine.continue_work_mode] Work mode prompt appended (full) | "
                  f"prompt_len={len(work_prompt)} | history_len={len(self.conversation_history)}")

        ai_text = self._call_provider()
        if not ai_text:
            log.error("[AIEngine.continue_work_mode] ✗ No AI text returned")
            return self._make_error_result(f"Error: No response from {self.ai_provider} provider")

        log.info(f"[AIEngine.continue_work_mode] ✓ Got response | length={len(ai_text)} chars | "
                 f"→ _process_work_mode_response()")
        return self._process_work_mode_response(ai_text)

    def _get_ai_response_internal(self, prompt):
        """Get a single-turn AI response without touching conversation history.
        Used for code explanation dialogs, approval dialogs, etc."""
        log.debug(f"[AIEngine._get_ai_response_internal] provider='{self.ai_provider}' | "
                  f"prompt_len={len(prompt)} chars")
        try:
            single_turn = [{'role': 'user', 'content': prompt}]
            if self.ai_provider == 'custom_script':
                result = self._provider_script(single_turn)
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

        # ── Unclosed-fence recovery ────────────────────────────────────────────────
        # If the model forgot the closing ``` on a tool call, auto-close it so the
        # call runs instead of leaking the raw block into chat.
        ai_text, _recovered_tool = self.tool_manager.recover_unclosed_tool_fence(ai_text)
        if _recovered_tool:
            self.tool_manager.approval_signal.system_message.emit(
                f"⚠️ **NOTICE:** Recovered a malformed `{_recovered_tool}` call "
                f"(missing closing code fence). It was auto-completed and run. "
                "If this happens often, consider a more capable model."
            )
        # ──────────────────────────────────────────────────────────────────────────

        # ── Single-exec guardrail ──────────────────────────────────────────────────
        ai_text, _violated, _ = self.tool_manager.enforce_single_exec_policy(ai_text)
        if _violated:
            from core.global_instructions import EXEC_CODE_TOOLCALL_VIOLATION_PROMPT
            # Append directly to conversation_history so the AI carries this
            # correction forward in ALL future turns, not just the next one.
            self.conversation_history.append({
                'role': 'system',
                'content': EXEC_CODE_TOOLCALL_VIOLATION_PROMPT
            })
        # ──────────────────────────────────────────────────────────────────────────

        if self.tool_execution_lockout:
            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
            return {
                'response': ai_text,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'session_name': None
            }

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

        _display_text = ai_text

        # ── Check for load_skill call (now valid anywhere) ────────────────────
        load_skill_result = self.tool_manager.parse_load_skill(_display_text)

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
            # Auto-follow-up: call AI immediately so user doesn't have to prompt first
            _followup = self._call_provider()
            if _followup:
                _r = self._process_ai_response(_followup)
                _r['skill_loaded'] = skill_name if success else None
                return _r
            return {
                'response': remaining_text.strip() if remaining_text and remaining_text.strip()
                else (f"✅ Skill '{skill_name}' loaded." if success else f"⚠ {msg}"),
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'skill_loaded': skill_name if success else None,
            }

        # ── Check for unload_skill call (valid anywhere) ──────────────────────
        unload_skill_result = self.tool_manager.parse_unload_skill(_display_text)
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
            # Auto-follow-up: call AI immediately so user doesn't have to prompt first
            _followup = self._call_provider()
            if _followup:
                _r = self._process_ai_response(_followup)
                _r['skill_unloaded'] = skill_name if success else None
                return _r
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
        work_call = self.tool_manager.parse_work_environment(_display_text)

        if work_call:
            code, visible_text = work_call
            log.info(f"[AIEngine._process_ai_response] work_environment detected | "
                     f"code_len={len(code)} | visible_text_len={len(visible_text)}")
            self.log(f"Work environment call detected")

            log.debug("[AIEngine._process_ai_response] → Calling tool_manager.run_work_environment()")
            # Set BEFORE run_work_environment so interrupt_response() can detect active work
            self.tool_manager.in_work_mode = True
            work_output = self.tool_manager.run_work_environment(code)

            if work_output == "EXITED_WORK_MODE":
                # Shouldn't happen on first entry — AI should never exit immediately.
                # Kept as a safety net only.
                log.warning("[AIEngine._process_ai_response] Unexpected immediate exit on work mode entry")
                self.tool_manager.in_work_mode = False
                if ai_text and ai_text.strip():
                    self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                return {
                    'response': visible_text if visible_text and visible_text.strip() else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True,
                    'session_name': session_name
                }

            log.debug(f"[AIEngine._process_ai_response] Storing work output | "
                      f"length={len(work_output)} chars | in_work_mode → True")
            self.tool_manager.last_work_output = work_output

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
        execute_call = self.tool_manager.parse_execute_code(_display_text)

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

        # Safety net: strip any remaining tool call from display text
        display_text = self.tool_manager.strip_tool_calls(ai_text)
        if display_text != ai_text:
            log.warning(f"[AIEngine._process_ai_response] Safety net stripped residual tool call | "
                        f"before={len(ai_text)} → after={len(display_text)} chars")
            if not session_name:
                missed_call = self.tool_manager.parse_set_session_name(ai_text)
                if missed_call:
                    session_name = missed_call[0]
                    log.info(f"[AIEngine._process_ai_response] Safety net recovered session_name: "
                             f"'{session_name}'")

        _mc = getattr(self, '_pending_memory_widget', None)
        self._pending_memory_widget = None  # consume — prevent stale reuse in recursive calls
        return {
            'response': display_text,
            'has_work_call': False,
            'in_work_mode': False,
            'thinking': False,
            'session_name': session_name,
            'memory_context': _mc,
        }

    def _process_work_mode_response(self, ai_text):
        """Process AI response while in work mode."""
        log.info(f"[AIEngine._process_work_mode_response] Processing work mode response | "
                 f"length={len(ai_text)} chars")
        log.debug(f"[AIEngine._process_work_mode_response] Preview: "
                  f"'{ai_text[:100].replace(chr(10), '↵')}'")
        self.last_raw_response = ai_text

        # ── Unclosed-fence recovery ────────────────────────────────────────────────
        ai_text, _recovered_tool = self.tool_manager.recover_unclosed_tool_fence(ai_text)
        if _recovered_tool:
            self.tool_manager.approval_signal.system_message.emit(
                f"⚠️ **NOTICE:** Recovered a malformed `{_recovered_tool}` call "
                f"(missing closing code fence). It was auto-completed and run. "
                "If this happens often, consider a more capable model."
            )
        # ──────────────────────────────────────────────────────────────────────────

        # ── Single-exec guardrail ──────────────────────────────────────────────────
        ai_text, _violated, _ = self.tool_manager.enforce_single_exec_policy(ai_text)
        if _violated:
            from core.global_instructions import EXEC_CODE_TOOLCALL_VIOLATION_PROMPT
            self._pending_exec_violation_prompt = EXEC_CODE_TOOLCALL_VIOLATION_PROMPT
        # ──────────────────────────────────────────────────────────────────────────

        log.debug("[AIEngine._process_work_mode_response] Checking for consecutive work_environment call...")

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
                    'session_name': session_name,
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
                'session_name': session_name,
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
                    'session_name': session_name,
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
                'session_name': session_name,
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

                # Slim down the last work-mode ping now that work mode is exiting.
                # It was never slimmed by continue_work_mode() because no next ping came after it.
                for entry in reversed(self.conversation_history):
                    if entry.get('role') == 'system' and entry.get('_is_work_prompt'):
                        prev_output = entry.get('_work_output', '')
                        entry['content'] = WORK_MODE_OUTPUT_ONLY_PROMPT.format(work_output=prev_output)
                        del entry['_is_work_prompt']
                        log.debug("[AIEngine._process_work_mode_response] Last work-mode ping slimmed on exit")
                        break

                if ai_text and ai_text.strip():
                    self.conversation_history.append({'role': 'assistant', 'content': ai_text})
                    log.debug("[AIEngine._process_work_mode_response] ai_text appended to history (exit)")

                # ── Empty-exit guard ──────────────────────────────────────────
                if not visible_text or not visible_text.strip():
                    log.warning("[AIEngine._process_work_mode_response] Exit without summary detected — "
                                "injecting reminder and re-calling provider")
                    self.conversation_history.append({'role': 'system', 'content': EMPTY_EXIT_SUMMARY_PROMPT})
                    ai_text2 = self._call_provider()
                    if ai_text2:
                        return self._process_ai_response(ai_text2)
                    return {
                        'response': "",
                        'has_work_call': False,
                        'in_work_mode': False,
                        'thinking': False,
                        'session_name': session_name,
                        'exited_work_mode': True
                    }
                # ─────────────────────────────────────────────────────────────

                return {
                    'response': visible_text,
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'session_name': session_name,
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

            # Slim down the last work-mode ping now that work mode is exiting.
            # It was never slimmed by continue_work_mode() because no next ping came after it.
            for entry in reversed(self.conversation_history):
                if entry.get('role') == 'system' and entry.get('_is_work_prompt'):
                    prev_output = entry.get('_work_output', '')
                    entry['content'] = WORK_MODE_OUTPUT_ONLY_PROMPT.format(work_output=prev_output)
                    del entry['_is_work_prompt']
                    log.debug("[AIEngine._process_work_mode_response] Last work-mode ping slimmed on exit")
                    break

            self.conversation_history.append({'role': 'assistant', 'content': ai_text})
            log.debug("[AIEngine._process_work_mode_response] Normal response appended to history | "
                      f"total entries={len(self.conversation_history)}")

            return {
                'response': ai_text,
                'has_work_call': False,
                'in_work_mode': False,
                'session_name': session_name,
                'thinking': False
            }

    # ═══════════════════════════════════════════════════════════════════════════
    # MEMORY METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    _MEM_BLOCK_START = "============================= SYSTEM MEMORY BLOCK ============================="
    _MEM_BLOCK_END = "================================================================================="

    def _strip_memory_block(self, text: str) -> str:
        """Remove any existing memory block section from prompt text."""
        start = text.find(self._MEM_BLOCK_START)
        if start == -1:
            return text
        end = text.find(self._MEM_BLOCK_END, start)
        if end == -1:
            return text[:start]
        return text[:start] + text[end + len(self._MEM_BLOCK_END):]

    def _get_memory_block(self, text: str) -> str:
        """Extract the memory block section from text, or empty string."""
        start = text.find(self._MEM_BLOCK_START)
        if start == -1:
            return ""
        end = text.find(self._MEM_BLOCK_END, start)
        if end == -1:
            return text[start:]
        return text[start:end + len(self._MEM_BLOCK_END)]

    def _build_memory_block(self) -> str | None:
        """Build the memory block string from current memories, or None if not applicable."""
        if not self.memory_manager or not self.memory_manager.is_ready:
            return None

        memory_enabled = True
        memory_recall_mode = 'inject_all'
        if self.settings_callback:
            settings = self.settings_callback()
            memory_enabled = settings.get('memory_enabled', True)
            memory_recall_mode = settings.get('memory_recall_mode', 'inject_all')
        if not memory_enabled or memory_recall_mode != 'inject_all':
            return None

        all_memories = self.memory_manager.get_all()
        if not all_memories:
            log.debug("[AIEngine._build_memory_block] No memories to inject")
            return None

        lines = "\n".join(
            f"- {m['text']}" + ("\n\n" if m['text'].splitlines()[-1].startswith("Tags:") else "")
            for m in all_memories
        )
        return (
            f"\n\n{self._MEM_BLOCK_START}\n\n"
            f"ALL MEMORIES:\n"
            f"{lines}\n"
            f"{self._MEM_BLOCK_END}"
        )

    def _inject_memories_into_prompt(self):
        """Inject all memories into self.system_prompt if in inject_all mode.
        Called after every system prompt rebuild. Strips any stale block first.
        Compares old vs new — skips if unchanged, replaces if different."""
        new_block = self._build_memory_block()

        if new_block is None:
            self.system_prompt = self._strip_memory_block(self.system_prompt)
            return

        old_block = self._get_memory_block(self.system_prompt)

        if old_block == new_block:
            log.debug("[AIEngine._inject_memories_into_prompt] Memory block unchanged — skipping")
            return

        self.system_prompt = self._strip_memory_block(self.system_prompt)
        self.system_prompt += new_block
        count = len(self.memory_manager.get_all() or []) if self.memory_manager else 0
        log.info(f"[AIEngine._inject_memories_into_prompt] Injected {count} memories")

    def refresh_memory_block(self):
        """Public method — refresh the memory block. Call after memories or settings change."""
        self._inject_memories_into_prompt()

    def _inject_memories(self, user_message: str):
        """Perform semantic recall (RAG mode only). inject_all is already in the prompt."""
        self._pending_memory_context = ""

        if not self.memory_manager or not self.memory_manager.is_ready:
            return

        memory_enabled = True
        memory_recall_mode = 'inject_all'
        memory_threshold = 0.4
        memory_max = 5

        if self.settings_callback:
            settings = self.settings_callback()
            memory_enabled = settings.get('memory_enabled', True)
            memory_recall_mode = settings.get('memory_recall_mode', 'inject_all')
            memory_threshold = float(settings.get('memory_threshold', 0.4))
            memory_max = int(settings.get('memory_max_results', 5))

        if not memory_enabled:
            return

        if memory_recall_mode == 'rag':
            self._inject_memories_rag(user_message, memory_threshold, memory_max)
        # inject_all: memories already in system prompt, nothing to do here

    def _inject_memories_rag(self, user_message: str, threshold: float, max_results: int):
        """Perform semantic recall and store memory context as a persistent ui_event in history."""
        recalled = self.memory_manager.recall(
            query=user_message,
            threshold=threshold,
            max_results=max_results
        )

        self._pending_memory_widget = None  # reset every call, not just when recalled
        if recalled:
            import uuid as _uuid
            lines = "\n".join(f"- {m['text']}" for m in recalled)
            context_text = (
                f"<SYSTEM_MEM_RECALL>\n"
                f"MEMORIES DETECTED:\n"
                f"{lines}\n"
                f"<SYSTEM_MEM_RECALL/>"
            )
            context_id = str(_uuid.uuid4())[:8]
            # Append as a persistent ui_event — saved with session, filtered from API
            # by _get_history_with_memory which promotes it to a system message inline
            self.conversation_history.append({
                'role': 'ui_event',
                '_type': 'memory_context',
                '_memory_context_id': context_id,
                'content': context_text,
                '_memories_preview': [m['text'] for m in recalled],
            })
            log.info(f"[AIEngine._inject_memories] Stored {len(recalled)} memories as "
                     f"persistent ui_event | id={context_id}")
            # Store for result dict so controller can show the widget on the main thread
            self._pending_memory_widget = (context_id, [m['text'] for m in recalled])
            # Notify Android bridge if a phone is connected (thread-safe via _dispatch lock)
            try:
                ab = getattr(getattr(getattr(self, 'controller', None), 'ui', None),
                             'android_bridge', None)
                if ab and ab.isVisible():
                    ab.add_memory_context_card(context_id, [m['text'] for m in recalled])
            except Exception:
                pass
        else:
            log.debug("[AIEngine._inject_memories] No memories passed threshold — skipping injection")

    def _clear_memory_context(self):
        """No-op — memory context is now stored as persistent ui_event entries."""
        self._pending_memory_context = ""  # kept for safety

    def detach_memory_context(self, context_id: str) -> bool:
        """Remove a memory_context ui_event from conversation history by its ID."""
        before = len(self.conversation_history)
        self.conversation_history = [
            m for m in self.conversation_history
            if not (m.get('role') == 'ui_event'
                    and m.get('_type') == 'memory_context'
                    and m.get('_memory_context_id') == context_id)
        ]
        removed = len(self.conversation_history) < before
        if removed:
            log.info(f"[AIEngine.detach_memory_context] ✓ Detached id={context_id}")
        else:
            log.warning(f"[AIEngine.detach_memory_context] id={context_id} not found")
        return removed

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
