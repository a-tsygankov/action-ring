"""badge: an icon with its label on a bottom band (the live-soccer key)."""

from pathlib import Path

from PIL import Image

import badge

ICON = Path(__file__).resolve().parent.parent / "icons" / "soccer_live__live.png"


def test_the_label_goes_on_a_band_and_the_picture_stays(tmp_path):
    out = tmp_path / "key.jpg"
    badge.draw(str(ICON), "2/12", None, out)
    src, img = Image.open(ICON).convert("RGB"), Image.open(out).convert("RGB")
    assert img.size == src.size
    w, h = img.size
    band = [img.getpixel((x, y)) for x in range(w) for y in range(int(h * 0.72), h)]
    assert any(min(p) > 200 for p in band)  # white text on the band
    assert sum(max(p) < 40 for p in band) > len(band) / 2  # mostly the black band
    assert _close(img, src, h)  # the ball above the band is untouched


def _close(img, src, h):
    w = img.size[0]
    diffs = [abs(a - b) for a, b in zip(img.crop((0, 0, w, int(h * 0.6))).tobytes(),
                                        src.crop((0, 0, w, int(h * 0.6))).tobytes())]
    return sum(diffs) / len(diffs) < 4  # JPEG noise only


def test_a_long_label_shrinks_to_fit(tmp_path):
    out = tmp_path / "key.jpg"
    badge.draw(str(ICON), "18 LIVE GAMES NOW", (40, 40, 40), out)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    edges = [img.getpixel((x, y)) for y in range(int(h * 0.72), h) for x in (0, 1, w - 2, w - 1)]
    assert all(min(p) < 200 for p in edges)  # the text stays inside the margins
