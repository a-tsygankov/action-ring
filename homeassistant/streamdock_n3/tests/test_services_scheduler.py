import math
import threading
import time

from services import JobSpec
from services.metrics import Metrics
from services.scheduler import Scheduler


def sched(**kw):
    t = [0.0]
    kw.setdefault("spawn", lambda fn: fn())
    return Scheduler(Metrics(), monotonic=lambda: t[0], **kw)


def boom():
    raise RuntimeError("boom")


def test_job_reschedules_by_return_value_with_floor():
    s, ran = sched(), []
    s.add_job("a", JobSpec("p", lambda: ran.append(1) or 0.2, first_in=0, long_running=False))
    assert s.run_pending(0.0) == 1 and s.run_pending(0.5) == 0 and s.run_pending(1.0) == 1


def test_first_in_delays_first_run():
    s = sched(); s.add_job("a", JobSpec("p", lambda: 10.0, 5, False))
    assert s.run_pending(4.9) == 0 and s.run_pending(5.0) == 1


def test_none_means_one_shot():
    s = sched(); s.add_job("a", JobSpec("p", lambda: None, 0, False))
    assert s.run_pending(0) == 1 and s.run_pending(10_000) == 0
    assert s.next_due() is None


def test_failures_back_off_and_log_streak(capsys):
    s = sched(); s.add_job("a", JobSpec("p", boom, 0, False))
    t, delays = 0.0, []
    for _ in range(11):
        s.run_pending(t); nxt = s.next_due(); delays.append(nxt - t); t = nxt
    assert delays[:6] == [60, 120, 240, 480, 960, 1800] and delays[10] == 1800
    out = capsys.readouterr().out
    assert out.count("services: a job:p failed: RuntimeError: boom") == 1
    assert out.count("services: a job:p failing (10 in a row)") == 1


def test_recovery_is_logged(capsys):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return 30.0

    s = sched(); s.add_job("a", JobSpec("p", flaky, 0, False))
    s.run_pending(0); s.run_pending(60); s.run_pending(90)
    out = capsys.readouterr().out
    assert out.count("services: a job:p recovered") == 1
    assert s.next_due() == 120.0
    s.run_pending(120)                      # a plain success logs nothing more
    assert capsys.readouterr().out == ""


def test_action_cap_per_service(capsys):
    pending = []
    s = sched(spawn=pending.append)                # spawned threads never start
    assert all(s.run_action("a", "go", lambda a: None, {}) for _ in range(16))
    assert s.run_action("a", "go", lambda a: None, {}) is False
    assert s.run_action("b", "go", lambda a: None, {}) is True
    assert "services: a action:go dropped (16 running)" in capsys.readouterr().out


def test_action_slot_released_when_it_finishes():
    pending = []
    s = sched(spawn=pending.append, max_actions=1)
    assert s.run_action("a", "go", lambda a: None, {}) is True
    assert s.run_action("a", "go", lambda a: None, {}) is False
    pending.pop()()
    assert s.run_action("a", "go", lambda a: None, {}) is True


def test_action_exception_logged_and_measured(capsys):
    s = sched(spawn=lambda fn: fn())
    s.run_action("a", "go", lambda a: 1 / 0, {})
    assert "services: a action:go failed: ZeroDivisionError" in capsys.readouterr().out
    assert s._metrics.snapshot({}, {})["services"]["a"]["ops"]["action:go"]["total"]["errors"] == 1


def test_action_receives_args():
    got = []
    s = sched()
    s.run_action("a", "go", got.append, {"x": 1})
    assert got == [{"x": 1}]


def test_hung_action_reported_once(capsys):
    held = []
    s = sched(spawn=held.append)               # the action thread never runs: it stays "running"
    s.run_action("a", "go", lambda a: None, {}) # started at monotonic 0
    s.check_hangs(119); s.check_hangs(121); s.check_hangs(300)
    assert capsys.readouterr().out.count("services: HUNG a action:go running 121s") == 1


def test_hung_job_reported(capsys):
    s = sched()
    seen = []

    def slow():
        s.check_hangs(125)
        seen.append(capsys.readouterr().out)

    s.add_job("a", JobSpec("p", slow, 0, False))
    s.run_pending(0)
    assert "services: HUNG a job:p running 125s" in seen[0]


def test_job_never_runs_twice_concurrently():
    s, inner = sched(), []

    def reenter():
        inner.append(s.run_pending(1000))   # while it is running, nothing is due
        return 1.0

    s.add_job("a", JobSpec("p", reenter, 0, False))
    assert s.run_pending(0) == 1 and inner == [0]
    assert s.run_pending(1.0) == 1


