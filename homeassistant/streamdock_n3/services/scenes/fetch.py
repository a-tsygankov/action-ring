"""Polite HTTP for the website sources: a pause per site, browser headers, block detection."""

from __future__ import annotations

import urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
_HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
_CHALLENGE_MARKERS = ("Just a moment...", "challenge-platform")


class Blocked(Exception):
    """The site refused us (403/429) or served a bot-check page. Never try to get past it."""


class Fetcher:
    """Spaces requests to the same `site` at least `gap_s` apart; counts requests made."""

    def __init__(self, ctx, gap_s: float = 3.0) -> None:
        self._ctx = ctx
        self._gap_s = gap_s
        self._last: dict[str, float] = {}
        self.count = 0

    def get(self, site: str, url: str, *, op: str) -> str:
        last = self._last.get(site)
        if last is not None:
            wait = self._gap_s - (self._ctx.monotonic() - last)
            if wait > 0:
                self._ctx.sleep(wait)
        self._last[site] = self._ctx.monotonic()
        self.count += 1
        try:
            body = self._ctx.http.get(url, op=op, headers=_HEADERS, timeout=15)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f"{site}: HTTP {e.code}") from None
            raise
        finally:
            self._last[site] = self._ctx.monotonic()
        if any(m in body for m in _CHALLENGE_MARKERS):
            raise Blocked(f"{site}: challenge page")
        return body
