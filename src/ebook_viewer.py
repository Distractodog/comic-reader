"""Text/novel EPUB reader view — renders chapters with Qt's built-in QTextBrowser.

One spine document (chapter) is shown at a time on a paper-coloured page, with
prev/next navigation, adjustable font size, and a chapter jump list. Embedded
images are resolved straight out of the EPUB via loadResource. Rendering fidelity
is "good novel", not pixel-perfect CSS — by design (no web engine dependency).
"""

from __future__ import annotations

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

MIN_FONT_PT = 11
MAX_FONT_PT = 32
DEFAULT_FONT_PT = 19


class _BookTextBrowser(QTextBrowser):
    """QTextBrowser that pulls images/resources from the open EPUB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book: EpubBook | None = None
        self._base = ""
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)

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
                        return img
        return super().loadResource(type_, url)


class EbookViewer(QWidget):
    chapter_changed = pyqtSignal(int)  # current chapter index

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book: EpubBook | None = None
        self._index = 0
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
        layout.addWidget(self._browser, 1)

        # Bottom navigation bar
        bar = QWidget(self)
        bar.setObjectName("EbookNavBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(8)

        self._prev_btn = QPushButton("‹ Prev")
        self._prev_btn.clicked.connect(self.prev_chapter)
        self._next_btn = QPushButton("Next ›")
        self._next_btn.clicked.connect(self.next_chapter)

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
        self._render()

    def show_chapter(self, index: int) -> None:
        if self._book is None:
            return
        count = self._book.chapter_count()
        if not (0 <= index < count):
            return
        self._index = index
        self._render()

    def next_chapter(self) -> None:
        if self._book and self._index < self._book.chapter_count() - 1:
            self.show_chapter(self._index + 1)

    def prev_chapter(self) -> None:
        if self._book and self._index > 0:
            self.show_chapter(self._index - 1)

    def adjust_font(self, delta: int) -> None:
        self.set_font_pt(self._font_pt + delta)

    def set_font_pt(self, pt: int) -> None:
        pt = max(MIN_FONT_PT, min(MAX_FONT_PT, int(pt)))
        if pt == self._font_pt:
            return
        self._font_pt = pt
        self._render(keep_scroll=True)

    # ----- rendering -----

    def _render(self, keep_scroll: bool = False) -> None:
        if self._book is None:
            return
        scroll_val = self._browser.verticalScrollBar().value() if keep_scroll else 0

        html = self._book.chapter_html(self._index)
        base = self._book.chapter_base_dir(self._index)
        self._browser.set_source(self._book, base)
        self._browser.document().setDefaultStyleSheet(self._reader_css())
        self._browser.setHtml(html)
        self._browser.verticalScrollBar().setValue(scroll_val)

        count = self._book.chapter_count()
        titles = self._book.chapter_titles()
        title = titles[self._index] if self._index < len(titles) else ""
        self._label.setText(f"{self._index + 1} / {count}   ·   {title}")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < count - 1)
        self.chapter_changed.emit(self._index)

    def _reader_css(self) -> str:
        pt = self._font_pt
        return (
            f"body {{ color: {_PAGE_FG}; font-size: {pt}pt;"
            f" font-family: 'Libre Baskerville', Georgia, serif; }}"
            f"p {{ margin-top: 0.5em; margin-bottom: 0.5em; }}"
            f"h1, h2, h3, h4 {{ font-weight: bold; margin: 0.8em 0 0.4em 0; }}"
            f"img {{ max-width: 100%; }}"
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
