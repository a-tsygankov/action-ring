"""streamdock-n3 daemon plus Home Assistant integration.

Runs the upstream daemon unchanged and adds:

1. Tabs with live key looks. The six LCD keys show one page at a time: a
   context page ("context:<layout>", picked from the LG TV / Apple TV
   foreground app, context.py) or a temporary page opened by a round button
   that closes on its own (tabs.py). A "tabs" config section holds "pages",
   "round" ({"7": ["apps", "apps_more"]}), "timeouts" and "context"; without
   one, the config's "keys" are the only page. Map presses with
   {"tab_key": <key>} ({"key_action": <key>} is an alias) and
   {"tab_round": <button>}. One renderer thread redraws only the keys whose
   look changed, on HA state events, presses and tab timers. One thread
   subscribes to Home Assistant (Supervisor WebSocket proxy, app token).

   A key may follow an entity's state with per-state overrides of
   icon/label/color:

       "1": {"label": "Night", "color": "#3b3f9e",
             "state_entity": "switch.tv_room_tv_room_night_sound",
             "on":  {"label": "Night\\nON", "color": "#3b5bdb"},
             "off": {"label": "Night\\nOFF", "color": "#262626"},
             "unavailable": {"label": "Night\\n?", "color": "#5c3b00"}}

   "off"/"standby" use "off"; unavailable/unknown (or HA unreachable) use
   "unavailable"; any other state ("on", "playing", "idle", ...) uses "on",
   so media players work as well as switches. Missing overrides fall back to
   the key's base look (keyspec.py).

2. Accelerated Apple TV knobs (knob_accel.py). An action of the form
   {"atv_knob": {"axis": "horizontal"|"vertical", "direction": 1|-1}} goes to
   the knob engine instead of a shell command; the top-level "atv" config
   names the remote/media_player entities and tuning values.

3. Key modes. A key may list "modes"; the first whose "when" holds sets the
   key's icon/label/color and its press action, else the key's own
   "action"/look apply. "when" is a named condition ("youtube": YouTube in
   the foreground on a configured screen) or a list of
   {"entity", "attr" ("state" by default), "in": [...]} (any).

4. YouTube (yt_lounge.py): {"youtube": name} controls the video playing in a
   linked YouTube TV app: "next_chapter" jumps to the next chapter; "forward"
   (+10 s), "next", "previous", "play_pause" and "subtitles" (toggle) go
   through the Lounge. Conditions "youtube_playing", "youtube_subtitles" and
   "youtube_chapters" (the current video has chapters) follow its live state.

5. Now-playing log (np_log.py): "now_playing_log": {"entities": [...]} logs
   what changes in those media players, to find ad markers per app.

6. Ad detection (ad_detect.py): "ad_detect": {"speaker", "media_player",
   "apps"} mutes the speaker during ad breaks of apps with a separate show
   clock (Pluto TV), from the app's own MRP session (AirPlay pairing).

7. Media actions (media_actions.py): {"launch": {"device": "atv"|"lg",
   "app": <id>}} powers the TV on if needed, then starts the app (config
   "tabs.launch": lg, atv, atv_remote, atv_source, power_on_wait);
   {"next_playing": [entities]} skips to the next track on the first playing
   speaker that leads its group.

8. Live soccer (soccer.py): a "soccer" config section (atv, atv_remote, lg,
   atv_source, power_on_wait, token_page, cache_s, poll_s, idle_poll_s,
   select_after_s) polls the Apple TV app listing for live Paramount+ / MLS
   games.
   {"soccer": "start"} powers on and plays the first, {"soccer": "next"}
   the next one (wrapping). Condition "soccer_live" holds while any game is
   live; labels get {soccer_count} and {soccer_pos}; context "live_game"
   applies while a started game's app is in the foreground.

9. Service logo (logo.py): a "tabs.logo" section flashes the logo of what is
   on screen (the app, the PS5 input, else the Apple TV) on key 6 of a
   context page for 2 s every 10-15 s; the key keeps its action.
   "tabs.context.content_apps" (the TV app) count only for real content, and
   an Apple TV app only once seen playing (context.AtvApp).

10. Probe tool (probe.py): with the Apple TV connection up, a request written
   to probe_request.json (homeassistant/tools/probe.sh) runs raw MRP,
   Companion, HA, Lounge and webOS commands and logs each result as
   "probe: …", for the live test session.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from context import AtvApp, foreground_layout
from keyspec import Look, changed_keys, entities_of, icon_path, resolve_action, resolve_look
from knob_accel import AppleTVKnobs
from logo import LogoClock, app_icons, logo_for
from media_actions import launch, next_playing
from streamdock_n3 import config as configmod
from streamdock_n3 import daemon, icons, paths
from tabs import TabEngine
from websockets.sync.client import connect

WS_URL = "ws://supervisor/core/websocket"
RETRY_SECONDS = 5
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
TICK_SECONDS = 0.2
SLOW_PASS_SECONDS = 0.5
EMPTY_LOOK = Look(None, "", "#000000")  # a key the visible page leaves undefined
ERROR_LOOK = Look(None, "?", "#5c3b00")  # a key whose spec failed to resolve
YOUTUBE_COMMANDS = ("next_chapter", "forward", "next", "previous", "play_pause", "subtitles")
SOCCER_COMMANDS = ("start", "next")

_ready = threading.Event()
_device = None
_draw_lock = threading.Lock()
_knobs: AppleTVKnobs | None = None
_youtube: Any = None  # yt_lounge.YouTubeScreens
_np_log: Any = None  # np_log.NowPlayingLog
_soccer: Any = None  # soccer.LiveSoccer; set in main() with a "soccer" config
_engine = TabEngine({}, {})  # replaced in main() before the device starts
_ctx_cfg: dict | None = None  # config "tabs.context"; None = no context detection
_atv_app: AtvApp | None = None  # with _ctx_cfg; HA thread only
_atv_now: str | None = None  # the Apple TV app that counts; set by the HA thread
_logo_cfg: dict | None = None  # config "tabs.logo"; None = no logo
_logo_icons: dict[str, str] = {}  # app id -> icon id
_logo_clock: LogoClock | None = None  # ticker thread only
_logo_on = False  # logo phase, set by the ticker, read by the renderer
_launch_cfg: dict | None = None  # config "tabs.launch"; None = launch actions do nothing
_core: "CoreAPI | None" = None  # shared REST client for media actions; set in main()
_wake = threading.Event()  # set by _signal(): the renderer has work
_drawn: dict[int, Look] = {}  # key -> look on screen; renderer thread only
_render_errors: set[str] = set()  # key spec errors already logged; renderer thread only

# entity_id -> {"s": state, "a": attributes}, kept current by _watch().
_states: dict[str, dict[str, Any]] = {}
_states_lock = threading.Lock()


def state_of(entity_id: str) -> dict[str, Any] | None:
    with _states_lock:
        st = _states.get(entity_id)
        return {"s": st.get("s"), "a": dict(st.get("a") or {})} if st else None


# Named "when" conditions for key modes.
_conds: dict[str, Callable[[], bool]] = {
    "youtube": lambda: _youtube is not None and _youtube.any_active(),
    "youtube_playing": lambda: _youtube is not None and _youtube.playing(),
    "youtube_subtitles": lambda: _youtube is not None and _youtube.subtitles_on(),
    "youtube_chapters": lambda: _youtube is not None and _youtube.has_chapters(),
    "soccer_live": lambda: _soccer is not None and bool(_soccer.games()),
}


def _watching_game() -> bool:
    """Whether the Apple TV is showing a live soccer game (context "live_game")."""
    return _soccer is not None and _soccer.watching(state_of)


def _vars() -> dict[str, str]:
    """Values for {name} placeholders in key labels."""
    return _soccer.vars() if _soccer is not None else {}


def _signal() -> None:
    """Wake the renderer; signals during a pass coalesce into one more pass."""
    _wake.set()


# -- upstream hooks ------------------------------------------------------------

_upstream_apply_icons = daemon.apply_icons
_upstream_run_actions = daemon.run_actions


def _apply_icons(device, config):
    # Static icons first; state redraws must not race the initial paint.
    global _device
    _upstream_apply_icons(device, config)
    _device = device
    _ready.set()


def _run_actions(actions: Any, *, dry_run: bool) -> None:
    # Runs on the SDK reader thread: tab presses only update the engine and
    # wake the renderer, they never draw here. A bad config (a non-numeric
    # key, a spec that fails to resolve) is logged, never raised into the SDK.
    if isinstance(actions, dict) and ("tab_key" in actions or "key_action" in actions):
        try:
            key = int(actions.get("tab_key", actions.get("key_action")))
            spec = _engine.key_press(key, time.monotonic())
            if spec:
                _run_actions(resolve_action(spec, state_of, _conds), dry_run=dry_run)
        except Exception as err:
            print(f"tabs: {type(err).__name__}: {err}", flush=True)
        finally:
            _signal()
        return
    if isinstance(actions, dict) and "tab_round" in actions:
        try:
            _engine.round_press(int(actions["tab_round"]), time.monotonic())
        except Exception as err:
            print(f"tabs: {type(err).__name__}: {err}", flush=True)
        finally:
            _signal()
        return
    if isinstance(actions, dict) and "youtube" in actions:
        command = actions["youtube"]
        if dry_run or _youtube is None or command not in YOUTUBE_COMMANDS:
            print(f"youtube: ignoring {command!r}", flush=True)
            return
        # Lounge commands wait on the network; keep the SDK reader thread free.
        run = _youtube.next_chapter if command == "next_chapter" else lambda: _youtube.command(command)
        threading.Thread(target=_safe, args=(run,), daemon=True).start()
        return
    if isinstance(actions, dict) and "soccer" in actions:
        command = actions["soccer"]
        if dry_run or _soccer is None or command not in SOCCER_COMMANDS:
            print(f"soccer: ignoring {command!r}", flush=True)
            return
        # Fetching, powering on and the select check take seconds.
        run = _soccer.start if command == "start" else _soccer.next
        threading.Thread(target=_safe, args=(run,), daemon=True).start()
        return
    if isinstance(actions, dict) and "launch" in actions:
        req = actions["launch"]
        if dry_run:
            print(f"launch (dry run): {req}", flush=True)
        elif _launch_cfg is None or _core is None:
            print("launch: no tabs.launch config", flush=True)
        else:
            # Powering the TV on can take ~25 s; keep the SDK reader thread free.
            threading.Thread(
                target=_safe,
                args=(lambda: launch(_core.call, state_of, _launch_cfg, req["device"], req["app"]),),
                daemon=True,
            ).start()
        return
    if isinstance(actions, dict) and "next_playing" in actions:
        candidates = list(actions["next_playing"])
        if dry_run:
            print(f"next-track (dry run): {candidates}", flush=True)
        elif _core is None:
            print("next-track: no Core API; ignoring", flush=True)
        else:
            threading.Thread(target=_safe, args=(lambda: _next_track(candidates),), daemon=True).start()
        return
    if isinstance(actions, dict) and "atv_knob" in actions:
        if dry_run:
            print(f"atv-knob (dry run): {actions['atv_knob']}", flush=True)
        elif _knobs is None:
            print("atv-knob: no \"atv\" section in config; ignoring", flush=True)
        else:
            _knobs.detent(actions["atv_knob"])
        return
    _upstream_run_actions(actions, dry_run=dry_run)


def _next_track(candidates: list[str]) -> None:
    entity = next_playing(_core.call, state_of, candidates)
    print(f"next-track: {entity or 'nothing playing'}", flush=True)


def _safe(fn) -> None:
    try:
        fn()
    except Exception as err:
        print(f"action failed: {type(err).__name__}: {err}", flush=True)


daemon.apply_icons = _apply_icons
daemon.run_actions = _run_actions


# -- Core REST calls -------------------------------------------------------------


class CoreAPI:
    """Blocking service calls over one kept-alive connection to the Supervisor."""

    def __init__(self) -> None:
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def call(self, domain: str, service: str, data: dict[str, Any]) -> None:
        self._post(domain, service, data, "")

    def call_response(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        """A service call with return_response: the parsed reply,
        {"changed_states": [...], "service_response": {...}}. Only for
        services that support responses (HA answers 400 otherwise)."""
        text = self._post(domain, service, data, "?return_response")
        return json.loads(text) if text else None

    def _post(self, domain: str, service: str, data: dict[str, Any], query: str) -> bytes:
        body = json.dumps(data)
        headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None:
                    self._conn = http.client.HTTPConnection("supervisor", 80, timeout=10)
                try:
                    self._conn.request(
                        "POST", f"/core/api/services/{domain}/{service}{query}", body, headers
                    )
                    resp = self._conn.getresponse()
                    text = resp.read()
                except (http.client.HTTPException, OSError):
                    # Usually a kept-alive connection the server closed; the
                    # request was not processed, so one retry is safe.
                    self._conn.close()
                    self._conn = None
                    if attempt == 2:
                        raise
                    continue
                if resp.status >= 300:
                    raise RuntimeError(f"{domain}.{service}: HTTP {resp.status} {text[:200]!r}")
                return text
        raise AssertionError("unreachable")  # the second attempt returns or raises


# -- tabs: engine setup, renderer, ticker --------------------------------------


def _key_specs(keys: Any) -> dict[str, dict]:
    return {str(k): v for k, v in keys.items() if isinstance(v, dict)} if isinstance(keys, dict) else {}


def _make_engine(config: dict[str, Any]) -> tuple[TabEngine, dict[str, dict], dict | None]:
    """The tab engine, its pages, and the context config (None: no detection)."""
    tabs = config.get("tabs")
    if not isinstance(tabs, dict):
        # No tabs: the config's own keys are the one page, shown for good.
        pages = {"context:idle": {"keys": config["keys"]}}
        return TabEngine(pages, {}), pages, None
    pages = {
        name: {**page, "keys": _key_specs(page.get("keys"))}
        for name, page in (tabs.get("pages") or {}).items()
        if isinstance(page, dict)
    }
    rounds = {int(k): list(v) for k, v in (tabs.get("round") or {}).items()}
    t = tabs.get("timeouts") or {}
    engine = TabEngine(
        pages,
        rounds,
        open_s=float(t.get("open", 10)),
        after_press_s=float(t.get("after_press", 5)),
        debounce_s=float(t.get("context_debounce", 1)),
    )
    ctx = tabs.get("context")
    return engine, pages, ctx if isinstance(ctx, dict) else None


def _action_entities(pages: dict[str, dict]) -> set[str]:
    """Speakers named by next_playing actions: their state must be watched."""
    out: set[str] = set()
    for page in pages.values():
        for spec in page["keys"].values():
            modes = [m for m in spec.get("modes") or [] if isinstance(m, dict)]
            for action in [spec.get("action"), *(m.get("action") for m in modes)]:
                for act in action if isinstance(action, list) else [action]:
                    if isinstance(act, dict) and isinstance(act.get("next_playing"), list):
                        out |= {e for e in act["next_playing"] if isinstance(e, str)}
    return out


def _image_for(key: int, look: Look) -> str:
    """The icon file, or a generated text tile (cached by label and color)."""
    if look.icon and Path(look.icon).exists():
        return look.icon
    # A missing icon file still names the key, e.g. "netflix".
    label = look.label or (Path(look.icon).stem if look.icon else "")
    color = icons.parse_color(look.color, icons.FALLBACK_COLORS[key - 1])
    digest = hashlib.sha1(f"{label}\0{color}".encode()).hexdigest()[:10]
    out = paths.generated_key_dir() / f"key-{key}-{digest}.jpg"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.name + ".tmp")
        icons.make_icon(label, color, tmp)
        os.replace(tmp, out)  # never leave a half-written tile to be reused
    return str(out)


def _look_for(key: int, keys: dict, label_vars: dict[str, str]) -> Look:
    """The key's look; a spec that fails to resolve shows ERROR_LOOK (logged once per error)."""
    if str(key) not in keys:
        return EMPTY_LOOK
    try:
        return resolve_look(keys[str(key)], state_of, _conds, label_vars)
    except Exception as err:  # one bad spec must not blank the other keys
        msg = f"key {key}: {type(err).__name__}: {err}"
        if msg not in _render_errors:
            _render_errors.add(msg)
            print(f"render: {msg}", flush=True)
        return ERROR_LOOK


