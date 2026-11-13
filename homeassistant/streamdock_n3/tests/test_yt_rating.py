"""yt_rating: like/dislike through the YouTube Data API, with a fake Google."""

import json
import os
import stat
import urllib.parse

import pytest

import yt_rating
from yt_rating import YouTubeRating

CLIENT = {"installed": {"client_id": "cid", "client_secret": "csecret", "auth_uri": "x"}}


class Google:
    """Answers in order per (method, path); records every request."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.replies: dict[tuple[str, str], list[tuple[int, dict]]] = {}

    def on(self, method, path, *replies):
        self.replies.setdefault((method, path), []).extend(replies)

    def __call__(self, method, url, form, headers):
        self.calls.append((method, url, form, headers))
        path = urllib.parse.urlsplit(url).path
        queue = self.replies.get((method, path))
        assert queue, f"unexpected {method} {url}"
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def paths(self):
        return [(m, urllib.parse.urlsplit(u).path, dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)))
                for m, u, _, _ in self.calls]


TOKEN = ("POST", "/token")
GET = ("GET", "/youtube/v3/videos/getRating")
RATE = ("POST", "/youtube/v3/videos/rate")


@pytest.fixture
def env(tmp_path):
    (tmp_path / "yt_oauth_client.json").write_text(json.dumps(CLIENT))
    g = Google()
    clock = {"t": 1000.0}
    state = {"video": "vid1", "changes": 0}

    def make(signed_in=True):
        if signed_in:
            (tmp_path / "yt_oauth.json").write_text(json.dumps({"refresh_token": "rt"}))
        return YouTubeRating(
            lambda: state["video"],
            on_change=lambda: state.__setitem__("changes", state["changes"] + 1),
            http=g, config_dir=tmp_path, clock=lambda: clock["t"],
            sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
            spawn=lambda fn: fn(),  # the background fetch runs inline
        )

    return g, clock, state, make, tmp_path


def signed_in_google(g, rating="none"):
    g.on(*TOKEN, (200, {"access_token": "at", "expires_in": 3600}))
    g.on(*GET, (200, {"items": [{"videoId": "vid1", "rating": rating}]}))
    g.on(*RATE, (204, {}))


def test_the_rating_is_fetched_once_per_video(env):
    g, _, state, make, _ = env
    signed_in_google(g, "like")
    r = make()
    assert r.liked()  # fetched inline here; on the device the first pass sees None
    assert not r.disliked()
    assert state["changes"] == 1
    assert [p[:2] for p in g.paths()] == [TOKEN, GET]
    assert g.calls[0][2] == {"client_id": "cid", "client_secret": "csecret",
                             "refresh_token": "rt", "grant_type": "refresh_token"}
    assert g.calls[1][3] == {"Authorization": "Bearer at"}
    assert g.paths()[1][2] == {"id": "vid1"}
    state["video"] = "vid2"
    r.rating()
    assert [p[:2] for p in g.paths()] == [TOKEN, GET, GET]  # the access token is reused


def test_like_toggles_and_dislike_replaces(env):
    g, _, state, make, _ = env
    signed_in_google(g, "like")
    r = make()
    r.rating()
    r.rate("like")  # liked already: clears it
    assert r.rating() == "none"
    r.rate("dislike")
    assert r.disliked()
    r.rate("like")
    assert r.liked()
    assert [p[2]["rating"] for p in g.paths() if p[:2] == RATE] == ["none", "dislike", "like"]


def test_a_refused_rating_falls_back(env, capsys):
    g, _, state, make, _ = env
    signed_in_google(g, "none")
    g.replies[RATE] = [(403, {"error": {"code": 403, "message": "quota exceeded"}})]
    r = make()
    r.rating()
    before = state["changes"]
    r.rate("like")
    assert r.rating() == "none"
    assert state["changes"] == before + 2  # optimistic look, then back
    assert "quota exceeded" in capsys.readouterr().out


def test_an_expired_access_token_is_refreshed_once(env):
    g, _, _, make, _ = env
    g.on(*TOKEN, (200, {"access_token": "old", "expires_in": 3600}), (200, {"access_token": "new", "expires_in": 3600}))
    g.on(*GET, (401, {}), (200, {"items": [{"rating": "dislike"}]}))
    r = make()
    assert r.disliked()
    assert [c[3].get("Authorization") for c in g.calls if "getRating" in c[1]] == ["Bearer old", "Bearer new"]


def test_a_revoked_sign_in_is_forgotten(env, capsys):
    g, _, _, make, tmp = env
    g.on(*TOKEN, (400, {"error": "invalid_grant"}))
    r = make()
    assert r.rating() is None
    assert not (tmp / "yt_oauth.json").exists()
    assert "sign in again" in capsys.readouterr().out
    assert r.rating() is None
    assert len(g.calls) == 1  # no more calls until a new sign-in


def test_a_failed_fetch_waits_before_asking_again(env):
    g, clock, _, make, _ = env
    g.on(*TOKEN, (200, {"access_token": "at", "expires_in": 3600}))
    g.on(*GET, (500, {}), (200, {"items": [{"rating": "like"}]}))
    r = make()
    assert r.rating() is None
    assert r.rating() is None
    assert sum("getRating" in c[1] for c in g.calls) == 1
    clock["t"] += yt_rating.RETRY_S + 1
    assert r.liked()


def test_not_signed_in_does_nothing(env, capsys):
    g, _, _, make, _ = env
    r = make(signed_in=False)
    assert r.rating() is None
    r.rate("like")
    r.rate("like")
    assert g.calls == []
    assert capsys.readouterr().out.count("not signed in") == 1


def test_no_video_does_nothing(env):
    g, _, state, make, _ = env
    state["video"] = None
    r = make()
    assert r.rating() is None
    r.rate("dislike")
    assert g.calls == []


def test_device_sign_in(env, capsys):
    g, _, state, make, tmp = env
    g.on("POST", "/device/code", (200, {"device_code": "dc", "user_code": "ABCD-EFGH",
                                        "verification_url": "https://www.google.com/device",
                                        "expires_in": 1800, "interval": 5}))
    g.on(*TOKEN, (428, {"error": "authorization_pending"}), (403, {"error": "slow_down"}),
         (200, {"access_token": "at", "expires_in": 3599, "refresh_token": "new-rt"}))
    g.on(*GET, (200, {"items": [{"rating": "none"}]}))
    r = make(signed_in=False)
    assert r.sign_in()
    out = capsys.readouterr().out
    assert "open https://www.google.com/device and enter ABCD-EFGH (within 30 min)" in out
    assert g.calls[0][2] == {"client_id": "cid", "scope": "https://www.googleapis.com/auth/youtube"}
    assert g.calls[1][2]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    saved = tmp / "yt_oauth.json"
    assert json.loads(saved.read_text()) == {"refresh_token": "new-rt"}
    if os.name == "posix":  # Windows has no owner-only mode bits
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert r.rating() == "none"
    assert sum(c[1].endswith("/token") for c in g.calls) == 3  # the access token came with the sign-in


def test_device_sign_in_denied(env, capsys):
    g, _, _, make, tmp = env
    g.on("POST", "/device/code", (200, {"device_code": "dc", "user_code": "X", "verification_url": "u",
                                        "expires_in": 60, "interval": 5}))
    g.on(*TOKEN, (403, {"error": "access_denied"}))
    r = make(signed_in=False)
    assert not r.sign_in()
    assert not (tmp / "yt_oauth.json").exists()
    assert "access_denied" in capsys.readouterr().out


def test_device_code_expires(env, capsys):
    g, _, _, make, _ = env
    g.on("POST", "/device/code", (200, {"device_code": "dc", "user_code": "X", "verification_url": "u",
                                        "expires_in": 12, "interval": 5}))
    g.on(*TOKEN, (428, {"error": "authorization_pending"}))
    assert not make(signed_in=False).sign_in()
    assert "expired" in capsys.readouterr().out


def test_sign_in_needs_the_client_file(env, capsys):
    g, _, _, make, tmp = env
    (tmp / "yt_oauth_client.json").unlink()
    assert not make(signed_in=False).sign_in()
    assert g.calls == [] and "yt_oauth_client.json" in capsys.readouterr().out


def test_a_flat_client_file_works(env):
    g, _, _, make, tmp = env
    (tmp / "yt_oauth_client.json").write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    signed_in_google(g)
    make().rating()
    assert g.calls[0][2]["client_id"] == "a"


def test_unknown_rating_kind():
    r = YouTubeRating(lambda: "v", http=lambda *a: (500, {}))
    with pytest.raises(ValueError):
        r.rate("love")
