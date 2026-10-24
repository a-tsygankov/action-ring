from keyspec import Look, changed_keys


def test_changed_keys_first_paint_and_diff():
    a, b = Look("/i/a.png", "", None), Look(None, "X", "#111111")
    assert changed_keys({}, {1: a, 2: b}) == [1, 2]
    assert changed_keys({1: a, 2: b}, {1: a, 2: Look(None, "Y", "#111111")}) == [2]


def test_changed_keys_nothing_changed_and_key_order():
    a, b = Look("/i/a.png", "", None), Look(None, "X", "#111111")
    assert changed_keys({1: a, 6: b}, {1: a, 6: b}) == []
    assert changed_keys({}, {6: a, 3: b, 1: a}) == [1, 3, 6]
