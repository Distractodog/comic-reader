"""Bookshelf library view — two-level browser: folder grid → comic grid."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from library import Comic, Folder, Library, Series, Shelf

TILE_W = 200
COVER_H = 300
TITLE_H = 28
STATUS_H = 22
TILE_H = COVER_H + TITLE_H + STATUS_H
TILE_SPACING = 18
PROGRESS_H = 3  # overlaid on bottom of cover

_BG = QColor("#f0e8e8")
_COVER_BG = QColor("#d8cccc")
_TITLE_FG = QColor("#2a1818")
_STATUS_FG = QColor("#7a5858")
_HOVER_OVERLAY = QColor(100, 30, 30, 22)
_PROGRESS_TRACK = QColor("#c4aeae")
_PROGRESS_FILL = QColor("#8b2a2a")
_PLACEHOLDER_FG = QColor("#b0a0a0")


class _Tile(QWidget):
    """Shared base for FolderTile and ComicTile."""

    opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._hovered = False
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _load_pixmap(self, path: str | None) -> None:
        if path and Path(path).exists():
            px = QPixmap(path)
            if not px.isNull():
                self._pixmap = px.scaled(
                    TILE_W, COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

    def _draw_cover(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(0.0, 0.0, float(TILE_W), float(TILE_H), 8.0, 8.0)
        painter.setClipPath(clip)

        painter.fillRect(0, 0, TILE_W, COVER_H, _COVER_BG)
        if self._pixmap:
            x = (TILE_W - self._pixmap.width()) // 2
            y = (COVER_H - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)
        else:
            painter.setPen(_PLACEHOLDER_FG)
            painter.drawText(
                QRect(0, 0, TILE_W, COVER_H),
                Qt.AlignmentFlag.AlignCenter,
                "?",
            )
        if self._hovered:
            painter.fillRect(0, 0, TILE_W, COVER_H, _HOVER_OVERLAY)

    def _draw_title(self, painter: QPainter, text: str) -> None:
        painter.fillRect(0, COVER_H, TILE_W, TITLE_H, _BG)
        painter.setPen(_TITLE_FG)
        font = painter.font()
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        elided = QFontMetrics(font).elidedText(
            text, Qt.TextElideMode.ElideRight, TILE_W - 8
        )
        painter.drawText(
            QRect(4, COVER_H + 2, TILE_W - 8, TITLE_H - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )

    def _draw_status(self, painter: QPainter, text: str) -> None:
        painter.fillRect(0, COVER_H + TITLE_H, TILE_W, STATUS_H, _BG)
        painter.setPen(_STATUS_FG)
        font = painter.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(
            QRect(4, COVER_H + TITLE_H + 2, TILE_W - 8, STATUS_H - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            text,
        )

    def enterEvent(self, event):
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()

    def _on_click(self):
        raise NotImplementedError


class FolderTile(_Tile):
    rescan_requested = pyqtSignal(str)   # folder_path

    def __init__(self, folder: Folder, parent=None):
        super().__init__(parent)
        self._folder = folder
        self._load_pixmap(folder.cover_path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        self._draw_title(painter, self._folder.name)
        n = self._folder.comic_count
        self._draw_status(painter, f"{n} comic{'s' if n != 1 else ''}")

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Rescan folder").triggered.connect(
            lambda: self.rescan_requested.emit(self._folder.path)
        )
        menu.exec(event.globalPos())

    def _on_click(self):
        self.opened.emit(self._folder.path)


class ComicTile(_Tile):
    shelf_action_requested = pyqtSignal(int, int, int)  # comic_id, global_x, global_y
    select_toggled = pyqtSignal(int)                    # comic_id

    def __init__(self, comic: Comic, selected: bool = False, parent=None):
        super().__init__(parent)
        self._comic = comic
        self._selected = selected
        self._load_pixmap(comic.cover_path)

    def set_selected(self, selected: bool):
        self._selected = selected

    def contextMenuEvent(self, event):
        self.shelf_action_requested.emit(
            self._comic.id, event.globalPos().x(), event.globalPos().y()
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                self.select_toggled.emit(self._comic.id)
            else:
                self._on_click()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        self._draw_progress(painter)
        if self._selected:
            self._draw_selection(painter)
        title = self._comic.title or Path(self._comic.file_path).stem
        self._draw_title(painter, title)
        self._draw_status(painter, self._status_text())

    def _status_text(self) -> str:
        if self._comic.read_status == "read":
            return "Read"
        if self._comic.read_status == "in_progress" and self._comic.page_count > 0:
            return f"Page {self._comic.current_page + 1} of {self._comic.page_count}"
        return "Not read"

    def _draw_progress(self, painter: QPainter) -> None:
        if self._comic.read_status == "unread" or self._comic.page_count <= 0:
            return
        ratio = 1.0 if self._comic.read_status == "read" else min(1.0, self._comic.current_page / self._comic.page_count)
        y = COVER_H - PROGRESS_H
        painter.fillRect(0, y, TILE_W, PROGRESS_H, _PROGRESS_TRACK)
        painter.fillRect(0, y, int(TILE_W * ratio), PROGRESS_H, _PROGRESS_FILL)

    def _draw_selection(self, painter: QPainter) -> None:
        fill = QColor(_PROGRESS_FILL.red(), _PROGRESS_FILL.green(), _PROGRESS_FILL.blue(), 50)
        painter.fillRect(0, 0, TILE_W, COVER_H, fill)
        pen = QPen(_PROGRESS_FILL, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(2, 2, TILE_W - 4, TILE_H - 4, 7, 7)

    def _on_click(self):
        self.opened.emit(self._comic.file_path)


class SeriesTile(_Tile):
    series_opened = pyqtSignal(str, str)    # folder_path, series_name
    ungroup_requested = pyqtSignal(str, str)

    def __init__(self, series: Series, parent=None):
        super().__init__(parent)
        self._series = series
        self._load_pixmap(series.cover_path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        self._draw_series_badge(painter)
        self._draw_title(painter, self._series.name)
        n = self._series.comic_count
        self._draw_status(painter, f"{n} issue{'s' if n != 1 else ''}")

    def _draw_series_badge(self, painter: QPainter) -> None:
        badge_w, badge_h = 28, 18
        x, y = TILE_W - badge_w - 6, 6
        painter.fillRect(x, y, badge_w, badge_h, QColor(0, 0, 0, 140))
        painter.setPen(QColor(255, 255, 255, 200))
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        painter.drawText(x, y, badge_w, badge_h, Qt.AlignmentFlag.AlignCenter,
                         str(self._series.comic_count))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Ungroup series").triggered.connect(
            lambda: self.ungroup_requested.emit(self._series.folder_path, self._series.name)
        )
        menu.exec(event.globalPos())

    def _on_click(self):
        self.series_opened.emit(self._series.folder_path, self._series.name)


class _HeaderBar(QWidget):
    back_clicked = pyqtSignal()
    search_changed = pyqtSignal(str)
    sort_changed = pyqtSignal(str, str)  # sort_by, order

    _SORT_OPTIONS = [
        ("Title A–Z",      "title",      "asc"),
        ("Title Z–A",      "title",      "desc"),
        ("Recently Added", "date_added", "desc"),
        ("Last Read",      "last_read",  "desc"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(56)
        self.setStyleSheet(
            "#HeaderBar { background: #ecdede; border: none; }"
        )
        self._in_comic_view = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self._title = QLabel("Library")
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(22)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)
        self._title.setStyleSheet("background: transparent; color: #2a1818;")
        layout.addWidget(self._title)
        layout.addStretch()

        self._sort_combo = QComboBox()
        for label, _, _ in self._SORT_OPTIONS:
            self._sort_combo.addItem(label)
        self._sort_combo.setFixedWidth(150)
        self._sort_combo.setStyleSheet(
            "QComboBox::drop-down { border: none; width: 20px; }"
        )
        self._sort_combo.currentIndexChanged.connect(self._emit_sort)
        self._sort_combo.hide()
        layout.addWidget(self._sort_combo)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search title, series, author, folder…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedWidth(250)
        self._search_input.setStyleSheet("QLineEdit:focus { border-color: #8b2a2a; }")
        self._search_input.textChanged.connect(self.search_changed)
        self._search_input.hide()
        self._search_input.installEventFilter(self)
        layout.addWidget(self._search_input)

        self._search_btn = QPushButton("⌕")
        self._search_btn.setFlat(True)
        self._search_btn.setToolTip("Search")
        self._search_btn.setFixedSize(34, 34)
        self._search_btn.setStyleSheet(
            "QPushButton { color: #7a5858; border: none; font-size: 18px;"
            " font-family: 'Libre Baskerville'; background: transparent; }"
            "QPushButton:hover { color: #2a1818; }"
        )
        self._search_btn.clicked.connect(self._toggle_search)
        layout.addWidget(self._search_btn)

    def _emit_sort(self, idx: int):
        _, sort_by, order = self._SORT_OPTIONS[idx]
        self.sort_changed.emit(sort_by, order)

    def eventFilter(self, obj, event):
        if obj is self._search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._close_search()
                return True
        return super().eventFilter(obj, event)

    def _toggle_search(self):
        if self._search_input.isVisible():
            self._close_search()
        else:
            self._open_search()

    def _open_search(self):
        self._sort_combo.hide()
        self._search_input.show()
        self._search_input.setFocus()
        self._search_btn.setText("✕")
        self._search_btn.setToolTip("Close search")

    def _close_search(self):
        self._search_input.hide()
        self._search_input.clear()
        self._search_btn.setText("⌕")
        self._search_btn.setToolTip("Search")
        if self._in_comic_view:
            self._sort_combo.show()

    def set_folder_mode(self):
        self._in_comic_view = False
        self._title.setText("Library")
        self._sort_combo.hide()
        if self._search_input.isVisible():
            self._search_input.hide()
            self._search_btn.setText("⌕")

    def set_comic_mode(self, folder_name: str):
        self._in_comic_view = True
        self._title.setText(folder_name)
        if not self._search_input.isVisible():
            self._sort_combo.show()

    def set_series_mode(self, series_name: str):
        self._in_comic_view = True
        self._title.setText(series_name)
        if not self._search_input.isVisible():
            self._sort_combo.hide()  # series always sorted by issue number

    def set_shelf_mode(self, shelf_name: str):
        self._in_comic_view = True
        self._title.setText(shelf_name)
        if not self._search_input.isVisible():
            self._sort_combo.show()

    def set_search_mode(self):
        self._title.setText("Search Results")
        if not self._search_input.isVisible():
            self._sort_combo.show()

    def clear_search(self) -> None:
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        if self._search_input.isVisible():
            self._search_input.hide()
            self._search_btn.setText("⌕")
            self._search_btn.setToolTip("Search")

    def apply_theme(self, c: dict):
        self.setStyleSheet(
            f"#HeaderBar {{ background: {c['header_bg']}; border: none; }}"
        )
        self._title.setStyleSheet(f"background: transparent; color: {c['text']};")
        self._search_input.setStyleSheet(f"QLineEdit:focus {{ border-color: {c['accent']}; }}")
        self._search_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text_secondary']}; border: none; font-size: 18px;"
            f" font-family: 'Libre Baskerville'; background: transparent; }}"
            f"QPushButton:hover {{ color: {c['text']}; }}"
        )


class BookshelfView(QWidget):
    comic_opened = pyqtSignal(str)
    folder_entered = pyqtSignal(bool)
    shelf_changed = pyqtSignal()            # emitted when shelf membership changes
    folder_rescan_requested = pyqtSignal(str)  # folder_path

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._current_folder: str | None = None
        self._current_shelf_id: int | None = None
        self._current_shelf_name: str = ""
        self._current_series_name: str | None = None
        self._last_n_cols = 0

        self._selected_ids: set[int] = set()
        self._comic_tiles: dict[int, ComicTile] = {}

        self._sort_by = "title"
        self._sort_order = "asc"
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder: str | None = None
        self._pre_search_shelf_id: int | None = None
        self._pre_search_shelf_name: str = ""

        self.setStyleSheet("background-color: #f0e8e8;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _HeaderBar()
        self._header.back_clicked.connect(self._on_back_clicked)
        self._header.search_changed.connect(self._on_search_changed)
        self._header.sort_changed.connect(self._on_sort_changed)
        root.addWidget(self._header)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: #f0e8e8; }")
        root.addWidget(self._scroll)

        self._nav_overlay = QLabel(self._scroll)
        self._nav_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._nav_overlay.hide()
        self._nav_opacity = QGraphicsOpacityEffect(self._nav_overlay)
        self._nav_overlay.setGraphicsEffect(self._nav_opacity)
        self._nav_anim = QPropertyAnimation(self._nav_opacity, b"opacity", self)
        self._nav_anim.setDuration(300)
        self._nav_anim.setStartValue(1.0)
        self._nav_anim.setEndValue(0.0)
        self._nav_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._nav_anim.finished.connect(self._nav_overlay.hide)

        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet(f"background: {_BG.name()};")
        self._scroll.setWidget(self._grid_widget)

        self._show_folders()

    def focus_search(self) -> None:
        self._header._search_input.setFocus()
        self._header._search_input.selectAll()

    def refresh(self):
        """Reload from library — call after scanning or returning from reader."""
        self._last_n_cols = 0
        self._repopulate()

    def _on_back_clicked(self):
        if self._current_series_name is not None:
            self._show_comics(self._current_folder)
            return
        if self._current_shelf_id is not None and self._current_folder is not None:
            self._show_shelf_folders()
            return
        self._search_timer.stop()
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder = None
        self._pre_search_shelf_id = None
        self._pre_search_shelf_name = ""
        self._header.clear_search()
        self._show_folders()

    def _on_search_changed(self, text: str):
        query = text.strip()
        if query and not self._in_search:
            self._in_search = True
            self._pre_search_folder = self._current_folder
            self._pre_search_shelf_id = self._current_shelf_id
            self._pre_search_shelf_name = self._current_shelf_name
        self._search_query = query
        self._search_timer.start()

    def _apply_search(self):
        if self._search_query:
            self._header.set_search_mode()
            self._repopulate()
        else:
            self._in_search = False
            folder = self._pre_search_folder
            shelf_id = self._pre_search_shelf_id
            shelf_name = self._pre_search_shelf_name
            self._pre_search_folder = None
            self._pre_search_shelf_id = None
            self._pre_search_shelf_name = ""
            if folder is not None:
                self._show_comics(folder)
            elif shelf_id is not None:
                self.show_shelf(shelf_id, shelf_name)
            else:
                self._show_folders()

    def _open_folder_from_search(self, folder_path: str):
        self._search_timer.stop()
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder = None
        self._header.clear_search()
        self._show_comics(folder_path)

    def _on_sort_changed(self, sort_by: str, order: str):
        self._sort_by = sort_by
        self._sort_order = order
        self._repopulate()

    def _nav_transition(self, switch_fn):
        """Grab current grid, run switch_fn, fade the grab out."""
        grab = self._scroll.grab()
        switch_fn()
        vp = self._scroll.viewport()
        self._nav_overlay.setPixmap(grab)
        self._nav_overlay.setGeometry(0, 0, vp.width(), vp.height())
        self._nav_overlay.show()
        self._nav_overlay.raise_()
        self._nav_anim.stop()
        self._nav_opacity.setOpacity(1.0)
        self._nav_anim.start()

    def go_to_root(self):
        self._on_back_clicked()

    def apply_theme(self, c: dict):
        global _BG, _COVER_BG, _TITLE_FG, _STATUS_FG, _HOVER_OVERLAY
        global _PROGRESS_TRACK, _PROGRESS_FILL, _PLACEHOLDER_FG
        _BG = QColor(c["tile_bg"])
        _COVER_BG = QColor(c["cover_bg"])
        _TITLE_FG = QColor(c["text"])
        _STATUS_FG = QColor(c["text_secondary"])
        r, g, b, a = c["hover_overlay"]
        _HOVER_OVERLAY = QColor(r, g, b, a)
        _PROGRESS_TRACK = QColor(c["progress_track"])
        _PROGRESS_FILL = QColor(c["progress_fill"])
        _PLACEHOLDER_FG = QColor(c["placeholder_fg"])
        self.setStyleSheet(f"background-color: {c['tile_bg']};")
        self._scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {c['tile_bg']}; }}")
        self._header.apply_theme(c)
        self.refresh()

    def _show_folders(self):
        def do():
            self._current_folder = None
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._current_series_name = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_folder_mode()
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def _show_comics(self, folder_path: str):
        def do():
            self._current_folder = folder_path
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._current_series_name = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_comic_mode(Path(folder_path).name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(True)
        self._nav_transition(do)

    def show_shelf(self, shelf_id: int, shelf_name: str):
        def do():
            self._current_folder = None
            self._current_shelf_id = shelf_id
            self._current_shelf_name = shelf_name
            self._current_series_name = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_shelf_mode(shelf_name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def show_series(self, folder_path: str, series_name: str):
        def do():
            self._current_folder = folder_path
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._current_series_name = series_name
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_series_mode(series_name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(True)
        self._nav_transition(do)

    def _show_shelf_folders(self):
        """Return to the shelf's folder-tile grid from a folder drill-down."""
        def do():
            self._current_folder = None
            self._current_series_name = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_shelf_mode(self._current_shelf_name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def _show_shelf_folder(self, folder_path: str):
        """Drill into a specific folder's comics within the current shelf."""
        def do():
            self._current_folder = folder_path
            self._current_series_name = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_comic_mode(Path(folder_path).name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(True)
        self._nav_transition(do)

    def _clear_selection(self):
        old = set(self._selected_ids)
        self._selected_ids.clear()
        for cid in old:
            if cid in self._comic_tiles:
                self._comic_tiles[cid].set_selected(False)
                self._comic_tiles[cid].update()

    def _n_cols(self) -> int:
        w = self._scroll.viewport().width()
        return max(1, w // (TILE_W + TILE_SPACING))

    def _repopulate(self):
        n_cols = self._n_cols()
        self._last_n_cols = n_cols

        old = self._grid_widget
        old.hide()
        old.deleteLater()

        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet(f"background: {_BG.name()};")
        self._scroll.setWidget(self._grid_widget)

        layout = QVBoxLayout(self._grid_widget)
        layout.setContentsMargins(TILE_SPACING, TILE_SPACING, TILE_SPACING, TILE_SPACING)
        layout.setSpacing(0)

        self._comic_tiles.clear()

        if self._search_query:
            folders, comics = self._library.search_library(
                self._search_query, self._sort_by, self._sort_order
            )
            items = folders + comics
            empty_msg = f'No results for "{self._search_query}"'
        elif self._current_shelf_id is not None and self._current_folder is None:
            # Shelf top level — show folders that have comics on this shelf
            items = self._library.get_shelf_folders(self._current_shelf_id)
            empty_msg = "This shelf is empty."
        elif self._current_shelf_id is not None:
            # Shelf drill-down — show comics from this folder that are on the shelf
            items = self._library.get_comics_in_shelf_for_folder(
                self._current_shelf_id, self._current_folder,
                sort_by=self._sort_by, order=self._sort_order,
            )
            empty_msg = "No comics from this folder on this shelf."
        elif self._current_series_name is not None:
            items = self._library.get_comics_in_series(
                self._current_folder, self._current_series_name
            )
            empty_msg = "No comics found in this series."
        elif self._current_folder is None:
            items = self._library.get_folders()
            empty_msg = "No comics yet.\nUse Library → Add Folder to Library to get started."
        else:
            # Folder view: series groups first, then ungrouped comics
            series_list = self._library.get_series_in_folder(self._current_folder)
            grouped_names = {s.name for s in series_list}
            all_comics = self._library.get_comics_in_folder(
                self._current_folder, sort_by=self._sort_by, order=self._sort_order
            )
            ungrouped = [c for c in all_comics if not c.series or c.series not in grouped_names]
            items = series_list + ungrouped
            empty_msg = "No comics found in this folder."

        if not items:
            lbl = QLabel(empty_msg)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #7a5858;")
            layout.addWidget(lbl)
            layout.addStretch()
            return

        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None

        for i, item in enumerate(items):
            if i % n_cols == 0:
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, TILE_SPACING)
                row_layout.setSpacing(TILE_SPACING)
                layout.addWidget(row_widget)

            if isinstance(item, Folder):
                tile = FolderTile(item)
                if self._search_query:
                    tile.opened.connect(self._open_folder_from_search)
                elif self._current_shelf_id is not None:
                    tile.opened.connect(self._show_shelf_folder)
                else:
                    tile.opened.connect(self._show_comics)
                tile.rescan_requested.connect(self.folder_rescan_requested)
            elif isinstance(item, Series):
                tile = SeriesTile(item)
                tile.series_opened.connect(self.show_series)
                tile.ungroup_requested.connect(self._ungroup_series)
            else:
                tile = ComicTile(item, selected=item.id in self._selected_ids)
                tile.opened.connect(self.comic_opened)
                tile.select_toggled.connect(self._toggle_selection)
                tile.shelf_action_requested.connect(self._on_comic_context_menu)
                self._comic_tiles[item.id] = tile

            row_layout.addWidget(tile)

        if row_layout is not None:
            row_layout.addStretch()

        layout.addStretch()

    def _on_comic_context_menu(self, comic_id: int, gx: int, gy: int):
        is_multi = comic_id in self._selected_ids and len(self._selected_ids) > 1
        target_ids = list(self._selected_ids) if is_multi else [comic_id]
        n = len(target_ids)

        menu = QMenu(self)

        # Metadata / grouping
        meta_label = f"Edit metadata… ({n} selected)" if is_multi else "Edit metadata…"
        menu.addAction(meta_label).triggered.connect(
            lambda: self._edit_metadata(target_ids)
        )
        if is_multi and self._current_folder:
            menu.addAction("Group as series…").triggered.connect(
                lambda: self._group_as_series(target_ids)
            )
        if self._current_series_name and self._current_folder:
            menu.addAction("Ungroup this series").triggered.connect(
                lambda: self._ungroup_series(self._current_folder, self._current_series_name)
            )

        # Shelf actions
        shelves = self._library.get_shelves()
        manual_shelves = [s for s in shelves if s.kind == "manual"]
        if manual_shelves:
            menu.addSeparator()
            if is_multi:
                add_menu = menu.addMenu(f"Add {n} comics to shelf")
                for shelf in manual_shelves:
                    sid = shelf.id
                    add_menu.addAction(shelf.name).triggered.connect(
                        lambda checked=False, s=sid: [
                            self._library.add_comic_to_shelf(cid, s) for cid in target_ids
                        ] or self.shelf_changed.emit()
                    )
            else:
                comic_shelf_ids = {s.id for s in self._library.get_shelves_for_comic(comic_id)}
                add_menu = menu.addMenu("Add to shelf")
                for shelf in manual_shelves:
                    action = QAction(shelf.name, add_menu)
                    action.setCheckable(True)
                    action.setChecked(shelf.id in comic_shelf_ids)
                    sid = shelf.id
                    action.triggered.connect(
                        lambda checked, cid=comic_id, s=sid: self._toggle_comic_in_shelf(cid, s, checked)
                    )
                    add_menu.addAction(action)

        if self._current_shelf_id is not None:
            shelf_obj = next((s for s in shelves if s.id == self._current_shelf_id), None)
            if shelf_obj and shelf_obj.kind == "manual":
                menu.addSeparator()
                menu.addAction("Remove from this shelf").triggered.connect(
                    lambda: self._remove_comics_from_current_shelf(target_ids)
                )

        menu.exec(QPoint(gx, gy))

    def _toggle_selection(self, comic_id: int):
        if comic_id in self._selected_ids:
            self._selected_ids.discard(comic_id)
        else:
            self._selected_ids.add(comic_id)
        if comic_id in self._comic_tiles:
            self._comic_tiles[comic_id].set_selected(comic_id in self._selected_ids)
            self._comic_tiles[comic_id].update()

    def _toggle_comic_in_shelf(self, comic_id: int, shelf_id: int, add: bool):
        if add:
            self._library.add_comic_to_shelf(comic_id, shelf_id)
        else:
            self._library.remove_comic_from_shelf(comic_id, shelf_id)
        self.shelf_changed.emit()

    def _remove_comics_from_current_shelf(self, comic_ids: list[int]):
        if self._current_shelf_id is not None:
            for cid in comic_ids:
                self._library.remove_comic_from_shelf(cid, self._current_shelf_id)
            self._repopulate()
            self.shelf_changed.emit()

    def _edit_metadata(self, comic_ids: list[int]):
        from metadata_editor import MetadataDialog
        comics = [c for cid in comic_ids if (c := self._library.get_comic_by_id(cid))]
        if not comics:
            return
        dlg = MetadataDialog(comics, self)
        if dlg.exec():
            changes = dlg.get_changes()
            if changes:
                for comic in comics:
                    self._library.update_metadata(comic.id, **changes)
                self._clear_selection()
                self._repopulate()

    def _group_as_series(self, comic_ids: list[int]):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Group as Series", "Series name:")
        if ok and name.strip():
            for cid in comic_ids:
                self._library.update_metadata(cid, series=name.strip())
            self._clear_selection()
            self._repopulate()

    def _ungroup_series(self, folder_path: str, series_name: str):
        comics = self._library.get_comics_in_series(folder_path, series_name)
        for c in comics:
            self._library.update_metadata(c.id, series=None)
        if self._current_series_name == series_name:
            self._show_comics(folder_path)
        else:
            self._repopulate()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._n_cols() != self._last_n_cols:
            self._repopulate()
