"""The time of the last dock input, shared with services."""

from dock_input import DockInput


def test_last_is_zero_until_touched():
    now = [50.0]
    d = DockInput(monotonic=lambda: now[0])
    assert d.last() == 0.0
    d.touch()
    now[0] = 60.0
    assert d.last() == 50.0
    d.touch()
    assert d.last() == 60.0
