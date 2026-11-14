"""nudidex: behind a Cloudflare challenge, so no parser exists. Only an opt-in probe of the home page."""

from __future__ import annotations

from ..fetch import Blocked, Fetcher
from ..model import Film, SourceResult

SITE = "nudidex"
HOME = "https://www.nudidex.com/"


def lookup(fetch: Fetcher, film: Film, cfg: dict) -> SourceResult:
    if not cfg.get("probe"):
        return SourceResult("blocked", requests=0)
    try:
        fetch.get(SITE, HOME, op="nudidex_probe")
    except Blocked:
        return SourceResult("blocked", requests=fetch.count)
    except Exception:
        return SourceResult("error", requests=fetch.count)
    return SourceResult("miss", requests=fetch.count)
