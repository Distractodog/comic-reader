# Phase 5 Implementation Plan — Comic Reader ("Beyond Cover")

**Audience:** the AI agent (Sonnet) that will implement these features.
**Author:** prior planning session, 2026-06-01.
**Status of the codebase when this was written:** Phases 1–4 complete; git `main` clean at commit `1ec9fd4`; DB schema at **version 7**; 14 source files in `src/`, ~5,700 lines.

Read this whole document once before touching code. Sections 0–2 are the rules of the road; Section 3 is the per-feature work. **Do exactly one feature per commit.** Do not batch features together — the user works "one item at a time" and reviews each push.

---

## 0. Orientation — what this app is and how it's built

A standalone, offline Windows comic-book reader (also runs on Mac/Linux). PyQt6 UI, local SQLite library, archive readers for CBZ/CBR/CB7/CBT/PDF/EPUB/image-folders. The user **does not write code** — explain in plain language, never ask them to edit code. They build on a Mac; the Windows `.exe` is produced in the cloud via GitHub Actions on every push to `main`.

Repo: `github.com/Distractodog/comic-reader` (public). GitHub auth is already set up via `gh`.

### The non-negotiable product principles (these gate every design choice)
1. **Offline-first.** The app must fully function with no network. Anything networked (Items 38, 42, AI Organization) is **opt-in**, off by default, and the app must behave identically when it's never enabled.
2. **No telemetry, no required cloud, no account.**
3. **No comic limits, free forever, open source.**
4. **Local-first data ownership.** The library is a file the user owns.

Any feature that touches the network or an external API: it must be behind an explicit user action and a stored, user-supplied key/URL. Never hardcode a key. Never call out on startup.

---

## 1. The reusable patterns you will use constantly

These already exist in the code. Match them exactly — do not invent new conventions.

### 1.1 Schema migration recipe (you will do this for almost every item)

All schema lives in `src/library.py`. The version is tracked with `PRAGMA user_version` and a module constant `_CURRENT_VERSION` (currently `7`, [src/library.py:215](src/library.py)). Migrations run sequentially in `Library._migrate()` ([src/library.py:232](src/library.py)).

**To add a migration:**

1. `grep -n "_CURRENT_VERSION" src/library.py` to find the current number `N`. Your new version is `N+1`. (Do this *at implementation time* — if you implement items out of the order below, the numbers shift. Always claim the next free integer.)
2. Define the SQL as a module constant near the other `_SCHEMA_V*` constants (around [src/library.py:134–213](src/library.py)). Use `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN`.
3. Add a new block at the end of `_migrate()`:
   ```python
   if version < N+1:
       self._conn.executescript(_SCHEMA_VNplus1_MIGRATION)  # or .execute(...) for a single ALTER
       self._conn.execute("PRAGMA user_version = N+1")
       self._conn.commit()
       version = N + 1
   ```
4. Bump `_CURRENT_VERSION = N+1`.
5. If you added a **column** to `comics`, also: add the field to the `Comic` dataclass ([src/library.py:48–68](src/library.py)) **with a default**, and add it to `_row_to_comic()` ([src/library.py:87](src/library.py)). If you forget `_row_to_comic`, every read breaks.
6. If the new data should survive export/import, extend `export_to_json()` ([src/library.py:905](src/library.py)) and `import_from_json()` ([src/library.py:986](src/library.py)) and bump the payload `"version"` there (that's a *separate* version number from the schema — it's the export-format version, currently `1`).

Migrations are forward-only and idempotent via `IF NOT EXISTS`. Never edit a past migration block; only append.

### 1.2 The `Library` CRUD style

`Library` ([src/library.py:220](src/library.py)) opens one shared `sqlite3` connection (`check_same_thread=False`, WAL, `row_factory=Row`, `foreign_keys=ON`). Writes go through `with self.transaction() as cur:` ([src/library.py:345](src/library.py)). Reads use `self._conn.execute(...).fetchall()`. Always filter user-facing comic queries with `WHERE hidden = 0` (see existing `get_all_comics`). Add new public methods alongside the related existing ones; keep them small and typed.

### 1.3 The smoke test (your unit-test surface for `library.py`)

`src/library.py` has a `if __name__ == "__main__":` smoke test ([src/library.py near end](src/library.py)) full of `assert`s against an in-memory DB. It needs **no Qt and no DYLD** (the only PyQt6 import in the file is lazy, inside `_default_db_path`). Run it with:
```
venv/bin/python src/library.py
```
Success prints `library.py smoke test: OK`. **Every time you add or change a `Library` method, add assertions here** in the matching style and confirm it still prints OK. This is the cheapest correctness signal in the repo — use it heavily for all the DB-heavy items.

