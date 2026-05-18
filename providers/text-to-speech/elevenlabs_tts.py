"""
providers/text-to-speech/elevenlabs_provider.py
ElevenLabs TTS — modular TTS provider

SETUP: Replace API_KEY and VOICE_ID below.
Get your key + voice IDs: https://elevenlabs.io
"""

# ─── Configuration ─────────────────────────────────────────────────────────────
API_KEY  = "your-elevenlabs-api-key"
VOICE_ID = "21m00Tcm4TlvDq8ikWAM"   # Rachel (default) — copy any ID from elevenlabs.io
MODEL    = "eleven_multilingual_v2"
# ──────────────────────────────────────────────────────────────────────────────


def speak(text: str, save_to: str) -> bool:
    """
    Required signature for all TTS provider scripts.

    Args:
        text    : The text to synthesise.
        save_to : Absolute path where the audio file should be saved (mp3).

    Returns:
        True on success, False on failure.
        Any raised exception is caught and logged by the voice handler.
    """
    import requests

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    headers = {
        "Accept":       "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key":   API_KEY,
    }
    payload = {
        "text":       text,
        "model_id":   MODEL,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        print(f"[ElevenLabs TTS] Error {resp.status_code}: {resp.text[:200]}")
        return False

    with open(save_to, "wb") as f:
        f.write(resp.content)
    return True

