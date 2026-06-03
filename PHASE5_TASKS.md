# Phase 5 — Granular Task Breakdown (companion to `PHASE5_PLAN.md`)

**What this file is:** the step-by-step checklist for the **remaining** Phase 5 items. `PHASE5_PLAN.md` is the *strategy* (why, design, risks, the reusable patterns in its Section 1). **This file is the *execution order* — atomic, numbered, tick-as-you-go tasks.** Keep both open: when a step says "follow the migration recipe," that recipe lives in `PHASE5_PLAN.md` §1.1.

**Audience:** the implementing agent (Sonnet). The user does **not** write code — explain in plain language, never ask them to edit code.

**Golden rules (do not violate):**
- **One item = one commit = one push.** Never batch items. The user reviews each push.
- **Verify every code anchor before editing.** Line numbers below are accurate as of the baseline (commit `66d85b7`, schema **v8**) but *shift as you add code*. Always `grep` the symbol named in brackets `[name]` rather than trusting the raw line number.
- **Claim the next free schema version at implementation time** (`grep -n _CURRENT_VERSION src/library.py`). The versions below assume the exact order in this file; reordering shifts them.
- After each item: run app (DYLD command), tell the user exactly what to click, let them confirm **visually (no screenshots of their screen)**, then `py_compile` → `library.py` smoke test → commit → push → confirm CI green for all 3 OS legs → tick `CLAUDE.md` status.

---

## Baseline (already done — do NOT redo)
- ✅ **Item 0** — CI build baseline verified green (Win/Mac/Linux).
- ✅ **Item 40** — per-comic fit mode + zoom, **schema v8** (`fit_mode`, `zoom` cols). Commit `fc3757b`.

## Remaining order at a glance
| # | Item | Title | Next schema | Tier |
|---|------|-------|-------------|------|
| 1 | 37 | Cover art override (per comic) | **v9** (col) | offline |
| 2 | 36 | Duplicate detection | **v10** (index) | offline |
| 3 | 34 | Reading statistics | **v11** (table) | offline |
| 4 | 35 | Reading queue / read-next | **v12** (table) | offline |
| 5 | 41 | Annotations / notes per page | **v13** (table) | offline |
| 6 | 39 | Batch tools (convert/rename) | none | offline |
| 7 | 43 | Public shelf sharing | none | offline |
| 8 | 33 | Folder-based sync (docs/harden) | none | offline |
| 9 | 38 | OPDS / Komga / Kavita client | **v14** (table) | ⚠ network — design review first |
| 10 | 42 | AI metadata lookup (Comic Vine) | none/opt | ⚠ network — design review first |
| 11 | — | **AI Organization** (Claude) | none/opt | ⚠ network — flagship, design review first |

**Items 1–8 are the pure-offline block. Land all of them before touching 9–11.** Items 9–11 each require an explicit design conversation with the user (they cost money / need API keys) — do not start coding them without sign-off.

## Verified code anchors (baseline `66d85b7`)
`library.py`: `_CURRENT_VERSION` **L222** · `Comic` dataclass **L48** · `_row_to_comic` **L89** · `_migrate` **L239** (last block = v8, **L304–305**) · schema constants **L138–220** · `transaction()` **L359** · `get_all_comics` **L416** · `set_hidden` **L448** · `update_progress` **L503** · `update_metadata` **L529** · `set_cover_path` **L545** · `set_content_hash` **L552** · `create_shelf` **L671** · `add_comic_to_shelf` **L712** · `toggle_bookmark` **L834** · `set_reading_mode` **L878** · `export_to_json` **L933** · `import_from_json` **L1016**
`bookshelf.py`: `_on_comic_context_menu` **L870** · `_on_folder_context_menu` **L994**
`main_window.py`: `_Sidebar` **L105** · `set_active` **L317** (sentinels: Folders `-1`, Hidden `-2`) · `_build_menus` **L479** · `_back_to_library` **L638** · `_show_reader_menu` **L654** · `load_file` **L870** · `_save_progress` **L1053** · `_on_shelf_right_click` **L305** · `rescan_all_folders` **L1207**
`viewer.py`: `FitMode` **L14** (`FIT_PAGE` default) · `restore_view_state` **L216** · `SeekBar` **L337** · `set_bookmarks` **L364** · `ThumbnailStrip` **L499**
Next free **sidebar sentinel**: `-3` (then `-4`, …). Next free **schema version**: `v9`.

