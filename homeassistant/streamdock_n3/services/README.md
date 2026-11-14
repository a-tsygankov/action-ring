# Services

A service is a self-contained feature of the dock (live soccer today; bet
tracking or scene jumps next) that lives in `services/<name>/` and plugs into
the daemon without editing `ha_daemon.py`, `tabs.py`, `keyspec.py` or the
`Dockerfile`. The daemon is only the **host**: it loads the services named in
the config, runs their jobs and actions, calls their key/condition hooks on
the render path, and measures every operation.

This file is enough to write a new service. `soccer/` is the worked example
(jobs, actions, conditions, keys, context override, tests); `scenes/` is the second
worked example (a database, several HTTP sources, a condition driven by the clock);
`profiles/` is an action-only service (it waits and watches after a key press);
the template below is the smallest useful one. Design background:
[the design spec](../../../docs/superpowers/specs/2026-11-13-pluggable-services-design.md).

## Add a service in five steps

1. Copy the template below to `services/<name>/` (`__init__.py`, `service.py`,
   `tests/test_<name>.py`). The folder name, the module path and
   `Service.name` must all be the same lowercase name (`[a-z][a-z0-9_]*`).
2. Add a section to the preset (`presets/tv_room.json`):
   `"services": {"<name>": {...}}`. The section is the service's
   `ctx.config`. `"enabled": false` keeps it but does not load it. A service
   without a section is never imported.
3. Put its keys in the preset: `{"service_key": "<name>.<key>"}` (see
   Placing keys and pages).
4. Add `<name>` to `toast.events` if it toasts.
5. Test (`python -m pytest homeassistant/streamdock_n3 -q`), bump `version`
   in `config.yaml`, run `restore.sh`. The image copies the whole `services/`
   tree, so no `Dockerfile` edit is needed unless you need a new pip package.

A service that fails to import, to `start()`, or whose `pages()`,
`entities()`, `conditions()` or job list raises at startup is disabled with
one log line (`services: <name> disabled (<Error>: <why>)`): its keys show
`?`, its jobs stop and the rest of the app runs. The name `host` is reserved
(the daemon's own metrics).

## The contract

`services/__init__.py`. Subclass `Service`; every method is optional except
the class attribute `name`. The package's `__init__.py` exposes the class as
`SERVICE` (`from .service import MyService as SERVICE`).

```python
from services import Service, action, condition, job

class MyService(Service):
    name = "my"

    def start(self, ctx): ...                 # once, before jobs run; keep ctx
    def entities(self) -> set[str]: ...       # HA entities to subscribe to
    def vars(self) -> dict[str, str]: ...     # label variables; names must start "my_"
    def key(self, name, args) -> dict | None: ...   # spec for {"service_key": "my.<name>"}
    def pages(self) -> dict[str, dict]: ...   # {"slip": {"keys": {...}, "close_on_press": False}}; before start()
    def context(self, layout) -> str | None: ...    # override the context layout, or None
    def on_state(self, changed: set[str]): ...      # HA entities (yours) changed
    def stop(self): ...                       # not called yet: see below

    @job("refresh", first_in=0, long_running=False)
    def refresh(self) -> float | None: ...    # seconds until the next run; None = once
    @action("press")
    def press(self, args: dict): ...          # a key press; may block
    @condition("live")
    def live(self) -> bool: ...               # usable as "when": "my.live"
```

- **Jobs** run on a pool of 3 worker threads and never overlap with
  themselves. The returned delay counts from the job's finish (minimum 1 s,
  maximum 1 day; NaN means the 60 s retry).
  An exception counts as an error and the job is retried after 60 s, doubling
  up to 1800 s; a streak is logged once at the first failure
  (`services: my job:refresh failed: <Error>: <why>`), once at the 10th
  (`... failing (10 in a row) ...`) and once on recovery (`... recovered`).
  Anything else escaping a job (`SystemExit`) logs
  `services: worker error <Error>: <why>` and the job is retried after 60 s.
  A `long_running=True` job (a websocket feed) gets its own thread, is rerun
  `delay` seconds after it returns, and is left out of slow-job warnings.
  `first_in` delays the first run (seconds).
