# ActionRing N3 tabs — design

Date: 2026-10-24. Status: approved by the user 2026-10-24 (icons: option A).

Related: [JOURNAL.md](../../JOURNAL.md) (system state, current mappings),
[DOCS.md](../../../homeassistant/streamdock_n3/DOCS.md) (app config format),
[AD_DETECTION_GUIDE.md](../../AD_DETECTION_GUIDE.md),
research notes in [2026-10-24-actionring-tabs-research/](2026-10-24-actionring-tabs-research/).

## 1. Intent

The dock has six LCD keys, and today each key does one fixed job. The TV room
uses many apps (Netflix, YouTube, Prime, Max, the LG's own media player, live
soccer, …), and each needs different controls. Six fixed keys can't hold
them all.

The keys become **tabs**. By default the six keys show controls for
**whatever is on the TV right now** (the *context tab*). The round buttons
open temporary tabs: app launchers, Sonos settings, and later lights. These
fall back to the context tab on their own. The user should rarely need the
Apple TV remote. A glance at the dock shows what each key does now, and
state where it is known (playing/paused, speed, subtitles on/off, Night
on/off).

## 2. Goals, non-goals, anti-goals

**Goals**

- Context tab that follows the foreground app on the Apple TV or the LG,
  with app-specific controls and a "nothing playing" set.
- Round-button tabs: Apps (+ overflow "1B"), Sonos. Temporary, with timeouts.
- Keys show live state wherever the source reports it.
- Brand keys look as close to the official app tile as the 64×64 key allows.
- Live soccer: one key starts a live game on Paramount+ or Apple TV (MLS);
  pressing again moves to the next live game.
- Every control on a key works in the app shown. A control that an app
  doesn't support is replaced, never left as a dead key.
- `restore.sh` still reproduces the live system. JOURNAL and DOCS are
  updated.

**Non-goals (this project)**

- Lights: Govee lamp, living-room light, scenes. Round 2 is reserved for
  them; it opens Sonos until then. Lights are a later, separate project.
- Tabs 4–6 by double-click (dropped; double-click now means overflow).
- YouTube playback speed, InSight/X-Ray "who's in this scene", Skip-ad key.
  All are possible later. None is in this plan.
- Changing what the knobs do.

**Anti-goals (a failure even if it technically works)**

- Single presses that feel delayed (e.g. waiting to tell single from double
  click).
- Keys that silently do nothing in the current app.
- Layout flicker while switching apps or when HA reports transient states.
- Committing any secret/identifier to the public repo, or artwork without
  a licence that allows it.
- A tab that "sticks" and hides the context controls.

## 3. Constraints and facts

- **Device.** 6 LCD keys `button.1–6` (top row 1–3, bottom row 4–6), round
  buttons `button.7–9` (no display), knobs `knob.1–3`. Each event comes as
  `.press`/`.release`, one event per detent. Firmware has no double-click
  or long-press events. The app has to time them itself; release events
  make long-press possible later.
- **Key images.** Config icons are 112×112 PNG. The vendored SDK
  (`StreamDockN3.set_key_image`) rotates −90°, resizes to **64×64**, and
  saves a JPEG at Pillow defaults (q75, 4:2:0). Then it sends one native
  call per key, followed by `refresh()`. Designs must read at 64 px. Round
  buttons can't show images.
- **Drawing.** All drawing goes through `ha_daemon._draw_lock`. A tab switch
  redraws up to six keys. This must feel instant: measure it on the device
  (task in the plan) and only send keys whose image changed.
- **State.** `ha_daemon._watch` keeps `_states` current from HA's WebSocket
  (`subscribe_entities`). The YouTube Lounge session (`yt_lounge.py`)
  reports video, position, play state, ads and (in pyytlounge 3.5.0)
  `onSubtitlesTrackChanged`. Our own pyatv connection has Companion (touch)
  and an AirPlay/MRP pairing (`mrp_raw.py` hooks the MRP protocol).
