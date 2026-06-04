"""Text/novel EPUB reader view — true page-box rendering.

Each chapter is laid out by Qt's own document-pagination engine (the same
machinery `QTextDocument` uses for printing). The document is given a fixed
page size and split into discrete pages; we then paint one page at a time onto
a plain widget. This is how real e-readers (Kindle, Apple Books) work: pages
are independent renders, not slices of a scrolled document. As a result a text
line is never split across a boundary, never clipped, and never duplicated —
the last line of a page flows straight into the first line of the next.

No web-engine dependency: this uses only PyQt6's built-in text layout.
"""

from __future__ import annotations

from posixpath import normpath

from PyQt6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSizeF,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from epub_book import EpubBook

# Reading page colours (a calm "paper" look, independent of the dark chrome, so
# the book's own black text stays readable regardless of app theme).
_PAGE_BG = "#f4ecd8"
_PAGE_FG = "#2a2420"

# Uniform whitespace inside every page, on all four sides.
_PAGE_MARGIN = 34

MIN_FONT_PT = 11
MAX_FONT_PT = 32
DEFAULT_FONT_PT = 19


class _BookDocument(QTextDocument):
    """QTextDocument that resolves <img> resources from inside the EPUB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book: EpubBook | None = None
        self._base = ""

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
                        avail = max(1, int(self.pageSize().width()) - 8)
                        if img.width() > avail:
                            img = img.scaledToWidth(
                                avail, Qt.TransformationMode.SmoothTransformation
                            )
                        return img
        return super().loadResource(type_, url)


class _PageCanvas(QWidget):
    """Paints a single page of the paginated document, with margins."""

    mouse_moved = pyqtSignal(int)      # viewport y — for fullscreen bar reveal
    page_swiped = pyqtSignal(int)      # +1 = next page, -1 = previous (sideswipe)
    resized = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = _BookDocument(self)
        self._doc.setDocumentMargin(0)  # we supply the margin ourselves
        self._doc.setUseDesignMetrics(True)
        self._page = 0
        self._n_pages = 1
        self._swipe_x = 0
        self._swipe_y = 0
        self._swipe_fired = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        # Page-turn slide animation (mirrors the comic viewer): two overlay
        # labels hold a snapshot of the outgoing and incoming page and slide
        # across in parallel, so a page turn glides instead of snapping.
        self._overlay = QLabel(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._overlay.hide()
        self._overlay_new = QLabel(self)
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

    def book_document(self) -> _BookDocument:
        return self._doc

    def _content_size(self) -> tuple[int, int]:
        return (
            max(1, self.width() - 2 * _PAGE_MARGIN),
            max(1, self.height() - 2 * _PAGE_MARGIN),
        )

    def relayout(self) -> None:
        """Re-fit the document to the current page (content) size and recount.

        Note: only setPageSize is used. Calling setTextWidth as well would reset
        the page height to -1 and silently disable pagination (collapsing every
        chapter to a single page). setPageSize already fixes the wrap width.

        Pagination rounding and trailing margins/empty blocks can spawn a final
        page that renders blank. We trim those by actually rendering each
        trailing page and dropping it if it has no ink — definitive, and it
        never mistakes an image-only page for blank.
        """
        cw, ch = self._content_size()
        self._doc.setPageSize(QSizeF(cw, ch))
        n = max(1, self._doc.documentLayout().pageCount())
        while n > 1 and not self._page_has_ink(n - 1):
            n -= 1
        self._n_pages = n

    def page_count(self) -> int:
        return self._n_pages

    def _render_page(self, page: int, painter: QPainter) -> None:
        """Draw `page`'s slice of the document at the painter's origin."""
        cw, ch = self._content_size()
        painter.translate(0, -page * ch)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.clip = QRectF(0, page * ch, cw, ch)
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(_PAGE_FG))
        self._doc.documentLayout().draw(painter, ctx)

    def _page_has_ink(self, page: int) -> bool:
        cw, ch = self._content_size()
        if cw <= 1 or ch <= 1:
            return True
        img = QImage(cw, ch, QImage.Format.Format_RGB32)
        img.fill(QColor(_PAGE_BG))
        p = QPainter(img)
        self._render_page(page, p)
        p.end()
        bg = QColor(_PAGE_BG).rgb()
        for y in range(0, ch, 5):
            for x in range(0, cw, 5):
                if img.pixel(x, y) != bg:
                    return True
        return False

    def set_page(self, i: int) -> None:
        self._page = max(0, min(i, self.page_count() - 1))
        self.update()

    def page_index(self) -> int:
        return self._page

    def transition(self, direction: int, apply_change) -> None:
        """Apply a page/chapter change with a sliding animation.

        `apply_change` is a callable that performs the actual change (advance a
        page, or rebuild a new chapter). We snapshot the page before and after
        and slide them across. direction: +1 = forward, -1 = back.
        """
        if direction == 0 or self.width() <= 1 or self.height() <= 1:
            apply_change()
            return
        self._anim_group.stop()
        self._overlay.hide()
        self._overlay_new.hide()
        old = self.grab()
        apply_change()
        new = self.grab()  # forces a synchronous repaint of the new page
        self._run_slide(old, new, direction)

    def _run_slide(self, old_pixmap: QPixmap, new_pixmap: QPixmap, direction: int):
        w, h = self.width(), self.height()
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

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_PAGE_BG))
        cw, ch = self._content_size()
        # Inset by the margin, clip to the content box, then draw the current
        # page's slice of the document at the top of that box.
        p.translate(_PAGE_MARGIN, _PAGE_MARGIN)
        p.setClipRect(QRectF(0, 0, cw, ch))
        self._render_page(self._page, p)
        p.end()

    def mouseMoveEvent(self, event):
        self.mouse_moved.emit(int(event.position().y()))
        super().mouseMoveEvent(event)

    def wheelEvent(self, event):
        # Same trackpad sideswipe handling as the comic viewer: accumulate the
        # horizontal gesture, fire exactly one page once it crosses the
        # threshold, and ignore inertia/momentum so a flick turns a single page.
        # Vertical scrolling does nothing (the book is paged, not scrolled).
        phase = event.phase()
        px = event.pixelDelta().x()
        py = event.pixelDelta().y()

        if phase == Qt.ScrollPhase.ScrollBegin:
            self._swipe_x = 0
            self._swipe_y = 0
            self._swipe_fired = False

        if phase == Qt.ScrollPhase.ScrollMomentum or self._swipe_fired:
            event.accept()
            return

        self._swipe_x += px
        self._swipe_y += py

        if abs(self._swipe_x) >= 60 and abs(self._swipe_x) > abs(self._swipe_y):
            self._swipe_fired = True
            self.page_swiped.emit(1 if self._swipe_x < 0 else -1)  # left = next
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()


class EbookViewer(QWidget):
    chapter_changed = pyqtSignal(int)  # current chapter index
    mouse_moved = pyqtSignal(int)      # viewport y — for fullscreen bar reveal
    exit_requested = pyqtSignal()      # user asked to leave the book

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

        self._canvas = _PageCanvas(self)
        self._canvas.mouse_moved.connect(self.mouse_moved.emit)
        self._canvas.page_swiped.connect(self._on_swipe)
        self._canvas.resized.connect(self._reflow)
        layout.addWidget(self._canvas, 1)

        # Bottom navigation bar
        bar = QWidget(self)
        bar.setObjectName("EbookNavBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(8)

        self._exit_btn = QPushButton("⌂ Library")
        self._exit_btn.clicked.connect(self.exit_requested.emit)
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

        bar_layout.addWidget(self._exit_btn)
        bar_layout.addSpacing(8)
        bar_layout.addWidget(self._prev_btn)
        bar_layout.addWidget(self._smaller_btn)
        bar_layout.addWidget(self._larger_btn)
        bar_layout.addStretch()
        bar_layout.addWidget(self._label, 1)
        bar_layout.addStretch()
        bar_layout.addWidget(self._next_btn)
        layout.addWidget(bar)
        self._nav_bar = bar

    # ----- sideswipe routing -----

    def _on_swipe(self, direction: int) -> None:
        if direction > 0:
            self.next_page()
        else:
            self.prev_page()

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
            self._canvas.transition(+1, lambda: self._step_page(self._page + 1))
        elif self._book and self._index < self._book.chapter_count() - 1:
            self._canvas.transition(+1, lambda: self._render_chapter(self._index + 1))

    def prev_page(self) -> None:
        if self._page > 0:
            self._canvas.transition(-1, lambda: self._step_page(self._page - 1))
        elif self._index > 0:
            self._canvas.transition(
                -1, lambda: self._render_chapter(self._index - 1, to_last_page=True)
            )

    def _step_page(self, page: int) -> None:
        self._page = page
        self._canvas.set_page(self._page)
        self._update_label()

    def next_chapter(self) -> None:
        if self._book and self._index < self._book.chapter_count() - 1:
            self._render_chapter(self._index + 1)

    def prev_chapter(self) -> None:
        if self._index > 0:
            self._render_chapter(self._index - 1)

    # ----- font -----

    def adjust_font(self, delta: int) -> None:
        self.set_font_pt(self._font_pt + delta)

    def set_font_pt(self, pt: int) -> None:
        pt = max(MIN_FONT_PT, min(MAX_FONT_PT, int(pt)))
        if pt == self._font_pt:
            return
        frac = self._page / self._page_count if self._page_count else 0
        self._font_pt = pt
        self._render_chapter(self._index, keep_index=True)
        self._goto_page(round(frac * self._page_count))

    # ----- rendering / pagination -----

    def _render_chapter(self, index: int, to_last_page: bool = False, keep_index: bool = False) -> None:
        if self._book is None:
            return
        emit = (index != self._index) or (not keep_index)
        self._index = index
        html = self._book.chapter_html(index)
        base = self._book.chapter_base_dir(index)
        doc = self._canvas.book_document()
        doc.set_source(self._book, base)
        doc.setDefaultStyleSheet(self._reader_css())
        doc.setDefaultFont(self._reader_font())
        doc.setHtml(html)
        self._canvas.relayout()
        self._page_count = self._canvas.page_count()
        self._goto_page(self._page_count - 1 if to_last_page else 0)
        if emit:
            self.chapter_changed.emit(self._index)

    def _goto_page(self, page: int) -> None:
        self._canvas.set_page(page)
        self._page = self._canvas.page_index()
        self._update_label()

    def _reflow(self) -> None:
        """Re-paginate for the current canvas size, keeping the reading spot."""
        if self._book is None:
            return
        frac = self._page / self._page_count if self._page_count else 0
        self._canvas.relayout()
        self._page_count = self._canvas.page_count()
        self._goto_page(round(frac * self._page_count))

    def showEvent(self, event):
        # The stack may switch to this view without a resize, so the first real
        # pagination has to happen here, once the canvas has its true size.
        super().showEvent(event)
        self._reflow()

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

    def _reader_font(self) -> QFont:
        font = QFont()
        font.setFamilies(["Libre Baskerville", "Georgia", "serif"])
        font.setPointSize(self._font_pt)
        return font

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
