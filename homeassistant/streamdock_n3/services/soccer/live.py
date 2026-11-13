"""Live soccer key: the live game list, a cursor through it, and switching to a pick.

Polls the Apple TV app listing (listing.py) for live Paramount+ / MLS games;
a press picks a game at once (the key shows it) and, once presses settle,
powers on and plays the last pick. LiveSoccer takes an injected service
`call`, state lookup, `fetch`, `clock` and `sleep`, so it is testable without
the network, Home Assistant or the device. Research: docs/superpowers/specs/
2026-10-24-actionring-tabs-research/live-soccer.md.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
from typing import Callable

from keyspec import StateLookup
from media_actions import Call, ensure_on, select_atv_input

from .listing import (
    CYCLE_S,
    OPEN_TIMEOUT,
    QUERIES,
    STALE_MIN,
    SWITCH_AFTER_S,
    TOKEN_MAX_AGE,
    UTS_PARAMS,
    UTS_SHELF,
    Game,
    next_start,
    order,
    parse_games,
    parse_tokens,
    poll_interval,
    retry_interval,
)


def log(msg: str) -> None:
    print(f"soccer: {msg}", flush=True)


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
        fetch: Callable[[str], str],
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        on_change: Callable[[], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        notify: Callable[..., None] | None = None,
        on_pick: Callable[[], None] | None = None,
    ):
        """`notify(message, channel=...)` shows a short message to the viewer (a TV toast);
        `on_pick()` runs after each pick (its CYCLE_S window ends unannounced)."""
        self._toast = notify or (lambda message, channel=None: None)
        self._on_pick = on_pick or (lambda: None)
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
        # The key's pick: shown at once, switched to once presses settle.
        self._pick_lock = threading.Lock()
        self._picked: str | None = None  # Game.id of the last pick
        self._picked_at = -1e9  # monotonic time of the last press
        self._gen = 0  # bumped per pick; a switch for an older one gives up
        self._switch_lock = threading.Lock()  # one switch (power on, play) at a time
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

    def poll_once(self) -> float:
        """One background refresh for the key's live look; never raises.
        Returns the seconds to wait before the next one.

        Every poll_s while a game is live or about to start, else every
        idle_poll_s (the listing is undocumented: use it lightly). After
        failed fetches: poll_s, doubling up to idle_poll_s; until the first
        successful fetch, poll_s (the network may still be coming up).
        """
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
        return wait

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
        """The key: pick the first live game, or the next one while picking."""
        self.press()

    def next(self) -> None:
        """The next live game, wrapping at the end."""
        self.press(advance=True)

    def press(self, advance: bool = False) -> None:
        """Pick a game at once (the key shows it), then switch to the last pick
        once presses settle. Blocks: run it off the device's reader thread."""
        gen = self.select(advance)
        if gen is None:  # nothing cached yet
            self.refresh(max_age=float(self._cfg.get("cache_s", 60)))
            gen = self.select(advance)
        if gen is None:
            log("no live games")
            self._toast("No live soccer games", channel="soccer")
            return
        self._sleep(float(self._cfg.get("switch_after_s", SWITCH_AFTER_S)))
        self.switch(gen)

    def select(self, advance: bool = False) -> int | None:
        """Pick the first game, or the next (wrapping) while picking or with
        `advance`; no I/O. The pick's generation, or None with no live games."""
        games = self.games()
        if not games:
            return None
        moving_on = advance or self.picking()
        with self._pick_lock:
            i = (self._index(games) + 1) % len(games) if moving_on else 0
            self._cursor, self._picked = i, games[i].id
            self._picked_at = self._monotonic()
            self._gen += 1
            gen = self._gen
        log(f"picked {i + 1}/{len(games)}: {games[i].title}")
        self._toast(f"{i + 1}/{len(games)} {games[i].title}", channel="soccer")
        self._on_change()  # {soccer_pos} changed
        self._on_pick()
        return gen

    def picking(self) -> bool:
        """A pick is current: pressed within CYCLE_S, or its game started and on screen."""
        if not self.games():
            return False
        if self._monotonic() - self._picked_at < CYCLE_S:
            return True
        return self.watching(self._state_of)

    def switch(self, gen: int) -> None:
        """Power on, select the input and play the pick, unless a newer pick came."""
        with self._switch_lock:
            if gen != self._gen:
                return
            games = self.games()
            if not games:
                log("no live games")
                return
            i = self._index(games)
            if not ensure_on(self._call, self._state_of, self._cfg, sleep=self._sleep, clock=self._monotonic):
                log("TV did not turn on")
                return
            if gen != self._gen:
                return  # picked again while the TV came on
            select_atv_input(self._call, self._state_of, self._cfg)
            self._play(games, i, gen)

    def play(self, i: int) -> None:
        """Pick game i and open it now (no power-on)."""
        games = self.games()
        if not games:
            log("no live games")
            return
        i %= len(games)
        with self._pick_lock:
            self._cursor, self._picked = i, games[i].id
            self._gen += 1
            gen = self._gen
        self._on_change()  # {soccer_pos} changed
        self._on_pick()
        self._play(games, i, gen)

    def _index(self, games: list[Game]) -> int:
        """The pick's position in `games`: its game's, else the old slot (clamped)."""
        for i, game in enumerate(games):
            if game.id == self._picked:
                return i
        return min(self._cursor, len(games) - 1)

    def _play(self, games: list[Game], i: int, gen: int) -> None:
        """Open game i; one `select` if its app is up but not playing (profile picker, modal)."""
        game = games[i]
        with self._play_lock:
            self._started, self._started_at, self._seen = game, self._monotonic(), False
        log(f"playing {i + 1}/{len(games)}: {game.title} ({game.provider})")
        atv = self._cfg["atv"]
        self._call(
            "media_player",
            "play_media",
            {"entity_id": atv, "media_content_type": "url", "media_content_id": game.url},
        )
        self._sleep(float(self._cfg.get("select_after_s", 8)))
        if gen != self._gen:
            return  # picked again: that game's switch takes over
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
        games = self.games()
        n = len(games)
        pos = self._index(games) + 1 if n else 0
        return {"soccer_count": str(n), "soccer_pos": f"{pos}/{n}"}
