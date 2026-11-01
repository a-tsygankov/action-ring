"""Live ad detection for apps that keep a separate "show clock" (Pluto TV).

Pluto TV gives Home Assistant nothing when an ad starts and a false "paused"
when it ends. Its raw media-remote (MRP) data does carry a signal, found in a
capture of a 2.5-minute break (docs/JOURNAL.md):

- The stream plays in an AVKit player ("avkit-…", ads included). Pluto also
  publishes a show clock on its default player ("MediaRemote-DefaultPlayer",
  ads excluded), which it is silent about while the show plays.
- Ad start: Pluto publishes its show clock (set to the break's cue point)
  only during ads or around a user pause/play/scrub. So any show-clock update
  while the stream plays, and not right after a stream state change, is an
  ad. (The cue point can equal continuous playback, e.g. after a quiet
  stretch, so a clock "jump" alone isn't required.)
- During the ad: the show clock is republished every 30 s (15 s near the end)
  frozen at the cue point.
- Ad end: the stream player republishes its supported commands; the show
  clock then goes quiet (or advances).

AdDetector turns these into ad start/end and mutes the configured speaker for
the break, undoing only its own mute (same rules as the HA pause-mute
automation). Events come from the app's own MRP session (AirPlay pairing).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

DEFAULTS: dict[str, Any] = {
    "apps": ["tv.pluto.ios"],
    "jump_threshold": 20.0,  # clock ahead of real time by more than this = cue jump
    "frozen_min": 10.0,  # clock unchanged across at least this long = frozen
    "stream_quiet": 5.0,  # ignore clock jumps this soon after a stream state change
    # The ad ends when the show-clock heartbeat stops. Pluto beats every
    # 15-30 s (one seen skipped) and nothing else marks the pod's end, so wait
    # heartbeat_min or heartbeat_factor x the last beat gap, whichever is
    # longer, capped at heartbeat_timeout. A beat arriving after the end
    # starts the ad again (mute back on).
    "heartbeat_min": 40.0,
    "heartbeat_factor": 1.5,
    "heartbeat_timeout": 90.0,
    # Frozen-clock apps (Peacock): the app's own player keeps state Playing
    # while an ad runs, but its content clock is published with playbackRate
    # 0 and its supportedCommands republished within a few seconds; the ad
    # ends with a clock update at rate > 0 (plus a republish). A scrub also
    # publishes rate-0 updates, but with the clock jumping and no republish.
    "frozen_apps": [],
    "frozen_cmds_window": 3.0,  # rate-0 update and republish this close = ad start
    "frozen_scrub_window": 3.0,  # ignore rate-0 updates this soon after a clock jump
    "frozen_jump": 5.0,  # clock off continuous playback by more than this = jump
    "frozen_timeout": 300.0,  # safety net: ad ends after this long regardless
}
DEFAULT_PLAYER = "MediaRemote-DefaultPlayer"
PLAYING = 1

Call = Callable[[str, str, dict[str, Any]], None]
StateLookup = Callable[[str], "dict[str, Any] | None"]


def log(msg: str) -> None:
    print(f"ad-detect: {time.strftime('%H:%M:%S')} {msg}", flush=True)


class AdDetector:
    def __init__(self, cfg: dict[str, Any], call: Call, state_of: StateLookup) -> None:
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if k in DEFAULTS}}
        self.apps = set(self.cfg["apps"])
        self.speaker: str = cfg["speaker"]  # media_player to mute (the Arc)
        self.source: str | None = cfg.get("source", "TV")  # only mute this source
        self.player: str | None = cfg.get("media_player")  # Apple TV, for app checks
        self.lg: str | None = cfg.get("lg")  # TV; with atv_source, only mute on that input
        self.atv_source: str | None = cfg.get("atv_source")
        self._call = call
        self._state_of = state_of
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.clock: tuple[float, float] | None = None  # (elapsed, elapsedTimestamp)
        self.clock_seen = 0.0
        self.beat_gap = 0.0  # seconds between the last two beats of this ad
        self.stream_state: int | None = None
        self.stream_change = -1e9
        self.ad = False
        self.muted_by_us = False

    # -- events (from the MRP hook; `now` is injectable for replay tests) ------
    def on_stream_state(self, bundle: str, state: int | None, commands: bool,
                        now: float | None = None) -> None:
        if bundle not in self.apps:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if state is not None and state != self.stream_state:
                self.stream_state, self.stream_change = state, now
            if commands and self.ad:
                self._end("stream player republished its commands")

    def on_show_clock(self, bundle: str, elapsed: float, stamp: float,
                      now: float | None = None) -> None:
        if bundle not in self.apps:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.ad and self.clock_seen:
                self.beat_gap = now - self.clock_seen
            prev, self.clock, self.clock_seen = self.clock, (elapsed, stamp), now
            streaming = self.stream_state == PLAYING
            scrubbing = now - self.stream_change < self.cfg["stream_quiet"]
            if not self.ad:
                if streaming and not scrubbing:
                    self._start(self._why(prev, elapsed, stamp))
                return
            if prev is not None:
                d_clock, d_time = elapsed - prev[0], stamp - prev[1]
                if d_time > 0 and abs(d_clock - d_time) < 5 and abs(d_clock) >= 1:
                    self._end("show clock advancing")

    def _why(self, prev, elapsed: float, stamp: float) -> str:
        if prev is None:
            return f"show clock published at {elapsed:.0f}s during playback"
        d_clock, d_time = elapsed - prev[0], stamp - prev[1]
        if abs(d_clock) < 1 and d_time >= self.cfg["frozen_min"]:
            return "show clock frozen"
        if d_clock - d_time > self.cfg["jump_threshold"]:
            return f"show clock jumped {d_clock:+.0f}s in {d_time:.0f}s"
        return f"show clock published at {elapsed:.0f}s during playback"

    def tick(self, now: float | None = None) -> None:
        """Periodic check: heartbeat stopped, or the app left the foreground."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self.ad:
                return
            if now - self.clock_seen > self._heartbeat_wait():
                self._end("show-clock heartbeat stopped")
            elif self.player:
                app = ((self._state_of(self.player) or {}).get("a") or {}).get("app_id")
                if app not in self.apps:
                    self._end(f"app changed to {app}")

    def _heartbeat_wait(self) -> float:
        wait = max(self.cfg["heartbeat_min"], self.cfg["heartbeat_factor"] * self.beat_gap)
        return min(wait, self.cfg["heartbeat_timeout"])

    # -- transitions (called with the lock held) -------------------------------
    def _start(self, why: str) -> None:
        self.ad = True
        self.beat_gap = 0.0
        log(f"ad started ({why})")
        threading.Thread(target=self._mute, args=(True,), daemon=True).start()

    def _end(self, why: str) -> None:
        self.ad = False
        log(f"ad ended ({why})")
        threading.Thread(target=self._mute, args=(False,), daemon=True).start()

    def _mute(self, on: bool) -> None:
        try:
            st = self._state_of(self.speaker) or {}
            attrs = st.get("a") or {}
            muted = bool(attrs.get("is_volume_muted"))
            if on:
                if muted or (self.source and attrs.get("source") != self.source):
                    return  # already muted (by the user), or not TV audio
                if self.lg and self.atv_source and                         (self._state_of(self.lg) or {}).get("a", {}).get("source") != self.atv_source:
                    return  # the TV shows another input (PS5): its sound isn't the ad
                self._call("media_player", "volume_mute",
                           {"entity_id": self.speaker, "is_volume_muted": True})
                self.muted_by_us = True
                log("muted")
            elif self.muted_by_us:
                self.muted_by_us = False
                if muted:  # the user may have unmuted already
                    self._call("media_player", "volume_mute",
                               {"entity_id": self.speaker, "is_volume_muted": False})
                    log("unmuted")
        except Exception as err:
            log(f"mute {'on' if on else 'off'} failed: {err}")


