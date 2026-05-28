"""Keyboard shortcut configuration and customization dialog."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

# action_id → {label, default primary shortcut, fixed secondary shortcuts}
ACTIONS: dict[str, dict] = {
    "next_page":        {"label": "Next Page",            "default": "Right",   "extras": ["Space", "PgDown"]},
    "prev_page":        {"label": "Previous Page",         "default": "Left",    "extras": ["Backspace", "PgUp"]},
    "first_page":       {"label": "First Page",            "default": "Home",    "extras": []},
    "last_page":        {"label": "Last Page",             "default": "End",     "extras": []},
    "fit_page":         {"label": "Fit Page",              "default": "1",       "extras": []},
    "fit_width":        {"label": "Fit Width",             "default": "2",       "extras": []},
    "actual_size":      {"label": "Actual Size",           "default": "3",       "extras": []},
    "zoom_in":          {"label": "Zoom In",               "default": "Ctrl++",  "extras": ["Ctrl+="]},
    "zoom_out":         {"label": "Zoom Out",              "default": "Ctrl+-",  "extras": []},
    "fullscreen":       {"label": "Fullscreen",            "default": "F11",     "extras": []},
    "back_to_library":  {"label": "Back to Library",       "default": "Escape",  "extras": []},
    "bookmark":         {"label": "Toggle Bookmark",       "default": "B",       "extras": []},
    "prev_bookmark":    {"label": "Previous Bookmark",     "default": "[",       "extras": []},
    "next_bookmark":    {"label": "Next Bookmark",         "default": "]",       "extras": []},
    "thumbnail_strip":  {"label": "Toggle Thumbnail Strip","default": "T",       "extras": []},
}


def _config_path() -> Path:
    from PyQt6.QtCore import QStandardPaths
    base = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / "keybindings.json"


class KeybindingManager:
    """Loads and saves primary keyboard shortcuts for each action."""

    def __init__(self):
        self._bindings: dict[str, str] = {k: v["default"] for k, v in ACTIONS.items()}
        self._load()

    def get(self, action_id: str) -> str:
        return self._bindings.get(action_id, ACTIONS[action_id]["default"])

    def set(self, action_id: str, shortcut: str) -> None:
        self._bindings[action_id] = shortcut
        self._save()

    def all_shortcuts(self) -> dict[str, str]:
        return dict(self._bindings)

    def _load(self) -> None:
        path = _config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for k, v in data.items():
                    if k in ACTIONS and isinstance(v, str):
                        self._bindings[k] = v
            except Exception:
                pass

    def _save(self) -> None:
        try:
            _config_path().write_text(json.dumps(self._bindings, indent=2))
        except Exception:
            pass


class KeybindingDialog(QDialog):
    """Table dialog for viewing and reassigning keyboard shortcuts."""

    def __init__(self, manager: KeybindingManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customize Shortcuts")
        self.setMinimumWidth(400)
        self._manager = manager
        self._pending: dict[str, str] = manager.all_shortcuts()
        self._capturing: int | None = None  # row being captured

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        hint = QLabel("Click a shortcut cell, then press a new key combination.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px;")
        layout.addWidget(hint)

        self._table = QTableWidget(len(ACTIONS), 2, self)
        self._table.setHorizontalHeaderLabels(["Action", "Shortcut"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().hide()
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.cellClicked.connect(self._on_cell_clicked)

        for row, (action_id, meta) in enumerate(ACTIONS.items()):
            label_item = QTableWidgetItem(meta["label"])
            label_item.setData(Qt.ItemDataRole.UserRole, action_id)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, label_item)

            key_item = QTableWidgetItem(self._pending.get(action_id, meta["default"]))
            key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, key_item)

        layout.addWidget(self._table)

        reset_btn = QPushButton("Reset All to Defaults")
        reset_btn.clicked.connect(self._reset_all)
        layout.addWidget(reset_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_cell_clicked(self, row: int, col: int) -> None:
        self._capturing = row
        self._table.item(row, 1).setText("Press a key…")
        self._table.setFocus()

    def keyPressEvent(self, event) -> None:
        if self._capturing is None:
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()

        # Ignore bare modifiers
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        # Build the key sequence string
        seq = QKeySequence(int(mods) | key).toString(QKeySequence.SequenceFormat.PortableText)
        if not seq:
            return

        row = self._capturing
        self._capturing = None

        # Conflict check
        action_ids = list(ACTIONS.keys())
        for r, (aid, _) in enumerate(ACTIONS.items()):
            if r != row and self._pending.get(aid) == seq:
                label_name = ACTIONS[aid]["label"]
                result = QMessageBox.question(
                    self,
                    "Shortcut conflict",
                    f'"{seq}" is already used by "{label_name}".\n\nReplace it?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                )
                if result != QMessageBox.StandardButton.Yes:
                    self._table.item(row, 1).setText(self._pending.get(action_ids[row], ""))
                    return
                # Clear the conflicting row
                default = ACTIONS[aid]["default"]
                self._pending[aid] = default
                self._table.item(r, 1).setText(default)
                break

        action_id = list(ACTIONS.keys())[row]
        self._pending[action_id] = seq
        self._table.item(row, 1).setText(seq)

    def _reset_all(self) -> None:
        self._pending = {k: v["default"] for k, v in ACTIONS.items()}
        for row, (action_id, meta) in enumerate(ACTIONS.items()):
            self._table.item(row, 1).setText(meta["default"])

    def _on_accept(self) -> None:
        for action_id, shortcut in self._pending.items():
            self._manager.set(action_id, shortcut)
        self.accept()
