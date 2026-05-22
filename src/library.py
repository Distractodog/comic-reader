"""Local SQLite library database — tracks comics, reading progress, and metadata."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Comic:
    id: int
    file_path: str
    content_hash: str | None
    title: str | None
    series: str | None
    series_number: float | None
    author: str | None
    publisher: str | None
    year: int | None
    page_count: int
    file_size: int
    cover_path: str | None
    date_added: str
    last_read: str | None
    current_page: int
    read_status: str  # 'unread' | 'in_progress' | 'read'
    source_folder: str | None


def _default_db_path() -> Path:
    from PyQt6.QtCore import QStandardPaths
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / "library.db"


def _row_to_comic(row: sqlite3.Row) -> Comic:
    return Comic(
        id=row["id"],
        file_path=row["file_path"],
        content_hash=row["content_hash"],
        title=row["title"],
        series=row["series"],
        series_number=row["series_number"],
        author=row["author"],
        publisher=row["publisher"],
        year=row["year"],
        page_count=row["page_count"],
        file_size=row["file_size"],
        cover_path=row["cover_path"],
        date_added=row["date_added"],
        last_read=row["last_read"],
        current_page=row["current_page"],
        read_status=row["read_status"],
        source_folder=row["source_folder"],
    )


def _derive_status(current_page: int, page_count: int) -> str:
    if page_count <= 0:
        return "unread"
    if current_page <= 0:
        return "unread"
    if current_page >= page_count - 1:
        return "read"
    return "in_progress"


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS comics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT    NOT NULL UNIQUE,
    content_hash    TEXT,
    title           TEXT,
    series          TEXT,
    series_number   REAL,
    author          TEXT,
    publisher       TEXT,
    year            INTEGER,
    page_count      INTEGER NOT NULL DEFAULT 0,
    file_size       INTEGER NOT NULL DEFAULT 0,
    cover_path      TEXT,
    date_added      TEXT    NOT NULL,
    last_read       TEXT,
    current_page    INTEGER NOT NULL DEFAULT 0,
    read_status     TEXT    NOT NULL DEFAULT 'unread'
                    CHECK(read_status IN ('unread', 'in_progress', 'read'))
);

CREATE INDEX IF NOT EXISTS idx_comics_title      ON comics(title);
CREATE INDEX IF NOT EXISTS idx_comics_series     ON comics(series);
CREATE INDEX IF NOT EXISTS idx_comics_last_read  ON comics(last_read);
CREATE INDEX IF NOT EXISTS idx_comics_date_added ON comics(date_added);
"""

_SCHEMA_V2_MIGRATION = "ALTER TABLE comics ADD COLUMN source_folder TEXT;"

_CURRENT_VERSION = 2

_VALID_SORT_COLUMNS = {"title", "series", "date_added", "last_read"}


