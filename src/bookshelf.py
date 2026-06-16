"""Bookshelf library view — two-level browser: folder grid → comic grid."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import (
    Qt,
    QEasingCurve,
    QEvent,
    QMimeData,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QSettings,
    QRect,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from batch_tools import BatchPlan, BatchWorker, plan_convert_to_cbz, plan_rename_from_metadata
from app_info import app_settings
from library import Comic, Folder, Library, ReadingSettings, Shelf
import themes


def _hex_to_rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


TILE_W = 200
COVER_H = 300
_TITLE_FONT_SIZE = 14

_TILE_SCALES = {"small": 164, "medium": 200, "large": 248}


def set_tile_scale(name: str) -> None:
    """Adjust the module-level tile dimensions. Rebuild tiles after calling.

    Tiles read ``TILE_W`` / ``COVER_H`` at construct + paint time, so updating
    these globals and refreshing the bookshelf reflows everything.
    """
    global TILE_W, COVER_H
    TILE_W = _TILE_SCALES.get(name, 200)
    COVER_H = int(TILE_W * 1.5)
_TITLE_PAD = 4
STATUS_H = 22
TILE_SPACING = 18
PROGRESS_H = 3  # overlaid on bottom of cover

_BG = QColor("#f0e8e8")
_COVER_BG = QColor("#d8cccc")
_TITLE_FG = QColor("#2a1818")
_STATUS_FG = QColor("#7a5858")
_HOVER_OVERLAY = QColor(100, 30, 30, 22)
_HOVER_OUTLINE = QColor("#8b2a2a")
_PROGRESS_TRACK = QColor("#c4aeae")
_PROGRESS_FILL = QColor("#8b2a2a")
_PLACEHOLDER_FG = QColor("#b0a0a0")
_DRAG_THRESHOLD = 12
_COMIC_FILE_MIME = "application/x-comic-reader-file-id"  # dragging a loose comic onto a shelf

# Per-view bookshelf backgrounds — stored as one JSON map (paths must not be QSettings keys).
_BG_MAP_KEY = "bookshelf/background_map"
_BG_LEGACY_KEY = "bookshelf/background"
_BG_OLD_GROUP = "bookshelf/backgrounds"  # prior broken per-key storage — removed on migrate
_BG_DIM_ALPHA = 198  # theme tile-bg painted over the cover at this alpha to mute it


def _title_font() -> QFont:
    font = QFont()
    font.setPixelSize(_TITLE_FONT_SIZE)
    font.setWeight(QFont.Weight.Medium)
    return font


def _transparent(widget: QWidget) -> None:
    """Let a QWidget show whatever is painted behind it (e.g. the bookshelf bg)."""
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAutoFillBackground(False)
    widget.setStyleSheet("background: transparent;")


_TITLE_MAX_LINES = 2


def _title_height_for(text: str) -> int:
    """Fixed two-line title block so every tile is the same height and tops align."""
    fm = QFontMetrics(_title_font())
    return fm.lineSpacing() * _TITLE_MAX_LINES + _TITLE_PAD


def _fit_two_lines(text: str) -> str:
    """Truncate with an ellipsis so the word-wrapped title fills at most two lines."""
    fm = QFontMetrics(_title_font())
    width = TILE_W - 8
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)
    two_line_h = fm.lineSpacing() * _TITLE_MAX_LINES + 1
    if fm.boundingRect(0, 0, width, 10000, flags, text).height() <= two_line_h:
        return text
    # Binary-search the longest prefix that still fits two lines with an ellipsis.
    best = "…"
    lo, hi = 0, len(text)
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + "…"
        if fm.boundingRect(0, 0, width, 10000, flags, candidate).height() <= two_line_h:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _title_block(text: str) -> tuple[str, int]:
    """Return the displayed title and its rendered pixel height (1 or 2 lines)."""
    fm = QFontMetrics(_title_font())
    width = TILE_W - 8
    display = _fit_two_lines(text)
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)
    return display, fm.boundingRect(0, 0, width, 10000, flags, display).height()


class _CoverLoader(QThread):
    """Decodes and scales cover images off the UI thread, emitting each as it's ready.

    QPixmap can't be created off the GUI thread, so the worker emits a QImage and
    the main thread converts it. A generation counter lets stale results (from a
    grid that's already been rebuilt) be dropped cheaply.
    """

    cover_ready = pyqtSignal(int, int, QImage)  # gen, tile_index, scaled image

    def __init__(self, jobs: list[tuple[int, str]], gen: int, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._gen = gen
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self):
        for index, path in self._jobs:
            if self._abort:
                return
            img = QImage(path)
            if img.isNull():
                continue
            scaled = img.scaled(
                TILE_W, COVER_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.cover_ready.emit(self._gen, index, scaled)


class _Tile(QWidget):
    """Shared base for FolderTile and ComicTile."""

    opened = pyqtSignal(str)

    def __init__(self, title_text: str = "", parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.cover_path: str | None = None
        self._hovered = False
        self._title_h = _title_height_for(title_text)
        self._tile_h = COVER_H + self._title_h + STATUS_H
        self.setFixedSize(TILE_W, self._tile_h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def apply_cover(self, pixmap: QPixmap) -> None:
        """Set the (already-scaled) cover pixmap — called from the cover loader slot."""
        self._pixmap = pixmap
        self.update()

    def _draw_cover(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(0.0, 0.0, float(TILE_W), float(self._tile_h), 8.0, 8.0)
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

    def _draw_hover_outline(self, painter: QPainter) -> None:
        """Outline the whole tile on hover (replaces the old cover-only tint)."""
        if not self._hovered:
            return
        pen = QPen(_HOVER_OUTLINE, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, TILE_W - 2, self._tile_h - 2, 8, 8)

    def _draw_title(self, painter: QPainter, text: str) -> int:
        """Draw the title; return the Y coordinate for the status line below it."""
        painter.setPen(_TITLE_FG)
        painter.setFont(_title_font())
        display, text_h = _title_block(text)
        title_y = COVER_H + 2
        painter.drawText(
            QRect(4, title_y, TILE_W - 8, text_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            display,
        )
        return title_y + text_h + 2

    def _draw_status(self, painter: QPainter, text: str, y: int) -> None:
        painter.setPen(_STATUS_FG)
        font = painter.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(
            QRect(4, y, TILE_W - 8, STATUS_H - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
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
    menu_requested = pyqtSignal(str, int, int)  # folder_path, global_x, global_y

    def __init__(self, folder: Folder, parent=None):
        super().__init__(folder.name, parent)
        self._folder = folder
        self.cover_path = folder.cover_path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        status_y = self._draw_title(painter, self._folder.name)
        n = self._folder.comic_count
        self._draw_status(painter, f"{n} comic{'s' if n != 1 else ''}", status_y)
        self._draw_hover_outline(painter)

    def contextMenuEvent(self, event):
        self.menu_requested.emit(
            self._folder.path, event.globalPos().x(), event.globalPos().y()
        )

    def _on_click(self):
        self.opened.emit(self._folder.path)


class ComicTile(_Tile):
    shelf_action_requested = pyqtSignal(int, int, int)  # comic_id, global_x, global_y
    select_toggled = pyqtSignal(int)                    # comic_id

    def __init__(self, comic: Comic, selected: bool = False, parent=None):
        title = comic.title or Path(comic.file_path).stem
        super().__init__(title, parent)
        self._comic = comic
        self._selected = selected
        self.cover_path = comic.cover_path
        self._file_drag_enabled = False
        self._press_pos: QPoint | None = None
        self._drag_started = False

    def set_selected(self, selected: bool):
        self._selected = selected

    def set_file_drag_enabled(self, enabled: bool) -> None:
        """Let a loose comic be dragged onto a shelf tile to file it (home grid)."""
        self._file_drag_enabled = enabled

    def contextMenuEvent(self, event):
        self.shelf_action_requested.emit(
            self._comic.id, event.globalPos().x(), event.globalPos().y()
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            mods = event.modifiers()
            if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                self.select_toggled.emit(self._comic.id)
                return
            if self._file_drag_enabled:
                self._press_pos = event.position().toPoint()
                self._drag_started = False
                return
            self._on_click()

    def mouseMoveEvent(self, event):
        if (
            self._file_drag_enabled
            and self._press_pos is not None
            and not self._drag_started
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            delta = event.position().toPoint() - self._press_pos
            if delta.manhattanLength() >= _DRAG_THRESHOLD:
                self._start_file_drag()
                return
        if not self._file_drag_enabled:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._file_drag_enabled
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if not self._drag_started and not ComicTile._is_deleted(self):
                self._on_click()
            self._press_pos = None
            self._drag_started = False
            return
        super().mouseReleaseEvent(event)

    def _start_file_drag(self) -> None:
        self._drag_started = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(_COMIC_FILE_MIME, str(self._comic.id).encode())
        drag.setMimeData(mime)
        if self._pixmap and not self._pixmap.isNull():
            thumb = self._pixmap.scaled(
                100, 150,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            drag.setPixmap(thumb)
            drag.setHotSpot(QPoint(thumb.width() // 2, thumb.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    @staticmethod
    def _is_deleted(widget: QWidget) -> bool:
        try:
            from PyQt6 import sip
            return sip.isdeleted(widget)
        except Exception:
            return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        self._draw_progress(painter)
        if self._selected:
            self._draw_selection(painter)
        title = self._comic.title or Path(self._comic.file_path).stem
        status_y = self._draw_title(painter, title)
        self._draw_status(painter, self._status_text(), status_y)
        self._draw_hover_outline(painter)

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
        painter.drawRoundedRect(2, 2, TILE_W - 4, self._tile_h - 4, 7, 7)

    def _on_click(self):
        self.opened.emit(self._comic.file_path)


class ShelfTile(_Tile):
    """A bookshelf shown as a tile on the home grid.

    The cover is the shelf's saved background image (falling back to the cover of
    the first comic on the shelf). Clicking it opens the shelf.
    """

    shelf_opened = pyqtSignal(int, str)             # shelf_id, shelf_name
    menu_requested = pyqtSignal(int, int, int)      # shelf_id, global_x, global_y

    def __init__(self, shelf: Shelf, cover_path: str | None, comic_count: int, parent=None):
        super().__init__(shelf.name, parent)
        self._shelf = shelf
        self.cover_path = cover_path
        self._comic_count = comic_count
        self.setAcceptDrops(True)
        self._drop_highlight = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._draw_cover(painter)
        if self._drop_highlight:
            pen = QPen(_PROGRESS_FILL, 3)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(1, 1, TILE_W - 2, self._tile_h - 2, 8, 8)
        status_y = self._draw_title(painter, self._shelf.name)
        n = self._comic_count
        self._draw_status(painter, f"{n} comic{'s' if n != 1 else ''}", status_y)
        self._draw_hover_outline(painter)

    def contextMenuEvent(self, event):
        self.menu_requested.emit(
            self._shelf.id, event.globalPos().x(), event.globalPos().y()
        )

    # --- accept a loose comic dropped onto the shelf to file it there ---
    def _dropped_comic_id(self, event) -> int | None:
        if not event.mimeData().hasFormat(_COMIC_FILE_MIME):
            return None
        try:
            return int(bytes(event.mimeData().data(_COMIC_FILE_MIME)).decode())
        except (TypeError, ValueError):
            return None

    def dragEnterEvent(self, event):
        if self._dropped_comic_id(event) is not None:
            event.acceptProposedAction()
            self._drop_highlight = True
            self.update()

    def dragMoveEvent(self, event):
        if self._dropped_comic_id(event) is not None:
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        if self._drop_highlight:
            self._drop_highlight = False
            self.update()

    def dropEvent(self, event):
        cid = self._dropped_comic_id(event)
        self._drop_highlight = False
        self.update()
        if cid is not None:
            self.file_comic_requested.emit(cid, self._shelf.id)
            event.acceptProposedAction()

    file_comic_requested = pyqtSignal(int, int)  # comic_id, shelf_id

    def _on_click(self):
        self.shelf_opened.emit(self._shelf.id, self._shelf.name)


HEADER_TITLE_PX = 18


def header_title_font() -> QFont:
    """The header title font, shared so the hamburger/back icons can match it."""
    f = QFont("Libre Baskerville")
    f.setPixelSize(HEADER_TITLE_PX)
    f.setWeight(QFont.Weight.DemiBold)
    return f


def header_title_cap_band() -> int:
    """Height in px of the title's *regular-letter* band: cap/ascender top down
    to the baseline, ignoring descenders (the tail of 'y'). The hamburger and
    back-arrow size their top/bottom edges to this so they line up with the text.
    """
    fm = QFontMetrics(header_title_font())
    # "Lh" = a capital + an ascender, the tallest regular ink; top() is negative
    # (above baseline), baseline is 0, so the band height is just -top().
    return -fm.tightBoundingRect("Lh").top()


class _HeaderBackButton(QPushButton):
    """Back button painted in the same slot as the sidebar rail icons."""

    WIDTH = 60
    HEIGHT = 60
    ICON_W = 16.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor("#ffffff")
        self._hover_color = QColor("#ffffff")
        self.setFlat(True)
        self.setText("")
        self.setToolTip("Back")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Same hover highlight as the sidebar rail icons — flush 60×60 square.
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " border-radius: 0; margin: 0; }"
            "QPushButton:hover { background: rgba(255,255,255,0.08); }"
        )

    def set_colors(self, color: str, hover: str | None = None) -> None:
        self._color = QColor(color)
        self._hover_color = QColor(hover or color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self._hover_color if self.underMouse() else self._color
        pen = QPen(
            color,
            1.3,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.FlatCap,
            Qt.PenJoinStyle.MiterJoin,
        )
        painter.setPen(pen)
        cx = self.width() / 2
        cy = self.height() / 2
        # Height matches the title's regular-letter band so the arrow's top/bottom
        # line up with the cap-top and baseline of the title text beside it.
        icon_h = header_title_cap_band()
        left = cx - self.ICON_W / 2
        right = cx + self.ICON_W / 2
        top = cy - icon_h / 2
        bottom = cy + icon_h / 2
        arm = self.ICON_W * 0.42
        painter.drawLine(
            QPointF(left, cy),
            QPointF(right, cy),
        )
        painter.drawLine(
            QPointF(left, cy),
            QPointF(left + arm, top),
        )
        painter.drawLine(
            QPointF(left, cy),
            QPointF(left + arm, bottom),
        )


class _HeaderTitle(QLabel):
    """Header title painted with explicit vertical centering in the header slot."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._color = QColor("#2a1818")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self.font())
        painter.setPen(self._color)
        fm = QFontMetrics(self.font())
        # Center the *regular-letter* band (cap/ascender top → baseline), ignoring
        # descenders, so the visible top and bottom of the text match the
        # hamburger / back-arrow icons that are sized to the same band.
        cap_band = -fm.tightBoundingRect("Lh").top()
        target_center = self.height() / 2
        baseline = target_center + cap_band / 2
        text = self.text()
        # Inset the text by the same gap the rail icons / back arrow have from the
        # bar edge (carried via contentsMargins so the label reserves the room),
        # and cancel the first glyph's left side-bearing so the *visible* left edge
        # of the text lands exactly at that inset.
        left_bearing = fm.tightBoundingRect(text or "M").left()
        x = self.contentsRect().left() - left_bearing
        painter.drawText(int(round(x)), int(round(baseline)), text)


