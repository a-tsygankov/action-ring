# ActionRing N3 Tabs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the dock's six LCD keys into tabs: a context tab that follows the app on the TV, round-button tabs for Apps (+1B) and Sonos, live state on keys, a committed icon set, a probe tool for the TV test session, and a live-soccer key.

**Architecture:** Three new pure modules hold the logic and are unit-tested on the PC: `keyspec.py` (key look/action from live state), `tabs.py` (tab state machine, timers, context debounce) and `context.py` (which context layout applies). `ha_daemon.py` wires them to the device. A single renderer thread draws only keys whose look changed. A ticker drives the timers. New action kinds live in `media_actions.py`, `yt_lounge.py` and `soccer.py`. Icons are rendered on the PC by the existing generator and committed.

**Tech Stack:** Python 3.12 (app image, `python:3.12-slim-bookworm`), PC Python 3.13 for tests; pytest; pyatv 0.18.0; pyytlounge 3.5.0; Pillow + resvg-py 0.5.0 (PC only, icon generator); HA Supervisor REST/WebSocket.

**Spec:** [docs/superpowers/specs/2026-10-24-actionring-tabs-design.md](../specs/2026-10-24-actionring-tabs-design.md) (approved 2026-10-24). Research notes: [docs/superpowers/specs/2026-10-24-actionring-tabs-research/](../specs/2026-10-24-actionring-tabs-research/).

**Order:** Tasks 1–8 in sequence. Tasks 9 and 10 are independent of each other once Task 8 is done (9 needs a match day, 10 needs a TV session with the user); bump the version per deploy in whichever order they land.

**Scope of this plan:** spec phases 1 (tab engine, static tabs, icons) and 3 (live soccer), plus the probe tool and the checkpoint for the spec's §10 live test session. Phase 2 (FF/REW speeds, Apple TV subtitles/audio, LG tracks) depends on the session's results. Its plan is written after the session (Task 10 ends by doing that).

## Global Constraints

- Public repo: never commit secrets, tokens, serials, addresses or credentials. Apple web-client parameters `utsk`/`utscf` are scraped at run time, never stored in the repo.
- Entities (exact): Apple TV `media_player.vugitv_4k_vugitv_4k`, `remote.vugitv_4k_vugitv_4k`; LG `media_player.lg_tv_2`; Arc `media_player.tv_room_tv_room`, `switch.tv_room_tv_room_night_sound`, `switch.tv_room_tv_room_speech_enhancement`, `switch.tv_room_tv_room_subwoofer_enabled`, `switch.tv_room_tv_room_surround_enabled`; other Sonos `media_player.living_room_living_room`, `media_player.kitchen_kitchen`, `media_player.bathroom_bathroom`, `media_player.boston_boston`, `media_player.sonos_move`.
- LG input for the Apple TV box: `Apple OTT`.
- Timeouts: tab open 10 s, after an LCD key press 5 s, context debounce 1 s. All in config.
- Round buttons: `button.7` = Apps → 1B → Apps; `button.8` = Sonos (repeat restarts the timer); `button.9` = Power on, unchanged. Knob actions unchanged.
- Key order 1 2 3 / 4 5 6 = `button.1–6`.
- Icons: 112×112 PNG in `homeassistant/streamdock_n3/icons/`, referenced by bare id (`"icon": "night__on"` → `/config/icons/night__on.png`). Existing `"/config/icons/x.png"` paths keep working.
- Bundle ids: Netflix `com.netflix.Netflix`, YouTube `com.google.ios.youtube`, Prime `com.amazon.aiv.AIVApp`, Apple TV app `com.apple.TVWatchList`, Max `com.wbd.stream`, Hulu `com.hulu.plus`, Disney+ `com.disney.disneyplus`, Paramount+ `com.cbsvideo.app`, Pluto `tv.pluto.ios`, Tubi `com.adrise.tubitv`. LG YouTube `youtube.leanback.v4`. LG media player `com.webos.app.mediadiscovery` (confirm in Task 10).
- Draw nothing from the SDK reader thread (input callback). It must stay free for button events.
- Every task that changes app code bumps `version` in `homeassistant/streamdock_n3/config.yaml` once per deploy (currently `0.4.0-21`; first deploy of this plan → `0.5.0-1`).
- After deploying: update `docs/JOURNAL.md` (final state only) and `homeassistant/streamdock_n3/DOCS.md`; `restore.sh` must reproduce the live system.
- Tests run on the PC: `python -m pytest homeassistant/streamdock_n3/tests -q` from the repo root.

## Review Focus

1. **HA drops while a tab is open.** `_states` is cleared on reconnect. Keys should show their `unavailable` look, the context should fall back to `idle`, and the engine must not raise. Test in Task 3 (`test_context_idle_when_states_empty`) and Task 2 (`test_look_unavailable_when_entity_missing`).
2. **Rapid round-button presses** (a double-click arrives in ~150 ms, possibly while a redraw is in flight). Every press must register, so two presses land on 1B and three on Apps. Test in Task 4 (`test_double_click_lands_on_1b`, `test_three_presses_cycle_back`); renderer coalescing in Task 5.
3. **Foreground app flaps** (empty `app_id` for a moment between apps, `idle` blips). The layout must not flicker. Test in Task 4 (`test_context_flap_shorter_than_debounce_is_ignored`).
4. **Key press while a new context is pending.** The press must act on what is drawn, not on the pending layout. Test in Task 4 (`test_press_uses_visible_context_not_pending`).
5. **App launch with the TV off when power-on never completes** (Apple TV asleep and unreachable). It must give up after the configured wait, log the failure, never block input, and not launch onto a dark TV. Test in Task 6 (`test_launch_gives_up_when_tv_never_wakes`).

---

## File Structure

