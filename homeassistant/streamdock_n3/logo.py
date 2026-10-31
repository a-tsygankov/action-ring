"""Service logo flashed on one key of a context page.

A "tabs.logo" config section makes a context page show the logo of what is
on screen on one key (6 by default) for `show_s` seconds, every `every_s`
seconds (a [min, max] range picks a random gap each time). Only the key's
image changes; a press still does what the key does on that page.

    "logo": {"key": 6, "show_s": 2, "every_s": [10, 15],
             "default": "apple_tv_app",
             "sources": {"PS5 Game Console": "ps5"},
             "apps": {"youtube.leanback.v4": "youtube"}}

Which logo: on the idle page the LG input's logo from `sources` (a game
console), else `default`; on an app page the app's icon. App icons come from
the launch keys on the pages ({"launch": {..., "app": id}} with an "icon"),
plus `apps`. No logo is known: the key keeps its own look.

Pure: time and the state lookup are passed in.
"""

from __future__ import annotations

import random
from typing import Any, Callable

from keyspec import StateLookup

APP_LAYOUTS = ("streaming", "live_game", "youtube", "lg_media", "lg_app")


def app_icons(pages: dict[str, dict], extra: dict[str, str] | None = None) -> dict[str, str]:
    """App id -> icon id from the pages' launch keys, then `extra`."""
    out: dict[str, str] = {}
    for page in pages.values():
        for spec in (page.get("keys") or {}).values():
            action = spec.get("action") if isinstance(spec, dict) else None
            launch = action.get("launch") if isinstance(action, dict) else None
            if isinstance(launch, dict) and launch.get("app") and spec.get("icon"):
                out.setdefault(launch["app"], spec["icon"])
    out.update(extra or {})
    return out


def logo_for(layout: str, state_of: StateLookup, ctx: dict, cfg: dict,
             icons: dict[str, str], atv_app: str | None) -> str | None:
    """Icon id to flash on a context page with this layout, or None."""
    lg = (state_of(ctx.get("lg") or "") or {}).get("a") or {}
    if layout == "idle":
        return (cfg.get("sources") or {}).get(lg.get("source")) or cfg.get("default")
    if layout not in APP_LAYOUTS:
        return None
    on_atv = lg.get("source") is not None and lg.get("source") == ctx.get("atv_source")
    app = atv_app if on_atv else lg.get("app_id")
    return icons.get(app) if app else None


class LogoClock:
    """When the logo shows: `show_s` seconds, then a gap of `every_s` (a number
    or a [min, max] range) from one start to the next."""

    def __init__(self, cfg: dict[str, Any], rand: Callable[[float, float], float] = random.uniform):
        every = cfg.get("every_s", [10, 15])
        self.low, self.high = (float(every[0]), float(every[1])) if isinstance(every, list) \
            else (float(every), float(every))
        self.show_s = float(cfg.get("show_s", 2))
        self._rand = rand
        self._start: float | None = None

    def showing(self, now: float) -> bool:
        if self._start is None:
            self._start = now + self._rand(self.low, self.high)
        elif now >= self._start + self.show_s:
            self._start += self._rand(self.low, self.high)
            if now >= self._start + self.show_s:  # ticks were missed: start over
                self._start = now + self._rand(self.low, self.high)
        return self._start <= now
