"""Short messages on the TV screen: webOS toasts on the LG.

The Apple TV has no way to show text from outside, but the LG draws a toast
(a small box in a corner for a few seconds) over any input, the Apple TV's
included, while the picture keeps playing. A "toast" config section turns it
on:

    "toast": {"lg": "media_player.lg_tv_2",
              "events": ["rating", "soccer", "ad", "sign_in"],
              "settle_s": 0.6}

`events` picks which of the app's own messages show (all by default); the
{"toast": "text"} key action always does. Messages on a `channel` (soccer
picks) are merged: only the last one shows, `settle_s` after the presses
stop, so a quick run of presses doesn't queue a toast each. Nothing is sent
while the TV is off.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from keyspec import StateLookup, variant

EVENTS = ("rating", "soccer", "ad", "sign_in")
MAX_LEN = 120  # longer text is cut (webOS wraps a toast to a few lines)

Call = Callable[[str, str, dict], Any]


def log(msg: str) -> None:
    print(f"toast: {msg}", flush=True)


class Toaster:
    def __init__(
        self,
        call: Call,
        state_of: StateLookup,
        cfg: dict,
        sleep: Callable[[float], None] = time.sleep,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._call = call
        self._state_of = state_of
        self.lg: str = cfg["lg"]
        events = cfg.get("events")
        self.events = set(EVENTS if events is None else events)
        self.settle_s = float(cfg.get("settle_s", 0.6))
        self._sleep = sleep
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._lock = threading.Lock()
        self._latest: dict[str, int] = {}  # channel -> newest message's number

    def notifier(self, event: str) -> Callable[..., None]:
        """show() for one of the app's events: a no-op unless `events` lists it."""
        if event not in self.events:
            return lambda message, channel=None: None
        return lambda message, channel=None: self.show(message, channel)

    def show(self, message: str, channel: str | None = None) -> None:
        """Toast `message` on the TV (in the background; never raises)."""
        message = " ".join(str(message).split())[:MAX_LEN]
        if not message:
            return
        if channel is None:
            self._spawn(lambda: self._send(message))
            return
        with self._lock:
            n = self._latest.get(channel, 0) + 1
            self._latest[channel] = n

        def settle() -> None:
            self._sleep(self.settle_s)
            with self._lock:
                if self._latest.get(channel) != n:
                    return  # a newer message on this channel replaced it
            self._send(message)

        self._spawn(settle)

    def _send(self, message: str) -> None:
        st = self._state_of(self.lg) or {}
        if variant(st.get("s")) != "on":
            log(f"TV off; not shown: {message}")
            return
        try:
            self._call("webostv", "command", {
                "entity_id": self.lg,
                "command": "system.notifications/createToast",
                "payload": {"message": message},
            })
            log(message)
        except Exception as err:
            log(f"failed ({type(err).__name__}: {err}): {message}")
