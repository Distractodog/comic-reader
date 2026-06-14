"""Full-page Settings view.

A page in the main-window QStackedWidget (settings = index 4). Layout is a left
category list + a right stack of panels, one panel per section. Global defaults
are persisted via ``prefs`` (QSettings); per-comic/folder/series settings still
win over these. The view emits signals so the main window can apply changes live
(theme swap, tile reflow, library actions, etc.).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import prefs
import themes
from app_info import APP_DISPLAY_NAME, APP_REPO_URL, APP_VERSION

SECTIONS: list[tuple[str, str]] = [
    ("Appearance", "Theme, animations, tile size, and sidebar defaults."),
    ("Reading", "Default fit mode, reading mode, spread, zoom, and direction."),
    ("Ebook & Text", "Default font size and family for text EPUBs."),
    ("Library & Data", "Manage folders, the thumbnail cache, and import/export."),
    ("Shortcuts", "Customize keyboard shortcuts."),
    ("Sidebar", "Choose which views and shelves appear in the rail."),
    ("About", "App version and project info."),
]


class _SettingsPanel(QWidget):
    """Base panel: heading + blurb, then a body column of rows.

    Subclasses add controls via ``self._row(...)`` / ``self.body``. Registered
    labels and checkboxes are recoloured in ``apply_theme``.
    """

    def __init__(self, title: str, blurb: str, parent=None):
        super().__init__(parent)
        self._labels: list[QLabel] = []
        self._checks: list[QCheckBox] = []
        self._dividers: list[QFrame] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 30, 36, 30)
        outer.setSpacing(14)

        self._heading = QLabel(title)
        head_font = QFont("Libre Baskerville")
        head_font.setPixelSize(26)
        self._heading.setFont(head_font)

        self._blurb = QLabel(blurb)
        self._blurb.setWordWrap(True)

        outer.addWidget(self._heading)
        outer.addWidget(self._blurb)
        outer.addSpacing(4)

        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        outer.addLayout(self.body)
        outer.addStretch(1)

    # ----- row builders -----

    def _row(self, label_text: str, control: QWidget, hint: str = "") -> None:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Libre Baskerville", -1))
        self._labels.append(lbl)
        left.addWidget(lbl)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setWordWrap(True)
            hint_font = QFont()
            hint_font.setPixelSize(11)
            hint_lbl.setFont(hint_font)
            hint_lbl.setObjectName("hint")
            self._labels.append(hint_lbl)
            left.addWidget(hint_lbl)
        h.addLayout(left, 1)

        if isinstance(control, QCheckBox):
            self._checks.append(control)
        control.setMinimumWidth(180)
        if isinstance(control, (QComboBox, QFontComboBox, QSpinBox)):
            control.setFixedWidth(220)
        h.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
        self.body.addWidget(row)

    def _full(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def _divider(self) -> None:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        self._dividers.append(line)
        self.body.addWidget(line)

    def _subheading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        f = QFont("Libre Baskerville")
        f.setPixelSize(16)
        lbl.setFont(f)
        self._labels.append(lbl)
        self.body.addWidget(lbl)
        return lbl

    # ----- theming -----

    def apply_theme(self, c: dict) -> None:
        self._heading.setStyleSheet(f"color: {c['text']}; background: transparent;")
        self._blurb.setStyleSheet(
            f"color: {c['text_secondary']}; background: transparent;"
        )
        for lbl in self._labels:
            if lbl is self._blurb:
                continue
            if lbl.objectName() == "hint":
                lbl.setStyleSheet(
                    f"color: {c['text_secondary']}; background: transparent;"
                )
            else:
                lbl.setStyleSheet(f"color: {c['text']}; background: transparent;")
        for cb in self._checks:
            cb.setStyleSheet(
                f"QCheckBox {{ color: {c['text']}; spacing: 8px;"
                f" background: transparent; }}"
            )
        for line in self._dividers:
            line.setStyleSheet(f"background: {c['border']}; border: none;")
        self.on_theme(c)

    def on_theme(self, c: dict) -> None:
        """Hook for subclasses with extra widgets to recolour."""


def _combo(items: list[tuple[str, object]], current) -> QComboBox:
    combo = QComboBox()
    for label, value in items:
        combo.addItem(label, value)
    idx = combo.findData(current)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    return combo


# ----------------------------------------------------------------------------
# Appearance
# ----------------------------------------------------------------------------
class AppearancePanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[0], parent)
        self._owner = owner

        self._theme = _combo(
            [("Dark", "dark"), ("Light", "light")], prefs.get_str(prefs.THEME)
        )
        self._theme.currentIndexChanged.connect(self._on_theme)
        self._row("Theme", self._theme)

        self._anim = QCheckBox("Enable view transitions")
        self._anim.setChecked(prefs.get_bool(prefs.ANIMATIONS))
        self._anim.toggled.connect(self._on_anim)
        self._row(
            "Animations",
            self._anim,
            hint="Fade between the bookshelf and the reader.",
        )

        self._tile = _combo(
            [("Small", "small"), ("Medium", "medium"), ("Large", "large")],
            prefs.get_str(prefs.TILE_SIZE),
        )
        self._tile.currentIndexChanged.connect(self._on_tile)
        self._row("Bookshelf tile size", self._tile)

        self._sidebar = _combo(
            [("Collapsed", False), ("Expanded", True)],
            prefs.get_bool(prefs.SIDEBAR_EXPANDED),
        )
        self._sidebar.currentIndexChanged.connect(self._on_sidebar)
        self._row(
            "Sidebar on launch",
            self._sidebar,
            hint="Whether the left rail starts showing labels.",
        )

    def _on_theme(self) -> None:
        val = self._theme.currentData()
        prefs.set_value(prefs.THEME, val)
        self._owner.theme_changed.emit(val)

    def _on_anim(self, checked: bool) -> None:
        prefs.set_value(prefs.ANIMATIONS, checked)
        self._owner.animations_changed.emit(checked)

    def _on_tile(self) -> None:
        val = self._tile.currentData()
        prefs.set_value(prefs.TILE_SIZE, val)
        self._owner.tile_size_changed.emit(val)

    def _on_sidebar(self) -> None:
        val = bool(self._sidebar.currentData())
        prefs.set_value(prefs.SIDEBAR_EXPANDED, val)
        self._owner.sidebar_default_changed.emit(val)


# ----------------------------------------------------------------------------
# Reading defaults
# ----------------------------------------------------------------------------
class ReadingPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[1], parent)
        self._owner = owner

        self._fit = _combo(
            [("Fit page", "page"), ("Fit width", "width"), ("Actual size", "actual")],
            prefs.get_str(prefs.DEFAULT_FIT),
        )
        self._fit.currentIndexChanged.connect(
            lambda: self._save(prefs.DEFAULT_FIT, self._fit.currentData())
        )
        self._row("Default fit mode", self._fit)

        self._mode = _combo(
            [("Single page", "single"), ("Webtoon (vertical)", "webtoon")],
            prefs.get_str(prefs.DEFAULT_MODE),
        )
        self._mode.currentIndexChanged.connect(
            lambda: self._save(prefs.DEFAULT_MODE, self._mode.currentData())
        )
        self._row("Default reading mode", self._mode)

        self._spread = QCheckBox("Show two pages side by side")
        self._spread.setChecked(prefs.get_bool(prefs.DEFAULT_SPREAD))
        self._spread.toggled.connect(
            lambda v: self._save(prefs.DEFAULT_SPREAD, v)
        )
        self._row("Default spread", self._spread)

        self._zoom = _combo(
            [("50%", 0.5), ("75%", 0.75), ("100%", 1.0), ("125%", 1.25),
             ("150%", 1.5), ("200%", 2.0)],
            round(prefs.get_float(prefs.DEFAULT_ZOOM), 2),
        )
        self._zoom.currentIndexChanged.connect(
            lambda: self._save(prefs.DEFAULT_ZOOM, self._zoom.currentData())
        )
        self._row("Default zoom", self._zoom)

        self._dir = _combo(
            [("Left to right", False), ("Right to left (manga)", True)],
            prefs.get_bool(prefs.DEFAULT_RTL),
        )
        self._dir.currentIndexChanged.connect(
            lambda: self._save(prefs.DEFAULT_RTL, bool(self._dir.currentData()))
        )
        self._row("Default reading direction", self._dir)

        self._divider()

        self._click = QCheckBox("Tap left/right edges to turn pages")
        self._click.setChecked(prefs.get_bool(prefs.CLICK_NAV))
        self._click.toggled.connect(lambda v: self._save(prefs.CLICK_NAV, v))
        self._row("Click-zone navigation", self._click)

        self._page_anim = QCheckBox("Slide pages when turning")
        self._page_anim.setChecked(prefs.get_bool(prefs.PAGE_ANIM))
        self._page_anim.toggled.connect(lambda v: self._save(prefs.PAGE_ANIM, v))
        self._row("Page-turn animation", self._page_anim)

        self._preload = QSpinBox()
        self._preload.setRange(0, 20)
        self._preload.setValue(prefs.get_int(prefs.PRELOAD))
        self._preload.setSuffix(" pages")
        self._preload.valueChanged.connect(
            lambda v: self._save(prefs.PRELOAD, v)
        )
        self._row(
            "Pages to preload",
            self._preload,
            hint="How many upcoming pages to decode ahead of time.",
        )

    def _save(self, key: str, value) -> None:
        prefs.set_value(key, value)
        self._owner.reading_defaults_changed.emit()


# ----------------------------------------------------------------------------
# Ebook & Text
# ----------------------------------------------------------------------------
class EbookPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[2], parent)
        self._owner = owner

        self._size = QSpinBox()
        self._size.setRange(10, 48)
        self._size.setValue(prefs.get_int(prefs.EBOOK_FONT_PT))
        self._size.setSuffix(" pt")
        self._size.valueChanged.connect(self._on_change)
        self._row("Default font size", self._size)

        self._family = QFontComboBox()
        fam = prefs.get_str(prefs.EBOOK_FONT_FAMILY)
        if fam:
            self._family.setCurrentFont(QFont(fam))
        self._use_family = QCheckBox("Use a specific font family")
        self._use_family.setChecked(bool(fam))
        self._use_family.toggled.connect(self._on_change)
        self._family.currentFontChanged.connect(self._on_change)
        self._row(
            "Font family",
            self._use_family,
            hint="When off, text EPUBs use the app's default serif font.",
        )
        self._row("", self._family)

    def _on_change(self, *_args) -> None:
        prefs.set_value(prefs.EBOOK_FONT_PT, self._size.value())
        fam = self._family.currentFont().family() if self._use_family.isChecked() else ""
        prefs.set_value(prefs.EBOOK_FONT_FAMILY, fam)
        self._family.setEnabled(self._use_family.isChecked())
        self._owner.ebook_defaults_changed.emit()


# ----------------------------------------------------------------------------
# Library & Data
# ----------------------------------------------------------------------------
class LibraryPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[3], parent)
        self._owner = owner

        self._subheading("Folders")
        self._full(self._btn("Add folder to library…", "add_folder"))
        self._full(self._btn("Add files to library…", "add_files"))
        self._full(self._btn("Rescan all library folders", "rescan_all"))

        self._divider()
        self._subheading("Thumbnails")
        self._full(self._btn("Regenerate thumbnail cache", "regen_thumbs"))
        self._full(self._btn("Clear thumbnail cache", "clear_thumbs"))

        self._divider()
        self._subheading("Import / export")
        self._full(self._btn("Export library…", "export"))
        self._full(self._btn("Import library…", "import"))
        self._full(self._btn("Import shelf…", "import_shelf"))

        self._divider()
        self._subheading("Tools")
        self._full(self._btn("Scan for duplicates…", "duplicates"))
        self._full(self._btn("Reading statistics…", "stats"))

    def _btn(self, label: str, action: str) -> QPushButton:
        b = QPushButton(label)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self._owner.library_action.emit(action))
        return b


# ----------------------------------------------------------------------------
# Shortcuts
# ----------------------------------------------------------------------------
class ShortcutsPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[4], parent)
        self._owner = owner
        btn = QPushButton("Customize keyboard shortcuts…")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self._owner.shortcuts_requested.emit)
        self._full(btn)


# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
class SidebarPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[5], parent)
        note = QLabel(
            "The sidebar rail currently shows a fixed set of items (Library, "
            "Currently Reading, Search, Settings). Per-shelf visibility options "
            "will appear here once shelves can be pinned to the rail."
        )
        note.setWordWrap(True)
        note.setObjectName("hint")
        self._labels.append(note)
        self._full(note)


# ----------------------------------------------------------------------------
# About
# ----------------------------------------------------------------------------
class AboutPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[6], parent)

        self._name = QLabel(APP_DISPLAY_NAME)
        nf = QFont("Libre Baskerville")
        nf.setPixelSize(22)
        self._name.setFont(nf)
        self._labels.append(self._name)
        self._full(self._name)

        self._version = QLabel(f"Version {APP_VERSION}")
        self._labels.append(self._version)
        self._full(self._version)

        self._link = QLabel(
            f'<a href="{APP_REPO_URL}">{APP_REPO_URL}</a>'
        )
        self._link.setOpenExternalLinks(True)
        self._link.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._full(self._link)

        self._divider()
        self._blurb2 = QLabel(
            "100% offline. No accounts, no telemetry, no required cloud — your "
            "library stays on your machine."
        )
        self._blurb2.setWordWrap(True)
        self._blurb2.setObjectName("hint")
        self._labels.append(self._blurb2)
        self._full(self._blurb2)

    def on_theme(self, c: dict) -> None:
        self._link.setStyleSheet(
            f"color: {c['accent']}; background: transparent;"
        )


class SettingsView(QWidget):
    """Settings page: category list on the left, panel stack on the right."""

    back_requested = pyqtSignal()
    theme_changed = pyqtSignal(str)
    animations_changed = pyqtSignal(bool)
    tile_size_changed = pyqtSignal(str)
    sidebar_default_changed = pyqtSignal(bool)
    reading_defaults_changed = pyqtSignal()
    ebook_defaults_changed = pyqtSignal()
    library_action = pyqtSignal(str)
    shortcuts_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme: dict = themes.DARK
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ----- left category nav -----
        self._nav = QListWidget()
        self._nav.setFixedWidth(220)
        self._nav.setFrameShape(QListWidget.Shape.NoFrame)
        for title, _ in SECTIONS:
            self._nav.addItem(QListWidgetItem(title))
        self._nav.currentRowChanged.connect(self._on_row_changed)

        # ----- right side: header + panel stack -----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(52)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 0, 16, 0)
        header_layout.setSpacing(10)

        self._back_btn = QPushButton("‹ Library")
        self._back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_btn.clicked.connect(self.back_requested.emit)

        self._title = QLabel("Settings")
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(20)
        self._title.setFont(title_font)

        header_layout.addWidget(self._back_btn)
        header_layout.addSpacing(6)
        header_layout.addWidget(self._title)
        header_layout.addStretch(1)
        self._header = header

        from PyQt6.QtWidgets import QScrollArea

        self._panels = QStackedWidget()
        self._panel_widgets = [
            AppearancePanel(self),
            ReadingPanel(self),
            EbookPanel(self),
            LibraryPanel(self),
            ShortcutsPanel(self),
            SidebarPanel(self),
            AboutPanel(self),
        ]
        for panel in self._panel_widgets:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(panel)
            self._panels.addWidget(scroll)

        right_layout.addWidget(header)
        right_layout.addWidget(self._panels, 1)

        root.addWidget(self._nav)
        root.addWidget(right, 1)

        self._nav.setCurrentRow(0)

    # ----- navigation -----

    def _on_row_changed(self, row: int) -> None:
        if 0 <= row < self._panels.count():
            self._panels.setCurrentIndex(row)

    def reset(self) -> None:
        """Return to the first section — called each time the page is opened."""
        self._nav.setCurrentRow(0)

    # ----- theming -----

    def apply_theme(self, c: dict) -> None:
        self._theme = c
        self.setStyleSheet(f"QWidget {{ background: {c['app_bg']}; }}")
        self._nav.setStyleSheet(
            f"QListWidget {{ background: {c['sidebar_bg']}; border: none;"
            f" outline: none; padding: 8px 0; }}"
            f"QListWidget::item {{ color: {c['text']}; padding: 11px 18px;"
            f" border: none; }}"
            f"QListWidget::item:hover {{ background: rgba(255,255,255,0.06); }}"
            f"QListWidget::item:selected {{ background: rgba(255,255,255,0.13);"
            f" color: {c['text']}; }}"
        )
        self._header.setStyleSheet(
            f"background: {c['header_bg']}; border-bottom: 1px solid {c['border']};"
        )
        self._title.setStyleSheet(f"color: {c['text']}; background: transparent;")
        self._back_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text']}; background: transparent;"
            f" border: 1px solid {c['border']}; border-radius: 6px;"
            f" padding: 5px 12px; font-size: 14px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.08); }}"
        )
        for panel in self._panel_widgets:
            panel.apply_theme(c)
