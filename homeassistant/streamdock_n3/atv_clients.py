"""Which Apple TV apps are alive, from the media-remote (MRP) client messages.

The Apple TV tells an MRP session when an app registers with the now-playing
system (update_client, with its process id) and when that process ends
(remove_client); on every new session it lists all live clients. AtvClients
keeps that list. Services read it through ctx.shared("atv_clients") to tell a
cold app launch from a warm one (services/profiles).

"alive" is reliable. "gone" is reliable only for apps that register as soon as
they start: an app that registers at playback (Hulu) is "gone" while it sits
alive in its menus. "unknown" means there is no session to ask.

A removal can fall in a gap between sessions (the Apple TV asleep, the
connection down). An app that was alive before the gap and is not listed again
once the new session's list is complete (or is listed with another process
id) counts as removed at the new session's start.

Needs the app's own MRP session (the AirPlay pairing, see atv_touch.py).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable


def _print(msg: str) -> None:
    print(msg, flush=True)


class AtvClients:
    """Thread-safe; fed on the pyatv loop, read from action threads. No I/O."""

    def __init__(self, *, settle_s: float = 3.0, monotonic: Callable[[], float] = time.monotonic,
                 watch: Iterable[str] = (), log: Callable[[str], None] = _print) -> None:
        self._settle_s = float(settle_s)
        self._monotonic = monotonic
        self._watch = set(watch)
        self._log = log
        self._lock = threading.Lock()
        self._session_at: float | None = None   # None: no session (never, or lost)
        self._first_at: float | None = None     # first message of this session
        self._alive: dict[str, int] = {}
        self._seen: dict[str, float] = {}
        self._removed: dict[str, float] = {}
        self._unconfirmed: dict[str, int] = {}  # alive before the last gap, not listed since

    # -- events (pyatv loop) -------------------------------------------------
    def on_session(self) -> None:
        """A new MRP session: the Apple TV is about to list its live clients."""
        with self._lock:
            ended = self._confirm()  # the previous session's list, before it is lost
            self._unconfirmed.update(self._alive)
            self._session_at = self._monotonic()
            self._first_at = None
            self._alive = {}
        self._log_ended(ended)

    def on_message(self) -> None:
        """Any message of the session: the list is complete settle_s after the first."""
        with self._lock:
            self._touch()

    def on_update(self, bundle: str, pid: int) -> None:
        with self._lock:
            if self._session_at is None:
                return
            now = self._touch()
            if not pid and bundle in self._alive:
                pid = self._alive[bundle]  # a message without a process id: keep the known one
            changed = self._alive.get(bundle) != pid
            before = self._unconfirmed.pop(bundle, None)
            if before is not None and pid and before != pid:
                self._removed[bundle] = self._session_at  # the old process ended in the gap
            self._alive[bundle] = pid
            self._seen[bundle] = now
        if changed and bundle in self._watch:
            self._log(f"atv-clients: {bundle} alive pid {pid}")

    def on_remove(self, bundle: str, pid: int) -> None:
        with self._lock:
            if self._session_at is None:
                return
            now = self._touch()
            current = self._alive.get(bundle)
            if current is not None and current != pid:
                return  # an old process, reported after the app restarted
            self._alive.pop(bundle, None)
            self._unconfirmed.pop(bundle, None)
            self._removed[bundle] = now
        if bundle in self._watch:
            self._log(f"atv-clients: {bundle} gone")

    def on_lost(self) -> None:
        """The connection dropped: nothing is known until the next session."""
        with self._lock:
            ended = self._confirm()  # the previous session's list, before it is lost
            self._unconfirmed.update(self._alive)
            self._session_at = None
            self._first_at = None
            self._alive = {}
        self._log_ended(ended)

    def _settled(self) -> bool:
        return (self._session_at is not None and self._first_at is not None
                and self._monotonic() - self._first_at >= self._settle_s)

    def _confirm(self) -> list[str]:
        """Once the list is complete: apps alive before the gap and not listed
        again ended in it. Returns the watched ones, for the log. Under the lock."""
        if not self._unconfirmed or not self._settled():
            return []
        ended = [b for b in self._unconfirmed if b not in self._alive]
        for bundle in ended:
            self._removed[bundle] = self._session_at
        self._unconfirmed = {}
        return [b for b in ended if b in self._watch]

    def _log_ended(self, ended: list[str]) -> None:
        for bundle in ended:
            self._log(f"atv-clients: {bundle} gone (not listed after reconnect)")

    def _touch(self) -> float:
        now = self._monotonic()
        if self._session_at is not None and self._first_at is None:
            self._first_at = now
        return now

    # -- queries (any thread) ------------------------------------------------
    def status(self, bundle: str) -> str:
        """"alive", "gone" or "unknown"."""
        with self._lock:
            ended = self._confirm()
            if self._session_at is None:
                status = "unknown"
            elif bundle in self._alive:
                status = "alive"
            else:
                status = "gone" if self._settled() else "unknown"
        self._log_ended(ended)
        return status

    def pid(self, bundle: str) -> int | None:
        with self._lock:
            return self._alive.get(bundle)

    def removed_at(self, bundle: str) -> float | None:
        with self._lock:
            ended = self._confirm()
            removed = self._removed.get(bundle)
        self._log_ended(ended)
        return removed

    def seen_at(self, bundle: str) -> float | None:
        with self._lock:
            return self._seen.get(bundle)

    def session_at(self) -> float | None:
        with self._lock:
            return self._session_at


def _handlers(tracker: AtvClients) -> dict[int, Callable[[Any], Any]]:
    """MRP listeners by message type. They never raise into pyatv."""
    from pyatv.protocols.mrp import protobuf

    def client_of(message) -> tuple[str, int] | None:
        inner = protobuf.extract_inner(message)
        if not inner.HasField("client") or not inner.client.bundleIdentifier:
            return None
        return inner.client.bundleIdentifier, int(inner.client.processIdentifier)

    def guarded(fn: Callable[[Any], None]):
        async def handle(message) -> None:
            try:
                fn(message)
            except Exception as err:
                tracker._log(f"atv-clients: {type(err).__name__}: {err}")

        return handle

    def update(message) -> None:
        client = client_of(message)
        if client is None:
            tracker.on_message()
        else:
            tracker.on_update(*client)

    def remove(message) -> None:
        client = client_of(message)
        if client is None:
            tracker.on_message()
        else:
            tracker.on_remove(*client)

    return {
        protobuf.UPDATE_CLIENT_MESSAGE: guarded(update),
        protobuf.SET_NOW_PLAYING_CLIENT_MESSAGE: guarded(update),
        protobuf.REMOVE_CLIENT_MESSAGE: guarded(remove),
        protobuf.SET_STATE_MESSAGE: guarded(lambda _message: tracker.on_message()),
    }


def install(tracker: AtvClients) -> None:
    """Feed every MRP session created from now on into the tracker. Must run
    before the Apple TV connection is made (as ad_detect.install)."""
    from pyatv.protocols.mrp import protocol as mrp_protocol

    handlers = _handlers(tracker)
    original = mrp_protocol.MrpProtocol.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        tracker.on_session()
        for message_type, handler in handlers.items():
            self.listen_to(message_type, handler)

    mrp_protocol.MrpProtocol.__init__ = init
