"""profiles: press OK on a profile picker after a cold launch, and only then."""

import pytest

from services.profiles import SERVICE
from services.profiles.decide import decide
from services.testing import fake_context

ATV, REMOTE, LG = "media_player.atv", "remote.atv", "media_player.lg"
HULU, PRIME, NETFLIX, PLUTO = "com.hulu.plus", "com.amazon.aiv.AIVApp", "com.netflix.Netflix", "tv.pluto.ios"
SELECT = ("remote", "send_command", {"entity_id": REMOTE, "command": "select"})
MENU = ("remote", "send_command", {"entity_id": REMOTE, "command": "menu"})
T0 = 1_000_000.0


def cfg(**over):
    base = {
        "atv": ATV, "atv_remote": REMOTE, "lg": LG, "atv_source": "Apple OTT", "power_on_wait": 25,
        "dry_run": False,
        "apps": {
            PRIME: {"picker": True, "registers": "launch"},
            HULU: {"picker": True},
            NETFLIX: {"picker": True, "undo": False},
        },
    }
    base.update(over)
    return base


class Tracker:
    pump = staticmethod(lambda: None)   # runs due timeline events (set by make)

    def __init__(self):
        self.statuses = {}        # bundle -> status; default "gone"
        self.removed = {}
        self.session = T0 - 500
        self.raises = False

    def status(self, bundle):
        self.pump()
        if self.raises:
            raise RuntimeError("tracker broke")
        return self.statuses.get(bundle, "gone")

    def pid(self, bundle):
        return 8061 if self.statuses.get(bundle) == "alive" else None

    def removed_at(self, bundle):
        self.pump()
        return self.removed.get(bundle)

    def session_at(self):
        return self.session


class Input:
    pump = staticmethod(lambda: None)

    def __init__(self):
        self.at = 0.0

    def last(self):
        self.pump()
        return self.at