def _logo_look() -> tuple[int, Look] | None:
    """(key, look) while the logo shows on a context page."""
    if _logo_cfg is None or not _logo_on or _ctx_cfg is None:
        return None
    page = _engine.visible
    if not page.startswith("context:"):
        return None
    icon = logo_for(page.split(":", 1)[1], state_of, _ctx_cfg, _logo_cfg, _logo_icons, _atv_now)
    return (int(_logo_cfg.get("key", 6)), Look(icon_path(icon), "", None)) if icon else None


def _render_pass() -> None:
    keys = _engine.keys()  # one snapshot per pass
    label_vars = _vars()
    looks = {k: _look_for(k, keys, label_vars) for k in range(1, 7)}
    logo = _logo_look()
    if logo is not None and logo[0] in looks:
        looks[logo[0]] = logo[1]
    todo = changed_keys(_drawn, looks)
    if not todo:
        return
    started = time.monotonic()
    images = {k: _image_for(k, looks[k]) for k in todo}
    with _draw_lock:
        for k in todo:
            _device.set_key_image(k, images[k])
        _device.refresh()
    _drawn.update((k, looks[k]) for k in todo)
    elapsed = time.monotonic() - started
    slow = " (slow)" if elapsed > SLOW_PASS_SECONDS else ""
    print(f"render: {len(todo)} keys in {elapsed:.2f}s{slow}", flush=True)


