# Pluggable Services Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let new dock features ("services") be added as self-contained folders under `streamdock_n3/services/` without editing `ha_daemon.py`, with every service operation measured, and move live soccer onto the contract with no change in behaviour.

**Architecture:** A `services` package holds the contract (`Service` base + decorators), a per-service `ServiceContext` (HTTP, store, HA, toasts, signals), a `Scheduler` (jobs on 3 workers, a thread per press), a `Metrics` recorder, and a `ServiceHost` that `ha_daemon.py` calls through ten one-time hooks. The preset places service keys (`service_key` slots) and pages (round cycles). Soccer's `LiveSoccer` moves almost unchanged into `services/soccer/` behind a thin `SoccerService` adapter.

**Tech Stack:** Python 3.12 (app image), PC Python 3.13 for tests; pytest; stdlib only for the new code (`urllib`, `sqlite3`, `threading`, `heapq`).

**Spec:** [docs/superpowers/specs/2026-11-13-pluggable-services-design.md](../specs/2026-11-13-pluggable-services-design.md) (approved 2026-11-13). Read it before any task; section numbers below refer to it.

**Order:** Tasks 1–7 in sequence. Branch first: `git checkout -b feat/pluggable-services` (the repo is on `main`); the spec and this plan go in the first commit.

## Global Constraints