- **Public repo.** No secrets, tokens, serials or addresses. Brand logos are
  trademarks, used nominatively with credits in NOTICE (see §9).
- **Entities.** Apple TV `media_player.vugitv_4k_vugitv_4k` /
  `remote.vugitv_4k_vugitv_4k`; LG `media_player.lg_tv_2`; Arc
  `media_player.tv_room_tv_room` with `switch.tv_room_tv_room_night_sound`,
  `switch.tv_room_tv_room_speech_enhancement`,
  `switch.tv_room_tv_room_subwoofer_enabled`,
  `switch.tv_room_tv_room_surround_enabled`. Other Sonos speakers:
  `media_player.{living_room_living_room,kitchen_kitchen,bathroom_bathroom,boston_boston,sonos_move}`.
  There are no light entities in HA.
- **Subscriptions** (user, 2026-10-24): Netflix, YouTube, Prime Video, Max,
  Hulu, Disney+, Paramount+, Apple TV (incl. MLS); Pluto and Tubi are free.
  Every app key on tabs 1/1B is usable.

## 4. Navigation (decided with the user)

| Input | Behaviour |
|---|---|
| Round 1 (`button.7`) | Opens **tab 1 Apps**. Pressed again while tab 1 is open → **tab 1B More apps**; again → tab 1 (cycles). A quick double-click is just two presses, so it lands on 1B. Single presses act immediately; nothing waits for a second click. |
| Round 2 (`button.8`) | Opens **tab 2 Sonos** (until lights exist; then lights take round 2 and Sonos moves to round 3). Pressed again while open: no 2B yet, it restarts the timer. |
| Round 3 (`button.9`) | **Power on**, as today (wake the Apple TV; the LG follows via CEC). Not a tab for now. |
| Knobs | Unchanged on every tab. Small knob 1 = Arc volume/mute; small knob 2 = focus up/down + OK; large knob = scrub / focus left-right + OK. |

**Temporary tabs.** A tab opened by a round button falls back to the context
tab after **10 s** with no LCD key press. Each LCD key press on that tab
(re)starts a **5 s** timer. Overflow tabs use the same rule. Pressing a
different round button switches straight to that tab, with a fresh 10 s.
Timeouts live in config.

**App keys end the tab** *(my call)*. Pressing an app key on tab 1/1B
launches the app and returns to the context tab at once. The new app's
controls are what the user wants next.

**Round buttons lose Back/Home.** Back and Home move onto LCD keys of the
context tab (as keys 5/6 are today).

## 5. Context tab

### 5.1 Which layout

The **foreground app** decides:

1. If the LG is off/standby/unavailable → *Nothing playing*.
2. If the LG `source` is `Apple OTT` (Apple TV HDMI input), the Apple TV's
   `app_id` decides. If the Apple TV has no app (home screen, screensaver)
   → *Nothing playing*. **Any other Apple TV app** (Plex, Peacock, music
   apps, …) gets the *Streaming* layout with its safe base keys (§5.2)
   *(my call)*, so Play/Pause and Back are always there.
3. Otherwise the LG's own `app_id` decides (YouTube `youtube.leanback.v4`,
   LG media player). Any other LG app gets the *Streaming* layout's base
   keys, sent as LG buttons (`PLAY`/`PAUSE`, `REWIND`/`FASTFORWARD` skip,
   `HOME`, `BACK`) *(my call)*. Live TV / HDMI inputs other than the Apple
   TV get *Nothing playing*. *(Builder: confirm the media player's app id
   live; probably `com.webos.app.mediadiscovery`.)*
4. A YouTube app on either device → the *YouTube* layout.
5. A live game started from the LIVE key (and the app is still the one it
   opened) → the *Live game* layout, until the app changes.

The layout follows the app **while it's in the foreground, menus included**,
not only while playing. **Debounce** *(my call)*: the new layout is drawn
once the foreground app has been stable for ~1 s (config). A
round-button tab that is open stays open. The context underneath updates
silently, ready for the fallback.

### 5.2 Layouts

