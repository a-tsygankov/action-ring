"""A key icon with its label on a band across the bottom (e.g. the live-soccer
key: "12 LIVE", "2/12"), so one key shows a picture and a changing value.

The band covers the lower BAND share of the icon in the key's color (black
by default); the text is white, as large as fits on one line.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BAND = 0.3  # share of the icon height under the band
MARGIN = 0.06  # side margin of the text, as a share of the width


def draw(icon: str, label: str, color: tuple[int, int, int] | None, out: Path) -> None:
    """Write `icon` with `label` on its bottom band to `out` (JPEG)."""
    image = Image.open(icon).convert("RGB")
    w, h = image.size
    top = round(h * (1 - BAND))
    canvas = ImageDraw.Draw(image)
    canvas.rectangle((0, top, w, h), fill=color or (0, 0, 0))
    text = label.replace("\\n", " ")
    room_w, room_h = w * (1 - 2 * MARGIN), (h - top) * 0.8
    size = int(room_h)
    while True:
        font = ImageFont.load_default(size=size)
        box = canvas.textbbox((0, 0), text, font=font)
        if size <= 8 or (box[2] - box[0] <= room_w and box[3] - box[1] <= room_h):
            break
        size -= 1
    x = (w - (box[2] - box[0])) / 2 - box[0]
    y = top + (h - top - (box[3] - box[1])) / 2 - box[1]
    canvas.text((x, y), text, fill=(255, 255, 255), font=font)
    image.save(out, "JPEG", quality=95)
