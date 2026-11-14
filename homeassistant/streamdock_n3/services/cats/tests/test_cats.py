import random

import pytest

from services.cats import SERVICE
from services.cats.service import PAGE, YOUTUBE_APP
from services.testing import fake_context

ATV = "media_player.atv"
LG = "media_player.lg"
CFG = {"atv": ATV, "atv_remote": "remote.atv", "lg": LG, "atv_source": "Apple TV",
       "videos": ["vid1", "vid2", "vid3"]}


class FakeYouTube:
    def __init__(self, connect_after=None):
        self.polls = 0
        self.connect_after = connect_after  # polls until the session is up; None: never
        self.played = []

    def connected(self, screen):
        self.polls += 1
        return self.connect_after is not None and self.polls > self.connect_after

    def play_video(self, video, screen):
        self.played.append((video, screen))


def make(youtube=None, config=CFG, lg_on=True):
    states = {LG: {"s": "on" if lg_on else "off", "a": {"source": "Apple TV"}}}
    ctx = fake_context("cats", config, states=states, shared={"youtube": youtube} if youtube else None)
    svc = SERVICE(rng=random.Random(1))
    svc.start(ctx)
    return svc, ctx


def test_page_has_a_cat_on_every_key_for_ten_seconds():
    page = SERVICE().pages()[PAGE]
    assert page["close_on_press"] is True and page["open_s"] == 10
    assert page["keys"] == {str(i): {"service_key": "cats.face", "args": {"i": i}} for i in range(1, 7)}


def test_face_key_animates_with_its_own_images():
    svc, _ = make()
    spec = svc.key("face", {"i": 3})
    icons = [p["icon"].rsplit("/", 1)[1] for p in spec["dynamic"]["phases"]]
    assert icons == ["cat_3.png", "cat_3_blink.png", "cat_3.png", "cat_3_tongue.png"]
    assert spec["dynamic"]["warn_s"] == 0
    assert spec["action"] == {"service": "cats", "do": "play"}
    assert svc.key("face", {"i": 7}) is None
    assert svc.key("other", {}) is None


def test_all_images_exist_at_key_size():
    from PIL import Image

    svc, _ = make()
    for i in range(1, 7):
        for p in svc.key("face", {"i": i})["dynamic"]["phases"]:
            with Image.open(p["icon"]) as im:
                assert im.size == (112, 112)


def test_show_opens_the_page():
    svc, ctx = make()
    svc.actions()["show"]({})
    assert ctx.fakes.pages == ["cats.faces"]


def test_play_through_a_connected_youtube_session():
    yt = FakeYouTube(connect_after=0)
    svc, ctx = make(yt)
    svc.actions()["play"]({})
    assert len(yt.played) == 1 and yt.played[0][0] in CFG["videos"] and yt.played[0][1] == "atv"
    assert ctx.fakes.calls == []  # no app launch, no URL


def test_play_starts_youtube_then_sends_the_video_once_connected():
    yt = FakeYouTube(connect_after=3)
    svc, ctx = make(yt)
    svc.actions()["play"]({})
    assert ("media_player", "play_media",
            {"entity_id": ATV, "media_content_type": "app", "media_content_id": YOUTUBE_APP}) in ctx.fakes.calls
    assert len(yt.played) == 1


def test_play_falls_back_to_the_url_when_the_session_never_comes():
    yt = FakeYouTube(connect_after=None)
    svc, ctx = make(yt)
    svc.actions()["play"]({})
    assert yt.played == []
    domain, service, data = ctx.fakes.calls[-1]
    assert (domain, service, data["media_content_type"]) == ("media_player", "play_media", "url")
    assert data["media_content_id"].startswith("https://www.youtube.com/watch?v=vid")


def test_videos_rotate_without_repeating_back_to_back():
    yt = FakeYouTube(connect_after=0)
    svc, _ = make(yt)
    for _ in range(20):
        svc.actions()["play"]({})
    ids = [v for v, _ in yt.played]
    assert all(a != b for a, b in zip(ids, ids[1:]))
    assert set(ids) == set(CFG["videos"])


def test_no_videos_disables_the_service():
    with pytest.raises(ValueError):
        make(config={**CFG, "videos": []})
