# Comic Reader — Project Context for Claude

## What this project is

A standalone Windows desktop comic book reader app. Single-window app that opens comic archive files and displays the pages with navigation/zoom.

## User context (important)

- **User does not write code.** They have surface-level Python familiarity only. Claude does ALL the building. Explain what's happening in plain language; do not ask the user to write or edit code themselves.
- **User builds on a Mac, but the target is Windows.** The Windows `.exe` is built in the cloud via GitHub Actions because you can't compile Windows apps directly on macOS.
- **The finished app must work offline.** No network calls at runtime.
- **User's GitHub account:** `Distractodog` (already authenticated via `gh` CLI on this Mac).
- **Repo is public** (free GitHub Actions minutes are generous for public repos).

## Tech stack

- **Language:** Python 3
- **UI:** PyQt6
- **File format support:**
  - `.cbz` / `.zip` → `zipfile` (stdlib)
  - `.cbr` / `.rar` → `rarfile` (needs unrar binary on system at runtime)
  - `.cb7` / `.7z` → `py7zr` (pure Python)
  - `.cbt` / `.tar` → `tarfile` (stdlib)
  - `.pdf` → `PyMuPDF` (a.k.a. `fitz`)
  - Loose images in a folder → direct read with `Pillow`-compatible formats
- **Packaging:** PyInstaller `--onefile --windowed`
- **CI/CD:** GitHub Actions on `windows-latest`, builds `.exe` on every push to `main`

## Project layout

```
comic-reader/
├── src/
│   ├── main.py              # Entry point — launches QApplication
│   ├── archive_handler.py   # ComicReader base + format-specific subclasses
│   ├── main_window.py       # QMainWindow with menus, toolbar, navigation
│   └── viewer.py            # ComicViewer (QScrollArea) with fit modes / zoom
├── requirements.txt
├── .github/workflows/build.yml  # Builds Windows .exe in cloud
├── .gitignore
├── README.md
└── CLAUDE.md
```

## How the code is organized

- `archive_handler.py` exposes a single factory `open_comic(path)` that returns a `ComicReader` for any supported format. Each subclass (`CBZReader`, `CBRReader`, `CB7Reader`, `CBTReader`, `PDFReader`, `ImageFolderReader`) implements `get_page_bytes(index)` and `page_count()`. Pages are sorted using a natural-sort key so `page2.jpg` comes before `page10.jpg`.
- `viewer.py` is a `QScrollArea` containing a `QLabel`. It holds the original `QPixmap` and rescales on resize or fit-mode change. Three fit modes: `ACTUAL_SIZE` (with zoom factor), `FIT_WIDTH`, `FIT_PAGE`.
- `main_window.py` owns the current `ComicReader` and `current_page` index, builds menus/toolbar/statusbar, handles drag-and-drop, and persists window geometry + last-used directory via `QSettings`.

## Build & run

- **Local dev (on Mac):**
  - Python: Homebrew Python 3.12 (`/opt/homebrew/bin/python3.12`) — NOT Apple's CLT Python 3.9 (`/usr/bin/python3`), which has hardened runtime that blocks PyQt6's dylibs.
  - Venv: `Documents/comic-reader/venv/` created with Homebrew Python 3.12.
  - macOS 16 Tahoe has a `libexpat` version mismatch with Homebrew Python bottles. The fix is to set `DYLD_LIBRARY_PATH` at launch:
    ```
    DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/python src/main.py
    ```
  - Dependencies: `brew install expat` must be installed (provides the newer libexpat). Then `DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/pip install -r requirements.txt`.
- **Windows build:** push to `main` → GitHub Actions runs → `.exe` appears as an artifact on the Actions run page. Tag with `v1.0.0` etc. to also publish a Release.

## Known things to watch / things still to do

- **CBR on Windows:** `rarfile` needs `unrar.exe` available. The current GitHub Actions build does NOT bundle it. If CBR files fail to open on the target Windows machine, we'll need to either bundle `unrar.exe` via PyInstaller's `--add-binary`, or swap to a pure-Python RAR library.
- **No library view yet.** App opens one file at a time; no list of recent files or bookshelf UI.
- **No double-page spread mode** (common in comic readers — show two pages side-by-side).
- **No bookmarks / reading progress per file.** Only the last-used folder is remembered.
- **App icon not set.** PyInstaller `--icon=path/to/icon.ico` would add one.

## Status

- Scaffold complete: all source files written, GitHub Actions workflow configured.
- Local environment working: Homebrew Python 3.12 + venv + all deps installed. App launches on Mac.
- Not yet pushed to GitHub.

## Conventions / preferences

- Keep code minimal and readable; this is a learning-adjacent project.
- Don't add features the user didn't ask for.
- Always ask before writing/editing files outside of work already scoped in conversation.
