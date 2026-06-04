# Phase 5 — Remaining Task Breakdown (companion to `PHASE5_PLAN.md`)

**What this file is:** the step-by-step checklist for the **remaining** Phase 5 items. `PHASE5_PLAN.md` is the *strategy* (why, design, risks, and the reusable patterns in its Section 1). **This file is the *execution order* — atomic, numbered, tick-as-you-go tasks.** Keep both open: when a step says "follow the migration recipe," that recipe lives in `PHASE5_PLAN.md` §1.1.

**Audience:** the implementing agent. The user does **not** write code — explain in plain language, never ask them to edit code.

**Golden rules (do not violate):**
- **One item = one commit = one push.** Never batch items. The user reviews each push.
- **Verify every code anchor before editing.** Line numbers below are accurate as of the baseline (commit `a0d1db3`, schema **v11**) but *shift as you add code*. Always `grep` the symbol named in brackets `[name]` rather than trusting the raw line number.
- **Claim the next free schema version at implementation time** (`grep -n "_CURRENT_VERSION =" src/library.py`). The versions below assume the exact order in this file; reordering shifts them.
- After each item: run app (DYLD command), tell the user exactly what to click, let them confirm **visually (no screenshots of their screen)**, then `py_compile` → `library.py` smoke test → commit → push → confirm CI green for all 3 OS legs → tick `CLAUDE.md` status.

---

## Baseline (already done — do NOT redo)
- ✅ **Item 0** — CI build baseline verified green (Win/Mac/Linux).
- ✅ **Item 40** — per-comic fit mode + zoom, **schema v8**. Commit `fc3757b`.
- ✅ **Item 37** — per-comic cover override (page or image file), survives rescans, **schema v9**. Commit `5e59771`.
- ✅ **Item 36** — duplicate detection via content signature, `DuplicateScanner` + review dialog, **schema v10**. Commit `5ebbd7f`.
- ✅ **Item 34** — reading statistics (`reading_events` log + session timer + `StatsDialog`), **schema v11**. Commit `3f3e390`.
- ✅ **Off-roadmap** — text/novel EPUB reader (`epub_book.py`, `ebook_viewer.py`, stack index **3**), true page-box pagination. Commit `a0d1db3`. *This changes a few items below — see the ⚠ EPUB notes.*

## Remaining order at a glance
| # | Item | Title | Next schema | Tier |
|---|------|-------|-------------|------|
| 1 | 35 | Reading queue / read-next | **v12** (table) | offline |
| 2 | 41 | Annotations / notes per page | **v13** (table) | offline |
| 3 | 39 | Batch tools (convert/rename) | none | offline |
| 4 | 43 | Public shelf sharing | none | offline |
| 5 | 33 | Folder-based sync (docs/harden) | none | offline |
| 6 | 38 | OPDS / Komga / Kavita client | **v14** (table) | ⚠ network — design review first |
| 7 | 42 | AI metadata lookup (Comic Vine) | none/opt | ⚠ network — design review first |
| 8 | — | **AI Organization** (Claude) | none/opt | ⚠ network — flagship, design review first |

**Items 1–5 are the pure-offline block. Land all of them before touching 6–8.** Items 6–8 each require an explicit design conversation with the user (they cost money / need API keys) — do not start coding them without sign-off.

