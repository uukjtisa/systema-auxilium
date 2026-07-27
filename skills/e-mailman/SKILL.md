---
name: e-mailman
description: >
  Unified email skill — reads AND sends emails across multiple accounts.
---

# e-mailman Skill

## Files

| File | Purpose |
|------|---------|
| `e_mailman.py` | Main script |
| `emails.json` | Account credentials — auto-generated if missing |
| `account_notes.json` | Per-account **operating instructions** — which account to use, AND how to behave while using it |

---

## Execution notes (Python subprocess)

When calling `e_mailman.py` from Python via `subprocess.run` (or `Popen`), **you must always add `encoding="utf-8"`** alongside `text=True`.

Without it, `capture_output=True` can silently return `None` for stdout on Windows (pipe inherit issue after the script reconfigures stdout), or emoji arrows will crash with `UnicodeEncodeError`.

Correct:
```python
subprocess.run(
    [sys.executable, script, "notes"],
    capture_output=True, text=True, encoding="utf-8", errors="replace"
)
```

Incorrect:
```python
subprocess.run(
    [sys.executable, script, "notes"],
    capture_output=True, text=True      # May return stdout=None or crash on emoji
)
```

---

## STEP 0 — Read the notes. Always. Before anything else.

```bash
python e_mailman.py notes
```

This is **not just an account picker.** Each entry in `account_notes.json` is
that account's standing instructions, and they cover two different things:

1. **WHEN to use the account** — which kind of task it is for.
2. **HOW to conduct yourself while using it** — who is speaking, what to always
   check, what never to do.

Nearly every failure with this skill is the same one: doing (1) and skipping (2).

### Picking the account

**If notes clearly match the task** → pick that account silently, no confirmation, just act.  
**If notes are ambiguous** → reason from context and pick the best fit.  
**If notes are all empty and only 1 account exists** → use it silently.  
**If notes are all empty and multiple accounts exist** → list them and ask the user which to use.

> `account_notes.json` is auto-created with empty entries per configured account if missing. Accounts added via `account --add` also get an empty entry automatically.

**After the user picks an account** (when notes were empty), offer once:
> "Would you like to add a note to **[account]** so I don't have to ask next time?"  
> If yes → `python e_mailman.py notes --set "account" "their note"`. Drop it if they decline or ignore.

Once resolved in a conversation, keep using it — don't re-ask.

---

## STEP 1 — The selected account's notes are ABSOLUTE

Having chosen an account, **re-read its note and treat every line as a binding
instruction for the rest of the interaction.** The notes are not background
colour and not a hint. They outrank your own defaults, your habits, and your
reading of how the user phrased the request.

This matters most for **VOICE — who the email is coming from.**

A note may establish any of these, and you follow whichever it says:

- a personal account that writes **as its owner**;
- a shared, assistant, or role account that writes **as itself**, delivering a
  message on someone's behalf;
- a team or business account that writes **as the organisation**.

**Whatever the note says, that is who you are in the body of the email** — in
the greeting, in every sentence, and in the sign-off.

**If the note says nothing about voice**, then and only then fall back to the
neutral default: write plainly on the user's behalf, and make clear who it is
from. Never invent a persona a note did not ask for.

### The failure this section exists to stop

The note says the account speaks **as itself** — a messenger, a liaison, an
assistant. The agent reads the note, uses it to *choose* the account, and then
writes the body as though it were the user anyway: first person as the user, the
user's name, the user's sign-off. The recipient receives a message claiming to
be written by someone who did not write it.

That is a real failure, not a stylistic quibble. It misrepresents the sender.

### Before writing a single line of a body, answer these

1. Which account is sending?
2. What does **its note** say about who is speaking?
3. Does the draft I am about to write actually sound like that person?

If the answers disagree, the note wins. Rewrite the draft.

### Never infer the voice from the user's phrasing

> "Email my mom that I'll be late."

This does **not** mean "write as the user." It means *deliver that message* — in
whatever voice the sending account's note specifies. The user is telling you
**what to convey**, not **who to be.** Who to be is the note's job.

The same applies to "tell them…", "say that I…", "let her know I…". These
describe the content. The account's note describes the speaker.

---

## READ

```bash
python e_mailman.py --account "gmail" read --latest [--count N] [--save-attachments DIR]
python e_mailman.py --account "gmail" read --from "addr" [--save-attachments DIR]
python e_mailman.py --account "gmail" read --search "keyword" [--save-attachments DIR]
```

No confirmation needed — run and summarize.

Dates are automatically converted to the **system's local timezone** (no extra modules required — uses the OS clock). Regardless of what timezone the sender was in, the displayed date always reflects local time.

Attachment filenames are always listed in the email summary (with a 📎 indicator and the UID).  
Add `--save-attachments DIR` to automatically download all attachments from fetched emails into `DIR`.

