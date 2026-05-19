"""
notif.py
Systema Auxilium — Fire-and-forget desktop notification popup.

Usage:
    python notif.py --title "Hello" --body "Something happened." --closing-time 10
                    --theme modern --close-button-text "Dismiss"

Arguments:
    --title             Notification title text (top bar)
    --body              Main message body text
    --closing-time      Seconds before auto-close (default: 10)
    --theme             Visual theme: modern | girly-pinkish | flower-girl | brutalist-darkmode
    --close-button-text Text shown on the close button (default: "Close")
"""

import argparse
import tkinter as tk

# ─── Themes ──────────────────────────────────────────────────────────────────

THEMES = {
    "modern": {
        "bg":        "#0d1117",
        "bg_card":   "#161b22",
        "border":    "#30363d",
        "fg_main":   "#e6edf3",
        "fg_sub":    "#8b949e",
        "fg_dim":    "#484f58",
        "accent":    "#58a6ff",
        "btn_hover": "#1f6feb",
        "btn_fg":    "#0d1117",
        "dot_color": "#3fb950",
        "dots_idle": "#30363d",
    },
    "brutalist-darkmode": {
        "bg":        "#000000",
        "bg_card":   "#0a0a0a",
        "border":    "#ffffff",
        "fg_main":   "#ffffff",
        "fg_sub":    "#aaaaaa",
        "fg_dim":    "#555555",
        "accent":    "#ffffff",
        "btn_hover": "#dddddd",
        "btn_fg":    "#000000",
        "dot_color": "#ffffff",
        "dots_idle": "#333333",
    },
    "girly-pinkish": {
        "bg":        "#1a0a12",
        "bg_card":   "#2d1020",
        "border":    "#c2185b",
        "fg_main":   "#fce4ec",
        "fg_sub":    "#f48fb1",
        "fg_dim":    "#ad1457",
        "accent":    "#f06292",
        "btn_hover": "#e91e63",
        "btn_fg":    "#1a0a12",
        "dot_color": "#f06292",
        "dots_idle": "#4a1530",
    },
    "flower-girl": {
        "bg":        "#fdf6f0",
        "bg_card":   "#fff9f5",
        "border":    "#f8bbd0",
        "fg_main":   "#4a1942",
        "fg_sub":    "#ad6b8d",
        "fg_dim":    "#c9a0b4",
        "accent":    "#e91e8c",
        "btn_hover": "#c2185b",
        "btn_fg":    "#ffffff",
        "dot_color": "#e91e8c",
        "dots_idle": "#f8bbd0",
    },
}

# ─── Animation state ─────────────────────────────────────────────────────────

SPINNER_FRAMES  = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
ELLIPSIS_FRAMES = [".  ", ".. ", "..."]
DOT_FRAMES      = ["●  ○  ○", "○  ●  ○", "○  ○  ●", "○  ●  ○"]

# ─── Helpers ─────────────────────────────────────────────────────────────────

def close_window(root):
    root.destroy()


def update_countdown(root, label, seconds_left):
    if seconds_left <= 0:
        close_window(root)
        return
    label.config(text=f"Notification closing in {seconds_left}s")
    root.after(1000, update_countdown, root, label, seconds_left - 1)


