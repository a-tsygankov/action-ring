# Handoff: dynamic keys, YouTube like/dislike, TV toasts

Repo `a-tsygankov/action-ring`, branch **`claude/dynamic-button-countdown-9zn5qa`**
(off `main` at `59bfae4`). Not merged, no PR. **Nothing is deployed to the Pi.**

| Commit | Pushed | What |
|---|---|---|
| `762031c` | yes | Dynamic keys (a key takes turns between functions, with a warning) |
| `468ebf4` | yes | YouTube page: key 2 next ⇄ previous, key 3 like ⇄ dislike (YouTube Data API) |
| `ce99530` | yes | TV toasts (webOS createToast on the LG): rating, soccer, ad, sign-in |
| `2bf741a` | yes | Docs: key screens are 64×64, Google console values, no text on tvOS |
| `2427e98` | **no, local only** | `streaming_pause_mute` automation toasts "Paused: sound muted" / "Sound on" (Tubi). The user interrupted the push: ask before pushing |

App version in `homeassistant/streamdock_n3/config.yaml`: `0.5.0-27`.

## What the user asked for, in order

1. **Dynamic button:** a key that switches image and function on a timer
   (e.g. 5 s thumb up / 5 s thumb down), with a 1 s warning before each
   switch (3-2-1 countdown, shrinking bar, or disappearing dots). A context
   tab may hold several.
2. Thumbs up/down for YouTube via the **YouTube Data API** (chosen over
   clicking through the on-screen controls). Previous/next video made
   dynamic too; like/dislike on the freed key.
3. Google Cloud OAuth app setup (branding etc.). This was done by hand: no
   session here can drive the user's browser. Values are in DOCS.md
   "Like / dislike".
4. **TV toasts** for everything: the Apple TV can't show outside text; the
   LG can (webOS toast over any input).
5. "Is it deployed? No toast after like in YouTube or ad muted on Tubi":
   it is **not deployed** (see below). Tubi is muted by the HA automation,
   not by ad detection, hence commit `2427e98`.

## Design (where to look)

All under `homeassistant/streamdock_n3/`:

- `dynamic.py`: pure timing. `"dynamic": {"phases": [...], "hold_s": 5,
  "warn_s": 1, "warn": "countdown"|"bar"|"dots", "warn_color", "grace_s": 0.25}`.
  Each phase is laid over the key's own spec. The clock runs from when the
  page was shown (`TabEngine.shown_at` / `page()` in `tabs.py`). A press
  resolves to the phase the renderer last **drew** (`_on_screen` in
  `ha_daemon.py`), or the previous one within `grace_s` of a swap.
  `draw_warning()` paints the overlay; on a key with a label band the bar sits
  above the band.
- `ha_daemon.py`: the renderer sleeps until the next dynamic frame
  (`_render_pass` returns a due time) and frame-only passes don't log.
  `Look.warn` (`keyspec.py`) carries the overlay. Actions
  `{"youtube": "like"|"dislike"}` and `{"toast": "text"}` (label placeholders
  via `keyspec.format_label`). Conditions `youtube_liked` / `youtube_disliked`.
- `yt_rating.py`: Data API v3, `videos.getRating` lazily per video (60 s
  back-off on failure), `videos.rate` on a press. The change shows on the key
  at once and is undone if Google refuses. Like on a liked video clears it.
  The video id comes from `YouTubeScreens.video_id()` (`yt_lounge.py`).
  Sign-in uses Google's **device flow** (scope `.../auth/youtube`):
  - The client comes from `/config/yt_oauth_client.json`.
  - The trigger file is `/config/yt_rate_auth`.
  - Only the refresh token is kept, in `/config/yt_oauth.json` (mode 0600).
- `toast.py`: `Toaster`; config `"toast": {"lg", "events": [rating, soccer,
  ad, sign_in], "settle_s": 0.6}`. Messages on a channel (soccer picks) are
  merged so only the last of a run of presses shows. Nothing is sent while the
  TV is off. Hooks:
  - `YouTubeRating(notify=, notify_sign_in=)`: the sign-in code is repeated
    every 60 s.
  - `LiveSoccer(notify=)`.
  - `AdDetector.notify`: only when ad detection itself mutes or unmutes.
