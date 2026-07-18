"""
systema/ui/chat/constants.py
Animation / sidebar timing constants shared by ChatWindow and its mixins.
Moved verbatim from chat_window.py (full-split pass, 2026-07-17).
"""

# ═══════════════════════════════════════════════════════════════════════════════
# ANIMATION TIMING CONSTANTS
# Tweak these values to adjust the feel of every animation in the chat window.
# ═══════════════════════════════════════════════════════════════════════════════

# --- Window ---
ANIM_WINDOW_FADE_IN_MS       = 340    # Chat window fade-in when shown (ms)

# --- Sidebar ---
ANIM_SIDEBAR_SLIDE_MS        = 360    # Sidebar slide in / out (ms)
SIDEBAR_DEFAULT_W            = 290    # Default sidebar width (px) — wide enough for hero + pills
SIDEBAR_MIN_W                = 280    # Minimum — wide enough for 3 hero pills without clipping
SIDEBAR_MAX_W                = 420    # Maximum sidebar width when dragging
SIDEBAR_CLOSE_TRIGGER_W      = 200    # Drag the handle this far past the minimum → auto-close

# --- Messages ---
ANIM_MSG_IN_HEIGHT_MS        = 480    # Message pop-in: height expand (ms)
ANIM_MSG_IN_FADE_MS          = 380    # Message pop-in: fade-in (ms)
ANIM_MSG_IN_OVERSHOOT_PX     = 120    # Extra pixels past natural height (OutBack spring feel)
ANIM_MSG_OUT_FADE_MS         = 220    # Message pop-out: fade-out (ms)
ANIM_MSG_OUT_HEIGHT_MS       = 280    # Message pop-out: height collapse (ms)

# --- Scroll (animated jumps, e.g. scroll-to-new-message) ---
ANIM_SCROLL_MIN_MS           = 180    # Shortest animated scroll duration (ms)
ANIM_SCROLL_MAX_MS           = 600    # Longest animated scroll duration (ms)

# --- Inertia scroll (mouse-wheel / trackpad momentum) ---
ANIM_INERTIA_INTERVAL_MS     = 14     # Tick interval (~70 fps)
ANIM_INERTIA_FRICTION        = 0.86   # Velocity multiplier per tick (lower = snappier stop)
ANIM_INERTIA_MIN_VELOCITY    = 0.5    # Stop threshold (px / tick)
ANIM_INERTIA_SCALE           = 0.38   # Wheel angleDelta → velocity scale
ANIM_INERTIA_MAX_VELOCITY    = 1400   # Max speed cap (px / tick)

# --- UI feedback timers ---
ANIM_COPY_FEEDBACK_MS        = 1500   # "✓ Copied!" button state duration (ms)
ANIM_STATUS_CLEAR_MS         = 2000   # Status-bar message clear delay (ms)