---

# ITEM 37 — Cover art override (per comic)  ·  schema v9

**Goal:** right-click a comic → set its cover to *any page of that comic* or *any image file*, and have it survive rescans.

1. [ ] **Schema v9.** Add constant near L220: `_SCHEMA_V9 = "ALTER TABLE comics ADD COLUMN cover_override INTEGER NOT NULL DEFAULT 0;"`. Append a `if version < 9:` block to `_migrate` mirroring the v8 block (execute → `PRAGMA user_version = 9` → `version = 9`). Bump `_CURRENT_VERSION = 9`.
2. [ ] **Dataclass + reader.** Add `cover_override: int = 0` to `Comic` [L48]; add it to `_row_to_comic` [L89]. (Forgetting `_row_to_comic` breaks *every* read.)
3. [ ] **Export/import.** Add `cover_override` to the per-comic payload in `export_to_json` [L933] and read it back in `import_from_json` [L1016].
4. [ ] **`library.py` API.** Add next to `set_cover_path` [L545]:
   - `set_cover_override(self, comic_id: int, override: bool) -> None` — UPDATE the flag via `with self.transaction() as cur:`.
   - (Reuse existing `set_cover_path` to write the new `cover_path`.)
5. [ ] **Protect auto-regeneration.** Find every place a cover is auto-(re)generated (`grep -n "generate_thumbnail\|cover_path" src/library_scanner.py src/library.py`). Guard the auto-refresh with `WHERE cover_override = 0` so a rescan never clobbers a manual cover.
6. [ ] **Thumbnail keying.** `generate_thumbnail_from_image` [`src/thumbnails.py`] — folder covers key by hash of folder path. For per-comic, key by **comic id** (or file-path hash) so it can't collide with a folder cover. Verify the key scheme in `thumbnails.py` before calling.
7. [ ] **UI — context menu.** In `bookshelf.py._on_comic_context_menu` [L870] add three actions:
   - **"Set cover from page…"** → page-number `QInputDialog` (or reuse `ThumbnailStrip` [viewer L499]) → `open_comic(path).get_page_bytes(i)` → `generate_thumbnail_from_image` → `set_cover_path` + `set_cover_override(True)` → refresh grid via the existing `_nav_transition(self._repopulate)` path.
   - **"Choose cover image…"** → `QFileDialog` (images) → `generate_thumbnail_from_image` → same.
   - **"Reset cover to default"** (show only when `cover_override`) → regenerate from page 0 → `set_cover_override(False)`.
   Mirror folder-cover wiring in `_on_folder_context_menu` [L994].
8. [ ] **Smoke test.** In `library.py`'s `__main__`: insert comic → `set_cover_override(id, True)` → assert flag reads back `1` → `set_cover_override(id, False)` → assert `0`. `venv/bin/python src/library.py` prints OK.
9. [ ] **Verify.** `py_compile` touched files → run app → tell user: "right-click any comic → Set cover from page → pick page 5 → tile updates; now Rescan that folder → cover stays."
10. [ ] **Ship.** `git ls-files | grep -c pyc` == 0 → commit `Item 37 — per-comic cover override (from page or image file)` → push → CI green → update `CLAUDE.md` status.

**Risk:** thumbnail cache key collision (step 6). Never auto-clobber a manual cover (step 5).

---

# ITEM 36 — Duplicate detection  ·  schema v10

**Goal:** find comics whose *content* is the same file copied to multiple places; surface groups, let the user hide copies. Never auto-delete.