### 1.4 Config/settings persistence — two existing mechanisms

- **`QSettings("ComicReader", "ComicReader")`** ([src/main_window.py:362](src/main_window.py)) — used for window geometry, `last_dir`, etc. Good for simple app-wide prefs and **secrets** are *not* ideal here but acceptable for v1 (note: QSettings is plaintext on disk; for API keys, see Item 42/AI notes).
- **JSON config file** — `KeybindingManager` ([src/keybindings.py:42](src/keybindings.py), `_config_path()`) reads/writes a `keybindings.json` under the app config dir. Mirror this pattern for any structured opt-in feature config (e.g. server connections, AI settings).

Reuse one of these; do not add a new settings framework.

### 1.5 UI wiring map (where things live)

- **`src/main_window.py`** (`MainWindow`, 1340 lines) — the shell. Owns a `QStackedWidget` (0 = bookshelf, 1 = reader, 2 = webtoon), the menu bar (`_build_menus`, [src/main_window.py:479](src/main_window.py)), the reader top-bar menu (`_show_reader_menu`, [src/main_window.py:654](src/main_window.py)), library actions (`add_folder_to_library`, `export_library`, `import_library`, `rescan_all_folders`), and the per-comic apply on open (`load_file`, [src/main_window.py:870](src/main_window.py); per-comic settings applied around [src/main_window.py:899](src/main_window.py)).
- **`src/bookshelf.py`** (`BookshelfView`, `FolderTile`, `ComicTile`, `SeriesTile`, 1115 lines) — the grid. Context menus are built centrally in `BookshelfView._on_comic_context_menu` ([src/bookshelf.py:870](src/bookshelf.py)) and `_on_folder_context_menu` ([src/bookshelf.py:994](src/bookshelf.py)); tiles only **emit** `menu_requested`/`shelf_action_requested` signals and the view builds the `QMenu`. Add new per-comic / per-folder actions there.
- **`src/main_window.py` `_Sidebar`** ([src/main_window.py:105](src/main_window.py)) — left nav. Sentinels: Folders `-1`, Hidden `-2`. Shelves are listed by id. Add new top-level views (e.g. Reading Queue, Stats) here as new sentinels (use `-3`, `-4`, … — pick the next free negative int; `grep` for `-2` and `setActive`).
- **`src/viewer.py`** — `ComicViewer` (`QGraphicsView`) with `FitMode` enum (`ACTUAL_SIZE`/`FIT_WIDTH`/`FIT_PAGE`, [src/viewer.py:14](src/viewer.py)), `SeekBar`, `ThumbnailStrip`. Per-comic fit/zoom (Item 40) lives here + `load_file`.
- **`src/themes.py`** + every widget's `apply_theme(c: dict)` — if you add a widget/dialog, give it an `apply_theme` and call it where siblings are themed, or it will look wrong in dark mode (dark mode is locked on). Search for `apply_theme` callers.
- **`src/archive_handler.py`** — `open_comic(path)` factory ([src/archive_handler.py:286](src/archive_handler.py)) → `ComicReader` ABC with `page_count()` and `get_page_bytes(index)`. Subclass per format. Needed for Item 39 (batch convert).
- **`src/comicinfo.py`** — `parse_comicinfo(file_path)` ([src/comicinfo.py:43](src/comicinfo.py)) returns a metadata dict from `ComicInfo.xml`. Relevant to Items 39/42.
- **`src/thumbnails.py`** — `generate_thumbnail` + `generate_thumbnail_from_image` (custom cover override path already used for folder covers). Reuse for Item 37.
- **`src/library_scanner.py`** — background `QThread` folder scanner emitting `progress`/`finished`. The model for any long background job (hashing, batch convert, server sync).

### 1.6 Run / build / commit recipe

```
# Run the app locally (Mac) — note the DYLD workaround is REQUIRED on this Mac
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/python src/main.py

# Compile-check every file you touched
venv/bin/python -m py_compile src/<file>.py

# DB smoke test (no DYLD needed)
venv/bin/python src/library.py
```