| File | Responsibility |
|---|---|
| `homeassistant/streamdock_n3/keyspec.py` (new) | Pure: key look (`Look`) and press action from a key spec + live state + named conditions + label vars; icon id → path; entities a spec reads. |
| `homeassistant/streamdock_n3/tabs.py` (new) | Pure: `TabEngine`, the tab state machine (round presses, page cycling, timers, close-on-press, context debounce). |
| `homeassistant/streamdock_n3/context.py` (new) | Pure: `foreground_layout()`, which context layout the TV state calls for. |
| `homeassistant/streamdock_n3/media_actions.py` (new) | `launch()` (power-first, input switch, app launch on Apple TV or LG), `next_playing()` (next track on the first playing Sonos). |
| `homeassistant/streamdock_n3/soccer.py` (new) | Live soccer: token scrape, UTS fetch, parse/filter/order, cache, cursor, `start()`/`next()`. |
| `homeassistant/streamdock_n3/probe.py` (new) | File-driven probe runner for the TV test session (MRP/Companion/HA/Lounge/webOS commands, logs results). |
| `homeassistant/streamdock_n3/ha_daemon.py` (modify) | Wire engine + renderer + ticker; new action kinds; remove the old per-key state/mode redraw path (moved into keyspec + renderer). |
| `homeassistant/streamdock_n3/yt_lounge.py` (modify) | `command()` for next/previous/play_pause/subtitles; subtitle state. |
| `homeassistant/streamdock_n3/yt_chapters.py` (modify) | `caption_languages(page)`. |
| `homeassistant/streamdock_n3/atv_touch.py` (modify) | `run(fn, timeout)`: run a coroutine against the live pyatv connection (for probe). |
| `homeassistant/streamdock_n3/config.tv_room.json` (modify) | `tabs` section; `button.1–8.press` routed to the engine. |
| `homeassistant/streamdock_n3/Dockerfile`, `config.yaml` (modify) | Copy new modules; version bump. |
| `homeassistant/streamdock_n3/icons/` (modify) | All generated PNGs, `NOTICE.md`, `manifest.json`. |
| `homeassistant/tools/icons/build_icons.py` (modify) | `--out` flag; writes manifest/NOTICE beside the PNGs. |
| `homeassistant/tools/probe.sh` (new) | PC side of the probe: send a probe file, print the app's `probe:` log lines. |
| `homeassistant/streamdock_n3/tests/` (new) | `conftest.py`, `requirements.txt`, one test file per module, `fixtures/`. |

---

### Task 1: Commit the icon set

**Files:**
- Modify: `homeassistant/tools/icons/build_icons.py`
- Create: `homeassistant/streamdock_n3/icons/*.png`, `homeassistant/streamdock_n3/icons/NOTICE.md`, `homeassistant/streamdock_n3/icons/manifest.json`
- Delete: `homeassistant/tools/icons/NOTICE.md`, `homeassistant/tools/icons/manifest.json` (moved)

**Interfaces:**
- Produces: icon ids used by later tasks, the file stems in `homeassistant/streamdock_n3/icons/` (e.g. `netflix`, `play_pause__playing`, `night__on`, `soccer_live__none`, `apple_tv_input`, `home`, `back`, `next_chapter`).

- [ ] **Step 1: Add `--out DIR` to `build_icons.py`** (default `out/`). PNGs, `manifest.json` and `NOTICE.md` go to DIR. The contact sheets and the SVG cache stay in `out/`. `home.png`/`back.png` are copied unchanged, as today.
- [ ] **Step 2: Render into the app's icon folder**

Run: `python homeassistant/tools/icons/build_icons.py --offline --out homeassistant/streamdock_n3/icons` (drop `--offline` if `out/cache/` is empty).
Expected: 51+ PNGs plus `NOTICE.md` and `manifest.json` in `homeassistant/streamdock_n3/icons/`. `git diff --stat homeassistant/streamdock_n3/icons/home.png homeassistant/streamdock_n3/icons/back.png` shows no change.

- [ ] **Step 3: Check NOTICE** credits selfhst (CC BY 4.0) for Prime Video, Max, Hulu, Disney+, and theSVG (MIT, github.com/glincker/thesvg) for Pluto TV. It also carries the no-affiliation line. Remove the two moved files from `homeassistant/tools/icons/`.
- [ ] **Step 4: Look at `homeassistant/tools/icons/out/contact_device.png`.** Every key must be legible. No regressions against the reviewed sheet.
- [ ] **Step 5: Commit**

```bash
git add homeassistant/tools/icons homeassistant/streamdock_n3/icons
git commit -m "Icons: commit the generated key set with NOTICE and manifest"
```

---

### Task 2: `keyspec.py`: key looks and actions

**Files:**
- Create: `homeassistant/streamdock_n3/keyspec.py`
- Create: `homeassistant/streamdock_n3/tests/conftest.py` (puts `homeassistant/streamdock_n3` on `sys.path`), `homeassistant/streamdock_n3/tests/requirements.txt` (`pytest`, `pyytlounge==3.5.0`)
- Test: `homeassistant/streamdock_n3/tests/test_keyspec.py`

**Interfaces:**
- Consumes: the key spec format of today's `config.tv_room.json` (`label`, `color`, `icon`, `state_entity`, `on`/`off`/`unavailable`, `modes` with `when`, `action`).
- Produces:
  - `StateLookup = Callable[[str], dict | None]` (returns `{"s": state, "a": attrs}`, as `ha_daemon.state_of`)
  - `@dataclass(frozen=True) class Look: icon: str | None; label: str; color: str | None`
  - `ICON_DIR = "/config/icons"`; `icon_path(ref: str | None) -> str | None`: a bare id → `f"{ICON_DIR}/{ref}.png"`; a ref containing `/` is returned unchanged.
  - `variant(state: str | None) -> str`: `"on" | "off" | "unavailable"`, today's `_variant` rule (off/standby → off; None/unavailable/unknown → unavailable; else on).
  - `active_mode(spec: dict, state_of, conds: Mapping[str, Callable[[], bool]]) -> dict | None`: first mode whose `when` holds. `when` is a string naming a condition in `conds`, or a list of `{"entity","attr","in"}` (any match, `attr` defaults to `"state"`).
  - `resolve_look(spec, state_of, conds, vars: Mapping[str, str] = {}) -> Look`: an active mode wins. Otherwise, with `state_entity`, the variant dict overlays the base. Otherwise the base applies. `icon` goes through `icon_path`, and `label` through `str.format_map(vars)` (missing var → left as `{name}`).
  - `resolve_action(spec, state_of, conds) -> Any`: the active mode's `action` if it has one, else `spec["action"]`.
  - `entities_of(spec: dict) -> set[str]`: `state_entity` + every `when` list entity.

