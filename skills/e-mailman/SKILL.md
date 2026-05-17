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
| `account_notes.json` | Per-account notes for deciding which account to use |

---

## STEP 0 — Read notes first. Always.

```bash
python e_mailman.py notes
```

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

## READ

```bash
python e_mailman.py --account "gmail" read --latest [--count N]
python e_mailman.py --account "gmail" read --from "addr"
python e_mailman.py --account "gmail" read --search "keyword"
```

No confirmation needed — run and summarize.

---

## SEND

Show draft first, get **yes / edit / cancel**.

```
📨 Draft
From    : sender@example.com
To      : recipient@example.com
Subject : ...
Body    : ...
Attachments: none
Send? (yes / edit / cancel)
```

On yes:

```bash
python e_mailman.py --account "disroot" send \
  --to "addr" --subject "..." --body "..." \
  [--attachments f1 f2]
```

---

## NOTES commands

```bash
python e_mailman.py notes                           # show all
python e_mailman.py notes --set "account" "note"    # replace entirely
python e_mailman.py notes --add "account" "note"    # append a line
```

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
# Account selection
python e_mailman.py notes                                         # check notes → decide account
python e_mailman.py notes --set "gmail" "personal, day-to-day"  # set a note
python e_mailman.py notes --add "gmail" "also used for Drive"   # append to a note
python e_mailman.py account --list                               # show all accounts

# Reading
python e_mailman.py --account gmail read --latest                # last 5 emails
python e_mailman.py --account gmail read --latest --count 10     # last N emails
python e_mailman.py --account gmail read --from "boss@co.com"   # filter by sender
python e_mailman.py --account gmail read --search "invoice"      # filter by subject

# Sending  (always show draft + confirm first)
python e_mailman.py --account disroot send \
  --to "user@example.com" --subject "Hello" --body "Hi there"
python e_mailman.py --account disroot send \
  --to "user@example.com" --subject "Files" --body "See attached" \
  --attachments report.pdf photo.jpg

# Managing accounts
python e_mailman.py account --add \
  --name "work" --email "me@company.com" --password "app-pw" \
  --smtp-host smtp.company.com --smtp-port 465 \
  --imap-host imap.company.com --imap-port 993
python e_mailman.py account --remove "work"
```