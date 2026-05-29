# Comic Reader

A free, local-first desktop comic book reader. Supports CBZ, CBR, CB7, CBT, PDF, image-only EPUB, and loose image folders.

Comic Reader has no comic limit, no account requirement, no telemetry, and no required cloud service. Your library database stays on your computer and can be exported to a portable JSON backup.

## Features

- Opens CBZ, CBR, CB7, CBT, PDF, image-only EPUB, and folders of images
- Local SQLite library with folder bookshelf, shelves, smart shelves, tags, series grouping, hidden/restore view, and folder cover overrides
- Reading progress, read/unread status, bookmarks, seek bar, page thumbnails, spread mode, manga right-to-left mode, and webtoon scroll mode
- Library export/import as JSON
- Folder-based sync friendly: keep comics in Dropbox, iCloud Drive, Syncthing, etc., then rescan library folders when files change
- Fit-to-page, fit-to-width, zoom controls, fullscreen, and customizable keyboard shortcuts
- Drag and drop files onto the window to open them
- Dark interface, offline runtime, and standalone Windows build

## Notes

- CBR/RAR support uses the Python `rarfile` package. Windows CI builds bundle `unrar.exe` when the Chocolatey package is available; source builds need an `unrar`-compatible tool on PATH. CBZ/ZIP, CB7/7Z, CBT/TAR, PDF, EPUB, and image folders do not need it.
- EPUB support is for image-based comic EPUBs. Text reflow EPUB reading is intentionally out of scope.

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
