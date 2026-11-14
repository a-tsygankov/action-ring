"""scenes: find the skip list of one film - cache first, then identity, then each website that is due."""

from __future__ import annotations

import dataclasses

from . import tmdb
from .db import Db
from .fetch import Fetcher
from .merge import merge
from .model import Film, SourceResult, norm_title
from .sources import SOURCES

_DAY_S = 86400
_META_FIELDS = ("year", "runtime_s", "director", "cast", "tmdb_id", "imdb_id")
_warned_no_key = False  # once per process


class Lookup:
    def __init__(self, ctx, db: Db, sources: dict | None = None, identify=tmdb.identify) -> None:
        self._ctx = ctx
        self._db = db
        self._sources = SOURCES if sources is None else sources
        self._identify = identify
        self._unknown_logged: set[str] = set()

    def run(self, title: str, runtime_s: int | None, app_id: str) -> tuple[tuple[float, float], ...]:
        try:
            return self._run(title, runtime_s, app_id)
        except Exception as e:  # never raises: a failed lookup is just "no scenes"
            self._ctx.log(f"lookup failed: {type(e).__name__}")
            return ()

    def _cfg(self, name: str, default):
        return self._ctx.config.get(name, default)

    def _run(self, title, runtime_s, app_id):
        ctx = self._ctx
        norm = norm_title(title)
        if not norm:
            ctx.log(f'"{title}": no usable title, not looked up')
            return ()
        found = self._db.find_film(norm, runtime_s)
        if found is None:
            film = self._identity(title, norm, runtime_s) or Film(title, norm, runtime_s)
            film_id = self._db.save_film(film, ctx.clock())
        else:
            film_id, film = found
        due = self._due(film_id)
        if due:
            if self._db.lookups_since(ctx.clock() - _DAY_S) >= self._cfg("max_lookups_per_day", 30):
                ctx.log("daily lookup limit reached")
            else:
                self._query(film_id, film, due, app_id)
        result = merge(self._db.scenes(film_id), self._cfg("categories", ["nudity", "sex"]),
                       self._cfg("join_gap_s", 20))
        ctx.log(f'"{title}": {len(result)} scenes')
        return result

    def _identity(self, title, norm, runtime_s) -> Film | None:
        global _warned_no_key
        ctx = self._ctx
        key = ctx.secret("tmdb_key")
        if not key:
            if not _warned_no_key:
                _warned_no_key = True
                ctx.log("no tmdb_key in secrets.json")
            return None
        try:
            return self._identify(ctx.http, key, title, runtime_s)
        except Exception as e:
            ctx.log(f"tmdb failed: {type(e).__name__}")
            return None

    def _due(self, film_id) -> list[str]:
        ctx = self._ctx
        state = self._db.lookup_state(film_id)
        now = ctx.clock()
        ttl_days = {"hit": self._cfg("hit_ttl_days", 90), "miss": self._cfg("miss_ttl_days", 14),
                    "ambiguous": self._cfg("miss_ttl_days", 14), "blocked": self._cfg("blocked_ttl_days", 1),
                    "error": self._cfg("blocked_ttl_days", 1)}
        due = []
        for name in self._cfg("sources", ["videoskip", "nsfwhen", "nudidex"]):
            if name not in self._sources:
                if name not in self._unknown_logged:
                    self._unknown_logged.add(name)
                    ctx.log(f"unknown source {name}")
                continue
            known = state.get(name)
            if known is not None:
                status, fetched_at = known
                if now - fetched_at < ttl_days.get(status, 0) * _DAY_S:
                    continue
            due.append(name)
        return due

    def _query(self, film_id, film, due, app_id) -> None:
        ctx = self._ctx
        fetch = Fetcher(ctx, self._cfg("request_gap_s", 3))
        for name in due:
            cfg = {**self._cfg("source_cfg", {}).get(name, {}),
                   "service": self._cfg("app_services", {}).get(app_id)}
            try:
                result = self._sources[name](fetch, film, cfg)
                ctx.log(f"{name}: {result.status}, {len(result.scenes)} scenes, {result.requests} requests")
            except Exception as e:
                result = SourceResult("error")
                ctx.log(f"{name}: error ({type(e).__name__})")
            if result.status == "hit":
                film = self._fold(film, result.meta)
            self._db.save_result(film_id, name, result, ctx.clock())

    def _fold(self, film: Film, meta: dict) -> Film:
        fill = {k: meta[k] for k in _META_FIELDS if meta.get(k) and not getattr(film, k)}
        if not fill:
            return film
        film = dataclasses.replace(film, **fill)
        self._db.save_film(film, self._ctx.clock())
        return film
