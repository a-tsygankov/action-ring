"""Media key actions: power the TV on, launch an app, and next track.

Pure functions of an injected service `call` (as CoreAPI.call), a state lookup
and, for power-on, an injectable sleep/clock, so they are testable without Home
Assistant or the device.
"""

from __future__ import annotations

import time
from typing import Callable

from keyspec import StateLookup, variant

Call = Callable[[str, str, dict], None]

POLL_SECONDS = 0.5
REMOTE_SETTLE_SECONDS = 2  # lets a disconnected HA remote reconnect before turn_on


def _lg_on(state_of: StateLookup, lg: str) -> bool:
    st = state_of(lg)
    return variant(st.get("s") if st else None) == "on"


def ensure_on(
    call: Call,
    state_of: StateLookup,
    cfg: dict,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Power the TV on through the Apple TV if it is off; False if it stayed off."""
    lg = cfg["lg"]
    if _lg_on(state_of, lg):
        return True
    # Apple TV first: its HDMI-CEC wake-up powers the LG. The remote
    # turn_on also heals a disconnected HA remote.
    call("remote", "turn_on", {"entity_id": cfg["atv_remote"]})
    sleep(REMOTE_SETTLE_SECONDS)
    call("media_player", "turn_on", {"entity_id": cfg["atv"]})
    deadline = clock() + float(cfg.get("power_on_wait", 25))
    while not _lg_on(state_of, lg):
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)
    return True


def select_atv_input(call: Call, state_of: StateLookup, cfg: dict) -> None:
    """Switch the LG to the Apple TV input ("atv_source") unless it is already there."""
    lg, source = cfg["lg"], cfg["atv_source"]
    if ((state_of(lg) or {}).get("a") or {}).get("source") != source:
        call("media_player", "select_source", {"entity_id": lg, "source": source})


def launch(
    call: Call,
    state_of: StateLookup,
    cfg: dict,
    device: str,
    app: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Start `app` on the Apple TV ("atv") or the LG itself ("lg"); False if the TV stayed off."""
    lg, atv = cfg["lg"], cfg["atv"]
    if not ensure_on(call, state_of, cfg, sleep=sleep, clock=clock):
        print("launch: TV did not turn on", flush=True)
        return False
    if device == "atv":
        select_atv_input(call, state_of, cfg)
        call(
            "media_player",
            "play_media",
            {"entity_id": atv, "media_content_type": "app", "media_content_id": app},
        )
    elif device == "lg":
        call(
            "webostv",
            "command",
            {"entity_id": lg, "command": "system.launcher/launch", "payload": {"id": app}},
        )
    else:
        print(f"launch: unknown device {device!r}", flush=True)
        return False
    return True


def next_playing(call: Call, state_of: StateLookup, candidates: list[str]) -> str | None:
    """Skip to the next track on the first playing candidate that leads its group."""
    for entity in candidates:
        st = state_of(entity)
        if not st or st.get("s") != "playing":
            continue
        members = (st.get("a") or {}).get("group_members")
        if members and members[0] != entity:
            continue  # grouped follower: the leader owns the queue
        call("media_player", "media_next_track", {"entity_id": entity})
        return entity
    return None
