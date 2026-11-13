"""Tests for ServiceContext, Http, Store and the fake_context helper."""

import urllib.error

import pytest

from services.context import Store
from services.testing import fake_context


def test_http_get_records_bytes_and_status():
    ctx = fake_context(http={"example.com/a": "hello"})
    assert ctx.http.get("https://example.com/a", op="a") == "hello"
    st = ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]["http:a"]["total"]
    assert st["count"] == 1 and st["bytes_in"] == 5 and st["status"] == {"2xx": 1}


def test_http_get_json_and_post():
    ctx = fake_context(http={"x.com/j": '{"a": 1}', "x.com/p": "ok"})
    assert ctx.http.get_json("https://x.com/j", op="j") == {"a": 1}
    assert ctx.http.post("https://x.com/p", op="p", data={"k": "v"}) == "ok"
    ops = ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]
    assert ops["http:p"]["total"]["bytes_out"] == len(b'{"k": "v"}')


def test_http_unmatched_url_asserts():
    ctx = fake_context()
    with pytest.raises(AssertionError):
        ctx.http.get("https://nowhere.com/", op="n")


def test_http_error_is_recorded_and_reraised():
    err = urllib.error.HTTPError("u", 403, "no", {}, None)
    ctx = fake_context(http={"x.com": err})
    with pytest.raises(urllib.error.HTTPError):
        ctx.http.get("https://x.com/", op="x")
    assert ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]["http:x"]["total"]["status"] == {"4xx": 1}


def test_http_timeout_status():
    ctx = fake_context(http={"x.com": TimeoutError("t")})
    with pytest.raises(TimeoutError):
        ctx.http.get("https://x.com/", op="x")
    assert ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]["http:x"]["total"]["status"] == {"timeout": 1}


def test_http_never_logs_urls(capsys):
    ctx = fake_context(http={"x.com": RuntimeError("boom")})
    with pytest.raises(RuntimeError):
        ctx.http.get("https://x.com/?utsk=SECRET", op="x")
    assert "SECRET" not in capsys.readouterr().out


def test_store_roundtrip_and_size(tmp_path):
    s = Store(tmp_path / "svc")
    assert s.load("k", {"d": 1}) == {"d": 1}
    s.save("k", {"a": [1, 2]})
    assert Store(tmp_path / "svc").load("k") == {"a": [1, 2]}
    assert s.size_bytes() > 0
    db = s.sqlite("hist")
    db.execute("create table t(x)")
    db.commit()
    db.close()
    assert (tmp_path / "svc" / "hist.sqlite").exists()


def test_secret_reads_only_this_service(tmp_path):
    ctx = fake_context(name="bets", secrets={"bets": {"api_key": "k1"}, "other": {"api_key": "k2"}})
    assert ctx.secret("api_key") == "k1" and ctx.secret("nope") is None


def test_call_records_ha_metric_and_forwards():
    ctx = fake_context()
    ctx.call("media_player", "play_media", {"entity_id": "m"})
    assert ctx.fakes.calls == [("media_player", "play_media", {"entity_id": "m"})]
    assert "ha:media_player.play_media" in ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]


def test_state_and_log(capsys):
    ctx = fake_context(name="bets", states={"light.a": {"s": "on"}})
    assert ctx.state("light.a") == {"s": "on"} and ctx.state("light.b") is None
    ctx.log("hi")
    assert "bets: hi" in capsys.readouterr().out


def test_media_uses_service_config():
    ctx = fake_context(config={"lg": "media_player.lg", "atv": "media_player.atv",
                               "atv_source": "AppleTV", "atv_remote": "remote.atv"},
                       states={"media_player.lg": {"s": "on", "a": {"source": "HDMI"}}})
    assert ctx.media.ensure_on() is True
    ctx.media.select_atv_input()
    assert ctx.fakes.calls == [("media_player", "select_source",
                                {"entity_id": "media_player.lg", "source": "AppleTV"})]


def test_toast_shown_by_default():
    ctx = fake_context(name="bets")
    ctx.toast("a", "ch")
    assert ctx.fakes.toasts == [("a", "ch")]


def test_toast_suppressed_logs_once(capsys):
    ctx = fake_context(name="bets", toast_events={"soccer"})
    ctx.toast("a")
    ctx.toast("b")
    assert ctx.fakes.toasts == []
    assert capsys.readouterr().out.count("toast: bets not in toast.events; not shown") == 1


def test_toast_without_toaster_logs_once(capsys):
    ctx = fake_context(name="bets", toast_events=None)  # None: no toast config
    ctx.toast("a")
    ctx.toast("b")
    assert capsys.readouterr().out.count("toast: no toast config; not shown") == 1


def test_signals_and_pages_are_namespaced():
    ctx = fake_context(name="bets")
    ctx.changed()
    ctx.context_changed()
    ctx.redraw_in(60)
    ctx.show_page("slip")
    f = ctx.fakes
    assert (f.signals, f.context_signals, f.redraws, f.pages) == (1, 1, [60], ["bets.slip"])


def test_icon_prefers_service_folder(tmp_path):
    (tmp_path / "bets" / "icons").mkdir(parents=True)
    (tmp_path / "bets" / "icons" / "slip.png").write_bytes(b"x")
    ctx = fake_context(name="bets", icons_root=tmp_path)
    assert ctx.icon("slip") == str(tmp_path / "bets" / "icons" / "slip.png")
    assert ctx.icon("soccer_live__live") == "soccer_live__live"


def test_shared_proxy_times_calls():
    class Touch:
        def swipe(self, n):
            return n * 2

    ctx = fake_context(shared={"atv_touch": Touch()})
    assert ctx.shared("atv_touch").swipe(3) == 6 and ctx.shared("youtube") is None
    assert "shared:atv_touch" in ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]


def test_timed_records_op():
    ctx = fake_context()
    with ctx.timed("work"):
        pass
    assert "timed:work" in ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]


def test_sleep_advances_fake_time():
    ctx = fake_context(now=100.0)
    ctx.sleep(5)
    assert ctx.clock() == 105.0 and ctx.monotonic() == 105.0
