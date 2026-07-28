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

You do NOT need to understand any of the above. Open any AI assistant
(ChatGPT, Claude, Gemini, Copilot, ...) and paste ONE of the two prompts
below. Save whatever it writes as a .py file in this folder, then select it in
Settings ▸ Voice ▸ TTS Provider.

    PROMPT A ("grill me")   — the AI interviews you first, one question at a
                              time, then writes the script. Use this if you
                              are unsure what your TTS service supports.
                              RECOMMENDED.
    PROMPT B ("just build") — you already know your endpoint, key and voice;
                              fill in the blanks and it writes it in one shot.

Both prompts contain the FULL contract, so the AI has everything it needs
without seeing this file.

────────────────────────────────────────────────────────────────────────────────
PROMPT A — "GRILL ME, THEN BUILD IT"   (copy everything between the lines)
────────────────────────────────────────────────────────────────────────────────
You are going to write a Python "TTS provider script" for my desktop AI
assistant app (Systema Auxilium). Do NOT write any code yet.

FIRST, interview me. Ask ONE question at a time, wait for my answer, and adapt
the next question to what I said. Keep questions short and in plain language —
assume I am not a programmer, and explain any jargon in half a sentence. If I
say "I don't know", offer a sensible default and move on. If something I want
is impossible or a bad idea, say so plainly and propose the better option.

Cover at least these areas, in roughly this order:

1. WHICH SERVICE: Which text-to-speech service am I using (ElevenLabs, OpenAI
   TTS, Azure, Google Cloud TTS, Kokoro, Piper, Coqui, a local server,
   something else)? Cloud API or running on my own machine?
2. ENDPOINT + AUTH: Exact URL. How does auth work (Bearer token, api-key
   header, none)? Real key in the file, or a placeholder I fill in later?
3. VOICE: Which voice id/name? Do I want ONE fixed voice, or several to pick
   from? Ask how to list available voices if I don't know mine.
4. REQUEST SHAPE: What does the API expect (JSON body? form fields? which
   field names for text and voice?) and what does it return — raw audio bytes,
   or JSON containing base64 audio, or a URL to download? If I'm not sure, ask
   me to paste the provider's curl example or docs snippet. Do NOT guess.
5. AUDIO FORMAT: Does it return MP3? If it can only return WAV/OGG/PCM, say
   so and tell me it must be converted to MP3 before saving (and what that
   needs, e.g. ffmpeg or pydub).
6. TUNING: speed/rate, pitch, stability/similarity, language, model id, output
   quality — whichever my service supports. Which do I want to change later?
7. RELIABILITY: rate limits, character limits per request (should long text be
   split into chunks and joined?), retries, proxy or self-signed certificate.
8. ANYTHING ELSE unusual about this service I have not mentioned?

THEN show me a SHORT summary of what you are about to build and ask me to
confirm or correct it.

ONLY AFTER I confirm, write the complete script following the CONTRACT
SPECIFICATION below EXACTLY. Output the whole file in one code block, ready to
save. After the code, tell me in 3 lines: what to name the file, where to put
it, and how to test it.

<<<PASTE THE "CONTRACT SPECIFICATION" SECTION BELOW HERE>>>
────────────────────────────────────────────────────────────────────────────────

────────────────────────────────────────────────────────────────────────────────
PROMPT B — "JUST BUILD IT"   (copy everything between the lines)
────────────────────────────────────────────────────────────────────────────────
Write a complete Python "TTS provider script" for my desktop AI assistant app
(Systema Auxilium), following the CONTRACT SPECIFICATION below EXACTLY.

My details:
- TTS service:     [e.g. ElevenLabs / OpenAI TTS / Kokoro / local server]
- API URL:         [e.g. https://api.elevenlabs.io/v1/text-to-speech/<voice>]
- Auth:            [e.g. xi-api-key header / Bearer token / none]
- API key:         [paste your key, or write "leave a placeholder"]
- Voice:           [voice id or name, or several if you want a choice]
- Request format:  [JSON fields it expects, or "see this curl example: ..."]
- Response format: [raw MP3 bytes / WAV / JSON with base64 / URL]
- Tuning options:  [speed, pitch, model id, ... or "none"]
- Anything special:[character limits, rate limits, or "nothing"]

If any answer is missing, contradictory, or looks wrong for that service, ask
me BEFORE writing code. Otherwise output the whole file in one code block.

<<<PASTE THE "CONTRACT SPECIFICATION" SECTION BELOW HERE>>>
────────────────────────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════════════════════════
CONTRACT SPECIFICATION  (paste this along with this _tempalte.py into whichever prompt you chose and send it to an AI agent)
════════════════════════════════════════════════════════════════════════════════

The script is a single self-contained .py file. The app imports it fresh on
every request, so it must be safe to import repeatedly and must do NO network
calls at import time.

REQUIRED — exactly one entry point:

    def speak(text: str, save_to: str) -> bool

Arguments:
  text    : str — the text to synthesise. Already cleaned by the app
                  (punctuation/symbols stripped); never None, never empty.
  save_to : str — an ABSOLUTE file path ending in .mp3. The function must
                  write valid MP3 bytes to exactly this path. Do not change
                  the path, do not write anywhere else, do not return audio.

Return:
  True  — the file was written successfully and contains playable MP3 audio.
  False — synthesis failed (the app logs it and stays silent for that line).
  Raising an exception is also handled: the app catches and logs it.

HOW IT IS USED:
  The app calls speak() once per spoken line, then plays the saved file. It
  may be called many times in a row, and it runs on a background thread — do
  not open windows, prompt for input, or block forever.

REQUIREMENTS:
  - If the API returns something other than MP3 (WAV/OGG/PCM), convert to MP3
    before writing (e.g. with pydub + ffmpeg) — or say clearly in a comment at
    the top of the file that ffmpeg must be installed.
  - If the API has a per-request character limit, split long text into chunks,
    synthesise each, and concatenate them into ONE MP3 at save_to.
  - Verify the response before writing: on a non-200 status or an error body,
    return False rather than writing a broken/empty file.
  - Do NOT hardcode a very long timeout; a modest one (e.g. 30-60s) is fine
    for TTS.
  - Do NOT do network calls or logins at import time.
  - Do NOT print secrets. Use a placeholder for the API key unless I said
    otherwise.
  - Keep configuration (URL, key, voice, tuning) as clearly-named constants at
    the TOP of the file, each with a short comment, so I can edit them without
    reading the code.
  - Keep it ONE self-contained file. Prefer the `requests` library, or the
    service's official SDK if that is clearly simpler.
  - Use plain, readable code with short comments explaining each block — a
    non-programmer will be reading this file.
  - Include a `if __name__ == "__main__":` block that synthesises a short test
    sentence to a temporary .mp3 and prints whether it succeeded and the file
    size, so I can run the file directly to verify my key and connection.

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