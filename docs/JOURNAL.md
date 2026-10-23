# ActionRing N3 × Home Assistant — Journal

The current, working state of the system and how it got there. Dead ends and
rolled-back steps are left out; only what is live (and the lessons that shaped
it) is recorded. Newest entries go at the bottom of **Log**; **System state**
always reflects the latest.

To restore onto a fresh device, see [Restore](#restore).

Design reference: [ActionRing_Home_Assistant_Developer_Handoff.md](ActionRing_Home_Assistant_Developer_Handoff.md)
(converted from the original `.docx`).

## System state (2026-10-23)

| Item | Value |
|---|---|
| Host | Raspberry Pi 4 (aarch64), `homeassistant.local` |
| Home Assistant OS / Supervisor / Core | 18.3 / 2026.10.3 / 2026.10.2 |
| SSH | Terminal & SSH app 10.5.0; from the PC: `ssh homeassistant` |
| Dock | ActionRing N3, USB `5548:1001` "HOTSPOTEKUSB HID DEMO", serial `C511D3781A4C` |
| Dock software | Local app `local_streamdock_n3` v0.4.0-5, upstream [asad-albadi/streamdock-n3](https://github.com/asad-albadi/streamdock-n3) @ `67f670a` |
| HA config added | `script.tv_room_mute_toggle` |
| Integrations used | Sonos, LG webOS, Apple TV (all paired in HA UI) |

### Repo layout

| Path | Purpose |
|---|---|
| `homeassistant/streamdock_n3/` | The HA app (Dockerfile, `config.yaml`, `run.sh` watchdog, `ha_daemon.py` live key state, `ha-call`, `patch_ids.py`, `DOCS.md`) |
| `homeassistant/streamdock_n3/config.tv_room.json` | Live dock mappings (source of truth) |
| `homeassistant/ha-config/scripts/*.yaml` | HA scripts, one top-level key per file |
| `homeassistant/restore.sh` | Restore everything above onto a HAOS device |

### Control mappings (TV Room)

| Control | Event | Action |
|---|---|---|
| Large knob | `knob.3.left/right/press` | Apple TV skip back/forward · play/pause |
| Small knob 1 | `knob.1.left/right/press` | Sonos Arc volume down/up · mute toggle (script) |
| Small knob 2 | `knob.2.left/right/press` | Apple TV up/down · select |
| Round 1 / 2 / 3 | `button.7/8/9.press` | Apple TV Menu (back) / Home / Power on (wakes LG via CEC) |
| Key 1 "Night" | `button.1.press` | Toggle `switch.tv_room_tv_room_night_sound`; LCD shows live state (blue ON / grey OFF / amber ?) |
| Key 2 "Speech" | `button.2.press` | Toggle `switch.tv_room_tv_room_speech_enhancement`; LCD shows live state (green ON / grey OFF / amber ?) |
| Key 3 "TV Off" | `button.3.press` | LG off + Apple TV sleep |
| Key 4 "ATV" | `button.4.press` | LG input → `Apple OTT` (the Apple TV HDMI input) |
| Keys 5–6 | — | Blank, unassigned |

## Restore

### Prerequisites (manual)

1. Flash Home Assistant OS to the Pi, complete onboarding.
2. Pair integrations in **Settings → Devices & services**: Sonos (auto-discovered),
   LG webOS TV (accept the prompt on the TV), Apple TV (enter the PINs shown on
   the TV). Entity IDs must match the ones in [Control mappings](#control-mappings-tv-room);
   rename entities in HA if pairing produced different IDs. `restore.sh` lists
   any that are missing.
3. Install the **Terminal & SSH** app. In its Configuration, add the PC's key
   (`~/.ssh/id_ed25519_ha.pub`) to `authorized_keys`, save, start.
4. On the PC, if the key or alias is missing:
   ```bash
   ssh-keygen -t ed25519 -N '' -C claude-ha -f ~/.ssh/id_ed25519_ha
   ```
   and add to `~/.ssh/config`:
   ```text
   Host homeassistant homeassistant.local
       HostName homeassistant.local
       User root
       IdentityFile ~/.ssh/id_ed25519_ha
       IdentitiesOnly yes
   ```
5. Plug the dock into the Pi.

### Automated

```bash
bash homeassistant/restore.sh
```

Idempotent. It checks the mapped entities exist, adds missing HA scripts
(backing up `scripts.yaml`, then `ha core check` + restart), uploads and
builds the app (patching in the dock's by-id link found on the target),
deploys the mappings (backing up any existing `config.json`), restarts the
app and confirms the controller is running. Verified against the live Pi on
2026-10-23.

Alternative for a like-for-like replacement: a full HA backup
(**Settings → System → Backups**) also carries integration pairings, which the
script cannot.

## Log

### 2026-10-23 — SSH access

- The PC's main key `~/.ssh/id_ed25519` is passphrase-protected and there is
  no running agent, so it can't be used non-interactively. Created a dedicated
  key `~/.ssh/id_ed25519_ha` (no passphrase) and the `homeassistant` alias.
- The SSH app is a container: it has the `ha` CLI and `/homeassistant`
  (config), `/local_apps`, `/app_configs`, but **no Core API token** (401).
  Entity data is read from `/homeassistant/.storage/core.entity_registry` with
  `jq` instead.
- HA 2026.10 renamed add-ons to **apps**: `ha apps …`, `/local_apps`,
  `/app_configs`.

### 2026-10-23 — Handoff document

- Converted the `.docx` handoff to Markdown (no pandoc on the PC; small stdlib
  converter). Content, tables and links preserved.

### 2026-10-23 — StreamDock N3 app

- HAOS can't host software directly, so the upstream daemon runs as a local
  app. Built on `python:3.12-slim-bookworm` because the vendored native
  transport `libtransport*.so` is glibc-linked (Alpine HA base images won't
  load it). Upstream pinned by commit (`STREAMDOCK_REF` in the Dockerfile).
- The dock enumerates as `5548:1001`, absent from upstream's N3 table
  (`6603:1003` etc.). `patch_ids.py` adds it at build time and fails the
  build if upstream changes shape.
- Device access keeps **Protection mode on** (security rating 7). `full_access`
  is not used: it only takes effect with Protection mode off.
- `homeassistant_api: true` + `ha-call <domain>.<service> '<json>'` calls Core
  with the Supervisor token, so no long-lived token exists. Failures go to the
  app log (the daemon discards action output).
- Config is `/app_configs/local_streamdock_n3/config.json` (from the SSH app;
  `/addon_configs/...` in File editor/Samba). Every input is logged as
  `<description> [<event>]`.
- Verified event names on this unit: LCD keys `button.1–6`, round buttons
  `button.7–9`, knobs `knob.1` (small 1), `knob.2` (small 2), `knob.3` (large),
  each `.left/.right/.press/.release`. One event per detent; no `evdev.*`
  events in this firmware mode, so nothing double-fires.

### 2026-10-23 — TV Room mappings

- Inventory: Sonos Arc (TV Room) + 5 other Sonos, LG OLED65C4 (webOS
  `media_player.lg_tv_2`, Cast `media_player.lg_tv`), one Apple TV 4K
  ("vugiTV 4K"), SHIELD, Google minis. **No Govee/lights, no second Apple TV.**
- Added `script.tv_room_mute_toggle` (HA has no mute-toggle action).
- LG input for the Apple TV box is `Apple OTT`; `Apple TV` in the source list
  is LG's Apple TV+ app.
- Mappings deployed and tested by pressing every control.

**Gotchas learned:**

- Apple TV `remote.turn_on/turn_off` only connect/disconnect HA from the box;
  they don't change power. After `remote.turn_off`, every `send_command` fails
  with "not connected". Power uses `media_player.turn_on/turn_off`; Round 3
  also calls `remote.turn_on` first so it self-heals a disconnected state.
- LG `select_source` fails with "Device is off" when the TV is off (HTTP 500
  in the app log). Expected.

### 2026-10-23 — Replug recovery

- After an unplug the upstream daemon keeps running with a dead handle, and
  hidraw numbers shift on replug (`hidraw0/1` → `hidraw1/2`).
- `run.sh` now watches the dock's USB `busnum:devnum` every second and
  restarts the daemon when it disappears or re-enumerates; it waits for the
  dock if absent at start.
- `config.yaml` lists `/dev/hidraw0–9` plus the dock's
  `/dev/input/by-id/...-event-kbd` link. The Supervisor skips missing paths at
  start and grants cgroup access on hotplug for listed paths or their
  symlinks (confirmed in Supervisor source and logs). Side effect: any other
  USB HID device landing in `hidraw0–9` is also accessible to the app.

- Physical replug confirmed: the log shows `dock disconnected or re-enumerated;
  restarting` → `waiting for dock` → `dock found` → `controller running`.
  The Supervisor grants the nodes one by one (~0.2 s apart), so the settle
  delay before starting the daemon is 2 s.

### 2026-10-23 — Restore tooling

- Added `homeassistant/restore.sh` and moved the HA script into
  `homeassistant/ha-config/scripts/`. It installs, updates (when
  `config.yaml`'s version was bumped) or rebuilds the app; the Supervisor
  refuses `rebuild` across a version change. Ran against the live Pi.

### 2026-10-23 — Live state on LCD keys

- `ha_daemon.py` wraps the upstream daemon (unchanged) and adds a thread that
  subscribes to HA over `ws://supervisor/core/websocket` (`subscribe_entities`,
  app token). Keys with `state_entity` are redrawn with their `on` / `off` /
  `unavailable` label/color overrides. Changes made elsewhere (Sonos app,
  HA UI) show too; while HA is unreachable the keys show `?`.
- Redraws wait for the daemon's initial icon paint and are serialized; the
  SDK uploads each key image in one native call, alongside its own heartbeat
  thread.
- Multi-line labels use a literal `\n` in the label (`"Night\\nON"` in JSON).
- Confirmed on the device: keys show ON/OFF and follow toggles.

### 2026-10-23 — Knob layout

- Skip/play-pause moved to the large knob, Sonos volume/mute to small knob 1.

### 2026-10-23 — Repository

- Published to [a-tsygankov/action-ring](https://github.com/a-tsygankov/action-ring)
  (public): `main` started with the original handoff `.docx`, and everything
  else landed through a pull request. The repo holds no secrets: Home
  Assistant calls use the app's Supervisor token at runtime.

## Open items

- [ ] Skip knob feel: check step size and overshoot on fast turns; coalesce
      events if needed.
- [ ] Keys 5–6 unassigned.
- [ ] Govee not integrated in HA; lighting controls pending.
- [ ] Only one Apple TV in HA; the handoff mentions two generations.