class FrozenClockDetector(AdDetector):
    """Ads in apps whose own player stays Playing but freezes its content
    clock (Peacock). Feed on_player_state / on_clock from the app's
    MediaRemote-DefaultPlayer; same mute rules and transitions as AdDetector."""

    def __init__(self, cfg: dict[str, Any], call: Call, state_of: StateLookup) -> None:
        super().__init__(cfg, call, state_of)
        self.apps = set(self.cfg["frozen_apps"])

    def reset(self) -> None:
        super().reset()
        self.player_state: int | None = None
        self.cmds_at = -1e9  # last supportedCommands republish
        self.frozen_at: float | None = None  # first rate-0 clock since the clock last ran
        self.jump_at = -1e9  # last clock jump (scrub)
        self.last_clock: tuple[float, float, float] | None = None  # (elapsed, rate, at)
        self.ad_since = 0.0

    def on_player_state(self, bundle: str, state: int | None, commands: bool,
                        now: float | None = None) -> None:
        if bundle not in self.apps:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if state is not None:
                self.player_state = state
                if state != PLAYING and self.ad:
                    self._end("player left Playing")
            if commands:
                self.cmds_at = now
                self._maybe_start(now)

    def on_clock(self, bundle: str, elapsed: float, rate: float, now: float | None = None) -> None:
        if bundle not in self.apps:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.last_clock is not None:
                e0, r0, t0 = self.last_clock
                if abs(elapsed - (e0 + r0 * (now - t0))) > self.cfg["frozen_jump"]:
                    self.jump_at = now
            self.last_clock = (elapsed, rate, now)
            if rate > 0:
                self.frozen_at = None
                if self.ad:
                    self._end("clock running again")
                return
            if self.frozen_at is None:
                self.frozen_at = now
            self._maybe_start(now)

    def _maybe_start(self, now: float) -> None:
        if self.ad or self.player_state != PLAYING or self.frozen_at is None:
            return
        if abs(now - self.cmds_at) > self.cfg["frozen_cmds_window"]:
            return
        if now - self.jump_at < self.cfg["frozen_scrub_window"]:
            return
        self.ad_since = now
        self._start("clock frozen with commands republished")

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self.ad:
                return
            if now - self.ad_since > self.cfg["frozen_timeout"]:
                self._end("timeout")
            elif self.player:
                app = ((self._state_of(self.player) or {}).get("a") or {}).get("app_id")
                if app not in self.apps:
                    self._end(f"app changed to {app}")


