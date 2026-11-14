# Scene Jump Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dock key that seeks a film on the Apple TV to its next nude/sex scene, using timecodes looked up once per film from VideoSkip and NSFWhen and cached in SQLite.

**Architecture:** A new service package `services/scenes/` on the existing service contract. A 5 s `watch` job notices a film from cached Home Assistant state and runs a lookup (SQLite cache → TMDB identity → one module per website → merge); the render path only reads an immutable in-memory snapshot. A press calls `media_player.media_seek`.

**Tech Stack:** Python 3.12 stdlib only (`sqlite3`, `json`, `re`, `html.parser`, `urllib.parse`), pytest, Pillow (icons, on the PC only).

**Spec:** `docs/superpowers/specs/2026-11-13-scene-jump-design.md`. Source facts: `docs/SCENE_SOURCES.md`. Contract: `homeassistant/streamdock_n3/services/README.md` (read it first; `services/soccer/` is the worked example).

## Global Constraints

- No edits to `ha_daemon.py`, `tabs.py`, `keyspec.py`, `Dockerfile`.
- Render-path methods (`key`, conditions, `vars`, `on_state`) do no I/O, take no lock held across I/O, and never call `ctx.changed()`/`ctx.context_changed()`.
- All HTTP goes through `ctx.http.get(url, op=..., headers=..., timeout=15)`. Never log URLs, response bodies, the TMDB key, or personal data from pages.
- No test and no development step requests a live site, except Task 2 Step 1 (at most 8 requests to VideoSkip, 3 s apart).
- Never try to pass a bot check. A challenge page, 403 or 429 means status `blocked`.
- Time comes from `ctx.clock()` / `ctx.monotonic()` / `ctx.sleep()`, never `time`.
- Defaults (all overridable in config): `min_film_s` 3000, `settle_s` 10, `lead_s` 5, `guard_s` 2, `join_gap_s` 20, `seek_hold_s` 5, `request_gap_s` 3, `max_lookups_per_day` 30, `hit_ttl_days` 90, `miss_ttl_days` 14, `blocked_ttl_days` 1, `categories` `["nudity", "sex"]`, `sources` `["videoskip", "nsfwhen", "nudidex"]`, `seek_apps` `["com.wbd.stream", "com.disney.disneyplus"]`, `exclude_apps` `["com.google.ios.youtube"]`.
- Run tests from the repo root: `python -m pytest homeassistant/streamdock_n3 -q`. Test files live in `services/scenes/tests/` with unique names (`test_scenes_*.py`).
- Commit after each task on a branch `feat/scene-jump` (create it before Task 1; the design, plan and `docs/SCENE_SOURCES.md` go in the first commit). End commit messages with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Review Focus

1. A source's page markup changed or came back truncated: that source reports `error`, the others are still used, nothing raises out of the job. (Tasks 3, 4, 8)
2. The film changes while its lookup is still running: the finished lookup must not light the key for the new film. (Task 9)
3. The app reports no `media_position` (or no `media_duration`): the key is gray and a press does nothing. (Task 9)
4. Titles as apps really send them: trailing year, accents, `&` vs `and`, a leading article, an edition suffix (`"Babylon (2022)"`, `"Amélie"`, `"Fast & Furious"`, `"Blade Runner: The Final Cut"`). (Task 1)
5. A skip file with odd content: no nudity blocks, a block whose end precedes its start, times as `M:SS` or with fractions, Windows line ends. (Task 3)

## File Structure

All under `homeassistant/streamdock_n3/services/scenes/`:

| File | Responsibility |
|---|---|
| `__init__.py` | `from .service import ScenesService as SERVICE` |
| `model.py` | `Film`, `Scene`, `SourceResult`, `norm_title`, `parse_time` |
| `fetch.py` | `Fetcher`: per-site request gap, browser headers, `Blocked` detection |
| `sources/__init__.py` | `SOURCES` registry: name → `lookup` function |
| `sources/videoskip.py`, `sources/nsfwhen.py`, `sources/nudidex.py` | One site each: pure parsers + `lookup` |
| `tmdb.py` | Title + runtime → identified `Film` |
| `match.py` | Pick a source candidate against a `Film` |
| `db.py` | SQLite cache: films, lookups, scenes |
| `merge.py` | Source scenes → sorted start/end tuple |
| `lookup.py` | Orchestration: cache → TMDB → sources → store → merged, TTLs, daily cap, logging |
| `service.py` | The contract: `watch` job, `ready` condition, `next` key and action, position logic |
| `icons/source.gif`, `icons/scene.png`, `icons/scene_off.png` | Key art |
| `tests/fixtures/*`, `tests/test_scenes_*.py` | Saved pages and tests |

---

### Task 1: Model and title normalisation

