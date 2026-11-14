"""profiles: opens an Apple TV app and presses OK on its profile picker.

A streaming app started from the dock may stop on "Who's watching". The
action "open" launches the app and, only when the app was not running before
(a cold launch, known from atv_clients) and is listed with "picker": true,
presses select once after the app's delay. If that click starts a film instead
(the app was on its home screen after all), it sends Menu once.

Config (services.profiles): atv, atv_remote, lg, atv_source, power_on_wait
(as tabs.launch); dry_run (decide and log, send nothing); delay_s 6, watch_s 5,
film_s 300, sync_wait_s 5, input_grace_s 1; apps: {bundle id: {"picker": bool,
"registers": "launch" | "playback" (default), "delay_s", "undo": bool (default
true)}}. Apps not in "apps" are only launched.

Never clicks when the app's status is unknown, when the app is playing or
paused, or after the dock was used. Menu is sent at most once per open, only
after our own click, only for playback that was not there before it.

Threads: "open" runs on its own thread per press and blocks (ctx.sleep). The
lock guards the open counter, the open times and the history. No render path.
"""

from __future__ import annotations

import threading
from typing import Any

from keyspec import variant
from services import Service, action

from .decide import decide

POLL_S = 0.25
HISTORY = 100
UNDO_TOAST = "Opened by mistake: went back"


