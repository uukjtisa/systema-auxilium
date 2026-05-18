"""
providers/text-to-speech/_template.py
============================
Copy this file, rename it, and point the Custom TTS Provider at it in Settings.

CONTRACT
--------
You must define exactly one function:

    def speak(text: str, save_to: str) -> bool

Parameters:
    text     -- The filtered text string to synthesise.
                Punctuation and special characters are already stripped
                by the voice handler before this function is called.

    save_to  -- Absolute path where the audio file must be saved.
                The file extension will be .mp3 — write valid MP3 bytes here.

Return:
    True on success, False on failure.
    Any raised exception will be caught and logged by the voice handler.

NOTES
-----
- This file is reimported fresh on every request. Live edits take effect immediately.
- All configuration (API keys, URLs, voices) lives here — the app forwards nothing.
- You can import any library available in your Python environment.
- You can also just use AI to build you a custom TTS script. See below.

════════════════════════════════════════════════════════════════════════════════
NOT FAMILIAR WITH PYTHON? LET AN AI WRITE THIS FOR YOU
════════════════════════════════════════════════════════════════════════════════

You can ask any AI assistant (ChatGPT, Claude, Gemini, etc.) to build your
custom TTS provider script. Just copy and paste this prompt:

──────────────────────────────────────────────────────────────────────────────
I need a Python script that connects to [your TTS provider name] and acts
as a custom TTS provider for my desktop AI assistant app.

The script must define exactly this function:

    def speak(text: str, save_to: str) -> bool

Where:
- text is the plain string to synthesise (already cleaned, never None)
- save_to is an absolute file path ending in .mp3 — write valid MP3 bytes to it
- The function must return True on success, False on failure
- Any exception raised will be caught and logged automatically

My TTS details:
- Provider: [e.g. ElevenLabs / Kokoro / Coqui / OpenAI TTS / a local HTTP server]
- API URL: [e.g. http://127.0.0.1:8080/tts  or  https://api.elevenlabs.io/v1/text-to-speech]
- API Key: [your key here, or tell the AI to leave a placeholder]
- Voice: [e.g. "default" / "af_sky" / a voice ID from your provider]

Use only the requests library (no SDK). Keep it simple and self-contained.
──────────────────────────────────────────────────────────────────────────────

Then save the script it gives you, and point the Custom TTS Provider at it
in Settings. That's it.

════════════════════════════════════════════════════════════════════════════════
"""

import requests


# ── Configure your provider here ─────────────────────────────────────────────

TTS_URL = "http://127.0.0.1:8080/tts"   # your TTS server or API endpoint
API_KEY = ""                             # leave empty if not required
VOICE   = "default"                      # voice name or ID


# ─────────────────────────────────────────────────────────────────────────────

def speak(text: str, save_to: str) -> bool:
    """Send a synthesis request to the configured provider and save the result."""

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    response = requests.post(
        TTS_URL,
        json={"text": text, "voice": VOICE},
        headers=headers,
        timeout=30,
    )

    if response.status_code != 200:
        return False

    with open(save_to, "wb") as f:
        f.write(response.content)

    return True