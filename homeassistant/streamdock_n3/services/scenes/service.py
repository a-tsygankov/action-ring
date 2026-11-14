"""scenes: notices a film on the Apple TV and jumps past its listed scenes.

Config (services.scenes): atv, exclude_apps, min_film_s, settle_s, seek_apps,
lead_s, guard_s, seek_hold_s, plus the lookup settings read by lookup.py.

- job "watch" (every 5 s): a film that stays on for settle_s is looked up
  (Lookup.run, no lock held) and its scenes become the snapshot.
- condition "ready": a scene starts after the position + guard_s and the app
  can seek.
- key "next": gray icon, colour while ready; action "next" seeks to
  lead_s before the next scene.

Threads: watch runs on a worker, next on its own thread per press, ready/key
on render threads. The lock only guards reading or replacing two immutable
values: the snapshot (film_key, app_id, scenes) and the jump memory
(scene_start, at_monotonic). ctx.changed()/redraw_in() come from the job and
the action only.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from services import Service, action, condition, job

from .db import Db
from .lookup import Lookup
from .model import norm_title

WATCH_S = 5
_DEFAULTS = {
    "min_film_s": 3000, "settle_s": 10, "lead_s": 5, "guard_s": 2, "seek_hold_s": 5,
    "seek_apps": ["com.wbd.stream", "com.disney.disneyplus"],
    "exclude_apps": ["com.google.ios.youtube"],
}


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


class ScenesService(Service):
    name = "scenes"

    def __init__(self, lookup: Any = None) -> None:
        self.ctx: Any = None
        self._lookup = lookup            # injected in tests; built in start() otherwise
        self._lock = threading.Lock()    # held only to read or replace _snap / _jump
        self._snap: tuple | None = None  # (film_key, app_id, scenes): never mutated
        self._jump: tuple | None = None  # (scene_start, at_monotonic): never mutated
        self._seen: tuple | None = None  # (film_key, first_seen_monotonic): watch only
        self._no_seek_logged: tuple | None = None   # film key already logged as unseekable
        self._icons: dict[str, str] = {}

    def start(self, ctx: Any) -> None:
        self.ctx = ctx
        # ctx.icon checks the disk: resolve once here, not on the render path.
        self._icons = {"on": ctx.icon("scene"), "off": ctx.icon("scene_off")}
        if self._lookup is None:
            try:
                self._lookup = Lookup(ctx, Db(ctx.store.sqlite("scenes")))
            except Exception as e:
                ctx.log(f"database cannot be opened ({type(e).__name__}): lookups off")

    def _cfg(self, name: str) -> Any:
        return self.ctx.config.get(name, _DEFAULTS[name])

    def entities(self) -> set[str]:
        return {self.ctx.config["atv"]}

    # --- reading the Apple TV (no I/O) -------------------------------------

    def _film(self, st: dict | None) -> tuple | None:
        """(film_key, title, runtime_s, app_id) when a film is on, else None."""
        if not st or st.get("s") not in ("playing", "paused"):
            return None
        a = st.get("a") or {}
        app = a.get("app_id") or ""
        title = a.get("media_title") or ""
        if app in self._cfg("exclude_apps") or not isinstance(title, str) or not title.strip():
            return None
        dur = _num(a.get("media_duration"))
        if not ((dur is not None and dur >= self._cfg("min_film_s")) or a.get("media_content_type") == "movie"):
            return None
        norm = norm_title(title)
        if not norm:
            return None
        runtime_s = None if dur is None else int(round(dur))
        key = (norm, None if dur is None else round(dur / 60), app)
        return key, title, runtime_s, app

    def _real_position(self, st: dict | None) -> float | None:
        """HA's position, plus its age while playing; None if missing."""
        a = (st or {}).get("a") or {}
        pos = _num(a.get("media_position"))
        if pos is None:
            return None
        if st.get("s") == "playing":
            try:
                updated = datetime.fromisoformat(a["media_position_updated_at"]).timestamp()
                pos += self.ctx.clock() - updated
            except (KeyError, TypeError, ValueError):
                pass
        return pos

    def _position(self, real: float | None) -> float | None:
        """The real position, or the start of the scene jumped to until playback passes it."""
        if real is None:
            return None
        with self._lock:
            jump = self._jump
        if jump is None:
            return real
        start, at = jump
        held = self.ctx.monotonic() - at >= self._cfg("seek_hold_s")
        if real > start or (held and real < start - (self._cfg("lead_s") + 10)):
            with self._lock:
                if self._jump is jump:
                    self._jump = None
            return real
        return start

    def _plan(self) -> tuple[float, float, tuple] | None:
        """(real_position, next_scene_start, snapshot) when a jump is possible, else None."""
        st = self.ctx.state(self.ctx.config["atv"])
        with self._lock:
            snap = self._snap
        film = self._film(st)
        if snap is None or film is None or film[0] != snap[0] or snap[1] not in self._cfg("seek_apps"):
            return None
        real = self._real_position(st)
        pos = self._position(real)
        if pos is None:
            return None
        limit = pos + self._cfg("guard_s")
        start = next((s for s, _ in snap[2] if s > limit), None)
        return None if start is None else (real, start, snap)

    def _next_start(self) -> float | None:
        plan = self._plan()
        return None if plan is None else plan[1]

    def _redraw_for(self, scenes: tuple, position: float | None) -> None:
        """Redraw when the last scene can no longer be jumped to (it gets no event)."""
        st = self.ctx.state(self.ctx.config["atv"]) or {}
        if not scenes or position is None or st.get("s") != "playing":
            return
        until = max(s for s, _ in scenes) - self._cfg("guard_s") - position
        if until > 0:
            self.ctx.redraw_in(until + 0.5)

    # --- job ---------------------------------------------------------------

    def _set_snap(self, snap: tuple | None) -> tuple | None:
        with self._lock:
            old, self._snap, self._jump = self._snap, snap, None
        return old

    @job("watch", first_in=0)
    def watch(self) -> float:
        ctx = self.ctx
        film = self._film(ctx.state(ctx.config["atv"]))
        with self._lock:
            snap = self._snap
        if film is None or (snap is not None and snap[0] != film[0]):
            if self._set_snap(None) is not None:
                ctx.changed()
        if film is None:
            self._seen = None
            return WATCH_S
        key, title, runtime_s, app = film
        if snap is not None and snap[0] == key:
            return WATCH_S                                   # looked up already
        now = ctx.monotonic()
        if self._seen is None or self._seen[0] != key:
            self._seen = (key, now)                          # settle clock starts
            return WATCH_S
        if now - self._seen[1] < self._cfg("settle_s") or self._lookup is None:
            return WATCH_S
        ctx.log(f'film: "{title}" {key[1]} min in {app}')
        try:
            scenes = tuple(self._lookup.run(title, runtime_s, app))
        except Exception as e:                               # Lookup.run should not raise; stay alive
            ctx.log(f"lookup failed: {type(e).__name__}")
            scenes = ()
        current = self._film(ctx.state(ctx.config["atv"]))
        if current is None or current[0] != key:
            return WATCH_S                                   # film changed while looking up
        self._set_snap((key, app, scenes))
        if scenes and app not in self._cfg("seek_apps") and self._no_seek_logged != key:
            self._no_seek_logged = key
            ctx.log("app cannot seek: key stays gray")
        ctx.changed()
        self._redraw_for(scenes, self._real_position(ctx.state(ctx.config["atv"])))
        return WATCH_S

    # --- render path ---------------------------------------------------------

    @condition("ready")
    def ready(self) -> bool:
        return self._next_start() is not None

    def key(self, name: str, args: dict) -> dict | None:
        if name != "next":
            return None
        return {"icon": self._icons["off"], "label": "",
                "modes": [{"when": "scenes.ready", "icon": self._icons["on"]}],
                "action": {"service": "scenes", "do": "next"}}

    # --- action ----------------------------------------------------------------

    @action("next")
    def next(self, args: dict) -> None:
        ctx = self.ctx
        plan = self._plan()
        if plan is None:
            return
        real, start, snap = plan
        target = max(real, start - self._cfg("lead_s"))
        ctx.call("media_player", "media_seek", {"entity_id": ctx.config["atv"], "seek_position": round(target)})
        with self._lock:
            if self._snap is snap:                           # not if the film changed meanwhile
                self._jump = (start, ctx.monotonic())
        ctx.log(f"jump {round(real)}s -> {round(target)}s (scene at {round(start)}s)")
        ctx.changed()
        self._redraw_for(snap[2], target)
