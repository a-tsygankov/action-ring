"""Static checks of the shipped TV Room config (config.tv_room.json).

The config is data the daemon only validates at run time, on the device; these
tests catch a typo'd icon id, entity, bundle id or button route on the PC.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from keyspec import ICON_DIR, entities_of, icon_path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG = json.loads((APP_DIR / "config.tv_room.json").read_text(encoding="utf-8"))
TABS = CONFIG["tabs"]
PAGES = TABS["pages"]

ATV = "media_player.vugitv_4k_vugitv_4k"
LG = "media_player.lg_tv_2"
# Global Constraints entities, plus the HA scripts the config calls (Ruling R1).
KNOWN_ENTITIES = {
    ATV,
    "remote.vugitv_4k_vugitv_4k",
    LG,
    "media_player.tv_room_tv_room",
    "switch.tv_room_tv_room_night_sound",
    "switch.tv_room_tv_room_speech_enhancement",
    "switch.tv_room_tv_room_subwoofer_enabled",
    "switch.tv_room_tv_room_surround_enabled",
    "media_player.living_room_living_room",
    "media_player.kitchen_kitchen",
    "media_player.bathroom_bathroom",
    "media_player.boston_boston",
    "media_player.sonos_move",
    "script.tv_room_mute_toggle",
    "script.tv_room_power_toggle",
}
CONTEXT_LAYOUTS = ("idle", "streaming", "youtube", "lg_media", "lg_app", "live_game")
NAMED_CONDITIONS = {"youtube", "youtube_playing", "youtube_subtitles", "soccer_live"}
# (device, app) per launch key, from the Global Constraints bundle ids.
LAUNCH_IDS = {
    ("apps", "1"): ("atv", "com.netflix.Netflix"),
    ("apps", "2"): ("atv", "com.google.ios.youtube"),
    ("apps", "3"): ("atv", "com.amazon.aiv.AIVApp"),
    ("apps", "4"): ("atv", "com.apple.TVWatchList"),
    ("apps", "5"): ("lg", "com.webos.app.mediadiscovery"),
    ("apps", "6"): ("atv", "com.wbd.stream"),
    ("apps_more", "1"): ("atv", "com.hulu.plus"),
    ("apps_more", "2"): ("atv", "com.disney.disneyplus"),
    ("apps_more", "3"): ("atv", "com.cbsvideo.app"),
    ("apps_more", "4"): ("atv", "tv.pluto.ios"),
    ("apps_more", "5"): ("atv", "com.adrise.tubitv"),
}


def key_specs():
    """(page, key, spec) for every key on every page."""
    for page_name, page in PAGES.items():
        for key, spec in page["keys"].items():
            yield page_name, key, spec


def looks(spec: dict):
    """The spec itself, its state variants and its modes: everything that can set an icon."""
    yield spec
    for name in ("on", "off", "unavailable"):
        if isinstance(spec.get(name), dict):
            yield spec[name]
    yield from spec.get("modes") or []


def all_actions():
    """Every action in the config: key/mode actions and the top-level event map."""
    for _, _, spec in key_specs():
        for part in looks(spec):
            if "action" in part:
                yield part["action"]
    yield from CONFIG["actions"].values()


def action_entities(action) -> set[str]:
    """entity_id values in a shell `ha-call domain.service '<json>'` string or a dict action."""
    if isinstance(action, dict):
        return set(action.get("next_playing") or [])
    out: set[str] = set()
    for payload in re.findall(r"ha-call \S+ '(\{.*?\})'", action):
        ids = json.loads(payload).get("entity_id")
        out |= set(ids if isinstance(ids, list) else [ids] if ids else [])
    return out


def test_every_page_has_six_keys():
    for name, page in PAGES.items():
        assert sorted(page["keys"]) == ["1", "2", "3", "4", "5", "6"], name


def test_every_context_layout_has_a_page():
    for layout in CONTEXT_LAYOUTS:
        assert f"context:{layout}" in PAGES


def test_every_icon_exists():
    for page, key, spec in key_specs():
        for part in looks(spec):
            path = icon_path(part.get("icon"))
            if path is None:
                continue
            assert path.startswith(ICON_DIR + "/"), (page, key, path)
            local = APP_DIR / "icons" / path[len(ICON_DIR) + 1:]
            assert local.is_file(), (page, key, path)


def test_entities_are_known():
    used: set[str] = set()
    for _, _, spec in key_specs():
        used |= entities_of(spec)
    for action in all_actions():
        used |= action_entities(action)
    launch = TABS["launch"]
    used |= {launch["lg"], launch["atv"], launch["atv_remote"]}
    used |= {TABS["context"]["lg"], TABS["context"]["atv"]}
    soccer = CONFIG["soccer"]
    used |= {soccer["lg"], soccer["atv"], soccer["atv_remote"]}
    assert used - KNOWN_ENTITIES == set()
    assert "script.tv_room_power_toggle" in used  # the shell-string parser saw entities


def test_named_conditions_are_known():
    for page, key, spec in key_specs():
        for mode in spec.get("modes") or []:
            if isinstance(mode["when"], str):
                assert mode["when"] in NAMED_CONDITIONS, (page, key)


def test_buttons_routed():
    actions = CONFIG["actions"]
    for n in range(1, 7):
        assert actions[f"button.{n}.press"] == {"tab_key": n}
    for n in (7, 8):
        assert actions[f"button.{n}.press"] == {"tab_round": n}
    assert actions["button.9.press"] == (
        "ha-call remote.turn_on '{\"entity_id\":\"remote.vugitv_4k_vugitv_4k\"}'; sleep 2; "
        "ha-call media_player.turn_on '{\"entity_id\":\"media_player.vugitv_4k_vugitv_4k\"}'"
    )
    assert CONFIG["keys"] == {}


def test_launch_ids_match_constraints():
    for (page, key), (device, app) in LAUNCH_IDS.items():
        assert PAGES[page]["keys"][key]["action"] == {"launch": {"device": device, "app": app}}


def test_every_launch_action_is_well_formed():
    launches = [a["launch"] for a in all_actions() if isinstance(a, dict) and "launch" in a]
    assert len(launches) == len(LAUNCH_IDS)
    for req in launches:
        assert req["device"] in {"atv", "lg"}
        assert isinstance(req["app"], str) and req["app"]


def test_launch_config_complete():
    assert {"lg", "atv", "atv_remote", "atv_source", "power_on_wait"} <= set(TABS["launch"])


def test_round_keys_are_digits_and_pages_exist():
    assert TABS["round"]
    for button, cycle in TABS["round"].items():
        assert isinstance(button, str) and button.isdigit()
        assert isinstance(cycle, list) and cycle, button
        for page in cycle:
            assert page in PAGES, (button, page)


def test_soccer_config_complete():
    assert CONFIG["soccer"] == {
        "atv": ATV,
        "atv_remote": "remote.vugitv_4k_vugitv_4k",
        "lg": LG,
        "atv_source": "Apple OTT",
        "power_on_wait": 25,
        "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
        "cache_s": 60,
        "poll_s": 180,
        "select_after_s": 8,
        "idle_poll_s": 1800,
    }


def test_soccer_keys():
    assert PAGES["context:idle"]["keys"]["2"] == {
        "icon": "soccer_live__none",
        "label": "",
        "modes": [{"when": "soccer_live", "icon": "soccer_live__live"}],
        "action": {"soccer": "start"},
    }
    assert PAGES["context:live_game"]["keys"]["2"] == {
        "label": "NEXT\\n{soccer_pos}",
        "color": "#1d6b32",
        "action": {"soccer": "next"},
    }


def test_every_soccer_action_is_known():
    soccer = [a["soccer"] for a in all_actions() if isinstance(a, dict) and "soccer" in a]
    assert sorted(soccer) == ["next", "start"]
