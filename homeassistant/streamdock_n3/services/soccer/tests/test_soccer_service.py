"""soccer service: LiveSoccer on the services contract (jobs, actions, conditions, keys, context)."""

from pathlib import Path

import pytest

from services import load
from services.soccer import SERVICE
from services.testing import fake_context

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed "now" for the fixtures (2026-10-24 14:33:20Z): three games are live.
NOW = 1792850000.0

ATV = "media_player.atv"
LG = "media_player.lg"
CFG = {
    "atv": ATV,
    "atv_remote": "remote.atv",
    "lg": LG,
    "atv_source": "Apple OTT",
    "power_on_wait": 25,
    "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
    "cache_s": 60,
    "poll_s": 180,
    "select_after_s": 8,
    "idle_poll_s": 1800,
}


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


HTTP = {
    "tv.apple.com": _fixture("tv_page_tokens.html"),
    "searchTerm=Paramount%2B": _fixture("uts_search_paramount.json"),
    "searchTerm=MLS": _fixture("uts_search_mls.json"),
}


def started(states=None):
    svc = SERVICE()
    st = {LG: {"s": "on", "a": {"source": "Apple OTT"}}} if states is None else states
    ctx = fake_context("soccer", dict(CFG), st, HTTP, now=NOW)
    svc.start(ctx)
    return svc, ctx


def test_loads_as_a_service():
    (svc,) = load({"soccer": dict(CFG)})
    assert svc.name == "soccer"


def test_entities_are_the_tv_and_the_apple_tv():
    svc, ctx = started()
    assert svc.entities() == {LG, ATV}


def test_refresh_job_fetches_and_reports_live():
    svc, ctx = started()
    (job,) = svc.jobs()
    assert (job.name, job.first_in, job.long_running) == ("refresh", 0, False)
    assert svc.conditions()["live"]() is False  # nothing fetched yet
    assert job.fn() == 180  # games live: poll_s
    assert svc.conditions()["live"]() is True
    assert svc.vars()["soccer_count"] == "2"
    assert svc.vars()["soccer_pos"] == "1/2"


def test_http_ops_are_named():
    svc, ctx = started()
    svc.jobs()[0].fn()
    ops = ctx._metrics.snapshot({}, {})["services"]["soccer"]["ops"]
    assert ops["http:token_page"]["total"]["count"] == 1
    assert ops["http:shelf"]["total"]["count"] == 2


def test_start_action_picks_toasts_and_plays():
    svc, ctx = started()
    svc.actions()["start"]({})
    game = svc.live.games()[0]
    assert ctx.fakes.toasts[0] == (f"1/2 {game.title}", "soccer")
    assert (
        "media_player", "play_media",
        {"entity_id": ATV, "media_content_type": "url", "media_content_id": game.url},
    ) in ctx.fakes.calls


def test_next_action_moves_on():
    svc, ctx = started()
    svc.actions()["start"]({})
    svc.actions()["next"]({})
    assert svc.vars()["soccer_pos"] == "2/2"
    assert [t for t, _ in ctx.fakes.toasts][1].startswith("2/2 ")


def test_pick_schedules_redraw_after_cycle():
    svc, ctx = started()
    svc.actions()["start"]({})
    assert ctx.fakes.redraws and ctx.fakes.redraws[0] == pytest.approx(60.1)


def test_picking_follows_the_pick():
    svc, ctx = started()
    svc.jobs()[0].fn()
    assert svc.conditions()["picking"]() is False
    svc.live.select()
    assert svc.conditions()["picking"]() is True
    ctx.fakes.time.now += 61
    assert svc.conditions()["picking"]() is False


def test_key_specs_match_previous_preset():
    svc = SERVICE()  # no start(): keys are static
    assert svc.key("live", {}) == {
        "icon": "soccer_live__none",
        "label": "",
        "modes": [
            {"when": "soccer.picking", "icon": "soccer_live__live", "label": "{soccer_pos}", "color": "#1d6b32"},
            {"when": "soccer.live", "icon": "soccer_live__live", "label": "{soccer_count} LIVE"},
        ],
        "action": {"service": "soccer", "do": "start"},
    }
    assert svc.key("next", {}) == {
        "label": "NEXT\\n{soccer_pos}",
        "color": "#1d6b32",
        "action": {"service": "soccer", "do": "next"},
    }


def test_context_turns_streaming_into_live_game_while_watching():
    states = {LG: {"s": "on", "a": {"source": "Apple OTT"}}}
    svc, ctx = started(states)
    assert svc.context("streaming") is None  # nothing started
    svc.actions()["start"]({})
    states[ATV] = {"s": "playing", "a": {"app_id": svc.live.games()[0].app}}
    assert svc.context("streaming") == "live_game"
    # Only the Apple TV's streaming layout turns: YouTube, an empty app (idle)
    # or another input keep theirs.
    for layout in ("idle", "youtube", "lg_app", "lg_media"):
        assert svc.context(layout) is None
    states[ATV] = {"s": "playing", "a": {"app_id": "com.netflix.Netflix"}}
    assert svc.context("streaming") is None  # another app took over


def test_context_survives_cleared_states():
    svc, ctx = started(states={})
    assert svc.context("streaming") is None and svc.conditions()["picking"]() is False


def test_unknown_key_is_none():
    svc, ctx = started()
    assert svc.key("nope", {}) is None
