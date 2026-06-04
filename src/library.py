"""Local SQLite library database — tracks comics, reading progress, and metadata."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Folder:
    path: str
    name: str
    comic_count: int
    cover_path: str | None


@dataclass
class Shelf:
    id: int
    name: str
    kind: str        # 'manual' | 'smart'
    smart_key: str | None
    sort_order: int


@dataclass
class Series:
    name: str
    comic_count: int
    cover_path: str | None
    folder_path: str


@dataclass
class Bookmark:
    id: int
    comic_id: int
    page_index: int
    label: str | None
    created_at: str


@dataclass
class Annotation:
    id: int
    comic_id: int
    page_index: int
    body: str
    created_at: str
    updated_at: str


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
    is_manga: bool = False
    reading_mode: str = "single"  # 'single' | 'webtoon'
    hidden: bool = False
    fit_mode: str = "page"        # 'actual' | 'width' | 'page'
    zoom: float = 1.0
    cover_override: bool = False  # cover set manually; skip auto-regeneration


def _default_db_path() -> Path:
    from PyQt6.QtCore import QStandardPaths
    base = Path(
        QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
    )
    base.mkdir(parents=True, exist_ok=True)
    return base / "library.db"


def _parent_dir(file_path: str) -> str:
    """The immediate parent directory of a comic file — how the folder grid groups."""
    return str(Path(file_path).parent)


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
        is_manga=bool(row["is_manga"]),
        reading_mode=row["reading_mode"],
        hidden=bool(row["hidden"]),
        fit_mode=row["fit_mode"],
        zoom=float(row["zoom"]),
        cover_override=bool(row["cover_override"]),
    )


def _sort_comics(comics: list, sort_by: str, order: str) -> list:
    reverse = order.lower() == "desc"
    if sort_by == "title":
        return sorted(comics, key=lambda c: (c.title or Path(c.file_path).stem).lower(), reverse=reverse)
    if sort_by == "date_added":
        return sorted(comics, key=lambda c: c.date_added or "", reverse=reverse)
    if sort_by == "last_read":
        has = sorted([c for c in comics if c.last_read], key=lambda c: c.last_read, reverse=reverse)
        return has + [c for c in comics if not c.last_read]
    return sorted(comics, key=lambda c: (c.title or Path(c.file_path).stem).lower())


def compute_content_signature(file_path: str) -> str:
    """A cheap, offline content fingerprint for duplicate detection.

    Hashing every byte of thousands of comics (PDFs can be hundreds of MB) is
    too slow. Instead we hash (file_size, first 64 KB, last 64 KB) — enough to
    identify the same file copied to multiple places without reading whole
    archives. Returns a hex sha256 digest.
    """
    import hashlib

    chunk = 64 * 1024
    h = hashlib.sha256()
    size = Path(file_path).stat().st_size
    h.update(str(size).encode("ascii"))
    with open(file_path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk:
            f.seek(max(0, size - chunk))
            h.update(f.read(chunk))
    return h.hexdigest()


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

_SCHEMA_V3_MIGRATION = """
CREATE TABLE IF NOT EXISTS shelves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'manual'
                CHECK(kind IN ('manual', 'smart')),
    smart_key   TEXT,
    cover_path  TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS comic_shelves (
    comic_id    INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    shelf_id    INTEGER NOT NULL REFERENCES shelves(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (comic_id, shelf_id)
);
CREATE INDEX IF NOT EXISTS idx_comic_shelves_shelf ON comic_shelves(shelf_id);
"""

_SCHEMA_V4_MIGRATION = """
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS comic_tags (
    comic_id INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (comic_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_comic_tags_tag ON comic_tags(tag_id);
"""

_SCHEMA_V5_MIGRATION_TABLES = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    comic_id    INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    page_index  INTEGER NOT NULL,
    label       TEXT,
    created_at  TEXT    NOT NULL,
    UNIQUE(comic_id, page_index)
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_comic ON bookmarks(comic_id);
"""

_SCHEMA_V7_MIGRATION = """
CREATE TABLE IF NOT EXISTS folder_covers (
    folder_path TEXT PRIMARY KEY,
    cover_path  TEXT NOT NULL
);
"""

_SCHEMA_V8_FIT_MODE = "ALTER TABLE comics ADD COLUMN fit_mode TEXT NOT NULL DEFAULT 'page';"
_SCHEMA_V8_ZOOM     = "ALTER TABLE comics ADD COLUMN zoom REAL NOT NULL DEFAULT 1.0;"

# Marks a cover as a manual override so automatic regeneration leaves it alone.
_SCHEMA_V9_COVER_OVERRIDE = (
    "ALTER TABLE comics ADD COLUMN cover_override INTEGER NOT NULL DEFAULT 0;"
)

# Index content_hash so duplicate-detection grouping is cheap.
_SCHEMA_V10_HASH_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_comics_content_hash ON comics(content_hash);"
)

# Per-day reading events, for reading statistics.
_SCHEMA_V11_READING_EVENTS = """
CREATE TABLE IF NOT EXISTS reading_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comic_id   INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    event_date TEXT    NOT NULL,
    pages_read INTEGER NOT NULL DEFAULT 0,
    seconds    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(comic_id, event_date)
);
CREATE INDEX IF NOT EXISTS idx_reading_events_date ON reading_events(event_date);
"""

# User-curated "read next" list. position keeps an explicit manual order;
# the row is removed automatically if the comic is deleted (FK CASCADE).
_SCHEMA_V12_READING_QUEUE = """
CREATE TABLE IF NOT EXISTS reading_queue (
    comic_id INTEGER PRIMARY KEY REFERENCES comics(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT    NOT NULL
);
"""

_SCHEMA_V13_ANNOTATIONS = """
CREATE TABLE IF NOT EXISTS annotations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comic_id   INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL,
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_comic ON annotations(comic_id);
"""

_CURRENT_VERSION = 13

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
            version = 2
        if version < 3:
            self._conn.executescript(_SCHEMA_V3_MIGRATION)
            self._seed_smart_shelves()
            self._conn.execute("PRAGMA user_version = 3")
            self._conn.commit()
            version = 3
        if version < 4:
            self._conn.executescript(_SCHEMA_V4_MIGRATION)
            self._conn.execute("PRAGMA user_version = 4")
            self._conn.commit()
            version = 4
        if version < 5:
            self._conn.execute(
                "ALTER TABLE comics ADD COLUMN reading_mode TEXT NOT NULL DEFAULT 'single'"
            )
            self._conn.execute(
                "ALTER TABLE comics ADD COLUMN is_manga INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.executescript(_SCHEMA_V5_MIGRATION_TABLES)
            self._conn.execute("PRAGMA user_version = 5")
            self._conn.commit()
            version = 5
        if version < 6:
            # Indexed immediate-parent directory, so folder queries no longer
            # need a full table scan + per-row Path() parsing in Python.
            self._conn.execute("ALTER TABLE comics ADD COLUMN parent_dir TEXT")
            rows = self._conn.execute("SELECT id, file_path FROM comics").fetchall()
            for r in rows:
                self._conn.execute(
                    "UPDATE comics SET parent_dir = ? WHERE id = ?",
                    (_parent_dir(r["file_path"]), r["id"]),
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_comics_parent ON comics(parent_dir)"
            )
            self._conn.execute("PRAGMA user_version = 6")
            self._conn.commit()
            version = 6
        if version < 7:
            # Soft-hide flag (remove from app without deleting from disk) +
            # per-folder cover overrides.
            self._conn.execute(
                "ALTER TABLE comics ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_comics_hidden ON comics(hidden)"
            )
            self._conn.executescript(_SCHEMA_V7_MIGRATION)
            self._conn.execute("PRAGMA user_version = 7")
            self._conn.commit()
            version = 7
        if version < 8:
            # Per-comic viewer state: remember fit mode and zoom level.
            self._conn.execute(_SCHEMA_V8_FIT_MODE)
            self._conn.execute(_SCHEMA_V8_ZOOM)
            self._conn.execute("PRAGMA user_version = 8")
            self._conn.commit()
            version = 8
        if version < 9:
            # Per-comic manual cover override flag (survives rescans).
            self._conn.execute(_SCHEMA_V9_COVER_OVERRIDE)
            self._conn.execute("PRAGMA user_version = 9")
            self._conn.commit()
            version = 9
        if version < 10:
            # Index content_hash for duplicate detection.
            self._conn.execute(_SCHEMA_V10_HASH_INDEX)
            self._conn.execute("PRAGMA user_version = 10")
            self._conn.commit()
            version = 10
        if version < 11:
            # Reading-events log for statistics.
            self._conn.executescript(_SCHEMA_V11_READING_EVENTS)
            self._conn.execute("PRAGMA user_version = 11")
            self._conn.commit()
            version = 11
        if version < 12:
            # User-curated reading queue ("read next").
            self._conn.executescript(_SCHEMA_V12_READING_QUEUE)
            self._conn.execute("PRAGMA user_version = 12")
            self._conn.commit()
            version = 12
        if version < 13:
            # Per-page annotations for image comic readers.
            self._conn.executescript(_SCHEMA_V13_ANNOTATIONS)
            self._conn.execute("PRAGMA user_version = 13")
            self._conn.commit()

    def _seed_smart_shelves(self):
        now = datetime.now(timezone.utc).isoformat()
        for order, (name, key) in enumerate([
            ("Recently Added",    "recently_added"),
            ("Currently Reading", "currently_reading"),
            ("Unread",            "unread"),
            ("Finished",          "finished"),
        ]):
            self._conn.execute(
                "INSERT OR IGNORE INTO shelves (name, kind, smart_key, sort_order, created_at)"
                " VALUES (?, 'smart', ?, ?, ?)",
                (name, key, order, now),
            )
        self._conn.commit()

    def _get_smart_shelf_comics(
        self, smart_key: str, sort_by: str, order: str
    ) -> list[Comic]:
        from datetime import timedelta
        if smart_key == "recently_added":
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            rows = self._conn.execute(
                "SELECT * FROM comics WHERE hidden = 0 AND date_added >= ?", (cutoff,)
            ).fetchall()
        elif smart_key == "currently_reading":
            rows = self._conn.execute(
                "SELECT * FROM comics WHERE hidden = 0 AND read_status = 'in_progress'"
            ).fetchall()
        elif smart_key == "unread":
            rows = self._conn.execute(
                "SELECT * FROM comics WHERE hidden = 0 AND read_status = 'unread'"
            ).fetchall()
        elif smart_key == "finished":
            rows = self._conn.execute(
                "SELECT * FROM comics WHERE hidden = 0 AND read_status = 'read'"
            ).fetchall()
        else:
            return []
        return _sort_comics([_row_to_comic(r) for r in rows], sort_by, order)

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

    # ----- Comics CRUD -----

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
                     author, publisher, year, date_added, source_folder, parent_dir)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO NOTHING
                """,
                (file_path, page_count, file_size, title, series, series_number,
                 author, publisher, year, now, source_folder, _parent_dir(file_path)),
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
            f"SELECT * FROM comics WHERE hidden = 0 ORDER BY {sort_by} {order}"
        ).fetchall()
        return [_row_to_comic(r) for r in rows]

    def comic_exists(self, file_path: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM comics WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row is not None

    def remove_comic(self, comic_id: int) -> None:
        comic = self.get_comic_by_id(comic_id)
        if comic and comic.cover_path:
            try:
                Path(comic.cover_path).unlink(missing_ok=True)
            except OSError:
                pass
        with self.transaction() as cur:
            cur.execute("DELETE FROM comics WHERE id = ?", (comic_id,))

    def update_file_path(self, comic_id: int, new_path: str) -> None:
        """Move a library row to a new on-disk path after a safe batch operation."""
        new_parent = _parent_dir(new_path)
        with self.transaction() as cur:
            cur.execute(
                """
                UPDATE comics
                SET file_path = ?, parent_dir = ?, source_folder = ?
                WHERE id = ?
                """,
                (new_path, new_parent, new_parent, comic_id),
            )

    # ----- Hide / restore (remove from app without deleting from disk) -----

    def set_hidden(self, comic_id: int, hidden: bool = True) -> None:
        """Hide or restore a single comic. Hidden comics never touch the disk."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET hidden = ? WHERE id = ?",
                (1 if hidden else 0, comic_id),
            )

    def hide_folder(self, folder_path: str) -> None:
        """Hide every comic currently in a folder. Files are left on disk."""
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET hidden = 1 WHERE parent_dir = ?", (folder_path,)
            )

    def rename_folder(self, folder_path: str, new_name: str) -> str:
        """Rename a folder on disk and update every comic path in the library."""
        new_name = new_name.strip()
        if not new_name or new_name in (".", ".."):
            raise ValueError("Enter a valid folder name.")
        if new_name != Path(new_name).name:
            raise ValueError("Folder name cannot include / or \\.")
        invalid = '<>:"/\\|?*'
        if any(ch in new_name for ch in invalid):
            raise ValueError("Folder name contains invalid characters.")

        old = Path(folder_path)
        if not old.is_dir():
            raise FileNotFoundError("This folder was not found on disk.")

        new_path = str(old.parent / new_name)
        if new_path == folder_path:
            return folder_path
        if Path(new_path).exists():
            raise FileExistsError("A folder with that name already exists here.")

        rows = self._conn.execute(
            "SELECT id, file_path FROM comics WHERE parent_dir = ?", (folder_path,)
        ).fetchall()
        if not rows:
            raise ValueError("No comics in the library use this folder.")

        os.rename(old, new_path)
        try:
            with self.transaction() as cur:
                for row in rows:
                    new_file = str(Path(new_path) / Path(row["file_path"]).name)
                    cur.execute(
                        """
                        UPDATE comics
                        SET file_path = ?, parent_dir = ?
                        WHERE id = ?
                        """,
                        (new_file, new_path, row["id"]),
                    )
                cur.execute(
                    "UPDATE folder_covers SET folder_path = ? WHERE folder_path = ?",
                    (new_path, folder_path),
                )
        except Exception:
            if Path(new_path).is_dir() and not Path(folder_path).is_dir():
                os.rename(new_path, folder_path)
            raise
        return new_path

    def get_hidden_comics(
        self, *, sort_by: str = "title", order: str = "asc"
    ) -> list[Comic]:
        """Return all hidden comics, for the restore view."""
        rows = self._conn.execute(
            "SELECT * FROM comics WHERE hidden = 1"
        ).fetchall()
        return _sort_comics([_row_to_comic(r) for r in rows], sort_by, order)

    # ----- Folder cover overrides -----

    def _folder_cover_overrides(self) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT folder_path, cover_path FROM folder_covers"
        ).fetchall()
        return {r["folder_path"]: r["cover_path"] for r in rows}

    def get_folder_cover(self, folder_path: str) -> str | None:
        row = self._conn.execute(
            "SELECT cover_path FROM folder_covers WHERE folder_path = ?",
            (folder_path,),
        ).fetchone()
        return row["cover_path"] if row else None

    def set_folder_cover(self, folder_path: str, cover_path: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO folder_covers (folder_path, cover_path) VALUES (?, ?)"
                " ON CONFLICT(folder_path) DO UPDATE SET cover_path = excluded.cover_path",
                (folder_path, cover_path),
            )

    def clear_folder_cover(self, folder_path: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM folder_covers WHERE folder_path = ?", (folder_path,)
            )

    # ----- Comic updates -----

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

    def set_cover_override(self, comic_id: int, override: bool) -> None:
        """Flag (or clear) a comic's cover as a manual override.

        While set, the background scanner must not regenerate this comic's cover.
        """
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET cover_override = ? WHERE id = ?",
                (1 if override else 0, comic_id),
            )

    def set_content_hash(self, comic_id: int, content_hash: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET content_hash = ? WHERE id = ?",
                (content_hash, comic_id),
            )

    # ----- Duplicate detection -----

    def get_unhashed_comics(self) -> list[Comic]:
        """Visible comics that don't yet have a content signature."""
        rows = self._conn.execute(
            "SELECT * FROM comics WHERE content_hash IS NULL AND hidden = 0"
        ).fetchall()
        return [_row_to_comic(r) for r in rows]

    def find_duplicate_groups(self) -> list[list[Comic]]:
        """Return groups of visible comics that share a content signature.

        Only groups with 2+ members are returned. Each group is sorted by
        file path so the original (shortest/earliest) tends to come first.
        """
        rows = self._conn.execute(
            "SELECT * FROM comics"
            " WHERE content_hash IS NOT NULL AND hidden = 0"
            " ORDER BY content_hash, file_path"
        ).fetchall()
        groups: dict[str, list[Comic]] = {}
        for r in rows:
            groups.setdefault(r["content_hash"], []).append(_row_to_comic(r))
        return [g for g in groups.values() if len(g) > 1]

    # ----- Reading statistics -----

    def record_reading(self, comic_id: int, pages_read: int, seconds: int) -> None:
        """Accumulate reading activity into today's (UTC) event row for a comic.

        ``pages_read`` should be net forward pages; both values are clamped to
        be non-negative. A no-op when there's nothing to record.
        """
        pages_read = max(0, int(pages_read))
        seconds = max(0, int(seconds))
        if pages_read == 0 and seconds == 0:
            return
        today = datetime.now(timezone.utc).date().isoformat()
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO reading_events (comic_id, event_date, pages_read, seconds)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(comic_id, event_date) DO UPDATE SET"
                " pages_read = pages_read + excluded.pages_read,"
                " seconds = seconds + excluded.seconds",
                (comic_id, today, pages_read, seconds),
            )

    def get_stats(self) -> dict:
        """Aggregate reading statistics for the stats view.

        Returns total pages, total hours, completion counts/rate, a
        pages-per-day series for the last 30 days, and the current daily streak.
        """
        totals = self._conn.execute(
            "SELECT COALESCE(SUM(pages_read), 0) AS pages,"
            " COALESCE(SUM(seconds), 0) AS seconds FROM reading_events"
        ).fetchone()
        total_pages = totals["pages"]
        total_seconds = totals["seconds"]

        total_comics = self._conn.execute(
            "SELECT COUNT(*) AS n FROM comics WHERE hidden = 0"
        ).fetchone()["n"]
        completed = self._conn.execute(
            "SELECT COUNT(*) AS n FROM comics WHERE hidden = 0 AND read_status = 'read'"
        ).fetchone()["n"]
        completion_rate = (completed / total_comics) if total_comics else 0.0

        # Pages per day for the last 30 calendar days (UTC), oldest first.
        from datetime import timedelta
        today = datetime.now(timezone.utc).date()
        per_day_rows = self._conn.execute(
            "SELECT event_date, SUM(pages_read) AS pages FROM reading_events"
            " GROUP BY event_date"
        ).fetchall()
        per_day_map = {r["event_date"]: r["pages"] for r in per_day_rows}
        pages_per_day = []
        for i in range(29, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            pages_per_day.append((d, per_day_map.get(d, 0)))

        # Current streak: consecutive days up to and including today with activity.
        active_days = set(per_day_map.keys())
        streak = 0
        d = today
        while d.isoformat() in active_days:
            streak += 1
            d = d - timedelta(days=1)

        return {
            "total_pages": total_pages,
            "total_hours": total_seconds / 3600.0,
            "total_comics": total_comics,
            "comics_completed": completed,
            "completion_rate": completion_rate,
            "pages_per_day": pages_per_day,
            "current_streak": streak,
        }

    # ----- Folder queries -----

    def get_folders(self) -> list[Folder]:
        """Return one Folder per unique parent directory, sorted alphabetically."""
        rows = self._conn.execute(
            "SELECT parent_dir, COUNT(*) AS cnt, MAX(cover_path) AS cover"
            " FROM comics WHERE hidden = 0 GROUP BY parent_dir"
        ).fetchall()
        overrides = self._folder_cover_overrides()
        folders = [
            Folder(
                path=row["parent_dir"],
                name=Path(row["parent_dir"]).name,
                comic_count=row["cnt"],
                cover_path=overrides.get(row["parent_dir"]) or row["cover"] or None,
            )
            for row in rows
        ]
        return sorted(folders, key=lambda f: f.name.lower())

    def get_comics_in_folder(
        self, folder_path: str, *, sort_by: str = "title", order: str = "asc"
    ) -> list[Comic]:
        """Return all comics whose file lives directly in folder_path."""
        if sort_by not in _VALID_SORT_COLUMNS:
            sort_by = "title"
        rows = self._conn.execute(
            "SELECT * FROM comics WHERE parent_dir = ? AND hidden = 0", (folder_path,)
        ).fetchall()
        return _sort_comics([_row_to_comic(r) for r in rows], sort_by, order)

    def get_series_in_folder(self, folder_path: str) -> list[Series]:
        """Return series that have 2+ comics in the folder, sorted by name."""
        comics = self.get_comics_in_folder(folder_path)
        groups: dict[str, list[Comic]] = {}
        for c in comics:
            if c.series:
                groups.setdefault(c.series, []).append(c)
        result = []
        for name, group in groups.items():
            if len(group) >= 2:
                sorted_group = sorted(group, key=lambda c: (c.series_number or 0, (c.title or "").lower()))
                cover = next((c.cover_path for c in sorted_group if c.cover_path), None)
                result.append(Series(name=name, comic_count=len(group), cover_path=cover, folder_path=folder_path))
        return sorted(result, key=lambda s: s.name.lower())

    def get_comics_in_series(self, folder_path: str, series_name: str) -> list[Comic]:
        """Return comics for a specific series within a folder, sorted by issue number."""
        comics = self.get_comics_in_folder(folder_path)
        series_comics = [c for c in comics if c.series == series_name]
        return sorted(series_comics, key=lambda c: (c.series_number or 0, (c.title or "").lower()))

    def search_library(
        self, query: str, sort_by: str = "title", order: str = "asc"
    ) -> tuple[list[Folder], list[Comic]]:
        """Search comics by title/series/author and folders by name.

        Returns (matching_folders, matching_comics) where matching_comics includes
        comics whose metadata matches plus all comics inside any matching folder.
        """
        if not query:
            return [], []
        if sort_by not in _VALID_SORT_COLUMNS:
            sort_by = "title"
        like = f"%{query}%"

        all_folders = self.get_folders()
        matching_folders = [f for f in all_folders if query.lower() in f.name.lower()]
        matching_folder_paths = {f.path for f in matching_folders}

        meta_rows = self._conn.execute(
            """
            SELECT * FROM comics
            WHERE hidden = 0 AND (
                   title LIKE ? COLLATE NOCASE
                OR series LIKE ? COLLATE NOCASE
                OR author LIKE ? COLLATE NOCASE
                OR file_path LIKE ? COLLATE NOCASE
            )
            """,
            (like, like, like, like),
        ).fetchall()
        meta_ids = {r["id"] for r in meta_rows}
        meta_comics = [_row_to_comic(r) for r in meta_rows]

        tag_rows = self._conn.execute(
            "SELECT DISTINCT c.* FROM comics c"
            " JOIN comic_tags ct ON c.id = ct.comic_id"
            " JOIN tags t ON ct.tag_id = t.id"
            " WHERE t.name LIKE ? COLLATE NOCASE AND c.hidden = 0",
            (like,),
        ).fetchall()
        for r in tag_rows:
            if r["id"] not in meta_ids:
                meta_ids.add(r["id"])
                meta_comics.append(_row_to_comic(r))

        folder_comics: list[Comic] = []
        if matching_folder_paths:
            placeholders = ",".join("?" for _ in matching_folder_paths)
            rows = self._conn.execute(
                f"SELECT * FROM comics WHERE hidden = 0 AND parent_dir IN ({placeholders})",
                tuple(matching_folder_paths),
            ).fetchall()
            folder_comics = [
                _row_to_comic(r) for r in rows if r["id"] not in meta_ids
            ]

        return matching_folders, _sort_comics(meta_comics + folder_comics, sort_by, order)

    # ----- Shelf CRUD -----

    def create_shelf(self, name: str) -> int:
        """Create a manual shelf and return its id."""
        now = datetime.now(timezone.utc).isoformat()
        new_id = None
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO shelves (name, kind, sort_order, created_at) VALUES (?, 'manual', 999, ?)",
                (name.strip(), now),
            )
            new_id = cur.lastrowid
        return new_id

    def rename_shelf(self, shelf_id: int, name: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE shelves SET name = ? WHERE id = ? AND kind = 'manual'",
                (name.strip(), shelf_id),
            )

    def delete_shelf(self, shelf_id: int) -> None:
        with self.transaction() as cur:
            cur.execute("DELETE FROM shelves WHERE id = ? AND kind = 'manual'", (shelf_id,))

    def get_shelves(self) -> list[Shelf]:
        """Return all shelves: smart shelves first (by sort_order), then manual alphabetically."""
        rows = self._conn.execute(
            "SELECT * FROM shelves ORDER BY"
            " CASE kind WHEN 'smart' THEN 0 ELSE 1 END,"
            " sort_order, name COLLATE NOCASE"
        ).fetchall()
        return [
            Shelf(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                smart_key=row["smart_key"],
                sort_order=row["sort_order"],
            )
            for row in rows
        ]

    def add_comic_to_shelf(self, comic_id: int, shelf_id: int) -> None:
        with self.transaction() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO comic_shelves (comic_id, shelf_id) VALUES (?, ?)",
                (comic_id, shelf_id),
            )

    def add_folder_to_shelf(self, folder_path: str, shelf_id: int) -> int:
        """Add every comic in folder_path to a manual shelf. Returns comics linked."""
        row = self._conn.execute(
            "SELECT kind FROM shelves WHERE id = ?", (shelf_id,)
        ).fetchone()
        if row is None or row["kind"] != "manual":
            return 0
        comics = self.get_comics_in_folder(folder_path)
        if not comics:
            return 0
        with self.transaction() as cur:
            for comic in comics:
                cur.execute(
                    "INSERT OR IGNORE INTO comic_shelves (comic_id, shelf_id) VALUES (?, ?)",
                    (comic.id, shelf_id),
                )
        return len(comics)

    def remove_comic_from_shelf(self, comic_id: int, shelf_id: int) -> None:
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM comic_shelves WHERE comic_id = ? AND shelf_id = ?",
                (comic_id, shelf_id),
            )

    def get_comics_in_shelf(
        self, shelf_id: int, *, sort_by: str = "title", order: str = "asc"
    ) -> list[Comic]:
        shelf_row = self._conn.execute(
            "SELECT * FROM shelves WHERE id = ?", (shelf_id,)
        ).fetchone()
        if shelf_row is None:
            return []
        if shelf_row["kind"] == "smart":
            return self._get_smart_shelf_comics(shelf_row["smart_key"], sort_by, order)
        if sort_by not in _VALID_SORT_COLUMNS:
            sort_by = "title"
        comic_rows = self._conn.execute(
            "SELECT c.* FROM comics c"
            " JOIN comic_shelves cs ON c.id = cs.comic_id"
            " WHERE cs.shelf_id = ? AND c.hidden = 0",
            (shelf_id,),
        ).fetchall()
        return _sort_comics([_row_to_comic(r) for r in comic_rows], sort_by, order)

    def get_shelf_folders(self, shelf_id: int) -> list[Folder]:
        """Return folders for comics on this shelf, with per-shelf counts.
        Folders with 0 comics on the shelf are excluded."""
        comics = self.get_comics_in_shelf(shelf_id)
        overrides = self._folder_cover_overrides()
        seen: dict[str, Folder] = {}
        for c in comics:
            parent = str(Path(c.file_path).parent)
            if parent not in seen:
                seen[parent] = Folder(
                    path=parent,
                    name=Path(parent).name,
                    comic_count=0,
                    cover_path=overrides.get(parent) or c.cover_path or None,
                )
            seen[parent].comic_count += 1
            if seen[parent].cover_path is None and c.cover_path:
                seen[parent].cover_path = c.cover_path
        return sorted(
            [f for f in seen.values() if f.comic_count >= 1],
            key=lambda f: f.name.lower(),
        )

    def get_comics_in_shelf_for_folder(
        self, shelf_id: int, folder_path: str, *, sort_by: str = "title", order: str = "asc"
    ) -> list[Comic]:
        """Return comics on a shelf that belong to a specific folder."""
        comics = self.get_comics_in_shelf(shelf_id, sort_by=sort_by, order=order)
        return [c for c in comics if str(Path(c.file_path).parent) == folder_path]

    def get_shelves_for_comic(self, comic_id: int) -> list[Shelf]:
        """Return manual shelves this comic belongs to."""
        rows = self._conn.execute(
            "SELECT s.* FROM shelves s"
            " JOIN comic_shelves cs ON s.id = cs.shelf_id"
            " WHERE cs.comic_id = ? AND s.kind = 'manual'",
            (comic_id,),
        ).fetchall()
        return [
            Shelf(id=r["id"], name=r["name"], kind=r["kind"],
                  smart_key=r["smart_key"], sort_order=r["sort_order"])
            for r in rows
        ]

    def export_shelf(self, shelf_id: int, output_path: str | Path) -> dict:
        """Export one manual shelf as a shareable list, not the comic files."""
        shelf = self._conn.execute(
            "SELECT * FROM shelves WHERE id = ? AND kind = 'manual'", (shelf_id,)
        ).fetchone()
        if shelf is None:
            raise ValueError("Shelf not found or is not a manual shelf.")
        comics = self.get_comics_in_shelf(shelf_id)
        payload = {
            "format": "comic-reader-shelf",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "shelf_name": shelf["name"],
            "comics": [
                {
                    "title": c.title,
                    "series": c.series,
                    "series_number": c.series_number,
                    "author": c.author,
                    "content_hash": c.content_hash,
                    "file_path": c.file_path,
                }
                for c in comics
            ],
        }
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {"shelf_name": shelf["name"], "comics": len(comics)}

    def import_shelf(self, input_path: str | Path) -> dict:
        """Import a shared shelf by matching comics already in this library."""
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if payload.get("format") != "comic-reader-shelf":
            raise ValueError("This is not a Comic Reader shelf export.")
        name = str(payload.get("shelf_name") or "Imported Shelf").strip()
        shelf_id = self.create_shelf(name)
        matched = 0
        unmatched = 0
        with self.transaction() as cur:
            for item in payload.get("comics", []):
                comic_id = self._match_shared_shelf_comic(cur, item)
                if comic_id is None:
                    unmatched += 1
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO comic_shelves (comic_id, shelf_id)"
                    " VALUES (?, ?)",
                    (comic_id, shelf_id),
                )
                matched += 1
        return {
            "shelf_id": shelf_id,
            "shelf_name": name,
            "matched": matched,
            "unmatched": unmatched,
        }

    def _match_shared_shelf_comic(self, cur, item: dict) -> int | None:
        content_hash = item.get("content_hash")
        if content_hash:
            row = cur.execute(
                "SELECT id FROM comics WHERE hidden = 0 AND content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if row:
                return row["id"]
        title = item.get("title")
        series = item.get("series")
        series_number = item.get("series_number")
        if not (title and series):
            return None
        row = cur.execute(
            """
            SELECT id FROM comics
            WHERE hidden = 0
              AND title = ?
              AND series = ?
              AND (
                    (? IS NULL AND series_number IS NULL)
                    OR series_number = ?
                  )
            LIMIT 1
            """,
            (title, series, series_number, series_number),
        ).fetchone()
        return row["id"] if row else None

    # ----- Reading queue ("read next") -----

    def add_to_queue(self, comic_id: int) -> None:
        """Append a comic to the end of the reading queue (no-op if already in it)."""
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            row = cur.execute(
                "SELECT MAX(position) AS m FROM reading_queue"
            ).fetchone()
            next_pos = (row["m"] + 1) if row["m"] is not None else 0
            cur.execute(
                "INSERT OR IGNORE INTO reading_queue (comic_id, position, added_at)"
                " VALUES (?, ?, ?)",
                (comic_id, next_pos, now),
            )

    def remove_from_queue(self, comic_id: int) -> None:
        """Remove a comic from the queue and renumber so positions stay contiguous."""
        with self.transaction() as cur:
            cur.execute("DELETE FROM reading_queue WHERE comic_id = ?", (comic_id,))
            self._renumber_queue(cur)

    def is_in_queue(self, comic_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM reading_queue WHERE comic_id = ?", (comic_id,)
        ).fetchone()
        return row is not None

    def get_queue(self) -> list[Comic]:
        """Return queued comics in manual order (hidden comics excluded)."""
        rows = self._conn.execute(
            "SELECT c.* FROM comics c"
            " JOIN reading_queue q ON c.id = q.comic_id"
            " WHERE c.hidden = 0"
            " ORDER BY q.position"
        ).fetchall()
        return [_row_to_comic(r) for r in rows]

    def move_up(self, comic_id: int) -> None:
        """Swap this comic with the one above it in the queue."""
        self._swap_with_neighbor(comic_id, -1)

    def move_down(self, comic_id: int) -> None:
        """Swap this comic with the one below it in the queue."""
        self._swap_with_neighbor(comic_id, +1)

    def _swap_with_neighbor(self, comic_id: int, direction: int) -> None:
        rows = self._conn.execute(
            "SELECT comic_id, position FROM reading_queue ORDER BY position"
        ).fetchall()
        order = [r["comic_id"] for r in rows]
        if comic_id not in order:
            return
        i = order.index(comic_id)
        j = i + direction
        if j < 0 or j >= len(order):
            return
        order[i], order[j] = order[j], order[i]
        with self.transaction() as cur:
            for pos, cid in enumerate(order):
                cur.execute(
                    "UPDATE reading_queue SET position = ? WHERE comic_id = ?",
                    (pos, cid),
                )

    def _renumber_queue(self, cur) -> None:
        """Rewrite positions to 0,1,2… preserving current order."""
        rows = cur.execute(
            "SELECT comic_id FROM reading_queue ORDER BY position"
        ).fetchall()
        for pos, r in enumerate(rows):
            cur.execute(
                "UPDATE reading_queue SET position = ? WHERE comic_id = ?",
                (pos, r["comic_id"]),
            )

    # ----- Tag CRUD -----

    def get_all_tags(self) -> list[str]:
        """Return all tag names sorted alphabetically."""
        rows = self._conn.execute(
            "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [r["name"] for r in rows]

    def get_tags_for_comic(self, comic_id: int) -> list[str]:
        """Return tags for a comic, sorted alphabetically."""
        rows = self._conn.execute(
            "SELECT t.name FROM tags t"
            " JOIN comic_tags ct ON t.id = ct.tag_id"
            " WHERE ct.comic_id = ? ORDER BY t.name COLLATE NOCASE",
            (comic_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def set_tags_for_comic(self, comic_id: int, tag_names: list[str]) -> None:
        """Replace all tags for a comic. Creates new tags as needed; prunes orphans."""
        normalized = [n.strip() for n in tag_names if n.strip()]
        with self.transaction() as cur:
            cur.execute("DELETE FROM comic_tags WHERE comic_id = ?", (comic_id,))
            for name in normalized:
                cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                row = cur.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()
                cur.execute(
                    "INSERT OR IGNORE INTO comic_tags (comic_id, tag_id) VALUES (?, ?)",
                    (comic_id, row["id"]),
                )
        self._prune_unused_tags()

    def _prune_unused_tags(self) -> None:
        with self.transaction() as cur:
            cur.execute(
                "DELETE FROM tags WHERE id NOT IN"
                " (SELECT DISTINCT tag_id FROM comic_tags)"
            )

    # ----- Bookmark CRUD -----

    def toggle_bookmark(self, comic_id: int, page_index: int, label: str | None = None) -> bool:
        """Toggle a bookmark on a page. Returns True if added, False if removed."""
        existing = self._conn.execute(
            "SELECT id FROM bookmarks WHERE comic_id = ? AND page_index = ?",
            (comic_id, page_index),
        ).fetchone()
        if existing:
            with self.transaction() as cur:
                cur.execute("DELETE FROM bookmarks WHERE id = ?", (existing["id"],))
            return False
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO bookmarks (comic_id, page_index, label, created_at)"
                " VALUES (?, ?, ?, ?)",
                (comic_id, page_index, label, now),
            )
        return True

    def get_bookmarks(self, comic_id: int) -> list[Bookmark]:
        rows = self._conn.execute(
            "SELECT * FROM bookmarks WHERE comic_id = ? ORDER BY page_index",
            (comic_id,),
        ).fetchall()
        return [
            Bookmark(
                id=r["id"],
                comic_id=r["comic_id"],
                page_index=r["page_index"],
                label=r["label"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def is_bookmarked(self, comic_id: int, page_index: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM bookmarks WHERE comic_id = ? AND page_index = ?",
            (comic_id, page_index),
        ).fetchone()
        return row is not None

    # ----- Annotation CRUD -----

    def add_annotation(self, comic_id: int, page_index: int, body: str) -> int:
        body = body.strip()
        if not body:
            raise ValueError("Annotation body cannot be empty.")
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO annotations"
                " (comic_id, page_index, body, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (comic_id, page_index, body, now, now),
            )
            return int(cur.lastrowid)

    def update_annotation(self, annotation_id: int, body: str) -> None:
        body = body.strip()
        if not body:
            raise ValueError("Annotation body cannot be empty.")
        now = datetime.now(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                "UPDATE annotations SET body = ?, updated_at = ? WHERE id = ?",
                (body, now, annotation_id),
            )

    def delete_annotation(self, annotation_id: int) -> None:
        with self.transaction() as cur:
            cur.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))

    def get_annotations(self, comic_id: int) -> list[Annotation]:
        rows = self._conn.execute(
            "SELECT * FROM annotations WHERE comic_id = ? ORDER BY page_index, id",
            (comic_id,),
        ).fetchall()
        return [
            Annotation(
                id=r["id"],
                comic_id=r["comic_id"],
                page_index=r["page_index"],
                body=r["body"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def get_annotation_for_page(
        self, comic_id: int, page_index: int
    ) -> Annotation | None:
        row = self._conn.execute(
            "SELECT * FROM annotations"
            " WHERE comic_id = ? AND page_index = ?"
            " ORDER BY updated_at DESC, id DESC LIMIT 1",
            (comic_id, page_index),
        ).fetchone()
        if row is None:
            return None
        return Annotation(
            id=row["id"],
            comic_id=row["comic_id"],
            page_index=row["page_index"],
            body=row["body"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ----- Per-comic reading settings -----

    def set_reading_mode(self, comic_id: int, mode: str) -> None:
        if mode not in ("single", "webtoon"):
            raise ValueError(f"Invalid reading_mode: {mode!r}")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET reading_mode = ? WHERE id = ?", (mode, comic_id)
            )

    def set_is_manga(self, comic_id: int, is_manga: bool) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET is_manga = ? WHERE id = ?",
                (1 if is_manga else 0, comic_id),
            )

    def set_fit_mode(self, comic_id: int, fit_mode: str) -> None:
        if fit_mode not in ("actual", "width", "page"):
            raise ValueError(f"Invalid fit_mode: {fit_mode!r}")
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET fit_mode = ? WHERE id = ?", (fit_mode, comic_id)
            )

    def set_zoom(self, comic_id: int, zoom: float) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET zoom = ? WHERE id = ?", (float(zoom), comic_id)
            )

    def get_comics_with_tag(
        self, tag_name: str, *, sort_by: str = "title", order: str = "asc"
    ) -> list[Comic]:
        """Return all comics that have the given tag."""
        if sort_by not in _VALID_SORT_COLUMNS:
            sort_by = "title"
        rows = self._conn.execute(
            "SELECT c.* FROM comics c"
            " JOIN comic_tags ct ON c.id = ct.comic_id"
            " JOIN tags t ON ct.tag_id = t.id"
            " WHERE t.name = ? COLLATE NOCASE AND c.hidden = 0",
            (tag_name,),
        ).fetchall()
        return _sort_comics([_row_to_comic(r) for r in rows], sort_by, order)

    # ----- Library export / import -----

    def get_source_folders(self) -> list[str]:
        """Return distinct library roots that were added through the scanner."""
        rows = self._conn.execute(
            "SELECT DISTINCT source_folder FROM comics"
            " WHERE source_folder IS NOT NULL AND source_folder != ''"
            " ORDER BY source_folder COLLATE NOCASE"
        ).fetchall()
        return [r["source_folder"] for r in rows]

    def export_to_json(self, output_path: str | Path) -> dict:
        """Write a portable JSON snapshot of library metadata."""
        payload = {
            "format": "comic-reader-library",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_folders": self.get_source_folders(),
            "comics": [],
            "shelves": [],
            "folder_covers": [],
        }

        comics = self._conn.execute("SELECT * FROM comics ORDER BY file_path").fetchall()
        for row in comics:
            comic_id = row["id"]
            payload["comics"].append({
                "file_path": row["file_path"],
                "content_hash": row["content_hash"],
                "title": row["title"],
                "series": row["series"],
                "series_number": row["series_number"],
                "author": row["author"],
                "publisher": row["publisher"],
                "year": row["year"],
                "page_count": row["page_count"],
                "file_size": row["file_size"],
                "cover_path": row["cover_path"],
                "date_added": row["date_added"],
                "last_read": row["last_read"],
                "current_page": row["current_page"],
                "read_status": row["read_status"],
                "source_folder": row["source_folder"],
                "parent_dir": row["parent_dir"],
                "is_manga": bool(row["is_manga"]),
                "reading_mode": row["reading_mode"],
                "hidden": bool(row["hidden"]),
                "fit_mode": row["fit_mode"],
                "zoom": float(row["zoom"]),
                "cover_override": bool(row["cover_override"]),
                "tags": self.get_tags_for_comic(comic_id),
                "bookmarks": [
                    {
                        "page_index": b.page_index,
                        "label": b.label,
                        "created_at": b.created_at,
                    }
                    for b in self.get_bookmarks(comic_id)
                ],
                "annotations": [
                    {
                        "page_index": a.page_index,
                        "body": a.body,
                        "created_at": a.created_at,
                        "updated_at": a.updated_at,
                    }
                    for a in self.get_annotations(comic_id)
                ],
            })

        shelves = self._conn.execute(
            "SELECT * FROM shelves WHERE kind = 'manual' ORDER BY name COLLATE NOCASE"
        ).fetchall()
        for shelf in shelves:
            members = self._conn.execute(
                "SELECT c.file_path FROM comics c"
                " JOIN comic_shelves cs ON c.id = cs.comic_id"
                " WHERE cs.shelf_id = ? ORDER BY cs.position, c.file_path",
                (shelf["id"],),
            ).fetchall()
            payload["shelves"].append({
                "name": shelf["name"],
                "sort_order": shelf["sort_order"],
                "created_at": shelf["created_at"],
                "comic_paths": [m["file_path"] for m in members],
            })

        folder_covers = self._conn.execute(
            "SELECT folder_path, cover_path FROM folder_covers ORDER BY folder_path"
        ).fetchall()
        payload["folder_covers"] = [
            {"folder_path": r["folder_path"], "cover_path": r["cover_path"]}
            for r in folder_covers
        ]

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return {
            "comics": len(payload["comics"]),
            "shelves": len(payload["shelves"]),
            "folder_covers": len(payload["folder_covers"]),
        }

    def import_from_json(self, input_path: str | Path) -> dict:
        """Merge a library JSON export into the current database by file path."""
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
        if payload.get("format") != "comic-reader-library":
            raise ValueError("This is not a Comic Reader library export.")

        stats = {
            "comics_added": 0,
            "comics_updated": 0,
            "shelves": 0,
            "folder_covers": 0,
        }
        path_to_id: dict[str, int] = {}

        with self.transaction() as cur:
            for item in payload.get("comics", []):
                file_path = item.get("file_path")
                if not file_path:
                    continue
                existing = cur.execute(
                    "SELECT id FROM comics WHERE file_path = ?", (file_path,)
                ).fetchone()
                values = (
                    item.get("content_hash"),
                    item.get("title"),
                    item.get("series"),
                    item.get("series_number"),
                    item.get("author"),
                    item.get("publisher"),
                    item.get("year"),
                    item.get("page_count") or 0,
                    item.get("file_size") or 0,
                    item.get("cover_path"),
                    item.get("date_added") or datetime.now(timezone.utc).isoformat(),
                    item.get("last_read"),
                    item.get("current_page") or 0,
                    item.get("read_status") or "unread",
                    item.get("source_folder"),
                    item.get("parent_dir") or _parent_dir(file_path),
                    1 if item.get("is_manga") else 0,
                    item.get("reading_mode") or "single",
                    1 if item.get("hidden") else 0,
                    item.get("fit_mode") or "page",
                    float(item.get("zoom") or 1.0),
                    1 if item.get("cover_override") else 0,
                )
                if existing:
                    comic_id = existing["id"]
                    cur.execute(
                        """
                        UPDATE comics
                        SET content_hash = ?, title = ?, series = ?, series_number = ?,
                            author = ?, publisher = ?, year = ?, page_count = ?,
                            file_size = ?, cover_path = ?, date_added = ?, last_read = ?,
                            current_page = ?, read_status = ?, source_folder = ?,
                            parent_dir = ?, is_manga = ?, reading_mode = ?, hidden = ?,
                            fit_mode = ?, zoom = ?, cover_override = ?
                        WHERE id = ?
                        """,
                        values + (comic_id,),
                    )
                    stats["comics_updated"] += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO comics
                            (content_hash, title, series, series_number, author,
                             publisher, year, page_count, file_size, cover_path,
                             date_added, last_read, current_page, read_status,
                             source_folder, parent_dir, is_manga, reading_mode,
                             hidden, fit_mode, zoom, cover_override, file_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values + (file_path,),
                    )
                    comic_id = cur.lastrowid
                    stats["comics_added"] += 1
                path_to_id[file_path] = comic_id

                cur.execute("DELETE FROM comic_tags WHERE comic_id = ?", (comic_id,))
                for tag in item.get("tags", []):
                    name = str(tag).strip()
                    if not name:
                        continue
                    cur.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                    tag_row = cur.execute(
                        "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                    ).fetchone()
                    cur.execute(
                        "INSERT OR IGNORE INTO comic_tags (comic_id, tag_id) VALUES (?, ?)",
                        (comic_id, tag_row["id"]),
                    )

                cur.execute("DELETE FROM bookmarks WHERE comic_id = ?", (comic_id,))
                for bm in item.get("bookmarks", []):
                    cur.execute(
                        "INSERT OR IGNORE INTO bookmarks"
                        " (comic_id, page_index, label, created_at) VALUES (?, ?, ?, ?)",
                        (
                            comic_id,
                            bm.get("page_index") or 0,
                            bm.get("label"),
                            bm.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        ),
                    )

                cur.execute("DELETE FROM annotations WHERE comic_id = ?", (comic_id,))
                for note in item.get("annotations", []):
                    body = str(note.get("body") or "").strip()
                    if not body:
                        continue
                    created = note.get("created_at") or datetime.now(timezone.utc).isoformat()
                    updated = note.get("updated_at") or created
                    cur.execute(
                        "INSERT INTO annotations"
                        " (comic_id, page_index, body, created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (
                            comic_id,
                            note.get("page_index") or 0,
                            body,
                            created,
                            updated,
                        ),
                    )

            for shelf in payload.get("shelves", []):
                name = str(shelf.get("name", "")).strip()
                if not name:
                    continue
                row = cur.execute(
                    "SELECT id FROM shelves WHERE name = ? AND kind = 'manual'",
                    (name,),
                ).fetchone()
                if row:
                    shelf_id = row["id"]
                else:
                    cur.execute(
                        "INSERT INTO shelves (name, kind, sort_order, created_at)"
                        " VALUES (?, 'manual', ?, ?)",
                        (
                            name,
                            shelf.get("sort_order") or 999,
                            shelf.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    shelf_id = cur.lastrowid
                stats["shelves"] += 1
                for position, comic_path in enumerate(shelf.get("comic_paths", [])):
                    comic_id = path_to_id.get(comic_path)
                    if comic_id is None:
                        row = cur.execute(
                            "SELECT id FROM comics WHERE file_path = ?", (comic_path,)
                        ).fetchone()
                        comic_id = row["id"] if row else None
                    if comic_id is None:
                        continue
                    cur.execute(
                        "INSERT OR REPLACE INTO comic_shelves (comic_id, shelf_id, position)"
                        " VALUES (?, ?, ?)",
                        (comic_id, shelf_id, position),
                    )

            for cover in payload.get("folder_covers", []):
                folder_path = cover.get("folder_path")
                cover_path = cover.get("cover_path")
                if not folder_path or not cover_path:
                    continue
                cur.execute(
                    "INSERT INTO folder_covers (folder_path, cover_path) VALUES (?, ?)"
                    " ON CONFLICT(folder_path) DO UPDATE SET cover_path = excluded.cover_path",
                    (folder_path, cover_path),
                )
                stats["folder_covers"] += 1

        self._prune_unused_tags()
        return stats


if __name__ == "__main__":
    import tempfile

    # Smoke test — in-memory DB, no real comic files needed.
    lib = Library(db_path=":memory:")

    id1 = lib.add_comic("/comics/batman.cbz", page_count=24, file_size=10_000_000, title="Batman #1", series="Batman", source_folder="/comics")
    id2 = lib.add_comic("/comics/xmen.cbz",   page_count=22, file_size=8_000_000,  title="X-Men #1",  series="X-Men",   source_folder="/comics")

    assert lib.comic_exists("/comics/batman.cbz")
    assert not lib.comic_exists("/comics/missing.cbz")
    lib.update_file_path(id2, "/renamed/xmen-renamed.cbz")
    assert not lib.comic_exists("/comics/xmen.cbz")
    assert lib.comic_exists("/renamed/xmen-renamed.cbz")
    assert lib._conn.execute(
        "SELECT parent_dir FROM comics WHERE id = ?", (id2,)
    ).fetchone()["parent_dir"] == "/renamed"
    lib.update_file_path(id2, "/comics/xmen.cbz")

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

    # Shelf tests
    shelves = lib.get_shelves()
    assert any(s.kind == "smart" and s.smart_key == "recently_added" for s in shelves)

    shelf_id = lib.create_shelf("Favorites")
    assert lib.get_shelves()[-1].name == "Favorites"

    lib.add_comic_to_shelf(id1, shelf_id)
    shelf_comics = lib.get_comics_in_shelf(shelf_id)
    assert len(shelf_comics) == 1 and shelf_comics[0].id == id1

    comic_shelves = lib.get_shelves_for_comic(id1)
    assert any(s.id == shelf_id for s in comic_shelves)

    lib.remove_comic_from_shelf(id1, shelf_id)
    assert len(lib.get_comics_in_shelf(shelf_id)) == 0

    lib.rename_shelf(shelf_id, "Classics")
    assert lib.get_shelves()[-1].name == "Classics"

    lib.delete_shelf(shelf_id)
    assert all(s.kind == "smart" for s in lib.get_shelves())

    # Tag tests
    lib.set_tags_for_comic(id1, ["Action", "classic", "superhero"])
    tags = lib.get_tags_for_comic(id1)
    assert tags == ["Action", "classic", "superhero"]
    assert lib.get_all_tags() == ["Action", "classic", "superhero"]

    lib.set_tags_for_comic(id1, ["classic", "superhero"])  # remove Action
    assert lib.get_all_tags() == ["classic", "superhero"]  # Action pruned

    tagged = lib.get_comics_with_tag("superhero")
    assert len(tagged) == 1 and tagged[0].id == id1

    lib.set_tags_for_comic(id1, [])  # clear all
    assert lib.get_tags_for_comic(id1) == []
    assert lib.get_all_tags() == []

    # Bookmark tests
    assert not lib.is_bookmarked(id1, 5)
    added = lib.toggle_bookmark(id1, 5, "Fight scene")
    assert added is True
    assert lib.is_bookmarked(id1, 5)
    bms = lib.get_bookmarks(id1)
    assert len(bms) == 1 and bms[0].page_index == 5 and bms[0].label == "Fight scene"
    removed = lib.toggle_bookmark(id1, 5)
    assert removed is False
    assert not lib.is_bookmarked(id1, 5)

    # Reading mode and manga tests
    lib.set_reading_mode(id1, "webtoon")
    assert lib.get_comic_by_id(id1).reading_mode == "webtoon"
    lib.set_reading_mode(id1, "single")
    assert lib.get_comic_by_id(id1).reading_mode == "single"

    lib.set_is_manga(id1, True)
    assert lib.get_comic_by_id(id1).is_manga is True
    lib.set_is_manga(id1, False)
    assert lib.get_comic_by_id(id1).is_manga is False

    # Fit mode and zoom tests
    assert lib.get_comic_by_id(id1).fit_mode == "page"
    assert lib.get_comic_by_id(id1).zoom == 1.0
    lib.set_fit_mode(id1, "width")
    assert lib.get_comic_by_id(id1).fit_mode == "width"
    lib.set_zoom(id1, 1.5)
    assert lib.get_comic_by_id(id1).zoom == 1.5
    lib.set_fit_mode(id1, "page")
    lib.set_zoom(id1, 1.0)

    # Cover override flag
    assert lib.get_comic_by_id(id1).cover_override is False
    lib.set_cover_override(id1, True)
    assert lib.get_comic_by_id(id1).cover_override is True
    lib.set_cover_override(id1, False)
    assert lib.get_comic_by_id(id1).cover_override is False

    # Duplicate detection
    assert lib.find_duplicate_groups() == []
    dup_a = lib.add_comic("/comics/dupe-a.cbz", page_count=10, file_size=100)
    dup_b = lib.add_comic("/comics/dupe-b.cbz", page_count=10, file_size=100)
    lonely = lib.add_comic("/comics/unique.cbz", page_count=10, file_size=100)
    assert any(c.id == dup_a for c in lib.get_unhashed_comics())
    lib.set_content_hash(dup_a, "SAMEHASH")
    lib.set_content_hash(dup_b, "SAMEHASH")
    lib.set_content_hash(lonely, "OTHERHASH")
    groups = lib.find_duplicate_groups()
    assert len(groups) == 1
    assert {c.id for c in groups[0]} == {dup_a, dup_b}
    lib.set_hidden(dup_b, True)  # hiding one copy removes the group
    assert lib.find_duplicate_groups() == []
    for cid in (dup_a, dup_b, lonely):
        lib.set_hidden(cid, False)
        lib.remove_comic(cid)

    # Reading statistics
    stats = lib.get_stats()
    assert stats["total_pages"] == 0 and stats["current_streak"] == 0
    lib.record_reading(id1, pages_read=5, seconds=120)
    lib.record_reading(id1, pages_read=3, seconds=60)  # accumulates into same day
    lib.record_reading(id1, pages_read=0, seconds=0)   # no-op
    stats = lib.get_stats()
    assert stats["total_pages"] == 8
    assert abs(stats["total_hours"] - 180 / 3600) < 1e-9
    assert stats["current_streak"] == 1
    # Add an event for yesterday to exercise streak + per-day series
    _yday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    lib._conn.execute(
        "INSERT INTO reading_events (comic_id, event_date, pages_read, seconds)"
        " VALUES (?, ?, ?, ?)",
        (id1, _yday, 4, 30),
    )
    lib._conn.commit()
    stats = lib.get_stats()
    assert stats["total_pages"] == 12
    assert stats["current_streak"] == 2
    assert len(stats["pages_per_day"]) == 30
    assert stats["pages_per_day"][-1][1] == 8   # today
    assert stats["pages_per_day"][-2][1] == 4   # yesterday

    # Hide / restore tests
    id3 = lib.add_comic("/comics/spawn.cbz", page_count=20, file_size=5_000_000, title="Spawn #1")
    assert len(lib.get_all_comics()) == 2  # id1 + id3 (id2 removed earlier)
    lib.set_hidden(id3, True)
    assert lib.get_comic_by_id(id3).hidden is True
    assert all(c.id != id3 for c in lib.get_all_comics())          # hidden out of library
    assert any(c.id == id3 for c in lib.get_hidden_comics())        # but in restore view
    lib.set_hidden(id3, False)
    assert any(c.id == id3 for c in lib.get_all_comics())           # restored

    # Hiding a whole folder hides every comic in it
    lib.hide_folder("/comics")
    assert lib.get_all_comics() == []
    assert len(lib.get_hidden_comics()) == 2
    for c in lib.get_hidden_comics():
        lib.set_hidden(c.id, False)
    assert len(lib.get_all_comics()) == 2

    # Folder cover override
    assert lib.get_folder_cover("/comics") is None
    lib.set_folder_cover("/comics", "/cache/custom.jpg")
    assert lib.get_folder_cover("/comics") == "/cache/custom.jpg"
    assert next(f for f in lib.get_folders() if f.path == "/comics").cover_path == "/cache/custom.jpg"
    lib.clear_folder_cover("/comics")
    assert lib.get_folder_cover("/comics") is None

    # Export / import merge test
    lib.set_folder_cover("/comics", "/cache/custom.jpg")
    lib.set_tags_for_comic(id1, ["classic"])
    lib.toggle_bookmark(id1, 3, "Opening")
    note_id = lib.add_annotation(id1, 4, "Check this panel")
    assert lib.get_annotation_for_page(id1, 4).body == "Check this panel"
    lib.update_annotation(note_id, "Updated note")
    assert lib.get_annotation_for_page(id1, 4).body == "Updated note"
    assert len(lib.get_annotations(id1)) == 1
    lib.set_fit_mode(id1, "width")
    lib.set_zoom(id1, 1.5)
    lib.set_cover_override(id1, True)
    shelf_id = lib.create_shelf("Export Shelf")
    lib.add_comic_to_shelf(id1, shelf_id)
    with tempfile.TemporaryDirectory() as tmp:
        export_path = Path(tmp) / "library.json"
        stats = lib.export_to_json(export_path)
        assert stats["comics"] == 2

        imported = Library(db_path=":memory:")
        import_stats = imported.import_from_json(export_path)
        assert import_stats["comics_added"] == 2
        imported_comic = imported.get_comic("/comics/batman.cbz")
        assert imported_comic is not None
        assert imported.get_tags_for_comic(imported_comic.id) == ["classic"]
        assert len(imported.get_bookmarks(imported_comic.id)) == 1
        imported_notes = imported.get_annotations(imported_comic.id)
        assert len(imported_notes) == 1
        assert imported_notes[0].page_index == 4
        assert imported_notes[0].body == "Updated note"
        assert any(s.name == "Export Shelf" for s in imported.get_shelves())
        assert imported.get_folder_cover("/comics") == "/cache/custom.jpg"
        assert imported_comic.fit_mode == "width"
        assert imported_comic.zoom == 1.5
        assert imported_comic.cover_override is True
        import_stats = imported.import_from_json(export_path)
        assert import_stats["comics_added"] == 0
        assert import_stats["comics_updated"] == 2
        imported.close()

    # Shared shelf export/import (Item 43)
    lib.set_content_hash(id1, "hash-batman")
    shared_shelf = lib.create_shelf("Shared Picks")
    lib.add_comic_to_shelf(id1, shared_shelf)
    with tempfile.TemporaryDirectory() as tmp:
        shelf_path = Path(tmp) / "shared-shelf.json"
        shelf_stats = lib.export_shelf(shared_shelf, shelf_path)
        assert shelf_stats["comics"] == 1
        payload = json.loads(shelf_path.read_text(encoding="utf-8"))
        payload["comics"].append({
            "title": "Missing Book",
            "series": "Missing",
            "series_number": 1,
            "author": None,
            "content_hash": "not-present",
            "file_path": "/elsewhere/missing.cbz",
        })
        shelf_path.write_text(json.dumps(payload), encoding="utf-8")

        recipient = Library(db_path=":memory:")
        rid = recipient.add_comic(
            "/recipient/batman-copy.cbz",
            page_count=24,
            file_size=10_000_000,
            title="Different local title",
        )
        recipient.set_content_hash(rid, "hash-batman")
        result = recipient.import_shelf(shelf_path)
        assert result["matched"] == 1
        assert result["unmatched"] == 1
        imported_shelf = next(s for s in recipient.get_shelves() if s.name == "Shared Picks")
        assert [c.id for c in recipient.get_comics_in_shelf(imported_shelf.id)] == [rid]
        recipient.close()

    # Reading queue tests (Item 35)
    q1 = lib.add_comic("/comics/q1.cbz", page_count=10, file_size=1_000, title="Queue 1")
    q2 = lib.add_comic("/comics/q2.cbz", page_count=10, file_size=2_000, title="Queue 2")
    q3 = lib.add_comic("/comics/q3.cbz", page_count=10, file_size=3_000, title="Queue 3")
    lib.add_to_queue(q1)
    lib.add_to_queue(q2)
    lib.add_to_queue(q3)
    assert [c.id for c in lib.get_queue()] == [q1, q2, q3]   # append order
    assert lib.is_in_queue(q2) is True
    lib.add_to_queue(q2)                                      # duplicate is a no-op
    assert [c.id for c in lib.get_queue()] == [q1, q2, q3]
    lib.move_up(q3)                                           # q3 swaps above q2
    assert [c.id for c in lib.get_queue()] == [q1, q3, q2]
    lib.move_down(q1)                                         # q1 swaps below q3
    assert [c.id for c in lib.get_queue()] == [q3, q1, q2]
    lib.move_up(q3)                                           # already top — no change
    assert [c.id for c in lib.get_queue()] == [q3, q1, q2]
    lib.remove_from_queue(q1)                                 # positions stay contiguous
    assert [c.id for c in lib.get_queue()] == [q3, q2]
    assert lib.is_in_queue(q1) is False
    qpos = lib._conn.execute(
        "SELECT position FROM reading_queue ORDER BY position"
    ).fetchall()
    assert [r["position"] for r in qpos] == [0, 1]
    lib.set_hidden(q2, True)                                  # hidden comics drop out of the queue view
    assert [c.id for c in lib.get_queue()] == [q3]
    lib.set_hidden(q2, False)

    lib.delete_annotation(note_id)
    assert lib.get_annotation_for_page(id1, 4) is None

    lib.close()
    print("library.py smoke test: OK")
