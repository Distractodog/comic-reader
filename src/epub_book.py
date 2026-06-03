"""Text (reflowable) EPUB parsing for the novel reader.

This is the *text* counterpart to the image-only EPUBReader in
archive_handler.py. It exposes an EPUB's chapters (the OPF spine of XHTML
documents), table-of-contents titles, metadata, cover image, and a way to read
embedded resources (images, CSS) so the reader view can render them.

It deliberately does no layout itself — rendering is left to Qt's built-in
QTextBrowser. Only image-less / mostly-text EPUBs are treated as books;
image-based comic EPUBs continue to go through EPUBReader.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from posixpath import dirname, normpath

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"}
HTML_EXTENSIONS = {".html", ".xhtml", ".htm"}


def _clean_title(text: str) -> str:
    """Normalise whitespace and strip TOC dot-leaders from a chapter title."""
    text = " ".join(text.split())
    text = re.sub(r"[ .]{2,}$", "", text)          # trailing "......" leaders
    return text.strip()


def is_text_epub(path: str) -> bool:
    """True if an EPUB is a text/novel ebook rather than an image-based comic.

    Heuristic: comic EPUBs carry roughly one image per page (images ≳ html
    documents). Novels are almost all XHTML text with at most a cover image.
    """
    try:
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
    except Exception:
        return False
    n_html = sum(1 for n in names if Path(n).suffix.lower() in HTML_EXTENSIONS)
    n_img = sum(1 for n in names if Path(n).suffix.lower() in IMAGE_EXTENSIONS)
    return n_html > 0 and n_img < n_html * 0.5


class EpubBook:
    """A parsed text EPUB: ordered chapters, titles, metadata, cover, resources."""

    def __init__(self, path: str):
        self.path = path
        self._zip = zipfile.ZipFile(path, "r")
        self.title: str | None = None
        self.author: str | None = None
        self._opf_dir = ""
        self._manifest: dict[str, str] = {}        # id -> absolute href in zip
        self._media_types: dict[str, str] = {}     # id -> media-type
        self._spine: list[str] = []                # ordered absolute hrefs
        self._cover_href: str | None = None
        self._titles: list[str] = []               # one per spine entry
        self._parse()

    # ----- parsing -----

    def _parse(self) -> None:
        opf_path = self._find_opf()
        if opf_path is None:
            return
        self._opf_dir = dirname(opf_path)
        opf = ET.fromstring(self._zip.read(opf_path))

        # Metadata
        title_el = opf.find(".//{*}metadata/{*}title")
        if title_el is not None and title_el.text:
            self.title = title_el.text.strip()
        creator_el = opf.find(".//{*}metadata/{*}creator")
        if creator_el is not None and creator_el.text:
            self.author = creator_el.text.strip()

        # Manifest
        cover_id_from_meta: str | None = None
        for meta in opf.findall(".//{*}metadata/{*}meta"):
            if meta.attrib.get("name") == "cover":
                cover_id_from_meta = meta.attrib.get("content")

        for item in opf.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id")
            href = item.attrib.get("href")
            if not item_id or not href:
                continue
            full = normpath(f"{self._opf_dir}/{href}" if self._opf_dir else href)
            self._manifest[item_id] = full
            self._media_types[item_id] = item.attrib.get("media-type", "")
            props = item.attrib.get("properties", "")
            if "cover-image" in props:
                self._cover_href = full

        if self._cover_href is None and cover_id_from_meta:
            self._cover_href = self._manifest.get(cover_id_from_meta)
        if self._cover_href is None:
            # Fallback: first manifest image whose name hints "cover".
            for item_id, href in self._manifest.items():
                if (
                    Path(href).suffix.lower() in IMAGE_EXTENSIONS
                    and "cover" in href.lower()
                ):
                    self._cover_href = href
                    break

        # Spine — the reading order of XHTML documents
        for itemref in opf.findall(".//{*}spine/{*}itemref"):
            item_id = itemref.attrib.get("idref")
            if not item_id or item_id not in self._manifest:
                continue
            href = self._manifest[item_id]
            if Path(href).suffix.lower() in HTML_EXTENSIONS or "html" in self._media_types.get(item_id, ""):
                self._spine.append(href)

        self._titles = self._build_titles(opf)

    def _find_opf(self) -> str | None:
        try:
            container = ET.fromstring(self._zip.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            if rootfile is not None:
                return rootfile.attrib.get("full-path")
        except Exception:
            pass
        # Fallback: any .opf in the archive
        for name in self._zip.namelist():
            if name.lower().endswith(".opf"):
                return name
        return None

    def _build_titles(self, opf) -> list[str]:
        """Map a human chapter title to each spine document, from the TOC.

        Tries the EPUB3 nav document first, then the EPUB2 NCX. Falls back to
        a generic "Chapter N" when nothing matches.
        """
        href_to_title: dict[str, str] = {}

        # EPUB3 nav
        nav_id = None
        for item in opf.findall(".//{*}manifest/{*}item"):
            if "nav" in item.attrib.get("properties", "").split():
                nav_id = item.attrib.get("id")
                break
        if nav_id and nav_id in self._manifest:
            href_to_title.update(self._parse_nav(self._manifest[nav_id]))

        # EPUB2 NCX
        if not href_to_title:
            ncx_href = None
            for item_id, mt in self._media_types.items():
                if mt == "application/x-dtbncx+xml":
                    ncx_href = self._manifest.get(item_id)
                    break
            if ncx_href:
                href_to_title.update(self._parse_ncx(ncx_href))

        titles = []
        for i, href in enumerate(self._spine):
            # TOC links may carry a #fragment; match on the path part.
            titles.append(href_to_title.get(href) or f"Chapter {i + 1}")
        return titles

    def _parse_nav(self, nav_href: str) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            doc = ET.fromstring(self._zip.read(nav_href))
        except Exception:
            return out
        base = dirname(nav_href)
        for a in doc.findall(".//{*}a"):
            href = a.attrib.get("href")
            text = _clean_title("".join(a.itertext()))
            if not href or not text:
                continue
            target = normpath(f"{base}/{href.split('#')[0]}" if base else href.split("#")[0])
            out.setdefault(target, text)
        return out

    def _parse_ncx(self, ncx_href: str) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            doc = ET.fromstring(self._zip.read(ncx_href))
        except Exception:
            return out
        base = dirname(ncx_href)
        for navpoint in doc.findall(".//{*}navPoint"):
            label = navpoint.find(".//{*}navLabel/{*}text")
            content = navpoint.find(".//{*}content")
            if label is None or content is None:
                continue
            src = content.attrib.get("src")
            text = _clean_title(label.text or "")
            if not src or not text:
                continue
            target = normpath(f"{base}/{src.split('#')[0]}" if base else src.split("#")[0])
            out.setdefault(target, text)
        return out

    # ----- public API -----

    def chapter_count(self) -> int:
        return len(self._spine)

    def chapter_titles(self) -> list[str]:
        return list(self._titles)

    def chapter_html(self, index: int) -> str:
        """Decoded XHTML for a spine document."""
        if not (0 <= index < len(self._spine)):
            return ""
        try:
            return self._zip.read(self._spine[index]).decode("utf-8", errors="ignore")
        except Exception:
            return ""

    def chapter_base_dir(self, index: int) -> str:
        """Directory of a spine document, for resolving relative resources."""
        if not (0 <= index < len(self._spine)):
            return ""
        return dirname(self._spine[index])

    def read_resource(self, abs_href: str) -> bytes | None:
        """Read a resource (image/css) by its absolute in-zip path."""
        try:
            return self._zip.read(abs_href)
        except Exception:
            return None

    def cover_image_bytes(self) -> bytes | None:
        if not self._cover_href:
            return None
        return self.read_resource(self._cover_href)

    def close(self) -> None:
        try:
            self._zip.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    import sys

    # Quick manual check: python src/epub_book.py path/to/book.epub
    if len(sys.argv) < 2:
        print("usage: epub_book.py <file.epub>")
        raise SystemExit(1)
    p = sys.argv[1]
    print("is_text_epub:", is_text_epub(p))
    with EpubBook(p) as book:
        print("title:", book.title)
        print("author:", book.author)
        print("chapters:", book.chapter_count())
        print("cover bytes:", len(book.cover_image_bytes() or b""))
        for i, t in enumerate(book.chapter_titles()[:8]):
            print(f"  [{i}] {t}  ({len(book.chapter_html(i))} chars)")
