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

### Apple TV knobs

Map a knob's turns to the built-in engine instead of a shell command:

```json
"knob.3.right": { "atv_knob": { "axis": "horizontal", "direction": 1 } },
"knob.3.left":  { "atv_knob": { "axis": "horizontal", "direction": -1 } },
"knob.2.right": { "atv_knob": { "axis": "vertical", "direction": 1 } },
"knob.2.left":  { "atv_knob": { "axis": "vertical", "direction": -1 } }
```

The horizontal axis seeks while the Apple TV is playing or paused, and moves
focus left/right otherwise. The vertical axis always moves focus up/down.
Steps grow with turning speed, and clicks are batched so fast spins never
queue up. Entities and tuning go in a top-level `atv` section (defaults
shown):

```json
"atv": {
  "remote": "remote.vugitv_4k_vugitv_4k",
  "media_player": "media_player.vugitv_4k_vugitv_4k",
  "seek_steps": [10, [30, 2], [90, 3]],
  "nav_repeats": [1, 2, 3],
  "medium_interval": 0.25,
  "fast_interval": 0.08,
  "flush_quiet": 0.06,
  "flush_max": 0.15,
  "nav_delay": 0.05,
  "nav_max": 20,
  "skip_max": 6,
  "seek_hold": 3.0,
  "scrub": "seek",
  "touch_steps": [12, [60, 2], [150, 3]],
  "touch_y": 500,
  "touch_min_x": 150,
  "touch_max_x": 850,
  "touch_move_px": 25,
  "touch_interval": 0.016,
  "touch_release_after": 0.6,
  "touch_keepalive": 0.05,
  "touch_pause_delay": 0.4,
  "resume_after": 3.0,
  "resume_check_delay": 1.5
}
```

**Auto-resume:** after a scrub, `resume_after` seconds without knob activity
resume playback. A touch scrub is first committed with OK (select); then, if
the Apple TV still reports paused `resume_check_delay` seconds later, `play`
is sent. A seek scrub only sends `play` if paused. Map the knob's press to the
engine so it can cancel this: `"knob.3.press": {"atv_knob": {"press": true}}`.
Pressing commits the scrub with one OK and skips the auto-resume; outside a
scrub it's a plain OK. `0` disables auto-resume.

The TV Room config sets `"scrub": "touch"` (see Preview scrubbing below).

Steps are listed for the slow / medium / fast tiers, either per click or as
`[amount, clicks]`. `[30, 2]` means 30 s per 2 clicks, i.e. 15 s per click.
Each click is scored at its own tier, so speeding up or slowing down mid-turn
changes the rate: 1 slow + 4 medium + 6 fast + 3 slow clicks scrub
10 + 60 + 180 + 30 = 280 s. A click counts as
medium when it comes less than `medium_interval` seconds after the previous
one, and fast under `fast_interval`. The log shows each call as
`atv-knob: seek +40s -> 1260s` or `atv-knob: down x3`.

### Preview scrubbing (touch)

With `"scrub": "touch"` in the `atv` section, the horizontal knob scrubs
during playback the way a Siri Remote swipe does, with tvOS thumbnail
previews:

1. If the video is playing, it's paused (previews only show while paused).
2. A virtual finger presses the centre of the touch surface, then drags with
   the knob. `touch_steps` sets finger travel per click for the slow / medium
   / fast tiers, in touchpad units out of 1000 across the surface.
3. At an edge (`touch_min_x`/`touch_max_x`) the finger lifts and re-grips in
   the centre, like repeated swipes.
4. After `touch_release_after` seconds without a click the finger lifts. The
   preview stays on screen; press the knob (OK) to jump there, or Back to
   cancel.
5. While the finger rests between clicks it repeats its position every
   `touch_keepalive` seconds, as a real Siri Remote does.

Touch needs the app's own Apple TV pairing over the Companion protocol
(`atv_touch.py`, pyatv). This pairing is separate from Home Assistant's,
because HA's integration doesn't expose touch gestures. Until the app is
paired, or whenever the connection is down, scrubbing falls back to
`"seek"`.

