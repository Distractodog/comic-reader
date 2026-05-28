"""Continuous vertical scroll viewer for webtoon / manhwa comics."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

_BUFFER_PX = 800  # pixels above/below viewport to keep loaded


class _PageLoader(QThread):
    """Loads a range of pages in priority order, emitting each as a QImage."""

    image_ready = pyqtSignal(int, QImage)

    def __init__(self, reader, indices: list[int], parent=None):
        super().__init__(parent)
        self._reader = reader
        self._indices = indices
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
                    self.image_ready.emit(idx, img)
            except Exception:
                pass


class WebtoonViewer(QScrollArea):
    """Vertically scrolling viewer that loads pages lazily as the user scrolls."""

    page_changed = pyqtSignal(int)  # current page at viewport center

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("background-color: #000000; border: none;")
        self.setWidgetResizable(True)

        self._content = QWidget()
        self._content.setStyleSheet("background-color: #000000;")
        self._layout = QVBoxLayout(self._content)
        self._layout.setSpacing(8)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setWidget(self._content)

        self._labels: list[QLabel] = []
        self._originals: dict[int, QImage] = {}  # original images for rescaling
        self._loaded: set[int] = set()
        self._reader = None
        self._page_count = 0
        self._current_page = 0
        self._last_emitted = -1
        self._loader: _PageLoader | None = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1500)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    # ----- Public API -----

    def load_comic(self, reader, start_page: int = 0) -> None:
        self._stop_loader()
        self._reader = reader
        self._page_count = reader.page_count()
        self._loaded.clear()
        self._originals.clear()
        self._last_emitted = -1

        # Remove old labels
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._labels.clear()

        # Create placeholder labels for each page
        for _ in range(self._page_count):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("background-color: #111111;")
            lbl.setMinimumHeight(300)
            self._layout.addWidget(lbl)
            self._labels.append(lbl)

        # Scroll to start page after layout settles, then load visible pages
        if start_page > 0:
            QTimer.singleShot(120, lambda: self._scroll_to(start_page))
        else:
            QTimer.singleShot(50, self._load_visible)

    def scroll_to_page(self, page_index: int) -> None:
        self._scroll_to(page_index)

    def current_page(self) -> int:
        return self._current_page

    # ----- Internal -----

    def _scroll_to(self, page_index: int) -> None:
        if 0 <= page_index < len(self._labels):
            self.ensureWidgetVisible(self._labels[page_index])
            self._load_visible()

    def _on_scroll(self) -> None:
        self._update_current_page()
        self._load_visible()

    def _update_current_page(self) -> None:
        if not self._labels:
            return
        vp_center = self.verticalScrollBar().value() + self.viewport().height() // 2
        best = 0
        for i, lbl in enumerate(self._labels):
            lbl_center = lbl.mapTo(self._content, lbl.rect().center()).y()
            if lbl_center <= vp_center:
                best = i
        if best != self._last_emitted:
            self._current_page = best
            self._last_emitted = best
            self.page_changed.emit(best)

    def _load_visible(self) -> None:
        if not self._reader or not self._labels:
            return
        scroll_top = self.verticalScrollBar().value()
        scroll_bot = scroll_top + self.viewport().height()
        load_top = scroll_top - _BUFFER_PX
        load_bot = scroll_bot + _BUFFER_PX

        # Collect pages that need loading, in viewport-priority order
        near: list[int] = []
        far: list[int] = []
        for i, lbl in enumerate(self._labels):
            if i in self._loaded:
                continue
            lbl_top = lbl.mapTo(self._content, lbl.rect().topLeft()).y()
            lbl_bot = lbl_top + lbl.height()
            if lbl_bot >= load_top and lbl_top <= load_bot:
                # Prioritise: in-viewport first, then buffer zone
                if lbl_bot >= scroll_top and lbl_top <= scroll_bot:
                    near.append(i)
                else:
                    far.append(i)

        to_load = near + far
        if not to_load:
            return

        self._stop_loader()
        self._loader = _PageLoader(self._reader, to_load)
        self._loader.image_ready.connect(self._on_image_ready)
        self._loader.start()

    def _on_image_ready(self, index: int, image: QImage) -> None:
        if index < 0 or index >= len(self._labels):
            return
        self._originals[index] = image
        self._loaded.add(index)
        self._render(index)

    def _render(self, index: int) -> None:
        img = self._originals.get(index)
        if img is None:
            return
        w = self.viewport().width()
        if w <= 0:
            return
        scaled = img.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
        lbl = self._labels[index]
        lbl.setPixmap(QPixmap.fromImage(scaled))
        lbl.setFixedHeight(scaled.height())

    def _stop_loader(self) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.abort()
            self._loader.wait()
        self._loader = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-render all loaded pages at new width
        w = self.viewport().width()
        if w <= 0:
            return
        for i in list(self._loaded):
            img = self._originals.get(i)
            if img is None:
                continue
            scaled = img.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
            lbl = self._labels[i]
            lbl.setPixmap(QPixmap.fromImage(scaled))
            lbl.setFixedHeight(scaled.height())
