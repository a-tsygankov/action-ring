"""Accelerated, coalesced Apple TV knob handling: scrubbing and menu moves.

Each detent is recorded with a speed tier taken from the time since the
previous detent on the same axis (slow / medium / fast). Detents are
coalesced into one Home Assistant call, sent after a short quiet gap or a
maximum batch age. While a call is in flight, new detents collect into the
next batch, so a fast spin never builds a growing queue.

- horizontal axis, Apple TV playing/paused:
  - "scrub": "touch" (and a Companion connection): tvOS preview scrubbing.
    Pause, put a virtual finger on the touch surface, drag it with the knob,
    lift it after a short idle; the knob press (select) then jumps to the
    previewed position, as on a real Siri Remote. The finger re-grips in the
    centre when it reaches an edge.
  - "scrub": "seek": absolute seek (media_player.media_seek), falling back to
    skip_* for apps that report no seekable position.
- otherwise: focus moves (remote.send_command with num_repeats).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

PLAYBACK_STATES = ("playing", "paused")
SEEK_FEATURE = 2  # MediaPlayerEntityFeature.SEEK

DEFAULTS: dict[str, Any] = {
    # Scrub amount for the slow, medium and fast tiers: seconds per detent, or
    # [seconds, detents] ("30 s per 2 clicks"). Applied per detent at that
    # detent's tier, so speeding up / slowing down mid-turn changes the rate.
    "seek_steps": [10, [30, 2], [90, 3]],
    "nav_repeats": [1, 2, 3],  # focus moves per detent: slow, medium, fast
    "medium_interval": 0.25,  # detents closer than this are "medium"
    "fast_interval": 0.08,  # ...and closer than this are "fast"
    "flush_quiet": 0.06,  # send a batch after this long without a detent
    "flush_max": 0.15,  # ...or once it is this old
    "nav_delay": 0.05,  # seconds between repeated focus moves
    "nav_max": 20,  # cap on focus moves per call
    "skip_max": 6,  # cap on skip_* per call (apps without seek support)
    "seek_hold": 3.0,  # keep scrubbing from our own target for this long
    "scrub": "seek",  # "touch" for tvOS preview scrubbing (needs pairing)
    # Touch scrubbing, in touchpad units (0..1000 across the surface).
    "touch_steps": [12, [60, 2], [150, 3]],  # finger travel per detent by tier
    "touch_y": 500,  # finger height on the surface
    "touch_min_x": 150,  # re-grip in the centre when the finger passes these
    "touch_max_x": 850,
    "touch_move_px": 25,  # max finger move per touch event
    "touch_interval": 0.016,  # s between touch events (as pyatv's own swipe)
    "touch_release_after": 0.6,  # lift the finger after this long idle
    "touch_pause_delay": 0.4,  # wait after pausing before touching
    # After a scrub (seek or touch), this long without knob activity resumes
    # playback: touch scrubs are committed with select first. 0 disables.
    "resume_after": 3.0,
    "resume_check_delay": 1.5,  # after the commit, wait before checking state
}

def per_detent(step: Any) -> float:
    """Amount per detent for a step given as N or [amount, detents]."""
    if isinstance(step, (list, tuple)):
        return float(step[0]) / float(step[1])
    return float(step)


Call = Callable[[str, str, dict[str, Any]], None]
StateLookup = Callable[[str], "dict[str, Any] | None"]


class AppleTVKnobs:
    def __init__(
        self, cfg: dict[str, Any], call: Call, state: StateLookup, touch: Any = None
    ) -> None:
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
        self.remote: str = cfg["remote"]
        self.player: str = cfg["media_player"]
        self._call = call
        self._state = state
        self._touch = touch  # AppleTVTouch-like: available(), action(x, y, mode)
        self._cv = threading.Condition()
        self._pending: dict[str, dict[str, Any]] = {}
        self._last_detent: dict[str, float] = {}
        self._seek_target: float | None = None
        self._seek_time = 0.0
        # Touch-scrub finger; only the sender thread touches these.
        self._finger_down = False
        self._finger_x = 0
        self._finger_last = 0.0
        # Scrub session awaiting auto-resume; guarded by _cv.
        self._scrub_active = False
        self._scrub_kind = "seek"
        self._scrub_last = 0.0
        self._press_requested = False
        threading.Thread(target=self._run, daemon=True, name="atv-knobs").start()

    # -- input (SDK reader thread) ---------------------------------------
    def tier(self, interval: float | None) -> int:
        if interval is None or interval >= self.cfg["medium_interval"]:
            return 0
        return 2 if interval < self.cfg["fast_interval"] else 1

    def detent(self, spec: dict[str, Any]) -> None:
        """A knob event: {"axis", "direction"} for a turn, {"press": true} for
        the knob's press (select; commits a scrub and cancels auto-resume)."""
        if spec.get("press"):
            with self._cv:
                self._press_requested = True
                self._cv.notify()
            return
        axis = "vertical" if spec.get("axis") == "vertical" else "horizontal"
        direction = 1 if spec.get("direction", 1) > 0 else -1
        now = time.monotonic()
        with self._cv:
            prev = self._last_detent.get(axis)
            self._last_detent[axis] = now
            tier = self.tier(None if prev is None else now - prev)
            batch = self._pending.get(axis)
            if batch is None:
                batch = {"units": 0, "first": now, "last": now, "mode": self._mode(axis)}
                self._pending[axis] = batch
            steps = {
                "seek": self.cfg["seek_steps"],
                "touch": self.cfg["touch_steps"],
                "nav": self.cfg["nav_repeats"],
            }[batch["mode"]]
            batch["units"] += direction * per_detent(steps[tier])
            batch["last"] = now
            if batch["mode"] in ("seek", "touch"):
                self._scrub_active, self._scrub_kind, self._scrub_last = True, batch["mode"], now
            self._cv.notify()

    def _mode(self, axis: str) -> str:
        if axis != "horizontal":
            return "nav"
        if self._finger_down:  # mid-scrub: keep dragging
            return "touch"
        if not self._in_playback():
            return "nav"
        if self.cfg["scrub"] == "touch" and self._touch is not None and self._touch.available():
            return "touch"
        return "seek"

    # -- output (own thread) -----------------------------------------------
    def _next_job(self) -> tuple[str, Any]:
        """Block until there is work; called with _cv held. Order: batches,
        knob press, idle finger lift, auto-resume after a scrub."""
        quiet, max_age = self.cfg["flush_quiet"], self.cfg["flush_max"]
        release_after = self.cfg["touch_release_after"]
        resume_after = self.cfg["resume_after"]
        while True:
            now = time.monotonic()
            due = [
                axis
                for axis, b in self._pending.items()
                if now - b["last"] >= quiet or now - b["first"] >= max_age
            ]
            if due:
                return "batches", [(axis, self._pending.pop(axis)) for axis in due]
            if self._press_requested:
                self._press_requested = False
                was_scrubbing, self._scrub_active = self._scrub_active, False
                return "press", was_scrubbing
            idle = not self._pending
            wakes = [min(b["last"] + quiet, b["first"] + max_age) for b in self._pending.values()]
            if idle and self._finger_down:
                if now - self._finger_last >= release_after:
                    return "release", None
                wakes.append(self._finger_last + release_after)
            if idle and self._scrub_active and resume_after > 0:
                if now - self._scrub_last >= resume_after:
                    self._scrub_active = False
                    return "resume", self._scrub_kind
                wakes.append(self._scrub_last + resume_after)
            if wakes:
                self._cv.wait(max(min(wakes) - now, 0.005))
            else:
                self._cv.wait()

    def _run(self) -> None:
        while True:
            with self._cv:
                job, payload = self._next_job()
            try:
                if job == "batches":
                    for axis, batch in payload:
                        try:
                            self._send(axis, batch)
                        except Exception as err:  # keep the knob alive on HA errors
                            print(f"atv-knob: {axis} {batch['mode']} failed: {err}", flush=True)
                elif job == "press":
                    self._press(payload)
                elif job == "release":
                    self._finger_release()
                elif job == "resume":
                    self._resume(payload)
            except Exception as err:
                print(f"atv-knob: {job} failed: {err}", flush=True)

    def _select(self) -> None:
        self._call("remote", "send_command", {"entity_id": self.remote, "command": "select"})

    def _press(self, was_scrubbing: bool) -> None:
        if self._finger_down:
            self._finger_release()
        self._select()
        print("atv-knob: select" + (" (scrub committed)" if was_scrubbing else ""), flush=True)

    def _resume(self, kind: str) -> None:
        """No knob activity for resume_after seconds after a scrub: commit a
        touch preview with select, then make sure playback runs."""
        if self._finger_down:
            self._finger_release()
        if kind == "touch":
            self._select()
            print("atv-knob: auto-commit scrub", flush=True)
            time.sleep(self.cfg["resume_check_delay"])
        if (self._state(self.player) or {}).get("s") == "paused":
            self._call("media_player", "media_play", {"entity_id": self.player})
            print("atv-knob: auto-resume", flush=True)

    def _send(self, axis: str, batch: dict[str, Any]) -> None:
        units = batch["units"]
        if abs(units) < 0.5:  # e.g. left and right detents cancelled out
            return
        if batch["mode"] == "touch":
            try:
                self._finger_drag(units)
            except Exception:
                self._finger_down = False  # start a fresh gesture next time
                raise
            return
        if batch["mode"] == "seek":
            target = self._seek_target_for(units)
            if target is not None:
                print(f"atv-knob: seek {units:+.0f}s -> {target}s", flush=True)
                self._call(
                    "media_player",
                    "media_seek",
                    {"entity_id": self.player, "seek_position": target},
                )
                return
            # No seekable position (app-dependent): use the app's own skips.
            command = "skip_forward" if units > 0 else "skip_backward"
            repeats = max(1, round(abs(units) / per_detent(self.cfg["seek_steps"][0])))
            repeats = min(repeats, self.cfg["skip_max"])
        else:
            if axis == "horizontal":
                command = "right" if units > 0 else "left"
            else:
                command = "down" if units > 0 else "up"
            repeats = min(round(abs(units)), self.cfg["nav_max"])
        print(f"atv-knob: {command} x{repeats}", flush=True)
        self._call(
            "remote",
            "send_command",
            {
                "entity_id": self.remote,
                "command": command,
                "num_repeats": repeats,
                "delay_secs": self.cfg["nav_delay"],
            },
        )

    # -- touch scrubbing (sender thread) ----------------------------------------
    def _finger_drag(self, distance: float) -> None:
        cfg, touch = self.cfg, self._touch
        y, lo, hi = cfg["touch_y"], cfg["touch_min_x"], cfg["touch_max_x"]
        centre = (lo + hi) // 2
        if not self._finger_down:
            if (self._state(self.player) or {}).get("s") == "playing":
                # Preview scrubbing only engages while paused.
                self._call("media_player", "media_pause", {"entity_id": self.player})
                time.sleep(cfg["touch_pause_delay"])
            touch.action(centre, y, "press")
            self._finger_x, self._finger_down = centre, True
        remaining = distance
        while abs(remaining) >= 1:
            target = round(min(hi, max(lo, self._finger_x + remaining)))
            remaining -= target - self._finger_x
            self._finger_move(target)
            if abs(remaining) >= 1:
                # Edge reached: lift and re-grip in the centre, like a
                # repeated swipe on a real remote.
                touch.action(self._finger_x, y, "release")
                time.sleep(cfg["touch_interval"])
                touch.action(centre, y, "press")
                self._finger_x = centre
        self._finger_last = time.monotonic()
        print(f"atv-knob: touch drag {distance:+.0f} -> x={self._finger_x}", flush=True)

    def _finger_move(self, target: int) -> None:
        start, step = self._finger_x, self.cfg["touch_move_px"]
        count = max(1, -(-abs(target - start) // step))  # ceil
        for i in range(1, count + 1):
            time.sleep(self.cfg["touch_interval"])
            x = round(start + (target - start) * i / count)
            self._touch.action(x, self.cfg["touch_y"], "hold")
        self._finger_x = target

    def _finger_release(self) -> None:
        try:
            self._touch.action(self._finger_x, self.cfg["touch_y"], "release")
        except Exception as err:
            print(f"atv-knob: touch release failed: {err}", flush=True)
        self._finger_down = False
        print("atv-knob: touch released", flush=True)

    # -- state helpers -------------------------------------------------------
    def _in_playback(self) -> bool:
        return (self._state(self.player) or {}).get("s") in PLAYBACK_STATES

    def _seek_target_for(self, delta: float) -> int | None:
        st = self._state(self.player) or {}
        attrs = st.get("a") or {}
        if not int(attrs.get("supported_features") or 0) & SEEK_FEATURE:
            return None
        now = time.monotonic()
        if self._seek_target is not None and now - self._seek_time < self.cfg["seek_hold"]:
            # HA's reported position lags a seek; keep building on ours.
            base = self._seek_target
        else:
            pos = attrs.get("media_position")
            if pos is None:
                return None
            base = float(pos)
            updated = attrs.get("media_position_updated_at")
            if st.get("s") == "playing" and updated:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(updated)
                    base += age.total_seconds()
                except (TypeError, ValueError):
                    pass
        target = max(0.0, base + delta)
        duration = attrs.get("media_duration")
        if duration:
            target = min(target, max(0.0, float(duration) - 1))
        self._seek_target, self._seek_time = target, now
        return round(target)
