"""media_actions: power-first app launch and next track on the playing speaker."""

from media_actions import launch, next_playing

LG = "media_player.lg_tv_2"
ATV = "media_player.atv"
CFG = {
    "lg": LG,
    "atv": ATV,
    "atv_remote": "remote.atv",
    "atv_source": "Apple OTT",
    "power_on_wait": 25,
}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, domain, service, data):
        self.calls.append((domain, service, data))

    def names(self):
        return [(d, s) for d, s, _ in self.calls]


class FakeTime:
    """sleep() advances a fake clock; `on_sleep` can change state per poll."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = []
        self._on_sleep = on_sleep

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self._on_sleep:
            self._on_sleep(len(self.sleeps))

    def clock(self):
        return self.now


def test_launch_atv_switches_input_then_launches():
    rec, st = Recorder(), {LG: {"s": "on", "a": {"source": "HDMI 1"}}}
    t = FakeTime()
    assert launch(rec, st.get, CFG, "atv", "com.netflix.Netflix", sleep=t.sleep, clock=t.clock)
    assert rec.names() == [("media_player", "select_source"), ("media_player", "play_media")]
    assert rec.calls[0][2] == {"entity_id": LG, "source": "Apple OTT"}
    assert rec.calls[1][2] == {
        "entity_id": ATV,
        "media_content_type": "app",
        "media_content_id": "com.netflix.Netflix",
    }


def test_launch_atv_skips_input_switch_when_already_on_atv():
    rec, st = Recorder(), {LG: {"s": "on", "a": {"source": "Apple OTT"}}}
    assert launch(rec, st.get, CFG, "atv", "app.id")
    assert rec.names() == [("media_player", "play_media")]


def test_launch_powers_on_first_and_waits():
    rec, st = Recorder(), {LG: {"s": "off", "a": {}}}

    def wake(polls):
        if polls == 3:  # settle sleep + two polls
            st[LG] = {"s": "on", "a": {"source": "Apple OTT"}}

    t = FakeTime(wake)
    assert launch(rec, st.get, CFG, "atv", "app.id", sleep=t.sleep, clock=t.clock)
    assert rec.names() == [
        ("remote", "turn_on"),
        ("media_player", "turn_on"),
        ("media_player", "play_media"),
    ]
    assert rec.calls[0][2] == {"entity_id": "remote.atv"}
    assert rec.calls[1][2] == {"entity_id": ATV}
    # 2 s remote settle, then two 0.5 s polls
    assert t.sleeps == [2, 0.5, 0.5]


def test_launch_treats_unknown_lg_as_off():
    rec, st = Recorder(), {}  # LG entity not known at all
    t = FakeTime()
    assert not launch(rec, st.get, CFG, "atv", "app.id", sleep=t.sleep, clock=t.clock)
    assert rec.names()[:2] == [("remote", "turn_on"), ("media_player", "turn_on")]


def test_launch_gives_up_when_tv_never_wakes(capsys):
    rec, st = Recorder(), {LG: {"s": "off", "a": {}}}
    t = FakeTime()
    assert launch(rec, st.get, CFG, "atv", "app.id", sleep=t.sleep, clock=t.clock) is False
    assert ("media_player", "play_media") not in rec.names()
    assert t.now <= 2 + CFG["power_on_wait"] + 0.5
    assert "launch: TV did not turn on" in capsys.readouterr().out


def test_launch_lg_uses_launcher():
    rec, st = Recorder(), {LG: {"s": "on", "a": {"source": "HDMI 1"}}}
    assert launch(rec, st.get, CFG, "lg", "youtube.leanback.v4")
    assert rec.calls == [
        (
            "webostv",
            "command",
            {
                "entity_id": LG,
                "command": "system.launcher/launch",
                "payload": {"id": "youtube.leanback.v4"},
            },
        )
    ]


def test_next_playing_skips_group_members_and_idle():
    rec = Recorder()
    st = {
        "a": {"s": "paused", "a": {}},
        "b": {"s": "playing", "a": {"group_members": ["c", "b"]}},
        "c": {"s": "playing", "a": {"group_members": ["c", "b"]}},
    }
    assert next_playing(rec, st.get, ["a", "b", "c"]) == "c"
    assert rec.calls == [("media_player", "media_next_track", {"entity_id": "c"})]


def test_next_playing_none_when_nothing_plays():
    rec = Recorder()
    st = {"a": {"s": "idle", "a": {}}}
    assert next_playing(rec, st.get, ["a", "missing"]) is None
    assert rec.calls == []
