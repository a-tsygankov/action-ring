"""The key chord in the daemon, end to end with the cats service: the three
round buttons together show the cats for 10 s (their own actions never run),
then the page comes back; a cat press plays a video; presses that are not a
chord still do what they do. The upstream package and the device are stubbed
as in test_services_daemon.py; the chord timer is driven by hand."""

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from chord import Chord
from services.host import ServiceHost

ROUND = frozenset({7, 8, 9})
IDLE_KEYS = {"1": {"label": "A", "color": "#123456"}, "2": {"label": "B", "color": "#654321"}}
CONFIG = {
    "services": {"cats": {"atv": "media_player.atv", "atv_remote": "remote.atv", "lg": "media_player.lg",
                          "atv_source": "Apple TV", "videos": ["vid1", "vid2"]}},
    "tabs": {"timeouts": {"open": 5}, "round": {"7": ["apps"]},
             "pages": {"context:idle": {"keys": IDLE_KEYS},
                       "apps": {"keys": {"1": {"label": "Apps"}}}}},
}
BUTTONS = {"button.7.press": {"tab_round": 7}, "button.8.press": "echo home",
           "button.9.press": "echo power", "button.1.press": {"tab_key": 1}}


class Device:
    def __init__(self):
        self.drawn = []

    def set_key_image(self, key, path):
        self.drawn.append((key, path))

    def refresh(self):
        pass


class FakeYouTube:
    def __init__(self, connected=True):
        self.is_connected = connected
        self.played = []

    def connected(self, screen):
        return self.is_connected

    def play_video(self, video, screen):
        self.played.append((video, screen))


@pytest.fixture
def d(monkeypatch, tmp_path):
    def module(name, **attrs):
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    def make_icon(label, color, out):
        Image.new("RGB", (112, 112), color).save(out, "JPEG")

    ran = []
    pkg = module("streamdock_n3")
    for name, attrs in {
        "config": {},
        "daemon": {"apply_icons": lambda *a: None, "main": lambda: 0,
                   "run_actions": lambda actions, dry_run: ran.append(actions),
                   "event_key": lambda event: event},
        "icons": {"parse_color": lambda c, d: d if not c else (38, 38, 38),
                  "FALLBACK_COLORS": [(0, 0, 0)] * 6, "make_icon": make_icon},
        "paths": {"generated_key_dir": lambda: tmp_path},
    }.items():
        setattr(pkg, name, module(f"streamdock_n3.{name}", **attrs))
    module("websockets")
    module("websockets.sync")
    module("websockets.sync.client", connect=None)
    monkeypatch.delitem(sys.modules, "ha_daemon", raising=False)
    hd = importlib.import_module("ha_daemon")
    clock = SimpleNamespace(t=100.0)
    monkeypatch.setattr(hd, "time", SimpleNamespace(monotonic=lambda: clock.t))
    timers = []
    monkeypatch.setattr(hd, "_arm_chord_timer", lambda: timers.append(hd._chord.next_due))
    hd._device = Device()
    yt = FakeYouTube()
    hd._shared["youtube"] = yt
    hd._states["media_player.lg"] = {"s": "on", "a": {"source": "Apple TV"}}
    host = ServiceHost.load(
        CONFIG, call=lambda *a: None, call_response=lambda *a: None, state_of=hd.state_of,
        toaster=None, signal=hd._signal, context_signal=hd._update_context,
        show_page=lambda p: hd._engine.show(p, clock.t), shared=hd._shared,
        monotonic=lambda: clock.t, data_root=tmp_path / "data", secrets_path=tmp_path / "secrets.json",
        snapshot_path=tmp_path / "metrics.json", spawn=lambda fn: fn())
    hd._host = host
    hd._engine, _, _ = hd._make_engine(CONFIG)
    host.start()
    hd._engine.set_context("idle", clock.t)
    hd._chord, hd._chord_action = Chord(ROUND, 0.3), {"service": "cats", "do": "show"}
    hd._button_actions = BUTTONS
    yield SimpleNamespace(hd=hd, clock=clock, ran=ran, yt=yt, timers=timers, host=host)
    host.stop()
    monkeypatch.delitem(sys.modules, "ha_daemon", raising=False)


