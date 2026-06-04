#!/usr/bin/env python3
"""Download GCIDE (GNU, public domain) and build src/data/dictionary.db.

Run before release builds or locally once:
  python scripts/build_dictionary.py

GCIDE: https://ftp.gnu.org/gnu/gcide/
License: GPL v3+ (dictionary text; bundling as data file is standard for GPL apps).
"""

from __future__ import annotations

import re
import sqlite3
import tarfile
import urllib.request
from pathlib import Path

GCIDE_URL = "https://ftp.gnu.org/gnu/gcide/gcide-0.53.tar.xz"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "data" / "dictionary.db"
DICT_FORMAT_VERSION = "2"
MAX_DEFINITION_CHARS = 16_000

ENT_RE = re.compile(r"<ent>([^<]+)</ent>", re.IGNORECASE)
DEF_RE = re.compile(r"<def>(.*?)</def>", re.IGNORECASE | re.DOTALL)
POS_RE = re.compile(r"<pos>([^<]+)</pos>", re.IGNORECASE)
SN_RE = re.compile(r"<sn>(\d+)\.</sn>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def strip_markup(text: str) -> str:
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _sense_label(chunk: str, def_start: int, fallback: int) -> str:
    before = chunk[:def_start]
    matches = list(SN_RE.finditer(before))
    if matches:
        return f"{matches[-1].group(1)}."
    if fallback > 0:
        return f"{fallback}."
    return ""


def _extract_pos(chunk: str) -> str:
    m = POS_RE.search(chunk)
    return strip_markup(m.group(1)) if m else ""


def parse_gcide(cide_dir: Path) -> dict[str, str]:
    """Parse CIDE.* files into word -> formatted definition text."""
    merged: dict[str, str] = {}
    current_word: str | None = None
    current_pos: str = ""
    current_lines: list[str] = []
    sense_fallback = 0

    def flush() -> None:
        nonlocal current_word, current_pos, current_lines, sense_fallback
        if not current_word or not current_lines:
            current_word = None
            current_pos = ""
            current_lines = []
            sense_fallback = 0
            return
        key = current_word.lower()
        header = f"[{current_pos}]\n" if current_pos else ""
        body = header + "\n".join(current_lines)
        if len(body) > MAX_DEFINITION_CHARS:
            body = body[:MAX_DEFINITION_CHARS].rstrip() + "…"
        if key in merged:
            merged[key] = merged[key] + "\n\n—\n\n" + body
        else:
            merged[key] = body
        current_word = None
        current_pos = ""
        current_lines = []
        sense_fallback = 0

    files = sorted(cide_dir.glob("CIDE.*"))
    if not files:
        raise FileNotFoundError(f"No CIDE.* files in {cide_dir}")

    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        chunks = re.split(r"(?=<p>)", raw)
        for chunk in chunks:
            if "<def>" not in chunk and "<ent>" not in chunk:
                continue
            ent_m = ENT_RE.search(chunk)
            if ent_m:
                flush()
                current_word = ent_m.group(1).strip()
                current_pos = _extract_pos(chunk)
                sense_fallback = 0
            if current_word is None:
                continue
            if not current_pos:
                pos = _extract_pos(chunk)
                if pos:
                    current_pos = pos
            for def_m in DEF_RE.finditer(chunk):
                cleaned = strip_markup(def_m.group(1))
                if not cleaned:
                    continue
                label = _sense_label(chunk, def_m.start(), sense_fallback)
                if label:
                    sn = int(label[:-1])
                    sense_fallback = sn + 1
                    current_lines.append(f"{label} {cleaned}")
                else:
                    sense_fallback += 1
                    current_lines.append(f"{sense_fallback}. {cleaned}")
        flush()

    return merged


def download_gcide(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / "gcide-0.53.tar.xz"
    if not archive.is_file():
        print(f"Downloading {GCIDE_URL} …")
        urllib.request.urlretrieve(GCIDE_URL, archive)
    extract_root = cache_dir / "gcide-0.53"
    if not extract_root.is_dir():
        print("Extracting GCIDE …")
        with tarfile.open(archive, "r:xz") as tar:
            tar.extractall(cache_dir)
    return extract_root


def write_sqlite(entries: dict[str, str], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(out)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(
        "CREATE TABLE definitions (word TEXT PRIMARY KEY COLLATE NOCASE, definition TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('format', ?)",
        (DICT_FORMAT_VERSION,),
    )
    batch = ((w, d) for w, d in sorted(entries.items()))
    conn.executemany("INSERT INTO definitions (word, definition) VALUES (?, ?)", batch)
    conn.commit()
    conn.close()


def main() -> None:
    cache = ROOT / "build" / "gcide-cache"
    cide_dir = download_gcide(cache)
    print("Parsing GCIDE entries …")
    entries = parse_gcide(cide_dir)
    if len(entries) < 10_000:
        raise RuntimeError(f"Unexpectedly few entries ({len(entries)}); parse may have failed")
    write_sqlite(entries, OUT)
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Wrote {len(entries):,} entries ({size_mb:.1f} MB) to {OUT}")


if __name__ == "__main__":
    main()