- Public repo: no secrets, tokens, serials, addresses, account data. Never log URLs or HTTP bodies (soccer's URLs carry tokens). Secrets live only in `/config/secrets.json` on the device; `restore.sh` never creates, copies or reads it.
- New code is stdlib only. No new pip packages.
- Service names: `[a-z][a-z0-9_]*`. Namespaced names: conditions and actions `<service>.<name>`, pages `<service>.<page>`, label vars must start with `<service>_`, metric op names `<kind>:<name>`.
- Metric op kinds: `job`, `action`, `key`, `cond`, `vars`, `context`, `on_state`, `http`, `ha`, `timed`, `shared`; host ops `render_pass`, `image_gen`, `ha_event` under service name `host`.
- Default slow budgets (ms): `job` 2000, `action` 3000, `http` 5000, `key` 20, `context` 5, `on_state` 20, `cond` 5, `vars` 5. Slow line at most once per (service, op) per 60 s.
- Breaker: 3 render-path overruns or exceptions within 60 s → that service's render-path calls off for 300 s.
- Scheduler: 3 job workers; job retry 60 s doubling to 1800 s; hang report at 120 s; max 16 running actions per service; minimum job delay 1.0 s.
- Error streak: log the first failure, then one line at the 10th consecutive failure, then one "recovered" line.
- Metrics summary every `metrics.log_every_s` (default 600 s); snapshot `/config/metrics.json`.
- HTTP default timeout 15 s, default `User-Agent: Mozilla/5.0`.
- Store root `/data/services/<name>/`; secrets file `/config/secrets.json`.
- Draw nothing from the SDK reader thread; service calls from it are only conditions inside `resolve_action`.
- Tests run on the PC from the repo root: `python -m pytest homeassistant/streamdock_n3 -q` (after Task 1 adds the root `conftest.py`; before that `homeassistant/streamdock_n3/tests`).
- Deploy: bump `version` in `homeassistant/streamdock_n3/config.yaml` from `0.5.0-32` to `0.6.0-1`. After deploying, update `docs/JOURNAL.md` (final state only) and `DOCS.md`; `restore.sh` must reproduce the live system.

## Review Focus

1. **HA disconnected** (`_watch` clears all states): every service render-path call must survive `ctx.state()` returning None. No crash, no breaker trip from it. → test in Task 6 (`test_context_survives_cleared_states`).
2. **A preset slot names a service that is not loaded** (disabled, misspelled, import failed): the key shows `?`, the press does nothing, one log line, other keys fine. → Task 4 (`test_key_spec_unknown_service_raises_once_logged`), Task 5 (`test_press_on_unknown_service_key_is_ignored`).
3. **Picking expires with no HA event**: soccer's key must leave the "picking" look 60 s after the last press without any state change (today `_tick` polled for it). → Task 6 (`test_pick_schedules_redraw_after_cycle`), Task 4 (`test_tick_signals_due_redraws`).
4. **Snapshot write fails** (read-only `/config`, disk full): the summary still logs and the next summary retries. → Task 1 (`test_write_snapshot_failure_is_logged_not_raised`).
5. **A service's toast with no toast config or not in `toast.events`**: nothing shown, exactly one log line per service. → Task 2 (`test_toast_suppressed_logs_once`).

---

### Task 1: Metrics recorder

**Files:**
- Create: `homeassistant/streamdock_n3/services/__init__.py` (empty for now; Task 2 fills it)
- Create: `homeassistant/streamdock_n3/services/metrics.py`
- Create: `homeassistant/streamdock_n3/conftest.py` (same body as `tests/conftest.py`: put the app dir on `sys.path`)
- Test: `homeassistant/streamdock_n3/tests/test_services_metrics.py`
- Also commit: the spec and this plan.

**Interfaces:**
- Produces:
  - `Histogram()` with `add(seconds: float) -> None`, `quantile(q: float) -> float`, `count: int`. 64 buckets, bucket `i` upper bound `1e-5 * 2 ** (i / 2)`; `quantile` returns the upper bound of the bucket holding the q-th sample (0.0 when empty).
  - `Metrics(slow_ms: dict[str, float] | None = None, log_every_s: float = 600, log: Callable[[str], None] = print, monotonic=time.monotonic, clock=time.time)`
  - `Metrics.record(service: str, op: str, wall: float, cpu: float, *, error: bool = False, bytes_in: int = 0, bytes_out: int = 0, status: str | None = None, check_slow: bool = True) -> bool` (True when over budget)
  - `Metrics.measure(service: str, op: str, *, check_slow: bool = True) -> ContextManager[Measure]`; `Measure` has settable `bytes_in`, `bytes_out`, `status`, and `over_budget: bool` after exit. Wall from `time.perf_counter`, CPU from `time.thread_time`. An exception marks `error=True` and re-raises.
  - `Metrics.summary_lines(process: dict) -> list[str]`, `Metrics.snapshot(process: dict, store_bytes: dict[str, int]) -> dict`, `Metrics.roll() -> None` (starts a new interval), `Metrics.write_snapshot(path: Path, snapshot: dict) -> None` (atomic, never raises; logs `metrics: snapshot not written (<err>)`).
  - `process_stats(proc: Path = Path("/proc/self")) -> dict` → `{"cpu_s": float, "rss_mb": float, "threads": int}`, `{}` when `/proc` is missing.
  - `format_table(snapshot: dict, by: str = "cpu", window: str = "interval") -> str` and a `__main__` that reads a snapshot from stdin: `python services/metrics.py --by cpu --window interval`.
  - Snapshot shape: `{"at": epoch_s, "interval_s": float, "process": {...}, "services": {svc: {"store_bytes": int, "ops": {op: {"total": Stats, "interval": Stats}}}}}`, `Stats = {"count", "errors", "wall_s", "wall_max_s", "p50_s", "p95_s", "cpu_s", "bytes_in", "bytes_out", "status": {...}}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_histogram_quantiles():
    h = Histogram()
    for _ in range(95): h.add(0.001)
    for _ in range(5): h.add(1.0)
    assert h.count == 100
    assert 0.001 <= h.quantile(0.5) < 0.0015
    assert 1.0 <= h.quantile(0.99) < 1.5
    assert Histogram().quantile(0.5) == 0.0

def test_record_accumulates_both_windows():
    m = Metrics()
    m.record("soccer", "job:refresh", 0.5, 0.1)
    m.record("soccer", "job:refresh", 1.5, 0.2, error=True)
    ops = m.snapshot({}, {})["services"]["soccer"]["ops"]["job:refresh"]
    assert ops["total"]["count"] == 2 and ops["total"]["errors"] == 1
    assert ops["total"]["wall_max_s"] == 1.5 and abs(ops["total"]["cpu_s"] - 0.3) < 1e-9
    m.roll()
    assert m.snapshot({}, {})["services"]["soccer"]["ops"]["job:refresh"]["interval"]["count"] == 0

def test_slow_line_rate_limited_per_op():
    lines, t = [], [0.0]
    m = Metrics(log=lines.append, monotonic=lambda: t[0])
    assert m.record("soccer", "job:refresh", 6.2, 0.31) is True
    assert lines == ["metrics: SLOW soccer job:refresh 6.2s wall, 0.31s cpu (budget 2.0s)"]
    m.record("soccer", "job:refresh", 6.2, 0.31); assert len(lines) == 1
    t[0] = 61.0; m.record("soccer", "job:refresh", 6.2, 0.31); assert len(lines) == 2

def test_check_slow_false_and_unknown_kind_never_warn():
    lines = []
    m = Metrics(log=lines.append)
    assert m.record("soccer", "job:feed", 999, 1, check_slow=False) is False
    assert m.record("host", "render_pass", 999, 1, check_slow=False) is False
    assert lines == []

def test_measure_records_error_and_reraises():
    m = Metrics()
    with pytest.raises(ValueError):
        with m.measure("bets", "http:odds") as h:
            h.bytes_in, h.status = 1200, "5xx"
            raise ValueError("x")
    st = m.snapshot({}, {})["services"]["bets"]["ops"]["http:odds"]["total"]
    assert st["errors"] == 1 and st["bytes_in"] == 1200 and st["status"] == {"5xx": 1}

def test_summary_ranks_services_by_interval_cpu():
    m = Metrics()
    m.record("a", "job:x", 0.1, 0.01); m.record("b", "job:x", 0.1, 0.5)
    lines = m.summary_lines({"cpu_s": 1.0, "rss_mb": 80.0, "threads": 12})
    assert lines[0].startswith("metrics: b cpu 0.50s") and lines[1].startswith("metrics: a cpu")
    assert lines[-1].startswith("metrics: app ") and "rss 80 MB" in lines[-1] and "threads 12" in lines[-1]

def test_write_snapshot_failure_is_logged_not_raised(tmp_path):
    lines = []
    m = Metrics(log=lines.append)
    m.write_snapshot(tmp_path / "missing-dir" / "metrics.json", {"x": 1})
    assert lines and lines[0].startswith("metrics: snapshot not written")

def test_process_stats_without_proc(tmp_path):
    assert process_stats(tmp_path / "nope") == {}

def test_format_table_orders_by_key():
    m = Metrics(); m.record("a", "job:x", 0.1, 0.01); m.record("b", "http:y", 3.0, 0.02, error=True)
    snap = m.snapshot({}, {})
    rows = format_table(snap, by="wall").splitlines()
    assert "b" in rows[1] and "http:y" in rows[1]
    assert "a" in format_table(snap, by="cpu").splitlines()[2]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest homeassistant/streamdock_n3/tests/test_services_metrics.py -q`
Expected: FAIL (`ModuleNotFoundError: services.metrics`)

- [ ] **Step 3: Implement `services/metrics.py`**

The summary line format (exact, per service; parts with zero counts are left out):
`metrics: {svc} cpu {cpu:.2f}s ({pct:.2f}%) wall {wall:.1f}s · {n} jobs, {err} err · http {req} req {mb:.1f} MB p95 {p95:.1f}s · keys {k} p95 {kp95_ms:.0f}ms`
where `pct = cpu / interval_s * 100`. App line: `metrics: app cpu {pct:.1f}% rss {rss:.0f} MB threads {n}` (CPU % from the `cpu_s` delta between summaries). One lock guards all stats. `format_table` columns: service, op, count, errors, cpu_s, wall_s, p95_s, max_s, http bytes; sorted descending by `cpu_s` / `wall_s` / `bytes_in` / `errors` for `by` = cpu / wall / http / errors.

- [ ] **Step 4: Run to verify they pass** — same command, Expected: PASS. Then the full suite `python -m pytest homeassistant/streamdock_n3 -q`: all pass (root conftest collects nothing new beyond `tests/`).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-11-13-pluggable-services-design.md docs/superpowers/plans/2026-11-13-pluggable-services.md homeassistant/streamdock_n3/conftest.py homeassistant/streamdock_n3/services homeassistant/streamdock_n3/tests/test_services_metrics.py
git commit -m "services: metrics recorder (spec, plan)"
```

---

### Task 2: Contract, context and test fakes

**Files:**
- Modify: `homeassistant/streamdock_n3/services/__init__.py`
- Create: `homeassistant/streamdock_n3/services/context.py`
- Create: `homeassistant/streamdock_n3/services/testing.py`
- Test: `homeassistant/streamdock_n3/tests/test_services_contract.py`, `homeassistant/streamdock_n3/tests/test_services_context.py`

**Interfaces:**
- Consumes: `Metrics.measure`, `Metrics.record` (Task 1); `media_actions.ensure_on(call, state_of, cfg, sleep=, clock=)`, `select_atv_input(call, state_of, cfg)`, `launch(call, state_of, cfg, device, app)`; `toast.Toaster` (`.events: set[str]`, `.show(message, channel)`).
- Produces (`services/__init__.py`):
  - decorators `job(name: str, *, first_in: float = 0.0, long_running: bool = False)`, `action(name: str)`, `condition(name: str)`
  - `@dataclass(frozen=True) JobSpec(name: str, fn: Callable[[], float | None], first_in: float, long_running: bool)`
  - `class Service`: `name: str`; overridable `start(ctx)`, `entities() -> set[str]`, `vars() -> dict[str, str]`, `key(name: str, args: dict) -> dict | None`, `pages() -> dict[str, dict]`, `context(layout: str) -> str | None`, `on_state(changed: set[str])`, `stop()` (defaults: no-op / empty / None); collectors `jobs() -> list[JobSpec]`, `actions() -> dict[str, Callable[[dict], None]]`, `conditions() -> dict[str, Callable[[], bool]]` (bound methods found by decorator markers).
  - `load(sections: dict, *, importer=importlib.import_module, log=print) -> list[Service]` — config order; skips non-dict sections and `"enabled": false`; imports `services.<name>`, instantiates its `SERVICE`; a bad name, import error, missing `SERVICE` or `SERVICE.name != name` logs `services: <name> disabled (<reason>)` and skips.
- Produces (`services/context.py`):
  - `Opener = Callable[[str, str, bytes | None, dict, float], tuple[int, bytes]]` (method, url, body, headers, timeout) → (status, body); the default uses `urllib.request` and raises `urllib.error.HTTPError` for ≥ 400 like `urlopen`.
  - `Http(service: str, metrics: Metrics, opener: Opener | None = None)`: `get(url, *, op, headers=None, timeout=15.0) -> str`, `get_json(...) -> Any`, `post(url, *, op, data: bytes | dict | None = None, headers=None, timeout=15.0) -> str` (dict → JSON body). Records `http:<op>` with bytes and status `2xx`/`3xx`/`4xx`/`5xx`/`timeout`/`error`; re-raises errors unchanged.
  - `Store(root: Path)`: `load(key: str, default=None)`, `save(key: str, value) -> None` (atomic `<key>.json`), `sqlite(name: str) -> sqlite3.Connection` (`check_same_thread=False`), `size_bytes() -> int`. Creates `root` on first write.
  - `ServiceContext(name, config, *, metrics, call, call_response, state_of, toaster, signal, context_signal, redraw_in, show_page, shared: dict | None = None, data_root=Path("/data/services"), secrets_path=Path("/config/secrets.json"), icons_root: Path | None = None, clock=time.time, monotonic=time.monotonic, sleep=time.sleep, opener=None, log=print)` with members exactly as spec §5.5: `config`, `secret(name)`, `http`, `store`, `state(entity)`, `call(domain, service, data)`, `call_response(...)`, `media` (`ensure_on()`, `select_atv_input()`, `launch(device, app)` bound to this service's `config`, `call`, `state`), `toast(message, channel=None)`, `changed()`, `redraw_in(seconds)`, `context_changed()`, `show_page(page)` (prefixes `<name>.`), `log(msg)` (`"<name>: msg"`), `clock()`, `monotonic()`, `sleep(s)`, `timed(op)`, `shared(name)`, `icon(name) -> str` (`<icons_root>/<name>/icons/<icon>.png` if it exists, else the bare id). `icons_root` defaults to the `services` package directory.
  - `ctx.call` records `ha:<domain>.<service>`. `ctx.shared(name)` returns None or a proxy timing each method call under `shared:<name>`.
- Produces (`services/testing.py`): `fake_context(name="test", config=None, states=None, http=None, secrets=None, now=1_000_000.0, tmp: Path | None = None) -> ServiceContext` — a real `ServiceContext` wired to fakes, with `ctx.fakes` holding `calls: list[tuple]`, `toasts: list[tuple[str, str | None]]`, `signals: int`, `context_signals: int`, `redraws: list[float]`, `pages: list[str]`, `urls: list[str]`, `time` (`.now`, `sleep` advances it, `clock`/`monotonic` read it). `http` maps a URL substring to a `str` body, an `Exception` to raise, or a callable `url -> str`; an unmatched URL raises `AssertionError`. Toasts are recorded only if the fake toaster's events include `name` (default: they do). A temp dir backs the store when `tmp` is None.

- [ ] **Step 1: Write the failing tests**

```python
# test_services_contract.py
class Demo(Service):
    name = "demo"
    @job("poll", first_in=5)
    def poll(self): return 30.0
    @action("go")
    def go(self, args): self.went = args
    @condition("on")
    def on(self): return True

def test_collectors_find_decorated_methods():
    d = Demo()
    assert [(j.name, j.first_in, j.long_running) for j in d.jobs()] == [("poll", 5, False)]
    assert d.jobs()[0].fn() == 30.0
    d.actions()["go"]({"x": 1}); assert d.went == {"x": 1}
    assert d.conditions()["on"]() is True

def test_defaults_are_empty():
    s = Service()
    assert s.entities() == set() and s.vars() == {} and s.pages() == {}
    assert s.key("k", {}) is None and s.context("idle") is None

def test_load_order_enabled_and_failures():
    mods = {"services.demo": types.SimpleNamespace(SERVICE=Demo)}
    def importer(n):
        if n not in mods: raise ImportError(n)
        return mods[n]
    lines = []
    got = load({"demo": {}, "off": {"enabled": False}, "ghost": {}, "Bad-Name": {}, "junk": 3},
               importer=importer, log=lines.append)
    assert [s.name for s in got] == ["demo"]
    assert any(l.startswith("services: ghost disabled") for l in lines)
    assert any(l.startswith("services: Bad-Name disabled") for l in lines)
    assert not any("off" in l for l in lines)

def test_load_rejects_name_mismatch():
    class Other(Service): name = "other"
    lines = []
    assert load({"demo": {}}, importer=lambda n: types.SimpleNamespace(SERVICE=Other), log=lines.append) == []
    assert "disabled" in lines[0]
```

```python
# test_services_context.py
def test_http_get_records_bytes_and_status():
    ctx = fake_context(http={"example.com/a": "hello"})
    assert ctx.http.get("https://example.com/a", op="a") == "hello"
    st = ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]["http:a"]["total"]
    assert st["count"] == 1 and st["bytes_in"] == 5 and st["status"] == {"2xx": 1}

