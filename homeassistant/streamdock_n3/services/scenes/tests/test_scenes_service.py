"""scenes service: noticing a film, the ready condition, the key and the press."""

import sqlite3
from datetime import datetime, timezone

from services.scenes import SERVICE
from services.scenes.lookup import Lookup
from services.testing import fake_context

ATV = "media_player.atv"
NOW = 1_000_000.0
CFG = {"atv": ATV}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def atv(state="playing", app="com.wbd.stream", title="Tuner", dur=6600, pos=0, ctype="tvshow", at=NOW):
    """The states dict for fake_context: one Apple TV whose position was reported at `at`."""
    a = {"app_id": app, "media_title": title, "media_duration": dur, "media_content_type": ctype,
         "media_position_updated_at": _iso(at)}
    if pos is not None:
        a["media_position"] = pos
    return {ATV: {"s": state, "a": a}}


class FakeLookup:
    def __init__(self, scenes):
        self.scenes = scenes
        self.calls = []
        self.on_run = None

    def run(self, title, runtime_s, app_id):
        self.calls.append((title, runtime_s, app_id))
        if self.on_run is not None:
            self.on_run()
        return self.scenes


def make(scenes=(), states=None, cfg=None, logs=None):
    ctx = fake_context("scenes", {**CFG, **(cfg or {})}, states=states if states is not None else atv(), now=NOW)
    if logs is not None:
        ctx._log = logs.append
    lk = FakeLookup(scenes)
    svc = SERVICE(lookup=lk)
    svc.start(ctx)
    return svc, ctx, lk


def set_pos(ctx, pos):
    """HA reports a new position now."""
    a = ctx.state(ATV)["a"]
    a["media_position"] = pos
    a["media_position_updated_at"] = _iso(ctx.fakes.time.now)


def set_state(ctx, state):
    ctx.state(ATV)["s"] = state


def ready(scenes, pos, app="com.wbd.stream", state="playing", logs=None):
    """make + settle; the position equals `pos` at the moment this returns."""
    svc, ctx, lk = make(scenes=scenes, states=atv(pos=pos, app=app, state=state), logs=logs)
    svc.watch()
    ctx.fakes.time.now += 10
    if pos is not None:
        set_pos(ctx, pos)
    svc.watch()
    return svc, ctx, lk


# --- the brief's cases -----------------------------------------------------

def test_film_noticed_after_settle_then_ready():
    svc, ctx, lk = make(scenes=((2943, 2946), (4000, 4010)), states=atv(pos=60))
    assert svc.watch() == 5 and lk.calls == []                 # settling
    ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [("Tuner", 6600, "com.wbd.stream")]
    assert svc.conditions()["ready"]() is True and ctx.fakes.signals == 1
    svc.watch(); assert len(lk.calls) == 1                     # same film: no second lookup


def test_press_seeks_to_next_scene_with_lead_and_steps_through():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 2938})]
    svc.actions()["next"]({})                                  # HA still reports the old position
    assert ctx.fakes.calls[-1][2]["seek_position"] == 3995
    assert svc.conditions()["ready"]() is False                # none after the last one
    svc.actions()["next"]({}); assert len(ctx.fakes.calls) == 2


def test_not_ready_cases():
    assert ready(scenes=((100, 110),), pos=500)[0].conditions()["ready"]() is False     # none ahead
    assert ready(scenes=((100, 110),), pos=None)[0].conditions()["ready"]() is False    # no position
    assert ready(scenes=((100, 110),), pos=0, app="com.netflix.Netflix")[0].conditions()["ready"]() is False
    assert ready(scenes=(), pos=0)[0].conditions()["ready"]() is False


def test_excluded_short_and_stopped_are_not_films():
    for st in (atv(app="com.google.ios.youtube"), atv(dur=1500), atv(state="idle"), atv(title="")):
        svc, ctx, lk = make(scenes=((100, 110),), states=st)
        svc.watch(); ctx.fakes.time.now += 10; svc.watch()
        assert lk.calls == []


def test_film_change_during_lookup_does_not_light_the_key():
    states = atv(pos=0)
    svc, ctx, lk = make(scenes=((100, 110),), states=states)
    lk.on_run = lambda: states.update(atv(title="Other Film", dur=7000, pos=0))   # film changes mid-lookup
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert svc.conditions()["ready"]() is False
    assert ctx.fakes.signals == 0


def test_film_ends_clears_snapshot_and_rewind_drops_jump_memory():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    svc.actions()["next"]({}); ctx.fakes.time.now += 6; set_pos(ctx, 600)       # user rewound far back
    svc.actions()["next"]({}); assert ctx.fakes.calls[-1][2]["seek_position"] == 2938
    signals = ctx.fakes.signals
    set_state(ctx, "idle"); svc.watch(); assert svc.conditions()["ready"]() is False
    assert ctx.fakes.signals == signals + 1


