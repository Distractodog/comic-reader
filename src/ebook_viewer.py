"""Text/novel EPUB reader view — paginated, using Qt's built-in QTextBrowser.

The book is rendered one chapter at a time, but instead of a single long
scroll, each chapter is split into screen-sized **pages**. Next/Prev (buttons,
arrow keys, space, or the mouse wheel) flip one page at a time and roll over
into the next/previous chapter at the edges. Rendering fidelity is "good
novel", not pixel-perfect CSS — by design (no web-engine dependency).
"""

from __future__ import annotations

import math
from posixpath import normpath

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from epub_book import EpubBook

# Reading page colours (a calm "paper" look, independent of the dark chrome, so
# the book's own black text stays readable regardless of app theme).
_PAGE_BG = "#f4ecd8"
_PAGE_FG = "#2a2420"

# Pixels of the previous page kept visible when flipping, so no line is lost.
_PAGE_OVERLAP = 36

MIN_FONT_PT = 11
MAX_FONT_PT = 32
DEFAULT_FONT_PT = 19


class _BookTextBrowser(QTextBrowser):
    """QTextBrowser that pulls images from the EPUB and forwards wheel turns."""

    wheel_turned = pyqtSignal(int)  # +1 = next page, -1 = previous page

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book: EpubBook | None = None
        self._base = ""
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        # Pagination is driven explicitly; hide the scrollbar and keep keyboard
        # focus on the window so app shortcuts (arrows/space) reach navigation.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_source(self, book: EpubBook | None, base: str) -> None:
        self._book = book
        self._base = base

    def loadResource(self, type_, url):
        if self._book is not None:
            ref = url.toString().split("#")[0].split("?")[0]
            if ref:
                abs_href = normpath(f"{self._base}/{ref}" if self._base else ref)
                data = self._book.read_resource(abs_href)
                if data is not None:
                    img = QImage.fromData(data)
                    if not img.isNull():
                        # Scale oversized images down to the page width so a big
                        # cover doesn't become a giant scroll (QTextBrowser
                        # ignores CSS max-width).
                        avail = max(1, self.viewport().width() - 8)
                        if img.width() > avail:
                            img = img.scaledToWidth(
                                avail, Qt.TransformationMode.SmoothTransformation
                            )
                        return img
        return super().loadResource(type_, url)

    def wheelEvent(self, event):
        self.wheel_turned.emit(1 if event.angleDelta().y() < 0 else -1)
        event.accept()


