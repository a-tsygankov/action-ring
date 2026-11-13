"""dynamic: a key that takes turns between functions, with a warning before each change."""

from pathlib import Path

import pytest
from PIL import Image

import dynamic
from dynamic import OnScreen, Warn, frame, on_screen, phase_spec, phase_specs, pressed_index
from keyspec import entities_of
from tabs import TabEngine

ICON = Path(__file__).resolve().parent.parent / "icons" / "home.png"

THUMBS = {
    "color": "#262626",
    "state_entity": "media_player.atv",
    "dynamic": {
        "hold_s": 5,
        "phases": [
            {"icon": "thumb_up", "action": "like"},
            {"icon": "thumb_down", "action": "dislike"},
        ],
    },
}


def styled(**dyn):
    return {**THUMBS, "dynamic": {**THUMBS["dynamic"], **dyn}}


def steps(spec, until):
    """(time, phase, warning step) at each look change, the way the renderer wakes."""
    t, out = 0.0, []
    while t < until:
        f = frame(spec, t)
        out.append((round(t, 3), f.index, f.warn.step if f.warn else None))
        t += f.next_in + 0.005
    return out


def test_two_phases_take_turns_every_5_s():
    assert frame(THUMBS, 0).index == 0
    assert frame(THUMBS, 4.99).index == 0
    assert frame(THUMBS, 5.0).index == 1
    assert frame(THUMBS, 9.99).index == 1
    assert frame(THUMBS, 10.0).index == 0
    assert frame(THUMBS, 123.0).index == 0  # 120 = 12 full turns


def test_the_last_second_counts_down_3_2_1():
    assert frame(THUMBS, 3.9).warn is None
    assert frame(THUMBS, 4.1).warn == Warn("countdown", 3, 3, dynamic.WARN_COLOR)
    assert frame(THUMBS, 4.5).warn.step == 2
    assert frame(THUMBS, 4.8).warn.step == 1
    assert frame(THUMBS, 5.1).warn is None  # the new phase starts clean


def test_the_renderer_wakes_exactly_on_each_frame():
    seen = steps(THUMBS, 10.5)
    assert [(phase, step) for _, phase, step in seen] == [
        (0, None), (0, 3), (0, 2), (0, 1), (1, None), (1, 3), (1, 2), (1, 1), (0, None)]
    due = [0, 4, 4 + 1 / 3, 4 + 2 / 3, 5, 9, 9 + 1 / 3, 9 + 2 / 3, 10]
    assert all(0 <= t - d < 0.03 for (t, _, _), d in zip(seen, due)), seen


def test_bar_and_dots_styles():
    bar = styled(warn="bar")
    assert [frame(bar, t).warn.step for t in (4.1, 4.3, 4.5, 4.7, 4.9)] == [5, 4, 3, 2, 1]
    assert frame(bar, 4.1).warn.steps == 5
    dots = styled(warn="dots", warn_color="#00ff00")
    assert frame(dots, 4.1).warn == Warn("dots", 3, 3, "#00ff00")
    assert frame(styled(warn="sparkles"), 4.1).warn.style == "countdown"  # unknown: the default


def test_per_phase_hold_and_warning_length():
    spec = styled(warn_s=2, phases=[{"icon": "a", "hold_s": 3}, {"icon": "b"}, {"icon": "c", "hold_s": 1}])
    assert [frame(spec, t).index for t in (0, 2.9, 3.0, 7.9, 8.0, 8.9, 9.0)] == [0, 0, 1, 1, 2, 2, 0]
    assert frame(spec, 0.9).warn is None and frame(spec, 1.1).warn.step == 3  # 2 s warning
    assert frame(spec, 8.0).warn.step == 3  # a 1 s phase warns all along


def test_no_warning():
    spec = styled(warn_s=0)
    assert frame(spec, 4.9).warn is None
    assert frame(spec, 1.0).next_in == pytest.approx(4.0)


def test_one_phase_is_not_dynamic():
    spec = {"icon": "x", "dynamic": {"phases": [{"icon": "y"}]}}
    assert dynamic.config(spec) is None
    assert phase_specs(spec) == [spec]
    assert frame(spec, 4.9).warn is None


def test_a_phase_lays_over_the_key():
    assert phase_spec(THUMBS, 1) == {
        "color": "#262626", "state_entity": "media_player.atv", "icon": "thumb_down", "action": "dislike"}
    spec = styled(phases=[{"icon": "a", "hold_s": 2}, {"icon": "b"}])
    assert phase_spec(spec, 0) == {"color": "#262626", "state_entity": "media_player.atv", "icon": "a"}
    assert [p["icon"] for p in phase_specs(spec)] == ["a", "b"]


def test_entities_of_a_dynamic_key_include_its_phases():
    spec = styled(phases=[
        {"icon": "a", "modes": [{"when": [{"entity": "switch.x", "in": ["on"]}], "icon": "b"}]},
        {"state_entity": "switch.y"},
    ])
    assert entities_of(spec) == {"media_player.atv", "switch.x", "switch.y"}


