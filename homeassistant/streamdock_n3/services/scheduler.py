"""Background jobs, key-press threads, retries and hang reports.

Jobs run on a small worker pool: a heap of (due, seq, job) under one
Condition.  A job is popped when it starts and pushed back by `_run_job` when
it finishes, so it never runs twice at once.  A long-running job gets its own
thread (via `spawn`) that reruns it after it returns.  Each key action runs on
its own thread, capped per service.  Job and action code never runs, and
nothing is logged, while the lock is held.  `check_hangs` reports work that
has run longer than `hang_s`, once per run.  Stdlib only.
"""

from __future__ import annotations

import heapq
import itertools
import math
import threading
import time
from typing import Callable

from . import JobSpec
from .metrics import Metrics

MIN_DELAY_S = 1.0
STREAK_LOG_AT = 10


class _Run:
    """One piece of work in flight, tracked for hang reports."""
    __slots__ = ("service", "op", "started", "reported")

    def __init__(self, service: str, op: str, started: float) -> None:
        self.service, self.op, self.started, self.reported = service, op, started, False


class _Job:
    __slots__ = ("service", "spec", "streak", "run")

    def __init__(self, service: str, spec: JobSpec) -> None:
        self.service, self.spec, self.streak = service, spec, 0
        self.run: _Run | None = None


