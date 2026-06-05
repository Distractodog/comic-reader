#!/usr/bin/env python3
"""Build macOS app icons using Apple's 1024pt icon grid."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "src" / "assets"
SOURCE = ASSETS / "app-icon-source.png"
OUT_PNG = ASSETS / "app-icon.png"
ICONSET = ASSETS / "app-icon.iconset"
ICNS = ASSETS / "app-icon.icns"
ICO = ASSETS / "app-icon.ico"

# Apple macOS app icon grid (1024pt reference artboard).
CANVAS = 1024
SHAPE = 824
GUTTER = (CANVAS - SHAPE) // 2  # 100pt on each side
CORNER_RADIUS = 185.4

ICONSET_SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def _scaled(value: float, size: int) -> int:
    return max(1, round(value * size / CANVAS))


def macos_icon_mask(size: int) -> Image.Image:
    """Continuous-corner mask sized like Apple's macOS icon template."""
    shape = _scaled(SHAPE, size)
    gutter = (size - shape) // 2
    radius = _scaled(CORNER_RADIUS, size)

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (gutter, gutter, gutter + shape - 1, gutter + shape - 1),
        radius=radius,
        fill=255,
    )
    return mask


def square_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def render_icon(source: Image.Image, size: int) -> Image.Image:
    shape = _scaled(SHAPE, size)
    gutter = (size - shape) // 2

    cropped = square_crop(source).convert("RGBA")
    artwork = cropped.resize((shape, shape), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(artwork, (gutter, gutter), artwork)

    mask = macos_icon_mask(size)
    alpha = Image.composite(
        canvas.getchannel("A"),
        Image.new("L", (size, size), 0),
        mask,
    )
    canvas.putalpha(alpha)
    return canvas


def build_iconset(source: Image.Image) -> None:
    ICONSET.mkdir(parents=True, exist_ok=True)
    for px, name in ICONSET_SIZES:
        icon = render_icon(source, px)
        if icon.size != (px, px):
            raise RuntimeError(f"{name} is {icon.size[0]}x{icon.size[1]}, expected {px}x{px}")
        icon.save(ICONSET / name, format="PNG", optimize=True)


def build_icns() -> None:
    subprocess.run(
        ["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)],
        check=True,
    )


def build_ico(source: Image.Image) -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [render_icon(source, side) for side in sizes]
    images[0].save(
        ICO,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )


def resolve_source_path() -> Path:
    if SOURCE.exists():
        return SOURCE
    if OUT_PNG.exists():
        return OUT_PNG
    return SOURCE


def main() -> int:
    src_path = resolve_source_path()
    if not src_path.exists():
        print(f"Missing source icon: {SOURCE}", file=sys.stderr)
        return 1

    source = Image.open(src_path)
    build_iconset(source)
    build_icns()
    build_ico(source)

    master = render_icon(source, CANVAS)
    master.save(OUT_PNG, format="PNG", optimize=True)
    print(f"Built {ICNS} and {ICO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
