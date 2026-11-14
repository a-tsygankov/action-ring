"""soccer: live game list from the Apple TV app listing, play/next and watching state."""

import json
import urllib.error
from pathlib import Path

from services.soccer import listing as soccer
from services.soccer.listing import Game, next_start, order, parse_games, parse_tokens, poll_interval
from services.soccer.live import LiveSoccer

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed "now" for the fixtures (2026-10-24 14:33:20Z): see test_parse_games_*.
NOW = 1792850000.0

ATV = "media_player.atv"
CFG = {
    "atv": ATV,
    "atv_remote": "remote.atv",
    "lg": "media_player.lg",
    "atv_source": "Apple OTT",
    "power_on_wait": 25,
    "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
    "cache_s": 60,
    "poll_s": 180,
    "select_after_s": 8,
    "idle_poll_s": 1800,
}
P_PLUS = "com.cbsvideo.app"
TV_APP = "com.apple.TVWatchList"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _game(id_, kickoff=0.0, league="L", app=P_PLUS) -> Game:
    return Game(id_, f"title {id_}", league, kickoff, "paramount", f"pplus://{id_}", app)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, domain, service, data):
        self.calls.append((domain, service, data))

    def names(self):
        return [(d, s) for d, s, _ in self.calls]


class FakeTime:
    def __init__(self, now=NOW):
        self.now = now
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


class FakeFetch:
    """Serves the token page and the two shelves from fixtures; can be told to fail."""

    def __init__(self):
        self.urls = []
        self.fail: Exception | None = None

    def __call__(self, url):
        self.urls.append(url)
        if self.fail is not None:
            raise self.fail
        if url == CFG["token_page"]:
            return _fixture("tv_page_tokens.html")
        if "searchTerm=Paramount%2B" in url:
            return _fixture("uts_search_paramount.json")
        if "searchTerm=MLS" in url:
            return _fixture("uts_search_mls.json")
        raise AssertionError(f"unexpected url {url}")


def _live(states=None, fetch=None, t=None):
    rec, t = Recorder(), t or FakeTime()
    st = states if states is not None else {"media_player.lg": {"s": "on", "a": {"source": "Apple OTT"}}}
    live = LiveSoccer(
        rec, st.get, CFG, fetch=fetch or FakeFetch(), clock=t.clock, sleep=t.sleep, monotonic=t.clock
    )
    return live, rec, st, t


def test_parse_tokens():
    assert parse_tokens(_fixture("tv_page_tokens.html")) == ("x", "y")
    assert parse_tokens("<html>nothing</html>") is None


def test_parse_games_filters_live_soccer_and_providers():
    # Paramount+ fixture: Charlton (clock running) is live; Tottenham v Arsenal
    # (inside the live badge window) is a Women's Super League game, left out;
    # Portsmouth ended, the rugby game is not soccer, Cagliari is on another
    # provider, Napoli has not started.
    pplus = parse_games(json.loads(_fixture("uts_search_paramount.json")), NOW)
    assert [g.id for g in pplus] == ["umc.cse.37en2vfv6drgjmfc8axpibi8a"]
    assert "umc.cse.7cplj98aglynqjm12kcaj9i11" not in {g.id for g in pplus}
    charlton = pplus[0]
    assert charlton.provider == "paramount" and charlton.app == P_PLUS
    assert charlton.league == "EFL Championship"
    assert charlton.kickoff == 1792841400.0
    assert charlton.url.startswith("pplus://www.paramountplus.com/live-tv/stream/english-football-league/")
    # MLS fixture: Montreal (clock running) is live; Philadelphia ended, Columbus
    # upcoming; the "MLS Countdown" studio show is inside its window but has no
    # competitors, so it is not a game.
    mls = parse_games(json.loads(_fixture("uts_search_mls.json")), NOW)
    assert [g.id for g in mls] == ["umc.cse.1vpiinvtiko54yrue5pxq8f5s"]
    assert "umc.cse.lkxye2n033t7susymaowjm4a" not in {g.id for g in mls}
    assert mls[0].provider == "apple" and mls[0].app == TV_APP
    assert mls[0].url.startswith("https://tv.apple.com/us/sporting-event/cf-montreal-vs-nashville-sc/")


