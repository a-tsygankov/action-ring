# Scene jump service: design

Date: 2026-11-13. Status: approved 2026-11-13.
Source research: [docs/SCENE_SOURCES.md](../../SCENE_SOURCES.md).
Service contract: [services/README.md](../../../homeassistant/streamdock_n3/services/README.md).

Lines marked **(call)** are choices made while writing this, not things the
owner said; everything else comes from the request or the conversation.

## Intent

A dock key that jumps a film to its next nude/sex scene. When a film starts in
a streaming app on the Apple TV, the service identifies it, finds scene
timecodes (local database first, then a list of websites), and lights the key
when it has any. A press seeks to the first scene that starts after the
current position.

## Goals

- New service `scenes` in `services/scenes/`, on the existing contract, with no
  edits to `ha_daemon.py`, `tabs.py` or `keyspec.py`.
- Key gray by default; full colour when a jump is possible; gray again when no
  known scene lies ahead.
- Timecodes cached in a local SQLite database, hits and misses alike.
- One module per website, independent of the others, merged by a main module.
  A blocked or empty source never stops the others.
- Polite to the sites: a few requests per film, once, with pauses.
- Everything the service does is logged.
- `docs/SCENE_SOURCES.md` is updated whenever a source is re-examined.

## Non-goals (this version)

- YouTube; the LG's own media player (optional later).
- Series episodes **(call)**: films only.
- Sources without timecodes (AZNude, image search): the key stays gray.
  Matching screenshots to the picture is a separate project.
- A cloud database or a Cloudflare Worker.
- Harvesting whole sites ahead of time.

## Anti-goals

- Getting past a bot check (Cloudflare challenge, captcha). A challenge means
  "source blocked", nothing more.
