"""Live soccer listing: Paramount+ and MLS games from the Apple TV app listing.

The Apple TV app's backend (UTS API, as used by tv.apple.com) lists live and
upcoming sporting events across partner apps, each with its provider and the
deep link the TV app itself hands off with. Two searches ("Paramount+" and
"MLS") give the candidates; live soccer on Paramount+ (`pplus://` punchout)
or Apple TV (MLS, `tv.apple.com/.../sporting-event/...` URL) is kept.

The only keys are two public web-client tokens (utsk, utscf) embedded in
every tv.apple.com page; they are scraped from `token_page` at run time,
once a day or after an HTTP 4xx, and never stored.

Everything here is pure: parsing, ordering and poll timing. Research:
docs/superpowers/specs/2026-10-24-actionring-tabs-research/live-soccer.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UTS_SHELF = "https://uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE"
UTS_PARAMS = "caller=web&locale=en-US&pfm=web&sf=143441&v=100"  # sf: US storefront
QUERIES = ("Paramount+", "MLS")
# channelId of playables[0] -> (provider, bundle id of the app that plays it)
PROVIDERS = {
    "tvs.sbd.10120": ("paramount", "com.cbsvideo.app"),
    "tvs.sbd.4000": ("apple", "com.apple.TVWatchList"),
}
TOKEN_MAX_AGE = 86400  # seconds
CLOCK_GRACE = 30 * 60  # seconds a running clock still counts after the live window ends
SOON = 15 * 60  # seconds before a listed start when polling speeds up to poll_s
OPEN_TIMEOUT = 60  # seconds for a started game's app to come up before it is forgotten
STALE_MIN = 30 * 60  # a list not refreshed for max(2 x poll_s, this) seconds counts as empty
CYCLE_S = 60  # a press this soon after the last one (or while a started game shows) moves on
SWITCH_AFTER_S = 1.5  # presses settle this long before the TV switches to the last pick

_UTSK = re.compile(r'"utsk":"([^"]+)"')
_UTSCF = re.compile(r'"utscf":"([^"]+)"')


@dataclass(frozen=True)
class Game:
    id: str
    title: str
    league: str
    kickoff: float  # epoch seconds
    provider: str  # "paramount" | "apple"
    url: str  # deep link for media_player.play_media (media_content_type "url")
    app: str  # bundle id the Apple TV shows while the game plays


def parse_tokens(html: str) -> tuple[str, str] | None:
    """(utsk, utscf) from a tv.apple.com page, or None."""
    sk, cf = _UTSK.search(html), _UTSCF.search(html)
    return (sk.group(1), cf.group(1)) if sk and cf else None


def _badge(item: dict) -> dict:
    return (item.get("eventTime") or {}).get("liveBadgeTime") or {}


def _ended(item: dict) -> bool:
    clock = item.get("clockScore")
    return isinstance(clock, dict) and bool(clock.get("hasEventEnded"))


def _is_live(item: dict, now_ms: float) -> bool:
    start, end = _badge(item).get("startTime"), _badge(item).get("endTime")
    if isinstance(item.get("clockScore"), dict):
        # A clock decides: an ended game stays out even while the (generous)
        # live badge window is open. A clock left "running" by a stale feed
        # stops counting CLOCK_GRACE after the window.
        return not _ended(item) and (end is None or now_ms <= end + CLOCK_GRACE * 1000)
    return start is not None and end is not None and start <= now_ms <= end


def _game(item: dict) -> Game | None:
    """The item as a Game if it is a soccer match on one of our providers."""
    if item.get("sportName") != "Soccer":
        return None
    if len(item.get("competitors") or []) != 2:
        return None  # studio shows (MLS Countdown, MLS 360, ...) have no teams
    playable = (item.get("playables") or [None])[0] or {}
    provider = PROVIDERS.get(playable.get("channelId"))
    if provider is None:
        return None  # another service is the primary provider
    name, app = provider
    url = (playable.get("punchoutUrls") or {}).get("play") if name == "paramount" else playable.get("url")
    kickoff = (item.get("eventTime") or {}).get("gameKickOffStartTime")
    if not url or kickoff is None or not item.get("id"):
        return None
    return Game(
        id=item["id"],
        title=str(item.get("title") or ""),
        league=str(item.get("leagueName") or ""),
        kickoff=kickoff / 1000,
        provider=name,
        url=url,
        app=app,
    )


def _items(payload: dict) -> list[dict]:
    items = ((payload.get("data") or {}).get("shelf") or {}).get("items") or []
    return [item for item in items if isinstance(item, dict)]


def parse_games(payload: dict, now: float) -> list[Game]:
    """Live soccer games on our providers from one UTS search shelf response."""
    out: list[Game] = []
    for item in _items(payload):
        game = _game(item)
        if game is not None and _is_live(item, now * 1000):
            out.append(game)
    return out


def next_start(payload: dict, now: float) -> float | None:
    """Epoch seconds when the next listed game's live window opens, or None."""
    starts = [
        _badge(item)["startTime"] / 1000
        for item in _items(payload)
        if _game(item) is not None
        and not _ended(item)
        and isinstance(_badge(item).get("startTime"), (int, float))
        and _badge(item)["startTime"] > now * 1000
    ]
    return min(starts, default=None)


def poll_interval(live: bool, start: float | None, now: float, poll_s: float, idle_poll_s: float) -> float:
    """Seconds to the next background fetch.

    poll_s while a game is live or one starts within SOON; otherwise
    idle_poll_s, but waking SOON before the next listed start.
    """
    if live:
        return poll_s
    if start is None:
        return idle_poll_s
    until_soon = start - SOON - now
    if until_soon <= 0:
        return poll_s
    return max(poll_s, min(idle_poll_s, until_soon))


def retry_interval(errors: int, poll_s: float, idle_poll_s: float) -> float:
    """Seconds to the next fetch after `errors` consecutive failures (>= 1).

    poll_s after the first, doubling each time up to idle_poll_s.
    """
    return min(idle_poll_s, poll_s * 2 ** min(errors - 1, 16))


def order(games: list[Game]) -> list[Game]:
    """By kickoff, then league, then id; the first of each id."""
    seen: set[str] = set()
    out: list[Game] = []
    for game in sorted(games, key=lambda g: (g.kickoff, g.league, g.id)):
        if game.id not in seen:
            seen.add(game.id)
            out.append(game)
    return out
