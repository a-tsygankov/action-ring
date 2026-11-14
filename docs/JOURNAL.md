# ActionRing N3 × Home Assistant — Journal

The current, working state of the system and how it got there. Dead ends and
rolled-back steps are left out; only what is live (and the lessons that shaped
it) is recorded. Newest entries go at the bottom of **Log**; **System state**
always reflects the latest.

To restore onto a fresh device, see [Restore](#restore). To swap in a new
Apple TV box, see [Replacing the Apple TV](#replacing-the-apple-tv).

Design reference: [ActionRing_Home_Assistant_Developer_Handoff.md](ActionRing_Home_Assistant_Developer_Handoff.md)
(converted from the original `.docx`).

## System state (2026-11-13)

| Item | Value |
|---|---|
| Host | `arpi`, 192.168.1.144: Raspberry Pi 4 (aarch64), Ubuntu 22.04 (since 2026-11-01; the old HAOS Pi `homeassistant.local` is powered off) |
| Home Assistant | Supervised, Core 2026.10.4; data folder `/var/lib/homeassistant` |
| SSH | Host sshd as `andrey` (passwordless sudo); from the PC: `ssh homeassistant` (= `ssh arpi`). Scripts go through `homeassistant/ssh_ha.sh` |
| Dock | ActionRing N3, USB `5548:1001` "HOTSPOTEKUSB HID DEMO", serial `C511D3781A4C` |
| Dock software | Local app `local_streamdock_n3` v0.6.0-1 (pluggable services host, soccer as a service), upstream [asad-albadi/streamdock-n3](https://github.com/asad-albadi/streamdock-n3) @ `67f670a` |
| HA config added | `script.tv_room_mute_toggle`, `script.tv_room_power_toggle`, `automation.streaming_pause_mute` |
| Integrations used | Sonos, LG webOS, Apple TV (all paired in HA UI) |
| MCP for Claude | None since 2026-11-01 (the `home-assistant` server pointed at the old Pi and was removed). Re-add per Restore > Prerequisites 6 with a token from arpi if wanted |

### Repo layout

| Path | Purpose |
|---|---|
| `homeassistant/streamdock_n3/` | The HA app (Dockerfile, `config.yaml`, `run.sh` watchdog, `ha_daemon.py` live key state + HA calls, `knob_accel.py` Apple TV knob engine, `atv_touch.py` Companion touch connection, `yt_lounge.py` + `yt_chapters.py` YouTube link and chapters, `yt_rating.py` like/dislike, `dynamic.py` keys that take turns, `np_log.py` now-playing log, `ha-call`, `patch_ids.py`, `DOCS.md`) |
| `homeassistant/streamdock_n3/services/` | Pluggable services: contract, `ctx`, scheduler, host, metrics (`README.md` = how to add one); `soccer/` is the live-soccer service |
| `homeassistant/streamdock_n3/presets/tv_room.json` | Live dock mappings (source of truth) |
| `homeassistant/streamdock_n3/icons/` | Key images, deployed to `/app_configs/local_streamdock_n3/icons/` (`/config/icons/` in the app) |
| `homeassistant/ha-config/scripts/*.yaml` | HA scripts, one top-level key per file |
| `homeassistant/ha-config/automations/*.yaml` | HA automations, one `- id:` entry per file |
| `homeassistant/restore.sh` | Restore everything above onto a HAOS device |
| `homeassistant/pair.sh` | Pair the Apple TV (`atv` touch, `atv airplay` media protocol), link YouTube apps, sign in for YouTube like/dislike (`youtube-rating`); `status` |
| `homeassistant/tools/` | Ad research: `mrp_summary.py`, `replay_ad_detect.py`, `sanitize_mrp.py`, sanitized `fixtures/`; live tests: `probe.sh` + `probes/*.json`; `metrics.sh` prints the app's per-operation metrics table |
| `docs/AD_DETECTION_GUIDE.md` | Guide for AI agents: analyze a new app and add its ad rule |

### Control mappings (TV Room)

| Control | Event | Action |
|---|---|---|
| Large knob | `knob.3.left/right/press` | Apple TV, speed-adaptive. While playing/paused: tvOS preview scrubbing (touch drag, finger travel 12 / 60 per 2 / 150 per 3 units per click); seek fallback 10 s per click / 30 s per 2 / 90 s per 3 when touch is unavailable. 3 s without knob activity after a scrub commits it and resumes playback. Otherwise focus left/right 1 / 2 / 3 steps per click · press = OK (`select`; commits a scrub and cancels the auto-resume) |
| Small knob 1 | `knob.1.left/right/press` | Sonos Arc volume down/up · mute toggle (script) |
| Small knob 2 | `knob.2.left/right/press` | Apple TV focus up/down, 1 / 2 / 3 steps per click · press = OK (`select`) |
| Round 1 / 2 / 3 | `button.7/8/9.press` | Apple TV Menu (back) / Home / Power on (wakes LG via CEC); 1 → 2 → 3 in a row: cat easter egg (Round 1 reacts 0.6 s late, see DOCS "Cat easter egg") |
| Key 1 "Night" | `button.1.press` | Toggle `switch.tv_room_tv_room_night_sound`; LCD shows live state (blue ON / grey OFF / amber ?) |
| Key 2 "Speech" | `button.2.press` | Toggle `switch.tv_room_tv_room_speech_enhancement`; LCD shows live state (green ON / grey OFF / amber ?) |
| Key 3 "TV" | `button.3.press` | Power toggle (`script.tv_room_power_toggle`): on → LG off + Apple TV sleep; off → wake Apple TV (LG follows via CEC). LCD shows LG state (red ON / grey OFF), or amber "TV / MUTED" while the Arc is muted |
| Key 4 "ATV" / "Next chapter" | `button.4.press` → `{"key_action": 4}` | Normally LG input → `Apple OTT` (the Apple TV HDMI input). While YouTube is in the foreground on the LG or the Apple TV: red skip icon, jumps to the video's next chapter |
| Key 5 (house icon) | `button.5.press` | Apple TV Home (same as Round 2) |
| Key 6 (back-arrow icon) | `button.6.press` | Apple TV Back (`menu`, same as Round 1) |

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
   **Home Assistant Supervised** (HA on Ubuntu/Debian, e.g. `arpi`) instead:
   no app needed. Install the key for a host user (`ssh-copy-id`, or on
   Windows pipe the `.pub` into `~/.ssh/authorized_keys` over ssh), give that
   user passwordless sudo (`/etc/sudoers.d/`), and pass the host alias to
   `restore.sh` (e.g. `restore.sh arpi`). `ssh_ha.sh` detects
   `/etc/hassio.json`, runs every command under `sudo`, and links
   `/homeassistant`, `/local_apps`, `/app_configs` into the Supervisor data
   folder. (The SSH app can't take port 22 there: the host's sshd has it.)
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
6. Optional, for Claude: add the **Model Context Protocol Server** integration
   in **Settings → Devices & services** (all LLM APIs, require admin). Create a
   long-lived access token at `http://homeassistant.local/profile/security`,
   then on the PC run:
   ```bash
   claude mcp add --scope user --transport http home-assistant http://homeassistant.local/api/mcp --header "Authorization: Bearer <token>"
   ```
   `claude mcp get home-assistant` should report Connected. The token lives
   only in `~/.claude.json` on the PC, never in this repo.

### Automated

```bash
bash homeassistant/restore.sh
```

Idempotent. Steps:

1. Checks that the mapped entities exist in HA.
2. Adds missing HA scripts and automations (backup first, then
   `ha core check` + restart). Entries that differ from the repo are kept
   unless you pass `--replace-ha-config`.
3. Uploads and builds the app, patching in the dock's by-id link found on
   the target.
4. Turns the app's watchdog on (Supervisor API); the dock self-restart
   needs it.
5. Deploys the mappings and icons, backing up any existing `config.json`.
6. Restarts the app (retrying while the Supervisor is busy) and confirms the
   controller is running.
7. Pairs the Apple TV via `pair.sh atv` if needed (prompts for the PIN),
   then prints `pair.sh status`.

Link YouTube apps afterwards with `bash homeassistant/pair.sh youtube <screen>`,
and sign in for like/dislike with `bash homeassistant/pair.sh youtube-rating
<client.json>` (the OAuth client JSON from the Google Cloud project, see
DOCS.md "Like / dislike"; `yt_oauth*.json` are not in the repo).
Verified against the live Pi.

### Hiccups seen on a real restore (arpi, 2026-11-01)

The first restore onto a fresh machine (HA Supervised on Ubuntu) found
these; all but the manual ones are now handled by the scripts:

- **Not HAOS.** The scripts assumed the Terminal & SSH app (root, `ha`,
  `/homeassistant`...). On Supervised the app can't take port 22 (the
  host's sshd has it) and the host refuses root logins. Fix: log in as a
  host user with passwordless sudo; `ssh_ha.sh` detects `/etc/hassio.json`,
  wraps commands in `sudo` and links the app paths into
  `/var/lib/homeassistant`.
- **Watchdog off on a fresh install**, which silently disables the dock
  self-restart. `restore.sh` now turns it on; the `ha` CLI has no option
  for it, so it goes through the Supervisor API.
- **Fresh build, newer dependencies:** srptools 2.0.0 broke pyatv 0.18
  pairing (`SRPClientSession.__init__() takes 2 positional arguments`). The
  live Pi never saw it (cached image). Pinned `srptools==1.0.1`. A fresh
  build is the real test of a restore.
- **"Supervisor is not ready"** right after the app update: the start step
  now retries for a minute.
- **Pairing needs the dock plugged in:** the app's main program (which
  pairs the Apple TV and links YouTube) only starts once the dock is there;
  without it `pair.sh` waits and leaves `atv_pair_request.json` behind
  (remove it, or it fires when the dock arrives). Move the dock first.
- **Windows shells:** `ssh-copy-id` doesn't exist (pipe the `.pub` into
  `~/.ssh/authorized_keys` over ssh), and PowerShell needs
  `$env:HA_HOST = "arpi"; bash ...` instead of `HA_HOST=arpi bash ...`.
- **Reading states:** on Supervised the CLI container's token gets 401 from
  the Core API; read the recorder DB (`home-assistant_v2.db`) with the
  host's `python3` instead.
- **Names:** `homeassistant.local` vanished with the old Pi; the
  `homeassistant` SSH alias now points at arpi by IP, so the scripts need
  no `HA_HOST`.

Alternative for a like-for-like replacement: a full HA backup
(**Settings → System → Backups**) also carries integration pairings, which the
script cannot.

## Replacing the Apple TV

A new box (another unit, same Apple ID) needs no code change as long as it
ends up with the same entity IDs in HA. Everything below is tied to the
physical box and is redone; nothing carries over from the old one.

| Tied to the box | Where | If not redone |
|---|---|---|
| HA Apple TV integration | HA config entry (holds the box's IP) | No control of the new box |
| `media_player.vugitv_4k_vugitv_4k`, `remote.vugitv_4k_vugitv_4k` | `presets/tv_room.json` (~25), `displaypad/presets/default.json`, both HA scripts, `streaming_pause_mute` | Every Apple TV control fails |
| App's Companion pairing | `atv_companion.json` (old IP + old keys) | Knob scrubbing falls back to 10 s seek steps |
| App's AirPlay pairing | `airplay_credentials` in the same file | Pluto / Peacock ad muting stops |
| YouTube link, screen `atv` | `yt_lounge.json` | Next chapter stops on the Apple TV |
| LG input `Apple OTT` | 5× in `tv_room.json`, the automation, the displaypad preset | Context tabs, soccer key, ad muting, pause mute |
| HDMI-CEC from the box | tvOS setting | Key 3 / Round 3 can't power the LG on |

### On the new box

- [ ] Set up from the iPhone (Apple ID, Wi-Fi, Home carry over; most
      settings and apps don't).
- [ ] **Settings → General → Name:** `vugiTV 4K`, before HA sees it.
- [ ] **Settings → Remotes and Devices → Control TVs and Receivers:** on.
- [ ] **Settings → AirPlay and Home → Allow Access:** at least "Anyone on
      the same network", or pairing PINs may not show.
- [ ] Install the apps the tuning covers: YouTube, Pluto TV, Peacock,
      Disney+, Max, Netflix (the Apple TV app is built in).
- [ ] Plug it into the **same LG HDMI port**; a DHCP reservation is worth
      it (the box gets a new IP).

### Home Assistant

- [ ] **Delete the old Apple TV entry first** (Settings → Devices &
      services → Apple TV → ⋮ → Delete). Otherwise the new box's entities
      get a `_2` suffix.
- [ ] Add the Apple TV integration for the new box (PINs on the TV).
- [ ] Entity IDs are exactly `media_player.vugitv_4k_vugitv_4k` and
      `remote.vugitv_4k_vugitv_4k`; rename in Settings → Entities if not (a
      fresh add on a current HA may name them differently, e.g.
      `media_player.vugitv_4k`). `restore.sh arpi` lists any mapped entity
      that is missing.
- [ ] The LG's `source_list` (`media_player.lg_tv_2` attributes) still
      names the box's input `Apple OTT`; if the LG renamed it, update `atv_source` everywhere.

### App pairings (from the PC; the dock must be plugged in)

`pair.sh` and `restore.sh` take the address from the HA entry that owns
`atv.media_player` in the app config, so the old box can't be picked even if
it is still in HA. `ATV_ADDRESS=<ip>` overrides.

```bash
ssh homeassistant sudo rm /app_configs/local_streamdock_n3/atv_companion.json
bash homeassistant/pair.sh atv            # Companion: knob preview scrubbing
bash homeassistant/pair.sh atv airplay    # AirPlay: Pluto / Peacock ad muting
bash homeassistant/pair.sh youtube atv    # YouTube > Settings > Link with TV code
bash homeassistant/pair.sh status         # warns if still paired with another address
```

Remove `atv_companion.json` first: `restore.sh` skips pairing while it
exists, and the app keeps connecting to the address stored in it.

### Retest on the TV

- [ ] Key 3 both ways (LG off + box asleep; box wakes the LG over CEC),
      Round 1–3, keys 5 and 6, both knobs in menus.
- [ ] Knob scrubbing with previews in the Apple TV app, Max, Netflix;
      YouTube (scrubs without pausing) and Disney+ (`seek`). The touch window
      (`touch_min_x` 300 / `touch_max_x` 800) and steps were measured on the old box.
- [ ] Ad muting on a Pluto TV channel and a Peacock episode (pre-roll and
      mid-roll). `mrp_raw_log` is on, so `mrp_raw.log` shows what the new box
      sends if a rule misses.
- [ ] Next chapter in YouTube; the tabs following the app. (Soccer key:
      Paramount+ checked 11-14 on 0.6.3-1.)
- [ ] If pairing fails outright, check pyatv's releases for the new box /
      tvOS before anything else: the app pins `pyatv==0.18.0` and
      `srptools==1.0.1`.

### The old box

- [ ] Gone from HA (above). If it stays on the network in another room,
      don't re-add it under the `vugitv_4k` name.
- [ ] Before giving it away: Settings → System → Reset.

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

- The large knob drives Apple TV scrubbing; Sonos volume/mute is on small
  knob 1.

### 2026-10-23 — Home and Back keys

- Key 5 = Apple TV Home (house icon, `icons/home.png`); key 6 = Apple TV
  Back (`menu` command, return-arrow icon, `icons/back.png`). Both are
  112×112 white glyphs on `#262626`, drawn with Pillow. Image keys use `"icon": "/config/icons/<file>"`; the
  folder lives in the app's config share, so new icons need no rebuild.
  `restore.sh` deploys the folder.

### 2026-10-23 — TV power toggle

- Key 3 became a power toggle, `script.tv_room_power_toggle`, keyed on
  `media_player.lg_tv_2`. Power-on goes through the Apple TV (webOS can't power
  the TV on); called via `script.turn_on` so `ha-call` doesn't hit its 5 s
  timeout during the wake delay. Confirmed on the device in both directions.
- `ha_daemon.py` now treats any state except off/standby/unavailable/unknown
  as ON, so media players (`playing`, `idle`, …) work as state keys.
- `run.sh` prints `run.sh started <epoch>`; `restore.sh` reads only log lines
  after this start's marker. Before, recent button presses could push the
  status line out of view and trigger a false warning.

### 2026-10-23 — Adaptive Apple TV knobs

- Apple TV ignores `skip_*` outside playback, so the large knob must switch
  mode by the Apple TV media player's state: `playing`/`paused` → scrub,
  anything else → focus moves. Both knobs' presses send `select` (OK), which
  also pauses/resumes in the player.
- The logic lives in the app (`knob_accel.py`, driven from `ha_daemon.py`),
  not in HA scripts, because speed needs per-click timing:
  - Speed tier per click from the gap to the previous click on that axis
    (≥ 0.25 s slow, ≥ 0.08 s medium, else fast) selects the step:
    seek 10 s per click / 30 s per 2 clicks / 90 s per 3 clicks
    (`[seconds, clicks]` in config, i.e. 10 / 15 / 30 s per click), focus
    1 / 2 / 3 moves. Every click is scored at its own tier, so a turn that
    goes slow → medium → fast → slow changes rate as it goes (verified in
    simulation: 1 S + 4 M + 6 F + 3 S = +280 s). The first click after a
    pause is always slow, so fine control stays exact.
  - Clicks are coalesced into one HA call after 60 ms of quiet or 150 ms of
    batch age. While a call is in flight, new clicks collect into the next
    batch, so the queue never grows (handoff requirement).
  - Scrubbing uses `media_player.media_seek` to an absolute target and
    builds on its own last target for 3 s, because HA's reported position
    lags a seek. Apps without seek support fall back to `skip_*` (max 6 per
    call). Focus moves use `remote.send_command` with `num_repeats`
    (max 20, 50 ms apart).
  - HA calls go over one kept-alive HTTP connection to the Supervisor
    instead of a shell + curl per click.
- Tuning lives in the `atv` section of `config.json`; changing it needs only
  an app restart.
- Preview scrubbing (tvOS thumbnails while dragging) isn't reachable through
  HA: it needs touch-surface gestures, which pyatv 0.18 has (`touch.swipe`,
  `touch.action` press/hold/release over the Companion protocol) but HA's
  Apple TV integration doesn't expose. It would need pyatv in this app with
  its own Apple TV pairing (see Open items).

### 2026-10-23 — Preview scrubbing (pyatv touch)

- tvOS thumbnail-preview scrubbing comes from touch-surface drags while
  paused. pyatv 0.18 sends these over the Companion protocol
  (`touch.action` press/hold/release, 0–1000 coordinates); HA's Apple TV
  integration doesn't expose them.
- `atv_touch.py`: the app runs its own pyatv (0.18.0, same as HA) on an
  asyncio thread with a **Companion-only** connection and its **own
  pairing**, so it never shares an identity with HA's session. It finds the
  box by unicast scan to its address, and reconnects after a drop.
- Pairing uses files in the app's config share: `atv_pair_request.json`
  (address) → TV shows PIN → `atv_pair_pin` → `atv_companion.json` (secret,
  mode 600, not in the repo). `restore.sh` reads the address from HA's
  Apple TV config entry and prompts for the PIN when run in a terminal.
- `knob_accel.py` touch mode (`"scrub": "touch"`): pause if playing → press
  at centre → drag in 25-unit moves at 16 ms intervals (as pyatv's own
  swipe) → re-grip in the centre at x = 150 / 850 → lift after 0.6 s idle;
  the knob press (OK) commits. Speed tiers and batching are shared with
  seek mode (`touch_steps` 12 / 60 per 2 / 150 per 3 units per click).
  Falls back to seek whenever touch isn't connected.
- Confirmed on the TV: tvOS thumbnail previews follow the knob, and the knob
  press jumps to the previewed position.

- Paired with the Apple TV (PIN on screen) and connected over Companion;
  the connection comes back by itself after an app restart.
- `homeassistant/pair.sh`: `atv` (asks the Apple TV for a PIN, prompts,
  waits for the touch connection), `youtube <screen> [code]` (links a TV's
  YouTube app) and `status`. It judges success from the files on the Pi:
  a pairing request always gets consumed, and success leaves newer
  credentials. `restore.sh` calls it.

### 2026-10-23 — Key modes and YouTube next chapter

- **Key modes** (`ha_daemon.py`): a key lists `modes` with a `when`
  condition; the first match sets its icon/label/color and press action.
  Otherwise the key's base look and `action` apply. The press maps to
  `{"key_action": <key>}`, and keys redraw on every HA state change.
- **Detecting YouTube:** LG `media_player.lg_tv_2` has `source` = `YouTube`
  or `app_id` = `youtube.leanback.v4`; the Apple TV has `app_id` =
  `com.google.ios.youtube`.
- **Next chapter** needs the playing video and its position, which no
  remote or HA control exposes. YouTube's TV-link ("Lounge") protocol does
  (`yt_lounge.py`, pyytlounge 3.5.0, the library iSponsorBlockTV uses):
  link each TV's YouTube app once with its 12-digit "Link with TV code"
  (file `yt_pair_<screen>`, tokens in `yt_lounge.json`, secret). A session
  is held only while that TV has YouTube in the foreground.
- **Chapters** (`yt_chapters.py`) come from the public watch page with no
  API key: YouTube's `chapterRenderer` markers (creator + auto chapters),
  else description timestamps under YouTube's chapter rules. Verified on 6
  live videos (4–87 chapters each). Cached per video, prefetched when it
  starts.
- **Ads:** the same session reports ad start/end and skippability; logged
  as `youtube: <screen>: ad started (…)`. A Skip-ad key is possible
  (`skip_ad`).
- Icon `icons/next_chapter.png`: YouTube-red key, white ⏭ glyph with three
  chapter ticks (chosen over a timeline-jump design that was too busy at
  112 px).
- Docker: `pyytlounge` installs in its own layer, so the slow
  pyatv/miniaudio compile stays cached. miniaudio has no aarch64 wheel; its
  build needs `g++` (removed after) and `libatomic1` (kept).
- Confirmed on the Apple TV: linked "YouTube on TV" (`pair.sh youtube atv`),
  a 12-chapter video, and key 4 jumped 162 → 181 → 240 → 293 s across
  chapter starts.
- Gotcha: `ha apps logs` returns only about the last 100 lines by default.
  The scripts pass `--lines 1000`; without it, busy logs (knob turns) hid
  startup and pairing results.

### 2026-10-24 — Now-playing log for ad research

- Outside YouTube there is no ad signal, so `np_log.py` records how the Apple
  TV's HA media player changes: only changed fields per line (state, app,
  title, series/episode, content type/id, duration), plus `pos_jump` when the
  reported position doesn't match continuous playback since the last report.
  Output goes to the app log (`np:`) and to `/app_configs/local_streamdock_n3/now_playing.log`
  (2 MB cap). Enabled by `"now_playing_log": {"entities": [...]}`.
- Simulated ad break: quiet during normal progress; one line on the ad
  (title blanked, 30 s duration, position jumped to 0) and one on the way back.
- Live: logs the Apple TV app as `com.apple.TVWatchList` with title,
  content id, duration and position.
- **Tubi (`com.adrise.tubitv`), first capture** (ad break triggered by
  scrubbing past an ad marker; pod of 60 + 45 + 4 × 15 s = 165 s):
  - During the break HA reported nothing new. The state stayed `paused` for
    the whole 165 s while ads played, and title, duration and content id
    were unchanged.
  - At the end the state became `playing` with a forward jump of exactly the
    pod length (1719 → 1884 s), then about 2 s later a jump back to movie
    time (1885 → 1752 s). Tubi stitches ads into the timeline, then maps
    back.
  - So the end of a break is unambiguous; its start ("paused") looks like a
    user pause. The log now also records `supported_features` (Tubi normal:
    450487) and the position timestamp.
- **Second capture** (natural 30 s break; pause/play pressed during it): the
  break again started as `paused` on time, with `supported_features`
  unchanged. The pause/play during the ad left no trace (Tubi locks
  playback during breaks), and this time no end was reported at all; HA
  stayed `paused` after the movie resumed.
- Conclusion: Tubi ads can't be skipped (server-side stitched, playback
  locked). "Paused in Tubi" is the only live signal, and its end may never
  arrive.
- **Solution: `automation.streaming_pause_mute`** (HA automation, so it is
  visible in the HA UI and works without the app;
  `ha-config/automations/streaming_pause_mute.yaml`). Confirmed on Tubi,
  then generalized to every app. When the Apple TV goes `paused` in any app
  (app_id set) while the Arc plays TV audio (`source` = `TV`) and isn't
  muted, it mutes the Arc. It unmutes when the state leaves `paused`, the
  app changes, the Arc's source changes (music started on the Arc), or after
  5 minutes (the cap covers a missing end report). It only undoes its own
  mute: it skips if the Arc was already muted and backs off if unmuted by
  hand. A deliberate pause also mutes, which is harmless while paused.
- Arc `source` is `TV` while it plays the TV/Apple TV and empty for music
  (read from HA's recorder DB; Sonos states aren't in `core.restore_state`).
- The first, Tubi-only version (`tubi_ad_mute`) was removed from
  `automations.yaml`; its orphaned registry entry
  `automation.tubi_ad_break_mute` can be deleted in HA → Settings →
  Entities.
- `restore.sh` now adds missing automations by `id` (as it does scripts by
  key) and checks their entities. Fixed on the way: under `set -e`, a final
  `[ … ] && …` inside a function aborted the script when nothing was added.
- **Per-app pause reporting** (from `now_playing.log` and the recorder DB):
  - **Pluto TV** (`tv.pluto.ios`) reports `paused`; the Arc muted and
    unmuted on every pause/resume (23:05–23:08 on 2026-10-23).
  - **Netflix** (`com.netflix.Netflix`) reports a pause as `idle` and
    publishes no title/position, so a `paused` trigger never fired. The
    automation now also triggers on `idle` for apps in `idle_pause_apps`
    (Netflix), and resume is any state other than paused/idle. Trade-off:
    Netflix menus are `idle` too, so menu trailers stay muted until play,
    app change or the 5-minute cap.
  - The Apple TV shows no mute icon: the mute is on the Arc, not the Apple
    TV. So the dock shows it instead: key 3 has a mode (Arc
    `is_volume_muted` = true) that draws an amber "TV / MUTED", whoever
    muted. Key modes now coexist with `state_entity`: an active mode owns
    the look, and the key returns to its on/off look when the mode ends.
    Seen live: key 3 switched to MUTED and back with pause/resume.
- Confirmed on the TV in Tubi, Pluto and Netflix (idle-as-pause).
- `restore.sh` updates existing HA entries on request: each entry (script
  key / automation `- id:` item) is compared with the repo fragment. A
  missing one is added; a matching one is left alone; a differing one is
  reported and kept unless `--replace-ha-config` is given (backup first).
  Edits made in HA's UI are never overwritten silently.

### 2026-10-24 — Auto-resume after scrubbing

- After a scrub, 3 s without knob activity (`resume_after`) resumes playback.
  A touch scrub is first committed with select. If the Apple TV still
  reports `paused` 1.5 s later (`resume_check_delay`), `media_play` follows.
  A seek scrub only plays if paused.
- The large knob's press now goes through the knob engine
  (`{"atv_knob": {"press": true}}`), because a second select on a playing
  video toggles pause. A press within the 3 s commits the scrub with exactly
  one select and cancels the auto-resume; outside a scrub it is a plain
  select.
- The engine's sender thread works through one ordered job queue: scrub
  batches, then knob press, then idle finger lift, then auto-resume.
- Simulated: idle after a touch scrub (lift → select → play only if still
  paused), press during the wait (one select, no auto), seek scrub
  paused/playing, press in menus.
- Confirmed on the TV: idle after a scrub jumps and plays; pressing the knob
  commits once with no extra pause.

### 2026-11-01 — Restore onto a second Pi running HA Supervised (`arpi`)

- `arpi` (192.168.1.144, `ssh arpi` as andrey): Raspberry Pi 4, Ubuntu
  22.04, **Home Assistant Supervised** 2026.10.4 (Supervisor, OS Agent, `ha`
  CLI as `docker exec hassio_cli`). Data folder `/var/lib/homeassistant`
  (`/etc/hassio.json`). Host sshd: `PermitRootLogin no`.
- `restore.sh`, `pair.sh` and `tools/probe.sh` now share `ssh_ha.sh`: on a
  Supervised host (detected by `/etc/hassio.json`) commands run under
  `sudo -n sh -c` as a user with passwordless sudo, and the app paths are
  symlinked into the data folder, so nothing else in the scripts changed.
  Tested on both hosts (root on each, quoting intact).
- New restore step: the app's **watchdog** is turned on through the
  Supervisor API (`ha_api_post`; the CLI has no option for it). A fresh
  install had it off, which would have disabled the dock self-restart.
- `restore.sh arpi`: scripts and the automation added (core restarted), app
  0.5.0-14 built and started, waiting for the dock. Missing on arpi:
  `media_player.lg_tv_2`, `media_player.vugitv_4k_vugitv_4k`,
  `remote.vugitv_4k_vugitv_4k` (pair LG webOS and Apple TV there), Apple TV
  touch/AirPlay pairing and YouTube links (`HA_HOST=arpi pair.sh ...`).

### 2026-11-01 — Apple TV pairing broke on a fresh build: srptools 2.0

- `pair.sh atv` on arpi failed: `PairingError: SRPClientSession.__init__()
  takes 2 positional arguments but 3 were given`. arpi's fresh image had
  pulled srptools 2.0.0 (pyatv 0.18.0 only pins `srptools>=…`); the old Pi's
  cached image and this PC have 1.0.1, which works. Dockerfile now pins
  `srptools==1.0.1`. Build 0.5.0-15.
- `restore.sh` "Starting app" retries for a minute: right after the update
  the Supervisor answered "not ready to perform this operation".
- Lesson: an unpinned transitive dependency can break a restore that the
  live system (old cached layers) never hits; a fresh build is the real test.

### 2026-10-27 — LG Media Player: what controls subtitles, audio and seeking (in progress)

Wanted on the `context:lg_media` page: knob scrubbing, subtitles on/off/next
and audio track next. Live probes (`tools/probes/lg_media_*.json`, each step
announced by a TV toast via `system.notifications/createToast`):
- `com.webos.media/getForegroundAppInfo` gives a **real mediaId** for the
  playing file (`_09ezunCbjac3Nd`; it changes per playback), unlike the `_`
  seen on webOS 7.
- Luna calls by mediaId via the createAlert hack (`seek`,
  `setSubtitleEnable`, `selectTrack` text/audio; alert confirmed with
  ENTER) changed nothing on screen. Direct `com.webos.media/setSubtitleEnable`
  is 404. `settings/getSystemSettings` caption read fails (500).
- Buttons: `CC` opens the "Internal Subtitle" panel (Subtitle switch
  focused; Track, Code Page, Sync, Position, Size); a second `CC` does not
  close it. `SAP` does nothing. `QMENU` opens the TV's own quick menu.
  **`BACK` with no menu open quits playback** - never send it blind.
- Audio track lives in the playback bar: bottom row `SUB`, replay, rewind,
  repeat, fast-forward, 360, `⋯` -> Options (Resume Play, Play Speed, Audio
  Track, progress bar) -> Audio Track list (radio buttons).
- Not yet known: whether RIGHT/LEFT or `FASTFORWARD` seek (for the knob), and
  the exact presses from playback to `⋯`. Next session: one button at a
  time with the user watching, then encode the sequences as key actions and
  give the lg_media page its own knob actions.

### 2026-10-27 — Dock back without device access: the app restarts itself

- The dock dropped off USB again (1:11 → 1:12) and came back, but the new
  nodes were not granted to the container: `hidraw nodes are not readable`,
  `event0: Operation not permitted`, and the dock showed its built-in images.
  `ha apps restart` fixed it (as on 10-25).
- `run.sh` now opens the dock's hidraw (read-write) and input nodes before
  each daemon start; still blocked after ~10 s, it exits 1 and the
  Supervisor watchdog (on for this app) restarts the app in a fresh
  container. Pitfall found while testing: a failed redirect on `:` exits a
  POSIX shell, so the open runs in a subshell.
- Tested: in the Terminal & SSH container, which has the nodes but no
  device access, the check reports all three nodes; the app's own start
  passes it (0.5.0-14). The watchdog restart itself has not been seen yet.

### 2026-10-27 — Context pages follow what is on screen; service logo; PS5 keeps its sound

- **The Apple TV reports its now-playing app, not the foreground app.**
  After leaving a paused YouTube video, HA's `app_id` stayed YouTube (paused)
  while the TV app was open, so the dock showed the YouTube page. A capture
  (YouTube pause → TV app → Home → YouTube) showed no signal at all: HA,
  raw MRP (now also logging client lifecycle messages: `update_client`,
  `default_supported_commands`, ...) and Companion are silent on app
  switches. When another app plays something (a menu preview, a TV-app
  trailer) and stops, the Apple TV falls back to reporting the paused app.
- Rule (`context.AtvApp`, `tabs.context.min_play_s` 10): an Apple TV app
  gets its page only after playing 10 s without a break since it became
  the reported app; a pause keeps it. The TV app (`content_apps`) counts
  only for real content: `media_content_type` `video` with an id (trailers
  report no type, hero previews `music`). Verified live: Netflix and Prime
  menu previews, then the fallback to the paused YouTube, left the dock on
  the idle page.
- Still open: YouTube paused, then straight into the TV app with nothing
  playing there: nothing reports the switch, so the YouTube page stays.
  `youtube.debug_events` logs raw Lounge events to look for a signal.
- **Service logo** (`logo.py`, `tabs.logo`): key 6 of a context page shows
  the logo of what is on screen for 2 s every 10-15 s and keeps its action.
  App icons come from the apps-page launch keys; the idle page shows the
  PS5 logo on the `PS5 Game Console` input (`com.webos.app.hdmi3`), else the
  Apple TV logo. New `icons/ps5.png` (Simple Icons `playstation5`, CC0).
  Verified on LG Media Player: 18 flashes in 215 s, key 1 followed the LG's
  playing/paused, Home went back to idle.
- **PS5 was muted** by the `streaming_pause_mute` automation whenever the
  Apple TV paused, whatever the LG showed. It and the Pluto ad muting
  (`ad_detect` `lg` + `atv_source`) now act only on the `Apple OTT` input;
  the automation also unmutes on an LG input change.
- The Dockerfile lists modules by name: `logo.py` was missing at first
  (`ModuleNotFoundError`); `test_dockerfile_copies_every_module` guards it.
- Builds 0.5.0-12 and 0.5.0-13, deployed with `restore.sh
  --replace-ha-config` (automation updated).

### 2026-10-26 — YouTube key 2: next chapter, else +10 s

- The YouTube context page's key 2 shows "next chapter" only while the
  playing video has chapters (condition `youtube_chapters`); otherwise it
  is `skip_fwd_10` and seeks +10 s through the Lounge (`{"youtube":
  "forward"}`, from the tracked position, so repeated presses add up).
- On a video change (next/previous, autoplay) the key falls back to +10 s
  at once and switches to "next chapter" when that video's chapters load;
  cached videos switch immediately. Chapter loading now redraws the keys.
  `tests/test_youtube.py`. Build 0.5.0-11, deployed to the Pi with
  `restore.sh` (config.json from `config.tv_room.json`); on a video with no
  chapters the log shows `0 chapters` and key 2 stays on +10 s.

### 2026-11-01 — Peacock ad detection (frozen content clock)

- Captured with the user marking breaks: a pre-roll (09:42:10–09:42:42) and
  a mid-roll (09:45:25–09:46:19) in "Halloween Surprise". HA sees nothing
  (`playing`, position frozen, then a `pos_jump`). MRP: Peacock has one
  player, its `MediaRemote-DefaultPlayer`; at an ad start it republishes
  `supportedCommands` and publishes the content clock with `playbackRate`
  0 in the same second while the state stays `Playing`; nothing during the
  ad; at the end the same republish plus a clock update at rate 1. A scrub
  (09:44:40–45) also gives rate-0 clock updates, but the clock jumps and
  there is no republish; a pause changes the state.
- `FrozenClockDetector` in `ad_detect.py` (config `ad_detect.frozen_apps`,
  TV Room preset: Peacock): start on a rate-0 clock within 3 s of a
  republish while Playing and no clock jump in the last 3 s; end on a
  rate > 0 clock, a state other than Playing, an app change, or 5 min.
  Same mute rules. Replay (`replay_ad_detect.py --frozen-app`) matches both
  windows; fixture `tools/fixtures/peacock_ads_20261101.jsonl`. 0.5.0-20.

### 2026-11-01 — Pluto ad end: no live signal, so a 40 s heartbeat wait

- On arpi (0.5.0-16) both breaks of the morning were caught at their start
  but ended only by the 90 s heartbeat timeout, ~86 s after the real end.
  The stream player's `supportedCommands` republish that ended breaks on
  10-24 no longer arrives; the raw MRP capture shows Pluto's AVKit players
  (the ad plays in its own interstitial player) publishing nothing at pod
  start or end, and the main player's clock frozen at the pre-ad snapshot
  throughout. The only trace of a break is the pinned show-clock beat every
  15-30 s (09:20:03, :33, :48, 21:18, :33, 22:03, :33; the pod ended ~7 s
  after the last one).
- Polling cannot do better: an MRP playback-queue request (new probe step
  `mrp_queue`, with `legacy`, `include_metadata`, `client`/`player` and
  `raw` options) returns the same stored snapshots for every player and
  form, so neither clock is live on demand. A "10 s heartbeat" would unmute
  inside every break.
- Fix: `heartbeat_min` 40 s / `heartbeat_factor` 1.5 x the last beat gap,
  capped at `heartbeat_timeout` 90 s; a beat after the end starts the ad
  again (mute back on). Today's break: unmute ~38 s after the end instead
  of 86 s. Replay of the 10-23 fixture: first break unchanged; the second
  (a skipped beat) now ends 2 s early and re-mutes for those 2 s. 0.5.0-19.
- Two mutes stuck during the session were my restarts (an app update wipes
  the detector while a pod runs; Pluto also runs a pre-roll pod at a
  movie's start). Don't update the app during a Pluto break.
- Knob: scrubbing in Pluto reacted within 0.7 s of the first click (pause
  through HA plus the 0.4 s delay); the user's earlier 30 s lag was with a
  thumbnail app, still to be timed there.

### 2026-10-25 — Stale touch connection: drop, reconnect, seek the batch (from #12)

- Ported from the unmerged PR #12 (10-24 TV checks): after the Apple TV
  sleeps, the app's Companion session still looks connected but every touch
  event fails ("Send event failed"; pyatv gets no `connection_lost`), so
  the knob did nothing in any app until the app restarted. Now a failed
  touch batch drops the connection (`AppleTVTouch.drop`, `_supervise`
  reconnects) and is sent as a seek, scaled from touch units to seek
  seconds; later turns use seek until the connection is back.
  `tests/test_knob_touch.py`. Build 0.5.0-10.
- Left out of #12: the 50 ms finger keepalive (its premise, the resting
  finger, was refuted by the touch-wrap finding; all apps work without it)
  and the 2.5 s lift (already in main).

### 2026-10-25 — Apps page 2: live-soccer key instead of the Apple TV input

- Round button 1, second page (`apps_more`), key 6: the live-soccer key
  (`soccer_live__none` / `__live` by `soccer_live`, action `soccer: start`)
  replaces the "Apple TV input" key (LG source → `Apple OTT`). The same
  key stays on the live-game context page; the input switch remains on key
  3 of the Sonos/context page. `test_every_soccer_action_is_known` now
  checks the set of actions, since `start` appears twice. Deployed to the
  Pi (config.json from `config.tv_room.json`, app restarted).

### 2026-10-25 — Scrubbing with preview: the touch surface wraps below x = 256

- Research session with the probe tool (notes with all numbers:
  `docs/superpowers/specs/2026-10-25-scrub-preview-research.md`). One
  cause behind every "backward scrub jumps forward": pyatv encodes the
  `_hidT` touch coordinates with OPACK, which uses a one-byte form below
  256, and tvOS misreads it. Apple TV app, 300-unit left swipe ending at
  x = 270: −181 s; ending at 260: +1910 s. Right edge clean to 950. The
  knob engine dragged to `touch_min_x` = 150, so long backward drags always
  crossed it; forward drags (500–850) never did.
- With the finger kept in 300..800: Apple TV app, Max and Netflix scrub
  both ways with their previews; YouTube too, but only without a pause
  first (its own scrubber opens on the swipe; an HA pause moves focus to
  the control row and swipes walk the buttons). Keys while paused are a
  second mechanism (10 s per press; Apple TV app commits on OK, Max moves
  at once, YouTube needs Up first).
- Fix (0.5.0-9, confirmed on the TV in Max, YouTube, Netflix and the
  Apple TV app, both directions with previews): `touch_min_x` 300 /
  `touch_max_x` 850 with the re-grip at 575; `scrub_apps` entries may be
  `{"mode": ..., "pause": false}` and YouTube has `"pause": false`; the
  seek override for Max and YouTube is gone.
- Tubi: scrubs correctly (swipes −41/−32/+45/−13 s; keys −29/−50 s) but
  shows only the playhead and time, no thumbnail, with either method: its
  AVKit player has no trick-play images for the stream. Not fixable here.
- The rest of the dock's apps, same two checks each, user watching: Prime,
  Paramount+, Hulu, Peacock and Pluto scrub both ways by touch with
  thumbnails (keys work too, 10 s per press in most). **Disney+ ignores
  touch swipes** and shows no thumbnail for key presses (the picture itself
  jumps); HA `media_seek` lands exactly (856 → 615 s), so it is the one app
  on `"seek"` in `scrub_apps`. Table with every number in the research
  notes.
- New probe step `touch` (swipe / event list); experiments in
  `tools/probes/scrub/`.

### 2026-10-25 — Backward scrubs jumped forward: Max and YouTube don't scrub by touch

- Symptom: in Max, slow or fast backward turns of the large knob ended in a
  big jump forward (e.g. 856 → 1309 s, 1505 → 1911 s). In YouTube the knob
  hardly moved the position at all.
- Not the cause (tested live, one variable at a time): the gap between the
  finger lift and the select (lift+select back to back still jumped +453 s),
  the resting finger (lifting 50 ms after the last click still jumped
  +406 s, and single-click swipes then did nothing), the auto-resume (not
  sent in these sessions). The raw MRP log showed the player paused at the
  pre-scrub position throughout the gesture and jumping only when the
  select committed.
- Cause: Max's player moves its scrub preview **forward whichever way the
  finger swipes**. Measured with the new `touch` probe step (one clean pyatv
  swipe, then OK, paused at the same spot each time): left 300 units in
  300 ms +519 s, right 300 units +112 s, left in 1500 ms +487 s, the knob
  engine's own event shape +542 s. YouTube's player ignores swipes (219 →
  206 s after 700 units). The one backward session that had worked in Max
  (12:30 on 10-24) is unexplained; everything else fits. tvOS AVKit players
  (Apple TV app) still scrub by touch as designed.
- Fix: `atv.scrub_apps` in `knob_accel.py` overrides the scrub mode per
  `app_id`; the TV Room config sets Max (`com.wbd.stream`) and YouTube
  (`com.google.ios.youtube`) to `"seek"` (HA `media_seek`, which Max lands
  exactly both ways: 3926 → 3575, 3582 → 3695; MRP `SkipBackward` did
  nothing in Max). Confirmed on the TV in both apps. Seek mode has no
  thumbnail preview and no pause: 10 s / 30 s per 2 / 90 s per 3 clicks.
- `touch_release_after` had been tuned to 2.5 s on the Pi during the
  10-24 sessions; now in the repo config too.
- Probe tool: `touch` step (`"swipe": [x0, y0, x1, y1], "ms"` or an explicit
  `"events"` list); experiments in `tools/probes/scrub/`.

### 2026-10-25 — Knob rotations lost after a USB reset (scrubbing "broken")

- Symptom: the large knob did nothing while playing; no scrub, and no Sonos
  volume from small knob 1 either. The knob engine, touch connection and
  config were fine: the app log showed knob presses and keys arriving but
  not one `rotated` line for 10 hours, against ~6400 before.
- Trigger: at 12:53 EDT on 2026-10-24 the dock dropped off the USB bus by
  itself (`dmesg`: disconnect, re-enumerated 0.35 s later), the daemon's
  write failed with `Protocol error`, and `run.sh` restarted the daemon on
  the new node (`hidraw1`, since the dying daemon still held `hidraw0`).
  Presses and keys came through that node; rotations never did.
- Not fixed by: another daemon restart, or a physical replug (back on
  `hidraw0`, device number 5). Fixed by: recreating the app container
  (`ha apps update` to 0.5.0-6), which did not touch the USB bus (still
  device 5, same node) — rotations came back at once and scrubbing worked
  end to end (touch drags, auto-commit, resume). So the stuck state lived in
  the old container, not in the dock, the kernel node or the daemon process;
  the mechanism is unknown (device access is per node, and presses used the
  same node). **Remedy when it happens again: `ha apps restart
  local_streamdock_n3`, not a dock replug.**
- Diagnostics added: probe step `tap` and `tools/probes/knob_tap.json` read
  the dock's raw reports alongside the daemon (hidraw gives every reader
  every report). The Terminal & SSH app cannot open the device nodes or
  write sysfs (`authorized`, `unbind`: read-only), so a USB reset from the
  Pi is not possible; only a replug or the app restart.
