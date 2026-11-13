# Pluggable services — design

Date: 2026-11-13. Status: approved by the user 2026-11-13.

Related: [JOURNAL.md](../../JOURNAL.md) (system state),
[DOCS.md](../../../homeassistant/streamdock_n3/DOCS.md) (app config format),
[tabs design](2026-10-24-actionring-tabs-design.md) (pages, rounds, context
layouts), [`ha_daemon.py`](../../../homeassistant/streamdock_n3/ha_daemon.py),
[`soccer.py`](../../../homeassistant/streamdock_n3/soccer.py).

## 1. Intent

The user wants to add new features to the dock **fast**, each with fairly
complex logic of its own, **without touching `ha_daemon.py`**. Examples:
live soccer broadcasts (exists), live bet tracking, and jumping to a named
scene in the movie that is playing.

Today every such feature is wired into the daemon by hand. Soccer, for
example, needs a module global, its own action verb (`{"soccer": "start"}`),
entries in `_conds`, label variables through `_vars()`, a `_watching_game()`
hook for the context layout, a poll in `_tick()`, a toast event name, and a
thread started in `main()`. Every new feature repeats this and makes
`ha_daemon.py` grow.

A **service** is a self-contained feature behind a fixed contract. It can:

- fetch from websites or APIs and parse the replies
- keep local data (cache, history, tokens)
- provide keys: a look that follows its state (including dynamic keys) and
  what a press does
- provide whole pages (tabs)
- send toasts to the TV
- call Home Assistant and read entity state
- nudge the context layout (soccer's `live_game`)

The daemon becomes a **host** that loads services, runs their work, and
**measures everything they do**, so an expensive service or operation is easy
to spot.

## 2. Goals, non-goals, anti-goals

**Goals**

- A new service is a folder under `streamdock_n3/services/<name>/` plus a
  config section and preset lines. No edits to `ha_daemon.py`, `Dockerfile`,
  `tabs.py` or `keyspec.py`.
- The preset decides where a service's keys and pages appear (decided with
  the user, option A).
- Every service operation is measured: count, errors, wall time, CPU time,
  HTTP and Home Assistant calls. Slow operations are logged when they
  happen. A periodic summary goes to the log and to a JSON snapshot, and one
  command on the PC shows a ranked table.
- A broken or slow service can't freeze the dock, block key presses or stop
  other services.
- Services are testable without the network, Home Assistant or the device,
  through a fake context.
- Live soccer is moved onto the contract with **no change in behaviour**, as
  the proof that the contract can carry a real feature.
- `restore.sh` still reproduces the live system. JOURNAL and DOCS are
  updated.

**Non-goals (this project)**

- Bet tracking and scene jumps themselves. They are the next projects and
  are built on this contract.
- Moving YouTube (Lounge, rating), ad detection, knobs, logo or toasts onto
  the contract. They keep working as they are. They may move later.
- Loading services at runtime from `/config` (drop-in code without an image
  rebuild). Services ship in the image (decided, approach 1).
- Per-service memory figures (not measurable inside one process; see §8).
- Home Assistant sensor entities for metrics. Possible later.
- Hot reload of a service without restarting the app.

**Anti-goals (a failure even if it technically works)**

