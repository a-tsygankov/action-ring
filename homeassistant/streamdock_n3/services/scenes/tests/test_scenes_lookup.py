import sqlite3

from services.scenes import lookup as lookup_mod
from services.scenes.db import Db
from services.scenes.lookup import Lookup
from services.scenes.model import Film, Scene, SourceResult, norm_title
from services.testing import fake_context

KEY = "sekrit-key-123"
seen_cfg = {}


def hit(spans, meta=None, requests=2, category="nudity"):
    def source(fetch, film, cfg):
        return SourceResult("hit", tuple(Scene(a, b, category, "videoskip") for a, b in spans),
                            meta or {}, requests)
    return source


def miss():
    return lambda fetch, film, cfg: SourceResult("miss", requests=1)


def blocked():
    return lambda fetch, film, cfg: SourceResult("blocked", requests=1)


def raises(exc):
    def source(*args):
        raise exc
    return source


def recording_cfg(fetch, film, cfg):
    seen_cfg.clear()
    seen_cfg.update(cfg)
    return SourceResult("miss")


def default_identify(http, key, title, runtime_s):
    return Film(title, norm_title(title), runtime_s, 2026, tmdb_id=7)


_DEFAULT = object()


def make(sources, config=None, identify=default_identify, secrets=_DEFAULT):
    if secrets is _DEFAULT:
        secrets = {"test": {"tmdb_key": KEY}}
    ctx = fake_context(config=config or {}, secrets=secrets)
    logs = []
    ctx._log = logs.append
    calls = []

    def wrap(name, fn):
        def source(fetch, film, cfg):
            calls.append(name)
            return fn(fetch, film, cfg)
        return source

    wrapped = {n: wrap(n, f) for n, f in sources.items()}
    db = Db(sqlite3.connect(":memory:", check_same_thread=False))
    lk = Lookup(ctx, db, sources=wrapped, identify=identify)
    ctx.logs = logs
    ctx.db = db
    return lk, calls, ctx


def test_first_run_queries_then_cache_serves():
    lk, calls, ctx = make(sources={"videoskip": hit([(100, 110)]), "nsfwhen": miss()})
    assert lk.run("Tuner", 6600, "com.wbd.stream") == ((100, 110),)
    assert calls == ["videoskip", "nsfwhen"]
    assert lk.run("Tuner (2026)", 6600, "com.wbd.stream") == ((100, 110),) and len(calls) == 2   # cached
    assert "videoskip: hit, 1 scenes, 2 requests" in ctx.logs[0] + " " + " ".join(ctx.logs)
    assert any(l.endswith('"Tuner": 1 scenes') for l in ctx.logs)


def test_ttls():
    lk, calls, ctx = make(sources={"videoskip": miss(), "nsfwhen": blocked()})
    lk.run("Tuner", 6600, "a"); ctx.fakes.time.now += 2 * 86400; lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip", "nsfwhen", "nsfwhen"]            # blocked retried after 1 day, miss not
    ctx.fakes.time.now += 14 * 86400; lk.run("Tuner", 6600, "a")
    assert calls[-2:] == ["videoskip", "nsfwhen"]


def test_hit_ttl_and_config_ttl():
    lk, calls, ctx = make(sources={"videoskip": hit([(1, 2)])}, config={"hit_ttl_days": 10})
    lk.run("Tuner", 6600, "a"); ctx.fakes.time.now += 9 * 86400; lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip"]
    ctx.fakes.time.now += 2 * 86400; lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip", "videoskip"]


def test_one_source_failing_does_not_stop_the_other():
    lk, calls, ctx = make(sources={"videoskip": raises(RuntimeError("secret http://x")), "nsfwhen": hit([(1800, 2280)])})
    assert lk.run("Dracula", 7800, "a") == ((1800, 2280),)
    assert "test: videoskip: error (RuntimeError)" in ctx.logs
    assert not any("http" in l or "secret" in l for l in ctx.logs)


