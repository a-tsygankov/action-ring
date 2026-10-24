#!/usr/bin/env python3
"""Build the StreamDock N3 (ActionRing N3) key icon set.

Downloads source SVGs from the Iconify API into ``out/cache/`` (once), composes
every key at 112x112 PNG into ``out/``, writes ``manifest.json`` next to this
script, and renders two contact sheets:

* ``out/contact_112.png``    - every icon at 112 px with its file name,
* ``out/contact_device.png`` - every icon as the device shows it: the SDK's
  ``to_native_key_format`` (rotate -90, resize to 64x64) and ``Image.save`` to a
  ``.jpg`` (Pillow defaults: quality 75, 4:2:0), rotated back upright and
  enlarged x2 with nearest-neighbour.

Requirements: Python 3.10+, Pillow, ``resvg_py`` (``pip install resvg_py``;
Rust/PyO3 abi3 wheels for win_amd64, macOS, manylinux x86_64 and
manylinux2014 aarch64, so it runs in the python:3.12-slim-bookworm app image
without apt packages). Labels need a bold sans TTF: Arial Bold on Windows,
Liberation Sans Bold (``fonts-liberation2``, metric-compatible with Arial) or
DejaVu Sans Bold on Linux, or pass ``--font``.

Usage::

    python build_icons.py                 # build everything
    python build_icons.py --only netflix  # ids starting with "netflix"
    python build_icons.py --offline       # cache only, no network
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import resvg_py
except ImportError:  # pragma: no cover
    sys.exit("resvg_py is required: pip install resvg_py")

HERE = Path(__file__).resolve().parent
REPO_ICONS = HERE.parent.parent / "streamdock_n3" / "icons"

SIZE = 112          # config icon size (px)
SS = 4              # supersampling factor for composition
W = SIZE * SS

# ---------------------------------------------------------------- palette --
NEUTRAL = "#262626"     # house background for neutral keys
WHITE = "#ffffff"
DIM_FG = "#8c8c8c"      # glyph colour for OFF states
DIMMER_FG = "#5e5e5e"   # glyph colour for "nothing to do" states
DIM_BG = "#1c1c1c"
NIGHT_ON = "#3b5bdb"
SPEECH_ON = "#2b8a3e"
TV_ON = "#a3302c"
MUTED = "#c77c02"
UNKNOWN = "#5c3b00"
SUBS_ON = "#0c8599"     # subtitles on (teal; distinct from Night/Speech)
SPEED_FG = "#ffd43b"    # speed multiplier text on FF/RW keys
YT_RED = "#e62117"      # existing next_chapter.png background
LIVE_RED = "#e03131"

# Iconify collection metadata (https://api.iconify.design/collections, 2026-10-24).
LICENSES = {
    "material-symbols": ("Material Symbols", "Google", "Apache-2.0"),
    "mdi": ("Material Design Icons", "Pictogrammers", "Apache-2.0"),
    "simple-icons": ("Simple Icons 16.35.0", "Simple Icons Collaborators", "CC0-1.0"),
    "logos": ("SVG Logos", "Gil Barbara", "CC0-1.0"),
    "selfhst": ("selfh.st/icons", "selfh.st", "CC-BY-4.0"),
    "thesvg-color": ("theSVG Color", "thesvg.org", "MIT"),
    "repo": ("action-ring repo asset", "action-ring project", "project asset"),
}
FORBIDDEN_PREFIXES = {"cbi"}  # CC BY-NC-SA: never use

ICONIFY = "https://api.iconify.design"
UA = "action-ring-icon-builder/1.0 (+https://github.com/a-tsygankov)"


# ------------------------------------------------------------------ fetch --
class Iconify:
    def __init__(self, cache: Path, offline: bool = False):
        self.cache = cache
        self.offline = offline

    def svg(self, icon_id: str) -> str:
        prefix, name = icon_id.split(":", 1)
        if prefix in FORBIDDEN_PREFIXES or prefix not in LICENSES:
            raise ValueError(f"icon set not allowed / not listed: {icon_id}")
        path = self.cache / prefix / f"{name}.svg"
        if path.exists():
            return path.read_text(encoding="utf-8")
        if self.offline:
            raise FileNotFoundError(f"{icon_id} not cached and --offline given")
        url = f"{ICONIFY}/{prefix}/{name}.svg"
        for attempt in range(8):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 7:
                    time.sleep(2 ** attempt)
                    continue
                raise
        if not data.lstrip().startswith("<svg"):
            raise ValueError(f"{icon_id}: not an SVG response")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        time.sleep(0.3)  # be polite to the public API
        return data


def _viewbox(svg: str) -> tuple[float, float]:
    m = re.search(r'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)', svg)
    if not m:
        return 1.0, 1.0
    return float(m.group(3)), float(m.group(4))


def rasterize(svg: str, box_w: int, box_h: int) -> Image.Image:
    """Render an SVG to RGBA so it fits inside box_w x box_h (aspect kept)."""
    vw, vh = _viewbox(svg)
    if vw / vh >= box_w / box_h:
        png = resvg_py.svg_to_bytes(svg_string=svg, width=int(box_w))
    else:
        png = resvg_py.svg_to_bytes(svg_string=svg, height=int(box_h))
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def recolor(im: Image.Image, color: str) -> Image.Image:
    solid = Image.new("RGBA", im.size, color)
    solid.putalpha(im.getchannel("A"))
    return solid


def trim(im: Image.Image) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    return im.crop(bbox) if bbox else im


# ------------------------------------------------------------------ fonts --
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]
_FONT_PATH: str | None = None


def font(px: float) -> ImageFont.FreeTypeFont:
    if _FONT_PATH:
        return ImageFont.truetype(_FONT_PATH, int(round(px)))
    return ImageFont.load_default(size=int(round(px)))


# ---------------------------------------------------------------- drawing --
def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


class Key:
    """A 112x112 key composed on a supersampled canvas (coords in 112-space)."""

    def __init__(self, ico: Iconify, bg: str = NEUTRAL, gradient: str | None = None):
        self.ico = ico
        self.sources: list[str] = []
        self.colors: dict[str, str] = {"background": bg if not gradient else f"{bg}->{gradient}"}
        if gradient:
            top, bot = _rgb(bg), _rgb(gradient)
            grad = Image.new("RGBA", (1, W))
            for y in range(W):
                t = y / (W - 1)
                grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
            self.im = grad.resize((W, W))
        else:
            self.im = Image.new("RGBA", (W, W), bg)
        self.draw = ImageDraw.Draw(self.im)

    # glyphs -----------------------------------------------------------------
    def glyph(self, icon_id: str, cx: float, cy: float, box: float, box_h: float | None = None,
              color: str | None = WHITE, flip: bool = False, crop: bool = True) -> tuple[int, int, int, int]:
        """Paste an Iconify icon centred on (cx, cy), fitted in box x box_h.

        color=None keeps the artwork's own colours (brand logos); otherwise the
        icon's alpha is filled with ``color``. Returns the pasted bbox (112-space).
        """
        self.sources.append(icon_id)
        bw, bh = box * SS, (box_h or box) * SS
        svg = self.ico.svg(icon_id)
        if color is not None:
            svg = svg.replace("currentColor", color)
        # Render big, trim the empty viewBox padding, then fit the box exactly.
        im = rasterize(svg, int(bw * 2), int(bh * 2))
        if crop:
            im = trim(im)
        s = min(bw / im.width, bh / im.height)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        if color is not None:
            im = recolor(im, color)
        if flip:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        x, y = round(cx * SS - im.width / 2), round(cy * SS - im.height / 2)
        self.im.alpha_composite(im, (x, y))
        return (x / SS, y / SS, (x + im.width) / SS, (y + im.height) / SS)

    def full_bleed(self, icon_id: str, zoom: float = 1.0) -> None:
        """Render a square tile artwork over the whole key, centre-cropped by zoom."""
        self.sources.append(icon_id)
        side = round(W * zoom)
        im = rasterize(self.ico.svg(icon_id), side, side)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.alpha_composite(im, ((side - im.width) // 2, (side - im.height) // 2))
        off = (side - W) // 2
        self.im.alpha_composite(canvas.crop((off, off, off + W, off + W)))

    # text -------------------------------------------------------------------
    def text(self, s: str, cx: float, cy: float, px: float, color: str = WHITE,
             max_w: float = 100, tracking: float = 0.0) -> None:
        """Draw text centred on (cx, cy) by its ink box; shrinks to fit max_w."""
        size = px * SS
        while True:
            f = font(size)
            widths = [self.draw.textlength(ch, font=f) for ch in s]
            total = sum(widths) + tracking * SS * (len(s) - 1)
            if total <= max_w * SS or size <= 8:
                break
            size -= SS / 2
        # ink box of the whole string for vertical centring
        l, t, r, b = self.draw.textbbox((0, 0), s, font=f, anchor="ls")
        x = cx * SS - total / 2
        y = cy * SS - (t + b) / 2
        for ch, w in zip(s, widths):
            self.draw.text((x, y), ch, font=f, fill=color, anchor="ls")
            x += w + tracking * SS
        self.colors.setdefault("text", color)

    # shapes -----------------------------------------------------------------
    def circle(self, cx: float, cy: float, r: float, fill: str, outline: str | None = None,
               width: float = 0) -> None:
        self.draw.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS],
                          fill=fill, outline=outline, width=round(width * SS))

    def rrect(self, x0: float, y0: float, x1: float, y1: float, radius: float, fill: str | None,
              outline: str | None = None, width: float = 0) -> None:
        self.draw.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=radius * SS,
                                    fill=fill, outline=outline, width=round(width * SS))

    def badge(self, icon_id: str | None, cx: float, cy: float, r: float, fill: str, fg: str,
              ring: str | None = None, text: str | None = None, glyph_scale: float = 1.25) -> None:
        if ring:
            self.circle(cx, cy, r + 3, ring)
        self.circle(cx, cy, r, fill)
        if icon_id:
            self.glyph(icon_id, cx, cy, r * glyph_scale, color=fg)
        if text:
            self.text(text, cx, cy, r * 1.3, fg, max_w=r * 1.7)

    def result(self) -> Image.Image:
        return self.im.resize((SIZE, SIZE), Image.LANCZOS).convert("RGB")


# ----------------------------------------------------------------- layout --
GLYPH_CY, GLYPH_BOX = 44, 54     # glyph above a label
LABEL_CY, LABEL_PX = 89, 23      # label baseline area


def labeled(ico, bg, glyph_id, label, fg=WHITE, glyph_fg=None, box=GLYPH_BOX):
    k = Key(ico, bg)
    k.glyph(glyph_id, 56, GLYPH_CY, box, color=glyph_fg or fg)
    k.text(label, 56, LABEL_CY, LABEL_PX, fg, max_w=102)
    k.colors["glyph"] = glyph_fg or fg
    return k


def centered(ico, bg, glyph_id, fg=WHITE, box=72, cy=56, flip=False):
    k = Key(ico, bg)
    k.glyph(glyph_id, 56, cy, box, color=fg, flip=flip)
    k.colors["glyph"] = fg
    return k


# ----------------------------------------------------------------- icons --
ICONS: list[tuple[str, str | None, callable, str]] = []  # (id, state, builder, notes)


def icon(id_: str, state: str | None = None, notes: str = ""):
    def deco(fn):
        ICONS.append((id_, state, fn, notes))
        return fn
    return deco


# ---- 1. Apps (brand tiles) ----------------------------------------------------
@icon("netflix", notes="Netflix app tile: red N ribbon on black.")
def _netflix(ico):
    k = Key(ico, "#000000")
    k.full_bleed("logos:netflix-icon", zoom=1.16)  # artwork is a black square with the N
    return k


@icon("youtube", notes="YouTube app tile: red play button on white.")
def _youtube(ico):
    k = Key(ico, "#ffffff")
    k.glyph("logos:youtube-icon", 56, 56, 88, color=None)
    return k


@icon("prime_video", notes="Prime Video app tile: white wordmark + smile on Prime Video blue. "
      "Simple Icons removed Prime Video, so the CC BY 4.0 selfh.st mark is used.")
def _prime(ico):
    k = Key(ico, "#1a98ff", "#0f79d9")
    k.glyph("selfhst:amazon-prime-video-light", 56, 57, 84, 66, color=WHITE)
    return k


@icon("apple_tv_app", notes="Apple TV app tile: white Apple TV mark on black.")
def _appletv(ico):
    k = Key(ico, "#000000")
    k.glyph("simple-icons:appletv", 56, 56, 88, color=WHITE)
    return k


@icon("max", notes="HBO Max app tile (2025 name): stacked white 'HBO max' on Max blue gradient.")
def _max(ico):
    k = Key(ico, "#0b39f5", "#061a7a")
    k.glyph("selfhst:hbo-max-light", 56, 56, 84, 72, color=WHITE)
    return k


@icon("hulu", notes="Hulu app tile: Hulu green wordmark on near-black.")
def _hulu(ico):
    k = Key(ico, "#0b0c0f")
    k.glyph("selfhst:hulu", 56, 56, 92, 50, color="#1ce783")
    return k


@icon("disney_plus", notes="Disney+ app tile: white wordmark with arc on Disney+ navy gradient.")
def _disney(ico):
    k = Key(ico, "#1a3c9e", "#06123d")
    k.glyph("selfhst:disney-plus-light", 56, 54, 96, 76, color=None)
    return k


@icon("paramount_plus", notes="Paramount+ app tile: white mountain and star arc on Paramount+ blue.")
def _paramount(ico):
    k = Key(ico, "#0064ff", "#0047c2")
    k.glyph("simple-icons:paramountplus", 56, 56, 84, color=WHITE)
    return k


@icon("pluto_tv", notes="Pluto TV app tile: yellow disc with 'pluto tv' on black. Not in Simple "
      "Icons/SVG Logos/selfh.st; theSVG colour artwork (MIT as declared, provenance unclear).")
def _pluto(ico):
    k = Key(ico, "#000000")
    k.full_bleed("thesvg-color:plutotv", zoom=1.06)
    return k


@icon("tubi", notes="Tubi app tile (2024 brand): yellow wordmark on Tubi purple.")
def _tubi(ico):
    k = Key(ico, "#7408ff", "#5200c4")
    k.glyph("simple-icons:tubi", 56, 57, 90, 44, color="#ffff13")
    return k


@icon("lg_media_player", notes="LG webOS Media Player has no brand mark: generic folder-play "
      "on LG red gradient.")
def _lgmedia(ico):
    k = Key(ico, "#c4003e", "#6e0021")
    k.glyph("mdi:folder-play", 56, 56, 70, color=WHITE)
    return k


@icon("apple_tv_input", notes="LG input -> Apple TV box: Apple TV 4K box seen from above on grey, "
      "with an HDMI-input badge.")
def _atv_input(ico):
    k = Key(ico, "#4a4a4e", "#2c2c2e")
    # the box: black glossy rounded square with a faint rim
    k.rrect(14, 14, 98, 98, 20, "#0d0d0f", outline="#6e6e73", width=1.5)
    k.rrect(17, 17, 95, 95, 17, None, outline="#1f1f22", width=2)
    k.glyph("simple-icons:appletv", 56, 52, 58, color="#d1d1d6")
    k.badge("material-symbols:input-rounded", 88, 88, 17, "#ffffff", "#1c1c1e", ring="#2c2c2e",
            glyph_scale=1.2)
    return k


def _soccer(ico, live: bool):
    if live:
        k = Key(ico, "#1b6e34", "#0d3d1c")
        k.circle(56, 46, 31, WHITE)                      # solid ball: white disc +
        k.glyph("mdi:soccer", 56, 46, 64, color="#141414")  # dark panels/outline
        k.rrect(16, 82, 96, 104, 11, LIVE_RED)
        k.circle(30, 93, 5, WHITE)
        k.text("LIVE", 62, 93, 19, WHITE, max_w=50)
        k.colors.update(glyph="#141414 on #ffffff", pill=LIVE_RED)
    else:
        k = Key(ico, NEUTRAL)
        k.glyph("mdi:soccer", 56, 52, 66, color=DIMMER_FG)
        k.colors["glyph"] = DIMMER_FG
    return k


@icon("soccer_live", "live", notes="Live soccer available: ball + red LIVE pill on pitch green.")
def _soccer_live(ico):
    return _soccer(ico, True)


@icon("soccer_live", "none", notes="No live games: dim ball on neutral.")
def _soccer_none(ico):
    return _soccer(ico, False)


# ---- 2. Playback ---------------------------------------------------------------
@icon("play_pause", "playing", notes="Playing: shows the pause glyph (press to pause).")
def _pp_playing(ico):
    return centered(ico, NEUTRAL, "material-symbols:pause-rounded", box=66)


@icon("play_pause", "paused", notes="Paused: shows the play glyph (press to play).")
def _pp_paused(ico):
    return centered(ico, NEUTRAL, "material-symbols:play-arrow-rounded", box=70)


def _skip10(ico, fwd: bool):
    k = Key(ico, NEUTRAL)
    gid = "material-symbols:forward-media-rounded" if fwd else "material-symbols:replay-rounded"
    k.glyph(gid, 56, 54, 92, color=WHITE)
    k.text("10", 56, 62, 34, WHITE, max_w=46)
    k.colors["glyph"] = WHITE
    return k


@icon("skip_back_10", notes="Back 10 s: circular arrow with large 10.")
def _skip_back(ico):
    return _skip10(ico, False)


@icon("skip_fwd_10", notes="Forward 10 s: circular arrow with large 10.")
def _skip_fwd(ico):
    return _skip10(ico, True)


def _speed(ico, gid: str, mult: int | None):
    if mult is None:
        return centered(ico, NEUTRAL, gid, box=80)
    k = Key(ico, NEUTRAL)
    k.glyph(gid, 56, 32, 64, 38, color=WHITE)
    k.text(f"{mult}\u00d7", 56, 80, 44, SPEED_FG, max_w=90)
    k.colors.update(glyph=WHITE, text=SPEED_FG)
    return k


for _gid, _name in (("material-symbols:fast-forward-rounded", "fast_forward"),
                    ("material-symbols:fast-rewind-rounded", "rewind")):
    for _m in (None, 2, 4, 8):
        icon(_name, f"{_m or 1}x", notes=("Plain glyph (normal speed)." if _m is None else
             f"Active {_m}x: small glyph + large amber {_m}\u00d7."))(
            lambda ico, g=_gid, m=_m: _speed(ico, g, m))


@icon("next_video", notes="Next video / episode.")
def _next_video(ico):
    return centered(ico, NEUTRAL, "material-symbols:skip-next-rounded", box=74)


@icon("prev_video", notes="Previous video / episode.")
def _prev_video(ico):
    return centered(ico, NEUTRAL, "material-symbols:skip-previous-rounded", box=74)


@icon("next_chapter", notes="Refined repo next_chapter.png: YouTube red, skip-next glyph over "
      "three chapter dashes.")
def _next_chapter(ico):
    k = Key(ico, YT_RED)
    k.glyph("material-symbols:skip-next-rounded", 56, 44, 64, color=WHITE)
    for x in (14, 44, 74):
        k.rrect(x, 80, x + 24, 88, 3, WHITE)
    k.colors["glyph"] = WHITE
    return k


@icon("stop", notes="Stop.")
def _stop(ico):
    return centered(ico, NEUTRAL, "material-symbols:stop-rounded", box=62)


# ---- 3. Tracks -----------------------------------------------------------------
CYCLE = "material-symbols:autorenew-rounded"


@icon("subtitles", "on", notes="Subtitles on: white glyph on teal.")
def _subs_on(ico):
    return centered(ico, SUBS_ON, "material-symbols:subtitles-rounded", box=78)


@icon("subtitles", "off", notes="Subtitles off: struck-through grey glyph.")
def _subs_off(ico):
    return centered(ico, NEUTRAL, "material-symbols:subtitles-off-rounded", fg=DIM_FG, box=78)


@icon("subtitles", "next", notes="Cycle subtitle track: glyph + cycle badge.")
def _subs_next(ico):
    k = Key(ico, NEUTRAL)
    k.glyph("material-symbols:subtitles-rounded", 48, 46, 70, color=WHITE)
    k.badge(CYCLE, 84, 82, 19, WHITE, NEUTRAL, ring=NEUTRAL)
    k.colors.update(glyph=WHITE, badge=WHITE)
    return k


@icon("audio_track", "default", notes="Audio track (language) key: sound-wave glyph.")
def _audio_default(ico):
    return centered(ico, NEUTRAL, "material-symbols:graphic-eq-rounded", box=72)


@icon("audio_track", "next", notes="Cycle audio track: sound-wave glyph + cycle badge.")
def _audio_next(ico):
    k = Key(ico, NEUTRAL)
    k.glyph("material-symbols:graphic-eq-rounded", 46, 46, 64, color=WHITE)
    k.badge(CYCLE, 84, 82, 19, WHITE, NEUTRAL, ring=NEUTRAL)
    k.colors.update(glyph=WHITE, badge=WHITE)
    return k


# ---- 4. Info -------------------------------------------------------------------
@icon("insight", notes="Who's in this scene (tvOS InSight): person + info badge.")
def _insight(ico):
    k = Key(ico, NEUTRAL)
    k.glyph("material-symbols:person-rounded", 48, 50, 74, color=WHITE)
    k.badge(None, 82, 80, 20, "#4dabf7", WHITE, ring=NEUTRAL, text="i")
    k.colors.update(glyph=WHITE, badge="#4dabf7")
    return k


# ---- 5. Navigation -----------------------------------------------------------
def _repo_png(name: str):
    def build(ico):
        k = Key(ico, NEUTRAL)
        src = Image.open(REPO_ICONS / name).convert("RGBA").resize((W, W), Image.LANCZOS)
        k.im.alpha_composite(src)
        k.sources.append(f"repo:homeassistant/streamdock_n3/icons/{name}")
        k.colors["glyph"] = WHITE
        return k
    return build


icon("home", notes="Existing repo icon (house), copied unchanged.")(_repo_png("home.png"))
icon("back", notes="Existing repo icon (return arrow), copied unchanged.")(_repo_png("back.png"))


@icon("menu_more", notes="More apps / next page: app grid + chevron.")
def _more(ico):
    k = Key(ico, NEUTRAL)
    k.glyph("material-symbols:apps-rounded", 44, 56, 54, color=WHITE)
    k.glyph("material-symbols:chevron-right-rounded", 90, 56, 30, 48, color=WHITE)
    k.colors["glyph"] = WHITE
    return k


# ---- 6. TV / Sonos -------------------------------------------------------------
POWER = "material-symbols:power-settings-new-rounded"
MOON = "material-symbols:bedtime-rounded"
VOICE = "material-symbols:record-voice-over-rounded"


@icon("tv_power", "on", notes="TV on (matches config TV ON colour).")
def _tv_on(ico):
    return labeled(ico, TV_ON, POWER, "TV")


@icon("tv_power", "off", notes="TV off.")
def _tv_off(ico):
    return labeled(ico, NEUTRAL, POWER, "TV", fg=DIM_FG)


@icon("tv_power", "muted", notes="TV on but muted (config MUTED amber).")
def _tv_muted(ico):
    return labeled(ico, MUTED, "material-symbols:volume-off-rounded", "MUTED")


def _toggle(ico, glyph, word, on_color, state):
    if state == "on":
        return labeled(ico, on_color, glyph, word)
    if state == "off":
        return labeled(ico, NEUTRAL, glyph, word, fg=DIM_FG)
    k = labeled(ico, UNKNOWN, glyph, "?", fg="#ffd8a8")
    return k


for _st in ("on", "off", "unknown"):
    icon("night", _st, notes={"on": "Sonos Night Sound on (config Night ON blue).",
                              "off": "Night Sound off.",
                              "unknown": "Sonos state unknown/unavailable: amber-brown with ?."}[_st])(
        lambda ico, s=_st: _toggle(ico, MOON, "NIGHT", NIGHT_ON, s))
for _st in ("on", "off", "unknown"):
    icon("speech", _st, notes={"on": "Sonos Speech Enhancement on (config Speech ON green).",
                               "off": "Speech Enhancement off.",
                               "unknown": "Sonos state unknown/unavailable: amber-brown with ?."}[_st])(
        lambda ico, s=_st: _toggle(ico, VOICE, "SPEECH", SPEECH_ON, s))


def _next_track(ico, on: bool):
    fg = WHITE if on else DIMMER_FG
    k = Key(ico, NEUTRAL if on else DIM_BG)
    k.glyph("material-symbols:skip-next-rounded", 50, 50, 60, color=fg)
    k.badge("material-symbols:music-note-rounded", 84, 82, 19, fg, NEUTRAL if on else DIM_BG,
            ring=NEUTRAL if on else DIM_BG, glyph_scale=1.3)
    k.colors["glyph"] = fg
    return k


@icon("next_track", "on", notes="Music playing somewhere (Sonos): next track.")
def _nt_on(ico):
    return _next_track(ico, True)


@icon("next_track", "off", notes="No music playing: dimmed.")
def _nt_off(ico):
    return _next_track(ico, False)


# ---- 7. Generic -----------------------------------------------------------------
@icon("unknown", "unavailable", notes="Placeholder / entity unavailable: dashed frame + ?.")
def _unavailable(ico):
    k = Key(ico, NEUTRAL)
    # dashed rounded frame
    for i in range(12):
        x = 14 + i * 7.5
        k.rrect(x, 12, x + 4, 15, 1, "#6b6b6b")
        k.rrect(x, 97, x + 4, 100, 1, "#6b6b6b")
        k.rrect(12, x, 15, x + 4, 1, "#6b6b6b")
        k.rrect(97, x, 100, x + 4, 1, "#6b6b6b")
    k.text("?", 56, 56, 60, DIM_FG, max_w=60)
    k.colors["text"] = DIM_FG
    return k


# ------------------------------------------------------------ simulation --
def device_view(png: Path, tmp: Path) -> Image.Image:
    """Reproduce StreamDockN3.set_key_image + PILHelper._to_native_format."""
    image = Image.open(png)
    fmt = {"size": (64, 64), "rotation": -90}
    expand = not (image.size[1] == fmt["size"][0] and image.size[0] == fmt["size"][1])
    fmt["size"] = (fmt["size"][1], fmt["size"][0])
    image = image.rotate(fmt["rotation"], expand=expand)
    if image.size != fmt["size"]:
        image = image.resize(fmt["size"])
    image = image.convert("RGB")
    image.save(tmp)                      # same call as the SDK: .jpg, Pillow defaults
    shown = Image.open(tmp).convert("RGB").rotate(90, expand=True)  # panel shows it upright
    return shown


def contact_sheet(items: list[tuple[str, Image.Image]], tile: int, path: Path, cols: int = 8,
                  title: str = "") -> None:
    pad, cap = 10, 30
    rows = (len(items) + cols - 1) // cols
    head = 34 if title else 0
    sheet = Image.new("RGB", (cols * (tile + pad) + pad, head + rows * (tile + cap + pad) + pad), "#111111")
    d = ImageDraw.Draw(sheet)
    f = font(12)
    if title:
        d.text((pad, 10), title, font=font(15), fill="#dddddd")
    for i, (name, im) in enumerate(items):
        x = pad + (i % cols) * (tile + pad)
        y = head + pad + (i // cols) * (tile + cap + pad)
        sheet.paste(im, (x, y))
        base, _, state = name.partition("__")
        d.text((x + tile / 2, y + tile + 3), base, font=f, fill="#e0e0e0", anchor="ma")
        if state:
            d.text((x + tile / 2, y + tile + 16), state, font=f, fill="#ffc078", anchor="ma")
    sheet.save(path)


# ------------------------------------------------------------------- main --
def main(argv=None) -> int:
    global _FONT_PATH
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--only", help="build only ids starting with this prefix")
    ap.add_argument("--offline", action="store_true", help="use cached SVGs only")
    ap.add_argument("--font", help="bold sans TTF for labels")
    ap.add_argument("--no-manifest", action="store_true")
    args = ap.parse_args(argv)

    _FONT_PATH = args.font or os.environ.get("ICON_FONT") or next(
        (p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if not _FONT_PATH:
        print("warning: no bold TTF found; using Pillow's default font", file=sys.stderr)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    ico = Iconify(out / "cache", args.offline)
    manifest: dict[str, dict] = {}
    big, small = [], []
    tmp = out / "cache" / "_device_tmp.jpg"
    tmp.parent.mkdir(parents=True, exist_ok=True)

    for id_, state, build, notes in ICONS:
        name = f"{id_}__{state}" if state else id_
        if args.only and not name.startswith(args.only):
            continue
        k = build(ico)
        img = k.result()
        path = out / f"{name}.png"
        img.save(path, optimize=True)
        big.append((name, img))
        small.append((name, device_view(path, tmp).resize((128, 128), Image.NEAREST)))
        srcs = []
        for s in dict.fromkeys(k.sources):
            prefix = s.split(":", 1)[0]
            set_name, author, lic = LICENSES[prefix]
            entry = {"id": s, "set": set_name, "author": author, "license": lic}
            if prefix != "repo":
                entry["url"] = f"{ICONIFY}/{s.replace(':', '/', 1)}.svg"
            srcs.append(entry)
        manifest.setdefault(id_, {})[state or "default"] = {
            "file": f"{name}.png", "sources": srcs, "colors": k.colors, "notes": notes}
        print(f"  {name}.png")
    tmp.unlink(missing_ok=True)

    if not args.only:
        contact_sheet(big, SIZE, out / "contact_112.png",
                      title="ActionRing N3 icons - 112x112 PNG as stored")
        contact_sheet(small, 128, out / "contact_device.png", cols=8,
                      title="Device simulation: rotate -90, resize 64x64, JPEG q75 4:2:0, shown x2 nearest")
        if not args.no_manifest:
            doc = {
                "_about": "Generated by build_icons.py. Key images are 112x112 RGB PNG; the N3 SDK "
                          "sends them as 64x64 JPEG rotated -90. Files are named <id>[__<state>].png.",
                "_palette": {k: v for k, v in globals().items()
                             if k.isupper() and isinstance(v, str) and v.startswith("#")},
                "_font": "Arial Bold (Windows) / Liberation Sans Bold (Linux) for labels",
                "icons": manifest,
            }
            (HERE / "manifest.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"{len(big)} icons -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
