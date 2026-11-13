"""Dynamic keys: one key that takes turns between functions on a timer.

A key spec with a "dynamic" section cycles through its phases (e.g. 5 s
thumbs up, 5 s thumbs down) while its page is on screen. Each phase is laid
over the key's own spec, so a phase may set any key field (icon, label,
color, action, modes, state_entity):

    "4": {"dynamic": {
            "hold_s": 5, "warn_s": 1, "warn": "countdown", "grace_s": 0.25,
            "phases": [
              {"icon": "thumb_up",   "action": ...},
              {"icon": "thumb_down", "action": ..., "hold_s": 3}]}}

For the last `warn_s` seconds of a phase the key shows a warning that it is
about to change: "countdown" (3-2-1 in the top-right corner), "bar" (a bar
along the bottom that shrinks to nothing) or "dots" (three dots along the top
that go out one by one), in `warn_color`.

The clock starts when the page is shown, so a page always opens on its first
phase. Several dynamic keys on a page each keep their own phases. A press
does what the key showed when it was pressed: the phase last drawn, or the
one before it if that was swapped in less than `grace_s` ago (the finger was
already on its way).

Timing is pure (time is passed in); draw_warning() renders the overlay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOLD_S = 5.0
WARN_S = 1.0
GRACE_S = 0.25
WARN_STYLE = "countdown"
WARN_COLOR = "#ffb300"
STEPS = {"countdown": 3, "bar": 5, "dots": 3}  # frames per warning
MIN_HOLD_S = 0.2  # a shorter (or missing/garbled) hold is raised to this


@dataclass(frozen=True)
class Warn:
    """The warning overlay on a key: frame `step` of `steps` (counts down to 1)."""

    style: str
    step: int
    steps: int
    color: str


@dataclass(frozen=True)
class Frame:
    index: int  # phase on now
    remaining: float  # seconds left in it
    warn: Warn | None  # the overlay to show, if warning
    next_in: float  # seconds until the key looks different (frame or phase)


@dataclass(frozen=True)
class OnScreen:
    """The phase a key showed: drawn at `since`, for the page shown at `shown_at`."""

    shown_at: float
    index: int
    since: float
    prev: int | None  # phase drawn before it on the same page


def config(spec: dict) -> dict | None:
    """The key's "dynamic" section if it has two phases or more, else None."""
    dyn = spec.get("dynamic") if isinstance(spec, dict) else None
    if not isinstance(dyn, dict):
        return None
    phases = dyn.get("phases")
    if not isinstance(phases, list) or len([p for p in phases if isinstance(p, dict)]) < 2:
        return None
    return dyn


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _phases(dyn: dict) -> list[dict]:
    return [p for p in dyn["phases"] if isinstance(p, dict)]


def _holds(dyn: dict) -> list[float]:
    base = _num(dyn.get("hold_s"), HOLD_S)
    return [max(MIN_HOLD_S, _num(p.get("hold_s"), base)) for p in _phases(dyn)]


def phase_spec(spec: dict, index: int) -> dict:
    """The key's spec with phase `index` laid over it (no "dynamic", no "hold_s")."""
    dyn = config(spec)
    base = {k: v for k, v in spec.items() if k != "dynamic"}
    if dyn is None:
        return base
    phases = _phases(dyn)
    phase = {k: v for k, v in phases[index % len(phases)].items() if k != "hold_s"}
    return {**base, **phase}


def phase_specs(spec: dict) -> list[dict]:
    """Every phase's spec (phase_spec); a key that is not dynamic: itself."""
    dyn = config(spec)
    return [spec] if dyn is None else [phase_spec(spec, i) for i in range(len(_phases(dyn)))]