Key order is 1 2 3 / 4 5 6. "(fb)" marks the fallback used when the app
doesn't support the control.

**Nothing playing**

| 1 | 2 | 3 |
|---|---|---|
| TV power toggle (state: on / off / muted, as key 3 today) | LIVE soccer (state: live / none) | ATV input (LG → `Apple OTT`) |
| **4** | **5** | **6** |
| Night (state) | Home (Apple TV) | Back (Apple TV) |

**Streaming app on Apple TV** (Netflix, Prime Video, Max, Hulu, Disney+,
Paramount+, Apple TV app, Pluto, Tubi)

| 1 | 2 | 3 |
|---|---|---|
| Play/Pause (state) | Rewind (state 1×/2×/4×/8×) — (fb) −10 s | Fast-forward (state 1×/2×/4×/8×) — (fb) +10 s |
| **4** | **5** | **6** |
| Subtitles on/off (state) — (fb) Night (state) | Audio track next — (fb) Home | Back |

**Base keys** *(my call)*: every app starts from the fallbacks: Play/Pause,
−10 s, +10 s / Night, Home, Back. Phase 1 ships exactly these keys. Phase 2
swaps in Rewind/FF/Subtitles/Audio per app, only where that app passed the
live test. Whether HA `skip_backward`/`skip_forward` work in each app is
itself part of the live test. If an app ignores them, its keys 2/3 fall
back to the large knob's scrubbing and show Home/ATV input instead.

**YouTube** (Apple TV or LG)

| 1 | 2 | 3 |
|---|---|---|
| Play/Pause (state) | Next chapter | Next video |
| **4** | **5** | **6** |
| Previous video | Subtitles on/off (state) | Back |

**LG media player** (files on the TV)

| 1 | 2 | 3 |
|---|---|---|
| Play/Pause (state) | Rewind (speed) | Fast-forward (speed) |
| **4** | **5** | **6** |
| Subtitles next — (fb) LG `CC` button | Audio track next — (fb) LG `SAP` button | Back (LG `BACK`) |

**Live game** (started from LIVE)

| 1 | 2 | 3 |
|---|---|---|
| Play/Pause (state) | Next game (shows "2/3") | Subtitles on/off — (fb) Home |
| **4** | **5** | **6** |
| −10 s | Home | Back |

Fallbacks are chosen **per app from evidence**. For Apple TV apps, MRP
`supportedCommands` say what an app accepts (AVKit apps such as the Apple
TV app and Pluto advertise FF/RW, rate change and language options). The
per-app result of the live tests (§10) is written into the config. Run-time
`supportedCommands` may refine it.

### 5.3 Speed semantics

Each FF press steps the speed 2× → 4× → 8× (then stays at 8×). REW does the
same backwards. FF while rewinding switches direction at 2×. Play/Pause, or
any other playback key, returns to 1× and normal play. The keys show the
current speed. The large knob still scrubs. Turning it while scanning first
returns to 1× *(builder: confirm on the TV that the knob engine's
assumptions still hold after a scan)*.

Mapping "2×/4×/8×" onto what each app supports is a builder decision, made
from the live tests. AVKit apps advertise `ChangePlaybackRate` rates
±8/±24/±48/±96. The label shows the step (2×/4×/8×), not the raw rate.

## 6. Round-button tabs

**Tab 1 — Apps:** Netflix, YouTube, Prime Video, Apple TV app, LG media
player, Max.
**Tab 1B — More apps:** Hulu, Disney+, Paramount+, Pluto TV, Tubi, ATV input.

App keys launch on the Apple TV: switch the LG to `Apple OTT` if needed,
then launch by bundle id. The LG media player launches on the LG via
`system.launcher/launch`. If the TV is off, an app key powers on first, then
launches *(my call)*. Bundle ids: Netflix `com.netflix.Netflix`, YouTube
`com.google.ios.youtube`, Prime `com.amazon.aiv.AIVApp`, Apple TV app
`com.apple.TVWatchList`, Max `com.wbd.stream`, Hulu `com.hulu.plus`, Disney+
`com.disney.disneyplus`, Paramount+ `com.cbsvideo.app`, Pluto
`tv.pluto.ios`, Tubi `com.adrise.tubitv`.