def _render() -> None:
    _ready.wait()  # after the upstream initial paint; _drawn is empty, so all 6 draw
    _signal()
    while True:
        _wake.wait()
        _wake.clear()  # a signal from here on schedules exactly one more pass
        try:
            _render_pass()
        except Exception as err:  # keys not drawn stay out of _drawn and retry
            print(f"render: {type(err).__name__}: {err}", flush=True)


def _tick() -> None:
    global _logo_on
    seen = -1
    while True:
        try:
            now = time.monotonic()
            _engine.tick(now)
            if _logo_clock is not None and _logo_clock.showing(now) != _logo_on:
                _logo_on = not _logo_on
                _signal()
            version = _engine.version  # unlocked read: only a "something changed" hint
            if version != seen:
                seen = version
                print(f"tabs: showing {_engine.visible}", flush=True)
                _signal()
        except Exception as err:  # a dead ticker would leave a temporary tab stuck
            print(f"tick: {type(err).__name__}: {err}", flush=True)
        time.sleep(TICK_SECONDS)


def _update_context() -> None:
    global _atv_now
    if _ctx_cfg is not None:
        _atv_now = _atv_app.update(state_of) if _atv_app is not None else None
        layout = foreground_layout(state_of, _ctx_cfg, _watching_game(), _atv_now)
        _engine.set_context(layout, time.monotonic())
    _signal()


