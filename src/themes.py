"""Light and dark palettes and the shared app-level stylesheet generator."""
from __future__ import annotations

LIGHT: dict = {
    "app_bg":           "#faf4f4",  # content area — near white with warmth
    "sidebar_bg":       "#b89898",  # sidebar — distinctly darker rose-grey
    "header_bg":        "#b89898",  # header bar — matches sidebar (unified chrome)
    "border":           "#a88080",
    "accent":           "#7a2020",
    "text":             "#2a1818",
    "text_secondary":   "#6a4040",
    "tile_bg":          "#faf4f4",
    "cover_bg":         "#e4d4d4",
    "hover_overlay":    (100, 30, 30, 25),
    "progress_track":   "#c8aaaa",
    "progress_fill":    "#7a2020",
    "placeholder_fg":   "#c0a8a8",
    "hover_bg":         "#c0a0a0",
    "btn_color":        "#faf4f4",   # light text on darker sidebar
    "btn_hover_bg":     "#a07878",
    "btn_hover_color":  "#ffffff",
    "btn_pressed":      "#7a2020",
    "btn_disabled":     "#c8b0b0",
    "logo_bg":          "#7a2020",
    "logo_color":       "#ffffff",
    "reader_bar_bg":    "#b89898",
    "scrollbar_bg":     "#b89898",
    "scrollbar_handle": "#906060",
    "scrollbar_hover":  "#7a4848",
    "input_bg":         "#ffffff",
    "selection_bg":     "#7a2020",
}

DARK: dict = {
    "app_bg":           "#2e2626",  # content area — medium dark warm
    "sidebar_bg":       "#171212",  # sidebar — distinctly darker
    "header_bg":        "#171212",  # header bar — matches sidebar
    "border":           "#3e2e2e",
    "accent":           "#c06060",
    "text":             "#e8dede",
    "text_secondary":   "#9a7878",
    "tile_bg":          "#2e2626",
    "cover_bg":         "#3c2e2e",
    "hover_overlay":    (220, 150, 150, 22),
    "progress_track":   "#4a3535",
    "progress_fill":    "#c06060",
    "placeholder_fg":   "#5a4545",
    "hover_bg":         "#3c2e2e",
    "btn_color":        "#a08080",
    "btn_hover_bg":     "#2e2626",
    "btn_hover_color":  "#e8dede",
    "btn_pressed":      "#c06060",
    "btn_disabled":     "#3e3030",
    "logo_bg":          "#7a2020",
    "logo_color":       "#ffffff",
    "reader_bar_bg":    "#171212",
    "scrollbar_bg":     "#171212",
    "scrollbar_handle": "#3e2e2e",
    "scrollbar_hover":  "#4e3e3e",
    "input_bg":         "#201a1a",
    "selection_bg":     "#c06060",
}


def app_stylesheet(c: dict) -> str:
    return (
        f"QMainWindow, QWidget {{ background-color: {c['app_bg']}; color: {c['text']};"
        f" font-family: 'Libre Baskerville'; }}"
        f"QMenuBar {{ background: {c['header_bg']}; color: {c['text']};"
        f" border-bottom: 1px solid {c['border']}; }}"
        f"QMenuBar::item {{ background: transparent; padding: 4px 10px; }}"
        f"QMenuBar::item:selected {{ background: {c['hover_bg']}; color: {c['text']}; }}"
        f"QMenu {{ background: {c['app_bg']}; color: {c['text']}; border: 1px solid {c['border']}; }}"
        f"QMenu::item {{ padding: 5px 24px; }}"
        f"QMenu::item:selected {{ background: {c['hover_bg']}; }}"
        f"QMenu::separator {{ height: 1px; background: {c['border']}; margin: 2px 0; }}"
        f"QStatusBar {{ background: {c['sidebar_bg']}; color: {c['text_secondary']};"
        f" border-top: 1px solid {c['border']}; }}"
        f"QScrollBar:vertical {{ background: {c['scrollbar_bg']}; width: 5px; border: none; margin: 0; }}"
        f"QScrollBar::handle:vertical {{ background: {c['scrollbar_handle']};"
        f" border-radius: 2px; min-height: 30px; }}"
        f"QScrollBar::handle:vertical:hover {{ background: {c['scrollbar_hover']}; }}"
        f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}"
        f"QToolTip {{ background: {c['app_bg']}; color: {c['text']};"
        f" border: 1px solid {c['border']}; padding: 4px 6px; }}"
        f"QPushButton {{ background: {c['sidebar_bg']}; color: {c['text']}; border: 1px solid {c['border']};"
        f" border-radius: 4px; padding: 6px 14px; }}"
        f"QPushButton:hover {{ background: {c['hover_bg']}; color: {c['text']};"
        f" border-color: {c['text_secondary']}; }}"
        f"QPushButton:pressed {{ background: {c['border']}; }}"
        f"QLineEdit {{ background: {c['input_bg']}; color: {c['text']}; border: 1px solid {c['border']};"
        f" border-radius: 4px; padding: 4px 8px; selection-background-color: {c['selection_bg']}; }}"
        f"QLineEdit:focus {{ border-color: {c['accent']}; }}"
        f"QComboBox {{ background: {c['input_bg']}; color: {c['text']}; border: 1px solid {c['border']};"
        f" border-radius: 4px; padding: 4px 8px; }}"
        f"QComboBox::drop-down {{ border: none; width: 20px; }}"
        f"QComboBox QAbstractItemView {{ background: {c['app_bg']}; color: {c['text']};"
        f" selection-background-color: {c['hover_bg']}; border: 1px solid {c['border']}; outline: none; }}"
        f"QProgressDialog QLabel {{ color: {c['text']}; }}"
        f"QMessageBox QLabel {{ color: {c['text']}; }}"
    )