def test_running_clock_expires_30_min_after_the_window():
    payload = json.loads(_fixture("uts_search_mls.json"))
    end = 1792868400.0  # Montreal's live badge window end
    assert [g.id for g in parse_games(payload, end + 29 * 60)] == ["umc.cse.1vpiinvtiko54yrue5pxq8f5s"]
    later = {g.id for g in parse_games(payload, end + 31 * 60)}  # Columbus is on by then
    assert "umc.cse.1vpiinvtiko54yrue5pxq8f5s" not in later


def test_next_start_is_the_earliest_upcoming_game():
    # Napoli v Roma; not Montreal (already on), the rugby game, the studio show
    # or the ended Philadelphia game.
    payloads = [json.loads(_fixture(f)) for f in ("uts_search_paramount.json", "uts_search_mls.json")]
    assert min(filter(None, (next_start(p, NOW) for p in payloads))) == 1792857600.0
    assert next_start(payloads[1], NOW) == 1792870200.0  # Columbus v Toronto
    assert next_start({}, NOW) is None


def test_poll_interval():
    fast, idle = 180, 1800
    assert poll_interval(True, None, NOW, fast, idle) == fast  # a game is live
    assert poll_interval(False, None, NOW, fast, idle) == idle  # nothing listed
    assert poll_interval(False, NOW + 600, NOW, fast, idle) == fast  # starts within 15 min
    assert poll_interval(False, NOW + 1000, NOW, fast, idle) == fast
    assert poll_interval(False, NOW + 1200, NOW, fast, idle) == 300  # wake 15 min before it
    assert poll_interval(False, NOW + 7200, NOW, fast, idle) == idle


def test_parse_games_tolerates_junk():
    assert parse_games({}, NOW) == []
    assert parse_games({"data": {"shelf": {"items": [{"sportName": "Soccer"}, None]}}}, NOW) == []


def test_order_by_kickoff_then_league():
    a = _game("a", kickoff=200, league="Serie A")
    b = _game("b", kickoff=100, league="MLS")
    c = _game("c", kickoff=200, league="EFL Championship")
    d = _game("d", kickoff=200, league="EFL Championship")
    assert [g.id for g in order([a, d, b, c, a])] == ["b", "c", "d", "a"]


def test_start_powers_on_and_plays_first_game():
    live, rec, st, t = _live()
    live.start()
    assert rec.calls[0] == (
        "media_player",
        "play_media",
        {
            "entity_id": ATV,
            "media_content_type": "url",
            "media_content_id": live.games()[0].url,
        },
    )
    assert live.vars() == {"soccer_count": "2", "soccer_pos": "1/2"}


def test_start_switches_lg_input_first():
    live, rec, st, t = _live({"media_player.lg": {"s": "on", "a": {"source": "HDMI 1"}}})
    live.start()
    assert rec.names()[:2] == [("media_player", "select_source"), ("media_player", "play_media")]
    assert rec.calls[0][2] == {"entity_id": "media_player.lg", "source": "Apple OTT"}


def test_start_gives_up_when_tv_stays_off(capsys):
    live, rec, st, t = _live({"media_player.lg": {"s": "off", "a": {}}})
    live.start()
    assert rec.names() == [("remote", "turn_on"), ("media_player", "turn_on")]
    assert "soccer: TV did not turn on" in capsys.readouterr().out