def test_http_error_is_recorded_and_reraised():
    err = urllib.error.HTTPError("u", 403, "no", {}, None)
    ctx = fake_context(http={"x.com": err})
    with pytest.raises(urllib.error.HTTPError):
        ctx.http.get("https://x.com/", op="x")
    assert ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]["http:x"]["total"]["status"] == {"4xx": 1}

def test_http_never_logs_urls(capsys):
    ctx = fake_context(http={"x.com": RuntimeError("boom")})
    with pytest.raises(RuntimeError):
        ctx.http.get("https://x.com/?utsk=SECRET", op="x")
    assert "SECRET" not in capsys.readouterr().out

def test_store_roundtrip_and_size(tmp_path):
    s = Store(tmp_path / "svc")
    assert s.load("k", {"d": 1}) == {"d": 1}
    s.save("k", {"a": [1, 2]}); assert Store(tmp_path / "svc").load("k") == {"a": [1, 2]}
    assert s.size_bytes() > 0
    db = s.sqlite("hist"); db.execute("create table t(x)"); db.commit()
    assert (tmp_path / "svc" / "hist.sqlite").exists()

def test_secret_reads_only_this_service(tmp_path):
    ctx = fake_context(name="bets", secrets={"bets": {"api_key": "k1"}, "other": {"api_key": "k2"}})
    assert ctx.secret("api_key") == "k1" and ctx.secret("nope") is None