1. [ ] **Schema v10.** Constant `_SCHEMA_V10 = "CREATE INDEX IF NOT EXISTS idx_comics_content_hash ON comics(content_hash);"`. Append `if version < 10:` block. Bump `_CURRENT_VERSION = 10`. (No new column — `content_hash` already exists but is never populated; `set_content_hash` is at L552.)
2. [ ] **Signature helper (fast & offline).** Hashing whole PDFs is too slow. Add `compute_content_signature(path) -> str` (in `library.py` or a small new `src/dedupe.py`): `hashlib.sha256` over `(file_size, first 64 KB, last 64 KB)`. Document the choice in a comment. This catches true file copies without reading whole archives.
3. [ ] **`library.py` API.**
   - `find_duplicate_groups(self) -> list[list[Comic]]` — `SELECT * FROM comics WHERE content_hash IS NOT NULL AND hidden = 0` then group by `content_hash`, return only groups with `COUNT > 1`, each as a list of `Comic`.
   - reuse `set_content_hash` [L552].
4. [ ] **Background hashing job.** New `QThread` modeled on `library_scanner.py`: select comics `WHERE content_hash IS NULL`, compute signature, `set_content_hash`, emit `progress(done, total)`; cancellable; emit `finished`. Trigger from a menu action **"Scan for duplicates"** (`_build_menus` [L479]) — **never on startup**.
5. [ ] **UI (prefer a dialog for v1).** After the scan finishes, open a modal `QDialog` listing each duplicate group (title + path) with a **"Hide this copy"** button per row → `set_hidden(comic_id, True)` [L448] → refresh. (A sidebar sentinel `-3` grid is the alternative; dialog ships faster.)
6. [ ] **Theme.** Give the dialog `apply_theme(c)` and call it where siblings are themed (dark mode is locked on).
7. [ ] **Smoke test.** Insert two comics, `set_content_hash` both to the same value → assert `find_duplicate_groups()` returns one group of two. Insert a third unique hash → assert it is **not** grouped.
8. [ ] **Verify.** Run app → "Library → Scan for duplicates" → progress → review dialog → Hide a copy → it disappears from the grid.
9. [ ] **Ship.** pyc check → commit `Item 36 — duplicate detection via content signature` → push → CI green → CLAUDE.md.

**Risk:** never auto-hide/delete — surface only. Hashing must be cancellable and off the UI thread.

---

# ITEM 34 — Reading statistics  ·  schema v11

**Goal:** pages/day, total hours, current streak, completion rate — from a real event log.

1. [ ] **Schema v11.** Constant:
   ```sql
   CREATE TABLE IF NOT EXISTS reading_events (
       id         INTEGER PRIMARY KEY AUTOINCREMENT,
       comic_id   INTEGER NOT NULL REFERENCES comics(id) ON DELETE CASCADE,
       event_date TEXT    NOT NULL,
       pages_read INTEGER NOT NULL DEFAULT 0,
       seconds    INTEGER NOT NULL DEFAULT 0,
       UNIQUE(comic_id, event_date)
   );
   CREATE INDEX IF NOT EXISTS idx_reading_events_date ON reading_events(event_date);
   ```
   Append `if version < 11:` block (use `executescript`). Bump `_CURRENT_VERSION = 11`.
2. [ ] **`library.py` API.**
   - `record_reading(self, comic_id, pages_read, seconds) -> None` — **upsert** into today's row: `INSERT ... ON CONFLICT(comic_id, event_date) DO UPDATE SET pages_read = pages_read + excluded.pages_read, seconds = seconds + excluded.seconds`. Use `datetime.now(timezone.utc).date().isoformat()` for `event_date` (match the UTC-ISO convention already in the file).
   - `get_stats(self) -> dict` → `{total_pages, total_hours, comics_completed, completion_rate, pages_per_day: [(date, n)…last 30], current_streak}`. Streak = count of consecutive days (ending today) with ≥1 event — compute over `SELECT DISTINCT event_date` in Python.
