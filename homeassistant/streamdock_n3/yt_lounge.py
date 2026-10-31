"""YouTube TV-app control via the Lounge ("Link with TV code") protocol.

Each configured screen (the YouTube app on a TV) is linked once with the
12-digit code from the app's Settings > Link with TV code. While Home
Assistant reports YouTube in the foreground on that TV, the app keeps a
Lounge session, which gives the playing video, its position, play/ad state
and a seek command. Used for "next chapter" (yt_chapters.py), a 10 s skip
forward, next/previous, play/pause and a subtitles toggle, and ad logging.

Linking is driven through files in the app's config share (/config here,
/addon_configs/local_streamdock_n3 outside): write the code to
yt_pair_<screen>; tokens are saved to yt_lounge.json (secret; not in repo).
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pyytlounge import EventListener, State, YtLoungeApi

import yt_chapters

CONFIG_DIR = Path("/config")
AUTH_FILE = CONFIG_DIR / "yt_lounge.json"
DEVICE_NAME = "ActionRing"

StateLookup = Callable[[str], "dict[str, Any] | None"]


def log(msg: str) -> None:
    print(f"youtube: {msg}", flush=True)


def matches(state_of: StateLookup, cond: dict[str, Any]) -> bool:
    """{"entity": id, "attr": name|"state", "in": [values]} against live HA state."""
    st = state_of(cond["entity"]) or {}
    value = st.get("s") if cond.get("attr", "state") == "state" else (st.get("a") or {}).get(cond["attr"])
    return value in cond.get("in", [])


class Screen(EventListener):
    """Live playback state of one YouTube screen, fed by Lounge events."""

    def __init__(
        self,
        name: str,
        match: list[dict[str, Any]],
        prefetch: Callable[[str], None],
        on_change: Callable[[], None] | None = None,
    ):
        self.name = name
        self.match = match
        self._prefetch = prefetch
        self._on_change = on_change or (lambda: None)
        self.api: YtLoungeApi | None = None
        self.video_id: str | None = None
        self.state: State | None = None
        self.position = 0.0
        self.position_at = 0.0
        self.in_ad = False
        self.subtitles: str | None = None  # current caption language; None = off

    def position_now(self) -> float:
        elapsed = time.monotonic() - self.position_at if self.state == State.Playing else 0
        return self.position + elapsed

    def _set(self, current_time: float | None, state: State | None) -> None:
        if current_time is not None:
            self.position, self.position_at = current_time, time.monotonic()
        if state is not None:
            self.state = state
            if self.in_ad and state not in (State.Advertisement, State.Buffering, State.Starting):
                self.in_ad = False
                log(f"{self.name}: ad ended")

    async def now_playing_changed(self, event) -> None:
        if event.video_id and event.video_id != self.video_id:
            self.video_id = event.video_id
            self.subtitles = None  # captions are per video; the TV reports them afterwards
            log(f"{self.name}: now playing {event.video_id}")
            self._prefetch(event.video_id)
        self._set(event.current_time, event.state)
        self._on_change()

    async def playback_state_changed(self, event) -> None:
        self._set(event.current_time, event.state)
        self._on_change()

    async def subtitles_track_changed(self, event) -> None:
        self.subtitles = event.language_code or None
        log(f"{self.name}: subtitles {self.subtitles or 'off'}")
        self._on_change()

    async def ad_playing_changed(self, event) -> None:
        if not self.in_ad:
            skip = "skippable" if event.is_skippable else "not skippable"
            log(f"{self.name}: ad started ({event.duration:.0f}s, {skip})")
        self.in_ad = True

    async def ad_state_changed(self, event) -> None:
        self.in_ad = event.ad_state == State.Advertisement or self.in_ad


def _log_raw_events() -> None:
    """Diagnostics ("debug_events": true): log every Lounge event but noop."""
    original = YtLoungeApi._process_event

    async def process(self, event_type, args):
        if event_type != "noop":
            log(f"event {event_type} {json.dumps(args, default=str)[:300]}")
        await original(self, event_type, args)

    YtLoungeApi._process_event = process


COMMANDS = ("next", "previous", "play_pause", "subtitles", "forward")
FORWARD_SECONDS = 10


class YouTubeScreens:
    def __init__(
        self,
        cfg: dict[str, Any],
        state_of: StateLookup,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._state_of = state_of
        self._on_change = on_change
        self._chapters: dict[str, list[tuple[float, str]]] = {}
        self._auth: dict[str, Any] = json.loads(AUTH_FILE.read_text()) if AUTH_FILE.exists() else {}
        self.screens = {
            name: Screen(name, spec.get("match", []), self._prefetch, self._notify)
            for name, spec in cfg.get("screens", {}).items()
        }
        if cfg.get("debug_events"):
            _log_raw_events()
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="youtube").start()
        for screen in self.screens.values():
            asyncio.run_coroutine_threadsafe(self._run_screen(screen), self._loop)

    # -- queries / commands (any thread) ---------------------------------------
    def entities(self) -> set[str]:
        return {c["entity"] for s in self.screens.values() for c in s.match}

    def active(self, screen: Screen) -> bool:
        return any(matches(self._state_of, c) for c in screen.match)

    def any_active(self) -> bool:
        return any(self.active(s) for s in self.screens.values())

    def next_chapter(self) -> None:
        asyncio.run_coroutine_threadsafe(self._next_chapter(), self._loop).result(20)

    def command(self, name: str) -> None:
        """next | previous | play_pause | subtitles | forward on the screen that is playing."""
        if name not in COMMANDS:
            raise ValueError(f"unknown YouTube command {name!r}")
        asyncio.run_coroutine_threadsafe(self._command(name), self._loop).result(20)

    def probe(self, method: str, args: list[Any]) -> Any:
        """Diagnostics (probe.py): await api.<method>(*args) on the playing screen.

        "$video_id" in args stands for the playing video.
        """
        return asyncio.run_coroutine_threadsafe(self._probe(method, args), self._loop).result(20)

    def snapshot(self) -> dict[str, Any]:
        """Each screen's live Lounge state, for probe snapshots."""
        return {
            name: {
                "connected": s.api is not None,
                "video": s.video_id,
                "state": s.state.name if s.state is not None else None,
                "position": round(s.position_now()),
                "subtitles": s.subtitles,
                "in_ad": s.in_ad,
            }
            for name, s in self.screens.items()
        }

    def playing(self) -> bool:
        screen = self._pick()
        return screen is not None and screen.state == State.Playing

    def subtitles_on(self) -> bool:
        screen = self._pick()
        return screen is not None and screen.subtitles is not None

    def has_chapters(self) -> bool:
        """Whether the current video's chapters are loaded and non-empty."""
        screen = self._pick()
        return screen is not None and bool(self._chapters.get(screen.video_id))

    # -- internals (YouTube loop) -----------------------------------------------
    def _prefetch(self, video_id: str) -> None:
        if video_id not in self._chapters:
            self._loop.create_task(self._load_chapters(video_id))

    async def _load_chapters(self, video_id: str) -> list[tuple[float, str]]:
        if video_id not in self._chapters:
            chs = await self._loop.run_in_executor(None, yt_chapters.chapters, video_id)
            self._chapters[video_id] = chs
            log(f"{video_id}: {len(chs)} chapters")
            self._notify()  # keys that depend on chapters (next chapter / +10 s)
        return self._chapters[video_id]

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()

    def _pick(self) -> Screen | None:
        """The linked, connected screen with a video; one that is playing wins."""
        live = [s for s in self.screens.values() if s.api is not None and s.video_id]
        return next((s for s in live if s.state == State.Playing), live[0] if live else None)

    async def _command(self, name: str) -> None:
        screen = self._pick()
        if screen is None:
            log(f"{name}: no linked YouTube session is playing")
            return
        api = screen.api
        if name == "next":
            await api.next()
        elif name == "previous":
            await api.previous()
        elif name == "play_pause":
            await (api.pause() if screen.state == State.Playing else api.play())
        elif name == "forward":
            target = screen.position_now() + FORWARD_SECONDS
            await api.seek_to(target)
            screen._set(target, None)
        else:
            await self._toggle_subtitles(screen)
        log(f"{screen.name}: {name}")

    async def _probe(self, method: str, args: list[Any]) -> Any:
        screen = self._pick()
        if screen is None:
            raise RuntimeError("no linked YouTube session is playing")
        args = [screen.video_id if a == "$video_id" else a for a in args]
        return await getattr(screen.api, method)(*args)

    async def _toggle_subtitles(self, screen: Screen) -> None:
        video_id = screen.video_id
        if screen.subtitles is not None:
            lang = None
        else:
            page = await self._loop.run_in_executor(None, yt_chapters.fetch_page, video_id)
            lang = yt_chapters.pick_caption(yt_chapters.caption_languages(page))
            if lang is None:
                log(f"{screen.name}: {video_id}: no captions")
                return
        await screen.api.set_closed_captions(lang, video_id)
        screen.subtitles = lang  # optimistic: the TV may not echo the change
        self._notify()

    async def _next_chapter(self) -> None:
        screen = self._pick()
        if screen is None:
            log("next chapter: no linked YouTube session is playing")
            return
        position = screen.position_now()
        target = yt_chapters.next_start(await self._load_chapters(screen.video_id), position)
        if target is None:
            log(f"{screen.name}: no chapter after {position:.0f}s")
            return
        start, title = target
        await screen.api.seek_to(start)
        screen._set(start, None)
        log(f"{screen.name}: next chapter {start:.0f}s '{title}' (from {position:.0f}s)")

    def _save_auth(self) -> None:
        AUTH_FILE.write_text(json.dumps(self._auth))
        os.chmod(AUTH_FILE, 0o600)

    async def _pair(self, screen: Screen, code_file: Path) -> None:
        # The code file is consumed at once; success rewrites yt_lounge.json,
        # so a caller can tell the outcome from the file alone.
        code = "".join(ch for ch in code_file.read_text() if ch.isdigit())
        code_file.unlink(missing_ok=True)
        try:
            async with YtLoungeApi(DEVICE_NAME) as api:
                if not await api.pair(code) or not api.linked():
                    raise RuntimeError("code rejected (wrong or expired?)")
                self._auth[screen.name] = api.store_auth_state()
                self._save_auth()
                log(f"{screen.name}: linked to '{api.screen_name}'")
        except Exception as err:
            log(f"{screen.name}: linking failed: {type(err).__name__}: {err}")

    async def _run_screen(self, screen: Screen) -> None:
        code_file = CONFIG_DIR / f"yt_pair_{screen.name}"
        while True:
            try:
                if code_file.exists():
                    await self._pair(screen, code_file)
                if screen.name not in self._auth or not self.active(screen):
                    await asyncio.sleep(2)
                    continue
                await self._session(screen)
            except Exception as err:
                log(f"{screen.name}: {type(err).__name__}: {err}")
                await asyncio.sleep(10)

    async def _session(self, screen: Screen) -> None:
        async with YtLoungeApi(DEVICE_NAME, screen) as api:
            api.load_auth_state(self._auth[screen.name])
            if not api.linked() or not await api.connect():
                await api.refresh_auth()  # lounge tokens expire; the screen id doesn't
                self._auth[screen.name] = api.store_auth_state()
                self._save_auth()
                if not await api.connect():
                    raise RuntimeError("connect failed (YouTube app not open?)")
            screen.api = api
            log(f"{screen.name}: connected")
            self._notify()
            sub = asyncio.create_task(api.subscribe())
            try:
                while not sub.done() and self.active(screen):
                    await asyncio.sleep(1)
                if sub.done():
                    sub.result()  # surface errors
            finally:
                screen.api = None
                if not sub.done():
                    sub.cancel()
                    await api.disconnect()
                log(f"{screen.name}: disconnected")
                self._notify()