class ProfilesService(Service):
    name = "profiles"

    def __init__(self) -> None:
        self.ctx: Any = None
        self._lock = threading.Lock()
        self._gen = 0                           # the newest open(); older ones stand down
        self._opened: dict[str, float] = {}     # app -> monotonic time of our last open
        self._history: list[dict] | None = None

    def start(self, ctx) -> None:
        self.ctx = ctx

    def entities(self) -> set[str]:
        cfg = self.ctx.config
        return {e for e in (cfg.get("atv"), cfg.get("lg")) if e}

    # -- config ---------------------------------------------------------------
    def _num(self, name: str, default: float, entry: dict | None = None) -> float:
        for source in (entry or {}, self.ctx.config):
            value = source.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return default

    # -- what the TV reports ----------------------------------------------------
    def _atv(self) -> tuple[str | None, dict]:
        st = self.ctx.state(self.ctx.config.get("atv") or "") or {}
        return st.get("s"), st.get("a") or {}

    def _film(self, app: str) -> float | None:
        """The duration of real playback in `app` right now, else None."""
        state, attrs = self._atv()
        duration = attrs.get("media_duration")
        if state != "playing" or attrs.get("app_id") != app:
            return None
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            return None
        return float(duration) if duration >= self._num("film_s", 300) else None

    def _lg_on(self) -> bool:
        st = self.ctx.state(self.ctx.config.get("lg") or "")
        return variant(st.get("s") if st else None) == "on"

    def _on_atv_input(self) -> bool:
        st = self.ctx.state(self.ctx.config.get("lg") or "") or {}
        return self._lg_on() and (st.get("a") or {}).get("source") == self.ctx.config.get("atv_source")

    def _dock_used(self, t0: float) -> bool:
        """The dock was used after this open's own press."""
        dock = self.ctx.shared("dock_input")
        return dock is not None and dock.last() > t0 + self._num("input_grace_s", 1.0)

    # -- the action -------------------------------------------------------------
    @action("open")
    def open(self, args: dict) -> None:
        app = (args or {}).get("app")
        if not isinstance(app, str) or not app:
            self.ctx.log("open: no app")
            return
        ctx = self.ctx
        with self._lock:
            self._gen += 1
            gen = self._gen
        t0 = ctx.monotonic()
        was_off = not self._lg_on()
        if not ctx.media.ensure_on():
            ctx.log(f"open {app}: TV did not turn on")
            return
        status, removed_at, pid = self._status(app, t0, was_off)  # before the launch: apps register fast
        if not ctx.media.launch("atv", app):
            return
        with self._lock:
            opened_before = self._opened.get(app)
            self._opened[app] = ctx.monotonic()
        outcome = "no click"
        reason = "error"
        try:
            reason, outcome = self._after_launch(app, gen, t0, status, removed_at, pid, opened_before)
        except Exception as err:  # the app is open; never raise past the launch
            ctx.log(f"{app}: {type(err).__name__}: {err}")
            outcome = "error"
        self._record(app, status, reason, outcome)

    def _status(self, app: str, t0: float, was_off: bool) -> tuple[str, float | None, int | None]:
        """(status, removed_at, pid) from the tracker; "unknown" on any doubt."""
        ctx = self.ctx
        try:
            tracker = ctx.shared("atv_clients")
            if tracker is None:
                return "unknown", None, None
            if was_off:
                # The Apple TV slept: the old session may be dead without notice.
                # Trust only a session that began after this press.
                deadline = ctx.monotonic() + self._num("sync_wait_s", 5.0)
                while True:
                    session = tracker.session_at()
                    if session is not None and session > t0 and tracker.status(app) != "unknown":
                        break
                    if ctx.monotonic() >= deadline:
                        return "unknown", None, None
                    ctx.sleep(POLL_S)
            return tracker.status(app), tracker.removed_at(app), tracker.pid(app)
        except Exception as err:
            ctx.log(f"{app}: tracker {type(err).__name__}: {err}")
            return "unknown", None, None

    def _after_launch(self, app: str, gen: int, t0: float, status: str, removed_at: float | None,
                      pid: int | None, opened_before: float | None) -> tuple[str, str]:
        """(reason, outcome). Decides, waits, clicks, watches."""
        ctx = self.ctx
        apps = ctx.config.get("apps")
        entry = apps.get(app) if isinstance(apps, dict) else None
        dry_run = bool(ctx.config.get("dry_run"))
        reason, click = decide(entry, status, opened_at=opened_before, removed_at=removed_at, dry_run=dry_run)
        shown = f"alive (pid {pid})" if status == "alive" and pid is not None else status
        delay = self._num("delay_s", 6.0, entry if isinstance(entry, dict) else None)
        if not click:
            ctx.log(f"open {app}: {'(dry run) ' if dry_run else ''}{shown} -> {reason}, no click")
            return reason, "no click"
        if dry_run:
            ctx.log(f"open {app}: (dry run) {shown} -> {reason}, would click in {delay:.1f}s")
            return reason, "dry run"
        ctx.log(f"open {app}: {shown} -> {reason}, click in {delay:.1f}s")

        deadline = ctx.monotonic() + delay
        while ctx.monotonic() < deadline:
            ctx.sleep(min(POLL_S, deadline - ctx.monotonic()))
            why = self._stand_down(app, gen, t0)
            if why:
                ctx.log(f"{app}: no click ({why})")
                return reason, f"cancelled: {why}"
        why = self._stand_down(app, gen, t0) or self._not_ready(app, t0)
        if why:
            ctx.log(f"{app}: no click ({why})")
            return reason, f"cancelled: {why}"
        was_film = self._film(app) is not None
        self._send("select")
        ctx.log(f"{app}: click sent")
        if was_film or not entry.get("undo", True):
            return reason, "clicked"
        return reason, "clicked, undone" if self._watch(app, gen, t0) else "clicked"

    def _stand_down(self, app: str, gen: int, t0: float) -> str | None:
        """Why this open must send nothing more: the viewer has moved on."""
        with self._lock:
            if self._gen != gen:
                return "newer open"
        if self._dock_used(t0):
            return "dock input"
        return None

    def _not_ready(self, app: str, t0: float) -> str | None:
        """Why the click must not be sent now, or None."""
        tracker = self.ctx.shared("atv_clients")
        if tracker is None or tracker.status(app) == "unknown":
            return "connection lost"
        removed = tracker.removed_at(app)
        if removed is not None and removed > t0:
            return "app ended"
        if not self._on_atv_input():
            return "input changed"
        state, attrs = self._atv()
        if attrs.get("app_id") == app and state in ("playing", "paused"):
            return "already playing"
        return None

    def _watch(self, app: str, gen: int, t0: float) -> bool:
        """After our click: Menu once if a film starts. True if it was sent."""
        ctx = self.ctx
        deadline = ctx.monotonic() + self._num("watch_s", 5.0)
        while ctx.monotonic() < deadline:
            ctx.sleep(POLL_S)
            if self._stand_down(app, gen, t0):
                return False
            duration = self._film(app)
            if duration is not None:
                self._send("menu")
                ctx.log(f"{app}: playback {duration:.0f}s after click -> menu sent")
                ctx.toast(UNDO_TOAST)
                return True
        return False

    def _send(self, command: str) -> None:
        self.ctx.call("remote", "send_command",
                      {"entity_id": self.ctx.config["atv_remote"], "command": command})

    def _record(self, app: str, status: str, reason: str, outcome: str) -> None:
        """Keep the last opens in the store (the app log rolls over)."""
        ctx = self.ctx
        try:
            with self._lock:
                if self._history is None:
                    loaded = ctx.store.load("history", [])
                    self._history = loaded if isinstance(loaded, list) else []
                self._history.append({"at": ctx.clock(), "app": app, "status": status,
                                      "reason": reason, "outcome": outcome})
                del self._history[:-HISTORY]
                snapshot = list(self._history)
            ctx.store.save("history", snapshot)
        except Exception as err:
            ctx.log(f"history: {type(err).__name__}: {err}")
