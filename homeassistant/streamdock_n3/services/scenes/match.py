"""Decides whether a website's film is the film being played."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Film, norm_title

_YEAR_TOL = 1
_RUNTIME_TOL_S = 180


@dataclass(frozen=True)
class Candidate:
    ref: str
    title: str
    year: int | None = None
    runtime_s: int | None = None
    director: str | None = None
    cast: tuple[str, ...] = ()
    tmdb_id: int | None = None
    imdb_id: str | None = None


def _id_state(film: Film, c: Candidate) -> bool | None:
    """True: same id; False: a known id differs; None: nothing to compare."""
    state = None
    for a, b in ((film.tmdb_id, c.tmdb_id), (film.imdb_id, c.imdb_id)):
        if a is None or b is None:
            continue
        if a != b:
            return False
        state = True
    return state


def _year_state(film: Film, c: Candidate) -> bool | None:
    if film.year is None or c.year is None:
        return None
    return abs(film.year - c.year) <= _YEAR_TOL


def _runtime_state(film: Film, c: Candidate) -> bool | None:
    if film.runtime_s is None or c.runtime_s is None:
        return None
    return abs(film.runtime_s - c.runtime_s) <= _RUNTIME_TOL_S


def _name(text: str | None) -> str:
    return norm_title(text) if text else ""


def pick(film: Film, candidates: list[Candidate]) -> tuple[Candidate | None, str]:
    """(candidate, "hit") | (None, "miss") | (None, "ambiguous")."""
    kept = []
    for c in candidates:
        if norm_title(c.title) != film.norm:
            continue
        states = (_id_state(film, c), _year_state(film, c), _runtime_state(film, c))
        if False in states or True not in states:
            continue
        kept.append(c)
    if not kept:
        return None, "miss"
    if len(kept) == 1:
        return kept[0], "hit"
    # No exact-year rule: the year filter above (+-1) already ran, and two films a year apart
    # (remake, re-release) must stay ambiguous, as the brief's test requires.
    film_cast = {_name(n) for n in film.cast}
    rules = (
        lambda c: _id_state(film, c) is True,
        lambda c: _runtime_state(film, c) is True,
        lambda c: bool(film.director) and _name(c.director) == _name(film.director),
        lambda c: len(film_cast & {_name(n) for n in c.cast}) >= 2,
    )
    for rule in rules:
        left = [c for c in kept if rule(c)]
        if len(left) == 1:
            return left[0], "hit"
    return None, "ambiguous"