## Verified code anchors (baseline `a0d1db3`, schema v11)
`library.py`: `_CURRENT_VERSION = 11` **L269** · `Comic` dataclass **L48** · `_row_to_comic` **L90** · `_migrate` **L286** (last block = v11, **L368–370**) · schema constants **L162–266** · `transaction()` **L424** · `set_hidden` **L513** · `update_progress` **L568** · `update_metadata` **L594** · `set_cover_path` **L610** · `set_cover_override` **L617** · `set_content_hash` **L628** · `record_reading` **L662** · `get_stats` **L683** · `create_shelf` **L847** · `add_comic_to_shelf` **L888** · `toggle_bookmark` **L1010** · `export_to_json` **L1109** · `import_from_json` **L1193**
`bookshelf.py`: `_on_comic_context_menu` **L870** · `_on_folder_context_menu` **L1009**
`main_window.py`: `_Sidebar` **L111** · `_on_shelf_right_click` **L311** · `set_active` **L323** (sentinels: Folders `-1`, Hidden `-2`) · `_stack.addWidget` order **L408–411** (0 bookshelf · 1 viewer · 2 webtoon · 3 ebook) · `_build_menus` **L502** · `_back_to_library` **L669** · `_show_reader_menu` **L692** · `load_file` **L936** · `_save_progress` **L1213** · `rescan_all_folders` **L1398**
`viewer.py`: `FitMode` **L14** · `restore_view_state` **L216** · `SeekBar` **L337** · `set_bookmarks` **L364** · `ThumbnailStrip` **L499**
`ebook_viewer.py`: `_PageCanvas` **L91** · `relayout` **L141** (page count depends on viewport + font size — **page indices are NOT stable**) · `set_page` **L188**
Next free **sidebar sentinel**: `-3` (then `-4`, …). Next free **stack index**: `4`. Next free **schema version**: `v12`.

---

# ITEM 35 — Reading queue / "read next"  ·  schema v12

**Goal:** a user-curated, ordered "read next" list.

1. [ ] **Schema v12.** Add constant near the other schema strings (`grep -n "_SCHEMA_V11" library.py`):
   ```sql
   CREATE TABLE IF NOT EXISTS reading_queue (
       comic_id INTEGER PRIMARY KEY REFERENCES comics(id) ON DELETE CASCADE,
       position INTEGER NOT NULL DEFAULT 0,
       added_at TEXT    NOT NULL
   );
   ```
   Append an `if version < 12:` block to `_migrate` mirroring the v11 block (`executescript` → `PRAGMA user_version = 12` → `version = 12`). Bump `_CURRENT_VERSION = 12`.
