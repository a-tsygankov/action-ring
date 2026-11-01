"""ad_detect.FrozenClockDetector: Peacock ads (capture 2026-11-01).

Peacock's own player stays Playing during an ad; the content clock is
published with playbackRate 0 and the supportedCommands republished within
seconds; the ad ends with a rate > 0 update. A scrub publishes rate-0 updates
with the clock jumping and no republish; a pause changes the state.
"""

from ad_detect import FrozenClockDetector, PLAYING

PAUSED = 2
APP = "com.peacocktv.peacock"
CFG = {"speaker": "media_player.arc", "media_player": "media_player.atv", "frozen_apps": [APP]}


def detector(calls):
    d = FrozenClockDetector(CFG, lambda *a: calls.append(a), lambda e: {"s": "playing", "a": {"app_id": APP}})
    d._mute = lambda on: calls.append(("mute", on))
    return d


def test_mid_roll_break_starts_and_ends_on_the_republished_rate_changes():
    calls = []
    d = detector(calls)
    d.on_player_state(APP, PLAYING, False, now=100.0)
    d.on_clock(APP, 438.2, 1.0, now=100.0)
    d.on_player_state(APP, None, True, now=139.0)  # 09:45:25: commands republished
    d.on_clock(APP, 476.1, 0.0, now=139.0)  # ...and the clock frozen at rate 0
    assert d.ad and calls[-1] == ("mute", True)
    d.tick(now=180.0)
    assert d.ad
    d.on_player_state(APP, None, True, now=193.0)  # 09:46:19
    d.on_clock(APP, 476.6, 1.0, now=193.0)
    assert not d.ad and calls[-1] == ("mute", False)


def test_pre_roll_with_the_republish_just_before_the_frozen_clock():
    calls = []
    d = detector(calls)
    d.on_player_state(APP, PAUSED, True, now=9.0)  # 09:42:09 Paused + commands
    d.on_player_state(APP, PLAYING, False, now=10.0)
    d.on_clock(APP, 0.0, 0.0, now=10.0)
    assert d.ad
    d.on_player_state(APP, None, True, now=42.0)
    d.on_clock(APP, 0.58, 1.0, now=42.0)
    assert not d.ad


def test_a_scrub_is_not_an_ad():
    calls = []
    d = detector(calls)
    d.on_player_state(APP, PLAYING, True, now=0.0)
    d.on_clock(APP, 0.58, 1.0, now=0.0)
    # 09:44:40-45: the clock jumps while rate-0 updates stream in, no republish
    for i, el in enumerate((137.6, 217.6, 277.6, 337.6, 387.6)):
        d.on_clock(APP, el, 0.0, now=118.0 + i)
    assert not d.ad
    d.on_clock(APP, 427.6, 1.0, now=123.0)
    assert not d.ad and calls == []


def test_a_rate_zero_update_right_after_a_republish_but_during_a_jump_is_ignored():
    d = detector([])
    d.on_player_state(APP, PLAYING, True, now=0.0)
    d.on_clock(APP, 100.0, 1.0, now=0.0)
    d.on_player_state(APP, None, True, now=50.0)
    d.on_clock(APP, 900.0, 0.0, now=50.0)  # jumped: a scrub landing, not an ad
    assert not d.ad


def test_pause_ends_an_ad_and_a_timeout_caps_it():
    calls = []
    d = detector(calls)
    d.on_player_state(APP, PLAYING, True, now=0.0)
    d.on_clock(APP, 100.0, 0.0, now=1.0)
    assert d.ad
    d.on_player_state(APP, PAUSED, False, now=20.0)
    assert not d.ad and calls[-1] == ("mute", False)
    d2 = detector([])
    d2.on_player_state(APP, PLAYING, True, now=0.0)
    d2.on_clock(APP, 100.0, 0.0, now=1.0)
    d2.tick(now=299.0)
    assert d2.ad
    d2.tick(now=302.0)
    assert not d2.ad
