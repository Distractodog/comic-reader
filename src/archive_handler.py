"""Reads comic book files of various formats and exposes pages as image bytes."""

from __future__ import annotations

import io
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path
from posixpath import dirname, normpath
import re
import xml.etree.ElementTree as ET

import py7zr
import rarfile
import fitz  # PyMuPDF

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


def _configure_rar_tool() -> None:
    """Prefer a bundled unrar binary when running from PyInstaller."""
    bundled_dir = Path(getattr(sys, "_MEIPASS", ""))
    candidates = [
        bundled_dir / "unrar.exe",
        bundled_dir / "UnRAR.exe",
        bundled_dir / "unrar",
    ]
    for candidate in candidates:
        if candidate.exists():
            rarfile.UNRAR_TOOL = str(candidate)
            return


_configure_rar_tool()


def _natural_sort_key(name: str):
    """Sort filenames so page2.jpg comes before page10.jpg."""
    import re
    parts = re.split(r"(\d+)", name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


class ComicReader(ABC):
    """Base interface for all comic file readers.

    A single reader instance is shared between the UI thread and background
    loader/preloader threads. The underlying archive/document handles
    (zipfile, rarfile, fitz, …) are NOT thread-safe, so all access to a page
    goes through a lock here. ``get_page_bytes`` / ``close`` are the locked
    public entry points; subclasses implement the unlocked ``_read_page`` /
    ``_close`` internals.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.pages: list = []
        self._lock = threading.Lock()

    def page_count(self) -> int:
        return len(self.pages)

    def get_page_bytes(self, index: int) -> bytes:
        """Return raw image bytes for the page at the given index (thread-safe)."""
        with self._lock:
            return self._read_page(index)

    @abstractmethod
    def _read_page(self, index: int) -> bytes:
        """Return raw image bytes for the page at the given index (no locking)."""

    def close(self):
        """Close the underlying handle (thread-safe — waits for any in-flight read)."""
        with self._lock:
            self._close()

    def _close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class _ArchiveReader(ComicReader):
    """Shared logic for ZIP/RAR/7z/TAR style comic archives."""

    def _filter_and_sort(self, names: list[str]):
        images = [n for n in names if Path(n).suffix.lower() in IMAGE_EXTENSIONS]
        images.sort(key=_natural_sort_key)
        return images


class CBZReader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._zip = zipfile.ZipFile(file_path, "r")
        self.pages = self._filter_and_sort(self._zip.namelist())

    def _read_page(self, index: int) -> bytes:
        return self._zip.read(self.pages[index])

    def _close(self):
        self._zip.close()


class CBRReader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        tools = [rarfile.UNRAR_TOOL, "unrar", "unar", "bsdtar", "7z", "7zz"]
        if not any(tool and (Path(str(tool)).exists() or shutil.which(str(tool))) for tool in tools):
            raise RuntimeError(
                "CBR/RAR files need an unrar-compatible tool. Install unrar, "
                "or use CBZ/ZIP files instead."
            )
        self._rar = rarfile.RarFile(file_path)
        self.pages = self._filter_and_sort(self._rar.namelist())

    def _read_page(self, index: int) -> bytes:
        return self._rar.read(self.pages[index])

    def _close(self):
        self._rar.close()


class CB7Reader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._tmpdir = tempfile.TemporaryDirectory()
        with py7zr.SevenZipFile(file_path, mode="r") as sz:
            all_names = sz.getnames()
            self.pages = self._filter_and_sort(all_names)
            sz.extractall(path=self._tmpdir.name)

    def _read_page(self, index: int) -> bytes:
        return (Path(self._tmpdir.name) / self.pages[index]).read_bytes()

    def _close(self):
        self._tmpdir.cleanup()


class CBTReader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._tar = tarfile.open(file_path, "r")
        members = [m.name for m in self._tar.getmembers() if m.isfile()]
        self.pages = self._filter_and_sort(members)

    def _read_page(self, index: int) -> bytes:
        f = self._tar.extractfile(self.pages[index])
        if f is None:
            raise IOError(f"Could not extract {self.pages[index]}")
        return f.read()

    def _close(self):
        self._tar.close()


class PDFReader(ComicReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._doc = fitz.open(file_path)
        self.pages = list(range(self._doc.page_count))

    def _read_page(self, index: int) -> bytes:
        page = self._doc.load_page(index)
        # Render at 2x for crisp display.
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        # Return uncompressed PPM rather than PNG: PNG-compressing a ~6-megapixel
        # raster (and decoding it again in Qt) is the single most expensive thing
        # we can do per page. PPM is a trivial header + raw RGB, so Qt loads it
        # near-instantly — a large speedup for multi-hundred-page PDFs.
        return pix.tobytes("ppm")

    def _close(self):
        self._doc.close()


class EPUBReader(_ArchiveReader):
    """Image-only EPUB reader.

    This intentionally treats EPUB as a comic/image container, not as a text
    ebook renderer. It prefers OPF spine order and falls back to natural image
    filename order when the package metadata is sparse.
    """

    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._zip = zipfile.ZipFile(file_path, "r")
        self.pages = self._epub_image_order()

    def _read_page(self, index: int) -> bytes:
        return self._zip.read(self.pages[index])

    def _close(self):
        self._zip.close()

    def _epub_image_order(self) -> list[str]:
        names = self._zip.namelist()
        all_images = self._filter_and_sort(names)
        ordered: list[str] = []

        try:
            container = ET.fromstring(self._zip.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is None:
                return all_images
            opf_path = rootfile.attrib.get("full-path")
            if not opf_path:
                return all_images
            opf = ET.fromstring(self._zip.read(opf_path))
            opf_dir = dirname(opf_path)

            manifest = {}
            media_types = {}
            for item in opf.findall(".//{*}manifest/{*}item"):
                item_id = item.attrib.get("id")
                href = item.attrib.get("href")
                if not item_id or not href:
                    continue
                full = normpath(f"{opf_dir}/{href}" if opf_dir else href)
                manifest[item_id] = full
                media_types[item_id] = item.attrib.get("media-type", "")

            for itemref in opf.findall(".//{*}spine/{*}itemref"):
                item_id = itemref.attrib.get("idref")
                if not item_id or item_id not in manifest:
                    continue
                path = manifest[item_id]
                if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
                    ordered.append(path)
                    continue
                if "html" in media_types.get(item_id, "") or Path(path).suffix.lower() in {".xhtml", ".html", ".htm"}:
                    ordered.extend(self._images_referenced_by_xhtml(path))
        except Exception:
            return all_images

        seen = set()
        result = []
        for page in ordered + all_images:
            if page in all_images and page not in seen:
                seen.add(page)
                result.append(page)
        return result

    def _images_referenced_by_xhtml(self, path: str) -> list[str]:
        try:
            text = self._zip.read(path).decode("utf-8", errors="ignore")
        except Exception:
            return []
        base = dirname(path)
        refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", text, flags=re.IGNORECASE)
        images = []
        for ref in refs:
            ref = ref.split("#", 1)[0].split("?", 1)[0]
            full = normpath(f"{base}/{ref}" if base else ref)
            if Path(full).suffix.lower() in IMAGE_EXTENSIONS:
                images.append(full)
        return images


class ImageFolderReader(_ArchiveReader):
    """For loose image files in a folder."""

    def __init__(self, folder_path: str):
        super().__init__(folder_path)
        names = [
            f for f in os.listdir(folder_path)
            if Path(f).suffix.lower() in IMAGE_EXTENSIONS
        ]
        self.pages = sorted(names, key=_natural_sort_key)

    def _read_page(self, index: int) -> bytes:
        with open(os.path.join(self.file_path, self.pages[index]), "rb") as f:
            return f.read()


def open_comic(path: str) -> ComicReader:
    """Factory: pick the right reader for a given file/folder path."""
    p = Path(path)

    if p.is_dir():
        return ImageFolderReader(str(p))

    suffix = p.suffix.lower()
    readers = {
        ".cbz": CBZReader,
        ".zip": CBZReader,
        ".cbr": CBRReader,
        ".rar": CBRReader,
        ".cb7": CB7Reader,
        ".7z": CB7Reader,
        ".cbt": CBTReader,
        ".tar": CBTReader,
        ".pdf": PDFReader,
        ".epub": EPUBReader,
    }
    reader_cls = readers.get(suffix)
    if reader_cls is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    return reader_cls(str(p))
