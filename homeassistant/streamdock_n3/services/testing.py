"""Test fakes: `fake_context` builds a real ServiceContext wired to recorders."""

from __future__ import annotations

import json
import tempfile
import types
from pathlib import Path
from typing import Any

from services.context import ServiceContext
from services.metrics import Metrics

_ALL = object()


class FakeTime:
    def __init__(self, now: float) -> None:
        self.now = now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now


class _FakeToaster:
    def __init__(self, events: set[str], toasts: list) -> None:
        self.events = events
        self._toasts = toasts

    def show(self, message: str, channel: "str | None" = None) -> None:
        self._toasts.append((message, channel))


def _fake_opener(http: dict, urls: list):
    def opener(method: str, url: str, body: "bytes | None", headers: dict, timeout: float) -> "tuple[int, bytes]":
        urls.append(url)
        for pattern, reply in http.items():
            if pattern in url:
                if isinstance(reply, BaseException):
                    raise reply
                text = reply(url) if callable(reply) else reply
                return 200, text.encode()
        raise AssertionError(f"fake_context: no http fake for {url}")
    return opener


def fake_context(
    name: str = "test",
    config: "dict | None" = None,
    states: "dict | None" = None,
    http: "dict | None" = None,
    secrets: "dict | None" = None,
    now: float = 1_000_000.0,
    tmp: "Path | None" = None,
    *,
    toast_events: Any = _ALL,
    icons_root: "Path | None" = None,
    shared: "dict | None" = None,
) -> ServiceContext:
    """A real ServiceContext whose outside world is recorded in `ctx.fakes`."""
    tempdir = None
    if tmp is None:
        tempdir = tempfile.TemporaryDirectory()
        tmp = Path(tempdir.name)
    tmp = Path(tmp)
    secrets_path = tmp / "secrets.json"
    if secrets is not None:
        secrets_path.write_text(json.dumps(secrets))

    fakes = types.SimpleNamespace(
        calls=[], toasts=[], signals=0, context_signals=0, redraws=[], pages=[], urls=[],
        time=FakeTime(now), tempdir=tempdir)
    states = states or {}
    if toast_events is _ALL:
        toast_events = {name}
    toaster = None if toast_events is None else _FakeToaster(set(toast_events), fakes.toasts)

    def signal() -> None:
        fakes.signals += 1

    def context_signal() -> None:
        fakes.context_signals += 1

    ctx = ServiceContext(
        name, config or {},
        metrics=Metrics(log=lambda _msg: None, monotonic=fakes.time.monotonic, clock=fakes.time.clock),
        call=lambda domain, service, data: fakes.calls.append((domain, service, data)),
        call_response=lambda domain, service, data, *a, **k: fakes.calls.append((domain, service, data)),
        state_of=states.get,
        toaster=toaster,
        signal=signal,
        context_signal=context_signal,
        redraw_in=fakes.redraws.append,
        show_page=fakes.pages.append,
        shared=shared,
        data_root=tmp / "data",
        secrets_path=secrets_path,
        icons_root=icons_root,
        clock=fakes.time.clock,
        monotonic=fakes.time.monotonic,
        sleep=fakes.time.sleep,
        opener=_fake_opener(http or {}, fakes.urls),
    )
    ctx.fakes = fakes
    return ctx
