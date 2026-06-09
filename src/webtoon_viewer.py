"""Continuous vertical scroll viewer for webtoon / manhwa comics."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

_BUFFER_PX = 600     # pixels above/below viewport to preload
_RETAIN_RADIUS = 10  # keep full-res images within ±N pages of the current page


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

    page_changed = pyqtSignal(int)  # current page at viewport center
    mouse_moved = pyqtSignal(int)   # viewport y — for fullscreen bar reveal

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.setWidgetResizable(True)

        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)

        self._width_fraction: float = 1.0

        self._content = QWidget()
        self._content.setStyleSheet("background-color: #000000;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setSpacing(8)
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
        if obj is self.viewport() and event.type() == QEvent.Type.MouseMove:
            self.mouse_moved.emit(int(event.position().y()))
        return super().eventFilter(obj, event)

    # ----- Public API -----

    def set_width_fraction(self, fraction: float) -> None:
        self._width_fraction = max(0.3, min(1.0, fraction))
        self._rerender_all()

    def load_comic(self, reader, start_page: int = 0) -> None:
        self._abort_loader()
        self._reader = reader
        self._page_count = reader.page_count()
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

        if start_page > 0:
            QTimer.singleShot(120, lambda: self._scroll_to(start_page))
        else:
            QTimer.singleShot(50, self._load_visible)

    def scroll_to_page(self, page_index: int) -> None:
        self._scroll_to(page_index)

    def current_page(self) -> int:
        return self._current_page

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

    def _scroll_to(self, page_index: int) -> None:
        if 0 <= page_index < len(self._labels):
            self.ensureWidgetVisible(self._labels[page_index])
            self._load_visible()

    def _on_scroll(self) -> None:
        self._update_current_page()
        self._load_timer.start()

    def _update_current_page(self) -> None:
        if not self._labels or not self._tops:
            return
        vp_center = self.verticalScrollBar().value() + self.viewport().height() // 2

        lo, hi = 0, len(self._labels) - 1
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            lbl_center = self._tops[mid] + self._labels[mid].height() // 2
            if lbl_center <= vp_center:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

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