- **Actions** (`{"service": "my", "do": "press", "args": {...}}`) run on their
  own thread per press, never on the SDK reader thread, so they may block
  (waiting for the TV to power on). At most 16 run at once per service; a
  press past that is dropped with a log line. Exceptions are logged
  (`services: my action:press failed: ...`).
- **Conditions** are named `<service>.<name>` in a key's `when`
  (`"when": "my.live"`).
- **Variables** returned by `vars()` fill `{my_count}` placeholders in labels.
  A name not starting with `<service>_` is dropped (logged once).
- **`context(layout)`** receives the layout the daemon decided (`idle`,
  `streaming`, `youtube`, ...) and may return another (soccer turns
  `streaming` into `live_game` while a started game is on screen). Services
  are asked in config order; the first non-None wins.
- **`start(ctx)`** runs synchronously on the main thread before the dock
  opens. Keep it quick: do network I/O (the first fetch) in a `first_in=0`
  job, not in `start()`.
- **`pages()`** is read once at startup, **before `start()`**: there is no
  `ctx` yet, so build the pages without it. Page names become
  `<service>.<page>` and are used in the preset's `round` cycles or opened
  with `ctx.show_page(page)`.
- **`entities()`** is read once, after `start()`. It must include every
  entity your key specs read (`state_entity`, the entities in a `when`
  list): a `service_key` slot is opaque to the daemon's entity scan, so
  those entities are watched (and in `ctx.state()`) only if you return them.
- **`stop()` is not called by the app yet**: the process is killed on
  restart. Do not rely on it for persistence; save state as you go
  (`ctx.store.save` after each change).
- A malformed return (a `key()` that is not a dict, `vars()` not a
  `dict[str, str]`, `context()` not a string) counts as an error, like an
  exception.

## The context (`ctx`)

Given to `start(ctx)`. Everything outside the process goes through it, so it is
measured and faked in tests.

| Member | What it does |
|---|---|
| `ctx.config` | The service's section of the config |
| `ctx.secret(name)` | A value from `/config/secrets.json` under this service's name, or `None` |
| `ctx.http.get(url, *, op, headers=None, timeout=15)` | GET, returns the body text. `op` names the operation in the metrics (`http:<op>`); raises on HTTP errors and timeouts. Default `User-Agent: Mozilla/5.0` |
| `ctx.http.get_json(url, *, op, ...)` | GET and parse JSON |
| `ctx.http.post(url, *, op, data=None, headers=None, timeout=15)` | POST; `data` is bytes or a dict (sent as JSON) |
| `ctx.store.load(key, default=None)` / `.save(key, value)` | JSON documents in `/data/services/<name>/`, written atomically |
| `ctx.store.sqlite(name)` | A SQLite connection to `<name>.sqlite` there (usable from any thread) |
| `ctx.state(entity)` | The host's cached HA state, `{"s": ..., "a": {...}}` or `None`; no I/O. Only entities in `entities()` (or used by the daemon) are cached |
| `ctx.call(domain, service, data)` / `ctx.call_response(...)` | Home Assistant service call (blocking; measured as `ha:<domain>.<service>`) |
| `ctx.media` | `ensure_on()`, `select_atv_input()`, `launch(device, app)`: the shared media helpers, bound to this service's config |
| `ctx.toast(message, channel=None)` | A TV toast. Shown only when the service name is in `toast.events`; the first suppressed toast logs one line (`toast: <name> not in toast.events; not shown`) |
| `ctx.shared(name)` | A handle the daemon already holds, or `None`: `"atv_touch"` (Companion connection), `"youtube"` (Lounge screens), `"atv_clients"` (which Apple TV apps are alive: `status(bundle)` gives `alive`, `gone` or `unknown`; see `atv_clients.py`) or `"dock_input"` (`last()`: monotonic time of the last key or knob event). Calls through it are timed as `shared:<name>` |
| `ctx.changed()` | Redraw the keys (their look changed) |
| `ctx.redraw_in(seconds)` | Redraw later without an event (a freshness deadline, a timed pick) |
| `ctx.context_changed()` | Re-run the context layout decision |
| `ctx.show_page(page)` | Open this service's page as a temporary tab, with the normal timeouts |
| `ctx.log(msg)` | A log line prefixed with the service name (`<name>: msg`) |
| `ctx.clock()` / `ctx.monotonic()` / `ctx.sleep(s)` | Time, injectable in tests; use these, not `time` |
| `ctx.timed(op)` | A context manager that measures a piece of your own work as `timed:<op>` |
| `ctx.icon(name)` | The path of `services/<service>/icons/<name>.png` if it exists, else the bare icon id (resolved from `/config/icons`) |