# -- presses -------------------------------------------------------------------


def test_a_press_does_what_the_key_showed():
    drawn = OnScreen(shown_at=100.0, index=1, since=105.05, prev=0)
    assert pressed_index(THUMBS, 100.0, 105.5, drawn) == 1
    # The clock moved on but the screen still shows phase 1 (a slow draw).
    assert pressed_index(THUMBS, 100.0, 110.02, drawn) == 1


def test_a_press_just_after_the_switch_counts_for_the_old_phase():
    drawn = OnScreen(shown_at=100.0, index=1, since=105.05, prev=0)
    assert pressed_index(THUMBS, 100.0, 105.2, drawn) == 0  # within grace_s (0.25)
    assert pressed_index(styled(grace_s=0), 100.0, 105.2, drawn) == 1


def test_a_press_before_the_page_is_drawn_uses_the_clock():
    old_page = OnScreen(shown_at=50.0, index=1, since=55.0, prev=0)
    assert pressed_index(THUMBS, 100.0, 101.0, old_page) == 0
    assert pressed_index(THUMBS, 100.0, 106.0, None) == 1


def test_on_screen_remembers_the_phase_before():
    first = on_screen(None, 100.0, 0, 100.1)
    assert first == OnScreen(100.0, 0, 100.1, None)
    assert on_screen(first, 100.0, 0, 103.0) is first  # same phase: unchanged
    second = on_screen(first, 100.0, 1, 105.05)
    assert second == OnScreen(100.0, 1, 105.05, 0)
    assert on_screen(second, 200.0, 0, 200.1) == OnScreen(200.0, 0, 200.1, None)  # page shown anew


# -- the page clock ------------------------------------------------------------


def test_the_clock_restarts_when_the_page_is_shown():
    pages = {"context:idle": {"keys": {}}, "context:youtube": {"keys": {"4": THUMBS}},
             "sonos": {"keys": {}}}
    e = TabEngine(pages, {8: ["sonos"]})
    e.set_context("idle", 0.0)
    e.set_context("youtube", 10.0)
    e.tick(11.5)
    keys, shown_at = e.page()
    assert keys == {"4": THUMBS} and shown_at == 11.5
    e.set_context("youtube", 12.0)
    e.tick(20.0)  # nothing changed
    assert e.shown_at == 11.5
    e.round_press(8, 21.0)
    assert e.shown_at == 21.0
    e.tick(31.1)  # the temporary page closes (10 s): youtube is shown anew
    assert e.visible == "context:youtube" and e.shown_at == 31.1


# -- drawing -------------------------------------------------------------------


def _amber(p):
    return p[0] > 200 and 130 < p[1] < 220 and p[2] < 90


def _count(img, box, test):
    x0, y0, x1, y1 = box
    return sum(test(img.getpixel((x, y))) for x in range(x0, x1) for y in range(y0, y1))


def test_countdown_in_the_top_right_corner(tmp_path):
    out = tmp_path / "k.jpg"
    dynamic.draw_warning(str(ICON), Warn("countdown", 2, 3, dynamic.WARN_COLOR), out)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    assert img.size == Image.open(ICON).size
    assert _count(img, (w * 6 // 10, 0, w, h * 4 // 10), _amber) > 30
    assert _count(img, (0, h // 2, w // 2, h), _amber) == 0


def test_bar_shrinks_from_the_right(tmp_path):
    out = tmp_path / "k.jpg"
    dynamic.draw_warning(str(ICON), Warn("bar", 2, 5, dynamic.WARN_COLOR), out)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    y = h - 3
    assert _amber(img.getpixel((w * 2 // 10, y)))  # 2/5 of the width left
    assert not _amber(img.getpixel((w * 7 // 10, y)))


def test_dots_go_out_one_by_one(tmp_path):
    lit = []
    for step in (3, 2, 1):
        out = tmp_path / f"k{step}.jpg"
        dynamic.draw_warning(str(ICON), Warn("dots", step, 3, "#00ff00"), out)
        img = Image.open(out).convert("RGB")
        w, h = img.size
        green = lambda p: p[1] > 200 and p[0] < 90 and p[2] < 90  # noqa: E731
        lit.append(_count(img, (0, 0, w, h // 4), green))
    assert lit[0] > lit[1] > lit[2] > 0


def test_bar_sits_above_a_label_band(tmp_path):
    out = tmp_path / "k.jpg"
    dynamic.draw_warning(str(ICON), Warn("bar", 5, 5, dynamic.WARN_COLOR), out, bottom=0.7)
    img = Image.open(out).convert("RGB")
    w, h = img.size
    assert _amber(img.getpixel((w // 2, round(h * 0.7) - 3)))
    assert not _amber(img.getpixel((w // 2, h - 3)))  # the band's text stays clear
