from keyspec import entities_of, icon_path, resolve_action, resolve_look, variant


def test_variant_rules():
    assert [variant(s) for s in ("on", "playing", "idle", "off", "standby", None, "unavailable")] == \
        ["on", "on", "on", "off", "off", "unavailable", "unavailable"]


def test_icon_path_bare_id_and_path():
    assert icon_path("night__on") == "/config/icons/night__on.png"
    assert icon_path("/config/icons/home.png") == "/config/icons/home.png"
    assert icon_path(None) is None


def test_state_variant_overrides_icon():
    spec = {"icon": "night__off", "state_entity": "switch.n",
            "on": {"icon": "night__on"}, "unavailable": {"icon": "night__unknown"}}
    st = {"switch.n": {"s": "on", "a": {}}}
    assert resolve_look(spec, st.get, {}).icon == "/config/icons/night__on.png"


def test_look_unavailable_when_entity_missing():
    spec = {"icon": "night__off", "state_entity": "switch.n", "unavailable": {"icon": "night__unknown"}}
    assert resolve_look(spec, {}.get, {}).icon == "/config/icons/night__unknown.png"


def test_mode_list_condition_and_named_condition():
    spec = {"icon": "play_pause__paused",
            "modes": [{"when": "yt_subs", "icon": "subtitles__on"},
                      {"when": [{"entity": "media_player.a", "in": ["playing"]}], "icon": "play_pause__playing"}]}
    st = {"media_player.a": {"s": "playing", "a": {}}}
    assert resolve_look(spec, st.get, {"yt_subs": lambda: False}).icon.endswith("play_pause__playing.png")
    assert resolve_look(spec, st.get, {"yt_subs": lambda: True}).icon.endswith("subtitles__on.png")


def test_mode_without_action_keeps_base_action():
    spec = {"action": "a", "modes": [{"when": [{"entity": "x", "in": ["on"]}], "icon": "i"}]}
    assert resolve_action(spec, {"x": {"s": "on", "a": {}}}.get, {}) == "a"


def test_label_vars():
    spec = {"label": "NEXT\n{soccer_pos}", "color": "#1d6b32"}
    assert resolve_look(spec, {}.get, {}, {"soccer_pos": "2/3"}).label == "NEXT\n2/3"


def test_entities_of():
    spec = {"state_entity": "s.a", "modes": [{"when": [{"entity": "s.b", "in": [1]}]}, {"when": "youtube"}]}
    assert entities_of(spec) == {"s.a", "s.b"}


# Beyond the brief: edge cases of the documented semantics.

def test_mode_action_overrides_base_action():
    spec = {"action": "a", "modes": [{"when": "m", "action": "b"}]}
    assert resolve_action(spec, {}.get, {"m": lambda: True}) == "b"
    assert resolve_action(spec, {}.get, {"m": lambda: False}) == "a"


def test_attr_condition_and_unknown_named_condition():
    spec = {"modes": [{"when": "nope", "icon": "x"},
                      {"when": [{"entity": "e", "attr": "app", "in": ["YouTube"]}], "icon": "y"}]}
    st = {"e": {"s": "playing", "a": {"app": "YouTube"}}}
    assert resolve_look(spec, st.get, {}).icon.endswith("/y.png")


def test_missing_label_var_left_in_place_and_no_label_is_empty():
    assert resolve_look({"label": "A {nope}"}, {}.get, {}).label == "A {nope}"
    assert resolve_look({}, {}.get, {}).label == ""
    assert resolve_look({"color": "#fff"}, {}.get, {}).color == "#fff"


def test_variant_overlay_keeps_base_fields():
    spec = {"label": "L", "color": "#111", "state_entity": "s",
            "off": {"color": "#222"}}
    look = resolve_look(spec, {"s": {"s": "off", "a": {}}}.get, {})
    assert (look.label, look.color) == ("L", "#222")


def test_label_format_errors_show_the_raw_label():
    # A bad placeholder must never raise into the render pass.
    for label, vars in [("{soccer_pos[a]}", {"soccer_pos": "2/3"}),  # TypeError
                        ("{d[k]}", {"d": {}}),                        # KeyError
                        ("{0}", {}),                                  # ValueError
                        ("{soccer_pos.x}", {"soccer_pos": "2/3"})]:   # AttributeError
        assert resolve_look({"label": label}, {}.get, {}, vars).label == label
