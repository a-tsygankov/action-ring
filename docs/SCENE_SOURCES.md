# Scene timecode sources

Where the planned `scenes` service can get scene timecodes for a film. This is
a living document: update a source's section whenever it is looked at again,
and add new sources at the end of the table.

Last checked: 2026-11-13, from a home connection, about 20 requests in total
(1-5 per site, spaced 2-3 s apart, with a desktop browser `User-Agent`).

## Summary

| Source | Timecodes | Films | Bot protection | API | Use |
|---|---|---|---|---|---|
| [VideoSkip exchange](#videoskip-exchange-new) (new) | Exact, to the second or better, with start and end | Skip-file ids run to ~5,950; film ids to ~9,600 (many have requests only) | None seen (Heroku, nginx) | No; plain HTML and a text download | **Primary** |
| [NSFWhen](#nsfwhen) | Start and end, but rounded to whole minutes | 707 films, only 75 scene marks (about 30 films) | None seen; age gate is client-side only | No; film data is JSON embedded in the page | **Secondary** |
| [nudidex](#nudidex) | Reported exact (not seen) | Reported small | Cloudflare challenge blocks curl and a real browser | Unknown | **Not usable** now |
| [AZNude](#aznude) | None: clip lengths only | Very many | Behind Cloudflare, no challenge seen | No; a search form | Scene count only; key stays gray |
| [Web image search](#web-search-google--duckduckgo) | None | n/a | Search engines block automated queries | Paid APIs only | Not planned |
| [Gemini with a screenshot](#gemini-query-with-a-screenshot-tried-2026-11-13) (tried) | Model's estimate; minutes off, sometimes an hour | Any well-known film | None (official API, paid key) | Yes | Not usable alone; maybe a low-confidence last resort |
| [IMDb parents guide](#imdb-parents-guide-new) (new) | Occasional, free text | Very many | Strong (WAF) | No free API | Not planned |

Only sources with timecodes can light the key. A film known only to AZNude
keeps the key gray (decided 2026-11-13); the scene count may be stored.

Both usable sources identify films by TMDB or IMDb ids, so those are the
natural keys for the local database.

## VideoSkip exchange (new)

- **URL**: `https://videoskip.org/exchange/` (redirects to
  `videoskip.herokuapp.com`). A free, open-source browser extension; users
  share "skip files". Login is needed only to upload or request.
- **Protection**: none seen. `robots.txt` not checked.
- **Search**: `GET /exchange/search/?q=<title>`. Captured 2026-11-13 with
  hits ("tuner"): the page has the sections "Series with existing videos",
  "Videos with skip files", "Videos with requests" and "Series without videos".
  Each hit is a table row `<tr><td><a href="/exchange/videos/<id>/">Title
  (year)</a></td></tr>` under the `<h3>Videos with skip files </h3>` heading,
  before the "Videos with requests" heading (rows there have no skip file: do
  not read them). A miss says "No videos matching your search with a skip
  file". The title text carries the year, which separates remakes.
- **Film page**: `/exchange/videos/<id>/`. `<h1>` title, content rating,
  release date, genres, a link to `imdb.com/title/tt<digits>` (and its
  `/parentalguide`), then `<h3>Skip Files:</h3>` followed by a table with one
  `<tr>` per skip file: `<a href="/exchange/skip/<id>/">` , uploader link,
  upload date, `Filters: <text>` (for example "Sex and Nudity for Adults"),
  "<n> downloads", and a form whose action is `/exchange/skip/<id>/download/`.
  A film without skip files has no such table (see the noskips fixture). The
  film page does NOT say which streaming service a skip file is for.
- **Skip page**: `/exchange/skip/<id>/`. Uploader, upload date, download
  count, "Synced with: Amazon Prime Video", "Filters: Sex and Nudity for
  Adults", a free-text note. Only this page names the sync service; the
  download carries the same in the offsets line.
- **Skip file**: `GET /exchange/skip/<id>/download/`, `text/plain`, named like
  `Tuner_2026_amazon.skp`, about 18-30 KB (mostly an embedded image). Layout:

  ```
  0:00:03.95                      <- time of the sync screenshot
  <optional sync note line>

  0:49:03 --> 0:49:06             <- one block per edit: start --> end
  Nudity 3 video                  <- category, severity 1-3, optional handling word
  
  0:54:46 --> 0:55:29
  Sex 3                           <- handling word may be absent (= skip)

  {"amazon":0}                    <- offset in seconds per streaming service

  data:image/jpeg;base64,...      <- the sync screenshot (ignore; do not store)
  ```

- **Parsing notes**: split the text at `data:image/jpeg;base64,` and then at the
  first `{`: before it are the skip blocks, after it the offsets JSON (the
  extension does exactly this). Categories follow IMDb's parents guide, so
  keep only the nudity/sex blocks. Block text is free: in the extension
  (`content2.js`, `setActions`) a text containing `//` means "ignore this
  block", words starting `aud|sou|spe|wor|mut` mean mute, `vid|ima|img|bla`
  blank the picture, `blu` blur, `fas` fast-forward, none of them = skip; for
  locating a scene all of these still mark the scene. Two files seen
  (Tuner, Weapons) have one and one block each; the Weapons block is a 43 s
  range.
- **Offsets line (from the extension source, fruiz500/VideoSkip-extension)**:
  `{"<service>": seconds}`. When the extension loads a file on service S it
  adds `offsets[S]` to every start and end time (`cuts[i].startTime +=
  offset`). So the times in the file are for the service that made the file
  (its own entry is 0) and `time_on_S = file_time + offsets[S]`: a positive
  offset means the scene is LATER on S than in the file. A service not listed
  has no known offset: the extension then makes the user sync by hand against
  the screenshot; the service must not guess. Exception hard-coded in the
  extension: on `disneyplus` with no entry, offset = +20 s. Service name =
  the second-level domain of the page host (`amazon`, `netflix`, `disneyplus`,
  `hulu`, `apple`, `pluto`, `max`/`hbomax` presumably for max.com; also
  `local` and `IPnumber`). Seen in captures: only `amazon` (value 0, both
  files). The download name's `[a-b]` tag in older files lists the service keys.
  Not established: values for Max / Disney+ in real files (no such skip file
  was found in the 6 requests made).
- **Quality**: user-made; download counts are a rough trust signal. Skips are
  often short (a few seconds). Aimed at hiding content, so coverage leans to
  family-audience titles.
- **Cost per film**: search + film page + one download per skip file = 3 or
  more requests.

## NSFWhen

- **URL**: `https://nsfwhen.com`. Laravel (PHP 8.3) behind Hostinger's CDN.
  "707 films · 75 scene marks · 75 verified". Text and timestamps only.
- **Protection**: none seen. The 18+ gate is a script (`age-gate.js`) drawn
  over the page; the HTML already holds the data. Sets session cookies that
  are not needed for reading.
- **Search**: `GET /browse?q=<title, cast or director>`; hits are links to
  `/film/<id>`. `/browse?category=sex_scene|nudity|suggestive` lists only the
  marked films (34 / 12 / 29), 18 per page, `&page=N`.
- **Film page**: `/film/<id>`. An inline script calls
  `initTimeline('#timeline-mount', { film: {...}, scenes: [...],
  runtimeSeconds: N, ... })`. `film` has `tmdb_id`, `title`,
  `original_title`, `release_date`, `runtime_minutes`, `genres`, `director`,
  `cast` (5 names), and `scenes`: `start_time_seconds`, `end_time_seconds`,
  `category` (`nudity`, `sex_scene`, `suggestive`), `verification_status`.
- **Parsing notes**: cut the `film: {...}` object out of the script and parse
  it as JSON. The scene records also carry the submitter's account details:
  **never store or log them**; keep only times, category and status.
- **Quality**: the film seen had three scenes at 30:00-38:00, 54:00-62:00 and
  76:00-83:00: whole minutes and long ranges, all by one editor. Good for
  "roughly here", not for an exact jump. Most of the 707 films have no marks.
- **Cost per film**: search + film page = 2 requests. The whole marked set is
  about 5 list pages.

## nudidex

- **URL**: `https://www.nudidex.com/` (`nudidex.com` redirects there).
- **Protection**: Cloudflare managed challenge ("Just a moment...", HTTP 403)
  for curl and for a real desktop browser. `robots.txt` is empty.
- **Status**: not analysed further. The service must not try to get past the
  challenge. Recheck occasionally (one request) in case the setting is relaxed.
- Not indexed by the web search used, so nothing is known beyond the owner's
  note: exact timecodes, a small number of films.

## AZNude

- **URL**: `https://www.aznude.com`. Behind Cloudflare, but the home page and
  a film page loaded with plain curl (no challenge). `robots.txt` is empty.
- **Search**: `GET /search.html?q=...` (not tried).
- **Film page**: `/view/movie/<first letter>/<slug>.html`, where the slug is
  the title in lower case without spaces (`itgetsworse`). Shows "Title
  (year)", country, and the clips: count, actress and role, clip length
  (`00:35`), thumbnail. Series clips carry season and episode in the file name
  (`01x03`).
- **Timecodes**: none. A clip length is not a position in the film, so this
  source cannot drive a jump.
- **Use**: optionally the number of scenes for a film (one request), as a hint
  that timecodes are worth looking for elsewhere. Very large catalogue.

## Web search (Google / DuckDuckGo)

- Both block automated queries (captcha or empty results) and offer no free
  search API. Image results would give stills, not times.
- Not planned: finding a time from a still means matching it against the
  playing picture, which is a separate project.

## Gemini query with a screenshot (tried 2026-11-13)

Proposed by the owner. Tried by hand with the owner's Gemini API key (about
25 requests, `gemini-pro-latest` and `gemini-flash-latest`, temperature 0).
Reference times are VideoSkip skip files fetched for the test.

- **API**: official Gemini API (`generativelanguage.googleapis.com`), image +
  text in one request, optional Google Search grounding. Needs a key with
  credits (a depleted prepaid project answers 402). No bot protection. One
  request with search took up to a minute or more (one timed out at 90 s).

**Film name and year only, no image**

| Film | Reference (VideoSkip) | Pro, from memory | Pro, with search | Flash, from memory |
|---|---|---|---|---|
| Titanic (1997), drawing scene | 1:22:04 | 1:22:50 (46 s late) | 1:21:19 (45 s early) | 1:25:30 (3.5 min late) |
| Titanic, car scene | 1:30:34 | 1:28:40 (2 min early) | 1:34:31 (4 min late) | 1:36:15 (5.5 min late) |
| Oppenheimer (2023), first | 0:23:13 | 0:28:40 (5.5 min late) | 0:23:11 (exact) | 0:28:40 |
| Oppenheimer, second | 1:14:49 | 1:14:20 (30 s early) | 0:29:07 (wrong) | 1:34:10 (20 min off) |
| Oppenheimer, third | 1:15:29 | 2:15:45 (an hour off) | 1:15:30 (exact) | 2:19:45 (an hour off) |
| Tuner (2026), Weapons (2025), Dracula (2025) | known | no times given | no times given ("scene exists, times not published") | not run |

- From memory the model is minutes off and sometimes an hour off, with
  "medium" or "high" confidence either way: its confidence says nothing.
- With search it is exact only when it finds a page that lists times (here a
  Reddit thread); that page is then the real source. For recent films it
  found none and said so instead of inventing times, when the prompt allowed
  an empty answer.
- Flash is clearly worse than Pro.

**With a still (AZNude thumbnail) plus name and year**

- Three answers for two Titanic stills of the drawing scene (reference
  1:22:04-1:24:09): 1:24:10, 1:24:42, 1:26:25. So it recognises the scene and
  lands within about 0-2.5 min after it; no better than the text-only
  question, and late rather than early.
- No refusal: the nude stills were accepted (3 of 3).
- AZNude: the Titanic page listed stills (`data-thumb`); the Oppenheimer page
  listed none in that markup, so stills are not always available.

**Verdict**

- Not good enough to light the key on its own: an error of minutes, in either
  direction, with no usable confidence signal.
- Possible later use, as a last-resort source: Pro with search, name and year
  only (the still adds nothing), stored as low confidence, used only when no
  timecode source has the film, and with a lead of a few minutes so the jump
  lands before the scene. One request per film.
- Cheaper and more exact for the same films: Reddit threads with timestamps
  are what search found; a direct search of such threads would be a source of
  its own (not analysed).

## IMDb parents guide (new)

- `imdb.com/title/tt…/parentalguide`: user-written notes per category, with a
  time mentioned in some of them as free text. Huge coverage, low precision,
  strong bot protection, no free API. Listed for completeness; not planned.

## Also looked at

- **moviecontentfilter.com**, **playitmyway.org**: did not answer (no
  connection) on 2026-11-13.
- **ClearPlay**, **VidAngel**: subscription services, excluded (no membership).
- **Filmiway**, **notrigger4.me**: apps that advertise skip timelines; not
  checked.

## Rules for the service

- At most one lookup per film per source, cached (hits and misses) in the
  local database; misses are retried after a long delay, not on every play.
  Config: `hit_ttl_days` (90), `miss_ttl_days` (14); `max_lookups_per_day`
  (30) caps new films looked up per day.
- A fixed pause between requests to the same site and one request at a time
  per site. Config: `request_gap_s` (3).
- A challenge page or a 403/429 marks the source as blocked for a day; the
  other sources are still used. Config: `blocked_ttl_days` (1).
- During development, work from saved pages (test fixtures), not the live
  sites.
