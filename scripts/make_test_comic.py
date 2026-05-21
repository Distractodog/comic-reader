"""Generates sample comic files for testing. Creates a CBZ, CB7, CBT, and PDF."""

import io
import os
import tarfile
import zipfile
from pathlib import Path

import py7zr
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent.parent / "test_data"
PAGE_COUNT = 10
PAGE_SIZE = (800, 1200)


def make_page(n: int) -> bytes:
    """Create a single test page as PNG bytes."""
    # Cycle through a few background colors
    colors = [
        (220, 80, 80), (80, 180, 80), (80, 120, 220),
        (220, 180, 60), (180, 80, 200), (60, 200, 200),
    ]
    bg = colors[n % len(colors)]

    img = Image.new("RGB", PAGE_SIZE, bg)
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 200)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 60)
    except Exception:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    text = f"Page {n}"
    bbox = draw.textbbox((0, 0), text, font=font_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((PAGE_SIZE[0] - tw) / 2, (PAGE_SIZE[1] - th) / 2 - 100),
        text,
        fill="white",
        font=font_big,
    )

    subtitle = f"Test Comic - {n} of {PAGE_COUNT}"
    bbox = draw.textbbox((0, 0), subtitle, font=font_small)
    tw = bbox[2] - bbox[0]
    draw.text(
        ((PAGE_SIZE[0] - tw) / 2, PAGE_SIZE[1] / 2 + 150),
        subtitle,
        fill="white",
        font=font_small,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pages = {f"page_{i:02d}.png": make_page(i) for i in range(1, PAGE_COUNT + 1)}

    # CBZ (zip)
    cbz_path = OUT_DIR / "sample.cbz"
    with zipfile.ZipFile(cbz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in pages.items():
            zf.writestr(name, data)
    print(f"Wrote {cbz_path}")

    # CB7 (7z)
    cb7_path = OUT_DIR / "sample.cb7"
    with py7zr.SevenZipFile(cb7_path, "w") as sz:
        for name, data in pages.items():
            sz.writestr(data, name)
    print(f"Wrote {cb7_path}")

    # CBT (tar)
    cbt_path = OUT_DIR / "sample.cbt"
    with tarfile.open(cbt_path, "w") as tf:
        for name, data in pages.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    print(f"Wrote {cbt_path}")

    # PDF (using Pillow's built-in PDF support)
    pdf_path = OUT_DIR / "sample.pdf"
    imgs = []
    for name in sorted(pages.keys()):
        imgs.append(Image.open(io.BytesIO(pages[name])).convert("RGB"))
    imgs[0].save(pdf_path, save_all=True, append_images=imgs[1:])
    print(f"Wrote {pdf_path}")

    print(f"\nAll test files written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