**Files:**
- Create: `services/scenes/__init__.py` (empty for now), `services/scenes/model.py`, `services/scenes/tests/test_scenes_model.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Film:
      title: str; norm: str; runtime_s: int | None = None; year: int | None = None
      director: str | None = None; cast: tuple[str, ...] = ()
      tmdb_id: int | None = None; imdb_id: str | None = None   # imdb_id like "tt1234567"
  @dataclass(frozen=True)
  class Scene:
      start_s: float; end_s: float; category: str   # "nudity" | "sex" | "suggestive"
      source: str = ""; synced_with: str = ""; note: str = ""
  @dataclass(frozen=True)
  class SourceResult:
      status: str                      # "hit" | "miss" | "ambiguous" | "blocked" | "error"
      scenes: tuple[Scene, ...] = ()
      meta: dict = field(default_factory=dict)   # any of year, runtime_s, director, cast, tmdb_id, imdb_id
      requests: int = 0
  def norm_title(title: str) -> str
  def parse_time(text: str) -> float      # "0:49:03", "49:03", "0:00:03.95" -> seconds; ValueError otherwise
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_norm_title():
    assert norm_title("Babylon (2022)") == "babylon"
    assert norm_title("  The  Matrix ") == "matrix"
    assert norm_title("Amélie") == "amelie"
    assert norm_title("Fast & Furious") == norm_title("Fast and Furious") == "fast and furious"
    assert norm_title("Blade Runner: The Final Cut") == "blade runner the final cut"
    assert norm_title("Se7en") == "se7en"
    assert norm_title("") == ""

def test_parse_time():
    assert parse_time("0:49:03") == 2943
    assert parse_time("49:03") == 2943
    assert parse_time("0:00:03.95") == 3.95
    with pytest.raises(ValueError):
        parse_time("soon")
```

- [ ] **Step 2: Run** `python -m pytest homeassistant/streamdock_n3/services/scenes -q` — expect import errors.
- [ ] **Step 3: Implement `model.py`.** `norm_title`: NFKD and drop combining marks, lower case, `&` → ` and `, drop a trailing `(YYYY)`, replace non-alphanumerics with spaces, drop one leading article (`the`, `a`, `an`), collapse spaces.
- [ ] **Step 4: Run** — expect pass.
- [ ] **Step 5: Commit** (`scenes: model and title normalisation`, including the design, plan and `docs/SCENE_SOURCES.md`).

---

### Task 2: Fixtures and the polite fetcher

**Files:**
- Create: `services/scenes/fetch.py`, `services/scenes/tests/fixtures/` (files below), `services/scenes/tests/test_scenes_fetch.py`
- Modify: `docs/SCENE_SOURCES.md` (VideoSkip section: what the capture showed)

**Interfaces:**
- Produces:
  ```python
  class Blocked(Exception): ...
  class Fetcher:
      def __init__(self, ctx, gap_s: float = 3.0): ...
      def get(self, site: str, url: str, *, op: str) -> str
      # waits (ctx.sleep) so calls for the same `site` are >= gap_s apart; sends UA + Accept-Language;
      # raises Blocked on HTTPError 403/429 or a body containing "Just a moment..." / "challenge-platform";
      # other errors propagate. `count` attribute: requests made since construction.
  UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
  ```

- [ ] **Step 1: Collect fixtures.** Copy from `C:/Users/tsyga/AppData/Local/Temp/claude/090bb6c475681199/f9407c6d-65ae-4ae8-8e11-c230486024ef/scratchpad/src` (recapture if gone, 3 s apart):
  - `nsfwhen_film2.html` → `nsfwhen_film_scenes.html`; `nsfwhen_film.html` → `nsfwhen_film_empty.html`; `nsfwhen_q.html` → `nsfwhen_search.html`. In all three, delete every `"submitter":{...}` object and any e-mail address (verify: `grep -c "@" fixture` is 0 apart from none expected).
  - `vs_5955.skp` → `videoskip_tuner.skp` with everything from the `data:image` line on replaced by `data:image/jpeg;base64,AAAA`; `vs_skip.html` → `videoskip_skip.html`; `vs_video.html` → `videoskip_film_noskips.html`; `vs_search.html` → `videoskip_search_empty.html`; `nudidex_home.html` → `challenge.html`.
  - Capture live from `https://videoskip.herokuapp.com` (max 8 requests): `/exchange/search/?q=tuner` → `videoskip_search_hits.html`; the film page it links (`/exchange/videos/9473/`) → `videoskip_film_skips.html`; if `/exchange/recent-movie-skips/` (1 request) shows a skip synced with Max/HBO or Disney+, its skip page and download → `videoskip_skip_max.html`, `videoskip_max.skp` (image stripped). Stop at 8 requests whatever was found.
  - Read the VideoSkip extension's source (its repository is linked from the site's About/Credits page; reading code on GitHub does not count against the 8) to learn the meaning and sign of the `{"service": seconds}` line and the list of service names. Record the findings in `docs/SCENE_SOURCES.md`. If it cannot be established, write that, and Task 3 does not apply offsets.
- [ ] **Step 2: Write the failing tests**

```python
def test_gap_between_requests_to_one_site():
    ctx = fake_context("scenes", {}, http={"a.test": "ok", "b.test": "ok"})
    f = Fetcher(ctx, gap_s=3)
    t0 = ctx.monotonic()
    f.get("a", "https://a.test/1", op="x"); f.get("a", "https://a.test/2", op="x")
    assert ctx.monotonic() - t0 == 3            # slept once
    f.get("b", "https://b.test/1", op="x")
    assert ctx.monotonic() - t0 == 3            # another site: no wait
    assert f.count == 3

def test_challenge_page_and_403_are_blocked():
    body = (FIX / "challenge.html").read_text(encoding="utf-8")
    err = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
    for reply in (body, err, urllib.error.HTTPError("u", 429, "Too Many", {}, None)):
        ctx = fake_context("scenes", {}, http={"a.test": reply})
        with pytest.raises(Blocked):
            Fetcher(ctx).get("a", "https://a.test/", op="x")

def test_other_errors_propagate():
    ctx = fake_context("scenes", {}, http={"a.test": TimeoutError()})
    with pytest.raises(TimeoutError):
        Fetcher(ctx).get("a", "https://a.test/", op="x")
```

