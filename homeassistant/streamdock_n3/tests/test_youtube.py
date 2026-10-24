"""YouTube commands and subtitle state (yt_chapters caption parsing, yt_lounge Screen/command)."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yt_chapters
import yt_lounge
from pyytlounge import State
from pyytlounge.events import SubtitlesTrackEvent
from yt_lounge import Screen, YouTubeScreens

FIXTURES = Path(__file__).parent / "fixtures"


def test_caption_languages_parses_tracks():
    page = (FIXTURES / "watch_captions.html").read_text(encoding="utf-8")
    # the page also lists an auto-generated "en" track: no duplicates
    assert yt_chapters.caption_languages(page) == ["de", "en"]


def test_caption_languages_without_captions():
    assert yt_chapters.caption_languages("<html>no captions here</html>") == []


def test_pick_caption_prefers_en_prefix():
    assert yt_chapters.pick_caption(["de", "en-US"]) == "en-US"
    assert yt_chapters.pick_caption(["de", "en"]) == "en"
    assert yt_chapters.pick_caption(["de"]) == "de"
    assert yt_chapters.pick_caption([]) is None
    assert yt_chapters.pick_caption(["de", "fr"], preferred="fr") == "fr"
    assert yt_chapters.pick_caption(["enm", "de"]) == "enm"  # "enm" is not English: first wins


def _screen(on_change=None) -> Screen:
    return Screen("tv", [], lambda vid: None, on_change)


def _subs(code):
    return SubtitlesTrackEvent({"videoId": "v", "languageCode": code})


def test_subtitle_event_sets_and_clears():
    screen = _screen()
    asyncio.run(screen.subtitles_track_changed(_subs("en")))
    assert screen.subtitles == "en"
    asyncio.run(screen.subtitles_track_changed(_subs("")))
    assert screen.subtitles is None
    asyncio.run(screen.subtitles_track_changed(_subs("de")))
    asyncio.run(screen.subtitles_track_changed(SubtitlesTrackEvent({"videoId": "v"})))
    assert screen.subtitles is None


def test_new_video_resets_subtitles():
    screen = _screen()
    asyncio.run(screen.subtitles_track_changed(_subs("en")))
    now = lambda vid: SimpleNamespace(video_id=vid, current_time=1.0, state=State.Playing)  # noqa: E731
    asyncio.run(screen.now_playing_changed(now("a")))
    assert screen.subtitles is None
    asyncio.run(screen.subtitles_track_changed(_subs("en")))
    asyncio.run(screen.now_playing_changed(now("a")))  # same video: kept
    assert screen.subtitles == "en"
    asyncio.run(screen.now_playing_changed(now("b")))
    assert screen.subtitles is None


def test_screen_events_call_on_change():
    calls = []
    screen = _screen(lambda: calls.append(1))
    asyncio.run(screen.subtitles_track_changed(_subs("en")))
    asyncio.run(screen.playback_state_changed(SimpleNamespace(current_time=2.0, state=State.Paused)))
    asyncio.run(screen.now_playing_changed(SimpleNamespace(video_id="v", current_time=0.0, state=State.Playing)))
    assert len(calls) == 3


# -- YouTubeScreens.command ---------------------------------------------------------


class FakeApi:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        async def record(*args):
            self.calls.append((name, *args))
            return True

        return record


@pytest.fixture
def yt(monkeypatch):
    monkeypatch.setattr(yt_lounge, "AUTH_FILE", Path("/nonexistent/yt_lounge.json"))
    changes = []
    screens = YouTubeScreens({"screens": {}}, lambda e: None, on_change=lambda: changes.append(1))
    screens.changes = changes
    for name in ("a", "b"):
        s = Screen(name, [], lambda vid: None, screens._notify)
        s.api, s.video_id, s.state = FakeApi(), f"vid_{name}", State.Paused
        screens.screens[name] = s
    return screens


def test_command_acts_on_playing_screen_first(yt):
    yt.screens["b"].state = State.Playing
    yt.command("next")
    assert yt.screens["b"].api.calls == [("next",)]
    assert yt.screens["a"].api.calls == []


def test_command_falls_back_to_first_live_screen(yt):
    yt.screens["a"].api = None  # not linked/connected
    yt.command("previous")
    assert yt.screens["b"].api.calls == [("previous",)]


def test_play_pause_follows_state(yt):
    a = yt.screens["a"]
    yt.command("play_pause")
    assert a.api.calls == [("play",)]
    a.state = State.Playing
    yt.command("play_pause")
    assert a.api.calls == [("play",), ("pause",)]


def test_command_without_live_session_logs(yt, capsys):
    for s in yt.screens.values():
        s.api = None
    yt.command("next")
    assert "youtube: next: no linked YouTube session is playing" in capsys.readouterr().out


def test_subtitles_toggle_off_when_on(yt):
    a = yt.screens["a"]
    a.subtitles = "en"
    yt.command("subtitles")
    assert a.api.calls == [("set_closed_captions", None, "vid_a")]
    assert a.subtitles is None
    assert not yt.subtitles_on()


def test_subtitles_toggle_on_picks_page_language(yt, monkeypatch):
    page = (FIXTURES / "watch_captions.html").read_text(encoding="utf-8")
    monkeypatch.setattr(yt_chapters, "fetch_page", lambda vid: page)
    a = yt.screens["a"]
    yt.command("subtitles")
    assert a.api.calls == [("set_closed_captions", "en", "vid_a")]
    assert a.subtitles == "en"  # optimistic: the TV may not echo
    assert yt.subtitles_on()


def test_subtitles_without_captions_does_nothing(yt, monkeypatch, capsys):
    monkeypatch.setattr(yt_chapters, "fetch_page", lambda vid: "<html></html>")
    yt.command("subtitles")
    assert yt.screens["a"].api.calls == []
    assert yt.screens["a"].subtitles is None
    assert "no captions" in capsys.readouterr().out


def test_unknown_command_raises(yt):
    with pytest.raises(ValueError):
        yt.command("explode")


def test_active_screen_and_playing(yt):
    assert not yt.playing()
    yt.screens["b"].state = State.Playing
    assert yt.playing()
    yt.screens["b"].api = None
    assert not yt.playing()