Pairing: from the repo root on the PC run `bash homeassistant/pair.sh atv`.
It asks the Apple TV for a PIN, prompts for it and waits until the touch
connection is up. `restore.sh` runs this step itself when the app isn't paired
yet. To pair by hand from the SSH app:

```bash
echo '{"address": "<Apple TV IP>"}' > /app_configs/local_streamdock_n3/atv_pair_request.json
# the TV shows a PIN and the app log says "pairing: waiting for PIN"
echo 1234 > /app_configs/local_streamdock_n3/atv_pair_pin
```

The credentials are saved to `atv_companion.json` in the same folder. It is
a secret: keep it out of the repo and delete it to unpair. The log shows
`atv-touch: connected to …`, and while scrubbing, `atv-knob: touch drag …`
and `touch released`.

### Tabs

The six LCD keys show one page at a time. A `tabs` section defines the pages;
the top-level `keys` stay `{}` (without `tabs`, `keys` is the only page, as
before):

```json
"tabs": {
  "timeouts": { "open": 10, "after_press": 5, "context_debounce": 1 },
  "round": { "7": ["apps", "apps_more"], "8": ["sonos"] },
  "context": { "lg": "media_player.lg_tv_2", "atv": "media_player.vugitv_4k_vugitv_4k",
               "atv_source": "Apple OTT",
               "youtube_apps": { "atv": ["com.google.ios.youtube"], "lg": ["youtube.leanback.v4"] },
               "lg_media_apps": ["com.webos.app.mediadiscovery"],
               "lg_idle_app_prefixes": ["com.webos.app.hdmi", "com.webos.app.livetv", "com.webos.app.home"] },
  "launch": { "lg": "media_player.lg_tv_2", "atv": "media_player.vugitv_4k_vugitv_4k",
              "atv_remote": "remote.vugitv_4k_vugitv_4k", "atv_source": "Apple OTT",
              "power_on_wait": 25 },
  "pages": {
    "apps": { "close_on_press": true, "keys": {
      "1": { "icon": "netflix",
             "action": { "launch": { "device": "atv", "app": "com.netflix.Netflix" } } } } },
    "context:idle": { "keys": { … } }
  }
}
```

- **Context pages** `context:idle`, `context:streaming`, `context:youtube`,
  `context:lg_media`, `context:lg_app`, `context:live_game` follow what is in
  the foreground (`context.py`): the LG off or on its home/HDMI/live TV →
  idle; the Apple TV input → its app (YouTube → youtube, any other app →
  streaming, no app → idle); on the LG itself, YouTube, the media player or
  any other app. A new layout is drawn after it has held for
  `context_debounce` seconds.
- **Temporary pages** (here `apps`, `apps_more`, `sonos`) open from a round
  button. `round` maps a button to its cycle of pages: each press shows the
  next page in the cycle, and after the last one the cycle starts again. A
  page closes back to the context after `open` seconds, or `after_press`
  seconds after a key press on it; with `close_on_press` a key press closes
  it at once.
- **Buttons:** map `button.1–6.press` to `{"tab_key": N}` (`{"key_action": N}`
  is an alias) and the tab round buttons to `{"tab_round": N}`. Other events
  keep their own actions (`button.9`, the knobs).

Key specs inside pages use the normal key format (`label`, `color`, `icon`,
`state_entity` with `on`/`off`/`unavailable`, `modes`, `action`).
`"icon": "night__on"` is a bare icon id for `/config/icons/night__on.png`;
full paths still work. Labels may hold `{name}` placeholders that the app fills
at run time; an unknown name is shown as written.

Key actions, besides shell strings (`ha-call domain.service '<json>'`):