class _HeaderBar(QWidget):
    back_clicked = pyqtSignal()                        # ← back button (only shown off-root)
    sort_changed = pyqtSignal(str, str)                # sort_by, order
    filter_changed = pyqtSignal(str)                   # '' | recently_read | unread | finished
    options_menu_requested = pyqtSignal(int, int)      # global_x, global_y (3-dot menu)

    _SORT_OPTIONS = [
        ("Title A–Z",      "title",      "asc"),
        ("Title Z–A",      "title",      "desc"),
        ("Recently Added", "date_added", "desc"),
        ("Last Read",      "last_read",  "desc"),
    ]

    _FILTER_OPTIONS = [
        ("Everything",    ""),
        ("Favorites",     "favorite"),
        ("Recently Read", "recently_read"),
        ("Recently Added","recently_added"),
        ("Unread",        "unread"),
        ("Finished",      "finished"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(_HeaderBackButton.HEIGHT)
        self.setStyleSheet(
            "#HeaderBar { background: transparent; border: none; }"
        )
        self.setAutoFillBackground(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 12, 0)
        layout.setSpacing(8)

        # Back button — hidden on the root folder view, shown once you drill in.
        self._back_btn = _HeaderBackButton()
        self._back_btn.clicked.connect(self.back_clicked)
        self._back_btn.hide()
        layout.addWidget(self._back_btn)

        self._title = _HeaderTitle("Library")
        self._title.setFont(header_title_font())
        # Same gap from the bar edge that the rail icons / back arrow have: the
        # arrow is a WIDTH-wide slot with a centred ICON_W glyph, so its inset is
        # (WIDTH - ICON_W) / 2. Give the title that exact left inset.
        title_inset = int((_HeaderBackButton.WIDTH - _HeaderBackButton.ICON_W) / 2)
        self._title.setContentsMargins(title_inset, 0, 0, 0)
        self._title.setFixedHeight(_HeaderBackButton.HEIGHT)
        self._title.set_color("#2a1818")
        layout.addWidget(self._title)
        layout.addStretch()

        self._sort_idx = 0
        self._sort_btn = QPushButton(self._SORT_OPTIONS[0][0] + "  ▾")
        self._sort_btn.setFlat(True)
        self._sort_btn.clicked.connect(self._show_sort_menu)
        layout.addWidget(self._sort_btn)

        self._filter_idx = 0
        self._filter_btn = QPushButton(self._FILTER_OPTIONS[0][0] + "  ▾")
        self._filter_btn.setFlat(True)
        self._filter_btn.clicked.connect(self._show_filter_menu)
        layout.addWidget(self._filter_btn)

        self._options_btn = QPushButton("⋮")
        self._options_btn.setFlat(True)
        self._options_btn.setToolTip("More options")
        self._options_btn.setFixedSize(38, 38)
        self._options_btn.clicked.connect(self._emit_options)
        layout.addWidget(self._options_btn)

        self._apply_btn_styles({})

    def _show_sort_menu(self):
        menu = themes.make_menu(self)
        for idx, (label, _, _) in enumerate(self._SORT_OPTIONS):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(idx == self._sort_idx)
            action.triggered.connect(lambda checked, i=idx: self._select_sort(i))
            menu.addAction(action)
        pos = self._sort_btn.mapToGlobal(QPoint(0, self._sort_btn.height()))
        menu.exec(pos)

    def _select_sort(self, idx: int):
        self._sort_idx = idx
        label, sort_by, order = self._SORT_OPTIONS[idx]
        self._sort_btn.setText(label + "  ▾")
        self.sort_changed.emit(sort_by, order)

    def set_sort_index(self, idx: int):
        self._sort_idx = idx
        self._sort_btn.setText(self._SORT_OPTIONS[idx][0] + "  ▾")

    def _show_filter_menu(self):
        menu = themes.make_menu(self)
        for idx, (label, _) in enumerate(self._FILTER_OPTIONS):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(idx == self._filter_idx)
            action.triggered.connect(lambda checked, i=idx: self._select_filter(i))
            menu.addAction(action)
        pos = self._filter_btn.mapToGlobal(QPoint(0, self._filter_btn.height()))
        menu.exec(pos)

    def _select_filter(self, idx: int):
        self._filter_idx = idx
        label, key = self._FILTER_OPTIONS[idx]
        self._filter_btn.setText(label + "  ▾")
        self.filter_changed.emit(key)

    def _emit_options(self):
        pos = self._options_btn.mapToGlobal(QPoint(0, self._options_btn.height()))
        self.options_menu_requested.emit(pos.x(), pos.y())

    def current_filter_key(self) -> str:
        return self._FILTER_OPTIONS[self._filter_idx][1]

    def set_filter_key(self, key: str) -> None:
        for idx, (label, k) in enumerate(self._FILTER_OPTIONS):
            if k == key:
                self._filter_idx = idx
                self._filter_btn.setText(label + "  ▾")
                break

    # The title just reflects the current view; sort + filter are always available.
    def set_folder_mode(self):
        self._title.setText("Library")
        self._back_btn.hide()

    def set_comic_mode(self, folder_name: str):
        self._title.setText(folder_name)
        self._back_btn.show()

    def set_shelf_mode(self, shelf_name: str):
        self._title.setText(shelf_name)
        self._back_btn.show()

    def set_search_mode(self):
        self._title.setText("Search Results")
        self._back_btn.show()

    def _apply_btn_styles(self, c: dict) -> None:
        text = c.get("text", "#2a1818")
        text_sec = c.get("text_secondary", "#7a5858")
        flat_css = (
            "QPushButton { color: rgba(255,255,255,180); border: none; font-size: 18px;"
            " font-family: 'Libre Baskerville'; font-weight: 600; background: transparent;"
            " padding: 4px 10px; }"
            "QPushButton:hover { color: #ffffff; }"
        )
        self._sort_btn.setStyleSheet(flat_css)
        self._filter_btn.setStyleSheet(flat_css)
        self._options_btn.setStyleSheet(
            f"QPushButton {{ color: {text_sec}; border: none; font-size: 20px;"
            f" font-family: 'Libre Baskerville'; background: transparent; }}"
            f"QPushButton:hover {{ color: {text}; }}"
        )
        self._back_btn.set_colors("#ffffff", "#ffffff")

    def apply_theme(self, c: dict):
        bg = _hex_to_rgba(c["header_bg"], 218)
        self.setStyleSheet(
            f"#HeaderBar {{ background: {bg}; border: none; }}"
        )
        self._title.set_color(c["text"])
        self._apply_btn_styles(c)


class BookshelfView(QWidget):
    comic_opened = pyqtSignal(str)
    folder_entered = pyqtSignal(bool)
    shelf_changed = pyqtSignal()            # emitted when shelf membership changes
    folder_rescan_requested = pyqtSignal(str)  # folder_path
    comics_delete_requested = pyqtSignal(list)  # comic ids — close reader before delete
    sidebar_toggle_requested = pyqtSignal()    # hamburger — expand/collapse sidebar
    rescan_all_requested = pyqtSignal()        # 3-dot menu — refresh whole library
    export_shelf_requested = pyqtSignal(int, str)  # shelf_id, shelf_name

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._current_folder: str | None = None
        self._current_shelf_id: int | None = None
        self._current_shelf_name: str = ""
        self._show_hidden_mode: bool = False
        self._currently_reading_mode: bool = False
        self._status_filter: str = ""   # '' | favorite | recently_read | recently_added | unread | finished
        self._last_n_cols = 0
        self._top_row_color = QColor("#171212")

        self._selected_ids: set[int] = set()
        self._comic_tiles: dict[int, ComicTile] = {}

        # Async cover loading — tiles render placeholders, covers fill in off-thread.
        self._ordered_tiles: list[_Tile] = []
        self._cover_loader: _CoverLoader | None = None
        self._cover_gen: int = 0
        self._batch_thread: QThread | None = None
        self._batch_worker: BatchWorker | None = None
        self._batch_progress: QProgressDialog | None = None

        self._sort_by = "title"
        self._sort_order = "asc"
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder: str | None = None
        self._pre_search_shelf_id: int | None = None
        self._pre_search_shelf_name: str = ""

        self.setAutoFillBackground(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Solid chrome row — same colour as the sidebar rail — so tile_bg / background
        # art never tints the library header (that caused the seam mismatch).
        self._header_chrome = QWidget()
        self._header_chrome.setObjectName("HeaderChrome")
        self._header_chrome.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._header_chrome.setFixedHeight(_HeaderBackButton.HEIGHT)
        chrome_layout = QHBoxLayout(self._header_chrome)
        chrome_layout.setContentsMargins(0, 0, 0, 0)
        chrome_layout.setSpacing(0)

        self._header = _HeaderBar()
        self._header.back_clicked.connect(self._on_back_clicked)
        self._header.sort_changed.connect(self._on_sort_changed)
        self._header.filter_changed.connect(self._on_filter_changed)
        self._header.options_menu_requested.connect(self._on_options_menu)
        self._header.customContextMenuRequested.connect(self._on_header_folder_menu)
        chrome_layout.addWidget(self._header)
        root.addWidget(self._header_chrome)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._apply_search)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAutoFillBackground(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Transparent so the global background (and the view's tile_bg) shows through.
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scroll.viewport().setAutoFillBackground(False)
        # Re-fit the background whenever the viewport resizes — this also catches
        # the scrollbar appearing/disappearing, which doesn't resize the view itself.
        self._scroll.viewport().installEventFilter(self)
        root.addWidget(self._scroll)

        # Fixed background image — content area only (never under the header row).
        self._bg_spec: dict | None = None  # v2 source spec — rendered at display resolution
        self._bg_label = QLabel(self)
        self._bg_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bg_label.setScaledContents(False)
        self._bg_label.hide()

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
        _transparent(self._grid_widget)
        self._scroll.setWidget(self._grid_widget)
        self._bg_label.lower()

        self._migrate_background_cover_paths()
        self._maybe_migrate_background_storage()
        self._migrate_all_background_entries()
        # Defer first paint — grab() during __init__ (before the event loop runs) can
        # stall the Dock bounce on macOS when launched as Cover 2.0.app.
        QTimer.singleShot(0, self._show_folders)
        self.shelf_changed.connect(self._on_shelf_membership_changed)

    def _on_shelf_membership_changed(self) -> None:
        # Refresh on any filing change: a comic added to a shelf must vanish
        # from the home grid / its home folder, and a removal must reappear.
        self._nav_transition(self._repopulate)

    def refresh(self):
        """Reload from library — call after scanning or returning from reader."""
        self._last_n_cols = 0
        self._repopulate()

    def _on_back_clicked(self):
        if self._current_shelf_id is not None and self._current_folder is not None:
            self._show_shelf_folders()
            return
        self._search_timer.stop()
        self._search_query = ""
        self._in_search = False
        self._pre_search_folder = None
        self._pre_search_shelf_id = None
        self._pre_search_shelf_name = ""
        self._show_folders()

    # ----- Search (driven from the sidebar) -----

    def search(self, text: str) -> None:
        """Run a library search — called by the sidebar search field."""
        query = text.strip()
        if query and not self._in_search:
            self._in_search = True
            self._pre_search_folder = self._current_folder
            self._pre_search_shelf_id = self._current_shelf_id
            self._pre_search_shelf_name = self._current_shelf_name
        self._search_query = query
        self._search_timer.start()

    def clear_search(self) -> None:
        """Close search and return to the view that was active before searching."""
        self._search_timer.stop()
        if not self._in_search:
            self._search_query = ""
            return
        self._search_query = ""
        self._apply_search()

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
        self._show_comics(folder_path)

    def _on_sort_changed(self, sort_by: str, order: str):
        self._sort_by = sort_by
        self._sort_order = order
        self._persist_sort()
        self._nav_transition(self._repopulate)

    def _on_filter_changed(self, key: str):
        self._status_filter = key
        self._nav_transition(self._repopulate)

    def _on_options_menu(self, gx: int, gy: int):
        """The top-bar ⋮ menu: refresh actions + bookshelf management for the view."""
        menu = themes.make_menu(self)
        is_home = (
            not self._show_hidden_mode
            and not self._currently_reading_mode and not self._search_query
            and self._current_shelf_id is None and self._current_folder is None
        )
        if is_home:
            menu.addAction("New bookshelf…").triggered.connect(self._create_shelf)
            menu.addSeparator()
        if self._current_folder is not None:
            folder = self._current_folder
            menu.addAction("Rescan this folder").triggered.connect(
                lambda: self.folder_rescan_requested.emit(folder)
            )
        else:
            menu.addAction("Rescan library").triggered.connect(
                self.rescan_all_requested.emit
            )
        menu.addSeparator()
        menu.addAction("Hidden comics").triggered.connect(self.show_hidden)
        menu.addSeparator()
        menu.addAction("Reload view").triggered.connect(
            lambda: self._nav_transition(self._repopulate)
        )
        menu.exec(QPoint(gx, gy))

    # ----- Bookshelf tile helpers -----

    def _shelf_tile_cover(self, shelf: Shelf, comics: list[Comic]) -> str | None:
        """The image a shelf tile shows: custom cover, else background source, else first comic cover."""
        from thumbnails import shelf_cover_path_for
        custom = shelf_cover_path_for(shelf.id)
        if custom.exists():
            return str(custom)
        spec = self._read_background_spec_for_parts(["shelf", shelf.id])
        if spec:
            cid = spec.get("comic_id")
            if cid is not None:
                c = self._library.get_comic_by_id(cid)
                if c and c.cover_path and Path(c.cover_path).exists():
                    return c.cover_path
            path = spec.get("path")
            if path and Path(path).exists():
                return path
        for c in comics:
            if c.cover_path and Path(c.cover_path).exists():
                return c.cover_path
        return None

    def _file_comic_into_shelf(self, comic_id: int, shelf_id: int) -> None:
        self._library.add_comic_to_shelf(comic_id, shelf_id)
        comic = self._library.get_comic_by_id(comic_id)
        shelf = next((s for s in self._library.get_shelves() if s.id == shelf_id), None)
        if comic and shelf:
            self.window().statusBar().showMessage(
                f"Filed “{comic.title or Path(comic.file_path).stem}” into “{shelf.name}”",
                3500,
            )
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()

    def _on_shelf_context_menu(self, shelf_id: int, gx: int, gy: int) -> None:
        shelf = next((s for s in self._library.get_shelves() if s.id == shelf_id), None)
        if shelf is None:
            return
        menu = themes.make_menu(self)
        menu.addAction("Rename bookshelf…").triggered.connect(
            lambda: self._rename_shelf(shelf_id, shelf.name)
        )
        menu.addSeparator()
        menu.addAction("Choose cover image…").triggered.connect(
            lambda: self._set_shelf_cover_from_image(shelf_id)
        )
        from thumbnails import shelf_cover_path_for
        if shelf_cover_path_for(shelf_id).exists():
            menu.addAction("Clear cover image").triggered.connect(
                lambda: self._clear_shelf_cover(shelf_id)
            )
        shelf_comics = self._library.get_comics_in_shelf(shelf_id)
        cover_path = self._shelf_tile_cover(shelf, shelf_comics)
        if cover_path:
            menu.addAction("Set as Library background").triggered.connect(
                lambda checked=False, c=cover_path: self._set_background(c, ["library"])
            )
        menu.addSeparator()
        menu.addAction("Delete bookshelf").triggered.connect(
            lambda: self._delete_shelf(shelf_id, shelf.name)
        )
        menu.exec(QPoint(gx, gy))

    def _create_shelf(self) -> None:
        name, ok = QInputDialog.getText(self, "New Bookshelf", "Bookshelf name:")
        if ok and name.strip():
            self._library.create_shelf(name.strip())
            self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    def _rename_shelf(self, shelf_id: int, current_name: str) -> None:
        name, ok = QInputDialog.getText(
            self, "Rename Bookshelf", "New name:", text=current_name
        )
        if ok and name.strip():
            self._library.rename_shelf(shelf_id, name.strip())
            self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    def _delete_shelf(self, shelf_id: int, name: str) -> None:
        reply = QMessageBox.question(
            self, "Delete Bookshelf",
            f"Delete the bookshelf “{name}”?\n\n"
            "The comics on it are NOT deleted — they return to the home grid as "
            "unsorted comics.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._library.delete_shelf(shelf_id)
            if self._current_shelf_id == shelf_id:
                self._show_folders()
            else:
                self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    # ----- Read-status filter (applies on the home grid and inside shelves) -----

    def _sort_tile_items(self, items: list) -> list:
        """Sort shelf or folder tiles by the current sort setting.

        Tile items only have a name, so date-based sorts fall back to A–Z.
        """
        reverse = self._sort_order == "desc"
        return sorted(items, key=lambda x: (x.name or "").lower(), reverse=reverse)

    def _passes_filter(self, comic: Comic) -> bool:
        f = self._status_filter
        if not f:
            return True
        if f == "unread":
            return comic.read_status == "unread"
        if f == "finished":
            return comic.read_status == "read"
        if f == "recently_read":
            return comic.read_status in ("in_progress", "read") and bool(comic.last_read)
        if f == "recently_added":
            if not comic.date_added:
                return False
            try:
                added = datetime.fromisoformat(comic.date_added.replace("Z", "+00:00"))
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                return added >= cutoff
            except Exception:
                return False
        if f == "favorite":
            return comic.favorite
        return True

    def _filter_comics(self, comics: list[Comic]) -> list[Comic]:
        if not self._status_filter:
            return comics
        return [c for c in comics if self._passes_filter(c)]

    def _shelf_matches_filter(self, shelf_id: int) -> bool:
        """True when a shelf has at least one comic passing the current filter."""
        if not self._status_filter:
            return True
        for c in self._library.get_comics_in_shelf(shelf_id):
            if self._passes_filter(c):
                return True
        return False

    def _home_folder_items(self) -> list[Folder]:
        """Folder tiles for the home grid: folders holding comics not yet filed
        onto any manual shelf, so unsorted comics appear grouped by folder
        (rather than as loose individual tiles) next to the bookshelves.

        Counts reflect unsorted comics only; once a folder's comics are all
        filed onto shelves it disappears from the home grid entirely."""
        folders = self._library.get_unsorted_folders()
        if not self._status_filter:
            return folders
        keep = []
        for f in folders:
            comics = self._library.get_unsorted_comics_in_folder(f.path)
            if any(self._passes_filter(c) for c in comics):
                keep.append(f)
        return keep

    def _folder_sort_settings_key(self, folder_path: str) -> str:
        return f"folder_sort/{folder_path}"

    _GLOBAL_SORT_KEY = "sort/last"

    def _restore_folder_sort(self, folder_path: str) -> None:
        """Apply this folder's saved sort, else the last sort chosen anywhere."""
        valid = {(key, ord_) for _, key, ord_ in self._header._SORT_OPTIONS}
        # "Set chapter order" persists "chapter/asc" but it isn't a dropdown
        # option — allow it through so the chapter order survives a reboot.
        valid.add(("chapter", "asc"))

        def _try(val) -> bool:
            if not isinstance(val, str) or "/" not in val:
                return False
            sort_by, order = val.split("/", 1)
            if (sort_by, order) in valid:
                self._apply_sort(sort_by, order)
                return True
            return False

        if _try(app_settings().value(self._folder_sort_settings_key(folder_path))):
            return
        _try(app_settings().value(self._GLOBAL_SORT_KEY))

    def _save_folder_sort(self, folder_path: str) -> None:
        app_settings().setValue(
            self._folder_sort_settings_key(folder_path),
            f"{self._sort_by}/{self._sort_order}",
        )

    def _persist_sort(self) -> None:
        """Remember the chosen sort globally, and per-folder when inside one.

        The global key is the fallback so the sort sticks across launches even
        for folders/views that never had their own saved preference.
        """
        app_settings().setValue(
            self._GLOBAL_SORT_KEY, f"{self._sort_by}/{self._sort_order}"
        )
        if self._current_folder and not self._show_hidden_mode:
            self._save_folder_sort(self._current_folder)

    def _nav_transition(self, switch_fn):
        """Grab current grid, run switch_fn, fade the grab out."""
        vp = self._scroll.viewport()
        can_animate = self.isVisible() and vp.width() > 0 and vp.height() > 0
        grab = self._scroll.grab() if can_animate else None
        switch_fn()
        self._switch_background()
        if grab is None or grab.isNull():
            return
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
        global _BG, _COVER_BG, _TITLE_FG, _STATUS_FG, _HOVER_OVERLAY, _HOVER_OUTLINE
        global _PROGRESS_TRACK, _PROGRESS_FILL, _PLACEHOLDER_FG
        _BG = QColor(c["tile_bg"])
        _COVER_BG = QColor(c["cover_bg"])
        _TITLE_FG = QColor(c["text"])
        _STATUS_FG = QColor(c["text_secondary"])
        r, g, b, a = c["hover_overlay"]
        _HOVER_OVERLAY = QColor(r, g, b, a)
        _HOVER_OUTLINE = QColor(c["accent"])
        _PROGRESS_TRACK = QColor(c["progress_track"])
        _PROGRESS_FILL = QColor(c["progress_fill"])
        _PLACEHOLDER_FG = QColor(c["placeholder_fg"])
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._header.apply_theme(c)
        self._header_chrome.setStyleSheet(
            "#HeaderChrome { background: transparent; border: none; }"
        )
        self._top_row_color = QColor(c["sidebar_bg"])
        self.update()
        self._apply_background()
        self.refresh()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), _BG)
        p.end()

    def _show_folders(self):
        def do():
            self._current_folder = None
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._show_hidden_mode = False
            self._currently_reading_mode = False
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
            self._show_hidden_mode = False
            self._currently_reading_mode = False
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_comic_mode(Path(folder_path).name)
            self._restore_folder_sort(folder_path)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(True)
        self._nav_transition(do)

    def show_shelf(self, shelf_id: int, shelf_name: str):
        def do():
            self._current_folder = None
            self._current_shelf_id = shelf_id
            self._current_shelf_name = shelf_name
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._show_hidden_mode = False
            self._currently_reading_mode = False
            self._header.set_shelf_mode(shelf_name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def _show_shelf_folders(self):
        """Return to the shelf's folder-tile grid from a folder drill-down."""
        def do():
            self._current_folder = None
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._show_hidden_mode = False
            self._currently_reading_mode = False
            self._header.set_shelf_mode(self._current_shelf_name)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def _show_shelf_folder(self, folder_path: str):
        """Drill into a specific folder's comics within the current shelf."""
        def do():
            self._current_folder = folder_path
            self._show_hidden_mode = False
            self._currently_reading_mode = False
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_comic_mode(Path(folder_path).name)
            self._restore_folder_sort(folder_path)
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(True)
        self._nav_transition(do)

    def show_hidden(self):
        """Show the Hidden view — comics removed from the library, for restoring."""
        def do():
            self._current_folder = None
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._show_hidden_mode = True
            self._currently_reading_mode = False
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_shelf_mode("Hidden")
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def show_currently_reading(self):
        """Show in-progress comics — backs the sidebar 'Currently Reading' icon."""
        def do():
            self._current_folder = None
            self._current_shelf_id = None
            self._current_shelf_name = ""
            self._show_hidden_mode = False
            self._currently_reading_mode = True
            self._selected_ids.clear()
            self._comic_tiles.clear()
            self._header.set_shelf_mode("Currently Reading")
            self._last_n_cols = 0
            self._repopulate()
            self.folder_entered.emit(False)
        self._nav_transition(do)

    def _clear_selection(self):
        old = set(self._selected_ids)
        self._selected_ids.clear()
        for cid in old:
            if cid in self._comic_tiles:
                self._comic_tiles[cid].set_selected(False)
                self._comic_tiles[cid].update()

    def _n_cols(self) -> int:
        w = self._scroll.viewport().width() - 2 * TILE_SPACING
        return max(1, (w + TILE_SPACING) // (TILE_W + TILE_SPACING))

    def _folder_grid_items(self) -> tuple[list, str]:
        """Build the flat tile list for the current folder view.

        Series issues and loose comics are shown together as individual tiles;
        a 'chapter' sort keeps each detected series contiguous and in issue order.
        """
        folder_path = self._current_folder
        assert folder_path is not None

        if self._current_shelf_id is not None:
            comics = self._library.get_comics_in_shelf_for_folder(
                self._current_shelf_id, folder_path,
                sort_by=self._sort_by, order=self._sort_order,
            )
        else:
            # Home folder view shows only unsorted comics — filed comics live
            # solely in their shelf and are gone from the main library.
            comics = self._library.get_unsorted_comics_in_folder(
                folder_path, sort_by=self._sort_by, order=self._sort_order
            )

        return comics, "No comics found in this folder."

    def _sync_header_folder_menu(self) -> None:
        """Allow header right-click folder actions when inside a folder view."""
        enabled = (
            self._current_folder is not None
            and not self._show_hidden_mode
        )
        self._header.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
            if enabled
            else Qt.ContextMenuPolicy.DefaultContextMenu
        )

    def _repopulate(self):
        self._sync_header_folder_menu()
        n_cols = self._n_cols()
        self._last_n_cols = n_cols

        old = self._grid_widget
        old.hide()
        old.deleteLater()

        self._grid_widget = QWidget()
        _transparent(self._grid_widget)
        self._scroll.setWidget(self._grid_widget)
        self._bg_label.lower()
        self._apply_background()

        layout = QVBoxLayout(self._grid_widget)
        layout.setContentsMargins(TILE_SPACING, TILE_SPACING, TILE_SPACING, TILE_SPACING)
        layout.setSpacing(0)

        self._comic_tiles.clear()
        self._stop_cover_loader()
        self._ordered_tiles = []

        if self._show_hidden_mode:
            items = self._filter_comics(self._library.get_hidden_comics(
                sort_by=self._sort_by, order=self._sort_order
            ))
            empty_msg = ("Nothing hidden.\n"
                         "Comics you remove from the library appear here.")
        elif self._currently_reading_mode:
            items = self._filter_comics(self._library.get_currently_reading(
                sort_by=self._sort_by, order=self._sort_order
            ))
            empty_msg = ("Nothing in progress.\n"
                         "Comics you're partway through show up here.")
        elif self._search_query:
            folders, comics = self._library.search_library(
                self._search_query, self._sort_by, self._sort_order
            )
            items = folders + self._filter_comics(comics)
            empty_msg = f'No results for "{self._search_query}"'
        elif self._current_shelf_id is not None and self._current_folder is None:
            # Shelf top level — show folder tiles for folders with comics on this shelf.
            shelf_folders = self._library.get_shelf_folders(self._current_shelf_id)
            if self._status_filter:
                passing = {
                    str(Path(c.file_path).parent)
                    for c in self._filter_comics(
                        self._library.get_comics_in_shelf(self._current_shelf_id)
                    )
                }
                shelf_folders = [f for f in shelf_folders if f.path in passing]
            items = self._sort_tile_items(shelf_folders)
            empty_msg = "This shelf is empty."
        elif self._current_folder is not None:
            items, empty_msg = self._folder_grid_items()
            items = self._filter_comics(items)
        else:
            # Home grid: bookshelf tiles + folder tiles for unsorted comics.
            shelves = [s for s in self._library.get_shelves() if s.kind == "manual"]
            if self._status_filter:
                shelves = [s for s in shelves if self._shelf_matches_filter(s.id)]
            folders = self._home_folder_items()
            items = self._sort_tile_items(shelves) + self._sort_tile_items(folders)
            empty_msg = (
                "No bookshelves or comics yet.\n"
                "Use the ⋮ menu → New bookshelf, or Refresh library to add comics."
            )

        if not items:
            lbl = QLabel(empty_msg)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {_STATUS_FG.name()};")
            layout.addWidget(lbl)
            layout.addStretch()
            return

        row_widget: QWidget | None = None
        row_layout: QHBoxLayout | None = None

        for i, item in enumerate(items):
            if i % n_cols == 0:
                if row_layout is not None:
                    row_layout.addStretch()
                row_widget = QWidget()
                _transparent(row_widget)
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, TILE_SPACING)
                row_layout.setSpacing(TILE_SPACING)
                layout.addWidget(row_widget)

            is_home = (
                not self._show_hidden_mode
                and not self._currently_reading_mode and not self._search_query
                and self._current_shelf_id is None and self._current_folder is None
            )
            if isinstance(item, Shelf):
                shelf_comics = self._library.get_comics_in_shelf(item.id)
                if self._status_filter:
                    shelf_comics = [c for c in shelf_comics if self._passes_filter(c)]
                cover = self._shelf_tile_cover(item, shelf_comics)
                tile = ShelfTile(item, cover, len(shelf_comics))
                tile.shelf_opened.connect(self.show_shelf)
                tile.menu_requested.connect(self._on_shelf_context_menu)
                tile.file_comic_requested.connect(self._file_comic_into_shelf)
            elif isinstance(item, Folder):
                tile = FolderTile(item)
                if self._search_query:
                    tile.opened.connect(self._open_folder_from_search)
                elif self._current_shelf_id is not None:
                    tile.opened.connect(self._show_shelf_folder)
                else:
                    tile.opened.connect(self._show_comics)
                tile.menu_requested.connect(self._on_folder_context_menu)
            else:
                tile = ComicTile(item, selected=item.id in self._selected_ids)
                tile.opened.connect(self.comic_opened)
                tile.select_toggled.connect(self._toggle_selection)
                tile.shelf_action_requested.connect(self._on_comic_context_menu)
                if is_home:
                    # Loose comic on the home grid — drag it onto a shelf to file it.
                    tile.set_file_drag_enabled(True)
                self._comic_tiles[item.id] = tile

            self._ordered_tiles.append(tile)
            row_layout.addWidget(tile)

        if row_layout is not None:
            row_layout.addStretch()

        layout.addStretch()

        self._start_cover_loader()

    def _start_cover_loader(self):
        jobs = [
            (i, tile.cover_path)
            for i, tile in enumerate(self._ordered_tiles)
            if tile.cover_path
        ]
        if not jobs:
            return
        self._cover_gen += 1
        self._cover_loader = _CoverLoader(jobs, self._cover_gen, self)
        self._cover_loader.cover_ready.connect(self._on_cover_ready)
        self._cover_loader.start()

    def _on_cover_ready(self, gen: int, index: int, image: QImage):
        if gen != self._cover_gen:
            return  # stale — grid was rebuilt
        if 0 <= index < len(self._ordered_tiles):
            self._ordered_tiles[index].apply_cover(QPixmap.fromImage(image))

    def _stop_cover_loader(self):
        if self._cover_loader and self._cover_loader.isRunning():
            self._cover_loader.abort()
            self._cover_loader.wait()
        self._cover_loader = None

    def _on_comic_context_menu(self, comic_id: int, gx: int, gy: int):
        is_multi = comic_id in self._selected_ids and len(self._selected_ids) > 1
        target_ids = list(self._selected_ids) if is_multi else [comic_id]
        n = len(target_ids)

        menu = themes.make_menu(self)

        # Hidden view — restore or permanently delete
        if self._show_hidden_mode:
            restore_label = f"Restore {n} to library" if is_multi else "Restore to library"
            menu.addAction(restore_label).triggered.connect(
                lambda: self._restore_comics(target_ids)
            )
            menu.addSeparator()
            delete_label = (
                f"Delete {n} from disk…" if is_multi else "Delete from disk…"
            )
            menu.addAction(delete_label).triggered.connect(
                lambda: self._delete_comics_from_disk(target_ids)
            )
            menu.exec(QPoint(gx, gy))
            return

        # Metadata / grouping
        meta_label = f"Edit metadata… ({n} selected)" if is_multi else "Edit metadata…"
        menu.addAction(meta_label).triggered.connect(
            lambda: self._edit_metadata(target_ids)
        )
        if is_multi and self._current_folder:
            menu.addAction("Set chapter order").triggered.connect(
                lambda: self._set_chapter_order(target_ids)
            )
            menu.addAction("Link as reading series…").triggered.connect(
                lambda: self._group_as_series(target_ids)
            )
        if self._current_folder:
            selected = [
                c for cid in target_ids
                if (c := self._library.get_comic_by_id(cid)) is not None and c.series
            ]
            series_names = {c.series for c in selected}
            if len(series_names) == 1 and len(selected) == len(target_ids):
                series_name = next(iter(series_names))
                menu.addAction("Remove series link").triggered.connect(
                    lambda: self._remove_series_link(self._current_folder, series_name)
                )
            elif len(series_names) > 1:
                menu.addAction("Remove series link").triggered.connect(
                    lambda: self._remove_comics_from_series(target_ids)
                )

        # Favorite toggle
        menu.addSeparator()
        if is_multi:
            menu.addAction("★  Mark all as favorite").triggered.connect(
                lambda: [self._set_favorite(cid, True) for cid in target_ids]
            )
            menu.addAction("☆  Remove from favorites").triggered.connect(
                lambda: [self._set_favorite(cid, False) for cid in target_ids]
            )
        else:
            is_fav = self._library.get_comic_by_id(comic_id)
            is_fav = is_fav.favorite if is_fav else False
            fav_label = "☆  Remove from favorites" if is_fav else "★  Add to favorites"
            menu.addAction(fav_label).triggered.connect(
                lambda: self._toggle_favorite(comic_id)
            )

        menu.addSeparator()
        batch_menu = menu.addMenu("Batch")
        batch_menu.addAction("Convert to CBZ…").triggered.connect(
            lambda: self._plan_batch_convert(target_ids)
        )
        batch_menu.addAction("Rename from metadata…").triggered.connect(
            lambda: self._plan_batch_rename(target_ids)
        )

        # Cover & background (single comic only)
        if not is_multi:
            comic = self._library.get_comic_by_id(comic_id)
            bg_noun = self._background_noun(self._current_scope_parts())
            cover_menu = menu.addMenu("Cover & Background")
            cover_menu.addAction("Set cover from page…").triggered.connect(
                lambda: self._set_comic_cover_from_page(comic_id)
            )
            cover_menu.addAction("Choose cover image…").triggered.connect(
                lambda: self._set_comic_cover_from_image(comic_id)
            )
            if comic and comic.cover_override:
                cover_menu.addAction("Reset cover to default").triggered.connect(
                    lambda: self._reset_comic_cover(comic_id)
                )
            if comic and comic.cover_path:
                cover_menu.addSeparator()
                cover_menu.addAction("Set as folder cover").triggered.connect(
                    lambda: self._use_comic_as_folder_cover(comic_id)
                )
                cover = comic.cover_path
                cover_menu.addAction(f"Set as {bg_noun} background").triggered.connect(
                    lambda checked=False, cid=comic_id, c=cover: self._set_background(
                        c, comic_id=cid
                    )
                )
            if self._scope_has_own_background():
                cover_menu.addAction(f"Clear {bg_noun} background").triggered.connect(
                    lambda: self._clear_background()
                )

        menu.addSeparator()
        remove_label = f"Remove {n} from library…" if is_multi else "Remove from library…"
        menu.addAction(remove_label).triggered.connect(
            lambda: self._remove_comics_from_library(target_ids)
        )
        delete_label = f"Delete {n} from disk…" if is_multi else "Delete from disk…"
        menu.addAction(delete_label).triggered.connect(
            lambda: self._delete_comics_from_disk(target_ids)
        )

        menu.exec(QPoint(gx, gy))

    def _toggle_favorite(self, comic_id: int):
        self._library.toggle_favorite(comic_id)
        self._repopulate()

    def _set_favorite(self, comic_id: int, state: bool):
        comic = self._library.get_comic_by_id(comic_id)
        if comic and comic.favorite != state:
            self._library.toggle_favorite(comic_id)
        self._repopulate()

    def _toggle_selection(self, comic_id: int):
        if comic_id in self._selected_ids:
            self._selected_ids.discard(comic_id)
        else:
            self._selected_ids.add(comic_id)
        if comic_id in self._comic_tiles:
            self._comic_tiles[comic_id].set_selected(comic_id in self._selected_ids)
            self._comic_tiles[comic_id].update()

    def _add_comics_to_shelf(self, comic_ids: list[int], shelf_id: int) -> None:
        for cid in comic_ids:
            self._library.add_comic_to_shelf(cid, shelf_id)
        self.shelf_changed.emit()

    def _toggle_comic_in_shelf(self, comic_id: int, shelf_id: int, add: bool):
        if add:
            self._library.add_comic_to_shelf(comic_id, shelf_id)
        else:
            self._library.remove_comic_from_shelf(comic_id, shelf_id)
        self.shelf_changed.emit()

    def _add_folder_to_shelf(self, folder_path: str, shelf_id: int) -> None:
        n = self._library.add_folder_to_shelf(folder_path, shelf_id)
        shelf = next((s for s in self._library.get_shelves() if s.id == shelf_id), None)
        shelf_name = shelf.name if shelf else "shelf"
        folder_name = Path(folder_path).name
        self.window().statusBar().showMessage(
            f"Added {n} comic{'s' if n != 1 else ''} from “{folder_name}” to “{shelf_name}”",
            4000,
        )
        self.shelf_changed.emit()

    def _remove_comics_from_current_shelf(self, comic_ids: list[int]):
        if self._current_shelf_id is not None:
            for cid in comic_ids:
                self._library.remove_comic_from_shelf(cid, self._current_shelf_id)
            self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    # ----- Batch tools -----

    def _selected_comics_for_batch(self, comic_ids: list[int]) -> list[Comic]:
        comics: list[Comic] = []
        for cid in comic_ids:
            comic = self._library.get_comic_by_id(cid)
            if comic:
                comics.append(comic)
        return comics

    def _plan_batch_convert(self, comic_ids: list[int]) -> None:
        plan = plan_convert_to_cbz(self._selected_comics_for_batch(comic_ids))
        self._confirm_and_run_batch(plan, "Convert to CBZ")

    def _plan_batch_rename(self, comic_ids: list[int]) -> None:
        plan = plan_rename_from_metadata(self._selected_comics_for_batch(comic_ids))
        self._confirm_and_run_batch(plan, "Rename from Metadata")

    def _confirm_and_run_batch(self, plan: BatchPlan, title: str) -> None:
        if not plan.tasks:
            skipped = "\n".join(f"- {Path(p).name}: {why}" for p, why in plan.skipped[:8])
            QMessageBox.information(
                self,
                title,
                "No files can be processed."
                + (f"\n\nSkipped:\n{skipped}" if skipped else ""),
            )
            return

        preview = "\n".join(
            f"- {Path(t.source).name} → {Path(t.target).name}"
            for t in plan.tasks[:8]
        )
        if len(plan.tasks) > 8:
            preview += f"\n- ...and {len(plan.tasks) - 8} more"
        skipped = ""
        if plan.skipped:
            skipped = "\n\nSkipped:\n" + "\n".join(
                f"- {Path(p).name}: {why}" for p, why in plan.skipped[:6]
            )
            if len(plan.skipped) > 6:
                skipped += f"\n- ...and {len(plan.skipped) - 6} more"
        reply = QMessageBox.question(
            self,
            title,
            f"Process {len(plan.tasks)} file(s)?\n\n{preview}{skipped}\n\n"
            "Original files are not deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_batch(plan, title)

    def _run_batch(self, plan: BatchPlan, title: str) -> None:
        self._batch_thread = QThread(self)
        self._batch_worker = BatchWorker(self._library, plan)
        self._batch_worker.moveToThread(self._batch_thread)

        self._batch_progress = QProgressDialog(title, "Cancel", 0, len(plan.tasks), self)
        self._batch_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._batch_progress.canceled.connect(self._batch_worker.cancel)

        self._batch_thread.started.connect(self._batch_worker.run)
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.finished.connect(self._on_batch_finished)
        self._batch_worker.finished.connect(self._batch_thread.quit)
        self._batch_thread.finished.connect(self._batch_worker.deleteLater)
        self._batch_thread.finished.connect(self._batch_thread.deleteLater)
        self._batch_thread.start()

    def _on_batch_progress(self, current: int, total: int, name: str) -> None:
        if not self._batch_progress:
            return
        self._batch_progress.setMaximum(total)
        self._batch_progress.setValue(current)
        self._batch_progress.setLabelText(name)

    def _on_batch_finished(self, result) -> None:
        if self._batch_progress:
            self._batch_progress.close()
        self._batch_thread = None
        self._batch_worker = None
        self._batch_progress = None

        self._clear_selection()
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()

        pieces = [f"Completed: {result.completed}"]
        if result.skipped:
            pieces.append(f"Skipped: {len(result.skipped)}")
        if result.errors:
            pieces.append(f"Errors: {len(result.errors)}")
        detail = "\n".join(pieces)
        if result.errors:
            detail += "\n\n" + "\n".join(
                f"- {Path(p).name}: {err}" for p, err in result.errors[:8]
            )
        QMessageBox.information(self, "Batch Complete", detail)

    # ----- Hide / restore -----

    def _remove_comics_from_library(self, comic_ids: list[int]):
        n = len(comic_ids)
        what = "this comic" if n == 1 else f"these {n} comics"
        reply = QMessageBox.question(
            self, "Remove from Library",
            f"Hide {what} from the app?\n\n"
            "This does not delete anything from your computer — the files stay on "
            "disk. You can bring them back any time from the Hidden view.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for cid in comic_ids:
                self._library.set_hidden(cid, True)
            self._clear_selection()
            self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    def _restore_comics(self, comic_ids: list[int]):
        for cid in comic_ids:
            self._library.set_hidden(cid, False)
        self._clear_selection()
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()

    def _delete_comics_from_disk(self, comic_ids: list[int]):
        comics = [
            c for cid in comic_ids if (c := self._library.get_comic_by_id(cid)) is not None
        ]
        if not comics:
            return

        n = len(comics)
        what = "this comic" if n == 1 else f"these {n} comics"
        if n == 1:
            detail = f"“{Path(comics[0].file_path).name}”"
        else:
            names = "\n".join(f"• {Path(c.file_path).name}" for c in comics[:6])
            if n > 6:
                names += f"\n• …and {n - 6} more"
            detail = names

        reply = QMessageBox.warning(
            self,
            "Delete from Disk",
            f"Permanently delete {what}?\n\n{detail}\n\n"
            "The file(s) will be removed from your computer. Reading progress, "
            "bookmarks and notes for these comics will also be "
            "removed from Comic Reader.\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.comics_delete_requested.emit(comic_ids)

        errors: list[str] = []
        for cid in comic_ids:
            err = self._library.delete_comic_from_disk(cid)
            if err:
                errors.append(err)

        if errors:
            QMessageBox.warning(
                self,
                "Delete Failed",
                "Some files could not be deleted:\n\n" + "\n".join(errors[:8]),
            )

        self._clear_selection()
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()
        if not errors:
            msg = "Deleted 1 comic" if n == 1 else f"Deleted {n} comics"
            self.window().statusBar().showMessage(msg, 4000)

    # ----- Folder context menu (rescan / cover / hide) -----

    def _on_header_folder_menu(self, pos) -> None:
        if not self._current_folder:
            return
        gp = self._header.mapToGlobal(pos)
        self._show_folder_menu(self._current_folder, gp.x(), gp.y())

    def _on_folder_context_menu(self, folder_path: str, gx: int, gy: int):
        self._show_folder_menu(folder_path, gx, gy)

    def _comics_for_folder_grouping(self, folder_path: str) -> list[Comic]:
        """Comics visible in folder_path — shelf-filtered when drilling into a shelf."""
        if self._current_shelf_id is not None and folder_path == self._current_folder:
            return self._library.get_comics_in_shelf_for_folder(
                self._current_shelf_id, folder_path
            )
        return self._library.get_comics_in_folder(folder_path)

    def _show_folder_menu(self, folder_path: str, gx: int, gy: int) -> None:
        menu = themes.make_menu(self)
        folder_comics = (
            [] if self._show_hidden_mode
            else self._comics_for_folder_grouping(folder_path)
        )

        # Series submenu
        series_menu = menu.addMenu("Series")
        series_menu.addAction("Scan for series").triggered.connect(
            lambda: self._scan_series_in_folder(folder_path)
        )
        if len(folder_comics) >= 2:
            series_menu.addAction("Set chapter order").triggered.connect(
                lambda: self._set_chapter_order([c.id for c in folder_comics])
            )
            series_names = {c.series for c in folder_comics if c.series}
            if len(series_names) == 1 and all(c.series for c in folder_comics):
                series_name = next(iter(series_names))
                series_menu.addAction("Ungroup series").triggered.connect(
                    lambda: self._remove_series_link(folder_path, series_name)
                )
            else:
                series_menu.addAction("Link as reading series…").triggered.connect(
                    lambda: self._group_as_series([c.id for c in folder_comics])
                )
        series_menu.addSeparator()
        series_menu.addAction("Series reading settings…").triggered.connect(
            lambda: self._show_series_reading_settings(folder_path, folder_comics)
        )

        if self._show_hidden_mode:
            menu.exec(QPoint(gx, gy))
            return

        manual_shelves = [s for s in self._library.get_shelves() if s.kind == "manual"]
        if manual_shelves:
            move_menu = menu.addMenu("Move folder to shelf")
            for shelf in manual_shelves:
                sid = shelf.id
                move_menu.addAction(shelf.name).triggered.connect(
                    lambda checked=False, fp=folder_path, s=sid: self._add_folder_to_shelf(fp, s)
                )

        menu.addSeparator()
        menu.addAction("Choose cover image…").triggered.connect(
            lambda: self._set_folder_cover_from_image(folder_path)
        )
        if self._library.get_folder_cover(folder_path):
            menu.addAction("Reset cover to default").triggered.connect(
                lambda: self._reset_folder_cover(folder_path)
            )
        folder_scope = self._folder_menu_background_scope(folder_path)
        bg_noun = self._background_noun(folder_scope)
        menu.addAction(f"Set as {bg_noun} background").triggered.connect(
            lambda checked=False, fp=folder_path, scope=folder_scope: self._set_background(
                self._effective_folder_cover(fp),
                scope,
                folder_path=fp,
            )
        )
        if self._scope_has_own_background(folder_scope):
            menu.addAction(f"Clear {bg_noun} background").triggered.connect(
                lambda: self._clear_background(folder_scope)
            )
        menu.addSeparator()
        menu.addAction("Hide this folder").triggered.connect(
            lambda: self._hide_folder(folder_path)
        )
        menu.exec(QPoint(gx, gy))

    def _show_series_reading_settings(self, folder_path: str, folder_comics: list) -> None:
        series_names = {c.series for c in folder_comics if c.series}
        if not series_names:
            QMessageBox.information(
                self, "Series Settings",
                "No series found in this folder. Use Series → Scan for series first."
            )
            return
        series_name = next(iter(series_names))
        current = self._library.get_series_reading_settings(folder_path, series_name)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Series settings — {series_name}")
        layout = QFormLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        fit_options = [("Inherit", None), ("Fit page", "page"), ("Fit width", "width"),
                       ("Fit height", "height"), ("Actual size", "actual")]
        fit_combo = QComboBox()
        for label, _ in fit_options:
            fit_combo.addItem(label)
        fit_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(fit_options) if v == current.fit_mode), 0)
        )
        layout.addRow("Fit mode:", fit_combo)

        mode_options = [("Inherit", None), ("Single page", "single"), ("Webtoon scroll", "webtoon")]
        mode_combo = QComboBox()
        for label, _ in mode_options:
            mode_combo.addItem(label)
        mode_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(mode_options) if v == current.reading_mode), 0)
        )
        layout.addRow("Reading mode:", mode_combo)

        spread_options = [("Inherit", None), ("On", True), ("Off", False)]
        spread_combo = QComboBox()
        for label, _ in spread_options:
            spread_combo.addItem(label)
        spread_combo.setCurrentIndex(
            next((i for i, (_, v) in enumerate(spread_options) if v == current.spread), 0)
        )
        layout.addRow("Spread mode:", spread_combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self._library.set_series_reading_settings(
            folder_path,
            series_name,
            ReadingSettings(
                fit_mode=fit_options[fit_combo.currentIndex()][1],
                reading_mode=mode_options[mode_combo.currentIndex()][1],
                spread=spread_options[spread_combo.currentIndex()][1],
            ),
        )
        self.window().statusBar().showMessage(
            f"Series settings saved for “{series_name}”", 4000
        )

    def _rename_folder(self, folder_path: str) -> None:
        current_name = Path(folder_path).name
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Folder",
            "New folder name:",
            text=current_name,
        )
        if not ok or not new_name.strip():
            return
        try:
            new_path = self._library.rename_folder(folder_path, new_name.strip())
        except FileExistsError:
            QMessageBox.warning(
                self,
                "Rename Folder",
                "A folder with that name already exists in this location.",
            )
            return
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Rename Folder",
                "This folder was not found on disk. Try rescanning the library folder.",
            )
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Rename Folder", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Rename Folder",
                f"Could not rename the folder:\n{exc}",
            )
            return

        if self._current_folder == folder_path:
            self._current_folder = new_path
        self._migrate_folder_backgrounds(folder_path, new_path)
        self._nav_transition(self._repopulate)
        self.window().statusBar().showMessage(
            f"Folder renamed to “{Path(new_path).name}”",
            4000,
        )

    def _use_comic_as_folder_cover(self, comic_id: int) -> None:
        comic = self._library.get_comic_by_id(comic_id)
        if comic is None or not comic.cover_path:
            QMessageBox.information(
                self,
                "Set Folder Cover",
                "This comic does not have a cover image yet.",
            )
            return
        folder_path = str(Path(comic.file_path).parent)
        self._library.set_folder_cover(folder_path, comic.cover_path)
        self._nav_transition(self._repopulate)
        folder_name = Path(folder_path).name
        comic_name = comic.title or Path(comic.file_path).stem
        self.window().statusBar().showMessage(
            f"Folder “{folder_name}” now uses the cover from “{comic_name}”",
            4000,
        )

    def _set_folder_cover_from_image(self, folder_path: str):
        from thumbnails import folder_cover_path_for, generate_thumbnail_from_image
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return
        out = folder_cover_path_for(folder_path)
        if generate_thumbnail_from_image(path, out):
            self._library.set_folder_cover(folder_path, str(out))
            self._nav_transition(self._repopulate)
        else:
            QMessageBox.warning(
                self, "Choose Cover Image", "Could not load that image file."
            )

    def _reset_folder_cover(self, folder_path: str):
        self._library.clear_folder_cover(folder_path)
        self._nav_transition(self._repopulate)

    # ----- Per-bookshelf (shelf) cover -----

    def _set_shelf_cover_from_image(self, shelf_id: int):
        from thumbnails import copy_image_as_cover, shelf_cover_path_for
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return
        out = shelf_cover_path_for(shelf_id)
        if not copy_image_as_cover(path, out):
            QMessageBox.warning(
                self, "Choose Cover Image", "Could not load that image file."
            )
            return
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()

    def _clear_shelf_cover(self, shelf_id: int):
        from thumbnails import shelf_cover_path_for
        try:
            shelf_cover_path_for(shelf_id).unlink(missing_ok=True)
        except OSError:
            pass
        self._nav_transition(self._repopulate)
        self.shelf_changed.emit()

    # ----- Per-comic cover override -----

    def _set_comic_cover_from_page(self, comic_id: int):
        from archive_handler import open_comic
        from thumbnails import (
            comic_cover_override_path_for,
            generate_thumbnail_from_bytes,
        )
        comic = self._library.get_comic_by_id(comic_id)
        if comic is None:
            return
        if not Path(comic.file_path).exists():
            QMessageBox.warning(
                self, "Set Cover",
                "This comic's file is no longer on your computer:\n"
                f"{comic.file_path}\n\n"
                "You can still use “Choose cover image…”, or remove the comic from "
                "your library.",
            )
            return
        try:
            with open_comic(comic.file_path) as reader:
                total = reader.page_count()
                if total <= 0:
                    QMessageBox.warning(self, "Set Cover", "This comic has no pages.")
                    return
                page, ok = QInputDialog.getInt(
                    self, "Set Cover from Page",
                    f"Page number (1–{total}):", 1, 1, total,
                )
                if not ok:
                    return
                page_bytes = reader.get_page_bytes(page - 1)
        except Exception as e:
            QMessageBox.warning(self, "Set Cover", f"Could not read that page:\n{e}")
            return
        out = comic_cover_override_path_for(comic_id)
        if generate_thumbnail_from_bytes(page_bytes, out):
            self._library.set_cover_path(comic_id, str(out))
            self._library.set_cover_override(comic_id, True)
            self._nav_transition(self._repopulate)
        else:
            QMessageBox.warning(self, "Set Cover", "Could not build a cover from that page.")

    def _set_comic_cover_from_image(self, comic_id: int):
        from thumbnails import (
            comic_cover_override_path_for,
            generate_thumbnail_from_image,
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cover Image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)",
        )
        if not path:
            return
        out = comic_cover_override_path_for(comic_id)
        if generate_thumbnail_from_image(path, out):
            self._library.set_cover_path(comic_id, str(out))
            self._library.set_cover_override(comic_id, True)
            self._nav_transition(self._repopulate)
        else:
            QMessageBox.warning(self, "Choose Cover Image", "Could not load that image file.")

    def _reset_comic_cover(self, comic_id: int):
        from thumbnails import generate_thumbnail, thumbnail_path_for
        comic = self._library.get_comic_by_id(comic_id)
        if comic is None:
            return
        out = thumbnail_path_for(comic_id)
        if generate_thumbnail(comic.file_path, out):
            self._library.set_cover_path(comic_id, str(out))
        self._library.set_cover_override(comic_id, False)
        self._nav_transition(self._repopulate)

    def _hide_folder(self, folder_path: str):
        name = Path(folder_path).name
        reply = QMessageBox.question(
            self, "Hide Folder",
            f"Hide “{name}” and its comics from the app?\n\n"
            "This does not delete anything from your computer — the files stay on "
            "disk. You can bring them back any time from the Hidden view.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._library.hide_folder(folder_path)
            self._nav_transition(self._repopulate)
            self.shelf_changed.emit()

    def _edit_metadata(self, comic_ids: list[int]):
        from metadata_editor import MetadataDialog
        comics = [c for cid in comic_ids if (c := self._library.get_comic_by_id(cid))]
        if not comics:
            return
        comic_tags = {c.id: self._library.get_tags_for_comic(c.id) for c in comics}
        dlg = MetadataDialog(comics, comic_tags=comic_tags, parent=self)
        if dlg.exec():
            changes = dlg.get_changes()
            if changes:
                tags = changes.pop("tags", None)
                is_manga = changes.pop("is_manga", None)
                if changes:
                    for comic in comics:
                        self._library.update_metadata(comic.id, **changes)
                if tags is not None:
                    for comic in comics:
                        self._library.set_tags_for_comic(comic.id, tags)
                if is_manga is not None:
                    for comic in comics:
                        self._library.set_is_manga(comic.id, is_manga)
                self._clear_selection()
                self._nav_transition(self._repopulate)

    def _group_as_series(self, comic_ids: list[int]):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self,
            "Link as Reading Series",
            "Series name (issues stay as separate tiles; you'll be offered the "
            "next one when you finish each issue):",
        )
        if ok and name.strip():
            self._library.group_comics_as_series(comic_ids, name.strip())
            self._clear_selection()
            self._apply_sort("chapter", "asc")
            self._persist_sort()
            self._nav_transition(self._repopulate)
            n = len(comic_ids)
            self.window().statusBar().showMessage(
                f"Linked {n} comic{'s' if n != 1 else ''} as “{name.strip()}”",
                4000,
            )

    def _set_chapter_order(self, comic_ids: list[int]):
        if len(comic_ids) < 2:
            return
        self._library.set_comics_chapter_order(comic_ids)
        self._clear_selection()
        self._apply_sort("chapter", "asc")
        self._persist_sort()
        self._repopulate()
        n = len(comic_ids)
        self.window().statusBar().showMessage(
            f"Set {n} comic{'s' if n != 1 else ''} to chapter order",
            4000,
        )

    def _apply_sort(self, sort_by: str, order: str):
        self._sort_by = sort_by
        self._sort_order = order
        for idx, (_, key, ord_) in enumerate(self._header._SORT_OPTIONS):
            if key == sort_by and ord_ == order:
                self._header.set_sort_index(idx)
                break

    def _scan_series_in_folder(self, folder_path: str) -> None:
        linked = self._library.scan_series_in_folder(folder_path)
        self._nav_transition(self._repopulate)
        if linked:
            self.window().statusBar().showMessage(
                f"Linked {linked} comic{'s' if linked != 1 else ''} into series",
                4000,
            )
        else:
            self.window().statusBar().showMessage(
                "No new series links found in this folder",
                4000,
            )

    def _remove_series_link(self, folder_path: str, series_name: str) -> None:
        """Clear series metadata — stops continue-to-next at end of issue."""
        comics = self._library.get_comics_in_series(folder_path, series_name)
        for c in comics:
            self._library.update_metadata(c.id, series=None)
        self._nav_transition(self._repopulate)

    def _remove_comics_from_series(self, comic_ids: list[int]) -> None:
        for cid in comic_ids:
            self._library.update_metadata(cid, series=None)
        self._clear_selection()
        self._nav_transition(self._repopulate)

    # ----- Per-view bookshelf backgrounds -----

    def _comic_id_from_thumb_path(self, cover_path: str) -> int | None:
        """Parse a comic id from a cached cover thumbnail filename, if possible."""
        name = Path(cover_path).name
        if name.startswith("cover_override_") and name.endswith(".jpg"):
            stem = name[len("cover_override_") : -4]
        elif name.endswith(".jpg"):
            stem = name[:-4]
        else:
            return None
        try:
            return int(stem)
        except ValueError:
            return None

    @staticmethod
    def _thumb_jpeg_bytes(image: QImage) -> bytes:
        """Match the JPEG bytes ``generate_thumbnail_from_bytes`` would write."""
        from PyQt6.QtCore import QBuffer, QIODevice

        from thumbnails import THUMB_MAX_HEIGHT, THUMB_MAX_WIDTH, THUMB_QUALITY

        thumb = image.scaled(
            THUMB_MAX_WIDTH,
            THUMB_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        thumb.save(buf, "JPEG", THUMB_QUALITY)
        return bytes(buf.data())

    def _find_cover_override_page(self, comic_id: int, thumb_path: str) -> int:
        """Find which comic page produced a manual cover override thumbnail."""
        from archive_handler import open_comic

        try:
            ref = Path(thumb_path).read_bytes()
        except OSError:
            return 0

        comic = self._library.get_comic_by_id(comic_id)
        if comic is None or not Path(comic.file_path).exists():
            return 0

        try:
            with open_comic(comic.file_path) as reader:
                for i in range(reader.page_count()):
                    page = QImage.fromData(reader.get_page_bytes(i))
                    if page.isNull():
                        continue
                    if self._thumb_jpeg_bytes(page) == ref:
                        return i
        except Exception:
            pass
        return 0

    def _comic_page_image(
        self, comic_id: int, page: int = 0, min_long_side: int = 0
    ) -> QImage | None:
        from archive_handler import render_page_qimage

        comic = self._library.get_comic_by_id(comic_id)
        if comic is None or not Path(comic.file_path).exists():
            return None
        return render_page_qimage(comic.file_path, page, min_long_side)

    def _parse_bg_entry(self, raw: str) -> dict | None:
        """Parse a stored background entry — v2 JSON spec or legacy image path."""
        if not raw:
            return None
        if raw.startswith("{"):
            try:
                spec = json.loads(raw)
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
            if isinstance(spec, dict) and spec.get("v") == 2:
                return spec
            return None
        if Path(raw).exists():
            return {"v": 1, "path": raw}
        return None

    @staticmethod
    def _encode_bg_entry(spec: dict) -> str:
        if spec.get("v") == 2:
            return json.dumps(spec, separators=(",", ":"))
        return spec.get("path", "")

    def _build_bg_spec(
        self,
        cover_path: str | None,
        *,
        comic_id: int | None = None,
        folder_path: str | None = None,
    ) -> dict | None:
        """Build a v2 background spec that keeps a link back to full-resolution source."""
        cid = comic_id
        if cid is None and cover_path:
            cid = self._comic_id_from_thumb_path(cover_path)
        if cid is None and folder_path and cover_path:
            for comic in self._library.get_comics_in_folder(folder_path):
                if comic.cover_path == cover_path:
                    cid = comic.id
                    break

        if cid is not None:
            page = 0
            if cover_path and Path(cover_path).name.startswith("cover_override_"):
                page = self._find_cover_override_page(cid, cover_path)
            elif cover_path:
                comic = self._library.get_comic_by_id(cid)
                if comic and comic.cover_override:
                    page = self._find_cover_override_page(cid, cover_path)
            return {"v": 2, "comic_id": cid, "page": page}

        if folder_path and cover_path:
            img = QImage(cover_path) if Path(cover_path).exists() else QImage()
            if img.isNull() or max(img.width(), img.height()) < 800:
                for comic in self._library.get_comics_in_folder(folder_path):
                    if comic.cover_path:
                        spec = self._build_bg_spec(
                            comic.cover_path, comic_id=comic.id, folder_path=folder_path
                        )
                        if spec:
                            return spec

        if cover_path and Path(cover_path).exists():
            img = QImage(cover_path)
            if not img.isNull():
                return {"v": 2, "path": cover_path}
        return None

    def _folder_path_from_scope_key(self, scope_key: str) -> str | None:
        if scope_key.startswith("folder:"):
            return scope_key[len("folder:"):]
        if "+folder:" in scope_key:
            return scope_key.split("+folder:", 1)[1]
        return None

    def _normalize_bg_entry(self, raw: str, scope_key: str = "") -> str | None:
        """Upgrade legacy thumbnail paths to v2 specs that render from full comic pages."""
        spec = self._parse_bg_entry(raw)
        if spec is None:
            return None
        if spec.get("v") == 2:
            if self._source_image_from_spec(spec) is not None:
                return self._encode_bg_entry(spec)
            return None

        path = spec.get("path", "")
        if not path:
            return None

        upgraded = self._build_bg_spec(
            path, folder_path=self._folder_path_from_scope_key(scope_key)
        )
        if upgraded and self._source_image_from_spec(upgraded) is not None:
            return self._encode_bg_entry(upgraded)
        if Path(path).exists():
            fallback = {"v": 2, "path": path}
            if self._source_image_from_spec(fallback) is not None:
                return self._encode_bg_entry(fallback)
        return None

    def _source_image_from_spec(
        self, spec: dict | None, min_long_side: int = 0
    ) -> QImage | None:
        """Load the full-resolution source image for a background spec."""
        if not spec:
            return None
        img: QImage | None = None
        if spec.get("v") == 2:
            if "comic_id" in spec:
                img = self._comic_page_image(
                    spec["comic_id"], spec.get("page", 0), min_long_side
                )
            else:
                path = spec.get("path")
                if path and Path(path).exists():
                    loaded = QImage(path)
                    img = loaded if not loaded.isNull() else None
                    if img is not None and min_long_side > 0:
                        from archive_handler import _scale_qimage_min_long_side
                        img = _scale_qimage_min_long_side(img, min_long_side)
        else:
            path = spec.get("path")
            if path and Path(path).exists():
                loaded = QImage(path)
                img = loaded if not loaded.isNull() else None
        if img is None or max(img.width(), img.height()) < 200:
            return None
        return img

    def _migrate_all_background_entries(self) -> None:
        """Upgrade every saved background from low-res thumbnails to v2 comic-linked specs."""
        data = self._load_background_map()
        if not data:
            return
        changed = False
        new_data: dict[str, str] = {}
        for key, raw in data.items():
            if isinstance(raw, str) and raw.startswith('{"v":2'):
                spec = self._parse_bg_entry(raw)
                if spec and "comic_id" in spec:
                    # Re-renders from the comic file, so it survives a cache purge.
                    # Keep it even if the page can't be decoded right now (the file
                    # may be temporarily unavailable) — never silently drop it.
                    new_data[key] = raw
                elif spec and self._source_image_from_spec(spec) is not None:
                    new_data[key] = raw
                else:
                    changed = True
                continue
            normalized = self._normalize_bg_entry(raw, key)
            if normalized:
                new_data[key] = normalized
                if normalized != raw:
                    changed = True
            else:
                changed = True
        if changed or len(new_data) != len(data):
            self._save_background_map(new_data)

    def _effective_folder_cover(self, folder_path: str) -> str | None:
        """The cover image a folder shows — its override, else its first comic's cover."""
        cover = self._library.get_folder_cover(folder_path)
        if cover:
            return cover
        for c in self._library.get_comics_in_folder(folder_path):
            if c.cover_path:
                return c.cover_path
        return None

    def _current_scope_parts(self) -> list:
        """Identify the bookshelf view the user is looking at right now."""
        if self._currently_reading_mode:
            return ["currently_reading"]
        if self._show_hidden_mode:
            return ["hidden"]
        if self._current_shelf_id is not None:
            if self._current_folder is not None:
                return ["shelf", self._current_shelf_id, "folder", self._current_folder]
            return ["shelf", self._current_shelf_id]
        if self._current_folder is not None:
            return ["folder", self._current_folder]
        return ["library"]

    def _folder_menu_background_scope(self, folder_path: str) -> list:
        """Background target for a folder/header menu — always the view on screen.

        From the library grid this is the library itself; from a shelf grid it's the
        shelf; from inside a folder it's that folder. The folder's cover art is used
        as the source image, but it sets the background of whatever you're looking at.
        """
        return self._current_scope_parts()

    def _scope_map_key(self, parts: list) -> str:
        """Flat map key — folder paths stay in the value, never in QSettings key names."""
        if parts[0] == "library":
            return "library"
        if parts[0] == "currently_reading":
            return "currently_reading"
        if parts[0] == "hidden":
            return "hidden"
        if parts[0] == "folder":
            return f"folder:{parts[1]}"
        if parts[0] == "shelf" and len(parts) == 2:
            return f"shelf:{parts[1]}"
        if parts[0] == "shelf" and len(parts) == 4 and parts[2] == "folder":
            return f"shelf:{parts[1]}+folder:{parts[3]}"
        return json.dumps(parts, separators=(",", ":"))

    def _background_noun(self, parts: list) -> str:
        """The word used in menu labels, e.g. 'Set as <noun> background'."""
        if "folder" in parts:
            return "folder"
        return {
            "library": "library",
            "shelf": "shelf",
            "currently_reading": "currently reading",
            "hidden": "hidden",
        }.get(parts[0], "view")

    def _scope_label(self, parts: list) -> str:
        if parts[0] == "library":
            return "Library"
        if parts[0] == "currently_reading":
            return "Currently Reading"
        if parts[0] == "hidden":
            return "Hidden"
        if parts[0] == "folder":
            return Path(parts[1]).name
        if parts[0] == "shelf" and len(parts) == 2:
            for shelf in self._library.get_shelves():
                if shelf.id == parts[1]:
                    return shelf.name
            return "Shelf"
        if parts[0] == "shelf" and len(parts) == 4 and parts[2] == "folder":
            return Path(parts[3]).name
        return "view"

    def _load_background_map(self) -> dict[str, str]:
        try:
            settings = app_settings()
            raw = settings.value(_BG_MAP_KEY) or settings.value("bookshelf.background_map")
            if isinstance(raw, str) and raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return {
                        k: v for k, v in data.items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return {}

    def _save_background_map(self, data: dict[str, str]) -> None:
        settings = app_settings()
        settings.setValue(_BG_MAP_KEY, json.dumps(data, separators=(",", ":")))
        settings.sync()

    def _background_scope_fallbacks(self, parts: list) -> list[list]:
        """Try the current view first, then parent views (shelf → library)."""
        chain: list[list] = [parts]
        if parts[0] == "shelf" and len(parts) == 4 and parts[2] == "folder":
            chain.append(["shelf", parts[1]])
        if parts[0] in ("shelf", "folder"):
            chain.append(["library"])
        seen: set[str] = set()
        result: list[list] = []
        for candidate in chain:
            key = self._scope_map_key(candidate)
            if key not in seen:
                seen.add(key)
                result.append(candidate)
        return result

    def _read_background_spec_for_parts(self, parts: list) -> dict | None:
        try:
            stored = self._load_background_map().get(self._scope_map_key(parts))
        except Exception:
            return None
        if not isinstance(stored, str) or not stored:
            return None
        spec = self._parse_bg_entry(stored)
        if spec is None or self._source_image_from_spec(spec) is None:
            return None
        return spec

    def _read_background_for_scope(self, parts: list) -> dict | None:
        for candidate in self._background_scope_fallbacks(parts):
            spec = self._read_background_spec_for_parts(candidate)
            if spec is not None:
                return spec
        return None

    def random_background_image(self, min_long_side: int = 0) -> "QImage | None":
        """A randomly chosen source image from among all saved bookshelf backgrounds.

        Used by the Settings page, which has no comic tiles of its own. Specs are
        shuffled and the first one that still decodes is returned, so the result is
        both random and resilient to a background whose comic file is unavailable.
        """
        import random
        specs = []
        for raw in self._load_background_map().values():
            spec = self._parse_bg_entry(raw)
            if spec is not None:
                specs.append(spec)
        random.shuffle(specs)
        for spec in specs:
            img = self._source_image_from_spec(spec, min_long_side)
            if img is not None:
                return img
        return None

    def _scope_has_own_background(self, parts: list | None = None) -> bool:
        parts = parts if parts is not None else self._current_scope_parts()
        return self._read_background_spec_for_parts(parts) is not None

    def _scope_has_background(self, parts: list | None = None) -> bool:
        parts = parts if parts is not None else self._current_scope_parts()
        return self._read_background_for_scope(parts) is not None

    def _migrate_background_cover_paths(self) -> None:
        """Repoint any background image paths from the old cache dir to persistent storage.

        Mirrors the DB cover-path migration so a saved background whose source was a
        cached image keeps resolving after covers move out of the purgeable cache.
        """
        try:
            from thumbnails import legacy_cover_base, cover_store_base
        except Exception:
            return
        old = legacy_cover_base().rstrip("/") + "/"
        new = cover_store_base().rstrip("/") + "/"
        if old == new:
            return
        data = self._load_background_map()
        if not data:
            return
        changed = False
        for key, value in list(data.items()):
            if old in value:
                data[key] = value.replace(old, new)
                changed = True
        if changed:
            self._save_background_map(data)

    def _maybe_migrate_background_storage(self) -> None:
        """Move old background settings into the map and delete broken per-key entries."""
        settings = app_settings()
        data = self._load_background_map()
        changed = False

        legacy = settings.value(_BG_LEGACY_KEY)
        if isinstance(legacy, str) and legacy and "library" not in data:
            data["library"] = legacy
            settings.remove(_BG_LEGACY_KEY)
            changed = True

        # Prior per-scope format that was safe for library but broke once folder paths
        # were embedded in QSettings key names (slashes confuse macOS plist nesting).
        old_library = settings.value(f'{_BG_OLD_GROUP}/["library"]')
        if isinstance(old_library, str) and old_library and "library" not in data:
            data["library"] = old_library
            changed = True

        # Always delete the old storage shapes — folder paths in key names broke macOS plists.
        settings.remove(_BG_LEGACY_KEY)
        settings.remove(_BG_OLD_GROUP)

        if changed:
            self._save_background_map(data)

    def _migrate_folder_backgrounds(self, old_path: str, new_path: str) -> None:
        data = self._load_background_map()
        changed = False
        pairs = [(f"folder:{old_path}", f"folder:{new_path}")]
        for shelf in self._library.get_shelves():
            pairs.append((
                f"shelf:{shelf.id}+folder:{old_path}",
                f"shelf:{shelf.id}+folder:{new_path}",
            ))
        for old_key, new_key in pairs:
            if old_key in data:
                data[new_key] = data.pop(old_key)
                changed = True
        if changed:
            self._save_background_map(data)

    def _switch_background(self) -> None:
        """Load the background saved for the current shelf/folder/library view."""
        try:
            self._bg_spec = self._read_background_for_scope(self._current_scope_parts())
        except Exception:
            self._bg_spec = None
        self._apply_background()

    def _set_background(
        self,
        cover_path: str | None,
        parts: list | None = None,
        *,
        comic_id: int | None = None,
        folder_path: str | None = None,
    ) -> None:
        parts = parts if parts is not None else self._current_scope_parts()
        scope_key = self._scope_map_key(parts)
        spec = self._build_bg_spec(
            cover_path,
            comic_id=comic_id,
            folder_path=folder_path,
        )
        if spec is None or self._source_image_from_spec(spec) is None:
            QMessageBox.information(
                self, "Set Bookshelf Background",
                "There's no cover image available to use as a background yet.",
            )
            return

        data = self._load_background_map()
        data[scope_key] = self._encode_bg_entry(spec)
        self._save_background_map(data)
        label = self._scope_label(parts)
        if parts == self._current_scope_parts():
            self._bg_spec = spec
            self._apply_background()
        self.window().statusBar().showMessage(f"Background set for {label}", 4000)

    def _clear_background(self, parts: list | None = None) -> None:
        parts = parts if parts is not None else self._current_scope_parts()
        data = self._load_background_map()
        data.pop(self._scope_map_key(parts), None)
        self._save_background_map(data)
        label = self._scope_label(parts)
        if parts == self._current_scope_parts():
            self._bg_spec = None
            self._bg_label.hide()
        self.window().statusBar().showMessage(f"Background cleared for {label}", 4000)

    def _build_background_pixmap(self, w: int, h: int) -> QPixmap | None:
        """Cover w x h with the source at the exact minimum zoom, then tint it."""
        # Load the source at native resolution so we control the zoom ourselves.
        src = self._source_image_from_spec(self._bg_spec, 0)
        if src is None:
            return None
        sw, sh = src.width(), src.height()
        if sw <= 0 or sh <= 0:
            return None
        # Exact "cover" scale: fill both dimensions with no margins and no extra
        # zoom beyond what is needed. Overflow is cropped equally from the center.
        scale = max(w / sw, h / sh)
        new_w = max(1, int(round(sw * scale)))
        new_h = max(1, int(round(sh * scale)))
        scaled = src.scaled(
            new_w, new_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Compose onto a full-view canvas: center the cover, crop overflow, then
        # dim everything so it sits quietly behind the tiles.
        pix = QPixmap(w, h)
        pix.fill(_BG)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage((w - scaled.width()) // 2, (h - scaled.height()) // 2, scaled)
        overlay = QColor(_BG)
        overlay.setAlpha(_BG_DIM_ALPHA)
        painter.fillRect(pix.rect(), overlay)
        painter.end()
        return pix

    def _background_device_pixel_ratio(self, vp) -> float:
        dpr = max(vp.devicePixelRatioF(), self.devicePixelRatioF())
        window = self.window()
        if window is not None:
            dpr = max(dpr, window.devicePixelRatioF())
            handle = window.windowHandle()
            if handle is not None:
                dpr = max(dpr, handle.devicePixelRatio())
        return dpr if dpr > 0 else 1.0

    def _apply_background(self) -> None:
        vp = self._scroll.viewport()
        dpr = self._background_device_pixel_ratio(vp)
        if not self._bg_spec or self.width() <= 0 or self.height() <= 0:
            self._bg_label.hide()
            return
        w = max(1, int(self.width() * dpr))
        h = max(1, int(self.height() * dpr))
        pix = self._build_background_pixmap(w, h)
        if pix is None:
            self._bg_label.hide()
            return
        pix.setDevicePixelRatio(dpr)
        self._bg_label.setGeometry(0, 0, self.width(), self.height())
        self._bg_label.setPixmap(pix)
        self._bg_label.lower()
        self._bg_label.show()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_background()

    def eventFilter(self, obj, event):
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_background()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_background()
        if self._n_cols() != self._last_n_cols:
            self._repopulate()
