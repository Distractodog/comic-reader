"""Dialog that shows a dictionary entry for a word."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
)

from dictionary_lookup import install_help_text, lookup, normalize_word


def _format_for_display(text: str) -> str:
    """Turn stored GCIDE blocks into readable plain text."""
    lines: list[str] = []
    for block in text.split("\n\n—\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("[") and "]\n" in block[:40]:
            close = block.index("]\n")
            pos = block[1:close].strip()
            rest = block[close + 2 :].strip()
            if pos:
                lines.append(pos.upper())
            if rest:
                lines.extend(rest.splitlines())
        else:
            lines.extend(block.splitlines())
        lines.append("")
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


class DictionaryDialog(QDialog):
    def __init__(self, word: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dictionary")
        self.setMinimumWidth(420)
        self.setMinimumHeight(280)

        word = normalize_word(word) or word.strip()
        layout = QVBoxLayout(self)

        heading = QLabel(f"<b>{word}</b>" if word else "<b>Dictionary</b>")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        layout.addWidget(body, 1)

        definition = lookup(word) if word else None
        if definition:
            body.setPlainText(_format_for_display(definition))
        else:
            body.setPlainText(
                f"No definition found for “{word}”.\n\n{install_help_text()}"
                if word
                else install_help_text()
            )

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
