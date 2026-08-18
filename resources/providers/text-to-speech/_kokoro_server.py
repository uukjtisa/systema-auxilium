"""
Kokoro TTS Server
=================
Persistent HTTP server that keeps Kokoro loaded in memory.
Launched in its own terminal window — visible so you can close it anytime.

Endpoints:
  GET  /health      → {"status": "ok", "voice": "af_heart"}
  POST /synthesize  → MP3 binary (send JSON: {"text": "...", "voice": "..."})

Default port: 11235
"""

import os, subprocess, numpy as np
from flask import Flask, request, Response, jsonify

PORT = 11235
DEFAULT_VOICE = "af_heart"

# ── Lazy-init model on first request ────────────────────────────────────────
pipeline = None

def get_pipeline():
    global pipeline
    if pipeline is None:
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code='a')
    return pipeline

# ── Flask app ────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "voice": DEFAULT_VOICE})

@app.route('/synthesize', methods=['POST'])
def synthesize():
    data = request.get_json(force=True)
    text = data.get('text', '')
    voice = data.get('voice', DEFAULT_VOICE)

    # Safety: strip any emojis that might have slipped through
    import re as _re
    _emoji_pat = _re.compile(
        "["
        "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF"
        "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF\U00002702-\U000027B0"
        "\U000024C2-\U0001F251\U0000FE00-\U0000FE0F"
        "\U0000200D\U00002600-\U000026FF]",
        flags=_re.UNICODE
    )
    text = _emoji_pat.sub('', text)

    import soundfile as sf

    pipe = get_pipeline()
    generator = pipe(text, voice=voice)

    segments = []
    for gs, ps, audio in generator:
        segments.append(audio)

    full = np.concatenate(segments) if len(segments) > 1 else segments[0]

    wav_temp = os.path.join(os.path.dirname(__file__), f"_temp_{os.getpid()}.wav")
    sf.write(wav_temp, full, 24000)

    mp3_temp = wav_temp.replace('.wav', '.mp3')
    subprocess.run([
        'ffmpeg', '-y', '-i', wav_temp,
        '-codec:a', 'libmp3lame', '-b:a', '192k',
        mp3_temp
    ], capture_output=True, timeout=30)

    with open(mp3_temp, 'rb') as f:
        mp3_data = f.read()

    os.remove(wav_temp)
    os.remove(mp3_temp)

    return Response(mp3_data, mimetype='audio/mpeg')

if __name__ == '__main__':
    print(f"\n🎤 Kokoro TTS Server — http://127.0.0.1:{PORT}")
    print(f"   Voice: {DEFAULT_VOICE}")
    print("   Press Ctrl+C or close this window to stop.\n")
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)