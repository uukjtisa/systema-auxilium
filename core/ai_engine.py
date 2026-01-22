"""
AI Engine - Handles AI interactions
FIXED: Removed voice mode prompt logic, using single prompt for all modes
"""

import requests
from core.tool_manager import ToolManager
from core.global_instructions import get_system_prompt, POST_EXIT_PROMPT, POST_EXIT_PROMPT_VOICE, \
    get_gemini_system_prompt


class AIEngine:
    """AI conversation engine"""

    def __init__(self, log_callback=None, api_key='', puter_server=None, gemini_api_key='', system_info='',
                 voice_mode=False, elevenlabs_enabled=False):
        self.log_callback = log_callback
        self.conversation_history = []
        self.tool_manager = ToolManager()
        self.api_key = api_key
        self.gemini_api_key = gemini_api_key
        self.puter_server = puter_server

        # LLaMA provider - SAFE INITIALIZATION
        self.llama_provider = None
        try:
            # Only try to load LLaMA if explicitly requested or no other provider available
            if api_key == '' and not puter_server and gemini_api_key == '':
                try:
                    from core.llama_provider import LLaMAProvider
                    self.llama_provider = LLaMAProvider(log_callback=log_callback)
                    if self.llama_provider.load_model():
                        self.ai_provider = 'llama'
                        self.log("LLaMA provider loaded as default (free, offline)", "SUCCESS")
                    else:
                        self.log("LLaMA model not found - using Anthropic as fallback", "WARNING")
                        self.llama_provider = None
                except Exception as e:
                    self.log(f"LLaMA initialization failed: {e}", "WARNING")
                    self.llama_provider = None
        except:
            pass

        # Store system information
        self.system_info = system_info

        # Voice mode flags
        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled

        # Generate system prompt with voice flags
        self.system_prompt = get_system_prompt(system_info, voice_mode, elevenlabs_enabled)

        # Store last raw response
        self.last_raw_response = None

        # Provider settings
        self.ai_provider = 'anthropic'
        self.puter_model = 'gpt-4o-mini'
        self.gemini_model = 'gemini-2.0-flash-exp'

        self.tts_provider = 'edge-tts'  # Can be 'edge-tts' or 'puter'
        self.puter_tts_model = 'tts-1'
        self.puter_tts_voice = None

        # API endpoints
        self.anthropic_api_url = "https://api.anthropic.com/v1/messages"
        self.gemini_api_url_template = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def set_voice_mode(self, enabled):
        """Enable/disable voice mode flag"""
        self.voice_mode = enabled
        # Regenerate prompt
        self.system_prompt = get_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled)
        if enabled:
            self.log("Voice mode ENABLED", "INFO")
        else:
            self.log("Voice mode DISABLED", "INFO")

    def update_voice_settings(self, voice_mode, elevenlabs_enabled):
        """Update voice mode settings and regenerate prompt"""
        self.voice_mode = voice_mode
        self.elevenlabs_enabled = elevenlabs_enabled
        self.system_prompt = get_system_prompt(self.system_info, voice_mode, elevenlabs_enabled)
        self.log(f"Voice settings updated: voice_mode={voice_mode}, elevenlabs={elevenlabs_enabled}")

    def set_api_key(self, api_key):
        self.api_key = api_key
        self.log("Anthropic API key updated")

    def set_gemini_api_key(self, api_key):
        self.gemini_api_key = api_key
        self.log("Gemini API key updated")

    def set_provider(self, provider):
        self.ai_provider = provider

        # Initialize LLaMA if selected
        if provider == 'llama' and self.llama_provider is None:
            try:
                from core.llama_provider import LLaMAProvider
                self.llama_provider = LLaMAProvider(log_callback=self.log)
                self.llama_provider.load_model()
            except:
                pass

        self.log(f"AI provider set to: {provider}")

    def set_puter_model(self, model):
        self.puter_model = model
        self.log(f"Puter.js model set to: {model}")

    def set_gemini_model(self, model):
        self.gemini_model = model
        self.log(f"Gemini model set to: {model}")

    def set_tts_provider(self, provider):
        self.tts_provider = provider
        self.log(f"TTS provider set to: {provider}")

    def set_puter_tts_model(self, model):
        self.puter_tts_model = model
        self.log(f"Puter TTS model set to: {model}")

    def set_puter_tts_voice(self, voice):
        self.puter_tts_voice = voice
        self.log(f"Puter TTS voice set to: {voice}")

    def log(self, message, level="INFO"):
        print(f"[AI Engine] {message}")
        if self.log_callback:
            self.log_callback(message, level)

    def generate_response(self, user_message):
        self.conversation_history.append({
            'role': 'user',
            'content': user_message
        })

        if self.ai_provider == 'llama':
            return self._generate_llama_response()
        elif self.ai_provider == 'puter':
            return self._generate_puter_response()
        elif self.ai_provider == 'gemini':
            return self._generate_gemini_response()
        else:
            return self._generate_anthropic_response()

    def generate_response_with_image(self, user_message, image_path):
        """Generate response with image attachment (Puter only)"""
        if self.ai_provider != 'puter':
            return {
                'response': "Error: Image attachment only supported with Puter",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

        self.conversation_history.append({
            'role': 'user',
            'content': user_message
        })

        return self._generate_puter_response_with_image(image_path)

    def _generate_puter_response_with_image(self, image_path):
        """Generate Puter response with image"""
        try:
            if not self.puter_server:
                return {
                    'response': "Error: Puter server not initialized",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
                return {
                    'response': "Error: Puter server not running",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            # NEW FORMAT: Build messages array
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use tools when I need outputs, and commands for quick actions."}
            ]

            # Add conversation history
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            # Send with image
            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                image=image_path,
                timeout=30
            )

            if not ai_reply:
                return {
                    'response': "Error: No response from Puter server",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            return self._process_ai_response(ai_reply)

        except Exception as e:
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _generate_puter_response(self):
        try:
            if not self.puter_server:
                return {
                    'response': "Error: Puter server not initialized",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
                return {
                    'response': "Error: Puter server not running",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            # NEW FORMAT: Build messages array with system prompt and history
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use tools when I need outputs, and commands for quick actions."}
            ]

            # Add conversation history
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=30
            )

            if not ai_reply:
                return {
                    'response': "Error: No response from Puter server",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            return self._process_ai_response(ai_reply)

        except Exception as e:
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _generate_llama_response(self):
        """Generate response using local LLaMA"""
        try:
            if not self.llama_provider or not self.llama_provider.model_loaded:
                return {
                    'response': "Error: LLaMA model not loaded",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            messages = self._build_messages()

            self.log("Generating with LLaMA...")
            ai_text = self.llama_provider.generate(messages, max_tokens=20000, temperature=0.7)

            return self._process_ai_response(ai_text)

        except Exception as e:
            error_msg = f"LLaMA Error: {e}"
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _generate_gemini_response(self):
        try:
            if not self.gemini_api_key:
                return {
                    'response': "Error: Gemini API key not set",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            messages = self._build_gemini_messages()
            api_url = self.gemini_api_url_template.format(model=self.gemini_model)

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
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    candidate = data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        ai_text = ''.join([part.get('text', '') for part in parts])
                        return self._process_ai_response(ai_text)

                return {
                    'response': "Error: Unexpected Gemini response format",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }
            else:
                error_msg = f"Gemini API Error: {response.status_code}"
                self.log(error_msg, "ERROR")
                return {
                    'response': f"Error: {error_msg}",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Gemini Error: {e}"
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _generate_anthropic_response(self):
        messages = self._build_messages()

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
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                ai_text = data['content'][0]['text']
                return self._process_ai_response(ai_text)
            else:
                error_msg = f"API Error: {response.status_code}"
                self.log(error_msg, "ERROR")
                return {
                    'response': f"Error: {error_msg}",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Error: {e}"
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _process_ai_response(self, ai_text):
        """Process AI response - check for commands first, then tools"""
        self.last_raw_response = ai_text

        # Check for COMMAND
        command_info = self.tool_manager.parse_command_call(ai_text)

        if command_info:
            command_name, command_input, visible_text = command_info
            self.log(f"Command detected: {command_name}")

            result = self.tool_manager.execute_command(
                command_name,
                command_input,
                log_callback=self.log
            )

            if result['success']:
                if visible_text:
                    final_text = visible_text
                else:
                    final_text = result['visible_message']
            else:
                final_text = f"{visible_text}\n\n{result['visible_message']}" if visible_text else result['visible_message']

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })

            return {
                'response': final_text,
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False,
                'command_name': command_name,
                'command_input': command_input
            }

        # Check for TOOL
        tool_call_info = self.tool_manager.parse_tool_call(ai_text)

        if tool_call_info:
            tool_name, tool_input, visible_text = tool_call_info
            self.log(f"Tool call detected: {tool_name}")

            tool_output = self.tool_manager.execute_tool(tool_name, tool_input)

            if tool_output == "EXITED_TOOL_MODE":
                self.tool_manager.in_tool_mode = False

                if visible_text and visible_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': visible_text
                    })

                return {
                    'response': visible_text if (visible_text and visible_text.strip()) else "",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False,
                    'exited_tool_mode': True
                }

            self.tool_manager.in_tool_mode = True
            self.tool_manager.last_tool_output = tool_output

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })

            return {
                'response': visible_text if visible_text else "**Working...**",
                'has_tool_call': True,
                'in_tool_mode': True,
                'thinking': True,
                'tool_name': tool_name,
                'tool_input': tool_input
            }

        # Normal response
        self.conversation_history.append({
            'role': 'assistant',
            'content': ai_text
        })

        return {
            'response': ai_text,
            'has_tool_call': False,
            'in_tool_mode': False,
            'thinking': False
        }

    def continue_tool_mode(self):
        if not self.tool_manager.in_tool_mode:
            return {
                'response': "Not in tool mode",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

        tool_prompt = self.tool_manager.get_tool_mode_prompt()

        self.conversation_history.append({
            'role': 'system',
            'content': tool_prompt
        })

        if self.ai_provider == 'puter':
            return self._continue_tool_mode_puter()
        elif self.ai_provider == 'gemini':
            return self._continue_tool_mode_gemini()
        else:
            return self._continue_tool_mode_anthropic()

    def send_post_exit_prompt(self):
        """Send post-exit prompt (same for all modes now)"""
        self.log("Sending post-exit prompt")
        if self.voice_mode:
            self.conversation_history.append({
                'role': 'system',
                'content': POST_EXIT_PROMPT_VOICE
            })
        else:
            self.conversation_history.append({
                'role': 'system',
                'content': POST_EXIT_PROMPT
            })

        if self.ai_provider == 'puter':
            return self._continue_tool_mode_puter()
        elif self.ai_provider == 'gemini':
            return self._continue_tool_mode_gemini()
        else:
            return self._continue_tool_mode_anthropic()

    def _continue_tool_mode_puter(self):
        try:
            # NEW FORMAT: Build full message history
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "assistant",
                 "content": "I understand. I will use tools when I need outputs, and commands for quick actions."}
            ]

            # Add conversation history (includes the tool prompt)
            for msg in self.conversation_history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=30
            )

            if not ai_reply:
                return {
                    'response': "Error: No response",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

            return self._process_tool_mode_response(ai_reply)

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _continue_tool_mode_gemini(self):
        try:
            messages = self._build_gemini_messages()
            api_url = self.gemini_api_url_template.format(model=self.gemini_model)

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
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    candidate = data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        ai_text = ''.join([part.get('text', '') for part in parts])
                        return self._process_tool_mode_response(ai_text)

                return {
                    'response': "Error: Unexpected response format",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }
            else:
                return {
                    'response': f"Gemini API Error: {response.status_code}",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _continue_tool_mode_anthropic(self):
        messages = self._build_messages()

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
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                ai_text = data['content'][0]['text']
                return self._process_tool_mode_response(ai_text)
            else:
                return {
                    'response': f"API Error: {response.status_code}",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False
                }

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _process_tool_mode_response(self, ai_text):
        self.last_raw_response = ai_text

        tool_call_info = self.tool_manager.parse_tool_call(ai_text)

        if tool_call_info:
            tool_name, tool_input, visible_text = tool_call_info
            self.log(f"Consecutive tool call detected: {tool_name}")

            tool_output = self.tool_manager.execute_tool(tool_name, tool_input)

            if tool_output == "EXITED_TOOL_MODE":
                self.tool_manager.in_tool_mode = False

                if visible_text and visible_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': visible_text
                    })

                return {
                    'response': visible_text if (visible_text and visible_text.strip()) else "",
                    'has_tool_call': False,
                    'in_tool_mode': False,
                    'thinking': False,
                    'exited_tool_mode': True
                }

            self.tool_manager.last_tool_output = tool_output

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })

            return {
                'response': visible_text if visible_text else "Working...",
                'has_tool_call': True,
                'in_tool_mode': True,
                'thinking': True,
                'tool_name': tool_name,
                'tool_input': tool_input
            }

        else:
            self.tool_manager.in_tool_mode = False

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })

            return {
                'response': ai_text,
                'has_tool_call': False,
                'in_tool_mode': False,
                'thinking': False
            }

    def _build_messages(self):
        messages = [
            {
                'role': 'system',
                'content': self.system_prompt
            },
            {
                'role': 'assistant',
                'content': 'I understand. I will use tools when I need outputs, and commands for quick actions.'
            }
        ]

        messages.extend(self.conversation_history)
        return messages

    def _build_gemini_messages(self):
        messages = []

        # Use voice-aware prompt
        gemini_prompt = get_gemini_system_prompt(self.system_info, self.voice_mode, self.elevenlabs_enabled)

        messages.append({
            "parts": [{"text": gemini_prompt}],
            "role": "system"
        })

        messages.append({
            "parts": [{"text": 'Understood. I will use tools and commands appropriately.'}],
            "role": "model"
        })

        for msg in self.conversation_history:
            role = "model" if msg['role'] == 'assistant' else "user"
            messages.append({
                "parts": [{"text": msg['content']}],
                "role": role
            })

        return messages

    def get_llama_available(self):
        """Check if LLaMA is available"""
        try:
            from core.llama_provider import LLaMAProvider
            provider = LLaMAProvider()
            return provider.is_available()
        except:
            return False

    def get_llama_models(self):
        """Get available LLaMA models"""
        try:
            from core.llama_provider import LLaMAProvider
            provider = LLaMAProvider()
            return provider.list_available_models()
        except:
            return []

    def get_llama_download_instructions(self):
        """Get LLaMA download instructions"""
        try:
            from core.llama_provider import LLaMAProvider
            provider = LLaMAProvider()
            return provider.download_default_model()
        except:
            return "LLaMA provider not available"

    def clear_history(self):
        self.conversation_history = []
        self.tool_manager.in_tool_mode = False
        self.tool_manager.last_tool_output = None
        self.last_raw_response = None