- [ ] **Step 3: Run** — expect failures. **Step 4: Implement `fetch.py`.** **Step 5: Run** — expect pass.
- [ ] **Step 6: Commit** (`scenes: fixtures and polite fetcher`).

---

### Task 3: VideoSkip source

**Files:**
- Create: `services/scenes/match.py`, `services/scenes/sources/__init__.py`, `services/scenes/sources/videoskip.py`, `services/scenes/tests/test_scenes_videoskip.py`, `services/scenes/tests/test_scenes_match.py`

**Interfaces:**
- Consumes: `Film`, `Scene`, `SourceResult`, `norm_title`, `parse_time`, `Fetcher`, `Blocked`.
- Produces:
  ```python
  # match.py
  @dataclass(frozen=True)
  class Candidate:
      ref: str; title: str; year: int | None = None; runtime_s: int | None = None
      director: str | None = None; cast: tuple[str, ...] = ()
      tmdb_id: int | None = None; imdb_id: str | None = None
  def pick(film: Film, candidates: list[Candidate]) -> tuple[Candidate | None, str]
  # -> (candidate, "hit") | (None, "miss") | (None, "ambiguous")

  # sources/videoskip.py
  def parse_search(html: str) -> list[Candidate]          # only films that have skip files; ref = "/exchange/videos/<id>/"
  def parse_film(html: str) -> tuple[dict, list[str]]     # (meta {year, imdb_id}, skip refs "/exchange/skip/<id>/")
  def parse_skip_page(html: str) -> dict                  # {"synced_with": str, "filters": str, "downloads": int}
  def parse_skip_file(text: str) -> tuple[list[Scene], dict]   # (all blocks, offsets dict or {})
  def lookup(fetch: Fetcher, film: Film, cfg: dict) -> SourceResult   # cfg: {"service": "<videoskip name>" | None}
  # sources/__init__.py
  SOURCES: dict[str, Callable[[Fetcher, Film, dict], SourceResult]]
  ```
- `pick` rules (spec §2): keep candidates with `norm_title(c.title) == film.norm`; reject any whose `tmdb_id`/`imdb_id` differs from a known film id, whose year differs by more than 1 from a known film year, or whose runtime differs by more than 180 s from a known film runtime — even a lone one. A candidate needs **at least one confirmed field** (id, year or runtime equal within those tolerances); title alone is not a hit. None left → `miss`. One → `hit`. Several → the first rule that leaves exactly one: same id; same year; runtime within 180 s; same director (normalised); ≥ 2 cast names in common; else `ambiguous`.
- `lookup` flow: search (1 request) → `pick` → film page → up to 3 skip pages (most recent ids first) to read filters, synced-with and downloads → choose the skip file whose filters mention sex/nudity, preferring `synced_with` equal to `cfg["service"]`, else most downloads → download it. Hard cap: 6 requests. Scenes returned: only blocks whose category line starts with `Nudity` or `Sex`, mapped to `"nudity"`/`"sex"`, `source="videoskip"`, `synced_with` set. Offsets are applied only as Task 2 Step 1 established. `Blocked` → `SourceResult("blocked")`; any other exception or unparseable page → `SourceResult("error")`; a film without a sex/nudity skip file → `miss`. `requests` always filled from the fetcher.

- [ ] **Step 1: Write the failing tests**

```python
FILM = Film("Tuner", "tuner", runtime_s=6600, year=2026)

def test_pick_rules():
    c = lambda **k: Candidate(ref="r", title="Tuner", **k)
    assert pick(FILM, [c(year=2026)])[1] == "hit"
    assert pick(FILM, [c(year=1999)])[1] == "miss"          # lone but contradicting
    assert pick(FILM, [c()])[1] == "miss"                   # title alone is not enough
    assert pick(FILM, [c(year=2026), c(year=2025)])[1] == "ambiguous"
    assert pick(FILM, [c(year=2026, runtime_s=6500), c(year=2026, runtime_s=3000)])[0].runtime_s == 6500
    assert pick(FILM, [Candidate("r", "Tuna", year=2026)])[1] == "miss"

def test_parse_skip_file():
    scenes, offsets = parse_skip_file((FIX / "videoskip_tuner.skp").read_text())
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [(2943, 2946, "nudity")]
    assert offsets == {"amazon": 0}

def test_parse_skip_file_odd_content():
    text = "0:00:01\r\n\r\n1:05 --> 1:10.5\r\nSex 2 video\r\n\r\n0:20:00 --> 0:19:00\r\nNudity 1 video\r\n\r\n0:30:00 --> 0:30:05\r\nProfanity 2 audio\r\n"
    scenes, offsets = parse_skip_file(text)
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [(65, 70.5, "sex")]   # reversed block and profanity dropped
    assert offsets == {}
    assert parse_skip_file("") == ([], {})

def test_parse_pages():
    assert parse_search((FIX / "videoskip_search_empty.html").read_text()) == []
    hits = parse_search((FIX / "videoskip_search_hits.html").read_text())
    assert any(norm_title(c.title) == "tuner" and c.ref.startswith("/exchange/videos/") for c in hits)
    meta, skips = parse_film((FIX / "videoskip_film_skips.html").read_text())
    assert skips and all(s.startswith("/exchange/skip/") for s in skips)
    assert parse_film((FIX / "videoskip_film_noskips.html").read_text())[1] == []
    assert parse_skip_page((FIX / "videoskip_skip.html").read_text()) == {
        "synced_with": "Amazon Prime Video", "filters": "Sex and Nudity for Adults", "downloads": 124}

def test_lookup_hit_miss_blocked_error():
    http = {"/search/": SEARCH_HITS, "/exchange/videos/": FILM_SKIPS, "/download/": SKP, "/exchange/skip/": SKIP_PAGE}
    r = lookup(Fetcher(fake_context("scenes", {}, http=http), gap_s=0), FILM, {"service": None})
    assert r.status == "hit" and r.scenes[0].start_s == 2943 and 3 <= r.requests <= 6
    r = lookup(Fetcher(fake_context("scenes", {}, http={"/search/": SEARCH_EMPTY}), gap_s=0), FILM, {})
    assert (r.status, r.requests) == ("miss", 1)
    r = lookup(Fetcher(fake_context("scenes", {}, http={"/search/": CHALLENGE}), gap_s=0), FILM, {})
    assert r.status == "blocked"
    r = lookup(Fetcher(fake_context("scenes", {}, http={"/search/": "<html><body>"}), gap_s=0), FILM, {})
    assert r.status in ("miss", "error")
    r = lookup(Fetcher(fake_context("scenes", {}, http={"/search/": TimeoutError()}), gap_s=0), FILM, {})
    assert r.status == "error"
```

  (Order the `http` dict so `/download/` is matched before `/exchange/skip/`; adjust the year in `FILM` if the captured film page shows another release year.)

