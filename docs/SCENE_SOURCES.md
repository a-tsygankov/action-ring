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
- **Search**: `GET /exchange/search/?q=<title>`. The result lists films with
  skip files and films with requests only. A miss says "No videos matching
  your search with a skip file". Not yet seen: the markup of a result with
  hits (the one test query, "babylon", had none).
- **Film page**: `/exchange/videos/<id>/`. Title, content rating, release
  date, genres, a link to `imdb.com/title/tt…`, the list of skip files, and
  pending requests.
- **Skip page**: `/exchange/skip/<id>/`. Uploader, upload date, download
  count, "Synced with: Amazon Prime Video", "Filters: Sex and Nudity for
  Adults", a free-text note.
- **Skip file**: `GET /exchange/skip/<id>/download/`, `text/plain`, named like
  `Tuner_2026_amazon.skp`, about 30 KB (mostly an embedded image). Layout:

  ```
  0:00:03.95                      <- time of the sync screenshot

  0:49:03 --> 0:49:06             <- one block per edit: start --> end
  Nudity 3 video                  <- category, severity 1-3, what to hide

  {"amazon":0}                    <- offset in seconds per streaming service

  data:image/jpeg;base64,...      <- the sync screenshot (ignore; do not store)
  ```

- **Parsing notes**: read blocks until the JSON line; stop before the `data:`
  line. Categories follow IMDb's parents guide (sex/nudity, violence,
  profanity, ...), so keep only the nudity/sex blocks. A film may have several
  skip files from different users and services: prefer the one synced with
  the playing service, else apply the per-service offset, else use it as is.
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
- A fixed pause between requests to the same site and one request at a time
  per site.
- A challenge page or a 403/429 marks the source as blocked for a day; the
  other sources are still used.
- During development, work from saved pages (test fixtures), not the live
  sites.
