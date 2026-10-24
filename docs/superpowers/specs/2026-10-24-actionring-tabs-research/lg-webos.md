# LG OLED65C4 (webOS 24) control surface via HA `webostv`: research notes

Date: 2026-10-24. Target: HA 2026.10, core `webostv`, entity `media_player.lg_tv_2`.
Scope: what a Stream Dock "context tab" can actually drive per foreground app.
Method: read current source (HA core `dev`, aiowebostv `main` = v0.10.x, bscpylgtv `master`,
webOS OSE uMediaServer), GitHub issues/PRs from 2026, community threads. Nothing was
tested on the TV. Items marked **LIVE TEST** need a quick check on the C4.

---

## 0. Things that changed in 2026 and matter here

- **aiowebostv 0.9.2 (2026-08-21) dropped the signed registration manifest** to fix webOS 26
  pairing ("403 Pairing rejected: blacklisted certificate detected"). HA 2026.8.3+ ships it;
  HA 2026.9 ships 0.10.0. Source: `aiowebostv/handshake.py` (unsigned permission list, includes
  `WRITE_NOTIFICATION_ALERT`, `CONTROL_INPUT_MEDIA_PLAYBACK`, `READ_RUNNING_APPS`).
  - https://github.com/home-assistant-libs/aiowebostv/blob/main/aiowebostv/handshake.py
  - https://github.com/home-assistant-libs/aiowebostv/releases (v0.9.2 "Fix webOS 26 pairing handshake")
  - https://github.com/home-assistant/core/issues/179669
- Consequence: on non-webOS-26 TVs the connection loses the signed tier, notably
  `WRITE_SETTINGS` (`settings/setSystemSettings` -> `401 insufficient permissions`). Reported on a
  **G4 (2024, firmware 33.31.68)** - same generation as the C4. Reads still work.
  - https://github.com/home-assistant-libs/aiowebostv/issues/728 (closed "not planned")
  - https://github.com/home-assistant/core/issues/180025 (open; C3, CX, C5, G5 reports)
