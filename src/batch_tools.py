"""Safe batch operations for comics: convert-to-CBZ and metadata renames."""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from archive_handler import open_comic
from library import Comic, Library

CONVERTIBLE_SUFFIXES = {".cbr", ".rar", ".cb7", ".7z", ".cbt", ".tar"}
SKIP_CONVERT_SUFFIXES = {".cbz", ".zip", ".pdf", ".epub"}


@dataclass
class BatchTask:
    comic_id: int
    source: str
    target: str
    operation: str  # "convert" | "rename"


@dataclass
class BatchPlan:
    operation: str
    tasks: list[BatchTask] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class BatchResult:
    completed: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 2
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _safe_filename(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:180] or "Untitled"


def _metadata_filename(comic: Comic) -> str:
    parts: list[str] = []
    if comic.series:
        if comic.series_number is not None:
            number = f"{comic.series_number:g}"
            parts.append(f"{comic.series} #{number}")
        else:
            parts.append(comic.series)
    if comic.title:
        parts.append(comic.title)
    if not parts:
        parts.append(Path(comic.file_path).stem)
    return _safe_filename(" - ".join(parts))


def _ext_from_bytes(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".jpg"


def plan_convert_to_cbz(comics: list[Comic]) -> BatchPlan:
    plan = BatchPlan(operation="convert")
    for comic in comics:
        src = Path(comic.file_path)
        suffix = src.suffix.lower()
        if suffix in SKIP_CONVERT_SUFFIXES:
            plan.skipped.append((comic.file_path, f"{suffix.upper()} is not converted"))
            continue
        if suffix not in CONVERTIBLE_SUFFIXES:
            plan.skipped.append((comic.file_path, "Unsupported source format"))
            continue
        target = _unique_path(src.with_suffix(".cbz"))
        plan.tasks.append(
            BatchTask(comic.id, str(src), str(target), operation="convert")
        )
    return plan


def plan_rename_from_metadata(comics: list[Comic]) -> BatchPlan:
    plan = BatchPlan(operation="rename")
    for comic in comics:
        src = Path(comic.file_path)
        target = src.with_name(_metadata_filename(comic) + src.suffix.lower())
        if target == src:
            plan.skipped.append((comic.file_path, "Already matches metadata"))
            continue
        target = _unique_path(target)
        plan.tasks.append(
            BatchTask(comic.id, str(src), str(target), operation="rename")
        )
    return plan


class BatchWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)

    def __init__(self, library: Library, plan: BatchPlan, parent=None):
        super().__init__(parent)
        self._library = library
        self._plan = plan
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        result = BatchResult(skipped=list(self._plan.skipped))
        total = len(self._plan.tasks)
        for i, task in enumerate(self._plan.tasks):
            if self._cancelled:
                break
            self.progress.emit(i, total, Path(task.source).name)
            try:
                if task.operation == "convert":
                    self._convert(task)
                elif task.operation == "rename":
                    self._rename(task)
                result.completed += 1
            except Exception as exc:
                result.errors.append((task.source, str(exc)))
        self.progress.emit(total, total, "Done")
        self.finished.emit(result)

    def _convert(self, task: BatchTask) -> None:
        src = Path(task.source)
        dst = Path(task.target)
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open_comic(str(src)) as reader:
            with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i in range(reader.page_count()):
                    data = reader.get_page_bytes(i)
                    zf.writestr(f"page_{i + 1:04d}{_ext_from_bytes(data)}", data)
        self._library.update_file_path(task.comic_id, str(dst))

    def _rename(self, task: BatchTask) -> None:
        src = Path(task.source)
        dst = Path(task.target)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        self._library.update_file_path(task.comic_id, str(dst))
