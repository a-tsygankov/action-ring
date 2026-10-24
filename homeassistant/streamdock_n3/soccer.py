"""Live soccer key: Paramount+ and MLS games from the Apple TV app listing.

The Apple TV app's backend (UTS API, as used by tv.apple.com) lists live and
upcoming sporting events across partner apps, each with its provider and the
deep link the TV app itself hands off with. Two searches ("Paramount+" and
"MLS") give the candidates; live soccer on Paramount+ (`pplus://` punchout)
or Apple TV (MLS, `tv.apple.com/.../sporting-event/...` URL) is kept.

The only keys are two public web-client tokens (utsk, utscf) embedded in
every tv.apple.com page; they are scraped from `token_page` at run time,
once a day or after an HTTP 4xx, and never stored.

Parsing is pure; LiveSoccer takes an injected service `call`, state lookup,
`fetch`, `clock` and `sleep`, so it is testable without the network, Home
Assistant or the device. Research: docs/superpowers/specs/
2026-10-24-actionring-tabs-research/live-soccer.md.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from keyspec import StateLookup
from media_actions import Call, ensure_on, select_atv_input

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
HTTP_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0"

_UTSK = re.compile(r'"utsk":"([^"]+)"')
_UTSCF = re.compile(r'"utscf":"([^"]+)"')


def log(msg: str) -> None:
    print(f"soccer: {msg}", flush=True)


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


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8")


class LiveSoccer:
    """The live game list, a cursor through it, and whether a started game is on screen.

    cfg ("soccer" section): atv, atv_remote, lg, atv_source, power_on_wait,
    token_page, cache_s, poll_s, idle_poll_s, select_after_s. `clock` is wall
    time (Apple's listing is in epoch ms); `monotonic` times waits.
    """

    def __init__(
        self,
        call: Call,
        state_of: StateLookup,
        cfg: dict,
        fetch: Callable[[str], str] = http_get,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        on_change: Callable[[], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._call = call
        self._state_of = state_of
        self._cfg = cfg
        self._fetch = fetch
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._on_change = on_change or (lambda: None)
        self._lock = threading.Lock()  # one refresh at a time
        self._games: list[Game] = []
        self._fetched_at: float | None = None  # last successful fetch (wall clock)
        self._errors = 0  # consecutive failed fetches
        self._next_start: float | None = None  # next listed game's live window opens (epoch s)
        self._tokens: tuple[str, str] | None = None
        self._tokens_at = 0.0
        self._cursor = 0
        # play() (a worker thread) and watching() (HA/render threads) share these three.
        self._play_lock = threading.Lock()
        self._started: Game | None = None  # the game play() last opened
        self._started_at = 0.0
        self._seen = False  # its app has been in the foreground since

    # -- list ----------------------------------------------------------------

    def games(self) -> list[Game]:
        """The cached live list (no I/O: safe on the render thread); empty once stale."""
        if self._stale(self._clock()):
            return []
        return list(self._games)

    def _stale(self, now: float) -> bool:
        """No successful fetch yet, or none for max(2 x poll_s, STALE_MIN) s."""
        max_age = max(2 * float(self._cfg.get("poll_s", 180)), STALE_MIN)
        return self._fetched_at is None or now - self._fetched_at > max_age

    def refresh(self, max_age: float = 0.0) -> None:
        """Refetch unless the list is younger than `max_age` s; on error keep the last list until stale."""
        with self._lock:
            now = self._clock()
            if self._fetched_at is not None and now - self._fetched_at < max_age:
                return
            try:
                payloads = self._shelves()
                games = order([g for p in payloads for g in parse_games(p, now)])
                starts = [t for t in (next_start(p, now) for p in payloads) if t is not None]
            except Exception as err:  # never let a key press or the poller die on this
                self._errors += 1
                log(f"refresh failed: {type(err).__name__}: {err}")
                if not (self._games and self._stale(now)):
                    return
                self._games = []  # games() already hides it; this signals the change once
                log("live list expired")
            else:
                self._errors = 0
                if [g.id for g in games] != [g.id for g in self._games]:
                    log(f"{len(games)} live: " + "; ".join(f"{g.title} ({g.provider})" for g in games))
                self._games, self._fetched_at = games, now
                self._next_start = min(starts, default=None)
        self._on_change()

    def poll_forever(self) -> None:
        """Background refresh for the key's live look; never raises.

        Every poll_s while a game is live or about to start, else every
        idle_poll_s (the listing is undocumented: use it lightly). After
        failed fetches: poll_s, doubling up to idle_poll_s; until the first
        successful fetch, poll_s (the network may still be coming up).
        """
        while True:
            poll_s = float(self._cfg.get("poll_s", 180))
            idle_poll_s = float(self._cfg.get("idle_poll_s", 1800))
            wait = poll_s
            try:
                self.refresh(max_age=float(self._cfg.get("cache_s", 60)))
                if self._errors:
                    wait = retry_interval(self._errors, poll_s, idle_poll_s)
                elif self._fetched_at is not None:
                    wait = poll_interval(bool(self.games()), self._next_start, self._clock(), poll_s, idle_poll_s)
            except Exception as err:
                log(f"poll: {type(err).__name__}: {err}")
            self._sleep(wait)

    def _shelves(self) -> list[dict]:
        if self._tokens is None or self._clock() - self._tokens_at >= TOKEN_MAX_AGE:
            self._load_tokens()
        try:
            return [self._shelf(q) for q in QUERIES]
        except urllib.error.HTTPError as err:
            if not 400 <= err.code < 500:
                raise
            log(f"HTTP {err.code}; refreshing tokens")
            self._load_tokens()
            return [self._shelf(q) for q in QUERIES]

    def _load_tokens(self) -> None:
        tokens = parse_tokens(self._fetch(self._cfg["token_page"]))
        if tokens is None:
            raise RuntimeError("no utsk/utscf on the token page")
        self._tokens, self._tokens_at = tokens, self._clock()
        log("tokens refreshed")

    def _shelf(self, term: str) -> dict:
        utsk, utscf = self._tokens or ("", "")
        q = lambda s: urllib.parse.quote(s, safe="")  # noqa: E731
        url = f"{UTS_SHELF}?{UTS_PARAMS}&utscf={q(utscf)}&utsk={q(utsk)}&searchTerm={q(term)}"
        return json.loads(self._fetch(url))  # the URL holds the tokens: never log it

    # -- actions ---------------------------------------------------------------

    def start(self) -> None:
        """Power on if needed and play the first live game."""
        self.refresh(max_age=float(self._cfg.get("cache_s", 60)))
        if not self.games():
            log("no live games")
            return
        if not ensure_on(self._call, self._state_of, self._cfg, sleep=self._sleep, clock=self._monotonic):
            log("TV did not turn on")
            return
        select_atv_input(self._call, self._state_of, self._cfg)
        self.play(0)

    def next(self) -> None:
        """The next live game, wrapping at the end."""
        games = self.games()
        if not games:
            log("no live games")
            return
        self.play((self._cursor + 1) % len(games))

    def play(self, i: int) -> None:
        """Open game i; one `select` if its app is up but not playing (profile picker, modal)."""
        games = self.games()
        if not games:
            log("no live games")
            return
        i %= len(games)
        game = games[i]
        self._cursor = i
        with self._play_lock:
            self._started, self._started_at, self._seen = game, self._monotonic(), False
        self._on_change()  # {soccer_pos} changed
        log(f"playing {i + 1}/{len(games)}: {game.title} ({game.provider})")
        atv = self._cfg["atv"]
        self._call(
            "media_player",
            "play_media",
            {"entity_id": atv, "media_content_type": "url", "media_content_id": game.url},
        )
        self._sleep(float(self._cfg.get("select_after_s", 8)))
        st = self._state_of(atv) or {}
        if (st.get("a") or {}).get("app_id") == game.app and st.get("s") != "playing":
            log(f"{game.app} is {st.get('s')}; sending select")
            self._call("remote", "send_command", {"entity_id": self._cfg["atv_remote"], "command": "select"})

    # -- state -----------------------------------------------------------------

    def watching(self, state_of: StateLookup) -> bool:
        """A started game's app is (still) in the foreground; cleared once another app takes over.

        No Apple TV state (an HA reconnect) or an empty app_id says nothing
        about the app: a game already seen holds through it (spec 5.1: until
        the app changes). A game whose app never came up within OPEN_TIMEOUT
        is forgotten.
        """
        app = ((state_of(self._cfg["atv"]) or {}).get("a") or {}).get("app_id")
        with self._play_lock:
            if self._started is None:
                return False
            if app == self._started.app:
                self._seen = True
                return True
            if self._seen and app:
                self._started = None  # another app took over
            elif not self._seen and self._monotonic() - self._started_at > OPEN_TIMEOUT:
                self._started = None  # the game never opened
            return self._seen and self._started is not None

    def vars(self) -> dict[str, str]:
        """Label placeholders: {soccer_count}, {soccer_pos}."""
        n = len(self.games())
        pos = min(self._cursor, n - 1) + 1 if n else 0
        return {"soccer_count": str(n), "soccer_pos": f"{pos}/{n}"}
