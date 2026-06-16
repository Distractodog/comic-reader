"""Global user preferences, stored in QSettings (app-wide defaults).

These are the *base* defaults that sit beneath the per-comic / per-folder /
per-series ``ReadingSettings`` in the DB — those still win when present. Keys are
namespaced so everything lands in the one shared plist (see ``app_info``).

Typed getters/setters live here so every view reads and writes the same keys
with the same coercion (QSettings round-trips bools/ints as strings on some
platforms, so we coerce explicitly).
"""
from __future__ import annotations

from app_info import app_settings

# ----- keys -----
THEME = "appearance/theme"                  # 'dark' | 'light'
ANIMATIONS = "appearance/animations"        # bool — bookshelf<->reader fade
TILE_SIZE = "appearance/tile_size"          # 'small' | 'medium' | 'large'
SIDEBAR_EXPANDED = "appearance/sidebar_expanded"  # bool

DEFAULT_FIT = "reading/fit_mode"            # 'actual' | 'width' | 'page'
DEFAULT_MODE = "reading/reading_mode"       # 'single' | 'webtoon'
DEFAULT_SPREAD = "reading/spread"           # bool
DEFAULT_ZOOM = "reading/zoom"               # float
DEFAULT_RTL = "reading/rtl"                 # bool — right-to-left (manga)
CLICK_NAV = "reading/click_nav"             # bool
PAGE_ANIM = "reading/page_animation"        # bool — page-slide on turn
PRELOAD = "reading/preload_count"           # int — pages to preload ahead
WEBTOON_WIDTH = "reading/webtoon_width"     # int — webtoon page width %% (100/80/.../30)

EBOOK_FONT_PT = "ebook_font_pt"             # int (pre-existing key)
EBOOK_FONT_FAMILY = "ebook/font_family"     # str ('' = app default)

# ----- defaults -----
_DEFAULTS = {
    THEME: "dark",
    ANIMATIONS: True,
    TILE_SIZE: "medium",
    SIDEBAR_EXPANDED: False,
    DEFAULT_FIT: "page",
    DEFAULT_MODE: "single",
    DEFAULT_SPREAD: True,
    DEFAULT_ZOOM: 1.0,
    DEFAULT_RTL: False,
    CLICK_NAV: True,
    PAGE_ANIM: True,
    PRELOAD: 5,
    WEBTOON_WIDTH: 100,
    EBOOK_FONT_PT: 19,
    EBOOK_FONT_FAMILY: "",
}


def _coerce_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


def get_str(key: str) -> str:
    return str(app_settings().value(key, _DEFAULTS[key]))


def get_bool(key: str) -> bool:
    return _coerce_bool(app_settings().value(key, _DEFAULTS[key]))


def get_int(key: str) -> int:
    try:
        return int(app_settings().value(key, _DEFAULTS[key]))
    except (TypeError, ValueError):
        return int(_DEFAULTS[key])


def get_float(key: str) -> float:
    try:
        return float(app_settings().value(key, _DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(_DEFAULTS[key])


def set_value(key: str, value) -> None:
    app_settings().setValue(key, value)
