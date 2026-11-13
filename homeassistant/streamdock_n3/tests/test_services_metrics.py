import pytest

from services.metrics import Histogram, Metrics, format_table, process_stats


def test_histogram_quantiles():
    h = Histogram()
    for _ in range(95): h.add(0.001)
    for _ in range(5): h.add(1.0)
    assert h.count == 100
    assert 0.001 <= h.quantile(0.5) < 0.0015
    assert 1.0 <= h.quantile(0.99) < 1.5
    assert Histogram().quantile(0.5) == 0.0


def test_record_accumulates_both_windows():
    m = Metrics()
    m.record("soccer", "job:refresh", 0.5, 0.1)
    m.record("soccer", "job:refresh", 1.5, 0.2, error=True)
    ops = m.snapshot({}, {})["services"]["soccer"]["ops"]["job:refresh"]
    assert ops["total"]["count"] == 2 and ops["total"]["errors"] == 1
    assert ops["total"]["wall_max_s"] == 1.5 and abs(ops["total"]["cpu_s"] - 0.3) < 1e-9
    m.roll()
    assert m.snapshot({}, {})["services"]["soccer"]["ops"]["job:refresh"]["interval"]["count"] == 0


def test_slow_line_rate_limited_per_op():
    lines, t = [], [0.0]
    m = Metrics(log=lines.append, monotonic=lambda: t[0])
    assert m.record("soccer", "job:refresh", 6.2, 0.31) is True
    assert lines == ["metrics: SLOW soccer job:refresh 6.2s wall, 0.31s cpu (budget 2.0s)"]
    m.record("soccer", "job:refresh", 6.2, 0.31); assert len(lines) == 1
    t[0] = 61.0; m.record("soccer", "job:refresh", 6.2, 0.31); assert len(lines) == 2


def test_check_slow_false_and_unknown_kind_never_warn():
    lines = []
    m = Metrics(log=lines.append)
    assert m.record("soccer", "job:feed", 999, 1, check_slow=False) is False
    assert m.record("host", "render_pass", 999, 1, check_slow=False) is False
    assert m.record("host", "render_pass", 999, 1) is False
    assert lines == []


def test_slow_budget_override():
    m = Metrics(slow_ms={"job": 100})
    assert m.record("a", "job:x", 0.5, 0.0) is True
    assert m.record("a", "job:x", 0.05, 0.0) is False


def test_measure_records_error_and_reraises():
    m = Metrics()
    with pytest.raises(ValueError):
        with m.measure("bets", "http:odds") as h:
            h.bytes_in, h.status = 1200, "5xx"
            raise ValueError("x")
    st = m.snapshot({}, {})["services"]["bets"]["ops"]["http:odds"]["total"]
    assert st["errors"] == 1 and st["bytes_in"] == 1200 and st["status"] == {"5xx": 1}


def test_measure_reports_over_budget():
    m = Metrics(slow_ms={"job": -1}, log=lambda s: None)
    with m.measure("a", "job:x") as h:
        pass
    assert h.over_budget is True


def test_summary_ranks_services_by_interval_cpu():
    m = Metrics()
    m.record("a", "job:x", 0.1, 0.01); m.record("b", "job:x", 0.1, 0.5)
    lines = m.summary_lines({"cpu_s": 1.0, "rss_mb": 80.0, "threads": 12})
    assert lines[0].startswith("metrics: b cpu 0.50s") and lines[1].startswith("metrics: a cpu")
    assert lines[-1].startswith("metrics: app ") and "rss 80 MB" in lines[-1] and "threads 12" in lines[-1]


def test_write_snapshot_failure_is_logged_not_raised(tmp_path):
    lines = []
    m = Metrics(log=lines.append)
    m.write_snapshot(tmp_path / "missing-dir" / "metrics.json", {"x": 1})
    assert lines and lines[0].startswith("metrics: snapshot not written")


def test_write_snapshot_ok(tmp_path):
    m = Metrics()
    p = tmp_path / "metrics.json"
    m.write_snapshot(p, {"x": 1})
    assert p.read_text() == '{"x": 1}'


def test_process_stats_without_proc(tmp_path):
    assert process_stats(tmp_path / "nope") == {}


def test_format_table_orders_by_key():
    m = Metrics(); m.record("a", "job:x", 0.1, 0.01); m.record("b", "http:y", 3.0, 0.02, error=True)
    snap = m.snapshot({}, {})
    rows = format_table(snap, by="wall").splitlines()
    assert "b" in rows[1] and "http:y" in rows[1]
    assert "a" in format_table(snap, by="cpu").splitlines()[2]
