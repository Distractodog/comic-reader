"""Widget that displays comic pages with zoom and fit modes."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


class FitMode(Enum):
    ACTUAL_SIZE = "actual"
    FIT_WIDTH = "width"
    FIT_PAGE = "page"


class ComicViewer(QGraphicsView):
    """Image viewer using QGraphicsView for smooth scrolling and zoom."""

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