# -- HA subscription -------------------------------------------------------------


def _watch(entities: list[str]) -> None:
    while True:
        try:
            with connect(WS_URL, open_timeout=10, ping_interval=20) as ws:
                json.loads(ws.recv())  # auth_required
                ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
                reply = json.loads(ws.recv())
                if reply.get("type") != "auth_ok":
                    raise RuntimeError(f"auth failed: {reply.get('type')}")
                ws.send(json.dumps({"id": 1, "type": "subscribe_entities", "entity_ids": entities}))
                for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "result" and not msg.get("success"):
                        raise RuntimeError(f"subscribe failed: {msg.get('error')}")
                    if msg.get("type") != "event":
                        continue
                    event = msg["event"]
                    # Compressed states: "a" full (initial + added), "c" diffs, "r" removed.
                    with _states_lock:
                        for entity_id, st in event.get("a", {}).items():
                            _states[entity_id] = {"s": st.get("s"), "a": st.get("a") or {}}
                        for entity_id, diff in event.get("c", {}).items():
                            cur = _states.setdefault(entity_id, {"s": None, "a": {}})
                            plus, minus = diff.get("+", {}), diff.get("-", {})
                            if "s" in plus:
                                cur["s"] = plus["s"]
                            cur["a"].update(plus.get("a") or {})
                            for name in minus.get("a") or []:
                                cur["a"].pop(name, None)
                        for entity_id in event.get("r", []):
                            _states.pop(entity_id, None)
                    _update_context()
                    if _np_log is not None:
                        for entity_id in {*event.get("a", {}), *event.get("c", {})}:
                            _np_log.update(entity_id, state_of(entity_id))
        except Exception as err:  # keep the keys alive across HA restarts
            print(f"ha-state: {err!r}; retrying in {RETRY_SECONDS}s", flush=True)
        # Show "unavailable" while disconnected, so stale state isn't trusted.
        with _states_lock:
            _states.clear()
        try:
            _update_context()
        except Exception as err:  # the retry loop must survive
            print(f"ha-state: context: {type(err).__name__}: {err}", flush=True)
        time.sleep(RETRY_SECONDS)


