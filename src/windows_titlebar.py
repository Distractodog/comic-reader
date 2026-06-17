"""Force a dark/black Windows title bar via the DWM API.

No-op on non-Windows platforms and on Windows builds too old to support the
attributes (calls fail silently). Call ``apply_dark_titlebar(window)`` once the
window has a native handle (i.e. after ``show()``).
"""

from __future__ import annotations

import sys

# DWM window attributes (dwmapi.h).
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20      # 19 on Windows 10 builds < 19041
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_DWMWA_CAPTION_COLOR = 35                # Windows 11 build 22000+; COLORREF 0x00BBGGRR
_BLACK = 0x00000000


def apply_dark_titlebar(widget) -> None:
    """Make the window's title bar as black as the OS allows. Safe to call always."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi

        # Enable immersive dark mode (dark window chrome). Try the modern
        # attribute id first, fall back to the older Win10 id.
        enabled = ctypes.c_int(1)
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE,
                     _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if dwm.DwmSetWindowAttribute(
                hwnd, ctypes.c_uint(attr),
                ctypes.byref(enabled), ctypes.sizeof(enabled),
            ) == 0:
                break

        # Paint the caption pure black where supported (Windows 11 22000+).
        color = ctypes.c_uint(_BLACK)
        dwm.DwmSetWindowAttribute(
            hwnd, ctypes.c_uint(_DWMWA_CAPTION_COLOR),
            ctypes.byref(color), ctypes.sizeof(color),
        )
    except Exception:
        # Older Windows / missing dwmapi: leave the default chrome.
        pass