def press(d, button, dt=0.0):
    d.clock.t += dt
    return d.hd._event_key(f"button.{button}.press")


def test_event_key_hook_is_installed(d):
    assert sys.modules["streamdock_n3.daemon"].event_key is d.hd._event_key


def test_chord_shows_cats_for_ten_seconds_then_restores_the_page(d):
    hd = d.hd
    hd._render_pass()
    before = dict(hd._drawn)
    assert [press(d, 7), press(d, 9, 0.05), press(d, 8, 0.05)] == [None, None, None]
    assert d.ran == []  # neither "echo home" nor "echo power"
    assert hd._engine.visible == "cats.faces"  # not the Round 1 "apps" tab either
    hd._render_pass()
    icons = [hd._drawn[k].icon for k in range(1, 7)]
    assert [i.rsplit("/", 1)[1] for i in icons] == [f"cat_{k}.png" for k in range(1, 7)]
    assert hd._chord.next_due is None  # nothing left to replay
    d.clock.t += 9.9
    hd._engine.tick(d.clock.t)
    assert hd._engine.visible == "cats.faces"  # 10 s, not the 5 s "open" timeout
    d.clock.t += 0.2
    hd._engine.tick(d.clock.t)
    assert hd._engine.visible == "context:idle"
    hd._render_pass()
    assert hd._drawn == before  # the page as it was


def test_cats_blink_and_stick_out_tongues(d):
    hd = d.hd
    press(d, 7), press(d, 8), press(d, 9)
    seen = set()
    for _ in range(100):  # 10 s in 0.1 s steps
        hd._render_pass()
        seen |= {hd._drawn[k].icon.rsplit("/", 1)[1] for k in range(1, 7)}
        d.clock.t += 0.1
    for k in range(1, 7):
        assert {f"cat_{k}.png", f"cat_{k}_blink.png", f"cat_{k}_tongue.png"} <= seen


def test_cat_press_plays_a_cat_video_and_ends_cat_mode(d):
    hd = d.hd
    press(d, 7), press(d, 8), press(d, 9)
    d.clock.t += 2
    hd._run_actions({"tab_key": 4}, dry_run=False)
    assert d.yt.played and d.yt.played[0][0] in ("vid1", "vid2")
    assert d.yt.played[0][1] == "atv"
    assert hd._engine.visible == "context:idle"
    assert d.ran == []


def test_sequential_presses_run_each_buttons_own_action(d):
    hd = d.hd
    assert press(d, 8) is None  # held for the window
    assert d.timers == [pytest.approx(100.3)]
    d.clock.t += 0.31
    hd._chord_expire()
    assert d.ran == ["echo home"]
    assert press(d, 9, 0.5) is None
    d.clock.t += 0.31
    hd._chord_expire()
    assert d.ran == ["echo home", "echo power"]
    assert press(d, 7, 0.5) is None
    d.clock.t += 0.31
    hd._chord_expire()
    assert hd._engine.visible == "apps"  # Round 1's tab, late by the window
    assert d.yt.played == []


def test_two_of_three_replay_in_order(d):
    hd = d.hd
    press(d, 9)
    press(d, 8, 0.1)
    d.clock.t += 0.25
    hd._chord_expire()
    assert d.ran == ["echo power", "echo home"]
    assert hd._engine.visible == "context:idle"


def test_other_inputs_pass_and_flush_held_presses(d):
    hd = d.hd
    press(d, 8)
    assert hd._event_key("button.1.press") == "button.1.press"  # LCD key: dispatched by the SDK
    assert d.ran == ["echo home"]  # Round 2 first, in press order
    assert hd._event_key("button.8.release") == "button.8.release"
    assert hd._event_key("knob.3.left") == "knob.3.left"
    assert hd._event_key(None) is None


def test_without_a_chord_config_nothing_waits(d):
    d.hd._chord = None
    assert press(d, 7) == "button.7.press"
