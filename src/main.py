"""Entry point: launches the comic book reader application."""

import sys

from PyQt6.QtWidgets import QApplication

from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Comic Reader")
    app.setOrganizationName("ComicReader")

    window = MainWindow()
    window.show()

    # If a file path was passed on the command line, open it
    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