def _default_spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class Scheduler:
    def __init__(
        self, metrics: Metrics, *, workers: int = 3, retry_s: float = 60,
        max_retry_s: float = 1800, hang_s: float = 120, max_actions: int = 16,
        monotonic: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
        spawn: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._metrics = metrics
        self._workers = workers
        self._retry_s, self._max_retry_s, self._hang_s = retry_s, max_retry_s, hang_s
        self._max_actions = max_actions
        self._monotonic, self._log = monotonic, log
        self._spawn = spawn or _default_spawn
        self._cond = threading.Condition()          # guards everything below
        self._heap: list[tuple[float, int, _Job]] = []
        self._seq = itertools.count()
        self._long: list[_Job] = []
        self._runs: set[_Run] = set()               # in-flight work, for hang reports
        self._actions: dict[str, int] = {}          # running actions per service
        self._dropped: set[str] = set()             # disabled services: never rescheduled
        self._started = False
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- registration -------------------------------------------------------

    def add_job(self, service: str, spec: JobSpec) -> None:
        """Queue a job; raises ValueError/TypeError on a malformed spec (nothing is added)."""
        first_in = spec.first_in
        if isinstance(first_in, bool) or not isinstance(first_in, (int, float)) \
                or not 0 <= first_in < math.inf:
            raise ValueError(f"job {spec.name}: bad first_in {first_in!r}")
        job = _Job(service, spec)
        spawn_now = False
        with self._cond:
            if spec.long_running:
                self._long.append(job)
                spawn_now = self._started
            else:
                heapq.heappush(self._heap, (self._monotonic() + spec.first_in, next(self._seq), job))
                self._cond.notify()
        if spawn_now:
            self._spawn(lambda: self._long_loop(job))

    def drop(self, service: str) -> None:
        """Stop scheduling a disabled service's jobs; a run in progress is not rescheduled."""
        with self._cond:
            self._dropped.add(service)
            self._heap = [e for e in self._heap if e[2].service != service]
            heapq.heapify(self._heap)
            self._long = [j for j in self._long if j.service != service]

    def _is_dropped(self, service: str) -> bool:
        with self._cond:
            return service in self._dropped

    def next_due(self) -> float | None:
        with self._cond:
            return self._heap[0][0] if self._heap else None

    # -- jobs ---------------------------------------------------------------

    def _mark_running(self, job: _Job, now: float) -> None:
        """Caller holds the lock."""
        if not job.spec.long_running:           # long-running jobs block by design
            job.run = _Run(job.service, f"job:{job.spec.name}", now)
            self._runs.add(job.run)

    def _take(self, now: float) -> _Job | None:
        """Pop the earliest due job and mark it running.  Caller holds the lock."""
        if self._heap and self._heap[0][0] <= now:
            job = heapq.heappop(self._heap)[2]
            self._mark_running(job, now)
            return job
        return None

    def run_pending(self, now: float) -> int:
        ran = 0
        while True:
            with self._cond:
                job = self._take(now)
            if job is None:
                return ran
            self._run_job(job, now)
            ran += 1

    def _run_job(self, job: _Job, now: float | None = None) -> float | None:
        """Run a job that is marked running, record the outcome, reschedule it.

        The next run is due `delay` after the job finishes: `now` if given (tests
        with a frozen clock), else the clock read at finish time.  Returns the
        delay until the next run, or None for a one-shot.
        """
        spec, svc, op = job.spec, job.service, f"job:{job.spec.name}"
        failed: Exception | None = None
        delay: float | None = None
        try:
            with self._metrics.measure(svc, op, check_slow=not spec.long_running):
                value = spec.fn()
            if value is not None:
                delay = max(MIN_DELAY_S, float(value))
        except Exception as e:
            failed = e
        line = None
        with self._cond:
            if job.run is not None:
                self._runs.discard(job.run)
                job.run = None
            if failed is None:
                if job.streak:
                    line = f"services: {svc} {op} recovered"
                job.streak = 0
            else:
                job.streak += 1
                delay = min(self._retry_s * 2 ** (job.streak - 1), self._max_retry_s)
                what = f"{type(failed).__name__}: {failed}"
                if job.streak == 1:
                    line = f"services: {svc} {op} failed: {what}"
                elif job.streak == STREAK_LOG_AT:
                    line = f"services: {svc} {op} failing ({job.streak} in a row): {what}"
            if delay is not None and not spec.long_running and svc not in self._dropped:
                base = self._monotonic() if now is None else now
                heapq.heappush(self._heap, (base + delay, next(self._seq), job))
                self._cond.notify()
        if line:
            self._log(line)
        return delay

    def _worker(self) -> None:
        while True:
            with self._cond:
                while True:
                    if self._stopped.is_set():
                        return
                    now = self._monotonic()
                    job = self._take(now)
                    if job is not None:
                        break
                    self._cond.wait(self._heap[0][0] - now if self._heap else None)
            self._run_job(job)

    def _long_loop(self, job: _Job) -> None:
        if self._stopped.wait(job.spec.first_in) or self._is_dropped(job.service):
            return
        while not self._stopped.is_set():
            delay = self._run_job(job)
            if delay is None or self._stopped.wait(delay) or self._is_dropped(job.service):
                return

    # -- actions ------------------------------------------------------------

    def run_action(self, service: str, name: str, fn: Callable[[dict], None], args: dict) -> bool:
        op = f"action:{name}"
        with self._cond:
            full = self._actions.get(service, 0) >= self._max_actions
            if not full:
                self._actions[service] = self._actions.get(service, 0) + 1
                run = _Run(service, op, self._monotonic())
                self._runs.add(run)
        if full:
            self._log(f"services: {service} {op} dropped ({self._max_actions} running)")
            return False

        def body() -> None:
            try:
                with self._metrics.measure(service, op):
                    fn(args)
            except Exception as e:
                self._log(f"services: {service} {op} failed: {type(e).__name__}: {e}")
            finally:
                self._release(service, run)

        try:
            self._spawn(body)
        except BaseException:
            self._release(service, run)
            raise
        return True

    def _release(self, service: str, run: _Run) -> None:
        with self._cond:
            self._actions[service] -= 1
            self._runs.discard(run)

    # -- hangs --------------------------------------------------------------

    def check_hangs(self, now: float) -> None:
        lines = []
        with self._cond:
            for run in self._runs:
                if not run.reported and now - run.started > self._hang_s:
                    run.reported = True
                    lines.append(f"services: HUNG {run.service} {run.op} running {int(now - run.started)}s")
        for line in lines:
            self._log(line)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        with self._cond:
            if self._started:
                return
            self._started = True
            long_jobs = list(self._long)
        for _ in range(self._workers):
            t = threading.Thread(target=self._worker, name="services-worker", daemon=True)
            self._threads.append(t)
            t.start()
        for job in long_jobs:
            self._spawn(lambda job=job: self._long_loop(job))

    def stop(self) -> None:
        self._stopped.set()
        with self._cond:
            self._cond.notify_all()
        for t in self._threads:
            t.join(2.0)
