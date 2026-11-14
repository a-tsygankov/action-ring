"""AppleTVTouch tells its owner when the connection is gone."""

from types import SimpleNamespace

from atv_touch import AppleTVTouch


def _touch(on_lost):
    t = AppleTVTouch.__new__(AppleTVTouch)  # no thread, no connection
    t._on_lost = on_lost
    t._atv = SimpleNamespace(close=lambda: None)
    t._loop = SimpleNamespace(call_soon_threadsafe=lambda fn: None)
    return t


def test_lost_and_drop_call_on_lost():
    seen = []
    t = _touch(lambda: seen.append(1))
    t._lost("closed")
    assert seen == [1] and t._atv is None
    t._atv = SimpleNamespace(close=lambda: None)
    t.drop("events fail")
    assert seen == [1, 1]
    t.drop("nothing to drop")            # no connection: nothing lost
    assert seen == [1, 1]


def test_a_raising_callback_does_not_propagate():
    def boom():
        raise RuntimeError("x")

    t = _touch(boom)
    t._lost("closed")
    assert t._atv is None


def test_without_a_callback():
    t = _touch(None)
    t._lost("closed")
    assert t._atv is None