def test_call_records_ha_metric_and_forwards():
    ctx = fake_context()
    ctx.call("media_player", "play_media", {"entity_id": "m"})
    assert ctx.fakes.calls == [("media_player", "play_media", {"entity_id": "m"})]
    assert "ha:media_player.play_media" in ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]

def test_toast_suppressed_logs_once(capsys):
    ctx = fake_context(name="bets", toast_events={"soccer"})
    ctx.toast("a"); ctx.toast("b")
    assert ctx.fakes.toasts == []
    assert capsys.readouterr().out.count("toast: bets not in toast.events; not shown") == 1

def test_toast_without_toaster_logs_once(capsys):
    ctx = fake_context(name="bets", toast_events=None)  # None: no toast config
    ctx.toast("a"); ctx.toast("b")
    assert capsys.readouterr().out.count("toast: no toast config; not shown") == 1

def test_signals_and_pages_are_namespaced():
    ctx = fake_context(name="bets")
    ctx.changed(); ctx.context_changed(); ctx.redraw_in(60); ctx.show_page("slip")
    f = ctx.fakes
    assert (f.signals, f.context_signals, f.redraws, f.pages) == (1, 1, [60], ["bets.slip"])

def test_icon_prefers_service_folder(tmp_path):
    (tmp_path / "bets" / "icons").mkdir(parents=True); (tmp_path / "bets" / "icons" / "slip.png").write_bytes(b"x")
    ctx = fake_context(name="bets", icons_root=tmp_path)
    assert ctx.icon("slip") == str(tmp_path / "bets" / "icons" / "slip.png")
    assert ctx.icon("soccer_live__live") == "soccer_live__live"

def test_shared_proxy_times_calls():
    class Touch:
        def swipe(self, n): return n * 2
    ctx = fake_context(shared={"atv_touch": Touch()})
    assert ctx.shared("atv_touch").swipe(3) == 6 and ctx.shared("youtube") is None
    assert "shared:atv_touch" in ctx._metrics.snapshot({}, {})["services"]["test"]["ops"]

def test_sleep_advances_fake_time():
    ctx = fake_context(now=100.0); ctx.sleep(5)
    assert ctx.clock() == 105.0 and ctx.monotonic() == 105.0
```

(`fake_context` therefore also takes `toast_events: set[str] | None = <all>` and `icons_root`, `shared`; `ctx._metrics` is the context's `Metrics`.)

- [ ] **Step 2: Run to verify they fail** — `python -m pytest homeassistant/streamdock_n3/tests/test_services_contract.py homeassistant/streamdock_n3/tests/test_services_context.py -q`, Expected: FAIL (import errors).

- [ ] **Step 3: Implement** `services/__init__.py`, `services/context.py`, `services/testing.py` to the interfaces above. Decorators set a marker attribute (`_service_job = JobSpec-args`, `_service_action = name`, `_service_condition = name`) that the collectors read via `inspect.getmembers(type(self))`. Toast suppression is decided in `ServiceContext.toast` (it reads `toaster.events`), so `toast.py` does not change.

- [ ] **Step 4: Run to verify they pass** — same command, then the full suite. Expected: PASS.

- [ ] **Step 5: Commit** — `git add homeassistant/streamdock_n3/services homeassistant/streamdock_n3/tests/test_services_c*.py && git commit -m "services: contract, context, test fakes"`

---

### Task 3: Scheduler

**Files:**
- Create: `homeassistant/streamdock_n3/services/scheduler.py`
- Test: `homeassistant/streamdock_n3/tests/test_services_scheduler.py`

**Interfaces:**
- Consumes: `JobSpec` (Task 2), `Metrics.measure` (Task 1).
- Produces: `Scheduler(metrics: Metrics, *, workers: int = 3, retry_s: float = 60, max_retry_s: float = 1800, hang_s: float = 120, max_actions: int = 16, monotonic=time.monotonic, log=print, spawn: Callable[[Callable[[], None]], None] | None = None)`
  - `add_job(service: str, spec: JobSpec) -> None` (first run at `now + spec.first_in`)
  - `run_pending(now: float) -> int` — runs every due, not-running, non-long-running job inline (for tests and for the worker loop's body); returns how many ran.
  - `start() -> None` — starts `workers` threads that sleep until the earliest due job and run it via the same code path; long-running jobs each get a thread through `spawn` that reruns the job after it returns (delay = its return value, or the retry delay after an exception).
  - `run_action(service: str, name: str, fn: Callable[[dict], None], args: dict) -> bool` — False (and `services: <svc> action:<name> dropped (16 running)`) at the cap; else `spawn`s a thread measuring `action:<name>`; exceptions logged `services: <svc> action:<name> failed: <Type>: <err>`.
  - `check_hangs(now: float) -> None` — logs `services: HUNG <svc> <op> running <n>s` once per run past `hang_s`.
  - `stop() -> None`.
- Rules: a job returning a float runs again after `max(1.0, value)`; None → never again; an exception → retry after `min(retry_s * 2 ** (streak - 1), max_retry_s)`. Logging: first failure `services: <svc> job:<name> failed: <Type>: <err>`; at streak 10 `services: <svc> job:<name> failing (10 in a row): <Type>: <err>`; after a success that ends a streak `services: <svc> job:<name> recovered`. Long-running jobs are measured with `check_slow=False`.

- [ ] **Step 1: Write the failing tests** (a fake `spawn` that runs the function inline or records it; `monotonic` from a list cell)

```python
def test_job_reschedules_by_return_value_with_floor():
    s, ran = sched(), []
    s.add_job("a", JobSpec("p", lambda: ran.append(1) or 0.2, first_in=0, long_running=False))
    assert s.run_pending(0.0) == 1 and s.run_pending(0.5) == 0 and s.run_pending(1.0) == 1

