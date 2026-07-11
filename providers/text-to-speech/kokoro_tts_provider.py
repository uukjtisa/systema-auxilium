"""
Kokoro TTS Provider for Systema Auxilium
=========================================
High-quality local TTS using a persistent Kokoro server.
Auto-launches the server in a visible terminal window on first use.
Detects if the server is already running — never double-launches.

Requirements
------------
Install the "Kokoro local TTS" optional group in setup.py (packages: kokoro,
soundfile, flask — heavy: pulls torch). The Kokoro model (~a few hundred MB)
downloads automatically on the first synthesis. The companion server script
(_kokoro_server.py, same folder) is launched for you — closing its terminal
window stops the server; it relaunches on the next use.

Voices:
  af_heart  - Warm female (default)
  af_bella  - Bright female       af_nicole - Clear female
  af_sarah  - Soft female          af_sky   - Light female
  af_alloy  - Smooth female
  am_adam   - Calm male            am_michael - Deep male
  am_echo   - Warm male            am_liam  - Young male
  am_puck   - Playful male         am_onyx  - Deep rich male
  bf_emma   - British female       bf_isabella - British female
  bm_george - British male         bm_lewis - British male
  ef_dora   - Spanish female       em_alex  - Spanish male
"""

import subprocess, sys, os, time, requests, socket

PORT = 11235
SERVER_URL = f"http://127.0.0.1:{PORT}"
VOICE = os.environ.get("KOKORO_VOICE", "af_heart")

_PROVIDER_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_SCRIPT = os.path.join(_PROVIDER_DIR, "_kokoro_server.py")


def _server_alive() -> bool:
    """Check if the Kokoro server is already running."""
    try:
        r = requests.get(f"{SERVER_URL}/health", timeout=2)
        return r.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def _launch_server() -> bool:
    """Launch the server in a visible terminal window. Wait until alive."""
    try:
        subprocess.Popen(
            [sys.executable, _SERVER_SCRIPT],
            creationflags=subprocess.CREATE_NEW_CONSOLE,  # ← visible terminal!
        )
    except Exception:
        return False

    # Wait up to 60s for server to start
    deadline = time.time() + 60
    while time.time() < deadline:
        if _server_alive():
            return True
        time.sleep(1)

    return False


def speak(text: str, save_to: str) -> bool:
    if not _server_alive():
        if not _launch_server():
            return False

    try:
        r = requests.post(
            f"{SERVER_URL}/synthesize",
            json={"text": text, "voice": VOICE},
            timeout=120,
        )
        if r.status_code != 200:
            return False

        with open(save_to, "wb") as f:
            f.write(r.content)

        return os.path.getsize(save_to) > 1000
    except Exception:
        return False