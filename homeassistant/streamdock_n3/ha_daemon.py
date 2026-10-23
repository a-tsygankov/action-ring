"""streamdock-n3 daemon plus live Home Assistant state on LCD keys.

Runs the upstream daemon unchanged and adds one thread that subscribes to
Home Assistant (Supervisor WebSocket proxy, app token) and redraws every key
whose config has a "state_entity". Per-state overrides of label/color:

    "1": {"label": "Night", "color": "#3b3f9e",
          "state_entity": "switch.tv_room_tv_room_night_sound",
          "on":  {"label": "Night\\nON", "color": "#3b5bdb"},
          "off": {"label": "Night\\nOFF", "color": "#262626"},
          "unavailable": {"label": "Night\\n?", "color": "#5c3b00"}}

Any state other than on/off uses "unavailable". Missing overrides fall back
to the key's base label/color.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from streamdock_n3 import config as configmod
from streamdock_n3 import daemon, icons, paths
from websockets.sync.client import connect

WS_URL = "ws://supervisor/core/websocket"
RETRY_SECONDS = 5

_ready = threading.Event()
_device = None
_draw_lock = threading.Lock()

_upstream_apply_icons = daemon.apply_icons


def _apply_icons(device, config):
    # Static icons first; state redraws must not race the initial paint.
    global _device
    _upstream_apply_icons(device, config)
    _device = device
    _ready.set()


daemon.apply_icons = _apply_icons


def _variant(state: str | None) -> str:
    return state if state in ("on", "off") else "unavailable"


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


def _watch(bindings: dict[str, list[tuple[int, dict]]]) -> None:
    _ready.wait()
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    shown: dict[str, str | None] = {}

    def update(entity_id: str, state: str | None) -> None:
        if entity_id not in bindings or shown.get(entity_id, "\0") == state:
            return
        shown[entity_id] = state
        print(f"ha-state: {entity_id} = {state}", flush=True)
        for key, item in bindings[entity_id]:
            _draw(key, item, state)

    while True:
        try:
            with connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                json.loads(ws.recv())  # auth_required
                ws.send(json.dumps({"type": "auth", "access_token": token}))
                reply = json.loads(ws.recv())
                if reply.get("type") != "auth_ok":
                    raise RuntimeError(f"auth failed: {reply}")
                ws.send(json.dumps({
                    "id": 1,
                    "type": "subscribe_entities",
                    "entity_ids": sorted(bindings),
                }))
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "result" and not msg.get("success"):
                        raise RuntimeError(f"subscribe failed: {msg.get('error')}")
                    if msg.get("type") != "event":
                        continue
                    event = msg["event"]
                    # "a": full states (initial + added), "c": diffs, "r": removed
                    for entity_id, st in event.get("a", {}).items():
                        update(entity_id, st.get("s"))
                    for entity_id, diff in event.get("c", {}).items():
                        new = diff.get("+", {})
                        if "s" in new:
                            update(entity_id, new["s"])
                    for entity_id in event.get("r", []):
                        update(entity_id, None)
        except Exception as err:  # keep the keys alive across HA restarts
            print(f"ha-state: {err!r}; retrying in {RETRY_SECONDS}s", flush=True)
        # Show "unavailable" while disconnected, so stale state isn't trusted.
        for entity_id in list(bindings):
            update(entity_id, None)
        time.sleep(RETRY_SECONDS)


def _bindings(config_path: Path) -> dict[str, list[tuple[int, dict]]]:
    keys = configmod.normalize(configmod.load(config_path))["keys"]
    out: dict[str, list[tuple[int, dict]]] = {}
    for key, item in keys.items():
        entity_id = item.get("state_entity")
        if isinstance(entity_id, str) and key.isdigit() and 1 <= int(key) <= 6:
            out.setdefault(entity_id, []).append((int(key), item))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    args, _ = parser.parse_known_args()
    bindings = _bindings(args.config or configmod.ensure_config())
    if bindings:
        threading.Thread(target=_watch, args=(bindings,), daemon=True).start()
    return daemon.main()


if __name__ == "__main__":
    raise SystemExit(main())