class States(dict):
    """The HA state cache; runs timeline events when the fake clock reaches them."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.clock = None
        self.events = []          # [(at, fn)], each run once

    def at(self, seconds_after_start, fn):
        self.events.append((T0 + seconds_after_start, fn))

    def pump(self):
        due = [e for e in self.events if e[0] <= self.clock()]
        for e in due:
            self.events.remove(e)
            e[1]()

    def get(self, key, default=None):
        self.pump()
        return super().get(key, default)


def make(config=None, *, lg="on", shared="default"):
    states = States({
        LG: {"s": lg, "a": {"source": "Apple OTT"}},
        ATV: {"s": "idle", "a": {"app_id": ""}},
    })
    tracker, inp = Tracker(), Input()
    if shared == "default":
        shared = {"atv_clients": tracker, "dock_input": inp}
    ctx = fake_context("profiles", config or cfg(), states=states, shared=shared, now=T0)
    states.clock = ctx.fakes.time.monotonic
    tracker.pump = inp.pump = states.pump
    svc = SERVICE()
    svc.start(ctx)
    return svc, ctx, states, tracker, inp


def remote_calls(ctx):
    return [c for c in ctx.fakes.calls if c[0] == "remote" and c[1] == "send_command"]


def launched(ctx, app):
    return ("media_player", "play_media",
            {"entity_id": ATV, "media_content_type": "app", "media_content_id": app}) in ctx.fakes.calls


def film(states, app, duration=7140, state="playing"):
    states[ATV] = {"s": state, "a": {"app_id": app, "media_duration": duration}}


# -- decide ------------------------------------------------------------------

@pytest.mark.parametrize("entry,status,opened,removed,dry,want", [
    (None, "gone", None, None, False, ("not listed", False)),
    ({"picker": False}, "gone", None, 5.0, False, ("not listed", False)),
    ({"picker": False, "registers": "launch"}, "gone", None, None, True, ("cold", True)),
    ({"picker": True}, "alive", None, None, False, ("warm", False)),
    ({"picker": True}, "unknown", None, 5.0, False, ("unknown", False)),
    ({"picker": True, "registers": "launch"}, "gone", None, None, False, ("cold", True)),
    ({"picker": True}, "gone", None, None, False, ("maybe alive", False)),
    ({"picker": True}, "gone", 10.0, None, False, ("maybe alive", False)),
    ({"picker": True}, "gone", 10.0, 5.0, False, ("maybe alive", False)),
    ({"picker": True}, "gone", 10.0, 20.0, False, ("cold", True)),
    ({"picker": True}, "gone", None, 20.0, False, ("cold", True)),
    ({"picker": True, "registers": "playback"}, "gone", None, None, False, ("maybe alive", False)),
    ("junk", "gone", None, None, False, ("not listed", False)),
])
def test_decide(entry, status, opened, removed, dry, want):
    assert decide(entry, status, opened_at=opened, removed_at=removed, dry_run=dry) == want


# -- open: the decision ------------------------------------------------------

def test_warm_launch_only_launches():
    svc, ctx, _, tracker, _ = make()
    tracker.statuses[PRIME] = "alive"
    svc.open({"app": PRIME})
    assert launched(ctx, PRIME) and remote_calls(ctx) == []
    assert ctx.fakes.time.now == T0          # no wait at all


def test_cold_launch_clicks_after_the_delay():
    svc, ctx, states, _, _ = make()
    seen = []
    states.at(5.9, lambda: seen.append(list(remote_calls(ctx))))
    svc.open({"app": PRIME})
    assert seen == [[]]                      # nothing sent before the delay ran out
    assert remote_calls(ctx) == [SELECT]
    assert ctx.fakes.calls.index(SELECT) > 0 and launched(ctx, PRIME)
    assert ctx.fakes.toasts == []


def test_per_app_delay():
    svc, ctx, states, _, _ = make(cfg(apps={PRIME: {"picker": True, "registers": "launch", "delay_s": 2}}))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]
    assert ctx.fakes.time.now < T0 + 2 + 5 + 1   # 2 s delay, then the 5 s watch


def test_playback_app_needs_a_removal_after_the_last_open():
    svc, ctx, _, tracker, _ = make()
    svc.open({"app": HULU})                  # never seen: no evidence
    assert remote_calls(ctx) == []
    svc.open({"app": HULU})                  # opened by the dock, no removal since
    assert remote_calls(ctx) == []
    tracker.removed[HULU] = ctx.fakes.time.now + 1
    ctx.fakes.time.now += 60
    svc.open({"app": HULU})                  # its process ended after our open
    assert remote_calls(ctx) == [SELECT]


def test_unknown_status_never_clicks():
    svc, ctx, _, tracker, _ = make()
    tracker.statuses[PRIME] = "unknown"
    svc.open({"app": PRIME})
    assert launched(ctx, PRIME) and remote_calls(ctx) == []


def test_app_not_listed_only_launches():
    svc, ctx, _, _, _ = make()
    svc.open({"app": PLUTO})
    assert launched(ctx, PLUTO) and remote_calls(ctx) == []


@pytest.mark.parametrize("args", [{}, {"app": 5}, {"app": ""}, {"app": None}])
def test_bad_args_do_nothing(args):
    svc, ctx, _, _, _ = make()
    svc.open(args)
    assert ctx.fakes.calls == []


def test_tracker_error_still_launches():
    svc, ctx, _, tracker, _ = make()
    tracker.raises = True
    svc.open({"app": PRIME})
    assert launched(ctx, PRIME) and remote_calls(ctx) == []


def test_no_tracker_only_launches():
    svc, ctx, _, _, _ = make(shared={})
    svc.open({"app": PRIME})
    assert launched(ctx, PRIME) and remote_calls(ctx) == []


def test_tv_stays_off():
    svc, ctx, _, _, _ = make(lg="off")
    svc.open({"app": PRIME})
    assert not launched(ctx, PRIME) and remote_calls(ctx) == []


# -- waking the TV -----------------------------------------------------------

def _wake(states):
    def on():
        states[LG] = {"s": "on", "a": {"source": "Apple OTT"}}
    states.at(4, on)


def test_wake_with_an_old_session_does_not_click():
    svc, ctx, states, tracker, _ = make(lg="off")
    _wake(states)
    svc.open({"app": PRIME})                 # the session is from before the wake
    assert launched(ctx, PRIME) and remote_calls(ctx) == []


def test_wake_with_a_new_synced_session_clicks():
    svc, ctx, states, tracker, _ = make(lg="off")
    _wake(states)
    tracker.statuses[PRIME] = "unknown"

    def reconnected():
        tracker.session = ctx.fakes.time.now
        tracker.statuses.pop(PRIME)
    states.at(3, reconnected)
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


# -- the checks after the delay ----------------------------------------------

def test_dock_input_during_the_wait_cancels():
    svc, ctx, states, _, inp = make()
    states.at(3, lambda: setattr(inp, "at", ctx.fakes.time.now))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == []


def test_input_right_after_the_press_is_the_press_itself():
    svc, ctx, states, _, inp = make()
    inp.at = T0 + 0.4                        # the key's release event
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


def test_connection_lost_during_the_wait_cancels():
    svc, ctx, states, tracker, _ = make()
    states.at(3, lambda: tracker.statuses.update({PRIME: "unknown"}))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == []


def test_app_ended_during_the_wait_cancels():
    svc, ctx, states, tracker, _ = make()
    states.at(3, lambda: tracker.removed.update({PRIME: ctx.fakes.time.now}))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == []


def test_lg_input_changed_cancels():
    svc, ctx, states, _, _ = make()
    states.at(3, lambda: states.update({LG: {"s": "on", "a": {"source": "PS5"}}}))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == []


@pytest.mark.parametrize("state", ["playing", "paused"])
def test_app_already_playing_or_paused_cancels(state):
    svc, ctx, states, _, _ = make()
    states.at(3, lambda: film(states, PRIME, state=state))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == []


def test_another_app_playing_does_not_cancel():
    svc, ctx, states, _, _ = make()
    film(states, HULU, state="paused")       # the Apple TV still reports the old app
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


def test_a_newer_open_cancels_the_older_one():
    svc, ctx, states, tracker, _ = make()
    tracker.statuses[HULU] = "alive"
    states.at(3, lambda: svc.open({"app": HULU}))
    svc.open({"app": PRIME})
    assert launched(ctx, HULU) and remote_calls(ctx) == []


def test_two_cold_opens_send_one_click():
    svc, ctx, states, tracker, _ = make(cfg(apps={
        PRIME: {"picker": True, "registers": "launch"}, NETFLIX: {"picker": True, "registers": "launch"}}))
    states.at(3, lambda: svc.open({"app": NETFLIX}))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


# -- the safety net ----------------------------------------------------------

def test_film_after_the_click_is_undone_once():
    svc, ctx, states, _, _ = make()
    states.at(8, lambda: film(states, PRIME))        # 2 s after the click
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT, MENU]
    assert ctx.fakes.toasts == [("Opened by mistake: went back", None)]
    assert ctx.fakes.time.now < T0 + 9               # stops watching at once


@pytest.mark.parametrize("duration", [27, None, "x", True, 299])
def test_short_or_missing_duration_is_not_a_film(duration):
    svc, ctx, states, _, _ = make()
    states.at(8, lambda: film(states, PRIME, duration))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT] and ctx.fakes.toasts == []


def test_another_apps_film_is_not_undone():
    svc, ctx, states, _, _ = make()
    states.at(8, lambda: film(states, HULU))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


def test_film_after_the_watch_window_is_left_alone():
    svc, ctx, states, _, _ = make()
    states.at(11.5, lambda: film(states, PRIME))     # 5.5 s after the click
    svc.open({"app": PRIME})
    ctx.state(ATV)                                   # let the late event run
    assert remote_calls(ctx) == [SELECT]


def test_undo_false_never_sends_menu():
    svc, ctx, states, _, _ = make(cfg(apps={NETFLIX: {"picker": True, "registers": "launch", "undo": False}}))
    states.at(8, lambda: film(states, NETFLIX))
    svc.open({"app": NETFLIX})
    assert remote_calls(ctx) == [SELECT]


def test_dock_input_during_the_watch_stops_it():
    svc, ctx, states, _, inp = make()
    states.at(7, lambda: setattr(inp, "at", ctx.fakes.time.now))
    states.at(8, lambda: film(states, PRIME))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


def test_newer_open_during_the_watch_stops_it():
    svc, ctx, states, tracker, _ = make()
    tracker.statuses[HULU] = "alive"
    states.at(7, lambda: svc.open({"app": HULU}))
    states.at(8, lambda: film(states, PRIME))
    svc.open({"app": PRIME})
    assert remote_calls(ctx) == [SELECT]


# -- dry run, log, history ---------------------------------------------------

def test_dry_run_decides_and_sends_nothing(capsys):
    svc, ctx, states, _, _ = make(cfg(dry_run=True, apps={PRIME: {"picker": False, "registers": "launch"}}))
    svc.open({"app": PRIME})
    assert launched(ctx, PRIME) and remote_calls(ctx) == []
    assert ctx.fakes.time.now == T0
    out = capsys.readouterr().out
    assert f"profiles: open {PRIME}: (dry run) gone -> cold, would click in 6.0s" in out


def test_log_lines(capsys):
    svc, ctx, states, tracker, _ = make()
    tracker.statuses[HULU] = "alive"
    svc.open({"app": HULU})
    states.at(70, lambda: film(states, PRIME))
    ctx.fakes.time.now += 60
    svc.open({"app": PRIME})
    out = capsys.readouterr().out
    assert f"profiles: open {HULU}: alive (pid 8061) -> warm, no click" in out
    assert f"profiles: open {PRIME}: gone -> cold, click in 6.0s" in out
    assert f"profiles: {PRIME}: click sent" in out
    assert f"profiles: {PRIME}: playback 7140s after click -> menu sent" in out


def test_cancel_reason_is_logged(capsys):
    svc, ctx, states, _, inp = make()
    states.at(3, lambda: setattr(inp, "at", ctx.fakes.time.now))
    svc.open({"app": PRIME})
    assert f"profiles: {PRIME}: no click (dock input)" in capsys.readouterr().out


def test_history_records_each_open_and_is_capped():
    svc, ctx, states, tracker, _ = make()
    tracker.statuses[HULU] = "alive"
    svc.open({"app": HULU})
    svc.open({"app": PRIME})
    hist = ctx.store.load("history")
    assert [(h["app"], h["status"], h["reason"], h["outcome"]) for h in hist] == [
        (HULU, "alive", "warm", "no click"), (PRIME, "gone", "cold", "clicked")]
    assert hist[0]["at"] == T0
    for _ in range(120):
        svc.open({"app": HULU})
    assert len(ctx.store.load("history")) == 100


def test_entities_and_no_render_surface():
    svc, ctx, _, _, _ = make()
    assert svc.entities() == {ATV, LG}
    assert svc.key("x", {}) is None
