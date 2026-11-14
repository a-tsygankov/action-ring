"""cats: an easter egg. Round 1, 2, 3 pressed in a row (the core's key
combo, config "combo") run {"service": "cats", "do": "show"}: every key
turns into a cat's face for 10 s, the cats blink and stick their tongues out,
then the page that was there comes back. Pressing a cat plays a cat video in
YouTube on the Apple TV and ends it.

The video goes through the YouTube Lounge (ctx.shared("youtube")) when a
session to the configured screen is up; otherwise the YouTube app is started
on the Apple TV and the video is sent once its session connects. If it never
does, the video URL is handed to the Apple TV (play_media, type "url").
"""

from __future__ import annotations

import random

from services import Service, action

PAGE = "faces"
OPEN_S = 10  # how long the cats stay
CATS = 6  # one per LCD key
YOUTUBE_APP = "com.google.ios.youtube"
CONNECT_WAIT_S = 20
POLL_S = 0.5

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
        video = self._pick()
        screen = self.ctx.config.get("screen", "atv")
        yt = self.ctx.shared("youtube")
        if yt is not None and yt.connected(screen):
            yt.play_video(video, screen)
            self.ctx.log(f"{video} via the YouTube link")
            return
        if not self.ctx.media.launch("atv", YOUTUBE_APP):
            self.ctx.log("TV did not turn on")
            return
        if yt is not None:
            deadline = self.ctx.monotonic() + float(self.ctx.config.get("connect_wait_s", CONNECT_WAIT_S))
            while self.ctx.monotonic() < deadline:
                if yt.connected(screen):
                    self.ctx.sleep(1)  # the app is up; give the session a moment
                    yt.play_video(video, screen)
                    self.ctx.log(f"{video} via the YouTube link, after starting YouTube")
                    return
                self.ctx.sleep(POLL_S)
        self.ctx.call("media_player", "play_media", {
            "entity_id": self.ctx.config["atv"],
            "media_content_type": "url",
            "media_content_id": f"https://www.youtube.com/watch?v={video}",
        })
        self.ctx.log(f"{video} as a URL (no YouTube link session)")
