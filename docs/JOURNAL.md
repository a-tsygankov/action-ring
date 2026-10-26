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
| Dock software | Local app `local_streamdock_n3` v0.4.0-21, upstream [asad-albadi/streamdock-n3](https://github.com/asad-albadi/streamdock-n3) @ `67f670a` |
| HA config added | `script.tv_room_mute_toggle`, `script.tv_room_power_toggle`, `automation.streaming_pause_mute` |
| Integrations used | Sonos, LG webOS, Apple TV (all paired in HA UI) |
| MCP for Claude | HA **Model Context Protocol Server** integration (all LLM APIs, admin only) at `http://homeassistant.local/api/mcp` (HA is on port 80); registered in Claude Code at user scope as `home-assistant` |

### Repo layout

| Path | Purpose |
|---|---|
| `homeassistant/streamdock_n3/` | The HA app (Dockerfile, `config.yaml`, `run.sh` watchdog, `ha_daemon.py` live key state + HA calls, `knob_accel.py` Apple TV knob engine, `atv_touch.py` Companion touch connection, `yt_lounge.py` + `yt_chapters.py` YouTube link and chapters, `np_log.py` now-playing log, `ha-call`, `patch_ids.py`, `DOCS.md`) |
| `homeassistant/streamdock_n3/config.tv_room.json` | Live dock mappings (source of truth) |
| `homeassistant/streamdock_n3/icons/` | Key images, deployed to `/app_configs/local_streamdock_n3/icons/` (`/config/icons/` in the app) |
| `homeassistant/ha-config/scripts/*.yaml` | HA scripts, one top-level key per file |
| `homeassistant/ha-config/automations/*.yaml` | HA automations, one `- id:` entry per file |
| `homeassistant/restore.sh` | Restore everything above onto a HAOS device |
| `homeassistant/pair.sh` | Pair the Apple TV (`atv` touch, `atv airplay` media protocol) and link YouTube apps; `status` |
| `homeassistant/tools/` | Ad research: `mrp_summary.py`, `replay_ad_detect.py`, `sanitize_mrp.py`, sanitized `fixtures/`; live tests: `probe.sh` + `probes/*.json` |
| `docs/AD_DETECTION_GUIDE.md` | Guide for AI agents: analyze a new app and add its ad rule |

### Control mappings (TV Room)

| Control | Event | Action |
|---|---|---|
| Large knob | `knob.3.left/right/press` | Apple TV, speed-adaptive. While playing/paused: tvOS preview scrubbing (touch drag, finger travel 12 / 60 per 2 / 150 per 3 units per click); seek fallback 10 s per click / 30 s per 2 / 90 s per 3 when touch is unavailable. 3 s without knob activity after a scrub commits it and resumes playback. Otherwise focus left/right 1 / 2 / 3 steps per click · press = OK (`select`; commits a scrub and cancels the auto-resume) |
| Small knob 1 | `knob.1.left/right/press` | Sonos Arc volume down/up · mute toggle (script) |
| Small knob 2 | `knob.2.left/right/press` | Apple TV focus up/down, 1 / 2 / 3 steps per click · press = OK (`select`) |
| Round 1 / 2 / 3 | `button.7/8/9.press` | Apple TV Menu (back) / Home / Power on (wakes LG via CEC) |
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
4. Deploys the mappings and icons, backing up any existing `config.json`.
5. Restarts the app and confirms the controller is running.
6. Pairs the Apple TV via `pair.sh atv` if needed (prompts for the PIN),
   then prints `pair.sh status`.

Link YouTube apps afterwards with `bash homeassistant/pair.sh youtube <screen>`.
Verified against the live Pi.

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

## Open items

- [ ] Tune knob acceleration on the TV (`atv` section: step sizes, speed
      thresholds) and note which apps support seek vs. fall back to skips.
- [ ] Preview scrubbing: tune `touch_steps` / `touch_release_after` with use;
      note which apps' players respond to touch scrubbing.
- [ ] Link the LG's YouTube app (`pair.sh youtube lg`) and confirm next
      chapter there (Apple TV done).
- [ ] Decide whether to track the HA-created script `netflix_on_apple_tv`
      in the repo.
- [ ] Govee not integrated in HA; lighting controls pending.
- [ ] Only one Apple TV in HA; the handoff mentions two generations.
