"""
core/ai_engine.py
AI Engine - Handles AI interactions
UNIFIED: Single _build_messages() for all providers. Each _http_* helper
         converts the standard message format to its provider's requirements.
         Anthropic uses the `anthropic` SDK, Gemini uses `google-genai` SDK,
         Puter uses raw HTTP to the local puter_server.
"""

import re

from systema.common.logger import _make_logger, _NoOpLogger
from systema.execution.tool_manager import ToolManager
from systema.engine import native_adapters as na
from systema.memory.memory_manager import get_memory_manager
from systema.common.token_est import estimate_tokens
from systema.engine.prompts.global_instructions import (
    get_system_prompt,
    EMPTY_EXIT_SUMMARY_PROMPT,
    PREFILLING,
    PREFILLING_NATIVE,
    WORK_MODE_OUTPUT_ONLY_PROMPT,
)

# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
log = _make_logger("AIEngine") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────

# Priming acknowledgement prepended to every conversation as an assistant turn
_ASSISTANT_PRIMING = (
    "I understand. I will use my Python Interpreter whenever a task needs code — "
    "chaining executions until it is complete, then finishing with a full report."
)


def summarize_memory_injection(memories: list, cap_tokens: int) -> dict:
    """Pure helper — how the inject-all memory block fits inside a token cap.

    `memories` is assumed newest-first (matching MemoryManager.get_all()). The
    newest memory is always kept, even if it alone exceeds the cap; keeping is a
    newest-first PREFIX cut (once the cap is hit, no further memories are added).
    Token counts use the app-wide estimator (~4 chars/token) so the cap decision
    and any UI readout can never drift apart.

    Returns {total_tokens, used_tokens, kept, omitted, entries}.
    """
    entries = []
    used = 0
    total = 0
    stop = False
    for m in memories:
        text = m.get('text', '') if isinstance(m, dict) else str(m)
        _last = text.splitlines()[-1:] or ['']
        entry = f"- {text}" + ("\n\n" if _last[0].startswith("Tags:") else "")
        et = estimate_tokens(entry)
        total += et
        if not stop:
            if entries and used + et > cap_tokens:
                stop = True   # prefix cut — keep only the newest that fit
            else:
                entries.append(entry)
                used += et
    kept = len(entries)
    return {"total_tokens": total, "used_tokens": used, "kept": kept,
            "omitted": len(memories) - kept, "entries": entries}


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
        # Native tool-calling: the structured {text, tool_calls} the provider
        # returned for the CURRENT turn, stashed by _provider_script_native and
        # consumed by _append_assistant (attached as metadata to the assistant
        # entry). Set fresh per _call_provider; None in compat / text-only turns.
        self._pending_native = None
        # Debug observability (surfaced in the Debug window): the tool transport
        # used this turn, its native tool-call count, and the exact wire payload +
        # raw provider result — so the user can confirm native is live and inspect
        # the sent structure vs. the received response.
        self.last_tool_transport = 'compat'
        self.last_native_tool_calls = 0
        self.last_sent_messages = None
        self.last_raw_provider_result = None
        log.debug("[AIEngine.__init__] conversation_history initialized (empty)")


        log.debug("[AIEngine.__init__] Creating ToolManager instance")
        self.tool_manager = ToolManager(
            settings_callback=settings_callback,
            ai_engine=self
        )
        log.info("[AIEngine.__init__] ToolManager created and attached")

        _embed_model = None
        if settings_callback:
            try:
                _embed_model = settings_callback().get('memory_embed_model')
            except Exception:
                _embed_model = None
        self.memory_manager = get_memory_manager(_embed_model)
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
        #   fn(context: str, interpreter_mode: bool, work_output: str) -> str | None
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

        # Ephemeral image analysis: paths queued by attach_image_to_context() are
        # fed to the provider's chat_image() on the NEXT work-mode step, exactly
        # once, then deleted — never pinned, never stored in history (token-cheap).
        import threading as _threading
        self._pending_context_images = []
        self._images_lock = _threading.Lock()

        log.info(f"[AIEngine.__init__] ── Initialization complete | provider='{self.ai_provider}'")

    # ── ephemeral image context (mirrors TaskAIEngine) ──────────────────────
    def queue_context_image(self, path: str):
        """Queue an image path to feed to the provider once on the next work
        step. Thread-safe (callable from a python_interpreter thread)."""
        with self._images_lock:
            self._pending_context_images.append(path)

    def _drain_context_images(self) -> list:
        with self._images_lock:
            pending = list(self._pending_context_images)
            self._pending_context_images.clear()
        return pending

    def supports_image_analysis(self) -> bool:
        """True if the active provider script exposes chat_image() — the capability
        attach_image_to_context() needs. Never raises."""
        try:
            module = self._load_provider_module()
            return bool(module and hasattr(module, 'chat_image')
                        and callable(getattr(module, 'chat_image')))
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════════════════
    # VOICE / VOICE-SETTINGS METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _rebuild_system_prompt(self):
        """Regenerate self.system_prompt from the current flags, then re-inject
        the memory block. EVERY prompt rebuild must go through here — assigning
        get_system_prompt() directly loses the injected memories."""
        self.system_prompt = get_system_prompt(
            system_info=self.system_info, voice_mode=self.voice_mode,
            elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
        )
        self._inject_memories_into_prompt()

    def set_voice_mode(self, enabled):
        """Enable/disable voice mode flag"""
        log.info(f"[AIEngine.set_voice_mode] voice_mode → {enabled} | regenerating system prompt")
        self.voice_mode = enabled
        self._rebuild_system_prompt()
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
        self._rebuild_system_prompt()
        log.debug(f"[AIEngine.update_voice_settings] Prompt regenerated | "
                  f"length={len(self.system_prompt)} chars")
        self.log(f"Voice settings updated: voice_mode={voice_mode}, elevenlabs={elevenlabs_enabled}")

    # ═══════════════════════════════════════════════════════════════════════════
    # SKILL METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    def _on_skills_changed(self):
        """Regenerate system prompt when the skills folder changes."""
        log.info("[AIEngine._on_skills_changed] Skills changed — regenerating system prompt")
        self._rebuild_system_prompt()
        log.info(f"[AIEngine._on_skills_changed] System prompt regenerated | "
                 f"length={len(self.system_prompt)} chars")

    def _neutralize_tool_fences(self, text: str) -> str:
        """Rewrite compat tool-call fences inside skill docs into plain code
        fences with a note line. In NATIVE mode the model must never SEE
        fence-call syntax — skills documenting compat examples otherwise teach
        it to write fences in free text instead of making real tool calls
        (the meta-control skill triggered exactly that)."""
        out = []
        for line in text.split('\n'):
            m = re.match(r'^(`{3,})[ \t]*([A-Za-z][\w-]*)[ \t]*(?::[ \t]*(.*))?[ \t]*$', line)
            if m:
                canonical, _ = self.tool_manager._resolve_tool_tag(
                    m.group(2), include_legacy=True)
                if canonical:
                    ann = (m.group(3) or '').strip().strip('[]').strip()
                    note = (f"(example — run via the {canonical} tool: {ann})"
                            if ann else f"(example — run via the {canonical} tool)")
                    lang = 'python' if canonical in ('python_interpreter',
                                                     'execute_code') else 'text'
                    out.append(note)
                    out.append(f"{m.group(1)}{lang}")
                    continue
            out.append(line)
        return '\n'.join(out)

    def _render_active_skills(self) -> str:
        """Build the skill injection block appended to the system prompt.

        Format sent to the API:
            SKILL: media-converter
            <instructions — frontmatter stripped>

            SKILL: another-skill
            <instructions — frontmatter stripped>

        In native tool-calling mode, compat fence examples inside skill docs
        are neutralized (see _neutralize_tool_fences) and a banner tells the
        model the fence syntax is reference-only.
        """
        active = self.skill_manager.get_loaded_skills() if self.skill_manager else {}
        if not active:
            return ""

        from systema.agents.skill_manager import SkillManager

        native = self._tool_mode() == 'native'
        lines = [
            "",
            "═══════════════════════════════════════════════════════════",
            "LOADED SKILLS (currently active)",
            "═══════════════════════════════════════════════════════════",
        ]
        if native:
            lines.append(
                "NOTE: You are in NATIVE tool-calling mode. Any fenced tool "
                "syntax inside skill docs is the compat format, shown for "
                "reference only — NEVER write fences like that in your replies. "
                "Invoke tools exclusively through native tool calls.")
        for name, content in active.items():
            instructions = SkillManager.strip_frontmatter(content)
            if native:
                instructions = self._neutralize_tool_fences(instructions)
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
        # In inject-all recall mode the full memory set is grafted into this
        # prompt, so the memory section drops search_memory (redundant). Only the
        # MAIN chat engine injects the block — tasks keep the search variant.
        inject_all = False
        if self.settings_callback:
            _s = self.settings_callback()
            inject_all = (_s.get('memory_enabled', True)
                          and _s.get('memory_recall_mode', 'inject_all') == 'inject_all')
        return get_system_prompt(
            system_info=self.system_info,
            voice_mode=self.voice_mode,
            elevenlabs_enabled=self.elevenlabs_enabled,
            skills=self.skill_manager.get_skills() if self.skill_manager else [],
            include_image_tools=self.include_image_tools,
            include_controller_ref=self.include_controller_ref,
            include_notify_tool=self.include_notify_tool,
            memory_inject_all=inject_all,
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
        # The memory block is maintained on self.system_prompt by
        # _inject_memories_into_prompt() (refreshed on every memory/settings/
        # skills change). base is a FRESH recompose that never carries it — read
        # the block from self.system_prompt, not from base, or inject_all never
        # ships (the send-path bug that made the toggle a silent no-op).
        mem_block = self._get_memory_block(self.system_prompt)
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
        self._rebuild_system_prompt()

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
        from systema import APP_ROOT as _APP_ROOT
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
                # Native mode gets the native primer (native function calls, no
                # fences/JSON) so the premade history doesn't push the model toward
                # writing fences as text.
                _prefill_src = PREFILLING_NATIVE if self._tool_mode() == 'native' else PREFILLING
                prefill_msgs = [
                    {'role': m['role'], 'content': m['content']}
                    for m in _prefill_src.get('messages', [])
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

    def _append_assistant(self, ai_text: str) -> dict:
        """Append an assistant turn, attaching native tool-call metadata when the
        turn came from a native provider call (self._pending_native). The stored
        `content` stays the reconstructed fence text — display, compat sending and
        the session JSON are all unchanged. The `_tool_calls` + `_assistant_text`
        metadata is what the NATIVE send path (_build_native_convo) turns into real
        tool_use / tool_result blocks. Compat / text turns get a plain entry. The
        metadata persists to the session file for free, like the existing
        `_is_work_prompt` key."""
        entry = {'role': 'assistant', 'content': ai_text}
        pend = self._pending_native
        if pend and pend.get('tool_calls'):
            entry['_tool_calls'] = pend['tool_calls']
            entry['_assistant_text'] = pend.get('text') or ''
        self._pending_native = None
        self.conversation_history.append(entry)
        return entry

    def _build_native_convo(self, messages):
        """Turn the unified message list into (system_prompt, neutral_convo) for
        native sending. The first system message becomes the system prompt.
        Assistant entries carrying `_tool_calls` metadata become real tool-CALL
        turns, and the system entries that immediately follow them are consumed as
        their tool RESULTS — positional pairing, bounded by the call count and
        stopping at the first non-system entry, synth-filled with "(no output)" so
        every tool_use is answered (no dialect ever sees an unpaired call).
        Everything else becomes a plain text turn; assistant text without metadata
        (legacy / compat-origin) is defensively fence-stripped so it can never leak
        a fence into the native channel. na.build_messages() renders the neutral
        convo into the provider's dialect."""
        system_prompt = None
        entries = []
        for m in messages:
            if m.get('role') == 'system' and system_prompt is None:
                system_prompt = m.get('content')
            else:
                entries.append(m)

        neutral = []
        i, n = 0, len(entries)
        while i < n:
            m = entries[i]
            role = m.get('role')
            calls = m.get('_tool_calls') if role == 'assistant' else None
            if calls:
                # Gather following system entries as this turn's results (a real
                # user/assistant turn is never a tool result). Pairing is
                # NAME-AWARE: legacy set_session_name calls (tool retired
                # 2026-07-19, #31) ran silently and produced no output entry,
                # so they never consume one — old sessions' metadata still
                # carries them and must keep rebuilding correctly.
                stored = m.get('_tool_results')
                if stored:
                    # Batch turn (2026-07 parallel revamp): exact per-call
                    # results were captured at execution time — no positional
                    # guessing. SLIMMED work pings that follow are pure
                    # duplication of these results and are dropped; the LIVE
                    # ping (still carrying the decision-time instructions) and
                    # any other system entry (reminders, skill prompts) are
                    # injected below like every unconsumed system entry.
                    tool_results = []
                    for k, c in enumerate(calls):
                        content = (stored[k].get('content')
                                   if k < len(stored) else None) or '(no output)'
                        if c.get('name') == 'set_session_name':
                            content = '(session named)'
                        tool_results.append({'id': c['id'], 'name': c['name'],
                                             'content': content})
                    neutral.append({
                        'role': 'assistant',
                        'content': self.tool_manager.strip_tool_calls(
                            m.get('_assistant_text') or '').strip() or None,
                        'tool_calls': [{'id': c['id'], 'name': c['name'],
                                        'arguments': c.get('arguments') or {}} for c in calls],
                    })
                    neutral.append({'role': 'tool', 'results': tool_results})
                    j = i + 1
                    while j < n and entries[j].get('role') == 'system':
                        e = entries[j]
                        _slim_ping = ('_work_output' in e
                                      and not e.get('_is_work_prompt'))
                        if not _slim_ping:
                            c2 = (e.get('content') or '').strip()
                            if c2:
                                c2 = self.tool_manager.strip_tool_calls(c2)
                                if c2.strip():
                                    neutral.append({'role': 'user',
                                                    'content': f'[SYSTEM]: {c2}'})
                        j += 1
                    i = j
                    continue

                # ── Legacy turns (pre-revamp sessions): positional pairing ──
                consumers = [c for c in calls if c.get('name') != 'set_session_name']
                results = []
                j = i + 1
                while (j < n and len(results) < len(consumers)
                        and entries[j].get('role') == 'system'):
                    results.append(entries[j].get('content') or '')
                    j += 1
                _it = iter(results)
                tool_results = []
                for c in calls:
                    if c.get('name') == 'set_session_name':
                        content = '(session named)'
                    else:
                        content = next(_it, '(no output)')
                    tool_results.append({'id': c['id'], 'name': c['name'],
                                         'content': content})
                # _assistant_text is fence-stripped defensively: sessions saved
                # BEFORE the native-purity fix can carry imitation fences the
                # model wrote in free text — those must never go back out.
                neutral.append({
                    'role': 'assistant',
                    'content': self.tool_manager.strip_tool_calls(
                        m.get('_assistant_text') or '').strip() or None,
                    'tool_calls': [{'id': c['id'], 'name': c['name'],
                                    'arguments': c.get('arguments') or {}} for c in calls],
                })
                neutral.append({'role': 'tool', 'results': tool_results})
                i = j
                continue

            content = m.get('content') or ''
            if role == 'assistant':
                content = self.tool_manager.strip_tool_calls(content)
                if content.strip():
                    neutral.append({'role': 'assistant', 'content': content})
            elif role == 'user':
                neutral.append({'role': 'user', 'content': content})
            elif role == 'system':
                # A system entry not consumed as a tool result → inject as a user
                # turn (mirrors _extract_system_and_convo's handling). Fence-
                # stripped: old compat instructional prompts stored in history
                # contain fence EXAMPLES that must not reach the native channel.
                if content.strip():
                    content = self.tool_manager.strip_tool_calls(content)
                    if content.strip():
                        neutral.append({'role': 'user', 'content': f'[SYSTEM]: {content}'})
            i += 1
        return system_prompt, neutral

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
        """Native tool-calling path. Rebuilds the conversation as REAL native
        tool_use / tool_result messages (via native_adapters) so the provider
        never sees reconstructed fence text in the history, then reconstructs the
        equivalent fence text from THIS turn's tool_calls for the display /
        execution pipeline and stashes the structured calls (self._pending_native)
        for _append_assistant to attach to the stored assistant entry."""
        import traceback
        dialect = getattr(module, 'NATIVE_DIALECT', 'openai')
        system_prompt, neutral = self._build_native_convo(messages)
        # Image edge: vision providers build their image message by REPLACING the
        # last message (_build_vision_message → a user turn). If the tail is a tool
        # result (work-mode + a queued context image), replacing it would drop the
        # result and orphan the assistant's tool_call (→ provider 400). Append a
        # placeholder user turn so the provider replaces THAT and the tool result
        # survives, keeping every tool_use answered. (No effect without images.)
        if images and neutral and neutral[-1].get('role') == 'tool':
            neutral.append({'role': 'user',
                            'content': 'Here are the image(s) I attached for this step — please use them.'})
        try:
            convo = na.build_messages(dialect, neutral)
        except Exception as e:
            log.error(f"[AIEngine._provider_script_native] ✗ build_messages({dialect!r}) failed: {e}\n"
                      f"{traceback.format_exc()}")
            # Degrade to plain text turns so the turn still goes through.
            system_prompt, convo = self._extract_system_and_convo(messages)
        # Session naming is the background SessionNamerAgent's job — the
        # set_session_name tool was retired 2026-07-19 (#31).
        tools = self.tool_manager.get_canonical_tools(
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
        raw_calls = result.get('tool_calls') or []
        # ── Native purity: sanitize at the mouth ──────────────────────────────
        # The model's free text must carry NO fence-shaped tool syntax. Any
        # imitation (learned from compat-era docs or old history) is stripped
        # HERE, before storage/display/metadata, so it can never be executed
        # and never leaks back to the API on later turns.
        if text:
            _clean = self.tool_manager.strip_tool_calls(text).strip()
            if _clean != text:
                log.warning(f"[AIEngine._provider_script_native] Model free text "
                            f"contained fence-style tool syntax — stripped "
                            f"({len(text)} → {len(_clean)} chars); native calls "
                            f"are the only tool channel")
                self.tool_manager.approval_signal.system_message.emit(
                    "**NOTICE:** The model wrote fence-style tool text in native "
                    "mode. That text was ignored — native tool calls are the only "
                    "tool channel in this mode.")
                text = _clean
        # ── Single-exec policy on the STRUCTURED calls (never on text) ────────
        # Keep the first exec call, pass non-exec calls through, drop extras.
        # Runs BEFORE fences/metadata are built, so the stored compat view, the
        # native metadata and what actually executes all agree.
        _violated = False
        if raw_calls:
            raw_calls, _violated, _ = \
                self.tool_manager.enforce_single_exec_policy_native(raw_calls)
        # Debug observability: record what actually went over the wire this turn
        # (the dialect-shaped payload + the raw normalized result) and the tool
        # transport, so the Debug window can show native-vs-plain + the structure.
        self.last_tool_transport = 'native'
        self.last_native_tool_calls = len(raw_calls)
        self.last_sent_messages = convo
        self.last_raw_provider_result = result
        # Stash the structured calls (with stable ids) so _append_assistant can
        # attach them to the stored assistant entry as native metadata. Only when
        # there ARE calls — a plain text turn leaves _pending_native None.
        if raw_calls:
            self._pending_native = {
                'text': text,
                'violation': _violated,
                'tool_calls': [
                    {'id': c.get('id') or na._new_call_id(),
                     'name': c.get('name', ''),
                     'arguments': c.get('arguments') or {}}
                    for c in raw_calls
                ],
            }
        # Single-source the user-facing narration: if the model wrote free text
        # this turn, that IS the reply — suppress each call's message_to_user so
        # it isn't rendered a second time (the native double-speak bug). Only when
        # there's no free text do we fall back to message_to_user.
        fences = self.tool_manager.tool_calls_to_fences(raw_calls, include_messages=not bool(text))
        ai_text = (text + ("\n\n" if text and fences else "") + fences).strip()
        log.info(f"[AIEngine._provider_script_native] ✓ native result | text_len={len(text)} | "
                 f"tool_calls={len(raw_calls)} | reconstructed_len={len(ai_text)}")
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
        # Debug observability: the compat wire payload (fenced text turns).
        self.last_sent_messages = convo

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

        self.last_raw_provider_result = result
        log.info(f"[AIEngine._http_custom_script] ✓ Response | length={len(result)} chars")
        return result

    def _call_provider(self, image=None, images=None) -> str | None:
        """Build the unified message list and dispatch to the active provider script.
        The 'manual' provider is the only built-in special case.
        All other providers are loaded from the custom_script_path.
        """
        if images is None and image is not None:
            images = [image]

        # Fresh per turn — only the native path (with tool_calls) sets this; a
        # compat or text-only turn leaves it None so no stale native metadata
        # attaches to the next assistant entry.
        self._pending_native = None
        # Fresh per turn as well: the manual provider returns before the
        # custom-script labeling below, and the response processors route on
        # this flag — a stale 'native' from a previous turn must never leak.
        self.last_tool_transport = 'compat'

        log.debug(f"[AIEngine._call_provider] script='{self.custom_script_path}' | "
                  f"images={len(images) if images else 0}")
        messages = self._build_messages()

        try:
            from systema.common.token_est import log_tokens, estimate_history_tokens
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

            # Debug transport label (the native path sets its own 'native' label;
            # here we only label the compat / native-unsupported-fallback cases).
            if _native_module is None:
                self.last_tool_transport = ('native→compat (provider lacks native support)'
                                            if self._tool_mode() == 'native' else 'compat')
                self.last_native_tool_calls = 0

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
                    from systema.common.token_est import log_output_tokens, estimate_tokens
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
            from systema.common.token_est import log_tokens, estimate_history_tokens
            log_tokens(estimate_history_tokens(messages))
        except Exception:
            pass
        result = self._provider_script(messages)
        if result:
            try:
                from systema.common.token_est import log_output_tokens, estimate_tokens
                log_output_tokens(estimate_tokens(result))
            except Exception:
                pass
        return result

    def _make_error_result(self, message="Error: No response from AI") -> dict:
        """Return a standard error result dict."""
        return {
            'response': message,
            'has_work_call': False,
            'is_working': False,
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

        # User message FIRST, then the memory ui_event — replay renders history
        # in order, and the recall card belongs to the AI turn that FOLLOWS the
        # user message (storing it before the user message parked the card in
        # the previous turn's bubble on session reload).
        self.conversation_history.append({'role': 'user', 'content': user_message})
        self._inject_memories(user_message)
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

        # Same ordering rule as generate_response: user message first, then the
        # memory ui_event, so reload puts the recall card in the right turn.
        self.conversation_history.append({'role': 'user', 'content': user_message})
        self._inject_memories(user_message)
        log.debug(f"[AIEngine.generate_response_with_image] User message appended | "
                  f"history_len={len(self.conversation_history)}")

        ai_text = self._call_provider(images=image_paths)
        if not ai_text:
            log.error("[AIEngine.generate_response_with_image] ✗ No AI text returned")
            return self._make_error_result("Error: No response from provider")

        log.info(f"[AIEngine.generate_response_with_image] ✓ Got response | "
                 f"length={len(ai_text)} chars | → _process_ai_response()")
        return self._process_ai_response(ai_text)

    def continue_work(self):
        """Continue work mode execution with AI analyzing previous output."""
        log.info(f"[AIEngine.continue_work] is_working={self.tool_manager.work.is_working} | "
                 f"provider='{self.ai_provider}'")
        if not self.tool_manager.work.is_working:
            log.warning("[AIEngine.continue_work] Not in work mode — returning early")
            return self._make_error_result("Not in work mode")

        # Slim down the previous work-mode system message so only the latest ping
        # carries the full instruction block — everything before becomes output-only.
        for entry in reversed(self.conversation_history):
            if entry.get('role') == 'system' and entry.get('_is_work_prompt'):
                prev_output = entry.get('_work_output', '')
                entry['content'] = WORK_MODE_OUTPUT_ONLY_PROMPT.format(work_output=prev_output)
                del entry['_is_work_prompt']
                break

        # Capture output now — before get_work_prompt() — to store alongside the entry
        _current_work_output = self.tool_manager.work.last_output or "No previous output"
        work_prompt = self.tool_manager.get_work_prompt()
        self.conversation_history.append({
            'role': 'system',
            'content': work_prompt,
            '_is_work_prompt': True,      # marks this as the "live" work ping
            '_work_output': _current_work_output,  # stored so we can slim it next iteration
        })
        log.debug(f"[AIEngine.continue_work] Work mode prompt appended (full) | "
                  f"prompt_len={len(work_prompt)} | history_len={len(self.conversation_history)}")

        # Ephemeral image context: if the AI queued image(s) via
        # attach_image_to_context() during the step it just ran, feed them to the
        # provider's chat_image() for THIS one continuation call, then delete the
        # files. They are never pinned to the chat or written into history, so the
        # tokens are spent exactly once (same contract as a task session).
        _pending = self._drain_context_images()
        if _pending:
            log.info(f"[AIEngine.continue_work] Feeding {len(_pending)} ephemeral "
                     "context image(s) to the provider for this turn only")
            ai_text = self._call_provider(images=_pending)
            import os as _os
            for _p in _pending:
                try:
                    _os.remove(_p)
                except Exception:
                    pass
        else:
            ai_text = self._call_provider()
        if not ai_text:
            log.error("[AIEngine.continue_work] ✗ No AI text returned")
            return self._make_error_result(f"Error: No response from {self.ai_provider} provider")

        log.info(f"[AIEngine.continue_work] ✓ Got response | length={len(ai_text)} chars | "
                 f"→ _process_work_response()")
        return self._process_work_response(ai_text)

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
    # RESPONSE PROCESSING
    # Two front-ends, one pipeline: native turns dispatch their STRUCTURED
    # tool calls (never fence parsing); compat turns run the fence machinery.
    # Both feed the same handlers and return the same result-dict shape.
    # ═══════════════════════════════════════════════════════════════════════════

    def _native_text_turn(self, ai_text, display, in_work: bool,
                          session_name=None, appended: bool = False):
        """A native turn that ends with plain text (no exec call): a normal
        chat reply, or — mid-work — the finish report. Never fence-parsed;
        the text was already sanitized at the provider mouth."""
        if not in_work:
            if not appended:
                self._append_assistant(ai_text)
            _mc = getattr(self, '_pending_memory_widget', None)
            self._pending_memory_widget = None  # consume — prevent stale reuse
            return {
                'response': display,
                'has_work_call': False,
                'is_working': False,
                'thinking': False,
                'session_name': session_name,
                'memory_context': _mc,
            }

        # Work mode: a reply with no exec call IS the finish report.
        log.info("[AIEngine._native_text_turn] No exec call — work finished "
                 "(skills persist)")
        self.tool_manager.work.is_working = False
        self._slim_last_work_ping()

        if not display or not display.strip():
            # Empty finish → ask for a real summary (same guard as compat).
            log.warning("[AIEngine._native_text_turn] Finished work with an "
                        "empty reply — injecting summary reminder")
            if not appended and ai_text and ai_text.strip():
                self._append_assistant(ai_text)
            self.conversation_history.append(
                {'role': 'system', 'content': EMPTY_EXIT_SUMMARY_PROMPT})
            ai_text2 = self._call_provider()
            if ai_text2:
                _r = self._process_ai_response(ai_text2)
                if session_name and not _r.get('session_name'):
                    _r['session_name'] = session_name
                return _r
            return {
                'response': "",
                'has_work_call': False,
                'is_working': False,
                'thinking': False,
                'session_name': session_name,
                'finished_working': True,
            }

        if not appended:
            self._append_assistant(ai_text)
        return {
            'response': display,
            'has_work_call': False,
            'is_working': False,
            'thinking': False,
            'session_name': session_name,
            'finished_working': True,
        }

    def _process_native_response(self, ai_text, in_work: bool):
        """Process a NATIVE-transport turn from its structured tool calls —
        the native counterpart of _process_ai_response / _process_work_response.

        NO fence parsing happens here: the provider's tool_calls (stashed in
        self._pending_native by _provider_script_native) are dispatched
        straight to the same handlers the compat parsers feed
        (run_python_interpreter, run_read_file/…, the skill manager, session
        naming). The reconstructed fence text in ai_text is STORAGE/DISPLAY
        ONLY — it is never parsed, so fence-shaped text can never execute in
        native mode, and the compat guard rails (fence recovery, typo
        tolerance, malformed-step retry) stay compat-only. Returns the same
        result-dict shape as the compat processors, so the work loop and the
        UI see no difference.
        """
        tm = self.tool_manager
        pend = self._pending_native or {}
        calls = pend.get('tool_calls') or []
        text = (pend.get('text') or '').strip()
        log.info(f"[AIEngine._process_native_response] ── "
                 f"{'work' if in_work else 'chat'} turn | "
                 f"calls={[c.get('name') for c in calls]} | "
                 f"text_len={len(text)} ──")

        # Violation on the structured calls (extras already pruned by
        # _provider_script_native) → teach the model. Same placement as
        # compat: the correction precedes the stored assistant turn.
        if pend.get('violation'):
            from systema.engine.prompts.global_instructions import (
                EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
            self.conversation_history.append({
                'role': 'system',
                'content': EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE})

        if not in_work and self.tool_execution_lockout:
            self._append_assistant(ai_text)
            return {
                'response': ai_text,
                'has_work_call': False,
                'is_working': False,
                'thinking': False,
                'session_name': None,
            }

        # ── Plain text turn (no structured calls) ────────────────────────────
        if not calls:
            return self._native_text_turn(ai_text, ai_text, in_work)

        # (then_tool chaining RETIRED 2026-07-19 — superseded by parallel
        # calls. A stale then_tool argument on a call is simply ignored.)

        # set_session_name tool RETIRED (#31): drop hallucinated naming calls
        # from the batch AND the stored metadata (a call absent from metadata
        # needs no tool_result on rebuild).
        session_name = None
        calls = [c for c in calls if c.get('name') != 'set_session_name']
        pend['tool_calls'] = calls
        if not calls:
            return self._native_text_turn(ai_text, text, in_work,
                                          session_name=session_name)

        # Store the assistant turn ONCE, up front (fences for the compat view
        # + native metadata). Execution order == emitted order (2026-07
        # revamp); exact per-call results land in entry['_tool_results'].
        entry = self._append_assistant(ai_text)

        # Convert structured calls → the shared batch shape. Conversion emits
        # the same UI side effects as the compat parsers (the Working banner);
        # unknown/retired names pass spec=None and produce corrective
        # observations inside the batch loop.
        batch = []
        for call in calls:
            name = call.get('name') or ''
            known = name in tm._tool_keys
            spec = (tm.native_args_to_spec(name, call.get('arguments') or {})
                    if known else None)
            batch.append({'tool': name, 'spec': spec,
                          'call_id': call.get('id')})

        # Bare/empty interpreter-only turn — never executed; fold into the
        # finish path (same safety net as compat).
        if (len(batch) == 1 and batch[0]['tool'] == 'python_interpreter'
                and str(batch[0]['spec'] or '').strip().lower() in ('exit', '')):
            log.warning("[AIEngine._process_native_response] Bare exit/empty "
                        "python_interpreter call — finishing")
            tm.work.is_working = False
            r = self._native_text_turn(ai_text, text, in_work,
                                       session_name=session_name,
                                       appended=True)
            r.setdefault('finished_working', True)
            return r

        return self._run_tool_batch(ai_text, batch, text, session_name,
                                    entry=entry, in_work=in_work)

    def _emit_work_narration(self, text):
        """Surface a work step's narration to the chat BEFORE its tool executes,
        so the text bubble renders AHEAD of the live tool card (correct visual
        order). Emitted from the worker thread via a queued signal; the controller
        shows it on the GUI thread. Returns True when something real was emitted —
        the caller then sets result['narration_shown'] so the worker-return handler
        does not show the same text a second time."""
        t = (text or '').strip()
        if not t or t in ('Working...', 'Working…') \
                or t.startswith(('Loading skill:', 'Unloading skill:')):
            return False
        try:
            self.tool_manager.approval_signal.work_narration.emit(t)
            return True
        except Exception:
            return False

    def _process_ai_response(self, ai_text):
        """
        Process AI response and handle python_interpreter / file / skill calls.

        Returns:
            dict: Response data with execution status
        """
        log.info(f"[AIEngine._process_ai_response] ── Processing response | "
                 f"length={len(ai_text)} chars ──")
        log.debug(f"[AIEngine._process_ai_response] Preview: '{ai_text[:120].replace(chr(10), '↵')}'")
        self._clear_memory_context()
        self.last_raw_response = ai_text

        # ── Native transport → structured dispatch, never fence parsing ──────────
        # Exact match on 'native': the 'native→compat (…)' fallback label means
        # the turn traveled as fenced text and must run the compat machinery.
        if getattr(self, 'last_tool_transport', 'compat') == 'native':
            return self._process_native_response(ai_text, in_work=False)

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
            from systema.engine.prompts.global_instructions import (
                EXEC_CODE_TOOLCALL_VIOLATION_PROMPT,
                EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
            _viol = (EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE
                     if self._tool_mode() == 'native'
                     else EXEC_CODE_TOOLCALL_VIOLATION_PROMPT)
            # Append directly to conversation_history so the AI carries this
            # correction forward in ALL future turns, not just the next one.
            self.conversation_history.append({
                'role': 'system',
                'content': _viol
            })
        # ──────────────────────────────────────────────────────────────────────────

        if self.tool_execution_lockout:
            self._append_assistant(ai_text)
            return {
                'response': ai_text,
                'has_work_call': False,
                'is_working': False,
                'thinking': False,
                'session_name': None
            }

        # set_session_name tool RETIRED (#31, 2026-07-19) — naming is the
        # background SessionNamerAgent's job. A stale model-emitted naming
        # fence is legacy-stripped for display; nothing is parsed here.
        session_name = None
        _display_text = ai_text

        # ── Parallel tool batch (2026-07 revamp): every tool fence in the
        # reply, in written order — executed sequentially, one combined
        # observation. Skills, file ops and (at most one) python_interpreter
        # all ride the same batch.
        batch, _rem = self.tool_manager.parse_tool_batch(_display_text)
        if batch:
            visible_text = self.tool_manager.strip_tool_calls(_rem).strip()
            # A bare `exit`/empty interpreter-only turn is not real code — the
            # exit sentinel was removed (work mode ends on a no-tool reply).
            # Fold it into an immediate finish (safety net for resumed sessions).
            if (len(batch) == 1 and batch[0]['tool'] == 'python_interpreter'
                    and str(batch[0]['spec'] or '').strip().lower() in ('exit', '')):
                log.warning("[AIEngine._process_ai_response] Bare exit/empty work-call on entry — finishing")
                self.tool_manager.work.is_working = False
                if ai_text and ai_text.strip():
                    self._append_assistant(ai_text)
                return {
                    'response': visible_text,
                    'has_work_call': False,
                    'is_working': False,
                    'thinking': False,
                    'finished_working': True,
                    'session_name': session_name
                }
            return self._run_tool_batch(ai_text, batch, visible_text,
                                        session_name)

        # ── Legacy execute_code emission — the tool is RETIRED ────────────────
        # workmode is the only code path now. The call is NOT run: strip it,
        # notify the user, teach the model, and follow up once so the user is
        # not left with a half reply.
        if self.tool_manager._parse_fence(_display_text, 'execute_code'):
            log.warning("[AIEngine._process_ai_response] Retired execute_code "
                        "emission — call NOT run; injecting corrective prompt")
            self.tool_manager.approval_signal.system_message.emit(
                "**NOTICE:** The model attempted the retired `execute_code` tool; "
                "the call was NOT run. It has been told to use python_interpreter.")
            from systema.engine.prompts.global_instructions import (
                EXECUTE_CODE_RETIRED_PROMPT, EXECUTE_CODE_RETIRED_PROMPT_NATIVE)
            _retired = (EXECUTE_CODE_RETIRED_PROMPT_NATIVE
                        if self._tool_mode() == 'native'
                        else EXECUTE_CODE_RETIRED_PROMPT)
            self._append_assistant(ai_text)
            self.conversation_history.append({'role': 'system', 'content': _retired})
            _followup = self._call_provider()
            if _followup:
                _r = self._process_ai_response(_followup)
                if session_name and not _r.get('session_name'):
                    _r['session_name'] = session_name
                return _r
            return {
                'response': self.tool_manager.strip_tool_calls(ai_text),
                'has_work_call': False,
                'is_working': False,
                'thinking': False,
                'session_name': session_name,
            }

        # No execution calls - normal response
        log.info(f"[AIEngine._process_ai_response] No tool calls detected — normal text response | "
                 f"length={len(ai_text)} chars")
        self._append_assistant(ai_text)
        log.debug("assistant response appended to history | "
                  f"total history entries={len(self.conversation_history)}")

        # Safety net: strip any remaining tool call from display text
        display_text = self.tool_manager.strip_tool_calls(ai_text)
        if display_text != ai_text:
            log.warning(f"[AIEngine._process_ai_response] Safety net stripped residual tool call | "
                        f"before={len(ai_text)} → after={len(display_text)} chars")

        _mc = getattr(self, '_pending_memory_widget', None)
        self._pending_memory_widget = None  # consume — prevent stale reuse in recursive calls
        return {
            'response': display_text,
            'has_work_call': False,
            'is_working': False,
            'thinking': False,
            'session_name': session_name,
            'memory_context': _mc,
        }

    # (_dispatch_file_tool / _run_file_tool_step RETIRED 2026-07-19 — file ops
    # are batch calls now; _run_tool_batch is the one execution path.)

    def _run_tool_batch(self, ai_text, batch, visible, session_name,
                        entry=None, in_work=False):
        """Execute an ordered BATCH of tool calls (2026-07 parallel revamp).

        Emission is parallel; execution is strictly SEQUENTIAL in emitted
        order — each call runs to completion (its own approval dialog, card,
        journal entry) before the next starts, and the model receives ONE
        combined observation on the next work ping (work.last_output). Shared
        by the compat fence front-end and the native structured front-end.

        batch: [{'tool': name, 'spec': parsed spec/code/skill name,
                 'call_id': native call id or None}] — spec None for
                unknown/retired names (they produce corrective observations).
        entry: the already-stored assistant history entry (native path stores
               it up front); None -> stored here (compat path).
        Interrupt: work.batch_abort (set by interrupt_running_code) skips the
        not-yet-run calls with an explanatory observation."""
        tm = self.tool_manager
        tm.work.is_working = True
        tm.work.batch_abort = False
        self._malformed_step_retries = 0
        _narrated = self._emit_work_narration(visible)
        if entry is None:
            entry = self._append_assistant(ai_text)

        run_file = {'read_file': tm.run_read_file, 'edit_file': tm.run_edit_file,
                    'write_file': tm.run_write_file, 'grep': tm.run_grep}
        results = []          # (tool_name, observation) in run order
        loaded_skill = None
        unloaded_skill = None
        file_ops = []
        last_tool = None
        last_code = None

        for call in batch:
            name = call['tool']
            spec = call['spec']
            if tm.work.batch_abort:
                obs = ("SKIPPED — the user interrupted this batch before this "
                       "call ran. Re-issue it later if still needed.")
                results.append((name, obs))
                continue
            try:
                if name == 'python_interpreter':
                    code = str(spec or '')
                    if not code.strip() or code.strip().lower() == 'exit':
                        obs = "(empty python_interpreter call — nothing was run)"
                    else:
                        obs = tm.run_python_interpreter(code)
                        last_code = code
                    last_tool = 'interpreter'
                elif name in run_file:
                    obs = run_file[name](spec)
                    last_tool = name
                    jid = getattr(tm, 'last_file_op_journal_id', None)
                    if name not in ('read_file', 'grep'):
                        file_ops.append({'tool': name,
                                         'path': (spec or {}).get('path', ''),
                                         'journal_id': jid})
                        tm.last_file_op_journal_id = None
                elif name == 'load_skill':
                    if self.skill_manager:
                        ok, msg = self.skill_manager.load_skill(spec)
                    else:
                        ok, msg = False, "ERROR: No skill manager"
                    if ok:
                        loaded_skill = spec
                        obs = f"Skill '{spec}' loaded into your system context."
                    else:
                        obs = (f"SKILL LOAD REJECTED: '{spec}' — {msg}. "
                               f"Do NOT retry; continue with your task.")
                    last_tool = 'skill'
                elif name == 'unload_skill':
                    if self.skill_manager:
                        ok, msg = self.skill_manager.unload_skill(spec)
                    else:
                        ok, msg = False, "ERROR: No skill manager"
                    if ok:
                        unloaded_skill = spec
                        obs = f"Skill '{spec}' unloaded from your system context."
                    else:
                        obs = (f"SKILL UNLOAD REJECTED: '{spec}' — {msg}. "
                               f"Do NOT retry; continue with your task.")
                    last_tool = 'skill'
                elif tm._legacy_keys_norm.get(tm._norm_key(name)):
                    obs = (f"The tool '{name}' is RETIRED and was NOT run. "
                           f"python_interpreter is the only way to run code; "
                           f"re-issue the action through an available tool.")
                    tm.approval_signal.system_message.emit(
                        f"**NOTICE:** The model attempted the retired `{name}` "
                        f"tool; the call was NOT run.")
                else:
                    obs = (f"Tool '{name}' does not exist and was NOT run. "
                           f"Available tools: {', '.join(tm._tool_keys)}.")
            except Exception as e:
                obs = f"ERROR: {type(e).__name__}: {e}"
                log.error(f"[AIEngine._run_tool_batch] '{name}' raised: {e}")
            results.append((name, obs))

        # Session-persisted file-op records (reload maps edits → journal ids).
        if file_ops:
            entry['_file_op'] = file_ops[-1]     # single-op key: backward compat
            if len(file_ops) > 1:
                entry['_file_ops'] = file_ops

        # Native pairing metadata: exact per-call results, stored in call order
        # (execution order == emitted order). _build_native_convo prefers these
        # over positional consumption for batch turns.
        if entry.get('_tool_calls'):
            entry['_tool_results'] = [
                {'id': (batch[k].get('call_id') if k < len(batch) else None),
                 'name': nm, 'content': ob}
                for k, (nm, ob) in enumerate(results)]

        # ONE combined observation. A single call keeps its raw output (the
        # pre-revamp shape); multi-call batches get labeled sections.
        if len(results) == 1:
            combined = results[0][1]
        else:
            combined = "\n\n".join(
                f"=== Result {k}/{len(results)} — {nm} ===\n{ob}"
                for k, (nm, ob) in enumerate(results, 1))
        tm.work.last_output = combined
        tm.work.last_tool = last_tool or 'batch'
        self._inject_pending_format_reminder()
        log.info(f"[AIEngine._run_tool_batch] ✓ {len(results)} call(s) done | "
                 f"combined_obs={len(combined)} chars | "
                 f"aborted={tm.work.batch_abort}")
        result = {
            'response': visible if visible else "Working...",
            'has_work_call': True,
            'is_working': True,
            'thinking': True,
            'session_name': session_name,
            'skill_loaded': loaded_skill,
            'skill_unloaded': unloaded_skill,
            'narration_shown': _narrated,
        }
        if last_code is not None:
            result['code'] = last_code
        return result

    def _inject_pending_format_reminder(self):
        """A parse auto-fix queued a format reminder — deliver it to the model
        as a role:system HISTORY entry. (It used to be prefixed inside the tool
        result, where weak models ignored it.)"""
        reminder = getattr(self.tool_manager, '_pending_format_reminder', None)
        if reminder:
            self.tool_manager._pending_format_reminder = None
            self.conversation_history.append({'role': 'system', 'content': reminder})
            log.debug("[AIEngine._inject_pending_format_reminder] Format reminder "
                      "appended to history as a system entry")

    # Fence tags that mean "this is executable-looking code, not a tool call".
    _SUSPECT_CODE_TAGS = frozenset(
        {'', 'python', 'py', 'python3', 'bash', 'sh', 'shell', 'powershell', 'cmd'})

    def _suspect_malformed_work_step(self, text):
        """Heuristic: is this mid-work-mode reply almost certainly a FAILED tool
        call rather than a finish report? True for a code fence with (near-)no
        prose around it, or a bare reply that compiles as multi-line Python.
        Real reports (prose around snippets) are never flagged. Never executes
        anything — the caller injects a corrective prompt instead."""
        if not text or not text.strip():
            return False
        fences = list(re.finditer(r'```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```',
                                  text, re.DOTALL))
        if fences:
            if not any(f.group(1).strip().lower() in self._SUSPECT_CODE_TAGS
                       for f in fences):
                return False
            prose = re.sub(r'```.*?```', '', text, flags=re.DOTALL).strip()
            return len(prose) < 200
        # No fences: bare code dump? Prose virtually never compiles as Python.
        lines = [ln for ln in text.strip().splitlines() if ln.strip()]
        if len(lines) < 2:
            return False
        try:
            compile(text.strip(), '<reply>', 'exec')
        except (SyntaxError, ValueError):
            return False
        return any(marker in text for marker in ('import ', 'def ', '=', '('))

    def _slim_last_work_ping(self):
        """Slim the last live work-mode ping down to its output-only form on
        work exit — it was never slimmed by continue_work() because no next
        ping came after it."""
        for entry in reversed(self.conversation_history):
            if entry.get('role') == 'system' and entry.get('_is_work_prompt'):
                prev_output = entry.get('_work_output', '')
                entry['content'] = WORK_MODE_OUTPUT_ONLY_PROMPT.format(
                    work_output=prev_output)
                del entry['_is_work_prompt']
                log.debug("[AIEngine._slim_last_work_ping] Last work-mode ping "
                          "slimmed on exit")
                break

    def _process_work_response(self, ai_text):
        """Process AI response while in work mode."""
        log.info(f"[AIEngine._process_work_response] Processing work mode response | "
                 f"length={len(ai_text)} chars")
        log.debug(f"[AIEngine._process_work_response] Preview: "
                  f"'{ai_text[:100].replace(chr(10), '↵')}'")
        self.last_raw_response = ai_text

        # ── Native transport → structured dispatch, never fence parsing ──────────
        if getattr(self, 'last_tool_transport', 'compat') == 'native':
            return self._process_native_response(ai_text, in_work=True)

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
            from systema.engine.prompts.global_instructions import (
                EXEC_CODE_TOOLCALL_VIOLATION_PROMPT,
                EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE)
            _viol = (EXEC_CODE_TOOLCALL_VIOLATION_PROMPT_NATIVE
                     if self._tool_mode() == 'native'
                     else EXEC_CODE_TOOLCALL_VIOLATION_PROMPT)
            # Appended directly — the old _pending_exec_violation_prompt stash
            # was never consumed anywhere, so work-mode violations silently
            # vanished instead of teaching the model.
            self.conversation_history.append({'role': 'system', 'content': _viol})
        # ──────────────────────────────────────────────────────────────────────────

        log.debug("[AIEngine._process_work_response] Checking for consecutive python_interpreter call...")

        # set_session_name tool RETIRED (#31, 2026-07-19) — naming is the
        # background SessionNamerAgent's job; a stale fence is legacy-stripped.
        session_name = None
        _display_text = ai_text

        # ── Parallel tool batch (2026-07 revamp): mid-work turns batch the
        # same way — skills, file ops and (at most one) python_interpreter run
        # sequentially off one reply, one combined observation.
        batch, _rem = self.tool_manager.parse_tool_batch(_display_text)

        if batch:
            visible_text = self.tool_manager.strip_tool_calls(_rem).strip()

            # Bare `exit`/empty interpreter-only turn → the exit sentinel was
            # removed, so fold it into the finish path (never executed). Normal
            # finishing is a reply with no work call at all (the else-branch).
            if (len(batch) == 1 and batch[0]['tool'] == 'python_interpreter'
                    and str(batch[0]['spec'] or '').strip().lower() in ('exit', '')):
                log.info("[AIEngine._process_work_response] Bare exit/empty work-call — "
                         "finishing work mode (skills persist)")
                self.tool_manager.work.is_working = False

                # Slim down the last work-mode ping now that work mode is exiting.
                self._slim_last_work_ping()

                if ai_text and ai_text.strip():
                    self._append_assistant(ai_text)
                    log.debug("[AIEngine._process_work_response] ai_text appended to history (exit)")

                # ── Empty-exit guard ──────────────────────────────────────────
                if not visible_text or not visible_text.strip():
                    log.warning("[AIEngine._process_work_response] Exit without summary detected — "
                                "injecting reminder and re-calling provider")
                    self.conversation_history.append({'role': 'system', 'content': EMPTY_EXIT_SUMMARY_PROMPT})
                    ai_text2 = self._call_provider()
                    if ai_text2:
                        return self._process_ai_response(ai_text2)
                    return {
                        'response': "",
                        'has_work_call': False,
                        'is_working': False,
                        'thinking': False,
                        'session_name': session_name,
                        'finished_working': True
                    }
                # ─────────────────────────────────────────────────────────────

                return {
                    'response': visible_text,
                    'has_work_call': False,
                    'is_working': False,
                    'thinking': False,
                    'session_name': session_name,
                    'finished_working': True
                }

            log.debug("[AIEngine._process_work_response] → _run_tool_batch() "
                      f"({len(batch)} call(s))")
            return self._run_tool_batch(ai_text, batch, visible_text,
                                        session_name, in_work=True)

        else:
            # ── Legacy execute_code on exit — the tool is RETIRED ─────────────
            # The call is NOT run. Keep the work loop alive with a corrective
            # prompt so the model re-issues the action via python_interpreter.
            if self.tool_manager._parse_fence(_display_text, 'execute_code'):
                log.warning("[AIEngine._process_work_response] Retired "
                            "execute_code emission in work mode — call NOT run")
                self.tool_manager.approval_signal.system_message.emit(
                    "**NOTICE:** The model attempted the retired `execute_code` tool; "
                    "the call was NOT run. It has been told to use python_interpreter.")
                from systema.engine.prompts.global_instructions import (
                    EXECUTE_CODE_RETIRED_PROMPT, EXECUTE_CODE_RETIRED_PROMPT_NATIVE)
                _retired = (EXECUTE_CODE_RETIRED_PROMPT_NATIVE
                            if self._tool_mode() == 'native'
                            else EXECUTE_CODE_RETIRED_PROMPT)
                self._append_assistant(ai_text)
                self.conversation_history.append({'role': 'system', 'content': _retired})
                return {
                    'response': self.tool_manager.strip_tool_calls(ai_text),
                    'has_work_call': True,
                    'is_working': True,
                    'thinking': True,
                    'session_name': session_name,
                }

            # ── Suspected malformed code step — self-retry guard rail ─────────
            # A mid-work reply that is essentially just code (a ```python fence
            # with no surrounding prose, or a bare compiling Python body) is a
            # failed tool call, not a report. NEVER guess-execute it: keep the
            # loop alive with a corrective prompt so the model re-issues the
            # step via python_interpreter (or truly finishes with a prose report).
            if self._suspect_malformed_work_step(_display_text):
                retries = getattr(self, '_malformed_step_retries', 0)
                if retries < 2:
                    self._malformed_step_retries = retries + 1
                    log.warning(f"[AIEngine._process_work_response] Suspected malformed "
                                f"interpreter step (code-shaped reply, no valid tool call) — "
                                f"corrective retry {retries + 1}/2, nothing was run")
                    if retries == 0:   # NOTICE once per interpreter session; retry 2 logs only
                        self.tool_manager.approval_signal.system_message.emit(
                            "**NOTICE:** The model replied with code that is not a valid "
                            "tool call; nothing was run. It has been asked to re-issue "
                            "the step as a python_interpreter call.")
                    from systema.engine.prompts.global_instructions import (
                        MALFORMED_WORK_STEP_PROMPT, MALFORMED_WORK_STEP_PROMPT_NATIVE)
                    _malformed = (MALFORMED_WORK_STEP_PROMPT_NATIVE
                                  if self._tool_mode() == 'native'
                                  else MALFORMED_WORK_STEP_PROMPT)
                    self._append_assistant(ai_text)
                    self.conversation_history.append({'role': 'system', 'content': _malformed})
                    return {
                        'response': "Working...",
                        'has_work_call': True,
                        'is_working': True,
                        'thinking': True,
                        'session_name': session_name,
                    }
                log.warning("[AIEngine._process_work_response] Still code-shaped after "
                            "2 corrective retries — letting it through as the report")

            log.info("[AIEngine._process_work_response] No more python_interpreter calls — "
                     "AI is done, clearing work.is_working (skills persist)")
            self.tool_manager.work.is_working = False

            # Slim down the last work-mode ping now that work mode is exiting.
            self._slim_last_work_ping()

            # ── Empty-finish guard ── the visible reply (no tool call) IS the
            # report; an empty one means the user sees nothing, so prompt for a
            # real summary. Judge emptiness on the stripped display text.
            if not _display_text or not _display_text.strip():
                log.warning("[AIEngine._process_work_response] Finished work mode with an empty reply — "
                            "injecting summary reminder and re-calling provider")
                # Persist a name-only turn (its fence + native metadata) so the
                # name survives even though we re-prompt for the missing summary.
                if ai_text and ai_text.strip():
                    self._append_assistant(ai_text)
                self.conversation_history.append({'role': 'system', 'content': EMPTY_EXIT_SUMMARY_PROMPT})
                ai_text2 = self._call_provider()
                if ai_text2:
                    _r = self._process_ai_response(ai_text2)
                    if session_name and not _r.get('session_name'):
                        _r['session_name'] = session_name
                    return _r
                return {
                    'response': "",
                    'has_work_call': False,
                    'is_working': False,
                    'thinking': False,
                    'session_name': session_name,
                    'finished_working': True,
                }

            self._append_assistant(ai_text)
            log.debug("[AIEngine._process_work_response] Normal response appended to history | "
                      f"total entries={len(self.conversation_history)}")

            # finished_working=True is REQUIRED — it stops the controller's
            # work_timer and fires the exit UI. This is now the primary finish
            # path (the explicit exit sentinel was removed).
            return {
                'response': self.tool_manager.strip_tool_calls(ai_text),
                'has_work_call': False,
                'is_working': False,
                'session_name': session_name,
                'thinking': False,
                'finished_working': True,
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
        """Build the memory block string from current memories, or None if not
        applicable. Newest memories first; total size is hard-capped by the
        memory_inject_cap_tokens setting (approx 4 chars/token).

        NOTE: no embedding-readiness gate — inject_all needs zero embeddings, so
        get_all() serving from the store (empty until the store loads) is the
        only gate. A missing/failed fastembed must not silently kill inject_all."""
        if not self.memory_manager:
            return None

        memory_enabled = True
        memory_recall_mode = 'inject_all'
        cap_tokens = 5000
        if self.settings_callback:
            settings = self.settings_callback()
            memory_enabled = settings.get('memory_enabled', True)
            memory_recall_mode = settings.get('memory_recall_mode', 'inject_all')
            try:
                cap_tokens = int(settings.get('memory_inject_cap_tokens', 5000))
            except (TypeError, ValueError):
                cap_tokens = 5000
        if not memory_enabled or memory_recall_mode != 'inject_all':
            return None

        all_memories = self.memory_manager.get_all()
        if not all_memories:
            log.debug("[AIEngine._build_memory_block] No memories to inject")
            return None

        # get_all() is sorted newest-first — keep the newest within budget.
        # The newest memory is always included, even if it alone exceeds the cap.
        summary = summarize_memory_injection(all_memories, cap_tokens)
        lines = list(summary['entries'])
        if summary['omitted'] > 0:
            lines.append(f"[{summary['omitted']} older memories omitted - use search_memory()]")
            log.info(f"[AIEngine._build_memory_block] Cap {cap_tokens} tokens reached — "
                     f"{summary['omitted']} older memories omitted")

        joined = "\n".join(lines)
        return (
            f"\n\n{self._MEM_BLOCK_START}\n\n"
            f"ALL MEMORIES:\n"
            f"{joined}\n"
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
        memory_max = 3

        if self.settings_callback:
            settings = self.settings_callback()
            memory_enabled = settings.get('memory_enabled', True)
            memory_recall_mode = settings.get('memory_recall_mode', 'inject_all')
            memory_threshold = float(settings.get('memory_threshold', 0.4))
            memory_max = int(settings.get('memory_max_results', 3))

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
            # Dicts carry the card's metadata (date + score); the card also
            # accepts plain strings so pre-upgrade sessions still replay fine.
            mem_dicts = [
                {'text': m['text'],
                 'created_at': m.get('created_at', ''),
                 'similarity': m.get('similarity', 0.0)}
                for m in recalled
            ]
            # Append as a persistent ui_event — saved with session, filtered from API
            # by _get_history_with_memory which promotes it to a system message inline
            self.conversation_history.append({
                'role': 'ui_event',
                '_type': 'memory_context',
                '_memory_context_id': context_id,
                'content': context_text,
                '_memories_preview': mem_dicts,
            })
            log.info(f"[AIEngine._inject_memories] Stored {len(recalled)} memories as "
                     f"persistent ui_event | id={context_id}")
            # Store for result dict so controller can show the widget on the main thread
            self._pending_memory_widget = (context_id, mem_dicts)
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
                 f"is_working={self.tool_manager.work.is_working}")
        self.conversation_history = []
        self.tool_manager.work.reset()
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
