---
name: notif-system
description: >
  Fire-and-forget desktop notification popups.
---

# notif-system

Send a themed desktop notification popup to the user. Fire-and-forget —
the agent launches it and moves on immediately. The window auto-closes after
a countdown and the user can dismiss it early.

---

## Script location

notif-system/
├── SKILL.md
└── scripts/
	└── notif.py

---

## How to fire a notification

Use `subprocess.Popen` — **never** `.wait()` or `.run()`. That keeps it
fire-and-forget so the agent doesn't block.

```python
import subprocess, sys

subprocess.Popen(
    [
        sys.executable, "scripts/notif.py",
        "--title",             "Task Complete",
        "--body",              "Your export finished successfully.",
        "--closing-time",      "8",
        "--theme",             "modern",
        "--close-button-text", "Got it",
    ],
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)
```

---

## Arguments

| Argument              | Required | Default              | Notes                              |
|-----------------------|----------|----------------------|------------------------------------|
| `--title`             | no       | `Systema Auxilium`   | Shown as animated spinner heading  |
| `--body`              | no       | *(empty)*            | Sub-text below the title           |
| `--closing-time`      | no       | `10`                 | Seconds until auto-close           |
| `--theme`             | no       | `modern`             | See themes below                   |
| `--close-button-text` | no       | `Close`              | Label on the dismiss button        |

---

## Themes

| Theme name          | Look & feel                                      |
|---------------------|--------------------------------------------------|
| `modern`            | GitHub-dark inspired. Dark navy, blue accent.    |
| `brutalist-darkmode`| Pure black + white. High contrast, no softness.  |
| `girly-pinkish`     | Deep dark rose, hot pink accents.                |
| `flower-girl`       | Warm cream background, magenta/purple accents.   |

---

## When to use each theme

- **Errors / warnings** → `brutalist-darkmode`
- **General system status** → `modern`
- **Casual / personal reminders** → `girly-pinkish` or `flower-girl`
- **Default / unsure** → `modern`

---

## Quick-reference examples

```bash
# Minimal
python scripts/notif.py --title "Done" --body "File saved."

# Custom theme + duration
python scripts/notif.py \
  --title "Reminder" \
  --body "Stand up and stretch!" \
  --closing-time 15 \
  --theme girly-pinkish \
  --close-button-text "Will do!"

# Error alert
python scripts/notif.py \
  --title "Error" \
  --body "Connection to API failed. Check your key." \
  --closing-time 20 \
  --theme brutalist-darkmode \
  --close-button-text "Dismiss"
```

---

## Notes

- The title text animates with a braille spinner and cycling ellipsis dots.
- The top-bar dots animate independently at a slower pace.
- The window is always-on-top and positioned above the Windows taskbar.
- No extra dependencies — stdlib only (`tkinter`, `argparse`).
- Safe to call multiple times concurrently; each call spawns its own process.