---

## DOWNLOAD

Download attachments from a **specific email** by its UID (shown in `read` output):

```bash
python e_mailman.py --account "gmail" download --uid 12345
python e_mailman.py --account "gmail" download --uid 12345 --dir ./my_files
```

- `--uid` is required. UIDs appear in the `Email #N (UID: …)` header when reading.
- `--dir` defaults to `./attachments` if omitted.
- Filenames are sanitised and won't overwrite existing files (a UID suffix is appended on collision).
- If the email has no attachments, the script says so and exits cleanly.

---

## SEND

**Compose in the voice the sending account's note establishes (STEP 1).** Do
that before drafting, not as a pass afterwards — a body written in the wrong
voice does not get fixed by swapping the sign-off.

Show the draft first and get **yes / edit / cancel**. The draft states the voice
in force, so a mis-applied persona is caught before it is sent rather than after:

```
📨 Draft
From      : sender@example.com   (account: "label")
Speaking as: <who the note says is speaking — or "the user (no voice set in notes)">
To        : recipient@example.com
Subject   : ...
Body      : ...
Attachments: none
Send? (yes / edit / cancel)
```

If the account's note sets no voice, say so on that line explicitly rather than
leaving it blank — it tells the user a note is missing, and they can add one.

On yes:

```bash
# Fresh email
python e_mailman.py --account "disroot" send \
  --to "addr" --subject "..." --body "..." \
  [--attachments f1 f2]

# Reply to an existing email (threads correctly in all email clients)
python e_mailman.py --account "disroot" send \
  --reply-to-uid UID \
  --to "addr" --body "..." \
  [--subject "..."]          # optional — auto-fills as "Re: <original subject>" if omitted
  [--attachments f1 f2]
```

`--reply-to-uid` takes the UID shown in the `Email #N (UID: …)` header when reading. It automatically sets the `In-Reply-To` and `References` headers so the message threads correctly in the recipient's email client.

---

## NOTES commands

```bash
python e_mailman.py notes                                    # show all (displays label, email, and note)
python e_mailman.py notes --set "account" "note"             # replace note text
python e_mailman.py notes --add "account" "note"             # append a line to note
python e_mailman.py notes --set-email "account" "addr@x.com" # set or update the email shown for an entry
```

Each entry in `account_notes.json` holds the email address and the note:
```json
{
  "personal inbox": {
    "email": "you@example.com",
    "notes": "Personal Gmail. Use for READING mail. When sending from here, write as me — first person, my name, my sign-off."
  },
  "assistant outbox": {
    "email": "assistant@example.org",
    "notes": "Use for SENDING unless told otherwise. Speak as YOURSELF, delivering a message on my behalf — a messenger, not me. Never write as if you were me, and don't quote me unless asked."
  }
}
```

Both halves of a note matter: the first sentence decides **when** the account is
used, the rest decides **how you behave** while using it (see STEP 1). The second
example above is exactly the case the agent keeps getting wrong — it picks that
account correctly and then writes the body as the user anyway.

When reading notes, the output clearly shows the label, email, and note for each entry — so the agent always knows which address it's dealing with. If an entry has no email set yet, it will say so and suggest the command to fix it.

---

## CONTACTS

`contacts.json` is a personal address book — separate from `account_notes.json`. It maps friendly labels (like "mom", "boss", "landlord") to email addresses, so the agent can resolve names without asking every time.

The `--to` flag on `send` accepts either a raw email address **or** a contact label. If a label is given, it is automatically resolved via `contacts.json`.

```bash
python e_mailman.py contacts --list
python e_mailman.py contacts --add --label "mom"  --email "mom@example.com" [--note "optional note"]
python e_mailman.py contacts --remove "mom"
python e_mailman.py contacts --search "mom"
```

### Proactive contact-saving behavior (initiative rule)

**When the user mentions a person by name for the first time and no matching contact exists**, the agent must:

1. Ask for their email address (once, clearly).
2. Send the email.
3. Immediately after — without being asked — run:
   ```bash
   python e_mailman.py contacts --add --label "name" --email "their@email.com"
   ```
4. Confirm: `"I've saved [name] as a contact so you won't need to tell me next time."`

**When the user provides a raw email address the agent hasn't seen before** (not already in contacts), after sending offer once:
> "Want me to save [addr] as a contact? If so, what label should I use?"  
> If yes → run `contacts --add`. If they decline or ignore, drop it.

Once a contact exists, use it silently — never ask for the email again.

---

## ACCOUNT commands

```bash
python e_mailman.py account --list
python e_mailman.py account --remove "name_or_email"
python e_mailman.py account --add \
  --name "label" --email "x@y.com" --password "..." \
  --smtp-host "host" --smtp-port 465 \
  --imap-host "host" --imap-port 993
```

`emails.json` is auto-created with instructions if it doesn't exist yet.

