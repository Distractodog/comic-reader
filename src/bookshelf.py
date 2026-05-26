"""Bookshelf library view — two-level browser: folder grid → comic grid."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QEasingCurve, QEvent, QPropertyAnimation, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from library import Comic, Folder, Library

TILE_W = 200
COVER_H = 300
TITLE_H = 28
STATUS_H = 22
TILE_H = COVER_H + TITLE_H + STATUS_H
TILE_SPACING = 18
PROGRESS_H = 3  # overlaid on bottom of cover

_BG = QColor("#0d0d0d")
_COVER_BG = QColor("#1a1a1a")
_TITLE_FG = QColor("#ffffff")
_STATUS_FG = QColor("#777777")
_HOVER_OVERLAY = QColor(255, 255, 255, 18)
_PROGRESS_TRACK = QColor("#333333")
_PROGRESS_FILL = QColor("#4a9eff")
_PLACEHOLDER_FG = QColor("#333333")


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

    def _on_click(self):
        self.opened.emit(self._folder.path)


class ComicTile(_Tile):
    def __init__(self, comic: Comic, parent=None):
        super().__init__(parent)
        self._comic = comic
        self._load_pixmap(comic.cover_path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        self._draw_progress(painter)
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

    def _on_click(self):
        self.opened.emit(self._comic.file_path)


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
        self.setFixedHeight(56)
        self.setStyleSheet(
            "#HeaderBar { background: #111111; border-bottom: 1px solid #222222; }"
        )
        self._in_comic_view = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self._back_btn = QPushButton("← Library")
        self._back_btn.setFlat(True)
        self._back_btn.setStyleSheet("color: #4a9eff; padding: 4px 8px;")
        self._back_btn.clicked.connect(self.back_clicked)
        self._back_btn.hide()
        layout.addWidget(self._back_btn)

        self._title = QLabel("Library")
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(22)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)
        self._title.setStyleSheet("color: #ffffff;")
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
        self._search_input.setStyleSheet("QLineEdit:focus { border-color: #4a9eff; }")
        self._search_input.textChanged.connect(self.search_changed)
        self._search_input.hide()
        self._search_input.installEventFilter(self)
        layout.addWidget(self._search_input)

        self._search_btn = QPushButton("⌕")
        self._search_btn.setFlat(True)
        self._search_btn.setToolTip("Search")
        self._search_btn.setFixedSize(34, 34)
        self._search_btn.setStyleSheet(
            "QPushButton { color: #777; border: none; font-size: 18px;"
            " font-family: 'Libre Baskerville'; background: transparent; }"
            "QPushButton:hover { color: #fff; }"
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
        self._back_btn.hide()
        self._title.setText("Library")
        self._sort_combo.hide()
        if self._search_input.isVisible():
            self._search_input.hide()
            self._search_btn.setText("⌕")

    def set_comic_mode(self, folder_name: str):
        self._in_comic_view = True
        self._back_btn.show()
        self._title.setText(folder_name)
        if not self._search_input.isVisible():
            self._sort_combo.show()

    def set_search_mode(self):
        self._back_btn.hide()
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


class BookshelfView(QWidget):
    comic_opened = pyqtSignal(str)

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._current_folder: str | None = None
        self._last_n_cols = 0

        self._sort_by = "title"
        self._sort_order = "asc"
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder: str | None = None

        self.setStyleSheet("background-color: #0d0d0d;")

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
        self._scroll.setStyleSheet("QScrollArea { border: none; background: #0d0d0d; }")
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
        self._grid_widget.setStyleSheet("background: #0d0d0d;")
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
        self._search_timer.stop()
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder = None
        self._header.clear_search()
        self._show_folders()

    def _on_search_changed(self, text: str):
        query = text.strip()
        if query and not self._in_search:
            self._in_search = True
            self._pre_search_folder = self._current_folder
        self._search_query = query
        self._search_timer.start()

    def _apply_search(self):
        if self._search_query:
            self._header.set_search_mode()
            self._repopulate()
        else:
            self._in_search = False
            folder = self._pre_search_folder
            self._pre_search_folder = None
            if folder is not None:
                self._show_comics(folder)
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

    def _show_folders(self):
        def do():
            self._current_folder = None
            self._header.set_folder_mode()
            self._last_n_cols = 0
            self._repopulate()
        self._nav_transition(do)

    def _show_comics(self, folder_path: str):
        def do():
            self._current_folder = folder_path
            self._header.set_comic_mode(Path(folder_path).name)
            self._last_n_cols = 0
            self._repopulate()
        self._nav_transition(do)

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
        self._grid_widget.setStyleSheet("background: #0d0d0d;")
        self._scroll.setWidget(self._grid_widget)

        layout = QVBoxLayout(self._grid_widget)
        layout.setContentsMargins(TILE_SPACING, TILE_SPACING, TILE_SPACING, TILE_SPACING)
        layout.setSpacing(0)

        if self._search_query:
            folders, comics = self._library.search_library(
                self._search_query, self._sort_by, self._sort_order
            )
            items = folders + comics
            empty_msg = f'No results for "{self._search_query}"'
        elif self._current_folder is None:
            items = self._library.get_folders()
            empty_msg = "No comics yet.\nUse Library → Add Folder to Library to get started."
        else:
            items = self._library.get_comics_in_folder(
                self._current_folder, sort_by=self._sort_by, order=self._sort_order
            )
            empty_msg = "No comics found in this folder."

        if not items:
            lbl = QLabel(empty_msg)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #666;")
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
                else:
                    tile.opened.connect(self._show_comics)
            else:
                tile = ComicTile(item)
                tile.opened.connect(self.comic_opened)

            row_layout.addWidget(tile)

        if row_layout is not None:
            row_layout.addStretch()

        layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._n_cols() != self._last_n_cols:
            self._repopulate()
