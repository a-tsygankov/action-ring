from pathlib import Path

from services.scenes.fetch import Fetcher
from services.scenes.model import Film
from services.scenes.sources import SOURCES, nudidex
from services.scenes.sources.nsfwhen import lookup, parse_film, parse_search
from services.testing import fake_context

FIX = Path(__file__).parent / "fixtures"


def fx(name):
    return (FIX / name).read_text(encoding="utf-8")


DRACULA = Film("Dracula", "dracula", runtime_s=7800, tmdb_id=1246049)
FILM_SCENES = fx("nsfwhen_film_scenes.html")
SEARCH_ONE_HIT_103 = fx("nsfwhen_search.html").replace("/film/399", "/film/103")


def run(film, http, cfg=None):
    ctx = fake_context("scenes", {}, http=http)
    return lookup(Fetcher(ctx, gap_s=0), film, cfg or {}), ctx


def test_parse_film_with_scenes():
    cand, scenes = parse_film(FILM_SCENES)
    assert (cand.tmdb_id, cand.runtime_s) == (1246049, 7800)
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [
        (1800, 2280, "nudity"), (3240, 3720, "suggestive"), (4560, 4980, "sex")]
    assert all(s.note == "" and s.source == "nsfwhen" for s in scenes)


def test_parse_film_submitter_null_absent_or_object():
    for variant in ('"submitter":{"name":"x","email":"a@b.c"}', '"x":1'):
        cand, scenes = parse_film(FILM_SCENES.replace('"submitter":null', variant))
        assert len(scenes) == 3
        assert "a@b.c" not in repr((cand, scenes))


def test_parse_film_without_scenes_and_search():
    cand, scenes = parse_film(fx("nsfwhen_film_empty.html"))
    assert (cand.title, cand.year, cand.director, scenes) == ("Babylon", 2022, "Damien Chazelle", [])
    assert parse_search(fx("nsfwhen_search.html")) == ["https://nsfwhen.com/film/399"]


def test_search_without_films_is_miss_only_on_browse_page():
    browse = fx("nsfwhen_search.html").replace("/film/399", "/other/399")
    assert parse_search(browse) == []
    r, _ = run(DRACULA, {"/browse": browse})
    assert (r.status, r.requests) == ("miss", 1)
    r, _ = run(DRACULA, {"/browse": "<html><body>maintenance</body></html>"})
    assert r.status == "error"


def test_lookup():
    http = {"/browse": SEARCH_ONE_HIT_103, "/film/103": FILM_SCENES}
    r, ctx = run(DRACULA, http)
    assert (r.status, len(r.scenes), r.requests) == ("hit", 3, 2)
    assert r.meta["tmdb_id"] == 1246049 and r.meta["year"] == 2025 and r.meta["runtime_s"] == 7800
    assert "q=Dracula" in ctx.fakes.urls[0]
    wrong = Film("Dracula", "dracula", runtime_s=5000, year=1958)
    assert run(wrong, http)[0].status == "miss"
    r, _ = run(DRACULA, {"/browse": SEARCH_ONE_HIT_103, "/film/103": "<html>"})
    assert r.status == "error"                     # markup changed: reported, not raised


def test_lookup_matched_film_without_scenes_is_miss():
    babylon = Film("Babylon", "babylon", year=2022)
    r, _ = run(babylon, {"/browse": SEARCH_ONE_HIT_103, "/film/103": fx("nsfwhen_film_empty.html")})
    assert (r.status, r.requests, r.scenes) == ("miss", 2, ())


def test_lookup_blocked():
    r, _ = run(DRACULA, {"/browse": fx("challenge.html")})
    assert (r.status, r.requests) == ("blocked", 1)


def test_nudidex():
    ctx = fake_context("scenes", {}, http={})
    r = nudidex.lookup(Fetcher(ctx), DRACULA, {})
    assert (r.status, r.requests) == ("blocked", 0) and ctx.fakes.urls == []
    r = nudidex.lookup(Fetcher(fake_context("scenes", {}, http={"nudidex.com": fx("challenge.html")}), gap_s=0),
                       DRACULA, {"probe": True})
    assert (r.status, r.requests) == ("blocked", 1)
    r = nudidex.lookup(Fetcher(fake_context("scenes", {}, http={"nudidex.com": "<html>ok</html>"}), gap_s=0),
                       DRACULA, {"probe": True})
    assert (r.status, r.requests) == ("miss", 1)
    assert set(SOURCES) == {"videoskip", "nsfwhen", "nudidex"}


def test_requests_are_this_lookups_own_and_nudidex_too():
    ctx = fake_context("scenes", {}, http={"/browse": SEARCH_ONE_HIT_103, "/film/103": FILM_SCENES,
                                            "nudidex.com": "<html>ok</html>"})
    fetch = Fetcher(ctx, gap_s=0)
    fetch.count = 5                                # requests made earlier by other sources
    assert lookup(fetch, DRACULA, {}).requests == 2
    assert nudidex.lookup(fetch, DRACULA, {"probe": True}).requests == 1


def test_non_finite_scene_times_are_skipped():
    for bad in ("NaN", "Infinity", "-Infinity"):
        page = FILM_SCENES.replace('"start_time_seconds":1800', f'"start_time_seconds":{bad}', 1)
        assert page != FILM_SCENES
        cand, scenes = parse_film(page)
        assert [s.start_s for s in scenes] == [3240, 4560]
    cand, _ = parse_film(FILM_SCENES.replace('"runtime_minutes":130', '"runtime_minutes":Infinity', 1))
    assert cand.runtime_s is None
