"""Static checks of the shipped TV Room config (presets/tv_room.json).

The config is data the daemon only validates at run time, on the device; these
tests catch a typo'd icon id, entity, bundle id or button route on the PC.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from dynamic import phase_specs
from keyspec import ICON_DIR, entities_of, icon_path
from services.soccer.service import SoccerService

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG = json.loads((APP_DIR / "presets" / "tv_room.json").read_text(encoding="utf-8"))
TABS = CONFIG["tabs"]
PAGES = TABS["pages"]
SERVICES = {"soccer": SoccerService()}  # key() needs no start()

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
NAMED_CONDITIONS = {"youtube", "youtube_playing", "youtube_subtitles", "youtube_chapters", "youtube_liked",
                    "youtube_disliked", "soccer.live", "soccer.picking"}
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


def service_key(slot: dict) -> dict | None:
    """What a {"service_key": "<service>.<key>"} slot shows (as ServiceHost.key_spec:
    the slot's other fields on top), or None."""
    svc, _, key = slot["service_key"].partition(".")
    spec = SERVICES[svc].key(key, slot.get("args", {}))
    return None if spec is None else {**spec, **{k: v for k, v in slot.items() if k not in ("service_key", "args")}}


def key_specs():
    """(page, key, spec) for every key on every page; a service key slot as the spec its service gives."""
    for page_name, page in PAGES.items():
        for key, spec in page["keys"].items():
            yield page_name, key, service_key(spec) if "service_key" in spec else spec


def looks(spec: dict):
    """The spec itself, its state variants and its modes, and those of each
    dynamic phase: everything that can set an icon."""
    for part in phase_specs(spec):
        yield part
        for name in ("on", "off", "unavailable"):
            if isinstance(part.get(name), dict):
                yield part[name]
        yield from part.get("modes") or []


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


EMPTY_KEYS = {"context:idle": {"4"}, "context:streaming": {"4"}, "context:lg_media": {"4"},
              "context:lg_app": {"4"}}  # left blank on purpose (Night moved to tab 2A)


def test_every_page_has_six_keys():
    for name, page in PAGES.items():
        assert sorted({*page["keys"], *EMPTY_KEYS.get(name, ())}) == ["1", "2", "3", "4", "5", "6"], name
        assert not set(page["keys"]) & EMPTY_KEYS.get(name, set()), name


def _icons(spec):
    return {spec.get("icon"), *(m.get("icon") for m in spec.get("modes", []))}


def test_night_and_subtitles_only_on_tab_2a():
    for name, page in PAGES.items():
        for key, spec in page["keys"].items():
            icons = _icons(spec)
            if icons & {"night__off", "night__on", "subtitles__off", "subtitles__on"}:
                assert name == "sonos", (name, key)
    assert CONFIG["tabs"]["round"]["8"][0] == "sonos"  # 2A: round button 2, one click
    sonos = PAGES["sonos"]["keys"]
    assert sonos["1"]["icon"] == "night__off"
    assert sonos["5"]["action"] == {"youtube": "subtitles"}
    assert sonos["5"]["modes"] == [{"when": "youtube_subtitles", "icon": "subtitles__on"}]


BACK_FWD = [("skip_back_10", "skip_fwd_10"), ("rewind__1x", "fast_forward__1x"), ("prev_video", "next_video")]


def test_back_and_forward_keys_pair_on_one_row():
    for name, page in PAGES.items():
        if not name.startswith("context:"):
            continue
        icon = {k: spec.get("icon") for k, spec in page["keys"].items()}
        for back, fwd in BACK_FWD:
            # A dynamic key that takes turns between back and forward is its own pair.
            both = [k for k, spec in page["keys"].items()
                    if {back, fwd} <= {p.get("icon") for p in phase_specs(spec)}]
            if both:
                continue
            at = [k for k, i in icon.items() if i == back]
            if at:
                assert at[0] in ("2", "4") and icon[str(int(at[0]) + 1)] == fwd, (name, back)
            elif fwd in icon.values():
                assert False, (name, fwd, "has no back key beside it")


def test_open_tabs_close_after_5_s():
    assert CONFIG["tabs"]["timeouts"]["open"] == 5
    assert CONFIG["tabs"]["timeouts"]["after_press"] == 5


def test_every_context_layout_has_a_page():
    for layout in CONTEXT_LAYOUTS:
        assert f"context:{layout}" in PAGES


def test_every_logo_icon_exists():
    logo = TABS["logo"]
    for icon in [logo["default"], *logo["sources"].values(), *logo["apps"].values()]:
        assert (APP_DIR / "icons" / f"{icon}.png").is_file(), icon


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
    soccer = CONFIG["services"]["soccer"]
    used |= {soccer["lg"], soccer["atv"], soccer["atv_remote"]}
    assert used - KNOWN_ENTITIES == set()
    assert "script.tv_room_power_toggle" in used  # the shell-string parser saw entities


def test_named_conditions_are_known():
    for page, key, spec in key_specs():
        for mode in (m for part in phase_specs(spec) for m in part.get("modes") or []):
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
    assert CONFIG["services"]["soccer"] == {
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
    assert PAGES["context:idle"]["keys"]["2"] == {"service_key": "soccer.live"}
    assert PAGES["apps_more"]["keys"]["6"] == {"service_key": "soccer.live"}
    assert PAGES["context:live_game"]["keys"]["4"] == {"service_key": "soccer.next"}
    assert "soccer" not in CONFIG  # the service's section lives under "services"


def test_service_keys_resolve():
    slots = [(page, key, spec) for page, p in PAGES.items() for key, spec in p["keys"].items()
             if "service_key" in spec]
    assert len(slots) == 3
    for page, key, slot in slots:
        svc, _, name = slot["service_key"].partition(".")
        assert svc in CONFIG["services"], (page, key)
        assert SoccerService().key(name, slot.get("args", {})) is not None, (page, key)


def test_every_soccer_action_is_known():
    soccer = [a["do"] for a in all_actions() if isinstance(a, dict) and a.get("service") == "soccer"]
    assert set(soccer) == {"next", "start"}  # "start" sits on the context page and on apps_more
    assert set(soccer) <= set(SoccerService().actions())


def test_dockerfile_copies_every_module():
    copied = " ".join(l for l in (APP_DIR / "Dockerfile").read_text().splitlines() if l.startswith("COPY"))
    for module in APP_DIR.glob("*.py"):
        if module.name == "conftest.py":  # test-only, not shipped
            continue
        assert module.name in copied, f"{module.name} missing from the Dockerfile COPY lines"


def test_dockerfile_copies_the_services_package():
    lines = (APP_DIR / "Dockerfile").read_text().splitlines()
    assert "COPY services /opt/streamdock/services" in lines  # next to ha_daemon.py, importable as "services"


def test_youtube_page_has_dynamic_next_previous_and_like_dislike():
    keys = PAGES["context:youtube"]["keys"]
    assert [(p["icon"], p["action"]) for p in phase_specs(keys["2"])] == [
        ("next_video", {"youtube": "next"}), ("prev_video", {"youtube": "previous"})]
    assert [(p["icon"], p["action"], p["modes"]) for p in phase_specs(keys["3"])] == [
        ("thumb_up__off", {"youtube": "like"}, [{"when": "youtube_liked", "icon": "thumb_up__on"}]),
        ("thumb_down__off", {"youtube": "dislike"}, [{"when": "youtube_disliked", "icon": "thumb_down__on"}]),
    ]
    assert CONFIG["youtube"]["rating"] is True
    for icon in ("prev_video", "next_video", "thumb_up__off", "thumb_up__on"):  # nowhere else on it
        assert sum(icon in {p.get("icon") for p in looks(spec)} for spec in keys.values()) <= 1


def test_every_youtube_action_is_known():
    # ha_daemon imports the device SDK; read its command list from the source.
    import ast
    tree = ast.parse((APP_DIR / "ha_daemon.py").read_text())
    known = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "YOUTUBE_COMMANDS")
    used = {a["youtube"] for a in all_actions() if isinstance(a, dict) and "youtube" in a}
    assert used <= set(known), used - set(known)
    assert {"like", "dislike", "next", "previous"} <= used


def test_toast_config():
    from toast import EVENTS

    toast = CONFIG["toast"]
    assert toast["lg"] == LG
    # every event the app sends is on, except ad mute / unmute (too noisy)
    assert set(toast["events"]) == set(EVENTS) - {"ad"}
    for action in all_actions():
        if isinstance(action, dict) and "toast" in action:
            assert isinstance(action["toast"], str) and action["toast"].strip()
