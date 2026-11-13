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
            {"icon": str(ICONS / "home.png"), "action": "like"},
            {"icon": str(ICONS / "back.png"), "action": "dislike"},
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
    assert len(drawn) == 6 and (4, str(ICONS / "home.png")) in drawn

    due, drawn = render_at(daemon, 104.01)
    assert [k for k, _ in drawn] == [4]  # only the dynamic key: its "3"
    assert drawn[0][1].endswith(".jpg") and Path(drawn[0][1]).exists()
    assert due == pytest.approx(104 + 1 / 3)
    assert press_at(daemon, 104.5) == ["like"]  # warning on: still the old function

    render_at(daemon, 104.34)
    render_at(daemon, 104.67)
    due, drawn = render_at(daemon, 105.01)
    assert drawn == [(4, str(ICONS / "back.png"))]
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
