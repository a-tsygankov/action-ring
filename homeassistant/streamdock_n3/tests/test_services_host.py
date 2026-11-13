"""Tests for ServiceHost: namespacing, key presets, circuit breaker, hooks, metrics job."""

import json
import types

import pytest

from services import Service, action, condition
from services.host import ServiceHost, ServiceUnavailable


class Demo(Service):
    name = "demo"

    def __init__(self):
        self.went, self.states = [], []

    def entities(self):
        return {"sensor.a", "sensor.b"}

    def vars(self):
        return {"demo_n": "3", "n": "x"}

    def key(self, name, args):
        if name == "none":
            return None
        return {"icon": "demo_icon", "label": "demo",
                "action": {"service": "demo", "do": "go", "args": args}}

    def pages(self):
        return {"slip": {"keys": {}}}

    def context(self, layout):
        return "live_game" if layout == "streaming" else None

    def on_state(self, changed):
        self.states.append(changed)

    @action("go")
    def go(self, args):
        self.went.append(args)

    @condition("on")
    def on(self):
        return True


class Slow(Service):
    name = "slow"

    def key(self, name, args):
        raise RuntimeError("bad")

    @condition("c")
    def c(self):
        return True


class Bad(Service):
    name = "bad"

    def start(self, ctx):
        raise ValueError("no start")


@pytest.fixture
def host(tmp_path):
    made = []

    def make(sections, **kw):
        mods = {f"services.{c.name}": types.SimpleNamespace(SERVICE=c) for c in (Demo, Slow, Bad)}
        kw.setdefault("signal", lambda: None)
        kw.setdefault("spawn", lambda fn: fn())
        cfg = {"services": sections, "metrics": kw.pop("metrics", {})}
        h = ServiceHost.load(
            cfg, call=lambda *a: None, call_response=lambda *a, **k: None,
            state_of=lambda e: None, toaster=None, context_signal=lambda: None,
            show_page=lambda p: True, importer=mods.__getitem__,
            data_root=tmp_path / "data", secrets_path=tmp_path / "s.json",
            snapshot_path=kw.pop("snapshot_path", tmp_path / "metrics.json"), **kw)
        made.append(h)
        return h

    yield make
    for h in made:
        h.stop()


def test_names_pages_conditions_are_namespaced(host):
    h = host({"demo": {}}); h.start()
    assert h.names() == ["demo"] and "demo.slip" in h.pages()
    assert h.conditions()["demo.on"]() is True
    assert h.entities() == {"sensor.a", "sensor.b"}


def test_key_spec_merges_preset_overrides(host):
    h = host({"demo": {}}); h.start()
    spec = h.key_spec({"service_key": "demo.k", "args": {"i": 2}, "label": "X"})
    assert spec["label"] == "X" and spec["icon"] == "demo_icon"
    assert spec["action"] == {"service": "demo", "do": "go", "args": {"i": 2}}
    assert h.key_spec({"service_key": "demo.none"}) is None


def test_key_spec_unknown_service_raises_once_logged(host, capsys):
    h = host({}); h.start()
    for _ in range(2):
        with pytest.raises(ServiceUnavailable):
            h.key_spec({"service_key": "bets.slot"})
    assert capsys.readouterr().out.count("services: key bets.slot:") == 1


def test_vars_require_prefix(host, capsys):
    h = host({"demo": {}}); h.start()
    assert h.vars() == {"demo_n": "3"}
    h.vars()
    assert capsys.readouterr().out.count("services: demo var 'n' dropped") == 1


def test_context_first_non_none_wins(host):
    h = host({"demo": {}}); h.start()
    assert h.context("streaming") == "live_game" and h.context("idle") == "idle"


def test_breaker_trips_after_three_strikes_and_resets(host, capsys):
    t = [0.0]; h = host({"slow": {}}, monotonic=lambda: t[0]); h.start()
    for _ in range(3):
        with pytest.raises(Exception):
            h.key_spec({"service_key": "slow.k"})
    with pytest.raises(ServiceUnavailable):
        h.key_spec({"service_key": "slow.k"})
    assert h.conditions()["slow.c"]() is False
    assert "services: slow render calls off for 5 min" in capsys.readouterr().out
    t[0] = 299; h.tick(299)
    assert h.conditions()["slow.c"]() is False
    t[0] = 301; h.tick(301)
    assert "services: slow render calls back on" in capsys.readouterr().out
    assert h.conditions()["slow.c"]() is True


def test_strikes_expire_after_60s(host):
    t = [0.0]; h = host({"slow": {}}, monotonic=lambda: t[0]); h.start()
    for at in (0, 30, 61):
        t[0] = at
        with pytest.raises(ServiceUnavailable):
            h.key_spec({"service_key": "slow.k"})
    t[0] = 62
    assert h._is_open("slow") is False


def test_overrun_counts_as_strike(host, capsys):
    h = host({"demo": {}}, metrics={"slow_ms": {"cond": -1}}); h.start()
    cond = h.conditions()["demo.on"]
    assert [cond() for _ in range(4)] == [True, True, True, False]
    assert "services: demo render calls off for 5 min (cond:on over budget)" in capsys.readouterr().out


def test_failing_start_disables_only_that_service(host, capsys):
    h = host({"bad": {}, "demo": {}}); h.start()
    assert h.names() == ["demo"]
    assert "services: bad disabled (ValueError: no start)" in capsys.readouterr().out


def test_run_action_dispatches_and_ignores_unknown(host, capsys):
    h = host({"demo": {}}); h.start()
    h.run_action("demo", "go", {"a": 1})
    h.run_action("demo", "go")
    assert h._services["demo"].went == [{"a": 1}, {}]
    h.run_action("demo", "nope"); h.run_action("ghost", "go")
    out = capsys.readouterr().out
    assert "services: ignoring demo.nope" in out and "services: ignoring ghost.go" in out


def test_on_state_gets_intersection_only(host):
    h = host({"demo": {}}); h.start()
    h.on_state({"sensor.a", "sensor.z"})
    h.on_state({"sensor.z"})
    assert h._services["demo"].states == [{"sensor.a"}]


def test_tick_signals_due_redraws(host):
    signals = []; t = [0.0]
    h = host({"demo": {}}, signal=lambda: signals.append(1), monotonic=lambda: t[0]); h.start()
    h._ctx["demo"].redraw_in(60)
    h.tick(59.9); assert signals == []
    h.tick(60.0); assert signals == [1]
    h.tick(61.0); assert signals == [1]


def test_metrics_job_logs_and_writes_snapshot(host, tmp_path, capsys):
    t = [0.0]; snap = tmp_path / "m.json"
    h = host({"demo": {}}, monotonic=lambda: t[0], snapshot_path=snap,
             metrics={"log_every_s": 100}); h.start()
    h.key_spec({"service_key": "demo.k"})
    assert h._sched.run_pending(99) == 0 and not snap.exists()
    assert h._sched.run_pending(100) == 1
    out = capsys.readouterr().out
    assert "metrics: demo" in out
    data = json.loads(snap.read_text())
    assert "key:k" in data["services"]["demo"]["ops"] and "store_bytes" in data["services"]["demo"]
    assert h._sched.next_due() == 200


def test_measure_host_records_under_host(host, tmp_path):
    h = host({}); h.start()
    with h.measure_host("render"):
        pass
    assert "render" in h._metrics.snapshot({}, {})["services"]["host"]["ops"]
