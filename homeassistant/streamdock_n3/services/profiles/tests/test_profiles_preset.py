"""The TV Room preset opens Apple TV apps through the service, clicking on
the apps that showed a picker on a cold launch (survey of 2026-11-14)."""

import json
from pathlib import Path

from logo import app_icons

ROOT = Path(__file__).resolve().parents[3]
PRESET = json.loads((ROOT / "presets" / "tv_room.json").read_text(encoding="utf-8"))
PAGES = PRESET["tabs"]["pages"]
CFG = PRESET["services"]["profiles"]
PICKER = {"com.netflix.Netflix", "com.amazon.aiv.AIVApp", "com.wbd.stream",
          "com.disney.disneyplus", "com.hulu.plus", "com.cbsvideo.app"}
# No picker (Tubi, the TV app), a sign-up promo where select signs up (Pluto),
# or a picker only sometimes, warm or cold, over a feed where select plays (YouTube).
LEFT_OUT = {"tv.pluto.ios", "com.adrise.tubitv", "com.apple.TVWatchList", "com.google.ios.youtube"}


def _app_keys():
    return [spec for page in ("apps", "apps_more") for spec in PAGES[page]["keys"].values()]


def test_clicks_on_the_apps_with_a_picker():
    assert CFG["dry_run"] is False
    assert {app for app, entry in CFG["apps"].items() if entry["picker"] is True} == PICKER
    assert not LEFT_OUT & set(CFG["apps"])
    assert CFG["apps"]["com.netflix.Netflix"]["undo"] is False   # it reports no duration


def test_delays_wait_for_the_picker():
    """Seconds from the press to the picker on the TV: Prime 3, Max, Hulu,
    Paramount+ 6, Netflix 7, Disney+ 11 (its avatars last)."""
    delay = {app: entry.get("delay_s", CFG["delay_s"]) for app, entry in CFG["apps"].items()}
    shown = {"com.amazon.aiv.AIVApp": 3, "com.wbd.stream": 6, "com.hulu.plus": 6,
             "com.cbsvideo.app": 6, "com.netflix.Netflix": 7, "com.disney.disneyplus": 11}
    for app, seconds in shown.items():
        assert seconds + 1.5 <= delay[app] <= seconds + 3, app


def test_only_prime_registers_at_launch():
    """Max, Hulu, Disney+, Paramount+ and Netflix register only at playback."""
    assert [a for a, e in CFG["apps"].items() if e.get("registers") == "launch"] == ["com.amazon.aiv.AIVApp"]


def test_apple_tv_app_keys_open_through_the_service():
    opened = []
    for spec in _app_keys():
        action = spec.get("action") or {}
        launch = action.get("launch")
        assert launch is None or launch["device"] == "lg"       # only the LG's own app is launched directly
        if action.get("service") == "profiles":
            assert action["do"] == "open" and isinstance(action["args"]["app"], str)
            opened.append(action["args"]["app"])
    assert len(opened) == 10 and len(set(opened)) == 10
    assert set(CFG["apps"]) <= set(opened)                       # every listed app has a key


def test_same_devices_as_the_launch_action():
    launch = PRESET["tabs"]["launch"]
    for name in ("atv", "atv_remote", "lg", "atv_source", "power_on_wait"):
        assert CFG[name] == launch[name]


def test_toasts_are_allowed():
    assert "profiles" in PRESET["toast"]["events"]


def test_logo_still_knows_the_app_icons():
    icons = app_icons(PAGES)
    assert icons["com.netflix.Netflix"] == "netflix" and icons["com.hulu.plus"] == "hulu"
    assert icons["com.webos.app.mediadiscovery"] == "lg_media_player"