## The rules

The render path is the code the daemon calls to draw and to react to presses:
`key`, conditions, `vars`, `context`, `on_state`. It runs on the renderer,
the Home Assistant thread and the SDK reader thread. Everything below keeps
the dock responsive.

1. **No I/O in render-path methods**: no HTTP, no files, no SQLite, no HA
   calls, no sleeping. Keep what they need in memory, updated by jobs.
2. **No lock in them that a job or action holds across I/O.** Swap an
   immutable snapshot under a short lock (the template does) and read it the
   same way.
3. **Never call `ctx.context_changed()` or `ctx.changed()` from them,
   synchronously.** `context()` runs under the daemon's context lock, and
   `context_changed()` re-enters it: a deadlock. Call them from jobs and
   actions only.
4. **Freshness is the service's job.** A key must not show stale data as live:
   record when data arrived, treat it as missing after a deadline, and use
   `ctx.redraw_in()` so the key redraws at the deadline without any event
   (the template shows it; soccer drops its list when it is stale).
5. **Never start threads.** Use a job (`long_running=True` for a connection
   that stays open).
6. **Every HTTP call has a timeout** (default 15 s) and an `op` name. **Never
   log URLs, tokens or response bodies**; `ctx.http` does not, and `ctx.log`
   is not a place to add them.
7. **Secrets** (API keys, tokens) never go in the preset or the repo: read
   them with `ctx.secret("api_key")` from `/config/secrets.json`
   (`{"<service>": {"api_key": "..."}}`), a file that exists only on the
   device; `restore.sh` never creates or copies it.
8. **Toasts** need the service name in `toast.events`.

The host enforces the render-path budget. Each call is timed against
`metrics.slow_ms` (key 20 ms, cond/vars 5 ms, context 5 ms, on_state 20 ms by
default). Three calls that raise or overrun within 60 s open a **circuit
breaker** for that service: for 5 minutes its keys show `?`, its conditions
read false, and its vars and context are skipped. Log lines:
`services: <name> render calls off for 5 min (<reason>)` and
`services: <name> render calls back on`. A key that cannot be produced logs
`services: key <service>.<key>: <reason>` once.

## Placing keys and pages

The preset decides where a service's keys and pages appear.

```json
"3": {"service_key": "hello.count"},
"4": {"service_key": "hello.count", "args": {"i": 1}, "label": "n={hello_count}", "color": "#1d6b32"}
```

- `service_key` is `<service>.<key>`; the host calls `key(name, args)` (`args`
  defaults to `{}`) and the result is an ordinary key spec (`icon`, `label`
  with `{var}` placeholders, `color`, `modes` with `when`, `state_entity`,
  `dynamic`, `action`), so dynamic keys, animations and label bands work as
  usual. A key returning `None` shows nothing; an unavailable or tripped
  service shows `?`.
- Any other field written in the slot (`label`, `color`, `icon`, ...)
  **overrides** the service's value, so the preset can relabel a key.