def animate_spinner(label, title_text, frame_index=0):
    spinner  = SPINNER_FRAMES[frame_index % len(SPINNER_FRAMES)]
    ellipsis = ELLIPSIS_FRAMES[(frame_index // 4) % len(ELLIPSIS_FRAMES)]
    label.config(text=f"{spinner}  {title_text}{ellipsis}")
    label.after(80, animate_spinner, label, title_text, frame_index + 1)


def animate_dots(label, frame_index=0):
    label.config(text=DOT_FRAMES[frame_index % len(DOT_FRAMES)])
    label.after(400, animate_dots, label, frame_index + 1)

# ─── Build UI ────────────────────────────────────────────────────────────────

def build_notification(title, body, closing_time, theme_name, close_btn_text):
    t = THEMES.get(theme_name, THEMES["modern"])

    root = tk.Tk()
    root.title("Systema Auxilium")
    root.geometry("420x220+{x}+{y}".format(
        x=root.winfo_screenwidth()  - 440,
        y=root.winfo_screenheight() - 270,
    ))
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.97)
    root.configure(bg=t["bg"])

    # Outer border
    outer = tk.Frame(root, bg=t["border"], padx=1, pady=1)
    outer.pack(fill="both", expand=True)

    frame = tk.Frame(outer, bg=t["bg_card"], padx=20, pady=16)
    frame.pack(fill="both", expand=True)

    # ── Top bar ──────────────────────────────────────────────────
    top_bar = tk.Frame(frame, bg=t["bg_card"])
    top_bar.pack(fill="x", pady=(0, 10))

    dot_canvas = tk.Canvas(top_bar, width=10, height=10, bg=t["bg_card"],
                           highlightthickness=0)
    dot_canvas.create_oval(1, 1, 9, 9, fill=t["dot_color"], outline="")
    dot_canvas.pack(side="left", padx=(0, 7), pady=2)

    tk.Label(top_bar, text="SYSTEMA AUXILIUM", bg=t["bg_card"], fg=t["fg_sub"],
             font=("Segoe UI", 8, "bold")).pack(side="left")

    dots_label = tk.Label(top_bar, text="●  ●  ●", bg=t["bg_card"],
                          fg=t["dots_idle"], font=("Segoe UI", 8))
    dots_label.pack(side="right")
    animate_dots(dots_label)

    # ── Divider ──────────────────────────────────────────────────
    tk.Frame(frame, bg=t["border"], height=1).pack(fill="x", pady=(0, 12))

    # ── Title (animated spinner) ─────────────────────────────────
    msg_label = tk.Label(frame, text=title, bg=t["bg_card"], fg=t["fg_main"],
                         font=("Segoe UI", 13, "bold"),
                         justify="left", anchor="w")
    msg_label.pack(fill="x")
    animate_spinner(msg_label, title)

    # ── Body ─────────────────────────────────────────────────────
    tk.Label(frame, text=body, bg=t["bg_card"], fg=t["fg_sub"],
             font=("Segoe UI", 9), justify="left", anchor="w",
             wraplength=380).pack(fill="x", pady=(4, 0))

    # ── Bottom row: countdown + close button ─────────────────────
    bottom = tk.Frame(frame, bg=t["bg_card"])
    bottom.pack(fill="x", pady=(14, 0))

    countdown_label = tk.Label(bottom,
                               text=f"Notification closing in {closing_time}s",
                               bg=t["bg_card"], fg=t["fg_dim"],
                               font=("Segoe UI", 8))
    countdown_label.pack(side="left", anchor="s")

    btn = tk.Button(bottom, text=f"  {close_btn_text}  ",
                    command=lambda: close_window(root),
                    bg=t["accent"], fg=t["btn_fg"],
                    font=("Segoe UI", 9, "bold"),
                    activebackground=t["btn_hover"], activeforeground="#ffffff",
                    bd=0, padx=14, pady=5,
                    cursor="hand2", relief="flat")
    btn.pack(side="right")

    btn.bind("<Enter>", lambda e: btn.config(bg=t["btn_hover"], fg="#ffffff"))
    btn.bind("<Leave>", lambda e: btn.config(bg=t["accent"],    fg=t["btn_fg"]))

    # ── Start countdown ──────────────────────────────────────────
    update_countdown(root, countdown_label, closing_time)

    root.mainloop()

# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Systema Auxilium — Desktop notification popup"
    )
    parser.add_argument("--title",             default="Systema Auxilium",
                        help="Notification title")
    parser.add_argument("--body",              default="",
                        help="Notification body text")
    parser.add_argument("--closing-time",      type=int, default=10,
                        dest="closing_time",
                        help="Seconds before auto-close (default: 10)")
    parser.add_argument("--theme",             default="modern",
                        choices=list(THEMES.keys()),
                        help="Visual theme")
    parser.add_argument("--close-button-text", default="Close",
                        dest="close_button_text",
                        help="Text on the close button")

    args = parser.parse_args()

    build_notification(
        title        = args.title,
        body         = args.body,
        closing_time = args.closing_time,
        theme_name   = args.theme,
        close_btn_text = args.close_button_text,
    )


if __name__ == "__main__":
    main()
