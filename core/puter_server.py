"""
Puter.js Server - Selenium-based WebSocket server with persistent browser profile
Complete feature set: Chat, Images, TTS (Standard + ElevenLabs), STT, Text-to-Image, Quota Reset, Account Setup

KEY UPDATES IN THIS VERSION:
1. Added ElevenLabs TTS support via provider parameter
2. Comprehensive documentation of all ElevenLabs voices and models
3. Support for all ElevenLabs output formats
4. Automatic provider detection (standard vs elevenlabs)
5. All previous features maintained and working
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


# Disable Flask's default logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


class PuterServer:
    """Selenium-based Flask-SocketIO server for Puter.js AI with full feature support including ElevenLabs"""

    def __init__(self, port=5555, log_callback=None):
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
        self.response_ready = threading.Event()

        # Profile path in working directory
        self.profile_path = os.path.join(os.getcwd(), "PuterAPIServerPROFILE")

    def log(self, message):
        """Log message"""
        print(f"[Puter Server] {message}")
        if self.log_callback:
            self.log_callback(f"[Puter Server] {message}", "INFO")

    def _get_html_template(self):
        """Return dark mode HTML template with full Puter.ai features including ElevenLabs"""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Puter.js AI Server - Full Feature Set + ElevenLabs</title>
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
        <h1>🤖 Puter.js AI Server + ElevenLabs</h1>
        <p class="subtitle">Selenium-powered persistent browser with full AI feature set including ElevenLabs TTS</p>
        
        <div id="connectionStatus" class="status disconnected">
            <strong>⏳ Connecting...</strong><br>
            Establishing WebSocket connection
        </div>

        <div style="margin: 20px 0;">
            <span class="badge warning" id="puter-status">Loading Puter.js...</span>
            <span class="badge warning" id="websocket-status">WebSocket: Connecting...</span>
            <span class="badge">Port: 5555</span>
            <span class="badge">Profile: PuterAPIServerPROFILE</span>
        </div>

        <div class="toggle-container">
            <div class="toggle-switch" id="verboseToggle">
                <div class="toggle-slider"></div>
            </div>
            <span class="toggle-label">Verbose Mode (Show Raw Data & Errors)</span>
        </div>

        <div class="info-box">
            <strong>✨ Available Features</strong>
            <div class="features-grid">
                <div class="feature-card">
                    <div class="icon">💬</div>
                    <div class="title">Chat (GPT-5.2, GPT-5, etc.)</div>
                </div>
                <div class="feature-card">
                    <div class="icon">🖼️</div>
                    <div class="title">Image Analysis (GPT-5-nano)</div>
                </div>
                <div class="feature-card">
                    <div class="icon">🎨</div>
                    <div class="title">Text-to-Image (DALL-E 3)</div>
                </div>
                <div class="feature-card">
                    <div class="icon">🔊</div>
                    <div class="title">Text-to-Speech (TTS-1)</div>
                </div>
                <div class="feature-card elevenlabs">
                    <div class="icon">🎙️</div>
                    <div class="title">ElevenLabs TTS (Premium)</div>
                </div>
                <div class="feature-card">
                    <div class="icon">🎤</div>
                    <div class="title">Speech-to-Text (Whisper)</div>
                </div>
                <div class="feature-card">
                    <div class="icon">♻️</div>
                    <div class="title">Quota Reset</div>
                </div>
            </div>
        </div>

        <div class="info-box">
            <strong>📊 Activity Log</strong>
            <div id="activityLog">
                <div class="log-entry log-info">Initializing...</div>
            </div>
        </div>

        <div class="info-box">
            <strong>ℹ️ Important Notes</strong>
            • Keep this tab open for API requests to work<br>
            • First request may require authentication popup<br>
            • <strong>NEW: ElevenLabs TTS with 70+ languages and premium voices</strong><br>
            • Image uploads limited to 512MB via 0x0.st<br>
            • History managed server-side for efficiency<br>
            • Browser profile persists across sessions<br>
            • TTI/TTS return base64 data (auto-saved with save_to parameter)<br>
            • Long operations: timeout increased to 180s<br>
            • WebSocket buffer: 50MB (supports large images)<br>
            • <strong>Verbose mode shows all raw received data and detailed errors</strong>
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
            
            socket = io('http://127.0.0.1:5555', {
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
            self.log("✓ WebSocket client connected")

        @socketio.on('disconnect')
        def handle_disconnect():
            self.client_connected = False
            self.log("⚠ WebSocket client disconnected")

        @socketio.on('puter_ready')
        def handle_puter_ready(data):
            self.puter_ready = True
            self.log("✓ Puter.js is ready with ElevenLabs support")

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
        try:
            self.log("Starting Selenium browser...")

            os.makedirs(self.profile_path, exist_ok=True)

            chrome_options = webdriver.ChromeOptions()
            chrome_options.add_argument(f"user-data-dir={self.profile_path}")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
            chrome_options.add_argument("--disable-blink-features=AutomationControlled")
            chrome_options.add_experimental_option('useAutomationExtension', False)
            chrome_options.add_experimental_option("detach", True)

            self.driver = webdriver.Chrome(options=chrome_options)
            self.log(f"✓ Browser started with profile: {self.profile_path}")

            self.driver.get(f"http://127.0.0.1:{self.port}")
            self.log(f"✓ Navigated to http://127.0.0.1:{self.port}")

            self.browser_ready = True
            return True

        except Exception as e:
            self.log(f"✗ Failed to start browser: {e}")
            return False

    def start(self):
        """Start the server and browser"""
        if self.is_running:
            self.log("Server already running")
            return True

        try:
            def run_server():
                try:
                    self.app, self.socketio = self.create_app()
                    self.log(f"Starting WebSocket server on port {self.port}")
                    self.socketio.run(
                        self.app,
                        host='127.0.0.1',
                        port=self.port,
                        debug=False,
                        use_reloader=False,
                        allow_unsafe_werkzeug=True
                    )
                except Exception as e:
                    self.log(f"Server error: {e}")
                    self.is_running = False

            self.server_thread = threading.Thread(target=run_server, daemon=True)
            self.server_thread.start()
            self.is_running = True

            time.sleep(2)
            self.log(f"✓ Server started at http://127.0.0.1:{self.port}")

            if not self._start_browser():
                self.log("⚠ Browser failed to start, but server is running")
                return False

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
            self.log(f"✗ Failed to start: {e}")
            self.is_running = False
            return False

    def _save_base64_to_file(self, base64_data, output_path):
        """Save base64 data URL to file"""
        try:
            import base64
            import re

            match = re.match(r'data:([^;]+);base64,(.+)', base64_data)
            if not match:
                self.log("✗ Invalid base64 data URL format")
                return False

            mime_type = match.group(1)
            b64_data = match.group(2)

            binary_data = base64.b64decode(b64_data)

            with open(output_path, 'wb') as f:
                f.write(binary_data)

            file_size_kb = len(binary_data) / 1024
            self.log(f"✓ Saved to {output_path} ({file_size_kb:.1f}KB)")
            return True

        except Exception as e:
            self.log(f"✗ Failed to save base64 data: {e}")
            return False

    def upload_image_to_host(self, image_path, max_size_mb=512):
        """Upload image to 0x0.st and return public URL"""
        try:
            if not os.path.exists(image_path):
                self.log(f"✗ Image file not found: {image_path}")
                return None

            file_size_mb = os.path.getsize(image_path) / (1024 * 1024)
            if file_size_mb > max_size_mb:
                self.log(f"✗ Image too large: {file_size_mb:.2f}MB (max: {max_size_mb}MB)")
                return None

            self.log(f"Uploading image ({file_size_mb:.2f}MB) to 0x0.st...")

            headers = {
                "User-Agent": "curl/8.0.0",
                "Accept": "*/*"
            }

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    with open(image_path, "rb") as f:
                        response = requests.post(
                            "https://0x0.st",
                            files={"file": f},
                            headers=headers,
                            timeout=120
                        )

                    if response.status_code == 200:
                        url = response.text.strip()
                        self.log(f"✓ Image uploaded: {url}")
                        time.sleep(1)
                        return url
                    else:
                        self.log(f"⚠ Upload attempt {attempt+1} failed: HTTP {response.status_code}")
                        if attempt < max_retries - 1:
                            time.sleep(2)
                except Exception as e:
                    self.log(f"⚠ Upload attempt {attempt+1} error: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(2)

            self.log(f"✗ Upload failed after {max_retries} attempts")
            return None

        except Exception as e:
            self.log(f"✗ Upload error: {e}")
            return None

    def send_chat_request(self, messages, model='gpt-5-chat-latest',
                         temperature=0.7, max_tokens=2000, image=None, timeout=60, is_test=False):
        """Send a chat request with full message history"""
        try:
            if not self.client_connected or not self.puter_ready:
                self.log("✗ Client not ready")
                return None

            image_url = None
            if image:
                if image.startswith('http://') or image.startswith('https://'):
                    image_url = image
                    self.log(f"Using image URL: {image_url}")
                else:
                    self.log(f"Uploading local image: {image}")
                    image_url = self.upload_image_to_host(image)
                    if not image_url:
                        self.log("✗ Image upload failed")
                        return None

            self.log(f"Sending chat request (model: {model})")
            if image_url:
                self.log(f"  → With image: {image_url}")
            self.log(f"  → Messages: {len(messages)} in history")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                self.socketio.emit('chat_request', {
                    'messages': messages,
                    'model': model,
                    'temperature': temperature,
                    'max_tokens': max_tokens,
                    'image': image_url
                })

                if is_test:
                    self.log("Test Message is Sent...")
                    return None

                self.log(f"Waiting for response (timeout: {timeout}s)...")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        result = response.get('response')
                        self.log(f"✓ Received response ({len(result)} chars)")
                        return result
                    else:
                        error_msg = response.get('response', 'Unknown error')
                        self.log(f"✗ Request failed: {error_msg}")
                        return None
                else:
                    self.log("✗ Timeout waiting for response")
                    return None
            else:
                self.log("✗ SocketIO not initialized")
                return None

        except Exception as e:
            self.log(f"✗ Error: {e}")
            return None

    def text_to_image(self, prompt, model='gpt-image-1', width=None, height=None,
                     save_to=None, timeout=180):
        """Generate image from text"""
        try:
            if not self.client_connected or not self.puter_ready:
                self.log("✗ Client not ready")
                return None

            self.log(f"Text-to-Image request: {prompt[:50]}...")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                self.socketio.emit('text_to_image_request', {
                    'prompt': prompt,
                    'model': model,
                    'width': width,
                    'height': height
                })

                self.log(f"Waiting for image generation (timeout: {timeout}s)...")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        image_data = response.get('image_url')
                        is_base64 = response.get('is_base64', False)

                        if is_base64:
                            size_kb = len(image_data) / 1024
                            self.log(f"✓ Received base64 image ({size_kb:.1f}KB)")
                            if save_to:
                                self._save_base64_to_file(image_data, save_to)
                            return image_data
                        else:
                            self.log(f"✓ Image URL: {image_data}")
                            return image_data
                    else:
                        error = response.get('error', 'Unknown error')
                        self.log(f"✗ Generation failed: {error}")
                        return None
                else:
                    self.log("✗ Timeout waiting for image generation")
                    return None

        except Exception as e:
            self.log(f"✗ Error: {e}")
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
                self.log("✗ Client not ready")
                return None

            self.log(f"Text-to-Speech request ({provider}): {text[:50]}...")

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

                self.socketio.emit('text_to_speech_request', request_data)

                self.log(f"Waiting for audio generation (timeout: {timeout}s)...")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        audio_data = response.get('audio_url')
                        is_base64 = response.get('is_base64', False)
                        response_provider = response.get('provider', 'standard')

                        if is_base64:
                            self.log(f"✓ Received base64 audio data ({response_provider})")
                            if save_to:
                                self._save_base64_to_file(audio_data, save_to)
                            return audio_data
                        else:
                            self.log(f"✓ Audio URL ({response_provider}): {audio_data}")
                            return audio_data
                    else:
                        error = response.get('error', 'Unknown error')
                        self.log(f"✗ Generation failed: {error}")
                        return None
                else:
                    self.log("✗ Timeout waiting for audio generation")
                    return None

        except Exception as e:
            self.log(f"✗ Error: {e}")
            return None

    def speech_to_text(self, audio_url, model='faster-whisper-large-v3', timeout=90):
        """Convert speech to text"""
        try:
            if not self.client_connected or not self.puter_ready:
                self.log("✗ Client not ready")
                return None

            self.log(f"Speech-to-Text request for: {audio_url}")

            self.latest_response = None
            self.response_ready.clear()

            if self.socketio:
                self.socketio.emit('speech_to_text_request', {
                    'audio_url': audio_url,
                    'model': model
                })

                self.log(f"Waiting for transcription (timeout: {timeout}s)...")
                if self.response_ready.wait(timeout=timeout):
                    response = self.latest_response
                    if response and response.get('success'):
                        text = response.get('text')
                        self.log(f"✓ Transcription completed successfully")
                        return text
                    else:
                        error = response.get('error', 'Unknown error')
                        self.log(f"✗ Transcription failed: {error}")
                        return None
                else:
                    self.log("✗ Timeout waiting for transcription")
                    return None

        except Exception as e:
            self.log(f"✗ Error: {e}")
            return None

    def reset_quota(self, email, password):
        """Reset Puter.com API quota using automation"""
        try:
            if not self.driver:
                self.log("✗ Browser not initialized")
                return False

            self.log("Starting quota reset automation...")
            self.log(f"Using account: {email}")

            original_tab = self.driver.current_window_handle

            self.log("Opening new tab for automation...")
            self.driver.execute_script("window.open('');")

            self.driver.switch_to.window(self.driver.window_handles[-1])

            try:
                wait = WebDriverWait(self.driver, 15)
                fiveSec_wait = WebDriverWait(self.driver, 5)

                self.log("Navigating to Puter dashboard...")
                self.driver.get("https://puter.com/dashboard")
                time.sleep(2)

                try:
                    expand_button = fiveSec_wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[2]/div[3]/div/button'))
                    )
                    expand_button.click()
                    time.sleep(1)
                    self.log("✓ Step 1 completed")
                except Exception as e:
                    self.log(f"  Step 1 skipped: {e}")

                try:
                    element1 = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/div[1]/div[3]'))
                    )
                    element1.click()
                    time.sleep(1)
                    self.log("✓ Step 2 completed")
                except Exception as e:
                    self.log(f"✗ Step 2 failed: {e}")
                    return False

                try:
                    element2 = wait.until(
                        EC.element_to_be_clickable(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[2]/div[3]/div/div[3]/div[5]/div/button'))
                    )
                    element2.click()
                    time.sleep(1)
                    self.log("✓ Step 3 completed")
                except Exception as e:
                    self.log(f"✗ Step 3 failed: {e}")
                    return False

                try:
                    element3 = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '/html/body/div[3]/div/div[3]/div/button[1]'))
                    )
                    element3.click()
                    time.sleep(1)
                    self.log("✓ Step 4 completed")
                except Exception as e:
                    self.log(f"✗ Step 4 failed: {e}")
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
                            self.log(f"  Found password field using: {xpath}")
                            break
                        except:
                            continue

                    if not password_field:
                        raise Exception("Could not find password field")

                    password_field.clear()
                    time.sleep(0.5)
                    password_field.send_keys(password)
                    time.sleep(1)
                    self.log("✓ Step 5 completed")
                except Exception as e:
                    self.log(f"✗ Step 5 failed: {e}")
                    return False

                try:
                    final_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-4"]/div/button[1]'))
                    )
                    final_button.click()
                    time.sleep(2)
                    self.log("✓ Step 6 completed")
                except Exception as e:
                    self.log(f"✗ Step 6 failed: {e}")
                    return False

                try:
                    setup_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-0"]/div/div[4]/button'))
                    )
                    setup_button.click()
                    time.sleep(2)
                    self.log("✓ Step 7 completed")
                except Exception as e:
                    self.log(f"✗ Step 7 failed: {e}")
                    return False

                try:
                    username = self._generate_random_username()
                    self.log(f"  Generated username: {username}")
                    username_field = wait.until(
                        EC.presence_of_element_located(
                            (By.XPATH, '/html/body/div[2]/div[3]/div/div[1]/form/div[2]/input'))
                    )
                    username_field.clear()
                    time.sleep(0.5)
                    username_field.send_keys(username)
                    time.sleep(1)
                    self.log("✓ Step 8 completed")
                except Exception as e:
                    self.log(f"✗ Step 8 failed: {e}")
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
                    self.log("✓ Step 9 completed")
                except Exception as e:
                    self.log(f"✗ Step 9 failed: {e}")
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
                    self.log("✓ Step 10 completed")
                except Exception as e:
                    self.log(f"✗ Step 10 failed: {e}")
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
                    self.log("✓ Step 11 completed")
                except Exception as e:
                    self.log(f"✗ Step 11 failed: {e}")
                    return False

                try:
                    submit_button = wait.until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@id="window-body-2"]/div/div[1]/form/button'))
                    )
                    submit_button.click()
                    time.sleep(3)
                    self.log("✓ Step 12 completed")
                except Exception as e:
                    self.log(f"✗ Step 12 failed: {e}")
                    return False

                self.log("✓ All 12 steps completed! Quota reset successful!")
                return True

            finally:
                try:
                    self.log("Closing automation tab...")
                    time.sleep(7)
                    self.driver.close()
                    self.driver.switch_to.window(original_tab)
                    self.log("✓ Returned to original tab")

                    messages = [{"role": "user", "content": "Test"}]
                    self.send_chat_request(messages=messages, model="gpt-5-nano", is_test=True)

                    self.log("Waiting for auth dialog...")
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
                                self.log("✓ Clicked Authentication button")
                                break
                            self.log(f"  Attempt {attempt + 1}/{max_attempts}: Dialog not ready yet...")
                            time.sleep(1)
                        else:
                            self.log("⚠ Auth dialog did not appear (this is okay if not needed)")
                    except Exception as e:
                        self.log(f"⚠ Could not click auth button: {e}")

                except Exception as e:
                    self.log(f"⚠ Error closing tab: {e}")
                    try:
                        self.driver.switch_to.window(original_tab)
                    except:
                        pass

        except Exception as e:
            self.log(f"✗ Quota reset failed: {e}")
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
        try:
            if not self.driver:
                self.log("✗ Browser not initialized")
                return False

            self.log("Opening Puter.com for account setup...")
            self.driver.get("https://puter.com")

            print("\n" + "="*60)
            print("ACCOUNT SETUP")
            print("="*60)
            print("1. Create your Puter account in the browser window")
            print("2. Complete the registration process")
            print("3. Return here when done")
            print("="*60 + "\n")

            input("Press Enter when account setup is complete...")

            self.log("✓ Account setup session completed")
            return True

        except Exception as e:
            self.log(f"✗ Setup failed: {e}")
            return False

    def check_health(self):
        """Check if server is healthy"""
        try:
            import requests
            response = requests.get(f'http://127.0.0.1:{self.port}/health', timeout=2)
            return response.status_code == 200
        except:
            return False

    def stop(self):
        """Stop the server and close browser"""
        self.is_running = False
        if self.driver:
            try:
                self.driver.quit()
                self.log("✓ Browser closed")
            except:
                pass
        self.log("Server stopped")