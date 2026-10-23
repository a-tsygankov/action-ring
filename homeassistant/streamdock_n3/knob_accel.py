"""Accelerated, coalesced Apple TV knob handling: scrubbing and menu moves.

Each detent is recorded with a speed tier taken from the time since the
previous detent on the same axis (slow / medium / fast). Detents are
coalesced into one Home Assistant call, sent after a short quiet gap or a
maximum batch age. While a call is in flight, new detents collect into the
next batch, so a fast spin never builds a growing queue.

- horizontal axis, Apple TV playing/paused: absolute seek
  (media_player.media_seek), falling back to skip_* for apps that report no
  seekable position.
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
}

def per_detent(step: Any) -> float:
    """Amount per detent for a step given as N or [amount, detents]."""
    if isinstance(step, (list, tuple)):
        return float(step[0]) / float(step[1])
    return float(step)


Call = Callable[[str, str, dict[str, Any]], None]
StateLookup = Callable[[str], "dict[str, Any] | None"]


class AppleTVKnobs:
    def __init__(self, cfg: dict[str, Any], call: Call, state: StateLookup) -> None:
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
        self.remote: str = cfg["remote"]
        self.player: str = cfg["media_player"]
        self._call = call
        self._state = state
        self._cv = threading.Condition()
        self._pending: dict[str, dict[str, Any]] = {}
        self._last_detent: dict[str, float] = {}
        self._seek_target: float | None = None
        self._seek_time = 0.0
        threading.Thread(target=self._run, daemon=True, name="atv-knobs").start()

    # -- input (SDK reader thread) ---------------------------------------
    def tier(self, interval: float | None) -> int:
        if interval is None or interval >= self.cfg["medium_interval"]:
            return 0
        return 2 if interval < self.cfg["fast_interval"] else 1

    def detent(self, spec: dict[str, Any]) -> None:
        axis = "vertical" if spec.get("axis") == "vertical" else "horizontal"
        direction = 1 if spec.get("direction", 1) > 0 else -1
        now = time.monotonic()
        with self._cv:
            prev = self._last_detent.get(axis)
            self._last_detent[axis] = now
            tier = self.tier(None if prev is None else now - prev)
            batch = self._pending.get(axis)
            if batch is None:
                mode = "seek" if axis == "horizontal" and self._in_playback() else "nav"
                batch = {"units": 0, "first": now, "last": now, "mode": mode}
                self._pending[axis] = batch
            steps = self.cfg["seek_steps"] if batch["mode"] == "seek" else self.cfg["nav_repeats"]
            batch["units"] += direction * per_detent(steps[tier])
            batch["last"] = now
            self._cv.notify()

    # -- output (own thread) -----------------------------------------------
    def _run(self) -> None:
        quiet, max_age = self.cfg["flush_quiet"], self.cfg["flush_max"]
        while True:
            with self._cv:
                while True:
                    now = time.monotonic()
                    due = [
                        axis
                        for axis, b in self._pending.items()
                        if now - b["last"] >= quiet or now - b["first"] >= max_age
                    ]
                    if due:
                        break
                    if not self._pending:
                        self._cv.wait()
                        continue
                    wake = min(
                        min(b["last"] + quiet, b["first"] + max_age)
                        for b in self._pending.values()
                    )
                    self._cv.wait(max(wake - now, 0.005))
                batches = [(axis, self._pending.pop(axis)) for axis in due]
            for axis, batch in batches:
                try:
                    self._send(axis, batch)
                except Exception as err:  # keep the knob alive on HA errors
                    print(f"atv-knob: {axis} {batch['mode']} failed: {err}", flush=True)

    def _send(self, axis: str, batch: dict[str, Any]) -> None:
        units = batch["units"]
        if abs(units) < 0.5:  # e.g. left and right detents cancelled out
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
