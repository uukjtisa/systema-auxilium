"""
core/voice_handler.py
Voice Handler - Complete voice input/output system
FIXED: Added proper initialization and error handling to prevent hangs
UPDATED: Unified colored logging system matching ToolManager style
"""

import threading
import queue
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
from systema.common.logger import _make_logger, _NoOpLogger

# Vosk import (offline, free alternative)
try:
    from vosk import Model, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

# Silero VAD import
try:
    try:
        import torch

        TORCH_AVAILABLE = True
    except (ImportError, OSError):
        torch = None
        TORCH_AVAILABLE = False
    import torchaudio
    SILERO_AVAILABLE = True
except ImportError:
    SILERO_AVAILABLE = False

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = False
log = _make_logger("VoiceHandler") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class VoiceHandler:
    """Handles all voice input/output operations"""

    def __init__(self, log_callback=None):
        log.info("[VoiceHandler.__init__] ── Initializing VoiceHandler ──────────────────────")
        log.debug(f"[VoiceHandler.__init__] has_log_callback={log_callback is not None}")
        log.debug(f"[VoiceHandler.__init__] Optional deps: VOSK={VOSK_AVAILABLE} | "
                  f"SILERO={SILERO_AVAILABLE} | PYTTSX3={PYTTSX3_AVAILABLE}")

        self.log_callback = log_callback

        # State
        self.is_listening = False
        self.is_speaking = False
        self.is_processing = False
        log.debug("[VoiceHandler.__init__] State flags: is_listening=False | is_speaking=False | "
                  "is_processing=False")

        # pyttsx3 engine
        self.pyttsx3_engine = None
        if PYTTSX3_AVAILABLE:
            log.debug("[VoiceHandler.__init__] Attempting pyttsx3 engine init...")
            try:
                self.pyttsx3_engine = pyttsx3.init()
                log.info("[VoiceHandler.__init__] ✓ pyttsx3 engine initialized")
            except Exception as e:
                log.warning(f"[VoiceHandler.__init__] pyttsx3 init failed: {type(e).__name__}: {e}")
        else:
            log.debug("[VoiceHandler.__init__] pyttsx3 not available — skipping engine init")

        # Interrupt mode
        self.interrupt_mode = 'manual'  # 'auto' or 'manual'
        log.debug(f"[VoiceHandler.__init__] interrupt_mode='{self.interrupt_mode}'")

        # Playback callback
        self.on_playback_started = None
        log.debug("[VoiceHandler.__init__] on_playback_started=None")

        # Thread locks
        log.debug("[VoiceHandler.__init__] Creating thread locks...")
        self.vad_lock = threading.Lock()    # Protects VAD access
        self.state_lock = threading.Lock()  # Protects state changes
        self.config_lock = threading.Lock() # Protects configuration changes
        log.debug("[VoiceHandler.__init__] ✓ Locks created: vad_lock | state_lock | config_lock")

        # Audio settings
        self.sample_rate = 16000
        self.frame_duration = 30  # ms (10, 20, or 30 for webrtcvad)
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)
        log.debug(f"[VoiceHandler.__init__] Audio settings: sample_rate={self.sample_rate} | "
                  f"frame_duration={self.frame_duration}ms | frame_size={self.frame_size} samples")

        # VAD settings
        self.vad_webrtc_enabled = True
        self.vad_silero_enabled = False
        self.vad_aggressiveness = 3
        self.silero_threshold = 0.5
        self.silence_duration = 1.5
        log.debug(f"[VoiceHandler.__init__] VAD settings: webrtc_enabled={self.vad_webrtc_enabled} | "
                  f"silero_enabled={self.vad_silero_enabled} | aggressiveness={self.vad_aggressiveness} | "
                  f"silero_threshold={self.silero_threshold} | silence_duration={self.silence_duration}s")

        # TTS script path (set when a script is selected in settings)
        self.tts_script_path = ''
        log.debug("[VoiceHandler.__init__] TTS script path: (not set)")

        # Buffers
        self.audio_queue = queue.Queue()
        self.speech_buffer = deque(maxlen=50)
        self.silence_frames = 0
        log.debug("[VoiceHandler.__init__] Buffers initialized: audio_queue | speech_buffer(maxlen=50) | "
                  "silence_frames=0")

        # VAD - WebRTC
        log.debug(f"[VoiceHandler.__init__] Initializing WebRTC VAD with aggressiveness={self.vad_aggressiveness}")
        self.vad_webrtc = webrtcvad.Vad(self.vad_aggressiveness)
        log.info("[VoiceHandler.__init__] ✓ WebRTC VAD initialized")

        # VAD - Silero (lazy load when enabled)
        self.vad_silero_model = None
        self.silero_utils = None
        log.debug("[VoiceHandler.__init__] Silero VAD: model=None (lazy load)")

        # Initialize Silero if available and enabled
        if SILERO_AVAILABLE and self.vad_silero_enabled:
            log.info("[VoiceHandler.__init__] Silero available and enabled — loading model now...")
            self._init_silero_vad()
        else:
            log.debug(f"[VoiceHandler.__init__] Silero not loading at init: "
                      f"available={SILERO_AVAILABLE} | enabled={self.vad_silero_enabled}")

        # Threads
        self.capture_thread = None
        self.processing_thread = None
        log.debug("[VoiceHandler.__init__] Thread handles: capture_thread=None | processing_thread=None")

        # Speech recognition — import here so it doesn't block module load time
        log.debug("[VoiceHandler.__init__] Importing + initializing speech_recognition.Recognizer...")
        import speech_recognition as sr
        self.sr = sr  # store so methods can use self.sr.AudioData etc.
        self.recognizer = sr.Recognizer()
        log.info("[VoiceHandler.__init__] ✓ Speech recognizer initialized")

        # TTS settings
        self.tts_provider = 'edge-tts'
        self.tts_voice = 'en-CA-ClaraNeural'
        self.tts_rate = '+0%'
        self.tts_volume = '+0%'
        log.debug(f"[VoiceHandler.__init__] TTS settings: provider='{self.tts_provider}' | "
                  f"voice='{self.tts_voice}' | rate='{self.tts_rate}' | volume='{self.tts_volume}'")

        # Audio device settings
        self.input_device = None
        self.output_device = None
        log.debug("[VoiceHandler.__init__] Audio devices: input=None (system default) | "
                  "output=None (system default)")

        # Callbacks
        self.on_transcription = None
        self.on_state_change = None
        log.debug("[VoiceHandler.__init__] Callbacks: on_transcription=None | on_state_change=None")

        # pygame mixer is initialized lazily on first TTS playback, not at startup
        self._pygame_mixer_ready = False
        log.debug("[VoiceHandler.__init__] pygame mixer deferred — will init on first TTS use")

        # Vosk model (if available)
        self.vosk_model = None
        self.vosk_recognizer = None
        log.debug(f"[VoiceHandler.__init__] Vosk: available={VOSK_AVAILABLE} | model=None | "
                  "recognizer=None (not loaded at init)")

        log.info("[VoiceHandler.__init__] ✓ VoiceHandler initialization complete")

    def _emit_log_callback(self, message, level="INFO"):
        """Forward a message to the external log_callback if set."""
        if self.log_callback:
            try:
                self.log_callback(f"[Voice] {message}", level)
            except Exception as e:
                log.warning(f"[VoiceHandler._emit_log_callback] log_callback raised: "
                            f"{type(e).__name__}: {e}")

    def _init_silero_vad(self):
        """Initialize Silero VAD model (lazy loading)"""
        log.info("[VoiceHandler._init_silero_vad] ── Loading Silero VAD model ──────────────")
        if not SILERO_AVAILABLE:
            log.warning("[VoiceHandler._init_silero_vad] ✗ Silero not available "
                        "(torch/torchaudio not installed) — aborting")
            self._emit_log_callback("Silero VAD not available (torch/torchaudio not installed)", "WARNING")
            return False

        try:
            if self.vad_silero_model is None:
                log.debug("[VoiceHandler._init_silero_vad] Model not yet loaded — calling torch.hub.load...")
                self._emit_log_callback("Loading Silero VAD model...")
                model, utils = torch.hub.load(
                    repo_or_dir='snakers4/silero-vad',
                    model='silero_vad',
                    force_reload=False,
                    onnx=False
                )
                self.vad_silero_model = model
                self.silero_utils = utils
                log.info("[VoiceHandler._init_silero_vad] ✓ Silero VAD model loaded successfully")
                self._emit_log_callback("Silero VAD model loaded successfully", "SUCCESS")
            else:
                log.debug("[VoiceHandler._init_silero_vad] Model already loaded — no action")
            return True
        except Exception as e:
            log.error(f"[VoiceHandler._init_silero_vad] ✗ Failed to load Silero VAD: "
                      f"{type(e).__name__}: {e}")
            self._emit_log_callback(f"Failed to load Silero VAD: {e}", "ERROR")
            self.vad_silero_enabled = False
            log.warning("[VoiceHandler._init_silero_vad] vad_silero_enabled forced to False due to load failure")
            return False

    def set_vad_configuration(self, webrtc_enabled, silero_enabled, webrtc_aggressiveness, silero_threshold):
        """Configure VAD settings - THREAD SAFE"""
        log.info(f"[VoiceHandler.set_vad_configuration] ── Updating VAD config ──────────────")
        log.debug(f"[VoiceHandler.set_vad_configuration] webrtc_enabled={webrtc_enabled} | "
                  f"silero_enabled={silero_enabled} | webrtc_aggressiveness={webrtc_aggressiveness} | "
                  f"silero_threshold={silero_threshold}")

        with self.config_lock:
            log.debug("[VoiceHandler.set_vad_configuration] Acquired config_lock")
            self.vad_webrtc_enabled = webrtc_enabled
            self.vad_silero_enabled = silero_enabled
            self.vad_aggressiveness = webrtc_aggressiveness
            self.silero_threshold = silero_threshold

            if webrtc_enabled:
                log.debug(f"[VoiceHandler.set_vad_configuration] Rebuilding WebRTC VAD with "
                          f"aggressiveness={webrtc_aggressiveness}")
                with self.vad_lock:
                    self.vad_webrtc = webrtcvad.Vad(webrtc_aggressiveness)
                log.info(f"[VoiceHandler.set_vad_configuration] ✓ WebRTC VAD aggressiveness "
                         f"updated to {webrtc_aggressiveness}")
                self._emit_log_callback(f"WebRTC VAD aggressiveness set to {webrtc_aggressiveness}")
            else:
                log.debug("[VoiceHandler.set_vad_configuration] WebRTC VAD disabled — skipping rebuild")

            if silero_enabled:
                log.debug("[VoiceHandler.set_vad_configuration] Silero enabled — initializing...")
                if not self._init_silero_vad():
                    log.warning("[VoiceHandler.set_vad_configuration] ✗ Silero init failed — "
                                "silero_enabled forced False")
                    self._emit_log_callback("Silero VAD initialization failed, disabled", "WARNING")
            else:
                log.debug("[VoiceHandler.set_vad_configuration] Silero disabled — skipping init")

        status_msg = (f"VAD Config: WebRTC={'ON' if webrtc_enabled else 'OFF'} | "
                      f"Silero={'ON' if silero_enabled else 'OFF'}")
        log.info(f"[VoiceHandler.set_vad_configuration] ✓ {status_msg}")
        self._emit_log_callback(status_msg)

    def _audio_capture_loop(self):
        """Continuously capture audio from microphone"""
        log.info("[VoiceHandler._audio_capture_loop] ── Audio capture thread started ────────")
        log.debug(f"[VoiceHandler._audio_capture_loop] sample_rate={self.sample_rate} | "
                  f"frame_size={self.frame_size} | input_device={self.input_device}")
        try:
            with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype='int16',
                    blocksize=self.frame_size,
                    device=self.input_device
            ) as stream:
                log.info("[VoiceHandler._audio_capture_loop] ✓ InputStream opened — capturing audio")
                self._emit_log_callback("Audio capture started")

                frame_count = 0
                while self.is_listening:
                    try:
                        audio_data, overflowed = stream.read(self.frame_size)
                        frame_count += 1

                        if overflowed:
                            log.warning(f"[VoiceHandler._audio_capture_loop] ⚠ Audio buffer overflow "
                                        f"at frame {frame_count}")
                            self._emit_log_callback("Audio buffer overflowed", "WARNING")

                        audio_bytes = audio_data.tobytes()
                        is_speech = self._check_speech(audio_bytes, audio_data)

                        if frame_count % 100 == 0:
                            log.debug(f"[VoiceHandler._audio_capture_loop] Heartbeat: "
                                      f"frames_captured={frame_count} | last_is_speech={is_speech}")

                        self.audio_queue.put((audio_bytes, is_speech))

                    except Exception as e:
                        if self.is_listening:
                            log.error(f"[VoiceHandler._audio_capture_loop] ✗ Audio read error at "
                                      f"frame {frame_count}: {type(e).__name__}: {e}")
                            self._emit_log_callback(f"Audio read error: {e}", "WARNING")
                        else:
                            log.debug("[VoiceHandler._audio_capture_loop] Read error after stop — "
                                      "expected, ignoring")
                        break

        except Exception as e:
            log.error(f"[VoiceHandler._audio_capture_loop] ✗ InputStream error: "
                      f"{type(e).__name__}: {e}")
            self._emit_log_callback(f"Audio capture error: {e}", "ERROR")
            self.is_listening = False

        log.info("[VoiceHandler._audio_capture_loop] ── Audio capture thread exiting ────────")

    def _check_speech(self, audio_bytes, audio_array):
        """
        Check if audio contains speech using enabled VAD(s).
        THREAD SAFE - Uses locks to prevent heap corruption.

        Args:
            audio_bytes: Raw audio bytes (for WebRTC VAD)
            audio_array: NumPy array of audio samples (for Silero VAD)

        Returns:
            bool: True if speech detected, False otherwise
        """
        webrtc_result = False
        silero_result = False

        with self.config_lock:
            webrtc_enabled = self.vad_webrtc_enabled
            silero_enabled = self.vad_silero_enabled

        if webrtc_enabled:
            try:
                with self.vad_lock:
                    webrtc_result = self.vad_webrtc.is_speech(audio_bytes, self.sample_rate)
            except Exception as e:
                log.warning(f"[VoiceHandler._check_speech] WebRTC VAD error: {type(e).__name__}: {e}")
                self._emit_log_callback(f"WebRTC VAD error: {e}", "WARNING")
                webrtc_result = False

        if silero_enabled:
            with self.vad_lock:
                if self.vad_silero_model is not None:
                    try:
                        audio_float = audio_array.astype(np.float32) / 32768.0
                        audio_tensor = torch.from_numpy(audio_float).squeeze()

                        with torch.no_grad():
                            speech_prob = self.vad_silero_model(audio_tensor, self.sample_rate).item()

                        silero_result = speech_prob > self.silero_threshold
                        log.debug(f"[VoiceHandler._check_speech] Silero speech_prob={speech_prob:.4f} | "
                                  f"threshold={self.silero_threshold} | result={silero_result}")
                    except Exception as e:
                        log.warning(f"[VoiceHandler._check_speech] Silero VAD error: "
                                    f"{type(e).__name__}: {e}")
                        self._emit_log_callback(f"Silero VAD error: {e}", "WARNING")
                        silero_result = False
                else:
                    log.warning("[VoiceHandler._check_speech] Silero enabled but model is None — "
                                "defaulting silero_result=False")

        if webrtc_enabled and silero_enabled:
            combined = webrtc_result or silero_result
            log.debug(f"[VoiceHandler._check_speech] Both VADs active — "
                      f"webrtc={webrtc_result} | silero={silero_result} | combined={combined}")
            return combined
        elif webrtc_enabled:
            return webrtc_result
        elif silero_enabled:
            return silero_result
        else:
            log.debug("[VoiceHandler._check_speech] No VAD enabled — returning True (no filtering)")
            return True

    def set_tts_voice(self, voice):
        """Set TTS voice"""
        log.info(f"[VoiceHandler.set_tts_voice] Changing TTS voice: '{self.tts_voice}' → '{voice}'")
        self.tts_voice = voice
        self._emit_log_callback(f"TTS voice set to {voice}")
        log.debug("[VoiceHandler.set_tts_voice] ✓ Voice updated")

    def set_interrupt_mode(self, mode):
        """Set interrupt mode ('auto' or 'manual')"""
        log.info(f"[VoiceHandler.set_interrupt_mode] Changing interrupt mode: "
                 f"'{self.interrupt_mode}' → '{mode}'")
        self.interrupt_mode = mode
        self._emit_log_callback(f"Interrupt mode set to {mode}")
        log.debug("[VoiceHandler.set_interrupt_mode] ✓ Interrupt mode updated")

    def set_puter_server(self, puter_server):
        """Set Puter server reference for TTS"""
        log.info(f"[VoiceHandler.set_puter_server] Setting Puter server reference | "
                 f"server_provided={puter_server is not None}")
        self.puter_server = puter_server
        self._emit_log_callback("Puter server reference set")
        log.debug("[VoiceHandler.set_puter_server] ✓ Puter server reference updated")

    def set_tts_provider(self, provider):
        """Set TTS provider"""
        log.info(f"[VoiceHandler.set_tts_provider] Changing TTS provider: "
                 f"'{self.tts_provider}' → '{provider}'")
        self.tts_provider = provider
        self._emit_log_callback(f"TTS provider set to {provider}")
        log.debug("[VoiceHandler.set_tts_provider] ✓ TTS provider updated")

    def set_tts_script_path(self, path):
        """Set the custom TTS provider script path."""
        log.info(f"[VoiceHandler.set_tts_script_path] path='{path}'")
        self.tts_script_path = path
        self._emit_log_callback(f"Custom TTS script set: {path}")

    def set_puter_tts_settings(self, model, voice):
        """Legacy stub — kept to avoid AttributeError if called. No-op."""
        pass

    def set_vad_aggressiveness(self, level):
        """Set VAD aggressiveness (0-3)"""
        log.info(f"[VoiceHandler.set_vad_aggressiveness] Requested level={level}")
        try:
            level = int(level)
            if 0 <= level <= 3:
                old_level = self.vad_aggressiveness
                self.vad_aggressiveness = level
                self.vad_webrtc = webrtcvad.Vad(level)
                log.info(f"[VoiceHandler.set_vad_aggressiveness] ✓ VAD aggressiveness: "
                         f"{old_level} → {level}")
                self._emit_log_callback(f"VAD aggressiveness set to {level}")
            else:
                log.warning(f"[VoiceHandler.set_vad_aggressiveness] ✗ Invalid level {level} — "
                            "must be 0-3")
                self._emit_log_callback("VAD level must be 0-3", "WARNING")
        except Exception as e:
            log.error(f"[VoiceHandler.set_vad_aggressiveness] ✗ Error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"Error setting VAD: {e}", "ERROR")

    def list_audio_devices(self):
        """List all available audio devices"""
        log.info("[VoiceHandler.list_audio_devices] Querying available audio devices...")
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

        log.info(f"[VoiceHandler.list_audio_devices] ✓ Found {len(input_devices)} input device(s) | "
                 f"{len(output_devices)} output device(s)")
        log.debug(f"[VoiceHandler.list_audio_devices] Input devices: "
                  f"{[d['name'] for d in input_devices]}")
        log.debug(f"[VoiceHandler.list_audio_devices] Output devices: "
                  f"{[d['name'] for d in output_devices]}")

        return input_devices, output_devices

    def set_devices(self, input_device_id=None, output_device_id=None):
        """Set audio input/output devices"""
        log.info(f"[VoiceHandler.set_devices] input_device_id={input_device_id} | "
                 f"output_device_id={output_device_id}")
        self.input_device = input_device_id
        self.output_device = output_device_id

        if input_device_id is not None:
            devices = sd.query_devices()
            device_name = devices[input_device_id]['name']
            log.info(f"[VoiceHandler.set_devices] ✓ Input device set: [{input_device_id}] '{device_name}'")
            self._emit_log_callback(f"Input device: {device_name}")
        else:
            log.debug("[VoiceHandler.set_devices] Input device: using system default")

        if output_device_id is not None:
            devices = sd.query_devices()
            device_name = devices[output_device_id]['name']
            log.info(f"[VoiceHandler.set_devices] ✓ Output device set: [{output_device_id}] '{device_name}'")
            self._emit_log_callback(f"Output device: {device_name}")
        else:
            log.debug("[VoiceHandler.set_devices] Output device: using system default")

    def start_listening(self):
        """Start listening for voice input"""
        log.info("[VoiceHandler.start_listening] ── Start listening requested ────────────────")

        if self.is_listening:
            log.warning("[VoiceHandler.start_listening] Already in listening state — ignoring request")
            self._emit_log_callback("Already listening")
            return False

        try:
            log.debug("[VoiceHandler.start_listening] Setting is_listening=True and clearing buffers...")
            self.is_listening = True
            self.speech_buffer.clear()
            self.silence_frames = 0

            # Drain any stale queue items
            drained = 0
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                log.debug(f"[VoiceHandler.start_listening] Drained {drained} stale queue item(s)")

            log.debug("[VoiceHandler.start_listening] Spawning capture thread...")
            self.capture_thread = threading.Thread(
                target=self._audio_capture_loop, daemon=True, name="VoiceCapture"
            )
            self.capture_thread.start()
            log.info(f"[VoiceHandler.start_listening] ✓ Capture thread started | "
                     f"tid={self.capture_thread.ident}")

            log.debug("[VoiceHandler.start_listening] Spawning processing thread...")
            self.processing_thread = threading.Thread(
                target=self._processing_loop, daemon=True, name="VoiceProcessing"
            )
            self.processing_thread.start()
            log.info(f"[VoiceHandler.start_listening] ✓ Processing thread started | "
                     f"tid={self.processing_thread.ident}")

            self._emit_log_callback("Started listening")
            self._notify_state_change('listening')
            log.info("[VoiceHandler.start_listening] ✓ Listening active")
            return True

        except Exception as e:
            log.error(f"[VoiceHandler.start_listening] ✗ Failed to start: {type(e).__name__}: {e}")
            self._emit_log_callback(f"Error starting listening: {e}", "ERROR")
            self.is_listening = False
            return False

    def stop_listening(self):
        """Stop listening for voice input - THREAD SAFE CLEANUP"""
        log.info("[VoiceHandler.stop_listening] ── Stop listening requested ────────────────")

        if not self.is_listening:
            log.debug("[VoiceHandler.stop_listening] Not currently listening — no-op")
            return

        self._emit_log_callback("Stopping voice input...")

        log.debug("[VoiceHandler.stop_listening] Acquiring state_lock to set is_listening=False...")
        with self.state_lock:
            self.is_listening = False
        log.debug("[VoiceHandler.stop_listening] is_listening=False — threads will exit their loops")

        if self.capture_thread and self.capture_thread.is_alive():
            log.debug("[VoiceHandler.stop_listening] Waiting for capture thread (timeout=2.0s)...")
            self._emit_log_callback("Waiting for capture thread...")
            self.capture_thread.join(timeout=2.0)
            if self.capture_thread.is_alive():
                log.warning("[VoiceHandler.stop_listening] ⚠ Capture thread did not stop within timeout")
                self._emit_log_callback("Capture thread did not stop cleanly", "WARNING")
            else:
                log.debug("[VoiceHandler.stop_listening] ✓ Capture thread stopped cleanly")
        else:
            log.debug("[VoiceHandler.stop_listening] Capture thread not running — skipping join")

        if self.processing_thread and self.processing_thread.is_alive():
            log.debug("[VoiceHandler.stop_listening] Waiting for processing thread (timeout=2.0s)...")
            self._emit_log_callback("Waiting for processing thread...")
            self.processing_thread.join(timeout=2.0)
            if self.processing_thread.is_alive():
                log.warning("[VoiceHandler.stop_listening] ⚠ Processing thread did not stop within timeout")
                self._emit_log_callback("Processing thread did not stop cleanly", "WARNING")
            else:
                log.debug("[VoiceHandler.stop_listening] ✓ Processing thread stopped cleanly")
        else:
            log.debug("[VoiceHandler.stop_listening] Processing thread not running — skipping join")

        drained = 0
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        log.debug(f"[VoiceHandler.stop_listening] Drained {drained} item(s) from audio queue")

        self.capture_thread = None
        self.processing_thread = None
        log.debug("[VoiceHandler.stop_listening] Thread handles cleared")

        self._emit_log_callback("Stopped listening")
        self._notify_state_change('inactive')
        log.info("[VoiceHandler.stop_listening] ✓ Listening stopped")

    def interrupt_speech(self):
        """Interrupt current TTS playback"""
        log.info("[VoiceHandler.interrupt_speech] ── Speech interrupt requested ────────────")

        if not self.is_speaking:
            log.debug("[VoiceHandler.interrupt_speech] Not currently speaking — no-op")
            return

        self._emit_log_callback("Interrupting speech...")
        self.is_speaking = False
        log.debug("[VoiceHandler.interrupt_speech] is_speaking=False")

        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                log.info("[VoiceHandler.interrupt_speech] ✓ pygame mixer stopped")
            else:
                log.warning("[VoiceHandler.interrupt_speech] pygame mixer not initialized — "
                            "nothing to stop")
        except Exception as e:
            log.warning(f"[VoiceHandler.interrupt_speech] Error stopping playback: "
                        f"{type(e).__name__}: {e}")
            self._emit_log_callback(f"Error stopping playback: {e}", "WARNING")

        new_state = 'listening' if self.is_listening else 'inactive'
        self._emit_log_callback("Speech interrupted")
        self._notify_state_change(new_state)
        log.info(f"[VoiceHandler.interrupt_speech] ✓ Speech interrupted | new_state='{new_state}'")

    def _processing_loop(self):
        """Process audio queue and detect speech segments"""
        log.info("[VoiceHandler._processing_loop] ── Processing thread started ────────────")
        audio_buffer = []
        currently_speaking = False
        frames_processed = 0

        while self.is_listening:
            try:
                audio_bytes, is_speech = self.audio_queue.get(timeout=0.1)
                frames_processed += 1

                if is_speech:
                    if not currently_speaking:
                        log.info("[VoiceHandler._processing_loop] ▶ Speech segment started")
                        self._emit_log_callback("Speech started")
                        currently_speaking = True
                        audio_buffer = []

                    audio_buffer.append(audio_bytes)
                    self.silence_frames = 0

                    if self.interrupt_mode == 'auto' and self.is_speaking:
                        log.info("[VoiceHandler._processing_loop] Auto-interrupt triggered — "
                                 "calling interrupt_speech()")
                        self._emit_log_callback("Auto-interrupting TTS due to voice detection")
                        self.interrupt_speech()

                else:
                    if currently_speaking:
                        audio_buffer.append(audio_bytes)
                        self.silence_frames += 1
                        silence_duration = (self.silence_frames * self.frame_duration) / 1000

                        log.debug(f"[VoiceHandler._processing_loop] Silence frame "
                                  f"{self.silence_frames} | duration={silence_duration:.2f}s / "
                                  f"{self.silence_duration}s threshold")

                        if silence_duration >= self.silence_duration:
                            log.info(f"[VoiceHandler._processing_loop] ■ Silence threshold reached "
                                     f"({silence_duration:.1f}s) — dispatching segment | "
                                     f"buffer_frames={len(audio_buffer)}")
                            self._emit_log_callback(
                                f"Silence detected ({silence_duration:.1f}s), processing speech"
                            )
                            self._process_audio_segment(audio_buffer)

                            audio_buffer = []
                            currently_speaking = False
                            self.silence_frames = 0
                            log.debug("[VoiceHandler._processing_loop] Segment dispatched — "
                                      "reset to idle state")

            except queue.Empty:
                continue
            except Exception as e:
                if self.is_listening:
                    log.error(f"[VoiceHandler._processing_loop] ✗ Processing error: "
                              f"{type(e).__name__}: {e}")
                    self._emit_log_callback(f"Processing error: {e}", "ERROR")

        log.info(f"[VoiceHandler._processing_loop] ── Processing thread exiting | "
                 f"total_frames_processed={frames_processed} ────────")

    def _process_audio_segment(self, audio_buffer):
        """Process captured audio segment with STT"""
        log.info(f"[VoiceHandler._process_audio_segment] ── Processing audio segment | "
                 f"frames={len(audio_buffer)} ──────────")

        if not audio_buffer:
            log.warning("[VoiceHandler._process_audio_segment] Empty audio buffer — skipping")
            return

        self.is_processing = True
        self._notify_state_change('processing')
        log.debug("[VoiceHandler._process_audio_segment] is_processing=True | state → 'processing'")

        try:
            audio_data = b''.join(audio_buffer)
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            total_bytes = len(audio_data)
            duration_s = total_bytes / (self.sample_rate * 2)
            log.debug(f"[VoiceHandler._process_audio_segment] Audio assembled: "
                      f"bytes={total_bytes} | duration≈{duration_s:.2f}s")

            audio_sr = self.sr.AudioData(audio_data, self.sample_rate, 2)
            log.debug("[VoiceHandler._process_audio_segment] sr.AudioData created — "
                      "sending to Google STT...")
            self._emit_log_callback("Transcribing with Google Speech Recognition...")

            try:
                text = self.recognizer.recognize_google(audio_sr)
                log.info(f"[VoiceHandler._process_audio_segment] ✓ Transcription: '{text}'")
                self._emit_log_callback(f"Transcribed: {text}")

                if self.on_transcription and text and text.strip():
                    log.debug("[VoiceHandler._process_audio_segment] Calling on_transcription callback...")
                    self.on_transcription(text)
                    log.debug("[VoiceHandler._process_audio_segment] ✓ on_transcription callback complete")
                else:
                    log.debug(f"[VoiceHandler._process_audio_segment] No callback dispatched — "
                              f"has_callback={self.on_transcription is not None} | "
                              f"text_empty={not bool(text and text.strip())}")

            except self.sr.UnknownValueError:
                log.warning("[VoiceHandler._process_audio_segment] ✗ Could not understand audio "
                            "(UnknownValueError)")
                self._emit_log_callback("Could not understand audio", "WARNING")
            except self.sr.RequestError as e:
                log.error(f"[VoiceHandler._process_audio_segment] ✗ STT request error: "
                          f"{type(e).__name__}: {e}")
                self._emit_log_callback(f"Speech recognition error: {e}", "ERROR")

        except Exception as e:
            log.error(f"[VoiceHandler._process_audio_segment] ✗ STT error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"STT error: {e}", "ERROR")

        finally:
            self.is_processing = False
            self._notify_state_change('listening')
            log.debug("[VoiceHandler._process_audio_segment] is_processing=False | state → 'listening'")

    async def speak_text(self, text):
        """Convert text to speech and play it"""
        log.info(f"[VoiceHandler.speak_text] ── TTS requested | text_len={len(text)} ──────────")
        log.debug(f"[VoiceHandler.speak_text] Raw text preview: '{text[:80].replace(chr(10), '↵')}'")

        if not text or not text.strip():
            log.warning("[VoiceHandler.speak_text] Empty text — aborting TTS")
            return

        filtered_text = self._filter_text_for_tts(text)
        log.debug(f"[VoiceHandler.speak_text] Filtered text_len={len(filtered_text)} | "
                  f"preview='{filtered_text[:80].replace(chr(10), '↵')}'")

        if not filtered_text or not filtered_text.strip():
            log.warning("[VoiceHandler.speak_text] No speakable text after filtering — aborting TTS")
            self._emit_log_callback("[TTS] No speakable text after filtering")
            return

        self.is_speaking = True
        self._notify_state_change('speaking')
        log.debug("[VoiceHandler.speak_text] is_speaking=True | state → 'speaking'")

        try:
            log.info(f"[VoiceHandler.speak_text] Routing to provider='{self.tts_provider}'")
            if self.tts_provider == 'pyttsx3':
                log.debug("[VoiceHandler.speak_text] → _speak_pyttsx3()")
                self._emit_log_callback("[TTS] Using pyttsx3 (offline)")
                await self._speak_pyttsx3(filtered_text)

            elif self.tts_provider == 'edge-tts':
                log.debug("[VoiceHandler.speak_text] → _speak_edge_tts()")
                self._emit_log_callback("[TTS] Using Edge TTS")
                await self._speak_edge_tts(filtered_text)

            elif self.tts_provider == 'custom_script':
                log.debug(f"[VoiceHandler.speak_text] → _speak_custom_script_tts() | "
                          f"script='{self.tts_script_path}'")
                self._emit_log_callback(f"[TTS] Using custom script provider")
                await self._speak_custom_script_tts(filtered_text)

            else:
                log.warning(f"[VoiceHandler.speak_text] ✗ Unknown provider '{self.tts_provider}' — "
                            "no audio produced")
                self._emit_log_callback(
                    f"[TTS] Provider '{self.tts_provider}' not implemented", "WARNING"
                )

        except Exception as e:
            log.error(f"[VoiceHandler.speak_text] ✗ TTS exception: {type(e).__name__}: {e}")
            self._emit_log_callback(f"[TTS] Error: {e}", "ERROR")

        finally:
            self.is_speaking = False
            new_state = 'listening' if self.is_listening else 'inactive'
            self._notify_state_change(new_state)
            log.info(f"[VoiceHandler.speak_text] ✓ TTS complete | is_speaking=False | "
                     f"new_state='{new_state}'")

    def _ensure_pygame_mixer(self):
        """Lazy-init pygame mixer on first TTS use instead of at startup."""
        if not self._pygame_mixer_ready:
            try:
                pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
                self._pygame_mixer_ready = True
                log.info("[VoiceHandler] ✓ pygame mixer initialized (lazy)")
            except Exception as e:
                log.warning(f"[VoiceHandler] pygame mixer lazy init failed: {e}")

    async def _speak_pyttsx3(self, text):
        """Speak using pyttsx3 (offline TTS)"""
        log.info(f"[VoiceHandler._speak_pyttsx3] ── pyttsx3 TTS | text_len={len(text)} ──────")

        try:
            if not PYTTSX3_AVAILABLE or not self.pyttsx3_engine:
                log.error("[VoiceHandler._speak_pyttsx3] ✗ pyttsx3 not available or engine not initialized")
                self._emit_log_callback("pyttsx3 not available", "ERROR")
                return

            log.debug("[VoiceHandler._speak_pyttsx3] Spawning speak thread...")

            def speak_thread():
                log.debug("[VoiceHandler._speak_pyttsx3:thread] pyttsx3 say() + runAndWait() starting")
                try:
                    self.pyttsx3_engine.say(text)
                    self.pyttsx3_engine.runAndWait()
                    log.debug("[VoiceHandler._speak_pyttsx3:thread] ✓ pyttsx3 runAndWait() complete")
                except Exception as e:
                    log.error(f"[VoiceHandler._speak_pyttsx3:thread] ✗ Error: {type(e).__name__}: {e}")
                    self._emit_log_callback(f"pyttsx3 error: {e}", "ERROR")

            thread = threading.Thread(target=speak_thread, daemon=True, name="pyttsx3Speak")
            thread.start()
            log.debug(f"[VoiceHandler._speak_pyttsx3] Speak thread started | tid={thread.ident}")

            if self.on_playback_started:
                log.debug("[VoiceHandler._speak_pyttsx3] Calling on_playback_started callback...")
                try:
                    self.on_playback_started()
                    log.debug("[VoiceHandler._speak_pyttsx3] ✓ on_playback_started callback complete")
                except Exception as e:
                    log.warning(f"[VoiceHandler._speak_pyttsx3] on_playback_started error: "
                                f"{type(e).__name__}: {e}")
                    self._emit_log_callback(f"Error in playback callback: {e}", "ERROR")

            log.debug("[VoiceHandler._speak_pyttsx3] Waiting for speak thread to finish...")
            thread.join()
            log.info("[VoiceHandler._speak_pyttsx3] ✓ pyttsx3 TTS complete")

        except Exception as e:
            log.error(f"[VoiceHandler._speak_pyttsx3] ✗ Outer error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"pyttsx3 TTS error: {e}", "ERROR")

    async def _speak_puter_tts(self, text):
        """Speak using Puter.js TTS"""
        self._ensure_pygame_mixer()
        log.info(f"[VoiceHandler._speak_puter_tts] ── Puter TTS | text_len={len(text)} | "
                 f"model='{self.puter_tts_model}' | voice='{self.puter_tts_voice}' ──────")
        try:
            if not self.puter_server:
                log.error("[VoiceHandler._speak_puter_tts] ✗ puter_server is None — aborting")
                self._emit_log_callback("Puter server not available for TTS", "ERROR")
                return

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()
            log.debug(f"[VoiceHandler._speak_puter_tts] Temp file: '{temp_path}'")

            log.debug("[VoiceHandler._speak_puter_tts] Calling puter_server.text_to_speech()...")
            audio_url = self.puter_server.text_to_speech(
                text=text,
                model=self.puter_tts_model,
                voice=self.puter_tts_voice,
                save_to=temp_path
            )

            if not audio_url:
                log.error("[VoiceHandler._speak_puter_tts] ✗ text_to_speech() returned None/empty")
                self._emit_log_callback("Failed to generate speech with Puter", "ERROR")
                return

            log.debug(f"[VoiceHandler._speak_puter_tts] ✓ Audio generated | url='{audio_url}' | "
                      f"loading into pygame...")
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            log.info("[VoiceHandler._speak_puter_tts] ✓ Pygame playback started")

            if self.on_playback_started:
                log.debug("[VoiceHandler._speak_puter_tts] Calling on_playback_started callback...")
                try:
                    self.on_playback_started()
                    log.debug("[VoiceHandler._speak_puter_tts] ✓ on_playback_started complete")
                    self._emit_log_callback("Playback started callback executed")
                except Exception as e:
                    log.warning(f"[VoiceHandler._speak_puter_tts] on_playback_started error: "
                                f"{type(e).__name__}: {e}")
                    self._emit_log_callback(f"Error in playback callback: {e}", "ERROR")

            log.debug("[VoiceHandler._speak_puter_tts] Entering playback wait loop...")
            wait_iters = 0
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:
                    log.info("[VoiceHandler._speak_puter_tts] is_speaking=False detected — "
                             "stopping playback")
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)
                wait_iters += 1

            log.info(f"[VoiceHandler._speak_puter_tts] ✓ Playback finished | "
                     f"wait_iters={wait_iters}")

            try:
                os.unlink(temp_path)
                log.debug(f"[VoiceHandler._speak_puter_tts] ✓ Temp file removed: '{temp_path}'")
            except Exception as e:
                log.warning(f"[VoiceHandler._speak_puter_tts] Could not remove temp file: "
                            f"{type(e).__name__}: {e}")

        except Exception as e:
            log.error(f"[VoiceHandler._speak_puter_tts] ✗ Puter TTS error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"Puter TTS error: {e}", "ERROR")

    async def _speak_puter_elevenlabs(self, text):
        self._ensure_pygame_mixer()
        """Speak using Puter.js with ElevenLabs"""
        voice_id = getattr(self, 'elevenlabs_voice_id', None)
        log.info(f"[VoiceHandler._speak_puter_elevenlabs] ── Puter+ElevenLabs TTS | "
                 f"text_len={len(text)} | voice_id='{voice_id}' ──────")
        try:
            if not self.puter_server:
                log.error("[VoiceHandler._speak_puter_elevenlabs] ✗ puter_server is None — aborting")
                self._emit_log_callback("Puter server not available for ElevenLabs TTS", "ERROR")
                return

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()
            log.debug(f"[VoiceHandler._speak_puter_elevenlabs] Temp file: '{temp_path}'")

            log.debug("[VoiceHandler._speak_puter_elevenlabs] Calling puter_server.text_to_speech() "
                      "with ElevenLabs provider...")
            audio_url = self.puter_server.text_to_speech(
                text=text,
                model='eleven_v3',
                voice=voice_id,
                provider='elevenlabs',
                save_to=temp_path
            )

            if not audio_url:
                log.error("[VoiceHandler._speak_puter_elevenlabs] ✗ text_to_speech() returned "
                          "None/empty")
                self._emit_log_callback("Failed to generate speech with ElevenLabs", "ERROR")
                return

            log.debug(f"[VoiceHandler._speak_puter_elevenlabs] ✓ Audio generated | url='{audio_url}' "
                      f"| loading into pygame...")
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            log.info("[VoiceHandler._speak_puter_elevenlabs] ✓ Pygame playback started")

            if self.on_playback_started:
                log.debug("[VoiceHandler._speak_puter_elevenlabs] Calling on_playback_started...")
                try:
                    self.on_playback_started()
                    log.debug("[VoiceHandler._speak_puter_elevenlabs] ✓ on_playback_started complete")
                    self._emit_log_callback("Playback started callback executed")
                except Exception as e:
                    log.warning(f"[VoiceHandler._speak_puter_elevenlabs] on_playback_started error: "
                                f"{type(e).__name__}: {e}")
                    self._emit_log_callback(f"Error in playback callback: {e}", "ERROR")

            log.debug("[VoiceHandler._speak_puter_elevenlabs] Entering playback wait loop...")
            wait_iters = 0
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:
                    log.info("[VoiceHandler._speak_puter_elevenlabs] is_speaking=False — "
                             "stopping playback")
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)
                wait_iters += 1

            log.info(f"[VoiceHandler._speak_puter_elevenlabs] ✓ Playback finished | "
                     f"wait_iters={wait_iters}")

            try:
                os.unlink(temp_path)
                log.debug(f"[VoiceHandler._speak_puter_elevenlabs] ✓ Temp file removed: '{temp_path}'")
            except Exception as e:
                log.warning(f"[VoiceHandler._speak_puter_elevenlabs] Could not remove temp file: "
                            f"{type(e).__name__}: {e}")

        except Exception as e:
            log.error(f"[VoiceHandler._speak_puter_elevenlabs] ✗ ElevenLabs TTS error: "
                      f"{type(e).__name__}: {e}")
            self._emit_log_callback(f"ElevenLabs TTS error: {e}", "ERROR")

    async def _speak_edge_tts(self, text):
        """Speak using Edge TTS (FREE)"""
        self._ensure_pygame_mixer()
        log.info(f"[VoiceHandler._speak_edge_tts] ── Edge TTS | text_len={len(text)} | "
                 f"voice='{self.tts_voice}' | rate='{self.tts_rate}' | "
                 f"volume='{self.tts_volume}' ──────")
        try:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()
            log.debug(f"[VoiceHandler._speak_edge_tts] Temp file: '{temp_path}'")

            log.debug("[VoiceHandler._speak_edge_tts] Creating edge_tts.Communicate and saving...")
            communicate = edge_tts.Communicate(
                text, self.tts_voice, rate=self.tts_rate, volume=self.tts_volume
            )
            await communicate.save(temp_path)
            log.debug(f"[VoiceHandler._speak_edge_tts] ✓ Audio saved to '{temp_path}'")

            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            log.info("[VoiceHandler._speak_edge_tts] ✓ Pygame playback started")

            if self.on_playback_started:
                log.debug("[VoiceHandler._speak_edge_tts] Calling on_playback_started callback...")
                try:
                    self.on_playback_started()
                    log.debug("[VoiceHandler._speak_edge_tts] ✓ on_playback_started complete")
                    self._emit_log_callback("Playback started callback executed")
                except Exception as e:
                    log.warning(f"[VoiceHandler._speak_edge_tts] on_playback_started error: "
                                f"{type(e).__name__}: {e}")
                    self._emit_log_callback(f"Error in playback callback: {e}", "ERROR")

            log.debug("[VoiceHandler._speak_edge_tts] Entering playback wait loop...")
            wait_iters = 0
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:
                    log.info("[VoiceHandler._speak_edge_tts] is_speaking=False — stopping playback")
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)
                wait_iters += 1

            log.info(f"[VoiceHandler._speak_edge_tts] ✓ Playback finished | wait_iters={wait_iters}")

            try:
                os.unlink(temp_path)
                log.debug(f"[VoiceHandler._speak_edge_tts] ✓ Temp file removed: '{temp_path}'")
            except Exception as e:
                log.warning(f"[VoiceHandler._speak_edge_tts] Could not remove temp file: "
                            f"{type(e).__name__}: {e}")

        except Exception as e:
            log.error(f"[VoiceHandler._speak_edge_tts] ✗ Edge TTS error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"Edge TTS error: {e}", "ERROR")

    async def _speak_custom_script_tts(self, text):
        """Speak using a custom TTS provider script.
        The script must define:  speak(text: str, save_to: str) -> bool
        """
        self._ensure_pygame_mixer()
        import importlib.util, traceback
        log.info(f"[VoiceHandler._speak_custom_script_tts] ── Custom Script TTS | "
                 f"text_len={len(text)} | script='{self.tts_script_path}' ──────")
        try:
            if not self.tts_script_path or not os.path.isfile(self.tts_script_path):
                log.error(f"[VoiceHandler._speak_custom_script_tts] ✗ Script not found: "
                          f"'{self.tts_script_path}'")
                self._emit_log_callback("Custom TTS script not found — check settings", "ERROR")
                return

            spec = importlib.util.spec_from_file_location("custom_tts_provider", self.tts_script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if not hasattr(module, 'speak') or not callable(module.speak):
                log.error("[VoiceHandler._speak_custom_script_tts] ✗ Script missing speak() function")
                self._emit_log_callback(
                    "Custom TTS script must define speak(text: str, save_to: str) -> bool", "ERROR")
                return

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_path = temp_file.name
            temp_file.close()
            log.debug(f"[VoiceHandler._speak_custom_script_tts] Temp file: '{temp_path}'")

            success = module.speak(text, temp_path)
            if not success:
                log.error("[VoiceHandler._speak_custom_script_tts] ✗ speak() returned False")
                self._emit_log_callback("Custom TTS speak() returned False", "ERROR")
                return

            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            log.info("[VoiceHandler._speak_custom_script_tts] ✓ Pygame playback started")

            if self.on_playback_started:
                try:
                    self.on_playback_started()
                except Exception as cb_e:
                    log.warning(f"[VoiceHandler._speak_custom_script_tts] on_playback_started error: {cb_e}")

            wait_iters = 0
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:
                    log.info("[VoiceHandler._speak_custom_script_tts] is_speaking=False — stopping")
                    pygame.mixer.music.stop()
                    break
                await asyncio.sleep(0.1)
                wait_iters += 1

            log.info(f"[VoiceHandler._speak_custom_script_tts] ✓ Playback finished | "
                     f"wait_iters={wait_iters}")

            try:
                os.unlink(temp_path)
            except Exception:
                pass

        except Exception as e:
            log.error(f"[VoiceHandler._speak_custom_script_tts] ✗ Error: {type(e).__name__}: {e}\n"
                      f"{traceback.format_exc()}")
            self._emit_log_callback(f"Custom TTS error: {e}", "ERROR")

    def _filter_text_for_tts(self, text):
        """
        Filter text for TTS - remove code blocks ONLY, keep everything else.
        Preserves: emojis, brackets, symbols, expressive text.
        """
        log.debug(f"[VoiceHandler._filter_text_for_tts] Filtering {len(text)} chars...")
        original_text = text

        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'`[^`]+`', '', text)

        # Strip markdown formatting while keeping content
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)
        text = re.sub(r'~~([^~]+)~~', r'\1', text)

        # Remove links but keep text
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

        # Remove headers
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)

        # Remove list markers
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        if original_text != text:
            log.debug(f"[VoiceHandler._filter_text_for_tts] Text was modified | "
                      f"original_len={len(original_text)} → filtered_len={len(text)}")
            self._emit_log_callback(f"[TTS Filter] Original: {original_text[:80]}...")
            self._emit_log_callback(f"[TTS Filter] Filtered: {text[:80]}...")
        else:
            log.debug("[VoiceHandler._filter_text_for_tts] No changes after filtering")

        return text

    def _remove_emotion_brackets(self, text):
        """
        Remove ElevenLabs emotion/voice effect brackets for DISPLAY only.
        Examples: [happy], [giggles], [whispers], [pause]
        Called AFTER TTS processing, before displaying in chat.
        """
        log.debug(f"[VoiceHandler._remove_emotion_brackets] Stripping emotion brackets from "
                  f"{len(text)} chars...")
        cleaned = re.sub(r'\[([^\]]+)\]', '', text)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = cleaned.strip()

        if cleaned != text:
            removed_count = text.count('[') - cleaned.count('[')
            log.debug(f"[VoiceHandler._remove_emotion_brackets] ✓ Removed ~{removed_count} bracket(s) | "
                      f"original_len={len(text)} → cleaned_len={len(cleaned)}")
        else:
            log.debug("[VoiceHandler._remove_emotion_brackets] No brackets to remove")

        return cleaned

    def speak_text_sync(self, text):
        """Synchronous wrapper for speak_text"""
        log.info(f"[VoiceHandler.speak_text_sync] ── Sync TTS wrapper | text_len={len(text)} ──")

        if not text or not text.strip():
            log.warning("[VoiceHandler.speak_text_sync] Empty text — aborting")
            return

        log.debug("[VoiceHandler.speak_text_sync] Creating new event loop...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            log.debug("[VoiceHandler.speak_text_sync] Running speak_text() in event loop...")
            loop.run_until_complete(self.speak_text(text))
            log.info("[VoiceHandler.speak_text_sync] ✓ Sync TTS complete")
        finally:
            loop.close()
            log.debug("[VoiceHandler.speak_text_sync] Event loop closed")

    def set_elevenlabs_settings(self, enabled, voice_id):
        """Set ElevenLabs TTS settings"""
        log.info(f"[VoiceHandler.set_elevenlabs_settings] enabled={enabled} | voice_id='{voice_id}'")
        self.use_elevenlabs = enabled
        self.elevenlabs_voice_id = voice_id
        if enabled:
            log.info(f"[VoiceHandler.set_elevenlabs_settings] ✓ ElevenLabs enabled with "
                     f"voice_id='{voice_id}'")
            self._emit_log_callback(f"ElevenLabs enabled with voice: {voice_id}")
        else:
            log.debug("[VoiceHandler.set_elevenlabs_settings] ElevenLabs disabled")

    def _notify_state_change(self, state):
        """Notify state change (thread-safe)"""
        log.debug(f"[VoiceHandler._notify_state_change] state='{state}' | "
                  f"has_callback={self.on_state_change is not None}")
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception as e:
                log.warning(f"[VoiceHandler._notify_state_change] ✗ Callback error: "
                            f"{type(e).__name__}: {e}")
                self._emit_log_callback(f"Error in state change callback: {e}", "ERROR")

    def cleanup(self):
        """Cleanup resources"""
        log.info("[VoiceHandler.cleanup] ── Cleanup started ──────────────────────────────────")

        log.debug("[VoiceHandler.cleanup] Stopping listening...")
        self.stop_listening()

        log.debug("[VoiceHandler.cleanup] Interrupting any ongoing speech...")
        self.interrupt_speech()

        log.debug("[VoiceHandler.cleanup] Clearing all callbacks...")
        self.on_transcription = None
        self.on_state_change = None
        self.on_playback_started = None
        log.debug("[VoiceHandler.cleanup] ✓ Callbacks cleared")

        log.debug("[VoiceHandler.cleanup] Quitting pygame mixer...")
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
                log.info("[VoiceHandler.cleanup] ✓ pygame mixer quit")
            else:
                log.debug("[VoiceHandler.cleanup] pygame mixer was not initialized — skipping quit")
        except Exception as e:
            log.warning(f"[VoiceHandler.cleanup] Error during mixer cleanup: "
                        f"{type(e).__name__}: {e}")
            self._emit_log_callback(f"Error during mixer cleanup: {e}", "WARNING")

        log.info("[VoiceHandler.cleanup] ✓ VoiceHandler cleanup complete")