"""Thumbnail generation and caching for comic cover images."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QStandardPaths
from PyQt6.QtGui import QImage

from archive_handler import open_comic

THUMB_MAX_WIDTH = 400
THUMB_MAX_HEIGHT = 600
THUMB_QUALITY = 85


def thumbnail_cache_dir() -> Path:
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
    )
    d = base / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbnail_path_for(comic_id: int) -> Path:
    return thumbnail_cache_dir() / f"{comic_id}.jpg"


def folder_cover_path_for(folder_path: str) -> Path:
    """Stable cache path for a custom folder cover, keyed by the folder's path."""
    import hashlib
    h = hashlib.md5(folder_path.encode("utf-8")).hexdigest()[:16]
    return thumbnail_cache_dir() / f"folder_{h}.jpg"


def comic_cover_override_path_for(comic_id: int) -> Path:
    """Cache path for a comic's manual cover override.

    Kept distinct from the auto thumbnail (``{id}.jpg``) so resetting to the
    default cover doesn't have to clobber the override file.
    """
    return thumbnail_cache_dir() / f"cover_override_{comic_id}.jpg"


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