def test_power_on_deadline_uses_the_monotonic_clock():
    wall = iter(NOW + 60 * n for n in range(1000))  # a wall clock that jumps 60 s per read (NTP)
    t = FakeTime()
    st = {"media_player.lg": {"s": "off", "a": {}}}
    live = LiveSoccer(Recorder(), st.get, CFG, fetch=FakeFetch(), clock=lambda: next(wall),
                      sleep=t.sleep, monotonic=t.clock)
    live.start()
    # The full 25 s wait despite the jumps: 2 s remote settle plus 0.5 s polls.
    assert t.sleeps[0] == soccer.SWITCH_AFTER_S  # presses settle first
    assert t.sleeps[1] == 2 and len(t.sleeps) == 2 + 50


def test_start_without_live_games_does_nothing(capsys):
    fetch = FakeFetch()
    fetch.fail = OSError("offline")  # nothing ever fetched: empty list
    live, rec, st, t = _live(fetch=fetch)
    live.start()
    assert rec.calls == []
    assert "soccer: no live games" in capsys.readouterr().out


def test_next_wraps():
    live, rec, st, t = _live()
    live.start()
    live.next()
    assert live.vars()["soccer_pos"] == "2/2"
    live.next()
    assert live.vars()["soccer_pos"] == "1/2"
    played = [d["media_content_id"] for _, s, d in rec.calls if s == "play_media"]
    urls = [g.url for g in live.games()]
    assert played == [urls[0], urls[1], urls[0]]


SELECT = ("remote", "send_command", {"entity_id": "remote.atv", "command": "select"})


def test_paramount_presses_select_twice_while_not_playing():
    # The profile picker, then "Live / Start from beginning".
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert t.sleeps == [8, 5]
    assert rec.calls[-2:] == [SELECT, SELECT]


def test_paramount_stops_pressing_once_it_plays():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}

    def call(domain, service, data):
        rec(domain, service, data)
        if data.get("command") == "select":
            st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}  # no profile picker: straight to Live

    live._call = call
    live.play(0)
    assert rec.names().count(("remote", "send_command")) == 1


def test_paramount_presses_even_when_now_playing_is_stale():
    # The Apple TV's now-playing stayed "idle, no app" while Paramount+ showed its picker.
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": ""}}
    live.play(0)
    assert rec.names().count(("remote", "send_command")) == 2


def test_apple_tv_game_one_select_when_not_playing():
    live, rec, st, t = _live()
    live.refresh()
    mls = next(i for i, g in enumerate(live.games()) if g.provider == "apple")
    st[ATV] = {"s": "idle", "a": {"app_id": TV_APP}}  # a modal
    live.play(mls)
    assert t.sleeps == [8]
    assert rec.calls[-1] == SELECT and rec.names().count(("remote", "send_command")) == 1


def test_play_no_select_when_playing():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert ("remote", "send_command") not in rec.names()


def test_apple_tv_game_no_select_when_other_app():
    live, rec, st, t = _live()
    live.refresh()
    mls = next(i for i, g in enumerate(live.games()) if g.provider == "apple")
    st[ATV] = {"s": "idle", "a": {"app_id": "com.netflix.Netflix"}}
    live.play(mls)
    assert ("remote", "send_command") not in rec.names()


def test_paramount_cold_start_quits_the_app_before_the_link():
    quits = []
    live, rec, st, t = _live()
    live._quit_app = lambda: quits.append(len(rec.calls))
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}
    live.play(0)
    game = live.games()[0]
    assert rec.calls[0] == ("media_player", "play_media",
                            {"entity_id": ATV, "media_content_type": "app", "media_content_id": P_PLUS})
    assert quits == [1]  # after bringing Paramount+ to the front, before the link
    assert rec.calls[1] == ("media_player", "play_media",
                            {"entity_id": ATV, "media_content_type": "url", "media_content_id": game.url})
    assert t.sleeps[:2] == [4, 2]


def test_a_failed_quit_still_opens_the_game(capsys):
    live, rec, st, t = _live()

    def fail():
        raise RuntimeError("no Companion session")

    live._quit_app = fail
    live.refresh()
    live.play(0)
    assert [d["media_content_type"] for _, s, d in rec.calls if s == "play_media"] == ["app", "url"]
    assert "quit com.cbsvideo.app failed: RuntimeError: no Companion session" in capsys.readouterr().out


