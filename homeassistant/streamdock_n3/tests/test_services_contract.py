"""Tests for the service contract: decorators, collectors and load()."""

import types

from services import Service, action, condition, job, load


class Demo(Service):
    name = "demo"

    @job("poll", first_in=5)
    def poll(self):
        return 30.0

    @action("go")
    def go(self, args):
        self.went = args

    @condition("on")
    def on(self):
        return True


def test_collectors_find_decorated_methods():
    d = Demo()
    assert [(j.name, j.first_in, j.long_running) for j in d.jobs()] == [("poll", 5, False)]
    assert d.jobs()[0].fn() == 30.0
    d.actions()["go"]({"x": 1})
    assert d.went == {"x": 1}
    assert d.conditions()["on"]() is True


def test_long_running_flag():
    class L(Service):
        name = "l"

        @job("run", long_running=True)
        def run(self):
            return None

    assert [(j.first_in, j.long_running) for j in L().jobs()] == [(0.0, True)]


def test_defaults_are_empty():
    s = Service()
    assert s.entities() == set() and s.vars() == {} and s.pages() == {}
    assert s.key("k", {}) is None and s.context("idle") is None
    s.start(None)
    s.on_state({"a"})
    s.stop()


def test_load_order_enabled_and_failures():
    mods = {"services.demo": types.SimpleNamespace(SERVICE=Demo)}

    def importer(n):
        if n not in mods:
            raise ImportError(n)
        return mods[n]

    lines = []
    got = load({"demo": {}, "off": {"enabled": False}, "ghost": {}, "Bad-Name": {}, "junk": 3},
               importer=importer, log=lines.append)
    assert [s.name for s in got] == ["demo"]
    assert any(l.startswith("services: ghost disabled") for l in lines)
    assert any(l.startswith("services: Bad-Name disabled") for l in lines)
    assert not any("off" in l for l in lines)


def test_load_missing_service_attr():
    lines = []
    assert load({"demo": {}}, importer=lambda n: types.SimpleNamespace(), log=lines.append) == []
    assert lines[0].startswith("services: demo disabled")


def test_load_rejects_name_mismatch():
    class Other(Service):
        name = "other"

    lines = []
    assert load({"demo": {}}, importer=lambda n: types.SimpleNamespace(SERVICE=Other), log=lines.append) == []
    assert "disabled" in lines[0]