- [ ] **Step 1: Write the failing tests**

```python
def test_variant_rules():
    assert [variant(s) for s in ("on", "playing", "idle", "off", "standby", None, "unavailable")] == \
        ["on", "on", "on", "off", "off", "unavailable", "unavailable"]

def test_icon_path_bare_id_and_path():
    assert icon_path("night__on") == "/config/icons/night__on.png"
    assert icon_path("/config/icons/home.png") == "/config/icons/home.png"
    assert icon_path(None) is None

def test_state_variant_overrides_icon():
    spec = {"icon": "night__off", "state_entity": "switch.n",
            "on": {"icon": "night__on"}, "unavailable": {"icon": "night__unknown"}}
    st = {"switch.n": {"s": "on", "a": {}}}
    assert resolve_look(spec, st.get, {}).icon == "/config/icons/night__on.png"

def test_look_unavailable_when_entity_missing():
    spec = {"icon": "night__off", "state_entity": "switch.n", "unavailable": {"icon": "night__unknown"}}
    assert resolve_look(spec, {}.get, {}).icon == "/config/icons/night__unknown.png"

def test_mode_list_condition_and_named_condition():
    spec = {"icon": "play_pause__paused",
            "modes": [{"when": "yt_subs", "icon": "subtitles__on"},
                      {"when": [{"entity": "media_player.a", "in": ["playing"]}], "icon": "play_pause__playing"}]}
    st = {"media_player.a": {"s": "playing", "a": {}}}
    assert resolve_look(spec, st.get, {"yt_subs": lambda: False}).icon.endswith("play_pause__playing.png")
    assert resolve_look(spec, st.get, {"yt_subs": lambda: True}).icon.endswith("subtitles__on.png")

def test_mode_without_action_keeps_base_action():
    spec = {"action": "a", "modes": [{"when": [{"entity": "x", "in": ["on"]}], "icon": "i"}]}
    assert resolve_action(spec, {"x": {"s": "on", "a": {}}}.get, {}) == "a"

def test_label_vars():
    spec = {"label": "NEXT\\n{soccer_pos}", "color": "#1d6b32"}
    assert resolve_look(spec, {}.get, {}, {"soccer_pos": "2/3"}).label == "NEXT\\n2/3"

def test_entities_of():
    spec = {"state_entity": "s.a", "modes": [{"when": [{"entity": "s.b", "in": [1]}]}, {"when": "youtube"}]}
    assert entities_of(spec) == {"s.a", "s.b"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pip install -r homeassistant/streamdock_n3/tests/requirements.txt && python -m pytest homeassistant/streamdock_n3/tests/test_keyspec.py -q`
Expected: FAIL, `ModuleNotFoundError: keyspec`.

- [ ] **Step 3: Implement `keyspec.py`** with the interfaces above. Reuse `yt_lounge.matches` semantics for list conditions, but copy the 3-line function rather than importing `yt_lounge` (that pulls in pyytlounge).
- [ ] **Step 4: Run to verify pass.** Same command. Expected: 8 passed.
- [ ] **Step 5: Commit**

```bash
git add homeassistant/streamdock_n3/keyspec.py homeassistant/streamdock_n3/tests
git commit -m "keyspec: pure key look/action resolution with icon ids"
```

---

### Task 3: `context.py`: which context layout

**Files:**
- Create: `homeassistant/streamdock_n3/context.py`
- Test: `homeassistant/streamdock_n3/tests/test_context.py`

**Interfaces:**
- Consumes: `keyspec.StateLookup`.
- Produces: `foreground_layout(state_of, cfg: dict, watching_game: bool = False) -> str`, returning one of `"idle" | "streaming" | "youtube" | "lg_media" | "lg_app" | "live_game"`. `cfg` is the `tabs.context` config section:

```json
{"lg": "media_player.lg_tv_2", "atv": "media_player.vugitv_4k_vugitv_4k",
 "atv_source": "Apple OTT",
 "youtube_apps": {"atv": ["com.google.ios.youtube"], "lg": ["youtube.leanback.v4"]},
 "lg_media_apps": ["com.webos.app.mediadiscovery"],
 "lg_idle_app_prefixes": ["com.webos.app.hdmi", "com.webos.app.livetv", "com.webos.app.home"]}
```

The rules come from spec §5.1:
- LG state off/standby/None/unavailable/unknown → `idle`.
- If LG `source == atv_source`: an empty ATV `app_id` → `idle`; a YouTube app → `youtube`; `watching_game` → `live_game`; anything else → `streaming`.
- Otherwise, on the LG: `source == "YouTube"` or a YouTube app id → `youtube`; a media app → `lg_media`; empty `app_id` or an idle prefix → `idle`; anything else → `lg_app`.

- [ ] **Step 1: Write the failing tests**: one per rule. Use state dicts built by a helper `st(lg_state, lg_source=None, lg_app=None, atv_app=None)`. Include:

```python
def test_context_idle_when_states_empty():
    assert foreground_layout({}.get, CFG) == "idle"

def test_unknown_atv_app_gets_streaming():
    assert foreground_layout(st("on", "Apple OTT", "com.webos.app.hdmi4", "com.plexapp.plex").get, CFG) == "streaming"

def test_live_game_only_on_atv_input():
    assert foreground_layout(st("on", "Apple OTT", None, "com.cbsvideo.app").get, CFG, watching_game=True) == "live_game"
    assert foreground_layout(st("on", "HDMI 1", "com.webos.app.hdmi1").get, CFG, watching_game=True) == "idle"

def test_lg_youtube_by_source():
    assert foreground_layout(st("on", "YouTube", None).get, CFG) == "youtube"
```

- [ ] **Step 2: Run to verify failure.** `python -m pytest homeassistant/streamdock_n3/tests/test_context.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement `foreground_layout`.**
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `git commit -m "context: foreground app to context layout"`.

---

### Task 4: `tabs.py`: tab state machine

**Files:**
- Create: `homeassistant/streamdock_n3/tabs.py`
- Test: `homeassistant/streamdock_n3/tests/test_tabs.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure, time passed in).
- Produces:

```python
class TabEngine:
    def __init__(self, pages: dict[str, dict], rounds: dict[int, list[str]],
                 open_s: float = 10.0, after_press_s: float = 5.0, debounce_s: float = 1.0) -> None
    # pages: name -> {"keys": {"1": spec, ...}, "close_on_press": bool}; context
    # layouts are pages named "context:<layout>".
    # rounds: button number -> page cycle, e.g. {7: ["apps", "apps_more"], 8: ["sonos"]}
    def round_press(self, button: int, now: float) -> bool        # False: not a tab button
    def key_press(self, key: int, now: float) -> dict | None       # spec of key on the VISIBLE page
    def set_context(self, layout: str, now: float) -> None
    def tick(self, now: float) -> None
    @property
    def visible(self) -> str                                     # page name currently shown
    def keys(self) -> dict[str, dict]                              # visible page's key specs
    version: int                                                   # bumps whenever `visible` changes
```

Behaviour (spec §4): a round press on a different round (or from context) opens `cycle[0]` with deadline `now + open_s`. The same round pressed while one of its pages is visible advances to the next page in the cycle (wrapping) with a fresh `open_s`. On a 1-page cycle that only restarts the timer. `key_press` on a temporary page returns the spec, then: `close_on_press` → context at once; otherwise deadline `now + after_press_s`. `tick` past the deadline → context. Context: the first `set_context` is adopted at once. A different layout becomes pending and is adopted by `tick` once stable for `debounce_s`. Setting the shown layout again clears pending. A pending context never changes `visible` while a temporary page is open, but it is adopted underneath. All methods are thread-safe (one internal lock).

- [ ] **Step 1: Write the failing tests**

```python
PAGES = {"apps": {"keys": {"1": {"action": "netflix"}}, "close_on_press": True},
         "apps_more": {"keys": {"1": {"action": "hulu"}}, "close_on_press": True},
         "sonos": {"keys": {"1": {"action": "night"}}},
         "context:idle": {"keys": {"1": {"action": "power"}}},
         "context:streaming": {"keys": {"1": {"action": "pp"}}}}
ROUNDS = {7: ["apps", "apps_more"], 8: ["sonos"]}

def eng():
    e = TabEngine(PAGES, ROUNDS); e.set_context("idle", 0.0); return e

def test_double_click_lands_on_1b():
    e = eng(); e.round_press(7, 1.0); e.round_press(7, 1.15)
    assert e.visible == "apps_more"

def test_three_presses_cycle_back():
    e = eng(); [e.round_press(7, t) for t in (1.0, 1.1, 1.2)]
    assert e.visible == "apps"

def test_open_timeout_10s_then_context():
    e = eng(); e.round_press(8, 1.0); e.tick(10.9); assert e.visible == "sonos"
    e.tick(11.01); assert e.visible == "context:idle"

def test_key_press_extends_by_5s():
    e = eng(); e.round_press(8, 1.0); e.key_press(1, 9.0); e.tick(13.9)
    assert e.visible == "sonos"; e.tick(14.01); assert e.visible == "context:idle"

def test_close_on_press_returns_immediately():
    e = eng(); e.round_press(7, 1.0)
    assert e.key_press(1, 2.0) == {"action": "netflix"}; assert e.visible == "context:idle"

def test_round_8_repeat_restarts_timer():
    e = eng(); e.round_press(8, 1.0); e.round_press(8, 9.0); e.tick(18.9)
    assert e.visible == "sonos"

def test_round_9_not_handled():
    assert eng().round_press(9, 1.0) is False

def test_context_flap_shorter_than_debounce_is_ignored():
    e = eng(); e.set_context("streaming", 1.0); e.set_context("idle", 1.5); e.tick(3.0)
    assert e.visible == "context:idle"

def test_context_adopted_after_debounce():
    e = eng(); e.set_context("streaming", 1.0); e.tick(1.9); assert e.visible == "context:idle"
    e.tick(2.01); assert e.visible == "context:streaming"

def test_press_uses_visible_context_not_pending():
    e = eng(); e.set_context("streaming", 1.0)
    assert e.key_press(1, 1.5) == {"action": "power"}

def test_context_changes_under_open_tab():
    e = eng(); e.round_press(8, 1.0); e.set_context("streaming", 1.0); e.tick(2.5)
    assert e.visible == "sonos"; e.tick(11.5); assert e.visible == "context:streaming"

def test_version_bumps_on_visible_change_only():
    e = eng(); v = e.version; e.tick(5.0); assert e.version == v
    e.round_press(8, 6.0); assert e.version == v + 1
```

