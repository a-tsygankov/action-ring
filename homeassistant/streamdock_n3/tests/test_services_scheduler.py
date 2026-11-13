import threading

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
