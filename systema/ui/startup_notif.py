"""
ui/startup_notif.py

Lightweight tkinter notice popups, shown as a separate process so they appear
instantly while the main app is still importing. Two modes:

  (default)                 boot splash while Systema Auxilium initializes
  --already-running <PID>   shown when a second instance is blocked

Optional (already-running mode):
  --console <HWND>          window handle of the terminal that launched the
                            blocked instance; it is closed when the notice is
                            dismissed / times out, so no stray console lingers.

Styling matches the app's GitHub-dark palette. No emojis.
"""

import sys
import tkinter as tk

# ── Palette (Systema Auxilium dark) ──────────────────────────────────────────
BG_ELEV  = "#161b22"
BORDER   = "#232a33"
FG_MAIN  = "#e6edf3"
FG_SUB   = "#8b949e"
FG_FAINT = "#586069"


def _arg(flag):
    if flag in sys.argv:
        try:
            return sys.argv[sys.argv.index(flag) + 1]
        except IndexError:
            return None
    return None


def _close_console(hwnd):
    """Post WM_CLOSE to a Windows console window (no-op if invalid/closed)."""
    if not hwnd:
        return
    try:
        import ctypes
        ctypes.windll.user32.PostMessageW(int(hwnd), 0x0010, 0, 0)  # WM_CLOSE
    except Exception:
        pass


MODE_ALREADY = "--already-running" in sys.argv
ACCENT = "#f85149" if MODE_ALREADY else "#3fb950"
HOVER = "#da3633" if MODE_ALREADY else "#2ea043"
CONSOLE_HWND = _arg("--console") if MODE_ALREADY else None

root = tk.Tk()
root.title("Systema Auxilium")
W, H = (472, 224) if MODE_ALREADY else (432, 202)
root.geometry(f"{W}x{H}+{root.winfo_screenwidth() - W - 24}"
              f"+{root.winfo_screenheight() - H - 56}")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.attributes("-alpha", 0.98)
root.configure(bg=BORDER)

# 1px border + accent left bar + card
outer = tk.Frame(root, bg=BORDER)
outer.pack(fill="both", expand=True, padx=1, pady=1)
tk.Frame(outer, bg=ACCENT, width=4).pack(side="left", fill="y")
card = tk.Frame(outer, bg=BG_ELEV, padx=20, pady=15)
card.pack(side="left", fill="both", expand=True)

# Header
hdr = tk.Frame(card, bg=BG_ELEV)
hdr.pack(fill="x")
_dot = tk.Canvas(hdr, width=9, height=9, bg=BG_ELEV, highlightthickness=0)
_dot.create_oval(0, 0, 9, 9, fill=ACCENT, outline="")
_dot.pack(side="left", padx=(0, 8), pady=2)
tk.Label(hdr, text="SYSTEMA AUXILIUM", bg=BG_ELEV, fg=FG_SUB,
         font=("Segoe UI", 8, "bold")).pack(side="left")
tk.Frame(card, bg=BORDER, height=1).pack(fill="x", pady=(11, 12))

if MODE_ALREADY:
    _pid = _arg("--already-running") or "?"
    tk.Label(card, text="Instance already active", bg=BG_ELEV, fg=FG_MAIN,
             font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
    tk.Label(card, text="Systema Auxilium is already running in the background.",
             bg=BG_ELEV, fg=FG_SUB, font=("Segoe UI", 9), anchor="w",
             justify="left").pack(fill="x", pady=(6, 0))
    tk.Label(card, text=f"Process ID   {_pid}", bg=BG_ELEV, fg=ACCENT,
             font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(4, 0))
    tk.Label(card, text="Use the existing floating window instead of launching again.",
             bg=BG_ELEV, fg=FG_FAINT, font=("Segoe UI", 8), anchor="w",
             justify="left", wraplength=W - 74).pack(fill="x", pady=(3, 0))
    BTN_TEXT, SECS = "Dismiss", 6
else:
    tk.Label(card, text="Starting Systema Auxilium", bg=BG_ELEV, fg=FG_MAIN,
             font=("Segoe UI", 13, "bold"), anchor="w").pack(fill="x")
    tk.Label(card, text="Please wait for the floating window to appear.",
             bg=BG_ELEV, fg=FG_SUB, font=("Segoe UI", 9), anchor="w",
             justify="left").pack(fill="x", pady=(6, 0))
    tk.Label(card, text="A freshly booted device may start slower while the OS settles.",
             bg=BG_ELEV, fg=FG_FAINT, font=("Segoe UI", 8), anchor="w",
             justify="left", wraplength=W - 74).pack(fill="x", pady=(3, 0))
    # Indeterminate progress sweep (a clean bar instead of a spinner glyph).
    _track = tk.Canvas(card, height=3, bg=BORDER, highlightthickness=0)
    _track.pack(fill="x", pady=(13, 0))
    _seg = _track.create_rectangle(0, 0, 0, 3, fill=ACCENT, outline="")

    def _sweep(x=0):
        try:
            w = _track.winfo_width() or (W - 74)
            seg_w = max(46, w // 3)
            pos = (x % (w + seg_w)) - seg_w
            _track.coords(_seg, pos, 0, pos + seg_w, 3)
            _track.after(16, _sweep, x + 6)
        except tk.TclError:
            pass  # window closed

    _sweep()
    BTN_TEXT, SECS = "Close", 5

# Footer: countdown (left) + button (right)
foot = tk.Frame(card, bg=BG_ELEV)
foot.pack(fill="x", pady=(15, 0))
_cd = tk.Label(foot, text=f"Closing in {SECS}s", bg=BG_ELEV, fg=FG_SUB,
               font=("Segoe UI", 8))
_cd.pack(side="left", anchor="s")


def _dismiss():
    if MODE_ALREADY:
        _close_console(CONSOLE_HWND)   # also close the launching terminal
    try:
        root.destroy()
    except tk.TclError:
        pass


_btn = tk.Button(foot, text=f"  {BTN_TEXT}  ", command=_dismiss, bg=ACCENT,
                 fg="#0d1117", font=("Segoe UI", 9, "bold"),
                 activebackground=HOVER, activeforeground="#ffffff",
                 bd=0, padx=16, pady=5, cursor="hand2", relief="flat")
_btn.pack(side="right")
_btn.bind("<Enter>", lambda e: _btn.config(bg=HOVER, fg="#ffffff"))
_btn.bind("<Leave>", lambda e: _btn.config(bg=ACCENT, fg="#0d1117"))

_left = [SECS]


def _tick():
    if _left[0] <= 0:
        _dismiss()
        return
    try:
        _cd.config(text=f"Closing in {_left[0]}s")
    except tk.TclError:
        return
    _left[0] -= 1
    root.after(1000, _tick)


root.after(1000, _tick)

# Drag-to-move (frameless).
def _press(e):
    root._dx, root._dy = e.x, e.y

def _move(e):
    root.geometry(f"+{e.x_root - root._dx}+{e.y_root - root._dy}")

for _w in (card, hdr):
    _w.bind("<Button-1>", _press)
    _w.bind("<B1-Motion>", _move)

root.mainloop()
sys.exit(0)
