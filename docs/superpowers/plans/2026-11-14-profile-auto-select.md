# Profile Auto-Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a dock key opens a streaming app on the Apple TV, press OK on its profile picker only when the app was started cold, and undo a click that started a film.

**Architecture:** A core tracker (`atv_clients.py`) listens to the app's own MRP session for app process start/end and is shared with services. A new service `profiles` owns the `open` action: launch, decide from the tracker, wait, click, watch. The daemon also shares the time of the last dock input so the service stays out once the viewer acts.

**Tech Stack:** Python 3, pyatv 0.18 (MRP protobuf listeners), pytest, the services contract in `homeassistant/streamdock_n3/services/`.

**Spec:** `docs/superpowers/specs/2026-11-14-profile-auto-select-design.md`

All paths below are relative to `homeassistant/streamdock_n3/` unless they start with `docs/`.

## Global Constraints

- Nothing is deployed and no command is sent to the TV by this plan.
- Ships off: `"dry_run": true`, every app `"picker": false`.
- Menu is sent at most once per open, only after our own click, only when playback with `media_duration >= film_s` appears that was not there before the click.
- No click when the status is `unknown`, when the app is playing or paused, or after dock input.
- The launch happens even if the tracker or the decision raises.
- Render-path rules of `services/README.md` hold (the service has no render-path methods).
- Defaults: `delay_s` 6, `watch_s` 5, `film_s` 300, `sync_wait_s` 5, tracker `settle_s` 3, `input_grace_s` 1, poll 0.25 s, history 100 entries.
- Regexes and `\b`: edit with the Edit tool, not shell heredocs.
- Tests: `python -m pytest homeassistant/streamdock_n3 -q` from the repo root; 534 pass before this work.
- Version in `config.yaml`: `0.6.2-1`.

## Review Focus

- A key release event arriving just after the press must not count as "dock input" and cancel every click: inputs within `input_grace_s` of the open are ignored (Task 3 test).
- A removal message for an old process id after the app restarted must not mark the new process gone (Task 1 test).
- `media_duration` missing or not a number (Netflix: `None`) must not raise in the watch loop (Task 3 test).
- An app id in `args` that is missing or not a string: log and do nothing, no exception trace (Task 3 test).
- A second `open` while the first waits: the first sends nothing (Task 3 test).

---

### Task 1: The tracker

**Files:**
- Create: `atv_clients.py`
- Test: `tests/test_atv_clients.py`

**Interfaces:**
- Produces:
  - `class AtvClients(settle_s: float = 3.0, monotonic=time.monotonic, watch: Iterable[str] = (), log=print)`
  - `on_session() -> None`, `on_message() -> None`, `on_update(bundle: str, pid: int) -> None`, `on_remove(bundle: str, pid: int) -> None`, `on_lost() -> None`
  - `status(bundle: str) -> str` (`"alive"`, `"gone"`, `"unknown"`), `pid(bundle) -> int | None`, `removed_at(bundle) -> float | None`, `seen_at(bundle) -> float | None`, `session_at() -> float | None` (monotonic times; methods, not attributes: `ctx.shared` proxies method calls)
  - `install(tracker: AtvClients) -> None`: wraps `MrpProtocol.__init__` as `ad_detect.install` does; calls `tracker.on_session()` in the wrapped init and listens to `UPDATE_CLIENT_MESSAGE` (update), `REMOVE_CLIENT_MESSAGE` (remove), `SET_NOW_PLAYING_CLIENT_MESSAGE` (update when it carries a client with a bundle id), plus `SET_STATE_MESSAGE` (message only). Fields: `inner.client.bundleIdentifier`, `inner.client.processIdentifier`.

Rules the tests pin:
- No session yet: `unknown`. After `on_session()` with no message: `unknown`. `settle_s` after the first message (any `on_*` event counts as a message): unlisted apps are `gone`; before that `unknown`. A listed app is `alive` at once.
- `on_session()` empties the alive list and restarts the settle clock; `removed_at` and `seen_at` are kept.
- `on_lost()`: everything `unknown` until the next session; times kept.
- `on_remove(bundle, pid)` with a pid other than the alive one: ignored. With the alive pid (or when the app is not listed): the app is not alive and `removed_at` is now.
- Apps in `watch` log one line per change: `atv-clients: <bundle> alive pid <pid>` / `atv-clients: <bundle> gone`. A repeated update with the same pid logs nothing.
- Thread-safe (one lock; listeners run on the pyatv loop, readers on action threads).

- [ ] **Step 1:** Write `tests/test_atv_clients.py` covering each rule above with an injected clock (a list-backed `monotonic`), and one test that feeds `install`'s handlers real protobuf messages built with `pyatv.protocols.mrp.messages.create(protobuf.UPDATE_CLIENT_MESSAGE)` (set `inner().client.bundleIdentifier`/`processIdentifier`) through a fake protocol object that records `listen_to` handlers.
- [ ] **Step 2:** Run `python -m pytest homeassistant/streamdock_n3/tests/test_atv_clients.py -q`; expect import failure.
- [ ] **Step 3:** Implement `atv_clients.py`.
- [ ] **Step 4:** Run the file's tests, then the whole suite; all pass.
- [ ] **Step 5:** Commit `atv_clients: track which Apple TV apps are alive (MRP client messages)`.

