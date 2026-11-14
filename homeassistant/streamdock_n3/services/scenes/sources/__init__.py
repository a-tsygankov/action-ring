"""Website sources: each is `lookup(fetch, film, cfg) -> SourceResult`."""

from __future__ import annotations

from typing import Callable

from ..fetch import Fetcher
from ..model import Film, SourceResult
from . import nsfwhen, nudidex, videoskip

SOURCES: dict[str, Callable[[Fetcher, Film, dict], SourceResult]] = {
    "videoskip": videoskip.lookup,
    "nsfwhen": nsfwhen.lookup,
    "nudidex": nudidex.lookup,
}

# Sources that cannot accept any match without a year or an id (VideoSkip gives no runtime):
# not asked for a film TMDB has not identified.
NEEDS_IDENTITY = {"videoskip"}
