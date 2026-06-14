"""MCP server exposing the Cover 2.0 / Comic Reader library as callable tools.

This is a thin wrapper over `src/library.py` — every tool maps to an existing
`Library` method, so it can only do what the app's own UI can do. It opens the
SAME SQLite database the app uses (resolved via the app's org/app names, not a
hardcoded path), so changes here show up in the app after a refresh/relaunch.

Run by Claude via .mcp.json; not part of the shipped PyInstaller app.

SAFETY: the app keeps the DB open while running. SQLite WAL tolerates this, but
the app won't see writes until it reloads. Prefer to quit Cover 2.0 before bulk
edits. Use `app_status` to check whether the app is currently running.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Make `src/` importable so we reuse the app's own library code verbatim.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# Resolve the DB to the exact location the app uses. _default_db_path() in
# library.py asks Qt for the AppDataLocation, which only points at
# ".../ComicReader/Comic Reader/" when the org + app names are set first.
from PyQt6.QtCore import QCoreApplication  # noqa: E402
from app_info import APP_INTERNAL_NAME, APP_ORGANIZATION  # noqa: E402

QCoreApplication.setOrganizationName(APP_ORGANIZATION)
QCoreApplication.setApplicationName(APP_INTERNAL_NAME)

from library import Library, _default_db_path  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("comic-library")

_DB_PATH = _default_db_path()


def _lib() -> Library:
    """Open the library with a generous busy timeout so a momentarily-locked
    DB (app writing) retries instead of erroring."""
    lib = Library(_DB_PATH)
    lib._conn.execute("PRAGMA busy_timeout = 4000")
    return lib


def _app_is_running() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "src/main.py"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except Exception:
        return False


def _title(c) -> str:
    return c.title or Path(c.file_path).stem


def _brief(c) -> dict:
    """Compact comic summary — used in list results to keep responses small."""
    return {
        "id": c.id,
        "title": _title(c),
        "series": c.series,
        "series_number": c.series_number,
        "read_status": c.read_status,
        "page_count": c.page_count,
        "current_page": c.current_page,
        "file_path": c.file_path,
    }


def _full(c) -> dict:
    return {
        **_brief(c),
        "author": c.author,
        "publisher": c.publisher,
        "year": c.year,
        "folder": str(Path(c.file_path).parent),
        "date_added": c.date_added,
        "last_read": c.last_read,
        "cover_path": c.cover_path,
        "cover_override": c.cover_override,
        "is_manga": c.is_manga,
        "reading_mode": c.reading_mode,
        "hidden": c.hidden,
    }


# --------------------------------------------------------------------------- #
# Status                                                                       #
# --------------------------------------------------------------------------- #

@mcp.tool()
def app_status() -> dict:
    """Report the database path and whether the Cover 2.0 app is currently
    running. Check this before bulk edits — writes made while the app is open
    won't appear until it is reloaded."""
    with _lib() as lib:
        comics = lib.get_all_comics()
        folders = lib.get_folders()
    return {
        "db_path": str(_DB_PATH),
        "app_running": _app_is_running(),
        "comic_count": len(comics),
        "folder_count": len(folders),
        "advice": (
            "App is running — quit Cover 2.0 (Cmd+Q) before bulk edits, or "
            "reload it afterward to see changes."
            if _app_is_running() else
            "App not running — safe to edit."
        ),
    }


# --------------------------------------------------------------------------- #
# Browsing (read-only)                                                         #
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_folders() -> list[dict]:
    """List every folder (library shelf row) with its comic count and cover."""
    with _lib() as lib:
        return [
            {"path": f.path, "name": f.name, "comic_count": f.comic_count,
             "cover_path": f.cover_path}
            for f in lib.get_folders()
        ]


@mcp.tool()
def list_comics_in_folder(folder_path: str) -> list[dict]:
    """List comics whose files live directly in the given folder path."""
    with _lib() as lib:
        return [_brief(c) for c in lib.get_comics_in_folder(folder_path)]


