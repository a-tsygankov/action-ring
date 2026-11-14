"""combo.py: buttons in order, each within the gap, make the combo; anything
else replays the held presses in order."""

import pytest

from combo import Combo, Outcome, from_config

SEQ = (7, 8, 9)


def make(gap=0.6):
    return Combo(SEQ, gap)


def test_presses_in_order_within_the_gap_are_the_combo():
    c = make()
    assert c.press(7, 10.0) == Outcome(held=True)
    assert c.press(8, 10.5) == Outcome(held=True)
    assert c.press(9, 11.0) == Outcome(done=True)  # held presses dropped: nothing replays
    assert c.next_due is None
    assert c.expire(20.0) == ()


def test_the_gap_counts_from_the_last_press():
    c = make()
    c.press(7, 10.0)
    c.press(8, 10.55)
    assert c.next_due == pytest.approx(11.15)
    assert c.press(9, 11.1).done  # 1.1 s after the first: still a combo


def test_round_1_alone_waits_the_gap_then_runs():
    c = make()
    assert c.press(7, 10.0).held
    assert c.expire(10.59) == ()
    assert c.expire(10.61) == (7,)
    assert c.next_due is None


def test_round_2_and_3_alone_never_wait():
    c = make()
    assert c.press(8, 10.0) == Outcome()
    assert c.press(9, 10.1) == Outcome()
    assert c.next_due is None


def test_slow_sequence_replays_each_press():
    c = make()
    c.press(7, 10.0)
    assert c.expire(10.61) == (7,)
    assert c.press(8, 11.0) == Outcome()  # not after a held 7: acts at once
    assert c.press(9, 12.0) == Outcome()


def test_late_third_press_replays_the_first_two_and_runs_itself():
    c = make()
    c.press(7, 10.0)
    c.press(8, 10.5)
    assert c.press(9, 11.2) == Outcome(replay=(7, 8))


def test_out_of_order_press_replays_before_itself():
    c = make()
    c.press(7, 10.0)
    assert c.press(9, 10.2) == Outcome(replay=(7,))  # 7, then 9 runs normally
    c.press(7, 11.0)
    assert c.press(3, 11.1) == Outcome(replay=(7,))  # an LCD key


def test_a_repeated_first_button_restarts_the_combo():
    c = make()
    c.press(7, 10.0)
    assert c.press(7, 10.3) == Outcome(replay=(7,), held=True)  # double tap: the first one runs
    c.press(8, 10.6)
    assert c.press(9, 10.9).done


def test_two_presses_needed():
    with pytest.raises(ValueError):
        Combo((7,))


def test_from_config():
    assert from_config(None) is None
    assert from_config({"enabled": False, "buttons": [7, 8, 9], "action": "x"}) is None
    combo, action = from_config({"buttons": [7, 8, 9], "gap_ms": 500, "action": {"service": "cats", "do": "show"}})
    assert combo.buttons == SEQ and combo.gap_s == pytest.approx(0.5)
    assert action == {"service": "cats", "do": "show"}
    with pytest.raises(ValueError):
        from_config({"buttons": "7,8,9", "action": "x"})
    with pytest.raises(ValueError):
        from_config({"buttons": [7, 8, 9]})
