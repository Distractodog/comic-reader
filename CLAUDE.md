# Comic Reader — Project Context for Claude

## What this project is

A standalone Windows desktop comic book reader app. Single-window app that opens comic archive files and displays the pages with navigation/zoom, backed by a local SQLite library with a folder-based bookshelf UI.

## At the start of every session (read first)

Before doing any work on this project:

1. **⛔ READ THE RELATED OBSIDIAN NOTES FIRST — THIS IS MANDATORY, DO IT EVERY TIME WITHOUT BEING ASKED.** This is the step agents keep skipping and the user is tired of having to remind every single one. It is not optional — not for quick questions, not for "I just need to check one thing," not for read-only tasks. The Obsidian notes are the real source of truth for the current state, recent decisions, and gotchas; **this CLAUDE.md lags behind them.** Skipping the notes means you act on stale context and repeat mistakes the user already paid to solve. So: **read the relevant Obsidian notes BEFORE you answer anything or touch any code.** The vault is at `/Users/ethanbristol/Documents/Obsidian Vault`. Search it for comic-reader / Cover 2.0 notes (e.g. glob `*omic*` and `*Cover 2*`) and read the relevant ones — at minimum the roadmap note and the most recent sessions — so you pick up the current state, recent decisions, and gotchas before touching code. Note that this app is also referred to as **"Cover 2.0"** in newer notes.
2. **Read the Documents CLAUDE.md** at `/Users/ethanbristol/Documents/CLAUDE.md` — it holds the session-time-tracking and "compile to Obsidian" rules that apply to all work in Documents.

## User context (important)

- **User does not write code.** Surface-level Python familiarity only. Claude does ALL the building. Explain what's happening in plain language; do not ask the user to write or edit code themselves.
- **User builds on a Mac, but the target is Windows.** The Windows `.exe` is built in the cloud via GitHub Actions because you can't compile Windows apps directly on macOS.
- **The finished app must work offline.** No network calls at runtime.
- **User's GitHub account:** `Distractodog` (already authenticated via `gh` CLI on this Mac).
- **Repo is public** (free GitHub Actions minutes are generous for public repos).
- **GitHub repo:** `github.com/Distractodog/comic-reader`

## Tech stack

- **Language:** Python 3.12 (Homebrew)
- **UI:** PyQt6
- **File format support:**
  - `.cbz` / `.zip` → `zipfile` (stdlib)
  - `.cbr` / `.rar` → `rarfile` (needs unrar binary on system at runtime)
  - `.cb7` / `.7z` → `py7zr` (pure Python, v1.x API — use `extractall()` not `read()`)
  - `.cbt` / `.tar` → `tarfile` (stdlib)
  - `.pdf` → `PyMuPDF` (a.k.a. `fitz`)
  - `.epub` → **two readers, auto-detected** by `epub_book.is_text_epub()`: image comic EPUBs use `EPUBReader` (archive_handler) shown page-by-page; text/novel EPUBs use `EpubBook` (epub_book.py) + `EbookViewer` (ebook_viewer.py), rendered as flowing text via PyQt6 `QTextBrowser` (no web-engine dependency)
  - Loose images in a folder → direct read with `Pillow`-compatible formats
- **Database:** SQLite via stdlib `sqlite3`, WAL mode, schema versioned via `PRAGMA user_version`
- **Packaging:** PyInstaller via `ComicReader.spec` so bundled resources are stable
- **CI/CD:** GitHub Actions builds Windows, macOS, and Linux PyInstaller artifacts on every push to `main`

## Project layout

```
comic-reader/
├── src/
│   ├── main.py              # Entry point — launches QApplication
│   ├── archive_handler.py   # ComicReader base + format-specific subclasses
│   ├── main_window.py       # QMainWindow — QStackedWidget with bookshelf + reader
│   ├── bookshelf.py         # BookshelfView, FolderTile, ComicTile
│   ├── viewer.py            # ComicViewer (QScrollArea) with fit modes / zoom
│   ├── library.py           # SQLite library DB — Comic/Folder dataclasses + CRUD
│   ├── library_scanner.py   # Background QThread folder scanner
│   └── thumbnails.py        # Cover thumbnail generation and caching
├── requirements.txt
├── .github/workflows/build.yml  # Builds Windows .exe in cloud
├── .gitignore
├── README.md
└── CLAUDE.md
```

## How the code is organized

- `archive_handler.py` exposes a factory `open_comic(path)` returning a `ComicReader` for any format. Each subclass (`CBZReader`, `CBRReader`, `CB7Reader`, `CBTReader`, `PDFReader`, `ImageFolderReader`) implements `get_page_bytes(index)` and `page_count()`. Pages are natural-sorted.
- `library.py` exposes a `Library` class with a full CRUD API over the SQLite DB. `get_folders()` groups comics by parent directory. `get_comics_in_folder(path)` returns comics in a specific folder. Schema is versioned: v1 = base, v2 adds `source_folder`.
- `thumbnails.py` generates 400×600 JPEG covers, cached in `QStandardPaths.CacheLocation/thumbnails/`.
- `library_scanner.py` walks a folder tree on a background `QThread`, emits `progress` and `finished` signals.
- `bookshelf.py` is a two-level browser: `BookshelfView` shows either folder tiles or comic tiles. `FolderTile` and `ComicTile` use `paintEvent` for rendering (cover image, title, progress bar on in-progress comics). Column count reflows on resize.
- `viewer.py` is a `QScrollArea` containing a `QLabel`. Three fit modes: `ACTUAL_SIZE`, `FIT_WIDTH`, `FIT_PAGE`.
- `main_window.py` owns a `QStackedWidget`: index 0 = `BookshelfView`, index 1 = `ComicViewer`. Opening a comic switches to index 1. "Library" toolbar button and Escape key return to index 0.

## Build & run

- **Local dev (on Mac):**
  - Python: Homebrew Python 3.12 (`/opt/homebrew/bin/python3.12`) — NOT Apple's CLT Python (`/usr/bin/python3`).
  - Venv: `Documents/comic-reader/venv/` created with Homebrew Python 3.12.
  - macOS 16 Tahoe has a `libexpat` version mismatch with Homebrew Python bottles. Fix:
    ```
    DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/python src/main.py
    ```
  - One-time setup:
    ```
    brew install python@3.12 expat
    /opt/homebrew/bin/python3.12 -m venv venv
    DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/pip install -r requirements.txt
    ```
- **Windows build:** push to `main` → GitHub Actions runs → `.exe` appears as workflow artifact. Tag `v1.0.0` etc. to publish a Release.

## Current status (as of 2026-06-04)

- **Phase 5 in progress** (see `PHASE5_PLAN.md` + `PHASE5_TASKS.md`). DB schema now at **v13**. Done so far:
  - Item 40: per-comic fit mode + zoom (schema v8)
  - Item 37: per-comic cover override from page or image file; `cover_override` flag (schema v9) survives rescans
  - Item 36: duplicate detection — cheap content signature (size + first/last 64 KB sha256), background `DuplicateScanner`, review dialog with per-copy hide (schema v10 indexes `content_hash`)
  - Item 34: reading statistics — `reading_events` log (schema v11), session timer in `MainWindow` (net forward pages + capped time), `StatsDialog` with paintEvent pages/day chart
  - Item 35: Reading Queue / read-next list (schema v12), sidebar view, context-menu add/remove/reorder, whole-book EPUB seek bar, matching comic seek bar styling, and end-of-book prompt to continue into the next queued book
  - Item 41: per-page annotations/notes for image comic readers (schema v13), exported/imported with library JSON, reader-menu add/edit/delete dialog, seek-bar note markers
  - Item 39: batch tools — convert CBR/CB7/CBT archives to CBZ on copies, rename files from metadata, update library paths safely, and skip PDF/EPUB conversion
  - Item 43: public shelf sharing — export/import a single shelf as a portable list, matching recipient libraries by content hash first and conservative metadata fallback
  - Item 33: folder-based sync stance documented in README; synced-away files now show a friendly unavailable-file message before either comic or text-EPUB loading
  - Offline Phase 5 block is complete. Remaining network tier requires design review before implementation: 38 (OPDS/Komga/Kavita), 42 (Comic Vine lookup), AI Organization
