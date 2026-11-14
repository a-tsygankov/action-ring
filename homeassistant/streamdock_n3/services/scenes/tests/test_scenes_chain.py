"""The real chain: SOURCES, Fetcher, Db, merge and the service, with fixture HTTP only."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from services.scenes import SERVICE
from services.scenes.db import Db
from services.scenes.lookup import Lookup
from services.scenes.model import Film, norm_title
from services.testing import fake_context

FIX = Path(__file__).parent / "fixtures"
ATV = "media_player.atv"
NOW = 1_000_000.0
SECRETS = {"scenes": {"tmdb_key": "k"}}


def fx(name):
    return (FIX / name).read_text(encoding="utf-8")


# Order matters: the first pattern found in the URL answers ("/download/" before "/exchange/skip/").
HTTP = {
    "/exchange/search/": fx("videoskip_search_hits.html"),
    "/exchange/videos/": fx("videoskip_film_skips.html"),
    "/download/": fx("videoskip_tuner.skp"),
    "/exchange/skip/": fx("videoskip_skip.html"),
    "nsfwhen.com/browse": fx("nsfwhen_search.html").replace("/film/399", "/other/399"),   # no film found
}


def identify(http, key, title, runtime_s):
    return Film(title, norm_title(title), runtime_s, 2026, tmdb_id=1)


def lookup(ctx):
    return Lookup(ctx, Db(sqlite3.connect(":memory:", check_same_thread=False)), identify=identify)


def test_lookup_runs_the_real_sources_once():
    ctx = fake_context("scenes", {"request_gap_s": 0}, http=HTTP, secrets=SECRETS, now=NOW)
    logs = []
    ctx._log = logs.append
    lk = lookup(ctx)
    assert lk.run("Tuner", 6600, "com.wbd.stream") == ((2943, 2946),)
    assert "scenes: videoskip: hit, 1 scenes, 4 requests" in logs     # each source's own count
    assert "scenes: nsfwhen: miss, 0 scenes, 1 requests" in logs
    urls = len(ctx.fakes.urls)
    assert urls == 5
    assert lk.run("Tuner", 6600, "com.wbd.stream") == ((2943, 2946),)
    assert len(ctx.fakes.urls) == urls                                 # served from the cache


def test_service_lights_ready_through_the_real_lookup():
    a = {"app_id": "com.wbd.stream", "media_title": "Tuner", "media_duration": 6600,
         "media_content_type": "movie", "media_position": 60,
         "media_position_updated_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat()}
    states = {ATV: {"s": "paused", "a": a}}
    ctx = fake_context("scenes", {"atv": ATV, "request_gap_s": 0}, states=states, http=HTTP,
                       secrets=SECRETS, now=NOW)
    svc = SERVICE(lookup=lookup(ctx))
    svc.start(ctx)
    svc.watch()
    ctx.fakes.time.now += 10
    svc.watch()
    assert svc.conditions()["ready"]() is True
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 2938})]
