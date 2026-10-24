# Ad detection: guide for AI agents

How this system finds ad breaks in TV-room streaming apps and mutes the Sonos
Arc during them. Use it to analyze a new app and add or adjust its rule. Read
[JOURNAL.md](JOURNAL.md) for the full system; this guide covers ad handling
only.

## 1. What exists today

| Piece | Where | Handles |
|---|---|---|
| HA automation `streaming_pause_mute` | `homeassistant/ha-config/automations/streaming_pause_mute.yaml` | Apps whose ads (or pauses) show up in HA's Apple TV media player: mute while `paused` (or `idle` for apps in `idle_pause_apps`), unmute on resume. Apps in `excluded_apps` are skipped. |
| App ad detector | `homeassistant/streamdock_n3/ad_detect.py`, config `ad_detect` | Apps whose ads are only visible in the Apple TV's raw media-remote (MRP) data. Currently the "show clock" pattern (Pluto TV). |
| YouTube session | `homeassistant/streamdock_n3/yt_lounge.py` | YouTube on the Apple TV or LG: explicit ad start/end events (logged; `skip_ad` available). |
| Now-playing log | `np_log.py` → `/app_configs/local_streamdock_n3/now_playing.log` | Research: what HA sees. |
| Raw MRP log | `mrp_raw.py` → `/app_configs/local_streamdock_n3/mrp_raw.log` | Research: everything the Apple TV publishes. |
| Tools | `homeassistant/tools/` | `mrp_summary.py`, `replay_ad_detect.py`, `sanitize_mrp.py`, `fixtures/` |

**Mute rules (every implementation must keep them):**
- Mute only while the Arc plays TV audio (`media_player.tv_room_tv_room`,
  attribute `source` = `TV`); never touch music on the Arc.
- Never mute if the Arc is already muted. Only undo your own mute, and skip
  the unmute if the user already unmuted.
- Always have an end condition that can't get stuck: a timeout, an app
  change, or both. A missed end must not leave the room muted.
- Key 3 on the dock shows "TV / MUTED" whenever the Arc is muted, which is
  the user's visual check.

## 2. Data sources, cheapest first

### A. Home Assistant's Apple TV media player (`media_player.vugitv_4k_vugitv_4k`)

Summary state only: `state` (playing / paused / idle / …), `app_id`,
`media_title`, `media_duration`, `media_position` (+ `_updated_at`),
`media_content_id`, `supported_features`. Position updates only on events,
not continuously.

The app logs every change to `now_playing.log`, one line per change, with
`pos_jump=<expected>-><reported>` when the position doesn't match continuous
playback:

```bash
ssh homeassistant 'grep " 23:5[0-9]" /app_configs/local_streamdock_n3/now_playing.log'
```

HA's history (recorder DB) also holds states. Install `sqlite` in the SSH
app (temporary) and open the DB read-only:

```bash
ssh homeassistant 'apk add -q sqlite; sqlite3 -readonly "file:/homeassistant/home-assistant_v2.db?mode=ro"' <<'EOF'
SELECT datetime(s.last_updated_ts,'unixepoch','localtime'), s.state,
       json_extract(sa.shared_attrs,'$.is_volume_muted')
FROM states s JOIN states_meta m ON s.metadata_id=m.metadata_id
LEFT JOIN state_attributes sa ON s.attributes_id=sa.attributes_id
WHERE m.entity_id='media_player.tv_room_tv_room' AND s.last_updated_ts > strftime('%s','now') - 3600
ORDER BY s.last_updated_ts;
EOF
```

### B. Raw MRP from the Apple TV (`mrp_raw.log`)

Needs the app's AirPlay pairing (`bash homeassistant/pair.sh atv airplay`; PIN
on the TV) and `"mrp_raw_log": true` in the `atv` config section. One JSON
line per message:

- `msg`: `set_state` (player state, supported commands, playback queue),
  `update_content_item` (metadata changes), `now_playing_client`,
  `now_playing_player`.
- `playerPath.client.bundleIdentifier`: the app (e.g. `tv.pluto.ios`).
- `playerPath.player.identifier`: **which player**. Apps can run several:
  - `MediaRemote-DefaultPlayer` is the app's own now-playing publisher. For
    Pluto it's a show clock that excludes ads.
  - `avkit-<uuid>` are AVKit video players (the actual stream). Pluto used up
    to 5.
- `playbackState` (`Playing`, `Paused`, …), `supportedCommands` (a republish
  is itself a signal), `contentItems[].metadata` (`title`, `duration`,
  `elapsedTime`, `elapsedTimeTimestamp`, `playbackRate`, and `*Data` blobs
  decoded from NSKeyedArchiver plists, e.g. `nowPlayingInfoData`).

MRP metadata has no "is advertisement" field (checked against pyatv 0.18's
`ContentItemMetadata.proto`, 104 fields). Signals come from behaviour: which
player publishes, clocks that stop or jump, and command republishes.

Summarize a capture (classifies each show-clock update as ADVANCING, FROZEN or
JUMP):

```bash
ssh homeassistant 'cat /app_configs/local_streamdock_n3/mrp_raw.log' > /tmp/capture.jsonl
python homeassistant/tools/mrp_summary.py /tmp/capture.jsonl --from 23:55:00 --app tv.pluto.ios
```

### C. YouTube's TV-link protocol (YouTube only)

`yt_lounge.py` receives `ad_playing_changed` / `ad_state_changed` events with
duration and skippability. The app log shows `youtube: <screen>: ad started (…)`.
`api.skip_ad()` exists. This is the only source with explicit ad flags.

## 3. Known app behaviour