- **Text/novel EPUB reading** (user-requested, off-roadmap): text EPUBs now open in a dedicated `EbookViewer` using true page-box pagination (not scroll offsets), chapter nav, remembered font size/chapter (no on-screen A+/A− controls), a whole-book seek bar, sideswipe/slide page turns, spread mode (two pages side by side, session-only via reader ⋮ menu), offline dictionary (double-click a word or ⋮ → Look up word; ~108k-entry GCIDE SQLite bundled via `scripts/build_dictionary.py`, copied to app data on first use), and an explicit exit path. Auto-detected vs image comic EPUBs via `epub_book.is_text_epub()`. Stack index 3. Scanner stores chapter count as `page_count` and pulls the OPF cover. No new dependency. EPUB annotations are intentionally deferred because page indices change with font/window layout.

### Status before Phase 5 (as of 2026-05-29)

- Phase 1 complete (items 1–9 + Phase 3 pull-forwards: seek bar, click-zone nav, reader progress bar)
- Mini-polish pass complete (page slide animation, bookshelf↔reader fade, rounded tiles, typography)
- Phase 2 complete (items 11–16):
  - 11: user-created shelves + smart shelves (Recently Added, Currently Reading, Unread, Finished)
  - 12: smart shelves seeded via schema v3
  - 13: series grouping (SeriesTile, manual "Group as series", ungroup)
  - 14: tags/labels many-to-many (schema v4: `tags` + `comic_tags`; Tags field in metadata editor; search includes tags)
  - 15: watch folders (simplified to right-click "Rescan folder" on FolderTile)
  - 16: manual metadata editor (single + multi-select, MetadataDialog)
- Additional polish: sidebar nav (180px), dark mode locked, theme system, WA_StyledBackground fix, smooth nav transitions on all grid refreshes
- App launches and runs locally on Mac with the `DYLD_LIBRARY_PATH` workaround
- Phase 3 reading polish substantially underway/partly complete:
  - seek bar, click-zone nav, reader progress bar
  - page slide animation, bookshelf↔reader fade
  - bookmarks, thumbnail strip, spread mode, RTL manga mode, webtoon mode
  - page preloading and PDF/webtoon performance fixes
- Pre-Phase-4 user features complete:
  - hide/restore comics and folders via soft-hide flag
  - folder cover overrides from comic covers or arbitrary image files
- Phase 4 started:
  - library export/import to portable JSON
  - "Rescan All Library Folders" for folder-sync workflows
  - image-only EPUB support
  - README positioning for no limits, local-first, no telemetry, no required cloud
  - GitHub Actions matrix for Windows/macOS/Linux artifacts
  - PyInstaller spec includes fonts and can bundle `unrar.exe` on Windows CI
- Next: push and verify GitHub Actions artifacts; fix any CI-only packaging failures

## Settings Page — Plan (started 2026-06-13)

A dedicated **Settings area** is being built. The entry point exists: a gear
(⚙) `_RailButton` pinned to the very bottom of the sidebar rail (`_Sidebar` in
`main_window.py`), wired through a `settings_clicked` signal to
`MainWindow._open_settings`.