def test_a_newer_pick_during_the_cold_start_stops_it():
    live, rec, st, t = _live()
    live.refresh()
    live._quit_app = lambda: live.select()  # pressed again while Paramount+ restarts
    live.switch(live.select())
    assert [d["media_content_type"] for _, s, d in rec.calls if s == "play_media"] == ["app"]


def test_watching_clears_on_app_change():
    live, rec, st, t = _live()
    assert not live.watching(st.get)  # nothing started
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": "com.netflix.Netflix"}}
    assert not live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    assert not live.watching(st.get)  # cleared for good until the next game starts


def test_watching_waits_for_the_app_to_open():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": "com.netflix.Netflix"}}
    live.play(0)  # the app has not come up within select_after_s
    assert not live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    assert live.watching(st.get)


def test_watching_forgets_a_game_that_never_opened():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": "com.netflix.Netflix"}}
    live.play(0)
    t.now += 61
    assert not live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}  # opened by hand later
    assert not live.watching(st.get)


def test_watching_survives_cleared_states():
    # An HA reconnect clears every state; the game holds until another app shows.
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert live.watching(st.get)
    assert live.watching({}.get)  # no state: no information
    t.now += 120  # longer than OPEN_TIMEOUT
    live.watching({}.get)
    assert live.watching(st.get)  # the same app is back


def test_watching_survives_an_empty_app_id():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert live.watching(st.get)
    for blip in ({"s": "idle", "a": {"app_id": ""}}, {"s": "idle", "a": {}}, {"s": "playing"}):
        st[ATV] = blip
        assert live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    assert live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": "com.netflix.Netflix"}}  # a different app
    assert not live.watching(st.get)
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    assert not live.watching(st.get)


def test_fetch_error_keeps_last_list(capsys):
    fetch = FakeFetch()
    live, rec, st, t = _live(fetch=fetch)
    live.refresh()
    first = live.games()
    assert len(first) == 2
    fetch.fail = OSError("network down")
    t.now += 600
    live.refresh()  # must not raise
    assert live.games() == first
    assert "soccer: refresh failed: OSError: network down" in capsys.readouterr().out


def test_stale_list_ages_out(capsys):
    # After failed fetches the list counts as empty once it is older than
    # max(2 x poll_s, 30 min): no lit LIVE key, no press opening an ended game.
    fetch = FakeFetch()
    live, rec, st, t = _live(fetch=fetch)
    live.refresh()
    fetch.fail = OSError("network down")
    t.now += 1799
    live.refresh()
    assert len(live.games()) == 2
    t.now += 2
    assert live.games() == []
    assert live.vars() == {"soccer_count": "0", "soccer_pos": "0/0"}
    live.start()
    live.next()
    assert rec.calls == []
    assert capsys.readouterr().out.count("soccer: no live games") == 2


def test_stale_after_scales_with_poll_s():
    fetch = FakeFetch()
    t = FakeTime()
    cfg = {**CFG, "poll_s": 1200}  # 2 x poll_s = 40 min > 30 min
    live = LiveSoccer(Recorder(), {}.get, cfg, fetch=fetch, clock=t.clock, sleep=t.sleep, monotonic=t.clock)
    live.refresh()
    t.now += 2399
    assert len(live.games()) == 2
    t.now += 2
    assert live.games() == []


def test_refresh_failure_signals_when_the_list_goes_stale():
    seen = []
    fetch, t = FakeFetch(), FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=fetch, clock=t.clock, sleep=t.sleep,
                      on_change=lambda: seen.append(1))
    live.refresh()
    fetch.fail = OSError("network down")
    t.now += 600
    live.refresh()
    assert seen == [1]  # still fresh: nothing changed
    t.now += 1800
    live.refresh()
    assert seen == [1, 1]  # the LIVE key must redraw dim
    t.now += 600
    live.refresh()
    assert seen == [1, 1]  # once


