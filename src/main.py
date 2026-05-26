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
        "QMainWindow, QWidget { background-color: #f0e8e8; color: #2a1818;"
        " font-family: 'Libre Baskerville'; }"
        "QMenuBar { background: #ecdede; color: #2a1818; border-bottom: 1px solid #c4aeae; }"
        "QMenuBar::item { padding: 4px 10px; }"
        "QMenuBar::item:selected { background: #d8cccc; color: #2a1818; }"
        "QMenu { background: #f0e8e8; color: #2a1818; border: 1px solid #c4aeae; }"
        "QMenu::item { padding: 5px 24px; }"
        "QMenu::item:selected { background: #ddd0d0; }"
        "QMenu::separator { height: 1px; background: #c4aeae; margin: 2px 0; }"
        "QStatusBar { background: #e4d8d8; color: #7a5858; border-top: 1px solid #c4aeae; }"
        "QScrollBar:vertical { background: #e4d8d8; width: 5px; border: none; margin: 0; }"
        "QScrollBar::handle:vertical { background: #c4aeae; border-radius: 2px; min-height: 30px; }"
        "QScrollBar::handle:vertical:hover { background: #b0a0a0; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }"
        "QToolTip { background: #f0e8e8; color: #2a1818; border: 1px solid #c4aeae; padding: 4px 6px; }"
        "QPushButton { background: #e4d8d8; color: #2a1818; border: 1px solid #c4aeae;"
        " border-radius: 4px; padding: 6px 14px; }"
        "QPushButton:hover { background: #d8cccc; color: #2a1818; border-color: #b0a0a0; }"
        "QPushButton:pressed { background: #c4aeae; }"
        "QLineEdit { background: #faf5f5; color: #2a1818; border: 1px solid #c4aeae;"
        " border-radius: 4px; padding: 4px 8px; selection-background-color: #8b2a2a; }"
        "QLineEdit:focus { border-color: #8b2a2a; }"
        "QComboBox { background: #faf5f5; color: #2a1818; border: 1px solid #c4aeae;"
        " border-radius: 4px; padding: 4px 8px; }"
        "QComboBox::drop-down { border: none; width: 20px; }"
        "QComboBox QAbstractItemView { background: #f0e8e8; color: #2a1818;"
        " selection-background-color: #ddd0d0; border: 1px solid #c4aeae; outline: none; }"
        "QProgressDialog QLabel { color: #2a1818; }"
        "QMessageBox QLabel { color: #2a1818; }"
    )

    window = MainWindow()
    window.show()

    # If a file path was passed on the command line, open it
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
