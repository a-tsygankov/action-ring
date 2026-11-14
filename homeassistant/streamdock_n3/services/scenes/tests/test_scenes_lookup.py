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
    lk, calls, ctx = make(sources={"nsfwhen": miss()}, identify=raises(TimeoutError("http://t?api_key=" + KEY)))
    assert lk.run("Tuner", 6600, "a") == ()
    assert "test: tmdb failed: TimeoutError" in ctx.logs
    assert calls == ["nsfwhen"]
    assert not any("http" in l or KEY in l for l in ctx.logs)
    lookup_mod._warned_no_key = False
    lk, calls, ctx = make(sources={"nsfwhen": miss()}, secrets=None)
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
    lk, calls, ctx = make(sources={"nsfwhen": hit([(1, 2)], meta)}, identify=lambda *a: None)
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


# --- identity is recorded and retried (I1) -----------------------------------

class Identify:
    """identify fake: returns (or raises) the next planned answer and counts calls."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    def __call__(self, http, key, title, runtime_s):
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer if answer is None else Film(title, norm_title(title), runtime_s, 2026, tmdb_id=7)


IDENTIFIED = "identified"


def tmdb_state(ctx):
    fid, _ = ctx.db.find_film("tuner", 6600)
    return ctx.db.lookup_state(fid).get("tmdb")


def test_identity_error_is_recorded_and_retried_after_blocked_ttl():
    ident = Identify(TimeoutError("x"), IDENTIFIED)
    lk, calls, ctx = make(sources={"videoskip": miss(), "nsfwhen": miss()}, identify=ident)
    lk.run("Tuner", 6600, "a")
    assert tmdb_state(ctx)[0] == "error" and ident.calls == 1
    assert calls == ["nsfwhen"]                                   # videoskip cannot match an unknown film
    assert "test: videoskip: skipped (film not identified)" in ctx.logs
    lk.run("Tuner", 6600, "a")
    assert ident.calls == 1                                       # not due yet
    ctx.fakes.time.now += 86400 + 1
    lk.run("Tuner", 6600, "a")
    assert ident.calls == 2 and tmdb_state(ctx)[0] == "hit"
    assert ctx.db.find_film("tuner", 6600)[1].tmdb_id == 7
    assert calls == ["nsfwhen", "videoskip", "nsfwhen"]           # identity gained: the miss is asked again
    ctx.fakes.time.now += 1000 * 86400
    lk.run("Tuner", 6600, "a")
    assert ident.calls == 2                                       # identified: never asked again


def test_identity_miss_is_retried_after_miss_ttl():
    ident = Identify(None)
    lk, calls, ctx = make(sources={"nsfwhen": miss()}, identify=ident)
    lk.run("Tuner", 6600, "a")
    assert tmdb_state(ctx)[0] == "miss"
    ctx.fakes.time.now += 13 * 86400; lk.run("Tuner", 6600, "a")
    assert ident.calls == 1
    ctx.fakes.time.now += 2 * 86400; lk.run("Tuner", 6600, "a")
    assert ident.calls == 2


def test_no_key_writes_no_row_and_identity_follows_once_a_key_exists():
    lookup_mod._warned_no_key = False
    ident = Identify(IDENTIFIED)
    lk, calls, ctx = make(sources={"videoskip": miss()}, identify=ident, secrets=None)
    lk.run("Tuner", 6600, "a")
    assert tmdb_state(ctx) is None and ident.calls == 0 and calls == []
    ctx._secrets_path.write_text('{"test": {"tmdb_key": "k"}}')
    lk.run("Tuner", 6600, "a")
    assert ident.calls == 1 and calls == ["videoskip"]


def test_identity_gained_reopens_miss_and_ambiguous_only():
    ident = Identify(TimeoutError("x"), IDENTIFIED)
    amb = lambda fetch, film, cfg: SourceResult("ambiguous")
    lk, calls, ctx = make(sources={"a": amb, "m": miss(), "h": hit([(1, 2)])}, identify=ident,
                          config={"sources": ["a", "m", "h"]})
    lk.run("Tuner", 6600, "a")
    ctx.fakes.time.now += 2 * 86400                               # miss/ambiguous TTL (14 days) not over
    lk.run("Tuner", 6600, "a")
    assert ident.calls == 2 and calls == ["a", "m", "h", "a", "m"]


def test_tmdb_row_does_not_count_against_the_daily_cap():
    ident = Identify(None, IDENTIFIED)                            # A1 unknown to TMDB, B2 identified
    lk, calls, ctx = make(sources={"videoskip": miss()}, identify=ident, config={"max_lookups_per_day": 1})
    lk.run("A1", 6000, "a")
    assert calls == [] and ctx.db.lookups_since(0) == 1           # only A1's tmdb row exists
    lk.run("B2", 6000, "a")
    assert calls == ["videoskip"]
    assert ctx.db.lookups_since(0, exclude=("tmdb",)) == 1


def test_tmdb_is_never_a_website_source():
    lk, calls, ctx = make(sources={"tmdb": miss(), "nsfwhen": miss()}, config={"sources": ["tmdb", "nsfwhen"]})
    lk.run("Tuner", 6600, "a")
    assert calls == ["nsfwhen"] and "test: unknown source tmdb" in ctx.logs
    assert tmdb_state(ctx)[0] == "hit"


def test_identified_by_year_alone_is_enough_for_videoskip():
    lk, calls, ctx = make(sources={"videoskip": miss()},
                          identify=lambda *a: Film("Tuner", "tuner", 6600, 2026))
    lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip"]


def test_each_source_logs_its_own_request_count():
    def fetching(n):
        def source(fetch, film, cfg):
            start = fetch.count
            fetch.count += n
            return SourceResult("miss", requests=fetch.count - start)
        return source
    lk, calls, ctx = make(sources={"videoskip": fetching(4), "nsfwhen": fetching(1)})
    lk.run("Tuner", 6600, "a")
    assert "test: videoskip: miss, 0 scenes, 4 requests" in ctx.logs
    assert "test: nsfwhen: miss, 0 scenes, 1 requests" in ctx.logs


def test_one_fetcher_per_lookup_keeps_the_site_gap_across_runs():
    seen = []
    lk, calls, ctx = make(sources={"nsfwhen": lambda fetch, film, cfg: seen.append(fetch) or SourceResult("miss")})
    lk.run("A1", 6600, "a"); lk.run("B2", 6600, "a")
    assert len(seen) == 2 and seen[0] is seen[1]


def test_cast_from_meta_is_stored_as_a_tuple_of_str():
    lk, calls, ctx = make(sources={"nsfwhen": hit([(1, 2)], {"cast": ["A", "B"]})})
    lk.run("Tuner", 6600, "a")
    film = lk._fold(Film("X", "x"), {"cast": ["A", "B"]})
    assert film.cast == ("A", "B") and isinstance(film.cast, tuple)
    hash(film)


def test_default_sources_leave_out_nudidex():
    lk, calls, ctx = make(sources={"videoskip": miss(), "nsfwhen": miss(), "nudidex": blocked()})
    lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip", "nsfwhen"]
