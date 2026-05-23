"""Parses ComicInfo.xml metadata from comic archives."""

from __future__ import annotations

import tarfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _parse_xml(data: bytes) -> dict:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}

    def _text(tag: str) -> str | None:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text and el.text.strip() else None

    result: dict = {}
    if (v := _text("Title")):
        result["title"] = v
    if (v := _text("Series")):
        result["series"] = v
    if (v := _text("Number")):
        try:
            result["series_number"] = float(v)
        except (ValueError, TypeError):
            pass
    if (v := _text("Writer")):
        result["author"] = v
    if (v := _text("Publisher")):
        result["publisher"] = v
    if (v := _text("Year")):
        try:
            result["year"] = int(v)
        except (ValueError, TypeError):
            pass
    return result


def parse_comicinfo(file_path: str) -> dict | None:
    """Extract and parse ComicInfo.xml from a comic archive.

    Returns a dict of metadata fields (any of: title, series, series_number,
    author, publisher, year) or None if not found or format doesn't support it.
    """
    p = Path(file_path)
    if p.is_dir():
        return None

    suffix = p.suffix.lower()
    try:
        if suffix in (".cbz", ".zip"):
            with zipfile.ZipFile(file_path, "r") as zf:
                for name in zf.namelist():
                    if name.lower() == "comicinfo.xml":
                        return _parse_xml(zf.read(name)) or None

        elif suffix in (".cbr", ".rar"):
            import rarfile
            with rarfile.RarFile(file_path) as rf:
                for name in rf.namelist():
                    if name.lower() == "comicinfo.xml":
                        return _parse_xml(rf.read(name)) or None

        elif suffix in (".cb7", ".7z"):
            import py7zr
            # Use read() here (not extractall) — we only need one small XML file,
            # not all image pages. The extractall-only note in CLAUDE.md applies
            # to image bytes (where read() had encoding issues), not XML text.
            with py7zr.SevenZipFile(file_path, mode="r") as sz:
                names = sz.getnames()
                ci_name = next((n for n in names if n.lower() == "comicinfo.xml"), None)
                if ci_name:
                    data_map = sz.read([ci_name])
                    if ci_name in data_map:
                        return _parse_xml(data_map[ci_name].read()) or None

        elif suffix in (".cbt", ".tar"):
            with tarfile.open(file_path, "r") as tf:
                for member in tf.getmembers():
                    if member.name.lower() == "comicinfo.xml":
                        f = tf.extractfile(member)
                        if f:
                            return _parse_xml(f.read()) or None

    except Exception:
        pass

    return None
