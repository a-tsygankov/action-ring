"""Per-service context: the only handle a service gets on the outside world.

Wraps HTTP (timed, URL-free logging), a small file/SQLite store, secrets, Home
Assistant calls, toasts, redraw signals, icons and shared helpers, all
attributed to one service in the metrics.  Stdlib only.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, ContextManager

import media_actions
from services.metrics import Metrics

Opener = Callable[[str, str, "bytes | None", dict, float], "tuple[int, bytes]"]

DEFAULT_TIMEOUT = 15.0
DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _urlopen(method: str, url: str, body: "bytes | None", headers: dict, timeout: float) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _status_label(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{exc.code // 100}xx"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
        return "timeout"
    return "error"


class Http:
    """HTTP client for one service; records `http:<op>` and never logs URLs."""

    def __init__(self, service: str, metrics: Metrics, opener: "Opener | None" = None) -> None:
        self._service = service
        self._metrics = metrics
        self._opener = opener or _urlopen

    def _request(self, method: str, url: str, op: str, body: "bytes | None",
                 headers: "dict | None", timeout: float) -> str:
        merged = {**DEFAULT_HEADERS, **(headers or {})}
        with self._metrics.measure(self._service, f"http:{op}") as m:
            m.bytes_out = len(body or b"")
            try:
                status, data = self._opener(method, url, body, merged, timeout)
            except BaseException as e:
                m.status = _status_label(e)
                raise
            m.bytes_in = len(data)
            m.status = f"{status // 100}xx"
        return data.decode("utf-8", errors="replace")

    def get(self, url: str, *, op: str, headers: "dict | None" = None, timeout: float = DEFAULT_TIMEOUT) -> str:
        return self._request("GET", url, op, None, headers, timeout)

    def get_json(self, url: str, *, op: str, headers: "dict | None" = None, timeout: float = DEFAULT_TIMEOUT) -> Any:
        return json.loads(self.get(url, op=op, headers=headers, timeout=timeout))

    def post(self, url: str, *, op: str, data: "bytes | dict | None" = None,
             headers: "dict | None" = None, timeout: float = DEFAULT_TIMEOUT) -> str:
        hdrs = dict(headers or {})
        if isinstance(data, dict):
            body: "bytes | None" = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
        else:
            body = data
        return self._request("POST", url, op, body, hdrs, timeout)


class Store:
    """JSON values and SQLite databases under one directory (created on first write)."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def load(self, key: str, default: Any = None) -> Any:
        try:
            return json.loads((self._root / f"{key}.json").read_text())
        except (OSError, ValueError):
            return default

    def save(self, key: str, value: Any) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{key}.json"
        tmp = self._root / f"{key}.json.tmp"
        tmp.write_text(json.dumps(value))
        os.replace(tmp, path)

    def sqlite(self, name: str) -> sqlite3.Connection:
        self._root.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._root / f"{name}.sqlite", check_same_thread=False)

    def size_bytes(self) -> int:
        total = 0
        try:
            for p in self._root.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        except OSError:
            pass
        return total


class _Media:
    """Media helpers bound to one service's config, call and state lookup."""

    def __init__(self, ctx: "ServiceContext") -> None:
        self._ctx = ctx

    def ensure_on(self) -> bool:
        c = self._ctx
        return media_actions.ensure_on(c.call, c.state, c.config, sleep=c.sleep, clock=c.monotonic)

    def select_atv_input(self) -> None:
        c = self._ctx
        media_actions.select_atv_input(c.call, c.state, c.config)

    def launch(self, device: str, app: str) -> bool:
        c = self._ctx
        return media_actions.launch(c.call, c.state, c.config, device, app, sleep=c.sleep, clock=c.monotonic)


class _SharedProxy:
    """Wraps a shared helper so each method call is timed under `shared:<name>`."""

    def __init__(self, ctx: "ServiceContext", name: str, target: Any) -> None:
        self._ctx = ctx
        self._name = name
        self._target = target

    def __getattr__(self, attr: str) -> Any:
        value = getattr(self._target, attr)
        if not callable(value):
            return value

        def timed_call(*args: Any, **kwargs: Any) -> Any:
            with self._ctx._metrics.measure(self._ctx._name, f"shared:{self._name}"):
                return value(*args, **kwargs)
        return timed_call


class ServiceContext:
    def __init__(
        self,
        name: str,
        config: dict,
        *,
        metrics: Metrics,
        call: Callable[[str, str, dict], Any],
        call_response: Callable[..., Any],
        state_of: Callable[[str], "dict | None"],
        toaster: Any,
        signal: Callable[[], None],
        context_signal: Callable[[], None],
        redraw_in: Callable[[float], None],
        show_page: Callable[[str], None],
        shared: "dict | None" = None,
        data_root: Path = Path("/data/services"),
        secrets_path: Path = Path("/config/secrets.json"),
        icons_root: "Path | None" = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        opener: "Opener | None" = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self._name = name
        self.config = config
        self._metrics = metrics
        self._call = call
        self._call_response = call_response
        self._state_of = state_of
        self._toaster = toaster
        self._signal = signal
        self._context_signal = context_signal
        self._redraw_in = redraw_in
        self._show_page = show_page
        self._shared = shared or {}
        self._secrets_path = Path(secrets_path)
        self._icons_root = Path(icons_root) if icons_root is not None else Path(__file__).resolve().parent
        self._log = log
        self._toast_note_logged = False
        self.clock = clock
        self.monotonic = monotonic
        self.sleep = sleep
        self.http = Http(name, metrics, opener)
        self.store = Store(Path(data_root) / name)
        self.media = _Media(self)

    def secret(self, name: str) -> Any:
        try:
            data = json.loads(self._secrets_path.read_text())
            return (data.get(self._name) or {}).get(name)
        except (OSError, ValueError, AttributeError):
            return None

    def state(self, entity: str) -> "dict | None":
        return self._state_of(entity)

    def call(self, domain: str, service: str, data: dict) -> Any:
        with self._metrics.measure(self._name, f"ha:{domain}.{service}"):
            return self._call(domain, service, data)

    def call_response(self, domain: str, service: str, data: dict, *args: Any, **kwargs: Any) -> Any:
        with self._metrics.measure(self._name, f"ha:{domain}.{service}"):
            return self._call_response(domain, service, data, *args, **kwargs)

    def toast(self, message: str, channel: "str | None" = None) -> None:
        if self._toaster is None:
            note = "toast: no toast config; not shown"
        elif self._name not in self._toaster.events:
            note = f"toast: {self._name} not in toast.events; not shown"
        else:
            self._toaster.show(message, channel)
            return
        if not self._toast_note_logged:
            self._toast_note_logged = True
            self._log(note)

    def changed(self) -> None:
        self._signal()

    def redraw_in(self, seconds: float) -> None:
        self._redraw_in(seconds)

    def context_changed(self) -> None:
        self._context_signal()

    def show_page(self, page: str) -> None:
        self._show_page(f"{self._name}.{page}")

    def log(self, msg: str) -> None:
        self._log(f"{self._name}: {msg}")

    def timed(self, op: str) -> ContextManager:
        return self._metrics.measure(self._name, f"timed:{op}")

    def shared(self, name: str) -> Any:
        target = self._shared.get(name)
        return None if target is None else _SharedProxy(self, name, target)

    def icon(self, name: str) -> str:
        path = self._icons_root / self._name / "icons" / f"{name}.png"
        return str(path) if path.exists() else name