@mcp.tool()
def get_comic(comic_id: int) -> dict:
    """Full metadata for one comic by id."""
    with _lib() as lib:
        c = lib.get_comic_by_id(comic_id)
        if c is None:
            return {"error": f"No comic with id {comic_id}"}
        return _full(c)


@mcp.tool()
def search(query: str) -> dict:
    """Search the library by title, series, author, filename, or tag. Returns
    matching folders and comics."""
    with _lib() as lib:
        folders, comics = lib.search_library(query)
    return {
        "folders": [{"path": f.path, "name": f.name,
                     "comic_count": f.comic_count} for f in folders],
        "comics": [_brief(c) for c in comics],
    }


@mcp.tool()
def list_shelves() -> list[dict]:
    """List all shelves (smart + manual) with their ids."""
    with _lib() as lib:
        return [
            {"id": s.id, "name": s.name, "kind": s.kind, "smart_key": s.smart_key}
            for s in lib.get_shelves()
        ]


@mcp.tool()
def list_comics_in_shelf(shelf_id: int) -> list[dict]:
    """List comics on a shelf (works for smart and manual shelves)."""
    with _lib() as lib:
        return [_brief(c) for c in lib.get_comics_in_shelf(shelf_id)]


@mcp.tool()
def list_tags() -> list[str]:
    """All tag names in the library, alphabetical."""
    with _lib() as lib:
        return lib.get_all_tags()


@mcp.tool()
def get_tags_for_comic(comic_id: int) -> list[str]:
    """Tags assigned to one comic."""
    with _lib() as lib:
        return lib.get_tags_for_comic(comic_id)


@mcp.tool()
def get_bookmarks(comic_id: int) -> list[dict]:
    """Bookmarks for one comic."""
    with _lib() as lib:
        return [
            {"id": b.id, "page_index": b.page_index, "label": b.label,
             "created_at": b.created_at}
            for b in lib.get_bookmarks(comic_id)
        ]


# --------------------------------------------------------------------------- #
# Metadata editing                                                             #
# --------------------------------------------------------------------------- #

@mcp.tool()
def update_metadata(
    comic_id: int,
    title: str | None = None,
    series: str | None = None,
    series_number: float | None = None,
    author: str | None = None,
    publisher: str | None = None,
    year: int | None = None,
) -> dict:
    """Update one or more metadata fields on a comic. Only provided fields
    change. Pass an empty string to clear a text field."""
    fields = {
        k: v for k, v in {
            "title": title, "series": series, "series_number": series_number,
            "author": author, "publisher": publisher, "year": year,
        }.items() if v is not None
    }
    if not fields:
        return {"error": "No fields provided to update."}
    with _lib() as lib:
        if lib.get_comic_by_id(comic_id) is None:
            return {"error": f"No comic with id {comic_id}"}
        lib.update_metadata(comic_id, **fields)
        return {"ok": True, "comic": _full(lib.get_comic_by_id(comic_id))}


@mcp.tool()
def set_read_status(comic_id: int, status: str) -> dict:
    """Set read status: one of 'unread', 'in_progress', 'read'."""
    if status not in ("unread", "in_progress", "read"):
        return {"error": "status must be 'unread', 'in_progress', or 'read'"}
    with _lib() as lib:
        if lib.get_comic_by_id(comic_id) is None:
            return {"error": f"No comic with id {comic_id}"}
        lib.set_read_status(comic_id, status)
        return {"ok": True, "comic_id": comic_id, "read_status": status}


@mcp.tool()
def group_comics_as_series(comic_ids: list[int], series_name: str) -> dict:
    """Assign a shared series name to several comics (sets reading order; issues
    stay as separate tiles)."""
    with _lib() as lib:
        lib.group_comics_as_series(comic_ids, series_name)
        return {"ok": True, "series": series_name, "count": len(comic_ids)}


# --------------------------------------------------------------------------- #
# Shelves                                                                      #
# --------------------------------------------------------------------------- #

@mcp.tool()
def create_shelf(name: str) -> dict:
    """Create a new manual shelf. Returns its id."""
    with _lib() as lib:
        shelf_id = lib.create_shelf(name)
        return {"ok": True, "shelf_id": shelf_id, "name": name}


