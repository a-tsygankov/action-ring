"""soccer: live game list from the Apple TV app listing, play/next and watching state."""

import json
import urllib.error
from pathlib import Path

import pytest
import soccer
from soccer import Game, LiveSoccer, next_start, order, parse_games, parse_tokens, poll_interval

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
    # Paramount+ fixture: Charlton (clock running), Tottenham (inside the live
    # badge window) are live; Portsmouth ended, the rugby game is not soccer,
    # Cagliari is on another provider, Napoli has not started.
    pplus = parse_games(json.loads(_fixture("uts_search_paramount.json")), NOW)
    assert [g.id for g in pplus] == [
        "umc.cse.37en2vfv6drgjmfc8axpibi8a",
        "umc.cse.7cplj98aglynqjm12kcaj9i11",
    ]
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
    assert live.vars() == {"soccer_count": "3", "soccer_pos": "1/3"}


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
    wall = iter(NOW + 3600 * n for n in range(1000))  # a wall clock that keeps jumping (NTP)
    t = FakeTime()
    st = {"media_player.lg": {"s": "off", "a": {}}}
    live = LiveSoccer(Recorder(), st.get, CFG, fetch=FakeFetch(), clock=lambda: next(wall),
                      sleep=t.sleep, monotonic=t.clock)
    live.start()
    # The full 25 s wait despite the jumps: 2 s remote settle plus 0.5 s polls.
    assert t.sleeps[0] == 2 and len(t.sleeps) == 1 + 50


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
    live.next()
    assert live.vars()["soccer_pos"] == "3/3"
    live.next()
    assert live.vars()["soccer_pos"] == "1/3"
    played = [d["media_content_id"] for _, s, d in rec.calls if s == "play_media"]
    urls = [g.url for g in live.games()]
    assert played == [urls[0], urls[1], urls[2], urls[0]]


def test_play_sends_select_when_not_playing():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": P_PLUS}}  # e.g. a profile picker
    live.play(0)
    assert t.sleeps == [8]
    assert rec.calls[-1] == ("remote", "send_command", {"entity_id": "remote.atv", "command": "select"})
    assert rec.names().count(("remote", "send_command")) == 1


def test_play_no_select_when_playing():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "playing", "a": {"app_id": P_PLUS}}
    live.play(0)
    assert ("remote", "send_command") not in rec.names()


def test_play_no_select_when_other_app():
    live, rec, st, t = _live()
    live.refresh()
    st[ATV] = {"s": "idle", "a": {"app_id": "com.netflix.Netflix"}}
    live.play(0)
    assert ("remote", "send_command") not in rec.names()


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


def test_fetch_error_keeps_last_list(capsys):
    fetch = FakeFetch()
    live, rec, st, t = _live(fetch=fetch)
    live.refresh()
    first = live.games()
    assert len(first) == 3
    fetch.fail = OSError("network down")
    t.now += 600
    live.refresh()  # must not raise
    assert live.games() == first
    assert "soccer: refresh failed: OSError: network down" in capsys.readouterr().out


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
    assert live.vars() == {"soccer_count": "3", "soccer_pos": "1/3"}


def test_on_change_after_refresh_and_play():
    seen = []
    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=FakeFetch(), clock=t.clock, sleep=t.sleep,
                      on_change=lambda: seen.append(1))
    live.refresh()
    assert seen == [1]
    live.play(1)  # the cursor moved: {soccer_pos} must redraw
    assert seen == [1, 1]


def test_poll_forever_survives_errors(monkeypatch):
    t = FakeTime()
    live = LiveSoccer(Recorder(), {}.get, CFG, fetch=FakeFetch(), clock=t.clock, sleep=t.sleep)
    calls = []

    def boom(max_age=0.0):
        calls.append(max_age)
        if len(calls) == 1:
            raise RuntimeError("unexpected")
        if len(calls) == 3:
            raise SystemExit  # stop the loop for the test

    monkeypatch.setattr(live, "refresh", boom)
    with pytest.raises(SystemExit):
        live.poll_forever()
    assert len(calls) == 3
    assert t.sleeps == [1800, 1800]  # nothing live or about to start: idle interval


def test_constants():
    assert soccer.UTS_PARAMS == "caller=web&locale=en-US&pfm=web&sf=143441&v=100"
    assert soccer.QUERIES == ("Paramount+", "MLS")