class Library:
    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = _default_db_path()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._migrate()

    def _migrate(self):
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._conn.executescript(_SCHEMA_V1)
            self._conn.execute("PRAGMA user_version = 1")
            self._conn.commit()
            version = 1
        if version < 2:
            self._conn.execute(_SCHEMA_V2_MIGRATION)
            self._conn.execute("PRAGMA user_version = 2")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @contextmanager
    def transaction(self):
        """Context manager for batch operations — commits on exit, rolls back on error."""
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ----- CRUD -----

    def add_comic(
        self,
        file_path: str,
        *,
        page_count: int,
        file_size: int,
        title: str | None = None,
        series: str | None = None,
        series_number: float | None = None,
        author: str | None = None,
        publisher: str | None = None,
        year: int | None = None,
        source_folder: str | None = None,
    ) -> int:
        """Insert a comic. If the path already exists, returns the existing id."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO comics
                    (file_path, page_count, file_size, title, series, series_number,
                     author, publisher, year, date_added, source_folder)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO NOTHING
                """,
                (file_path, page_count, file_size, title, series, series_number,
                 author, publisher, year, now, source_folder),
            )
        row = self._conn.execute(
            "SELECT id FROM comics WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row["id"]

    def get_comic(self, file_path: str) -> Comic | None:
        row = self._conn.execute(
            "SELECT * FROM comics WHERE file_path = ?", (file_path,)
        ).fetchone()
        return _row_to_comic(row) if row else None

    def get_comic_by_id(self, comic_id: int) -> Comic | None:
        row = self._conn.execute(
            "SELECT * FROM comics WHERE id = ?", (comic_id,)
        ).fetchone()
        return _row_to_comic(row) if row else None

    def get_all_comics(
        self,
        *,
        sort_by: str = "date_added",
        order: str = "desc",
    ) -> list[Comic]:
        if sort_by not in _VALID_SORT_COLUMNS:
            sort_by = "date_added"
        order = "DESC" if order.lower() == "desc" else "ASC"
        rows = self._conn.execute(
            f"SELECT * FROM comics ORDER BY {sort_by} {order}"
        ).fetchall()
        return [_row_to_comic(r) for r in rows]

    def comic_exists(self, file_path: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM comics WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row is not None

    def remove_comic(self, comic_id: int) -> None:
        with self.transaction() as cur:
            cur.execute("DELETE FROM comics WHERE id = ?", (comic_id,))

    # ----- Updates -----

    def update_progress(self, comic_id: int, current_page: int) -> None:
        """Update current page, auto-derive read_status, and stamp last_read."""
        comic = self.get_comic_by_id(comic_id)
        if comic is None:
            return
        status = _derive_status(current_page, comic.page_count)
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE comics
                SET current_page = ?, read_status = ?, last_read = ?
                WHERE id = ?
                """,
                (current_page, status, now, comic_id),
            )

    def set_read_status(self, comic_id: int, status: str) -> None:
        if status not in ("unread", "in_progress", "read"):
            raise ValueError(f"Invalid read_status: {status!r}")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET read_status = ? WHERE id = ?",
                (status, comic_id),
            )

    def update_metadata(self, comic_id: int, **fields) -> None:
        """Update any subset of metadata columns (title, series, author, etc.)."""
        allowed = {
            "title", "series", "series_number", "author",
            "publisher", "year", "page_count",
        }
        filtered = {k: v for k, v in fields.items() if k in allowed}
        if not filtered:
            return
        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [comic_id]
        with self.transaction() as cur:
            cur.execute(
                f"UPDATE comics SET {set_clause} WHERE id = ?", values
            )

    def set_cover_path(self, comic_id: int, cover_path: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET cover_path = ? WHERE id = ?",
                (cover_path, comic_id),
            )

    def set_content_hash(self, comic_id: int, content_hash: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET content_hash = ? WHERE id = ?",
                (content_hash, comic_id),
            )


if __name__ == "__main__":
    # Smoke test — in-memory DB, no real comic files needed.
    lib = Library(db_path=":memory:")

    id1 = lib.add_comic("/comics/batman.cbz", page_count=24, file_size=10_000_000, title="Batman #1", series="Batman", source_folder="/comics")
    id2 = lib.add_comic("/comics/xmen.cbz",   page_count=22, file_size=8_000_000,  title="X-Men #1",  series="X-Men",   source_folder="/comics")

    assert lib.comic_exists("/comics/batman.cbz")
    assert not lib.comic_exists("/comics/missing.cbz")

    comics = lib.get_all_comics(sort_by="title", order="asc")
    assert len(comics) == 2
    assert comics[0].title == "Batman #1"
    assert comics[1].title == "X-Men #1"

    assert comics[0].read_status == "unread"
    lib.update_progress(id1, current_page=5)
    assert lib.get_comic_by_id(id1).read_status == "in_progress"

    lib.update_progress(id1, current_page=23)
    assert lib.get_comic_by_id(id1).read_status == "read"

    lib.set_read_status(id1, "unread")
    assert lib.get_comic_by_id(id1).read_status == "unread"

    lib.update_metadata(id2, author="Stan Lee", year=1963)
    assert lib.get_comic_by_id(id2).author == "Stan Lee"
    assert lib.get_comic_by_id(id1).source_folder == "/comics"

    # Duplicate add returns existing id
    same_id = lib.add_comic("/comics/batman.cbz", page_count=24, file_size=10_000_000)
    assert same_id == id1

    lib.remove_comic(id2)
    assert len(lib.get_all_comics()) == 1

    lib.close()
    print("library.py smoke test: OK")
