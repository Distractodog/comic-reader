"""Entry point: launches the comic book reader application."""

import sys
from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Comic Reader")
    app.setOrganizationName("ComicReader")
    app.setStyle("Fusion")

    fonts_dir = Path(__file__).parent / "fonts"
    for ttf in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))

    font = QFont("Libre Baskerville", 11)
    app.setFont(font)
    app.setStyleSheet(
        "QMainWindow, QWidget { background-color: #0d0d0d; color: #e0e0e0;"
        " font-family: 'Libre Baskerville'; }"
        "QMenuBar { background: #0d0d0d; color: #bbb; border-bottom: 1px solid #1a1a1a; }"
        "QMenuBar::item { padding: 4px 10px; }"
        "QMenuBar::item:selected { background: #1e1e1e; color: #fff; }"
        "QMenu { background: #161616; color: #e0e0e0; border: 1px solid #2a2a2a; }"
        "QMenu::item { padding: 5px 24px; }"
        "QMenu::item:selected { background: #2d2d2d; }"
        "QMenu::separator { height: 1px; background: #2a2a2a; margin: 2px 0; }"
        "QStatusBar { background: #080808; color: #555; border-top: 1px solid #1a1a1a; }"
        "QScrollBar:vertical { background: #0a0a0a; width: 5px; border: none; margin: 0; }"
        "QScrollBar::handle:vertical { background: #2a2a2a; border-radius: 2px; min-height: 30px; }"
        "QScrollBar::handle:vertical:hover { background: #3a3a3a; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }"
        "QToolTip { background: #1a1a1a; color: #e0e0e0; border: 1px solid #333; padding: 4px 6px; }"
        "QPushButton { background: #1e1e1e; color: #ccc; border: 1px solid #2d2d2d;"
        " border-radius: 4px; padding: 6px 14px; }"
        "QPushButton:hover { background: #2a2a2a; color: #fff; border-color: #444; }"
        "QPushButton:pressed { background: #111; }"
        "QLineEdit { background: #111; color: #e0e0e0; border: 1px solid #2d2d2d;"
        " border-radius: 4px; padding: 4px 8px; selection-background-color: #4a9eff; }"
        "QLineEdit:focus { border-color: #4a9eff; }"
        "QComboBox { background: #111; color: #e0e0e0; border: 1px solid #2d2d2d;"
        " border-radius: 4px; padding: 4px 8px; }"
        "QComboBox::drop-down { border: none; width: 20px; }"
        "QComboBox QAbstractItemView { background: #161616; color: #e0e0e0;"
        " selection-background-color: #2d2d2d; border: 1px solid #333; outline: none; }"
        "QProgressDialog QLabel { color: #e0e0e0; }"
        "QMessageBox QLabel { color: #e0e0e0; }"
    )

    window = MainWindow()
    window.show()

    # If a file path was passed on the command line, open it
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