**Tab 2 — Sonos** (TV Room Arc):

| 1 | 2 | 3 |
|---|---|---|
| Night (state) | Speech (state) | Next track (lit only while another Sonos speaker plays music; dim otherwise) |
| **4** | **5** | **6** |
| Arc mute (state) | Sub on/off (state) | Surround on/off (state) |

"Next track" goes to the first non-Arc Sonos `media_player` in state
`playing` (builder: group members count once).

## 7. Controls: how each works

Legend: **HA** = HA service call as-is; **pyatv** = our own pyatv
connection in the app; **Lounge** = YouTube session; **test** = must pass
the live test (§10) before it ships for an app.

| Control | Path | Notes |
|---|---|---|
| Play/Pause | HA `media_player.media_play_pause` (Apple TV); LG `media.controls/play`/`pause` via `webostv.command`; Lounge play/pause for YouTube | State from HA media player state, or Lounge state. Netflix reports pause as `idle` (see JOURNAL). |
| FF/REW speed | pyatv MRP `ChangePlaybackRate` (rate option) or `BeginFastForward`/`BeginRewind`; Companion `mediacontrol_command(FastForwardBegin…)` + repeated `right`; LG `FASTFORWARD`/`REWIND` buttons | **test** per app. Pick the path that works per app. |
| Subtitles on/off (Apple TV) | pyatv MRP `EnableLanguageOption`/`DisableLanguageOption` (ids 53/54) with an NSKeyedArchiver-wrapped `LanguageOption` (pyatv #720); state from `currentLanguageOptions` | **test**. Only where the app advertises the commands (AVKit apps do). Netflix/YouTube on the Apple TV don't expose these commands, so they need the fallback. |
| Audio track next (Apple TV) | Same mechanism, type 0, cycle `availableLanguageOptions` | **test**. |
| Subtitles on/off (YouTube) | Lounge `set_closed_captions(lang, video_id)` / `None` = off; state from `onSubtitlesTrackChanged` | Language *(my call)*: English if the video has it, else the first available (`get_available_captions`). |
| Next chapter | Lounge (exists) | — |
| Next / previous video | Lounge `next` / `previous` | — |
| LG subtitles / audio track | luna `com.webos.media/setSubtitleEnable`, `selectTrack` via the `createAlert`/`closeAlert` trick; else `CC` / `SAP` buttons | **test**. Uncertain: the media player may report media id `_`. If luna fails, the keys send the buttons, which open LG's own menu. |
| LG play/pause/FF/REW | `webostv.command` `media.controls/*` or `webostv.button` | FF stepping speed is a **test**. |
| Launch app (Apple TV) | HA `media_player.play_media` `media_content_type: app`, id = bundle id | — |
| Launch app (LG) | `webostv.command` `system.launcher/launch {id}` | — |
| ATV input | HA `media_player.select_source` `Apple OTT` (exists) | — |
| Home / Back / −10 s | HA `remote.send_command` `home` / `menu` / `skip_backward` | `skip_backward` uses the app's preferred interval (10 s in AVKit). |
| TV power, Night, Speech, Mute, Sub, Surround | HA (existing scripts/switches) | — |
| Live soccer | §8 | **test** (deep-link behaviour). |

## 8. Live soccer

**Source** *(my call, from research)*: the Apple TV app's web API
(`uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE`, queries
`searchTerm=Paramount+` and `searchTerm=MLS`). It lists in-progress events
first, with sport, league, live clock, provider and the **deep link** the TV
app itself uses:

- Paramount+: `pplus://www.paramountplus.com/live-tv/stream/<league>/<uuid>…`
- MLS: `https://tv.apple.com/us/sporting-event/<slug>/umc.cse.<id>?playableId=…`

It needs two public web-client parameters (`utsk`, `utscf`) that appear in
every tv.apple.com page. Scrape them at run time, cache them for a day,
re-scrape on 4xx. **Never commit them.** The API is unofficial, so use it
lightly: fetch on key press and every few minutes for the LIVE key's state,
cache ~60 s. ESPN's public scoreboard (`site.api.espn.com/.../soccer/all/scoreboard`)
can confirm "really live". It's optional.

**Filter:** `sportName == "Soccer"`, live now (`clockScore` present and not
ended, or inside `liveBadgeTime`). Provider must be Paramount+
(`tvs.sbd.10120`) or Apple TV (`tvs.sbd.4000`).
**Order** *(my call)*: kick-off time, then league name.

**Behaviour:**

- The LIVE key shows the number of live games (dim when 0).
- First press: if the TV is off, power on. Open game 1 via
  `media_player.play_media` (`url`) or pyatv `launch_app(url)`.
- If the app isn't `playing` within ~8 s, send one `select` (profile picker
  / "watch live" prompt).