**Scaffold + navigation are DONE** (2026-06-13). `src/settings_view.py` holds the
`SettingsView` shell: a left `QListWidget` category nav + right `QStackedWidget` of
panels (one `_PlaceholderPanel` per section in `SECTIONS`), themed via `apply_theme`.
It is wired into the main-window `QStackedWidget` as **index 4**. `_open_settings`
switches to index 4 (hides reader/seek/thumb chrome); `_close_settings` returns to
the bookshelf. Escape, the back button (`back_requested`), and the sidebar
Library/Currently-Reading buttons (`_go_home` / `_show_currently_reading`) all leave
settings. Every panel is still a "coming soon" placeholder.

**ALL PANELS BUILT** (2026-06-13). `src/settings_view.py` now has real,
functional panels (no more placeholders) and a new `src/prefs.py` centralizes
global defaults in QSettings (typed getters/setters, namespaced keys). Panels
emit signals; `MainWindow` applies them live. Summary:

- **Appearance** — Theme Dark/Light (`apply_theme` swap, persisted, applied at
  startup; unlocks the previously-unused `themes.LIGHT`), Animations toggle
  (gates `_fade_switch` via `self._animations_enabled`), Bookshelf tile size
  Small/Medium/Large (`bookshelf.set_tile_scale` rescales module `TILE_W`/
  `COVER_H` + refresh), Sidebar-on-launch Collapsed/Expanded.
- **Reading** — default fit mode / reading mode / spread / zoom / direction
  (RTL), click-zone nav toggle (`viewer.set_click_nav`), page-turn animation
  toggle (`viewer.set_animate`), and pages-to-preload (`PagePreloader(radius=)`
  via `preloader._offsets_for_radius`). `Library.resolve_reading_settings` now
  takes an optional `defaults: ReadingSettings` (global base beneath the DB);
  `MainWindow._global_reading_defaults()` builds it from prefs. NOTE: because
  fit/zoom/mode are stored per-comic at scan time, global defaults mainly affect
  ad-hoc (non-library) opens + the `spread` field + direction; already-scanned
  comics keep their stored values.
- **Ebook & Text** — default font size (shares the existing `ebook_font_pt`
  key) + optional font family (`EbookViewer.set_font_family`, new).
- **Library & Data** — buttons routed through `MainWindow._on_library_action`
  to existing methods (add folder/files, rescan all, export/import, import
  shelf, duplicates, stats) plus new thumbnail cache regenerate/clear
  (`_regenerate_thumbnails` / `_clear_thumbnail_cache`).
- **Shortcuts** — button opens the existing `KeybindingDialog`.
- **Sidebar** — placeholder note only. The current `_Sidebar` rail is a FIXED
  button set with no dynamic shelves; `_HideSidebarDialog` exists but is unwired
  and has no persistence. Wire this once shelves can be pinned to the rail.
- **About** — app name/version (`APP_VERSION`/`APP_REPO_URL` in `app_info.py`),
  repo link, offline positioning.

Not yet committed. Next candidates: commit; optionally make new-comic scans
inherit the global reading defaults so they apply to library comics too; build
out the real Sidebar visibility panel when rail-shelves exist.

### Architecture (decided)

- **Full page inside the main window**, NOT a dialog. Add a new `SettingsView`
  widget to the existing `QStackedWidget` as a new index (current stack: 0 =
  bookshelf, 1 = viewer, 2 = webtoon, 3 = ebook → **settings = index 4**).
- `_open_settings` switches the stack to the settings index; Escape, the sidebar
  Library button, and a back affordance return to the bookshelf. Hide the
  reader/seek/thumb bars while settings is showing (same chrome handling as the
  bookshelf view).
- **Internal layout:** a left category list (`QListWidget`) + a right
  `QStackedWidget` of panels — one panel per section below. Mirror the app's
  look via `apply_theme(c)` like every other view.
- **Persistence:** global preferences go in `QSettings` (the app already uses
  `self._settings` / `bookshelf.app_settings()`). Namespace keys, e.g.
  `settings/theme`, `reading/default_fit_mode`. Per-comic / per-series / per-folder
  `ReadingSettings` already exist in the DB and are unchanged — the new global
  defaults sit *beneath* them.
