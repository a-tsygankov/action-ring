"""ad_detect: our mute survives an app restart (deploy at 04:50 on 2026-11-07).

The old app muted a Peacock mid-roll and was replaced 10 s later; the new one
saw the ad still running but found the speaker muted, took it for the user's
mute and would not unmute at the ad's end. Each detector now keeps "the mute
is ours" in a file; a new one picks it up, and undoes it if no ad resumes.
"""

import json
import time

import pytest

import ad_detect
from ad_detect import FrozenClockDetector, PLAYING

APP = "com.peacocktv.peacock"
CFG = {"speaker": "media_player.arc", "media_player": "media_player.atv", "frozen_apps": [APP]}


class Now:  # runs the mute threads at once, in order
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def speaker(monkeypatch):
    monkeypatch.setattr(ad_detect.threading, "Thread", Now)
    return {"s": "playing", "a": {"source": "TV", "is_volume_muted": False}}


def detector(speaker, path):
    def call(domain, service, data):
        speaker["a"]["is_volume_muted"] = data["is_volume_muted"]

    states = {"media_player.arc": speaker, "media_player.atv": {"s": "playing", "a": {"app_id": APP}}}
    return FrozenClockDetector(CFG, call, states.get, state_file=path)


def ours(path, muted=True, age=0.0):
    path.write_text(json.dumps({"muted_by_us": muted, "at": time.time() - age}))


def test_the_file_follows_our_mute(speaker, tmp_path):
    path = tmp_path / "mute.json"
    d = detector(speaker, path)
    d._start("ad")
    assert speaker["a"]["is_volume_muted"] and json.loads(path.read_text())["muted_by_us"]
    d._end("over")
    assert not speaker["a"]["is_volume_muted"] and not json.loads(path.read_text())["muted_by_us"]


def test_a_new_app_undoes_its_predecessors_mute_when_the_ad_ends(speaker, tmp_path):
    path = tmp_path / "mute.json"
    ours(path)
    speaker["a"]["is_volume_muted"] = True  # muted by the old app at 04:50:36
    d = detector(speaker, path)
    d.on_player_state(APP, PLAYING, True, now=0.0)  # 04:50:50: the reconnect snapshot
    d.on_clock(APP, 993.0, 0.0, "ep", now=0.0, stamp=1000.0)
    assert d.ad
    d.on_player_state(APP, None, True, now=60.0)
    d.on_clock(APP, 993.5, 1.0, "ep", now=60.0, stamp=1060.0)
    assert not d.ad and not speaker["a"]["is_volume_muted"]


def test_an_inherited_mute_is_undone_when_no_ad_resumes(speaker, tmp_path):
    path = tmp_path / "mute.json"
    ours(path)
    speaker["a"]["is_volume_muted"] = True
    d = detector(speaker, path)
    d.tick(now=time.monotonic() + 5)
    assert speaker["a"]["is_volume_muted"]  # the ad may still show up
    d.tick(now=time.monotonic() + ad_detect.RESTORE_GRACE_S + 1)
    assert not speaker["a"]["is_volume_muted"] and not d.muted_by_us


def test_a_users_mute_is_still_left_alone(speaker, tmp_path):
    path = tmp_path / "mute.json"
    ours(path, muted=False)
    speaker["a"]["is_volume_muted"] = True
    d = detector(speaker, path)
    d._start("ad")
    d._end("over")
    d.tick(now=time.monotonic() + ad_detect.RESTORE_GRACE_S + 1)
    assert speaker["a"]["is_volume_muted"]


def test_an_old_file_is_ignored(speaker, tmp_path):
    path = tmp_path / "mute.json"
    ours(path, age=ad_detect.RESTORE_MAX_AGE_S + 60)
    assert not detector(speaker, path).muted_by_us


def test_a_broken_file_is_ignored(speaker, tmp_path):
    path = tmp_path / "mute.json"
    path.write_text("{not json")
    assert not detector(speaker, path).muted_by_us
