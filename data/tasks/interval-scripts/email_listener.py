"""
Script Trigger Template
────────────────────────────────────────────────────────────────
Email Listener — fires when any UNSEEN email lands in either inbox.
State is persisted to a .json file so reloads don't re-initialize.
On True, writes new mail details to a temp file, then deletes it
after 5 seconds via a background thread.
────────────────────────────────────────────────────────────────
"""

import imaplib
import email
import json
import threading
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# ══════════════════════════════════════════════════════════════
# ✏️  CREDENTIALS — Replace these before running!
# ══════════════════════════════════════════════════════════════

ACCOUNT_1_ADDRESS  = "your_primary_email@example.com"   # replace me
ACCOUNT_1_PASSWORD = "your_password_here"               # replace me
ACCOUNT_1_IMAP     = "imap.example.com"                 # replace me
ACCOUNT_1_PORT     = 993                                # replace me if different

ACCOUNT_2_ADDRESS  = "your_secondary_email@example.com" # replace me
ACCOUNT_2_PASSWORD = "your_password_here"               # replace me
ACCOUNT_2_IMAP     = "imap.example.com"                 # replace me
ACCOUNT_2_PORT     = 993                                # replace me if different

# ══════════════════════════════════════════════════════════════
# ✏️  TRUSTED SENDERS — Add addresses you want special handling for
# ══════════════════════════════════════════════════════════════

TRUSTED_USER_EMAIL = "you@example.com"                  # replace me — your own email
TRUSTED_MOM_EMAIL  = "mom@example.com"                  # replace me — or remove if unused

# ══════════════════════════════════════════════════════════════

# ── Account configurations ─────────────────────────────────────
ACCOUNTS = [ # ACCOUNTS TO LISTEN FOR NEW MAILS
    {
        "name": "Primary Inbox",
        "account":   ACCOUNT_1_ADDRESS,
        "password":  ACCOUNT_1_PASSWORD, #GMAIL USES APP PASSWORD INSTEAD, GET IT BY 
                                         #1. Go to myaccount.google.com → Security
                                         #2. Search "App Passwords" in the search bar
                                         #3. Click App Passwords → name it anything (e.g. "e-mail-LISTENER") → Create
                                         #4. Copy the 16-character code — this is your App Password

        "imap_host": ACCOUNT_1_IMAP, # GMAIL USES imap.gmail.com
        "imap_port": ACCOUNT_1_PORT, # GMAIL USES 993
                                     # SEARCH FOR YOUR PROVIDER DETAILS ON THIS ONE.
    },
    {
        "name": "Secondary Inbox",
        "account":   ACCOUNT_2_ADDRESS,
        "password":  ACCOUNT_2_PASSWORD,
        "imap_host": ACCOUNT_2_IMAP,
        "imap_port": ACCOUNT_2_PORT,
    },
]

# ── Paths ──────────────────────────────────────────────────────
_STATE_FILE = Path.home() / "email_trigger_state.json"
_TEMP_FILE  = Path.home() / "email_trigger_latest.json"

# ── Delete delay (seconds) ─────────────────────────────────────
_DELETE_AFTER = 5


# ──────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _get_unseen_uids(account: dict) -> set[str]:
    mail = imaplib.IMAP4_SSL(account["imap_host"], account["imap_port"])
    mail.login(account["account"], account["password"])
    mail.select("INBOX")
    status, data = mail.uid("search", None, "UNSEEN")
    mail.logout()
    if status != "OK" or not data[0]:
        return set()
    return set(data[0].decode().split())


def _decode_str(value: str) -> str:
    """Decode an encoded email header string."""
    parts = decode_header(value or "")
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += part
    return result.strip()


def _get_body(msg: email.message.Message) -> str:
    """Extract plain-text body, falling back to HTML if needed."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True).decode(charset, errors="replace")
            except Exception:
                payload = ""
            if ct == "text/plain" and not plain:
                plain = payload
            elif ct == "text/html" and not html:
                html = payload
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            payload = ""
        if msg.get_content_type() == "text/html":
            html = payload
        else:
            plain = payload

    return plain if plain else html


def _fetch_full_message(account: dict, uid: str) -> dict:
    """Fetch the full message and return a structured dict."""
    try:
        mail = imaplib.IMAP4_SSL(account["imap_host"], account["imap_port"])
        mail.login(account["account"], account["password"])
        mail.select("INBOX")
        _, msg_data = mail.uid("fetch", uid.encode(), "(RFC822)")
        mail.logout()

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = _decode_str(msg.get("Subject", "(no subject)"))
        sender  = _decode_str(msg.get("From", "(unknown sender)"))

        # Parse date
        date_raw = msg.get("Date", "")
        try:
            dt = parsedate_to_datetime(date_raw)
            time_sent = dt.isoformat()
        except Exception:
            time_sent = date_raw

        body = _get_body(msg)

        return {
            "uid":       uid,
            "account":   account["account"],
            "from":      sender,
            "subject":   subject,
            "time_sent": time_sent,
            "body":      body,
        }
    except Exception as e:
        return {
            "uid":     uid,
            "account": account["account"],
            "error":   str(e),
        }


def _write_temp_file(messages: list[dict]):
    """Write new messages to the temp file, then delete after _DELETE_AFTER seconds."""
    payload = json.dumps(messages, indent=2, ensure_ascii=False)
    _TEMP_FILE.write_text(payload, encoding="utf-8")
    print(f"[email_trigger] Wrote {len(messages)} new message(s) to {_TEMP_FILE}")

    def _delayed_delete():
        time.sleep(_DELETE_AFTER)
        try:
            _TEMP_FILE.unlink(missing_ok=True)
            print(f"[email_trigger] Temp file deleted after {_DELETE_AFTER}s.")
        except Exception as e:
            print(f"[email_trigger] Could not delete temp file: {e}")

    t = threading.Thread(target=_delayed_delete, daemon=True)
    t.start()


# ──────────────────────────────────────────────────────────────
# Main trigger
# ──────────────────────────────────────────────────────────────

def fire_ping() -> bool:
    """
    Return True  → new email detected → fire the ping.
    Return False → no new mail → wait for next poll cycle.

    On True, writes new mail details to:
        ~/email_trigger_latest.json
    The file is automatically deleted after 5 seconds.
    """
    state = _load_state()
    new_mail_found = False
    all_new_messages = []

    for acc in ACCOUNTS:
        key = acc["account"]
        try:
            current_uids = _get_unseen_uids(acc)
        except Exception as e:
            print(f"[email_trigger] IMAP error for {key}: {e}")
            continue

        if key not in state:
            state[key] = list(current_uids)
            _save_state(state)
            print(f"[email_trigger] Initialized {acc['name']} — "
                  f"{len(current_uids)} existing unread message(s) skipped.")
            continue

        seen     = set(state[key])
        new_uids = current_uids - seen

        if new_uids:
            for uid in new_uids:
                msg_data = _fetch_full_message(acc, uid)
                all_new_messages.append(msg_data)
                print(f"[email_trigger] NEW MAIL on {acc['name']}: "
                      f"UID={uid} | From: {msg_data.get('from')} "
                      f"| Subject: {msg_data.get('subject')}")
            state[key] = list(seen | new_uids)
            _save_state(state)
            new_mail_found = True

    if new_mail_found:
        _write_temp_file(all_new_messages)

    return new_mail_found