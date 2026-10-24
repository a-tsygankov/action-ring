# Apple TV "context tab" feasibility: research notes

Date: 2026-10-24. Scope: read-only research. Nothing was changed in any repo or on HA.

## Sources used

- **pyatv 0.18.0 source.** This is the exact version HA's `apple_tv` manifest pins (`"requirements": ["pyatv==0.18.0"]`). I downloaded the wheel and read it locally. Files:
  - `pyatv/interface.py`
  - `pyatv/protocols/mrp/__init__.py`, `mrp/messages.py`, `mrp/player_state.py`
  - `mrp/protobuf/CommandInfo.proto`, `CommandOptions.proto`, `ContentItem.proto`, `LanguageOption.proto`, `PlaybackQueueRequestMessage.proto`
  - `pyatv/protocols/companion/__init__.py`, `companion/api.py`
  - `pyatv/core/facade.py`, `core/relayer.py`
- **HA core `dev` branch.** Files:
  - `homeassistant/components/apple_tv/remote.py`, `media_player.py`, `manifest.json`
  - `homeassistant/components/remote/const.py`
- **Live evidence from our own Pi.** This is our raw MRP dump, `/app_configs/local_streamdock_n3/mrp_raw.log`, written by `mrp_raw.py`. It holds 125 records from 2026-10-23/24 covering Pluto, the Apple TV app, Netflix and AirPlay. I read it only.
- **pyatv GitHub issues:**
  - [#2230 FastForwardBegin](https://github.com/postlund/pyatv/issues/2230)
  - [PR #2363 FF/RW for Companion (open, unmerged)](https://github.com/postlund/pyatv/pull/2363)
  - [#720 Language Options](https://github.com/postlund/pyatv/issues/720)
  - [#2325 Companion dumps (captions, touch)](https://github.com/postlund/pyatv/issues/2325)
  - [#2461 tvOS 18 Companion now-playing / InSight / UpNext](https://github.com/postlund/pyatv/issues/2461)
  - [#1720 hold_secs](https://github.com/postlund/pyatv/issues/1720)
  - [#2927 Netflix deep link](https://github.com/postlund/pyatv/issues/2927)
  - [#1545 URL launch (shipped 0.12.1)](https://github.com/postlund/pyatv/issues/1545)
  - [#1755 skip](https://github.com/postlund/pyatv/issues/1755)
- **Docs and support pages:**
  - [HA apple_tv docs](https://www.home-assistant.io/integrations/apple_tv/)
  - [pyatv Apps docs](https://pyatv.dev/development/apps/)
  - [pyatv supported features](https://pyatv.dev/documentation/supported_features/)
  - [Apple: Control what's playing (tvOS guide; the page currently serves tvOS 27)](https://support.apple.com/guide/tv/control-whats-playing-atvb7944597f/tvos)
  - [Apple: Siri Remote usage 102337](https://support.apple.com/102337)
  - [Apple: Accessibility Shortcut](https://support.apple.com/en-ca/guide/tv/atvb0a315d10/16.0/tvos)
  - [Apple deployment: tvOS bundle IDs](https://support.apple.com/en-gb/guide/deployment/depcdd66fe58/web)
  - [Netflix help: subtitles](https://help.netflix.com/en/node/372)
  - [Amazon: X-Ray](https://aboutamazon.com/news/entertainment/what-is-x-ray-on-prime-video)
- **iTunes lookup API for bundle IDs:** `https://itunes.apple.com/lookup?bundleId=<id>`.
- **apple-app-site-association (AASA) files** fetched from each service's domain.

---

## 0. Architecture facts that drive everything else

- **Protocol priority.** The pyatv facade relays every `RemoteControl` call to the highest-priority protocol that implements it. The default order is `MRP > DMAP > Companion > AirPlay > RAOP` (`core/facade.py: DEFAULT_PRIORITIES`). Power is the exception and prefers Companion.
  - So in HA, `up/down/left/right/select/menu/home/top_menu/play/pause/next/previous/stop/skip_*` go over MRP. On tvOS 15+, MRP is tunnelled through AirPlay.
  - `screensaver/guide/channel_up/channel_down/control_center` exist only in Companion, so they go over Companion.
- **Picking a protocol yourself.** In our own pyatv code, `atv.remote_control.get(Protocol.Companion)` and `atv.remote_control.get(Protocol.MRP)` return the per-protocol implementations (`core/relayer.py: Relayer.get`). This gives access to:
  - Companion: `CompanionRemoteControl.api` (a `CompanionAPI`, with `mediacontrol_command`, `hid_command`, `launch_app` and so on).
  - MRP: `MrpRemoteControl.protocol` (an `MrpProtocol`, with `send_and_receive(messages.command(...))`) and `MrpRemoteControl._send_command(cmd, **options)`.
- **MRP commands carry options.** `messages.command(cmd, **kwargs)` copies any keyword into `SendCommandMessage.options` (a `CommandOptions` protobuf). Raw `ChangePlaybackRate(playbackRate=…)`, `SkipForward(skipInterval=…)` and `EnableLanguageOption(languageOption=…)` are therefore one call each.
- **Apps advertise what they accept.** Each player sends `supportedCommands` (a list of `CommandInfo`). pyatv stores the list per player (`player_state.py: command_info()`) and derives `features` from it (`_FEATURE_COMMAND_MAP`). An app accepts only the commands it lists. Anything else is answered with a `SendError` or ignored.

### Live `supportedCommands` captured on our Apple TV (`mrp_raw.log`)

| App / player | Advertised commands |
|---|---|
| **Apple TV app** `com.apple.TVWatchList`, player `avkit-…` | PreviousInContext, NextInContext, **DisableLanguageOption, EnableLanguageOption**, SeekToPlaybackPosition (canScrub 0), **ChangePlaybackRate supportedRates = −96/−48/−24/−8/8/24/48/96**, SkipBackward (preferredIntervals 10), SkipForward (preferredIntervals 10), **BeginRewind/EndRewind/BeginFastForward/EndFastForward**, Stop, TogglePlayPause, Pause, Play |
| **Pluto TV** `tv.pluto.ios`, AVKit player | identical to the Apple TV app (the stock AVKit set) |
| Pluto `MediaRemote-DefaultPlayer` (show clock) | SeekToPlaybackPosition, Pause, Play |
| `com.apple.TVAirPlay` | Play, Pause, TogglePlayPause, Stop, NextTrack, PreviousTrack, Begin/End FF and RW, AdvanceRepeat/Shuffle, Seek |
| Netflix | no `supportedCommands` captured yet (we only saw a paused DefaultPlayer state) |
| YouTube, Prime, Max, Hulu, Disney+, Paramount+ | not captured yet |

Takeaways:

- Any app that uses the system **AVPlayerViewController** (AVKit) advertises FF/RW, rate change, 10 s skip and language-option commands.
- AVKit players advertise **NextInContext/PreviousInContext, not NextTrack/PreviousTrack**. HA's `media_next_track` (MRP `NextTrack`) is therefore probably rejected in AVKit apps. That needs a test.
- The Pluto AVKit content item also carried **`availableLanguageOptions`/`currentLanguageOptions` without being asked**:
  - Audio group: type 0, `en`, identifier `"English [Original]-en-English"`.
  - Subtitle group: type 1, allowEmptySelection true, options `__AUTO__` and `en`, identifier `"English-en-English"`.

---

## 1. HA `remote.send_command` for apple_tv (HA 2026.10, pyatv 0.18.0)

The implementation (`apple_tv/remote.py`) works like this:

- `wakeup`, `suspend`, `turn_on`, `turn_off`, `volume_up` and `volume_down` map to `atv.power.*` and `atv.audio.*`.
- **Every other string is resolved with `getattr(atv.remote_control, command)`.** Any zero-argument `RemoteControl` method name is therefore valid, not just the list in the docs.
- `ValueError("Command not found")` is raised otherwise.

**Valid commands** (from `pyatv/interface.py RemoteControl`):

- Navigation: `up`, `down`, `left`, `right`, `select`, `menu`, `home`, `home_hold`, `top_menu`
- Playback: `play`, `pause`, `play_pause`, `stop`, `next`, `previous`, `skip_forward`, `skip_backward`
- Power: `wakeup`, `suspend`, `turn_on`, `turn_off`
- Volume: `volume_up`, `volume_down`
- Companion-only: `channel_up`, `channel_down`, `screensaver`, `guide`, `control_center`

`set_position`, `set_shuffle` and `set_repeat` exist but need an argument, so they cannot be called through send_command. The HA docs list a subset: wakeup, suspend, home, top_menu, menu, control_center, turn_on, turn_off, select, play, pause, up, down, left, right, volume_up, volume_down, previous, next, skip_backward, skip_forward.

Parameters:

- **`num_repeats`** repeats the whole list.
- **`delay_secs`** is the sleep after each command. Default **0.4 s** (`remote/const.py`).
- **`hold_secs`**:
  - Only the threshold matters. If `hold_secs >= 1`, HA calls `cmd(action=InputAction.Hold)`.
  - pyatv's hold is **hard-coded to 1 s**: MRP `_send_hid_key` sends HID down, `sleep(1)`, HID up. Companion `_press_button` does the same with `delay=1`. `hold_secs: 5` still holds for 1 s ([pyatv #1720](https://github.com/postlund/pyatv/issues/1720) is still open).
  - Hold only works with commands that take `action`: `up`, `down`, `left`, `right`, `select`, `menu`, `home`.
  - `hold_secs >= 1` with `skip_forward`, `play` and similar raises `TypeError` (unexpected kwarg). `hold_secs` applies to every command in the list.
- `select`/`menu`/`home` have no DoubleTap through HA. pyatv supports `InputAction.DoubleTap`, but HA never passes it.

MRP HID key map (`_KEY_LOOKUP`):

| Key | (usage page, usage) |
|---|---|
| up | (1, 0x8C) |
| down | (1, 0x8D) |
| left | (1, 0x8B) |
| right | (1, 0x8A) |
| select | (1, 0x89) |
| menu | (1, 0x86) |
| home | (12, 0x40) |
| topmenu | (12, 0x60) |
| suspend | (1, 0x82) |
| wakeup | (1, 0x83) |
| volume_up | (12, 0xE9) |
| volume_down | (12, 0xEA) |
| stop | (12, 0xB7) |
| next | (12, 0xB5) |
| previous | (12, 0xB6) |

Companion HID codes: Up 1, Down 2, Left 3, Right 4, Menu 5, Select 6, Home 7, VolUp 8, VolDown 9, Siri 10, Screensaver 11, Sleep 12, Wake 13, PlayPause 14, ChannelInc 15, ChannelDec 16, Guide 17, PageUp 18, PageDown 19. `control_center` sends PageDown.

---

## 2. Playback speed / fast-forward

**pyatv public API: no fast-forward or rate API.** There are no FF/RW/rate methods in `interface.RemoteControl` in 0.18.0.

- [pyatv #2230](https://github.com/postlund/pyatv/issues/2230): the author says FF/RW is "currently not supported… would otherwise be really easy to implement".
- [PR #2363](https://github.com/postlund/pyatv/pull/2363), which adds it for Companion, is **still OPEN**.

**Skip with an interval does exist:**

- MRP: `skip_forward(time_interval)` sends `SkipForward` with `skipInterval=int(t)`. With no argument it uses the app's `preferredIntervals[0]` (10 s in AVKit), else 15 s.
- Companion: `skip_forward` sends `_mcc` SkipBy with `{"_skpS": float(t or 10)}`.
- HA's `skip_forward` takes no argument, so you get the app default.

**Raw paths with our own pyatv:**

1. **Companion media control (confirmed by a user in #2230):**
   ```python
   from pyatv.const import Protocol
   from pyatv.protocols.companion.api import MediaControlCommand
   comp = atv.remote_control.get(Protocol.Companion)
   await comp.api.mediacontrol_command(MediaControlCommand.FastForwardBegin)  # _mcc 8
   # ... later
   await comp.api.mediacontrol_command(MediaControlCommand.FastForwardEnd)    # _mcc 9
   # RewindBegin 10 / RewindEnd 11
   ```
   In #2230, zehnm reported FF works in the Apple TV app ("fast forward works (in Apple TV+)!"). There is only one speed level, but **calling `remote_control.right()` while in FF increases the speed**. The `_iMC` flags expose `FastForward 0x10` / `Rewind 0x20` availability per app (`MediaControlFlags`).

2. **MRP commands (advertised live by AVKit apps, so expected to work there; needs a test):**
   ```python
   from pyatv.protocols.mrp.protobuf import CommandInfo_pb2 as CI
   mrp = atv.remote_control.get(Protocol.MRP)        # MrpRemoteControl
   await mrp._send_command(CI.BeginFastForward)       # 9;  EndFastForward 10
   await mrp._send_command(CI.BeginRewind)            # 11; EndRewind 12
   await mrp._send_command(CI.ChangePlaybackRate, playbackRate=24.0)  # 20; options.playbackRate (float)
   await mrp._send_command(CI.SkipForward, skipInterval=30)           # or mrp.skip_forward(30)
   ```
   - The AVKit apps' `ChangePlaybackRate.supportedRates` = ±8, ±24, ±48, ±96. These are probably the scan steps behind the UI's 2×/3×/4× labels. Use only advertised values.
   - `CommandInfo.preferredPlaybackRate` and `supportedRates` let the key decide per app.
   - The raw `_send_command` raises `CommandError` with the SendError name when the app rejects the command. That gives clean feedback.

3. **HID emulation of the Siri Remote** ([Apple guide](https://support.apple.com/guide/tv/control-whats-playing-atvb7944597f/tvos); [Apple 102337](https://support.apple.com/102337)):
   - Single press left/right: skip 10 s.
   - Press and hold: continuous FF/RW.
   - Press repeatedly: cycle 2×, 3×, 4×.
   - Play/Pause resumes.

   So:
   - `remote.send_command right` with `hold_secs: 1` (a 1 s HID hold) should start scanning in AVKit apps.
   - Repeated `right` presses while scanning should step the speed. #2230 confirms this when FF was started by `_mcc`.
   - Whether the scan keeps going after the HID release, and whether a plain `right` tap skips 10 s, must be tested live.
   - Apps with custom players differ. YouTube reportedly supports 10 s skip but not press-and-hold FF ([Apple discussions](https://discussions-kr-prz.apple.com/thread/254374843)).

**App coverage.** AVKit apps (Apple TV app, Pluto; probably Tubi and Paramount+, unverified) advertise everything above. Netflix, YouTube, Prime, Max, Hulu and Disney+ use custom players. Their `supportedCommands` must be captured (mrp_raw already logs `set_state` with `supportedCommands`; just play each app).

---

## 3. Subtitles / audio track

**A. MRP language options (best path for AVKit apps).** Command ids: `EnableLanguageOption = 53`, `DisableLanguageOption = 54` (`CommandInfo.proto`). Id 26 is `PreviousChapter`, not a language command.

- **Reading the options:**
  - `ContentItem.availableLanguageOptions` (groups of `LanguageOption{type, languageTag, characteristics, displayName, identifier}`) and `ContentItem.currentLanguageOptions`.
  - Metadata flag `languageOptionsAvailable`.
  - Request them with `PlaybackQueueRequest.includeLanguageOptions = true`. pyatv's `messages.playback_queue_request()` does not set it, so build the message yourself.
  - The Pluto/AVKit player already pushed them to us unsolicited (see §0).
- **Setting an option:** send `EnableLanguageOption` with `CommandOptions.languageOption` (bytes).
  - Per [pyatv #720](https://github.com/postlund/pyatv/issues/720), the bytes are an NSKeyedArchiver binary plist wrapping a serialized `LanguageOption` protobuf. Only `type` (0 = audio, 1 = subtitle), `identifier` (if present) and `languageTag` are needed.
  - Subtitles off = `DisableLanguageOption` with only `type=1`.
  - #720 attaches a sample plist. pyatv has a partial `companion/keyed_archiver.py` (decode only). Building the archive with `plistlib` (binary, `$archiver`/`$objects`/UID) is doable.
- **Not exposed by pyatv:** no API, issue #720 still open.
- **Feasibility:** needs our pyatv plus custom code. Works only where the app advertises Enable/DisableLanguageOption. The Apple TV app and Pluto do (live). Others are unknown.
- **Known flakiness (#720):** tvOS does not always push updates after a change. Treat the selection we sent as current, as Apple's Remote app does.

**B. Companion caption toggle** ([pyatv #2325](https://github.com/postlund/pyatv/issues/2325)):

- `_mcc 12` (GetCaptionSettings) returns `_mcs` 2 or 3.
- `_mcc 13` with `{"_mcs": 4}` (SetCaptionSettings) toggles the value.
- This is what the iOS Remote app sends. The reporter saw "**0** effect in Netflix, youtube, zee". It probably flips the system closed-captions preference, so AVKit apps may honour it.
- Call shape: `await comp.api.mediacontrol_command(MediaControlCommand.SetCaptionSettings, {"_mcs": 4})`.
- Needs a test in the Apple TV app.

**C. Accessibility Shortcut** ([Apple](https://support.apple.com/en-ca/guide/tv/atvb0a315d10/16.0/tvos)):

- Settings > Accessibility > Accessibility Shortcut > Closed Captions. Triple-pressing **Back** (Siri Remote 2nd gen+) or Menu (1st gen) then toggles captions.
- A user reports it works in Apple's TV app, but not in some third-party players (Channels).
- Remote emulation: `remote.send_command` `["menu","menu","menu"]` with `delay_secs: 0.05`. pyatv also adds a flush round-trip per press.
- **Risk:** if tvOS does not count injected HID presses as a triple-click, three Back presses will exit the player. Test carefully. MRP HID `menu` is (1, 0x86). Companion HID Menu is 5.

**D. On-screen UI (gestures through our Companion touch):**

- **Apple TV app (tvOS guide):** show controls, swipe up on the clickpad, then the Subtitles button. Audio Options sits in the controls. Pressing left (skip back) also auto-enables subtitles briefly.
- **Netflix:** [Netflix help](https://help.netflix.com/en/node/372) says swipe up or press up, then "Audio & Subtitles". Apple's older text says swipe down. App-specific, so test.
- **Siri ("turn on subtitles"):** not reachable. pyatv has `HidCommand.Siri = 10` (Companion) but no audio path.

---

## 4. "Who's in this scene"

- **Apple TV app InSight** ([Apple tvOS guide](https://support.apple.com/guide/tv/control-whats-playing-atvb7944597f/tvos)): show playback controls, then **swipe down** on the clickpad or touch surface (or press down on the ring, silver remote). The panel has tabs Info / **InSight** / Chapters / Continue Watching. It covers Apple TV+ originals only.
  - Via Companion: `select` (or a tap) to show controls, then a `swipe` downward. Our `atv_touch.py` already does touch swipes.
- **Prime Video X-Ray** ([Amazon](https://aboutamazon.com/news/entertainment/what-is-x-ray-on-prime-video); [Cult of Mac](https://www.cultofmac.com/?p=604444)): tap or pause, then **swipe up** to show the X-Ray strip (bottom-left). Pressing up again or selecting "X-Ray" opens it full screen.
- **No MRP or Companion command triggers either one.** It is gesture emulation only.
- **Alternative for InSight:** fetch the data instead of opening the UI. tvOS 18+ Companion `NowPlayingInfo` (register `_interest` with `NowPlayingInfo`, request `FetchCurrentNowPlayingInfoEvent`) carries `rawTimedMetadata` JSON with Person/Song entities for Apple TV+ titles ([#2461](https://github.com/postlund/pyatv/issues/2461), [#2325](https://github.com/postlund/pyatv/issues/2325)). The cast could be shown on the dock without touching the TV UI. This is not in the pyatv API (raw `_send_command` and an NSKeyedArchiver decode).

---

## 5. Launching apps

- **HA:**
  - `media_player.select_source` with `source: <app name from source_list>` calls `atv.apps.launch_app(bundle_id)`. The list comes from Companion `app_list` (FetchLaunchableApplicationsEvent) and needs a Companion pairing.
  - `media_player.play_media` with `media_content_type: app` **or** `url` and `media_content_id: <bundle id or URL>` calls `atv.apps.launch_app(media_id)` directly. In `media_player.py`: `if media_type in {MediaType.APP, MediaType.URL}: await self.atv.apps.launch_app(media_id)`.
- **pyatv:** `CompanionAPI.launch_app` sends `_launchApp` with `{"_urlS": url}` when `is_url_or_scheme()`, else `{"_bundleID": id}`.
- **Bundle IDs.** Checked with the iTunes lookup API; every hit reports Apple TV in `supportedDevices`, i.e. a universal app with the same ID on tvOS:

| App | Bundle ID | Evidence |
|---|---|---|
| Netflix | `com.netflix.Netflix` | iTunes 363590051; seen live in our MRP log |
| YouTube | `com.google.ios.youtube` | iTunes 544007664; live HA `app_id` |
| Prime Video | `com.amazon.aiv.AIVApp` | iTunes 545519333; HA community app_list |
| HBO Max | `com.wbd.stream` | iTunes 1666653815 ("HBO Max: Stream Movies & TV"); `play.hbomax.com` AASA lists `72XN96Y3B5.com.wbd.stream` (also `com.wbd.hbomax`). Old `com.hbo.hbonow` returns 0 results |
| Hulu | `com.hulu.plus` | iTunes 376510438; hulu.com AASA `ZUW57S6CYF.com.hulu.plus` |
| Disney+ | `com.disney.disneyplus` | iTunes 1446075923; AASA `WZ62FEL3UG.com.disney.disneyplus` |
| Paramount+ | `com.cbsvideo.app` | iTunes 530168168; AASA `H443NM7F8H.com.cbsvideo.app` |
| Apple TV app | `com.apple.TVWatchList` | Apple deployment guide; live MRP log |
| Pluto TV | `tv.pluto.ios` | iTunes 751712884; live |
| Tubi | `com.adrise.tubitv` | iTunes 886445756; live (journal) |

Final check: `atvremote … app_list`, or read `source_list` on the HA entity. I could not read attributes over the read-only paths available here.

---

## 6. Deep links (`launch_app(url)`)

- **pyatv docs, known working:**
  - `https://tv.apple.com/show/severance/umc.cmc.1srk2goyh2q2zdxcx605w8vtx`
  - `https://tv.apple.com/show/...?...&action=play`
  - Disney+ `/series/…`
  - `https://play.hbomax.com/page/urn:hbo:page:…:type:series`
  - `https://www.netflix.com/title/80234304`
  - Tips from the docs: drop the country code if a link fails, and check `/.well-known/apple-app-site-association`.
- **Apple TV app.** [pyatv #2927](https://github.com/postlund/pyatv/issues/2927) reports `https://tv.apple.com/us/episode/being-alive/umc.cmc.…?action=play` working (opens and plays).
  - **Sporting events** (`https://tv.apple.com/us/sporting-event/<slug>/umc.cse.<id>`, e.g. MLS): **no evidence found** either way.
  - The Channels "FruitDeepLinks" project logs Apple TV event IDs of the form `umc.cse.…` ([thread](https://community.getchannels.com/t/fruitdeeplinks-universal-sports-aggregator-for-channels-dvr/45197?page=8)).
  - Expectation: it opens the event page. Add `?action=play` and test.
- **Netflix:** in #2927 (open), `https://www.netflix.com/watch/<id>` now opens Netflix but **stays on the home screen**. Netflix deep-to-playback looks broken.
- **Paramount+:**
  - The AASA claims `/live-tv`, `/live-tv/*/`, `/live-tv/stream/*/`, `/live-tv/stream/sports/`, `/shows/*/video/*/*/` and `/movies/*/`, and **excludes `/watch/`**.
  - The app's own scheme seen in the wild: `pplus://www.paramountplus.com/live-tv/stream/<league>/<uuid>?searchReferral=appleatv`.
  - Community reports say it **opens the page but needs a manual "Watch Live" select**. ADB tuners send DPAD_CENTER afterwards. A Firecore user saw "Failed to open url" on tvOS with a link that worked on iOS.
  - Plan: `launch_app("https://www.paramountplus.com/live-tv/")` (or `pplus://…`), then a delayed `select`. Test live.
- **YouTube:** `youtube://watch/<id>` (HA docs example) or `https://www.youtube.com/watch?v=<id>` (AASA covers `/watch`).
- **Prime Video:** AASA claims `/detail/*`, `/watch/*` and `/dp/*` on `www.primevideo.com` for `J7P34ALZ5R.com.amazon.aiv.AIVApp`.
- **Hulu:** AASA claims `/watch/*`, `/series/*`, `/movie/*`, `/sports_episode/*` and `/hub/*`.
- **Caveat (HA docs):** links "may not work if a developer maintains separate iOS and tvOS apps". AASA reflects the iOS entitlement. All the apps above are universal (same bundle ID), which helps but proves nothing.
- **Newer Companion `PlayMediaEvent`** (#2461): it "doesn't actually start playing the media, just opens to its page", and only works for the Apple TV app.

---

## 7. YouTube "next video"

- HA `media_player.media_next_track` and the `next` remote command resolve to MRP `NextTrack` (MRP has priority). If MRP were absent, they would resolve to Companion `_mcc NextTrack`.
- YouTube on tvOS uses its own player. **We have no capture of its `supportedCommands`**, and I found no community confirmation that `next` works. The Companion `_mcF` bitmask in #2325 for a YouTube video is 1806 = 0x70E, which includes NextTrack (0x4) and PreviousTrack (0x8). That hints Next is at least advertised over Companion. The decoded mask is 0x400 SkipBackward | 0x200 SkipForward | 0x100 Volume | 0x8 Previous | 0x4 Next | 0x2 Pause. **FastForward (0x10) and Rewind (0x20) are not set**, which fits YouTube having no press-and-hold FF.
- The reliable path is our existing **YouTube Lounge session** (`next` command), already working on the Apple TV per JOURNAL.
- Test: play YouTube, watch `mrp_raw.log` for the YouTube player's `supportedCommands`, then try `remote.send_command next`.

---

## Feasibility table

| Control | Via HA as-is | Needs our pyatv | Infeasible / notes |
|---|---|---|---|
| D-pad / select / menu / home / top_menu / play_pause | ✅ `remote.send_command` | | |
| Skip ±10 s | ✅ `skip_forward`/`skip_backward` (app default interval) | custom interval: `mrp.skip_forward(30)` | |
| FF/RW scan | ~ `right`/`left` with `hold_secs: 1` (fixed 1 s HID hold), then repeated `right` to step speed. Needs a test | ✅ Companion `_mcc` 8/9/10/11, or MRP `BeginFastForward`/`EndFastForward`, or `ChangePlaybackRate(playbackRate=±8/24/48/96)` (AVKit) | custom players: per app |
| Subtitles toggle | ~ triple `menu` if the Accessibility Shortcut is set to CC (risky) | MRP `Enable/DisableLanguageOption` (AVKit apps); Companion `_mcc 13 {_mcs:4}` | Netflix, YouTube: in-app UI only (gestures) |
| Audio track | | MRP `EnableLanguageOption` type 0 | |
| InSight / X-Ray | | gesture: show controls, then swipe down (Apple) or swipe up (Prime), via Companion touch | no command exists |
| Launch app | ✅ `select_source` / `play_media` type `app` | `apps.launch_app(id)` | |
| Deep link | ✅ `play_media` type `url` | same | Netflix watch links land on home; Paramount+ needs a `select` after opening |
| YouTube next | ? `next` (untested) | Lounge `next` ✅ | |

## Live tests to run

1. Capture `supportedCommands` for Netflix, YouTube, Prime, Max, Hulu, Disney+ and Paramount+ (play each title for a few seconds with mrp_raw on).
2. FF tests:
   - AVKit: MRP `BeginFastForward` then `EndFastForward`, and `ChangePlaybackRate(8/24/48)`.
   - Companion `_mcc 8`/`9`.
   - HA `right` with `hold_secs: 1`: does the scan persist after release? Do extra `right` presses step 2×/3×/4×?
3. `EnableLanguageOption`/`DisableLanguageOption` with an NSKeyedArchiver-wrapped `LanguageOption` in the Apple TV app and Pluto.
4. Companion `_mcc 13 {_mcs:4}` in the Apple TV app.
5. Triple `menu` with `delay_secs` 0.05 after setting the Accessibility Shortcut to Closed Captions (expect a player exit if it fails).
6. `tv.apple.com/us/sporting-event/…?action=play` and Paramount+ `/live-tv/…` (+ `select`).
7. YouTube `next` over MRP.
