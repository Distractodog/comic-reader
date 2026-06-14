"""Thumbnail generation and caching for comic cover images."""

from __future__ import annotations

import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QStandardPaths
from PyQt6.QtGui import QImage

from archive_handler import open_comic

THUMB_MAX_WIDTH = 400
THUMB_MAX_HEIGHT = 600
THUMB_QUALITY = 85


def _thumbnail_cache_base() -> Path:
    """Persistent store for cover thumbnails, folder covers, and override covers.

    Lives in AppDataLocation (alongside the library DB) — NOT CacheLocation, which
    macOS can purge on low disk or across reboots. Keeping covers here is what lets
    folder covers and bookshelf backgrounds survive restarts.
    """
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
    )
    return base / "covers"


def _legacy_thumbnail_cache_base() -> Path:
    """The old (purgeable) cache location covers used to live in."""
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
    )
    return base / "thumbnails"


def cover_store_base() -> str:
    return str(_thumbnail_cache_base())


def legacy_cover_base() -> str:
    return str(_legacy_thumbnail_cache_base())


def migrate_cover_store() -> None:
    """Move cover files out of the old purgeable cache into persistent storage.

    Idempotent: only moves files that don't already exist at the destination, and
    does nothing once the old directory is gone.
    """
    old = _legacy_thumbnail_cache_base()
    new = _thumbnail_cache_base()
    new.mkdir(parents=True, exist_ok=True)
    if not old.exists() or old == new:
        return
    try:
        for f in old.iterdir():
            if not f.is_file():
                continue
            target = new / f.name
            if target.exists():
                continue
            try:
                shutil.move(str(f), str(target))
            except OSError:
                pass
    except OSError:
        pass


def thumbnail_cache_dir() -> Path:
    d = _thumbnail_cache_base()
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbnail_path_for(comic_id: int) -> Path:
    return _thumbnail_cache_base() / f"{comic_id}.jpg"


def folder_cover_path_for(folder_path: str) -> Path:
    """Stable cache path for a custom folder cover, keyed by the folder's path."""
    import hashlib
    h = hashlib.md5(folder_path.encode("utf-8")).hexdigest()[:16]
    return _thumbnail_cache_base() / f"folder_{h}.jpg"


def comic_cover_override_path_for(comic_id: int) -> Path:
    """Cache path for a comic's manual cover override.

    Kept distinct from the auto thumbnail (``{id}.jpg``) so resetting to the
    default cover doesn't have to clobber the override file.
    """
    return _thumbnail_cache_base() / f"cover_override_{comic_id}.jpg"


def generate_thumbnail_from_bytes(page_bytes: bytes, output_path: Path) -> bool:
    """Scale raw image bytes (e.g. one comic page) into a JPEG cover. True on success."""
    try:
        image = QImage.fromData(page_bytes)
        if image.isNull():
            return False
        thumb = image.scaled(
            THUMB_MAX_WIDTH,
            THUMB_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return thumb.save(str(output_path), "JPEG", THUMB_QUALITY)
    except Exception:
        return False


def generate_thumbnail_from_image(image_path: str, output_path: Path) -> bool:
    """Scale an arbitrary image file into a JPEG cover thumbnail. Returns True on success."""
    try:
        image = QImage(image_path)
        if image.isNull():
            return False
        thumb = image.scaled(
            THUMB_MAX_WIDTH,
            THUMB_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return thumb.save(str(output_path), "JPEG", THUMB_QUALITY)
    except Exception:
        return False


def generate_thumbnail(file_path: str, output_path: Path) -> bool:
    """Open comic, render first page to a JPEG thumbnail. Returns True on success."""
    try:
        with open_comic(file_path) as reader:
            if reader.page_count() == 0:
                return False
            page_bytes = reader.get_page_bytes(0)

        image = QImage.fromData(page_bytes)
        if image.isNull():
            return False

        thumb = image.scaled(
            THUMB_MAX_WIDTH,
            THUMB_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return thumb.save(str(output_path), "JPEG", THUMB_QUALITY)
    except Exception:
        return False
