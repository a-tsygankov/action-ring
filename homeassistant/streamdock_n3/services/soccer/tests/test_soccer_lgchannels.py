"""soccer: live games on LG Channels (LG's guide), tuning by number keys, and the women's filter."""

import base64
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

from services.soccer import lgchannels
from services.soccer.listing import is_womens, parse_games as parse_uts
from services.soccer.live import LiveSoccer

FIXTURES = Path(__file__).parent / "fixtures"
# A trimmed copy of the real guide (2026-11-14) with made-up programmes (ids
# TEST-*): "Boxeo En Vivo", "Fútbol Mexicano Liga Expansión (En Vivo)" and a
# women's live match on 807. The real 807 titles carry no live marker.
GUIDE = json.loads((FIXTURES / "lg_schedule.json").read_text(encoding="utf-8"))
CHANNELS = ["109", "807"]
LG = "media_player.lg"
ATV = "media_player.atv"
CFG = {
    "atv": ATV,
    "atv_remote": "remote.atv",
    "lg": LG,
    "atv_source": "Apple OTT",
    "power_on_wait": 25,
    "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
    "cache_s": 60,
    "poll_s": 180,
    "idle_poll_s": 1800,
    "select_after_s": 8,
    "lg_channels": CHANNELS,
    "lg_open_s": 12,
}


def _at(stamp: str) -> float:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc).timestamp()


PORTUGAL = _at("2026-11-14T17:30:00")  # 109 live; 807 shows boxing ("En Vivo")
LIGA_EXP = _at("2026-11-14T19:35:00")  # 807 live Liga Expansión; 109 a replay
WOMEN = _at("2026-11-14T21:30:00")  # 807: a women's live match only


def _ids(games):
    return [g.id for g in games]


# -- guide -------------------------------------------------------------------


def test_decode_reads_base64_zlib_and_plain_json():
    body = base64.b64encode(zlib.compress(json.dumps(GUIDE).encode())).decode()
    assert lgchannels.decode(body) == GUIDE
    assert lgchannels.decode(json.dumps(GUIDE)) == GUIDE


def test_live_marked_men_soccer_only():
    (game,) = lgchannels.parse_games(GUIDE, PORTUGAL, CHANNELS)
    assert game.title == "Portugal - Germany"
    assert game.league == "U19 EURO Qualifier 2026/27"
    assert game.channel == "109" and game.provider == "lgchannels" and game.app == lgchannels.APP
    assert game.kickoff == _at("2026-11-14T17:00:00")
    # Boxing on 807 is marked "En Vivo" at the same time but is not soccer.
    assert "TEST-BOXING-1" not in _ids(lgchannels.parse_games(GUIDE, PORTUGAL, ["807"]))


def test_replays_and_highlights_are_left_out():
    # 13:30: 109 shows a "Full Match" replay, 807 basketball: nothing live.
    assert lgchannels.parse_games(GUIDE, _at("2026-11-14T13:30:00"), CHANNELS) == []
    # 19:35: 109's replay is out, 807's "(En Vivo)" game is in.
    games = lgchannels.parse_games(GUIDE, LIGA_EXP, CHANNELS)
    assert _ids(games) == ["TEST-LIGAEXP-1"]
    assert games[0].channel == "807" and games[0].league == "ITV Deportes"


def test_womens_matches_are_left_out():
    assert lgchannels.parse_games(GUIDE, WOMEN, CHANNELS) == []


def test_only_configured_channels():
    assert lgchannels.parse_games(GUIDE, PORTUGAL, ["807"]) == []
    # 123 (news) runs "LiveNOW" all day: not a configured channel, and not soccer.
    assert lgchannels.parse_games(GUIDE, PORTUGAL, ["123"]) == []
    assert lgchannels.parse_games({}, PORTUGAL, CHANNELS) == []


def test_next_start_is_the_next_live_soccer_programme():
    assert lgchannels.next_start(GUIDE, _at("2026-11-14T13:30:00"), CHANNELS) == _at("2026-11-14T17:00:00")
    assert lgchannels.next_start(GUIDE, PORTUGAL, CHANNELS) == _at("2026-11-14T19:00:00")
    assert lgchannels.next_start(GUIDE, LIGA_EXP, CHANNELS) is None  # only the women's match is left


def test_tune_keys_pause_after_the_first_digit_and_end_with_enter():
    assert lgchannels.tune_keys("109") == [
        ("1", lgchannels.FIRST_DIGIT_S), ("0", lgchannels.DIGIT_S), ("9", lgchannels.DIGIT_S), ("ENTER", 0.0)
    ]
    assert lgchannels.tune_keys("") == []


def test_is_womens():
    for text in ("Women's Super League", "NWSL", "Liga MX Femenil", "Frauen-Bundesliga", "Arsenal (W)",
                 "Serie A Femminile", "D1 Arkema Féminine", "Primera División Femenina",
                 "Brasileirão Feminino", "Selección Femenina"):
        assert is_womens(text), text
    for text in ("Premier League", "Liga MX", "Bundesliga", "Werder Bremen", "Swansea"):
        assert not is_womens(text), text


