# Research: "Live soccer" Stream Dock key (Paramount+ / Apple TV MLS)

Date of research: 2026-10-24 (Saturday, ~04:40 UTC). All endpoints below were
actually fetched from this machine unless marked "not tested". No code changes.

## TL;DR

* **Best source is Apple itself.** The Apple TV app's backend (UTS API,
  `uts-api.itunes.apple.com`) lists live and upcoming sporting events across *all*
  partner apps. Each event includes the provider (`channelId`, e.g.
  `tvs.sbd.10120` = Paramount+, `tvs.sbd.4000` = Apple TV/MLS) **and the exact
  deep link the TV app uses to hand off to the provider app** (`punchoutUrls.play`,
  e.g. `pplus://www.paramountplus.com/live-tv/stream/serie-a/<uuid>?searchReferral=appleatv&source=spotlight`).
  For MLS it gives a `tv.apple.com/us/sporting-event/.../umc.cse.<id>?playableId=...` URL.
  Two GETs (`searchTerm=Paramount+` and `searchTerm=MLS`) give the whole candidate list.
  No account needed. The only "keys" are two public web-client tokens (`utsk`, `utscf`)
  that are embedded in every tv.apple.com page and can be scraped at runtime.
* **ESPN's unofficial scoreboard API** is a good cross-check for "is it really live"
  (`status.type.state == "in"`) and has US broadcaster per match (`geoBroadcasts`
  with "Paramount+", "Apple TV"). But it has **no deep-link ids** for either service.
* **Deep links on tvOS:** pyatv `apps.launch_app(url)` (HA: `media_player.play_media`
  with `media_content_type: url`) opens URLs on the Apple TV over Companion. Paramount+
  registers `pplus://` and universal links for `www.paramountplus.com/live-tv/stream/*/`
  (verified in its apple-app-site-association). Whether it **starts playback directly**
  vs. lands on a profile picker / detail page must be verified live.
