from services.scenes.merge import merge
from services.scenes.model import Scene


def S(a, b, cat="sex", src="videoskip"):
    return Scene(a, b, cat, src)


def test_merge():
    out = merge([S(100, 110), S(120, 125), S(1800, 2280, "nudity", "nsfwhen"), S(90, 300, "sex", "nsfwhen"),
                 S(500, 510, "suggestive", "nsfwhen"), S(4000, 4010, "nudity")], ["nudity", "sex"])
    assert out == ((100, 125), (1800, 2280), (4000, 4010))
    assert merge([], ["sex"]) == ()
    assert merge([S(500, 510, "suggestive", "nsfwhen")], ["nudity", "sex", "suggestive"]) == ((500, 510),)


def test_invalid_scenes_ignored():
    assert merge([S(-5, 10), S(50, 50), S(60, 40)], ["sex"]) == ()
    assert merge([S(-5, 10), S(100, 110)], ["sex"]) == ((100, 110),)


def test_only_non_primary_sources_are_joined():
    out = merge([S(100, 110, src="nsfwhen"), S(125, 130, src="nudidex"), S(500, 510, src="nsfwhen")], ["sex"])
    assert out == ((100, 130), (500, 510))


def test_join_gap_boundary_and_touching_not_overlapping():
    assert merge([S(0, 10), S(30, 40)], ["sex"], join_gap_s=20) == ((0, 40),)
    assert merge([S(0, 10), S(31, 40)], ["sex"], join_gap_s=20) == ((0, 10), (31, 40))
    # a non-primary scene merely touching a primary one does not overlap, so it is kept
    assert merge([S(100, 110), S(110, 115, src="nsfwhen")], ["sex"], join_gap_s=0) == ((100, 115),)
