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


def test_scrub_apps_entries_may_be_objects():
    cfg = {"scrub_apps": {"com.google.ios.youtube": {"pause": False}, "com.example": {"mode": "seek"}}}
    yt = engine(cfg, {"s": "playing", "a": {"app_id": "com.google.ios.youtube"}})
    assert yt._mode("horizontal") == "touch"
    assert yt._pause_before_touch("com.google.ios.youtube") is False
    assert yt._pause_before_touch("com.wbd.stream") is True
    assert engine(cfg, {"s": "playing", "a": {"app_id": "com.example"}})._mode("horizontal") == "seek"


class RecordingTouch(Touch):
    def __init__(self):
        super().__init__()
        self.events = []

    def action(self, x, y, mode):
        self.events.append((x, y, mode))


def test_finger_stays_above_the_wrap_zone_and_regrips_in_the_middle():
    """tvOS misreads touch x below 256 (pyatv's one-byte OPACK form), so the
    finger starts at the middle of 300..850 and never goes below 300."""
    calls = []
    touch = RecordingTouch()
    cfg = {"touch_interval": 0, "touch_pause_delay": 0}
    eng = engine(cfg, {"s": "paused", "a": {"app_id": "com.wbd.stream"}}, touch)
    eng._call = lambda *a: calls.append(a)
    eng._finger_drag(-400)
    xs = [x for x, _, _ in touch.events]
    assert touch.events[0] == (575, 500, "press")
    assert min(xs) >= 300
    # 575 -> 300 (275 units), re-grip at 575, then the remaining 125 -> 450
    assert ("release" in [m for _, _, m in touch.events]) and touch.events[-1][0] == 450
    assert eng._finger_x == 450
    assert calls == []  # already paused: no pause call


def test_youtube_is_not_paused_before_the_first_touch():
    calls = []
    touch = RecordingTouch()
    cfg = {"touch_interval": 0, "touch_pause_delay": 0, "scrub_apps": {"com.google.ios.youtube": {"pause": False}}}
    eng = engine(cfg, {"s": "playing", "a": {"app_id": "com.google.ios.youtube"}}, touch)
    eng._call = lambda *a: calls.append(a)
    eng._finger_drag(-12)
    assert calls == []
    other = engine(cfg, {"s": "playing", "a": {"app_id": "com.wbd.stream"}}, RecordingTouch())
    other._call = lambda *a: calls.append(a)
    other._finger_drag(-12)
    assert calls == [("media_player", "media_pause", {"entity_id": "media_player.atv"})]
