"""The live soccer service: LiveSoccer (live.py) on the services contract.

Config (services.soccer): atv, atv_remote, lg, atv_source, power_on_wait,
token_page, cache_s, poll_s, idle_poll_s, select_after_s.

- job "refresh": polls the listing; the wait it returns is poll_interval /
  retry_interval (listing.py).
- actions "start" (the first live game, or the next while picking) and
  "next" (always the next, wrapping); each pick is toasted on the "soccer"
  channel and the TV switches once presses settle.
- conditions "live" (any game live) and "picking" (pressed within CYCLE_S,
  or a started game on screen); vars {soccer_count}, {soccer_pos}.
- keys "live" (the LIVE key) and "next" (the NEXT key on context:live_game).
- context: "streaming" becomes "live_game" while a started game's app is in
  the foreground.
"""

from __future__ import annotations

from typing import Any

from services import Service, action, condition, job

from .listing import CYCLE_S
from .live import LiveSoccer

PICK_GREEN = "#1d6b32"


class SoccerService(Service):
    name = "soccer"

    def __init__(self) -> None:
        self.ctx: Any = None
        self.live: LiveSoccer | None = None

    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        self.live = LiveSoccer(
            ctx.call, ctx.state, ctx.config, fetch=self._fetch, clock=ctx.clock, sleep=ctx.sleep,
            on_change=ctx.changed, monotonic=ctx.monotonic, notify=ctx.toast,
            # The pick's CYCLE_S window ends without an event: redraw just after it.
            on_pick=lambda: ctx.redraw_in(CYCLE_S + 0.1),
        )

    def _fetch(self, url: str) -> str:
        op = "token_page" if url == self.ctx.config["token_page"] else "shelf"
        return self.ctx.http.get(url, op=op)

    def entities(self) -> set[str]:
        cfg = self.ctx.config
        return {e for e in (cfg.get("lg"), cfg.get("atv")) if e}

    @job("refresh", first_in=0)
    def refresh(self) -> float:
        return self.live.poll_once()

    @action("start")
    def start_game(self, args: dict) -> None:
        self.live.start()

    @action("next")
    def next_game(self, args: dict) -> None:
        self.live.next()

    @condition("live")
    def is_live(self) -> bool:
        return bool(self.live.games())

    @condition("picking")
    def is_picking(self) -> bool:
        return self.live.picking()

    def vars(self) -> dict[str, str]:
        return self.live.vars()

    def key(self, name: str, args: dict) -> dict | None:
        if name == "live":
            return {
                "icon": "soccer_live__none",
                "label": "",
                "modes": [
                    {"when": "soccer.picking", "icon": "soccer_live__live", "label": "{soccer_pos}",
                     "color": PICK_GREEN},
                    {"when": "soccer.live", "icon": "soccer_live__live", "label": "{soccer_count} LIVE"},
                ],
                "action": {"service": "soccer", "do": "start"},
            }
        if name == "next":
            return {"label": "NEXT\\n{soccer_pos}", "color": PICK_GREEN,
                    "action": {"service": "soccer", "do": "next"}}
        return None

    def context(self, layout: str) -> str | None:
        # watching() runs on every context update, as before the move: it is
        # what forgets a game once another app takes over.
        watching = self.live.watching(self.ctx.state)
        return "live_game" if layout == "streaming" and watching else None
