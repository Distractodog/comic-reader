"""Metadata editor dialog — single and multi-comic edit."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from library import Comic


class MetadataDialog(QDialog):
    MULTI = "(multiple values)"

    def __init__(
        self,
        comics: list[Comic],
        comic_tags: dict[int, list[str]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        n = len(comics)
        self.setWindowTitle("Edit Metadata" if n == 1 else f"Edit Metadata — {n} comics")
        self.setMinimumWidth(360)

        layout = QFormLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        self._fields: dict[str, QLineEdit] = {}
        self._originals: dict[str, str] = {}

        for key, label in [
            ("title",         "Title"),
            ("series",        "Series"),
            ("series_number", "Issue #"),
            ("author",        "Author"),
            ("publisher",     "Publisher"),
            ("year",          "Year"),
        ]:
            raw_vals = {getattr(c, key) for c in comics}
            shared = raw_vals.pop() if len(raw_vals) == 1 else None
            text = (str(shared) if shared is not None else "") if len({getattr(c, key) for c in comics}) == 1 else ""

            widget = QLineEdit()
            if len({getattr(c, key) for c in comics}) == 1:
                widget.setText(str(shared) if shared is not None else "")
                self._originals[key] = str(shared) if shared is not None else ""
            else:
                widget.setPlaceholderText(self.MULTI)
                self._originals[key] = self.MULTI

            self._fields[key] = widget
            layout.addRow(label + ":", widget)

        # Tags field
        self._tag_field = QLineEdit()
        self._tag_original = ""
        if comic_tags is not None:
            all_tag_sets = [frozenset(comic_tags.get(c.id, [])) for c in comics]
            if len(set(all_tag_sets)) == 1:
                shared_tags = sorted(all_tag_sets[0], key=str.lower)
                self._tag_original = ", ".join(shared_tags)
                self._tag_field.setText(self._tag_original)
            else:
                self._tag_field.setPlaceholderText(self.MULTI)
                self._tag_original = self.MULTI
        self._tag_field.setPlaceholderText(
            self._tag_field.placeholderText() or "e.g. Action, Classic, Sci-Fi"
        )
        layout.addRow("Tags:", self._tag_field)

        # Manga checkbox
        manga_vals = {getattr(c, "is_manga", False) for c in comics}
        self._manga_original: bool | None = manga_vals.pop() if len(manga_vals) == 1 else None
        self._manga_check = QCheckBox("Manga (right-to-left)")
        if self._manga_original is not None:
            self._manga_check.setChecked(self._manga_original)
        else:
            self._manga_check.setTristate(True)
            self._manga_check.setCheckState(Qt.CheckState.PartiallyChecked)
        layout.addRow(self._manga_check)

        if n > 1:
            note = QLabel(f"Filled fields apply to all {n} comics.\nLeave blank to keep existing values.")
            note.setWordWrap(True)
            note.setStyleSheet("color: rgba(255,255,255,0.45); font-size: 11px; padding-top: 4px;")
            layout.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self._changes: dict = {}

    def _on_accept(self):
        year_text = self._fields["year"].text().strip()
        snum_text = self._fields["series_number"].text().strip()

        if year_text and year_text != self.MULTI:
            try:
                int(year_text)
            except ValueError:
                QMessageBox.warning(self, "Invalid value", "Year must be a whole number.")
                return

        if snum_text and snum_text != self.MULTI:
            try:
                float(snum_text)
            except ValueError:
                QMessageBox.warning(self, "Invalid value", "Issue # must be a number (e.g. 1 or 1.5).")
                return

        self._changes = self._compute_changes()
        self.accept()

    def _compute_changes(self) -> dict:
        changes: dict = {}
        for key, widget in self._fields.items():
            text = widget.text().strip()
            orig = self._originals[key]

            if orig == self.MULTI:
                if not text or text == self.MULTI:
                    continue
            else:
                if text == orig:
                    continue

            if key == "year":
                changes[key] = int(text) if text else None
            elif key == "series_number":
                changes[key] = float(text) if text else None
            else:
                changes[key] = text if text else None

        # Tags
        tag_text = self._tag_field.text().strip()
        if self._tag_original == self.MULTI:
            if tag_text and tag_text != self.MULTI:
                changes["tags"] = [t.strip() for t in tag_text.split(",") if t.strip()]
        else:
            if tag_text != self._tag_original:
                changes["tags"] = [t.strip() for t in tag_text.split(",") if t.strip()]

        # Manga checkbox (only record a change if not in indeterminate state)
        check_state = self._manga_check.checkState()
        if check_state != Qt.CheckState.PartiallyChecked:
            new_manga = check_state == Qt.CheckState.Checked
            if self._manga_original is None or new_manga != self._manga_original:
                changes["is_manga"] = new_manga

        return changes

    def get_changes(self) -> dict:
        return self._changes
