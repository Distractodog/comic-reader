"""Widget that displays comic pages with zoom and fit modes."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel, QWidget

CLICK_ZONE_FRACTION = 0.30  # left/right 30% = nav zones, middle 40% = dead zone


class FitMode(Enum):
    ACTUAL_SIZE = "actual"
    FIT_WIDTH = "width"
    FIT_PAGE = "page"


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

    def set_image(self, image_bytes: bytes, direction: int = 0):
        """Load a new page. direction: 1=forward (slide left), -1=back (slide right), 0=instant."""
        self._anim_group.stop()
        self._overlay.hide()
        self._overlay_new.hide()

        should_animate = direction != 0 and self._has_image
        old_grab = self.viewport().grab() if should_animate else None

        pixmap = QPixmap()
        pixmap.loadFromData(image_bytes)
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
                self.page_back.emit()
                return
            elif x > w * (1 - CLICK_ZONE_FRACTION):
                self.page_forward.emit()
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
            if self._swipe_x < 0:
                self.page_forward.emit()
            else:
                self.page_back.emit()
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
