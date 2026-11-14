# Profile auto-selection: design

Date: 2026-11-14. Status: approved 2026-11-14; built (0.6.2-1), not deployed.
Earlier research: [2026-10-26-profile-picker-research.md](2026-10-26-profile-picker-research.md).
Service contract: [services/README.md](../../../homeassistant/streamdock_n3/services/README.md).

Lines marked **(call)** are choices made while writing this, not things the
owner said; everything else comes from the request or the conversation.

## Intent

When a dock key opens a streaming app on the Apple TV, the app may stop on its
"Who's watching" picker. The dock should press OK there by itself, so the app
lands on its home screen under the last-used profile, and it should do nothing
when there is no picker. The viewer should not see a probe.

## Goals

- Enter the highlighted profile after a launch that shows a picker.
- No click at all when the app was still alive (a warm launch): nothing to see.
- A click that turns out wrong and starts a film is undone at once.
- Apps without profiles (Pluto, Tubi, the Apple TV app) are never clicked.
- Every launch leaves a log line saying what the dock decided and why, so the
  per-app facts can be measured on the TV.
- Ships switched off for every app; an app is switched on only after it was
  checked on the TV.

## Non-goals (this version)

- Choosing a profile other than the highlighted one; typing a profile PIN.
- Launches made with the Siri Remote: the dock does not see them.
- YouTube's account picker **(call)**: the same mechanism would cover it by
  adding it to the config, but it was not asked for and is not surveyed.
- Apps on the LG itself.
- Muting the sound during the wait **(call)**.

## Anti-goals

- A BACK/Menu sent without proof that our own click started playback. A blind
  BACK has quit the owner's film before.
- A click while the launched app is playing or paused.
- A click when the dock does not know whether the app was alive.
- Launching made less reliable: if anything in this feature fails, the app
  still opens exactly as today.

## What is known

Measured on the Pi's logs of 2026-11-13 (19:25-22:42), nothing sent to the TV:

- The raw MRP session (the app's own, over the AirPlay pairing) reports app
  **process** lifecycle: `update_client {bundleIdentifier, processIdentifier}`
  when an app registers with the now-playing system and `remove_client` when
  its process ends. A new process id for the same app is a fresh process.
- On every (re)connect of that session the Apple TV lists all live clients.
- Background apps did not live long that evening: Tubi was removed 3 min after
  Prime opened, Pluto, Paramount+ and Max within 7 s as Netflix opened,
  Netflix 31 min after its start. The cause is not in the logs.
- Registration time differs per app: Prime and the Apple TV app register
  shortly after launch with nothing playing; Paramount+ 5 s before playback;
  Netflix in the second of its first `playing`; Hulu only when playback starts
  (about 2 min after its process started).
- Home Assistant reports nothing at a launch until something plays.
- Netflix never reports a title, duration or position: a home-screen preview
  and a film both read `playing`.
- Elsewhere real playback carries data within 1-3 s of `playing`: Prime,
  Paramount+, Hulu, Tubi, Pluto and the Apple TV app report a title and a
  duration; Max reports the name as `media_artist` and a duration. A Max
  start also showed a 27 s item first (a preview or pre-roll).

Not measured (assumed, or from the October desk research):

| Question | Current belief |
|---|---|
| Which apps show a picker on a cold launch on this TV | Netflix, Max, Disney+, Hulu, Paramount+, Prime and Peacock can; an account with one profile shows none. Tubi, Pluto and the Apple TV app do not. |
| A warm launch shows no picker | Assumed; an app may ask again after hours in the background. |
| How soon after a cold launch the picker accepts a click | Unknown; a click during the splash is lost. |
| What select does on each home screen | Netflix probably plays the billboard title; others open a detail page, or play a "continue" tile. |
| What Menu does in each app's player | Assumed to leave the player for the screen before it. |
| Whether an app registers on its picker or only after it | Unknown. |

## Approaches

- **A. Blind click, undo on playback** (the owner's first idea). Click after
  every launch; if a film starts, go back. It clicks on every warm launch; a
  click that opens a detail page reports nothing, so nothing undoes it; and
  in Netflix a preview after a correct click looks the same as a film after a
  wrong one. An accidental start shows for 2-4 s.
- **B. Decide before clicking** (chosen by the owner, 2026-11-14). Know from
  the MRP process messages whether the app was alive. Warm: no click. Cold
  launch of an app listed as having a picker: one click after that app's
  delay. A's playback check stays as a safety net.
- **C. Close the app first**, so every launch is cold. Predictable, but adds a
  visible app-switcher step and loses "resume where I left off".
- **D. Read the screen** from a screenshot. The LG probably cannot capture
  the HDMI input and tvOS needs developer tooling.

## Design

### Parts

1. **`atv_clients.py`** (new core module, next to `ad_detect.py`) **(call)**:
   hooks the MRP session the same way `ad_detect.install` and
   `mrp_raw.install` do, and keeps which apps are alive. The daemon installs
   it before the Apple TV connection is made, whenever the config has an
   `atv` section, and publishes it as `ctx.shared("atv_clients")`. The
   daemon also publishes `ctx.shared("dock_input")` (see Dock input). It lives in
   the core because the hook must exist before the connection does, and
   services start earlier and may not start threads or own connections.
2. **Service `profiles`** in `services/profiles/`: the action `open` and the
   decision. No keys, pages, jobs or conditions of its own.
3. **Preset**: the Apple TV app keys change from
   `{"launch": {"device": "atv", "app": X}}` to
   `{"service": "profiles", "do": "open", "args": {"app": X}}`. The LG Media
   Player key keeps `launch`.

A service was chosen over extending the daemon's `launch` action because the
decision waits, watches and toasts, which is what service actions are for;
the daemon gains only the tracker and two shared handles.

### The tracker (`atv_clients.py`)

Pure state fed by four events; thread-safe; no I/O.

- `on_session()`: a new MRP session began. The alive list is emptied; the
  Apple TV is about to list its live clients. Removal times are kept.
- `on_update(bundle, pid)`: the app is alive with this process id.
- `on_remove(bundle, pid)`: its process ended; remember when.
- `on_lost()`: the connection dropped (`atv_touch.py` `_lost` and `drop`
  call it).
- `status(bundle)` returns one of:
  - `alive`: listed.
  - `gone`: not listed, the session is up and synced.
  - `unknown`: no session, the connection is down, or not synced yet.
- `synced`: `settle_s` (3 s **(call)**) after the **first message** of the
  session, not after its creation (the hook runs before the connection is
  made). Every list seen on 11-13 arrived within one second and always held
  `com.apple.TVAirPlay`, so a live session is never silent after connecting.
- `session_started_at`, `removed_at(bundle)`, `seen_at(bundle)`: times, or
  `None`. Kept in memory only **(call)**: after an app restart there is no
  removal evidence until the next one is seen.

`alive` is reliable. `gone` is reliable only for apps that register as soon
as they start; an app like Hulu that registers at playback is `gone` while it
sits alive in its menus. The service handles that below.

Known limit: a connection that died without notice (seen after the Apple TV
slept) still looks up. The wake rule below covers the usual cause; the
builder should check whether the MRP heartbeat in pyatv can mark such a
session lost, and use it if so.

### The decision (`open`)

```
open(app):
  was_off = the LG is off now
  ctx.media.ensure_on(); TV stayed off -> stop
  if was_off: wait up to sync_wait_s for a session that began after the
              wake and is synced; none -> status is "unknown"
  before = tracker.status(app)          # read BEFORE the launch call
  ctx.media.launch("atv", app)          # always
  entry = apps[app]; no entry or picker false      -> "not listed", stop
  before == alive                                  -> "warm", stop
  before == unknown                                -> "unknown", stop
  before == gone:
    entry.registers == "launch"                    -> cold
    else (registers at playback, the default):
      cold only with evidence: a removal of this app was seen after the
      dock last opened it; otherwise "maybe alive", stop
  cold: wait entry.delay_s, then check again:
    a newer open() was made, or a dock key or knob was used    -> cancel
    the app registered and was removed during the wait         -> cancel
    the tracker now answers "unknown" (connection lost)        -> cancel
    the LG is not on the Apple TV input                        -> cancel
    the Apple TV reports this app playing or paused            -> cancel
    click: remote.send_command select
  watch for watch_s:
    a dock key or knob was used        -> stop watching
    real playback of this app appears  -> remote.send_command menu, once;
                                          toast; stop
```

Details:

- The status is read after the TV is on and before the launch call, because
  some apps register within a second of starting.
- **Waking the TV** **(call)**: when the key also had to power the TV on, the
  Apple TV was asleep and the old session may be dead without notice. The
  tracker is trusted only if a new session began after the wake and synced
  within `sync_wait_s` (5 s). Otherwise the open is "unknown": launch only.
  Whether apps survive a night of sleep, and whether a session reconnects in
  time, are on the survey list; this is the first launch of an evening, so
  it decides how often the feature acts.
- **Apps that register at playback**: no guessing. The service keeps
  `opened_at[app]` (memory only, like the tracker's times). `gone` counts as
  cold only when a removal was seen and it is later than `opened_at[app]`
  (or the dock has not opened the app since it started). An app the dock opened
  that was never played leaves no evidence, so the next open is "maybe
  alive": no click, the viewer picks the profile as today; once the app has
  played and been removed, evidence exists again. An app never seen to end
  has no evidence either: no click.
- Known gap: an app whose removal was seen, then opened with the Siri
  Remote, never played, and left alive looks cold. The safety net is what
  covers a wrong click there.
- **Dock input**: the daemon records the time of the last key or knob event
  and publishes it as `ctx.shared("dock_input")` (`last()` gives a monotonic
  time) **(call)**: a second small core addition, because services cannot
  see other presses today. If the viewer used the dock after this open
  began, the dock stays out: no click, and no undo.
- "Real playback" for the safety net: the Apple TV entity is `playing`, its
  `app_id` is this app, and `media_duration` is at least `film_s` (300 s
  **(call)**). A duration is used rather than a title because previews carry
  titles too and Max sends no title. The state just before the click is
  recorded; playback that was already there never triggers the undo.
- Menu is sent once, never repeated, and only in this case. What Menu does in
  each player is on the survey list; an app where it does not simply leave
  the player gets `"undo": false`.
- **Accepted risk** **(call)**: Siri Remote presses are invisible. If the
  click was right and the viewer then starts a film with the Siri Remote
  inside `watch_s` (5 s), the dock takes it for its own mistake and sends
  Menu: the film closes to the screen before it. `watch_s` is kept short for
  this reason; `"undo": false` per app removes the risk and the safety net
  together.
- Netflix reports no duration, so its safety net can never fire. Netflix
  therefore depends on the cold/warm decision alone, and is switched on only
  if the survey shows it registers reliably.
- The click and Menu go through Home Assistant
  (`remote.send_command` on `atv_remote`) **(call)**: about 0.1 s slower than
  the direct Companion connection, but it is the path the Menu key of the
  dock already uses and it is recorded by the test fakes.
- An exception before the launch call (the tracker) is logged and the launch
  still happens; one after it is logged and ends the action.
- If the tracker is missing (`ctx.shared` returns `None`: no AirPlay pairing,
  no `atv` section), every open is "unknown": launch only.

### Speed

- Warm launch: no wait, no click, nothing on screen.
- Cold launch: the click comes `delay_s` after the launch call. The app's own
  start-up is longer than any probe, so the click adds no visible step when
  the delay is right. `delay_s` is per app and comes from the survey; the
  default is 6 s **(call)**. Too short loses the click (the viewer presses OK,
  as today); too long leaves the picker showing a little longer.
- A wrong click that starts a film: Home Assistant reports the duration 1-3 s
  after playback starts, so the film shows for roughly 2-4 s before Menu.

### Config

In the preset, `"services": {"profiles": {...}}`:

```json
"profiles": {
  "atv": "media_player.vugitv_4k_vugitv_4k",
  "atv_remote": "remote.vugitv_4k_vugitv_4k",
  "lg": "media_player.lg_tv_2",
  "atv_source": "Apple OTT",
  "power_on_wait": 25,
  "dry_run": true,
  "delay_s": 6, "watch_s": 5, "film_s": 300, "sync_wait_s": 5,
  "apps": {
    "com.netflix.Netflix":   {"picker": false, "undo": false},
    "com.amazon.aiv.AIVApp": {"picker": false, "registers": "launch"},
    "com.wbd.stream":        {"picker": false},
    "com.disney.disneyplus": {"picker": false},
    "com.hulu.plus":         {"picker": false},
    "com.cbsvideo.app":      {"picker": false}
  }
}
```

- Per app: `picker` (default false), `delay_s`, `registers` (`"launch"` or
  `"playback"`, default `"playback"`, the cautious one), `undo` (default
  true).
- `dry_run` **(call)**: everything is decided and logged, but neither select
  nor Menu is sent. It ships `true`, with every app `picker: false`. In dry
  run the decision is made for every app in `apps` whatever its `picker`
  value, so the log shows what would have happened.
- Pluto, Tubi and the Apple TV app are simply not in `apps`.
- `profiles` joins `toast.events`; the only toast is the undo
  ("Opened by mistake: went back").

### Log and history

One line per open through `ctx.log`, for example:

```
profiles: open com.hulu.plus: gone, opened 21:44 removed 21:53 -> cold, click in 6.0s
profiles: open com.amazon.aiv.AIVApp: alive (pid 8061) -> warm, no click
profiles: com.hulu.plus: click sent
profiles: com.hulu.plus: playback 7140s after click -> menu sent
profiles: open com.netflix.Netflix: (dry run) gone -> cold, would click in 6.0s
```

The tracker logs each `update_client` / `remove_client` it sees for apps in
the config (`atv-clients: com.hulu.plus alive pid 8155`), so the app log
alone shows launch, registration and removal in order. The last 100 opens
are also kept in `ctx.store` (`history`) with time, app, status, decision
and outcome **(call)**, because the app log rolls over.

### Failure cases

| Case | Result |
|---|---|
| Service fails to load | Its keys do nothing and the log says so. The owner then sees dead app keys; `restore.sh` output and the tests are the guard. Accepted **(call)**: the alternative, a launch hook in the daemon, grows the core for one feature. |
| No MRP session (AirPlay pairing lost) | Launch only. |
| App crashes or is killed during the delay | The click may land on the tvOS Home screen and open the focused app. Guard: if the tracker saw this app register and be removed during the delay, cancel. An app that never registered cannot be guarded. |
| TV stayed off | `ctx.media.ensure_on` returns false: stop. |
| Profile needs a PIN | The click opens the PIN pad and the viewer types it, as they would after pressing OK themselves. |
| Two opens in a row | The older one is cancelled. |

## Tests

Test-first, in `services/profiles/tests/test_profiles.py` and
`tests/test_atv_clients.py`, with `fake_context` and a fake tracker:

- tracker: alive / gone / unknown, the settle time counted from the first
  message, reset on a new session (removal times kept), `on_lost`, removal
  and re-registration, a stale removal for an old pid.
- each branch of the decision, including the `registers` rule (with and
  without evidence) and the wake rule.
- the checks after the delay (newer open, dock input, LG input changed, app
  playing/paused, connection lost).
- the safety net: fires on a long duration, not on a 27 s item, not when
  playback was there before the click, not after dock input, never twice,
  never with `undo: false`.
- dry run sends nothing and logs what it would do.
- the launch happens even when the tracker raises.
- a preset test: every Apple TV app key uses `profiles.open`.

## To check on the TV (with the owner, one step at a time, on "go")

Run with `dry_run: true` first; the log records each launch.

1. Per app (Netflix, Prime, Max, Disney+, Paramount+, Hulu; Peacock if it gets
   a key): open it cold from the dock. Does a picker show? How many seconds
   from the key press until it would accept OK? Does the log say `cold`?
2. Per app: leave to the Home screen, open it again from the dock (warm).
   Picker or not? Does the log say `warm`?
3. Per app: does it register at launch or at playback (read from the log)?
4. Per app, only with a film playing that the owner started for this check:
   what does Menu do in the player?
5. Then, app by app: `picker: true`, `dry_run: false`, and a cold and a warm
   launch each.
6. First launch after the TV was off for the night: does the log show a new
   session in time, and was the app still alive?
7. Later, from normal use: an app left for hours and opened again; an app
   opened with the Siri Remote first.

## Left to the builder

- The exact tracker interface and how its hook reads the protobuf messages
  (`mrp_raw.py` shows the message types and fields).
- Where in the daemon the last-input time is recorded.
- Whether the MRP heartbeat in pyatv can mark a silently dead session lost.
- File layout inside `services/profiles/`.
- Version bump (`0.6.2-1`), `DOCS.md`, the services README's `ctx.shared`
  row, `Dockerfile` (lists core modules by name: add `atv_clients.py`),
  `restore.sh` if the preset change needs it, `docs/JOURNAL.md`.