- Also seen: options `scrub_seconds_*` / `touch_step_*` had been saved in
  the app's Configuration tab (21:57 on 10-24). They are not in the schema
  (Supervisor warns every 15 min) and the daemon never reads them; scrub
  tuning lives in `config.json`'s `atv` section only. Worth removing there.

### 2026-10-24 — Pluto TV ad detection (raw MRP)

- Pluto shows nothing in HA when an ad starts and a false `paused` when it
  ends (which the pause-mute turned into a wrong mute of the show). MRP
  metadata has no ad flag (104 fields in pyatv's `ContentItemMetadata`).
- **Diagnostics:** the app can also pair the Apple TV's **AirPlay** service
  (`pair.sh atv airplay`, PIN on the TV). pyatv then opens its own MRP
  tunnel, and `mrp_raw.py` logs every now-playing message (all players,
  content metadata, decoded `nowPlayingInfoData` plists) to `mrp_raw.log`.
  Parsed with protobuf `MessageToDict`; the C-backed repeated containers on
  the Pi don't look like sequences to hand-written walkers.
- **Finding (3 breaks):** Pluto plays the stream in AVKit players
  (`avkit-…`, duration with ads, e.g. 7306.7 s) and publishes a **show
  clock** on `MediaRemote-DefaultPlayer` (ads excluded, e.g. 7005 s). It
  publishes the show clock only during ads (pinned at the cue point,
  republished every 30 s, 15 s near the end, one beat seen skipped) and
  around user pause/play/scrub (always with an AVKit state change). Each
  break ended with the AVKit player republishing `supportedCommands`.
  Seek/skip commands stay enabled during ads.
- **`ad_detect.py`** (config `ad_detect`): ad starts on a show-clock update
  while the stream plays and no stream state change happened in the last
  5 s; ends on the AVKit commands republish, a show clock advancing again,
  heartbeat silence over 90 s, or the app changing. It mutes and unmutes the
  Arc with the automation's rules. Pluto is in the automation's
  `excluded_apps`.
- Replay (`tools/replay_ad_detect.py`, sanitized fixture
  `tools/fixtures/pluto_ads_20261023.jsonl`) detects both reported breaks
  exactly: 23:57:19–23:59:51 and 00:09:21–00:11:52. The first live version
  needed a clock jump and caught the second break 30 s late, which led to
  the "publication itself is the signal" rule. Its 45 s heartbeat timeout
  also split a break at a skipped beat, now 90 s.
- Confirmed live: `00:18:32 ad started` → muted, `00:21:03 ad ended` (AVKit
  commands republish) → unmuted, matching the user's report.
- `docs/AD_DETECTION_GUIDE.md` lets other agents extend this to more apps.
- `mrp_raw_log` turned off after the Pluto research (`false` in the `atv`
  section). Ad detection doesn't need it; set it to `true` to research the
  next app (see the guide). The last capture stays on the Pi as
  `mrp_raw.log` until deleted.
- Found on the way: `scripts.yaml` had been re-saved from HA's UI
  (reformatted) and holds a script not from this repo,
  `netflix_on_apple_tv`; `--replace-ha-config` restored the repo formatting
  of the managed scripts and left it alone.

### 2026-10-23 — Repository

- Published to [a-tsygankov/action-ring](https://github.com/a-tsygankov/action-ring)
  (public): `main` started with the original handoff `.docx`, and everything
  else landed through a pull request. The repo holds no secrets: Home
  Assistant calls use the app's Supervisor token at runtime.

### 2026-10-23 — Home Assistant MCP for Claude

- HA's built-in **Model Context Protocol Server** integration was already
  installed (entry "All LLM APIs", `require_admin: true`). Its endpoint is
  `http://homeassistant.local/api/mcp`: HA Core listens on port 80 here, not
  8123. Without a token it answers 401.
- Claude Code connects over streamable HTTP with a long-lived access token
  sent as a Bearer header, registered at user scope (`home-assistant`), and
  `claude mcp get` reports Connected. No proxy (`mcp-proxy`) is needed.
  Its tools load in new Claude sessions.
- Not in `restore.sh`: the integration and the token are manual steps
  (Restore → Prerequisites, step 6), because the token belongs to a user and
  must not be in the repo.

### 2026-11-01 — Presets out of the Supervisor's manifest scan

- The Supervisor finds app manifests with `**/config.*` (`.json`, `.yaml`,
  `.yml`), recursively, so the shipped presets `config.default.json` and
  `config.tv_room.json` were read as manifests on every store reload and
  logged `Can't read …/config.tv_room.json: Invalid app config!`.
- They are now `presets/default.json` and `presets/tv_room.json` (DisplayPad:
  `presets/default.json`). The live config is still `/config/config.json`;
  `run.sh` seeds it from `/opt/<app>/default.json` on first start only, so
  existing configs are untouched. `restore.sh` replaces the app folder on
  upload, which removes the old files from `/local_apps/`.

### 2026-11-07 — Peacock pre-rolls at an episode start; soccer key picks at once

- Symptom: a Peacock episode's opening ad played unmuted, and often the
  show was then muted after it. The app log showed, ~31 s after each new
  episode, `ad started` and `ad ended (clock running again)` in the same
  millisecond, with `muted` logged after them. The raw MRP capture
  (`mrp_raw_log` was on) and a replay through the detector pinned four
  faults, each seen live:
  1. The pre-roll's first clock (elapsed 0, rate 0) was compared with the
     previous episode's clock (~2,550 s), counted as a scrub, and the start
     was blocked for 3 s, covering the republish. Only the first episode
     after an app restart was caught. Now a jump is measured against the
     same content item's last clock (MRP `contentItem.identifier`), so a
     title not seen before can't jump. Going back to the same episode
     after another app still counts as a jump (22:29 on 11-05: rewind on
     resume).
  2. 03:44:37 on 11-07: the pre-roll clock came inside a `set_state`
     playback queue, which the detector ignored. Clocks in `set_state`
     queues now feed it too.
  3. The start check paired a republish with a rate-0 clock *any* time
     earlier, so the republish that *ends* a missed pre-roll started a 0 s
     "ad". The republish must now come within 3 s of the rate-0 clock.
  4. Mute and unmute ran on separate unordered threads; the unmute (nothing
     to undo yet) could run first, and the mute then stuck on the show
     (Pluto too). Each thread now applies the state wanted when it runs,
     one at a time.
  Replay of the 11-05..07 capture (`tools/fixtures/peacock_episodes_20261107.jsonl`,
  sanitized; the sanitizer now tags clocks with an anonymous `item-N`)
  catches all 7 episode starts with no 0 s windows; the 11-01 Peacock
  fixture still matches. `test_ad_detect_episodes.py`. The Pluto fixture's
  `--expect` predates the 40 s heartbeat (it ends one break 2 s early and
  re-starts it); unchanged by this.
- Soccer key: a press used to start a fetch + power-on + play of game 1
  with nothing on the key until the game opened, and every press played
  game 1 again. Now `{"soccer": "start"}` picks at once and the key shows
  `1/8` (green band); presses within a minute, or while a started game is
  on screen, move to `2/8`, `3/8`, … (wrapping); after presses settle for
  1.5 s (`switch_after_s`) it powers on and plays only the last pick; a newer
  pick cancels an older switch, including its 8 s `select` check. The pick
  follows its game id across list refreshes. While games are live the key
  shows `N LIVE`. A key with an icon and a label now draws the label on a
  band across the icon's bottom (`badge.py`); no other key had both.
  Condition `soccer_picking`; the ticker redraws when the pick's minute
  runs out.
- `mrp_raw_log` is on in the TV Room preset now (it was on live, off in the
  repo), so a deploy keeps the capture used to check this. 0.5.0-21.

### 2026-11-07 — Key layout: Night and subtitles on tab 2A, back/forward pairs, 5 s tabs

- Night sound left every context page (idle, streaming, LG media, LG app:
  key 4 is now blank; live game: key 3). It stays on tab 2A (`sonos`, round
  button 2 once), key 1. YouTube subtitles moved from the YouTube page to
  tab 2A key 5, in place of the subwoofer toggle (removed from the dock).
- Back/forward pairs sit side by side on one row, back on key 2 or 4:
  streaming and live game ⟲10/⟳10 on 2+3 (live game: NEXT game moved to
  key 4), LG ⏪/⏩ on 2+3 (unchanged), YouTube previous/next video on 2+3 and
  −10 s/+10 s (next chapter) on 4+5. New `{"youtube": "rewind"}` (−10 s,
  never before 0). `test_back_and_forward_keys_pair_on_one_row`,
  `test_night_and_subtitles_only_on_tab_2a`.
- Temporary tabs close after 5 s without a press (`open` 10 → 5;
  `after_press` was already 5), and as soon as a new context layout is
  adopted (TV off, another app or input; after the 1 s debounce).
- When the default (`context:idle`) page shows, checked against the
  now-playing log: the LG off/standby/unavailable; the LG on its
  home/HDMI/live TV app; the Apple TV input with no app that counts. The
  Apple TV asleep reports `off` with `app_id` None (seen 8 times 11-04..06),
  so it is idle too. The TV app counts only for real content that has
  played 10 s; opened or browsing (`idle`, no title) it is idle, while a
  paused title keeps the streaming page (as asked). No rule change was
  needed; the gap was a temporary tab staying up over it, fixed above.
  0.5.0-22.

### 2026-11-07 — Peacock mid-roll missed after an app restart: clocks run from their stamps

- Symptom: right after the 0.5.0-22 deploy (04:42:54), the 04:44:12–04:45:04
  mid-roll played unmuted. On reconnect the Apple TV sends a state snapshot
  whose clock is "0.5 s at 04:33:10" (rate 1): true, but 10 minutes old. The
  jump check extrapolated it from its *arrival*, predicted ~79 s at 04:44:12,
  saw 661 s, took the ad for a scrub and blocked its start.
- Fix: a clock is extrapolated from its own `elapsedTimeTimestamp`
  (0.5 + 661 = 661.5 s, the ad showed 661.1 s). Live clocks carry a stamp
  within ~1 s of arrival, so nothing else changes. Fixture
  `tools/fixtures/peacock_reconnect_20261107.jsonl` (what the restarted app
  saw); `test_ad_detect_episodes.py`.
- `replay_ad_detect.py` now keeps time running across midnight. Before, the
  wrap gave a false jump that hid a stale scrub time; with it fixed, the
  11-05..07 replay also has 00:06:25–00:07:56, a mid-roll the live app did
  catch on 11-06. 0.5.0-23.

### 2026-11-07 — The ad mute survives an app restart

- Seen on the 0.5.0-23 deploy (04:50): the old app muted a Peacock mid-roll
  at 04:50:36 and was replaced ~10 s later. The new app saw the ad still
  running (the reconnect fix worked) but found the speaker muted, took it
  for the user's mute, and would not unmute at the ad's end.
- Each detector now keeps "the mute is ours" in `/data/ad_mute_frozen.json`
  / `ad_mute_show_clock.json` (written on every change; ignored when older
  than 10 min or unreadable). A new app inherits it: the ad's end unmutes as
  usual, and if no ad shows up within 20 s the inherited mute is undone. A
  user's mute is still left alone. `test_ad_detect_restart.py`. 0.5.0-24.

### 2026-11-13 — Replacing the Apple TV: checklist, pick the right HA entry

- New section [Replacing the Apple TV](#replacing-the-apple-tv): what is
  tied to the box (HA entry, entity IDs, the app's Companion and AirPlay
  pairings, the YouTube link, the LG input name, CEC) and the steps in order.
- `pair.sh` and `restore.sh` took the address of the *first* `apple_tv`
  entry in HA. With the old and new box both in HA (mid-swap, or a box in
  another room) that could pair the app with the wrong one. They now use the
  entry that owns `atv.media_player` from the app config (`atv_address` in
  `ssh_ha.sh`; the only entry when the config has none); `ATV_ADDRESS`
  overrides. `pair.sh status` warns when `atv_companion.json` holds another
  address than that entry (a replaced box, or a new IP).
- The AirPlay pairing is no longer described as "diagnostics": ad muting
  (Pluto, Peacock) runs on that MRP session.

### 2026-11-13 — Google OAuth app published; home and privacy pages on GitHub Pages

- Google Cloud project `action-ring-home`, Google Auth Platform: app name
  `action-ring-home-client`, user type External, one client
  `action-ring-tv` (TV and Limited Input). No scopes on Data Access yet.
- Publishing was blocked ("complete your configuration on the Branding
  page") until Branding had a home page, a privacy policy link and an
  authorized domain. Added:
  - Authorized domain `a-tsygankov.github.io`
  - Home page <https://a-tsygankov.github.io/action-ring/>
  - Privacy policy <https://a-tsygankov.github.io/action-ring/privacy.html>
- The pages live on the orphan branch `gh-pages` (only `index.html`,
  `privacy.html`, `.nojekyll`); GitHub Pages serves that branch, so `docs/`
  is not published. Edit the pages there.
- Publishing status moved from Testing to **In production**: refresh tokens
  no longer expire after 7 days. Unverified, so sign-in shows Google's
  "unverified app" screen and the lifetime cap is 100 accounts. No logo on
  purpose: a logo triggers brand verification. "Back to testing" on the
  Audience page undoes it.
- The privacy policy is written for any scope (data stays on the HA device,
  not shared or sold, Limited Use). Recheck it when scopes are added.

### 2026-11-13 — Dynamic keys: one key, functions in turn, with a warning

- Asked for: a key that changes its image and function on a timer (e.g.
  YouTube on the Apple TV: 5 s thumbs up / 5 s thumbs down), with a 1 s
  warning before each change so the wrong function isn't pressed; several
  such keys per context page.
- `dynamic.py` (pure timing; Pillow overlay) and a `"dynamic"` section on
  any key spec: `phases` laid over the key, `hold_s` per key or phase,
  `warn_s`, `warn` = `countdown` (3-2-1, top-right) | `bar` (bottom,
  shrinking; sits above a label band) | `dots` (top, going out),
  `warn_color`, `grace_s`. DOCS.md "Dynamic keys".
- The clock runs from when the page was shown (`TabEngine.shown_at`), so
  a page opens on phase 1. The renderer now sleeps until the next frame of
  a dynamic key on screen instead of only waiting for a signal; frame-only
  passes don't log.
- A press resolves to the phase the renderer last *drew* (not the clock),
  or the previous one within `grace_s` (0.25 s) of the swap.
- Entities in phases are subscribed; the preset checks cover phases.
- Not in the preset yet: there is no like/dislike action for YouTube on
  the Apple TV (the Lounge API has none). Tests: `test_dynamic.py`,
  `test_dynamic_render.py` (daemon with the device stubbed). 0.5.0-25.

### 2026-11-13 — YouTube page: next/previous and like/dislike as dynamic keys

- Asked for: thumbs up/down via the YouTube Data API (option 1 of two;
  the other was clicking through YouTube's on-screen controls), previous/
  next video as one dynamic key, like/dislike on the freed position.
- `context:youtube` now: 1 play/pause, **2 next ⇄ previous video**, **3 like
  ⇄ dislike**, 4 −10 s, 5 +10 s / next chapter, 6 Back. Both dynamic keys
  hold each phase 5 s with a 1 s 3-2-1 countdown and switch together. A
  thumb is blue and filled when the video has that rating.
- Why the Data API: pyytlounge 3.5 (the TV link) has no rating command
  (only play/pause, seek, next/previous, captions, d-pad), and the Apple TV
  remote has none either. `yt_rating.py`: `videos.getRating` once per
  video (lazily, from the key's condition; 60 s back-off after a failure),
  `videos.rate` on a press, optimistic with a fall-back. Like on a liked
  video clears it. Commands `{"youtube": "like" | "dislike"}`, conditions
  `youtube_liked` / `youtube_disliked`, config `"youtube": {"rating": true}`.
- Sign-in: Google's device flow (scope `youtube`), started by the file
  `yt_rate_auth` or `pair.sh youtube-rating <client.json>`; the code to
  enter at google.com/device is printed. Only the refresh token is kept
  (`yt_oauth.json`, 0600). The OAuth app must be published ("In
  production"): in Testing, Google expires the refresh token after 7 days.
  The allowed-scopes page for the device flow could not be checked from the
  build sandbox; Google's YouTube Data API device-flow guide implies the
  scope is allowed. If Google refuses it, the sign-in log line says so.
- Icons `thumb_up__off/on`, `thumb_down__off/on` (Material Symbols thumb-up
  / thumb-down, rounded; filled on `RATED` #1971c2). Iconify is blocked in
  the build sandbox, so their SVGs came from the npm package
  `@iconify-json/material-symbols` (the same data the Iconify API serves;
  `next_video` rebuilt from it matches the committed PNG pixel for pixel).
- Untested on the device and against Google (no network from the build
  sandbox): needs a sign-in on the Pi, then a like/unlike/dislike on a real
  video. Tests: `test_yt_rating.py` (fake Google), the YouTube page through
  the daemon in `test_dynamic_render.py`, preset checks. 0.5.0-26.

### 2026-11-13 — Like/dislike live on arpi: signed in, Data API enabled

- Merged with main as #31 (plus: `pair.sh` checks the client JSON with the
  Pi's `jq`, since Git Bash on the PC has none; tests portable to Windows;
  `*.local.json` ignored). Deployed with `restore.sh`: 0.5.0-26 running,
  dock connected, Apple TV and YouTube (atv) pairings kept.
- `pair.sh youtube-rating homeassistant/client.local.json` (client
  `action-ring-tv` from project `action-ring-home`, kept next to the repo,
  not committed). The device code went in at google.com/device; the
  personal account (the one YouTube on the Apple TV uses, not the brand
  account) chose "Advanced > Go to action-ring-home-client" on the
  unverified-app screen and allowed "Manage your YouTube account".
  `pair.sh status`: like / dislike: signed in.
- The first `getRating` failed with HTTP 403 "YouTube Data API v3 has not
  been used in project ... or it is disabled": the sign-in works without
  the API, the calls don't. Enabled YouTube Data API v3 in the project
  (APIs & Services). After the 60 s back-off no further 403.
- Checked on the TV: key 3 in phase 1 (like) on a playing video logged
  `like`, again `none` (unlike), again `like`; on the next video `like`.
  No errors.

### 2026-11-13 — YouTube dynamic keys: 8 s per option, 1.8 s shrinking bar, shake on press

- Asked for: longer turns and a longer, clearer warning. Keys 2 (next ⇄
  previous) and 3 (like ⇄ dislike) on `context:youtube`: `hold_s` 5 → 8,
  `warn_s` 1 → 1.8, `warn` `countdown` → `bar` (a bar along the bottom that
  shrinks in 5 steps, one every 0.36 s). Preset only; no code change.
  `test_dynamic_render.py` follows the new timings.
- Then: a longer pause between the bar's segments. New `warn_steps` (any
  style; default 3, `bar` 5, at most 20): the YouTube keys use 3, so each
  segment stays 0.6 s.
- Then: a press on a thumb shows an animation of the thumb pressed. A phase
  may set `"press": "shake"`: the key keeps that phase's look (the liked
  thumb turns blue at once) and wiggles it, 7 frames 0.09 s apart (−16° to
  0°, turned about a point below the centre; the uncovered corners take the
  tile's own background). During it there is no warning and a press means
  that phase again; the timer keeps running, so a shake across a switch
  ends on the next phase. `dynamic.Pressed` / `anim_frame` / `draw_anim`;
  `_press_anims` in `ha_daemon.py` (set on a press, cleared by the
  renderer); `Look.anim`. Like and dislike on `context:youtube` have it.
  Tests in `test_dynamic.py` and `test_dynamic_render.py`. 0.5.0-27.

### 2026-11-13 — YouTube grid: the knob moved focus only fast, and clicked a tile

- Symptom: in YouTube on the Apple TV, picking a video with the large
  knob: unexpected clicks (a tile opened), and slow turns didn't move the
  focus. While watching, the first scrub started late; later ones at once.
- Logged with timestamps (`docker logs -t`): the Apple TV reports the
  YouTube player as **paused** while the grid is on screen (playing while
  a video plays full screen), so the knob ran its touch scrub there: the
  finger's swipes moved focus only when big (fast tier), and 2.5 s + 0.5 s
  after the last click the auto-commit `select` clicked the focused tile.
  With `resume_after` 0 as a test: no clicks in the grid, but full screen
  the scrub then stays on its preview until a press, so the commit is
  needed there.
- Also seen: a re-grip at the touch surface's edge (a long fast turn)
  pauses YouTube about 0.5 s later; the auto-commit `select` then resumes
  it and lands the scrub, so it is left as is.
- Fix (`knob_accel.py`, YouTube's `scrub_apps` entry): `"paused": "nav"`:
  paused and no scrub awaiting its commit -> focus moves (`left`/`right`,
  1 / 2 / 3 per click), no select. Checked on the TV: no clicks in the
  grid, slow clicks move one tile, a scrub commits and plays on. 0.5.0-28.
- First scrub after a while: slow clicks did nothing at first in any app
  (YouTube, Netflix, ...), later turns acted at once. Tried and dropped:
  40 and then 100 extra units on the first drag (0.5.0-28), a quick swipe
  before it (0.5.0-29): none opened the scrubber sooner. Now (0.5.0-30,
  every touch-scrub app): a 150-unit swipe in the turn's direction, then
  0.8 s before the drag (`touch_flick`, `touch_flick_wait`), so the lost
  first input is the swipe. Checked on the TV: Netflix now scrubs on slow
  first clicks; YouTube does not (open item). Tests in
  `test_knob_scrub_mode.py`.
- Meanwhile: the Pi dropped off the network for ~10 min (~07:41); HA's
  Sonos entity stopped updating then (subscriptions lost), so the pause
  mute never undid itself (its "only undo my own mute" check read a stale
  `is_volume_muted: false`). Reloading the Sonos integration fixed it. And
  at 08:13 HA core exited with 137 during an app rebuild (not an OOM kill,
  per Docker) and came back by itself at 08:18.

### 2026-11-13 — TV toasts: rating, soccer picks, ad muting, sign-in code

- Asked: can the Pi put text on the Apple TV? Not on tvOS (no outside
  overlay or notification; AirPlaying a picture would take over the
  screen, and the Companion keyboard only types into an open text field).
  The LG can: webOS `system.notifications/createToast` over any input,
  already used to announce probe steps (2026-10-27).
- `toast.py` + config `toast` (`lg`, `events`, `settle_s`) + key action
  `{"toast": "text"}` (label placeholders filled). Wired: rating results,
  each soccer pick (merged: only the last of a run of presses, 0.6 s after
  they stop; webOS would queue one toast per press), ad mute / unmute (only
  when ad muting changes the sound itself), and the Google sign-in code
  (repeated every minute while waiting, then done / failed / expired).
  Skipped while the LG is off.
- Untested on the TV: a toast over the Apple TV HDMI input (expected to
  work, as with any webOS toast) and over the PS5 in Game mode (ads only
  mute on the Apple TV input anyway). Merged after the 8 s keys and the
  knob fix, as 0.5.0-31.

### 2026-11-13 — Key screens are 64×64; why the icons are 112×112

- From the vendored SDK (upstream streamdock-n3 at the pinned revision):
  `StreamDockN3.key_image_format()` is 64×64 JPEG rotated −90°, and
  `set_key_image` saves it with Pillow defaults (q75, 4:2:0). The
  "112 * 112" comment above `set_key_image` is copied from the N4 driver
  (N4 keys are 112×112); upstream's own text tiles are 64×64. Our icons
  followed the comment (2026-10-23) and `build_icons.py` kept 112.
- Not changed: the downscale is harmless. If keys look soft on the dock,
  patch the JPEG save first (`quality=95, subsampling=0`), then consider
  drawing at 64 natively. DOCS.md "Key images" has the details and palette.
- Google Cloud console values for the like/dislike OAuth app (branding,
  audience, no scopes, TV client) are in DOCS.md "Like / dislike".
- Tubi showed no toast: its breaks are muted by the HA automation
  `streaming_pause_mute` (any app paused), not by ad detection (Pluto,
  Peacock). The automation now toasts `Paused: sound muted` and `Sound on`
  itself (`webostv.command`, `continue_on_error`). Deploy with
  `restore.sh --replace-ha-config`, or the Pi keeps its older copy.

### 2026-11-13 — TV toasts deployed on arpi (0.5.0-31)

- Merged as #35 and deployed with `restore.sh --replace-ha-config arpi`:
  0.5.0-31 running, dock connected, pairings kept (Apple TV touch and
  AirPlay, YouTube atv, like / dislike signed in). Only
  `streaming_pause_mute` differed from the repo and was replaced.
- Test toast: `webostv.command` `system.notifications/createToast` sent
  from inside the app container (its token can call HA services; the
  `hassio_cli` container's gets 401) returned 200 and showed on the TV.
- Then: mute toasts were too noisy. Removed from `streaming_pause_mute`
  (`Paused: sound muted` / `Sound on`), and `ad` dropped from the preset's
  toast `events` (`Ad: sound muted` / `Ad over: sound on`; the option stays).
  Rating, soccer and sign-in toasts are kept. 0.5.0-32.

### 2026-11-13 — Pluggable services; live soccer is the first one (0.6.0-1)

- New features now go in `services/<name>/` (a package) plus preset lines,
  without touching `ha_daemon.py`. The host runs each service's jobs (3
  workers), gives every press its own thread, and catches failures: a
  service that fails at startup is disabled with one log line; render-path
  calls (keys, conditions, vars, context) that run slow or raise 3 times in
  60 s are switched off for 5 min (its keys show `?`). How to write one:
  `homeassistant/streamdock_n3/services/README.md`.
- The preset places service keys (`{"service_key": "soccer.live"}`) and
  pages; actions are `{"service": "soccer", "do": "start"}`; conditions are
  namespaced (`soccer.live`, `soccer.picking`). The top-level `"soccer"`
  config moved to `services.soccer`; a leftover one logs a line.
- Live soccer moved from `soccer.py` to `services/soccer/` unchanged in
  behaviour (same key looks, picks, toasts, `live_game` page).
- Metrics: every operation is timed (count, wall p50/p95/max, CPU, HTTP
  bytes). A `metrics: SLOW …` line when one goes over budget, a ranked
  summary every 10 min in the log and in `/config/metrics.json`
  (`/app_configs/local_streamdock_n3/metrics.json` on the host);
  `bash homeassistant/tools/metrics.sh` prints the table on the PC. The
  summary appears even with no services (host ops are measured).
- Checked on arpi: soccer tokens refreshed and the list polled; apps page
  1B key 6 shows the soccer ball; a press with no live games toasts "No live
  soccer games". First 40 min with Tubi playing: app CPU 1.7-2.6 %, RSS
  ~90 MB, 19 threads; the dock redraws about once a second during playback
  (`host render_pass`, ~4 s CPU in 40 min); soccer's own cost is small (a
  refresh ~0.7 s wall, 0.1-0.4 s CPU). Only the first refresh (with the
  token page) went over its 2 s budget. `services/**/tests` stay out of the
  image (`.dockerignore`).

### 2026-11-13 — Scene jump service (0.6.1-1)

Status: **not deployed yet; not checked on the TV.** Built and tested on the
PC only (fixtures, no live requests in tests).

- What it is: a `services/scenes/` service and one key (`context:streaming`
  slot 4, `scenes.next`). In Max or Disney+ it jumps the film to a few seconds
  before the next scene marked as nudity or sex in public lists. Gray key =
  nothing to jump to, colour = a press moves the picture. Design:
  `docs/superpowers/specs/2026-11-13-scene-jump-design.md`; sources:
  `docs/SCENE_SOURCES.md`.
- How it works: a job watches the Apple TV state; a film (>= 50 min or typed
  movie) is noticed after 10 s; TMDB identifies it (`tmdb_key` in
  `/config/secrets.json`; retried until it answers); VideoSkip and NSFWhen
  are asked once per film with a 3 s gap and a daily cap of 30 films, hits
  and misses cached in `scenes.sqlite` (nudidex is registered but off by
  default; VideoSkip is skipped for a film TMDB has not identified); the
  lists are merged (scenes within 20 s join); the key is lit only while a
  scene's lead-in point is ahead of the position.
- Press: `media_seek` to `start - lead_s` (5 s). Right after a jump the
  position counts as the scene's start, so a second press goes to the next
  scene instead of repeating.
- Decisions made during the build (in the design, section 6): a scene is
  "ahead" only if `start - max(guard_s, lead_s)` is later than the position;
  VideoSkip offsets only when the file lists the service (no implicit
  Disney+ +20 s).
- Final review fixes: TMDB identification is cached as a `tmdb` lookups row
  and retried (gaining it re-asks sources that missed); each source logs its
  own request count; a film that drops out for a tick (loading after a seek)
  keeps its snapshot and second-press memory for `settle_s`; "nan"/"inf"
  numbers are ignored; nudidex left out of the default sources; a chain test
  runs the real sources, Fetcher, Db and service on fixtures. The first
  lookup of a new film runs inside the 5 s `watch` job (20-30 s), so one
  `metrics: SLOW scenes job:watch` line per new film is expected.
- Icons: the owner's silhouette (`services/scenes/icons/source.gif`) built
  by `homeassistant/tools/icons/build_scene_icons.py` into `scene.png`
  (white on red) and `scene_off.png` (gray). Only checked by eye as images.
- No real offsets for Max or Disney+ yet (see open items); no toasts.

### 2026-11-13 — Scene jump on the TV: works in Prime (0.6.1-3)

- Deployed from `feat/scene-jump`. `tmdb_key` is in `/config/secrets.json`
  on arpi (`{"scenes": {"tmdb_key": ...}}`, mode 600). The PC's copy is
  `services/scenes/sources/action-ring-tmdb.local.json`: `*.local.json` is
  git-ignored, kept out of the image and removed from `restore.sh`'s staging
  copy, so it never leaves the PC. Putting the key on a new device stays a
  manual step.
- Checked with Novocaine (2025) in Prime Video (VideoSkip file timed on
  Paramount+, scenes 0:19:06-0:23:26 and 1:18:37): lookup 4 requests, 10 s;
  the key turned to colour; first press `jump 231s -> 1142s (scene at
  1147s)` and the Apple TV reported 1142 a second later; second press, at
  the end of the first scene, `jump 1407s -> 4713s`, reported 4713; the key
  then went gray. So `media_seek` is exact in Prime and the 5 s lead is as
  designed. No ad played on the jumps.
- Two fixes found by the test:
  - Prime reports titles as "Novocaine (2025)". TMDB's search returns
    nothing for that, so the film was never identified and VideoSkip was
    skipped. The trailing year is now split off (`model.split_title`): the
    bare title is used for TMDB and the sources, and the year identifies the
    film even when TMDB has no match.
  - A VideoSkip file labelled its scene "Nude ..." and parsed to nothing
    (After the Hunt, 2025). The category word is now matched as people write
    it (Nude / Nudity / Sex / Sexual).
- `seek_apps` now also lists Prime (checked above), Paramount+, Tubi, Pluto
  and Netflix, added at the owner's request before a check. Netflix cannot
  work: the now-playing log shows it reports no title, duration or position.
- Paramount+ works (0.6.1-4): Titanic, 3 scenes from VideoSkip (6 requests,
  17 s); presses landed at 1:21:59, 1:30:29 and 1:31:07, each 5 s before
  its scene and confirmed by the reported position; the key then went gray.
- Pluto (Fight Club) is not usable as it stands:
  - both presses were sent while an ad break played and Pluto ignored them
    (seeking is locked during ads, as in Tubi); a press outside a break was
    not tried;
  - the reported film length changes around breaks (172, 139 and 142 min
    for the same film), so the film is identified only while the true
    length is reported, and each change makes it "another film" (served
    from the cache, but the jump memory is lost);
  - during a break the reported position is the ad's own clock (the service
    read the film as being at 0:58).
  The app's ad detector already knows when a Pluto break runs
  (`ad-detect: ad started / ended`); the scenes service does not see that.
- A temporary TV toast after each lookup (`toast_lookup`, on in the preset;
  `scenes` added to `toast.events`): `Scenes: 3 found for <title>` or
  `Scenes: none found for <title>`. Remove when no longer needed.
- Which scenes count (0.6.1-5): two of Titanic's three jumps were not nude
  scenes; its file marks them `sex 2 (non-explicit)`. VideoSkip's level
  (1-3) is now kept, and the key uses every nudity block plus sex at level
  3 (`min_level`, default `{"sex": 3}`). The cache was upgraded in place
  (level-less VideoSkip rows dropped and fetched again). After the
  redeploy: `videoskip: hit, 3 scenes` -> `"Titanic": 1 scenes`, toast
  `Scenes: 1 found for Titanic`.
- A mangled `\b` (a backspace character) in the fixture privacy test made
  its e-mail check match nothing since Task 2; repaired, and the check
  passes for real.
- Max works (0.6.1-6): HBO Max sends an empty `media_title` and the film's
  name as `media_artist`, so no film was noticed; the service now falls back
  to the artist when the title is empty. Weapons: 1 scene (`Sex 3`,
  0:54:46, file timed on Prime), one press landed near 0:54:41, key gray.
- Tubi works (0.6.1-6): Fargo, 2 scenes (both `sex 3`); presses landed at
  0:11:18 and 1:05:20, confirmed by the reported position. The second jump
  crossed 54 min of film: Tubi landed on the target first, played about 14 s
  (into the scene), then started an ad break (HA `paused` at 3933 s). So on
  Tubi a long jump can be followed by a break a few seconds after landing;
  after the break the film resumed where it had stopped (owner watching).
- Tuner in Paramount+: 1 scene (`Nudity 3`, 0:49:03, file timed on Prime);
  the press went to 0:48:58 and the position was reported as 0:48:59.
- The Apple TV app works (0.6.1-7, `com.apple.TVWatchList` added to
  `seek_apps`): Pulp Fiction, 5 blocks in the file, 3 used (two `nudity 1`,
  one `sex 3`); first press `jump 31s -> 4859s`, reported 4859.
- Films with known times for further checks (VideoSkip; service per TMDB's
  US listings on 11-13): Fight Club on Pluto (0:48:38, 0:55:42, 0:58:43),
  Titanic on Paramount+ (1:22:04, 1:30:34, 1:31:12), Weapons on Max
  (0:54:46), After the Hunt on Prime (1:25:58), Tuner on Paramount+
  (0:49:03). Nothing found on Tubi yet.

### 2026-11-14 — LG Channels live soccer on the LIVE key (0.6.2-1)

- The LG has no antenna channels (`tv/getChannelList`: count 0, "tuner
  channel map does not exist"). Its live channels are **LG Channels**
  (`com.webos.app.lgchannels`, HA source `LG Channels`).
- Guide: `GET https://api.lgchannels.com/api/v1.0/schedulelist` with
  headers `x-device-country: US`, `x-device-language: en`; no key. The body
  is base64 of zlib JSON: `categories[].channels[]` (`channelId`,
  `channelNumber`, `channelName`) with `programs[]` (`programTitle`,
  `description`, `startDateTime`/`endDateTime` UTC), about 6 h ahead.
  Soccer channels: 109 German Soccer Network (live games titled
  "... Live: A - B"; otherwise "Full Match" replays) and 807 ITV Deportes
  (Liga Expansión MX; titles don't say live vs. replay). 447 SportsGrid
  only has talk shows.
- Tuning (probes `lg_channels_*`, `lg_tune*`, all accepted by the TV):
  - Deep link `system.launcher/launch` with `params.contentTarget`: the app
    loads `/homeapp/v2/?channelId=<contentTarget>`. The guide's `channelId`
    opened the home page on some other channel; JSON in it gives Tomcat
    "HTTP 400 Invalid character". The ID the app wants is unknown.
  - `tv/getCurrentChannel` / `tv/getChannelProgramInfo` fail (500) even in
    LG Channels: no read-back of the channel.
  - **Number keys work, from a fresh launch**: `system.launcher/close`
    LG Channels, 3 s, `launch`, 12 s (the app's home page), then the
    digits and `ENTER` (`webostv.button`). Over full-screen playback the
    first digit only wakes the player (109 -> "09", 807 -> "07"), and on
    the home page a doubled first digit counts (-> "1109"), so the app is
    always closed first. Closing matters with another input in front too:
    the app keeps running behind HDMI and a launch resumes its playback.
    Without `ENTER` the number shows but nothing tunes.
  - **Screen capture checks it**: `tv/executeOneShot` returns an
    `imageUri` (`https://<tv>:3001/resources/.../capture.jpg`, 960x540,
    self-signed TLS) of what is on screen, video and overlays included
    (the number box shows "807 · IP-807 ITV Deportes"). It also worked on
    the Apple TV's HDMI input (the Apple TV screensaver came through);
    DRM/HDCP video there is untested. The code's exact `_tune` calls,
    replayed as probes, tuned 807 -> 109 with LG Channels in front and
    Apple OTT -> 807, both confirmed by captures.
- qoool.xyz (asked first) was dropped: its stream links are unlicensed
  restreams.

### 2026-11-14 — Paramount+ live games: links only work from a cold start (0.6.2-2)

- The LIVE key's Paramount+ path (never checked end to end before) failed:
  Exeter v Cheltenham (live, 90+3) -> Paramount+ "fatal error", black.
  Opened by hand inside Paramount+ it played (after a "watch live / catch up
  with highlights" choice).
- Tests with Chelsea v Man City (live, Paramount+), `media_player.play_media`
  url on the Apple TV, the user watching (the Apple TV's now-playing reports
  were frozen at "idle, no app" from the end of the Exeter stream until a
  game played again, so HA state could not be used; LG captures of the
  Apple OTT input are black while protected video is up):
  - `punchoutUrls.play` with Paramount+ running: fatal error / black.
  - The TV app page (`item.url?playableId=tvs.sbd.10120:...`) + `select`:
    the TV app hands off to Paramount+, then black.
  - `punchoutUrls.open` (`#open`) + `select`: black.
  - **After a force-quit of Paramount+**: `play` link -> profile picker ->
    `select` -> "Live / Start from beginning" -> `select` -> plays live
    (now-playing `playing`, `Chelsea vs. Man. City`).
- Quitting works from the app's own pyatv connection: Companion HID Home
  twice (app switcher, front app centred), touch swipe 500,700 -> 500,50 in
  250 ms, Home. Paramount+ went from playing to "idle, no app", and the
  link then showed the profile picker (user watched).
- 0.6.2-2: for Paramount+ games the key brings Paramount+ to the front
  (`play_media` app), quits it (`services/soccer/atv_quit.py`), opens the
  link and presses `select` up to twice, 5 s apart, until Paramount+
  reports playing.
- The MRP client messages (PR #40's `atv_clients`) for Paramount+:
  pid 8423 alive at the key press (removed 09:23:14), 8458 added 09:24:58
  (Exeter by hand) and alive through every failed link test, removed
  09:40:58 (the user's force-quit); the cold link + picker + "Live" (09:41
  to 09:43) sent nothing until playback (8473 added 09:43:08); the swipe
  quit logged its removal (09:44:43). So Paramount+ registers at playback:
  "gone" can't be trusted, but a quit of a running Paramount+ is confirmed.
- 0.6.3-1: PR #40 merged into this branch (both had used 0.6.2-1; this
  branch's 0.6.2-1 / 0.6.2-2 deploys had replaced #40's dry-run build on
  arpi). Soccer's cold start now reads `atv_clients` (confirms the quit)
  and stops its presses on `dock_input` like `services/profiles`, without
  its `picker` / `dry_run` switches or its "already playing" guard
  (Paramount+ reports "paused" with a title on the screens that need the
  press).
- **Checked with the real key on 0.6.3-1** (15:02 UTC, 4 live men's EFL
  games): `picked 1/4: Stockport County vs. Reading FC`, `quit
  com.cbsvideo.app for a cold start (was gone)`, two `sending select`,
  then `atv-clients: com.cbsvideo.app alive pid 8489` and now-playing
  `playing` "Stockport County vs. Reading" (about 20 s from the press).

### 2026-11-14 — Profile picker: OK after a cold launch (0.6.2-1)

Status: **deployed 11-14 04:00 in dry run; not checked on the TV.** It only
logs what it would do (`dry_run: true`, every app `picker: false`). At start
the tracker listed the running apps at once (`atv-clients:
com.amazon.aiv.AIVApp alive pid 8286`, `com.hulu.plus alive pid 8155`).

- What it is: a `services/profiles/` service. The Apple TV app keys now use
  `{"service": "profiles", "do": "open", "args": {"app": ...}}` instead of
  `launch` (the LG Media Player key keeps `launch`). `open` launches the app
  as before and presses select once on a cold start of an app listed with
  `picker: true`; a click that starts a film is undone with one Menu.
  Design: `docs/superpowers/specs/2026-11-14-profile-auto-select-design.md`;
  how to configure it: `DOCS.md`, "Profile picker".
- What made it possible (read from `mrp_raw.log` of 11-13, nothing sent to
  the TV): the Apple TV's media-remote session reports app **processes**:
  `update_client` with a process id when an app registers, `remove_client`
  when its process ends, and the full list of live apps on every reconnect.
  New core module `atv_clients.py` keeps that list
  (`ctx.shared("atv_clients")`: `alive` / `gone` / `unknown`).
- Facts from the same logs:
  - Background apps were short-lived that evening: Tubi was removed 3 min
    after Prime opened; Pluto, Paramount+ and Max within 7 s as Netflix
    opened; Netflix 31 min after its start. The cause is not in the logs.
  - Prime and the Apple TV app register right after launch with nothing
    playing; Paramount+ 5 s before playback; Netflix in the second of its
    first `playing`; Hulu only at playback. So "not listed" means "not
    running" only for the first kind (`"registers": "launch"`).
  - Nothing is reported at a launch until something plays; the logs cannot
    say what a launch showed. Each open now logs its decision
    (`profiles: open <app>: ...`) and the last 100 are kept in
    `/data/services/profiles/history.json`.
  - Netflix reports no duration, so the undo cannot work there
    (`"undo": false`).
- Safety rules built in: no click when the state is unknown (no AirPlay
  pairing, connection down, or the key also woke the TV or the Apple TV was
  asleep and no fresh
  connection came within 5 s), when the app is playing or paused, or after
  any dock key or knob was used (new `ctx.shared("dock_input")`). Menu is
  sent at most once, only after the dock's own click, only for playback of
  300 s or more that was not there before it.
- The logo key reads app icons from the new key actions as well (`logo.py`).
- `restore.sh` needs no change: it uploads the preset whole and the image
  copies the `services/` tree; the `Dockerfile` lists the two new core
  modules (`atv_clients.py`, `dock_input.py`).
- Tests: 619 passing.

### 2026-11-14 — Cat easter egg: Round 1 → 2 → 3 (0.6.4-2)

- Pressing Round 1, 2, 3 in a row (each within 0.6 s of the one before)
  shows a cat's face on every key for 10 s, then the page comes back.
  Pressing a cat plays a cat video in YouTube on the Apple TV. The cats
  blink and stick their tongues out (dynamic-key phases, staggered per key).
- First tried as a chord, all three pressed together (0.6.4-1, deployed on
  arpi). It never fired: each attempt reached the app as **one** button,
  pressed and released, and nothing at all for the other two (upstream log,
  e.g. `round button 1 pressed` 15:35:08.70, `released` 09.52, no other
  button in between). The N3 reports only the first button held. The HID
  report carries one key code (`decode_input_event(data[9], data[10])`), so
  the dock cannot do chords.
- Core: `combo.py`, hooked on the upstream `daemon.event_key()` (a held
  press maps to no action). A press that starts or continues the combo is
  held; the combo completing drops the held presses and runs the combo's
  action; the gap running out (or any other press) replays them in order.
  `run_actions` alone could not do this: it gets the mapped action, not the
  button (Round 3 maps to a plain shell string). Cost: Round 1 reacts 0.6 s
  late; Round 2 waits only right after Round 1, Round 3 never.
- Tabs: a page may set its own `open_s` (the cat page stays 10 s; the
  preset's `open` timeout is 5 s).
- `yt_lounge.py`: `connected(screen)` and `play_video(id, screen)`
  (pyytlounge `play_video`, the Lounge `setPlaylist` command). The existing
  `_pick()` path needs a playing video, which a freshly opened app has not.
- Service `cats`: `show` opens the page, `play` sends a random video (not
  the last one) over the Lounge session to `atv`, else starts YouTube on the
  Apple TV and waits up to 20 s for the session, else hands the Apple TV the
  `youtube.com/watch` URL. Videos checked public with YouTube oEmbed on
  2026-11-14: Keyboard Cat, Surprised Kitty, The two talking cats, two cat
  compilations.
- Images: six original cat faces drawn with Pillow
  (`tools/icons/build_cat_icons.py`), 112×112, three frames each, CC0.
- Stacked on PR #41 (0.6.3-1).

### 2026-11-14 — Cats on the dock: the combo works; YouTube's profile picker (0.6.4-3)

- 0.6.4-2 on arpi: Round 1 → 2 → 3 shows the cats (`combo: 7-8-9`, the
  three presses 0.39 s and 0.38 s apart). Pressing a cat started YouTube on
  the Apple TV, which stopped on "Who's watching". The Lounge session never
  connected (20 s), and the URL fallback failed: `play_media` with
  `media_content_type: url` and a `youtube.com/watch` URL answered HTTP 500
  (HA traceback: pyatv companion `launch_app` → `exchange_opack`).
- Fix (0.6.4-3): the profiles service's technique, one select on the
  picker. YouTube is not in `profiles.apps`, so `atv_clients` does not track
  it; the cats service uses its own signal instead: no Lounge session
  `picker_after_s` (6 s) after the launch means the picker. Select is sent
  once under the profiles service's guards (YouTube in front and not
  playing, LG on the Apple TV input, no dock input since the cat press),
  then the video goes over the session once it connects. The URL fallback
  is gone.

### 2026-11-14 — Cats: the Lounge never tried to connect (0.6.4-4)

- 0.6.4-3 on arpi: YouTube cold-started without a profile picker this time,
  and still no video: `cats: no profile click (YouTube not in front)`, then
  `no YouTube link session, not sent`.
- Cause: HA's `media_player.vugitv_4k_vugitv_4k` reports the Apple TV's
  **now-playing** app (`np:` lines showed Paramount+ paused while YouTube
  was on screen). `yt_lounge.py` opens a session only while that matches
  YouTube, so it never tried. The guard in 0.6.4-3 read the same field. The
  0.6.4-3 entry's reading ("the session connects only past the picker") was
  not shown: the session never attempted at all.
- Fix: `YouTubeScreens.want(screen, seconds)` makes a screen count as active
  for a while (retry every 2 s instead of 10). The cats service calls it
  before launching YouTube. The select guard drops "YouTube in front" (not
  knowable from HA) and keeps: LG on the Apple TV input, no app playing, no
  dock input since the press.
- Open: whether the Lounge connects while YouTube shows its picker. If it
  does, the 6 s select never fires and the picker may swallow the video.

## Open items

- [ ] Profile picker: survey on the TV (0.6.2-1 runs in dry run), one
      step at a time: per app (Netflix, Prime, Max, Disney+, Paramount+,
      Hulu) a cold and a warm launch from the dock: is there a picker, how
      many seconds until it accepts OK, does the log say `cold` / `warm`,
      does the app register at launch or at playback; what Menu does in each
      app's player (only in a film started for the check). Then per app
      `picker: true` and `dry_run: false`, and a cold and a warm launch each.
- [ ] Profile picker: the first launch after a night off: does a fresh
      connection come up within `sync_wait_s` (5 s), and was the app still
      running? This decides how often the feature acts in the evening.
- [ ] Profile picker, from normal use: an app left for hours and opened
      again (does it ask for the profile although it was running?); an app
      opened with the Siri Remote first.
- [ ] Profile picker: YouTube's account picker is not covered (its key goes
      through the service but it is not in `apps`); Peacock has no app key.
- [ ] Scenes, left from the final review (small, not fixed): a film that an
      app reports as content type `movie` and whose duration drops out for a
      tick is treated as another film (key gray ~15 s, served from cache
      again); identity folded into a same-title row when the stored film has
      no runtime (fix: update by id); a film still settling that blinks out
      can be looked up slightly early.
- [ ] Scenes sources to add: Reddit timestamp threads through the official
      API (needs a Reddit "script" app: client id and secret in
      `secrets.json`); Gemini Pro with search as a low-confidence last
      resort. Trial results and analysis: `docs/SCENE_SOURCES.md`.

- [ ] Scene jump, TV checks left: Disney+ (no film with known nudity times
      found on it yet); Pluto outside an ad break; whether a longer lead on
      Tubi lets the ad break start before the scene does; whether the position reported after a seek ever
      lags more than `seek_hold_s` (5 s) (it arrived within 1-3 s in Prime,
      Paramount+, Max and Tubi).
- [ ] Scene jump: keep the key gray while an ad break runs (Pluto, Tubi):
      give services the ad detector's state, or take Pluto out of
      `seek_apps`. Remove the temporary `toast_lookup` toast when done.
- [ ] Scene jump: Netflix reports nothing to the Apple TV; it would need a
      different design (film chosen by hand, timing from a known point).
- [ ] Scene jump: one `SLOW scenes cond:ready 7ms` line seen (budget 5 ms);
      three in a minute would switch the service's keys off for 5 min. Watch.
- [ ] Scene jump: no Max or Disney+ VideoSkip file seen yet, so those
      offsets (`app_services`) are unverified.
- [ ] Scene jump: a Gemini-with-screenshot source needs a trial before it is
      built.
- [ ] Scene jump: recheck nudidex (coverage, blocking) before relying on it.

- [ ] Services: watch for `render calls off` / `SLOW soccer cond:` lines
      over a few evenings (the breaker counts wall time, so lock waits
      could trip it); if they show, strike on CPU instead.
- [ ] `metrics.sh`: p95 is a histogram bucket's upper bound and can read
      above max (seen 2.62 vs 2.28 s); clamp quantiles to the max.
- [ ] Before the next service (bets, scene jumps): a canned
      `call_response` in `fake_context`; stop a disabled service's
      long-running job; guard a malformed `metrics` section at startup.

- [ ] YouTube (Apple TV): a slow first scrub still opens no scrubber; fast
      turns open it after ~1 s. The log ties every opening to the finger
      reaching the touch surface's edge and lifting (a 150-unit warm-up
      swipe lifting at x=725 did nothing). Next try: a YouTube-only
      `"touch_flick": 275` (to the edge) in its `scrub_apps` entry. Netflix
      is fixed by the 150-unit swipe + 0.8 s wait (checked on the TV).

- [ ] Google OAuth client `action-ring-tv` is used by `yt_rating.py`
      (scope `youtube`, sensitive). Unverified, so sign-in shows the
      "unverified app" screen; fine for this one household. If the scopes
      change, check the privacy policy on the `gh-pages` branch still
      describes what is done with the data.

- [ ] Tune knob acceleration on the TV (`atv` section: step sizes, speed
      thresholds) and note which apps support seek vs. fall back to skips.
- [ ] Preview scrubbing: tune `touch_steps` / `touch_release_after` with use;
      note which apps' players respond to touch scrubbing.
- [ ] `arpi` is live (11-01): integrations, Apple TV touch + AirPlay,
      YouTube (Apple TV) all paired; old Pi powered off. Left: `pair.sh
      youtube lg` if the LG's YouTube app is used; if the old Pi comes back,
      stop its app (boot auto) and its `streaming_pause_mute`.
- [ ] LG Media Player keys: subtitles on/off/next via the `CC` panel,
      audio track via playback bar `⋯` -> Options -> Audio Track, knob
      scrubbing (find what seeks). See the 10-27 entry.
- [ ] YouTube paused, then the TV app with nothing playing: the YouTube
      page stays (no signal). Check the `youtube: event` lines
      (`debug_events`, enabled on the Pi only) for one.
- [ ] Link the LG's YouTube app (`pair.sh youtube lg`) and confirm next
      chapter there (Apple TV done).
- [ ] Decide whether to track the HA-created script `netflix_on_apple_tv`
      in the repo.
- [ ] Live soccer from LG Channels (11-14 entry): implemented
      (`services/soccer/lgchannels.py`, `lg_channels` 109/807, men's only),
      tuning checked on the TV by screen captures; left: power-on from off
      (does the Apple TV's CEC wake pull the input back to Apple OTT?) and
      a press of the real key during a live game. First live
      game: 11-14 17:00-19:30 UTC on 109. 807's titles carry no live marker,
      so it may never list a game.
- [ ] Govee not integrated in HA; lighting controls pending.
- [ ] Only one Apple TV in HA; the handoff mentions two generations.
- [ ] Profile picker, left from the branch review (not fixed): if the
      service fails to load, the Apple TV app keys do nothing (no fall-back
      to plain `launch`); the logo's icon lookup accepts any service action
      named `open`; the undo window (`watch_s`, 5 s) may be shortened once
      the survey shows how fast each app reports a film's duration.
