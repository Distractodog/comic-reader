"""Background worker that scans a folder for comic files and adds them to the library."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from archive_handler import open_comic
from comicinfo import parse_comicinfo
from library import Library
from thumbnails import generate_thumbnail, thumbnail_path_for

SCANNABLE_EXTENSIONS = {
    ".cbz", ".cbr", ".cb7", ".cbt", ".pdf", ".epub",
    ".zip", ".rar", ".7z", ".tar",
}


@dataclass
class ScanResult:
    added: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    cancelled: bool = False


class LibraryScanner(QObject):
    progress = pyqtSignal(int, int, str)  # current, total, filename
    comic_added = pyqtSignal(int)         # comic_id
    finished = pyqtSignal(object)         # ScanResult

    def __init__(self, library: Library, folder: Path, parent=None):
        super().__init__(parent)
        self._library = library
        self._folder = folder
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result = ScanResult()

        # Pass 1: collect all comic file paths (fast — no file opening)
        comic_paths = [
            p for p in self._folder.rglob("*")
            if p.is_file() and p.suffix.lower() in SCANNABLE_EXTENSIONS
        ]
        total = len(comic_paths)

        # Pass 2: open each file, read page count, insert into library
        for i, path in enumerate(comic_paths):
            if self._cancelled:
                result.cancelled = True
                break

            self.progress.emit(i, total, path.name)

            if self._library.comic_exists(str(path)):
                result.skipped += 1
                existing = self._library.get_comic(str(path))
                if existing:
                    if existing.cover_path is None and not existing.cover_override:
                        thumb_path = thumbnail_path_for(existing.id)
                        if generate_thumbnail(str(path), thumb_path):
                            self._library.set_cover_path(existing.id, str(thumb_path))
                    # Backfill ComicInfo metadata if not yet populated
                    if existing.title is None and existing.author is None:
                        meta = parse_comicinfo(str(path))
                        if meta:
                            self._library.update_metadata(existing.id, **meta)
                continue

            try:
                with open_comic(str(path)) as reader:
                    page_count = reader.page_count()
                file_size = path.stat().st_size
                meta = parse_comicinfo(str(path)) or {}
                comic_id = self._library.add_comic(
                    str(path),
                    page_count=page_count,
                    file_size=file_size,
                    source_folder=str(self._folder),
                    **meta,
                )
                result.added += 1
                self.comic_added.emit(comic_id)

                thumb_path = thumbnail_path_for(comic_id)
                if generate_thumbnail(str(path), thumb_path):
                    self._library.set_cover_path(comic_id, str(thumb_path))
            except Exception as e:
                result.errors.append((str(path), str(e)))

        self.finished.emit(result)
