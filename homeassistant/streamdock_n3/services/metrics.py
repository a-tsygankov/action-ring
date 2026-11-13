"""Per-service performance metrics: counters, latency histograms, slow-op warnings.

Every unit of work (job, key press, HTTP call, ...) is recorded under a
(service, op) pair, where op is "<kind>:<name>" (or a bare kind).  Stats are
kept for two windows: the whole process lifetime ("total") and the current
logging interval ("interval").  Slow operations log one line, at most once per
(service, op) per 60 s.  URLs are never logged.  Stdlib only.

CLI: `python services/metrics.py --by cpu --window interval < metrics.json`
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, ContextManager, Iterator

OP_KINDS = ("job", "action", "key", "cond", "vars", "context", "on_state", "http", "ha", "timed", "shared")

DEFAULT_SLOW_MS: dict[str, float] = {
    "job": 2000, "action": 3000, "http": 5000, "key": 20,
    "context": 5, "on_state": 20, "cond": 5, "vars": 5,
}

SLOW_LOG_INTERVAL_S = 60.0
_BUCKETS = 64
_BASE = 1e-5


class Histogram:
    """Log-scale latency histogram: bucket i has upper bound 1e-5 * 2 ** (i / 2)."""

    def __init__(self) -> None:
        self.buckets = [0] * _BUCKETS
        self.count = 0

    @staticmethod
    def _index(seconds: float) -> int:
        if seconds <= _BASE:
            return 0
        i = math.ceil(2 * math.log2(seconds / _BASE) - 1e-9)
        return min(max(i, 0), _BUCKETS - 1)

    def add(self, seconds: float) -> None:
        self.buckets[self._index(seconds)] += 1
        self.count += 1

    def merge(self, other: "Histogram") -> None:
        for i, n in enumerate(other.buckets):
            self.buckets[i] += n
        self.count += other.count

    def quantile(self, q: float) -> float:
        if not self.count:
            return 0.0
        target = max(1, math.ceil(q * self.count))
        seen = 0
        for i, n in enumerate(self.buckets):
            seen += n
            if seen >= target:
                return _BASE * 2 ** (i / 2)
        return _BASE * 2 ** ((_BUCKETS - 1) / 2)


class _Stats:
    def __init__(self) -> None:
        self.count = 0
        self.errors = 0
        self.wall_s = 0.0
        self.wall_max_s = 0.0
        self.cpu_s = 0.0
        self.bytes_in = 0
        self.bytes_out = 0
        self.status: dict[str, int] = {}
        self.hist = Histogram()

    def add(self, wall: float, cpu: float, error: bool, bytes_in: int, bytes_out: int, status: str | None) -> None:
        self.count += 1
        self.errors += 1 if error else 0
        self.wall_s += wall
        self.wall_max_s = max(self.wall_max_s, wall)
        self.cpu_s += cpu
        self.bytes_in += bytes_in
        self.bytes_out += bytes_out
        if status is not None:
            self.status[status] = self.status.get(status, 0) + 1
        self.hist.add(wall)

    def as_dict(self) -> dict:
        return {
            "count": self.count, "errors": self.errors,
            "wall_s": self.wall_s, "wall_max_s": self.wall_max_s,
            "p50_s": self.hist.quantile(0.5), "p95_s": self.hist.quantile(0.95),
            "cpu_s": self.cpu_s, "bytes_in": self.bytes_in, "bytes_out": self.bytes_out,
            "status": dict(self.status),
        }


class Measure:
    """Handle yielded by Metrics.measure; set fields inside the with-block."""

    def __init__(self) -> None:
        self.bytes_in = 0
        self.bytes_out = 0
        self.status: str | None = None
        self.over_budget = False


def _kind(op: str) -> str:
    return op.split(":", 1)[0]


def _wall_fmt(s: float) -> str:
    return f"{s:.1f}s" if s >= 1 else f"{s * 1000:.0f}ms"


def _cpu_fmt(s: float) -> str:
    return f"{s:.2f}s" if s >= 0.01 else f"{s * 1000:.0f}ms"


class Metrics:
    def __init__(
        self,
        slow_ms: dict[str, float] | None = None,
        log_every_s: float = 600,
        log: Callable[[str], None] = print,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.slow_ms = {**DEFAULT_SLOW_MS, **(slow_ms or {})}
        self.log_every_s = log_every_s
        self._log = log
        self._monotonic = monotonic
        self._clock = clock
        self._lock = threading.Lock()
        # (service, op) -> (total, interval)
        self._ops: dict[tuple[str, str], tuple[_Stats, _Stats]] = {}
        self._last_slow: dict[tuple[str, str], float] = {}
        self._interval_start = monotonic()
        self._app_prev = (0.0, self._interval_start)

    def record(
        self, service: str, op: str, wall: float, cpu: float, *,
        error: bool = False, bytes_in: int = 0, bytes_out: int = 0,
        status: str | None = None, check_slow: bool = True,
    ) -> bool:
        budget_ms = self.slow_ms.get(_kind(op)) if check_slow else None
        over = budget_ms is not None and wall * 1000 > budget_ms
        line = None
        with self._lock:
            pair = self._ops.get((service, op))
            if pair is None:
                pair = self._ops[(service, op)] = (_Stats(), _Stats())
            for st in pair:
                st.add(wall, cpu, error, bytes_in, bytes_out, status)
            if over:
                now = self._monotonic()
                last = self._last_slow.get((service, op))
                if last is None or now - last >= SLOW_LOG_INTERVAL_S:
                    self._last_slow[(service, op)] = now
                    line = (f"metrics: SLOW {service} {op} {_wall_fmt(wall)} wall, "
                            f"{_cpu_fmt(cpu)} cpu (budget {_wall_fmt(budget_ms / 1000)})")
        if line:
            self._log(line)
        return over

    def measure(self, service: str, op: str, *, check_slow: bool = True) -> ContextManager[Measure]:
        return self._measure(service, op, check_slow)

    @contextlib.contextmanager
    def _measure(self, service: str, op: str, check_slow: bool) -> Iterator[Measure]:
        m = Measure()
        w0, c0 = time.perf_counter(), time.thread_time()
        error = True
        try:
            yield m
            error = False
        finally:
            m.over_budget = self.record(
                service, op, time.perf_counter() - w0, time.thread_time() - c0,
                error=error, bytes_in=m.bytes_in, bytes_out=m.bytes_out,
                status=m.status, check_slow=check_slow)

    def roll(self) -> None:
        """Start a new interval: reset the interval stats, keep the totals."""
        with self._lock:
            for key in self._ops:
                self._ops[key] = (self._ops[key][0], _Stats())
            self._interval_start = self._monotonic()

    def snapshot(self, process: dict, store_bytes: dict[str, int]) -> dict:
        with self._lock:
            services: dict[str, dict] = {
                svc: {"store_bytes": n, "ops": {}} for svc, n in store_bytes.items()}
            for (svc, op), (total, interval) in self._ops.items():
                entry = services.setdefault(svc, {"store_bytes": 0, "ops": {}})
                entry["ops"][op] = {"total": total.as_dict(), "interval": interval.as_dict()}
            return {
                "at": self._clock(),
                "interval_s": self._monotonic() - self._interval_start,
                "process": dict(process),
                "services": services,
            }

    def summary_lines(self, process: dict) -> list[str]:
        with self._lock:
            now = self._monotonic()
            interval_s = now - self._interval_start
            per: dict[str, dict] = {}
            for (svc, op), (_, iv) in self._ops.items():
                if not iv.count:
                    continue
                s = per.setdefault(svc, {
                    "cpu": 0.0, "wall": 0.0, "jobs": 0, "err": 0, "http": 0, "http_bytes": 0,
                    "http_hist": Histogram(), "keys": 0, "key_hist": Histogram()})
                s["cpu"] += iv.cpu_s
                s["wall"] += iv.wall_s
                s["err"] += iv.errors
                kind = _kind(op)
                if kind == "job":
                    s["jobs"] += iv.count
                elif kind == "http":
                    s["http"] += iv.count
                    s["http_bytes"] += iv.bytes_in
                    s["http_hist"].merge(iv.hist)
                elif kind == "key":
                    s["keys"] += iv.count
                    s["key_hist"].merge(iv.hist)
            lines = []
            for svc, s in sorted(per.items(), key=lambda kv: -kv[1]["cpu"]):
                pct = s["cpu"] / interval_s * 100 if interval_s > 0 else 0.0
                parts = [f"metrics: {svc} cpu {s['cpu']:.2f}s ({pct:.2f}%) wall {s['wall']:.1f}s"]
                if s["jobs"] or s["err"]:
                    parts.append(f"{s['jobs']} jobs, {s['err']} err")
                if s["http"]:
                    parts.append(f"http {s['http']} req {s['http_bytes'] / 1e6:.1f} MB "
                                 f"p95 {s['http_hist'].quantile(0.95):.1f}s")
                if s["keys"]:
                    parts.append(f"keys {s['keys']} p95 {s['key_hist'].quantile(0.95) * 1000:.0f}ms")
                lines.append(" · ".join(parts))
            if process:
                prev_cpu, prev_at = self._app_prev
                cpu = process.get("cpu_s", 0.0)
                dt = now - prev_at
                pct = (cpu - prev_cpu) / dt * 100 if dt > 0 else 0.0
                self._app_prev = (cpu, now)
                lines.append(f"metrics: app cpu {pct:.1f}% rss {process.get('rss_mb', 0.0):.0f} MB "
                             f"threads {process.get('threads', 0)}")
            return lines

    def write_snapshot(self, path: Path, snapshot: dict) -> None:
        """Atomically write the snapshot as JSON; failures are logged, never raised."""
        tmp = Path(str(path) + ".tmp")
        try:
            tmp.write_text(json.dumps(snapshot))
            os.replace(tmp, path)
        except Exception as e:  # never let metrics break the app
            self._log(f"metrics: snapshot not written ({e})")


def process_stats(proc: Path = Path("/proc/self")) -> dict:
    """CPU seconds, RSS and thread count of the process; {} when /proc is unavailable."""
    try:
        stat = (proc / "stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()  # fields[0] is state (field 3)
        cpu_s = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        rss_kb, threads = 0.0, 0
        for line in (proc / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_kb = float(line.split()[1])
            elif line.startswith("Threads:"):
                threads = int(line.split()[1])
        return {"cpu_s": cpu_s, "rss_mb": rss_kb / 1024, "threads": threads}
    except (OSError, ValueError, IndexError, AttributeError):
        return {}


def format_table(snapshot: dict, by: str = "cpu", window: str = "interval") -> str:
    key = {"cpu": "cpu_s", "wall": "wall_s", "http": "bytes_in", "errors": "errors"}[by]
    rows = []
    for svc, entry in snapshot.get("services", {}).items():
        for op, w in entry.get("ops", {}).items():
            st = w[window]
            if st["count"]:
                rows.append((st[key], svc, op, st))
    rows.sort(key=lambda r: -r[0])
    out = [f"{'service':<12} {'op':<24} {'count':>6} {'errors':>6} {'cpu_s':>8} "
           f"{'wall_s':>8} {'p95_s':>7} {'max_s':>7} {'http_bytes':>10}"]
    for _, svc, op, st in rows:
        out.append(f"{svc:<12} {op:<24} {st['count']:>6} {st['errors']:>6} {st['cpu_s']:>8.3f} "
                   f"{st['wall_s']:>8.3f} {st['p95_s']:>7.3f} {st['wall_max_s']:>7.3f} {st['bytes_in']:>10}")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Print a metrics snapshot (JSON on stdin) as a table.")
    ap.add_argument("--by", choices=["cpu", "wall", "http", "errors"], default="cpu")
    ap.add_argument("--window", choices=["interval", "total"], default="interval")
    a = ap.parse_args()
    print(format_table(json.load(sys.stdin), by=a.by, window=a.window))