- [ ] **Step 2: Run** — fail. **Step 3: Implement** `match.py`, then `videoskip.py` (parsers with `re`/`html.parser` over the fixtures; register in `SOURCES`). **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: candidate matching and VideoSkip source`).

---

### Task 4: NSFWhen and nudidex sources

**Files:**
- Create: `services/scenes/sources/nsfwhen.py`, `services/scenes/sources/nudidex.py`, `services/scenes/tests/test_scenes_nsfwhen.py`
- Modify: `services/scenes/sources/__init__.py`

**Interfaces:**
- Consumes: Task 1-3 types, `pick`, `Fetcher`.
- Produces: `nsfwhen.parse_search(html) -> list[str]` (film URLs), `nsfwhen.parse_film(html) -> tuple[Candidate, list[Scene]]`, `nsfwhen.lookup(fetch, film, cfg) -> SourceResult`, `nudidex.lookup(fetch, film, cfg) -> SourceResult`; both registered in `SOURCES`.
- NSFWhen flow: `GET https://nsfwhen.com/browse?q=<title>` → for at most 3 result films, fetch the film page and build a `Candidate` from the embedded JSON (`tmdb_id`, `release_date` year, `runtime_minutes`, `director`, `cast`) → `pick` → scenes with category mapped `nudity`→`nudity`, `sex_scene`→`sex`, `suggestive`→`suggestive`, `source="nsfwhen"`. Extract the `film: {...}` object by brace matching from `initTimeline(`, then `json.loads`. Read only: ids, title, release_date, runtime_minutes, director, cast, and per scene start/end/category/verification_status. `meta` carries year, runtime_s, director, cast, tmdb_id. A matched film with no scenes → `miss`. Cap: 4 requests.
- nudidex: returns `SourceResult("blocked")` with 0 requests unless `cfg.get("probe")`; with `probe`, one GET of the home page → `blocked` on `Blocked`, else `miss` (no parser exists yet).

- [ ] **Step 1: Write the failing tests**