- The context tab then shows *Live game*. **Next game** opens the next game
  in the list, wrapping at the end, with "n/N" on the key.
- With no live games, the press does nothing *(builder's choice: or opens
  CBS Sports Golazo on Paramount+)*.

## 9. Icons

**Done already** (prototype, `homeassistant/tools/icons/`): `build_icons.py`
downloads SVGs from the Iconify API and composes 51 keys at 112×112. It
covers brand tiles, playback glyphs, and state variants (`<id>__<state>.png`).
It writes `manifest.json` (source, set, licence, colours per icon) and
`NOTICE.md`. Contact sheets simulate the device's 64×64 JPEG. Rasterizer
`resvg-py` 0.5.0 (aarch64 manylinux wheel, no apt packages). Labels need a
bold sans font: `fonts-liberation2` on Debian.

**Licences used:** material-symbols, mdi (Apache-2.0); logos, simple-icons
(CC0); selfhst (CC BY 4.0, attribution in NOTICE: Prime Video, Max, Hulu,
Disney+); thesvg-color (MIT, per Iconify's collection info linking
github.com/glincker/thesvg: Pluto TV; checked and accepted by the user). No
non-commercial sets. All allow redistribution. Brand marks are used only
on keys that open that service. NOTICE carries the credits and a
no-affiliation line.

**Pipeline** (decided with the user: option A, commit the PNGs):

- The generator runs **on the PC** (Python + Pillow + `resvg-py`). Its
  rendered PNGs, **brand logos included**, are committed to
  `homeassistant/streamdock_n3/icons/` next to `home.png`/`back.png`.
  `NOTICE.md` and `manifest.json` go there too.
- `restore.sh` deploys the folder to `/config/icons/` as today. An icon
  change needs no app rebuild, and the Pi needs no rasterizer or fonts.
- Keys reference icons by **id + state** (resolved to
  `/config/icons/<id>[__<state>].png`), not by hand-written paths.
- `out/` and the SVG download cache stay git-ignored. The generator's
  output folder becomes the committed icons folder (builder: add a flag or
  a copy step).

**Placeholders:** keys without a designed icon yet (Next game, Sub,
Surround, Arc mute) are drawn as text tiles through the existing
`icons.make_icon` path. The user will revise individual icons later.
Unclear brand colours (Hulu, Tubi, Prime, Max) and the thin Disney+
wordmark are known items.

## 10. Live test session (gate for "test" controls)

**Who:** the builder prepares a test script, run from the PC over
`ssh homeassistant`. It sends each candidate command and logs what HA, MRP
and Lounge report. The **user watches the TV** and says what happened, as
in every earlier feature ("Confirmed on the TV"). This is a blocking
checkpoint in the plan. Live soccer needs a match on at the time.

Run with `mrp_raw_log` on, and record the results in JOURNAL:

1. Play each app (Netflix, Prime, Max, Hulu, Disney+, Paramount+, Apple TV
   app, Pluto, Tubi, YouTube) and capture `supportedCommands`.