* **Verdict: feasible end-to-end (listing + per-game deep link), with live testing
  needed for the last step (does the link auto-play, any profile picker / "Watch live
  or from start" modal).** Worst case degrades to "open the right app/page + 1-2 remote
  presses".

---

## 1. Data sources for "what soccer is live now, on which US service"

### 1a. Apple TV app UTS API (recommended primary)

Undocumented, used by tv.apple.com. Public web-client parameters (scraped from the
`serialized-server-data` JSON in any tv.apple.com page, e.g.
`https://tv.apple.com/us/channel/mls/tvs.sbd.7000`):

```
caller=web&locale=en-US&pfm=web&sf=143441&v=100
&utscf=<scraped-at-runtime>
&utsk=<scraped-at-runtime>
```
(`sf=143441` = US storefront. FruitDeepLinks uses the same params with `v=90`.
`utsk`/`utscf` may rotate - scrape them with a regex `"utsk":"([^"]+)"`,
`"utscf":"([^"]+)"` from a tv.apple.com page and cache for a day.)

Endpoint list (from the page's own config) includes
`/uts/v3/sporting-events/{id}`, `/uts/v3/shelves/{shelfId}`,
`/uts/v3/mcp/apple-tv-live-sports`, `/uts/v3/mcp/apple-tv-upcoming-sports`,
`/uts/v3/sports/{sportId}`. v2 sports routes return
`400: Route not supported on API v2 ... migrate to API v3`.

**Sports search shelf (the useful one):**
```
GET https://uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE?searchTerm=Paramount%2B&<params>
GET https://uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE?searchTerm=MLS&<params>
GET https://uts-api.itunes.apple.com/uts/v3/shelves/uts.col.search.SE?searchTerm=Soccer&<params>
```
Returns ~27-28 items, sorted by kickoff, **starting with events already in progress**
(no `nextToken` observed). Each item has `sportName`, `leagueName`, `eventTime`,
`clockScore` (present while live), and `playables[0]` = primary provider with deep link.
`searchTerm=Paramount+` returned only Paramount+ events (Soccer, Rugby, MMA... -> filter
`sportName == "Soccer"`); `searchTerm=MLS` returned the MLS matches on Apple TV; a team
name (`searchTerm=Napoli`) also works for matching an ESPN game to an Apple id.

Sample (trimmed) item, `searchTerm=Soccer`, Paramount+ EFL match:
```json
{
  "id": "umc.cse.37en2vfv6drgjmfc8axpibi8a",
  "title": "Charlton Athletic vs. Norwich City",
  "sportName": "Soccer", "leagueName": "EFL Championship",
  "eventTime": {"gameKickOffStartTime": 1792841400000,
                "liveBadgeTime": {"startTime": 1792841400000, "endTime": 1792856100000}},
  "clockScore": null,
  "playables": [{
    "channelId": "tvs.sbd.10120",
    "serviceName": "US Digital Sports",
    "externalId": "aed28659-0eaf-4a61-b38d-415b491291a3",
    "punchoutUrls": {
      "open": "pplus://www.paramountplus.com/live-tv/stream/english-football-league/aed28659-0eaf-4a61-b38d-415b491291a3?searchReferral=appleatv&source=spotlight#open",
      "play": "pplus://www.paramountplus.com/live-tv/stream/english-football-league/aed28659-0eaf-4a61-b38d-415b491291a3?searchReferral=appleatv&source=spotlight"
    }
  }]
}
```
Live item (ESPN+ A-League, in progress at fetch time) shows `clockScore`:
```json
"clockScore": {"clockTime": "45+1:11.000", "hasEventEnded": false,
               "periodType": "Half", "periodValue": 1, "lastUpdatedTime": 1792817305604, ...}
```
MLS item (`searchTerm=MLS`):
```json
{"title": "CF Montréal vs. Nashville SC", "playables": [{"channelId": "tvs.sbd.4000",
  "url": "https://tv.apple.com/us/sporting-event/cf-montreal-vs-nashville-sc/umc.cse.1vpiinvtiko54yrue5pxq8f5s?playableId=tvs.sbd.4000%3AMLS-MAT-0009NT_EN"}]}
```
Response `channels` map gives names: `tvs.sbd.10120` Paramount+, `tvs.sbd.30061` ESPN,
`tvs.sbd.1000024` NBC Sports, `tvs.sbd.1000371` Peacock, `tvs.sbd.11280` DAZN,
`tvs.sbd.12962` Prime Video, `tvs.sbd.1000188` CBS Sports, `tvs.sbd.4000` Apple TV.

**Event detail** (all providers for one match, plus app bundle ids):
`GET https://uts-api.itunes.apple.com/uts/v3/sporting-events/umc.cse.35g35pp9ropax7038sphbc3lq?<params>`
(SSC Napoli vs. AS Roma, Serie A, 2026-10-24 16:00Z) - `playables` (trimmed):
```
tvs.sbd.10120  Paramount+   play: pplus://www.paramountplus.com/live-tv/stream/serie-a/6f7a5df2-a679-45b4-9ae5-ca496bcb5403?searchReferral=appleatv&source=spotlight
tvs.sbd.1000188 CBS Sports  play: cbssportsapp://home/watch/LET-211534833?source=tvapp
tvs.sbd.12962  Prime Video  play: aiv://aiv/detail?gti=...&action=watch&type=live...
tvs.sbd.1000371 Peacock     play: https://www.peacocktv.com/deeplink?deeplinkData=...
tvs.sbd.1000024 NBC Sports  play: nbcsportstve://watch/30000440
channels: tvs.sbd.10120 Paramount+ appBundleIds ['com.cbsvideo.app'] (adamId 530168168)
```
Use detail only when the shelf's `playables[0]` is not one of our services (the
shelf shows only the primary provider; e.g. "Juventus vs Napoli" primary is Peacock).

MLS detail (`umc.cse.1vpiinvtiko54yrue5pxq8f5s`) has three playables (EN/ES/FR feeds),
`channelId tvs.sbd.4000`, `subBrandId tvs.sbd.7000` (MLS), `serviceName "MLS Live US"`,
`externalId "MLS-MAT-0009NT_EN"` (the MLS stats match id), `url` =
`https://tv.apple.com/us/sporting-event/cf-montreal-vs-nashville-sc/umc.cse.1vpiinvtiko54yrue5pxq8f5s?playableId=tvs.sbd.4000%3AMLS-MAT-0009NT_EN`,
`showModal: true`, `howToWatch: [{"channelId":"tvs.sbd.4000","actions":["Subscribe"],...}]`
(unauthenticated request, so "Subscribe").

Other Apple sources checked:
* `https://tv.apple.com/us/channel/mls/tvs.sbd.7000` (HTML, server-rendered) - lists the
  whole MLS matchweek as `SportingEventLockup`s with `broadcastState`
  (`final`/`upcoming`/`live`) and `contextAction.url` (the sporting-event URL). Good
  MLS-only fallback by HTML scraping.
* `/uts/v3/mcp/apple-tv-live-sports` - returned an empty shelf header at fetch time
  (nothing Apple-owned live at 04:40Z); probably only Apple's own live sports. Re-check
  during an MLS match.
* `/uts/v3/mcp/apple-tv-upcoming-sports` - curated 20 items (MLS + F1), not complete.

Live determination: `clockScore` present and `hasEventEnded == false`, or
`liveBadgeTime.startTime <= now <= endTime` (endTime is a generous estimate: 2-6 h after
kickoff), ideally cross-checked with ESPN `state == "in"`.

Caveats: undocumented/private API, may change or require new tokens; Apple ToS prohibit
automated scraping in principle - keep it to personal, low-rate use (a few calls per key
press, cache 60 s). The open-source project FruitDeepLinks
(https://github.com/kineticman/FruitDeepLinks, `bin/apple_scraper_db.py`) uses exactly
these endpoints (`tv.apple.com/api/uts/v3/sporting-events/{id}`,
`/uts/v3/shelves/uts.col.search.SE`) and `punchoutUrls`, and is actively maintained
(last push 2026-10-20) - useful as a reference when Apple changes something.

### 1b. ESPN unofficial site API (recommended cross-check / fallback list)

No key, CORS `*`, `Cache-Control: max-age=5`. Undocumented; ESPN's Terms of Use
don't grant API rights - personal low-rate polling is common practice
(see community docs e.g. https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b).

Per league: `https://site.api.espn.com/apis/site/v2/sports/soccer/<league>/scoreboard[?dates=YYYYMMDD]`
All soccer in one call: `https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?limit=500`
(default limit 100; with 500 it returned 411 events for 2026-10-24).
Header (featured, one event per league, 45 leagues): `https://site.web.api.espn.com/apis/v2/scoreboard/header?sport=soccer&region=us&lang=en`.

Broadcast fields - yes, US streaming services are present:
```json
// usa.1 (MLS) event 761903, NSH @ MTL, 2026-10-24T17:30Z
"broadcasts": [{"market": "national", "names": ["Apple TV"]}],
"geoBroadcasts": [{"type": {"id": "4", "shortName": "STREAMING"},
                   "market": {"id": "1", "type": "National"},
                   "media": {"shortName": "Apple TV"}, "lang": "en", "region": "us"}]
// uefa.champions 401915390 SHK @ INT 2026-10-21
"broadcasts": [{"market": "national", "names": ["CBSSN", "Paramount+"]}]
// ita.1 401874957 MON @ TOR
"broadcasts": [{"market": "national", "names": ["Paramount+"]}]
```
Observed names: `Paramount+`, `CBSSN`, `Apple TV`, `ESPN+`, `USA Net`, `Peacock`,
`NBCSN`, `Prime Video`, `NWSL+`, `Fandango`, `ION`, `beIn Sports`, `Universo`...
Status: `status.type.state` = `pre` / `in` / `post`, `shortDetail` e.g. `41'`.
On 2026-10-24 the all-soccer board had 14 Apple TV (all MLS) and 14 Paramount+ events
(Serie A, WSL, EFL Championship). Note NWSL is **not** Paramount+ in 2026 per ESPN
(NWSL+ / Prime Video seen); UEFA UCL/UEL all Paramount+.
Missing: no Paramount+ content id, no Apple `umc.cse` id (ESPN MLS ids like `761903`
are not Apple ids) - so ESPN alone can't deep link; match by team names via Apple search.

### 1c. Others (checked, not recommended)
* **TheSportsDB** - `eventstv.php?d=2026-10-24&s=Soccer` with free key `123` returns
  only 1 row (free tier truncation) and stale channel names ("MLS Season Pass").
  Premium (paid) needed for full TV listings. No deep links.
* **football-data.org** (free key) - fixtures/scores only, no broadcaster data;
  without key returned 0 matches.
* **API-Football** (free 100 req/day) - fixtures/live scores, no US broadcaster/TV
  field. Not tested.
* **LiveSoccerTV** - has the best broadcaster listings but no API; scraping is against
  its ToS. Not tested.
* **Paramount+ apps-api** - `/apps-api/v3.0/androidtv/hub/multi-channel-collection/live-and-upcoming.json`
  (from EPlusTV source, https://github.com/tonywagner/EPlusTV `services/paramount-handler.ts`)
  returns `videoContentId`, start/end, `channelName`; needs an app token (`at=`) and the
  user's logged-in cookies. Not tested; unnecessary given Apple's data already carries
  the P+ stream UUIDs.
* **MLS APIs** (`sportapi.mlssoccer.com/api/matches`, `stats-api.mlssoccer.com/v1/matches`)
  returned 404 with the guessed paths; not needed.

### 1d. Apple TV / MLS subscription (verified)
Starting with the 2026 season MLS Season Pass was folded into the regular Apple TV
subscription: all MLS matches incl. playoffs at no extra cost; standalone Season Pass
discontinued.
Sources: https://9to5mac.com/2025/11/13/mls-games-free-apple-tv-next-season/ ,
https://support.apple.com/en-ae/111113 ,
https://frontofficesports.com/article/mls-folding-its-season-pass-shifting-all-games-to-apple-tv-in-2026/ ,
https://en.wikipedia.org/wiki/MLS_Season_Pass .
The UTS data agrees: MLS playables are on channel `tvs.sbd.4000` (Apple TV) with
sub-brand `tvs.sbd.7000` (MLS).

---

## 2. Deep links on tvOS

### pyatv / Home Assistant mechanics
* pyatv: `atv.apps.launch_app("<bundle id or URL>")`; requires Companion protocol.
  Docs: https://pyatv.dev/development/apps/ ("To launch an app with a URL, pass the URL").
  Links copied from share sheets/browsers usually work; sometimes need small edits
  (e.g. dropping the country code).
* HA: `media_player.play_media` with `media_content_type: url`,
  `media_content_id: <url>` (https://www.home-assistant.io/integrations/apple_tv -
  examples include `https://tv.apple.com/show/severance/umc.cmc...`, Netflix, Disney+,
  `youtube://`). Caveat in the docs: links may fail if a developer ships separate
  iOS/tvOS apps.

### Paramount+ (bundle id `com.cbsvideo.app`, App Store id 530168168)
* `https://www.paramountplus.com/.well-known/apple-app-site-association`: appID
  `H443NM7F8H.com.cbsvideo.app`, paths include `/live-tv`, `/live-tv/*/`,
  `/live-tv/stream/*/`, `/live-tv/stream/sports/`, `shows/*/live_feed`,
  `/shows/*/video/*/*/`, `/movies/*/` (excludes `/watch/`, `/schedule/`).
  So `https://www.paramountplus.com/live-tv/stream/<league-slug>/<uuid>/` is a declared
  universal link.
* Custom scheme `pplus://` - this is what Apple's TV app itself uses as the punchout
  (`pplus://www.paramountplus.com/live-tv/stream/serie-a/<uuid>?searchReferral=appleatv&source=spotlight`),
  so it is the format most likely to start the specific live game on tvOS. A Channels
  DVR thread shows the same `pplus://.../live-tv/stream/...` URLs working on Android TV;
  FruitDeepLinks reports Apple TV handles its deeplinks.
* The per-match UUID comes from Apple UTS (`playables[].externalId` / the punchout).
  Slugs seen: `serie-a`, `english-football-league`, `world-rugby`; UCL/UEL slugs
  will appear on match days (re-check, e.g. `uefa-champions-league`).
* **CBS Sports Golazo Network** (24/7 soccer channel on Paramount+): web slug is
  `/live-tv/stream/golazo/` (found on https://www.paramountplus.com/sports/ ;
  `cbs-sports-golazo` is a 404). Fallback candidates:
  `pplus://www.paramountplus.com/live-tv/stream/golazo/` and
  `https://www.paramountplus.com/live-tv/stream/golazo/` (CBS Sports HQ:
  `/live-tv/stream/cbssportshq/`). Not tested on device.
* Web check: event stream URL redirects to `/shows/serie-a/` and Golazo to signup when
  not logged in (expected; irrelevant to the tvOS app).

### Apple TV app (MLS) (bundle id `com.apple.TVWatchList`)
* URL format confirmed from UTS:
  `https://tv.apple.com/us/sporting-event/<home>-vs-<away>/umc.cse.<id>` and the
  playable-specific variant `...?playableId=tvs.sbd.4000%3AMLS-MAT-<matchid>_EN`
  (`_ES`, `_FR` for other commentary feeds).
* Getting the id for a given match: Apple search shelf `searchTerm=MLS` (or a team name),
  the MLS channel page `tv.apple.com/us/channel/mls/tvs.sbd.7000`, or matching
  `externalId` `MLS-MAT-xxxxxx` from MLS data. No need for ESPN mapping.
* Expected tvOS behaviour (to verify): opens the TV app on the event page; with
  `playableId` it may start playback or show a "Watch Live / From Beginning" modal
  (`showModal: true` in the data) needing one `select` press.

---

## 3. Fallbacks when a deep link doesn't play directly
1. **Deep link + scripted presses**: launch link, wait ~3-5 s, check HA
   `media_player` state/`app_id` (`com.cbsvideo.app` / `com.apple.TVWatchList`) and
   whether it's `playing`; if not, send `select` (and re-check). Handles a profile picker
   ("Who's watching?" - P+ remembers the last profile on many devices, verify) or a
   "watch live" modal. Moderately robust because the target page is already correct.
2. **Open the provider's live section**: `pplus://www.paramountplus.com/live-tv/` or
   `.../live-tv/stream/golazo/` (Golazo usually shows/whips around live matches),
   or the Apple TV app event page without `playableId`.
3. **App launch + blind remote navigation** (`com.cbsvideo.app`, then up/down/select)
   - fragile: home rows are personalised/A-B tested, so only as last resort.
4. **No live game**: show "No live soccer" on the LCD key, or start Golazo.

---

## 4. Suggested algorithm for the key

```
on press:
  if press within 20 s of previous and list cached: idx = (idx+1) % len(list)
  else:
    tokens = cached utsk/utscf (refresh daily from tv.apple.com page)
    items  = shelf(searchTerm="Paramount+") + shelf(searchTerm="MLS")   # 2 GETs
    keep   = sportName=="Soccer" and is_live(item)   # clockScore && !hasEventEnded, or liveBadgeTime window
    for each kept: link = playables[0].punchoutUrls.play  (P+)  or  playables[0].url (Apple TV)
                   if playables[0].channelId not in {tvs.sbd.10120, tvs.sbd.4000}:
                       fetch /uts/v3/sporting-events/{id} and pick our provider
    optional: cross-check with ESPN soccer/all scoreboard (state=="in",
              geoBroadcasts includes Paramount+/Apple TV); add ESPN-only games
              by Apple search on home team name
    sort: priority leagues (UCL, MLS, Serie A, UEL, EFL...), then kickoff
    idx = 0
  media_player.play_media(apple_tv, url=list[idx].link, type=url)
  show "HOME v AWAY 63'" on the LCD key
```

---

## 5. Verdict

**Feasible end-to-end** for both services, with data that is already available
without accounts: Apple's UTS API provides the live list *and* the provider deep link
per match (P+ `pplus://.../live-tv/stream/<league>/<uuid>`, Apple
`tv.apple.com/us/sporting-event/.../umc.cse...?playableId=...`), ESPN gives a reliable
live-state cross-check. Risks: both APIs are unofficial (tokens/params can change);
deep-link playback behaviour is app-controlled.

**Needs live testing on the Apple TV 4K (during a live match):**
1. `launch_app("pplus://www.paramountplus.com/live-tv/stream/<slug>/<uuid>?searchReferral=appleatv&source=spotlight")`
   - does P+ start the game directly? Profile picker? Try also the `https://` universal
   form and without query params.
2. Golazo: `pplus://www.paramountplus.com/live-tv/stream/golazo/` vs `https://` form.
3. MLS: `https://tv.apple.com/us/sporting-event/<slug>/umc.cse.<id>?playableId=tvs.sbd.4000%3AMLS-MAT-..._EN`
   vs. without `playableId` / without `/us` - autoplay, modal, or event page only.
4. Whether `searchTerm=Paramount+` / `MLS` keep returning in-progress games first on a
   busy UCL night (cap ~28 items; UCL matchdays have up to 9 simultaneous P+ games
   - fine; otherwise also query `searchTerm=Soccer` or league names).
5. Token lifetime of `utsk`/`utscf` (scrape-and-refresh logic).
6. That HA `play_media` (url) behaves the same as raw pyatv `launch_app` for custom
   schemes (`pplus://`).
