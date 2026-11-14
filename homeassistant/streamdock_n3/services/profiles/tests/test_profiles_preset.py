"""The TV Room preset opens Apple TV apps through the service, switched off."""

import json
from pathlib import Path

from logo import app_icons

ROOT = Path(__file__).resolve().parents[3]
PRESET = json.loads((ROOT / "presets" / "tv_room.json").read_text(encoding="utf-8"))
PAGES = PRESET["tabs"]["pages"]
CFG = PRESET["services"]["profiles"]
NO_PICKER = {"tv.pluto.ios", "com.adrise.tubitv", "com.apple.TVWatchList"}


def _app_keys():
    return [spec for page in ("apps", "apps_more") for spec in PAGES[page]["keys"].values()]


def test_ships_switched_off():
    assert CFG["dry_run"] is True
    assert CFG["apps"] and all(entry["picker"] is False for entry in CFG["apps"].values())
    assert not NO_PICKER & set(CFG["apps"])
    assert CFG["apps"]["com.netflix.Netflix"]["undo"] is False   # it reports no duration


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
