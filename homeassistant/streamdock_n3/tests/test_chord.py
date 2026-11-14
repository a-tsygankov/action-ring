"""chord.py: presses within the window make the chord; anything else replays in order."""

import pytest

from chord import Chord, Outcome, from_config

ROUND = frozenset({7, 8, 9})


def make(window=0.3):
    return Chord(ROUND, window)


def test_three_presses_within_the_window_are_the_chord():
    c = make()
    assert c.press(7, 10.00) == Outcome(held=True)
    assert c.press(9, 10.05) == Outcome(held=True)
    assert c.press(8, 10.12) == Outcome(chord=True)  # held presses dropped: nothing replays
    assert c.next_due is None
    assert c.expire(11.0) == ()


def test_a_single_press_is_held_then_replayed_after_the_window():
    c = make()
    assert c.press(9, 10.0).held
    assert c.next_due == pytest.approx(10.3)
    assert c.expire(10.29) == ()
    assert c.expire(10.3) == (9,)
    assert c.next_due is None


def test_sequential_presses_each_replay_on_their_own():
    c = make()
    assert c.press(7, 10.0).held
    assert c.expire(10.3) == (7,)
    assert c.press(8, 10.5).held
    assert c.expire(10.8) == (8,)
    assert c.press(9, 11.0).held  # three presses, but never together: no chord
    assert c.expire(11.3) == (9,)


def test_late_third_press_replays_the_first_two_and_starts_over():
    c = make()
    c.press(7, 10.0)
    c.press(8, 10.1)
    out = c.press(9, 10.31)  # the window of 7 ran out before 9 came
    assert out == Outcome(replay=(7, 8), held=True)
    assert c.expire(10.62) == (9,)


def test_repeated_button_without_the_others_replays_both_in_order():
    c = make()
    c.press(7, 10.0)
    c.press(7, 10.1)  # a double tap on Round 1 (apps, then apps_more)
    assert c.expire(10.3) == (7, 7)


def test_another_button_flushes_held_presses_before_it():
    c = make()
    c.press(8, 10.0)
    assert c.press(3, 10.1) == Outcome(replay=(8,))  # LCD key 3: not held, after Round 2
    assert c.next_due is None


def test_two_buttons_needed():
    with pytest.raises(ValueError):
        Chord(frozenset({7}))


def test_from_config():
    assert from_config(None) is None
    assert from_config({"enabled": False, "buttons": [7, 8, 9], "action": "x"}) is None
    chord, action = from_config({"buttons": [7, 8, 9], "window_ms": 250, "action": {"service": "cats", "do": "show"}})
    assert chord.buttons == ROUND and chord.window_s == pytest.approx(0.25)
    assert action == {"service": "cats", "do": "show"}
    with pytest.raises(ValueError):
        from_config({"buttons": "7,8,9", "action": "x"})
    with pytest.raises(ValueError):
        from_config({"buttons": [7, 8, 9]})
