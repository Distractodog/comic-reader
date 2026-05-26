"""Main application window with menus, navigation, and the page viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QSettings, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

class _ReaderBar(QWidget):
    """Top bar shown while reading — back button + comic title."""
    back_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReaderBar")
        self.setFixedHeight(48)
        self.setStyleSheet(
            "#ReaderBar { background: #1e1e1e; border-bottom: 2px solid #404040; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        btn = QPushButton("←")
        btn.setFlat(True)
        btn.setStyleSheet("color: #4a9eff; border: none; padding: 4px 8px;")
        btn.clicked.connect(self.back_clicked)
        layout.addWidget(btn)

        self._title = QLabel()
        self._title.setStyleSheet("color: #888888;")
        layout.addWidget(self._title)
        layout.addStretch()
        self.hide()

    def set_title(self, title: str):
        self._title.setText(title)


from archive_handler import ComicReader, open_comic
from bookshelf import BookshelfView
from library import Library
from library_scanner import LibraryScanner
from viewer import ComicViewer, FitMode, SeekBar

SUPPORTED_FILTERS = [
    "Comic files (*.cbz *.cbr *.cb7 *.cbt *.pdf *.zip *.rar *.7z *.tar)",
    "CBZ files (*.cbz *.zip)",
    "CBR files (*.cbr *.rar)",
    "CB7 files (*.cb7 *.7z)",
    "CBT files (*.cbt *.tar)",
    "PDF files (*.pdf)",
    "All files (*)",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Comic Reader")
        self.resize(1100, 1000)

        self._reader: ComicReader | None = None
        self._current_page: int = 0
        self._current_comic_id: int | None = None
        self._settings = QSettings("ComicReader", "ComicReader")
        self._library = Library()
        self._scan_thread: QThread | None = None
        self._scanner: LibraryScanner | None = None
        self._scan_progress: QProgressDialog | None = None

        self._stack = QStackedWidget()
        self._bookshelf = BookshelfView(self._library)
        self.viewer = ComicViewer()
        self._stack.addWidget(self._bookshelf)  # index 0
        self._stack.addWidget(self.viewer)       # index 1

        self._seek_bar = SeekBar()
        self._seek_bar.setVisible(False)

        self._reader_bar = _ReaderBar()
        self._reader_bar.back_clicked.connect(self._back_to_library)

        self._bar_timer = QTimer(self)
        self._bar_timer.setSingleShot(True)
        self._bar_timer.setInterval(2500)
        self._bar_timer.timeout.connect(self._hide_reader_bar_fullscreen)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._reader_bar)
        content_layout.addWidget(self._stack)
        content_layout.addWidget(self._seek_bar)

        sidebar = self._build_sidebar()

        container = QWidget()
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)
        h_layout.addWidget(sidebar)
        h_layout.addWidget(content)
        self.setCentralWidget(container)

        self._trans_overlay = QLabel(content)
        self._trans_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._trans_overlay.hide()
        self._opacity_effect = QGraphicsOpacityEffect(self._trans_overlay)
        self._trans_overlay.setGraphicsEffect(self._opacity_effect)
        self._trans_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._trans_anim.setDuration(520)
        self._trans_anim.setStartValue(1.0)
        self._trans_anim.setEndValue(0.0)
        self._trans_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._trans_anim.finished.connect(self._trans_overlay.hide)

        self._bookshelf.comic_opened.connect(self._open_comic_from_bookshelf)
        self.viewer.page_forward.connect(self.next_page)
        self.viewer.page_back.connect(self.prev_page)
        self.viewer.mouse_moved.connect(self._on_viewer_mouse_y)
        self._seek_bar.seeked.connect(self.seek_to_page)

        self._build_menus()

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

        library_menu = menubar.addMenu("&Library")

        self._add_folder_action = QAction("&Add Folder to Library...", self)
        self._add_folder_action.setShortcut("Ctrl+L")
        self._add_folder_action.triggered.connect(self.add_folder_to_library)
        library_menu.addAction(self._add_folder_action)

        back_action = QAction("← &Back to Library", self)
        back_action.setShortcut("Escape")
        back_action.triggered.connect(self._on_escape)
        library_menu.addAction(back_action)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(56)
        sidebar.setStyleSheet("QWidget { background: #1a1a1a; }")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        logo = QLabel("◉")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedHeight(48)
        logo.setStyleSheet("background: #4a9eff; color: #fff; font-size: 18px; font-family: 'Libre Baskerville';")
        layout.addWidget(logo)

        btn_css = (
            "QPushButton { background: transparent; color: #555; border: none;"
            " border-radius: 0; font-size: 20px; font-family: 'Libre Baskerville'; padding: 0; }"
            "QPushButton:hover { color: #fff; background: #252525; }"
            "QPushButton:pressed { color: #4a9eff; }"
            "QPushButton:disabled { color: #2a2a2a; }"
        )

        btn_lib = QPushButton("⌂")
        btn_lib.setFixedSize(56, 52)
        btn_lib.setToolTip("Library")
        btn_lib.setStyleSheet(btn_css)
        btn_lib.clicked.connect(self._back_to_library)
        layout.addWidget(btn_lib)

        self._btn_add = QPushButton("+")
        self._btn_add.setFixedSize(56, 52)
        self._btn_add.setToolTip("Add Folder to Library (Ctrl+L)")
        self._btn_add.setStyleSheet(btn_css)
        self._btn_add.clicked.connect(self.add_folder_to_library)
        layout.addWidget(self._btn_add)

        layout.addStretch()

        return sidebar

    def _fade_switch(self, switch_fn):
        """Capture the current stack view, run switch_fn, then fade the capture out."""
        grab = self._stack.grab()
        stack_geom = self._stack.geometry()
        switch_fn()
        self._trans_overlay.setPixmap(grab)
        self._trans_overlay.setGeometry(stack_geom)
        self._trans_overlay.show()
        self._trans_overlay.raise_()
        self._trans_anim.stop()
        self._opacity_effect.setOpacity(1.0)
        self._trans_anim.start()

    # ----- Library / reader navigation -----

    def _open_comic_from_bookshelf(self, path: str):
        self.load_file(path)

    def _back_to_library(self):
        if self.isFullScreen():
            self.showNormal()
        def do_switch():
            self._reader_bar.hide()
            self._bar_timer.stop()
            self._seek_bar.setVisible(False)
            self._bookshelf.refresh()
            self._stack.setCurrentIndex(0)
        self._fade_switch(do_switch)
        self.setWindowTitle("Comic Reader")
        self._current_comic_id = None

    def _on_escape(self):
        if self.isFullScreen():
            self._toggle_fullscreen()
        else:
            self._back_to_library()

    # ----- File loading -----

    def open_file_dialog(self):
        last_dir = self._settings.value("last_dir", str(Path.home()))
        dialog = QFileDialog(self, "Open Comic", last_dir)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setNameFilters(SUPPORTED_FILTERS)
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self.load_file(files[0])

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
        title = Path(path).stem
        self.setWindowTitle(f"Comic Reader — {title}")
        self._reader_bar.set_title(title)
        if not self.isFullScreen():
            self._reader_bar.show()

        # Resume to saved page if this comic is in the library.
        comic = self._library.get_comic(path)
        if comic is not None:
            self._current_comic_id = comic.id
            if 0 < comic.current_page < self._reader.page_count():
                self._current_page = comic.current_page
            else:
                self._current_page = 0
        else:
            self._current_comic_id = None
            self._current_page = 0

        self._seek_bar.set_page_count(self._reader.page_count())

        # Pre-load the page while the bookshelf is still showing
        self._show_current_page()

        # Short pause so the click registers visually, then fade to reader
        def do_switch():
            self._seek_bar.setVisible(True)
            self._stack.setCurrentIndex(1)
        QTimer.singleShot(180, lambda: self._fade_switch(do_switch))

    # ----- Page navigation -----

    def _show_current_page(self, direction: int = 0):
        if not self._reader:
            return
        try:
            page_count = self._reader.page_count()
            data = self._reader.get_page_bytes(self._current_page)
            self.viewer.set_image(data, direction)
            if page_count > 0:
                self._seek_bar.set_progress((self._current_page + 1) / page_count)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load page:\n{e}")

    def next_page(self):
        if self._reader and self._current_page < self._reader.page_count() - 1:
            self._current_page += 1
            self._show_current_page(direction=1)
            self._save_progress()

    def prev_page(self):
        if self._reader and self._current_page > 0:
            self._current_page -= 1
            self._show_current_page(direction=-1)
            self._save_progress()

    def first_page(self):
        if self._reader:
            self._current_page = 0
            self._show_current_page(direction=-1)
            self._save_progress()

    def last_page(self):
        if self._reader:
            self._current_page = self._reader.page_count() - 1
            self._show_current_page(direction=1)
            self._save_progress()

    def seek_to_page(self, page: int):
        if self._reader:
            new_page = max(0, min(page, self._reader.page_count() - 1))
            direction = 1 if new_page > self._current_page else (-1 if new_page < self._current_page else 0)
            self._current_page = new_page
            self._show_current_page(direction=direction)
            self._save_progress()

    def _save_progress(self):
        if self._current_comic_id is not None:
            self._library.update_progress(self._current_comic_id, self._current_page)

    # ----- Window helpers -----

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._stack.currentIndex() == 1:
                self._reader_bar.show()
                self._bar_timer.stop()
        else:
            self.showFullScreen()
            self._reader_bar.hide()

    def _on_viewer_mouse_y(self, y: int):
        if self.isFullScreen() and y < 60:
            self._reader_bar.show()
            self._reader_bar.raise_()
            self._bar_timer.start()

    def _hide_reader_bar_fullscreen(self):
        if self.isFullScreen():
            self._reader_bar.hide()

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
        if self._scanner:
            self._scanner.cancel()
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait()
        self._settings.setValue("geometry", self.saveGeometry())
        if self._reader:
            self._reader.close()
        self._library.close()
        super().closeEvent(event)

    # ----- Library scanning -----

    def add_folder_to_library(self):
        last_dir = self._settings.value("last_library_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Add Folder to Library", last_dir)
        if not folder:
            return

        self._settings.setValue("last_library_dir", folder)
        self._add_folder_action.setEnabled(False)
        self._btn_add.setEnabled(False)

        self._scan_thread = QThread(self)
        self._scanner = LibraryScanner(self._library, Path(folder))
        self._scanner.moveToThread(self._scan_thread)

        self._scan_progress = QProgressDialog("Finding comic files…", "Cancel", 0, 0, self)
        self._scan_progress.setWindowTitle("Scanning Library")
        self._scan_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._scan_progress.setMinimumDuration(0)
        self._scan_progress.setValue(0)

        self._scan_thread.started.connect(self._scanner.run)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scan_progress.canceled.connect(self._scanner.cancel)

        self._scan_thread.start()

    def _on_scan_progress(self, current: int, total: int, filename: str):
        if self._scan_progress is None:
            return
        if self._scan_progress.maximum() == 0 and total > 0:
            self._scan_progress.setMaximum(total)
        self._scan_progress.setValue(current)
        self._scan_progress.setLabelText(
            f"Processing {current + 1} of {total}:\n{filename}"
        )

    def _on_scan_finished(self, result):
        if self._scan_progress:
            self._scan_progress.close()
        self._cleanup_scan()
        self._bookshelf.refresh()

        msg = QMessageBox(self)
        if result.cancelled:
            msg.setWindowTitle("Scan Cancelled")
            msg.setText(
                f"Scan cancelled.\n\n"
                f"Added:   {result.added}\n"
                f"Skipped: {result.skipped} (already in library)"
            )
        else:
            msg.setWindowTitle("Library Scan Complete")
            msg.setText(
                f"Library scan complete.\n\n"
                f"Added:   {result.added}\n"
                f"Skipped: {result.skipped} (already in library)\n"
                f"Errors:  {len(result.errors)}"
            )
        if result.errors:
            msg.setDetailedText(
                "\n\n".join(f"{path}\n  {err}" for path, err in result.errors)
            )
        msg.exec()

    def _cleanup_scan(self):
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait()
            self._scan_thread = None
        self._scanner = None
        self._scan_progress = None
        self._add_folder_action.setEnabled(True)
        self._btn_add.setEnabled(True)
