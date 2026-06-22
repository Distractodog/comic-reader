"""Main application window with menus, navigation, and the page viewer."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app_info import APP_DISPLAY_NAME, app_settings

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSettings,
    QThread,
    QTimer,
    QVariantAnimation,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRegion,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# Minimum time the loading screen stays up. Fast opens are held to this so the
# screen reads as a deliberate transition (and the reader finishes preparing
# underneath) instead of an unrecognizable flicker.
_MIN_LOADING_SCREEN_S = 0.7


def _hex_to_rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


class _LoadingOverlay(QWidget):
    """Instant 'opening…' screen: the comic's faded cover plus a spinner.

    Shown over the view stack the moment a comic is clicked, so the app feels
    responsive while the archive is opened on a background thread.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.hide()
        self._source: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._angle = 0.0  # spinner arc rotation
        self._spin = QTimer(self)
        self._spin.setInterval(16)
        self._spin.timeout.connect(self._tick)
        # Fade-out used when the real page is ready underneath.
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._after_fade)
        # Track the stack's size so the overlay always fills it.
        parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            self._rescale()
        return super().eventFilter(obj, event)

    def _tick(self):
        self._angle = (self._angle + 6.5) % 360  # one full turn ≈ 0.9s
        self.update()

    def _rescale(self):
        if self._source is None or self.width() <= 0 or self.height() <= 0:
            self._scaled = None
            return
        self._scaled = self._source.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def start(self, cover_path: str | None):
        self._fade.stop()
        self._fx.setOpacity(1.0)
        self._source = None
        if cover_path and Path(cover_path).exists():
            img = QImage(cover_path)
            if not img.isNull():
                # Greyscale so the placeholder reads as "not loaded yet".
                img = img.convertToFormat(QImage.Format.Format_Grayscale8)
                self._source = QPixmap.fromImage(img)
        self.setGeometry(self.parentWidget().rect())
        self._rescale()
        self.raise_()
        self.show()
        # Paint NOW — queued events (e.g. a fast open finishing) would otherwise
        # be processed before the paint event and the user would never see this.
        self.repaint()
        self._spin.start()

    def finish(self):
        """Fade out to reveal the loaded page underneath."""
        if self.isVisible():
            self._fade.start()

    def stop(self):
        """Hide immediately (open failed or was superseded)."""
        self._fade.stop()
        self._spin.stop()
        self.hide()

    def _after_fade(self):
        self._spin.stop()
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))  # match the reader background
        if self._scaled is not None:
            p.setOpacity(0.14)  # heavily greyed out
            x = (self.width() - self._scaled.width()) // 2
            y = (self.height() - self._scaled.height()) // 2
            p.drawPixmap(x, y, self._scaled)
            p.setOpacity(1.0)
        # Single-line spinner: one open arc, smoothly rotating.
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(235, 230, 230, 235), 3.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        r = 24
        cx, cy = self.width() // 2, self.height() // 2
        # 100°-long arc; start angle advances each tick (Qt angles are 1/16°).
        p.drawArc(cx - r, cy - r, 2 * r, 2 * r, int(-self._angle * 16), 100 * 16)


class _ComicOpenWorker(QThread):
    """Opens a comic archive AND decodes the starting page(s) off the GUI thread.

    Decoding is the slow part for big pages, so doing it here keeps the loading
    screen responsive. Decoded QImages go into the page cache on arrival.
    """

    # generation, reader (or None), {page_index: QImage}, error text
    done = pyqtSignal(int, object, object, str)

    def __init__(self, gen: int, path: str, start_page: int, spread: bool,
                 webtoon: bool, parent=None):
        super().__init__(parent)
        self._gen = gen
        self._path = path
        self._start_page = start_page
        self._spread = spread
        self._webtoon = webtoon

    def run(self):
        try:
            reader = open_comic(self._path)
        except Exception as e:
            self.done.emit(self._gen, None, {}, str(e))
            return
        images: dict[int, QImage] = {}
        # The webtoon viewer decodes its own pages on a background thread already.
        if not self._webtoon:
            try:
                count = reader.page_count()
                # Mirror _finish_open_comic's resume logic so the indices match.
                start = self._start_page if 0 < self._start_page < count else 0
                if self._spread:
                    start = (start // 2) * 2
                pages = (start, start + 1) if self._spread else (start,)
                for i in pages:
                    if 0 <= i < count:
                        img = QImage()
                        img.loadFromData(reader.get_page_bytes(i))
                        if not img.isNull():
                            images[i] = img
            except Exception:
                pass  # cache misses fall back to the normal decode path
        self.done.emit(self._gen, reader, images, "")


class _ReaderBar(QWidget):
    """Top bar shown while reading — back, title, reader tools, and ⋮ menu."""
    back_clicked = pyqtSignal()
    menu_requested = pyqtSignal()
    bookmark_requested = pyqtSignal()
    fit_requested = pyqtSignal()
    page_mode_requested = pyqtSignal()
    manga_requested = pyqtSignal()
    fullscreen_requested = pyqtSignal()
    HEIGHT = 63

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ReaderBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(0)
        self.setMaximumHeight(self.HEIGHT)
        self.setStyleSheet(
            "#ReaderBar { background: #ecdede; border-bottom: 2px solid #c4aeae; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(14)

        self._back_btn = QPushButton("←")
        self._back_btn.setFlat(True)
        self._back_btn.setFixedSize(41, 41)
        self._back_btn.setStyleSheet("color: #8b2a2a; border: none; font-size: 24px; padding: 0;")
        self._back_btn.clicked.connect(self.back_clicked)
        layout.addWidget(self._back_btn)

        self._title = QLabel()
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(25)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)
        self._title.setStyleSheet("background: transparent; color: #2a1818;")
        layout.addWidget(self._title)
        layout.addStretch()

        def _tool_btn(glyph: str, tooltip: str) -> QPushButton:
            btn = QPushButton(glyph)
            btn.setFlat(True)
            btn.setFixedSize(47, 47)
            btn.setToolTip("")
            btn.setStyleSheet(
                "color: #8b2a2a; border: none; font-size: 26px; padding: 0;"
            )
            layout.addWidget(btn)
            return btn

        # Reader tools, in order: bookmarks, fit, page layout, manga direction.
        self._bookmark_btn = _tool_btn("⚑", "Bookmarks")
        self._bookmark_btn.clicked.connect(self.bookmark_requested)
        self._fit_btn = _tool_btn("⤢", "Fit image")
        self._fit_btn.clicked.connect(self.fit_requested)
        self._page_mode_btn = _tool_btn("▤", "Page layout")
        self._page_mode_btn.clicked.connect(self.page_mode_requested)
        self._manga_btn = _tool_btn("漫", "Reading direction (manga)")
        self._manga_btn.clicked.connect(self.manga_requested)

        # Fullscreen lives outside the comic-only tools so it also shows for
        # text EPUBs; it stays visible regardless of comic type.
        self._fullscreen_btn = _tool_btn("⛶", "Fullscreen")
        self._fullscreen_btn.clicked.connect(self.fullscreen_requested)

        self._menu_btn = _tool_btn("⋮", "More")
        self._menu_btn.clicked.connect(self.menu_requested)

        self._tool_buttons = [
            self._bookmark_btn, self._fit_btn, self._page_mode_btn,
            self._manga_btn, self._fullscreen_btn, self._menu_btn,
        ]

        self.hide()

    def set_title(self, title: str):
        self._title.setText(title)

    def set_comic_tools_visible(self, visible: bool) -> None:
        """Bookmarks/fit/page/manga only apply to image comics, not text EPUBs."""
        for btn in (self._bookmark_btn, self._fit_btn,
                    self._page_mode_btn, self._manga_btn):
            btn.setVisible(visible)

    @staticmethod
    def _btn_global_pos(btn: QPushButton) -> QPoint:
        return btn.mapToGlobal(QPoint(0, btn.height()))

    def menu_btn_global_pos(self) -> QPoint:
        return self._btn_global_pos(self._menu_btn)

    def bookmark_btn_global_pos(self) -> QPoint:
        return self._btn_global_pos(self._bookmark_btn)

    def fit_btn_global_pos(self) -> QPoint:
        return self._btn_global_pos(self._fit_btn)

    def page_mode_btn_global_pos(self) -> QPoint:
        return self._btn_global_pos(self._page_mode_btn)

    def manga_btn_global_pos(self) -> QPoint:
        return self._btn_global_pos(self._manga_btn)

    def apply_theme(self, c: dict):
        bg = _hex_to_rgba(c["reader_bar_bg"], 218)
        border = _hex_to_rgba(c["border"], 190)
        self.setStyleSheet(
            f"#ReaderBar {{ background: {bg}; border-bottom: 2px solid {border}; }}"
        )
        self._back_btn.setStyleSheet(
            f"background: transparent; color: {c['text']}; border: none; font-size: 24px; padding: 0;"
        )
        btn_style = (
            f"background: transparent; color: {c['text']}; border: none;"
            f" font-size: 26px; padding: 0;"
        )
        for btn in self._tool_buttons:
            btn.setStyleSheet(btn_style)
        self._title.setStyleSheet(f"background: transparent; color: {c['text']};")

class _AnnotationDialog(QDialog):
    """Small themed editor for one page note."""

    def __init__(self, body: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page Note")
        self._delete_requested = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._editor = QPlainTextEdit()
        self._editor.setPlainText(body)
        self._editor.setMinimumSize(420, 180)
        layout.addWidget(self._editor)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        if body:
            delete_btn = buttons.addButton(
                "Delete", QDialogButtonBox.ButtonRole.DestructiveRole
            )
            delete_btn.clicked.connect(self._delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _delete(self) -> None:
        self._delete_requested = True
        self.accept()

    def body(self) -> str:
        return self._editor.toPlainText().strip()

    def delete_requested(self) -> bool:
        return self._delete_requested

    def apply_theme(self, c: dict) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {c['app_bg']}; color: {c['text']}; }}"
            f"QPlainTextEdit {{ background: {c['input_bg']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 4px;"
            f" padding: 8px; selection-background-color: {c['selection_bg']}; }}"
            f"QPushButton {{ background: {c['sidebar_bg']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 4px;"
            f" padding: 6px 14px; }}"
            f"QPushButton:hover {{ background: {c['hover_bg']}; }}"
        )