class EbookViewer(QWidget):
    chapter_changed = pyqtSignal(int)  # current chapter index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book: EpubBook | None = None
        self._index = 0
        self._page = 0
        self._page_count = 1
        self._font_pt = DEFAULT_FONT_PT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._browser = _BookTextBrowser(self)
        self._browser.document().setDocumentMargin(28)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: {_PAGE_BG}; color: {_PAGE_FG};"
            f" border: none; }}"
        )
        self._browser.wheel_turned.connect(self._on_wheel)
        layout.addWidget(self._browser, 1)

        # Bottom navigation bar
        bar = QWidget(self)
        bar.setObjectName("EbookNavBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(8)

        self._prev_btn = QPushButton("‹ Prev")
        self._prev_btn.clicked.connect(self.prev_page)
        self._next_btn = QPushButton("Next ›")
        self._next_btn.clicked.connect(self.next_page)

        self._smaller_btn = QPushButton("A−")
        self._smaller_btn.setFixedWidth(40)
        self._smaller_btn.clicked.connect(lambda: self.adjust_font(-1))
        self._larger_btn = QPushButton("A+")
        self._larger_btn.setFixedWidth(40)
        self._larger_btn.clicked.connect(lambda: self.adjust_font(+1))

        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bar_layout.addWidget(self._prev_btn)
        bar_layout.addWidget(self._smaller_btn)
        bar_layout.addWidget(self._larger_btn)
        bar_layout.addStretch()
        bar_layout.addWidget(self._label, 1)
        bar_layout.addStretch()
        bar_layout.addWidget(self._next_btn)
        layout.addWidget(bar)
        self._nav_bar = bar

    # ----- loading -----

    def load_book(self, book: EpubBook, start_chapter: int = 0, font_pt: int | None = None) -> None:
        self._book = book
        if font_pt is not None:
            self._font_pt = max(MIN_FONT_PT, min(MAX_FONT_PT, int(font_pt)))
        count = book.chapter_count()
        self._index = max(0, min(start_chapter, count - 1)) if count else 0
        self._render_chapter(self._index)

    def show_chapter(self, index: int) -> None:
        if self._book is None:
            return
        if 0 <= index < self._book.chapter_count():
            self._render_chapter(index)

    # ----- page navigation -----

    def next_page(self) -> None:
        if self._page < self._page_count - 1:
            self._goto_page(self._page + 1)
        elif self._book and self._index < self._book.chapter_count() - 1:
            self._render_chapter(self._index + 1)

    def prev_page(self) -> None:
        if self._page > 0:
            self._goto_page(self._page - 1)
        elif self._index > 0:
            self._render_chapter(self._index - 1, to_last_page=True)

    def next_chapter(self) -> None:
        if self._book and self._index < self._book.chapter_count() - 1:
            self._render_chapter(self._index + 1)

    def prev_chapter(self) -> None:
        if self._index > 0:
            self._render_chapter(self._index - 1)

    def _on_wheel(self, direction: int) -> None:
        self.next_page() if direction > 0 else self.prev_page()

    # ----- font -----

    def adjust_font(self, delta: int) -> None:
        self.set_font_pt(self._font_pt + delta)

    def set_font_pt(self, pt: int) -> None:
        pt = max(MIN_FONT_PT, min(MAX_FONT_PT, int(pt)))
        if pt == self._font_pt:
            return
        self._font_pt = pt
        # Re-flow at the new size, keeping roughly the same spot in the chapter.
        frac = self._page / self._page_count if self._page_count else 0
        self._render_chapter(self._index, keep_index=True)
        self._goto_page(round(frac * self._page_count))

    # ----- rendering / pagination -----

    def _render_chapter(self, index: int, to_last_page: bool = False, keep_index: bool = False) -> None:
        if self._book is None:
            return
        chapter_switched = index != self._index or not keep_index
        self._index = index
        html = self._book.chapter_html(index)
        base = self._book.chapter_base_dir(index)
        self._browser.set_source(self._book, base)
        self._browser.document().setDefaultStyleSheet(self._reader_css())
        self._browser.setHtml(html)
        self._recompute_pages()
        self._page = self._page_count - 1 if to_last_page else 0
        self._goto_page(self._page)
        if chapter_switched and not keep_index:
            self.chapter_changed.emit(self._index)

    def _page_step(self) -> int:
        return max(80, self._browser.viewport().height() - _PAGE_OVERLAP)

    def _recompute_pages(self) -> None:
        vp_w = self._browser.viewport().width()
        vp_h = self._browser.viewport().height()
        if vp_w <= 1 or vp_h <= 1:
            self._page_count = 1
            return
        doc = self._browser.document()
        doc.setTextWidth(vp_w)
        content_h = doc.size().height()
        max_scroll = max(0.0, content_h - vp_h)
        if max_scroll <= 0:
            self._page_count = 1
        else:
            self._page_count = math.ceil(max_scroll / self._page_step()) + 1

    def _goto_page(self, page: int) -> None:
        self._page = max(0, min(page, self._page_count - 1))
        sb = self._browser.verticalScrollBar()
        target = min(self._page * self._page_step(), sb.maximum())
        sb.setValue(int(target))
        self._update_label()

    def _update_label(self) -> None:
        if self._book is None:
            return
        titles = self._book.chapter_titles()
        title = titles[self._index] if self._index < len(titles) else ""
        self._label.setText(
            f"Ch {self._index + 1}/{self._book.chapter_count()} · {title}"
            f"   —   Page {self._page + 1}/{self._page_count}"
        )
        at_start = self._index == 0 and self._page == 0
        at_end = (
            self._index == self._book.chapter_count() - 1
            and self._page == self._page_count - 1
        )
        self._prev_btn.setEnabled(not at_start)
        self._next_btn.setEnabled(not at_end)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._book is not None:
            frac = self._page / self._page_count if self._page_count else 0
            self._recompute_pages()
            self._goto_page(round(frac * self._page_count))

    def _reader_css(self) -> str:
        pt = self._font_pt
        return (
            f"body {{ color: {_PAGE_FG}; font-size: {pt}pt;"
            f" font-family: 'Libre Baskerville', Georgia, serif; }}"
            f"p {{ margin-top: 0.5em; margin-bottom: 0.5em; }}"
            f"h1, h2, h3, h4 {{ font-weight: bold; margin: 0.8em 0 0.4em 0; }}"
            f"a {{ color: {_PAGE_FG}; }}"
        )

    # ----- accessors -----

    def current_chapter(self) -> int:
        return self._index

    def font_pt(self) -> int:
        return self._font_pt

    def chapter_titles(self) -> list[str]:
        return self._book.chapter_titles() if self._book else []

    def apply_theme(self, c: dict) -> None:
        # The reading page stays "paper"; only the nav bar follows the app theme.
        self._nav_bar.setStyleSheet(
            f"#EbookNavBar {{ background: {c['header_bg']};"
            f" border-top: 1px solid {c['border']}; }}"
            f"#EbookNavBar QLabel {{ background: transparent; color: {c['text']}; }}"
        )