def main() -> int:
    global _knobs, _youtube, _np_log, _engine, _ctx_cfg, _launch_cfg, _core, _soccer
    global _atv_app, _logo_cfg, _logo_icons, _logo_clock
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    args, _ = parser.parse_known_args()
    config = configmod.normalize(configmod.load(args.config or configmod.ensure_config()))

    _engine, pages, _ctx_cfg = _make_engine(config)
    entities: set[str] = {
        e for page in pages.values() for spec in page["keys"].values() for e in entities_of(spec)
    }
    if _ctx_cfg is not None:
        entities |= {e for e in (_ctx_cfg.get("lg"), _ctx_cfg.get("atv")) if e}
        _atv_app = AtvApp(_ctx_cfg)
    _core = CoreAPI()
    tabs_cfg = config.get("tabs")
    if isinstance(tabs_cfg, dict) and isinstance(tabs_cfg.get("logo"), dict):
        _logo_cfg = tabs_cfg["logo"]
        _logo_icons = app_icons(pages, _logo_cfg.get("apps"))
        _logo_clock = LogoClock(_logo_cfg)
    if isinstance(tabs_cfg, dict) and isinstance(tabs_cfg.get("launch"), dict):
        _launch_cfg = tabs_cfg["launch"]
        entities |= {e for e in (_launch_cfg.get("lg"),) if e}
    entities |= _action_entities(pages)
    soccer_cfg = config.get("soccer")
    if isinstance(soccer_cfg, dict):
        from soccer import LiveSoccer

        _soccer = LiveSoccer(_core.call, state_of, soccer_cfg, on_change=_signal)
        entities |= {e for e in (soccer_cfg.get("lg"), soccer_cfg.get("atv")) if e}
        threading.Thread(target=_soccer.poll_forever, daemon=True).start()
    np_entities = (config.get("now_playing_log") or {}).get("entities") or []
    if np_entities:
        from np_log import NowPlayingLog

        _np_log = NowPlayingLog(np_entities)
        entities |= set(np_entities)
    yt = config.get("youtube")
    if isinstance(yt, dict) and yt.get("screens"):
        try:
            from yt_lounge import YouTubeScreens

            _youtube = YouTubeScreens(yt, state_of, on_change=_signal)
            entities |= _youtube.entities()
        except Exception as err:
            print(f"youtube: disabled ({err!r})", flush=True)
    ad_cfg = config.get("ad_detect")
    if isinstance(ad_cfg, dict) and ad_cfg.get("speaker"):
        # Must hook MRP before the Apple TV connection below is made.
        try:
            import ad_detect

            ad_detect.install(ad_detect.AdDetector(ad_cfg, CoreAPI().call, state_of))
            entities |= {e for e in (ad_cfg.get("speaker"), ad_cfg.get("media_player")) if e}
        except Exception as err:
            print(f"ad-detect: disabled ({err!r})", flush=True)
    atv = config.get("atv")
    if isinstance(atv, dict) and atv.get("remote") and atv.get("media_player"):
        touch = None
        if atv.get("mrp_raw_log"):  # diagnostics: dump raw MRP now-playing messages
            try:
                import mrp_raw

                mrp_raw.install()
            except Exception as err:
                print(f"mrp-raw: disabled ({err!r})", flush=True)
        try:  # direct Companion connection for preview scrubbing (pairing via files)
            from atv_touch import AppleTVTouch

            touch = AppleTVTouch()
        except Exception as err:
            print(f"atv-touch: disabled ({err!r}); scrubbing uses seek", flush=True)
        _knobs = AppleTVKnobs(atv, CoreAPI().call, state_of, touch)
        entities.add(atv["media_player"])
        if touch is not None:  # live test probes (homeassistant/tools/probe.sh)
            try:
                from probe import ProbeRunner

                probe_core = CoreAPI()

                def probe_call(domain: str, service: str, data: dict, response: bool = False) -> Any:
                    if response:
                        return probe_core.call_response(domain, service, data)
                    return probe_core.call(domain, service, data)

                ProbeRunner(touch, probe_call, _youtube, state_of).start()
            except Exception as err:
                print(f"probe: disabled ({err!r})", flush=True)
    if entities:
        threading.Thread(target=_watch, args=(sorted(entities),), daemon=True).start()
    threading.Thread(target=_render, daemon=True).start()
    threading.Thread(target=_tick, daemon=True).start()
    return daemon.main()


if __name__ == "__main__":
    raise SystemExit(main())
