"""Widget that displays comic pages with zoom and fit modes."""

from __future__ import annotations

from enum import Enum

from PyQt6.QtCore import Qt, QEasingCurve, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QThread, QTimer, pyqtSignal
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
        # Full-resolution source kept for zoom; the scene shows a pixmap pre-scaled
        # to the viewport so the view never smooth-scales a huge image per repaint.
        self._source_pixmap: QPixmap | None = None
        self._display_key = None
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

        self._source_pixmap = pixmap
        self._display_key = None
        self._zoom_factor = 1.0
        self._has_image = True
        self._apply_display_pixmap()
        self._apply_fit()

        if old_grab is not None:
            new_grab = self.viewport().grab()
            self._run_slide(old_grab, new_grab, direction)

    def _apply_display_pixmap(self):
        """Put a viewport-sized pixmap in the scene for fit modes (cheap to paint),
        or the full-resolution source when zoomed. Skips work when nothing changed."""
        src = self._source_pixmap
        if src is None or src.isNull():
            return
        smooth = Qt.TransformationMode.SmoothTransformation
        if self._fit_mode == FitMode.ACTUAL_SIZE:
            key = ("actual",)
            disp = src
        elif self._fit_mode == FitMode.FIT_WIDTH:
            vw = max(1, self.viewport().width())
            if src.width() > vw:
                key = ("fitw", vw)
                disp = src.scaledToWidth(vw, smooth)
            else:
                key = ("src",)
                disp = src
        else:  # FIT_PAGE — constrained by whichever dimension is tighter
            vw = max(1, self.viewport().width())
            vh = max(1, self.viewport().height())
            if src.width() > vw or src.height() > vh:
                key = ("fitp", vw, vh)
                disp = src.scaled(
                    vw, vh, Qt.AspectRatioMode.KeepAspectRatio, smooth
                )
            else:
                key = ("src",)
                disp = src
        if key == self._display_key:
            return
        self._display_key = key
        self._pixmap_item.setPixmap(disp)
        self._scene.setSceneRect(disp.rect().toRectF())

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

    @property
    def fit_mode(self) -> FitMode:
        return self._fit_mode

    @property
    def zoom_factor(self) -> float:
        return self._zoom_factor

    def restore_view_state(self, fit_mode: FitMode, zoom: float) -> None:
        """Restore saved fit mode + zoom without the zoom-reset that set_fit_mode does."""
        self._fit_mode = fit_mode
        self._zoom_factor = float(zoom)
        if self._has_image:
            self._apply_display_pixmap()
            self._apply_fit()

    def set_fit_mode(self, mode: FitMode):
        self._fit_mode = mode
        self._zoom_factor = 1.0
        self._apply_display_pixmap()
        self._apply_fit()

    def zoom_in(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor *= 1.25
        self._apply_display_pixmap()
        self._apply_fit()

    def zoom_out(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor *= 0.8
        self._apply_display_pixmap()
        self._apply_fit()

    def reset_zoom(self):
        self._fit_mode = FitMode.ACTUAL_SIZE
        self._zoom_factor = 1.0
        self._apply_display_pixmap()
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
            self._apply_display_pixmap()
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


_BAR_TRACK = QColor("#4a3535")        # matches dark theme progress_track
_BAR_FILL = QColor("#c06060")         # matches dark theme progress_fill
_BAR_HANDLE_HOVER = QColor("#f5e6e6") # matches dark theme text (handle :hover)
_BAR_BOOKMARK = QColor("#ffffff")
_BAR_NOTE = QColor("#f0c76a")
_BOOKMARK_SNAP_PX = 5  # pixels either side of a tick that triggers tooltip


class SeekBar(QWidget):
    """Thin interactive seek bar for scrubbing through comic pages."""

    seeked = pyqtSignal(int)  # page index to jump to

    _GROOVE_H = 4    # height of the drawn groove
    _HANDLE_D = 14   # diameter of the round handle
    _WIDGET_H = 16   # total hit area height (fits the handle)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self._WIDGET_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._ratio: float = 0.0
        self._drag_ratio: float | None = None
        self._page_count: int = 0
        self._hover: bool = False
        self._bookmarks: list[tuple[int, str | None]] = []  # (page_index, label)
        self._notes: list[tuple[int, str]] = []              # (page_index, body)

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

    def set_notes(self, notes: list[tuple[int, str]]) -> None:
        self._notes = notes
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

        # Show bookmark/note tooltip when hovering near a tick.
        if self._page_count > 0 and (self._bookmarks or self._notes):
            mx = event.position().x()
            w = self.width()
            for page_idx, body in self._notes:
                tick_x = int(w * page_idx / self._page_count)
                if abs(mx - tick_x) <= _BOOKMARK_SNAP_PX:
                    preview = body.replace("\n", " ").strip()
                    if len(preview) > 80:
                        preview = preview[:77] + "..."
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        f"Note, page {page_idx + 1}: {preview}",
                        self,
                    )
                    return
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

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        w = self.width()
        cy = self._WIDGET_H / 2
        gy = cy - self._GROOVE_H / 2
        r = self._GROOVE_H / 2
        ratio = self._drag_ratio if self._drag_ratio is not None else self._ratio

        # Rounded groove + fill (sub-page), matching the ebook slider.
        painter.setBrush(_BAR_TRACK)
        painter.drawRoundedRect(QRectF(0, gy, w, self._GROOVE_H), r, r)
        if ratio > 0:
            painter.setBrush(_BAR_FILL)
            painter.drawRoundedRect(QRectF(0, gy, w * ratio, self._GROOVE_H), r, r)

        # Bookmark ticks — white lines spanning the full widget height.
        if self._page_count > 0 and self._bookmarks:
            painter.setPen(_BAR_BOOKMARK)
            for page_idx, _ in self._bookmarks:
                x = int(w * page_idx / self._page_count)
                painter.drawLine(x, 0, x, self._WIDGET_H)
            painter.setPen(Qt.PenStyle.NoPen)

        # Annotation ticks — shorter gold lines so notes and bookmarks are distinct.
        if self._page_count > 0 and self._notes:
            painter.setPen(_BAR_NOTE)
            for page_idx, _ in self._notes:
                x = int(w * page_idx / self._page_count)
                painter.drawLine(x, 2, x, self._WIDGET_H - 2)
            painter.setPen(Qt.PenStyle.NoPen)

        # Round handle at the current position.
        hx = max(self._HANDLE_D / 2, min(w - self._HANDLE_D / 2, w * ratio))
        painter.setBrush(_BAR_HANDLE_HOVER if self._hover else _BAR_FILL)
        painter.drawEllipse(QPointF(hx, cy), self._HANDLE_D / 2, self._HANDLE_D / 2)


# ---------------------------------------------------------------------------
# Thumbnail strip
# ---------------------------------------------------------------------------

_THUMB_W = 60
_THUMB_H = 80
_THUMB_SPACING = 4
_STRIP_H = _THUMB_H + 16  # top + bottom padding


class _ThumbLoader(QThread):
    """Loads thumbnails for a given list of page indices, emitting each as a QImage."""

    image_ready = pyqtSignal(int, int, QImage)  # gen, page_index, image

    def __init__(self, reader, indices: list[int], gen: int, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._indices = indices
        self._gen = gen
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self):
        for i in self._indices:
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
                    self.image_ready.emit(self._gen, i, scaled)
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
        self._reader = None
        self._loaded: set[int] = set()
        self._loader: _ThumbLoader | None = None
        self._loader_gen: int = 0

        # Debounce scroll → load so fast scrubbing doesn't spawn a thread per pixel.
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(60)
        self._load_timer.timeout.connect(self._load_visible)
        self.horizontalScrollBar().valueChanged.connect(self._load_timer.start)

    def load_comic(self, reader) -> None:
        self.stop()
        self._reader = reader
        self._loaded.clear()

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

        # Cells aren't laid out yet — defer the first visible-range load.
        QTimer.singleShot(50, self._load_visible)

    def set_current(self, page_index: int) -> None:
        if self._current == page_index:
            return
        if 0 <= self._current < len(self._cells):
            self._cells[self._current].set_selected(False)
        self._current = page_index
        if 0 <= page_index < len(self._cells):
            self._cells[page_index].set_selected(True)
            self.ensureWidgetVisible(self._cells[page_index])
            self._load_timer.start()

    def _load_visible(self) -> None:
        if not self._reader or not self._cells:
            return
        sx = self.horizontalScrollBar().value()
        vw = self.viewport().width()
        buf = 200  # px of look-ahead on each side
        lo, hi = sx - buf, sx + vw + buf
        want = [
            i for i, c in enumerate(self._cells)
            if i not in self._loaded and c.x() <= hi and c.x() + c.width() >= lo
        ]
        if not want:
            return
        self._stop_loader()
        self._loader_gen += 1
        self._loader = _ThumbLoader(self._reader, want, self._loader_gen)
        self._loader.image_ready.connect(self._on_image_ready)
        self._loader.start()

    def _on_image_ready(self, gen: int, index: int, image: QImage) -> None:
        if gen != self._loader_gen:
            return  # stale result — discard
        if 0 <= index < len(self._cells):
            self._cells[index].setPixmap(QPixmap.fromImage(image))
            self._loaded.add(index)

    def _stop_loader(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.abort()
            self._loader.wait()
        self._loader = None

    def stop(self) -> None:
        """Stop the background loader — call before the bound reader is closed."""
        self._stop_loader()
