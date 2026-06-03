"""Background worker that computes content signatures so duplicates can be found."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from library import Library, compute_content_signature


@dataclass
class DedupeResult:
    hashed: int = 0
    errors: int = 0
    cancelled: bool = False


class DuplicateScanner(QObject):
    """Hashes every visible comic that lacks a content signature.

    Runs on a background QThread (same pattern as LibraryScanner). The actual
    grouping is done afterward by Library.find_duplicate_groups().
    """

    progress = pyqtSignal(int, int, str)  # current, total, filename
    finished = pyqtSignal(object)         # DedupeResult

    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result = DedupeResult()
        comics = self._library.get_unhashed_comics()
        total = len(comics)
        from pathlib import Path

        for i, comic in enumerate(comics):
            if self._cancelled:
                result.cancelled = True
                break
            self.progress.emit(i, total, Path(comic.file_path).name)
            try:
                sig = compute_content_signature(comic.file_path)
                self._library.set_content_hash(comic.id, sig)
                result.hashed += 1
            except Exception:
                # Missing/unreadable file (e.g. synced away) — skip, don't crash.
                result.errors += 1

        self.finished.emit(result)
