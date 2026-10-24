"""Tab state machine for the dock's key pages.

The dock shows either a context page ("context:<layout>", chosen from what the
TV/room is doing) or a temporary page opened by a round button (a cycle of
pages that closes on its own after a timeout). All time is passed in, so this
is a pure, deterministic module: no clocks, no threads of its own, no device
or Home Assistant. Callers (SDK input thread, a ticker, the HA state thread)
share one engine; every public method takes the same internal lock.
"""

from __future__ import annotations

import threading

DEFAULT_LAYOUT = "idle"


class TabEngine:
    def __init__(self, pages: dict[str, dict], rounds: dict[int, list[str]],
                 open_s: float = 10.0, after_press_s: float = 5.0,
                 debounce_s: float = 1.0) -> None:
        self._pages = pages
        self._rounds = rounds
        self._open_s = open_s
        self._after_press_s = after_press_s
        self._debounce_s = debounce_s
        self._lock = threading.Lock()
        self._layout = DEFAULT_LAYOUT          # context layout currently shown
        self._layout_seen = False              # first set_context is adopted at once
        self._pending: str | None = None       # layout waiting out the debounce
        self._pending_since = 0.0
        self._temp: str | None = None          # open temporary page, if any
        self._deadline = 0.0                   # when the temporary page closes
        self.version = 0                       # bumps whenever `visible` changes

    # -- internals (call with the lock held) --------------------------------

    def _visible(self) -> str:
        return self._temp if self._temp is not None else f"context:{self._layout}"

    def _bump_if_changed(self, before: str) -> None:
        if self._visible() != before:
            self.version += 1

    def _close(self) -> None:
        self._temp = None

    # -- public API ----------------------------------------------------------

    @property
    def visible(self) -> str:
        with self._lock:
            return self._visible()

    def keys(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._pages.get(self._visible(), {}).get("keys", {}))

    def round_press(self, button: int, now: float) -> bool:
        """False if `button` is not a tab button; otherwise open/advance its cycle."""
        with self._lock:
            cycle = self._rounds.get(button)
            if not cycle:
                return False
            before = self._visible()
            if before in cycle:
                self._temp = cycle[(cycle.index(before) + 1) % len(cycle)]
            else:
                self._temp = cycle[0]
            self._deadline = now + self._open_s
            self._bump_if_changed(before)
            return True

    def key_press(self, key: int, now: float) -> dict | None:
        """Spec of `key` on the visible page (None if undefined).

        On a temporary page the press also closes it (close_on_press, defined
        keys only) or restarts the shortened after-press timer.
        """
        with self._lock:
            page_name = self._visible()
            page = self._pages.get(page_name, {})
            spec = page.get("keys", {}).get(str(key))
            if self._temp is not None:
                before = page_name
                if spec is not None and page.get("close_on_press"):
                    self._close()
                else:
                    self._deadline = now + self._after_press_s
                self._bump_if_changed(before)
            return spec

    def set_context(self, layout: str, now: float) -> None:
        """Report the current context layout; a change is adopted by tick()."""
        with self._lock:
            before = self._visible()
            if not self._layout_seen:
                self._layout_seen = True
                self._layout = layout
                self._pending = None
            elif layout == self._layout:
                self._pending = None
            elif layout != self._pending:
                self._pending = layout
                self._pending_since = now
            self._bump_if_changed(before)

    def tick(self, now: float) -> None:
        with self._lock:
            before = self._visible()
            if self._temp is not None and now > self._deadline:
                self._close()
            if (self._pending is not None
                    and now - self._pending_since >= self._debounce_s):
                self._layout = self._pending
                self._pending = None
            self._bump_if_changed(before)
