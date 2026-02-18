"""
Puter.js Server - Selenium-based WebSocket server with persistent browser profile
Complete feature set: Chat, Images, TTS (Standard + ElevenLabs), STT, Text-to-Image, Quota Reset, Account Setup
"""

from flask import Flask, render_template_string
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import threading
import time
import logging
import os
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from core.logger import _make_logger, _NoOpLogger

# Disable Flask's default logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


# ─────────────────────────── Colored Logger Setup ────────────────────────────
_verbose = True
plog = _make_logger("PuterServer") if _verbose else _NoOpLogger()
# ─────────────────────────────────────────────────────────────────────────────


class PuterServer:
    """Selenium-based Flask-SocketIO server for Puter.js AI with full feature support including ElevenLabs"""

    def __init__(self, port=8888, log_callback=None):
        plog.info(f"[PuterServer.__init__] ── Initializing PuterServer | port={port} | "
                  f"has_log_callback={log_callback is not None} ──")
        self.port = port
        self.log_callback = log_callback
        self.server_thread = None
        self.is_running = False
        self.app = None
        self.socketio = None

        # Selenium browser
        self.driver = None
        self.browser_ready = False

        # WebSocket state
        self.client_connected = False
        self.puter_ready = False

        # Request/response handling
        self.latest_response = None
        self._recent_image_host = None
        self.response_ready = threading.Event()

        # Profile path in working directory
        self.profile_path = os.path.join(os.getcwd(), "PuterAPIServerPROFILE")
        plog.info(f"[PuterServer.__init__] ✓ PuterServer initialized | port={port} | "
                  f"profile='{self.profile_path}'")

    def _get_html_template(self):
        """Return dark mode HTML template with full Puter.ai features including ElevenLabs"""
        html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Puter.js AI Server </title>
    <script src="https://js.puter.com/v2/"></script>
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            padding: 20px;
            min-height: 100vh;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: #2a2a2a;
            padding: 30px;
            border-radius: 10px;
            border: 1px solid #3a3a3a;
        }
        h1 {
            color: #ffffff;
            margin-bottom: 10px;
            font-size: 24px;
        }
        .subtitle {
            color: #888;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .status {
            padding: 12px;
            border-radius: 6px;
            margin: 15px 0;
            font-size: 14px;
        }
        .status.connected {
            background: #1a3a1a;
            color: #4ade80;
            border: 1px solid #2a4a2a;
        }
        .status.disconnected {
            background: #3a1a1a;
            color: #f87171;
            border: 1px solid #4a2a2a;
        }
        .badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            margin: 5px 5px 5px 0;
            background: #3a3a3a;
            color: #e0e0e0;
        }
        .badge.success {
            background: #1a3a1a;
            color: #4ade80;
        }
        .badge.warning {
            background: #3a2a1a;
            color: #fbbf24;
        }
        .info-box {
            background: #2a2a2a;
            border: 1px solid #3a3a3a;
            padding: 15px;
            border-radius: 6px;
            margin: 15px 0;
            font-size: 13px;
        }
        .info-box strong {
            color: #fff;
            display: block;
            margin-bottom: 8px;
        }
        #activityLog {
            background: #1a1a1a;
            border: 1px solid #3a3a3a;
            border-radius: 5px;
            padding: 10px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            margin: 10px 0;
        }
        .log-entry {
            margin: 3px 0;
            padding: 2px 5px;
        }
        .log-success { color: #4ade80; }
        .log-info { color: #60a5fa; }
        .log-warning { color: #fbbf24; }
        .log-error { color: #f87171; }
        .log-verbose { 
            color: #a78bfa; 
            font-size: 10px;
            margin-left: 10px;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .features-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 10px;
            margin: 15px 0;
        }
        .feature-card {
            background: #1a1a1a;
            padding: 12px;
            border-radius: 6px;
            border: 1px solid #3a3a3a;
            text-align: center;
        }
        .feature-card .icon {
            font-size: 24px;
            margin-bottom: 5px;
        }
        .feature-card .title {
            font-size: 12px;
            color: #888;
        }
        .feature-card.elevenlabs {
            border: 1px solid #4a4a2a;
            background: #2a2a1a;
        }
        .toggle-container {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 15px 0;
        }
        .toggle-switch {
            position: relative;
            width: 50px;
            height: 24px;
            background: #3a3a3a;
            border-radius: 12px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .toggle-switch.active {
            background: #4ade80;
        }
        .toggle-slider {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 20px;
            height: 20px;
            background: white;
            border-radius: 50%;
            transition: transform 0.3s;
        }
        .toggle-switch.active .toggle-slider {
            transform: translateX(26px);
        }
        .toggle-label {
            font-size: 14px;
            color: #e0e0e0;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 Puter.js AI Server</h1>
        <p class="subtitle">API provider</p>
        
        <div id="connectionStatus" class="status disconnected">
            <strong>⏳ Connecting...</strong><br>
            Establishing WebSocket connection
        </div>

        <div style="margin: 20px 0;">
            <span class="badge warning" id="puter-status">Loading Puter.js...</span>
            <span class="badge warning" id="websocket-status">WebSocket: Connecting...</span>
            <span class="badge">Port: [{used_port}]</span>
            <span class="badge">Profile: PuterAPIServerPROFILE</span>
        </div>

        <div class="toggle-container">
            <div class="toggle-switch" id="verboseToggle">
                <div class="toggle-slider"></div>
            </div>
            <span class="toggle-label">Verbose Mode (Show Raw Data & Errors)</span>
        </div>

        <div class="info-box">
            <strong>📊 Activity Log</strong>
            <div id="activityLog">
                <div class="log-entry log-info">Initializing...</div>
            </div>
        </div>

        <div class="info-box">
            <strong>ℹ️ Important Notes</strong>
            • First request may require authentication popup<br>
            • Image uploads limited to 512MB via 0x0.st
            <strong>Verbose mode shows all raw received data and detailed errors</strong>
        </div>
    </div>

    <script>
        let isPuterReady = false;
        let socket = null;
        let verboseMode = false;
        
        const connectionStatus = document.getElementById('connectionStatus');
        const puterStatus = document.getElementById('puter-status');
        const websocketStatus = document.getElementById('websocket-status');
        const activityLog = document.getElementById('activityLog');
        const verboseToggle = document.getElementById('verboseToggle');

        verboseToggle.addEventListener('click', () => {
            verboseMode = !verboseMode;
            verboseToggle.classList.toggle('active');
            addLog(`Verbose mode ${verboseMode ? 'ENABLED' : 'DISABLED'}`, 'info');
        });

        function addLog(message, type = 'info') {
            const entry = document.createElement('div');
            entry.className = `log-entry log-${type}`;
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            activityLog.appendChild(entry);
            activityLog.scrollTop = activityLog.scrollHeight;
            
            while (activityLog.children.length > 100) {
                activityLog.removeChild(activityLog.firstChild);
            }
        }

        function addVerboseLog(label, data) {
            if (!verboseMode) return;
            
            const entry = document.createElement('div');
            entry.className = 'log-entry log-verbose';
            entry.textContent = `[VERBOSE] ${label}:\n${JSON.stringify(data, null, 2)}`;
            activityLog.appendChild(entry);
            activityLog.scrollTop = activityLog.scrollHeight;
            
            while (activityLog.children.length > 100) {
                activityLog.removeChild(activityLog.firstChild);
            }
        }

        async function initializePuter() {
            try {
                addLog('Initializing Puter.js SDK...', 'info');
                
                if (typeof puter === 'undefined') {
                    addLog('ERROR: Puter.js SDK not loaded', 'error');
                    puterStatus.textContent = 'Puter.js Failed';
                    return;
                }

                puterStatus.textContent = 'Puter.js Ready ✓';
                puterStatus.className = 'badge success';
                isPuterReady = true;
                
                addLog('✓ Puter.js initialized successfully', 'success');
                addLog('✓ ElevenLabs TTS support enabled', 'success');
                
                if (socket && socket.connected) {
                    socket.emit('puter_ready', { ready: true });
                }
                
            } catch (error) {
                addLog(`Puter init error: ${error.message}`, 'error');
                addVerboseLog('Puter Init Error Details', {
                    error: error.message,
                    stack: error.stack
                });
                puterStatus.textContent = 'Init Error';
            }
        }

        function initializeWebSocket() {
            addLog('Connecting to WebSocket server...', 'info');
            
            socket = io('http://127.0.0.1:[{used_port}]', {
                transports: ['websocket', 'polling'],
                reconnection: true,
                reconnectionDelay: 1000,
                reconnectionAttempts: Infinity,
                maxHttpBufferSize: 50 * 1024 * 1024
            });

            socket.on('connect', () => {
                addLog('✓ WebSocket connected!', 'success');
                websocketStatus.textContent = 'WebSocket: Connected ✓';
                websocketStatus.className = 'badge success';
                
                connectionStatus.className = 'status connected';
                connectionStatus.innerHTML = '<strong>✓ Connected & Ready</strong><br>Server is operational with ElevenLabs support';
                
                if (isPuterReady) {
                    socket.emit('puter_ready', { ready: true });
                }
            });

            socket.on('disconnect', () => {
                addLog('⚠ WebSocket disconnected', 'warning');
                websocketStatus.textContent = 'WebSocket: Disconnected';
                websocketStatus.className = 'badge warning';
                
                connectionStatus.className = 'status disconnected';
                connectionStatus.innerHTML = '<strong>⚠ Disconnected</strong><br>Attempting to reconnect...';
            });

            socket.on('chat_request', async (data) => {
                addLog(`📨 Chat request (model: ${data.model})`, 'info');
                addVerboseLog('Chat Request Data', data);
                await handleChatRequest(data);
            });

            socket.on('text_to_image_request', async (data) => {
                addLog(`🎨 Text-to-Image request`, 'info');
                addVerboseLog('Text-to-Image Request Data', data);
                await handleTextToImage(data);
            });

            socket.on('text_to_speech_request', async (data) => {
                const provider = data.provider || 'standard';
                addLog(`🔊 Text-to-Speech request (${provider})`, 'info');
                addVerboseLog('Text-to-Speech Request Data', data);
                await handleTextToSpeech(data);
            });

            socket.on('speech_to_text_request', async (data) => {
                addLog(`🎤 Speech-to-Text request`, 'info');
                addVerboseLog('Speech-to-Text Request Data', data);
                await handleSpeechToText(data);
            });

            socket.on('ping', () => {
                socket.emit('pong');
            });
        }

        async function handleChatRequest(data) {
            try {
                addLog('Processing with Puter AI...', 'info');
                
                const { messages, model, image } = data;
                
                let response;
                const lastUserMessage = messages[messages.length - 1].content;
                
                if (image) {
                    addLog('Including image in request...', 'info');
                    response = await puter.ai.chat(lastUserMessage, image, { model });
                } else {
                    response = await puter.ai.chat(messages, { model });
                }

                let responseText = String(response);
                addLog(`✓ Got response (${responseText.length} chars)`, 'success');
                addVerboseLog('Chat Response', { response: responseText });

                socket.emit('chat_response', { 
                    response: responseText,
                    success: true 
                });

                addLog('✓ Response sent to Python app', 'success');

            } catch (error) {
                addLog(`Error: ${error.message}`, 'error');
                addVerboseLog('Chat Error Details', {
                    error: error.message,
                    stack: error.stack,
                    request_data: data
                });
                socket.emit('chat_response', { 
                    response: `Error: ${error.message}`,
                    success: false 
                });
            }
        }

        async function handleTextToImage(data) {
            try {
                const { prompt, model, width, height } = data;
                
                const options = { model: model || 'gpt-image-1' };
                if (width) options.width = width;
                if (height) options.height = height;
                
                addLog(`Generating image: ${prompt.substring(0, 50)}...`, 'info');
                
                const imageElement = await puter.ai.txt2img(prompt, options);
                
                let imageData = null;
                let isBase64 = false;
                
                if (imageElement && imageElement.src) {
                    imageData = imageElement.src;
                    if (imageData.startsWith('data:')) {
                        isBase64 = true;
                        const sizeKB = Math.round(imageData.length/1024);
                        addLog(`✓ Image generated as base64 (${sizeKB}KB)`, 'success');
                        addLog(`Sending ${sizeKB}KB image via WebSocket...`, 'info');
                    } else {
                        addLog(`✓ Image generated: ${imageData}`, 'success');
                    }
                } else if (typeof imageElement === 'string') {
                    imageData = imageElement;
                    isBase64 = imageData.startsWith('data:');
                    if (isBase64) {
                        const sizeKB = Math.round(imageData.length/1024);
                        addLog(`✓ Image as base64 string (${sizeKB}KB)`, 'success');
                    }
                }
                
                addVerboseLog('Text-to-Image Response', {
                    has_data: !!imageData,
                    is_base64: isBase64,
                    data_length: imageData ? imageData.length : 0
                });
                
                if (imageData) {
                    socket.emit('text_to_image_response', {
                        image_url: imageData,
                        is_base64: isBase64,
                        success: true
                    });
                    addLog('✓ Image data sent to Python', 'success');
                } else {
                    throw new Error('Failed to extract image data from response');
                }
                
            } catch (error) {
                const errorMsg = error.message || String(error);
                addLog(`Text-to-Image error: ${errorMsg}`, 'error');
                addVerboseLog('Text-to-Image Error Details', {
                    error: errorMsg,
                    stack: error.stack,
                    request_data: data
                });
                socket.emit('text_to_image_response', {
                    image_url: null,
                    error: errorMsg,
                    success: false
                });
            }
        }

        async function handleTextToSpeech(data) {
            try {
                const { text, model, voice, provider, output_format } = data;
                
                const useElevenLabs = provider === 'elevenlabs';
                
                if (useElevenLabs) {
                    addLog(`Generating speech with ElevenLabs...`, 'info');
                    
                    const options = { 
                        provider: 'elevenlabs',
                        model: model || 'eleven_multilingual_v2'
                    };
                    if (voice) options.voice = voice;
                    if (output_format) options.output_format = output_format;
                    
                    const audioElement = await puter.ai.txt2speech(text, options);
                    
                    let audioData = null;
                    let isBase64 = false;
                    
                    if (audioElement && audioElement.src) {
                        audioData = audioElement.src;
                        if (audioData.startsWith('data:')) {
                            isBase64 = true;
                            addLog(`✓ ElevenLabs speech as base64 (${Math.round(audioData.length/1024)}KB)`, 'success');
                        } else {
                            addLog(`✓ ElevenLabs speech URL: ${audioData}`, 'success');
                        }
                    } else if (typeof audioElement === 'string') {
                        audioData = audioElement;
                        isBase64 = audioData.startsWith('data:');
                    }
                    
                    addVerboseLog('ElevenLabs TTS Response', {
                        has_data: !!audioData,
                        is_base64: isBase64,
                        data_length: audioData ? audioData.length : 0
                    });
                    
                    if (audioData) {
                        socket.emit('text_to_speech_response', {
                            audio_url: audioData,
                            is_base64: isBase64,
                            provider: 'elevenlabs',
                            success: true
                        });
                        addLog('✓ ElevenLabs audio sent to Python', 'success');
                    } else {
                        throw new Error('Failed to extract audio data from ElevenLabs response');
                    }
                } else {
                    addLog(`Generating speech with standard TTS...`, 'info');
                    
                    const options = { model: model || 'tts-1' };
                    if (voice) options.voice = voice;
                    
                    const audioElement = await puter.ai.txt2speech(text, options);
                    
                    let audioData = null;
                    let isBase64 = false;
                    
                    if (audioElement && audioElement.src) {
                        audioData = audioElement.src;
                        if (audioData.startsWith('data:')) {
                            isBase64 = true;
                            addLog(`✓ Speech generated as base64 (${Math.round(audioData.length/1024)}KB)`, 'success');
                        } else {
                            addLog(`✓ Speech generated: ${audioData}`, 'success');
                        }
                    } else if (typeof audioElement === 'string') {
                        audioData = audioElement;
                        isBase64 = audioData.startsWith('data:');
                    }
                    
                    addVerboseLog('Standard TTS Response', {
                        has_data: !!audioData,
                        is_base64: isBase64,
                        data_length: audioData ? audioData.length : 0
                    });
                    
                    if (audioData) {
                        socket.emit('text_to_speech_response', {
                            audio_url: audioData,
                            is_base64: isBase64,
                            provider: 'standard',
                            success: true
                        });
                        addLog('✓ Audio sent to Python', 'success');
                    } else {
                        throw new Error('Failed to extract audio data from response');
                    }
                }
                
            } catch (error) {
                const errorMsg = error.message || String(error);
                addLog(`Text-to-Speech error: ${errorMsg}`, 'error');
                addVerboseLog('Text-to-Speech Error Details', {
                    error: errorMsg,
                    stack: error.stack,
                    request_data: data
                });
                socket.emit('text_to_speech_response', {
                    audio_url: null,
                    error: errorMsg,
                    success: false
                });
            }
        }

        async function handleSpeechToText(data) {
            try {
                const { audio_url, model } = data;
                
                const options = { model: model || 'faster-whisper-large-v3' };
                
                addLog('Transcribing audio...', 'info');
                
                const text = await puter.ai.speech2txt(audio_url, options);
                
                const transcription = String(text);
                addLog(`✓ Transcription complete (${transcription.length} chars)`, 'success');
                addVerboseLog('Speech-to-Text Response', { transcription });
                
                socket.emit('speech_to_text_response', {
                    text: transcription,
                    success: true
                });
                
            } catch (error) {
                const errorMsg = error.message || String(error);
                addLog(`Speech-to-Text error: ${errorMsg}`, 'error');
                addVerboseLog('Speech-to-Text Error Details', {
                    error: errorMsg,
                    stack: error.stack,
                    request_data: data
                });
                socket.emit('speech_to_text_response', {
                    text: null,
                    error: errorMsg,
                    success: false
                });
            }
        }

        window.addEventListener('load', async () => {
            addLog('Page loaded, starting initialization...', 'info');
            initializeWebSocket();
            await initializePuter();
            addLog('✓ All systems ready (including ElevenLabs)!', 'success');
        });

        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                addLog('Window minimized - staying active', 'info');
            } else {
                addLog('Window visible again', 'info');
            }
        });

        setInterval(() => {
            if (socket && socket.connected) {
                socket.emit('heartbeat');
            }
        }, 30000);
    </script>
</body>
</html>'''
        html = html.replace("[{used_port}]", str(self.port))
        return html

    def create_app(self):
        """Create and configure Flask app with SocketIO"""
        app = Flask(__name__)
        app.config['SECRET_KEY'] = 'puter-bridge-secret'
        CORS(app)
        app.logger.disabled = True

        socketio = SocketIO(
            app,
            cors_allowed_origins="*",
            async_mode='threading',
            logger=False,
            engineio_logger=False,
            ping_timeout=180,
            ping_interval=25,
            max_http_buffer_size=50 * 1024 * 1024
        )

        @app.route('/')
        def index():
            return render_template_string(self._get_html_template())

        @app.route('/health')
        def health():
            return {
                'status': 'running',
                'service': 'puter-selenium-elevenlabs',
                'puter_ready': self.puter_ready,
                'client_connected': self.client_connected,
                'browser_ready': self.browser_ready,
                'elevenlabs_enabled': True
            }

        @socketio.on('connect')
        def handle_connect():
            self.client_connected = True
            plog.info("[PuterServer] ✓ WebSocket client connected")

        @socketio.on('disconnect')
        def handle_disconnect():
            self.client_connected = False
            plog.info("[PuterServer] ⚠ WebSocket client disconnected")

        @socketio.on('puter_ready')
        def handle_puter_ready(data):
            self.puter_ready = True
            plog.info("[PuterServer] ✓ Puter.js is ready with ElevenLabs support")

        @socketio.on('chat_response')
        def handle_chat_response(data):
            self.latest_response = data
            self.response_ready.set()

        @socketio.on('text_to_image_response')
        def handle_tti_response(data):
            self.latest_response = data
            self.response_ready.set()

        @socketio.on('text_to_speech_response')
        def handle_tts_response(data):
            self.latest_response = data
            self.response_ready.set()

        @socketio.on('speech_to_text_response')
        def handle_stt_response(data):
            self.latest_response = data
            self.response_ready.set()

        @socketio.on('heartbeat')
        def handle_heartbeat():
            pass

        @socketio.on('pong')
        def handle_pong():
            pass

        return app, socketio

    def _start_browser(self):
        """Start Selenium browser with persistent profile"""
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        try:
            plog.info("[PuterServer] Starting Selenium browser...")

            os.makedirs(self.profile_path, exist_ok=True)

            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument(f"user-data-dir={self.profile_path}")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_experimental_option("detach", True)

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(options=chrome_options, service=service)
            plog.info(f"[PuterServer] ✓ Browser started with profile: {self.profile_path}")

            self.driver.get(f"http://127.0.0.1:{self.port}")
            plog.info(f"[PuterServer] ✓ Navigated to http://127.0.0.1:{self.port}")

            self.browser_ready = True
            return True

        except Exception as e:
            plog.error(f"[PuterServer.start] Failed to start browser: {e}")
            return False

    def start(self):
        """Start the server and browser"""
        plog.info(f"[PuterServer.start] ── Starting PuterServer | port={self.port} ──")
        if self.is_running:
            plog.warning(f"[PuterServer.start] Server already running, skipping start")
            return True

        try:
            def run_server():
                try:
                    self.app, self.socketio = self.create_app()
                    plog.info(f"[PuterServer] Starting WebSocket server on port {self.port}")
                    plog.info(f"[PuterServer.start] WebSocket server binding to 127.0.0.1:{self.port}")
                    self.socketio.run(
                        self.app,
                        host='127.0.0.1',
                        port=self.port,
                        debug=False,
                        use_reloader=False,
                        allow_unsafe_werkzeug=True
                    )
                except Exception as e:
                    plog.error(f"[PuterServer] Server error: {e}")
                    self.is_running = False

            self.server_thread = threading.Thread(target=run_server, daemon=True)
            self.server_thread.start()
            self.is_running = True
            plog.debug(f"[PuterServer.start] Server thread started (daemon=True)")

            time.sleep(2)
            plog.info(f"[PuterServer] ✓ Server started at http://127.0.0.1:{self.port}")
            plog.info(f"[PuterServer.start] ✓ Server running at http://127.0.0.1:{self.port}")

            if not self._start_browser():
                plog.warning("[PuterServer] ⚠ Browser failed to start, but server is running")
                plog.warning(f"[PuterServer.start] ⚠ Browser launch failed | server still running")
                return False
            plog.info(f"[PuterServer.start] ✓ Browser launched successfully")

            print("\n" + "="*60)
            print("⚠️  IMPORTANT NOTICE")
            print("="*60)
            print("If this is a NEW account or you recently RESET quota:")
            print("• First API request may require authentication popup")
            print("• Check browser window for any authentication prompts")
            print("• Wait for authentication to complete before making requests")
            print("="*60 + "\n")

            return True

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Failed to start: {e}")
            plog.error(f"[PuterServer.start] ✗ Failed to start server | error={e}")
            self.is_running = False
            return False

    def _save_base64_to_file(self, base64_data, output_path):
        """Save base64 data URL to file"""
        try:
            import base64
            import re

            match = re.match(r'data:([^;]+);base64,(.+)', base64_data)
            if not match:
                plog.error("[PuterServer] ✗ Invalid base64 data URL format")
                return False

            mime_type = match.group(1)
            b64_data = match.group(2)

            binary_data = base64.b64decode(b64_data)

            with open(output_path, 'wb') as f:
                f.write(binary_data)

            file_size_kb = len(binary_data) / 1024
            plog.info(f"[PuterServer] ✓ Saved to {output_path} ({file_size_kb:.1f}KB)")
            return True

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Failed to save base64 data: {e}")
            return False

    def upload_image_to_host(self, image_path, max_size_mb=512):
        """Upload image to first available host, trying providers in order.

        Caches the last working provider in self._recent_image_host and tries it
        first on subsequent calls for speed, falling back to the full chain on failure.
        """
        plog.info(f"[PuterServer.upload_image_to_host] ── Uploading image | path={image_path} | max_size_mb={max_size_mb} ──")
        try:
            if not os.path.exists(image_path):
                plog.error(f"[PuterServer] ✗ Image file not found: {image_path}")
                plog.error(f"[PuterServer.upload_image_to_host] ✗ File not found | path={image_path}")
                return None

            file_size_mb = os.path.getsize(image_path) / (1024 * 1024)
            plog.debug(f"[PuterServer.upload_image_to_host] File size | size_mb={file_size_mb:.2f}")
            if file_size_mb > max_size_mb:
                plog.error(f"[PuterServer] ✗ Image too large: {file_size_mb:.2f}MB (max: {max_size_mb}MB)")
                plog.error(f"[PuterServer.upload_image_to_host] ✗ File too large | size_mb={file_size_mb:.2f} | max_mb={max_size_mb}")
                return None

            providers = [
                {
                    "name": "0x0.st",
                    "url": "https://0x0.st",
                    "max_mb": 512,
                    "field": "file",
                    "data": {},
                    "headers": {"User-Agent": "curl/8.0.0", "Accept": "*/*"},
                    "parse": lambda r: r.text.strip(),
                },
                {
                    "name": "Catbox",
                    "url": "https://catbox.moe/user/api.php",
                    "max_mb": 200,
                    "field": "fileToUpload",
                    "data": {"reqtype": "fileupload", "userhash": ""},
                    "headers": {},
                    "parse": lambda r: r.text.strip(),
                },
                {
                    "name": "Litterbox",
                    "url": "https://litterbox.catbox.moe/resources/internals/api.php",
                    "max_mb": 1024,
                    "field": "fileToUpload",
                    "data": {"reqtype": "fileupload", "time": "72h"},
                    "headers": {},
                    "parse": lambda r: r.text.strip(),
                },
                {
                    "name": "uguu.se",
                    "url": "https://uguu.se/upload",
                    "max_mb": 100,
                    "field": "files[]",
                    "data": {},
                    "headers": {},
                    "parse": lambda r: r.json()["files"][0]["url"],
                },
                {
                    "name": "Pixeldrain",
                    "url": "https://pixeldrain.com/api/file",
                    "max_mb": 5120,
                    "field": "file",
                    "data": {"anonymous": "true"},
                    "headers": {},
                    "parse": lambda r: "https://pixeldrain.com/u/" + r.json()["id"],
                },
                {
                    # GoFile requires fetching a server list first, so we use a
                    # custom_fn that handles its own retry/server-fallback logic.
                    "name": "GoFile",
                    "max_mb": float("inf"),
                    "custom_fn": "_upload_gofile",
                },
            ]

            def try_provider(p):
                """Attempt an upload against a single provider. Returns URL or None."""
                if file_size_mb > p["max_mb"]:
                    plog.debug(f"[PuterServer] ⚠ [{p['name']}] Skipping — file too large ({file_size_mb:.2f}MB, max {p['max_mb']}MB)")
                    plog.debug(f"[PuterServer.upload_image_to_host] Skipping {p['name']} | too large {file_size_mb:.2f}MB > {p['max_mb']}MB")
                    return None

                # GoFile has a dedicated handler
                if "custom_fn" in p:
                    return getattr(self, p["custom_fn"])(image_path, file_size_mb)

                plog.info(f"[PuterServer] Uploading ({file_size_mb:.2f}MB) to {p['name']}...")
                plog.debug(f"[PuterServer.upload_image_to_host] Trying provider={p['name']} | size_mb={file_size_mb:.2f}")
                for attempt in range(3):
                    try:
                        with open(image_path, "rb") as f:
                            response = requests.post(
                                p["url"],
                                files={p["field"]: f},
                                data=p["data"],
                                headers=p["headers"],
                                timeout=120
                            )
                        if response.status_code == 200:
                            url = p["parse"](response)
                            plog.info(f"[PuterServer] ✓ [{p['name']}] Uploaded: {url}")
                            plog.info(f"[PuterServer.upload_image_to_host] ✓ Upload success | provider={p['name']} | url={url}")
                            time.sleep(1)
                            return url
                        plog.warning(f"[PuterServer] ⚠ [{p['name']}] Attempt {attempt + 1} failed: HTTP {response.status_code}")
                        plog.warning(f"[PuterServer.upload_image_to_host] Provider {p['name']} attempt {attempt+1} | status={response.status_code}")
                    except Exception as e:
                        plog.warning(f"[PuterServer] ⚠ [{p['name']}] Attempt {attempt + 1} error: {e}")
                        plog.warning(f"[PuterServer.upload_image_to_host] Provider {p['name']} attempt {attempt+1} error | error={e}")
                    if attempt < 2:
                        time.sleep(2)

                plog.warning(f"[PuterServer] ✗ [{p['name']}] Failed, trying next provider...")
                plog.debug(f"[PuterServer.upload_image_to_host] Provider {p['name']} exhausted all retries")
                return None

            # --- Fast path: try the last known-good provider first ---
            if self._recent_image_host:
                recent = next((p for p in providers if p["name"] == self._recent_image_host), None)
                if recent:
                    plog.info(f"[PuterServer] [Fast path] Trying last successful provider: {self._recent_image_host}")
                    plog.debug(f"[PuterServer.upload_image_to_host] Fast path | trying cached provider={self._recent_image_host}")
                    url = try_provider(recent)
                    if url:
                        return url
                    plog.warning(f"[PuterServer] [Fast path] {self._recent_image_host} failed, falling back to full chain...")
                    plog.warning(f"[PuterServer.upload_image_to_host] Fast path failed | falling back to full chain")
                    self._recent_image_host = None

            # --- Full chain ---
            plog.debug(f"[PuterServer.upload_image_to_host] Running full provider chain | count={len(providers)}")
            for p in providers:
                url = try_provider(p)
                if url:
                    self._recent_image_host = p["name"]
                    return url

            plog.error("[PuterServer] ✗ All upload providers failed")
            plog.error(f"[PuterServer.upload_image_to_host] ✗ All providers failed")
            return None

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Upload error: {e}")
            plog.error(f"[PuterServer.upload_image_to_host] ✗ Exception | error={e}")
            return None

    def _upload_gofile(self, image_path, file_size_mb):
        """GoFile upload with full server-list fallback.

        Fetches all available servers from the API and tries each one in order
        before giving up, so a single downed server won't block the upload.
        """
        plog.info(f"[PuterServer] Uploading ({file_size_mb:.2f}MB) to GoFile...")
        try:
            resp = requests.get("https://api.gofile.io/servers", timeout=15)
            servers = resp.json()["data"]["servers"]  # [{"name": "store1", ...}, ...]
            server_names = [s["name"] for s in servers]
            plog.info(f"[PuterServer] [GoFile] Available servers: {server_names}")
        except Exception as e:
            plog.error(f"[PuterServer] ✗ [GoFile] Could not fetch server list: {e}")
            return None

        for server in server_names:
            upload_url = f"https://{server}.gofile.io/contents/uploadfile"
            plog.info(f"[PuterServer] [GoFile] Trying server: {server}")
            for attempt in range(3):
                try:
                    with open(image_path, "rb") as f:
                        response = requests.post(upload_url, files={"file": f}, timeout=120)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") == "ok":
                            url = data["data"]["downloadPage"]
                            plog.info(f"[PuterServer] ✓ [GoFile/{server}] Uploaded: {url}")
                            time.sleep(1)
                            return url
                        plog.warning(f"[PuterServer] ⚠ [GoFile/{server}] Bad response: {data.get('status')}")
                    else:
                        plog.warning(f"[PuterServer] ⚠ [GoFile/{server}] Attempt {attempt + 1} failed: HTTP {response.status_code}")
                except Exception as e:
                    plog.warning(f"[PuterServer] ⚠ [GoFile/{server}] Attempt {attempt + 1} error: {e}")
                if attempt < 2:
                    time.sleep(2)

            plog.warning(f"[PuterServer] ✗ [GoFile/{server}] All attempts failed, trying next server...")

        plog.error("[PuterServer] ✗ [GoFile] All servers exhausted")
        return None

    def send_chat_request(self, messages, model='gpt-5-chat-latest',
                         temperature=0.7, max_tokens=2000, image=None, timeout=60, is_test=False):
        """Send a chat request with full message history"""
        plog.info(f"[PuterServer.send_chat_request] ── Chat request | model={model} | messages={len(messages)} | max_tokens={max_tokens} | timeout={timeout}s ──")
        try:
            if not self.client_connected or not self.puter_ready:
                plog.warning(f"[PuterServer] ✗ Client not ready")
                plog.warning(f"[PuterServer.send_chat_request] ✗ Client not ready | connected={self.client_connected} | puter_ready={self.puter_ready}")
                return None

            image_url = None
            if image:
                if image.startswith('http://') or image.startswith('https://'):
                    image_url = image
                    plog.info(f"[PuterServer] Using image URL: {image_url}")
                    plog.debug(f"[PuterServer.send_chat_request] Image is URL, using directly | url={image_url}")
                else:
                    plog.info(f"[PuterServer] Uploading local image: {image}")
                    plog.debug(f"[PuterServer.send_chat_request] Uploading local image | path={image}")
                    image_url = self.upload_image_to_host(image)
                    if not image_url:
                        plog.error("[PuterServer] ✗ Image upload failed")
                        plog.error(f"[PuterServer.send_chat_request] ✗ Image upload failed | path={image}")
                        return None
                    plog.info(f"[PuterServer.send_chat_request] ✓ Image uploaded | url={image_url}")

            plog.info(f"[PuterServer] Sending chat request (model: {model})")
            if image_url:
                plog.info(f"[PuterServer]   → With image: {image_url}")
            plog.info(f"[PuterServer]   → Messages: {len(messages)} in history")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                plog.debug(f"[PuterServer.send_chat_request] Emitting 'chat_request' via SocketIO")
                self.socketio.emit('chat_request', {
                    'messages': messages,
                    'model': model,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                    'image': image_url
                })

                if is_test:
                    plog.info("[PuterServer] Test Message is Sent...")
                    plog.debug(f"[PuterServer.send_chat_request] Test mode, returning after emit")
                    return None

                plog.info(f"[PuterServer] Waiting for response (timeout: {timeout}s)...")
                plog.debug(f"[PuterServer.send_chat_request] Waiting for response_ready event | timeout={timeout}s")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        result = response.get('response')
                        plog.info(f"[PuterServer] ✓ Received response ({len(result)} chars)")
                        plog.info(f"[PuterServer.send_chat_request] ✓ Response received | chars={len(result)}")
                        return result
                    else:
                        error_msg = response.get('response', 'Unknown error')
                        plog.error(f"[PuterServer] ✗ Request failed: {error_msg}")
                        plog.error(f"[PuterServer.send_chat_request] ✗ Request failed | error={error_msg}")
                        return None
                else:
                    plog.error("[PuterServer] ✗ Timeout waiting for response")
                    plog.error(f"[PuterServer.send_chat_request] ✗ Timeout after {timeout}s waiting for response")
                    return None
            else:
                plog.error("[PuterServer] ✗ SocketIO not initialized")
                plog.error(f"[PuterServer.send_chat_request] ✗ SocketIO not initialized")
                return None

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Error: {e}")
            plog.error(f"[PuterServer.send_chat_request] ✗ Exception | error={e}")
            return None

    def text_to_image(self, prompt, model='gpt-image-1', width=None, height=None,
                     save_to=None, timeout=180):
        """Generate image from text"""
        plog.info(f"[PuterServer.text_to_image] ── TTI request | model={model} | size={width}x{height} | prompt='{prompt[:50]}...' ──")
        try:
            if not self.client_connected or not self.puter_ready:
                plog.warning("[PuterServer] ✗ Client not ready")
                plog.warning(f"[PuterServer.text_to_image] ✗ Client not ready | connected={self.client_connected} | puter_ready={self.puter_ready}")
                return None

            plog.info(f"[PuterServer] Text-to-Image request: {prompt[:50]}...")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                plog.debug(f"[PuterServer.text_to_image] Emitting 'text_to_image_request'")
                self.socketio.emit('text_to_image_request', {
                    'prompt': prompt,
                    'model': model,
                    'width': width,
                    'height': height
                })

                plog.info(f"[PuterServer] Waiting for image generation (timeout: {timeout}s)...")
                plog.debug(f"[PuterServer.text_to_image] Waiting for response | timeout={timeout}s")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        image_data = response.get('image_url')
                        is_base64 = response.get('is_base64', False)

                        if is_base64:
                            size_kb = len(image_data) / 1024
                            plog.info(f"[PuterServer] ✓ Received base64 image ({size_kb:.1f}KB)")
                            plog.info(f"[PuterServer.text_to_image] ✓ Base64 image received | size_kb={size_kb:.1f}")
                            if save_to:
                                plog.debug(f"[PuterServer.text_to_image] Saving to file | path={save_to}")
                                self._save_base64_to_file(image_data, save_to)
                            return image_data
                        else:
                            plog.info(f"[PuterServer] ✓ Image URL: {image_data}")
                            plog.info(f"[PuterServer.text_to_image] ✓ Image URL received | url={image_data}")
                            return image_data
                    else:
                        error = response.get('error', 'Unknown error')
                        plog.error(f"[PuterServer] ✗ Generation failed: {error}")
                        plog.error(f"[PuterServer.text_to_image] ✗ Generation failed | error={error}")
                        return None
                else:
                    plog.error("[PuterServer] ✗ Timeout waiting for image generation")
                    plog.error(f"[PuterServer.text_to_image] ✗ Timeout after {timeout}s")
                    return None

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Error: {e}")
            plog.error(f"[PuterServer.text_to_image] ✗ Exception | error={e}")
            return None

    def text_to_speech(self, text, model='tts-1', voice=None, save_to=None,
                      timeout=90, provider='standard', output_format=None):
        """Convert text to speech with support for both standard TTS and ElevenLabs

        Args:
            text: Text to convert
            model: TTS model - Standard models or ElevenLabs models
            voice: Voice ID (optional)
            save_to: File path to save audio (optional, for base64 data)
            timeout: Response timeout in seconds
            provider: 'standard' or 'elevenlabs' (default: 'standard')
            output_format: Output audio format (ElevenLabs only)

        Standard TTS Models:
            - tts-1: Standard quality
            - tts-1-hd: High definition
            - gpt-4o-mini-tts: GPT-4o mini TTS

        ElevenLabs Models (provider='elevenlabs'):
            - eleven_v3: Latest model with 70+ languages, highest emotional range (research preview)
            - eleven_multilingual_v2: High-quality multilingual (29 languages) - DEFAULT
            - eleven_flash_v2_5: Ultra-fast (~75ms latency, 32 languages)
            - eleven_turbo_v2_5: Balanced quality & speed (32 languages)
            - eleven_turbo_v2: English-only turbo (deprecated, use turbo_v2_5)
            - eleven_flash_v2: Previous flash model
            - eleven_multilingual_sts_v2: For speech-to-speech conversion

        ElevenLabs Popular Voices (use with provider='elevenlabs'):
            Sample/Default Voices (always available):
            - 21m00Tcm4TlvDq8ikWAM: Rachel (female, neutral)
            - wViXBPUzp2ZZixB1xQuM: Ryan (male)
            - yoZ06aMxZJJ28mfd3POQ: Sam (male)
            - pMsXgVXv3BLzUgSXRplE: Serena (female)
            - GBv7mTt0atIp3Br8iCZE: Thomas (male)

            Named Voices (from voice library):
            - Aria, Roger, Sarah, Laura, Charlie, George, Callum, River
            - Liam, Charlotte, Alice, Matilda, Will, Jessica, Eric, Chris
            - Brian, Daniel, Lily, Bill, Bella

            Note: For custom/cloned voices, get voice ID from ElevenLabs dashboard
            or use the List Voices API endpoint

        ElevenLabs Output Formats:
            - mp3_44100_128: Standard MP3 (44.1kHz, 128kbps) - DEFAULT
            - mp3_44100_192: High quality MP3 (44.1kHz, 192kbps)
            - pcm_16000: Raw PCM audio (16kHz)
            - pcm_22050: PCM at 22.05kHz
            - pcm_24000: PCM at 24kHz
            - ulaw_8000: Compressed 8-bit audio

        Language Support:
            ElevenLabs multilingual models support 32+ languages including:
            English (USA, UK, Australia, Canada), Japanese, Chinese, German, Hindi,
            French (France, Canada), Korean, Portuguese (Brazil, Portugal), Italian,
            Spanish (Spain, Mexico), Indonesian, Dutch, Turkish, Filipino, Polish,
            Swedish, Bulgarian, Romanian, Arabic, Czech, Greek, Finnish, Croatian,
            Malay, Slovak, Danish, Tamil, Ukrainian, Russian, Vietnamese, Hungarian, Norwegian

        Returns:
            String (URL or base64 data URL) or None on failure

        Example Usage:
            # Standard TTS
            audio = server.text_to_speech("Hello world", model="tts-1")

            # ElevenLabs with default settings
            audio = server.text_to_speech(
                "Hello world",
                provider="elevenlabs"
            )

            # ElevenLabs with specific model and voice
            audio = server.text_to_speech(
                "Welcome to ElevenLabs",
                model="eleven_turbo_v2_5",
                voice="21m00Tcm4TlvDq8ikWAM",  # Rachel
                provider="elevenlabs",
                output_format="mp3_44100_192",
                save_to="output.mp3"
            )

            # Ultra-fast ElevenLabs for real-time apps
            audio = server.text_to_speech(
                "Quick response",
                model="eleven_flash_v2_5",
                voice="21m00Tcm4TlvDq8ikWAM",
                provider="elevenlabs"
            )
        """
        try:
            if not self.client_connected or not self.puter_ready:
                plog.warning("[PuterServer] ✗ Client not ready")
                plog.warning(f"[PuterServer.text_to_speech] ✗ Client not ready | connected={self.client_connected} | puter_ready={self.puter_ready}")
                return None

            plog.info(f"[PuterServer.text_to_speech] ── TTS request | provider={provider} | model={model} | voice={voice} | text='{text[:50]}...' ──")
            plog.info(f"[PuterServer] Text-to-Speech request ({provider}): {text[:50]}...")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                request_data = {
                    'text': text,
                    'model': model,
                    'voice': voice,
                    'provider': provider
                }

                if provider == 'elevenlabs' and output_format:
                    request_data['output_format'] = output_format

                plog.debug(f"[PuterServer.text_to_speech] Emitting 'text_to_speech_request'")
                self.socketio.emit('text_to_speech_request', request_data)

                plog.info(f"[PuterServer] Waiting for audio generation (timeout: {timeout}s)...")
                plog.debug(f"[PuterServer.text_to_speech] Waiting for audio response | timeout={timeout}s")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        audio_data = response.get('audio_url')
                        is_base64 = response.get('is_base64', False)
                        response_provider = response.get('provider', 'standard')

                        if is_base64:
                            plog.info(f"[PuterServer] ✓ Received base64 audio data ({response_provider})")
                            plog.info(f"[PuterServer.text_to_speech] ✓ Base64 audio received | provider={response_provider}")
                            if save_to:
                                plog.debug(f"[PuterServer.text_to_speech] Saving audio to | path={save_to}")
                                self._save_base64_to_file(audio_data, save_to)
                            return audio_data
                        else:
                            plog.info(f"[PuterServer] ✓ Audio URL ({response_provider}): {audio_data}")
                            plog.info(f"[PuterServer.text_to_speech] ✓ Audio URL received | provider={response_provider} | url={audio_data}")
                            return audio_data
                    else:
                        error = response.get('error', 'Unknown error')
                        plog.error(f"[PuterServer] ✗ Generation failed: {error}")
                        plog.error(f"[PuterServer.text_to_speech] ✗ Generation failed | error={error}")
                        return None
                else:
                    plog.error("[PuterServer] ✗ Timeout waiting for audio generation")
                    plog.error(f"[PuterServer.text_to_speech] ✗ Timeout after {timeout}s")
                    return None

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Error: {e}")
            plog.error(f"[PuterServer.text_to_speech] ✗ Exception | error={e}")
            return None

    def speech_to_text(self, audio_url, model='faster-whisper-large-v3', timeout=90):
        """Convert speech to text"""
        plog.info(f"[PuterServer.speech_to_text] ── STT request | model={model} | url={audio_url} ──")
        try:
            if not self.client_connected or not self.puter_ready:
                plog.warning("[PuterServer] ✗ Client not ready")
                plog.warning(f"[PuterServer.speech_to_text] ✗ Client not ready")
                return None

            plog.info(f"[PuterServer] Speech-to-Text request for: {audio_url}")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                plog.debug(f"[PuterServer.speech_to_text] Emitting 'speech_to_text_request'")
                self.socketio.emit('speech_to_text_request', {
                    'audio_url': audio_url,
                    'model': model
                })

                plog.info(f"[PuterServer] Waiting for transcription (timeout: {timeout}s)...")
                plog.debug(f"[PuterServer.speech_to_text] Waiting for transcription | timeout={timeout}s")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        text = response.get('text')
                        plog.info("[PuterServer] ✓ Transcription completed successfully")
                        plog.info(f"[PuterServer.speech_to_text] ✓ Transcription done | chars={len(text) if text else 0}")
                        return text
                    else:
                        error = response.get('error', 'Unknown error')
                        plog.error(f"[PuterServer] ✗ Transcription failed: {error}")
                        plog.error(f"[PuterServer.speech_to_text] ✗ Transcription failed | error={error}")
                        return None
                else:
                    plog.error("[PuterServer] ✗ Timeout waiting for transcription")
                    plog.error(f"[PuterServer.speech_to_text] ✗ Timeout after {timeout}s")
                    return None

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Error: {e}")
            return None

    def reset_quota(self, email, password):
        """Reset Puter.com API quota using automation"""
        plog.info(f"[PuterServer.reset_quota] ── Starting quota reset | email={email} ──")
        try:
            if not self.driver:
                plog.error("[PuterServer] ✗ Browser not initialized")
                plog.error(f"[PuterServer.reset_quota] ✗ Browser not initialized")
                return False

            plog.info("[PuterServer] Starting quota reset automation...")
            plog.info(f"[PuterServer] Using account: {email}")
            plog.debug(f"[PuterServer.reset_quota] Browser driver available, starting automation")

            original_tab = self.driver.current_window_handle

            plog.info("[PuterServer] Opening new tab for automation...")
            plog.debug(f"[PuterServer.reset_quota] Opening new browser tab")
            self.driver.execute_script("window.open('');")

            self.driver.switch_to.window(self.driver.window_handles[-1])

            try:
                wait = WebDriverWait(self.driver, 15)
                fiveSec_wait = WebDriverWait(self.driver, 5)

                plog.info("[PuterServer] Navigating to Puter dashboard...")
                self.driver.get("https://puter.com/dashboard")
                time.sleep(2)

                try:
                    expand_button = fiveSec_wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[2]/div[3]/div/button'))
                    )
                    expand_button.click()
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 1 completed")
                except Exception as e:
                    plog.info(f"[PuterServer]   Step 1 skipped: {e}")

                try:
                    element1 = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/div[1]/div[4]'))
                    )
                    element1.click()
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 2 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 2 failed: {e}")
                    return False

                try:
                    element2 = wait.until(
                        EC.element_to_be_clickable(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[2]/div[4]/div/div[3]/div[5]/div/button'))
                    )
                    element2.click()
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 3 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 3 failed: {e}")
                    return False

                try:
                    element3 = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[3]/div/div[3]/div/button[1]'))
                    )
                    element3.click()
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 4 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 4 failed: {e}")
                    return False

                try:
                    password_field = None
                    xpaths_to_try = [
                        '//*[@id="window-body-4"]/div/input',
                        '/html/body/div[3]/div/div[3]/div/input'
                    ]

                    for xpath in xpaths_to_try:
                        try:
                            password_field = wait.until(
                                EC.presence_of_element_located((By.XPATH, xpath))
                            )
                            plog.info(f"[PuterServer]   Found password field using: {xpath}")
                            break
                        except:
                            continue

                    if not password_field:
                        raise Exception("Could not find password field")

                    password_field.clear()
                    time.sleep(0.5)
                    password_field.send_keys(password)
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 5 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 5 failed: {e}")
                    return False

                try:
                    final_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-4"]/div/button[1]'))
                    )
                    final_button.click()
                    time.sleep(2)
                    plog.info("[PuterServer] ✓ Step 6 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 6 failed: {e}")
                    return False

                try:
                    setup_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-0"]/div/div[4]/button'))
                    )
                    setup_button.click()
                    time.sleep(2)
                    plog.info("[PuterServer] ✓ Step 7 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 7 failed: {e}")
                    return False

                try:
                    username = self._generate_random_username()
                    plog.info(f"[PuterServer]   Generated username: {username}")
                    username_field = wait.until(
                        EC.presence_of_element_located(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/form/div[2]/input'))
                    )
                    username_field.clear()
                    time.sleep(0.5)
                    username_field.send_keys(username)
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 8 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 8 failed: {e}")
                    return False

                try:
                    email_field = wait.until(
                        EC.presence_of_element_located(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/form/div[3]/input'))
                    )
                    email_field.clear()
                    time.sleep(0.5)
                    email_field.send_keys(email)
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 9 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 9 failed: {e}")
                    return False

                try:
                    password_field = wait.until(
                        EC.presence_of_element_located(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/form/div[4]/input'))
                    )
                    password_field.clear()
                    time.sleep(0.5)
                    password_field.send_keys(password)
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 10 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 10 failed: {e}")
                    return False

                try:
                    confirm_password_field = wait.until(
                        EC.presence_of_element_located(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/form/div[5]/input'))
                    )
                    confirm_password_field.clear()
                    time.sleep(0.5)
                    confirm_password_field.send_keys(password)
                    time.sleep(1)
                    plog.info("[PuterServer] ✓ Step 11 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 11 failed: {e}")
                    return False

                try:
                    submit_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-2"]/div/div[1]/form/button'))
                    )
                    submit_button.click()
                    time.sleep(3)
                    plog.info("[PuterServer] ✓ Step 12 completed")
                except Exception as e:
                    plog.error(f"[PuterServer] ✗ Step 12 failed: {e}")
                    return False

                plog.info("[PuterServer] ✓ All 12 steps completed! Quota reset successful!")
                return True

            finally:
                try:
                    plog.info("[PuterServer] Closing automation tab...")
                    time.sleep(7)
                    self.driver.close()
                    self.driver.switch_to.window(original_tab)
                    plog.info("[PuterServer] ✓ Returned to original tab")

                    messages = [{"role": "user", "content": "Test"}]
                    self.send_chat_request(messages=messages, model="gpt-5-nano", is_test=True)

                    plog.info("[PuterServer] Waiting for auth dialog...")
                    time.sleep(2)

                    try:
                        click_script = """
                        let puterDialog = document.querySelector('puter-dialog');
                        if (puterDialog && puterDialog.shadowRoot) {
                            let dialog = puterDialog.shadowRoot.querySelector('dialog');
                            if (dialog) {
                                let buttons = dialog.querySelectorAll('button');
                                if (buttons.length >= 2) {
                                    buttons[1].click();
                                    return 'clicked';
                                }
                            }
                        }
                        return 'not_found';
                        """

                        max_attempts = 5
                        for attempt in range(max_attempts):
                            result = self.driver.execute_script(click_script)
                            if result == 'clicked':
                                plog.info("[PuterServer] ✓ Clicked Authentication button")
                                break
                            plog.info(f"[PuterServer]   Attempt {attempt + 1}/{max_attempts}: Dialog not ready yet...")
                            time.sleep(1)
                        else:
                            plog.warning("[PuterServer] ⚠ Auth dialog did not appear (this is okay if not needed)")
                    except Exception as e:
                        plog.warning(f"[PuterServer] ⚠ Could not click auth button: {e}")

                except Exception as e:
                    plog.warning(f"[PuterServer] ⚠ Error closing tab: {e}")
                    try:
                        self.driver.switch_to.window(original_tab)
                    except:
                        pass

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Quota reset failed: {e}")
            plog.error(f"[PuterServer.reset_quota] ✗ Quota reset failed | error={e}")
            return False

    def _generate_random_username(self):
        """Generate a random username"""
        import random
        import string

        adjectives = ["Cool", "Fast", "Smart", "Bright", "Lucky", "Happy", "Swift", "Bold", "Wise", "Strong"]
        nouns = ["Tiger", "Eagle", "Dragon", "Phoenix", "Wolf", "Fox", "Hawk", "Lion", "Bear", "Shark"]

        adjective = random.choice(adjectives)
        noun = random.choice(nouns)
        numbers = ''.join(random.choices(string.digits, k=4))

        username = f"{adjective}{noun}{numbers}"
        return username

    def setup_account(self):
        """Open Puter.com for manual account setup"""
        plog.info(f"[PuterServer.setup_account] ── Starting manual account setup ──")
        try:
            if not self.driver:
                plog.error("[PuterServer] ✗ Browser not initialized")
                plog.error(f"[PuterServer.setup_account] ✗ Browser not initialized")
                return False

            plog.info("[PuterServer] Opening Puter.com for account setup...")
            plog.debug(f"[PuterServer.setup_account] Navigating to https://puter.com")
            self.driver.get("https://puter.com")

            print("\n" + "="*60)
            print("ACCOUNT SETUP")
            print("="*60)
            print("1. Create your Puter account in the browser window")
            print("2. Complete the registration process")
            print("3. Return here when done")
            print("="*60 + "\n")

            input("Press Enter when account setup is complete...")

            plog.info("[PuterServer] ✓ Account setup session completed")
            plog.info(f"[PuterServer.setup_account] ✓ Account setup completed by user")
            return True

        except Exception as e:
            plog.error(f"[PuterServer] ✗ Setup failed: {e}")
            plog.error(f"[PuterServer.setup_account] ✗ Setup failed | error={e}")
            return False

    def check_health(self):
        """Check if server is healthy"""
        plog.debug(f"[PuterServer.check_health] Checking health at port={self.port}")
        try:
            import requests
            response = requests.get(f'http://127.0.0.1:{self.port}/health', timeout=2)
            healthy = response.status_code == 200
            plog.debug(f"[PuterServer.check_health] Health check result | status={response.status_code} | healthy={healthy}")
            return healthy
        except Exception as e:
            plog.warning(f"[PuterServer.check_health] Health check failed | error={e}")
            return False

    def stop(self):
        """Stop the server and close browser"""
        plog.info(f"[PuterServer.stop] ── Stopping PuterServer ──")
        self.is_running = False
        if self.driver:
            try:
                self.driver.quit()
                plog.info(f"[PuterServer.stop] ✓ Browser driver closed")
            except Exception as e:
                plog.error(f"[PuterServer.stop] Browser close error | error={e}")
                pass
        plog.info(f"[PuterServer.stop] ✓ Server stopped")