### Task 2: Daemon wiring

**Files:**
- Create: `dock_input.py`
- Modify: `ha_daemon.py` (`_shared`, `_run_actions`, `main`), `atv_touch.py` (`AppleTVTouch.__init__`, `_lost`, `drop`), `Dockerfile` (the `COPY ... /opt/streamdock/` module list: add `atv_clients.py dock_input.py`)
- Test: `tests/test_dock_input.py`, `tests/test_services_daemon.py` (one test), `tests/test_atv_touch_lost.py`

**Interfaces:**
- Consumes: `AtvClients`, `install` (Task 1).
- Produces:
  - `dock_input.DockInput(monotonic=time.monotonic)` with `touch() -> None` and `last() -> float` (0.0 before any input).
  - `ctx.shared("dock_input")` and `ctx.shared("atv_clients")`.
  - `AppleTVTouch(on_lost: Callable[[], None] | None = None)`: called from `_lost` and from `drop` (when a connection was dropped), exceptions swallowed.

Wiring decisions:
- `_shared` gains `"atv_clients": None` and `"dock_input": <DockInput>` (the latter always present).
- `_run_actions` calls `_dock_input.touch()` first thing when `dry_run` is false. This counts every key, round key and knob event.
- In `main`, inside the `atv` section branch and before `AppleTVTouch()` is made: build `AtvClients(watch=<bundle ids of services.profiles.apps, if any>)`, `atv_clients.install(...)`, put it in `_shared`, pass `on_lost=tracker.on_lost`. A failure prints `atv-clients: disabled (<err>)` and leaves the handle `None`.
- `docs`: add both handles to the `ctx.shared(name)` row of `services/README.md`.

- [ ] **Step 1:** Tests: `DockInput` (0.0, then the injected time after `touch`); in `test_services_daemon.py` a press of a plain key moves `ha_daemon._dock_input.last()` and a `dry_run=True` call does not; `AppleTVTouch.__new__`-built object (no thread) calls `on_lost` from `_lost("x")` and from `drop("x")` with a fake `_atv`, and a raising callback does not propagate.
- [ ] **Step 2:** Run them; expect failures.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Whole suite passes.
- [ ] **Step 5:** Commit `daemon: share app liveness and last dock input with services`.

### Task 3: The `profiles` service

**Files:**
- Create: `services/profiles/__init__.py`, `services/profiles/decide.py`, `services/profiles/service.py`
- Test: `services/profiles/tests/test_profiles.py`

**Interfaces:**
- Consumes: `ctx.shared("atv_clients")` (Task 1 methods), `ctx.shared("dock_input").last()`, `ctx.media.ensure_on()`, `ctx.media.launch("atv", app)`, `ctx.call("remote", "send_command", {"entity_id": cfg["atv_remote"], "command": "select" | "menu"})`, `ctx.state(cfg["atv"])`, `ctx.state(cfg["lg"])`, `ctx.sleep`, `ctx.monotonic`, `ctx.store`, `ctx.toast`, `ctx.log`.
- Produces: `SERVICE` with `name = "profiles"` and `@action("open")` taking `{"app": "<bundle id>"}`.

`decide.py` (pure):

```python
def decide(entry: dict | None, status: str, *, opened_at: float | None,
           removed_at: float | None, dry_run: bool) -> tuple[str, bool]:
    """(reason, click). Reasons: "not listed", "warm", "unknown", "maybe alive", "cold"."""
```

- `entry is None`, or `picker` false while not `dry_run` -> `("not listed", False)`.
- `alive` -> `("warm", False)`; `unknown` -> `("unknown", False)`.
- `gone` and `entry.get("registers") == "launch"` -> `("cold", True)`.
- `gone` otherwise -> cold only when `removed_at is not None and (opened_at is None or removed_at > opened_at)`; else `("maybe alive", False)`.

`service.py`, `open(args)`:

1. `app` not a non-empty string: log `open: no app` and return.
2. `gen = ++self._gen` (under a lock); `t0 = ctx.monotonic()`.
3. `was_off`: the LG state's variant is not `on` (as `media_actions._lg_on`). `ctx.media.ensure_on()`; false -> log, return.
4. Status, inside `try` (any exception -> log, status `unknown`): no tracker -> `unknown`. If `was_off`: poll every 0.25 s up to `sync_wait_s` until `tracker.session_at()` is later than `t0` and `tracker.status(app) != "unknown"`; never -> `unknown`. Else `tracker.status(app)`. Also read `removed_at`.
5. `ctx.media.launch("atv", app)`; false -> return. Then `opened_before = self._opened.get(app)`; `self._opened[app] = ctx.monotonic()` (memory only: tracker times are monotonic and do not survive a restart either).
6. `reason, click = decide(entry, status, opened_at=opened_before, removed_at=..., dry_run=...)`. Log one line (formats in the spec's "Log and history"); record history.
7. No click -> done. Dry run -> log `(dry run) ... would click in <delay>s`, done.
8. Wait `delay_s` (entry's, else the service's) in 0.25 s steps; then cancel, each with its own log reason, if: `self._gen != gen` (`newer open`); `dock_input.last() > t0 + input_grace_s` (`dock input`); `tracker.status(app) == "unknown"` (`connection lost`); `tracker.removed_at(app)` is later than `t0` (`app ended`); the LG's `source` is not `cfg["atv_source"]` (`input changed`); the Apple TV entity has `app_id == app` and state `playing` or `paused` (`already playing`).
9. Record `was_film = _film(state)`; send `select`; log `click sent`.
10. If `entry.get("undo", True)` and not `was_film`: poll every 0.25 s for `watch_s`; stop on a newer open or dock input; when `_film(state)` -> send `menu` once, `ctx.toast("Opened by mistake: went back")`, log `playback <n>s after click -> menu sent`, stop.

`_film(st)`: state `playing`, `app_id == app`, and `media_duration` is an `int`/`float` (not `bool`) `>= film_s`.

History: `ctx.store.save("history", last 100)` of `{"at": ctx.clock(), "app", "status", "reason", "outcome"}`; a store error is logged, never raised.

- [ ] **Step 1:** Write `test_profiles.py` with `fake_context("profiles", CFG, states=..., shared={"atv_clients": FakeTracker(), "dock_input": FakeInput()})`. `states` is a dict the test mutates; a `FakeTracker` with settable `status`, `removed`, `session` and an optional hook run from `ctx.sleep` (wrap `ctx.fakes.time.sleep`) to change state mid-wait. Tests, one per line:
  - `decide`: every branch above (table-driven).
  - warm: launch call only (`play_media`), no `remote.send_command`.
  - cold, `registers: "launch"`: `select` sent after 6 s of fake time, not before.
  - cold by evidence (`removed_at` later than the previous open) and "maybe alive" without it.
  - wake: LG `off` at press, tracker session older than the press -> no click; session newer and synced -> click.
  - each cancel reason in step 8 sends no `select`; a dock input within 1 s of the press does not cancel.
  - a second `open` during the first's wait: exactly one `select` (run the second from the sleep hook).
  - watch: duration 7140 appearing 2 s after the click -> one `menu` and one toast; duration 27 -> none; `media_duration` `None` or `"x"` -> none, no exception; film already playing before the click -> none; `undo: false` -> none; dock input during the watch -> none; never two `menu`.
  - dry run: decision logged for an app with `picker: false`, nothing sent.
  - app not in `apps`: launch only. `args` `{}` or `{"app": 5}`: no call at all.
  - tracker `status` raising: launch still sent, no click.
  - no tracker (`shared={}`): launch only.
  - history holds the open with reason and outcome, capped at 100.
- [ ] **Step 2:** Run; expect import failure.
- [ ] **Step 3:** Implement the three files.
- [ ] **Step 4:** Whole suite passes.
- [ ] **Step 5:** Commit `profiles: press OK on a profile picker after a cold launch (service)`.

### Task 4: Preset, version, docs

**Files:**
- Modify: `presets/tv_room.json` (services section from the spec's Config block, with `"delay_s": 6, "watch_s": 5, "film_s": 300, "sync_wait_s": 5`; the ten Apple TV app keys on pages `apps` and `apps_more` become `{"service": "profiles", "do": "open", "args": {"app": X}}`, YouTube included, the LG key unchanged; `"profiles"` added to `toast.events`), `config.yaml` (`0.6.2-1`), `DOCS.md` (new `### Profile picker` after `### Scene jump`: what it does, the config table, dry run, log lines, limits), `services/README.md` (intro list of worked examples: one line), `docs/JOURNAL.md` (log entry for 2026-11-14 with the measured facts and the design in outcomes; open items: the TV survey list from the spec), `docs/superpowers/specs/2026-11-14-profile-auto-select-design.md` (status approved; `opened_at` is memory only).
- Check: `homeassistant/restore.sh` uploads the preset whole and the `services/` tree, so it needs no change; say so in the journal entry if true.
- Test: `services/profiles/tests/test_profiles_preset.py`

- [ ] **Step 1:** Preset test: `services.profiles` has `dry_run` true and every app `picker` false; every key whose action targets the Apple TV on pages `apps`/`apps_more` uses `profiles.open` with a string `app`; no `{"launch": {"device": "atv"...}}` is left on those pages; Pluto, Tubi and the TV app are not in `apps`; `profiles` is in `toast.events`; the service's `atv`, `atv_remote`, `lg`, `atv_source` equal those of `tabs.launch`.
- [ ] **Step 2:** Run; expect failure.
- [ ] **Step 3:** Edit the preset, version and docs.
- [ ] **Step 4:** Whole suite passes (`test_config.py` checks entities and may need the new section's entities to be known ones).
- [ ] **Step 5:** Commit `profiles: preset keys open apps through the service; docs; 0.6.2-1`.
