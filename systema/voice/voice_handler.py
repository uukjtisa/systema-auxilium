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
import sys
import time
import random
import shutil
import hashlib
from pathlib import Path
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

# Sentinel tag for a silent pause enqueued between utterances. A pause travels
# the normal FIFO pipeline as ((_PAUSE_TAG, seconds), gen, '') so ordering,
# generation staleness and stop_all draining all apply to it like real audio.
_PAUSE_TAG = '__pause__'


class VoiceHandler:
    """Handles all voice input/output operations"""

    # ── Auto barge-in (duck-then-confirm) tuning ─────────────────────────────
    # Auto mode never hard-stops playback on VAD alone. A qualified speech
    # candidate only DUCKS the volume; the stop is committed solely once STT
    # transcribes real non-echo words. Per preset:
    #   window_frames     30 ms frames in the candidate window
    #   min_speech_ratio  fraction of the window that must be VAD-speech
    #   margin_db         dB the speech frames' mean RMS must clear the
    #                     ambient noise floor by
    #   confirm_timeout   restore full volume when no STT segment is open by
    #                     then (seconds since duck)
    #   hard_cap          unconditional restore even mid-segment — continuous
    #                     noise can hold an STT segment open indefinitely
    _BARGEIN_PRESETS = {
        'relaxed':  {'window_frames': 15, 'min_speech_ratio': 0.85, 'margin_db': 12.0,
                     'confirm_timeout': 3.5, 'hard_cap': 9.0},
        'balanced': {'window_frames': 10, 'min_speech_ratio': 0.75, 'margin_db': 9.0,
                     'confirm_timeout': 2.5, 'hard_cap': 8.0},
        'eager':    {'window_frames': 7,  'min_speech_ratio': 0.70, 'margin_db': 6.0,
                     'confirm_timeout': 2.0, 'hard_cap': 6.0},
    }
    _DUCK_VOLUME = 0.2             # playback volume while a candidate is pending
    _ECHO_OVERLAP_THRESHOLD = 0.6  # transcript/TTS word-overlap ratio => echo
    _NOISE_FLOOR_ALPHA = 0.05      # EMA weight per idle frame
    _NOISE_FLOOR_MIN = 30.0        # int16 RMS clamp (keeps margins meaningful)
    _SPEECH_GRACE_S = 0.4          # recent-speech window that defers timeout restore

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
        # True only while audio is AUDIBLY playing (pygame play() → end/stop).
        # Auto-interrupt keys on this, not is_speaking: during the silent
        # synthesis phase a stray speech frame must not invisibly kill the
        # upcoming audio (the barge-in transcription path handles real speech).
        self.playback_active = False
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

        # Auto barge-in (duck-then-confirm) state — see _update_bargein_frame.
        self.bargein_preset = 'balanced'
        self._bargein_cfg = dict(self._BARGEIN_PRESETS['balanced'])
        self._bargein_window = deque(maxlen=self._bargein_cfg['window_frames'])  # (is_speech, rms)
        self._noise_floor = None       # EMA of ambient int16 RMS, learned while idle
        self._ducked = False
        self._duck_started = 0.0
        self._last_speech_ts = 0.0
        self._duck_lock = threading.Lock()
        self._recent_tts_texts = deque(maxlen=3)  # played chunk texts (echo check)

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

        # Serialized speech pipeline — a SYNTH worker renders text chunks to
        # audio files while a PLAYBACK worker plays them strictly one at a time
        # (prefetch: chunk N+1 renders while chunk N plays, so chunk boundaries
        # are seamless). audio_ready is bounded → natural prefetch depth.
        self.speech_queue = queue.Queue()               # text chunks in
        self.audio_ready = queue.Queue(maxsize=2)       # (path, generation, text) out
        self._speech_worker = None                      # synth thread
        self._playback_worker = None                    # playback thread
        self._speech_worker_lock = threading.Lock()
        self._speech_generation = 0                     # stop_all bumps → stale results discarded
        self._synth_in_flight = False
        self._custom_tts_module = None                  # cached (path, module)
        # Filler interjections ("Hmm...", "And...") in the SAME voice, played
        # when playback drains before the next chunk's audio is ready.
        self.fillers_enabled = True
        self._last_filler = None
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

    def set_bargein_sensitivity(self, preset):
        """Set auto barge-in sensitivity ('relaxed' | 'balanced' | 'eager')."""
        if preset not in self._BARGEIN_PRESETS:
            log.warning(f"[VoiceHandler.set_bargein_sensitivity] Unknown preset "
                        f"'{preset}' — falling back to 'balanced'")
            preset = 'balanced'
        with self.config_lock:
            self.bargein_preset = preset
            self._bargein_cfg = dict(self._BARGEIN_PRESETS[preset])
            self._bargein_window = deque(maxlen=self._bargein_cfg['window_frames'])
        self._emit_log_callback(f"Barge-in sensitivity set to {preset}")
        log.info(f"[VoiceHandler.set_bargein_sensitivity] ✓ preset='{preset}'")

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

    # Preferred host-API name (substring, case-insensitive) per platform. The
    # SAME physical mic is listed once per host API on Windows (MME, WASAPI,
    # DirectSound, WDM-KS) — and MME truncates names to 31 chars, so name-based
    # merging across APIs is impossible. Instead we keep ONLY the one modern
    # platform API and drop the rest outright.
    _HOSTAPI_PREFERENCE = {
        'win32':  ['wasapi'],
        'linux':  ['pipewire', 'pulse', 'alsa'],
        'darwin': ['core audio', 'coreaudio'],
    }

    # Junk/pseudo devices that aren't real selectable mics (case-insensitive
    # substrings). Windows mappers + loopback capture, and Linux ALSA plugins.
    _DEVICE_NAME_JUNK = (
        'primary sound capture driver', 'sound mapper', 'microsoft sound mapper',
        '@system32', 'stereo mix', 'what u hear', 'wave out mix', 'loopback',
        'sysdefault', 'dmix', 'dsnoop', 'spdif', 'iec958', 'surround',
    )

    def _pick_hostapi(self, hostapis):
        """Index of the ONE host API to enumerate. Preference list first; else
        the API owning the default input device; else the sole/first API."""
        pref = self._HOSTAPI_PREFERENCE.get(sys.platform, [])
        for token in pref:
            for idx, api in enumerate(hostapis):
                if token in (api.get('name') or '').lower():
                    return idx
        # No preferred API present (e.g. Linux exposes only "ALSA") — use the
        # API that owns the system default input device.
        try:
            default_in = sd.default.device[0]
            if default_in is not None and default_in >= 0:
                return sd.query_devices(default_in)['hostapi']
        except Exception:
            pass
        return 0 if hostapis else None

    def _is_junk(self, name):
        low = name.lower()
        return not name or any(j in low for j in self._DEVICE_NAME_JUNK)

    def _enumerate_devices(self, hostapi_index):
        """Enumerate (inputs, outputs) restricted to hostapi_index (None = all
        APIs). Name-based safety net: first occurrence of a name wins. Entries:
        {id, name, channels, is_default, hostapi}."""
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        def default_idx(kind_pos):
            # Prefer the chosen host API's own default; fall back to the global.
            if hostapi_index is not None:
                try:
                    key = 'default_input_device' if kind_pos == 0 else 'default_output_device'
                    d = hostapis[hostapi_index].get(key, -1)
                    if d is not None and d >= 0:
                        return d
                except Exception:
                    pass
            try:
                d = sd.default.device[kind_pos]
                return d if d is not None and d >= 0 else None
            except Exception:
                return None

        default_in, default_out = default_idx(0), default_idx(1)

        input_devices, output_devices = [], []
        seen_input_names, seen_output_names = set(), set()
        for i, dev in enumerate(devices):
            if hostapi_index is not None and dev.get('hostapi') != hostapi_index:
                continue
            name = (dev.get('name') or '').strip()
            if self._is_junk(name):
                continue
            api_name = ''
            try:
                api_name = hostapis[dev['hostapi']]['name']
            except Exception:
                pass
            if dev.get('max_input_channels', 0) > 0 and name.lower() not in seen_input_names:
                seen_input_names.add(name.lower())
                input_devices.append({'id': i, 'name': name,
                                      'channels': dev['max_input_channels'],
                                      'is_default': (i == default_in),
                                      'hostapi': api_name})
            if dev.get('max_output_channels', 0) > 0 and name.lower() not in seen_output_names:
                seen_output_names.add(name.lower())
                output_devices.append({'id': i, 'name': name,
                                       'channels': dev['max_output_channels'],
                                       'is_default': (i == default_out),
                                       'hostapi': api_name})
        return input_devices, output_devices

    @staticmethod
    def _merge_truncated_names(devices):
        """Unfiltered-fallback helper: MME truncates names to 31 chars, so a name
        that is a >=31-char prefix of another device's name is the same physical
        device — keep the longer-named one."""
        full = sorted(devices, key=lambda d: -len(d['name']))
        kept = []
        for dev in full:
            if any(len(dev['name']) >= 31 and k['name'].startswith(dev['name'])
                   for k in kept):
                continue
            kept.append(dev)
        return kept

    def list_audio_devices(self):
        """List selectable audio devices from the ONE platform-appropriate host
        API (WASAPI on Windows), name-deduped, junk filtered, default marked.
        Falls back to unfiltered enumeration if the filtered list is empty.
        Returns (inputs, outputs)."""
        log.info("[VoiceHandler.list_audio_devices] Querying available audio devices...")
        input_devices, output_devices = [], []
        try:
            hostapis = sd.query_hostapis()
            api_idx = self._pick_hostapi(hostapis)
            input_devices, output_devices = self._enumerate_devices(api_idx)
            api_name = hostapis[api_idx]['name'] if api_idx is not None else 'ALL'
            if not input_devices:
                # Unusual setup — better a crowded list than an empty dropdown.
                log.warning(f"[VoiceHandler.list_audio_devices] No inputs under host API "
                            f"'{api_name}' — falling back to unfiltered enumeration")
                input_devices, output_devices = self._enumerate_devices(None)
                input_devices = self._merge_truncated_names(input_devices)
                output_devices = self._merge_truncated_names(output_devices)
        except Exception as e:
            log.error(f"[VoiceHandler.list_audio_devices] enumeration failed: "
                      f"{type(e).__name__}: {e}")
            try:
                input_devices, output_devices = self._enumerate_devices(None)
            except Exception as e2:
                log.error(f"[VoiceHandler.list_audio_devices] unfiltered fallback also failed: {e2}")

        input_devices.sort(key=lambda d: (not d['is_default'], d['name'].lower()))
        output_devices.sort(key=lambda d: (not d['is_default'], d['name'].lower()))

        log.info(f"[VoiceHandler.list_audio_devices] ✓ {len(input_devices)} input | "
                 f"{len(output_devices)} output")
        log.debug(f"[VoiceHandler.list_audio_devices] Input devices: "
                  f"{[d['name'] for d in input_devices]}")

        return input_devices, output_devices

    # ── Hotplug support ──────────────────────────────────────────────────────

    def hotplug_signature(self):
        """Cheap LIVE probe of the system device population, bypassing PortAudio's
        frozen snapshot (PortAudio only refreshes its device list on init). On
        Windows use winmm's live counts; elsewhere return None so callers fall
        back to a slow periodic rescan."""
        if sys.platform == 'win32':
            try:
                import ctypes
                winmm = getattr(ctypes, 'windll', None)
                if winmm is not None:
                    return (ctypes.windll.winmm.waveInGetNumDevs(),
                            ctypes.windll.winmm.waveOutGetNumDevs())
            except Exception:
                pass
        return None

    def rescan_devices(self):
        """Re-initialize PortAudio so newly connected/disconnected devices appear
        in query_devices(). MUST NOT run while any stream is open — callers stop
        meters first, and we refuse under active voice capture. True on success."""
        if self.is_listening:
            log.debug("[VoiceHandler.rescan_devices] Skipped — voice capture is active")
            return False
        try:
            sd._terminate()
            sd._initialize()
            log.info("[VoiceHandler.rescan_devices] ✓ PortAudio re-initialized (device list refreshed)")
            return True
        except Exception as e:
            log.warning(f"[VoiceHandler.rescan_devices] re-init failed: {type(e).__name__}: {e}")
            return False

    def _resolve_input_id(self, value):
        """Map a stored input-device value (name str, id int, or None) to a live
        PortAudio device id for opening a stream. None stays None (system
        default). An unresolved name is passed through (PortAudio accepts a name
        substring), else None."""
        if value is None:
            return None
        if isinstance(value, int):
            return value
        try:
            inputs, _ = self.list_audio_devices()
            for dev in inputs:
                if dev['name'] == value:
                    return dev['id']
        except Exception:
            pass
        return value  # PortAudio can match a name string directly

    def set_devices(self, input_device=None, output_device=None):
        """Set audio input/output devices. Accepts a device NAME (str), a live
        PortAudio id (int), or None (system default). Names are resolved to the
        current preferred-host-API id so the capture stream opens the same
        physical device the user picked, even though ids shift across sessions."""
        log.info(f"[VoiceHandler.set_devices] input_device={input_device!r} | "
                 f"output_device={output_device!r}")
        self.input_device = self._resolve_input_id(input_device)
        self.output_device = output_device  # output is system-default only now

        if input_device is not None:
            log.info(f"[VoiceHandler.set_devices] ✓ Input device set: {input_device!r} "
                     f"→ id {self.input_device!r}")
            self._emit_log_callback(f"Input device: {input_device}")
        else:
            log.debug("[VoiceHandler.set_devices] Input device: using system default")

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
        self.playback_active = False
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

        self._restore_playback('interrupt')

        new_state = 'listening' if self.is_listening else 'inactive'
        self._emit_log_callback("Speech interrupted")
        self._notify_state_change(new_state)
        log.info(f"[VoiceHandler.interrupt_speech] ✓ Speech interrupted | new_state='{new_state}'")

    # ── Auto barge-in: duck / restore ────────────────────────────────────────
    # Ducking is inherently a no-op for the pyttsx3 provider (no pygame stage —
    # interrupt_speech cannot stop it either; pre-existing limitation).

    def _apply_playback_volume(self):
        """Set the music volume for the current duck state. Called before every
        play(): pygame music volume persists across load(), so each chunk and
        filler must start at a deterministic level."""
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(
                    self._DUCK_VOLUME if self._ducked else 1.0)
        except Exception as e:
            log.warning(f"[VoiceHandler._apply_playback_volume] "
                        f"{type(e).__name__}: {e}")

    def _duck_playback(self):
        """Enter ducked state: playback continues at low volume while STT
        decides whether real words were spoken (processing thread)."""
        with self._duck_lock:
            if self._ducked:
                return
            self._ducked = True
            self._duck_started = time.monotonic()
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(self._DUCK_VOLUME)
        except Exception as e:
            log.warning(f"[VoiceHandler._duck_playback] {type(e).__name__}: {e}")
        self._emit_log_callback("Barge-in: ducked playback to 20%")
        log.info("[VoiceHandler._duck_playback] Ducked playback (candidate speech)")

    def _restore_playback(self, reason):
        """Leave ducked state and restore full volume. Safe from any thread and
        a no-op when not ducked."""
        with self._duck_lock:
            if not self._ducked:
                return
            self._ducked = False
        self._bargein_window.clear()
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.set_volume(1.0)
        except Exception as e:
            log.warning(f"[VoiceHandler._restore_playback] {type(e).__name__}: {e}")
        self._emit_log_callback(f"Barge-in: restored playback ({reason})")
        log.info(f"[VoiceHandler._restore_playback] Restored playback ({reason})")

    def _update_bargein_frame(self, audio_bytes, is_speech, segment_open):
        """Per-frame duck-then-confirm bookkeeping (processing thread only).

        Auto mode never hard-stops playback on VAD alone: a qualified speech
        candidate (mostly-speech window whose RMS clears the ambient noise
        floor) only DUCKS the volume; the stop is committed solely by the
        transcript path once STT hears real non-echo words
        (_bargein_on_segment_result). Noise, coughs and TTS echo at worst
        cause a brief volume dip that restores on timeout."""
        now = time.monotonic()
        try:
            samples = np.frombuffer(audio_bytes, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))
        except Exception:
            return

        # Ambient noise floor — learned only while idle (no TTS, no speech).
        if not self.playback_active and not is_speech:
            if self._noise_floor is None:
                self._noise_floor = max(rms, self._NOISE_FLOOR_MIN)
            else:
                a = self._NOISE_FLOOR_ALPHA
                self._noise_floor = max((1 - a) * self._noise_floor + a * rms,
                                        self._NOISE_FLOOR_MIN)

        if is_speech:
            self._last_speech_ts = now

        if self.interrupt_mode != 'auto':
            return

        if self._ducked:
            cfg = self._bargein_cfg
            if now - self._duck_started > cfg['hard_cap']:
                self._restore_playback('hard cap')
            elif (not segment_open
                    and now - self._duck_started > cfg['confirm_timeout']
                    and now - self._last_speech_ts > self._SPEECH_GRACE_S):
                self._restore_playback('confirm timeout')
            return

        if not self.playback_active:
            self._bargein_window.clear()
            return
        if self._noise_floor is None:
            return

        self._bargein_window.append((is_speech, rms))
        if len(self._bargein_window) < self._bargein_window.maxlen:
            return
        cfg = self._bargein_cfg
        speech_rms = [r for s, r in self._bargein_window if s]
        ratio = len(speech_rms) / len(self._bargein_window)
        if ratio < cfg['min_speech_ratio']:
            return
        threshold = self._noise_floor * (10 ** (cfg['margin_db'] / 20.0))
        mean_rms = sum(speech_rms) / len(speech_rms)
        if mean_rms < threshold:
            return
        self._emit_log_callback(
            f"Barge-in candidate: ratio={ratio:.2f} rms={mean_rms:.0f} "
            f"floor={self._noise_floor:.0f} margin_db={cfg['margin_db']}")
        log.info(f"[VoiceHandler._update_bargein_frame] Candidate open: "
                 f"ratio={ratio:.2f} rms={mean_rms:.0f} floor={self._noise_floor:.0f}")
        self._duck_playback()

    def _processing_loop(self):
        """Process audio queue and detect speech segments"""
        log.info("[VoiceHandler._processing_loop] ── Processing thread started ────────────")
        audio_buffer = []
        currently_speaking = False
        segment_during_playback = False
        frames_processed = 0

        while self.is_listening:
            try:
                audio_bytes, is_speech = self.audio_queue.get(timeout=0.1)
                frames_processed += 1
                # Duck-then-confirm barge-in bookkeeping — never stops playback
                # itself; a committed transcript (via on_transcription) is the
                # sole stopper in auto mode.
                self._update_bargein_frame(audio_bytes, is_speech, currently_speaking)

                if is_speech:
                    if not currently_speaking:
                        log.info("[VoiceHandler._processing_loop] ▶ Speech segment started")
                        self._emit_log_callback("Speech started")
                        currently_speaking = True
                        audio_buffer = []
                        segment_during_playback = self.playback_active or self._ducked

                    audio_buffer.append(audio_bytes)
                    segment_during_playback = (segment_during_playback
                                               or self.playback_active or self._ducked)
                    self.silence_frames = 0

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
                            self._process_audio_segment(
                                audio_buffer, during_playback=segment_during_playback)

                            audio_buffer = []
                            currently_speaking = False
                            segment_during_playback = False
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

    def _process_audio_segment(self, audio_buffer, during_playback=False):
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

                if not self._bargein_on_segment_result(text, during_playback):
                    return

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
                self._bargein_on_segment_result(None, during_playback)
            except self.sr.RequestError as e:
                log.error(f"[VoiceHandler._process_audio_segment] ✗ STT request error: "
                          f"{type(e).__name__}: {e}")
                self._emit_log_callback(f"Speech recognition error: {e}", "ERROR")
                self._bargein_on_segment_result(None, during_playback)

        except Exception as e:
            log.error(f"[VoiceHandler._process_audio_segment] ✗ STT error: {type(e).__name__}: {e}")
            self._emit_log_callback(f"STT error: {e}", "ERROR")
            self._bargein_on_segment_result(None, during_playback)

        finally:
            self.is_processing = False
            self._notify_state_change('listening')
            log.debug("[VoiceHandler._process_audio_segment] is_processing=False | state → 'listening'")

    def _bargein_on_segment_result(self, text, during_playback):
        """Commit-or-restore decision for a speech segment that overlapped TTS
        playback (the confirm half of duck-then-confirm).

        Returns True when the transcript should be dispatched to
        on_transcription — the controller's transcript path then stops playback
        (the SOLE stopper in auto mode; its stop_all resets the duck). Manual
        mode and idle-time segments pass through unchanged."""
        if self.interrupt_mode != 'auto' or not during_playback:
            return True
        if not text or not text.strip():
            self._restore_playback('no words')
            return False
        is_echo, overlap = self._is_probable_echo(text)
        if is_echo:
            self._restore_playback(f'echo (overlap={overlap:.2f})')
            return False
        self._emit_log_callback("Barge-in: commit — transcript accepted, "
                                "controller will stop playback")
        log.info(f"[VoiceHandler._bargein_on_segment_result] Commit: '{text}'")
        return True

    @staticmethod
    def _normalize_words(text):
        return set(re.sub(r'[^a-z0-9 ]', ' ', text.lower()).split())

    def _is_probable_echo(self, text):
        """(is_echo, overlap_ratio) — normalized word-set overlap between the
        transcript and recently played TTS chunk texts (plus fillers). Ducked
        TTS can still be transcribed by STT; without this gate the app would
        barge in on its own voice AND send it to the AI as a user message."""
        words = self._normalize_words(text)
        if not words:
            return True, 1.0
        tts_words = set()
        for t in list(self._recent_tts_texts) + list(self._FILLER_TEXTS):
            tts_words |= self._normalize_words(t)
        if not tts_words:
            return False, 0.0
        overlap = len(words & tts_words) / len(words)
        return overlap >= self._ECHO_OVERLAP_THRESHOLD, overlap

    def _flush_display_buffer(self):
        """Release any chat message buffered "until playback starts".

        INVARIANT: every way a TTS utterance can end WITHOUT playback ever
        starting (synthesis interrupted/discarded, provider error, no speakable
        text, unknown provider) must still fire this, or the buffered reply
        stays hidden and voice-mode gating stays stuck until voice is toggled
        off. Thread-safe (the hook just emits a Qt signal) and a no-op when
        nothing is waiting or playback already fired it."""
        if self.on_playback_started:
            try:
                self.on_playback_started()
            except Exception as e:
                log.warning(f"[VoiceHandler._flush_display_buffer] flush callback error: "
                            f"{type(e).__name__}: {e}")

    async def speak_text(self, text):
        """Convert text to speech and play it"""
        log.info(f"[VoiceHandler.speak_text] ── TTS requested | text_len={len(text)} ──────────")
        log.debug(f"[VoiceHandler.speak_text] Raw text preview: '{text[:80].replace(chr(10), '↵')}'")

        if not text or not text.strip():
            log.warning("[VoiceHandler.speak_text] Empty text — aborting TTS")
            self._flush_display_buffer()
            return

        filtered_text = self._filter_text_for_tts(text)
        log.debug(f"[VoiceHandler.speak_text] Filtered text_len={len(filtered_text)} | "
                  f"preview='{filtered_text[:80].replace(chr(10), '↵')}'")

        if not filtered_text or not filtered_text.strip():
            log.warning("[VoiceHandler.speak_text] No speakable text after filtering — aborting TTS")
            self._emit_log_callback("[TTS] No speakable text after filtering")
            self._flush_display_buffer()
            return

        # is_speaking covers the WHOLE TTS cycle (synthesis + playback) so it can
        # be interrupted at any point; the notified STATE distinguishes the silent
        # synthesis phase from actual audio playback ('speaking' is emitted by
        # each provider at pygame play() time, next to on_playback_started).
        self.is_speaking = True
        self._notify_state_change('synthesizing')
        log.debug("[VoiceHandler.speak_text] is_speaking=True | state → 'synthesizing'")

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
            self.playback_active = False
            # Safety net: if playback never started (interrupted mid-synthesis,
            # provider error, unknown provider), release the buffered chat
            # message now — no-op when playback already fired it.
            self._flush_display_buffer()
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

            if not self.is_speaking:
                log.debug("[VoiceHandler._speak_pyttsx3] Interrupted before playback — aborting")
                return
            thread = threading.Thread(target=speak_thread, daemon=True, name="pyttsx3Speak")
            thread.start()
            self.playback_active = True
            self._notify_state_change('speaking')
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
            if not self.is_speaking:
                # Interrupted while synthesizing — discard the audio, never play
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                return
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            self.playback_active = True
            self._notify_state_change('speaking')
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
            if not self.is_speaking:
                # Interrupted while synthesizing — discard the audio, never play
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                return
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            self.playback_active = True
            self._notify_state_change('speaking')
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

            if not self.is_speaking:
                # Interrupted while synthesizing — discard the audio, never play
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                return
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            self.playback_active = True
            self._notify_state_change('speaking')
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

            if not self.is_speaking:
                # Interrupted while synthesizing — discard the audio, never play
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
                return
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            self.playback_active = True
            self._notify_state_change('speaking')
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
        Filter text for TTS - remove code blocks, emojis, keep everything else.
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

        # Remove emojis (they get read aloud and sound silly)
        # Covers most emoji ranges including modifiers, flags, and ZWJ sequences
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # Emoticons
            "\U0001F300-\U0001F5FF"  # Misc symbols & pictographs
            "\U0001F680-\U0001F6FF"  # Transport & map symbols
            "\U0001F1E0-\U0001F1FF"  # Flags
            "\U0001F900-\U0001F9FF"  # Supplemental symbols
            "\U0001FA00-\U0001FA6F"  # Chess symbols
            "\U0001FA70-\U0001FAFF"  # Symbols extended-A
            "\U00002702-\U000027B0"  # Dingbats
            "\U000024C2-\U0001F251"  # Enclosed chars
            "\U0000FE00-\U0000FE0F"  # Variation selectors
            "\U0000200D"              # Zero-width joiner
            "\U00002600-\U000026FF"  # Misc symbols
            "]",
            flags=re.UNICODE
        )
        text = emoji_pattern.sub('', text)

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
        # Tidy the gaps the removed brackets leave WITHOUT touching newlines —
        # collapsing '\s+' here destroyed the message's paragraph structure.
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
        cleaned = re.sub(r'[ \t]+\n', '\n', cleaned)
        cleaned = cleaned.strip()

        if cleaned != text:
            removed_count = text.count('[') - cleaned.count('[')
            log.debug(f"[VoiceHandler._remove_emotion_brackets] ✓ Removed ~{removed_count} bracket(s) | "
                      f"original_len={len(text)} → cleaned_len={len(cleaned)}")
        else:
            log.debug("[VoiceHandler._remove_emotion_brackets] No brackets to remove")

        return cleaned

    # ── Serialized speech queue (public API: speak / stop_all / speech_busy) ──

    @staticmethod
    def _split_for_tts(text, max_len=350):
        """Split long text into sentence-group chunks of ~max_len chars each.

        A multi-kilobyte reply synthesized as ONE request means minutes of dead
        air and blows through provider timeouts (a Kokoro POST for an 11 KB
        story exceeded its 120 s client timeout). Chunks keep every synthesis
        short: audio starts after chunk 1 while the rest render behind it. A
        single overlong sentence falls back to comma/space splits; no chunk
        exceeds ~2*max_len."""
        text = (text or '').strip()
        if len(text) <= max_len:
            return [text] if text else []

        parts = re.split(r'(?<=[.!?…])\s+|\n{2,}', text)
        chunks = []
        cur = ''

        def flush_cur():
            nonlocal cur
            if cur.strip():
                chunks.append(cur.strip())
            cur = ''

        for part in parts:
            part = (part or '').strip()
            if not part:
                continue
            # Hard-split a single run-on sentence that dwarfs the budget.
            while len(part) > 2 * max_len:
                cut = part.rfind(', ', 0, 2 * max_len)
                if cut < max_len // 2:
                    cut = part.rfind(' ', 0, 2 * max_len)
                if cut < max_len // 2:
                    cut = 2 * max_len
                piece, part = part[:cut].strip(), part[cut:].lstrip(', ').strip()
                flush_cur()  # keep earlier buffered sentences ahead of the piece
                if piece:
                    chunks.append(piece)
            if cur and len(cur) + 1 + len(part) > max_len:
                flush_cur()
            cur = (cur + ' ' + part).strip() if cur else part
        flush_cur()
        return chunks

    def speak(self, text):
        """Enqueue text for TTS. Long text is split into sentence-group chunks,
        each a separate utterance — playback starts after the first chunk while
        the rest synthesize behind it. Utterances play strictly one at a time in
        FIFO order on a single persistent worker thread — never overlapping.
        Returns immediately (non-blocking)."""
        if not text or not text.strip():
            log.debug("[VoiceHandler.speak] Empty text — not enqueued")
            self._flush_display_buffer()  # never strand a buffered reply
            return
        # Filter BEFORE splitting so code blocks/markdown don't inflate chunks
        # and sentence boundaries reflect the actual speakable text (the
        # per-chunk filter inside speak_text is then an idempotent second pass).
        filtered = self._filter_text_for_tts(text)
        if not filtered or not filtered.strip():
            log.debug("[VoiceHandler.speak] No speakable text after filtering — not enqueued")
            self._flush_display_buffer()
            return
        chunks = self._split_for_tts(filtered)
        for chunk in chunks:
            self.speech_queue.put(chunk)
        log.info(f"[VoiceHandler.speak] Enqueued {len(chunks)} chunk(s) | "
                 f"text_len={len(filtered)} | queue_size≈{self.speech_queue.qsize()}")
        # Open the speech session: is_speaking spans the WHOLE pipeline run
        # (interrupt/gating key on it); the playback worker closes it.
        self.is_speaking = True
        if not self.playback_active:
            self._notify_state_change('synthesizing')
        self._ensure_speech_worker()

    def speak_pause(self, seconds=0.6):
        """Enqueue a short silent pause between utterances (FIFO-ordered like
        any chunk). No-op audio-wise if nothing is speaking when it's reached."""
        self.speech_queue.put((_PAUSE_TAG, float(seconds)))
        self._ensure_speech_worker()

    def _ensure_speech_worker(self):
        """Start the synth + playback worker threads if they aren't running."""
        with self._speech_worker_lock:
            if self._speech_worker is None or not self._speech_worker.is_alive():
                self._speech_worker = threading.Thread(
                    target=self._speech_worker_loop, daemon=True, name="VoiceSynth")
                self._speech_worker.start()
                log.debug(f"[VoiceHandler._ensure_speech_worker] Synth worker started | "
                          f"tid={self._speech_worker.ident}")
            if self._playback_worker is None or not self._playback_worker.is_alive():
                self._playback_worker = threading.Thread(
                    target=self._playback_worker_loop, daemon=True, name="VoicePlayback")
                self._playback_worker.start()
                log.debug(f"[VoiceHandler._ensure_speech_worker] Playback worker started | "
                          f"tid={self._playback_worker.ident}")

    # ── Synthesis (producer) ────────────────────────────────────────────────

    def _load_custom_tts_module(self):
        """Import (and cache) the custom TTS provider script."""
        path = self.tts_script_path
        if not path or not os.path.isfile(path):
            raise RuntimeError("custom TTS script not found — check settings")
        cached = self._custom_tts_module
        if cached and cached[0] == path:
            return cached[1]
        import importlib.util
        spec = importlib.util.spec_from_file_location("custom_tts_provider", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, 'speak') or not callable(module.speak):
            raise RuntimeError("custom TTS script must define speak(text, save_to) -> bool")
        self._custom_tts_module = (path, module)
        return module

    def _synthesize_chunk(self, text):
        """Render text to a temp audio file via the current provider.

        Pure synthesis — no pygame, no state changes. Returns the file path, or
        None on failure. (pyttsx3 has no file stage and never routes here.)"""
        temp = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
        path = temp.name
        temp.close()
        try:
            if self.tts_provider == 'edge-tts':
                communicate = edge_tts.Communicate(
                    text, self.tts_voice, rate=self.tts_rate, volume=self.tts_volume)
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(communicate.save(path))
                finally:
                    loop.close()
            elif self.tts_provider == 'custom_script':
                module = self._load_custom_tts_module()
                if not module.speak(text, path):
                    raise RuntimeError("speak() returned False")
            else:
                raise RuntimeError(f"provider '{self.tts_provider}' has no file synthesis")
            if os.path.getsize(path) < 200:
                raise RuntimeError("synthesis produced empty audio")
            return path
        except Exception as e:
            log.error(f"[VoiceHandler._synthesize_chunk] ✗ {type(e).__name__}: {e}")
            self._emit_log_callback(f"[TTS] Synthesis error: {e}", "ERROR")
            self._discard(path)
            return None

    def _speech_worker_loop(self):
        """SYNTH worker: render queued text chunks to audio files, feeding the
        bounded audio_ready buffer (prefetch). Exits when the text queue stays
        empty; a later speak() respawns it."""
        log.debug("[VoiceHandler._speech_worker_loop] ── Synth worker started ──")
        while True:
            try:
                text = self.speech_queue.get(timeout=1.0)
            except queue.Empty:
                break
            gen = self._speech_generation
            self._synth_in_flight = True
            try:
                if isinstance(text, tuple) and text and text[0] == _PAUSE_TAG:
                    # Pause marker — no synthesis; forward through the bounded
                    # buffer so it keeps its FIFO slot and staleness semantics.
                    while True:
                        try:
                            self.audio_ready.put((text, gen, ''), timeout=0.5)
                            break
                        except queue.Full:
                            if gen != self._speech_generation:
                                break
                    continue
                if self.tts_provider == 'pyttsx3':
                    # No file stage — legacy direct path, serial (no prefetch).
                    self.speak_text_sync(text)
                    continue
                if not self.playback_active:
                    self._notify_state_change('synthesizing')
                path = self._synthesize_chunk(text)
                if path is None:
                    continue
                if gen != self._speech_generation:
                    self._discard(path)
                    continue
                # Bounded put — blocks while playback is 2+ chunks behind;
                # re-check staleness so a stop_all during the wait discards.
                while True:
                    try:
                        self.audio_ready.put((path, gen, text), timeout=0.5)
                        break
                    except queue.Full:
                        if gen != self._speech_generation:
                            self._discard(path)
                            break
            except Exception as e:
                log.error(f"[VoiceHandler._speech_worker_loop] ✗ Chunk failed: "
                          f"{type(e).__name__}: {e}")
            finally:
                self._synth_in_flight = False
                self.speech_queue.task_done()
        log.debug("[VoiceHandler._speech_worker_loop] ── Synth worker idle — exiting ──")

    # ── Playback (consumer) ─────────────────────────────────────────────────

    def _synth_pending(self):
        """More audio is still on its way (text queued or synth in flight)."""
        return self._synth_in_flight or not self.speech_queue.empty()

    def _play_file(self, path, delete_after=True):
        """Play one rendered audio file; blocks until it ends or is interrupted.
        The shared playback half of every file-based provider."""
        started = False
        try:
            if not self.is_speaking:
                return False
            self._ensure_pygame_mixer()
            pygame.mixer.music.load(path)
            self._apply_playback_volume()
            pygame.mixer.music.play()
            started = True
            self.playback_active = True
            self._notify_state_change('speaking')
            if self.on_playback_started:
                try:
                    self.on_playback_started()
                except Exception as e:
                    log.warning(f"[VoiceHandler._play_file] on_playback_started error: "
                                f"{type(e).__name__}: {e}")
            while pygame.mixer.music.get_busy():
                if not self.is_speaking:
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.05)
            return True
        except Exception as e:
            log.error(f"[VoiceHandler._play_file] ✗ {type(e).__name__}: {e}")
            return False
        finally:
            self.playback_active = False
            if started:
                try:
                    pygame.mixer.music.unload()  # release the file handle (Windows)
                except Exception:
                    pass
            if delete_after:
                self._discard(path)

    def _playback_worker_loop(self):
        """PLAYBACK worker: play ready audio in order; when the buffer runs dry
        while more speech is still coming, bridge the gap with ONE same-voice
        filler interjection. Handles session teardown (state + display flush)."""
        log.debug("[VoiceHandler._playback_worker_loop] ── Playback worker started ──")
        filler_played_this_gap = False
        played_real_chunk = False
        while True:
            try:
                path, gen, text = self.audio_ready.get(timeout=0.3)
            except queue.Empty:
                if self._synth_pending() and self.is_speaking:
                    # Gap: next chunk isn't ready. Bridge it once, then wait.
                    # Only BETWEEN chunks — never as the session opener (the
                    # silence before the first chunk is normal synthesis lead-in,
                    # and a "Hmm..." before anything was said sounds absurd).
                    if (self.fillers_enabled and played_real_chunk
                            and not filler_played_this_gap):
                        filler = self._pick_filler()
                        if filler is not None:
                            log.info(f"[VoiceHandler._playback_worker_loop] Gap — playing "
                                     f"filler '{os.path.basename(filler)}'")
                            self._play_file(filler, delete_after=False)
                            filler_played_this_gap = True
                    continue
                if self._synth_pending():
                    continue  # not is_speaking (interrupted) — let synth drain
                break  # session over
            filler_played_this_gap = False
            try:
                if gen != self._speech_generation:
                    self._discard(path)
                    continue
                if isinstance(path, tuple) and path and path[0] == _PAUSE_TAG:
                    # Silent beat between utterances — interruptible, never a
                    # "real chunk" (fillers/session bookkeeping unaffected).
                    deadline = time.monotonic() + max(0.0, path[1])
                    while (time.monotonic() < deadline and self.is_speaking
                            and gen == self._speech_generation):
                        time.sleep(0.05)
                    continue
                self._recent_tts_texts.append(text)  # echo-gate reference
                if self._play_file(path):
                    played_real_chunk = True
            finally:
                self.audio_ready.task_done()
        # ── Session teardown (skipped if new work raced in after the break —
        # the next speak() call respawns a fresh worker for it) ───────────────
        if not self._synth_pending() and self.audio_ready.empty():
            self.is_speaking = False
            # A candidate that opened during the final chunk and never resolved
            # must not leave the NEXT session ducked.
            self._restore_playback('session ended')
            # Never strand a reply buffered "until playback starts" — if every
            # chunk failed or was interrupted pre-playback, release it now
            # (no-op normally).
            self._flush_display_buffer()
            self._notify_state_change('listening' if self.is_listening else 'inactive')
        log.debug("[VoiceHandler._playback_worker_loop] ── Playback worker idle — exiting ──")

    @staticmethod
    def _discard(path):
        try:
            # Pause markers travel the audio queue as tuples — nothing to unlink.
            if isinstance(path, str) and path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    # ── Filler interjections ────────────────────────────────────────────────

    # Short natural continuers, rendered once per provider+voice and reused.
    _FILLER_TEXTS = ["Hmm...", "So...", "And...", "Let me see...", "Mmm."]

    def _filler_cache_dir(self):
        from systema import APP_ROOT
        key = f"{self.tts_provider}|{self.tts_voice}|{self.tts_script_path}"
        h = hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]
        d = Path(APP_ROOT) / 'data' / 'voice_fillers' / h
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _pick_filler(self):
        """A random cached filler path (never the same one twice in a row), or
        None when the cache hasn't been rendered yet."""
        try:
            files = sorted(str(p) for p in self._filler_cache_dir().glob('filler_*.mp3'))
        except Exception:
            return None
        if not files:
            return None
        choices = [f for f in files if f != self._last_filler] or files
        choice = random.choice(choices)
        self._last_filler = choice
        return choice

    def ensure_fillers_async(self):
        """Render any missing filler clips in the background (same provider +
        voice as normal speech). Safe to call on every voice-mode enable; does
        nothing once the cache is warm. Skipped for pyttsx3 (no file stage)."""
        if self.tts_provider == 'pyttsx3':
            return

        def _work():
            try:
                cache = self._filler_cache_dir()
                for i, filler_text in enumerate(self._FILLER_TEXTS):
                    target = cache / f'filler_{i}.mp3'
                    if target.exists():
                        continue
                    path = self._synthesize_chunk(filler_text)
                    if path:
                        shutil.move(path, target)
                        log.info(f"[VoiceHandler.ensure_fillers_async] ✓ Rendered filler "
                                 f"'{filler_text}' → {target.name}")
            except Exception as e:
                log.warning(f"[VoiceHandler.ensure_fillers_async] {type(e).__name__}: {e}")

        threading.Thread(target=_work, daemon=True, name="VoiceFillers").start()

    # ── Stop / status ───────────────────────────────────────────────────────

    def stop_all(self):
        """Clear ALL pending speech — queued text, prefetched audio, and the
        current playback/synthesis — in one shot."""
        self._speech_generation += 1  # in-flight synth results become stale
        drained = 0
        while True:
            try:
                self.speech_queue.get_nowait()
                self.speech_queue.task_done()
                drained += 1
            except queue.Empty:
                break
        while True:
            try:
                path, _gen, _text = self.audio_ready.get_nowait()
                self.audio_ready.task_done()
                self._discard(path)
                drained += 1
            except queue.Empty:
                break
        log.info(f"[VoiceHandler.stop_all] Cleared {drained} pending item(s); "
                 f"interrupting current playback")
        self.interrupt_speech()
        self.is_speaking = False  # force even if interrupt_speech no-op'd
        self._restore_playback('stop_all')  # no-op if interrupt already restored
        # Release any chat message buffered "until playback starts" — if we
        # killed the pipeline during synthesis, on_playback_started never fired
        # and the reply would be silently lost from view.
        self._flush_display_buffer()

    @property
    def speech_busy(self):
        """True while any speech is pending: queued, synthesizing, prefetched,
        or playing."""
        return (self.is_speaking or self._synth_in_flight or self.playback_active
                or not self.speech_queue.empty() or not self.audio_ready.empty())

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

        log.debug("[VoiceHandler.cleanup] Stopping speech queue + any ongoing speech...")
        self.stop_all()

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