# StreamDock N3 (local app)

Runs the community [streamdock-n3](https://github.com/asad-albadi/streamdock-n3)
daemon, pinned to commit `67f670a`, inside Home Assistant OS. The ActionRing N3
enumerates as USB `5548:1001`; the image patches that ID into the upstream
device table (`patch_ids.py`).

## Configuration

Edit `/addon_configs/local_streamdock_n3/config.json` (File editor, Samba, or
`/app_configs/local_streamdock_n3/` from the SSH app), then restart the app.
The format is the upstream one: `keys` sets LCD labels/colors, `actions` maps
event names to shell commands.

Use `ha-call` to run Home Assistant actions. It authenticates with the app's
Supervisor token; no long-lived token is needed.

```json
"actions": {
  "knob.1.right": "ha-call media_player.volume_up '{\"entity_id\":\"media_player.living_room\"}'",
  "knob.1.left":  "ha-call media_player.volume_down '{\"entity_id\":\"media_player.living_room\"}'",
  "button.1.press": "ha-call script.turn_on '{\"entity_id\":\"script.sonos_night_toggle\"}'"
}
```

### Live state on keys

A key with `state_entity` is redrawn whenever that entity changes in Home
Assistant (`ha_daemon.py`). `on`, `off` and `unavailable` (any other state,
or HA unreachable) override the key's `label`/`color`. Use `\\n` in a JSON
label for a line break.

```json
"1": {
  "label": "Night", "color": "#3b3f9e",
  "state_entity": "switch.tv_room_tv_room_night_sound",
  "on":  { "label": "Night\\nON",  "color": "#3b5bdb" },
  "off": { "label": "Night\\nOFF", "color": "#262626" }
}
```

Event names verified on the ActionRing N3 (2026-10-23):

| Control | Events |
|---|---|
| LCD keys 1–6 | `button.1.press` … `button.6.press` (+ `.release`) |
| Round buttons 1–3 | `button.7.press` … `button.9.press` (+ `.release`) |
| Small knob 1 | `knob.1.left`, `knob.1.right`, `knob.1.press`, `knob.1.release` |
| Small knob 2 | `knob.2.left`, `knob.2.right`, `knob.2.press`, `knob.2.release` |
| Large knob | `knob.3.left`, `knob.3.right`, `knob.3.press`, `knob.3.release` |

One rotation event per detent. No `evdev.*` events were emitted in this
firmware mode, so nothing double-fires.

Apple TV gotcha: `remote.turn_on`/`remote.turn_off` only connect/disconnect
Home Assistant from the box; they do not change its power. Use
`media_player.turn_on`/`turn_off` for power. After a `remote.turn_off`, every
`remote.send_command` fails with "not connected" until `remote.turn_on`.

Every input is printed to the app log as `<description> [<event name>]`, so
press a control and read the log to find its event name. `ha-call` failures
also appear there.

## Devices

With Protection mode on, the app can only open device paths listed in
`config.yaml`. The Supervisor skips listed paths that don't exist at start and
grants them when they appear later (hotplug), so the list covers:

- `/dev/hidraw0`–`/dev/hidraw9`: hidraw numbers shift on replug and have no
  stable symlink. This also grants any other USB HID device that lands in
  that range.
- The dock's `/dev/input/by-id/...-event-kbd` link. It embeds this unit's
  serial (`C511D3781A4C`); a different dock needs its own link here.

## Reconnect

The upstream daemon keeps running with a dead handle after an unplug, so
`run.sh` watches the dock's USB `busnum:devnum` once a second and restarts the
daemon when it disappears or re-enumerates. The log shows `waiting for dock`,
`dock found at usb ...` and `dock disconnected or re-enumerated; restarting`.

## Updating

From the repo root on the PC run `bash homeassistant/restore.sh`: it uploads
this folder, rebuilds the app and deploys `config.tv_room.json`. To move to a
newer upstream revision, change `STREAMDOCK_REF` in the Dockerfile.
