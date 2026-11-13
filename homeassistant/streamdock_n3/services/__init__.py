"""The service contract: decorators, the Service base class and the loader.

A service is a package `services/<name>/` exposing `SERVICE`, a `Service`
subclass whose `name` equals the package name.  Methods marked with `@job`,
`@action` or `@condition` are collected by `jobs()`, `actions()` and
`conditions()`; everything else is an optional hook with a no-op default.
`load()` imports the configured services in config order and skips (with one
log line) any that cannot be loaded.  Stdlib only.
"""

from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable

_NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class JobSpec:
    name: str
    fn: Callable[[], "float | None"]
    first_in: float
    long_running: bool


def job(name: str, *, first_in: float = 0.0, long_running: bool = False) -> Callable:
    """Mark a method as a scheduled job; it returns the delay (s) to its next run, or None."""
    def mark(fn: Callable) -> Callable:
        fn._service_job = (name, first_in, long_running)
        return fn
    return mark


def action(name: str) -> Callable:
    """Mark a method `(args: dict) -> None` as a key action."""
    def mark(fn: Callable) -> Callable:
        fn._service_action = name
        return fn
    return mark


def condition(name: str) -> Callable:
    """Mark a method `() -> bool` as a named condition."""
    def mark(fn: Callable) -> Callable:
        fn._service_condition = name
        return fn
    return mark


class Service:
    name: str = ""

    def start(self, ctx: Any) -> None:
        pass

    def entities(self) -> set[str]:
        return set()

    def vars(self) -> dict[str, str]:
        return {}

    def key(self, name: str, args: dict) -> "dict | None":
        return None

    def pages(self) -> dict[str, dict]:
        """Read once at startup, BEFORE `start()`: no ctx yet, so build pages from nothing else."""
        return {}

    def context(self, layout: str) -> "str | None":
        return None

    def on_state(self, changed: set[str]) -> None:
        pass

    def stop(self) -> None:
        """Not called by the app yet (the process is killed on restart): save state as you go."""
        pass

    def _marked(self, attr: str) -> list[tuple[Any, Callable]]:
        found = []
        for member, _ in inspect.getmembers(type(self), inspect.isfunction):
            marker = getattr(getattr(type(self), member), attr, None)
            if marker is not None:
                found.append((marker, getattr(self, member)))
        return found

    def jobs(self) -> list[JobSpec]:
        return [JobSpec(n, fn, first_in, long_running)
                for (n, first_in, long_running), fn in self._marked("_service_job")]

    def actions(self) -> dict[str, Callable[[dict], None]]:
        return {n: fn for n, fn in self._marked("_service_action")}

    def conditions(self) -> dict[str, Callable[[], bool]]:
        return {n: fn for n, fn in self._marked("_service_condition")}


def load(
    sections: dict,
    *,
    importer: Callable[[str], Any] = importlib.import_module,
    log: Callable[[str], None] = print,
) -> list[Service]:
    """Instantiate the services configured in `sections`, in config order."""
    loaded: list[Service] = []
    for name, cfg in sections.items():
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        if name == "host":                      # the pseudo-service holding the daemon's own metrics
            log("services: host disabled (reserved name)")
            continue
        try:
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError("bad service name")
            module = importer(f"services.{name}")
            factory = getattr(module, "SERVICE", None)
            if factory is None:
                raise AttributeError("no SERVICE")
            service = factory()
            if service.name != name:
                raise ValueError(f"name mismatch: {service.name!r}")
        except Exception as e:
            log(f"services: {name} disabled ({type(e).__name__}: {e})")
            continue
        loaded.append(service)
    return loaded