def test_first_in_delays_first_run():
    s = sched(); s.add_job("a", JobSpec("p", lambda: 10.0, 5, False))
    assert s.run_pending(4.9) == 0 and s.run_pending(5.0) == 1

def test_none_means_one_shot():
    s = sched(); s.add_job("a", JobSpec("p", lambda: None, 0, False))
    assert s.run_pending(0) == 1 and s.run_pending(10_000) == 0

def test_failures_back_off_and_log_streak(capsys):
    s = sched(); s.add_job("a", JobSpec("p", boom, 0, False))
    t, delays = 0.0, []
    for _ in range(11):
        s.run_pending(t); nxt = s.next_due(); delays.append(nxt - t); t = nxt
    assert delays[:6] == [60, 120, 240, 480, 960, 1800] and delays[10] == 1800
    out = capsys.readouterr().out
    assert out.count("services: a job:p failed: RuntimeError: boom") == 1
    assert out.count("services: a job:p failing (10 in a row)") == 1

def test_recovery_is_logged(capsys): ...  # boom once, then 30.0 → "services: a job:p recovered"

def test_action_cap_per_service(capsys):
    pending = []
    s = sched(spawn=pending.append)                # spawned threads never start
    assert all(s.run_action("a", "go", lambda a: None, {}) for _ in range(16))
    assert s.run_action("a", "go", lambda a: None, {}) is False
    assert s.run_action("b", "go", lambda a: None, {}) is True
    assert "services: a action:go dropped (16 running)" in capsys.readouterr().out

def test_action_exception_logged_and_measured(capsys):
    s = sched(spawn=lambda fn: fn())
    s.run_action("a", "go", lambda a: 1 / 0, {})
    assert "services: a action:go failed: ZeroDivisionError" in capsys.readouterr().out
    assert s._metrics.snapshot({}, {})["services"]["a"]["ops"]["action:go"]["total"]["errors"] == 1

def test_hung_action_reported_once(capsys):
    held = []
    s = sched(spawn=held.append)               # the action thread never runs: it stays "running"
    s.run_action("a", "go", lambda a: None, {}) # started at monotonic 0
    s.check_hangs(119); s.check_hangs(121); s.check_hangs(300)
    assert capsys.readouterr().out.count("services: HUNG a action:go running 121s") == 1

def test_job_never_runs_twice_concurrently(): ...  # mark running, run_pending while running → 0
```

(`next_due() -> float | None` is a small public helper for tests and the worker loop; add it to the interface. `boom` raises `RuntimeError("boom")`.)

- [ ] **Step 2: Run to verify they fail** — `python -m pytest homeassistant/streamdock_n3/tests/test_services_scheduler.py -q`, Expected: FAIL.
- [ ] **Step 3: Implement** with a `heapq` of `(due, seq, job)` under one `threading.Condition`; a job's `running` flag and `started_at` are set before release and cleared in `_finish`.
- [ ] **Step 4: Run to verify they pass**, then the full suite.
- [ ] **Step 5: Commit** — `git commit -m "services: scheduler"` (with the two files).

---

### Task 4: Service host

**Files:**
- Create: `homeassistant/streamdock_n3/services/host.py`
- Modify: `homeassistant/streamdock_n3/tabs.py` (add `TabEngine.show`)
- Test: `homeassistant/streamdock_n3/tests/test_services_host.py`, `homeassistant/streamdock_n3/tests/test_tabs.py` (one new test)

**Interfaces:**
- Consumes: `load`, `Service`, `ServiceContext` (Task 2); `Scheduler` (Task 3); `Metrics`, `process_stats` (Task 1).
- Produces:
  - `TabEngine.show(page: str, now: float) -> bool` — opens `page` as the temporary page with the `open_s` deadline (same as a round press); False if `page` is not a known page.
  - `class ServiceUnavailable(Exception)`
  - `ServiceHost.load(config: dict, *, call, call_response, state_of, toaster, signal: Callable[[], None], context_signal: Callable[[], None], show_page: Callable[[str], bool], shared: dict | None = None, importer=importlib.import_module, monotonic=time.monotonic, clock=time.time, data_root=Path("/data/services"), secrets_path=Path("/config/secrets.json"), snapshot_path=Path("/config/metrics.json"), spawn=None, log=print) -> ServiceHost` — reads `config["services"]` and `config["metrics"]` (`log_every_s`, `slow_ms` merged over the defaults).
  - `entities() -> set[str]`; `start() -> None` (calls each `service.start(ctx)`; one that raises is dropped with `services: <svc> disabled (<Type>: <err>)`; registers jobs and the host's own `metrics` job (service name `host`, first run after `log_every_s`), which logs `summary_lines(process_stats())`, writes `snapshot(process_stats(), {svc: ctx.store.size_bytes()})` to `snapshot_path` and `roll()`s, every `log_every_s`; starts the scheduler); `stop() -> None`.
  - `names() -> list[str]` (running services, config order).
  - `pages() -> dict[str, dict]` — `<svc>.<page>` → page dict; called once before the tab engine is built.
  - `conditions() -> dict[str, Callable[[], bool]]` — `<svc>.<cond>`; each wrapper returns False while the breaker is open; measured as `cond:<name>`; an exception counts a strike and returns False.
  - `vars() -> dict[str, str]` — merged; a key without the `<svc>_` prefix is dropped and logged once.
  - `key_spec(slot: dict) -> dict | None` — `slot["service_key"] = "<svc>.<key>"`, `slot.get("args", {})`; returns `{**service_spec, **overrides}` where overrides are the slot's other fields; None when the service returns None; raises `ServiceUnavailable` for an unknown service/key name or an open breaker (logged once per message: `services: key <service_key>: <reason>`).
  - `run_action(service: str, do: str, args: dict | None = None) -> None` — unknown service or action logs `services: ignoring <svc>.<do>`.
  - `context(layout: str) -> str` — services in order; the first non-None wins; measured `context`.
  - `on_state(changed: set[str]) -> None` — calls each service whose `entities()` meets `changed`, with the intersection; measured `on_state`.
  - `tick(now: float) -> None` — calls `signal()` once when any `redraw_in` deadline passed; `check_hangs`; closes breakers whose 300 s ran out (`services: <svc> render calls back on`).
  - `measure_host(op: str) -> ContextManager` — `Metrics.measure("host", op, check_slow=False)`.
- Breaker (exact): strikes are render-path overruns (`Measure.over_budget`) or exceptions in `key`, conditions, `vars`, `context`, `on_state`; 3 strikes within 60 s → open for 300 s, log `services: <svc> render calls off for 5 min (<reason>)`.

- [ ] **Step 1: Write the failing tests** (a `Demo` service via a fake importer; fake `monotonic`; `spawn=lambda fn: fn()`)

```python
def test_tabs_show_opens_known_page_only():
    e = TabEngine({"x.p": {"keys": {}}, "context:idle": {"keys": {}}}, {})
    assert e.show("x.p", 0.0) is True and e.visible == "x.p"
    assert e.show("nope", 0.0) is False
    e.tick(10.1); assert e.visible == "context:idle"

