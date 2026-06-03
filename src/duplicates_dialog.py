"""Dialog that lists duplicate comics (same content) and lets the user hide copies."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from library import Library


class DuplicatesDialog(QDialog):
    """Shows groups of comics that share a content signature.

    Each group lists every copy with a "Hide this copy" button. Hiding never
    deletes from disk — it reuses the same soft-hide as the rest of the app.
    """

    changed = pyqtSignal()  # emitted when a copy is hidden, so the grid refreshes

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self.setWindowTitle("Duplicate Comics")
        self.resize(640, 480)

        outer = QVBoxLayout(self)

        self._intro = QLabel()
        self._intro.setWordWrap(True)
        outer.addWidget(self._intro)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        outer.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

        self._rebuild()

    def _rebuild(self):
        groups = self._library.find_duplicate_groups()

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        if not groups:
            self._intro.setText("No duplicate comics found.")
            layout.addStretch()
            self._scroll.setWidget(body)
            return

        n_copies = sum(len(g) for g in groups)
        self._intro.setText(
            f"Found {len(groups)} group(s) of duplicates ({n_copies} files). "
            "Hiding a copy removes it from the app but leaves the file on your disk; "
            "you can restore it any time from the Hidden view."
        )

        for group in groups:
            header = QLabel(f"{len(group)} copies — same content")
            header.setStyleSheet("font-weight: bold;")
            layout.addWidget(header)

            for comic in group:
                row = QHBoxLayout()
                name = comic.title or Path(comic.file_path).stem
                lbl = QLabel(f"{name}\n{comic.file_path}")
                lbl.setWordWrap(True)
                row.addWidget(lbl, 1)

                hide_btn = QPushButton("Hide this copy")
                hide_btn.clicked.connect(lambda _=False, cid=comic.id: self._hide(cid))
                row.addWidget(hide_btn, 0, Qt.AlignmentFlag.AlignTop)
                layout.addLayout(row)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            layout.addWidget(line)

        layout.addStretch()
        self._scroll.setWidget(body)

    def _hide(self, comic_id: int):
        self._library.set_hidden(comic_id, True)
        self.changed.emit()
        self._rebuild()
