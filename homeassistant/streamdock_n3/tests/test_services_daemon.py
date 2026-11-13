"""The service host's hooks in the daemon: service_key slots render and
press through the host, {"service": ...} actions run there, and the host can
override the context layout. The upstream streamdock_n3 package and the device
are stubbed as in test_dynamic_render.py."""

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from keyspec import Look, icon_path
from services import Service, action
from services.host import ServiceHost
from tabs import TabEngine

SLOT = {"service_key": "demo.k", "args": {"i": 2}, "label": "X"}
PLAIN = {"label": "B", "color": "#123456"}


class Demo(Service):
    name = "demo"

    def __init__(self):
        self.went = []

    def key(self, name, args):
        return {"icon": "demo_icon", "label": "demo",
                "action": {"service": "demo", "do": "go", "args": args}}

    def pages(self):
        return {"slip": {"keys": {"1": {"service_key": "demo.k"}}}}

    def vars(self):
        return {"demo_n": "3"}

    def context(self, layout):
        return "live_game" if layout == "streaming" else None

    @action("go")
    def go(self, args):
        self.went.append(args)


class Device:
    def __init__(self):
        self.drawn: list[tuple[int, str]] = []

    def set_key_image(self, key, path):
        self.drawn.append((key, path))

    def refresh(self):
        pass


@pytest.fixture
def daemon_without_host(monkeypatch, tmp_path):
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
    clock = SimpleNamespace(t=100.0)
    monkeypatch.setattr(hd, "time", SimpleNamespace(monotonic=lambda: clock.t))
    hd._device = Device()
    hd._engine = TabEngine({"context:idle": {"keys": {"1": SLOT, "2": PLAIN}}}, {})
    hd._engine.set_context("idle", 100.0)
    yield SimpleNamespace(hd=hd, clock=clock, ran=ran, tmp_path=tmp_path)
    monkeypatch.delitem(sys.modules, "ha_daemon", raising=False)


@pytest.fixture
def daemon_with_demo(daemon_without_host):
    d = daemon_without_host
    demo = Demo()
    host = ServiceHost.load(
        {"services": {"demo": {}}}, call=lambda *a: None, call_response=lambda *a: None,
        state_of=d.hd.state_of, toaster=None, signal=d.hd._signal,
        context_signal=d.hd._update_context, show_page=lambda p: True,
        importer={"services.demo": SimpleNamespace(SERVICE=lambda: demo)}.__getitem__,
        data_root=d.tmp_path / "data", secrets_path=d.tmp_path / "secrets.json",
        snapshot_path=d.tmp_path / "metrics.json", spawn=lambda fn: fn())
    host.start()
    d.hd._host = host
    d.demo, d.host = demo, host
    yield d
    host.stop()


def test_service_key_slot_renders_service_look(daemon_with_demo):
    d = daemon_with_demo
    d.hd._render_pass()
    assert d.hd._drawn[1] == Look(icon_path("demo_icon"), "X", None)  # the slot's label wins
    assert d.hd._drawn[2] == Look(None, "B", "#123456")


def test_unavailable_service_key_shows_error_look(daemon_without_host, capsys):
    d = daemon_without_host
    d.hd._render_pass()
    d.hd._render_pass()
    assert d.hd._drawn[1] is d.hd.ERROR_LOOK
    assert d.hd._drawn[2] == Look(None, "B", "#123456")  # the other keys are drawn
    assert len(d.hd._device.drawn) == 6
    assert capsys.readouterr().out.count("render: key 1: ServiceUnavailable: no services") == 1


def test_press_on_service_key_runs_service_action(daemon_with_demo, monkeypatch):
    d = daemon_with_demo
    calls = []
    real = d.host.run_action
    monkeypatch.setattr(d.host, "run_action", lambda *a: (calls.append(a), real(*a)))
    d.hd._run_actions({"tab_key": 1}, dry_run=False)
    assert calls == [("demo", "go", {"i": 2})]
    assert d.demo.went == [{"i": 2}] and d.ran == []


def test_press_on_unknown_service_key_is_ignored(daemon_without_host, capsys):
    d = daemon_without_host
    d.hd._run_actions({"tab_key": 1}, dry_run=False)  # no exception into the SDK
    assert "tabs: ServiceUnavailable: no services" in capsys.readouterr().out
    assert d.ran == []


def test_service_action_branch(daemon_with_demo, capsys):
    d = daemon_with_demo
    d.hd._run_actions({"service": "demo", "do": "go"}, dry_run=False)
    d.hd._run_actions({"service": "demo", "do": "go", "args": {"a": 1}}, dry_run=True)
    assert d.demo.went == [{}] and d.ran == []
    assert "service (dry run): demo.go" in capsys.readouterr().out


def test_service_action_without_host_is_ignored(daemon_without_host, capsys):
    d = daemon_without_host
    d.hd._run_actions({"service": "demo", "do": "go"}, dry_run=False)
    assert "service: no services" in capsys.readouterr().out and d.ran == []


def test_context_hook_applies(daemon_with_demo):
    d = daemon_with_demo
    hd = d.hd
    hd._engine = TabEngine({"context:streaming": {"keys": {}}, "context:live_game": {"keys": {}}}, {})
    hd._ctx_cfg = {"lg": "media_player.lg", "atv": "media_player.atv", "atv_source": "Apple TV"}
    hd._atv_app = SimpleNamespace(update=lambda state_of, now: "com.app", pending=False)
    hd._states["media_player.lg"] = {"s": "on", "a": {"source": "Apple TV"}}
    hd._update_context()
    assert hd._engine.visible == "context:live_game"  # streaming, overridden by the service


def test_make_engine_adds_service_pages_and_vars(daemon_with_demo):
    hd = daemon_with_demo.hd
    _, pages, _ = hd._make_engine({"tabs": {"pages": {"context:idle": {"keys": {}}}}})
    assert set(pages) == {"context:idle", "demo.slip"}
    _, pages, _ = hd._make_engine({"keys": {}})  # no tabs: the config's keys and the service pages
    assert set(pages) == {"context:idle", "demo.slip"}
    assert hd._vars() == {"demo_n": "3"}


def test_top_level_soccer_config_logs_that_it_moved(daemon_without_host, capsys):
    hd = daemon_without_host.hd
    hd._warn_legacy_config({"soccer": {"poll_s": 60}, "services": {"soccer": {}}})
    hd._warn_legacy_config({"services": {"soccer": {}}})
    out = capsys.readouterr().out
    assert out == 'soccer: top-level "soccer" config is ignored; it moved to services.soccer\n'
