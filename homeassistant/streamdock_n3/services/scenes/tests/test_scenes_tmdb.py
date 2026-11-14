import json
from pathlib import Path

from services.scenes.tmdb import identify
from services.testing import fake_context

FIX = Path(__file__).parent / "fixtures"
SEARCH = (FIX / "tmdb_search.json").read_text(encoding="utf-8")
MOVIE_130 = (FIX / "tmdb_movie.json").read_text(encoding="utf-8")


def movie(**changes):
    data = json.loads(MOVIE_130)
    data.update(changes)
    return json.dumps(data)


MOVIE_82 = movie(id=138, runtime=82, release_date="1931-02-12", imdb_id="tt0021814")


def test_identify_picks_by_runtime():
    http = {"search/movie": SEARCH, "movie/1246049": MOVIE_130, "movie/138": MOVIE_82}
    ctx = fake_context("scenes", {}, http=http)
    film = identify(ctx.http, "k", "Dracula", 7800)
    assert (film.tmdb_id, film.year, film.runtime_s, film.norm) == (1246049, 2025, 7800, "dracula")
    assert film.director == "Director One" and film.imdb_id.startswith("tt")
    assert film.cast == ("Actor One", "Actor Two", "Actor Three", "Actor Four", "Actor Five")


def test_identify_picks_other_candidate_by_runtime():
    http = {"search/movie": SEARCH, "movie/1246049": MOVIE_130, "movie/138": MOVIE_82}
    film = identify(fake_context("scenes", {}, http=http).http, "k", "Dracula", 82 * 60 + 100)
    assert (film.tmdb_id, film.year) == (138, 1931)


def test_identify_no_match():
    ctx = fake_context("scenes", {}, http={"search/movie": '{"results": []}'})
    assert identify(ctx.http, "k", "Nope Nope", 7000) is None
    ctx = fake_context("scenes", {}, http={"search/movie": SEARCH, "movie/": MOVIE_82})
    assert identify(ctx.http, "k", "Dracula", 7800) is None      # runtime disagrees everywhere


def test_identify_without_runtime_needs_a_single_title_match():
    assert identify(fake_context("scenes", {}, http={"search/movie": SEARCH}).http, "k", "Dracula", None) is None
    one = json.dumps({"results": [json.loads(SEARCH)["results"][0]]})
    ctx = fake_context("scenes", {}, http={"search/movie": one, "movie/1246049": MOVIE_130})
    assert identify(ctx.http, "k", "Dracula", None).tmdb_id == 1246049


def test_identify_survives_malformed_results():
    bad = movie(runtime=None, release_date=None, imdb_id=None, credits=None)
    search = json.dumps({"results": [
        {"id": None, "title": "Dracula"}, "junk", {"id": 5, "title": None, "original_title": None},
        {"id": 7, "title": "Dracula", "original_title": "Dracula", "release_date": None}]})
    http = {"search/movie": search, "movie/7": bad}
    assert identify(fake_context("scenes", {}, http=http).http, "k", "Dracula", 7800) is None
    film = identify(fake_context("scenes", {}, http=http).http, "k", "Dracula", None)
    assert (film.runtime_s, film.year, film.director, film.cast, film.imdb_id) == (None, None, None, (), None)
    assert identify(fake_context("scenes", {}, http={"search/movie": "[]"}).http, "k", "Dracula", 7800) is None


def test_identify_encodes_title_and_key_in_the_url():
    ctx = fake_context("scenes", {}, http={"search/movie": '{"results": []}'})
    identify(ctx.http, "a&b", "Fast & Furious", 7000)
    url = ctx.fakes.urls[0]
    assert "query=Fast%20%26%20Furious" in url and "api_key=a%26b" in url