```python
DRACULA = Film("Dracula", "dracula", runtime_s=7800, tmdb_id=1246049)

def test_parse_film_with_scenes():
    cand, scenes = parse_film((FIX / "nsfwhen_film_scenes.html").read_text(encoding="utf-8"))
    assert (cand.tmdb_id, cand.runtime_s) == (1246049, 7800)
    assert [(s.start_s, s.end_s, s.category) for s in scenes] == [
        (1800, 2280, "nudity"), (3240, 3720, "suggestive"), (4560, 4980, "sex")]
    assert all(s.note == "" for s in scenes)

def test_parse_film_without_scenes_and_search():
    cand, scenes = parse_film((FIX / "nsfwhen_film_empty.html").read_text(encoding="utf-8"))
    assert (cand.title, cand.year, cand.director, scenes) == ("Babylon", 2022, "Damien Chazelle", [])
    assert parse_search((FIX / "nsfwhen_search.html").read_text(encoding="utf-8")) == ["https://nsfwhen.com/film/399"]

def test_lookup():
    http = {"/browse": SEARCH_ONE_HIT_103, "/film/103": FILM_SCENES}
    r = lookup(Fetcher(fake_context("scenes", {}, http=http), gap_s=0), DRACULA, {})
    assert (r.status, len(r.scenes), r.requests) == ("hit", 3, 2)
    wrong = Film("Dracula", "dracula", runtime_s=5000, year=1958)
    assert lookup(Fetcher(fake_context("scenes", {}, http=http), gap_s=0), wrong, {}).status == "miss"
    r = lookup(Fetcher(fake_context("scenes", {}, http={"/browse": SEARCH_ONE_HIT_103, "/film/103": "<html>"}), gap_s=0), DRACULA, {})
    assert r.status == "error"                     # markup changed: reported, not raised

def test_nudidex_makes_no_request_by_default():
    ctx = fake_context("scenes", {}, http={})
    assert nudidex.lookup(Fetcher(ctx), DRACULA, {}).status == "blocked" and ctx.fakes.urls == []
```

  (`SEARCH_ONE_HIT_103` is `nsfwhen_search.html` with `/film/399` replaced by `/film/103`.)

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: NSFWhen source and nudidex placeholder`).

---

### Task 5: TMDB identity

**Files:**
- Create: `services/scenes/tmdb.py`, `services/scenes/tests/fixtures/tmdb_search.json`, `tmdb_movie.json`, `services/scenes/tests/test_scenes_tmdb.py`

**Interfaces:**
- Produces: `identify(http, key: str, title: str, runtime_s: int | None) -> Film | None` where `http` is `ctx.http`. Raises on HTTP errors (the caller handles them).
- Calls (v3 API, `key` as the `api_key` query parameter, `op="tmdb_search"` / `"tmdb_movie"`): `https://api.themoviedb.org/3/search/movie?query=<title>&api_key=<key>`; then for up to 3 results whose `norm_title(title)` or `norm_title(original_title)` equals the film's, `https://api.themoviedb.org/3/movie/<id>?append_to_response=credits&api_key=<key>`. Choose the one whose `runtime`×60 is within 180 s of `runtime_s`; with `runtime_s` None, accept only a single title match. Result: `Film(title, norm, runtime_s (TMDB's), year from release_date, director = crew job "Director" (first), cast = first 5 names, tmdb_id, imdb_id)`. No match → `None`.
- Fixtures are hand-written minimal JSON in TMDB's documented shape (two search results titled "Dracula", ids 1246049 and 138; the movie detail for 1246049 with `runtime: 130`, `imdb_id`, `release_date`, `credits`). No live request.

- [ ] **Step 1: Write the failing tests**

```python
def test_identify_picks_by_runtime():
    http = {"search/movie": SEARCH, "movie/1246049": MOVIE_130, "movie/138": MOVIE_82}
    ctx = fake_context("scenes", {}, http=http)
    film = identify(ctx.http, "k", "Dracula", 7800)
    assert (film.tmdb_id, film.year, film.runtime_s, film.norm) == (1246049, 2025, 7800, "dracula")
    assert film.director and len(film.cast) <= 5 and film.imdb_id.startswith("tt")

def test_identify_no_match():
    ctx = fake_context("scenes", {}, http={"search/movie": '{"results": []}'})
    assert identify(ctx.http, "k", "Nope Nope", 7000) is None
    ctx = fake_context("scenes", {}, http={"search/movie": SEARCH, "movie/": MOVIE_82})
    assert identify(ctx.http, "k", "Dracula", 7800) is None      # runtime disagrees everywhere
```

  (Set `release_date` in the fixture to match the asserted year.)

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: TMDB identity`).

---

### Task 6: Database

**Files:**
- Create: `services/scenes/db.py`, `services/scenes/tests/test_scenes_db.py`

**Interfaces:**
- Produces:
  ```python
  class Db:
      def __init__(self, conn: sqlite3.Connection): ...       # creates tables if missing; one internal lock
      def find_film(self, norm: str, runtime_s: int | None) -> tuple[int, Film] | None   # runtime within 180 s when both known
      def save_film(self, film: Film, now: float) -> int      # insert, or update the row found by find_film; returns id
      def lookup_state(self, film_id: int) -> dict[str, tuple[str, float]]   # source -> (status, fetched_at)
      def save_result(self, film_id: int, source: str, result: SourceResult, now: float) -> None  # replaces that source's scenes
      def scenes(self, film_id: int) -> list[Scene]
      def lookups_since(self, since: float) -> int             # distinct films with a lookup row fetched after `since`
  ```
- Tables as spec §3 (`films`, `lookups`, `scenes`); `cast` stored as JSON. All calls go through one `threading.Lock` (jobs and actions share the connection).

- [ ] **Step 1: Write the failing tests** (use `sqlite3.connect(":memory:", check_same_thread=False)`)

```python
def test_film_roundtrip_and_runtime_match():
    db = Db(mem()); fid = db.save_film(Film("Dracula", "dracula", 7800, 2025, "X", ("A", "B"), 1, "tt1"), 100.0)
    assert db.find_film("dracula", 7700) == (fid, Film("Dracula", "dracula", 7800, 2025, "X", ("A", "B"), 1, "tt1"))
    assert db.find_film("dracula", 5000) is None
    assert db.find_film("dracula", None)[0] == fid
    assert db.save_film(Film("Dracula", "dracula", 7800, 2025), 200.0) == fid     # same row

def test_results_replace_and_states():
    db = Db(mem()); fid = db.save_film(Film("D", "d", 7800), 0.0)
    db.save_result(fid, "videoskip", SourceResult("hit", (Scene(10, 20, "sex", "videoskip"),)), 50.0)
    db.save_result(fid, "nsfwhen", SourceResult("miss"), 60.0)
    db.save_result(fid, "videoskip", SourceResult("hit", (Scene(30, 40, "nudity", "videoskip"),)), 70.0)
    assert [s.start_s for s in db.scenes(fid)] == [30]
    assert db.lookup_state(fid) == {"videoskip": ("hit", 70.0), "nsfwhen": ("miss", 60.0)}
    assert db.lookups_since(55.0) == 1 and db.lookups_since(80.0) == 0