- A key's action is `{"service": "<name>", "do": "<action>", "args": {...}}`.
- A service page is placed by name in the round cycles:
  `"round": {"8": ["sonos", "hello.slip"]}`. The page's keys are usually
  `service_key` slots, so the layout is fixed at startup and only the
  contents change.

## Template service

Copy these three files to `services/<name>/` and rename `hello`. It polls a
URL that returns `{"count": N}`, shows the count on a key, and a press runs an
HA script and toasts. It follows every rule above: the job fetches, the
render-path methods only read an immutable snapshot, and stale data shows `-`.

`services/hello/__init__.py`

```python
from .service import HelloService as SERVICE

__all__ = ["SERVICE"]
```

`services/hello/service.py`

```python
"""hello: shows a count fetched from a URL; a press runs an HA script and toasts."""

import threading

from services import Service, action, condition, job


class HelloService(Service):
    name = "hello"

    def __init__(self):
        self.ctx = None
        self._lock = threading.Lock()   # held only to swap or read the snapshot
        self._snap = None               # (fetched_at, count): never mutated, only replaced

    def start(self, ctx):
        self.ctx = ctx

    @job("refresh", first_in=0)
    def refresh(self):
        poll_s = self.ctx.config.get("poll_s", 60)
        count = int(self.ctx.http.get_json(self.ctx.config["url"], op="count")["count"])
        with self._lock:
            self._snap = (self.ctx.monotonic(), count)
        self.ctx.changed()                           # keys changed: redraw
        self.ctx.redraw_in(3 * poll_s + 0.1)         # and again when the data goes stale
        return poll_s                                # seconds until the next run

    def _count(self):
        """The count, or None when there is none or it is stale (no I/O)."""
        with self._lock:
            snap = self._snap
        if snap is None or self.ctx.monotonic() - snap[0] > 3 * self.ctx.config.get("poll_s", 60):
            return None
        return snap[1]

    @condition("busy")
    def busy(self):
        return (self._count() or 0) > 0

    def vars(self):
        n = self._count()
        return {"hello_count": "-" if n is None else str(n)}

    def key(self, name, args):
        if name == "count":
            return {"label": "{hello_count}", "action": {"service": "hello", "do": "ping"}}
        return None

    @action("ping")
    def ping(self, args):
        self.ctx.call("script", "turn_on", {"entity_id": self.ctx.config["script"]})
        self.ctx.toast("Pinged")
```

`services/hello/tests/test_hello.py`

```python
from services.hello import SERVICE
from services.testing import fake_context

CFG = {"url": "http://example.test/count", "poll_s": 60, "script": "script.ping"}


def make(body='{"count": 3}'):
    ctx = fake_context("hello", CFG, http={"example.test": body})
    svc = SERVICE()
    svc.start(ctx)
    return svc, ctx


def test_refresh_fills_vars_and_asks_for_redraws():
    svc, ctx = make()
    assert svc.vars() == {"hello_count": "-"}          # nothing fetched yet
    assert svc.refresh() == 60                         # next run in 60 s
    assert svc.vars() == {"hello_count": "3"}
    assert svc.conditions()["busy"]() is True
    assert ctx.fakes.signals == 1
    assert ctx.fakes.redraws == [180.1]


def test_data_goes_stale():
    svc, ctx = make()
    svc.refresh()
    ctx.fakes.time.now += 181                          # three polls missed
    assert svc.vars() == {"hello_count": "-"}
    assert svc.conditions()["busy"]() is False


def test_ping_calls_ha_and_toasts():
    svc, ctx = make()
    svc.actions()["ping"]({})
    assert ctx.fakes.calls == [("script", "turn_on", {"entity_id": "script.ping"})]
    assert ctx.fakes.toasts == [("Pinged", None)]


def test_key_and_unknown_key():
    svc, _ = make()
    assert svc.key("count", {})["label"] == "{hello_count}"
    assert svc.key("nope", {}) is None
```

Preset side:

```json
"services": {"hello": {"url": "https://example.invalid/count", "poll_s": 60, "script": "script.ping"}},
"toast": {"events": ["rating", "soccer", "sign_in", "hello"]}
```

and `"3": {"service_key": "hello.count"}` in a page.

## Tests

`from services.testing import fake_context` builds a real `ServiceContext`
whose outside world is recorded:

```python
ctx = fake_context(name, config, states={"entity": {"s": "on", "a": {}}},
                   http={"url-substring": "body" | callable(url) | Exception},
                   secrets={"<name>": {"api_key": "x"}}, now=1_000_000.0,
                   toast_events={name}, shared=None)
```

`ctx.fakes` holds `calls` (HA calls as `(domain, service, data)`), `toasts`
(`(message, channel)`), `signals` (`changed()` count), `context_signals`,
`redraws` (the seconds passed to `redraw_in`), `pages`, `urls` and `time`
(`ctx.fakes.time.now += 30` moves the injected clock; `ctx.sleep` advances it
instead of sleeping). An HTTP call with no matching fake raises an
`AssertionError`; that fails the test only if your code lets it through, so
a service that catches exceptions around its fetch should assert on
`ctx.fakes.urls` and on its results instead. Tests
call jobs, actions, conditions and keys directly, without threads; put them in
`services/<name>/tests/` (give the file a unique name: `test_<name>.py`).

```bash
python -m pytest homeassistant/streamdock_n3 -q
```

`services/<name>/tests` and `__pycache__` are kept out of the image by
`.dockerignore`.

## Reading the metrics

With the host running, **a summary appears in the log and
`/config/metrics.json` is rewritten every `metrics.log_every_s` (600 s) even
with no services at all**, because the host's own work is measured too.
Operations are recorded as `<kind>:<name>` per service: `job:`, `action:`,
`key:`, `cond:`, `vars`, `context`, `on_state`, `http:<op>`,
`ha:<domain.service>`, `shared:<name>`, `timed:<op>`; the pseudo-service
`host` holds the daemon's own (`render_pass`, `image_gen`, `ha_event`).

Log lines:

```
metrics: SLOW soccer job:refresh 6.2s wall, 310ms cpu (budget 2.0s)
metrics: soccer cpu 0.42s (0.07%) wall 3.1s · 4 jobs, 0 err · http 8 req 1.2 MB p95 0.9s · keys 410 p95 1ms
metrics: app cpu 5.1% rss 120 MB threads 38
```

`SLOW` appears at most once per operation per minute (budgets in
`metrics.slow_ms`: job 2000, action 3000, http 5000, key 20, context 5,
on_state 20, cond 5, vars 5 ms). The service lines are ranked by CPU time in
the interval.

Snapshot (`/app_configs/local_streamdock_n3/metrics.json` on the host):

```json
{"at": 1790000000.0, "interval_s": 600.1,
 "process": {"cpu_s": 812.3, "rss_mb": 120.4, "threads": 38},
 "services": {"soccer": {"store_bytes": 2048, "ops": {
   "job:refresh": {
     "total":    {"count": 40, "errors": 0, "wall_s": 12.1, "wall_max_s": 1.1, "p50_s": 0.25,
                  "p95_s": 0.5, "cpu_s": 0.9, "bytes_in": 0, "bytes_out": 0, "status": {}},
     "interval": {"count": 4, "...": "same fields, since the last summary"}}}}}}
```

On the PC, `bash homeassistant/tools/metrics.sh` reads it over SSH and prints a
ranked table:

```bash
bash homeassistant/tools/metrics.sh                          # by CPU, last interval
bash homeassistant/tools/metrics.sh --by wall --window total # by wall time since start
bash homeassistant/tools/metrics.sh --by http|errors [host]
```

Columns: service, op, count, errors, cpu_s, wall_s, p95_s, max_s, http_bytes
(bytes received). Memory cannot be measured per service in one process: look at
`process.rss_mb` and each service's `store_bytes`.
