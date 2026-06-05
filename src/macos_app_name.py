"""Best-effort macOS process name for menu bar, Dock, and Stage Manager."""

from __future__ import annotations

import sys


def apply_macos_app_name(name: str) -> None:
    """Show *name* instead of the generic Python label on macOS."""
    if sys.platform != "darwin":
        return

    if sys.argv:
        sys.argv[0] = name

    try:
        import ctypes

        libc = ctypes.CDLL("libc.dylib")
        libc.setprogname(name.encode("utf-8"))
    except Exception:
        pass

    try:
        from Foundation import NSBundle  # type: ignore[import-untyped]

        bundle = NSBundle.mainBundle()
        if bundle is None:
            return

        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is None:
            return

        current = info.get("CFBundleName", "")
        if current in ("Python", "python", "Python Launcher"):
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
    except Exception:
        pass
