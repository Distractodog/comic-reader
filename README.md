# Comic Reader

A simple Windows comic book reader. Supports CBZ, CBR, CB7, CBT, PDF, and loose image folders.

## Features

- Opens CBZ, CBR, CB7, CBT, PDF, and folders of images
- Fit-to-page, fit-to-width, and zoom controls
- Keyboard navigation (arrow keys, space, Page Up/Down, Home/End)
- Drag and drop files onto the window to open them
- Remembers window size and last folder

## Download

Grab the latest Windows `.exe` from the [Releases](../../releases) page, or from the most recent successful build in the [Actions](../../actions) tab.

## Running from source (for development)

```bash
pip install -r requirements.txt
python src/main.py
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
