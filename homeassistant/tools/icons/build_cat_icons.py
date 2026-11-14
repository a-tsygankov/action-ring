#!/usr/bin/env python3
"""Draw the cat easter egg's key images (services/cats/icons/).

Six original cat faces, drawn here with Pillow (no source images), each in
three frames for the key's animation: ``cat_<n>.png`` (eyes open),
``cat_<n>_blink.png`` (eyes shut) and ``cat_<n>_tongue.png`` (tongue out).
Drawn at 4x and scaled down to 112x112 (LANCZOS) for smooth edges. The
images are original work, dedicated to the public domain (CC0 1.0).

Usage::

    python homeassistant/tools/icons/build_cat_icons.py
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

ICONS = Path(__file__).resolve().parents[2] / "streamdock_n3" / "services" / "cats" / "icons"
SIZE = 112
S = 4  # supersampling
W = SIZE * S

INNER_EAR = "#f4a6b8"
NOSE = "#e8798f"
TONGUE = "#f06b8a"


@dataclass(frozen=True)
class Cat:
    bg: str
    fur: str
    line: str  # mouth, whisker and outline colour
    eyes: tuple[str, str]  # left, right iris
    stripes: str | None = None  # forehead tabby stripes
    patches: tuple[tuple[str, tuple[int, int, int, int]], ...] = ()  # (colour, ellipse box in 112 px)
    mask: str | None = None  # a darker muzzle/face mask (Siamese)
    ear_fill: str | None = None  # outer ear colour, if not the fur


CATS = {
    1: Cat("#2f6f8f", "#e8963a", "#5a2e0c", ("#7cc243", "#7cc243"), stripes="#b5651d"),
    2: Cat("#7b5ea7", "#222226", "#cfcfcf", ("#f2c230", "#f2c230")),
    3: Cat("#d98b3a", "#8f97a3", "#3a3f47", ("#5fb3e8", "#5fb3e8"), stripes="#6c7480"),
    4: Cat("#3d8b5a", "#f4f1ea", "#6b6b6b", ("#5fa8e8", "#e3a32b")),
    5: Cat("#b8434e", "#faf6ef", "#5c4a3a", ("#6dbb4a", "#6dbb4a"),
           patches=(("#e48a2f", (4, 18, 52, 66)), ("#2b2b2b", (66, 14, 112, 58)))),
    6: Cat("#2e4f86", "#efe3cc", "#3b2a20", ("#4f9fe8", "#4f9fe8"), mask="#6b4a35", ear_fill="#5c3e2c"),
}


def sc(*v: float) -> list[float]:
    """112-px coordinates scaled to the drawing."""
    return [x * S for x in v]


def draw_cat(cat: Cat, frame: str) -> Image.Image:
    img = Image.new("RGB", (W, W), cat.bg)
    d = ImageDraw.Draw(img)
    outline = cat.line
    ear = cat.ear_fill or cat.fur
    # Ears: outer triangles, inner pink.
    for (a, b, c), (ia, ib, ic) in (
        (((6, 52), (14, 2), (46, 26)), ((15, 42), (18, 12), (38, 27))),
        (((106, 52), (98, 2), (66, 26)), ((97, 42), (94, 12), (74, 27))),
    ):
        d.polygon(sc(*a, *b, *c), fill=ear, outline=outline, width=S)
        d.polygon(sc(*ia, *ib, *ic), fill=INNER_EAR)
    # Head: a wide ellipse running off the bottom (a close-up).
    head = sc(-4, 18, 116, 136)
    d.ellipse(head, fill=cat.fur, outline=outline, width=S)
    # Markings, clipped to the head.
    if cat.patches or cat.mask:
        layer = Image.new("RGB", (W, W), cat.fur)
        ld = ImageDraw.Draw(layer)
        for colour, box in cat.patches:
            ld.ellipse(sc(*box), fill=colour)
        if cat.mask:
            ld.ellipse(sc(26, 50, 86, 112), fill=cat.mask)
        clip = Image.new("L", (W, W), 0)
        ImageDraw.Draw(clip).ellipse(sc(-3, 19, 115, 135), fill=255)
        img.paste(layer, (0, 0), clip)
        d = ImageDraw.Draw(img)
    if cat.stripes:
        for x0, x1 in ((48, 52), (54, 58), (60, 64)):
            d.polygon(sc(x0, 22, x1, 22, (x0 + x1) / 2, 38), fill=cat.stripes)
    # Eyes.
    for cx, iris in ((36, cat.eyes[0]), (76, cat.eyes[1])):
        cy = 60
        if frame == "blink":
            d.arc(sc(cx - 13, cy - 10, cx + 13, cy + 8), 20, 160, fill=outline, width=3 * S)
            continue
        d.ellipse(sc(cx - 13, cy - 12, cx + 13, cy + 12), fill=iris, outline=outline, width=S)
        d.ellipse(sc(cx - 3.5, cy - 10, cx + 3.5, cy + 10), fill="#111111")  # slit pupil
        d.ellipse(sc(cx + 2, cy - 8, cx + 7, cy - 3), fill="#ffffff")  # catch light
    # Nose and mouth.
    d.polygon(sc(49, 76, 63, 76, 56, 84), fill=NOSE, outline=outline, width=S // 2)
    d.line(sc(56, 84, 56, 89), fill=outline, width=2 * S)
    d.arc(sc(44, 82, 56, 94), 20, 160, fill=outline, width=2 * S)
    d.arc(sc(56, 82, 68, 94), 20, 160, fill=outline, width=2 * S)
    if frame == "tongue":  # over the mouth line: the tongue hangs out of it
        d.rounded_rectangle(sc(49, 91, 63, 109), radius=7 * S, fill=TONGUE, outline="#b8435f", width=S)
        d.line(sc(56, 94, 56, 104), fill="#b8435f", width=S)
    # Whiskers.
    for side in (1, -1):
        x0 = 56 + side * 16
        for dy, tilt in ((-2, -6), (3, 0), (8, 6)):
            d.line(sc(x0, 84 + dy, x0 + side * 34, 84 + dy + tilt), fill=outline, width=S)
    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    for n, cat in CATS.items():
        for frame, suffix in (("open", ""), ("blink", "_blink"), ("tongue", "_tongue")):
            out = ICONS / f"cat_{n}{suffix}.png"
            draw_cat(cat, frame).save(out, optimize=True)
            print(f"wrote {out}")


if __name__ == "__main__":
    main()
