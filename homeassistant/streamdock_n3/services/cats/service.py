"""cats: an easter egg. Round 1, 2, 3 pressed in a row (the core's key
combo, config "combo") run {"service": "cats", "do": "show"}: every key
turns into a cat's face for 10 s, the cats blink and stick their tongues out,
then the page that was there comes back. Pressing a cat plays a cat video in
YouTube on the Apple TV and ends it.

The video goes through the YouTube Lounge (ctx.shared("youtube")), which is
up only while YouTube is open on the screen and past its "Who's watching"
picker. When it is not up, YouTube is started on the Apple TV; if the session
is still not up `picker_after_s` later, the app is taken to be on its picker
and select is pressed once (the first profile has the focus), as the profiles
service does after a cold launch. The select is sent only while YouTube is
the Apple TV's app and nothing plays, the LG is on the Apple TV input and the
dock was not used since the cat press. The video is sent once the session
connects (`connect_wait_s`). A youtube.com URL handed to the Apple TV
(play_media "url") is refused (HTTP 500 from pyatv launch_app), so there is
no fallback.
"""

from __future__ import annotations

import random

from keyspec import variant
from services import Service, action

PAGE = "faces"
OPEN_S = 10  # how long the cats stay
CATS = 6  # one per LCD key
YOUTUBE_APP = "com.google.ios.youtube"
PICKER_AFTER_S = 6.0  # no session this long after the launch: on the profile picker
CONNECT_WAIT_S = 20.0  # after the select
POLL_S = 0.5
INPUT_GRACE_S = 1.0

# Each cat's face, blink and tongue phases, timed so the six do not move together.
# (open, blink, open, tongue) hold times in seconds; the cycle repeats.
TIMING = {
    1: (1.6, 0.25, 2.2, 1.0),
    2: (2.4, 0.25, 1.1, 0.9),
    3: (1.1, 0.3, 2.8, 1.1),
    4: (2.9, 0.25, 1.5, 0.8),
    5: (1.9, 0.3, 1.0, 1.2),
    6: (3.3, 0.25, 1.8, 0.9),
}


class CatsService(Service):
    name = "cats"

    def __init__(self, rng: "random.Random | None" = None):
        self.ctx = None
        self._rng = rng or random.Random()
        self._last: str | None = None
        self._icons: dict[str, str] = {}

    def start(self, ctx):
        self.ctx = ctx
        # ctx.icon checks the disk: resolve once here, not on the render path.
        self._icons = {f"cat_{i}{v}": ctx.icon(f"cat_{i}{v}")
                       for i in TIMING for v in ("", "_blink", "_tongue")}
        if not self._videos():
            raise ValueError('no "videos" configured')

    def entities(self):
        cfg = self.ctx.config
        return {e for e in (cfg.get("atv"), cfg.get("lg")) if e}

    def pages(self):
        return {PAGE: {
            "close_on_press": True,
            "open_s": OPEN_S,
            "keys": {str(i): {"service_key": "cats.face", "args": {"i": i}} for i in range(1, CATS + 1)},
        }}

    def key(self, name, args):
        if name != "face":
            return None
        i = int(args.get("i", 1))
        if i not in TIMING:
            return None
        open_s, blink_s, open2_s, tongue_s = TIMING[i]
        face, blink, tongue = (self._icons[f"cat_{i}{v}"] for v in ("", "_blink", "_tongue"))
        return {
            "icon": face,
            "action": {"service": "cats", "do": "play"},
            "dynamic": {"warn_s": 0, "grace_s": 0, "phases": [
                {"icon": face, "hold_s": open_s},
                {"icon": blink, "hold_s": blink_s},
                {"icon": face, "hold_s": open2_s},
                {"icon": tongue, "hold_s": tongue_s},
            ]},
        }

    def _videos(self) -> list[str]:
        videos = self.ctx.config.get("videos") if self.ctx else None
        return [v for v in videos if isinstance(v, str) and v] if isinstance(videos, list) else []

    def _pick(self) -> str:
        """A random video, not the one played last time (when there is a choice)."""
        videos = self._videos()
        choices = [v for v in videos if v != self._last] or videos
        self._last = self._rng.choice(choices)
        return self._last

    @action("show")
    def show(self, args):
        self.ctx.log("meow")
        self.ctx.show_page(PAGE)

    @action("play")
    def play(self, args):
        ctx = self.ctx
        t0 = ctx.monotonic()
        video = self._pick()
        screen = ctx.config.get("screen", "atv")
        yt = ctx.shared("youtube")
        if yt is not None and yt.connected(screen):
            self._send(yt, video, screen, "via the YouTube link")
            return
        if not ctx.media.launch("atv", YOUTUBE_APP):
            ctx.log("TV did not turn on")
            return
        if yt is None:
            ctx.log("YouTube started; no YouTube link (youtube.screens), video not sent")
            return
        if self._wait(yt, screen, self._num("picker_after_s", PICKER_AFTER_S)):
            self._send(yt, video, screen, "after starting YouTube")
            return
        why = self._no_click(t0)
        if why:
            ctx.log(f"no profile click ({why})")
        else:
            ctx.call("remote", "send_command", {"entity_id": ctx.config["atv_remote"], "command": "select"})
            ctx.log("no YouTube link yet: profile picker, select sent")
        if self._wait(yt, screen, self._num("connect_wait_s", CONNECT_WAIT_S)):
            self._send(yt, video, screen, "after the profile picker")
            return
        ctx.log(f"{video}: no YouTube link session, not sent")

    def _num(self, name: str, default: float) -> float:
        value = self.ctx.config.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default

    def _wait(self, yt, screen: str, seconds: float) -> bool:
        """Whether the Lounge session to `screen` comes up within `seconds`."""
        deadline = self.ctx.monotonic() + seconds
        while not yt.connected(screen):
            if self.ctx.monotonic() >= deadline:
                return False
            self.ctx.sleep(POLL_S)
        return True

    def _send(self, yt, video: str, screen: str, how: str) -> None:
        yt.play_video(video, screen)
        self.ctx.log(f"{video} {how}")

    def _no_click(self, t0: float) -> "str | None":
        """Why select must not be pressed now, or None (the profiles service's guards)."""
        ctx = self.ctx
        dock = ctx.shared("dock_input")
        if dock is not None and dock.last() > t0 + INPUT_GRACE_S:
            return "dock input"
        lg = ctx.state(ctx.config.get("lg") or "") or {}
        if variant(lg.get("s")) != "on" or (lg.get("a") or {}).get("source") != ctx.config.get("atv_source"):
            return "input changed"
        atv = ctx.state(ctx.config.get("atv") or "") or {}
        if (atv.get("a") or {}).get("app_id") != YOUTUBE_APP:
            return "YouTube not in front"
        if atv.get("s") in ("playing", "paused"):
            return "already playing"
        return None