```

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: SQLite cache`).

---

### Task 7: Merge

**Files:**
- Create: `services/scenes/merge.py`, `services/scenes/tests/test_scenes_merge.py`

**Interfaces:**
- Produces: `merge(scenes: list[Scene], categories: list[str], join_gap_s: float = 20, primary: str = "videoskip") -> tuple[tuple[float, float], ...]` — (start, end) pairs sorted by start.
- Rules (spec §5): keep scenes whose category is in `categories`; take all `primary` scenes; add another source's scene only if it overlaps no primary scene; sort; join a scene into the previous one when its start ≤ previous end + `join_gap_s` (end = max of both).

- [ ] **Step 1: Write the failing test**

```python
def test_merge():
    S = lambda a, b, cat="sex", src="videoskip": Scene(a, b, cat, src)
    out = merge([S(100, 110), S(120, 125), S(1800, 2280, "nudity", "nsfwhen"), S(90, 300, "sex", "nsfwhen"),
                 S(500, 510, "suggestive", "nsfwhen"), S(4000, 4010, "nudity")], ["nudity", "sex"])
    assert out == ((100, 125), (1800, 2280), (4000, 4010))
    assert merge([], ["sex"]) == ()
    assert merge([S(500, 510, "suggestive", "nsfwhen")], ["nudity", "sex", "suggestive"]) == ((500, 510),)
```

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: merge`).

---

### Task 8: Lookup orchestration

**Files:**
- Create: `services/scenes/lookup.py`, `services/scenes/tests/test_scenes_lookup.py`

**Interfaces:**
- Consumes: `Db`, `SOURCES`, `Fetcher`, `tmdb.identify`, `merge`.
- Produces:
  ```python
  class Lookup:
      def __init__(self, ctx, db: Db, sources: dict | None = None, identify=tmdb.identify): ...   # sources defaults to SOURCES
      def run(self, title: str, runtime_s: int | None, app_id: str) -> tuple[tuple[float, float], ...]
  ```
- `run` (never raises; logs through `ctx.log`):
  1. `norm_title`; `db.find_film`. If not found: with a `ctx.secret("tmdb_key")`, `identify(...)` (exception → log `tmdb failed: <ErrorName>`, treat as no identity); no key → log `no tmdb_key in secrets.json` once per process. Film = identified one, else `Film(title, norm, runtime_s)`. An identified film is saved; an unidentified one is saved too (so misses are cached).
  2. For each name in `config["sources"]` (unknown names logged once and skipped): skip when its `lookup_state` is fresh — `hit` < `hit_ttl_days`, `miss`/`ambiguous` < `miss_ttl_days`, `blocked`/`error` < `blocked_ttl_days`.
  3. If any source is due and `db.lookups_since(now - 86400) >= max_lookups_per_day` → log `daily lookup limit reached`, query nothing.
  4. Call each due source with a shared `Fetcher(ctx, request_gap_s)` and `cfg = {**config.get("source_cfg", {}).get(name, {}), "service": config.get("app_services", {}).get(app_id)}`. A source raising → `SourceResult("error")`. Fold a hit's `meta` into the film (fill only missing fields) and `save_film`. `save_result` each. Log per source: `<source>: <status>, <n> scenes, <requests> requests`.
  5. Return `merge(db.scenes(film_id), config categories, join_gap_s)`; log `"<title>": <n> scenes` (from cache or fresh).

- [ ] **Step 1: Write the failing tests** — sources are fakes (`lambda fetch, film, cfg: SourceResult(...)`) recording calls; `identify` is a fake.

```python
def test_first_run_queries_then_cache_serves():
    lk, calls, ctx = make(sources={"videoskip": hit([(100, 110)]), "nsfwhen": miss()})
    assert lk.run("Tuner", 6600, "com.wbd.stream") == ((100, 110),)
    assert calls == ["videoskip", "nsfwhen"]
    assert lk.run("Tuner (2026)", 6600, "com.wbd.stream") == ((100, 110),) and len(calls) == 2   # cached

def test_ttls():
    lk, calls, ctx = make(sources={"videoskip": miss(), "nsfwhen": blocked()})
    lk.run("Tuner", 6600, "a"); ctx.fakes.time.now += 2 * 86400; lk.run("Tuner", 6600, "a")
    assert calls == ["videoskip", "nsfwhen", "nsfwhen"]            # blocked retried after 1 day, miss not
    ctx.fakes.time.now += 14 * 86400; lk.run("Tuner", 6600, "a")
    assert calls[-2:] == ["videoskip", "nsfwhen"]

def test_one_source_failing_does_not_stop_the_other():
    lk, calls, ctx = make(sources={"videoskip": raises(RuntimeError("x")), "nsfwhen": hit([(1800, 2280)])})
    assert lk.run("Dracula", 7800, "a") == ((1800, 2280),)

def test_daily_cap():
    lk, calls, ctx = make(sources={"videoskip": miss()}, config={"max_lookups_per_day": 2})
    for t in ("A1", "B2", "C3"): lk.run(t, 6000, "a")
    assert len(calls) == 2

