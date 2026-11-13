"""Service host: loads services, gives each a context and guards the render path.

`ServiceHost` is what the daemon talks to.  It namespaces service pages,
conditions and vars, merges key presets with slot overrides, runs actions and
jobs through the scheduler, and trips a per-service circuit breaker when
render-path calls (key, conditions, vars, context, on_state) keep raising or
overrunning their budget: 3 strikes within 60 s switch that service's render
calls off for 300 s.  Service code is never called, and nothing is logged,
while the host lock is held.  Stdlib only.
"""

from __future__ import annotations

import importlib
import math
import threading
import time
from pathlib import Path
from typing import Callable, ContextManager

from . import JobSpec, Service, load
from .context import ServiceContext
from .metrics import Metrics, process_stats
from .scheduler import Scheduler

STRIKES = 3
STRIKE_WINDOW_S = 60.0
BREAKER_OPEN_S = 300.0


class ServiceUnavailable(Exception):
    """A requested service key cannot be produced right now."""


def _checked(value, ok: Callable[[object], bool], what: str):
    """Raise TypeError (so the guarded call counts a strike) on a malformed return."""
    if not ok(value):
        raise TypeError(f"returned {type(value).__name__}, expected {what}")
    return value


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _metrics_kw(mcfg: dict, log: Callable[[str], None]) -> dict:
    """Metrics() arguments from the "metrics" config; bad values fall back, one line each."""
    kw: dict = {}
    if "log_every_s" in mcfg:
        every = mcfg["log_every_s"]
        if _is_number(every) and every > 0:
            kw["log_every_s"] = every
        else:
            log(f"services: metrics.log_every_s {every!r} is not a positive number; using 600")
    slow = mcfg.get("slow_ms")
    if slow is not None and not isinstance(slow, dict):
        log("services: metrics.slow_ms is not an object; ignored")
    elif slow:
        bad = [k for k, v in slow.items() if not _is_number(v)]
        if bad:
            log(f"services: metrics.slow_ms entries dropped (not numbers): {', '.join(map(str, bad))}")
        kw["slow_ms"] = {k: v for k, v in slow.items() if k not in bad}
    return kw