def test_names_pages_conditions_are_namespaced():
    h = host({"demo": {}}); h.start()
    assert h.names() == ["demo"] and "demo.slip" in h.pages()
    assert h.conditions()["demo.on"]() is True

def test_key_spec_merges_preset_overrides():
    h = host({"demo": {}}); h.start()
    spec = h.key_spec({"service_key": "demo.k", "args": {"i": 2}, "label": "X"})
    assert spec["label"] == "X" and spec["icon"] == "demo_icon" and spec["action"] == {"service": "demo", "do": "go", "args": {"i": 2}}

def test_key_spec_unknown_service_raises_once_logged(capsys):
    h = host({}); h.start()
    for _ in range(2):
        with pytest.raises(ServiceUnavailable): h.key_spec({"service_key": "bets.slot"})
    assert capsys.readouterr().out.count("services: key bets.slot:") == 1

def test_vars_require_prefix(capsys):
    h = host({"demo": {}}); h.start()          # Demo.vars() returns {"demo_n": "3", "n": "x"}
    assert h.vars() == {"demo_n": "3"}
    h.vars(); assert capsys.readouterr().out.count("services: demo var 'n' dropped") == 1

def test_context_first_non_none_wins():  # demo returns "live_game" for "streaming", None otherwise
    h = host({"demo": {}}); h.start()
    assert h.context("streaming") == "live_game" and h.context("idle") == "idle"

def test_breaker_trips_after_three_strikes_and_resets(capsys):
    t = [0.0]; h = host({"slow": {}}, monotonic=lambda: t[0]); h.start()   # Slow.key raises
    for _ in range(3):
        with pytest.raises(Exception): h.key_spec({"service_key": "slow.k"})
    with pytest.raises(ServiceUnavailable): h.key_spec({"service_key": "slow.k"})
    assert h.conditions()["slow.c"]() is False
    assert "services: slow render calls off for 5 min" in capsys.readouterr().out
    t[0] = 301; h.tick(301)
    assert "services: slow render calls back on" in capsys.readouterr().out

def test_failing_start_disables_only_that_service(capsys): ...
def test_run_action_dispatches_and_ignores_unknown(capsys): ...
def test_on_state_gets_intersection_only(): ...
def test_tick_signals_due_redraws():
    signals = []; t = [0.0]
    h = host({"demo": {}}, signal=lambda: signals.append(1), monotonic=lambda: t[0]); h.start()
    h._ctx["demo"].redraw_in(60)
    h.tick(59.9); assert signals == []
    h.tick(60.0); assert signals == [1]
    h.tick(61.0); assert signals == [1]
