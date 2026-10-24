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
