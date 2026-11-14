"""Quit the Apple TV's front app through the app switcher (Companion HID + touch).

Paramount+ only opens a live game from a link when it starts cold: while it
runs (even in the background) the link ends in "fatal error" or a black
screen. Home Assistant cannot quit an Apple TV app, so this does what a person
does: Home twice (the app switcher, the front app centred), a swipe up on it,
Home. Checked on the TV 2026-11-14 (docs/JOURNAL.md).

`atv` is a connected pyatv AppleTV with a Companion session (atv_touch).
"""

from __future__ import annotations

import asyncio
from typing import Any

DOUBLE_PRESS_GAP_S = 0.1  # between the two Home presses: well inside a double-press
SWITCHER_S = 1.5  # for the app switcher to open
SWIPE = (500, 700, 500, 50)  # touch surface 0..1000: up from the lower middle
SWIPE_MS = 250
AFTER_SWIPE_S = 1.5


async def _home(comp: Any) -> None:
    from pyatv.protocols.companion.api import HidCommand

    await comp.api.hid_command(True, HidCommand.Home)
    await comp.api.hid_command(False, HidCommand.Home)


async def quit_front_app(atv: Any) -> None:
    from pyatv.const import Protocol

    comp = atv.remote_control.get(Protocol.Companion)
    if comp is None:
        raise RuntimeError("no Companion session")
    await _home(comp)
    await asyncio.sleep(DOUBLE_PRESS_GAP_S)
    await _home(comp)
    await asyncio.sleep(SWITCHER_S)
    await atv.touch.swipe(*SWIPE, SWIPE_MS)
    await asyncio.sleep(AFTER_SWIPE_S)
    await _home(comp)