| Action | Does |
|---|---|
| `{"launch": {"device": "atv", "app": "<bundle id>"}}` | Powers the TV on if it is off (Apple TV wake, HDMI-CEC), switches the LG to `atv_source`, starts the app on the Apple TV |
| `{"launch": {"device": "lg", "app": "<webOS app id>"}}` | Powers on if needed, starts the app on the LG (`system.launcher/launch`) |
| `{"next_playing": [entities]}` | Next track on the first of those speakers that is playing and leads its group |
| `{"youtube": "play_pause" \| "next" \| "previous" \| "next_chapter" \| "subtitles"}` | Controls the linked YouTube app (see below); `subtitles` toggles captions |
| `{"soccer": "start" \| "next"}` | Plays the first live soccer game (powering on first), or the next one (see Live soccer) |

Named `when` conditions for modes: `youtube` (YouTube in the foreground on a
screen), `youtube_playing`, `youtube_subtitles` (its live play and caption
state), `soccer_live` (a live soccer game is listed).

### Key modes

A key on any page can change its look and action with live state. The first
entry in `modes` whose `when` holds wins; otherwise the key's own look and
`action` apply. The press needs no extra mapping: `button.N.press` →
`{"tab_key": N}` runs whatever the visible page's key N resolves to. On the
`context:youtube` page, Back goes to the LG's own YouTube app when that is in
the foreground, else to the Apple TV:

```json
"6": {
  "icon": "back",
  "modes": [{
    "when": [ { "entity": "media_player.lg_tv_2", "attr": "source", "in": ["YouTube"] },
              { "entity": "media_player.lg_tv_2", "attr": "app_id", "in": ["youtube.leanback.v4"] } ],
    "action": "ha-call webostv.button '{…\"button\":\"BACK\"}'"
  }],
  "action": "ha-call remote.send_command '{…\"command\":\"menu\"}'"
}
```

`when` is a named condition (`"youtube"`, `"youtube_playing"`,
`"youtube_subtitles"`, `"soccer_live"`, see above) or a list of conditions,
any of which may match:
`{"entity": …, "attr": "source" | "app_id" | … | "state", "in": [values]}`.
The LIVE key on `context:idle` lights up while a game is listed:

```json
"2": {
  "icon": "soccer_live__none", "label": "",
  "modes": [ { "when": "soccer_live", "icon": "soccer_live__live" } ],
  "action": { "soccer": "start" }
}
```

Modes also work on a key with `state_entity`: while a mode is active its look
wins, and when it ends the key returns to its live on/off look. A mode
without `action` leaves the key's press unchanged. The power key on
`context:idle` uses this to show the Arc's mute over the TV's on/off icon:

```json
"1": {
  "icon": "tv_power__off",
  "state_entity": "media_player.lg_tv_2",
  "on": { "icon": "tv_power__on" }, "off": { "icon": "tv_power__off" },
  "modes": [{
    "when": [{ "entity": "media_player.tv_room_tv_room", "attr": "is_volume_muted", "in": [true] }],
    "icon": "tv_power__muted"
  }],
  "action": "ha-call script.turn_on '{\"entity_id\":\"script.tv_room_power_toggle\"}'"
}
```

### YouTube: next chapter and ads

The top-level `youtube` section lists screens, i.e. the YouTube app on each
TV, and how HA shows YouTube in the foreground there:

```json
"youtube": { "screens": {
  "lg":  { "match": [ { "entity": "media_player.lg_tv_2", "attr": "source", "in": ["YouTube"] } ] },
  "atv": { "match": [ { "entity": "media_player.vugitv_4k_vugitv_4k", "attr": "app_id",
                        "in": ["com.google.ios.youtube"] } ] }
} }
```

While a screen's YouTube is in the foreground, the app holds a session with
it over YouTube's TV-link ("Lounge") protocol (`yt_lounge.py`, pyytlounge).
That session provides the playing video, its position, play and ad state, and
seek. `{"youtube": "next_chapter"}` reads the video's chapters from its public
watch page (`yt_chapters.py`: YouTube's chapter markers, else description
timestamps) and seeks to the next one. Ad starts and ends are logged
(`youtube: lg: ad started (15s, skippable)`).

