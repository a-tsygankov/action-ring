from context import foreground_layout

LG = "media_player.lg_tv_2"
ATV = "media_player.vugitv_4k_vugitv_4k"
CFG = {
    "lg": LG,
    "atv": ATV,
    "atv_source": "Apple OTT",
    "youtube_apps": {"atv": ["com.google.ios.youtube"], "lg": ["youtube.leanback.v4"]},
    "lg_media_apps": ["com.webos.app.mediadiscovery"],
    "lg_idle_app_prefixes": ["com.webos.app.hdmi", "com.webos.app.livetv", "com.webos.app.home"],
}


def st(lg_state, lg_source=None, lg_app=None, atv_app=None):
    """State dicts shaped like ha_daemon.state_of: {"s": state, "a": attrs}."""
    states = {LG: {"s": lg_state, "a": {"source": lg_source, "app_id": lg_app}}}
    if atv_app is not None:
        states[ATV] = {"s": "playing", "a": {"app_id": atv_app}}
    return states


def layout(states, **kw):
    return foreground_layout(states.get, CFG, **kw)


def test_context_idle_when_states_empty():
    assert foreground_layout({}.get, CFG) == "idle"


def test_idle_when_lg_off_standby_or_unavailable():
    for s in ("off", "standby", None, "unavailable", "unknown"):
        assert layout(st(s, "Apple OTT", None, "com.cbsvideo.app")) == "idle"


def test_atv_input_with_empty_app_is_idle():
    assert layout(st("on", "Apple OTT", None, "")) == "idle"
    assert layout(st("on", "Apple OTT", None, None)) == "idle"
    assert layout(st("on", "Apple OTT", None)) == "idle"  # ATV entity missing


def test_atv_youtube_app():
    assert layout(st("on", "Apple OTT", None, "com.google.ios.youtube")) == "youtube"


def test_atv_streaming_app():
    assert layout(st("on", "Apple OTT", None, "com.netflix.Netflix")) == "streaming"


def test_unknown_atv_app_gets_streaming():
    assert foreground_layout(st("on", "Apple OTT", "com.webos.app.hdmi4", "com.plexapp.plex").get, CFG) == "streaming"


def test_live_game_only_on_atv_input():
    assert foreground_layout(st("on", "Apple OTT", None, "com.cbsvideo.app").get, CFG, watching_game=True) == "live_game"
    assert foreground_layout(st("on", "HDMI 1", "com.webos.app.hdmi1").get, CFG, watching_game=True) == "idle"


def test_atv_youtube_beats_live_game():
    assert layout(st("on", "Apple OTT", None, "com.google.ios.youtube"), watching_game=True) == "youtube"


def test_atv_empty_app_beats_live_game():
    assert layout(st("on", "Apple OTT", None, ""), watching_game=True) == "idle"


def test_lg_youtube_by_source():
    assert foreground_layout(st("on", "YouTube", None).get, CFG) == "youtube"


def test_lg_youtube_by_app_id():
    assert layout(st("on", "HDMI 1", "youtube.leanback.v4")) == "youtube"


def test_atv_youtube_id_ignored_on_lg_branch():
    assert layout(st("on", "HDMI 1", "com.google.ios.youtube")) == "lg_app"


def test_lg_media_app():
    assert layout(st("on", "HDMI 1", "com.webos.app.mediadiscovery")) == "lg_media"


def test_lg_idle_when_app_empty_or_idle_prefix():
    assert layout(st("on", "HDMI 1", None)) == "idle"
    assert layout(st("on", "HDMI 1", "")) == "idle"
    assert layout(st("on", "HDMI 1", "com.webos.app.hdmi1")) == "idle"
    assert layout(st("on", "Live TV", "com.webos.app.livetv")) == "idle"
    assert layout(st("on", "Home", "com.webos.app.home")) == "idle"


def test_lg_other_app():
    assert layout(st("on", "HDMI 1", "com.webos.app.browser")) == "lg_app"


def test_lg_playing_state_counts_as_on():
    assert layout(st("playing", "HDMI 1", "com.webos.app.browser")) == "lg_app"


# -- AtvApp: the Apple TV app that counts ------------------------------------------

from context import AtvApp  # noqa: E402

TV = "com.apple.TVWatchList"
YT = "com.google.ios.youtube"


def atv(state, app, ctype=None, cid=None):
    return {ATV: {"s": state, "a": {"app_id": app, "media_content_type": ctype, "media_content_id": cid}}}.get


def test_app_counts_once_seen_playing_and_through_a_pause():
    t = AtvApp({**CFG, "content_apps": [TV]})
    assert t.update(atv("idle", "com.netflix.Netflix")) is None  # menus
    assert t.update(atv("playing", "com.netflix.Netflix")) == "com.netflix.Netflix"
    assert t.update(atv("paused", "com.netflix.Netflix")) == "com.netflix.Netflix"
    assert t.update(atv("idle", "")) is None  # Home screen


def test_paused_fallback_after_another_app_does_not_count():
    # YouTube paused, the TV app plays a trailer, then the Apple TV reports
    # the paused YouTube again while the user is still in the TV app.
    t = AtvApp({**CFG, "content_apps": [TV]})
    assert t.update(atv("playing", YT)) == YT
    assert t.update(atv("paused", YT)) == YT
    assert t.update(atv("paused", TV, None)) is None  # trailer
    assert t.update(atv("paused", YT)) is None
    assert t.update(atv("playing", YT)) == YT  # back in YouTube, playing


def test_tv_app_counts_only_real_content():
    t = AtvApp({**CFG, "content_apps": [TV]})
    assert t.update(atv("playing", TV, None)) is None  # trailer
    assert t.update(atv("paused", TV, "video", "PREVIEW")) is None  # hero preview, never played
    assert t.update(atv("paused", TV, "music", "PREVIEW")) is None
    assert t.update(atv("paused", TV, "video", "A1")) is None  # title opened, not started
    assert t.update(atv("playing", TV, "video", "A1")) == TV
    assert t.update(atv("paused", TV, "video", "A1")) == TV
    assert t.update(atv("paused", TV, None)) is None  # trailer after the episode
    assert t.update(atv("paused", TV, "video", "A2")) is None  # next title must play first


def test_layout_uses_tracked_app():
    states = st("on", "Apple OTT", None, YT)
    assert layout(states, atv_app=None) == "idle"
    assert layout(states, atv_app=YT) == "youtube"
    assert layout(states, atv_app="com.netflix.Netflix") == "streaming"
