"""Dynamic keys through the daemon: the renderer draws each frame, a press
does what the key showed. The upstream streamdock_n3 package and the device
are stubbed (ha_daemon only uses a few of their functions)."""

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from tabs import TabEngine

ICONS = Path(__file__).resolve().parent.parent / "icons"
SPEC = {
    "dynamic": {
        "hold_s": 5,
        "phases": [
            {"icon": (ICONS / "home.png").as_posix(), "action": "like"},
            {"icon": (ICONS / "back.png").as_posix(), "action": "dislike"},
        ],
    },
}


class Device:
    def __init__(self):
        self.drawn: list[tuple[int, str]] = []

    def set_key_image(self, key, path):
        self.drawn.append((key, path))

    def refresh(self):
        pass


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    def module(name, **attrs):
        mod = ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    def make_icon(label, color, out):
        Image.new("RGB", (112, 112), color).save(out, "JPEG")

    ran: list = []
    pkg = module("streamdock_n3")
    for name, attrs in {
        "config": {},
        "daemon": {"apply_icons": lambda *a: None, "main": lambda: 0,
                   "run_actions": lambda actions, dry_run: ran.append(actions)},
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
    clock = SimpleNamespace(t=0.0)
    monkeypatch.setattr(hd, "time", SimpleNamespace(monotonic=lambda: clock.t))
    hd._device = Device()
    hd._engine = TabEngine({"context:youtube": {"keys": {"4": SPEC}}}, {})
    hd._engine.set_context("youtube", 100.0)
    yield SimpleNamespace(hd=hd, clock=clock, ran=ran)
    monkeypatch.delitem(sys.modules, "ha_daemon", raising=False)


def render_at(d, t):
    d.clock.t = t
    d.hd._device.drawn.clear()
    due = d.hd._render_pass()
    return due, list(d.hd._device.drawn)


def press_at(d, t):
    d.clock.t = t
    d.ran.clear()
    d.hd._run_actions({"tab_key": 4}, dry_run=False)
    return d.ran


def test_frames_drawn_and_presses_follow_the_screen(daemon, capsys):
    due, drawn = render_at(daemon, 100.0)
    assert due == pytest.approx(104.0)  # the warning starts 1 s before the switch
    assert len(drawn) == 6 and (4, (ICONS / "home.png").as_posix()) in drawn

    due, drawn = render_at(daemon, 104.01)
    assert [k for k, _ in drawn] == [4]  # only the dynamic key: its "3"
    assert drawn[0][1].endswith(".jpg") and Path(drawn[0][1]).exists()
    assert due == pytest.approx(104 + 1 / 3)
    assert press_at(daemon, 104.5) == ["like"]  # warning on: still the old function

    render_at(daemon, 104.34)
    render_at(daemon, 104.67)
    due, drawn = render_at(daemon, 105.01)
    assert drawn == [(4, (ICONS / "back.png").as_posix())]
    assert due == pytest.approx(109.0)
    assert press_at(daemon, 105.1) == ["like"]  # within grace of the swap
    assert press_at(daemon, 105.4) == ["dislike"]

    # The clock is past the next switch but the screen was not redrawn yet.
    assert press_at(daemon, 110.2) == ["dislike"]

    out = capsys.readouterr().out
    assert "render: 6 keys" in out and "render: 1 keys" not in out  # frames don't log


def test_the_same_frame_is_not_drawn_twice(daemon):
    render_at(daemon, 100.0)
    _, drawn = render_at(daemon, 102.0)
    assert drawn == []
    _, first = render_at(daemon, 104.1)
    _, again = render_at(daemon, 104.2)
    assert len(first) == 1 and again == []


def test_youtube_page_next_previous_and_like_dislike(daemon):
    import json

    preset = json.loads((ICONS.parent / "presets" / "tv_room.json").read_text())
    page = preset["tabs"]["pages"]["context:youtube"]
    hd = daemon.hd
    hd._engine = TabEngine({"context:youtube": page}, {})
    hd._engine.set_context("youtube", 100.0)
    commands, rated = [], {"like": False}
    hd._youtube = SimpleNamespace(command=commands.append, next_chapter=lambda: None,
                                  any_active=lambda: True, playing=lambda: True,
                                  subtitles_on=lambda: False, has_chapters=lambda: False)
    hd._rating = SimpleNamespace(rate=commands.append, liked=lambda: rated["like"], disliked=lambda: False)

    class Inline:
        def __init__(self, target, args=(), daemon=None):
            self.run = lambda: target(*args)

        def start(self):
            self.run()

    hd.threading = SimpleNamespace(Thread=Inline)

    def press(key, t):
        daemon.clock.t = t
        commands.clear()
        hd._run_actions({"tab_key": key}, dry_run=False)
        return commands

    render_at(daemon, 100.0)
    assert hd._drawn[2].icon.endswith("/next_video.png")
    assert hd._drawn[3].icon.endswith("/thumb_up__off.png")
    assert press(2, 101.0) == ["next"] and press(3, 101.0) == ["like"]
    rated["like"] = True
    render_at(daemon, 102.0)
    assert hd._drawn[3].icon.endswith("/thumb_up__on.png")  # liked: lit
    render_at(daemon, 106.1)  # 8 s phases, 1.8 s warning: none yet
    assert hd._drawn[2].warn is None and hd._drawn[3].warn is None
    render_at(daemon, 106.21)
    for key in (2, 3):  # both warn together, with a shrinking bar
        assert hd._drawn[key].warn is not None and hd._drawn[key].warn.style == "bar"
    render_at(daemon, 108.01)
    assert hd._drawn[2].icon.endswith("/prev_video.png")
    assert hd._drawn[3].icon.endswith("/thumb_down__off.png")
    assert press(2, 108.5) == ["previous"] and press(3, 108.5) == ["dislike"]


def test_a_press_shakes_the_pressed_thumb_then_the_turns_resume(daemon):
    import dynamic

    shaky = {"dynamic": {"hold_s": 5, "warn_s": 1, "phases": [
        {**SPEC["dynamic"]["phases"][0], "press": "shake"},
        {**SPEC["dynamic"]["phases"][1], "press": "shake"},
    ]}}
    hd = daemon.hd
    hd._engine = TabEngine({"context:youtube": {"keys": {"4": shaky}}}, {})
    hd._engine.set_context("youtube", 100.0)
    render_at(daemon, 100.0)

    # A press 0.4 s before the switch: the like thumb shakes, frame by frame.
    assert press_at(daemon, 104.6) == ["like"]
    due, drawn = render_at(daemon, 104.6)
    assert [k for k, _ in drawn] == [4] and hd._drawn[4].anim == dynamic.Anim("shake", 0)
    assert due == pytest.approx(104.6 + dynamic.PRESS_FRAME_S)
    seen = {0}
    t = 104.6
    while hd._drawn[4].anim is not None:
        t += dynamic.PRESS_FRAME_S
        render_at(daemon, t + 0.001)
        if hd._drawn[4].anim is not None:
            seen.add(hd._drawn[4].anim.step)
            assert hd._drawn[4].warn is None  # no warning over the shake
            assert hd._drawn[4].icon.endswith("/home.png")  # still the thumb that was pressed
    assert seen == set(range(len(dynamic.PRESS_ANIMS["shake"])))

    # Shaking past the switch at 105: a press meant the shaken phase; then dislike.
    assert hd._press_anims == {}
    assert hd._drawn[4].icon.endswith("/back.png")
    assert press_at(daemon, t + 0.5) == ["dislike"]


def test_a_press_during_the_shake_means_the_shaken_phase(daemon):
    shaky = {"dynamic": {"hold_s": 5, "warn_s": 1, "phases": [
        {**SPEC["dynamic"]["phases"][0], "press": "shake"},
        SPEC["dynamic"]["phases"][1],
    ]}}
    hd = daemon.hd
    hd._engine = TabEngine({"context:youtube": {"keys": {"4": shaky}}}, {})
    hd._engine.set_context("youtube", 100.0)
    render_at(daemon, 100.0)
    assert press_at(daemon, 104.85) == ["like"]
    render_at(daemon, 104.86)
    render_at(daemon, 105.05)  # the clock says dislike; the screen still shakes like
    assert hd._drawn[4].anim is not None and hd._drawn[4].icon.endswith("/home.png")
    assert press_at(daemon, 105.1) == ["like"]
