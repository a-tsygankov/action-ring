# ActionRing N3 Home Assistant Controller

Developer handoff  |  Architecture, setup, mappings, and acceptance criteria

## Purpose and recommended design

Build a configurable multimedia control surface using the ActionRing N3. Use Home Assistant (HA) as the device-action hub for Sonos, LG webOS, and supported Govee devices. Keep Apple TV playback and especially video scrubbing on a direct local control path when latency matters. The ActionRing is an input device; it does not itself become a Home Assistant integration.

| Path | Responsibility | Recommendation |
|---|---|---|
| ActionRing N3 | Physical keys, three rotary encoders, encoder push events | USB HID device attached to a Linux host |
| N3 input daemon / app | Read HID/input events; map controls; run local actions | Community Linux app or a small custom daemon; validate exact event names on the actual unit |
| Local action bridge | Translate named controls into HA API calls or direct Apple TV commands | Keep mapping/configuration here; avoid embedding credentials in controller profile |
| Home Assistant | Own device integrations, scripts, scenes, stateful actions | Use scripts as stable named operations for Sonos/LG/Govee |
| Apple TV path | Remote navigation, play/pause, seek/scrub | Use pyatv or another direct local protocol path; test each Apple TV generation and app |

## Deployment choice

Preferred first build: run Raspberry Pi OS 64-bit on a Pi, with the N3 daemon on the host and Home Assistant Container on that same machine or another always-on LAN host. If HA runs elsewhere, point the bridge to its LAN address. If HA is on the same host, use the local published port. Do not plan to install a general-purpose HID daemon and desktop dependencies inside Home Assistant OS; HAOS is an appliance-style operating system. An old Mac or PC is also a good first host for faster troubleshooting and development, then move the tested service to a Pi if desired.

- Confirm USB device ID and event interface with lsusb and the project diagnostic commands before building mappings. The community project reports N3 USB ID 6603:1003; verify on the physical unit.
- Give the service access to the correct hidraw/input device through a scoped udev rule and group permissions. Run it as a dedicated unprivileged user, not as root, after testing.
- Pin the community app revision and record OS, kernel, device firmware, and app version in the implementation notes; upstream support and controls may vary by revision.

## Home Assistant setup

- Install and configure the HA integrations for the specific LG OLED, Sonos system, Apple TVs (if HA should expose their state), and exact Govee models. Confirm each target entity and action from HA Developer Tools before connecting the controller.
- Create HA scripts with stable names for actions such as Sonos volume up/down, Night Sound on/off or toggle, speaker group/scene selection, TV power/input, and Govee scene/brightness. Put device-specific entity IDs and service data inside those scripts.
- Generate a long-lived access token in the HA user profile Security section for the local bridge. Store it in a root/user-readable-only environment file or OS secret store. Do not put it in ActionRing profile JSON, source control, or logs.
- Check reachability from the N3 host and test a script call. HA REST API runs under /api/; authenticated actions use POST /api/services/<domain>/<service>. The default port is 8123 for standard installations.

```bash
curl -i -H "Authorization: Bearer $HA_TOKEN" -H "Content-Type: application/json" -d '{"entity_id":"script.sonos_night_toggle"}' http://homeassistant.local:8123/api/services/script/turn_on
```

Replace the hostname and entity ID with values from the installation. A successful GET to /api/ returns “API running.” The official REST API documentation describes bearer authentication and action calls: <https://developers.home-assistant.io/docs/api/rest>

## Action bridge and mapping

Implement a small local bridge that accepts a logical action name (for example, sonos.volume_up) and dispatches it to either a Home Assistant script or the direct Apple TV adapter. Use a persistent HTTP session or asynchronous client if event rate warrants it. Give calls short timeouts, bounded retries only for safe idempotent commands, and structured logs without tokens.

| Control | Example mapping | Target path |
|---|---|---|
| Button 1 short press | sonos.night_sound_toggle | HA script; preferably explicit on/off actions if the UI must show state |
| Knob 1 clockwise / counterclockwise | sonos.volume_up / sonos.volume_down | HA script or media_player volume action; one logical step per encoder detent |
| Knob 1 press | sonos.mute_toggle | HA script or supported media_player action |
| Knob 2 turn | apple_tv.seek_relative | Direct pyatv/custom adapter; use small seek increments and coalesce rapid events |
| Knob 2 press / buttons | apple_tv.play_pause, back, select | Direct Apple TV adapter; validate actions against each generation and active app |
| Button 2/3 | lighting.scene_movie / lighting.scene_off | HA script or scene activation for supported Govee integration |
| LCD key | profile or action selection | Show current function/state where the N3 API supports it; do not assume button LCD feedback is available until tested |

