"""ad_detect: Peacock pre-rolls at an episode start (captures 2026-11-05..07).

A new episode's first clock (elapsed 0, rate 0) is not a scrub even though it
is far from the last title's clock; it may come inside a set_state queue; the
pre-roll's end (republish + rate 1) alone must not start an ad; and a start
followed at once by an end must leave the speaker unmuted, whatever order the
mute calls run in.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import ad_detect
from ad_detect import AdDetector, FrozenClockDetector, PLAYING

PAUSED = 2
APP = "com.peacocktv.peacock"
CFG = {"speaker": "media_player.arc", "media_player": "media_player.atv", "frozen_apps": [APP]}
TOOLS = Path(__file__).resolve().parents[2] / "tools"


def detector(calls):
    d = FrozenClockDetector(CFG, lambda *a: calls.append(a), lambda e: {"s": "playing", "a": {"app_id": APP}})
    d._mute = lambda on: calls.append(("mute", on))
    return d


def watch_previous_episode(d):
    d.on_player_state(APP, PLAYING, True, now=0.0)
    d.on_clock(APP, 2400.0, 1.0, "ep-1", now=0.0)
    d.on_clock(APP, 2553.1, 0.0, "credits", now=153.0)  # 03:44:36: the episode ends


def test_a_new_episodes_pre_roll_is_not_taken_for_a_scrub():
    calls = []
    d = detector(calls)
    watch_previous_episode(d)
    d.on_player_state(APP, PAUSED, True, now=154.0)  # 02:25:38 Paused + commands
    d.on_player_state(APP, PLAYING, False, now=155.0)
    d.on_clock(APP, 0.0, 0.0, "ep-2", now=155.0)  # 02:25:39: the pre-roll
    d.on_player_state(APP, None, True, now=156.0)
    assert d.ad and calls[-1] == ("mute", True)
    d.on_player_state(APP, None, True, now=186.0)  # 02:26:10: the pre-roll ends
    d.on_clock(APP, 0.58, 1.0, "ep-2", now=186.0)
    assert not d.ad and calls[-1] == ("mute", False)


def test_a_scrub_within_one_episode_is_still_a_scrub():
    d = detector([])
    d.on_player_state(APP, PLAYING, True, now=0.0)
    d.on_clock(APP, 100.0, 1.0, "ep-1", now=0.0)
    d.on_player_state(APP, None, True, now=50.0)
    d.on_clock(APP, 900.0, 0.0, "ep-1", now=50.0)
    assert not d.ad


def test_back_in_the_same_episode_after_another_app_a_rewind_is_still_a_jump():
    d = detector([])
    d.on_player_state(APP, PLAYING, False, now=0.0)
    d.on_clock(APP, 298.6, 0.0, "ep-1", now=0.0)  # 22:21:48: paused, then YouTube
    d.on_clock(APP, 298.6, 0.0, "placeholder", now=24.0)  # 22:22:12: the app in the background
    d.on_player_state(APP, None, True, now=431.0)  # 22:28:59: back in Peacock
    d.on_player_state(APP, PLAYING, False, now=432.0)
    d.on_clock(APP, 288.3, 0.0, "ep-1", now=432.0)  # 22:29:00: resumed 10 s back
    assert not d.ad


def test_the_pre_roll_end_alone_does_not_start_an_ad():
    calls = []
    d = detector(calls)
    d.on_player_state(APP, PLAYING, False, now=0.0)
    d.on_clock(APP, 0.0, 0.0, now=0.0)  # missed: no republish near it
    assert not d.ad
    d.on_player_state(APP, None, True, now=31.0)  # 23:34:49: republish...
    d.on_clock(APP, 0.5, 1.0, now=31.0)  # ...and the clock runs
    assert not d.ad and calls == []


def test_a_pre_roll_clock_in_the_set_state_queue_is_seen():
    from pyatv.protocols.mrp import protobuf

    calls = []
    fz = detector(calls)
    on_set_state, _ = ad_detect._handlers(AdDetector({"speaker": "x"}, None, None), fz)

    def set_state(state=None, commands=False, clock=None):
        msg = protobuf.ProtocolMessage()
        msg.type = protobuf.SET_STATE_MESSAGE
        inner = protobuf.extract_inner(msg)
        inner.playerPath.client.bundleIdentifier = APP
        inner.playerPath.player.identifier = ad_detect.DEFAULT_PLAYER
        if state is not None:
            inner.playbackState = state
        if commands:
            inner.supportedCommands.supportedCommands.add().command = protobuf.CommandInfo_pb2.Play
        if clock is not None:
            item = inner.playbackQueue.contentItems.add()
            item.identifier = "ep-2"
            item.metadata.elapsedTime, item.metadata.playbackRate = clock
        asyncio.run(on_set_state(msg))

    set_state(PAUSED, True)  # 03:44:36
    set_state(PLAYING, clock=(0.0, 0.0))  # 03:44:37: Playing, the pre-roll's clock in the queue
    set_state(commands=True)
    assert fz.ad and calls[-1] == ("mute", True)


def test_mute_and_unmute_settle_on_the_latest_state_in_any_order(monkeypatch):
    speaker = {"s": "playing", "a": {"source": "TV", "is_volume_muted": False}}

    def call(domain, service, data):
        speaker["a"]["is_volume_muted"] = data["is_volume_muted"]

    started = []

    class Deferred:  # run the mute workers later, newest first
        def __init__(self, target, args=(), daemon=None):
            self.run = lambda: target(*args)

        def start(self):
            started.append(self)

    d = FrozenClockDetector(CFG, call, lambda e: speaker)
    monkeypatch.setattr(ad_detect.threading, "Thread", Deferred)
    d._start("pre-roll")
    d._end("clock running again")  # 23:34:49: both in the same millisecond
    for t in reversed(started):
        t.run()
    assert speaker["a"]["is_volume_muted"] is False and not d.muted_by_us


def test_the_capture_replays_with_every_pre_roll_caught():
    fixture = TOOLS / "fixtures" / "peacock_episodes_20261107.jsonl"
    out = subprocess.run([sys.executable, str(TOOLS / "replay_ad_detect.py"), str(fixture),
                          "--frozen-app", APP], capture_output=True, text=True, check=True).stdout
    starts = [ln.split()[0] for ln in out.splitlines() if "ad started" in ln]
    ends = [ln.split()[0] for ln in out.splitlines() if "ad ended" in ln]
    windows = list(zip(starts, ends))
    assert not [w for w in windows if w[0] == w[1]], windows  # no zero-length "ads"
    for pre_roll in ("20:29:03", "21:17:47", "22:16:19", "23:34:18", "00:24:49", "02:25:39", "03:44:37"):
        assert any(s == pre_roll for s, _ in windows), (pre_roll, windows)