2. [ ] **`library.py` API.** `add_to_queue(comic_id)` (position = current max+1, UTC-ISO `added_at`), `remove_from_queue(comic_id)`, `is_in_queue(comic_id) -> bool`, `get_queue() -> list[Comic]` (JOIN comics, `WHERE hidden = 0`, `ORDER BY position`), `move_in_queue(comic_id, new_position)` (renumber to stay contiguous).
3. [ ] **UI — context menu.** In `_on_comic_context_menu` [L870]: toggle **"Add to reading queue"** / **"Remove from queue"** based on `is_in_queue`. (Works for any comic, including text/image EPUBs — they're rows in `comics` like anything else.)
4. [ ] **UI — sidebar.** Add sentinel `-3` **"Reading Queue"** in `_Sidebar` [L111] + a branch in `set_active` [L323] and the repopulate path showing `get_queue()` as an ordered grid. Reorder via right-click **"Move up" / "Move down"** → `move_in_queue`. (Drag-reorder is optional polish — skip for v1.)
5. [ ] **Optional:** when a comic hits `read`, *offer* (don't silently do) auto-remove from queue.
6. [ ] **Smoke test.** add 3 → assert order; `move_in_queue` → assert new order; `remove_from_queue` → assert contiguous positions remain.
7. [ ] **Verify.** Right-click comic → Add to reading queue → sidebar "Reading Queue" shows it; Move up/down reorders.
8. [ ] **Ship.** pyc check → commit `Item 35 — reading queue (read-next list)` → push → CI green → CLAUDE.md.

**Risk:** keep `position` contiguous on removal; don't conflate the queue with shelves in the UI.

---

# ITEM 41 — Annotations / notes per page  ·  schema v13

**Goal:** attach a text note to a specific page; show an indicator; view/edit/delete; export with the comic.

**⚠ EPUB note:** the text-ebook viewer repaginates on font-size / window changes, so a stored `page_index` is **not stable** for ebooks (`ebook_viewer.relayout` recomputes the count). **Scope v1 annotations to the image readers only** — gate the "Add note" action to `_stack.currentIndex() in (1, 2)` (comic + webtoon). Defer ebook notes (they'd need to anchor to a chapter/character offset, not a page) and say so in the reader menu by simply not offering the action there.

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
   Append `if version < 13:` block, bump `_CURRENT_VERSION = 13`.
2. [ ] **`library.py` API.** `add_annotation(comic_id, page_index, body) -> int`, `update_annotation(id, body)`, `delete_annotation(id)`, `get_annotations(comic_id) -> list`, `get_annotation_for_page(comic_id, page_index)`. UTC-ISO timestamps.
3. [ ] **Export/import.** Extend the per-comic payload alongside `bookmarks` in `export_to_json` [L1109] / `import_from_json` [L1193] so notes are portable. (Mirror the existing bookmark handling exactly.)
4. [ ] **UI — editor.** Reader menu `_show_reader_menu` [L692]: **"Add/edit note for this page"** (only when in stack 1/2 — see EPUB note) → small `QDialog` with `QPlainTextEdit` → on save call add/update. Add `apply_theme`.
5. [ ] **UI — indicator.** Mark annotated pages on the `SeekBar` — extend `set_bookmarks` [viewer L364] rendering, or add a parallel marker list. (A "Notes" side panel listing all notes with jump-to-page is a nice-to-have, not required for v1.)
6. [ ] **Smoke test.** full CRUD + an export→import round-trip asserting the note survives (mirror the bookmark assertions).
7. [ ] **Verify.** Open a CBZ → add a note on page 3 → indicator appears → reopen → note still there. Confirm the action is absent when reading a text EPUB.
8. [ ] **Ship.** pyc check → commit `Item 41 — per-page annotations/notes` → push → CI green → CLAUDE.md.

**Risk:** rely on FK `ON DELETE CASCADE` for comic deletion; remember to include notes in export; do **not** offer page-notes in the ebook viewer (unstable page indices).

---

# ITEM 39 — Batch tools (convert to CBZ, rename from metadata)  ·  no schema

**⚠ Highest data-safety item. Operate on copies. Confirm everything. Never delete originals silently.**

1. [ ] **New API only.** No schema change, BUT after a convert/rename you must update the DB path — add `update_file_path(self, comic_id, new_path)` updating `file_path` **and** `parent_dir`/`source_folder` (mirror how `add_comic` derives them). Smoke-test it.
2. [ ] **New module `src/batch_tools.py`** on a `QThread` (model: `library_scanner.py` / `dedupe_scanner.py`) with `progress`/`finished`/`error` signals.
3. [ ] **Convert CBR/CB7/CBT → CBZ:** `reader = open_comic(src)`; new `zipfile.ZipFile(dst, "w")`; for each page write `get_page_bytes(i)` as `page_{i:04d}.<ext>` (detect ext from byte magic, default `.jpg`); write next to original (or chosen output dir); `update_file_path`. **Do not delete the source** — deletion is a *separate, explicitly confirmed* action. **Skip/loudly warn on PDF and on BOTH EPUB kinds** (PDF→CBZ is lossy and huge; a text novel can't become a page-image archive at all — filter these out of the batch before it runs).
4. [ ] **Rename from metadata:** build filename from a template like `{series} #{series_number} - {title}`, sanitize for filesystem, `os.rename`, `update_file_path`. Handle collisions and read-only files.
5. [ ] **UI.** Multi-select (already supported via the `MetadataDialog` flow) → **"Batch" submenu** in `_on_comic_context_menu` [L870] → operation → **dry-run confirmation dialog** showing counts + target names (and which files were skipped, e.g. PDFs/EPUBs) → progress dialog. Add `apply_theme` to new dialogs.
6. [ ] **Test.** Use `scripts/make_test_comic.py` to make a CBR/CBT fixture → convert → assert the output `.cbz` opens via `open_comic` with the **same page count**.
7. [ ] **Verify.** Multi-select two comics → Batch → Convert to CBZ → confirm dialog → progress → new `.cbz` files appear, originals untouched.
8. [ ] **Ship.** pyc check → commit `Item 39 — batch tools (convert to CBZ, rename from metadata)` → push → CI green → CLAUDE.md.

**Risk:** never destroy originals without explicit per-operation consent; filename collisions; read-only files; PDF/EPUB must be filtered out of convert.

---

# ITEM 43 — Public shelf sharing  ·  no schema (new export format)

**Goal:** export ONE shelf as a portable file; import recreates the shelf by matching comics the recipient already owns. A shared shelf is a *list of which comics*, not the files.

1. [ ] **`export_shelf(self, shelf_id, output_path) -> dict`** — write `{"format": "comic-reader-shelf", "version": 1, "shelf_name": ..., "comics": [{title, series, series_number, author, content_hash, file_path}…]}`. Include `content_hash` (populated by Item 36's scanner) so recipients can match by content.
2. [ ] **`import_shelf(self, input_path) -> dict`** — `create_shelf(name)` [L847]; for each entry match an existing comic by `content_hash` first, then by `(series, series_number, title)`; `add_comic_to_shelf` [L888] for matches; return `{matched, unmatched}` counts.
3. [ ] **UI.** Shelf right-click `_on_shelf_right_click` [L311] → **"Export shelf…"** → `QFileDialog`. Library menu `_build_menus` [L502] → **"Import shelf…"**. After import, show a summary dialog: "Added N comics to '<shelf>'. M comics in the file aren't in your library." Add `apply_theme`.
4. [ ] **Smoke test.** create shelf + add comics → `export_shelf` to a temp file → fresh `:memory:` library seeded with the same comics by `content_hash` → `import_shelf` → assert shelf recreated with the right members and correct matched/unmatched counts.
5. [ ] **Verify.** Export a shelf → import it back → summary shows all matched.
6. [ ] **Ship.** pyc check → commit `Item 43 — share/import a single shelf` → push → CI green → CLAUDE.md.

**Risk:** set expectations (shares the *list*, not the comics). Match conservatively — never attach unrelated comics. `content_hash` is only present for comics the duplicate scanner has touched; fall back to metadata match and count the rest as unmatched.

---

# ITEM 33 — Folder-based sync (docs + harden)  ·  no schema, minimal code

**Goal:** support (never require) the user's own folder-sync tool. Mostly documentation + resilience — **do NOT build a cloud sync service.**

1. [ ] **README.** Document the setup: keep comics in a synced folder (Syncthing/Dropbox/iCloud); **don't sync the live `.db`** — sync the *export JSON* instead, or accept per-device progress. Explain the trade-offs plainly.
2. [ ] **Harden rescan.** `rescan_all_folders` [L1398] must cleanly handle files that appeared/moved via sync.
3. [ ] **Harden missing files.** A comic whose file was synced away must show as **unavailable**, not crash. Commit `e68416a` already made set-cover-from-a-missing-file friendly; extend the same care to the open path: `load_file` [L936] for image comics **and** the ebook branch (stack index 3) must catch missing-file and surface a friendly "file unavailable" state, not throw.
4. [ ] **Optional:** a "Sync help" menu entry linking to the README section.
5. [ ] **Verify.** Move a comic file out of a library folder → open it → graceful "unavailable", no crash; move it back → rescan → it returns. Repeat with a text EPUB.
6. [ ] **Ship.** commit `Item 33 — document folder-based sync & harden rescan for synced folders` → push → CI green → CLAUDE.md.

**Risk:** scope creep into building actual sync — don't. Guidance + resilience only.

---

# ⚠ NETWORK TIER — STOP. Design review with the user before each of these.

Items 38, 42, and AI Organization touch the network, need API keys/credentials, and (42/AI) cost money. **Each requires an explicit conversation with the user before any code.** The offline core (Items 35→33) must be fully done and rock-solid first. Non-negotiable: the app stays 100% functional with **zero** servers/keys configured and **never** calls out on startup.

---

# ITEM 38 — OPDS / Komga / Kavita client  ·  schema v14  ·  ⚠ network, opt-in

**Before coding — confirm with the user:** read-only browse+stream as v1? download vs stream? which servers? This is the largest item.

1. [ ] **New dependency `requests`.** Add to `requirements.txt` **and** verify PyInstaller bundles it (`ComicReader.spec` `hiddenimports` if needed). After it lands, **re-verify CI** bundles it on all three OS legs before continuing.
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
4. [ ] **`RemoteReader(ComicReader)`** whose `get_page_bytes(i)` does an authenticated HTTP GET; plug into the existing page cache / `preloader.py`.
5. [ ] **UI.** Sidebar "Servers" section (next free sentinel after the queue's `-3`); **"Add server…"** dialog (name/kind/url/credentials + a **"Test connection"** button); browse remote library in the existing grid; open → stream. `apply_theme` everywhere.
6. [ ] **Test.** Thin parser test against a saved OPDS XML fixture; manual test against a real Komga/Kavita instance the user provides.
7. [ ] **Ship.** commit `Item 38 — OPDS/Komga/Kavita client (opt-in remote libraries)` → push → **CI green incl. `requests` bundled** → CLAUDE.md.

**Risk:** must work with zero servers; never block on network at startup; plaintext credentials in v1 (document; OS keyring is a follow-up).

---

# ITEM 42 — AI metadata lookup (Comic Vine)  ·  no schema (opt)  ·  ⚠ network, opt-in

**Before coding — confirm with the user:** Comic Vine needs a free API key the *user* registers. Per-comic or batch? Never auto-overwrite existing metadata.

1. [ ] **Dependency `requests`** (shared with Item 38; if 38 shipped, no new dep).
2. [ ] **Config.** Store the Comic Vine API key in `QSettings`/JSON config (document the plaintext caveat). Never hardcode.
3. [ ] **(Optional sub-scope)** add a `summary` column (the long-deferred CLAUDE.md decision) + a detail panel if you want to store/show summaries — its own small schema bump; treat as optional.
4. [ ] **New module `src/metadata_lookup.py`.** Query Comic Vine search by filename/series guess on a `QThread`; respect rate limits; present **candidate matches** in a dialog (poster + fields) — on confirm call `update_metadata` [L594]. **Never auto-overwrite** user data.
5. [ ] **UI.** Comic context menu [L870] → **"Look up metadata…"** → results dialog → "Apply". Batch variant over multi-select with per-comic confirmation. `apply_theme`.
6. [ ] **Test.** Mapping unit test against a saved sample API JSON; manual test with a real key.
7. [ ] **Ship.** commit `Item 42 — opt-in AI/Comic Vine metadata lookup` → push → CI green → CLAUDE.md.

**Risk:** opt-in + key required; never auto-overwrite; offline app unaffected when unused.

---

# AI ORGANIZATION — the flagship  ·  no schema (opt)  ·  ⚠ network/AI, opt-in

**Before coding — dedicated design pass with the user.** Uses the **Anthropic Claude API** (default to the latest Claude — a current Sonnet for cost/throughput on bulk metadata). Default behavior = **suggest, user approves** — never silently restructure the library.

1. [ ] **Dependency `anthropic`** (Python SDK). Add to `requirements.txt` + verify `ComicReader.spec` bundling; re-verify CI on all three legs.
2. [ ] **Config.** Anthropic API key in config (plaintext caveat; OS keyring as follow-up). Off by default; app is 100% functional without it.
3. [ ] **New module `src/ai_organize.py`.** Build a **compact metadata-only** payload (title/series/number/author/year/tags/read-status) — **never send page images or file contents** (privacy + cost). Call the API on a `QThread`. **Use prompt caching** on the large, stable library-metadata context. Constrain output to strict JSON (tool use / JSON mode): each proposal `{name, rationale, comic_ids[]}`.
4. [ ] **Apply via existing APIs.** On user-accept, `create_shelf` [L847] + `add_comic_to_shelf` [L888] — **no new schema** unless you tag shelves as AI-suggested (optional `source` column).
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
- [ ] Text/image EPUBs considered where page-index or file-format assumptions matter (Items 41, 39, 33).
- [ ] Network/AI features opt-in, off by default; app fully works without them; no startup network calls.
- [ ] Destructive ops (Item 39) confirm first, never delete originals silently.
- [ ] App launched with DYLD command; told the user exactly what to click; they confirmed visually (no screenshots of their screen).
- [ ] `git ls-files | grep -c pyc` == 0.
- [ ] One feature = one commit (clear message + `Co-Authored-By` trailer), pushed to `main`.
- [ ] `gh run list -L 1` green for all three OS legs; fixed any CI-only packaging failure (esp. after adding `requests`/`anthropic`).
- [ ] `CLAUDE.md` "Current status" updated to record the item done.