def test_retry_interval():
    assert [soccer.retry_interval(n, 180, 1800) for n in range(1, 8)] == [180, 360, 720, 1440, 1800, 1800, 1800]
    assert soccer.retry_interval(10_000, 180, 1800) == 1800


def _poll(live, times):
    """Run poll_once `times` times; the waits it returned."""
    return [live.poll_once() for _ in range(times)]


def test_failed_first_fetch_retries_after_poll_s():
    fetch = FakeFetch()
    fetch.fail = OSError("offline at boot")
    live, rec, st, t = _live(fetch=fetch)
    assert _poll(live, 1) == [180]  # not idle_poll_s


def test_poll_backs_off_on_consecutive_errors():
    fetch = FakeFetch()
    fetch.fail = OSError("API broken")
    live, rec, st, t = _live(fetch=fetch)
    waits = []
    for _ in range(6):
        waits.append(live.poll_once())
        t.sleep(waits[-1])  # the scheduler waits this long before the next run
    assert waits == [180, 360, 720, 1440, 1800, 1800]


def test_poll_backoff_resets_after_a_good_fetch():
    fetch = FakeFetch()
    outcomes = iter([OSError("a"), OSError("b"), None, OSError("c")])
    real = fetch.__call__

    def flaky(url):
        if "searchTerm=Paramount" in url:
            fetch.fail = next(outcomes)  # one outcome per refresh
        return real(url)

    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=flaky, clock=t.clock, sleep=t.sleep, monotonic=t.clock)
    waits = []
    for _ in range(4):
        waits.append(live.poll_once())
        t.sleep(waits[-1])  # the scheduler waits this long before the next run
    # fail, fail, ok (games live: poll_s), fail again: back to the first step
    assert waits == [180, 360, 180, 180]


def test_refresh_respects_max_age():
    fetch = FakeFetch()
    live, rec, st, t = _live(fetch=fetch)
    live.refresh()
    n = len(fetch.urls)
    t.now += 30
    live.refresh(max_age=60)
    assert len(fetch.urls) == n
    t.now += 31
    live.refresh(max_age=60)
    assert len(fetch.urls) == n + 2  # both shelves, tokens still fresh


def test_tokens_refetched_daily_and_on_4xx():
    fetch = FakeFetch()
    live, rec, st, t = _live(fetch=fetch)
    live.refresh()
    assert fetch.urls.count(CFG["token_page"]) == 1
    shelf = [u for u in fetch.urls if "searchTerm=" in u][0]
    assert "utsk=x" in shelf and "utscf=y" in shelf
    assert "caller=web&locale=en-US&pfm=web&sf=143441&v=100" in shelf

    t.now += 3600
    live.refresh()
    assert fetch.urls.count(CFG["token_page"]) == 1  # cached
    t.now += 86400
    live.refresh()
    assert fetch.urls.count(CFG["token_page"]) == 2  # a day later

    # A 4xx on a shelf: new tokens, then one retry.
    real, rejected = fetch.__call__, []

    def reject_once(url):
        if "searchTerm=" in url and not rejected:
            rejected.append(url)
            raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)
        return real(url)

    live._fetch = reject_once
    fetch.urls.clear()
    live.refresh()
    assert rejected and fetch.urls.count(CFG["token_page"]) == 1
    assert sum("searchTerm=" in u for u in fetch.urls) == 2  # both shelves refetched
    assert live.games() == []  # a day on, nothing in the fixtures is live any more


def test_vars():
    live, rec, st, t = _live()
    assert live.vars() == {"soccer_count": "0", "soccer_pos": "0/0"}
    live.refresh()
    assert live.vars() == {"soccer_count": "2", "soccer_pos": "1/2"}


