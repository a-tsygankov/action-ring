"""Ad muting only while the TV shows the Apple TV input."""

import ad_detect

CFG = {"speaker": "media_player.arc", "media_player": "media_player.atv",
       "lg": "media_player.lg", "atv_source": "Apple OTT"}


def _detector(lg_source):
    calls = []
    states = {
        "media_player.arc": {"s": "on", "a": {"source": "TV", "is_volume_muted": False}},
        "media_player.lg": {"s": "on", "a": {"source": lg_source}},
    }
    det = ad_detect.AdDetector(CFG, lambda d, s, data: calls.append((s, data)), states.get)
    return det, calls


def test_mutes_on_apple_tv_input():
    det, calls = _detector("Apple OTT")
    det._mute(True)
    assert calls == [("volume_mute", {"entity_id": "media_player.arc", "is_volume_muted": True})]


def test_other_input_keeps_sound():
    det, calls = _detector("PS5")
    det._mute(True)
    assert calls == [] and not det.muted_by_us
