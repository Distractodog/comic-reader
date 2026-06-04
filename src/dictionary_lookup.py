"""Offline dictionary lookups for the text EPUB reader (SQLite)."""

from __future__ import annotations

import re
import shutil
import sqlite3
import sys
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / relative


def dictionary_path() -> Path:
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
    )
    return base / "ComicReader" / "dictionary.db"


# Installed copies smaller than this are treated as the old starter dictionary.
_FULL_DICT_MIN_BYTES = 2_000_000
_DICT_FORMAT_VERSION = "2"


def _installed_format_version(path: Path) -> str | None:
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'format'"
            ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _needs_dictionary_upgrade(dest: Path) -> bool:
    if not dest.is_file():
        return True
    if dest.stat().st_size < _FULL_DICT_MIN_BYTES:
        return True
    return _installed_format_version(dest) != _DICT_FORMAT_VERSION


def install_dictionary_if_needed() -> None:
    """Copy the bundled full (or starter) dictionary on first use."""
    dest = dictionary_path()
    full = _resource_path("data/dictionary.db")
    if full.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _needs_dictionary_upgrade(dest):
            shutil.copy2(full, dest)
        return
    if dest.is_file():
        return
    seed = _resource_path("data/dictionary_seed.db")
    if seed.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed, dest)


def dictionary_available() -> bool:
    install_dictionary_if_needed()
    return dictionary_path().is_file()


def normalize_word(raw: str) -> str:
    word = raw.strip().lower()
    word = re.sub(r"^[^a-z0-9']+|[^a-z0-9']+$", "", word, flags=re.IGNORECASE)
    return word


def lookup(word: str) -> str | None:
    """Return a definition string, or None if the word is not in the database."""
    word = normalize_word(word)
    if len(word) < 2:
        return None
    install_dictionary_if_needed()
    path = dictionary_path()
    if not path.is_file():
        return None
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT definition FROM definitions WHERE word = ? COLLATE NOCASE",
            (word,),
        ).fetchone()
    return row[0] if row else None


def install_help_text() -> str:
    path = dictionary_path()
    return (
        "No dictionary database was found.\n\n"
        f"Expected file:\n{path}\n\n"
        "The file must be SQLite with a table:\n"
        "  definitions(word TEXT PRIMARY KEY, definition TEXT)\n\n"
        "The app normally installs a full offline dictionary (GCIDE) on "
        "first run. If you see this message, rebuild with "
        "scripts/build_dictionary.py or place your own SQLite file at the "
        "path above (table: definitions(word, definition))."
    )