class ServiceHost:
    def __init__(self, services: list[Service], contexts: dict[str, ServiceContext],
                 metrics: Metrics, scheduler: Scheduler, *, signal: Callable[[], None],
                 redraws: list[float], lock: threading.Lock,
                 monotonic: Callable[[], float], snapshot_path: Path,
                 log: Callable[[str], None]) -> None:
        self._services = {s.name: s for s in services}
        self._ctx = contexts
        self._metrics = metrics
        self._sched = scheduler
        self._signal = signal
        self._redraws, self._lock = redraws, lock    # the one lock guards all mutable state
        self._monotonic = monotonic
        self._snapshot_path = snapshot_path
        self._log = log
        self._strikes: dict[str, list[float]] = {}
        self._open_until: dict[str, float] = {}
        self._logged: set[str] = set()
        self._log_every_s = metrics.log_every_s
        self._actions: dict[str, dict[str, Callable]] = {}

    @classmethod
    def load(cls, config: dict, *, call, call_response, state_of, toaster,
             signal: Callable[[], None], context_signal: Callable[[], None],
             show_page: Callable[[str], bool], shared: dict | None = None,
             importer=importlib.import_module,
             monotonic: Callable[[], float] = time.monotonic,
             clock: Callable[[], float] = time.time,
             data_root: Path = Path("/data/services"),
             secrets_path: Path = Path("/config/secrets.json"),
             snapshot_path: Path = Path("/config/metrics.json"),
             spawn=None, log: Callable[[str], None] = print) -> "ServiceHost":
        sections = config.get("services") or {}
        if not isinstance(sections, dict):
            log('services: "services" config is not an object; ignored')
            sections = {}
        mcfg = config.get("metrics") or {}
        metrics = Metrics(**_metrics_kw(mcfg, log), log=log, monotonic=monotonic, clock=clock)
        sched = Scheduler(metrics, monotonic=monotonic, log=log, spawn=spawn)
        services = load(sections, importer=importer, log=log)
        redraws: list[float] = []
        lock = threading.Lock()

        def redraw_in(seconds: float) -> None:
            with lock:
                redraws.append(monotonic() + seconds)

        contexts = {
            s.name: ServiceContext(
                s.name, sections[s.name], metrics=metrics, call=call,
                call_response=call_response, state_of=state_of, toaster=toaster,
                signal=signal, context_signal=context_signal,
                redraw_in=redraw_in, show_page=show_page, shared=shared,
                data_root=data_root, secrets_path=secrets_path, clock=clock,
                monotonic=monotonic, log=log)
            for s in services}
        return cls(services, contexts, metrics, sched, signal=signal, redraws=redraws,
                   lock=lock, monotonic=monotonic, snapshot_path=Path(snapshot_path), log=log)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        for name, svc in list(self._services.items()):
            try:
                svc.start(self._ctx[name])
                self._actions[name] = svc.actions()
                for spec in svc.jobs():
                    self._sched.add_job(name, spec)
            except Exception as e:
                self._disable(name, e)
        self._sched.add_job("host", JobSpec("metrics", self._metrics_job, self._log_every_s, False))
        self._sched.start()

    def _disable(self, name: str, err: Exception) -> None:
        """Drop a service whose load-time hook failed: one log line, no more calls or jobs.

        `_services` is replaced, not mutated, so a thread iterating it is unaffected.
        """
        self._log(f"services: {name} disabled ({type(err).__name__}: {err})")
        self._services = {n: s for n, s in self._services.items() if n != name}
        self._actions.pop(name, None)
        self._sched.drop(name)

    def stop(self) -> None:
        self._sched.stop()
        for name, svc in self._services.items():
            try:
                svc.stop()
            except Exception as e:
                self._log(f"services: {name} stop failed: {type(e).__name__}: {e}")

    def _metrics_job(self) -> float:
        proc = process_stats()
        for line in self._metrics.summary_lines(proc):
            self._log(line)
        sizes = {n: self._ctx[n].store.size_bytes() for n in self._services}
        self._metrics.write_snapshot(self._snapshot_path, self._metrics.snapshot(proc, sizes))
        self._metrics.roll()
        return self._log_every_s

    # -- breaker --------------------------------------------------------------

    def _is_open(self, svc: str) -> bool:
        with self._lock:
            return svc in self._open_until

    def _strike(self, svc: str, reason: str) -> None:
        now = self._monotonic()
        trip = False
        with self._lock:
            hits = [t for t in self._strikes.get(svc, []) if now - t < STRIKE_WINDOW_S]
            hits.append(now)
            if len(hits) >= STRIKES and svc not in self._open_until:
                self._open_until[svc] = now + BREAKER_OPEN_S
                hits, trip = [], True
            self._strikes[svc] = hits
        if trip:
            self._log(f"services: {svc} render calls off for 5 min ({reason})")

    def _render(self, svc: str, op: str, fn: Callable):
        """Run one render-path call: measured, breaker-checked, strikes counted."""
        if self._is_open(svc):
            raise ServiceUnavailable("render calls off")
        try:
            with self._metrics.measure(svc, op) as m:
                value = fn()
        except Exception as e:
            self._strike(svc, f"{op} {type(e).__name__}: {e}")
            raise
        if m.over_budget:
            self._strike(svc, f"{op} over budget")
        return value

    def _log_once(self, line: str) -> None:
        with self._lock:
            new = line not in self._logged
            self._logged.add(line)
        if new:
            self._log(line)

    # -- queries --------------------------------------------------------------

    def names(self) -> list[str]:
        return list(self._services)

    # entities(), pages() and conditions() are read once at startup (pages()
    # before start()); a service whose hook raises is disabled, not the app.

    def entities(self) -> set[str]:
        out: set[str] = set()
        for n, s in list(self._services.items()):
            try:
                out |= set(s.entities())
            except Exception as e:
                self._disable(n, e)
        return out

    def pages(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for n, s in list(self._services.items()):
            try:
                out.update({f"{n}.{p}": page for p, page in s.pages().items()})
            except Exception as e:
                self._disable(n, e)
        return out

    def conditions(self) -> dict[str, Callable[[], bool]]:
        out: dict[str, Callable[[], bool]] = {}
        for n, s in list(self._services.items()):
            try:
                out.update({f"{n}.{c}": self._cond(n, c, fn) for c, fn in s.conditions().items()})
            except Exception as e:
                self._disable(n, e)
        return out

    def _cond(self, svc: str, cname: str, fn: Callable[[], bool]) -> Callable[[], bool]:
        def wrapper() -> bool:
            try:
                return bool(self._render(svc, f"cond:{cname}", fn))
            except Exception:
                return False
        return wrapper

    def vars(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for n, s in self._services.items():
            try:
                got = self._render(n, "vars", lambda: _checked(
                    s.vars(), lambda v: isinstance(v, dict) and all(isinstance(k, str) for k in v),
                    "dict[str, str]"))
            except Exception:
                continue
            for k, v in got.items():
                if k.startswith(f"{n}_"):
                    out[k] = v
                else:
                    self._log_once(f"services: {n} var '{k}' dropped (name must start with {n}_)")
        return out

    def key_spec(self, slot: dict) -> dict | None:
        sk = slot["service_key"]
        svc_name, _, key = sk.partition(".")
        overrides = {k: v for k, v in slot.items() if k not in ("service_key", "args")}
        try:
            svc = self._services.get(svc_name)
            if svc is None or not key:
                raise ServiceUnavailable("unknown service or key")
            spec = self._render(svc_name, f"key:{key}", lambda: _checked(
                svc.key(key, slot.get("args", {})), lambda v: v is None or isinstance(v, dict), "dict or None"))
        except Exception as e:
            reason = str(e) if isinstance(e, ServiceUnavailable) else f"{type(e).__name__}: {e}"
            self._log_once(f"services: key {sk}: {reason}")
            if isinstance(e, ServiceUnavailable):
                raise
            raise ServiceUnavailable(reason) from e
        return None if spec is None else {**spec, **overrides}

    def context(self, layout: str) -> str:
        for n, s in self._services.items():
            try:
                got = self._render(n, "context", lambda: _checked(
                    s.context(layout), lambda v: v is None or isinstance(v, str), "str or None"))
            except Exception:
                continue
            if got is not None:
                return got
        return layout

    def on_state(self, changed: set[str]) -> None:
        for n, s in self._services.items():
            try:
                hit = set(s.entities()) & changed
                if hit:
                    self._render(n, "on_state", lambda: s.on_state(hit))
            except Exception:
                continue

    # -- actions, time --------------------------------------------------------

    def run_action(self, service: str, do: str, args: dict | None = None) -> None:
        fn = self._actions.get(service, {}).get(do) if service in self._services else None
        if fn is None:
            self._log(f"services: ignoring {service}.{do}")
            return
        self._sched.run_action(service, do, fn, args or {})

    def tick(self, now: float) -> None:
        reopened = []
        with self._lock:
            due = [t for t in self._redraws if t <= now]
            if due:
                self._redraws[:] = [t for t in self._redraws if t > now]
            for svc, until in list(self._open_until.items()):
                if now >= until:
                    del self._open_until[svc]
                    reopened.append(svc)
        if due:
            self._signal()
        self._sched.check_hangs(now)
        for svc in reopened:
            self._log(f"services: {svc} render calls back on")

    def measure_host(self, op: str) -> ContextManager:
        return self._metrics.measure("host", op, check_slow=False)
