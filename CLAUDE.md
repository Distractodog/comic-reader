# Comic Reader — Project Context for Claude

## What this project is

A standalone Windows desktop comic book reader app. Single-window app that opens comic archive files and displays the pages with navigation/zoom, backed by a local SQLite library with a folder-based bookshelf UI.

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

## Current status (as of 2026-06-03)

- **Phase 5 in progress** (see `PHASE5_PLAN.md` + `PHASE5_TASKS.md`). DB schema now at **v11**. Done so far:
  - Item 40: per-comic fit mode + zoom (schema v8)
  - Item 37: per-comic cover override from page or image file; `cover_override` flag (schema v9) survives rescans
  - Item 36: duplicate detection — cheap content signature (size + first/last 64 KB sha256), background `DuplicateScanner`, review dialog with per-copy hide (schema v10 indexes `content_hash`)
  - Item 34: reading statistics — `reading_events` log (schema v11), session timer in `MainWindow` (net forward pages + capped time), `StatsDialog` with paintEvent pages/day chart
  - Remaining offline order: 35 → 41 → 39 → 43 → 33, then the network tier (38, 42, AI Org)
- **Text/novel EPUB reading** (user-requested, off-roadmap): text EPUBs now open in a dedicated `EbookViewer` (QTextBrowser, paper-themed page, chapter nav, font sizing, remembered chapter). Auto-detected vs image comic EPUBs via `epub_book.is_text_epub()`. Stack index 3. Scanner stores chapter count as `page_count` and pulls the OPF cover. No new dependency.

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