- Request bursts, retries in a loop, or a lookup on every play of the same film.
- Storing or logging personal data found in pages (NSFWhen's scene records
  carry the submitter's account details), URLs, or response bodies.
- Showing the key in colour when a press would do nothing.

## Constraints found in the project

- The Apple TV entity reports `media_title`, `app_id`, `media_duration`,
  `media_position` (+ `media_position_updated_at`), `media_content_type` and
  `supported_features`. It reports no year, director or cast.
- Exact jumps exist only where the app accepts `media_player.media_seek`
  (seen working: Max, Disney+). `knob_accel.py` `_seek_target_for` shows how
  the current position is computed (position + age while playing) and that
  HA's position lags a seek for a few seconds.
- Render-path methods (`key`, conditions, `vars`, `context`, `on_state`) must
  do no I/O and finish in milliseconds; work happens in jobs and actions.
- `ctx.http` is GET/POST text with a timeout and an `op` name; tests fake it
  by URL substring. `ctx.store.sqlite(name)` gives the database.

## Approaches considered

- **A. On demand, on the Pi (chosen by the owner).** Look up a film when it
  starts; cache the answer. Fewest requests; home connection; nothing new to
  run. The key lights a few seconds into the film.
- **B. Harvest ahead.** Copy every skip file weekly. Instant and offline, but
  thousands of requests: the spamming the owner asked to avoid.
- **C. Cloudflare Worker + D1.** Central scraping and database. More to run,
  data-centre addresses are blocked more often, useful only for several docks.

## How it works

### 1. Noticing a film

A job `watch` runs every 5 s and reads cached state only (cheap when nothing
changed). A film is "on" when all hold **(call)**:

- the Apple TV is `playing` or `paused`, and its app is not in
  `exclude_apps` (YouTube by default);
- `media_title` is non-empty and `media_duration` is at least `min_film_s`
  (default 3000 s = 50 min), or `media_content_type` is `movie`;
- the same title and app have been seen for `settle_s` (default 10 s), so
  previews while browsing cause no lookups.

The film key is the normalised title (lower case, punctuation and a trailing
"(year)" removed) plus the runtime in minutes. A new key starts a lookup; the
key going away clears the snapshot (the dock key turns gray).

### 2. Identifying it

The request asks for name, year, director and cast. The TV gives only the
name and runtime, so **(call)**:

- If `ctx.secret("tmdb_key")` exists, search TMDB by title, pick the result
  whose runtime is within 3 min, and take year, director, top cast, TMDB and
  IMDb ids. Without a key this step is skipped.
- Each source's own search then gives candidates. A candidate matches when
  its normalised title equals the film's and nothing known disagrees: a
  candidate whose id, year (±1) or runtime (±3 min) contradicts what is known
  about the film is rejected, **even when it is the only one**. If several
  remain, choose in this order: same TMDB/IMDb id; same year; runtime within
  3 min; same director; at least two cast names in common. Still ambiguous
  means "no match" (logged), never a guess.
- A source that gives no runtime (VideoSkip) can only be checked by year or
  id, which need TMDB (see Decided: TMDB key).
- Long series episodes: real content reports `media_content_type` `video`,
  so the 50-minute rule lets them through; they are filtered by failing to
  match a film (TMDB film search, or the sources' film lists).
- Whatever metadata the sources return (NSFWhen: year, runtime, director,
  cast, TMDB id; VideoSkip: release date, IMDb id) is stored with the film.

### 3. Database (`scenes.sqlite` via `ctx.store.sqlite("scenes")`)

- `films`: id, norm_title, title, year, runtime_s, director, cast (JSON),
  tmdb_id, imdb_id, updated_at.
- `lookups`: film_id, source, status (`hit`, `miss`, `ambiguous`, `blocked`,
  `error`), fetched_at. One row per film and source.
- `scenes`: film_id, source, start_s, end_s, category, synced_with, note.

Lookup by norm_title, then narrowed by runtime/year as in step 2. A `hit` is
reused for `hit_ttl_days` (90); a `miss` or `ambiguous` for `miss_ttl_days`
(14); `blocked` and `error` for 1 day **(call)**. Schema details and
migrations are the builder's.

### 4. Sources

`services/scenes/sources/<name>.py`, each exposing one function:

```python
def lookup(http, film: Film, cfg: dict) -> SourceResult
# SourceResult(status, scenes=[Scene(start_s, end_s, category, synced_with, note)], meta={...})
```

A source knows nothing about the database, the dock or other sources, so
they can run in parallel later; for now the merger calls them one after
another in config order. Parsing is pure functions over page text, tested
from saved pages. The list is the `sources` config entry; adding a site is a
new module plus its name there.

- **videoskip** (primary): search → film page → the best skip file whose
  filters include sex/nudity: prefer one synced with the playing app's
  service (`app_services` config maps `app_id` to VideoSkip's names), else
  the most downloaded. One download per film. Parse `start --> end` blocks
  and their category line; apply the per-service offset line when it names
  the playing service; ignore everything from the `data:` line on.
- **nsfwhen** (secondary): search → film page → the embedded `film` JSON.
  Keep times, category, verification status; drop everything else.
- **nudidex**: a module that returns `blocked` without a request unless
  `probe` is enabled in its config; kept so the list and the document stay in
  step **(call)**.
- **aznude**: not built in this version **(call)**; documented only.

Shared helper for all sources: a per-site minimum gap between requests
(default 3 s), a full browser `User-Agent`, a 15 s timeout, and detection of
a challenge page / 403 / 429 → `blocked`. At most `max_lookups_per_day` (30)
films are looked up per day; beyond that the film waits **(call)**.

### 5. Merging

Keep the categories in `categories` (default nudity and sex scene;
"suggestive" off **(call)**). Take VideoSkip's scenes as they are; add an
NSFWhen scene only if it overlaps none of them. Sort by start; join scenes
that start within `join_gap_s` (20 s) of the previous one's end, so a press
never lands inside the scene it just showed. The result, an immutable tuple
of start times, is the in-memory snapshot the render path reads.

### 6. The key and the press

`{"service_key": "scenes.next"}` on the `context:streaming` page, slot 4
(free today) **(call)**. `tests/test_config.py` asserts that slot is empty
and is updated. The key is absent on soccer's `live_game` page, which is
fine: no film is playing there.

- Condition `scenes.ready`: a film is on, the app is in `seek_apps`, and a
  scene starts later than position + `guard_s` (2 s).
- `seek_apps` **(call)**: the apps where `media_seek` has been checked on the
  TV, initially Max (`com.wbd.stream`) and Disney+ (`com.disney.disneyplus`).
  The `supported_features` seek bit alone is not trusted: an app may set it
  and still land wrong, and a coloured key that misses is an anti-goal. Other
  apps are added to the list after a check on the TV. Key spec: gray icon, with a
  mode `when: scenes.ready` → colour icon. No label **(call)**.
- Position = `media_position` + age of `media_position_updated_at` while
  playing. After a jump, the position counts as the **start of the scene
  jumped to** until the real position passes it **(call)**: a second press
  during the lead-in goes to the following scene, and the key is gray if
  there is none. Seeking backwards by hand (real position more than
  `lead_s` + 10 s before that start, once `seek_hold_s` = 5 s has passed)
  drops this memory.
- Action `next`: find the first scene start after the position; call
  `media_player.media_seek` to `start - lead_s` (default 5 s, not before the
  current position); log it. If none: do nothing.
- Apps not in `seek_apps`: the key stays gray and the first film there logs
  one line (lookups still run, so the cache fills). Jumping by swipes or 10 s skips is left for later **(call)**.
- The key must turn gray when the last scene's start passes with no event:
  after each change the job/action calls `ctx.redraw_in()` for that moment
  and `ctx.changed()`.

### 7. Icons

From the attached silhouette (64×64 GIF, now at
`C:/Users/tsyga/AppData/Local/Temp/claude/090bb6c475681199/f9407c6d-65ae-4ae8-8e11-c230486024ef/images/1.gif`; to be copied to
`services/scenes/icons/source.gif`):
`scene.png` (colour) and `scene_off.png` (gray), 112×112, used through
`ctx.icon()`. **(call)** colour = white figure on a deep red tile; gray =
`#5e5e5e` figure on the house neutral `#262626`. Other icon options come
later from the owner, so the two file names are the only coupling.

### 8. Logging

Through `ctx.log`, one line each: film noticed (title, runtime, app);
database hit/miss; per source: status, scene count, requests made (no URLs);
merge result; key ready / not ready with the reason (no scenes, none ahead,
app cannot seek); each press with from → to. Metrics come free from the host
(`http:<source>_<step>` op names).

### 9. Config (`services.scenes` in `presets/tv_room.json`)

`atv`, `exclude_apps`, `min_film_s`, `settle_s`, `sources`, `app_services`,
`categories`, `lead_s`, `guard_s`, `join_gap_s`, `request_gap_s`,
`max_lookups_per_day`, `seek_apps`, `seek_hold_s`, the three TTLs. No toasts in this version **(call)**.

## Testing

- Parsers: from saved pages in `services/scenes/tests/fixtures/` (NSFWhen
  film page with the submitter data removed; a VideoSkip skip file with the
  image removed; search and film pages). The VideoSkip search page **with
  hits** has not been seen yet: capture it with one live request for a title
  known to have a skip file.
- Service: `fake_context` with HTTP fakes: film noticed → lookups → key
  ready; cache reuse (no second request); miss TTL; blocked source with the
  other still used; ambiguous match; press picks the next scene and seeks
  with the lead; no scene ahead → not ready; app without seek → not ready;
  settle time; daily cap.
- No test touches the live sites.
- Pages saved during the research (NSFWhen home, browse, search, two film
  pages; VideoSkip home, film page, skip page, one skip file, an empty
  search; AZNude home and film page) are in the session scratchpad
  `C:/Users/tsyga/AppData/Local/Temp/claude/090bb6c475681199/f9407c6d-65ae-4ae8-8e11-c230486024ef/scratchpad/src`.
  Copy the needed ones into the fixtures folder, cleaned as above; if that
  folder is gone, recapture them.
- Capture budget **(call)**: up to 8 more live requests to VideoSkip, 3 s
  apart, to settle what the research did not see: the search page with
  hits; a film page that lists skip files (are filters, synced-with and
  download counts on it, or only on each skip page?); a skip file synced
  with Max or Disney+ if one exists (service names, a non-zero offset).
  The meaning and sign of the offset line come from the VideoSkip
  extension's open source code, not from guessing; until that is read,
  offsets are not applied and only the lead covers the difference.
- On the TV: one command at a time on the owner's go (existing rule); check
  one film in Max or Disney+.

## Decided: TMDB key (owner, 2026-11-13)

A free TMDB API key will be on the device (`ctx.secret("tmdb_key")`), so the
normal path is: TMDB gives year, director, cast, runtime and ids, and every
source match is checked against them. If the key is missing or TMDB fails,
the service logs it once and accepts only matches it can still check
(NSFWhen by runtime); VideoSkip title-only matches are not accepted.

## Rollout

Bump `version` in `config.yaml`, run `restore.sh`, add a journal entry and
keep `restore.sh` reproducing the live state. `tmdb_key`, if wanted, goes in
`/config/secrets.json` on the device by hand.

## Left to the builder

Schema details, title normalisation rules, module layout inside
`services/scenes/`, exact log wording, how the icons are generated (Pillow
one-off or a `build_icons.py` entry).
