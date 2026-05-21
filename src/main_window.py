"""Main application window with menus, navigation, and the page viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QToolBar,
)

from archive_handler import ComicReader, open_comic
from viewer import ComicViewer, FitMode

SUPPORTED_FILTER = (
    "Comic files (*.cbz *.cbr *.cb7 *.cbt *.pdf *.zip *.rar *.7z *.tar);;"
    "CBZ files (*.cbz *.zip);;"
    "CBR files (*.cbr *.rar);;"
    "CB7 files (*.cb7 *.7z);;"
    "CBT files (*.cbt *.tar);;"
    "PDF files (*.pdf);;"
    "All files (*)"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comic Reader")
        self.resize(1000, 1200)

        self._reader: ComicReader | None = None
        self._current_page: int = 0
        self._settings = QSettings("ComicReader", "ComicReader")

        self.viewer = ComicViewer()
        self.setCentralWidget(self.viewer)

        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()

        self.setAcceptDrops(True)
        self._restore_window_state()

    # ----- UI construction -----

    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        open_folder_action = QAction("Open &Folder...", self)
        open_folder_action.setShortcut("Ctrl+Shift+O")
        open_folder_action.triggered.connect(self.open_folder_dialog)
        file_menu.addAction(open_folder_action)

        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&View")

        fit_page = QAction("Fit &Page", self)
        fit_page.setShortcut("1")
        fit_page.triggered.connect(lambda: self.viewer.set_fit_mode(FitMode.FIT_PAGE))
        view_menu.addAction(fit_page)

        fit_width = QAction("Fit &Width", self)
        fit_width.setShortcut("2")
        fit_width.triggered.connect(lambda: self.viewer.set_fit_mode(FitMode.FIT_WIDTH))
        view_menu.addAction(fit_width)

        actual = QAction("&Actual Size", self)
        actual.setShortcut("3")
        actual.triggered.connect(self.viewer.reset_zoom)
        view_menu.addAction(actual)

        view_menu.addSeparator()

        zoom_in = QAction("Zoom &In", self)
        zoom_in.setShortcuts([QKeySequence.StandardKey.ZoomIn, QKeySequence("Ctrl+=")])
        zoom_in.triggered.connect(self.viewer.zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Zoom &Out", self)
        zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out.triggered.connect(self.viewer.zoom_out)
        view_menu.addAction(zoom_out)

        view_menu.addSeparator()
        fullscreen = QAction("&Fullscreen", self)
        fullscreen.setShortcut("F11")
        fullscreen.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen)

        nav_menu = menubar.addMenu("&Navigate")

        next_page = QAction("&Next Page", self)
        next_page.setShortcuts([QKeySequence("Right"), QKeySequence("Space"), QKeySequence("PgDown")])
        next_page.triggered.connect(self.next_page)
        nav_menu.addAction(next_page)

        prev_page = QAction("&Previous Page", self)
        prev_page.setShortcuts([QKeySequence("Left"), QKeySequence("Backspace"), QKeySequence("PgUp")])
        prev_page.triggered.connect(self.prev_page)
        nav_menu.addAction(prev_page)

        first = QAction("&First Page", self)
        first.setShortcut("Home")
        first.triggered.connect(self.first_page)
        nav_menu.addAction(first)

        last = QAction("&Last Page", self)
        last.setShortcut("End")
        last.triggered.connect(self.last_page)
        nav_menu.addAction(last)

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction("Open", self.open_file_dialog)
        toolbar.addSeparator()
        toolbar.addAction("◀ Prev", self.prev_page)
        toolbar.addAction("Next ▶", self.next_page)
        toolbar.addSeparator()
        toolbar.addAction("Fit Page", lambda: self.viewer.set_fit_mode(FitMode.FIT_PAGE))
        toolbar.addAction("Fit Width", lambda: self.viewer.set_fit_mode(FitMode.FIT_WIDTH))

    def _build_statusbar(self):
        self._page_label = QLabel("No file loaded")
        sb = QStatusBar()
        sb.addPermanentWidget(self._page_label)
        self.setStatusBar(sb)

    # ----- File loading -----

    def open_file_dialog(self):
        last_dir = self._settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Comic", last_dir, SUPPORTED_FILTER
        )
        if path:
            self.load_file(path)

    def open_folder_dialog(self):
        last_dir = self._settings.value("last_dir", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Open Comic Folder", last_dir)
        if path:
            self.load_file(path)

    def load_file(self, path: str):
        try:
            if self._reader:
                self._reader.close()
            self._reader = open_comic(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
            return

        if self._reader.page_count() == 0:
            QMessageBox.warning(self, "Empty", "No pages found in this file.")
            return

        self._settings.setValue("last_dir", str(Path(path).parent))
        self.setWindowTitle(f"Comic Reader — {Path(path).name}")
        self._current_page = 0
        self._show_current_page()

    # ----- Page navigation -----

    def _show_current_page(self):
        if not self._reader:
            return
        try:
            data = self._reader.get_page_bytes(self._current_page)
            self.viewer.set_image(data)
            self._page_label.setText(
                f"Page {self._current_page + 1} / {self._reader.page_count()}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load page:\n{e}")

    def next_page(self):
        if self._reader and self._current_page < self._reader.page_count() - 1:
            self._current_page += 1
            self._show_current_page()

    def prev_page(self):
        if self._reader and self._current_page > 0:
            self._current_page -= 1
            self._show_current_page()

    def first_page(self):
        if self._reader:
            self._current_page = 0
            self._show_current_page()

    def last_page(self):
        if self._reader:
            self._current_page = self._reader.page_count() - 1
            self._show_current_page()

    # ----- Window helpers -----

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _restore_window_state(self):
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)

    # ----- Drag and drop -----

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_file(urls[0].toLocalFile())

    def closeEvent(self, event):
        self._settings.setValue("geometry", self.saveGeometry())
        if self._reader:
            self._reader.close()
        super().closeEvent(event)