def frame(spec: dict, elapsed: float) -> Frame:
    """Where a dynamic key is, `elapsed` seconds after its page was shown."""
    dyn = config(spec)
    if dyn is None:
        return Frame(0, math.inf, None, math.inf)
    holds = _holds(dyn)
    t = max(0.0, elapsed) % sum(holds)
    index = 0
    while index < len(holds) - 1 and t >= holds[index]:
        t -= holds[index]
        index += 1
    remaining = holds[index] - t
    style = dyn.get("warn") if dyn.get("warn") in STEPS else WARN_STYLE
    warn_s = min(max(0.0, _num(dyn.get("warn_s"), WARN_S)), holds[index])
    if warn_s <= 0 or remaining > warn_s:
        return Frame(index, remaining, None, remaining - warn_s if warn_s > 0 else remaining)
    steps = STEPS[style]
    slot = warn_s / steps
    step = min(steps, max(1, math.ceil(remaining / slot - 1e-9)))
    warn = Warn(style, step, steps, str(dyn.get("warn_color") or WARN_COLOR))
    return Frame(index, remaining, warn, remaining - (step - 1) * slot)


def pressed_index(spec: dict, shown_at: float, now: float, drawn: OnScreen | None) -> int:
    """The phase a press at `now` means: what the key showed (see module doc)."""
    dyn = config(spec)
    if dyn is None:
        return 0
    if drawn is None or drawn.shown_at != shown_at:
        return frame(spec, now - shown_at).index  # not drawn on this page yet
    grace = max(0.0, _num(dyn.get("grace_s"), GRACE_S))
    if drawn.prev is not None and now - drawn.since < grace:
        return drawn.prev
    return drawn.index


def on_screen(prev: OnScreen | None, shown_at: float, index: int, now: float) -> OnScreen:
    """The record after a pass drew phase `index` at `now`."""
    if prev is not None and prev.shown_at == shown_at:
        return prev if prev.index == index else OnScreen(shown_at, index, now, prev.index)
    return OnScreen(shown_at, index, now, None)


# -- drawing -------------------------------------------------------------------


def _rgb(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    try:
        if len(text) == 6:
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        pass
    return _rgb(WARN_COLOR)


def draw_warning(image: str, warn: Warn, out: Path, bottom: float = 1.0) -> None:
    """Write `image` with the warning overlay to `out` (JPEG). The bar's lower
    edge sits at `bottom` (a share of the height: above a label band)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(image).convert("RGB")
    w, h = img.size
    canvas = ImageDraw.Draw(img)
    color = _rgb(warn.color)
    if warn.style == "bar":
        bar_h = max(4, round(h * 0.09))
        y1 = round(h * bottom)
        canvas.rectangle((0, y1 - bar_h, w, y1 - 1), fill=(0, 0, 0))
        width = round(w * warn.step / warn.steps)
        canvas.rectangle((0, y1 - bar_h, width - 1, y1 - 1), fill=color)
    elif warn.style == "dots":
        r = max(3, round(w * 0.065))
        gap = r * 3
        x0 = w / 2 - gap * (warn.steps - 1) / 2
        y = round(h * 0.03) + r + 3
        pad = r + 3  # a dark pill behind the dots, so they read on any icon
        canvas.rounded_rectangle((x0 - pad, y - pad, x0 + gap * (warn.steps - 1) + pad, y + pad),
                                 radius=pad, fill=(0, 0, 0))
        for i in range(warn.steps):
            x = x0 + gap * i
            box = (x - r, y - r, x + r, y + r)
            if i < warn.step:
                canvas.ellipse(box, fill=color, outline=(0, 0, 0), width=max(1, r // 3))
            else:  # gone out: a dim ring, so the count of what is left reads
                canvas.ellipse(box, outline=(90, 90, 90), width=max(1, r // 3))
    else:  # countdown
        d = round(min(w, h) * 0.36)
        x1, y0 = w - 2, 2
        canvas.ellipse((x1 - d, y0, x1, y0 + d), fill=(0, 0, 0), outline=color, width=max(2, d // 12))
        text = str(warn.step)
        size = int(d * 0.75)
        while True:
            font = ImageFont.load_default(size=size)
            box = canvas.textbbox((0, 0), text, font=font)
            if size <= 8 or (box[2] - box[0] <= d * 0.6 and box[3] - box[1] <= d * 0.6):
                break
            size -= 1
        cx, cy = x1 - d / 2, y0 + d / 2
        canvas.text((cx - (box[2] - box[0]) / 2 - box[0], cy - (box[3] - box[1]) / 2 - box[1]),
                    text, fill=color, font=font)
    img.save(out, "JPEG", quality=95)