3. [ ] **Session timer in `MainWindow`.** Start a timer on `load_file` [L870]; flush elapsed seconds + net forward pages on page change, `_save_progress` [L1053], and `_back_to_library` [L638], calling `record_reading`. **Count net forward progress only** (don't double-count back-and-forth) — document the rule in a comment.
4. [ ] **UI — Statistics dialog.** Menu "Library → Statistics" [L479]. Headline numbers as labels; pages/day as simple bars drawn with `paintEvent` (copy the pattern from `SeekBar` [viewer L337] / tiles) — **no charting dependency.** Add `apply_theme`.
5. [ ] **Smoke test.** `record_reading` across two distinct dates → assert totals, completion rate (read/total), and a 2-day streak when both dates are consecutive.
6. [ ] **Verify.** Read a few pages in a comic, go back, open "Library → Statistics" → numbers populated.
7. [ ] **Ship.** pyc check → commit `Item 34 — reading statistics (events log + stats view)` → push → CI green → CLAUDE.md.

**Risk:** double-counting pages; timezone consistency (store UTC date). Keep the timer cheap.

---

# ITEM 35 — Reading queue / "read next"  ·  schema v12

**Goal:** a user-curated, ordered "read next" list.

1. [ ] **Schema v12.**
   ```sql
   CREATE TABLE IF NOT EXISTS reading_queue (
       comic_id INTEGER PRIMARY KEY REFERENCES comics(id) ON DELETE CASCADE,
       position INTEGER NOT NULL DEFAULT 0,
       added_at TEXT    NOT NULL
   );
   ```
   Append block, bump `_CURRENT_VERSION = 12`.
2. [ ] **`library.py` API.** `add_to_queue(comic_id)` (position = current max+1), `remove_from_queue(comic_id)`, `is_in_queue(comic_id) -> bool`, `get_queue() -> list[Comic]` (JOIN comics, `WHERE hidden = 0`, ORDER BY position), `move_in_queue(comic_id, new_position)` (renumber to stay contiguous).
3. [ ] **UI — context menu.** In `_on_comic_context_menu` [L870]: toggle **"Add to reading queue"** / **"Remove from queue"** based on `is_in_queue`.
4. [ ] **UI — sidebar.** Add sentinel `-3` **"Reading Queue"** in `_Sidebar` [L105] + a branch in `set_active`/the repopulate path [L317] showing `get_queue()` as an ordered grid. Reorder via right-click **"Move up" / "Move down"** → `move_in_queue`. (Drag-reorder is optional polish — skip for v1.)
5. [ ] **Optional:** when a comic hits `read`, *offer* (don't silently do) auto-remove from queue.
6. [ ] **Smoke test.** add 3 → assert order; `move_in_queue` → assert new order; `remove_from_queue` → assert contiguous positions remain.
7. [ ] **Verify.** Right-click comic → Add to reading queue → sidebar "Reading Queue" shows it; Move up/down reorders.
8. [ ] **Ship.** pyc check → commit `Item 35 — reading queue (read-next list)` → push → CI green → CLAUDE.md.

**Risk:** keep `position` contiguous on removal; don't conflate the queue with shelves in the UI.

---

# ITEM 41 — Annotations / notes per page  ·  schema v13

**Goal:** attach a text note to a specific page; show an indicator; view/edit/delete; export with the comic.

1. [ ] **Schema v13.**
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
   Append block, bump `_CURRENT_VERSION = 13`.
2. [ ] **`library.py` API.** `add_annotation(comic_id, page_index, body) -> int`, `update_annotation(id, body)`, `delete_annotation(id)`, `get_annotations(comic_id) -> list`, `get_annotation_for_page(comic_id, page_index)`. Use UTC-ISO timestamps.
3. [ ] **Export/import.** Extend the per-comic payload alongside `bookmarks` in `export_to_json` [L933] / `import_from_json` [L1016] so notes are portable.
4. [ ] **UI — editor.** Reader menu `_show_reader_menu` [L654]: **"Add/edit note for this page"** → small `QDialog` with `QPlainTextEdit` → on save call add/update. Add `apply_theme`.
5. [ ] **UI — indicator.** Mark annotated pages on the `SeekBar` — extend `set_bookmarks` [viewer L364] rendering, or add a parallel marker list. (A "Notes" side panel listing all notes with jump-to-page is a nice-to-have, not required for v1.)
6. [ ] **Smoke test.** full CRUD + an export→import round-trip asserting the note survives (mirror the existing bookmark assertions).
7. [ ] **Verify.** Open comic → add a note on page 3 → indicator appears → reopen → note still there.
8. [ ] **Ship.** pyc check → commit `Item 41 — per-page annotations/notes` → push → CI green → CLAUDE.md.

**Risk:** rely on FK `ON DELETE CASCADE` for comic deletion; remember to include notes in export.

---

# ITEM 39 — Batch tools (convert to CBZ, rename from metadata)  ·  no schema

**⚠ Highest data-safety item. Operate on copies. Confirm everything. Never delete originals silently.**

1. [ ] **New API only.** No schema change, BUT after a convert/rename you must update the DB path — add `update_file_path(self, comic_id, new_path)` updating `file_path` **and** `parent_dir` (mirror how `add_comic` derives `parent_dir`). Smoke-test it.
2. [ ] **New module `src/batch_tools.py`** on a `QThread` (model: `library_scanner.py`) with `progress`/`finished`/`error` signals.
3. [ ] **Convert CBR/CB7/CBT → CBZ:** `reader = open_comic(src)`; new `zipfile.ZipFile(dst, "w")`; for each page write `get_page_bytes(i)` as `page_{i:04d}.<ext>` (detect ext from byte magic, default `.jpg`); write next to original (or chosen output dir); `update_file_path`. **Do not delete the source** — deletion is a *separate, explicitly confirmed* action. **Skip/loudly warn on PDF** (PDF→CBZ is lossy and huge).
4. [ ] **Rename from metadata:** build filename from a template like `{series} #{series_number} - {title}`, sanitize for filesystem, `os.rename`, `update_file_path`. Handle collisions and read-only files.
5. [ ] **UI.** Multi-select (already supported via `MetadataDialog` flow) → **"Batch" submenu** in `_on_comic_context_menu` [L870] → operation → **dry-run confirmation dialog** showing counts + target names → progress dialog. Add `apply_theme` to new dialogs.
6. [ ] **Test.** Use `scripts/make_test_comic.py` to make a CBR/CBT fixture → convert → assert the output `.cbz` opens via `open_comic` with the **same page count**.
7. [ ] **Verify.** Multi-select two comics → Batch → Convert to CBZ → confirm dialog → progress → new `.cbz` files appear, originals untouched.
8. [ ] **Ship.** pyc check → commit `Item 39 — batch tools (convert to CBZ, rename from metadata)` → push → CI green → CLAUDE.md.

**Risk:** never destroy originals without explicit per-operation consent; filename collisions; read-only files; PDF blow-up.

---

# ITEM 43 — Public shelf sharing  ·  no schema (new export format)

**Goal:** export ONE shelf as a portable file; import recreates the shelf by matching comics the recipient already owns. A shared shelf is a *list of which comics*, not the files.

1. [ ] **`export_shelf(self, shelf_id, output_path) -> dict`** — write `{"format": "comic-reader-shelf", "version": 1, "shelf_name": ..., "comics": [{title, series, series_number, author, content_hash, file_path}…]}`. Include `content_hash` (populated by Item 36) so recipients can match by content.
2. [ ] **`import_shelf(self, input_path) -> dict`** — `create_shelf(name)` [L671]; for each entry match an existing comic by `content_hash` first, then by `(series, series_number, title)`; `add_comic_to_shelf` [L712] for matches; return `{matched, unmatched}` counts.
3. [ ] **UI.** Shelf right-click `_on_shelf_right_click` [L305] → **"Export shelf…"** → `QFileDialog`. Library menu [L479] → **"Import shelf…"**. After import, show a summary dialog: "Added N comics to '<shelf>'. M comics in the file aren't in your library." Add `apply_theme`.
4. [ ] **Smoke test.** create shelf + add comics → `export_shelf` to a temp file → fresh `:memory:` library seeded with the same comics by `content_hash` → `import_shelf` → assert shelf recreated with the right members and correct matched/unmatched counts.
5. [ ] **Verify.** Export a shelf → import it back → summary shows all matched.
6. [ ] **Ship.** pyc check → commit `Item 43 — share/import a single shelf` → push → CI green → CLAUDE.md.

**Risk:** set expectations (shares the *list*, not the comics). Match conservatively — never attach unrelated comics.

---

# ITEM 33 — Folder-based sync (docs + harden)  ·  no schema, minimal code

**Goal:** support (never require) the user's own folder-sync tool. Mostly documentation + resilience — **do NOT build a cloud sync service.**

1. [ ] **README.** Document the setup: keep comics in a synced folder (Syncthing/Dropbox/iCloud); **don't sync the live `.db`** — sync the *export JSON* instead, or accept per-device progress. Explain the trade-offs plainly.
2. [ ] **Harden rescan.** `rescan_all_folders` [L1207] must cleanly handle files that appeared/moved via sync.
3. [ ] **Harden missing files.** A comic whose file was synced away must show as **unavailable**, not crash the reader. Check the `open_comic` / `load_file` [L870] path: catch missing-file and surface a friendly "file unavailable" state.
4. [ ] **Optional:** a "Sync help" menu entry linking to the README section.
5. [ ] **Verify.** Move a comic file out of a library folder → open it → graceful "unavailable", no crash; move it back → rescan → it returns.
6. [ ] **Ship.** commit `Item 33 — document folder-based sync & harden rescan for synced folders` → push → CI green → CLAUDE.md.

**Risk:** scope creep into building actual sync — don't. Guidance + resilience only.

---

# ⚠ NETWORK TIER — STOP. Design review with the user before each of these.

Items 38, 42, and AI Organization touch the network, need API keys/credentials, and (42/AI) cost money. **Each requires an explicit conversation with the user before any code.** The offline core (Items 37→33) must be fully done and rock-solid first. Non-negotiable: the app stays 100% functional with **zero** servers/keys configured and **never** calls out on startup.

---

# ITEM 38 — OPDS / Komga / Kavita client  ·  schema v14  ·  ⚠ network, opt-in

**Before coding — confirm with the user:** read-only browse+stream as v1? download vs stream? which servers? This is the largest item.

1. [ ] **New dependency `requests`.** Add to `requirements.txt` **and** to `ComicReader.spec` (`hiddenimports` / verify PyInstaller bundles it). After it lands, **re-verify CI** bundles it on all three OS legs before continuing.
2. [ ] **Schema v14 — saved connections (no secrets in DB):**
   ```sql
   CREATE TABLE IF NOT EXISTS servers (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       name TEXT NOT NULL, kind TEXT NOT NULL,   -- 'opds'|'komga'|'kavita'
       base_url TEXT NOT NULL, username TEXT, created_at TEXT NOT NULL
   );
   ```
   Bump `_CURRENT_VERSION = 14`. **Store passwords/tokens in `QSettings`/JSON config keyed by server id — never in the DB, never in the export.**
3. [ ] **New module `src/server_client.py`.** OPDS feed = Atom XML (reuse the stdlib XML approach from `comicinfo.py`); per-server REST for covers/page streaming. All requests on a `QThread`; timeouts; graceful offline handling (never hang the UI).
4. [ ] **`RemoteReader(ComicReader)`** whose `get_page_bytes(i)` does an authenticated HTTP GET; plug into the existing page cache/preloader.
5. [ ] **UI.** Sidebar "Servers" section; **"Add server…"** dialog (name/kind/url/credentials + a **"Test connection"** button); browse remote library in the existing grid; open → stream. `apply_theme` everywhere.
6. [ ] **Test.** Thin parser test against a saved OPDS XML fixture; manual test against a real Komga/Kavita instance the user provides.
7. [ ] **Ship.** commit `Item 38 — OPDS/Komga/Kavita client (opt-in remote libraries)` → push → **CI green incl. `requests` bundled** → CLAUDE.md.

**Risk:** must work with zero servers; never block on network at startup; plaintext credentials in v1 (document; OS keyring is a follow-up).

---

# ITEM 42 — AI metadata lookup (Comic Vine)  ·  no schema (opt)  ·  ⚠ network, opt-in

**Before coding — confirm with the user:** Comic Vine needs a free API key the *user* registers. Per-comic or batch? Never auto-overwrite existing metadata.

1. [ ] **Dependency `requests`** (shared with Item 38; if 38 shipped, no new dep).
2. [ ] **Config.** Store the Comic Vine API key in `QSettings`/JSON config (document the plaintext caveat). Never hardcode.
3. [ ] **(Optional sub-scope)** add a `summary` column (the long-deferred CLAUDE.md decision) + a detail panel if you want to store/show summaries — its own small schema bump; treat as optional.
4. [ ] **New module `src/metadata_lookup.py`.** Query Comic Vine search by filename/series guess on a `QThread`; respect rate limits; present **candidate matches** in a dialog (poster + fields) — on confirm call `update_metadata` [L529]. **Never auto-overwrite** user data.
5. [ ] **UI.** Comic context menu [L870] → **"Look up metadata…"** → results dialog → "Apply". Batch variant over multi-select with per-comic confirmation. `apply_theme`.
6. [ ] **Test.** Mapping unit test against a saved sample API JSON; manual test with a real key.
7. [ ] **Ship.** commit `Item 42 — opt-in AI/Comic Vine metadata lookup` → push → CI green → CLAUDE.md.

**Risk:** opt-in + key required; never auto-overwrite; offline app unaffected when unused.

---

# AI ORGANIZATION — the flagship  ·  no schema (opt)  ·  ⚠ network/AI, opt-in

**Before coding — dedicated design pass with the user.** Uses the **Anthropic Claude API** (default to the latest Claude — a current Sonnet for cost/throughput on bulk metadata). Default behavior = **suggest, user approves** — never silently restructure the library.

1. [ ] **Dependency `anthropic`** (Python SDK). Add to `requirements.txt` + `ComicReader.spec`; re-verify CI bundling.
2. [ ] **Config.** Anthropic API key in config (plaintext caveat; OS keyring as follow-up). Off by default; app is 100% functional without it.
3. [ ] **New module `src/ai_organize.py`.** Build a **compact metadata-only** payload (title/series/number/author/year/tags/read-status) — **never send page images or file contents** (privacy + cost). Call the API on a `QThread`. **Use prompt caching** on the large, stable library-metadata context. Constrain output to strict JSON (tool use / JSON mode): each proposal `{name, rationale, comic_ids[]}`.
4. [ ] **Apply via existing APIs.** On user-accept, `create_shelf` [L671] + `add_comic_to_shelf` [L712] — **no new schema** unless you tag shelves as AI-suggested (optional `source` column).
5. [ ] **UI.** "Library → Organize with AI" → progress → **proposals review dialog** (accept/edit/reject each) → apply selected. `apply_theme`.
6. [ ] **Test.** Unit-test proposal-JSON → shelf-creation mapping with a canned response (no live API in tests). Manual test with a real key on the user's ~2000-comic library; confirm cost is reasonable (metadata-only + caching).
7. [ ] **Ship.** commit `AI organization — opt-in Claude-powered shelf suggestions` → push → CI green → CLAUDE.md.

**Risk:** never auto-restructure; metadata-only; every call user-initiated; no-op when offline/no key. Get sign-off on the interaction model before coding.

---

## Per-item definition of done (tick all before moving on)
- [ ] Matches existing patterns (`PHASE5_PLAN.md` §1).
- [ ] Schema: bumped `_CURRENT_VERSION`, appended (never edited) a migration block, updated `Comic` + `_row_to_comic`, updated export/import if portable.
- [ ] `library.py` smoke test extended; `venv/bin/python src/library.py` prints OK.
- [ ] `py_compile` clean on every touched file.
- [ ] New dialogs/widgets have `apply_theme` and look right in locked dark mode.
- [ ] Network/AI features opt-in, off by default; app fully works without them; no startup network calls.
- [ ] Destructive ops (Item 39) confirm first, never delete originals silently.
- [ ] App launched with DYLD command; told the user exactly what to click; they confirmed visually (no screenshots of their screen).
- [ ] `git ls-files | grep -c pyc` == 0.
- [ ] One feature = one commit (clear message + `Co-Authored-By` trailer), pushed to `main`.
- [ ] `gh run list -L 1` green for all three OS legs; fixed any CI-only packaging failure (esp. after adding `requests`/`anthropic`).
- [ ] `CLAUDE.md` "Current status" updated to record the item done.
