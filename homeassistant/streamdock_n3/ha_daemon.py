"""streamdock-n3 daemon plus Home Assistant integration.

Runs the upstream daemon unchanged and adds:

1. Live state on LCD keys. One thread subscribes to Home Assistant
   (Supervisor WebSocket proxy, app token) and redraws every key whose config
   has a "state_entity". Per-state overrides of label/color:

       "1": {"label": "Night", "color": "#3b3f9e",
             "state_entity": "switch.tv_room_tv_room_night_sound",
             "on":  {"label": "Night\\nON", "color": "#3b5bdb"},
             "off": {"label": "Night\\nOFF", "color": "#262626"},
             "unavailable": {"label": "Night\\n?", "color": "#5c3b00"}}

   "off"/"standby" use "off"; unavailable/unknown (or HA unreachable) use
   "unavailable"; any other state ("on", "playing", "idle", ...) uses "on",
   so media players work as well as switches. Missing overrides fall back to
   the key's base label/color.

2. Accelerated Apple TV knobs (knob_accel.py). An action of the form
   {"atv_knob": {"axis": "horizontal"|"vertical", "direction": 1|-1}} goes to
   the knob engine instead of a shell command; the top-level "atv" config
   names the remote/media_player entities and tuning values.

3. Key modes. A key may list "modes"; the first whose "when" holds sets the
   key's icon/label/color and its press action, else the key's own
   "action"/look apply. Map the press with {"key_action": <key>}.
   "when" is "youtube" (YouTube in the foreground on a configured screen) or
   a list of {"entity", "attr" ("state" by default), "in": [...]} (any).

4. YouTube (yt_lounge.py): {"youtube": "next_chapter"} jumps to the next
   chapter of the video playing in a linked YouTube TV app.

5. Now-playing log (np_log.py): "now_playing_log": {"entities": [...]} logs
   what changes in those media players, to find ad markers per app.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from knob_accel import AppleTVKnobs
from streamdock_n3 import config as configmod
from streamdock_n3 import daemon, icons, paths
from websockets.sync.client import connect

WS_URL = "ws://supervisor/core/websocket"
RETRY_SECONDS = 5
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

OFF_STATES = {"off", "standby"}
UNKNOWN_STATES = {None, "unavailable", "unknown"}

_ready = threading.Event()
_device = None
_draw_lock = threading.Lock()
_knobs: AppleTVKnobs | None = None
_youtube: Any = None  # yt_lounge.YouTubeScreens
_np_log: Any = None  # np_log.NowPlayingLog
_keys: dict[str, dict] = {}  # config "keys", for key modes
_shown_mode: dict[int, int] = {}  # key -> index of the mode on screen (-1 = base)

# entity_id -> {"s": state, "a": attributes}, kept current by _watch().
_states: dict[str, dict[str, Any]] = {}
_states_lock = threading.Lock()


def state_of(entity_id: str) -> dict[str, Any] | None:
    with _states_lock:
        st = _states.get(entity_id)
        return {"s": st.get("s"), "a": dict(st.get("a") or {})} if st else None


# -- upstream hooks ------------------------------------------------------------

_upstream_apply_icons = daemon.apply_icons
_upstream_run_actions = daemon.run_actions


def _apply_icons(device, config):
    # Static icons first; state redraws must not race the initial paint.
    global _device
    _upstream_apply_icons(device, config)
    _device = device
    _ready.set()


def _run_actions(actions: Any, *, dry_run: bool) -> None:
    if isinstance(actions, dict) and "key_action" in actions:
        key = str(actions["key_action"])
        mode = _active_mode(_keys.get(key, {}))
        chosen = mode[1].get("action") if mode else _keys.get(key, {}).get("action")
        _run_actions(chosen, dry_run=dry_run)
        return
    if isinstance(actions, dict) and "youtube" in actions:
        command = actions["youtube"]
        if dry_run or _youtube is None or command != "next_chapter":
            print(f"youtube: ignoring {command!r}", flush=True)
            return
        # Seeking waits on the network; keep the SDK reader thread free.
        threading.Thread(target=_safe, args=(_youtube.next_chapter,), daemon=True).start()
        return
    if isinstance(actions, dict) and "atv_knob" in actions:
        if dry_run:
            print(f"atv-knob (dry run): {actions['atv_knob']}", flush=True)
        elif _knobs is None:
            print("atv-knob: no \"atv\" section in config; ignoring", flush=True)
        else:
            _knobs.detent(actions["atv_knob"])
        return
    _upstream_run_actions(actions, dry_run=dry_run)


def _safe(fn) -> None:
    try:
        fn()
    except Exception as err:
        print(f"action failed: {type(err).__name__}: {err}", flush=True)


daemon.apply_icons = _apply_icons
daemon.run_actions = _run_actions


# -- key modes -------------------------------------------------------------------


def _when(cond: Any) -> bool:
    if cond == "youtube":
        return _youtube is not None and _youtube.any_active()
    if isinstance(cond, list):
        from yt_lounge import matches

        return any(matches(state_of, c) for c in cond)
    return False


def _active_mode(item: dict) -> tuple[int, dict] | None:
    for i, mode in enumerate(item.get("modes") or []):
        if isinstance(mode, dict) and _when(mode.get("when")):
            return i, mode
    return None


def _mode_entities() -> set[str]:
    out: set[str] = set()
    for item in _keys.values():
        for mode in item.get("modes") or []:
            if isinstance(mode.get("when"), list):
                out |= {c["entity"] for c in mode["when"]}
    return out


def _refresh_modes() -> None:
    for key_str, item in _keys.items():
        if not item.get("modes") or not key_str.isdigit():
            continue
        key = int(key_str)
        mode = _active_mode(item)
        index = mode[0] if mode else -1
        if _shown_mode.get(key) == index:
            continue
        _shown_mode[key] = index
        look = mode[1] if mode else item
        print(f"key {key}: mode {look.get('label', index)!r}", flush=True)
        if mode is None and item.get("state_entity"):
            # Back to the key's live state look (on/off/unavailable).
            _draw(key, item, (_states.get(item["state_entity"]) or {}).get("s"))
        else:
            _draw_look(key, look, item)


def _draw_look(key: int, look: dict, base: dict) -> None:
    icon = look.get("icon")
    if icon and Path(icon).exists():
        with _draw_lock:
            _device.set_key_image(key, icon)
            _device.refresh()
        return
    _draw(key, {"label": look.get("label", base.get("label", key)),
                "color": look.get("color", base.get("color"))}, "on")


# -- Core REST calls -------------------------------------------------------------


class CoreAPI:
    """Blocking service calls over one kept-alive connection to the Supervisor."""

    def __init__(self) -> None:
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        body = json.dumps(data)
        headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None:
                    self._conn = http.client.HTTPConnection("supervisor", 80, timeout=10)
                try:
                    self._conn.request(
                        "POST", f"/core/api/services/{domain}/{service}", body, headers
                    )
                    resp = self._conn.getresponse()
                    text = resp.read()
                except (http.client.HTTPException, OSError):
                    # Usually a kept-alive connection the server closed; the
                    # request was not processed, so one retry is safe.
                    self._conn.close()
                    self._conn = None
                    if attempt == 2:
                        raise
                    continue
                if resp.status >= 300:
                    raise RuntimeError(f"{domain}.{service}: HTTP {resp.status} {text[:200]!r}")
                return


# -- key state rendering ---------------------------------------------------------


def _variant(state: str | None) -> str:
    if state in UNKNOWN_STATES:
        return "unavailable"
    return "off" if state in OFF_STATES else "on"


def _draw(key: int, item: dict, state: str | None) -> None:
    variant = item.get(_variant(state))
    variant = variant if isinstance(variant, dict) else {}
    label = str(variant.get("label", item.get("label", key)))
    color = icons.parse_color(
        variant.get("color", item.get("color")), icons.FALLBACK_COLORS[key - 1]
    )
    out = paths.generated_key_dir() / f"key-{key}-{_variant(state)}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    icons.make_icon(label, color, out)
    with _draw_lock:
        _device.set_key_image(key, str(out))
        _device.refresh()


# -- HA subscription -------------------------------------------------------------


def _watch(bindings: dict[str, list[tuple[int, dict]]], extra: set[str]) -> None:
    _ready.wait()
    entities = sorted(set(bindings) | extra)
    shown: dict[str, str | None] = {}

    def show(entity_id: str) -> None:
        if entity_id not in bindings:
            return
        state = (_states.get(entity_id) or {}).get("s")
        if shown.get(entity_id, "\0") == state:
            return
        shown[entity_id] = state
        print(f"ha-state: {entity_id} = {state}", flush=True)
        for key, item in bindings[entity_id]:
            if _shown_mode.get(key, -1) >= 0:
                continue  # an active mode owns the key's look
            _draw(key, item, state)

    while True:
        try:
            with connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                json.loads(ws.recv())  # auth_required
                ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
                reply = json.loads(ws.recv())
                if reply.get("type") != "auth_ok":
                    raise RuntimeError(f"auth failed: {reply.get('type')}")
                ws.send(json.dumps({"id": 1, "type": "subscribe_entities", "entity_ids": entities}))
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "result" and not msg.get("success"):
                        raise RuntimeError(f"subscribe failed: {msg.get('error')}")
                    if msg.get("type") != "event":
                        continue
                    event = msg["event"]
                    # Compressed states: "a" full (initial + added), "c" diffs, "r" removed.
                    with _states_lock:
                        for entity_id, st in event.get("a", {}).items():
                            _states[entity_id] = {"s": st.get("s"), "a": st.get("a") or {}}
                        for entity_id, diff in event.get("c", {}).items():
                            cur = _states.setdefault(entity_id, {"s": None, "a": {}})
                            plus, minus = diff.get("+", {}), diff.get("-", {})
                            if "s" in plus:
                                cur["s"] = plus["s"]
                            cur["a"].update(plus.get("a") or {})
                            for name in minus.get("a") or []:
                                cur["a"].pop(name, None)
                        for entity_id in event.get("r", []):
                            _states.pop(entity_id, None)
                    for entity_id in entities:
                        show(entity_id)
                    _refresh_modes()
                    if _np_log is not None:
                        for entity_id in {*event.get("a", {}), *event.get("c", {})}:
                            _np_log.update(entity_id, state_of(entity_id))
        except Exception as err:  # keep the keys alive across HA restarts
            print(f"ha-state: {err!r}; retrying in {RETRY_SECONDS}s", flush=True)
        # Show "unavailable" while disconnected, so stale state isn't trusted.
        with _states_lock:
            _states.clear()
        for entity_id in entities:
            show(entity_id)
        _refresh_modes()
        time.sleep(RETRY_SECONDS)


def _bindings(config: dict[str, Any]) -> dict[str, list[tuple[int, dict]]]:
    out: dict[str, list[tuple[int, dict]]] = {}
    for key, item in config["keys"].items():
        entity_id = item.get("state_entity")
        if isinstance(entity_id, str) and key.isdigit() and 1 <= int(key) <= 6:
            out.setdefault(entity_id, []).append((int(key), item))
    return out


def main() -> int:
    global _knobs, _youtube, _keys, _np_log
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    args, _ = parser.parse_known_args()
    config = configmod.normalize(configmod.load(args.config or configmod.ensure_config()))
    _keys = config["keys"]

    bindings = _bindings(config)
    extra: set[str] = _mode_entities()
    np_entities = (config.get("now_playing_log") or {}).get("entities") or []
    if np_entities:
        from np_log import NowPlayingLog

        _np_log = NowPlayingLog(np_entities)
        extra |= set(np_entities)
    yt = config.get("youtube")
    if isinstance(yt, dict) and yt.get("screens"):
        try:
            from yt_lounge import YouTubeScreens

            _youtube = YouTubeScreens(yt, state_of)
            extra |= _youtube.entities()
        except Exception as err:
            print(f"youtube: disabled ({err!r})", flush=True)
    atv = config.get("atv")
    if isinstance(atv, dict) and atv.get("remote") and atv.get("media_player"):
        touch = None
        try:  # direct Companion connection for preview scrubbing (pairing via files)
            from atv_touch import AppleTVTouch

            touch = AppleTVTouch()
        except Exception as err:
            print(f"atv-touch: disabled ({err!r}); scrubbing uses seek", flush=True)
        _knobs = AppleTVKnobs(atv, CoreAPI().call, state_of, touch)
        extra.add(atv["media_player"])
    if bindings or extra:
        threading.Thread(target=_watch, args=(bindings, extra), daemon=True).start()
    return daemon.main()


if __name__ == "__main__":
    raise SystemExit(main())