def test_metrics_job_logs_and_writes_snapshot(tmp_path, capsys): ...   # run the host job once; file exists; "metrics: app" logged
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest homeassistant/streamdock_n3/tests/test_services_host.py homeassistant/streamdock_n3/tests/test_tabs.py -q`, Expected: FAIL.
- [ ] **Step 3: Implement** `host.py` and `TabEngine.show`. Each service gets one `ServiceContext` (`self._ctx[name]`) whose `redraw_in` stores `monotonic() + s` in a host list and whose `show_page` calls the host's `show_page` callback.
- [ ] **Step 4: Run to verify they pass**, then the full suite.
- [ ] **Step 5: Commit** — `git commit -m "services: host, TabEngine.show"`.

---

### Task 5: Wire the host into the daemon

**Files:**
- Modify: `homeassistant/streamdock_n3/ha_daemon.py` (hooks in spec §5.6; module docstring gets a "13. Services" entry)
- Modify: `homeassistant/streamdock_n3/Dockerfile` (add `COPY services /opt/streamdock/services`)
- Test: `homeassistant/streamdock_n3/tests/test_services_daemon.py`

**Interfaces:**
- Consumes: `ServiceHost` (Task 4), `ServiceUnavailable`.
- Produces in `ha_daemon.py`: module global `_host: ServiceHost | None`; `_resolve_slot(spec: dict | None) -> dict | None` (a spec with `service_key` → `_host.key_spec(spec)`; without a host → raises `ServiceUnavailable("no services")`; anything else unchanged).

Hook list (each a few lines; soccer stays as it is until Task 6):
- `main()`: build `_host = ServiceHost.load(config, call=_core.call, call_response=_core.call_response, state_of=state_of, toaster=_toaster, signal=_signal, context_signal=_update_context, show_page=lambda p: _engine.show(p, time.monotonic()), shared={"atv_touch": touch, "youtube": _youtube})` **after** the toaster, YouTube and touch objects exist and **before** `_make_engine`; `_make_engine` merges `_host.pages()` into `pages`; `entities |= _host.entities()`; `_conds.update(_host.conditions())`; `_host.start()` before the watcher thread starts. `touch` is hoisted so it's None when there's no `atv` section.
- `_vars()`: `{**soccer vars, **_host.vars()}`.
- `_render_pass`: `spec = _resolve_slot(keys.get(str(k)))` inside a try that turns `ServiceUnavailable` and any exception into `ERROR_LOOK` for that key (reuse `_look_for`'s logging); wrap the whole pass in `_host.measure_host("render_pass")` and each `_image_for` call in `measure_host("image_gen")`.
- `_run_actions`: in the `tab_key` branch resolve the slot with `_resolve_slot` before `_pressed_phase`; a `ServiceUnavailable` is printed as `tabs: <err>` and the press does nothing. New branch: `{"service": svc, "do": name, "args": {...}}` → `_host.run_action(svc, name, args)` (dry run prints `service (dry run): ...`).
- `_update_context`: `layout = _host.context(layout)` before `set_context`.
- `_watch`: after each event, `_host.on_state(changed)` with `changed = set(a) | set(c) | set(r)`, inside `measure_host("ha_event")` together with `_update_context()`.
- `_tick`: `_host.tick(now)`.

- [ ] **Step 1: Write the failing tests.** Import `ha_daemon` with the SDK modules stubbed the same way `test_render_plan.py` / `test_dynamic_render.py` do (follow their pattern), install a host with a fake `Demo` service, and check:

```python
def test_service_key_slot_renders_service_look(daemon_with_demo): ...      # key 1 look == Look(icon_path("demo_icon"), "X", None)
def test_unavailable_service_key_shows_error_look(daemon_without_host): ... # look is ERROR_LOOK, other keys drawn
def test_press_on_service_key_runs_service_action(daemon_with_demo): ...    # host.run_action called with ("demo", "go", {"i": 2})
def test_press_on_unknown_service_key_is_ignored(daemon_without_host, capsys): ...  # no exception; "tabs:" line
def test_service_action_branch(daemon_with_demo): ...                       # _run_actions({"service": "demo", "do": "go"})
def test_context_hook_applies(daemon_with_demo): ...                        # streaming → live_game via host.context
```

- [ ] **Step 2: Run to verify they fail** — `python -m pytest homeassistant/streamdock_n3/tests/test_services_daemon.py -q`, Expected: FAIL.
- [ ] **Step 3: Implement the hooks and the Dockerfile line.**
- [ ] **Step 4: Run the full suite** — `python -m pytest homeassistant/streamdock_n3 -q`, Expected: all pass (no existing test changes in this task).
- [ ] **Step 5: Commit** — `git commit -m "daemon: host services (hooks, Dockerfile)"`.

---

### Task 6: Move live soccer onto the contract

**Files:**
- Create: `homeassistant/streamdock_n3/services/soccer/__init__.py` (`from .service import SoccerService as SERVICE`)
- Create: `homeassistant/streamdock_n3/services/soccer/listing.py` (Game, constants, `parse_tokens`, `parse_games`, `next_start`, `poll_interval`, `retry_interval`, `order` and the helpers they use, moved verbatim from `soccer.py`)
- Create: `homeassistant/streamdock_n3/services/soccer/live.py` (`LiveSoccer`, moved from `soccer.py`)
- Create: `homeassistant/streamdock_n3/services/soccer/service.py`
- Move: `tests/test_soccer.py` → `services/soccer/tests/test_soccer_live.py` (imports change to `services.soccer.listing` / `services.soccer.live`)
- Create: `services/soccer/tests/test_soccer_service.py`
- Delete: `homeassistant/streamdock_n3/soccer.py`; remove it from the Dockerfile `COPY` list.
- Modify: `ha_daemon.py` (remove `_soccer`, `SOCCER_COMMANDS`, the `soccer` action branch, `soccer_live`/`soccer_picking` in `_conds`, `_watching_game`, the soccer parts of `_vars`/`_tick`/`main`; docstring item 8 now points at the service), `context.py` (drop `foreground_layout`'s `watching_game` parameter), `tests/test_context.py` (drop the `watching_game=True` cases; the `live_game` case moves to `test_soccer_service.py`).
- Modify: `presets/tv_room.json`, `tests/test_config.py`.

**Interfaces:**
- Consumes: `Service`, decorators, `fake_context` (Task 2).
- Changes to `LiveSoccer` (everything else verbatim):
  - constructor gains `on_pick: Callable[[], None] | None = None`, called in `select()` and `play()` right after `_on_change()`.
  - `poll_forever()` is replaced by `poll_once() -> float`: the body of one loop pass, returning the wait it used to sleep.
  - `http_get` moves out; `fetch` stays a required-by-default injected callable.
- Produces `SoccerService(Service)`, `name = "soccer"`:
  - `start(ctx)`: builds `LiveSoccer(ctx.call, ctx.state, ctx.config, fetch=self._fetch, clock=ctx.clock, sleep=ctx.sleep, on_change=ctx.changed, monotonic=ctx.monotonic, notify=ctx.toast, on_pick=lambda: ctx.redraw_in(CYCLE_S + 0.1))`.
  - `_fetch(url) -> str`: `ctx.http.get(url, op="token_page" if url == config["token_page"] else "shelf")`.
  - `entities()`: `{config["lg"], config["atv"]}` (present ones).
  - `@job("refresh", first_in=0)` → `self.live.poll_once()`.
  - `@action("start")` → `live.start()`; `@action("next")` → `live.next()`.
  - `@condition("live")` → `bool(live.games())`; `@condition("picking")` → `live.picking()`.
  - `vars()` → `live.vars()`.
  - `key("live", args)` → exactly today's start key with the new names:
    `{"icon": "soccer_live__none", "label": "", "modes": [{"when": "soccer.picking", "icon": "soccer_live__live", "label": "{soccer_pos}", "color": "#1d6b32"}, {"when": "soccer.live", "icon": "soccer_live__live", "label": "{soccer_count} LIVE"}], "action": {"service": "soccer", "do": "start"}}`
  - `key("next", args)` → `{"label": "NEXT\\n{soccer_pos}", "color": "#1d6b32", "action": {"service": "soccer", "do": "next"}}`
  - other key names → None.
  - `context(layout)` → `"live_game"` if `layout == "streaming"` and `live.watching(ctx.state)`, else None.
- Preset (`tv_room.json`): top-level `"soccer"` becomes `"services": {"soccer": {…same fields…}}`; add `"metrics": {"log_every_s": 600}`; slots `apps_more` 6 and `context:idle` 2 become `{"service_key": "soccer.live"}`, `context:live_game` 4 becomes `{"service_key": "soccer.next"}`. `toast.events` keeps `"soccer"`.

- [ ] **Step 1: Move the code and its tests** (`git mv soccer.py services/soccer/live.py`, then split the pure part into `listing.py`; `git mv tests/test_soccer.py services/soccer/tests/test_soccer_live.py`). Rewrite only the poll tests (`test_failed_first_fetch_retries_after_poll_s`, `test_poll_backs_off_on_consecutive_errors`, `test_poll_backoff_resets_after_a_good_fetch`, `test_poll_forever_survives_errors`) to call `poll_once()` and assert its return value equals the sleep they asserted before. Run: `python -m pytest homeassistant/streamdock_n3/services/soccer -q` → FAIL until the moves compile, then PASS once imports are fixed and `poll_once`/`on_pick` exist. Every other moved test must pass unchanged in its assertions.

- [ ] **Step 2: Write the failing service tests** (`test_soccer_service.py`, using `fake_context` with the fixtures from `tests/fixtures/` served by URL substring `tv.apple.com`, `searchTerm=Paramount%2B`, `searchTerm=MLS`, and `now` = `1792850000.0`)

```python
def test_refresh_job_fetches_and_reports_live(): ...   # job returns poll wait; conditions live True; vars soccer_count == "2" (fixture count)
def test_http_ops_are_named(): ...                     # metrics ops contain http:token_page and http:shelf
def test_start_action_picks_toasts_and_plays(): ...    # toasts[0] == ("1/2 <title>", "soccer"); calls end with play_media
def test_pick_schedules_redraw_after_cycle():          # Review Focus 3
    svc, ctx = started()
    svc.actions()["start"]({})
    assert ctx.fakes.redraws and ctx.fakes.redraws[0] == pytest.approx(60.1)
