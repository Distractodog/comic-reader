"""Local SQLite library database — tracks comics, reading progress, and metadata."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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

_CURRENT_VERSION = 7

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

    def set_content_hash(self, comic_id: int, content_hash: str) -> None:
        with self.transaction() as cur:
            cur.execute(
                "UPDATE comics SET content_hash = ? WHERE id = ?",
                (content_hash, comic_id),
            )

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

    lib.close()
    print("library.py smoke test: OK")
