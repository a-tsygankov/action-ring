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
  "scrub_apps": {},
  "touch_steps": [12, [60, 2], [150, 3]],
  "touch_y": 500,
  "touch_min_x": 300,
  "touch_max_x": 850,
  "touch_move_px": 25,
  "touch_interval": 0.016,
  "touch_release_after": 0.6,
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
`"scrub_apps"` adjusts it per app, by the player's `app_id`: a mode name
(`"seek"` for an app that can't scrub by touch) or an object with `"mode"`
and/or `"pause": false`. YouTube (`com.google.ios.youtube`) has
`"pause": false`: its scrubber opens on a swipe while playing, whereas after
a pause the focus sits on the control row and swipes walk the buttons.
It also has `"paused": "nav"`: YouTube reports paused while its video grid
is on screen, so there the large knob moves focus left/right (one tile per
slow click) instead of scrubbing, and no auto-commit `select` lands on a
tile. A scrub still awaiting its commit keeps scrubbing (a re-grip at the
touch surface's edge can pause the video mid-scrub).

`"touch_flick": 150` and `"touch_flick_wait": 0.8` (TV Room config, every
app; per app in `"scrub_apps"` too): a fresh touch starts with a quick
150-unit swipe in the turn's direction (finger down, fast move, lift), then
waits 0.8 s before the real drag. After 20-30 s of playback no app reacted
to the first slow clicks, while later turns acted at once: the first touch
input after a while is lost. The first scrub so starts about a second
after the first click. `0` disables it.
Disney+ (`com.disney.disneyplus`) has `"seek"`: its player ignores touch
swipes and draws no preview for key presses either, but lands an absolute
seek exactly. Every other app on the dock scrubs by touch with its preview
(Tubi shows the playhead and time only: its streams carry no thumbnails).

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
   the middle of that range (575), like repeated swipes. The range starts at
   300 because tvOS misreads touch x below 256 (pyatv sends those in OPACK's
   one-byte form) as a far-right position and the scrub jumps forward.
4. After `touch_release_after` seconds without a click the finger lifts. The
   preview stays on screen; press the knob (OK) to jump there, or Back to
   cancel.

If a touch event fails although the connection looks up (seen after the
Apple TV slept: pyatv gets no `connection_lost`), the app drops the
connection so it reconnects, and sends that knob batch as a seek instead;
the log says `atv-knob: touch failed (...); seeking instead`.

Touch needs the app's own Apple TV pairing over the Companion protocol
(`atv_touch.py`, pyatv). This pairing is separate from Home Assistant's,
because HA's integration doesn't expose touch gestures. Until the app is
paired, or whenever the connection is down, scrubbing falls back to
`"seek"`.

Pairing: from the repo root on the PC run `bash homeassistant/pair.sh atv`.
It asks the Apple TV for a PIN, prompts for it and waits until the touch
connection is up. The Apple TV is the one whose HA entry owns `atv.media_player`
(not merely the first Apple TV in HA); `ATV_ADDRESS=<ip>` overrides. A new box
needs a fresh pairing (remove `atv_companion.json` first; see "Replacing the
Apple TV" in `docs/JOURNAL.md`). `restore.sh` runs this step itself when the
app isn't paired yet. To pair by hand from the SSH app:

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
  "timeouts": { "open": 5, "after_press": 5, "context_debounce": 1 },
  "round": { "7": ["apps", "apps_more"], "8": ["sonos"] },
  "context": { "lg": "media_player.lg_tv_2", "atv": "media_player.vugitv_4k_vugitv_4k",
               "atv_source": "Apple OTT",
               "youtube_apps": { "atv": ["com.google.ios.youtube"], "lg": ["youtube.leanback.v4"] },
               "lg_media_apps": ["com.webos.app.mediadiscovery"],
               "lg_idle_app_prefixes": ["com.webos.app.hdmi", "com.webos.app.livetv", "com.webos.app.home"],
               "content_apps": ["com.apple.TVWatchList"], "min_play_s": 10 },
  "logo": { "key": 6, "show_s": 2, "every_s": [10, 15], "default": "apple_tv_app",
            "sources": { "PS5 Game Console": "ps5" }, "apps": { "youtube.leanback.v4": "youtube" } },
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
- **Which Apple TV app counts.** The Apple TV reports its now-playing app,
  not the one on screen: after you leave a paused video it keeps reporting
  that app while you browse elsewhere, and menus autoplay previews. So an
  app gets its page only after playing `min_play_s` seconds without a break
  since it became the reported app; a pause keeps the page. Apps in
  `content_apps` (the TV app) count only for real content
  (`media_content_type` `video`), never for trailers or previews. Anything
  else shows the idle page.
- **Service logo** (`logo`, `logo.py`): on a context page, key `key` shows
  the logo of what is on screen for `show_s` seconds every `every_s` seconds
  (a `[min, max]` range picks each gap at random); a press does what the key
  does on that page. App pages show the app's icon, taken from the launch
  keys on the pages plus `apps`; the idle page shows the LG input's logo
  from `sources` (here the PS5), else `default` (the Apple TV).
- **Temporary pages** (here `apps`, `apps_more`, `sonos`) open from a round
  button. `round` maps a button to its cycle of pages: each press shows the
  next page in the cycle, and after the last one the cycle starts again. A
  page closes back to the context after `open` seconds, or `after_press`
  seconds after a key press on it; with `close_on_press` a key press closes
  it at once. A new context layout (the TV turned off, another app or
  input) closes it too, once that layout has held `context_debounce` s.
- **TV Room layout.** Keys run 1 2 3 on the top row, 4 5 6 below. Each
  context page puts its back/forward pair side by side on one row (back on
  key 2 or 4): ⟲10/⟳10 (Apple TV apps, a live game), ⏪/⏩ (LG), −10 s/+10 s
  (YouTube). The YouTube page's key 2 takes turns between next and previous
  video and key 3 between like and dislike, 8 s each, with a bar that
  shrinks in 3 steps over the last 1.8 s; a press on a thumb shakes it
  (dynamic keys, below).
  Night sound and subtitles are only
  on tab 2A (`sonos`, round button 2 once): Night, Speech, Next track,
  Mute, Subtitles (the linked YouTube's captions), Surround. Key 4 of the
  idle, streaming and LG pages is left blank.
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
| `{"youtube": "play_pause" \| "next" \| "previous" \| "next_chapter" \| "forward" \| "rewind" \| "subtitles"}` | Controls the linked YouTube app (see below); `forward` / `rewind` skip 10 s, `subtitles` toggles captions |
| `{"toast": "text"}` | Shows `text` on the TV for a few seconds, over any input (see TV toasts); `{name}` placeholders work |
| `{"youtube": "like" \| "dislike"}` | Rates the playing video as the signed-in Google account; again on a rated video clears it (see Like / dislike) |
| `{"service": "<name>", "do": "<action>", "args": {…}}` | Runs a service action on its own thread (see Services). Live soccer: `{"service": "soccer", "do": "start" \| "next"}` picks the first live game, or the next one while picking, and switches to it (powering on first); `next` always moves on (see Live soccer) |

Named `when` conditions for modes: `youtube` (YouTube in the foreground on a
screen), `youtube_playing`, `youtube_subtitles` (its live play and caption
state), `youtube_chapters` (the playing video has chapters; rechecked on every
video change), `youtube_liked`, `youtube_disliked` (the playing video's rating
by the signed-in account), `soccer.live` (a live soccer game is listed), `soccer.picking`
(a game was picked in the last minute, or a started game is on screen).
Services add conditions named `<service>.<name>`.

A key slot can also take its whole spec from a service:
`{"service_key": "soccer.live"}` (see Services).

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
`"youtube_subtitles"`, `"soccer.live"`, `"soccer.picking"`, see above) or a list of conditions,
any of which may match:
`{"entity": …, "attr": "source" | "app_id" | … | "state", "in": [values]}`.
The LIVE key on `context:idle` lights up with the number of listed games,
and shows the pick (e.g. `2/8`, on green) as soon as it is pressed:

```json
"2": {
  "icon": "soccer_live__none", "label": "",
  "modes": [
    { "when": "soccer.picking", "icon": "soccer_live__live", "label": "{soccer_pos}", "color": "#1d6b32" },
    { "when": "soccer.live", "icon": "soccer_live__live", "label": "{soccer_count} LIVE" }
  ],
  "action": { "service": "soccer", "do": "start" }
}
```

A key with both an icon and a label shows the label on a band across the
bottom of the icon, in the key's `color` (black by default).

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

### Dynamic keys

A key can take turns between functions on a timer, to fit more on a page:
e.g. 5 s thumbs up, then 5 s thumbs down, and again. Give it a `dynamic`
section with two or more `phases`; each phase is laid over the key's own
spec, so it can set `icon`, `label`, `color`, `action`, `modes` or
`state_entity`, and fields it leaves out come from the key. The YouTube
page's like/dislike key (key 3); each phase lights up when the video has
that rating:

```json
"3": {
  "dynamic": {
    "hold_s": 5, "warn_s": 1, "warn": "countdown",
    "phases": [
      { "icon": "thumb_up__off", "modes": [ { "when": "youtube_liked", "icon": "thumb_up__on" } ],
        "action": { "youtube": "like" } },
      { "icon": "thumb_down__off", "modes": [ { "when": "youtube_disliked", "icon": "thumb_down__on" } ],
        "action": { "youtube": "dislike" } }
    ]
  }
}
```

Key 2 beside it takes turns between `next_video` (`{"youtube": "next"}`)
and `prev_video` (`{"youtube": "previous"}`) the same way.

| Field | Default | Meaning |
|---|---|---|
| `hold_s` | 5 | Seconds each phase shows; a phase's own `hold_s` overrides it |
| `warn_s` | 1 | Seconds before a change that the warning shows (0: none) |
| `warn` | `countdown` | `countdown`: 3-2-1 in the top-right corner; `bar`: a bar along the bottom (above a label band) that shrinks to nothing; `dots`: three dots along the top that go out one by one |
| `warn_steps` | 3 (`bar`: 5) | Segments the warning moves through, spread evenly over `warn_s` (fewer: each one stays up longer) |
| `warn_color` | `#ffb300` | Colour of the warning |
| `grace_s` | 0.25 | A press this soon after a change still does the old phase's function |

A phase may also set `"press": "shake"`: a press of the key in that phase
wiggles the phase's icon (what was pressed, e.g. the thumb) for about 0.6 s.
During it the key stays on that phase and shows no warning, so a second
press does the same again; the timer keeps running, and if it moved on to
the next phase meanwhile, that phase shows when the shake ends.

- The cycle starts on the first phase whenever the page is shown (a context
  page adopted, a tab opened or closed back to it).
- A page may hold several dynamic keys; each keeps its own phases and holds.
- A press does what the key *showed*: the phase last drawn on the dock (a
  slow redraw never runs the next function early), or the one before it
  within `grace_s` of the swap, since the finger was already on its way.
  The log shows `dynamic: key N pressed in phase P`.
- While a dynamic key is on screen the renderer wakes for each warning
  frame (3 per warning for `countdown` and `dots`, 5 for `bar`, spread over
  `warn_s`); frames are
  cached images and are not logged.

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
timestamps) and seeks to the next one. The YouTube page's key 5 shows
"next chapter" while the playing video has chapters and "+10 s"
(`{"youtube": "forward"}`) otherwise, beside "−10 s" (`{"youtube":
"rewind"}`, not before the start) on key 4; a new video shows "+10 s" until its
chapters have loaded. Ad starts and ends are logged
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

#### Like / dislike

The TV link has no rating command, so `{"youtube": "like"}` and
`{"youtube": "dislike"}` go to the YouTube Data API (`yt_rating.py`) as your
Google account, for the video the TV link reports. Turn it on with
`"youtube": { "rating": true, "screens": … }`. Pressing like on a liked
video clears the rating (as in the YouTube app); the key changes at once and
changes back if Google refuses. The rating of each new video is read once
(`videos.getRating`, 1 quota unit; a rating costs 50 of the free 10,000 a
day).

One-time setup in the Google Cloud console (console.cloud.google.com):

1. Create a project (e.g. `action-ring`).
2. **APIs & Services → Library → YouTube Data API v3 → Enable.**
3. **Google Auth Platform → Branding:**

   | Field | Value |
   |---|---|
   | App name | `ActionRing TV Remote` (no "YouTube" or "Google": Google rejects its trademarks in app names) |
   | User support email | your Gmail |
   | App logo | **none**: a logo makes Google require verification first |
   | Home page, privacy policy, terms of service | empty (only needed to submit for verification) |
   | Authorized domains | empty (the device sign-in has no redirect) |
   | Developer contact email | your Gmail |

   Accepting the Google API Services User Data Policy checkbox is yours to
   do. Save.
4. **Audience:** External, then **Publish app** (In production); if it
   offers verification, don't submit. No test users needed. Left in
   *Testing*, the sign-in expires after 7 days. Unverified is fine for your
   own account (up to 100 users): the sign-in shows a warning, pick
   **Advanced → Go to ActionRing TV Remote**.
5. **Data Access:** add no scopes; the app asks for the YouTube scope at
   sign-in, and declaring it here only adds verification prompts.
6. **Clients → Create client → TVs and Limited Input devices** (name e.g.
   `ActionRing N3`), then download its JSON.

Then, from the PC:

```bash
bash homeassistant/pair.sh youtube-rating ~/Downloads/client_secret_….json
```

It copies the client to `yt_oauth_client.json` in the app's config folder,
starts the sign-in and prints e.g. `youtube-rating: sign-in: open
https://www.google.com/device and enter ABCD-EFGH`. Open that on a phone,
sign in with the account that is signed in to YouTube on the TV, enter the
code and allow access. With TV toasts on, the code also shows on the TV, again
every minute until you sign in. The refresh token is saved to `yt_oauth.json`
(secret; not in the repo). By hand: put the client JSON in
`yt_oauth_client.json` and create an empty `yt_rate_auth` there; the log shows
the code. If the sign-in is revoked, the log says so; sign in again the same
way. `pair.sh status` shows whether ratings are signed in.

### TV toasts

The Apple TV can't show text sent from outside: tvOS has no overlay or
notification for other devices, AirPlaying a picture with the text would
replace what is playing, and the Apple TV keyboard (pyatv `text_set`) only
types into a text field that is already open. The LG can: a webOS toast
is a small box in the corner for a few seconds, over any input (the Apple TV
included), while the picture keeps playing (`toast.py`, HA
`webostv.command` → `system.notifications/createToast`). A `toast` section
turns it on:

```json
"toast": { "lg": "media_player.lg_tv_2",
           "events": ["rating", "soccer", "sign_in"], "settle_s": 0.6 }
```

| Event | Toasts |
|---|---|
| `rating` | `Liked`, `Like removed`, `Disliked`, `Dislike removed`, `Rating failed`, `Like/dislike: not signed in`, `Like/dislike: no YouTube video` |
| `soccer` | Each pick, e.g. `2/8 Columbus Crew vs. Toronto FC`; `No live soccer games` |
| `ad` | `Ad: sound muted`, `Ad over: sound on` (only when ad muting itself mutes or unmutes; off in `tv_room.json`: too noisy) |
| `sign_in` | The Google sign-in code for like/dislike, every minute until you sign in; then done / failed / expired |

Tubi (and any app that pauses for its ads) is muted by the Home Assistant
automation `streaming_pause_mute`, not by the app's ad detection; it shows
no toast.

`events` lists which of these show (all when left out). Soccer picks are
merged: a run of presses shows only the last pick, `settle_s` after the
presses stop. Nothing is sent while the TV is off. A key can show its own
text with `{"toast": "text"}`. Toasts are plain text, cut to 120
characters; webOS sets their place, look and length on screen.

### Services

A service is a plug-in feature (live soccer is the first) that ships in the
image under `services/<name>/`. [services/README.md](services/README.md)
explains how to write one. The `services` section of `config.json` turns
services on; each section is passed to its service as its config, and
`"enabled": false` keeps it but does not load it. A service that is not listed
is never loaded.

```json
"services": { "soccer": { "atv": "media_player.…", "poll_s": 180 } },
"metrics": { "log_every_s": 600, "slow_ms": { "job": 2000, "key": 20 } }
```

- **`service_key`** puts a service's key on a slot:
  `"6": { "service_key": "soccer.live" }`, optionally with `"args": {…}`. The
  slot's other fields (`label`, `color`, `icon`, …) are written on top of
  what the service gives, so a preset can relabel it. A key the service
  cannot produce right now shows `?`.
- **`{"service": "<name>", "do": "<action>", "args": {…}}`** is the key
  action that runs a service action (on its own thread, so it may wait for
  the TV).
- **Pages.** A service page is named `<service>.<page>` and goes in the
  `tabs.round` cycles like any page.
- **Conditions** are `<service>.<name>` (`soccer.live`, `soccer.picking`) and
  label variables start with `<service>_` (`{soccer_count}`).
- **Toasts.** A service's toasts show only when its name is in
  `toast.events`.
- **`/config/secrets.json`** holds API keys and tokens, per service:
  `{"<service>": {"api_key": "…"}}`. It exists only on the device, is never
  in the repo, and `restore.sh` neither creates nor overwrites it; a service
  reads it with `ctx.secret(name)`.

A service that fails to load or start is skipped with
`services: <name> disabled (…)` in the log. Service keys, conditions and
variables must not block: if a service's render-path calls fail or overrun 3
times within a minute, its keys show `?` and its conditions read false for 5
minutes (`services: <name> render calls off for 5 min (…)`).

#### Metrics

The services host measures every service operation (jobs, actions, keys, HTTP
and Home Assistant calls) and the daemon's own render pass, image generation
and HA events. Even with no services configured, the host writes a
`metrics:` summary to the log and rewrites `/config/metrics.json` (on the host:
`/app_configs/local_streamdock_n3/metrics.json`) every `metrics.log_every_s`
seconds (default 600):

```
metrics: soccer cpu 0.42s (0.07%) wall 3.1s · 4 jobs, 0 err · http 8 req 1.2 MB p95 0.9s · keys 410 p95 1ms
metrics: app cpu 5.1% rss 120 MB threads 38
```

An operation that passes its `metrics.slow_ms` budget (milliseconds per kind:
`job` 2000, `action` 3000, `http` 5000, `key` 20, `context` 5, `on_state` 20,
`cond` 5, `vars` 5) logs `metrics: SLOW <service> <op> …`, at most once a
minute per operation. From the PC, `bash homeassistant/tools/metrics.sh [--by
cpu|wall|http|errors] [--window interval|total] [host]` prints the snapshot as
a table ranked by that column.

### Live soccer

The `services.soccer` section turns on a live-soccer key for Paramount+ and
MLS (Apple TV) games (`services/soccer/`):

```json
"services": { "soccer": {
  "atv": "media_player.vugitv_4k_vugitv_4k", "atv_remote": "remote.vugitv_4k_vugitv_4k",
  "lg": "media_player.lg_tv_2", "atv_source": "Apple OTT", "power_on_wait": 25,
  "token_page": "https://tv.apple.com/us/channel/mls/tvs.sbd.7000",
  "cache_s": 60, "poll_s": 180, "idle_poll_s": 1800, "select_after_s": 8 } }
```

The LIVE key is `{"service_key": "soccer.live"}` and the NEXT key on
`context:live_game` is `{"service_key": "soccer.next"}`; add `soccer` to
`toast.events` to see the picks.

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

- `{"service": "soccer", "do": "start"}` picks the first game, and the key shows `1/8` at
  once. Pressing again within a minute (or while a started game is on
  screen) picks the next one, `2/8`, wrapping after the last. Once the
  presses settle for `switch_after_s` seconds (default 1.5) it powers the TV
  on through the Apple TV if needed, switches the LG to `atv_source`, and
  opens the last pick only; a newer pick cancels a switch still under way.
  The pick follows its game when the list changes. With nothing listed yet,
  a press fetches first. The game opens from the same link
  the Apple TV app uses (`pplus://…` for Paramount+, a
  `tv.apple.com/…/sporting-event/…` link for MLS). If that app is in front
  but not playing after `select_after_s` seconds (a profile picker or a
  "watch live" prompt), one `select` is sent.
- `{"service": "soccer", "do": "next"}` always picks the next game (the NEXT key on
  `context:live_game`).
- The `soccer.live` condition lights the key while any game is live. Labels
  can show `{soccer_count}` (live games) and `{soccer_pos}` (e.g. `2/3`).
- While a started game's app stays in front, the context page is
  `context:live_game`. It returns to normal once another app opens.

The log shows `soccer: 3 live: …`, `soccer: picked 2/3: …`, `soccer: playing 2/3: …` and fetch
errors. After an error the last list is kept.

### Scene jump

The `services.scenes` section turns on a key that jumps to the next scene
of a film that is marked in public scene lists (`services/scenes/`; the
sources and their rules are in [docs/SCENE_SOURCES.md](../../docs/SCENE_SOURCES.md),
the design in `docs/superpowers/specs/2026-11-13-scene-jump-design.md`).

The key is `{"service_key": "scenes.next"}`, slot 4 of `context:streaming`.
It is gray, and turns to colour when a film is playing in one of the
`seek_apps`, the film is in the lists, and a scene starts more than
max(`guard_s`, `lead_s`) seconds after the current position (so a press
always moves the picture forward). One press seeks (`media_player.media_seek`)
to `lead_s` seconds before that scene. A second press during the lead-in goes
to the scene after it; with none left the key goes gray again. If the film
drops out for a moment (loading after a seek, the title briefly missing) the
key is gray, and the same film coming back within `settle_s` carries on at
once, second-press memory included; after longer it is noticed anew.

```json
"services": { "scenes": {
  "atv": "media_player.vugitv_4k_vugitv_4k",
  "seek_apps": ["com.wbd.stream", "com.disney.disneyplus"],
  "exclude_apps": ["com.google.ios.youtube"],
  "sources": ["videoskip", "nsfwhen"],
  "app_services": { "com.wbd.stream": "max", "com.disney.disneyplus": "disneyplus" } } }
```

- **`seek_apps`**: the apps where the jump is allowed (Max and Disney+ to
  start with). Other apps still get their film looked up, but the key stays
  gray; add an app only after a seek has been checked on the TV in it.
- **`lead_s`** (5): how many seconds before the scene the jump lands.
  `guard_s` (2) and `join_gap_s` (20, nearby scenes merged into one) tune
  the same thing.
- **`categories`** (`["nudity", "sex"]`): which kinds of scene count.
- **`sources`** (`["videoskip", "nsfwhen"]`): where the lists
  come from, in this order. `app_services` names the VideoSkip streaming
  service per app, so the file's time offset for that service is applied.
  VideoSkip reads the film's 3 newest skip files and uses the best of those
  (synced with the playing service, else the most downloaded of the three).
  `nudidex` is known but left out: it sits behind a bot check and has no
  parser, so asking it only stored a `blocked` row per film that counted
  against `max_lookups_per_day`.
- **`source_cfg`** (`{}`): per-source settings, by source name, passed to
  that source. The only one today is `{"nudidex": {"probe": true}}`: with
  `nudidex` in `sources`, one request to its home page per film to see
  whether the bot check is still there (`blocked`) or gone (`miss`);
  without `probe` nudidex makes no request.
- **`max_lookups_per_day`** (30), `request_gap_s` (3) and the TTLs
  `hit_ttl_days` (90), `miss_ttl_days` (14), `blocked_ttl_days` (1) keep the
  use of the sites light. Results are cached in `scenes.sqlite`.
- **Film filter**: only content of at least `min_film_s` (3000 s) or typed
  as a movie; a film is looked up `settle_s` (10) seconds after it starts.
  The first lookup of a new film runs inside the 5 s `watch` job and normally
  takes 20-30 s (the sites are asked `request_gap_s` apart), so one
  `metrics: SLOW scenes job:watch` line per new film is expected.

The TMDB key (to identify the film) goes in `/config/secrets.json` on the
device: `{"scenes": {"tmdb_key": "..."}}`. Without it VideoSkip is not asked
(a title alone is not enough to trust a skip file; the log says
`videoskip: skipped (film not identified)`), but NSFWhen can still match by
title and runtime and light the key. The log says
`scenes: no tmdb_key in secrets.json` once. Identification is retried: with
no key on every lookup until one is there, after a failure after
`blocked_ttl_days`, after "not found" after `miss_ttl_days`; once a film is
identified, sources that missed or were ambiguous for it are asked again.

Log lines (all start with `scenes:`): `film: "Title" 110 min in <app>` when a
film is noticed; `"Title": 3 scenes` for the merged result; one line per
source, `videoskip: hit, 2 scenes, 4 requests` (status `hit`, `miss`, `ambiguous`, `error` or
`blocked`; no addresses are logged); `tmdb failed: <error type>`;
`videoskip: skipped (film not identified)`; `daily lookup limit reached`;
`app cannot seek: key stays gray`; and each press `jump 1203s -> 1388s
(scene at 1393s)`. No toasts in this version.

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
  Its own mute is remembered across app restarts (`/data/ad_mute_*.json`):
  after a restart mid-ad it still unmutes at the ad's end, or after 20 s if
  no ad resumes.
  With `"lg"` and `"atv_source"` it mutes only while the TV shows the Apple TV
  input, so another input (a game console) keeps its sound.
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
device taps and snapshots. A tap (`{"kind": "tap", "path": "/dev/hidraw0",
"s": 40}`) reads the dock's raw reports from a `/dev/hidraw*` or
`/dev/input/event*` node for `s` seconds alongside the daemon and logs each
one as hex (input events as type/code/value): `tools/probes/knob_tap.json`
shows whether knob turns reach the host at all (vendor reports carry the
code in byte 9: `0x50`/`0x51` large knob, `0x01`–`0x06` LCD keys). A snapshot logs the Apple TV player's advertised commands and
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
a generated label (the repo's icons are 112×112, see below). Put the files in
`/addon_configs/local_streamdock_n3/icons/`; the repo copy lives in
`icons/` and `restore.sh` deploys it. No rebuild is needed, just restart the app.

**What a key actually shows.** Each of the six LCD keys is **64×64 pixels**;
the round buttons have no screen. The SDK's `StreamDockN3.set_key_image`
takes any image file, rotates it −90°, resizes it to 64×64
(`key_image_format()`) and sends it as a JPEG saved with Pillow's defaults:
**quality 75, 4:2:0 chroma subsampling**, so colour detail is about 32×32.
Images go out as 24-bit RGB; the panel's own colour depth is not known
(small LCDs are often 16-bit, which would band gradients).

- Design for 64 px: strokes under ~2 px at 112 px blur or vanish, and thin
  coloured lines or small coloured text smear more than white on dark.
- **Why 112×112:** inherited, not a hardware size. The vendor SDK's N3
  driver carries the comment "Set device key icon image 112 * 112", copied
  from the StreamDock N4 driver (whose keys are 112×112); the N3's real
  format is 64×64, and the upstream daemon draws its own text tiles at
  64×64. The first icons here followed the comment and `build_icons.py`
  keeps `SIZE = 112`. It does no harm (the downscale smooths edges), but
  nothing lines up with real pixels.
- If keys look soft, the bigger win is patching the SDK's JPEG save to
  `quality=95, subsampling=0` (as `patch_ids.py` patches it at build);
  drawing at 64×64 natively would come after.
- `tools/icons/build_icons.py` renders `out/contact_device.png`, every icon
  as the device shows it (rotate, 64×64, JPEG q75 4:2:0, enlarged ×2).
- Palette (`build_icons.py`): background `#262626`, glyphs `#ffffff`, off
  `#8c8c8c`, unknown `#5c3b00`; on-states Night `#3b5bdb`, Speech
  `#2b8a3e`, TV `#a3302c`, muted `#c77c02`, subtitles `#0c8599`, rated
  (like/dislike) `#1971c2`; dynamic-key warnings `#ffb300`.
- Dynamic-key warnings at 64 px: the countdown circle is ~23 px (digit
  ~17 px), the bar ~6 px tall, the dots ~8 px across.

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

Sometimes the dock comes back but the Supervisor does not grant its new
device nodes to the running container (the dock shows its built-in images;
`Operation not permitted`). Before each start `run.sh` opens the dock's
hidraw and input nodes; if they stay blocked for ~10 s it logs `dock nodes
not accessible: ... - restarting the app` and exits with an error, and the
app's **Watchdog** (keep it on) restarts the app in a fresh container, which
gets the nodes again.

## Updating

From the repo root on the PC run `bash homeassistant/restore.sh`. It adds any
missing HA scripts from `ha-config/scripts/`, uploads this folder, then
installs, updates (after a `version` bump in `config.yaml`) or rebuilds the
app. It also deploys `presets/tv_room.json` (as `config.json`) and `icons/`, and confirms the
controller is running. Changes to `config.json` or icons alone only need an
app restart. To move to a newer upstream revision, change `STREAMDOCK_REF` in
the Dockerfile.