| App (bundle) | Ad start | During ad | Ad end | Handling | Evidence |
|---|---|---|---|---|---|
| Tubi (`com.adrise.tubitv`) | HA: `paused` | HA unchanged (still paused); playback locked | HA: `playing` + forward `pos_jump` = pod length, then a jump back; sometimes **never reported** | automation (5-min cap covers the missing end) | 2 captures, confirmed on the TV |
| Pluto TV (`tv.pluto.ios`) | HA: nothing. MRP: show-clock (`MediaRemote-DefaultPlayer`) `update_content_item` while the AVKit stream plays | show clock republished every 30 s (15 s near the end), frozen at the cue point; heartbeat can skip one beat | MRP: AVKit player republishes `supportedCommands`; HA then reports a **false `paused`** | `ad_detect`; excluded from the automation | 3 breaks; fixture `tools/fixtures/pluto_ads_20261023.jsonl` |
| Netflix (`com.netflix.Netflix`) | n/a (pause = `idle`) | n/a | n/a | automation with `idle_pause_apps` | confirmed on the TV |
| YouTube (`com.google.ios.youtube`, LG) | Lounge `ad_playing_changed` | Lounge state `Advertisement` | Lounge state change | logged only (no mute yet) | events in the app log |
| Apple TV app (`com.apple.TVWatchList`) | not analyzed | | | automation pause-mute applies | |

Pluto's user actions (pause, play, scrub) also produce show-clock updates,
but always together with an AVKit `Paused`/`Playing` change within a few
seconds. That's what `stream_quiet` uses to tell them from ads.

## 4. Procedure: analyze a new app

1. **Prepare.** Make sure the app runs on the Apple TV, `now_playing.log` and
   `mrp_raw.log` are being written, and `pair.sh status` shows AirPlay paired.
   Rotate old logs if they're large.
2. **Capture with the user.** Ask them to watch the app until an ad break and
   report start and end times (clock time). Also ask for controls in the same
   session: a normal pause/resume, a scrub, and switching the app away and
   back. Without controls, a user action can look like an ad.
3. **Pull and summarize.** Start with `now_playing.log`; if HA shows nothing
   distinctive, go to `mrp_raw.log` via `mrp_summary.py`. Look at all
   players of the app, not just the one HA picks.
4. **Find a signal** that is present at the ad start (within seconds), absent
   during content and user actions, and has a clear end. Compare:
   - which player publishes, and how often (silence vs. heartbeat);
   - clocks: does `elapsedTime` advance with `elapsedTimeTimestamp`, freeze,
     or jump?
   - `playbackState` per player, `supportedCommands` republishes, `duration`
     changes (stitched ads change the stream duration);
   - decoded `nowPlayingInfoData` keys that differ during ads.
5. **Check every candidate** against all captured ads and all controls.
   Count misses and false hits, and look at the start latency.
6. **Implement** (section 5), then **replay** the capture with the expected
   windows. Add a sanitized fixture (`sanitize_mrp.py`; it strips titles,
   content/profile IDs and device IDs; the repo is public) and keep the
   `--expect` command in the PR.
7. **Deploy** (`bash homeassistant/restore.sh`, plus `--replace-ha-config`
   for automation changes), then **confirm live** on the next ad. Detector
   lines carry a timestamp (`ad-detect: 00:09:21 ad started (…)`).
8. **Document**: update the table in section 3 and add a JOURNAL entry with
   the evidence (times, values).

## 5. Where a rule goes

- **Visible in HA state** (like Tubi or Netflix): extend
  `streaming_pause_mute.yaml`, e.g. add to `idle_pause_apps` or
  `excluded_apps`, or write a new automation with the same mute rules.
  Deploy with `restore.sh --replace-ha-config`.
- **Same show-clock pattern as Pluto**: add the bundle to `ad_detect.apps`
  in `config.tv_room.json` and to the automation's `excluded_apps`. Replay
  first; the thresholds (`stream_quiet`, `heartbeat_timeout`) may need
  per-app values.
- **A different MRP pattern**: add a detector next to `AdDetector` in
  `ad_detect.py`. Feed it from `install()`, which receives every MRP
  `set_state` / `update_content_item` with the bundle and player id. Reuse
  `_start` / `_end` / `_mute` so the mute rules stay identical. Make it
  replayable: give the event handlers an injectable `now`, and extend
  `tools/replay_ad_detect.py`.
- **YouTube**: wire `yt_lounge.Screen.in_ad` (already tracked) to the same
  mute helper, or call `skip_ad` when `is_skippable`.

## 6. Pitfalls seen so far

- HA's state for an app can be stale or wrong (Tubi stuck on `paused` after
  a break; Pluto's false `paused` at the end). Always have a timeout or a
  second signal for the end.
- A "jump" in a clock isn't required: after a quiet stretch, the cue point
  can equal continuous playback. Pluto's second break was found only
  because the publication itself was the signal.
- Heartbeats skip beats (Pluto: 61 s gap with a 30 s period). Keep timeouts
  at 3× the period.
- `ha apps logs` returns about the last 100 lines by default; use
  `--lines 1000`.
- The app config, credentials (`atv_companion.json`) and YouTube tokens live
  only on the Pi. Never commit them; never commit unsanitized captures.
- Editing an automation in HA's UI rewrites the YAML; `restore.sh` then
  reports "differs" (kept) until run with `--replace-ha-config`.

## 7. Candidates not analyzed yet

Peacock, Hulu (ad tier), Prime Video (ads), Max, Paramount+, The Roku
Channel, Freevee, Plex free streams, Netflix with ads, and YouTube on the
LG (link with `pair.sh youtube lg`). For each, follow section 4 and record
the result in section 3.
