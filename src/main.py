"""Entry point: launches the comic book reader application."""

import sys
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from main_window import MainWindow
from themes import DARK, app_stylesheet


def resource_path(relative: str) -> Path:
    """Return a source/dev or PyInstaller onefile resource path."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Comic Reader")
    app.setOrganizationName("ComicReader")
    app.setStyle("Fusion")

    fonts_dir = resource_path("fonts")
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))

    font = QFont("Libre Baskerville", 11)
    app.setFont(font)
    app.setStyleSheet(app_stylesheet(DARK))

    window = MainWindow()
    window.show()

    # If a file path was passed on the command line, open it
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
