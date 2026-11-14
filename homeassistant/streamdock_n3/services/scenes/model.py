"""scenes: data types shared by the sources, the store and the service, plus title helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Film:
    title: str
    norm: str
    runtime_s: int | None = None
    year: int | None = None
    director: str | None = None
    cast: tuple[str, ...] = ()
    tmdb_id: int | None = None
    imdb_id: str | None = None  # like "tt1234567"


@dataclass(frozen=True)
class Scene:
    start_s: float
    end_s: float
    category: str  # "nudity" | "sex" | "suggestive"
    source: str = ""
    synced_with: str = ""
    note: str = ""


@dataclass(frozen=True)
class SourceResult:
    status: str  # "hit" | "miss" | "ambiguous" | "blocked" | "error"
    scenes: tuple[Scene, ...] = ()
    meta: dict = field(default_factory=dict)  # any of year, runtime_s, director, cast, tmdb_id, imdb_id
    requests: int = 0


_TRAILING_YEAR = re.compile(r"\(\d{4}\)\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_ARTICLES = ("the ", "a ", "an ")


def norm_title(title: str) -> str:
    """Comparable form of a film title: ascii, lower case, no year, article or punctuation."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ")
    text = _TRAILING_YEAR.sub("", text.strip())
    text = _NON_ALNUM.sub(" ", text).strip()
    text = text + " "
    for article in _ARTICLES:
        if text.startswith(article):
            text = text[len(article):]
            break
    return " ".join(text.split())


def parse_time(text: str) -> float:
    """Seconds from "H:MM:SS", "MM:SS" or either with a fractional second; ValueError otherwise."""
    parts = text.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"not a timestamp: {text!r}")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"not a timestamp: {text!r}") from None
    if any(n < 0 or n != n or n in (float("inf"),) for n in nums):
        raise ValueError(f"not a timestamp: {text!r}")
    seconds = 0.0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds
