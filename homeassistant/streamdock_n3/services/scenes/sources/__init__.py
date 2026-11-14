"""Website sources: each is `lookup(fetch, film, cfg) -> SourceResult`."""

from __future__ import annotations

from typing import Callable

from ..fetch import Fetcher
from ..model import Film, SourceResult
from . import videoskip

SOURCES: dict[str, Callable[[Fetcher, Film, dict], SourceResult]] = {
    "videoskip": videoskip.lookup,
}
