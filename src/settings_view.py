"""Full-page Settings view.

A page in the main-window QStackedWidget (settings = index 4). Layout is a left
category list + a right stack of panels, one panel per section. Global defaults
are persisted via ``prefs`` (QSettings); per-comic/folder/series settings still
win over these. The view emits signals so the main window can apply changes live
(theme swap, tile reflow, library actions, etc.).
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFontComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import prefs
import themes
from app_info import APP_DISPLAY_NAME, APP_REPO_URL, APP_VERSION
from keybindings import ACTIONS, KeybindingManager

SECTIONS: list[tuple[str, str]] = [
    ("Appearance", "Theme, animations, tile size, and sidebar defaults."),
    ("Reading", "Default fit mode, reading mode, spread, zoom, and direction."),
    ("Ebook & Text", "Default font size and family for text EPUBs."),
    ("Library & Data", "Manage folders, the thumbnail cache, and import/export."),
    ("Shortcuts", "Customize keyboard shortcuts."),
    ("Sidebar", "Choose which views and shelves appear in the rail."),
    ("About", "App version and project info."),
]

_CONTENT_FONT_SCALE = 1.125


def _scaled_px(px: int) -> int:
    return round(px * _CONTENT_FONT_SCALE)


def _qt_value(value) -> int:
    return int(getattr(value, "value", value))


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
        outer.setSpacing(18)

        self._heading = QLabel(title)
        head_font = QFont("Libre Baskerville")
        head_font.setPixelSize(_scaled_px(20))
        self._heading.setFont(head_font)

        self._blurb = QLabel(blurb)
        self._blurb.setWordWrap(True)
        blurb_font = QFont()
        blurb_font.setPixelSize(_scaled_px(13))
        self._blurb.setFont(blurb_font)

        outer.addWidget(self._heading)
        outer.addWidget(self._blurb)
        outer.addSpacing(4)

        self.body = QVBoxLayout()
        self.body.setSpacing(28)
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
        lbl_font = QFont("Libre Baskerville")
        lbl_font.setPixelSize(_scaled_px(13))
        lbl.setFont(lbl_font)
        self._labels.append(lbl)
        left.addWidget(lbl)
        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setWordWrap(True)
            hint_font = QFont()
            hint_font.setPixelSize(_scaled_px(11))
            hint_lbl.setFont(hint_font)
            hint_lbl.setObjectName("hint")
            self._labels.append(hint_lbl)
            left.addWidget(hint_lbl)
        h.addLayout(left, 1)

        if isinstance(control, QCheckBox):
            self._checks.append(control)
            font = control.font()
            font.setPixelSize(_scaled_px(13))
            control.setFont(font)
        control.setMinimumWidth(180)
        if isinstance(control, (QComboBox, QFontComboBox, QSpinBox)):
            font = control.font()
            font.setPixelSize(_scaled_px(13))
            control.setFont(font)
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
        f.setPixelSize(_scaled_px(16))
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
        self._full(self._btn("Export shelf…", "export_shelf"))
        self._full(self._btn("Import shelf…", "import_shelf"))

        self._divider()
        self._subheading("Tools")
        self._full(self._btn("Scan for duplicates…", "duplicates"))
        self._full(self._btn("Reading statistics…", "stats"))

    def _btn(self, label: str, action: str) -> QPushButton:
        b = QPushButton(label)
        font = b.font()
        font.setPixelSize(_scaled_px(13))
        b.setFont(font)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda: self._owner.library_action.emit(action))
        return b


# ----------------------------------------------------------------------------
# Shortcuts
# ----------------------------------------------------------------------------
class _ShortcutButton(QPushButton):
    captured = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self._capturing = False
        self.clicked.connect(self._begin_capture)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _begin_capture(self) -> None:
        self._capturing = True
        self.setText("Press a key...")
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return
        seq = QKeySequence(
            _qt_value(event.modifiers()) | _qt_value(key)
        ).toString(QKeySequence.SequenceFormat.PortableText)
        if not seq:
            return
        self._capturing = False
        self.captured.emit(seq)


class ShortcutsPanel(_SettingsPanel):
    def __init__(self, owner: "SettingsView", parent=None):
        super().__init__(*SECTIONS[4], parent)
        self._owner = owner
        self._manager = KeybindingManager()
        self._buttons: dict[str, _ShortcutButton] = {}

        for action_id, meta in ACTIONS.items():
            btn = _ShortcutButton(self._manager.get(action_id))
            font = btn.font()
            font.setPixelSize(_scaled_px(13))
            btn.setFont(font)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.captured.connect(lambda seq, aid=action_id: self._set_shortcut(aid, seq))
            self._buttons[action_id] = btn
            extras = meta.get("extras", [])
            hint = f"Also: {', '.join(extras)}" if extras else ""
            self._row(meta["label"], btn, hint=hint)

        self._divider()
        reset = QPushButton("Reset all to defaults")
        font = reset.font()
        font.setPixelSize(_scaled_px(13))
        reset.setFont(font)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(self._reset_all)
        self._full(reset)

    def _set_shortcut(self, action_id: str, seq: str) -> None:
        current = self._manager.all_shortcuts()
        for other_id, other_seq in current.items():
            if other_id == action_id or other_seq != seq:
                continue
            result = QMessageBox.question(
                self,
                "Shortcut conflict",
                f'"{seq}" is already used by "{ACTIONS[other_id]["label"]}".\n\nReplace it?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if result != QMessageBox.StandardButton.Yes:
                self._buttons[action_id].setText(current[action_id])
                return
            default = ACTIONS[other_id]["default"]
            self._manager.set(other_id, default)
            self._buttons[other_id].setText(default)
            break

        self._manager.set(action_id, seq)
        self._buttons[action_id].setText(seq)
        self._owner.shortcut_changed.emit()

    def _reset_all(self) -> None:
        for action_id, meta in ACTIONS.items():
            shortcut = meta["default"]
            self._manager.set(action_id, shortcut)
            self._buttons[action_id].setText(shortcut)
        self._owner.shortcut_changed.emit()


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
        nf.setPixelSize(_scaled_px(22))
        self._name.setFont(nf)
        self._labels.append(self._name)
        self._full(self._name)

        self._version = QLabel(f"Version {APP_VERSION}")
        version_font = QFont()
        version_font.setPixelSize(_scaled_px(13))
        self._version.setFont(version_font)
        self._labels.append(self._version)
        self._full(self._version)

        self._link = QLabel(
            f'<a href="{APP_REPO_URL}">{APP_REPO_URL}</a>'
        )
        link_font = QFont()
        link_font.setPixelSize(_scaled_px(13))
        self._link.setFont(link_font)
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
    """Settings page: horizontal tab bar at the top, swipeable panel below."""

    back_requested = pyqtSignal()
    theme_changed = pyqtSignal(str)
    animations_changed = pyqtSignal(bool)
    tile_size_changed = pyqtSignal(str)
    sidebar_default_changed = pyqtSignal(bool)
    reading_defaults_changed = pyqtSignal()
    ebook_defaults_changed = pyqtSignal()
    library_action = pyqtSignal(str)
    shortcuts_requested = pyqtSignal()
    shortcut_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme: dict = themes.DARK
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("SettingsRoot")

        # A random bookshelf background, dimmed, sits behind the content (the
        # settings page has no comic tiles of its own). Source image is supplied
        # by the main window each time the page is opened; rendered here at the
        # current size. Stays behind everything and ignores mouse events.
        self._bg_source: QImage | None = None
        self._bg_label = QLabel(self)
        self._bg_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bg_label.setScaledContents(False)
        self._bg_label.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ----- header -----
        header = QWidget()
        header.setObjectName("SettingsHeader")
        header.setFixedHeight(60)
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(30, 0, 16, 0)
        header_layout.setSpacing(10)

        self._title = QLabel("Settings")
        title_font = QFont("Libre Baskerville")
        title_font.setPixelSize(20)
        self._title.setFont(title_font)

        header_layout.addWidget(self._title)
        header_layout.addStretch(1)
        self._header = header

        # ----- horizontal tab bar -----
        self._tab_bar = QWidget()
        self._tab_bar.setObjectName("SettingsTabBar")
        self._tab_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tab_bar.setFixedHeight(60)
        tab_layout = QHBoxLayout(self._tab_bar)
        tab_layout.setContentsMargins(16, 0, 16, 0)
        tab_layout.setSpacing(2)
        self._tab_btns: list[QPushButton] = []
        for i, (title, _) in enumerate(SECTIONS):
            btn = QPushButton(title)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._go_to_section(idx))
            tab_layout.addWidget(btn)
            self._tab_btns.append(btn)
        tab_layout.addStretch(1)

        # ----- panel stack -----
        self._panels = QStackedWidget()
        self._panels.setObjectName("SettingsStack")
        self._panel_widgets = [
            AppearancePanel(self),
            ReadingPanel(self),
            EbookPanel(self),
            LibraryPanel(self),
            ShortcutsPanel(self),
            SidebarPanel(self),
            AboutPanel(self),
        ]
        self._scroll_viewports: set = set()
        for panel in self._panel_widgets:
            panel.setObjectName("SettingsPanel")
            panel.setMaximumWidth(720)

            # Center the fixed-width panel inside the full-width scroll area.
            wrapper = QWidget()
            wrapper.setObjectName("SettingsPanelWrapper")
            wl = QHBoxLayout(wrapper)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(0)
            wl.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)
            wl.addStretch(1)

            scroll = QScrollArea()
            scroll.setObjectName("SettingsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(wrapper)
            scroll.viewport().setAutoFillBackground(False)
            self._panels.addWidget(scroll)
            # Install on both scroll area and viewport to catch wheel events
            scroll.installEventFilter(self)
            scroll.viewport().installEventFilter(self)
            self._scroll_viewports.add(scroll.viewport())
            self._scroll_viewports.add(scroll)

        root.addWidget(header)
        root.addWidget(self._tab_bar)
        root.addWidget(self._panels, 1)

        self._current_section = 0
        self._swipe_start: QPoint | None = None
        self._swiping = False
        # Accumulate horizontal trackpad delta for swipe-between-sections
        self._swipe_dx: float = 0.0
        self._swipe_reset = QTimer(self)
        self._swipe_reset.setSingleShot(True)
        self._swipe_reset.setInterval(180)
        self._swipe_reset.timeout.connect(lambda: setattr(self, "_swipe_dx", 0.0))
        self._go_to_section(0)

    # ----- navigation -----

    def _go_to_section(self, idx: int) -> None:
        idx = max(0, min(idx, len(self._panel_widgets) - 1))
        self._current_section = idx
        self._panels.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            btn.setChecked(i == idx)

    def _go_next(self) -> None:
        self._go_to_section(self._current_section + 1)

    def _go_prev(self) -> None:
        self._go_to_section(self._current_section - 1)

    def reset(self) -> None:
        """Return to the first section — called each time the page is opened."""
        self._go_to_section(0)

    # ----- theming -----

    def apply_theme(self, c: dict) -> None:
        self._theme = c
        self.setStyleSheet(
            f"#SettingsRoot {{ background: {c['app_bg']}; }}"
            f"#SettingsStack, #SettingsScroll, #SettingsPanelWrapper, #SettingsPanel"
            f" {{ background: transparent; }}"
            f"#SettingsScroll > QWidget {{ background: transparent; }}"
            f"#SettingsPanel .QWidget {{ background: transparent; }}"
            f"#SettingsPanel QComboBox, #SettingsPanel QFontComboBox,"
            f" #SettingsPanel QSpinBox {{ background: {c['input_bg']};"
            f" color: {c['text']}; border: 1px solid {c['border']};"
            f" border-radius: 4px; padding: 4px 8px;"
            f" font-size: {_scaled_px(13)}px; }}"
            f"#SettingsPanel QPushButton {{ font-size: {_scaled_px(13)}px; }}"
        )
        self._render_background()
        hbg = QColor(c["header_bg"])
        header_semi = f"rgba({hbg.red()},{hbg.green()},{hbg.blue()},180)"
        self._header.setStyleSheet(
            f"#SettingsHeader {{ background: {header_semi};"
            f" border-bottom: 1px solid {c['border']}; }}"
        )
        self._title.setStyleSheet(f"color: {c['text']}; background: transparent;")
        self._tab_bar.setStyleSheet(
            f"#SettingsTabBar {{ background: {header_semi}; }}"
            f"QPushButton {{ color: {c['text_secondary']}; background: transparent;"
            f" border: none; border-bottom: 2px solid transparent;"
            f" border-radius: 0; padding: 10px 14px; font-size: 13px; }}"
            f"QPushButton:checked {{ color: {c['text']};"
            f" border-bottom: 2px solid {c['accent']}; }}"
            f"QPushButton:hover:!checked {{ color: {c['text']};"
            f" background: rgba(255,255,255,0.06); }}"
        )
        for panel in self._panel_widgets:
            panel.apply_theme(c)

    # ----- random backdrop -----

    def set_background_image(self, img: QImage | None) -> None:
        """Set (or clear) the dimmed backdrop. The main window supplies a fresh
        random bookshelf background each time the settings page is opened."""
        self._bg_source = img if (img is not None and not img.isNull()) else None
        self._render_background()

    def _render_background(self) -> None:
        w, h = self.width(), self.height()
        if self._bg_source is None or w <= 0 or h <= 0:
            self._bg_label.hide()
            return
        dpr = self.devicePixelRatioF() or 1.0
        pw, ph = max(1, int(w * dpr)), max(1, int(h * dpr))
        src = self._bg_source
        sw, sh = src.width(), src.height()
        if sw <= 0 or sh <= 0:
            self._bg_label.hide()
            return
        # Exact "cover" scale: fill the view, crop overflow from the center, then
        # dim heavily toward the theme background so it sits quietly behind the UI.
        scale = max(pw / sw, ph / sh)
        scaled = src.scaled(
            max(1, int(sw * scale)), max(1, int(sh * scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pix = QPixmap(pw, ph)
        pix.fill(QColor(self._theme["app_bg"]))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(
            (pw - scaled.width()) // 2, (ph - scaled.height()) // 2, scaled
        )
        overlay = QColor(self._theme["app_bg"])
        overlay.setAlpha(198)
        painter.fillRect(pix.rect(), overlay)
        painter.end()
        pix.setDevicePixelRatio(dpr)
        self._bg_label.setGeometry(0, 0, w, h)
        self._bg_label.setPixmap(pix)
        self._bg_label.lower()
        self._bg_label.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_background()

    def showEvent(self, event):
        super().showEvent(event)
        self._render_background()

    # ----- swipe to navigate -----

    def eventFilter(self, obj, event) -> bool:
        if obj not in self._scroll_viewports:
            return super().eventFilter(obj, event)
        t = event.type()

        # Trackpad two-finger horizontal swipe → navigate sections.
        # Accumulate pixel delta; once it crosses the threshold, switch once and reset.
        if t == QEvent.Type.Wheel:
            dx = event.pixelDelta().x()
            dy = event.pixelDelta().y()
            if abs(dx) > abs(dy):
                self._swipe_reset.start()
                self._swipe_dx += dx
                if self._swipe_dx < -80:
                    self._swipe_dx = 0.0
                    self._go_next()
                elif self._swipe_dx > 80:
                    self._swipe_dx = 0.0
                    self._go_prev()
                return True  # consume so the scroll area doesn't scroll horizontally

        # Mouse-drag swipe (click-and-drag left/right on the panel).
        if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._swipe_start = QPoint(int(event.position().x()), int(event.position().y()))
            self._swiping = False
            return False
        if t == QEvent.Type.MouseMove and self._swipe_start is not None:
            dx = abs(int(event.position().x()) - self._swipe_start.x())
            dy = abs(int(event.position().y()) - self._swipe_start.y())
            if not self._swiping and dx > dy and dx > 20:
                self._swiping = True
            return self._swiping
        if t == QEvent.Type.MouseButtonRelease and self._swipe_start is not None:
            was_swiping = self._swiping
            dx = int(event.position().x()) - self._swipe_start.x()
            self._swipe_start = None
            self._swiping = False
            if was_swiping:
                if dx < -60:
                    self._go_next()
                elif dx > 60:
                    self._go_prev()
                return True
        return False