def test_uts_listing_leaves_out_womens_games():
    item = {
        "id": "umc.cse.w", "title": "Chelsea vs. Arsenal", "sportName": "Soccer",
        "leagueName": "Women's Super League", "competitors": [{}, {}],
        "eventTime": {"gameKickOffStartTime": 0, "liveBadgeTime": {"startTime": 0, "endTime": 10**13}},
        "playables": [{"channelId": "tvs.sbd.10120", "punchoutUrls": {"play": "pplus://x"}}],
    }
    payload = {"data": {"shelf": {"items": [item]}}}
    assert parse_uts(payload, 1000) == []
    men = dict(item, leagueName="Premier League")
    assert _ids(parse_uts({"data": {"shelf": {"items": [men]}}}, 1000)) == ["umc.cse.w"]


# -- LiveSoccer with LG Channels ---------------------------------------------------


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, domain, service, data):
        self.calls.append((domain, service, data))


class FakeTime:
    def __init__(self, now):
        self.now = now
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


class Fetch:
    """The LG guide only; the Apple TV listing fails (as during an Apple outage)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, headers))
        if url == lgchannels.GUIDE:
            return base64.b64encode(zlib.compress(json.dumps(GUIDE).encode())).decode()
        raise OSError("no Apple listing in this test")


def _live(lg_app="com.webos.app.hdmi1", now=PORTUGAL):
    rec, t, fetch = Recorder(), FakeTime(now), Fetch()
    st = {LG: {"s": "on", "a": {"source": "HDMI 1", "app_id": lg_app}}}
    live = LiveSoccer(rec, st.get, CFG, fetch=fetch, clock=t.clock, sleep=t.sleep, monotonic=t.clock)
    return live, rec, st, t, fetch


def test_lg_games_listed_even_when_the_apple_listing_fails():
    live, rec, st, t, fetch = _live()
    live.refresh()
    assert [g.title for g in live.games()] == ["Portugal - Germany"]
    assert (lgchannels.GUIDE, {"x-device-country": "US", "x-device-language": "en"}) in fetch.calls


def test_lg_guide_is_fetched_once_per_lg_cache_s():
    live, rec, st, t, fetch = _live()
    live.refresh()
    t.now += 300
    live.refresh()
    assert sum(url == lgchannels.GUIDE for url, _ in fetch.calls) == 1
    t.now += 301
    live.refresh()
    assert sum(url == lgchannels.GUIDE for url, _ in fetch.calls) == 2


def test_live_games_follow_the_clock_from_the_cached_guide():
    live, rec, st, t, fetch = _live(now=_at("2026-11-14T16:58:00"))
    live.refresh()
    assert live.games() == []
    t.now = _at("2026-11-14T17:01:00")
    live.refresh(max_age=0)
    assert [g.channel for g in live.games()] == ["109"]


def test_start_relaunches_lg_channels_and_types_the_number():
    # Another input in front: LG Channels may still run in the background and
    # would resume full-screen playback, so it is closed first here too.
    live, rec, st, t, fetch = _live()
    live.start()
    assert rec.calls[:2] == [
        ("webostv", "command",
         {"entity_id": LG, "command": "system.launcher/close", "payload": {"id": lgchannels.APP}}),
        ("webostv", "command",
         {"entity_id": LG, "command": "system.launcher/launch", "payload": {"id": lgchannels.APP}}),
    ]
    buttons = [d["button"] for dom, svc, d in rec.calls if (dom, svc) == ("webostv", "button")]
    assert buttons == ["1", "0", "9", "ENTER"]
    # No Apple TV input switch and nothing played on the Apple TV.
    assert not any(svc in ("select_source", "play_media") for _, svc, _ in rec.calls)
    assert 12 in t.sleeps and lgchannels.FIRST_DIGIT_S in t.sleeps


def test_start_relaunches_lg_channels_when_in_front():
    # Over full-screen playback the first digit is swallowed: start from the home page.
    live, rec, st, t, fetch = _live(lg_app=lgchannels.APP)
    live.start()
    commands = [d["command"] for _, svc, d in rec.calls if svc == "command"]
    assert commands == ["system.launcher/close", "system.launcher/launch"]
    assert [d["button"] for _, svc, d in rec.calls if svc == "button"] == ["1", "0", "9", "ENTER"]
    assert t.sleeps.index(lgchannels.CLOSE_S) < t.sleeps.index(12)


def test_a_failed_close_still_launches(capsys):
    live, rec, st, t, fetch = _live()

    def call(domain, service, data):
        if data.get("command") == "system.launcher/close":
            raise RuntimeError("app not running")
        rec(domain, service, data)

    live._call = call
    live.start()
    assert [d["command"] for _, svc, d in rec.calls if svc == "command"] == ["system.launcher/launch"]
    assert [d["button"] for _, svc, d in rec.calls if svc == "button"] == ["1", "0", "9", "ENTER"]
    assert "close LG Channels: RuntimeError" in capsys.readouterr().out


def test_watching_an_lg_game_follows_the_lg_app():
    live, rec, st, t, fetch = _live()
    live.start()
    st[LG]["a"]["app_id"] = lgchannels.APP
    assert live.watching(st.get)
    st[LG]["a"]["app_id"] = "netflix"
    assert not live.watching(st.get)


def test_off_without_lg_channels_config():
    rec, t = Recorder(), FakeTime(PORTUGAL)
    fetch = Fetch()
    cfg = {k: v for k, v in CFG.items() if k != "lg_channels"}
    live = LiveSoccer(rec, {}.get, cfg, fetch=fetch, clock=t.clock, sleep=t.sleep, monotonic=t.clock)
    live.refresh()
    assert live.games() == []
    assert all(url != lgchannels.GUIDE for url, _ in fetch.calls)