@mcp.tool()
def rename_shelf(shelf_id: int, name: str) -> dict:
    """Rename a manual shelf."""
    with _lib() as lib:
        lib.rename_shelf(shelf_id, name)
        return {"ok": True, "shelf_id": shelf_id, "name": name}


@mcp.tool()
def delete_shelf(shelf_id: int) -> dict:
    """Delete a manual shelf (does not delete the comics)."""
    with _lib() as lib:
        lib.delete_shelf(shelf_id)
        return {"ok": True, "deleted_shelf_id": shelf_id}


@mcp.tool()
def add_comic_to_shelf(comic_id: int, shelf_id: int) -> dict:
    """Add one comic to a manual shelf."""
    with _lib() as lib:
        if lib.get_comic_by_id(comic_id) is None:
            return {"error": f"No comic with id {comic_id}"}
        lib.add_comic_to_shelf(comic_id, shelf_id)
        return {"ok": True, "comic_id": comic_id, "shelf_id": shelf_id}


@mcp.tool()
def add_folder_to_shelf(folder_path: str, shelf_id: int) -> dict:
    """Add every comic in a folder to a manual shelf. Returns how many were
    linked."""
    with _lib() as lib:
        n = lib.add_folder_to_shelf(folder_path, shelf_id)
        return {"ok": True, "folder_path": folder_path, "shelf_id": shelf_id,
                "comics_added": n}


@mcp.tool()
def remove_comic_from_shelf(comic_id: int, shelf_id: int) -> dict:
    """Remove one comic from a manual shelf."""
    with _lib() as lib:
        lib.remove_comic_from_shelf(comic_id, shelf_id)
        return {"ok": True, "comic_id": comic_id, "shelf_id": shelf_id}


# --------------------------------------------------------------------------- #
# Tags                                                                         #
# --------------------------------------------------------------------------- #

@mcp.tool()
def set_tags_for_comic(comic_id: int, tags: list[str]) -> dict:
    """Replace ALL tags on a comic with the given list (creates tags as needed,
    prunes orphans). Pass [] to clear all tags."""
    with _lib() as lib:
        if lib.get_comic_by_id(comic_id) is None:
            return {"error": f"No comic with id {comic_id}"}
        lib.set_tags_for_comic(comic_id, tags)
        return {"ok": True, "comic_id": comic_id,
                "tags": lib.get_tags_for_comic(comic_id)}


# --------------------------------------------------------------------------- #
# Bookmarks                                                                    #
# --------------------------------------------------------------------------- #

@mcp.tool()
def toggle_bookmark(comic_id: int, page_index: int, label: str | None = None) -> dict:
    """Toggle a bookmark on a page (0-based index). Returns whether it is now
    added or removed."""
    with _lib() as lib:
        if lib.get_comic_by_id(comic_id) is None:
            return {"error": f"No comic with id {comic_id}"}
        added = lib.toggle_bookmark(comic_id, page_index, label)
        return {"ok": True, "comic_id": comic_id, "page_index": page_index,
                "bookmarked": added}


# --------------------------------------------------------------------------- #
# Folder covers                                                                #
# --------------------------------------------------------------------------- #

@mcp.tool()
def set_folder_cover(folder_path: str, cover_path: str) -> dict:
    """Set a folder's cover to an image file on disk (absolute path)."""
    if not Path(cover_path).is_file():
        return {"error": f"cover_path does not exist: {cover_path}"}
    with _lib() as lib:
        lib.set_folder_cover(folder_path, cover_path)
        return {"ok": True, "folder_path": folder_path, "cover_path": cover_path}


@mcp.tool()
def clear_folder_cover(folder_path: str) -> dict:
    """Clear a folder's cover override (revert to the auto cover)."""
    with _lib() as lib:
        lib.clear_folder_cover(folder_path)
        return {"ok": True, "folder_path": folder_path}


if __name__ == "__main__":
    mcp.run()