- [ ] **Step 2: Run to verify failure.** `python -m pytest homeassistant/streamdock_n3/tests/test_tabs.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement `TabEngine`.**
- [ ] **Step 4: Run to verify pass.** Expected: 12 passed.
- [ ] **Step 5: Commit** `git commit -m "tabs: tab state machine with timers and context debounce"`.

---

### Task 5: Daemon wiring: engine, renderer, ticker

**Files:**
- Modify: `homeassistant/streamdock_n3/ha_daemon.py`, `homeassistant/streamdock_n3/keyspec.py` (`changed_keys`)
- Modify: `homeassistant/streamdock_n3/Dockerfile` (COPY `keyspec.py tabs.py context.py`)
- Test: `homeassistant/streamdock_n3/tests/test_render_plan.py`

**Interfaces:**
- Consumes: `keyspec.resolve_look/resolve_action/entities_of/Look`, `context.foreground_layout`, `tabs.TabEngine`.
- Produces:
  - In `keyspec.py` (pure, testable): `changed_keys(drawn: dict[int, Look], looks: dict[int, Look]) -> list[int]`, the keys 1–6 whose look differs or that were never drawn, in key order.
  - In `ha_daemon.py`:
    - `_conds: dict[str, Callable[[], bool]]` (named conditions: `"youtube"`, plus those Tasks 7–9 add)
    - `_vars() -> dict[str, str]` (label vars; empty until Task 9)
    - `_signal()` (wakes the renderer)
    - action kinds `{"tab_key": N}` and `{"tab_round": N}`
    - `_watching_game: Callable[[], bool]` (stub `lambda: False` until Task 9).

Wiring:
- **Config.** `config["tabs"]` holds `timeouts`, `round`, `pages`, `context` (shape in Task 8). Without a `tabs` section the daemon behaves as before. Keep the old path only if it costs nothing; otherwise require `tabs` and say so in DOCS.
- **Subscriptions.** Watched entities = `entities_of` over every page's keys + the `context.lg`/`context.atv` entities + today's extras.
- **On every state event,** `_watch` updates `_states`, then calls `engine.set_context(foreground_layout(state_of, ctx_cfg, _watching_game()), now)` and `_signal()`. The old `_bindings`/`show`/`_refresh_modes`/`_draw_look` paths go away. The renderer replaces them.
- **Ticker thread** (0.2 s): `engine.tick(now)`; `_signal()` if `engine.version` changed.
- **Renderer thread** waits on the signal, then:
  - computes `looks = {k: resolve_look(spec, state_of, _conds, _vars())}` for keys 1–6 of `engine.keys()` (missing key → `Look(None, "", "#000000")`);
  - draws `changed_keys(_drawn, looks)` under `_draw_lock`, with one `refresh()` per batch;
  - draws icon looks with `set_key_image(path)` when the file exists, else falls back to the text tile;
  - draws text looks with `icons.make_icon` into `generated_key_dir()/key-<n>-<sha1(label,color)[:10]>.jpg`, reusing the file if present;
  - coalesces signals that arrive during a draw into one more pass;
  - times each pass (`render: 6 keys in 0.43s`) and logs passes over 0.5 s.
- **Input routing.** `{"tab_round": N}`: `engine.round_press(N, now)`, then `_signal()`. `{"tab_key": N}`: `spec = engine.key_press(N, now)`, then `_run_actions(resolve_action(spec, …))` if spec, then `_signal()`. Both return at once; no drawing on this thread.
- **Startup.** The renderer starts after `_ready` (the upstream initial paint), with `_drawn` empty, so it paints the context page fully once.
- **`state_of`.** Unchanged.

- [ ] **Step 1: Write the failing test** for `changed_keys`:

```python
def test_changed_keys_first_paint_and_diff():
    a, b = Look("/i/a.png", "", None), Look(None, "X", "#111111")
    assert changed_keys({}, {1: a, 2: b}) == [1, 2]
    assert changed_keys({1: a, 2: b}, {1: a, 2: Look(None, "Y", "#111111")}) == [2]
```

- [ ] **Step 2: Run to verify failure** (`ImportError: changed_keys`).
- [ ] **Step 3: Implement `changed_keys`, then the wiring above.**
- [ ] **Step 4: Run all tests.** `python -m pytest homeassistant/streamdock_n3/tests -q` → all pass. Then `python -m py_compile homeassistant/streamdock_n3/ha_daemon.py`.
- [ ] **Step 5: Commit** `git commit -m "ha_daemon: tab engine, diffing renderer and ticker"`.

---

### Task 6: `media_actions.py`: launch and next-playing

**Files:**
- Create: `homeassistant/streamdock_n3/media_actions.py`
- Modify: `homeassistant/streamdock_n3/ha_daemon.py` (action kinds), `Dockerfile` (COPY)
- Test: `homeassistant/streamdock_n3/tests/test_media_actions.py`

**Interfaces:**
- Consumes: `Call = Callable[[str, str, dict], None]` (as `CoreAPI.call(domain, service, data)`), `keyspec.StateLookup`.
- Produces:
  - `launch(call, state_of, cfg: dict, device: str, app: str, *, sleep=time.sleep, clock=time.monotonic) -> bool`. `cfg` is the `tabs.launch` section `{"lg", "atv", "atv_remote", "atv_source": "Apple OTT", "power_on_wait": 25}`. Steps:
    1. If the LG is off/standby/unknown: `remote.turn_on {entity_id: atv_remote}`, `media_player.turn_on {entity_id: atv}`, then poll every 0.5 s until the LG state is on, up to `power_on_wait`. On timeout, log `launch: TV did not turn on` and return False.
    2. For `device == "atv"`: if the LG `source != atv_source`, call `media_player.select_source {entity_id: lg, source: atv_source}`. Then `media_player.play_media {entity_id: atv, media_content_type: "app", media_content_id: app}`.
    3. For `device == "lg"`: `webostv.command {entity_id: lg, command: "system.launcher/launch", payload: {"id": app}}`.
    4. Return True.
  - `next_playing(call, state_of, candidates: list[str]) -> str | None`: skip a candidate whose `group_members` exists and doesn't start with itself (not the group leader). The first candidate in state `playing` gets `media_player.media_next_track`. Returns that entity or None.
  - Action kinds in `ha_daemon._run_actions`: `{"launch": {"device": "atv"|"lg", "app": id}}` (in a thread) and `{"next_playing": [entities]}`.

- [ ] **Step 1: Write the failing tests** with a recording fake `call` and a mutable state dict:

```python
def test_launch_atv_switches_input_then_launches():
    calls, st = [], {"media_player.lg_tv_2": {"s": "on", "a": {"source": "HDMI 1"}}}
    assert launch(lambda d, s, data: calls.append((d, s, data)), st.get, CFG, "atv", "com.netflix.Netflix")
    assert [c[:2] for c in calls] == [("media_player", "select_source"), ("media_player", "play_media")]
    assert calls[1][2]["media_content_id"] == "com.netflix.Netflix"

def test_launch_powers_on_first_and_waits():
    # fake sleep flips the LG to "on" after two polls; assert turn_on calls come first

def test_launch_gives_up_when_tv_never_wakes():
    # fake clock advances 0.5 per sleep; LG stays "off"; returns False, no play_media call

def test_launch_lg_uses_launcher():
    # LG on, device "lg" -> exactly one webostv.command with payload {"id": app}

def test_next_playing_skips_group_members_and_idle():
    st = {"a": {"s": "paused", "a": {}}, "b": {"s": "playing", "a": {"group_members": ["c", "b"]}},
          "c": {"s": "playing", "a": {"group_members": ["c", "b"]}}}
    assert next_playing(rec, st.get, ["a", "b", "c"]) == "c"
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement both functions and the two action kinds.**
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `git commit -m "media_actions: power-first app launch and next track on playing Sonos"`.

---

### Task 7: YouTube commands and subtitle state

**Files:**
- Modify: `homeassistant/streamdock_n3/yt_lounge.py`, `homeassistant/streamdock_n3/yt_chapters.py`, `homeassistant/streamdock_n3/ha_daemon.py`
- Test: `homeassistant/streamdock_n3/tests/test_youtube.py`