- A key press that feels slower because of a service.
- A service that silently stops working with nothing in the log.
- A key that shows stale data as if it were live (soccer's stale-list rule
  must hold for every service: the host gives no data-freshness guarantee,
  but the contract makes it the service's job, see §5.4).
- Secrets, tokens, account IDs or cookies in the public repo, or in log
  lines (URLs that carry tokens are never logged).
- Metrics that cost noticeable CPU on the Pi.

## 3. Constraints and facts

- **Runtime.** One Python 3.12 process in the app container on a Raspberry
  Pi 4 (4 cores, about 2.5 GB free, idles at about 5 % CPU; see
  `docs/perf/2026-10-26-pi-24h.md`). Threads, no asyncio in the daemon.
- **Threads today.** The SDK reader thread runs `_run_actions` (must never
  block: slow work goes to a new thread). The HA thread runs `_watch` and
  `_update_context`. One renderer thread draws keys (`_render_pass`). The
  ticker runs every 0.2 s (`_tick`). Code called from the renderer or HA
  threads must not do I/O.
- **Key specs.** Keys resolve through `keyspec.resolve_look/resolve_action`
  (icon, label with `{var}` placeholders, color, `state_entity` variants,
  `modes` with `when`) and `dynamic.py` (phases, warnings, press
  animations). Labels on icons go on a band (`badge.py`). `icon_path()`
  treats anything with a `/` as a path, so a service can point at its own
  icon files.
- **Dependencies.** Standard library plus what the image installs
  (`websockets`, `pyatv`, `pyytlounge`, Pillow from the upstream SDK). A
  service that needs a new pip package needs a `Dockerfile` line. That is
  allowed but should be rare.
- **Files.** `/data` is private to the app and persistent (ad detection keeps
  state there). `/config` is the user-editable `addon_config` share
  (`config.json`, icons, the YouTube token).
- **Deploy.** Bump `version` in `config.yaml`, run `restore.sh` (it rebuilds
  the app on the device). The `Dockerfile` copies modules by name today.
- **Public repo.** No secrets, serials, addresses or account data.

## 4. Approaches considered

1. **Host-run services (chosen).** A service is a class that declares jobs,
   keys, pages, actions and conditions, and does all its I/O through a
   context object the host gives it. The host runs its jobs on a small
   scheduler, catches its errors, and measures every operation. Costs:
   services must follow the contract, and soccer's own polling loop becomes
   a job.
2. **Thin registry, services keep their own threads.** Today's soccer
   pattern with the wiring moved out of `ha_daemon.py`. Quickest, but CPU and
   time are only measured where a service chooses to report them, and a
   runaway thread can't be traced to its service.
3. **One process per service.** True isolation and exact per-service CPU and
   memory from the OS. Costs about 20 MB RAM per service on the Pi, a
   message protocol between processes, and slower development.

The user picked **1** (2026-11-13): it meets "fast, without touching
`ha_daemon.py`" and gives per-operation numbers at almost no cost.

## 5. Design

### 5.1 Layout on disk

```
streamdock_n3/
  services/
    __init__.py        # Service base class, decorators, discovery (the contract)
    host.py            # ServiceHost: loading, dispatch, breaker, hooks for ha_daemon
    scheduler.py       # jobs, action threads, retries, hang detection
    context.py         # ServiceContext: http, store, ha, toast, signals
    metrics.py         # Metrics recorder, summaries, snapshot file
    testing.py         # FakeContext and helpers for service tests
    README.md          # How to write a service (for people and AI agents)
    soccer/
      __init__.py      # SERVICE = SoccerService
      service.py       # the contract side
      listing.py       # pure parsing (today's soccer.py functions)
      live.py          # LiveSoccer, moved from soccer.py
      tests/
```

Discovery: at startup the host imports `services.<name>` for every name
under the config's `"services"` section (§5.2) and takes its `SERVICE`
class. A service that isn't configured is never imported and costs nothing.
An import or start error disables that service with one log line; the rest
of the app runs.

The `Dockerfile` copies the whole `services/` tree once (one-time change),
so a new service never needs a `Dockerfile` edit unless it adds a package.

### 5.2 Configuration

Services are configured in the preset (`tv_room.json`):

```json
"services": {
  "soccer": {"atv": "media_player.vugitv_4k_vugitv_4k", "poll_s": 180, "...": "..."},
  "bets":   {"enabled": false, "...": "..."}
},
"metrics": {"log_every_s": 600, "slow_ms": {"job": 2000, "action": 3000, "http": 5000,
                                            "key": 20, "context": 5, "on_state": 20}}
```

- Each section is passed to its service as `ctx.config`.
- `"enabled": false` keeps the config but doesn't load the service.
- Secrets never go in the preset (it's in the repo). `ctx.secret(name)` reads
  `/config/secrets.json` (`{"bets": {"api_key": "…"}}`), a file only on the
  device. `restore.sh` never creates or copies it.

### 5.3 Placing keys and pages (preset owns layout)

A preset key slot refers to a service key by name:

```json
"2": {"service_key": "soccer.live"}
"4": {"service_key": "soccer.next"}
"3": {"service_key": "bets.slot", "args": {"i": 0}, "label": "{bets_slot_0}"}
```

- The host asks the service for that key's spec
  (`key(name, args) -> dict`). The result is an ordinary key spec (icon,
  label, color, modes, `dynamic`, action). It goes through the existing
  `keyspec`/`dynamic`/`badge` code, so services get dynamic keys, press
  animations, warnings and label bands for free.
- Fields written in the preset slot override the service's spec (the preset
  can relabel or recolor a key without touching the service).
- A service's own icons are given as paths
  (`/opt/streamdock/services/soccer/icons/soccer_live__live.png`, or via
  helper `ctx.icon("soccer_live__live")`).

A service page is placed by name in the preset's round cycles:

```json
"round": {"7": ["apps", "apps_more"], "8": ["sonos", "bets.slip"]}
```

The service declares the page (`pages() -> {"slip": {"keys": {...},
"close_on_press": false}}`). Its keys are usually `service_key` slots, so the
page layout is fixed at startup and only key contents change. Page names
are `<service>.<page>`.

The preset can also keep using service features in its own key specs:

- action `{"service": "soccer", "do": "start", "args": {...}}`
- conditions named `soccer.live`, `soccer.picking` in `when`
- label variables `{soccer_count}`, `{soccer_pos}` (a service's variables
  must start with `<service>_`; the host checks this)

### 5.4 The service contract

```python
class SoccerService(Service):
    name = "soccer"

    def start(self, ctx): ...                      # once, after config is loaded
    def entities(self) -> set[str]: ...            # HA entities to subscribe to
    @job("refresh", first_in=0)
    def refresh(self) -> float: ...                # returns seconds until next run
    @action("start")
    def start_pick(self, args): ...                # a press; may block (worker thread)
    @condition("live")
    def live(self) -> bool: ...                    # no I/O
    def vars(self) -> dict[str, str]: ...          # no I/O
    def key(self, name, args) -> dict | None: ...  # no I/O
    def pages(self) -> dict[str, dict]: ...        # once, at startup
    def context(self, layout: str) -> str | None: ...  # no I/O; override the layout
    def on_state(self, changed: set[str]): ...     # no I/O; HA entities changed
    def stop(self): ...                            # app shutting down (best effort)
```

All methods are optional except `name`. Rules the host enforces or measures:

- **No I/O in "no I/O" methods.** `key`, conditions, `vars`, `context` and
  `on_state` run on the renderer, the HA thread and (for conditions in a
  key's `when`) the SDK reader thread on every press. The host times each
  call and logs a slow-operation warning past its `slow_ms` budget (20 ms
  for keys, 5 ms for context). A service keeps the data these need in
  memory, updated by its jobs, and never takes a lock in them that a job
  or action holds across I/O (`README.md` shows the pattern: swap in a new
  immutable snapshot under a short lock).
- **Circuit breaker** *(my call: the host can't pre-empt a call already
  running, so a single slow call still delays that one pass)*. If a
  service's render-path calls overrun their budget 3 times within a
  minute, or raise 3 times, the host stops calling them for 5 minutes. The
  service's keys show the `?` error look, its conditions read False, and
  its vars and context are skipped. One log line marks the trip and one
  marks the reset. A service with a bug can then cost the dock at most a
  few slow passes, not a stuck dock.
- **Jobs** return the delay until their next run (None: don't run again) (so soccer's adaptive
  polling — faster while a game is live or about to start, backing off
  after errors — stays inside the service). An exception counts as an error
  and the job is retried after `retry_s` (default 60 s, doubling to
  `max_retry_s`, default 1800 s).
- **Actions** run on a worker thread, never the SDK reader thread. They may
  block (soccer waits for the TV to power on and sleeps before `select`).
- **Freshness is the service's job.** A key must not show stale data as
  live; the service decides when its data has expired (as soccer does with
  `_stale`). `README.md` says so and the template shows it.
- **Threads.** A service never starts its own threads. If it needs a
  long-lived connection (a websocket feed), it declares a `@job` that
  doesn't return until the connection drops (marked `long_running=True` so
  the host gives it its own thread and leaves it out of the slow-job
  warnings).

### 5.5 The context object (`ctx`)

| Member | What it does |
|---|---|
| `ctx.config` | The service's config section |
| `ctx.secret(name)` | A value from `/config/secrets.json` for this service, or None |
| `ctx.http.get(url, *, op, headers, timeout)` / `.post(...)` / `.get_json(...)` | HTTP with a required timeout (default 15 s). `op` names the operation for metrics. Never logs URLs or bodies |
| `ctx.store` | A JSON document store in `/data/services/<name>/`: `load(key, default)`, `save(key, value)` (atomic write). `ctx.store.sqlite(name)` opens a SQLite file there for larger data |
| `ctx.state(entity)` | Entity state from the host's cache (`{"s", "a"}`), no I/O |
| `ctx.call(domain, service, data)` / `ctx.call_response(...)` | Home Assistant service calls |
| `ctx.media` | The shared media helpers (`ensure_on`, `select_atv_input`, `launch`) bound to `ctx.call`/`ctx.state` |
| `ctx.toast(message, channel=None)` | A TV toast through the existing `Toaster`. The event name is the service name. `toast.events` stays an allow-list *(my call, in line with dropping the noisy `ad` toasts)*: a service's toasts show only once its name is listed. The first toast suppressed that way logs one line (`toast: bets not in toast.events; not shown`), so a missing entry is never silent |
| `ctx.shared(name)` | Optional handles the daemon already holds, or None when not running: `"atv_touch"` (the Companion connection, for scrubbing and exact seeks), `"youtube"` (the Lounge screens) *(my call: scene jumps may need exact seeking or YouTube position; exposing these now keeps the next project out of `ha_daemon.py`)*. Calls through them are timed under `shared:<name>` |
| `ctx.changed()` | Redraw keys (the look of something changed) |
| `ctx.redraw_in(seconds)` | Redraw at a later time without an event (replaces soccer's `picking()` poll in `_tick`) |
| `ctx.context_changed()` | Re-run the context layout (soccer: a started game came on screen) |
| `ctx.show_page(name)` | Open one of the service's pages as a temporary tab, with the normal timeouts *(my call: lets a service surface something, such as a goal or a settled bet; never sticks, per the tabs anti-goal)* |
| `ctx.log(msg)` | A log line prefixed with the service name |
| `ctx.clock()` / `ctx.monotonic()` / `ctx.sleep(s)` | Time, injectable for tests |
| `ctx.timed(op)` | A context manager to measure a piece of a service's own work (e.g. a heavy parse) as its own operation |

### 5.6 The host and `ha_daemon.py` (one-time changes)

`ServiceHost` gives `ha_daemon.py` a small, stable surface. After this
project, `ha_daemon.py` changes only when the host itself changes:

| Daemon hook | Host call |
|---|---|
| `main()` | `host = ServiceHost.load(config, core, state_of, toaster, signal, engine)`; `entities |= host.entities()`; `host.start()` |
| `_conds` | merged with `host.conditions()` (names `<service>.<cond>`) |
| `_vars()` | merged with `host.vars()` |
| `_look_for` / `_render_pass` | a slot with `service_key` is resolved to `host.key_spec(slot)` first, then goes through the normal path |
| `_make_engine` | `host.pages()` added to the engine's pages before it's built |
| `_run_actions` | `{"service": …}` goes to `host.run_action(...)` |
| `_update_context` | `layout = host.context(layout)` after `foreground_layout` (services in config order; the first non-None wins) |
| `_watch` | `host.on_state(changed_entities)` after each event |
| `_tick` | `host.tick(now)` for `redraw_in` deadlines |

The soccer-specific code in `ha_daemon.py` is removed: `_soccer`,
`SOCCER_COMMANDS`, the `soccer` action branch, the `soccer_*` conditions,
`_watching_game`, the soccer part of `_vars` and `_tick`.
`foreground_layout`'s `watching_game` parameter goes; soccer's `context()`
turns `streaming` into `live_game` while a started game is on screen.

**Scheduler.** A heap of due jobs and a pool of **3 worker threads** *(my
call: enough for a few services polling at once on 4 cores; a config value)*.
A job never runs twice at the same time. **Each press gets its own thread,
as today** *(my call: soccer's press holds a lock for 10–33 s while the TV
powers on and the app opens, so a fixed pool would queue quick
cycling presses and break "no change in behaviour")*. The host caps
running actions at 16 per service and drops a press past that with a log
line. Long-running jobs get a thread each.

**Hung work.** Python can't kill a thread. A job or action running past
`hang_s` (default 120 s) is logged as hung once, and that service's other
jobs keep running on the remaining workers. Because HTTP has required
timeouts, a real hang should be rare; the log names it if it happens.

**Error storms.** A service whose operations fail 10 times in a row is
logged once per streak (not per failure), with the last error.

### 5.7 Performance metrics

Every operation goes through the host, so the host records it. Operation
names:

| Kind | Example | Measured on |
|---|---|---|
| `job:<name>` | `soccer job:refresh` | worker thread |
| `action:<name>` | `soccer action:start` | action worker |
| `key:<name>` | `soccer key:live` | renderer |
| `cond:<name>`, `vars`, `context`, `on_state` | `soccer context` | renderer / HA thread |
| `http:<op>` | `soccer http:shelf` | inside a job or action |
| `ha:<domain.service>` | `soccer ha:media_player.play_media` | inside a job or action |
| `timed:<op>` | `bets timed:parse` | wherever `ctx.timed` is used |
| host ops | `host render_pass`, `host image_gen`, `host ha_event` | daemon threads |

For each operation: count, errors, wall time (total, max, p50, p95 from a
fixed 64-bucket log-scale histogram, so no per-call storage) and CPU time
(`time.thread_time()` deltas: the CPU used by that thread, which is exact
for the operation itself). For HTTP also bytes in and out and status
classes (2xx/4xx/5xx/timeout).

Process-wide, sampled at each summary: CPU % of the app and RSS (from
`/proc/self/stat` and `/proc/self/status`), thread count, and each
service's store size on disk.

Two windows: since start, and the last summary interval.

**Where you see it**

- **Slow operations, as they happen:** one log line when an operation goes
  past its `slow_ms` budget, e.g.
  `metrics: SLOW soccer job:refresh 6.2s wall, 0.31s cpu (budget 2.0s)`.
  At most one line per operation per minute.
- **Summary in the log** every `log_every_s` (default 10 min): one line per
  service, ranked by CPU time in the interval, e.g.
  `metrics: soccer cpu 0.42s (0.07%) wall 3.1s · 4 jobs, 0 err · http 8 req 1.2 MB p95 0.9s · keys 410 p95 1ms`.
- **Snapshot file** `/config/metrics.json` (on the host:
  `/app_configs/local_streamdock_n3/metrics.json`), rewritten at each
  summary, with every operation's figures for both windows.
- **On the PC:** `homeassistant/tools/metrics.sh` reads the snapshot over
  SSH and prints a table ranked by CPU (or `--by wall|http|errors`), so
  "which service or operation is too expensive" is one command.

**Cost.** Recording an operation is two clock reads, two `thread_time()`
reads and a few integer updates under a lock (microseconds). The summary
runs every 10 minutes. Both stay far below the Pi's noise floor.

**Limit (said up front).** Memory can't be measured per service inside one
process. The summary shows the app's RSS and each service's store size;
a sudden RSS jump points at whatever started at that time.

### 5.8 Testing

- `services/testing.py` gives a `FakeContext`: canned HTTP responses by URL
  pattern, an in-memory store, a dict of entity states, recorded HA calls
  and toasts, a controllable clock. A service test drives `job`/`action`/
  `key` calls directly with no threads.
- Host tests (scheduler, retries, hang detection, dispatch, key-spec
  overrides, metrics histogram and summary) use fake clocks and run without
  the device.
- Existing `test_soccer.py` cases keep passing (pure parsing moves with
  minor import changes). New tests cover soccer through `FakeContext`: the
  same pick-and-cycle, stale-list and `live_game` behaviour as today.
- Tests run with the existing `pytest` setup (`tests/conftest.py` adds the
  app dir to `sys.path`; service tests live next to their service and are
  collected too).

### 5.9 Soccer migration (the proof)

Behaviour stays the same; only the wiring changes.

| Today | After |
|---|---|
| Top-level `"soccer": {...}` config | `"services": {"soccer": {...}}` |
| `{"soccer": "start"}` / `"next"` actions | `{"service": "soccer", "do": "start"}` / `"next"`, or a `service_key` slot |
| `soccer_live`, `soccer_picking` conditions | `soccer.live`, `soccer.picking` |
| `{soccer_count}`, `{soccer_pos}` | unchanged (already prefixed) |
| `poll_forever` thread | `@job("refresh")` returning `poll_interval` / `retry_interval` |
| `_tick` polling `picking()` | `ctx.redraw_in(CYCLE_S)` after each pick |
| `_watching_game()` in context | `context()` returns `live_game` |
| `fetch=http_get` | `ctx.http.get(..., op="shelf")`, `op="token_page"` |
| `notify=` toast | `ctx.toast(..., channel="soccer")` |

The three soccer keys in `tv_room.json` (`apps_more` 6, `context:idle` 2,
`context:live_game` 4) become `service_key` slots `soccer.live` (×2) and
`soccer.next`. The soccer icons stay in the shared `icons/` folder: they
are generated by `tools/icons/build_icons.py` with the rest of the set.
`ctx.icon(name)` looks in the service's own `icons/` folder first, then
falls back to the shared set, so a new service can ship its own icons
without a `restore.sh` step.

## 6. Documentation and deploy

- `services/README.md`: the contract, the context table, rules (no I/O in
  render-path methods, freshness, secrets), a copyable template, how to test,
  how to read the metrics. Written so an AI agent can add a service from it
  (like `AD_DETECTION_GUIDE.md`).
- `DOCS.md`: the `services` and `metrics` sections, `service_key`, the
  `{"service": …}` action.
- `JOURNAL.md`: a log entry and the new system state; the soccer
  conditions renamed in the control mappings.
- `restore.sh`: still reproduces the live system. It needs no service step
  (services ship in the image). It must not touch `/config/secrets.json`.
- `config.yaml` version bump.

## 7. Decided by the user

- Purpose: add complex features fast without touching `ha_daemon.py`.
- Preset owns layout; services provide keys and pages (option A).
- Host-run services in one process (approach 1).
- Performance metrics to find expensive services and operations.

## 8. Left to the builder

- Exact names of decorators and helper methods, as long as the contract in
  §5.4 holds.
- Histogram bucket layout and the snapshot's JSON shape (document it in
  `README.md`).
- Whether `ServiceHost` wraps `CoreAPI` per service (separate connections)
  or shares one with a lock; measure and pick.
- The exact form of `metrics.sh` output.
