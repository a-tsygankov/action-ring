"""scenes: identify a film (year, director, cast, ids) from the TV's title and runtime via TMDB."""

from __future__ import annotations

from urllib.parse import quote

from services.scenes.model import Film, norm_title

_API = "https://api.themoviedb.org/3"
_MAX_CANDIDATES = 3
_RUNTIME_TOLERANCE_S = 180


def _year(release_date) -> int | None:
    try:
        return int(str(release_date)[:4])
    except (TypeError, ValueError):
        return None


def _runtime_s(detail: dict) -> int | None:
    minutes = detail.get("runtime")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return None
    return int(minutes * 60)


def _film(title: str, norm: str, detail: dict) -> Film:
    credits = detail.get("credits") if isinstance(detail.get("credits"), dict) else {}
    crew = credits.get("crew") if isinstance(credits.get("crew"), list) else []
    cast = credits.get("cast") if isinstance(credits.get("cast"), list) else []
    director = next((p["name"] for p in crew
                     if isinstance(p, dict) and p.get("job") == "Director" and p.get("name")), None)
    names = tuple(p["name"] for p in cast if isinstance(p, dict) and p.get("name"))[:5]
    imdb = detail.get("imdb_id")
    return Film(
        title=title, norm=norm, runtime_s=_runtime_s(detail), year=_year(detail.get("release_date")),
        director=director, cast=names,
        tmdb_id=detail.get("id") if isinstance(detail.get("id"), int) else None,
        imdb_id=imdb if isinstance(imdb, str) and imdb else None)


def identify(http, key: str, title: str, runtime_s: int | None) -> Film | None:
    """The TMDB film matching the title and (within 3 minutes) the runtime, or None.

    HTTP errors propagate to the caller. With no runtime, only a lone title match is accepted.
    """
    norm = norm_title(title)
    if not norm:
        return None
    query = f"query={quote(title, safe='')}&api_key={quote(key, safe='')}"
    found = http.get_json(f"{_API}/search/movie?{query}", op="tmdb_search")
    results = found.get("results") if isinstance(found, dict) else None
    candidates = []
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("id"), int) or isinstance(r.get("id"), bool):
            continue
        if norm in (norm_title(str(r.get("title") or "")), norm_title(str(r.get("original_title") or ""))):
            candidates.append(r["id"])
    candidates = candidates[:_MAX_CANDIDATES]
    if runtime_s is None and len(candidates) != 1:
        return None
    for movie_id in candidates:
        detail = http.get_json(
            f"{_API}/movie/{movie_id}?append_to_response=credits&api_key={quote(key, safe='')}",
            op="tmdb_movie")
        if not isinstance(detail, dict):
            continue
        film = _film(title, norm, detail)
        if runtime_s is None:
            return film
        if film.runtime_s is not None and abs(film.runtime_s - runtime_s) <= _RUNTIME_TOLERANCE_S:
            return film
    return None