def test_error_keeps_old_scenes():
    state = {"fail": False}

    def flaky(fetch, film, cfg):
        if state["fail"]:
            raise ValueError("x")
        return SourceResult("hit", (Scene(5, 9, "sex", "videoskip"),))
    lk, calls, ctx = make(sources={"videoskip": flaky})
    assert lk.run("Tuner", 6600, "a") == ((5, 9),)
    state["fail"] = True
    ctx.fakes.time.now += 100 * 86400
    assert lk.run("Tuner", 6600, "a") == ((5, 9),)


def test_daily_cap():
    lk, calls, ctx = make(sources={"videoskip": miss()}, config={"max_lookups_per_day": 2})
    for t in ("A1", "B2", "C3"): lk.run(t, 6000, "a")
    assert len(calls) == 2
    assert any("daily lookup limit reached" in l for l in ctx.logs)


def test_tmdb_failure_and_missing_key_do_not_raise():
    lk, calls, ctx = make(sources={"videoskip": miss()}, identify=raises(TimeoutError("http://t?api_key=" + KEY)))
    assert lk.run("Tuner", 6600, "a") == ()
    assert "test: tmdb failed: TimeoutError" in ctx.logs
    assert calls == ["videoskip"]
    assert not any("http" in l or KEY in l for l in ctx.logs)
    lookup_mod._warned_no_key = False
    lk, calls, ctx = make(sources={"videoskip": miss()}, secrets=None)
    assert lk.run("Tuner", 6600, "a") == ()
    assert "test: no tmdb_key in secrets.json" in ctx.logs
    lk.run("Other", 6600, "a")
    assert sum("no tmdb_key" in l for l in ctx.logs) == 1


def test_unknown_source_logged_once_and_skipped():
    lk, calls, ctx = make(sources={"videoskip": miss()}, config={"sources": ["videoskip", "bogus"]})
    lk.run("Tuner", 6600, "a"); lk.run("Other", 6600, "a")
    assert calls == ["videoskip", "videoskip"]
    assert sum("bogus" in l for l in ctx.logs) == 1


def test_service_name_passed_to_source():
    lk, calls, ctx = make(sources={"videoskip": recording_cfg},
                          config={"app_services": {"com.wbd.stream": "max"}, "source_cfg": {"videoskip": {"k": 1}}})
    lk.run("Tuner", 6600, "com.wbd.stream")
    assert seen_cfg["service"] == "max" and seen_cfg["k"] == 1


def test_meta_fills_missing_film_fields():
    meta = {"year": 1999, "director": "D", "cast": ("A", "B"), "imdb_id": "tt1"}
    lk, calls, ctx = make(sources={"videoskip": hit([(1, 2)], meta)}, identify=lambda *a: None)
    lk.run("Tuner", 6600, "a")
    _, film = ctx.db.find_film("tuner", 6600)
    assert (film.year, film.director, film.cast, film.imdb_id) == (1999, "D", ("A", "B"), "tt1")


def test_meta_does_not_override_known():
    lk, calls, ctx = make(sources={"videoskip": hit([(1, 2)], {"year": 1900})})
    lk.run("Tuner", 6600, "a")
    assert ctx.db.find_film("tuner", 6600)[1].year == 2026


def test_empty_normalised_title_not_looked_up():
    lk, calls, ctx = make(sources={"videoskip": miss()})
    assert lk.run("Тюнер", 6600, "a") == () and calls == []
    assert len([l for l in ctx.logs]) == 1
    assert ctx.db.lookups_since(0) == 0


def test_categories_config():
    lk, calls, ctx = make(sources={"videoskip": hit([(1, 2)], category="suggestive")})
    assert lk.run("Tuner", 6600, "a") == ()
    lk, calls, ctx = make(sources={"videoskip": hit([(1, 2)], category="suggestive")},
                          config={"categories": ["suggestive"]})
    assert lk.run("Tuner", 6600, "a") == ((1, 2),)


def test_database_errors_do_not_raise():
    class Broken:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise sqlite3.OperationalError("disk http://x")
            return fail
    lk, calls, ctx = make(sources={"videoskip": miss()})
    lk._db = Broken()
    assert lk.run("Tuner", 6600, "a") == ()
    assert any("OperationalError" in l for l in ctx.logs)
    assert not any("http" in l for l in ctx.logs)
