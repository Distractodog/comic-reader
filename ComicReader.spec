# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path.cwd()
binary_name = os.environ.get("COMIC_READER_BINARY_NAME", "ComicReader")

datas = [
    (str(ROOT / "src" / "fonts"), "fonts"),
]
datas += collect_data_files("py7zr")

binaries = []
if sys.platform.startswith("win"):
    unrar_path = os.environ.get("COMIC_READER_UNRAR")
    if unrar_path and Path(unrar_path).exists():
        binaries.append((unrar_path, "."))


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
)
