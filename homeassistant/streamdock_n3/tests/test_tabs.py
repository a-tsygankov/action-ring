from tabs import TabEngine

PAGES = {"apps": {"keys": {"1": {"action": "netflix"}}, "close_on_press": True},
         "apps_more": {"keys": {"1": {"action": "hulu"}}, "close_on_press": True},
         "sonos": {"keys": {"1": {"action": "night"}}},
         "context:idle": {"keys": {"1": {"action": "power"}}},
         "context:streaming": {"keys": {"1": {"action": "pp"}}}}
ROUNDS = {7: ["apps", "apps_more"], 8: ["sonos"]}


def eng():
    e = TabEngine(PAGES, ROUNDS)
    e.set_context("idle", 0.0)
    return e


def test_double_click_lands_on_1b():
    e = eng(); e.round_press(7, 1.0); e.round_press(7, 1.15)
    assert e.visible == "apps_more"


def test_three_presses_cycle_back():
    e = eng(); [e.round_press(7, t) for t in (1.0, 1.1, 1.2)]
    assert e.visible == "apps"


def test_open_timeout_10s_then_context():
    e = eng(); e.round_press(8, 1.0); e.tick(10.9); assert e.visible == "sonos"
    e.tick(11.01); assert e.visible == "context:idle"


def test_key_press_extends_by_5s():
    e = eng(); e.round_press(8, 1.0); e.key_press(1, 9.0); e.tick(13.9)
    assert e.visible == "sonos"; e.tick(14.01); assert e.visible == "context:idle"


def test_close_on_press_returns_immediately():
    e = eng(); e.round_press(7, 1.0)
    assert e.key_press(1, 2.0) == {"action": "netflix"}; assert e.visible == "context:idle"


def test_round_8_repeat_restarts_timer():
    e = eng(); e.round_press(8, 1.0); e.round_press(8, 9.0); e.tick(18.9)
    assert e.visible == "sonos"


def test_round_9_not_handled():
    assert eng().round_press(9, 1.0) is False


def test_context_flap_shorter_than_debounce_is_ignored():
    e = eng(); e.set_context("streaming", 1.0); e.set_context("idle", 1.5); e.tick(3.0)
    assert e.visible == "context:idle"


def test_context_adopted_after_debounce():
    e = eng(); e.set_context("streaming", 1.0); e.tick(1.9); assert e.visible == "context:idle"
    e.tick(2.01); assert e.visible == "context:streaming"


def test_press_uses_visible_context_not_pending():
    e = eng(); e.set_context("streaming", 1.0)
    assert e.key_press(1, 1.5) == {"action": "power"}


def test_context_changes_under_open_tab():
    e = eng(); e.round_press(8, 1.0); e.set_context("streaming", 1.0); e.tick(2.5)
    assert e.visible == "sonos"; e.tick(11.5); assert e.visible == "context:streaming"


def test_version_bumps_on_visible_change_only():
    e = eng(); v = e.version; e.tick(5.0); assert e.version == v
    e.round_press(8, 6.0); assert e.version == v + 1


def test_initial_context_is_idle_and_first_set_is_immediate():
    e = TabEngine(PAGES, ROUNDS)
    assert e.visible == "context:idle"
    e.set_context("streaming", 0.0)
    assert e.visible == "context:streaming"


def test_keys_is_a_shallow_copy_and_missing_page_is_empty():
    e = eng()
    e.keys()["9"] = {"action": "x"}
    assert e.keys() == {"1": {"action": "power"}}
    e.set_context("nowhere", 0.0); e.tick(2.0)
    assert e.visible == "context:nowhere"
    assert e.keys() == {}


def test_undefined_key_on_temp_page_restarts_timer_without_closing():
    e = eng(); e.round_press(7, 1.0)
    assert e.key_press(5, 9.0) is None
    assert e.visible == "apps"
    e.tick(13.9); assert e.visible == "apps"
    e.tick(14.01); assert e.visible == "context:idle"


def test_key_press_on_context_has_no_timer_effect():
    e = eng(); e.key_press(1, 5.0); e.round_press(8, 6.0); e.tick(15.9)
    assert e.visible == "sonos"


def test_one_page_cycle_repeat_does_not_bump_version():
    e = eng(); e.round_press(8, 1.0); v = e.version; e.round_press(8, 2.0)
    assert e.version == v


def test_other_round_while_open_starts_its_own_cycle():
    e = eng(); e.round_press(7, 1.0); e.round_press(7, 1.1); e.round_press(8, 1.2)
    assert e.visible == "sonos"; e.round_press(7, 1.3)
    assert e.visible == "apps"