Link each TV once: in its YouTube app open **Settings → Link with TV code**,
then on the PC run `bash homeassistant/pair.sh youtube <screen>` (`lg`,
`atv`) and type the code. Pass the code as a third argument to skip the
prompt. `bash homeassistant/pair.sh status` shows what is paired and linked.
By hand, write the code into `yt_pair_<screen>` in the app's config folder:

```bash
echo '123 456 789 012' > /app_configs/local_streamdock_n3/yt_pair_lg
```

The log shows `youtube: lg: linked to '…'`. Tokens are saved to
`yt_lounge.json`, which is secret and stays out of the repo.

### Live soccer

The top-level `soccer` section turns on a live-soccer key for Paramount+ and
MLS (Apple TV) games (`soccer.py`):

```json
"soccer": { "atv": "media_player.vugitv_4k_vugitv_4k", "atv_remote": "remote.vugitv_4k_vugitv_4k",
            "lg": "media_player.lg_tv_2", "atv_source": "Apple OTT", "power_on_wait": 25,
            "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
            "cache_s": 60, "poll_s": 180, "idle_poll_s": 1800, "select_after_s": 8 }
```

The list comes from the Apple TV app's own listing (the UTS API behind
tv.apple.com): two searches, `Paramount+` and `MLS`. They run every
`poll_s` seconds while a game is live or one starts within 15 minutes, and
every `idle_poll_s` seconds otherwise; an idle wait still ends 15 minutes
before the next listed start. The listing is undocumented, so the app keeps
its use light. A game counts when it is soccer between two teams (studio
shows such as MLS Countdown are left out), it is live, and its main provider
is Paramount+ or Apple TV. Live means the clock is running, or, when there
is no clock, the game is inside Apple's live window. A running clock stops
counting 30 minutes after that window ends. Games are ordered by kickoff,
then league. The
listing needs two public web-page tokens; the app reads them from
`token_page` once a day, or again when Apple rejects them. They are never
saved.

- `{"soccer": "start"}` fetches again if the list is older than `cache_s`,
  powers the TV on through the Apple TV if needed, switches the LG to
  `atv_source`, and opens the first game. The game opens from the same link
  the Apple TV app uses (`pplus://…` for Paramount+, a
  `tv.apple.com/…/sporting-event/…` link for MLS). If that app is in front
  but not playing after `select_after_s` seconds (a profile picker or a
  "watch live" prompt), one `select` is sent.
- `{"soccer": "next"}` opens the next game, starting over after the last.
- The `soccer_live` condition lights the key while any game is live. Labels
  can show `{soccer_count}` (live games) and `{soccer_pos}` (e.g. `2/3`).
- While a started game's app stays in front, the context page is
  `context:live_game`. It returns to normal once another app opens.

The log shows `soccer: 3 live: …`, `soccer: playing 1/3: …` and fetch
errors. After an error the last list is kept.

### Now-playing log (ad research)

`"now_playing_log": { "entities": ["media_player.…"] }` records what changes
in those media players (`np_log.py`). Each line names only the fields that
changed: state, app, title, series/episode, content type/id, duration. It also
records `pos_jump=<expected>-><reported>` when the position doesn't match
continuous playback. The goal is to find how a given app (e.g. Tubi) shows
an ad break. Lines go to the app log (`np: …`) and to `now_playing.log` in the
app's config folder (2 MB cap, previous file kept as `.log.1`):

```bash
ssh homeassistant tail -f /app_configs/local_streamdock_n3/now_playing.log
```

### Ad detection (raw media protocol)

Some apps show nothing in HA during ads (Pluto TV). With the app also paired
for the Apple TV's AirPlay service (`bash homeassistant/pair.sh atv airplay`,
PIN on the TV), it has its own media-remote (MRP) session:

- `"ad_detect": {"speaker": "media_player.tv_room_tv_room", "media_player":
  "media_player.vugitv_4k_vugitv_4k", "apps": ["tv.pluto.ios"]}` mutes the
  speaker during detected ad breaks (`ad_detect.py`) and unmutes after,
  undoing only its own mute. Log lines: `ad-detect: HH:MM:SS ad started (…)`.
