"""Shared app branding — display name vs internal ids used for data paths."""

# Shown in the window title, dock (when launched as .app), and menu bar.
APP_DISPLAY_NAME = "Cover 2.0"

# Internal Qt application name — keep stable so AppData/library.db paths don't move.
APP_INTERNAL_NAME = "Comic Reader"
APP_ORGANIZATION = "ComicReader"

# User-facing version + project links (shown on the Settings → About panel).
APP_VERSION = "1.0.0"
APP_REPO_URL = "https://github.com/Distractodog/comic-reader"


def app_settings():
    """Shared QSettings — stable path shared by dev runs and packaged builds.

    Uses the short app id ``ComicReader`` (not the display/internal name
    ``Comic Reader``) so backgrounds and other prefs land in one plist:
    ``com.comicreader.ComicReader.plist``.
    """
    from PyQt6.QtCore import QSettings

    return QSettings(APP_ORGANIZATION, "ComicReader")