def test_on_change_after_refresh_and_play():
    seen = []
    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=FakeFetch(), clock=t.clock, sleep=t.sleep,
                      on_change=lambda: seen.append(1))
    live.refresh()
    assert seen == [1]
    live.play(1)  # the cursor moved: {soccer_pos} must redraw
    assert seen == [1, 1]


def test_poll_once_survives_errors(monkeypatch):
    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=FakeFetch(), clock=t.clock, sleep=t.sleep)
    calls = []

    def boom(max_age=0.0):
        calls.append(max_age)
        if len(calls) == 1:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(live, "refresh", boom)
    assert [live.poll_once(), live.poll_once()] == [180, 180]  # nothing fetched yet: retry after poll_s
    assert len(calls) == 2


def test_constants():
    assert soccer.UTS_PARAMS == "caller=web&locale=en-US&pfm=web&sf=143441&v=100"
    assert soccer.QUERIES == ("Paramount+", "MLS")


# -- the key: pick at once, cycle, switch to the last pick ---------------------


def test_a_press_shows_the_pick_before_any_switching():
    seen = []
    live, rec, st, t = _live()
    live._on_change = lambda: seen.append(live.vars()["soccer_pos"])
    live.refresh()
    assert live.select() is not None
    assert seen[-1] == "1/2" and live.picking() and rec.calls == []


def test_presses_cycle_through_the_list_and_wrap():
    live, rec, st, t = _live()
    live.refresh()
    picks = []
    for _ in range(4):
        live.select()
        picks.append(live.vars()["soccer_pos"])
    assert picks == ["1/2", "2/2", "1/2", "2/2"]


def test_the_cycle_restarts_after_a_quiet_minute():
    live, rec, st, t = _live()
    live.refresh()
    live.select()
    live.select()
    t.now += soccer.CYCLE_S + 1
    assert not live.picking()
    live.select()
    assert live.vars()["soccer_pos"] == "1/2"


def test_while_a_started_game_is_on_screen_a_press_moves_on():
    live, rec, st, t = _live()
    live.press()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    t.now += soccer.CYCLE_S + 1
    assert live.watching(st.get) and live.picking()
    live.select()
    assert live.vars()["soccer_pos"] == "2/2"


def test_next_always_moves_on():
    live, rec, st, t = _live()
    live.refresh()
    t.now += soccer.CYCLE_S + 1
    live.select(advance=True)
    assert live.vars()["soccer_pos"] == "2/2"


def test_rapid_presses_switch_once_to_the_last_pick():
    live, rec, st, t = _live()
    live.refresh()
    first = live.select()
    second = live.select()
    live.switch(first)  # superseded: nothing happens
    assert rec.calls == []
    live.switch(second)
    played = [d["media_content_id"] for _, s, d in rec.calls if s == "play_media"]
    assert played == [live.games()[1].url]


def test_press_waits_for_the_presses_to_settle():
    live, rec, st, t = _live()
    live.press()
    assert t.sleeps[0] == soccer.SWITCH_AFTER_S
    assert [s for _, s, _ in rec.calls][0] == "play_media"


def test_a_newer_pick_cancels_the_select_check():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}  # a profile picker would get a select

    def sleep(seconds):
        t.sleep(seconds)
        if seconds == CFG["select_after_s"]:
            live.select()  # pressed again while the first game opens

    live._sleep = sleep
    live.switch(live.select())
    assert ("remote", "send_command") not in rec.names()


def test_the_pick_follows_its_game_when_the_list_changes():
    live, rec, st, t = _live()
    live.refresh()
    live.select()
    live.select()  # game 2 of 2
    picked = live.games()[1]
    live._games = [g for g in live._games if g is not live._games[0]]  # game 1 ended
    assert live.vars()["soccer_pos"] == "1/1"
    live.switch(live._gen)
    assert rec.calls[-1][2]["media_content_id"] == picked.url


