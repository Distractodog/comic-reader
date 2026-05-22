"""Widget that displays comic pages with zoom and fit modes."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QWidget

CLICK_ZONE_FRACTION = 0.30  # left/right 30% = nav zones, middle 40% = dead zone


class FitMode(Enum):
    ACTUAL_SIZE = "actual"
    FIT_WIDTH = "width"
    FIT_PAGE = "page"


class ComicViewer(QGraphicsView):
    """Image viewer using QGraphicsView for smooth scrolling and zoom."""

    page_forward = pyqtSignal()
    page_back = pyqtSignal()

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
        self.setStyleSheet("background-color: #1a1a1a; border: none;")

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._fit_mode = FitMode.FIT_PAGE
        self._zoom_factor = 1.0
        self._has_image = False

        self.setMouseTracking(True)

    def set_image(self, image_bytes: bytes):
        """Load a new page from raw image bytes."""
        pixmap = QPixmap()
        pixmap.loadFromData(image_bytes)
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(pixmap.rect().toRectF())
        self._zoom_factor = 1.0
        self._has_image = True
        self._apply_fit()

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
                self.page_back.emit()
                return
            elif x > w * (1 - CLICK_ZONE_FRACTION):
                self.page_forward.emit()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._has_image:
            x = event.position().x()
            w = self.viewport().width()
            if x < w * CLICK_ZONE_FRACTION or x > w * (1 - CLICK_ZONE_FRACTION):
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

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


class SeekBar(QWidget):
    """Thin interactive seek bar for scrubbing through comic pages."""

    seeked = pyqtSignal(int)  # page index to jump to

    _VISUAL_H = 3   # height of the drawn bar
    _WIDGET_H = 12  # total hit area height

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self._WIDGET_H)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self._ratio: float = 0.0
        self._drag_ratio: float | None = None
        self._page_count: int = 0

    def set_page_count(self, n: int):
        self._page_count = n

    def set_progress(self, ratio: float):
        self._ratio = max(0.0, min(1.0, ratio))
        if self._drag_ratio is None:
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
