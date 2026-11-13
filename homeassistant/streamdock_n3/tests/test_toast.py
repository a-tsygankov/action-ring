"""toast: short messages on the LG over any input, and the events that send them."""

import json

import ad_detect
from ad_detect import FrozenClockDetector
from toast import EVENTS, Toaster
from yt_rating import YouTubeRating

LG = "media_player.lg"


class Calls(list):
    def __call__(self, domain, service, data):
        self.append((domain, service, data))


def toaster(tv="on", **cfg):
    calls, queued = Calls(), []
    states = {LG: {"s": tv, "a": {}}}
    t = Toaster(calls, states.get, {"lg": LG, **cfg}, sleep=lambda s: None, spawn=queued.append)
    return t, calls, queued, states


def run(queued):
    while queued:
        queued.pop(0)()


def messages(calls):
    return [d["payload"]["message"] for _, _, d in calls]


def test_a_toast_is_a_webos_create_toast_call():
    t, calls, queued, _ = toaster()
    t.show("Liked")
    assert calls == []  # sent in the background
    run(queued)
    assert calls == [("webostv", "command", {"entity_id": LG, "command": "system.notifications/createToast",
                                             "payload": {"message": "Liked"}})]


def test_nothing_is_sent_while_the_tv_is_off(capsys):
    for state in ("off", "unavailable", None):
        t, calls, queued, _ = toaster(tv=state)
        t.show("Liked")
        run(queued)
        assert calls == []
    assert "TV off; not shown: Liked" in capsys.readouterr().out


def test_a_run_of_presses_on_a_channel_shows_only_the_last():
    t, calls, queued, _ = toaster()
    for pos in (1, 2, 3):
        t.show(f"{pos}/8 game", channel="soccer")
    t.show("Ad: sound muted")  # no channel: not merged
    run(queued)
    assert messages(calls) == ["3/8 game", "Ad: sound muted"]
    t.show("4/8 game", channel="soccer")  # a later press shows again
    run(queued)
    assert messages(calls)[-1] == "4/8 game"


def test_text_is_one_short_line():
    t, calls, queued, _ = toaster()
    t.show("  two\nlines  ")
    t.show("x" * 500)
    t.show("   ")
    run(queued)
    assert messages(calls) == ["two lines", "x" * 120]


def test_events_can_be_turned_off():
    t, calls, queued, _ = toaster(events=["ad"])
    t.notifier("rating")("Liked")
    t.notifier("ad")("Ad: sound muted")
    run(queued)
    assert messages(calls) == ["Ad: sound muted"]
    assert toaster()[0].events == set(EVENTS)  # all by default


def test_a_failed_call_is_logged_not_raised(capsys):
    states = {LG: {"s": "on"}}

    def boom(*a):
        raise RuntimeError("HTTP 500")

    Toaster(boom, states.get, {"lg": LG}, spawn=lambda fn: fn()).show("Liked")
    assert "failed (RuntimeError: HTTP 500): Liked" in capsys.readouterr().out


# -- the events ------------------------------------------------------------------


class Google:
    def __init__(self, *replies):
        self.replies = list(replies)

    def __call__(self, method, url, form, headers):
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def rating(tmp_path, http, signed_in=True):
    (tmp_path / "yt_oauth_client.json").write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    if signed_in:
        (tmp_path / "yt_oauth.json").write_text(json.dumps({"refresh_token": "rt"}))
    clock = {"t": 0.0}
    shown, sign_in = [], []
    r = YouTubeRating(lambda: "vid", http=http, config_dir=tmp_path, clock=lambda: clock["t"],
                      sleep=lambda s: clock.__setitem__("t", clock["t"] + s), spawn=lambda fn: fn(),
                      notify=shown.append, notify_sign_in=sign_in.append)
    return r, shown, sign_in


def test_rating_toasts(tmp_path):
    ok = Google((200, {"access_token": "at", "expires_in": 3600, "items": [{"rating": "none"}]}))
    r, shown, _ = rating(tmp_path, ok)
    r.rating()
    r.rate("like")
    r.rate("like")
    r.rate("dislike")
    r.rate("dislike")
    assert shown == ["Liked", "Like removed", "Disliked", "Dislike removed"]


def test_rating_failure_toast(tmp_path):
    refused = Google((200, {"access_token": "at", "expires_in": 3600}),
                     (403, {"error": {"message": "forbidden"}}))
    r, shown, _ = rating(tmp_path, refused)
    r.rate("like")
    assert shown == ["Rating failed"]


def test_not_signed_in_toast(tmp_path):
    r, shown, _ = rating(tmp_path, Google((500, {})), signed_in=False)
    r.rate("dislike")
    assert shown == ["Like/dislike: not signed in"]


def test_the_sign_in_code_is_shown_and_repeated_each_minute(tmp_path):
    pending = (428, {"error": "authorization_pending"})
    replies = [(200, {"device_code": "dc", "user_code": "ABCD-EFGH", "interval": 5, "expires_in": 1800,
                      "verification_url": "https://www.google.com/device"})]
    replies += [pending] * 24  # two minutes
    replies += [(200, {"access_token": "at", "refresh_token": "rt2", "expires_in": 3600})]
    r, shown, sign_in = rating(tmp_path, Google(*replies), signed_in=False)
    assert r.sign_in()
    code = "YouTube sign-in: on a phone open google.com/device, code ABCD-EFGH"
    assert sign_in == [code, code, code, "YouTube sign-in done: like/dislike is on"]
    assert shown == []


def test_ad_mute_toasts(monkeypatch, tmp_path):
    class Now:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(ad_detect.threading, "Thread", Now)
    speaker = {"s": "playing", "a": {"source": "TV", "is_volume_muted": False}}

    def call(domain, service, data):
        speaker["a"]["is_volume_muted"] = data["is_volume_muted"]

    states = {"media_player.arc": speaker, "media_player.atv": {"s": "playing", "a": {"app_id": "x"}}}
    d = FrozenClockDetector({"speaker": "media_player.arc", "media_player": "media_player.atv",
                             "frozen_apps": ["x"]}, call, states.get, state_file=tmp_path / "m.json")
    shown = []
    d.notify = shown.append
    d._start("ad")
    d._end("over")
    assert shown == ["Ad: sound muted", "Ad over: sound on"]
    speaker["a"]["is_volume_muted"] = True  # the user muted: an ad changes nothing
    d._start("ad")
    d._end("over")
    assert shown == ["Ad: sound muted", "Ad over: sound on"]
