# Comic Reader

A free, local-first desktop comic book reader. Supports CBZ, CBR, CB7, CBT, PDF, EPUB (both image comics and text novels), and loose image folders.

Comic Reader has no comic limit, no account requirement, no telemetry, and no required cloud service. Your library database stays on your computer and can be exported to a portable JSON backup.

## Features

- Opens CBZ, CBR, CB7, CBT, PDF, EPUB (image comics and text novels), and folders of images
- Built-in novel reader for text EPUBs — chapter navigation, adjustable font size, and remembered position
- Local SQLite library with folder bookshelf, shelves, smart shelves, tags, series grouping, hidden/restore view, and folder cover overrides
- Reading progress, read/unread status, bookmarks, seek bar, page thumbnails, spread mode, manga right-to-left mode, and webtoon scroll mode
- Library export/import as JSON
- Folder-based sync friendly: keep comics in Dropbox, iCloud Drive, Syncthing, etc., then rescan library folders when files change
- Fit-to-page, fit-to-width, zoom controls, fullscreen, and customizable keyboard shortcuts
- Drag and drop files onto the window to open them
- Dark interface, offline runtime, and standalone Windows build

## Notes

- CBR/RAR support uses the Python `rarfile` package. Windows CI builds bundle `unrar.exe` when the Chocolatey package is available; source builds need an `unrar`-compatible tool on PATH. CBZ/ZIP, CB7/7Z, CBT/TAR, PDF, EPUB, and image folders do not need it.
- EPUB support covers both image-based comic EPUBs (shown page-by-page) and text/novel EPUBs (rendered as flowing text with Qt's built-in renderer). Comic Reader auto-detects which kind a file is. Text rendering is "good novel" fidelity, not a full web-engine layout — fonts, paragraphs, headings, and embedded images work; complex EPUB CSS may not be pixel-perfect.

## Folder-Based Sync

Comic Reader does not run its own cloud service. If you want the same comic files on multiple machines, put the comic folders themselves inside a sync tool you already trust, such as Syncthing, Dropbox, iCloud Drive, OneDrive, or a NAS-mounted folder.

Recommended setup:

1. Keep your `.cbz`, `.cbr`, `.cb7`, `.cbt`, `.pdf`, and `.epub` files in a synced folder.
2. Add that synced folder to Comic Reader on each computer.
3. After files are added, moved, or renamed by the sync tool, use **Library → Rescan All Library Folders**.
4. Use **Library → Export Library** when you want a portable metadata/progress backup.

Do not sync the live SQLite database file while Comic Reader is open. SQLite is safe locally, but file-sync tools can copy a database mid-write and create conflicts. Sync the comic files, and either keep reading progress per device or exchange deliberate JSON exports/imports.

If a sync tool temporarily removes a file or leaves it as an online-only placeholder, Comic Reader will show a friendly unavailable-file message instead of crashing. Restore or download the file, then rescan.

## Download

Grab the latest Windows `.exe` from the [Releases](../../releases) page, or from the most recent successful build in the [Actions](../../actions) tab. CI also builds macOS and Linux artifacts.

## Running from source (for development)

```bash
pip install -r requirements.txt
python src/main.py
```

On the project Mac, use Homebrew Python 3.12 and the expat workaround documented in `CLAUDE.md`:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib venv/bin/python src/main.py
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+O` | Open file |
| `Ctrl+Shift+O` | Open folder |
| `Right` / `Space` / `PgDn` | Next page |
| `Left` / `Backspace` / `PgUp` | Previous page |
| `Home` / `End` | First / Last page |
| `1` | Fit Page |
| `2` | Fit Width |
| `3` | Actual Size |
| `Ctrl++` / `Ctrl+-` | Zoom in / out |
| `F11` | Fullscreen |