2. FF/REW three ways (MRP rate/begin-end, Companion begin/end + `right`, HA
   `right` hold): which apps respond, what steps exist, how to return to 1×.
3. Subtitles/audio via MRP language options in the Apple TV app and Pluto,
   then in the others that advertise them.
4. YouTube: `next`, `previous`, `set_closed_captions` and the subtitle event.
5. LG media player: app id, `getForegroundAppInfo` media id, luna trick for
   subtitles/tracks, `CC`/`SAP`/`FASTFORWARD` behaviour.
6. Live soccer during a live match: P+ `pplus://` and `https://` forms, MLS
   link, profile picker, token lifetime.
7. Tab-switch redraw time for six keys.

## 11. Config shape (sketch — builder may refine)

Extends `config.tv_room.json`. Keys keep the existing key format
(`state_entity`, `on`/`off`/`unavailable`, `modes`, `action`), plus
`icon_id` (+ per-state ids) in place of file paths. Sketch:

```json
{
  "tabs": {
    "timeouts": {"open": 10, "after_press": 5, "context_debounce": 1},
    "round": {"7": ["apps", "apps_more"], "8": ["sonos"]},
    "apps": {"keys": {"1": {"icon_id": "netflix", "action": {"launch": "com.netflix.Netflix"}}}},
    "sonos": {"keys": {}},
    "context": {
      "layouts": {
        "idle": {"keys": {}},
        "streaming": {"apps": ["com.netflix.Netflix", "..."], "keys": {}},
        "youtube": {"keys": {}}
      }
    }
  },
  "actions": {"button.9.press": "…power on (unchanged)…"}
}
```

`button.1–8.press` route to the tab engine. `button.9` and the knobs keep
their own actions. Per-app fallbacks are written as key `modes` or as
per-app key overrides within a layout. Builder's choice.

## 12. Build order

1. **Tab engine + static tabs:** tab state machine (round presses, cycle to
   1B, timers, fallback), context detection with debounce, layout drawing
   with redraw-only-changed, Apps/1B/Sonos tabs. Context layouts use only
   controls that already work (play/pause, Lounge, Home/Back/−10 s, launch,
   Sonos). Committed icon set (§9). Deploy through `restore.sh`;
   update JOURNAL and DOCS.
2. **Live test session** (§10), then FF/REW speeds and subtitles/audio for
   the apps where they passed, plus LG media-player tracks.
3. **Live soccer** (§8).
4. Later, separate: lights (Govee research is in the research notes), and
   InSight/X-Ray, YouTube speed, Skip-ad if wanted.

## 13. Calls I made (for the user to check)

- App keys return to the context tab immediately.
- App key with the TV off: power on first, then launch.
- Context layout switches after ~1 s of a stable foreground app; it follows
  the app in its menus too.
- FF/REW: 2×/4×/8×, capped at 8×; Play returns to 1×; FF while rewinding
  restarts at 2× forward.
- YouTube subtitles: English if available, else the first track.
- Live soccer: Apple's TV-app API as the source; order by kick-off; wrap
  around.
- Round 2 pressed again on the Sonos tab only restarts its timer.
- Sonos tab keys 4–6: Arc mute, Sub, Surround.
- Streaming base keys before (or without) a passed test: Play/Pause, −10 s,
  +10 s / Night, Home, Back.
- Apple TV apps outside the list get the Streaming layout; other LG apps get
  its base keys as LG buttons.
- Live tests: the builder scripts them, the user watches the TV and
  confirms.

## 14. Left to the builder

- Module layout (e.g. a new `tabs.py` beside `ha_daemon.py`), config field
  names, and how key `modes` merge with per-app overrides.
- FF/REW implementation per app, from the test results.
- Exact debounce, the fetch cadence for the LIVE key, ESPN cross-check
  yes/no.
- Whether LIVE with no games opens Golazo.
- Error display: a failed action briefly shows the amber "?" variant.