def test_key_specs_match_previous_preset():            # exact dicts above
def test_context_turns_streaming_into_live_game_while_watching(): ...
def test_context_survives_cleared_states():            # Review Focus 1
    svc, ctx = started(states={})
    assert svc.context("streaming") is None and svc.conditions()["picking"]() is False
def test_unknown_key_is_none(): ...
```

Run: `python -m pytest homeassistant/streamdock_n3/services/soccer/tests/test_soccer_service.py -q` → FAIL.

- [ ] **Step 3: Implement `service.py`, then the daemon/context/preset changes.** In `test_config.py`: the known-conditions set uses `"soccer.live"`, `"soccer.picking"`; entity checks read `CONFIG["services"]["soccer"]`; add `test_service_keys_resolve` (each `service_key` slot in the preset names a key `SoccerService().key(name, slot.get("args", {}))` returns non-None for; it needs no `start()`), and make the icon-existence check use the resolved spec for `service_key` slots.

- [ ] **Step 4: Run the full suite** — `python -m pytest homeassistant/streamdock_n3 -q`, Expected: all pass; `git grep -n "_soccer\|soccer_live\"\|SOCCER_COMMANDS\|watching_game" homeassistant/streamdock_n3 -- '*.py'` returns nothing (except `soccer_live__` icon ids).

- [ ] **Step 5: Commit** — `git commit -m "soccer: move onto the services contract (no behaviour change)"`.

---

### Task 7: Docs, metrics tool, deploy, verify

**Files:**
- Create: `homeassistant/streamdock_n3/services/README.md`
- Create: `homeassistant/tools/metrics.sh`
- Modify: `homeassistant/streamdock_n3/DOCS.md`, `homeassistant/streamdock_n3/config.yaml` (`0.6.0-1`), `docs/JOURNAL.md`, `README.md` (start-here table: add `services/README.md`)

- [ ] **Step 1: Write `services/README.md`** for people and AI agents: the contract (spec §5.4), the `ctx` table (§5.5), the rules (no I/O or I/O-holding locks in render-path methods; swap an immutable snapshot under a short lock; freshness is the service's job; never log URLs; secrets via `ctx.secret` and `/config/secrets.json`; toasts need the service name in `toast.events`), placing keys/pages in the preset (`service_key`, `args`, overrides, `<svc>.<page>` in `round`), a copyable minimal template service with one job, one key, one action and its `fake_context` test, how to run tests, and how to read metrics (log lines, snapshot shape from Task 1, `metrics.sh`).

- [ ] **Step 2: Write `tools/metrics.sh`**: `metrics.sh [--by cpu|wall|http|errors] [--window interval|total] [host]` → `ssh_ha.sh` `cat /app_configs/local_streamdock_n3/metrics.json` piped into `python homeassistant/streamdock_n3/services/metrics.py --by … --window …` on the PC. Check locally: `printf '%s' '<snapshot json from a Metrics test>' | python homeassistant/streamdock_n3/services/metrics.py --by wall` prints a header and rows.

- [ ] **Step 3: Update `DOCS.md`** (`services`, `metrics`, `service_key`, the `{"service": …}` action, soccer's renamed conditions, `/config/secrets.json`, `/config/metrics.json`) and bump `config.yaml` to `0.6.0-1`. Run the full suite once more. Commit: `git commit -m "services: README, metrics.sh, DOCS, 0.6.0-1"`.

- [ ] **Step 4: Deploy** with the user's go: `bash homeassistant/restore.sh arpi`. Expected in the app log after the `run.sh started` marker: no traceback; `tabs: showing context:…`; within the first poll a `soccer:` line (`tokens refreshed` and/or `N live: …`); no `services: soccer disabled`.

- [ ] **Step 5: Verify on the device** (the user watches the dock; per the live-probe rule, one action at a time on the user's go, never a blind BACK while something plays):
  1. Round 1 twice → `apps_more`: key 6 shows the soccer ball (`soccer_live__none`, or `__live` with `N LIVE` if games are on).
  2. If a game is live: press it → toast `1/N <title>`, the game opens, the layout becomes `live_game`, key 4 shows `NEXT\n1/N`. Press key 4 → next game. Without live games: the press toasts `No live soccer games`.
  3. After 10 min: the log shows `metrics: soccer …` and `metrics: app …` lines; `bash homeassistant/tools/metrics.sh` prints a table with `soccer job:refresh`, `soccer http:shelf` and `host render_pass`.
  Record what could not be checked (no live game that day) in the JOURNAL entry.

- [ ] **Step 6: Update `docs/JOURNAL.md`**: system state (app version `0.6.0-1`; the repo layout row gains `services/` and `tools/metrics.sh`; control mappings note `soccer.live`/`soccer.picking`), a log entry for the services host and the soccer move, and an open item for anything Step 5 couldn't verify. Confirm `restore.sh` needs no change (services ship in the image; it doesn't touch `/config/secrets.json`). Commit: `git commit -m "journal: services host deployed (0.6.0-1)"`.