- **The luna-via-alert hack still works with the unsigned manifest** and is now the community
  workaround in HA (see section 2.4). Confirmed on G4/webOS 24-ish firmware 33.31.68 (#728),
  C3/CX/C5 (#180025), C6/G6/G5 webOS 26 firmware 43.21.6x/7x (bscpylgtv PR #9 thread, zedr32 README).

---

## 1. HA `webostv` actions (2026) - exact behaviour from source

Source files (HA core `dev`):
- `homeassistant/components/webostv/services.py` - https://github.com/home-assistant/core/blob/dev/homeassistant/components/webostv/services.py
- `homeassistant/components/webostv/media_player.py` - https://github.com/home-assistant/core/blob/dev/homeassistant/components/webostv/media_player.py
- `homeassistant/components/webostv/services.yaml`
- aiowebostv `webos_client.py`, `buttons.py`, `endpoints.py` - https://github.com/home-assistant-libs/aiowebostv/tree/main/aiowebostv

### 1.1 Response support (services.py)

| Action | Schema | `supports_response` | What comes back |
|---|---|---|---|
| `webostv.button` | `button: cv.string` | `SupportsResponse.NONE` | nothing (fire-and-forget over the input websocket, no ack) |
| `webostv.command` | `command: cv.string`, `payload: dict` (optional) | `SupportsResponse.OPTIONAL` | the TV's SSAP response `payload` dict |
| `webostv.select_sound_output` | `sound_output: cv.string` | `SupportsResponse.OPTIONAL` | TV payload of `com.webos.service.apiadapter/audio/changeSoundOutput` |
| `media_player.select_source`, `play_media`, `media_play/pause/stop/next/previous`, volume | std | none | nothing (but they `await` the TV reply; errors raise) |

- Services are registered with `async_register_platform_entity_service`, so the response is keyed by
  entity id: `resp["media_player.lg_tv_2"]` (confirmed by the community YAML
  `'{{ alert["media_player.lg_smart_tv"].alertId }}'` in core#180025).
- `async_command` = `return await self._client.request(command, payload=...)`.
  `request()` waits for the reply (timeout `REQUEST_TIMEOUT`), then `_parse_response` raises on
  `type: error`, on `404 no such service or method` (`WebOsTvServiceNotFoundError`) and on
  `returnValue: false`. So a bad URI surfaces as an HA error, which is useful for probing.
- `command()` always builds `"uri": f"ssap://{uri}"` (webos_client.py ~line 615). You cannot pass a
  `luna://` URI directly; luna only via the alert hack. Only `request` type, no subscriptions.

### 1.2 `webostv.button` - names

`async_button` -> `client.button(name)` -> sends `type:button\nname:{name}\n\n` on the pointer input
socket. **HA does not validate the name** (`cv.string`), so any key name the TV understands works;
unknown names are silently ignored.

aiowebostv `buttons.py` (known list): `LEFT RIGHT UP DOWN RED GREEN YELLOW BLUE CHANNELUP CHANNELDOWN
VOLUMEUP VOLUMEDOWN PLAY PAUSE STOP REWIND FASTFORWARD ASTERISK BACK EXIT ENTER AMAZON NETFLIX 3D_MODE
AD (audio description) ASPECT_RATIO CC (closed captions) DASH GUIDE HOME INFO INPUT_HUB LIST LIVE_ZOOM
MAGNIFIER_ZOOM MENU MUTE MYAPPS POWER PROGRAM QMENU RECENT RECORD SAP (multi audio) SCREEN_REMOTE
TELETEXT TEXTOPTION 0-9`.
https://github.com/home-assistant-libs/aiowebostv/blob/main/aiowebostv/buttons.py

bscpylgtv adds (all sendable through HA since names aren't validated): `ADVANCE_SETTING ALEXA EMANUAL
EZPIC EZ_ADJUST EYE_Q HCEC IN_START IVI RECLIST SEARCH SOCCER TIMER TV TWIN UPDOWN USP YANDEX`.
Avoid `EZ_ADJUST`/`IN_START` (service menus).
https://github.com/chros73/bscpylgtv/blob/master/bscpylgtv/buttons.py

### 1.3 Media transport in HA

- `media_player.media_play` / `media_pause` / `media_stop` -> `ssap://media.controls/play|pause|stop`.
- `media_player.media_next_track` -> `media.controls/fastForward` (or `tv/channelUp` on Live TV);
  `media_previous_track` -> `media.controls/rewind` (or `tv/channelDown`).
- `media_play_pause` toggles on an internal `_paused` flag (not TV state) - prefer explicit
  play/pause chosen from the entity state.
- Entity state: HA subscribes to `ssap://com.webos.media/getForegroundAppInfo` and maps
  `playState` playing/paused/unloaded -> playing/paused/idle (media_player.py ~line 173). The
  `mediaId` is **not** exposed as an attribute; poll it with `webostv.command`.

### 1.4 `select_source` / `play_media`

- `select_source(source)`: looks up the title in `source_list`; app -> `system.launcher/launch {"id"}`,
  input -> `tv/switchInput`. Fails with "Device is off" when off (already in JOURNAL).
- `play_media` **only handles `media_content_type: channel`** (number / exact / substring name). There is
  no app-id play_media in core. For app ids use:
  ```yaml
  action: webostv.command
  target: {entity_id: media_player.lg_tv_2}
  data:
    command: system.launcher/launch
    payload: {id: netflix}          # optional: contentId / params
  ```
  (`launch_app_with_content_id` / `launch_app_with_params` in aiowebostv show the payload shapes.)

### 1.5 `select_sound_output`
`com.webos.service.apiadapter/audio/changeSoundOutput {"output": ...}`; values seen:
`tv_speaker`, `external_arc`, `external_optical`, `bt_soundbar`, `tv_external_speaker`, `headphone`,
`tv_speaker_headphone`, `external_speaker`. Current value is in the entity attribute `sound_output`.

---

## 2. LG built-in Media Player (USB/DLNA) - subtitles, audio track, speed

### 2.1 App id
- `com.webos.app.mediadiscovery` is what `getForegroundAppInfo` reports while the built-in Media
  Player plays (webOS 7 / G2 evidence in aiowebostv PR #178:
  `[{'appId': 'com.webos.app.mediadiscovery', 'playState': 'playing', 'type': 'media', 'mediaId': '_', 'windowId': '_Window_Id_3'}]`).
  https://github.com/home-assistant-libs/aiowebostv/pull/178
- `com.webos.app.photovideo` / `com.webos.app.music` are older (webOS 3-5 era) ids, still in lists
  like https://github.com/WesSouza/lgtv-ip-control/blob/main/packages/lgtv-ip-control/src/constants/TV.ts
- **LIVE TEST**: play a USB file, read `app_id` on the entity and the `com.webos.media/getForegroundAppInfo`
  response. Launch it with `system.launcher/launch {"id":"com.webos.app.mediadiscovery"}`.

### 2.2 Public SSAP (no hack, works today)
| Need | Call | Notes |
|---|---|---|
| play | `media.controls/play` (or button `PLAY`) | HA `media_play` |
| pause | `media.controls/pause` (or button `PAUSE`) | HA `media_pause` |
| stop | `media.controls/stop` | |
| FF / REW | `media.controls/fastForward`, `media.controls/rewind`, buttons `FASTFORWARD`/`REWIND` | speed-step behaviour in LG player undocumented -> LIVE TEST |
| close player | `media.viewer/close` | |
| play state + mediaId | `com.webos.media/getForegroundAppInfo` | returns `foregroundAppInfo[]` with `appId, playState, type, mediaId, windowId` |
| system CC setting (read) | `settings/getSystemSettings {"category":"caption","keys":["captionEnable"]}` | READ_SETTINGS survives unsigned manifest |

There is **no public SSAP for subtitle on/off, subtitle track, audio track, seek-to-position or play rate.**
`ssap://com.webos.media/<anything but getForegroundAppInfo>` is not in any client's endpoint list;
expect `404 no such service or method` -> **LIVE TEST** (cheap, error is surfaced by HA).

### 2.3 Luna `com.webos.media` methods that would do it (LG-TV uMediaServer)
Documented by the community (not in OSE docs except `setPlayRate`), all keyed by `mediaId`:
- `setSubtitleEnable {"mediaId":"_x","enable":true|false}`
- `selectTrack {"mediaId":"_x","type":"audio"|"text","index":n}` (webOS 4+: default-flagged audio track is index 0)
- `setPlayRate {"mediaId":"_x","playRate":0.1..2.0,"audioOutput":true}` (OSE doc example uses 2.0)
- `seek {"mediaId":"_x","position":<ms>}` (OSE API group lists `com.webos.media/seek`)
- `subscribe {"mediaId":"_x","subscribe":true}` -> `sourceInfo` with track lists (needs a reply channel; not available via the hack)
- Sources: https://gist.github.com/aabytt/bddbb1bcf031a050d89a89aeee3a6737 ,
  https://www.webosose.org/docs/reference/ls2-api/com-webos-media/ ,
  https://github.com/webosose/umediaserver/blob/master/files/sysbus/com.webos.media.api.json ,
  real-world use inside apps: https://github.com/Viren070/AIOStreams/pull/1586 (500 ms delay after
  `setSubtitleEnable` before `selectTrack`), Stremio WebOsVideo (C8, webOS 4).

Blockers for driving another app's player from outside:
1. **mediaId availability**: for `com.webos.app.mediadiscovery` the reported `mediaId` was `'_'`
   (PR #178, webOS 7) - i.e. effectively hidden, while Netflix/Amazon expose real ids
   (`'_LWZpv6U9CkVIAz'`). If the C4 also returns `_`, luna track control of the LG player is a
   non-starter. `getActivePipelines` would list ids but the alert hack cannot return data.
2. **Managed vs unmanaged pipelines**: `setPlayRate` etc. reportedly only act on *managed*
   pipelines (created via `com.webos.media/load`); app-internal pipelines may ignore them.
   https://github.com/webosbrew/youtube-webos/issues/6 (DrA1ex, Aug 2024)
3. **Caller identity / ACG**: through the alert hack the call is made by the notification
   service, not by the media app. Works for `com.webos.settingsservice`; unknown for
   `com.webos.media` on webOS 24. -> **LIVE TEST**.
4. **bscpylgtv has no `set_current_subtitle`, `get_audio_tracks`, `select_audio_track` or similar.**
   Its `luna_request` is used only for settings/picture/eim/oledepl/config/tvpower endpoints.
   Its docstring: "An important limitation is that any returned data is not accessible."
   https://github.com/chros73/bscpylgtv/blob/master/bscpylgtv/webos_client.py (luna_request ~line 1300)

### 2.4 The luna-via-alert hack, HA form (no root, no dev mode)
bscpylgtv shape: `createAlert` with `buttons[{label:"", onClick: lunauri, params}]`, `onclose: {uri, params}`,
`onfail: {uri, params}`, then `closeAlert {alertId}` -> the TV runs `onclose`. HA community version
(core#180025, sfortis), adapted:
```yaml
- action: webostv.command
  target: {entity_id: media_player.lg_tv_2}
  data:
    command: system.notifications/createAlert
    payload:
      message: " "
      buttons: [{label: "", onClick: "luna://com.webos.media/setSubtitleEnable",
                 params: {mediaId: "{{ mid }}", enable: false}}]
      onclose: {uri: "luna://com.webos.media/setSubtitleEnable",
                params: {mediaId: "{{ mid }}", enable: false}}
      onfail:  {uri: "luna://com.webos.media/setSubtitleEnable",
                params: {mediaId: "{{ mid }}", enable: false}}
  response_variable: alert
- action: webostv.command
  target: {entity_id: media_player.lg_tv_2}
  data:
    command: system.notifications/closeAlert
    payload: {alertId: "{{ alert['media_player.lg_tv_2'].alertId }}"}
```
where `mid` comes from a prior
`webostv.command command: com.webos.media/getForegroundAppInfo` ->
`resp['media_player.lg_tv_2'].foregroundAppInfo[0].mediaId`.
- Fire-and-forget: success of the luna call is invisible; verify by eye or by state change.
- If `closeAlert` ever hangs (bscpylgtv PR #9, webOS 26, cleared by reboot), fallback is button `ENTER`
  while the alert is up. https://github.com/chros73/bscpylgtv/pull/9
- Known-good luna targets via this path: `com.webos.settingsservice/setSystemSettings`
  (e.g. `{"category":"caption","settings":{"captionEnable":"on"}}` - system CC, mainly broadcast;
  whether Media Player honours it = LIVE TEST). https://github.com/zedr32/lg_webos_brightness_adjustment
- LG may patch `createAlert` in future firmware (both READMEs warn).

### 2.5 Verdict for the LG Media Player
| Control | Verdict |
|---|---|
| play / pause / stop | **Feasible** (`media.controls/*` or HA media_play/pause) |
| FF / REW | **Feasible** (buttons or `media.controls/fastForward|rewind`); step behaviour LIVE TEST |
| subtitle on/off | **Hack, uncertain** (`setSubtitleEnable` via alert; needs real mediaId) else button navigation |
| cycle subtitle track | **Hack, uncertain** (`selectTrack type:text`; no way to read track count -> cycle blind) |
| read audio tracks | **Infeasible** without root (needs luna reply / `subscribe`) |
| cycle audio track | **Hack, uncertain** (`selectTrack type:audio index:n`, blind) or `SAP` button (LIVE TEST) |
| seek to position / play rate | **Hack, uncertain** (`seek`, `setPlayRate`; managed-pipeline caveat) |

---

## 3. Remote buttons alone

- `CC` and `SAP` exist in the key map. On US remotes `CC` toggles captions and `SAP` opens
  multi-audio for broadcast; whether the Media Player reacts is undocumented -> **LIVE TEST**
  (press `CC`, `SAP`, `TEXTOPTION`, `INFO` during USB playback and note what pops up).
- Menu navigation sequences (ENTER to show the OSD, then arrows to the gear/"Subtitles"/"Audio")
  are possible but layout-dependent and stateful (OSD auto-hides, focus position varies) - brittle;
  only use if CC/SAP do nothing. Record exact sequences on the C4 if chosen.
- FF/REW speed stepping: no authoritative doc found. One openHAB user: "REWIND is a command that
  will toggle rewinding with speeds" (https://community.openhab.org/t/lg-webos-binding-fast-forward/156787).
  Third-party apps instead do 10 s skips (Tablo, Jellyfin). Expect LG player: repeated `FASTFORWARD`
  steps through x2/x4/x8/x16..., `PLAY` returns to x1 -> **LIVE TEST**. In third-party apps
  (Netflix/YouTube/Prime) `media.controls/fastForward` behaviour is app-defined (often a skip or nothing).

---

## 4. App ids (launch via `system.launcher/launch {"id": ...}`)

| App | id | evidence |
|---|---|---|
| Netflix | `netflix` | aiowebostv PR #178 output, many lists |
| YouTube | `youtube.leanback.v4` | already observed on this TV (JOURNAL.md) |
| Prime Video | `amazon` | PR #178 output, lgtv-ip-control |
| Apple TV (app) | `com.apple.appletv` | free-my-tv/web/tv.js list; on this TV its title is "Apple TV" (input for the box is "Apple OTT") |
| HBO Max | `com.wbd.stream` (current) or legacy `com.hbo.hbomax` | free-my-tv lists both; lgtv-ip-control has legacy |
| Hulu | `hulu` | lgtv-ip-control |
| Disney+ | `com.disney.disneyplus-prod` | lgtv-ip-control, lovelace-touchpad-card |
| Paramount+ | `com.cbs-all-access.webapp.prod` | klattimer/LGWebOSRemote test/rc |
| LG Media Player | `com.webos.app.mediadiscovery` | PR #178 (webOS 7); verify on webOS 24 |

Authoritative check on the TV: `webostv.command com.webos.applicationManager/listLaunchPoints`
(returns `launchPoints[].id/title`), or read `source_list` + `app_id` on the entity.
Links: https://github.com/WesSouza/lgtv-ip-control/blob/main/packages/lgtv-ip-control/src/constants/TV.ts ,
https://github.com/simbasjungles/free-my-tv/blob/aa34335d25fa2224053d1bb16851e4522a6bbeb0/web/tv.js ,
https://github.com/klattimer/LGWebOSRemote/blob/master/test/rc

---

## 5. Power on

- webOS cannot turn itself on over SSAP: `system/turnOn` is listed under "endpoints below were removed
  at some point" in bscpylgtv; HA's `turn_on` just runs the `webostv.turn_on` device trigger
  (`coordinator.turn_on.async_run`), and the TURN_ON feature only appears if such an automation exists.
  https://www.home-assistant.io/triggers/webostv.turn_on/
- WoL: HA docs recommend `wake_on_lan.send_magic_packet` from that trigger; needs "Turn on via Wi-Fi"
  / LG Connect Apps / Mobile TV On enabled (path for 2017+ models: Settings > General > Mobile TV On >
  Turn On Via WiFi; 2025+: Support > IP control settings > Wake on LAN). Works best wired, same L2 segment.
  No C4-specific confirmation found; generally works on 2017+ LG. https://www.home-assistant.io/integrations/webostv/
- This setup already powers on via the Apple TV (CEC), confirmed working (JOURNAL 2026-10-23). WoL is an
  optional, faster/independent alternative - LIVE TEST if wanted.

---

## 6. Live test checklist (in order, all via HA Developer Tools > Actions)

1. `webostv.command` `com.webos.service.update/getCurrentSWInformation` (may 401 on unsigned) or
   `system/getSystemInfo` - confirm webOS version (C4 may have been upgraded to webOS 25 via Re:New).
2. Play a USB/DLNA file; `webostv.command` `com.webos.media/getForegroundAppInfo` -> note `appId` and
   whether `mediaId` is a real id or `_`. Repeat in Netflix / YouTube / Prime.
3. Try `webostv.command` `com.webos.media/setSubtitleEnable` directly (expect 404).
4. If mediaId is real: alert-hack `setSubtitleEnable false/true`, then `selectTrack text 0/1`,
   `selectTrack audio 0/1`, `setPlayRate 1.5`, `seek`. Watch the screen.
5. Buttons during USB playback: `CC`, `SAP`, `TEXTOPTION`, `INFO`, `FASTFORWARD` x3 then `PLAY`,
   `REWIND` x2 then `PLAY`, `media.controls/fastForward`.
6. Alert hack with `settingsservice/setSystemSettings caption captionEnable on/off` - see if the LG
   player or any app follows it.
7. `listLaunchPoints` to lock HBO Max / Paramount+ / Media Player ids.