Before committing: `git ls-files | grep -c pyc` must be `0` (a stray `.pyc` got committed once — `.gitignore` won't fix an already-tracked file; use `git rm --cached`). Then commit with a clear message and push to `main`; the Windows/Mac/Linux Actions build runs automatically. End commit messages with the `Co-Authored-By` trailer the repo uses.

**After each push, check the Actions run is green** (`gh run list -R Distractodog/comic-reader -L 1`, then `gh run view`). CI-only packaging failures (PyInstaller missing a hidden import, a new dependency not bundled) are the most likely breakage — Item 0 covers verifying this baseline first.

### 1.7 Verification protocol with the user

The user verifies **visually** — do **not** screenshot their screen. After implementing an item: launch the app with the run command, tell them exactly what to click to see the new feature, and let them confirm. Then compile-check, smoke-test, commit, push, confirm CI green.

---

## 2. Implementation order & schema-version map

Do them in this order — earlier items are smaller/foundational and lower-risk; networked/AI items come last so the offline core is rock-solid first. **The schema version numbers below assume this exact order. If you reorder, recompute with `grep _CURRENT_VERSION`.**

| Step | Item | Title | New schema? | Network/AI? | New dep? |
|------|------|-------|-------------|-------------|----------|
| 0 | — | **Verify CI build baseline** | no | no | no |
| 1 | 40 | Per-comic reading settings | v8 (cols) | no | no |
| 2 | 37 | Cover art override (per comic) | v9 (col) | no | no |
| 3 | 36 | Duplicate detection | v10 (index) | no | no |
| 4 | 34 | Reading statistics | v11 (table) | no | no |
| 5 | 35 | Reading queue / read-next | v12 (col or table) | no | no |
| 6 | 41 | Annotations / notes per page | v13 (table) | no | no |
| 7 | 39 | Batch tools (convert/rename/repack) | maybe none | no | no |
| 8 | 43 | Public shelf sharing | none (export fmt) | no | no |
| 9 | 33 | Optional folder-based sync (docs/stance) | no | no | no |
| 10 | 38 | OPDS / Komga / Kavita client | v14 (tables) | **yes** | `requests` |
| 11 | 42 | AI metadata lookup (Comic Vine) | none | **yes** | `requests` |
| 12 | — | **AI Organization** (the end-goal) | maybe table | **yes** | `anthropic` |

Items 0–9 are pure offline work and should land first as a block. 10–12 are the networked/AI tier and each needs an explicit opt-in design review with the user before building (they cost money / need keys).

---

## 3. Per-feature specifications

Each section: **Goal → Schema → `library.py` API → UI → Testing → Commit → Risks.** Method signatures are recommendations; match surrounding style.

---

### Item 0 — Verify the CI build baseline (do this first, no code)

**Goal:** Confirm Phases 1–4 actually produce a working Windows `.exe` before piling on Phase 5, so any later breakage is clearly attributable.

**Steps:**
1. `gh run list -R Distractodog/comic-reader -L 5` — confirm the latest `main` build is green for **all three** OS matrix legs (Windows/macOS/Linux).
2. `gh run download` the Windows artifact (or check the artifact list). The user can run the `.exe` on a Windows machine if available; if not, at minimum confirm the artifact was produced and PyInstaller didn't error.
3. Open `.github/workflows/build.yml` and `ComicReader.spec`. Confirm: fonts are bundled, `unrar.exe` bundling step for Windows CBR support is present and the Chocolatey install step still resolves (known-fragile per CLAUDE.md "Known issues"). Note any `hiddenimports` that future deps will need to be added to.
4. Report status to the user. If red, fix CI **before** any Phase 5 work.

**No commit unless a fix is needed.** This item is a gate.

---

### Item 40 — Per-comic reading settings

**Goal:** Remember each comic's preferred **fit mode** and **zoom** (and reuse the existing per-comic `reading_mode`/`is_manga`), so reopening a comic restores how the user last read it.

**Context:** `reading_mode` and `is_manga` are *already* per-comic and applied in `load_file` ([src/main_window.py:899](src/main_window.py)). This item adds fit mode + zoom to that same mechanism. `FitMode` is an enum in [src/viewer.py:14](src/viewer.py); `ComicViewer.set_fit_mode` / zoom methods already exist.

**Schema (v8):** Add two columns to `comics`:
```sql
ALTER TABLE comics ADD COLUMN fit_mode TEXT NOT NULL DEFAULT 'page';   -- 'actual'|'width'|'page'
ALTER TABLE comics ADD COLUMN zoom REAL NOT NULL DEFAULT 1.0;
```
Update `Comic` dataclass (`fit_mode: str = "page"`, `zoom: float = 1.0`), `_row_to_comic`, and export/import.

**`library.py` API:**
- `set_fit_mode(self, comic_id: int, fit_mode: str) -> None`
- `set_zoom(self, comic_id: int, zoom: float) -> None`
(Mirror `set_reading_mode` at [src/library.py:864](src/library.py).)

**UI:** In `load_file` where `reading_mode`/`is_manga` are read back, also read `fit_mode`/`zoom` and apply via the viewer. When the user changes fit mode or zooms in the reader, persist it for `self._current_comic_id` (debounce zoom writes — only persist on a settle, e.g. via the existing save-progress path `_save_progress` [src/main_window.py:1044](src/main_window.py), to avoid a DB write per wheel tick).

**Testing:** smoke-test asserts for the two setters; manually open a comic, set Fit Width + zoom, close, reopen → state restored.

**Commit:** `Item 40 — remember fit mode & zoom per comic`

**Risks:** zoom-write spam (debounce); make sure the default `'page'` matches the viewer's current default (`FitMode.FIT_PAGE`, [src/viewer.py:83](src/viewer.py)) so existing comics behave unchanged.

---

### Item 37 — Cover art override (per comic)

**Goal:** Let the user set a comic's cover to **any page of that comic** or **any image file on disk**. (Folder-cover override already exists — this is the per-comic analogue.)

**Context:** Folder covers use a `folder_covers` table + `generate_thumbnail_from_image` ([src/thumbnails.py](src/thumbnails.py)); `set_cover_path` ([src/library.py:531](src/library.py)) already updates a comic's `cover_path`. The cover override just needs a UI to pick the source and a regenerate step.

**Schema (v9):** Optional — to remember that a cover is a *manual override* (so a rescan/refresh doesn't clobber it), add:
```sql
ALTER TABLE comics ADD COLUMN cover_override INTEGER NOT NULL DEFAULT 0;
```
Guard any automatic cover regeneration with `WHERE cover_override = 0`. Update dataclass/`_row_to_comic`/export-import.

**`library.py` API:** reuse `set_cover_path`; add a tiny `set_cover_override(comic_id, bool)` or fold the flag into `set_cover_path`.

**UI:** In `BookshelfView._on_comic_context_menu` ([src/bookshelf.py:870](src/bookshelf.py)) add:
- **"Set cover from page…"** → small dialog (reuse `ThumbnailStrip` from [src/viewer.py:483](src/viewer.py), or a simple page-number spinbox) → render that page bytes via `open_comic(path).get_page_bytes(i)` → `generate_thumbnail_from_image` → `set_cover_path` + flag → refresh grid.
- **"Choose cover image…"** → file dialog → `generate_thumbnail_from_image` → same.
- **"Reset cover to default"** (only when `cover_override`) → regenerate from page 0, clear flag.

Mirror the folder-cover menu wiring at [src/bookshelf.py:1002–1013](src/bookshelf.py).

**Testing:** smoke test for the flag; manual: right-click a comic → set cover from page 5 → tile updates; rescan → cover persists.

**Commit:** `Item 37 — per-comic cover override (from page or image file)`

**Risks:** thumbnail cache key collisions — folder covers key by hash of folder path ([src/thumbnails.py:34](src/thumbnails.py)); per-comic should key by comic id or file-path hash so it doesn't collide.

---

### Item 36 — Duplicate detection

**Goal:** Find comics that are the same file content stored in multiple places (re-downloads, copies). Surface them so the user can review/hide.

**Context:** The `content_hash` column **exists but is never populated** (`set_content_hash` exists at [src/library.py:538](src/library.py); no caller computes a hash). This item: compute hashes in the background, then group by hash.

**Schema (v10):** No new column. Add an index for grouping:
```sql
CREATE INDEX IF NOT EXISTS idx_comics_content_hash ON comics(content_hash);
```

**Hashing strategy (important — keep it fast & offline):** Hashing every byte of ~2000 comics (many hundreds of MB each for PDFs) is too slow. Use a **cheap content signature**: hash of `(file_size, first 64KB, last 64KB)` with `hashlib.sha256`, or hash the bytes of page 0 + page count. Document the choice in a comment. This catches true duplicates (same file copied) without reading whole archives. Put the helper in `library.py` or a small `dedupe.py`.

**`library.py` API:**
- `find_duplicate_groups(self) -> list[list[Comic]]` — `SELECT ... WHERE content_hash IS NOT NULL GROUP BY content_hash HAVING COUNT(*) > 1`, return groups.
- reuse `set_content_hash`.

**Background job:** Add a `QThread` (model: `library_scanner.py`) that walks comics with `content_hash IS NULL`, computes the signature, calls `set_content_hash`, emits progress. Trigger it from a menu action ("Scan for duplicates"), not on startup.

**UI:** A "Duplicates" entry — either a sidebar sentinel (`-3`) showing duplicate groups as a grid, or a modal dialog listing groups with a "Hide this copy" action (reuse `set_hidden`, [src/library.py:434](src/library.py)). Dialog is simpler; prefer it for v1.

**Testing:** smoke test: insert two comics, set the same `content_hash`, assert `find_duplicate_groups` returns one group of two.

**Commit:** `Item 36 — duplicate detection via content signature`

**Risks:** never auto-delete or auto-hide — only surface and let the user act. Hashing must be cancellable and not block the UI.

---

### Item 34 — Reading statistics

**Goal:** Show reading stats: pages read per day, total hours, streaks, completion rate.

**Context:** Today only `last_read` + `current_page` are tracked (`update_progress`, [src/library.py:489](src/library.py)). Real stats need an **event log** of reading sessions.

**Schema (v11):** New table:
```sql
CREATE TABLE IF NOT EXISTS reading_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comic_id   INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    event_date TEXT    NOT NULL,            -- ISO date (UTC) of the reading
    pages_read INTEGER NOT NULL DEFAULT 0,
    seconds    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_reading_events_date ON reading_events(event_date);
```

**Recording:** In `update_progress` (or the reader's page-turn path), append/accumulate a `reading_events` row for "today" (UTC date). Track session time with a simple timer in `MainWindow`: start on `load_file`, flush elapsed seconds on page change / close / back-to-library (`_back_to_library`, [src/main_window.py:638](src/main_window.py); `_save_progress`, [src/main_window.py:1044](src/main_window.py)). Accumulate into the current day's row (`INSERT ... ON CONFLICT` on a `(comic_id, event_date)` unique key, or upsert).

**`library.py` API:**
- `record_reading(self, comic_id, pages_read, seconds)`
- `get_stats(self) -> dict` returning: total pages, total hours, comics completed, completion rate (`read` / total), pages-per-day series (last 30 days), current streak (consecutive days with ≥1 event).
- Implement streak in SQL or Python over distinct `event_date`s.

**UI:** A "Statistics" dialog (menu: Library → Statistics, or a sidebar sentinel). Show headline numbers + a simple bar list of pages/day (no charting library needed — render bars with `paintEvent` like `SeekBar`/tiles already do, or simple labeled rows). Keep it dependency-free.

**Testing:** smoke test: record events across two dates, assert totals/streak/completion.

**Commit:** `Item 34 — reading statistics (events log + stats view)`

**Risks:** don't double-count pages on back-and-forth navigation — count net forward progress or just count page-turn events; document the rule. Timezone: store UTC date consistently (the rest of the code uses `datetime.now(timezone.utc).isoformat()`).

---

### Item 35 — Reading queue / "read next"

**Goal:** A user-curated ordered list of comics to read next.

**Context:** Smart shelves already exist (`shelves`/`comic_shelves` with `position`, [src/library.py:174](src/library.py)). A queue is essentially an **ordered manual shelf** with special UI placement, OR a dedicated table. A dedicated table is cleaner because order + "remove on finish" semantics differ from shelves.

**Schema (v12):** New table:
```sql
CREATE TABLE IF NOT EXISTS reading_queue (
    comic_id INTEGER PRIMARY KEY REFERENCES comics(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    added_at TEXT    NOT NULL
);
```

**`library.py` API:**
- `add_to_queue(comic_id)` / `remove_from_queue(comic_id)` / `is_in_queue(comic_id)`
- `get_queue(self) -> list[Comic]` ordered by `position` (filter `hidden = 0`)
- `move_in_queue(comic_id, new_position)` (renumber)

**UI:**
- Context-menu action in `_on_comic_context_menu`: "Add to reading queue" / "Remove from queue".
- Sidebar sentinel (`-4`) "Reading Queue" showing the queue as an ordered grid. Reorder via up/down menu actions (drag-reorder is optional polish — `QGridLayout` reorder is fiddly, skip for v1).
- Optional: when a comic becomes `read` (completion), offer to auto-remove from queue (don't do it silently).

**Testing:** smoke test for add/remove/order/get_queue.

**Commit:** `Item 35 — reading queue (read-next list)`

**Risks:** keep `position` contiguous on removal, or sort defensively. Don't conflate with shelves in the UI.

---

### Item 41 — Annotations / notes per page

**Goal:** Let the user attach a text note to a specific page of a comic; show an indicator and let them view/edit notes.

**Context:** Bookmarks already do "marker on a page" (`bookmarks` table, `toggle_bookmark`, [src/library.py:820](src/library.py); `SeekBar.set_bookmarks`, [src/viewer.py:348](src/viewer.py)). Annotations are the same shape + a body.

**Schema (v13):** New table:
```sql
CREATE TABLE IF NOT EXISTS annotations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    comic_id   INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
    page_index INTEGER NOT NULL,
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_comic ON annotations(comic_id);
```

**`library.py` API:**
- `add_annotation(comic_id, page_index, body) -> int`
- `update_annotation(annotation_id, body)`
- `delete_annotation(annotation_id)`
- `get_annotations(comic_id) -> list[...]`
- `get_annotation_for_page(comic_id, page_index)` (or list per page)

Add annotations to export/import (extend the per-comic payload alongside `bookmarks`, [src/library.py:942](src/library.py)).

**UI:** Reader toolbar/menu action "Add/edit note for this page" → a small `QDialog` with a `QPlainTextEdit`. Show a subtle indicator on annotated pages (extend the `SeekBar` marker rendering, or a corner badge in the viewer). A "Notes" panel listing all notes in the current comic with jump-to-page is a nice-to-have.

**Testing:** smoke test full CRUD + export/import round-trip (mirror the bookmark assertions in the smoke test).

**Commit:** `Item 41 — per-page annotations/notes`

**Risks:** make sure deleting a comic cascades (FK `ON DELETE CASCADE` handles it). Include annotations in export so notes are portable.

---

### Item 39 — Batch tools (rename from metadata, CBR→CBZ convert, repack)

**Goal:** Multi-select comics and run batch operations: (a) rename file from metadata, (b) convert CBR/CB7/CBT → CBZ, (c) repack/optimize.

**Context:** Multi-select already exists in the bookshelf (multi-select metadata editing — `MetadataDialog`, [src/metadata_editor.py](src/metadata_editor.py)). Archive reading is via `open_comic` ([src/archive_handler.py:286](src/archive_handler.py)). Conversion = read all pages from the source reader, write a new `.cbz` (a zip of the page images in order).

**Schema:** None (file operations + path updates). After converting/renaming, you **must** update the comic's `file_path` (and `parent_dir`) in the DB — there's no `update_file_path` method yet, so add one:
- `update_file_path(self, comic_id, new_path)` updating `file_path` + `parent_dir` (mirror `_parent_dir` usage in `add_comic`).

**Batch module:** New `src/batch_tools.py` (or extend `library_scanner.py` pattern) running on a `QThread` with progress, since converting many archives is slow.
- **CBR→CBZ:** `reader = open_comic(src)`; create a new zip via stdlib `zipfile`; for `i in range(reader.page_count())` write `get_page_bytes(i)` as `page_{i:04d}.<ext>` (detect ext from bytes magic, default `.jpg`); close; write next to original or to a chosen output dir; update DB `file_path`. **Do not delete the original by default** — offer it as a separate confirmed step (data-safety principle).
- **Rename from metadata:** build a filename from a template (e.g. `{series} #{series_number} - {title}`), sanitize for the filesystem, `os.rename`, update DB.
- **Repack:** re-zip a CBZ with consistent naming/order; optional, lowest priority.

**UI:** Multi-select → context menu (or a "Batch" submenu) → operation → confirmation dialog showing what will happen (counts, target naming) → progress dialog. Convert/rename touch the user's real files — **always confirm, never auto-delete, show a dry-run summary first.**

**Testing:** Use `scripts/make_test_comic.py` to generate fixtures; convert one and assert the output `.cbz` opens via `open_comic` with the same page count. Smoke-test `update_file_path`.

**Commit:** `Item 39 — batch tools (convert to CBZ, rename from metadata)`

**Risks:** **Highest data-safety risk item.** Operate on copies, confirm everything, never destroy originals without explicit per-operation consent. Watch out for filename collisions and read-only files. PDF→CBZ is lossy/huge — either skip PDFs or warn loudly.

---

### Item 43 — Public shelf sharing

**Goal:** Export a single shelf as a portable, shareable file; import it elsewhere.

**Context:** `export_to_json`/`import_from_json` ([src/library.py:905](src/library.py)) already export *all* manual shelves with their member `file_path`s. This item is a **scoped, single-shelf** export + an import that recreates the shelf and matches members by content where possible.

**Schema:** None. New export format variant.

**`library.py` API:**
- `export_shelf(self, shelf_id, output_path) -> dict` — write `{"format": "comic-reader-shelf", "version": 1, "shelf_name": ..., "comics": [ {title, series, series_number, author, content_hash, file_path}... ]}`. Include `content_hash` so a recipient can match by content (Item 36 populates it), falling back to `(series, series_number, title)`.
- `import_shelf(self, input_path) -> dict` — create the shelf; for each entry, try to match an existing comic by `content_hash`, then by metadata; add matches to the new shelf; report matched/unmatched counts. **A shared shelf is a list of *which* comics, not the comic files themselves** — be explicit to the user that it links to comics they already have.

**UI:** Shelf right-click in the sidebar (`_on_shelf_right_click`, [src/main_window.py:305](src/main_window.py)) → "Export shelf…" → file dialog. Library menu → "Import shelf…". Show the matched/unmatched summary after import.

**Testing:** smoke test: create shelf, add comics, export to temp file, import into a fresh `:memory:` library that has the same comics by `content_hash`, assert shelf recreated with members.

**Commit:** `Item 43 — share/import a single shelf`

**Risks:** set user expectations — sharing a shelf shares the *list*, not the comics. Match logic must be conservative (don't wrongly attach unrelated comics).

---

### Item 33 — Optional folder-based sync (stance + docs, minimal code)

**Goal:** Support (but never require) syncing the library via the user's own folder-sync tool (Syncthing/Dropbox/iCloud) at the *folder* level. This is mostly a **documentation + robustness** item, not a feature build.

**Work:**
- **README:** document the recommended setup — keep comics in a synced folder; the library DB itself is per-machine (don't sync the live `.db`; sync the *export JSON* instead, or accept that progress is per-device). Explain the trade-offs plainly.
- **Robustness:** ensure "Rescan All Library Folders" (`rescan_all_folders`, [src/main_window.py:1182](src/main_window.py)) cleanly handles files that appeared/moved via sync, and that missing files (synced away) don't crash the reader — they should show as unavailable, not throw.
- Optionally add a small "Sync help" entry in the menu linking to the README section.

**Schema:** None.

**Commit:** `Item 33 — document folder-based sync & harden rescan for synced folders`

**Risks:** Do **not** build a cloud sync service — that violates the local-first principle. This is guidance + resilience only.

---

### Item 38 — OPDS / Komga / Kavita client (NETWORK — opt-in)

**Goal:** Browse and read comics from a user's self-hosted server (Komga/Kavita speak OPDS + their own REST APIs). Opt-in, off by default.

**⚠ Before building:** confirm scope with the user (read-only browse+stream is the right v1; downloading vs streaming; which servers). This is the largest item.

**New dependency:** `requests` (add to `requirements.txt` **and** to `ComicReader.spec` `hiddenimports` / ensure PyInstaller bundles it — re-verify CI after). Keep it to one well-known dep.

**Schema (v14):** New tables for saved connections (no secrets in plaintext if avoidable — but acceptable v1 in the config JSON, document it):
```sql
CREATE TABLE IF NOT EXISTS servers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL,        -- 'opds'|'komga'|'kavita'
    base_url  TEXT NOT NULL,
    username  TEXT,
    created_at TEXT NOT NULL
);
```
Store passwords/tokens in `QSettings` or the JSON config keyed by server id, **not** in the DB export (never export credentials).

**New module:** `src/server_client.py` — OPDS feed parsing (it's Atom XML — reuse the stdlib XML approach from `comicinfo.py`) and per-server REST for covers/page streaming. All requests on a `QThread`; show loading state; handle timeouts/offline gracefully (the app must not hang).

**Reading server comics:** Implement a `ComicReader` subclass or an adapter that fetches page bytes over HTTP on demand (with the existing page cache/preloader). The cleanest path: a `RemoteReader(ComicReader)` whose `get_page_bytes(i)` does an authenticated GET.

**UI:** Sidebar "Servers" section; an "Add server…" dialog (name/kind/url/credentials, with a "Test connection" button); browse remote library in the existing grid; open → stream.

**Testing:** Hard to unit-test without a server. Add a thin parser test for a sample OPDS XML fixture. Manual test against a real Komga/Kavita instance the user provides.

**Commit:** `Item 38 — OPDS/Komga/Kavita client (opt-in remote libraries)`

**Risks:** offline-first principle — the app must be fully usable with zero servers configured and must never block on network at startup. Credential storage is plaintext in v1; document it and consider OS keyring later. Re-verify the CI build bundles `requests`.

---

### Item 42 — AI metadata lookup (Comic Vine — NETWORK, opt-in)

**Goal:** Fill missing `ComicInfo` metadata (series, issue, year, publisher, summary) from Comic Vine, opt-in, with a user-supplied API key.

**⚠ Before building:** confirm with the user — Comic Vine needs a free API key the *user* registers; this is opt-in and per-comic or batch.

**New dependency:** `requests` (shared with Item 38; if 38 is done first, no new dep).

**Schema:** None required (writes into existing `comics` metadata columns via `update_metadata`, [src/library.py:515](src/library.py)). *Optionally* add a `summary` column (the long-deferred decision noted in CLAUDE.md "Deferred decisions") if you want to store/display summaries — that's its own small schema bump and a detail-panel UI; treat as optional sub-scope.

**Config:** Store the Comic Vine API key in `QSettings`/JSON config (document plaintext caveat). Never hardcode.

**New module:** `src/metadata_lookup.py` — query Comic Vine search API by filename/series guess, present candidate matches to the user (never auto-overwrite — show a match dialog), on confirm call `update_metadata`. Respect Comic Vine rate limits; all on a `QThread`.

**UI:** Comic context menu → "Look up metadata…" → results dialog (poster + fields) → "Apply". Batch variant over multi-select with per-comic confirmation or a review list.

**Testing:** parser/mapping unit test against a saved sample API JSON response; manual test with a real key.

**Commit:** `Item 42 — opt-in AI/Comic Vine metadata lookup`

**Risks:** opt-in + key required + never auto-overwrite user data without confirmation. Offline app must be unaffected when unused.

---

### AI Organization — the end-goal (NETWORK/AI, opt-in)

**Goal (the user's stated north star):** Integrated AI that looks at the library's metadata and **suggests or auto-creates shelves** ("group these as a series run", "shelf: 80s X-Men", "these are all unread one-shots") — built on the shelves foundation that already exists (`shelves`/`comic_shelves`).

**⚠ Before building:** This is the flagship feature — do a dedicated design pass with the user. It uses the **Anthropic Claude API** (the user already works in this ecosystem). Decide: opt-in key, which model (default to the latest Claude — e.g. a current Sonnet for cost/throughput on bulk metadata), suggest-vs-auto-apply (default **suggest, user approves** — never silently restructure their library).

**New dependency:** `anthropic` (Python SDK). Add to `requirements.txt` + `ComicReader.spec` and re-verify CI bundling. **Use prompt caching** for the (large, stable) library-metadata context across requests.

**Config:** Anthropic API key in config (plaintext caveat documented; consider OS keyring as a follow-up). Off by default. The app is 100% functional without it.

**Design:**
1. Gather a compact metadata table of the library (title/series/number/author/year/tags/read-status) — **send metadata only, never page images or file contents** (privacy + cost).
2. Ask the model to propose shelves: each proposal = `{name, rationale, comic_ids[]}`. Constrain output to strict JSON (tool use / JSON mode).
3. Present proposals in a review UI; user accepts/edits/rejects each; on accept, create the shelf (`create_shelf`, `add_comic_to_shelf`) — reusing existing APIs, **no new schema needed** unless you want to tag a shelf as "AI-suggested" (optional `source` column on `shelves`).
4. Make it incremental and cheap: cache the metadata context (prompt caching), only re-run on demand.

**New module:** `src/ai_organize.py` — builds the metadata payload, calls the Anthropic API on a `QThread`, parses proposals. Keep all networking off the UI thread; handle errors/no-key/offline gracefully.

**UI:** A "Organize with AI" action (Library menu) → progress → proposals review dialog → apply selected.

**Testing:** unit-test the proposal-JSON → shelf-creation mapping with a canned response (no live API in tests). Manual test with a real key on the user's ~2000-comic library; verify cost is reasonable (metadata-only + caching).

**Commit:** `AI organization — opt-in Claude-powered shelf suggestions`

**Risks:** **Never auto-restructure** the library without explicit approval. Send metadata only. Costs money — make every call user-initiated. Must degrade to a no-op when no key/offline. This is the highest-value, highest-care feature — get the user's sign-off on the interaction model before coding.

---

## 4. Final checklist for each item (paste into your working notes)

- [ ] Implemented matching existing patterns (Section 1).
- [ ] If schema changed: bumped `_CURRENT_VERSION`, appended migration block, updated `Comic` dataclass + `_row_to_comic`, updated export/import if the data is portable.
- [ ] Added/updated assertions in the `library.py` smoke test; `venv/bin/python src/library.py` prints OK.
- [ ] `py_compile` clean on every touched file.
- [ ] New widgets/dialogs have `apply_theme` and look right in dark mode.
- [ ] Networked/AI features are opt-in, off by default, and the app is fully functional without them.
- [ ] File-destroying operations (Item 39) confirm first and never delete originals silently.
- [ ] App launches with the DYLD run command; told the user exactly what to click; they confirmed visually (no screenshots of their screen).
- [ ] `git ls-files | grep -c pyc` is `0`.
- [ ] One feature = one commit, clear message + `Co-Authored-By` trailer, pushed to `main`.
- [ ] Actions run is green for all three OS legs (`gh run list -L 1`); fixed any CI-only packaging failure (esp. after adding `requests`/`anthropic`).
- [ ] Updated `## Current status` in `CLAUDE.md` to record the item as done.

---

## 5. Things deliberately left as judgment calls for the implementer

- Exact dialog layouts and copy — match the app's existing minimal style.
- Whether queue/duplicates/stats/annotations get a sidebar sentinel vs. a dialog — dialogs are faster to ship; sentinels are more discoverable. Default to whichever matches nearby features.
- Drag-to-reorder (queue) and full charting (stats) are polish — ship the functional version first.
- For networked items, credential storage hardening (OS keyring) is a fine **follow-up** after the v1 plaintext-with-disclosure version.

When a real fork appears (cost, destructive behavior, which servers/models), **ask the user** — they own those calls. Everything else: follow the patterns above and proceed.
