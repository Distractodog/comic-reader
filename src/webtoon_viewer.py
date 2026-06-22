"""Continuous vertical scroll viewer for webtoon / manhwa comics."""

from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve, QEvent, QPropertyAnimation, Qt, QThread, QTimer, pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from viewer import CLICK_ZONE_FRACTION  # center band = same as single-page reader

_BUFFER_PX = 600     # pixels above/below viewport to preload
_RETAIN_RADIUS = 10  # keep full-res images within ±N pages of the current page
_CLICK_MOVE_TOL = 6  # max px of movement for a press+release to count as a click
_EDGE_SCROLL_FRACTION = 0.9   # left/right edge click scrolls down ~a full screen
                              # (slight overlap so you don't lose your place)
_EDGE_SCROLL_MS = 520         # smooth-scroll animation duration


class _Spinner(QWidget):
    """Small rotating arc floated over the viewport while visible pages decode.

    Matches the main loading overlay's spinner so the app reads consistently.
    Transparent to mouse events so click-zone paging still works underneath."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 60)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 6.5) % 360  # one turn ≈ 0.9s
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        if not self.isVisible():
            self.show()
            self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(235, 230, 230, 235), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        r = 18
        cx, cy = self.width() // 2, self.height() // 2
        p.drawArc(cx - r, cy - r, 2 * r, 2 * r, int(-self._angle * 16), 100 * 16)


class _PageLoader(QThread):
    """Loads a list of pages in order, scaling off the UI thread."""

    image_ready = pyqtSignal(int, int, QImage, QImage)  # gen, page_index, original, scaled

    def __init__(self, reader, indices: list[int], gen: int, display_w: int, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._indices = indices
        self._gen = gen
        self._display_w = display_w
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self):
        for idx in self._indices:
            if self._abort:
                return
            try:
                data = self._reader.get_page_bytes(idx)
                img = QImage()
                img.loadFromData(data)
                if not img.isNull():
                    scaled = img.scaledToWidth(
                        self._display_w, Qt.TransformationMode.FastTransformation
                    )
                    self.image_ready.emit(self._gen, idx, img, scaled)
            except Exception:
                pass


class WebtoonViewer(QScrollArea):
    """Vertically scrolling viewer that loads pages lazily as the user scrolls."""

    page_changed = pyqtSignal(int)  # current page at viewport top
    mouse_moved = pyqtSignal(int)   # viewport y — for fullscreen bar reveal
    start_page_rendered = pyqtSignal()  # the page load_comic targeted is on screen
    center_clicked = pyqtSignal()   # center-band click — toggles reader chrome
    scrolled = pyqtSignal()         # any scroll — drives debounced progress save

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.setWidgetResizable(True)

        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

        # Spinner shown over the viewport whenever the page(s) in view haven't
        # decoded yet (resume reload, scrolling into not-yet-loaded territory) —
        # avoids staring at blank placeholder boxes.
        self._spinner = _Spinner(self.viewport())

        self._width_fraction: float = 1.0

        self._content = QWidget()
        self._content.setStyleSheet("background-color: #000000;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setSpacing(0)  # pages sit flush — no seam line between images
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setWidget(self._content)

        self._labels: list[QLabel] = []
        self._tops: list[int] = []  # cached y of each label top — avoids O(n) layout queries
        self._originals: dict[int, QImage] = {}
        self._loaded: set[int] = set()
        self._loading: set[int] = set()
        self._reader = None
        self._page_count = 0
        self._current_page = 0
        self._last_emitted = -1
        self._pending_start: int | None = None
        self._pending_fraction: float = 0.0  # offset into _pending_start to restore
        self._press_pos = None  # left-button press position, for click detection
        self._scroll_anim: QPropertyAnimation | None = None  # smooth edge-scroll

        # Generation counter: stale loader signals are dropped without blocking
        self._loader: _PageLoader | None = None
        self._loader_gen: int = 0

        # Debounce scroll → load so we don't spawn a thread on every pixel
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(80)
        self._load_timer.timeout.connect(self._load_visible)

        # Debounce resize → re-render
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(150)
        self._resize_timer.timeout.connect(self._rerender_all)

        # Batch pixmap/height updates to one layout pass per frame
        self._render_queue: list[tuple[int, QImage]] = []
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._flush_renders)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ----- Event filter for mouse tracking -----

    def eventFilter(self, obj, event):
        if obj is self.viewport():
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                self.mouse_moved.emit(int(event.position().y()))
            elif (etype == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._press_pos = event.position()
            elif (etype == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton
                    and self._press_pos is not None):
                start = self._press_pos
                self._press_pos = None
                pos = event.position()
                moved = (pos - start).manhattanLength()
                if moved <= _CLICK_MOVE_TOL:  # a click, not a drag-scroll
                    w = self.viewport().width()
                    x = pos.x()
                    if w * CLICK_ZONE_FRACTION <= x <= w * (1 - CLICK_ZONE_FRACTION):
                        # Center band toggles the reader chrome — matching the
                        # single-page reader's dead zone.
                        self.center_clicked.emit()
                    else:
                        # Left/right edges advance the scroll by a fraction of a
                        # screen (there's no page-flip in continuous scroll).
                        self._scroll_by_fraction(_EDGE_SCROLL_FRACTION)
        return super().eventFilter(obj, event)

    # ----- Public API -----

    def set_width_fraction(self, fraction: float) -> None:
        self._width_fraction = max(0.3, min(1.0, fraction))
        self._rerender_all()

    def load_comic(
        self, reader, start_page: int = 0, start_fraction: float = 0.0
    ) -> None:
        self._abort_loader()
        self._reader = reader
        self._page_count = reader.page_count()
        # Watched by the main window's loading screen: reveal once this page
        # is actually rendered, not merely once the archive is open.
        self._pending_start: int | None = (
            min(max(start_page, 0), self._page_count - 1) if self._page_count > 0 else None
        )
        # Exact scroll offset within the start page, applied once it renders.
        self._pending_fraction = max(0.0, min(1.0, start_fraction))
        self._loaded.clear()
        self._loading.clear()
        self._originals.clear()
        self._render_queue.clear()
        self._last_emitted = -1

        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._labels.clear()
        self._tops.clear()

        for _ in range(self._page_count):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("background-color: #111111;")
            lbl.setMinimumHeight(300)
            self._layout.addWidget(lbl)
            self._labels.append(lbl)

        self._rebuild_tops()
        self._current_page = max(0, start_page)

        if start_page > 0 or self._pending_fraction > 0:
            QTimer.singleShot(120, lambda: self._scroll_to(start_page))
        else:
            # The viewer is reused across comics — pin a fresh comic to the top
            # so it doesn't inherit the previous comic's scroll offset. Reset now
            # and again after layout settles, in case the range updates late.
            self.verticalScrollBar().setValue(0)
            QTimer.singleShot(50, lambda: (
                self.verticalScrollBar().setValue(0), self._load_visible()
            ))

        self._update_spinner()  # show immediately; cleared once pages decode

    def scroll_to_page(self, page_index: int) -> None:
        self._scroll_to(page_index)

    def current_page(self) -> int:
        return self._current_page

    def scroll_anchor(self) -> tuple[int, float]:
        """Current resume point: (page at viewport top, fraction 0..1 into it)."""
        return self._top_anchor()

    # ----- Internal -----

    def _rebuild_tops(self, from_index: int = 0) -> None:
        """Recompute cached label tops from *from_index* onward."""
        spacing = self._layout.spacing()
        if from_index <= 0:
            y = 0
            self._tops = []
            from_index = 0
        else:
            prev = from_index - 1
            y = self._tops[prev] + self._labels[prev].height() + spacing
            del self._tops[from_index:]

        for i in range(from_index, len(self._labels)):
            self._tops.append(y)
            y += self._labels[i].height() + spacing

    def _scroll_by_fraction(self, fraction: float) -> None:
        """Smoothly scroll down by *fraction* of the viewport height."""
        sb = self.verticalScrollBar()
        delta = int(self.viewport().height() * fraction)
        # Stack rapid clicks onto the in-flight animation's destination so each
        # tap advances another step instead of restarting from the same spot.
        anim = self._scroll_anim
        if anim is not None and anim.state() == QPropertyAnimation.State.Running:
            base = anim.endValue()
        else:
            base = sb.value()
        target = max(sb.minimum(), min(sb.maximum(), base + delta))

        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(_EDGE_SCROLL_MS)
        anim.setStartValue(sb.value())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._scroll_anim = anim
        anim.start()

    def _scroll_to(self, page_index: int) -> None:
        if 0 <= page_index < len(self._labels):
            self.ensureWidgetVisible(self._labels[page_index])
            self._load_visible()

    def _on_scroll(self) -> None:
        self._update_current_page()
        self._load_timer.start()
        self._update_spinner()
        self.scrolled.emit()

    # ----- Loading spinner -----

    def _visible_pages_loaded(self) -> bool:
        """True when every page intersecting the viewport has decoded."""
        if not self._reader or not self._labels or not self._tops:
            return True
        scroll_top = self.verticalScrollBar().value()
        scroll_bot = scroll_top + self.viewport().height()
        for i, lbl in enumerate(self._labels):
            top = self._tops[i]
            bot = top + lbl.height()
            if bot >= scroll_top and top <= scroll_bot and i not in self._loaded:
                return False
        return True

    def _update_spinner(self) -> None:
        if self._reader and not self._visible_pages_loaded():
            vp = self.viewport()
            self._spinner.move(
                (vp.width() - self._spinner.width()) // 2,
                (vp.height() - self._spinner.height()) // 2,
            )
            self._spinner.start()
        else:
            self._spinner.stop()

    def _top_anchor(self) -> tuple[int, float]:
        """The page occupying the viewport top, and how far (0..1) into it.

        Anchored on the top edge — not the center — because manhwa pages are
        often taller than the viewport, so a center anchor reports the previous
        page while you're reading the top of the current one (the "pushed back a
        page" bug). The fraction lets us resume at the exact pixel offset."""
        if not self._labels or not self._tops:
            return (0, 0.0)
        sb = self.verticalScrollBar()
        scroll_top = sb.value()
        # At the very bottom, call it the last page so finishing marks the comic
        # read (a tall final page would otherwise never reach the top edge).
        if scroll_top >= sb.maximum() - 2:
            return (len(self._labels) - 1, 0.0)

        lo, hi = 0, len(self._labels) - 1
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._tops[mid] <= scroll_top:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        height = self._labels[best].height()
        frac = 0.0 if height <= 0 else (scroll_top - self._tops[best]) / height
        return (best, max(0.0, min(1.0, frac)))

    def _update_current_page(self) -> None:
        if not self._labels or not self._tops:
            return
        best, _ = self._top_anchor()
        if best != self._last_emitted:
            self._current_page = best
            self._last_emitted = best
            self.page_changed.emit(best)

    def _current_display_w(self) -> int:
        return max(1, int(self.viewport().width() * self._width_fraction))

    def _evict_far(self) -> None:
        """Drop full-resolution images for pages far from the current page so memory
        stays bounded on long comics. The label keeps its fixed height (so scroll
        position is preserved) and the page is reloaded if scrolled back into view."""
        if len(self._loaded) <= 2 * _RETAIN_RADIUS:
            return
        cur = self._current_page
        for idx in list(self._loaded):
            if abs(idx - cur) > _RETAIN_RADIUS:
                self._originals.pop(idx, None)
                self._loaded.discard(idx)
                if 0 <= idx < len(self._labels):
                    self._labels[idx].clear()

    def _load_visible(self) -> None:
        if not self._reader or not self._labels:
            return
        self._evict_far()
        scroll_top = self.verticalScrollBar().value()
        scroll_bot = scroll_top + self.viewport().height()
        load_top = scroll_top - _BUFFER_PX
        load_bot = scroll_bot + _BUFFER_PX

        near: list[int] = []
        far: list[int] = []
        for i, lbl in enumerate(self._labels):
            if i in self._loaded or i in self._loading:
                continue
            top = self._tops[i]
            bot = top + lbl.height()
            if bot >= load_top and top <= load_bot:
                if bot >= scroll_top and top <= scroll_bot:
                    near.append(i)
                else:
                    far.append(i)

        self._update_spinner()

        to_load = near + far
        if not to_load:
            return

        # Keep the current loader running if all viewport pages are already in flight.
        urgent = set(near) - self._loaded - self._loading
        if self._loader and self._loader.isRunning() and not urgent:
            return

        self._abort_loader()
        self._loader_gen += 1
        gen = self._loader_gen
        self._loading = set(to_load)
        self._loader = _PageLoader(self._reader, to_load, gen, self._current_display_w())
        self._loader.image_ready.connect(self._on_image_ready)
        self._loader.start()

    def _on_image_ready(
        self, gen: int, index: int, original: QImage, scaled: QImage
    ) -> None:
        if gen != self._loader_gen:
            return  # stale result — discard
        if index < 0 or index >= len(self._labels):
            return
        self._loading.discard(index)
        self._originals[index] = original
        self._loaded.add(index)
        self._render_queue.append((index, scaled))
        self._render_timer.start()

    def _flush_renders(self) -> None:
        queue = self._render_queue
        self._render_queue = []
        if not queue:
            return

        first_changed = min(index for index, _ in queue)
        self._content.setUpdatesEnabled(False)
        try:
            for index, scaled in queue:
                lbl = self._labels[index]
                lbl.setPixmap(QPixmap.fromImage(scaled))
                lbl.setFixedHeight(scaled.height())
            self._rebuild_tops(first_changed)
        finally:
            self._content.setUpdatesEnabled(True)

        if self._pending_start is not None and any(
            index == self._pending_start for index, _ in queue
        ):
            page = self._pending_start
            self._pending_start = None
            self._apply_pending_fraction(page)
            self.start_page_rendered.emit()

        self._update_spinner()

    def _apply_pending_fraction(self, page: int) -> None:
        """Now that `page` has its real rendered height, nudge the scroll to the
        exact within-page offset we saved last session."""
        frac = self._pending_fraction
        self._pending_fraction = 0.0
        if frac <= 0 or not (0 <= page < len(self._labels)):
            return
        self._rebuild_tops()
        height = self._labels[page].height()
        sb = self.verticalScrollBar()
        target = int(self._tops[page] + frac * height)
        sb.setValue(max(sb.minimum(), min(sb.maximum(), target)))
        self._load_visible()

    def _render(self, index: int) -> None:
        img = self._originals.get(index)
        if img is None:
            return
        display_w = self._current_display_w()
        scaled = img.scaledToWidth(display_w, Qt.TransformationMode.FastTransformation)
        lbl = self._labels[index]
        lbl.setPixmap(QPixmap.fromImage(scaled))
        lbl.setFixedHeight(scaled.height())

    def _rerender_all(self) -> None:
        loaded = list(self._loaded)
        if not loaded:
            return
        self._content.setUpdatesEnabled(False)
        try:
            for i in loaded:
                self._render(i)
            self._rebuild_tops()
        finally:
            self._content.setUpdatesEnabled(True)

    def _abort_loader(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.abort()
        self._loader = None
        self._loading.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start()
        self._update_spinner()  # keep the spinner centered on resize