def test_key_spec_and_redraw_scheduled():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    k = svc.key("next", {})
    assert k["action"] == {"service": "scenes", "do": "next"} and k["modes"][0]["when"] == "scenes.ready"
    assert k["icon"] == ctx.icon("scene_off") and k["modes"][0]["icon"] == ctx.icon("scene") and k["label"] == ""
    assert svc.key("nope", {}) is None
    assert ctx.fakes.redraws and 2880 < ctx.fakes.redraws[-1] < 2885


# --- the controller's additional cases ------------------------------------

def test_paused_film_is_ready_and_press_uses_paused_position():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=2000, state="paused")
    ctx.fakes.time.now += 300                                  # paused: the position does not move
    assert svc.conditions()["ready"]() is True
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 2938})]


def test_press_never_seeks_backwards():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=2940)
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 2940


def test_title_with_empty_normalised_form_is_not_looked_up():
    svc, ctx, lk = make(scenes=((100, 110),), states=atv(title="Фильм"))
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [] and svc.conditions()["ready"]() is False


def test_movie_content_type_counts_without_duration():
    svc, ctx, lk = make(scenes=((100, 110),), states=atv(dur=None, ctype="movie"))
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [("Tuner", None, "com.wbd.stream")]


def test_lookup_raising_does_not_kill_the_job():
    logs = []
    svc, ctx, lk = make(scenes=((100, 110),), states=atv(pos=0), logs=logs)

    def boom():
        raise RuntimeError("x")
    lk.on_run = boom
    assert svc.watch() == 5
    ctx.fakes.time.now += 10
    assert svc.watch() == 5
    assert svc.conditions()["ready"]() is False
    assert any("RuntimeError" in line for line in logs)


def test_presses_after_real_playback_passed_the_jump_use_the_real_position():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010), (5000, 5010)), pos=60)
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 2938
    ctx.fakes.time.now += 20; set_pos(ctx, 2950)               # playback moved past the first scene
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 3995
    svc.actions()["next"]({})                                  # HA still at 2950: steps on from 4000
    assert ctx.fakes.calls[-1][2]["seek_position"] == 4995


def test_settle_restarts_when_the_film_changes():
    states = atv(pos=0)
    svc, ctx, lk = make(scenes=((100, 110),), states=states)
    svc.watch(); ctx.fakes.time.now += 5
    states.update(atv(title="Other Film", dur=7000, pos=0, at=ctx.fakes.time.now))
    svc.watch(); ctx.fakes.time.now += 5; svc.watch()
    assert lk.calls == []                                      # Other Film seen for 5 s only
    ctx.fakes.time.now += 5; svc.watch()
    assert lk.calls == [("Other Film", 7000, "com.wbd.stream")]


def test_app_that_cannot_seek_logs_once():
    logs = []
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=0, app="com.netflix.Netflix", logs=logs)
    svc.watch(); svc.watch()
    assert sum("app cannot seek: key stays gray" in line for line in logs) == 1
    assert any('film: "Tuner" 110 min in com.netflix.Netflix' in line for line in logs)


def test_jump_is_logged():
    logs = []
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60, logs=logs)
    svc.actions()["next"]({})
    assert "scenes: jump 60s -> 2938s (scene at 2943s)" in logs


def test_missing_or_bad_updated_at_uses_raw_position():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    ctx.state(ATV)["a"]["media_position_updated_at"] = "garbage"
    ctx.fakes.time.now += 100
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 2938
    del ctx.state(ATV)["a"]["media_position_updated_at"]
    assert svc.conditions()["ready"]() is False                # memory at 2943: nothing after it


def test_entities():
    svc, ctx, lk = make()
    assert svc.entities() == {ATV}


def test_database_cannot_be_opened_keeps_service_alive():
    logs = []
    ctx = fake_context("scenes", CFG, states=atv(pos=0), now=NOW)
    ctx._log = logs.append

    def broken(name):
        raise sqlite3.OperationalError("unable to open database file")
    ctx.store.sqlite = broken
    svc = SERVICE()
    svc.start(ctx)
    assert logs == ["scenes: database cannot be opened (OperationalError): lookups off"]
    assert svc.watch() == 5
    ctx.fakes.time.now += 10
    assert svc.watch() == 5
    assert svc.conditions()["ready"]() is False and ctx.fakes.urls == []


def test_start_builds_the_real_lookup():
    ctx = fake_context("scenes", CFG, now=NOW)
    ctx.store.sqlite = lambda name: sqlite3.connect(":memory:", check_same_thread=False)
    svc = SERVICE()
    svc.start(ctx)
    assert isinstance(svc._lookup, Lookup)
