"""Background page preloader with thread-safe LRU image cache."""

from __future__ import annotations

import threading
from collections import OrderedDict

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QImage

_PRELOAD_OFFSETS = [1, 2, -1, 3, 4, 5, -2]
_CACHE_MAX = 20


class PageCache:
    """Thread-safe LRU cache mapping page index → QImage."""

    def __init__(self, max_pages: int = _CACHE_MAX):
        self._max = max_pages
        self._data: OrderedDict[int, QImage] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, index: int) -> QImage | None:
        with self._lock:
            if index not in self._data:
                return None
            self._data.move_to_end(index)
            return self._data[index]

    def put(self, index: int, image: QImage) -> None:
        with self._lock:
            self._data[index] = image
            self._data.move_to_end(index)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class PagePreloader(QThread):
    """Preloads pages around the current reading position into a PageCache."""

    def __init__(self, reader, cache: PageCache, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._cache = cache
        self._center = 0
        self._lock = threading.Lock()
        self._abort = threading.Event()

    def set_center(self, page_index: int) -> None:
        with self._lock:
            self._center = page_index

    def abort(self) -> None:
        self._abort.set()

    def run(self):
        page_count = self._reader.page_count()
        while not self._abort.is_set():
            with self._lock:
                center = self._center

            did_work = False
            for offset in _PRELOAD_OFFSETS:
                if self._abort.is_set():
                    return
                with self._lock:
                    if self._center != center:
                        break  # center moved — restart priority order
                idx = center + offset
                if 0 <= idx < page_count and self._cache.get(idx) is None:
                    try:
                        data = self._reader.get_page_bytes(idx)
                        img = QImage()
                        img.loadFromData(data)
                        if not img.isNull():
                            self._cache.put(idx, img)
                            did_work = True
                    except Exception:
                        pass

            if not did_work:
                self._abort.wait(0.2)
