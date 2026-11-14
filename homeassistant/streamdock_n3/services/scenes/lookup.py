"""scenes: find the skip list of one film - cache first, then identity (retried until known), then each
website that is due."""

from __future__ import annotations

import dataclasses

from . import tmdb
from .db import Db
from .fetch import Fetcher
from .merge import merge
from .model import Film, SourceResult, norm_title
from .sources import NEEDS_IDENTITY, SOURCES

_DAY_S = 86400
_META_FIELDS = ("year", "runtime_s", "director", "cast", "tmdb_id", "imdb_id")
_warned_no_key = False  # once per process
IDENTITY = "tmdb"  # lookup row of the TMDB identification; never a website source


class Lookup:
    def __init__(self, ctx, db: Db, sources: dict | None = None, identify=tmdb.identify) -> None:
        self._ctx = ctx
        self._db = db
        self._sources = SOURCES if sources is None else sources
        self._identify = identify
        self._unknown_logged: set[str] = set()
        # One for the process: the per-site gap holds even when a site is asked for two films in a row.
        self._fetch = Fetcher(ctx, ctx.config.get("request_gap_s", 3))

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
            film = Film(title, norm, runtime_s)
            film_id = self._db.save_film(film, ctx.clock())
        else:
            film_id, film = found
        state = self._db.lookup_state(film_id)
        gained = False
        if not film.tmdb_id and self._is_due(state.get(IDENTITY)):
            identified = self._identity(film_id, title, runtime_s)
            if identified is not None:
                film, gained = self._fold_identity(film, identified), True
        due = self._due(state, reopen=("miss", "ambiguous") if gained else ())
        due = [name for name in due if self._can_match(name, film)]
        if due:
            if self._db.lookups_since(ctx.clock() - _DAY_S, exclude=(IDENTITY,)) >= self._cfg("max_lookups_per_day", 30):
                ctx.log("daily lookup limit reached")
            else:
                self._query(film_id, film, due, app_id)
        result = merge(self._db.scenes(film_id), self._cfg("categories", ["nudity", "sex"]),
                       self._cfg("join_gap_s", 20))
        ctx.log(f'"{title}": {len(result)} scenes')
        return result

    def _identity(self, film_id, title, runtime_s) -> Film | None:
        """Ask TMDB and record the outcome as the film's "tmdb" lookup row (none without a key)."""
        global _warned_no_key
        ctx = self._ctx
        key = ctx.secret("tmdb_key")
        if not key:
            if not _warned_no_key:
                _warned_no_key = True
                ctx.log("no tmdb_key in secrets.json")
            return None
        try:
            film = self._identify(ctx.http, key, title, runtime_s)
            status = "miss" if film is None else "hit"
        except Exception as e:
            ctx.log(f"tmdb failed: {type(e).__name__}")
            film, status = None, "error"
        self._db.save_result(film_id, IDENTITY, SourceResult(status), ctx.clock())
        return film

    def _fold_identity(self, film: Film, identified: Film) -> Film:
        """Fill the stored film's missing fields from TMDB's (stored values win, as in save_film)."""
        merged = Film(**{f.name: getattr(film, f.name) or getattr(identified, f.name)
                         for f in dataclasses.fields(Film)})
        self._db.save_film(merged, self._ctx.clock())
        return merged

    def _is_due(self, known: tuple[str, float] | None) -> bool:
        if known is None:
            return True
        status, fetched_at = known
        ttl_days = {"hit": self._cfg("hit_ttl_days", 90), "miss": self._cfg("miss_ttl_days", 14),
                    "ambiguous": self._cfg("miss_ttl_days", 14), "blocked": self._cfg("blocked_ttl_days", 1),
                    "error": self._cfg("blocked_ttl_days", 1)}
        return self._ctx.clock() - fetched_at >= ttl_days.get(status, 0) * _DAY_S

    def _due(self, state: dict, reopen: tuple[str, ...] = ()) -> list[str]:
        """Configured sources whose stored outcome has expired, or whose status is in `reopen`."""
        due = []
        for name in self._cfg("sources", ["videoskip", "nsfwhen"]):
            if name == IDENTITY or name not in self._sources:
                if name not in self._unknown_logged:
                    self._unknown_logged.add(name)
                    self._ctx.log(f"unknown source {name}")
                continue
            known = state.get(name)
            if self._is_due(known) or known[0] in reopen:
                due.append(name)
        return due

    def _can_match(self, name: str, film: Film) -> bool:
        if name in NEEDS_IDENTITY and not (film.year or film.imdb_id or film.tmdb_id):
            self._ctx.log(f"{name}: skipped (film not identified)")
            return False
        return True

    def _query(self, film_id, film, due, app_id) -> None:
        ctx = self._ctx
        for name in due:
            cfg = {**self._cfg("source_cfg", {}).get(name, {}),
                   "service": self._cfg("app_services", {}).get(app_id)}
            try:
                result = self._sources[name](self._fetch, film, cfg)
                ctx.log(f"{name}: {result.status}, {len(result.scenes)} scenes, {result.requests} requests")
            except Exception as e:
                result = SourceResult("error")
                ctx.log(f"{name}: error ({type(e).__name__})")
            if result.status == "hit":
                film = self._fold(film, result.meta)
            self._db.save_result(film_id, name, result, ctx.clock())

    def _fold(self, film: Film, meta: dict) -> Film:
        fill = {k: meta[k] for k in _META_FIELDS if meta.get(k) and not getattr(film, k)}
        if "cast" in fill:
            fill["cast"] = tuple(str(n) for n in fill["cast"])  # Film is frozen and hashable
        if not fill:
            return film
        film = dataclasses.replace(film, **fill)
        self._db.save_film(film, self._ctx.clock())
        return film
