# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path.cwd()
binary_name = os.environ.get("COMIC_READER_BINARY_NAME", "ComicReader")
app_display_name = os.environ.get("COMIC_READER_APP_NAME", "Cover 2.0")

ASSETS_DIR = ROOT / "src" / "assets"

datas = [
    (str(ROOT / "src" / "fonts"), "fonts"),
    (str(ASSETS_DIR), "assets"),
    (str(ROOT / "src" / "data" / "dictionary.db"), "data"),
    (str(ROOT / "src" / "data" / "dictionary_seed.db"), "data"),
]
datas += collect_data_files("py7zr")

binaries = []
if sys.platform.startswith("win"):
    unrar_path = os.environ.get("COMIC_READER_UNRAR")
    if unrar_path and Path(unrar_path).exists():
        binaries.append((unrar_path, "."))

app_icon_path = None
if sys.platform.startswith("win"):
    icon_candidate = ASSETS_DIR / "app-icon.ico"
elif sys.platform == "darwin":
    icon_candidate = ASSETS_DIR / "app-icon.icns"
else:
    icon_candidate = ASSETS_DIR / "app-icon.png"
if icon_candidate.exists():
    app_icon_path = str(icon_candidate)


a = Analysis(
    ["src/main.py"],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=binary_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon_path,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=f"{app_display_name}.app",
        icon=app_icon_path,
        bundle_identifier="com.comicreader.cover",
        info_plist={
            "CFBundleName": app_display_name,
            "CFBundleDisplayName": app_display_name,
            "CFBundleShortVersionString": "2.0",
        },
    )
