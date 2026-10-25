# Scrubbing with preview in every Apple TV app: research notes

Date: 2026-10-25. Measured live on the TV Room Apple TV (tvOS, pyatv 0.18.0
over the app's own Companion pairing) with the probe tool
(`homeassistant/tools/probes/scrub/*.json`); positions from HA's
now-playing log and, for Max, the raw MRP log.

## The finding that changes everything

**The touch surface wraps below x ≈ 256.** A swipe whose finger reaches
x < 256 (pyatv coordinates, 0..1000) is read by tvOS as a huge rightward
move, so the scrub jumps forward, in every app. Above that the direction
and size are right, and the right edge is clean up to x = 950.

Apple TV app (AVKit), 300-unit left swipes in 300 ms, then OK:

| finger ends at x | result |
|---|---|
| 400 / 350 / 300 | −105 / −109 / −120 s |
| 290 / 280 / 270 | −182 / −182 / −181 s |
| 260 | **+1910 s** |
| 250 / 200 | **+1230 / +1298 s** |

300-unit right swipes ending at 800 / 850 / 900 / 950: +184 / +189 / +177
/ +183 s. A stationary touch and a vertical swipe do nothing.

The knob engine (`knob_accel.py`) started the finger at 500, dragged it
to `touch_min_x` = 150 and re-gripped in the centre. Every backward scrub
longer than ~250 units crossed the wrap, which is why backward scrubs
"ended with a huge jump forward" in Max, YouTube and the Apple TV app
alike, and why forward scrubs (finger between 500 and 850) always worked.
The two earlier fixes attacked symptoms: the lift timing (ruled out) and
the per-app `seek` override for Max and YouTube (works, but loses the
preview).

Mechanism (pyatv 0.18.0, `support/opack.py` lines 50–55): the `_hidT`
coordinates are OPACK integers. Values below 0x28 are encoded inline,
values up to 0xFF as `0x30` + one byte, larger ones as `0x31` + two
bytes little-endian. So `_cx` switches encoding exactly at 256, and tvOS
evidently reads the one-byte form as a different value (a swipe to
x = 260 lands +1910 s, to 270 lands −181 s). The threshold is empirical
and sharp. Two ways to avoid it: keep x ≥ 256 (the engine's job; also
keep y = 500), or make pyatv encode coordinates with `size_hint=2` always
(a vendored patch in the Dockerfile, like `patch_ids.py`; would also fix
pyatv's own `swipe()` for values below 256).

## Per-app behaviour with safe swipes (x kept in 300..800)

| App | Player | Touch swipe while paused | Keys (left/right) while paused | Notes |
|---|---|---|---|---|
| Apple TV app `com.apple.TVWatchList` | AVKit | correct both ways, preview thumbnails | preview, 10 s per press, **commits on OK** (nothing moves before) | the reference behaviour |
| Max `com.wbd.stream` | custom, advertises SeekToPlaybackPosition, Skip 30 s | correct both ways (77 → 26 s left; 5 → 77 s right) | 10 s per press, **moves at once**, preview frame shown; quick presses batch | HA `media_seek` lands exactly (3926 → 3575) |
| YouTube `com.google.ios.youtube` | custom, advertises everything disabled | **do not pause first**: a swipe while playing opens YouTube's scrubber (pauses, chapter strip + preview), later swipes accumulate (left, left, right, left → net −2), OK commits (75 → 43 s). After an HA pause the focus sits on the control row: swipes and presses walk the buttons, OK clicks one | keys: pause, **Up** to focus the bar, then 10 s per press with preview, OK commits (1191 → 1161 s) |
| Netflix `com.netflix.Netflix` | custom, advertises Seek, Skip 10 s; reports no position, shows `idle` when paused | safe swipes (700→400, 800→500, 500→800, short 500→400) followed with preview, user-confirmed; no numbers possible | quick presses (3 and 5) worked, user-confirmed | measured by eye only |
| Tubi `com.adrise.tubitv` | AVKit | correct both ways (−41 / −32 / +45 / −13 s), **playhead and time only, no thumbnail** | 3 / 5 quick presses −29 / −50 s, commit on OK, no thumbnail | the stream carries no trick-play images (ad-stitched); nothing on our side can add the picture |
| Pluto `tv.pluto.ios` | AVKit (confirmed 10-24) | expected as the Apple TV app | expected as the Apple TV app | not re-measured |
| Prime, Disney+, Hulu, Paramount+ | unknown | not measured | not measured | one `app_check.json` run each |

Other findings:

- A `select` sent over MRP (HA `remote.send_command`) commits a touch
  preview fine; the finger need not be down. Lift timing is irrelevant.
- HID holds through HA last 1 s (pyatv) and did nothing useful in Max or
  YouTube while paused.
- MRP `SkipBackward` did nothing in Max; `ChangePlaybackRate`/FF/RW exist
  only in AVKit players (10-24 research).
- The first-day journal note "previews follow the knob" was only ever
  verified forward.

## What "proper scrubbing with preview" needs

1. **Keep the finger out of the wrap zone.** `touch_min_x` 300 (margin
   over 256), `touch_max_x` 850 can stay (950 is fine). Start/re-grip at
   the centre of the safe range (575) so both directions get equal travel.
   This alone fixes the Apple TV app, Max, and probably Pluto/Tubi, with
   previews.
2. **YouTube: no pre-pause.** Swipe while playing; YouTube's own scrubber
   pauses and previews. A per-app flag (`pause_first: false`). The
   auto-resume's "play if paused" still applies after OK.
3. **Drop the `seek` override for Max and YouTube** once 1 and 2 are in;
   keep `scrub_apps` for apps that truly can't (none known yet).
4. **Keys as a second mechanism** (optional): pause, N × left/right, OK
   gives previews in the Apple TV app (commit on OK), Max (immediate),
   YouTube (after Up). 10 s per press, so a fast knob turn needs many
   presses (0.1–0.2 s each) or a hold; less smooth than touch. Worth
   keeping as a per-app fallback (`"scrub": "keys"`, with `focus_bar: "up"`
   for YouTube) for an app where touch fails.
5. **Measure Prime, Disney+, Hulu and Paramount+** with
   `tools/probes/scrub/app_check.json` plus safe swipes
   (`touch_safe.json`), eyes on the TV. Netflix is done (by eye).

## Probe files used

`scrub/max_swipes.json`, `scrub/max_seek.json`, `scrub/keys*.json`,
`scrub/app_check.json`, `scrub/touch_map.json`, `scrub/touch_edges.json`,
`scrub/touch_edges2.json`, `scrub/touch_safe.json`, `scrub/yt_touch*.json`.