- `"mrp_raw_log": true` in the `atv` section writes every raw now-playing
  message to `mrp_raw.log` (research; 5 MB cap).

How the Pluto rule was found, the per-app findings, and how to add another
app are in `docs/AD_DETECTION_GUIDE.md` in the repo.

### Probes (live tests)

To find out which controls an app accepts, the app runs probe requests
(`probe.py`) while its Apple TV connection is up. Each request lists steps:
raw MRP commands (`ChangePlaybackRate`, `EnableLanguageOption`, ...; these need
the AirPlay pairing), Companion media-control and button commands, HA
actions, YouTube Lounge calls, webOS commands (with the TV's reply), waits,
and snapshots. A snapshot logs the Apple TV player's advertised commands and
language options, the playing app, the Lounge state, and the HA state of the
listed entities (only entities the app already follows).

The probe files for the test session live in `homeassistant/tools/probes/`,
one per test group. Start the app under test on the TV, then from the repo
root on the PC run:

```bash
bash homeassistant/tools/probe.sh homeassistant/tools/probes/ff_rew.json
```

It prints the file's note, uploads the request to `probe_request.json` in the
app's config folder (consumed at once) and prints the results as they come:
`probe: 3 mrp ChangePlaybackRate {"playbackRate": 24.0} -> ok`, or
`-> error CommandError: …`. A failed step doesn't stop the others; the run
ends with `probe: done`. Power-off steps are refused when the request is
read: any `turn_off`/`toggle` action, every `homeassistant.*` and `hassio.*`
action, `remote.send_command` naming `suspend`/`turn_off`/`sleep`,
`webostv.button` `POWER`, any `script.*` action naming
`script.tv_room_power_toggle`, the Companion `Sleep` button, and webOS
commands (a `webos` step or an `ha` `webostv.command`) containing `turnoff`,
`power` or `reboot`.

### Key images

Set `"icon": "/config/icons/<file>.png"` on a key to show an image instead of
a generated label (112×112 works best). Put the files in
`/addon_configs/local_streamdock_n3/icons/`; the repo copy lives in
`icons/` and `restore.sh` deploys it. No rebuild is needed, just restart the app.

### Live state on keys

A key with `state_entity` is redrawn whenever that entity changes in Home
Assistant (`ha_daemon.py`). `on`, `off` and `unavailable` override the key's
`icon`, `label` and `color` (a field a variant leaves out keeps the key's
own): `off` covers off/standby, `unavailable` covers
unavailable/unknown and HA being unreachable, and `on` covers every other
state (so `playing`/`idle` media players count as on). Use `\\n` in a JSON
label for a line break.

```json
"1": {
  "label": "Night", "color": "#3b3f9e",
  "state_entity": "switch.tv_room_tv_room_night_sound",
  "on":  { "label": "Night\\nON",  "color": "#3b5bdb" },
  "off": { "label": "Night\\nOFF", "color": "#262626" }
}
```

## Events

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

Every input is printed to the app log as `<description> [<event name>]`, so
press a control and read the log to find its event name. `ha-call` and
`atv-knob` failures also appear there.

## Apple TV gotcha

`remote.turn_on`/`remote.turn_off` only connect/disconnect Home Assistant from
the box; they do not change its power. Use `media_player.turn_on`/`turn_off`
for power. After a `remote.turn_off`, every `remote.send_command` fails with
"not connected" until `remote.turn_on`.

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

From the repo root on the PC run `bash homeassistant/restore.sh`. It adds any
missing HA scripts from `ha-config/scripts/`, uploads this folder, then
installs, updates (after a `version` bump in `config.yaml`) or rebuilds the
app. It also deploys `config.tv_room.json` and `icons/`, and confirms the
controller is running. Changes to `config.json` or icons alone only need an
app restart. To move to a newer upstream revision, change `STREAMDOCK_REF` in
the Dockerfile.
