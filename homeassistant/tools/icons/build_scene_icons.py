#!/usr/bin/env python3
"""Build the scene-jump key icons from the owner's silhouette.

Reads ``services/scenes/icons/source.gif`` (a dark figure on a light
background), uses its darkness as the mask, crops to the figure, scales it to
fit 112x112 with 10 px padding (LANCZOS) and writes ``scene.png`` (white figure
on deep red) and ``scene_off.png`` (gray figure on the house neutral).

Usage::

    python homeassistant/tools/icons/build_scene_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

ICONS = Path(__file__).resolve().parents[2] / "streamdock_n3" / "services" / "scenes" / "icons"
SIZE, PAD = 112, 10
SETS = {"scene.png": ("#ffffff", "#8f1d2c"), "scene_off.png": ("#5e5e5e", "#262626")}


def figure_mask(source: Path) -> Image.Image:
    """The figure as an 8-bit mask (255 = figure), cropped and scaled to fit the padded tile."""
    gray = Image.open(source).convert("L")
    mask = ImageOps.autocontrast(ImageOps.invert(gray))
    mask = mask.crop(mask.point(lambda v: 255 if v > 24 else 0).getbbox())
    box = SIZE - 2 * PAD
    scale = min(box / mask.width, box / mask.height)
    return mask.resize((max(1, round(mask.width * scale)), max(1, round(mask.height * scale))), Image.LANCZOS)


def main() -> None:
    mask = figure_mask(ICONS / "source.gif")
    at = ((SIZE - mask.width) // 2, (SIZE - mask.height) // 2)
    for name, (fg, bg) in SETS.items():
        tile = Image.new("RGB", (SIZE, SIZE), bg)
        tile.paste(Image.new("RGB", mask.size, fg), at, mask)
        tile.save(ICONS / name)
        print(f"wrote {ICONS / name}")


if __name__ == "__main__":
    main()
