# Research: Govee lamp control + icon sources for StreamDock keys

Date: 2026-10-24. Context: HA 2026.10 (HAOS, RPi 4), StreamDock N3 add-on (`homeassistant/streamdock_n3`, `python:3.12-slim-bookworm`, aarch64), 112x112 LCD keys. Research only; nothing changed in the repo.

Method: HA docs + HA core source at tags `2026.10.0..2026.10.3`, govee-local-api source, govee2mqtt source, Govee developer docs, Iconify API (`/collections`, `/<prefix>.json?icons=`, `/search`) queried live on 2026-10-24, PyPI JSON API.

---

## A. Govee lamp

### Which lamp is it?
Likely Govee floor lamps (from Govee's lamp line-up and product pages):
- **H6076** "RGBICW Smart Floor Lamp Basic" (corner lamp, ~1000 lm; Govee lists Matter support). https://eu.govee.com/es-es/products/govee-rgbicw-smart-corner-floor-lamp
- **H607C** "Floor Lamp 2" (Govee pages disagree on RGBICWW vs. plain RGB+W; one reseller says Matter). https://us.govee.com/collections/lamp/products/govee-floor-lamp-2
- **H6079** "RGBICWW Floor Lamp Pro" (with speaker), **H6072** (RGBICWW floor lamp, ~1500 lm), **H6073**, **H6078**. Comparison: https://us.govee.com/blogs/smart-lamps/govee-lamp-review
The SKU is printed on the label of the base/control box and shown in Govee Home > device > settings > device info. **Check that first**: if it's H6076 or H607C, Matter is an extra option (see recommendation).

### A1. HA core `govee_light_local` (LAN API)
Docs: https://www.home-assistant.io/integrations/govee_light_local/ — added in HA 2024.2, `local_push`, codeowner @Galorhallen, library `Galorhallen/govee-local-api`.

- **HA 2026.10.x pins `govee-local-api==3.1.0`** (checked manifest at tags 2026.10.0, .2, .3 and `dev`).
- Features (from `homeassistant/components/govee_light_local/light.py`): on/off, **brightness** (0-100 mapped to 0-255), **RGB**, **color temperature** (default 2000-9000 K), and **effects**: if the model's capabilities have the SCENES flag, the entity gets `LightEntityFeature.EFFECT` and `effect_list = ["none", *scenes]`; `light.turn_on effect: <name>` calls `set_scene`.
- What "scenes" are in 3.1.0: a **generic hard-coded table** of 1-byte codes (`sunrise, sunset, movie, dating, romantic, twinkle, candlelight, snowflake, energetic, breathe, crossing`). The library itself now warns that these codes "predate Govee's per-model scene libraries; on many models a code plays a different scene or nothing at all". The floor lamps H6072, H6076, H607C are declared with scenes=True (segments 8/7/13); **H6076A is BASIC (no scenes)**.
- Upstream fix in progress: govee-local-api 3.3.0 (committed 2026-10-23, "Generate per-model scenes from Govee's scene library", `data/model_scenes.json`) gives per-model built-in scene names, e.g. H6076: night light, fireworks, white light, stacking, bouncing ball, bubble, rainbow, meteor, ripple; H607C: 15 scenes incl. reading, work, white light; H6072: 20. PyPI latest is still 3.2.0; it will reach HA core in a later release (not 2026.10).
- **Not supported**: DIY scenes, Govee app snapshots, Tap-to-Run, DreamView/video modes, music modes.
- Supported models: long list on the docs page; includes floor lamps **H6072, H6073, H6076, H6076A, H6078, H6079, H607C** and H60A0/H60A1/H60A4/H60A6.
- **Enabling LAN control**: Govee Home app > the device > Settings (gear) > **LAN Control** toggle. Govee's own guide: https://app-h5.govee.com/user-manual/wlan-guide . The toggle can take minutes to appear and may need a firmware update (https://www.openhab.org/addons/bindings/govee/ , https://www.nuget.org/packages/DMXCore.Plugin.Govee).
- Network: discovery multicast **239.255.255.250 UDP 4001**, devices reply to **UDP 4002** on the controller, commands go to the device on **UDP 4003**. Only one process per host can bind 4002 (so core `govee_light_local` and govee2mqtt's LAN client conflict if both run on the same HAOS host). Multicast across Wi-Fi/VLANs may need IGMP/mDNS help. Sources: openHAB binding above; https://github.com/homebridge-plugins/homebridge-govee/wiki/LAN-Control ; govee2mqtt `docs/CONFIG.md` (`GOVEE_LAN_SCAN` for static IPs).

### A2. Govee cloud "Platform API" (Developer API v2, `openapi.api.govee.com`)
Docs: https://developer.govee.com/reference/get-you-devices , https://developer.govee.com/reference/get-light-scene
- Header `Govee-API-Key: <key>`. Endpoints:
  - `GET /router/api/v1/user/devices` - devices + capabilities.
  - `POST /router/api/v1/device/state`, `POST /router/api/v1/device/control`.
  - `POST /router/api/v1/device/scenes` - capability `devices.capabilities.dynamic_scene` / instance **`lightScene`**, options `{name, value:{paramId,id}}`.
  - `POST /router/api/v1/device/diy-scenes` - type `devices.capabilities.diy_color_setting` / instance **`diyScene`**, value = integer id.
  - **Snapshots**: `dynamic_scene` instance **`snapshot`** appears in the device's capabilities from `/user/devices` (options are the snapshots saved in the app, e.g. `{"name":"color scene","value":465503}`); apply via `/device/control` with `{type: devices.capabilities.dynamic_scene, instance: snapshot, value: <id>}`.
  - Other capabilities: `on_off/powerSwitch`, `range/brightness`, `color_setting/colorRgb|colorTemperatureK`, `segment_color_setting`, `music_setting/musicMode`, `toggle/gradientToggle|nightlightToggle`, `mode/presetScene|nightlightScene`.
- **Rate limits** (current docs, per-minute table): `user/devices` 30/min/account; `device/control` 12/s per account (burst 80) and 2/s per device (burst 6); `device/state` 30/min/device; `device/scenes` 30/min/device. HTTP 429 on excess. The **10,000 requests/day per key** figure comes from the older v1 docs and community reports (https://community.home-assistant.io/t/govee-integration/228516?page=58 , https://apis.io/rate-limits/govee/govee-rate-limits/); the v2 docs don't mention a daily cap. Either way, button presses are fine; **aggressive state polling is what burns quota**.
- **API key**: Govee Home app > Profile > Settings (gear) > **Apply for API Key** (name + reason), the key is emailed (LaggAt README: https://github.com/LaggAt/hacs-govee ; govee2mqtt `docs/CONFIG.md`).

HA integrations using the cloud:
| Project | Status (checked 2026-10-24) | Scenes / snapshots |
|---|---|---|
| **govee2mqtt** (wez), HA add-on + MQTT. https://github.com/wez/govee2mqtt | Active-ish: last tag 2026.03.25, last push 2026-06-15, ~1.5k stars, 347 open issues/PRs. | Best coverage. Uses **LAN first**, then IoT (AWS MQTT, needs Govee email+password, undocumented), then Platform API (needs API key). Light `effect_list` = Govee scene library (via LAN `ptReal` packets using the public `app2.govee.com/appsku/v1/light-effect-libraries?sku=` catalogue) + Platform `lightScene` + **`diyScene`** + **`snapshot`** (from device capabilities) (`src/platform_api.rs:get_scene_caps`). **Tap-to-Run / one-click** become HA **`scene.*` entities** (via undocumented `app2.govee.com/bff-app/v1/exec-plat/home`, needs account login). FAQ: "Purge Caches" button after adding snapshots/Tap-to-Run; video/DreamView effects aren't returned by the API, so wrap them in a Tap-to-Run or snapshot. https://github.com/wez/govee2mqtt/blob/main/docs/FAQ.md |
| **LaggAt/hacs-govee** (HACS). https://github.com/LaggAt/hacs-govee | Last release 2025.7.1; maintainer looking for a successor, only breaking-change fixes. | on/off, brightness, CT, color only; no scenes/snapshots. Not recommended. |
| A "govee-cloud" integration | No such maintained repo found. | - |
| Core HA | No official Govee cloud integration in 2026.10. Core has `govee_light_local` (LAN) and `govee_ble` (sensors only). | - |
Note: govee2mqtt requires the HA MQTT integration + a broker (Mosquitto add-on).

### A3. Favourites / Tap-to-Run via API?
- **Snapshots**: yes, official v2 API (`dynamic_scene/snapshot`), and exposed as effects by govee2mqtt.
- **DIY scenes**: yes, official (`diyScene`).
- **Tap-to-Run (one-click)**: **not in the official API**. Only via the undocumented app API with account login (govee2mqtt "IoT" path -> HA scene entities). Could break whenever Govee changes the app backend.
- **"Favourites"** (starred scenes in the app): not exposed anywhere. Workaround: re-save the favourites as **snapshots** (or build an ordered list of scene names in the dock config).
- **DreamView / TV sync** (if "TV mode" means syncing with a Govee TV backlight / AI Sync Box): not controllable via API; wrap it in a Tap-to-Run (govee2mqtt -> `scene.*`) or snapshot.

### A4. Recommendation
1. **Find the SKU.** If H6076/H607C and Matter-capable, pairing to HA via **Matter** gives the most robust local on/off/brightness/CT/RGB, but no scenes.
2. **Baseline (no cloud, works today):** enable LAN Control, add core **Govee lights local**. On the dock:
   - Brightness keys/knob: `light.turn_on` with `brightness_step_pct: +10 / -10` (works with any integration; core HA service).
   - "Normal light": `light.turn_on` with `color_temp_kelvin: 3000, brightness_pct: 80`.
   - "TV mode": an **HA-side preset** (e.g. `color_temp_kelvin: 2700, brightness_pct: 15`, or a `scene.tv_mode` created in HA). Toggle = compare current attributes or keep a small `input_boolean`. Fully local; no dependency on Govee scene codes.
   - Scene cycling: read `effect_list` from the entity state and call `light.turn_on effect=<next>`. Caveat: on HA 2026.10 the list is the generic 11 names and may play the wrong scene on these lamps until govee-local-api 3.3.0 lands in core.
3. **If they really want their app favourites/DIY/snapshots/Tap-to-Run:** install the **govee2mqtt add-on** (needs Mosquitto + MQTT integration; API key for DIY/snapshots; Govee email/password for Tap-to-Run). Then remove/disable core `govee_light_local` for that lamp to avoid the UDP 4002 clash and duplicate entities. Save favourites as snapshots in the app -> "Purge Caches" -> they appear in `effect_list`; scene-cycle key filters that list to a configured subset (e.g. the snapshot names) in a fixed order. "TV mode" = a snapshot or a Tap-to-Run (`scene.turn_on`). Cloud calls only happen on button presses, so rate limits are a non-issue as long as govee2mqtt's polling stays default.
4. Dock implementation hint: keep the scene list in the add-on config (`scenes: ["Reading", "Sunset", ...]`) with fallback to `effect_list`; one key = "next scene", long-press = "previous" or "none".

---

## B. Icons for streaming-service and control keys

### B1. Licenses of Iconify sets (from `https://api.iconify.design/collections?prefixes=...`, live)
| Prefix | Name / author | License (Iconify metadata) | Colour? | Notes |
|---|---|---|---|---|
| `logos` | SVG Logos, Gil Barbara | **CC0-1.0** | full colour | README: logos are "property of their respective owners". https://github.com/gilbarbara/logos |
| `simple-icons` | Simple Icons (v16.35.0) | **CC0-1.0** | mono (`currentColor`; brand hex available in simple-icons data) | DISCLAIMER: users must obtain permission for trademark use; brands can request removal. https://github.com/simple-icons/simple-icons/blob/develop/DISCLAIMER.md |
| `mdi` | Material Design Icons, Pictogrammers | Apache-2.0 | mono | has some brand glyphs (netflix, hulu, youtube, plex) |
| `material-symbols` / `-light` | Google | Apache-2.0 | mono | variants `-rounded`, `-outline`, `-sharp` |
| `ic` | Google Material Icons | Apache-2.0 | mono | |
| `tabler` | Tabler Icons 3.49 | MIT | mono (stroke) | `brand-netflix`, `brand-youtube`, `brand-disney`, `brand-hbo`, `brand-amazon` |
| `ph` | Phosphor 2.1.1 | MIT | mono | |
| `lucide` | Lucide | ISC | mono | |
| `ri` | Remix Icon | Apache-2.0 | mono | |
| `cib` | CoreUI Brands | CC0-1.0 | mono | |
| `selfhst` | selfh.st/icons | **CC BY 4.0** (attribution required) | full colour, 512x512 square app-style | best coverage of streaming apps |
| `thesvg` / `thesvg-color` | theSVG (thesvg.org) | MIT (as declared) | mono / colour | newer aggregator; provenance of brand SVGs unclear |
| `streamline-logos`, `streamline-color` | Streamline | CC BY 4.0 | | |
| `arcticons` | Arcticons | CC BY-SA 4.0 (share-alike) | mono line-art | only set with a Govee icon |
| `fa6-brands` | Font Awesome 6 Brands | CC BY 4.0 | mono | |
| `cbi` | Custom Brand Icons | **CC BY-NC-SA 4.0** | mono | **avoid** (non-commercial + share-alike) |

**Trademark angle.** A copyright licence (CC0/MIT/Apache) on the SVG does **not** grant trademark rights; Netflix/Disney etc. marks remain theirs. Showing a service's logo on a key that launches that service in a personal controller is classic **nominative use** (identifies the product, no implied endorsement) and is low-risk; the repo is non-commercial. Good hygiene for a public repo:
- add `ICONS.md`/`NOTICE` listing each icon id, source set, licence, and "All trademarks are property of their respective owners; no affiliation or endorsement";
- don't modify logos to look like an official product, don't use them in the project name/branding;
- prefer **not committing** large sets of third-party logos (fetch/render at build time, see B4), so a takedown would only mean changing an id in config;
- note Simple Icons has already removed **Hulu, Prime Video, Amazon Prime** (still served by Iconify as `hidden` for back-compat: `simple-icons:hulu`, `simple-icons:primevideo`), so treat those as at-risk ids.

### B2. Availability (verified via `https://api.iconify.design/<prefix>.json?icons=...` and `/search`)
Recommended pick in **bold**. "colour" = multi-colour artwork; "mono" = single colour (tint with brand colour on a dark key).

| Service | Full-colour options | Mono options |
|---|---|---|
| Netflix | **`logos:netflix-icon`** (CC0, red "N" ribbon, square); `logos:netflix` (wordmark 512x138); `selfhst:netflix`; `thesvg-color:netflix` | `simple-icons:netflix`, `mdi:netflix`, `cib:netflix`, `tabler:brand-netflix`, `ri:netflix-fill` |
| YouTube | **`logos:youtube-icon`** (CC0, red play button 256x180); `logos:youtube` (wordmark); `selfhst:youtube` | `simple-icons:youtube`, `mdi:youtube`, `cib:youtube`, `tabler:brand-youtube-filled` |
| Prime Video | **`selfhst:amazon-prime-video`** (CC BY 4.0, square); `thesvg-color:prime-video` (MIT, wide wordmark 800x246) | `simple-icons:primevideo` (**hidden/removed upstream**), `arcticons:amazon-prime-video` |
| Apple TV app | `selfhst:apple-tv`, `thesvg-color:apple-tv` (essentially mono) | **`simple-icons:appletv`** (CC0), `cbi:apple-tv` (NC) |
| HBO Max / Max | `selfhst:hbo-max`, `selfhst:max` (blue), `thesvg-color:hbo-max` | **`simple-icons:hbomax`** (CC0, "max" wordmark) / `simple-icons:max`, `simple-icons:hbo`, `tabler:brand-hbo` |
| Hulu | **`selfhst:hulu`** (CC BY 4.0, green) / `selfhst:hulu-light` | **`mdi:hulu`** (Apache) or `cib:hulu` (CC0); `simple-icons:hulu` hidden/removed |
| Disney+ | **`selfhst:disney-plus`** (CC BY 4.0); `thesvg-color:disney-plus` (wide) | `tabler:brand-disney` (Disney, not Disney+), `arcticons:disney` |
| Paramount+ | `selfhst:paramount-plus` (blue), `thesvg-color:paramountplus` | **`simple-icons:paramountplus`** (CC0) |
| LG webOS media player | none for "webOS" | **`simple-icons:lg`** (CC0, tint #A50034) or generic `material-symbols:tv-rounded` / `mdi:television-play` |
| Pluto TV | `thesvg-color:pluto-tv` (MIT, wide 29.6x8 wordmark) | `arcticons:pluto-tv` (CC BY-SA); `cbi:plutotv` (NC). Not in logos/simple-icons/selfhst. Fallback: render text "Pluto" or use the official app icon nominatively. |
| Tubi | `thesvg-color:tubi` (purple #7408FF) | **`simple-icons:tubi`** (CC0) |
| Sonos | `thesvg-color:sonos` (mono) | **`simple-icons:sonos`** (CC0), `cib:sonos` |
| Govee | none | `arcticons:govee-home` (CC BY-SA 4.0 line-art); `cbi:govee-h6076` / `cbi:govee-h6046` (NC, avoid). Practical: use `material-symbols:floor-lamp-rounded` / `mdi:floor-lamp-torchiere` instead of a Govee logo. |
(Others found: `simple-icons:plex`, `simple-icons:roku`, `logos:spotify-icon`.)

Practical approach: a **uniform style** works better on 112 px keys than mixed artwork: use mono `simple-icons`/`mdi` glyphs rendered white (or in the brand colour) on a brand-coloured/dark rounded background, falling back to `selfhst` colour icons (CC BY 4.0, attribute) for Prime Video, Disney+, Hulu. Wide wordmarks (`thesvg-color:prime-video`, `pluto-tv`) are illegible at 112 px.

### B3. Media-control and lamp glyphs (consistent permissive set)
Primary: **Material Symbols, `-rounded` variant (Apache-2.0)**; all ids below verified to exist. Fallbacks from `mdi` (Apache-2.0) / `tabler` (MIT).

| Function | `material-symbols:` | Fallback |
|---|---|---|
| Play/pause | `play-pause-rounded` (also `play-arrow-rounded`, `pause-rounded`) | `mdi:play-pause`, `tabler:player-play-filled` |
| Subtitles | `subtitles-rounded` / `closed-caption-rounded` | `mdi:subtitles-outline`, `tabler:badge-cc` |
| Audio track / language | `voice-selection-rounded`, `record-voice-over-rounded`, `translate-rounded`, `music-note-rounded`, `audio-description-rounded` | `mdi:translate`, `tabler:message-language` |
| Fast-forward / rewind | `fast-forward-rounded` / `fast-rewind-rounded` | `mdi:fast-forward`, `mdi:rewind` |
| Skip +10/-10 s | `forward-10-rounded` / `replay-10-rounded` (also 5/30) | `mdi:fast-forward-10`, `tabler:rewind-forward-10` |
| Next / previous | `skip-next-rounded` / `skip-previous-rounded` | `mdi:skip-next`, `tabler:player-track-next-filled` |
| Skip chapter / chapter list | no dedicated glyph: `skip-next-rounded`, `next-plan-rounded`, `menu-book-rounded`, `format-list-numbered-rounded` | `mdi:skip-forward`, `mdi:book-open-page-variant` |
| Skip ad | `ad-off-rounded` | `tabler:badge-ad` |
| Lamp | `floor-lamp-rounded` / `floor-lamp-outline-rounded`, `lightbulb-rounded` | `mdi:floor-lamp-torchiere`, `mdi:floor-lamp`, `tabler:lamp` |
| Brightness up / down | `brightness-high-rounded` / `brightness-low-rounded` (`brightness-medium-rounded`) | `tabler:brightness-up` / `tabler:brightness-down`, `mdi:brightness-7` / `mdi:brightness-5` |
| Scene (next) | `palette-rounded`, `auto-awesome-rounded`, `scene` | `mdi:palette`, `tabler:sparkles` |
| TV mode / normal light | `tv-rounded`, `live-tv-rounded`, `ambient-screen-rounded` / `light-mode-rounded` | `mdi:television`, `tabler:device-tv` |
| Night mode (Sonos) | `bedtime-rounded`, `dark-mode-rounded` | `mdi:weather-night`, `tabler:moon` |
| Speech enhancement (Sonos) | `record-voice-over-rounded`, `hearing-rounded`, `interpreter-mode-rounded` | `mdi:account-voice`, `mdi:text-to-speech` |
| Volume / mute / power | `volume-up-rounded`, `volume-down-rounded`, `volume-off-rounded`, `power-settings-new-rounded` | `mdi:volume-high` etc. |

The Iconify API can pre-colour and size: `https://api.iconify.design/material-symbols/play-pause-rounded.svg?color=%23ffffff&width=96` (or `logos/netflix-icon.svg?height=96`).

### B4. Rendering SVG -> 112x112 on bookworm aarch64
| Option | Pros | Cons |
|---|---|---|
| **cairosvg** 2.9.1 (LGPL-3.0, pure-Python wheel) https://pypi.org/project/cairosvg/ | Mature, simple `svg2png(bytestring=..., output_width=..)` | Needs `libcairo2` (+ pango-free) via apt (~10-15 MB extra in slim image); occasional SVG feature gaps (some gradients/masks) |
| **resvg-py** 0.5.0 (`pip install resvg_py`, Rust/PyO3, manylinux2014 **aarch64 wheel**, released 2026-08) https://github.com/baseplate-admin/resvg-py | Zero system deps, very accurate renderer (resvg), fast, small | Younger binding; API `resvg_py.svg_to_bytes(svg_string=..., width=..)` returns PNG bytes; `resvg` 0.2.0 (briceyan, MIT) is an alternative binding also with aarch64 wheels |
| skia-python 144.1 (aarch64 wheel) | Accurate | ~30+ MB wheel, overkill |
| svglib + reportlab (LGPL) | pure Python | poor fidelity (gradients) |
| **Render on the PC at build time, commit PNGs** | No runtime deps; deterministic; the image already copies PNGs (`icons/back.png` etc.) | Third-party logos live in the public repo history (trademark/takedown surface, licence attribution needed per file); regenerating needs a script |

**Recommendation.**
- **Generic glyphs (Material Symbols/mdi/tabler):** commit the source SVGs or rendered PNGs; Apache/MIT allow redistribution (keep a NOTICE with the licence). Low risk.
- **Brand logos:** don't commit rendered logos. Keep only icon **ids** in config (e.g. `"icon": "logos:netflix-icon"`), and render at add-on start/first use: fetch SVG from `api.iconify.design` (or vendor the CC0 `simple-icons`/`logos` SVGs at a pinned version during `docker build`), rasterise with **resvg-py** (no apt packages, aarch64 wheel exists), composite with Pillow (already present via the streamdock package) onto a 112x112 rounded background, cache PNG/JPEG in `/data/.cache/icons`. Fall back to a text label if offline. This keeps the public repo free of trademark artwork, makes swaps a config change, and needs no libcairo.
- If network access at runtime is undesirable, do the fetch+render in a `tools/render_icons.py` run on the PC and write PNGs into a git-ignored folder that `restore.sh`/deploy copies to the add-on's `/data` (or the HA `/share`), rather than committing them.
- Mind the attribution requirement if using `selfhst` (CC BY 4.0) or `arcticons` (CC BY-SA 4.0); prefer CC0/Apache/MIT ids where an equivalent exists, and never `cbi` (NC).

---

## Sources
- HA Govee lights local: https://www.home-assistant.io/integrations/govee_light_local/ ; source https://github.com/home-assistant/core/tree/2026.10.3/homeassistant/components/govee_light_local
- govee-local-api: https://github.com/Galorhallen/govee-local-api (light_capabilities.py v3.1.0; data/model_scenes.json on master)
- Govee LAN guide: https://app-h5.govee.com/user-manual/wlan-guide ; openHAB Govee binding https://www.openhab.org/addons/bindings/govee/ ; Homebridge LAN notes https://github.com/homebridge-plugins/homebridge-govee/wiki/LAN-Control
- Govee Platform API: https://developer.govee.com/reference/get-you-devices , https://developer.govee.com/reference/get-light-scene
- Rate-limit reports: https://community.home-assistant.io/t/govee-integration/228516?page=58 , https://apis.io/rate-limits/govee/govee-rate-limits/
- govee2mqtt: https://github.com/wez/govee2mqtt , FAQ https://github.com/wez/govee2mqtt/blob/main/docs/FAQ.md , CONFIG https://github.com/wez/govee2mqtt/blob/main/docs/CONFIG.md , `src/platform_api.rs`, `src/undoc_api.rs`, `src/lan_api.rs`
- LaggAt hacs-govee: https://github.com/LaggAt/hacs-govee
- Govee lamps: https://us.govee.com/blogs/smart-lamps/govee-lamp-review , https://us.govee.com/collections/lamp/products/govee-floor-lamp-2 , https://eu.govee.com/es-es/products/govee-rgbicw-smart-corner-floor-lamp
- Iconify: https://icon-sets.iconify.design/ , https://api.iconify.design/collections , https://iconify.design/docs/api/
- Simple Icons disclaimer: https://github.com/simple-icons/simple-icons/blob/develop/DISCLAIMER.md ; SVG Logos: https://github.com/gilbarbara/logos
- PyPI: https://pypi.org/project/resvg-py/ , https://pypi.org/project/resvg/ , https://pypi.org/project/cairosvg/ , https://pypi.org/project/skia-python/
