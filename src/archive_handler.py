"""Reads comic book files of various formats and exposes pages as image bytes."""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path

import py7zr
import rarfile
import fitz  # PyMuPDF

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}


def _natural_sort_key(name: str):
    """Sort filenames so page2.jpg comes before page10.jpg."""
    import re
    parts = re.split(r"(\d+)", name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


class ComicReader(ABC):
    """Base interface for all comic file readers."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.pages: list = []

    def page_count(self) -> int:
        return len(self.pages)

    @abstractmethod
    def get_page_bytes(self, index: int) -> bytes:
        """Return raw image bytes for the page at the given index."""

    def close(self):
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

    def get_page_bytes(self, index: int) -> bytes:
        return self._zip.read(self.pages[index])

    def close(self):
        self._zip.close()


class CBRReader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._rar = rarfile.RarFile(file_path)
        self.pages = self._filter_and_sort(self._rar.namelist())

    def get_page_bytes(self, index: int) -> bytes:
        return self._rar.read(self.pages[index])

    def close(self):
        self._rar.close()


class CB7Reader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._sz = py7zr.SevenZipFile(file_path, mode="r")
        all_names = self._sz.getnames()
        self.pages = self._filter_and_sort(all_names)

    def get_page_bytes(self, index: int) -> bytes:
        target = self.pages[index]
        # py7zr requires resetting for repeat reads
        self._sz.reset()
        result = self._sz.read([target])
        return result[target].read()

    def close(self):
        self._sz.close()


class CBTReader(_ArchiveReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._tar = tarfile.open(file_path, "r")
        members = [m.name for m in self._tar.getmembers() if m.isfile()]
        self.pages = self._filter_and_sort(members)

    def get_page_bytes(self, index: int) -> bytes:
        f = self._tar.extractfile(self.pages[index])
        if f is None:
            raise IOError(f"Could not extract {self.pages[index]}")
        return f.read()

    def close(self):
        self._tar.close()


class PDFReader(ComicReader):
    def __init__(self, file_path: str):
        super().__init__(file_path)
        self._doc = fitz.open(file_path)
        self.pages = list(range(self._doc.page_count))

    def get_page_bytes(self, index: int) -> bytes:
        page = self._doc.load_page(index)
        # Render at 2x for crisp display
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        return pix.tobytes("png")

    def close(self):
        self._doc.close()


class ImageFolderReader(_ArchiveReader):
    """For loose image files in a folder."""

    def __init__(self, folder_path: str):
        super().__init__(folder_path)
        names = [
            f for f in os.listdir(folder_path)
            if Path(f).suffix.lower() in IMAGE_EXTENSIONS
        ]
        self.pages = sorted(names, key=_natural_sort_key)

    def get_page_bytes(self, index: int) -> bytes:
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
    }
    reader_cls = readers.get(suffix)
    if reader_cls is None:
        raise ValueError(f"Unsupported file type: {suffix}")
    return reader_cls(str(p))
