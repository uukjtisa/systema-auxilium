"""
ui/startup_notif.py
"""
import tkinter as tk

def close_window(root):
    root.destroy()

def update_countdown(root, label, seconds_left):
    if seconds_left <= 0:
        close_window(root)
        return
    label.config(text=f"Notification Closing in {seconds_left}s")
    root.after(1000, update_countdown, root, label, seconds_left - 1)

root = tk.Tk()
root.title("Systema Auxilium")
root.geometry("420x210+{x}+{y}".format(
    x=root.winfo_screenwidth() - 440,
    y=root.winfo_screenheight() - 260
))
root.overrideredirect(True)
root.attributes('-topmost', True)
root.attributes('-alpha', 0.97)

CLOSE_AFTER_SECONDS = 10  # ← change this number to adjust how long the notification stays

bg        = "#0d1117"
bg_card   = "#161b22"
border    = "#30363d"
fg_main   = "#e6edf3"
fg_sub    = "#8b949e"
accent    = "#58a6ff"
btn_hover = "#1f6feb"

root.configure(bg=bg)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

def animate_spinner(label, frame_index=0):
    spinner = SPINNER_FRAMES[frame_index % len(SPINNER_FRAMES)]
    label.config(text=f"{spinner}  Initializing Systema Auxilium")
    label.after(80, animate_spinner, label, frame_index + 1)

outer = tk.Frame(root, bg=border, padx=1, pady=1)
outer.pack(fill="both", expand=True)

frame = tk.Frame(outer, bg=bg_card, padx=20, pady=16)
frame.pack(fill="both", expand=True)

# ── Top bar ──────────────────────────────────────────────────────
top_bar = tk.Frame(frame, bg=bg_card)
top_bar.pack(fill="x", pady=(0, 10))

dot_canvas = tk.Canvas(top_bar, width=10, height=10, bg=bg_card,
                        highlightthickness=0)
dot_canvas.create_oval(1, 1, 9, 9, fill="#3fb950", outline="")
dot_canvas.pack(side="left", padx=(0, 7), pady=2)

tk.Label(top_bar, text="SYSTEMA AUXILIUM", bg=bg_card, fg=fg_sub,
         font=("Segoe UI", 8, "bold")).pack(side="left")


# ── Divider ──────────────────────────────────────────────────────
tk.Frame(frame, bg=border, height=1).pack(fill="x", pady=(0, 12))

# ── Main message ─────────────────────────────────────────────────
msg_label = tk.Label(frame, text="Initializing Systema Auxilium", bg=bg_card, fg=fg_main,
                     font=("Segoe UI", 13, "bold"),
                     justify="left", anchor="w")
msg_label.pack(fill="x")
animate_spinner(msg_label)

tk.Label(frame, text="Please wait for the floating window to pop up.",
         bg=bg_card, fg=fg_sub, font=("Segoe UI", 9),
         justify="left", anchor="w").pack(fill="x", pady=(4, 0))

tk.Label(frame, text="Startup on a newly booted device may be slower\ndue to cold boot lag while Operating System "
                     "stabilizes.",
         bg=bg_card, fg="#484f58", font=("Segoe UI", 8),
         justify="left", anchor="w").pack(fill="x", pady=(2, 0))

# ── Bottom row: countdown left, close button right ───────────────
bottom = tk.Frame(frame, bg=bg_card)
bottom.pack(fill="x", pady=(14, 0))

countdown_label = tk.Label(bottom, text=f"Notification Closing in {CLOSE_AFTER_SECONDS}s", bg=bg_card, fg=fg_sub,
                            font=("Segoe UI", 8))
countdown_label.pack(side="left", anchor="s")

btn = tk.Button(bottom, text="  Close  ",
                command=lambda: close_window(root),
                bg=accent, fg="#0d1117",
                font=("Segoe UI", 9, "bold"),
                activebackground=btn_hover, activeforeground="#ffffff",
                bd=0, padx=14, pady=5,
                cursor="hand2", relief="flat")
btn.pack(side="right")

btn.bind("<Enter>", lambda e: btn.config(bg=btn_hover, fg="#ffffff"))
btn.bind("<Leave>", lambda e: btn.config(bg=accent,    fg="#0d1117"))

# ── Start countdown ──────────────────────────────────────────────
update_countdown(root, countdown_label, CLOSE_AFTER_SECONDS)

root.mainloop()