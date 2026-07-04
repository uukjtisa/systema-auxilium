# Voice & Text-to-Speech

## Voice mode

Voice mode lets you speak to the assistant and hear its replies read aloud. Turn
it on from the app; when active, your speech is transcribed into the chat and the
assistant's replies are synthesized through your configured TTS provider.

## TTS providers

Like LLM backends, text-to-speech is a **modular provider script** — drop a file
in `providers/text-to-speech/` and select it in Settings. A TTS provider defines:

```python
def speak(text: str, save_to: str) -> bool
```

- `text` — the cleaned string to synthesize. Punctuation and special characters
  are already stripped by the voice handler before this is called; never `None`.
- `save_to` — an absolute path ending in `.mp3`. Write valid MP3 bytes there.
- Returns `True` on success, `False` on failure. A raised exception is caught and
  logged.

The script is re-imported on every request, so live edits apply immediately, and
all configuration (keys, URLs, voice IDs) lives in the script.

### Included

- `elevenlabs_tts.py` — ElevenLabs expressive voice synthesis (laughter, sighs,
  emphasis). Add your key and voice ID at the top of the file.

### Add your own

`providers/text-to-speech/_template.py` is a ready-to-use skeleton for any TTS
service (a cloud API or a local HTTP server) and includes a paste-ready prompt you
can give an AI to generate the script for your provider. It uses only `requests`
and is fully self-contained.

See [Providers](providers.md) for the shared provider model.