def install(detector: AdDetector, frozen: "FrozenClockDetector | None" = None) -> None:
    """Feed every MRP session in this process into the detectors, and run
    their periodic checks."""
    from pyatv.protocols.mrp import protobuf
    from pyatv.protocols.mrp import protocol as mrp_protocol

    def path_of(inner) -> tuple[str, str]:
        pp = inner.playerPath
        return pp.client.bundleIdentifier, pp.player.identifier

    async def on_set_state(message) -> None:
        inner = protobuf.extract_inner(message)
        bundle, player = path_of(inner)
        state = inner.playbackState if inner.HasField("playbackState") else None
        if player.startswith("avkit-"):
            detector.on_stream_state(bundle, state, inner.HasField("supportedCommands"))
        elif player == DEFAULT_PLAYER and frozen is not None:
            frozen.on_player_state(bundle, state, inner.HasField("supportedCommands"))

    async def on_content(message) -> None:
        inner = protobuf.extract_inner(message)
        bundle, player = path_of(inner)
        if player != DEFAULT_PLAYER:
            return
        for item in inner.contentItems:
            md = item.metadata
            if md.HasField("elapsedTime") and md.HasField("elapsedTimeTimestamp"):
                detector.on_show_clock(bundle, md.elapsedTime, md.elapsedTimeTimestamp)
            if frozen is not None and md.HasField("elapsedTime") and md.HasField("playbackRate"):
                frozen.on_clock(bundle, md.elapsedTime, md.playbackRate)

    original = mrp_protocol.MrpProtocol.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.listen_to(protobuf.SET_STATE_MESSAGE, on_set_state)
        self.listen_to(protobuf.UPDATE_CONTENT_ITEM_MESSAGE, on_content)

    mrp_protocol.MrpProtocol.__init__ = init

    def ticker() -> None:
        while True:
            time.sleep(5)
            detector.tick()
            if frozen is not None:
                frozen.tick()

    threading.Thread(target=ticker, daemon=True, name="ad-detect").start()
    apps = sorted(detector.apps | (frozen.apps if frozen is not None else set()))
    log(f"watching {', '.join(apps)}")