def test_long_running_job_gets_its_own_thread_and_loops_until_stop():
    spawned, runs = [], []
    s = sched(spawn=spawned.append, workers=1)
    s.add_job("a", JobSpec("watch", lambda: runs.append(1) or s.stop() or 5.0, 0, True))
    assert s.run_pending(0) == 0 and s.next_due() is None   # never on the heap
    s.start()
    assert len(spawned) == 1
    spawned[0]()                                            # one run, then stop() ends the loop
    assert runs == [1]
    assert s._metrics.snapshot({}, {})["services"]["a"]["ops"]["job:watch"]["total"]["count"] == 1


def test_start_runs_job_on_a_worker_and_stop_ends_workers():
    ran = threading.Event()
    s = Scheduler(Metrics(), workers=2)
    s.add_job("a", JobSpec("p", lambda: ran.set() or None, 0, False))
    s.start()
    assert ran.wait(2.0)
    s.stop()
    assert not any(t.is_alive() for t in s._threads)


def test_worker_reschedules_from_finish_time_not_start():
    clock = [0.0]
    s = Scheduler(Metrics(), monotonic=lambda: clock[0], spawn=lambda fn: fn())
    ran = []

    def slow():
        ran.append(1)
        clock[0] += 3.0 if len(ran) == 1 else 70.0   # takes 3 s, then fails after a 70 s timeout
        return 1.0 if len(ran) == 1 else boom()

    s.add_job("a", JobSpec("p", slow, 0, False))
    with s._cond:
        job = s._take(clock[0])
    s._run_job(job)                      # the worker path: no explicit now
    assert s.next_due() == 4.0           # finish (3.0) + 1.0, not start (0) + 1.0
    clock[0] = 4.0
    with s._cond:
        job = s._take(clock[0])
    s._run_job(job)
    assert s.next_due() == 74.0 + 60.0   # 4 + 70 = 74, then the 60 s retry   # retry delay counts from the failure


def test_dropped_service_is_not_rescheduled_even_mid_run():
    s = sched()
    s.add_job("a", JobSpec("p", lambda: s.drop("a") or 5.0, 0, False))    # disabled while it runs
    s.add_job("a", JobSpec("q", lambda: 5.0, 10, False))
    s.add_job("b", JobSpec("r", lambda: 5.0, 10, False))
    assert s.run_pending(0) == 1
    assert [j.service for _, _, j in s._heap] == ["b"]


def test_add_job_rejects_bad_first_in():
    s = sched()
    for bad in ("soon", None, True, -1, float("inf"), float("nan")):
        try:
            s.add_job("a", JobSpec("p", lambda: None, bad, False))
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    assert s.next_due() is None


def test_job_delay_is_capped_at_a_day_and_nan_means_retry():
    s = sched()
    s.add_job("a", JobSpec("inf", lambda: math.inf, 0, False))
    s.add_job("b", JobSpec("nan", lambda: math.nan, 0, False))
    assert s.run_pending(0) == 2
    assert sorted((j.spec.name, due) for due, _, j in s._heap) == [("inf", 86400.0), ("nan", 60.0)]


def _until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return cond()


def test_worker_thread_survives_a_job_returning_inf():
    s = Scheduler(Metrics(), workers=1)
    later = threading.Event()
    s.add_job("a", JobSpec("inf", lambda: math.inf, 0, False))
    s.start()
    worker = s._threads[0]                               # runs Scheduler._worker
    try:
        assert _until(lambda: s.next_due() is not None)  # the inf job ran and was put back
        s.add_job("b", JobSpec("p", lambda: later.set() or None, 0, False))
        assert later.wait(2.0)                           # the other job still runs
        worker.join(0.2)
        assert worker.is_alive()
        assert s.next_due() - time.monotonic() <= 86400
    finally:
        s.stop()
    assert not worker.is_alive()


def test_worker_thread_survives_base_exception_and_keeps_the_job():
    lines = []
    s = Scheduler(Metrics(), workers=1, log=lines.append)

    def bye():
        raise SystemExit("bye")

    later = threading.Event()
    s.add_job("a", JobSpec("bye", bye, 0, False))
    s.start()
    worker = s._threads[0]
    try:
        assert _until(lambda: lines)
        s.add_job("b", JobSpec("p", lambda: later.set() or None, 0, False))
        assert later.wait(2.0)
        assert worker.is_alive()
        assert [j.spec.name for _, _, j in s._heap] == ["bye"]   # re-pushed, not lost
    finally:
        s.stop()
    assert lines == ["services: worker error SystemExit: bye"]
    assert not worker.is_alive()


def test_long_running_job_survives_base_exception():
    lines, runs, spawned = [], [], []
    s = sched(spawn=spawned.append, retry_s=0.01, log=lines.append)

    def watch():
        runs.append(1)
        if len(runs) == 1:
            raise SystemExit("bye")
        s.stop()
        return 5.0

    s.add_job("a", JobSpec("watch", watch, 0, True))
    s.start()
    spawned[0]()                                         # raise, retry after 0.01 s, then stop
    assert runs == [1, 1]
    assert lines == ["services: worker error SystemExit: bye"]
