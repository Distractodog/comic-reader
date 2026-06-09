"""Background worker that scans a folder for comic files and adds them to the library."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from archive_handler import open_comic
from comicinfo import parse_comicinfo
from epub_book import EpubBook, is_text_epub
from library import Library, enrich_series_metadata
from thumbnails import (
    generate_thumbnail,
    generate_thumbnail_from_bytes,
    thumbnail_path_for,
)

SCANNABLE_EXTENSIONS = {
    ".cbz", ".cbr", ".cb7", ".cbt", ".pdf", ".epub",
    ".zip", ".rar", ".7z", ".tar",
}


def _probe(path: Path) -> tuple[int, dict, bytes | None]:
    """Inspect a file → (page/chapter count, metadata dict, cover bytes or None).

    Text/novel EPUBs report their chapter count and OPF cover; everything else
    (comics, image EPUBs) reports image page count and ComicInfo metadata.
    """
    if path.suffix.lower() == ".epub" and is_text_epub(str(path)):
        with EpubBook(str(path)) as book:
            meta: dict = {}
            if book.title:
                meta["title"] = book.title
            if book.author:
                meta["author"] = book.author
            return book.chapter_count(), meta, book.cover_image_bytes()
    with open_comic(str(path)) as reader:
        page_count = reader.page_count()
    return page_count, parse_comicinfo(str(path)) or {}, None


def _write_cover(path: Path, comic_id: int, cover_bytes: bytes | None) -> str | None:
    """Write a cover thumbnail (from bytes if given, else page 0). Returns its path."""
    thumb = thumbnail_path_for(comic_id)
    if cover_bytes is not None:
        ok = generate_thumbnail_from_bytes(cover_bytes, thumb)
    else:
        ok = generate_thumbnail(str(path), thumb)
    return str(thumb) if ok else None


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

    def __init__(
        self,
        library: Library,
        folder: Path | None = None,
        paths: list[Path] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._library = library
        self._folder = folder
        self._paths = [Path(p) for p in paths] if paths else None
        if self._folder is None and not self._paths:
            raise ValueError("LibraryScanner requires folder or paths")
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result = ScanResult()

        if self._paths is not None:
            comic_paths = [
                p.resolve()
                for p in self._paths
                if p.is_file() and p.suffix.lower() in SCANNABLE_EXTENSIONS
            ]
        else:
            comic_paths = [
                p for p in self._folder.rglob("*")
                if p.is_file() and p.suffix.lower() in SCANNABLE_EXTENSIONS
            ]
        total = len(comic_paths)

        for i, path in enumerate(comic_paths):
            if self._cancelled:
                result.cancelled = True
                break

            self.progress.emit(i, total, path.name)

            if self._library.comic_exists(str(path)):
                result.skipped += 1
                existing = self._library.get_comic(str(path))
                if existing:
                    try:
                        page_count, meta, cover_bytes = _probe(path)
                        meta = enrich_series_metadata(str(path), meta)
                    except Exception:
                        continue
                    if existing.cover_path is None and not existing.cover_override:
                        cover = _write_cover(path, existing.id, cover_bytes)
                        if cover:
                            self._library.set_cover_path(existing.id, cover)
                    # Backfill metadata if not yet populated
                    if existing.title is None and existing.author is None and meta:
                        self._library.update_metadata(existing.id, **meta)
                    elif existing.series is None and meta.get("series"):
                        fields = {"series": meta["series"]}
                        if meta.get("series_number") is not None:
                            fields["series_number"] = meta["series_number"]
                        self._library.update_metadata(existing.id, **fields)
                    # Older scans stored text-EPUB page_count as image count (0/1);
                    # correct it to the real chapter count.
                    if page_count and existing.page_count != page_count:
                        self._library.update_metadata(existing.id, page_count=page_count)
                continue

            try:
                page_count, meta, cover_bytes = _probe(path)
                meta = enrich_series_metadata(str(path), meta)
                file_size = path.stat().st_size
                source_folder = (
                    str(path.parent)
                    if self._paths is not None
                    else str(self._folder)
                )
                comic_id = self._library.add_comic(
                    str(path),
                    page_count=page_count,
                    file_size=file_size,
                    source_folder=source_folder,
                    **meta,
                )
                result.added += 1
                self.comic_added.emit(comic_id)

                cover = _write_cover(path, comic_id, cover_bytes)
                if cover:
                    self._library.set_cover_path(comic_id, cover)
            except Exception as e:
                result.errors.append((str(path), str(e)))

        if not self._cancelled:
            if self._folder is not None:
                self._library.scan_series_in_folder(str(self._folder))
            elif self._paths:
                for parent in {str(p.parent) for p in self._paths}:
                    self._library.scan_series_in_folder(parent)

        self.finished.emit(result)
