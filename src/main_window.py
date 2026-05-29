"""Main application window with menus, navigation, and the page viewer."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QSettings, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

class _ReaderBar(QWidget):
    """Top bar shown while reading — back button, comic title, and ⋮ menu."""
    back_clicked = pyqtSignal()
    menu_requested = pyqtSignal()
    HEIGHT = 56

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReaderBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.HEIGHT)
        self.setStyleSheet(
            "#ReaderBar { background: #ecdede; border-bottom: 2px solid #c4aeae; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(12)

        self._back_btn = QPushButton("←")
        self._back_btn.setFlat(True)
        self._back_btn.setStyleSheet("color: #8b2a2a; border: none; padding: 4px 8px;")
        self._back_btn.clicked.connect(self.back_clicked)
        layout.addWidget(self._back_btn)

        self._title = QLabel()
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(22)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)
        self._title.setStyleSheet("background: transparent; color: #2a1818;")
        layout.addWidget(self._title)
        layout.addStretch()

        self._menu_btn = QPushButton("⋮")
        self._menu_btn.setFlat(True)
        self._menu_btn.setFixedSize(36, 36)
        self._menu_btn.setStyleSheet(
            "color: #8b2a2a; border: none; font-size: 18px; padding: 0;"
        )
        self._menu_btn.clicked.connect(self.menu_requested)
        layout.addWidget(self._menu_btn)

        self.hide()

    def set_title(self, title: str):
        self._title.setText(title)

    def menu_btn_global_pos(self) -> QPoint:
        return self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height())
        )

    def apply_theme(self, c: dict):
        self.setStyleSheet(
            f"#ReaderBar {{ background: {c['reader_bar_bg']}; border-bottom: 2px solid {c['border']}; }}"
        )
        self._back_btn.setStyleSheet(
            f"background: transparent; color: {c['accent']}; border: none; padding: 4px 8px;"
        )
        self._menu_btn.setStyleSheet(
            f"background: transparent; color: {c['accent']}; border: none;"
            f" font-size: 18px; padding: 0;"
        )
        self._title.setStyleSheet(f"background: transparent; color: {c['text']};")


from archive_handler import ComicReader, open_comic
from bookshelf import BookshelfView
from keybindings import ACTIONS, KeybindingDialog, KeybindingManager
from library import Library, Shelf
from library_scanner import LibraryScanner
from preloader import PageCache, PagePreloader
from viewer import ComicViewer, FitMode, ReadingMode, SeekBar, ThumbnailStrip, make_spread_pixmap
from webtoon_viewer import WebtoonViewer
import themes


class _Sidebar(QWidget):
    """Left sidebar — action buttons + library/shelf navigation list."""

    show_folders_clicked = pyqtSignal()
    show_hidden_clicked = pyqtSignal()
    show_shelf_clicked = pyqtSignal(int, str)   # shelf_id, shelf_name
    add_folder_clicked = pyqtSignal()
    new_shelf_clicked = pyqtSignal()
    rename_shelf_requested = pyqtSignal(int, str)  # shelf_id, current_name
    delete_shelf_requested = pyqtSignal(int)        # shelf_id
    back_to_root_clicked = pyqtSignal()

    WIDTH = 180

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._active_id: int = -1   # -1 = Folders, -2 = Hidden, int = shelf id
        self._shelf_btns: list[tuple[int, QPushButton]] = []
        self._folders_btn: QPushButton | None = None
        self._hidden_btn: QPushButton | None = None
        self._theme: dict = themes.DARK

        self.setFixedWidth(self.WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top action row
        top_row = QWidget()
        top_row.setFixedHeight(56)
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(4, 0, 4, 0)
        top_layout.setSpacing(0)

        self._btn_back = QPushButton("←")
        self._btn_back.setFixedSize(44, 56)
        self._btn_back.setFlat(True)
        self._btn_back.setToolTip("Back to folder list")
        self._btn_back.clicked.connect(self.back_to_root_clicked)
        self._btn_back.hide()
        top_layout.addWidget(self._btn_back)
        top_layout.addStretch()

        self._btn_add = QPushButton("+")
        self._btn_add.setFixedSize(44, 56)
        self._btn_add.setFlat(True)
        self._btn_add.setToolTip("Add Folder to Library (Ctrl+L)")
        self._btn_add.clicked.connect(self.add_folder_clicked)
        top_layout.addWidget(self._btn_add)

        outer.addWidget(top_row)

        # Scrollable shelf list
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 4, 0, 8)
        self._list_layout.setSpacing(0)
        self._scroll.setWidget(self._list_widget)

        self._build_list()
        self._apply_btn_styles()

    # ----- Internal builders -----

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "QLabel { color: rgba(255,255,255,0.30); font-size: 10px;"
            " letter-spacing: 1px; padding: 10px 12px 2px; background: transparent; }"
        )
        return lbl

    def _nav_btn(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setCheckable(True)
        btn.setStyleSheet(self._nav_btn_css())
        return btn

    def _nav_btn_css(self) -> str:
        c = self._theme
        return (
            f"QPushButton {{ text-align: left; padding: 5px 14px; border: none;"
            f" background: transparent; color: {c['text']}; font-size: 13px;"
            f" font-family: 'Libre Baskerville'; border-radius: 4px; margin: 1px 6px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.07); }}"
            f"QPushButton:checked {{ background: rgba(255,255,255,0.13); }}"
        )

    def _build_list(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._shelf_btns.clear()
        self._folders_btn = None
        self._hidden_btn = None

        # LIBRARY section
        self._list_layout.addWidget(self._section_label("LIBRARY"))
        self._folders_btn = self._nav_btn("  Folders")
        self._folders_btn.setChecked(self._active_id == -1)
        self._folders_btn.clicked.connect(self._on_folders_clicked)
        self._list_layout.addWidget(self._folders_btn)

        self._hidden_btn = self._nav_btn("  Hidden")
        self._hidden_btn.setChecked(self._active_id == -2)
        self._hidden_btn.clicked.connect(self._on_hidden_clicked)
        self._list_layout.addWidget(self._hidden_btn)

        # SHELVES section
        self._list_layout.addWidget(self._section_label("SHELVES"))

        shelves = self._library.get_shelves()
        smart = [s for s in shelves if s.kind == "smart"]
        manual = [s for s in shelves if s.kind == "manual"]

        for shelf in smart:
            btn = self._nav_btn(f"  {shelf.name}")
            btn.setChecked(self._active_id == shelf.id)
            sid, sname = shelf.id, shelf.name
            btn.clicked.connect(lambda checked, s=sid, n=sname: self._on_shelf_clicked(s, n))
            self._shelf_btns.append((shelf.id, btn))
            self._list_layout.addWidget(btn)

        if manual:
            div = QFrame()
            div.setFrameShape(QFrame.Shape.HLine)
            div.setStyleSheet("QFrame { color: rgba(255,255,255,0.10); margin: 4px 12px; }")
            self._list_layout.addWidget(div)

            for shelf in manual:
                btn = self._nav_btn(f"  {shelf.name}")
                btn.setChecked(self._active_id == shelf.id)
                sid, sname = shelf.id, shelf.name
                btn.clicked.connect(lambda checked, s=sid, n=sname: self._on_shelf_clicked(s, n))
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(
                    lambda pos, s=sid, n=sname, b=btn: self._on_shelf_right_click(s, n, b.mapToGlobal(pos))
                )
                self._shelf_btns.append((shelf.id, btn))
                self._list_layout.addWidget(btn)

        self._list_layout.addStretch()

        new_btn = QPushButton("  + New Shelf")
        new_btn.setFlat(True)
        new_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 8px 14px; border: none;"
            " background: transparent; color: rgba(255,255,255,0.40); font-size: 12px;"
            " font-family: 'Libre Baskerville'; border-radius: 4px; margin: 1px 6px; }"
            "QPushButton:hover { color: rgba(255,255,255,0.80); background: rgba(255,255,255,0.07); }"
        )
        new_btn.clicked.connect(self.new_shelf_clicked)
        self._list_layout.addWidget(new_btn)

    def _apply_btn_styles(self):
        c = self._theme
        btn_css = (
            f"QPushButton {{ background: transparent; color: {c['btn_color']}; border: none;"
            f" border-radius: 4px; font-size: 20px; font-family: 'Libre Baskerville'; padding: 0; }}"
            f"QPushButton:hover {{ color: {c['btn_hover_color']}; background: {c['btn_hover_bg']}; }}"
            f"QPushButton:pressed {{ color: {c['btn_pressed']}; }}"
            f"QPushButton:disabled {{ color: {c['btn_disabled']}; }}"
        )
        add_css = (
            f"QPushButton {{ background: transparent; color: {c['btn_color']}; border: none;"
            f" border-radius: 4px; font-size: 20px; font-family: 'Libre Baskerville'; padding: 0; }}"
            f"QPushButton:hover {{ color: {c['btn_hover_color']}; background: {c['btn_hover_bg']};"
            f" border: 1px solid white; }}"
            f"QPushButton:pressed {{ color: {c['btn_pressed']}; }}"
            f"QPushButton:disabled {{ color: {c['btn_disabled']}; }}"
        )
        self._btn_back.setStyleSheet(btn_css)
        self._btn_add.setStyleSheet(add_css)

    # ----- Slots -----

    def _on_folders_clicked(self):
        self.set_active(-1)
        self.show_folders_clicked.emit()

    def _on_hidden_clicked(self):
        self.set_active(-2)
        self.show_hidden_clicked.emit()

    def _on_shelf_clicked(self, shelf_id: int, shelf_name: str):
        self.set_active(shelf_id)
        self.show_shelf_clicked.emit(shelf_id, shelf_name)

    def _on_shelf_right_click(self, shelf_id: int, shelf_name: str, pos):
        menu = QMenu(self)
        rename_action = menu.addAction("Rename…")
        delete_action = menu.addAction("Delete shelf")
        action = menu.exec(pos)
        if action == rename_action:
            self.rename_shelf_requested.emit(shelf_id, shelf_name)
        elif action == delete_action:
            self.delete_shelf_requested.emit(shelf_id)

    # ----- Public API -----

    def set_active(self, active_id: int):
        self._active_id = active_id
        if self._folders_btn:
            self._folders_btn.setChecked(active_id == -1)
        if self._hidden_btn:
            self._hidden_btn.setChecked(active_id == -2)
        for sid, btn in self._shelf_btns:
            btn.setChecked(sid == active_id)

    def refresh_shelves(self):
        self._build_list()

    def set_back_visible(self, visible: bool):
        self._btn_back.setVisible(visible)

    def set_add_enabled(self, enabled: bool):
        self._btn_add.setEnabled(enabled)

    def apply_theme(self, c: dict):
        self._theme = c
        self.setStyleSheet(f"QWidget {{ background: {c['sidebar_bg']}; }}")
        self._apply_btn_styles()
        self._build_list()

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

        # Reading-mode state (spread is session-only; webtoon + manga persist per-comic)
        self._spread_mode: bool = False
        self._webtoon_mode: bool = False
        self._is_manga: bool = False
        self._webtoon_width_pct: int = 100  # 100 / 80 / 60

        # Page cache + preloader
        self._cache: PageCache = PageCache()
        self._preloader: PagePreloader | None = None

        # Thumb strip load-once guard
        self._thumb_strip_loaded: bool = False

        self._kb = KeybindingManager()

        self._stack = QStackedWidget()
        self._bookshelf = BookshelfView(self._library)
        self.viewer = ComicViewer()
        self._webtoon_viewer = WebtoonViewer()
        self._stack.addWidget(self._bookshelf)      # index 0
        self._stack.addWidget(self.viewer)           # index 1
        self._stack.addWidget(self._webtoon_viewer)  # index 2

        self._seek_bar = SeekBar()
        self._seek_bar.setVisible(False)

        self._thumb_strip = ThumbnailStrip()
        self._thumb_strip.setVisible(False)

        self._reader_bar = _ReaderBar()
        self._reader_bar.back_clicked.connect(self._back_to_library)
        self._reader_bar_opacity = QGraphicsOpacityEffect(self._reader_bar)
        self._reader_bar.setGraphicsEffect(self._reader_bar_opacity)
        self._reader_bar_opacity.setOpacity(1.0)

        self._reader_bar_height_anim = QPropertyAnimation(self._reader_bar, b"maximumHeight", self)
        self._reader_bar_height_anim.setDuration(180)
        self._reader_bar_height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_bar_opacity_anim = QPropertyAnimation(self._reader_bar_opacity, b"opacity", self)
        self._reader_bar_opacity_anim.setDuration(150)
        self._reader_bar_opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_bar_anim = QParallelAnimationGroup(self)
        self._reader_bar_anim.addAnimation(self._reader_bar_height_anim)
        self._reader_bar_anim.addAnimation(self._reader_bar_opacity_anim)
        self._reader_bar_anim.finished.connect(self._on_reader_bar_anim_finished)
        self._reader_bar_should_hide = False
        self._reader_bar_target_visible = False

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._reader_bar)
        content_layout.addWidget(self._stack)
        content_layout.addWidget(self._thumb_strip)
        content_layout.addWidget(self._seek_bar)

        self._sidebar = _Sidebar(self._library)
        self._sidebar.show_folders_clicked.connect(self._bookshelf.go_to_root)
        self._sidebar.show_hidden_clicked.connect(self._bookshelf.show_hidden)
        self._sidebar.show_shelf_clicked.connect(self._bookshelf.show_shelf)
        self._sidebar.add_folder_clicked.connect(self.add_folder_to_library)
        self._sidebar.new_shelf_clicked.connect(self._create_new_shelf)
        self._sidebar.rename_shelf_requested.connect(self._rename_shelf)
        self._sidebar.delete_shelf_requested.connect(self._delete_shelf)
        self._sidebar.back_to_root_clicked.connect(self._bookshelf.go_to_root)

        container = QWidget()
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)
        h_layout.addWidget(self._sidebar)
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
        self._bookshelf.folder_entered.connect(self._on_folder_level_changed)
        self._bookshelf.shelf_changed.connect(self._sidebar.refresh_shelves)
        self._bookshelf.folder_rescan_requested.connect(self.rescan_folder)
        self.viewer.page_forward.connect(self.next_page)
        self.viewer.page_back.connect(self.prev_page)
        self.viewer.mouse_moved.connect(self._on_viewer_mouse_y)
        self._seek_bar.seeked.connect(self.seek_to_page)
        self._reader_bar.menu_requested.connect(self._show_reader_menu)
        self._thumb_strip.page_selected.connect(self.seek_to_page)
        self._webtoon_viewer.page_changed.connect(self._on_webtoon_page_changed)
        self._webtoon_viewer.mouse_moved.connect(self._on_viewer_mouse_y)

        self._build_menus()

        self.setAcceptDrops(True)
        self._restore_window_state()

        self.apply_theme(themes.DARK)

    # ----- UI construction -----

    def _build_menus(self):
        kb = self._kb
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

        def _shortcuts(action_id: str) -> list[QKeySequence]:
            primary = QKeySequence(kb.get(action_id))
            extras = [QKeySequence(s) for s in ACTIONS[action_id]["extras"]]
            return [primary] + extras

        fit_page = QAction("Fit &Page", self)
        fit_page.setShortcuts(_shortcuts("fit_page"))
        fit_page.triggered.connect(lambda: self.viewer.set_fit_mode(FitMode.FIT_PAGE))
        view_menu.addAction(fit_page)

        fit_width = QAction("Fit &Width", self)
        fit_width.setShortcuts(_shortcuts("fit_width"))
        fit_width.triggered.connect(lambda: self.viewer.set_fit_mode(FitMode.FIT_WIDTH))
        view_menu.addAction(fit_width)

        actual = QAction("&Actual Size", self)
        actual.setShortcuts(_shortcuts("actual_size"))
        actual.triggered.connect(self.viewer.reset_zoom)
        view_menu.addAction(actual)

        view_menu.addSeparator()

        zoom_in = QAction("Zoom &In", self)
        zoom_in.setShortcuts(_shortcuts("zoom_in"))
        zoom_in.triggered.connect(self.viewer.zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Zoom &Out", self)
        zoom_out.setShortcuts(_shortcuts("zoom_out"))
        zoom_out.triggered.connect(self.viewer.zoom_out)
        view_menu.addAction(zoom_out)

        view_menu.addSeparator()
        fullscreen = QAction("&Fullscreen", self)
        fullscreen.setShortcuts(_shortcuts("fullscreen"))
        fullscreen.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen)

        view_menu.addSeparator()
        shortcuts_action = QAction("Customize Shortcuts…", self)
        shortcuts_action.triggered.connect(self._open_shortcuts_dialog)
        view_menu.addAction(shortcuts_action)

        nav_menu = menubar.addMenu("&Navigate")

        next_page = QAction("&Next Page", self)
        next_page.setShortcuts(_shortcuts("next_page"))
        next_page.triggered.connect(self.next_page)
        nav_menu.addAction(next_page)

        prev_page = QAction("&Previous Page", self)
        prev_page.setShortcuts(_shortcuts("prev_page"))
        prev_page.triggered.connect(self.prev_page)
        nav_menu.addAction(prev_page)

        first = QAction("&First Page", self)
        first.setShortcuts(_shortcuts("first_page"))
        first.triggered.connect(self.first_page)
        nav_menu.addAction(first)

        last = QAction("&Last Page", self)
        last.setShortcuts(_shortcuts("last_page"))
        last.triggered.connect(self.last_page)
        nav_menu.addAction(last)

        nav_menu.addSeparator()

        bm_toggle = QAction("Toggle Bookmark", self)
        bm_toggle.setShortcuts(_shortcuts("bookmark"))
        bm_toggle.triggered.connect(self._toggle_bookmark)
        nav_menu.addAction(bm_toggle)

        bm_prev = QAction("Previous Bookmark", self)
        bm_prev.setShortcuts(_shortcuts("prev_bookmark"))
        bm_prev.triggered.connect(self._prev_bookmark)
        nav_menu.addAction(bm_prev)

        bm_next = QAction("Next Bookmark", self)
        bm_next.setShortcuts(_shortcuts("next_bookmark"))
        bm_next.triggered.connect(self._next_bookmark)
        nav_menu.addAction(bm_next)

        library_menu = menubar.addMenu("&Library")

        self._add_folder_action = QAction("&Add Folder to Library...", self)
        self._add_folder_action.setShortcut("Ctrl+L")
        self._add_folder_action.triggered.connect(self.add_folder_to_library)
        library_menu.addAction(self._add_folder_action)

        back_action = QAction("← &Back to Library", self)
        back_action.setShortcuts(_shortcuts("back_to_library"))
        back_action.triggered.connect(self._on_escape)
        library_menu.addAction(back_action)

        # Thumbnail strip shortcut (no menu entry needed — handled via ⋮)
        thumb_action = QAction(self)
        thumb_action.setShortcuts(_shortcuts("thumbnail_strip"))
        thumb_action.triggered.connect(self._toggle_thumb_strip)
        self.addAction(thumb_action)


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
        self._stop_preloader()

        def do_switch():
            self._hide_reader_bar(animated=False)
            self._seek_bar.setVisible(False)
            self._thumb_strip.setVisible(False)
            self._bookshelf.refresh()
            self._stack.setCurrentIndex(0)
            self._sidebar.show()
        self._fade_switch(do_switch)
        self.setWindowTitle("Comic Reader")
        self._current_comic_id = None

    # ----- Reader ⋮ menu -----

    def _show_reader_menu(self) -> None:
        menu = QMenu(self)

        spread_act = menu.addAction("Spread mode")
        spread_act.setCheckable(True)
        spread_act.setChecked(self._spread_mode)
        spread_act.triggered.connect(self._toggle_spread)

        manga_act = menu.addAction("Manga (right-to-left)")
        manga_act.setCheckable(True)
        manga_act.setChecked(self._is_manga)
        manga_act.triggered.connect(self._toggle_manga)

        webtoon_act = menu.addAction("Webtoon / scroll mode")
        webtoon_act.setCheckable(True)
        webtoon_act.setChecked(self._webtoon_mode)
        webtoon_act.triggered.connect(self._toggle_webtoon)

        menu.addSeparator()

        is_bm = self._current_page_is_bookmarked()
        bm_text = "Remove bookmark" if is_bm else "Bookmark this page…"
        bm_act = menu.addAction(bm_text)
        bm_act.triggered.connect(self._toggle_bookmark)

        menu.addSeparator()

        thumb_act = menu.addAction("Page thumbnails")
        thumb_act.setCheckable(True)
        thumb_act.setChecked(self._thumb_strip.isVisible())
        thumb_act.triggered.connect(self._toggle_thumb_strip)

        if self._webtoon_mode:
            menu.addSeparator()
            width_menu = menu.addMenu("Webtoon width")
            for pct in (100, 80, 60):
                act = width_menu.addAction(f"{pct}%")
                act.setCheckable(True)
                act.setChecked(self._webtoon_width_pct == pct)
                act.triggered.connect(lambda checked, p=pct: self._set_webtoon_width(p))

        menu.exec(self._reader_bar.menu_btn_global_pos())

    # ----- Toggle handlers -----

    def _toggle_spread(self) -> None:
        if self._webtoon_mode:
            return  # spread is meaningless in webtoon mode
        self._spread_mode = not self._spread_mode
        if not self._reader:
            return
        page_count = self._reader.page_count()
        if self._spread_mode:
            self._current_page = (self._current_page // 2) * 2
            self._seek_bar.set_page_count(max(1, (page_count + 1) // 2))
        else:
            self._seek_bar.set_page_count(page_count)
        self._show_current_page(direction=0)

    def _toggle_manga(self) -> None:
        self._is_manga = not self._is_manga
        self.viewer.set_rtl(self._is_manga)
        if self._current_comic_id is not None:
            self._library.set_is_manga(self._current_comic_id, self._is_manga)

    def _toggle_webtoon(self) -> None:
        if not self._reader:
            return
        self._webtoon_mode = not self._webtoon_mode
        if self._current_comic_id is not None:
            self._library.set_reading_mode(
                self._current_comic_id, "webtoon" if self._webtoon_mode else "single"
            )
        if self._webtoon_mode:
            self._spread_mode = False
            self._stop_preloader()
            self._seek_bar.set_page_count(self._reader.page_count())
            self._webtoon_viewer.set_width_fraction(self._webtoon_width_pct / 100)
            self._webtoon_viewer.load_comic(self._reader, self._current_page)
            self._stack.setCurrentIndex(2)
        else:
            self._cache.clear()
            self._preloader = PagePreloader(self._reader, self._cache)
            self._preloader.set_center(self._current_page)
            self._preloader.start()
            self._stack.setCurrentIndex(1)
            self._show_current_page(direction=0)

    def _set_webtoon_width(self, pct: int) -> None:
        self._webtoon_width_pct = pct
        self._webtoon_viewer.set_width_fraction(pct / 100)

    def _toggle_thumb_strip(self) -> None:
        if self._thumb_strip.isVisible():
            self._thumb_strip.hide()
        else:
            if self._reader and not self._thumb_strip_loaded:
                self._thumb_strip.load_comic(self._reader)
                self._thumb_strip_loaded = True
                self._thumb_strip.set_current(self._current_page)
            self._thumb_strip.show()

    # ----- Bookmarks -----

    def _current_page_is_bookmarked(self) -> bool:
        if self._current_comic_id is None:
            return False
        return self._library.is_bookmarked(self._current_comic_id, self._current_page)

    def _toggle_bookmark(self) -> None:
        if self._current_comic_id is None or not self._reader:
            return
        page = self._current_page
        if self._library.is_bookmarked(self._current_comic_id, page):
            self._library.toggle_bookmark(self._current_comic_id, page)
        else:
            label, ok = QInputDialog.getText(self, "Add Bookmark", "Label (optional):")
            if not ok:
                return
            self._library.toggle_bookmark(self._current_comic_id, page, label.strip() or None)
        self._reload_bookmarks()

    def _prev_bookmark(self) -> None:
        if self._current_comic_id is None:
            return
        bookmarks = sorted(self._library.get_bookmarks(self._current_comic_id),
                           key=lambda b: b.page_index, reverse=True)
        for b in bookmarks:
            if b.page_index < self._current_page:
                self.seek_to_page(b.page_index)
                return

    def _next_bookmark(self) -> None:
        if self._current_comic_id is None:
            return
        bookmarks = sorted(self._library.get_bookmarks(self._current_comic_id),
                           key=lambda b: b.page_index)
        for b in bookmarks:
            if b.page_index > self._current_page:
                self.seek_to_page(b.page_index)
                return

    def _reload_bookmarks(self) -> None:
        if self._current_comic_id is None:
            self._seek_bar.set_bookmarks([])
            return
        bookmarks = self._library.get_bookmarks(self._current_comic_id)
        self._seek_bar.set_bookmarks([(b.page_index, b.label) for b in bookmarks])

    # ----- Preloader helpers -----

    def _stop_preloader(self) -> None:
        if self._preloader and self._preloader.isRunning():
            self._preloader.abort()
            self._preloader.wait()
        self._preloader = None

    # ----- Webtoon page tracking -----

    def _on_webtoon_page_changed(self, page: int) -> None:
        self._current_page = page
        page_count = self._reader.page_count() if self._reader else 0
        if page_count > 0:
            self._seek_bar.set_progress((page + 1) / page_count)
        self._save_progress()

    # ----- Shortcuts dialog -----

    def _open_shortcuts_dialog(self) -> None:
        dlg = KeybindingDialog(self._kb, self)
        if dlg.exec():
            # Rebuild menus so new shortcuts take effect immediately
            self.menuBar().clear()
            self._build_menus()

    def _on_escape(self):
        if self._stack.currentIndex() in (1, 2):  # single or webtoon viewer
            self._back_to_library()
        elif self.isFullScreen():
            self._toggle_fullscreen()

    def _on_folder_level_changed(self, in_folder: bool):
        self._sidebar.set_back_visible(in_folder)
        if not in_folder:
            if self._bookshelf._show_hidden_mode:
                self._sidebar.set_active(-2)
            else:
                shelf_id = self._bookshelf._current_shelf_id
                self._sidebar.set_active(shelf_id if shelf_id is not None else -1)

    def apply_theme(self, c: dict):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(themes.app_stylesheet(c))
        self._sidebar.apply_theme(c)
        self._reader_bar.apply_theme(c)
        self._bookshelf.apply_theme(c)

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
            # Stop any background threads bound to the previous reader before
            # closing it, so they can't read from a closed handle.
            self._stop_preloader()
            self._thumb_strip.stop()
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
            self._show_reader_bar(animated=False)

        # Reset session state
        self._spread_mode = False
        self._thumb_strip.setVisible(False)
        self._thumb_strip_loaded = False

        # Resume to saved page; apply per-comic settings from library
        comic = self._library.get_comic(path)
        if comic is not None:
            self._current_comic_id = comic.id
            self._current_page = comic.current_page if 0 < comic.current_page < self._reader.page_count() else 0
            self._webtoon_mode = comic.reading_mode == "webtoon"
            self._is_manga = comic.is_manga
        else:
            self._current_comic_id = None
            self._current_page = 0
            self._webtoon_mode = False
            self._is_manga = False

        self.viewer.set_rtl(self._is_manga)

        # Set up page cache + preloader (only in non-webtoon mode)
        self._stop_preloader()
        if not self._webtoon_mode:
            self._cache.clear()
            self._preloader = PagePreloader(self._reader, self._cache)
            self._preloader.set_center(self._current_page)
            self._preloader.start()

        # Load bookmarks for seek bar
        self._reload_bookmarks()

        page_count = self._reader.page_count()
        if self._webtoon_mode:
            self._seek_bar.set_page_count(page_count)
            self._webtoon_viewer.set_width_fraction(self._webtoon_width_pct / 100)
            self._webtoon_viewer.load_comic(self._reader, self._current_page)
            target_index = 2
        else:
            self._seek_bar.set_page_count(page_count)
            self._show_current_page()
            target_index = 1

        def do_switch():
            self._seek_bar.setVisible(True)
            self._stack.setCurrentIndex(target_index)
            self._sidebar.hide()

        QTimer.singleShot(180, lambda: self._fade_switch(do_switch))

    # ----- Page navigation -----

    def _show_current_page(self, direction: int = 0):
        if not self._reader:
            return
        try:
            if self._spread_mode:
                self._show_spread(direction)
            else:
                self._show_single(direction)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load page:\n{e}")

    def _show_single(self, direction: int = 0):
        page_count = self._reader.page_count()
        cached = self._cache.get(self._current_page)
        if cached is not None:
            from PyQt6.QtGui import QPixmap
            self.viewer.set_image_pixmap(QPixmap.fromImage(cached), direction)
        else:
            data = self._reader.get_page_bytes(self._current_page)
            self.viewer.set_image(data, direction)
        if page_count > 0:
            self._seek_bar.set_progress((self._current_page + 1) / page_count)
        if self._thumb_strip.isVisible():
            self._thumb_strip.set_current(self._current_page)

    def _show_spread(self, direction: int = 0):
        page_count = self._reader.page_count()
        p1 = self._current_page
        p2 = p1 + 1
        data1 = self._reader.get_page_bytes(p1)
        data2 = self._reader.get_page_bytes(p2) if p2 < page_count else None
        if data2 is not None:
            pixmap = make_spread_pixmap(data1, data2, self._is_manga)
        else:
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap()
            pixmap.loadFromData(data1)
        self.viewer.set_image_pixmap(pixmap, direction)
        pair_count = (page_count + 1) // 2
        pair_index = p1 // 2
        if pair_count > 0:
            self._seek_bar.set_progress((pair_index + 1) / pair_count)

    def next_page(self):
        if not self._reader:
            return
        step = 2 if self._spread_mode else 1
        limit = self._reader.page_count() - 1
        if self._current_page < limit:
            self._current_page = min(self._current_page + step, limit)
            if self._spread_mode:
                self._current_page = (self._current_page // 2) * 2
            self._show_current_page(direction=1)
            self._advance_preloader()
            self._save_progress()

    def prev_page(self):
        if not self._reader:
            return
        step = 2 if self._spread_mode else 1
        if self._current_page > 0:
            self._current_page = max(self._current_page - step, 0)
            if self._spread_mode:
                self._current_page = (self._current_page // 2) * 2
            self._show_current_page(direction=-1)
            self._advance_preloader()
            self._save_progress()

    def first_page(self):
        if self._reader:
            self._current_page = 0
            self._show_current_page(direction=-1)
            self._advance_preloader()
            self._save_progress()

    def last_page(self):
        if self._reader:
            page_count = self._reader.page_count()
            self._current_page = ((page_count - 1) // 2) * 2 if self._spread_mode else page_count - 1
            self._show_current_page(direction=1)
            self._advance_preloader()
            self._save_progress()

    def seek_to_page(self, page: int):
        if not self._reader:
            return
        if self._spread_mode:
            page = (page // 2) * 2  # snap to spread boundary
        new_page = max(0, min(page, self._reader.page_count() - 1))
        direction = 1 if new_page > self._current_page else (-1 if new_page < self._current_page else 0)
        self._current_page = new_page
        self._show_current_page(direction=direction)
        self._advance_preloader()
        self._save_progress()

    def _advance_preloader(self) -> None:
        if self._preloader and self._preloader.isRunning():
            self._preloader.set_center(self._current_page)

    def _save_progress(self):
        if self._current_comic_id is not None:
            self._library.update_progress(self._current_comic_id, self._current_page)

    # ----- Window helpers -----

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._stack.currentIndex() in (1, 2):
                self._show_reader_bar(animated=False)
        else:
            self.showFullScreen()
            self._hide_reader_bar(animated=True)

    def _on_viewer_mouse_y(self, y: int):
        # Windowed mode always shows the bar. In fullscreen it's hidden for immersive
        # reading but reveals when the cursor moves to the top edge, and hides again
        # when the cursor moves away — otherwise there's no way to reach the menu/back.
        if not self.isFullScreen():
            return
        if y < 60:
            self._show_reader_bar(animated=True)
        elif y > _ReaderBar.HEIGHT + 60:
            self._hide_reader_bar(animated=True)

    def _show_reader_bar(self, animated: bool):
        if self._reader_bar_target_visible and self._reader_bar.isVisible():
            return
        self._reader_bar_target_visible = True
        self._reader_bar_should_hide = False
        self._reader_bar_anim.stop()
        self._reader_bar.show()
        self._reader_bar.raise_()
        if not animated:
            self._reader_bar.setMaximumHeight(_ReaderBar.HEIGHT)
            self._reader_bar_opacity.setOpacity(1.0)
            return
        self._animate_reader_bar(_ReaderBar.HEIGHT, 1.0)

    def _hide_reader_bar(self, animated: bool):
        if not self._reader_bar_target_visible and not self._reader_bar.isVisible():
            return
        self._reader_bar_target_visible = False
        self._reader_bar_should_hide = True
        self._reader_bar_anim.stop()
        if not animated:
            self._reader_bar.setMaximumHeight(0)
            self._reader_bar_opacity.setOpacity(0.0)
            self._reader_bar.hide()
            return
        self._animate_reader_bar(0, 0.0)

    def _animate_reader_bar(self, height: int, opacity: float):
        self._reader_bar_height_anim.setStartValue(self._reader_bar.maximumHeight())
        self._reader_bar_height_anim.setEndValue(height)
        self._reader_bar_opacity_anim.setStartValue(self._reader_bar_opacity.opacity())
        self._reader_bar_opacity_anim.setEndValue(opacity)
        self._reader_bar_anim.start()

    def _on_reader_bar_anim_finished(self):
        if self._reader_bar_should_hide:
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
        self._stop_preloader()
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

    # ----- Shelf management -----

    def _create_new_shelf(self):
        name, ok = QInputDialog.getText(self, "New Shelf", "Shelf name:")
        if ok and name.strip():
            shelf_id = self._library.create_shelf(name.strip())
            self._sidebar.refresh_shelves()
            self._sidebar.set_active(shelf_id)
            self._bookshelf.show_shelf(shelf_id, name.strip())

    def _rename_shelf(self, shelf_id: int, current_name: str):
        name, ok = QInputDialog.getText(self, "Rename Shelf", "New name:", text=current_name)
        if ok and name.strip() and name.strip() != current_name:
            self._library.rename_shelf(shelf_id, name.strip())
            self._sidebar.refresh_shelves()
            if self._bookshelf._current_shelf_id == shelf_id:
                self._bookshelf._current_shelf_name = name.strip()
                self._bookshelf._header.set_shelf_mode(name.strip())

    def _delete_shelf(self, shelf_id: int):
        reply = QMessageBox.question(
            self, "Delete Shelf",
            "Delete this shelf? Comics will not be removed from your library.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._library.delete_shelf(shelf_id)
            if self._bookshelf._current_shelf_id == shelf_id:
                self._bookshelf.go_to_root()
                self._sidebar.set_active(-1)
            self._sidebar.refresh_shelves()

    # ----- Library scanning -----

    def add_folder_to_library(self):
        last_dir = self._settings.value("last_library_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Add Folder to Library", last_dir)
        if not folder:
            return
        self._settings.setValue("last_library_dir", folder)
        self._start_scan(folder)

    def rescan_folder(self, folder_path: str):
        self._start_scan(folder_path)

    def _start_scan(self, folder: str):
        if self._scan_thread and self._scan_thread.isRunning():
            return

        self._add_folder_action.setEnabled(False)
        self._sidebar.set_add_enabled(False)

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
        self._sidebar.set_add_enabled(True)
