"""
Voice Handler - Complete voice input/output system
FIXED: Added proper initialization and error handling to prevent hangs
"""

import threading
import queue
import time
import io
import numpy as np
import sounddevice as sd
import webrtcvad
from collections import deque
import asyncio
import edge_tts
import pygame
import tempfile
import os
import re

# STT imports
import speech_recognition as sr

# Vosk import (offline, free alternative)
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

# Silero VAD import
try:
    import torch
    import torchaudio
    SILERO_AVAILABLE = True
except ImportError:
    SILERO_AVAILABLE = False

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False

class VoiceHandler:
    """Handles all voice input/output operations"""

    def __init__(self, log_callback=None):
        self.log_callback = log_callback

        # State
        self.is_listening = False
        self.is_speaking = False
        self.is_processing = False

        # pyttsx3 engine
        self.pyttsx3_engine = None
        if PYTTSX3_AVAILABLE:
            try:
                self.pyttsx3_engine = pyttsx3.init()
            except:
                pass

        # NEW: Interrupt mode
        self.interrupt_mode = 'manual'  # 'auto' or 'manual'

        # NEW: Playback callback
        self.on_playback_started = None  # Called when audio starts playing

        # CRITICAL FIX: Thread locks for thread safety
        self.vad_lock = threading.Lock()  # Protects VAD access
        self.state_lock = threading.Lock()  # Protects state changes
        self.config_lock = threading.Lock()  # Protects configuration changes

        # Audio settings
        self.sample_rate = 16000
        self.frame_duration = 30  # ms (10, 20, or 30 for webrtcvad)
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)

        # VAD settings - NOW SUPPORTS BOTH WEBRTC AND SILERO
        self.vad_webrtc_enabled = True
        self.vad_silero_enabled = False
        self.vad_aggressiveness = 3  # WebRTC: 0-3, higher = more aggressive
        self.silero_threshold = 0.5  # Silero: 0.0-1.0, higher = more conservative
        self.silence_duration = 1.5  # seconds of silence before sending

        self.puter_server = None
        self.puter_tts_model = 'tts-1'
        self.puter_tts_voice = None

        # Buffers
        self.audio_queue = queue.Queue()
        self.speech_buffer = deque(maxlen=50)
        self.silence_frames = 0

        # VAD - WebRTC
        self.vad_webrtc = webrtcvad.Vad(self.vad_aggressiveness)

        # VAD - Silero (lazy load when enabled)
        self.vad_silero_model = None
        self.silero_utils = None

        # Initialize Silero if available and enabled
        if SILERO_AVAILABLE and self.vad_silero_enabled:
            self._init_silero_vad()

        # Threads
        self.capture_thread = None
        self.processing_thread = None

        # Speech recognition
        self.recognizer = sr.Recognizer()

        # TTS settings - NOW CONFIGURABLE!
        self.tts_provider = 'edge-tts'
        self.tts_voice = 'en-CA-ClaraNeural'
        self.tts_rate = '+0%'
        self.tts_volume = '+0%'

        # Audio device settings
        self.input_device = None
        self.output_device = None

        # Callbacks
        self.on_transcription = None
        self.on_state_change = None

        # Initialize pygame for audio playback
        try:
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
        except Exception as e:
            self.log(f"Warning: Could not initialize audio: {e}", "WARNING")

        # Vosk model (if available)
        self.vosk_model = None
        self.vosk_recognizer = None

    def log(self, message, level="INFO"):
        """Log message"""
        print(f"[Voice] {message}")
        if self.log_callback:
            self.log_callback(f"[Voice] {message}", level)

    def _init_silero_vad(self):
        """Initialize Silero VAD model (lazy loading)"""
        if not SILERO_AVAILABLE:
            self.log("Silero VAD not available (torch/torchaudio not installed)", "WARNING")
            return False

        try:
            if self.vad_silero_model is None:
                self.log("Loading Silero VAD model...")
                model, utils = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    force_reload=False,
                    onnx=False
                )
                self.vad_silero_model = model
                self.silero_utils = utils
                self.log("Silero VAD model loaded successfully", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Failed to load Silero VAD: {e}", "ERROR")
            self.vad_silero_enabled = False
            return False

    def set_vad_configuration(self, webrtc_enabled, silero_enabled, webrtc_aggressiveness, silero_threshold):
        """Configure VAD settings - THREAD SAFE"""
        with self.config_lock:  # CRITICAL: Prevent concurrent config changes
            self.vad_webrtc_enabled = webrtc_enabled
            self.vad_silero_enabled = silero_enabled
            self.vad_aggressiveness = webrtc_aggressiveness
            self.silero_threshold = silero_threshold

            # Update WebRTC VAD with lock
            if webrtc_enabled:
                with self.vad_lock:
                    self.vad_webrtc = webrtcvad.Vad(webrtc_aggressiveness)
                self.log(f"WebRTC VAD aggressiveness set to {webrtc_aggressiveness}")

            # Initialize/update Silero VAD
            if silero_enabled:
                if not self._init_silero_vad():
                    self.log("Silero VAD initialization failed, disabled", "WARNING")

            self.log(f"VAD Config: WebRTC={'ON' if webrtc_enabled else 'OFF'}, Silero={'ON' if silero_enabled else 'OFF'}")

    def _audio_capture_loop(self):
        """Continuously capture audio from microphone"""
        try:
            with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='int16',
                    blocksize=self.frame_size,
                    device=self.input_device
            ) as stream:
                self.log("Audio capture started")

                while self.is_listening:
                    try:
                        audio_data, overflowed = stream.read(self.frame_size)

                        if overflowed:
                            self.log("Audio buffer overflowed", "WARNING")

                        # Convert to bytes for VAD
                        audio_bytes = audio_data.tobytes()

                        # ENHANCED: Check with enabled VAD(s)
                        is_speech = self._check_speech(audio_bytes, audio_data)

                        # Put in queue for processing
                        self.audio_queue.put((audio_bytes, is_speech))
                    except Exception as e:
                        if self.is_listening:
                            self.log(f"Audio read error: {e}", "WARNING")
                        break

        except Exception as e:
            self.log(f"Audio capture error: {e}", "ERROR")
            self.is_listening = False

    def _check_speech(self, audio_bytes, audio_array):
        """
        Check if audio contains speech using enabled VAD(s)
        THREAD SAFE - Uses locks to prevent heap corruption

        Args:
            audio_bytes: Raw audio bytes (for WebRTC VAD)
            audio_array: NumPy array of audio samples (for Silero VAD)

        Returns:
            bool: True if speech detected, False otherwise
        """
        webrtc_result = False
        silero_result = False

        # Check WebRTC VAD if enabled - WITH LOCK
        with self.config_lock:
            webrtc_enabled = self.vad_webrtc_enabled
            silero_enabled = self.vad_silero_enabled

        if webrtc_enabled:
            try:
                with self.vad_lock:  # CRITICAL: Lock access to VAD model
                    webrtc_result = self.vad_webrtc.is_speech(audio_bytes, self.sample_rate)
            except Exception as e:
                self.log(f"WebRTC VAD error: {e}", "WARNING")
                webrtc_result = False

        # Check Silero VAD if enabled - WITH LOCK
        if silero_enabled:
            with self.vad_lock:  # CRITICAL: Lock access to Silero model
                if self.vad_silero_model is not None:
                    try:
                        # Convert int16 array to float32 tensor
                        audio_float = audio_array.astype(np.float32) / 32768.0
                        audio_tensor = torch.from_numpy(audio_float).squeeze()

                        # Get speech probability from Silero
                        with torch.no_grad():
                            speech_prob = self.vad_silero_model(audio_tensor, self.sample_rate).item()

                        silero_result = speech_prob > self.silero_threshold
                    except Exception as e:
                        self.log(f"Silero VAD error: {e}", "WARNING")
                        silero_result = False

        # Logic: If both enabled, use OR logic (speech if either detects it)
        # If only one enabled, use that one
        # If neither enabled, default to True (always process)
        if webrtc_enabled and silero_enabled:
            return webrtc_result or silero_result
        elif webrtc_enabled:
            return webrtc_result
        elif silero_enabled:
            return silero_result
        else:
            # Neither enabled - always return True (no filtering)
            return True

    def set_tts_voice(self, voice):
        """Set TTS voice"""
        self.tts_voice = voice
        self.log(f"TTS voice set to {voice}")

    def set_interrupt_mode(self, mode):
        """Set interrupt mode ('auto' or 'manual')"""
        self.interrupt_mode = mode
        self.log(f"Interrupt mode set to {mode}")

    def set_puter_server(self, puter_server):
        """Set Puter server reference for TTS"""
        self.puter_server = puter_server
        self.log("Puter server reference set")

    def set_tts_provider(self, provider):
        """Set TTS provider"""
        self.tts_provider = provider
        self.log(f"TTS provider set to {provider}")

    def set_puter_tts_settings(self, model, voice):
        """Set Puter TTS settings"""
        self.puter_tts_model = model
        self.puter_tts_voice = voice
        self.log(f"Puter TTS: model={model}, voice={voice}")

    def set_vad_aggressiveness(self, level):
        """Set VAD aggressiveness (0-3)"""
        try:
            level = int(level)
            if 0 <= level <= 3:
                self.vad_aggressiveness = level
                self.vad = webrtcvad.Vad(level)
                self.log(f"VAD aggressiveness set to {level}")
            else:
                self.log("VAD level must be 0-3", "WARNING")
        except Exception as e:
            self.log(f"Error setting VAD: {e}", "ERROR")

    def list_audio_devices(self):
        """List all available audio devices"""
        devices = sd.query_devices()

        input_devices = []
        output_devices = []

        for i, device in enumerate(devices):
            if device['max_input_channels'] > 0:
                input_devices.append({
                    'id': i,
                    'name': device['name'],
                    'channels': device['max_input_channels']
                })
            if device['max_output_channels'] > 0:
                output_devices.append({
                    'id': i,
                    'name': device['name'],
                    'channels': device['max_output_channels']
                })

        return input_devices, output_devices

    def set_devices(self, input_device_id=None, output_device_id=None):
        """Set audio input/output devices"""
        self.input_device = input_device_id
        self.output_device = output_device_id

        if input_device_id is not None:
            devices = sd.query_devices()
            device_name = devices[input_device_id]['name']
            self.log(f"Input device: {device_name}")

        if output_device_id is not None:
            devices = sd.query_devices()
            device_name = devices[output_device_id]['name']
            self.log(f"Output device: {device_name}")

    def start_listening(self):
        """Start listening for voice input"""
        if self.is_listening:
            self.log("Already listening")
            return False

        try:
            self.is_listening = True
            self.speech_buffer.clear()
            self.silence_frames = 0

            # Clear any existing queue items
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    break

            # Start capture thread
            self.capture_thread = threading.Thread(target=self._audio_capture_loop, daemon=True)
            self.capture_thread.start()

            # Start processing thread
            self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
            self.processing_thread.start()

            self.log("Started listening")
            self._notify_state_change('listening')
            return True

        except Exception as e:
            self.log(f"Error starting listening: {e}", "ERROR")
            self.is_listening = False
            return False

    def stop_listening(self):
        """Stop listening for voice input - THREAD SAFE CLEANUP"""
        if not self.is_listening:
            return

        self.log("Stopping voice input...")

        # CRITICAL: Set flag FIRST before waiting for threads
        with self.state_lock:
            self.is_listening = False

        # Wait for threads to finish with timeout
        if self.capture_thread and self.capture_thread.is_alive():
            self.log("Waiting for capture thread...")
            self.capture_thread.join(timeout=2.0)
            if self.capture_thread.is_alive():
                self.log("Capture thread did not stop cleanly", "WARNING")

        if self.processing_thread and self.processing_thread.is_alive():
            self.log("Waiting for processing thread...")
            self.processing_thread.join(timeout=2.0)
            if self.processing_thread.is_alive():
                self.log("Processing thread did not stop cleanly", "WARNING")

        # Clear queue safely
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        # Clear threads
        self.capture_thread = None
        self.processing_thread = None

        self.log("Stopped listening")
        self._notify_state_change('inactive')

    def interrupt_speech(self):
        """Interrupt current TTS playback"""
        if not self.is_speaking:
            return

        self.log("Interrupting speech...")
        self.is_speaking = False

        try:
            # Check if pygame mixer is initialized
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception as e:
            self.log(f"Error stopping playback: {e}", "WARNING")

        self.log("Speech interrupted")
        self._notify_state_change('listening' if self.is_listening else 'inactive')

    def _audio_capture_loop(self):
        """Continuously capture audio from microphone"""
        try:
            with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='int16',
                    blocksize=self.frame_size,
                    device=self.input_device
            ) as stream:
                self.log("Audio capture started")

                while self.is_listening:
                    try:
                        audio_data, overflowed = stream.read(self.frame_size)

                        if overflowed:
                            self.log("Audio buffer overflowed", "WARNING")

                        # Convert to bytes for VAD
                        audio_bytes = audio_data.tobytes()

                        # VAD check
                        is_speech = self.vad.is_speech(audio_bytes, self.sample_rate)

                        # Put in queue for processing
                        self.audio_queue.put((audio_bytes, is_speech))
                    except Exception as e:
                        if self.is_listening:  # Only log if we're still supposed to be listening
                            self.log(f"Audio read error: {e}", "WARNING")
                        break

        except Exception as e:
            self.log(f"Audio capture error: {e}", "ERROR")
            self.is_listening = False

    def _processing_loop(self):
        """Process audio queue and detect speech segments"""
        audio_buffer = []
        currently_speaking = False

        while self.is_listening:
            try:
                # Get audio frame
                audio_bytes, is_speech = self.audio_queue.get(timeout=0.1)

                if is_speech:
                    # Speech detected
                    if not currently_speaking:
                        self.log("Speech started")
                        currently_speaking = True
                        audio_buffer = []

                    audio_buffer.append(audio_bytes)
                    self.silence_frames = 0

                    # NEW: Check interrupt mode
                    if self.interrupt_mode == 'auto' and self.is_speaking:
                        self.log("Auto-interrupting TTS due to voice detection")
                        self.interrupt_speech()

                else:
                    # Silence detected
                    if currently_speaking:
                        audio_buffer.append(audio_bytes)
                        self.silence_frames += 1

                        # Check if silence threshold reached
                        silence_duration = (self.silence_frames * self.frame_duration) / 1000

                        if silence_duration >= self.silence_duration:
                            self.log(f"Silence detected ({silence_duration:.1f}s), processing speech")

                            # Process the audio
                            self._process_audio_segment(audio_buffer)

                            # Reset
                            audio_buffer = []
                            currently_speaking = False
                            self.silence_frames = 0

            except queue.Empty:
                continue
            except Exception as e:
                if self.is_listening:
                    self.log(f"Processing error: {e}", "ERROR")

    def _process_audio_segment(self, audio_buffer):
        """Process captured audio segment with STT"""
        if not audio_buffer:
            return

        self.is_processing = True
        self._notify_state_change('processing')

        try:
            # Combine audio frames
            audio_data = b''.join(audio_buffer)

            # Convert to audio for speech recognition
            audio_array = np.frombuffer(audio_data, dtype=np.int16)

            # Use speech_recognition with Google Speech Recognition (FREE)
            audio_sr = sr.AudioData(audio_data, self.sample_rate, 2)

            self.log("Transcribing with Google Speech Recognition...")

            try:
                # Try Google Speech Recognition (free, no API key needed!)
                text = self.recognizer.recognize_google(audio_sr)
                self.log(f"Transcribed: {text}")

                if self.on_transcription and text and text.strip():
                    # Call transcription callback in main thread
                    self.on_transcription(text)

            except sr.UnknownValueError:
                self.log("Could not understand audio", "WARNING")
            except sr.RequestError as e:
                self.log(f"Speech recognition error: {e}", "ERROR")

        except Exception as e:
            self.log(f"STT error: {e}", "ERROR")

        finally:
            self.is_processing = False
            self._notify_state_change('listening')

    async def speak_text(self, text):
        """Convert text to speech and play it"""
        if not text or not text.strip():
            return

        # FILTER TEXT FOR TTS (removes code blocks only, keeps brackets)
        filtered_text = self._filter_text_for_tts(text)

        if not filtered_text or not filtered_text.strip():
            self.log("[TTS] No speakable text after filtering")
            return

        self.log(f"[TTS] Speaking: {filtered_text[:100]}...")  # Debug output

        self.is_speaking = True
        self._notify_state_change('speaking')

        try:
            # Check provider and route accordingly
            if self.tts_provider == 'pyttsx3':
                self.log("[TTS] Using pyttsx3 (offline)")
                await self._speak_pyttsx3(filtered_text)
            elif self.tts_provider == 'puter':
                # Check if ElevenLabs is enabled
                use_elevenlabs = getattr(self, 'use_elevenlabs', False)
                if use_elevenlabs and hasattr(self, 'elevenlabs_voice_id') and self.elevenlabs_voice_id:
                    self.log("[TTS] Using Puter + ElevenLabs")
                    await self._speak_puter_elevenlabs(filtered_text)
                else:
                    self.log("[TTS] Using Puter standard TTS")
                    await self._speak_puter_tts(filtered_text)
            elif self.tts_provider == 'edge-tts':
                self.log("[TTS] Using Edge TTS")
                await self._speak_edge_tts(filtered_text)
            else:
                self.log(f"[TTS] Provider '{self.tts_provider}' not implemented", "WARNING")

        except Exception as e:
            self.log(f"[TTS] Error: {e}", "ERROR")

        finally:
            self.is_speaking = False
            self._notify_state_change('listening' if self.is_listening else 'inactive')

    async def _speak_pyttsx3(self, text):
        """Speak using pyttsx3 (offline TTS)"""
        try:
            if not PYTTSX3_AVAILABLE or not self.pyttsx3_engine:
                self.log("pyttsx3 not available", "ERROR")
                return

            # Run in thread to avoid blocking
            import threading

            def speak_thread():
                try:
                    self.pyttsx3_engine.say(text)
                    self.pyttsx3_engine.runAndWait()
                except Exception as e:
                    self.log(f"pyttsx3 error: {e}", "ERROR")

            thread = threading.Thread(target=speak_thread, daemon=True)
            thread.start()

            # Notify playback started
            if self.on_playback_started:
                try:
                    self.on_playback_started()
                except Exception as e:
                    self.log(f"Error in playback callback: {e}", "ERROR")

            # Wait for thread
            thread.join()

        except Exception as e:
            self.log(f"pyttsx3 TTS error: {e}", "ERROR")

    async def _speak_puter_tts(self, text):
        """Speak using Puter.js TTS"""
        try:
            if not self.puter_server:
                self.log("Puter server not available for TTS", "ERROR")
                return

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()

            # Generate speech with Puter
            audio_url = self.puter_server.text_to_speech(
                text=text,
                model=self.puter_tts_model,
                voice=self.puter_tts_voice,
                save_to=temp_path
            )

            if not audio_url:
                self.log("Failed to generate speech with Puter", "ERROR")
                return

            # Load and play audio
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()

            # NEW: Notify that playback started IMMEDIATELY after play() call
            if self.on_playback_started:
                try:
                    self.on_playback_started()
                    self.log("Playback started callback executed")
                except Exception as e:
                    self.log(f"Error in playback callback: {e}", "ERROR")

            # Wait for playback to finish (with interruption check)
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:  # Interrupted
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)

            # Cleanup
            try:
                os.unlink(temp_path)
            except:
                pass

        except Exception as e:
            self.log(f"Puter TTS error: {e}", "ERROR")

    async def _speak_puter_elevenlabs(self, text):
        """Speak using Puter.js with ElevenLabs"""
        try:
            if not self.puter_server:
                self.log("Puter server not available for ElevenLabs TTS", "ERROR")
                return

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()

            # Get ElevenLabs voice ID from settings
            voice_id = getattr(self, 'elevenlabs_voice_id', None)

            # Generate speech with Puter + ElevenLabs
            audio_url = self.puter_server.text_to_speech(
                text=text,
                model='eleven_v3',  # Default model
                voice=voice_id,
                provider='elevenlabs',
                save_to=temp_path
            )

            if not audio_url:
                self.log("Failed to generate speech with ElevenLabs", "ERROR")
                return

            # Load and play audio
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()

            # NEW: Notify that playback started IMMEDIATELY after play() call
            if self.on_playback_started:
                try:
                    self.on_playback_started()
                    self.log("Playback started callback executed")
                except Exception as e:
                    self.log(f"Error in playback callback: {e}", "ERROR")

            # Wait for playback to finish (with interruption check)
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:  # Interrupted
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)

            # Cleanup
            try:
                os.unlink(temp_path)
            except:
                pass

        except Exception as e:
            self.log(f"ElevenLabs TTS error: {e}", "ERROR")

    def _filter_text_for_tts(self, text):
        """
        Filter text for TTS - remove code blocks ONLY, keep everything else
        Preserves: emojis, brackets, symbols, expressive text
        """

        original_text = text  # Store for debug

        # ONLY remove code blocks (triple backticks and inline code)
        text = re.sub(r'```[\s\S]*?```', '', text)  # Fenced code blocks
        text = re.sub(r'`[^`]+`', '', text)  # Inline code

        # Remove markdown formatting but KEEP the content
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **bold** → bold
        text = re.sub(r'\*([^*]+)\*', r'\1', text)  # *italic* → italic
        text = re.sub(r'__([^_]+)__', r'\1', text)  # __bold__ → bold
        text = re.sub(r'_([^_]+)_', r'\1', text)  # _italic_ → italic
        text = re.sub(r'~~([^~]+)~~', r'\1', text)  # ~~strike~~ → strike

        # Remove links but keep text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # [text](url) → text

        # Remove headers
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)  # ## Header → Header

        # Remove list markers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)  # - item → item
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # 1. item → item

        # Clean up excessive whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        # Debug output
        if original_text != text:
            self.log(f"[TTS Filter] Original: {original_text[:80]}...")
            self.log(f"[TTS Filter] Filtered: {text[:80]}...")

        return text

    def _remove_emotion_brackets(self, text):
        """
        Remove ElevenLabs emotion/voice effect brackets for DISPLAY only
        Examples: [happy], [giggles], [whispers], [pause]

        This is called AFTER TTS processing, before displaying in chat
        """

        # Remove emotion brackets like [happy], [giggles], [whispers], etc.
        cleaned = re.sub(r'\[([^\]]+)\]', '', text)

        # Clean up any double spaces left behind
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()

        return cleaned

    async def _speak_edge_tts(self, text):
        """Speak using Edge TTS (FREE)"""
        try:
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()

            # Generate speech with configured voice
            communicate = edge_tts.Communicate(text, self.tts_voice, rate=self.tts_rate, volume=self.tts_volume)
            await communicate.save(temp_path)

            # Load and play audio
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()

            # NEW: Notify that playback started IMMEDIATELY after play() call
            if self.on_playback_started:
                try:
                    self.on_playback_started()
                    self.log("Playback started callback executed")
                except Exception as e:
                    self.log(f"Error in playback callback: {e}", "ERROR")

            # Wait for playback to finish (with interruption check)
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:  # Interrupted
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)

            # Cleanup
            try:
                os.unlink(temp_path)
            except:
                pass

        except Exception as e:
            self.log(f"Edge TTS error: {e}", "ERROR")

    def speak_text_sync(self, text):
        """Synchronous wrapper for speak_text"""
        if not text or not text.strip():
            return

        # Run in new event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.speak_text(text))
        finally:
            loop.close()

    def set_elevenlabs_settings(self, enabled, voice_id):
        """Set ElevenLabs TTS settings"""
        self.use_elevenlabs = enabled
        self.elevenlabs_voice_id = voice_id
        if enabled:
            self.log(f"ElevenLabs enabled with voice: {voice_id}")

    def _notify_state_change(self, state):
        """Notify state change (thread-safe)"""
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception as e:
                self.log(f"Error in state change callback: {e}", "ERROR")

    def cleanup(self):
        """Cleanup resources"""
        self.log("Cleaning up voice handler...")

        # Stop listening first
        self.stop_listening()

        # Interrupt any ongoing speech
        self.interrupt_speech()

        # Clear callbacks to prevent post-cleanup calls
        self.on_transcription = None
        self.on_state_change = None
        self.on_playback_started = None

        # Quit pygame mixer safely
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except Exception as e:
            self.log(f"Error during mixer cleanup: {e}", "WARNING")

        self.log("Voice handler cleaned up")