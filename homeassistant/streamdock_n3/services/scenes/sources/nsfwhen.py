"""NSFWhen: search page, then film pages whose script embeds the film as JSON."""

from __future__ import annotations

import json
import re
from urllib.parse import quote

from ..fetch import Blocked, Fetcher
from ..match import Candidate, pick
from ..model import Film, Scene, SourceResult

BASE = "https://nsfwhen.com"
SITE = "nsfwhen"
_MAX_FILMS = 3
_CATEGORIES = {"nudity": "nudity", "sex_scene": "sex", "suggestive": "suggestive"}
_FILM_LINK = re.compile(r'href="(https://nsfwhen\.com/film/\d+)"')
_BROWSE_MARK = 'class="catalog-layout"'   # the browse page's layout, present also when nothing matches


def parse_search(html: str) -> list[str]:
    """Film page URLs, in page order. A page that is not the browse page is an error, not a miss."""
    if _BROWSE_MARK not in html:
        raise ValueError("nsfwhen search page is not the browse page")
    urls: list[str] = []
    for m in _FILM_LINK.finditer(html):
        if m.group(1) not in urls:
            urls.append(m.group(1))
    return urls


def _film_object(html: str) -> dict:
    """The `film: {...}` object of the initTimeline call, cut out by brace matching."""
    at = html.find("initTimeline(")
    start = html.find("film:", at) if at >= 0 else -1
    if start < 0:
        raise ValueError("nsfwhen film page has no embedded film")
    start = html.find("{", start)
    depth, in_str, esc = 0, False, False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                film = json.loads(html[start:i + 1])
                if not isinstance(film, dict):
                    raise ValueError("nsfwhen film is not an object")
                return film
    raise ValueError("nsfwhen film object is not closed")


def _num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_film(html: str) -> tuple[Candidate, list[Scene]]:
    """Only ids, title, date, runtime, director, cast and per scene times/category/status are read."""
    film = _film_object(html)
    title = film.get("title")
    if not isinstance(title, str) or not title or not isinstance(film.get("scenes"), list):
        raise ValueError("nsfwhen film object has no title or scenes")
    year = None
    date = film.get("release_date")
    if isinstance(date, str) and re.match(r"\d{4}-", date):
        year = int(date[:4])
    minutes = film.get("runtime_minutes")
    runtime_s = int(minutes * 60) if _num(minutes) and minutes > 0 else None
    director = film.get("director") if isinstance(film.get("director"), str) else None
    cast = film.get("cast")
    tmdb = film.get("tmdb_id")
    cand = Candidate(ref="", title=title, year=year, runtime_s=runtime_s, director=director,
                     cast=tuple(n for n in cast if isinstance(n, str)) if isinstance(cast, list) else (),
                     tmdb_id=tmdb if isinstance(tmdb, int) and not isinstance(tmdb, bool) else None)
    scenes = []
    for s in film["scenes"]:
        if not isinstance(s, dict):
            continue
        category = _CATEGORIES.get(s.get("category"))
        start, end = s.get("start_time_seconds"), s.get("end_time_seconds")
        if (category is None or s.get("verification_status") == "rejected"
                or not (_num(start) and _num(end)) or end <= start):
            continue
        scenes.append(Scene(start, end, category, source=SITE))
    return cand, scenes


def lookup(fetch: Fetcher, film: Film, cfg: dict) -> SourceResult:
    try:
        return _lookup(fetch, film)
    except Blocked:
        return SourceResult("blocked", requests=fetch.count)
    except Exception:
        return SourceResult("error", requests=fetch.count)


def _lookup(fetch: Fetcher, film: Film) -> SourceResult:
    def done(status: str, scenes=(), meta=None) -> SourceResult:
        return SourceResult(status, tuple(scenes), meta or {}, fetch.count)

    urls = parse_search(fetch.get(SITE, f"{BASE}/browse?q={quote(film.title, safe='')}", op="nsfwhen_search"))
    found: list[tuple[Candidate, list[Scene]]] = []
    for url in urls[:_MAX_FILMS]:
        c, scenes = parse_film(fetch.get(SITE, url, op="nsfwhen_film"))
        found.append((Candidate(url, c.title, c.year, c.runtime_s, c.director, c.cast, c.tmdb_id), scenes))
    cand, status = pick(film, [c for c, _ in found])
    if cand is None:
        return done(status)
    scenes = next(s for c, s in found if c is cand)
    meta = {k: v for k, v in (("year", cand.year), ("runtime_s", cand.runtime_s), ("director", cand.director),
                              ("cast", list(cand.cast) or None), ("tmdb_id", cand.tmdb_id)) if v is not None}
    return done("hit" if scenes else "miss", scenes, meta)