def test_a_press_with_nothing_cached_fetches_first():
    live, rec, st, t = _live()
    live.press()
    assert live.vars()["soccer_pos"] == "1/2"
    assert [s for _, s, _ in rec.calls][0] == "play_media"


def test_each_pick_is_toasted_on_the_soccer_channel():
    shown = []
    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=FakeFetch(), clock=t.clock, sleep=t.sleep,
                      monotonic=t.clock, notify=lambda m, channel=None: shown.append((m, channel)))
    live.refresh()
    games = live.games()
    live.select()
    live.select()
    assert shown == [(f"1/{len(games)} {games[0].title}", "soccer"), (f"2/{len(games)} {games[1].title}", "soccer")]


def test_no_live_games_is_toasted():
    shown = []
    fetch, t = FakeFetch(), FakeTime()
    fetch.fail = OSError("down")
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=fetch, clock=t.clock, sleep=t.sleep,
                      monotonic=t.clock, notify=lambda m, channel=None: shown.append((m, channel)))
    live.start()
    assert shown == [("No live soccer games", "soccer")]


# -- Paramount+ cold start with the daemon's app tracker and dock input (PR #40) ----


class FakeClients:
    """atv_clients: Paramount+ alive until `quit()`, then removed at that time."""

    def __init__(self, t, status="alive", removes=True):
        self.t, self._status, self.removes, self._removed = t, status, removes, None

    def status(self, bundle):
        return self._status

    def removed_at(self, bundle):
        return self._removed

    def quit(self):
        if self.removes:
            self._status, self._removed = "gone", self.t.now


class FakeDock:
    def __init__(self):
        self.at = 0.0

    def last(self):
        return self.at


def _shared_live(clients=None, dock=None):
    live, rec, st, t = _live()
    clients = clients(t) if callable(clients) else clients
    handles = {"atv_clients": clients, "dock_input": dock}
    live._shared = handles.get
    live._quit_app = clients.quit if clients is not None else (lambda: None)
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}
    return live, rec, st, t


def test_cold_start_waits_for_the_quit_to_be_confirmed(capsys):
    live, rec, st, t = _shared_live(FakeClients)
    live.play(0)
    out = capsys.readouterr().out
    assert "quit com.cbsvideo.app for a cold start (confirmed)" in out
    assert [d["media_content_type"] for _, s, d in rec.calls if s == "play_media"] == ["app", "url"]


def test_cold_start_logs_an_unconfirmed_quit_and_opens_anyway(capsys):
    live, rec, st, t = _shared_live(lambda t: FakeClients(t, removes=False))
    live.play(0)
    assert "quit com.cbsvideo.app: not confirmed (alive); opening anyway" in capsys.readouterr().out
    assert [d["media_content_type"] for _, s, d in rec.calls if s == "play_media"] == ["app", "url"]


def test_cold_start_when_not_seen_running(capsys):
    # Paramount+ registers only once it plays: "gone" may still be running, so it is quit anyway.
    live, rec, st, t = _shared_live(lambda t: FakeClients(t, status="gone", removes=False))
    live.play(0)
    assert "quit com.cbsvideo.app for a cold start (was gone)" in capsys.readouterr().out


def test_dock_input_stops_the_selects(capsys):
    dock = FakeDock()
    live, rec, st, t = _shared_live(FakeClients, dock)
    real_sleep = t.sleep

    def sleep(seconds):
        real_sleep(seconds)
        if seconds == CFG["select_after_s"]:
            dock.at = t.now  # a key or knob while Paramount+ opens
    live._sleep = sleep
    live.play(0)
    assert ("remote", "send_command") not in rec.names()
    assert "com.cbsvideo.app: no select (dock input)" in capsys.readouterr().out


def test_input_change_stops_the_selects():
    live, rec, st, t = _shared_live(FakeClients)
    st["media_player.lg"] = {"s": "on", "a": {"source": "HDMI 3"}}
    live.play(0)
    assert ("remote", "send_command") not in rec.names()