def test_tmdb_failure_and_missing_key_do_not_raise():
    lk, calls, ctx = make(sources={"videoskip": miss()}, identify=raises(TimeoutError()))
    assert lk.run("Tuner", 6600, "a") == ()
    lk, calls, ctx = make(sources={"videoskip": miss()}, secrets=None)
    assert lk.run("Tuner", 6600, "a") == ()

def test_service_name_passed_to_source():
    lk, calls, ctx = make(sources={"videoskip": recording_cfg}, config={"app_services": {"com.wbd.stream": "max"}})
    lk.run("Tuner", 6600, "com.wbd.stream"); assert seen_cfg["service"] == "max"
```

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** — pass.
- [ ] **Step 5: Commit** (`scenes: lookup orchestration with TTLs and daily cap`).

---

### Task 9: The service

**Files:**
- Create: `services/scenes/service.py`, `services/scenes/tests/test_scenes_service.py`
- Modify: `services/scenes/__init__.py` (`from .service import ScenesService as SERVICE`)

**Interfaces:**
- Consumes: `Lookup`, `Db`.
- Produces: `ScenesService` with `name = "scenes"`, job `watch`, condition `ready`, key `next`, action `next`.
- Behaviour:
  - `start(ctx)`: keep ctx; `Db(ctx.store.sqlite("scenes"))`; `Lookup`. If the database cannot be opened, log once and run with lookups disabled (key gray).
  - `entities()`: `{config["atv"]}`.
  - Snapshot (replaced whole under a short lock, never mutated): `(film_key, app_id, scenes_tuple)` plus the jump memory `(scene_start, at_monotonic)`.
  - `watch` (returns 5): read `ctx.state(atv)`. A film is on when state is `playing`/`paused`, `app_id` not in `exclude_apps`, `media_title` non-empty, and (`media_duration` ≥ `min_film_s` or `media_content_type == "movie"`). Film key = `(norm_title(title), round(duration/60), app_id)`. A key first seen starts the `settle_s` clock; once it has stayed for `settle_s`, log `film: "<title>" <min> min in <app>`, run `Lookup.run` and — **only if the current film key still equals the one looked up** — swap the snapshot, then `ctx.changed()` and schedule the gray-out redraw. No film → clear the snapshot (and `ctx.changed()` if it was set). App not in `seek_apps` with scenes found: log once per film `app cannot seek: key stays gray`.
  - `_position()` (no I/O): `media_position` (+ age of `media_position_updated_at` when `playing`); `None` if missing. If a jump memory exists and the real position has not passed its scene start, the position counts as that scene start; the memory is dropped once the real position passes it, or when `seek_hold_s` has elapsed and the real position is more than `lead_s + 10` before it.
  - `_next_start()`: first scene start > `_position() + guard_s`, else `None`; `None` when position is `None`, no snapshot, or the app is not in `seek_apps`.
  - Condition `ready`: `_next_start() is not None`.
  - `key("next", args)`: `{"icon": ctx.icon("scene_off"), "label": "", "modes": [{"when": "scenes.ready", "icon": ctx.icon("scene")}], "action": {"service": "scenes", "do": "next"}}`; other names → `None`.
  - Action `next`: `start = _next_start()`; none → return. `target = max(real_position, start - lead_s)`, `ctx.call("media_player", "media_seek", {"entity_id": atv, "seek_position": round(target)})`, set the jump memory, log `jump <from>s -> <to>s (scene at <start>s)`, `ctx.changed()`, schedule the redraw.
  - Gray-out redraw: after a snapshot swap or a jump, `ctx.redraw_in(seconds until the last scene start - guard_s is reached + 0.5)` when playing and that is in the future. `on_state` does nothing (the 5 s job and the host's own redraws on state change cover it).

- [ ] **Step 1: Write the failing tests** (`Lookup` replaced by a fake returning fixed scenes; states built by a helper `atv(state="playing", app="com.wbd.stream", title="Tuner", dur=6600, pos=0)` with `media_position_updated_at` = now)

```python
def test_film_noticed_after_settle_then_ready():
    svc, ctx, lk = make(scenes=((2943, 2946), (4000, 4010)), states=atv(pos=60))
    assert svc.watch() == 5 and lk.calls == []                 # settling
    ctx.fakes.time.now += 10; svc.watch()
    assert lk.calls == [("Tuner", 6600, "com.wbd.stream")]
    assert svc.conditions()["ready"]() is True and ctx.fakes.signals == 1
    svc.watch(); assert len(lk.calls) == 1                     # same film: no second lookup

def test_press_seeks_to_next_scene_with_lead_and_steps_through():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    svc.actions()["next"]({})
    assert ctx.fakes.calls == [("media_player", "media_seek", {"entity_id": ATV, "seek_position": 2938})]
    svc.actions()["next"]({})                                  # HA still reports the old position
    assert ctx.fakes.calls[-1][2]["seek_position"] == 3995
    assert svc.conditions()["ready"]() is False                # none after the last one
    svc.actions()["next"]({}); assert len(ctx.fakes.calls) == 2

def test_not_ready_cases():
    assert ready(scenes=((100, 110),), pos=500)[0].conditions()["ready"]() is False     # none ahead
    assert ready(scenes=((100, 110),), pos=None)[0].conditions()["ready"]() is False    # no position
    assert ready(scenes=((100, 110),), pos=0, app="com.netflix.Netflix")[0].conditions()["ready"]() is False
    assert ready(scenes=(), pos=0)[0].conditions()["ready"]() is False

