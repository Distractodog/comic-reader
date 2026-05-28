"""Widget that displays comic pages with zoom and fit modes."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QScrollArea, QToolTip, QWidget

CLICK_ZONE_FRACTION = 0.30  # left/right 30% = nav zones, middle 40% = dead zone


class FitMode(Enum):
    ACTUAL_SIZE = "actual"
    FIT_WIDTH = "width"
    FIT_PAGE = "page"


class ReadingMode(Enum):
    SINGLE = "single"
    WEBTOON = "webtoon"


def make_spread_pixmap(page1_bytes: bytes, page2_bytes: bytes, rtl: bool = False) -> QPixmap:
    """Compose two pages side by side into a single pixmap."""
    pix1 = QPixmap()
    pix1.loadFromData(page1_bytes)
    pix2 = QPixmap()
    pix2.loadFromData(page2_bytes)

    if pix1.isNull():
        return pix2
    if pix2.isNull():
        return pix1

    w = pix1.width() + pix2.width()
    h = max(pix1.height(), pix2.height())

    combined = QPixmap(w, h)
    combined.fill(Qt.GlobalColor.black)

    painter = QPainter(combined)
    if rtl:
        painter.drawPixmap(0, (h - pix2.height()) // 2, pix2)
        painter.drawPixmap(pix2.width(), (h - pix1.height()) // 2, pix1)
    else:
        painter.drawPixmap(0, (h - pix1.height()) // 2, pix1)
        painter.drawPixmap(pix1.width(), (h - pix2.height()) // 2, pix2)
    painter.end()

    return combined


class ComicViewer(QGraphicsView):
    """Image viewer using QGraphicsView for smooth scrolling and zoom."""

    page_forward = pyqtSignal()
    page_back = pyqtSignal()
    mouse_moved = pyqtSignal(int)  # viewport y — used for fullscreen bar reveal

    def __init__(self, parent=None):
        super().__init__(parent)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._pixmap_item = QGraphicsPixmapItem()
        self._pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._pixmap_item)

        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setStyleSheet("background-color: #000000; border: none;")

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._fit_mode = FitMode.FIT_PAGE
        self._zoom_factor = 1.0
        self._has_image = False
        self._rtl = False
        self._swipe_x = 0
        self._swipe_y = 0
        self._swipe_fired = False

        self.setMouseTracking(True)

        self._overlay = QLabel(self.viewport())
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay.hide()
        self._overlay_new = QLabel(self.viewport())
        self._overlay_new.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay_new.hide()

        self._slide_anim = QPropertyAnimation(self._overlay, b"geometry", self)
        self._slide_anim.setDuration(200)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_anim_new = QPropertyAnimation(self._overlay_new, b"geometry", self)
        self._slide_anim_new.setDuration(200)
        self._slide_anim_new.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim_group = QParallelAnimationGroup(self)
        self._anim_group.addAnimation(self._slide_anim)
        self._anim_group.addAnimation(self._slide_anim_new)
        self._anim_group.finished.connect(self._on_slide_done)

    def set_rtl(self, rtl: bool) -> None:
        self._rtl = rtl

    def set_image(self, image_bytes: bytes, direction: int = 0):
        """Load a new page from raw bytes."""
        pixmap = QPixmap()
        pixmap.loadFromData(image_bytes)
        self.set_image_pixmap(pixmap, direction)

    def set_image_pixmap(self, pixmap: QPixmap, direction: int = 0):
        """Load a pre-built pixmap (e.g. from the page cache or spread composer)."""
        self._anim_group.stop()
        self._overlay.hide()
        self._overlay_new.hide()

        should_animate = direction != 0 and self._has_image
        old_grab = self.viewport().grab() if should_animate else None

        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(pixmap.rect().toRectF())
        self._zoom_factor = 1.0
        self._has_image = True
        self._apply_fit()

        if old_grab is not None:
            new_grab = self.viewport().grab()
            self._run_slide(old_grab, new_grab, direction)

    def _run_slide(self, old_pixmap: QPixmap, new_pixmap: QPixmap, direction: int):
        vp = self.viewport()
        w, h = vp.width(), vp.height()
        out_x = -w if direction > 0 else w
        in_x = w if direction > 0 else -w

        self._overlay.setPixmap(old_pixmap)
        self._overlay.setGeometry(0, 0, w, h)
        self._overlay.show()

        self._overlay_new.setPixmap(new_pixmap)
        self._overlay_new.setGeometry(in_x, 0, w, h)
        self._overlay_new.show()
        self._overlay_new.raise_()

        self._slide_anim.setStartValue(QRect(0, 0, w, h))
        self._slide_anim.setEndValue(QRect(out_x, 0, w, h))

        self._slide_anim_new.setStartValue(QRect(in_x, 0, w, h))
        self._slide_anim_new.setEndValue(QRect(0, 0, w, h))

        self._anim_group.start()

    def _on_slide_done(self):
        self._overlay.hide()
        self._overlay_new.hide()

    def set_fit_mode(self, mode: FitMode):
        self._fit_mode = mode
        self._zoom_factor = 1.0
        self._apply_fit()

    def zoom_in(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor *= 1.25
        self._apply_fit()

    def zoom_out(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor *= 0.8
        self._apply_fit()

    def reset_zoom(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor = 1.0
        self._apply_fit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._has_image:
            x = event.position().x()
            w = self.viewport().width()
            if x < w * CLICK_ZONE_FRACTION:
                (self.page_forward if self._rtl else self.page_back).emit()
                return
            elif x > w * (1 - CLICK_ZONE_FRACTION):
                (self.page_back if self._rtl else self.page_forward).emit()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.mouse_moved.emit(int(event.position().y()))
        if self._has_image:
            x = event.position().x()
            w = self.viewport().width()
            if x < w * CLICK_ZONE_FRACTION or x > w * (1 - CLICK_ZONE_FRACTION):
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        if not self._has_image:
            super().wheelEvent(event)
            return

        phase = event.phase()
        px = event.pixelDelta().x()
        py = event.pixelDelta().y()

        # New gesture — reset state
        if phase == Qt.ScrollPhase.ScrollBegin:
            self._swipe_x = 0
            self._swipe_y = 0
            self._swipe_fired = False

        # Ignore momentum (inertia after finger lift) — this was causing wrong directions
        if phase == Qt.ScrollPhase.ScrollMomentum or self._swipe_fired:
            return

        self._swipe_x += px
        self._swipe_y += py

        # Fire as soon as threshold is crossed — don't wait for finger lift
        if abs(self._swipe_x) >= 60 and abs(self._swipe_x) > abs(self._swipe_y):
            self._swipe_fired = True
            forward = self._swipe_x < 0  # swipe left = forward in LTR
            if self._rtl:
                forward = not forward
            (self.page_forward if forward else self.page_back).emit()
            return

        # Pass vertical scroll through for zoomed/fit-width views
        if abs(py) > abs(px):
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._has_image and self._fit_mode != FitMode.ACTUAL_SIZE:
            self._apply_fit()

    def _apply_fit(self):
        if not self._has_image:
            return

        self.resetTransform()
        pix_size = self._pixmap_item.pixmap().size()

        if self._fit_mode == FitMode.FIT_PAGE:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        elif self._fit_mode == FitMode.FIT_WIDTH:
            if pix_size.width() > 0:
                scale = self.viewport().width() / pix_size.width()
                self.scale(scale, scale)
            # Snap scroll position to top of the page when entering fit-width
            self.verticalScrollBar().setValue(0)
        else:  # ACTUAL_SIZE with zoom factor
            self.scale(self._zoom_factor, self._zoom_factor)


_BAR_TRACK = QColor("#2d2d2d")
_BAR_FILL = QColor("#4a9eff")
_BAR_BOOKMARK = QColor("#ffffff")
_BOOKMARK_SNAP_PX = 5  # pixels either side of a tick that triggers tooltip


class SeekBar(QWidget):
    """Thin interactive seek bar for scrubbing through comic pages."""

    seeked = pyqtSignal(int)  # page index to jump to

    _VISUAL_H = 3   # height of the drawn bar
    _WIDGET_H = 12  # total hit area height

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self._WIDGET_H)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self._ratio: float = 0.0
        self._drag_ratio: float | None = None
        self._page_count: int = 0
        self._bookmarks: list[tuple[int, str | None]] = []  # (page_index, label)

    def set_page_count(self, n: int):
        self._page_count = n
        self.update()

    def set_progress(self, ratio: float):
        self._ratio = max(0.0, min(1.0, ratio))
        if self._drag_ratio is None:
            self.update()

    def set_bookmarks(self, marks: list[tuple[int, str | None]]) -> None:
        self._bookmarks = marks
        self.update()

    def _ratio_from_x(self, x: float) -> float:
        w = self.width()
        return max(0.0, min(1.0, x / w)) if w > 0 else 0.0

    def _page_from_ratio(self, ratio: float) -> int:
        if self._page_count <= 0:
            return 0
        return min(self._page_count - 1, int(ratio * self._page_count))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_ratio = self._ratio_from_x(event.position().x())
            self.update()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_ratio is not None:
            self._drag_ratio = self._ratio_from_x(event.position().x())
            self.update()
            return

        # Show bookmark tooltip when hovering near a tick
        if self._page_count > 0 and self._bookmarks:
            mx = event.position().x()
            w = self.width()
            for page_idx, label in self._bookmarks:
                tick_x = int(w * page_idx / self._page_count)
                if abs(mx - tick_x) <= _BOOKMARK_SNAP_PX:
                    text = label if label else f"Page {page_idx + 1}"
                    QToolTip.showText(event.globalPosition().toPoint(), text, self)
                    return
        QToolTip.hideText()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_ratio is not None:
            ratio = self._ratio_from_x(event.position().x())
            self._drag_ratio = None
            self.seeked.emit(self._page_from_ratio(ratio))

    def paintEvent(self, event):
        painter = QPainter(self)
        w = self.width()
        y = (self._WIDGET_H - self._VISUAL_H) // 2
        ratio = self._drag_ratio if self._drag_ratio is not None else self._ratio
        painter.fillRect(0, y, w, self._VISUAL_H, _BAR_TRACK)
        painter.fillRect(0, y, int(w * ratio), self._VISUAL_H, _BAR_FILL)

        # Bookmark ticks — 2px white lines spanning full widget height
        if self._page_count > 0 and self._bookmarks:
            painter.setPen(_BAR_BOOKMARK)
            for page_idx, _ in self._bookmarks:
                x = int(w * page_idx / self._page_count)
                painter.drawLine(x, 0, x, self._WIDGET_H)


# ---------------------------------------------------------------------------
# Thumbnail strip
# ---------------------------------------------------------------------------

_THUMB_W = 60
_THUMB_H = 80
_THUMB_SPACING = 4
_STRIP_H = _THUMB_H + 16  # top + bottom padding


class _ThumbLoader(QThread):
    """Loads comic page thumbnails in order and emits each as a QImage."""

    image_ready = pyqtSignal(int, QImage)

    def __init__(self, reader, page_count: int, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._page_count = page_count
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self):
        for i in range(self._page_count):
            if self._abort:
                return
            try:
                data = self._reader.get_page_bytes(i)
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    scaled = img.scaled(
                        _THUMB_W, _THUMB_H,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.image_ready.emit(i, scaled)
            except Exception:
                pass


class _ThumbCell(QLabel):
    clicked = pyqtSignal(int)

    def __init__(self, page_index: int, parent=None):
        super().__init__(parent)
        self._index = page_index
        self.setFixedSize(_THUMB_W, _THUMB_H)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self._refresh_style()

    def _refresh_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "border: 2px solid #4a9eff; background: #0a0a0a;"
            )
        else:
            self.setStyleSheet(
                "border: 2px solid transparent; background: #1a1a1a;"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)


class ThumbnailStrip(QScrollArea):
    """Horizontal strip of page thumbnails for quick navigation."""

    page_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(_STRIP_H)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: #0d0d0d; border: none;")

        self._content = QWidget()
        self._content.setStyleSheet("background: #0d0d0d;")
        self._row = QHBoxLayout(self._content)
        self._row.setSpacing(_THUMB_SPACING)
        self._row.setContentsMargins(8, 8, 8, 8)
        self._row.addStretch()

        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._cells: list[_ThumbCell] = []
        self._current: int = -1
        self._loader: _ThumbLoader | None = None

    def load_comic(self, reader) -> None:
        self._stop_loader()

        # Clear existing cells (all items except the trailing stretch)
        while self._row.count() > 1:
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cells.clear()
        self._current = -1

        page_count = reader.page_count()
        for i in range(page_count):
            cell = _ThumbCell(i)
            cell.clicked.connect(self.page_selected)
            self._row.insertWidget(i, cell)
            self._cells.append(cell)

        self._loader = _ThumbLoader(reader, page_count)
        self._loader.image_ready.connect(self._on_image_ready)
        self._loader.start()

    def set_current(self, page_index: int) -> None:
        if self._current == page_index:
            return
        if 0 <= self._current < len(self._cells):
            self._cells[self._current].set_selected(False)
        self._current = page_index
        if 0 <= page_index < len(self._cells):
            self._cells[page_index].set_selected(True)
            self.ensureWidgetVisible(self._cells[page_index])

    def _on_image_ready(self, index: int, image: QImage) -> None:
        if 0 <= index < len(self._cells):
            self._cells[index].setPixmap(QPixmap.fromImage(image))

    def _stop_loader(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.abort()
            self._loader.wait()
        self._loader = None
