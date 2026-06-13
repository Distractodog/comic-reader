"""Shared app branding — display name vs internal ids used for data paths."""

# Shown in the window title, dock (when launched as .app), and menu bar.
APP_DISPLAY_NAME = "Cover 2.0"

# Internal Qt application name — keep stable so AppData/library.db paths don't move.
APP_INTERNAL_NAME = "Comic Reader"
APP_ORGANIZATION = "ComicReader"


def app_settings():
    """Shared QSettings — stable path shared by dev runs and packaged builds.

    Uses the short app id ``ComicReader`` (not the display/internal name
    ``Comic Reader``) so backgrounds and other prefs land in one plist:
    ``com.comicreader.ComicReader.plist``.
    """
    from PyQt6.QtCore import QSettings

    return QSettings(APP_ORGANIZATION, "ComicReader")
