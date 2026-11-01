"""ad_detect: the heartbeat timeout follows the beat cadence (Pluto, 2026-11-01).

Pluto's ad players announce nothing at a pod's end; the only trace is the
pinned show clock's beat every 15-30 s stopping. The old 90 s wait unmuted
86 s after a break; 40 s (or 1.5 x the last gap) is the floor that still
tolerates one skipped 30 s beat without flapping.
"""

from ad_detect import AdDetector, PLAYING

CFG = {"speaker": "media_player.arc", "media_player": "media_player.atv", "apps": ["tv.pluto.ios"]}


def detector(calls):
    d = AdDetector(CFG, lambda *a: calls.append(a), lambda e: {"s": "playing", "a": {"app_id": "tv.pluto.ios"}})
    d._mute = lambda on: calls.append(("mute", on))  # synchronous for the test
    return d


def start_ad(d, t0=1000.0):
    d.on_stream_state("tv.pluto.ios", PLAYING, False, now=t0 - 60)
    d.on_show_clock("tv.pluto.ios", 856.0, 5000.0, now=t0)  # cue: clock published during playback
    assert d.ad


def test_timeout_is_40s_before_any_beat_gap_is_known():
    calls = []
    d = detector(calls)
    start_ad(d, 1000.0)
    d.tick(now=1039.0)
    assert d.ad
    d.tick(now=1041.0)
    assert not d.ad


def test_timeout_is_45s_after_a_30s_cadence():
    calls = []
    d = detector(calls)
    start_ad(d, 1000.0)
    d.on_show_clock("tv.pluto.ios", 856.0, 5030.0, now=1030.0)  # a 30 s beat
    d.tick(now=1074.0)
    assert d.ad  # 44 s after the last beat: still an ad (1.5 x 30 s = 45 s)
    d.tick(now=1076.0)
    assert not d.ad  # 46 s: ended
    assert ("mute", False) in calls


def test_timeout_stretches_with_a_longer_gap_and_caps_at_90s():
    calls = []
    d = detector(calls)
    start_ad(d, 1000.0)
    d.on_show_clock("tv.pluto.ios", 856.0, 5060.0, now=1060.0)  # one beat skipped: 60 s gap
    d.tick(now=1100.0)
    assert d.ad  # 40 s: not yet, the cadence looks like 60 s
    d.tick(now=1151.0)
    assert not d.ad  # 1.5 x 60 = 90 s: ended
    d2 = detector([])
    start_ad(d2, 1000.0)
    d2.on_show_clock("tv.pluto.ios", 856.0, 5100.0, now=1100.0)  # 100 s gap
    d2.tick(now=1189.0)
    assert d2.ad
    d2.tick(now=1191.0)
    assert not d2.ad  # capped at 90 s


def test_a_late_beat_after_the_timeout_restarts_the_ad():
    calls = []
    d = detector(calls)
    start_ad(d, 1000.0)
    d.tick(now=1041.0)
    assert not d.ad
    d.on_show_clock("tv.pluto.ios", 856.0, 5045.0, now=1045.0)  # the pod was still running
    assert d.ad
    assert calls[-1] == ("mute", True)
