"""
LLaMA Provider - Local AI using llama-cpp-python
Free, offline, no API key needed
"""

import os
from pathlib import Path


class LLaMAProvider:
    """Local LLaMA model provider"""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback
        self.model = None
        self.model_loaded = False
        self.model_path = None

        # Default model directory
        self.models_dir = Path("llama_models")
        try:
            self.models_dir.mkdir(exist_ok=True)
        except:
            pass  # If we can't create dir, that's fine

        # Generation parameters (can be overridden)
        self.max_tokens = 2000
        self.temperature = 0.7
        self.top_p = 0.9

    def log(self, message, level="INFO"):
        """Log message"""
        print(f"[LLaMA] {message}")
        if self.log_callback:
            self.log_callback(f"[LLaMA] {message}", level)

    def load_model(self, model_path=None):
        """Load LLaMA model with safety checks"""
        try:
            # Check if llama-cpp-python is installed
            try:
                from llama_cpp import Llama
            except ImportError:
                self.log("llama-cpp-python not installed. Run: pip install llama-cpp-python", "ERROR")
                return False

            if model_path is None:
                # Try to find default model
                model_path = self.find_default_model()

            if model_path is None:
                self.log("No .gguf model found in llama_models/ folder", "WARNING")
                return False

            # Verify file exists
            if not Path(model_path).exists():
                self.log(f"Model file not found: {model_path}", "ERROR")
                return False

            self.log(f"Loading model: {model_path}")

            # SAFE LOADING with error handling
            self.model = Llama(
                model_path=str(model_path),
                n_ctx=2048,  # Context window
                n_threads=4,  # CPU threads
                n_gpu_layers=0,  # 0 = CPU only
                verbose=False  # Reduce output
            )

            self.model_loaded = True
            self.model_path = model_path
            self.log(f"✓ Model loaded successfully!", "SUCCESS")
            return True

        except Exception as e:
            self.log(f"Error loading model: {e}", "ERROR")
            self.model_loaded = False
            return False

    def find_default_model(self):
        """Find first available .gguf model"""
        if not self.models_dir.exists():
            return None

        for model_file in self.models_dir.glob("*.gguf"):
            return model_file

        return None

    def generate(self, messages, max_tokens=None, temperature=None, top_p=None):
        """Generate response from messages with configurable parameters"""
        if not self.model_loaded:
            return "Error: Model not loaded. Please load a model first."

        # Use instance defaults if not provided
        max_tokens = max_tokens or self.max_tokens
        temperature = temperature or self.temperature
        top_p = top_p or self.top_p

        try:
            # Convert messages to prompt format
            prompt = self._messages_to_prompt(messages)

            self.log(f"Generating response (max_tokens={max_tokens}, temp={temperature})...")

            # Generate
            response = self.model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=["User:", "\n\n\n"],  # Stop sequences
                echo=False
            )

            text = response['choices'][0]['text'].strip()
            self.log(f"✓ Generated {len(text)} characters")

            return text

        except Exception as e:
            self.log(f"Generation error: {e}", "ERROR")
            return f"Error: {e}"

    def _messages_to_prompt(self, messages):
        """Convert message format to LLaMA prompt"""
        prompt_parts = []

        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')

            if role == 'system':
                prompt_parts.append(f"System: {content}\n\n")
            elif role == 'user':
                prompt_parts.append(f"User: {content}\n\n")
            elif role == 'assistant':
                prompt_parts.append(f"Assistant: {content}\n\n")
            else:
                role = role[0].upper() + role[1:].lower()  # Convert to lowercase
                prompt_parts.append(f"{role}: {content}\n\n")

        # Add final assistant prompt
        prompt_parts.append("Assistant:")

        return "".join(prompt_parts)

    def is_available(self):
        """Check if llama-cpp-python is available - NO IMPORTS"""
        try:
            # Don't import - just check if module exists
            import importlib.util
            spec = importlib.util.find_spec("llama_cpp")
            return spec is not None
        except:
            return False

    def get_model_info(self):
        """Get current model info"""
        if not self.model_loaded:
            return "No model loaded"

        return f"Loaded: {Path(self.model_path).name}"

    def list_available_models(self):
        """List available .gguf models"""
        if not self.models_dir.exists():
            return []

        models = []
        for model_file in self.models_dir.glob("*.gguf"):
            size_mb = model_file.stat().st_size / (1024 * 1024)
            models.append({
                'name': model_file.name,
                'path': str(model_file),
                'size_mb': round(size_mb, 2)
            })

        return models

    def download_default_model(self):
        """Provide instructions for downloading a model"""
        instructions = """
To use LLaMA offline, download a model:

1. Visit: https://huggingface.co/TheBloke
2. Search for "GGUF" models (e.g., "Llama-2-7B-Chat-GGUF")
3. Download a .gguf file (recommend Q4_K_M for balance)
4. Place it in the 'llama_models' folder
5. Restart the app

Recommended starter model (1.5GB):
https://huggingface.co/TheBloke/Llama-2-7B-Chat-GGUF/resolve/main/llama-2-7b-chat.Q4_K_M.gguf
"""
        return instructions