def test_excluded_short_and_stopped_are_not_films():
    for st in (atv(app="com.google.ios.youtube"), atv(dur=1500), atv(state="idle"), atv(title="")):
        svc, ctx, lk = make(scenes=((100, 110),), states=st)
        svc.watch(); ctx.fakes.time.now += 10; svc.watch()
        assert lk.calls == []

def test_film_change_during_lookup_does_not_light_the_key():
    svc, ctx, lk = make(scenes=((100, 110),), states=atv(pos=0))
    lk.on_run = lambda: ctx_states.update(atv(title="Other Film", dur=7000, pos=0))   # film changes mid-lookup
    svc.watch(); ctx.fakes.time.now += 10; svc.watch()
    assert svc.conditions()["ready"]() is False

def test_film_ends_clears_snapshot_and_rewind_drops_jump_memory():
    svc, ctx, lk = ready(scenes=((2943, 2946), (4000, 4010)), pos=60)
    svc.actions()["next"]({}); ctx.fakes.time.now += 6; set_pos(ctx, 600)       # user rewound far back
    svc.actions()["next"]({}); assert ctx.fakes.calls[-1][2]["seek_position"] == 2938
    set_state(ctx, "idle"); svc.watch(); assert svc.conditions()["ready"]() is False

def test_key_spec_and_redraw_scheduled():
    svc, ctx, lk = ready(scenes=((2943, 2946),), pos=60)
    k = svc.key("next", {})
    assert k["action"] == {"service": "scenes", "do": "next"} and k["modes"][0]["when"] == "scenes.ready"
    assert svc.key("nope", {}) is None
    assert ctx.fakes.redraws and 2880 < ctx.fakes.redraws[-1] < 2885
```

- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** the whole suite (`python -m pytest homeassistant/streamdock_n3 -q`) — pass.
- [ ] **Step 5: Commit** (`scenes: service (watch, ready, next)`).

---

### Task 10: Icons, preset, docs, version

**Files:**
- Create: `services/scenes/icons/source.gif` (copy of `C:/Users/tsyga/AppData/Local/Temp/claude/090bb6c475681199/f9407c6d-65ae-4ae8-8e11-c230486024ef/images/1.gif`), `services/scenes/icons/scene.png`, `services/scenes/icons/scene_off.png`, `homeassistant/tools/icons/build_scene_icons.py`
- Modify: `presets/tv_room.json`, `tests/test_config.py` (`EMPTY_KEYS`: drop `"context:streaming"`), `config.yaml` (`version: "0.6.1-1"`), `services/README.md` (one line: `scenes/` is the second worked example), `DOCS.md` (a short "Scene jump" section: what the key does, `seek_apps`, the `tmdb_key` secret), `docs/SCENE_SOURCES.md` (rules section points at the config names), `docs/JOURNAL.md` (entry + open items), `homeassistant/restore.sh` only if it lists files explicitly.

**Interfaces:**
- Consumes: the service from Task 9.
- Preset additions:
  ```json
  "services": {"scenes": {"atv": "media_player.vugitv_4k_vugitv_4k",
      "seek_apps": ["com.wbd.stream", "com.disney.disneyplus"],
      "exclude_apps": ["com.google.ios.youtube"],
      "sources": ["videoskip", "nsfwhen", "nudidex"],
      "app_services": {}}}
  ```
  (`app_services` filled with the VideoSkip service names found in Task 2 for Max and Disney+), and `"4": {"service_key": "scenes.next"}` in `tabs.pages["context:streaming"].keys`.
- Icons: `build_scene_icons.py` (Pillow) reads `source.gif`, takes the dark silhouette as a mask, scales it to fit 112×112 with 10 px padding (LANCZOS), and writes `scene.png` = white `#ffffff` figure on `#8f1d2c`, `scene_off.png` = `#5e5e5e` figure on `#262626`.

- [ ] **Step 1: Write the failing tests** — in `services/scenes/tests/test_scenes_service.py`:

```python
def test_icons_exist_and_preset_places_the_key():
    icons = Path(scenes.__file__).parent / "icons"
    for n in ("scene.png", "scene_off.png"):
        assert Image.open(icons / n).size == (112, 112)
    preset = json.loads((ROOT / "presets" / "tv_room.json").read_text())
    assert preset["tabs"]["pages"]["context:streaming"]["keys"]["4"] == {"service_key": "scenes.next"}
    assert preset["services"]["scenes"]["atv"].startswith("media_player.")
```

- [ ] **Step 2: Run** — fail. **Step 3: Do the file work above; run `python homeassistant/tools/icons/build_scene_icons.py` and look at both PNGs.** **Step 4: Run the whole suite** — pass (including `tests/test_config.py` and `tests/test_services_*.py`).
- [ ] **Step 5: Commit** (`scenes: icons, preset key, docs, 0.6.1-1`).
- [ ] **Step 6: Deploy and check on the TV — with the owner, not alone.** The owner puts `{"scenes": {"tmdb_key": "..."}}` into `/config/secrets.json` on the device. Ask before running `restore.sh`. Then, one command at a time on the owner's "go": start a film known to VideoSkip in Max or Disney+; read the add-on log for the `scenes:` lines (film noticed, per-source results); confirm the key turns to colour; one press; confirm the landing point is about 5 s before the scene. Record the result and any `lead_s`/offset finding in `docs/JOURNAL.md` and `docs/SCENE_SOURCES.md`.