Names above are proposed logical actions, not guaranteed ActionRing event identifiers or HA entity IDs. The implementer must inspect actual N3 events and HA entities, then keep a user-editable mapping file. The Linux project configuration reportedly invokes shell commands; use a narrow wrapper command such as “n3-action sonos.volume_up” instead of placing raw API calls in every mapping.

## Latency and tactile behavior

- For tactile feel, minimize work between encoder event and command: local daemon → direct Apple TV protocol for scrubbing. Avoid a cloud service or HA automation round trip for every scrub tick.
- HA REST on a local LAN should be suitable for discrete light/audio commands, but measure end-to-end response on the actual network and speakers. Sonos buffering and the operation itself may dominate perceived latency.
- Do not queue an unbounded series of seek calls. Coalesce encoder deltas over a short window, preserve direction, and send a bounded seek step; let a new turn update/cancel the pending step. Tune experimentally to avoid overshoot.
- Test event rate and duplicate events: some encoder/firmware modes may report both key and dial events. Debounce only duplicate transitions; do not debounce valid fast turns into sluggish controls.
- Rotary encoders provide discrete detents, not force feedback. “Tactile” feel comes primarily from the physical encoder; software can improve response but cannot add haptics unless the device hardware supports it.

## Implementation sequence

| Stage | Work | Exit check |
|---|---|---|
| 1. Identify | Connect N3; record USB ID, permissions, raw event names, encoder direction and press behavior | All controls produce stable, understood events after reboot |
| 2. HA actions | Add integrations; find entities; create and run HA scripts manually | Each intended action works from HA UI / Developer Tools |
| 3. Bridge | Implement config-driven named action dispatcher, REST client, direct Apple TV adapter | A local test command runs one HA script and one Apple TV command |
| 4. Map | Bind physical buttons/knobs; add profiles and LCD labels if supported | Mappings can be changed without recompiling |
| 5. Tune | Measure event-to-command and command-to-response latency; adjust coalescing and detents | No double-fires; volume and scrubbing feel predictable |
| 6. Install | Add udev permissions and user-level systemd service; document recovery | Service starts at boot and recovers after device disconnect/reconnect |

## Acceptance criteria

- After reboot, the N3 is detected without manual root commands and the controller service starts reliably.
- Each physical input is identified correctly; rapid knob turns do not lose direction, generate duplicate actions, or build a growing queue.
- Night Sound can be switched both on and off deterministically, and the LCD/profile indicates the selected function if supported.
- Volume controls target the intended Sonos room/group; mute and lighting controls affect the expected targets.
- Apple TV navigation/play-pause works on both owned generations. Scrubbing is tested separately in the video apps the user actually uses; document apps where seek is unavailable or behaves differently.
- The bridge handles HA unavailable, invalid entity, Apple TV unavailable, and N3 disconnect cases with bounded timeouts and useful logs; secrets never appear in logs.
- A user can change entity/script IDs, knob direction, per-detent behavior, and action assignments in configuration without code changes.

## Known constraints and open verification

- “iOS compatible” on the product listing does not establish iOS as the host for this Linux bridge. Treat ActionRing as a USB accessory whose iOS use depends on vendor app/firmware support; this design targets Linux host control.
- Home Assistant integrations expose different controls depending on model, firmware, integration version, and app state. Discover entities at implementation time rather than assuming exact names.
- Apple TV seek behavior may differ across apps. Standard remote actions can be broadly available while fine-grained relative seek may not be exposed consistently. Verify on both Apple TV generations and target apps before committing to a scrubbing UX.
- The community N3 Linux software is not equivalent to the polished VSD Craft desktop editor. Validate profile editing, LCD behavior, firmware modes, and startup behavior against the current repository version.

## Reference links

- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest)
- [Community Linux controller and diagnostics for N3](https://github.com/asad-albadi/streamdock-n3)
- [Apple TV control library pyatv](https://pyatv.dev/)
