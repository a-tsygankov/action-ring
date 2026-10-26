"""knob_accel / atv_touch: a stale touch connection falls back to seek and reconnects.

From PR #12 (10-24 TV checks): after the Apple TV sleeps, the Companion
session still looks connected but every touch event fails."""

import pytest

from knob_accel import AppleTVKnobs

PLAYER = "media_player.atv"
CFG = {"remote": "remote.atv", "media_player": PLAYER, "scrub": "touch"}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, domain, service, data):
        self.calls.append((domain, service, data))


class StaleTouch:
    """Connected as far as pyatv knows, but every event fails."""

    def __init__(self):
        self.connected = True
        self.dropped = []

    def available(self):
        return self.connected

    def action(self, x, y, mode, timeout=2.0):
        raise RuntimeError("Send event failed")

    def drop(self, reason):
        self.dropped.append(reason)
        self.connected = False


def paused_at(pos):
    st = {PLAYER: {"s": "paused", "a": {"supported_features": 2, "media_position": pos}}}
    return st.get


def touch_batch(units):
    return {"units": units, "first": 0.0, "last": 0.0, "mode": "touch"}


def test_failed_touch_drops_the_connection_and_seeks_instead():
    call, touch = Recorder(), StaleTouch()
    knobs = AppleTVKnobs(CFG, call, paused_at(1000), touch)
    knobs._send("horizontal", touch_batch(-30))
    assert touch.dropped == ["Send event failed"]
    # 30 touch units at the slow tier (12 per detent) = 2.5 detents = 25 s of seek.
    assert call.calls == [("media_player", "media_seek", {"entity_id": PLAYER, "seek_position": 975})]


def test_next_turn_after_a_drop_uses_seek_mode():
    call, touch = Recorder(), StaleTouch()
    knobs = AppleTVKnobs(CFG, call, paused_at(1000), touch)
    knobs._send("horizontal", touch_batch(12))
    assert knobs._mode("horizontal") == "seek"
    assert knobs._finger_down is False


def test_drop_closes_the_pyatv_handle_so_supervise_reconnects():
    pytest.importorskip("pyatv")
    from atv_touch import AppleTVTouch

    class Loop:
        def call_soon_threadsafe(self, fn, *args):
            fn(*args)

    class Handle:
        closed = False

        def close(self):
            Handle.closed = True

    touch = object.__new__(AppleTVTouch)
    touch._loop, touch._atv = Loop(), Handle()
    touch.drop("Send event failed")
    assert touch.available() is False
    assert Handle.closed is True
