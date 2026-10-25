"""Knob engine: which scrub mode the horizontal knob uses, per app."""

from knob_accel import AppleTVKnobs


class Touch:
    def __init__(self, up=True):
        self.up = up

    def available(self):
        return self.up


def engine(cfg, state, touch=Touch()):
    base = {"remote": "remote.atv", "media_player": "media_player.atv", "scrub": "touch"}
    return AppleTVKnobs({**base, **cfg}, lambda *a: None, lambda e: state, touch)


def test_touch_is_the_default_while_playing():
    assert engine({}, {"s": "playing", "a": {"app_id": "com.apple.TVWatchList"}})._mode("horizontal") == "touch"


def test_listed_apps_scrub_by_seek():
    cfg = {"scrub_apps": {"com.wbd.stream": "seek"}}
    assert engine(cfg, {"s": "paused", "a": {"app_id": "com.wbd.stream"}})._mode("horizontal") == "seek"
    assert engine(cfg, {"s": "paused", "a": {"app_id": "com.netflix.Netflix"}})._mode("horizontal") == "touch"


def test_listed_apps_can_opt_into_touch_when_seek_is_the_default():
    cfg = {"scrub": "seek", "scrub_apps": {"com.apple.TVWatchList": "touch"}}
    assert engine(cfg, {"s": "playing", "a": {"app_id": "com.apple.TVWatchList"}})._mode("horizontal") == "touch"
    assert engine(cfg, {"s": "playing", "a": {"app_id": "com.wbd.stream"}})._mode("horizontal") == "seek"


def test_touch_falls_back_to_seek_without_a_connection():
    assert engine({}, {"s": "playing", "a": {}}, Touch(up=False))._mode("horizontal") == "seek"


def test_outside_playback_the_knob_navigates():
    assert engine({}, {"s": "idle", "a": {"app_id": "com.wbd.stream"}})._mode("horizontal") == "nav"
    assert engine({}, {"s": "playing", "a": {}})._mode("vertical") == "nav"
