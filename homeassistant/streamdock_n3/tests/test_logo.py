"""Service logo on a context page (logo.py)."""

from logo import LogoClock, app_icons, logo_for

LG = "media_player.lg"
CTX = {"lg": LG, "atv_source": "Apple OTT"}
CFG = {"default": "apple_tv_app", "sources": {"PS5 Game Console": "ps5"}}
PAGES = {
    "apps": {"keys": {
        "1": {"icon": "netflix", "action": {"launch": {"device": "atv", "app": "com.netflix.Netflix"}}},
        "2": {"icon": "home", "action": "ha-call remote.send_command"},
        "5": {"icon": "lg_media_player", "action": {"launch": {"device": "lg", "app": "com.webos.app.mediadiscovery"}}},
    }},
}
ICONS = app_icons(PAGES, {"youtube.leanback.v4": "youtube"})


def lg(source, app=None):
    return {LG: {"s": "on", "a": {"source": source, "app_id": app}}}.get


def test_app_icons_from_launch_keys_plus_extra():
    assert ICONS == {"com.netflix.Netflix": "netflix", "com.webos.app.mediadiscovery": "lg_media_player",
                     "youtube.leanback.v4": "youtube"}


def test_idle_shows_input_logo_else_default():
    assert logo_for("idle", lg("PS5 Game Console", "com.webos.app.hdmi3"), CTX, CFG, ICONS, None) == "ps5"
    assert logo_for("idle", lg("Apple OTT"), CTX, CFG, ICONS, None) == "apple_tv_app"
    assert logo_for("idle", {}.get, CTX, CFG, ICONS, None) == "apple_tv_app"  # TV state unknown


def test_app_pages_show_the_app():
    assert logo_for("streaming", lg("Apple OTT"), CTX, CFG, ICONS, "com.netflix.Netflix") == "netflix"
    assert logo_for("streaming", lg("Apple OTT"), CTX, CFG, ICONS, "com.unknown") is None
    assert logo_for("youtube", lg("YouTube", "youtube.leanback.v4"), CTX, CFG, ICONS, None) == "youtube"
    assert logo_for("lg_media", lg("Media", "com.webos.app.mediadiscovery"), CTX, CFG, ICONS, None) == "lg_media_player"


def test_clock_shows_for_show_s_every_gap():
    gaps = iter([10, 15, 12, 10])
    clock = LogoClock({"show_s": 2, "every_s": [10, 15]}, rand=lambda lo, hi: next(gaps))
    on = [t for t in range(0, 45) if clock.showing(float(t))]
    assert on == [10, 11, 25, 26, 37, 38]


def test_clock_fixed_gap_and_missed_ticks():
    clock = LogoClock({"show_s": 2, "every_s": 10}, rand=lambda lo, hi: lo)
    assert not clock.showing(0.0)
    assert clock.showing(10.0)
    assert not clock.showing(100.0)  # long gap without ticks: start over, no stale flash
    assert clock.showing(110.0)