**Interfaces:**
- Consumes: pyytlounge 3.5.0 `YtLoungeApi.next() / previous() / play() / pause() / set_closed_captions(lang|None, video_id)` and `EventListener.subtitles_track_changed(SubtitlesTrackEvent)` (`.video_id`, `.language_code`).
- Produces:
  - `yt_chapters.caption_languages(page: str) -> list[str]`: `languageCode` values from the watch page's `"captionTracks"` array, in page order, without duplicates.
  - `yt_chapters.pick_caption(langs: list[str], preferred: str = "en") -> str | None`: `preferred` if present (also matching `en-US`-style codes by prefix), else the first, else None.
  - `Screen.subtitles: str | None`: the current language, or None when off. It is set by `subtitles_track_changed` (an empty or None `language_code` → None) and reset to None when `video_id` changes.
  - `YouTubeScreens.command(name: str) -> None`, blocking up to 20 s. Names are `"next" | "previous" | "play_pause" | "subtitles"`. It acts on the same screen `_next_chapter` picks (playing first). For subtitles: if on, call `set_closed_captions(None, vid)`; else `pick_caption(caption_languages(fetch_page(vid)))`. If that is None, log `no captions`. Otherwise set that language. Set `screen.subtitles` optimistically, since tvOS/YouTube may not echo.
  - `YouTubeScreens.subtitles_on() -> bool`.
  - In `ha_daemon`: `{"youtube": name}` now accepts the four names above plus `next_chapter`. `_conds["youtube_subtitles"] = lambda: _youtube is not None and _youtube.subtitles_on()`. `_conds["youtube_playing"]` is true when the active screen's state is `State.Playing`. Lounge events call `_signal()` (pass a callback into `YouTubeScreens`).

- [ ] **Step 1: Write the failing tests**:
  - `test_caption_languages_parses_tracks` on a trimmed `fixtures/watch_captions.html` containing `"captionTracks":[{"baseUrl":"…","languageCode":"de"…},{"languageCode":"en"…}]` → `["de", "en"]`;
  - `test_pick_caption_prefers_en_prefix` (`["de", "en-US"]` → `"en-US"`; `["de"]` → `"de"`; `[]` → None);
  - `test_subtitle_event_sets_and_clears` (build a `Screen`, call `asyncio.run(screen.subtitles_track_changed(SubtitlesTrackEvent({"videoId": "v", "languageCode": "en"})))`, assert `"en"`; then an empty code → None);
  - `test_new_video_resets_subtitles`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** Make the fixture by fetching a real watch page with captions once and trimming it to the `captionTracks` JSON (no cookies, no personal data).
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** `git commit -m "youtube: next/previous/play-pause and subtitles toggle with live state"`.

---

### Task 8: TV Room tabs config, deploy, confirm on the device

**Files:**
- Modify: `homeassistant/streamdock_n3/config.tv_room.json`, `homeassistant/streamdock_n3/config.yaml` (`version: "0.5.0-1"`), `homeassistant/streamdock_n3/DOCS.md`, `docs/JOURNAL.md`
- Test: `homeassistant/streamdock_n3/tests/test_config.py`

**Interfaces:**
- Consumes: all action kinds and conditions from Tasks 5–7; icon ids from Task 1.
- Produces: the `tabs` config shape:

```json
"tabs": {
  "timeouts": {"open": 10, "after_press": 5, "context_debounce": 1},
  "round": {"7": ["apps", "apps_more"], "8": ["sonos"]},
  "context": { …Task 3 cfg… },
  "launch": { …Task 6 cfg… },
  "pages": {
    "apps": {"close_on_press": true, "keys": {"1": {"icon": "netflix", "action": {"launch": {"device": "atv", "app": "com.netflix.Netflix"}}}, …}},
    "apps_more": {…}, "sonos": {…},
    "context:idle": {…}, "context:streaming": {…}, "context:youtube": {…},
    "context:lg_media": {…}, "context:lg_app": {…}, "context:live_game": {…}
  }
}
```

Key contents (spec §5.2 and §6; phase 1 uses base keys where a control is "test"):

