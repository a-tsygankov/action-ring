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
    assert ctx.fakes.redraws and 2876 < ctx.fakes.redraws[-1] < 2881


# --- the controller's additional cases ------------------------------------

def test_paused_film_is_ready_and_press_uses_paused_position():
    logs = []
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=2900, state="paused", logs=logs)
    ctx.fakes.time.now += 300                                  # with age added it would be past the scene
    assert svc.conditions()["ready"]() is True
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 2938})]
    assert "scenes: jump 2900s -> 2938s (scene at 2943s)" in logs


def test_scene_whose_lead_in_is_behind_is_not_ahead():
    svc, ctx, lk = ready(scenes=((100, 110),), pos=97)
    assert svc.conditions()["ready"]() is False
    svc.actions()["next"]({}); assert ctx.fakes.calls == []
    svc, ctx, lk = ready(scenes=((100, 110),), pos=94)
    assert svc.conditions()["ready"]() is True
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 95})]


def test_noticed_while_paused_then_played_past_last_scene_signals():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60, state="paused")
    assert svc.conditions()["ready"]() is True and ctx.fakes.signals == 1 and ctx.fakes.redraws == []
    set_state(ctx, "playing"); set_pos(ctx, 60)
    ctx.fakes.time.now += 2900                                 # playback at 2960, nothing redrew
    svc.watch()
    assert ctx.fakes.signals == 2 and svc.conditions()["ready"]() is False


def test_pause_resume_then_passing_last_scene_signals():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    set_state(ctx, "paused"); set_pos(ctx, 1000)
    svc.watch()
    ctx.fakes.time.now += 5000                                 # the first redraw fired long ago
    set_state(ctx, "playing"); set_pos(ctx, 1000)
    svc.watch()
    assert svc.conditions()["ready"]() is True and ctx.fakes.signals == 1
    assert 1937 < ctx.fakes.redraws[-1] < 1939                 # re-armed for the new moment
    ctx.fakes.time.now += 2000
    svc.watch()
    assert ctx.fakes.signals == 2 and svc.conditions()["ready"]() is False


def test_quiet_ticks_send_no_signal_and_no_new_redraws():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    signals, redraws = ctx.fakes.signals, len(ctx.fakes.redraws)
    for _ in range(5):
        ctx.fakes.time.now += 5
        assert svc.watch() == 5
    assert ctx.fakes.signals == signals and len(ctx.fakes.redraws) == redraws


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


# --- a film that blinks out keeps its snapshot for settle_s (I2) -----------

def test_brief_gap_keeps_snapshot_and_jump_memory():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010), (5000, 5010)), pos=60)
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 2938
    signals = ctx.fakes.signals
    set_state(ctx, "idle")                                     # loading right after the seek
    ctx.fakes.time.now += 5; svc.watch()
    assert svc.conditions()["ready"]() is False and ctx.fakes.signals == signals + 1   # gray meanwhile
    set_state(ctx, "playing"); set_pos(ctx, 2930)              # back, a little before the scene
    ctx.fakes.time.now += 5; svc.watch()
    assert svc.conditions()["ready"]() is True and ctx.fakes.signals == signals + 2
    assert len(lk.calls) == 1                                  # no second lookup, no settle wait
    assert svc._jump is not None
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 3995     # jump memory kept: on to the next scene


def test_title_briefly_missing_keeps_snapshot():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    a = ctx.state(ATV)["a"]
    a["media_title"] = ""; ctx.fakes.time.now += 5; svc.watch()
    a["media_title"] = "Tuner"; ctx.fakes.time.now += 5; svc.watch()
    assert svc.conditions()["ready"]() is True and len(lk.calls) == 1


def test_gap_longer_than_settle_drops_snapshot_and_looks_up_again():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    svc.actions()["next"]({})
    set_state(ctx, "idle")
    for _ in range(3):                                         # gone at +5, +10, +15: past settle_s
        ctx.fakes.time.now += 5; svc.watch()
    assert svc._snap is None and svc._jump is None
    set_state(ctx, "playing"); set_pos(ctx, 60)
    ctx.fakes.time.now += 5; svc.watch()
    assert svc.conditions()["ready"]() is False and len(lk.calls) == 1   # settling again
    ctx.fakes.time.now += 10; set_pos(ctx, 60); svc.watch()
    assert len(lk.calls) == 2 and svc.conditions()["ready"]() is True
    svc.actions()["next"]({})
    assert ctx.fakes.calls[-1][2]["seek_position"] == 2938     # jump memory went with the snapshot


def test_other_film_during_gap_drops_snapshot_at_once():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    set_state(ctx, "idle"); ctx.fakes.time.now += 5; svc.watch()
    ctx.state(ATV).update(atv(title="Other Film", dur=7000, pos=0, at=ctx.fakes.time.now)[ATV])
    ctx.fakes.time.now += 5; svc.watch()
    assert svc._snap is None


def test_non_finite_numbers_are_not_a_film_and_nothing_raises():
    for bad in ("nan", "inf", "-inf", float("nan"), float("inf")):
        svc, ctx, lk = make(scenes=((100, 110),), states=atv(dur=bad, pos=0))
        svc.watch(); ctx.fakes.time.now += 10; svc.watch()
        assert lk.calls == [] and svc.conditions()["ready"]() is False
    for bad in ("nan", float("inf")):
        svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
        ctx.state(ATV)["a"]["media_position"] = bad
        assert svc.conditions()["ready"]() is False
        svc.actions()["next"]({}); assert ctx.fakes.calls == []


def test_lookup_toast_is_off_by_default_and_reports_the_count_when_on():
    svc, ctx, lk = make(scenes=((2943, 2946), (4000, 4010)), states=atv(pos=60))
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert ctx.fakes.toasts == []                                   # toast_lookup defaults to off

    svc, ctx, lk = make(scenes=((2943, 2946), (4000, 4010)), states=atv(pos=60), cfg={"toast_lookup": True})
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert ctx.fakes.toasts == [("Scenes: 2 found for Tuner", None)]
    svc.watch(); svc.watch()
    assert len(ctx.fakes.toasts) == 1                               # once per lookup, not per tick

    svc, ctx, lk = make(scenes=((2943, 2946),), states=atv(pos=60), cfg={"toast_lookup": True})
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert ctx.fakes.toasts == [("Scenes: 1 found for Tuner", None)]

    svc, ctx, lk = make(scenes=(), states=atv(pos=60), cfg={"toast_lookup": True})
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert ctx.fakes.toasts == [("Scenes: none found for Tuner", None)]


def test_max_reports_the_film_name_as_artist_with_an_empty_title():
    # Seen live 2026-11-13: HBO Max sends media_title='' and media_artist='Weapons'.
    st = atv(title="", dur=7709, pos=60)
    st[ATV]["a"]["media_artist"] = "Weapons"
    svc, ctx, lk = make(scenes=((3286, 3329),), states=st)
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [("Weapons", 7709, "com.wbd.stream")]

    st = atv(title="Tuner", dur=6600, pos=60)                  # a real title always wins over the artist
    st[ATV]["a"]["media_artist"] = "Someone"
    svc, ctx, lk = make(scenes=(), states=st)
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [("Tuner", 6600, "com.wbd.stream")]