class _HideSidebarDialog(QDialog):
    """Pick which library views and shelves to hide from the sidebar."""

    def __init__(
        self,
        library_items: list[tuple[int, str]],
        shelves: list[Shelf],
        hidden_ids: set[int],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Hide Sidebar Items")
        self.resize(320, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        hint = QLabel("Checked items are hidden from the sidebar.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(4)

        self._checks: list[tuple[int, QCheckBox]] = []

        lib_lbl = QLabel("LIBRARY")
        lib_lbl.setStyleSheet(
            "color: rgba(255,255,255,0.45); font-size: 10px; letter-spacing: 1px;"
        )
        inner_layout.addWidget(lib_lbl)
        for nav_id, label in library_items:
            cb = QCheckBox(label)
            cb.setChecked(nav_id in hidden_ids)
            inner_layout.addWidget(cb)
            self._checks.append((nav_id, cb))

        if shelves:
            shelf_lbl = QLabel("SHELVES")
            shelf_lbl.setStyleSheet(
                "color: rgba(255,255,255,0.45); font-size: 10px;"
                " letter-spacing: 1px; padding-top: 8px;"
            )
            inner_layout.addWidget(shelf_lbl)
            for shelf in shelves:
                cb = QCheckBox(shelf.name)
                cb.setChecked(shelf.id in hidden_ids)
                inner_layout.addWidget(cb)
                self._checks.append((shelf.id, cb))

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def hidden_ids(self) -> set[int]:
        return {nav_id for nav_id, cb in self._checks if cb.isChecked()}

    def apply_theme(self, c: dict) -> None:
        self.setStyleSheet(
            f"QDialog {{ background: {c['app_bg']}; color: {c['text']}; }}"
            f"QCheckBox {{ color: {c['text']}; spacing: 6px; }}"
            f"QPushButton {{ background: {c['sidebar_bg']}; color: {c['text']};"
            f" border: 1px solid {c['border']}; border-radius: 4px; padding: 6px 14px; }}"
            f"QPushButton:hover {{ background: {c['hover_bg']}; }}"
        )


from archive_handler import ComicReader, open_comic
import bookshelf as bookshelf_mod
import prefs
from bookshelf import BookshelfView
from dedupe_scanner import DuplicateScanner
from duplicates_dialog import DuplicatesDialog
from ebook_viewer import EbookViewer
from epub_book import EpubBook, is_text_epub
from keybindings import ACTIONS, KeybindingDialog, KeybindingManager
from library import Library, ReadingSettings, Shelf
from library_scanner import SCANNABLE_EXTENSIONS, LibraryScanner
from preloader import PageCache, PagePreloader
from settings_view import SettingsView
from stats_dialog import StatsDialog
from viewer import (
    ComicThumbButton,
    ComicViewer,
    FitMode,
    ReaderFooter,
    ReadingMode,
    ThumbnailStrip,
    make_spread_pixmap,
    make_spread_pixmap_from_images,
)
from webtoon_viewer import WebtoonViewer
import themes


class _RailButton(QPushButton):
    """Sidebar button that paints its icon centered by ink, not glyph advance.

    Centering with text-align/padding depends on each glyph's side-bearing, which
    differs per glyph and per platform (the 📖 emoji especially). Painting the
    icon ourselves at its tight-bounding-box center makes every icon line up
    exactly, on macOS and Windows alike. The rounded background/hover/active still
    come from the stylesheet (drawn by super().paintEvent).
    """

    ICON_SIDE = 18.0

    def __init__(self, icon_char: str, label_text: str, kind: str = "glyph", parent=None):
        super().__init__(parent)
        self._icon = icon_char
        self._label = label_text
        self._kind = kind  # "glyph" or a vector-painted rail icon
        self._expanded = False
        self._fg = QColor("#2a1818")
        self._icon_size = 21  # label fallback size; vectors use ICON_SIDE
        self.setFlat(True)
        self.setText("")  # we draw the glyph ourselves

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.update()

    def set_fg(self, color: str) -> None:
        self._fg = QColor(color)
        self.update()

    def set_icon_size(self, px: int) -> None:
        """Pin the drawn glyph size so icons don't shrink when labels appear."""
        self._icon_size = px
        self.update()

    def _icon_px(self) -> int:
        return self._icon_size if self._icon_size > 0 else 18

    def paintEvent(self, event):
        super().paintEvent(event)  # stylesheet background / hover / active
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self._fg)
        h = self.height()

        # Icon is always drawn at its pinned size; the label (expanded only) uses
        # the smaller widget font so the glyph never shrinks to make text fit.
        icon_font = QFont(self.font())
        icon_font.setPixelSize(self._icon_px())

        if self._expanded and self._label:
            # Icon stays centered on the collapsed rail center (x=30) so it never
            # moves when the panel opens; the label is drawn to its right.
            icon_cx = _Sidebar.COLLAPSED_W / 2
            if self._draw_vector_icon(painter, icon_cx, h / 2):
                pass
            else:
                painter.setFont(icon_font)
                ifm = QFontMetrics(icon_font)
                tight = ifm.tightBoundingRect(self._icon)
                ix = icon_cx - tight.left() - tight.width() / 2
                ibaseline = h / 2 - (tight.top() + tight.height() / 2)
                painter.drawText(int(round(ix)), int(round(ibaseline)), self._icon)
            painter.setFont(self.font())
            lfm = QFontMetrics(self.font())
            lbaseline = h / 2 + (lfm.ascent() - lfm.descent()) / 2
            painter.setPen(self._fg)
            painter.drawText(int(_Sidebar.COLLAPSED_W) + 4, int(round(lbaseline)), self._label)
        else:
            if self._draw_vector_icon(painter, self.width() / 2, h / 2):
                pass
            else:
                painter.setFont(icon_font)
                ifm = QFontMetrics(icon_font)
                tight = ifm.tightBoundingRect(self._icon)
                x = self.width() / 2 - tight.left() - tight.width() / 2
                baseline = h / 2 - (tight.top() + tight.height() / 2)
                painter.drawText(int(round(x)), int(round(baseline)), self._icon)
        painter.end()

    def _icon_rect(self, cx: float, cy: float) -> QRectF:
        side = 16.0 if self._kind == "hamburger" else self.ICON_SIDE
        return QRectF(cx - side / 2, cy - side / 2, side, side)

    def _draw_vector_icon(self, painter: QPainter, cx: float, cy: float) -> bool:
        drawers = {
            "hamburger": self._draw_hamburger,
            "home": self._draw_home,
            "book": self._draw_book,
            "bookmark": self._draw_bookmark,
            "search": self._draw_search,
            "menu": self._draw_menu,
            "settings": self._draw_settings,
            "fullscreen": self._draw_fullscreen,
        }
        drawer = drawers.get(self._kind)
        if drawer is None:
            return False
        drawer(painter, cx, cy)
        return True

    def _icon_pen(self, width: float = 1.3) -> QPen:
        # Libre Baskerville vibe: thin engraved stroke, sharp mitred corners and
        # flat terminals (we add our own serifs) rather than a modern monoline.
        pen = QPen(self._fg)
        pen.setWidthF(width)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        return pen

    def _serif_v(self, painter: QPainter, x: float, y: float, t: float = 1.8) -> None:
        """Horizontal serif foot centred on the end of a vertical stroke."""
        painter.drawLine(QPointF(x - t, y), QPointF(x + t, y))

    def _draw_book(self, painter: QPainter, cx: float, cy: float) -> None:
        r = self._icon_rect(cx, cy)
        # Open book: each page is a parallelogram whose top AND bottom edges slope
        # gently upward toward the outer corners (parallel, same direction).
        rise = 1.6
        top_c = r.top() + 2.5
        bot_c = r.bottom() - 2.5
        top_o = top_c - rise
        bot_o = bot_c - rise
        lx = r.left() + 1
        rx = r.right() - 1
        path = QPainterPath()
        path.moveTo(cx, top_c)
        path.lineTo(lx, top_o)
        path.lineTo(lx, bot_o)
        path.lineTo(cx, bot_c)
        path.moveTo(cx, top_c)
        path.lineTo(rx, top_o)
        path.lineTo(rx, bot_o)
        path.lineTo(cx, bot_c)
        path.moveTo(cx, top_c)
        path.lineTo(cx, bot_c)
        painter.setPen(self._icon_pen(1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_bookmark(self, painter: QPainter, cx: float, cy: float) -> None:
        r = self._icon_rect(cx, cy)
        path = QPainterPath()
        path.moveTo(r.left() + 4, r.top() + 1)
        path.lineTo(r.right() - 4, r.top() + 1)
        path.lineTo(r.right() - 4, r.bottom() - 1)
        path.lineTo(cx, r.bottom() - 5)
        path.lineTo(r.left() + 4, r.bottom() - 1)
        path.closeSubpath()
        painter.setPen(self._icon_pen(1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_hamburger(self, painter: QPainter, cx: float, cy: float) -> None:
        # Three thin lines; the top and bottom align with the title's cap-top and
        # baseline beside them.
        half_w = 8.0
        half_h = bookshelf_mod.header_title_cap_band() / 2
        painter.setPen(self._icon_pen(1.3))
        for y in (cy - half_h, cy, cy + half_h):
            painter.drawLine(QPointF(cx - half_w, y), QPointF(cx + half_w, y))

    def _draw_menu(self, painter: QPainter, cx: float, cy: float) -> None:
        self._draw_hamburger(painter, cx, cy)

    def _draw_home(self, painter: QPainter, cx: float, cy: float) -> None:
        r = self._icon_rect(cx, cy)
        path = QPainterPath()
        path.moveTo(r.left() + 1, cy - 1)
        path.lineTo(cx, r.top() + 1)
        path.lineTo(r.right() - 1, cy - 1)
        path.moveTo(r.left() + 4, cy - 1)
        path.lineTo(r.left() + 4, r.bottom() - 1)
        path.lineTo(r.right() - 4, r.bottom() - 1)
        path.lineTo(r.right() - 4, cy - 1)
        painter.setPen(self._icon_pen(1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        # serif feet under the walls
        self._serif_v(painter, r.left() + 4, r.bottom() - 1, 1.6)
        self._serif_v(painter, r.right() - 4, r.bottom() - 1, 1.6)

    def _draw_search(self, painter: QPainter, cx: float, cy: float) -> None:
        r = self._icon_rect(cx, cy)
        painter.setPen(self._icon_pen(1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(r.left() + 1, r.top() + 1, 12, 12))
        # handle with a small serif foot at its end
        end = QPointF(r.right() - 1, r.bottom() - 1)
        painter.drawLine(QPointF(cx + 3, cy + 3), end)
        painter.drawLine(
            QPointF(end.x() - 1.4, end.y() + 1.4),
            QPointF(end.x() + 1.4, end.y() - 1.4),
        )

    def _draw_fullscreen(self, painter: QPainter, cx: float, cy: float) -> None:
        # Four corner brackets — the standard "expand to fullscreen" mark.
        r = self._icon_rect(cx, cy)
        painter.setPen(self._icon_pen(1.3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arm = 5.0
        l, t, rt, b = r.left() + 1, r.top() + 1, r.right() - 1, r.bottom() - 1
        for (px, py, dx, dy) in (
            (l, t, 1, 1), (rt, t, -1, 1), (l, b, 1, -1), (rt, b, -1, -1),
        ):
            painter.drawLine(QPointF(px, py), QPointF(px + dx * arm, py))
            painter.drawLine(QPointF(px, py), QPointF(px, py + dy * arm))

    def _draw_settings(self, painter: QPainter, cx: float, cy: float) -> None:
        r = self._icon_rect(cx, cy)
        painter.setPen(self._icon_pen(1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(r.left() + 1, r.top() + 1, 16, 16))
        painter.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))
        for x1, y1, x2, y2 in (
            (cx, r.top() + 1, cx, r.top() + 4),
            (cx, r.bottom() - 4, cx, r.bottom() - 1),
            (r.left() + 1, cy, r.left() + 4, cy),
            (r.right() - 4, cy, r.right() - 1, cy),
            (r.left() + 3, r.top() + 3, r.left() + 5, r.top() + 5),
            (r.right() - 5, r.top() + 5, r.right() - 3, r.top() + 3),
            (r.left() + 3, r.bottom() - 3, r.left() + 5, r.bottom() - 5),
            (r.right() - 5, r.bottom() - 5, r.right() - 3, r.bottom() - 3),
        ):
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))


class _Sidebar(QWidget):
    """Slim icon rail split into two independent sections.

    A fixed top box (hamburger + Library) is part of the top-bar zone and never
    widens or shows labels. Below it, an expanding panel (Currently Reading,
    Search, Settings) is the only part that opens out — it overlays the comics
    with labels when the hamburger is clicked. The widget is masked to those two
    boxes so the empty area beside the top box stays click-through.
    """

    home_clicked = pyqtSignal()
    currently_reading_clicked = pyqtSignal()
    bookmarks_clicked = pyqtSignal()
    search_changed = pyqtSignal(str)
    search_closed = pyqtSignal()
    app_menu_requested = pyqtSignal()
    settings_clicked = pyqtSignal()
    fullscreen_clicked = pyqtSignal()
    expanded_changed = pyqtSignal()  # emitted when the rail width changes (overlay re-layout)

    COLLAPSED_W = 60
    CELL = 60
    EXPANDED_W = 220  # widened ~two spaces so labels like "Currently Reading" aren't tight

    def __init__(self, *, show_app_menu: bool = False, parent=None):
        super().__init__(parent)
        self._theme: dict = themes.DARK
        self._expanded = False
        self._active = "library"
        self._search_active = False
        self._panel_w = self.COLLAPSED_W  # current (animated) panel width

        # Smoothly slide the panel open/closed instead of snapping.
        self._panel_anim = QVariantAnimation(self)
        self._panel_anim.setDuration(150)
        self._panel_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._panel_anim.valueChanged.connect(self._on_panel_anim)

        # The sidebar spans the full expanded width, but a mask clips it to just
        # the two inner boxes (see _relayout). That keeps the fixed top box
        # separate from the expanding panel below it and leaves the area beside
        # the top box genuinely click-through to the comics behind it.
        self.setFixedWidth(self.EXPANDED_W)

        # --- Top box: fixed icon group (hamburger + Library), part of the top
        #     bar. It never widens or shows labels when the panel expands. ---
        self._top_box = QWidget(self)
        self._top_box.setObjectName("SidebarTop")
        self._top_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        top_layout = QVBoxLayout(self._top_box)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self._btn_hamburger = self._make_item("", "", kind="hamburger")
        self._btn_hamburger.setToolTip("")
        self._btn_hamburger.clicked.connect(self.toggle_expanded)
        top_layout.addWidget(self._btn_hamburger)

        # --- Panel: the expanding group (Library, Currently Reading, Search,
        #     Settings). This is the only part that widens and overlays comics. ---
        self._panel = QWidget(self)
        self._panel.setObjectName("SidebarPanel")
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        self._btn_home = self._make_item("", "Library", kind="home")
        self._btn_home.clicked.connect(self._on_home)
        panel_layout.addWidget(self._btn_home)

        self._btn_reading = self._make_item("", "Currently Reading", kind="book")
        self._btn_reading.clicked.connect(self._on_reading)
        panel_layout.addWidget(self._btn_reading)

        self._btn_bookmarks = self._make_item("", "Bookmarks", kind="bookmark")
        self._btn_bookmarks.clicked.connect(self._on_bookmarks)
        panel_layout.addWidget(self._btn_bookmarks)

        self._btn_search = self._make_item("", "Search", kind="search")
        self._btn_search.clicked.connect(self._on_search)
        panel_layout.addWidget(self._btn_search)

        # Search field — hidden until Search is clicked.
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self.search_changed)
        self._search_input.installEventFilter(self)
        self._search_input.hide()
        panel_layout.addWidget(self._search_input)

        panel_layout.addStretch(1)

        # Optional app menu (non-macOS only) near the bottom of the panel.
        self._btn_menu: QPushButton | None = None
        if show_app_menu:
            self._btn_menu = self._make_item("", "Menu", kind="menu")
            self._btn_menu.clicked.connect(self.app_menu_requested.emit)
            panel_layout.addWidget(self._btn_menu)

        # Fullscreen + Settings sit at the very bottom of the panel.
        self._btn_fullscreen = self._make_item("", "Fullscreen", kind="fullscreen")
        self._btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        panel_layout.addWidget(self._btn_fullscreen)

        self._settings_separator = QFrame()
        self._settings_separator.setFrameShape(QFrame.Shape.HLine)
        self._settings_separator.setFixedHeight(1)
        panel_layout.addWidget(self._settings_separator)

        self._btn_settings = self._make_item("", "Settings", kind="settings")
        self._btn_settings.clicked.connect(self._on_settings)
        panel_layout.addWidget(self._btn_settings)

        self._top_buttons = [self._btn_hamburger]
        self._panel_buttons = [
            self._btn_home, self._btn_reading, self._btn_bookmarks, self._btn_search
        ]
        if self._btn_menu is not None:
            self._panel_buttons.append(self._btn_menu)
        self._panel_buttons.append(self._btn_fullscreen)
        self._panel_buttons.append(self._btn_settings)
        self._all_buttons = [*self._top_buttons, *self._panel_buttons]
        self._refresh_text()
        self._apply_styles()
        self._relayout()

    # ----- item construction -----

    def _make_item(self, icon: str, label: str, kind: str = "glyph") -> _RailButton:
        btn = _RailButton(icon, label, kind=kind)
        btn.setToolTip("")
        btn.setFixedHeight(self.CELL)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _refresh_text(self) -> None:
        want = self._expanded or self._search_active
        # The top group is fixed: it never shows labels. Only the panel expands.
        for btn in self._top_buttons:
            btn.set_expanded(False)
        for btn in self._panel_buttons:
            btn.set_expanded(want)

    def _relayout(self) -> None:
        """Lay the fixed top box above the panel at its current animated width."""
        top_h = self._top_box.sizeHint().height()
        self._top_box.setGeometry(0, 0, self.COLLAPSED_W, top_h)
        self._panel.setGeometry(0, top_h, self._panel_w, max(0, self.height() - top_h))
        # Clip to the two boxes so the gap beside the top box is click-through.
        mask = QRegion(self._top_box.geometry()).united(QRegion(self._panel.geometry()))
        self.setMask(mask)

    def _on_panel_anim(self, value) -> None:
        self._panel_w = int(value)
        self._relayout()

    def _animate_panel_to(self, target: int) -> None:
        self._panel_anim.stop()
        if self._panel_w == target:
            self._relayout()
            return
        self._panel_anim.setDuration(95 if target == self.COLLAPSED_W else 150)
        self._panel_anim.setStartValue(self._panel_w)
        self._panel_anim.setEndValue(target)
        self._panel_anim.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    # ----- expand / collapse -----

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        want = expanded or self._search_active
        self._refresh_text()
        self._apply_styles()
        self._search_input.setVisible(self._search_active and want)
        self._animate_panel_to(self.EXPANDED_W if want else self.COLLAPSED_W)
        self.expanded_changed.emit()

    def collapse(self) -> None:
        """Close the expanded sidebar/search panel."""
        if self._search_active:
            self._close_search_input()
            return
        if self._expanded:
            self.set_expanded(False)

    def is_open(self) -> bool:
        return self._expanded or self._search_active

    # ----- slots -----

    def _on_home(self):
        self._close_search_input()
        self.set_active("library")
        self.home_clicked.emit()

    def _on_reading(self):
        self._close_search_input()
        self.set_active("currently_reading")
        self.currently_reading_clicked.emit()

    def _on_bookmarks(self):
        self._close_search_input()
        self.set_active("bookmarks")
        self.bookmarks_clicked.emit()

    def _on_search(self):
        self._search_active = True
        self.set_expanded(True)
        self._search_input.show()
        self._search_input.setFocus()

    def _on_settings(self):
        self._close_search_input()
        self.settings_clicked.emit()

    def _close_search_input(self):
        if not self._search_active:
            return
        self._search_active = False
        self._search_input.blockSignals(True)
        self._search_input.clear()
        self._search_input.blockSignals(False)
        self._search_input.hide()
        self.search_closed.emit()
        self.set_expanded(self._expanded)

    def eventFilter(self, obj, event):
        if obj is self._search_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._close_search_input()
                return True
        return super().eventFilter(obj, event)

    # ----- public API -----

    def set_active(self, key: str) -> None:
        """key is 'library' or 'currently_reading' (or '' for none)."""
        self._active = key
        self._apply_styles()

    def apply_theme(self, c: dict) -> None:
        self._theme = c
        self._apply_styles()

    def _apply_styles(self) -> None:
        c = self._theme
        # The sidebar itself stays transparent/click-through; the two boxes paint.
        self.setStyleSheet("background: transparent;")
        self._top_box.setStyleSheet("#SidebarTop { background: transparent; }")
        self._panel.setStyleSheet(f"#SidebarPanel {{ background: {c['sidebar_bg']}; }}")
        self._settings_separator.setStyleSheet(
            "background: #4a4a4a; border: none;"
        )
        expanded = self._expanded or self._search_active
        # Icons keep their full collapsed size whether or not the rail is expanded;
        # only the panel labels appear (top group never gets a label).
        active_map = {
            id(self._btn_home): "library",
            id(self._btn_reading): "currently_reading",
            id(self._btn_bookmarks): "bookmarks",
        }
        for btn in self._all_buttons:
            key = active_map.get(id(btn))
            active = key is not None and key == self._active
            bg = "rgba(255,255,255,0.13)" if active else "transparent"
            icon_px = 21
            label_px = 13 if (expanded and btn in self._panel_buttons) else icon_px
            font = QFont("Libre Baskerville")
            font.setPixelSize(label_px)
            btn.setFont(font)
            btn.set_icon_size(icon_px)
            btn.set_fg(c["text"])
            # Background/hover/active only — the glyph is painted by _RailButton.
            btn.setStyleSheet(
                f"QPushButton {{ border: none; background: {bg};"
                f" border-radius: 0; margin: 0; }}"
                f"QPushButton:hover {{ background: rgba(255,255,255,0.08); }}"
            )
        self._search_input.setStyleSheet(
            f"QLineEdit {{ margin: 4px 8px; padding: 6px; border: 1px solid {c['border']};"
            f" border-radius: 6px; background: {c['input_bg']}; color: {c['text']}; }}"
            f"QLineEdit:focus {{ border-color: {c['accent']}; }}"
        )


SUPPORTED_FILTERS = [
    "Comic files (*.cbz *.cbr *.cb7 *.cbt *.pdf *.epub *.zip *.rar *.7z *.tar)",
    "CBZ files (*.cbz *.zip)",
    "CBR files (*.cbr *.rar)",
    "CB7 files (*.cb7 *.7z)",
    "CBT files (*.cbt *.tar)",
    "PDF files (*.pdf)",
    "EPUB files (*.epub)",
    "All files (*)",
]


def _setup_comic_file_dialog(dialog: QFileDialog) -> None:
    """macOS often greys out comics unless 'All files' is the active filter."""
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    filters = ["All files (*)"] + [
        f for f in SUPPORTED_FILTERS if not f.startswith("All files")
    ]
    dialog.setNameFilters(filters)
    dialog.selectNameFilter("All files (*)")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1100, 1000)

        self._theme = themes.DARK
        self._reader: ComicReader | None = None
        self._current_page: int = 0
        self._current_comic_id: int | None = None
        # Order the reader uses for prev/next comic when a comic isn't in a
        # series — captured from the bookshelf at open time so navigation follows
        # the order the user was actually looking at (their chosen sort / manual
        # order), not a hardcoded title sort.
        self._nav_order_ids: list[int] = []
        self._settings = app_settings()
        self._library = Library()
        self._scan_thread: QThread | None = None
        self._scanner: LibraryScanner | None = None
        self._scan_progress: QProgressDialog | None = None
        self._scan_queue: list[str] = []
        self._scan_totals = {"added": 0, "skipped": 0, "errors": [], "cancelled": False}
        self._scan_generation = 0
        self._dedupe_thread: QThread | None = None
        self._dedupe_scanner: DuplicateScanner | None = None
        self._dedupe_progress: QProgressDialog | None = None

        # Text-ebook (novel) reading state
        self._ebook: EpubBook | None = None
        self._ebook_mode: bool = False

        # Reading-session tracking for statistics (Item 34)
        self._session_clock: float | None = None  # time.monotonic at last flush
        self._session_pages: int = 0              # net forward pages since last flush
        self._session_max_page: int = 0           # furthest page reached this session

        # Reading-mode state (spread is session-only; webtoon + manga persist per-comic)
        self._spread_mode: bool = True
        self._webtoon_mode: bool = False
        self._is_manga: bool = False
        # 100 / 80 / 60 / 50 / 40 / 30 — persisted globally so it survives restart.
        self._webtoon_width_pct: int = prefs.get_int(prefs.WEBTOON_WIDTH)

        # Page cache + preloader
        self._cache: PageCache = PageCache()
        self._preloader: PagePreloader | None = None

        # Debounce webtoon scroll progress — avoid SQLite writes on every page boundary
        self._webtoon_save_timer = QTimer(self)
        self._webtoon_save_timer.setSingleShot(True)
        self._webtoon_save_timer.setInterval(1500)
        self._webtoon_save_timer.timeout.connect(self._save_progress)

        # Thumb strip load-once guard
        self._thumb_strip_loaded: bool = False
        self._thumb_strip_enabled: bool = False

        self._kb = KeybindingManager()
        self._last_right_key_ms: int | None = None
        self._RIGHT_DOUBLE_MS = 400

        self._stack = QStackedWidget()
        bookshelf_mod.set_tile_scale(prefs.get_str(prefs.TILE_SIZE))
        self._bookshelf = BookshelfView(self._library)
        self.viewer = ComicViewer()
        self._webtoon_viewer = WebtoonViewer()
        self._ebook_viewer = EbookViewer()
        self._settings_view = SettingsView()
        self._settings_view.back_requested.connect(self._close_settings)
        self._settings_view.theme_changed.connect(self._on_theme_changed)
        self._settings_view.animations_changed.connect(self._on_animations_changed)
        self._settings_view.tile_size_changed.connect(self._on_tile_size_changed)
        self._settings_view.sidebar_default_changed.connect(
            self._on_sidebar_default_changed
        )
        self._settings_view.reading_defaults_changed.connect(
            self._on_reading_defaults_changed
        )
        self._settings_view.ebook_defaults_changed.connect(
            self._on_ebook_defaults_changed
        )
        self._settings_view.library_action.connect(self._on_library_action)
        self._settings_view.shortcuts_requested.connect(self._open_shortcuts_dialog)
        self._settings_view.shortcut_changed.connect(self._reload_shortcuts)
        self._animations_enabled = prefs.get_bool(prefs.ANIMATIONS)
        self._stack.addWidget(self._bookshelf)      # index 0
        self._stack.addWidget(self.viewer)           # index 1
        self._stack.addWidget(self._webtoon_viewer)  # index 2
        self._stack.addWidget(self._ebook_viewer)    # index 3 — text ebooks
        # _settings_view is a floating overlay on _content, not a stack page.

        # Instant loading screen + background archive opening.
        self._loading_overlay = _LoadingOverlay(self._stack)
        self._open_gen = 0
        self._open_worker: _ComicOpenWorker | None = None
        self._opening_path: str | None = None
        self._opening_start_at_top: bool = False
        self._opening_target_page: int | None = None
        self._open_started: float = 0.0
        self._open_min_elapsed = False
        self._open_page_ready = False

        self._reader_footer = ReaderFooter()
        self._reader_footer.setVisible(False)
        self._seek_bar = self._reader_footer.seek_bar  # inner bar; existing calls reuse it
        self._reader_footer_opacity = QGraphicsOpacityEffect(self._reader_footer)
        self._reader_footer.setGraphicsEffect(self._reader_footer_opacity)
        self._reader_footer_opacity.setOpacity(1.0)
        self._reader_footer_height_anim = QPropertyAnimation(
            self._reader_footer, b"maximumHeight", self
        )
        self._reader_footer_height_anim.setDuration(260)
        self._reader_footer_height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_footer_opacity_anim = QPropertyAnimation(
            self._reader_footer_opacity, b"opacity", self
        )
        self._reader_footer_opacity_anim.setDuration(260)
        self._reader_footer_opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_footer_opacity_anim.valueChanged.connect(
            lambda value: self._set_footer_thumb_opacity(float(value))
        )
        self._reader_footer_anim = QParallelAnimationGroup(self)
        self._reader_footer_anim.addAnimation(self._reader_footer_height_anim)
        self._reader_footer_anim.addAnimation(self._reader_footer_opacity_anim)
        self._reader_footer_anim.finished.connect(
            self._on_reader_footer_anim_finished
        )
        self._reader_footer_should_hide = False
        self._reader_footer_target_visible = False

        self._thumb_strip = ThumbnailStrip()
        self._thumb_strip.setVisible(False)
        self._thumb_strip_opacity = QGraphicsOpacityEffect(self._thumb_strip)
        self._thumb_strip.setGraphicsEffect(self._thumb_strip_opacity)
        self._thumb_strip_opacity.setOpacity(1.0)

        self._reader_bar = _ReaderBar()
        self._reader_bar.back_clicked.connect(self._back_to_library)
        self._reader_bar.bookmark_requested.connect(self._show_bookmark_menu)
        self._reader_bar.fit_requested.connect(self._show_fit_menu)
        self._reader_bar.page_mode_requested.connect(self._show_page_mode_menu)
        self._reader_bar.manga_requested.connect(self._show_manga_menu)
        self._reader_bar.fullscreen_requested.connect(self._toggle_window_fullscreen)
        self._reader_bar_opacity = QGraphicsOpacityEffect(self._reader_bar)
        self._reader_bar.setGraphicsEffect(self._reader_bar_opacity)
        self._reader_bar_opacity.setOpacity(1.0)

        self._reader_bar_height_anim = QPropertyAnimation(self._reader_bar, b"maximumHeight", self)
        self._reader_bar_height_anim.setDuration(260)
        self._reader_bar_height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_bar_opacity_anim = QPropertyAnimation(self._reader_bar_opacity, b"opacity", self)
        self._reader_bar_opacity_anim.setDuration(260)
        self._reader_bar_opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reader_bar_anim = QParallelAnimationGroup(self)
        self._reader_bar_anim.addAnimation(self._reader_bar_height_anim)
        self._reader_bar_anim.addAnimation(self._reader_bar_opacity_anim)
        self._reader_bar_anim.finished.connect(self._on_reader_bar_anim_finished)
        self._reader_bar_should_hide = False
        self._reader_bar_target_visible = False
        self._chrome_hidden = False
        self._window_fullscreen = False

        content = QWidget()
        self._content = content
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content.setAutoFillBackground(False)
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        self._stack.setStyleSheet("background: transparent;")
        content_layout.addWidget(self._stack)
        self._settings_view.setParent(content)
        self._settings_view.hide()
        self._reader_bar.setParent(content)
        self._thumb_strip.setParent(content)
        self._reader_footer.setParent(content)

        self._sidebar = _Sidebar(show_app_menu=sys.platform != "darwin")
        self._sidebar.app_menu_requested.connect(self._show_app_menu)
        self._sidebar.home_clicked.connect(self._go_home)
        self._sidebar.currently_reading_clicked.connect(
            self._show_currently_reading
        )
        self._sidebar.bookmarks_clicked.connect(self._show_bookmarks)
        self._sidebar.search_changed.connect(self._bookshelf.search)
        self._sidebar.search_closed.connect(self._bookshelf.clear_search)
        self._sidebar.settings_clicked.connect(self._open_settings)
        self._sidebar.fullscreen_clicked.connect(self._toggle_window_fullscreen)

        container = QWidget()
        self._sidebar_host = container
        h_layout = QHBoxLayout(container)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)
        # Reserve a fixed gutter for the collapsed icon rail so the content never
        # shifts. The sidebar itself floats on top (see below) and, when expanded,
        # spills over the content instead of pushing it.
        h_layout.addSpacing(_Sidebar.COLLAPSED_W)
        self._gutter_spacer = h_layout.itemAt(0)  # collapses when the sidebar hides
        h_layout.addWidget(content)
        self.setCentralWidget(container)

        # One solid strip for the whole top icon/title row — sidebar + library
        # header both sit on this so the colour is identical pixel-for-pixel.
        self._top_chrome = QWidget(container)
        self._top_chrome.setObjectName("TopChrome")
        self._top_chrome.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Float the sidebar over the content as an overlay child of the container.
        self._sidebar.setParent(container)
        self._sidebar.raise_()
        self._sidebar.show()
        self._top_chrome.show()
        self._sidebar.expanded_changed.connect(self._position_sidebar_overlay)
        container.installEventFilter(self)
        self._position_sidebar_overlay()

        # Prev/next comic covers float over the top edge of the reader footer.
        # They're children of `content` (not the footer) so they aren't clipped
        # to the thin bar and can rise above it over the page.
        self._prev_thumb_ov = ComicThumbButton(ReaderFooter.THUMB_W, ReaderFooter.THUMB_H, content)
        self._next_thumb_ov = ComicThumbButton(ReaderFooter.THUMB_W, ReaderFooter.THUMB_H, content)
        self._prev_thumb_ov.clicked.connect(self._open_prev_comic)
        self._next_thumb_ov.clicked.connect(self._open_next_comic)
        self._prev_thumb_ov.hide()
        self._next_thumb_ov.hide()
        self._prev_thumb_opacity = QGraphicsOpacityEffect(self._prev_thumb_ov)
        self._next_thumb_opacity = QGraphicsOpacityEffect(self._next_thumb_ov)
        self._prev_thumb_ov.setGraphicsEffect(self._prev_thumb_opacity)
        self._next_thumb_ov.setGraphicsEffect(self._next_thumb_opacity)
        self._prev_thumb_opacity.setOpacity(1.0)
        self._next_thumb_opacity.setOpacity(1.0)
        # Reposition/redisplay the thumbs whenever the footer shows, hides, or moves.
        self._reader_footer.installEventFilter(self)
        content.installEventFilter(self)
        self._position_reader_overlays()

        self._trans_overlay = QLabel(content)
        self._trans_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._trans_overlay.hide()
        self._opacity_effect = QGraphicsOpacityEffect(self._trans_overlay)
        self._trans_overlay.setGraphicsEffect(self._opacity_effect)
        self._trans_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._trans_anim.setDuration(520)
        self._trans_anim.setStartValue(1.0)
        self._trans_anim.setEndValue(0.0)
        self._trans_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._trans_anim.finished.connect(self._trans_overlay.hide)

        self._bookshelf.comic_opened.connect(self._open_comic_from_bookshelf)
        self._bookshelf.bookmark_opened.connect(self._open_bookmark_from_bookshelf)
        self._bookshelf.folder_entered.connect(self._on_folder_level_changed)
        self._bookshelf.rescan_all_requested.connect(self.rescan_all_folders)
        self._bookshelf.export_shelf_requested.connect(self.export_shelf)
        self._bookshelf.folder_rescan_requested.connect(self.rescan_folder)
        self._bookshelf.comics_delete_requested.connect(self._prepare_comics_for_deletion)
        self.viewer.page_forward.connect(self.next_page)
        self.viewer.page_back.connect(self.prev_page)
        self.viewer.center_clicked.connect(self._toggle_reading_chrome)
        self._seek_bar.seeked.connect(self._on_seek_bar)
        self._seek_bar.preview.connect(self._on_seek_preview)
        self._reader_footer.prev_comic_clicked.connect(self._open_prev_comic)
        self._reader_footer.next_comic_clicked.connect(self._open_next_comic)
        self._reader_bar.menu_requested.connect(self._show_reader_menu)
        self._thumb_strip.page_selected.connect(self.seek_to_page)
        self._webtoon_viewer.page_changed.connect(self._on_webtoon_page_changed)
        self._webtoon_viewer.start_page_rendered.connect(self._on_webtoon_start_page_rendered)
        self._webtoon_viewer.center_clicked.connect(self._toggle_reading_chrome)
        # Scrolling within a single (tall) page never crosses a page boundary, so
        # page_changed won't fire — hook raw scrolls too so the exact offset still
        # gets saved (debounced) once you stop scrolling.
        self._webtoon_viewer.scrolled.connect(self._on_webtoon_scrolled)
        self._ebook_viewer.chapter_changed.connect(self._on_ebook_chapter_changed)
        self._ebook_viewer.exit_requested.connect(self._back_to_library)
        self._ebook_viewer.end_reached.connect(self._on_ebook_end)

        self._build_menus()
        if sys.platform != "darwin":
            self.menuBar().setVisible(False)
            self.statusBar().setVisible(False)

        self.setAcceptDrops(True)
        self._restore_window_state()

        if prefs.get_bool(prefs.SIDEBAR_EXPANDED):
            self._sidebar.set_expanded(True)

        start_theme = themes.LIGHT if prefs.get_str(prefs.THEME) == "light" else themes.DARK
        self.apply_theme(start_theme)

        QApplication.instance().installEventFilter(self)

    # ----- UI construction -----

    def _position_sidebar_overlay(self) -> None:
        """Pin the floating sidebar to the left edge, full height, above content."""
        host = getattr(self, "_sidebar_host", None)
        if host is None:
            return
        chrome = getattr(self, "_top_chrome", None)
        if chrome is not None and chrome.isVisible():
            chrome.setGeometry(0, 0, host.width(), _Sidebar.CELL)
            chrome.lower()
        self._sidebar.setGeometry(0, 0, self._sidebar.width(), host.height())
        self._sidebar.raise_()

    def _position_settings_overlay(self) -> None:
        """Size the floating settings panel to fill the content area."""
        content = getattr(self, "_content", None)
        if content is None:
            return
        self._settings_view.setGeometry(0, 0, content.width(), content.height())
        self._settings_view.raise_()

    def _position_reader_overlays(self) -> None:
        """Pin reader chrome over the page without reserving layout space."""
        content = getattr(self, "_content", None)
        if content is None:
            return
        w = content.width()
        h = content.height()
        self._reader_bar.setGeometry(0, 0, w, _ReaderBar.HEIGHT)
        self._reader_footer.setGeometry(0, h - ReaderFooter.HEIGHT, w, ReaderFooter.HEIGHT)
        strip_h = max(
            self._thumb_strip.minimumHeight(),
            self._thumb_strip.height(),
            self._thumb_strip.sizeHint().height(),
        )
        if strip_h <= 0:
            strip_h = 110
        footer_top = self._reader_footer.y()
        visible_strip_h = min(strip_h, max(0, footer_top))
        self._thumb_strip.setGeometry(
            0,
            max(0, footer_top - visible_strip_h),
            w,
            visible_strip_h,
        )
        if self._thumb_strip.isVisible():
            self._thumb_strip.raise_()
        self._reader_footer.raise_()
        self._position_footer_thumbs()

    def _sync_thumb_strip_visibility(self) -> None:
        """Show page thumbnails only as part of the bottom reader chrome."""
        visible = (
            self._thumb_strip_enabled
            and self._reader is not None
            and not self._ebook_mode
            and self._reader_footer_target_visible
            and self._reader_footer.isVisible()
            and not self._reader_footer_should_hide
        )
        self._thumb_strip.setVisible(visible)
        if visible:
            if not self._thumb_strip_loaded:
                self._thumb_strip.load_comic(self._reader)
                self._thumb_strip_loaded = True
            self._thumb_strip.set_current(self._current_page)
            self._thumb_strip.raise_()
            self._reader_footer.raise_()

    def _position_footer_thumbs(self) -> None:
        """Float the prev/next covers over the top edge of the footer bar.

        Each cover overlaps the bar by a little and rises above it over the page.
        Shown only while the footer is visible and the neighbor exists.
        """
        prev_ov = getattr(self, "_prev_thumb_ov", None)
        if prev_ov is None:
            return
        footer = self._reader_footer
        visible = footer.isVisible() and not self._reader_footer_should_hide
        fw, fh = ReaderFooter.THUMB_W, ReaderFooter.THUMB_H
        # Bottom of the cover rests just above the bottom of the bar; the rest
        # of it rises above the bar over the page.
        lift = 7
        top = footer.y() + footer.height() - lift - fh
        margin = 14
        right_x = self._content.width() - margin - fw
        for thumb, x in (
            (self._prev_thumb_ov, margin),
            (self._next_thumb_ov, right_x),
        ):
            thumb.move(x, top)
            thumb.setVisible(visible and thumb.has_cover())
            if thumb.isVisible():
                thumb.raise_()

    def _set_footer_thumb_opacity(self, opacity: float) -> None:
        if getattr(self, "_prev_thumb_opacity", None) is None:
            return
        self._prev_thumb_opacity.setOpacity(opacity)
        self._next_thumb_opacity.setOpacity(opacity)
        if getattr(self, "_thumb_strip_opacity", None) is not None:
            self._thumb_strip_opacity.setOpacity(opacity)

    def _set_gutter(self, on: bool) -> None:
        """Reserve (or release) the left gutter the floating sidebar sits in.

        The sidebar overlay reserves a fixed COLLAPSED_W column so content never
        shifts. In the reader the sidebar is hidden, so that column must collapse
        to zero — otherwise an empty gutter lingers and pushes the page right.
        """
        spacer = getattr(self, "_gutter_spacer", None)
        if spacer is None:
            return
        spacer.changeSize(int(_Sidebar.COLLAPSED_W) if on else 0, 0)
        host = getattr(self, "_sidebar_host", None)
        if host is not None and host.layout() is not None:
            host.layout().invalidate()
            host.layout().activate()

    def _show_sidebar(self) -> None:
        self._set_gutter(True)
        self._sidebar.show()
        if getattr(self, "_top_chrome", None) is not None:
            self._top_chrome.show()
            self._position_sidebar_overlay()

    def _hide_sidebar(self) -> None:
        self._sidebar.hide()
        if getattr(self, "_top_chrome", None) is not None:
            self._top_chrome.hide()
        self._set_gutter(False)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._collapse_sidebar_if_click_outside(event)
        if obj is getattr(self, "_sidebar_host", None) and event.type() == QEvent.Type.Resize:
            self._position_sidebar_overlay()
            self._position_footer_thumbs()
            return False
        if obj is getattr(self, "_content", None) and event.type() == QEvent.Type.Resize:
            self._position_reader_overlays()
            sv = getattr(self, "_settings_view", None)
            if sv is not None and sv.isVisible():
                self._position_settings_overlay()
            return False
        if obj is getattr(self, "_reader_footer", None) and event.type() in (
            QEvent.Type.Show, QEvent.Type.Hide,
            QEvent.Type.Move, QEvent.Type.Resize,
        ):
            self._position_footer_thumbs()
            return False
        if event.type() == QEvent.Type.KeyPress and self._is_reading_view():
            focus = QApplication.focusWidget()
            if focus is not None and self.isAncestorOf(focus):
                if (
                    event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and event.modifiers() == Qt.KeyboardModifier.NoModifier
                    and self._is_at_last_page()
                ):
                    self._last_right_key_ms = None
                    if not self._advance_to_next_comic():
                        self._open_neighbor_comic(forward=True)
                    return True
                if (
                    event.key() == Qt.Key.Key_Right
                    and event.modifiers() == Qt.KeyboardModifier.NoModifier
                ):
                    now = int(time.monotonic() * 1000)
                    if (
                        self._last_right_key_ms is not None
                        and now - self._last_right_key_ms <= self._RIGHT_DOUBLE_MS
                        and self._is_at_last_page()
                    ):
                        self._last_right_key_ms = None
                        if not self._advance_to_next_comic():
                            self._open_neighbor_comic(forward=True)
                        return True
                    else:
                        self._last_right_key_ms = now
                else:
                    self._last_right_key_ms = None
        return super().eventFilter(obj, event)

    def _collapse_sidebar_if_click_outside(self, event) -> None:
        sidebar = getattr(self, "_sidebar", None)
        if sidebar is None or not sidebar.isVisible() or not sidebar.is_open():
            return
        try:
            global_pos = event.globalPosition().toPoint()
        except AttributeError:
            global_pos = event.globalPos()
        local_pos = sidebar.mapFromGlobal(global_pos)
        if sidebar.visibleRegion().contains(local_pos):
            return
        sidebar.collapse()

    def _build_menus(self):
        kb = self._kb
        menubar = self.menuBar()
        old_thumb_action = getattr(self, "_thumb_shortcut_action", None)
        if old_thumb_action is not None:
            self.removeAction(old_thumb_action)
            old_thumb_action.deleteLater()
            self._thumb_shortcut_action = None

        file_menu = menubar.addMenu("&File")

        open_action = QAction("&Open...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        open_folder_action = QAction("Open &Folder...", self)
        open_folder_action.setShortcut("Ctrl+Shift+O")
        open_folder_action.triggered.connect(self.open_folder_dialog)
        file_menu.addAction(open_folder_action)

        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("&View")

        def _shortcuts(action_id: str) -> list[QKeySequence]:
            primary = QKeySequence(kb.get(action_id))
            extras = [QKeySequence(s) for s in ACTIONS[action_id]["extras"]]
            return [primary] + extras

        fit_page = QAction("Fit &Page", self)
        fit_page.setShortcuts(_shortcuts("fit_page"))
        fit_page.triggered.connect(lambda: self._set_fit_mode(FitMode.FIT_PAGE))
        view_menu.addAction(fit_page)

        fit_width = QAction("Fit &Width", self)
        fit_width.setShortcuts(_shortcuts("fit_width"))
        fit_width.triggered.connect(lambda: self._set_fit_mode(FitMode.FIT_WIDTH))
        view_menu.addAction(fit_width)

        actual = QAction("&Actual Size", self)
        actual.setShortcuts(_shortcuts("actual_size"))
        actual.triggered.connect(lambda: self._set_fit_mode(FitMode.ACTUAL_SIZE))
        view_menu.addAction(actual)

        view_menu.addSeparator()

        zoom_in = QAction("Zoom &In", self)
        zoom_in.setShortcuts(_shortcuts("zoom_in"))
        zoom_in.triggered.connect(self.viewer.zoom_in)
        view_menu.addAction(zoom_in)

        zoom_out = QAction("Zoom &Out", self)
        zoom_out.setShortcuts(_shortcuts("zoom_out"))
        zoom_out.triggered.connect(self.viewer.zoom_out)
        view_menu.addAction(zoom_out)

        view_menu.addSeparator()
        fullscreen = QAction("&Fullscreen", self)
        fullscreen.setShortcuts(_shortcuts("fullscreen"))
        fullscreen.triggered.connect(self._toggle_window_fullscreen)
        view_menu.addAction(fullscreen)

        hide_chrome = QAction("Hide &Reader Bars", self)
        hide_chrome.triggered.connect(self._toggle_reading_chrome)
        view_menu.addAction(hide_chrome)

        view_menu.addSeparator()
        shortcuts_action = QAction("Customize Shortcuts…", self)
        shortcuts_action.triggered.connect(self._open_shortcuts_dialog)
        view_menu.addAction(shortcuts_action)

        nav_menu = menubar.addMenu("&Navigate")

        next_page = QAction("&Next Page", self)
        next_page.setShortcuts(_shortcuts("next_page"))
        next_page.triggered.connect(self.next_page)
        nav_menu.addAction(next_page)

        prev_page = QAction("&Previous Page", self)
        prev_page.setShortcuts(_shortcuts("prev_page"))
        prev_page.triggered.connect(self.prev_page)
        nav_menu.addAction(prev_page)

        first = QAction("&First Page", self)
        first.setShortcuts(_shortcuts("first_page"))
        first.triggered.connect(self.first_page)
        nav_menu.addAction(first)

        last = QAction("&Last Page", self)
        last.setShortcuts(_shortcuts("last_page"))
        last.triggered.connect(self.last_page)
        nav_menu.addAction(last)

        nav_menu.addSeparator()

        bm_toggle = QAction("Toggle Bookmark", self)
        bm_toggle.setShortcuts(_shortcuts("bookmark"))
        bm_toggle.triggered.connect(self._toggle_bookmark)
        nav_menu.addAction(bm_toggle)

        bm_prev = QAction("Previous Bookmark", self)
        bm_prev.setShortcuts(_shortcuts("prev_bookmark"))
        bm_prev.triggered.connect(self._prev_bookmark)
        nav_menu.addAction(bm_prev)

        bm_next = QAction("Next Bookmark", self)
        bm_next.setShortcuts(_shortcuts("next_bookmark"))
        bm_next.triggered.connect(self._next_bookmark)
        nav_menu.addAction(bm_next)

        library_menu = menubar.addMenu("&Library")

        self._add_folder_action = QAction("&Add Folder to Library...", self)
        self._add_folder_action.setShortcut("Ctrl+L")
        self._add_folder_action.triggered.connect(self.add_folder_to_library)
        library_menu.addAction(self._add_folder_action)

        self._add_files_action = QAction("Add &Files to Library...", self)
        self._add_files_action.setShortcut("Ctrl+Shift+L")
        self._add_files_action.triggered.connect(self.add_files_to_library)
        library_menu.addAction(self._add_files_action)

        self._rescan_all_action = QAction("&Rescan All Library Folders", self)
        self._rescan_all_action.triggered.connect(self.rescan_all_folders)
        library_menu.addAction(self._rescan_all_action)

        dupes_action = QAction("Scan for &Duplicates…", self)
        dupes_action.triggered.connect(self.scan_for_duplicates)
        library_menu.addAction(dupes_action)

        stats_action = QAction("Reading &Statistics…", self)
        stats_action.triggered.connect(self.show_statistics)
        library_menu.addAction(stats_action)

        library_menu.addSeparator()

        export_action = QAction("&Export Library...", self)
        export_action.triggered.connect(self.export_library)
        library_menu.addAction(export_action)

        import_action = QAction("&Import Library...", self)
        import_action.triggered.connect(self.import_library)
        library_menu.addAction(import_action)

        import_shelf_action = QAction("Import &Shelf...", self)
        import_shelf_action.triggered.connect(self.import_shelf)
        library_menu.addAction(import_shelf_action)

        library_menu.addSeparator()

        back_action = QAction("← &Back to Library", self)
        back_action.setShortcuts(_shortcuts("back_to_library"))
        back_action.triggered.connect(self._on_escape)
        library_menu.addAction(back_action)

        self._file_menu = file_menu
        self._view_menu = view_menu
        self._nav_menu = nav_menu
        self._library_menu = library_menu

        # Thumbnail strip shortcut (no menu entry needed — handled via ⋮)
        thumb_action = QAction(self)
        thumb_action.setShortcuts(_shortcuts("thumbnail_strip"))
        thumb_action.triggered.connect(self._toggle_thumb_strip)
        self.addAction(thumb_action)
        self._thumb_shortcut_action = thumb_action


    def _fade_switch(self, switch_fn):
        """Capture the current stack view, run switch_fn, then fade the capture out."""
        if not getattr(self, "_animations_enabled", True):
            switch_fn()
            return
        grab = self._stack.grab()
        stack_geom = self._stack.geometry()
        switch_fn()
        self._trans_overlay.setPixmap(grab)
        self._trans_overlay.setGeometry(stack_geom)
        self._trans_overlay.show()
        self._trans_overlay.raise_()
        self._trans_anim.stop()
        self._opacity_effect.setOpacity(1.0)
        self._trans_anim.start()

    # ----- Library / reader navigation -----

    def _open_comic_from_bookshelf(self, path: str):
        self._nav_order_ids = self._bookshelf.displayed_comic_order()
        self.load_file(path)

    def _open_bookmark_from_bookshelf(self, path: str, page_index: int):
        self._nav_order_ids = self._bookshelf.displayed_comic_order()
        self.load_file(path, target_page=page_index)

    def _prepare_comics_for_deletion(self, comic_ids: list[int]) -> None:
        """Release open file handles before the bookshelf deletes comics from disk."""
        # Cancel any comic still opening in the background.
        self._open_gen += 1
        self._loading_overlay.stop()
        if self._current_comic_id is None or self._current_comic_id not in comic_ids:
            return
        self._record_reading_session()
        self._session_clock = None
        self._stop_preloader()
        self._thumb_strip.stop()
        if self._reader:
            self._reader.close()
            self._reader = None
        if self._ebook is not None:
            self._settings.setValue("ebook_font_pt", self._ebook_viewer.font_pt())
            self._ebook.close()
            self._ebook = None
        self._ebook_mode = False
        self._current_comic_id = None
        if self._stack.currentIndex() != 0:
            self._chrome_hidden = False
            self._hide_reader_bar(animated=False)
            self._reader_footer.setVisible(False)
            self._thumb_strip.setVisible(False)
            self._stack.setCurrentIndex(0)
            self._show_sidebar()
            self.setWindowTitle(APP_DISPLAY_NAME)

    def _back_to_library(self):
        # Cancel any comic still opening in the background.
        self._open_gen += 1
        self._loading_overlay.stop()
        # Returning to the library is just an in-window view switch — stay in
        # whatever fullscreen state the user chose (Esc still leaves fullscreen).
        if self._webtoon_save_timer.isActive():
            self._webtoon_save_timer.stop()
        self._save_progress()
        self._record_reading_session()
        self._session_clock = None
        self._stop_preloader()
        if self._ebook is not None:
            self._settings.setValue("ebook_font_pt", self._ebook_viewer.font_pt())
            self._ebook.close()
            self._ebook = None
        self._ebook_mode = False

        def do_switch():
            self._chrome_hidden = False
            self._hide_reader_bar(animated=False)
            self._reader_footer.setVisible(False)
            self._thumb_strip.setVisible(False)
            self._bookshelf.refresh()
            self._stack.setCurrentIndex(0)
            self._show_sidebar()
        self._fade_switch(do_switch)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self._current_comic_id = None

    # ----- Reader ⋮ menu -----

    def _show_reader_menu(self) -> None:
        if self._ebook_mode:
            self._show_ebook_menu()
            return

        menu = themes.make_menu(self)

        apply_folder_act = menu.addAction("Apply settings to folder")
        apply_folder_act.triggered.connect(self._apply_reading_settings_to_folder)
        if self._current_comic_id is not None:
            comic = self._library.get_comic_by_id(self._current_comic_id)
            if comic is not None and comic.series:
                apply_series_act = menu.addAction("Apply settings to series")
                apply_series_act.triggered.connect(
                    self._apply_reading_settings_to_series
                )

        menu.addSeparator()

        note = None
        if self._current_comic_id is not None:
            note = self._library.get_annotation_for_page(
                self._current_comic_id, self._current_page
            )
        note_text = "Edit note" if note else "Add note"
        note_act = menu.addAction(note_text)
        note_act.triggered.connect(self._edit_page_note)

        menu.addSeparator()

        if not self._webtoon_mode:
            thumb_act = menu.addAction("Thumbnails")
            thumb_act.setCheckable(True)
            thumb_act.setChecked(self._thumb_strip_enabled)
            thumb_act.triggered.connect(self._toggle_thumb_strip)

        if self._webtoon_mode:
            menu.addSeparator()
            width_menu = menu.addMenu("Webtoon width")
            for pct in (100, 80, 60, 50, 40, 30):
                act = width_menu.addAction(f"{pct}%")
                act.setCheckable(True)
                act.setChecked(self._webtoon_width_pct == pct)
                act.triggered.connect(lambda checked, p=pct: self._set_webtoon_width(p))

        menu.exec(self._reader_bar.menu_btn_global_pos())

    def _show_ebook_menu(self) -> None:
        menu = themes.make_menu(self)

        spread_act = menu.addAction("Spread mode (two pages)")
        spread_act.setCheckable(True)
        spread_act.setChecked(self._spread_mode)
        spread_act.triggered.connect(self._toggle_spread)

        menu.addSeparator()

        chapters_menu = menu.addMenu("Chapters")
        titles = self._ebook_viewer.chapter_titles()
        current = self._ebook_viewer.current_chapter()
        for i, title in enumerate(titles):
            label = f"{i + 1}. {title}"
            act = chapters_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(i == current)
            act.triggered.connect(lambda _checked=False, idx=i: self._ebook_viewer.show_chapter(idx))

        menu.addSeparator()
        menu.addAction("Look up word…").triggered.connect(
            self._ebook_viewer.prompt_dictionary
        )

        menu.exec(self._reader_bar.menu_btn_global_pos())

    # ----- Top-bar tool menus -----

    def _show_bookmark_menu(self) -> None:
        if self._current_comic_id is None or not self._reader:
            return
        menu = themes.make_menu(self)

        is_bm = self._current_page_is_bookmarked()
        toggle = menu.addAction(
            "Remove bookmark from this page" if is_bm else "Bookmark this page…"
        )
        toggle.triggered.connect(self._toggle_bookmark)

        bookmarks = sorted(
            self._library.get_bookmarks(self._current_comic_id),
            key=lambda b: b.page_index,
        )
        if bookmarks:
            menu.addSeparator()
            jump = menu.addMenu("Go to bookmark")
            for b in bookmarks:
                label = b.label or f"Page {b.page_index + 1}"
                act = jump.addAction(label)
                act.triggered.connect(lambda _c=False, p=b.page_index: self.seek_to_page(p))

        menu.exec(self._reader_bar.bookmark_btn_global_pos())

    def _show_fit_menu(self) -> None:
        if not self._reader:
            return
        menu = themes.make_menu(self)
        current = self.viewer.fit_mode
        options = [
            ("Fit width", FitMode.FIT_WIDTH),
            ("Fit height", FitMode.FIT_HEIGHT),
            ("Fit both", FitMode.FIT_PAGE),
        ]
        for label, mode in options:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == mode)
            act.triggered.connect(lambda _c=False, m=mode: self._set_fit_mode(m))
        menu.exec(self._reader_bar.fit_btn_global_pos())

    def _show_page_mode_menu(self) -> None:
        if not self._reader:
            return
        menu = themes.make_menu(self)
        if self._webtoon_mode:
            current = "webtoon"
        elif self._spread_mode:
            current = "double"
        else:
            current = "single"
        options = [
            ("Single page", "single"),
            ("Double page", "double"),
            ("Webtoon (continuous scroll)", "webtoon"),
        ]
        for label, mode in options:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(current == mode)
            act.triggered.connect(lambda _c=False, m=mode: self._set_page_mode(m))
        menu.exec(self._reader_bar.page_mode_btn_global_pos())

    def _show_manga_menu(self) -> None:
        if not self._reader:
            return
        menu = themes.make_menu(self)

        rtl_act = menu.addAction("Right-to-left (manga)")
        rtl_act.setCheckable(True)
        rtl_act.setChecked(self._is_manga)
        rtl_act.triggered.connect(self._toggle_manga)

        menu.exec(self._reader_bar.manga_btn_global_pos())

    def _set_page_mode(self, mode: str) -> None:
        """Switch between single / double (spread) / webtoon page layouts."""
        if self._ebook_mode or not self._reader:
            return
        if mode == "webtoon":
            if not self._webtoon_mode:
                self._toggle_webtoon()
            return
        # single / double both require webtoon off first.
        if self._webtoon_mode:
            self._toggle_webtoon()
        want_spread = mode == "double"
        if self._spread_mode != want_spread:
            self._toggle_spread()

    # ----- Toggle handlers -----

    def _toggle_spread(self) -> None:
        if self._webtoon_mode:
            return  # spread is meaningless in webtoon mode
        self._spread_mode = not self._spread_mode
        if self._ebook_mode:
            self._ebook_viewer.set_spread_mode(self._spread_mode)
            return
        if not self._reader:
            return
        page_count = self._reader.page_count()
        if self._spread_mode:
            self._current_page = (self._current_page // 2) * 2
            self._seek_bar.set_page_count(max(1, (page_count + 1) // 2))
        else:
            self._seek_bar.set_page_count(page_count)
        self._sync_seek_bar_display()
        self._show_current_page(direction=0)
        self._persist_reading_settings()

    def _toggle_manga(self) -> None:
        self._is_manga = not self._is_manga
        self.viewer.set_rtl(self._is_manga)
        if self._current_comic_id is not None:
            self._library.set_is_manga(self._current_comic_id, self._is_manga)

    def _toggle_webtoon(self) -> None:
        if not self._reader:
            return
        self._webtoon_mode = not self._webtoon_mode
        if self._webtoon_mode:
            self._thumb_strip_enabled = False
            self._thumb_strip.setVisible(False)
            self._spread_mode = False
            self._stop_preloader()
            self._seek_bar.set_page_count(self._reader.page_count())
            self._sync_seek_bar_display()
            self._webtoon_viewer.set_width_fraction(self._webtoon_width_pct / 100)
            self._webtoon_viewer.load_comic(self._reader, self._current_page)
            self._stack.setCurrentIndex(2)
        else:
            self._cache.clear()
            self._preloader = PagePreloader(
                self._reader, self._cache, radius=prefs.get_int(prefs.PRELOAD)
            )
            self._preloader.set_center(self._current_page)
            self._preloader.start()
            self._seek_bar.set_page_count(
                max(1, (self._reader.page_count() + 1) // 2)
                if self._spread_mode
                else self._reader.page_count()
            )
            self._sync_seek_bar_display()
            self._stack.setCurrentIndex(1)
            self._show_current_page(direction=0)
        self._persist_reading_settings()

    def _set_webtoon_width(self, pct: int) -> None:
        self._webtoon_width_pct = pct
        self._webtoon_viewer.set_width_fraction(pct / 100)
        prefs.set_value(prefs.WEBTOON_WIDTH, pct)  # survive restart

    def _toggle_thumb_strip(self) -> None:
        # Only meaningful inside the reader — without a comic the strip is an
        # empty black bar, so ignore the shortcut on the bookshelf/elsewhere.
        if not self._reader or self._webtoon_mode:
            return
        self._thumb_strip_enabled = not self._thumb_strip_enabled
        self._sync_thumb_strip_visibility()

    # ----- Bookmarks -----

    def _current_page_is_bookmarked(self) -> bool:
        if self._current_comic_id is None:
            return False
        return self._library.is_bookmarked(self._current_comic_id, self._current_page)

    def _toggle_bookmark(self) -> None:
        if self._current_comic_id is None or not self._reader:
            return
        page = self._current_page
        if self._library.is_bookmarked(self._current_comic_id, page):
            self._library.toggle_bookmark(self._current_comic_id, page)
        else:
            label, ok = QInputDialog.getText(self, "Add Bookmark", "Label (optional):")
            if not ok:
                return
            self._library.toggle_bookmark(self._current_comic_id, page, label.strip() or None)
        self._reload_bookmarks()

    def _prev_bookmark(self) -> None:
        if self._current_comic_id is None:
            return
        bookmarks = sorted(self._library.get_bookmarks(self._current_comic_id),
                           key=lambda b: b.page_index, reverse=True)
        for b in bookmarks:
            if b.page_index < self._current_page:
                self.seek_to_page(b.page_index)
                return

    def _next_bookmark(self) -> None:
        if self._current_comic_id is None:
            return
        bookmarks = sorted(self._library.get_bookmarks(self._current_comic_id),
                           key=lambda b: b.page_index)
        for b in bookmarks:
            if b.page_index > self._current_page:
                self.seek_to_page(b.page_index)
                return

    def _sync_seek_bar_display(self) -> None:
        if not self._reader:
            return
        self._seek_bar.set_display_mode(
            spread=self._spread_mode,
            total_pages=self._reader.page_count(),
        )

    def _reload_bookmarks(self) -> None:
        if self._current_comic_id is None:
            self._seek_bar.set_bookmarks([])
            self._seek_bar.set_notes([])
            return
        bookmarks = self._library.get_bookmarks(self._current_comic_id)
        self._seek_bar.set_bookmarks([(b.page_index, b.label) for b in bookmarks])
        annotations = self._library.get_annotations(self._current_comic_id)
        self._seek_bar.set_notes([(a.page_index, a.body) for a in annotations])

    def _edit_page_note(self) -> None:
        if self._current_comic_id is None or not self._reader:
            return
        if self._stack.currentIndex() not in (1, 2):
            return

        existing = self._library.get_annotation_for_page(
            self._current_comic_id, self._current_page
        )
        dlg = _AnnotationDialog(existing.body if existing else "", self)
        dlg.apply_theme(self._theme)
        if not dlg.exec():
            return

        if dlg.delete_requested():
            if existing:
                self._library.delete_annotation(existing.id)
                self.statusBar().showMessage("Page note deleted", 2500)
            self._reload_bookmarks()
            return

        body = dlg.body()
        if not body:
            QMessageBox.information(self, "Page Note", "Write a note or choose Delete.")
            return
        if existing:
            self._library.update_annotation(existing.id, body)
            self.statusBar().showMessage("Page note updated", 2500)
        else:
            self._library.add_annotation(
                self._current_comic_id, self._current_page, body
            )
            self.statusBar().showMessage("Page note added", 2500)
        self._reload_bookmarks()

    # ----- Preloader helpers -----

    def _stop_preloader(self) -> None:
        if self._preloader and self._preloader.isRunning():
            self._preloader.abort()
            self._preloader.wait()
        self._preloader = None

    # ----- Webtoon page tracking -----

    def _on_webtoon_page_changed(self, page: int) -> None:
        self._current_page = page
        page_count = self._reader.page_count() if self._reader else 0
        if page_count > 0:
            self._seek_bar.set_progress((page + 1) / page_count)
        self._reader_footer.set_current(page + 1)
        self._webtoon_save_timer.start()

    def _on_webtoon_scrolled(self) -> None:
        if self._webtoon_mode and self._current_comic_id is not None:
            self._webtoon_save_timer.start()

    # ----- Shortcuts dialog -----

    def _open_shortcuts_dialog(self) -> None:
        dlg = KeybindingDialog(self._kb, self)
        if dlg.exec():
            self._reload_shortcuts()

    def _reload_shortcuts(self) -> None:
        self._kb = KeybindingManager()
        self.menuBar().clear()
        self._build_menus()

    def _on_escape(self):
        if self._settings_view.isVisible():
            self._close_settings()
            return
        # In the reader, Escape reveals the chrome (top bar + progress bar)
        # rather than leaving fullscreen. Only once the chrome is already
        # showing does another Escape return to the library. Fullscreen is
        # left untouched here.
        if self._is_reading_view():
            if self._chrome_hidden:
                self._show_reading_chrome()
            else:
                self._back_to_library()
            return
        if self.isFullScreen():
            self._exit_window_fullscreen()

    def _on_folder_level_changed(self, in_folder: bool):
        # Keep the sidebar highlight in sync with the active view.
        if self._bookshelf._currently_reading_mode:
            self._sidebar.set_active("currently_reading")
        elif self._bookshelf._bookmarks_mode:
            self._sidebar.set_active("bookmarks")
        elif (
            not in_folder
            and self._bookshelf._current_shelf_id is None
            and not self._bookshelf._show_hidden_mode
        ):
            self._sidebar.set_active("library")
        else:
            self._sidebar.set_active("")

    def apply_theme(self, c: dict):
        from PyQt6.QtWidgets import QApplication
        self._theme = c
        themes.set_active(c)
        QApplication.instance().setStyleSheet(themes.app_stylesheet(c))
        chrome = getattr(self, "_top_chrome", None)
        if chrome is not None:
            chrome.setStyleSheet(
                f"#TopChrome {{ background-color: {c['sidebar_bg']}; border: none; }}"
            )
        self._sidebar.apply_theme(c)
        self._reader_bar.apply_theme(c)
        self._reader_footer.apply_theme(c)
        self._thumb_strip.apply_theme(c)
        self._bookshelf.apply_theme(c)
        self._ebook_viewer.apply_theme(c)
        self._settings_view.apply_theme(c)

    # ----- File loading -----

    def open_file_dialog(self):
        last_dir = self._settings.value("last_dir", str(Path.home()))
        dialog = QFileDialog(self, "Open Comic", last_dir)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        _setup_comic_file_dialog(dialog)
        if dialog.exec():
            files = dialog.selectedFiles()
            if files:
                self.load_file(files[0])

    def open_folder_dialog(self):
        last_dir = self._settings.value("last_dir", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, "Open Comic Folder", last_dir)
        if path:
            self.load_file(path)

    def _ask_completed_comic_open(self, title: str) -> bool:
        """Ask how to open a finished comic.

        Returns True to start at the beginning, False to stay on the saved page.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Finished Comic")
        box.setText(f"You've finished reading \"{title}\".")
        box.setInformativeText(
            "Would you like to start over or continue from where you left off?"
        )
        stay_btn = box.addButton("Stay where you are", QMessageBox.ButtonRole.AcceptRole)
        start_btn = box.addButton(
            "Start at the beginning", QMessageBox.ButtonRole.AcceptRole
        )
        box.setDefaultButton(stay_btn)
        box.exec()
        return box.clickedButton() is start_btn

    def load_file(
        self, path: str, start_at_top: bool = False, target_page: int | None = None
    ):
        if not Path(path).exists():
            QMessageBox.warning(
                self,
                "File Unavailable",
                "This file is not currently available on disk.\n\n"
                "If it lives in a synced folder, wait for sync to finish or "
                "restore the file, then rescan the library folder.",
            )
            return
        # Flush the previous comic's reading session before switching away.
        self._record_reading_session()
        # Text/novel EPUBs go to the dedicated ebook reader, not the comic viewer.
        if Path(path).suffix.lower() == ".epub" and is_text_epub(path):
            self._load_ebook(path)
            return
        try:
            # Stop any background threads bound to the previous reader before
            # closing it, so they can't read from a closed handle.
            self._stop_preloader()
            self._thumb_strip.stop()
            if self._reader:
                self._reader.close()
                self._reader = None
            if self._ebook is not None:
                self._settings.setValue("ebook_font_pt", self._ebook_viewer.font_pt())
                self._ebook.close()
                self._ebook = None
            self._ebook_mode = False
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open file:\n{e}")
            return

        # Show the loading screen immediately — faded cover + spinner — and
        # open + decode on a background thread so the UI never freezes.
        comic = self._library.get_comic(path)
        if (
            comic is not None
            and comic.read_status == "read"
            and target_page is None
            and not start_at_top
        ):
            title = comic.title or Path(path).stem
            start_at_top = self._ask_completed_comic_open(title)
        cover = comic.cover_path if comic is not None else None
        start_page = 0
        spread = True  # default for comics not in the library (mirrors below)
        webtoon = False
        if comic is not None:
            # Advancing forward to the next comic starts at the top, not wherever
            # that comic was last left (resume only applies to opening directly).
            if target_page is not None:
                start_page = max(0, target_page)
            else:
                start_page = 0 if start_at_top else max(0, comic.current_page)
            rs = self._library.resolve_reading_settings(
                comic, self._global_reading_defaults()
            )
            webtoon = rs.reading_mode == "webtoon"
            spread = bool(rs.spread) and not webtoon
        else:
            webtoon = prefs.get_str(prefs.DEFAULT_MODE) == "webtoon"
            spread = prefs.get_bool(prefs.DEFAULT_SPREAD) and not webtoon
        self._open_gen += 1
        self._opening_path = path
        self._opening_start_at_top = start_at_top
        self._opening_target_page = target_page
        self._open_started = time.monotonic()
        self._loading_overlay.start(cover)
        worker = _ComicOpenWorker(self._open_gen, path, start_page, spread, webtoon, self)
        worker.done.connect(self._on_comic_opened)
        worker.finished.connect(worker.deleteLater)
        self._open_worker = worker
        worker.start()

    def _on_comic_opened(self, gen: int, reader, images: dict, error: str) -> None:
        if gen != self._open_gen:
            # A newer open (or a cancel) superseded this one — discard it.
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            return
        self._open_worker = None
        if error:
            self._loading_overlay.stop()
            QMessageBox.critical(self, "Error", f"Could not open file:\n{error}")
            return
        if reader.page_count() == 0:
            reader.close()
            self._loading_overlay.stop()
            QMessageBox.warning(self, "Empty", "No pages found in this file.")
            return
        self._reader = reader
        self._finish_open_comic(self._opening_path, images)

    def _global_reading_defaults(self) -> ReadingSettings:
        """Global default viewer settings from QSettings (base beneath the DB)."""
        return ReadingSettings(
            reading_mode=prefs.get_str(prefs.DEFAULT_MODE),
            spread=prefs.get_bool(prefs.DEFAULT_SPREAD),
            fit_mode=prefs.get_str(prefs.DEFAULT_FIT),
            zoom=prefs.get_float(prefs.DEFAULT_ZOOM),
        )

    def _finish_open_comic(self, path: str, images: dict | None = None) -> None:
        self._settings.setValue("last_dir", str(Path(path).parent))
        title = Path(path).stem
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — {title}")
        self._reader_bar.set_title(title)
        self._reader_bar.set_comic_tools_visible(True)
        # Reset session state
        self._thumb_strip.setVisible(False)
        self._thumb_strip_loaded = False

        # Resume to saved page; apply reading settings resolved series→folder→comic
        comic = self._library.get_comic(path)
        _fit_mode_map = {
            "actual": FitMode.ACTUAL_SIZE,
            "width":  FitMode.FIT_WIDTH,
            "height": FitMode.FIT_HEIGHT,
            "page":   FitMode.FIT_PAGE,
        }
        default_rtl = prefs.get_bool(prefs.DEFAULT_RTL)
        if comic is not None:
            self._current_comic_id = comic.id
            if self._opening_target_page is not None:
                limit = max(0, self._reader.page_count() - 1)
                self._current_page = max(0, min(self._opening_target_page, limit))
            elif self._opening_start_at_top:
                self._current_page = 0
            else:
                self._current_page = comic.current_page if 0 < comic.current_page < self._reader.page_count() else 0
            rs = self._library.resolve_reading_settings(
                comic, self._global_reading_defaults()
            )
            self._webtoon_mode = rs.reading_mode == "webtoon"
            self._spread_mode = bool(rs.spread) and not self._webtoon_mode
            # Per-comic manga flag, falling back to the global default direction.
            self._is_manga = comic.is_manga or default_rtl
            self.viewer.restore_view_state(
                _fit_mode_map.get(rs.fit_mode, FitMode.FIT_PAGE), rs.zoom
            )
        else:
            defaults = self._global_reading_defaults()
            self._current_comic_id = None
            self._current_page = 0
            self._webtoon_mode = defaults.reading_mode == "webtoon"
            self._spread_mode = bool(defaults.spread) and not self._webtoon_mode
            self._is_manga = default_rtl
            self.viewer.restore_view_state(
                _fit_mode_map.get(defaults.fit_mode, FitMode.FIT_PAGE),
                defaults.zoom or 1.0,
            )

        # Apply global reading prefs that aren't per-comic.
        self.viewer.set_click_nav(prefs.get_bool(prefs.CLICK_NAV))
        self.viewer.set_animate(prefs.get_bool(prefs.PAGE_ANIM))

        if self._spread_mode:
            self._current_page = (self._current_page // 2) * 2

        self.viewer.set_rtl(self._is_manga)

        # Start a fresh reading session for statistics.
        self._session_clock = time.monotonic()
        self._session_pages = 0
        self._session_max_page = self._current_page

        # Set up page cache + preloader (only in non-webtoon mode)
        self._stop_preloader()
        if not self._webtoon_mode:
            self._cache.clear()
            # Seed with the page(s) the open worker already decoded off-thread,
            # so showing the first page below is instant.
            for idx, img in (images or {}).items():
                self._cache.put(idx, img)
            self._preloader = PagePreloader(
                self._reader, self._cache, radius=prefs.get_int(prefs.PRELOAD)
            )
            self._preloader.set_center(self._current_page)
            self._preloader.start()

        # Load bookmarks for seek bar
        self._reload_bookmarks()

        page_count = self._reader.page_count()
        self._reader_footer.set_total(page_count)
        self._reader_footer.set_current(self._current_page + 1)
        self._update_footer_neighbors()
        if self._webtoon_mode:
            self._thumb_strip_enabled = False
            self._thumb_strip.setVisible(False)
            self._seek_bar.set_page_count(page_count)
            self._webtoon_viewer.set_width_fraction(self._webtoon_width_pct / 100)
            # Resume at the exact scroll offset, but only when actually resuming
            # (not when jumping to a specific page or restarting from the top).
            start_fraction = 0.0
            if (
                comic is not None
                and self._opening_target_page is None
                and not self._opening_start_at_top
                and self._current_page == comic.current_page
            ):
                start_fraction = comic.current_page_fraction
            self._webtoon_viewer.load_comic(
                self._reader, self._current_page, start_fraction
            )
            target_index = 2
        else:
            if self._spread_mode:
                self._seek_bar.set_page_count(max(1, (page_count + 1) // 2))
            else:
                self._seek_bar.set_page_count(page_count)
            self._sync_seek_bar_display()
            self._show_current_page()
            target_index = 1

        # The loading overlay already covers the stack — switch underneath it
        # right away so the reader keeps preparing (webtoon pages decode, the
        # preloader warms neighbors) while the loading screen is still up.
        self._stack.setCurrentIndex(target_index)
        self._hide_sidebar()
        self._chrome_hidden = True
        self._apply_reading_chrome()

        # Reveal only when BOTH are true: the minimum hold time has passed AND
        # the starting page is actually rendered. In single/spread mode the page
        # was just shown synchronously above; in webtoon mode we wait for the
        # viewer's start_page_rendered signal.
        gen = self._open_gen
        self._open_min_elapsed = False
        self._open_page_ready = not self._webtoon_mode

        elapsed = time.monotonic() - self._open_started
        delay_ms = max(0, int((_MIN_LOADING_SCREEN_S - elapsed) * 1000))

        def min_hold_done():
            if gen == self._open_gen:
                self._open_min_elapsed = True
                self._maybe_reveal()

        QTimer.singleShot(delay_ms, min_hold_done)

        # Failsafe: never leave the loading screen stuck (e.g. a webtoon page
        # that fails to decode) — force the reveal after 10s.
        def failsafe():
            if gen == self._open_gen:
                self._loading_overlay.finish()

        QTimer.singleShot(10000, failsafe)

    def _on_webtoon_start_page_rendered(self) -> None:
        self._open_page_ready = True
        self._maybe_reveal()

    def _maybe_reveal(self) -> None:
        if self._open_min_elapsed and self._open_page_ready:
            self._loading_overlay.finish()

    def _load_ebook(self, path: str):
        """Open a text/novel EPUB in the dedicated ebook reader."""
        # Tear down any comic reader that was active.
        self._stop_preloader()
        self._thumb_strip.stop()
        if self._reader:
            self._reader.close()
            self._reader = None
        if self._ebook:
            self._ebook.close()
            self._ebook = None

        try:
            book = EpubBook(path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open ebook:\n{e}")
            return
        if book.chapter_count() == 0:
            book.close()
            QMessageBox.warning(self, "Empty", "No readable chapters found in this EPUB.")
            return

        self._settings.setValue("last_dir", str(Path(path).parent))
        title = book.title or Path(path).stem
        self.setWindowTitle(f"{APP_DISPLAY_NAME} — {title}")
        self._reader_bar.set_title(title)
        self._reader_bar.set_comic_tools_visible(False)
        # Hide comic-only chrome.
        self._reader_footer.setVisible(False)
        self._thumb_strip.setVisible(False)

        # Restore progress (current chapter) and the saved reading font size.
        comic = self._library.get_comic(path)
        chapters = book.chapter_count()
        font_pt = int(self._settings.value("ebook_font_pt", 19))
        start_at_top = False
        if comic is not None:
            if comic.read_status == "read":
                start_at_top = self._ask_completed_comic_open(title)
            self._current_comic_id = comic.id
            start_chapter = (
                0
                if start_at_top
                else (comic.current_page if 0 <= comic.current_page < chapters else 0)
            )
            # Keep the stored page_count in sync with the real chapter count so
            # the bookshelf progress bar/badges are correct (older scans stored 0).
            if comic.page_count != chapters:
                self._library.update_metadata(comic.id, page_count=chapters)
        else:
            self._current_comic_id = None
            start_chapter = 0

        self._ebook = book
        self._ebook_mode = True
        self._webtoon_mode = False
        self._spread_mode = False
        self._current_page = start_chapter
        self._ebook_viewer.set_spread_mode(False)
        self._ebook_viewer.set_font_family(prefs.get_str(prefs.EBOOK_FONT_FAMILY))
        self._ebook_viewer.load_book(book, start_chapter, font_pt)

        # Start a reading session for statistics.
        self._session_clock = time.monotonic()
        self._session_pages = 0
        self._session_max_page = self._current_page

        def do_switch():
            self._stack.setCurrentIndex(3)
            self._hide_sidebar()
            self._chrome_hidden = False
            self._apply_reading_chrome()

        QTimer.singleShot(180, lambda: self._fade_switch(do_switch))

    def _on_ebook_chapter_changed(self, index: int) -> None:
        self._current_page = index
        self._save_progress()
        self._settings.setValue("ebook_font_pt", self._ebook_viewer.font_pt())

    # ----- Page navigation -----

    def _show_current_page(self, direction: int = 0):
        if not self._reader:
            return
        try:
            if self._spread_mode:
                self._show_spread(direction)
            else:
                self._show_single(direction)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load page:\n{e}")

    def _show_single(self, direction: int = 0):
        page_count = self._reader.page_count()
        cached = self._cache.get(self._current_page)
        if cached is not None:
            from PyQt6.QtGui import QPixmap
            self.viewer.set_image_pixmap(QPixmap.fromImage(cached), direction)
        else:
            data = self._reader.get_page_bytes(self._current_page)
            self.viewer.set_image(data, direction)
        if page_count > 0:
            self._seek_bar.set_progress((self._current_page + 1) / page_count)
        self._reader_footer.set_current(self._current_page + 1)
        if self._thumb_strip.isVisible():
            self._thumb_strip.set_current(self._current_page)

    def _show_spread(self, direction: int = 0):
        page_count = self._reader.page_count()
        p1 = self._current_page
        p2 = p1 + 1
        has_p2 = p2 < page_count
        img1 = self._cache.get(p1)
        img2 = self._cache.get(p2) if has_p2 else None
        if img1 is not None and (not has_p2 or img2 is not None):
            # Both pages already decoded (open worker or preloader) — cheap path.
            pixmap = make_spread_pixmap_from_images(img1, img2, self._is_manga)
        else:
            data1 = self._reader.get_page_bytes(p1)
            data2 = self._reader.get_page_bytes(p2) if has_p2 else None
            if data2 is not None:
                pixmap = make_spread_pixmap(data1, data2, self._is_manga)
            else:
                pixmap = QPixmap()
                pixmap.loadFromData(data1)
        self.viewer.set_image_pixmap(pixmap, direction)
        pair_count = (page_count + 1) // 2
        pair_index = p1 // 2
        if pair_count > 0:
            self._seek_bar.set_progress((pair_index + 1) / pair_count)
        # The left label shows the real first page of the spread, not the pair index.
        self._reader_footer.set_current(p1 + 1)

    def _last_spread_start(self, page_count: int) -> int:
        return ((page_count - 1) // 2) * 2

    def _effective_progress_page(self) -> int:
        """Page index stored in the library (last spread counts as finished)."""
        if not self._reader:
            return self._current_page
        page_count = self._reader.page_count()
        if page_count <= 0:
            return self._current_page
        limit = page_count - 1
        if self._spread_mode and self._current_page >= self._last_spread_start(page_count):
            return limit
        return self._current_page

    def _is_at_last_page(self) -> bool:
        """True when the reader is showing the final page/spread/chapter."""
        if not self._reader:
            return False
        page_count = self._reader.page_count()
        if page_count <= 0:
            return False
        return self._effective_progress_page() >= page_count - 1

    def next_page(self):
        if self._ebook_mode:
            self._ebook_viewer.next_page()
            return
        if not self._reader:
            return
        page_count = self._reader.page_count()
        if page_count <= 0:
            return
        limit = page_count - 1

        if self._spread_mode:
            last_spread = self._last_spread_start(page_count)
            if self._current_page >= last_spread:
                self._save_progress()
                if not self._advance_to_next_comic():
                    self._open_neighbor_comic(forward=True)
                return
            self._current_page = min(self._current_page + 2, last_spread)
            self._show_current_page(direction=1)
            self._advance_preloader()
            self._save_progress()
            return

        if self._current_page < limit:
            self._current_page += 1
            self._show_current_page(direction=1)
            self._advance_preloader()
            self._save_progress()
        else:
            if not self._advance_to_next_comic():
                self._open_neighbor_comic(forward=True)

    def _advance_to_next_comic(self) -> bool:
        """Open the next series issue if one exists."""
        if self._current_comic_id is not None:
            next_in_series = self._library.get_next_in_series(self._current_comic_id)
            if next_in_series is not None:
                title = next_in_series.title or Path(next_in_series.file_path).stem
                self.load_file(next_in_series.file_path, start_at_top=True)
                self.statusBar().showMessage(f"Opened next in series: {title}", 3500)
                return True
        return False

    def _on_ebook_end(self) -> None:
        if not self._advance_to_next_comic():
            self._open_neighbor_comic(forward=True)

    def prev_page(self):
        if self._ebook_mode:
            self._ebook_viewer.prev_page()
            return
        if not self._reader:
            return
        step = 2 if self._spread_mode else 1
        if self._current_page > 0:
            self._current_page = max(self._current_page - step, 0)
            if self._spread_mode:
                self._current_page = (self._current_page // 2) * 2
            self._show_current_page(direction=-1)
            self._advance_preloader()
            self._save_progress()

    def first_page(self):
        if self._ebook_mode:
            self._ebook_viewer.show_chapter(0)
            return
        if self._reader:
            self._current_page = 0
            self._show_current_page(direction=-1)
            self._advance_preloader()
            self._save_progress()

    def last_page(self):
        if self._ebook_mode:
            if self._ebook:
                self._ebook_viewer.show_chapter(self._ebook.chapter_count() - 1)
            return
        if self._reader:
            page_count = self._reader.page_count()
            self._current_page = ((page_count - 1) // 2) * 2 if self._spread_mode else page_count - 1
            self._show_current_page(direction=1)
            self._advance_preloader()
            self._save_progress()

    def _on_seek_bar(self, value: int) -> None:
        """Handle a click/drag on the seek bar.

        In spread mode the seek bar counts two-page spreads, so it reports a
        spread-pair index — convert it back to the pair's first page index.
        In single mode the value is already a real page index.
        """
        page = value * 2 if self._spread_mode else value
        self.seek_to_page(page)

    def _on_seek_preview(self, value: int) -> None:
        """Live-update the footer's page number while dragging, without navigating.

        Replaces the old page-number-on-the-handle tooltip. The seek bar reports
        spread-pair indices in spread mode, so convert back to a real page.
        """
        page = value * 2 if self._spread_mode else value
        self._reader_footer.set_current(page + 1)
        if self._thumb_strip.isVisible():
            self._thumb_strip.set_preview(page)

    def _comic_neighbors(self, comic_id: int) -> tuple["Comic | None", "Comic | None"]:
        """Resolve (previous, next) comic for reader navigation.

        A comic that belongs to a series follows its series order. A loose comic
        follows the order captured from the bookshelf when it was opened (the
        user's current chapter order) — falling back to the library's folder
        order only when that captured order doesn't contain this comic.
        """
        comic = self._library.get_comic_by_id(comic_id)
        if comic is not None and not comic.series:
            ids = self._nav_order_ids
            if comic_id in ids:
                i = ids.index(comic_id)
                prev_id = ids[i - 1] if i > 0 else None
                next_id = ids[i + 1] if i + 1 < len(ids) else None
                prev_comic = self._library.get_comic_by_id(prev_id) if prev_id else None
                next_comic = self._library.get_comic_by_id(next_id) if next_id else None
                return prev_comic, next_comic
        return self._library.get_comic_neighbors(comic_id)

    def _update_footer_neighbors(self) -> None:
        """Refresh the floating prev/next covers and nav arrows for the open comic."""
        prev_comic = next_comic = None
        if self._current_comic_id is not None:
            prev_comic, next_comic = self._comic_neighbors(
                self._current_comic_id
            )
        self._prev_thumb_ov.set_cover(prev_comic.cover_path if prev_comic else None)
        self._next_thumb_ov.set_cover(next_comic.cover_path if next_comic else None)
        self._reader_footer.set_nav_enabled(prev_comic is not None, next_comic is not None)
        self._position_footer_thumbs()

    def _open_neighbor_comic(self, forward: bool) -> None:
        """Open the previous/next comic in series-or-folder order, if any."""
        if self._current_comic_id is None:
            return
        prev_comic, next_comic = self._comic_neighbors(
            self._current_comic_id
        )
        target = next_comic if forward else prev_comic
        if target is not None:
            # Moving forward to the next comic starts at its top; going back to
            # the previous comic resumes where it was left.
            self.load_file(target.file_path, start_at_top=forward)

    def _open_prev_comic(self) -> None:
        self._open_neighbor_comic(forward=False)

    def _open_next_comic(self) -> None:
        self._open_neighbor_comic(forward=True)

    def seek_to_page(self, page: int):
        if not self._reader:
            return
        if self._webtoon_mode:
            # Webtoon shows the continuous-scroll viewer (not self.viewer), so
            # scroll it to the target page; page_changed syncs the bar/footer.
            new_page = max(0, min(page, self._reader.page_count() - 1))
            self._current_page = new_page
            self._webtoon_viewer.scroll_to_page(new_page)
            self._save_progress()
            return
        if self._spread_mode:
            page = (page // 2) * 2  # snap to spread boundary
        new_page = max(0, min(page, self._reader.page_count() - 1))
        direction = 1 if new_page > self._current_page else (-1 if new_page < self._current_page else 0)
        self._current_page = new_page
        self._show_current_page(direction=direction)
        self._advance_preloader()
        self._save_progress()

    def _advance_preloader(self) -> None:
        if self._preloader and self._preloader.isRunning():
            self._preloader.set_center(self._current_page)

    def _save_progress(self):
        if self._current_comic_id is not None:
            page, fraction = self._effective_progress_anchor()
            self._library.update_progress(self._current_comic_id, page, fraction)
            self._persist_reading_settings()
            self._record_reading_session()

    def _effective_progress_anchor(self) -> tuple[int, float]:
        """(page, within-page fraction) to persist. Webtoon resumes at the exact
        scroll offset; paged readers store the page with a 0 fraction."""
        if self._webtoon_mode and self._reader:
            return self._webtoon_viewer.scroll_anchor()
        return self._effective_progress_page(), 0.0

    def _current_reading_settings(self) -> ReadingSettings:
        return ReadingSettings(
            reading_mode="webtoon" if self._webtoon_mode else "single",
            spread=self._spread_mode,
            fit_mode=self._FIT_MODE_STR.get(self.viewer.fit_mode, "page"),
            zoom=self.viewer.zoom_factor,
        )

    def _persist_reading_settings(self) -> None:
        """Save the current viewer settings to the comic's series (whole-series
        scope), or to the comic itself when it isn't part of a detected series."""
        if self._current_comic_id is None or self._ebook_mode:
            return
        comic = self._library.get_comic_by_id(self._current_comic_id)
        if comic is None:
            return
        settings = self._current_reading_settings()
        if comic.series:
            folder = str(Path(comic.file_path).parent)
            self._library.set_series_reading_settings(folder, comic.series, settings)
        else:
            # Loose comic: keep the legacy per-issue behavior (no series to apply to).
            self._library.set_reading_mode(comic.id, settings.reading_mode)
            self._library.set_fit_mode(comic.id, settings.fit_mode)
            self._library.set_zoom(comic.id, settings.zoom)

    def _record_reading_session(self) -> None:
        """Flush elapsed time + net forward pages for the current comic into stats.

        Pages are counted as net forward progress (re-reading the same pages back
        and forth doesn't inflate the count). A single flush's time is capped so
        leaving the app open on a page doesn't balloon the total.
        """
        if self._current_comic_id is None:
            return
        progress_page = self._effective_progress_page()
        if progress_page > self._session_max_page:
            self._session_pages += progress_page - self._session_max_page
            self._session_max_page = progress_page
        now = time.monotonic()
        elapsed = 0
        if self._session_clock is not None:
            elapsed = min(int(now - self._session_clock), 1800)
        self._session_clock = now
        pages = self._session_pages
        self._session_pages = 0
        self._library.record_reading(self._current_comic_id, pages, elapsed)

    _FIT_MODE_STR = {
        FitMode.ACTUAL_SIZE: "actual",
        FitMode.FIT_WIDTH:   "width",
        FitMode.FIT_HEIGHT:  "height",
        FitMode.FIT_PAGE:    "page",
    }

    def _set_fit_mode(self, mode: FitMode) -> None:
        """Set viewer fit mode and persist it (whole-series, or per-comic if loose)."""
        self.viewer.set_fit_mode(mode)
        self._persist_reading_settings()

    def _apply_reading_settings_to_folder(self) -> None:
        """Push the current viewer settings to every comic in this folder."""
        if self._current_comic_id is None:
            return
        comic = self._library.get_comic_by_id(self._current_comic_id)
        if comic is None:
            return
        folder = str(Path(comic.file_path).parent)
        self._library.apply_folder_reading_settings(
            folder, self._current_reading_settings()
        )
        self.statusBar().showMessage(
            f"Reading settings applied to “{Path(folder).name}”", 4000
        )

    def _apply_reading_settings_to_series(self) -> None:
        """Push the current viewer settings to every issue in this series."""
        if self._current_comic_id is None:
            return
        comic = self._library.get_comic_by_id(self._current_comic_id)
        if comic is None or not comic.series:
            return
        folder = str(Path(comic.file_path).parent)
        self._library.set_series_reading_settings(
            folder, comic.series, self._current_reading_settings()
        )
        self.statusBar().showMessage(
            f"Reading settings applied to “{comic.series}”", 4000
        )

    # ----- Window helpers -----

    def _is_reading_view(self) -> bool:
        return self._stack.currentIndex() in (1, 2, 3)

    def _apply_reading_chrome(self, animated: bool = False) -> None:
        """Top + bottom reader bars: always on unless chrome-hidden mode is active."""
        if not self._is_reading_view():
            return

        hidden = self._chrome_hidden
        if hidden:
            self._hide_reader_bar(animated=animated)
        else:
            self._show_reader_bar(animated=animated)

        if self._ebook_mode:
            self._ebook_viewer.set_reader_chrome_visible(not hidden)
            self._hide_reader_footer(animated=False)
        else:
            if hidden:
                self._hide_reader_footer(animated=animated)
            else:
                self._show_reader_footer(animated=animated)
        self._sync_thumb_strip_visibility()

    def _toggle_reading_chrome(self) -> None:
        """Hide/show reader bars only — window size and mode stay the same."""
        if not self._is_reading_view():
            return
        self._chrome_hidden = not self._chrome_hidden
        self._apply_reading_chrome(animated=True)

    def _show_app_menu(self) -> None:
        """Popup File/View/Navigate/Library menus — used on Windows/Linux."""
        menu = themes.make_menu(self)
        menu.addMenu(self._file_menu)
        menu.addMenu(self._view_menu)
        menu.addMenu(self._nav_menu)
        menu.addMenu(self._library_menu)
        menu.exec(self._sidebar.mapToGlobal(QPoint(0, 56)))

    def _open_settings(self) -> None:
        """Float the settings overlay over the content area."""
        if self._settings_view.isVisible():
            return
        self._chrome_hidden = False
        self._hide_reader_bar(animated=False)
        self._reader_footer.setVisible(False)
        self._thumb_strip.setVisible(False)
        self._settings_view.reset()
        self._settings_view.set_background_image(
            self._bookshelf.random_background_image()
        )
        self._sidebar.set_active("")
        self._position_settings_overlay()
        self._settings_view.show()

    def _close_settings(self) -> None:
        """Hide the settings overlay and return to whatever is behind it."""
        if not self._settings_view.isVisible():
            return
        self._settings_view.hide()
        self._bookshelf.refresh()
        self._on_folder_level_changed(self._bookshelf._current_folder is not None)

    # ----- settings handlers -----

    def _on_theme_changed(self, name: str) -> None:
        self.apply_theme(themes.LIGHT if name == "light" else themes.DARK)

    def _on_animations_changed(self, enabled: bool) -> None:
        self._animations_enabled = enabled

    def _on_tile_size_changed(self, name: str) -> None:
        bookshelf_mod.set_tile_scale(name)
        self._bookshelf.refresh()

    def _on_sidebar_default_changed(self, expanded: bool) -> None:
        self._sidebar.set_expanded(expanded)

    def _on_reading_defaults_changed(self) -> None:
        # Apply global-only reading controls live; per-comic fit/zoom/mode still
        # resolve when a comic is opened.
        self.viewer.set_click_nav(prefs.get_bool(prefs.CLICK_NAV))
        self.viewer.set_animate(prefs.get_bool(prefs.PAGE_ANIM))
        if (
            self._preloader
            and self._preloader.isRunning()
            and self._reader is not None
        ):
            center = self._current_page
            self._stop_preloader()
            self._cache.clear()
            self._preloader = PagePreloader(
                self._reader, self._cache, radius=prefs.get_int(prefs.PRELOAD)
            )
            self._preloader.set_center(center)
            self._preloader.start()

    def _on_ebook_defaults_changed(self) -> None:
        # Apply the new defaults live if a text ebook is open.
        if self._ebook_mode:
            self._ebook_viewer.set_font_family(prefs.get_str(prefs.EBOOK_FONT_FAMILY))
            self._ebook_viewer.set_font_pt(prefs.get_int(prefs.EBOOK_FONT_PT))

    def _on_library_action(self, action: str) -> None:
        if action == "export_shelf":
            self._export_shelf_from_settings()
            return
        handlers = {
            "add_folder": self.add_folder_to_library,
            "add_files": self.add_files_to_library,
            "rescan_all": self.rescan_all_folders,
            "regen_thumbs": self._regenerate_thumbnails,
            "clear_thumbs": self._clear_thumbnail_cache,
            "export": self.export_library,
            "import": self.import_library,
            "import_shelf": self.import_shelf,
            "duplicates": self.scan_for_duplicates,
            "stats": self.show_statistics,
            "remove_folder": self._remove_library_folder,
            "reset_library": self._reset_library,
        }
        fn = handlers.get(action)
        if fn is not None:
            fn()

    def _remove_library_folder(self) -> None:
        """Let the user drop every comic added from one source folder."""
        folders = self._library.get_source_folders()
        if not folders:
            QMessageBox.information(
                self, "Remove Folder", "No library folders to remove."
            )
            return
        folder, ok = QInputDialog.getItem(
            self, "Remove Folder",
            "Remove all comics added from this folder?\n"
            "(Your files on disk are NOT touched.)",
            folders, 0, False,
        )
        if not ok or not folder:
            return
        confirm = QMessageBox.question(
            self, "Remove Folder",
            f"Remove every comic added from:\n\n{folder}\n\n"
            "This only clears them from the library — no files on disk are deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        n = self._library.remove_source_folder(folder)
        self._bookshelf.refresh()
        QMessageBox.information(
            self, "Remove Folder", f"Removed {n} comic(s) from the library."
        )

    def _reset_library(self) -> None:
        """Wipe the whole library so the user can re-point it from scratch."""
        confirm = QMessageBox.warning(
            self, "Reset Library",
            "This removes EVERYTHING from the library — every comic, folder, "
            "shelf, tag, bookmark, and reading progress.\n\n"
            "Your actual comic files on disk are NOT deleted. You'll get an "
            "empty library and can re-add the folders you want.\n\n"
            "Reset the library now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._library.reset_library()
        self._clear_thumbnail_cache(announce=False)
        self._bookshelf.refresh()
        QMessageBox.information(
            self, "Reset Library",
            "Library cleared. Use “Add folder to library…” to start fresh.",
        )

    def _export_shelf_from_settings(self) -> None:
        shelves = self._library.get_shelves()
        user_shelves = [s for s in shelves if not s.is_smart]
        if not user_shelves:
            QMessageBox.information(self, "Export Shelf", "No bookshelves to export.")
            return
        names = [s.name for s in user_shelves]
        name, ok = QInputDialog.getItem(
            self, "Export Shelf", "Choose a bookshelf to export:", names, 0, False
        )
        if not ok or not name:
            return
        shelf = next((s for s in user_shelves if s.name == name), None)
        if shelf:
            self.export_shelf(shelf.id, shelf.name)

    def _regenerate_thumbnails(self) -> None:
        """Clear the cache so covers are rebuilt on next display, then refresh."""
        self._clear_thumbnail_cache(announce=False)
        self._bookshelf.refresh()
        QMessageBox.information(
            self, "Thumbnails", "Thumbnail cache cleared. Covers will rebuild as you browse."
        )

    def _clear_thumbnail_cache(self, announce: bool = True) -> None:
        import shutil
        import thumbnails
        cache_dir = thumbnails.thumbnail_cache_dir()
        try:
            if Path(cache_dir).exists():
                shutil.rmtree(cache_dir)
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Thumbnails", f"Could not clear cache:\n{exc}")
            return
        self._bookshelf.refresh()
        if announce:
            QMessageBox.information(self, "Thumbnails", "Thumbnail cache cleared.")

    def _go_home(self) -> None:
        """Sidebar Library button: leave settings if open, then go to root."""
        if self._settings_view.isVisible():
            self._close_settings()
        self._bookshelf.go_to_root()

    def _show_currently_reading(self) -> None:
        """Sidebar Currently Reading: leave settings if open first."""
        if self._settings_view.isVisible():
            self._close_settings()
        self._bookshelf.show_currently_reading()

    def _show_bookmarks(self) -> None:
        """Sidebar Bookmarks: leave settings if open first."""
        if self._settings_view.isVisible():
            self._close_settings()
        self._bookshelf.show_bookmarks()

    def _exit_window_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        self.showNormal()
        self._window_fullscreen = False
        if self._is_reading_view():
            self._chrome_hidden = False
            self._apply_reading_chrome()

    def _toggle_window_fullscreen(self) -> None:
        """F11: true OS fullscreen — hides the Windows taskbar and title bar."""
        if self.isFullScreen():
            self._exit_window_fullscreen()
            return
        if self._is_reading_view():
            self._chrome_hidden = True
            self._apply_reading_chrome()
        self.showFullScreen()
        self._window_fullscreen = True

    def _show_reading_chrome(self) -> None:
        if not self._chrome_hidden:
            return
        self._chrome_hidden = False
        self._apply_reading_chrome()

    def _show_reader_bar(self, animated: bool):
        if self._reader_bar_target_visible and self._reader_bar.isVisible():
            return
        self._reader_bar_target_visible = True
        self._reader_bar_should_hide = False
        self._reader_bar_anim.stop()
        self._reader_bar.show()
        self._reader_bar.raise_()
        self._position_reader_overlays()
        if not animated:
            self._reader_bar.setMinimumHeight(_ReaderBar.HEIGHT)
            self._reader_bar.setMaximumHeight(_ReaderBar.HEIGHT)
            self._reader_bar_opacity.setOpacity(1.0)
            return
        self._reader_bar.setMinimumHeight(_ReaderBar.HEIGHT)
        self._reader_bar.setMaximumHeight(_ReaderBar.HEIGHT)
        self._animate_reader_bar(_ReaderBar.HEIGHT, 1.0)

    def _hide_reader_bar(self, animated: bool):
        if not self._reader_bar_target_visible and not self._reader_bar.isVisible():
            return
        self._reader_bar_target_visible = False
        self._reader_bar_should_hide = True
        self._reader_bar_anim.stop()
        if not animated:
            self._reader_bar.setMinimumHeight(0)
            self._reader_bar.setMaximumHeight(0)
            self._reader_bar_opacity.setOpacity(0.0)
            self._reader_bar.hide()
            return
        self._reader_bar.setMinimumHeight(_ReaderBar.HEIGHT)
        self._reader_bar.setMaximumHeight(_ReaderBar.HEIGHT)
        self._animate_reader_bar(0, 0.0)

    def _animate_reader_bar(self, height: int, opacity: float):
        self._reader_bar_height_anim.setStartValue(_ReaderBar.HEIGHT)
        self._reader_bar_height_anim.setEndValue(_ReaderBar.HEIGHT)
        self._reader_bar_opacity_anim.setStartValue(self._reader_bar_opacity.opacity())
        self._reader_bar_opacity_anim.setEndValue(opacity)
        self._reader_bar_anim.start()

    def _on_reader_bar_anim_finished(self):
        if self._reader_bar_should_hide:
            self._reader_bar.setMinimumHeight(0)
            self._reader_bar.hide()
        else:
            self._reader_bar.setMinimumHeight(_ReaderBar.HEIGHT)
            self._reader_bar.setMaximumHeight(_ReaderBar.HEIGHT)

    def _show_reader_footer(self, animated: bool):
        if self._reader_footer_target_visible and self._reader_footer.isVisible():
            return
        self._reader_footer_target_visible = True
        self._reader_footer_should_hide = False
        self._reader_footer_anim.stop()
        self._reader_footer.show()
        self._reader_footer.raise_()
        self._position_reader_overlays()
        self._sync_thumb_strip_visibility()
        if not animated:
            self._reader_footer.setMinimumHeight(ReaderFooter.HEIGHT)
            self._reader_footer.setMaximumHeight(ReaderFooter.HEIGHT)
            self._reader_footer_opacity.setOpacity(1.0)
            self._set_footer_thumb_opacity(1.0)
            return
        self._reader_footer.setMinimumHeight(ReaderFooter.HEIGHT)
        self._reader_footer.setMaximumHeight(ReaderFooter.HEIGHT)
        self._animate_reader_footer(ReaderFooter.HEIGHT, 1.0)

    def _hide_reader_footer(self, animated: bool):
        if (
            not self._reader_footer_target_visible
            and not self._reader_footer.isVisible()
        ):
            return
        self._reader_footer_target_visible = False
        self._reader_footer_should_hide = True
        self._reader_footer_anim.stop()
        self._sync_thumb_strip_visibility()
        if not animated:
            self._reader_footer.setMinimumHeight(0)
            self._reader_footer.setMaximumHeight(0)
            self._reader_footer_opacity.setOpacity(0.0)
            self._set_footer_thumb_opacity(0.0)
            self._reader_footer.hide()
            self._position_footer_thumbs()
            return
        self._reader_footer.setMinimumHeight(ReaderFooter.HEIGHT)
        self._reader_footer.setMaximumHeight(ReaderFooter.HEIGHT)
        self._animate_reader_footer(0, 0.0)

    def _animate_reader_footer(self, height: int, opacity: float):
        self._reader_footer_height_anim.setStartValue(ReaderFooter.HEIGHT)
        self._reader_footer_height_anim.setEndValue(ReaderFooter.HEIGHT)
        self._reader_footer_opacity_anim.setStartValue(
            self._reader_footer_opacity.opacity()
        )
        self._reader_footer_opacity_anim.setEndValue(opacity)
        self._reader_footer_anim.start()

    def _on_reader_footer_anim_finished(self):
        if self._reader_footer_should_hide:
            self._reader_footer.setMinimumHeight(0)
            self._reader_footer.hide()
            self._sync_thumb_strip_visibility()
            self._position_footer_thumbs()
        else:
            self._reader_footer.setMinimumHeight(ReaderFooter.HEIGHT)
            self._reader_footer.setMaximumHeight(ReaderFooter.HEIGHT)
            self._set_footer_thumb_opacity(1.0)
            self._sync_thumb_strip_visibility()
            self._position_footer_thumbs()

    def _restore_window_state(self):
        geom = self._settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        # Saved geometry can land the window off-screen after a display change,
        # which looks like the app never opened (Dock keeps bouncing).
        screens = QApplication.screens()
        if screens:
            frame = self.frameGeometry()
            on_screen = any(
                s.availableGeometry().intersects(frame) for s in screens
            )
            if not on_screen:
                sg = QApplication.primaryScreen().availableGeometry()
                w = max(self.width(), 800)
                h = max(self.height(), 600)
                self.resize(w, h)
                self.move(
                    sg.x() + max(0, (sg.width() - w) // 2),
                    sg.y() + max(0, (sg.height() - h) // 2),
                )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._window_fullscreen = self.isFullScreen()
            if sys.platform == "darwin":
                self.menuBar().setVisible(not self._window_fullscreen)

    # ----- Drag and drop -----

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self.load_file(urls[0].toLocalFile())

    def closeEvent(self, event):
        self._stop_preloader()
        if self._scanner:
            self._scanner.cancel()
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait()
        if self._dedupe_scanner:
            self._dedupe_scanner.cancel()
        if self._dedupe_thread:
            self._dedupe_thread.quit()
            self._dedupe_thread.wait()
        self._save_progress()
        if self._ebook is not None:
            self._settings.setValue("ebook_font_pt", self._ebook_viewer.font_pt())
        self._settings.setValue("geometry", self.saveGeometry())
        if self._reader:
            self._reader.close()
        if self._ebook:
            self._ebook.close()
        self._library.close()
        super().closeEvent(event)

    # ----- Shelf management -----

    # ----- Library scanning -----

    def add_folder_to_library(self):
        last_dir = self._settings.value("last_library_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Add Folder to Library", last_dir)
        if not folder:
            return
        self._settings.setValue("last_library_dir", folder)
        self._start_scan(folder=folder)

    def add_files_to_library(self):
        last_dir = self._settings.value("last_library_dir", str(Path.home()))
        dialog = QFileDialog(self, "Add Files to Library", last_dir)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        _setup_comic_file_dialog(dialog)
        if not dialog.exec():
            return
        files = dialog.selectedFiles()
        if not files:
            return
        comic_files = [
            f for f in files if Path(f).suffix.lower() in SCANNABLE_EXTENSIONS
        ]
        if not comic_files:
            QMessageBox.warning(
                self,
                "Add Files to Library",
                "No supported comic files were selected.\n\n"
                "Supported types: CBZ, CBR, CB7, CBT, PDF, EPUB, and common archive extensions.",
            )
            return
        self._settings.setValue("last_library_dir", str(Path(comic_files[0]).parent))
        self._start_scan(paths=comic_files)

    def rescan_folder(self, folder_path: str):
        self._start_scan(folder=folder_path)

    def rescan_all_folders(self):
        folders = self._library.get_source_folders()
        if not folders:
            QMessageBox.information(
                self,
                "No Library Folders",
                "No scanned library folders are saved yet.",
            )
            return
        self._scan_queue = folders[1:]
        self._scan_totals = {"added": 0, "skipped": 0, "errors": [], "cancelled": False}
        self._start_scan(folder=folders[0])

    def export_library(self):
        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Library",
            str(Path(last_dir) / "comic-reader-library.json"),
            "JSON files (*.json)",
        )
        if not path:
            return
        self._settings.setValue("last_export_dir", str(Path(path).parent))
        try:
            stats = self._library.export_to_json(path)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not export library:\n{e}")
            return
        QMessageBox.information(
            self,
            "Export Complete",
            f"Library exported.\n\n"
            f"Comics: {stats['comics']}\n"
            f"Shelves: {stats['shelves']}\n"
            f"Folder covers: {stats['folder_covers']}",
        )

    def import_library(self):
        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Library",
            last_dir,
            "JSON files (*.json)",
        )
        if not path:
            return
        reply = QMessageBox.question(
            self,
            "Import Library",
            "Import this library export? Existing entries with the same file path will be updated.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            stats = self._library.import_from_json(path)
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", f"Could not import library:\n{e}")
            return
        self._bookshelf.refresh()
        QMessageBox.information(
            self,
            "Import Complete",
            f"Library import complete.\n\n"
            f"Comics added: {stats['comics_added']}\n"
            f"Comics updated: {stats['comics_updated']}\n"
            f"Shelves merged: {stats['shelves']}\n"
            f"Folder covers: {stats['folder_covers']}",
        )

    def export_shelf(self, shelf_id: int, shelf_name: str):
        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        safe_name = "".join(
            ch if ch.isalnum() or ch in " -_" else " " for ch in shelf_name
        ).strip() or "shelf"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Shelf",
            str(Path(last_dir) / f"{safe_name}.comic-reader-shelf.json"),
            "Comic Reader shelf (*.json)",
        )
        if not path:
            return
        self._settings.setValue("last_export_dir", str(Path(path).parent))
        try:
            stats = self._library.export_shelf(shelf_id, path)
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not export shelf:\n{e}")
            return
        QMessageBox.information(
            self,
            "Shelf Exported",
            f"Exported '{stats['shelf_name']}'.\n\n"
            f"Comics listed: {stats['comics']}\n\n"
            "This file shares the shelf list only, not the comic files.",
        )

    def import_shelf(self):
        last_dir = self._settings.value("last_export_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Shelf",
            last_dir,
            "Comic Reader shelf (*.json);;JSON files (*.json)",
        )
        if not path:
            return
        try:
            stats = self._library.import_shelf(path)
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", f"Could not import shelf:\n{e}")
            return
        self._bookshelf.refresh()
        QMessageBox.information(
            self,
            "Shelf Imported",
            f"Imported '{stats['shelf_name']}'.\n\n"
            f"Matched comics: {stats['matched']}\n"
            f"Not in this library: {stats['unmatched']}",
        )

    def _start_scan(self, *, folder: str | None = None, paths: list[str] | None = None):
        if self._scan_thread and self._scan_thread.isRunning():
            return

        self._scan_generation += 1
        self._active_scan_generation = self._scan_generation

        self._add_folder_action.setEnabled(False)
        self._add_files_action.setEnabled(False)
        self._rescan_all_action.setEnabled(False)

        self._scan_thread = QThread(self)
        reading_defaults = self._global_reading_defaults()
        if paths:
            self._scanner = LibraryScanner(
                self._library,
                paths=[Path(p) for p in paths],
                reading_defaults=reading_defaults,
            )
            progress_label = "Adding files…"
            progress_title = "Adding to Library"
        else:
            self._scanner = LibraryScanner(
                self._library,
                folder=Path(folder),
                reading_defaults=reading_defaults,
            )
            progress_label = "Finding comic files…"
            progress_title = "Scanning Library"
        self._scanner.moveToThread(self._scan_thread)

        self._scan_progress = QProgressDialog(progress_label, "Cancel", 0, 0, self)
        self._scan_progress.setWindowTitle(progress_title)
        self._scan_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._scan_progress.setMinimumDuration(0)
        self._scan_progress.setValue(0)

        self._scan_thread.started.connect(self._scanner.run)
        self._scanner.progress.connect(self._on_scan_progress)
        self._scanner.finished.connect(self._on_scan_finished)
        self._scan_progress.canceled.connect(self._scanner.cancel)

        self._scan_thread.start()

    def _on_scan_progress(self, current: int, total: int, filename: str):
        if getattr(self, "_active_scan_generation", 0) != self._scan_generation:
            return
        dlg = self._scan_progress
        if dlg is None:
            return
        label = f"Processing {current + 1} of {total}:\n{filename}"
        try:
            if dlg.maximum() == 0 and total > 0:
                dlg.setMaximum(total)
            dlg.setValue(current)
            # setValue can process events and finish the scan — re-check the dialog.
            if self._scan_progress is not dlg:
                return
            dlg.setLabelText(label)
        except RuntimeError:
            pass

    def _on_scan_finished(self, result):
        if self._scan_progress:
            self._scan_progress.close()
        self._cleanup_scan()
        if self._scan_queue or self._scan_totals["added"] or self._scan_totals["skipped"] or self._scan_totals["errors"]:
            self._scan_totals["added"] += result.added
            self._scan_totals["skipped"] += result.skipped
            self._scan_totals["errors"].extend(result.errors)
            self._scan_totals["cancelled"] = self._scan_totals["cancelled"] or result.cancelled
            if self._scan_queue and not result.cancelled:
                next_folder = self._scan_queue.pop(0)
                self._start_scan(folder=next_folder)
                return
            self._scan_queue.clear()
            result.added = self._scan_totals["added"]
            result.skipped = self._scan_totals["skipped"]
            result.errors = self._scan_totals["errors"]
            result.cancelled = self._scan_totals["cancelled"]
            self._scan_totals = {"added": 0, "skipped": 0, "errors": [], "cancelled": False}
        self._bookshelf.refresh()

        msg = QMessageBox(self)
        if result.cancelled:
            msg.setWindowTitle("Scan Cancelled")
            msg.setText(
                f"Scan cancelled.\n\n"
                f"Added:   {result.added}\n"
                f"Skipped: {result.skipped} (already in library)"
            )
        else:
            msg.setWindowTitle("Library Scan Complete")
            msg.setText(
                f"Library scan complete.\n\n"
                f"Added:   {result.added}\n"
                f"Skipped: {result.skipped} (already in library)\n"
                f"Errors:  {len(result.errors)}"
            )
        if result.errors:
            msg.setDetailedText(
                "\n\n".join(f"{path}\n  {err}" for path, err in result.errors)
            )
        msg.exec()

    def _cleanup_scan(self):
        scanner = self._scanner
        if scanner is not None:
            try:
                scanner.progress.disconnect(self._on_scan_progress)
                scanner.finished.disconnect(self._on_scan_finished)
            except (TypeError, RuntimeError):
                pass
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait()
            self._scan_thread = None
        self._scanner = None
        self._scan_progress = None
        self._add_folder_action.setEnabled(True)
        self._add_files_action.setEnabled(True)
        self._rescan_all_action.setEnabled(True)

    # ----- Duplicate detection -----

    def scan_for_duplicates(self):
        if self._dedupe_thread and self._dedupe_thread.isRunning():
            return

        # If everything is already hashed, skip straight to the results.
        if not self._library.get_unhashed_comics():
            self._show_duplicates()
            return

        self._dedupe_thread = QThread(self)
        self._dedupe_scanner = DuplicateScanner(self._library)
        self._dedupe_scanner.moveToThread(self._dedupe_thread)

        self._dedupe_progress = QProgressDialog(
            "Checking comics for duplicates…", "Cancel", 0, 0, self
        )
        self._dedupe_progress.setWindowTitle("Scanning for Duplicates")
        self._dedupe_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._dedupe_progress.setMinimumDuration(0)
        self._dedupe_progress.setValue(0)

        self._dedupe_thread.started.connect(self._dedupe_scanner.run)
        self._dedupe_scanner.progress.connect(self._on_dedupe_progress)
        self._dedupe_scanner.finished.connect(self._on_dedupe_finished)
        self._dedupe_progress.canceled.connect(self._dedupe_scanner.cancel)

        self._dedupe_thread.start()

    def _on_dedupe_progress(self, current: int, total: int, filename: str):
        if self._dedupe_progress is None:
            return
        if self._dedupe_progress.maximum() == 0 and total > 0:
            self._dedupe_progress.setMaximum(total)
        self._dedupe_progress.setValue(current)
        self._dedupe_progress.setLabelText(
            f"Checking {current + 1} of {total}:\n{filename}"
        )

    def _on_dedupe_finished(self, result):
        if self._dedupe_progress:
            self._dedupe_progress.close()
        if self._dedupe_thread:
            self._dedupe_thread.quit()
            self._dedupe_thread.wait()
            self._dedupe_thread = None
        self._dedupe_scanner = None
        self._dedupe_progress = None
        if not result.cancelled:
            self._show_duplicates()

    def _show_duplicates(self):
        dlg = DuplicatesDialog(self._library, self)
        dlg.changed.connect(self._bookshelf.refresh)
        dlg.exec()

    # ----- Reading statistics -----

    def show_statistics(self):
        # Flush the in-progress session so the figures are current.
        self._record_reading_session()
        StatsDialog(self._library, self).exec()
