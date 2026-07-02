"""
skills/e-mailman/e_mailman.py — Multi-account email skill script.
"""

import argparse
import datetime
import email
import imaplib
import json
import mimetypes
import smtplib
import sys
import textwrap
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

# Force UTF-8 for stdout in both terminal and pipe/subprocess contexts
if sys.stdout.encoding != "utf-8":
    try:
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer,
            encoding="utf-8",
            errors="replace",
            line_buffering=getattr(sys.stdout, "line_buffering", False),
        )
    except (AttributeError, OSError):
        pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE           = Path(__file__).parent
EMAILS_FILE     = _HERE / "emails.json"
NOTES_FILE      = _HERE / "account_notes.json"
CONTACTS_FILE   = _HERE / "contacts.json"
DEFAULT_ACCOUNT = 0

# ---------------------------------------------------------------------------
# emails.json — load, save, auto-generate
# ---------------------------------------------------------------------------

_EMAILS_TEMPLATE = {
    "_readme": (
        "e-mailman accounts file. Add your email accounts in the 'accounts' array. "
        "The '_readme' and '_fields' keys are documentation only — ignored by the script."
    ),
    "_fields": {
        "name":      "Friendly label for this account. Used with --account flag (e.g. 'gmail', 'work').",
        "account":   "Full email address.",
        "password":  "App password — could be or could be NOT your normal login password. See setup guide in SKILL.md.",
        "smtp_host": "Outgoing mail server hostname.",
        "smtp_port": "Outgoing port: 465 for SSL (most common) or 587 for STARTTLS.",
        "imap_host": "Incoming mail server hostname.",
        "imap_port": "Incoming port. Almost always 993.",
    },
    "accounts": [],
}