- **Reading-defaults resolution:** extend `resolve_reading_settings` so the
  fallback order becomes comic-specific → series → folder → **global defaults
  (QSettings)** → hardcoded constant. Today it stops at the comic's own fields.

### Sections / panels

**1. Appearance**
- Theme: Dark / Light. `themes.LIGHT` is fully defined but currently unused —
  `apply_theme(themes.DARK)` is hardcoded at app start. Wire a theme selector that
  re-runs `apply_theme` across all views and persists the choice.
- Animations on/off: page-slide transition + bookshelf↔reader fade.
- Bookshelf tile size / column behavior.
- Sidebar starts expanded or collapsed (default for `_Sidebar.set_expanded`).

**2. Reading defaults** (new global defaults new comics inherit before any override)
- Default fit mode (`actual` / `width` / `page`).
- Default reading mode (`single` / `webtoon`) and default spread on/off.
- Default zoom.
- Default reading direction (LTR / RTL-manga).
- Click-zone navigation on/off; page-turn animation on/off.
- Page preload count (feeds `preloader.py`).

**3. Ebook / text**
- Default font size (currently persisted as `ebook_font_pt`) and font family for
  text EPUBs rendered in `EbookViewer`.

**4. Library & data**
- Manage library folders: add / rescan / remove (consolidates the Library menu).
- Thumbnail cache: regenerate or clear (`thumbnails.py` cache dir).
- Export / import library, import shelf.
- Links to existing tools: Scan for Duplicates, Reading Statistics.

**5. Shortcuts**
- Embed the existing keybindings editor (`keybindings.py`, "Customize Shortcuts"
  dialog) as a panel.

**6. Sidebar**
- Which library views / shelves appear in the rail (the existing
  hide-from-sidebar dialog, moved in here).

**7. About**
- App version, GitHub repo link, and the "100% offline / no telemetry" positioning.

### Build order

Build **one section at a time** (per the user's working style). Suggested order:
scaffold the `SettingsView` shell + navigation first, then Appearance (most visible
payoff and unlocks Light mode), then Reading defaults, then the remaining panels.
Each section: implement → relaunch locally for visual confirmation → only then move
on. Nothing ships to Windows until pushed (push triggers the GitHub Actions build).

## Known issues

- **macOS file dialog greys out comic files.** macOS Tahoe requires UTI registration for `.cbz`, `.cbr`, etc. in the file picker. Drag-and-drop and clicking tiles in the bookshelf both work as workarounds.
- **CBR on Windows:** CI tries to install `unrar` via Chocolatey and bundle `unrar.exe` through `ComicReader.spec`. If Chocolatey changes package paths or availability, CBR support may need a workflow adjustment.

## Feature roadmap (5 phases, 43 items)

See Obsidian note `2026-05-21 - Comic Reader GitHub push and feature roadmap` for the full roadmap.

## Deferred decisions

- **ComicInfo.xml `<Summary>` field:** Decided to skip storing `summary` during item 7 implementation. Would require a schema v3 migration (new `summary` column). No UI to display it yet, and item 9 search only covers title/series/author. Add when a comic detail/info panel is built (Phase 2 or later).

## Conventions

- Keep code minimal and readable.
- Don't add features the user didn't ask for.
- Tile implementation: custom `QWidget` per tile in `QGridLayout` (not `QListView` + delegate). Reason: progress bar fits naturally as a child widget; ~200 tiles is below scale threshold for delegates.
- Library is two-level: folders on main screen (~200), comics inside (~20 visible at a time).

## Polish strategy

No dedicated polish phase. Instead:
- **After Phase 1 completes:** mini-polish pass — rounded corners, bookshelf↔reader transition, typography refinements. All core UI surfaces exist by then so the whole picture is visible at once.
- **Phase 4 item 32:** App theming / true dark mode — the big visual overhaul lives here.
- **Ongoing:** small hover effects, spacing tweaks, and visual fixes can be added any time alongside feature work.