| Page | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| `apps` | Netflix | YouTube | Prime Video | Apple TV app | LG media player (`device: lg`) | Max |
| `apps_more` | Hulu | Disney+ | Paramount+ | Pluto TV | Tubi | ATV input (`media_player.select_source` `Apple OTT`) |
| `sonos` | Night (state) | Speech (state) | Next track (`next_playing` over the 5 other Sonos; `next_track__on` mode when any is `playing`, base `next_track__off`) | Arc mute (`script.tv_room_mute_toggle`; text tile `MUTE
ON` amber `#c77c02` while `is_volume_muted`, else `MUTE
OFF` grey) | Sub (state, text tile) | Surround (state, text tile) |
| `context:idle` | TV power (today's key 3 look, as `tv_power__on/off/muted`) | LIVE (Task 9; until then a dim `soccer_live__none` with no action) | ATV input | Night (state) | Home | Back |
| `context:streaming` | Play/Pause (`media_player.media_play_pause` on the ATV; `play_pause__playing` mode when the ATV state is `playing`) | −10 s (`skip_backward`) | +10 s (`skip_forward`) | Night (state) | Home | Back |
| `context:youtube` | Play/Pause (`{"youtube": "play_pause"}`, mode `youtube_playing`) | Next chapter | Next video | Previous video | Subtitles (`{"youtube": "subtitles"}`, mode `youtube_subtitles` → `subtitles__on`, base `subtitles__off`) | Back (Apple TV `menu`; on the LG `webostv.button BACK`, via a mode on LG source) |
| `context:lg_media` and `context:lg_app` | Play/Pause (`media_player.media_play_pause` on the LG; HA's webostv tracks paused itself) | `webostv.button REWIND` | `webostv.button FASTFORWARD` | Night | LG `HOME` | LG `BACK` |
| `context:live_game` | Play/Pause | Next game (Task 9; until then = `context:streaming` key 2) | Night (base key until subtitles pass in phase 2; avoids a second Home) | −10 s | Home | Back |

`button.1–6.press` → `{"tab_key": N}`, `button.7/8.press` → `{"tab_round": N}`, `button.9.press` unchanged. The top-level `keys` become `{}`.

- [ ] **Step 1: Write the failing config tests**:
  - `test_every_page_has_six_keys`;
  - `test_every_icon_exists`: every `icon` in every spec/variant/mode resolves via `icon_path` to a file under `homeassistant/streamdock_n3/icons/` (map `/config/icons` → repo folder);
  - `test_entities_are_known`: every `entities_of` and every `entity_id` inside actions is in the Global Constraints list;
  - `test_buttons_routed` (`button.1–6` → `tab_key`, `7/8` → `tab_round`, `9` unchanged);
  - `test_launch_ids_match_constraints`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Write the config.** Add a "Tabs" section to DOCS.md (shape, page names, action kinds `tab_key`/`tab_round`/`launch`/`next_playing`/`youtube`, named conditions, label vars, bare icon ids). Bump the version.
- [ ] **Step 4: Run all tests to verify pass.**
- [ ] **Step 5: Deploy.** `bash homeassistant/restore.sh`. Expected: it ends with the controller running and no missing-entity warnings. The app log shows `render: 6 keys in …s` on start.
- [ ] **Step 6: Device check with the user** (blocking). Ask the user to confirm on the dock:
  - the idle context shows at start;
  - round 1 → Apps, a double-click → 1B, a third press → Apps;
  - 10 s idle → context; a press on Sonos keeps it 5 s;
  - an app key launches the app and the context follows within ~1 s;
  - a launch with the TV off powers on first;
  - the YouTube layout appears with subtitles on/off showing state;
  - round 3 still powers on.

  Record the redraw time from the log. If a 6-key pass is over ~0.5 s, report it to the user before continuing.
- [ ] **Step 7: Update JOURNAL** (System state, Control mappings table → tabs, a Log entry with what was confirmed). Commit:

```bash
git add homeassistant/streamdock_n3 docs/JOURNAL.md
git commit -m "Tabs: TV Room tab config deployed and confirmed on the dock"
```

---

### Task 9: Live soccer

**Files:**
- Create: `homeassistant/streamdock_n3/soccer.py`
- Create: `homeassistant/streamdock_n3/tests/fixtures/uts_search_paramount.json`, `uts_search_mls.json` (trimmed, sanitized real responses), `tv_page_tokens.html` (a tiny synthetic snippet with fake `"utsk":"x"`, `"utscf":"y"`)
- Modify: `ha_daemon.py`, `config.tv_room.json`, `Dockerfile`, `config.yaml` (`0.5.0-2`), `DOCS.md`, `docs/JOURNAL.md`
- Test: `homeassistant/streamdock_n3/tests/test_soccer.py`

**Interfaces:**
- Consumes: `media_actions.launch`-style power-first (reuse `launch()`'s power step: factor it out as `media_actions.ensure_on(call, state_of, cfg, *, sleep, clock) -> bool` in this task), `Call`, `StateLookup`.
- Produces:
  - `@dataclass(frozen=True) class Game: id: str; title: str; league: str; kickoff: float; provider: str; url: str; app: str` (`provider` is `"paramount"` or `"apple"`, `app` is the bundle id to watch for).
  - `parse_tokens(html: str) -> tuple[str, str] | None`: regex `"utsk":"([^"]+)"` and `"utscf":"([^"]+)"`.
  - `parse_games(payload: dict, now: float) -> list[Game]`. It keeps `sportName == "Soccer"` items that are live: `clockScore` present and not `hasEventEnded`, or `liveBadgeTime.startTime <= now*1000 <= endTime`. The provider comes from `playables[0].channelId`: `tvs.sbd.10120` → paramount, using `punchoutUrls.play` and app `com.cbsvideo.app`; `tvs.sbd.4000` → apple, using `playables[0].url` and app `com.apple.TVWatchList`. Other providers are dropped.
  - `order(games: list[Game]) -> list[Game]`: by kickoff, then league, then id; deduplicated by id.
  - `class LiveSoccer(call, state_of, cfg, fetch=urllib-based, clock=time.time)`:
    - `games() -> list[Game]`: cached for `cache_s` (60), refreshed in the background every `poll_s` (180) for the key state. It refreshes the tokens from `token_page` (`https://tv.apple.com/us/channel/mls/tvs.sbd.7000`) once a day or on HTTP 4xx. It never raises; errors are logged and the last list is kept.
    - `start()`: refetch, then `ensure_on`, cursor 0, then `play(0)`.
    - `next()`: cursor + 1 modulo the list length, then `play`.
    - `play(i)`: `media_player.play_media {entity_id: atv, media_content_type: "url", media_content_id: game.url}`. After `select_after_s` (8), if the ATV `app_id == game.app` and its state is not `playing`, send `remote.send_command select` once.
    - `watching(state_of) -> bool`: a game was started and the ATV `app_id` still equals its `app`; cleared once the app changes.
    - `vars() -> {"soccer_count": str(n), "soccer_pos": f"{cursor+1}/{n}"}`.
  - The cfg section is `"soccer": {"atv", "atv_remote", "lg", "atv_source", "power_on_wait", "token_page", "cache_s": 60, "poll_s": 180, "select_after_s": 8}`. The UTS query params `caller=web&locale=en-US&pfm=web&sf=143441&v=100` and the queries `searchTerm=Paramount+` and `searchTerm=MLS` are constants in `soccer.py`.
  - In `ha_daemon`:
    - actions `{"soccer": "start"|"next"}` (in a thread);
    - `_conds["soccer_live"] = lambda: bool(_soccer.games())`;
    - `_vars()` includes `_soccer.vars()`;
    - `_watching_game = lambda: _soccer.watching(state_of)`;
    - `_signal()` after each refresh.
  - In the config:
    - `context:idle` key 2 = `{"icon": "soccer_live__none", "label": "", "modes": [{"when": "soccer_live", "icon": "soccer_live__live"}], "action": {"soccer": "start"}}`;
    - `context:live_game` key 2 = `{"label": "NEXT\\n{soccer_pos}", "color": "#1d6b32", "action": {"soccer": "next"}}`.

- [ ] **Step 1: Capture fixtures.** Fetch the two shelves once with live tokens. Trim each to ≤ 6 items covering: a live P+ soccer game, a live MLS game, an upcoming soccer game, a non-soccer P+ event, and an ended game. Remove nothing identifying the user (there is nothing), but **do not store the tokens**: the fixture is the response body only. `grep -c utsk homeassistant/streamdock_n3/tests/fixtures/*` must print 0 for the JSON files.
- [ ] **Step 2: Write the failing tests**:
  - `test_parse_tokens`;
  - `test_parse_games_filters_live_soccer_and_providers` (exact ids expected from the fixture);
  - `test_order_by_kickoff_then_league`;
  - `test_next_wraps`;
  - `test_play_sends_select_when_not_playing` and `test_play_no_select_when_playing` (fake clock/sleep);
  - `test_watching_clears_on_app_change`;
  - `test_fetch_error_keeps_last_list` (fake fetch raises after one success);
  - `test_vars`.
- [ ] **Step 3: Run to verify failure.**
- [ ] **Step 4: Implement `soccer.py`, `ensure_on` and the wiring/config.**
- [ ] **Step 5: Run all tests to verify pass.**
- [ ] **Step 6: Deploy** (`bash homeassistant/restore.sh`) **and check with the user during a live match** (blocking; it can wait for a match day). Check:
  - LIVE lights with games on;
  - a press starts game 1 (note whether a profile picker or prompt appeared and whether the auto-`select` handled it);
  - Next game cycles, wrapping at the end;
  - the context returns to normal when another app opens.

  Record the results and the observed token lifetime in JOURNAL. Commit:

```bash
git add homeassistant/streamdock_n3 docs/JOURNAL.md
git commit -m "Live soccer key: Paramount+ and MLS from the Apple TV app listing"
```

---

### Task 10: Probe tool and the live test session

**Files:**
- Create: `homeassistant/streamdock_n3/probe.py`, `homeassistant/tools/probe.sh`, `homeassistant/tools/probes/*.json` (one file per §10 test group)
- Modify: `homeassistant/streamdock_n3/atv_touch.py`, `ha_daemon.py`, `Dockerfile`, `config.yaml` (`0.5.0-3`), `DOCS.md`, `docs/JOURNAL.md`
- Test: `homeassistant/streamdock_n3/tests/test_probe.py`

**Interfaces:**
- Consumes: `AppleTVTouch`, `CoreAPI.call`, `YouTubeScreens`.
- Produces:
  - `AppleTVTouch.run(fn: Callable[[Any], Awaitable[Any]], timeout: float = 10) -> Any`: awaits `fn(atv)` on the pyatv loop. Raises `RuntimeError("not connected")` without a connection.
  - `probe.py`:
    - `parse_request(text: str) -> list[dict]` validates a probe list. Each step is `{"kind": "mrp"|"companion"|"ha"|"lounge"|"webos"|"wait"|"snapshot", …}`:
      - `mrp`: `{"command": "<CommandInfo name>", "options": {…}}`, sent with `MrpRemoteControl._send_command`;
      - `companion`: `{"mcc": "<MediaControlCommand name>", "args": {…}}` or `{"hid": "<name>"}`;
      - `ha`: `{"service": "domain.service", "data": {…}}`;
      - `lounge`: `{"method": "next"|…, "args": […]}`;
      - `webos`: `{"command": "ssap path", "payload": {…}}` via HA `webostv.command` with `return_response`;
      - `wait`: `{"s": 2}`;
      - `snapshot`: logs the ATV player's `supportedCommands` names, current app, HA states of ATV/LG, and Lounge state.
    - Unknown kinds raise `ValueError` naming the step.
    - `class ProbeRunner(touch, call, youtube)` watches `/config/probe_request.json`. It consumes the file, runs the steps in order, and logs each as `probe: <n> <kind> <summary> -> ok <result>` or `-> error <Type>: <msg>`. It finishes with `probe: done`. One failing step doesn't stop the rest.
  - `probe.sh <file.json>`: `scp`s the file to `/app_configs/local_streamdock_n3/probe_request.json` over `ssh homeassistant`, then follows `ha apps logs local_streamdock_n3 --lines 1000` until `probe: done`, printing `probe:` lines.
  - The probe files map one-to-one to spec §10 items 1–6. Each sets up its group, e.g.:
    - `ff_rew.json`: snapshot, `BeginFastForward`, wait 3, `EndFastForward`, `ChangePlaybackRate {"playbackRate": 24.0}`, wait 3, `Play`, the Companion `FastForwardBegin`/`right`/`FastForwardEnd`, and HA `remote.send_command right hold_secs 1`;
    - `subtitles_atv.json`: the language-option builder from the research notes §3A (`EnableLanguageOption` with an NSKeyedArchiver-wrapped `LanguageOption`, built with `plistlib`; put the builder in `probe.py` as `language_option_payload(type: int, tag: str | None, identifier: str | None) -> bytes`, so phase 2 can reuse it).

- [ ] **Step 1: Write the failing tests**:
  - `test_parse_request_rejects_unknown_kind`;
  - `test_parse_request_accepts_all_kinds`;
  - `test_language_option_payload_roundtrip`: `plistlib.loads(payload)` has `$archiver == "NSKeyedArchiver"`, and the archived bytes decode as a `LanguageOption` protobuf with the given `type`/`languageTag`. The protobuf comes from `pyatv.protocols.mrp.protobuf`. Skip the test if pyatv isn't installed on the PC, and add `pyatv==0.18.0` to `tests/requirements.txt`.
  - `test_runner_continues_after_error` (fake touch/call; step 2 raises; steps 1 and 3 logged ok).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement**, then deploy with `restore.sh`.
- [ ] **Step 4: Live test session** (blocking; the user at the TV). For each app in spec §10 item 1, start playback, run `bash homeassistant/tools/probe.sh homeassistant/tools/probes/<group>.json`, and ask the user what the TV did. Keep a results table: app × control → works / doesn't / how.
- [ ] **Step 5: Record and plan the next phase.** Write the results to JOURNAL (a Log entry plus a per-app capability table). Set `mrp_raw_log` back to false if no more research is planned (JOURNAL open item). Then write the phase-2 plan `docs/superpowers/plans/<date>-actionring-tabs-phase2.md` with superpowers:writing-plans, from the results table. Commit:

```bash
git add homeassistant docs
git commit -m "Probe tool and live test results for phase 2"
```