- `presets/tv_room.json`:
  - `context:youtube` keys 2 and 3 are dynamic.
  - `"youtube": {"rating": true}`.
  - New `"toast"` section.
- `../pair.sh youtube-rating <client.json>`: copies the client, starts the
  sign-in, prints the code. `status` shows whether it's signed in.
- `../ha-config/automations/streaming_pause_mute.yaml` (commit `2427e98`):
  toasts with `continue_on_error`.
- Icons `icons/thumb_{up,down}__{off,on}.png` plus `manifest.json` entries;
  `tools/icons/build_icons.py` has the builders and `RATED = #1971c2`.

Docs: `homeassistant/streamdock_n3/DOCS.md` sections "Dynamic keys",
"Like / dislike", "TV toasts", "Key images"; `docs/JOURNAL.md` has entries
dated 2026-11-13.

## To deploy (from the user's PC; no session here can reach the Pi)

```bash
git fetch origin && git checkout claude/dynamic-button-countdown-9zn5qa && git pull
bash homeassistant/restore.sh --replace-ha-config arpi   # arpi = the live HA (journal)
```

- `--replace-ha-config` is needed for the updated `streaming_pause_mute`. A
  plain restore keeps the Pi's older copy. The flag also overwrites any other
  script or automation edited in HA's UI (backups are kept).
- New modules are in the Dockerfile COPY line, so restore rebuilds the app.

Then the like/dislike sign-in:
1. Finish the Google Cloud setup (DOCS.md "Like / dislike": publish the app,
   no logo, no scopes, client type "TVs and Limited Input devices").
2. Run `bash homeassistant/pair.sh youtube-rating ~/Downloads/client_secret_….json`.
3. Enter the printed code at google.com/device.

## Untested / open

- **Nothing has run on the dock, the TV or against Google.** The build sandbox
  had no network to Google or the Pi.
- **Device-flow scope:** it is assumed Google allows the `youtube` scope for
  the device flow. Google's YouTube Data API device guide implies it, but the
  allowed-scopes page (developers.google.com) was blocked here. If Google
  refuses it, the sign-in log and toast say so. The fallback is a one-time
  loopback/browser sign-in from the PC that writes `yt_oauth.json`.
- **Toasts:** not yet seen over the Apple TV HDMI input (expected to work) or
  over the PS5 in Game mode. Quick check:
  `ha-call webostv.command '{"entity_id":"media_player.lg_tv_2","command":"system.notifications/createToast","payload":{"message":"Test"}}'`
- **Likes are only toasted for presses on the dock** (key 3 of the YouTube
  page), not for likes made with the Apple TV remote inside the YouTube app.
- Tubi toasts come from the pause-mute automation, so a user's own pause also
  shows "Paused: sound muted": the automation can't tell an ad from a pause.
- **Suggested, not done:** patch the SDK's JPEG save to `quality=95,
  subsampling=0` (via `patch_ids.py`). Key images are 64×64 JPEG q75 4:2:0 on
  the device, and that save is the bigger blur. Drawing icons at 64×64
  natively would come after.
- The dynamic-key dots warning is ~8 px on the device; check how readable it
  is if it gets used (the YouTube keys use the countdown).

## Working notes

- Tests:
  `cd homeassistant/streamdock_n3 && pip install pytest pyytlounge==3.5.0 && python -m pytest -q tests`.
  Result: 290 passed, 1 failed. The failure,
  `test_ad_detect_episodes.py::test_a_pre_roll_clock_in_the_set_state_queue_is_seen`,
  happens without these changes too (it needs `pyatv`).
- `ha_daemon.py` imports the device SDK and websockets. `tests/test_dynamic_render.py`
  stubs both to drive the renderer and presses with a fake clock.
- The Iconify API was blocked in the sandbox. The thumb SVGs came from the
  npm package `@iconify-json/material-symbols`, written into
  `tools/icons/out/cache/` (gitignored). Rebuilding `next_video` that way
  matched the committed PNG pixel for pixel.
- Upstream SDK facts came from cloning `asad-albadi/streamdock-n3` at the
  pinned ref (`StreamDockN3.key_image_format()` → 64×64).
- User preference: replies concise; primarily Java/TypeScript (this repo is
  Python).
