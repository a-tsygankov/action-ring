from pathlib import Path

from services.scenes.fetch import Fetcher
from services.scenes.model import Film, norm_title
from services.scenes.sources import SOURCES
from services.scenes.sources.videoskip import (lookup, synced_with_service, parse_film, parse_search, parse_skip_file,
                                               parse_skip_page)
from services.testing import fake_context

FIX = Path(__file__).parent / "fixtures"
FILM = Film("Tuner", "tuner", runtime_s=6600, year=2026)


def fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_skip_file():
    scenes, offsets = parse_skip_file(fx("videoskip_tuner.skp"))
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [(2943, 2946, "nudity")]
    assert offsets == {"amazon": 0}
    scenes, _ = parse_skip_file(fx("videoskip_weapons.skp"))     # no handling word still counts
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [(3286, 3329, "sex")]


def test_parse_skip_file_odd_content():
    text = "0:00:01\r\n\r\n1:05 --> 1:10.5\r\nSex 2 video\r\n\r\n0:20:00 --> 0:19:00\r\nNudity 1 video\r\n\r\n0:30:00 --> 0:30:05\r\nProfanity 2 audio\r\n"
    scenes, offsets = parse_skip_file(text)
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [(65, 70.5, "sex")]
    assert offsets == {}
    assert parse_skip_file("") == ([], {})
    ignored = "0:00:01\n\n0:10:00 --> 0:10:05\nSex 2 video // not this one\n\n0:11:00 --> 0:11:05\nSex 1\n"
    assert [s.start_s for s in parse_skip_file(ignored)[0]] == [660]


def test_parse_pages():
    assert parse_search(fx("videoskip_search_empty.html")) == []
    hits = parse_search(fx("videoskip_search_hits.html"))
    assert any(norm_title(c.title) == "tuner" and c.ref.startswith("/exchange/videos/") for c in hits)
    assert hits[0].year == 2026
    meta, skips = parse_film(fx("videoskip_film_skips.html"))
    assert skips and all(s.startswith("/exchange/skip/") for s in skips)
    assert meta == {"year": 2026, "imdb_id": "tt33296751"}
    assert parse_film(fx("videoskip_film_noskips.html"))[1] == []
    assert parse_skip_page(fx("videoskip_skip.html")) == {
        "synced_with": "Amazon Prime Video", "filters": "Sex and Nudity for Adults", "downloads": 124}


def _run(http, film=FILM, cfg=None):
    return lookup(Fetcher(fake_context("scenes", {}, http=http), gap_s=0), film, cfg or {})


def test_lookup_hit_miss_blocked_error():
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": fx("videoskip_film_skips.html"),
            "/download/": fx("videoskip_tuner.skp"), "/exchange/skip/": fx("videoskip_skip.html")}
    r = _run(http, cfg={"service": None})
    assert r.status == "hit" and r.scenes[0].start_s == 2943 and 3 <= r.requests <= 6
    assert r.scenes[0].source == "videoskip" and r.scenes[0].synced_with == "Amazon Prime Video"
    r = _run({"/search/": fx("videoskip_search_empty.html")})
    assert (r.status, r.requests) == ("miss", 1)
    r = _run({"/search/": fx("challenge.html")})
    assert r.status == "blocked"
    r = _run({"/search/": "<html><body>"})
    assert r.status == "error"
    r = _run({"/search/": TimeoutError()})
    assert r.status == "error"


def test_lookup_film_without_skip_files_is_miss():
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": fx("videoskip_film_noskips.html")}
    r = _run(http)
    assert (r.status, r.requests) == ("miss", 2)


def test_offsets_applied_only_for_listed_service():
    skp = '0:00:01\n\n0:10:00 --> 0:10:05\nSex 3\n\n{"amazon":0,"disneyplus":7}\n\ndata:image/jpeg;base64,AAAA\n'
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": fx("videoskip_film_skips.html"),
            "/download/": skp, "/exchange/skip/": fx("videoskip_skip.html")}
    r = _run(http, cfg={"service": "disneyplus"})
    assert (r.scenes[0].start_s, r.scenes[0].end_s) == (607, 612)
    for service in (None, "max"):                      # not listed, or no service: nothing applied
        r = _run(http, cfg={"service": service})
        assert (r.scenes[0].start_s, r.scenes[0].end_s) == (600, 605)


def test_registered():
    assert SOURCES["videoskip"] is lookup


def test_synced_with_loose_match():
    assert synced_with_service("Amazon Prime Video", "amazon")
    assert synced_with_service("Disney+", "disneyplus")
    assert synced_with_service("HBO Max", "max")
    assert not synced_with_service("Netflix", "amazon")
    assert not synced_with_service("Netflix", None)


def test_garbage_film_or_skip_page_is_error_not_miss():
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": "<html><body>x</body></html>"}
    assert _run(http).status == "error"
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": fx("videoskip_film_skips.html"),
            "/exchange/skip/": "<html><body>x</body></html>"}
    assert _run(http).status == "error"


def test_requests_are_this_lookups_own():
    http = {"/search/": fx("videoskip_search_hits.html"), "/exchange/videos/": fx("videoskip_film_skips.html"),
            "/download/": fx("videoskip_tuner.skp"), "/exchange/skip/": fx("videoskip_skip.html")}
    fetch = Fetcher(fake_context("scenes", {}, http=http), gap_s=0)
    fetch.count = 5                                # requests made earlier by other sources
    assert lookup(fetch, FILM, {}).requests == 4


def test_non_finite_offset_is_ignored():
    skp = '0:00:01\n\n0:10:00 --> 0:10:05\nSex 3\n\n{"amazon":NaN,"max":Infinity,"disneyplus":7}\n'
    assert parse_skip_file(skp)[1] == {"disneyplus": 7}