---

## SETUP — Adding a new account

When the user wants to add an email account, walk them through it. Collect all required fields, then run `account --add`.

### What to ask

1. Email address
2. Which provider (or "other" for custom)
3. App password (explain how to get it — see table below)

### Provider settings

| Provider | smtp_host | smtp_port | imap_host | imap_port | App password |
|---|---|---|---|---|---|
| **Gmail** | smtp.gmail.com | 465 | imap.gmail.com | 993 | myaccount.google.com → Security → 2-Step Verification → App Passwords |
| **Disroot** | disroot.org | 587 | disroot.org | 993 | Account settings → Security → set a separate app password |
| **Outlook / Hotmail** | smtp-mail.outlook.com | 587 | outlook.office365.com | 993 | account.microsoft.com → Security → Advanced security → App passwords |
| **Yahoo** | smtp.mail.yahoo.com | 465 | imap.mail.yahoo.com | 993 | myaccount.yahoo.com → Security → Generate app password |
| **iCloud** | smtp.mail.me.com | 587 | imap.mail.me.com | 993 | appleid.apple.com → Sign-In & Security → App-Specific Passwords |
| **Other / custom** | Ask the user or check provider's IMAP/SMTP docs | — | — | — | Varies — usually under account security settings |

> **App passwords** are required when 2FA is on (which it should be). They are separate short passwords that bypass 2FA just for this app. The user's main password will NOT work.

### After collecting all info

Run `account --add` with the gathered values. Confirm success, then offer to add a note for this account.

---

## Edge cases

| Situation | Action |
|---|---|
| Note sets a voice that isn't the user | Write the whole body as that voice. This is the note doing its job, not an error. |
| Note sets no voice | Neutral default — plainly on the user's behalf, sender made clear. Say so on the draft's `Speaking as` line. |
| User's wording implies "write as me", note says otherwise | **The note wins.** The user described the message, not the speaker. |
| User overrides the voice for one message | Honour it for that message only; don't rewrite the note unless they ask. |
| `account_notes.json` missing | Script auto-creates it with empty entries per account. If all empty + multiple accounts, ask user. |
| `emails.json` missing | Script auto-creates it; prompt user to add an account |
| Notes don't match any account in `emails.json` | Warn, run `--info`, ask to confirm |
| User says "use a different account" | Override for this action only |
| Auth failure | Show error, tell user to check `emails.json` — likely wrong app password |
| No emails found | Say so, suggest alternatives |
| Attachment path missing | Tell user before running |

---

## Usage summary

```bash
# Account selection AND conduct — notes drive both (STEP 0 + STEP 1)
python e_mailman.py notes                                         # ALWAYS first: which account, and who speaks
python e_mailman.py notes --set "gmail" "personal, day-to-day"  # set a note
python e_mailman.py notes --add "gmail" "also used for Drive"   # append to a note
python e_mailman.py account --list                               # show all accounts

# Reading  (dates shown in local system time; UIDs shown in Email #N header — needed for download/reply)
python e_mailman.py --account gmail read --latest                # last 5 emails
python e_mailman.py --account gmail read --latest --count 10     # last N emails
python e_mailman.py --account gmail read --from "boss@co.com"   # filter by sender
python e_mailman.py --account gmail read --search "invoice"      # filter by subject

# Attachment listing & downloading
python e_mailman.py --account gmail read --latest --save-attachments ./downloads
                                                                 # read + save all attachments
python e_mailman.py --account gmail download --uid 12345         # download from a specific email
python e_mailman.py --account gmail download --uid 12345 --dir ./my_files

# Contacts (address book — resolves names to emails on send)
python e_mailman.py contacts --list
python e_mailman.py contacts --add --label "mom" --email "mom@example.com" --note "optional"
python e_mailman.py contacts --remove "mom"
python e_mailman.py contacts --search "mom"

# Sending  (always show draft + confirm first)
python e_mailman.py --account disroot send \
  --to "mom" --subject "Hello" --body "Hi there"          # contact label
python e_mailman.py --account disroot send \
  --to "user@example.com" --subject "Hello" --body "Hi"   # raw address
python e_mailman.py --account disroot send \
  --to "user@example.com" --subject "Files" --body "See attached" \
  --attachments report.pdf photo.jpg

# Replying  (threads correctly in all email clients)
python e_mailman.py --account disroot send \
  --reply-to-uid 12345 \
  --to "user@example.com" \
  --body "Thanks for your message!"
  # --subject auto-fills as "Re: <original subject>" — override it if needed

# Managing accounts
python e_mailman.py account --add \
  --name "work" --email "me@company.com" --password "app-pw" \
  --smtp-host smtp.company.com --smtp-port 465 \
  --imap-host imap.company.com --imap-port 993
python e_mailman.py account --remove "work"
```