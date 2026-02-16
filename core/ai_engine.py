"""
AI Engine - Handles AI interactions
FIXED: Removed voice mode prompt logic, using single prompt for all modes
UPDATED: Added supervised execution support with code approval dialogs
UPDATED: Appends error traceback to execute_code failures
"""

import requests
from core.tool_manager import ToolManager
from core.global_instructions import (
    get_system_prompt,
    POST_EXIT_PROMPT,
    POST_EXIT_PROMPT_VOICE,
    get_gemini_system_prompt
)


class AIEngine:
    """AI conversation engine"""

    def __init__(self, log_callback=None, api_key='', puter_server=None, gemini_api_key='', system_info='',
                 voice_mode=False, elevenlabs_enabled=False, settings_callback=None):
        self.log_callback = log_callback
        self.conversation_history = []

        # Store these first so tool_manager can access them
        self.api_key = api_key
        self.gemini_api_key = gemini_api_key
        self.puter_server = puter_server

        # Initialize tool_manager with settings callback and AI engine reference
        self.tool_manager = ToolManager(
            settings_callback=settings_callback,
            ai_engine=self  # Pass self so dialog can call AI for explanations
        )

        # LLaMA provider removed

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
        self.puter_timeout = 30  # Default timeout in seconds

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

    def _get_ai_response_internal(self, prompt):
        """Get AI response without adding to conversation history"""
        # This is similar to get_response but doesn't modify conversation_history
        if self.ai_provider == 'anthropic':
            return self._get_anthropic_response_internal(prompt)
        elif self.ai_provider == 'gemini':
            return self._get_gemini_response_internal(prompt)
        elif self.ai_provider == 'puter':
            return self._get_puter_response_internal(prompt)
        else:
            return "Error: Unknown provider"

    def _get_anthropic_response_internal(self, prompt):
        """Get Anthropic response for internal use (explanation)"""
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

            return result['content'][0]['text']

        except Exception as e:
            return f"Error getting explanation: {str(e)}"

    def _get_gemini_response_internal(self, prompt):
        """Get Gemini response for internal use (explanation)"""
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

            return result['candidates'][0]['content']['parts'][0]['text']

        except Exception as e:
            return f"Error getting explanation: {str(e)}"

    def _get_puter_response_internal(self, prompt):
        """Get Puter response for internal use (explanation)"""
        try:
            response = self.generate_response(prompt)
            return response

        except Exception as e:
            return f"Error getting explanation: {str(e)}"

    def set_api_key(self, api_key):
        self.api_key = api_key
        self.log("Anthropic API key updated")

    def set_gemini_api_key(self, api_key):
        self.gemini_api_key = api_key
        self.log("Gemini API key updated")

    def set_provider(self, provider):
        self.ai_provider = provider
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

    def set_puter_timeout(self, timeout):
        """Set Puter.js server timeout in seconds"""
        self.puter_timeout = int(timeout)
        self.log(f"Puter timeout set to: {timeout} seconds")

    def log(self, message, level="INFO"):
        print(f"[AI Engine] {message}")
        if self.log_callback:
            self.log_callback(message, level)

    def generate_response(self, user_message):
        self.conversation_history.append({
            'role': 'user',
            'content': user_message
        })

        if self.ai_provider == 'puter':
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
                'has_work_call': False,
                'in_work_mode': False,
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
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
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

            # Send with image
            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                image=image_path,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                return {
                    'response': "Error: No response from Puter server",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            return self._process_ai_response(ai_reply)

        except Exception as e:
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _generate_puter_response(self):
        try:
            if not self.puter_server:
                return {
                    'response': "Error: Puter server not initialized",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            if not self.puter_server.is_running:
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

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                return {
                    'response': "Error: No response from Puter server",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            return self._process_ai_response(ai_reply)

        except Exception as e:
            self.log(f"Puter error: {e}", "ERROR")
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _generate_gemini_response(self):
        try:
            if not self.gemini_api_key:
                return {
                    'response': "Error: Gemini API key not set",
                    'has_work_call': False,
                    'in_work_mode': False,
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
                timeout=self.puter_timeout
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
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }
            else:
                error_msg = f"Gemini API Error: {response.status_code}"
                self.log(error_msg, "ERROR")
                return {
                    'response': f"Error: {error_msg}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Gemini Error: {e}"
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_work_call': False,
                'in_work_mode': False,
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
                timeout=self.puter_timeout
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
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            error_msg = f"Error: {e}"
            self.log(error_msg, "ERROR")
            return {
                'response': error_msg,
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _process_ai_response(self, ai_text):
        """
        Process AI response and handle work_environment or execute_code calls

        Returns:
            dict: Response data with execution status
        """
        self.last_raw_response = ai_text

        # Check for work_environment call
        work_call = self.tool_manager.parse_work_environment(ai_text)
        if work_call:
            code, visible_text = work_call
            self.log(f"Work environment call detected")

            # Execute code in work mode
            work_output = self.tool_manager.run_work_environment(code)

            # Check if AI exited work mode
            if work_output == "EXITED_WORK_MODE":
                self.tool_manager.in_work_mode = False

                # Add FULL AI TEXT to history for consistency
                if ai_text and ai_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': ai_text
                    })

                return {
                    'response': visible_text if visible_text.strip() else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True
                }

            # Store output for next iteration
            self.tool_manager.last_work_output = work_output
            self.tool_manager.in_work_mode = True

            # Add FULL AI TEXT to history (with JSON) so AI remembers tool usage
            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text  # Keep original with JSON for AI's memory!
            })

            return {
                'response': visible_text if visible_text else "Working...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'code': code
            }

        # Check for execute_code call
        execute_call = self.tool_manager.parse_execute_code(ai_text)
        if execute_call:
            code, visible_text = execute_call
            self.log(f"Execute code call detected")

            # Execute code (AI doesn't see output)
            result = self.tool_manager.run_execute_code(code, self.log_callback)

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

            return {
                'response': response_text,  # Return clean text (with error if failed) to display in chat
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False,
                'executed': True,
                'execution_success': result['success']
            }

        # No execution calls - normal response
        self.conversation_history.append({
            'role': 'assistant',
            'content': ai_text
        })

        return {
            'response': ai_text,
            'has_work_call': False,
            'in_work_mode': False,
            'thinking': False
        }

    def continue_work_mode(self):
        """Continue work mode execution with AI analyzing previous output"""
        if not self.tool_manager.in_work_mode:
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

        # Generate next response based on provider
        if self.ai_provider == 'puter':
            return self._continue_work_mode_puter()
        elif self.ai_provider == 'gemini':
            return self._continue_work_mode_gemini()
        else:
            return self._continue_work_mode_anthropic()

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
            return self._continue_work_mode_puter()
        elif self.ai_provider == 'gemini':
            return self._continue_work_mode_gemini()
        else:
            return self._continue_work_mode_anthropic()

    def _continue_work_mode_puter(self):
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

            ai_reply = self.puter_server.send_chat_request(
                messages=messages,
                model=self.puter_model,
                timeout=self.puter_timeout
            )

            if not ai_reply:
                return {
                    'response': "Error: No response",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

            return self._process_work_mode_response(ai_reply)

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _continue_work_mode_gemini(self):
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
                timeout=self.puter_timeout
            )

            if response.status_code == 200:
                data = response.json()
                if 'candidates' in data and len(data['candidates']) > 0:
                    candidate = data['candidates'][0]
                    if 'content' in candidate and 'parts' in candidate['content']:
                        parts = candidate['content']['parts']
                        ai_text = ''.join([part.get('text', '') for part in parts])
                        return self._process_work_mode_response(ai_text)

                return {
                    'response': "Error: Unexpected response format",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }
            else:
                return {
                    'response': f"Gemini API Error: {response.status_code}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _continue_work_mode_anthropic(self):
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
                timeout=self.puter_timeout
            )

            if response.status_code == 200:
                data = response.json()
                ai_text = data['content'][0]['text']
                return self._process_work_mode_response(ai_text)
            else:
                return {
                    'response': f"API Error: {response.status_code}",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False
                }

        except Exception as e:
            return {
                'response': f"Error: {e}",
                'has_work_call': False,
                'in_work_mode': False,
                'thinking': False
            }

    def _process_work_mode_response(self, ai_text):
        """Process AI response while in work mode"""
        self.last_raw_response = ai_text

        work_call = self.tool_manager.parse_work_environment(ai_text)

        if work_call:
            code, visible_text = work_call
            self.log(f"Consecutive work environment call detected")

            work_output = self.tool_manager.run_work_environment(code)

            if work_output == "EXITED_WORK_MODE":
                self.tool_manager.in_work_mode = False

                # Add FULL AI TEXT to history for consistency
                if ai_text and ai_text.strip():
                    self.conversation_history.append({
                        'role': 'assistant',
                        'content': ai_text
                    })

                return {
                    'response': visible_text if (visible_text and visible_text.strip()) else "",
                    'has_work_call': False,
                    'in_work_mode': False,
                    'thinking': False,
                    'exited_work_mode': True
                }

            self.tool_manager.last_work_output = work_output

            # Add FULL AI TEXT to history (with JSON) so AI remembers tool usage
            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text  # Keep original with JSON for AI's memory!
            })

            return {
                'response': visible_text if visible_text else "Working...",
                'has_work_call': True,
                'in_work_mode': True,
                'thinking': True,
                'code': code
            }

        else:
            self.tool_manager.in_work_mode = False

            self.conversation_history.append({
                'role': 'assistant',
                'content': ai_text
            })

            return {
                'response': ai_text,
                'has_work_call': False,
                'in_work_mode': False,
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
                'content': 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'
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
            "parts": [{"text": 'I understand. I will use My Work Environment when I need to complete complex tasks or need information, and Execute Single commands for quick actions.'}],
            "role": "model"
        })

        for msg in self.conversation_history:
            role = "model" if msg['role'] == 'assistant' else "user"
            messages.append({
                "parts": [{"text": msg['content']}],
                "role": role
            })

        return messages

    def clear_history(self):
        """Clear conversation history and reset work mode"""
        self.conversation_history = []
        self.tool_manager.in_work_mode = False
        self.tool_manager.last_work_output = None
        self.last_raw_response = None

    def remove_last_user_message(self):
        """Remove the last user message from conversation history"""
        # Find and remove the last user message
        for i in range(len(self.conversation_history) - 1, -1, -1):
            if self.conversation_history[i]['role'] == 'user':
                removed_msg = self.conversation_history.pop(i)
                self.log(f"Removed user message from history: {removed_msg['content'][:50]}...")
                return True
        return False

    def reset_python_interpreter(self):
        """Reset the Python interpreter state"""
        self.tool_manager.reset_python()
        self.log("Python interpreter reset")