def load_emails_file() -> dict:
    """Load emails.json, auto-generating it if missing."""
    if not EMAILS_FILE.exists():
        save_emails_file(_EMAILS_TEMPLATE)
        print(f"[info] Created empty emails.json at {EMAILS_FILE}")
        print("[info] No accounts configured yet. Use 'account --add' or edit the file directly.")
        return dict(_EMAILS_TEMPLATE)
    with open(EMAILS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_emails_file(data: dict) -> None:
    with open(EMAILS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_accounts() -> list:
    data = load_emails_file()
    # Support both old plain-array format and new {"accounts": [...]} format
    if isinstance(data, list):
        return data
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list):
        print("[error] emails.json 'accounts' must be an array.", file=sys.stderr)
        sys.exit(1)
    return accounts


ACCOUNTS = load_accounts()

# ---------------------------------------------------------------------------
# account_notes.json — load / save
# ---------------------------------------------------------------------------

def load_notes() -> dict:
    if not NOTES_FILE.exists():
        # Auto-create with an empty entry for every configured account.
        notes = {
            acc.get("name", acc["account"]): {"notes": ""}
            for acc in ACCOUNTS
        }
        save_notes(notes)
        print(f"[info] Created account_notes.json at {NOTES_FILE} (all entries empty).")
        return notes
    with open(NOTES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_notes(notes: dict) -> None:
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=2, ensure_ascii=False)
        f.write("\n")

# ---------------------------------------------------------------------------
# Account resolution
# ---------------------------------------------------------------------------

def resolve_account(selector) -> dict:
    if not ACCOUNTS:
        print("[error] No accounts configured. Add one with: python e_mailman.py account --add ...", file=sys.stderr)
        sys.exit(1)
    if selector is None:
        idx = DEFAULT_ACCOUNT
        if idx >= len(ACCOUNTS):
            print(f"[error] DEFAULT_ACCOUNT={idx} out of range.", file=sys.stderr)
            sys.exit(1)
        return ACCOUNTS[idx]
    try:
        idx = int(selector)
        if idx < 0 or idx >= len(ACCOUNTS):
            print(f"[error] Index {idx} out of range (0–{len(ACCOUNTS)-1}).", file=sys.stderr)
            sys.exit(1)
        return ACCOUNTS[idx]
    except ValueError:
        pass
    needle = selector.lower()
    matches = [
        acc for acc in ACCOUNTS
        if needle in acc.get("name", "").lower() or needle in acc["account"].lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(f"\"{a.get('name','')}\" ({a['account']})" for a in matches)
        print(f"[error] '{selector}' matched multiple accounts: {names}.", file=sys.stderr)
        sys.exit(1)
    print(f"[error] No account matching '{selector}'. Use --info to list.", file=sys.stderr)
    sys.exit(1)


def validate_account(cfg: dict) -> None:
    if not cfg.get("account"):
        print("[error] Account entry missing 'account' field.", file=sys.stderr)
        sys.exit(1)
    if not cfg.get("password"):
        print(f"[error] Account '{cfg['account']}' missing 'password'.", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_account_banner(cfg: dict) -> None:
    label = cfg.get("name", "")
    addr  = cfg["account"]
    tag   = f"[{label}]  {addr}" if label else addr
    print("╔══════════════════════════════════════════════════════════════╗")
    print(f"  📬  Active Account : {tag}")
    print("╚══════════════════════════════════════════════════════════════╝")


def print_all_accounts() -> None:
    if not ACCOUNTS:
        print("  (no accounts configured)")
        return
    print(f"\n  {'#':<4} {'Name':<20} {'Account':<36} SMTP                  IMAP")
    print("  " + "─" * 84)
    for i, acc in enumerate(ACCOUNTS):
        default_tag = " ← default" if i == DEFAULT_ACCOUNT else ""
        smtp = f"{acc['smtp_host']}:{acc['smtp_port']}"
        imap = f"{acc['imap_host']}:{acc['imap_port']}"
        print(f"  {i:<4} {acc.get('name',''):<20} {acc['account']:<36} {smtp:<22}{imap}{default_tag}")
    print()

# ---------------------------------------------------------------------------
# READ helpers (IMAP)
# ---------------------------------------------------------------------------

def decode_str(value) -> str:
    if value is None:
        return "(none)"
    parts = decode_header(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def format_local_date(date_str: str) -> str:
    """Convert an email date string to the system's local timezone. No extra modules needed."""
    try:
        dt = parsedate_to_datetime(date_str)
        local_dt = dt.astimezone()          # stdlib: converts to OS local timezone
        offset = local_dt.strftime("%z")    # e.g. "+0800"
        if len(offset) == 5:
            offset = f"{offset[:3]}:{offset[3:]}"   # → "+08:00"
        tz_name = local_dt.strftime("%Z") or f"UTC{offset}"
        return local_dt.strftime(f"%a, %d %b %Y  %H:%M:%S  ({tz_name})")
    except Exception:
        return date_str     # fallback: show original if unparseable



def get_attachments(msg) -> list:
    """Return a list of (filename, part) tuples for all attachments in the message."""
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                raw_name = part.get_filename()
                filename = decode_str(raw_name) if raw_name else f"attachment_{len(attachments)+1}"
                attachments.append((filename, part))
    return attachments


def save_attachments(msg, save_dir: Path, uid: str) -> list:
    """Save all attachments from msg into save_dir. Returns list of saved filenames."""
    save_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for filename, part in get_attachments(msg):
        # Sanitise filename for filesystem
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in filename).strip()
        if not safe_name:
            safe_name = f"attachment_{uid}"
        dest = save_dir / safe_name
        # Avoid overwriting — append uid suffix if collision
        if dest.exists():
            stem, suffix = dest.stem, dest.suffix
            dest = save_dir / f"{stem}_{uid}{suffix}"
        dest.write_bytes(part.get_payload(decode=True) or b"")
        saved.append(str(dest))
        print(f"  💾  Saved: {dest}")
    return saved


def get_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp  = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                except Exception:
                    body = "(could not decode body)"
                break
    else:
        try:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            body = "(could not decode body)"
    lines = body.strip().splitlines()
    if len(lines) > 30:
        lines = lines[:30] + [f"... [{len(lines) - 30} more lines]"]
    return "\n".join(lines)


def imap_connect(cfg: dict) -> imaplib.IMAP4_SSL:
    try:
        mail = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        mail.login(cfg["account"], cfg["password"])
        mail.select("inbox")
        return mail
    except imaplib.IMAP4.error as e:
        print(f"[error] IMAP auth/connection failed: {e}", file=sys.stderr)
        sys.exit(1)


def fetch_emails(mail: imaplib.IMAP4_SSL, ids: list) -> list:
    results = []
    for uid in ids:
        _, data = mail.fetch(uid, "(RFC822)")
        raw = data[0][1]
        msg = email.message_from_bytes(raw)
        attachment_names = [fname for fname, _ in get_attachments(msg)]
        results.append({
            "uid":          uid.decode(),
            "from":         decode_str(msg.get("From")),
            "to":           decode_str(msg.get("To")),
            "subject":      decode_str(msg.get("Subject")),
            "date":         msg.get("Date", "(no date)"),
            "message_id":   msg.get("Message-ID", ""),
            "references":   msg.get("References", ""),
            "body":         get_body(msg),
            "attachments":  attachment_names,
            "_raw":         raw,   # kept for --save-attachments / download
        })
    return results


def print_email(em: dict, index: int) -> None:
    print(f"\n{'═' * 62}")
    print(f"  📧  Email #{index}  (UID: {em['uid']})")
    print(f"{'═' * 62}")
    print(f"  From    : {em['from']}")
    print(f"  Subject : {em['subject']}")
    print(f"  Date    : {format_local_date(em['date'])}")
    if em.get("attachments"):
        print(f"  📎  Attachments ({len(em['attachments'])}):")
        for name in em["attachments"]:
            print(f"        • {name}")
    print(f"{'─' * 62}")
    print(textwrap.indent(em["body"] or "(empty body)", "  "))
    print()


def cmd_latest(cfg: dict, count: int = 5, save_dir: Path = None) -> None:
    print_account_banner(cfg)
    mail = imap_connect(cfg)
    _, data = mail.search(None, "ALL")
    all_ids = data[0].split()
    if not all_ids:
        print("[info] Inbox is empty.")
        mail.logout()
        return
    target = list(reversed(all_ids[-count:]))
    emails = fetch_emails(mail, target)
    mail.logout()
    print(f"\n  Showing latest {len(emails)} email(s):\n")
    for i, em in enumerate(emails, 1):
        print_email(em, i)
        if save_dir and em.get("attachments"):
            msg = email.message_from_bytes(em["_raw"])
            save_attachments(msg, save_dir, em["uid"])
    print(f"[ok] Fetched {len(emails)} email(s).")


def cmd_from(cfg: dict, sender: str, save_dir: Path = None) -> None:
    print_account_banner(cfg)
    mail = imap_connect(cfg)
    _, data = mail.search(None, f'FROM "{sender}"')
    ids = data[0].split()
    if not ids:
        print(f"[info] No emails found from: {sender}")
        mail.logout()
        return
    target = list(reversed(ids[-10:]))
    emails = fetch_emails(mail, target)
    mail.logout()
    print(f"\n  Emails from '{sender}' — {len(emails)} found:\n")
    for i, em in enumerate(emails, 1):
        print_email(em, i)
        if save_dir and em.get("attachments"):
            msg = email.message_from_bytes(em["_raw"])
            save_attachments(msg, save_dir, em["uid"])


def cmd_search(cfg: dict, keyword: str, save_dir: Path = None) -> None:
    print_account_banner(cfg)
    mail = imap_connect(cfg)
    _, data = mail.search(None, f'SUBJECT "{keyword}"')
    ids = data[0].split()
    if not ids:
        print(f"[info] No emails found matching: {keyword}")
        mail.logout()
        return
    target = list(reversed(ids[-10:]))
    emails = fetch_emails(mail, target)
    mail.logout()
    print(f"\n  Emails matching '{keyword}' — {len(emails)} found:\n")
    for i, em in enumerate(emails, 1):
        print_email(em, i)
        if save_dir and em.get("attachments"):
            msg = email.message_from_bytes(em["_raw"])
            save_attachments(msg, save_dir, em["uid"])

# ---------------------------------------------------------------------------
# SEND helpers (SMTP)
# ---------------------------------------------------------------------------

def attach_file(msg: EmailMessage, path: str) -> None:
    p = Path(path)
    if not p.exists():
        print(f"[error] Attachment not found: {path}", file=sys.stderr)
        sys.exit(1)
    mime_type, _ = mimetypes.guess_type(str(p))
    maintype, subtype = (mime_type.split("/", 1) if mime_type else ("application", "octet-stream"))
    with open(p, "rb") as f:
        msg.add_attachment(f.read(), maintype=maintype, subtype=subtype, filename=p.name)
    print(f"[info] Attached: {p.name}")


def cmd_send(cfg: dict, to: str, subject: str, body: str, attachments: list,
             in_reply_to: str = "", references: str = "") -> None:
    msg = EmailMessage()
    msg["From"]    = cfg["account"]
    msg["To"]      = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        # References = previous chain + the message we're replying to
        ref_chain = (references.strip() + " " + in_reply_to).strip()
        msg["References"] = ref_chain
    msg.set_content(body)
    for path in attachments:
        attach_file(msg, path)
    port = cfg["smtp_port"]
    host = cfg["smtp_host"]
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port) as smtp:
                smtp.login(cfg["account"], cfg["password"])
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(cfg["account"], cfg["password"])
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print(f"[error] Auth failed for {cfg['account']}. Check password in emails.json.", file=sys.stderr)
        sys.exit(1)
    except (smtplib.SMTPException, OSError) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] Sent from {cfg['account']} → {to}")

def fetch_reply_meta(cfg: dict, uid: str) -> dict:
    """Fetch Message-ID, References, From, and Subject from an email by UID (for reply threading)."""
    mail = imap_connect(cfg)
    uid_bytes = uid.encode()
    _, data = mail.fetch(uid_bytes, "(RFC822)")
    mail.logout()
    if not data or data[0] is None:
        print(f"[error] No email found with UID {uid}.", file=sys.stderr)
        sys.exit(1)
    raw = data[0][1]
    msg = email.message_from_bytes(raw)
    return {
        "message_id": msg.get("Message-ID", ""),
        "references":  msg.get("References", ""),
        "from":        decode_str(msg.get("From", "")),
        "subject":     decode_str(msg.get("Subject", "")),
    }


# ---------------------------------------------------------------------------
# DOWNLOAD subcommand — fetch attachments from a specific email by UID
# ---------------------------------------------------------------------------

def cmd_download(cfg: dict, uid: str, save_dir: Path) -> None:
    print_account_banner(cfg)
    mail = imap_connect(cfg)
    uid_bytes = uid.encode()
    _, data = mail.fetch(uid_bytes, "(RFC822)")
    if not data or data[0] is None:
        print(f"[error] No email found with UID {uid}.", file=sys.stderr)
        mail.logout()
        sys.exit(1)
    raw = data[0][1]
    msg = email.message_from_bytes(raw)
    mail.logout()

    attachments = get_attachments(msg)
    if not attachments:
        print(f"[info] Email UID {uid} has no attachments.")
        return

    print(f"\n  📎  {len(attachments)} attachment(s) found in UID {uid}:")
    for name, _ in attachments:
        print(f"       • {name}")
    print()
    saved = save_attachments(msg, save_dir, uid)
    print(f"\n[ok] {len(saved)} file(s) saved to: {save_dir}")



# ---------------------------------------------------------------------------
# NOTES subcommand
# ---------------------------------------------------------------------------

def cmd_notes_show() -> None:
    notes = load_notes()
    if not notes:
        print("[info] account_notes.json is empty.")
        return
    for account, data in notes.items():
        email_str = data.get("email", "")
        note_str  = data.get("notes", "(no notes)")
        print(f"\n  ▸ {account}")
        if email_str:
            print(f"    📧  {email_str}")
        else:
            print(f"    📧  (no email set — use: notes --set-email \"{account}\" \"addr@example.com\")")
        print(f"    📝  {note_str}")
    print()


def cmd_notes_set(account: str, note: str) -> None:
    notes = load_notes()
    existing_email = notes.get(account, {}).get("email", "")
    notes[account] = {"email": existing_email, "notes": note}
    save_notes(notes)
    print(f"[ok] Notes replaced for '{account}'.")


def cmd_notes_set_email(account: str, email_addr: str) -> None:
    notes = load_notes()
    existing_notes = notes.get(account, {}).get("notes", "")
    notes[account] = {"email": email_addr, "notes": existing_notes}
    save_notes(notes)
    print(f"[ok] Email set for '{account}' → {email_addr}")


def cmd_notes_add(account: str, note: str) -> None:
    notes = load_notes()
    existing_email = notes.get(account, {}).get("email", "")
    existing_notes = notes.get(account, {}).get("notes", "")
    updated = (existing_notes + "\n" + note).strip() if existing_notes else note
    notes[account] = {"email": existing_email, "notes": updated}
    save_notes(notes)
    print(f"[ok] Note appended to '{account}'.")

# ---------------------------------------------------------------------------
# CONTACTS — contacts.json  (name → email + optional note)
# ---------------------------------------------------------------------------
# Separate from account_notes.json (which is about *sending* accounts).
# This is an address book: maps friendly names to email addresses so the
# agent can resolve "send my mom an email" without asking every time.
# Structure:
#   { "mom": {"email": "mom@example.com", "note": "..."},  ... }
# ---------------------------------------------------------------------------

def load_contacts() -> dict:
    if not CONTACTS_FILE.exists():
        save_contacts({})
        print(f"[info] Created empty contacts.json at {CONTACTS_FILE}")
        return {}
    with open(CONTACTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_contacts(contacts: dict) -> None:
    with open(CONTACTS_FILE, "w", encoding="utf-8") as f:
        json.dump(contacts, f, indent=2, ensure_ascii=False)
        f.write("\n")


def resolve_contact(name_or_email: str) -> str | None:
    """Return the email address for a contact label, or None if not found.
    If name_or_email already looks like an address, return it as-is."""
    if "@" in name_or_email:
        return name_or_email
    contacts = load_contacts()
    needle = name_or_email.strip().lower()
    # Exact key match first
    for key, data in contacts.items():
        if key.lower() == needle:
            return data["email"]
    # Partial match fallback
    matches = [(k, v) for k, v in contacts.items() if needle in k.lower()]
    if len(matches) == 1:
        return matches[0][1]["email"]
    if len(matches) > 1:
        names = ", ".join(k for k, _ in matches)
        print(f"[warn] '{name_or_email}' matched multiple contacts: {names}. Be more specific.")
    return None


def cmd_contacts_list() -> None:
    contacts = load_contacts()
    if not contacts:
        print("[info] contacts.json is empty. Add one with: contacts --add \"name\" --email \"addr\"")
        return
    print(f"\n  {'Label':<20} {'Email':<36} Note")
    print("  " + "─" * 72)
    for label, data in sorted(contacts.items()):
        note = data.get("note", "")
        print(f"  {label:<20} {data['email']:<36} {note}")
    print()


def cmd_contacts_add(label: str, email_addr: str, note: str = "") -> None:
    contacts = load_contacts()
    key = label.strip()
    existed = key in contacts
    contacts[key] = {"email": email_addr.strip(), "note": note.strip()}
    save_contacts(contacts)
    verb = "Updated" if existed else "Added"
    print(f"[ok] {verb} contact '{key}' → {email_addr}")


def cmd_contacts_remove(label: str) -> None:
    contacts = load_contacts()
    needle = label.strip().lower()
    to_delete = [k for k in contacts if k.lower() == needle]
    if not to_delete:
        print(f"[error] No contact matching '{label}'.", file=sys.stderr)
        sys.exit(1)
    for k in to_delete:
        del contacts[k]
    save_contacts(contacts)
    print(f"[ok] Removed contact '{label}'.")


def cmd_contacts_search(query: str) -> None:
    contacts = load_contacts()
    needle = query.strip().lower()
    results = {k: v for k, v in contacts.items()
               if needle in k.lower() or needle in v["email"].lower()
               or needle in v.get("note", "").lower()}
    if not results:
        print(f"[info] No contacts matching '{query}'.")
        return
    print(f"\n  {'Label':<20} {'Email':<36} Note")
    print("  " + "─" * 72)
    for label, data in results.items():
        print(f"  {label:<20} {data['email']:<36} {data.get('note', '')}")
    print()

# ---------------------------------------------------------------------------
# ACCOUNT subcommand — add / remove / list
# ---------------------------------------------------------------------------

def cmd_account_list() -> None:
    print_all_accounts()


def cmd_account_add(name: str, email_addr: str, password: str,
                    smtp_host: str, smtp_port: int,
                    imap_host: str, imap_port: int) -> None:
    data = load_emails_file()
    accounts = data.get("accounts", []) if isinstance(data, dict) else data

    # Check for duplicate
    for acc in accounts:
        if acc["account"].lower() == email_addr.lower():
            print(f"[error] Account '{email_addr}' already exists.", file=sys.stderr)
            sys.exit(1)

    new_entry = {
        "name":      name,
        "account":   email_addr,
        "password":  password,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "imap_host": imap_host,
        "imap_port": imap_port,
    }

    if isinstance(data, dict):
        data["accounts"].append(new_entry)
        save_emails_file(data)
    else:
        data.append(new_entry)
        with open(EMAILS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print(f"[ok] Account '{name}' ({email_addr}) added to emails.json.")

    # Keep account_notes.json in sync — add an empty entry if not already present.
    notes = load_notes()
    key = name or email_addr
    if key not in notes:
        notes[key] = {"email": email_addr, "notes": ""}
        save_notes(notes)
        print(f"[info] Added notes entry for '{key}' ({email_addr}) in account_notes.json.")


def cmd_account_remove(selector: str) -> None:
    data = load_emails_file()
    accounts = data.get("accounts", []) if isinstance(data, dict) else data

    needle = selector.lower()
    remaining = [
        acc for acc in accounts
        if needle not in acc.get("name", "").lower() and needle not in acc["account"].lower()
    ]

    removed = len(accounts) - len(remaining)
    if removed == 0:
        print(f"[error] No account matching '{selector}'.", file=sys.stderr)
        sys.exit(1)

    if isinstance(data, dict):
        data["accounts"] = remaining
        save_emails_file(data)
    else:
        with open(EMAILS_FILE, "w", encoding="utf-8") as f:
            json.dump(remaining, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print(f"[ok] Removed {removed} account(s) matching '{selector}'.")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="e-mailman: multi-account read/send email script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--account", metavar="NAME_OR_INDEX", default=None)
    parser.add_argument("--info", action="store_true", help="List accounts and exit")

    sub = parser.add_subparsers(dest="mode")

    # --- READ ---
    read_p = sub.add_parser("read")
    read_grp = read_p.add_mutually_exclusive_group(required=True)
    read_grp.add_argument("--latest", action="store_true")
    read_grp.add_argument("--from",   dest="sender")
    read_grp.add_argument("--search", dest="keyword")
    read_p.add_argument("--count", type=int, default=5)
    read_p.add_argument("--save-attachments", dest="save_attachments", metavar="DIR", default=None,
                        help="Download all attachments from fetched emails into DIR")

    # --- DOWNLOAD ---
    dl_p = sub.add_parser("download", help="Download attachments from a specific email by UID")
    dl_p.add_argument("--uid", required=True, help="Email UID (shown in read output)")
    dl_p.add_argument("--dir", dest="dl_dir", default="./attachments",
                      help="Directory to save attachments (default: ./attachments)")

    # --- SEND ---
    send_p = sub.add_parser("send")
    send_p.add_argument("--to",           required=True)
    send_p.add_argument("--subject",      default=None,
                        help="Subject line. Auto-filled with 'Re: …' when --reply-to-uid is used.")
    send_p.add_argument("--body",         required=True)
    send_p.add_argument("--attachments",  nargs="*", default=[], metavar="FILE")
    send_p.add_argument("--reply-to-uid", dest="reply_uid", default=None,
                        help="UID of the email to reply to (sets In-Reply-To / References headers)")

    # --- NOTES ---
    notes_p = sub.add_parser("notes")
    notes_grp = notes_p.add_mutually_exclusive_group()
    notes_grp.add_argument("--set",       nargs=2, metavar=("ACCOUNT", "NOTE"))
    notes_grp.add_argument("--add",       nargs=2, metavar=("ACCOUNT", "NOTE"))
    notes_grp.add_argument("--set-email", nargs=2, metavar=("ACCOUNT", "EMAIL"),
                           dest="set_email", help="Set the email address shown for an account entry")

    # --- CONTACTS ---
    con_p = sub.add_parser("contacts", help="Manage contacts.json address book")
    con_grp = con_p.add_mutually_exclusive_group(required=True)
    con_grp.add_argument("--list",   action="store_true",  help="Show all contacts")
    con_grp.add_argument("--add",    action="store_true",  help="Add or update a contact")
    con_grp.add_argument("--remove", metavar="LABEL",      help="Remove a contact by label")
    con_grp.add_argument("--search", metavar="QUERY",      help="Search contacts by name, email, or note")
    con_p.add_argument("--label",    default="",  help="Friendly name  (required with --add)")
    con_p.add_argument("--email",    default="",  help="Email address  (required with --add)")
    con_p.add_argument("--note",     default="",  help="Optional note  (used with --add)")

    # --- ACCOUNT ---
    acc_p = sub.add_parser("account", help="Manage accounts in emails.json")
    acc_grp = acc_p.add_mutually_exclusive_group(required=True)
    acc_grp.add_argument("--list",   action="store_true")
    acc_grp.add_argument("--remove", metavar="NAME_OR_EMAIL")
    acc_grp.add_argument("--add",    action="store_true")
    acc_p.add_argument("--name",      default="")
    acc_p.add_argument("--email",     default="")
    acc_p.add_argument("--password",  default="")
    acc_p.add_argument("--smtp-host", dest="smtp_host", default="")
    acc_p.add_argument("--smtp-port", dest="smtp_port", type=int, default=465)
    acc_p.add_argument("--imap-host", dest="imap_host", default="")
    acc_p.add_argument("--imap-port", dest="imap_port", type=int, default=993)

    return parser


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.info:
        if args.account:
            cfg = resolve_account(args.account)
            print(f"\n  [{cfg.get('name','')}] {cfg['account']}")
            print(f"  SMTP: {cfg['smtp_host']}:{cfg['smtp_port']}")
            print(f"  IMAP: {cfg['imap_host']}:{cfg['imap_port']}")
        else:
            print(f"\n  {len(ACCOUNTS)} account(s) (DEFAULT={DEFAULT_ACCOUNT}):\n")
            print_all_accounts()
        sys.exit(0)

    if not args.mode:
        parser.print_help()
        sys.exit(1)

    # --- NOTES ---
    if args.mode == "notes":
        if args.set:
            cmd_notes_set(args.set[0], args.set[1])
        elif args.add:
            cmd_notes_add(args.add[0], args.add[1])
        elif args.set_email:
            cmd_notes_set_email(args.set_email[0], args.set_email[1])
        else:
            cmd_notes_show()
        return

    # --- CONTACTS ---
    if args.mode == "contacts":
        if args.list:
            cmd_contacts_list()
        elif args.search:
            cmd_contacts_search(args.search)
        elif args.remove:
            cmd_contacts_remove(args.remove)
        elif args.add:
            if not args.label or not args.email:
                print("[error] --add requires --label and --email.", file=sys.stderr)
                sys.exit(1)
            cmd_contacts_add(args.label, args.email, args.note)
        return

    # --- ACCOUNT ---
    if args.mode == "account":
        if args.list:
            cmd_account_list()
        elif args.remove:
            cmd_account_remove(args.remove)
        elif args.add:
            missing = [f for f in ("email", "smtp_host", "imap_host")
                       if not getattr(args, f.replace("-", "_"), "")]
            if missing:
                print(f"[error] --add requires: --email, --smtp-host, --imap-host (missing: {', '.join(missing)})", file=sys.stderr)
                sys.exit(1)
            cmd_account_add(
                args.name, args.email, args.password,
                args.smtp_host, args.smtp_port,
                args.imap_host, args.imap_port,
            )
        return

    # --- READ / SEND ---
    cfg = resolve_account(args.account)
    validate_account(cfg)

    if args.mode == "read":
        save_dir = Path(args.save_attachments) if args.save_attachments else None
        if args.sender:
            cmd_from(cfg, args.sender, save_dir)
        elif args.keyword:
            cmd_search(cfg, args.keyword, save_dir)
        else:
            cmd_latest(cfg, args.count, save_dir)

    elif args.mode == "download":
        cmd_download(cfg, args.uid, Path(args.dl_dir))

    elif args.mode == "send":
        body = args.body.replace("\\n", "\n")

        # Resolve recipient — contacts.json lookup if not a raw email address
        recipient = resolve_contact(args.to)
        if not recipient:
            print(f"[error] No contact found for '{args.to}'. "
                  f"Add them with: contacts --add --label \"{args.to}\" --email \"addr@example.com\"",
                  file=sys.stderr)
            sys.exit(1)
        if recipient != args.to:
            print(f"[info] Resolved '{args.to}' → {recipient}")

        in_reply_to = ""
        references  = ""
        subject     = args.subject

        if args.reply_uid:
            meta = fetch_reply_meta(cfg, args.reply_uid)
            in_reply_to = meta["message_id"]
            references  = meta["references"]
            # Auto-build subject if not explicitly provided
            if not subject:
                orig_subj = meta["subject"]
                subject = orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
        elif not subject:
            print("[error] --subject is required when not using --reply-to-uid.", file=sys.stderr)
            sys.exit(1)

        print(f"[info] From: {cfg['account']}  →  To: {recipient}  |  Subject: {subject}")
        cmd_send(cfg, recipient, subject, body, args.attachments,
                 in_reply_to=in_reply_to, references=references)


if __name__ == "__main__":
    main()