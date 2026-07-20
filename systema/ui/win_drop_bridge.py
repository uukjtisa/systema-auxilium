"""
systema/ui/win_drop_bridge.py

Windows-elevation drag-and-drop fix.

When the app runs ELEVATED on Windows, UIPI (User Interface Privilege
Isolation) silently blocks drag-and-drop from a non-elevated Explorer: every
drag shows the forbidden cursor before any Qt code runs, so no Qt-side fix can
help. The documented, memory-safe remedy is to open the window's UIPI message
filter for the messages the shell's OLE drag-drop uses to move data into a
higher-integrity process. Qt's own IDropTarget then works and its normal
dragEnterEvent/dropEvent fire — we do NOT touch Qt's drop target.

An earlier revision revoked Qt's OLE target and handled WM_DROPFILES by hand
(RevokeDragDrop + a raw-vtable AddRef); doing that during the startup show()
corrupted Qt's drop-site state and crashed the app with an access violation.
The message-filter approach needs none of that.

Cross-OS rule: everything here is a hard no-op on non-Windows platforms AND on
non-elevated Windows runs — both keep the plain Qt drag path unchanged.
"""
import sys

from systema.common.logger import _make_logger

log = _make_logger("DropBridge")

# Messages the shell uses to hand a drag payload to a higher-integrity window.
# WM_DROPFILES + WM_COPYDATA + the undocumented WM_COPYGLOBALDATA (0x0049) that
# actually carries the dragged data across the boundary.
_ALLOWED_MESSAGES = (0x0233, 0x004A, 0x0049)
_MSGFLT_ALLOW = 1


def is_elevated() -> bool:
    """True only on Windows when the process runs with admin rights."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def install(window) -> bool:
    """Open the UIPI message filter on a SHOWN top-level widget so drags from a
    non-elevated Explorer reach Qt's drop handling.

    Returns True when the filter was opened (elevated Windows); False means the
    normal Qt drag path is already fully in charge (other OSes / non-elevated).
    Purely additive and idempotent — it only widens a per-window message
    filter and never touches Qt state, so it is safe to call on every show and
    can never corrupt the window or crash the app.
    """
    if not is_elevated():
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt

        hwnd = wt.HWND(int(window.winId()))
        user32 = ctypes.windll.user32
        # ChangeWindowMessageFilterEx(hwnd, msg, action, pChangeFilterStruct)
        user32.ChangeWindowMessageFilterEx.argtypes = (
            wt.HWND, wt.UINT, wt.DWORD, ctypes.c_void_p)
        user32.ChangeWindowMessageFilterEx.restype = wt.BOOL
        for msg in _ALLOWED_MESSAGES:
            user32.ChangeWindowMessageFilterEx(hwnd, msg, _MSGFLT_ALLOW, None)
        log.info(f"[install] UIPI drag-drop filter opened | hwnd={int(window.winId())}")
        return True
    except Exception:
        log.warning("[install] could not open the UIPI message filter", exc_info=True)
        return False
