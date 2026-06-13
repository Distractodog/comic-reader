"""Entry point: launches the comic book reader application."""

import sys
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from PyQt6.QtWidgets import QApplication

from app_info import APP_DISPLAY_NAME, APP_INTERNAL_NAME, APP_ORGANIZATION
from macos_app_name import apply_macos_app_name
from main_window import MainWindow
from themes import DARK, app_stylesheet


def resource_path(relative: str) -> Path:
    """Return a source/dev or PyInstaller onefile resource path."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def app_icon() -> QIcon | None:
    path = resource_path("assets/app-icon.png")
    if path.exists():
        return QIcon(str(path))
    return None


def _activate_macos_app() -> None:
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication  # type: ignore[import-untyped]

        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def main():
    apply_macos_app_name(APP_DISPLAY_NAME)
    app = QApplication(sys.argv)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setApplicationName(APP_INTERNAL_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setStyle("Fusion")

    fonts_dir = resource_path("fonts")
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))

    font = QFont("Libre Baskerville", 11)
    font.setFamilies(["Libre Baskerville", "Georgia", "Times New Roman", "serif"])
    app.setFont(font)
    app.setStyleSheet(app_stylesheet(DARK))

    icon = app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    window = MainWindow()
    if icon is not None:
        window.setWindowIcon(icon)
    window.show()
    window.raise_()
    window.activateWindow()
    _activate_macos_app()

    # If a file path was passed on the command line, open it
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
