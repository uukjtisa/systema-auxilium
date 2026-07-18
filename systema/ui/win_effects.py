"""
systema/ui/win_effects.py
Windows-only window composition effects (DWM blur-behind / acrylic).

Used by the chat window's glass mode: with the 'blend' bubble style the AI
text has no opaque box, so the desktop behind the translucent window must be
heavily blurred to keep text readable. Everything here is best-effort — every
entry point returns False (never raises) on non-Windows platforms or when the
undocumented SetWindowCompositionAttribute API is unavailable, and callers
fall back to the smoked rgba text panels alone (e.g. on Kali).
"""
import ctypes
import sys

_ACCENT_DISABLED             = 0
_ACCENT_ENABLE_BLURBEHIND    = 3   # classic blur (cheaper, Win10+)
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4   # acrylic (stronger blur + noise, Win10 1803+)
_WCA_ACCENT_POLICY           = 19

# AABBGGRR gradient tint required for acrylic to render; ~25% black keeps the
# frosting neutral — the app's own overlays provide the actual darkening.
_ACRYLIC_TINT = 0x40000000


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState",   ctypes.c_int),
        ("AccentFlags",   ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId",   ctypes.c_int),
    ]


class _WinCompAttrData(ctypes.Structure):
    _fields_ = [
        ("Attribute",  ctypes.c_int),
        ("Data",       ctypes.POINTER(_AccentPolicy)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _set_accent(hwnd: int, state: int, gradient: int = 0) -> bool:
    if sys.platform != "win32":
        return False
    try:
        accent = _AccentPolicy()
        accent.AccentState = state
        accent.AccentFlags = 2 if state != _ACCENT_DISABLED else 0
        accent.GradientColor = gradient
        data = _WinCompAttrData()
        data.Attribute = _WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WinCompAttrData)]
        res = fn(ctypes.c_void_p(hwnd), ctypes.byref(data))
        return bool(res)
    except Exception:
        return False


def enable_blur_behind(hwnd: int) -> bool:
    """Frost the desktop behind the window: acrylic first, classic blur as a
    fallback (some Windows builds throttle acrylic on frameless windows)."""
    if _set_accent(hwnd, _ACCENT_ENABLE_ACRYLICBLURBEHIND, _ACRYLIC_TINT):
        return True
    return _set_accent(hwnd, _ACCENT_ENABLE_BLURBEHIND)


def disable_blur_behind(hwnd: int) -> bool:
    """Remove the blur-behind accent (back to plain translucency)."""
    return _set_accent(hwnd, _ACCENT_DISABLED)
